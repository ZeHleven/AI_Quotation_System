# 旗胜投标机会研判 Pure Agent — Architecture Baseline v0.1

| 项目 | 内容 |
|---|---|
| 状态 | 已冻结，可作为隔离本地开发依据 |
| 版本 | v0.1 |
| 日期 | 2026-08-20 |
| 目标 | 用最小可演进架构启动 Pure Agent 开发，避免重新落入固定 Workflow |
| 详细讨论归档 | `bid-assessment-pure-agent-architecture-design-record-20260819.md` |
| 开发任务清单 | `bid-assessment-pure-agent-development-task-list-v0.1-20260820.md` |
| 当前实现状态 | C08-4 隔离本地页面真实闭环通过：最终 Task `completed/v18`，动态 Action Loop、只读检索/证据升级、Grounding、5 条 Runtime Citation 和 Answer Commit 全部成功；中间失败均安全终止且未发布无效回答。不等同于 B07/ECS 发布授权，0110 仅存在于隔离本地库 |
| 隔离要求 | 功能开关默认关闭；只允许完全隔离的本地开发；不得进入 ECS 或正式发布候选 |

## 1. Agent 目标

用户提交招标资料或自然语言问题后，Agent 应根据当前问题自主决定是否解析资料、查询招标证据、查询离线企业知识、读取证据、澄清缺失信息、形成计划或直接回答，从而：

- 识别招标资料中的重要信息、限制条件和风险项；
- 结合离线 RAG 中经过解析、切块、向量化和入库的企业资料判断匹配度与风险；
- 支持围绕资料、企业能力、证据和研判结果的实时连续问答；
- 对证据不足、冲突、权限限制和未知内容进行明确披露。

输入和用户可见输出均保持开放，不预设固定表单、固定报告章节、七项硬门或固定 Task 路径。

## 2. 非目标与硬边界

- 不构建固定 DAG、节点编排、预设步骤链或以报告字段驱动的隐藏 Workflow；
- 不恢复已移除的旧执行链，不修改旧 `bid_intake_*`；
- 不新增独立 Intent Classifier、Router LLM、Planner Agent、Answer Agent 或 Verifier Agent；
- 不把 Plan Step、Tool Call 或 Answer Artifact 状态提升为 Agent Task 顶层状态；
- 不因本基线自动运行 Agent 测试、模型、真实 PDF、OCR/视觉、Embedding、Reranker、检索评测或外部 MCP；
- 不连接、迁移、备份、重启或修改 ECS、生产 MySQL、Redis、MinIO、Milvus 和旧 CentOS 环境。

## 3. 总体架构

```text
用户 / 对话界面
        │ 自然语言、文档引用、Slot 补充、取消
        ▼
Conversation API + 安全事件流
        ▼
Pure Agent Runtime Controller
  ├─ IntentUnderstandingPort
  ├─ Complexity Gate ──按需──> PlannerPort
  ├─ ContextAssemblerPort
  ├─ Provider Adapter：Message / Schema / strict / Structured Output
  ├─ Main Agent：running 内开放动态 Action Loop
  │      ├─ 直接回答 / 澄清
  │      ├─ 生成或修订有限步 Plan
  │      └─ Function Calling
  ├─ Canonical Tool Gateway
  │      Registry → Router → Permission Guard → Executor
  │          ├─ documents_outline
  │          ├─ bid_document_search
  │          ├─ enterprise_knowledge_search
  │          └─ evidence_read
  ├─ AnswerDraft / Grounding Guard / CitationProjector / Renderer
  └─ State、Checkpoint、Budget、Effect、Call、Context 与 Audit Ledger
        │
        ├─ 招标资料 Parse/Chunk/Retrieval/Evidence Store
        └─ 企业资料离线 RAG / Evidence Store
```

图中组件是职责边界，不是依次执行的 Workflow 节点。Main Agent 每轮可根据当前问题、状态和 Observation 自主选择下一动作，也可直接结束。

## 4. Agent 运行模型

Agent Task 创建后进入 `running`。在同一个 `running` 状态内，Main Agent 可以动态执行零个或多个 Action：理解问题、直接回答、形成或修订 Plan、选择工具、观察结果、请求澄清或生成回答。

Runtime 只负责合法性、权限、资源、幂等、恢复和安全提交，不规定 `Intent → Plan → Search → Read → Answer` 的固定顺序。

### 4.1 顶层状态

只允许五个顶层状态：

- `running`：正在自主决策或执行；
- `pending`：存在合法未填 Slot，已保存可恢复 Checkpoint，正在等待用户；
- `completed`：已安全提交回答，包括带 Unknown、No Result 或权限限制的回答；
- `failed`：Runtime 无法产生安全结果；
- `cancelled`：任务已取消，后续结果不得提交。

合法转换：

```text
create -> running
running -> running | pending | completed | failed | cancelled
pending -> pending | running | failed | cancelled
completed / failed / cancelled -> 不允许普通恢复
```

每次转换必须校验 Event ID、State Version、合法矩阵、Slot/Checkpoint 条件和 Effect 幂等性。

### 4.2 Direct 与 Planned

- 单一目标、短证据循环或可直接回答的问题保持 `direct`；
- 多目标、存在依赖、跨来源完整研判、明显分支或高风险动作时启用 `planned`；
- Direct 运行中复杂度上升时可以升级为 Planned，保留已接受的 Evidence 和 Observation，只规划剩余任务；
- `direct/planned` 是运行模式，不是顶层状态。

## 5. 意图理解、Planner 与 Slot

### 5.1 意图与信息需求

`IntentUnderstandingPort` 是逻辑独立合同，初版复用 Main Agent 模型和当前完整授权 Context，输出开放式目标、信息需求、来源提示、澄清需求和复杂度判断。它不采用固定业务标签分类器。

只有确实阻塞且必须由用户提供的信息才创建 Slot；可以通过检索、工具或已有上下文获得的信息不得转嫁给用户。

### 5.2 Planner

Planner 是 Runtime 内的一次结构化 LLM 调用，不是第二个 Agent 或独立服务。只在 Complexity Gate 触发时生成有限步滚动 Plan，并只在目标、证据、约束或可执行性发生实质变化时修订。

Plan 顶层保留：

- `goal_summary`
- `completion_criteria`
- `steps`
- `next_decision`
- `replan_conditions`
- `user_projection`

每个 Step 保留：

- `id`
- `title`
- `description`
- `dependencies`
- `tool_hint`
- `expected_output`
- `output_schema`
- `risk_level`

`tool_hint` 必须引用当前 Registry 中可见工具；`expected_output` 指导模型，`output_schema` 由 Runtime 强制校验。Step 描述信息或决策子目标，不等同于 Tool Call，也不携带 Slot。

完整 Plan JSON 通过版本化 Pydantic/JSON Schema 校验后才能采用；用户可见计划进度可以安全流式展示，但内部推理、未校验 JSON 和 Chain-of-Thought 不展示。

### 5.3 Pending、Slot 与 Continuation

进入 `pending` 前必须同时存在：未解决的合法 Slot、PendingContext 和可恢复 Continuation Checkpoint。

用户补充内容后执行两阶段校验：

1. Pydantic 格式校验；失败时返回字段级安全提示并继续等待；
2. 业务校验；格式正确但不符合业务约束时返回可操作提示并继续等待。

校验成功后，Slot resolved、Checkpoint consumed、Task 恢复 `running` 和 State Version 更新必须原子提交；从原挂起点继续，不重启整个任务，也不重复已完成 Effect。

## 6. Tool 合同与执行边界

### 6.1 Canonical ToolDefinition

首版只有六个必填字段：

```text
name
description
input_model: type[BaseModel]
output_model: type[BaseModel]
execution
safety
```

- `name` 同时是内部和 Provider 可见的 snake_case 身份；
- `input_model` 与 `output_model` 是 Pydantic 模型，JSON Schema 是它们面向协议的投影；
- Runtime Context 不进入 ToolDefinition 或模型参数，通过显式依赖注入提供给 Executor；
- 版本通过 Registry Snapshot、Definition Hash 和 Schema Hash 追踪，不给每个 Tool 建复杂 SemVer 路由；
- `examples/few_shots` 默认不存在或为空，只有评测证明 description 和 Input Schema 仍无法稳定区分时才增加。

### 6.2 模型可见合同

Function Calling 首版向模型投影 `name`、低重叠的正向场景 `description` 和 `input_schema`。Provider 支持时开启 strict，但 Runtime 的 Pydantic、业务、权限、安全、输出和溯源校验始终是最终权威。首版不提前发送完整 Output Schema。

Description 使用具体正向白名单场景，说明动作、来源、返回和证据升级要求；避免多个 Tool 描述覆盖同一场景，优先缩小每轮动态工具白名单。

Provider Adapter 使用冻结能力快照协商 Function Calling、结构化输出和 strict。Pydantic Schema 不能安全表达时必须报告不兼容；`preferred` 可显式降级为非 strict，`required` 不可降级。最终 Wire Payload 由匹配 Token Counter 复核 Context Budget，Provider 错误只以安全结构回流。默认 Provider、Counter、Transport 全部 disabled，首版不内置网络客户端。

Provider 返回的 Tool Call 只规范化为带当前 Context/Registry/Visible Tools/State Version 的 Proposal，再交给 Gateway；Adapter 不执行 Tool。Intent 与 Planner 复用同一结构化调用桥接及 Pydantic 最终校验，不形成新的 Agent 或固定调用链。

### 6.3 Registry、Router、Guard、Gateway、Executor

- Registry：唯一工具定义与 Binding 事实源；
- Router：根据当前目标和状态投影本轮相关工具，不做权限判断，初版无独立路由 LLM；
- Permission Guard：在工具可见前和执行前分别校验用户、租户、项目、资料、企业知识和引用 Scope；
- Gateway：组织输入、业务、权限、安全、执行、输出和溯源校验；
- Executor：按 `execution` 选择 Local Handler 或 MCP Binding，并注入受控 Runtime Context。

模型侧统一使用 Function Calling；执行侧 Local 与 MCP 都适配为同一个 Canonical Tool Message。采用 MCP 是部署绑定选择，不改变 Agent 合同。

### 6.4 首批工具

| Tool | 最小用途 | 证据边界 |
|---|---|---|
| `documents_outline` | 浏览当前招标文档结构并缩小检索范围 | 导航结果不可直接引用 |
| `bid_document_search` | 在当前已绑定招标资料中查找条款候选 | Candidate 不可直接引用 |
| `enterprise_knowledge_search` | 在当前授权企业离线知识中查找能力候选 | Candidate 不可直接引用 |
| `evidence_read` | 按受控 Evidence Ref 读取原文、定位和有限上下文 | 校验成功后形成可引用 Evidence Atom |

## 7. RAG 与证据

### 7.1 离线知识工程

- 招标资料与企业资料共享逻辑合同，但严格隔离 Scope、Head、索引和权限；
- 解析保留 Native/OCR 来源、页码/章节/表格定位、版本和 Hash；
- 采用 Parent/Child/Atom 三层：Parent 保留语义结构，Child 用于召回，Atom 用于事实和引用；
- 切块结构优先，重叠必须可追溯，不能破坏表格、条款编号和来源定位；
- 通过 Content Hash 幂等、旁路重建、Ready Head、Stale/Tombstone 管理版本；
- Chunk 大小、重叠、Embedding 和索引参数不在本基线冻结。

### 7.2 在线召回

- 先执行 Scope、Snapshot 和 Metadata Filter；
- 初版使用确定性 Query Strategy 和词法主导 Hybrid Retrieval；
- BM25F 与 Child-only Vector 通过 rank-only Weighted RRF 融合并稳定去重；
- Reranker 是默认关闭的可选策略，失败时回到冻结的 Fusion Baseline；
- Search 只返回不可引用 Child Candidate，事实和引用前必须经 `evidence_read` 升级为 Atom；
- No Result、Degraded、Permission Limited、Source Missing 和 Evidence Insufficient 必须保持不同语义。

相关性分数、Reranker 分数和模型判断都不能替代来源权威、Scope、版本、Hash 和引用完整性校验。

## 8. Memory 与 Context

Memory 采用四个逻辑层：Working、Conversation、Project/Assessment、User。Memory 是带 Source、可版本化、可失效和可遗忘的 Context 辅助，不是真实业务数据或证据权威，也不替代 Message、Checkpoint、Observation 或 Evidence。

`ContextAssemblerPort` 在每次模型调用前确定性组装六个 Lane：Policy/Protocol、Active Control、Tool Contract/Active Calls、Observation/Grounding、Relevant Interaction、Historical Memory。

- 使用硬保护区与动态弹性区；
- Token 以 Provider 实际序列化和匹配 Tokenizer 为权威；
- 按 L0 去重、L1 结构化投影、L2 旧对话摘要、L3 索引+原文回取、L4 显式限制/缩小逐级压缩；
- 当前 Policy、用户消息、Task/Slot 控制、可见 Tool Schema、活动 Tool Pair、最新关键纠正和结论所需 Evidence Atom不得无声截断；
- 每个模型调用冻结 Context Snapshot，记录 Included/Excluded、压缩、Token、Source/Policy/Profile/Registry 版本和 Hash；
- 文档、Evidence、Memory、历史消息和 Tool Output 全部按不可信数据投影，不能提升为系统指令或扩大权限。

## 9. 回答与引用

用户看到自由自然语言；Main Agent 与 Runtime 之间使用通用 Pydantic `AnswerDraft`，包含 Narrative、Statement、Limitation 和 Interaction Block。

- Material Fact、Calculation、Inference、Recommendation 使用 Statement 并绑定当前 Context Snapshot 中合法 Grounding Ref；
- Claim Type、Epistemic Status 和 Source Basis 分离，明确 Supported、Partial、Conflicted 和 Unknown；
- 模型只选择 Grounding/Quote Ref，不能手写用户可见 URL、页码或内部 Source ID；
- Runtime 校验 Schema、Scope、Source Head、Version、Hash、Grounding Status、支持矩阵和 Quote Span；
- `CitationProjector` 生成当前权限下的安全引用，Renderer 生成用户回答；
- 无效 Draft 不发布，可有界修复；仍失败时只基于已验证 Grounding 与 Limitation 安全回退；
- 首版完整 Draft 校验并 Commit 后再发布，不抢跑未经验证的事实 Token；
- 已发送回答不可变，纠正或来源变化产生新版本或 Supersede 关系。

这些组件均为同一 Runtime 内部合同，不构成固定回答 Workflow 或第二个 Agent。

B05-1 已按该边界落地：`AnswerDraft` 只冻结 Narrative、Statement、Limitation、Interaction 四类通用 Block；Source Basis 由 Runtime Grounding Record 推导，不由模型自由填写；模型侧不存在 URL、页码、Locator 或 Citation 显示字段。确定性 Guard 在回答 Observation 前校验当前 Context/Authorization、Scope、Source Head/Version/Hash、支持矩阵、Conflict Group、Limitation Receipt、Quote Span 和 Slot Ref。

B05-2 已按该边界落地：`CitationProjector` 只从 B05-1 已接受 Draft、当前 Grounding Snapshot 与 Runtime Citation Authority Snapshot 生成安全 Citation Bundle，并重新核验 Authorization、Scope、Source Head/Version/Hash、Locator、Conflict Group 与精确 Quote Span；禁止模型手写 URL、页码、Citation 标记或内部引用。通用 `AnswerBlockRenderer` 负责安全标签、行内引用编号与受控来源列表，不固定研判章节。输出仅为不可发布的 `RenderedAnswerCandidate`，提交、版本、不可变发布和 Supersede/Stale 仍属于 B05-3。

B05-3 已按该边界落地：`AnswerCommitRuntime` 只接受已被当前 Task 接受的 AnswerDraft Observation，并再次绑定 Context、Draft Validation、Citation Bundle 和 Rendered Hash；提交事件与取消使用同一 State Version/CAS。`PublishedAnswerMessage` 不携带内部 Task/Context/Draft/Grounding/Authorization/Source Ref；内部 `CommittedResponseArtifact` 保留完整审计链。B02 Repository 在调用者事务内原子追加 Message、Response、旧版本 Supersede 生命周期事件和 Task Completed 事件，幂等重放不重复消息。历史正文保持不可变，Stale/Supersede 只向哈希链式生命周期 Envelope 追加事件；未新增迁移，API、SSE 和界面仍属于 B05-4 以后。

B05-4 已按该边界落地：新增独立默认关闭的 Conversation API，提供对话创建/读取、开放消息、受控 Assessment/Document Version 引用、消息分页、Task 状态、Slot 补充与取消。API 只按当前五态确定新 Task Trigger 或 Steering Candidate，不做关键词意图分类，不解释用户目标；Slot 入口复用 Pydantic 格式校验、业务校验、Resume Token、Checkpoint 和 State Version，校验失败返回安全重填提示，成功后从原 Checkpoint 恢复 `running`。取消与 Answer Commit 使用相同 Conversation→Task 锁顺序和 CAS/Fence；接口不调度 Worker，不调用模型、Tool 或 RAG，安全事件流仍属于 B05-5。

B05-5 已按该边界落地：新增 Task 级安全事件分页与可恢复 SSE，以现有不可变 State Transition Ledger 的 `state_version` 作为连续游标，不新增事件表或迁移；Task 创建投影为 version 1，后续 transition 按 version 2…N 发布，`Last-Event-ID` 只包含已公开 Task Ref 和版本。公开事件只允许 Task Started、Plan Updated、白名单 Progress、Input Required/Validating/Rejected/Accepted、Answer Preparing/Completed、Failed 和 Cancelled；计划只展示已验证 `user_projection` 摘要与可见 Step 标题，Action 不展示 Tool Name/Arguments，完成事件只复用 B05-3 已验证的 `PublishedAnswerMessage` 与 Citation。SSE 的轮询只属于传输层，不拥有 Agent Action Loop、业务路径或状态决策权。

B05-6 已按该边界落地：新增默认关闭的 `/admin/bid-assessment-pure-agent` Vite 交互入口，提供开放自然语言输入、受控 Assessment/Document Version 引用、对话恢复、Task 五态、按需 Plan 投影、安全进度、Pending Slot 重填、回答 Block/Citation 查看和取消。页面只消费 B05-4/5 的公开合同，不在前端模拟 Agent、推导业务阶段或固定报告结构；简单问题可以没有 Plan，只有 Runtime 实际生成计划时才展示。SSE 使用带鉴权的 Fetch Stream 和 `Last-Event-ID` 恢复，浏览器只持久化公开事件游标。Slot Resume Token 不进入 DOM、日志或 Web Storage，只允许后续 Runtime 调度器在同页内存中绑定；未绑定时补充按钮安全禁用，用户不需要填写内部凭证。所有写请求继续携带幂等键与当前 State Version。

B05-7 已按该边界落地：新增管理员专用只读诊断 API 与独立 `/admin/bid-assessment-pure-agent-diagnostics` 页面，从现有 Ledger 投影脱敏 State/Call/Budget/Loop/Cancel/Recovery Trace。投影不包含思维链、Prompt、消息/证据正文、Tool 参数/结果、权限与 Scope 凭证、Resume Token、Effect Key、Provider 回执或原始异常；Loop 首版只报告可证明的精确指纹重复，不伪造尚未持久化的语义无进展判定。诊断页没有调度、重试、恢复、取消或修改入口，不驱动 Main Agent 下一步，也不构成 Workflow。入口复用默认关闭的 Pure Agent 本地开发开关并强制管理员鉴权；未新增迁移。

## 10. T13 六项最小运行护栏

1. 资源/预算上限：Model/Tool Action 在可配置 Runtime Profile 内执行，超限后停止继续消耗并安全收束；
2. 重复/无进展循环防护：使用结构化 Fingerprint 发现重复 Action、相同 Plan 或连续无新增信息，不读取 Chain-of-Thought；
3. 幂等/Effect Fence：有成本或副作用的 Action 执行前建立唯一身份与 Fence，恢复、重试和并发不得重复产生 Effect；
4. 取消/迟到结果隔离：取消后阻断新 Action，迟到结果不得进入 Context、Memory、Answer 或改变终态；
5. Direct/Durable 触发边界：普通 Model Call 和首批只读 Tool 直接有界执行；只有长时、跨进程、需回执或副作用协调的操作进入 Durable 执行；
6. Checkpoint/Recovery：只在安全边界保存最小可恢复状态；恢复复用已接受 Observation，并按 Replay/Reconcile Policy 继续。

具体次数、时长、Token、费用、Retry/Failover、退避、并发和完整 Ledger 字段属于开发配置与后续评测 Backlog，不是继续架构讨论的前置条件。

## 11. 最小持久化逻辑域

首版实现需要支持以下逻辑事实，但本基线不冻结物理表名和字段：

- Conversation、Message、Turn；
- Agent Task、State Version、Action、Event、Artifact；
- Plan 与 Plan Revision；
- Slot、Validation Result、PendingContext、Continuation Checkpoint；
- Tool Call/Result、Model Call、Effect Fence、Budget Ledger；
- Context Snapshot、Observation、Grounding、Response、Citation；
- Memory Entry、Source Dependency、Invalidation/Audit。

物理 Schema 与 Alembic 迁移在实现时设计，只允许应用到完全隔离的本地开发数据库；开始迁移前必须重新核对当前本地唯一 head。

## 12. 初版延期项

以下内容不阻塞开发：

- 所有数值阈值、模型选择、Prompt 优化和算法超参数；
- 高级 Retry/Provider Failover、复杂并发、分布式编排和全量 Reconcile；
- Memory 向量索引、独立语义摘要模型、独立 Answer Verifier；
- 每个 Tool 的 Example/Few-shot 与模型可见完整 Output Schema；
- T14 完整评测体系及真实资料基准集；
- ECS/生产迁移、发布、开关启用和基础设施变更。

## 13. 基线变更规则

- 开发以本文为架构权威；长设计记录只作为讨论依据和 ADR 归档；
- 实现细节可在不违反本基线的前提下边做边调整，不需要继续扩写长记录；
- 只有改变 Pure Agent 核心边界、五状态、Planner/Slot/Tool/RAG/证据权威或六项运行护栏时，才新增 ADR 并升级本基线版本；
- 首批开发从合同、状态机和禁用态骨架开始。任何受限测试或运行必须另行取得用户明确授权。

## 14. C04-2 / C05 / C06 实现结论（2026-08-21）

- `evidence_read` 的持久化结果只有在 Evidence Atom 文本、locator、scope、version 与 provenance Hash 完整一致时才进入可引用 Context；Search 结果、旧无 provenance 结果和篡改结果只保留为不可引用收据或直接拒绝；
- C05 Composition Root 显式注入 DeepSeek Provider、Planner、四个 Canonical Tool、本地 RAG、Tool Gateway、Capability Executor、Evidence Authority 和 Runtime Guard。Main Agent 每轮仍只自主选择一个下一 Action，没有固定阶段图或 Tool 顺序；
- C06 复用默认关闭的 Conversation API、后台 Pulse Dispatcher、安全 Event/SSE 与 Vite 页面，确定性测试已贯通对话创建、开放问题、动态检索/取证、引用、Answer Commit、Task/消息/事件读取；
- 本地真实验收使用 307 页冻结 PDF、冻结企业基线、本地 BCE、内存 SQLite 与官方 DeepSeek。Runtime 正确拒绝了输入预算超限、重复 JSON key、Pydantic Decision/Answer 失败和 Tool/Answer 持久化合同失败，从未提交未通过 Grounding/Citation 的回答；
- 真实验收门当前为 `failed`，不是架构完成或上线信号。C07 必须收敛更小的 Provider-visible Answer Projection，并在两次结构修复上限、Atom-only 引用门和五态状态机不放宽的前提下重新验收。

## 15. C07 Provider-visible Answer Projection 结论（2026-08-21）

- 模型可见的无 Tool 决策只保留 `action_kind`、`concise_basis`、`payload`；Answer payload 只包含回答语言和自由组合 blocks，不再要求模型复制 Runtime Context/版本谱系或完整引用合同；
- Adapter 按当前请求注入 `context_snapshot_ref`、`state_version` 与其他 Runtime 字段，再升级为原权威 `MainAgentModelDecision / AnswerDraft`。任何 Projection 或升级校验失败仍 fail-closed；
- 单层 block 投影按 `block_type` 做条件字段校验，保留 Narrative、Statement、Limitation、Interaction，不增加固定章节、阶段图或 Tool 顺序，因此 C07 不改变 Pure Agent 的动态 Action 边界；
- DeepSeek Tool 分支仍为 Function Calling；仅无 Tool 回答和受控修复使用精简 Projection。Runtime Pydantic、Grounding Guard、CitationProjector、Answer Commit、预算/循环护栏和五态状态机继续是最终权威；
- C07 独立专项 `11 passed / 0 failed`，C01—C07 影响域相邻回归 `60 passed / 0 failed`；覆盖 Adapter 首次无 Tool 输出、Provider Schema 兼容、精简结构修复、白名单 `loc/type/reason_code`、Answer 条件业务规则和 Runtime 权威谱系注入；
- 官方 DeepSeek 隔离真实业务复验使用冻结 307 页香港中心 PDF、冻结企业基线和本地 BCE，最终 Task `completed`、状态版本/事件均为 `18`，提交 1 份回答和 5 条 Citation。结果保存于 `.local-c07-pure-agent-acceptance/result.json`；
- 复验没有启用 OCR/视觉、外部 MCP、Milvus、ECS 或生产数据库。历史 C06 `failed` 产物及 C07 四个 fail-closed 中间产物均独立保留，证明兼容/校验失败不会提交回答；
- C07 证明本地真实业务闭环可用，但不构成 B07、迁移、ECS 发布、正式开关启用或生产可用性授权。

## 16. C08-1 本地显式入口结论（2026-08-21）

- 日常入口固定为 `read-only Preflight → explicit Runtime materialization → existing Bootstrap`，但这只是启动权限门，不规定 Main Agent 的 Action 路径，不形成 Workflow；
- Preflight 只验证隔离环境和能力元数据，公开 Hash Report 不含路径、密钥或内部对象；Runtime Authority 仍由双开关、Continuation Secret、完整 Adapter、Guard 与 Bootstrap 共同决定；
- 实际资料/BCE 装配与模块导入分离，只有显式 Factory 且 Preflight 全通过后才发生；官方 DeepSeek Adapter 仍在首次模型 Action 时才产生网络调用；
- 页面从安全状态端点判断是否允许提交新 Task，Conversation API 的持久化/执行分层合同未改变；
- 本地启动器不接入生产 lifespan，不修改默认关闭开关，不连接 ECS、生产 MySQL/Redis/MinIO/Milvus 或旧 CentOS。

## 17. C08-2 显式本地启动结论（2026-08-22）

- Runtime 的显式启动边界已落地：Preflight `20/20` 后才读取冻结资料与 Secret 白名单、加载本地 BCE 并执行既有 Bootstrap；这仍只是能力装配，不把 Agent Action 固定成 Workflow；
- Preflight 新增应用运行依赖门，启动器使用隔离 venv；依赖不完整时在资料读取和模型加载前 fail-closed；
- 回环服务在 `127.0.0.1:9018` 可访问，Health/管理页均为 HTTP 200，PID 文件绑定真实监听进程，不存在公网监听；
- 六张核心运行表均为 0，说明“完成启动”与“开始 Agent Task”保持严格分离；本轮没有问题输入、Action、Provider Call 或 DeepSeek 网络请求；
- C08-2 不改变 Function Calling、动态 Planner/Action Loop、状态机或 Capability 边界，也不授权正式发布、ECS、生产迁移、生产开关或外部能力。

## 18. C08-3 本地鉴权与页面用户态结论（2026-08-22）

- 隔离 `staff` 用户可以通过既有 Auth/RBAC 登录，并只因本地 Pure Agent 功能开关和模块合同满足而看到入口；C08 没有新增旁路认证或特殊页面权限；
- 鉴权 Runtime Status 只返回安全状态投影：`ready`、`runtime_available=true` 和公开原因码，不暴露路径、Secret、Adapter 或内部 Runtime 对象；
- 浏览器登录、目标页重定向和刷新恢复均通过，页面无 Runtime 阻断告警；空输入禁用发送、有输入且 Runtime Ready 时启用、清空后再次禁用；
- C08-3 全程没有创建 Conversation/Task/Action/Call/Event，证明身份会话与 Agent 执行仍是两条分离边界；
- 下一次真实页面提交仍需独立授权真实资料、Embedding 和官方 DeepSeek。C08-3 不扩大 ECS、生产发布、迁移、OCR/视觉、MCP 或 Milvus 权限。

## 19. C08-4 页面真实闭环结论（2026-08-22）

- 页面自然语言输入触发的是 Main Agent 每轮动态选择下一 Action，而非固定阶段图。最终任务按实际信息需要自主形成 `Main Agent Decision → Tool Batch → Decision` 的有限循环，再进入 Answer；Planner 未被强制启用；
- 真实运行补齐了活动 Action 未处理异常的顶层失败收束，以及完整 Provider Envelope 的 Context 预留。任何异常都会结清预算/Effect、持久化安全 Observation 并进入五态之一，不再留下虚假的 `running`；
- 模型可见 Answer Projection 进一步缩小语义权限：`block.text` 只能表达业务内容，模型只选择 `grounding_refs`；Citation 编号、页码 Locator、来源标签继续由 Runtime 投影。Grounding/Citation Guard 没有降级或绕过；
- 最终 Task `completed/v18`，包含 4 次模型决策、3 个 Tool Batch、6 个只读 Tool Call 和 1 个 Answer Action。Answer Validation/Citation Projection 均接受，3 个 Statement、1 个 Limitation 和 5 条 Citation 已原子提交；
- 回答给出当前“不建议立即投标”的研判，识别工期延误高额违约责任与担保能力未核验两项关键风险，并将缺少担保/资金能力资料明确标为 `evidence_insufficient`；
- Context 超限、Provider 无效 JSON、模型正文自行生成定位符等中间失败均被 fail-closed，未提交未验证回答；修正后的模型原始正文不含 Citation Marker、页码或 URL，最终显示信息全部来自 Runtime Authority；
- C08 证明隔离本地日常使用入口已经可用，但不会改变默认关闭、生产隔离、逐次真实资料/模型授权或禁止 ECS 操作等边界。
