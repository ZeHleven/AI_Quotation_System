import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.model_call_log import ModelCallLog
from app.services.drawing_pdf_agent_prompts import (
    AGENT_BILL_ITEMS_SCHEMA,
    AGENT_BILL_SUMMARY_PROMPT,
    AGENT_EVIDENCE_EXTRACTION_PROMPT,
    AGENT_EVIDENCE_SCHEMA,
    CONCRETE_ITEM_NAME_RULES,
)
from app.services.drawing_layout_planner import build_layout_planner_prompt
from app.services.drawing_cad_view_detail_planner import build_cad_view_detail_planner_prompt
from app.services.drawing_material_region_planner import build_material_region_planner_prompt


logger = logging.getLogger(__name__)


def glm_vision_model_label() -> str:
    model = (settings.glm_vision_model or "GLM Vision").strip()
    known_labels = {
        "glm-4v": "GLM-4V",
        "glm-4v-flash": "GLM-4V-Flash",
        "glm-5v-turbo": "GLM-5V-Turbo",
    }
    return known_labels.get(model.lower(), model)


def quote_vision_provider() -> str:
    provider = str(getattr(settings, "quote_vision_provider", "glm") or "glm").strip().lower()
    if provider in {"qwen", "qwenvl", "qwen-vl", "dashscope"}:
        return "dashscope"
    return "glm"


def quote_vision_model_label() -> str:
    if quote_vision_provider() == "dashscope":
        return (settings.dashscope_vision_model or "qwen-vl").strip()
    return glm_vision_model_label()


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitState:
    failure_count: int = 0
    opened_until: Optional[datetime] = None
    last_error: str = ""


_CIRCUITS: dict[str, CircuitState] = {}


VISION_PROMPT = """你是一个专业的装修造价数据提取员。请严格提取这张图片表格中的【所有】行的完整信息。
⚠️ 提取红线：每一行必须完整包含【施工空间】、【施工项目】、【规格/工艺/材料要求】、【预估工程量】（面积/长度）四项数据。
⚠️ 分隔符生死线（极度重要）：
1. 每一行的所有信息自然合并为一句话，这句话【内部绝对不允许】出现分号（请统一用逗号替代）。
2. 【仅在】换行到下一个独立项目时，强制使用全角分号“；”作为分割。
示范格式：客厅直线型吊顶，使用龙牌轻钢龙骨无造型平顶要求做L型抗裂及接缝处理，50平米；厕所铲墙皮，铲除原大白腻子，50平米。
请直接输出结果，严禁包含任何Markdown格式或多余解释。"""

DRAWING_TILE_VISION_PROMPT = """你是建筑装饰、给排水、电气施工图的视觉证据提取助手。
你只负责从当前 PDF 分块图片中提取“能支撑清单列项的可追溯证据”，不负责生成最终清单，不负责计算工程量。

请逐项提取以下证据，能看清多少提取多少，不要只概括大类：
1. 装饰材料编号和材料说明：例如 CT-02、WD-01、PT-01、600X1200灰色地砖、防水石膏板、铝扣板、墙布、硬包、石材。
2. 装饰构造和节点做法：例如 轻钢龙骨吊顶、灯槽、窗帘盒、玻璃隔断、淋浴隔断、踢脚线、门槛石、窗台石、售卖窗口、门套、门扇、成品门、拆除、基层处理。
3. 电气设备/符号/规格：例如 灯具、筒灯、射灯、格栅灯、灯带、开关、插座、配电箱AL、桥架、SC20、MT20、JDG20、WDZC-BYJ、YJV、电缆截面、色温/功率。
4. 给排水设备/管线规格：例如 给水管、排水管、地漏、阀门、水表、台盆、马桶、花洒、龙头、洁具五金、DN25、De50、PPR、不锈钢管。
5. 房间/空间/部位名称：例如 餐厅、后厨、卫生间、办公室、走廊、包间。
6. 图例、材料表、设备材料表、图签、图名、图号、设计说明。
7. 箭头、引线或文字指向的区域关系。

证据抽取规则：
- 一个材料编号、设备符号、管线规格、做法或构造节点尽量输出一条 evidence_item。
- 同一格里有多个编号/规格时，拆成多条，避免合并为“墙面、地面、灯具”这种泛化项。
- 材料表/图例中如果能看到编号，必须把编号放入 material_codes，同时在 text 或 spec_or_method 中保留原文；不要只输出材料名称。
- 防水石膏板、铝扣板如果没有明确“墙面/隔墙/墙体”语义，优先作为吊顶/天棚候选证据，不要默认写成墙面装饰板。
- DN/De/SC/MT/JDG 等规格尽量带上系统或用途文字，例如 给水管 DN40、排水管 De50、电气配管 SC20；如果只看到孤立规格，也可以保留为低置信度证据并标记 needs_manual_review=true。
- 单个字母、方向符号、轴线编号或无法形成清单项的符号（例如 T、N、E、W）不要输出为 evidence_item。
- item_hint 是“可能形成的清单项目提示”，不是最终清单；例如 块料楼地面、墙面装饰板、石膏板吊顶、灯具安装、插座安装、给水管安装。
- suggested_unit 只给计量习惯单位（㎡/m/个/套/台/樘），不写数量。
- 看不清时也可输出低置信度证据，但 needs_manual_review 必须为 true。

必须返回严格 JSON，不要 Markdown，不要解释。格式如下：
{
  "evidence_items": [
    {
      "evidence_role": "material_legend|finish_material|construction_method|device_symbol|electrical_spec|plumbing_spec|equipment_schedule|door_window_mark|room_name|drawing_title|drawing_code|arrow_relation|construction_note|unknown_note",
      "discipline": "decoration|electrical|plumbing|unknown",
      "text": "识别到的原文",
      "normalized_text": "规范化文本",
      "item_hint": "可能形成的清单项目提示，不确定则为空",
      "space": "空间或部位，不确定则为空",
      "material_codes": ["CT-02"],
      "spec_or_method": "规格、材料、做法、安装方式或构造说明",
      "suggested_unit": "㎡/m/个/套/台/樘；不确定则为空",
      "confidence": 0.0,
      "needs_manual_review": true,
      "reason": "一句话说明依据"
    }
  ]
}

禁止事项：
- 不要估算工程量。
- 不要自造 GB/T 编码或单位。
- 不要把看不清的内容补写成确定内容。
- 看不清时返回低置信度并标记 needs_manual_review=true。
"""

DRAWING_TILE_VISION_PROMPT_ADDITIONS = {
    "general": "",
    "finish_schedule": """

专项模式：装饰材料表/图例/节点做法召回。
- 优先读取材料表、材料说明、做法表、节点大样、引线说明。
- 重点拆出地面、墙面、天棚、踢脚线、门槛石、窗台石、灯槽、窗帘盒、隔断、淋浴隔断、售卖窗口、门套、门扇、成品门、玻璃门、拆除和基层处理。
- 材料编号不同必须拆成多条 evidence_item；例如 CT-01、CT-02、WD-01、PT-01 分别输出。
- 如果只有材料编号但没有材料名称或做法，只作为低置信度 evidence，needs_manual_review=true。
""",
    "electrical_mep": """

专项模式：电气系统/设备/线管线缆召回。
- 优先读取电气图例、系统图、配电箱编号与箱体规格、灯具表、灯具型号、开关、插座、弱电点位、桥架、配管、配线、电缆标注。
- SC/MT/JDG 规格优先作为“电气配管”证据；BYJ/BV 优先作为“电气配线”证据；YJY/YJV 优先作为“电缆敷设”证据。
- 灯具、开关、插座等符号要尽量带上型号、功率、色温、回路、安装方式或可见图例说明；不同灯具类型要拆开，不要统一写成“灯具安装”。
- 配电箱如出现 AL/AP/AT 等编号，item_hint 优先写“配电箱”，spec_or_method 保留箱号、安装方式、回路或可见规格。
- 单独的 T/N/E/W 等端子或方向字母不要输出为清单项。
""",
    "plumbing_fixture": """

专项模式：给排水管线/洁具/阀门召回。
- 优先读取给排水系统图、材料表、卫生间/厨房平面、管径标注和洁具图例。
- 重点拆出给水管、排水管、地漏供货及安装、阀门供货及安装、水表供货及安装、台盆供货及安装、马桶供货及安装、淋浴花洒供货及安装、冷热水龙头供货及安装、洁具五金等候选证据。
- DN/De/PPR/SUS304/不锈钢/铜质等规格材质要保留在 spec_or_method。
- 洁具、阀门、水表、地漏不能泛化成“管道安装”；如果只看到图例符号，也要以低置信度拆出独立候选并标记复核。
- 看不到系统或用途时，不要把孤立 DN/De 强行写成确定项目，低置信度并标记复核。
""",
    "fixture_valve_schedule": """

专项模式：洁具/阀门/水表/地漏表格与图例召回。
- 优先读取洁具表、阀门表、水表表、给排水附件表、卫生间/厨房图例和材料设备表，逐行拆出候选。
- 重点输出：阀门供货及安装、水表供货及安装、地漏供货及安装、马桶供货及安装、台盆供货及安装、淋浴花洒供货及安装、冷热水龙头供货及安装。
- 阀门必须尽量保留 DN/De、材质、连接方式；水表必须尽量保留口径；洁具必须尽量保留型号、材质、安装方式或五金附件。
- 不要把洁具、阀门、水表、地漏合并为“管道安装”或“给排水安装”；每个符号/表格行都要独立 evidence_item。
""",
    "demolition_node": """

专项模式：拆除/修补/基层/收口节点召回。
- 优先读取施工说明、拆除说明、节点大样和原有构件标注。
- 重点拆出拆除墙体、拆除地面/墙面/天棚、门窗拆除、门套/门扇/五金拆除、不锈钢玻璃门拆除、实木门拆除、铝合金门拆除、售卖窗口拆除、台阶拆除、洗手台拆除、马桶拆除、铲除、清运、基层处理、找平、防水、收口、成品保护等证据。
- 只输出图纸可见或节点可支持的证据，不要凭常识补全未出现的拆除项。
""",
    "door_window_demolition": """

专项模式：门窗/洞口/售卖窗口/拆除对象召回。
- 优先读取门窗表、拆除说明、平面标注、门窗编号、洞口节点、售卖窗口节点和材料表。
- 重点输出：拆除不锈钢玻璃门、拆除单开实木门、拆除双开实木门、拆除铝合金门、门套/门扇/五金拆除、售卖窗口拆除、台阶拆除、洗手台拆除、马桶拆除。
- 新做项目也要独立拆出：成品不锈钢双开玻璃门、成品实木门、不锈钢门套、人造石窗台石、售卖窗口、淋浴隔断。
- spec_or_method 必须保留可见尺寸、材质、门型、五金、门套、门扇、拆除清运或节点做法；看不清时低置信度并标记复核。
- 不要把门窗、售卖窗口、洁具拆除泛化成“拆除地面”“拆除墙面”或“拆除原有构件”。
""",
    "table_legend": """

专项模式：表格/图例/材料设备表逐行召回。
- 只要当前 tile 中出现材料表、设备表、图例、符号说明、门窗表、灯具表、洁具表、阀门/水表表、管材表或文字表格，就逐行读取，不要只总结表名。
- 每一行尽量拆成独立 evidence_item，并保留“编号/符号/名称/规格/材质/安装方式/单位/系统”的可见字段。
- 表格中同一行出现多个规格或多个型号时，拆成多条 evidence_item；不要合并成“各种灯具”“各种洁具”“管材”等泛化项。
- 对 CT、WD、PT、LT、PM、MT、插座/开关符号、AL/AP/AT 配电箱编号、DN/De、SC/MT/JDG、BYJ/BV/YJY/YJV 等，必须尽量保留原始编号或规格。
- 如果表格列名能看清，请把列名语义写入 reason；如果只看清局部，也可低置信度输出并标记复核。
""",
    "node_detail": """

专项模式：节点详图/做法说明/构造剖面逐条召回。
- 优先读取节点大样、剖面、详图、施工做法说明、引线标注和收口说明。
- 重点拆出吊顶龙骨与面层、灯槽、窗帘盒、踢脚线、门槛石、窗台石、人造石、售卖窗口、门套、成品门、墙面基层/面层、隔断、淋浴隔断、玻璃门、台阶、洗手台、马桶、拆除、修补、防水、找平、收口等能形成清单项目的做法。
- 每个做法节点拆成独立 evidence_item，spec_or_method 要保留构造层次、材料、厚度、安装方式或节点编号。
- 只输出图纸或节点文字能支撑的做法，不要从常识补全未出现的材料层。
""",
}


def drawing_tile_vision_prompt_for_mode(prompt_mode: str | None = None) -> str:
    mode = (prompt_mode or "general").strip().lower() or "general"
    if mode not in DRAWING_TILE_VISION_PROMPT_ADDITIONS:
        raise ValueError(f"未知图纸视觉识别模式: {prompt_mode}")
    return DRAWING_TILE_VISION_PROMPT + DRAWING_TILE_VISION_PROMPT_ADDITIONS[mode]

PDF_DRAWING_ITEMIZATION_PROMPT = """你是装饰、安装工程清单“具体项目名称”生成助手。
你的任务是根据当前 PDF 图纸页面或分块图片，识别能形成清单列项的图纸做法，并生成预算员可读的具体项目名称。

本步骤输出工程量清单项目名称的前半部分。后续系统会根据 item_name 再匹配 GB/T 国标清单项目，并组合为“具体项目名称（国标项目名称）”。

读取重点：
1. 材料表、图例、做法表、节点详图、设计说明、设备材料表。
2. 平面图中的材料编号、设备编号、灯具、开关、插座、洁具、给排水、电气符号及文字标注。
3. 门窗编号、隔墙、墙面、地面、天棚、踢脚线、灯槽、窗帘盒、门套、售卖窗口等装饰做法标注。

命名目标：
- item_name 要像人工工程量清单里的项目名称，短、具体、稳定，适合放入 Excel 清单。
- item_name 要优先保留图纸中的工程部位、材料名称、材料编号、设备对象、规格特征和施工动作。
- item_name 采用“部位/材料/对象 + 做法/动作 + 材料编号/规格”的业务短名称。
- item_name 只写图纸具体项目名称，GB/T 国标名称和国标编码由系统后续匹配。
- spec_or_method 承载较长的材料、规格、构造层次、安装方式和做法说明。

常用命名结构：
1. 材料/对象 + 部位 + 材料编号
   例如：瓷砖地面CT-02、石材地面ST-01、人造石窗台石PM-01
2. 部位 + 材料/对象 + 做法 + 材料编号
   例如：墙面瓷砖湿贴CT-04、墙面瓷砖CT-04、墙面瓷砖美缝
3. 构造/基层 + 材料/面层 + 形式
   例如：轻钢龙骨防水石膏板平级吊顶、轻钢龙骨防水石膏板造型吊顶、铝扣板吊顶
4. 动作 + 对象 + 材质/类型
   例如：拆除不锈钢玻璃门、拆除单开实木门、拆除铝合金门
5. 对象 + 动作
   例如：地砖拆除、木地板拆除、墙砖拆除、管线拆除
6. 成品 + 材质 + 开启方式 + 对象
   例如：成品不锈钢双开玻璃门、成品实木双开门、成品实木单开门
7. 材料/对象 + 构件名称
   例如：不锈钢踢脚线、金属线条、灯槽、窗帘盒、售卖窗口、淋浴隔断、洗手台

分类命名规则：
- 拆除工程：门类拆除写成“拆除 + 材质/类型 + 门”，例如拆除不锈钢玻璃门、拆除单开实木门、拆除双开实木门、拆除铝合金门。
- 面层拆除：写成“对象 + 拆除”，例如地砖拆除、木地板拆除、矿棉板天花拆除、条形扣板天花拆除、石膏板天花拆除、墙砖拆除、墙面装饰层拆除。
- 地面工程：石材地面保留石材类型和材料编号，例如石材地面ST-01、石材地面ST-02、石材门槛石地面ST-02。
- 地面工程：瓷砖地面保留材料编号，例如瓷砖地面CT-01、瓷砖地面CT-02、瓷砖地面CT-03、瓷砖地面CT-04。
- 地面功能项：防水、保护层、美缝、挡水条按功能命名，例如墙地面防水处理、防水保护层、瓷砖美缝、人造石挡水条。
- 天花工程：石膏板吊顶保留龙骨、面层和吊顶形式，例如轻钢龙骨防水石膏板平级吊顶、轻钢龙骨防水石膏板造型吊顶。
- 天花工程：木饰面、铝扣板、涂料天花保留面层材料，例如木饰面天花吊顶、铝扣板吊顶、白色防潮无机涂料、黑色防潮无机涂料。
- 天花构件：灯槽和窗帘盒按构件名称命名，例如灯槽、圆形灯槽、窗帘盒。
- 墙面工程：砌筑和基层类按对象命名，例如砖砌隔墙、墙面抹灰、陶粒回填。
- 墙面工程：墙砖、石材、饰面类保留部位、材料、施工方式、材料编号，例如墙面瓷砖湿贴CT-04、墙面瓷砖CT-04、墙面瓷砖湿贴CT-05、人造石墙面ST-03、墙面石材湿贴。
- 墙面工程：玻璃、木饰面、硬包、墙布、镜面、生态板保留材料对象，例如玻璃隔墙、木饰面包柱、硬包墙面、墙布墙面、清境墙面MR-02、生态板墙面。
- 墙面构件：金属、线条、踢脚线、隔断、窗口、门套按构件对象命名，例如圆形不锈钢隔断、金属线条、不锈钢踢脚线、定制成品装饰隔断、售卖窗口、不锈钢门套MT-01。
- 门类项目：保留成品、材质、开启方式，例如成品不锈钢双开玻璃门、成品实木双开门、成品实木单开门。
- 措施工程：按人工清单常用短名称输出，例如开荒精保洁、墙地面成品保护、材料二次运输。
- 电气项目：保留设备对象和安装动作，例如筒灯安装、射灯安装、灯带安装、五孔插座安装、开关安装、配电箱安装。
- 给排水项目：保留系统、材质、管径或设备对象，例如PPR给水管安装DN25、PVC排水管安装De50、阀门安装DN25、水表安装、地漏安装、台盆安装、马桶安装。

拆分规则：
- 不同工程部位要拆成不同项目。
- 不同材料编号要拆成不同项目。
- 不同材料名称要拆成不同项目。
- 不同规格尺寸影响报价时要拆成不同项目。
- 不同施工方式要拆成不同项目。
- 同一名称但区域不同且人工清单通常分开计量时，可以保留为多条同名项目。
- 门的单开、双开、材质不同要拆分。
- 吊顶的平级、造型、铝扣板、木饰面要拆分。
- 墙砖湿贴和带基层构造做法要拆分。
- 开关、插座识别为两类对象时，要拆成两条。
- 灯具类型不同要拆分。

输出要求：
- 必须返回严格 JSON。
- drawing_items 每个元素代表一条可进入清单的具体项目。
- item_name 填写具体项目名称，不填写国标项目名称。
- material_codes 填写图纸材料编号，例如 CT-02、ST-01、WD-01、MT-01。
- suggested_unit 按计量习惯填写 m²、m、m³、个、套、台、樘。
- evidence_text 填写支撑 item_name 的图纸原文或识别依据。
- confidence 填写 0 到 1。
- 图纸证据不足时，needs_manual_review 填 true。
- 返回格式中的空字符串是占位符，实际输出时要替换为图纸可见内容；看不清时保留为空并提高复核风险。
- spec_or_method 和 evidence_text 要来自图纸，不要复制“材料编号、材料名称、规格、做法、安装方式或构造说明”等字段说明文字。

示例：
- 图纸证据：灰色地砖600x1200，材料编号CT-02；item_name：瓷砖地面CT-02
- 图纸证据：600*1200白色墙面砖，CT-04，湿贴；item_name：墙面瓷砖湿贴CT-04
- 图纸证据：U型50系列轻钢天棚龙骨，双层9.5mm防水石膏板，平级；item_name：轻钢龙骨防水石膏板平级吊顶
- 图纸证据：600*600铝扣板安装；item_name：铝扣板吊顶
- 图纸证据：白色无机涂料三遍；item_name：白色无机涂料
- 图纸证据：黑色拉丝不锈钢踢脚线MT-01，50mm高；item_name：不锈钢踢脚线
- 图纸证据：成品定制木饰面门，双开；item_name：成品实木双开门

返回格式：
{
  "drawing_items": [
    {
      "item_name": "",
      "space": "",
      "material_codes": [],
      "spec_or_method": "",
      "evidence_text": "",
      "suggested_unit": "",
      "confidence": 0.0,
      "needs_manual_review": true,
      "reason": ""
    }
  ]
}

边界：本步骤只生成具体项目名称和图纸证据，工程量、国标项目名称、国标编码由系统后续步骤处理。
"""

PDF_QUANTITY_SUGGESTION_PROMPT = """你是建筑装饰及安装施工图候选工程量复核助手。
你的任务是基于当前 PDF 页面/分块图片、已识别项目候选、GB/T 标准项目和工程量计算规则，生成“AI候选工程量”，供业务员人工确认。

当前是 MVP 预览模式：工程量允许粗略估算，不要求作为最终结算量。
你可以根据图纸中清晰可见的尺寸标注、数量符号、材料编号、空间名称、图例表格、灯具/洁具图例数量、常见餐厅/装修改造尺度和国标计算规则进行推理计算。
但你不能把建议量说成最终工程量；所有结果都必须标记为 candidate_needs_manual_review。

必须返回严格 JSON，不要 Markdown，不要解释。格式如下：
{
  "quantity_suggestions": [
    {
      "item_ref": "对应上下文中的识别编号或行号，例如 PDFITEM-000001",
      "project_name": "项目名称",
      "standard_item_name": "国标项目名称",
      "quantity": 42.6,
      "unit": "㎡",
      "formula": "7.10m × 6.00m = 42.60㎡",
      "quantity_rule": "按设计图示尺寸以面积计算",
      "evidence_text": "PDF上可见的尺寸/数量/材料/房间证据",
      "source_page": 1,
      "source_tile_id": "p001_whole",
      "confidence": 0.0,
      "risk_flags": ["missing_dxf_boundary_check"],
      "review_status": "candidate_needs_manual_review",
      "reason": "一句话说明为什么可以作为候选量"
    }
  ]
}

红线：
- 优先使用图纸中可见的尺寸、数量、图例数量、面积表或可解释的计算依据。
- 如果依据不完整，但能根据空间名称、材料做法、图纸比例感和常见工程量口径给出粗估量，也要给 quantity；confidence 设为 0.20-0.45，并在 risk_flags/reason 中明确“粗估、待复核”。
- 不要把粗估量包装成精确量。
- 不要输出最终工程量；review_status 必须是 candidate_needs_manual_review。
- 公式可以是“按当前图纸可见范围粗估约 X”，但必须可被业务员理解；证据文本必须来自图纸或上下文。
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _circuit_key(provider: str, endpoint_type: str) -> str:
    return f"{provider}:{endpoint_type}"


def _estimate_cost(input_chars: int, output_chars: int) -> float:
    if settings.model_gateway_cost_per_1k_chars <= 0:
        return 0.0
    return round(((input_chars + output_chars) / 1000) * settings.model_gateway_cost_per_1k_chars, 6)


def reset_circuit_breakers() -> None:
    _CIRCUITS.clear()


def get_circuit_status() -> dict:
    now = _utcnow()
    result = {}
    for key, state in _CIRCUITS.items():
        opened_until = state.opened_until
        result[key] = {
            "failure_count": state.failure_count,
            "is_open": bool(opened_until and opened_until > now),
            "opened_until": opened_until.isoformat() if opened_until else None,
            "last_error": state.last_error,
        }
    return result


def _before_call(provider: str, endpoint_type: str) -> None:
    key = _circuit_key(provider, endpoint_type)
    state = _CIRCUITS.setdefault(key, CircuitState())
    if state.opened_until and state.opened_until > _utcnow():
        raise CircuitOpenError(f"模型网关熔断中: {key}，恢复时间 {state.opened_until.isoformat()}")
    if state.opened_until and state.opened_until <= _utcnow():
        state.opened_until = None
        state.failure_count = 0


def _after_success(provider: str, endpoint_type: str) -> None:
    _CIRCUITS[_circuit_key(provider, endpoint_type)] = CircuitState()


def _after_failure(provider: str, endpoint_type: str, error_message: str) -> None:
    key = _circuit_key(provider, endpoint_type)
    state = _CIRCUITS.setdefault(key, CircuitState())
    state.failure_count += 1
    state.last_error = error_message[:500]
    if state.failure_count >= settings.model_gateway_failure_threshold:
        state.opened_until = _utcnow() + timedelta(seconds=settings.model_gateway_circuit_reset_seconds)


def record_model_call(
    *,
    provider: str,
    model: str,
    endpoint_type: str,
    status: str,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
    http_status: Optional[int] = None,
    latency_ms: float = 0.0,
    input_chars: int = 0,
    output_chars: int = 0,
    error_message: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            ModelCallLog(
                provider=provider,
                model=model,
                endpoint_type=endpoint_type,
                status=status,
                username=_limit_text(username, 64),
                trace_id=_limit_text(trace_id, 64),
                http_status=http_status,
                latency_ms=round(latency_ms, 2),
                input_chars=input_chars,
                output_chars=output_chars,
                estimated_cost=_estimate_cost(input_chars, output_chars),
                error_message=(error_message or "")[:1000] or None,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("model_call_log_failed", extra={"event": "model_call_log_failed", "provider": provider})
    finally:
        db.close()


async def record_model_call_async(**kwargs: Any) -> None:
    await asyncio.to_thread(record_model_call, **kwargs)


def _limit_text(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    return str(value)[:limit]


async def call_openai_pdf_agent_evidence_extract(
    view_payloads: list[Dict[str, Any]],
    *,
    prompt_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "openai"
    endpoint_type = "pdf_agent_evidence_extract"
    model = settings.openai_vision_model
    prompt = _build_openai_agent_evidence_prompt(view_payloads, prompt_override=prompt_override)
    content = _build_openai_agent_view_content(prompt, view_payloads)
    return await _call_openai_responses_json(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        content=content,
        prompt_text=prompt,
        username=username,
        trace_id=trace_id,
    )


async def call_openai_pdf_agent_bill_summarize(
    merged_evidence: Dict[str, Any],
    *,
    prompt_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "openai"
    endpoint_type = "pdf_agent_bill_summarize"
    model = settings.openai_vision_model
    prompt = _build_openai_agent_bill_prompt(merged_evidence, prompt_override=prompt_override)
    content = [{"type": "input_text", "text": prompt}]
    return await _call_openai_responses_json(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        content=content,
        prompt_text=prompt,
        username=username,
        trace_id=trace_id,
    )


async def call_dashscope_pdf_agent_evidence_extract(
    view_payloads: list[Dict[str, Any]],
    *,
    prompt_override: Optional[str] = None,
    model_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "dashscope"
    endpoint_type = "pdf_agent_evidence_extract"
    model = (model_override or settings.dashscope_evidence_model or settings.dashscope_vision_model).strip()
    prompt = _build_openai_agent_evidence_prompt(view_payloads, prompt_override=prompt_override)
    content = _build_dashscope_agent_view_content(prompt, view_payloads)
    return await _call_dashscope_chat_completions_json(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        content=content,
        prompt_text=prompt,
        username=username,
        trace_id=trace_id,
    )


async def call_dashscope_drawing_layout_plan(
    page_payloads: list[Dict[str, Any]],
    *,
    model_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "dashscope"
    endpoint_type = "drawing_layout_planner"
    model = (model_override or settings.drawing_layout_planner_model or settings.dashscope_vision_model).strip()
    prompt = build_layout_planner_prompt(page_payloads)
    content = _build_dashscope_agent_view_content(prompt, page_payloads)
    return await _call_dashscope_chat_completions_json(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        content=content,
        prompt_text=prompt,
        username=username,
        trace_id=trace_id,
    )


async def call_dashscope_cad_view_detail_plan(
    view_payloads: list[Dict[str, Any]],
    *,
    model_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "dashscope"
    endpoint_type = "cad_view_detail_planner"
    model = (model_override or settings.drawing_cad_view_detail_planner_model or settings.dashscope_vision_model).strip()
    prompt = build_cad_view_detail_planner_prompt(view_payloads)
    content = _build_dashscope_agent_view_content(prompt, view_payloads)
    return await _call_dashscope_chat_completions_json(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        content=content,
        prompt_text=prompt,
        username=username,
        trace_id=trace_id,
    )


async def call_dashscope_material_region_plan(
    view_payloads: list[Dict[str, Any]],
    *,
    model_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "dashscope"
    endpoint_type = "material_region_planner"
    model = (model_override or settings.drawing_material_region_planner_model or settings.dashscope_vision_model).strip()
    prompt = build_material_region_planner_prompt(view_payloads)
    content = _build_dashscope_agent_view_content(prompt, view_payloads)
    return await _call_dashscope_chat_completions_json(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        content=content,
        prompt_text=prompt,
        username=username,
        trace_id=trace_id,
    )


async def call_dashscope_pdf_agent_bill_summarize(
    merged_evidence: Dict[str, Any],
    *,
    prompt_override: Optional[str] = None,
    model_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "dashscope"
    endpoint_type = "pdf_agent_bill_summarize"
    model = (model_override or settings.dashscope_bill_summary_model or settings.dashscope_vision_model).strip()
    prompt = _build_openai_agent_bill_prompt(merged_evidence, prompt_override=prompt_override)
    content = [{"type": "text", "text": prompt}]
    return await _call_dashscope_chat_completions_json(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        content=content,
        prompt_text=prompt,
        username=username,
        trace_id=trace_id,
    )


async def _call_dashscope_chat_completions_json(
    *,
    provider: str,
    model: str,
    endpoint_type: str,
    content: list[Dict[str, Any]],
    prompt_text: str,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    _before_call(provider, endpoint_type)
    _validate_dashscope_api_key()

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": settings.dashscope_temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.dashscope_api_key.strip()}",
    }
    url = _dashscope_chat_completions_url()

    started = time.perf_counter()
    last_error = "未知错误"
    for delay in [1, 2]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.dashscope_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                content_text = _extract_chat_completion_text(response.json())
                parsed = _parse_openai_json_content(content_text)
                _after_success(provider, endpoint_type)
                await record_model_call_async(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(prompt_text),
                    output_chars=len(content_text or ""),
                )
                return {"raw_content": content_text, **parsed}

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc) or exc.__class__.__name__
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    await record_model_call_async(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(prompt_text),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


async def call_dashscope_vision_extract(
    base64_image: str,
    mime_type: str,
    *,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    provider = "dashscope"
    endpoint_type = "vision_extract"
    model = (settings.dashscope_vision_model or "qwen-vl-max").strip()
    _before_call(provider, endpoint_type)
    _validate_dashscope_api_key()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
                ],
            }
        ],
        "temperature": settings.dashscope_temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.dashscope_api_key.strip()}",
    }
    url = _dashscope_chat_completions_url()

    started = time.perf_counter()
    last_error = "未知错误"
    for delay in [1, 2]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.dashscope_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                content = _extract_chat_completion_text(response.json())
                _after_success(provider, endpoint_type)
                await record_model_call_async(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(VISION_PROMPT),
                    output_chars=len(content or ""),
                )
                return content

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc) or exc.__class__.__name__
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    await record_model_call_async(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(VISION_PROMPT),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


async def _call_openai_responses_json(
    *,
    provider: str,
    model: str,
    endpoint_type: str,
    content: list[Dict[str, Any]],
    prompt_text: str,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    _before_call(provider, endpoint_type)
    _validate_openai_api_key()

    payload = {
        "model": model,
        "input": [{"role": "user", "content": content}],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.openai_api_key.strip()}",
    }

    started = time.perf_counter()
    last_error = "未知错误"
    for delay in [1, 2]:
        try:
            client_kwargs: Dict[str, Any] = {
                "timeout": settings.openai_drawing_agent_timeout_seconds,
                "trust_env": False,
            }
            proxy_url = _openai_proxy_url()
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(settings.openai_responses_url, headers=headers, json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                response_json = response.json()
                content_text = _extract_openai_response_text(response_json)
                parsed = _parse_openai_json_content(content_text)
                _after_success(provider, endpoint_type)
                await record_model_call_async(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(prompt_text),
                    output_chars=len(content_text or ""),
                )
                return {"raw_content": content_text, **parsed}

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    await record_model_call_async(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(prompt_text),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


def _build_openai_agent_evidence_prompt(
    view_payloads: list[Dict[str, Any]],
    *,
    prompt_override: Optional[str] = None,
) -> str:
    view_context = [
        {
            "view_id": str(view.get("view_id") or "").strip(),
            "source_file": str(view.get("source_file") or "").strip(),
            "page": view.get("page") or "",
            "tile_type": str(view.get("tile_type") or "").strip(),
            "selection_role": str(view.get("selection_role") or "").strip(),
            "bbox_pixel": view.get("bbox_pixel") or [],
            "bbox_pdf": view.get("bbox_pdf") or [],
        }
        for view in view_payloads
    ]
    base_prompt = prompt_override or AGENT_EVIDENCE_EXTRACTION_PROMPT
    return (
        base_prompt
        + "\n\n输出 JSON schema：\n"
        + AGENT_EVIDENCE_SCHEMA
        + "\n\n本次输入 view_manifest：\n"
        + json.dumps(view_context, ensure_ascii=False, separators=(",", ":"))
    )


def _build_openai_agent_bill_prompt(
    merged_evidence: Dict[str, Any],
    *,
    prompt_override: Optional[str] = None,
) -> str:
    base_prompt = prompt_override or AGENT_BILL_SUMMARY_PROMPT
    return (
        base_prompt
        + "\n\n"
        + CONCRETE_ITEM_NAME_RULES
        + "\n\n输出 JSON schema：\n"
        + AGENT_BILL_ITEMS_SCHEMA
        + "\n\n合并后的图纸证据：\n"
        + json.dumps(merged_evidence, ensure_ascii=False, separators=(",", ":"))
    )


def _build_openai_agent_view_content(prompt: str, view_payloads: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    content: list[Dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for view in view_payloads:
        view_id = str(view.get("view_id") or "").strip()
        image_base64 = str(view.get("image_base64") or "").strip()
        if not image_base64:
            continue
        mime_type = str(view.get("mime_type") or "image/png").strip() or "image/png"
        content.append(
            {
                "type": "input_text",
                "text": (
                    f"以下图片对应 view_id={view_id or 'unknown'}，"
                    f"tile_type={view.get('tile_type') or ''}，"
                    f"selection_role={view.get('selection_role') or ''}，"
                    f"page={view.get('page') or ''}。"
                ),
            }
        )
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{image_base64}",
            }
        )
    return content


def _build_dashscope_agent_view_content(prompt: str, view_payloads: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    content: list[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for view in view_payloads:
        view_id = str(view.get("view_id") or "").strip()
        image_base64 = str(view.get("image_base64") or "").strip()
        if not image_base64:
            continue
        mime_type = str(view.get("mime_type") or "image/png").strip() or "image/png"
        content.append(
            {
                "type": "text",
                "text": (
                    f"以下图片对应 view_id={view_id or 'unknown'}，"
                    f"tile_type={view.get('tile_type') or ''}，"
                    f"selection_role={view.get('selection_role') or ''}，"
                    f"page={view.get('page') or ''}。"
                ),
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
            }
        )
    return content


def _extract_openai_response_text(response_json: Dict[str, Any]) -> str:
    output_text = str(response_json.get("output_text") or "").strip()
    if output_text:
        return output_text

    texts: list[str] = []
    for output in response_json.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = str(content.get("text") or "").strip()
            if text and content.get("type") in {"output_text", "text"}:
                texts.append(text)
    if texts:
        return "\n".join(texts).strip()

    choices = response_json.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return str((choices[0].get("message") or {}).get("content") or "").strip()
    return ""


def _extract_chat_completion_text(response_json: Dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") if isinstance(choices[0].get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if text:
                    texts.append(text)
            return "\n".join(texts).strip()
    return ""


def _parse_openai_json_content(content: str) -> Dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        text = match.group(0) if match else "{}"
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise RuntimeError("OpenAI 返回内容不是合法 JSON") from exc
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _validate_openai_api_key() -> None:
    api_key = settings.openai_api_key.strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY 未配置")
    if "请在这里" in api_key or re.search(r"[\u4e00-\u9fa5]", api_key):
        raise ValueError("OPENAI_API_KEY 格式异常")


def _openai_proxy_url() -> str:
    return str(getattr(settings, "https_proxy", "") or getattr(settings, "http_proxy", "") or "").strip()


def _validate_dashscope_api_key() -> None:
    api_key = settings.dashscope_api_key.strip()
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY 未配置")
    if "请在这里" in api_key or re.search(r"[\u4e00-\u9fa5]", api_key):
        raise ValueError("DASHSCOPE_API_KEY 格式异常")


def _dashscope_chat_completions_url() -> str:
    base_url = settings.dashscope_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


async def call_glm_vision_extract(
    base64_image: str,
    mime_type: str,
    *,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    provider = "zhipu"
    endpoint_type = "vision_extract"
    model = settings.glm_vision_model
    _before_call(provider, endpoint_type)

    if "请在这里" in settings.zhipu_api_key or re.search(r"[\u4e00-\u9fa5]", settings.zhipu_api_key):
        error_message = "API Key 格式异常(包含中文字符)"
        _after_failure(provider, endpoint_type, error_message)
        await record_model_call_async(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status="error",
            username=username,
            trace_id=trace_id,
            error_message=error_message,
            input_chars=len(VISION_PROMPT),
        )
        raise ValueError(error_message)

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.zhipu_api_key.strip()}"}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
                ],
            }
        ],
    }

    last_error = "未知错误"
    started = time.perf_counter()
    for delay in [1, 2]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.model_gateway_timeout_seconds,
                trust_env=False,
                verify=False,
            ) as client:
                response = await client.post(settings.glm_vision_url, headers=headers, json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                _after_success(provider, endpoint_type)
                await record_model_call_async(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(VISION_PROMPT),
                    output_chars=len(content or ""),
                )
                return content

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    await record_model_call_async(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(VISION_PROMPT),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


async def call_quote_vision_extract(
    base64_image: str,
    mime_type: str,
    *,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    if quote_vision_provider() == "dashscope":
        return await call_dashscope_vision_extract(
            base64_image,
            mime_type,
            username=username,
            trace_id=trace_id,
        )
    return await call_glm_vision_extract(
        base64_image,
        mime_type,
        username=username,
        trace_id=trace_id,
    )


async def call_glm_drawing_tile_extract(
    base64_image: str,
    mime_type: str,
    *,
    tile_context: Optional[Dict[str, Any]] = None,
    prompt_mode: str = "general",
    prompt_override: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "zhipu"
    endpoint_type = "drawing_tile_vision"
    model = settings.glm_vision_model
    _before_call(provider, endpoint_type)
    prompt = prompt_override or drawing_tile_vision_prompt_for_mode(prompt_mode)

    if "请在这里" in settings.zhipu_api_key or re.search(r"[\u4e00-\u9fa5]", settings.zhipu_api_key):
        error_message = "API Key 格式异常(包含中文字符)"
        _after_failure(provider, endpoint_type, error_message)
        await record_model_call_async(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status="error",
            username=username,
            trace_id=trace_id,
            error_message=error_message,
            input_chars=len(prompt),
        )
        raise ValueError(error_message)

    if tile_context:
        enriched_context = dict(tile_context)
        enriched_context["prompt_mode"] = prompt_mode
        prompt += "\n\n当前 tile 上下文：\n" + json_dumps_compact(enriched_context)

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.zhipu_api_key.strip()}"}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
                ],
            }
        ],
    }

    last_error = "未知错误"
    started = time.perf_counter()
    for delay in [1, 2]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.model_gateway_timeout_seconds,
                trust_env=False,
                verify=False,
            ) as client:
                response = await client.post(settings.glm_vision_url, headers=headers, json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = parse_drawing_tile_vision_json(content)
                _after_success(provider, endpoint_type)
                await record_model_call_async(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(prompt),
                    output_chars=len(content or ""),
                )
                return {"raw_content": content, "prompt_mode": prompt_mode, **parsed}

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    await record_model_call_async(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(prompt),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


async def call_glm_pdf_drawing_itemize(
    base64_image: str,
    mime_type: str,
    *,
    page_context: Optional[Dict[str, Any]] = None,
    prompt_addition: Optional[str] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "zhipu"
    endpoint_type = "pdf_drawing_itemization"
    model = settings.glm_vision_model
    _before_call(provider, endpoint_type)

    if "请在这里" in settings.zhipu_api_key or re.search(r"[\u4e00-\u9fa5]", settings.zhipu_api_key):
        error_message = "API Key 格式异常(包含中文字符)"
        _after_failure(provider, endpoint_type, error_message)
        await record_model_call_async(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status="error",
            username=username,
            trace_id=trace_id,
            error_message=error_message,
            input_chars=len(PDF_DRAWING_ITEMIZATION_PROMPT),
        )
        raise ValueError(error_message)

    prompt = PDF_DRAWING_ITEMIZATION_PROMPT
    if prompt_addition:
        prompt += "\n\n补充列项规则：\n" + str(prompt_addition).strip()
    if page_context:
        prompt += "\n\n当前页面/分块上下文：\n" + json_dumps_compact(page_context)

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.zhipu_api_key.strip()}"}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
                ],
            }
        ],
    }

    last_error = "未知错误"
    started = time.perf_counter()
    for delay in [1, 2]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.model_gateway_timeout_seconds,
                trust_env=False,
                verify=False,
            ) as client:
                response = await client.post(settings.glm_vision_url, headers=headers, json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = parse_pdf_drawing_itemization_json(content)
                _after_success(provider, endpoint_type)
                await record_model_call_async(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(prompt),
                    output_chars=len(content or ""),
                )
                return {"raw_content": content, **parsed}

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    await record_model_call_async(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(prompt),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


async def call_glm_pdf_quantity_suggest(
    base64_image: str,
    mime_type: str,
    *,
    quantity_context: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = "zhipu"
    endpoint_type = "pdf_quantity_suggestion"
    model = settings.glm_vision_model
    _before_call(provider, endpoint_type)

    if "请在这里" in settings.zhipu_api_key or re.search(r"[\u4e00-\u9fa5]", settings.zhipu_api_key):
        error_message = "API Key 格式异常(包含中文字符)"
        _after_failure(provider, endpoint_type, error_message)
        await record_model_call_async(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status="error",
            username=username,
            trace_id=trace_id,
            error_message=error_message,
            input_chars=len(PDF_QUANTITY_SUGGESTION_PROMPT),
        )
        raise ValueError(error_message)

    prompt = PDF_QUANTITY_SUGGESTION_PROMPT
    if quantity_context:
        prompt += "\n\n当前候选工程量上下文：\n" + json_dumps_compact(quantity_context)

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.zhipu_api_key.strip()}"}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}" }},
                ],
            }
        ],
    }

    last_error = "未知错误"
    started = time.perf_counter()
    for delay in [1, 2]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.model_gateway_timeout_seconds,
                trust_env=False,
                verify=False,
            ) as client:
                response = await client.post(settings.glm_vision_url, headers=headers, json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = parse_pdf_quantity_suggestion_json(content)
                _after_success(provider, endpoint_type)
                await record_model_call_async(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(prompt),
                    output_chars=len(content or ""),
                )
                return {"raw_content": content, **parsed}

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    await record_model_call_async(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(prompt),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


def parse_drawing_tile_vision_json(content: str) -> Dict[str, Any]:
    import json

    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    json_text = text
    if not (json_text.startswith("{") or json_text.startswith("[")):
        match = re.search(r"\{.*\}|\[.*\]", json_text, flags=re.DOTALL)
        json_text = match.group(0) if match else "{}"
    try:
        parsed = json.loads(json_text)
    except Exception:
        parsed = {}
    if isinstance(parsed, dict):
        items = parsed.get("evidence_items")
    elif isinstance(parsed, list):
        items = parsed
    else:
        items = []
    if not isinstance(items, list):
        items = []
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text_value = _drawing_evidence_text(item)
        if not text_value:
            continue
        fallback_role = _drawing_fallback_evidence_role(item)
        normalized_items.append(
            {
                "evidence_role": str(item.get("evidence_role") or fallback_role).strip() or "unknown_note",
                "discipline": str(item.get("discipline") or _drawing_fallback_discipline(fallback_role)).strip()
                or "unknown",
                "text": text_value,
                "normalized_text": str(item.get("normalized_text") or text_value).strip(),
                "item_hint": str(item.get("item_hint") or _drawing_first_present(item, _PUBLIC_DIAMETER_KEYS)).strip(),
                "space": str(item.get("space") or "").strip(),
                "material_codes": _coerce_string_list(item.get("material_codes")),
                "spec_or_method": str(
                    item.get("spec_or_method") or _drawing_first_present(item, _PLASTIC_OUTSIDE_DIAMETER_KEYS)
                ).strip(),
                "suggested_unit": str(item.get("suggested_unit") or "").strip(),
                "confidence": _drawing_confidence(item, fallback_role),
                "needs_manual_review": bool(item.get("needs_manual_review", True)),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return {"evidence_items": normalized_items}


_PUBLIC_DIAMETER_KEYS = ("public_diameter", "public diameter", "公称直径")
_PLASTIC_OUTSIDE_DIAMETER_KEYS = (
    "plastic_pipe_outside_diameter",
    "plastic pipe outside diameter",
    "塑料管外径",
)
_INCH_LABEL_KEYS = ("inch_label", "inch label", "英寸")


def _drawing_evidence_text(item: Dict[str, Any]) -> str:
    explicit = str(item.get("text") or item.get("evidence_text") or "").strip()
    if explicit:
        return explicit
    row_parts = [
        _drawing_first_present(item, _PUBLIC_DIAMETER_KEYS),
        _drawing_first_present(item, _PLASTIC_OUTSIDE_DIAMETER_KEYS),
        _drawing_first_present(item, _INCH_LABEL_KEYS),
    ]
    if any(row_parts):
        return " | ".join(row_parts).strip()
    return ""


def _drawing_fallback_evidence_role(item: Dict[str, Any]) -> str:
    if _drawing_first_present(item, _PUBLIC_DIAMETER_KEYS) or _drawing_first_present(
        item, _PLASTIC_OUTSIDE_DIAMETER_KEYS
    ):
        return "table_row"
    return "unknown_note"


def _drawing_fallback_discipline(fallback_role: str) -> str:
    if fallback_role == "table_row":
        return "plumbing"
    return "unknown"


def _drawing_confidence(item: Dict[str, Any], fallback_role: str) -> float:
    if item.get("confidence") in (None, "") and fallback_role == "table_row":
        return 0.78
    return _coerce_confidence(item.get("confidence"))


def _drawing_first_present(item: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def parse_pdf_drawing_itemization_json(content: str) -> Dict[str, Any]:
    import json

    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    json_text = text
    if not json_text.startswith("{"):
        match = re.search(r"\{.*\}", json_text, flags=re.DOTALL)
        json_text = match.group(0) if match else "{}"
    try:
        parsed = json.loads(json_text)
    except Exception:
        parsed = {}
    items = parsed.get("drawing_items") if isinstance(parsed, dict) else []
    if not isinstance(items, list):
        items = []
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("item_name") or "").strip()
        evidence_text = str(item.get("evidence_text") or "").strip()
        spec_or_method = str(item.get("spec_or_method") or "").strip()
        if _is_pdf_item_placeholder_text(spec_or_method):
            spec_or_method = ""
        if _is_pdf_item_placeholder_text(evidence_text):
            evidence_text = ""
        if not item_name and not evidence_text and not spec_or_method:
            continue
        material_codes = item.get("material_codes") or []
        if not isinstance(material_codes, list):
            material_codes = [material_codes]
        normalized_items.append(
            {
                "item_name": item_name or evidence_text[:80],
                "space": str(item.get("space") or "").strip(),
                "material_codes": [str(code).strip() for code in material_codes if str(code).strip()],
                "spec_or_method": spec_or_method,
                "evidence_text": evidence_text or spec_or_method or item_name,
                "suggested_unit": str(item.get("suggested_unit") or "").strip(),
                "confidence": _coerce_confidence(item.get("confidence")),
                "needs_manual_review": bool(item.get("needs_manual_review", True)),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return {"drawing_items": normalized_items}


def _is_pdf_item_placeholder_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    placeholder_parts = [
        "材料编号、材料名称、规格、做法、安装方式或构造说明",
        "图纸上可见的原文或可追溯依据",
        "具体项目名称，例如",
        "空间或部位，例如",
        "建议计量单位",
    ]
    return any(part in text for part in placeholder_parts)


def parse_pdf_quantity_suggestion_json(content: str) -> Dict[str, Any]:
    import json

    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    json_text = text
    if not json_text.startswith("{"):
        match = re.search(r"\{.*\}", json_text, flags=re.DOTALL)
        json_text = match.group(0) if match else "{}"
    try:
        parsed = json.loads(json_text)
    except Exception:
        parsed = {}
    suggestions = parsed.get("quantity_suggestions") if isinstance(parsed, dict) else []
    if not isinstance(suggestions, list):
        suggestions = []
    if not suggestions:
        suggestions = _recover_quantity_suggestion_objects(text)
    normalized: list[dict[str, Any]] = []
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        quantity = _coerce_optional_number(item.get("quantity"))
        item_ref = str(item.get("item_ref") or item.get("识别编号") or "").strip()
        project_name = str(item.get("project_name") or "").strip()
        formula = str(item.get("formula") or "").strip()
        evidence_text = str(item.get("evidence_text") or "").strip()
        if quantity is None and not formula and not evidence_text:
            continue
        risk_flags = item.get("risk_flags") or []
        if not isinstance(risk_flags, list):
            risk_flags = [risk_flags]
        review_status = str(item.get("review_status") or "candidate_needs_manual_review").strip()
        normalized.append(
            {
                "item_ref": item_ref,
                "project_name": project_name,
                "standard_item_name": str(item.get("standard_item_name") or "").strip(),
                "quantity": quantity,
                "unit": str(item.get("unit") or "").strip(),
                "formula": formula,
                "quantity_rule": str(item.get("quantity_rule") or "").strip(),
                "evidence_text": evidence_text,
                "source_page": item.get("source_page") or "",
                "source_tile_id": str(item.get("source_tile_id") or "").strip(),
                "confidence": _coerce_confidence(item.get("confidence")),
                "risk_flags": [str(flag).strip() for flag in risk_flags if str(flag).strip()],
                "review_status": review_status or "candidate_needs_manual_review",
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return {"quantity_suggestions": normalized}


def _recover_quantity_suggestion_objects(text: str) -> list[dict[str, Any]]:
    """Recover complete suggestion objects from a partially malformed GLM JSON response."""
    import json

    start = text.find('"quantity_suggestions"')
    if start < 0:
        start = text.find("'quantity_suggestions'")
    if start < 0:
        start = 0
    array_start = text.find("[", start)
    scan_from = array_start + 1 if array_start >= 0 else start
    recovered: list[dict[str, Any]] = []
    cursor = scan_from
    while cursor < len(text):
        object_start = text.find("{", cursor)
        if object_start < 0:
            break
        object_end = _find_balanced_json_object_end(text, object_start)
        if object_end is None:
            break
        raw_object = text[object_start : object_end + 1]
        try:
            parsed = json.loads(raw_object)
        except Exception:
            cursor = object_end + 1
            continue
        if isinstance(parsed, dict):
            recovered.append(parsed)
        cursor = object_end + 1
    return recovered


def _find_balanced_json_object_end(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _coerce_optional_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _coerce_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _coerce_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = re.split(r"[,，、;；\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = str(item or "").strip()
        if not cleaned:
            continue
        key = cleaned.upper().replace("－", "-")
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def json_dumps_compact(value: Dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def post_json_via_gateway(
    *,
    provider: str,
    model: str,
    endpoint_type: str,
    url: str,
    json_payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: Optional[float],
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> httpx.Response:
    _before_call(provider, endpoint_type)
    started = time.perf_counter()
    input_chars = len(str(json_payload))
    client_timeout = None if timeout is None or timeout <= 0 else timeout
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            response = await client.post(url, json=json_payload, headers=headers)
        latency_ms = (time.perf_counter() - started) * 1000
        output_chars = len(response.text or "")

        if 200 <= response.status_code < 300:
            _after_success(provider, endpoint_type)
            status = "success"
            error_message = None
        else:
            error_message = f"HTTP {response.status_code}: {(response.text or '')[:300]}"
            _after_failure(provider, endpoint_type, error_message)
            status = "error"

        await record_model_call_async(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status=status,
            username=username,
            trace_id=trace_id,
            http_status=response.status_code,
            latency_ms=latency_ms,
            input_chars=input_chars,
            output_chars=output_chars,
            error_message=error_message,
        )
        return response
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        error_message = str(exc)
        _after_failure(provider, endpoint_type, error_message)
        await record_model_call_async(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status="error",
            username=username,
            trace_id=trace_id,
            latency_ms=latency_ms,
            input_chars=input_chars,
            error_message=error_message,
        )
        raise
