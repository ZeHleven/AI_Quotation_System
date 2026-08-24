# 旗胜投标机会研判 Agent RQ2-B：BM25F + Semantic 候选融合

版本：v0.1-r43

日期：2026-08-15

状态：**协议、代码、Schema/Profile、本地隔离专项与“香港中心”三组消融 A/B 已完成。**

## 1. 目标与非目标

RQ2-A 的“香港中心”真实 A/B 表明，semantic-only 能补回2个 RQ1-D Top-5 零命中题，但总体 Hit@5/Recall@5 从 `0.92/0.86` 降至 `0.68/0.60`，且11题回退。RQ2-B 因此不允许语义替换词法，而是在同一权威范围、同一 RQ1-C Query Plan 上并行取得 RQ1-D 和 RQ2-A 的 Child 候选，再做确定性融合。

RQ2-B 只解决候选集合和首轮排序，不实现 cross-encoder/LLM reranker，不生成答案、Fact、Claim 或 Citation，不改变 Chunk、Parse、RetrievalIndex 或 SemanticIndex。

## 2. 冻结合同

| 对象 | 版本 |
|---|---|
| Fusion contract | `bid.evidence.candidate-fusion.v1` |
| Fusion profile | `bid-evidence-candidate-fusion-profile-v1-rq2b` |
| 默认 Profile | `bid-evidence-candidate-fusion-profile-v0-disabled` |
| Query | `bid-evidence-query-optimizer-profile-v1-rq1c` |
| Lexical | `bid-evidence-lexical-profile-v1-rq1d` |
| Semantic | `bid-evidence-semantic-profile-v1-rq2a-bce` |
| Adapter | `bid-evidence-mcp-rq2b-search@v6-bm25f-semantic-fusion` |
| 最大输出 | 8个不可引用 Retrieval Child |
| Reranker | 禁止，留给 RQ2-C |

机器产物：

- `contracts/bid_assessment/v1/rq2b-candidate-fusion-profile.json`
- `schemas/bid_assessment/v1/candidate-fusion.schema.json`

## 3. 双通道候选

每个请求只构建一次当前 Manifest/C3 IndexSet、RQ1-D Lexical Projection 与 RQ2-A Semantic IndexSet：

1. RQ1-C 生成最多6条确定性 Query，q1 保留原查询；
2. RQ1-D 执行 Child BM25基线、BM25F、Parent辅助和原查询 Anchor，取最终稳定排名前40；
3. RQ2-A 对同一组 Query 执行 BCE Child-only recall 和语义通道内 weighted RRF，取前40；
4. 只按 `retrieval_child_key` 合并；相同运行时 ID 对应不同稳定 Key、或同一稳定 Key 对应多个 Child 均 fail-closed；
5. Parent 与 Atom 不进入候选集合。

两个通道的候选深度是召回预算，不表示最终输出数量。文档、Role、Type过滤在双通道前共同执行，Tool 参数不能扩大 Run/Manifest 权限范围。

## 4. 加权 RRF

BM25/BM25F 原始分数与 BCE cosine 不在同一标尺，禁止直接线性相加。首版冻结 rank-only weighted RRF：

```text
fusion_score = 1.00 / (60 + lexical_rank)
             + 0.35 / (60 + semantic_rank)
             + 0.20 / (60 + min(lexical_rank, semantic_rank))  # 双通道重合时
```

- 词法权重1.00，保持已验证 RQ1-D 为主通道；
- 语义权重0.35，只做有界补强；
- 同一 Child 被两个独立通道命中时增加0.20一致性奖励；
- 不强制提升 semantic-only 候选，避免为单份“香港中心”资料写 promotion 规则；
- 并列按通道数、稳定 Child Key 排序，不使用随机 UUID；
- 融合后继续执行同 Parent 最多2个 Child 的多样性约束，再截取调用方 `top_k≤8`。

以上权重是首个待消融 Profile，不因单文档结果在代码中动态调整。任何权重变化必须发布新 Profile/Adapter 或在本 Profile 尚未验证前重新冻结并完整复测。

## 5. 失败与降级

- SemanticHead、模型血缘、Provider Hit 身份/Hash 不一致：fail-closed；
- Provider 暂时不可用或 SemanticIndex 未就绪：保留现有 retryable 错误，由 Dispatch/Checkpoint 恢复，不静默返回 RQ1-D 并冒充融合成功；
- 语义通道成功执行但合法零结果：允许输出词法候选，并记录 semantic candidate count=0；
- 词法通道零结果：允许输出语义候选；
- 两通道均为空：`no_result`；
- 历史 v4/v5 Dispatch 继续使用冻结 Adapter，不升级为 v6。

## 6. 输出与证据门

Search Hit 新增 `fusion_channels`、lexical/semantic rank、两通道来源分、融合分；有语义命中的 Child 还携带 semantic index/vector hash。Search 结果记录：

- Retrieval/Query/Lexical/Semantic/Fusion Profile；
- Manifest、C3 IndexSet、Lexical ProjectionSet、Semantic IndexSet Hash；
- 候选数、重合数、Fusion result hash；
- Query Plan、每条 Route 的 `executed_mode=hybrid`；
- 完整 payload result hash。

Search 仍只返回 `retrieval_child`、`is_citable=false`、`context_read=false`。后续 Fact/Claim 必须通过既有 `evidence.read` 获取同 Child 下的可引用 Atom；RQ2-B 不放宽任何证据门。

## 7. 配置与迁移门禁

默认配置：

```text
FEATURE_BID_ASSESSMENT_RQ2B_CANDIDATE_FUSION=false
BID_EVIDENCE_CANDIDATE_FUSION_PROFILE_VERSION=bid-evidence-candidate-fusion-profile-v0-disabled
```

启用 RQ2-B 必须启用 RQ2-A 及其 RQ1-D/RQ1-C/PDF-C3/Phase 2 前置闭包，并精确选择 v1 Fusion Profile。只设置 Profile 或只设置 Feature Flag 均 fail-closed。

RQ2-B 的融合结果是由现有不可变 C3/Semantic 权威和冻结 Profile 确定性派生的 Search Result，现有 Dispatch/Result Store 已冻结 Adapter 和结果正文/Hash；不新增表、字段、事件或受约束枚举，因此不新增 Alembic revision，唯一 head 保持 `20260815_0103`。

## 8. 本地隔离验证

用户已授权并完成以下无重复矩阵，共 `245 passed / 0 failed`：

- 合同/Schema/Profile/配置与纯 Fusion 110；
- PDF-C3/Evidence MCP v2—v6、稳定 Key、空通道、Provider/Stale、Adapter 围栏 24；
- Query Optimizer、BM25F、Semantic 状态机相邻专项 22；
- Phase 3E/3F、Dispatch/事务/幂等/Checkpoint/恢复、API-41/SSE 29；
- 0083—0103 线性迁移与隔离 upgrade/downgrade 60。

真实资料首次运行还发现并修正了一个合成测试未覆盖的合同缺口：PDF-C1 真实 Retrieval Child Key 为 `chunk:<sha256>`，而 RQ2-B 初稿只接受合成 fixture 的 `child:<sha256>`。最终 Fusion 与 Schema 同时兼容冻结历史 `child:` 和真实权威 `chunk:`，仍禁止不带稳定 Hash 的身份进入候选。

## 9. “香港中心”三组消融 A/B

同一307页 PDF、同一25题/42目标 Silver、同一 RQ1-B ParseHead、PDF-C3 RetrievalIndexHead 与 RQ2-A SemanticIndexHead：

| 指标 | RQ1-D lexical | RQ2-A semantic-only | RQ2-B fusion | Fusion - Lexical |
|---|---:|---:|---:|---:|
| Hit@5 | 0.92 | 0.68 | **0.96** | +0.04 |
| Target Recall@5 | 0.86 | 0.60 | **0.90** | +0.04 |
| Precision@5 | 0.208 | 0.136 | **0.216** | +0.008 |
| MRR@5 | 0.584 | 0.478 | **0.651333** | +0.067333 |
| nDCG@5 | 0.634161 | 0.476004 | **0.693317** | +0.059156 |
| Hit@8 | 0.96 | 0.76 | 0.96 | 0.00 |
| Target Recall@8 | **0.92** | 0.68 | 0.90 | -0.02 |
| Atom Read Recall@5 | 0.82 | 0.60 | **0.86** | +0.04 |
| Atom-only 违规 | 0 | 0 | 0 | 0 |

Fusion 保留并补回 semantic-only 发现的 `HKC-C3-012`、`HKC-C3-025` 两个词法 Top-5 零命中题；同时 `HKC-C3-020` 被挤出 Top-5/Top-8，`HKC-C3-004` 的多目标 Top-8 Recall 回退0.5。共有10题 MRR提升、3题 MRR回退，说明 RQ2-B 已证明语义候选的互补价值并提升总体 Top-5，但还不是最终排序器；下一步 RQ2-C 应在冻结候选上做轻量重排，重点修复挤出而不是继续提高语义权重。

语义权威为1个 ready Index、1个 Head、1244个唯一 Entry，全部768维且 vector hash 长度64；25题均生成 Fusion Hash，确定性重放一致。Lexical/Semantic/Fusion Search P95 分别约 `1865/606/1835ms`。评测使用固定本地 BCE 快照、CPU 与非生产 `isolated-bce-exact-cosine`；`production_milvus_adapter_executed=false`，没有调用 OCR、视觉、生成模型、外部 MCP，也没有连接 ECS/CentOS、真实 MinIO/Redis/Milvus。

最终私有输出位于 `outputs/bid_assessment_pdf_c3_quality/private_hkc_silver_v1_20260815_ab_shared_index_rq2b_r43_validated/`。生产 Milvus Adapter 的首次真实 daemon 联调仍保留为部署前门禁。
