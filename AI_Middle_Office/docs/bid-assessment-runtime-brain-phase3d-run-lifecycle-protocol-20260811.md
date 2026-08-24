# 报价资料研判 Agent Phase 3D：API-42/API-43 与 Run 生命周期收口协议

版本：`v0.1`  
日期：`2026-08-11`  
对应主规范：`v0.1-r21`  
实现边界：Run 取消请求、异步取消收敛、检查点重试、Attempt/Fencing 续代、API-41/SSE 投影  
验证状态：代码已实现并通过授权范围内本地隔离专项验证

## 1. 目标与非目标

Phase 3D 补齐 Phase 3A—3C 控制面的负责人运行操作：API-42 持久化取消意图并由维护任务完成硬围栏；API-43 只在原 failed/retryable Run 下，从最近 Checkpoint 创建递增 Attempt 并恢复到 queued。本阶段不执行模型、OCR、视觉、文档解析、工具调用、事实落库、问题发布、报告生成或对象存储读写。

继续禁止连接、迁移、备份、重启或改动 ECS/CentOS/真实 MinIO/Redis，也不修改旧 `bid_intake_*`。

## 2. 开关与隔离

```text
FEATURE_BID_ASSESSMENT_PHASE3_RUN_LIFECYCLE=false
```

API-42/API-43 只有在总运行时、Phase 3A Run Bootstrap 和本开关同时启用时才可见。该开关不隐式开启 Planner、Task Runtime、模型或工具执行器；正式启用时必须与兼容版本的 API/Worker 同步发布。

## 3. API-42 取消协议

`POST /api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel`

- 必需 Headers：`Idempotency-Key`、API-41 返回的单个私有强 `If-Match`；
- Body：`reason`，trim 后 1—1000 字符；
- 未授权 Assessment/Run 统一返回 404；
- 可取消状态为 created/planning/queued/running/waiting_input/waiting_operation/validating，以及 `failed,retryable=true`；
- succeeded/stale、`failed,retryable=false` 和非 active Run 返回 `BID_RUN_NOT_CANCELLABLE`；
- 已 cancelled 且使用当前 ETag 时返回当前快照；相同幂等请求永远精确重放首次响应。

取消请求事务只做以下动作：

1. 锁 Assessment 和 Run，复核 owner/admin ACL 与强 ETag；
2. 写 `cancel_requested_at`，把可见阶段置为 `cancelling`，递增 Run row version；
3. 同事务写 `bid.run.cancel_requested.v1`、用户审计和完成的幂等响应；
4. 返回 HTTP 202，不声称 Worker 已经停止。

取消请求落库后，Phase 3C 的新 Lease、Heartbeat、Checkpoint、完成和失败写入均因 `cancel_requested_at` 或旧 fencing 被拒绝。维护任务每 30 秒扫描取消请求，在一个事务内把所有非终态 Task、活跃 Attempt 和活跃 AsyncOperation 转为 cancelled，再把 Run 和 Assessment business status 转为 cancelled，写 `bid.run.cancelled.v1` 与服务审计。旧 Worker 的迟到结果永不重新激活。

## 4. API-43 检查点重试协议

`POST /api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry`

- 必需 Headers：`Idempotency-Key`、API-41 私有强 `If-Match`；
- Body 固定 `retry_mode=from_latest_checkpoint`，`note` 可空且不超过 1000 字符；
- 只允许当前 active Run 为 `failed,retryable=true` 且尚未请求取消；
- 当前 Manifest、Scope、Assessment lifecycle/business 状态或 active Run 指针失效时返回 `BID_RUN_INPUT_STALE`；
- 其他状态返回 `BID_RUN_NOT_RETRYABLE`；
- 不创建新 Run，不修改 run_sequence/input_hash/冻结版本，不复活旧 Attempt。

成功事务：

1. 锁 Assessment、Run、Task 和相关 Attempt/AsyncOperation；
2. 把仍活跃的旧 Attempt/Operation 置 cancelled，形成硬 fence；
3. 对失败或中断且父依赖仍满足的 Task 创建 `status=created`、attempt_no/fencing_token 单调递增的新 Attempt；
4. Task 回到 ready，新 Attempt 成为 current attempt；
5. Run `failed -> queued`，清除 retryable/waiting/finished 标记；
6. 写 `bid.run.retry_requested.v1`、用户审计和幂等响应。

下一次 `lease_next_ready_task` 必须复用 API-43 已创建的 Attempt，不得再多创建一代。Lease 返回最近一次历史 Checkpoint 的 ID、来源 Attempt、action sequence、state hash 和候选输出引用；没有历史 Checkpoint 时返回 null，从 TaskContract 起点开始。Checkpoint 本身仍不可变，旧 Attempt 永不复活。

## 5. ETag、ACL 与幂等

- ETag 继续覆盖完整 actor-visible RunProgressSnapshot，包括 row version、最新公共事件、allowed actions 和 `cancel_requested_at`；
- 版本比较在业务状态判断前执行，旧 ETag 返回 412 和当前 ETag；
- 幂等 scope 分别为 API-42/API-43 的 method + route template + actor；
- 同 key/同 hash 精确重放；同 key/异 hash 返回 `BID_IDEMPOTENCY_KEY_REUSED`；
- 业务写、Outbox、审计和幂等完成记录必须同事务提交，任一步失败全部回滚。

## 6. 事件与公共投影

- `bid.run.cancel_requested.v1`：内部保留 reason/timestamp，公共只投影脱敏 `run.stage.changed`；
- `bid.run.cancelled.v1`：公共投影 `run.status.changed`；
- `bid.run.retry_requested.v1`：内部保留新 Attempt 与恢复点引用，公共只投影 `failed -> queued` 的 `run.status.changed`；
- 公共事件不得包含 Task/Attempt ID、fencing、Worker、Checkpoint 内容、Prompt、工具参数或内部结果。

## 7. 迁移门禁

Phase 3D 复用 `0085/0086` 已有 `cancel_requested_at`、Task/Attempt/Checkpoint/AsyncOperation、Outbox、Public Event、幂等和审计结构，不新增表或字段。由于 `bid_outbox_events.event_type` 受数据库 CHECK 约束，新增线性 Alembic revision `20260811_0094`，只把 `bid.run.retry_requested.v1` 加入允许集合；代码唯一 head 为 `20260811_0094`。0094 降级要求在线检查且不存在已持久化的 retry-requested 事件，否则拒绝降级。

## 8. 验证门禁

专项测试必须覆盖：

- API-42/API-43 请求合同、404 ACL、强 ETag/412、幂等重放与 key 冲突；
- 取消请求、旧 Worker fence、维护收敛、Task/Attempt/Operation 全取消；
- failed/retryable 门、输入 stale 门、递增 Attempt/fencing、Lease 复用与 resume Checkpoint；
- Outbox/审计/幂等事务回滚；
- API-41 allowed actions/ETag 与 SSE 脱敏相邻回归；
- 周期任务 fail-closed 和迁移唯一 head `20260811_0094`。

上述属于报价资料研判 Agent 测试。用户已明确授权本节范围；本地隔离验证共 `137 passed`：机器合同与迁移拓扑 `115`、API-42/API-43 及 Phase 3C/API-41 相邻链 `10`、事务/幂等/Outbox/SSE/周期维护运行服务 `12`。另完成 Phase 3D 相关 Python `py_compile`、17 个合同/Schema/OpenAPI JSON 解析及 Draft 2020-12 Schema 自检、核心与 Celery 模块导入和 `git diff --check`。测试仅使用合成结构化数据与临时 SQLite，未运行真实样例、OCR/视觉解析、模型调用或外部服务。
