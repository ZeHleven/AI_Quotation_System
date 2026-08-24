# 旗胜投标机会研判 Agent PDF-C3：role-aware Evidence MCP / Retrieval Profile、索引与失效协议

版本：v0.1-r38

日期：2026-08-15

状态：代码增量与本地隔离专项验证完成；真实单文档 Silver 基线已建立，检索质量未达可用门槛

## 1. 目标与边界

PDF-C3 把 PDF-C1/C2 形成的 `Section Parent → Retrieval Child → Evidence Atom` 从“存储约定”提升为检索运行时的强协议：

- `evidence.search` 只返回 `retrieval_child`，用于候选召回，不可直接引用；
- `section_parent` 只作为低权重辅助召回通道，不作为搜索结果；
- `evidence.read` 只返回 `evidence_atom`，且只有 Atom 可进入 Fact、Claim 和 Citation；
- 每个文档版本、ParseRun 和 Retrieval Profile 形成不可变索引快照；
- MCP 只有在 RetrievalHead、ParseHead、Profile 与 Run Manifest 全部一致时才可读取；
- 任何输入、Hash 或 Head 漂移都 fail-closed，不回退到旧索引或全 Fragment 检索。

本阶段不引入 embedding、向量数据库、模型、OCR/视觉、公网检索或原始文件读取；不复用旧 `bid_intake_*` 权威表。查询规划、BM25、RRF 和 MCP 传输方法可复用，但权威数据只来自新数据域。

## 2. 冻结版本

| 对象 | 版本 |
|---|---|
| Chunk role contract | `bid.evidence.chunk.v2` |
| Parser profile | `bid-document-parser-profile-v2-pdf-native-layout` |
| Retrieval index contract | `bid.evidence.retrieval-index.v1` |
| Retrieval profile | `bid-evidence-retrieval-profile-v2-role-aware` |
| Evidence MCP | `bid-assessment-evidence-mcp/v2` |
| Index coordinator | `bid-evidence-retrieval-index-coordinator-v1` |

机器合同见：

- `contracts/bid_assessment/v1/pdf-c3-role-aware-retrieval-profile.json`
- `schemas/bid_assessment/v1/evidence-retrieval.schema.json`

## 3. 索引权威

### 3.1 `bid_evidence_retrieval_indexes`

一行代表一个不可变派生输入 `(document_version_id, parse_run_id, retrieval_profile_version)`。状态仅允许：

`queued → building → ready`，或收敛为 `failed/stale`。

`input_hash` 冻结文档版本、ParseRun、Parse result hash、Role contract 和 Retrieval Profile。`result_hash` 只在 ready 时存在，覆盖稳定 Entry 描述，不包含随机数据库物理 ID。

### 3.2 `bid_evidence_retrieval_entries`

每个 Entry 对应一个 `retrieval_child`，并冻结：

- Child/Parent 的稳定 Evidence Key；
- Child 的检索文本与 `retrieval_hash`；
- 页范围与顺序；
- 所属 Parent；
- 源 Atom 的稳定 Key、物理 ID、数量与集合 Hash；
- `entry_hash`。

Entry 不是事实证据；它只负责把 Child 命中安全地映射到可引用 Atom。

### 3.3 `bid_evidence_retrieval_heads`

Head 主键是 `(document_version_id, retrieval_profile_version)`，同时以组合外键绑定 `current_index_id + current_parse_run_id`，避免 Head 与 Index 的 ParseRun 在数据库层发生漂移。

## 4. 构建、幂等与恢复

1. `bid.document.parsed.v1` 由独立 processed marker 幂等消费。
2. 只有 PDF-C2 v2 ParseRun 的 `succeeded/partial` 结果可以排队。
3. 30 秒维护任务扫描 queued Index；一次构建只读取该 ParseRun 的 EvidenceFragment。
4. 构建逐行验证 Role、Parent/Child/Atom 层级、citable 标记、Evidence Key、text/locator/retrieval Hash 与页范围。
5. 所有 Entry、Index ready 和 Head 晋升在同一事务完成；晋升前再次锁定并验证 ParseHead。
6. 维护扫描同时从当前 terminal PDF-C2 ParseHead 反向 reconcile 缺失 Index，覆盖 C3 启用前已解析或 Outbox 投递中断的存量文档。
7. 确定性合同错误收敛为 failed；事务或进程中断不会留下部分可见索引，queued 行仍是恢复真相。
8. 同一输入重复事件、reconcile 或维护扫描不会创建第二份索引。

Tool Adapter 将 `INDEX_NOT_READY` 保留为可重试失败，等待索引维护收敛；Profile、Role 或 Hash 无效属于不可重试合同失败。所有重试仍受 Phase 3F 的 DispatchAttempt、Lease、Fencing、预算和最大尝试次数约束。

## 5. Search 协议

`evidence.search` 的服务端范围固定为当前 Run 的不可变 Manifest，调用参数只能缩小范围，不能扩大：

- 可按 `document_roles`、`document_types`、`document_version_ids` 过滤；
- Query Planner 最多生成 3 个受控查询；
- Child 检索文本为 PDF-C1 冻结的 `context_prefix + child body`；
- Child BM25 是主通道，权重 1.0；Parent BM25 只把 0.35 权重映射回其 Child；
- 多查询、多通道使用 `RRF(k=60)`；
- 当前无语义后端时，semantic/hybrid 路由明确降级为 BM25 并返回 warning；
- 最多返回 8 个 Child hit，`is_citable=false`、`context_read=false`，同时返回对应 `source_atom_ids`；
- Parent 和 Atom 均不会作为 search hit 返回。

## 6. Read 与引用协议

`evidence.read` 只接受当前作用域内的 Child 或 Atom anchor：

- Child anchor 默认展开为其源 Atom；
- Atom anchor 默认只返回该 Atom；
- `neighbors`、`parent_section`、`bounded_pages` 只能在同一个 Section Parent 内扩展；
- radius 最大 2、页窗最大 4、最多 12 个 Atom、总字符数最大 12000；
- 每个返回项必须为 `evidence_atom + is_citable=true + context_read=true`；
- Read 会再次校验 Entry、Child、Atom 的 Role、层级、Hash 和源 Atom 成员关系。

Fact 权威写入层同步增加最终证据门：带 v2 Role 的 Evidence 只有 Atom 可被接受；旧无 Role Evidence 仅为默认关闭旧 Profile 的兼容路径。

## 7. Manifest、Profile 与 ParseHead 失效

### 7.1 ParseHead 变化

ParseHead 一旦指向新的 queued/running ParseRun，旧 RetrievalHead 即刻不可读。MCP 在维护任务尚未清理前也会返回 `BID_EVIDENCE_RETRIEVAL_INDEX_NOT_READY`；维护扫描随后把旧 Index 标为 stale 并删除对应 Head。禁止 stale fallback。

### 7.2 Retrieval Profile 变化

不同 Profile 使用不同 Head。开启 v2 后，历史 v1 Tool Dispatch 会被拒绝，不能绕过 Role-aware Index 走全 Fragment 检索。

### 7.3 Manifest 变化

文档级 Index 可被不同不可变 Manifest 复用，但每次 MCP 调用都计算 `index_set_hash`，覆盖：

- `manifest_id/manifest_hash`；
- 有序文档版本；
- Manifest 声明的文档角色与顺序；
- 每个文档当前 Index 的 `result_hash`。

因此新增/删除/换版文档或改变声明角色都会产生新的 Manifest 作用域 Hash，不会复用旧查询结果语义。

### 7.4 Hash 漂移

Parse result、Index input、Entry、Fragment text/locator/retrieval Hash 任一不一致均返回 `BID_EVIDENCE_RETRIEVAL_INDEX_INVALID`，不尝试在线修补。

## 8. 配置门禁

新增默认关闭配置：

```text
FEATURE_BID_ASSESSMENT_PDF_C3_ROLE_AWARE_RETRIEVAL=false
BID_EVIDENCE_RETRIEVAL_PROFILE_VERSION=bid-evidence-retrieval-profile-v1-legacy
```

启用 C3 必须同时启用 V1 Runtime、Phase 2 Document Worker、PDF-C2 和 Phase 4 Evidence MCP，并把 Parser/Retrieval Profile 精确设置为 v2；缺一项即启动 fail-closed。默认配置与现有 9001 MVP-1 行为保持不变。

## 9. 迁移门禁

PDF-C3 需要独立索引快照与 Head 权威，因此新增线性 Alembic revision：

`20260813_0101 → 20260814_0102`

0102 仅新增上述三张表，不修改旧 `bid_intake_*`。非空降级会拒绝执行，离线降级同样拒绝。本地隔离 upgrade/downgrade 与非空降级保护已经验证；在整个 Agent 获准上线前仍不得进入正式发布候选或应用到 ECS。目标 ECS 最近只读基线仍为 `20260808_0082`。

## 10. 本地隔离验证结果

用户已明确授权并完成以下验证矩阵：

1. Profile、JSON Schema、错误码和配置 fail-closed；
2. 0102 线性拓扑、隔离 upgrade/downgrade 与非空降级保护；
3. parsed event 幂等、Index 构建、重复扫描、事务回滚和失败恢复；
4. Parent/Child/Atom 层级、稳定 Key/Hash、源 Atom 映射；
5. Child-only Search、Parent 辅助 RRF、Role/Type/Version 缩域；
6. Atom-only Read、同 Parent 扩展、字符/数量/页窗上限；
7. ParseHead、Profile、Manifest、Hash 漂移失效；
8. Tool Adapter v1/v2 围栏与 Fact 引用门；
9. PDF-C2→PDF-C1→Phase 2、Phase 3E/F、MVP-1、API-41/SSE 相邻回归。

结果：

- PDF-C3、合同、配置、0102 迁移拓扑与隔离 upgrade/downgrade：`165 passed`；
- PDF-C2/C1、Phase 2 Parse/Lot Worker 相邻回归：`28 passed`；
- Phase 3E/3F、MVP-1、API-41/SSE 相邻回归：`18 passed`；
- 合计：`211 passed / 0 failed`。

上述 `211 passed / 0 failed` 验证只使用合成结构数据与完全隔离的 SQLite/Alembic 环境；没有读取真实 PDF，没有调用 OCR/视觉/模型/向量服务或外部 MCP，也没有连接 ECS/CentOS/真实 MinIO/Redis。非失败提示仅涉及本地 requests 依赖版本、Alembic `path_separator` 旧配置和既有 Pydantic 字段兼容告警，不影响验证结果。

## 11. 真实资料 Silver 检索质量基线

2026-08-15 经用户授权，在完全隔离的本地 SQLite 环境使用一份 307 页真实招标 PDF 建立了 25 题、42 个 phrase-anchored 目标事实的单审阅者 Silver 基线。仍未调用 OCR、视觉、模型、向量服务或外部 MCP。

关键结果：`Hit@5=0.32`、`Target Recall@5=0.24`、`MRR@5=0.1667`、`Atom Read Target Recall@5=0.16`；42 个目标中 5 个没有可引用 Atom。Evidence MCP v2 的角色安全保持成立，Atom-only Read 违规为 0，确定性重放一致。结构侧存在 1432 条警告，88.01% Child 低于 220 token，但质量投影仍为 `high / 100`。

结论是“协议和证据门通过，真实检索质量未通过”。下一增量进入 Retrieval Quality-1，按结构/可引用性修复、Parse Quality Gate、确定性 Query Optimizer、字段感知词法召回、语义召回/重排的顺序推进。完整基线与门槛见 `docs/bid-assessment-pdf-c3-real-document-silver-baseline-20260815.md`；原始文件、题集答案锚点和逐题输出保持本地私有且不入库。

RQ1-A 后，C3 Coordinator 的 Parser Profile allowlist 增加 `bid-document-parser-profile-v3-pdf-structure-rq1a`；原 v2 仍保留。Retrieval Profile、Index schema、Head 身份、Child-only Search、Atom-only Read 与所有失效规则均未改变。新 Profile 只能通过新的 ParseRun/ParseHead 生效，禁止原地改写历史 Index。
