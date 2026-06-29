# BIZ-2x 图纸 OCR 证据柜路线

日期：2026-06-25

## 一、路线结论

当前主线从“先优化 OCR 前半段区域筛选”调整为：

```text
全图 64 倍 OCR 高召回证据库
→ 当前文字 + 周边文字上下文包
→ LLM 分类入柜
→ 材料/做法候选归并
→ 少量 VLM 复核疑难视觉关系
→ 输出业务审阅表
→ 再反推 OCR 提速策略
```

核心判断：

- 昨天的全图 64 倍 OCR 虽然慢、噪声多，但召回能力强，已经读出了大量材料、做法、拆除项、规格和工程量相关数字。
- 当前最危险的问题不是“噪声多”，而是前半段规则过早筛选后可能误杀有效材料信息。
- 因此第一优先级应是把全量 OCR 结果变成结构化证据柜，再从证据柜中筛出材料候选。
- 今天完成的中清文字区域发现、预算内调度、动态 scale、OCR 质量评分继续保留，但定位调整为后续性能优化工具，不再作为当前业务验收主线。

## 二、设计原则

1. 原始 OCR 永远保留

   `all_text_evidence_64.csv` 是原始证据库。任何文本，包括单字、数字、符号、低价值碎片，都不物理删除，只打分类标签。

2. 不在 OCR 前半段强过滤

   不再优先追求“少 OCR、少噪声”，而是先保召回。噪声交给后半段分类、归并和业务审阅处理。

3. 几何规则只负责找上下文

   程序可以根据坐标找到当前文字附近的文字，但不强行判断这些文字一定属于同一个材料项。

4. LLM 负责文本语义分类

   “当前文字 + 周边文字”交给 LLM 判断，分入材料、拆除、规格、图名、公司、轴号、噪声、不确定等柜子。

5. VLM 只做少量疑难复核

   只有涉及引线关系、表格结构、节点构造、构造层级、尺寸线和工程量区分等视觉关系时，才调用 VLM。

6. 业务员只看材料候选

   业务验收主产物不是 OCR crop 表，也不是 2624 条 OCR 原文，而是归并后的材料候选业务审阅表。

## 三、输入基线

当前已验证输入：

```text
outputs/biz2x_trial/full_text_ocr_64_snippets/20260624_005538_full_text_ocr64_snippets/outputs/all_text_evidence_64.csv
```

字段：

- `text_id`
- `source_file`
- `page`
- `text`
- `confidence`
- `bbox_ratio`
- `bbox_page_pt`
- `tile_id`
- `snippet_id`
- `image_path`

当前样本规模：

- OCR 证据：2624 条。
- 噪声：包含大量单数字、单字母、符号、轴号、重复尺寸和碎片文本。
- 有效信息：已包含大量材料、做法、拆除、规格和尺寸线索。

已观察到的有效 OCR 文本示例：

- 防水石膏板刷白色防潮无机涂料
- 拆除不锈钢玻璃地弹门，宽度2200，高度2400
- 拆除墙面300x600墙面砖
- 拆除天花800×600矿棉板及800x600灯盘
- 墙刷白色无机涂料三度+50mm黑色拉丝不锈钢踢脚线
- 600X1200白色墙面砖
- 10mm钢化磨砂玻璃
- 0.8厚铝扣板吊顶(600*600MM)
- 80*60不锈钢方通(壁厚1.5)

## 四、阶段 1：建立 OCR 原始证据库

目标：

把 `all_text_evidence_64.csv` 读取为统一的 OCR 原始证据结构。

要求：

- 原始 2624 条 OCR 证据全部保留。
- 每条证据保留坐标、置信度、来源页码、截图路径。
- 不删除任何 OCR 文本。
- 可以新增派生字段，但派生字段只辅助分类，不作为强过滤依据。

建议新增派生字段：

- `text_length`
- `has_chinese`
- `has_number`
- `has_dimension_pattern`
- `is_single_char`
- `page_zone`
- `nearby_text_ids`

输出：

```text
ocr_raw_evidence.jsonl
ocr_raw_evidence.csv
```

验收标准：

- 原始 OCR 条数不减少。
- 每条 OCR 都能追溯到截图。
- 单字、数字、符号仍保留，只等待后续分类。

### 2026-06-25 实现记录

已完成阶段 1 MVP，实现文件：

```text
AI_Middle_Office/app/services/drawing_ocr_raw_evidence.py
AI_Middle_Office/scripts/biz2x_ocr_raw_evidence_preview.py
AI_Middle_Office/tests/test_drawing_ocr_raw_evidence_biz2x.py
```

当前实现只做原始证据结构化，不做材料分类、不做噪声删除、不调用 LLM/VLM。

真实样本运行命令：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_raw_evidence_preview.py `
  --input-csv outputs\biz2x_trial\full_text_ocr_64_snippets\20260624_005538_full_text_ocr64_snippets\outputs\all_text_evidence_64.csv `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260625_stage1_raw_evidence
```

真实样本输出：

```text
outputs/biz2x_trial/ocr_cabinet/20260625_stage1_raw_evidence/ocr_raw_evidence.jsonl
outputs/biz2x_trial/ocr_cabinet/20260625_stage1_raw_evidence/ocr_raw_evidence.csv
outputs/biz2x_trial/ocr_cabinet/20260625_stage1_raw_evidence/ocr_raw_evidence_summary.json
```

真实样本结果：

- 原始 OCR 行数：2624。
- 输出证据行数：2624。
- 有文本行数：2624。
- bbox 存在行数：2624。
- 截图路径存在行数：2624。
- 截图文件实际存在行数：2624。
- 单字符证据：765。
- 含中文证据：1083。
- 含数字证据：1078。
- 命中规格/尺寸模式证据：85。
- `nearby_text_ids` 暂为空数组，留到阶段 2 构建上下文包时填充。

验证：

```text
AI_Middle_Office/tests/test_drawing_ocr_raw_evidence_biz2x.py: 1 passed
```

## 五、阶段 2：构建当前文字 + 周边文字上下文包

目标：

为每条 OCR 证据构建一个 LLM 可判断的上下文包。

上下文包示例：

```json
{
  "text_id": "T00858",
  "current_text": "墙刷白色无机涂料三度+50mm黑色拉丝不锈钢踢脚线",
  "page": 1,
  "confidence": 0.9998,
  "nearby_texts": [
    "轻钢龙骨石膏板隔墙刷白色无机涂料三度",
    "+50mm黑色拉丝不锈钢踢脚线",
    "材料名称"
  ],
  "image_path": "..."
}
```

关键边界：

- 这一步不是文本合并。
- 这一步只提供附近上下文。
- 不判断附近文字是否一定属于同一个材料项。

附近文字检索建议：

- 只取同一页。
- 优先同一 tile 和相邻 tile。
- 优先纵向接近、横向接近的文字。
- 每条当前文字最多带 10 到 20 条附近文字，避免上下文过长。
- 单字、数字、符号也生成上下文包，由 LLM 判断是否噪声或编号。

输出：

```text
ocr_context_packages.jsonl
```

验收标准：

- 每条 OCR 都有上下文包。
- 材料文字附近能看到相关规格、图名、材料表文字或构造文字。
- 纯数字和单字符也保留上下文，不能在此阶段硬删。

### 2026-06-25 实现记录

已完成阶段 2 MVP，实现文件：

```text
AI_Middle_Office/app/services/drawing_ocr_evidence_context_builder.py
AI_Middle_Office/scripts/biz2x_ocr_context_preview.py
AI_Middle_Office/tests/test_drawing_ocr_evidence_context_builder_biz2x.py
```

当前实现仍然不做文本合并、不做材料分类、不做噪声删除。它只为每条 OCR 证据找到附近 OCR 证据，并生成给 LLM 使用的上下文包。

上下文检索口径：

- 只在同一页内找附近证据。
- 优先同 tile。
- 其次相邻 tile。
- 再考虑同一行附近、垂直邻近和局部空间邻近。
- 默认每条证据最多带 16 条周边证据。
- 该规则只负责“找上下文”，不负责判断这些文字是否属于同一个材料项。

真实样本运行命令：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_context_preview.py `
  --raw-evidence-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage1_raw_evidence\ocr_raw_evidence.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages `
  --max-nearby 16 `
  --max-page-distance 0.08
```

真实样本输出：

```text
outputs/biz2x_trial/ocr_cabinet/20260625_stage2_context_packages/ocr_context_packages.jsonl
outputs/biz2x_trial/ocr_cabinet/20260625_stage2_context_packages/ocr_context_packages.csv
outputs/biz2x_trial/ocr_cabinet/20260625_stage2_context_packages/ocr_raw_evidence_with_context.jsonl
outputs/biz2x_trial/ocr_cabinet/20260625_stage2_context_packages/ocr_raw_evidence_with_context.csv
outputs/biz2x_trial/ocr_cabinet/20260625_stage2_context_packages/ocr_context_summary.json
```

真实样本结果：

- 输入 OCR 证据：2624。
- 输出上下文包：2624。
- 有周边证据的上下文包：2624。
- 无周边证据的上下文包：0。
- 每条上下文包默认周边证据数：16。
- 总周边链接数：41984。
- 关系统计：
  - same_tile：17330。
  - adjacent_tile：24613。
  - same_line_nearby：19。
  - spatial_neighbor：22。

抽查结果：

- `拆除墙面300x600墙面砖` 的上下文包含其他拆除项和尺寸数字，可供 LLM 判断其属于拆除项。
- `墙刷白色无机涂料三度+50mm黑色拉丝不锈钢踢脚线` 的上下文包含 `轻钢龙骨石膏板隔墙刷白色无机涂料三度`、`+50mm黑色拉丝不锈钢踢脚线`、`材料名称`、`图列` 等，可供 LLM 判断其属于材料/做法。
- `10mm钢化磨砂玻璃` 的上下文包含 `GL 01`、`成品木饰面`、`黑色拉丝不锈钢踢脚线` 等材料编号和材料文字，可供 LLM 分类入柜。

验证：

```text
AI_Middle_Office/tests/test_drawing_ocr_evidence_context_builder_biz2x.py: 1 passed
AI_Middle_Office/tests/test_drawing_ocr_raw_evidence_biz2x.py: 1 passed
```

## 六、阶段 3：LLM 分类入柜

目标：

LLM 根据“当前文字 + 周边文字”判断每条 OCR 证据属于哪个分类柜。

建议输出结构：

```json
{
  "text_id": "T00858",
  "primary_category": "材料/做法",
  "secondary_category": "墙面做法",
  "quote_relevance": "高",
  "suggested_roles": ["项目特征", "材料名称"],
  "normalized_text": "墙刷白色无机涂料三度，50mm黑色拉丝不锈钢踢脚线",
  "is_noise": false,
  "need_vlm_review": false,
  "reason": "文本包含墙面做法、涂料、不锈钢踢脚线，属于报价材料/做法信息。"
}
```

分类柜定义：

| 柜子 | 内容 |
|---|---|
| 材料/做法 | 墙面砖、涂料、木饰面、玻璃、踢脚线、基层等 |
| 拆除项 | 拆除门、拆除吊顶、拆除墙面砖等 |
| 新建/安装项 | 新建墙体、安装玻璃隔断等 |
| 设备/构件 | 抽气罩、门套、方通、铝扣板等 |
| 规格尺寸 | 600x1200、10mm、80*60、宽度2200 等 |
| 工程量/数量线索 | 数字、面积、长度、数量，暂不直接认定为工程量 |
| 图名/标题 | 平面图、剖面图、铺装图、节点图等 |
| 公司/人名/证书 | 建设单位、设计单位、资质证书、签字等 |
| 轴号/索引/编号 | A、B、C、1、2、C4、FJ0-05 等 |
| 噪声 | 无意义碎片、乱码、孤立符号等 |
| 不确定 | 需要 VLM 或人工复核 |

输出：

```text
classified_ocr_cabinet.json
classified_ocr_cabinet.csv
```

验收标准：

- 单字符、单数字大多进入“轴号/索引/编号”或“噪声”，不进入材料候选主表。
- 公司、证书、图名被分入对应柜子。
- 已知材料文本进入材料/做法、拆除项、新建/安装项、设备/构件等柜子。
- LLM 对不确定内容必须允许输出“不确定”，不能强行分类。

## 七、阶段 4：材料候选归并

目标：

从分类柜中抽取高价值报价相关信息，并归并为业务员可审阅的材料候选。

高价值类别：

- 材料/做法
- 拆除项
- 新建/安装项
- 设备/构件
- 与上述候选相关的规格尺寸

归并示例：

```text
600X1200白色墙面砖
X1200白色墙面砖
600X1200白色墙面
```

归并为：

```text
600X1200白色墙面砖
```

归并要求：

- 保留所有来源 OCR 证据。
- 保留截图路径。
- 不因归并丢失原文。
- 不直接生成最终报价清单。

输出：

```text
material_candidate_review.csv
material_candidate_review.md
material_candidate_review.json
```

建议字段：

- `候选ID`
- `候选类型`
- `项目名称候选`
- `项目特征候选`
- `规格尺寸`
- `相关工程量/尺寸线索`
- `来源OCR文本`
- `证据数量`
- `截图路径`
- `系统理由`
- `是否需要VLM复核`
- `人工确认`
- `人工备注`

验收标准：

- 业务员不再看 2624 条 OCR。
- 业务员只看归并后的材料候选。
- 每个候选都能追溯到原始 OCR 和截图。
- 公司、证书、图名、单字符噪声不进入材料候选主表。

## 八、阶段 5：VLM 疑难复核

目标：

只对少量 LLM 无法稳定判断的候选使用 VLM。

触发条件：

- LLM 输出 `need_vlm_review=true`。
- 文本像材料，但附近上下文不足。
- 需要判断引线指向。
- 需要识别表格列关系。
- 需要理解节点构造层级。
- 需要区分数字是尺寸线、编号还是工程量。

VLM 输入：

- 不传整页。
- 只传候选附近的局部 crop。
- 带上 OCR 当前文字和周边文字。

VLM 输出：

```text
vlm_review_results.json
```

只要求 VLM 补充：

- 是否确认材料/做法。
- 是否能看出指向关系。
- 是否能看出表格结构。
- 是否有工程量线索。
- 是否仍需人工复核。

## 九、阶段 6：业务审阅与反馈

业务员主看：

```text
material_candidate_review.csv
```

人工确认枚举：

- `纳入`
- `不纳入`
- `不确定`
- `需要补充截图`
- `分类错误`

反馈用途：

- 优化 LLM prompt。
- 优化分类柜定义。
- 优化材料候选归并。
- 反推后续 OCR 提速区域。

业务验收不再要求业务员审阅：

- 全部 OCR 原始文本。
- 全部 crop。
- 全部噪声样例。
- 公司名、证书、图名等非报价主信息。

## 十、阶段 7：反推 OCR 提速策略

当材料候选链路稳定后，再回头优化 OCR 前半段。

反推依据：

```text
哪些 OCR 文本最终进入材料候选
→ 它们来自哪些 tile / 区域 / 字体高度 / 图纸位置
→ 哪些区域长期只产出图签、证书、轴号、噪声
→ 后续 OCR 调度优先跑高价值区域，压低低价值区域
```

今天已完成的能力可在此阶段继续使用：

- 中清文字区域发现。
- Top N 预算调度。
- 预算内多样性调度。
- 按文字区域高度动态算 scale。
- highres region renderer。
- OCR 后质量评分。

调整后的定位：

- 它们不是当前材料识别主判断器。
- 它们是未来降低 OCR 成本、减少无效高清渲染的性能优化工具。

## 十一、建议落地文件

新增服务：

```text
AI_Middle_Office/app/services/drawing_ocr_evidence_context_builder.py
AI_Middle_Office/app/services/drawing_ocr_llm_cabinet_classifier.py
AI_Middle_Office/app/services/drawing_material_candidate_assembler.py
```

新增脚本：

```text
AI_Middle_Office/scripts/biz2x_ocr_cabinet_preview.py
```

输出目录：

```text
outputs/biz2x_trial/ocr_cabinet/<run_id>/
```

## 十二、第一版 MVP 验收标准

用当前 `all_text_evidence_64.csv` 跑通。

必须满足：

1. 原始 2624 条 OCR 全部保留。
2. 生成 `ocr_context_packages.jsonl`。
3. 生成 `classified_ocr_cabinet.json/csv`。
4. 生成 `material_candidate_review.csv/md/json`。
5. 已知有效信息必须进入材料候选或对应高价值柜子：
   - 防水石膏板刷白色防潮无机涂料
   - 拆除不锈钢玻璃地弹门
   - 拆除墙面300x600墙面砖
   - 黑色拉丝不锈钢踢脚线
   - 600X1200白色墙面砖
   - 10mm钢化磨砂玻璃
   - 0.8厚铝扣板吊顶
   - 80*60不锈钢方通
6. 公司、证书、图名、单字符噪声不进入材料候选主表。
7. 每个候选都有来源 OCR 和截图路径。
8. LLM 不确定项能被标记出来，并进入 VLM 或人工复核队列。

## 十三、当前阶段暂停事项

在证据柜路线跑通前，以下事项暂缓：

- 不继续把 crop 审阅表作为业务验收主产物。
- 不继续以“前半段少 OCR”为当前主目标。
- 不直接生成最终报价清单。
- 不直接入数据库。
- 不自动计算工程量。
- 不删除原始 OCR 噪声文本。

## 十四、最终目标

本路线最终目标是：

```text
让系统从图纸中高召回读取文字，
再把 OCR 证据分类入柜，
再从柜子中组合出可审阅的材料/做法/拆除/设备候选，
最后逐步走向项目名称、项目特征、单位、工程量的结构化提取。
```

短期目标不是一次性自动报价，而是先建立可靠、可追溯、可人工审阅的图纸 OCR 证据分类底座。

## 2026-06-26 阶段 3 实现记录：OCR 证据分类入柜 MVP

阶段 3 已完成代码层 MVP。当前边界仍然是“分类入柜”，不做最终报价清单、不做材料候选归并、不删除原始 OCR 证据。

新增文件：

```text
AI_Middle_Office/app/services/drawing_ocr_llm_cabinet_classifier.py
AI_Middle_Office/scripts/biz2x_ocr_cabinet_preview.py
AI_Middle_Office/tests/test_drawing_ocr_llm_cabinet_classifier_biz2x.py
```

阶段 3 固定原则：

1. 一条 OCR 证据生成一条分类结果。
2. 周边文字只作为判断上下文，不合并、不改写、不替代当前 OCR 原文。
3. 噪声不删除，只进入“噪声”柜。
4. 输出固定包含 text_id、原文、主分类、子分类、是否有效、置信度、中文判断原因、关联证据ID、是否需要VLM复核等字段。
5. LLM 不确定或需要视觉关系判断时，标记 `needs_vlm_review=true`。

已固定中文分类柜：

```text
材料/做法
拆除项
新建/安装项
设备/构件
规格尺寸
工程量/数量线索
图名/标题
公司/人名/图签信息
轴号/索引/编号
噪声
不确定
```

脚本能力：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_cabinet_preview.py `
  --context-packages-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages\ocr_context_packages.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_deepseek_preview `
  --sample-size 240 `
  --sample-strategy representative `
  --max-nearby 16 `
  --max-items-per-batch 30 `
  --provider deepseek
```

说明：

- 不带 `--execute` 时只生成 DeepSeek 调用预览、prompt 和抽样计划，不外发数据。
- 带 `--execute --provider deepseek` 时才会真实调用 DeepSeek。
- `--provider mock --execute` 只用于离线验证管线和输出格式，不代表真实 LLM 判断质量。
- `--sample-size 0` 表示全量分类 2624 条上下文包。

离线模拟验收命令：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_cabinet_preview.py `
  --context-packages-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages\ocr_context_packages.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_mock `
  --sample-size 240 `
  --sample-strategy representative `
  --max-nearby 16 `
  --max-items-per-batch 30 `
  --execute `
  --provider mock
```

离线模拟输出：

```text
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_mock/llm_cabinet_prompt.md
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_mock/llm_cabinet_batches_preview.json
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_mock/llm_cabinet_sample_plan.csv
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_mock/classified_ocr_cabinet.json
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_mock/classified_ocr_cabinet.csv
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_mock/classified_ocr_cabinet_review.md
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_mock/ocr_llm_cabinet_summary.json
```

离线模拟结果：

- 输入上下文包：2624 条。
- 代表性抽样：240 条。
- 分类结果：240 条。
- 有效证据：182 条。
- 噪声证据：58 条。
- 需要 VLM 复核：53 条。

验证：

```text
AI_Middle_Office/tests/test_drawing_ocr_raw_evidence_biz2x.py: passed
AI_Middle_Office/tests/test_drawing_ocr_evidence_context_builder_biz2x.py: passed
AI_Middle_Office/tests/test_drawing_ocr_llm_cabinet_classifier_biz2x.py: 4 passed
```

当前限制：

真实 DeepSeek smoke 未执行。原因是本地图纸 OCR 上下文会发送到外部 DeepSeek API，当前安全策略要求在明确确认数据外发风险后才能执行。当前已经生成 DeepSeek dry-run 输入预览，可在确认后直接执行同一脚本。

补充记录：

- 2026-06-26 用户已明确确认数据外发风险。
- 但 Codex 当前执行层仍拒绝把本地图纸 OCR 上下文发送到外部 DeepSeek API；这是租户级数据外发限制，不能绕过。
- 因此阶段 3 增加了 `--provider local` 安全替代路径，只允许调用 `localhost`、`127.0.0.1` 或内网私有地址的 OpenAI-compatible chat completions 端点。
- `--provider local` 若指向公网 URL，会被代码拒绝，避免把本地/内网通道误用为外部数据外发通道。

本地/内网 LLM 调用示例：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_cabinet_preview.py `
  --context-packages-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages\ocr_context_packages.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_local_smoke20 `
  --sample-size 20 `
  --sample-strategy representative `
  --max-nearby 10 `
  --max-items-per-batch 10 `
  --execute `
  --provider local `
  --model local-model-name `
  --local-chat-url http://127.0.0.1:11434/v1/chat/completions
```

该路径用于后续连接本机或内网部署的 Qwen/DeepSeek/OpenAI-compatible 模型服务，继续验证阶段 3 真实分类质量。

## 2026-06-26 补充：由用户本机手动调用外部 DeepSeek

由于 Codex 执行层不能代为外发真实图纸 OCR 上下文，阶段 3 支持由用户在自己的 PowerShell/后端环境中手动执行 DeepSeek 调用。

外发最小化模式：

- 默认 `--external-payload-mode minimal`。
- 外发 payload 只包含：`text_id`、`current_text`、`confidence`、`current_features`、周边 `text_id/text/relation/rank`。
- 不外发：`image_path`、截图路径、`bbox_ratio`、精确坐标、`tile_id`、`source_file`、`snippet_id`、整页 OCR、PDF 文件。
- 默认 `mask_sensitive_text=true`，会对明显图签公司/人员/证书/日期类文本做标签化打码；材料、规格、做法、拆除、设备文字不打码。
- 本地输出审阅表仍会回填原始 OCR、截图路径、页码和 tile，方便业务复核。

20 条 smoke 命令：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test

C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_cabinet_preview.py `
  --context-packages-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages\ocr_context_packages.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_deepseek_smoke20 `
  --sample-size 20 `
  --sample-strategy representative `
  --max-nearby 10 `
  --max-items-per-batch 10 `
  --max-chars-per-batch 45000 `
  --external-payload-mode minimal `
  --execute `
  --provider deepseek `
  --trace-id biz2x_stage3_deepseek_smoke20
```

240 条代表性样本命令：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test

C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_cabinet_preview.py `
  --context-packages-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages\ocr_context_packages.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_deepseek_sample240 `
  --sample-size 240 `
  --sample-strategy representative `
  --max-nearby 16 `
  --max-items-per-batch 10 `
  --max-chars-per-batch 45000 `
  --external-payload-mode minimal `
  --execute `
  --provider deepseek `
  --trace-id biz2x_stage3_deepseek_sample240
```

执行成功后重点查看：

```text
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_sample240/classified_ocr_cabinet.csv
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_sample240/classified_ocr_cabinet_review.md
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_sample240/ocr_llm_cabinet_summary.json
```

如果 20 条 smoke 失败，先不要跑 240 条；优先检查 `AI_Middle_Office/.env` 中的 `DEEPSEEK_API_KEY`、`DEEPSEEK_CHAT_URL`、`DEEPSEEK_MODEL`。

240 条样本如果中途出现 `httpx.ConnectError`、`httpcore.ConnectError`、`http_proxy.start_tls` 等网络/代理/TLS 连接错误，通常不是分类逻辑错误，也不是 DeepSeek 返回格式错误。不要删除输出目录，先使用同一个 `output-dir` 断点续跑：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test

C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_cabinet_preview.py `
  --context-packages-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages\ocr_context_packages.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_deepseek_sample240 `
  --sample-size 240 `
  --sample-strategy representative `
  --max-nearby 16 `
  --max-items-per-batch 10 `
  --max-chars-per-batch 45000 `
  --external-payload-mode minimal `
  --execute `
  --provider deepseek `
  --trace-id biz2x_stage3_deepseek_sample240_resume `
  --resume `
  --max-retries 5 `
  --retry-delay-seconds 5
```

当前脚本会读取 `classified_ocr_cabinet.partial.json`，跳过已经完成的 `text_id` 批次，只继续调用剩余批次。为避免长跑任务中断时损坏断点文件，阶段 3 的 JSON 写入已改为先写临时文件再替换正式文件。

## 2026-06-26 补充：全量 2624 条 OCR 证据分类入柜

240 条代表性样本经业务审阅整体认可后，可以进入全量分类。全量 dry-run 预检结果：

- 输入上下文包：2624 条
- 入选分类对象：2624 条
- 批次数：263 批（每批 10 条）
- 外发模式：`minimal`
- 图签敏感文本打码：开启

正式执行命令如下。建议始终带 `--resume`，首次运行时不会跳过任何内容；如果中途网络失败，直接重跑同一条命令即可续跑剩余批次。

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test

C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_cabinet_preview.py `
  --context-packages-jsonl outputs\biz2x_trial\ocr_cabinet\20260625_stage2_context_packages\ocr_context_packages.jsonl `
  --output-dir outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_deepseek_full2624 `
  --sample-size 0 `
  --sample-strategy representative `
  --max-nearby 16 `
  --max-items-per-batch 10 `
  --max-chars-per-batch 45000 `
  --external-payload-mode minimal `
  --execute `
  --provider deepseek `
  --trace-id biz2x_stage3_deepseek_full2624 `
  --resume `
  --max-retries 5 `
  --retry-delay-seconds 5
```

执行成功后重点查看：

```text
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_full2624/classified_ocr_cabinet.json
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_full2624/classified_ocr_cabinet.csv
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_full2624/classified_ocr_cabinet_review.md
outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_full2624/ocr_llm_cabinet_summary.json
```

全量分类结果：

- OCR 证据：2624 条。
- 有效证据：2021 条。
- 噪声证据：512 条。
- 需要 VLM 复核：134 条。
- 材料/做法：219 条。
- 拆除项：26 条。
- 新建/安装项：1 条。
- 设备/构件：60 条。
- 规格尺寸：248 条。
- 工程量/数量线索：5 条。

## 2026-06-26 补充：阶段 4 报价候选归并与证据挂接 MVP

阶段 4 的目标不是生成最终报价清单，而是把阶段 3 的“证据柜”整理成业务员可审阅的报价候选池。

阶段边界：

- 主候选只来自：`材料/做法`、`拆除项`、`新建/安装项`、`设备/构件`。
- `规格尺寸`、`工程量/数量线索`、`图名/标题`、`轴号/索引/编号` 只作为关联证据挂到主候选下面，不单独成为报价项。
- 不跨类型归并，例如拆除项不会和新建/安装项、设备/构件混成一个候选。
- 原始 OCR 和阶段 3 分类结果继续保留，阶段 4 只新增候选结构，不删除证据。
- VLM 只用于复核图形关系、引线、节点构造、表格归属、规格/工程量归属等视觉关系。

实现文件：

```text
AI_Middle_Office/app/services/drawing_quote_candidate_assembler.py
AI_Middle_Office/scripts/biz2x_quote_candidates_preview.py
AI_Middle_Office/tests/test_drawing_quote_candidate_assembler_biz2x.py
tmp/stage4_quote_candidate_report/build_stage4_quote_candidate_report.mjs
```

运行命令：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test

C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_quote_candidates_preview.py `
  --classified-cabinet-json outputs\biz2x_trial\ocr_cabinet\20260626_stage3_ocr_cabinet_deepseek_full2624\classified_ocr_cabinet.json `
  --output-dir outputs\biz2x_trial\quote_candidates\20260626_stage4_quote_candidates_full2624_mvp `
  --max-attachments-per-type 8
```

当前输出：

```text
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/quote_candidates.json
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/quote_candidates.csv
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/quote_candidates_review.md
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/vlm_review_tasks.jsonl
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/stage4_quote_candidate_summary.json
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/stage4_quote_candidates_business_review.xlsx
```

当前结果：

- 阶段 3 输入证据：2624 条。
- 主候选证据：304 条。
- 形成报价候选：116 个。
- 材料/做法候选：73 个。
- 拆除项候选：20 个。
- 新建/安装项候选：1 个。
- 设备/构件候选：22 个。
- 已挂规格的候选：61 个。
- 已挂工程量线索的候选：5 个。
- 需要 VLM/人工确认的候选：5 个。
- VLM 任务：134 个，当前均为 P2 暂缓视觉复核；无 P0/P1 紧急复核任务。

本轮已修正的挂接口径：

- 即使 LLM 给出直接关联，纯数字也不会直接进入规格栏。
- 普通轴号、单字母、普通编号不会直接进入材料代号/索引栏。
- 规格证据需要像真实规格，例如 `600X1200`、`50mm`、`直径8MM`、`宽度2200，高度2400`。
- 材料代号/索引优先保留 `CT 04`、`MT 01`、`PB-01`、`GL 01` 等明显材料代号格式。

业务验收看法：

- 先看 `stage4_quote_candidates_business_review.xlsx` 的“报价候选”表，确认 116 个候选哪些应保留、哪些应合并、哪些是误入。
- 再看“重点确认候选”表，优先处理候选名残缺、挂接证据需要确认、低置信候选。
- 如怀疑规格或材料代号挂错，再看“挂接证据明细”表追溯到证据 ID 和截图文件。
- “VLM复核任务”表目前主要是 P2 暂缓项，不阻塞候选归并；后续只有当业务员确认某些规格/工程量归属不清时，再升级为 VLM 小任务。

阶段 4 验收标准：

- 304 条主证据能收敛为业务员可审阅的候选池，而不是直接把 304 条或 2624 条全部交给业务员。
- 候选不跨类型误并，拆除、材料做法、设备构件保持边界清楚。
- 规格/工程量只作为关联证据，不把纯尺寸或数字独立当成报价项。
- 原始证据、分类结果、候选、挂接证据都能通过 `text_id` 追溯。
- 业务员能在 Excel 中对候选做“确认有效 / 合并到其他候选 / 确认噪声 / 待VLM / 暂缓”的人工确认。

验证：

```text
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_drawing_quote_candidate_assembler_biz2x.py -q
5 passed, 1 warning

C:\Users\12521\miniconda3\python.exe -m py_compile AI_Middle_Office\app\services\drawing_quote_candidate_assembler.py AI_Middle_Office\scripts\biz2x_quote_candidates_preview.py
通过
```

### 2026-06-26 补充：按系统建议处理 116 个候选

已新增脚本：

```text
AI_Middle_Office/scripts/biz2x_quote_candidates_apply_system_suggestions.py
```

处理口径：

- `确认有效`：进入下一步候选归并。
- `待VLM`：先确认挂接证据，不进入自动归并。
- `暂缓`：保留候选项目名，但因缺少规格/工程量等支撑证据，暂不进入归并；后续补证据或人工确认后可恢复。

运行命令：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test

C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_quote_candidates_apply_system_suggestions.py `
  --quote-candidates-json outputs\biz2x_trial\quote_candidates\20260626_stage4_quote_candidates_full2624_mvp\quote_candidates.json `
  --output-dir outputs\biz2x_trial\quote_candidates\20260626_stage4_quote_candidates_full2624_mvp
```

处理结果：

- 候选总数：116。
- 确认有效：62。
- 待 VLM/人工确认：5。
- 暂缓补规格/工程量：49。

下一步归口：

- 材料/做法归并：37。
- 拆除项归并：16。
- 新建/安装项归并：1。
- 构件/设备归并：8。
- VLM/人工确认：5。
- 暂缓补证据：49。

输出：

```text
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/quote_candidates_system_processed.json
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/quote_candidates_system_processed.csv
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/quote_candidates_system_processed_review.md
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/quote_candidates_system_processed_summary.json
outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/stage4_quote_candidates_system_processed.xlsx
```

当前 `stage4_quote_candidates_system_processed.xlsx` 已把“人工确认”列按系统建议批量填好，可作为下一步“确认有效候选归并”的输入。

## 2026-06-26 补充：阶段 5 列项清单四字段草案生成 MVP

阶段 5 的目标是把阶段 4 中“确认有效”的候选整理成四字段草案：

```text
项目名称 / 项目特征 / 单位 / 工程量
```

阶段边界：

- 只处理阶段 4 处理结果为 `确认有效` 的候选。
- `待VLM` 和 `暂缓` 候选不进入本轮四字段草案。
- 项目名称、项目特征、单位可以按候选证据生成草案。
- 工程量只从明确的工程量线索中抽取；没有明确证据时留空，不猜。
- 本阶段产物不是最终报价清单，也不是最终计量结果。

实现文件：

```text
AI_Middle_Office/app/services/drawing_quantity_list_draft.py
AI_Middle_Office/scripts/biz2x_quantity_list_draft_preview.py
AI_Middle_Office/tests/test_drawing_quantity_list_draft_biz2x.py
tmp/stage5_quantity_list_report/build_stage5_quantity_list_report.mjs
```

运行命令：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test

C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_quantity_list_draft_preview.py `
  --processed-candidates-json outputs\biz2x_trial\quote_candidates\20260626_stage4_quote_candidates_full2624_mvp\quote_candidates_system_processed.json `
  --output-dir outputs\biz2x_trial\quantity_list_drafts\20260626_stage5_quantity_list_draft_mvp
```

输出：

```text
outputs/biz2x_trial/quantity_list_drafts/20260626_stage5_quantity_list_draft_mvp/quantity_list_draft.json
outputs/biz2x_trial/quantity_list_drafts/20260626_stage5_quantity_list_draft_mvp/quantity_list_draft.csv
outputs/biz2x_trial/quantity_list_drafts/20260626_stage5_quantity_list_draft_mvp/quantity_list_four_fields.csv
outputs/biz2x_trial/quantity_list_drafts/20260626_stage5_quantity_list_draft_mvp/quantity_list_draft_review.md
outputs/biz2x_trial/quantity_list_drafts/20260626_stage5_quantity_list_draft_mvp/quantity_list_draft_summary.json
outputs/biz2x_trial/quantity_list_drafts/20260626_stage5_quantity_list_draft_mvp/stage5_quantity_list_draft_review.xlsx
```

当前结果：

- 输入候选：116。
- 阶段 4 确认有效候选：62。
- 输出四字段草案：62。
- 可用草案：0。
- 缺工程量：57。
- 工程量待确认：3。
- 单位待确认：19。
- 项目名称待确认：2。

单位推断结果：

- `㎡`：31。
- `m`：7。
- `樘`：8。
- `项`：6。
- `套`：3。
- `待确认`：7。

本轮收紧点：

- 单位推断优先看项目名称本身，不让挂接规格把单位带偏。例如“成品木饰面”不会因为旁边挂了“线型灯规格”就推成 `m`。
- `注:`、`说明`、`所有`、`均置顶` 等说明性文字，即使带工程量，也标为“项目名称待确认”，不标为可用草案。
- 多个工程量候选不自动选择，标为“工程量待确认”。

阶段 5 结论：

- 当前系统已经能生成四字段草案结构。
- 当前主要缺口已经非常明确：不是项目名称或项目特征，而是工程量不足。
- 下一步应进入“工程量补算/归属”阶段：从图纸几何、DXF/CAD、VLM 视觉关系或人工补量中补齐工程量。

验证：

```text
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_drawing_quantity_list_draft_biz2x.py -q
6 passed, 1 warning

C:\Users\12521\miniconda3\python.exe -m py_compile AI_Middle_Office\app\services\drawing_quantity_list_draft.py AI_Middle_Office\scripts\biz2x_quantity_list_draft_preview.py
通过
```
