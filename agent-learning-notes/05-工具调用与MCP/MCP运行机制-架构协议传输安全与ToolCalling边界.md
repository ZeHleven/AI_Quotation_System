---
title: MCP 运行机制：架构、协议、传输、安全与 Tool Calling 边界
category: 工具调用与 MCP
tags:
  - MCP
  - Tool Calling
  - JSON-RPC
  - Streamable HTTP
  - OAuth
  - Agent Engineering
  - FDE
sources:
  - title: MCP Specification 2026-07-28
    url: https://modelcontextprotocol.io/specification/2026-07-28
  - title: MCP 2026-07-28 Transports
    url: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
  - title: MCP 2026-07-28 Authorization
    url: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
  - title: MCP Security Best Practices
    url: https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices
reviewed_at: 2026-07-30
status: 已整理
---

# MCP 运行机制：架构、协议、传输、安全与 Tool Calling 边界

## 核心结论

MCP（Model Context Protocol）解决的是：

> AI 应用怎样用统一协议发现和调用外部工具、读取上下文资源，以及复用交互模板。

MCP 不负责：

- 判断下一步该做什么；
- 自动赋予 Agent 权限；
- 保证工具安全和幂等；
- 管理完整业务 Workflow；
- 替代 Agent State、Memory 或 RAG；
- 让普通工具自动变成 Multi-Agent。

最重要的分工：

```text
模型 / Agent Runtime：决定是否需要某项能力
MCP Host / Client：发现能力、适配协议、管理调用
MCP Server：提供受控的 Tool / Resource / Prompt
权限与业务系统：决定能否执行以及允许影响哪些资源
```

---

## 1. MCP 为什么出现

没有统一协议时，每个 AI 应用都要分别适配：

```text
文件系统
数据库
GitHub
企业知识库
浏览器
内部 API
消息系统
```

每种集成都要自行定义工具发现、输入 Schema、调用方式、上下文读取、错误结构、传输、认证和版本兼容。

MCP 把 AI 应用与外部能力之间的接口标准化，类似 Language Server Protocol 把编辑器与语言工具解耦。

主要价值：

- Host 不需要了解每个后端的内部实现；
- Server 可以服务多个兼容的 AI 应用；
- Tool、Resource 和 Prompt 可动态发现；
- 本地进程与远程服务使用相同语义；
- 能力契约更容易独立测试、版本化和审计。

---

## 2. 三个参与者

```text
MCP Host
   ├─ MCP Client A ── MCP Server A
   ├─ MCP Client B ── MCP Server B
   └─ MCP Client C ── MCP Server C
```

### Host

承载模型和用户交互的 AI 应用，例如 IDE Agent、桌面 AI 应用、企业 Agent Runtime 或 LangGraph Worker。

Host 负责：

- 管理多个 Client；
- 决定向模型提供哪些能力；
- 处理用户授权；
- 将 MCP Tool 转换成模型可理解的 Tool Schema；
- 验证结果并写入 Agent State；
- 控制预算、重试和 Human-in-the-loop。

### Client

Host 内部连接某个 MCP Server 的协议适配器，负责：

- 发送和接收 MCP 消息；
- 处理协议版本与能力信息；
- 发现 Tool、Resource 和 Prompt；
- 发起调用或读取；
- 处理传输、取消、超时和认证。

### Server

真正提供外部能力的程序，可以是本地子进程、远程 HTTP 服务、数据库或内部 API 的受控适配层。

Server 不应直接相信模型参数，仍需进行认证、授权、Schema、资源范围和业务规则校验。

---

## 3. 数据层与传输层

### 数据层

定义消息的意义：

- JSON-RPC 2.0 消息；
- Tool、Resource、Prompt；
- 能力发现；
- 错误；
- 进度与取消；
- 扩展。

### 传输层

定义消息怎样到达：

- 消息如何分帧；
- 请求怎样发送；
- 响应怎样返回或流式传输；
- 认证信息放在哪里；
- 如何取消和终止。

同一 Tool 语义可以通过不同传输运行。

---

## 4. 当前协议版本边界

MCP 变化较快，讨论实现时必须先说协议版本。

### 2026-07-28

当前规范的基础协议强调：

- 无状态、自包含请求；
- 协议版本和 Client 能力随请求携带；
- 没有协议级 Session；
- Server 不应把“持有某个状态句柄”当成认证；
- 可选能力通过扩展协商；
- 长任务等能力从核心协议演进为可选扩展。

### 2025-11-25 及更早版本

旧版常见流程是：

```text
initialize
→ 协议版本协商
→ Client / Server 能力协商
→ initialized
→ connection-scoped session 内操作
```

旧教程中大量 `initialize`、`ClientSession` 和 Session ID 内容可能仍用于兼容旧服务或旧 SDK，但不能不标版本地当成最新协议。

### 工程原则

- Client 与 Server 明确支持的协议版本；
- 做集成测试，不假设 SDK 自动兼容；
- 升级时先检查传输、能力元数据、认证和错误合同；
- 不把协议 Session 或状态句柄当成用户身份；
- 长任务采用哪个 Tasks 扩展版本必须显式确认。

---

## 5. Server 的三类核心能力

### 5.1 Tools

Tool 是可执行函数，例如搜索资料、查询数据库、创建草稿、运行测试和调用内部 API。

典型元数据：

- `name`；
- `description`；
- `inputSchema`；
- 输出结构或结构化内容；
- 可选注解和展示信息。

Tool 描述帮助模型选择，但不是权限政策。

### 5.2 Resources

Resource 是可读取的上下文数据，通常用 URI 标识：

```text
file:///project/README.md
db://schema/orders
tender://current/manifest
```

适合文件内容、数据库 Schema、项目清单、当前配置和其他已存在数据。

```text
Resource：读取某项已存在的上下文
Tool：请求系统执行一个动作或计算
```

### 5.3 Prompts

Prompt 是由 Server 提供、可复用的消息或交互模板，例如代码审查模板、业务研判步骤和 Few-shot 示例。

Prompt 只是模板，不是不可覆盖的系统政策。来自不可信 Server 的 Prompt 也必须视为不可信内容。

---

## 6. Client 能力与扩展

不同协议版本和实现可让 Client 提供：

- Roots：可访问根目录范围；
- Sampling：由 Server 请求 Host 使用模型；
- Elicitation：向用户请求补充信息或确认；
- 其他版本化扩展。

2026-07-28 规范还将 Tasks、Skills over MCP、MCP Apps 等作为可选扩展。

```text
未声明支持 → 不应调用
声明支持 → 仍需权限、预算和用户控制
```

不要因为某项能力出现在协议中，就默认所有 Host、Client 和 Server 都已实现。

---

## 7. 两种标准传输

### 7.1 stdio

Client 启动 MCP Server 子进程，通过标准输入输出传递换行分隔的 JSON-RPC 消息。

适合本地工具、IDE 插件和单用户桌面应用。

注意：

- `stdout` 只能输出合法 MCP 消息；
- 日志写入 `stderr`；
- Server 包、启动命令和环境变量必须可信；
- 不应因为“本地运行”就开放整个文件系统和全部密钥。

### 7.2 Streamable HTTP

远程 Server 提供统一 MCP Endpoint。2026-07-28 语义下，每条消息通过 HTTP POST 发送，响应可以是 JSON 或请求级 SSE 流。

适合企业内部共享服务、多实例部署、跨机器调用以及需要标准 HTTP 认证和观测的场景。

安全要求：

- HTTPS；
- 每个请求都验证认证与权限；
- 校验 `Origin`，防止 DNS Rebinding；
- 本地服务默认只绑定 localhost；
- 限制请求大小、超时和并发；
- 不把状态句柄当成身份。

旧版 HTTP+SSE 已被 Streamable HTTP 取代。兼容旧 Client/Server 时可以保留，但新设计应明确协议版本和传输。

---

## 8. MCP 与 Function / Tool Calling 的区别

| 维度 | Function / Tool Calling | MCP |
|---|---|---|
| 所在位置 | 模型与 Agent Runtime 之间 | AI 应用与外部能力服务之间 |
| 解决问题 | 模型如何提出结构化函数调用 | 外部能力如何被统一发现、描述和调用 |
| 是否绑定模型厂商 | 常与模型 API Schema 相关 | 协议本身模型无关 |
| 是否包含传输 | 通常不关心 | 定义 stdio、Streamable HTTP 等绑定 |
| 是否提供资源/模板 | 通常只描述函数 | Tool、Resource、Prompt 和扩展 |
| 是否自动授权 | 否 | 否 |
| 是否管理 Agent 循环 | 否 | 否 |

组合关系：

```text
MCP Client 发现 MCP Tool
→ Host 转换为模型 Tool Schema
→ 模型产生 Tool Call
→ Runtime 做授权和参数校验
→ MCP Client 发起 tools/call
→ MCP Server 执行并返回结果
→ Runtime 验证结果并写回上下文
```

一句话：

> Tool Calling 是模型“提出调用”；MCP 是应用“连接和调用外部能力”的标准协议。

---

## 9. 一次完整调用链

```text
1. Host 确认协议版本和当前请求能力
2. Client 发现 Server 暴露的 Tool / Resource / Prompt
3. Host 按用户、租户、任务和风险过滤能力
4. Host 把候选 Tool Schema 提供给模型
5. 模型提出 Tool Call
6. Runtime 校验白名单、参数、预算和审批
7. Client 通过 stdio 或 HTTP 发送 MCP 请求
8. Server 验证认证、scope、Schema 和业务前置条件
9. Server 执行并返回结构化结果或错误
10. Host 验证来源、权限范围、完整性和副作用回执
11. Observation 写入 Agent State，模型决定下一步
12. Trace 记录版本、调用、耗时、结果和人工决策
```

MCP 只是协议与能力边界的一部分，完整 Agent 仍需要 Runtime。

---

## 10. 认证、授权与安全

### 10.1 HTTP 授权角色

受保护的 MCP Server 是 OAuth Resource Server；MCP Client 是 OAuth Client；Authorization Server 负责签发访问令牌。

关键要求：

- 每个 HTTP 请求都携带并验证授权；
- token 必须绑定目标 MCP Server audience；
- Client 使用 Resource Indicator 指明目标服务；
- token 不能出现在 URL；
- 401 表示无有效身份，403 表示权限不足；
- scope 按最小权限和按需升级。

### 10.2 禁止 Token Passthrough

MCP Server 不能把 Client 传来的 token 原样转给下游 API。

```text
Client Token
→ MCP Server 验证自身 audience
→ MCP Server 使用独立下游身份访问第三方 API
```

否则会产生 Confused Deputy、audience 绕过、下游审计失真、权限扩大和 token 泄漏。

### 10.3 状态句柄不是身份

购物车 ID、Workflow ID、任务 ID 或旧 Session ID 只能标识状态，不能证明调用者有权访问。

Server 必须：

- 每次验证身份；
- 将句柄与认证主体绑定；
- 使用不可预测值；
- 设置过期时间；
- 拒绝其他主体提交的句柄。

### 10.4 Tool 和 Resource 内容不可信

Server 的工具描述、Resource 正文和 Tool 结果都可能被污染。

Host 仍需：

- 来源信任分级；
- Prompt Injection 防护；
- 用户同意；
- 参数与输出验证；
- 数据外传限制；
- Tool 沙箱；
- 高风险人工审批。

MCP 统一了接口，不代表接口后面的系统安全。

---

## 11. Tool 设计原则

### 粒度明确

优先：

```text
search_evidence
read_evidence_context
create_quote_draft
```

避免：

```text
manage_everything(action, payload)
```

### 描述包含边界

Tool 描述应说明使用场景、输入输出、前置条件、禁用条件、副作用和与相似 Tool 的区别。

### Schema 严格

- 拒绝未知字段；
- 数量、长度和枚举有范围；
- 资源 ID 与 scope 一致；
- 输出使用版本化合同；
- 错误区分可重试、永久失败和部分成功。

### 写操作单独治理

写 Tool 还需要幂等键、当前业务版本、审批参数哈希、唯一回执、补偿策略和更严格的审计。

---

## 12. MCP、RAG、Memory 和 API 不要混

| 概念 | 解决问题 |
|---|---|
| MCP | AI 应用如何标准接入外部能力 |
| Tool Calling | 模型如何提出结构化动作 |
| RAG | 如何检索外部知识并构造证据上下文 |
| Memory | 哪些信息跨步骤或跨会话保留 |
| REST/RPC | 普通服务之间如何调用 |
| Agent Runtime | 如何规划、执行、观察、恢复和终止 |

MCP Server 内部可以调用 REST API、数据库或 RAG；MCP 不是这些后端的替代品。

### 什么时候直接 API 更合适

- 只有一个固定调用方；
- 接口已经稳定；
- 不需要动态发现；
- 不需要在多个 AI Host 之间复用；
- 业务服务不应向模型暴露。

### 什么时候 MCP 更有价值

- 同一能力需要被多个 Agent 或 AI 应用复用；
- 希望工具和资源独立部署、发现和演进；
- 需要本地与远程统一适配；
- 希望把 AI 可见合同与内部实现解耦；
- 需要在 Host 层统一治理 Tool Catalog。

---

## 13. 当前报价资料研判 Agent 的项目映射

### 13.1 角色对应

| MCP 角色 | 项目实现 |
|---|---|
| Host | 报价资料研判 LangGraph Worker / Runtime |
| Client | `mcp_adapter.py` 中的 MCP Tool Caller 与 Evidence Adapter |
| Server | Tender Evidence FastMCP Server |
| Resource | `tender://current/manifest` |
| Transport | Streamable HTTP；本地验证也支持 stdio |
| 后端 | MySQL 证据库、混合检索和分层正文存储 |

### 13.2 当前 Tool

```text
search_tender_evidence
read_evidence_context
compare_document_versions
validate_evidence_refs
```

职责分离：

- `search` 只返回候选和引用；
- `read` 读取关键上下文并留下本次运行的读取审计；
- `compare` 检查文档版本；
- `validate` 由确定性证据门验证引用和读取状态。

这比一个万能“查询招标资料”Tool 更容易授权、评测和审计。

### 13.3 安全设计

- Agent 只使用只读工具；
- 用户登录 token 不传给 MCP；
- Worker 使用短期 scoped token；
- scope 绑定 `case_id / assessment_id / agent_run_id`；
- token 中包含 `allowed_tools`；
- `case_id` 由服务端 scope 决定，不允许模型跨项目修改；
- manifest、版本、内容哈希和上下文读取均有证据门；
- JWT secret 只保留在启动进程中，不写业务表和日志。

### 13.4 MCP 不等于检索算法

MCP Tool 合同保持不变，Repository 可以从数据库检索演进到：

```text
Exact / BM25
→ Embedding
→ Hybrid + RRF
→ 自适应检索路由
```

协议层稳定，检索实现可以独立优化。

### 13.5 当前版本边界

项目中的持久 `ClientSession` 是当前 SDK 和运行实现的连接复用策略。升级到 2026-07-28 协议时，应重点验证：

- 无协议 Session 的请求模型；
- 逐请求协议版本和能力元数据；
- Streamable HTTP 新消息方向；
- 认证是否在每个请求重新校验；
- 旧 Server / Client 的兼容回退；
- Tasks 等扩展是否真正需要。

不能只升级依赖版本后假设行为完全不变。

---

## 14. 常见错误

### 把 MCP 当成 Agent

MCP Server 提供能力，不负责整个任务的自主决策循环。

### 把 MCP 当成权限系统

协议支持认证与授权框架，但租户隔离、最小 scope、审批和业务规则仍由系统实现。

### 把所有内部 API 都暴露为 Tool

模型只应看见任务必需、风险可控、合同清晰的能力。

### 把用户 token 原样传给 MCP 或下游

这会破坏 audience、审计和服务边界。

### 把 Tool 描述当成可信系统指令

来自未知 Server 的 Tool、Prompt 和 Resource 元数据都应按不可信输入处理。

### 忽略协议版本

旧版连接 Session、当前无状态请求和不同 Tasks 设计不能混用。

### 用 MCP 替代业务状态机

长任务的状态、幂等、Checkpoint、人工恢复和副作用控制仍属于 Runtime 与业务系统。

---

## 15. 面试回答

### MCP 和 Function Calling 有什么区别？

> Function 或 Tool Calling 是模型输出结构化调用意图的机制；MCP 是 Host、Client、Server 之间发现和调用 Tool、Resource、Prompt 的标准协议。Host 可以把 MCP Tool 转成模型 Tool Schema，但模型提出调用后，仍要由 Runtime 做授权、参数校验、执行和结果验证。

### MCP 的核心架构是什么？

> Host 是承载模型和用户交互的 AI 应用；Host 内的 Client 连接具体 Server；Server 暴露 Tool、Resource 和 Prompt。协议数据使用 JSON-RPC 2.0，标准传输是 stdio 和 Streamable HTTP。

### MCP 是否自动安全？

> 不会。MCP 统一接口和授权框架，但租户隔离、最小 scope、token audience、禁止 token passthrough、参数校验、工具沙箱、幂等、审批和审计都需要应用实现。Tool 和 Resource 内容本身也可能不可信。

### 为什么项目要使用 MCP，而不是直接调用 Python 函数？

> 报价资料证据能力需要独立进程、标准 Tool/Resource 合同、短期项目 scope、读取审计和未来多 Host 复用。MCP 把 Agent 与证据存储和检索实现解耦；后端从数据库演进到混合检索时，Agent Tool 合同不需要改变。

### 怎样评价项目当前的 MCP 实现？

> 项目已经有真实 Tender Evidence MCP、Streamable HTTP、官方 Client Adapter、短期 scoped token、资源隔离、Tool 白名单和证据读取审计，不是只写了一个概念 Demo。边界是当前实现仍需按 2026-07-28 新协议验证无状态请求和兼容性，写工具、企业 OIDC/JWKS 和统一生产授权还没有因此自动完成。

---

## 16. 检查清单

### 协议

- [ ] 明确 Client 与 Server 支持的 MCP 版本
- [ ] Tool、Resource、Prompt 和扩展能力按版本发现
- [ ] stdio 或 Streamable HTTP 的消息与取消语义正确
- [ ] 升级时有旧版本兼容测试

### Tool 与 Resource

- [ ] Tool 粒度和输入输出明确
- [ ] Resource URI、版本和 MIME 类型可追溯
- [ ] Tool/Prompt/Resource 描述按不可信输入处理
- [ ] 写 Tool 有幂等、审批和回执

### 安全

- [ ] 每个请求都验证身份和权限
- [ ] token 绑定 MCP Server audience
- [ ] 没有 token passthrough
- [ ] scope 绑定租户、项目、动作和时间
- [ ] 状态句柄不被当成认证
- [ ] Streamable HTTP 校验 Origin、HTTPS 和请求限制
- [ ] stdio Server 的包、命令、目录和环境变量可信

### Runtime

- [ ] 模型 Tool Call 只是候选动作
- [ ] Host 在执行前重新校验权限、参数和预算
- [ ] Tool 结果经过 Schema、scope、来源和完整性验证
- [ ] Trace 记录版本、耗时、结果和人工决策
- [ ] MCP 没有替代 State、Checkpoint、Memory 和 Workflow

---

## 记忆口诀

```text
Host 管模型与用户，
Client 管协议连接，
Server 提供工具和资源；
Tool Calling 提出动作，
MCP 标准化能力接入；
协议不等于授权，
接入不等于可信，
版本不清就不能谈兼容。
```

## 关联笔记

- [Agent 工具安全：权限作用域、执行沙箱、注入防护与执行校验](./Agent工具安全-权限作用域注入防护与执行校验.md)
- [Agent 工具与 Skill 路由](./Agent工具与Skill路由-分层召回重排执行校验与Workflow边界.md)
- [Agent 技术栈全景图与数据流](../02-Agent核心架构/Agent技术栈全景图与数据流.md)
- [生产级 Agent 核心机制与工程实践](../03-生产级开发基础/生产级Agent核心机制与工程实践.md)
- [Agent 决策循环与执行架构](../06-Agent规划与工作流/Agent决策循环与执行架构.md)
- [Multi-Agent 协作](../06-Agent规划与工作流/Multi-Agent协作-角色编排共享状态故障治理与工程边界.md)
- [报价中台面试映射手册 03：Agent 编排、MCP 与人机协作](../12-面试与职业发展/报价中台面试映射手册03-Agent编排MCP与人机协作.md)
