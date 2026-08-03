# Bid-intake Agent Retrieval Experiment Ledger v1

本台账是检索优化的可读索引。原始预测、JSON报告和私有逐题分析才是指标事实源；本文件只记录脱敏后的实验结论。

## 强制实验规则

每次优化必须记录：

1. 唯一实验ID、执行时间和代码/配置版本；
2. 要解决的问题和可验证假设；
3. 唯一自变量，即本次到底改变了什么；
4. 保持不变的模型、数据集指纹、Top K、切块、路由和融合配置；
5. Hit@K、Recall@K、Precision@K、MRR、nDCG@K；
6. 路由准确率、Query数量准确率和主题召回率；
7. 平均延迟、P95延迟和执行错误数；
8. 改善样本、回退样本及关键失败类型；
9. 相对基线的指标变化；
10. 最终决定：保留、拒绝或继续验证。
11. 结构化推理记录：
    - `observation`：客观观察到了什么；
    - `evidence`：哪些指标或样本支持观察；
    - `inference`：由证据得到什么工程判断；
    - `alternatives`：还有哪些可能解释；
    - `falsifiable_hypothesis`：下一实验要验证、且可能被推翻的假设；
    - `decision_rationale`：为什么保留或拒绝本次改动。

以下结果禁止比较：

- 数据集指纹不同；
- 同时改变两个及以上关键变量；
- 一个实验使用Development，另一个使用Holdout；
- 保存或评估口径发生变化却没有显式记录；
- 根据Holdout失败样本继续调参。

## 通用性硬约束

本项目的优化目标是“招投标资料研判领域内可泛化”，不是修复某一道评测题。候选改动必须同时满足：

- 不允许写入项目名称、题目原句、Gold Evidence ID或只对单一样本成立的关键词组合；
- 优先优化通用能力层，例如原子事实拆分、结构化字段识别、候选召回、覆盖约束和重排；
- 接受候选前，至少应改善两个不同问法的样本；若是专项业务策略，还必须覆盖不同项目或独立Challenge Set；
- 每轮报告必须同时展示总体指标和按项目、路由、难度、标签的分层指标，防止平均分掩盖局部回退；
- 若主要收益只来自一个样本，则只能记为“局部信号”，不能启用；
- Holdout只用于候选冻结后的最终盲测，不能根据Holdout结果继续改规则。

## 默认通过门槛

- Mean Recall@5：相对基线至少提高0.03；
- MRR：不得下降；
- nDCG@5：不得下降；
- P95延迟：增幅不超过15%；
- 执行错误：不得增加；
- 明确列出所有回退样本，不允许只看平均分；
- 候选版本确定前不得运行Holdout。

## 实验记录

| 实验ID | 唯一变化 | Hit@5 | Recall@5 | MRR | nDCG@5 | P95延迟 | 回退 | 决定 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| RET-BASELINE-001 | 无，建立基线 | 0.8000 | 0.7250 | 0.7375 | 0.7035 | 1197ms | 0 | Baseline |
| RET-EXP-001 | 每路候选深度5→20，最终Top5不变 | 0.8500 | 0.7750 | 0.7875 | 0.7535 | 1288ms | 0 | 保留 |
| RET-EXP-002 | 单主题风险问题增加1条宽泛事实支撑Query | 0.9000 | 0.7917 | 0.8000 | 0.7636 | 1401ms | 0 | 未达门槛，继续验证 |
| RET-EXP-003A | 复合问句增加通用表面事实槽Query | 0.8500 | 0.7750 | 0.7875 | 0.7575 | 2646ms | 0 | 拒绝 |
| RET-EXP-003B | 现有候选池增加答案承载信号与需求覆盖选择 | 0.9500 | 0.8833 | 0.9250 | 0.8883 | 1294ms | 0 | 保留为Development候选 |
| RET-EXP-004A | 覆盖种子扩展同章节前后各1个直接邻居作为独立候选 | 0.9500 | 0.8833 | 0.9500 | 0.8996 | 1343ms | 0 | 拒绝：仅改善排序 |
| RET-EXP-004B | Top5锚点不变，合格直接邻居作为附属证据组成员 | 0.9500 | 0.9083 | 0.9250 | 0.8883 | 1303ms | 0 | 未达门槛：单项目Recall改善 |
| RET-HOLDOUT-003B | 冻结RET-EXP-003B后唯一一次未见项目盲测 | 0.8889 | 0.7778 | 0.8333 | 0.7423 | 1515ms | 不适用 | 整体未通过：nDCG与拒答门槛失败 |
| RET-FACT-GATE-001-DEV-A | 只增加事实槽状态与shadow充分性门，不改检索 | 0.8000 | 0.6500 | 0.5667 | 0.5858 | 982ms | 0 | 硬门未通过；保留shadow可观测性 |
| RET-STRUCT-CONTEXT-001-DEV-A | 为候选附着真实父章节/表头，不增加Query | 1.0000 | 0.7167 | 0.6333 | 0.6327 | 748ms | 0 | 拒绝：只改善1题 |
| RET-STRUCT-CONTEXT-002-DEV-A | 同一表格父节点下附着最多3条候选池兄弟子行 | 1.0000 | 0.9000 | 0.6333 | 0.6327 | 696ms | 0 | Development A通过；待新项目Development B |
| RET-CONTROLLED-RETRY-001-DEV-A | 部分覆盖时补查，结果重新参与RRF与锚点选择 | 1.0000 | 0.9500 | 0.6667 | 0.5791 | 681ms | 0 | 拒绝：Recall升但nDCG退化 |
| RET-CONTROLLED-RETRY-002-DEV-A | 保留首轮锚点，补查只参与普通结构组选取 | 1.0000 | 0.9000 | 0.6333 | 0.6327 | 658ms | 0 | 拒绝：安全但无收益 |
| RET-CONTROLLED-RETRY-003-DEV-A | 保留首轮锚点，以补查结果指定真实父表并扩展兄弟行 | 1.0000 | 0.9500 | 0.6333 | 0.6327 | 687ms | 0 | Development A检索门通过；事实门仍shadow |
| RET-CANDIDATE-COVERAGE-004 | 共享普通Baseline Top5骨架；Candidate只修改其副本 | 0.9143 | 0.7305 | 0.8024 | 0.7046 | 1302ms | 0 | 拒绝：收益、保留率与跨项目门失败 |
| RET-CANDIDATE-COVERAGE-005 | 谓词感知的边际事实收益与替换损失选择 | 0.9429 | 0.7662 | 0.8090 | 0.7237 | 1172ms | 0 | 拒绝：候选池顺序等价与Gold保留率门失败 |

## Baseline补充指标

| 指标 | 结果 |
|---|---:|
| Precision@5 | 0.1800 |
| 路由完全正确率 | 1.0000 |
| Query数量准确率 | 1.0000 |
| 主题召回率 | 1.0000 |
| 平均延迟 | 465.1ms |
| 执行错误 | 0 |
| Hard Recall@5 | 0.3333 |
| Hybrid Recall@5 | 0.0000 |
| Top5完全未命中样本 | 4 |
| Top5部分召回样本 | 3 |

## 当前结论

候选深度解耦和候选覆盖选择已经保留。RET-EXP-004B只带来单项目局部收益，继续追加关系同义词会沿单题路径逼近答案，因此原计划RET-EXP-004C取消。

冻结的RET-EXP-003B已经完成唯一一次未见项目Holdout盲测。正向检索Hit@5、Recall@5和MRR达到预设门槛，但nDCG@5为0.7423，低于0.75门槛；唯一负向样本没有拒答，Negative Accuracy为0。完整发布门槛未通过，候选只能表述为“正向证据检索条件性验证”，不能标记为生产就绪。

Holdout从此锁定，不重跑、不改标签、不用于调参。下一步不是继续修泰丰花园失败题，而是从全新项目建设含无结果、长表格和多条款问题的新Development/Challenge数据，再开启独立的通用能力实验。

## RET-BASELINE-001 推理记录

| 环节 | 记录 |
|---|---|
| Observation | 路由完全正确率、Query数量准确率和主题召回率均为100%，但Mean Recall@5只有72.5%。 |
| Evidence | 20题中4题完全未命中、3题部分召回；Hard Recall@5为33.33%，Hybrid Recall@5为0%。 |
| Inference | Agent已经能判断“应该采用哪种检索方式、是否需要拆Query”，但当前候选召回和融合排序没有把足够多的Gold Evidence送进最终Top5。 |
| Alternatives | Gold集合可能偏严格；长块或相邻表格字段可能造成证据粒度不一致；Embedding可能不适合合同风险语义；RRF可能缺少主题覆盖约束。 |
| Additional evidence | 候选深度20的Development诊断中，15个目标证据出现10个；其中一个Hybrid样本从Top5无命中变为候选深度20时排第1，但仍有5个目标证据未出现。 |
| Falsifiable hypothesis | 如果将每路候选深度从5提高到20、最终仍返回Top5，Recall@5应至少提高0.03且MRR不下降；否则“候选池过浅”不是主要或充分原因。 |
| Next experiment | `RET-EXP-001`：只解耦内部候选深度和最终Top K。 |
| Decision rationale | 当前仅建立基线，不改变实现；先用单变量实验验证推断，再决定是否保留候选深度优化。 |

## RET-EXP-001 推理记录

| 环节 | 记录 |
|---|---|
| Observation | 每路候选深度由5提高到20、最终Top5不变后，Hit@5从0.80升至0.85，Recall@5从0.725升至0.775，MRR从0.7375升至0.7875，nDCG@5从0.7035升至0.7535。 |
| Evidence | 1个样本从完全未命中改善为Gold排第1，0个样本回退；P95延迟从1197ms升至1288ms，执行错误仍为0。 |
| Inference | 内部候选池过浅确实是一个召回瓶颈，且将候选深度与最终TopK解耦能够以可接受延迟成本改善结果。 |
| Alternatives | 只改善1题说明候选池深度不是全部原因；其他失败仍可能来自Query表达、证据粒度、Embedding或融合覆盖。 |
| Falsifiable hypothesis result | 原假设得到支持：Recall@5提升0.05≥0.03，MRR未下降，P95增幅约7.6%≤15%。 |
| Decision | 保留，并将MCP运行入口的默认每路候选深度设为20；最终返回TopK仍由调用方控制。 |
| Next experiment | `RET-EXP-002`：只增加Hybrid风险问题的基础事实原子Query。 |

## RET-EXP-002 推理记录

| 环节 | 记录 |
|---|---|
| Observation | 增加1条宽泛事实支撑Query后，Hit@5从0.85升至0.90，但Recall@5只从0.775升至0.7917；Hybrid Recall从0.50升至0.6667，未达到1.0。 |
| Evidence | 1个样本从完全未命中改善为只召回3个Gold中的1个；该Gold排第4；0个样本回退。P95从1288ms升至1401ms，增幅约8.8%。 |
| Inference | “风险原问 + 事实支撑”方向有效，但单条宽泛付款Query只能提供部分覆盖，无法同时召回分散在预付款、进度款、结算和质保块中的完整证据。 |
| Alternatives | Top5容量可能限制多块覆盖；Gold按三个证据块标注可能较严格；支付条款可能需要相邻上下文扩展而不只是更多Query。 |
| Falsifiable hypothesis result | 部分支持但未通过：Recall增量0.0167低于0.05，Hybrid Recall为0.6667而非1.0；MRR、nDCG和延迟门槛通过。 |
| Decision | 不启用当前宽泛事实支撑方案。收益只来自一个付款样本，不能据此继续增加付款专用规则。 |
| Generalization review | 原计划`RET-EXP-002B`直接增加预付款、进度款、结算/质保三个固定Query，存在针对单一失败样本过拟合的风险，因此取消为下一实验。付款生命周期可保留为领域知识候选，但必须在独立Challenge Set中跨项目验证后才能进入通用Planner。 |
| Next experiment | `RET-EXP-003A`：从问句结构中抽取“对象 + 待求属性 + 正反约束”的原子事实槽，不硬编码付款主题；先验证它是否同时改善保证金、承包范围、递交要求、付款等不同类型问题。 |

## RET-EXP-003A 推理记录

| 环节 | 记录 |
|---|---|
| Observation | 通用表面事实槽在13个样本上触发并增加32次检索，但Hit@5、Recall@5、MRR均无变化；仅1个本来已完整召回的样本nDCG改善。 |
| Evidence | Recall增量为0；改善样本不足2个且Recall改善样本为0。平均延迟由480.5ms升至1127.1ms，P95由1288ms升至2646ms，增幅约105.4%。 |
| Generalization result | 规则确实跨两个项目和多个主题触发，但没有形成跨问法有效收益，因此“触发广”不等于“泛化好”，通用性门槛未通过。 |
| Candidate-pool diagnosis | 事实槽Top20中出现4个最终结果缺失的Gold，分布在3个样本；但RET-EXP-001原有主查询Top20中已经出现5个最终结果缺失的Gold，分布在5个样本。 |
| Inference | 当前主要瓶颈不是Query数量不足，而是已有正确候选没有被最终Top5的RRF与覆盖逻辑选中。继续增加Query既冗余又昂贵。 |
| Alternatives | 部分Gold仍未进入任何候选池；严格块级Gold和Top5容量也可能限制完整召回；后续覆盖选择必须防止低相关槽位平均分挤掉高相关证据。 |
| Falsifiable hypothesis result | 不支持：Recall没有提升、跨样本收益不足、P95远超15%门槛。 |
| Decision | 拒绝，不启用；代码仅作为默认关闭的实验能力保留，便于复现实验。 |
| Next experiment | `RET-EXP-003B`：不增加Query，只在RET-EXP-001已有候选池内验证可解释的原子需求/主题覆盖选择；与RET-EXP-001比较总体收益。 |

## RET-EXP-003B 推理记录

| 环节 | 记录 |
|---|---|
| Observation | 仅改变现有候选池的Top5选择后，Hit@5、Recall@5、Precision@5、MRR和nDCG@5均明显提升，未增加检索Query。 |
| Evidence | Recall由0.7750升至0.8833（+0.1083），MRR由0.7875升至0.9250，nDCG由0.7535升至0.8883；P95由1288ms升至1294ms，执行错误和回退样本均为0。 |
| Generalization result | 6个样本改善，其中4个Recall改善、2个仅排序改善；改善覆盖香港中心和深圳丰隆两个项目，以及金额、范围、时间地点、付款、工期、质量等不同事实类型。 |
| Mechanism | 对复合问题抽取通用需求类型，但不执行额外Query；在现有候选中优先选择同时具备主题匹配和答案承载信号的证据，例如金额值、日期/工期值、包括/不含、份数、付款方式或验收标准。 |
| Alternatives | Development只有20题，仍可能高估收益；答案信号规则可能对其他文档格式不完整；Hybrid风险题没有触发复合需求覆盖，仍是独立弱项。 |
| Falsifiable hypothesis result | 支持：Recall提升超过0.03，至少两个不同问法且跨项目改善，MRR/nDCG不降，P95增幅约0.47%，无回退。 |
| Decision | 保留为当前Development候选，并接入MCP环境配置；运行进程重启后生效。最终泛化结论仍需候选冻结后的Holdout盲测。 |
| Remaining failures | 4个样本未完整召回，其中3个为跨块问题；香港范围题和深圳递交题的缺失Gold分别与已返回Gold相邻一个block。 |
| Next experiment | `RET-EXP-004A`：不修改Query，仅验证同文档、同章节的相邻块候选扩展是否能通用修复跨块问题，并严格控制Top5替换与延迟。 |

## RET-EXP-004A 推理记录

| 环节 | 记录 |
|---|---|
| Observation | 相邻读取能够找到新的正确块并改善排序，但Recall@5保持0.8833，没有改善任何跨块问题的完整召回。 |
| Evidence | 13题产生18个种子并执行18次上下文读取，6题加入7个邻居，错误为0；MRR提升0.0250、nDCG提升0.0113，Recall增量为0。 |
| Mechanism diagnosis | 香港范围题的新邻居是Gold且升至第1名，但它作为独立候选挤出了原Top5中的另一条Gold，所以该题仍只命中2条Gold中的1条。深圳递交题加入2个邻居，但缺失Gold仍未进入最终Top5。 |
| Inference | 相邻关系适合作为上下文归组信号，不适合直接作为独立Top5竞争加分；检索锚点与证据上下文应分成两个层次。 |
| Alternatives | 覆盖评分可能没有识别某些表格/短块的答案承载信号；严格同章节过滤可能排除了解析器切错章节的真实邻居；Top5块级标注本身也限制了多块条款的容纳量。 |
| Falsifiable hypothesis result | 不支持：Recall未提高，跨块Recall改善数为0；MRR、nDCG、P95和错误门槛虽通过，仍不足以发布。 |
| Decision | 拒绝，不接入MCP运行配置；默认关闭的代码和完整实验产物保留用于复现。 |
| Next experiment | 验证“上下文证据组”：Top5按锚点计数，只有承载未覆盖需求的同章节直接邻居随锚点成组返回，避免兄弟块互相淘汰。至少跨香港中心和深圳丰隆两个项目改善才可保留。 |

## RET-EXP-004B 推理记录

| 环节 | 记录 |
|---|---|
| Observation | 证据组在完全不改变Top5锚点的情况下提高Recall，但只改善一个项目，未达到发布门槛。 |
| Evidence | Recall由0.8833提高到0.9083（+0.025）；香港范围题由0.5提高到1.0；MRR/nDCG不变，P95下降96ms，错误为0；20题锚点序列差异为0。 |
| Payload quality | 18个种子只形成2个附属成员：1个补回Gold，1个为非Gold。后者使对应单题Precision由0.2降至0.1667，说明成员筛选仍需提高区分力。 |
| Failure diagnosis | 深圳递交题的正确邻居使用“递送至”，问题使用“递交地点”，覆盖得分0.7857略低于0.8；相邻错误块同样0.7857，直接降阈值会引入歧义。 |
| Inference | “锚点与上下文分层”机制成立，但当前字符覆盖归一化不足；应提升关系同义表达的规范化，而不是扩大邻居窗口或降低阈值。 |
| Alternatives | 可使用轻量Embedding判断邻居相关性，但会增加延迟与系统复杂度；也可优化切块使表格相邻字段合并，但那会改变索引和所有实验变量。 |
| Falsifiable hypothesis result | 部分支持但未通过：Recall、跨项目改善门槛失败；锚点稳定性、MRR、nDCG、延迟和错误门槛通过。 |
| Decision | 不接入运行时；默认关闭的证据组代码和同口径控制/候选报告保留。 |
| Next experiment | 原计划`RET-EXP-004C`已取消。单项目局部收益不足以支持继续增加关系归一化规则；先冻结RET-EXP-003B并执行唯一一次Holdout盲测。 |

## RET-HOLDOUT-003B 盲测推理记录

| 环节 | 记录 |
|---|---|
| Blind-test integrity | Development与Holdout项目交叉为0；候选、代码哈希、配置和门槛均在执行前冻结；Holdout只执行1次。 |
| Observation | 正向检索达到大部分绝对门槛，但排序质量略低于门槛，且唯一无结果样本没有拒答。 |
| Evidence | 正向Hit@5=0.8889、Recall@5=0.7778、MRR=0.8333；nDCG@5=0.7423，比0.75门槛低0.0077；Negative Accuracy=0；P95=1515ms，错误=0。 |
| Layered diagnosis | Hard Recall=0.6667；1个长表格问题完全未命中；1个Exact+Hybrid复合问题Recall=0.5且Query规划不匹配；唯一负向题返回5条近似证据。 |
| Inference | RET-EXP-003B的“候选覆盖选择”对正向证据召回有跨项目泛化，但系统尚未建立经过Development验证的证据不足拒答能力，长表格与复杂Query也仍是薄弱层。 |
| Alternatives | 排序损失可能来自Top5容量、块粒度或打分，而非单一规则；负向拒答可能需要绝对相关性、覆盖度和跨通道一致性联合判断，不能用单题定阈值。 |
| Gate result | 完整发布门槛未通过；不得事后放宽nDCG门槛，也不得忽略预注册的100%负向准确率要求。 |
| Decision | 不发布为完整生产候选；保留为正向检索候选，Holdout结果永久锁定。 |
| Next direction | 从全新项目建立含无结果、长表格、跨块和精确+语义复合问题的新Development/Challenge数据。数据独立复核后，再启动拒答和复杂检索实验；不使用本Holdout选择参数。 |

## 优化位置与实验队列

| 优先级 | 实验ID | 优化位置 | 准备改变什么 | 主要目标 | 状态 |
|---|---|---|---|---|---|
| P0 | `RET-EXP-001` | `mcp_servers/tender_evidence/service.py` → `TenderEvidenceService.search_tender_evidence` | 将每路 `per_query_top_k` 与最终 `bounded_top_k` 解耦；每路候选深度设为20，最终仍只返回5条 | Recall@5至少+0.03，MRR不下降，P95≤1377ms | 已通过并保留 |
| P1 | `RET-EXP-002` | `mcp_servers/tender_evidence/query_planner.py` → `plan_tender_query` | 对合同风险类Hybrid问题增加1条宽泛事实支撑Query | Hybrid Recall达到1.0、整体Recall至少+0.05 | 部分改善，未启用 |
| P1.1 | `RET-EXP-002B` | `mcp_servers/tender_evidence/query_planner.py` → 风险事实支撑生成规则 | 只对付款风险生成预付款、进度款、结算/质保三个固定分面Query | 找回剩余付款Gold，控制查询数和延迟 | 取消直接实施：单样本过拟合风险 |
| P2 | `RET-EXP-003A` | `mcp_servers/tender_evidence/query_planner.py` → 通用原子事实槽拆分 | 按“对象 + 待求属性 + 正反约束”拆分复合问题，不写死付款、项目或题目表达 | 至少改善两个不同问法，且跨主题或跨项目；总体Recall提升、无明显回退 | 已拒绝：无Recall收益且延迟翻倍 |
| P2.1 | `RET-EXP-003B` | `mcp_servers/tender_evidence/query_planner.py` → `merge_planned_results` | 不增加检索Query，只在现有主查询候选池中增加可解释的原子需求/主题覆盖选择 | Recall至少+0.03，至少改善两个不同问法，MRR/nDCG不降，P95增幅≤15% | 已通过，保留为Development候选 |
| P2.2 | `RET-EXP-004A` | `mcp_servers/tender_evidence/service.py` / 候选合并层 | 对已选证据按同文档、同章节扩展直接相邻block，再由覆盖选择控制最终Top5 | 改善至少两个跨块问法，无回退，P95增幅≤15% | 已拒绝：Recall无提升，仅改善排序 |
| P2.3 | `RET-EXP-004B` | MCP结果建模 / 候选合并层 | Top5按锚点计数；承载需求的直接邻居以证据组成员随锚点返回，不单独竞争Top5 | Recall至少+0.03，跨两个项目改善，无锚点/Gold丢失，P95增幅≤15% | 部分改善但拒绝：+0.025且仅一个项目 |
| P2.4 | `RET-EXP-004C` | `query_planner.py`覆盖归一化层 | 原计划增加关系表达归一化 | 原计划验证跨项目改善 | 已取消：沿单项目局部收益继续优化有过拟合风险 |
| P2.5 | `DATA-CHALLENGE-001` | 新项目资料与评测集治理 | 从未参与现有Development/Holdout的项目建立负向、长表格、跨块和多条款样本；独立复核后才能进入实验 | 为拒答和复杂检索提供可比较、未污染的Development/Challenge基线 | 已完成唯一一次盲测；完整门槛未通过，数据集锁定 |
| P2.6 | `RET-FACT-GATE-001` | LangGraph事实覆盖状态与确定性证据门 | 不增加Query和不改Top5；从已有coverage元数据记录未覆盖/候选覆盖/上下文验证，先shadow评测误充分和误拒答 | 提升证据不足识别能力，同时不改变现有研判行为 | 部分有效但未通过：保留shadow，拒绝enforced |
| P2.7 | `RET-STRUCT-CONTEXT-001/002` | 表格/章节父子结构检索与上下文证据组 | 为证据块补充可验证父章节/表头，并从既有候选池组成有界同父兄弟证据组；不增加第二轮Query | 改善关系型事实覆盖并降低误充分/误拒答 | Development A通过；生产默认关闭，待Development B |
| P2.8 | `RET-CONTROLLED-RETRY-001/002/003` | 未覆盖事实的受控第二轮检索 | 只为部分覆盖后的未覆盖事实槽生成有限补查Query；最终以补查结果导航真实结构父组且保留首轮锚点 | 补齐结构证据组后仍缺失的事实 | 003 Development A检索门通过；默认关闭，待Development B |
| P2.9 | `RET-GRAPH-EXPAND-001` | 跨文档问题的选择性图关系扩展 | 只对明确跨文档关系问题沿有来源的文档/实体/条款关系扩展 | 补齐普通检索难以串联的跨文档事实 | 待跨文档Development数据；不从单文档A集宣称收益 |
| P3 | `RET-EXP-004` | `mcp_servers/tender_evidence/sqlalchemy_repository.py`及混合检索服务 | 增强相近条款区分和结构字段权重，例如投标保证金/履约保证金、地点/份数 | 提升Exact Recall和MRR | 等待P2结论 |
| P4 | `RET-EXP-005` | 混合检索融合结果之后 | 在召回稳定后再评估轻量Rerank，不提前引入额外变量 | 提升nDCG和MRR | 暂缓 |

执行原则：

- 以上顺序是当前假设，不是最终答案；
- 每次只实现并评测一行；
- 如果前一个实验未通过门槛，先记录“拒绝”及原因，再决定是否进入下一项；
- 任何新增优化点都必须写明代码位置、目标指标和验证实验；
- Holdout不出现在此优化队列中，只在候选版本冻结后运行一次。

## DATA-CHALLENGE-001 接入准备记录

| 环节 | 记录 |
|---|---|
| Observation | 新取得的蓝城真实项目包含43.5 MB、25个Sheet的工程量清单；10个Sheet存在声明区域异常膨胀，原按声明维度遍历的方式存在超时和内存失控风险。资料还包含施工合同自动分类缺口，以及一个标识为“一期零星工程”的冲突Sheet。 |
| Change | 只改输入证据治理层：XLSX改为按真实单元格流式解析并计算有效区域，增加通用资源上限、重复Sheet隔离、期次冲突隔离和诊断事件；补充施工合同自动分类和自动重跑分类能力。没有修改Query Planner、召回排序、RRF、Top K或Gold评分。 |
| Evidence | 真实清单约70.1秒完成解析；25个Sheet中24个进入证据链、1个冲突Sheet隔离；生成1502个清单证据块；没有触发截列或文本上限。三份资料最终形成3202个有效证据块。 |
| Inference | 检索指标成立的前提是资料先被安全、正确且无污染地转成证据。异常维度、误分类和冲突Sheet若不先处理，会把输入故障误判成检索排序问题。 |
| Generalization review | 规则只使用OOXML结构、资源预算、内容重复和一期/二期等通用期次标识，不包含“蓝城”“明月江南”等项目专属关键词。 |
| Dataset governance | 已登记为`DATA-CHALLENGE-001`并建立10题Challenge；经业务复核通过后冻结为approved。Challenge不计入原20条Development + 10条Holdout门槛，不用于回调已冻结候选。 |
| Current result | 已完成唯一一次正式盲测：Hit@5 88.89%、Recall@5 47.59%、MRR 68.52%、nDCG@5 49.73%、路由与Query数量准确率100%、负样本准确率0%、P95 1677ms、错误0。完整门槛未通过，数据集和结果已锁定。 |
| Next direction | 不重跑本Challenge，不根据失败题调参。先从全新项目建立含足量无结果、长表结构、四事实以上和跨文档覆盖问题的新Development数据；再分别验证证据充分性/拒答门、结构元数据召回和Top5多事实覆盖。不得追加项目关键词。 |

## DATA-CHALLENGE-001 唯一一次盲测记录

| 环节 | 记录 |
|---|---|
| Observation | Hit@5达到88.89%，但Recall@5仅47.59%、nDCG@5仅49.73%；路由和Query数量准确率均为100%。 |
| Evidence | Q006五个付款/质保事实只命中一个；Q008长清单结构证据完全未进入Top5；Q009无图纸负样本仍返回5条结果；Q010没有召回被隔离的一期Sheet。 |
| Inference | Planner已能识别检索方向，核心瓶颈是Top5有限预算下的多事实覆盖、跨文档多样性、长表结构召回和证据不足判断，不是运行稳定性或主要路由错误。 |
| Alternative explanation | 错误为0、P95为1677ms，排除基础运行失败；Gold已存在于冻结manifest，不能把Q008简单归因于未解析。 |
| Decision | `RET-EXP-003B`未通过完整泛化门槛，只保留为正向检索条件性候选，不标记生产就绪。 |
| Generalization review | 不从蓝城题目直接增加关键词、Query或阈值。Challenge永久锁定，只作为一次泛化证据。 |
| Next direction | 用全新项目建立新的Development集，先补足正负证据充分性样本，再单变量验证拒答门；随后验证长表结构字段召回和跨文档多事实覆盖。 |

## CONTEXT/MEMORY/GRAPHRAG 架构判断记录

| 环节 | 记录 |
|---|---|
| Problem | Challenge显示Top5经常只能命中部分事实，跨文档和长表结构覆盖不足，无答案时仍返回弱相关证据。 |
| Evidence | Hit@5 88.89%，但Recall@5 47.59%、nDCG@5 49.73%；路由与Query数量准确率100%；Q006五个事实命中一个、Q008长表为0、Q009负样本拒答失败。 |
| Hypothesis | 核心缺口是事实覆盖工作状态、结构化父子上下文、有限Top5内的多样性选择和证据充分性判断，不是简单扩大LLM上下文或增加长期记忆。 |
| Alternatives | 评估了大上下文、长期Memory、LangGraph工作状态、结构化上下文、受控第二轮检索、完整GraphRAG和选择性图扩展。 |
| Decision | 不直接上完整GraphRAG，不引入长期项目记忆；优先在新Development数据上依次验证拒答门、结构化父子检索、事实槽工作状态和受控第二轮检索。选择性图扩展只作为跨文档关系问题的后续单独实验。 |
| Generalization review | 不添加项目名、楼栋号或失败题固定关键词；每次只改一个主要变量；现有Holdout与Challenge永久锁定。 |
| Current result | 当前只是架构假设与实验排序，尚未编码、尚无新指标，不能表述为已优化完成。 |
| Learning note | 完整思考与方案比较见`docs/bid-intake-agent-development-notes.md`。 |

## RET-FACT-GATE-001 实现与试验准备

| 环节 | 记录 |
|---|---|
| Problem | 当前只能看到Top5结果，Agent State无法显式表示多事实问题中哪些已覆盖、哪些未覆盖，证据门也无法区分“命中一条”和“完整支持”。 |
| Change | LangGraph增加`update_fact_coverage`节点；新增事实槽覆盖状态和`off/shadow/enforced`证据门模式；新增误充分、误拒答和门对齐指标。没有改Query、路由、候选、Top5、RRF或Embedding。 |
| Safety | 当前环境固定`shadow`，事实状态不进入LLM输入、不改变证据门结论；只有未来明确切换`enforced`才阻断。 |
| Tests | 事实覆盖、上下文验证、普通Top5不视为充分、shadow不阻断、enforced阻断、评测隐私和旧流程回归共`50 passed`。 |
| New data | 未见项目“总部基地设计任务书”已建立8题Development A：5题多事实正向、3题无结果；经业务逐题复核后冻结。approved指纹`a253cbe9e3bdad051b3e903077740cbec2c71cadd6cafc8a3a8f6d036ea74886`。 |
| Predeclared gate | 评估覆盖率100%、对齐≥80%、负样本准确率100%、误充分率0%、误拒答率≤20%、P95≤2000ms、错误0。 |
| Current result | 唯一正式shadow评测：评估覆盖率100%、对齐62.5%、负样本准确率100%、误充分33.33%、误拒答50%、P95 982ms、错误0。整体未通过。 |
| Observation | 三道无结果题都被事实门正确判断不足；Q002/Q004把“提到设计阶段”误当成“回答该阶段的工作/时间”；Q005正确证据Top1却未通过关系型答案信号。 |
| Inference | 状态可观测性有价值，但表层事实槽和词法答案信号不足以证明证据充分；当前开启硬门会同时错误放行与错误阻断。 |
| Decision | 保留shadow状态和图谱展示；拒绝切换enforced，不从本数据集增加项目专属词或放宽阈值。 |
| Generalization review | 没有使用锁定Challenge调阈值或加关键词；单项目结果不得作为通用结论，至少需要第二个全新项目。 |
| Next direction | 进入父子结构检索与上下文证据组，验证结构上下文能否表达“对象 + 待求属性 + 关系”；结构稳定后再复评硬门，并建立Development B。 |

## RET-STRUCT-CONTEXT-001/002 父子结构与证据组

| 环节 | 记录 |
|---|---|
| Problem | DOCX表格子行被扁平化，Top20中虽有正确行，但子行缺失共同表头语义，Top5也无法同时容纳多条同表答案。 |
| Evidence | Q002/Q003/Q004全部Gold在原候选Top20内；对应排名分别为`18/11/7`、`10/4/5/3`、`12/8/1/4`，说明主要损失发生在结构解释和最终证据组织。 |
| Hypothesis | 若只使用同文档版本内真实父表头，把同一父节点下查询相关的兄弟子行作为有界证据组交付，就能恢复多行答案且不增加Query。 |
| Alternatives | 简单物理相邻扩展已被旧实验否定；扩大TopK会增加噪声和LLM上下文；完整GraphRAG成本过高；先验证结构父子组更直接。 |
| Experiment 001 | 只附着父章节/表头。Recall 65%→71.67%，但只改善Q002一题，未达到至少2题的预设门槛，拒绝。 |
| Experiment 002 | 父节点下已有Top5锚点时，从现有Top20选择最多3条相关兄弟行；最多1个父组，成员不占Top5，不增加Query。 |
| Result | Recall 65%→90%，MRR 56.67%→63.33%，nDCG 58.58%→63.27%；Q002和Q004改善、无正向退化；误充分33.33%→20%，误拒答50%→33.33%；P95 696ms、错误0。 |
| Metric caveat | `Recall@5`以5个锚点证据组为结果单位，组内成员会使实际文本块数超过5，不能表述为严格5个独立块的Recall。 |
| Generalization review | 改动不含项目词和固定题目规则，且跨两种问题改善；但只验证了一个项目，生产开关保持关闭，必须用全新项目Development B验证。 |
| Decision | 001拒绝；002接受为Development A结构候选，不上线。 |
| Next direction | Q003/Q004仍各缺1条Gold。下一实验只对未覆盖事实槽执行一次受控第二轮检索，继续禁止重跑旧Holdout/Challenge。 |

## RET-CONTROLLED-RETRY-001/002/003 受控第二轮检索

| 环节 | 记录 |
|---|---|
| Problem | 结构证据组后Q003/Q004仍各缺1条Gold，需要验证只补查未覆盖事实能否提高覆盖，而不是无条件增加Query。 |
| Trigger design | 只在`0 < covered < required`时触发；零覆盖与已充分均不重试；最多1轮2条Query。 |
| Experiment 001 | 补查候选重新参与RRF和Top5锚点。Recall 90%→95%，但nDCG 63.27%→57.91%，拒绝。 |
| 001 inference | 正确证据找到了，但重新洗牌把原正确锚点挤入组内；证据召回和证据整合必须分开设计。 |
| Experiment 002 | 完全保留首轮锚点，补查只进入普通结构组候选。所有质量指标与基线相同，安全但无效，拒绝。 |
| 002 inference | 补查产生3个新候选，却未改变全局父组选择；补查应提供“扩展哪个父节点”的导航信号。 |
| Experiment 003 | 保留首轮锚点和排序；补查首个具有真实父表的结果指定优先父节点，再附着同父兄弟行。 |
| Result | Recall 90%→95%，MRR/nDCG不变；Q003 75%→100%，无退化；仅1条补查，3个负样本均不触发；P95 687ms、错误0。 |
| Fact-gate caveat | Q003新增Gold后shadow门仍为`insufficient`。派生误拒答率上升来自Gold完整性变化，逐题门决定没有回退；说明关系谓词确认仍需独立优化。 |
| Generalization review | 没有项目词；触发、父节点与预算均为通用契约。但只有一个项目中的一个触发样本，生产默认关闭，必须Development B。 |
| Decision | 001/002拒绝；003接受为Development A检索候选，不接受为硬证据门，不上线。 |
| Next direction | 进入选择性跨文档图关系扩展；单文档Development A无法评估其收益，先准备契约并等待新跨文档数据。 |

## RET-GRAPH-EXPAND-001 选择性跨文档图扩展准备

| 环节 | 记录 |
|---|---|
| Problem | 下一优化项针对招标文件、合同、清单、答疑之间的联合事实和冲突关系。 |
| Evidence | 当前Development A manifest只有1份文档；现有多文档Challenge已锁定，不能重跑调参。 |
| Falsifiability | 单文档集上触发0次只能验证不误触发，无法证明跨文档Recall或路径Precision，因此当前没有合法效果实验。 |
| Design | 只有“至少两类资料角色 + 跨来源关系意图”同时出现才触发；只沿可回溯的case/document/section/evidence/exact-reference边扩展。 |
| Budget | 1跳、2个种子、4条扩展证据；同case/租户；扩展作为组成员，不重排Top5；证据门继续生效。 |
| Alternatives | 暂不引入Neo4j，不让LLM自由生成永久边；首版可用MySQL/MinIO已有关系验证价值。 |
| Decision | `blocked_by_evaluation_data`；不编码、不宣称收益、不启用。 |
| Next direction | 取得全新项目的招标文件+合同+清单，建立8—10题经业务复核的数据后再冻结门槛。 |

## 长期项目记忆决策

| 环节 | 记录 |
|---|---|
| Problem | 评估图扩展之后是否还需要长期Memory。 |
| Evidence | 当前失败均发生在本项目检索、结构和证据关系层，没有证据表明跨会话遗忘是质量瓶颈。 |
| Current capabilities | LangGraph State负责单次运行状态；SQL Checkpointer/事件负责恢复审计；RAG负责原始资料知识。 |
| Risk | 过期资料、跨项目污染、权限隔离、人工推断与原文混淆，会降低可解释性并破坏评测归因。 |
| Decision | `deferred_not_justified`，当前不开发。 |
| Revisit condition | 只有出现可度量的跨会话重复确认、稳定人工修正复用需求，并具备来源/版本/有效期/撤回/租户规则时才开独立实验。 |
| Detail | 见`docs/bid-intake-agent-long-term-memory-decision-20260730.md`。 |

## RET-CANDIDATE-COVERAGE-002 固定Development跨项目复验

| 环节 | 记录 |
|---|---|
| Problem | 惠州Holdout显示部分Gold已进入Top20却未进入Top5，需要判断旧候选覆盖选择能否跨项目稳定修复，同时停止“每步索要新项目”的holdout treadmill。 |
| Method | 合并香港中心、深圳丰隆、总部基地、陵水为固定4项目38题Development池；Baseline/Candidate各正式运行一次；唯一变量为候选覆盖选择开关；逐项目门和候选池诊断门在运行前冻结。 |
| Global result | Hit@5 88.57%→94.29%，Recall@5 70.67%→76.38%，MRR 79.29%→82.14%，nDCG@5 69.04%→74.99%；搜索次数不变，P95降低142ms，错误0。 |
| Candidate-pool result | 候选池Recall同为87.14%；Gold保留率80.05%→87.19%，只提升7.14个百分点，未达到10个百分点；被Top5淘汰Gold由17降至13。 |
| Cross-project result | 香港中心、深圳丰隆改善；总部基地Recall不变但MRR/nDCG退化；陵水Recall、MRR、nDCG均退化。改善项目2/4，非起源项目改善0/2。 |
| Regression evidence | `RET-GRAPH-DEV-B-Q008`的两条Gold都在候选池；选择器用两个普通“提交”条款替换了直接说明履约保证金退还的合同块，Recall 100%→50%。 |
| Inference | 主要缺口是Top5选择器把表面动作词覆盖误当成目标对象—关系—属性已经被回答，而不是候选生成完全找不到。 |
| Fact-gate note | shadow误充分率23.53%、误拒答率42.86%，进一步证明词面槽位覆盖不等于证据充分。 |
| Decision | 尽管总体指标改善，仍按冻结的零Recall退化、3/4项目改善、非起源项目改善和Gold保留率门拒绝；生产开关保持关闭。 |
| Generalization review | 未运行泰丰、蓝城、惠州；未添加项目名、保证金等题目专用词；不重跑002挑结果。 |
| Next direction | 在同一固定Development池做003：保留高置信基线锚点，覆盖选择只填剩余位置；只有目标对象、关系或属性确定性对齐时才能替换锚点。通过后再做上下文证据组。 |

## RET-CANDIDATE-COVERAGE-003 锚点保护实验

| 环节 | 记录 |
|---|---|
| Problem | 002总体改善但会用普通动作词证据替换正确基线锚点，需要在不增加Query的前提下保护直接回答证据。 |
| Predeclared change | 以普通最终Top-5为骨架；Top-1和至少4字符有效直接对齐锚点受保护；只有答案信号与直接对齐同时满足的候选才能替换未保护尾部。 |
| Tests before freeze | 55项通过；4项目Gold、manifest和索引全部有效；数据、代码、配置和门槛已记录SHA-256。 |
| Formal result | Hit不变88.57%；Recall 70.67%→70.57%；MRR 79.29%→75.24%；nDCG 69.04%→67.65%；Gold保留率80.05%→79.10%；被淘汰Gold 17→18；2题Recall退化，1/4项目改善。 |
| Implementation observation | 只有2题发生真实coverage promotion，但12题结果变化；10题在promotion=0时仍变化，且全部是多Query题。 |
| Root cause | 实现用原始RRF前5建立骨架，而Baseline实际先保留辅助Query多样性锚点再RRF填充；Candidate意外绕过了原有合并语义。 |
| Valid evidence | 002的`RET-GRAPH-DEV-B-Q008`退化被修复，两条Gold与顺序均保持，Recall仍为100%。 |
| Causal limit | 质量差异混合“锚点保护”和“取消多Query多样性”两个变量，不能据此判断直接对齐阈值或锚点保护思想。 |
| Decision | `rejected_implementation_contract_violation_and_quality_gates`；生产关闭；不静默修复后重跑003。 |
| Learning | 单变量A/B除了配置快照，还需要变形不变量证明未触及的路径保持等价；`promotion=0`时输出等价是本类选择器的关键契约。 |
| Next direction | 独立建立004：共享同一个Baseline Top-5生成函数，Candidate只修改其副本；先增加promotion=0输出完全等价、保护锚点不移动/不删除、搜索次数不变测试，再重新冻结和唯一运行。 |

## RET-CANDIDATE-COVERAGE-004 共享Baseline骨架实现

| 环节 | 记录 |
|---|---|
| Problem | 003从原始RRF前5初始化Candidate，绕过普通Baseline的辅助Query多样性锚点；38题中只有2题promotion却有12题变化，10题在promotion=0时变化。 |
| Hypothesis | 若Baseline与anchor-preserving Candidate调用同一个有序Top5生成函数，且Candidate只修改其副本，则promotion=0必然逐项等价，受保护锚点保持原位，真实promotion只替换最低优先级未保护尾部。 |
| Contract | 新增`ret_candidate_coverage_004_experiment_contract.md`；状态`implementation_before_freeze`，固定38题与003门槛口径不变，泰丰/蓝城/惠州继续锁定。 |
| Change | `query_planner.py`新增`_select_baseline_top_k`，固定先保留辅助Query多样性候选、再按RRF补齐；普通Baseline和anchor Candidate共享该结果，Candidate复制后替换；旧greedy继续原coverage→多样性→RRF顺序。 |
| Invariant evidence | 多Query无promotion用例中两侧均为3条辅助Query锚点加2条RRF候选，顺序逐项相同；promotion为0。确有promotion用例只替换第5个未保护尾部，Top-1与第二个受保护锚点保持原位置。 |
| Cost evidence | 服务层Baseline/Candidate搜索调用的`query / top_k / search_mode`列表完全相同，总搜索次数相同；没有新增上下文、图或LLM调用。 |
| Tests | Query Planner、检索评测、结构证据组和选择性图聚焦回归`66 passed`；仅有本机pytest缓存权限与既有requests依赖版本警告。 |
| Result | `implementation_invariants_passed_pending_user_approval_to_freeze`。这是代码层结果，没有固定38题质量指标，没有004冻结或执行产物。 |
| Decision | 保留004实现与契约，生产候选覆盖开关继续关闭；本轮不执行正式A/B，不重跑003。 |
| Next direction | 用户批准后单独冻结004，记录哈希；固定38题Baseline一次、Candidate一次，不重跑挑结果，不运行泰丰Holdout、蓝城Challenge或惠州Holdout。 |

## RET-CANDIDATE-COVERAGE-004 固定Development正式结果

| 环节 | 记录 |
|---|---|
| Problem | 在共享普通Baseline骨架已经通过代码不变量后，验证4—12字符直接对齐promotion能否跨固定4项目稳定减少“Gold已进入候选池、却被最终Top-5淘汰”的损失。 |
| Hypothesis | 受控promotion应在不改变零promotion样本、不增加搜索调用和不移动保护锚点的同时，使Recall至少提升5个百分点、Gold保留率至少提升10个百分点，并改善至少3/4项目和1个非起源项目。 |
| Contract and freeze | 独立契约为`ret_candidate_coverage_004_experiment_contract.md`；冻结ID为`RET-CANDIDATE-COVERAGE-004-FIXED-DEV-FREEZE-20260730-V1`，固定池指纹`c1b1dc434353aee5b8e19aed30be204c43e8385195f5d0fb94afb918bce81c87`，门槛在结果前冻结；泰丰/蓝城/惠州继续锁定。 |
| Formal method | 用户批准后，固定4项目38题先执行Baseline一次，再执行Candidate一次；`baseline_pass=1`、`candidate_pass=1`、`rerun_performed=false`，未运行Agent、泰丰Holdout、蓝城Challenge或惠州Holdout。 |
| Global result | Hit@5 88.57%→91.43%，Recall@5 70.67%→73.05%，MRR 79.29%→80.24%，nDCG@5 69.04%→70.46%；没有Recall或排序退化。 |
| Candidate-pool result | 候选池Recall同为87.14%；Gold保留率80.05%→83.86%，只提升3.81个百分点，低于冻结的10个百分点门；被Top5淘汰Gold由17降至15。 |
| Implementation audit | 38题中3题发生3次promotion，且只有这3题变化；35道零promotion题、其中9道多Query题，均与Baseline有序Top5逐项一致。三个变化题各只替换一个位置，其余位置与保护锚点不变；候选池顺序、Query任务均38/38一致。 |
| Cost evidence | Baseline/Candidate搜索调用均为62，P95 1257ms→1302ms，错误均为0，没有新增LLM、GraphRAG或长期Memory。 |
| Cross-project result | 只有香港中心Recall改善8.33个百分点；深圳丰隆、总部基地、陵水均不变。改善项目1/4，非起源项目改善0/2。 |
| Case result | 改善题只有`HK-CENTER-DEV-Q004`和`HK-CENTER-DEV-Q010`，低于至少3题门；`RET-GRAPH-DEV-B-Q001`发生一次质量中性promotion。 |
| Gate result | 实现一致性、绝对指标、无退化、MRR/nDCG、淘汰Gold下降、成本安全门通过；Recall增量、改善题数、Gold保留率增量、改善项目数和非起源项目改善门失败。 |
| Inference | 共享Baseline骨架修复使004能够有效检验预注册假设；但直接对齐promotion触发过于稀疏且收益集中，不能充分弥合候选池Recall与最终Top5 Recall的差距。 |
| Alternatives | 不因均值向好事后降低门槛，不重跑挑结果；保留工程不变量，但不接受质量候选。 |
| Decision | `rejected_quality_gain_retention_and_cross_project_gates`；保留共享Baseline函数与测试，生产候选覆盖继续关闭。 |
| Next direction | 不重跑003/004；若继续，仅使用已保存Development诊断建立独立新候选，预注册“边际事实覆盖收益高于锚点替换损失”的替换规则，仍从共享Baseline副本开始。 |

## RET-CANDIDATE-COVERAGE-005 保存诊断与预实现契约

| 环节 | 记录 |
|---|---|
| Problem | 004已证明共享Baseline骨架安全，但直接对齐promotion只恢复2条Gold且收益集中在香港中心。普通Baseline中仍有17条Gold已进入候选池却被Top-5淘汰，分布在12题和全部4个项目。 |
| Saved evidence | 固定35道正向题共有82条Gold；候选池包含66条，Top-5保留49条。16条候选池外Gold不属于005范围；17条选择损失中9条排在候选池前12、13条排在前20。 |
| Project distribution | 香港中心5条、深圳丰隆1条、总部基地6条、陵水5条候选池内Gold被Top-5淘汰，说明选择缺口并非只存在于起源项目。 |
| Failure taxonomy | 12道选择损失题中11道已有原子槽，但只有2道发生有效promotion；`FACT-COVERAGE-DEV-A-Q004`表面槽4/4覆盖却仍漏2条Gold；`RET-GRAPH-DEV-B-Q001`发生非Gold质量中性promotion；`HK-CENTER-DEV-Q009`没有可审计槽位。 |
| Inference | 只放宽连续字符阈值会放大主体词面误命中。005必须保留共享Baseline，并把候选收益与被替换证据的事实、Query多样性损失放在同一模拟中比较。 |
| Falsifiable hypothesis | 若事实需求保留“主体＋共享谓词/答案维度”，且只有替换后关系感知覆盖集合严格增加、Top-1与唯一辅助Query代表不丢失时才promotion，则应比004更稳定地提升Top-5 Gold保留率并产生非香港项目收益。 |
| Planned variable | 新政策`predicate_aware_marginal_gain`；Baseline关闭覆盖选择，Candidate只在共享Baseline有序Top-5副本上做最多2次正净收益替换。Query、候选池、RRF、Top-K和所有其他增强不变。 |
| Fail-closed boundary | 无法可靠识别主体和关系时标记`unsupported_relation_shape`并保持Baseline；主体名称、文档角色或关系短词本身不构成答案；候选池外16条Gold不在005解决范围。 |
| Required invariants | 零promotion逐项同序；每次promotion的`net_coverage_gain >= 1`；覆盖集合严格增加；Top-1、唯一辅助Query代表、未替换位置不变；搜索与候选池完全一致；004与greedy兼容回归不变。 |
| Quality gates | 延续004的Recall至少+5pp、改善至少3题、Gold保留率至少+10pp、至少3/4项目改善且至少1个非起源项目改善等全部门槛，不事后降低。 |
| Current result | `diagnostic_contract_before_implementation`；只新增`ret_candidate_coverage_005_experiment_contract.md`和记录，不改检索代码、不运行测试或A/B、不生成冻结。 |
| Decision | 005可以进入下一阶段的实现与不变量测试；生产候选覆盖继续关闭。 |
| Next direction | 下一阶段仅实现005政策和测试。测试通过后先报告代码层结果，用户再次批准后才允许冻结并各唯一运行一次Baseline/Candidate；泰丰、蓝城、惠州继续锁定。 |

## RET-CANDIDATE-COVERAGE-005 实现与冻结前不变量

| 环节 | 记录 |
|---|---|
| Problem | 004的直接对齐只恢复2条Gold，且现有表面槽会把主体提及误判为答案充分；005需要比较新增关系事实与替换损失。 |
| Change | 新增`predicate_aware_marginal_gain`；并列主体继承共享谓词/答案形态；Candidate只复制共享Baseline有序Top-K并原位替换；关系需求最多6个，每题最多2次promotion。 |
| Answer-bearing gate | 主体对齐≥0.75、覆盖分数≥4.5并且类型化答案信号成立；单独主体、关系标题、文档角色或无法建模的语义风险问法失败关闭。 |
| Loss control | Top-1和辅助Query唯一代表固定；被替换项独占事实必须由新候选继续覆盖；每次替换后重算，只有`net_coverage_gain >= 1`执行。 |
| Audit | 保存promotion顺序、替换证据/位置、替换前后事实索引、新增事实索引、被替换项独占事实、净增益和Query多样性；评测清洗不保留业务问题或主体文本。 |
| Invariant evidence | 零promotion逐项等于Baseline；重复事实不替换；丢独占事实不替换；保留独占事实并新增事实时只替换未保护尾部；Top-1、唯一辅助Query代表和未替换位置不动；promotion≤2。 |
| Cost evidence | Baseline/Candidate的`query / top_k / search_mode`调用列表相同，搜索次数不增，上下文读取为0；没有新增LLM、GraphRAG或Memory。 |
| Compatibility | 004`anchor_preserving_direct_alignment`、旧greedy、结构证据组和选择性图聚焦回归均通过。 |
| Tests | 四个聚焦测试文件共`80 passed`；相关Python文件`py_compile`通过；仅有既有`requests`依赖版本警告。 |
| Production safety | 代码和CLI默认关闭；发现本地`.env`与`.env.example`旧值为`true`后只将该候选覆盖开关纠正为`false`。 |
| Current result | `implementation_invariants_passed_pending_user_approval_to_freeze`；没有005质量指标、冻结或执行产物。 |
| Decision | 保留005实现，生产关闭；本轮不得执行固定38题A/B。 |
| Next direction | 用户再次批准后才单独冻结005，并严格执行Baseline一次、Candidate一次；不运行泰丰Holdout、蓝城Challenge或惠州Holdout，不重跑挑结果。 |

## RET-CANDIDATE-COVERAGE-005 固定Development正式结果

| 环节 | 记录 |
|---|---|
| Problem | 验证谓词感知正净收益替换能否在保持普通Baseline骨架、候选池和检索成本不变时，跨项目减少候选池到Top-5的Gold损失。 |
| Contract and freeze | 冻结ID`RET-CANDIDATE-COVERAGE-005-FIXED-DEV-FREEZE-20260731-V1`；固定池4项目38题，指纹`c1b1dc434353aee5b8e19aed30be204c43e8385195f5d0fb94afb918bce81c87`；80项测试和4项目Gold/manifest/index在运行前通过。 |
| Formal method | 用户批准后执行Baseline一次、Candidate一次；`baseline_pass=1`、`candidate_pass=1`、`rerun_performed=false`；没有运行Agent或三个锁定数据集。 |
| Global result | Hit 88.57%→94.29%，Recall 70.67%→76.62%，MRR 79.29%→80.90%，nDCG 69.04%→72.37%；4题改善、无Recall或排序退化。 |
| Candidate-pool result | 候选池Recall同为87.14%；Gold保留率80.05%→87.43%，提升7.38个百分点，低于冻结的10个百分点；淘汰Gold 17→13。 |
| Cross-project result | 香港中心+15.00pp、深圳丰隆+3.33pp、总部基地+5.00pp、陵水不变；3/4项目改善，包含非起源项目总部基地，跨项目门通过。 |
| Selector audit | 8题变化、9次promotion、累计净覆盖增益10；30道零promotion题全部同序；所有promotion正净收益，Top-1、Query多样性、独占事实、未替换位置和每题最多2次门均通过。 |
| Cost evidence | Query任务38/38一致；搜索调用62→62；上下文、图、LLM和Memory新增调用均为0；P95 1541ms→1172ms；错误0。 |
| Candidate-pool order mismatch | `HK-CENTER-DEV-Q009`候选池第9/10位互换，ID集合、Top-5和质量相同，该题`unsupported_relation_shape`且零promotion；有序候选池等价仅37/38，未达到冻结的100%。 |
| Implementation judgment | 选择器自身不变量通过，但正式实现一致性门因1题低位候选顺序波动失败。按契约不得重跑，也不能用其余质量收益覆盖该失败。 |
| Quality gate | 绝对指标、Recall增量、改善题数、无退化、排序、淘汰Gold、跨项目和成本门通过；Gold保留率增量门失败。 |
| Decision | `rejected_candidate_pool_order_equivalence_and_gold_retention_gates`；保留代码，生产关闭，不重跑005。 |
| Generalization review | 没有添加项目词、题目词或Gold ID；没有解锁泰丰Holdout、蓝城Challenge或惠州Holdout。陵水无收益仍是残留跨项目缺口。 |
| Next direction | 若继续，独立建立新实验，先冻结或确定性回放共享候选池再比较最终选择器，消除实时检索低位排序波动；不得回改005门槛。 |

## RET-EVIDENCE-SUFFICIENCY-GATE-002 只读关系充分度与拒答门禁

| 环节 | 记录 |
|---|---|
| Problem | 普通检索在固定Development的3道无答案题上都会返回弱相关Top5，`negative_accuracy=0/3`；旧事实门001虽识别3题不足，但关系型正例误充分/误拒答，不能enforced。 |
| Saved evidence | 005保存结果显示3道负例的谓词感知关系覆盖均为0；但005选择器已被正式拒绝且生产关闭，不能借开启选择器来获得门禁状态。 |
| Hypothesis | 把005的主体＋关系＋答案形态判定抽成独立只读评估，只在普通Baseline最终证据上计算，可识别“相关但没有回答”，且不改变任何检索结果或成本。 |
| Variable | 新开关只生成`sufficiency_need_*`、`evidence_sufficiency_summary`和事实工作状态；候选覆盖选择继续关闭。 |
| Fail-closed rule | 零覆盖或部分覆盖为`insufficient`；无法可靠建模为`not_assessed`；未来enforced下两者都不得直接通过。 |
| Negative regression | 付款条件弱相关、投标/履约保证金近义干扰、资质硬门槛/评分经历干扰三类合成负例均为零有效关系覆盖。 |
| Positive regression | 直接关系答案可判候选充分；多关系部分覆盖判不足；已有后续搜索补齐后可升级；普通Top5不能制造覆盖。 |
| Invariants | 评估开/关有序Top5、搜索参数和次数一致；`coverage_selection_policy=off`、promotion=0、上下文读取0、新增搜索0。 |
| Tests | Query Planner、事实门、检索评测、结构组、选择性图、Agent运行时/配置/图谱共`111 passed`；`py_compile`和CLI入口通过，仅有既有requests警告。 |
| Production safety | `.env`与`.env.example`新开关均为false；事实门继续shadow；未运行固定38题、Agent或锁定集。 |
| Current result | `implementation_invariants_passed_pending_new_development_and_user_approval`。 |
| Decision | 保留实现作为独立门禁候选，不启用enforced，不复用005正式结论。 |
| Next direction | 新建并业务复核未使用过的充分度Development集，冻结后经用户批准只运行一次影子评测；泰丰、蓝城、惠州继续锁定。 |

### 002工程通用性审计补充

| 环节 | 记录 |
|---|---|
| User scope | 当前不做新的跨项目泛化验证，不为此引入新项目；只验收实现是否为通用规则。 |
| Static audit | 运行代码中项目名、评测题号、Evidence ID、case UUID和Gold标识命中均为0；没有具体日期、金额、房型或固定答案规则。 |
| Metamorphic test | “区域甲/乙”整体改名为“设备东/西”后，关系类型、答案形态、直接覆盖索引与弱相关零覆盖完全一致。 |
| Tests | 加入通用主体改名测试后联合回归`112 passed`；只有既有requests警告。 |
| Engineering decision | `engineering_genericity_audit_passed_no_formal_generalization_requested`；可以确认不是当前项目专属补丁。 |
| Boundary | 不宣称统计泛化或误拒答率已达生产门槛，不启用enforced；当前无需新项目，生产开关继续关闭。 |

## INTERNAL-PILOT-FREEZE-STAGE1 只读冻结基线

| 环节 | 记录 |
|---|---|
| Problem | Agent架构已形成完整闭环，但需要判断当前工作区能否形成可复现、可回滚的内部试运行版本，而不是继续优化候选召回。 |
| Scope | 只盘点依赖、Git边界、运行状态、数据库版本、开关、依赖版本、保存指标和本地测试；不重启、不迁移、不改开关、不运行正式A/B或锁定集。 |
| Runtime evidence | `/health/ready=ready`；受控项目`ready_to_start=true`；1个专用Agent Worker在线；MCP/模型/政策能力匹配；`hybrid_rrf`索引完成。 |
| Version evidence | Git HEAD为`ac8f0b6ca57664640a0f50816df70d315321d07a`；采集前工作树267项变化，Agent主体大多未跟踪；共享入口混有无关业务改动。 |
| Database evidence | 当前`20260731_0077`，代码头`20260731_0078`；0078删除旧执行系统，且`start_all.ps1`重启会自动升级到head。 |
| Safety evidence | 充分度评估关闭、事实门shadow、候选覆盖关闭；固定Development普通检索负例仍为`0/3`。 |
| Probe boundary | 配置预检通过，但真实MCP和模型探针均为`skipped`，没有产生远程调用或费用。 |
| Tests | 本地聚焦回归`89 passed, 1 warning in 5.22s`；没有运行固定38题、Agent、Holdout、Challenge或正式A/B。 |
| Artifacts | `docs/bid-intake-agent-internal-pilot-freeze-stage1-baseline.md`；`evals/bid_intake/internal_pilot_freeze_stage1_baseline_v1.json`。 |
| Decision | `stage1_baseline_captured_pending_stage2_database_decision`；架构方向可冻结，但当前不是正式发布冻结。 |
| Next direction | 阶段2只审计0078删除目标、现存记录、代码引用、备份与恢复方案；用户单独批准前禁止迁移、整套重启或门禁启用。 |

## INTERNAL-PILOT-FREEZE-STAGE2 数据库收口

| 环节 | 记录 |
|---|---|
| Authorization | 用户批准备份0080、升级0081并重启验证；执行开始时环境已经完成，因此本任务不重复备份、迁移或重启。 |
| Backup | `backups/pre_0081_pricing_snapshot_recovery_20260801_092701.sql`，809,317,170 bytes，SHA-256 `64DC1AA14C68DBB474B1C02031C051551FFE1EB4991CB88AFD3CCADF891580A3`，mysqldump结束标记存在。 |
| Migration | `current=head=20260801_0081`；0081只新增预算计价草稿快照表，不修改Agent schema；核验时快照表2条记录。 |
| Schema audit | 14张Agent/Evidence/Checkpoint关键表全部存在；9张退役表全部不存在。 |
| Runtime | `/health/ready=ready`；Agent `ready_to_start=true`、blockers空、专用Worker在线、`hybrid_rrf`索引completed。 |
| Safety | 充分度评估false、事实门shadow、候选覆盖false；没有改变运行行为。 |
| Tests | 聚焦回归`89 passed, 1 warning in 4.95s`；未运行固定38题、Agent、Holdout、Challenge或A/B。 |
| Artifacts | `docs/bid-intake-agent-internal-pilot-freeze-stage2-database-closeout.md`；`evals/bid_intake/internal_pilot_freeze_stage2_database_closeout_v1.json`。 |
| Decision | `stage2_database_alignment_verified_head_0081`；数据库版本阻塞关闭。 |
| Next direction | 进入阶段3可复现发布版本整理；先拆分Agent专属文件与共享集成补丁，仍不启用拒答门或运行正式评测。 |
