---
title: Multi-Agent 协作：角色编排、共享状态、故障治理与工程边界
category: Agent 规划与工作流
tags:
  - Multi-Agent
  - Orchestration
  - State
  - Reliability
  - Human-in-the-loop
  - FDE
reviewed_at: 2026-07-30
status: 已整理
---

# Multi-Agent 协作：角色编排、共享状态、故障治理与工程边界

## 核心结论

Multi-Agent 不是“多调用几个模型”，而是多个具有独立目标、状态、工具权限和决策循环的 Agent，通过明确合同协作完成任务。

生产级 Multi-Agent 的重点不在角色名称，而在：

```text
为什么必须拆分
→ 谁负责什么
→ 怎样交接任务
→ 状态由谁保存
→ 局部失败怎样恢复
→ 冲突由谁裁决
→ 高风险动作怎样受控
→ 怎样证明它比单 Agent 更好
```

如果单 Agent、Workflow 和普通工具调用已经能稳定解决问题，就不应为了技术名词引入 Multi-Agent。

---

## 1. 什么才算 Multi-Agent

一个可工程化的 Agent 通常具有：

- 明确目标；
- 独立决策循环；
- 可用工具集合；
- 私有或局部状态；
- 权限与资源预算；
- 输入输出合同；
- 成功、失败和退出条件。

Multi-Agent 在此基础上增加：

- 角色间任务分配；
- 结构化 Handoff；
- 共享事实和产物引用；
- 协作状态机；
- 冲突裁决；
- 跨 Agent Trace；
- 局部失败与整体完成规则。

### 以下情况不等于 Multi-Agent

| 情况 | 正确归类 |
|---|---|
| FastAPI、N8N、Dify、RAG、Milvus 相互调用 | 多服务系统 |
| 一个 Agent 连续调用多个 Tool | 单 Agent 工具调用 |
| 多个模型按固定顺序执行 | 模型流水线或 Workflow |
| 同一模型使用不同 Prompt 做两次检查 | 多阶段推理 |
| 多个 Worker 并发消费任务 | 分布式任务执行 |
| 多个独立 Agent 功能互不通信 | Agent 集合，不是协作系统 |

判断标准是：是否存在多个独立决策主体，以及它们之间是否发生受控的任务交接和状态协作。

---

## 2. 什么时候值得拆成多个 Agent

至少出现以下两类强需求，再考虑 Multi-Agent。

### 2.1 上下文确实可以隔离

不同子任务依赖不同资料：

```text
招标资料研判：合同、清单、技术要求
成本治理：成本条目、历史价格、异常单位
项目进度：任务、证据、延期和门禁
```

隔离后可以减少无关上下文、工具干扰和敏感数据暴露。

### 2.2 权限边界不同

例如：

- 研判 Agent 只读招标资料；
- 成本治理 Agent 可生成 draft，但不能启用 active；
- 审核 Agent 只能给建议；
- 外部推送必须由确定性服务在人工确认后执行。

如果不同职责需要明显不同的权限，拆分才可能带来安全价值。

### 2.3 子任务可以独立并行

多个子任务互不依赖，可同时执行：

```text
合同风险分析 ─┐
成本证据检查 ─┼→ 汇总与裁决
资料完整性检查 ┘
```

如果后一步完全依赖前一步，多 Agent 往往只会增加通信开销。

### 2.4 需要独立的专业判断

例如一个 Agent 负责生成，另一个只按独立标准检查证据覆盖。这里的价值是职责和上下文隔离，不是简单让同一模型“再想一次”。

### 2.5 团队和生命周期本身独立

不同业务域由不同团队维护，有独立发布、评测、权限和 SLO，拆分后责任边界更清楚。

---

## 3. 什么时候不应使用 Multi-Agent

以下场景优先使用 Workflow、规则或单 Agent：

- 流程固定，分支可以由代码明确表达；
- 核心问题只是工具太多或工具描述相似；
- 多个角色使用同一上下文、同一工具和同一权限；
- 子任务之间强依赖，无法并行；
- 最终结果必须严格一致，但没有确定性裁决规则；
- 当前连单 Agent 的成功率、Trace 和评测集都不完整；
- 只是希望架构图看起来更复杂；
- 没有证明拆分后质量提升足以覆盖成本和延迟。

### 常见误区

```text
工具选不准 → 拆成多个 Agent
```

更优先的处理通常是：

```text
工具目录
→ 权限与业务域过滤
→ 候选召回
→ 重排
→ 参数和执行校验
```

Multi-Agent 不会自动解决工具路由问题，反而会新增“先选哪个 Agent”的路由问题。

---

## 4. 常见协作拓扑

| 拓扑 | 工作方式 | 适合场景 | 主要风险 |
|---|---|---|---|
| Supervisor–Worker | Supervisor 分解、派发并汇总 | 子任务清晰、需要统一出口 | Supervisor 成为瓶颈和单点 |
| Router–Specialist | Router 选择一个或多个专家 | 业务域清楚、工具差异大 | 路由错误、重复调用 |
| Pipeline | 角色按固定顺序交接 | 阶段输出稳定 | 很可能普通 Workflow 更合适 |
| Generator–Critic | 一个生成，一个独立审查 | 高价值内容复核 | 成本高、两个模型可能共同出错 |
| Blackboard | Agent 围绕共享事实板协作 | 多源信息逐步补全 | 状态冲突和调试复杂 |
| Peer-to-Peer | Agent 直接协商 | 开放研究或仿真 | 循环、失控和责任不清 |

### 生产系统的推荐起点

优先从下面的最小结构开始：

```text
Supervisor
   ├─ Specialist A
   └─ Specialist B
          ↓
确定性 Verifier / Policy
          ↓
Human Gate
```

不要一开始就建立自由对话、任意转交和动态创建 Agent 的网状系统。

---

## 5. Handoff：任务交接必须结构化

Agent 之间不应只发送一段自然语言：“请帮我继续处理。”

一个最小任务合同应包含：

```json
{
  "task_id": "task-20260730-001",
  "parent_task_id": "root-001",
  "trace_id": "trace-001",
  "objective": "检查报价中是否存在无成本证据的高风险条目",
  "input_refs": ["quote-job:123", "evidence-set:456"],
  "constraints": {
    "tenant_id": "tenant-a",
    "read_only": true,
    "deadline_ms": 10000,
    "max_tool_calls": 5
  },
  "tool_scope": ["read_quote", "read_cost_evidence"],
  "output_schema": "cost-risk-finding/v1",
  "idempotency_key": "cost-risk:quote-123:v1"
}
```

### Handoff 的基本规则

- 传目标、约束和资料引用，不传全部聊天记录；
- 输入输出必须有版本化 Schema；
- 上游不能假设下游一定成功；
- 下游不能扩大自己的任务范围；
- 交接事件必须落 Trace；
- 高风险权限不能随任务文本传递；
- 重试必须复用同一业务幂等键；
- 输出要带来源、置信边界和未完成项。

---

## 6. 共享状态不等于共享聊天记录

推荐把状态分为四层。

### 6.1 全局任务状态

保存整体协作进度：

```text
created
→ routed
→ running
→ waiting_dependency
→ waiting_human
→ completed / partial / failed / cancelled
```

### 6.2 Agent 私有工作状态

保存某个 Agent 当前步骤、工具结果、局部计划和预算。默认不向其他 Agent 暴露全部私有上下文。

### 6.3 共享事实板

只保存经过验证、可引用的事实或产物：

- 证据编号；
- 结构化风险发现；
- 版本化计算结果；
- 未覆盖事实；
- 冲突记录；
- 产物位置和内容哈希。

### 6.4 Append-only 事件

记录谁在什么时候：

- 接收了什么任务；
- 使用了什么版本；
- 调用了什么工具；
- 产生了什么结果；
- 为什么重试、降级或转人工。

### 三个概念不要混

| 概念 | 作用 |
|---|---|
| State | 当前任务运行到了哪里 |
| Checkpoint | 从哪个安全位置恢复 |
| Memory | 跨任务或跨会话复用什么信息 |

---

## 7. 协作状态机与终止控制

Multi-Agent 必须由 Runtime 或 Workflow 控制，而不是让 Agent 自由聊天到“感觉完成”。

### 必须设置的上限

- 最大 Agent 跳转次数；
- 单 Agent 最大 ReAct 轮数；
- 最大 Tool 调用次数；
- 总 Token 和费用预算；
- 单 Agent 和整单截止时间；
- 同一任务的重复转交次数；
- 同一结论的冲突轮数。

### 整体完成条件

不能简单判断“所有 Agent 都返回了文本”。

应明确：

```text
必要子任务是否完成
关键事实是否覆盖
输出 Schema 是否通过
冲突是否已裁决
高风险项是否已人工确认
是否存在未处理的 partial / failed
```

---

## 8. 局部失败与任务恢复

Multi-Agent 的常态不是全部成功，而是部分成功、超时或返回矛盾结果。

| 故障 | 工程处理 |
|---|---|
| Specialist 超时 | 有界重试、降级为缺失结果或转人工 |
| 任务重复投递 | 幂等键、条件更新、唯一约束 |
| Agent 进程崩溃 | 租约过期后重新领取，从 Checkpoint 恢复 |
| 下游不可用 | 熔断、隔离、排队或使用安全默认值 |
| 输出不符合 Schema | 修复节点或失败，不直接交给下一 Agent |
| 多个 Agent 结论冲突 | 规则、独立 Verifier 或 Human Gate 裁决 |
| Supervisor 失败 | 全局状态持久化，由新实例接管 |
| 高风险写操作不确定 | 禁止自动重试，进入人工确认 |

### 恢复时必须保留

- 原 `task_id` 和 `trace_id`；
- 当前 Agent 与步骤；
- 已完成子任务；
- 已提交的副作用；
- Checkpoint 版本；
- 剩余预算；
- 错误分类和重试次数。

---

## 9. 冲突裁决

多个 Agent 一致不代表正确，多个 Agent 不一致也不应无限讨论。

裁决优先级：

```text
权威数据和业务规则
→ 确定性 Verifier / PolicyEngine
→ 有证据约束的独立检查
→ Human-in-the-loop
```

### 不可靠的做法

- 简单多数投票；
- 让 Supervisor 凭语言风格选答案；
- 让生成 Agent 自己宣布冲突已解决；
- 为获得一致而反复调用模型；
- 把低置信度包装成确定结论。

高风险业务中，模型输出只能是建议或证据摘要，不能绕过确定性规则和人工责任人。

---

## 10. 权限与安全边界

每个 Agent 都应使用独立能力边界：

- 独立工具白名单；
- 租户、项目和资源范围；
- 只读与写入权限分离；
- 短期 scoped token；
- 单独的网络、文件和密钥访问范围；
- 调用预算和速率限制；
- 高风险动作审批；
- 全链路审计。

### 安全原则

```text
Supervisor 能派发任务
≠ Supervisor 自动拥有所有工具权限
```

Agent A 的输出对 Agent B 来说仍是不可信输入，必须进行：

- Schema 校验；
- 来源校验；
- 租户和资源校验；
- Prompt Injection 检查；
- 业务前置条件检查。

写操作不要因为被包装成“执行 Agent”就获得自治权。支付、删除、发布、启用成本、确认报价和外部推送等操作应继续由确定性服务和人工 Gate 控制。

---

## 11. 可观测性与评测

### 11.1 跨 Agent Trace

最少记录：

```text
trace_id
task_id / parent_task_id
source_agent / target_agent
handoff_reason
input_refs
tool_scope
model / prompt / policy version
start_time / duration_ms
status / error_type
output_ref
token / cost
human_decision
```

### 11.2 核心指标

| 维度 | 指标示例 |
|---|---|
| 结果 | 整体任务完成率、关键事实覆盖率 |
| 路由 | Agent 路由准确率、无效 Handoff 率 |
| 协作 | 平均跳转次数、重复工作率、冲突率 |
| 可靠性 | 局部失败率、恢复成功率、人工接管率 |
| 工具 | 调用成功率、越权拦截率、重复副作用数 |
| 效率 | P50/P95 延迟、Token、单任务成本 |
| 安全 | 越权、注入、敏感信息暴露、危险动作拦截 |

### 11.3 必须与单 Agent 基线比较

Multi-Agent 上线前应做消融实验：

```text
单 Agent
vs
单 Agent + 更好的工具路由
vs
Workflow + 局部 Agent
vs
Multi-Agent
```

只有当质量、覆盖率、权限隔离或吞吐收益明确超过额外延迟、成本和故障面时，拆分才有工程价值。

---

## 12. 当前 AI 智能报价中台的真实映射

### 12.1 当前不是 Multi-Agent 系统

报价资料研判当前是一套持久化的单 Agent Runtime：

```text
LangGraph StateGraph
→ ReAct / Tool Calling
→ Tool 授权门
→ Tender Evidence MCP
→ PolicyEngine
→ 证据门
→ Human-in-the-loop
→ SQL Checkpointer 恢复
```

报价复核小助手是另一项独立的只读审计能力。

它们目前没有：

- 统一 Supervisor；
- Agent 间结构化 Handoff；
- 共享协作状态机；
- 跨 Agent 冲突裁决；
- 整体 Multi-Agent 成功标准。

因此，正确表述是：

> 系统已有多个 Agent 相邻能力，但当前核心链路仍是“Workflow + 单 Agent + Tool/MCP + Policy + Human Gate”，尚未建立生产级 Multi-Agent 协作。

### 12.2 N8N、Dify、RAG 不是其他 Agent

它们分别承担工作流、模型应用和检索服务职责。组件多不等于决策主体多。

### 12.3 当前不急于拆分的原因

- 报价资料研判的主目标仍然单一；
- LangGraph 已能组织工具选择、证据门和人工恢复；
- 高风险业务需要统一 Policy 和人工责任；
- 多 Agent 会增加延迟、Token、权限和故障面；
- 真实金标与线上基线应先继续积累；
- 工具路由和上下文问题应先在单 Agent 内优化。

### 12.4 未来的合理触发条件

满足以下条件后，可以做小范围实验：

- 资料研判、成本治理、报价复核和项目进度形成稳定独立职责；
- 每个职责有独立数据、工具权限和评测集；
- 需要并行执行或统一经营汇总；
- Handoff Schema 和共享事实引用已经稳定；
- 单 Agent 基线暴露出明确的上下文、权限或吞吐瓶颈。

### 12.5 最小演进方案

```text
Read-only Supervisor
   ├─ 报价资料研判 Agent
   ├─ 报价后审计 Agent
   └─ 成本质量检查 Agent
              ↓
统一事实引用与风险发现 Schema
              ↓
确定性 Policy / Verifier
              ↓
Human Gate
              ↓
原有 Workflow 执行写操作
```

边界：

- Supervisor 只汇总，不获得所有写权限；
- Specialist 返回结构化发现和证据引用，不互传长上下文；
- Agent 不自动改价、不自动启用成本、不自动外部推送；
- 任何 Multi-Agent 实验必须能随时回退到现有单 Agent / Workflow。

---

## 13. 最小实验设计

### 实验目标

验证“独立成本检查”是否提高资料研判中的成本风险覆盖率，而不是为了展示多个角色。

### 对照组

- A：现有单 Agent；
- B：单 Agent 增加一个只读成本检查 Specialist。

### 固定条件

- 相同数据集；
- 相同模型版本；
- 相同证据和工具权限；
- 相同 PolicyEngine；
- 相同 Human Gate。

### 通过标准

- 关键风险覆盖率显著提高；
- 幻觉和无证据断言不增加；
- P95 延迟和单任务成本在预算内；
- Handoff 失败可恢复；
- 无新增越权和重复副作用。

如果没有稳定收益，应保留单 Agent。

---

## 14. 面试回答

### 什么是 Multi-Agent？

> Multi-Agent 是多个拥有独立职责、状态、工具权限和决策循环的 Agent，通过结构化任务合同、共享事实引用和协作状态机完成一个整体目标。它的难点不是角色 Prompt，而是 Handoff、状态一致性、局部失败恢复、权限隔离、冲突裁决和跨 Agent 评测。

### 为什么不把所有复杂流程都拆成 Multi-Agent？

> 多 Agent 会增加路由、通信、状态、延迟、成本和故障面。如果流程固定或只是工具选择问题，我优先使用 Workflow、规则和分层工具路由。只有上下文、权限、专业职责或并行任务确实需要隔离，并且评测证明收益时，才拆成多个 Agent。

### 多 Agent 怎样共享状态？

> 我不会共享完整聊天记录，而是区分全局任务状态、Agent 私有状态、经过验证的共享事实和 append-only 事件。Agent 之间通过版本化 Handoff Schema 传目标、约束、资料引用和输出合同；Runtime 用 Checkpoint、幂等键和 Trace 保证恢复与审计。

### 多个 Agent 结论冲突怎么办？

> 先使用权威数据和确定性 Policy/Verifier 裁决；仍无法判断时进入 Human-in-the-loop。不会简单多数投票，也不会让模型无限讨论到一致。

### 怎样评价当前报价中台？

> 当前报价资料研判是持久化的 LangGraph 单 Agent，已经具备 MCP、工具授权、PolicyEngine、证据门、Checkpoint 和人工审核；报价后审计是另一项独立 Agent 能力。但它们没有统一 Supervisor、Handoff 和共享协作状态机，所以我不会把项目夸大为 Multi-Agent。未来只有在职责、权限、数据和评测集稳定后，才会从只读 Supervisor 加少量 Specialist 做对照实验。

---

## 15. 检查清单

### 必要性

- [ ] 单 Agent 或 Workflow 的真实瓶颈已被数据证明
- [ ] 拆分能带来上下文、权限、并行或责任边界收益
- [ ] 不是为了工具路由或架构展示而拆分

### 合同

- [ ] 每个 Agent 有明确目标、输入、输出和退出条件
- [ ] Handoff 使用版本化 Schema
- [ ] 共享事实带来源、版本和内容哈希

### 状态与可靠性

- [ ] 全局状态与 Agent 私有状态分离
- [ ] 有 Checkpoint、租约、幂等和有界重试
- [ ] 定义 partial、failed、cancelled 和 waiting_human
- [ ] Supervisor 故障后可接管恢复

### 安全

- [ ] 每个 Agent 使用独立最小权限
- [ ] Agent 输出按不可信输入校验
- [ ] 高风险写操作不能由 Agent 自主执行
- [ ] 敏感数据和密钥不通过自然语言 Handoff

### 评测

- [ ] 有单 Agent 基线
- [ ] 评估路由、协作、冲突、成本和尾延迟
- [ ] 真实失败样本进入回归集
- [ ] 无稳定收益时可以回退

---

## 记忆口诀

```text
先证明需要拆，
再定义角色和合同；
状态集中管，
事实按引用共享；
局部失败可恢复，
冲突交给规则和人；
没有评测收益，
就不要上 Multi-Agent。
```

## 关联笔记

- [Agent 决策循环与执行架构](./Agent决策循环与执行架构.md)
- [生产级 Agent 核心机制与工程实践](../03-生产级开发基础/生产级Agent核心机制与工程实践.md)
- [Agent 任务可靠性：租约、幂等、调度与状态恢复](../03-生产级开发基础/Agent任务可靠性-租约幂等调度与状态恢复.md)
- [Agent 工具安全：权限作用域、注入防护与执行校验](../05-工具调用与MCP/Agent工具安全-权限作用域注入防护与执行校验.md)
- [Agent 工具与 Skill 路由](../05-工具调用与MCP/Agent工具与Skill路由-分层召回重排执行校验与Workflow边界.md)
- [Agent Memory 分层架构](../07-Memory/Agent记忆系统架构-短期工作压缩与长期记忆.md)
- [Agent 评测体系](../10-LLMOps与可观测性/Agent评测体系-事实过程工具效率安全与版本治理.md)
- [报价中台面试映射手册 03：Agent 编排、MCP 与人机协作](../12-面试与职业发展/报价中台面试映射手册03-Agent编排MCP与人机协作.md)
