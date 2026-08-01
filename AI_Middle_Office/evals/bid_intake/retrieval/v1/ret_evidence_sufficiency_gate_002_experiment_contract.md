# RET-EVIDENCE-SUFFICIENCY-GATE-002 实验契约

## 当前状态

`engineering_genericity_audit_passed_no_formal_generalization_requested`

本实验独立于 `RET-FACT-GATE-001` 和
`RET-CANDIDATE-COVERAGE-005`。它只开发“证据充分度评估与拒答门禁”，
不修改检索结果选择，不覆盖或改写既有实验结论。

本阶段只允许完成代码、不变量测试和开发记录。测试通过后先报告代码层
结果；未经用户再次确认，不冻结正式评测，不执行正式 A/B。

## Problem

固定 Development 池的普通检索在 3 道无答案问题上都会返回弱相关
Top5，普通 `negative_accuracy=0/3`。这意味着“检索到了内容”目前可能被
误解为“资料已经回答问题”。

`RET-FACT-GATE-001` 已证明事实状态和门禁骨架有价值：3 道负例全部被
识别为不足；但它基于表层事实槽，对关系型正例产生了误充分和误拒答，
正式影子评测未通过，生产只能保持 `shadow`。

`RET-CANDIDATE-COVERAGE-005` 后续形成了更严格的通用关系信号：

- `subject`
- `relation_type`
- `answer_shape`
- 主体对齐、类型化答案信号和覆盖分数

005 在固定池 3 道负例上均得到 `covered_need_count=0`，但该逻辑当前与
被拒绝的 Top5 候选替换政策耦合。生产候选覆盖开关关闭后，普通
Baseline 不会生成可供事实门使用的关系需求，事实门只能
`not_assessed`。

## 可证伪假设

如果把005已经冻结过的关系感知需求与答案承载判定抽成独立、
只读的充分度评估，并且只在普通 Baseline 最终证据上计算覆盖，不执行
promotion，那么系统应能：

1. 在不改变任何检索结果和调用成本的前提下识别“只有主题相似、没有
   直接关系答案”的证据；
2. 将零覆盖或部分覆盖判为 `insufficient`；
3. 将全部关系需求均有直接答案承载证据的情况判为
   `candidate_sufficient`；
4. 对无法可靠建模的问法判为 `not_assessed`，不得假装充分；
5. 在未来 `enforced` 模式下，让 `insufficient` 和 `not_assessed`
   都不能直接通过证据门。

以下任一结果会推翻假设：

- 打开评估后 Baseline 有序 Top5、候选池或搜索调用发生变化；
- 只有主体、章节标题、文档角色或近义主题即可覆盖关系需求；
- 三类已知负例的通用回归仍被判断为充分；
- 明确的直接答案承载证据仍无法形成充分判断；
- `not_assessed` 在 `enforced` 模式下可以直接通过；
- 引入额外检索、上下文读取、LLM、GraphRAG或长期Memory。

## 唯一实验变量

未来正式比较的唯一变量是：

- Baseline：关系充分度评估关闭；
- Candidate：关系充分度评估开启，但候选覆盖选择继续关闭。

Candidate 只增加审计字段和门禁工作状态：

- `sufficiency_need_*`
- `sufficiency_need_indexes`
- `evidence_sufficiency_summary`
- `FactCoverageState`

以下行为必须保持完全一致：

- Query Planner 的实际检索 Query；
- exact / semantic / hybrid 路由；
- 每 Query 候选深度；
- 候选池 Evidence ID 及顺序；
- 普通 Baseline 最终 Top5 Evidence ID 及顺序；
- 搜索调用次数；
- 上下文读取、结构组、第二轮、图扩展和模型调用。

## 充分度判定

只复用005已经冻结过的通用关系提取与答案信号，不新增项目词、题目词、
Gold ID、具体日期、金额、房型或专属术语。

一个事实需求必须保留：

- 主体；
- 关系类型；
- 答案形态。

一条证据只有同时满足以下条件才能覆盖需求：

1. 主体对齐达到既有阈值；
2. 出现与关系类型一致的答案信号；
3. 复合答案形态要求同时成立；
4. 覆盖分数达到既有阈值；
5. 只有主体、文档角色、章节标题或关系短词时不成立。

状态解析：

- 无可靠关系需求：`not_assessed`
- 已识别需求但覆盖数为0：`insufficient`
- 只覆盖部分需求：`insufficient`
- 全部需求均被直接答案承载证据覆盖：
  `candidate_sufficient`

`candidate_sufficient` 只表示当前返回证据具备候选级直接覆盖，不代替
证据引用校验、上下文读取、高风险复核或人工审核。

## 拒答门禁语义

- `shadow`：只记录，不改变 Agent 行为；
- `enforced + insufficient`：增加
  `FACT_SLOT_EVIDENCE_INSUFFICIENT`，不能直接通过；
- `enforced + not_assessed`：增加
  `FACT_SLOT_EVIDENCE_NOT_ASSESSED`，失败关闭到人工复核；
- 当前环境继续保持 `shadow`，新评估功能默认关闭。

本实验不让 Agent 编造“资料没有规定”。门禁只允许表达：

- 当前证据足以支持；
- 当前证据只支持部分结论，需补充；
- 当前证据不足，无法确认；
- 当前规则无法可靠评估，转人工复核。

## 冻结前必须通过的不变量与回归

### 负例安全

- 付款比例/节点/结算条件只有主题相似内容时判不足；
- 保证金金额/方式/退还条件只有近义保证金内容时判不足；
- 资质等级/负责人资格/团队最低人数只有评分或经历描述时判不足；
- 只有主体或关系标题不得判充分。

### 正例与部分覆盖

- 主体、关系和答案值均直接成立时判充分；
- 多关系问题只覆盖部分时判不足；
- 后续已有搜索补齐全部关系时可升级为充分；
- 上下文读取只升级 `verified_slot_count`，不制造新覆盖。

### 失败关闭

- 无法可靠构造关系需求时为 `not_assessed`；
- `shadow` 不阻断；
- `enforced` 下 `insufficient` 和 `not_assessed` 均不得直接通过。

### 检索与成本不变量

- 评估关闭与开启时有序 Top5 逐项相同；
- 候选池 Evidence ID 及顺序相同；
- Query、`top_k`、`search_mode`和搜索调用次数逐项相同；
- 候选覆盖选择保持关闭，promotion 数为0；
- 不增加上下文读取、LLM、GraphRAG或长期Memory；
- 003、004、005选择器兼容回归保持不变。

## 数据纪律

- 3 道既有负例只作为已知问题诊断，不得据此宣称泛化通过；
- 单元测试使用通用合成文本，不写入项目专属答案；
- 泰丰 Holdout、蓝城 Challenge、惠州 Holdout继续锁定，不运行；
- 005固定38题不重跑，不把本实验冒充005修复；
- 正式质量判断需要新的、未参与历史调参的 Development 负例和近似
  负例集，且必须在运行前完成业务复核与冻结。

## 生产边界

- `TENDER_EVIDENCE_CANDIDATE_COVERAGE_SELECTION=false`
- 新充分度评估开关默认 `false`
- `BID_INTAKE_FACT_COVERAGE_MODE=shadow`

本阶段完成代码和测试不等于允许生产启用。只有新的独立 Development
通过预注册的负例准确率、误充分率、误拒答率和成本门后，才允许讨论
`enforced`。

## 2026-07-31 实现与冻结前验证

### 实现结果

- Query Planner 新增
  `predicate_aware_relation_evidence_v1` 只读充分度计划；
- 关系需求与005候选替换彻底解耦，候选覆盖选择关闭时也可以只生成
  充分度工作状态；
- MCP只在普通最终证据及其已存在的证据组成员上计算
  `sufficiency_need_indexes`，不改变任何结果位置；
- 输出新增 `evidence_sufficiency_summary`，记录关系结构是否支持、
  必需/已覆盖数量、状态、原因码、零选择变化和零新增搜索；
- `FactCoverageState` 优先消费新的充分度关系，不再被旧表面覆盖槽
  干扰；
- `enforced + not_assessed` 新增
  `FACT_SLOT_EVIDENCE_NOT_ASSESSED`，失败关闭到人工复核；
- MCP CLI和检索评测CLI均支持独立开关；
- `.env`与`.env.example`中的新开关均明确为`false`，现有
  `BID_INTAKE_FACT_COVERAGE_MODE`继续为`shadow`。

### 不变量与回归结果

通用合成测试覆盖：

- 设计费付款条件弱相关块；
- 投标保证金与履约保证金近义干扰；
- 资质/负责人/人数与评分经历描述干扰；
- 主体＋关系＋时间值直接正例；
- 两个关系只覆盖一个的部分覆盖；
- 无法建模的语义风险问法；
- 后续已有搜索补齐后从不足升级为候选充分；
- `shadow`不阻断；
- `enforced`下不足和未评估均不能直接通过；
- 评测清洗保留安全审计字段，不保存问题或主体文本。

评估关闭/开启的服务级变形测试确认：

- 有序Top5逐项一致；
- 搜索调用参数和次数一致；
- 上下文读取为0；
- `coverage_selection_policy=off`；
- `promoted_evidence_count=0`；
- `additional_search_query_count=0`。

Query Planner、事实门、检索评测、结构证据组、选择性图、Agent运行时、
运行配置和执行图谱联合聚焦回归：

`111 passed`

相关Python文件`py_compile`通过；两个CLI帮助入口可见新开关。只有既有
`requests`依赖版本警告，无新增失败。

### 当前判断

代码层假设和不变量成立，但本阶段没有运行固定38题，没有运行Agent，
没有生成正式冻结、预测或报告，也没有运行三个锁定数据集。

下一步必须先建立新的、未参与历史调参的充分度 Development 集，重点
包含直接无答案、部分答案、近义干扰、否定条件和可完整回答正例；业务
复核并冻结后，再向用户申请唯一一次正式影子评测。生产开关继续关闭，
不得直接切换为`enforced`。

## 2026-07-31 用户范围调整与通用性审计

用户明确决定当前不做新的跨项目泛化验证，本阶段验收目标调整为：

> 证明实现是通用关系规则，不是针对当前项目、当前题目或当前Gold的
> 专属补丁。

该调整不删除前述“正式生产强制启用需要独立数据”的事实，也不把代码
审计冒充统计泛化结果；只是当前不再要求引入新项目。

完成的工程通用性审计：

1. 在门禁运行代码、MCP和评测入口中检索当前及锁定项目名，结果为0；
2. 检索`FACT-COVERAGE`、`RET-GRAPH`、香港/深圳评测题号，结果为0；
3. 检索`EV-*`、已知case UUID和固定Gold标识，结果为0；
4. 运行规则只包含通用主体对齐、关系类型、答案形态、时间/金额/数量/
   地点/条件/要求/标准/职责/缺失/冲突等领域通用信号；
5. 新增主体改名变形测试：把“区域甲、区域乙”整体替换为“设备东、
   设备西”，关系类型、答案形态、两条直接答案覆盖索引和弱相关零覆盖
   结果完全一致；
6. 变形测试不使用任何现有项目名、题号、Gold或固定答案。

加入该测试后的联合回归为：

`112 passed`

因此可以确认：

- 工程实现层面不存在当前项目特化；
- 规则对主体名称改写保持结构不变；
- 已知负例只用于定义通用失败类型，没有写入运行时例外规则；
- 当前可以结束“是否为项目补丁”的审查，不需要为此再引入新项目。

仍然不能宣称：

- 已经统计证明所有新项目误拒答率达标；
- 可以直接启用`enforced`；
- 已完成生产验收。

当前配置继续保持评估关闭、事实门shadow。
