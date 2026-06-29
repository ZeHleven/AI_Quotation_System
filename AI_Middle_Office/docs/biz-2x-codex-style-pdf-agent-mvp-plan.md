# BIZ-2x Codex 式 PDF 识图列项 Agent MVP 规划

生成日期：2026-06-22

## 1. 目标对齐

本规划的目标，是把“用户直接把 PDF 图纸交给 Codex 后，Codex 通过看图生成四字段清单草稿”的工作方式，复制到当前系统中。

系统第一版要实现的最小闭环：

`上传 PDF 图纸 -> 高清渲染 -> 拆分图框/视图 -> AI 识图抽取证据 -> AI 归纳列项 -> 匹配国标清单名称 -> 输出四字段 Excel`

四字段固定为：

| 字段 | 要求 |
| --- | --- |
| 项目名称 | 采用“图纸具体做法名称（国标清单名称）” |
| 项目特征 | 来自图纸证据、材料代号、空间、做法、复核提示 |
| 单位 | 优先使用国标库匹配单位，其次使用 AI 建议单位 |
| 工程量 | V1 允许粗估，统一标记“约 xx，待复核”或“待复核” |

V1 的业务定位：

- 要复制 Codex 的识图列项方法和模式，而不是追求和某一次 Codex 输出结果一模一样。
- 要把“先看整套图纸、先提证据、再归纳清单”的过程产品化。
- 要让系统像 Codex 处理图纸那样，跨平面、地面、天花、立面、节点综合判断项目。
- 要把中间过程显性化，保留整页图、图框 manifest、证据 JSON、合并证据、清单归纳 JSON、国标匹配记录。
- 要把最终四字段 Excel 作为这个过程的结果物，而不是唯一目标。

### 1.1 Codex 方法拆解：复制过程，不复制某次结果

本规划参考的样本，是 Codex 直接处理“信达资产职工餐厅装饰 PDF”并生成四字段 Excel 的那次工作过程。

这份 Excel 的行数、类别和工程量，只用于帮助我们反推 Codex 的工作方法，不作为系统必须复刻的固定答案。

Codex 当时实际做的是一套过程：

| 步骤 | Codex 的做法 | 系统需要复制的模式 |
| --- | --- | --- |
| 1. 输入理解 | 判断 PDF 是一页 CAD 拼图，不是普通文本 PDF | 系统先识别 PDF 形态：整页图、拼图、单图框、多页图册 |
| 2. 高清渲染 | 把 PDF 渲染成高清图片 | 系统保留高清整页图，作为全局上下文 |
| 3. 图框拆分 | 把一页 CAD 拼图拆成多个独立视图 | 系统生成稳定的 view manifest，每个图框有 view_id |
| 4. 图纸类型判断 | 区分平面、地面、天花、立面、节点 | 系统要求模型先判断 view_type，而不是马上列项 |
| 5. 证据观察 | 从每张图里观察空间、材料代号、对象、做法、尺寸线索 | 系统第一阶段只抽证据，不输出最终清单 |
| 6. 跨图综合 | 把平面、立面、节点的证据放在一起理解 | 系统侧合并证据，再让模型做全局归纳 |
| 7. 具体做法命名 | 先生成“餐厅地面瓷砖铺贴CT系列”这类图纸做法名称 | 系统先生成具体做法名称，避免直接套国标大类 |
| 8. 国标归类 | 再把具体做法匹配到“块料楼地面”等国标类目 | 系统调用国标库，把国标名称放在括号里 |
| 9. 工程量处理 | 只做草稿级粗估，并标记待复核 | 系统 V1 允许粗估，但必须显式标注待复核 |
| 10. Excel 输出 | 把归纳结果整理为四字段清单 | 系统输出 Excel，同时保留 debug 过程文件 |

因此，V1 要复制的是这个方法链：

`整页理解 -> 图框拆分 -> 图纸类型判断 -> 证据抽取 -> 证据合并 -> 全局归纳 -> 具体做法命名 -> 国标匹配 -> 四字段输出`

不是复制某一次生成的 39 行，也不是要求每个项目、每个工程量与 Codex 那次输出一致。

### 1.2 Codex 结果如何用于规划

Codex 生成的清单结果只用于三个目的：

1. 观察 Codex 的工作方法最终会形成什么样的清单结构。
2. 帮助定义系统输出是否“像经过整套图纸综合判断”，而不是“像单图框局部识别”。
3. 作为人工验收时的参考样例，而不是固定答案。

这次 Codex 结果体现出的过程特征：

| 过程特征 | 说明 |
| --- | --- |
| 跨图判断 | 同时参考平面、地面、天花、立面、节点 |
| 证据驱动 | 项目来自图纸中可见的空间、材料、构件、做法 |
| 先具体后国标 | 先写图纸做法名称，再括号标注国标类目 |
| 合并重复 | 同类材料和做法不会因为多张立面重复出现而重复列项 |
| 草稿量意识 | 工程量以粗估和待复核表达，不伪装成精算 |
| 人工可接手 | 最终 Excel 可以让人继续删改、补量、修名称 |

这些才是 V1 的复制对象。

### 1.3 V1 方法对标标准

V1 是否成功，不首先看输出是否和 Codex 某次结果一样，而看系统是否完成了同样的方法步骤。

过程对标要求如下：

| 对标项 | V1 要达到的状态 |
| --- | --- |
| PDF 形态识别 | 系统知道输入是整页图纸、拼图图纸还是多页图册 |
| 整页上下文 | 保留并送入整页预览，帮助模型理解整体布局 |
| 视图拆分 | 每个 CAD 图框有稳定 view_id、页码、bbox、image_path |
| 图纸类型判断 | 每个视图尽量标出 plan、floor_plan、ceiling_plan、elevation、section、detail、schedule 等类型 |
| 证据先行 | 第一阶段输出 drawing_evidence，不直接生成最终清单 |
| 证据可追溯 | 每条材料、对象、做法、尺寸线索都绑定 source view_id |
| 证据合并 | 系统侧合并同材料、同对象、同做法，保留来源列表 |
| 全局归纳 | 第二阶段基于合并证据生成 bill_items，而不是逐图框拼接 |
| 具体做法命名 | 每行先生成图纸具体做法名称 |
| 国标匹配 | 系统再把具体做法名称匹配国标清单名称 |
| 工程量定位 | 工程量作为草稿级辅助字段，明确待复核 |
| 过程文件 | 输出证据 JSON、合并 JSON、归纳 JSON、国标匹配 CSV、四字段 Excel |

只要这些步骤没有建立起来，即使偶然生成了一份看起来不错的 Excel，也不能算复制了 Codex 的模式。

### 1.4 V1 输出物定位

V1 的最终 Excel 是过程产物，不是唯一验收对象。

V1 必须输出以下过程文件：

| 输出物 | 用途 |
| --- | --- |
| 整页渲染图 | 证明系统看到了整张图纸 |
| 图框裁切图 | 证明系统拆出了可识别视图 |
| `view_manifest.json` | 记录每个 view_id、页码、bbox、图片路径 |
| `drawing_evidence.json` | 记录模型从图中看到的证据 |
| `merged_evidence.json` | 记录系统合并后的全局证据 |
| `bill_items_raw.json` | 记录模型基于证据归纳出的原始清单项 |
| `standard_mapping.csv` | 记录具体做法名称如何匹配国标 |
| 四字段 Excel | 给用户人工修改的清单草稿 |

如果只有 Excel，没有过程文件，就不是 Codex 式方法，只是一次黑箱生成。

### 1.5 V1 方法失败边界

以下情况视为没有复制 Codex 方法：

1. 直接把每张图框送给模型生成项目，然后简单拼接。
2. 没有独立的证据抽取阶段。
3. 没有区分图纸类型。
4. 没有 view_id 证据来源。
5. 没有系统侧证据合并。
6. 没有基于全局证据做二次归纳。
7. 项目名称直接输出国标大类，没有图纸具体做法名称。
8. 工程量没有“待复核”定位，像正式精算结果。
9. Debug 文件不能解释每个项目从哪些证据来。

### 1.6 结果指标只作为观察项

V1 仍然可以记录结果指标，但它们只用于观察和调优，不作为“复制 Codex 方法”的定义。

观察指标包括：

| 指标 | 用途 |
| --- | --- |
| 输出行数 | 观察是否过少或过多 |
| 项目类别分布 | 观察是否过度集中在某一类 |
| 具体做法名称占比 | 观察是否保留了图纸语义 |
| 国标匹配成功率 | 观察标准库匹配质量 |
| 待复核工程量占比 | 观察工程量定位是否诚实 |
| 每行来源 view_id 覆盖率 | 观察可追溯性 |

信达样本中 Codex 生成过 39 行清单，这只能说明该样本在人工视觉处理下可能形成几十行草稿；系统不需要固定输出 39 行，也不需要和那份清单一模一样。

## 2. 当前问题判断

当前 PDF direct 链路大致是：

`PDF -> 渲染 -> CAD 图框裁切 -> 逐张图框调用 GLM -> 每张图框直接输出项目 -> 去重 -> 国标匹配 -> Excel`

这条链路已经具备工程基础，但与 Codex 直接处理图纸的方式有本质差异。

主要问题：

1. 当前模型在单张图框中直接列项，容易只抓最显眼、重复最多的标注，例如墙面 CT 瓷砖。
2. 当前链路缺少“整套图纸证据合并”阶段，模型看不到平面、天花、立面、节点之间的关联。
3. 当前链路缺少“先证据、后归纳”的中间层，模型直接输出最终清单，容易漏项和重复。
4. 多图框逐张请求 GLM 时容易触发限流，尤其一页 CAD 拼图切出几十个图框时。
5. 当前提示词即使继续优化，也仍然受“单图框局部理解”的上限约束。

因此，升级重点不是继续给单图框提示词打补丁，而是新增一条 Codex 式的全局图纸理解链路。

## 3. V1 总体架构

目标架构：

```text
PDF 上传
  ↓
PDF 高清渲染
  ↓
整页预览 + CAD 图框拆分
  ↓
图框选择与排序
  ↓
AI 证据抽取
  ↓
系统侧证据合并
  ↓
AI 全局清单归纳
  ↓
国标标准库匹配
  ↓
四字段 Excel / CSV / Debug JSON
```

与当前链路的核心差异：

| 当前 GLM PDF direct | Codex 式 Agent V1 |
| --- | --- |
| 单张图框直接列项 | 多图框先抽证据，再整体列项 |
| 局部识别 | 跨平面、地面、天花、立面、节点综合判断 |
| 去重发生在项目层 | 合并发生在证据层和项目层 |
| 输出容易集中在重复标注 | 输出按预算清单类别归纳 |
| 工程量多为待算量或单图粗估 | 工程量允许按整体图面粗估并明确待复核 |

## 4. 复用现有能力

V1 要尽量复用现有底座，避免重新扩大系统复杂度。

| 能力 | 现有文件 | V1 用法 |
| --- | --- | --- |
| PDF 文件收集 | `app/services/drawing_pdf_evidence_pipeline.py` | 继续复用 |
| PDF 高清渲染 | `app/services/drawing_pdf_evidence_pipeline.py` | 继续复用，默认 350dpi，可配置 |
| 3x3 切片 | `app/services/drawing_pdf_evidence_pipeline.py` | 作为 fallback |
| CAD 图框检测 | `app/services/cad_view_frame_detector.py` | 作为主输入视图 |
| 国标库检索 | `app/services/quantity_standard_index.py` | 继续复用 |
| 四字段导出 | `app/services/quantity_list_export.py` | 继续复用 |
| PDF 上传入口 | `app/api/v1/dwg_quantity_trial.py` | 新增独立 Agent 入口或 provider 参数 |

## 5. 建议新增模块

### 5.1 新增主编排服务

建议新增：

`app/services/drawing_pdf_agent_itemizer.py`

职责：

- 接收 PDF 上传目录和输出目录。
- 调用现有 PDF 渲染、图框拆分模块。
- 构建 view manifest。
- 选择整页预览和关键图框。
- 调用模型进行证据抽取。
- 合并证据。
- 调用模型进行全局清单归纳。
- 执行国标匹配。
- 写出四字段 Excel、CSV、Markdown、Debug JSON。

主函数建议：

```python
def run_pdf_agent_itemization(
    *,
    pdf_dir: str | Path,
    output_dir: str | Path,
    timestamp: str | None = None,
    render_dpi: int = 350,
    max_views: int | None = None,
    batch_size: int | None = None,
    model_provider: str | None = None,
) -> dict[str, Any]:
    ...
```

返回结构与当前 `run_pdf_direct_itemization` 保持接近，便于前端复用：

```json
{
  "ok": true,
  "phase": "BIZ-2x-pdf-agent-itemization",
  "summary": {},
  "quantity_list_rows": [],
  "agent_bill_items": [],
  "agent_evidence_rows": [],
  "standard_mapping_rows": [],
  "outputs": {},
  "issues": []
}
```

### 5.2 新增提示词模块

建议新增：

`app/services/drawing_pdf_agent_prompts.py`

职责：

- 存放证据抽取提示词。
- 存放全局清单归纳提示词。
- 存放 JSON schema 说明。
- 存放“图纸具体做法名称”生成规则。

原因：

- 避免把长提示词塞进主流程。
- 方便后续只调提示词，不动编排代码。
- 方便做 prompt regression。

### 5.3 模型网关扩展

优先在现有文件中扩展：

`app/services/model_gateway.py`

新增函数：

```python
async def call_pdf_agent_evidence_extract(...):
    ...

async def call_pdf_agent_bill_summarize(...):
    ...
```

V1 可先支持 OpenAI provider。GLM provider 可保留后续扩展，不作为第一版阻塞项。

如果后续 provider 逻辑变多，再拆出：

`app/services/openai_model_gateway.py`

## 6. 配置项规划

在 `app/core/config.py` 中新增：

```env
FEATURE_PDF_AGENT_ITEMIZATION=true
PDF_ITEMIZATION_PROVIDER=dashscope_agent

OPENAI_API_KEY=
OPENAI_VISION_MODEL=
OPENAI_DRAWING_AGENT_MAX_VIEWS=24
OPENAI_DRAWING_AGENT_BATCH_SIZE=8
OPENAI_DRAWING_AGENT_TIMEOUT_SECONDS=120
OPENAI_DRAWING_AGENT_RENDER_DPI=350
OPENAI_DRAWING_AGENT_INCLUDE_WHOLE_PAGE=true
OPENAI_DRAWING_AGENT_ENABLE_ROUGH_QUANTITY=true
DASHSCOPE_API_KEY=
DASHSCOPE_VISION_MODEL=qwen3.7-plus
DASHSCOPE_EVIDENCE_MODEL=qwen3.7-plus
DASHSCOPE_BILL_SUMMARY_MODEL=qwen3.7-plus
```

字段解释：

| 配置 | 作用 |
| --- | --- |
| `FEATURE_PDF_AGENT_ITEMIZATION` | 控制新链路是否启用 |
| `PDF_ITEMIZATION_PROVIDER` | `glm` 使用旧链路，`dashscope_agent` 使用 DashScope/Qwen 链路，`openai_agent` 使用 OpenAI 链路 |
| `OPENAI_API_KEY` | OpenAI API key，不提交 git |
| `OPENAI_VISION_MODEL` | 视觉模型名称，先留空由环境配置 |
| `DASHSCOPE_API_KEY` | DashScope API key，不提交 git |
| `DASHSCOPE_VISION_MODEL` | DashScope/Qwen 视觉模型，例如 `qwen3.7-plus` |
| `DASHSCOPE_EVIDENCE_MODEL` | DashScope/Qwen 证据抽取模型 |
| `DASHSCOPE_BILL_SUMMARY_MODEL` | DashScope/Qwen 清单归纳模型 |
| `OPENAI_DRAWING_AGENT_MAX_VIEWS` | 最多送入 Agent 的图框数量 |
| `OPENAI_DRAWING_AGENT_BATCH_SIZE` | 每批证据抽取最多图片数 |
| `OPENAI_DRAWING_AGENT_TIMEOUT_SECONDS` | 单次模型调用超时 |
| `OPENAI_DRAWING_AGENT_INCLUDE_WHOLE_PAGE` | 是否额外送整页预览图 |
| `OPENAI_DRAWING_AGENT_ENABLE_ROUGH_QUANTITY` | 是否允许生成粗估工程量 |

## 7. 图纸输入处理设计

### 7.1 渲染

默认使用现有渲染能力：

- `render_dpi=350`
- 输出整页 PNG
- 保存至 debug 目录

对于特别小字的 CAD 拼图，后续可加二次高分辨率局部裁图，但 V1 先不扩大范围。

### 7.2 图框拆分

优先使用：

`build_cad_view_frame_report`

图框 manifest 建议字段：

```json
{
  "view_id": "p001_view005",
  "source_file": "03.xxx.pdf",
  "page": 1,
  "tile_type": "cad_view_frame",
  "image_path": "...",
  "bbox_pixel": [0, 0, 100, 100],
  "priority": 250,
  "width": 438,
  "height": 319,
  "ink_ratio": 0.05
}
```

### 7.3 图框选择规则

V1 选择策略：

1. 要优先选择 CAD 图框，而不是普通 3x3 宫格。
2. 要额外保留整页预览图，帮助模型理解图纸整体布局。
3. 要按页面顺序、图框位置顺序稳定排序。
4. 要限制最大图框数，避免请求过大和成本失控。
5. 当图框数超过上限时，要保留不同区域的代表图框。

建议默认：

- 整页预览：1 张
- CAD 图框：最多 24 张
- 每批证据抽取：8 张

对于“信达职工餐厅”这种一页 33 个图框的 PDF，V1 预期送入：

- 1 张整页预览
- 24 张代表图框

后续如果效果不足，再升级为 33 张全量图框分批抽取。

## 8. 两阶段 AI 设计

### 8.1 第一阶段：证据抽取

目标：

让模型先看图纸，不直接生成最终清单，只抽取可追溯证据。

输入：

- 一批图框图片。
- 每张图框的 `view_id`。
- PDF 文件名、页码、图框顺序。

输出 schema：

```json
{
  "drawing_evidence": [
    {
      "view_id": "p001_view005",
      "view_title": "职工餐厅天花布置图",
      "view_type": "plan",
      "spaces": ["餐厅", "卫生间", "后厨"],
      "visible_texts": ["CT-01", "MT-01"],
      "material_codes": [
        {
          "code": "CT-01",
          "name_or_hint": "瓷砖",
          "spec_or_method": "地面/墙面铺贴",
          "confidence": 0.72
        }
      ],
      "objects": [
        {
          "name": "成品双开玻璃门",
          "space": "入口",
          "method": "安装",
          "unit_hint": "樘",
          "confidence": 0.78
        }
      ],
      "methods": [
        "墙面瓷砖湿贴",
        "地面瓷砖铺贴",
        "石膏板吊顶",
        "灯槽",
        "防火玻璃售卖窗口"
      ],
      "quantity_clues": [
        {
          "text": "25800 x 12800",
          "meaning": "整体平面尺寸",
          "confidence": 0.7
        }
      ],
      "evidence_notes": [
        "立面可见墙面砖分格和美缝说明",
        "节点可见售卖台和防火玻璃"
      ],
      "confidence": 0.76,
      "needs_manual_review": true
    }
  ]
}
```

证据抽取提示词要点：

- 要识别图纸类型：平面、地面、天花、立面、剖面、节点、材料表、图例。
- 要保留材料代号：CT、ST、MT、MR、PT 等。
- 要保留空间：餐厅、后厨、售卖区、卫生间、洗手区、入口等。
- 要保留对象：门、窗、墙面、地面、天棚、柱、台面、隔断、灯槽、窗帘盒、售卖口等。
- 要保留做法：湿贴、铺贴、吊顶、涂料、包柱、门套、收边、美缝、防水等。
- 要保留工程量线索：尺寸、数量、分格、标高、范围。
- 要用 `view_id` 绑定每条证据来源。
- 要只输出 JSON。

### 8.2 第二阶段：系统侧证据合并

系统要把多批证据合并成统一证据库。

合并规则：

| 合并对象 | 规则 |
| --- | --- |
| 材料代号 | 同一 code 合并，保留所有来源 view_id |
| 同类做法 | 同一空间 + 同一对象 + 同一做法合并 |
| 图名 | 同一 view_id 保留最可信图名 |
| 数量线索 | 不强行计算，只汇总到项目归纳上下文 |
| 低置信证据 | 保留，但标记 `needs_manual_review=true` |

合并后输出：

`openai_drawing_evidence.json`

建议结构：

```json
{
  "phase": "pdf-agent-evidence-merge",
  "view_count": 24,
  "evidence_count": 120,
  "merged_materials": [],
  "merged_objects": [],
  "merged_methods": [],
  "quantity_clues": [],
  "source_views": []
}
```

这个文件非常重要，是后续人工调试和信任建立的核心。

### 8.3 第三阶段：全局清单归纳

目标：

让模型基于合并后的整套图纸证据，像预算员一样生成四字段清单候选。

输入：

- 合并后的证据 JSON。
- 整页图纸概览摘要。
- “图纸具体做法名称”生成规则。
- V1 工程量粗估规则。

输出 schema：

```json
{
  "bill_items": [
    {
      "concrete_item_name": "餐厅地面瓷砖铺贴CT系列",
      "feature": "餐厅主要区域地面块料铺装；材料代号CT系列；含结合层、勾缝/美缝；来源p001_view004,p001_view014",
      "unit": "m2",
      "rough_quantity": "约135",
      "quantity_note": "按图面区域粗估，待复核",
      "source_view_ids": ["p001_view004", "p001_view014"],
      "source_evidence": ["地面铺装图可见CT标注", "立面图备注墙地砖作美缝处理"],
      "confidence": 0.78,
      "needs_manual_review": true,
      "reason": "地面图和立面备注均支持该列项"
    }
  ]
}
```

清单归纳提示词要点：

- 要先基于图纸证据生成具体做法名称。
- 要把重复图框证据归并成一条清单项。
- 要覆盖装饰工程常见分部：拆除、地面、墙面、天棚、门窗、玻璃、台面、隔断、金属线条、成品保护等。
- 要优先使用图纸证据中的材料代号和做法。
- 要对弱证据项目标记人工复核。
- 要工程量粗估时使用“约 xx，待复核”。
- 要只输出 JSON。

## 9. 具体做法名称生成规则

V1 名称格式：

`空间/部位 + 材料/代号 + 施工方式 + 构件/面层`

常见组合：

| 图纸证据 | 具体做法名称 |
| --- | --- |
| 餐厅 + CT + 地面铺装 | 餐厅地面瓷砖铺贴CT系列 |
| 卫生间 + 墙面 + CT + 湿贴 | 卫生间墙面瓷砖湿贴CT系列 |
| 天花 + 石膏板 + 跌级 | 跌级造型石膏板吊顶 |
| 立面 + 售卖口 + 防火玻璃 | 防火玻璃售卖窗口 |
| 门洞 + MT + 不锈钢 | 金属门套/不锈钢门套 |
| 墙脚 + 不锈钢 | 不锈钢踢脚线 |
| 洗手区 + 台盆 | 洗手台及人造石台面 |

国标显示名称：

`具体做法名称（国标清单名称）`

示例：

| 具体做法名称 | 国标匹配后显示 |
| --- | --- |
| 餐厅地面瓷砖铺贴CT系列 | 餐厅地面瓷砖铺贴CT系列（块料楼地面） |
| 墙面瓷砖湿贴CT系列 | 墙面瓷砖湿贴CT系列（块料墙面） |
| 跌级造型石膏板吊顶 | 跌级造型石膏板吊顶（吊顶天棚） |
| 成品双开玻璃门 | 成品双开玻璃门（金属玻璃门） |
| 防火玻璃售卖窗口 | 防火玻璃售卖窗口（防火玻璃） |

## 10. 国标匹配设计

继续复用：

`search_standard_index(query, limit=5)`

查询文本建议：

```text
concrete_item_name
feature
source_evidence
space
method
unit
```

匹配后补充轻量约束，减少明显错配：

| 关键词 | 优先匹配方向 |
| --- | --- |
| 地面、楼地面、门槛石 | 楼地面工程 |
| 墙面、柱面、包柱 | 墙柱面工程 |
| 天棚、吊顶、灯槽 | 天棚工程 |
| 门、门套、玻璃门 | 门窗工程 |
| 隔断、玻璃隔断 | 隔断工程 |
| 涂料、乳胶漆、无机涂料 | 涂饰工程 |
| 拆除 | 拆除工程 |
| 成品保护、保洁、二次搬运 | 措施项目 |

V1 不重建国标库，只在现有国标检索结果上增加上下文约束。

## 11. 工程量 V1 规则

V1 工程量定位：

工程量是草稿级，不作为结算依据。

优先级：

1. 图纸中有明确尺寸和范围时，AI 可给粗估。
2. 图纸中只有对象和做法时，输出“待复核”。
3. 对常规措施项，可输出“1，待复核”或按面积粗估。
4. 所有 AI 生成工程量都要带“待复核”。

输出格式：

| 情况 | 输出 |
| --- | --- |
| 可粗估面积 | `约135，待复核` |
| 可粗估数量 | `约5，待复核` |
| 无依据 | `待复核` |
| 措施项 | `1，待复核` |

后续精算阶段再单独做：

- CAD 几何面积。
- 线性长度。
- 门窗数量。
- 灯具/洁具数量。
- 墙面扣洞口。
- 防水翻边。

## 12. 输出文件规划

业务输出目录继续放在：

`outputs/biz2x_trial/business/{timestamp}/`

Debug 输出目录继续放在：

`outputs/biz2x_trial/debug/{timestamp}/`

建议输出：

| 文件 | 说明 |
| --- | --- |
| `BIZ2x_PDF_Agent四字段清单_{timestamp}.xlsx` | 给用户看的四字段 Excel |
| `BIZ2x_PDF_Agent四字段清单_{timestamp}.csv` | 同步 CSV |
| `BIZ2x_PDF_Agent识图证据_{timestamp}.json` | 证据抽取和合并结果 |
| `BIZ2x_PDF_Agent清单归纳_{timestamp}.json` | AI 归纳出的原始 bill_items |
| `BIZ2x_PDF_Agent国标匹配_{timestamp}.csv` | 国标匹配调试表 |
| `BIZ2x_PDF_Agent运行报告_{timestamp}.md` | 人工阅读报告 |

## 13. API 接入规划

### 推荐方案：新增独立测试入口

新增接口：

`POST /api/v1/admin/dwg-quantity-trial/list-items-from-pdf-agent`

原因：

- 保留现有 GLM 链路不受影响。
- 方便对比旧链路和新链路。
- 避免在一个接口里堆太多 provider 分支。
- 符合当前“先审核、先试运行”的阶段。

请求：

```http
multipart/form-data
pdf_files: File[]
```

可选参数后续再加：

```json
{
  "max_views": 24,
  "include_whole_page": true,
  "enable_rough_quantity": true
}
```

响应继续复用 `_listing_response_payload(report)`，确保前端能拿到：

- `quantity_list_rows`
- `outputs.quantity_list_xlsx`
- `debug_files`
- `summary`
- `issues`

### 后续方案：provider 参数切换

等新链路稳定后，再考虑把旧接口扩展为：

`POST /api/v1/admin/dwg-quantity-trial/list-items-from-pdf?provider=openai_agent`

V1 暂不推荐直接做。

## 14. 前端接入规划

V1 最小前端方案：

1. 先不改主工作台。
2. 在现有 PDF 识图试运行页面增加一个“Agent 识图”按钮。
3. 点击后调用新接口。
4. 结果展示继续使用现有四字段 Excel 下载逻辑。

按钮文案建议：

- `生成清单草稿（Agent）`
- `旧版GLM识图`

V1 页面结果至少展示：

| 展示项 | 说明 |
| --- | --- |
| 生成状态 | 成功/失败/部分成功 |
| 识别项目数 | 四字段行数 |
| 证据图框数 | 实际送入模型图框数 |
| 输出 Excel | 下载链接 |
| Debug JSON | 管理员可见 |
| 风险提示 | 工程量为粗估，需复核 |

## 15. 提示词草案

### 15.1 证据抽取提示词草案

```text
你是装饰工程图纸识图助手。

任务：根据输入的 PDF 图纸视图图片，抽取可用于生成工程量清单的图纸证据。

你要完成：
1. 要识别每张图的图纸类型：平面、地面、天花、立面、剖面、节点、材料表、图例或未知。
2. 要提取图中可见的空间、材料代号、构件对象、施工做法、尺寸线索和文字说明。
3. 要把每条证据绑定到对应 view_id。
4. 要保留材料代号，例如 CT、ST、MT、MR、PT 等。
5. 要保留做法词，例如铺贴、湿贴、吊顶、涂料、防水、美缝、门套、收边、隔断、售卖口、台面。
6. 要给每张图和每条主要证据标注置信度。
7. 要对文字看不清或推断成分较强的内容标记 needs_manual_review=true。
8. 要只输出 JSON。

输出 JSON schema：
{
  "drawing_evidence": [
    {
      "view_id": "",
      "view_title": "",
      "view_type": "",
      "spaces": [],
      "visible_texts": [],
      "material_codes": [
        {
          "code": "",
          "name_or_hint": "",
          "spec_or_method": "",
          "confidence": 0.0
        }
      ],
      "objects": [
        {
          "name": "",
          "space": "",
          "method": "",
          "unit_hint": "",
          "confidence": 0.0
        }
      ],
      "methods": [],
      "quantity_clues": [
        {
          "text": "",
          "meaning": "",
          "confidence": 0.0
        }
      ],
      "evidence_notes": [],
      "confidence": 0.0,
      "needs_manual_review": true
    }
  ]
}
```

### 15.2 清单归纳提示词草案

```text
你是装饰工程预算员。

任务：根据已经抽取并合并的 PDF 图纸证据，生成一份可供人工继续修改的四字段工程量清单草稿。

你要完成：
1. 要先生成“图纸具体做法名称”，再由系统匹配国标清单名称。
2. 具体做法名称要体现空间或部位、材料代号、施工方式、构件或面层。
3. 要把平面图、地面图、天花图、立面图、节点图中的证据综合起来判断。
4. 要把重复证据归并成一条清单项。
5. 要覆盖图纸中出现的主要项目类型：拆除、地面、墙面、天棚、门窗、玻璃、隔断、台面、线条、成品保护等。
6. 项目特征要写明材料代号、做法、空间、来源 view_id、复核提示。
7. 单位要根据项目类型选择 m2、m、樘、个、组、项等。
8. 工程量有图面依据时可粗估，格式使用“约xx，待复核”。
9. 工程量依据不足时填“待复核”。
10. 每行要保留 source_view_ids 和 source_evidence。
11. 要只输出 JSON。

输出 JSON schema：
{
  "bill_items": [
    {
      "concrete_item_name": "",
      "feature": "",
      "unit": "",
      "rough_quantity": "",
      "quantity_note": "",
      "source_view_ids": [],
      "source_evidence": [],
      "confidence": 0.0,
      "needs_manual_review": true,
      "reason": ""
    }
  ]
}
```

## 16. 实施阶段

### 阶段 0：文档审核

产物：

- 本规划文档。

验收：

- 用户确认目标、边界、输出质量、模型路线。

### 阶段 1：后端骨架和单元测试

目标：

在不调用真实模型的情况下，把主流程跑通。

改动：

- 新增 `drawing_pdf_agent_itemizer.py`
- 新增 `drawing_pdf_agent_prompts.py`
- 新增测试 `tests/test_drawing_pdf_agent_itemizer_biz2x.py`

测试覆盖：

- view manifest 构建。
- 图框选择规则。
- 证据 JSON 解析。
- 证据合并。
- bill_items 转四字段。
- 国标匹配 fallback。
- Excel 输出。

验收：

- 使用 fake model 响应时，能生成四字段 Excel。
- 不需要 OpenAI key。

### 阶段 2：模型网关接入

目标：

接入 OpenAI 视觉模型调用。

改动：

- `config.py` 新增 OpenAI Agent 配置。
- `.env.example` 新增配置项。
- `model_gateway.py` 新增两个调用函数。
- 增加 JSON 解析和异常处理。

验收：

- 配置 API key 后，可以对 1-2 张图框完成证据抽取 smoke。
- 模型失败时返回 issues，不让整个接口崩溃。

### 阶段 3：全局清单归纳和国标匹配

目标：

跑通完整 Agent 链路。

改动：

- Agent 服务调用证据抽取。
- 合并证据。
- 调用清单归纳。
- 调用 `search_standard_index`。
- 生成四字段 Excel。

验收：

- 使用信达餐厅 PDF，能输出至少 25 项清单草稿。
- 项目类型覆盖地面、墙面、天棚、门窗、玻璃、台面、措施项。
- 单一类别占比要可控，例如“墙面瓷砖湿贴”不能占全部项目的一半以上。
- 每行项目名称采用“具体做法名称（国标清单名称）”。

### 阶段 4：API 和前端测试入口

目标：

让用户能在系统上传 PDF 并下载 Agent 清单。

改动：

- `dwg_quantity_trial.py` 新增 Agent endpoint。
- 前端新增测试按钮或入口。
- 复用现有下载逻辑。

验收：

- 页面上传 PDF 后能生成 Excel。
- 页面能展示识别行数和输出文件。
- Debug 文件管理员可查看。

### 阶段 5：人工验收与提示词小迭代

目标：

基于真实 PDF 进行 3-5 轮人工验收。

验收指标：

- 是否漏掉明显类别。
- 是否重复输出同类项目。
- 是否项目名称像人工清单。
- 是否国标括号名称基本合理。
- 是否工程量表达符合“草稿、待复核”的定位。

本阶段只调：

- 提示词。
- 图框选择数量。
- 合并规则。
- 国标匹配轻约束。

暂时不做：

- 精确算量。
- 数据库持久化。
- 自动报价下发。

## 17. 测试计划

### 17.1 单元测试

新增测试文件：

`tests/test_drawing_pdf_agent_itemizer_biz2x.py`

覆盖：

| 测试 | 说明 |
| --- | --- |
| `test_select_agent_views_prefers_cad_views` | 优先选择 CAD 图框 |
| `test_select_agent_views_keeps_whole_page` | 保留整页预览 |
| `test_parse_evidence_json_normalizes_rows` | 证据 JSON 可解析 |
| `test_merge_evidence_keeps_source_view_ids` | 合并后保留来源 |
| `test_bill_items_to_four_fields_uses_standard_name` | 项目名称生成括号国标名 |
| `test_agent_itemization_writes_quantity_list_outputs` | 可写出 Excel/CSV |
| `test_agent_handles_model_error_as_issue` | 模型失败变成 issue |

### 17.2 集成测试

使用 monkeypatch fake 模型响应：

- fake 证据抽取返回 CT、天花、门、售卖口。
- fake 清单归纳返回 5-10 行。
- 验证最终输出四字段完整。

### 17.3 人工 smoke

使用信达餐厅 PDF：

- 上传 PDF。
- 下载 Excel。
- 人工看项目类型。
- 人工检查是否像 Codex 直接生成的那类草稿。

## 18. 验收标准

以信达职工餐厅装饰 PDF 为首个验收样本。

最低通过标准：

| 指标 | 标准 |
| --- | --- |
| 输出文件 | 生成四字段 Excel |
| 行数 | 25-50 行为合理区间 |
| 项目类型 | 至少覆盖地面、墙面、天棚、门窗、玻璃/隔断、台面/售卖台、措施项 |
| 项目名称 | 大部分采用“具体做法名称（国标名称）” |
| 项目特征 | 大部分包含空间/材料/做法/来源提示 |
| 工程量 | 有粗估或待复核，不出现大量空值 |
| 可追溯 | Debug JSON 能看到来源 view_id |
| 稳定性 | 模型失败时可返回错误说明，不生成空白无提示结果 |

第一版不要求：

- 与人工 Excel 行数一致。
- 与人工工程量一致。
- 所有项目国标匹配完全正确。
- 自动进入报价流程。

## 19. 风险与控制

| 风险 | 控制方式 |
| --- | --- |
| 模型成本升高 | 限制 max_views 和 batch_size |
| 请求超时 | 分批证据抽取，失败批次记录 issue |
| 输出 JSON 不合法 | 增加 JSON 提取和 schema 容错 |
| 项目重复 | 证据层合并 + 项目层合并 |
| 仍然漏项 | 调整图框选择和清单归纳提示词 |
| 国标错配 | 增加轻量上下文约束 |
| 工程量被误认为精算 | 全部带“待复核”，前端和 Excel 增加说明 |
| 工作区再次变复杂 | 新增模块控制在 2-3 个，实验代码不散落 |

## 20. 不纳入 V1 的事项

V1 暂不做：

- 精确工程量计算。
- CAD/DWG 几何解析。
- 自动报价。
- 成本库入库。
- 数据库持久化。
- 多专业完整融合。
- Togal/Kreo 式几何算量能力。
- 复杂人工复核工作台。

这些内容放到后续阶段。

## 21. 建议开发顺序

建议按以下顺序执行：

1. 确认本规划。
2. 新增后端 Agent 骨架，先用 fake model 跑通。
3. 接 OpenAI 视觉模型做证据抽取。
4. 接全局清单归纳。
5. 接国标匹配和 Excel 输出。
6. 用信达 PDF 做人工验收。
7. 根据验收调提示词和图框选择。
8. 再接前端按钮。

原因：

- 先把核心链路跑通，再接前端。
- 先用 fake model 保证系统结构稳定。
- 再接真实模型，避免调 API 时同时调业务流程。

## 22. 待用户确认项

开工前需要确认：

1. V1 是否确定以 OpenAI Agent 链路为主，GLM 旧链路保留对照。
2. V1 是否接受工程量全部为“约 xx，待复核”或“待复核”。
3. V1 是否先只做装饰 PDF，不覆盖水电专业。
4. V1 是否新增独立接口，而不是直接改旧 PDF 识图接口。
5. V1 是否允许上传图纸图片到 OpenAI API。
6. V1 首个验收样本是否继续使用信达职工餐厅 PDF。

建议默认选择：

- 使用 OpenAI Agent 链路。
- 旧 GLM 链路保留。
- 工程量按草稿粗估。
- 新增独立测试接口。
- 先做装饰专业。
- 首个样本使用信达餐厅 PDF。
