# RET-CANDIDATE-COVERAGE-002 — Development C复验契约

状态：`pre_freeze_validation`。改用固定Development池，正式检索尚未运行。

## 目标

验证现有`RET-EXP-003B`确定性候选覆盖选择，能否在固定的跨项目Development池中，把已经进入每Query Top20候选池的不同事实证据更完整地保留到最终Top5。

这不是根据惠州Holdout修改算法。首个候选直接复用现有实现，不增加项目关键词、同义词、Query、Embedding、Reranker模型或LLM调用。

## 为什么先复验现有实现

旧Development中，候选覆盖选择曾把Recall@5从77.50%提高到88.33%；旧Holdout的正向Recall为77.78%，但完整发布门因nDCG和负向拒答失败。惠州图扩展Holdout又显示部分Gold已进入候选池却被Top5淘汰。

这些证据足以提出“候选选择仍值得复验”的假设，但不足以依据旧Holdout逐题调整规则。因此先原样复验003B，比直接开发覆盖选择v2更能防止过拟合。

## 固定Development池

不再为每个微优化索要新项目。复用已经业务复核的4个Development项目，共38题：

- 香港中心与深圳丰隆：20题；
- 总部基地设计任务书：8题；
- 陵水福朋喜来登：10题。

这4个项目允许在开发阶段重复实验和调参，但必须：

- 每次保持单变量；
- 同时报告总体指标和逐项目指标；
- 收益不能只集中于最早用于开发候选覆盖选择的香港中心/深圳丰隆；
- 泰丰花园、蓝城明月江南、惠州未来花园继续锁定，不参与本轮；
- 最终组合架构全部冻结后，才使用一个全新项目做一次最终Holdout。

本轮把4个项目看作项目级轮换观察：算法不在单次运行中训练，但通过逐项目宏观指标模拟“留一项目检查”，判断同一固定规则在不同文档结构上是否稳定。

## Baseline与Candidate

共同配置：

- 数据库分层检索；
- Query Planner与自适应路由保持不变；
- 每Query候选深度20；
- 最终Top5；
- Embedding、BM25与RRF保持不变；
- 不启用相邻块、上下文证据组、受控第二轮、图扩展或长期Memory；
- 不调用分析LLM，不保存证据正文。

唯一变量：

- Baseline：`candidate_coverage_selection=false`；
- Candidate：`candidate_coverage_selection=true`。

## 新增诊断指标

评测器在同一次正式检索调用中只记录候选证据ID，不记录正文，并区分：

- `CandidatePoolRecall`：Gold中有多少已经进入候选池；
- `Top5Recall`：最终Top5实际保留多少Gold；
- `Candidate→Top5 Gold保留率`：候选池中的正确证据有多少没有被选择阶段淘汰；
- `GoldDroppedFromCandidatePool`：已召回却在Top5被淘汰的Gold数量；
- `CandidatePoolOracleCompleteRate`：候选池是否具备答全问题的理论条件。

这样可以区分两类失败：

1. Gold根本没有进入候选池，应优化Query、索引或召回；
2. Gold已经在候选池但被Top5淘汰，应优化候选选择或证据组装。

## 预注册验收门槛

具体冻结文件在新数据完成复核后生成，首次运行前不得再修改。预定最低门槛：

- Candidate Hit@5 ≥ 80%；
- Candidate Recall@5 ≥ 60%；
- Candidate MRR ≥ 75%；
- Candidate nDCG@5 ≥ 60%；
- Candidate相对Baseline的Recall提升 ≥ 5个百分点；
- 至少3题Recall或排序改善；
- Recall回退题数为0；
- 至少3/4项目的Recall或nDCG改善；
- 任一项目Recall不得回退超过2个百分点；
- 香港中心/深圳丰隆之外的2个项目中至少1个改善；
- MRR、nDCG变化均不得低于-2个百分点；
- `Candidate→Top5 Gold保留率`相对Baseline提高 ≥ 10个百分点；
- `GoldDroppedFromCandidatePool`相对Baseline减少；
- 路由、Query数量、Topic准确率均为100%；
- 错误0、新增LLM Token 0；
- 本地确定性选择本身不新增远程检索调用。

如果候选池Recall本身低于60%，即使Top5保留率提高，也只能得出“选择器工作正常但召回不足”，不能接受为完整候选。

## 结果后的分支

- 通过：把002作为下一项“上下文证据组”的新Baseline；
- 候选池Recall高、Top5保留率仍低：只在固定Development池设计通用覆盖选择v2；
- 候选池Recall低：停止调选择器，转向Query/索引/受控补查；
- 改善集中单一项目：拒绝，不进入下一组合；
- 无论结果如何，不运行或修改旧Holdout/Challenge。

## 当前步骤

先机械合并3份已经批准的Development JSONL，校验38题Gold、项目索引和数据指纹；随后冻结数据、代码、配置与上述门槛，再各运行一次Baseline与Candidate。不会为本轮读取或重跑任何旧Holdout/Challenge。
