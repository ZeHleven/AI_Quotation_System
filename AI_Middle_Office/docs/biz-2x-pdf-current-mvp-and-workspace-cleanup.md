# BIZ-2x PDF 当前 MVP 目标与工作区整理

生成日期：2026-06-21

## 当前唯一目标

当前最小目标不是严格三字段验收，也不是精准工程量，而是：

`上传整套 PDF 图纸 -> AI 识图列项 -> 按国标清单口径整理 -> 输出四字段 Excel 预览版`

四字段为：

- 项目名称
- 项目特征
- 单位
- 工程量

当前质量要求是“看得过去、像一份预算清单、方便人工继续修改”。本阶段允许工程量为粗略识别、模型建议或 `待复核/待算量`，不要求和人工清单逐行一致。

## 当前主链路

### 保留为主链路

1. API 入口：
   - `AI_Middle_Office/app/api/v1/dwg_quantity_trial.py`
   - 关键接口：`POST /api/v1/admin/dwg-quantity-trial/list-items-from-pdf`

2. PDF 直接识图服务：
   - `AI_Middle_Office/app/services/drawing_pdf_direct_itemizer.py`

3. PDF 渲染/切片基础能力：
   - `AI_Middle_Office/app/services/drawing_pdf_evidence_pipeline.py`

4. AI 候选工程量建议：
   - `AI_Middle_Office/app/services/drawing_pdf_ai_quantity_suggester.py`

5. GLM-4V 调用与 JSON 解析：
   - `AI_Middle_Office/app/services/model_gateway.py`

6. 国标库检索：
   - `AI_Middle_Office/app/services/quantity_standard_index.py`
   - `AI_Middle_Office/app/services/quantity_standard_library.py`

7. 四字段 Excel 输出：
   - 复用 `AI_Middle_Office/app/services/dwg_item_listing.py` 中的 `write_quantity_list_outputs`

### 当前配置

当前 PDF 直接识图相关开关在：

- `AI_Middle_Office/app/core/config.py`
- `AI_Middle_Office/.env.example`

关键项：

- `FEATURE_PDF_DIRECT_ITEMIZATION`
- `PDF_DIRECT_ITEMIZATION_MAX_IMAGES`
- `FEATURE_PDF_AI_QUANTITY_SUGGESTION`
- `PDF_AI_QUANTITY_SUGGESTION_MAX_IMAGES`
- `ZHIPU_API_KEY`
- `GLM_VISION_MODEL`

## 暂时降级为实验/质量提升链路

以下能力不是当前 MVP 主线，先归档为质量提升或实验工具：

- PDF 三字段严格验收
- `missing_candidate` 清零目标
- feature precision capture
- object recall / gap recall
- external recall template
- closed loop stage report
- PDF + DXF 融合
- 工程量精准算量 trace

这些不是没价值，而是不应该继续挡住当前 MVP：先让 PDF 能输出一份“看得过去”的四字段 Excel。

## 当前输出区保留原则

`AI_Middle_Office/outputs/pdf_v2_takeoff` 根目录只保留当前需要直接查看的内容：

1. `source_pdfs_xinda`
   - 当前信达项目 4 份源 PDF。

2. `r15_glm_direct_itemization_style_short`
   - 当前最接近“PDF 直接生成四字段预览”的历史结果。

3. `r15_local_existing_combined_acceptance`
   - 历史严格三字段基线，只作质量对照，不再作为当前 MVP 门禁。

4. `CURRENT_BEST.md`
   - 历史基线入口。后续应新增新的 PDF MVP 入口文件替代。

其他实验目录归档到 `_archive_20260621_experiments`。

## 当前代码整理原则

本轮不直接移动服务代码，因为 `drawing_pdf_*` 服务、脚本、测试存在互相引用，贸然移动会破坏 import。

代码层后续按三类收束：

1. `current_mvp`
   - PDF 上传到四字段 Excel 的必要文件。

2. `quality_eval`
   - 严格三字段验收、人工答案对比、质量门禁。

3. `experimental`
   - recall、feature precision、外部召回模板等探索工具。

## 当前不做

- 不删除历史实验目录。
- 不删除代码文件。
- 不继续跑 GLM。
- 不做精准工程量。
- 不以三字段 `55/127` 作为当前 MVP 是否可用的阻断条件。
- 不把旧文档中的严格验收 M0 继续当作当前目标。

## 下一步建议

1. 先用当前主链路重新跑一次 4 份 PDF，生成新的四字段 Excel。
2. 把输出命名为 `r16_pdf_mvp_four_field_preview`。
3. 人工只看“是否像一份可改的清单”，不做 127 行精准验收。
4. 通过后，再做质量提升：
   - 项目名称更贴近人工清单
   - 项目特征更完整
   - 单位更稳定
   - 工程量从 `待复核` 逐步变成可解释建议量

