---
title: MySQL 数据建模、事务、索引与 SQL 优化
category: 后端基础设施
tags:
  - MySQL
  - SQL
  - Transaction
  - Index
  - EXPLAIN
  - SQLAlchemy
reviewed_at: 2026-07-30
status: 持续更新
---

# MySQL 数据建模、事务、索引与 SQL 优化

> 配套实战：
>
> - [练习 01：MySQL 查询、EXPLAIN、联合索引与并发领取实战](./练习记录/01-MySQL查询EXPLAIN联合索引与并发领取实战.md)
> - [练习 02：MySQL 事务回滚、唯一约束、乐观锁与死锁实战](./练习记录/02-MySQL事务回滚唯一约束乐观锁与死锁实战.md)

## 核心结论

MySQL 在 AI 应用中保存的是可追责的业务事实，而不是模型临时上下文：

```text
模型输出可以重算
缓存可以失效
消息可以重投
但报价状态、成本版本、人工确认和审计记录必须可靠保存
```

SQL 优化的基本顺序是：

```text
明确查询目标
→ 查看真实 SQL
→ EXPLAIN / EXPLAIN ANALYZE
→ 找扫描、排序、回表和锁等待
→ 调整查询或索引
→ 用相同数据重新测量
```

不要一看到慢查询就盲目增加索引。

---

## 1. 数据建模：先找事实和生命周期

### 常见对象

| 类型 | 报价中台示例 | 建模重点 |
|---|---|---|
| 主数据 | 成本条目、用户、项目 | 唯一性、状态、版本 |
| 业务单据 | 报价任务、询价、执行任务 | 生命周期、归属、终态 |
| 明细 | 报价行、标准需求行 | 外键、顺序、来源追溯 |
| 事件 | 任务事件、成本历史 | 只追加、时间、操作者 |
| 草稿 | 预审草稿、会议草稿 | 可修改、版本、最终确认 |
| 审计 | 成本访问、权限事件 | 不可抵赖、最小必要信息 |

### 主键与业务键

- 自增 `id` 适合数据库内部关联；
- UUID/业务编号适合跨系统传递；
- 业务唯一性必须使用唯一约束，而不是只在 Python 中查询；
- 不要把可修改字段当主键。

示例：

```sql
CREATE TABLE quote_jobs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_id CHAR(36) NOT NULL,
    username VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_quote_jobs_job_id (job_id),
    KEY ix_quote_jobs_user_status_created (
        username, status, created_at
    )
);
```

是否需要联合索引，必须由查询模式和执行计划决定，不能仅因“看起来常用”就添加。

### 金额不要使用浮点数

`FLOAT/DOUBLE` 存在二进制精度误差。正式金额建议：

```sql
unit_price DECIMAL(18, 4)
total_price DECIMAL(18, 2)
```

同时明确：

- 舍入规则；
- 币种；
- 税前/含税口径；
- 单价和合计由谁计算；
- 历史价格是否需要快照。

---

## 2. 事务与 ACID

| 特性 | 含义 | 工程问题 |
|---|---|---|
| Atomicity | 要么全部成功，要么全部失败 | 报价确认与审计不能只成功一半 |
| Consistency | 事务前后满足约束 | 终态任务不能被改回 `running` |
| Isolation | 并发事务相互隔离 | 两个管理员不能重复启用同一版本 |
| Durability | 提交后持久保存 | 服务重启不能丢失确认结果 |

### 事务边界

事务应该围绕一项本地业务不变量，而不是围绕整个 HTTP 请求无限扩大。

```python
def activate_cost_item(db, item_id: int):
    item = (
        db.query(CostItem)
        .filter(CostItem.id == item_id)
        .with_for_update()
        .one()
    )
    if item.status != "draft":
        raise ValueError("only draft can become active")

    item.status = "active"
    db.add(CostItemHistory(
        cost_item_id=item.id,
        action="activated",
    ))
    db.commit()
```

如果后面还要调用 RAG 或 N8N，不要让数据库事务一直等待网络请求。推荐：

```text
本地事务保存事实和待发送事件
→ 提交
→ 后台发送外部请求
→ 保存回执
```

这就是 Outbox 等模式的基础。

### 隔离级别

| 隔离级别 | 可能出现的问题 | 常见使用 |
|---|---|---|
| Read Uncommitted | 脏读、不可重复读、幻读 | 很少使用 |
| Read Committed | 不可重复读、幻读 | 许多业务数据库 |
| Repeatable Read | InnoDB 默认；一致性读更稳定 | MySQL 常见默认 |
| Serializable | 并发最低、隔离最强 | 少量强一致场景 |

不要把“提高隔离级别”当成并发问题的唯一解。唯一约束、条件更新、行锁和状态机通常更直接。

---

## 3. MVCC、锁和并发更新

### MVCC

MVCC 允许读操作通过版本信息读取一致性快照，减少普通读写冲突。它不代表所有查询都无锁：

- 当前读、`SELECT ... FOR UPDATE` 会加锁；
- 更新和删除会锁定记录；
- 范围条件可能涉及间隙锁；
- 长事务会拖延旧版本清理。

### 乐观锁

适合冲突不频繁的场景：

```sql
UPDATE quote_jobs
SET status = 'running',
    version = version + 1
WHERE job_id = ?
  AND status = 'queued'
  AND version = ?;
```

受影响行数为 0，代表状态或版本已经变化。

### 悲观锁

适合必须串行修改同一行的短事务：

```sql
SELECT *
FROM cost_items
WHERE id = ?
FOR UPDATE;
```

风险：

- 等待时间增加；
- 锁顺序不一致会死锁；
- 范围过大降低并发；
- 网络调用放在事务内会长期持锁。

### 死锁处理

1. 所有事务按相同顺序访问资源；
2. 缩小事务和锁范围；
3. 为过滤条件建立合适索引；
4. 捕获死锁错误并有限重试；
5. 保存死锁日志，不能无限重试。

---

## 4. InnoDB 索引

### B+Tree 为什么适合范围查询

- 非叶子节点保存导航信息；
- 叶子节点有序；
- 高扇出减少磁盘页访问；
- 叶子节点之间便于范围扫描。

### 聚簇索引与二级索引

InnoDB 主键索引的叶子节点保存整行数据。二级索引叶子节点通常保存：

```text
二级索引列 + 主键
```

使用二级索引查找其他列时，可能先找到主键，再回主键索引取整行，这叫回表。

### 联合索引与最左前缀

索引：

```sql
KEY ix_job_user_status_created (
    username, status, created_at
)
```

通常适合：

```sql
WHERE username = ?
WHERE username = ? AND status = ?
WHERE username = ? AND status = ?
ORDER BY created_at DESC
```

不一定适合：

```sql
WHERE status = ?
WHERE created_at > ?
```

联合索引列顺序取决于等值过滤、范围、排序和覆盖需求，不是简单把“区分度最高”的字段放最前。

### 覆盖索引

查询所需列全部在索引中，可以减少回表：

```sql
SELECT job_id, status, created_at
FROM quote_jobs
WHERE username = ?
  AND status = ?
ORDER BY created_at DESC
LIMIT 20;
```

但不能为了覆盖查询把大量大字段塞入索引。

---

## 5. 索引容易失效或收益下降的场景

- 在索引列上使用不匹配的函数或表达式；
- 隐式类型转换；
- 前置模糊匹配：`LIKE '%关键词'`；
- 联合索引跳过前导列；
- 范围条件之后还期望所有后续列都参与定位；
- 返回数据比例过高，优化器认为全表扫描更便宜；
- `OR` 两侧缺少合适索引；
- 排序和过滤的索引顺序不兼容。

不要背诵“必然失效”。最终以优化器选择和执行计划为准。

---

## 6. 使用 EXPLAIN

重点观察：

| 字段 | 关注点 |
|---|---|
| `type` | `const/ref/range` 通常优于 `ALL`，但需结合数据量 |
| `possible_keys` | 理论可用索引 |
| `key` | 实际选择索引 |
| `key_len` | 使用了联合索引的多大部分 |
| `rows` | 预计扫描行数 |
| `filtered` | 过滤后预计保留比例 |
| `Extra` | `Using index`、临时表、排序等 |

示例：

```sql
EXPLAIN ANALYZE
SELECT job_id, status, created_at
FROM quote_jobs
WHERE username = 'alice'
  AND status = 'succeeded'
ORDER BY created_at DESC
LIMIT 20;
```

检查：

1. 是否命中预期索引；
2. 实际扫描行是否远高于返回行；
3. 是否额外排序；
4. 是否读取了大文本列；
5. 冷缓存和热缓存耗时是否不同。

---

## 7. 高频 SQL 优化

### 不要 `SELECT *`

大字段如 `result_json`、`events_json`、文件 Base64 会增加：

- 磁盘读取；
- 网络传输；
- ORM 对象构造；
- 内存占用。

列表页只查摘要列，详情页再取大字段。

### 深分页

```sql
SELECT *
FROM quote_jobs
ORDER BY id DESC
LIMIT 100000, 20;
```

数据库仍可能扫描并丢弃大量记录。可使用游标/Keyset：

```sql
SELECT job_id, status, created_at
FROM quote_jobs
WHERE id < ?
ORDER BY id DESC
LIMIT 20;
```

### N+1 查询

先查 100 个任务，再为每个任务单独查事件，会产生 101 次 SQL。解决方式：

- Join；
- ORM eager loading；
- 批量 `IN` 查询；
- 聚合接口；
- 只在详情页加载事件。

### 批量写入

- 减少每行一次网络往返；
- 控制单批大小；
- 保留失败定位能力；
- 不因批量而跳过业务校验；
- 注意大事务对锁和日志的压力。

---

## 8. 连接池与长事务

当前项目在 `app/core/database.py` 中使用：

```text
pool_pre_ping = True
MySQL pool_recycle = 1800
pool_timeout = 10
```

含义：

- `pool_pre_ping`：取连接时检查连接是否可用；
- `pool_recycle`：避免长期闲置连接被服务端关闭后继续复用；
- `pool_timeout`：连接池耗尽时限制等待时间。

连接池不是越大越好。估算时要考虑：

```text
应用实例数 × 每实例连接上限
≤ MySQL 可承受连接数
```

长事务的风险：

- 长时间持锁；
- Undo 版本积累；
- 连接池被占满；
- 故障回滚成本高；
- 外部调用放大不确定性。

---

## 9. 报价中台映射

### 已有设计

- `quote_jobs.job_id` 使用唯一约束和索引；
- `username`、`status`、`failure_stage`、`trace_id` 等有单列索引；
- `quote_job_events.quote_job_id` 支持按任务读取事件；
- 多个新模型通过 `UniqueConstraint` 和联合 `Index` 固化业务规则；
- 数据库结构变更统一使用 Alembic；
- 正式成本与 RAG 数据源是 MySQL `cost_items.active`；
- 连接启用了 `pool_pre_ping` 和 MySQL 连接回收。

### 可练习的优化问题

1. 报价运营列表是否经常按“用户 + 状态 + 创建时间”查询？
2. 列表接口是否不必要地读取 `result_json` 或文件内容？
3. 事件查询是否需要 `(quote_job_id, event_index)` 联合索引或唯一约束？
4. `Float` 金额字段是否满足正式财务精度要求？
5. 状态迁移是否通过条件更新防止并发覆盖？

这些是分析题，不代表应该立即修改生产代码。必须先看真实 SQL、数据量和执行计划。

---

## 10. 面试回答

### 怎样优化一条慢 SQL？

> 我先确认接口耗时是否真的来自数据库，再从慢查询或 Trace 取得真实 SQL 和参数。随后使用 `EXPLAIN ANALYZE` 查看访问类型、扫描行、回表、排序和临时表。如果是查询结构问题，会先减少返回列、消除 N+1 或改写分页；如果查询模式稳定，再设计联合或覆盖索引。修改后使用相同数据量比较耗时、扫描行和写入成本，而不是只看是否命中索引。

### 为什么数据库唯一约束比代码查重更可靠？

> “先查询不存在、再插入”在并发下不是原子的，两个请求可能同时通过检查。唯一约束由数据库在写入点保证不变量，应用捕获冲突后返回已有结果或 409。代码校验改善提示，数据库约束负责最终正确性。

### 事务里为什么不应调用大模型？

> 模型调用耗时和失败都不可预测，把它放在事务内会长期占用连接和锁，放大并发问题。我会先在本地事务中保存任务和待执行事件，提交后异步调用模型，最后用状态机和幂等键写回结果。

---

## 11. 动手练习

### 练习一：执行计划

对任务列表设计三种查询：

1. 按用户和状态筛选；
2. 按失败阶段和时间筛选；
3. 按游标翻页。

分别记录：

```text
SQL
索引
估计/实际扫描行
返回行数
耗时
是否排序
是否回表
```

### 练习二：并发状态迁移

实现：

```sql
UPDATE quote_jobs
SET status = 'running'
WHERE job_id = ?
  AND status = 'queued';
```

并发运行两个 Worker，验证只有一个更新成功。

### 练习三：N+1

构造 100 个任务及事件：

- 先用循环逐个查事件；
- 再使用批量查询；
- 比较 SQL 次数和耗时。

---

## 12. 掌握检查

- [ ] 能区分主键、业务键和唯一约束；
- [ ] 能解释 ACID、隔离级别和 MVCC；
- [ ] 能正确选择乐观锁或悲观锁；
- [ ] 能解释聚簇索引、二级索引和回表；
- [ ] 能根据查询设计联合索引；
- [ ] 能阅读 `EXPLAIN ANALYZE`；
- [ ] 能解决深分页和 N+1；
- [ ] 能说明连接池与长事务风险；
- [ ] 能把 SQL 优化映射到报价中台真实表和接口；
- [ ] 不会在缺少指标时直接宣称“加索引能提升性能”。

---

## 代码证据

- `AI_Middle_Office/app/core/database.py`
- `AI_Middle_Office/app/models/quote_job.py`
- `AI_Middle_Office/app/models/cost_item.py`
- `AI_Middle_Office/app/models/budget_pricing.py`
- `AI_Middle_Office/app/models/bid_intake_runtime.py`
- `AI_Middle_Office/alembic/versions/`
