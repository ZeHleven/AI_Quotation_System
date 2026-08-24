# 旗胜投标机会研判 Agent Phase 4C-3：真实业务验收与 MVP Release Candidate

> 版本：v0.1-r57
> 日期：2026-08-17
> 状态：首次真实 PDF 业务验收已完成，MVP RC 已以 `accepted_with_follow_up` 不可变冻结
> 环境边界：仅允许完全隔离本地环境；不得应用到 ECS 或正式数据库

## 1. 阶段目标

Phase 4C-3 不再增加检索、模型或 DAG 能力，而是把“机器执行成功”收敛为可审计的“业务负责人已验收、可以作为 MVP 候选演示”。它解决三个问题：

1. Run succeeded、Report ready、Validation passed 只证明工程链完整，不等于业务负责人认可结果；
2. 七项硬门出现 fail/unknown 并不表示系统错误，业务验收应确认其结论、证据和后续动作是否正确，而不是强制七项全部 pass；
3. Run、Manifest、Scope、企业快照或报告发生变化后，旧的人工确认不得被复用。

## 2. 权威边界

新增唯一权威 `bid_mvp_release_candidates`，每个 Run 最多一条不可变记录。RC 绑定：

- 当前 succeeded Run 与 Run input hash；
- 当前 Manifest、Scope、最新冻结企业能力快照及各自 Hash；
- ready Report、Report Validation、Run Validation；
- Preliminary Decision 与 HG01—HG07 结果 Hash；
- 业务复核人、七项硬门复核、五项报告质量复核与总体验收说明。

人工复核与机器结论分离：

- `accepted`：七项硬门没有 fail/unknown，且所有业务复核确认；
- `accepted_with_follow_up`：存在 fail/unknown，但负责人确认该结论及证据正确，并为每个非通过硬门记录后续说明；
- `correction_required` 或 `not_reviewed`：只用于预览，禁止冻结 RC。

## 3. 两步冻结协议

### 3.1 零持久化 Validate

`POST /api/v1/bid-assessment-runtime-lab/release-candidates/validate`

- 只读重建当前权威血缘；
- 校验 Report Hash、Report Validation Hash、七项硬门投影、Decision、Claim/Citation 与 Atom-only 证据门；
- 返回 system blockers、review blockers 与稳定 Candidate Hash；
- 不写 RC、Audit 或 Outbox。

### 3.2 不可变 Freeze

`POST /api/v1/bid-assessment-runtime-lab/release-candidates`

必须同时携带：

- `Idempotency-Key`；
- 最近 Validate 返回的 `X-MVP-RC-Candidate-Hash`。

服务端在事务内锁定 Run 与 Assessment，重新计算 Candidate Hash；任何资料、Scope、企业快照、报告、Validation、硬门或人工复核变化均 fail-closed。成功后写一条 RC 和一条 AuditLog，不发 Outbox、不触发 Worker、模型或工具。

## 4. API 与权限

| API | 权限 | 模式 |
|---|---|---|
| `GET .../release-candidate?run_id=` | Run owner / admin | 本地 view-only、execute 均可读 |
| `POST .../release-candidates/validate` | admin | 仅本地 execute |
| `POST .../release-candidates` | admin | 仅本地 execute |

Runtime Lab 继续由服务器 Capability 决定按钮状态；浏览器不能提升权限。view-only 中间件仍在路由前阻断全部写请求。

## 5. 迁移与开关

- 新 Alembic revision：`20260817_0105`；
- down revision：`20260817_0104`；
- 新表：`bid_mvp_release_candidates`；
- 有数据时禁止 downgrade，离线 downgrade 同样拒绝；
- 新开关：`FEATURE_BID_ASSESSMENT_PHASE4_MVP_RELEASE_CANDIDATE=false`；
- 启动脚本使用显式 `-EnableMvpReleaseCandidate -AccessMode execute` 创建独立 `phase4c3` Lab；
- 不修改旧 `bid_intake_*`，不新增 Worker、Task、事件或模型调用。

## 6. 协议与工程验证

已按用户授权完成完全隔离的本地收口：

- 合同/Schema、0105 upgrade/downgrade、Validate 零持久化、全血缘漂移、人工复核矩阵、Candidate Hash、事务/ACL/view-only/幂等、Phase 4C-1/4C-2/Preflight/API-41/SSE，以及本地确定性全链共 `232 passed / 0 failed`；
- 动态 execute 使用合成 TXT 与本地确定性 Provider 跑通 27/27 Task：Run succeeded、Report ready、Run Validation passed；形成 31 次 Model Gateway 动作、20 次本地 Tool、89 个 Checkpoint、1 条 Claim 与 3 条引用；
- 动态运行暴露并修正两处夹具未覆盖的问题：Run Validation 自描述 `result_hash` 的完整性校验，以及确定性 Provider 汇总 Claim 未优先绑定文档 Fact；
- RC 零持久化校验中 13 项系统检查通过，旧 TXT/legacy 解析产生的 3 条引用因没有 `evidence_atom` 角色被 `CITATIONS_ATOM_ONLY` 正确阻断，冻结按钮保持禁用，未生成伪 RC；
- 同库切换 view-only 后 Worker/模型/写权限关闭，POST 返回 `403 / BID_MVP1_VIEW_ONLY`，RC 验收按钮禁用；一次性 9007/9008 服务均已停止，既有 9003 未改动。


## 7. 首次真实资料验收与 RC 冻结

用户明确授权后，在全隔离本地 SQLite/对象目录/进程内 Worker 环境中，使用“香港中心”307页真实 PDF、固定本地 BCE Embedding、RQ2-B 和 DeepSeek V4 Flash 完成了首次业务负责人验收：

- Run `run_5f87fd55fe1249a1b0d246e7f81c0e07` 与 Report `262cb95b-47af-4aa1-af18-c1c1e24e9dad` 分别收敛为 `succeeded` / `ready`；27 个 Task、88 个 Attempt、36 次模型调用、25 次本地 Evidence MCP Tool 调用和 99 个 Checkpoint 完成闭环；
- 生成3条 Claim、12条原文引用；14项 RC 系统检查全部通过，`CITATIONS_ATOM_ONLY` 违规0；
- 模型用量为 `281339 / 8556` 输入/输出 Token，账本成本 `26401` micro-USD（约 `$0.026401`）；
- 七项硬门为2 pass、1 fail、4 unknown，Decision 为 `no_bid`。因存在 fail/unknown 且已逐项记录后续处置，业务验收为 `accepted_with_follow_up`；
- RC Validate 首先确认0持久化，随后以 Candidate Hash `f77d02eded0792c252db525e98b47eeb51347b9a8fb25af8e7af43ef3abf8e08` 冻结；同一幂等请求重放命中原结果；
- 不可变 RC `d009ce1a-2f04-5bf5-b635-aa5de367b326` 版本为 `mvp-rc-20260817084759-f77d02eded07`，Release Hash 为 `e91131dd681297522727c7d7096426245b145c48bfa6d081c18f435d1b9d1c50`。

第一次实跑还暴露了一个真实失败案例：模型同时返回 Atom 引用候选和不可引用的 Search Child 候选，事实权威正确以 `BID_MVP1_AUTHORITY_ERROR` 拒绝了整次提交。修复保持证据门不变：Model Gateway 只保留 `evidence.read` 明确返回的 `citable_evidence_ids` 候选，整条丢弃混入 Child/空/重复引用的候选；全部无效时安全收敛为 `EVIDENCE_INSUFFICIENT`，不将 Child 重写为 Atom，也不放宽引用权威。

冻结后同库以 view-only 启动，Worker/模型/写权限关闭，Validate POST 在路由前返回 `403 / BID_MVP1_VIEW_ONLY`；浏览器已确认真实 Run、初筛报告、七项硬门、RC 状态和安全边界均可读，写操作禁用。本轮直接专项 `31 passed / 0 failed`。

## 8. 已知限制与发布边界

- 企业能力快照仍是隔离演示数据；HG02 的资质精确集合不匹配由演示快照触发，因此本次 `no_bid` 只证明硬门闭环正确，不能作为真实投标决策；
- Parse Quality 为84分 / `review_required`，第272页原生文本不足；本轮未调用 OCR 或视觉，必须保留人工复核；
- 实时链使用 RQ2-B，未使用 RQ2-C Reranker；本 PDF 属于 Development 资料，不是未见 Holdout；
- 本轮仅外联 DeepSeek 官方 API，未调用外部 MCP、生产 Milvus 或任何 ECS 依赖；
- Alembic 开发 head 保持 `20260817_0105`，所有 Agent 开关默认关闭，本 RC 仍是“本地可演示 MVP”，不是生产发布候选，不得应用到 ECS。
