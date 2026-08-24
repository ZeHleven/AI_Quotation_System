# 旗胜投标机会研判 Agent RQ1-C：确定性 Query Optimizer

版本：v0.1-r40

日期：2026-08-15

状态：协议、代码、机器合同、本地隔离专项验证与真实 Silver A/B 已完成

## 1. 问题与目标

“香港中心”RQ1-B 基线中，25题平均只执行 `1.08` 条计划查询。审查发现旧 `tender-query-planner-v1` 已能生成 `atomic_queries`、`fact_slot_queries` 和 supporting query，但 PDF-C3/Evidence MCP v2 只执行 `plan.queries[:3]`，其余结果只出现在审计 payload 中，没有进入 BM25/RRF。这使多事实、并列主体、字段别名和答案形状没有真正参与召回。

RQ1-C 的目标是在不调用模型、不读取原文、不改变 C1/C2 Chunk、不重建 C3 Index 的前提下，把单个问题变成可审计、可重放、最多6条的确定性 Query Plan，并由独立冻结的 Search Adapter 执行。

## 2. 冻结版本

| 对象 | 版本 |
|---|---|
| Query Plan contract | `bid.evidence.query-plan.v2` |
| Query Optimizer profile | `bid-evidence-query-optimizer-profile-v1-rq1c` |
| 复用的旧 Planner | `tender-query-planner-v1` |
| C3 Index profile | `bid-evidence-retrieval-profile-v2-role-aware` |
| Evidence MCP role contract | `bid-assessment-evidence-mcp/v2` |
| Search Adapter | `bid-evidence-mcp-rq1c-search@v3-rq1c-query-optimizer` |

Query Plan v2 是 Search 派生合同，不是新的索引合同。C3 RetrievalIndex/Entry/Head 和 Index result hash 均保持不变。

## 3. 确定性优化顺序

每次 Search 按以下固定优先级填充查询预算：

1. 原查询：规范化后固定为 `q1`，权重1.0，永不删除；
2. 并列主体：识别“分别/各自”前的并列主体，为每个主体拼接同一组字段与答案形状，权重1.0；
3. 投标字段别名：按版本化通用字段目录生成字段词、同义词和答案形状，权重0.9；
4. 旧 Planner atomic clause：权重0.85；
5. 旧 Planner fact slot：权重0.85；
6. 旧 semantic fact companion：权重0.75；当前无语义后端时明确降级为 BM25。

总查询数上限6、并列主体上限4、单查询上限500字符。输入先做 NFKC 与空白规范化，再以 `lower + ASCII/CJK 字符` 指纹去重。不得根据具体项目名、文件名、MIME、parser_hint 或检索结果动态改写查询，也不得生成问题中不存在的日期、金额、比例或资格等级。

## 4. 字段与答案形状

首版字段目录覆盖招标工程名称/地点、面积/区域、Scope 包含与排除、承包计价与调价、投标人/项目经理资格、投标/履约担保、替代方案、有效期、递交地点与截止时间、标书份数、发标日期、踏勘、开工/工期/完工认定、税率、预付款/进度款/暂停支付/结算款/质保金、保修期、索赔与工期延长、争议管辖、质量标准/检测主体和评标方法。

目录只保存通用投标概念和答案形状，例如 location、money、ratio、date、duration、count、qualification、boolean、condition；它不保存“香港中心”或任何项目答案。排除、否定结果和缺失日期模板保留 polarity，避免同义扩展把“不含/不允许/不批准/未提供”改写成相反含义。

## 5. 检索执行与 Hash

Query Plan v2 中每条 query 都带 `query_id/text/kind/weight/field_codes/answer_shapes/reason_codes/subject/polarity`。完整 Plan 自哈希，并作为 Evidence MCP Search result hash 的组成部分。

每条 query 分别走现有确定性 Retrieval Router；Child BM25 排名按 query weight 进入 `k=60` 的 weighted RRF，Parent BM25 仍只以 `0.35 * query weight` 辅助其 Child。Search 仍只返回不可引用 Retrieval Child，Read 仍只返回同 Parent 的可引用 Atom，RQ1-C 不改变证据角色门。

## 6. Adapter、配置与恢复

新增默认关闭配置：

```text
FEATURE_BID_ASSESSMENT_RQ1C_QUERY_OPTIMIZER=false
BID_EVIDENCE_QUERY_OPTIMIZER_PROFILE_VERSION=tender-query-planner-v1
```

启用时必须同时启用 RQ1-B、PDF-C3 role-aware Retrieval 和 Evidence MCP，并精确选择 `bid-evidence-query-optimizer-profile-v1-rq1c`。新 `evidence.search` Dispatch 冻结为 v3 RQ1-C Adapter；功能关闭或 Profile 不匹配时 fail closed。历史 v2 role-aware Dispatch 继续执行旧 Planner，不会被运行时开关悄悄改写。

Query Optimizer 是纯函数，没有新数据库权威、租约或异步状态；Dispatch/Attempt/Checkpoint/Result Store 继续复用 Phase 3E/3F。发送后未知结果、取消、超时和 Fence 语义不变。

## 7. 兼容与迁移边界

- RQ1-C 关闭时，Evidence MCP v2 的旧 Query Plan、检索排序和 result hash 语义保持不变；
- RQ1-C 启用时只改变新 v3 Search Dispatch 的 Query Plan 和 Search result hash；
- C3 索引不包含 query，因此无需重建或创建新 Head；
- 不新增表、字段、枚举或事件，不需要 Alembic revision；代码唯一 head 保持 `20260814_0102`；
- 不修改旧 `app/agents/bid_intake` 权威和 `bid_intake_*` 数据域，不得应用到 ECS 或正式发布候选。

## 8. 本地隔离专项验证

授权矩阵按无重复用例统计共 `202 passed / 0 failed`：RQ1-C 合同/Schema/配置、原查询锚点、NFKC 去重、6条预算、并列主体、字段别名、答案形状、否定 polarity、稳定 Hash/篡改拒绝、旧 Planner/v2 Adapter 兼容、C3/Evidence MCP weighted RRF 共48项；总合同74项；0083—0102 线性迁移拓扑58项；Phase 3E/3F/API-41/Dispatch/Checkpoint/恢复相邻链12项；事务/Outbox/SSE/维护恢复10项。首次运行发现一处测试选择器把带同字段标签的原查询误当成字段别名扩展，修正测试定位条件后最终矩阵全部通过，生产逻辑未因此改变。

运行器新增 `--compare-query-optimizer-profile`，在同一隔离 SQLite、同一 ParseHead、同一 RetrievalIndexHead 上顺序执行两个 Query Profile，避免独立 authority UUID 导致 Index hash 不可比。无新增数据库迁移，唯一 Alembic head 保持 `20260814_0102`。

## 9. “香港中心”25题共享 Index A/B

使用同一307页真实 PDF、同一25题/42目标 Silver、同一 RQ1-B v4 Parse、同一 C3 Index，对 `tender-query-planner-v1` 与 RQ1-C 做隔离 A/B。两组共享同一 Parse Run、Retrieval Index ID/Input Hash/Result Hash；Parent/Child/Atom 数量不变，Atom-only Read 违规均为0，确定性重放均一致。

| 指标 | RQ1-B/旧 Planner | RQ1-C | 变化 |
|---|---:|---:|---:|
| 平均计划查询数 | 1.08 | 3.84 | +2.76 |
| Hit@5 | 0.44 | 0.72 | +0.28 |
| Target Recall@5 | 0.38 | 0.66 | +0.28 |
| MRR@5 | 0.218 | 0.394 | +0.176 |
| nDCG@5 | 0.2383 | 0.4393 | +0.2010 |
| Atom Read Target Recall@5 | 0.38 | 0.66 | +0.28 |
| Hit@8 / Target Recall@8 | 0.48 / 0.44 | 0.84 / 0.78 | +0.36 / +0.34 |
| Top-5 零命中题 | 14 | 7 | -7 |
| Search P50 / P95 | 290 / 397 ms | 690 / 1065 ms | +400 / +668 ms |

整体质量明确提升且证据角色、索引血缘和确定性均无回退，RQ1-C 因此完成阶段验收。仍有两个需要后续处理的排序现象：`HKC-C3-011` 的目标从 Top-5 移到第6位，`HKC-C3-009` Recall 不变但首命中排名下降；同时 Hit@5 `0.72` 与 Recall@5 `0.66` 仍低于建议门槛 `0.80/0.70`，查询扩展也使单文档词法 Search P95 增至约1.07秒。下一增量进入 RQ1-D 字段感知词法召回、通道消融与模板/高频词抑制，而不是直接接模型掩盖排序问题。

本阶段没有调用 OCR、视觉、模型、Embedding、向量数据库或外部 MCP，也未连接 ECS/CentOS、真实 MinIO/Redis 或其他外部环境。
