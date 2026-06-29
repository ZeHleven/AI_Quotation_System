# BIZ-2x 图纸识别问题讨论记录

生成时间：2026-06-24

本文档用于记录 PDF/CAD 图纸识别与报价材料候选提取过程中，围绕 OCR 调度、材料文字召回、噪声控制和后续优化方向的讨论结论。

## 最终目标

最终要实现的目标不是单纯 OCR，也不是单纯“读出图纸文字”，而是：

**从 PDF/CAD 图纸中自动提取可用于报价的材料、构件、做法、拆除、设备等候选项，并尽量减少漏项和噪声，最终辅助生成可人工确认的报价清单。**

更具体地说，目标链路是：

```text
PDF / CAD 图纸输入
→ 自动判断页类型
→ 对 CAD 图纸页做高效区域调度
→ OCR 读出图中文字
→ VLM 理解图形、节点、引线、表格、材料表
→ LLM 语义分类材料、构件、做法、拆除、设备
→ 跨区域/跨页归并材料代号、说明、节点引用
→ 形成报价材料/项目候选
→ 匹配成本库、历史报价、RAG
→ 人工复核确认
→ 进入报价清单/报价流程
```

当前最关注的是前半段：

```text
如何从图纸里更快、更准、更少噪声地找到报价相关材料信息
```

核心目标包括：

1. 尽量不漏材料信息

   重点覆盖材料代号、材料表、构造做法、节点说明、拆除/新建/安装项、设备项。

2. 尽量少跑无效 OCR

   不再对所有图纸无差别 64 倍切块，而是通过预算调度优先处理高价值区域。

3. 噪声可控

   减少公司名、图框、轴号、纯尺寸、线条、符号、乱码等进入材料候选。

4. 结果可追溯

   每个材料候选都能追溯到页码、crop、OCR 文本、图纸区域、置信度和判断依据。

5. 系统可以逐步自我校准

   OCR 后质量分、人工审阅、LLM 分类结果，都可以反向优化 discovery、排序和召回策略。

6. 先生成可人工确认的报价候选

   当前阶段不追求无人值守自动下单，而是先实现“智能提取 + 证据归并 + 人工复核”，再进入报价系统。

一句话概括：

**把图纸从“视觉文件”变成“带证据链的报价材料候选清单”。**

## 实施路线

总路线：

```text
先保证能找到足够多的文字证据
→ 再区分有效文字和噪声
→ 再识别哪些文字是材料/做法/构件
→ 再做跨区域归并
→ 最后生成可人工确认的报价候选清单
```

### 阶段 1：OCR 调度器稳定化，解决“慢”和“杂”

目标：少跑无效 OCR，同时保留足够多的高价值文字区域。

当前已完成：

- 中清文字区域发现
- 大 CAD 区域二次拆分
- 动态 scale
- OCR 质量评分
- 正负样本反馈 discovery 排序
- 有效文字/噪声审阅文档

下一步要补：

- 标记是否 hit cap：每块 40、每页上限、全局上限
- 输出 overflow 候选，知道哪些区域因为预算没进 OCR
- rejected 分层：真噪声、可恢复文字、需继续拆分大块

### 阶段 2：召回保障，解决“材料文字找不全”

目标：不是无限 OCR，而是保证关键材料文字不被漏掉。

重点：

- 对短材料代号兜底：`PT-01`、`CT-02`、`WD-01`
- 对材料表/图例/右侧说明保留专门预算
- 对 rejected 中可能误杀的文字做低成本 OCR 抽样
- 对 overflow 区域做抽样复核

验收标准：

- 能明确说明哪些区域是被预算截断
- 能从 rejected 中恢复一批 OCR high 的有效文字
- 材料代号类文本召回明显提升

### 阶段 3：OCR 证据结构化，解决“文字不是清单”

目标：把 OCR 行变成可追溯证据，而不是直接变报价项。

建议输出结构：

```text
页码
区域 ID
OCR 文本
质量分
文本类型初判
来源 crop
是否材料相关
是否需人工复核
```

这一层先不生成最终报价，只做证据整理。

### 阶段 4：材料语义分类

目标：判断 OCR 文本属于什么业务类型。

分类建议：

- 材料
- 构件
- 做法
- 拆除
- 新建/安装
- 设备
- 材料代号
- 尺寸/工程量
- 图名/公司/标题栏噪声

这一层可以复用已经接入的 DeepSeek/Qwen/LLM 分类思路。

### 阶段 5：跨区域归并，形成材料候选

目标：把分散证据合并成报价候选。

示例：

```text
CT-01
+ 600x1200 灰色地砖
+ 餐厅地面
+ 节点/材料表说明
= 地面 600x1200 灰色地砖候选
```

输出应包含：

- 候选名称
- 规格/做法
- 类型
- 来源证据
- 置信度
- 是否缺工程量
- 是否需人工确认

### 阶段 6：接入报价系统

目标：把材料候选接到现有成本库和报价链路。

重点：

- 匹配 `cost_items.active`
- 找不到底价的进入 draft 候选
- 人工确认后进入报价预审
- 保留证据链

### 阶段 7：人工反馈闭环

目标：让系统越用越准。

人工可以标记：

- 这是有效材料
- 这是噪声
- 漏掉了材料
- 材料归并错了
- 规格/做法错了

这些反馈再反向优化：

- discovery 排序
- OCR 质量权重
- LLM 分类 prompt
- 材料归并规则

### 近期优先路线

当前最合理的近期推进顺序：

```text
1. cap / overflow / rejected 分层可视化
2. rejected 可恢复文字 OCR 抽样
3. OCR 证据结构化
4. 材料语义分类
5. 材料候选归并 MVP
```

近期不应急着做最终自动报价，应先把“召回保障 + OCR 证据结构化”做稳。

## 当前背景

当前已打通的 MVP 链路：

```text
中清图发现疑似文字区域
→ Top N 高价值区域高清渲染
→ PaddleOCR 识别
→ OCR 质量评分
→ OCR 质量分反向反馈 discovery 排序
```

当前能力定位：

- 已能减少“整图切块 + 全部 64 倍 OCR”的粗暴流程。
- 已能识别一批高质量 OCR 区域。
- 已能区分部分有效文字与低质量/no_text 噪声。
- 已能用正负 OCR 样本反向调整下一轮 discovery 排序。
- 当前仍不是完整的“材料文字全召回 + 报价候选生成”系统。

## 问题 1：为什么现在不能稳定保证所有材料文字都被找全？

### 当前判断

现在系统能更聪明地决定优先 OCR 哪些区域，但还没有完成“材料语义召回保障”。它提升了效率和局部准确性，但不能稳定保证所有材料文字都被覆盖。

### 主要原因

1. discovery 只看图像形态，不懂材料语义

   中清文字区域发现层主要依据高度、密度、连通组件、宽高比、线条比例、颜色比例等视觉特征判断“像不像文字”。它不知道某个区域是否是材料、做法、构件、设备、拆除项或材料表。

2. 短材料代号和噪声形态相似

   CAD 图纸里的关键材料信息可能很短，例如：

   - `PT-01`
   - `CT-02`
   - `WD-01`
   - `600x1200`
   - `不锈钢`

   这些在图像上可能只有少量组件，容易被误判为单组件线条、稀疏噪声、尺寸碎片、轴号或符号。

3. 大 CAD 区域二次拆分还不稳定

   当前已经对 `too_large_for_text_region` 大块区域做二次拆分，但如果文字被线框、填充、节点线、尺寸线包围，仍可能出现：

   - 被并进大块区域
   - 拆得过碎
   - 只拆出半行
   - 被误认为 hatch/line noise

4. Top N 预算天然会牺牲召回

   为了提升速度，系统不会无限 OCR 所有候选，而是优先处理 Top N。这个策略会带来取舍：速度更快，但可能漏掉低优先级但真实有用的材料文字。

5. OCR 高质量不等于材料高价值

   当前 OCR 质量评分主要衡量“文字是否读得清楚”。公司名、图名、尺寸数字也可能得到高分，但报价材料真正关心的是材料、构造、做法和构件信息。

6. 负样本还不够丰富，误杀边界仍需校准

   “噪声”和“短材料代号”在图像形态上有重叠。负样本不足时，系统还不能稳定地区分：

   - 真噪声：`I`、`L`、`口`、单线条、符号
   - 真材料：`PT-01`、`CT-01`、`Φ50`、`600×600`

7. 跨区域关系理解还没建立

   材料信息经常分散在多个区域，例如：

   - 一个区域是材料代号
   - 另一个区域是材料表说明
   - 节点编号对应另一页节点详图
   - 引线文字和构件图形分离

   当前 discovery + OCR 主要处理单区域，还没有稳定恢复引线、节点、表格、图例和页间引用关系。

### 后续需要补的能力

1. 召回兜底

   对短材料代号、引线端点、右侧说明、标题栏、材料表区域设置专门召回策略。

2. OCR 后语义二筛

   OCR 后用规则或 LLM 判断文本属于材料、构件、做法、拆除、设备、尺寸、图名、公司信息等哪一类。

3. 跨区域归并

   将材料代号、说明文字、表格、节点引用归并成报价候选，而不是只看单个 OCR crop。

## 后续问题记录区

### 问题 2：标注数量上限是否也是导致材料文字找不全的原因？

结论：是的，这是一个明确原因。

当前系统为了控制 OCR 成本和调度预算，对候选区域设置了多层数量上限。真实页面如果刚好打满上限，后续区域即使被中间过程发现，也可能不会进入最终 `text_region_plan.json`、不会生成标注框，也不会进入 highres OCR。

当前主要有几类截断：

1. 大块 CAD 区域二次拆分上限

   对 `too_large_for_text_region` 的大块区域，二次拆分后最多保留一批高优先级子区域。当前 MVP 中单个大块最多返回 40 个 selected 子区域。真实页如果刚好打满 40 个，排在第 41 个及之后的子区域会被预算截断。

2. 单页候选上限

   discovery 阶段还会按 `max_regions_per_page` 控制每页最多进入后续流程的区域数量。如果某页文字/标注非常密集，即使多个区域形态像文字，也只会保留优先级靠前的一部分。

3. 全局候选上限

   整份 PDF 还会受 `max_regions` 控制。多页图纸时，前面页面或高分区域可能占用预算，导致后面页面的低优先级但真实有效文字被延后或截断。

4. 标注图只展示最终保留结果

   当前标注图主要用于查看最终进入候选计划的区域，以及部分 rejected 区域。没有标注不一定代表系统完全没看到，也可能是：

   - 中间发现过，但被 Top N 截断
   - 被归入 rejected
   - 被大块拆分上限截断
   - 被单页/全局预算截断

这说明当前 MVP 的目标是“有限预算下优先找高价值区域”，不是“全量标注所有文字”。因此，数量上限会提升速度，但也会带来材料文字召回风险。

后续优化方向：

1. 在 summary 中显式输出是否 hit cap

   例如：

   - `large_region_split_hit_cap=true`
   - `page_region_hit_cap=true`
   - `global_region_hit_cap=true`

2. 输出被截断候选的审阅清单

   把第 41 个之后的候选单独写入 `text_region_overflow.json/csv`，用于人工判断是否截掉了有效材料文字。

3. 对不同类型设置独立预算

   不要让公司名、图名、尺寸数字挤占所有预算。可以为材料代号、右侧说明、材料表、节点说明、引线文字分别保留最小配额。

4. 引入低成本兜底 OCR

   对 overflow 区域不做 64x 高清 OCR，但可以做低 scale 快速 OCR 或抽样 OCR，用于判断是否需要升级。

### 问题 3：为什么有些 rejected 区域 OCR 后发现其实有效，说明误拒绝恢复还要优化？

结论：因为 rejected 只代表“中清图形态规则认为它不像稳定文字候选”，不代表“真实 OCR 后没有价值”。当 rejected 区域经过高清渲染和 PaddleOCR 后得到高质量文本，就说明前置 discovery 规则把一部分有效文字误杀了。

本轮审阅样本中出现了这种情况：

- `neg_003_tr_raw_001_1132` 原本来自 rejected/疑似噪声，但 OCR 质量分为 `0.969385`，被系统判为 high。
- `neg_001_tr_raw_001_1109` 原本来自 rejected/疑似噪声，但 OCR 质量分为 `0.798119`，被系统判为 high。
- `neg_006_tr_raw_001_1091` 原本来自 rejected/疑似噪声，但 OCR 质量分为 `0.724118`，被系统判为 high。

这说明 rejected 中不全是噪声，里面混有真实有效文字。

造成误拒绝的典型原因：

1. 有效文字被线条、边框、填充包围

   CAD 图纸中文字经常贴着线框、图例、节点、尺寸线或填充区域。中清图规则可能看到的是“线条/填充占比高”，于是标记为 `line_dominant`、`too_dense_possible_fill_or_hatch` 或 `split_dense_hatch_noise`，但高清 OCR 后文字仍然可读。

2. 有效文字区域太小或太碎

   图名、材料代号、轴号附近的小字可能被拆成很小的区域。规则可能因为 `split_candidate_too_small`、`too_few_split_text_fragments`、`single_component_stroke` 将其拒绝，但 OCR 放大后能读出有意义文本。

3. 标题、图名、说明文字和噪声形态相似

   一些图名、比例、节点编号、说明文字在形态上和尺寸线、轴号、符号混在一起。前置规则无法仅凭形态判断其语义价值。

4. 中清发现层没有 OCR 证据

   discovery 阶段只看图像特征，不看 OCR 文本。它只能保守猜测“像不像文字”，而 OCR 后才知道这个区域是否真的有中文、材料代号、尺寸或关键词。

因此，误拒绝恢复要优化的目标不是“让所有 rejected 都进 OCR”，而是：

- 从 rejected 中找出最可能被误杀的有效文字；
- 给它们一个低成本复核机会；
- 如果 OCR 后证明有效，就反向修正规则和排序。

后续优化方向：

1. 为 rejected 增加“可复核候选”分层

   不把 rejected 简单当作噪声，而是拆成：

   - hard_noise：明确线条/无文本/单符号
   - recoverable_text_like：可能被误杀的小字、图名、材料代号、说明文字
   - too_large_needs_split：需要继续拆分的大块 CAD 区域

2. 对 recoverable_text_like 做低成本 OCR 抽样

   不做全量 64x，只对 Top K recoverable rejected 区域做低 scale 或限量高清 OCR。

3. 把 OCR 证明有效的 rejected 重新喂给 discovery

   如果某类 rejected 区域多次 OCR high，就应该降低它的拒绝强度，或把它转为“兜底召回类型”。

4. 把 OCR 证明无效的 rejected 作为负样本

   真正 no_text、符号、单字、乱码的区域用于负向降权，减少下一轮噪声。

### 问题 4：为什么 OCR 读出的文字还没有在这一层直接变成最终报价材料清单？

结论：因为当前这一层的职责是“找到并评估 OCR 文字证据”，不是“完成报价材料结构化”。OCR 输出只是原始文字证据，距离最终报价材料清单还差语义分类、跨区域归并、去重、工程量/单位识别、报价口径判断等步骤。

OCR 结果不能直接等于报价材料清单，主要原因如下：

1. OCR 文本里包含大量非报价信息

   OCR 会读出很多文字，包括：

   - 公司名
   - 图名
   - 图号
   - 比例
   - 轴号
   - 尺寸数字
   - 设计单位
   - 标题栏
   - 材料代号
   - 节点说明
   - 做法说明

   这些里面只有一部分和报价材料有关。OCR 层只知道“读到了文字”，不知道哪些该进入报价。

2. OCR 文本粒度和报价清单粒度不同

   OCR 输出通常是一行行文本或一个个小块，例如：

   - `CT-01`
   - `600x1200`
   - `墙面`
   - `乳胶漆`

   但报价材料清单需要的是结构化条目，例如：

   ```text
   项目名称：墙面乳胶漆
   项目特征：基层处理、批腻子、刷乳胶漆
   单位：m2
   工程量：待图纸/清单确认
   证据：来源页码、区域、OCR 文本
   ```

   单条 OCR 文本往往不足以构成一个报价项目。

3. 材料信息经常分散在多个区域

   一个完整材料候选可能需要合并多个证据：

   - 图中标注：`CT-01`
   - 材料表：`CT-01 600x1200 灰色地砖`
   - 平面区域：餐厅地面
   - 做法说明：水泥砂浆结合层

   当前 OCR 质量层只处理单 crop 的文字质量，还没有完成这些跨区域、跨页、跨表格的归并。

4. 有效 OCR 不一定是报价有效

   例如本轮高质量 OCR 中包含：

   - `宏发建设`
   - `HONGFA CONSTRUCTION`
   - `PLAN`
   - `SCALE`
   - `3600`
   - `3900`

   这些 OCR 很清楚，但不一定是报价材料。当前质量分只证明“文字读得出来”，不证明“应该进入报价清单”。

5. 报价材料需要分类和口径判断

   最终报价候选至少要判断：

   - 是材料还是构件？
   - 是做法还是图名？
   - 是拆除项还是新建项？
   - 是设备还是装饰材料？
   - 是尺寸信息还是工程量信息？
   - 是材料代号还是普通编号？
   - 是否已经在其它区域重复出现？

   这些需要规则 + LLM 语义分类 + 证据归并，不适合直接在 OCR 质量层完成。

6. 最终报价清单还需要去重和可信度

   同一个材料可能在多个 crop 中反复出现。直接把 OCR 行变成清单，会产生大量重复、碎片和误报。必须先做：

   - 同义归并
   - 材料代号归并
   - 页码/区域证据合并
   - 置信度评分
   - 人工复核标记

当前合理分层应该是：

```text
OCR 质量层
  负责：哪些区域文字可靠，哪些是噪声

OCR 证据结构化层
  负责：把文字变成可追溯证据行

LLM/规则语义层
  负责：判断材料、构件、做法、拆除、设备、图名、尺寸等类别

证据归并层
  负责：合并材料代号、说明、表格、节点引用

报价候选层
  负责：输出可人工确认的材料/项目候选
```

因此，当前 OCR 层不能直接输出最终报价材料清单。它应该输出的是“可靠 OCR 证据”和“噪声过滤结果”，再交给后续语义归并链路生成材料候选。

### 问题 5：判断信号是否应该中文化？

结论：应该中文化。

内部程序字段可以继续保留英文，例如 `ocr_quality_score`、`text_density`、`component_count`、`quality_flags`，这样便于代码稳定和数据追溯。但所有面向人工审阅的报告、CSV 和验收材料，都应该提供中文含义。

需要中文化的内容包括：

- OCR 质量标签：`high / medium / low / no_text`
- 样本来源：`expected_effective_text / expected_noise_or_no_text`
- 区域类型：`colored_text_or_callout / text_block / split_noise`
- 页面区域：`main_drawing / right_notes / bottom_title`
- 判断信号：文字密度、小组件数、线条占比、材料代号数、尺寸命中数、噪声行数等
- 拒绝/质量标记：`single_component_stroke`、`line_dominant`、`split_candidate_too_small` 等

输出原则：

```text
中文说明为主
英文 code 放在括号中保留
```

示例：

```text
质量标签：高质量有效文字 (high)
区域类型：彩色文字/引线标注 (colored_text_or_callout)
页面区域：右侧说明区 (right_notes)
区域拒绝/质量标记：来自大块区域二次拆分 (split_from_too_large_region)
```

这样既方便人工审阅，也保留了程序可追溯性。

## 阶段一实现记录：OCR 调度稳定化 MVP

本轮阶段一已开始落地，目标不是直接输出最终报价材料清单，而是先让系统说清楚：

- 哪些区域进入本轮 OCR 计划。
- 哪些区域被预算截断，没有进入本轮 OCR。
- 哪些 rejected 区域可能是误拒绝，需要后续召回或复核。
- 哪些区域更像明确噪声，可以作为负样本压低优先级。
- OCR 质量审阅报告里，判断信号必须同时给出中文含义和原始 code。

已实现内容：

1. discovery 输出新增 `text_region_overflow.json` / `text_region_overflow.csv`

   当候选区域超过单页预算、全局预算，或大块 CAD 区域二次拆分后超过保留上限时，系统不再静默丢弃，而是写入 overflow 文件。这样可以明确知道“不是没发现”，而是“发现了但本轮预算没有排上”。

2. rejected / overflow 分层

   当前分层包括：

   - `hard_noise`：明确噪声。
   - `recoverable_text_like`：可能被误拒绝的文字。
   - `too_large_needs_split`：需要继续拆分的大块区域。
   - `low_priority_text_like`：低优先级文字候选。

   面向人工审阅的 CSV 同时输出中文字段，例如“明确噪声”“可能被误拒绝的文字”“需要继续拆分的大块区域”“低优先级文字候选”。

3. OCR 质量审阅抽样扩展为四类样本

   `biz2x_ocr_quality_sample_review.py` 现在可以同时抽样：

   - `expected_effective_text`：预计有效文字区域。
   - `recoverable_rejected_text_like`：可能被误拒绝的 rejected 区域。
   - `expected_noise_or_no_text`：预计噪声/无文本区域。
   - `overflow_budget_cut`：预算截断 overflow 区域。

   这能帮助人工同时审阅“系统优先选了什么”“系统拒绝了什么”“系统没预算 OCR 什么”，而不是只看已入选区域。

4. 真实 PDF 一页 smoke 结果

   输入：`tmp/xinda_staff_canteen_drawing.pdf`

   输出目录：

   - `outputs/biz2x_trial/text_region_discovery/20260624_stage1_overflow_probe`
   - `outputs/biz2x_trial/ocr_quality_review/20260624_stage1_review_plan_smoke`

   discovery 结果：

   - 入选 OCR 计划区域：8 个。
   - rejected 区域：82 个。
   - overflow 区域：73 个。
   - rejected 中“可能被误拒绝的文字”：14 个。
   - rejected 中“明确噪声”：67 个。
   - rejected 中“需要继续拆分的大块区域”：1 个。
   - overflow 中“低优先级文字候选”：73 个。

   审阅计划 smoke 结果：

   - 预计有效文字区域：2 个。
   - 可能被误拒绝的 rejected 区域：2 个。
   - 预计噪声/无文本区域：2 个。
   - 预算截断 overflow 区域：2 个。
   - highres crop 渲染：8 个全部成功。

5. 当前阻断点

   本轮真实 OCR 小样本运行时，PaddleOCR 初始化被本地模型文件权限挡住：

   ```text
   Permission denied:
   AI_Middle_Office/runtime/paddlex_cache/official_models/PP-LCNet_x1_0_textline_ori/inference.yml
   ```

   因此本次 smoke 已验证 discovery、overflow、抽样计划、highres crop 和中文审阅报告链路；真正 OCR 质量得分需要在修复本地 PaddleOCR 模型文件权限后重跑。

验收口径：

- 能看到 `text_region_plan.json`：本轮优先 OCR 哪些区域。
- 能看到 `text_region_rejected.json`：哪些区域被规则拒绝，以及属于哪一层。
- 能看到 `text_region_overflow.json`：哪些区域因预算未进入本轮 OCR。
- 能看到 `ocr_quality_review_plan.csv`：四类审阅样本及中文说明。
- 能看到 `ocr_quality_review.md`：中文判断口径、中文信号对照和 crop 路径。
- 聚焦测试通过：阶段一区域发现、renderer、OCR 质量评分、四类审阅抽样。

### 2026-06-25 真实 OCR 小样本复跑结果

已将 PaddleOCR 缓存切换到 D 盘干净目录：

```text
D:\AI_Middle_Office_Runtime\paddlex_cache_clean
```

真实 OCR 小样本输出目录：

```text
outputs/biz2x_trial/ocr_quality_review/20260625_stage1_real_ocr_smoke
```

本次结果：

- OCR 状态：`completed`
- crop 数：8
- OCR 完成 crop 数：8
- OCR 文本行数：47
- 高质量文字：0
- 中等质量可疑文字：1
- 低质量文字/噪声：6
- 无 OCR 文本：1
- 反馈画像正样本：0
- 反馈画像负样本：7

人工审阅口径下，本轮样本说明：

- `pos_002_tr_001_0002` 被判为中等质量可疑文字，读出了“宏发建设”“宏发建设有限公司”“25800”“3900”“4200”等内容，但这些更像图签/轴网/尺寸信息，不是材料文字。
- `pos_001_tr_001_0001` 是预计有效文字区域，但 OCR 未读出文本，说明当前“预计有效”的发现规则仍需更多样本校准。
- 两个“可能被误拒绝”的 rejected 区域主要读出 `A/B/C/I/1/2/3900` 等轴号、数字或单字符，当前看更像低价值文字/噪声，不应直接召回为材料文字。
- 两个“预计噪声/无文本区域”被 OCR 后仍是 `A/B/2`、`B/1/C/1` 之类单字符和轴号，噪声判断基本正确。
- 两个“预算截断 overflow 区域”读出少量轴号和尺寸，例如 `3900`、`4200`、`2100`，当前不属于高价值材料文字。

随后将真实 OCR 反馈画像回灌 discovery，输出目录：

```text
outputs/biz2x_trial/text_region_discovery/20260625_stage1_feedback_probe
```

回灌结果：

- OCR feedback 已启用。
- 正样本数：0。
- 负样本数：7。
- 入选区域命中负样本形态：0。
- rejected 区域命中负样本形态：67。

这说明本轮反馈主要用于降噪：系统已经能识别一批“形态像文字但 OCR 后低价值”的 rejected 区域，并将这些形态作为负样本压低；但由于本轮没有高质量材料文字正样本，还不能证明“有效材料文字召回”已经稳定。

### 2026-06-25 彩色图签/说明块候选补强

进一步查看真实渲染页后发现，当前 PDF 实际是一张多图框拼版页：一页里排布了很多小图，每个小图右侧或底部有绿色/黄色图签、材料表或说明块。此前 discovery 直接在整页上找小字，容易优先抓到轴号、尺寸和图签数字，材料表区域可能落入 overflow。

因此阶段一补强了一个旁路候选来源：

```text
candidate_source = colored_annotation_cluster
```

判断方式：

- 在中清图上提取高饱和度的绿色、黄色、红色、紫色 CAD 标注像素。
- 排除主要青色图框线。
- 对彩色像素做小核聚类，形成“彩色图签/说明块”候选。
- 给这类候选少量优先级加成。
- 在 `text_region_candidates.csv` 中输出 `candidate_source`，方便人工筛选。

真实 discovery 输出目录：

```text
outputs/biz2x_trial/text_region_discovery/20260625_stage1_colored_annotation_probe
```

本次结果：

- 入选 OCR 计划区域：40 个。
- 入选区域全部为 `colored_text_or_callout`。
- `candidate_source=colored_annotation_cluster` 的区域进入前排候选。
- overflow 区域：159 个。
- 说明本页的可 OCR 候选数量远超预算，后续仍需要预算排序和多轮抽样。

随后跑了一个较小的真实 OCR 审阅样本，输出目录：

```text
outputs/biz2x_trial/ocr_quality_review/20260625_stage1_colored_annotation_ocr_smoke_small
```

本次结果：

- OCR 状态：`completed`
- 样本数：7
- OCR 完成 crop 数：7
- OCR 文本行数：112
- 高质量有效文字：1
- 中等质量可疑文字：4
- 低质量文字/噪声：2
- 无文本：0
- 反馈画像正样本：1
- 反馈画像负样本：1

其中关键正样本：

```text
ovf_001_tr_raw_001_0053
质量：高质量有效文字
来源：预算截断 overflow 区域
OCR 预览：
图纸名称
职工餐厅平面布置图
图列
材料名称
白色墙砖400×800横贴
```

这个结果说明：

- 之前被预算截断的 overflow 中确实存在高价值材料表文字。
- “彩色图签/说明块候选”是拼版 CAD 图纸上的有效正样本来源。
- 预算排序不能只看文字形态，还要给材料表/图签块足够机会。

随后将这次正/负反馈回灌 discovery，输出目录：

```text
outputs/biz2x_trial/text_region_discovery/20260625_stage1_colored_feedback_probe
```

回灌结果：

- OCR feedback 已启用。
- 正样本数：1。
- 负样本数：1。
- 入选区域命中正样本形态：34。
- rejected 区域命中正样本形态：10。
- 入选区域命中负样本形态：0。

这说明“正向提权”已经开始工作：系统已经能从一个高质量材料表样本中学习到类似彩色图签/说明块形态，并在下一轮 discovery 中提高同类区域优先级。

当前新的阶段一结论：

- 对多图框拼版 CAD 页，必须先照顾彩色图签/说明块候选。
- 仅靠整页小字发现，会把材料表区域挤到 overflow。
- overflow 不是废弃区，其中可能有高价值材料表文字。
- 下一步应做“预算内多样性”：Top N 不能全被尺寸/轴号或同类图签占满，应按候选来源、图面位置、OCR 历史质量进行分桶保留。

### 2026-06-25 预算内多样性调度 MVP 与验收

本轮已实现“预算内多样性调度”MVP。它解决的问题是：当一页 CAD 拼版图纸上可疑文字区域远多于 OCR 预算时，不能只按分数从高到低截断，否则容易被同一种候选占满，例如轴号、尺寸、标题栏或局部图签，导致材料表/说明块被挤到 overflow。

当前调度桶：

- `OCR 正反馈相似区域 (ocr_positive_feedback)`：OCR 历史正样本证明类似形态曾产出有效文字，本轮优先保留。
- `彩色图签/材料表候选 (colored_annotation)`：彩色图签、材料表或说明块候选，拼版 CAD 页中可能包含材料名称、图例和做法说明。
- `右侧说明/做法文字候选 (right_notes)`：位于说明区的文字块，可能包含节点说明、材料做法或图例。
- `大块 CAD 二次拆分小字 (large_region_split)`：来自大块 CAD 区域二次拆分的小字召回，避免大块区域被一次性误拒绝。
- `主图文字/引线标注 (main_drawing)`：主图内房间、节点、引线和材料代号证据。
- `综合高优先级兜底 (fallback_high_priority)`：没有明确归类但综合优先级高的候选。

输出层面新增/确认的中文判断信号：

- `budget_bucket_cn`：预算调度分类中文名。
- `budget_reason_cn`：为什么这个区域属于该类，以及为什么值得保留。
- `budget_decision_cn`：本轮“预算内保留”还是“预算不足，进入 overflow”。
- `selected_budget_bucket_counts`：本轮实际进入 OCR 计划的各类数量。
- `overflow_budget_bucket_counts`：已发现但被预算截断的各类数量。

真实图纸验收输入：

```text
tmp/xinda_staff_canteen_drawing.pdf
```

真实图纸验收输出：

```text
outputs/biz2x_trial/text_region_discovery/20260625_stage1_budget_diversity_probe_v2
```

验收结果：

- 本轮 OCR 预算：40 个区域。
- 实际进入 OCR 计划：40 个区域。
- overflow：159 个区域。
- rejected：84 个区域。
- `budget_diversity_enabled=true`。
- 入选分桶：`OCR 正反馈相似区域=30`，`彩色图签/材料表候选=10`。
- overflow 分桶：`OCR 正反馈相似区域=68`，`彩色图签/材料表候选=18`，`大块 CAD 二次拆分小字=73`。
- `text_region_candidates.csv` 中 40 行入选区域均已有中文预算判断信号。
- `text_region_overflow.csv` 中 159 行预算截断区域均已有中文预算判断信号。

本轮验收结论：

- 系统已经能明确说明“哪些区域预算内优先 OCR”“哪些区域发现了但被预算截断”“被截断区域属于哪类候选”。
- 预算截断不再是静默丢弃，而是进入 `text_region_overflow.csv/json`，可人工复核。
- 当前真实页由于正样本来自彩色材料表区域，所以预算内区域仍主要集中在 `OCR 正反馈相似区域` 和 `彩色图签/材料表候选`；这说明正反馈正在生效，但也提示后续需要更多不同类型的正样本，让 `right_notes`、`main_drawing`、`large_region_split` 的预算保留更稳定。

当前边界：

- `rejected` 是规则拒绝层，不是预算截断层，因此主要看 `rejected_layer_cn` 和 `rejected_layer_reason_cn`，而不是预算桶。
- 本轮实现的是 OCR 前的区域调度，不是最终报价材料清单归并。
- 下一步若要继续提升找全率，应让 OCR 执行计划从 selected 区域之外，按小比例抽样一部分 `overflow` 和 `recoverable_rejected_text_like`，形成“预算内主路径 + 少量兜底复核”的闭环。

### 2026-06-25 预算内主路径 + 少量兜底 OCR 执行计划

本轮已实现正式的预算 OCR 执行计划，不再只把 `text_region_plan.json` 里的 selected 区域直接交给 OCR，而是按总预算拆成：

- 主路径 selected 区域：大部分预算。
- overflow 兜底区域：少量预算，用来验证“发现了但被预算截断”的候选。
- recoverable rejected 兜底区域：少量预算，用来验证“可能被误拒绝”的候选。

新增产物：

```text
AI_Middle_Office/app/services/drawing_ocr_budget_scheduler.py
AI_Middle_Office/scripts/biz2x_budgeted_ocr_execution_plan.py
```

40 名额真实图纸执行计划验收：

```text
outputs/biz2x_trial/ocr_execution/20260625_stage1_budgeted_execution_plan
```

结果：

- OCR 总预算：40。
- 主路径 selected：32。
- 可能误拒绝兜底：3。
- overflow 兜底：5。
- 输入 selected 候选：40。
- 输入 recoverable rejected 候选：14。
- 输入 overflow 候选：159。
- 最终执行计划：40 个区域。
- 兜底区域：8 个。
- `budgeted_ocr_execution_plan.csv` 中每行都有中文执行桶、中文执行原因和中文预算决策。

12 名额端到端 OCR smoke：

```text
outputs/biz2x_trial/ocr_execution/20260625_stage1_budgeted_execution_ocr_smoke
```

结果：

- OCR 总预算：12。
- 主路径 selected：8。
- 可能误拒绝兜底：2。
- overflow 兜底：2。
- highres crop：12/12 成功。
- PaddleOCR：12/12 完成。
- OCR 文本行：280。
- 高质量 OCR crop：6。
- 中等质量 OCR crop：4。
- 低质量 OCR crop：2。
- no_text：0。

按来源看：

- 主路径 selected：8 个样本中 6 个 high、2 个 medium，说明主路径当前确实能读出大量有效图纸文字。
- overflow 兜底：2 个样本均为 medium，其中一个彩色图签/材料表候选读出了中文和材料关键词，说明 overflow 兜底是必要的。
- recoverable rejected 兜底：2 个样本均为 low，主要是 `A/B/C/1/2/3900` 这类轴号或数字，说明当前这批“可能误拒绝”暂时不应大规模召回，只适合作少量复核样本。

本轮结论：

- 系统已经从“只跑预算内 selected”升级为“主路径 + 少量兜底复核”。
- overflow 兜底能降低材料表/图签文字被预算截断后完全漏掉的风险。
- rejected 兜底当前主要用于验证误拒绝，不应过早扩大比例。
- 后续应把 OCR 结果继续回灌 discovery：overflow 兜底若多次产出 medium/high，应提高同类区域预算；recoverable rejected 若持续 low，应进一步降低同类误召回。

### 2026-06-25 候选区域中文判断说明

本轮已为每个候选区域补充统一的中文判断说明，目标是让人工打开 CSV 后能直接看懂系统为什么选、为什么没选、风险是什么、下一步要做什么。

新增字段：

- `candidate_decision_cn`：本轮候选决策，例如“入选：预算内主路径 OCR”“未入选主路径：预算截断，进入 overflow”“未入选：规则拒绝，但可能误拒绝”。
- `candidate_reason_cn`：一句话主原因，解释为什么这样判断。
- `candidate_signal_cn`：关键判断信号，汇总预算分类、拒绝层级、页面位置、区域类型、OCR 反馈、尺寸、文字密度、小组件数量和关键标记。
- `candidate_risk_cn`：风险提示，例如可能是材料表/图例，也可能只是轴号、尺寸、图签碎片或噪声。
- `next_action_cn`：下一步动作，例如“直接 OCR”“兜底 OCR”“暂不 OCR”“继续二次拆分或抽样复核”。

覆盖文件：

- `text_region_candidates.csv`
- `text_region_overflow.csv`
- `text_region_rejected.csv`
- `budgeted_ocr_execution_plan.csv`

真实图纸 discovery 验收输出：

```text
outputs/biz2x_trial/text_region_discovery/20260625_stage1_candidate_reason_probe
```

验收结果：

- selected：40 行，五个中文判断字段均无空值。
- overflow：159 行，五个中文判断字段均无空值。
- rejected：84 行，五个中文判断字段均无空值。

真实图纸预算 OCR 执行计划验收输出：

```text
outputs/biz2x_trial/ocr_execution/20260625_stage1_candidate_reason_execution_plan
```

验收结果：

- OCR 执行计划：40 行。
- 主路径 selected：32 行。
- 可能误拒绝兜底：3 行。
- overflow 兜底：5 行。
- 五个中文判断字段均无空值。

本轮结论：

- 阶段一已经具备“可人工审阅”的基础：不仅知道区域是否入选，还知道系统为什么这么判断。
- 后续人工验收时，可以优先看 `candidate_decision_cn`、`candidate_reason_cn`、`candidate_signal_cn`、`candidate_risk_cn`、`next_action_cn` 五列，而不是先看英文 code 和数字特征。
- 下一步应把 OCR 后的质量标签和材料语义命中情况继续写回同一套中文说明，形成“发现理由 -> OCR 结果 -> 是否有效材料文字”的闭环。

### 2026-06-25 OCR 结果有效性中文审阅闭环

本轮已完成阶段一后半段的“发现理由 -> OCR 结果 -> 是否有效材料文字/噪声 -> 下轮调度建议”闭环。它仍然属于 OCR 调度层，不做最终报价材料清单归并。

新增产物：

```text
AI_Middle_Office/app/services/drawing_ocr_result_reviewer.py
AI_Middle_Office/scripts/biz2x_ocr_result_review.py
```

新增输出：

- `ocr_result_review.csv`
- `ocr_result_review.json`
- `ocr_result_review.md`
- `ocr_result_review_summary.json`
- `ocr_result_business_review.csv`
- `ocr_result_business_review.md`

核心中文字段：

- `ocr_result_summary_cn`：OCR 实际读出了什么，包括文本行数、有效行、中文数、材料代号、尺寸和文本预览。
- `ocr_quality_label_cn`：高质量有效文字 / 中等质量可疑文字 / 低质量文字噪声 / 无 OCR 文本。
- `ocr_effectiveness_label_cn`：有效材料/做法文字、可能有效材料文字、有效但非材料优先文字、可疑文字、低价值文字/噪声等。
- `material_signal_cn`：命中的材料/做法关键词、材料代号、尺寸、图例/材料表线索，以及非材料弱信号。
- `noise_reason_cn`：为什么像噪声或为什么不适合做材料正样本。
- `feedback_action_cn`：下一轮调度建议，例如提权 overflow、降低误拒绝兜底比例、保留为有效图纸文字但不作为材料正样本。

业务员默认审阅简表：

为了避免人工验收时被技术字段干扰，本轮新增“业务审阅简表”。它只保留 6 类必要信息：

- 系统结论：有效材料/做法文字、有效但非材料优先文字、低价值文字/噪声等。
- 建议处理：纳入材料/做法候选、暂不纳入材料候选、作为噪声降权、人工复核等。
- 识别到的文字：优先展示命中材料/做法关键词的 OCR 文本，再补充上下文。
- 系统判断依据：用中文解释为什么这么判。
- 人工确认：留空给业务员填写正确 / 误判 / 不确定。
- 截图路径：用于必要时查看 crop 原图。

完整 `ocr_result_review.*` 仍保留给研发排查和规则调试；业务验收优先看 `ocr_result_business_review.csv` 或 `ocr_result_business_review.md`。

如需让业务员在固定目录查看截图，可传入 D 盘截图导出目录。系统会复制 crop 截图，并把简表里的“截图路径”改成 D 盘路径，避免业务员打开 C 盘工作区内部路径：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_ocr_result_review.py `
  --execution-run-dir outputs\biz2x_trial\ocr_execution\20260625_stage1_budgeted_execution_ocr_smoke `
  --output-dir outputs\biz2x_trial\ocr_execution\20260625_stage1_business_review_simple_d_drive `
  --business-screenshot-dir D:\AI_Middle_Office_Runtime\biz2x_review_assets\20260625_stage1_business_review_simple
```

真实 OCR smoke 审阅输入：

```text
outputs/biz2x_trial/ocr_execution/20260625_stage1_budgeted_execution_ocr_smoke
```

真实 OCR 结果审阅输出：

```text
outputs/biz2x_trial/ocr_execution/20260625_stage1_ocr_result_review_probe_v3
```

业务审阅简表试生成输出：

```text
outputs/biz2x_trial/ocr_execution/20260625_stage1_business_review_simple
```

D 盘截图路径版试生成输出：

```text
outputs/biz2x_trial/ocr_execution/20260625_stage1_business_review_simple_d_drive
D:\AI_Middle_Office_Runtime\biz2x_review_assets\20260625_stage1_business_review_simple
```

验收结果：

- 审阅区域：12 个。
- 主路径 selected：8 个。
- overflow 兜底：2 个。
- 可能误拒绝兜底：2 个。
- OCR 质量标签：high 6 个、medium 4 个、low 2 个、no_text 0 个。
- OCR 有效性判断：有效材料/做法文字 2 个，有效但非材料优先文字 8 个，低价值文字/噪声 2 个。

关键发现：

- overflow 兜底确实有价值：其中 `ocro_001_tr_raw_001_0113` 被判断为“有效材料/做法文字”，命中“墙面”等材料/做法关键词，并建议下轮提高同类 overflow 区域优先级。
- recoverable rejected 兜底目前价值较低：2 个样本均为低价值文字/噪声，主要是 `A/B/C/1/2/3900` 这类轴号、数字或碎片字符，因此建议下轮降低同类误拒绝兜底比例。
- 主路径中有大量“有效但非材料优先文字”：例如设计单位、证书号、公司信息、标题栏等。它们是 OCR 有效文字，但不应作为材料正样本，后续语义分类时应降低材料权重。
- 材料正样本判断已收紧：只有明确命中材料/做法关键词、图例/材料表线索或更强材料证据的区域，才进入“有效材料/做法文字”；单纯大量尺寸或疑似代号不再直接提为材料正样本。

本轮结论：

- 阶段一已经能回答“哪些 OCR 结果有效、哪些是噪声”，并给出中文原因。
- 当前不应继续扩大最终材料清单归并，而应先用这份审阅表做人工验收，确认系统对“材料文字 / 非材料有效文字 / 噪声”的判断是否符合业务直觉。
- 下一步如果继续推进阶段一，应把 `feedback_action_cn` 结构化回灌到 discovery 权重，形成自动提权/降权闭环。
