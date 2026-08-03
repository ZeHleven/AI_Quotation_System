---
title: 练习 03：Redis Cache Aside、一致性、穿透、热点重建与降级实战
category: 后端基础设施练习记录
tags:
  - Redis
  - Cache Aside
  - Consistency
  - Cache Penetration
  - Hot Key
  - TTL Jitter
  - Degradation
  - AI 报价中台
practiced_at: 2026-07-30
status: 已完成
---

# 练习 03：Redis Cache Aside、一致性、穿透、热点重建与降级实战

## 实验结论

本次在 Redis 7.2.13、MySQL 8.0.39 上完成六项隔离实验：

1. 第一次查询回源 MySQL，第二次查询命中 Redis，数据库只查询 1 次；
2. MySQL 价格从 100 更新为 120 并提交后删除缓存，下一次读取获得版本 2 的新价格，随后再次命中缓存；
3. 对不存在条目连续查询 20 次，不缓存空值时回源 20 次，使用 30 秒空值缓存后只回源 1 次；
4. 24 个并发请求同时访问失效热点 Key，朴素实现触发 24 次数据库重建，互斥重建后降为 1 次；
5. 20 个固定 TTL Key 只有 1 种过期时间，加入随机抖动后分散为 12 种 TTL；
6. 模拟 Redis 连接超时时，系统回源 MySQL 并正确返回数据。

核心认识：

```text
MySQL 管事实
Redis 管速度
Cache Aside 管读写路径
空值缓存管穿透
互斥重建管热点失效
TTL 抖动管同时过期
降级与限流共同保护数据库
```

---

## 1. 安全边界

| 项目 | 本次做法 |
|---|---|
| Redis | 7.2.13 |
| Redis 逻辑库 | DB 15 |
| Celery 使用的库 | DB 0/1，本次未连接 |
| Key 前缀 | `codex:practice:cache:{随机 UUID}` |
| MySQL 练习表 | `codex_practice_cache_items_20260730` |
| 业务数据 | 未读取、未修改任何现有成本或报价表 |
| Redis 清理 | 不使用 `FLUSHDB/FLUSHALL`，只删除本轮随机前缀 Key |
| MySQL 清理 | 只删除脚本创建的固定练习表 |

独立复核结果：

```text
Redis DB 15 key 数量 = 0
codex:practice:cache:* 剩余 Key = 0
MySQL 练习表存在 = false
```

逻辑 DB 只能隔离 Key 空间，不能隔离 Redis 实例的 CPU、内存和网络资源。本次负载很小，不影响 Celery 队列。

---

## 2. 实验数据

MySQL 合成表保存两个成本条目：

| `item_id` | 名称 | 初始单价 | 版本 |
|---|---|---:|---:|
| 1001 | 合成成本条目 | 100.00 | 1 |
| 2001 | 合成热点条目 | 88.88 | 1 |

Redis Key 使用随机运行前缀，例如：

```text
codex:practice:cache:{run_id}:cost-item:1001
```

正式系统还应在 Key 中加入租户、权限范围和数据版本。

---

## 3. 实验一：Cache Aside 命中

### 读取流程

```text
读取 Redis
→ miss
→ 查询 MySQL
→ SETEX 120 秒
→ 返回

第二次请求
→ Redis hit
→ 直接返回
```

### 真实结果

| 指标 | 结果 |
|---|---|
| 第一次数据来源 | `database` |
| 第二次数据来源 | `cache` |
| MySQL 查询次数 | 1 |
| Redis TTL | 120 秒 |
| 两次返回条目 | 相同 |

### 伪代码

```python
def get_cost_item(item_id):
    cached = redis.get(cache_key(item_id))
    if cached is not None:
        return decode(cached)

    item = mysql.query(item_id)
    if item is not None:
        redis.setex(cache_key(item_id), 120, encode(item))
    return item
```

缓存必须允许丢失。Redis Key 被删除后，数据仍能从 MySQL 重建。

---

## 4. 实验二：更新数据库后删除缓存

### 初始状态

```text
MySQL unit_price = 100.00
MySQL version = 1
Redis 已缓存旧值
```

### 更新顺序

```text
BEGIN
→ MySQL unit_price = 120.00
→ version = version + 1
→ COMMIT
→ DELETE Redis Key
```

### 真实结果

| 检查项 | 结果 |
|---|---|
| 缓存中的旧价格 | 100.00 |
| 删除缓存影响 Key | 1 |
| 更新后第一次读取来源 | MySQL |
| 更新后价格 | 120.00 |
| 更新后版本 | 2 |
| 再次读取来源 | Redis |
| 本实验 MySQL 查询次数 | 1 |

### 为什么提交后再删除

如果先删缓存、数据库事务随后失败，其他请求可能重建旧值。提交数据库后删除缓存，事实库已经是新版本。

但该顺序仍有窗口：

```text
MySQL 提交成功
→ Redis 删除失败
→ 旧值存活到 TTL
```

生产治理需要：

- 有界 TTL；
- 删除重试；
- Outbox/变更事件；
- 版本化 Key；
- 高风险操作回源或校验版本。

本次没有声称实现了无成本强一致。

---

## 5. 实验三：缓存穿透

查询不存在的：

```text
item_id = 9999
```

### 不缓存空值

每次 Redis miss 后都查询 MySQL：

| 请求数 | MySQL 查询数 |
|---:|---:|
| 20 | 20 |

### 缓存空值

第一次查询确认不存在后写入：

```text
value = __NULL__
TTL = 30 秒
```

真实结果：

| 来源 | 次数 |
|---|---:|
| 第一次 MySQL | 1 |
| 后续空值缓存 | 19 |
| MySQL 总查询数 | 1 |

数据库回源减少：

```text
20 → 1
```

### 风险

空值缓存 TTL 不能过长，否则数据刚创建时可能短暂读不到。

还需要：

- 参数格式校验；
- 用户/IP 限流；
- 不存在查询比例监控；
- 大规模明确 ID 集合可考虑布隆过滤器。

---

## 6. 实验四：热点 Key 并发重建

模拟热点条目缓存刚好失效，同时到达 24 个请求；数据库查询增加 80 ms 模拟重建成本。

### 朴素实现

```text
24 个请求同时 miss
→ 24 个请求都查询 MySQL
→ 24 个请求都回写同一个 Key
```

真实结果：

| 指标 | 结果 |
|---|---:|
| 并发请求 | 24 |
| MySQL 重建查询 | 24 |
| 数据来源 | 24 次 database |
| 总耗时 | 123.275 ms |

虽然总耗时没有乘 24，但数据库在同一时间承受了 24 次重复查询。

### 互斥重建

流程：

```text
Redis miss
→ SET lock_key token NX PX 2000
→ 获锁者再次检查缓存
→ 只有获锁者查询 MySQL 和重建
→ 其他请求短暂轮询缓存
→ Lua 比较 token 后安全释放锁
```

真实结果：

| 指标 | 结果 |
|---|---:|
| MySQL 正常重建查询 | 1 |
| 超时回源查询 | 0 |
| 获锁重建请求 | 1 |
| 等待后命中请求 | 23 |
| 总耗时 | 97.852 ms |

数据库重建：

```text
24 → 1
```

### 为什么要二次检查

请求等待获得锁期间，其他请求可能已经重建缓存。获得锁后再次读缓存，可以避免重复查询数据库。

### 为什么要比较 Token 解锁

不能直接：

```text
DEL lock_key
```

旧请求超时恢复后可能删除新持有者的锁。实验使用 Lua：

```lua
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
```

该锁只用于可重建缓存的短临界区，不替代数据库事务、任务租约和业务幂等。

---

## 7. 实验五：TTL 随机抖动

### 固定 TTL

20 个 Key 都设置：

```text
TTL = 60 秒
```

结果：

```text
唯一 TTL 数量 = 1
范围 = 60～60 秒
```

它们可能在相近时间同时失效。

### 加入抖动

```text
TTL = 60 + random(0, 30)
```

真实结果：

```text
唯一 TTL 数量 = 12
范围 = 61～90 秒
```

这只能分散正常过期，不能解决 Redis 整体故障。雪崩治理还需要：

- 数据库限流；
- 热点预热；
- Redis 高可用；
- 降级；
- 背压；
- 故障演练。

---

## 8. 实验六：Redis 故障降级

实验使用不可达端口模拟 Redis 连接超时：

```text
Redis GET
→ TimeoutError
→ 捕获 RedisError
→ 查询 MySQL
→ 返回 item_id = 1001
```

真实结果：

| 检查项 | 结果 |
|---|---|
| 缓存错误 | `TimeoutError` |
| 降级数据源 | MySQL |
| 返回条目 | 1001 |
| MySQL 查询次数 | 1 |

### 重要边界

回源成功不代表可以无限回源。Redis 整体故障时，全部流量直接进入 MySQL，可能造成数据库雪崩。

因此降级必须配合：

- 入口限流；
- 数据库连接池保护；
- 热点请求合并；
- 本地短缓存；
- 非核心接口降级；
- Redis 故障告警。

---

## 9. 与报价中台的映射

### 当前 Redis 角色

- Celery Broker：DB 0；
- Celery Result Backend：DB 1；
- Celery 模式下 SlowAPI 使用 Redis 存储限流状态；
- 运维健康检查对 Redis 执行 `PING`；
- MySQL 保存报价、成本和状态事实。

### 正式缓存设计原则

对 `cost_items.active` 可考虑缓存，但必须：

```text
事实源 = MySQL
Key 包含租户/条目/版本
状态变更提交后失效缓存
报价确认保存成本快照
Redis 失败时不丢业务事实
```

RAG 或模型结果缓存还需要加入：

- 租户；
- 权限范围；
- 知识库版本；
- 检索策略；
- Prompt；
- 模型版本。

否则可能发生越权或旧版本污染。

### Redis 多职责风险

缓存、Celery Broker 和限流如果共用一个 Redis 实例：

- 缓存大 Key 可能影响 Broker 延迟；
- 内存淘汰可能影响队列；
- 阻塞命令影响所有角色；
- 逻辑 DB 不能提供资源隔离。

正式生产需根据风险决定是否拆分实例和资源配额。

---

## 10. 面试回答

### Cache Aside

> 我做过 Redis Cache Aside 实验。第一次请求回源 MySQL 并写入带 TTL 的缓存，第二次直接命中，数据库只查询一次。更新时先提交 MySQL，再删除缓存；下一次读取新版本并重建。删除失败仍可能存在短暂旧值，所以还要用 TTL、重试或 Outbox 补偿，高风险操作回源或校验版本。

### 缓存穿透

> 我连续查询不存在的条目 20 次，不做保护时 MySQL 被查询 20 次；使用 30 秒空值缓存后只有第一次回源，后续 19 次命中空值。空值 TTL 需要较短，并结合参数校验、限流和不存在查询监控。

### 缓存击穿

> 我模拟 24 个并发请求同时访问失效热点 Key，朴素实现产生 24 次数据库重建。加入带过期时间和随机 Token 的互斥重建后，只有一个请求查询 MySQL，其他 23 个等待后读取缓存。锁只用于短期可重建数据，还要有等待上限和故障回源，不能替代业务事务。

### Redis 故障

> 缓存是可丢失能力，Redis 超时时可以回源 MySQL，但不能让全部流量无保护地穿透。系统需要限流、连接池、请求合并和降级，同时保证正式状态从 MySQL 恢复。Celery Broker 等非普通缓存职责还要单独评估可用性和资源隔离。

---

## 11. 练习脚本

文件：

```text
AI_Middle_Office/scripts/redis_cache_practice.py
```

执行：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m scripts.redis_cache_practice
```

每次运行使用新的 UUID Key 前缀，结束后删除本轮 Key 和 MySQL 合成表。

---

## 12. 本次没有证明什么

- 没有对正式业务数据启用缓存；
- 没有证明 Redis DB 15 是物理资源隔离；
- 没有测试 Redis 主从、哨兵或集群故障切换；
- 没有模拟删除缓存失败后的 Outbox 补偿；
- 没有测试百万 QPS 或正式容量；
- 没有证明简单 Redis 锁适合所有分布式锁场景；
- 没有验证缓存 Key 的真实租户与权限设计；
- 没有改变当前报价、成本或 Celery 配置。

---

## 13. 下一步练习

完成本次后，Redis 基础已经覆盖：

```text
Cache Aside
→ 更新后失效
→ 空值缓存
→ 热点互斥重建
→ TTL 抖动
→ Redis 故障回源
```

下一步建议进入消息队列：验证 Celery/Redis Broker 的至少一次语义、重复投递、消费者幂等、重试和死信处理。
