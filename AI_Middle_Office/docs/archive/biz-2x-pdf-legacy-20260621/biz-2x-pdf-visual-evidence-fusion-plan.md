# BIZ-2x PDF 高清视觉证据链与 DXF 证据合并规划

日期：2026-06-17  
状态：规划完成，首版代码已接入  
范围：业务员上传正式 PDF，系统把 PDF 渲染为高清 PNG，按页和分块提取视觉证据，再与 DXF 结构化证据合并，最后进入现有 GB/T 标准库 + LLM 动态列项 + 人工确认链路。

## 0. 2026-06-17 首版实现进度

已接入代码：

- PDF-1：新增 DWG + PDF 上传接口 `POST /api/v1/admin/dwg-quantity-trial/list-items-with-pdf`，旧 DWG 单独上传接口保持不变。
- PDF-2：新增 PDF 基础解析服务，支持页数、页面尺寸、文件 hash、内嵌文字优先提取；缺少 PDF 依赖时提供 regex fallback。
- PDF-3：新增高清 PNG 渲染尝试，优先调用 `pdftoppm`；环境未配置 Poppler 时输出 `render_tool_missing` 阻断状态，不伪造真实 PNG。
- PDF-4：新增页面分块 tile manifest；有渲染图和 Pillow 时生成真实 tile 图片，无渲染图时生成可追溯逻辑 tile。
- PDF-5：新增结构化 PDF 证据提取，优先使用 PDF 内嵌文字；当高清 tile 图片存在且 `FEATURE_PDF_TILE_VISION=true` 时，会调用 GLM-4V 图纸 tile 专用 prompt，提取材料编号、房间、图例、箭头关系、图签和做法说明。
- PDF-6：新增 DWG/PDF 对应关系评分，按材料编号、文字 token、文件名相似度判断 `auto_matched / needs_manual_bind / blocked`。
- PDF-7：新增 DXF + PDF 证据合并报告，未匹配时阻断合并。
- PDF-8：PDF 合并证据已作为 `evidence_signals` 接入现有 R0-R9 动态列项输入。
- PDF-9：Vite `/admin/dwg-trial` 已支持选择正式 PDF，并展示 PDF 证据链摘要、证据表和输出文件。

当前限制：

- 正式环境需安装 Python 依赖 `pypdf`、`pdfplumber`、`Pillow`。
- 高清 PNG 真实渲染需安装 Poppler，并保证 `pdftoppm` 在 PATH 中，或配置 `PDFTOPPM_EXE`。
- GLM-4V tile 识别受 `FEATURE_PDF_TILE_VISION` 和 `PDF_TILE_VISION_MAX_TILES` 控制；如果 Poppler 未安装导致无真实 tile 图片，则自动跳过视觉模型调用并在报告中显示状态。
- PDF 仍不直接出工程量；最终工程量必须来自 DXF 规则 trace 或人工补量。

## 1. 首版边界

本规划只解决“正式 PDF 作为视觉证据”的链路，不在首版实现系统自动 DWG 转 PDF。

首版输入：

- 业务员上传 DWG。
- 业务员上传与 DWG 对应的正式 PDF。
- 系统仍使用现有 DWG -> DXF 结构化解析获取文字、标注、图层、块和几何线索。
- PDF 只作为图纸视觉证据来源，用于补足区域归属、图例说明、箭头指向、非规范文字、整体空间关系。

首版输出：

- PDF 页面清单。
- 高清 PNG 页面图。
- 分块 tile 清单。
- PDF 视觉证据 JSON/CSV/Markdown。
- DWG/PDF 对应关系校验报告。
- DXF + PDF 合并证据报告。
- 接入 R0-R9 后的动态列项人工确认表。
- 人工确认后导出的四字段 Excel：项目名称、项目特征、单位、工程量。

首版不做：

- 不自动 DWG -> PDF。
- 不把视觉模型识别出的数字直接当最终工程量。
- 不在 DWG/PDF 对应关系不明确时合并证据。
- 不跳过 R0-R9 硬校验和人工确认。
- 不绕过标准库范围；项目名称、项目特征、单位必须来自 GB/T 标准库或经人工确认的合法口径。

## 2. 总体流程

当前主链路：

```text
DWG 上传
-> ODA 转 DXF
-> DXF 结构化解析
-> GB/T 标准库映射
-> R2.5 图纸类型/算量场景路由
-> 分类取证
-> GB/T/补充清单规则算量
-> 人工确认
-> 四字段 Excel
```

新增 PDF 链路后：

```text
DWG + 正式 PDF 上传
-> DWG 转 DXF
-> DXF 结构化证据
-> PDF 基础解析
-> PDF 高清 PNG 渲染
-> PDF 分块 tile
-> OCR/视觉模型提取 PDF 证据
-> DWG/PDF 对应关系校验
-> DXF + PDF 证据合并
-> R0-R9 标准库约束型动态列项
-> 人工确认/补量
-> 四字段 Excel
```

核心分工：

| 任务 | DXF 负责 | PDF/PNG 负责 | 最终规则 |
| --- | --- | --- | --- |
| 材料编号和文字 | 优先读取 TEXT/MTEXT/ATTRIB | 补充漏读、图例文字、箭头文字 | DXF 优先，PDF 辅证 |
| 尺寸数字 | 优先读取 DIMENSION/标注实体 | 只做提示和复核 | 不能仅凭视觉数字出最终量 |
| 图层/块/实体 | 结构化读取 | 不负责 | DXF 为准 |
| 区域归属 | 坐标和几何候选 | 视觉判断房间、图例、箭头和空间关系 | 合并后需人工可追溯 |
| 设计意图 | 弱 | 强 | 只进入项目特征/证据，不直接算量 |
| 工程量 | 结构化几何和标准规则 | 辅助判断范围 | 必须通过规则 trace 或人工补量 |

## 3. PDF-1 双文件上传与任务建模

目标：让业务员在 DWG 识图入口同时上传正式 PDF，系统建立同一识图任务。

建议改动：

- 后端新增或扩展 API：`POST /api/v1/admin/dwg-quantity-trial/list-items-with-pdf`。
- 前端 `/admin/dwg-trial` 增加 PDF 上传区。
- 任务目录保留原 DWG/DXF 输出，并新增 PDF 输出子目录。

建议输入：

```json
{
  "dwg_files": ["*.dwg"],
  "pdf_files": ["*.pdf"],
  "source_mode": "manual_pdf_upload",
  "business_note": "业务员上传正式 PDF"
}
```

建议输出：

```json
{
  "task_id": "biz2x_pdf_20260617_xxxxxx",
  "dwg_files": [],
  "pdf_files": [],
  "pdf_pipeline_status": "pending",
  "dxf_pipeline_status": "pending"
}
```

验收口径：

- 可以同时上传 DWG 和 PDF。
- 只上传 DWG 时仍走旧链路。
- 上传 PDF 但无 DWG 时不进入最终四字段链路，只允许生成 PDF 诊断报告。
- PDF 文件必须记录原文件名、页数、大小、hash。

## 4. PDF-2 PDF 基础解析

目标：先不调用视觉模型，读取 PDF 的基本结构和可直接提取的文字。

建议新增服务：

- `AI_Middle_Office/app/services/pdf_drawing_parser.py`
- `AI_Middle_Office/scripts/biz2x_pdf_basic_parse.py`

处理内容：

- 页数、页面尺寸、旋转角度。
- PDF 内嵌文字。
- 页面标题候选、图号候选、设计阶段候选、专业候选。
- PDF 文件 hash 和页 hash。
- 是否为扫描版 PDF。
- 是否需要 OCR/视觉模型。

建议输出文件：

- `PDF_基础解析.json`
- `PDF_页面清单.csv`
- `PDF_内嵌文字.csv`
- `PDF_基础解析.md`

验收口径：

- 能解析页数和每页尺寸。
- 能判断文本型 PDF 或扫描型 PDF。
- 对文本型 PDF，优先保留可直接提取的文字和页内位置。
- 对扫描型 PDF，明确标记 `needs_visual_recognition=true`。

## 5. PDF-3 高清 PNG 渲染

目标：把 PDF 页面渲染为足够清晰的 PNG，解决放大后才能看清细节的问题。

建议新增服务：

- `AI_Middle_Office/app/services/pdf_drawing_renderer.py`
- `AI_Middle_Office/scripts/biz2x_pdf_render_pages.py`

渲染策略：

| 用途 | DPI 建议 | 说明 |
| --- | --- | --- |
| 页面预览 | 150-200 DPI | 前端快速查看整页 |
| 识别基础图 | 300-400 DPI | OCR/视觉模型主要输入 |
| 局部裁剪 | 500-600 DPI | 小字、材料表、图例、尺寸密集区域 |

必须控制：

- 单张 PNG 最大像素数。
- 单页最大输出文件大小。
- 渲染失败页的错误报告。
- 页面坐标和像素坐标的转换比例。

建议输出：

```json
{
  "page": 1,
  "pdf_width_pt": 2384.0,
  "pdf_height_pt": 1684.0,
  "render_dpi": 400,
  "image_width_px": 13244,
  "image_height_px": 9356,
  "scale_x": 5.5556,
  "scale_y": 5.5556,
  "png_path": "..."
}
```

验收口径：

- 至少能对 A1/A2/A3 图纸生成可放大的高清 PNG。
- 前端能打开整页预览。
- 后端能根据 PDF 坐标反算 PNG 像素坐标。
- 小字识别失败时可按区域生成更高清裁剪图。

## 6. PDF-4 页面分块与重点区域裁剪

目标：施工图整页信息过密，不能直接把整页交给视觉模型。必须先分块，再按重要区域识别。

建议新增服务：

- `AI_Middle_Office/app/services/pdf_tile_planner.py`
- `AI_Middle_Office/scripts/biz2x_pdf_tile_plan.py`

分块类型：

- 整页缩略图：用于识别图纸类型和整体布局。
- 固定网格块：例如 3x3、4x4，用于覆盖全页。
- 重叠块：每块保留 10%-20% overlap，避免文字跨块丢失。
- 重点裁剪块：材料表、图例、图签、平面核心区、立面核心区、箭头密集区。

tile manifest 示例：

```json
{
  "tile_id": "p001_g03_r02_c01",
  "page": 1,
  "tile_type": "grid",
  "bbox_pdf": [0, 0, 420, 297],
  "bbox_pixel": [0, 0, 2400, 1700],
  "overlap_ratio": 0.15,
  "image_path": "...",
  "priority": 70,
  "recognition_status": "pending"
}
```

验收口径：

- 每页都有整页预览 tile。
- 每页都有可覆盖全页的网格 tile。
- 材料表、图签、图例可被单独裁剪。
- tile 与原 PDF 页坐标可互相映射。

## 7. PDF-5 OCR/视觉证据提取

目标：从高清 PNG 和 tile 中提取 PDF 视觉证据，重点补 DXF 难以判断的空间归属和图例语义。

建议新增服务：

- `AI_Middle_Office/app/services/pdf_visual_evidence_extractor.py`
- `AI_Middle_Office/scripts/biz2x_pdf_visual_evidence.py`

识别优先级：

1. PDF 内嵌文字。
2. 本地 OCR 或可控 OCR。
3. GLM-4V/多模态 LLM 对重点 tile 做结构化识别。
4. 只对低置信或高价值区域发起二次高清裁剪识别。

视觉模型任务不是“直接出清单”，而是输出证据：

- 房间名称。
- 材料编号。
- 材料表/图例解释。
- 箭头指向关系。
- 图纸标题和图号。
- 平面图/立面图/节点图/材料表等图纸类型。
- 疑似区域归属关系。
- 疑似不规范标注。

证据 schema：

```json
{
  "evidence_id": "pdf_ev_000001",
  "source_kind": "pdf_visual_tile",
  "source_file": "xxx.pdf",
  "page": 3,
  "tile_id": "p003_focus_material_legend_01",
  "evidence_role": "material_legend",
  "text": "CT-02 600X1200灰色地砖",
  "normalized_text": "CT-02 600X1200 灰色地砖",
  "bbox_pdf": [120.0, 88.0, 210.0, 105.0],
  "bbox_pixel": [667, 489, 1167, 583],
  "confidence": 0.86,
  "model": "glm-4v",
  "needs_manual_review": false
}
```

验收口径：

- 可以输出结构化 JSON，而不是自然语言长段回答。
- 每条证据能回到 PDF 页、tile、bbox。
- 视觉证据有置信度和来源。
- 低置信证据只进入复核，不直接参与最终算量。

## 8. PDF-6 DWG/PDF 对应关系校验

目标：确保业务员上传的 PDF 确实对应当前 DWG，防止把不同版本图纸的证据合并。

建议新增服务：

- `AI_Middle_Office/app/services/drawing_file_matcher.py`
- `AI_Middle_Office/scripts/biz2x_dwg_pdf_match.py`

匹配信号：

| 信号 | 来源 | 权重建议 |
| --- | --- | --- |
| 项目名称/工程名称 | DXF 文本 + PDF 图签 | 高 |
| 图号/图名 | DXF 文本 + PDF 页标题 | 高 |
| 专业/图纸类型 | DXF 图层/文件名 + PDF 视觉 | 中 |
| 材料编号集合 | DXF TEXT + PDF OCR/视觉 | 高 |
| 房间名称集合 | DXF TEXT + PDF OCR/视觉 | 中 |
| 页数/图纸数 | DWG 文件数 + PDF 页数 | 低 |
| 日期/版本号 | 图签文字 | 中 |

建议匹配结论：

| 分数 | 状态 | 动作 |
| --- | --- | --- |
| >= 0.75 | auto_matched | 自动允许进入证据合并 |
| 0.55-0.75 | needs_manual_bind | 前端要求人工确认 DWG/PDF 对应关系 |
| < 0.55 | blocked | 阻断合并，只输出诊断报告 |

验收口径：

- 上传明显不相关 PDF 时必须阻断。
- 图号或项目名不一致时必须提示。
- 人工确认动作必须写入报告。
- 未通过对应关系校验时，不允许生成 DXF + PDF 合并证据。

## 9. PDF-7 DXF + PDF 证据合并

目标：把 DXF 的精确结构化数据和 PDF 的视觉语义数据合并为可追溯证据，不让任何单一路径独断。

建议新增服务：

- `AI_Middle_Office/app/services/drawing_evidence_fusion.py`
- `AI_Middle_Office/scripts/biz2x_dxf_pdf_evidence_fusion.py`

合并原则：

- DXF 负责精确文字、坐标、图层、标注、块、几何。
- PDF 负责空间归属、图例解释、箭头指向、图纸类型、整体上下文。
- 同一文字同时存在于 DXF 和 PDF 时，建立 cross_reference。
- PDF 视觉证据不能覆盖 DXF 数字尺寸，只能补充解释。
- 任何合并关系必须有证据链和置信度。

合并关系类型：

- `same_text_cross_source`：DXF 与 PDF 识别到同一文字。
- `material_code_to_legend`：材料编号与材料表/图例解释。
- `material_code_to_room`：材料编号疑似归属房间。
- `room_to_region_candidate`：房间名与 DXF 几何区域候选。
- `arrow_text_to_region`：箭头文字与区域。
- `drawing_page_to_dxf_file`：PDF 页与 DXF 文件对应。
- `visual_note_to_project_feature`：视觉注释补充项目特征。

建议输出：

```json
{
  "fusion_id": "fusion_20260617_xxxxxx",
  "match_status": "auto_matched",
  "evidence_links": [],
  "project_evidence_signals": [],
  "blocked_reasons": [],
  "manual_review_items": []
}
```

验收口径：

- 每个合并结论都能看到 DXF 来源和 PDF 来源。
- 冲突证据必须进入人工复核。
- 低置信空间归属不能自动变成工程量。
- 合并报告可导出 CSV/Markdown，业务和开发都能复核。

## 10. PDF-8 接入 R0-R9 标准库 + LLM 动态列项

目标：把合并证据作为 R0-R9 的输入，让 LLM 做“具体问题具体分析”，但仍受标准库和硬校验约束。

接入点：

- 当前 R0-R9 已有 `evidence_signals` 概念。
- PDF-7 输出的 `project_evidence_signals` 可作为新增输入。
- 现有 `drawing_dynamic_itemization.py` 继续负责标准库召回、LLM itemization、硬校验和人工确认包。

R0-R9 使用方式：

- 项目名称：必须从标准库候选或人工确认标准项中选取。
- 项目特征：可由 DXF/PDF/LLM 综合生成，但必须保留证据出处。
- 单位：必须来自标准库，不允许 LLM 自造。
- 工程量：必须来自 DXF 标准规则 trace 或人工补量；PDF 只能证明范围和归属。

验收口径：

- LLM 可用于细支分项判断。
- LLM 不允许新增标准库不存在的标准编码。
- LLM 不能绕过单位校验。
- LLM 不能用视觉估算量直接写最终工程量。
- 人工确认表必须显示 PDF 证据来源。

## 11. PDF-9 前端人工验收页

目标：让业务员可以在系统上验收“PDF 是否清晰、是否对应 DWG、证据是否可信、最终四字段是否可接受”。

建议前端页面增强：

- 仍使用 `/admin/dwg-trial` 作为首版入口。
- 上传区增加正式 PDF。
- 结果页增加 PDF 标签页。
- 展示 DWG/PDF 匹配状态。
- 展示 PDF 页缩略图和高清页查看。
- 展示 tile 清单。
- 展示 PDF 视觉证据表。
- 展示 DXF + PDF 合并证据表。
- 展示进入 R0-R9 的证据信号。
- 人工确认时可打开 PDF 页/tile 定位原始证据。

业务验收步骤：

1. 上传同一项目的 DWG 和正式 PDF。
2. 检查系统是否生成 DXF 解析报告。
3. 检查系统是否生成 PDF 页面清单。
4. 检查高清 PNG 是否能放大看清小字。
5. 检查材料表、图签、图例是否被切成重点 tile。
6. 检查 PDF 视觉证据是否能定位到页和区域。
7. 检查 DWG/PDF 是否自动匹配或要求人工确认。
8. 检查合并证据是否解释了材料编号、房间、图例的关系。
9. 检查 R0-R9 人工确认表中项目名称、项目特征、单位是否有标准库依据。
10. 仅在工程量有规则 trace 或人工补量后导出四字段 Excel。

验收口径：

- 业务员不需要看开发诊断日志，也能判断 PDF 证据是否可信。
- 任何自动合并、自动列项、自动算量都能追溯到原图页。
- 不清晰、不匹配、不确定的证据会阻断或进入人工确认。

## 12. 开发顺序

建议按以下顺序推进，避免一次性开发过大：

| 阶段 | 内容 | 可验收成果 |
| --- | --- | --- |
| PDF-1 | 双文件上传和任务目录 | 前端能上传 DWG+PDF，后端记录任务 |
| PDF-2 | PDF 基础解析 | 页清单、内嵌文字、扫描判断报告 |
| PDF-3 | 高清 PNG 渲染 | 可放大页面 PNG 和坐标映射 |
| PDF-4 | 分块和重点裁剪 | tile manifest 和重点区域图片 |
| PDF-5 | OCR/视觉证据 | 结构化 PDF 证据 JSON/CSV |
| PDF-6 | DWG/PDF 匹配 | 对应关系评分和阻断/确认 |
| PDF-7 | 证据合并 | DXF + PDF 合并证据报告 |
| PDF-8 | 接 R0-R9 | 动态列项人工确认表带 PDF 证据 |
| PDF-9 | 前端验收 | 业务员可在页面验收全链路 |

最小可用版本：

- PDF-1 + PDF-2 + PDF-3 + PDF-6。
- 先证明“业务员上传正式 PDF -> 系统能清晰渲染 -> 能校验是否对应 DWG”。

第一版业务可验收版本：

- PDF-1 到 PDF-7。
- 能看到 PDF 视觉证据和 DXF 合并证据，但不强求直接出最终工程量。

进入四字段清单版本：

- PDF-1 到 PDF-9。
- 必须通过 R0-R9 标准库约束、人工确认和工程量证据校验。

## 13. 测试与回归建议

后端测试建议：

- `tests/test_pdf_drawing_parser_biz2x.py`
- `tests/test_pdf_drawing_renderer_biz2x.py`
- `tests/test_pdf_tile_planner_biz2x.py`
- `tests/test_pdf_visual_evidence_extractor_biz2x.py`
- `tests/test_dwg_pdf_matcher_biz2x.py`
- `tests/test_drawing_evidence_fusion_biz2x.py`
- `tests/test_dwg_quantity_trial_pdf_biz2x.py`

测试样例建议：

- 同一 DWG + 正确 PDF。
- 同一项目但不同版本 PDF。
- 完全不相关 PDF。
- 扫描版 PDF。
- 文本型 PDF。
- 小字密集材料表 PDF。
- 多页 PDF 对多 DWG。

关键断言：

- PDF 不匹配时不能合并证据。
- PDF 视觉数字不能直接写最终工程量。
- 单位必须来自标准库。
- 工程量缺规则 trace 或人工补量时必须阻断最终导出。
- 每条 PDF 证据必须能追溯到页和 tile。

## 14. 风险控制

主要风险：

- PDF 与 DWG 版本不一致。
- PDF 分辨率不足或扫描质量差。
- 多模态模型漏识别小字。
- 图纸标注拥挤导致 tile 上下文不足。
- LLM 过度推断，把不确定关系说成确定。
- 视觉证据和 DXF 证据冲突。

控制策略：

- 先做 DWG/PDF 匹配，后做证据合并。
- 只把 PDF 作为视觉证据，不作为最终工程量来源。
- 所有视觉证据带置信度和原图定位。
- 高风险关系进入人工确认。
- R0-R9 保持标准库硬约束。
- 最终 Excel 仍走人工确认和工程量规则 trace 校验。

## 15. 当前结论

这条链路可以实现，但目前系统尚未完成 PDF 高清渲染、分块识别、DWG/PDF 匹配和 DXF+PDF 证据合并。

最合理的落地路线不是让 PDF 替代 DXF，而是让 PDF 补足 DXF 难以判断的空间归属和图例语义：

```text
DXF 负责精确结构化数据
PDF/PNG 负责视觉空间语义
标准库负责项目名称/项目特征/单位边界
LLM 负责动态分项和疑难判断
人工确认负责最终责任闭环
```

在这个边界下，DXF + PDF + 国标标准库 + LLM 判断项目，生成可用的四字段列项清单，是可开发、可验收、可逐步上线的。
