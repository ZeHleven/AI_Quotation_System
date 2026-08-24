# 旗胜投标机会研判 Agent Phase 3E Tool/Context Control Plane 协议

版本：v0.1-r22  
日期：2026-08-12  
状态：协议冻结、代码增量已实现并完成本地隔离专项验证  
上游：Phase 3A Run Bootstrap、Phase 3B Plan Commit、Phase 3C Task Runtime、Phase 3D Run Lifecycle

## 1. 本阶段目标与边界

Phase 3E 建立运行大脑的工具与上下文控制面：Context Assembler、Tool Gateway、Tool Result Store 和异步操作恢复。它负责决定“某一有效 Attempt 能看到什么、能请求哪个工具、消耗多少预算、结果如何持久化和恢复”，但不执行模型、OCR、视觉、解析、检索、计算、对象存储或任何外部服务。

本阶段没有新增外部 API，也不开放模型执行器或工具适配器。内部服务只返回已签名的调用 envelope 或持久化受控结果；功能开关 `FEATURE_BID_ASSESSMENT_PHASE3_TOOL_CONTEXT=false` 默认关闭。旧 `bid_intake_*` 不修改。

## 2. 权威对象

### 2.1 Context Manifest

每次上下文装配都绑定 `run_id/task_id/task_attempt_id/fencing_token`、Run 的全部 frozen version、TaskContract hash、context/budget profile 和 assembler version。Manifest 只可包含：

- 当前 Run Manifest 且属于当前 ParseHead 的 Evidence；
- 当前 Task 的直接依赖 Checkpoint 输出引用；
- 当前 Run、当前 Task 的历史 ToolResult 引用；
- 当前 Attempt 的工作状态哈希；
- 确定性的 token 估算、压缩级别和预算排除清单。

P0/P1 证据不可因预算静默丢弃；超过 hard token limit 必须 fail closed。相同 Attempt、相同语义输入产生相同 `manifest_hash` 和确定性 `context_manifest_id`，重放返回原对象。

### 2.2 Tool Invocation

模型可见参数只通过 `tools.schema.json` 校验，`additionalProperties=false`；Assessment、Run、Task、Scope、Manifest、版本和对象存储定位均由服务端注入，模型不得提交。Tool Gateway 还必须校验：

- 当前 Lease/Attempt/Fence 有效；
- Context Manifest 属于同一 Attempt/Fence；
- Task 注册表的 tool profile 与 allowlist；
- Run 绑定的 frozen ToolRegistry version；
- 每 Attempt 的工具调用预算；
- `(task_attempt_id,idempotency_key)` 幂等，请求哈希不同则冲突。

Gateway 生成短生命周期语义的 HMAC scope token；数据库只保存 token hash。适配器必须先验证 token，再使用服务端 envelope，不能信任模型输入补充 Scope。

### 2.3 Tool Result

ToolResult 是不可变观察结果，状态统一为 `ok/no_result/partial/failed/unauthorized/invalid_arguments/missing_inputs/stale/budget_exhausted`。结果包含 summary、data hash、Evidence 引用、warnings、metrics、是否截断和 expiry。超过 24 KiB 的结果必须使用受控外部引用；本阶段不实现真实对象存储适配器。Result 默认保留 30 天，并且只允许同一 Run、同一 Task 的有效新 Attempt 分页读取，禁止跨 Task/Run 引用。

## 3. 同步与异步状态机

同步调用：`accepted -> succeeded|failed`，在同一事务内写 ToolResult、Invocation 终态和审计；相同结果哈希可重放，不同结果复用同一 Invocation 必须冲突。

异步调用必须先写与 Context Manifest 绑定的不可变 Checkpoint，再执行：

1. Invocation `accepted -> pending`，创建 AsyncOperation；
2. Attempt/Task/Run 进入 `waiting_operation`，释放 Lease，并写 `bid.task.waiting_operation.v1`；
3. 操作回执写 ToolResult，旧 Attempt 记为 `cancelled` 且错误码为 `BID_TOOL_OPERATION_CONTINUATION_TRANSFERRED`；
4. Task 置 `ready`、Run 置 `queued`，写 `bid.task.ready.v1`；
5. 下一次 Lease 创建 attempt_no/fencing 单调递增的新 Attempt，携带最近 Checkpoint 恢复，并可读取同 Task 的历史 ToolResult。

旧 Attempt 不能被复活。重复回执只有结果哈希相同才可重放；取消、重试或终态 Run 会同时把所有 `accepted/pending` Invocation 围栏为 `cancelled`。晚到回执必须返回 stale/conflict，不能重新排队 Task。

## 4. 数据表与迁移门禁

线性 revision `20260812_0095`（down revision `20260811_0094`）新增：

- `bid_context_manifests`：不可变上下文权威；
- `bid_tool_invocations`：调用、预算、幂等、Fence、Checkpoint/AsyncOperation 关联；
- `bid_tool_results`：不可变结果权威；
- `bid_checkpoints.context_manifest_id -> bid_context_manifests.id` 外键和索引。

早期 Phase 3C 曾把 `context_manifest_id` 作为预留字符串字段，因此 0095 只允许在线迁移，并在升级前确认所有既有值为空；发现非空值时拒绝自动“重新解释”，必须先导出和显式治理。降级也只允许在线执行，并要求三张新表和 Checkpoint 引用均为空，防止抹除审计与恢复血缘。

本地代码唯一 head 变为 `20260812_0095`。该 migration 仅允许独立本地/开发数据库；目标 ECS 最近只读 head 仍为 `20260808_0082`，不得连接、迁移、备份、重启或改动 ECS/CentOS/真实 MinIO/Redis。

## 5. 失败与事务规则

- Context、Invocation、Result、Attempt/Task/Run 状态、Outbox 和审计由调用者在一个数据库事务内提交；任一步失败全部回滚。
- 所有写入先持有当前 Attempt/Fence 或 AsyncOperation/Invocation/Task/Run 行锁。
- Context hash、request hash、result hash 都使用 canonical JSON + SHA-256；任何幂等键下的哈希漂移均 fail closed。
- Evidence 只能引用当前 Run Manifest 和当前 ParseHead；文件名、MIME、parser_hint 仍不能成为标段或工具 Scope 权威。
- 维护任务只负责围栏、超时和恢复，不主动执行模型或工具。

默认关闭的 30 秒维护入口只扫描 `operation_type=tool:*` 且已超过 `timeout_at` 的 active AsyncOperation；它持久化标准 failed ToolResult、把 Operation 标为 `timed_out`、围栏旧 Attempt，并使 Task 在新 Fence 上恢复。并发晚到回执由行锁、Operation 状态和结果哈希共同拒绝。

默认关闭的 30 秒维护入口只扫描 `operation_type=tool:*` 且已超过 `timeout_at` 的 active AsyncOperation；它持久化标准 failed ToolResult、把 Operation 标为 `timed_out`、围栏旧 Attempt，并使 Task 在新 Fence 上恢复。并发晚到回执由行锁、Operation 状态和结果哈希共同拒绝。

## 6. 验证门禁与交接

已完成 Phase 3E 合同、Context、Tool Gateway、同步/异步、幂等、预算、Fence、回滚、取消/恢复和 0095 拓扑专项验证：合同与迁移拓扑 117、API/Phase 3C/3D 相邻链 16、Outbox/SSE/维护恢复 13，共 `146 passed`。

验证仅使用本地隔离数据库与 fake；未运行真实样例、OCR/视觉解析、模型或真实工具调用，未连接或改动 ECS/CentOS/真实 MinIO/Redis。
