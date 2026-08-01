---
title: 练习 02：MySQL 事务回滚、唯一约束、乐观锁与死锁实战
category: 后端基础设施练习记录
tags:
  - MySQL
  - Transaction
  - Unique Constraint
  - Optimistic Lock
  - Deadlock
  - Retry
  - AI 报价中台
practiced_at: 2026-07-30
status: 已完成
---

# 练习 02：MySQL 事务回滚、唯一约束、乐观锁与死锁实战

## 实验结论

本次在 MySQL 8.0.39 上完成四项隔离实验：

1. 同一事务内先写任务、再写事件，随后故意触发唯一约束错误，任务和事件均回滚为 0 行；
2. 两个并发请求插入相同业务编号，数据库只保存一行，另一个收到 MySQL 1062 重复键错误；
3. 两个 Worker 使用相同旧版本更新任务，只有一个影响 1 行，最终版本从 0 变成 1；
4. 两个事务按相反顺序加锁，真实触发 MySQL 1213 死锁；失败事务完整回滚，随后按统一锁顺序有限重试成功。

核心认识：

```text
事务保证一组本地操作原子
唯一约束保证最终唯一性
乐观锁防止并发覆盖
统一锁顺序减少死锁
死锁重试必须重跑整个事务
```

---

## 1. 安全边界

| 项目 | 本次做法 |
|---|---|
| 数据库 | 当前开发环境 MySQL 8.0.39 |
| 业务数据 | 未读取、未修改任何现有报价业务表 |
| 任务练习表 | `codex_practice_tx_jobs_20260730` |
| 事件练习表 | `codex_practice_tx_events_20260730` |
| 数据 | 脚本生成的少量合成任务和事件 |
| 安全保护 | 任一同名表已存在就立即中止，不覆盖、不删除 |
| 清理 | 先删除事件表，再删除任务表 |
| 二次确认 | `information_schema` 查询剩余练习表数量为 0 |

练习表只用于验证事务和并发机制。

---

## 2. 实验一：事务回滚

### 业务问题

创建报价任务时还要写入首个审计事件：

```text
quote_jobs：任务事实
quote_job_events：任务创建事件
```

如果任务写成功但事件失败，系统会留下无法解释的不完整记录。

### 实验事务

```sql
BEGIN;

INSERT INTO practice_jobs (...);

INSERT INTO practice_events
    (job_id, event_index, event_type)
VALUES
    ('practice-rollback-job', 1, 'job_created');

-- 故意重复相同 job_id + event_index
INSERT INTO practice_events
    (job_id, event_index, event_type)
VALUES
    ('practice-rollback-job', 1, 'duplicate_event');

COMMIT;
```

事件表具有：

```sql
UNIQUE (job_id, event_index)
```

第三条写入触发：

```text
MySQL error 1062：Duplicate entry
```

应用捕获错误并执行：

```sql
ROLLBACK;
```

### 真实结果

| 检查项 | 结果 |
|---|---:|
| 故意触发的错误码 | 1062 |
| 回滚后任务行数 | 0 |
| 回滚后事件行数 | 0 |
| 原子性验证 | 通过 |

虽然前两条 SQL 已执行，但整个事务未提交，因此都被撤销。

### 学习结论

事务边界必须覆盖同一个本地业务不变量：

> “任务存在时，任务创建事件也必须存在。”

但不能把不可预测的大模型或 N8N 网络调用放进这个事务，否则会长期占用连接和数据库锁。

---

## 3. 实验二：唯一约束解决并发查重

### 危险方式

两个请求分别执行：

```text
SELECT：业务编号不存在
→ INSERT
```

由于查询和插入不是一个原子动作，两个请求可能同时通过查询。

### 实验方式

两个独立连接同时插入：

```text
job_id = practice-unique-job
```

任务表具有：

```sql
UNIQUE (job_id)
```

### 真实结果

| 请求 | 结果 | 错误码 |
|---|---|---:|
| request-A | 被唯一约束拒绝 | 1062 |
| request-B | 插入成功 | 无 |

数据库最终：

```text
stored_rows = 1
job_id = practice-unique-job
```

哪个请求成功由并发调度决定，不应该依赖 A 或 B 固定胜出。

### 正确处理

应用层可以先校验以改善提示，但最终仍要捕获唯一约束冲突：

```python
try:
    db.commit()
except IntegrityError as exc:
    db.rollback()
    if mysql_error_code(exc) == 1062:
        return existing_resource_or_conflict()
    raise
```

---

## 4. 实验三：乐观锁防止并发覆盖

### 初始任务

```text
job_id = practice-optimistic-job
status = queued
version = 0
worker_id = NULL
```

两个 Worker 同时执行：

```sql
UPDATE practice_jobs
SET status = 'running',
    worker_id = :worker_id,
    version = version + 1
WHERE job_id = 'practice-optimistic-job'
  AND status = 'queued'
  AND version = 0;
```

### 真实结果

| Worker | `affected_rows` |
|---|---:|
| worker-A | 1 |
| worker-B | 0 |

最终数据：

```text
status = running
version = 1
worker_id = worker-A
successful_updates = 1
```

第二个 Worker 必须根据 `affected_rows = 0` 判断：

- 数据已被其他请求修改；
- 自己没有任务所有权；
- 不能继续调用模型或写最终结果。

### 与普通状态条件的区别

只校验状态：

```sql
WHERE status = 'queued'
```

可以完成简单领取。

再校验版本：

```sql
WHERE status = 'queued' AND version = :old_version
```

还能检测同一状态下的其他并发修改，适合草稿、配置和人工编辑场景。

---

## 5. 实验四：制造真实死锁

### 两条资源

```text
practice-deadlock-A
practice-deadlock-B
```

### 相反锁顺序

```text
事务 A：锁 A → 等待 → 锁 B
事务 B：锁 B → 等待 → 锁 A
```

形成：

```text
事务 A 等待事务 B 释放 B
事务 B 等待事务 A 释放 A
```

MySQL 检测到循环等待后，选择一个事务作为牺牲者并回滚。

### 第一次真实结果

| Worker | 结果 | 错误码 |
|---|---|---:|
| worker-A | 提交成功 | 无 |
| worker-B | 死锁回滚 | 1213 |

不能依赖固定某个 Worker 被回滚；由 InnoDB 根据事务代价等因素选择。

### 有限重试

失败事务没有从报错语句继续，而是：

```text
回滚整个事务
→ 重新读取当前状态
→ 按统一 A → B 顺序加锁
→ 完整执行一次
→ 提交
```

最终：

| 资源 | `counter_value` |
|---|---:|
| practice-deadlock-A | 2 |
| practice-deadlock-B | 2 |

代表两个逻辑事务最终各生效一次。

---

## 6. 怎样减少死锁

### 统一访问顺序

所有代码都按同样顺序锁定资源：

```text
按主键升序
或按固定业务编号顺序
```

### 缩小事务

- 不在事务中调用模型、RAG 或外部 HTTP；
- 不让用户输入期间保持事务；
- 尽快提交或回滚。

### 使用合适索引

缺少索引时，更新可能扫描并锁定更多记录，增加冲突范围。

### 控制批量大小

一个事务更新太多行，会扩大锁集合和回滚成本。

### 有限重试

只对 MySQL 1213 等明确的暂时并发错误重试：

```text
最大次数
退避和抖动
重新开始整个事务
重新读取数据
操作本身幂等
保存重试指标
```

不能捕获所有数据库错误后无限重试。

---

## 7. 四种机制不要混

| 机制 | 作用 | 本次证据 |
|---|---|---|
| 事务 | 一组本地写入要么全部成功，要么全部失败 | 任务和事件同时回滚 |
| 唯一约束 | 数据库最终禁止重复业务键 | 并发插入只保留一行 |
| 乐观锁 | 数据版本变化后拒绝旧请求覆盖 | 两个 Worker 只有一个更新成功 |
| 悲观锁 | 事务期间锁定记录 | `SELECT ... FOR UPDATE` 制造死锁 |

分布式锁不能替代这些数据库机制。

---

## 8. 报价中台映射

### 任务与事件

`quote_jobs` 与 `quote_job_events` 应在需要共同成立的本地操作中使用明确事务，避免只写任务或只写事件。

### 业务唯一性

项目多个模型已经使用：

- `UniqueConstraint`；
- 单字段 `unique=True`；
- 联合唯一约束；
- Alembic 数据库迁移。

这比只在 FastAPI 中“先查是否存在”更可靠。

### 并发任务

Celery late ACK 允许任务重投。普通报价 Worker 仍应依靠：

- 条件状态更新；
- 原子领取或租约；
- 版本/Token 写保护；
- 外部副作用幂等。

### 外部调用

模型、RAG、N8N 不应放在长数据库事务中。推荐：

```text
短事务保存任务与待执行事件
→ 提交
→ 异步外部调用
→ 短事务条件写回
```

---

## 9. 面试回答

### 事务回滚

> 我用任务表和事件表做过事务实验。在同一事务中写入任务和首个事件，然后故意插入重复事件触发 MySQL 1062。捕获异常并回滚后，两张表相关行数都为 0，证明任务事实和审计事件不会只成功一半。模型和外部 HTTP 不放进这个事务，避免长期占用连接和锁。

### 并发查重

> “先查后插”在并发下不可靠，两个请求可能同时看到不存在。我会把业务唯一性固化为数据库唯一约束，应用捕获 1062 后返回已有资源或 409。本地校验只负责友好提示，数据库约束负责最终正确性。

### 乐观锁

> 更新时同时带旧版本和旧状态，只有影响 1 行才算获得修改权。并发请求中的失败者读取最新数据并返回冲突，不能继续覆盖。它适合冲突不频繁的任务状态和草稿编辑。

### 死锁

> 我实际用两个事务按 A→B 和 B→A 的相反顺序加锁，触发了 MySQL 1213。治理首先是统一锁顺序、缩短事务并确保过滤条件有索引；仍发生时，只对明确死锁错误有限退避重试，并从事务开始重新执行，不能从失败语句中间继续。

---

## 10. 练习脚本

文件：

```text
AI_Middle_Office/scripts/mysql_transaction_practice.py
```

执行：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m scripts.mysql_transaction_practice
```

脚本每次都会检查同名练习表不存在，并在结束后按外键依赖顺序清理。

---

## 11. 本次没有证明什么

- 没有修改或压测正式业务表；
- 没有证明现有所有服务代码的事务边界都正确；
- 没有实现跨 MySQL、Redis、N8N 的分布式事务；
- 没有证明所有数据库错误都可以重试；
- 没有实现完整任务租约与 fencing token；
- 没有验证外部确认下发的端到端幂等；
- 没有形成正式生产并发容量结论。

---

## 12. MySQL 阶段总结

完成练习 01 和练习 02 后，已经实际验证：

```text
SQL 查询
→ EXPLAIN
→ 联合索引
→ 条件领取
→ 事务回滚
→ 唯一约束
→ 乐观锁
→ 悲观锁
→ 死锁检测与有限重试
```

下一阶段进入 Redis，重点练习 Cache Aside、缓存一致性和热点 Key 并发重建。
