# 报价资料研判 Agent Phase 3C：Task Runtime Control Plane 协议

版本：`v0.1`  
日期：`2026-08-11`  
对应主规范：`v0.1-r19`  
实现边界：TaskContract、Attempt Lease、Heartbeat/Fencing、Checkpoint、失败恢复、依赖释放  
验证状态：本地隔离专项验证通过（`123 passed`）

## 1. 目标与非目标

Phase 3C 把 Phase 3B 已提交的 Task DAG 转换为可安全执行、可中断恢复且可审计的运行控制面。本阶段只负责控制，不执行模型、OCR、视觉解析、文档正文解析、工具调用或真实对象存储读写。

本阶段明确禁止：

- Worker 自行修改 TaskContract、DAG、Scope 或 Run 冻结版本；
- 从文件名、MIME、`parser_hint` 或未治理输入推断任务范围；
- API/Outbox fan-out 线程直接执行模型或工具；
- 旧 Attempt 使用过期租约或 fencing token 写状态、Checkpoint 或结果；
- Checkpoint 保存完整文档、大工具结果、Prompt、隐藏思维链或密钥；
- 修改旧 `bid_intake_*` 运行时；
- 连接、迁移、备份、重启或改动 ECS/CentOS/真实 MinIO/Redis。

## 2. 开关与部署隔离

独立默认关闭开关：

```text
FEATURE_BID_ASSESSMENT_PHASE3_TASK_RUNTIME=false
BID_TASK_RUNTIME_LEASE_SECONDS=180
BID_TASK_RUNTIME_MAX_ATTEMPTS=3
```

该开关只允许租约维护入口运行，不隐式启用总运行时、Phase 3A、Phase 3B、模型或工具执行器。维护任务只回收旧租约和清理终态 Run 的活跃 Attempt，不主动领取 ready Task。

## 3. TaskContract 权威来源

领取任务前，控制面必须从以下持久化权威重新构造 TaskContract：

1. 当前 `BidTask`；
2. 同 Run 的唯一 committed `BidPlanRevision` 及完整 Plan envelope；
3. envelope 中通过校验的 TaskDefinition、PlannerInput 和 frozen `bound_versions`；
4. Run 的 `input_hash`、Scope、Manifest 和九类冻结版本；
5. 本代码版本内不可变的 49 项标准任务注册表；
6. 已提交 `BidTaskDependency` 实际边。

下列任一不一致必须 fail closed，禁止创建 Attempt：Plan 未 committed、validated hash 不一致、TaskDefinition 不唯一、Run input hash 漂移、Scope/Manifest 绑定漂移、Task 行与注册表 profile 不一致、Task input hash 不一致、依赖边不一致、预算 profile 未注册。

TaskContract 固定包含：Task 身份、目标、Scope/lot、依赖、全部 bound versions、必需事实槽、允许工具、Context profile、预算、完成合同、停止条件、失败策略和输出版本。合同规范 JSON 的 SHA-256 作为 `task_contract_hash` 写入租约事件和审计。Worker 只能按合同缩小执行，不能扩大目标、工具、预算或作用域。

冻结预算：

| profile | max iterations | max tool calls | max input tokens | max output tokens |
|---|---:|---:|---:|---:|
| `LOW_V1` | 3 | 4 | 8,000 | 2,000 |
| `STANDARD_V1` | 6 | 8 | 16,000 | 4,000 |
| `HIGH_V1` | 8 | 12 | 24,000 | 6,000 |

停止条件固定为：完成合同满足、任务预算耗尽、连续两次动作没有新增治理信息、Run 输入或 fencing 失效。当前标准任务失败策略固定为 `retry_then_fail`；执行器不得自行增加 Attempt。

## 4. Lease、Attempt 与 fencing

### 4.1 领取

`lease_next_ready_task` 只选择：

- `Task.status=ready`；
- Run 为 `queued|running` 且没有取消请求；
- Run 仍是 active Assessment 的 `active_run_id`；
- Assessment 仍为 active；
- Task type 属于 Worker 显式能力集合（若提供）。

MySQL 使用行锁与 `SKIP LOCKED`；排序固定为 `priority, created_at, task_id`。同一事务内：

1. 重构并校验 TaskContract；
2. `attempt_no=max+1`、`fencing_token=max+1`；
3. 创建 `BidTaskAttempt(status=leased)`；
4. Task `ready -> leased` 并绑定 `current_attempt_id`；
5. 首个租约令 Run `queued -> running`；
6. 写 `bid.task.leased.v1` 和 `task.attempt.lease` 审计。

事务失败时上述写入全部回滚。租约事件仅供内部消费，不投影 Worker 身份、Attempt 或 fencing 给公共 SSE。

### 4.2 启动与心跳

启动是幂等的 `leased -> running`；重复同一 claim 返回原开始时间。心跳默认每 30 秒调用，把租约续到数据库当前 UTC 时间之后 180 秒。

每次启动、心跳、Checkpoint、完成或失败必须同时校验：

- `task.current_attempt_id == attempt_id`；
- `lease_owner == worker_id`；
- `fencing_token` 相等；
- Attempt 处于允许的活跃状态；
- 租约尚未过期；
- Run 仍可执行且没有取消请求。

任一失败返回稳定的 `BID_TASK_FENCE_LOST` 或 `BID_TASK_LEASE_EXPIRED`，且不得产生任何业务写入。

## 5. Checkpoint 协议

Checkpoint 是不可变动作恢复点。首个 `action_seq=0`，后续必须严格连续；同一 action sequence 与完全相同内容可安全重放，不同内容返回 `BID_CHECKPOINT_CONFLICT`。

每个 Checkpoint 绑定：Attempt、当前 fencing token、Run Manifest、规范化 state 及 state hash、工具结果引用、预算消耗、候选输出引用和 next state。单个规范 JSON 字段上限 64 KiB；大结果只保留受控 `result_ref`。写入成功同步更新 Run `last_checkpoint_at/row_version` 并写只含哈希和身份的审计。

Checkpoint 不代表业务结果已生效。任务完成必须另行提供 `bid-task-output-validator-v1` 确定性输出校验器签发的 `TaskCompletionReceipt`，其中固定携带 `checkpoint_id/state_hash/output_hash/completion_contract/validator_version/output_ref`，且只能引用该 Attempt 的最新 Checkpoint；完成合同、校验器版本、输出引用或 hash 任一不一致均拒绝落地。

## 6. 完成、依赖释放与验证门

完成事务必须校验最新 Checkpoint、state hash、output hash、可选 output ref 和当前 fencing，然后原子完成：

1. Attempt `running|validating -> succeeded`；
2. Task `running|validating -> succeeded`；
3. 写 `bid.task.succeeded.v1` 和审计；
4. 对所有下游 Task 重查全部父依赖；只有父任务全部为 `succeeded|skipped` 才将 `blocked -> ready`；
5. 每个新 ready Task 分别写 `bid.task.ready.v1`；
6. 全部 Task 成功/跳过后，Run `running -> validating` 并写内部 `bid.run.validation_requested.v1`。

Phase 3C 不把 Run 直接标记 succeeded，不发布报告；输出验证、事实落库、问题轮次、最终 Run 完成属于后续阶段。

公共 SSE 继续只接收脱敏的 `run.stage.changed`，不包含 Task ID、DAG、Attempt、Worker、fencing、Prompt、工具参数或内部结果引用。每个公共 Task 事件先递增 Run row version，避免同 Run projection key 冲突并保证 API-41 ETag 变化。

## 7. 失败与恢复

Worker 主动失败时，Attempt 保存稳定错误码和受控 detail ref。若 `retryable=true` 且未达到自动重试上限，Task 经 `failed -> ready` 获得创建新 Attempt 的资格；旧 Attempt 永不复活。达到上限时 Run 转 `failed`，并根据错误是否可恢复设置 `retryable`，等待后续 API-43 显式恢复。

Maintenance 每 30 秒扫描：

- 活跃 Attempt 的 `lease_until <= database UTC now`；
- 或 Attempt 所属 Run 已为 `stale|cancelled|succeeded`。

租约过期会把旧 Attempt 置为 `lease_expired` 并记录 `lease_reclaimed_at`。自动重试时 Task 回到 ready，下一次领取必须创建递增 Attempt/fencing；连续过期达到上限时 Run 进入 `failed,retryable=true`，不再自动领取。Run 已 stale 时 Attempt/Task 转 stale 并写 `bid.task.stale.v1`；旧 Worker 的任何迟到写入继续被 fencing 拒绝。

Maintenance 逐 Attempt 使用独立事务；单行失败不回滚其他恢复结果。扫描本身不执行 Task，不调用模型/工具，不读取对象正文。

## 8. 事件边界

内部事件：

- `bid.task.leased.v1`：Attempt、租约、fencing 和 TaskContract hash；
- `bid.task.succeeded.v1`：最新 Checkpoint 和结果 hash；
- `bid.task.failed.v1`：稳定错误、retryable、是否已自动排队；
- `bid.task.stale.v1`：Run 输入失效产生的硬 fence；
- `bid.run.validation_requested.v1`：DAG 全部成功后进入后续验证器。

`bid.task.ready/succeeded/failed/stale.v1` 只把既有脱敏进度字段投影为公共 `run.stage.changed`；`bid.task.leased.v1` 和 `bid.run.validation_requested.v1` 不做公共投影。

## 9. API 边界

Phase 3C 不新增外部 API。API-41 继续只返回 actor 可见的 Run 状态、阶段、计数、等待原因和时间，不暴露内部 Task DAG 或控制面凭据。

API-42 取消和 API-43 从检查点重试仍保持主规范边界，待其独立协议冻结后实现。Phase 3C 已保证 `cancel_requested_at` 会阻止新租约，Run stale/terminal 会使旧 claim 失去写权限，但本阶段不伪造外部取消或人工重试入口。

## 10. 迁移门禁

Phase 3C 复用 `0085/0086` 已有结构：

- `bid_tasks`；
- `bid_task_attempts`；
- `bid_checkpoints`；
- `bid_task_dependencies`；
- `bid_analysis_runs`；
- Outbox、审计与公共事件表。

现有字段、FK、`(task_id,attempt_no)`、`(task_id,fencing_token)`、`(attempt_id,action_seq)` 唯一约束及租约索引足以实现本阶段，因此不新增 Alembic revision，代码唯一 head 保持 `20260811_0093`。目标 ECS 最近只读基线仍为 `20260808_0082`，本阶段不得应用到外部环境。

## 11. 验证门

专项测试必须覆盖：

- TaskContract Schema、冻结版本、注册表与 input hash 防漂移；
- 并发领取唯一性、Attempt/fencing 单调递增、启动与心跳；
- 旧 token、过期租约、终态 Run、取消请求拒写；
- Checkpoint 连续序号、不可变重放、Manifest 绑定和大小门；
- 完成事务、依赖释放、DAG 全完成后的 validation request；
- retryable/final failure、租约恢复、终态 Run fence；
- Outbox/审计回滚、SSE 脱敏、API-41 ETag 相邻回归；
- 迁移拓扑仍为唯一 head `20260811_0093`。

这些测试属于报价资料研判 Agent 测试。2026-08-11 经用户明确授权后已完成本地隔离验证：机器合同 `66 passed`、Phase 3C 控制面状态机 `4 passed`、周期恢复 `1 passed`、API-41/SSE 相邻回归 `5 passed`、迁移拓扑 `47 passed`，合计 `123 passed`。未连接或改动 ECS/CentOS/真实 MinIO/Redis，未执行模型、OCR、视觉、真实样例或外部 Tool 调用。
