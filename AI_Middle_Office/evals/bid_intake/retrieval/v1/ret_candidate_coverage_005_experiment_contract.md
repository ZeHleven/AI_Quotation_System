# RET-CANDIDATE-COVERAGE-005 实验契约

## 当前状态

`implementation_invariants_passed_pending_user_approval_to_freeze`

本文件先于实现固化005的保存结果诊断、可证伪假设、实现边界与验收门槛。005代码和冻结前测试现已完成；尚未生成005冻结文件，尚未执行Baseline/Candidate，生产候选覆盖开关保持关闭。

只有005实现完成、全部不变量与聚焦回归通过，并向用户报告代码层结果、获得再次明确批准后，才允许单独冻结并执行唯一一次正式A/B。

## 事实来源

005只使用下列已保存的固定Development资料形成诊断，不重新检索：

- 数据集：`private_fixed_development_pool_v2_approved.jsonl`
- 数据文件SHA-256：`203737153d1fe5b85383ec6972c14cd5f7088fe2bf3695c3daa4412a9627eebd`
- 数据集指纹：`c1b1dc434353aee5b8e19aed30be204c43e8385195f5d0fb94afb918bce81c87`
- 004 Baseline报告SHA-256：`cd33635edddc119ad687e2555f6e60f44ddc3066c91ad3616925537422775d99`
- 004 Baseline预测SHA-256：`3f953d7c83a897b4dbf7ec30d15b16a57d634807a1d96cf90d329c5e9b49364b`
- 004 Candidate报告SHA-256：`bc351a877f2ea9b7518280059df9a83594c6891eeb8c9c005c18e7f9afafecaf`
- 004 Candidate预测SHA-256：`afda1fe7dd6926d1699e7b95dc22584b928a44d669d150436f82bf516fa9fbb1`

泰丰Holdout、蓝城Challenge、惠州Holdout继续锁定，未参与005诊断，也不得用于005实现、调参、测试或重跑。

## 004后仍存在的问题

固定Development包含35道正向题、82条Gold引用。普通Baseline的候选池包含66条Gold，其中49条进入最终Top-5：

- 16条Gold根本不在候选池，属于候选生成缺口，不是005选择器能解决的问题；
- 17条Gold已经进入候选池，却在最终Top-5阶段被淘汰；
- 17条选择损失分布在12题、4个项目；
- 其中9条候选池排序在前12，13条在前20，说明并非全部是极深候选；
- 004只恢复2条Gold，淘汰数17→15；
- 004共有3次promotion，其中`RET-GRAPH-DEV-B-Q001`提升了一个非Gold候选，但质量指标不变。

逐项目的17条选择损失为：

| 项目 | 被Top-5淘汰的候选池Gold |
|---|---:|
| 香港中心 | 5 |
| 深圳丰隆 | 1 |
| 总部基地 | 6 |
| 陵水 | 5 |

因此005仍有真实的跨项目选择空间，但不得宣称它能解决16条候选池外Gold。

## 逐题保存诊断

下表的“覆盖槽”来自004 Candidate已保存的`coverage_selection_summary`，不是Gold标签驱动的运行时特征。

| 题目 | 候选池Gold / Baseline Top-5 Gold | 被淘汰Gold候选池名次 | 004覆盖槽 | 004 promotion | 诊断 |
|---|---:|---|---:|---:|---|
| `FACT-COVERAGE-DEV-A-Q002` | 3 / 0 | 7, 11, 18 | 3/4 | 0 | 多主体共享“工作与成果”谓词，表面主体命中没有把正确成果送入Top-5 |
| `FACT-COVERAGE-DEV-A-Q003` | 4 / 3 | 10 | 3/4 | 0 | 文件、份数、电子格式是共享答案维度，现有槽位没有保留完整关系 |
| `FACT-COVERAGE-DEV-A-Q004` | 4 / 2 | 8, 12 | 4/4 | 0 | **假充分**：槽位显示全覆盖，但仍有2条正确时间节点Gold被淘汰 |
| `HK-CENTER-DEV-Q003` | 1 / 0 | 19 | 1/2 | 0 | 所有5个Baseline项均被直接对齐保护，缺少“新事实收益与现有锚点损失”的比较 |
| `HK-CENTER-DEV-Q004` | 1 / 0 | 6 | 2/3 | 1 | 004恢复1条Gold，证明受控替换方向有效 |
| `HK-CENTER-DEV-Q009` | 1 / 0 | 6 | 无槽位 | 0 | 单句语义风险问法没有可审计原子槽，005必须失败关闭而不是强行替换 |
| `HK-CENTER-DEV-Q010` | 3 / 1 | 13, 49 | 2/3 | 1 | 004恢复1条Gold，但另一条仍未进入Top-5 |
| `RET-GRAPH-DEV-B-Q005` | 5 / 3 | 23, 26 | 0/3 | 0 | 跨文档冲突与版本同步关系没有被表面主体匹配识别 |
| `RET-GRAPH-DEV-B-Q007` | 3 / 2 | 21 | 1/2 | 0 | 缺附件/补取关系需要否定或缺失答案信号 |
| `RET-GRAPH-DEV-B-Q009` | 4 / 3 | 14 | 2/4 | 0 | 时间、地点、保证金、份数的多维答案需逐关系验证 |
| `RET-GRAPH-DEV-B-Q010` | 4 / 3 | 7 | 1/3 | 0 | “负责/其他单位负责但需配合”的正反职责关系未完整表达 |
| `SZ-FENGLONG-DEV-Q010` | 3 / 2 | 12 | 1/3 | 0 | 多Query已有多样性锚点，但仍缺一个关系答案 |

这12题中11题已经生成原子覆盖槽，但只有2题发生有质量收益的promotion。仅放宽004的4—12字符连续对齐阈值，无法解决：

1. 主体被提及但关系或答案形态不成立；
2. 多主体共享谓词在拆槽时丢失；
3. 当前Baseline已被表面槽误判为充分；
4. 替换现有证据时没有显式计算会损失哪些已覆盖事实和Query多样性。

## 可证伪假设

如果005把复合问题表示为“主体＋共享谓词/答案维度”的关系感知事实需求，并且每次替换前显式模拟：

`替换后事实覆盖集合 - 替换前事实覆盖集合`

那么它应当在不损失Top-1、唯一辅助Query代表或现有唯一事实支持的前提下，只执行净事实覆盖严格增加的替换，从而比004更稳定地把候选池内Gold送入Top-5。

该假设被以下任一结果推翻：

- 主体词命中但没有关系答案的候选仍可promotion；
- 任一次promotion的净事实覆盖增量小于1；
- 替换后丢失原有唯一事实支持或辅助Query多样性；
- `promotion_count == 0`时输出与Baseline不同；
- 收益仍只集中在香港中心；
- 正式A/B未达到预注册Recall、Gold保留率和跨项目门槛。

## 唯一实验变量

未来正式A/B的唯一变量为最终Top-5选择政策。

Baseline：

- `candidate_coverage_selection=off`

Candidate：

- `candidate_coverage_selection=on`
- `coverage_selection_policy=predicate_aware_marginal_gain`

005政策由两个不可拆分的内部步骤组成：

1. 为选择器构造关系感知的事实需求；
2. 只执行净事实覆盖严格增加、且替换损失受控的Top-5替换。

这两个步骤共同构成一个预注册的最终选择政策，不改变Query计划、搜索结果或候选池。004的`anchor_preserving_direct_alignment`和旧`greedy`行为必须保持不变。

## 关系感知事实需求

005不得硬编码项目、题目、Gold Evidence ID或业务专属答案。事实需求只能由原问题的通用结构确定性生成：

1. 识别并列主体及其共享问法；
2. 将共享谓词或答案维度继承到每个主体；
3. 事实需求至少保留：
   - `subject`
   - `relation_type`
   - `answer_shape`
4. 可识别的通用关系类型包括：
   - 时间/期限
   - 金额/价格
   - 数量/份数
   - 地点
   - 包含
   - 排除
   - 要求/条件
   - 标准/验收
   - 职责/配合
   - 缺失/否定
   - 通用实体事实
5. 若不能可靠识别“主体＋关系”，该题在005中标记为`unsupported_relation_shape`，不得进行promotion；
6. 不得把“招标文件、工程量清单、合同”等单独文档角色或只有主体名称的证据视为答案充分。

关系感知事实需求只用于已有候选池的最终选择，不生成或改写检索Query。

## Answer-bearing匹配

候选覆盖一个事实需求必须同时满足：

1. 主体对齐；
2. 与`relation_type`一致的答案信号；
3. 覆盖分数达到预注册门槛；
4. 对通用实体事实，除主体外还必须存在实质性谓词或答案片段；
5. 只有文档角色、元话语、关系短词或主体重复时不合格。

类型化答案信号必须采用通用模式，例如时间值、金额单位、数量单位、地点表达、包含/排除、责任/配合、缺失/否定或规范性要求；不得追加具体项目名、具体日期、金额、房型、保证金或题目答案词。

## 边际收益与替换损失

Candidate必须从共享`_select_baseline_top_k`返回的有序Top-5副本开始。

对每个Baseline位置和候选池外部候选，确定性计算：

- `coverage_before`：当前Top-5关系感知事实覆盖集合；
- `candidate_coverage`：新候选合格覆盖的事实集合；
- `victim_exclusive_coverage`：只有拟被替换证据支持的事实集合；
- `coverage_after`：模拟替换后的事实覆盖集合；
- `net_coverage_gain = |coverage_after| - |coverage_before|`；
- 是否损失唯一辅助Query代表；
- 是否触及不可替换锚点。

只有同时满足以下条件才可promotion：

1. `net_coverage_gain >= 1`；
2. Top-1保持原位；
3. 不删除任何辅助Query的唯一代表；
4. 若被替换项独占某个已覆盖事实，新候选必须同时覆盖该事实；
5. 未替换位置保持原位；
6. 每次替换后重新计算覆盖集合；
7. 每题最多2次promotion；
8. 没有合格净收益时，结果逐项等于Baseline。

候选排序依次使用：

1. 净覆盖增量降序；
2. 关系感知匹配置信度降序；
3. 覆盖分数降序；
4. RRF分数降序；
5. Evidence ID稳定排序。

被替换位置依次使用：

1. 不可替换锚点排除；
2. 唯一辅助Query代表排除；
3. 独占事实支持损失最小；
4. RRF优先级最低；
5. 有序Top-5尾部优先。

## 必须新增的可审计元数据

每次005选择必须记录：

- 关系感知事实需求数量与类型；
- `unsupported_relation_shape`及原因；
- Baseline覆盖集合；
- 每个promotion候选的新增事实集合；
- 被替换证据的独占事实集合；
- 替换前后覆盖数量；
- `net_coverage_gain`；
- 辅助Query多样性是否保持；
- 替换位置；
- promotion或拒绝原因码。

评测产物必须能够离线验证每次输出变化都由正净收益promotion解释。

## 冻结前必须通过的不变量测试

### 关系感知拆槽

- 并列主体共享时间谓词时，每个主体都继承时间关系；
- 并列主体共享文件/份数/格式时，答案维度不丢失；
- 只有主体、没有对应答案形态的证据不得覆盖该需求；
- 无法可靠识别关系的语义问法标记为unsupported，并保持Baseline。

### 边际覆盖

- 候选只重复当前已覆盖事实时，`net_coverage_gain == 0`且不得替换；
- 候选新增事实但会删除另一个独占事实时，净收益不大于0则不得替换；
- 候选同时保留被替换项独占事实并新增至少1个事实时，可以替换；
- 每次promotion必须满足`coverage_after`严格大于`coverage_before`。

### 锚点与多样性

- Top-1不得移动或消失；
- 辅助Query的唯一代表不得移动或消失；
- 未替换位置逐项不变；
- 每题promotion不超过2次。

### 变形与成本

- `promotion_count == 0`时：
  `ordered_candidate_top_k == ordered_baseline_top_k`
- Candidate与Baseline的Query序列、`top_k`、`search_mode`和搜索调用次数逐项相同；
- 候选池Evidence ID及顺序相同；
- 不增加上下文读取、图调用、LLM调用或长期Memory。

### 兼容回归

- 004`anchor_preserving_direct_alignment`结果与元数据不变；
- 旧`greedy`策略结果与元数据不变；
- Query Planner、检索评测、结构证据组和选择性图聚焦回归通过。

## 未来正式A/B共同配置

只有实现与测试通过、用户再次批准、005单独冻结后才可执行。

- 固定数据：4项目38题
- `top_k=5`
- `per_query_candidate_top_k=20`
- Query Planner检索Query与自适应路由不变
- MySQL/MinIO/Milvus分层存储不变
- Embedding模型、768维向量、BM25/向量/RRF不变
- `chunk_max_chars=1200`
- `chunk_overlap_chars=0`
- 结构证据组、相邻块扩展、受控第二轮检索、选择性图扩展全部关闭
- 事实充分性门保持shadow
- 不调用分析LLM
- 不保存问题外的证据正文

## 预注册实现一致性门

以下条件必须全部满足：

- 所有`promotion_count == 0`题有序Top-5与Baseline逐项一致；
- 所有输出变化题至少有1次promotion；
- 每个promotion都满足`net_coverage_gain >= 1`；
- 每次promotion后关系感知覆盖集合严格增加；
- Top-1保持原位；
- 辅助Query唯一代表保持原位；
- 未替换位置保持原位；
- 候选池ID与顺序100%一致；
- Query任务100%一致；
- 搜索调用次数不增加；
- 每题promotion不超过2；
- 004与greedy兼容回归100%通过。

任一实现一致性门失败，005不得冻结或正式评测。

## 预注册质量门

延续003/004口径，不因004失败事后降低：

- Candidate Hit@5 ≥ 80%
- Candidate Recall@5 ≥ 60%
- Candidate MRR ≥ 75%
- Candidate nDCG@5 ≥ 60%
- Recall@5相对Baseline提升 ≥ 5个百分点
- 改善题数 ≥ 3
- Recall退化题数 = 0
- MRR变化 ≥ -2个百分点
- nDCG@5变化 ≥ -2个百分点
- 候选池Recall ≥ 60%
- 候选池到Top-5的Gold保留率提升 ≥ 10个百分点
- 被Top-5淘汰的Gold数量下降
- 至少3/4项目改善
- 每个项目Recall变化不得低于-2个百分点
- 香港中心/深圳丰隆之外至少1个项目改善
- 路由准确率、Query数量准确率、Topic Recall均为100%
- 负样本准确率不得下降
- 搜索调用次数不得增加
- Candidate P95相对Baseline增加不得超过500ms
- 执行错误为0
- 新增LLM Token为0
- 所有实现一致性门100%通过

## 正式运行纪律

- 当前只建立契约，不冻结、不实现、不运行；
- 实现与测试通过后先向用户报告代码层结果；
- 获得再次明确批准后才生成005冻结文件；
- 冻结时记录数据、代码、配置、门槛和测试SHA-256；
- 固定38题上只允许一次Baseline、随后一次Candidate；
- 不得重跑挑结果；
- 不得根据逐题结果追加项目词、题目词、日期、金额、房型或Evidence ID；
- 005不得覆盖、修改或冒充002、003、004历史结论；
- 生产候选覆盖功能继续关闭；
- 不运行泰丰Holdout、蓝城Challenge或惠州Holdout。

## 结果分支

- 若实现一致性门失败：不冻结，修复实现并重新运行聚焦测试；
- 若实现一致性通过但质量门失败：拒绝005，保留失败记录和共享Baseline工程能力；
- 若收益仍只来自香港中心：按跨项目门失败拒绝；
- 若全部Development门通过：005仅成为Development候选；后续生产判断需要新的独立泛化数据，不得解锁旧Holdout或Challenge；
- 普通检索负样本准确率问题必须另开独立事实充分性/拒答实验，不能与005混为同一变量。

## 2026-07-31 实现完成记录

本节只记录预注册契约之后的代码层实现与冻结前验证，不修改前述正式A/B变量、质量门或运行纪律。

### 实现

- 新政策名：`predicate_aware_marginal_gain`；
- 普通Baseline、004和005都从共享`_select_baseline_top_k`取得“辅助Query多样性优先、RRF补齐”的有序结果；
- 005只复制该有序Top-K并在原位置做受控替换；
- 关系感知事实需求最多6个，只用于已有候选池选择，不增加或改写检索Query；
- 主体对齐门为`0.75`，覆盖分数门为`4.5`；
- 支持时间、金额、数量、地点、包含、排除、条件、要求、标准、交付物、职责/配合、缺失、冲突和通用实体事实；
- 只有主体、关系标题或单独文档角色时不构成答案；无法可靠构造至少2个关系需求时标记`unsupported_relation_shape`；
- 每次替换前计算Baseline覆盖、候选覆盖、被替换项独占覆盖和替换后覆盖；只有`net_coverage_gain >= 1`且独占事实与辅助Query多样性不丢失时执行；
- Top-1固定，每题最多2次promotion，每次后重新计算；
- 004`anchor_preserving_direct_alignment`和旧`greedy`分支未改写；
- 服务审计新增promotion顺序、替换位置、替换前后事实索引、增量事实索引、独占事实索引、净增益和Query多样性结果；
- 评测清洗层保留安全的关系类型、答案形态与选择审计，但移除业务问题和主体文本；
- MCP与评测CLI只新增005政策枚举；功能开关默认值、本地`.env`和`.env.example`均为`false`。

### 冻结前不变量证据

- 并列主体共享时间谓词可逐主体生成`time_value`需求，且覆盖真实“个工作日”写法；
- 文件、份数和电子格式保留为复合答案形态；
- 通用冲突、缺失、职责/配合关系可确定性构造；
- 单独文档角色和无法识别的语义风险问法失败关闭；
- 只有主体或只重复现有事实时零promotion且逐项等于Baseline；
- 新增事实但会丢失被替换项独占事实时拒绝；
- 同时保留独占事实并新增事实时，只替换最低优先级未保护位置；
- Top-1和辅助Query唯一代表保持原位置；
- 未替换位置逐项不变，每题promotion不超过2；
- Baseline/Candidate的`query / top_k / search_mode`调用列表逐项一致，上下文读取均为0；
- 候选池输入ID和顺序未被选择器修改；
- 005审计可离线解释每次输出变化的正净收益。

聚焦回归覆盖：

- `tests/test_tender_query_planner.py`
- `tests/test_bid_intake_retrieval_evaluation.py`
- `tests/test_tender_structure_context.py`
- `tests/test_tender_selective_graph.py`

最终结果为`80 passed`。另以`py_compile`验证005涉及的规划器、服务、两个CLI、评测清洗层和测试文件；没有语法错误。测试仅出现既有`requests`依赖版本警告。

### 当前判断

005已通过冻结前代码不变量，状态为：

`implementation_invariants_passed_pending_user_approval_to_freeze`

该状态不代表固定Development质量门通过，也不代表生产可用。本阶段没有运行固定38题，没有生成005冻结、预测、执行或总结产物，没有运行泰丰Holdout、蓝城Challenge或惠州Holdout。
