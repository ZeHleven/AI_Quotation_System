---
title: Redis 缓存设计：一致性、穿透、击穿、雪崩与热点治理
category: 后端基础设施
tags:
  - Redis
  - Cache
  - Cache Aside
  - Consistency
  - Hot Key
  - Celery
reviewed_at: 2026-07-30
status: 持续更新
---

# Redis 缓存设计：一致性、穿透、击穿、雪崩与热点治理

> 配套实战：[练习 03：Redis Cache Aside、一致性、穿透、热点重建与降级实战](./练习记录/03-RedisCacheAside一致性穿透热点重建与降级实战.md)

## 核心结论

Redis 的核心价值是低延迟的临时能力，不是替代业务事实库：

```text
MySQL 管事实
Redis 管速度、短期状态和协调
```

缓存设计必须同时回答：

1. 缓存什么；
2. Key 如何隔离用户、租户和版本；
3. 数据何时失效；
4. 数据库更新后怎样处理缓存；
5. Redis 故障时系统怎样运行；
6. 如何监控命中率、热点、大 Key 和内存。

---

## 1. Redis 常用数据结构

| 结构 | 适合场景 | 不适合的误用 |
|---|---|---|
| String | 单值缓存、计数器、分布式令牌 | 保存无限增长的大 JSON |
| Hash | 对象字段、小型配置 | 把整个数据库表塞入一个 Hash |
| Set | 去重、成员关系 | 需要排序和分数的场景 |
| ZSet | 排行榜、延迟调度、优先级 | 强事务业务账本 |
| List | 简单队列、时间序列 | 需要复杂消费确认的可靠消息系统 |
| Stream | 消息流、消费组 | 不了解确认和裁剪就长期堆积 |
| Bitmap/HyperLogLog | 状态位、近似去重统计 | 精确业务金额统计 |

数据结构选择应由操作模式决定，而不是因为 Redis “快”。

---

## 2. Cache Aside

### 读取

```text
读取缓存
→ 命中：返回
→ 未命中：查询 MySQL
→ 写入带 TTL 的缓存
→ 返回
```

Python 伪代码：

```python
def get_cost_item(item_id: int):
    key = f"cost-item:v2:{item_id}"
    cached = redis.get(key)
    if cached is not None:
        return decode(cached)

    item = db.get(CostItem, item_id)
    if item is None:
        redis.setex(key, 30, "__NULL__")
        return None

    redis.setex(key, 300 + random.randint(0, 60), encode(item))
    return item
```

### 更新

常见方式：

```text
先更新数据库并提交
→ 再删除缓存
```

为什么通常选择删除而不是直接更新缓存：

- 缓存结构可能由多表计算；
- 低频读取的数据不必立即重建；
- 并发写入时直接更新更容易覆盖新值；
- 下次读取可以从事实库重建。

删除失败仍会造成旧值，所以需要：

- 有界 TTL；
- 删除重试；
- 事件/Outbox；
- 版本化 Key；
- 对强一致操作绕过缓存。

---

## 3. 一致性不是“永远相同”

先明确业务允许的窗口：

| 数据 | 一致性要求 | 建议 |
|---|---|---|
| 权限、账号状态 | 很高 | 短 TTL、版本号、变更主动失效 |
| 正式成本 active 状态 | 高 | MySQL 为准，写后失效，关键操作回源 |
| 报价列表摘要 | 中等 | 秒级缓存可接受 |
| 模型结果 | 取决于输入版本 | Key 必须包含模型、Prompt、资料版本 |
| 公共配置 | 中等 | 版本化缓存和发布失效 |
| 统计看板 | 可最终一致 | 定时聚合或短期缓存 |

### 缓存 Key 必须包含语义

不安全：

```text
rag:answer:{question_hash}
```

更完整：

```text
rag:answer:
  {tenant_id}:
  {knowledge_version}:
  {permission_scope_hash}:
  {model_version}:
  {question_hash}
```

否则可能发生跨租户、跨权限或旧知识版本污染。

---

## 4. 穿透、击穿与雪崩

### 缓存穿透

大量请求查询根本不存在的数据，每次都访问数据库。

治理：

- 参数校验；
- 短时间缓存空结果；
- 布隆过滤器；
- 防爬、限流；
- 监控不存在 Key 比例。

空值 TTL 要短，避免数据后来创建后仍长期读不到。

### 缓存击穿

单个热点 Key 过期，大量并发同时回源。

治理：

- 互斥重建；
- Singleflight 请求合并；
- 逻辑过期，后台刷新；
- 热点预热；
- 热点 Key 更长 TTL。

必须设置重建超时，不能让所有请求无限等待锁。

### 缓存雪崩

大量 Key 同时失效或 Redis 整体不可用。

治理：

- TTL 加随机抖动；
- 分批预热；
- Redis 高可用；
- 数据库限流和连接保护；
- 本地小缓存；
- 降级结果；
- 演练 Redis 故障。

---

## 5. 热点 Key 与大 Key

### 热点 Key

风险：

- 单分片 CPU/网络成为瓶颈；
- Key 失效时集中回源；
- 故障迁移后瞬间过载。

治理：

- 本地缓存；
- 复制或拆分 Key；
- 请求合并；
- 热点识别和预热；
- 对热点单独限流。

### 大 Key

风险：

- 网络传输时间长；
- 删除或过期阻塞；
- 迁移和持久化压力大；
- 单次命令占用事件循环。

治理：

- 拆分对象；
- 分页读取；
- 限制集合成员；
- 异步删除；
- 避免缓存大文件、Base64 和完整长对话。

---

## 6. TTL 与淘汰

TTL 不是随便填一个数字。需要考虑：

```text
数据变化频率
允许陈旧窗口
重建成本
访问热度
故障时数据库承载能力
```

常见策略：

- 固定 TTL + 随机抖动；
- 热数据主动刷新；
- 版本 Key 自然淘汰旧数据；
- 空值使用更短 TTL；
- 安全敏感数据不依赖长 TTL。

淘汰发生时，Redis 可能在达到内存上限后删除 Key。业务不能假设缓存永久存在。

---

## 7. Redis 作为队列和限流存储

当前报价中台中 Redis 不只可用于缓存：

- Celery Broker：`CELERY_BROKER_URL` 默认指向 Redis DB 0；
- Celery Result Backend：默认指向 Redis DB 1；
- SlowAPI：Celery 模式下复用 Redis 存储限流计数；
- 队列健康检查：使用 `PING` 检查 Broker，并继续检查 Worker。

### 必须区分三种数据

| 数据 | 是否可丢 | 示例 |
|---|---|---|
| 缓存 | 通常可重建 | 查询结果 |
| 队列消息 | 不能静默丢失 | 报价任务通知 |
| 业务事实 | 不可依赖 Redis 单独保存 | 报价终态、成本状态 |

同一个 Redis 实例承载多种职责会互相影响。生产设计要考虑：

- 不同 DB 只提供逻辑隔离，不是资源隔离；
- 大量缓存淘汰可能影响队列；
- 阻塞命令会影响限流和 Broker；
- 应按风险决定是否拆实例或设置资源边界。

---

## 8. 缓存不适合什么

- 作为唯一订单、报价或成本账本；
- 保存无法重建的审批结果；
- 用超长 TTL 掩盖慢 SQL；
- 缓存没有权限范围的 RAG 答案；
- 把模型错误结果长期缓存；
- 不设大小限制地缓存文件和对话；
- 把分布式锁当成数据库事务替代品。

---

## 9. 报价中台设计练习

### 成本条目缓存

事实源：

```text
MySQL cost_items.active
```

可设计：

```text
Key:
cost-item:{tenant}:{item_id}:{updated_at_version}

读取:
Redis → miss → MySQL active 条目 → set TTL

状态变更:
MySQL 事务提交 → 删除相关 Key → 发送失效事件
```

关键操作如报价确认仍应保存成本条目快照，不能只引用可能变化的缓存。

### RAG 结果缓存

只有在以下维度都进入 Key 时才较安全：

- 租户；
- 用户权限范围；
- 知识库版本；
- Query 规范化版本；
- 检索策略版本；
- 模型与 Prompt 版本。

不满足时宁可先不缓存。

---

## 10. 监控指标

至少观察：

```text
命中率
GET/SET 延迟 P95/P99
内存使用率
evicted_keys
expired_keys
connected_clients
blocked_clients
热点 Key
大 Key
缓存重建耗时
回源 MySQL QPS
Broker 队列长度
```

缓存命中率高不等于设计正确：命中了错误、过期或越权数据更危险。

---

## 11. 面试回答

### 怎样保证数据库和缓存一致？

> 我先根据数据定义可接受的陈旧窗口。普通查询采用 Cache Aside：更新 MySQL 事务提交后删除缓存，失败则通过有限重试或 Outbox 补偿，并设置有界 TTL。权限、正式状态等高风险操作会回源数据库或使用版本号校验。缓存 Key 还包含租户和数据版本，避免跨权限和旧版本污染。这里追求的是明确的一致性边界，不会声称数据库和缓存能无成本地实时强一致。

### 穿透、击穿、雪崩有什么区别？

> 穿透是请求不存在数据导致持续回源；击穿是单个热点 Key 失效后并发回源；雪崩是大量 Key 同时失效或 Redis 故障。对应治理分别是参数校验与空值/布隆过滤、互斥重建或请求合并、TTL 抖动与数据库保护和降级。

### Redis 为什么不能代替 MySQL？

> Redis 更适合低延迟临时状态、计数和协调，但业务事实需要约束、事务、复杂查询、审计和稳定持久化。报价、成本和审批以 MySQL 为事实源，Redis 丢失后应能从事实数据重建。

---

## 12. 动手练习

1. 为 `cost_items.active` 设计 Cache Aside；
2. 制造 100 个并发请求访问同一失效 Key；
3. 分别测试无保护、互斥重建和 Singleflight；
4. 模拟删除缓存失败，验证 TTL 和补偿；
5. 模拟 Redis 不可用，确认接口是回源、限流还是降级；
6. 输出命中率、数据库 QPS 和 P95 对比。

---

## 13. 掌握检查

- [ ] 能根据操作选择 Redis 数据结构；
- [ ] 能实现 Cache Aside；
- [ ] 能定义可接受的一致性窗口；
- [ ] 能处理穿透、击穿和雪崩；
- [ ] 能识别热点 Key 和大 Key；
- [ ] 能设计带租户、权限和版本的缓存 Key；
- [ ] 能区分缓存、队列消息与业务事实；
- [ ] 能解释 Redis 故障时的降级方式；
- [ ] 能用指标验证缓存价值；
- [ ] 不会用缓存掩盖未解决的数据库问题。

---

## 代码证据

- `AI_Middle_Office/app/core/config.py`
- `AI_Middle_Office/app/core/rate_limit.py`
- `AI_Middle_Office/app/tasks/celery_app.py`
- `AI_Middle_Office/app/services/queue_health.py`
- `AI_Middle_Office/app/services/ops_monitor.py`
