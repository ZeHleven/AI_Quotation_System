---
title: 报价中台面试映射手册 03：Agent 编排、MCP 与人机协作
category: 面试与职业发展
tags:
  - FDE
  - Agent 工程
  - LangGraph
  - MCP
  - Tool Calling
  - Human-in-the-loop
reviewed_at: 2026-07-29
status: 持续更新
---

# 报价中台面试映射手册 03：Agent 编排、MCP 与人机协作

## 使用方法

每道题只记一条主线：

```text
动态分析交给 Agent
确定性控制交给 Workflow、Policy、证据门和人工
```

先讲 30 秒版本；面试官追问后，再补状态机、工具预算、权限作用域、Checkpoint 和审批恢复。

## 考前 2 分钟速记

| 问题 | 记忆句 |
|---|---|
| Agent 与 Workflow 区别 | Workflow 控制边界，Agent 动态决定下一步查什么 |
| LangGraph 怎样编排 | State 保存过程，条件边控制循环，Checkpoint 支持恢复 |
| 怎样控制 Tool | 结构化参数 + 白名单 + 预算 + 去重 + 错误语义 |
| MCP 怎样防越权 | 项目范围写进短期 Token，不让模型传 `case_id` |
| 怎样人工协作 | `interrupt` 暂停，决策先落库，再用 `Command(resume)` 恢复 |

---

## 一张图记住整体架构

```text
FastAPI 控制面：创建任务、查状态、提交人工决定
        ↓
独立 Agent Worker
        ↓
LangGraph 确定性状态机
        ↓
ReAct：模型动态选择只读 Tool
        ↓
MCP：按项目和能力隔离证据
        ↓
AssessmentDraft
        ↓
PolicyEngine → 证据门
        ↓
interrupt → 人工审核 → Checkpoint 恢复
```

核心原则：

> 模型负责发现、归纳和提出建议；系统负责权限、预算、规则、证据有效性和最终操作。

---

## 卡片 1：为什么采用“Workflow + Agent”混合架构？

**一句话：** 资料检索和分析路径具有不确定性，适合 Agent；权限、评分、审批和状态流转必须确定，适合 Workflow。

### 项目映射

Agent 可以动态决定：

- 先搜索什么问题；
- 读取哪条证据的上下文；
- 是否比较文件版本；
- 是否查询某项立项政策。

Agent 不能决定：

- 读取哪个项目；
- 是否绕过工具白名单和调用预算；
- 怎样计算政策分数和硬门槛；
- 证据是否有效；
- 是否直接批准项目。

### 三层职责

| 层次 | 职责 |
|---|---|
| ReAct Agent | 根据当前信息缺口选择下一步 Tool |
| LangGraph / Policy / Gate | 控制循环、预算、规则、证据和异常路径 |
| Human | 对业务风险和最终立项动作负责 |

### 关键取舍

- 纯 Workflow 路径固定，难以适配不同招标文件的信息分布。
- 纯 Agent 灵活，但容易越权、循环失控或把建议当成决策。
- 混合架构增加了状态机设计成本，却获得可控性、可恢复性和审计能力。

### 30 秒回答

> 我没有把整个研判流程都交给模型。不同招标文件的信息位置和缺口不一样，所以证据搜索、上下文读取和版本比较采用 ReAct 动态决策；但工具授权、政策评分、证据校验、状态流转和最终审批都由确定性组件控制。这样保留 Agent 的适应能力，同时避免模型越权或把概率性判断直接变成业务动作。

---

## 卡片 2：LangGraph 怎样组织 Agent 的决策循环？

**一句话：** 用显式 StateGraph 管理 ReAct 循环、异常修复、政策评估、证据门和人工暂停，不使用不可拆解的黑盒 Agent。

### 主流程

```text
prepare
→ react_model
→ authorize_tools
→ tool_executor
→ react_model
→ finalize_draft
→ evaluate_policy
→ evidence_gate
→ repair_prompt / human_review
```

关键分支：

- 模型返回 Tool Call：先进入 `authorize_tools`，通过后才执行。
- 模型返回结构化结果：进入 `finalize_draft`，用 Pydantic 校验。
- 输出不符合契约：最多修复一次。
- 证据问题可修复：回到 ReAct 修复，最多一次。
- 调用预算耗尽：停止继续调用 Tool，使用已有 Observation 强制形成保守草稿。
- 到达人工节点：`interrupt()` 暂停。

### State 保存什么

- 资料 manifest 与分析目标；
- 消息和 Tool Observation；
- 研判草稿、政策结果和证据门结果；
- ReAct 循环数、Tool 调用数和重复调用签名；
- 修复次数、终止原因和错误；
- 模型、Prompt、Policy 等版本。

### 两层持久化

| 持久化层 | 作用 |
|---|---|
| 业务控制表 | 页面查询任务、状态、事件、报告和人工决定 |
| LangGraph Checkpoint | 保存图状态、节点写入、interrupt 和 resume 数据 |

业务页面不解析 Checkpoint；Checkpoint 也不能替代业务审计表。

### 30 秒回答

> 我使用显式 LangGraph StateGraph，而不是直接套高层预制 Agent。ReAct 只是一段受控循环，Tool 调用前有授权节点，模型输出后有结构校验，之后再经过确定性 PolicyEngine 和证据门。状态按节点写入 SQL Checkpoint，因此 Worker 或进程重启后可以从最近节点继续，而不需要重新执行已经完成的模型和 Tool 调用。

---

## 卡片 3：怎样设计和控制 Agent Tool？

**一句话：** Tool 不只是一个函数，还必须有明确输入、输出、权限、副作用、预算和失败语义。

### 当前四个模型可见 Tool

| Tool | 作用 |
|---|---|
| `search_tender_evidence` | 在当前项目内搜索证据 |
| `read_evidence_context` | 读取证据相邻上下文并记录服务端轨迹 |
| `compare_document_versions` | 比较同一逻辑文档的版本 |
| `get_bid_policy_rule` | 读取当前任务绑定的政策规则 |

招标证据 Tool 全部只读，不提供“批准、写报价、改资料”等高风险写操作。

### 输入与输出边界

- 使用 `StructuredTool` 暴露结构化参数。
- `top_k` 被限制在安全范围内。
- 上下文前后块数量有上限。
- MCP 返回统一 Envelope：
  - `status`：`ok / no_result / partial / failed`；
  - `retryable`；
  - `error_code`；
  - `trace_id`；
  - 安全错误信息。

`no_result` 是正常业务结果，不应与网络失败混为一谈。

### 运行时护栏

- Tool 名称白名单。
- 默认最多 8 轮推理、24 次 Tool、每轮绝对上限 3 次。
- 相同 Tool 与相同参数默认最多执行 2 次。
- 自适应节奏：
  - 首轮宽检索最多 1 次；
  - 证据读取最多 2 次；
  - 缺口补查最多 1 次。
- Runtime 只裁剪模型已经提出的调用，不替模型编造 Tool Call。

### 关键取舍

- 预算过小会影响召回，预算过大则增加延迟、费用和循环风险。
- 当前先用确定性阶段预算控制成本；是否扩大预算应由真实评测决定。
- Tool 错误由结构化状态传递，不能让模型靠解析异常字符串猜测。

### 30 秒回答

> 我把 Tool 当成正式接口设计，而不是随便包装一个 Python 函数。每个 Tool 都有结构化参数、范围限制和统一结果状态，并且在执行前经过白名单、总调用预算、每轮预算和重复参数检查。首轮只允许一次主检索，之后优先读取关键证据，存在明确缺口才补查。预算耗尽时系统停止调用并生成保守草稿，而不是让模型无限循环。

---

## 卡片 4：MCP 怎样解决权限隔离和提示注入风险？

**一句话：** 模型只能决定“查什么”，不能决定“查哪个项目”；项目范围由模型不可见的短期服务 Token 绑定。

### Token 作用域

每次 Agent Run 签发短期内部 Token，包含：

- `case_id`；
- `assessment_id`；
- `agent_run_id`；
- `subject`；
- `allowed_tools`；
- `issued_at / expires_at`；
- `issuer / audience`。

默认有效期 5 分钟，最长不超过 1 小时。

### 为什么 Tool 参数中没有 `case_id`

如果 `case_id` 由模型传入，恶意文档可能通过提示注入诱导模型查询其他项目。当前设计中：

```text
模型参数：query、evidence_id、document_key
项目范围：从服务 Token 读取
```

即使模型被诱导，也无法改变项目、任务和运行范围。

### 其他安全边界

- 用户登录 Token 不传给 MCP，MCP 使用专用服务身份。
- 每个能力都检查 `allowed_tools`。
- Token 校验 issuer、audience、有效期和 subject。
- MCP Tool 标记为只读、非破坏、幂等和封闭世界。
- Token 不进入模型可见参数，不写业务日志和数据库。
- 检索命中不代表已读取；只有 `read_evidence_context` 才写入本次 Run 的读取轨迹。

### 当前边界

- 当前内网启动使用进程级随机 HS256 Secret。
- 企业级身份体系可替换为 OIDC/JWKS，但无需修改 Tool 契约。
- MCP 解决协议和服务作用域，不替代数据库行级校验、证据门和业务 RBAC。

### 30 秒回答

> MCP Tool 中没有 case ID，模型只传查询内容或证据 ID。Worker 为每次 Run 签发短期服务 Token，把项目、assessment、run 和能力白名单绑定在 Token 里，MCP 服务端从认证上下文取得范围。这样即使招标文档含有提示注入，模型也无法通过修改参数读取其他项目。用户 Token、服务 Token、数据库权限和证据校验仍然分层处理。

---

## 卡片 5：Human-in-the-loop 怎样暂停和恢复？

**一句话：** 人工审核不是弹一个确认框，而是可持久化、可幂等、可校验版本的暂停与恢复协议。

### 状态流转

```text
queued
→ running
→ waiting_human
→ resume_queued
→ running
→ completed
```

异常恢复：

```text
Worker 失联 → 租约过期 → 新 Worker 领取 → 从 Checkpoint 继续
资料更新   → manifest 不一致 → blocked_stale_manifest
```

### 暂停

- `human_review` 节点调用 `interrupt(review_payload)`。
- Checkpoint 保存暂停位置。
- 业务表把任务置为 `waiting_human`。
- 页面展示研判草稿、PolicyEngine 结果、证据门问题和允许动作。

### 人工命令

人工可以：

- 批准；
- 有条件批准；
- 驳回；
- 要求补资料；
- 要求重新研判。

请求必须携带：

- `decision_uuid`：幂等键；
- `report_version`；
- `manifest_version`；
- 操作、备注和条件。

相同 UUID、相同内容返回原命令；相同 UUID、不同内容返回冲突。

### 恢复与双重校验

1. API 先检查任务确实处于 `waiting_human`。
2. 校验报告版本和当前 active manifest。
3. 政策或证据硬阻断存在时，普通批准被 API 拒绝。
4. 人工决定先持久化，任务进入 `resume_queued`。
5. Worker 领取后执行 `Command(resume=decision)`。
6. LangGraph 再次校验版本和批准动作，防止绕过控制面。

### 真实闭环证据

首次真实运行态验收记录：

- ReAct 5 轮、Tool 10 次；
- 10 个研判维度、11 个政策因素；
- Agent 和 PolicyEngine 均建议补资料；
- 证据门为 `supplement_required`；
- 人工选择“要求补资料”，命令成功应用；
- 从 SQL Checkpoint 恢复，最终进入 `waiting_supplement`。

后续自适应 Tool 预算、运行图谱与相关联合回归为 `67 passed`。

### 30 秒回答

> 人工审核在 LangGraph 中是一个真正的 interrupt。图状态和暂停位置写入 SQL Checkpoint，人工决定则先以带 UUID 的幂等命令落入业务表。系统校验报告版本、manifest 版本和证据硬门槛后，再由 Worker 使用 Command resume 恢复原线程。API 和 LangGraph 都会验证批准动作，因此换资料、重复点击或绕过前端都不能误批。

---

## PolicyEngine：模型建议为什么不能直接成为业务结论？

```text
Agent：提取因素档位、来源和证据
PolicyEngine：计算权重、覆盖率、硬门槛和政策建议
证据门：验证结论是否有有效证据
Human：做最终业务决定
```

- 当前任务绑定不可变政策版本，恢复时不能悄悄换规则。
- `unknown` 不允许模型猜测，按 0 分并单独降低信息覆盖率。
- 合规、资质、预计亏损、现金流和履约等 critical 因素触发硬门槛。
- 政策建议稿仍需使用真实历史项目做总经办校准，不能把当前权重说成最终生产标准。

面试表达：

> Prompt 负责指导模型输出什么；PolicyEngine 决定企业规则怎么算。规则需要版本化、回放和审计，所以不能只写在 Prompt 里。

---

## 高频追问：六个概念不要混

| 概念 | 回答 |
|---|---|
| Workflow | 控制确定性节点、状态和异常路径 |
| Agent | 根据 Observation 动态决定下一步行动 |
| Tool | 模型可请求的一项结构化能力 |
| MCP | Tool 的协议、服务发现、认证和调用边界 |
| Checkpoint | 当前执行线程的恢复点，不是长期记忆 |
| Human-in-the-loop | 带状态、版本和幂等语义的人工决策协议 |

记忆口诀：

> Workflow 管路线，Agent 选动作，Tool 干具体事，MCP 管边界，Checkpoint 管恢复，Human 管责任。

---

## 当前项目边界

| 能力 | 当前状态 |
|---|---|
| 显式 LangGraph 状态机 | 已实现 |
| 受控 ReAct 与 Tool 授权 | 已实现 |
| 项目级 MCP 短期 Token | 已实现 |
| SQL Checkpoint 与跨 Worker 恢复 | 已实现 |
| 人工命令幂等与版本校验 | 已实现 |
| PolicyEngine 与证据门 | 已实现 |
| MCP 写操作 Tool | 未开放，当前证据 Tool 全部只读 |
| 企业 OIDC/JWKS | 未接入，当前为内网进程级 HS256 |
| 正式政策历史校准 | 待使用真实项目完成 |
| 长期 Agent Memory | 未实现；Checkpoint 只保存执行状态 |

项目总结：

> 这套 Agent 的价值不是“能自动调用工具”，而是把模型的不确定性限制在证据发现和解释环节，把项目权限、工具预算、企业规则、证据有效性、状态恢复和最终责任放在确定性系统里。

## 代码证据

- `AI_Middle_Office/app/agents/bid_intake/graph.py`
- `AI_Middle_Office/app/agents/bid_intake/state.py`
- `AI_Middle_Office/app/agents/bid_intake/tools.py`
- `AI_Middle_Office/app/agents/bid_intake/ports.py`
- `AI_Middle_Office/app/agents/bid_intake/contracts.py`
- `AI_Middle_Office/app/agents/bid_intake/evidence_gate.py`
- `AI_Middle_Office/app/agents/bid_intake/persistent_executor.py`
- `AI_Middle_Office/app/agents/bid_intake/sqlalchemy_checkpointer.py`
- `AI_Middle_Office/mcp_servers/tender_evidence/auth.py`
- `AI_Middle_Office/mcp_servers/tender_evidence/server.py`
- `AI_Middle_Office/mcp_servers/tender_evidence/service.py`
- `AI_Middle_Office/app/services/bid_intake_runtime.py`
- `AI_Middle_Office/app/api/v1/bid_intake_runtime.py`
- `AI_Middle_Office/agent_tests/bid_intake_checks.py`
- `AI_Middle_Office/agent_tests/tender_evidence_mcp_checks.py`
- `AI_Middle_Office/tests/test_bid_intake_runtime_phase4a.py`

## 深入阅读

- [Agent 决策循环与执行架构](../06-Agent规划与工作流/Agent决策循环与执行架构.md)
- [Agent 工具安全：权限作用域、注入防护与执行校验](../05-工具调用与MCP/Agent工具安全-权限作用域注入防护与执行校验.md)
- [Agent 任务可靠性：租约、幂等、调度与状态恢复](../03-生产级开发基础/Agent任务可靠性-租约幂等调度与状态恢复.md)
- [报价中台面试映射手册 01：异步任务可靠性](./报价中台面试映射手册01-异步任务可靠性.md)
- [报价中台面试映射手册 02：RAG 检索路由与证据链](./报价中台面试映射手册02-RAG检索路由与证据链.md)
