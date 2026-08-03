from __future__ import annotations

import re
from typing import Any, Mapping

from app.models.bidding import BidDraftSection
from app.services.bidding_technical_quality import (
    FORMAL_FIXED_MATERIAL_SECTION_NOS,
    TECHNICAL_FORMAL_DEPTH_INTENTS,
    _normalize_for_contains,
    _paragraph_count,
    _plain_text,
    _section_intent,
    _section_no,
)
from app.services.bidding_technical_section_templates import (
    SECTION_TEMPLATE_INTENT_FOCUS,
    SECTION_TEMPLATE_INTENT_TITLES,
)


BID_TECHNICAL_SECTION_REVIEW_FOCUS_REINFORCEMENT_VERSION = "biz4c4_p8_section_review_focus_reinforcement_v1"

SECTION_REVIEW_FOCUS_REINFORCEMENT_HEADING = "评审关注点专项响应"

SECTION_REVIEW_FOCUS_SPECS: dict[str, dict[str, Any]] = {
    "quality_schedule_commitment": {
        "keywords": ("质量目标", "验收标准", "工期承诺", "开工令", "开工日期", "节点考核", "资源调动", "现场管理", "履约责任", "整改复验"),
        "focuses": (
            (
                "质量目标与验收标准",
                ("质量目标", "验收标准", "整改复验"),
                "围绕{quality_phrase}建立质量目标分解、样板确认、材料报审、过程验收和整改复验机制；每个分项工程形成检查记录、验收结论和问题销项台账，保证质量承诺能够被复核。",
                "质量目标分解表、样板确认记录、检验批资料、整改复验记录",
            ),
            (
                "工期承诺与开工令响应",
                ("工期承诺", "开工令", "开工日期"),
                "工期承诺以{schedule_phrase}为控制依据，实际开工日期按发包人开工令和现场移交条件执行；项目部不擅自压缩工期，按总控计划、周计划和日协调落实节点考核。",
                "开工令、总控计划、周计划、节点复盘记录",
            ),
            (
                "资源调动与现场管理",
                ("资源调动", "现场管理", "履约责任"),
                "项目经理统筹劳动力、材料、施工机械设备、资金和专业配合资源，强化施工现场管理；对影响节点的事项及时调动资源、调整作业顺序并形成履约责任闭环。",
                "资源投入表、协调纪要、问题整改台账",
            ),
            (
                "履约检查与奖惩闭环",
                ("节点考核", "检查", "闭环"),
                "质量和工期履约纳入日检查、周复盘和节点考核，发现偏差后明确责任人、整改措施、完成时限和复查标准；复查合格后关闭，未关闭事项持续跟踪。",
                "节点考核表、整改通知单、复查记录",
            ),
        ),
    },
    "schedule_plan": {
        "keywords": ("总进度计划", "施工机械设备", "机械投入", "材料设备", "准备齐全", "交叉作业", "相互制约", "关键线路", "进度纠偏", "验收移交"),
        "focuses": (
            (
                "总控计划与关键线路",
                ("总进度计划", "关键线路", "验收移交"),
                "总进度计划按施工准备、样板确认、基层隐蔽、面层安装、机电末端配合、整改复验和验收移交设置关键线路，所有节点围绕{schedule_phrase}倒排。",
                "总控计划、阶段计划、验收移交清单",
            ),
            (
                "施工机械设备投入",
                ("施工机械设备", "机械投入", "准备齐全"),
                "根据施工阶段提前准备切割、打磨、钻孔、搬运、测量、临时配电、移动操作平台和清洁设备，进场前完成检查、维护和报验，保证施工机械设备准备齐全。",
                "机械设备进场计划、机具检查记录、维护保养记录",
            ),
            (
                "材料设备进场联动",
                ("材料设备", "材料进场", "设备进场"),
                "材料设备部按样板确认、采购加工、到货验收和现场领用节奏组织进场，重点材料提前锁定品牌规格、加工周期和运输保护要求，避免材料设备滞后影响节点。",
                "材料设备进场计划、采购台账、验收记录",
            ),
            (
                "交叉作业与进度纠偏",
                ("交叉作业", "相互制约", "进度纠偏"),
                "对多专业交叉作业可能出现的相互制约、工作面冲突、通道占用和成品污染提前排查，通过日协调、界面确认和进度纠偏措施恢复关键节点。",
                "交叉作业协调表、进度偏差台账、纠偏措施单",
            ),
        ),
    },
    "construction_organization": {
        "keywords": ("施工准备", "施工部署", "项目经理", "技术负责人", "施工机械", "工作面移交", "交叉配合", "成品保护", "资料移交", "应急协调"),
        "focuses": (
            (
                "施工准备与总体部署",
                ("施工准备", "施工部署", "工作面移交"),
                "施工准备阶段完成图纸会审、技术交底、现场移交、临时设施、材料计划和施工机械配置；总体部署按{work_zone}分区组织，先确认工作面移交条件再展开施工。",
                "施工准备清单、施工部署图、工作面移交单",
            ),
            (
                "项目组织与岗位责任",
                ("项目经理", "技术负责人", "岗位责任"),
                "项目经理统筹进度、质量、安全、材料和外部协调，技术负责人负责施工方法、节点深化、技术交底和验收资料，质量安全人员负责过程检查和整改复验。",
                "组织架构表、岗位职责表、交底记录",
            ),
            (
                "工序组织与交叉配合",
                ("施工机械", "交叉配合", "工序"),
                "按测量放线、基层处理、隐蔽施工、面层安装、机电末端、收口清洁和验收移交组织工序，施工机械、材料运输和专业穿插统一纳入现场协调。",
                "工序交接单、机械检查表、协调纪要",
            ),
            (
                "成品保护与资料移交",
                ("成品保护", "资料移交", "应急协调"),
                "已完工程和既有设施采取覆盖、围护、警示、专人巡查和损坏追责措施；资料移交与现场验收同步推进，突发界面问题通过应急协调机制处理。",
                "成品保护记录、资料移交清单、问题销项表",
            ),
        ),
    },
    "site_facility_management": {
        "keywords": ("办公室", "工具间", "材料间", "窗明地净", "洒水降尘", "责任人", "宿舍卫生", "食堂卫生", "消防器材", "领用登记"),
        "focuses": (
            (
                "办公室整洁与资料管理",
                ("办公室", "窗明地净", "责任人"),
                "办公室保持窗明地净、资料分类、标识清楚和责任人明确，会议协调、技术交底、资料整理和台账复核均在固定区域完成，避免资料散乱影响复核。",
                "办公室检查表、资料目录、责任牌",
            ),
            (
                "工具间领用与机具保养",
                ("工具间", "领用登记", "机具保养"),
                "工具间实行小型机具、检测工具、劳保用品和周转材料分类存放、领用登记、损坏隔离和维修保养，电动工具同步检查绝缘和防护装置。",
                "工具领用台账、机具保养记录、绝缘检查记录",
            ),
            (
                "材料间分类与消防临电",
                ("材料间", "消防器材", "临电"),
                "材料间按类别、规格、批次和使用区域分类堆放，易燃材料、胶粘剂、油漆类材料设置防火间距和消防器材，临时用电线路保持规范敷设。",
                "材料收发存台账、消防检查表、临电巡检表",
            ),
            (
                "后勤卫生与扬尘控制",
                ("宿舍卫生", "食堂卫生", "洒水降尘"),
                "现场生活后勤区域服从总承包单位管理，按宿舍卫生、食堂卫生和公共区域清洁要求落实检查；通道和临时堆放点根据现场情况适量洒水降尘。",
                "卫生检查记录、扬尘控制记录、整改台账",
            ),
        ),
    },
    "waste_management_plan": {
        "keywords": ("垃圾清运", "环境保护措施", "排放标准", "分类堆放", "封闭运输", "扬尘控制", "水资源", "再生利用", "消纳", "文明施工"),
        "focuses": (
            (
                "垃圾分类与清运责任",
                ("垃圾清运", "分类堆放", "责任"),
                "装修垃圾、包装物、边角料、拆改废料和可回收材料分类收集、分类堆放、随产随清，班组长和现场负责人共同落实垃圾清运责任。",
                "垃圾清运台账、分类检查表、责任分工表",
            ),
            (
                "封闭运输与消纳交接",
                ("封闭运输", "消纳", "外运"),
                "场内运输采用袋装、桶装、覆盖或封闭措施，按指定路线和时间组织；外运时配合计量、消纳、车辆清洁和交接确认，避免遗撒污染。",
                "运输记录、消纳交接单、路线清洁确认",
            ),
            (
                "环境保护与排放控制",
                ("环境保护措施", "排放标准", "扬尘控制"),
                "切割、打磨、拆除和清运环节落实环境保护措施，控制扬尘、噪声、异味、污水和固体废弃物排放，现场排放控制按招标文件和属地管理标准执行。",
                "环保检查表、扬尘噪声记录、整改复查记录",
            ),
            (
                "资源节约与再生利用",
                ("水资源", "再生利用", "文明施工"),
                "对可回收包装物、金属边角料、木质材料和可再利用周转材料分类回收；清洁用水、洒水降尘和冲洗用水按节约原则使用，减少资源浪费。",
                "回收记录、水资源使用记录、文明施工检查表",
            ),
        ),
    },
    "temporary_power_plan": {
        "keywords": ("现场电工", "安全技术规范", "操作规程", "职业道德", "供电线路", "用电设备", "绝缘程度", "运行情况", "停送电", "漏电保护"),
        "focuses": (
            (
                "现场电工职责",
                ("现场电工", "职业道德", "操作规程"),
                "现场电工必须熟悉施工现场临时用电安全技术规范和操作规程，履行巡检、维护、停送电审批、故障隔离和用电交底职责，保持良好职业道德和服务意识。",
                "电工岗位职责、持证资料、班前交底记录",
            ),
            (
                "供电线路与设备绝缘",
                ("供电线路", "用电设备", "绝缘程度"),
                "现场电工随时掌握供电线路、配电箱、开关箱、照明线路和用电设备的绝缘程度、运行情况和保护装置状态，发现破损、拖地、碾压或接头松动立即整改。",
                "线路检查记录、设备验收表、隐患整改单",
            ),
            (
                "配电保护与漏电试验",
                ("漏电保护", "三级配电", "一机一闸"),
                "临时用电执行三级配电、二级保护、一机一闸一漏一箱，配电箱编号、门锁、防雨、防砸和接地保护齐全，漏电保护器按规定进行动作试验。",
                "临电验收记录、漏保试验记录、配电箱巡检表",
            ),
            (
                "停送电与应急处置",
                ("停送电", "应急", "运行情况"),
                "停送电执行申请、审批、挂牌、复核、记录和人员告知流程；异常运行时先断电隔离，再查明原因、落实整改、复查合格后恢复供电。",
                "停送电记录、应急处置记录、复电确认单",
            ),
        ),
    },
    "material_procurement_plan": {
        "keywords": ("采购计划", "责任部门", "责任人", "品牌规格", "样板报审", "加工周期", "到货验收", "不合格材料", "替代审批", "风险预警"),
        "focuses": (
            (
                "采购计划与责任分工",
                ("采购计划", "责任部门", "责任人"),
                "材料采购由项目部、材料部门和专业班组按职责分工执行，明确需求提出、品牌规格复核、样板报审、采购下单、到货验收和领用追溯的责任部门和责任人。",
                "采购计划台账、责任分工表、需求审批记录",
            ),
            (
                "品牌规格与样板报审",
                ("品牌规格", "样板报审", "甲指乙供"),
                "对招标品牌、甲指乙供材料、规格型号、颜色纹理、环保性能、检测资料和使用部位进行复核，样板报审确认后作为批量采购和现场验收依据。",
                "品牌规格复核表、样板报审单、封样台账",
            ),
            (
                "加工周期与到货验收",
                ("加工周期", "到货验收", "进场批次"),
                "定制材料、饰面材料、五金配件和机电末端按加工周期、运输周期和施工节点倒排到货计划，到货后核对数量、外观、资料、批次和堆放防护。",
                "加工计划、到货验收记录、收发存台账",
            ),
            (
                "风险预警与替代审批",
                ("风险预警", "替代审批", "不合格材料"),
                "对供货滞后、停产、破损、色差、资料缺失和不合格材料建立风险预警机制；替代材料必须完成技术参数、样板实物、适用部位和审批记录确认。",
                "风险预警单、退换货记录、替代审批记录",
            ),
        ),
    },
    "safety_civil_fire": {
        "keywords": ("安全隐患", "文明施工区域", "油品", "化学品", "废弃物处理", "施工机具", "维修保养", "水资源", "动火审批", "消防通道"),
        "focuses": (
            (
                "文明施工区域落实",
                ("文明施工区域", "安全隐患", "消防通道"),
                "文明施工区域按责任分区落实材料定置、通道畅通、消防通道、安全出口、警示标识和工完场清要求，杜绝重大安全隐患长期存在。",
                "文明施工检查表、安全隐患台账、消防通道检查记录",
            ),
            (
                "油品化学品与废弃物处理",
                ("油品", "化学品", "废弃物处理"),
                "油品、胶粘剂、涂料、清洗剂等化学品采购、发放、使用和剩余物回收设置专项管理措施，废弃物按类别收集、封闭存放和合规处理。",
                "化学品领用台账、废弃物处理记录、专项检查表",
            ),
            (
                "施工机具检查维修",
                ("施工机具", "维修保养", "良好状态"),
                "对施工机具进行全面检查、维修保养和安全防护确认，重点核查电源线、开关、外壳、防护罩、漏电保护和操作人员交底，保证设备处于良好状态。",
                "施工机具检查表、维修保养记录、班前交底记录",
            ),
            (
                "节水环保与消防动火",
                ("水资源", "动火审批", "消防"),
                "现场清洁、洒水降尘和冲洗用水合理安排，减少水资源浪费；动火作业执行审批、隔离、监护、灭火器配置和作业后复查。",
                "水资源使用记录、动火审批单、消防巡查记录",
            ),
        ),
    },
    "quality_assurance": {
        "keywords": ("质量管理机构", "项目经理领导", "分项工程开工前", "预防措施", "治理方法", "关键过程", "质量问题分析", "技术管理记录", "验收制度", "整改复验"),
        "focuses": (
            (
                "质量管理机构与制度",
                ("质量管理机构", "项目经理领导", "验收制度"),
                "建立由项目经理领导、技术负责人组织、质量管理人员专职检查、班组自检互检的质量管理机构，分项工程按验收制度和技术交底要求组织实施。",
                "质量管理机构图、质量制度、岗位责任表",
            ),
            (
                "分项工程开工前预控",
                ("分项工程开工前", "预防措施", "治理方法"),
                "分项工程开工前完成图纸复核、样板确认、材料验收、工艺交底和质量通病预防措施及治理方法交底，凡达不到质量标准的条件不得进入大面积施工。",
                "开工前检查表、技术交底记录、样板确认单",
            ),
            (
                "关键过程质量控制",
                ("关键过程", "工序验收", "隐蔽验收"),
                "抓住测量放线、基层处理、龙骨隐蔽、管线配合、面层安装、收口处理和成品保护等关键过程进行质量控制，实施自检、专检、报验和旁站复核。",
                "工序验收记录、隐蔽验收记录、实测实量表",
            ),
            (
                "质量问题分析与技术记录",
                ("质量问题分析", "技术管理记录", "整改复验"),
                "对空鼓、开裂、起鼓、气泡、色差、接缝不顺、标高偏差和污染损坏等质量问题进行原因分析，形成整改措施、复验结论和技术管理记录。",
                "质量问题分析表、整改复验记录、技术管理台账",
            ),
        ),
    },
    "material_sample_plan": {
        "keywords": ("主要材料样板", "规格尺寸", "颜色纹理", "环保性能", "封样留存", "样板确认", "采购联动", "进场比对", "替代审批", "影像资料"),
        "focuses": (
            (
                "样板清单与规格尺寸",
                ("主要材料样板", "规格尺寸", "颜色纹理"),
                "主要材料样板按饰面材料、涂料胶粘剂、五金配件、门窗配套、机电末端和收口材料建立清单，逐项复核规格尺寸、颜色纹理、环保性能和适用部位。",
                "样板清单、规格复核表、适用部位表",
            ),
            (
                "报审确认与封样留存",
                ("样板确认", "封样留存", "影像资料"),
                "样板制作后进行编号、拍照、报审、确认和封样留存，确认样板作为采购、进场验收和现场施工的实物标准，影像资料同步归档。",
                "样板报审单、封样台账、影像资料",
            ),
            (
                "采购联动与进场比对",
                ("采购联动", "进场比对", "批量采购"),
                "批量采购前复核确认样板，材料进场时按品牌、规格、颜色、纹理、尺寸、检测资料和批次进行比对，偏差材料不得直接使用。",
                "采购计划、进场比对记录、材料验收台账",
            ),
            (
                "替代材料审批",
                ("替代审批", "样板实物", "复核"),
                "材料替代必须说明原因、技术参数、样板实物、质量影响和适用部位，经审批确认后实施，替代后的样板、采购和验收资料同步更新。",
                "替代审批记录、复核记录、更新样板台账",
            ),
        ),
    },
    "key_difficulty_analysis": {
        "keywords": ("重点难点", "难点分析", "应对措施", "工序", "相互制约", "空鼓", "起鼓", "气泡", "200mm", "mm"),
        "focuses": (
            (
                "重点难点识别",
                ("重点难点", "难点分析", "应对措施"),
                "本工程装修重点难点按工作面移交、专业交叉、材料供应、细部收口、质量通病、安全文明和成品保护分类识别，逐项制定应对措施和复核标准。",
                "重点难点清单、应对措施表、复核记录",
            ),
            (
                "交叉工序相互制约",
                ("工序", "相互制约", "交叉作业"),
                "对吊顶、墙面、地面、门窗、五金、机电末端和清洁收口等工序之间可能出现的相互制约提前排布作业顺序，明确工作面、时间段和成品保护责任。",
                "工序穿插计划、界面确认单、协调纪要",
            ),
            (
                "质量通病预防",
                ("空鼓", "起鼓", "气泡", "mm"),
                "针对空鼓、起鼓、气泡、裂缝、接缝不顺、阴阳角偏差和观感污染等问题设置预防措施；涉及尺寸、间距、平整度和垂直度的控制项目按毫米(mm)级偏差要求复核。",
                "质量通病预控表、实测实量记录、整改复验记录",
            ),
            (
                "细部节点与保护距离",
                ("200mm", "节点", "成品保护"),
                "细部节点收口、材料堆放、切割加工和成品保护按现场条件设置控制距离和保护措施；涉及通道、门洞、阴阳角和已完饰面时，可按不小于200mm的保护或避让控制原则复核。",
                "节点复核记录、保护措施检查表、影像资料",
            ),
        ),
    },
    "competitive_enhancement": {
        "keywords": ("投标竞争力", "精细化管理", "快速响应", "样板先行", "数字化台账", "持续改进", "服务承诺", "资料移交", "风险预警", "协同效率"),
        "focuses": (
            (
                "精细化组织优势",
                ("投标竞争力", "精细化管理", "协同效率"),
                "投标竞争力体现在精细化管理和协同效率，项目部通过总控计划、周计划、日协调和界面确认提升现场组织效率，减少交叉作业等待和重复返工。",
                "总控计划、协调纪要、界面确认单",
            ),
            (
                "样板先行与质量可视化",
                ("样板先行", "数字化台账", "质量"),
                "关键分项坚持样板先行和实测实量，样板、材料、隐蔽验收、质量问题和成品保护形成数字化台账，便于发包人、监理和项目部同步复核。",
                "样板台账、实测实量表、质量问题台账",
            ),
            (
                "快速响应与风险预警",
                ("快速响应", "风险预警", "持续改进"),
                "对材料供应、设计深化、工作面移交、质量整改和安全文明问题建立快速响应和风险预警机制，问题闭环后复盘原因并持续改进。",
                "风险预警单、问题销项表、复盘记录",
            ),
            (
                "服务承诺与资料移交",
                ("服务承诺", "资料移交", "持续服务"),
                "过程资料、验收资料、整改记录和竣工移交清单随施工同步整理，移交阶段快速响应复核意见，形成完整、可追溯、便于后续维护的资料包。",
                "资料移交清单、服务响应记录、竣工资料目录",
            ),
        ),
    },
}


def reinforce_technical_bid_section_review_focus(
    *,
    draft: BidDraftSection,
    component: Mapping[str, Any] | None,
    content: str,
    project_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    section_component = component or {}
    section_no = _section_no(section_component, draft)
    intent = _section_intent(section_component, draft)
    before_text = _plain_text(content)
    before_normalized = _normalize_for_contains(before_text)
    before_paragraph_count = _paragraph_count(content)
    before_visible_length = len(re.sub(r"\s+", "", before_text))
    spec = SECTION_REVIEW_FOCUS_SPECS.get(intent)
    keywords = tuple(str(item).strip() for item in (spec or {}).get("keywords", ()) if str(item).strip())
    matched_before = _matched_keywords(before_normalized, keywords)
    missing_before = [item for item in keywords if item not in matched_before]

    if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS or intent not in TECHNICAL_FORMAL_DEPTH_INTENTS or not spec:
        return _review_focus_result(
            content,
            changed=False,
            reason="fixed_material_section" if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS else "no_review_focus_spec",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=before_paragraph_count,
            after_visible_length=before_visible_length,
            matched_keywords_before=matched_before,
            missing_keywords_before=missing_before,
            matched_keywords_after=matched_before,
            missing_keywords_after=missing_before,
            added_focus_count=0,
            added_keyword_count=0,
            added_table_count=0,
            added_blocks=[],
        )
    if SECTION_REVIEW_FOCUS_REINFORCEMENT_HEADING in str(content or ""):
        return _review_focus_result(
            content,
            changed=False,
            reason="already_reinforced",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=before_paragraph_count,
            after_visible_length=before_visible_length,
            matched_keywords_before=matched_before,
            missing_keywords_before=missing_before,
            matched_keywords_after=matched_before,
            missing_keywords_after=missing_before,
            added_focus_count=0,
            added_keyword_count=0,
            added_table_count=0,
            added_blocks=[],
        )
    if not missing_before:
        return _review_focus_result(
            content,
            changed=False,
            reason="review_keywords_already_covered",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=before_paragraph_count,
            after_visible_length=before_visible_length,
            matched_keywords_before=matched_before,
            missing_keywords_before=missing_before,
            matched_keywords_after=matched_before,
            missing_keywords_after=missing_before,
            added_focus_count=0,
            added_keyword_count=0,
            added_table_count=0,
            added_blocks=[],
        )

    title = _section_title(draft, section_component, intent)
    lines = _review_focus_lines(title=title, intent=intent, spec=spec, project_context=project_context)
    reinforced_content = f"{str(content or '').rstrip()}\n\n" + "\n".join(lines).strip() + "\n"
    after_text = _plain_text(reinforced_content)
    after_normalized = _normalize_for_contains(after_text)
    matched_after = _matched_keywords(after_normalized, keywords)
    missing_after = [item for item in keywords if item not in matched_after]
    return _review_focus_result(
        reinforced_content,
        changed=True,
        reason="review_focus_keywords_reinforced",
        section_no=section_no,
        intent=intent,
        before_paragraph_count=before_paragraph_count,
        before_visible_length=before_visible_length,
        after_paragraph_count=_paragraph_count(reinforced_content),
        after_visible_length=len(re.sub(r"\s+", "", after_text)),
        matched_keywords_before=matched_before,
        missing_keywords_before=missing_before,
        matched_keywords_after=matched_after,
        missing_keywords_after=missing_after,
        added_focus_count=len(spec.get("focuses") or ()),
        added_keyword_count=max(0, len(matched_after) - len(matched_before)),
        added_table_count=1,
        added_blocks=[SECTION_REVIEW_FOCUS_REINFORCEMENT_HEADING, "评审关注点响应表"],
    )


def _review_focus_lines(
    *,
    title: str,
    intent: str,
    spec: Mapping[str, Any],
    project_context: Mapping[str, Any] | None,
) -> list[str]:
    focus = SECTION_TEMPLATE_INTENT_FOCUS.get(intent) or "评审关注点、过程控制和资料闭环"
    context = _review_context(project_context)
    lines = [
        f"## {SECTION_REVIEW_FOCUS_REINFORCEMENT_HEADING}",
        (
            f"围绕“{title}”的技术评审口径，本节将{focus}进一步转化为评审关注点、响应措施和复核资料。"
            f"相关内容结合{context['work_zone']}组织实施，并与{context['affected_zone']}的现场秩序、成品保护和专业协同保持一致。"
        ),
        "",
        "### 评审关注点响应表",
        "| 评审关注点 | 关键词 | 响应措施 | 复核资料 |",
        "| --- | --- | --- | --- |",
    ]
    focuses = [tuple(item) for item in spec.get("focuses") or ()]
    for item in focuses:
        title_text, keywords, body, record = _focus_tuple(item)
        lines.append(
            "| "
            + " | ".join(
                _table_cell(cell)
                for cell in (
                    title_text,
                    "、".join(str(keyword) for keyword in keywords),
                    _render_review_text(body, context),
                    record,
                )
            )
            + " |"
        )
    for item in focuses:
        title_text, keywords, body, record = _focus_tuple(item)
        lines.extend(
            [
                "",
                f"### {title_text}",
                _render_review_text(body, context),
                _review_focus_evidence_sentence(record, keywords),
            ]
        )
    return lines


def _focus_tuple(item: tuple[Any, ...]) -> tuple[str, tuple[str, ...], str, str]:
    title = str(item[0] if len(item) > 0 else "评审关注点").strip() or "评审关注点"
    keywords = tuple(str(keyword).strip() for keyword in (item[1] if len(item) > 1 else ()) if str(keyword).strip())
    body = str(item[2] if len(item) > 2 else "").strip()
    record = str(item[3] if len(item) > 3 else "检查记录、验收资料、整改台账").strip()
    return title, keywords, body, record


def _review_focus_evidence_sentence(record: str, keywords: tuple[str, ...]) -> str:
    keyword_text = "、".join(str(keyword).strip() for keyword in keywords if str(keyword).strip())
    if keyword_text:
        return f"过程复核资料以{record}为主，并围绕{keyword_text}等评审要点形成检查、验收和整改闭环。"
    return f"过程复核资料以{record}为主，作为检查、验收和整改闭环依据。"


def _review_context(project_context: Mapping[str, Any] | None) -> dict[str, str]:
    return {
        "work_zone": _project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域"),
        "affected_zone": _project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域"),
        "schedule_phrase": _schedule_phrase(project_context),
        "quality_phrase": _quality_phrase(project_context),
    }


def _schedule_phrase(project_context: Mapping[str, Any] | None) -> str:
    schedule = project_context.get("schedule") if isinstance(project_context, Mapping) else None
    if isinstance(schedule, Mapping):
        sentence = str(schedule.get("sentence") or "").strip()
        if sentence:
            return sentence
        duration = schedule.get("total_duration_days")
        if isinstance(duration, int) and duration > 0:
            return f"招标文件明确总工期为{duration}天"
        zones = []
        for zone in schedule.get("zones") or []:
            if not isinstance(zone, Mapping):
                continue
            zone_name = str(zone.get("name") or zone.get("zone") or "").strip()
            days = zone.get("duration_days")
            if zone_name and isinstance(days, int) and days > 0:
                zones.append(f"{zone_name}{days}天")
        if zones:
            return "、".join(zones)
    return "发包人开工令、招标文件约定工期和现场工作面移交条件"


def _quality_phrase(project_context: Mapping[str, Any] | None) -> str:
    quality = project_context.get("quality") if isinstance(project_context, Mapping) else None
    if isinstance(quality, Mapping):
        goal = str(quality.get("goal") or "").strip()
        if goal:
            return f"招标文件质量目标“{goal}”"
    return "招标文件、施工图纸和现行验收规范确定的质量标准"


def _project_context_phrase(project_context: Mapping[str, Any] | None, key: str, fallback: str) -> str:
    if isinstance(project_context, Mapping):
        value = str(project_context.get(key) or "").strip()
        if value:
            return value
    return fallback


def _section_title(draft: BidDraftSection, component: Mapping[str, Any], intent: str) -> str:
    for value in (
        draft.section_title,
        component.get("component_title") if isinstance(component, Mapping) else None,
        SECTION_TEMPLATE_INTENT_TITLES.get(intent),
    ):
        text = str(value or "").strip()
        if text:
            return text[:160]
    return "本章节"


def _render_review_text(template: str, context: Mapping[str, str]) -> str:
    text = str(template or "")
    for key, value in context.items():
        text = text.replace("{" + key + "}", value)
    return text


def _matched_keywords(normalized_text: str, keywords: tuple[str, ...]) -> list[str]:
    result = []
    for keyword in keywords:
        normalized_keyword = _normalize_for_contains(keyword)
        if normalized_keyword and normalized_keyword in normalized_text:
            result.append(keyword)
    return result


def _review_focus_result(
    content: str,
    *,
    changed: bool,
    reason: str,
    section_no: str,
    intent: str,
    before_paragraph_count: int,
    before_visible_length: int,
    after_paragraph_count: int,
    after_visible_length: int,
    matched_keywords_before: list[str],
    missing_keywords_before: list[str],
    matched_keywords_after: list[str],
    missing_keywords_after: list[str],
    added_focus_count: int,
    added_keyword_count: int,
    added_table_count: int,
    added_blocks: list[str],
) -> dict[str, Any]:
    return {
        "version": BID_TECHNICAL_SECTION_REVIEW_FOCUS_REINFORCEMENT_VERSION,
        "status": "applied" if changed else "no_action",
        "changed": changed,
        "reason": reason,
        "content": content,
        "section_no": section_no,
        "intent": intent,
        "paragraph_count_before": before_paragraph_count,
        "paragraph_count_after": after_paragraph_count,
        "visible_length_before": before_visible_length,
        "visible_length_after": after_visible_length,
        "matched_keyword_count_before": len(matched_keywords_before),
        "matched_keyword_count_after": len(matched_keywords_after),
        "missing_keywords_before": list(missing_keywords_before)[:30],
        "missing_keywords_after": list(missing_keywords_after)[:30],
        "added_keyword_count": added_keyword_count,
        "added_focus_count": added_focus_count,
        "added_table_count": added_table_count,
        "added_blocks": list(added_blocks)[:12],
    }


def _table_cell(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace("|", "｜")
    return text or "-"
