# 旗胜投标机会研判 Agent RQ2-C：冻结候选上的轻量重排

版本：v0.1-r44

日期：2026-08-15

状态：**协议、Schema/Profile、默认关闭配置、代码、本地隔离专项与“香港中心”四组 A/B 已完成。**

## 1. 问题与目标

RQ2-B 已证明 BM25F 与 BCE Semantic 候选互补：25题 Silver 的 Hit@5/Recall@5 达到 `0.96/0.90`，但 `HKC-C3-020` 被融合候选挤出 Top-5/Top-8，`HKC-C3-004` 的多目标 Top-8 Recall 也回退。继续提高语义权重会扩大这种风险，不能作为 RQ2-C。

RQ2-C 只解决“正确 Child 已在 RQ2-B 候选池，但最终 Top-K 被弱相关候选挤出”的排序损失：

1. 冻结 RQ2-B 稳定融合顺序前20个 Retrieval Child；
2. 使用本地、离线、固定 revision 的 BCE Cross-Encoder 对 q1 原查询与20个 Child 配对打分；
3. 以 RQ2-B Parent 多样性 Top-K 作为唯一 Baseline 骨架；
4. 只允许具备明确正分差的候选替换未保护尾部位置；
5. 没有合法 promotion 时，最终有序 Top-K 必须与 RQ2-B 逐项一致。

它不增加 Query、不召回新候选、不读取原始文件、不调用生成模型，也不改变 Search/Read 的证据角色。

## 2. 冻结合同

| 对象 | 版本 |
|---|---|
| Rerank contract | `bid.evidence.lightweight-rerank.v1` |
| Rerank profile | `bid-evidence-rerank-profile-v1-rq2c-bce` |
| 默认 Profile | `bid-evidence-rerank-profile-v0-disabled` |
| 上游 Fusion | `bid-evidence-candidate-fusion-profile-v1-rq2b` |
| Adapter | `bid-evidence-mcp-rq2c-search@v7-bce-anchor-preserving-rerank` |
| 候选窗口 | RQ2-B 稳定顺序 Top-20 |
| 最大输出 | 8个不可引用 Retrieval Child |

机器产物：

- `contracts/bid_assessment/v1/rq2c-lightweight-rerank-profile.json`
- `schemas/bid_assessment/v1/lightweight-rerank.schema.json`

## 3. Provider 与模型边界

首个 Profile 冻结：

```text
provider_id=bce-cross-encoder-local
model_id=maidalun1020/bce-reranker-base_v1
model_revision=eb7650fca1d81e2856fbd0d522488844aa502735
max_sequence_length=512
score_transform=sigmoid
pair_budget=20
```

- 模型只允许 cache-only 离线加载，禁止运行时联网下载；
- 输入 Query 只使用 RQ1-C 保留的 q1 原查询，防止扩展 Query 再次覆盖原始意图；
- 文本只使用 C3 当前 RetrievalIndexEntry 的 `retrieval_text`，不旁路读取原文件；
- Provider 必须为全部且仅全部冻结 Child 返回唯一 `[0,1]` 分数；缺项、重复、越界、NaN、模型血缘不一致均 fail-closed；
- Provider 临时不可用由 Phase 3F Dispatch/Checkpoint 走既有 retryable 恢复，禁止静默降级并冒充 RQ2-C 成功。

## 4. 锚点保护与尾部替换

RQ2-C 不按 Cross-Encoder 分数全量洗牌。先从同一份冻结候选生成 RQ2-B Baseline Top-K，再保护：

1. Baseline Top-1；
2. Baseline 中 lexical rank 最好的 `top_k - 2` 个词法锚点（至少1个）。

候选按 `rerank_score desc → fusion_rank asc → stable child key asc` 检查。只有同时满足下列条件才允许 promotion：

- 候选尚未在 Baseline Top-K；
- `rerank_score >= 0.30`；
- 候选与被替换尾部项分差 `>= 0.08`；
- 被替换项不是受保护锚点；
- 替换后同一 Parent 集中度不得高于 RQ2-B Baseline；当候选中有足够 Parent 时仍执行每 Parent 最多2个 Child，候选不足时只继承 RQ2-B 既有 overflow，不得因 promotion 进一步恶化；
- 每次只替换一个既有位置，每题最多2次。

不得强制提升 semantic-only 或 lexical-only 候选。未替换位置保持原 ID 和原顺序；`promotion_count=0` 时必须满足 `final_child_keys == baseline_child_keys`。这条变形不变量来自旧 Agent 003 曾经“没有 promotion 仍改变多 Query Baseline”的失败教训。

## 5. Hash、重放与失效

Rerank Result Hash 纳入：

- RQ2-C Profile；
- RQ2-B Fusion Result Hash；
- RQ1-C Query Plan Hash；
- 固定模型描述；
- 每个候选的稳定 Child/Parent Key、Fusion rank/score、Lexical/Semantic rank、Retrieval Hash 和 q1 Query Hash；
- 全部 Rerank Score；
- Baseline/Final 稳定 Key 和 promotion 审计。

Manifest、ParseHead、RetrievalHead、SemanticHead、Lexical Projection、Query Plan 或 Fusion 变化都会先改变上游 Hash，再使 Rerank Hash 变化。模型 revision、阈值或选择政策变化必须发布新 Profile 与 Adapter，不能修改 v7 历史语义。

## 6. Evidence MCP v7 与证据门

Evidence MCP v7 Search 增加：

- `rerank_profile_version`、`rerank_model`；
- `candidate_rerank` 完整稳定审计与 result hash；
- Hit 的 `fusion_rank`、`rerank_score`、`rerank_input_hash`、`rerank_protected_anchor`、`rerank_promotion_sequence`、`rerank_replaced_child_key`。

Search 仍为 `retrieval_child / is_citable=false / context_read=false`。Fact/Claim 仍必须调用既有 `evidence.read` 回源为同 Parent 下的 `evidence_atom / is_citable=true`，RQ2-C 不放宽 Citation、Manifest、ParseHead 或 Scope 门禁。

历史 v4/v5/v6 Dispatch 保留冻结 Adapter；只有新 Dispatch 且完整开启 RQ2-C 依赖闭包才选择 v7。

## 7. 配置与迁移门禁

默认关闭：

```text
FEATURE_BID_ASSESSMENT_RQ2C_LIGHTWEIGHT_RERANK=false
BID_EVIDENCE_RERANK_PROFILE_VERSION=bid-evidence-rerank-profile-v0-disabled
BID_EVIDENCE_RERANK_PROVIDER_ID=disabled
```

启用 RQ2-C 必须完整开启 RQ2-B/RQ2-A/RQ1-D/RQ1-C/PDF-C3/Phase 2 前置闭包，精确选择固定 Profile/Provider/Model/Revision，并保持离线加载。只设置开关、Profile 或 Provider 中任一部分均 fail-closed。

Rerank 是现有不可变上游权威的请求级派生结果；Dispatch 已冻结 Adapter，Result Store 已持久化正文和 Hash，不新增表、字段、事件或枚举。因此 **不新增 Alembic revision**，唯一 head 保持 `20260815_0103`。

## 8. 本地隔离验证结果

2026-08-15 经用户授权完成：

- RQ2-C 合同、Schema、配置、Query/Retrieval/Semantic/Fusion/Rerank 核心矩阵 `166 passed / 0 failed`；
- Dispatch、事务/幂等、Lease/Fencing、Checkpoint/恢复、API-41/SSE 与 0083—0103 迁移拓扑相邻矩阵 `201 passed / 0 failed`；两组无重复文件，共 `367 passed / 0 failed`；
- Alembic 保持唯一 head `20260815_0103`，RQ2-C 没有新增 revision；
- 固定 Reranker 权重 `pytorch_model.bin` 为 `1,112,244,270` 字节，SHA-256 为 `657771d9eaf9a92440a1d78a53c3c382eee0a1bb9ca313dbc08181deff295fe9`；本地加载只读固定 revision，独立 Worker 运行锁为 `numpy 1.26.4 / torch 2.6.0+cpu / transformers 4.44.2 / sentence-transformers 3.1.0`；
- Evidence MCP v7、历史 v4/v5/v6 Adapter、Atom-only Read、Provider fail-closed、零 promotion 恒等性和确定性重放全部通过。

“香港中心”307 页 PDF、25 题/42 个 Silver 目标在同一 ParseHead、RetrievalIndexHead、SemanticIndexHead 上完成四臂消融：

| Arm | Hit@5 | Recall@5 | MRR@5 | nDCG@5 | Hit@8 | Recall@8 | Atom Read Recall@5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| RQ1-D | 0.92 | 0.86 | 0.584000 | 0.634161 | 0.96 | 0.92 | 0.82 |
| RQ2-A semantic-only | 0.68 | 0.60 | 0.478000 | 0.476004 | 0.76 | 0.68 | 0.60 |
| RQ2-B fusion | 0.96 | 0.90 | 0.651333 | 0.693317 | 0.96 | 0.90 | 0.86 |
| RQ2-C rerank | 0.96 | 0.90 | 0.651333 | 0.693317 | **1.00** | **0.94** | 0.86 |

RQ2-C 共执行 500 个 Query/Child pair、4 次 promotion；21/25 题零 promotion 且有序结果逐项等于 RQ2-B。`HKC-C3-020` 的正确 Child 从 Fusion rank 11 提升到最终 rank 7，修复 Top-8 候选挤出；Top-5 指标和其余24题均无退化。Atom-only 违规为0，冻结候选池、Parse/Retrieval/Semantic 权威和确定性重放不变量均通过。

冻结 Worker 依赖下 CPU Search P95 从 RQ2-B `1525.684ms` 增至 RQ2-C `3583.660ms`，这是后续批处理/量化/缓存优化项，不影响本轮正确性门。单文档 Silver 仍只用于开发消融；进入默认启用候选前必须完成业务 Gold 复核和跨项目固定 Development/Holdout。评测未调用 OCR、视觉、生成模型、外部 MCP 或生产 Milvus，未连接外部环境。
