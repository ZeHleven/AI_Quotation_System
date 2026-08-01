---
title: 练习 07：异步任务状态机、取消、超时、租约、Checkpoint 与恢复实战
category: 后端基础设施练习记录
tags:
  - Async Job
  - State Machine
  - Cancellation
  - Lease
  - Checkpoint
  - Idempotency
  - Recovery
  - AI 报价中台
practiced_at: 2026-07-30
status: 已完成
---

# 练习 07：异步任务状态机、取消、超时、租约、Checkpoint 与恢复实战

## 实验结论

本次用线程安全的内存任务仓库和虚拟时钟完成 31 项断言：

- 同一任务被 8 个 Worker 同时领取，只有 1 个成功；
- 重复投递没有增加执行次数；
- 取消请求幂等，Worker 在安全点确认取消；
- 取消请求发出后，成功结果不能再覆盖任务状态；
- Worker A 的租约过期后，Worker B 从 Checkpoint 接管；
- Worker A 的旧租约令牌不能迟到写回；
- 已完成的 `parse` 步骤不重复执行；
- 外部推送尝试 2 次，幂等账本保证业务效果只有 1 次；
- 任务 Deadline 到期后进入 `timed_out` 并释放租约；
- 超时 Worker 的迟到成功不能覆盖 `timed_out`；
- 重试创建关联的新任务，原失败尝试保持不变；
- 恢复达到最大次数后进入 `failed`，不会无限接管；
- 状态、Checkpoint、恢复和终态事件均可审计。

核心记忆：

```text
队列负责“通知执行”
数据库负责“任务事实”
状态机负责“能否迁移”
租约负责“谁有权写”
心跳负责“近期是否活动”
Checkpoint 负责“从哪里继续”
幂等键负责“重复执行不重复产生副作用”
事件流负责“发生过什么”
```

---

## 1. 安全边界

| 项目 | 本次做法 |
|---|---|
| 数据库 | 线程安全内存仓库 |
| 网络端口 | 未开放 |
| Redis / Celery | 未连接 |
| MySQL | 未连接 |
| 模型 / RAG / N8N | 未调用 |
| 时间 | 虚拟时钟 |
| 持久化文件 | 未创建 |
| 当前业务任务 | 未调用、未修改 |

本练习验证控制逻辑，不代表现有运行环境已经通过故障演练。

---

## 2. 状态机与执行阶段

基础状态：

```text
queued → running → succeeded
                ↘ failed

queued/running → cancel_requested → cancelled
queued/running → timed_out
```

终态：

```text
succeeded / failed / cancelled / timed_out
```

终态不可被迟到 Worker 改回 `running` 或 `succeeded`。

状态和阶段必须分开：

```text
status = running
stage  = parsing / retrieving / pushing / building_preview
```

`status` 用于控制状态迁移，`stage` 用于展示执行位置。不要为了每个进度步骤增加一个新状态。

---

## 3. 重复投递与原子领取

消息队列通常是至少一次投递。可能出现：

- Worker 执行成功但 ACK 丢失；
- Worker 崩溃后消息重新可见；
- Broker 网络抖动导致重复投递；
- API 或 Dispatcher 重复发送任务。

因此，收到消息不等于拥有执行权。

数据库领取的核心语义：

```sql
UPDATE jobs
SET status = 'running',
    worker_id = :worker_id,
    lease_token = :token,
    attempt_count = attempt_count + 1
WHERE job_id = :job_id
  AND status = 'queued';
```

只有影响 1 行的 Worker 获得执行权。

本次实验：

```text
竞争 Worker = 8
成功领取 = 1
attempt_count = 1
job_claimed 事件 = 1
```

真实 MySQL 还可以使用：

```text
SELECT ... FOR UPDATE SKIP LOCKED
```

领取待执行任务，避免多个 Worker 互相等待。

---

## 4. 租约与 Fencing Token

租约字段：

```text
worker_id
lease_token
lease_expires_at
heartbeat_at
attempt_count
max_attempts
```

Worker 每次写进度、Checkpoint 或终态都必须携带当前 `lease_token`：

```sql
UPDATE jobs
SET ...
WHERE job_id = :job_id
  AND status = 'running'
  AND lease_token = :lease_token;
```

### 为什么只记录 worker_id 不够

Worker A 卡顿后租约过期，Worker B 接管。此时 A 可能突然恢复并写入旧结果。

```text
A 获得 token-A
→ A 卡住
→ token-A 租约过期
→ B 获得 token-B
→ A 恢复并迟到写回
```

服务端必须拒绝 `token-A`。

本次验证：

```text
Worker B 接管成功
attempt_count = 2
Worker A 旧 Token 写 Checkpoint = 被拒绝
```

租约令牌就是 Fencing Token：它不是只判断 Worker 是否在线，而是判断它是否仍拥有写权限。

---

## 5. 心跳、租约和停滞

三者回答不同问题：

| 信号 | 回答的问题 |
|---|---|
| Worker 心跳 | 这个 Worker 进程近期是否在线 |
| 任务心跳 | 该任务近期是否有活动 |
| 租约 | 当前 Worker 是否仍拥有任务 |
| 进度事件 | 任务是否真正向前推进 |

只更新时间戳可能掩盖死循环：

```text
Worker 持续心跳
但 completed_items 一直不变
```

因此停滞检测应结合：

- `last_heartbeat_at`；
- `last_progress_at`；
- 阶段停留时间；
- 已完成数量；
- 外部调用延迟；
- 任务总 Deadline。

---

## 6. 协作式取消

推荐协议：

```text
用户请求取消
→ status = cancel_requested
→ Worker 在安全点读取取消状态
→ 停止后续工作
→ status = cancelled
→ 释放租约
```

安全点包括：

- 每个批次开始前；
- 外部调用前后；
- 写最终结果前；
- 长循环固定间隔。

本次实验验证：

```text
第一次取消：cancel_requested，replayed=false
第二次取消：cancel_requested，replayed=true
取消后尝试 complete：被状态机拒绝
Worker 安全点确认：cancelled
租约被释放
```

取消不是自动回滚：

- 已发送的通知可能无法撤回；
- 已扣减的外部额度可能已发生；
- 已写入第三方系统的数据可能需要补偿；
- 事件中必须记录已完成阶段和操作者。

---

## 7. 三类超时

| 类型 | 作用 |
|---|---|
| 单次请求 Timeout | 限制一次 HTTP、模型或数据库等待 |
| 租约过期 | 当前 Worker 失去所有权，可由新 Worker 接管 |
| 任务 Deadline | 整个业务任务到期，进入终态 |

租约过期不等于任务失败：

```text
租约过期 + 仍有重试预算
→ 新 Worker 接管
→ 从 Checkpoint 恢复
```

任务 Deadline 到期：

```text
running → timed_out
→ 清理租约
→ 旧 Worker 迟到结果被拒绝
```

本次验证：

```text
超时扫描命中 1 个任务
原任务 = timed_out
lease_token = null
旧 Worker complete = 被拒绝
```

Celery `task_time_limit` 只能终止执行进程，不自动完成业务状态、租约和审计收口。

---

## 8. Checkpoint 恢复

Checkpoint 至少保存：

```text
input_version
completed_steps
结构化中间结果
外部结果引用
模型 / Prompt / 检索版本
保存时间
```

本次流程：

```text
Worker A 完成 parse
→ 保存 Checkpoint: [parse]
→ 完成外部 push
→ 保存 push Checkpoint 前崩溃
→ 租约过期
→ Worker B 接管
→ 跳过 parse
→ 再次尝试 push
→ 完成 build_preview
→ succeeded
```

恢复时剩余步骤：

```text
push
build_preview
```

最终 Checkpoint：

```text
parse
push
build_preview
```

输入版本不一致时禁止复用 Checkpoint，否则可能把旧文件、旧 Prompt 或旧规则的中间结果混入新任务。本次也验证了版本不匹配会被拒绝。

---

## 9. 崩溃窗口与副作用幂等

最危险的窗口：

```text
外部推送已成功
→ Worker 在保存 Checkpoint 前崩溃
→ 新 Worker 无法知道是否成功
→ 必须再次尝试
```

不能依靠“只调用一次”解决，因为分布式系统无法可靠判断第一次调用的结果。

应使用稳定幂等键：

```text
operation + business_object + input_version
```

例如：

```text
budget-push:{job_id}:{confirmed_quote_version}
```

本次结果：

```text
外部调用尝试 = 2
实际业务效果 = 1
第二次返回首次 receipt
```

准确表述是：

> 投递和执行仍是至少一次，业务副作用通过幂等账本达到“有效一次”。

不要轻易声称消息队列实现了端到端 Exactly Once。

---

## 10. 恢复、内部重试和新任务重试

| 方式 | 适用场景 | 审计方式 |
|---|---|---|
| 同一次调用内部重试 | 短暂网络错误 | 记录 attempt |
| 租约接管 | Worker 崩溃或失联 | 同一任务增加 attempt |
| 从 Checkpoint 恢复 | 长任务部分完成 | 保留步骤和输入版本 |
| 新建 retry job | 原任务已失败、取消或超时 | `retry_of` 关联原任务 |

本次超时重试：

```text
原任务 = timed_out
新任务 = queued
新任务.retry_of = 原任务 ID
```

原终态没有被覆盖，方便比较每次尝试。

恢复也必须有上限。本次设置 `max_attempts=2`：

```text
第 1 个 Worker 租约过期
→ 第 2 个 Worker 接管
→ 再次过期
→ 第 3 个 Worker不能继续接管
→ failed
```

---

## 11. 事件流与可观测性

恢复任务的事件顺序：

```text
job_created
job_claimed
checkpoint_saved
lease_recovered
checkpoint_saved
checkpoint_saved
job_succeeded
```

事件记录应包含：

```text
sequence
event_type
from_status / to_status
stage
worker_id
attempt_count
checkpoint_id
trace_id
occurred_at
安全的错误摘要
```

重点指标：

- queued 任务数量和最老年龄；
- running 数量；
- 各阶段 P50/P95/P99；
- 任务心跳停滞数量；
- 租约过期和接管次数；
- 每任务平均 attempt；
- 取消响应时间；
- 超时率和恢复成功率；
- 幂等重放次数；
- 迟到写回拒绝次数；
- Checkpoint 大小和保存耗时。

---

## 12. 与报价中台真实代码映射

### 普通报价任务

| 能力 | 代码位置 |
|---|---|
| 状态、阶段、事件、结果 | `app/models/quote_job.py` |
| 创建、取消、重试、超时扫描 | `app/api/v1/quote_jobs.py` |
| 执行、心跳、分批补报 | `app/services/quote_job_runner.py` |
| Local / Celery 分发 | `app/services/quote_dispatcher.py` |
| Late ACK、时间上限、低预取 | `app/tasks/celery_app.py` |

当前普通报价任务：

- API 取消会撤销 Celery 任务并直接写 `canceled`；
- Runner 在处理事件时刷新任务，发现终态就停止；
- 超时任务可由管理员扫描标记；
- 重试会创建新的报价任务；
- 大清单按批次执行并有限补报。

需要如实说明：普通 `quote_jobs` 路径没有本练习完整的租约令牌和 Checkpoint 接管模型，领取任务的统一原子性仍需结合真实部署路径评估。

### 报价资料研判 Agent

`app/services/bid_intake_runtime.py` 已具备更完整的恢复模型：

- `SELECT ... FOR UPDATE SKIP LOCKED` 领取；
- `lease_token` 与 `lease_expires_at`；
- 过期租约由新 Worker 接管；
- `attempt_count / max_attempts`；
- SQL Checkpoint；
- 有 Checkpoint 时从最近状态恢复；
- 取消时清空租约，阻止旧 Worker 写运行轨迹和图快照；
- 领取、恢复、失败、取消均写事件。

这正是本次练习的项目级映射。

### 可继续评审

- 普通报价任务是否需要统一租约和 Fencing Token；
- 创建数据库任务与发送 Celery 之间的双写窗口是否需要 Outbox；
- N8N 下发是否有稳定的业务幂等键和结果查询；
- Checkpoint 的输入版本、Prompt 版本和证据版本是否完整；
- 超时扫描是否自动化并配套告警；
- 取消与外部副作用之间是否需要补偿状态；
- 队列积压是否按最老任务年龄触发背压。

这些是评审清单，不是未经验证就修改现有系统的结论。

---

## 13. 面试回答

### Worker 崩溃后怎样恢复？

> 队列采用至少一次投递，业务库通过条件更新或 `FOR UPDATE SKIP LOCKED` 决定唯一执行者。Worker 获得带有效期的租约，每次写回必须携带租约 Token。租约过期后，新 Worker 在最大尝试次数内接管并从持久化 Checkpoint 恢复；旧 Worker 的迟到写回因 Token 不匹配被拒绝。外部副作用使用稳定幂等键。

### 怎样处理任务重复执行？

> 我不依赖队列提供端到端 Exactly Once。任务领取使用数据库条件更新，步骤完成写 Checkpoint，外部写操作使用业务幂等键和唯一约束。实验中推送尝试了两次，但幂等账本只产生一次业务效果。

### 心跳和租约有什么区别？

> 心跳表示近期有活动，租约表示当前 Worker 是否仍拥有写权限。Worker 在线不代表仍拥有任务；租约未过期也不代表任务在向前推进。因此要同时观察租约、心跳、阶段停留时间和进度事件。

### 如何实现取消？

> 取消是协作式协议。API 把任务置为 `cancel_requested`，Worker 在批次、外部调用和最终写入前检查，在安全点停止并转为 `cancelled`。取消请求本身幂等；如果外部副作用已经发生，就保留步骤记录并按业务设计补偿，不能假装状态修改已经回滚第三方系统。

### 租约过期和任务超时有什么区别？

> 租约过期表示当前 Worker 失去所有权，任务在重试预算内仍可接管；任务 Deadline 表示业务允许的总时间已耗尽，应进入 `timed_out` 终态。Celery 时间上限只是执行层保护，业务库仍需完成状态、租约和事件收口。

---

## 14. 练习脚本

文件：

```text
AI_Middle_Office/scripts/async_job_recovery_practice.py
```

执行：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m scripts.async_job_recovery_practice
```

成功标志：

```text
"passed": 31
"total": 31
"all_assertions_passed": true
```

---

## 15. 本次没有证明什么

- 没有连接真实 MySQL、Redis 或 Celery；
- 没有真实终止 Worker 进程；
- 没有注入网络分区和数据库主从切换；
- 没有验证当前报价任务的并发领取安全性；
- 没有调用真实 N8N 下发接口；
- 没有验证 LangGraph Checkpoint 的内容和体积；
- 没有做积压和吞吐压测；
- 没有形成生产故障恢复 Runbook。

---

## 16. 复习卡片

```text
1. 消息可能重复，业务任务必须有唯一执行权
2. 状态管迁移，阶段管进度，事件管审计
3. 租约过期允许接管，旧 Token 禁止迟到写回
4. 心跳不等于进度，也不等于所有权
5. 取消在安全点生效，已发生的副作用要补偿
6. Checkpoint 必须绑定不可变输入和版本
7. 至少一次执行 + 幂等账本 = 有效一次业务效果
8. 租约过期可恢复，任务 Deadline 到期是终态
9. 失败重试保留原尝试，新任务用 retry_of 关联
10. 恢复必须有 max_attempts，不能无限循环
```

下一步适合练习高并发压测、容量估算与性能瓶颈定位。
