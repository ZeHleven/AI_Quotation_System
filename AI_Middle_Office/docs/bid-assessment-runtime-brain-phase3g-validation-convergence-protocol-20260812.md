# 旗胜投标机会研判 Agent Phase 3G Run Validation/Convergence 协议

版本：v0.1-r24（Phase 3 总收口由 v0.1-r25 追加）  
日期：2026-08-12  
状态：协议冻结、代码增量已实现并完成本地隔离专项验证  
上游：Phase 3C Task Runtime、Phase 3D Run Lifecycle、Phase 3E/3F Tool Control Plane

## 1. 目标与边界

Phase 3G 消费全 DAG 完成后唯一的 `bid.run.validation_requested.v1`，以数据库权威对象执行运行级完整性校验，并把 Run/Assessment 原子收敛到成功、失败或输入失效终态。本阶段不调用模型、OCR/视觉、公网、外部工具或真实对象存储，也不重新判断投标内容质量；Claim/Evidence 与报告一致性等内容质量仍由 DAG 内标准 TaskContract 负责。

## 2. 权威对象与状态机

线性 revision `20260812_0097` 新增：

- `bid_run_validations`：每个 Run 唯一验证意图，绑定 source event、committed Plan、validator version、确定性 input hash、最终 result JSON/hash 和失败分类。
- `bid_run_validation_attempts`：每次领取的不可变 Lease/Fence 轨迹，`attempt_no/fencing_token` 单调递增，旧 Worker 不能提交新 Fence。

Validation 状态：`requested -> leased -> running -> passed|failed|stale`；取消或终态围栏进入 `cancelled`。租约到期只把同一 Validation 恢复为 `requested`，不猜测结果，也不创建第二条 Run Validation。

## 3. 确定性完整性规则

Validator 固定检查：

1. Assessment 仍 active，Run 仍为 active pointer，Manifest 与最新 Scope 未变化，且未请求取消；这些失败归类为 `stale`。
2. 唯一 committed Plan 存在并与 Validation 绑定，Plan validated hash 非空。
3. Task 集非空、task key 唯一、task type 属于 49 项注册表、Plan proposal 与 Task 集完全覆盖。
4. 全部 Task 为 `succeeded|skipped`，Dependency 两端存在且父任务成功。
5. 每个 succeeded Task 的 current Attempt 为 succeeded，最终 Checkpoint fence 与 Attempt 一致，next state 合法。
6. 不存在未结 AsyncOperation、Tool Invocation 或 Tool Dispatch；Tool Result 不得失去 Invocation 血缘。
7. 执行前重建 validation input hash，任何权威行漂移 fail closed 为不可重试完整性失败。

Phase 3 总收口 v0.1-r25 将 Validator 版本升级为 `bid-run-integrity-validator-v2`：materialization input 进一步绑定全部 Task Attempt/Checkpoint、Context Manifest、Tool Invocation、AsyncOperation、Dispatch/DispatchAttempt 和 ToolResult 的稳定标识、状态、Hash 与 Fence；终态检查逐行验证 Context payload hash、Invocation request/arguments hash、Dispatch envelope/scope hash、Attempt 单调代际、Result immutable hash、Operation result ref 和 Checkpoint ToolResult 引用。该增量的综合验证状态以 `bid-assessment-runtime-brain-phase3-closeout-protocol-20260812.md` 为准。

规则结果按固定顺序写入不可变 Result JSON，并以 canonical SHA-256 固化。代码异常或事务回滚不产生失败结果，由同一 Validation Lease 恢复重试。

## 4. Run/Assessment 收敛

- `passed`：Run `validating -> succeeded`，`retryable=false`，`finished_at` 固化；当前 active Run 对应的 Assessment 按 run kind 收敛为 `preliminary_ready` 或 `deep_ready`；写 `bid.run.succeeded.v1`、审计和 result hash。
- `failed`：Run `validating -> failed`，当前规则失败默认不可重试，Assessment 仅在该 Run 仍为 active pointer 时转为 `failed`；写 `bid.run.failed.v1`。
- `stale`：Run `validating -> stale`，仅当仍为 active pointer 时把 Assessment 转为 `stale_input`；若新 Run 已接管 active pointer，绝不覆盖新 Run 的 Assessment 状态；写新增 `bid.run.stale.v1`。

Run、Assessment、Validation、ValidationAttempt、Outbox 与 Audit 必须在同一事务内提交。取消维护同步围栏未完成 Validation/Attempt；晚到旧 Fence fail closed。

## 5. 开关、调度与迁移门禁

独立开关 `FEATURE_BID_ASSESSMENT_PHASE3_RUN_VALIDATION=false` 默认关闭，启用时强制依赖 Phase 3C Task Runtime 和 Phase 3D Run Lifecycle。队列处理周期 5 秒，恢复扫描周期 30 秒；事件实时消费与维护扫描使用同一 processed marker 幂等边界。

`0097` 同时把 `bid.run.stale.v1` 加入数据库 Outbox CHECK。降级前两张验证表和 stale event 必须为空；否则拒绝降级。目标 ECS 最近只读 head 仍为 `20260808_0082`，不得应用 `0097` 或连接外部环境。

## 6. 验证门禁

已添加并获授权运行 Phase 3G 合同、0097 拓扑、事件幂等、Validation Lease/Heartbeat/Fence、成功/失败/stale 收敛、事务回滚、验证期取消与过期租约恢复，以及 Phase 3C—3F/API-41/SSE 相邻回归测试。

本地隔离验证结果为 `158 passed / 0 failed`：Phase 3G 合同、迁移和核心链 `128 passed`，Phase 3C—3F/API-41/SSE 精确相邻回归 `30 passed`。验证不包含真实样例、OCR/视觉解析、模型、真实外部工具或真实对象存储调用，未连接外部环境。
