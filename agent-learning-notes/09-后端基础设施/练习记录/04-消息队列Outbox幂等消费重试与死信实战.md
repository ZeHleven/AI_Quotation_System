---
title: 练习 04：消息队列 Outbox、幂等消费、重试与死信实战
category: 后端基础设施练习记录
tags:
  - Celery
  - Redis
  - Message Queue
  - Outbox
  - Idempotent Consumer
  - Retry
  - Dead Letter
  - AI 报价中台
practiced_at: 2026-07-30
status: 已完成
---

# 练习 04：消息队列 Outbox、幂等消费、重试与死信实战

## 实验结论

本次使用 Celery 5.6.3、Redis 7.2.13 和 MySQL 8.0.39，启动一个临时 `solo` Worker，完成三项真实实验：

1. 同一个 Outbox 逻辑事件被发布两次，消费者分别返回 `applied` 和 `duplicate_ignored`，数据库只保存 1 条消费记录和 1 条业务效果；
2. 暂时错误前两次进入重试，第 3 次成功，最终只产生 1 条业务效果；
3. 确定性毒消息前两次重试，第 3 次进入应用级死信记录，没有产生业务效果。

核心结论：

```text
Outbox 解决业务数据与待发送事件的一致性
至少一次场景允许重复到达
消费者幂等保证一次业务效果
暂时错误有限重试
确定性错误进入死信或人工处理
```

本次通过“相同 Outbox 事件重复发布”验证重复消费保护，没有通过杀死 Worker 验证 Broker 的真实崩溃重投，因此不能把它描述成完整的 redelivery 故障实验。

---

## 1. 安全边界

| 项目 | 本次做法 |
|---|---|
| Celery | 5.6.3 |
| Worker | 临时隐藏 `solo` Worker，并发 1 |
| 队列 | 每轮随机名称 `codex_mq_practice_{UUID}` |
| Redis | DB 15 |
| 正式 Broker/Backend | DB 0/1，隔离重跑未连接 |
| MySQL | 5 张 `codex_practice_` 合成表 |
| 业务数据 | 未读取、未修改现有报价和队列表 |
| Redis 清理 | 删除“结束 Key 集合－启动 Key 集合”，不使用 `FLUSHDB` |
| Worker 清理 | 任务完成后终止并等待该确切子进程退出 |

隔离实验结束后：

```text
Redis DB 15 Key = 0
DB 0/1 中本次随机运行标识 = 0
MySQL 剩余练习表 = 0
```

---

## 2. Celery 配置

```text
task_acks_late = true
task_acks_on_failure_or_timeout = true
worker_prefetch_multiplier = 1
visibility_timeout = 10 秒
worker_pool = solo
```

理解：

- late ACK：成功执行后再确认，Worker 崩溃时消息可能重新可见；
- prefetch=1：避免单 Worker 提前占有多个长任务；
- visibility timeout：未确认消息超过时间后可以重新投递；
- 这些配置仍不能保证端到端 exactly-once；
- 业务副作用必须独立做幂等。

本次 Worker 运行日志确认：

```text
transport = redis://.../15
results   = redis://.../15
queue     = codex_mq_practice_{UUID}
```

---

## 3. 数据模型

### Outbox

```text
event_id
event_type
payload_json
status
publish_attempts
created_at
published_at
```

### 已消费事件

```sql
UNIQUE (consumer_name, event_id)
```

### 业务效果

```sql
UNIQUE (event_id)
```

### 尝试记录

```sql
UNIQUE (task_kind, event_id, attempt_no)
```

### 死信记录

```sql
UNIQUE (event_id)
```

每一层唯一约束分别保护：

- 同一消费者不重复处理；
- 同一事件不重复产生业务效果；
- 同一次重试编号不重复记录；
- 同一毒消息不重复进入死信。

---

## 4. 实验一：Outbox 事件重复发布

### 模拟故障

典型窗口：

```text
发布器发送消息成功
→ 发布器在更新 Outbox 状态前崩溃
→ 恢复后再次发送同一个 event_id
```

实验主动把同一逻辑事件发布两次：

```text
event_id = duplicate-{run_id}
publish_count = 2
```

### 消费事务

```text
BEGIN
→ INSERT consumed_events
→ INSERT business_effects
→ COMMIT
```

两张表都对事件建立唯一约束。

### 真实结果

| 投递 | 消费结果 |
|---|---|
| 第一次 | `applied` |
| 第二次 | `duplicate_ignored` |

数据库：

| 记录 | 数量 |
|---|---:|
| Outbox 发布次数 | 2 |
| 已消费记录 | 1 |
| 业务效果记录 | 1 |

结论：

```text
两次投递
≠ 两次业务效果
```

消费者不能只在内存中保存“处理过”，因为重启后会丢失；需要数据库唯一约束或可靠 Inbox。

---

## 5. 实验二：暂时错误有限重试

实验任务设置：

```text
max_retries = 2
```

处理过程：

| 尝试 | 结果 |
|---:|---|
| 1 | `retry_scheduled` |
| 2 | `retry_scheduled` |
| 3 | `succeeded` |

最终：

```text
attempt_no = 3
business_effect_rows = 1
```

### 为什么是三次

`max_retries=2` 表示初始执行失败后最多再重试两次：

```text
初始尝试 1 次 + 重试 2 次 = 总尝试 3 次
```

### 生产策略

真实任务不能固定 0.2 秒无脑重试。需要：

```text
delay = min(cap, base × 2^attempt) + jitter
```

并且只重试：

- 网络瞬断；
- 429；
- 部分 5xx；
- 临时锁冲突；
- 服务短暂不可用。

参数错误、无权限和业务状态冲突通常应快速失败。

---

## 6. 实验三：毒消息进入死信

毒消息代表输入或业务条件决定了它每次都会失败。

实验过程：

| 尝试 | 结果 |
|---:|---|
| 1 | `retry_scheduled` |
| 2 | `retry_scheduled` |
| 3 | `dead_lettered` |

死信记录：

```text
reason = simulated deterministic poison message
attempts = 3
business_effect_rows = 0
```

### 为什么不能无限重试

无限重试会：

- 占用 Worker；
- 堆积队列；
- 重复消耗模型额度；
- 淹没日志；
- 阻塞同类任务；
- 掩盖需要人工修复的数据问题。

### 本次死信的边界

本次使用 MySQL 表实现应用级死信记录，不是：

- RabbitMQ 原生 Dead Letter Exchange；
- Kafka Dead Letter Topic；
- RocketMQ 死信队列；
- Celery/Redis Broker 自动 DLQ。

正式系统还需要：

- 死信查询页面；
- 错误修复；
- 权限控制；
- 受控重放；
- 重放审计；
- 死信数量和年龄告警。

---

## 7. 实验中发现的真实配置问题

第一次运行时，代码虽然在 Celery 构造函数中传入 Redis DB 15，但 Worker 日志显示：

```text
transport = Redis DB 0
results   = Redis DB 1
```

原因：

> Celery CLI 子进程继承的 `CELERY_BROKER_URL` 和 `CELERY_RESULT_BACKEND` 环境变量覆盖了构造参数。

### 风险控制

- 使用随机队列，未消费正式队列任务；
- DB 0 中唯一随机绑定 Key 被精确删除；
- DB 1 中 5 个实验结果 Key 使用 120 秒 TTL，未删除无法按任务 ID 精确归属的 Key，随后自动过期；
- 验证 DB 0/1 中旧运行标识均为 0。

### 修复

在临时主进程和 Worker 子进程中同时强制设置：

```text
CELERY_BROKER_URL = Redis DB 15
CELERY_RESULT_BACKEND = Redis DB 15
```

并再次通过 Worker 启动日志验证运行时地址。

### 学习结论

```text
代码中的配置
≠ 进程最终生效配置
```

生产系统必须观察：

- Worker 启动日志；
- 实际 Broker；
- 实际 Result Backend；
- 队列名称；
- 配置来源和优先级。

这是本次练习中最有价值的真实排障之一。

---

## 8. 与报价中台的映射

当前报价任务链：

```text
FastAPI 创建 quote_jobs
→ quote_dispatcher 发送 job_id
→ Redis Broker
→ Celery Worker
→ quote_job_runner
→ MySQL 状态、事件和结果
```

### 已有能力

- `task_acks_late=True`；
- `worker_prefetch_multiplier=1`；
- 可见性超时大于任务时间上限；
- 任务有状态、事件、失败阶段和 Trace；
- 支持取消、超时标记和失败重试。

### 应继续核对

- 创建 `quote_jobs` 与发送 Celery 之间的双写窗口；
- 是否需要 Outbox 或 queued 扫描恢复；
- 普通 Worker 是否原子领取任务；
- 同一 `job_id` 重投时是否重复调用 N8N；
- `confirm_push` 是否有统一端到端幂等键；
- 队列积压、最老消息和死信是否有告警。

---

## 9. 面试回答

### 怎样保证消息不丢又不重复生效？

> 我会在同一 MySQL 事务中写业务数据和 Outbox，发布器扫描待发送事件并使用 Broker 确认。消费者成功处理后再 ACK，因此消息可能重复到达；消费者通过 `consumer_name + event_id` 唯一约束和业务效果唯一键实现幂等。我实际把同一个 Outbox 事件发布两次，消费结果是一条 applied、一条 duplicate ignored，最终只有一条业务效果。

### 怎样设计重试？

> 先分类错误，只对网络瞬断、429 和部分 5xx 等暂时错误做有限重试，使用指数退避、抖动和总预算。我的实验中前两次暂时失败，第 3 次成功，并记录了每次尝试；确定性毒消息达到预算后进入死信，没有产生业务效果。

### 死信之后怎么办？

> 死信不是结束或垃圾桶，需要保存原始事件、失败原因、版本和尝试次数，提供告警、人工修复和受控重放。重放仍使用原 event ID 或新的审计关联，并继续经过消费者幂等保护。

### Celery 能保证 exactly-once 吗？

> 不能。late ACK 提供的是更接近至少一次的处理方式，Worker 崩溃时任务可能重投。端到端一次效果要靠业务幂等、唯一约束、状态机和外部系统幂等键。我不会把 Celery 配置描述成 exactly-once。

---

## 10. 练习脚本

文件：

```text
AI_Middle_Office/scripts/mq_reliability_practice.py
```

执行：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m scripts.mq_reliability_practice
```

安全要求：

- Redis DB 15 必须为空；
- 五张固定练习表必须不存在；
- 运行后必须看到 DB 15 为 0 Key；
- Worker 日志必须显示 Broker/Backend 为 DB 15。

---

## 11. 本次没有证明什么

- 没有杀死执行中的 Worker 验证真实 Broker redelivery；
- 没有建立持续运行的 Outbox 发布器和扫描任务；
- 没有使用 Kafka、RabbitMQ 或 RocketMQ；
- 没有测试消息分区顺序；
- 没有做大规模队列积压和扩容实验；
- 没有实现正式死信管理页面；
- 没有修改当前 Celery 正式配置；
- 没有证明报价下发已经实现端到端一次效果。

---

## 12. 下一步练习

消息可靠性的基础闭环已经覆盖：

```text
Outbox
→ 重复发布
→ 幂等消费
→ 暂时错误重试
→ 毒消息死信
→ 配置优先级排障
```

下一步建议进入 RESTful API：设计异步任务创建、状态查询、取消、重试、统一错误码、权限和幂等请求。
