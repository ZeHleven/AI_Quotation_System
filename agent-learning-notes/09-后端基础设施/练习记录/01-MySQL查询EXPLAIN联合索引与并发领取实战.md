---
title: 练习 01：MySQL 查询、EXPLAIN、联合索引与并发领取实战
category: 后端基础设施练习记录
tags:
  - MySQL
  - EXPLAIN
  - Composite Index
  - Concurrency
  - Atomic Update
  - AI 报价中台
practiced_at: 2026-07-30
status: 已完成
---

# 练习 01：MySQL 查询、EXPLAIN、联合索引与并发领取实战

## 实验结论

本次实验在 MySQL 8.0.39 上使用 60,001 条合成报价任务完成了两个验证：

1. 对“用户 + 状态筛选，按创建时间倒序”的查询增加联合索引后，执行计划从全表扫描变成索引查找，估计扫描行数从 59,730 降到 400；
2. 两个 Worker 同时使用带状态条件的 `UPDATE` 领取同一任务，只有一个 Worker 更新成功，证明条件更新可以实现最小原子领取。

本次结果不能直接证明正式 `quote_jobs` 应该增加同一个索引。正式优化仍需基于真实查询、数据分布和写入成本。

---

## 1. 实验目标

### 查询与索引

理解：

- 为什么业务查询需要索引；
- 怎样阅读 `EXPLAIN`；
- 联合索引的列顺序怎样匹配过滤与排序；
- 为什么必须比较优化前后的真实指标。

### 并发领取

理解：

- “先查询、再更新”为什么存在竞争窗口；
- 条件更新怎样保证只有一个 Worker 获得执行权；
- 为什么队列重投后仍需要业务数据库保护。

---

## 2. 安全边界

| 项目 | 本次做法 |
|---|---|
| 数据库 | 当前开发环境 MySQL 8.0.39 |
| 业务表 | 未读取、未修改任何现有报价业务表 |
| 练习表 | `codex_practice_quote_jobs_20260730` |
| 数据 | 脚本生成的 60,001 条合成任务 |
| 清理 | 脚本 `finally` 只删除自己创建的固定练习表 |
| 二次确认 | 实验后查询 `information_schema`，练习表不存在 |

脚本有以下保护：

```text
表名必须以 codex_practice_ 开头
→ 创建前确认表不存在
→ 如果已存在则立即中止，不删除、不覆盖
→ 只有本进程成功创建后才允许 finally 删除
```

---

## 3. 业务场景

报价运营页面需要查询某个用户最近成功的 20 个报价任务：

```sql
SELECT job_id, status, duration_ms, created_at
FROM codex_practice_quote_jobs_20260730
WHERE username = 'user_018'
  AND status = 'succeeded'
ORDER BY created_at DESC
LIMIT 20;
```

查询包含：

```text
username：等值过滤
status：等值过滤
created_at：倒序排序
LIMIT 20：只需要最近记录
```

---

## 4. 索引前执行计划

`EXPLAIN` 结果：

| 字段 | 结果 | 含义 |
|---|---|---|
| `type` | `ALL` | 全表扫描 |
| `possible_keys` | `NULL` | 没有可用索引 |
| `key` | `NULL` | 实际没有使用索引 |
| `rows` | 59,730 | 优化器估计需要检查的行数 |
| `filtered` | 1.0% | 大量扫描结果会被过滤 |
| `Extra` | `Using where; Using filesort` | 过滤后还需要额外排序 |

这条 SQL 只返回 20 行，但需要检查大量数据并执行排序。

### 80 轮查询基线

| 指标 | 索引前 |
|---|---:|
| 返回行数 | 20 |
| 中位数 | 16.131 ms |
| P95 | 41.617 ms |
| 最小值 | 13.579 ms |
| 最大值 | 103.115 ms |

这些耗时只代表本次开发环境与合成数据，不是正式 SLO。

---

## 5. 联合索引设计

创建：

```sql
CREATE INDEX ix_practice_user_status_created
ON codex_practice_quote_jobs_20260730 (
    username,
    status,
    created_at
);
```

### 为什么是这个顺序

```text
username：第一个等值条件
status：第二个等值条件
created_at：在前两个条件确定后保持有序
```

因此数据库可以：

1. 直接定位 `user_018 + succeeded` 的索引区间；
2. 从该区间末尾反向扫描；
3. 取得最近 20 条后停止；
4. 不再对接近整表的数据做额外排序。

这不是“字段顺序固定公式”。如果主要查询只有 `status`、没有 `username`，这个索引未必合适。

---

## 6. 索引后执行计划

| 字段 | 结果 | 变化 |
|---|---|---|
| `type` | `ref` | 从全表扫描变成非唯一索引等值查找 |
| `possible_keys` | `ix_practice_user_status_created` | 优化器识别到联合索引 |
| `key` | `ix_practice_user_status_created` | 实际命中 |
| `ref` | `const,const` | 前两列使用常量等值匹配 |
| `rows` | 400 | 估计检查行数显著下降 |
| `Extra` | `Backward index scan` | 利用索引反向读取倒序结果 |

### 80 轮查询结果

| 指标 | 索引前 | 索引后 | 对比 |
|---|---:|---:|---:|
| 返回行数 | 20 | 20 | 结果不变 |
| 估计检查行数 | 59,730 | 400 | 约减少 149 倍 |
| 中位数 | 16.131 ms | 0.557 ms | 约快 29 倍 |
| P95 | 41.617 ms | 1.155 ms | 约快 36 倍 |
| 最大值 | 103.115 ms | 5.907 ms | 长尾显著下降 |

### 正确解读

- 索引减少了候选范围和排序成本；
- 59,730 与 400 是普通 `EXPLAIN` 的估计值，不是实际逐行计数；
- 小型合成表上的倍数不能直接外推到正式环境；
- 索引会增加磁盘占用和 `INSERT/UPDATE/DELETE` 维护成本；
- 正式表是否增加索引，必须先查看真实慢查询和数据分布。

---

## 7. 两个 Worker 并发领取

练习任务初始状态：

```text
job_id = practice-concurrent-claim
status = queued
worker_id = NULL
```

两个线程使用独立数据库连接，同时执行：

```sql
UPDATE codex_practice_quote_jobs_20260730
SET status = 'running',
    worker_id = :worker_id,
    started_at = NOW(6)
WHERE job_id = 'practice-concurrent-claim'
  AND status = 'queued';
```

真实结果：

| Worker | `affected_rows` | 结论 |
|---|---:|---|
| worker-A | 0 | 状态已被其他 Worker 改变，不能执行 |
| worker-B | 1 | 成功从 `queued` 原子迁移到 `running` |

最终数据：

```text
status = running
worker_id = worker-B
successful_claims = 1
```

### 为什么只有一个成功

第一个获得行更新权的 Worker 把状态改为 `running`。第二个 Worker 继续执行时：

```sql
WHERE status = 'queued'
```

已经不成立，所以影响 0 行。

Worker 必须检查 `affected_rows`：

```python
if result.rowcount != 1:
    return  # 没有获得任务执行权
```

不能忽略返回值后仍继续调用模型。

---

## 8. 为什么“先查询再更新”不可靠

危险流程：

```text
Worker A：SELECT，看到 queued
Worker B：SELECT，也看到 queued
Worker A：UPDATE running
Worker B：UPDATE running
两个 Worker 都认为自己应该执行
```

如果查询和更新之间没有锁、事务条件或版本校验，就存在竞态。

更稳妥的最小方案：

```text
一条带旧状态条件的 UPDATE
→ 检查 affected_rows
→ 只有成功者继续
```

长任务还应继续增加：

- `lease_token`；
- `lease_expires_at`；
- 心跳续租；
- fencing token；
- Checkpoint；
- 外部副作用幂等。

条件更新解决“谁领取”，不自动解决 Worker 崩溃后的接管。

---

## 9. 与报价中台的映射

真实 `quote_jobs` 已有：

- 唯一 `job_id`；
- `username`、`status`、`created_at`；
- `stage`、`failure_stage`、`trace_id`；
- 任务事件、取消、超时和重试；
- Celery late ACK 与低 prefetch。

本次练习带来的设计问题：

1. 运营列表的真实查询是否经常使用 `username + status + created_at`？
2. 如果是，现有单列索引是否足够？
3. 普通报价 Worker 是否通过原子条件获得执行权？
4. Celery 消息重投时，重复 Worker 是否会重复调用 N8N？
5. 外部确认下发是否有独立幂等保护？

这些问题需要继续通过真实 SQL 和代码路径验证，不能仅靠练习推断。

---

## 10. 30 秒面试回答

> 我用 6 万条合成报价任务验证过一个按用户和状态过滤、按创建时间倒序的列表查询。索引前执行计划是全表扫描并额外排序，估计检查约 5.97 万行；增加 `username、status、created_at` 联合索引后变成 `ref` 查找和反向索引扫描，估计检查约 400 行，80 轮查询中位数从约 16.1 毫秒降到 0.56 毫秒。这个结果只证明查询模式与索引匹配，正式表仍要结合慢查询和写入成本判断。我还用两个独立连接同时领取同一任务，带 `status='queued'` 的条件更新只允许一个 Worker 影响 1 行，从而避免明显的重复执行。

---

## 11. 练习脚本

文件：

```text
AI_Middle_Office/scripts/mysql_backend_foundation_practice.py
```

执行：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m scripts.mysql_backend_foundation_practice
```

脚本每次运行都会生成新合成数据，耗时会因机器、网络和数据库负载变化。

---

## 12. 本次没有证明什么

- 没有证明正式 `quote_jobs` 必须增加该联合索引；
- 没有对正式业务数据做压测；
- 没有测试百万级数据和持续高并发；
- 没有测量索引对写入吞吐和磁盘的影响；
- 没有实现任务租约、续租和故障接管；
- 没有验证外部 N8N 下发的端到端幂等；
- 没有形成正式生产容量结论。

这些边界应在面试中主动说明。

---

## 13. 下一步练习

建议继续完成：

1. 只读分析真实报价列表 SQL 和现有索引；
2. 使用 `EXPLAIN ANALYZE` 对比估计行与实际行；
3. 模拟事务回滚，验证任务与事件同时成功或失败；
4. 模拟 Worker 领取后崩溃，引出租约和恢复；
5. 为确认下发设计幂等记录表。
