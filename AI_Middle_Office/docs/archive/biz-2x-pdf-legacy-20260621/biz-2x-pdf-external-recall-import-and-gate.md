# BIZ-2x PDF 外部补识图导入与三字段门禁

## 目标

当前 PDF 识图最小闭环先验收三字段：

- 项目名称
- 项目特征
- 单位

工程量必须等三字段质量门禁通过后再启用。外部 GLM、人工识图或对象召回工作台的结果只能作为补充图纸证据导入，不能直接改人工答案，也不能直接解锁工程量。

## 外部证据格式

推荐 JSON：

```json
{
  "evidence_rows": [
    {
      "source_file": "03.xxx.pdf",
      "page": 1,
      "tile_id": "p001_whole",
      "vision_pass": "door_window_demolition",
      "evidence_role": "construction_note",
      "discipline": "decoration",
      "item_hint": "拆除不锈钢玻璃门",
      "spec_or_method": "含门套、门扇及五金拆除并清运",
      "suggested_unit": "套",
      "text": "图纸可见拆除不锈钢玻璃门说明",
      "confidence": 0.86,
      "model": "offline-glm-export",
      "needs_manual_review": true,
      "reason": "门窗拆除说明可见"
    }
  ]
}
```

也支持 CSV / XLSX。最少需要能形成 `item_hint/spec_or_method/text` 中至少一个有效内容；如果能提供 `source_file/page/tile_id/vision_pass`，系统会更容易把证据追溯到召回计划。

## 对象召回工作台回填

当三字段验收仍有 `missing_candidate` 时，流水线会输出两类文件：

- `object_recall_pack/*.xlsx`：系统生成的对象缺口任务包，主要用于追溯和机器处理中间产物。
- `object_recall_workbench/*.xlsx`：给人工或外部识图填写的工作台，工作表名为 `object_recall_workbench`，包含 `open_image` 图片链接。

实际回填入口优先使用 `object_recall_workbench/*.xlsx`。

业务或外部识图只允许填写这些证据字段：

- `evidence_item_hint`
- `evidence_spec_or_method`
- `evidence_suggested_unit`
- `evidence_text`

这些字段必须来自图纸证据。`target_item_name / target_feature / target_unit` 只是人工答案参考列，不能被系统当作证据导入。

工作台填写后，先跑预检：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_template_status.py `
  --external-template path\to\filled_object_recall_workbench.xlsx `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\external_recall_template_status
```

预检要求：

- `importable_row_count > 0` 才能进入导入验收
- `answer_only_count` 只代表参考列存在，不代表可导入证据
- 空对象包不会生成候选行

如果要先尝试用系统已有 V2 图纸证据做保守预填，可运行：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_object_recall_workbench_prefill.py `
  --object-workbench AI_Middle_Office\outputs\pdf_v2_takeoff\external_recall_acceptance_pipeline\object_recall_workbench\BIZ2x_PDF_external_recall_acceptance_20260618_auto_workbench_object_workbench.xlsx `
  --v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\BIZ2x_PDF_V2证据驱动列项_20260618_gap_trace_pack.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_workbench_prefill `
  --timestamp 20260618_safe_prefill_v1
```

该预填只使用已有 `evidence_rows`，并按专业类型、源文件/页码、对象关键词过滤；`target_*` 答案参考列不会被当成证据。

## 一键导入并验收

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\BIZ2x_PDF_V2证据驱动列项_20260618_gap_trace_pack.json `
  --external-results path\to\external_recall_results_or_filled_object_workbench.xlsx `
  --recall-plan-json AI_Middle_Office\outputs\pdf_v2_takeoff\gap_recall_plan\BIZ2x_PDF_gap_recall_plan_20260618_stage2_p1_p2.json `
  --image-root AI_Middle_Office\outputs\pdf_v2_takeoff\gap_review_pack `
  --image-root AI_Middle_Office\outputs\pdf_v2_takeoff\pdf_evidence `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\external_recall_acceptance_pipeline `
  --source-name offline-object-recall `
  --require-importable
```

`--external-results` 可以重复传入，用于合并多批外部/本地证据：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\BIZ2x_PDF_V2证据驱动列项_20260618_gap_trace_pack.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\external_recall_prefill\BIZ2x_PDF_external_recall_prefill_20260618_source_page_filtered_v2_prefill.xlsx `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_workbench_prefill\BIZ2x_PDF_object_recall_workbench_prefill_20260618_safe_prefill_v1.xlsx `
  --recall-plan-json AI_Middle_Office\outputs\pdf_v2_takeoff\gap_recall_plan\BIZ2x_PDF_gap_recall_plan_20260618_stage2_p1_p2.json `
  --image-root AI_Middle_Office\outputs\pdf_v2_takeoff\gap_review_pack `
  --image-root AI_Middle_Office\outputs\pdf_v2_takeoff\pdf_evidence `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\ext_combined_prefill `
  --source-name combined_source_page_and_object_prefill_v1 `
  --timestamp comb1 `
  --stem-prefix BIZ2x `
  --require-importable
```

如果暂时没有召回计划 JSON，也可以不传 `--recall-plan-json`。系统会保留证据，并在导入校验中标记为 `imported_unmatched_call`，后续仍可进入 V2 回灌和三字段验收。

多批 `--external-results` 合并时，若不同 GLM 执行包里出现重复 `evidence_id`，流水线会自动按来源文件名加前缀，例如 `B2x_03_fixture_clos_7d9599f5__PDFCAP-000001`。这样可以避免多个批次都从 `PDFCAP-000001` 起号导致追溯混淆；未重复的证据 ID 保持原值。

## 答案盲审对象证据采集包

当 `object_recall_workbench` 里仍然只有答案参考列、没有真实 `evidence_*` 字段时，可以先生成答案盲审采集包：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_object_recall_capture_pack.py `
  --object-workbench AI_Middle_Office\outputs\pdf_v2_takeoff\ext_combined_capture\object_recall_workbench\BIZ2x_comb2_object_workbench.xlsx `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_capture_pack `
  --timestamp next_round
```

该采集包输出：
- `capture_tasks`：按同一图纸图片合并后的识图任务，不包含 `target_item_name / target_feature / target_unit`
- `blank_evidence_template`：给外部 GLM-4V 或人工识图填写的证据模板
- `*_prompts/`：每个识图任务对应的提示词文件

采集包默认只包含尚未 `ready_for_import` 的任务；提示词只描述图纸识别要求、源文件、页码、tile 和识图方向，不把人工答案泄露给识图模型。外部识图完成后，把填好的 `blank_evidence_template` 或等价 CSV/XLSX 作为 `--external-results` 再跑一键导入验收。

当 `missing_candidate` 降低后，若主要卡点转为 `feature_enrichment / split_variant_review`，应生成精确规格补召回包，而不是继续堆泛化对象召回：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_feature_precision_capture_pack.py `
  --defect-router AI_Middle_Office\outputs\pdf_v2_takeoff\qrev_1_31_guard_router_wh_r2_unitfix_select_strict\three_field_defect_router\BIZ2xq_qrev31whr2uss_defect_router.json `
  --recall-plan-json AI_Middle_Office\outputs\pdf_v2_takeoff\gap_recall_plan\BIZ2x_PDF_gap_recall_plan_20260618_stage2_p1_p2.json `
  --image-root AI_Middle_Office\outputs\pdf_v2_takeoff\pdf_evidence `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_capture_pack_strict `
  --timestamp qrev31whr2uss `
  --stem BIZ2xq_qrev31whr2uss_feature_precision
```

该包只给外部 GLM-4V 发送 `source_file/page/tile_id/vision_pass/object_classes/feature_gap_families` 和答案盲审 prompt，不发送人工答案、目标规格、目标单位，也不做工程量识别。

采集包也可以通过系统执行器 dry-run 或正式执行。dry-run 不调用外部模型，只验证图片路径、调用数量和答案盲审状态：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_object_recall_capture_run.py `
  --capture-pack-json AI_Middle_Office\outputs\pdf_v2_takeoff\ext_combined_capture\object_recall_capture_pack\BIZ2x_comb2_object_capture.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_capture_run `
  --timestamp comb2_dry_run `
  --stem BIZ2x_comb2_object_capture_dry_run
```

正式调用外部 GLM-4V 时，在同一命令后增加 `--execute`；输出的 `*_evidence.csv/json/xlsx` 可直接作为下一轮 `--external-results` 回灌。执行器只发送 `source_file/page/tile_id/vision_pass/task_nos/object_classes` 和答案盲审 prompt，不发送 `target_item_name / target_feature / target_unit`。

执行器支持分批运行，便于控制成本和逐批观察增益：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_object_recall_capture_run.py `
  --capture-pack-json AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_capture_pack\BIZ2x_comb2_object_capture_v2.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_capture_run `
  --execute `
  --start-call-no 6 `
  --end-call-no 10 `
  --trace-id biz2x-comb2-v2-batch-6-10 `
  --stem BIZ2x_comb2_object_capture_v2_execute_6_10
```

每批执行结果建议先与既有预填证据合并回灌，观察 `matched_three_fields_count / missing_candidate_count / unit_conflict_count / feature_review_count`，确认有增益后再继续下一批。

## 外部证据质量闸门

外部 GLM-4V 或人工证据在导入前可以先跑质量评分，避免把泛化说明、空证据或 `unknown` 单位直接堆进 V2 回灌：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_evidence_quality.py `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_capture_run\BIZ2x_comb2_object_capture_v2_execute_1.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\object_recall_capture_run\BIZ2x_comb2_object_capture_v2_execute_2_5_tasktrace.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\external_evidence_quality `
  --timestamp next_quality
```

质量报告会把证据分成：

- `accepted`：字段完整、对象/做法/单位/来源可追溯，可直接导入。
- `review`：有项目或做法价值，但单位、文本或颗粒度仍需复核。
- `rejected`：空证据、答案参考列、泛化说明、`unknown` 单位等，不进入回灌。

一键验收流水线可启用质量过滤：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\BIZ2x_PDF_V2证据驱动列项_20260618_gap_trace_pack.json `
  --external-results path\to\external_results.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\external_recall_acceptance_pipeline `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

当前推荐执行口径是 `--quality-filter --quality-include-review`：`accepted` 和 `review` 都进入回灌，但质量报告仍保留每行状态，便于人工优先核查 `review`。只导入 `accepted` 会更保守，适合做噪声定位，不适合作为当前三字段召回主线。

质量闸门会清理外部模型把提示词原样抄回来的占位字段，例如 `可见规格、材质、安装方式；没有则留空`、`个/套；不要写数量`。清理后这类行通常进入 `review`，不会被当成字段完整的 `accepted` 行。

质量闸门也会拦截过宽泛的图纸标题、日期、装饰/材料大类、图纸目录和空 `item_hint/spec` 的纯文本证据。当前 4 份 CAD 转 PDF 的文本层抽取均为 `char_count=0`，所以不能依赖 PDF 文本解析补齐三字段，后续仍要走高 DPI 图像裁剪、黑白增强和外部 GLM-4V/人工证据回灌。

## 2026-06-18 当前最佳回灌记录

当前最佳执行包：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev_1_31_guard_router_wh_r2_unitfix_select_strict\eval\BIZ2xq_qrev31whr2uss_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\structured_feature_fusion_qrev31_drain_r2\B2x_qrev31_structured_supply_drain_fusion_r2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_fixture_zoom\B2x_03_fixture_object_capture_run.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_fixture_zoom\B2x_03_fixture_close_object_capture_run.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31drain_fixture_close_r3 `
  --source-name qrev31_drain_fixture_close_r3 `
  --stem-prefix B2x_q31drain_fixture_close_r3 `
  --quality-filter `
  --quality-include-review
```

关键结果：

- 三字段通过：`37/127`，相比严格基线 `27/127` 提升 `+10`。
- 剩余缺口：`missing_candidate=38`、`feature_review=50`、`unit_conflict=0`、`weak_match=2`。
- 外部证据质量：输入 `26` 行，`accepted=24`、`review=2`、`rejected=0`。
- 国标预览：候选 `231` 行，`174` 行已映射，`57` 行未映射。
- 工程量：`blocked_until_three_field_gate_passed`，继续锁定。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31drain_fixture_close_r3\closed_loop_stage_report\B2x_q31drain_fixture_close_r3_20260618_230915_closed_loop.xlsx`

本轮通过的洁具重点行：

- `厕纸架供货及安装`：单位 `个`，映射 `031003014 给、排水附件`。
- `梳妆镜供货及安装`：单位 `个`，映射 `031003014 给、排水附件`。
- `马桶供货及安装`：单位 `套`，映射 `031003006 大便器`。
- `淋浴花洒供货及安装`：单位 `套`，映射 `031003014 给、排水附件`。
- `台盆供货及安装`：单位 `套`，映射 `031003003 洗脸盆`。

仍需复核的洁具重点行：

- `冷热水龙头供货及安装` 仍为 `feature_review`。当前图纸证据只显示泛化 `水龙头数量：3`，没有明确 `冷热` 或冷热水同时存在的证据，不能强行通过。

## 2026-06-19 黑白增强 key-block 补充记录

本轮对 4 份 PDF 做了文本层探测和关键块高 DPI 视觉探针：

- 文本层探测：`AI_Middle_Office\outputs\pdf_v2_takeoff\all_pdf_text_layer_probe\summary.json`，4 份 PDF 均为 `char_count=0`。
- 彩色 CAD key-block：GLM 容易返回标题、日期、泛化材料大类和提示词占位字段，质量闸门会过滤，不作为直接候选扩充依据。
- 黑白增强 key-block：`AI_Middle_Office\outputs\pdf_v2_takeoff\all_pdf_key_blocks_2000dpi\enhanced_bw\B2x_key_blocks_bw_capture_run.json` 能读到部分有效文字，例如 `D-顶面设备（空调、新风、换气扇）`。
- 清洗后的单项回灌包：`qrev31_keyblocks_fan_r1`，只导入 01 材料表/图例中与换气扇相关的一条 evidence，避免把灯具、插座、配电箱、空调和图纸标题噪声一起导入。

`qrev31_keyblocks_fan_r1` 的 gate 结果：

- 三字段严格通过：`37/127`，未超过 `qrev31drain_fixture_close_r3`。
- 系统候选：`232` 行。
- 剩余缺候选：`missing_candidate=37`，比上一版减少 1。
- 特征复核：`feature_review=51`，比上一版增加 1；排气扇已从缺候选进入特征复核，但证据仍不足以 strict pass。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=2`。
- 国标预览：`174/232` 已映射，`58` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_keyblocks_fan_r1\closed_loop_stage_report\B2x_q31_keyblocks_fan_r1_20260619_002200_closed_loop.xlsx`。

结论：黑白增强 key-block 对小字 CAD 图纸比彩色图更有效，但当前增益仍停留在“降低 missing”而非“增加 strict pass”。下一轮应继续用高 DPI/黑白增强裁剪去找剩余机电对象（尤其电热水器）和装饰面层/吊顶/墙面做法证据，不应降低三字段门禁阈值。

## 2026-06-19 装饰可见标注人工复核回灌记录

继续基于高 DPI 裁剪推进装饰缺口时，发现黑白增强会丢失红色引线和黄色材料编号的语义，GLM 在 03 装饰图子图上仍容易输出 `材料清单/立面图`、`墙面装饰材料表` 等泛化噪声。已补强质量闸门：

- 清空 `?/m/?/?/m`、`㎡/m/套/樘/m³` 等单位选项串，避免提示词单位被误判为真实单位。
- 拦截 `材料清单或立面证据`、`材料清单/立面图`、`墙面装饰材料表`、`未指定` 等泛化 evidence。
- 保留真正可见的材料编号/引线文字，如 `WD01 成品木饰面`、`ST02 深咖大理石`、`ST03 人造石`、`MT01 黑色拉丝不锈钢`、`MT02 玫瑰金不锈钢`、`CT04 墙面砖作美缝`、`售卖口`。

当前最佳执行包更新为 `qrev31_finish_manual_r2`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_top5_finish_manual_r1\eval\B2x_q31_top5_finish_manual_r1_20260619_011700_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r2.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r2 `
  --source-name qrev31_finish_manual_r2 `
  --stem-prefix B2x_q31_finish_manual_r2 `
  --timestamp 20260619_013000 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`38/127`，较严格基线 `27/127` 提升 `+11`。
- 系统候选：`244` 行。
- 剩余缺候选：`missing_candidate=30`。
- 特征复核：`feature_review=55`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=4`。
- 国标预览：`185/244` 已映射，`59` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r2\closed_loop_stage_report\B2x_q31_finish_manual_r2_20260619_013000_closed_loop.xlsx`。

这一版的增益来自人工视觉复核可见标注，不来自人工清单答案直接回填。它证明“高 DPI 彩色局部 + 可追溯人工/外部视觉 evidence”可以继续压低缺候选，但 strict gate 仍远未通过，下一步应聚焦剩余 `30` 条 missing 和 `55` 条 feature_review 的真实图纸证据，而不是进入工程量。

03 装饰可见标注执行包 `qrev31_finish_manual_r3`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r2\eval\B2x_q31_finish_manual_r2_20260619_013000_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r3 `
  --source-name qrev31_finish_manual_r3 `
  --stem-prefix B2x_q31_finish_manual_r3 `
  --timestamp 20260619_014600 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`39/127`，较严格基线 `27/127` 提升 `+12`。
- 系统候选：`250` 行。
- 剩余缺候选：`missing_candidate=24`。
- 特征复核：`feature_review=56`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=8`。
- 国标预览：`191/250` 已映射，`59` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r3\closed_loop_stage_report\B2x_q31_finish_manual_r3_20260619_014600_closed_loop.xlsx`。

`r3` 继续基于 03 图纸高 DPI 彩色局部的可见标注，新增/补强 `GL01`、`WD01`、`MT01`、`MR02`、`ST02`、洗手台和淋浴等门窗、玻璃、镜面、台面、隔断相关证据。它把缺候选从 `30` 压到 `24`，并新增 `1` 条 strict pass，但也把更多真实但未完全对齐的候选推进到 `feature_review/weak_match`。后续不能降低门禁，应转向两条线：先补剩余 `24` 条 missing 的真实图纸证据，再用缺陷分流包处理 `56` 条 feature_review 和 `8` 条 weak_match。

在 `r3` 基础上补做装饰/电气分类顺序修复后，当前最佳执行包更新为 `q31_r3_classfix`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r2\eval\B2x_q31_finish_manual_r2_20260619_013000_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r3_classfix `
  --source-name q31_r3_classfix `
  --stem-prefix q31r3cf `
  --timestamp 0325 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`40/127`，较严格基线 `27/127` 提升 `+13`，较 `r3` 提升 `+1`。
- 系统候选：`250` 行。
- 剩余缺候选：`missing_candidate=20`。
- 特征复核：`feature_review=59`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=8`。
- 国标预览：`190/250` 已映射，`60` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r3_classfix\closed_loop_stage_report\q31r3cf_0325_closed_loop.xlsx`。

这次不是新增外部证据，而是修复分类顺序：`MT01` 既可能是装饰材料编号，也可能被旧规则当作电气配管代号。现在先判断门窗、隔断、隔墙，再判断 `SC/MT/JDG` 电气配管，避免“成品不锈钢玻璃门/淋浴隔断”被误命名为“电气配管”。分类修复把一部分缺候选推进到可复核候选，但 strict gate 仍远未通过，下一步仍应补剩余 `20` 条 missing 的真实图纸证据，并处理 `59` 条 feature_review。

在 `q31_r3_classfix` 基础上补做 `清境/清镜` 镜面墙验收归一后，当前最佳执行包更新为 `q31_r3_classfix_mirror`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r2\eval\B2x_q31_finish_manual_r2_20260619_013000_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r3_classfix_mirror `
  --source-name q31_r3_classfix_mirror `
  --stem-prefix q31r3m `
  --timestamp 0350 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`40/127`。
- 系统候选：`250` 行。
- 剩余缺候选：`missing_candidate=19`。
- 特征复核：`feature_review=60`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=8`。
- 国标预览：`190/250` 已映射，`60` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r3_classfix_mirror\closed_loop_stage_report\q31r3m_0350_closed_loop.xlsx`。

这一轮没有新增图纸证据；它利用 r3 已有的 `MR02 清镜` 图纸证据，把人工答案中的 `清境墙面MR-02` 识别为同一镜面墙候选。因为候选特征仍缺 `木方+15厚阻燃板基层、暗藏灯槽、黑色拉丝不锈钢包边MT-02` 等完整做法，所以状态只从 `missing_candidate` 进入 `matched_name_unit_feature_review`，没有算作严格三字段通过。

`q31_r3_classfix_mirror` 前补做了两条诊断分支，但不作为新的 strict 基线：

- `q31_missingfb2_r1`：为 `24` 条 missing_candidate 增加 `finish_schedule/table_legend` fallback 图片，解决对象召回包 `missing_image_call_count=3` 的结构性问题；GLM-4V 返回 `6` 条证据，质量闸门过滤后仅 `1` 条可导入，三字段 strict 仍为 `39/127`，`missing_candidate=24`、`feature_review=56` 不变。
- `q31_ftr_qfix`：复用 r3 feature precision GLM 结果并加严质量过滤，拦截 `墙面装饰材料/地面装饰材料/吊顶材料` 等泛化面层词；三字段 strict 仍为 `39/127`，说明总览图上的泛化证据不能替代精确材料/节点证据。

本轮补强的规则：

- 对象召回 workbench 支持 `--fallback-image recommended_pass=图片路径`，只补 `image_path/source trace`，不把目标答案字段当证据。
- 质量闸门硬拦截 `墙面装饰材料`、`地面装饰材料`、`天花板装饰材料`、`吊顶材料`、`天花板`、`瓷砖`、`地板` 等泛化 item_hint。
- 质量闸门硬拦截 “通过测量/计算面积/数量/工程量” 的文本，避免在三字段阶段引入工程量推断。
- 相关 PDF 识图回归：`169 passed, 1 warning`；warning 为 `.pytest_cache` 权限，不影响功能。

在 `q31_r3_classfix_mirror` 基础上补做 r4 手工视觉证据后，当前最佳执行包更新为 `q31_r3_mirror_manual_r4`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_finish_manual_r2\eval\B2x_q31_finish_manual_r2_20260619_013000_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r4.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r3_mirror_manual_r4 `
  --source-name q31_r3_mirror_manual_r4 `
  --stem-prefix q31r4 `
  --timestamp 0410 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`41/127`，较严格基线 `27/127` 提升 `+14`。
- 系统候选：`253` 行。
- 剩余缺候选：`missing_candidate=17`。
- 特征复核：`feature_review=61`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=8`。
- 国标预览：`191/253` 已映射，`62` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r3_mirror_manual_r4\closed_loop_stage_report\q31r4_0410_closed_loop.xlsx`。

r4 新增 `圆形不锈钢隔断`、`金属线条`、`定制成品装饰隔断` 三条可见 `MT02/MT01` 手工视觉证据。其中 `圆形不锈钢隔断` 已进入 `matched_three_fields`，`定制成品装饰隔断` 进入 `matched_name_unit_feature_review`；两条 `金属线条` 仍为 `missing_candidate`，说明候选生成仍缺能区分 `60mm` 与 `10mm` 线条的独立项目证据。

当前剩余 `17` 条 missing 为：`零星砌筑`、`防水保护层`、`人造石挡水条`、`圆形灯槽`、`白色防潮无机涂料`、`黑色防潮无机涂料`、`砖砌隔墙`、`陶粒回填`、`瓷砖包柱`、`人造石窗台石PM-01`、`隔断底座（卡座区）`、`钢化玻璃造型柱（卡座区）`、两条 `金属线条`、`白色无机涂料`、`开荒精保洁`、`材料二次运输`。下一轮应优先找材料做法表、节点大样和立面局部中的精确规格，不能用泛化材料名或工程量推断放行。

随后补做 `MT01` 装饰线条分类保护，当前最佳执行包更新为 `q31_r2_r3_r4_linefix_full`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_top5_finish_manual_r1\eval\B2x_q31_top5_finish_manual_r1_20260619_011700_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r4.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_linefix_full `
  --source-name q31_r2_r3_r4_linefix_full `
  --stem-prefix q31r234lf `
  --timestamp 0525 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`43/127`，较严格基线 `27/127` 提升 `+16`。
- 系统候选：`253` 行。
- 剩余缺候选：`missing_candidate=15`。
- 特征复核：`feature_review=61`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=8`。
- 国标预览：`187/253` 已映射，`66` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_linefix_full\closed_loop_stage_report\q31r234lf_0525_closed_loop.xlsx`。

这轮不是降低验收门槛，而是修复候选生成口径：`MT01` 在电气图纸中可表示电气配管，但在 03 装饰立面中是黑色拉丝不锈钢材料编号。新增保护后，`金属线条MT-01` 不再生成 `电气配管 MT01`，两条 `金属线条` 均进入 `matched_three_fields`。国标映射数下降是合理结果：错误的电气配管映射被移除，后续需要补正确的装饰线条标准映射。

当前剩余 `15` 条 missing 为：`零星砌筑`、`防水保护层`、`人造石挡水条`、`圆形灯槽`、`白色防潮无机涂料`、`黑色防潮无机涂料`、`砖砌隔墙`、`陶粒回填`、`瓷砖包柱`、`人造石窗台石PM-01`、`隔断底座（卡座区）`、`钢化玻璃造型柱（卡座区）`、`白色无机涂料`、`开荒精保洁`、`材料二次运输`。下一轮优先级：先找 03 图纸材料做法表/节点大样里能支撑基层、保护层、挡水条、灯槽、底座和玻璃造型柱的精确证据；措施项若图纸无明示，应作为清单口径差异单独处理，不能用图纸泛化文字强行放行。

随后补做顶面防潮无机涂料口径修正，当前最佳执行包更新为 `q31_r2_r3_r4_linefix_coating_stdfix`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_top5_finish_manual_r1\eval\B2x_q31_top5_finish_manual_r1_20260619_011700_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r4.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_linefix_coating_stdfix `
  --source-name q31_r2_r3_r4_linefix_coating_stdfix `
  --stem-prefix q31r234coatstd `
  --timestamp 0615 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`44/127`，较严格基线 `27/127` 提升 `+17`。
- 系统候选：`253` 行。
- 剩余缺候选：`missing_candidate=14`。
- 特征复核：`feature_review=61`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=8`。
- 国标预览：`187/253` 已映射，`66` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_linefix_coating_stdfix\closed_loop_stage_report\q31r234coatstd_0615_closed_loop.xlsx`。

这轮补强两件事：一是把 `防潮无机涂料饰面` 从普通吊顶基层中拆出来，项目名按清单候选生成为 `防潮无机涂料`；二是把其国标映射从 `011302001 平面吊顶 | 天棚` 纠正为 `011404002 天棚喷刷涂料`。`白色防潮无机涂料` 已进入 strict pass；`黑色防潮无机涂料` 仍为 `missing_candidate`，因为当前只有一条泛化防潮无机涂料证据，不能一证两用去同时覆盖白色和黑色变体。

当前剩余 `14` 条 missing 为：`零星砌筑`、`防水保护层`、`人造石挡水条`、`圆形灯槽`、`黑色防潮无机涂料`、`砖砌隔墙`、`陶粒回填`、`瓷砖包柱`、`人造石窗台石PM-01`、`隔断底座（卡座区）`、`钢化玻璃造型柱（卡座区）`、`白色无机涂料`、`开荒精保洁`、`材料二次运输`。下一轮应优先从 03 图纸高 DPI 局部裁剪中找灯槽、隔断底座、玻璃造型柱、包柱和黑色顶面涂料证据；`开荒精保洁/材料二次运输` 若图纸不明示，应保留为措施项清单口径差异，不用泛化图纸文字强行通过。

继续补做 r5 手工视觉证据后，当前最佳执行包更新为 `q31_r2_r3_r4_r5_objectfix`：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_top5_finish_manual_r1\eval\B2x_q31_top5_finish_manual_r1_20260619_011700_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r4.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r5.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_objectfix `
  --source-name q31_r2_r3_r4_r5_objectfix `
  --stem-prefix q31r2345obj `
  --timestamp 0640 `
  --quality-filter `
  --quality-include-review `
  --require-importable
```

关键结果：

- 三字段严格通过：`45/127`，较严格基线 `27/127` 提升 `+18`。
- 系统候选：`255` 行。
- 剩余缺候选：`missing_candidate=12`。
- 特征复核：`feature_review=62`。
- 单位冲突：`unit_conflict=0`。
- 弱匹配：`weak_match=8`。
- 国标预览：`188/255` 已映射，`67` 未映射。
- 工程量：继续 `blocked_until_three_field_gate_passed`。
- 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_objectfix\closed_loop_stage_report\q31r2345obj_0640_closed_loop.xlsx`。

r5 只导入两条高 DPI 可见对象证据：`圆形灯槽` 和 `瓷砖包柱 CT04`。结果中 `圆形灯槽` 进入 `matched_three_fields`；`瓷砖包柱` 从 `missing_candidate` 进入 `matched_name_unit_feature_review`，原因是图纸可见 CT04 墙面砖/包柱区域，但木方龙骨、15厘阻燃板基层等完整做法仍需要节点大样或材料做法表补证。当前仍不允许用工程量阶段补洞，工程量继续锁定。

当前剩余 `12` 条 missing 为：`零星砌筑`、`防水保护层`、`人造石挡水条`、`黑色防潮无机涂料`、`砖砌隔墙`、`陶粒回填`、`人造石窗台石PM-01`、`隔断底座（卡座区）`、`钢化玻璃造型柱（卡座区）`、`白色无机涂料`、`开荒精保洁`、`材料二次运输`。下一轮应优先寻找地面/墙体节点和材料做法表；措施项若无图纸明示，保留为清单口径差异单独验收。

同轮为剩余 12 条生成了带 fallback 图片的工作台和答案盲审采集包：

- 工作台：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r5_remaining12_workbench\q31r5_remaining12_workbench.xlsx`，`image_link_count=12`、`missing_image_count=0`。
- 采集包：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r5_remaining12_capture_pack\q31r5_remaining12_capture.xlsx`，`capture_call_count=2`、`target_fields_in_prompt=false`。
- Dry-run：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r5_remaining12_capture_dry_run\q31r5_remaining12_capture_dry.xlsx`，2 个调用均为 planned。
- GLM 执行：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r5_remaining12_capture_execute\q31r5_remaining12_capture_execute.xlsx`，2 个调用中 1 个成功、1 个错误，产出 2 条证据候选。
- 质量结论：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r5_remaining12_capture_execute_quality\q31r5_remaining12_execute_quality.xlsx`，2 条均因 `generic_or_weak_item_hint / generic_section_item_hint` 被拒绝，未进入回灌。

这次 GLM 结果只返回 `墙面装饰材料/地面装饰材料` 一类泛化证据，不能支撑剩余清单项。后续应改为更小的节点/材料表局部图，或由人工从图纸可见标注填写 `evidence_item_hint/spec_or_method/suggested_unit/text`，不能把泛化大类作为候选项目。

## 三字段缺陷分流修复包

当 `missing_candidate` 已明显下降但 `feature_review/unit_conflict` 仍然存在时，不应继续盲目增加 GLM 批次。应先基于 `three_field_review.json` 生成缺陷分流修复包：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_three_field_defect_router.py `
  --three-field-review-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev_1_31_guard\three_field_review\BIZ2xq_qrev31g_three_field_review.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\qrev_1_31_guard\three_field_defect_router `
  --timestamp qrev31g
```

外部回灌一键流水线已自动生成 `three_field_defect_router/`。修复包把失败行分为：

- `object_evidence_recall`：缺真实对象证据，进入对象召回或答案盲审 GLM。
- `feature_enrichment`：项目名称和单位基本可用，但项目特征不足，需要补材料编号、规格、部位、做法和报价边界。
- `split_variant_review`：候选合并了平级/造型、规格、部位、单开/双开等变体，需要拆分。
- `unit_rule_review`：疑似同一对象但单位口径不一致，需梳理单位规则或标准库单位，不自动通过。

## 输出

一键脚本会输出：

- `template_status/`：外部证据或对象包填写状态
- `evidence_quality/`：外部证据质量评分与过滤报告；仅启用 `--quality-filter` 时生成
- `import/`：导入校验与标准化 `evidence_rows`
- `eval/`：回灌 V2 后的指标差异和增强版 V2 清单
- `three_field_review/`：人工验收用三字段 Excel
- `three_field_defect_router/`：三字段失败行分流修复包
- `object_recall_pack/`：下一轮缺失候选对象补召回包
- `object_recall_workbench/`：下一轮人工/外部识图填证据工作台
- `object_recall_capture_pack/`：答案盲审识图任务包与空白证据模板
- `feature_precision_capture_pack_strict/`：精确规格/材质补召回任务包与空白证据模板
- `three_field_gate/`：是否允许进入工程量阶段的质量门禁
- `standard_bill_preview/`：国标清单格式预览
- `quantity_stage_placeholder/`：工程量阶段占位文件，当前必须保持锁定
- `closed_loop_stage_report/`：七阶段状态总览，包含当前卡点、下一步和所有关键产物路径

当前真实项目基线 `20260618_auto_workbench`：

- 三字段严格通过：`19/127`
- 系统候选：`92`
- 待补真实图纸证据：`missing_candidate=69`
- 对象工作台：`image_link_count=69`、`importable_row_count=0`、`answer_only_count=69`
- 工程量：`can_enable_quantity=false`，继续锁定

当前组合预填分支 `ext_combined_prefill / comb1`：

- 合并证据输入：`177` 行，其中 `95` 行可导入
- 三字段严格通过：`21/127`
- 系统候选：`95`
- 待补真实图纸证据：`missing_candidate=67`
- 工程量：`can_enable_quantity=false`，继续锁定

当前答案盲审采集包分支 `ext_combined_capture / comb2`：

- 合并证据输入：`177` 行，其中 `95` 行可导入
- 三字段严格通过：`21/127`
- 待补真实图纸证据：`missing_candidate=67`
- 对象工作台：`67` 条缺项，`image_link_count=67`
- 答案盲审采集包：`31` 个识图任务，`target_fields_in_prompt=false`
- 答案盲审执行器 dry-run：`31` 个计划调用，`missing_image_call_count=0`，`target_fields_sent_to_model=false`
- 工程量：`can_enable_quantity=false`，继续锁定

当前答案盲审执行分支 `ext_combined_capture_v2_batch_1_20`：

- 已执行答案盲审识图任务：`1-20`，外部 GLM 返回证据共 `35` 条
- 合并可导入证据：`141` 行，`unassigned_evidence_count=0`
- 三字段严格通过：`24/127`
- 系统候选：`130`
- 待补真实图纸证据：`missing_candidate=58`
- 需复核特征：`feature_review=43`
- 单位冲突：`unit_conflict=2`
- 弱匹配：`weak_match=0`
- 国标预览：`130` 行，其中 `96` 行已映射、`34` 行未映射
- 工程量：`can_enable_quantity=false`，继续锁定

当前质量闸门分支 `qrev_1_20_v2`：

- 质量输入：`223` 行，其中原始可导入 `141` 行，答案参考列 `82` 行不作为证据
- 质量评分：`accepted=106`、`review=32`、`rejected=85`
- 采用 `--quality-filter --quality-include-review` 后实际导入：`138` 行
- 三字段严格通过：`24/127`
- 待补真实图纸证据：`missing_candidate=58`
- 需复核特征：`feature_review=43`
- 单位冲突：`unit_conflict=2`
- 工程量：`can_enable_quantity=false`，继续锁定

当前全批次质量闸门 + 对象类别保护分支 `qrev_1_31_guard`：

- 答案盲审 GLM 已执行任务：`1-31`，其中 `21-31` 新增 11 次调用、20 条证据
- 质量输入：`243` 行，其中原始可导入 `161` 行，答案参考列 `82` 行不作为证据
- 质量评分：`accepted=120`、`review=38`、`rejected=85`
- 采用 `--quality-filter --quality-include-review` 后实际导入：`158` 行
- 三字段严格通过：`25/127`
- 待补真实图纸证据：`missing_candidate=51`
- 需复核特征：`feature_review=47`
- 单位冲突：`unit_conflict=4`
- 弱匹配：`weak_match=0`
- 国标预览：`146` 行，其中 `106` 行已映射、`40` 行未映射
- 工程量：`can_enable_quantity=false`，继续锁定

`qrev_1_31_guard` 比未加对象类别保护的 `qrev_1_31` 更适合作为下一步基线：它会阻止“水表匹配阀门”“马桶匹配地漏”“台盆匹配龙头”等跨对象误配，让剩余缺口更真实。下一步不建议继续盲目增加 GLM 批次，而应分流处理 `missing_candidate`、`feature_review` 和 `unit_conflict`。

当前带自动缺陷分流的基线 `qrev_1_31_guard_router`：

- 三字段严格通过：`25/127`
- 缺陷分流总数：`102`
- `object_evidence_recall=51`
- `feature_enrichment=33`
- `split_variant_review=14`
- `unit_rule_review=4`
- P1 修复任务：`25`
- 工程量：`can_enable_quantity=false`，继续锁定

当前电热水器对象保护复跑基线 `qrev_1_31_guard_router_wh`：

- 三字段严格通过：`26/127`
- 缺陷分流总数：`101`
- `object_evidence_recall=51`
- `feature_enrichment=33`
- `split_variant_review=14`
- `unit_rule_review=3`
- P1 修复任务：`24`
- 对象分类新增稳定识别：`water_heater=1`
- 国标预览：`146` 行，其中 `106` 行已映射、`40` 行未映射
- 对象工作台：`51` 条缺项，`image_link_count=50`、`missing_image_count=1`
- 下一轮答案盲审任务：`30` 个，`target_fields_in_prompt=false`
- 工程量：`can_enable_quantity=false`，继续锁定

`qrev_1_31_guard_router_wh` 是当前推荐基线。它在 `qrev_1_31_guard_router` 基础上补充了电热水器对象类别保护，避免“电热水器供货及安装”被“水表供货及安装”一类给排水对象误吸收；因此单位冲突由 `4` 降为 `3`，剩余问题更接近真实缺证据、缺特征或单位口径问题。

当前第二轮对象补召回基线 `qrev_1_31_guard_router_wh_r2`：

- 在 `qrev_1_31_guard_router_wh` 后继续执行两轮答案盲审对象召回：
  - `BIZ2x_qrev31wh_object_capture_execute_1_30.json`：30 次计划调用，29 次成功、1 次缺图跳过，新增 `52` 条证据
  - `BIZ2x_qrev31whr1i_object_capture_execute_1_30.json`：30 次调用全部成功，新增 `41` 条证据
- 对象工作台图片兜底已修复：`image_link_count=46/46`、`missing_image_count=0`
- 质量评分：`accepted=197`、`review=54`、`rejected=85`
- 实际回灌证据：`251` 行
- 三字段严格通过：`30/127`
- 系统候选：`205`
- 缺候选：`missing_candidate=46`
- 需复核特征：`feature_review=48`
- 单位冲突：`unit_conflict=3`
- 缺陷分流：`object_evidence_recall=46`、`feature_enrichment=35`、`split_variant_review=13`、`unit_rule_review=3`
- 国标预览：`205` 行，其中 `148` 行已映射、`57` 行未映射
- 工程量：`can_enable_quantity=false`，继续锁定

`qrev_1_31_guard_router_wh_r2` 是当前最新基线。继续盲目增加同类 GLM 批次的边际收益已经下降：`missing_candidate` 暂停在 `46`，而候选总数继续增长到 `205`。下一步应优先处理 `feature_enrichment`、`split_variant_review` 和 `unit_rule_review`，同时针对剩余 `object_evidence_recall` 检查是否是图纸确实无证据、证据定位错误或标准/人工答案口径差异。

当前单位口径治理基线 `qrev_1_31_guard_router_wh_r2_unitfix`：

- 在 `qrev_1_31_guard_router_wh_r2` 基础上，把候选输出单位规则调整到当前人工清单验收口径：
  - 配电箱：`套`
  - 马桶、小便器、台盆、淋浴花洒、冷热水龙头、龙头、浴缸、洗涤盆：`套`
  - 阀门、地漏、水表、厕纸架、梳妆镜、洁具五金：`个`
- 三字段严格通过：`32/127`
- 系统候选：`205`
- 缺候选：`missing_candidate=46`
- 需复核特征：`feature_review=49`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=0`
- 缺陷分流：`object_evidence_recall=46`、`feature_enrichment=36`、`split_variant_review=13`
- 国标预览：`205` 行，其中 `148` 行已映射、`57` 行未映射
- 工程量：`can_enable_quantity=false`，继续锁定

`qrev_1_31_guard_router_wh_r2_unitfix` 是单位口径治理基线。单位门禁已清零，后续三字段突破点只剩“缺真实对象证据”和“项目特征/变体拆分”。其中 `feature_enrichment` 不应再依靠泛化 GLM 召回堆候选，而应按材料编号、规格、部位、平级/造型、做法和报价边界补结构化特征证据。

当前严格规格门禁基线 `qrev_1_31_guard_router_wh_r2_unitfix_select_strict`：

- 在 `qrev_1_31_guard_router_wh_r2_unitfix` 基础上补强三字段匹配门禁：
  - 同一状态候选优先选择明确命中规格/材质 token 的候选，例如 `DN15` 候选优先于泛化 `PPR` 候选。
  - `DN/De/SC/MT/JDG/WDZC/材质` 等关键规格缺失或冲突时，不允许算作三字段通过，只能进入 `feature_review`。
  - 拆除类跨对象弱匹配继续阻断，例如 `地砖拆除` 不可被 `拆除门` 吸收。
- 回归测试：PDF 相关与模型网关聚焦回归 `147 passed, 1 warning`；warning 为 `.pytest_cache` 权限，不影响功能。
- 三字段严格通过：`27/127`
- 系统候选：`205`
- 缺候选：`missing_candidate=43`
- 需复核特征：`feature_review=55`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=2`
- 缺陷分流：`object_evidence_recall=43`、`feature_enrichment=40`、`split_variant_review=15`、`object_match_review=2`
- 对象补召回工作台：`image_link_count=43`、`missing_image_count=0`
- 下一轮答案盲审任务：`15` 个，`target_fields_in_prompt=false`
- 精确规格补召回包：`selected_defect_count=38`、`capture_call_count=13`、`image_exists_call_count=13`、`target_fields_in_prompt=false`
- 精确规格补召回产物：`AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_capture_pack_strict\BIZ2xq_qrev31whr2uss_feature_precision.xlsx`
- 国标预览：`205` 行，其中 `148` 行已映射、`57` 行未映射
- 工程量：`can_enable_quantity=false`，继续锁定

`qrev_1_31_guard_router_wh_r2_unitfix_select_strict` 是当前最新推荐基线。它把上一版中 5 条“规格/材质未真实识别但被泛化特征误放行”的候选打回复核，因此 `matched_three_fields_count` 从 `32` 修正为 `27`。这不是放弃进度，而是防止工程量阶段建立在假三字段通过上。下一轮升级应优先补视觉证据中的精确规格：`SC40/SC50/MT20/MT25`、`WDZC-YJY-*`、`SUS304 DN40/DN20`、`柔性铸铁 De110/De63` 等。

当前门禁规则为严格验收：三字段通过率必须达到 100%，且 `missing_candidate/unit_conflict/feature_review/weak_match_review` 必须为 0。未通过时，`can_enable_quantity=false`，工程量继续保持 `deferred_until_three_fields_accepted`。

## 2026-06-19 可信口径与 fallback capture 包

本轮继续复核 03 装饰高 DPI 裁图后，发现 `remaining12_local_visual_review` 的 4 条证据均属于“只能看到相关构造/材料，不能直接证明完整项目特征”的人工判断行。质量门已收紧：

- `needs_manual_review=true` 的具体证据降级为 `review`，只有在 `--quality-include-review` 下才可导入。
- 含 `未直接可见`、`不能区分`、`无法确定`、`not directly visible`、`cannot distinguish` 等不确定表达的行，标记 `uncertain_or_incomplete_evidence`，即使启用 `--quality-include-review` 也不导入。
- `m³/m3/m^3/立方米` 保留为合法单位，避免砖砌/回填类真实证据被误判为无效单位。
- `窗台石` 支持使用明确外部建议单位，避免被门窗关键词兜底规则强行改成 `m`。

当前可信口径执行包：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_pdf_external_recall_acceptance_pipeline.py `
  --base-v2-json AI_Middle_Office\outputs\pdf_v2_takeoff\qrev31_top5_finish_manual_r1\eval\B2x_q31_top5_finish_manual_r1_20260619_011700_eval_augmented_v2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r2.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r3.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r4.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\feature_precision_03_finish_zoom\highdpi_2000\subsheet_content_crops\B2x_03_visible_finish_manual_r5.json `
  --external-results AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\manual_zoom_r6\B2x_03_remaining12_local_manual_r6.json `
  --output-dir AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_tight_quality_fallback `
  --source-name q31_r2_r3_r4_r5_r6_tight_quality_fallback `
  --stem-prefix q31r23456tightfb `
  --timestamp 20260619_0910 `
  --quality-filter `
  --quality-include-review `
  --require-importable `
  --fallback-image finish_schedule=AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\high_crop_contact.png `
  --fallback-image table_legend=AI_Middle_Office\outputs\pdf_v2_takeoff\all_pdf_key_blocks_2000dpi\01_material_table_2000dpi.png
```

关键结果：

- 三字段严格通过：`45/127`
- 系统候选：`255`
- 缺候选：`missing_candidate=12`
- 需复核特征：`feature_review=62`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=8`
- 外部证据质量：输入 `23` 行，`review=23`；其中 `19` 行可导入，`4` 条不确定证据被挡住
- 对象补召回工作台：`image_link_count=12`、`missing_image_count=0`
- 答案盲审 capture 包：`capture_call_count=2`、`image_exists_call_count=2`、`target_fields_in_prompt=false`
- 国标预览：`255` 行，其中 `188` 行已映射、`67` 行未映射
- 工程量：`can_enable_quantity=false`，继续锁定

仍缺真实候选的 12 条：

- `零星砌筑`：`m³`，过厅砖砌地台，抬高 240mm
- `防水保护层`：`㎡`，10 厚水泥砂浆防水保护层
- `人造石挡水条`：`m`，60 宽人造石挡水条
- `黑色防潮无机涂料`：`㎡`，基层处理、贴绷带及点防锈漆，防水腻子，面油黑色防潮无机涂料三遍
- `砖砌隔墙`：`m³`，100mm 宽，新建蒸压加气砼砌块墙
- `陶粒回填`：`m³`，隔墙陶粒回填
- `人造石窗台石PM-01`：`㎡`，专用粘结剂粘贴，白色人造石
- `隔断底座（卡座区）`：`m`，钢通结构+15 厚阻燃夹板基层，古堡灰大理石 ST-1 底座，1470*240*200mm
- `钢化玻璃造型柱（卡座区）`：`套`，10mm 钢化玻璃造型柱，200*200*2366mm，4 套
- `白色无机涂料`：`㎡`，基层处理、贴绷带及点防锈漆，防水腻子，面油白色无机涂料三遍
- `开荒精保洁`：`㎡`
- `材料二次运输`：`㎡`

本轮新增可执行下一步产物：

- 三字段验收：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_tight_quality_fallback\three_field_review\q31r23456tightfb_20260619_0910_three_field_review.xlsx`
- 质量门：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_tight_quality_fallback\evidence_quality\q31r23456tightfb_20260619_0910_evidence_quality.xlsx`
- 对象工作台：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_tight_quality_fallback\object_recall_workbench\q31r23456tightfb_20260619_0910_object_workbench.xlsx`
- 下一轮答案盲审包：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_tight_quality_fallback\object_recall_capture_pack\q31r23456tightfb_20260619_0910_object_capture.xlsx`
- 答案盲审包 dry-run：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_tight_quality_fallback\object_recall_capture_run\q31r23456tightfb_object_capture_dryrun.xlsx`，`capture_call_count=2`、`missing_image_call_count=0`、`target_fields_sent_to_model=false`
- 闭环阶段报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_tight_quality_fallback\closed_loop_stage_report\q31r23456tightfb_20260619_0910_closed_loop.xlsx`

结论：当前不是工程量阶段，也不能靠降低三字段阈值推进。下一步必须用 `object_recall_capture_pack` 的两条图片任务，继续获取能直接证明上述 12 条和 62 条 feature_review 的真实图纸证据；若没有更清晰 CAD/可 OCR 图层或可合规调用外部视觉模型，严格三字段 gate 无法闭合。

## 2026-06-19 task-image 精确裁图回灌包

本轮没有降低三字段 gate，也没有把人工答案字段当作证据。新增能力是给对象回灌工作台和完整 acceptance pipeline 增加 `task_no=图片路径` 的精确图片覆盖入口：

- `biz2x_pdf_object_recall_workbench.py --task-image 1=...png`
- `biz2x_pdf_external_recall_acceptance_pipeline.py --task-image 1=...png`
- 优先级：`task_image` 高于 `recall_plan_target/evidence/source_page` 和 `fallback_image`
- capture prompt 仍保持 answer-blind：`target_fields_in_prompt=false`
- 工程量仍保持锁定：`can_enable_quantity=false`

本轮生成的局部裁图目录：

- `AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\local_zoom_r7`

当前 task-image 流水线产物：

- 输出目录：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_task_image_r7`
- 三字段验收：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_task_image_r7\three_field_review\q31r23456taskimg_20260619_1030_three_field_review.xlsx`
- gate：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_task_image_r7\three_field_gate\q31r23456taskimg_20260619_1030_three_field_gate.json`
- 精确裁图工作台：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_task_image_r7\object_recall_workbench\q31r23456taskimg_20260619_1030_object_workbench.xlsx`
- 精确裁图 capture 包：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_task_image_r7\object_recall_capture_pack\q31r23456taskimg_20260619_1030_object_capture.xlsx`
- dry-run：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_task_image_r7\object_recall_capture_run\q31r23456taskimg_object_capture_dryrun.xlsx`
- 闭环阶段报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\q31_r2_r3_r4_r5_r6_task_image_r7\closed_loop_stage_report\q31r23456taskimg_20260619_1030_closed_loop.xlsx`

关键结果：

- 三字段严格通过：`45/127`
- 缺候选：`missing_candidate=12`
- 特征待复核：`feature_review=62`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=8`
- object workbench：`image_link_count=12`、`missing_image_count=0`
- task-image 命中：`task_image:1` 至 `task_image:10` 各 1 条；`fallback_image:table_legend` 2 条
- capture 包：`capture_call_count=7`、`image_exists_call_count=7`、`missing_image_call_count=0`
- capture dry-run：`target_fields_sent_to_model=false`、`answer_columns_count_as_evidence=false`
- 工程量：`quantity_status=blocked_until_three_field_gate_passed`

结论：本轮把下一次视觉/人工回灌从“2 张泛化 fallback 图”细化为“7 个更精确识图任务”，但没有新增可直接导入的真实证据；因此 gate 指标不变，工程量仍不可启动。下一步必须对这 7 个 capture 任务进行真实视觉识别或人工证据回填，且回填内容必须能直接证明图纸可见对象、做法/材料、单位口径，不能写“未直接可见”“不能区分”等不确定表达。
## 2026-06-19 r10c 本地 UTF-8 视觉补证 + 匹配器修复

本轮继续严格遵守三字段 gate：只验收 `项目名称 / 项目特征 / 单位`，不启用工程量。没有把人工答案字段当作图纸证据，也没有调用外部 GLM；仅使用本地已生成的精确裁图和人工可审计视觉判断。

新增修复：

- `drawing_three_field_acceptance.py`：候选特征完整包含人工特征时，忽略系统模板噪声、`1、/2、` 清单序号噪声，避免真实补证行被误判为 `feature_review`。
- `drawing_three_field_acceptance.py`：项目名称匹配忽略括号内部位，避免 `隔断底座（卡座区）` 与 `隔断底座 ST-1` 因部位/材料编号被降为弱匹配。
- `drawing_pdf_v2_takeoff.py`：补充零星砌筑、防水保护层、人造石挡水条、砖砌隔墙、人造石窗台石、隔断底座、钢化玻璃造型柱、黑/白无机涂料等候选列项规则。
- `object_recall_workbench` / `external_recall_acceptance_pipeline`：支持 `--task-image task_no=path` 精确裁图回灌。

r10c 关键产物：

- UTF-8 本地补证包：`AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\local_visual_r10\local_visual_r10_conservative_evidence_utf8.json`
- r10c 输出目录：`AI_Middle_Office\outputs\pdf_v2_takeoff\r10c_lv_utf8`
- r10c gate：`AI_Middle_Office\outputs\pdf_v2_takeoff\r10c_lv_utf8\three_field_gate\r10c_1150_three_field_gate.json`
- r10c 三字段验收 Excel：`AI_Middle_Office\outputs\pdf_v2_takeoff\r10c_lv_utf8\three_field_review\r10c_1150_three_field_review.xlsx`
- r10c 闭环报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\r10c_lv_utf8\closed_loop_stage_report\r10c_1150_closed_loop.xlsx`
- 剩余 6 项带图工作台：`AI_Middle_Office\outputs\pdf_v2_takeoff\r10c_lv_utf8_taskimg\object_recall_workbench\r10c_taskimg_workbench.xlsx`
- 剩余 6 项 answer-blind capture 包：`AI_Middle_Office\outputs\pdf_v2_takeoff\r10c_lv_utf8_taskimg\object_recall_capture_pack\r10c_taskimg_capture.xlsx`

r10c 指标：

- 人工答案行：`127`
- 系统候选行：`261`
- 三字段严格通过：`51/127`
- 通过率：`0.4016`
- 缺候选：`missing_candidate=6`
- 特征复核：`feature_review=63`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=7`
- 标准清单预览：`261` 行，其中 `193` 行已映射，`68` 行未映射
- 工程量：`can_enable_quantity=false`，`quantity_status=blocked_until_three_field_gate_passed`

已从 12 个缺候选推进为 6 个缺候选。剩余 6 个为：

- `零星砌筑`：可见砖砌构造和尺寸线，但黄色做法文字不足以直接证明“过厅砖砌地台，抬高240mm”。
- `防水保护层`：可见节点构造，但“10厚水泥砂浆防水保护层”文字不够清晰，质量门标记为 `uncertain_or_incomplete_evidence`。
- `陶粒回填`：当前本地图纸裁图无可靠证据。
- `钢化玻璃造型柱（卡座区）`：卡座立面有图形线索，但 `10mm / 200*200*2366mm / 4套` 关键文字不清晰。
- `开荒精保洁`：更像商务措施/清单规则项，当前 PDF 裁图无直接证据。
- `材料二次运输`：更像商务措施/清单规则项，当前 PDF 裁图无直接证据。

结论：r10c 已把本地可审计视觉证据能推进的部分推进完，但 gate 仍失败，不能进入工程量阶段。下一步必须补足剩余 6 个缺候选和 63 个 feature_review 的直接证据；若继续仅靠当前本地 OCR/裁图，三字段闭环无法达到 100%。

## 2026-06-19 r11 业务措施项规则补证

本轮继续只验收三字段，不启用工程量。r11 将 `开荒精保洁`、`材料二次运输` 从“图纸构件缺候选”改为显式的 `business_measure_rule` 来源：这两项不是 PDF 中稳定可见的构件，而是项目整体交付/施工组织措施项，应作为业务规则候选进入三字段验收。系统没有把它们伪装为视觉识别结果。

新增/修复：

- 新增措施项补证输入：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure\input\business_measure_rules_r11.json`
- `drawing_pdf_v2_takeoff.py` 保留 `source_kind / vision_pass / evidence_role` 到归一化证据组。
- 业务措施项特征展示为 `规则依据 / 措施范围`，不再显示为 `图纸证据 / 报价范围`。
- 业务措施项三字段候选保留，但不做低分搜索式国标映射，避免 `开荒精保洁/材料二次运输` 误映射到 `干燥机`。

r11 最新产物：

- 输出目录：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure`
- gate：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure\three_field_gate\r11m_1230_three_field_gate.json`
- 三字段验收 Excel：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure\three_field_review\r11m_1230_three_field_review.xlsx`
- 标准清单预览：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure\standard_bill_preview\r11m_1230_stdbill.xlsx`
- 剩余 4 项带图工作台：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure\object_recall_workbench\r11m_1230_object_workbench.xlsx`
- 剩余 4 项 answer-blind capture 包：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure\object_recall_capture_pack\r11m_1230_object_capture.xlsx`
- 闭环阶段报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\r11_measure\closed_loop_stage_report\r11m_1230_closed_loop.xlsx`

r11 指标：

- 人工答案行：`127`
- 系统候选行：`263`
- 三字段严格通过：`53/127`
- 通过率：`0.4173`
- 缺候选：`missing_candidate=4`
- 特征复核：`feature_review=63`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=7`
- 标准清单预览：`263` 行，其中 `193` 行已映射、`70` 行未映射
- 对象工作台：`object_recall_task_count=4`，`image_link_count=3`，`missing_image_count=1`
- 工程量：`can_enable_quantity=false`，`quantity_status=blocked_until_three_field_gate_passed`

剩余 4 个缺候选：

- `零星砌筑`：可见局部砖砌构造和尺寸线，但不足以完整证明“过厅砖砌地台，抬高240mm”。
- `防水保护层`：可见节点构造，但“10厚水泥砂浆防水保护层”文字证据不完整。
- `陶粒回填`：当前本地图纸裁图无可靠证据，且 r11 工作台仍缺图。
- `钢化玻璃造型柱（卡座区）`：卡座立面有图形/尺寸线索，但 `10mm / 200*200*2366mm / 4套` 关键文字证据不完整。

feature_review 复核结论：`63` 条中多数是拆除对象、门尺寸、灯型/功率、线缆规格、平级/造型吊顶等关键细分差异，当前不能安全放行。严格 gate 仍失败，工程量功能继续锁定。

补充搜索：对 4 份 PDF 尝试文本层/原始字节关键词搜索，`零星砌筑`、`防水保护层`、`陶粒回填`、`钢化玻璃`、`造型柱`、`砖砌地台`、`水泥砂浆防水保护层` 均无可靠命中；pypdf 在大 CAD 页解压限制上失败，pdfplumber 超时，当前环境也没有 Poppler `pdftotext`。因此剩余 4 项不能靠现有本地文本层直接闭合。

## 2026-06-19 r12 增强 task-image 取证包

r12 没有降低三字段 gate，也没有把人工答案列当作证据。本轮只完成“剩余 4 个缺候选的增强图片取证入口”：为每个缺口补上明确的 task-image，使下一轮真实视觉识别或人工证据回填可以逐项处理。

增强裁图目录：

- `AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\r12_enhanced_crops`

r12 最新产物：

- 输出目录：`AI_Middle_Office\outputs\pdf_v2_takeoff\r12_enhanced_taskimg`
- gate：`AI_Middle_Office\outputs\pdf_v2_takeoff\r12_enhanced_taskimg\three_field_gate\r12e_1245_three_field_gate.json`
- 三字段验收 Excel：`AI_Middle_Office\outputs\pdf_v2_takeoff\r12_enhanced_taskimg\three_field_review\r12e_1245_three_field_review.xlsx`
- 剩余 4 项增强带图工作台：`AI_Middle_Office\outputs\pdf_v2_takeoff\r12_enhanced_taskimg\object_recall_workbench\r12e_1245_object_workbench.xlsx`
- 剩余 4 项 answer-blind capture 包：`AI_Middle_Office\outputs\pdf_v2_takeoff\r12_enhanced_taskimg\object_recall_capture_pack\r12e_1245_object_capture.xlsx`
- 标准清单预览：`AI_Middle_Office\outputs\pdf_v2_takeoff\r12_enhanced_taskimg\standard_bill_preview\r12e_1245_stdbill.xlsx`
- 闭环阶段报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\r12_enhanced_taskimg\closed_loop_stage_report\r12e_1245_closed_loop.xlsx`

r12 指标：

- 人工答案行：`127`
- 系统候选行：`263`
- 三字段严格通过：`53/127`
- 通过率：`0.4173`
- 缺候选：`missing_candidate=4`
- 特征复核：`feature_review=63`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=7`
- object workbench：`object_recall_task_count=4`，`image_link_count=4`，`missing_image_count=0`
- capture 包：`capture_call_count=4`，`image_exists_call_count=4`，`missing_image_call_count=0`
- capture prompt：`target_fields_in_prompt=false`，`answer_columns_count_as_evidence=false`
- 工程量：`can_enable_quantity=false`，`quantity_status=blocked_until_three_field_gate_passed`

剩余 4 个缺候选的增强图片任务：

- `零星砌筑`：`AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\r12_enhanced_crops\detail17_wall_section_full_x3.png`
- `防水保护层`：`AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\r12_enhanced_crops\detail17_left_labels_full_x3.png`
- `陶粒回填`：`AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\local_zoom_r7\mid3_cardseat_base_dimensions_x4.png`
- `钢化玻璃造型柱（卡座区）`：`AI_Middle_Office\outputs\pdf_v2_takeoff\source_pdf_high_crops\r12_enhanced_crops\cardseat_circle_and_red_labels_x3.png`

结论：r12 将 r11 的 `missing_image_count=1` 修正为 `0`，四个缺口现在都有可追溯图片任务；但没有产生可直接导入的真实证据行，因此 gate 指标保持 `53/127` 不变。当前仍处于三字段验收阶段，不能进入工程量阶段。

## 2026-06-19 r13 精确规格补召回包

r13 继续围绕第 2 阶段三字段验收推进，不启用工程量。本轮不是新增识图结论，而是把 r12 defect router 中可执行的 `feature_enrichment` / `split_variant_review` 缺陷转成有图、答案盲审的精确规格取证任务，重点覆盖电气管线规格、线缆型号、给排水管径/材质、装饰材料编号和拆分变体。

新增/修复：

- `drawing_pdf_feature_precision_capture_pack.py` 的图片解析器新增 `evidence_tiles -> image_root` 匹配能力。
- 当同一 tile 在多份 PDF 中重复出现时，优先使用 `candidate_source_files` 的 `01/02/03/04` 前缀约束，避免跨 PDF 误命中。
- 图片根目录扫描改为排序扫描，保证同样输入生成同样输出。
- 新增单元测试覆盖 `03 + p001_g03_r02_c03` 这类网格块图片解析，且继续验证 prompt 不包含人工答案字段。

r13 最新产物：

- 输出目录：`AI_Middle_Office\outputs\pdf_v2_takeoff\r13_feature_precision`
- 精确规格补召回包：`AI_Middle_Office\outputs\pdf_v2_takeoff\r13_feature_precision\feature_precision_capture_pack\r13fp_1315_feature_precision_capture.xlsx`
- 精确规格补召回 JSON：`AI_Middle_Office\outputs\pdf_v2_takeoff\r13_feature_precision\feature_precision_capture_pack\r13fp_1315_feature_precision_capture.json`
- 精确规格 dry-run：`AI_Middle_Office\outputs\pdf_v2_takeoff\r13_feature_precision\feature_precision_capture_run\r13fp_1315_feature_precision_dryrun.xlsx`
- 精确规格 dry-run JSON：`AI_Middle_Office\outputs\pdf_v2_takeoff\r13_feature_precision\feature_precision_capture_run\r13fp_1315_feature_precision_dryrun.json`

r13 取证包指标：

- defect router 来源缺陷：`74`
- 选中精确规格/拆分变体缺陷：`39`
- capture 调用：`15`
- 有图调用：`image_exists_call_count=15`
- 缺图调用：`missing_image_call_count=0`
- 覆盖路线：`feature_enrichment=33`，`split_variant_review=6`
- 覆盖对象：`electrical_mep=22`、`fixture_valve_schedule=7`、`finish_wall=4`、`finish_ceiling=3`、`door_window_demolition=2`、`water_heater=1`
- 缺口族：`material=17`、`mt=5`、`dn=5`、`wdzcbyj=3`、`wdzcyjy=2`、`sc=2`
- prompt：`target_fields_in_prompt=false`，`answer_columns_count_as_evidence=false`

r13 dry-run 指标：

- capture 调用：`15`
- 状态：`planned_dry_run=15`
- 缺图调用：`missing_image_call_count=0`
- 发送给模型的目标答案字段：`target_fields_sent_to_model=false`
- 证据行：`0`，因为本轮没有执行外部视觉模型，只生成可执行取证包

结论：r13 将 r12 中一批 `feature_review` 的后续动作从“笼统补特征证据”推进为“15 个可直接执行的有图视觉取证任务”。但它尚未产生可导入证据，因此三字段 gate 指标仍以 r12 为准：`53/127`、`missing_candidate=4`、`feature_review=63`、`weak_match=7`。工程量继续锁定。

## 2026-06-19 r14 空字段标签清洗复验

r14 针对三字段匹配器做了一个保守修复：人工特征中类似 `2、型号:`、`3、规格:` 这类没有实际值的空字段标签，不再参与特征相似度计算；但带有实际值的型号、规格、材质仍继续作为严格验收条件。

新增/修复：

- `drawing_three_field_acceptance.py` 新增空字段标签清洗，仅移除无值标签。
- 新增测试覆盖 `电热水器供货及安装` 中的空 `型号:` 标签可被忽略。
- 新增反向测试确认 `WDZC-BYJ-6` 这类有值规格不会被误删，仍需真实图纸证据。

r14 复验产物：

- 输出目录：`AI_Middle_Office\outputs\pdf_v2_takeoff\r14_matchfix`
- 三字段验收：`AI_Middle_Office\outputs\pdf_v2_takeoff\r14_matchfix\three_field_review\r14mf_1400_three_field_review.xlsx`
- gate：`AI_Middle_Office\outputs\pdf_v2_takeoff\r14_matchfix\three_field_gate\r14mf_1400_three_field_gate.json`
- defect router：`AI_Middle_Office\outputs\pdf_v2_takeoff\r14_matchfix\three_field_defect_router\r14mf_1400_defect_router.xlsx`
- 标准清单预览：`AI_Middle_Office\outputs\pdf_v2_takeoff\r14_matchfix\standard_bill_preview\r14mf_1400_stdbill.xlsx`
- 工程量占位：`AI_Middle_Office\outputs\pdf_v2_takeoff\r14_matchfix\quantity_stage_placeholder\r14mf_1400_qty.xlsx`
- 闭环阶段报告：`AI_Middle_Office\outputs\pdf_v2_takeoff\r14_matchfix\closed_loop_stage_report\r14mf_1400_closed_loop.xlsx`

r14 指标：

- 人工答案行：`127`
- 系统候选行：`263`
- 三字段严格通过：`53/127`
- 通过率：`0.4173`
- 缺候选：`missing_candidate=4`
- 特征复核：`feature_review=63`
- 单位冲突：`unit_conflict=0`
- 弱匹配：`weak_match=7`
- defect router：`feature_enrichment=48`、`split_variant_review=15`、`object_match_review=7`、`object_evidence_recall=4`
- 标准清单预览：`263` 行，其中 `193` 行已映射、`70` 行未映射，仍为 `review_only`
- 工程量：`blocked_count=263`，`quantity_status=blocked_until_three_field_gate_passed`

视觉复核补充：

- `detail17_left_labels_full_x3.png` 可见 `PM 01`、`CT 11`、`MT 01`、部分尺寸和砖墙构造，但没有清晰出现 `10厚水泥砂浆防水保护层`，不能作为 `防水保护层` 的严格三字段证据。
- `detail17_wall_section_full_x3.png` 可见砖墙构造、尺寸和剖面 17，但没有清晰出现 `过厅砖砌地台，抬高240mm`，不能作为 `零星砌筑` 的严格三字段证据。
- 当前环境无 `tesseract`，也未安装 `easyocr/paddleocr/pytesseract/keras_ocr/cnocr`；本轮没有调用外部视觉模型。

结论：r14 修复了一个真实的匹配清洗问题，但当前真实数据 gate 未改善，仍不能进入工程量阶段。下一步必须执行 r13 的 15 个有图答案盲审视觉任务，或提供更清晰 CAD/PDF 导出图层，用真实证据回灌后再复验。
