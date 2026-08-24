# 旗胜投标机会研判 Agent Phase 3F Tool Adapter/Executor 调度协议

版本：v0.1-r23  
日期：2026-08-12  
状态：协议冻结、代码增量与本地隔离专项验证完成  
上游：Phase 3C Task Runtime、Phase 3D Run Lifecycle、Phase 3E Tool/Context Control Plane

## 1. 目标与边界

Phase 3F 把 Phase 3E 已授权的 Invocation 转换为可恢复、可审计、可围栏的执行派发。任何 Adapter I/O 前必须先原子持久化 Checkpoint、AsyncOperation、Invocation pending 状态和唯一 Dispatch 意图；Executor 只能领取数据库权威 Dispatch，不能直接消费模型参数或绕过 Tool Gateway。

本增量不调用真实模型、OCR、视觉、公网服务或真实对象存储，不修改旧 `bid_intake_*`。唯一真实 Adapter 是本地只读 `documents.outline`，只读取当前 Run Manifest 成员和 DocumentVersion 当前 ParseHead 的结构化 ParseUnit，不读取原始文件，不重新解析，也不根据文件名、MIME 或 `parser_hint` 推断任何业务事实。

## 2. 权威对象

`bid_tool_dispatches` 是一个 Invocation 的唯一派发意图，绑定 Invocation、AsyncOperation、Task/Attempt、Adapter/版本、不可变服务端 Envelope、HMAC scope token hash、稳定 `provider_request_id`、成本预留/实耗、重放策略和可变派发状态。

`bid_tool_dispatch_attempts` 是每次 Executor 领取的不可变 Attempt 轨迹。每次领取单调增加 `attempt_no/fencing_token`；旧 Worker 只能保留历史记录，不能结算新 Fence。

状态：

`queued -> leased -> sending -> succeeded|failed`；安全可重放错误进入 `retry_wait`，取消进入 `cancelled`，发送后不可安全重放且无法对账时进入 `uncertain`，耗尽重试进入 `dead_letter`。`awaiting_receipt` 为后续外部异步 Adapter 预留，本阶段本地 Adapter 不使用。

## 3. 派发与恢复规则

- Gateway 的 scope token 必须在 enqueue 时验证；数据库只保存 hash，Dispatch 领取时根据 Invocation/request hash 重新生成并二次比对。
- `(invocation_id)`、`(async_operation_id)`、`dispatch_key`、`provider_request_id` 全部唯一；跨 Executor 重试时 `provider_request_id` 保持不变。
- Executor 先在事务内把 Dispatch 从 `leased` 推进到 `sending`，提交后才调用 Adapter。数据库回滚不会发生 Adapter I/O。
- `safe_idempotent` Adapter 在发送后 Lease 丢失也可使用相同 provider request id 重放；`reconcile_required/no_replay` 在发送后 Lease 丢失必须进入 `uncertain`，不可猜测成功或重试收费。
- Adapter 回执只能在当前 Dispatch fence、当前 Operation/Invocation、当前 active Run/Task 状态下通过 Phase 3E Result Store 结算；迟到旧 Fence fail closed。
- Run 取消、显式重试和 AsyncOperation 超时会同时取消/终止未完成 Dispatch 及当前 DispatchAttempt；之后的回执不能重新排队 Task。

## 4. Adapter 注册边界

Phase 3F 代码注册表当前只包含：

- `documents.outline -> bid-local-documents-outline@v1`
- mode=`local_readonly`
- replay_policy=`safe_idempotent`
- reserved/actual external cost=`0`
- max attempts=`3`

未注册工具由 Executor 返回 `BID_TOOL_ADAPTER_UNAVAILABLE`，不能回退到旧 MCP、Dify、n8n 或公网服务。未来外部 Adapter 必须独立冻结 endpoint allowlist、凭据、请求脱敏、回调鉴权、provider 幂等/对账及成本上限后才能加入注册表。

## 5. 迁移与开关

线性 revision `20260812_0096`（down revision `20260812_0095`）新增 `bid_tool_dispatches`、`bid_tool_dispatch_attempts`，并为 `bid_async_operations(task_id,task_attempt_id,id)` 增加复合唯一键以建立聚合内外键。降级前要求两张 Dispatch 表为空，防止删除派发与费用血缘。

代码唯一 head 为 `20260812_0096`。新开关 `FEATURE_BID_ASSESSMENT_PHASE3_TOOL_EXECUTOR=false` 默认关闭；启用时强制依赖 `FEATURE_BID_ASSESSMENT_PHASE3_TOOL_CONTEXT=true`、`FEATURE_BID_ASSESSMENT_PHASE3_TASK_RUNTIME=true` 和至少 32 字符的 `BID_TOOL_SCOPE_SIGNING_KEY`。目标 ECS 最近只读 head 仍为 `20260808_0082`，本迁移不得应用到 ECS。

## 6. 验证门禁

Phase 3F 合同、0096 拓扑、enqueue 事务回滚、Dispatch 幂等/Lease/Fence、安全重放、发送后未知结果、本地 outline Adapter、Result Store、新 Attempt/Checkpoint 恢复、取消/超时联动和 Phase 3C—3E/API-41/SSE 相邻回归已获授权并完成本地隔离专项验证，共 `149 passed`。测试期间修复了固定时钟下 Attempt 结束时间倒序，以及 Result Store flush 前 Dispatch 终态尚未固化导致生命周期约束短暂失效的问题。

同时通过 JSON/Schema 解析、Python 编译、模型导入、Alembic head 和 `git diff --check` 等静态检查；未运行真实样例、OCR/视觉解析、模型、真实外部工具或真实对象存储调用，未连接外部环境。
