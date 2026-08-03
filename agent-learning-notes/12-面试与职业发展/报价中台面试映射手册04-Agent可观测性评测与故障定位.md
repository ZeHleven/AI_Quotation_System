---
title: 报价中台面试映射手册 04：Agent 可观测性、评测与故障定位
category: 面试与职业发展
tags:
  - FDE
  - Agent 工程
  - LLMOps
  - 可观测性
  - 评测
  - 故障定位
reviewed_at: 2026-07-29
status: 持续更新
---

# 报价中台面试映射手册 04：Agent 可观测性、评测与故障定位

## 使用方法

每道题记住这一条主线：

```text
Trace 解释单次运行
Metrics 发现总体异常
Evaluation 判断版本好坏
Alert 通知需要处理
Feedback 形成改进数据
```

## 考前 2 分钟速记

| 问题 | 记忆句 |
|---|---|
| 为什么不能只看答案 | 最终错可能来自输入、检索、Tool、模型、规则或运行时 |
| Trace 记录什么 | 节点、父子关系、Tool、Observation、耗时、版本和终止原因 |
| 怎样定位故障 | 先基础设施，再运行时，再检索/Tool，最后看模型与业务规则 |
| 怎样评测 | 检索、生成、政策和业务结果分层评测，不能只用一个总分 |
| 怎样安全发布 | 冻结版本和数据集，单变量对比，Holdout 过门后再灰度 |

---

## 先分清五种信号

| 信号 | 回答的问题 | 项目对应 |
|---|---|---|
| Log | 某个组件发生了什么 | FastAPI、Worker、MCP 日志 |
| Trace | 一次任务为什么得到这个结果 | Agent Runtime Graph |
| Metric | 一段时间内是否异常 | 耗时、成功率、Tool 次数、人工介入率 |
| Evaluation | 新版本是否优于旧版本 | 检索评测、政策校准、Prompt 回归 |
| Alert | 现在是否需要人处理 | 服务探活、卡住任务、错误事件、钉钉告警 |

> 有日志不等于可观测；能从业务问题反查到相关运行、节点、版本和证据，才叫可定位。

---

## 卡片 1：怎样追踪一次 Agent 运行？

**一句话：** 把 LangGraph 节点事件投影成安全、追加写、可还原父子关系的运行 Trace。

### 项目映射

`PersistentBidIntakeExecutor` 使用 `stream_mode="tasks"` 读取节点开始、完成、失败和中断事件，再写入：

- `trace_step_started`；
- `trace_step_completed`；
- `trace_step_failed`；
- `trace_step_waiting`。

事件使用 `bid-intake-agent-trace/v1`，核心字段包括：

- `sequence`、`step_id`、`parent_step_ids`；
- `node_name`、`kind`、`state`；
- `iteration`、`duration_ms`；
- `summary` 和脱敏后的 `details`。

### 能看见什么

- LLM 输入的安全结构：资料版本、消息数、Observation 数、模型和政策版本；
- ReAct 轮次、行动计划和停止原因；
- Tool 名称、受限输入、结果状态、命中数和证据 ID；
- Observation 是否已回写下一轮 LLM；
- 主模型或备用模型路由；
- PolicyEngine、证据门和 Human-in-the-loop 状态。

### 安全边界

- 不记录或展示模型私有思维链。
- 不保存 Tool Observation 的资料长原文。
- `secret / token / password / authorization` 字段统一脱敏。
- 字段数量、嵌套深度和文本长度都有上限。

### 30 秒回答

> 我们不是只记录“任务成功或失败”，而是把 LangGraph 的节点任务流投影成 append-only Trace。每个步骤都有父节点、状态、轮次、耗时和安全摘要，因此可以还原 LLM、Tool、Observation、证据门和人工节点之间的关系。同时不保存私有思维链和资料长原文，只保留调试所需的结构、证据 ID 和脱敏元数据。

---

## 卡片 2：Agent 结果错误时怎样分层定位？

**一句话：** 按数据流从前往后排查，先找到最早发生偏差的层，不要一开始就改 Prompt。

| 层次 | 先看什么 | 常见问题 |
|---|---|---|
| 输入与版本 | manifest、解析状态、文件哈希 | 资料缺失、旧版本、解析失败 |
| 基础设施 | DB、Redis、Worker、MCP、模型 Readiness | 服务离线、网络或凭证错误 |
| Runtime | run 状态、租约、Checkpoint、事件时间 | 卡住、旧 Worker、恢复失败 |
| Query Planning | Query 数、主题、路由原因 | 问题拆错、漏掉关键主题 |
| Retrieval | exact/semantic/hybrid、命中数、fallback | 召回失败、索引过期、排序靠后 |
| Tool | `status / retryable / error_code / trace_id` | 无结果、参数错误、依赖失败 |
| 模型输出 | model route、结构校验和修复次数 | 输出非法、备用模型接管 |
| Policy/Gate | policy 版本、硬门槛、GateIssue | 规则不匹配、证据不足 |
| Human | decision UUID、报告和 manifest 版本 | 重复提交、旧页面误审批 |

### 排查顺序

```text
任务有没有真正开始
→ 卡在哪个节点
→ 该节点输入是否正确
→ Tool/模型返回了什么状态
→ 后续规则是否正确消费
```

真实故障中，任务长时间 `running` 曾由“旧 Worker 使用旧 MCP 凭证，预执行失败又未释放租约”导致，而不是模型推理慢。分层检查 Worker、MCP 401、事件缺失和租约后才定位到根因。

### 30 秒回答

> Agent 出错时我不会先调 Prompt，而是找最早发生偏差的层。先确认 Worker、MCP 和模型是否就绪，再看运行卡在哪个 LangGraph 节点，然后检查 Query、检索路由、Tool Envelope、模型结构化输出、Policy 和证据门。这样可以区分基础设施故障、检索质量问题和模型判断问题，避免用 Prompt 掩盖系统错误。

---

## 卡片 3：怎样建立分层 Agent 评测体系？

**一句话：** 每一层使用自己的金标和指标，不能用最终答案分数代替检索、规则和业务效果。

| 评测层 | 金标来源 | 核心指标 | 当前状态 |
|---|---|---|---|
| 检索 | 人工 Gold Evidence | Recall@K、MRR、nDCG、路由准确率、P95 | 框架完成；真实历史基线待建 |
| 证据与生成 | 人工核验结论—证据关系 | 支持率、缺证据率、拒答准确率 | 证据门已实现；端到端金标待补 |
| Policy | 总经办独立判断或实际项目结果 | 一致率、危险报价数、硬红线召回率 | 校准闭环完成；真实历史金标待录入 |
| 报价 Prompt | 人工确认、打回和修改记录 | 金额偏差、缺项率、格式错误率、打回率 | 已有回归用例与运行报告 |
| 线上业务 | 真实任务和人工动作 | 完成率、人工介入率、采纳率、时延、成本 | 部分数据已有，Agent 聚合 SLO 待补 |

### 评测原则

- Agent 原结论是被评测对象，不能同时当标准答案。
- 标注人与复核人必须分离。
- Development 用于调参，Holdout 用于一次性泛化验证。
- 同一项目不能跨 Development/Holdout，防止信息泄漏。
- 不只看平均分，还要保留关键样本回归清单。

### 政策发布门

当前政策校准设计要求：

- 总样本至少 30，Holdout 至少 10；
- 危险报价不能增加；
- Holdout 硬红线召回率 100%；
- Holdout 一致率不低于基线且不低于 80%；
- 通过也只生成发布建议，不自动切换 active 政策。

当前环境曾用 30 条临时验收样本走通双人复核、冻结、候选和盲测，验收后已全部删除；这不能冒充真实历史业务成绩。

### 30 秒回答

> 我把 Agent 评测拆成检索、证据生成、政策决策和线上业务四层。检索看 Recall、MRR 和路由，政策用独立总经办金标看一致率、危险报价和硬红线召回，线上再看人工介入、采纳率、延迟和成本。Development 与 Holdout 按项目隔离，候选版本即使过门也不自动发布。当前框架完整，但真实历史金标基线仍是下一步。

---

## 卡片 4：Prompt、模型或规则变更怎样安全发布？

**一句话：** 先冻结输入、组件和数据集版本，再做单变量回归；没有可复现基线，就不能证明修改有效。

### 需要记录的版本

- graph：`bid_intake_graph_v3`；
- state schema：`bid_intake_state_v3`；
- prompt：`bid_intake_prompt_v3`；
- Tool schema；
- 模型 ID 与 primary/fallback 路由；
- Policy 版本；
- manifest 版本与哈希；
- 检索索引 schema；
- 评测数据集指纹。

运行记录中的 `versions_json` 保存 Agent 组件版本；任务还固定绑定 Policy 和 manifest，恢复过程中不能悄悄换版本。

### 发布流程

```text
固定基线
→ 一次只改一个主要变量
→ Development 分析与调参
→ Holdout 一次验证
→ 检查总体指标与关键回归
→ 人工批准
→ 小范围灰度
→ 监控并保留回滚版本
```

### 关键取舍

- 模型 fallback 解决“主模型不可用”，不证明备用模型质量相同。
- 只有数据集指纹相同的报告才能 A/B 对比。
- Policy 候选与冻结数据集绑定，当前没有自动激活接口。
- Prompt 回归可从人工确认和修正记录生成用例，但仍要防止把脏反馈直接当金标。

### 30 秒回答

> 每次运行都记录图、状态、Prompt、Tool、模型、Policy 和资料版本。做优化时固定数据集指纹，一次只改变一个变量，在 Development 上调试，最后用按项目隔离的 Holdout 验证。发布门检查总体指标和危险样本回归，通过后仍需人工批准和灰度。模型自动切换只是可用性措施，不能替代质量评测。

---

## 卡片 5：线上应该监控什么，当前还缺什么？

**一句话：** 线上监控要同时覆盖可用性、质量、效率、安全和业务结果。

### 当前已有

- Worker 默认每 10 秒心跳，Readiness 只认可 30 秒窗口内的健康 Worker。
- Readiness 检查 Runtime、manifest、可用证据、Policy、Worker、MCP、模型及版本一致性。
- 平台运维探测 MySQL、Redis、Celery、RAG、n8n、MinIO 的状态和延迟。
- 可发现卡住的报价任务、当前错误事件并发送钉钉告警。
- Agent 单次 Trace 已有节点耗时、循环数、Tool 数、模型切换、Gate 和人工状态。

### 建议补齐的 Agent 聚合指标

| 类型 | 指标 |
|---|---|
| 可用性 | 启动成功率、完成率、失败率、恢复率 |
| 效率 | 端到端 P50/P95、各节点耗时、ReAct 轮数、Tool 次数 |
| 模型 | primary/fallback 比例、超时率、Token 和单次成本 |
| Tool | 成功、无结果、可重试失败、重复调用和预算耗尽率 |
| 质量 | Gate 通过率、修复率、补资料率、关键样本回归 |
| 业务 | 人工介入率、批准/驳回/补资料分布、建议采纳率 |

### 当前真实缺口

- 模型响应已携带 `usage`，但尚未形成 Agent 每次运行的 Token/成本聚合。
- 尚无资料研判 Agent 专属的成功率、P95、fallback、Gate 和人工介入看板。
- 平台已有通用运维告警，但 Agent 专属 SLO 和聚合告警仍待补齐。
- 真实历史检索 Gold 集和真实政策校准数据集尚未建立。

### 30 秒回答

> 当前系统已经能解释一次 Agent 运行，并能通过心跳和 Readiness 发现服务不可用。但完整线上治理还需要聚合成功率、P95、模型 fallback、Tool 失败、Gate 修复、人工介入以及 Token 成本，并为这些指标设置 SLO 和告警。我会明确区分“单次 Trace 已完成”和“Agent 聚合监控仍待补齐”。

---

## 高频追问

| 追问 | 回答 |
|---|---|
| Trace 和 Log 的区别 | Log 以组件为中心；Trace 以一次业务任务为中心 |
| Trace 和 Checkpoint 的区别 | Trace 用于解释；Checkpoint 用于恢复 |
| 为什么不记录思维链 | 风险高且不是可靠审计依据，应记录输入结构、动作和结果 |
| 离线指标好就能上线吗 | 不能，还要看灰度时延、失败、成本和业务反馈 |
| 为什么不能只看平均分 | 平均分可能掩盖资质、截止时间等关键样本退化 |
| fallback 成功是否代表无问题 | 只代表可用性恢复，必须单独观察质量、延迟和成本 |

记忆口诀：

> 日志看组件，Trace 看单次，指标看趋势，评测看版本，告警促行动。

---

## 当前项目结论

| 能力 | 状态 |
|---|---|
| Agent 单次运行图谱 | 已实现 |
| 节点、Tool、Observation 与耗时追踪 | 已实现 |
| 敏感字段与资料正文保护 | 已实现 |
| 检索离线评测框架 | 已实现，真实金标待建 |
| Policy 双人复核、冻结与发布门 | 已实现，真实历史数据待录入 |
| 报价 Prompt 回归 | 已实现 |
| 平台服务探活与通用告警 | 已实现 |
| Agent 聚合 SLO、Token/成本看板 | 待补 |
| Agent 端到端真实业务基线 | 待补 |

面试表达：

> 当前系统已经从“黑盒运行”进入“单次过程可解释、组件版本可追溯、离线评测可复现”的阶段；下一步重点不是继续增加日志，而是建立真实历史金标和 Agent 聚合 SLO，把 Trace、Metrics、Evaluation、Alert 与人工反馈真正闭环。

## 代码证据

- `AI_Middle_Office/app/agents/bid_intake/execution_trace.py`
- `AI_Middle_Office/app/agents/bid_intake/persistent_executor.py`
- `AI_Middle_Office/app/agents/bid_intake/openai_compatible_model.py`
- `AI_Middle_Office/app/agents/bid_intake/retrieval_evaluation.py`
- `AI_Middle_Office/app/agents/bid_intake/calibration.py`
- `AI_Middle_Office/app/models/bid_intake_runtime.py`
- `AI_Middle_Office/app/services/bid_intake_runtime.py`
- `AI_Middle_Office/app/services/bid_policy_calibration.py`
- `AI_Middle_Office/app/services/prompt_regression.py`
- `AI_Middle_Office/app/services/ops_monitor.py`
- `AI_Middle_Office/tests/test_bid_intake_execution_trace.py`
- `AI_Middle_Office/tests/test_bid_intake_retrieval_evaluation.py`
- `AI_Middle_Office/tests/test_prompt_regression.py`
- `AI_Middle_Office/tests/test_ops_monitor.py`

## 深入阅读

- [LLMOps：大模型与 Agent 的持续评测、发布和运营](../10-LLMOps与可观测性/LLMOps全生命周期管理.md)
- [Agent 线上故障定位与可靠性治理](../10-LLMOps与可观测性/Agent线上故障定位与可靠性治理.md)
- [Agent 评测体系：事实、过程、工具、效率、安全与版本治理](../10-LLMOps与可观测性/Agent评测体系-事实过程工具效率安全与版本治理.md)
- [报价中台面试映射手册 01：异步任务可靠性](./报价中台面试映射手册01-异步任务可靠性.md)
- [报价中台面试映射手册 02：RAG 检索路由与证据链](./报价中台面试映射手册02-RAG检索路由与证据链.md)
- [报价中台面试映射手册 03：Agent 编排、MCP 与人机协作](./报价中台面试映射手册03-Agent编排MCP与人机协作.md)
