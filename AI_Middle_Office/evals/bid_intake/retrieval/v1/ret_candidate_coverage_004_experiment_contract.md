# RET-CANDIDATE-COVERAGE-004 实验契约

## 当前状态

`implementation_before_freeze`

本文件先约束004的实现与测试。当前阶段不生成冻结文件、不执行正式Baseline/Candidate，也不改变生产开关。只有代码不变量与聚焦回归通过、并获得用户明确批准后，才单独冻结004并执行唯一一次正式A/B。

## 问题

`RET-CANDIDATE-COVERAGE-003`要求Candidate以普通最终Top-5为骨架，但实现从原始RRF前5初始化。普通Baseline在多Query场景下实际先保留辅助Query多样性候选，再按RRF补齐。两条路径因此改变了不止一个变量：

- 38题中12题输出变化；
- 只有2题真正发生coverage promotion；
- 10题在`promoted_evidence_count=0`时仍变化；
- 这10题全部是多Query题。

003已永久记录为`rejected_implementation_contract_violation_and_quality_gates`，不得修复后重跑或改写为004结果。

## 可证伪假设

若Baseline和锚点保护Candidate都从同一个普通Baseline有序Top-5开始，且Candidate只在该列表副本上执行受控替换，则：

1. `promotion_count == 0`时，Candidate有序Top-5与Baseline逐项完全一致；
2. 发生promotion时，受保护锚点的位置与内容均不改变；
3. promotion只替换最低优先级的未保护尾部位置；
4. Query计划、搜索调用次数、候选深度、RRF候选顺序和所有其他增强保持不变。

若任一实现不变量失败，004不得冻结或正式评测。

## 固定数据边界

- 数据集：`private_fixed_development_pool_v2_approved.jsonl`
- 项目：香港中心、深圳丰隆、总部基地、陵水
- 数量：38题，35道正向题、3道无结果题
- 数据集指纹：`c1b1dc434353aee5b8e19aed30be204c43e8385195f5d0fb94afb918bce81c87`
- 泰丰Holdout、蓝城Challenge、惠州Holdout永久锁定，不参与004开发、调参或重跑
- 正式评测前不得读取旧锁定集的逐题结果来修改004规则

## 唯一共享Baseline骨架

普通Baseline最终Top-5必须由唯一共享函数生成，顺序固定为：

1. 初始化空的有序结果；
2. 按Query计划顺序遍历辅助Query，即`ranked_results[1:]`；
3. 对每条辅助Query，保留其结果中第一个尚未入选的候选，直到Top-K已满或辅助Query耗尽；
4. 再按全局RRF有序候选`ranked_ids`补入尚未入选的证据，直到Top-K已满；
5. 返回有序Baseline Top-K。

`candidate_coverage_selection=off`的普通Baseline和
`coverage_selection_policy=anchor_preserving_direct_alignment`的Candidate都必须调用该函数。不得为Candidate复制一份近似实现，也不得从`ranked_ids[:top_k]`重新构造骨架。

## Candidate受控替换

Candidate的唯一变化是：

1. 复制共享函数返回的有序Baseline Top-K；
2. Top-1始终保护；
3. Baseline锚点若与原问题存在至少4个连续字符的有效直接对齐，则保护；
4. 单独文档角色、无目标对象的通用关系短语和元话语不构成直接对齐；
5. 新候选只有同时满足事实槽答案信号、覆盖分数门槛和有效直接对齐，才具有promotion资格；
6. 若当前Baseline尚未覆盖该事实槽，从列表尾部向前寻找第一个“未保护且尚未承担其他coverage槽”的位置并替换；
7. 除被替换的该位置外，其余Baseline项不得移动；
8. Candidate不得新增Query、搜索、上下文读取、图调用、LLM调用或长期Memory。

## 旧greedy策略兼容边界

`coverage_selection_policy=greedy`不是004的实验变量。它继续保持既有行为：

- 先按旧规则选择coverage候选；
- 再执行既有辅助Query多样性补位；
- 最后按RRF补齐。

004不得借共享Baseline骨架重构而改变greedy路径的选择顺序、coverage元数据或结果。

## 冻结前必须通过的测试门

### 变形不变量

- 多Query且无强直接对齐promotion时：
  `ordered_candidate_top_k == ordered_baseline_top_k`
- 同一用例中：
  `promoted_evidence_count == 0`

### 锚点保护

- Top-1不得移动或消失；
- 所有直接对齐受保护锚点不得移动或消失；
- 未发生替换的位置必须与Baseline逐项一致。

### 受控promotion

- 构造一个确有合格promotion的用例；
- 只能替换最低优先级的未保护尾部位置；
- 更高优先级未保护项和所有受保护项保持原位置。

### 成本不变量

- Baseline与Candidate的Query序列逐项相同；
- 搜索调用次数相同；
- 每次搜索的`query / top_k / search_mode`逐项相同；
- 不增加上下文读取、图调用或LLM调用。

### 回归

- 现有anchor-preserving测试通过；
- 现有greedy coverage测试通过；
- Query Planner与检索评测聚焦测试通过。

## 正式A/B配置

仅在用户批准并完成独立004冻结后执行。

Baseline：

- `candidate_coverage_selection=off`

Candidate：

- `candidate_coverage_selection=on`
- `coverage_selection_policy=anchor_preserving_direct_alignment`

共同配置：

- `top_k=5`
- `per_query_candidate_top_k=20`
- Query Planner与自适应路由不变
- MySQL/MinIO/Milvus分层存储不变
- Embedding模型、768维向量、BM25/向量/RRF不变
- `chunk_max_chars=1200`
- `chunk_overlap_chars=0`
- 结构证据组、相邻块扩展、受控第二轮检索、选择性图扩展全部关闭
- 事实充分性门保持shadow
- 不调用分析LLM，不保存问题外的证据正文

## 预注册质量门

与003保持同口径，避免因003失败事后降低门槛。

全部门槛必须同时满足：

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
- 所有实现不变量为100%通过

## 正式运行纪律

- 冻结时记录数据、代码、配置、门槛和测试结果SHA-256；
- 固定38题上只允许一次Baseline、随后一次Candidate；
- 不得为选择更好结果重跑；
- 不得根据逐题结果追加项目词、保证金词或问题专用规则；
- 004不得覆盖003的契约、冻结、执行、总结或台账记录；
- Development通过也不等于生产通过；
- 生产候选覆盖功能继续关闭；
- 不运行泰丰、蓝城、惠州。

## 结果分支

- 若实现不变量失败：不冻结，修复代码后重新运行聚焦测试；这不属于正式A/B。
- 若实现不变量通过但质量门失败：004拒绝，保留工程修复与失败记录，不启用生产。
- 若全部Development门通过：004成为Development候选；完整架构冻结前仍不运行旧Holdout或Challenge。
