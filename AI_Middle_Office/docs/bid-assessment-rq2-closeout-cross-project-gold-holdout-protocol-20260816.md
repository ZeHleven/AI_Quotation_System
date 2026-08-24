# 报价资料研判 Agent RQ2 总收口与跨项目 Gold/Holdout 协议

> 版本：v0.1-r45
> 日期：2026-08-16
> 状态：协议、代码、专项、三项目 Development Gold/Snapshot 与正式跨项目 A/B 已完成；Development 准出失败，Holdout 未开始
> 运行边界：完全隔离本地环境；不得连接 ECS、CentOS、真实 MinIO/Redis、生产 Milvus 或外部 MCP

## 1. 这一步收什么口

RQ2-A 已提供 Child-only 语义召回，RQ2-B 已提供 BM25F + Semantic 冻结候选融合，RQ2-C 已在冻结 Top-20 上提供锚点保护的轻量重排。它们证明了单项目上的技术链可以工作，但“香港中心”既参与了问题发现，也参与了参数选择，不能单独证明跨项目泛化。

RQ2 总收口不再修改查询、召回、融合或重排参数，而是回答三个发布问题：

1. RQ2-C 在多个不同项目上是否稳定优于或至少不弱于 RQ2-B；
2. 结论是否来自项目级隔离、独立复核的 Gold，而不是一份资料上的过拟合；
3. 在完全未见项目的单次 Holdout 中，质量、证据安全、确定性和时延是否同时过门。

只有 Development Gold 和一次性 Holdout 都通过，RQ2 才能标记为完成。协议或代码完成不等于 RQ2 已完成，更不等于 Agent 可上线。

## 2. 旧 Agent 复用边界

直接复用旧 Agent 已经验证有效的评测治理方法：

- 以项目而不是题目或文档片段划分 Development/Holdout；
- 冻结数据、代码、配置、阈值和依赖 Hash；
- Holdout 不参与候选选择和调参；
- Holdout 最多一次正式执行，失败后不得重跑；
- Holdout 失败不能直接产生新关键词、规则或阈值，下一轮必须新增 Development 项目并建立新 Holdout 版本。

禁止复用旧 `bid_intake_*` 的数据库权威、Evidence ID、旧索引结果或旧 Holdout 结论。旧资料如纳入新 Development，必须在新数据域重新解析、重新生成 Parent/Child/Atom 并按新证据合同重新标注。任何曾用于 RQ1/RQ2 调试的项目都不得进入 Holdout。

## 3. 数据集合同

### 3.1 Development Gold

- 至少 3 个互不重叠的项目族；
- 至少 60 题，每项目至少 20 题；
- 每个 Split 至少覆盖 5 类问题；
- “香港中心”只能在 Silver 经第二位独立复核者复核、争议项裁决后进入 Development Gold；
- 允许继续观察失败样本，但任何算法改变都会生成新 Candidate/Profile/Freeze 版本。

### 3.2 Holdout

- 至少 2 个从未参与 RQ1/RQ2 调试的项目族；
- 至少 40 题，每项目至少 20 题；
- `prior_rq_exposure=none`；
- Project ID、项目族 Hash、源文件 SHA-256、规范化问题 Hash 与 Development 的交集必须全部为 0；
- 标签文件为 `private_restricted`，在正式执行前封存；
- 最多一次正式 baseline→candidate 执行。

第一版基准单位严格是“每项目一份主招标 PDF”。补遗、合同、清单或跨文档关系型问题属于后续评测扩展，不在本次收口时静默混入；这样能直接复用已经冻结的单 PDF-C2/C3 质量执行器而不改变评测语义。

### 3.3 Gold Case

每题至少冻结：

- `case_id/project_id/category/difficulty/question/answer_status`；
- 正例或资料不足题的一个或多个 Gold Target；
- 每个 Target 恰好一个 phrase anchor、PDF 页码，且该 phrase 必须落在同一 Atom；
- `evidence_role=evidence_atom`，Search Child 不能直接成为 Gold 引用；
- 标注者 Hash、独立复核者 Hash和 approved/adjudicated 状态；
- RQ2 v1 收口只评估可回答和“资料不足但有可引用依据”的问题；纯 `expected_no_result` 负例需要不同的无结果判定合同，本次不混入。

不得把项目特有关键词、目标原文或页码写进 Query Optimizer、BM25F 字段权重、语义召回或 Reranker 配置。

## 4. 冻结顺序

1. 完成 Development Gold 双人复核并计算 Dataset Hash；
2. 建立不可变 `DevelopmentSnapshot`，冻结 RQ2-C/Profile、Evidence MCP v7、Parser/Retrieval/Query/Lexical/Semantic/Fusion/Rerank、代码/合同/依赖 Hash 与阈值；
3. 只在该 Snapshot 上运行 Development RQ2-B→RQ2-C，并以同一 Parse/Index/Query Authority 聚合；
4. Development 全部门通过后，准备并封存 Holdout Dataset；
5. 才允许建立新的 `frozen_preholdout` Freeze；Freeze 必须绑定 Development Snapshot/报告 SHA-256、Development/Holdout Dataset Hash，并逐项继承相同代码/合同/依赖 Hash；
6. Holdout 只执行一次正式 baseline→candidate；
7. 结果为通过或该 Freeze 版本最终失败，不存在“修复后重跑同一 Holdout”。

CLI 在 Holdout 开始前以排他创建方式写 `ExecutionLedger(status=started, execution_count=1)`；同一路径已存在即拒绝第二次开始。聚合只接受与 Freeze/Dataset/Execution ID 完全一致的 started Ledger，成功写报告后原子收敛为 completed。执行中断只能从同一批已产生的不可变产物继续聚合，不能重新发起检索。

## 5. 准出指标

主统计采用项目宏平均，避免大项目用更多题数淹没小项目；同时报告逐题汇总、最差项目和分类宏平均。

| 门 | 阈值 |
|---|---:|
| Macro Hit@5 | ≥ 0.85 |
| Macro Target Recall@5 | ≥ 0.78 |
| Macro MRR@5 / NDCG@5 | ≥ 0.55 / 0.60 |
| Macro Hit@8 / Target Recall@8 | ≥ 0.92 / 0.85 |
| Macro Atom Read Target Recall@5 | ≥ 0.75 |
| Macro Citable Target Availability | ≥ 0.98 |
| Worst-project Hit@5 / Recall@5 | ≥ 0.75 / 0.65 |
| RQ2-C − RQ2-B Macro Hit/Recall@5/@8、Atom Read | 均 ≥ 0 |
| 逐题质量退化数 | 0 |
| Atom-only 违规 | 0 |
| Candidate Search P95 | ≤ 4500 ms |
| Paired Search Delta P95 | ≤ 2500 ms |

候选挤出恢复属于机制观察项：若该 Split 存在恢复则记录；若不存在可恢复样本，标记 `not_observed_in_this_split`，不能虚构为通过或失败。质量、安全和时延门仍必须全部通过。

## 6. 强不变量

每项目的 RQ2-B/RQ2-C 对比必须同时满足：

- 同一隔离数据库、ParseHead、RetrievalHead 和 SemanticHead；
- RQ2-C 接收的 RQ2-B 冻结候选池 Hash 完全一致；
- 零 promotion 时最终有序结果与 RQ2-B 逐项相同；
- Search 只返回不可引用 Child，Read 只返回 Atom；
- 确定性重放 Hash 一致；
- 数据、Profile、代码或依赖任一 Hash 变化都产生新 Freeze，禁止覆盖原结果。

任一强不变量失败时，报告直接失败，不以平均质量抵消。

## 7. 工程产物

- `contracts/bid_assessment/v1/rq2-closeout-cross-project-profile.json`：规模、冻结版本、阈值和盲测政策；
- `schemas/bid_assessment/v1/retrieval-benchmark.schema.json`：Dataset、Development Snapshot、Pre-Holdout Freeze、单次执行 Ledger 与 Report Manifest Schema；
- `app/services/bid_retrieval_benchmark.py`：Dataset/Freeze 校验、反泄漏检查、跨项目聚合和准出判定；
- `scripts/bid_rq2_cross_project_benchmark.py`：验证数据、检查隔离、按项目投影冻结 PDF-C3 Cases、生成 Freeze、排他开始 Holdout 和聚合报告的离线 CLI；
- `tests/test_bid_retrieval_benchmark.py`：合同、Hash、反泄漏、宏平均和退化门专项。

以上均是离线评测域，不新增 API、数据库表、运行时 Adapter 或 Alembic revision；唯一 head 保持 `20260815_0103`，Evidence MCP v7 和全部历史 Adapter 不变。

## 8. 当前状态与下一门

经用户授权，本轮已完成：

- RQ2 总收口合同/Schema `82 passed`；PDF-C3、RQ2-A/B/C 与 Evidence MCP v2—v7 相邻链 `44 passed`，合计 `126 passed / 0 failed`；
- 在完全隔离本地目录固化三份 Development 主招标 PDF：香港中心307页、深圳丰隆35页、泰丰花园41页，三份均为100%页面具有原生文本；
- 用户批准其余33题后，与27题历史双审 Gold 合并为60题/156目标/19类别的 `gold_approved` Development Dataset；Dataset Hash 为 `50857d3fd486af0ba7699aa9770d319fca2d31afe5d2d59a8ca8620deacac963`，批准记录 Hash 为 `f69769950c1fbae79807b2e49bbd08035bc9793b9e021753b4c9487748f83a07`；
- 正式 Dataset/Schema/项目内反重复校验通过，并建立不可变 `RQ2-DEVELOPMENT-SNAPSHOT-20260816-V1`，Snapshot Hash 为 `8ad347180312320cdebffc8767ce023bb0827855b0f0a9a454809144c4c4b00a`；
- 在该 Snapshot 上，以固定本地 BCE Embedding revision `9c0d82af44af61abe171ffae23fde5740c0ec1a8` 和 BCE Reranker revision `eb7650fca1d81e2856fbd0d522488844aa502735`，对三项目执行同一 Parse/Index/Query Authority 下的 RQ2-B→RQ2-C 正式 A/B；
- 泰丰曾被旧 Agent 用作 Holdout，本轮按用户明确指定改作历史已暴露 Development；它不能再次进入任何未见 Holdout，也不能单独证明泛化。

### 8.1 正式 Development 结果

| 指标 | RQ2-B Baseline | RQ2-C Candidate | Delta | 门禁 |
|---|---:|---:|---:|---|
| Macro Hit@5 | 0.966667 | 0.966667 | 0 | 通过 |
| Macro Target Recall@5 | 0.886667 | 0.886667 | 0 | 通过 |
| Macro MRR@5 | 0.763055 | 0.763055 | 0 | 通过 |
| Macro NDCG@5 | 0.731853 | 0.731853 | 0 | 通过 |
| Macro Hit@8 | 0.983333 | 0.983333 | 0 | 通过 |
| Macro Target Recall@8 | 0.921111 | 0.922500 | +0.001389 | 通过 |
| Macro Atom Read Recall@5 | 0.838055 | 0.838055 | 0 | 通过 |
| Macro Citable Target Availability | 0.955855 | 0.955855 | 0 | **失败：低于0.98** |
| Candidate Search P95 | — | 4044.799 ms | — | 通过：不高于4500 ms |
| Paired Search Delta P95 | — | +3705.179 ms | — | **失败：高于2500 ms** |

其余门禁：最差项目 Hit@5/Recall@5 为 `0.90/0.808333`，逐题质量退化数0，冻结候选挤出恢复1，Atom-only违规0，血缘/冻结候选池/零promotion恒等/确定性重放全部通过。报告 Hash 为 `b53067bde3e07c5c96b35d00f2464279092b96543f190a9d3e4fe7329cc613c6`。

正式评分器发现6/156个 Gold Target 未映射为可引用 Atom：香港中心2个、深圳丰隆3个、泰丰花园1个。此前预检证明目标短语和页码可在结构解析产物中找到，但正式 Evidence Atom 匹配口径仍未达到0.98门槛；该差异必须作为引用覆盖问题修复，不能通过放宽阈值或删除 Gold Target 消除。

当前状态必须写作：

`RQ2 closeout contracts passed / Development Gold frozen / formal A-B completed / Development gate failed / Holdout not started`

### 8.2 下一门

1. 在 Development 内定位并修复6个 Target 的正式 Atom 可引用映射，优先检查表格值拆分、同页多 Atom 和规范化匹配边界；Gold 业务含义不变，任何数据或解析合同改变都必须生成新 Dataset/Snapshot；
2. 优化 RQ2-C 增量执行成本，目标是保持冻结候选与逐题零退化的同时把 Paired Search Delta P95 降至2500 ms以内；不得直接放宽门槛；
3. 形成新 Candidate/Profile 和新 Development Snapshot，重新执行三项目 Development A/B；
4. 只有新 Development 报告全门通过，才另选两个从未暴露的项目建立40题 sealed Holdout 并创建 Pre-Holdout Freeze；当前三份资料都不得进入 Holdout。

本轮未调用 OCR、视觉、生成模型、外部 MCP、生产 Milvus 或任何外部环境；没有创建 Pre-Holdout Freeze，也没有开始 Holdout。
