---
title: Agent 任务可靠性：租约、幂等、调度与状态恢复
category: 生产级开发基础
tags:
  - Agent Runtime
  - Lease
  - Idempotency
  - Scheduling
  - State Management
  - Checkpoint
  - Failure Recovery
source:
  - title: 为什么要设计任务租约机制
    url: https://www.douyin.com/video/7664255292204610533
  - title: 为什么必须做请求去重
    url: https://www.douyin.com/video/7663514331342877669
  - title: 大模型排队为何不能先进先出
    url: https://www.douyin.com/video/7662760862251615951
  - title: Agent 设计的核心是状态管理
    url: https://www.douyin.com/video/7660151673629375690
reviewed_at: 2026-07-28
status: 持续更新
---

# Agent 任务可靠性：租约、幂等、调度与状态恢复

## 核心结论

生产级 Agent 不是“模型加几个工具”，而是一套可恢复的任务系统：

```text
状态决定任务在哪里
租约决定谁有权执行
幂等决定重试会不会重复生效
调度决定有限资源先服务谁
Checkpoint 决定故障后从哪里继续
Trace 决定出错后能否解释
```

这几项必须一起设计。只有重试而没有幂等，会重复产生副作用；只有租约而没有 Checkpoint，接管后仍要从头执行；只有队列而没有分层调度，长任务会拖垮短任务。

> 资料说明：本笔记依据视频公开简介和章节摘要整理，并结合当前 AI 智能报价中台代码与运行记录扩展，不是逐字字幕。

---

## 1. 先区分四类状态

视频把 Agent 状态分成四类。工程上还要为每类状态指定所有者、事实来源和写入规则。

| 状态 | 典型内容 | 生命周期 | 权威来源 | 写入原则 |
|---|---|---|---|---|
| Session State | 当前问题、临时上下文、模型消息 | 一次会话 | 会话存储或运行上下文 | 可丢弃，不作为业务事实 |
| Task State | 阶段、进度、重试、Checkpoint、错误 | 一次任务 | 任务控制表与状态机 | 受状态迁移规则约束 |
| User State | 身份、角色、偏好、长期目标 | 跨任务 | 用户与权限系统 | 结构化、可撤销、有来源 |
| External State | 订单、报价、库存、审批结果 | 独立于 Agent | 外部业务系统 | 调用前读取，写入后复核 |

关键原则：

```text
模型消息 ≠ 任务状态
任务状态 ≠ 外部业务事实
Checkpoint ≠ 最终业务提交
用户偏好 ≠ 用户权限
```

模型可以提出状态变化，但不能仅凭自然语言自行修改权威状态。

### 状态字段的最小集合

长任务至少应保存：

```text
task_id
status
phase
attempt_count
max_attempts
worker_id
lease_token
lease_expires_at
checkpoint_id
input_version
error_code
error_message
created_at / started_at / finished_at
```

状态应描述已发生的事实，不要用一个笼统的 `processing` 覆盖排队、领取、执行、暂停、等待人工和恢复。

建议的状态机：

```text
queued
  → running
  → waiting_human
  → resume_queued
  → running
  → completed

running
  → failed
  → recovery_queued
  → running

running
  → lease_expired
  → recovering

任意可执行状态
  → cancelled / blocked_stale_input
```

---

## 2. 租约：证明“我现在有权执行”

普通锁回答“资源是否被占用”，租约还回答“占用者是否仍被允许继续执行”。

任务被 Worker 领取时写入：

```text
worker_id
lease_token
lease_expires_at
```

正常 Worker 定期续租；超过期限未续租，调度器才允许其他 Worker 接管。

### 为什么不能只看 `running`

`running` 可能代表：

- Worker 正常执行；
- Worker 进程崩溃；
- 网络已断，但进程仍在运行；
- 旧 Worker 已失去任务所有权；
- Worker 卡在模型或工具调用中；
- 状态写入失败，实际执行已经结束。

因此：

```text
任务活性 = 状态 + 租约是否有效 + Worker 心跳 + 最近进度
```

### 领取任务必须是原子操作

伪代码：

```sql
BEGIN;

SELECT *
FROM agent_runs
WHERE
  status IN ('queued', 'resume_queued')
  OR (status = 'running' AND lease_expires_at < NOW())
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;

UPDATE agent_runs
SET
  status = 'running',
  worker_id = :worker_id,
  lease_token = :new_token,
  lease_expires_at = :deadline,
  attempt_count = attempt_count + 1;

COMMIT;
```

如果“先查再改”不在同一事务中，两个 Worker 可能同时领取同一任务。

### `lease_token` 是 fencing token

任务被接管后，旧 Worker 可能迟到并尝试写回结果。所有进度、续租和最终提交都必须附带当前 token：

```sql
UPDATE agent_runs
SET status = 'completed'
WHERE task_id = :task_id
  AND status = 'running'
  AND lease_token = :lease_token;
```

受影响行数为 0，说明 Worker 已失去所有权，必须停止写入。

### 心跳、租约和 Checkpoint 不可互相替代

| 机制 | 回答的问题 |
|---|---|
| Worker Heartbeat | 这个进程是否还在线 |
| Task Lease | 这个进程是否仍有权执行这个任务 |
| Progress Event | 任务最近执行到什么阶段 |
| Checkpoint | 接管后从哪个安全点恢复 |

Worker 在线不代表某个任务仍正常推进；任务租约有效也不代表内部没有死循环。

### 续租策略

建议满足：

```text
heartbeat_interval < renew_interval < lease_ttl / 2
```

续租时同时校验：

- `task_id` 与 `lease_token` 匹配；
- 状态仍为 `running`；
- 未收到取消信号；
- 最近进度没有越过异常阈值；
- 输入或资料版本仍然有效。

租约过期不是“立即终止旧进程”的充分条件，而是“拒绝旧 token 后续写入”的条件。

---

## 3. Checkpoint：保存可安全恢复的执行边界

Checkpoint 不应只是完整消息列表，而应包含恢复所需的最小状态：

```text
已完成节点
下一节点
结构化中间结果
工具调用结果引用
输入与政策版本
已消费的人工指令
待执行副作用
```

### 保存时机

- 一个代价高的阶段完成后；
- 工具结果已经验证后；
- 进入人工审核前；
- 外部副作用执行前后；
- 长循环达到固定步数时。

### 副作用前后的安全顺序

最危险的场景是“外部系统已成功，任务状态尚未写入，Worker 随后崩溃”。

推荐：

```text
1. 持久化准备执行的动作与幂等键
2. 提交本地事务
3. 调用外部工具
4. 使用相同幂等键确认结果
5. 持久化外部回执和下一 Checkpoint
```

不能保证外部原子事务时，可采用 Outbox、Saga 或补偿操作。

---

## 4. 幂等：让同一意图只产生一次业务效果

请求去重不等于简单比较文本。

### 三个概念

| 概念 | 作用 | 能否保护写操作 |
|---|---|---|
| 精确重复检测 | 判断请求 ID 或幂等键是否相同 | 可以 |
| 语义相似检测 | 判断两段文字是否表达相似意图 | 不应直接用于写操作 |
| 缓存 | 复用已有计算结果 | 需额外校验权限、版本和时效 |

相似请求可能是用户有意重新执行，不能因为向量相似就吞掉一次正式提交。

### 幂等键如何设计

推荐由服务端定义业务边界：

```text
tenant_id
+ operation
+ business_object_id
+ input_version
+ normalized_payload_hash
```

示例：

```text
confirm_quote:{account_id}:{quote_job_id}:{preview_version}
resume_agent:{run_id}:{decision_uuid}
push_budget:{project_id}:{confirmed_quote_version}
```

### 幂等记录

```text
idempotency_key
payload_hash
status
result_reference
error_class
created_at
expires_at
```

处理规则：

1. 新 key：原子占位为 `processing`。
2. 相同 key、相同 payload、已成功：返回原结果。
3. 相同 key、不同 payload：返回冲突，不静默覆盖。
4. 相同 key、仍在处理：返回当前任务引用。
5. 可重试失败：在预算内继续，仍使用同一 key。
6. 永久失败：返回稳定错误，不重复执行。

### 幂等必须贯穿下游

入口去重只能防止应用层重复创建。如果 Worker 重投、n8n 重试或外部接口超时后实际成功，下游副作用仍可能重复。

因此需要：

```text
HTTP 幂等键
→ 任务唯一约束
→ Worker 原子领取
→ 工具调用幂等键
→ 外部回执唯一约束
```

---

## 5. 调度：为什么 FIFO 不够

大模型任务的耗时和资源消耗差异很大：

- 轻量查询可能几秒完成；
- 大 Excel、OCR 或长文档解析可能耗时数分钟；
- Agent 可能多轮调用模型和工具；
- 离线评测会消耗大量 Token，但实时性要求低。

单一 FIFO 容易产生队头阻塞：一个长任务占住执行槽，后面的短任务全部等待。

### 分层维度

| 维度 | 示例 |
|---|---|
| 任务类型 | 报价、文档解析、Agent 研判、离线评测、通知 |
| 业务优先级 | 正式业务、人工恢复、普通请求、后台批处理 |
| 资源类型 | CPU、OCR、GPU/模型、数据库、外部 API |
| 成本预算 | 预计 Token、最大工具次数、最大运行时间 |
| 等待时间 | 防止低优先级任务永久饥饿 |

实践中可采用：

```text
队列隔离 + 每类并发上限 + 加权公平 + aging + 背压
```

### 背压不是报错，而是保护系统

当以下指标超过阈值时，应减速或拒绝新任务：

- 队列长度；
- 最老任务等待时间；
- 可用 Worker 槽位；
- 模型并发或 Token 预算；
- 下游超时率；
- 数据库连接池占用。

可选动作：

```text
延迟受理
返回预计等待时间
暂停低优先级任务
降低单任务预算
切换降级模型
拒绝超过系统能力的新请求
```

### 防止任务饥饿

严格优先级会让低优先级任务永久无法运行。可按等待时间逐步提高有效优先级：

```text
effective_priority
= business_priority
+ waiting_time_weight
- estimated_cost_penalty
```

---

## 6. 故障恢复的完整闭环

```text
检测异常
→ 确认租约或进度是否失效
→ 阻止旧 Worker 写入
→ 判断错误是否可重试
→ 从最近安全 Checkpoint 恢复
→ 对所有副作用执行幂等校验
→ 验证最终业务状态
→ 记录事件、指标和复盘
```

### 错误分类

| 类型 | 示例 | 处理 |
|---|---|---|
| 瞬时错误 | 网络闪断、限流、临时 5xx | 退避重试 |
| 依赖配置错误 | 密钥错误、MCP 401、模型配置缺失 | 快速失败并告警 |
| 输入错误 | 文件损坏、Schema 不合法 | 阻断并提示用户 |
| 版本冲突 | 资料清单已更新 | 标记 stale，重新绑定 |
| 业务不确定 | 证据缺失、结论冲突 | 转人工或补资料 |
| 永久代码错误 | 未处理异常、契约不兼容 | 失败并进入修复流程 |

“再试一次”只适用于瞬时错误。鉴权失败若持续重试，只会放大积压。

---

## 7. 当前报价中台的项目映射

### 已落地

| 能力 | 项目证据 | 判断 |
|---|---|---|
| 持久化状态机 | `bid_intake_agent_runs`、assessment、event | 已具备 |
| 原子领取 | `SELECT ... FOR UPDATE SKIP LOCKED` | 已具备 |
| 任务租约 | `lease_token + lease_expires_at` | 已具备 |
| 旧 Worker 写保护 | 所有关键写回校验相同 token | 已具备 |
| 过期接管 | 过期 `running` 可被新 Worker 领取 | 已具备 |
| Worker 心跳 | 独立 heartbeat 表，记录 PID、能力与当前任务 | 已具备 |
| 状态恢复 | SQLAlchemy LangGraph Checkpointer | 已具备 |
| 人工暂停与恢复 | `waiting_human → resume_queued` | 已具备 |
| 幂等人工命令 | 人工决策与取消返回 `idempotent` | 已具备 |
| 输入版本保护 | manifest version/hash 不一致则阻断 | 已具备 |
| 执行审计 | 安全 Trace、运行事件、错误码 | 已具备 |

### 仍需补强

1. **租约续期**：当前运行租约主要在领取时设置，Worker 心跳独立更新；若单次运行可能超过租约时长，应增加绑定 `lease_token` 的运行中续租。
2. **统一幂等合同**：人工决策和取消已有幂等，但任务创建、正式报价确认、跨 n8n/Dify/外部推送仍应统一键格式、状态语义和回执。
3. **分层调度**：Celery 已有 `worker_prefetch_multiplier=1`、late ack 和超时保护，Agent 领取也按创建时间排序；但尚未形成任务类型隔离、业务优先级、Token 预算和背压联动。
4. **租约与进度联判**：Worker 在线、任务租约和图节点进度可进一步形成统一卡死检测规则。

---

## 8. 真实故障案例：401 为什么会让任务长期卡住

### 现象

报价资料研判任务长期停留在 `running/queued`，前端看不到明确失败。

### 根因链

```text
Windows 启动脚本记录的是 Python 包装进程 PID
→ 重启后遗留旧 Agent Worker
→ 新 MCP 使用新的进程内 JWT secret
→ 旧 Worker 仍携带旧 secret
→ MCP 返回 401
→ 异常发生在进入 LangGraph 之前
→ 原失败路径没有持久化 run_failed，也未释放租约
→ 前端只能看到任务一直运行
```

真正根因不是“模型卡死”，而是进程身份、鉴权版本和租约失败路径没有闭合。

### 修复

- Worker 自报真实 PID，启动器精确管理进程；
- Worker 启动时先做 MCP 鉴权会话预检；
- LangGraph 前的 MCP/模型初始化异常也写入 `run_failed`；
- 失败时释放租约，在重试预算内允许重新领取；
- 保留 Checkpoint 和事件，便于安全恢复与审计。

### 验证

受影响任务释放旧租约后由新 Worker 第 2 次接管，MCP 从 401 恢复到 200，动态图谱累计 75 个事件，最终正常进入 `waiting_human / human_review`。

### 面试价值

可以用一句话概括：

> 我们遇到的不是普通接口报错，而是旧 Worker 持有旧 MCP 凭证，且预执行异常未释放任务租约。我把进程身份、鉴权预检、失败状态持久化和租约释放连成闭环，使任务能够由新 Worker 从持久化状态安全接管。

---

## 9. 设计检查清单

### 状态

- [ ] 每个状态都有清晰含义和合法迁移
- [ ] 任务状态与外部业务状态分离
- [ ] 输入、模型、政策和资料版本可追溯
- [ ] 人工暂停不会占用运行租约

### 租约与恢复

- [ ] 领取任务是原子操作
- [ ] 所有写回都校验当前 `lease_token`
- [ ] 长任务能续租且续租失败会停止写入
- [ ] Worker 心跳、任务租约和进度分别记录
- [ ] 关键阶段有持久化 Checkpoint

### 幂等

- [ ] 幂等键由业务身份和输入版本组成
- [ ] 相同 key 不同 payload 返回冲突
- [ ] 成功重放返回原结果
- [ ] 外部副作用也使用幂等键或唯一回执
- [ ] 重试次数与错误分类可观测

### 调度

- [ ] 已测量各类任务的等待时间和资源消耗
- [ ] 长任务与低延迟任务不会相互阻塞
- [ ] 具有并发上限、背压和降级策略
- [ ] 优先级调度能防止低优先级任务饥饿

---

## 10. 面试速答

### 为什么 Agent 需要任务租约？

因为 `running` 只能说明数据库状态，不能证明 Worker 仍存活或仍有执行权。租约通过有效期和 fencing token 允许失联任务被安全接管，并阻止旧 Worker 迟到写回。

### 幂等和去重有什么区别？

去重识别重复请求，幂等保证重复执行不会产生第二次业务效果。生产系统必须把幂等延伸到任务、工具和外部副作用。

### 为什么不能只有 FIFO？

Agent 任务耗时和资源差异很大，单一 FIFO 会造成队头阻塞。应按任务类型、优先级、资源和成本预算分层，并用 aging 和背压保证公平与稳定。

### 为什么说 Agent 的核心是状态管理？

模型只负责不确定性判断；能否恢复、接管、暂停、重试、审计和安全提交，都依赖显式状态、版本、权限和迁移规则。

---

## 关联笔记

- [Agent 可靠性工程：上下文、护栏、状态、评测与追踪](./Agent可靠性工程-上下文护栏状态评测与追踪.md)
- [生产级 Agent 核心机制与工程实践](./生产级Agent核心机制与工程实践.md)
- [Agent 线上故障定位与可靠性治理](../10-LLMOps与可观测性/Agent线上故障定位与可靠性治理.md)
- [Agent 决策循环与执行架构](../06-Agent规划与工作流/Agent决策循环与执行架构.md)
