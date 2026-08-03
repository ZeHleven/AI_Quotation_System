# BIZ-2x DWG 图纸识别生成工程量清单路线文档

日期：2026-06-10

## 1. 定位

BIZ-2x 是现有报价链路之前的“图纸识别生成标准清单”模块。它负责把 DWG 施工图中的工程概况、目录、设计说明、材料表、构造做法表、平面图、立面图和大样图信息，整理成可人工确认的工程量清单草稿。

本模块输出的最终 Excel 只包含四个业务字段：

| 字段 | 口径 |
| --- | --- |
| 项目名称 | 依据图纸内容识别并归并到 GB/T 50854-2024 对应清单项目或企业标准项目 |
| 项目特征 | 必须使用标准库中的项目特征字段口径，按标准字段顺序组织，不允许 AI 自由发挥 |
| 单位 | 必须来自标准库该项目的计量单位或人工确认的等价单位 |
| 工程量 | 必须按标准库中的工程量计算规则生成；不能按规则计算时必须标记为待人工确认 |

它不替代现有报价系统，不计算价格，不下发钉钉，不自动沉淀成本库，不自动启用 `cost_items.active`。人工确认后的清单可以继续进入现有 `/api/v1/quote/jobs` 异步报价链路。

## 2. 已确认业务约束

1. 项目特征必须来自《GB/T 50854-2024 房屋建筑与装饰工程工程量计算标准》的标准库字段口径。
2. 工程量必须来自同一标准库中的工程量计算规则。
3. AI 可以辅助识别图纸内容、表格、标注和候选项目，但不能跳过标准库规则直接生成最终项目特征或工程量。
4. 每一条清单行必须保留内部证据链：来源 DWG、图号/布局、来源区域、识别文本、标准库条目、项目特征字段填充情况、工程量计算规则和计算依据。
5. 最终导出的 Excel 为业务简表，只显示“项目名称、项目特征、单位、工程量”；证据链保存在系统内部和确认页面中。
6. 图纸识别结果必须先进入人工确认页面，确认后才能导出或发起报价。

## 3. 样例与初始事实

本轮业务样例：

- 标准文件：`（总）GBT50854-2024 房屋建筑与装饰工程工程量计算标准.pdf`
- 样例清单：`信达公司职工食堂装修改造工程量清单.xls`
- 样例图纸：
  - `01.前言文件.dwg`
  - `02.通用节点【一】.dwg`
  - `03.整理完毕1017信达资产职工餐厅施工图.dwg`
  - `04.信达资产职工餐厅水电施工图.dwg`

当前轻量探测结论：

- 标准 PDF 共 108 页，当前文件没有可直接抽取的文本层，需要 OCR 或人工结构化导入。
- 样例 DWG 文件头为 `AC1018`，属于 AutoCAD 2004/2005/2006 代际格式，适合通过独立 CAD 转换服务处理。
- 当前系统已有需求单标准化、报价任务、预审人工确认、成本库参考和证据链能力，BIZ-2x 应复用这些能力，不新建报价引擎。

## 4. 总体流程

```text
上传 DWG 图纸包
  -> 创建 drawing_recognition_jobs 异步任务
  -> DWG 转换为 PDF/PNG 预览 + CAD 结构化中间数据
  -> 提取标题栏、图纸目录、设计说明、材料表、构造做法表、平立剖/大样标注
  -> 生成图纸元素库与证据坐标
  -> 匹配 GB/T 50854-2024 标准项目库
  -> 按标准项目特征字段填充项目特征
  -> 按标准工程量计算规则计算或标记待补量
  -> 生成四字段工程量清单草稿
  -> 人工核对、补量、合并、删除、确认
  -> 导出 Excel 或发起现有报价任务
```

## 5. 标准库设计

### 5.1 标准库来源

标准库首版来自《GB/T 50854-2024 房屋建筑与装饰工程工程量计算标准》。由于当前 PDF 无文本层，标准库建设分两步：

1. OCR/人工校对标准条文，形成结构化表。
2. 将结构化表导入系统，后续所有图纸识别结果都只能引用该结构化标准库。

### 5.2 标准库核心字段

建议新增标准库结构时至少包含：

| 字段 | 说明 |
| --- | --- |
| `standard_version` | 标准版本，例如 `GBT50854-2024` |
| `chapter_code` | 章节或专业分类 |
| `item_code` | 清单项目编码或内部标准项目编码 |
| `item_name` | 标准项目名称 |
| `feature_fields_json` | 项目特征字段列表，包含字段名、顺序、是否必填、示例值 |
| `unit_options_json` | 标准计量单位与可接受别名 |
| `quantity_rule_text` | 标准工程量计算规则原文或结构化描述 |
| `quantity_formula_json` | 可执行/半可执行的计算模板 |
| `drawing_evidence_requirements_json` | 计算该工程量需要哪些图纸证据，例如长度、面积、展开面积、数量、厚度、做法范围 |
| `keywords_json` | 图纸识别和归类关键词 |
| `exclusion_keywords_json` | 排除关键词，避免误分类 |
| `source_page` | 标准 PDF 页码或校对来源 |
| `status` | `draft/active/archived`，首版必须人工审核后 active |

### 5.3 项目特征生成规则

项目特征生成必须遵守以下原则：

- 先确定标准库 `item_code/item_name`，再按该项目的 `feature_fields_json` 填字段。
- 项目特征文本按标准库字段顺序拼接，例如“材料品种、规格；基层类型；做法厚度；施工部位”。
- 图纸中缺失的必填特征不得由 AI 猜测，可填“待确认”并标记阻断或警告。
- 图纸中识别到但标准库没有的描述，放入内部 `extra_notes`，不能随意塞进项目特征主字段。
- 项目特征字段必须能反查来源证据：设计说明、材料表、构造做法表、节点大样或人工补录。

### 5.4 工程量计算规则

工程量必须遵守标准库中的 `quantity_rule_text/quantity_formula_json`：

- 能从图纸证据计算的，生成 `quantity`、`quantity_basis` 和 `calculation_trace`。
- 只能识别项目但缺少尺寸、范围、比例或标注的，`quantity` 为空或标记为待补量。
- 不允许 AI 按经验估算工程量后直接进入最终清单。
- 对同一项目存在多个工程量候选时，必须展示候选来源，人工选择后才能确认。
- 图纸比例、单位、闭合区域、扣减规则、展开面积规则不可靠时，必须提示人工复核。

## 6. DWG 解析与转换层

DWG 是专有格式，系统不应只依赖单一 Python 解析器。建议做独立转换层：

1. DWG 原文件入库或存入 MinIO。
2. 使用 CAD 转换服务将每个 layout/model 转为 PDF/PNG 预览。
3. 尽可能导出 DXF 或 JSON 中间结构，包含文字、表格、图层、块、标注、线段、多段线、尺寸、标题栏和布局信息。
4. 对 PDF/PNG 做 OCR 和视觉表格识别，补充 CAD 结构化提取遗漏。
5. 所有识别结果归一到统一的 `drawing_elements` 结构。

转换服务可选路线：

- ODA File Converter / Teigha 类工具：适合批量 DWG 转 DXF/PDF。
- AutoCAD 授权环境：识别稳定但部署和授权成本高。
- 要求客户同时上传 PDF 图纸：作为首版低风险过渡方案。
- DXF 优先支持：DWG 转换失败时提示另存 DXF/PDF 后重试。

首版建议以“DWG 转预览 + CAD 文本/表格提取 + OCR 补充”为目标，不承诺完整自动算量。

## 7. 图纸内容识别模块

### 7.1 图纸目录与标题栏

识别：

- 图号
- 图名
- 专业
- 版本
- 比例
- 日期
- 设计单位
- 项目名称

作用：

- 给每条清单行建立来源图纸索引。
- 判断哪些图纸属于装饰、建筑、给排水、电气等专业。
- 对超出 GB/T 50854-2024 房屋建筑与装饰范围的内容标记为“非本标准覆盖，需人工确认”。

### 7.2 设计说明

识别：

- 施工范围
- 通用材料
- 通用做法
- 防火、防水、防腐、环保等说明
- 适用房间或区域

作用：

- 填充项目特征字段。
- 给平面/立面图中只出现索引编号的项目补充做法说明。

### 7.3 材料表

识别：

- 材料名称
- 规格型号
- 品牌/等级
- 单位
- 使用部位
- 备注

作用：

- 生成或补充清单项目特征。
- 不是所有材料表条目都直接生成施工项目；需要结合标准库和构造做法表判断。

### 7.4 构造做法表

识别：

- 部位
- 面层
- 基层
- 龙骨/骨架
- 厚度
- 工艺做法
- 节点编号

作用：

- 这是项目特征的重要来源。
- 做法表优先级高于自由文字识别结果。

### 7.5 平面图、立面图、大样图

识别：

- 房间/区域
- 构件边界
- 长度、宽度、高度、面积、数量标注
- 索引编号和节点编号
- 门窗、墙面、地面、天棚、柜体、隔断、踢脚线、收口等构件

作用：

- 生成工程量计算证据。
- 将构件与做法表、材料表、设计说明关联。

## 8. 清单行生成逻辑

### 8.1 候选行生成

候选来源包括：

- 材料表直接项目
- 构造做法表项目
- 设计说明中的施工范围
- 平面图/立面图/大样图中的构件与标注
- 标准库根据关键词推导的候选项目

每个候选行必须包含：

| 字段 | 说明 |
| --- | --- |
| `candidate_id` | 内部候选 ID |
| `source_type` | `drawing_catalog/design_note/material_table/construction_method/plan/elevation/detail/manual` |
| `source_file` | DWG 文件 |
| `source_layout` | layout 或图纸页 |
| `source_bbox` | 证据区域坐标 |
| `raw_text` | 原始识别文本 |
| `standard_item_code` | 匹配到的标准库项目 |
| `feature_values_json` | 按标准项目特征字段填充的值 |
| `quantity_rule_id` | 采用的工程量计算规则 |
| `quantity_basis_json` | 长度、面积、数量、扣减、展开等计算依据 |
| `quantity` | 工程量，不能计算则为空 |
| `unit` | 标准单位 |
| `confidence` | 综合置信度 |
| `warnings` | 缺特征、缺工程量、标准不覆盖、疑似重复、需人工确认等 |

### 8.2 去重与合并

清单候选去重遵循：

- 同一标准项目、同一部位、同一做法、同一单位可合并。
- 同名但规格/做法不同不得合并。
- 表格来源与图形来源冲突时不自动覆盖，展示冲突并让人工选择。
- 识别来源不同但工程量计算规则一致时，可以生成合并建议，不自动合并。

### 8.3 输出四字段

确认后的最终导出：

| 项目名称 | 项目特征 | 单位 | 工程量 |
| --- | --- | --- | --- |
| 标准项目名称或人工确认名称 | 标准库项目特征字段拼接结果 | 标准单位 | 按标准规则计算或人工确认值 |

内部证据链不导出到业务简表，但保留在任务详情和后续报价证据链中。

## 9. 页面设计

新增“图纸识别”入口，建议放在需求单上传之前：

```text
报价工作台
  -> 输入文字
  -> 上传需求单
  -> 识别图纸生成需求单
```

页面结构：

- 顶部：上传 DWG 图纸包、识别任务状态、转换日志。
- 左侧：图纸预览，支持按文件、图号、layout、目录切换。
- 中间：证据高亮，点击清单行后定位来源区域。
- 右侧：工程量清单草稿，显示项目名称、项目特征、单位、工程量、置信度、警告。
- 侧边抽屉：标准库条目详情，展示项目特征字段、工程量计算规则和标准来源页码。
- 底部操作：保存草稿、导出 Excel、发起报价。

人工确认规则：

- 项目特征必填字段缺失时，不能直接确认通过。
- 工程量无计算依据时，必须人工补量或标记不报价。
- 标准库未覆盖的候选行，必须人工选择标准项目或排除。
- 最终导出前必须没有阻断级警告。

## 10. 后端建议

### 10.1 Feature Flag

建议新增：

- `FEATURE_DRAWING_RECOGNITION`
- `FEATURE_DRAWING_STANDARD_LIBRARY`

默认关闭，先在内网验证环境打开。

### 10.2 数据表建议

如进入实现阶段，新增表必须走 Alembic revision：

- `quantity_standards`
- `quantity_standard_items`
- `quantity_standard_feature_fields`
- `quantity_standard_rules`
- `drawing_recognition_jobs`
- `drawing_recognition_files`
- `drawing_sheets`
- `drawing_elements`
- `drawing_requirement_candidates`
- `drawing_requirement_confirmed_rows`
- `drawing_recognition_events`

如果首版只做离线工具，也可以先不建库，输出 JSON/XLSX 到 `outputs/` 做技术验证。但一旦接入页面和任务历史，必须建表。

### 10.3 API 建议

| API | 用途 |
| --- | --- |
| `POST /api/v1/drawing-recognition/jobs` | 上传 DWG 并创建识别任务 |
| `GET /api/v1/drawing-recognition/jobs/{job_id}` | 查询任务摘要 |
| `GET /api/v1/drawing-recognition/jobs/{job_id}/events` | 订阅转换和识别事件 |
| `GET /api/v1/drawing-recognition/jobs/{job_id}/sheets` | 查询图纸页/layout |
| `GET /api/v1/drawing-recognition/jobs/{job_id}/candidates` | 查询清单候选行 |
| `POST /api/v1/drawing-recognition/jobs/{job_id}/confirm` | 保存人工确认行 |
| `POST /api/v1/drawing-recognition/jobs/{job_id}/export` | 导出四字段 Excel |
| `POST /api/v1/drawing-recognition/jobs/{job_id}/quote` | 将确认行接入现有报价任务 |
| `GET /api/v1/quantity-standards/items` | 查询标准库条目 |
| `GET /api/v1/quantity-standards/items/{item_code}` | 查询项目特征和工程量规则 |

### 10.4 与现有报价链路对接

确认后的图纸清单行转换为现有标准需求行：

```text
project_name <- 项目名称
spec         <- 项目特征
unit         <- 单位
quantity     <- 工程量
remark       <- 图纸来源摘要
raw_text     <- 图纸识别原始证据摘要
```

然后复用：

- `/api/v1/quote/jobs`
- `quote_job_requirement_rows`
- `requirement_integrity`
- 成本库 active 前置参考
- AI 预审
- 人工改价/草稿
- `/confirm_push`
- 报价历史、成本证据和后审计

## 11. 分阶段路线

### BIZ-2x-0 路线确认与样例基线

状态：当前文档阶段。

目标：

- 明确 DWG 图纸识别不是新报价引擎，而是需求单生成前置模块。
- 明确项目特征和工程量都必须受 GB/T 标准库约束。
- 收集样例 DWG、样例清单和标准 PDF。

验收：

- 路线文档完成。
- ROADMAP 记录当前阶段。
- 不改数据库、不改报价逻辑、不启动功能开发。

### BIZ-2x-1 GB/T 50854-2024 标准库结构化

状态：已完成首版 Word 结构化 active 标准库文件（2026-06-10）。当前已建立标准库种子 JSON、只读加载服务、预览脚本、人工校对包脚本、Word 自动解析脚本和聚焦测试；经业务确认，“并人”按“并入”处理，“项日”按“项目”处理，且未拆分出项目特征字段的项目确认为标准表原本为空。已生成 `data/standards/gbtn50854_2024_word_active_20260610_024610.json`，`active=457`，`safe_for_final_generation=true`。该文件作为后续 BIZ-2x 识图匹配的标准库基线，当前尚未接入页面/API/报价链路。

目标：

- 将 GB/T 标准文件结构化为可检索、可校验、可版本化的标准库。
- 覆盖房屋建筑与装饰工程常见清单项目。
- 建立项目特征字段和工程量计算规则的结构化表达。

交付：

- 标准库 JSON/CSV/XLSX 初版。
- 标准库校对报告。
- 标准库导入脚本或只读预览脚本。
- 当前已新增 `data/standards/gbtn50854_2024_min_seed.json`、`app/services/quantity_standard_library.py`、`scripts/biz2x1_quantity_standard_preview.py`、`scripts/biz2x1_standard_review_pack.py`、`tests/test_quantity_standard_library_biz2x1.py` 和 `tests/test_quantity_standard_review_pack_biz2x1.py`。
- 当前已生成标准库人工校对包，输出 Markdown/CSV/JSON；CSV 可用 Excel 打开逐行校对标准页码、官方项目名称、官方项目特征字段、官方单位和官方工程量计算规则。
- 当前校对包已改为业务员友好的中文文件名、中文表头和中文状态值；每行最后一列为“核验结论（通过/有问题）”。
- 用户已提供 Word 版 GB/T 标准，当前已新增 Word 标准自动解析与预填脚本，可从 Word 表格自动提取项目编码、项目名称、项目特征、计量单位、工程量计算规则和工作内容，并生成业务员预填校对表。
- 当前已生成首版可导入 active 标准库文件 `data/standards/gbtn50854_2024_word_active_20260610_024610.json` 和摘要 `data/standards/gbtn50854_2024_word_active_20260610_024610.md`；共 457 个 active 标准项目、1679 个项目特征字段，保留项目编码、项目名称、项目特征字段、单位、工程量计算规则、工作内容和来源表格追溯。
- 已按业务确认固化 Word/OCR 修正规则：`并人 -> 并入`、`项日 -> 项目`；标准表原本项目特征为空的项目用 `no_feature_fields_in_standard=true` 标记，允许 active，但不伪造项目特征字段。

验收：

- 每个 active 标准项目都有单位和工程量计算规则；项目特征字段必须来自标准表，标准表原本为空的项目必须显式标记 `no_feature_fields_in_standard=true`。
- 能按项目名称/关键词检索标准项目。
- 项目特征字段和工程量规则可追溯到标准页码或校对来源。
- 当前校验策略：active 条目必须标记为 `verified_against_standard`，并具备已校对工程量规则和图纸证据需求；未校对草稿只能用于技术验证和后续 OCR/人工校对。

### BIZ-2x-2 DWG 转换与图纸预览

状态：已完成代码层转换能力探测、ODA File Converter 工作区可用安装和真实样例 DWG -> DXF 转换验证（2026-06-10）。当前新增 DWG 文件探测服务和脚本，可识别 DWG 版本头、文件大小、哈希和本机转换工具可用性；真实 4 个样例 DWG 均识别为 `AC1018` / AutoCAD 2004/2005/2006 DWG。由于系统级 MSI 安装授权未返回，本阶段采用 MSI 管理提取方式将 ODA File Converter 放入工作区 `tools/oda/extracted/ODAFileConverter.exe`，已验证可批量输出 DXF。

目标：

- 验证样例 DWG 可稳定转换为 PDF/PNG 预览。
- 提取 layout、图号、图名、标题栏和基础文本。

交付：

- DWG 转换技术选型结论。
- 转换脚本或服务封装。
- 样例图纸预览输出。
- 当前已新增 `app/services/dwg_preview_probe.py`、`scripts/biz2x2_dwg_preview_probe.py` 和 `tests/test_dwg_preview_probe_biz2x2.py`；脚本可生成 JSON/Markdown/CSV 探测报告。
- 当前已新增 `app/services/dwg_oda_converter.py`、`scripts/biz2x2_oda_dwg_to_dxf.py` 和 `tests/test_dwg_oda_converter_biz2x2.py`；脚本可调用 ODA File Converter 将 DWG 目录批量转换为 DXF，并生成 JSON/Markdown 转换报告。
- 当前真实样例探测报告已生成：`outputs/biz2x2/BIZ2x2_DWG转换预览探测报告_20260610_175429.json`、`.md`、`.csv`，状态为 `ready_for_dxf_conversion_with_oda_file_converter`。
- 当前真实 DWG -> DXF 输出目录：`outputs/biz2x2/oda_dxf_20260610_1800`，共 4 个 DXF；转换报告为 `outputs/biz2x2/BIZ2x2_ODA_DWG转DXF结果_20260610_175732.json` 和 `.md`。

验收：

- 4 个样例 DWG 至少可稳定转换为 DXF；当前已达成。PDF/PNG 可读预览仍需继续补充 DXF 渲染或 PDF 过渡方案。
- 转换失败时有明确错误码和用户提示。
- 识别任务可记录文件、layout 和转换事件。
- 当前已满足“转换失败明确提示”和“文件级探测记录”部分：样例文件数 4、可识别 DWG 文件数 4、ODA 转换器可用，DWG -> DXF 转换退出码 0，输出 DXF 数 4。

### BIZ-2x-3 图纸文本、表格与说明提取

状态：已完成 DXF 轻量文本/图层/layout 提取、图纸目录/材料表/构造做法表候选重建、图纸索引候选归档，以及表格字段级收敛（2026-06-10）。当前基于 ODA 转出的 DXF，已可流式读取 DXF group code，识别实际文本编码、图层、layout、实体类型统计、TEXT/MTEXT/ATTRIB/ATTDEF 文字、坐标和业务标签；并按相近 Y 坐标聚行、X 坐标排序成列，输出目录/材料/做法表格候选和图号/图名候选；再进一步将图纸目录拆成序号、图纸名称、图纸编号和图幅，将材料/做法候选整理为名称、规格或做法、备注和置信度。当前仍是识图中间结果，不做 AI 识图、不生成最终清单。

目标：

- 提取工程概况、目录、设计说明、材料表、构造做法表。
- 建立图纸元素统一结构。

交付：

- `drawing_elements` 数据结构。
- 表格识别和 OCR 结果预览。
- 样例图纸提取报告。
- 当前已新增 `app/services/dxf_text_extractor.py`、`scripts/biz2x3_dxf_text_extract.py` 和 `tests/test_dxf_text_extractor_biz2x3.py`；可输出 DXF 文本/图层提取 JSON、Markdown 和 CSV。
- 当前真实样例报告已生成：`outputs/biz2x3/BIZ2x3_DXF图纸文本图层提取_20260610_180650.json`、`.md`、`.csv`；CSV 包含文字、业务标签、实体类型、图层、layout、块名、坐标、字高、旋转角和源行号。
- 当前已新增 `app/services/dxf_table_reconstructor.py`、`scripts/biz2x3_dxf_table_reconstruct.py` 和 `tests/test_dxf_table_reconstructor_biz2x3.py`；可输出表格候选 JSON/Markdown、表格候选 CSV 和图纸索引 CSV。
- 当前真实样例表格重建报告已生成：`outputs/biz2x3/BIZ2x3_DXF表格重建与图纸索引_20260610_181949.json`、`.md`、`_表格候选.csv`、`_图纸索引.csv`。
- 当前已新增 `app/services/dxf_table_field_convergence.py`、`scripts/biz2x3_dxf_table_fields.py` 和 `tests/test_dxf_table_field_convergence_biz2x3.py`；可输出图纸目录字段 CSV、材料/做法字段 CSV、JSON 和 Markdown 报告。
- 当前真实样例字段收敛报告已生成：`outputs/biz2x3/BIZ2x3_DXF表格字段收敛_20260610_182855.json`、`.md`、`_图纸目录字段.csv`、`_材料做法字段.csv`；共收敛 24 条图纸目录字段行和 52 条材料/做法线索行。

验收：

- 能列出主要图纸目录和说明段落。
- 能识别材料表/构造做法表中的主要行。
- 每个识别元素有来源文件、图纸页/layout 和证据区域。
- 当前已满足“目录/说明/材料表/平立面/节点文字初筛”和“来源文件、图层、layout、坐标记录”部分；真实 4 个 DXF 共识别 4967 个文字实体，保存 4933 条非空文字记录，业务标签命中：项目身份 97、设计说明 79、平面 72、立面 36、材料表 33、节点/详图 23、构造做法 16、目录 11。
- 当前已完成首版表格候选重建和图纸索引候选归档：真实样例输出 24 个表格候选，其中图纸目录 11、材料表 10、构造做法/通用节点 3；输出 87 个图纸索引候选，其中平面图 28、立面图 20、设计说明 12、构造做法/通用节点 11、材料表 10、目录 5、大样/节点 1。
- 当前已完成首版表格字段级收敛：真实样例输出 24 条图纸目录字段行（含序号、图纸名称、图纸编号、图幅、类型和来源行）和 52 条材料/做法线索行（材料 22、构造做法 30），所有行保留来源文件、来源锚点、来源行号、原始行文本和置信度。
- 尚未完成图框范围精确识别、候选去重人工确认页和 OCR 补充；标准项目候选匹配已进入 BIZ-2x-4，工程量证据提取已进入 BIZ-2x-5。

### BIZ-2x-4 标准项目匹配与项目特征填充

目标：

- 将图纸元素匹配到 GB/T 标准项目。
- 按标准库字段口径填充项目特征。

状态：

- 已完成首版标准项目候选匹配和项目特征待填充预览（2026-06-10）。
- 当前只输出候选标准项目、标准库字段名、候选填充值、缺失字段和标准工程量规则状态，不生成最终清单。
- 工程量因缺少尺寸/面积/长度/数量等可追溯证据，统一标记为待人工确认。

交付：

- 标准项目匹配服务。
- 项目特征字段填充服务。
- 缺失特征和冲突特征警告。
- 当前已新增 `app/services/drawing_standard_matcher.py`、`scripts/biz2x4_standard_match_preview.py` 和 `tests/test_drawing_standard_matcher_biz2x4.py`。
- 当前真实样例匹配报告已生成：`outputs/biz2x4/BIZ2x4_GBT标准项目候选匹配_20260610_184107.json`、`.md`、`_标准项目候选.csv`、`_项目特征待填充.csv`；输出 45 条标准项目候选，覆盖 8 个唯一标准项目，工程量可直接生成数为 0。

验收：

- 项目特征字符串完全由标准库字段拼接。
- 缺失字段不猜测，明确标记待确认。
- 标准不覆盖或低置信度行不能直接确认通过。
- 当前已验证候选匹配只使用 active GB/T 标准库，项目特征字段名来自标准库 `feature_fields`，候选填充值来自图纸字段证据。
- 当前真实样例命中块料楼地面、楼(地)面涂膜防水、平面吊顶/天棚、艺术造型吊顶天棚、天棚喷刷涂料、窗台板、窗帘盒、金属踢脚线等 8 个标准项目；所有候选工程量状态均为 `missing_quantity_measurement_needs_manual_review`。

### BIZ-2x-5 工程量计算规则引擎

目标：

- 将标准库工程量规则转为可执行或半可执行计算模板。
- 从图纸中提取长度、面积、数量、展开面积、扣减等计算证据。

状态：

- 已完成首版工程量证据提取与标准规则判断预览（2026-06-10）。
- 当前只判断候选标准项目是否具备可追溯工程量证据，并区分直接面积/长度/数量/体积文本、局部尺寸/厚度/规格证据和缺失证据。
- 即使识别到直接工程量文本，当前仍标记为候选并要求人工复核；不允许免人工直接生成最终清单。

交付：

- 工程量规则解释器。
- 工程量候选和计算依据展示。
- 无法计算时的人工补量流程。
- 当前已新增 `app/services/drawing_quantity_evidence.py`、`scripts/biz2x5_quantity_evidence_preview.py` 和 `tests/test_drawing_quantity_evidence_biz2x5.py`。
- 当前真实样例工程量证据报告已生成：`outputs/biz2x5/BIZ2x5_工程量证据提取_20260610_192627.json`、`.md`、`_工程量候选判断.csv`、`_工程量证据明细.csv`；45 个标准候选中，直接工程量候选 0 个，局部证据 43 个，缺失工程量证据 2 个。

验收：

- 工程量必须带 `quantity_rule_id` 和 `calculation_trace`。
- 无规则或无证据时不自动填工程量。
- 多候选工程量必须人工选择。
- 当前已验证真实样例不会把“吊顶超过 1.5m/2.5m 需反支撑/转换层”等构造高度误判为窗帘盒工程量长度。
- 当前真实样例 `quantity_ready_without_manual_review_count=0`，最终清单生成状态仍为 `blocked_until_quantity_evidence_and_manual_review`。

### BIZ-2x-6 人工确认页面与四字段 Excel 导出

目标：

- 新增图纸识别确认页面。
- 支持清单行编辑、证据高亮、标准库规则查看、警告处理和导出。

状态：

- 已完成轻量人工确认/补量 Excel 包和最终四字段导出校验（2026-06-10）。
- 当前先以脚本和 Excel 工作簿闭环实现，不新增页面、不新增数据库；业务员在确认表中确认是否采用候选、填写核验结论、人工工程量、单位、工程量来源说明，并补全项目特征。
- 系统读取业务员填完的确认表后，只导出“是否采用=是、核验结论=通过、工程量>0、工程量来源不空、项目特征无待确认/缺失提示”的行。

交付：

- Vite 页面或现有工作台入口。
- 四字段 Excel 导出。
- 任务草稿和确认状态。
- 当前已新增 `app/services/drawing_quantity_confirmation.py`、`scripts/biz2x6_confirmation_pack.py` 和 `tests/test_drawing_quantity_confirmation_biz2x6.py`。
- 当前真实样例已生成业务确认补量包：`outputs/biz2x6/BIZ2x6_图纸识别人工确认补量包_20260610_193659.xlsx`、`.json`、`.md`、`_人工确认补量.csv`；包含 45 行人工确认候选、265 条项目特征明细、331 条工程量证据明细。

验收：

- 导出的 Excel 列固定为“项目名称、项目特征、单位、工程量”。
- 导出前无阻断级警告。
- 人工确认后的修改有记录。
- 当前已验证未填写确认表直接导出时会阻断，不生成最终四字段 Excel；45 行均为未采用/待确认状态。
- 当前已通过测试验证：采用行必须核验通过、工程量必须为大于 0 的数字、工程量来源说明不能为空、项目特征不能残留“待确认/缺失”等提示；通过校验后最终 Excel 仅包含“项目名称、项目特征、单位、工程量”四列。

### BIZ-2x-7 接入现有报价链路

目标：

- 将图纸确认清单接入现有 `/api/v1/quote/jobs`。
- 复用成本库、AI 预审、草稿保存、确认下发和审计链路。

交付：

- “发起报价”入口。
- 图纸来源摘要写入标准需求行。
- 报价运营详情可追溯图纸来源。

验收：

- 不新建报价引擎。
- 不改现有价格口径。
- 图纸清单进入报价后的完整性对账正常。

### BIZ-2x-8 样例回归与命中率评估

目标：

- 使用“信达公司职工食堂”样例图纸和人工清单建立回归包。
- 评估项目名称、项目特征、单位、工程量四字段准确度。

交付：

- 样例回归 JSON。
- 差异报告 XLSX/Markdown。
- 命中率、缺项率、误项率、人工补量率指标。

验收：

- 能对比系统生成清单与人工清单。
- 每个差异可定位原因：标准库缺失、图纸识别失败、工程量规则缺证据、人工口径差异。
- 不以一次样例通过作为正式生产承诺，继续纳入试运行观察。

## 12. 未完成功能专项路线

本节针对当前识图和报价链路仍未完成的 6 个功能点，拆成可执行路线。原则仍然不变：项目特征必须来自 active GB/T 标准库字段口径，工程量必须来自标准库 `quantity_rule` 和可追溯图纸/人工补量证据；未确认前不得进入最终清单和报价。

| 未完成功能 | 阶段编号 | 目标 | 具体任务 | 验收口径 | 优先级与依赖 |
| --- | --- | --- | --- | --- | --- |
| 1. 前端页面缺失 | BIZ-2x-6a 图纸识别确认页面 | 把当前 Excel 确认补量闭环迁移到管理台页面，让业务员不用直接操作脚本 | 新增 Vite 页面入口；展示候选行、标准项目、项目特征字段、工程量规则、证据摘要和阻断原因；支持采用/不采用、核验结论、人工工程量、单位、来源说明和项目特征编辑；支持筛选“待补量/待补特征/有问题/可导出”；保留 Excel 下载入口作为兜底 | 页面能打开真实 45 行样例候选；业务员可在线修改并触发校验；未通过行清楚显示原因；页面不改变价格口径、不自动报价 | P0；依赖 BIZ-2x-6 现有确认包结构 |
| 2. API 化缺失 | BIZ-2x-6b 图纸识别任务 API | 把脚本能力封装成后端接口，支持页面调用和后续接报价 | 新增上传/创建识图任务接口；封装 DWG 转 DXF、DXF 文本提取、表格收敛、标准匹配、工程量证据、确认包生成；新增确认表上传/读取、行级校验、最终四字段 Excel 导出接口；首版可用文件型任务目录保存 JSON/XLSX，暂不强制新增数据库 | 可通过 API 完成“上传样例图纸 -> 生成确认补量包 -> 上传/保存确认结果 -> 校验 -> 导出四字段 Excel”；失败返回中文阻断原因；接口权限沿用后台登录鉴权 | P0；依赖 BIZ-2x-6a 页面或先做 API 再接页面 |
| 3. 未接报价链路 | BIZ-2x-7 图纸确认清单接入报价 | 将通过校验的四字段清单接入现有 `/api/v1/quote/jobs`，复用 BIZ-2l 的确认清单逐行报价保障 | 将最终四字段 Excel/JSON 转为标准需求行；写入图纸来源摘要、候选编号、标准项目编码、工程量来源说明；调用现有异步报价任务；复用成本库前置、AI 预审、完整性对账、草稿、确认下发和报价运营详情；报价运营展示“来源：DWG 图纸识别确认清单” | 只有通过 BIZ-2x-6 校验的行可发起报价；报价任务中的标准需求行数量与确认行一致；预审完整性能对账；报价运营可追溯图纸来源和确认表 | P1；依赖 BIZ-2x-6b API 和业务确认表校验通过 |
| 4. 缺 PDF/PNG 图纸预览 | BIZ-2x-6c 图纸预览与证据定位 | 让业务员补量时能对照图纸预览和证据位置，减少只看文字表格的校验压力 | 评估 ODA/其他工具输出 PDF/PNG；若转换不稳定，首版允许用户同步上传 PDF；建立 DXF 文本坐标到预览图的定位映射；确认页面支持按候选查看来源文件、图层、坐标、证据文本和预览跳转；预览失败时仍保留 Excel/文字证据兜底 | 至少能在样例图纸中打开预览并定位材料/做法/尺寸证据；预览失败有明确提示，不影响人工确认表生成；不依赖预览结果自动算量 | P1；可与 BIZ-2x-6a 并行，依赖图纸转换方案 |
| 5. 自动算量能力缺失 | BIZ-2x-9 可追溯半自动算量增强 | 在不猜工程量的前提下，逐步把可计算项目从“人工补量”提升为“系统建议 + 人工确认” | 扩展 DXF 全量实体解析，识别闭合多段线、面积标注、房间/区域名称、门窗洞口和材料做法引用；建立单位/比例校验；按标准库 `quantity_rule` 生成 `calculation_trace`；先覆盖地面/天棚面积、窗帘盒/踢脚线长度、门窗数量等低风险类型；无法闭合、比例不明、扣减不明时继续待人工补量 | 每个自动建议工程量必须带来源图元、计算公式、扣减说明和置信度；样例中至少选 2-3 类低风险项目形成可复核建议；人工确认前仍不得进入最终清单 | P2；依赖 BIZ-2x-6c 证据定位和更多样例回归 |
| 6. 未与人工清单逐行回归 | BIZ-2x-8 样例回归与差异评估 | 用《信达公司职工食堂装修改造工程量清单.xls》建立识图结果质量基线 | 将 `.xls` 样例另存/转换为 `.xlsx` 后解析；读取人工清单四字段；与 BIZ-2x-6 确认后最终四字段清单按项目名称、标准项目编码、特征关键词、单位和工程量做逐行对照；输出命中、缺项、误项、工程量差异、特征差异和原因分类；沉淀回归 JSON/XLSX/Markdown | 能输出差异报告；每个差异至少归因到标准匹配、图纸识别、工程量证据、人工补量或口径差异；形成后续优化优先级 | P1；依赖业务员完成首版确认补量，或先用人工清单做静态对照基线 |

### 12.1 推荐实施顺序

1. **BIZ-2x-6b API 化**：先把脚本能力封装成可调用接口，为页面和报价接入打底。
2. **BIZ-2x-6a 页面化确认**：把当前 Excel 工作簿搬到管理台，让业务员在线确认和补量。
3. **BIZ-2x-8 样例回归**：用人工清单做第一轮质量基线，明确识别缺口。
4. **BIZ-2x-7 接入报价**：确认清单质量稳定后，再接入现有报价任务，避免脏数据进入报价。
5. **BIZ-2x-6c 图纸预览定位**：与页面化并行推进，优先做 PDF 过渡方案。
6. **BIZ-2x-9 半自动算量增强**：作为中期能力，不影响当前人工补量闭环上线试用。

### 12.2 分阶段验收门槛

| 阶段 | 最小可验收结果 | 不允许发生 |
| --- | --- | --- |
| BIZ-2x-6a/6b | 页面或 API 能复现当前 Excel 确认补量闭环，未确认行不能导出 | 绕过核验直接生成最终清单 |
| BIZ-2x-7 | 通过校验的确认行能创建报价任务，报价完整性可对账 | 改动现有报价价格口径或跳过预审 |
| BIZ-2x-6c | 业务员能看到图纸预览或上传 PDF 预览，并定位证据 | 因预览失败导致确认包无法生成 |
| BIZ-2x-8 | 形成样例清单差异报告和指标 | 用一次样例结果宣称生产可用 |
| BIZ-2x-9 | 自动建议工程量有完整计算追溯，且必须人工确认 | 无证据猜面积、猜长度、猜数量 |

### 12.3 BIZ-2x-9 CAD 几何算量细化路线

CAD 几何算量可以实现，但不能直接等同于最终工程量。路线拆成“图元证据 -> 比例单位 -> 区域/材料关联 -> 标准规则计算 -> 人工确认”的可追溯闭环。任何阶段只要缺比例、缺边界、缺材料关联、缺扣减依据或不符合标准库 `quantity_rule`，都只能回到人工补量。

| 子阶段 | 目标 | 具体任务 | 输出 | 验收口径 |
| --- | --- | --- | --- | --- |
| BIZ-2x-9a CAD 几何图元探测 | 先确认 DXF 里有哪些可用几何证据 | 解析 `LINE`、`LWPOLYLINE`、`POLYLINE`、`HATCH`、`INSERT`、`DIMENSION`、`CIRCLE` 等实体；统计图层、块名、闭合多段线、线段长度、块数量和尺寸标注；识别可能的面积/长度/数量候选 | JSON/Markdown/CSV 图元探测报告 | 只输出候选和风险，不生成工程量；报告必须标记 `safe_for_auto_quantity=false` |
| BIZ-2x-9b 图框、比例和单位校验 | 判断图元数值是否可换算为工程量单位 | 识别图框、比例文字、标注比例、单位说明；校验坐标单位与标注尺寸是否一致；建立比例置信度 | 比例/单位校验报告 | 未确认比例或单位时，禁止自动建议工程量 |
| BIZ-2x-9c0 低风险图层/块名映射 | 在面积/长度/数量建议量前筛出可信 CAD 来源 | 按候选类型、图层、块名分组；排除 `0` 层、图框、标注、轴号、索引、辅助线、匿名块和生成块；识别地面/顶面面积、踢脚/线脚长度、门窗/灯具/洁具/插座/开关数量等低风险分组 | 图层/块名映射 JSON/Markdown/CSV | 只有 `allowed_low_risk_mapping` 分组可进入 9c/9d/9e 建议量探测；待人工映射和排除分组不得计算 |
| BIZ-2x-9c 区域边界与面积候选 | 对地面、天棚、防水等面积类项目形成可复核面积候选 | 识别闭合边界、房间/区域文字、填充边界；合并碎片边界；标记洞口、柱、墙体等扣减风险 | 面积候选和边界追溯 | 每个面积建议必须有边界图元、面积公式、比例、扣减说明和来源图层；当前首版只输出几何建议面积，尚未套用标准扣减规则 |
| BIZ-2x-9d 线性工程量候选 | 对踢脚线、窗帘盒、线条、压条等长度类项目形成长度候选 | 识别目标图层线段/多段线；关联空间区域和做法；排除轴线、标注线、辅助线；记录拼接长度 | 长度候选和线段追溯 | 每个长度建议必须列出线段来源、求和公式、排除规则和风险；当前首版只输出线段求和建议，尚未关联空间区域和标准项目 |
| BIZ-2x-9e 块引用与数量候选 | 对门窗、灯具、洁具、设备等数量类项目形成数量候选 | 统计 `INSERT` 块引用；建立块名/图层/图例映射；剔除图框、索引、详图符号等非工程实体 | 数量候选和块映射表 | 块名未映射或图例不明确时只能作为待确认数量候选；当前首版只统计低风险块引用，仍需人工复核 |
| BIZ-2x-9f 材料/做法/项目关联 | 把几何候选关联到标准项目和项目特征字段 | 将区域名称、材料表、构造做法表、图层名、图例和标准项目候选关联；只填标准库已有特征字段 | 几何候选到标准候选的关联表 | 项目特征字段名必须来自 active 标准库；缺字段不猜测 |
| BIZ-2x-9g 标准规则计算 trace | 按 GB/T 标准库 `quantity_rule` 生成建议工程量 | 将面积/长度/数量候选套入标准库规则；生成 `quantity_rule_id`、公式、输入值、扣减说明、证据链和置信度 | `calculation_trace` 和建议工程量 | 无 `quantity_rule`、无证据链或扣减不明时不得给出最终建议量 |
| BIZ-2x-9h 人工确认与样例回归 | 把建议量纳入业务确认，而不是绕过人工 | 在 BIZ-2x-6 确认表/页面中增加“系统建议量、计算依据、采纳/修改原因”；与人工清单做差异回归 | 确认后的四字段清单和差异报告 | 人工确认前不得进入最终清单和报价；人工修改必须保留原因 |

当前执行进度：BIZ-2x-9a、9b、9b-1、9c0、9c/9d/9e、9f/9g、9h 和 9h-2 首版已完成。9c/9d/9e 只回答“低风险 CAD 来源能否形成可复核面积、长度、数量建议”，9f/9g 进一步回答“这些建议量能否引用 active GB/T 标准项目和标准库 `quantity_rule` 形成可复核 trace”，9h 将 trace 放入人工复核工作簿，9h-2 再由系统自动初判采用/不采用/需人工确认。以上步骤都不直接回答“最终工程量是多少”。后续需要业务员确认系统初判、补全项目特征值、复核扣减规则后，才能进入最终四字段清单。

## 13. 风险与控制

| 风险 | 控制方式 |
| --- | --- |
| DWG 转换不稳定 | 独立转换服务；支持 PDF/DXF 过渡；失败明确提示 |
| 标准 PDF 无文本层 | 已改用 Word 表格解析形成首版 active 标准库；后续版本更新仍需记录来源、修正规则和校验结果 |
| AI 乱写项目特征 | 项目特征只能由标准库字段生成，AI 只提供候选证据 |
| AI 猜工程量 | 工程量必须引用标准计算规则和图纸证据，缺证据则待人工确认 |
| 图纸比例/单位不清 | 提示人工确认，不自动计算 |
| 同一项目重复识别 | 候选合并建议，不自动合并不同做法/规格 |
| 机电图纸超出本标准 | 标记“非本标准覆盖”，不强行套用建筑装饰标准 |
| 直接进入报价导致风险 | 必须人工确认后才可导出或发起报价 |
| 页面/API 化过早接入报价 | 先通过 BIZ-2x-6a/6b 校验闭环，再启动 BIZ-2x-7 报价接入 |
| 自动算量误差被业务误用 | BIZ-2x-9 只输出带 `calculation_trace` 的建议量，人工确认前不得进入最终清单 |

## 14. 当前进度记录

| 日期 | 阶段 | 状态 | 记录 |
| --- | --- | --- | --- |
| 2026-06-10 | BIZ-2x-0 | 已创建 | 明确 DWG 图纸识别模块定位、标准库约束、四字段输出、分阶段路线和验收口径 |
| 2026-06-10 | 样例基线 | 已收集 | 已确认用户提供 GB/T 标准 PDF、1 份 `.xls` 样例清单和 4 个 DWG 样例图纸 |
| 2026-06-10 | 技术探测 | 已完成轻量探测 | 标准 PDF 108 页且无可直接抽取文本层；样例 DWG 为 `AC1018` 格式；后续需要 CAD 转换服务和标准库 OCR/人工结构化 |
| 2026-06-10 | BIZ-2x-1 | 已启动代码层骨架 | 新增 GB/T 标准库最小可用种子、只读加载服务、预览脚本和聚焦测试；当前 12 个条目均为 draft，`active=0`，`safe_for_final_generation=false` |
| 2026-06-10 | BIZ-2x-1 验证 | 已通过聚焦测试 | `python -m pytest tests/test_quantity_standard_library_biz2x1.py` 结果为 `5 passed, 1 warning`；预览脚本可输出 Markdown/CSV/JSON 并按关键词搜索候选 |
| 2026-06-10 | BIZ-2x-1 校对包 | 已完成代码层骨架 | 新增标准库人工校对包生成能力，将 12 个候选标准项目按项目特征字段拆成 61 行待校对项；预留标准页码、官方项目编码、官方项目特征字段、官方单位、官方工程量规则和原文摘录等校对列 |
| 2026-06-10 | BIZ-2x-1 校对包验证 | 已通过聚焦测试 | `python -m pytest tests/test_quantity_standard_library_biz2x1.py tests/test_quantity_standard_review_pack_biz2x1.py` 结果为 `9 passed, 1 warning`；已生成 `outputs/biz2x1/biz2x1_standard_review_pack_20260610_004026.md`、`.csv`、`.json` |
| 2026-06-10 | BIZ-2x-1 中文校对表 | 已完成 | 校对表文件名、字段名和主要状态值已改为中文业务口径，并在每行末尾新增“核验结论（通过/有问题）”；已生成 `outputs/biz2x1/BIZ2x1_GBT50854标准库人工校对表_20260610_004952.csv`、`.md`、`.json` |
| 2026-06-10 | BIZ-2x-1 中文校对表验证 | 已通过聚焦测试 | `python -m pytest tests/test_quantity_standard_library_biz2x1.py tests/test_quantity_standard_review_pack_biz2x1.py` 结果为 `10 passed, 1 warning`；已抽查 CSV 表头最后一列为“核验结论（通过/有问题）” |
| 2026-06-10 | BIZ-2x-1 Word 自动预填 | 已完成代码层骨架 | 新增 `app/services/quantity_standard_docx_parser.py`、`scripts/biz2x1_gbt_docx_prefill.py` 和 `tests/test_quantity_standard_docx_parser_biz2x1.py`；解析 Word 内部 XML，不依赖 `python-docx`；支持标准表头识别、纵向合并单元格继承、项目特征编号拆分、中文空格清洗和“并人/并入”“项日/项目”风险提示 |
| 2026-06-10 | BIZ-2x-1 Word 自动预填验证 | 已通过聚焦测试和真实 Word 解析 | `python -m pytest tests/test_quantity_standard_library_biz2x1.py tests/test_quantity_standard_review_pack_biz2x1.py tests/test_quantity_standard_docx_parser_biz2x1.py` 结果为 `13 passed, 1 warning`；真实 Word 识别 129 个表格、121 个标准清单表格、457 个标准项目、1679 个项目特征字段、81 个风险项目；已生成 `outputs/biz2x1/BIZ2x1_GBT50854标准库Word自动预填表_20260610_022633.csv`、`.md`、`.json` |
| 2026-06-10 | BIZ-2x-1 已确认 OCR 修正规则 | 已完成 | 经业务确认，Word 自动预填表中“并人”均应为“并入”；解析器已加入确认修正规则 `并人 -> 并入`，输出增加“自动修正说明”列，并不再把该项计入风险提示 |
| 2026-06-10 | BIZ-2x-1 Word 自动预填复验 | 已通过聚焦测试和真实 Word 解析 | `python -m pytest tests/test_quantity_standard_library_biz2x1.py tests/test_quantity_standard_review_pack_biz2x1.py tests/test_quantity_standard_docx_parser_biz2x1.py` 结果为 `13 passed, 1 warning`；真实 Word 仍识别 457 个标准项目、1679 个项目特征字段，风险项目由 81 个降至 40 个，49 个“并人”已自动修正为“并入”；已生成 `outputs/biz2x1/BIZ2x1_GBT50854标准库Word自动预填表_20260610_023312.csv`、`.md`、`.json` |
| 2026-06-10 | BIZ-2x-1 OCR 修正规则补全 | 已业务确认 | 用户确认“项日”一律按“项目”处理；未拆分出项目特征字段的项目已验证为标准表原本为空，允许导入标准库但必须显式保留空特征标记 |
| 2026-06-10 | BIZ-2x-1 active 标准库导入文件 | 已生成并校验通过 | 执行 Word 标准解析导入生成 `AI_Middle_Office/data/standards/gbtn50854_2024_word_active_20260610_024610.json` 和 `.md`；真实 Word 识别 129 个表格、121 个标准清单表格、457 个 active 标准项目、1679 个项目特征字段，风险项目 0 个，自动修正 53 处（`并人 -> 并入` 49 处，`项日 -> 项目` 4 处），`safe_for_final_generation=true` |
| 2026-06-10 | BIZ-2x-1 active 标准库验证 | 已通过聚焦测试和预览抽查 | `python -m pytest tests/test_quantity_standard_library_biz2x1.py tests/test_quantity_standard_review_pack_biz2x1.py tests/test_quantity_standard_docx_parser_biz2x1.py` 结果为 `15 passed, 1 warning`；用 `块料楼地面` 抽查可检索到 `011102003`，项目特征字段和工程量计算规则均来自标准库 |
| 2026-06-10 | BIZ-2x-2 DWG 转换探测骨架 | 已完成代码层骨架 | 新增 `app/services/dwg_preview_probe.py`、`scripts/biz2x2_dwg_preview_probe.py` 和 `tests/test_dwg_preview_probe_biz2x2.py`；支持 DWG 版本头识别、样例文件摘要、转换工具探测、阻断策略输出和 JSON/Markdown/CSV 报告生成 |
| 2026-06-10 | BIZ-2x-2 真实样例探测 | 已完成探测，预览转换被环境阻断 | 4 个样例 DWG 均可识别为 `AC1018` / AutoCAD 2004/2005/2006 DWG，文件本身无格式风险；当前机器未发现 ODA File Converter、AutoCAD Core Console、LibreDWG 等转换器，`conversion_status=blocked_missing_dwg_converter`，已生成 `outputs/biz2x2/BIZ2x2_DWG转换预览探测报告_20260610_173856.md`、`.json`、`.csv` |
| 2026-06-10 | BIZ-2x-2 聚焦测试 | 已通过 | `python -m pytest tests/test_dwg_preview_probe_biz2x2.py` 结果为 `5 passed, 1 warning`；覆盖 `AC1018` 识别、无转换器阻断、模拟 ODA 转 DXF 策略、目录去重和报告输出 |
| 2026-06-10 | BIZ-2x-2 ODA 准备 | 已完成工作区可用安装 | 从 ODA 官方下载 `ODAFileConverter_QT6_vc16_amd64dll_27.1.msi`，SHA256 为 `3D5961F510CF95F398B8E2920899DC8E8C51ADECDAF5B20A40B3D1A29269DE81`；系统级 MSI 安装授权超时后，采用 MSI 管理提取到 `tools/oda/extracted/ODAFileConverter.exe`，探测脚本已识别 `converter_available_count=1` |
| 2026-06-10 | BIZ-2x-2 ODA DWG 转 DXF | 已完成真实样例转换 | 新增 `app/services/dwg_oda_converter.py`、`scripts/biz2x2_oda_dwg_to_dxf.py` 和 `tests/test_dwg_oda_converter_biz2x2.py`；真实 4 个 DWG 已通过 ODA 转为 4 个 DXF，输出目录 `outputs/biz2x2/oda_dxf_20260610_1800`，转换报告 `outputs/biz2x2/BIZ2x2_ODA_DWG转DXF结果_20260610_175732.md`、`.json`，状态 `converted`、退出码 0 |
| 2026-06-10 | BIZ-2x-2 ODA 转换测试 | 已通过 | `python -m pytest tests/test_dwg_preview_probe_biz2x2.py tests/test_dwg_oda_converter_biz2x2.py` 结果为 `8 passed, 1 warning`；覆盖 ODA 命令参数顺序、模拟转换成功和无输入报错 |
| 2026-06-10 | BIZ-2x-3 DXF 文本图层提取 | 已完成代码层骨架和真实样例报告 | 新增 `app/services/dxf_text_extractor.py`、`scripts/biz2x3_dxf_text_extract.py` 和 `tests/test_dxf_text_extractor_biz2x3.py`；支持 UTF-8/GBK 编码探测、图层和 layout 提取、TEXT/MTEXT/ATTRIB/ATTDEF 文字提取、坐标记录、实体类型统计和业务标签初筛 |
| 2026-06-10 | BIZ-2x-3 真实 DXF 提取报告 | 已生成 | 基于 `outputs/biz2x2/oda_dxf_20260610_1800` 的 4 个 DXF 生成 `outputs/biz2x3/BIZ2x3_DXF图纸文本图层提取_20260610_180650.md`、`.json`、`.csv`；共识别 4967 个文字实体，保存 4933 条非空文字记录，未达到保存上限，已初筛目录、设计说明、材料表、构造做法、平面、立面和节点等业务标签 |
| 2026-06-10 | BIZ-2x-3 聚焦测试 | 已通过 | `python -m pytest tests/test_dxf_text_extractor_biz2x3.py` 结果为 `5 passed, 1 warning`；覆盖编码探测、图层/layout、TEXT/MTEXT 清洗、业务标签和报告输出 |
| 2026-06-10 | BIZ-2x-3 表格重建与图纸索引 | 已完成候选重建骨架和真实样例报告 | 新增 `app/services/dxf_table_reconstructor.py`、`scripts/biz2x3_dxf_table_reconstruct.py` 和 `tests/test_dxf_table_reconstructor_biz2x3.py`；支持按坐标聚行、按 X 排列列、识别图纸目录/材料表/构造做法表候选，并归档图号、图名、图纸类型和来源坐标 |
| 2026-06-10 | BIZ-2x-3 真实表格重建报告 | 已生成 | 基于 4 个 DXF 生成 `outputs/biz2x3/BIZ2x3_DXF表格重建与图纸索引_20260610_181949.md`、`.json`、`_表格候选.csv`、`_图纸索引.csv`；共输出 24 个表格候选（目录 11、材料表 10、构造做法/通用节点 3）和 87 个图纸索引候选 |
| 2026-06-10 | BIZ-2x-3 表格重建测试 | 已通过 | `python -m pytest tests/test_dxf_table_reconstructor_biz2x3.py` 结果为 `3 passed, 1 warning`；BIZ-2x 全部聚焦测试 `31 passed, 1 warning` |
| 2026-06-10 | BIZ-2x-3 表格字段收敛 | 已完成字段级收敛骨架和真实样例报告 | 新增 `app/services/dxf_table_field_convergence.py`、`scripts/biz2x3_dxf_table_fields.py` 和 `tests/test_dxf_table_field_convergence_biz2x3.py`；支持将图纸目录拆成序号、图纸名称、图纸编号、图幅和图纸类型，将材料/做法候选拆成名称、规格或做法、备注、置信度和来源证据 |
| 2026-06-10 | BIZ-2x-3 真实字段收敛报告 | 已生成 | 基于 4 个 DXF 生成 `outputs/biz2x3/BIZ2x3_DXF表格字段收敛_20260610_182855.md`、`.json`、`_图纸目录字段.csv`、`_材料做法字段.csv`；共输出 24 条图纸目录字段行和 52 条材料/做法线索行（材料 22、构造做法 30） |
| 2026-06-10 | BIZ-2x-3 字段收敛测试 | 已通过 | 新增字段收敛测试 `3 passed, 1 warning`；BIZ-2x 全部聚焦测试 `34 passed, 1 warning` |
| 2026-06-10 | BIZ-2x-4 标准项目候选匹配 | 已完成候选匹配骨架和真实样例报告 | 新增 `app/services/drawing_standard_matcher.py`、`scripts/biz2x4_standard_match_preview.py` 和 `tests/test_drawing_standard_matcher_biz2x4.py`；仅使用 active GB/T 标准库，输出标准项目候选、项目特征待填充字段、缺失字段、工程量规则和证据链，不生成最终清单 |
| 2026-06-10 | BIZ-2x-4 真实标准匹配报告 | 已生成 | 基于 `BIZ2x3_DXF表格字段收敛_20260610_182855.json` 和 `gbtn50854_2024_word_active_20260610_024610.json` 生成 `outputs/biz2x4/BIZ2x4_GBT标准项目候选匹配_20260610_184107.md`、`.json`、`_标准项目候选.csv`、`_项目特征待填充.csv`；39 条图纸线索中 35 条有候选，输出 45 条标准候选、8 个唯一标准项目，工程量可直接生成数为 0 |
| 2026-06-10 | BIZ-2x-4 标准匹配测试 | 已通过 | 新增标准匹配测试 `3 passed, 1 warning`；BIZ-2x 全部聚焦测试 `37 passed, 1 warning` |
| 2026-06-10 | BIZ-2x-5 工程量证据提取 | 已完成首版证据提取骨架和真实样例报告 | 新增 `app/services/drawing_quantity_evidence.py`、`scripts/biz2x5_quantity_evidence_preview.py` 和 `tests/test_drawing_quantity_evidence_biz2x5.py`；按标准库 `quantity_rule` 的规则类型识别面积、长度、数量、体积和局部尺寸/厚度/规格证据，不生成最终清单 |
| 2026-06-10 | BIZ-2x-5 真实工程量证据报告 | 已生成 | 基于 BIZ-2x-4 标准候选和 ODA 转出的 4 个 DXF 全量文字记录生成 `outputs/biz2x5/BIZ2x5_工程量证据提取_20260610_192627.md`、`.json`、`_工程量候选判断.csv`、`_工程量证据明细.csv`；45 个标准候选、4933 条 DXF 文字记录，直接工程量候选 0、局部证据 43、缺失工程量证据 2、免人工可生成 0 |
| 2026-06-10 | BIZ-2x-5 工程量证据测试 | 已通过 | 新增工程量证据测试 `2 passed, 1 warning`；BIZ-2x 全部聚焦测试 `39 passed, 1 warning`；已验证直接面积候选、局部尺寸证据、直接长度候选和真实样例构造高度防误判 |
| 2026-06-10 | BIZ-2x-6 人工确认补量包 | 已完成轻量 Excel 闭环 | 新增 `app/services/drawing_quantity_confirmation.py`、`scripts/biz2x6_confirmation_pack.py` 和 `tests/test_drawing_quantity_confirmation_biz2x6.py`；支持生成业务员确认补量工作簿、读取填写后的确认表、阻断校验和最终四字段 Excel 导出 |
| 2026-06-10 | BIZ-2x-6 真实确认补量包 | 已生成 | 基于 BIZ-2x-4 标准候选和 BIZ-2x-5 工程量证据生成 `outputs/biz2x6/BIZ2x6_图纸识别人工确认补量包_20260610_193659.xlsx`、`.json`、`.md`、`_人工确认补量.csv`；工作簿包含“人工确认补量”“项目特征明细”“工程量证据明细”“填写说明”4 个 Sheet，主表 45 行候选 |
| 2026-06-10 | BIZ-2x-6 导出阻断验证 | 已通过 | 未填写确认表直接执行 `export-final` 时，45 行均跳过，`adopted_final_row_count=0`，未生成最终四字段 Excel；确认表填完后才允许导出 |
| 2026-06-10 | BIZ-2x-6 聚焦测试 | 已通过 | 新增确认补量测试 `3 passed, 1 warning`；BIZ-2x 全部聚焦测试 `42 passed, 1 warning` |
| 2026-06-10 | BIZ-2x 未完成功能专项路线 | 已补充 | 已将“前端页面、API 化、报价接入、PDF/PNG 预览、半自动算量、人工清单回归”6 个未完成项拆成 BIZ-2x-6a/6b/6c/7/8/9 路线，明确目标、任务、验收口径、优先级和实施顺序 |
| 2026-06-10 | BIZ-2x-9 CAD 几何算量细化路线 | 已补充 | 已将“可追溯半自动算量”拆成 BIZ-2x-9a 至 BIZ-2x-9h：几何图元探测、图框/比例/单位校验、区域边界面积、线性长度、块引用数量、材料做法关联、标准规则计算 trace、人工确认与样例回归；明确缺比例、缺边界、缺材料关联、缺扣减依据或不符合标准库 `quantity_rule` 时只能人工补量 |
| 2026-06-10 | BIZ-2x-9a CAD 几何图元探测 | 已完成首版骨架和真实样例报告 | 新增 `app/services/dxf_geometry_probe.py`、`scripts/biz2x9a_dxf_geometry_probe.py` 和 `tests/test_dxf_geometry_probe_biz2x9a.py`；基于 4 个 DXF 生成 `outputs/biz2x9a/BIZ2x9a_CAD几何图元探测_20260610_202318.md`、`.json`、`_几何候选.csv`；探测几何实体 63898 个，候选抽样内面积 1200、长度 1200、数量 1200、标注 440，报告明确 `safe_for_auto_quantity=false`，候选数是抽样上限内证据池，不是最终工程量 |
| 2026-06-10 | BIZ-2x-9a 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests\test_dxf_geometry_probe_biz2x9a.py` 结果为 `2 passed, 1 warning`；已覆盖闭合多段线面积/周长、线段长度、块引用数量、尺寸标注和报告输出；BIZ-2x 全部聚焦测试 `44 passed, 315 deselected, 1 warning` |
| 2026-06-10 | BIZ-2x-9b 图框、比例、单位校验 | 已完成首版骨架和真实样例报告，当前阻断进入几何建议量 | 新增 `app/services/dxf_scale_unit_probe.py`、`scripts/biz2x9b_scale_unit_probe.py` 和 `tests/test_dxf_scale_unit_probe_biz2x9b.py`；基于 BIZ-2x-3 文本 CSV/JSON 和 BIZ-2x-9a 几何报告生成 `outputs/biz2x9b/BIZ2x9b_图框比例单位校验_20260610_204046.md`、`.json`、`_证据清单.csv`；4933 条文本记录中识别到 64 个“比例”标签，但未识别到具体比例值；单位仅有材料/尺寸文字弱线索 80 条，未识别到全图单位说明；图框图层已识别，尺寸标注实体 440 个；`ready_for_geometry_quantity_probe=false`、`safe_for_auto_quantity=false` |
| 2026-06-10 | BIZ-2x-9b 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests\test_dxf_scale_unit_probe_biz2x9b.py` 结果为 `3 passed, 1 warning`；已覆盖比例/单位/图框/尺寸标注通过场景，以及 `1:2水泥砂浆` 不误判为图纸比例；BIZ-2x 全部聚焦测试 `47 passed, 315 deselected, 1 warning` |
| 2026-06-10 | BIZ-2x-9b-1 比例/单位人工确认配置 | 已按业务口径生成 | 经业务确认：本批图纸绘图单位一般按 `mm`，模型空间按真实尺寸 `1:1` 绘制，标题栏比例每张图可不同但只作为出图/打印比例，不参与模型空间算量乘除，基本都可进入几何建议量探测；新增 `scripts/biz2x9b_manual_confirmation.py`，生成 `outputs/biz2x9b/BIZ2x9b_比例单位人工确认配置_20260610_205902.md`、`.json`、`_图纸确认.csv`；4 个 DXF 均确认允许进入几何建议量探测，`ready_for_geometry_quantity_probe=true`，但 `safe_for_auto_quantity=false`，后续仍需图层/块名映射、扣减规则、标准库 `quantity_rule` 和人工确认 |
| 2026-06-10 | BIZ-2x-9b-1 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests\test_dxf_scale_unit_probe_biz2x9b.py` 更新为 `4 passed, 1 warning`；新增覆盖人工确认配置可解除比例/单位阻断，但仍不允许直接生成最终工程量；BIZ-2x 全部聚焦测试 `48 passed, 315 deselected, 1 warning` |
| 2026-06-10 | BIZ-2x-9c0 低风险图层/块名映射 | 已完成首版骨架和真实样例报告 | 新增 `app/services/dxf_layer_block_mapper.py`、`scripts/biz2x9c0_layer_block_mapping.py` 和 `tests/test_dxf_layer_block_mapper_biz2x9c0.py`；基于 9a 几何候选和 9b-1 确认配置生成 `outputs/biz2x9c0/BIZ2x9c0_低风险图层块名映射_20260610_223506.md`、`.json`、`_映射清单.csv`；256 个图层/块名分组中，54 个允许进入建议量探测、104 个需人工映射、98 个排除；允许分组包括计数 44、面积 8、长度 2；已收紧规则，灯具图层不作为面积低风险来源，窗帘块不误归为窗数量 |
| 2026-06-10 | BIZ-2x-9c0 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests\test_dxf_layer_block_mapper_biz2x9c0.py` 结果为 `3 passed, 1 warning`；覆盖低风险面积/长度/数量映射、比例单位未确认时阻断、标注/0 层/灯具面积/窗帘计数排除和报告输出；BIZ-2x 全部聚焦测试 `51 passed, 315 deselected, 1 warning` |
| 2026-06-10 | BIZ-2x-9c/9d/9e 低风险几何建议量 | 已完成首版骨架和真实样例报告 | 新增 `app/services/dxf_quantity_suggester.py`、`scripts/biz2x9cde_quantity_suggestions.py` 和 `tests/test_dxf_quantity_suggester_biz2x9cde.py`；基于 9a 几何报告和 9c0 低风险映射生成 `outputs/biz2x9cde/BIZ2x9cde_低风险几何建议量_20260610_224353.md`、`.json`、`_建议量清单.csv`；54 条建议量中 52 条可进入人工复核、2 条因无可用几何数值阻断；类型为计数 44、面积 8、长度 2；单位按 9b-1 确认的 `mm` 换算，模型空间 `1:1`，标题栏比例不参与工程量换算 |
| 2026-06-10 | BIZ-2x-9c/9d/9e 边界确认 | 已记录 | 本阶段 `standard_quantity_rule_applied=false`、`safe_for_auto_quantity=false`；所有建议量均标记 `is_final_quantity=false`、`requires_manual_review=true`，只可作为后续 9f/9g 标准项目绑定和标准规则计算 trace 的输入，不能直接写入最终四字段 Excel 或发起报价 |
| 2026-06-10 | BIZ-2x-9c/9d/9e 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `54 passed, 315 deselected, 1 warning`；新增建议量服务测试覆盖低风险建议量生成、比例/单位未确认阻断、空几何值阻断和非最终工程量边界 |
| 2026-06-10 | BIZ-2x-9f/9g 标准项目绑定与标准规则 trace | 已完成首版骨架和真实样例报告 | 新增 `app/services/dxf_standard_rule_binder.py`、`scripts/biz2x9fg_standard_rule_binding.py` 和 `tests/test_dxf_standard_rule_binder_biz2x9fg.py`；基于 9cde 几何建议量、BIZ-2x-4 标准候选和 active GB/T 标准库生成 `outputs/biz2x9fg/BIZ2x9fg_标准规则绑定trace_20260610_225642.md`、`.json`、`_绑定清单.csv`、`_标准规则trace.csv`；54 条建议量中 12 条找到标准候选、42 条未绑定；生成 41 行标准规则 trace，其中 20 行规则类型兼容可进入人工复核，0 行可直接进入最终清单 |
| 2026-06-10 | BIZ-2x-9f/9g 边界确认 | 已记录 | 本阶段只引用 active 标准库 `quantity_rule` 生成可复核 trace；插座、开关、洁具、普通灯具、家具设备等不强行套用 GB/T 50854 建筑装饰标准；门类块引用仅有数量时不能替代标准规则要求的洞口面积；天棚、线条、地面等多个标准候选需人工选择做法和项目特征值后才能进入最终清单 |
| 2026-06-10 | BIZ-2x-9f/9g 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `57 passed, 315 deselected, 1 warning`；新增测试覆盖标准规则 trace 生成、超出标准范围阻断、门类数量与面积规则不兼容阻断和输出文件生成 |
| 2026-06-10 | BIZ-2x-9h 标准规则 trace 人工复核包 | 已完成首版骨架和真实样例工作簿 | 新增 `app/services/dxf_trace_review_pack.py`、`scripts/biz2x9h_trace_review_pack.py` 和 `tests/test_dxf_trace_review_pack_biz2x9h.py`；基于 9f/9g 绑定报告生成 `outputs/biz2x9h/BIZ2x9h_标准规则trace人工复核包_20260610_230625.xlsx`、`.md`、`.json`、`_trace复核.csv`；工作簿包含“标准规则trace复核”“阻断项明细”“计算追溯明细”“填写说明”4 个 Sheet；41 行 trace 中 20 行可进入人工复核、21 行 trace 阻断、46 条建议量无可复核 trace |
| 2026-06-10 | BIZ-2x-9h 边界确认 | 已记录 | 可复核 trace 行默认“是否采用=待确认”，阻断 trace 默认“不采用”；主表要求业务员填写采用/核验结论/确认工程量/确认单位/项目特征/扣减合并规则复核/工程量来源说明；本工作簿仍不导出最终四字段清单，也不接报价 |
| 2026-06-10 | BIZ-2x-9h 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `59 passed, 315 deselected, 1 warning`；新增测试覆盖 trace 复核包生成、默认填写状态、阻断项口径、工作簿 Sheet 和输出文件生成 |
| 2026-06-10 | BIZ-2x-9h-2 系统自动初判复核包 | 已完成首版规则和真实样例工作簿 | 在 `app/services/dxf_trace_review_pack.py` 中新增系统初判列和规则：`系统初判结论`、`系统建议动作`、`风险等级`、`自动初判原因`；同一 CAD 建议量多个标准候选时只预选一个，其余自动建议不采用；节点/大样、小面积、展开面积不足、门数量套面积规则等自动阻断或需人工确认；生成 `outputs/biz2x9h/BIZ2x9h2_标准规则trace自动初判复核包_20260610_231748.xlsx`、`.md`、`.json`、`_trace复核.csv` |
| 2026-06-10 | BIZ-2x-9h-2 真实样例初判结果 | 已生成 | 41 行 trace 中，系统建议采用 2 行、建议不采用 33 行、需人工确认 6 行；风险等级为中风险 14 行、高风险 27 行；采用列已按系统初判预填“是/否/待确认”，但 `核验结论` 仍需业务员填写，本阶段仍不导出最终四字段清单、不接报价 |
| 2026-06-10 | BIZ-2x-9h-2 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `59 passed, 315 deselected, 1 warning`；新增/更新测试覆盖系统初判结论、建议动作、风险等级、采用列预填、阻断项口径和工作簿字段 |
| 2026-06-10 | BIZ-2x-9h-3 trace 复核转确认行 | 已完成只读转换闭环 | 新增 `app/services/dxf_trace_review_converter.py`、`scripts/biz2x9h3_trace_review_to_confirmation.py` 和 `tests/test_dxf_trace_review_converter_biz2x9h3.py`；只把 `系统建议动作=建议采用`、`是否采用=是`、`核验结论=通过` 且项目名称、项目特征、单位、工程量、工程量来源说明、扣减/合并规则复核均完整的行转换为 BIZ-2x-6 兼容确认行；项目特征仍含“待确认/待补/缺失”时自动阻断 |
| 2026-06-10 | BIZ-2x-9h-3 真实样例预检 | 已生成 | 基于 `outputs/biz2x9h/BIZ2x9h2_标准规则trace自动初判复核包_20260610_231748.xlsx` 生成 `outputs/biz2x9h3/BIZ2x9h3_trace复核转确认行_20260610_233312.md`、`.json`、`_转换问题.csv`、`_跳过行.csv`、`_BIZ2x6确认行.csv`；41 行 trace 中系统建议采用 2 行，但当前 `核验结论` 未填写、项目特征仍含待确认且扣减/合并规则复核为空，因此转换确认行 0、问题行 2、跳过行 39，未生成最终四字段清单 |
| 2026-06-10 | BIZ-2x-9h-3 聚焦测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `61 passed, 315 deselected, 1 warning`；新增测试覆盖复核通过行转换、缺字段阻断、工作簿读取、转换报告输出、BIZ-2x-6 确认包和最终四字段校验输出 |
| 2026-06-10 | BIZ-2x-9h-3 试运行代填闭环 | 已跑通 2 行最终四字段清单 | 新增 `scripts/biz2x9h3_trial_autofill_trace_review.py`，保留原始 9h-2 表不动，另存 `outputs/biz2x9h3/BIZ2x9h2_系统代填通过版_20260610_234052.xlsx`；补齐 `BIZ2x9h-0007`、`BIZ2x9h-0010` 两行艺术造型吊顶天棚的标准项目特征、扣减/合并规则复核和工程量来源说明后，重跑 9h-3 生成 `outputs/biz2x9h3/BIZ2x9h3_trace复核转确认行_20260610_234147_BIZ2x6确认行校验_最终四字段清单.xlsx`；转换确认行 2、问题行 0、最终可导出行 2 |
| 2026-06-10 | BIZ-2x 试运行 Excel SOP 与后续路线 | 已补充 | 新增 `docs/biz-2x-trial-run-excel-sop-and-next-route.md`，明确业务员在 9h-2 表中填写哪些列、通过/有问题判定、9h-3 转换命令、页面/API 化路线、3-5 套样例回归计划和 BIZ-2x-7 报价接入条件 |
| 2026-06-11 | BIZ-2x 最快舒服试运行入口纠偏 | 已改为上传 DWG 直接列项 | 新增 `app/services/dwg_item_listing.py` 并扩展 `app/api/v1/dwg_quantity_trial.py`；Vite 管理台 `/admin/dwg-trial` 已从“上传复核表”改为“上传 DWG 图纸”，调用 `POST /api/v1/admin/dwg-quantity-trial/list-items`，自动执行 DWG -> DXF、DXF 文字/表格提取、材料/做法字段收敛、GB/T active 标准项目匹配，页面直接展示列项候选并可下载列项 Excel/CSV、匹配明细和识别报告；旧复核表转换接口保留为辅助入口 |
| 2026-06-11 | BIZ-2x DWG 上传列项真实样例验证 | 已跑通 | 使用当前 4 张真实 DWG 样例重跑：DWG 4、DXF 4、文字实体 4967、保存文字记录 4933、材料/做法字段 52、图纸线索 39、匹配线索 35、列项候选 35、唯一标准项目 7；最新列项 Excel 为 `outputs/biz2x_trial/BIZ2x_DWG上传列项_20260611_003854_列项候选.xlsx`；唯一标准项目包括块料楼地面、楼(地)面涂膜防水、平面吊顶/天棚、艺术造型吊顶天棚、窗台板、窗帘盒、金属踢脚线 |
| 2026-06-11 | BIZ-2x DWG 上传列项测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests\test_dwg_quantity_trial_biz2x.py tests\test_dwg_oda_converter_biz2x2.py tests\test_drawing_standard_matcher_biz2x4.py` 结果为 `9 passed, 1 warning`；`ai-web` 执行 `npm.cmd run build` 通过，仅保留 Vite chunk size 提示；新增 API 测试覆盖 DWG 上传列项、返回候选行、下载列项 Excel，并修复 ODA 转换命令统一使用绝对路径和 Windows `.dwg/.DWG` 重复统计问题 |
| 2026-06-14 | BIZ-2x DWG 上传列项接入几何建议量 | 已完成首版 | 扩展 `app/services/dwg_item_listing.py`，在 DWG 上传列项后继续执行 CAD 几何图元探测、试运行默认比例/单位确认、低风险图层/块名映射、低风险几何建议量、active GB/T 标准规则绑定 trace 和标准规则 trace 复核包生成；列项 Excel 新增“系统建议工程量、建议单位、建议量状态、标准规则Trace状态、算量证据”，API 新增 `quantity_trace_rows`、`geometry_quantity_summary` 和 trace 复核工作簿下载 |
| 2026-06-14 | BIZ-2x DWG 上传列项 + 建议量真实样例验证 | 已跑通 | 使用当前 4 张真实 DWG 样例重跑：列项候选 35、有关联建议量的列项行 30、几何实体 63898、面积候选 1200、长度候选 1200、数量候选 1200、低风险映射分组 54、几何建议量 54、可复核几何建议量 52、标准规则 trace 41、可复核标准规则 trace 20、系统建议采用 trace 2；最新列项 Excel 为 `outputs/biz2x_trial/BIZ2x_DWG上传列项_20260614_175826_列项候选.xlsx`，同时输出 `标准规则trace.csv` 和 `标准规则trace复核包.xlsx` |
| 2026-06-14 | BIZ-2x DWG 上传列项 + 建议量边界确认 | 已记录 | 当前建议量按标准项目编码汇总关联到列项候选，尚未逐条绑定每个图纸文字线索；页面和 Excel 已明确“同标准项目有 N 条可复核建议量，未逐条绑定图纸线索，未作为最终工程量”。未完成人工复核、项目特征值补齐、材料/做法区域绑定、扣减/合并规则确认前，不生成最终四字段清单、不接报价 |
| 2026-06-14 | BIZ-2x DWG 上传列项 + 建议量测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `65 passed, 315 deselected, 1 warning`；`ai-web` 执行 `npm.cmd run build` 通过，仅保留 Vite chunk size 提示；新增测试覆盖标准规则 trace 合并进列项行、建议工程量状态和 CAD 证据展示 |
| 2026-06-14 | BIZ-2x 列项与 CAD 建议量逐条绑定初判 | 已完成首版 | 在 `app/services/dwg_item_listing.py` 中新增逐条绑定评分逻辑：同标准编码只是基础条件，还会综合来源 DXF、图纸识别名称/规格、材料做法、CAD 图层/块名、业务提示、节点/大样/说明风险和小面积风险，输出“逐条绑定状态、绑定建议编号、绑定置信度、绑定说明”；页面 `/admin/dwg-trial` 同步展示绑定状态和绑定说明 |
| 2026-06-14 | BIZ-2x 逐条绑定真实样例验证 | 已跑通 | 当前 4 张 DWG 最新重跑结果：列项候选 35、逐条自动绑定 0、需人工选择 CAD 候选 30、未找到 CAD 标准规则建议量 5、可复核标准规则 trace 20；最新列项 Excel 为 `outputs/biz2x_trial/BIZ2x_DWG上传列项_20260614_182149_列项候选.xlsx`。结论：本样例列项多来自材料说明/节点做法，系统保守判定不得自动逐条绑定，避免把同标准编码的 CAD 面积复制到不确定列项 |
| 2026-06-14 | BIZ-2x 逐条绑定测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `66 passed, 315 deselected, 1 warning`；`ai-web` 执行 `npm.cmd run build` 通过；新增测试覆盖高置信逐条绑定和材料说明/节点图低置信候选不自动绑定 |
| 2026-06-14 | BIZ-2x CAD 候选选择 API | 已完成首版 | `app/services/dwg_item_listing.py` 为每条列项新增 `CAD候选列表`，并生成 `line_quantity_candidate_rows` 扁平候选明细；每个候选包含建议编号、标准项目、建议工程量、单位、trace状态、可复核状态、绑定评分、推荐动作、推荐原因、CAD公式、CAD来源、CAD行号、未解决事项和阻断原因。列项 Excel 仍保持业务审核列，不把复杂候选对象写入表格 |
| 2026-06-14 | BIZ-2x CAD 候选选择页面 | 已完成首版 | `/admin/dwg-trial` 的列项表新增展开行，可查看本列项的 CAD 候选量并在页面内标记“采纳 / 不采纳 / 有问题”；页面新增候选选择统计和候选数列。当前选择结果保存在页面状态中，刷新后不持久化，下一步需要提交后端生成最终四字段 Excel |
| 2026-06-14 | BIZ-2x CAD 候选选择真实样例验证 | 已跑通 | 使用 4 张真实 DWG 重跑：列项候选 35、需人工选择 CAD 候选 30、行级候选列项 30、CAD 候选明细 102、标准规则 trace 41；最新输出为 `outputs/biz2x_trial/BIZ2x_DWG上传列项_20260614_184031_列项候选.xlsx` 和 `outputs/biz2x_trial/BIZ2x_DWG上传列项_20260614_184031.json` |
| 2026-06-14 | BIZ-2x CAD 候选选择测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `66 passed, 315 deselected, 1 warning`；`ai-web` 执行 `npm.cmd run build` 通过；新增测试覆盖 API 候选明细返回、行级候选列表、推荐动作、CAD公式和扁平候选行 |
| 2026-06-14 | BIZ-2x CAD 候选采纳生成最终四字段 Excel API | 已完成首版 | 新增 `app/services/dwg_selection_finalizer.py` 和 `POST /api/v1/admin/dwg-quantity-trial/finalize-selection`；页面提交“采纳”候选后，后端校验建议编号必须属于当前列项、候选必须可复核、工程量必须大于 0，并转换为 BIZ-2x-6 确认行后复用原最终四字段校验导出 Excel；同一标准项目下同一个 CAD 建议编号重复采纳会被阻断 |
| 2026-06-14 | BIZ-2x CAD 候选采纳页面闭环 | 已完成首版 | `/admin/dwg-trial` 新增“生成最终Excel”按钮、最终清单结果下载区和最终清单校验问题区；页面会提交所有“采纳”选择，并返回最终 Excel、确认行工作簿、转换报告、问题 CSV 等文件 |
| 2026-06-14 | BIZ-2x CAD 候选采纳真实样例验证 | 已跑通 | 基于真实 4 张 DWG 的 `outputs/biz2x_trial/BIZ2x_DWG上传列项_20260614_184031.json`，采纳 2 个不重复 CAD 候选后生成 `outputs/biz2x_trial/BIZ2x_DWG候选采纳生成最终清单_20260614_193244_BIZ2x6确认行校验_最终四字段清单.xlsx`；最终 Excel 表头为“项目名称、项目特征、单位、工程量”，最终可导出 2 行 |
| 2026-06-14 | BIZ-2x CAD 候选采纳测试 | 已通过 | `C:\Users\12521\miniconda3\python.exe -m pytest tests -k biz2x` 结果为 `67 passed, 315 deselected, 1 warning`；`ai-web` 执行 `npm.cmd run build` 通过；新增测试覆盖页面采纳 payload、后端最终 Excel 生成和文件下载 |

## 15. 当前不做

- 不直接改现有报价逻辑。
- 不新增数据库结构。
- 不把 CAD 几何建议量直接当作最终工程量。
- 不在未人工复核、未补齐项目特征值、未确认扣减/合并规则时导出最终四字段清单或发起报价。
- 不把同一标准项目编码下的 CAD 候选量复制到所有列项行。
- 不承诺自动完成全部工程量计算。
- 不把 AI 输出作为无人工确认的最终工程量清单。
- 不自动把识别结果写入成本库或 RAG。

## 16. 下一步建议

1. 继续保留 `gbtn50854_2024_word_active_20260610_024610.json` 为唯一 active 标准库基线；如后续发现 Word 表格原文差异，再按版本化方式更新。
2. 业务员先填写 `outputs/biz2x6/BIZ2x6_图纸识别人工确认补量包_20260610_193659.xlsx` 的“人工确认补量”Sheet：采用/不采用、核验结论、人工工程量、确认单位、工程量来源说明、项目名称和项目特征。
3. 填完确认表后执行 `scripts/biz2x6_confirmation_pack.py export-final`，通过校验后生成最终四字段 Excel；未通过则按校验报告逐行修正。
4. BIZ-2x-9c/9d/9e 已生成低风险几何建议量：重点复核 `outputs/biz2x9cde/BIZ2x9cde_低风险几何建议量_20260610_224353_建议量清单.csv` 中 52 条可复核建议和 2 条阻断项，尤其是面积类是否来自真实施工区域而不是节点/图例。
5. BIZ-2x-9f/9g 已生成标准规则 trace：重点复核 `outputs/biz2x9fg/BIZ2x9fg_标准规则绑定trace_20260610_225642_标准规则trace.csv` 中 20 行可进入人工复核的 trace，确认标准项目、做法和扣减规则；超出装饰建筑标准范围的插座、开关、洁具、普通灯具等继续阻断。
6. BIZ-2x-9h-2 已生成系统自动初判复核工作簿：优先查看 `outputs/biz2x9h/BIZ2x9h2_标准规则trace自动初判复核包_20260610_231748.xlsx` 的“标准规则trace复核”Sheet；当前系统建议采用的 2 行为 `BIZ2x9h-0007` 和 `BIZ2x9h-0010`，需要补全项目特征、扣减/合并规则复核，并在核验无误后填写 `核验结论=通过`。
7. BIZ-2x-9h-3 已用系统代填方式跑出当前样例的最终四字段 Excel：`outputs/biz2x9h3/BIZ2x9h3_trace复核转确认行_20260610_234147_BIZ2x6确认行校验_最终四字段清单.xlsx`。这说明技术闭环可用，但该代填结果仍标记为试运行验证版，正式业务试运行前建议业务复核。
8. 最快舒服试运行使用 `/admin/dwg-trial`：业务员先在 9h-2 Excel 中完成复核，再上传页面一键生成最终四字段 Excel；页面/API 只处理确认闭环，不接报价、不写数据库。
9. 立即收集 3-5 套真实图纸按轻量入口回归；每套记录 trace 行数、系统建议采用数、问题行、最终清单行、误采纳和漏项。
10. 样例回归通过后启动 BIZ-2x-7 报价接入：先让最终四字段 Excel 走现有需求单解析/标准清单确认链路，再做图纸识别任务到报价任务的自动跳转。
