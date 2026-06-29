# BIZ-2x PDF 识图工作区整理与最小目标

生成日期：2026-06-19

## 结论先行

当前工作区可以继续使用，但必须先收束。现在的问题不是“完全没有链路”，而是同一目标连续试了多条实验路线，代码、脚本、测试和输出产物都堆在一起，缺少一个唯一基线和下一步最小目标。

当前可信基线是：

`AI_Middle_Office/outputs/pdf_v2_takeoff/r15_local_existing_combined_acceptance`

当前三字段严格验收结果是：

| 指标 | 当前值 |
| --- | ---: |
| 人工答案行 | 127 |
| 系统候选行 | 292 |
| 三字段严格通过 | 55 |
| 通过率 | 43.31% |
| missing_candidate | 4 |
| unit_conflict | 0 |
| feature_review | 61 |
| weak_match | 7 |
| 工程量状态 | deferred_until_three_fields_accepted |

因此：工程量识别、国标库正式输出和完整 Excel 清单验收都不能继续放开。下一步只做三字段阶段的最小闭环。

## 工作区现状

### 1. 代码区

`git status --short` 显示当前工作区存在较多变更：

- 已跟踪修改文件：9 个。
- 未跟踪 BIZ-2x PDF 相关服务、脚本、测试文件约 80 个。
- 另有若干标准文件、导出文件、工具目录和训练资料目录未纳入清晰归档。

已跟踪修改中，主要集中在：

- `AI_Middle_Office/app/services/model_gateway.py`
- `AI_Middle_Office/app/api/v1/dwg_quantity_trial.py`
- `AI_Middle_Office/app/services/dwg_item_listing.py`
- `AI_Middle_Office/tests/test_model_gateway.py`
- `AI_Middle_Office/tests/test_dwg_quantity_trial_biz2x.py`
- 少量配置、依赖和前端入口文件

未跟踪新增中，主要集中在：

- `AI_Middle_Office/app/services/drawing_pdf_*.py`
- `AI_Middle_Office/scripts/biz2x_pdf_*.py`
- `AI_Middle_Office/tests/test_drawing_pdf_*.py`
- `AI_Middle_Office/docs/biz-2x-pdf-*.md`

判断：代码区不是不可用，但已经进入“实验分支”状态。继续开发前，应先把后续要保留的模块分成核心链路、实验工具、测试支撑三类。

### 2. 输出区

`AI_Middle_Office/outputs/pdf_v2_takeoff` 当前约有 8006 个文件，主要类型：

| 类型 | 数量 |
| --- | ---: |
| `.csv` | 2210 |
| `.json` | 1402 |
| `.md` | 1354 |
| `.xlsx` | 1342 |
| `.png` | 852 |
| `.txt` | 842 |
| `.pdf` | 4 |

文件最多的目录包括：

| 目录 | 文件数 | 体积 MB | 用途判断 |
| --- | ---: | ---: | --- |
| `pdf_evidence` | 470 | 29.63 | 早期图纸证据渲染/切片 |
| `external_recall_acceptance_pipeline` | 397 | 58.75 | 早期外部召回验收链路 |
| `r11_measure` | 266 | 66.36 | 工程量相关探索，当前应锁定 |
| `feature_precision_03_finish_zoom` | 203 | 62.68 | 装饰节点/材料表局部图，可复用 |
| `gap_recall_acceptance_pipeline` | 130 | 26.72 | 缺口召回旧链路 |

判断：输出区已经明显混乱。不能再以“最新目录”为准，必须以“当前最佳门禁结果”为准。

### 3. 可信产物

当前可信验收入口：

- `r15_local_existing_combined_acceptance/three_field_gate/r15local_combined_r15local_1530_three_field_gate.json`
- `r15_local_existing_combined_acceptance/three_field_review/r15local_combined_r15local_1530_three_field_review.xlsx`
- `r15_local_existing_combined_acceptance/standard_bill_preview/r15local_combined_r15local_1530_stdbill.xlsx`
- `r15_local_existing_combined_acceptance/closed_loop_stage_report/r15local_combined_r15local_1530_closed_loop.xlsx`

这些文件可以作为下一步的基线。其他 `r15_glm_*`、`r15_ensemble_*`、`qrev_*`、`r10_*` 等目录暂时只作为实验记录，不作为验收入口。

## 为什么耗时很长仍未完成

根因不是 GLM-4V 不能调用，而是目标被证明比普通 OCR 更复杂：

1. 人工清单不是图纸文字逐字摘抄，而是预算员根据平面、立面、节点、材料表、做法和报价习惯综合出来的列项。
2. 直接让 GLM 对整页或切片“列项目”会产生大类项，粒度不贴近人工清单。
3. 继续增加切片数量没有线性提升，20 图、40 图、完整提示词、简短提示词、融合路线都未突破当前最佳基线。
4. 验收门禁很严格，项目名称、项目特征、单位三者必须同时可靠；只要特征缺少关键 token，就进入复核。
5. 工作区没有及时收束，每次实验都保留了完整 JSON/CSV/MD/XLSX/PNG 产物，导致后续判断成本越来越高。

## 7 阶段当前状态

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| 1. 输入图纸 PDF | 已完成 | 4 份信达项目 PDF 已进入链路 |
| 2. 渲染/切片 | 已完成但需收束 | 切片充足，但目录混乱 |
| 3. 图纸识别 | 已跑通 | GLM-4V、局部识别、直接列项都已验证 |
| 4. 候选生成/融合 | 已有基线 | 当前候选 292 行 |
| 5. 三字段验收 | 未通过 | 当前 55/127 |
| 6. 国标格式输出 | 仅预览 | 不能作为最终验收 |
| 7. 工程量识别 | 锁定 | 三字段通过前不做工程量 |

## 现在确立的最小目标

### M0：三字段工作区基线收束 + missing_candidate 清零

目标不是立刻做到 127/127，也不是启动工程量。当前最小目标是：

1. 固定 `r15_local_existing_combined_acceptance` 为唯一当前基线。
2. 不再新增 direct itemization 大批量试验。
3. 只针对当前 4 条 `missing_candidate` 做 answer-blind 图纸证据召回。
4. 生成一个新的 `r16_missing_candidate_recall` 结果包。
5. 验收指标只看：
   - `missing_candidate = 0`
   - `unit_conflict = 0`
   - `candidate_count > 292`
   - 工程量仍保持锁定

如果 M0 通过，再进入 M1：处理 `feature_review=61` 的高价值项族，而不是全量盲扫。

## M0 不做什么

M0 明确不做：

- 不做工程量识别。
- 不承诺三字段 127/127。
- 不做前端页面。
- 不改数据库。
- 不接正式报价链路。
- 不删除历史输出目录。
- 不把人工答案字段直接喂给 GLM 当识别证据。

## 下一步执行路线

### Step 1：建立当前基线索引

在输出区新增一个轻量 manifest，指向当前可信基线和关键文件。后续所有对比都从 manifest 读，不再从最新目录猜。

### Step 2：整理 4 条 missing_candidate

当前 4 条缺口是：

1. `零星砌筑` / `1、过厅砖砌地台，抬高240mm` / `m³`
2. `防水保护层` / `1、10厚水泥砂浆防水保护层` / `㎡`
3. `陶粒回填` / `1、隔墙陶粒回填` / `m³`
4. `钢化玻璃造型柱（卡座区）` / `1、10mm钢化玻璃造型柱，200*200*2366mm，4套` / `套`

处理方式：用已渲染的局部节点图作为任务图片输入，让 GLM 只提取图纸证据字段，不让模型直接照答案补表。

### Step 3：重跑三字段验收

将新增证据导入当前基线，生成 `r16_missing_candidate_recall`，只比较 M0 指标。

### Step 4：决定是否进入 M1

如果 M0 通过，下一阶段只处理 `feature_review` 的项目族，优先从出现频率高、单位稳定、证据集中的项目开始。

如果 M0 不通过，说明当前图纸切片仍无法证明这些 missing 项，需要回到 PDF 渲染层，重新定位更清晰的节点/材料表区域。

## 工作区整理原则

1. 不删除历史实验目录，只停止继续从它们里随意取结果。
2. 不把最新产物等同于最好产物。
3. 每个新实验必须有目标指标、输入基线、输出目录和结论。
4. 工程量相关目录暂时冻结，直到三字段门禁通过。
5. 后续代码收束时，先保留可复现链路，再清理一次性实验脚本。

