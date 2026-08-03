# RET-CANDIDATE-COVERAGE-003 实验契约

## 目标

修复`RET-CANDIDATE-COVERAGE-002`暴露的Top-5选择退化：正确证据已经进入候选池，但贪心事实覆盖会把普通动作词或文档角色命中误当成答案事实，并替换原本正确的基线锚点。

本实验只验证“锚点保护型事实覆盖选择”，不增加新项目，不运行旧Holdout或Challenge。

## 固定数据

- 数据集：`private_fixed_development_pool_v2_approved.jsonl`
- 项目：香港中心、深圳丰隆、总部基地、陵水
- 数量：38题，35道正向题、3道无结果题
- 数据集指纹：`c1b1dc434353aee5b8e19aed30be204c43e8385195f5d0fb94afb918bce81c87`
- 泰丰、蓝城、惠州继续锁定，不参与本轮开发或调参

## 唯一主要变量

Baseline：

- `candidate_coverage_selection=off`

Candidate：

- `candidate_coverage_selection=on`
- `coverage_selection_policy=anchor_preserving_direct_alignment`

Candidate的选择步骤固定为：

1. 以普通RRF Top-5作为基线骨架；
2. Top-1始终保护；
3. 基线锚点若与原问题存在至少4个连续字符的有效直接对齐，则保护；
4. “招标文件、施工合同、工程量清单”等单独文档角色，以及“提交方式、提交节点”等无目标对象的通用关系短语，不构成直接对齐；
5. 新候选只有同时满足事实槽答案信号和有效直接对齐，才可替换一个未保护基线位置；
6. 从最低基线位置开始替换，已保护锚点不移动、不删除；
7. 不增加检索Query、候选深度、TopK、上下文读取、图调用或LLM调用。

## 保持不变

- `top_k=5`
- `per_query_candidate_top_k=20`
- Query Planner与自适应路由
- MySQL/MinIO/Milvus分层存储
- Embedding模型与768维向量
- BM25/向量/RRF检索
- `chunk_max_chars=1200`
- `chunk_overlap_chars=0`
- 结构证据组、相邻块扩展、受控第二轮检索、选择性图扩展全部关闭
- 事实充分性门保持shadow
- 评测不调用分析LLM，不保存证据正文

## 预注册门槛

全部门槛必须同时满足：

### 绝对质量

- Candidate Hit@5 ≥ 80%
- Candidate Recall@5 ≥ 60%
- Candidate MRR ≥ 75%
- Candidate nDCG@5 ≥ 60%

### 相对质量

- Recall@5相对Baseline提升 ≥ 5个百分点
- 改善题数 ≥ 3
- Recall退化题数 = 0
- MRR变化 ≥ -2个百分点
- nDCG@5变化 ≥ -2个百分点

### 候选池与Top-5保留

- 候选池Recall ≥ 60%
- 候选池到Top-5的Gold保留率提升 ≥ 10个百分点
- 被Top-5淘汰的Gold数量必须下降

### 跨项目稳定性

- 至少3/4项目改善
- 每个项目Recall变化不得低于-2个百分点
- 香港中心/深圳丰隆之外，至少1个非起源项目改善

### 安全、成本与稳定性

- 路由准确率、Query拆分准确率、Topic Recall均为100%
- 负样本准确率不得下降
- 搜索调用次数不得增加
- Candidate P95延迟相对Baseline增加不得超过500ms
- 执行错误为0
- 新增LLM Token为0

## 运行纪律

- 实现与测试完成后，记录代码、数据、配置和门槛SHA-256；
- 正式评测只允许一组：一次Baseline，随后一次Candidate；
- 不得因首次结果不理想而重跑挑选更好结果；
- 不得根据逐题结果追加项目词、保证金词或问题专用规则；
- Development通过也不等于生产通过；
- 只有完整检索架构冻结后，才申请一个全新项目做最终Holdout。

## 结果分支

- 若候选池Recall高、Top-5保留率与跨项目门同时通过：003成为新的Development候选，再进入上下文证据组实验；
- 若保留率改善但总体Recall收益不足：说明保护过强，003拒绝；下一实验只能重新设计通用替换置信度，不能降低门槛；
- 若仍发生Recall退化：说明直接对齐不足以保护关系型证据，003拒绝；
- 若收益仍只集中于香港中心/深圳丰隆：按跨项目稳定性失败拒绝；
- 无论结果如何，都不运行旧Holdout/Challenge。
