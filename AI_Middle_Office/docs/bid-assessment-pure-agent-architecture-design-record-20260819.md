# 旗胜投标机会研判 Pure Agent 架构设计记录

| 项目 | 内容 |
|---|---|
| 文档状态 | Discussion Archive；Architecture Baseline v0.1 已冻结，停止继续扩写 |
| 当前版本 | v0.47 |
| 建立日期 | 2026-08-19 |
| 适用范围 | 旗胜投标机会研判 Agent 的新架构设计 |
| 当前实现状态 | 旧固定 Workflow 已退出活动执行链；Pure Agent Runtime 尚未实现 |
| 短版架构基线 | `bid-assessment-pure-agent-architecture-baseline-v0.1-20260820.md` |
| 开发任务清单 | `bid-assessment-pure-agent-development-task-list-v0.1-20260820.md` |
| 决策原则 | 后续开发以短版 Architecture Baseline 为权威；本文仅保留历史讨论、ADR 和重大架构变更 |

## 1. 文档目的

本文档是新一代旗胜投标机会研判 Agent 的历史设计讨论与 ADR 归档，用于：

1. 记录设计讨论中提出的问题、候选方案和取舍依据；
2. 明确区分“已确认决策”“候选方案”“开放问题”和“已否决方案”；
3. 避免开发过程中把未经确认的假设固化为代码；
4. 在实现、评测和验收阶段提供统一的架构基线；
5. 保持产品、Agent、RAG、工具、Memory 和运行治理设计的信息对称。

Architecture Baseline v0.1 已另行冻结。自 v0.47 起停止按议题继续扩写本文；实现以短版基线和开发任务清单为准。只有修正历史事实、补充必要 ADR 或发生经确认的重大架构变化时才更新本文。

## 2. 当前工程基线

### 2.1 已完成

- Phase 4D-3 v0.1-r62 的历史业务闭环、真实 Run 和冻结 RC 保持为已有资产；
- 固定 Plan Commit、P0-P4 Continuation、Task DAG Maintenance 和固定 MVP1 Task Queue 已退出活动 Celery 注册及 Beat 调度；
- 本地 Runtime Lab 固定为历史只读模式；
- Worker、模型调用和写请求保持关闭；
- 历史 Run、Task、Checkpoint、证据、报告、验证、模型与工具账本继续保留，用于审计和设计参考。

### 2.2 尚未完成

- 尚无可运行的 Pure Agent Runtime；
- 尚未冻结新的 Agent 控制循环；
- 尚未冻结意图理解、任务规划、工具路由、RAG、Memory 和上下文方案；
- 尚未建立新架构的分层评测基线。

### 2.3 继承约束

- 新 Agent 必须与 ECS 和正式环境隔离，未获明确上线许可不得进入正式发布候选；
- 所有 Agent 开关默认关闭；
- 不修改旧 `bid_intake_*`；
- 未经用户明确授权，不运行 Agent 测试、真实资料、OCR/视觉、Embedding、Reranker、模型或外部 MCP；
- 工作区现有未提交改动必须完整保留。

## 3. Agent 目标工作稿

> 旗胜投标机会研判 Agent 是一个持续存在的对话式认知主体。它接收用户在会话中提供的问题、指令和招标资料，理解用户当前想解决的问题，结合当前招标资料、企业离线 RAG、会话上下文以及获准使用的工具，持续形成和修正对项目重要信息、企业匹配情况、风险项和未知信息的认知，并根据当前问题提供合适形式的回答或研判结果。

该目标仍是工作稿。它描述希望具备的能力，不预先决定内部必须采用独立意图分类器、Planner、Tool Router、某种检索算法或某种 Memory 实现。

## 4. 已确认的设计原则

### P-001：必须是 Pure Agent，而不是固定业务 Workflow

状态：**已确认**

- Agent 的下一步应由运行时目标、上下文和观察结果决定；
- 不使用 P0-P4 等固定业务阶段驱动执行；
- 不预先创建固定数量的业务 Task DAG；
- 不以“所有预设 Task 是否执行完毕”作为 Agent 完成标准。

允许存在确定性的基础设施、权限校验、协议校验、数据处理任务和最终结果校验。它们不替 Agent 决定业务调查路径。

### P-002：业务输入和业务输出不固定

状态：**已确认**

- 用户可以在任意时刻输入问题、指令、文件、补充事实或纠正信息；
- 不要求用户先完成固定表单或一次性提交全部资料；
- Agent 可以回答、追问、提示风险、解释证据、给出阶段性认知或整理完整结果；
- 不强制每次会话填满同一份业务报告结构。

工程层仍可存在稳定的消息、工具、权限、证据和审计协议。这些协议不等同于固定业务输入输出。

### P-003：知识问答按具体问题动态选择信息

状态：**已确认**

- 不把知识问答固定为“招标问答”或“企业问答”两条入口；
- Agent 应根据当前问题判断回答需要什么信息；
- 一个问题可以组合当前招标资料、企业离线 RAG、会话上下文和其他获准信息源；
- Agent 可以直接回答、检索、比较、拆解问题或请求澄清。

### P-004：必须具备 LLM 驱动的任务规划能力

状态：**已确认**

- LLM 需要判断完成当前用户任务可能采用什么路径；
- 任务路径由当前目标、上下文、已有证据、可用工具和约束共同决定；
- 规划结果不能退化成开发阶段预设的固定 P0-P4 或固定 27 Task DAG；
- Planner 是按任务复杂度触发的能力，不是每个用户 Turn 或模型决策轮次的固定前置步骤。

尚未确认 Planner 的组件形态、Complexity Gate 实现、规划粒度、完整 Schema 和重规划策略；与 Tool Router 的最小边界已在 ADR-015 确认。

### P-005：架构讨论完成并保持信息对称后再开始开发

状态：**已确认**

- 所有关键设计必须讨论候选方案和取舍；
- 已确认与未确认内容必须明确标识；
- 不能把助手的推荐自动视为用户确认；
- 开发前应冻结一版 Architecture Baseline 和相应评测方案。

这里的 Architecture Baseline 只要求冻结目标、职责边界、核心组件、关键数据流、安全底线和可演进接口，不要求在开发前穷举并冻结全部字段、数量上限、错误分支和算法参数。

### P-006：Planner 机器输出结构化，用户计划可流式展示

状态：**已确认**

- Planner 返回给 Agent Runtime 或后续模型的机器输出必须符合版本化 JSON Schema；
- 未通过 Schema 校验的计划不得直接进入执行；
- 计划可以通过流式事件向用户展示；
- 用户可见内容是计划摘要、步骤和状态变化，不是模型原始内部推理文本；
- 固定的是 Planner 通信协议，计划内容、长度和路径仍由 LLM 动态产生。

### P-007：Step 与 Slot 分离，关键字段程序化校验

状态：**已确认**

- Planner Schema 保留 `goal_summary`、`completion_criteria`、`steps`、`next_decision`、`replan_conditions` 和 `user_projection`；
- `steps` 是 Planner 生成的具体子任务列表，Step 不内嵌 Slot；
- Step 保留 `id`、`title`、`description`、`dependencies`、`tool_hint`、`expected_output`、`output_schema` 和 `risk_level`；
- 对结构、类型、格式、范围和跨字段关系有要求时，应通过 Pydantic、JSON Schema Validator 等数据校验库执行程序化校验；
- Agent 在执行任务时发现关键信息不足，才单独创建 Slot 并将运行状态挂起为 `pending`；
- 用户提供的 Slot 值必须校验，不能把自然语言抽取结果直接当作可信结构化事实；
- Slot 是 Agent 运行时按需产生的交互对象，不是 Step 字段，也不是要求用户预先填完的固定业务表单。

### P-008：State Machine 是 Agent Runtime 的状态控制权威

状态：**已确认**

- Agent 需要独立 State Machine 精确管理行为、运行流转和状态数据；
- LLM 负责理解、规划和提出下一动作，但不能直接任意修改 Runtime State；
- 所有状态变化必须由事件触发，经过合法转换和 Guard 校验；
- 状态转换应产生可持久化、可恢复、可审计的版本或事件；
- State Machine 管理通用 Agent 生命周期，不预设投标研判业务阶段，因此不等同于固定 Workflow；
- `direct/planned` 是执行模式，规划、检索、工具调用、观察和回答是动态活动或事件，均不得膨胀为强制顶层状态链。

### P-009：Tool Registry 引用与 Step 输出双轨约束

状态：**已确认**

- `tool_hint` 必须引用工具注册表中的稳定工具标识或能力标识；
- Planner 的 `tool_hint` 是可解析建议，最终调用仍需经过 Tool Router、权限和可用性校验；
- Step 同时保留自然语言 `expected_output` 和结构化 `output_schema`；
- `expected_output` 用于指导模型理解预期结果，`output_schema` 用于程序化强制校验；
- 未通过 `output_schema` 校验的 Step 结果不能直接标记为成功。

### P-010：Pending Slot 采用格式校验加业务校验闭环

状态：**已确认**

- Agent 进入 `pending` 时，必须向用户明确输出需要补充的内容、原因、格式要求和必要示例；
- `pending` 是可持久等待状态，等待期间不得空转调用模型或工具；
- 用户输入先填入对应 Slot 的候选值，再由 Pydantic 执行参数类型、格式、枚举、范围和跨字段校验；
- Pydantic 校验失败时保持 `pending`，将内部错误转换为友好、可操作的重新输入指导；
- 格式校验通过后必须继续执行独立业务校验；格式正确不代表内容符合业务要求；
- 业务校验失败时同样保持 `pending`，向用户说明不符合的业务条件并指导修正；
- 只有格式校验和业务校验全部通过，Slot 才能标记为已解决并恢复 Agent 运行。

### P-011：Slot 解决后从原 Pending 暂停点精确续跑

状态：**已确认**

- 进入 `pending` 前必须保存 Continuation Checkpoint，记录暂停位置和恢复目标；
- Slot 两阶段校验通过后，将已验证值合并到对应 Runtime Context；
- State Machine 从上一次 `pending` 挂起位置的下一条合法 Transition 继续；
- 已完成 Step、已接受 Observation 和已完成外部副作用不得重复执行；
- 默认行为是续跑而不是从头规划；只有新输入触发明确的 `replan_conditions`、原 Plan 已失效或恢复 Guard 不通过时，才在 `running` 内触发重新规划；
- 恢复过程必须使用幂等键、状态版本和 Checkpoint 防止重复消费用户输入或重复调用工具。

### P-012：新 Agent 的 Tool Registry 收敛为单一权威来源

状态：**已确认收敛原则、首批工具清单和六字段 ToolDefinition**

- 新 Pure Agent 不继续维护 Schema 枚举、数据库版本记录、Task `allowed_tools` 和 Adapter Map 四份彼此可能漂移的工具注册事实；
- 新 Runtime 只能有一个 Canonical Tool Registry，Planner、Tool Router、参数/结果校验和 Executor 都读取它的投影或绑定；
- Registry 只描述工具身份、能力、调用合同、执行绑定和安全属性，不承载固定业务 Task 分类或固定业务路径；
- 当前会话可用工具由权限、数据范围、功能开关和运行环境从 Canonical Registry 动态投影，不再由旧 Task Category 预置一套 `allowed_tools`；
- 已冻结历史 Run 依赖的 `BidToolRegistryVersion`、旧 Schema 和旧执行账本只保留为历史只读兼容资产，不进入新 Agent 的注册或路由链；
- 未实现、近期不使用的工具不能进入首批 Active Registry；需要时按版本新增，不能为设想中的远期能力预占大量空工具。
- 首批 Active Registry 只包含 `bid_document_search`、`enterprise_knowledge_search`、`evidence_read` 和 `documents_outline` 四个 Provider-safe Tool Name；
- 旧 Schema 中其余工具不进入新 Agent 的首批 Registry，即使历史合同或 Handler 仍为审计兼容而保留。

### P-013：Canonical ToolDefinition 采用六个必填核心字段

状态：**已确认**

- Canonical ToolDefinition 保留 `name`、`description`、`input_model`、`output_model`、`execution` 和 `safety` 六个必填核心字段；只有 P-014 规定的评测条件成立时，才增加默认空的可选 `examples` 或 `few_shots`；
- `name` 同时作为 Registry Key、Planner `tool_hint` 和模型可见 Function Name，不再维护独立 `id` 与 `name` 映射；
- `input_model` 和 `output_model` 使用 Pydantic Model 作为 Python Runtime 事实源，JSON Schema 由其自动生成；
- Runtime Context 不属于 ToolDefinition，不进入模型 Tool Schema；Executor/Gateway 通过显式依赖注入向 Handler 提供经过验证的 `ToolExecutionContext`；
- `ToolExecutionContext` 不携带完整 Messages 或 State，只携带当前调用必要的身份、权限、会话和数据 Scope 引用；
- 不要求每个 Tool 手工维护 SemVer；历史回放和兼容判断由不可变 Registry Snapshot Version/Hash、Tool Definition Hash 及 Input/Output Schema Hash 负责；
- Function Calling 是 Canonical Definition 的模型协议投影，`execution` 只描述真正的 `local` 或 `mcp` 执行绑定。

### P-014：Tool 选择采用场景去重、动态白名单和按需示例

状态：**已确认**

- Tool description 必须写清具体正向使用场景和数据来源，邻近工具的使用场景不得大面积重叠；
- Description 以“什么情况下允许并适合使用”为主，不依赖大范围黑名单枚举排除条件，避免误伤合法调用；
- 只有两个相邻工具存在已知混淆时，才允许写一条窄而明确的边界说明；
- Canonical Registry 是工具全集；Router/Gateway 每轮根据当前问题、Planner 建议、权限、Scope、数据就绪状态、风险和功能开关计算动态白名单，只向模型暴露本轮允许的工具子集；
- 动态白名单是 Runtime Projection，不是旧 Task Category 的静态 `allowed_tools`，不得重新形成固定 Workflow；
- 模型返回 Tool Call 后，Gateway 必须再次检查 Tool Name 是否仍在本轮白名单并重新执行权限和 Scope Guard；
- 当 description 和 Input Schema 经评测仍无法稳定区分工具或构造参数时，允许在 Canonical ToolDefinition 中按需增加可选 `examples` 或 `few_shots`；默认均为空，不要求每个工具配置；
- Function Calling 首版不提前发送完整 Output Schema；Provider 支持时启用 `strict`，但 Gateway 的 Pydantic、业务和权限校验始终是最终权威。

### P-015：初版架构只冻结最小边界，细节通过实践迭代

状态：**已确认**

- 初版先形成可实现、可验证、可替换的最小架构，不在开发前穷举所有边界条件；
- 架构层冻结职责、控制权、安全边界、最小输入输出语义和扩展点；字符数、召回数、分页大小、上下文展开方式等实现参数不升格为架构决策；
- Pydantic 仍负责基本类型、必填性和禁止额外字段，复杂校验只在真实问题出现或风险明确时增加；
- 开发和小范围验证中发现的问题必须回写设计记录、测试和评测，再决定修正实现、Schema 或架构；
- “边做边改”不放松权限、Scope、证据溯源、状态一致性和生产隔离等安全底线。

### P-016：Planner 仅在任务复杂度达到条件时启用

状态：**已确认**

- Planner 不作为每个用户 Turn 或模型决策轮次的固定前置步骤；
- 简单任务允许不创建正式 Plan，复杂任务达到触发条件时才启用 Planner；
- 初版由主 Agent 判断复杂度，不增加独立复杂度分类模型；
- 单一目标的短证据循环允许保持 `direct`；多目标、依赖、跨来源完整研判、明显分支或高风险动作触发 `planned`；
- `direct` 执行中发现任务变复杂时，保留已有 Evidence 和 Observation 后升级为 `planned`，不得从头重复执行；
- Planner 未启用时，Pydantic、Tool Router、Permission Guard、State Machine、证据和审计约束仍然全部生效。

### P-017：Agent Task 状态机不得成为隐藏 Workflow

状态：**已确认**

- Task 顶层状态只表达任务是否可继续运行、正在等待或已经终止，不表达研判业务做到哪一步；
- 禁止使用 `planning → retrieving → comparing → reporting` 等认知/业务活动作为必须依次经过的顶层状态；
- Plan Step、Tool Call、Observation、Response 和 `direct/planned` 模式作为 Task Context 中的动态记录，不作为固定状态链节点；
- State Machine 只验证事件、数据不变量、权限、幂等和终止条件，不根据当前状态预设下一项业务调查内容；
- 同一 `running` 状态内允许主 Agent 根据目标和 Observation 自主回答、规划、调用工具、重规划或继续思考；不再同时保留语义重叠的 `active` 顶层状态。

### P-018：Memory 分层治理但不替代恢复状态和事实权威

状态：**已确认**

- Memory 采用 Working、Conversation、Project/Assessment 和 User 四个逻辑层，但不要求拆成四套物理服务或索引；
- Agent Task State/Continuation Checkpoint、Conversation Message、Tool Observation、Evidence/业务事实、Memory 和 Context Projection 保持职责分离；
- Checkpoint 负责执行位置和副作用围栏的精确恢复；当前 Business Record、Source Atom 和 Grounding Record 继续负责事实权威；Memory 只是带来源、可版本化和可失效的 Context 辅助；
- 首版不向模型暴露可任意调用的 `write_memory` Tool，不增加独立自由运行的 LLM Memory Extractor，也不建设 Memory Vector Index；
- 当前 Task 运行资产和 Conversation Message 可以自动记录；持久 Project/Assessment Memory 必须绑定 Scope、Source、Version 和资格；User Memory 只保存用户明确表达或确认的稳定偏好；
- 私有推理、隐藏 Prompt、认证秘密、无界原文/日志和未验证猜测禁止进入长期 Memory；
- Memory 读取必须先执行权限和 Scope 过滤，再校验版本、有效性和相关性；当前 Source/Evidence 始终优先于派生摘要，历史内容不能成为 System 指令或扩张权限；
- 用户纠正、Source 变化和证据冲突通过新版本、Supersede、Stale、Revoked 或 Conflicted 管理，不静默覆盖；
- 用户遗忘请求立即阻止对应 Memory 的未来检索，但不会越权删除独立的招标原文、企业知识、Evidence 或业务记录；
- Memory Commit 是可选 Runtime 副作用，一个 Task 可以写零条或多条 Memory，不设置每个 Task 必经的“提取并写入记忆”业务阶段。

### P-019：Context 按当前模型调用动态组装且关键内容不可无声截断

状态：**已确认**

- `ContextAssemblerPort` 是每次实际模型调用前的确定性 Runtime 边界，只负责授权内容的选择、预算、压缩、安全投影和 Snapshot，不承担意图、规划、授权或事实判断；
- Context 采用 Policy/Protocol、Active Control、Tool Contract/Active Calls、Observation/Grounding、Relevant Interaction、Historical Memory 六个逻辑 Lane；Lane 是保护与选择规则，不是 Agent 业务阶段；
- 预算采用“硬保护区 + 动态弹性区”，具体 Token、阈值和单项上限通过版本化 Provider/Context Profile 管理，不在架构中冻结永久百分比；
- 当前 Policy、User Message、Task/Slot 控制不变量、Visible Tool Schema、活动 Tool Pair 和当前引用所需 Evidence Atom 不得无声截断；不能容纳时必须明确降级、缩小或失败；
- 调用前 Token 权威来自 Provider 实际序列化与匹配 Tokenizer；缺少匹配 Tokenizer 时使用经校准的保守 Estimator 和 Safety Margin，`chars / 4` 只允许作为诊断；
- 压缩按 L0 无损去重、L1 结构化投影、L2 旧对话结构化摘要、L3 索引 + 原文回取、L4 显式限制/拆分逐级触发；短 Context 不生成摘要；
- Conversation Summary 必须保留 Source Message Ref/Hash、覆盖范围、版本和失效关系；关键决定与纠正优先回取原文，不允许依赖失去原始来源的递归摘要；
- 活动 Tool Call/Result 保持 Provider 协议完整，旧结果使用 Canonical Observation Projection；用于事实或引用的 Evidence Atom 保持原文、Locator、Version、Hash 和 Grounding；
- 每个模型调用冻结独立 Context Snapshot，并记录 Included/Excluded、Compression、Token、Policy/Profile/Registry/Source Version 和 Hash；
- Evidence、Memory、历史 Message 和 Tool Output 始终按不可信数据投影，不能成为 System 指令、扩大 Tool 权限或跨越 Scope。

### P-020：用户回答自由组织，事实责任通过内部 AnswerDraft 和 Runtime Citation 治理

状态：**已确认**

- 用户可见回答保持自由自然语言，不固定为 Preliminary Report、七项硬门或其他业务章节；内部使用 Narrative、Statement、Limitation、Interaction 通用 Block 的 Pydantic `AnswerDraft`；
- 所有会影响用户判断的 Fact、Calculation、Inference 和 Recommendation 必须使用 Statement Block 并绑定当前 Context Snapshot 中的 Grounding Ref；Narrative 只用于标题、过渡和非事实交互；
- Claim Type、Epistemic Status 和 Source Basis 分离；Supported、Partial、Conflicted、Unknown 采用不同表达，User Assertion 不自动升级为外部已验证事实；
- 模型只能选择合法 Grounding/Quote Ref，不能手写用户可见 Citation URL、页码或 Source 标识；`CitationProjector` 根据当前权限、Source Version、Locator 和 Hash 生成安全引用；
- Material Fact 必须有当前有效 Citation；Inference/Recommendation 必须绑定前提或触发条件；Conflict 展示各方；Unknown 不生成支持该事实的假 Citation；
- 确定性 Guard 校验 Schema、Scope、Version、Hash、状态、支持矩阵和 Quote Span，但不夸大为完全证明自由文本语义蕴含；首版不增加独立 Answer Verifier 模型；
- Retrieval No Result、Source Not Provided、Evidence Insufficient、Conflict、Stale/Unavailable、Permission Limited、Tool Degraded 和 Context Limited 使用不同限制语义；
- 无效 Draft 不发送用户；先有界修复，再使用已验证 Grounding 构造安全回退，具体修复预算由 T13 治理；
- 首版可以从 Provider 向 Runtime 流式接收，但完整 Draft 通过 Guard 并 Commit 后才向用户展示 Rendered Response 和 Citation；
- 已发送回答不可变；用户纠正或 Source/权限变化产生新 Response/替代关系，历史回答可以 Stale 但不静默修改；
- 语言、长度和格式偏好可以读取 User Memory，但不能覆盖 Grounding、Citation、Unknown 和安全披露；
- Main Agent 生成 AnswerDraft，Grounding/Citation Guard、CitationProjector 和 Renderer 是同进程 Runtime 组件，不构成第二个 Agent 或固定 Workflow。

### P-021：Runtime 只冻结六项最小运行护栏

状态：**已确认**

- 资源与预算上限：所有 Model/Tool Action 必须受可配置预算约束，超限时停止继续消耗并安全收束；
- 重复与无进展循环防护：基于结构化 Action、Arguments、Observation 和 Plan Fingerprint 识别重复或无新增信息，不读取或保存 Chain-of-Thought；
- 幂等与 Effect Fence：有成本或副作用的 Action 在执行前建立唯一调用身份和 Effect Fence，避免恢复、重试或并发导致重复执行；
- 取消与迟到结果隔离：取消后立即阻断新 Action；迟到结果不得进入 Context、Memory、Answer 或改变终态；
- Direct/Durable 触发边界：普通 Model Call 和首批只读 Tool 直接有界执行，只有长时、跨进程、需回执或需副作用协调的操作进入 Durable 执行；
- Checkpoint 与恢复：只在安全边界保存最小可恢复状态，恢复时复用已接受结果，并依据 Replay/Reconcile Policy 决定后续动作。

具体次数、时长、Token、费用、退避、并发、Failover 和高级循环策略不在 v0.1 架构中冻结，统一作为开发 Backlog 和后续授权评测参数；这些护栏只约束自主循环，不规定 Agent 的业务步骤或调用顺序。

## 5. 已确认决策记录

### ADR-001：移除固定 Workflow 的活动执行权

| 字段 | 内容 |
|---|---|
| 状态 | 已确认、已完成第一阶段代码收敛 |
| 决策 | 固定 Plan、P0-P4、Plan Continuation、Task DAG 和固定 MVP1 Task Queue 不再作为新 Agent 执行主链 |
| 原因 | 其下一步由代码预先决定，本质上是 Workflow |
| 保留内容 | 历史数据、证据、Checkpoint、审计账本、模型/工具基础设施和最终校验能力 |
| 非决策 | 不代表旧历史文件已经全部物理删除 |

### ADR-002：开放式会话而非固定输入输出

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 决策 | Agent 接受开放式会话输入，并按当前问题选择回答形式 |
| 原因 | 固定输入和固定报告会反向塑造固定执行步骤 |
| 工程含义 | 只固定最小通信和安全协议，不固定业务字段集合 |

### ADR-003：问题驱动的信息源选择

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 决策 | 每个问题单独判断所需信息和来源，允许多信息源组合 |
| 原因 | 同一问题可能同时涉及招标条件、企业事实、历史结论和用户补充信息 |
| 非决策 | 尚未决定是否采用独立意图理解器或信息需求分析模块 |

### ADR-004：引入 LLM Task Planning 能力

| 字段 | 内容 |
|---|---|
| 状态 | 已确认能力需求，组件细节未确认 |
| 决策 | LLM 负责判断完成任务需要走什么路径 |
| 原因 | 开放式用户问题无法由固定路由覆盖 |
| 禁止退化 | 不得把 LLM Planner 的输出直接固化为强制固定 DAG |

### ADR-005：Planner JSON Schema 与用户可见计划流

| 字段 | 内容 |
|---|---|
| 状态 | 已确认原则，具体字段和事件协议待确认 |
| 决策 | Planner 的机器输出必须使用结构化 JSON 并通过版本化 JSON Schema 校验 |
| 用户体验 | 计划可以按流式事件向用户展示，并持续呈现步骤开始、完成、修订、等待和结束状态 |
| 安全边界 | 用户流展示计划投影，不展示模型原始内部推理文本；不执行尚未通过校验的部分 JSON |
| 非决策 | 尚未确定 Schema dialect、完整字段、修复策略、SSE 事件命名和持久化模型 |

### ADR-006：Plan Step 与运行时 Slot 分离

| 字段 | 内容 |
|---|---|
| 状态 | 已确认分离原则；Slot 完整字段和状态全集待确认 |
| Step | Planner Step 使用 `id`、`title`、`description`、`dependencies`、`tool_hint`、`expected_output`、`output_schema` 和 `risk_level` 描述子任务 |
| Slot | Slot 不属于 Step；Agent 仅在执行中发现信息不足时创建独立 Slot |
| Pending | Slot 产生后，Agent 的当前任务运行状态挂起为 `pending`，并向用户请求所需信息 |
| 校验 | 用户输入通过 Pydantic 或等价验证器执行类型、格式、范围和跨字段校验 |
| 恢复 | Slot 校验成功后从进入 `pending` 前保存的 Continuation Checkpoint 精确续跑；校验失败时保持 `pending` 并返回结构化错误 |
| 防 Workflow | 状态机只管理通用运行生命周期，不预设投标研判的固定业务阶段 |

### ADR-007：State Machine 管理 Agent 行为、流转和状态数据

| 字段 | 内容 |
|---|---|
| 状态 | 已确认架构需求，状态全集和实现库待确认 |
| 决策权 | LLM 提出动作；State Machine 根据当前状态、事件、Guard 和数据不变量决定是否允许转换 |
| 管理范围 | Task、Plan、Step Runtime、Slot、Tool Call、Observation、响应和终止状态 |
| 数据要求 | State Context 使用结构化模型并版本化；转换后持久化 Checkpoint/Event |
| 防 Workflow | 状态是通用运行生命周期，不是固定业务阶段；Plan Step 仍由 LLM 动态生成 |

### ADR-008：Tool Hint 注册表约束与 Expected Output 双轨制

| 字段 | 内容 |
|---|---|
| 状态 | 已确认原则和首版 Tool Name 规则；Output Schema dialect 待确认 |
| `tool_hint` | 首版必须直接引用 Canonical Registry 中的稳定 Provider-safe Tool Name；不能使用无法解析的任意字符串驱动执行 |
| `expected_output` | 保留自然语言描述，用于指导 Planner、模型和用户理解 |
| `output_schema` | 使用结构化 Schema 约束 Step 结果，并由运行时程序化验证 |
| 成功条件 | Tool 执行成功不等于 Step 成功；结果还必须满足 `output_schema` 和必要业务校验 |

### ADR-009：Pending Slot 两阶段验证和友好重试

| 字段 | 内容 |
|---|---|
| 状态 | 已确认行为；错误码、重试限制和超时策略待确认 |
| 等待输出 | 明确告诉用户需要填写什么、为什么需要、允许格式和示例 |
| 第一阶段 | 使用 Pydantic 校验类型、格式、枚举、范围和跨字段关系 |
| 第二阶段 | 使用业务校验器检查业务规则、权威数据、冲突和可接受性 |
| 失败行为 | 任一阶段失败都保持 `pending`，输出用户可理解、可操作的修正指导，不返回原始异常堆栈 |
| 成功行为 | 两阶段均通过后提交 Slot 值，标记 Slot resolved，并恢复为 `running` 从原暂停点继续；仅在重规划条件成立时触发重新规划活动 |

### ADR-010：Pending Continuation Checkpoint 精确恢复

| 字段 | 内容 |
|---|---|
| 状态 | 已确认行为；Checkpoint 存储模型和 Resume Token 格式待确认 |
| 暂停 | 进入 `pending` 前记录 Plan/Step/Action、State Version、已完成副作用和恢复目标 |
| 恢复 | Slot resolved 后把验证值写入 Context，恢复顶层 `running`，并根据 Checkpoint 中保存的 Action/Plan/Step 引用从暂停点继续 |
| 默认策略 | 从暂停点继续，不重新执行已经完成的 Step，不默认重新规划 |
| 重规划例外 | 用户输入触发 `replan_conditions`、Plan 版本失效、依赖变化或恢复 Guard 失败 |
| 幂等 | 用户输入事件、Slot 提交和恢复 Transition 必须使用幂等键并检查状态版本 |

### ADR-011：四份旧 Tool Register 收敛为一个 Canonical Registry

| 字段 | 内容 |
|---|---|
| 状态 | 已确认收敛方向、首批 Tool Name、工具清单和六字段 Canonical Definition |
| 当前问题 | 旧实现分别在 `tools.schema.json`、`BidToolRegistryVersion`、Task `allowed_tools` 和 Python Adapter Map 保存工具事实，存在声明、可见、允许和可执行集合不一致的风险 |
| 决策 | 新 Pure Agent 只以一个 Canonical Tool Registry 作为工具定义事实源，其他位置只能是自动生成投影、运行时授权结果或具体 Handler 实现 |
| Schema | 输入和输出 Schema 从 Canonical Tool Definition 取得；不得再人工维护第二份 Tool Name 枚举与参数合同 |
| 授权 | 当前会话的可用工具集合是 Router/Gateway 根据 Context 计算的授权结果，不是新的 Registry，也不绑定固定 Task 类型 |
| 执行 | Executor 根据 Canonical Definition 中的执行绑定调用 Handler 或 MCP；Handler 代码可以独立存在，但不能再维护一份重复 Tool Name 映射 |
| 历史兼容 | 不破坏 Phase 4D-3 冻结 Run 的版本引用和审计读取；旧 Registry 表及旧账本不被新 Runtime 写入或依赖 |
| 首批 Active Tools | `bid_document_search`、`enterprise_knowledge_search`、`evidence_read`、`documents_outline` |
| 不进入首批 | `facts.query`、`evidence.compare`、旧 Schema 中未实现或近期不使用的文档、企业、定额和计算工具 |
| 边界 | 文档解析、切块、Embedding 和索引入库属于离线确定性数据处理，不作为首批模型可调用 Tool；比较和综合判断默认属于 Agent 推理，不把 `evidence.compare` 注册为伪工具 |
| 待确认 | description/example 规则、Input/Output Model、Safety Model、Function Calling/MCP 统一绑定细节和错误协议 |

### ADR-012：六个必填核心字段与运行时上下文分离

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 必填核心字段 | `name`、`description`、`input_model`、`output_model`、`execution`、`safety` |
| 可选选择辅助 | 仅在评测触发时允许默认空的 `examples` 或 `few_shots`；不改变六个核心字段的必填地位 |
| 单一身份 | `name` 同时承担内部 Registry Key、Planner `tool_hint` 和模型可见 Function Name；首版使用保守的 Provider-safe 命名规则 |
| 模型合同 | `input_model`、`output_model` 是 Pydantic Model；Function Calling/MCP 所需 JSON Schema 自动生成，不人工维护第二份合同 |
| Runtime Context | `ToolExecutionContext` 由 Executor/Gateway 显式依赖注入，永不出现在模型参数 Schema，不允许模型填写权限和数据 Scope |
| 版本追溯 | 不使用每 Tool 手工 SemVer；冻结 Registry Snapshot/Definition/Input Schema/Output Schema Hash |
| 执行绑定 | `execution` 是 `local` 或 `mcp` 判别联合；Function Calling 是模型协议投影，不是执行类型 |
| 防隐藏依赖 | 权限、Scope 等关键上下文不得由闭包隐式捕获，也不得混入模型可填写 `input_model` |

### ADR-013：Description 场景去重、每轮白名单与按需 Example/Few-shot

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| Description 主规则 | 描述具体正向使用场景、来源边界、返回语义和证据规则；相邻工具不得使用高度重叠的泛化描述 |
| 白名单优先 | 每轮从 Canonical Registry 计算允许集合，只把允许集合投影给模型；不以维护一长串禁止条件作为主要控制手段 |
| 白名单来源 | 当前信息需求、Planner `tool_hint`、用户/角色权限、会话和数据 Scope、数据就绪状态、风险、审批状态、功能开关及 Provider 能力 |
| 二次授权 | 模型返回 Tool Call 后，Gateway 再次验证白名单成员、权限、Scope、参数和调用状态，防止过期投影或伪造调用 |
| 与旧架构区别 | 动态白名单是当前 Turn 的授权结果，不是 Task Category 的静态 Tool Register，不决定固定业务路径 |
| 可选辅助 | `examples` 和 `few_shots` 只在评测证明存在稳定误选或参数构造问题时加入；默认空，不是六个核心必填字段 |
| Output Schema | Function Calling 首版不提前发送完整 Output Schema；模型在执行后接收已验证的实际 `ToolExecutionResult` |
| Strict | Provider 支持时开启；无论是否开启，Runtime Pydantic、业务、权限和 Scope 校验都必须执行 |

### ADR-014：初版采用最小合同并以实践反馈演进

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 决策 | 初版只冻结 Tool 的职责、最小必要参数、最小结果语义和安全校验边界；详细字段与数值上限在实现和评测中迭代 |
| 保留底线 | Pydantic 基本结构校验、额外字段拒绝、Runtime 权限与 Scope 校验、Evidence 溯源和 Output 校验 |
| 暂不冻结 | `top_k`、`max_depth`、字符数、结果数、Context Mode、分页策略、底层检索分数和复杂错误分类 |
| 演进方式 | 实践中发现稳定问题后，先记录问题和证据，再修改 Schema/实现并补专项验证；不为设想中的问题提前增加字段 |
| 与 P-005 的关系 | 开发前仍需 Architecture Baseline，但 Baseline 是最小可实施边界，不是穷尽式详细设计 |

### ADR-015：Router 管相关性，Permission Guard 管授权

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| Router | 只从 Guard 已允许的工具中形成当前模型决策轮次的可见工具集合，不认证身份、不授予权限、不执行工具、不固定业务路径 |
| Guard | Visibility Preflight 做工具级资格检查，Execution Authorization 对模型返回的具体调用、资源引用和当前 Scope 再次授权 |
| Gateway | 校验冻结白名单、Arguments、Guard、Safety/Approval 和 Output 后才允许结果回流主 Agent |
| 初版策略 | 不增加独立路由 LLM；有有效 `tool_hint` 时优先缩小集合，无 hint 时向主 Agent 暴露全部 eligible Tool |
| 动态白名单 | 每个模型决策轮次重新计算并冻结，不是整个用户 Turn 固定一次 |
| 安全不变量 | Router 只能缩小 Guard 允许的集合；任何身份、引用、Scope 或状态不明确时默认拒绝 |
| Agent 性 | 主 Agent/Planner 决定下一动作，Router 只提供合适动作，Guard 只约束合法性，不形成固定 Tool 序列 |

### ADR-016：Planner 按任务复杂度触发

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 决策 | Planner 不在每个用户 Turn 或模型决策轮次固定运行，仅在任务复杂度达到条件时启用 |
| 简单任务 | 允许主 Agent 不创建正式 Plan，直接回答、澄清或执行边界清楚的行动 |
| 复杂任务 | 启用结构化 Planner，生成并校验 Plan 后再按 Observation 动态推进 |
| 不变约束 | 是否启用 Planner 不影响 Tool、权限、State Machine、Evidence 和审计校验 |
| Complexity Gate | 初版由主 Agent 判断；单一目标短证据循环保持 direct，多目标、依赖、跨来源完整研判、明显分支或高风险动作进入 planned |
| 运行中升级 | direct 中发现复杂度提高时触发 `planning_required`，保存并复用已有 Evidence/Observation，只规划剩余任务 |
| 仍待确认 | Planner 是否采用主 Agent 的结构化调用模式，以及具体计划粒度和重规划规则 |

### ADR-017：Agent Task 状态只描述运行生命周期

| 字段 | 内容 |
|---|---|
| 状态 | 已确认五个顶层状态、合法转换和最小 Guard |
| 决策 | Task State Machine 只管理运行、等待和终止等通用生命周期，不编码规划、检索、比较、报告等业务或认知阶段 |
| 执行模式 | `direct/planned` 保存为 Context 字段，模式变化是 running 内事件，不是顶层状态跳转链 |
| 动态活动 | Planner、Tool Call、Observation、Response 和 Plan Step 使用独立记录及事件追踪，Task 顶层保持 running |
| 否决候选 | 撤销 `received → planning → plan_validating → ready → executing → observing → responding` 顶层状态链 |
| 状态机权力 | 允许或拒绝事件与副作用，维护权限、数据、版本和幂等不变量；不决定下一项业务调查内容 |
| 初版状态 | 顶层仅保留 `running`、`pending`、`completed`、`failed`、`cancelled`；`active` 不再单独存在 |
| 合法转换 | running 可自转换或进入 pending/三个终态；pending 可自转换、恢复 running 或进入 failed/cancelled；三个终态拒绝普通后续转换 |
| 最小 Guard | 校验状态版本、Event/副作用幂等、进入 pending 的 Slot/Checkpoint、恢复的两阶段校验、完成条件、致命错误和取消围栏 |

### ADR-018：Slot、PendingContext 与 Continuation 原子恢复

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| Slot | 保存 Task 绑定、语义名称、用户请求、Pydantic Model 引用、业务校验器引用、unresolved/resolved 状态及 candidate/resolved value 引用 |
| PendingContext | 只保存当前 Slot、Checkpoint、`waiting_input/validating_format/validating_business` 阶段、Validation Attempt 和最近错误引用 |
| Checkpoint | 保存 suspended State Version、execution mode、Context Snapshot、通用 suspended Action、Effect Fence、一次性 Resume Token Hash 和 open/consumed/invalidated 状态 |
| direct/planned | 共用 `suspended_action_ref`；direct 不伪造 Plan/Step，planned 通过 Action 记录间接关联 Plan/Step |
| 恢复目标 | 顶层统一恢复为 `running`，不保存 `executing` 等伪状态 |
| 原子性 | Slot resolved、Checkpoint consumed、Task pending→running、state_version 更新和已验证值合并必须同一事务或等价原子提交 |
| 幂等 | Effect Fence、Event ID、State Version 和一次性 Resume Token 防止重复模型/工具调用、重复消息和重复恢复 |

### ADR-019：Planner 是内部结构化调用并采用有限步滚动计划

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 组件形态 | Planner 具有独立 Port、Prompt、输入投影、Plan JSON Schema、审计和评测，但部署在 Main Agent Runtime 内，不是第二个持续 Agent 或独立服务 |
| 模型 | 初版默认复用 Main Agent 模型配置；只有 Complexity Gate 进入 planned 才产生单独 Planner 调用，未来可按评测替换实现 |
| 规划粒度 | 使用有限步滚动计划，只规划当前已知且必要的信息/决策子目标并明确 `next_decision`，不穷尽未知远期路径 |
| Step | 描述可验证的信息或决策子目标；Tool Call 是 Step 内动态 Action，不把 Search→Read 等函数调用固化为状态或节点 |
| 执行边界 | Planner 不直接执行 Tool；Main Agent 在 running 内经 Router、Guard、Gateway 和 Executor 执行并接受 Observation |
| 修订 | 仅在目标/范围变化、关键假设被推翻、重要子目标新增、证据冲突或原路径失效等实质条件下创建新 Plan Version |
| 查询认知能力 | Query Decomposition/Expansion 初版不注册为 Tool，由 Main Agent/Search 执行路径先承担，待 RAG 评测证明需要后再提取 |

### ADR-020：意图理解使用独立接口而非固定标签分类模型

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 接口 | 保留逻辑独立的 `IntentUnderstandingPort`，用于结构化合同、Pydantic 校验、审计、评测和未来替换 |
| 初版实现 | 复用 Main Agent 模型和完整有效 Context，联合提取开放目标、信息需求、阻塞性缺失、来源提示、execution mode 和 next action |
| 不采用 | 不部署独立固定标签分类模型，不建立招标问答/企业问答/风险研判等互斥业务意图枚举，不注册 intent classifier Tool |
| 澄清 | 只有阻塞、不可从授权来源检索、不可用合理假设/unknown 处理且能定义校验合同的信息才创建 Slot |
| 拆解 | 信息需求拆解属于语义层；Query Rewrite/Expansion 和检索参数属于 Retrieval 层，不把单一事实的多个查询表达拆成多个 Plan Step |
| 快速路径 | 未来只有评测证明高频封闭意图适合分类时才增加轻量分类器；低置信或未命中必须回退 Main Agent 开放式理解 |
| 安全 | Understanding Decision 和 source hints 均不是授权，Router/Permission Guard 仍是工具与资源访问权威 |

### ADR-021：离线 RAG 使用不可变派生产物与三层证据合同

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 现有资产 | 复用隔离的 Document Version、Parse Run/Head、`bid.evidence.chunk.v2` 和 Retrieval Index/Head，不创建平行 RAG 事实源；历史 Tender Evidence Ingestion 只作参考 |
| 知识域 | 招标资料和企业知识共享逻辑 Parse/Chunk 合同，但 Source Domain、Scope、Head 和索引严格隔离；授权 Scope 由 Runtime Context/Guard 注入 |
| 解析 | 产生可追溯的 Structured Blocks；Native/OCR 分源，表格、标题层级、页码、BBox、Sheet/Cell 等 Locator 和解析质量为一等元数据 |
| 切块 | 保留 `section_parent -> retrieval_child -> evidence_atom`；Parent 用于上下文，Child 用于召回，只有 Atom 可直接引用 |
| 边界与重叠 | 结构和语义边界优先，长度由版本化 Chunk Profile 控制；只在长 Block 被迫拆分时使用可追溯重叠，不做机械全局重叠 |
| 版本治理 | Source、Parse、Chunk、Embedding 和 Index 派生产物不可变，以内容/Profile Hash 幂等；旁路构建并校验后原子切换 Ready Head，旧版本进入 Stale |
| 撤销与删除 | 撤销后立即从在线 Head/授权范围移除，并以 Tombstone 或新索引阻断召回；历史引用保留撤销状态，物理清理由独立保留策略和授权决定 |
| Agent 边界 | 离线管线只生产可检索知识，不预设在线研判路径；在线召回、Query Rewrite、BM25/向量/RRF 和 Reranker 在 T05/T06 设计 |

### ADR-022：在线召回采用受控 Query Strategy 与词法主导 Hybrid Default

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 职责边界 | Main Agent/Planner 负责业务目标、Information Need 和知识域选择；Search 内 `RetrievalQueryStrategyPort` 只优化单一信息需求，不改变目标、Tool 或 Scope |
| Query Strategy | 初版复用确定性 Query Optimizer，保留 Original Anchor、有界变体、稳定去重和 Hash；不额外调用 LLM，生成式 Rewrite 由后续评测触发 |
| 默认召回 | 使用 Profile 驱动、词法主导的 Hybrid Default；BM25F 负责精确术语和结构字段，Semantic 只召回 Retrieval Child 并补充措辞变化 |
| 模型边界 | 模型不选择 `exact/semantic/hybrid`，不填写通道权重、RRF K、底层候选深度或索引参数；这些由版本化 Retrieval Profile 治理 |
| 过滤 | Permission Scope、Ready Snapshot、有效期、撤销和 Metadata Filter 在召回前强制生效；模型限定只能缩小 Runtime 授权集合 |
| 融合 | 多查询/多通道使用 Rank-only Weighted RRF，不直接比较 BM25/Cosine 分数；按稳定 Child Key 去重并使用确定性 Tie-break |
| 证据边界 | Search 只返回 `retrieval_child + is_citable=false` Candidate；形成事实或引用前通过 `evidence_read` 升级为可引用 Evidence Atom |
| 失败语义 | No Result 不等于资料不存在；Semantic 不可用时禁止旧向量结果，只允许带明确 Warning 的 Lexical-only Degraded；Scope/Head/Hash 越界直接失败 |
| 知识域 | 招标与企业知识使用独立 Adapter/Profile/Index，共用 Query Plan、Candidate 和 Evidence Ref 逻辑合同；跨域取证和综合由 Main Agent 完成 |
| 后续边界 | Reranker、证据权威性、冲突处理和引用验证进入 T06；算法数值由获得授权后的检索评测调整 |

### ADR-023：Reranker 与证据权威分离，Atom Read 是唯一证据升级边界

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 能力分离 | Reranker 只调整冻结 Retrieval Child Candidate 的查询相关性；不能召回新内容、改变 Scope/Role、判断事实真假或产生引用 |
| 触发 | 使用 Search Handler 内部 `RerankPolicyPort`；初版默认 `skip`，只在知识域存在已批准 Profile 且 Runtime 条件/预算允许时启用；模型不能传 `rerank=true` 或底层参数 |
| 重排不变量 | 输入 Candidate Window 冻结；保留 Hybrid/词法锚点，只允许有界 Promotion；无合法 Promotion 时保持 Fusion Baseline 有序恒等；模型、输入和结果全量 Hash 追溯 |
| 失败边界 | 可选 Reranker 不可用或输出无效时丢弃全部重排结果并返回未修改 Fusion Baseline及 Warning；Scope/Snapshot/上游 Hash 血缘异常时整个 Search fail-closed |
| 证据升级 | `evidence_read` 是 `retrieval_child + non-citable` 升级为 `evidence_atom + citable` 的唯一边界；每个 Atom 独立校验 Scope、版本、Role、Text/Locator Hash、撤销和 Span 完整性 |
| 三层判断 | `EvidenceIntegrityGuard` 管确定性完整性，版本化 `SourceAuthorityPolicy` 管明确替代/有效期规则，Main Agent `EvidenceAssessmentPort` 管语义支持、部分、冲突和未知 |
| Grounding | 内部 Record 区分 `source_fact/inference/unknown` 与 `supported/partial/conflicted/unsupported/unknown`，只固定证据责任，不固定用户输出格式 |
| 冲突 | 有明确修订/替代关系时按 Authority Policy 处理；无确定优先关系的有效证据必须保留为 conflicted，不按 Search/Rerank Score 选择 |
| 引用 | 最终引用必须通过 Citation Integrity Guard；验证当前权限、Source Version/Snapshot、Atom Role、Text/Locator Hash，直接引语还需精确 Span/Quote Hash |
| 验证模型 | 初版不增加独立 Evidence Verifier 模型；只有高风险场景和评测证明需要时才替换/扩展 `EvidenceAssessmentPort` |

### ADR-024：模型侧 Function Calling 与执行侧 Local/MCP 使用统一 Canonical 协议

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 协议分层 | Main Agent 统一使用 Function Calling 提出结构化动作；Local Handler/MCP 只是 Canonical Tool 的执行绑定，模型不感知执行位置 |
| 单一事实源 | Canonical Registry 与 Pydantic Input/Output Model 是唯一工具合同事实源；Provider/MCP Schema 是投影或外部依赖合同，MCP Discovery 不自动注册或暴露 Tool |
| Call Proposal | Provider Tool Call 必须规范化并绑定 Model Turn、Provider Call ID、Registry/Visible Tools Hash 和 State Version，再进入 Gateway |
| 权威顺序 | Provider strict/MCP Schema 只做早期防错；Runtime Pydantic、业务/权限/Safety、Effect Fence、Output/Provenance 校验为最终权威 |
| Context | Local 通过显式 DI 接收最小 `ToolExecutionContext`；MCP 使用短期最小权限 Auth Context，不把完整 Runtime State、权限或 Scope 塞入模型 Arguments |
| MCP 输出 | 只接受 Structured Content，并重新通过 Canonical `output_model`；MCP 原始文本、日志、Stack Trace 和自定义元数据默认不回流模型 |
| Observation | Tool Message 精确关联原 Provider Call ID，内容只包含 Canonical `ToolExecutionResult`；内部 Call/Schema/Scope/Attempt/时延信息保留在 Ledger |
| 执行形态 | 首批四个只读 Tool 直接有界异步执行，不把旧 Durable Dispatch/Worker 队列设为必经层；多 Tool Call 初版按响应顺序执行 |
| 可靠性 | 只读 Tool 可按同一 Idempotency Key 有界重试；Deadline、取消和 Effect Fence 阻止迟到结果；写操作/长任务的耐久投递以后单独设计 |
| 大结果 | 具体 Output Model 必须有界；完整合法结果可进入受控 Result Store，模型只接收安全投影；初版不恢复通用 `tool_result.read_slice` |
| 首批绑定 | `documents_outline` 推荐 Local；`bid_document_search` 可复用 Evidence MCP；`enterprise_knowledge_search` 初版 Local；`evidence_read` 推荐 Local Facade 统一解析双知识域 Evidence Ref |

### ADR-025：四层 Memory 使用受控写入、Source 依赖和可遗忘治理

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 逻辑分层 | Working Memory 绑定当前 Task；Conversation Memory 绑定当前会话；Project/Assessment Memory 绑定租户内业务对象；User Memory 只保存受控的跨会话稳定偏好 |
| 非物理强制 | 四层是职责和 Scope 合同，不预设四套服务、表或向量索引；实现按访问、保留和容量需求选择 |
| 资产分离 | Task State/Checkpoint 管精确恢复，Message 管实际对话，Observation 管工具结果，Evidence/Business Record 管事实，Memory 管以后可复用的派生 Context，Context Projection 管当前轮次输入 |
| 权威 | Memory 只能保留或降低 Source 权威，不能把用户转述、模型摘要、`unknown` 或推测升级为已证实事实；重要结论回到当前 Source Head、Evidence Atom 和 Grounding Record 验证 |
| 写入控制 | Runtime 自动记录当前 Task/Message；持久项目记录必须有 Scope、Source、Version、Grounding 和资格；跨会话用户偏好需要明确表达或确认 |
| 提交形态 | Main Agent 可以提出结构化 `memory_candidate`，最终由 Runtime Memory Policy Guard 执行 Pydantic、Scope、Source、授权、保留和版本校验；首版没有通用 `write_memory` Tool 或独立 LLM Memory Extractor |
| 禁入 | Chain-of-Thought、隐藏 Prompt、凭证秘密、无界原文/日志、未验证猜测、越权指令和无用途敏感个人信息不得进入长期 Memory |
| 读取 | 先做 Auth/Scope，再做当前版本、有效性、Source Head、相关性和去重；当前原文/Evidence 优先于摘要；Memory 按不可信数据投影，不能覆盖 System Policy、Tool Guard 或 Permission Guard |
| 纠正与失效 | Message 不可变；纠正和更新产生新 Memory Version 并 Supersede 旧记录；Source/权限变化使用 Stale/Revoked，证据冲突使用 Conflicted，不静默重定向到新版本 |
| 遗忘 | 用户有权查看、纠正和遗忘允许其管理的 Memory；遗忘先 Tombstone 并立即停止检索/驱逐缓存，物理清除遵循保留和审计策略；不越权删除独立事实源 |
| Pending 恢复 | Continuation Checkpoint 保存暂停时 Working Memory/Context Snapshot 引用；Slot 通过两阶段校验后原子恢复，Memory 不替代 Checkpoint，也不默认重跑已完成调用 |
| 防 Workflow | Memory Commit 是事件驱动且可为零，不是所有 Task 必须经过的固定阶段；窗口预算、摘要压缩和 Context 组装算法在 T11 单独设计 |

### ADR-026：Context 使用六个 Lane、动态预算和分层压缩

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 组件职责 | `ContextAssemblerPort` 在每次实际模型调用前完成收集、Scope/Version 校验、预算、选择、压缩、安全投影和 Snapshot；不识别意图、不生成 Plan、不授权、不判断事实，也不决定下一业务动作 |
| 逻辑 Lane | Policy/Protocol、Active Control、Tool Contract/Active Calls、Observation/Grounding、Relevant Interaction、Historical Memory；Lane 决定保护和选择，不构成固定调用顺序或 Task 状态 |
| 预算 | 使用“硬保护区 + 动态弹性区”；Provider/Model Profile 管 Capacity、Tokenizer 和输出能力，Context Profile 管 Runtime 上限、输出预留、安全余量、保护和压缩策略；数值以后由评测校准 |
| 不可无声截断 | 当前 Policy、User Message、Task/Slot 控制不变量、Visible Tool Schema、活动 Tool Pair、最新关键纠正和当前结论所需 Evidence Atom 必须精确保留或显式失败/降级 |
| Token 权威 | 最终 Provider Message/Function Schema 序列化后使用匹配 Tokenizer 计数；不可用时采用保守 Estimator + Safety Margin；字符估算不作为是否调用模型的最终依据 |
| 压缩阶梯 | L0 无损去重，L1 结构化投影，L2 旧 Conversation 结构化摘要，L3 索引 + 原文块回取，L4 显式限制、动态拆分或缩小范围；短 Context 不摘要 |
| Summary | Conversation Summary 是派生 Memory，保存 Message Ref、Source Range Hash、Valid Through、版本和限制；关键决定/纠正回取原文，不能只做失去 Source 的递归摘要 |
| Tool/Evidence | 活动 Provider Tool Pair 保持完整；旧 Tool Result 只投影 Canonical Observation；Search Candidate 非可引用，当前结论所需 Evidence Atom 保持原文、Locator、Version、Hash 和 Grounding |
| Assembly Result | 区分 `ready/ready_with_limits/needs_narrowing/blocked_on_user/failed`；内部预算问题不自动创建 Slot，只有确实需要用户选择或输入时才进入 `pending` |
| Snapshot | 每个 Intent Understanding、Planner 或 Main Agent Model Call 冻结独立 Context Snapshot，记录 Included/Excluded/Compression/Token 及 Policy/Profile/Registry/Source Version/Hash，并由 Model Call/Checkpoint 引用 |
| 安全 | 所有 Document/Evidence/Memory/历史 Message/Tool Output 按不可信数据投影；真实 Tool Call 仍只接受 Provider Function Calling 并经过 Registry、Guard、Gateway 和 Effect Fence |
| 初版范围 | 先实现确定性选择与结构化投影；条件式模型摘要、数值阈值和语义历史索引只有在授权评测证明需要后再启用或调整 |
| 防 Workflow | Context Assembly 是模型调用的输入治理管线，不规定 Agent 的意图、规划、检索、回答顺序；`consumer` 只选择投影 Profile，不是顶层状态 |

### ADR-027：自由回答通过 AnswerDraft、Grounding Binding 和 Runtime Citation 安全发布

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 双层输出 | 用户看到自由自然语言；Main Agent 与 Runtime 之间使用 Narrative/Statement/Limitation/Interaction 通用 Block 的严格 Pydantic `AnswerDraft`，不固定完整研判、问答或澄清的业务章节 |
| Statement | 所有 Material Fact、Calculation、Inference、Recommendation 必须绑定当前 Context Snapshot 中合法 Grounding Ref；Narrative 不承载会影响判断的项目事实 |
| 认知维度 | Claim Type、Epistemic Status、Source Basis 分离；Supported/Partial/Conflicted/Unknown 使用不同表达，Document/Enterprise/Business/System/User/Formula 来源不混淆 |
| Citation 控制权 | 模型只选择 Grounding/Quote Ref；Runtime `CitationProjector` 从有效 Source Link 生成 Title、Locator、Version、受控链接和冲突分组，模型不能自由填写 URL、页码或内部 ID |
| 支持矩阵 | Material Fact 需要当前 Citation；Calculation 绑定输入和 Formula/Rule Version；Inference/Recommendation 绑定前提/触发；Conflict 引用各方；Unknown 只展示检索/缺失范围，不生成假支持 |
| Guard 权威 | 确定性校验覆盖 Schema、Snapshot/State、Auth/Scope、Source Head、Version/Hash、Grounding Status、支持矩阵和精确 Quote Span；不声称仅靠 Pydantic 即可证明自由文本语义真实性 |
| 限制语义 | 区分 Retrieval No Result、Source Not Provided、Evidence Insufficient、Evidence Conflicted、Source Stale/Unavailable、Permission Limited、Tool/Index Degraded、Context Limited |
| 修复与回退 | 无效 Draft 不发布；可以把安全 Validation Error 返回同一 Main Agent 做有界修复，仍失败时只用已验证 Grounding/Limitations 构造最小安全回退或返回生成失败 |
| Streaming | Provider 到 Runtime 可以流式生成；首版完整缓冲、Schema/引用校验并 Commit 后才发布 Rendered Response/Citation，不做未经验证 Claim 的抢跑式输出 |
| 版本治理 | 已发送 Response 不原地改写；用户纠正和 Source/权限变化产生新 Response/Supersede，旧 Response 可标记 Stale/Revoked Support，历史 Citation 打开时重新授权 |
| 风格 | 语言、长度、专业程度和格式可结合当前问题与 User Memory；偏好不能隐藏证据不足、冲突、权限或安全限制 |
| 初版组件 | Main Agent 生成 Draft；Answer Contract/Grounding Guard、Citation Integrity Guard、CitationProjector、Renderer 同进程实现；首版不增加 Answer Writer/Verifier Agent |
| 防 Workflow | `draft/validated/committed/rejected/stale` 是回答 Artifact 发布状态，不是 Agent Task 顶层状态；内部合同不规定用户问题必须经过固定报告路径 |

### ADR-028：T13 按六项最小运行护栏收束

| 字段 | 内容 |
|---|---|
| 状态 | 已确认 |
| 最小护栏 | 资源/预算上限、重复/无进展循环防护、幂等/Effect Fence、取消/迟到结果隔离、Direct/Durable 触发边界、Checkpoint/Recovery |
| 参数治理 | 具体阈值由版本化 Runtime Profile 配置；架构不冻结永久数值 |
| 延后项 | 高级 Retry/Failover、精细并发、完整 Ledger 字段、复杂 Reconcile 和评测参数进入开发 Backlog |
| 防 Workflow | Runtime Guard 只限制资源、合法性、Effect 和恢复，不规定 Intent、Plan、Search、Tool、Answer 的顺序，也不增加业务阶段状态 |

## 6. 候选总体架构

状态：**候选方案，未确认**

### 6.1 离线知识工程

```text
企业资料治理
    → 文档解析
    → 内容切块与元数据
    → Embedding
    → BM25/向量等索引
    → 版本、删除和重建管理
    → 离线质量评测
```

离线知识处理允许采用确定性 Pipeline。它负责提供可检索环境，不负责决定在线 Agent 当前应调查什么。

### 6.2 在线 Agent

```text
用户消息、文件或补充信息
             ↓
        Main Agent
             ↓
       判断当前任务复杂度
        ├── 简单 → 直接回答 / 澄清 / 直接行动
        └── 复杂 → LLM Task Planning
                         ↓
              产生动态下一行动
                         ↓
                  Tool Router
                         ↓
               Tool / Retrieval / MCP
                         ↓
                   Observation
                         ↓
             更新认知并继续、升级规划或停止
```

此图只表达职责关系。Planner 已确认按复杂度触发，但复杂度 Gate 和 Planner 是否作为主 Agent 的一种结构化调用模式仍待确认；不要求存在独立 Planner 服务或独立 Tool Router 服务。

## 7. 当前重点：Task Planner

### 7.1 用户已经确认

- Agent 需要任务规划功能；
- 应由 LLM 判断完成任务需要采用什么路径；
- 路径必须根据具体问题动态产生；
- Planner 仅在任务复杂度达到条件时启用，不是每轮固定前置步骤。

### 7.2 当前候选方案

状态：**Planner 触发后的有限步滚动执行方式已确认**

采用动态滚动规划：

```text
形成高层策略
    → 只承诺当前下一行动
    → 获得观察结果
    → 修正或替换原计划
    → 继续、澄清、回答或停止
```

候选理由：它可以保留总体方向，同时避免初始计划锁死后续执行。

### 7.3 Planner 按任务复杂度触发

#### 7.3.1 已确认原则

Planner 不在每个用户 Turn 或模型决策轮次固定运行。简单任务可以直接回答、澄清或行动；复杂任务才生成正式、结构化 Plan。没有 Plan 不代表绕过 Runtime 控制，直接模式仍受 State Machine、Tool Router、Permission Guard、Pydantic、Evidence 和审计约束。

#### 7.3.2 初版 Complexity Gate（已确认）

初版由主 Agent 在理解当前用户目标时同时判断 `direct` 或 `planned`，不增加独立复杂度分类器。复杂度 Gate 只判断是否需要正式 Plan，不输出固定业务类别。

保持 `direct` 的场景：

- 可以直接利用可靠会话上下文回答；
- 只需向用户澄清一个关键问题；
- 当前只有一个清晰信息需求，范围和完成条件明确；
- 只需一个边界清楚的短证据循环，例如围绕同一个问题完成 Search 和 Evidence Read；
- 只需查看某份文档结构或解释已有 Evidence。

触发 `planned` 的信号：

- 用户目标包含多个相互独立的子问题，需要跟踪覆盖情况；
- 子任务之间存在明确依赖，后一步必须等待前一步结果；
- 需要组合招标要求与企业知识进行跨来源比较或形成完整研判；
- 用户要求全面审查、完整风险清单、系统性评估或具有明确交付标准的报告；
- 执行中出现冲突证据、明显分支、范围扩大或多个未知项，直接模式已难以判断完成条件；
- 未来出现写入、外部副作用、高成本或需要审批的动作，需要在执行前明确步骤和完成标准。

初版不使用固定工具调用次数、Token 数或文档页数作为唯一触发条件。一个问题即使需要 Search→Read 两次 Tool Call，只要仍是单一目标和单一路径，也可以保持 `direct`；反之，一个没有 Tool Call 的多目标分析也可能需要 Planner。

#### 7.3.3 运行中升级（已确认）

```text
direct
  → 回答 / 澄清 / Tool Call
  → Observation 显示任务仍是单一路径：继续 direct
  → Observation 显示多目标、依赖、冲突或范围扩大：触发 planning_required
  → 保存已有 Observation，生成 Plan，从当前位置继续
```

从 `direct` 升级为 `planned` 时不得丢弃或重复执行已经接受的 Tool Result；Planner 将已有 Evidence 和 Observation 作为输入，规划剩余任务而不是从头再跑。是否允许从 `planned` 降级回 `direct` 暂不作为初版必需能力。

#### 7.3.4 尚未决定

1. Complexity Gate 是否采用主 Agent 的一个结构化控制字段，还是完全由 Runtime 状态推断；
2. 是否允许一次规划多个并行工具调用；
3. Plan/Step Runtime 的具体持久化表、恢复和版本压缩方式；
4. `steps` 和用户可见计划投影的完整字段约束；
5. Plan JSON Schema dialect、兼容规则和失败修复策略；
6. Planner 的 Token、时间、费用和循环限制；
7. 如何评测规划正确性，而不是只评测最终回答。

### 7.4 Planner 输出协议

#### 7.4.1 已确认要求

Planner 的机器输出必须满足：

1. 是一个完整、可解析的 JSON 对象；
2. 声明 Schema 和协议版本；
3. 通过对应 JSON Schema 校验后才能驱动后续动作；
4. 能表达当前目标、计划状态、动态步骤、下一决策和重规划信息；
5. 能产生单独的用户可见计划投影；
6. 支持计划版本更新，不能静默覆盖已经展示或执行的计划。

以下顶层字段已确认保留：

```text
goal_summary
completion_criteria
steps
next_decision
replan_conditions
user_projection
```

Schema 固定的是通信结构，不固定任务数量、业务阶段、工具数量或具体路径。

#### 7.4.2 Planner Step JSON 实例

状态：**Step 字段框架已确认；字段约束待逐项确认**

```json
{
  "schema_version": "agent.plan.v1",
  "plan_id": "plan_xxx",
  "plan_version": 1,
  "goal_summary": "判断企业香港项目是否满足本次类似业绩要求",
  "completion_criteria": [
    "确认招标文件中的类似业绩定义",
    "取得企业项目的可比事实",
    "完成关键维度比较并说明未知项"
  ],
  "status": "active",
  "steps": [
    {
      "id": "S1",
      "title": "确认招标认定标准",
      "description": "从当前招标资料中查找类似业绩的定义、时间范围、规模和类型要求",
      "dependencies": [],
      "tool_hint": "bid_document_search",
      "expected_output": "带原文引用的类似业绩认定条件",
      "output_schema": {
        "type": "object",
        "required": ["criteria", "evidence_refs"],
        "properties": {
          "criteria": {"type": "array", "items": {"type": "string"}},
          "evidence_refs": {"type": "array", "items": {"type": "string"}}
        },
        "additionalProperties": false
      },
      "risk_level": "low"
    },
    {
      "id": "S2",
      "title": "查询香港项目事实",
      "description": "从企业离线 RAG 中查找项目日期、规模、类型和工作内容",
      "dependencies": [],
      "tool_hint": "enterprise_knowledge_search",
      "expected_output": "结构化企业项目事实及证据引用",
      "output_schema": {
        "type": "object",
        "required": ["project_facts", "evidence_refs"],
        "properties": {
          "project_facts": {"type": "object"},
          "evidence_refs": {"type": "array", "items": {"type": "string"}}
        },
        "additionalProperties": false
      },
      "risk_level": "low"
    },
    {
      "id": "S3",
      "title": "比较类似业绩匹配情况",
      "description": "比较招标认定条件与香港项目事实，说明满足项、风险项和未知项",
      "dependencies": ["S1", "S2"],
      "tool_hint": "evidence.compare",
      "expected_output": "带证据引用的匹配判断和信息缺口",
      "output_schema": {
        "type": "object",
        "required": ["matched", "risks", "unknowns", "evidence_refs"],
        "properties": {
          "matched": {"type": "array", "items": {"type": "string"}},
          "risks": {"type": "array", "items": {"type": "string"}},
          "unknowns": {"type": "array", "items": {"type": "string"}},
          "evidence_refs": {"type": "array", "items": {"type": "string"}}
        },
        "additionalProperties": false
      },
      "risk_level": "medium"
    }
  ],
  "next_decision": {
    "type": "execute_step",
    "step_id": "S1"
  },
  "replan_conditions": [
    "未找到明确认定标准",
    "证据之间存在冲突",
    "用户补充新的限定条件"
  ],
  "user_projection": {
    "summary": "我会分别确认招标要求和企业项目事实，然后进行证据比较。",
    "visible_step_ids": ["S1", "S2", "S3"]
  }
}
```

Step 字段框架和 `expected_output`/`output_schema` 双轨制已经确认；字段类型、枚举值、长度限制和 Output Schema dialect 尚未冻结。

#### 7.4.3 候选流式事件

状态：**候选协议，待确认**

```text
plan.started
plan.published
plan.step.started
plan.step.completed
plan.step.failed
plan.revised
plan.waiting_user
plan.completed
```

每个事件应至少携带 `plan_id`、`plan_version`、事件时间和用户可见安全投影。内部 Planner JSON 可以由模型流式生成，但在完整接收并通过 Schema 校验前，不允许执行其中的工具动作。

### 7.5 Step、Slot 和状态机

#### 7.5.1 Planner Step 字段

状态：**字段框架已确认**

```json
{
  "id": "S1",
  "title": "查询官方财报",
  "description": "优先在企业投资者关系网站查找",
  "dependencies": [],
  "tool_hint": "finance.official_report.search",
  "expected_output": "YYYY-MM-DD",
  "output_schema": {
    "type": "string",
    "format": "date"
  },
  "risk_level": "low"
}
```

| 字段 | 已确认语义 |
|---|---|
| `id` | Step 唯一标识，供依赖、执行状态和流式事件引用 |
| `title` | 面向用户和运行日志的简短步骤标题 |
| `description` | LLM 对子任务目标、范围和优先策略的详细描述 |
| `dependencies` | 当前 Step 依赖的其他 Step ID；内容由 Planner 动态生成 |
| `tool_hint` | Planner 建议使用的 Tool Registry ID 或 Capability ID；必须可解析，但不直接绕过 Tool Router 和权限校验 |
| `expected_output` | 自然语言描述该步骤期望得到的内容或格式，用于指导 Planner、模型和用户理解 |
| `output_schema` | 结构化约束 Step 结果，由运行时强制校验 |
| `risk_level` | 执行该步骤的风险等级，用于审批、工具约束和用户提示 |

Step 只描述 Planner 规划出的子任务，不承载 Slot，也不把用户缺失信息提前写入计划。`expected_output` 和 `output_schema` 必须同时保留；Step 的字段类型、长度、`risk_level` 枚举、依赖合法性以及 Output Schema dialect 仍需继续讨论。

#### 7.5.2 Slot 的产生时机

状态：**已确认**

Slot 不在 Planner 初始 `steps` 中。Agent 执行任务时，如果确认继续完成任务必须获得用户提供的信息，才创建独立 Slot：

```text
执行任务
   ↓
发现关键信息不足
   ↓
创建独立 Slot
   ↓
任务运行状态挂起为 pending
   ↓
向用户请求所需信息
   ↓
校验用户输入
   ├── 通过 → 解除 pending，从 Continuation Checkpoint 续跑
   └── 失败 → 保持 pending，请求用户修正
```

Slot 可以引用触发它的 Plan 和 Step，但 Slot 不属于 Step Schema，也不要求 Planner 在初始计划时预知所有可能缺失的信息。

#### 7.5.3 Slot 最小可恢复结构

状态：**已确认**

```json
{
  "slot_id": "slot_xxx",
  "task_id": "task_xxx",
  "name": "contract_date",
  "request_message": "请提供香港项目合同签订日期，例如 2025-03-18。",
  "input_model_ref": "slot.date.v1",
  "business_validator_refs": ["project.contract_date.allowed_range.v1"],
  "status": "unresolved",
  "candidate_input_ref": null,
  "resolved_value_ref": null
}
```

最小字段语义：

| 字段 | 用途 |
|---|---|
| `slot_id` / `task_id` | 唯一定位 Slot 并绑定当前 Agent Task |
| `name` | 稳定的语义名称，供主 Agent、校验器和用户输入抽取引用 |
| `request_message` | 持久化已经展示给用户的需求、原因、格式和必要示例，恢复后无需重新猜测如何提问 |
| `input_model_ref` | 指向 Runtime 可解析的 Pydantic Model；JSON Schema 由该模型生成，不再维护第二份手写 Schema |
| `business_validator_refs` | 格式校验通过后必须执行的业务校验器；允许空集合 |
| `status` | 首版只使用 `unresolved` 或 `resolved`；等待和验证阶段由 PendingContext 表达 |
| `candidate_input_ref` | 指向最新用户输入事件或候选值记录，不在 Slot 中复制整段消息 |
| `resolved_value_ref` | 指向通过格式和业务校验后的类型安全值；未解决时必须为空 |

`plan_id`、`origin_step_id`、`input_examples`、`validation_attempts` 和完整错误列表不进入最小 Slot。来源 Plan/Step/Action 由 Continuation Checkpoint 的通用 `suspended_action_ref` 追踪；示例已经包含在 `request_message`，验证尝试和错误进入独立 Validation Ledger。这样同一结构同时支持 direct 和 planned 模式。

#### 7.5.4 运行状态机

状态：**`pending` 两阶段验证行为、五个顶层状态及 Transition Guard 已确认**

已确认的任务运行状态：

```text
running
  ├──→ running      接受 Action、Tool Result、Observation、Plan 更新或模式变化
  ├──→ pending      需要用户 Slot，当前任务挂起
  ├──→ completed
  ├──→ failed
  └──→ cancelled

pending
  ├──→ running      Slot 两阶段校验通过，从 Continuation Checkpoint 续跑
  ├──→ pending      输入校验失败或继续等待用户
  ├──→ cancelled
  └──→ failed       出现不可恢复的 Runtime/Checkpoint 错误
```

`active` 不再作为独立顶层状态；它与 `running` 的含义重叠，二者并存会产生“何时 active、何时 running”的无效转换。`planning`、`executing`、`observing` 和 `responding` 也不属于顶层 Task 状态，它们发生在 `running` 内，并由 Action/Event、Plan、Tool Call、Observation 或 Response 记录表达。

Task 为 `pending` 时使用以下已确认的最小 `PendingContext`：

```json
{
  "slot_ref": "slot_xxx",
  "checkpoint_ref": "checkpoint_xxx:v12",
  "phase": "waiting_input",
  "validation_attempt_ref": null,
  "last_error_ref": null
}
```

`phase` 只允许 `waiting_input`、`validating_format`、`validating_business`。`validation_attempt_ref` 关联当前幂等验证尝试，`last_error_ref` 用于恢复后重新展示最近一次友好修正指导；尝试历史和详细错误不复制进 Task State。Task 不在 `pending` 时，整个 `PendingContext` 必须为空。

`pending` 候选复合子状态：

```text
pending.waiting_input
   └──user.input.received──→ pending.validating_format

pending.validating_format
   ├──通过──→ pending.validating_business
   └──失败──→ pending.waiting_input

pending.validating_business
   ├──通过──→ resolved → continuation.resume → running
   └──失败──→ pending.waiting_input
```

- `pending.waiting_input`：已经向用户输出 Slot 请求，持久等待用户输入；
- `pending.validating_format`：候选输入已经填入 Slot，正在通过 Pydantic 做参数格式校验；
- `pending.validating_business`：格式已经正确，正在执行独立业务规则校验；
- `resolved`：两阶段校验均通过，Slot 可以提交并从原暂停点恢复任务；
- 任一校验失败都回到 `pending.waiting_input`，并携带新的用户修正指导。

等待期间不进行空转轮询。State Context、Slot、Plan 和关联引用持久化后，由新的用户输入事件唤醒验证流程。

Planner Step 定义中不保存这些状态。如果需要展示每个 Step 的执行进度，应由独立 Runtime State 按 Step ID 维护，不能反向污染 Planner 的 Step Schema。该 Runtime State 的具体结构尚未确认。

#### 7.5.5 程序化校验分层

状态：**已确认需要程序化校验；技术组合待确认**

1. **JSON Schema 校验**：验证 Planner、Tool 和 Slot 的外部协议；
2. **Pydantic 字段校验**：验证 Python Runtime 中的类型、格式、枚举、范围和默认值；
3. **Pydantic Model Validator**：验证跨字段关系；
4. **业务校验器**：验证仅靠类型系统无法判断的业务条件；
5. **证据校验器**：验证输入是否具有合格来源，不能只验证格式正确。

候选跨字段规则包括：

```text
task_runtime.status == "pending"
    → pending_context 非空
    → pending_context.slot_ref 和 checkpoint_ref 可解析
    → 对应 Slot 存在且 status == "unresolved"

pending_context.phase in {"validating_format", "validating_business"}
    → Slot.candidate_input_ref 非空
    → pending_context.validation_attempt_ref 非空

slot.status == "resolved"
    → resolved_value_ref 非空
    → input_model_ref 和所有 business_validator_refs 已通过
    → 对应 Checkpoint 必须在同一恢复事务中从 open 变为 consumed

格式校验或业务校验失败
    → Slot.status 保持 unresolved
    → resolved_value_ref 仍为空
    → pending_context.last_error_ref 非空
    → 返回 pending 前产生用户可理解的修正提示

step.dependencies
    → 每个依赖 ID 必须存在
    → 不能依赖自身
    → 不得形成循环依赖
```

Pydantic 可以确认“合同日期是合法日期”，但不能单独确认“这个日期真实属于香港项目”。真实性仍需要企业证据、用户确认或其他权威来源。

#### 7.5.6 用户友好错误协议

状态：**行为已确认；字段和错误码待冻结**

候选错误投影：

```json
{
  "slot_id": "slot_xxx",
  "stage": "format_validation",
  "code": "SLOT_DATE_FORMAT_INVALID",
  "field": "contract_date",
  "message": "合同日期格式无法识别。",
  "guidance": "请使用 YYYY-MM-DD 格式，例如 2025-03-18。",
  "retryable": true
}
```

业务校验失败示例：

```json
{
  "slot_id": "slot_xxx",
  "stage": "business_validation",
  "code": "SLOT_CONTRACT_DATE_OUT_OF_ALLOWED_RANGE",
  "field": "contract_date",
  "message": "该日期不在本次类似业绩允许的时间范围内。",
  "guidance": "请确认合同签订日期，或补充能够证明项目满足时间要求的其他日期和材料。",
  "retryable": true
}
```

要求：

- 不把 Pydantic 原始异常、Python 堆栈或内部字段路径直接返回给用户；
- 错误必须说明哪里不符合、需要怎样修改，并尽可能提供合法示例；
- 业务错误不能伪装成格式错误；
- 用户修改后覆盖候选值，但保留验证尝试和审计记录；
- 是否限制最大重试次数、是否允许跳过和超时后如何处理尚未决定。

#### 7.5.7 Slot 相关候选流式事件

```text
task.pending
slot.requested
slot.provided
slot.format_validation_started
slot.format_validation_failed
slot.format_validation_passed
slot.business_validation_started
slot.business_validation_failed
slot.business_validation_passed
slot.resolved
task.resumed
```

校验失败事件应返回用户可理解的字段错误，但不泄露内部 Prompt、权限策略或模型原始推理。

### 7.6 Agent State Machine

#### 7.6.1 职责边界

状态：**State Machine 与防 Workflow 边界已确认；最小状态集合待确认**

```text
LLM / Planner
    ↓ 提出计划或下一动作
State Machine
    ├── 检查当前状态
    ├── 校验输入事件
    ├── 执行 Guard
    ├── 验证 State Context 数据不变量
    ├── 决定是否允许状态转换
    └── 产生受控 Action / Effect
             ↓
     Tool、模型、Slot、响应或持久化
             ↓
         新事件返回 State Machine
```

LLM 不能直接把 Task 从 `running` 修改成 `completed`，也不能绕过 State Machine 直接执行工具。LLM 只能提出结构化动作或完成建议，由 State Machine 和相应 Guard 决定是否允许；State Machine 也不能根据固定阶段替 LLM 决定下一项业务动作。

#### 7.6.2 State Machine 构成

| 构成 | 作用 |
|---|---|
| State | 当前 Agent Task 所处的离散运行状态 |
| Context | 当前任务的结构化数据，包括 Plan、Step Runtime、Slot、工具调用、Observation、预算和错误引用 |
| Event | 触发状态变化的结构化事实 |
| Guard | 判断某个转换是否允许的无副作用校验 |
| Transition | `当前状态 + 事件 + Guard → 下一状态` |
| Action | 状态转换时执行的同步内部更新 |
| Effect | 工具调用、模型调用、持久化和消息发送等外部副作用 |

State Machine 应尽量保持转换决定可重复。外部副作用必须携带幂等键，并在结果返回后通过新事件推进状态。

#### 7.6.3 Agent Task 顶层状态

状态：**`running / pending / completed / failed / cancelled`、合法转换和最小 Guard 已确认**

原候选 `received → planning → plan_validating → ready → executing → observing → responding` 已撤销。它把 Agent 的认知活动固化为必须依次经过的状态，本质上会重新形成 Workflow。

新的顶层状态只保留：

```text
                   ┌──── Action / Observation / Plan / Mode 更新 ────┐
                   │                                                   │
创建 Task ───→ running ───────────────→ running ◀────────────────────┘
                   ├── information_required ──→ pending
                   │                               └── slot.resolved ──→ running
                   ├── completion_accepted ─────→ completed
                   ├── fatal_error ─────────────→ failed
                   └── cancel.requested ────────→ cancelled

pending ── fatal_error / cancel.requested ──→ failed / cancelled
```

| 顶层状态 | 唯一生命周期语义 | 不代表 |
|---|---|---|
| `running` | Task 正在运行，可以继续接受主 Agent 动作、等待已发起的模型/工具结果、处理 Observation、Plan 更新或响应 | 不代表正在执行某个固定业务阶段 |
| `pending` | Task 已持久挂起，等待完成恢复所必需的用户输入 | 不代表一个固定业务审批节点 |
| `completed` | 当前 Task 的完成建议已通过 Guard，结果已可靠持久化或交付 | 不关闭持续对话 Session |
| `failed` | 出现不可自动恢复且无法通过当前任务继续处理的错误 | 普通 Tool 失败、零召回或一次参数错误不会直接失败 Task |
| `cancelled` | 用户或授权方明确取消，Runtime 已阻止新副作用 | 不等同于业务结论“不投标” |

合法转换矩阵：

| From \ To | `running` | `pending` | `completed` | `failed` | `cancelled` |
|---|---|---|---|---|---|
| Task 创建 | 是，初始状态 | 否 | 否 | 否 | 否 |
| `running` | 是，普通 Action/Event 自转换 | 是，需要用户输入 | 是，完成 Guard 通过 | 是，仅不可恢复错误 | 是，收到合法取消 |
| `pending` | 是，Slot resolved 后恢复 | 是，继续等待或校验失败 | 否，必须先恢复 running | 是，仅不可恢复错误 | 是，收到合法取消 |
| `completed` | 否 | 否 | 否 | 否 | 否 |
| `failed` | 否 | 否 | 否 | 否 | 否 |
| `cancelled` | 否 | 否 | 否 | 否 | 否 |

`completed`、`failed` 和 `cancelled` 是终态；不能通过普通事件重新进入 `running`。用户继续提问时创建新的 Agent Task，并按 Memory/Context 规则引用之前允许复用的信息。

以下内容必须与顶层状态分离：

| 内容 | 保存位置 |
|---|---|
| `direct / planned` | `execution_mode` |
| Planning、Tool Call、Observation、Response | Action/Event 及对应独立记录 |
| Plan Step 的等待、执行和完成 | Step Runtime，不升级为 Task 顶层状态 |
| Slot 格式/业务校验阶段 | `pending` 的 Suspension/Slot Context |
| 当前正在等待的模型或工具 | `in_flight_action_ref` |

#### 7.6.4 候选 State Context

初版只保留生命周期和恢复所需引用，不把所有执行细节复制进 Task State：

```json
{
  "task_id": "task_xxx",
  "session_id": "session_xxx",
  "state_version": 12,
  "status": "pending",
  "execution_mode": "planned",
  "goal_ref": "message_xxx",
  "plan_ref": "plan_xxx:v3",
  "pending_context": {
    "slot_ref": "slot_xxx",
    "checkpoint_ref": "checkpoint_xxx:v12",
    "phase": "waiting_input",
    "validation_attempt_ref": null,
    "last_error_ref": null
  },
  "in_flight_action_ref": null,
  "observation_refs": ["observation_xxx"],
  "last_error_ref": null
}
```

`plan_ref`、`pending_context`、`in_flight_action_ref` 和 `last_error_ref` 均可为空，并受当前 `status` 和 `execution_mode` 的跨字段 Guard 约束；`status=pending` 时 `pending_context` 必须非空，其他状态必须为空。Plan、Step Runtime、Tool Call、Slot、Observation 和 Memory 使用独立记录；Context 只保存引用，不复制大段工具结果、文档内容或会话历史。所有字段应由 Pydantic 等模型校验，并通过 `state_version` 防止并发覆盖。

#### 7.6.5 已确认的最小事件和 Guard

顶层状态转换只需要少量生命周期事件：

```text
task.started
action.accepted
observation.accepted
execution_mode.changed
information.required
slot.resolved
continuation.resumed
completion.proposed
completion.accepted
fatal_error
cancel.requested
```

Planner、Tool、Slot 校验仍可产生更细的领域中立事件，但它们通常形成 `running → running` 或 `pending → pending` 自转换，不增加新的顶层状态。

所有转换共用四个最小 Guard：Event ID 未消费、期望 `state_version` 与当前版本一致、转换存在于上方合法矩阵、关联副作用幂等键未被冲突消费。各转换再执行以下专属 Guard：

```text
running + action/observation/plan/tool/response event → running
    要求对应 Schema、权限、Scope、状态版本和幂等 Guard 通过
    State Machine 只接受或拒绝事件，不规定接受后必须做哪类业务动作

running + planning_required → running
    execution_mode 从 direct 更新为 planned
    已有 Evidence 和 Observation 保留，只规划剩余任务

running + information.required → pending
    要求已经创建独立 Slot
    要求进入 pending 前已经保存 Continuation Checkpoint
    要求不存在无法安全挂起的未登记副作用

pending + slot.resolved → running
    要求 Slot 值通过 Pydantic 格式校验、业务校验和必要证据校验
    要求 Continuation Checkpoint 和 Resume Token 有效
    要求当前 State Version 与挂起链路一致，且恢复幂等键未消费
    把验证通过的 Slot resolved value 合并进保存的 Runtime Context 后恢复 running
    跳过已完成 Step、已接受 Observation 和已完成外部副作用

running + completion.proposed → completed
    要求完成条件或当前回答目标满足
    要求用户可见结果已经可靠持久化或交付
    要求没有未决的 in-flight Action

running/pending + fatal_error → failed
    要求错误不可自动恢复且已有结构化失败记录
    普通 Tool Error、零召回和可重试校验错误不得触发 failed

running/pending + cancel.requested → cancelled
    要求建立取消围栏并阻止新的外部副作用

completed/failed/cancelled + 任意普通事件 → 拒绝
    终态不可被普通 Resume、Tool Result 或迟到消息重新激活
    迟到的异步结果只进入 Ledger 并标记 ignored，不改变 Task 状态
```

#### 7.6.6 数据持久化和恢复

Continuation Checkpoint 最小字段已确认：

```json
{
  "checkpoint_id": "checkpoint_xxx",
  "task_id": "task_xxx",
  "slot_ref": "slot_xxx",
  "suspended_state_version": 12,
  "execution_mode": "direct",
  "context_snapshot_ref": "task_context_xxx:v12",
  "suspended_action_ref": "action_xxx",
  "effect_fence_ref": "effect_fence_xxx:v12",
  "resume_token_hash": "sha256:...",
  "status": "open"
}
```

| 字段 | 恢复作用 |
|---|---|
| `checkpoint_id` / `task_id` / `slot_ref` | 将 Checkpoint、Task 和唯一活动 Slot 绑定 |
| `suspended_state_version` | 证明 Checkpoint 从哪一版 running Context 创建，检测错误分支或过期恢复 |
| `execution_mode` | 恢复 direct 或 planned 模式，但顶层状态统一恢复为 running |
| `context_snapshot_ref` | 引用挂起前的最小 Task Context 快照，不复制原文和完整历史 |
| `suspended_action_ref` | 通用暂停点；可以指向 direct Action，也可以指向 planned 模式下的 Step Action |
| `effect_fence_ref` | 指向挂起前已登记/已完成副作用边界，防止恢复后重复调用模型、工具或发送消息 |
| `resume_token_hash` | 一次性恢复凭证只保存 Hash，不在数据库明文保存 Token |
| `status` | `open`、`consumed` 或 `invalidated`；只有 open 可以恢复一次 |

Checkpoint 不保存 `resume_state=executing`，也不要求存在 `plan_id/current_step_id`。direct 与 planned 共用 `suspended_action_ref`：direct 恢复当前未完成 Action 或下一模型决策；planned 通过 Action 记录间接关联对应 Plan/Step，只继续剩余任务。

已确认要求：

- 每次合法 Transition 增加 `state_version`；
- 状态变化记录事件并形成可恢复 Checkpoint；
- 进入 `pending` 前必须保存 Continuation Checkpoint，记录挂起前的 `execution_mode`、未完成 Action/Plan/Step 引用和一次性 Resume Token Hash；顶层恢复目标固定为 `running`，不保存 `executing` 等伪顶层状态；
- Slot resolved 后优先恢复 Continuation，不得默认重新执行 Planner 或已经完成的 Step；
- 恢复前检查 Checkpoint 版本、Resume Token 和副作用幂等记录；
- Tool/Model 外部调用在发起前记录意图，返回后记录结果事件；
- 恢复时从最近 Checkpoint 加后续事件重建 State Context；
- 重复事件通过 Event ID 和幂等键去重；
- 非法转换拒绝执行，并记录结构化错误；
- State Machine 实现库、事件存储形式和快照频率尚未决定。

#### 7.6.7 最小恢复事务（已确认）

```text
pending 收到用户输入事件
    ↓ 绑定当前 Slot，保存 candidate_input_ref
Pydantic 格式校验
    ├──失败：保存友好错误引用，回到 waiting_input
    └──通过：进入 validating_business
业务校验
    ├──失败：保存友好错误引用，回到 waiting_input
    └──通过：写入 resolved_value_ref
恢复 Guard
    ↓ Slot/Task/Checkpoint 匹配
    ↓ Checkpoint=open，Resume Token、State Version 和 Effect Fence 有效
原子提交
    ↓ Slot=resolved
    ↓ Checkpoint=consumed
    ↓ Task pending→running，state_version+1
    ↓ 合并已验证值并按 suspended_action_ref 继续
```

Slot resolved、Checkpoint consumed 和 Task 恢复必须在同一事务或等价的原子状态转换中完成。重复用户消息、重复 Resume 请求或进程重启只能读到已经 consumed 的 Checkpoint，不得再次执行原 Action。

如果 Slot 已正确解决但 Context/Plan 在等待期间发生合法变化，Runtime 恢复到 running 后可以触发重新规划；如果 Checkpoint、Token 或 Effect Fence 无法验证，则不得猜测暂停点。可恢复冲突返回安全错误并保持 pending 或重新生成受控恢复方案，不可恢复损坏才进入 failed。

### 7.7 Canonical ToolDefinition 最小字段

状态：**已替代；保留八字段原始候选以记录设计过程，当前基线见 7.7.1**

首版推荐只保留八个顶层字段：

```text
id
contract_version
description
input_model
context_model
output_model
execution
safety
```

| 字段 | 候选语义 | 保留理由 |
|---|---|---|
| `id` | 稳定、全局唯一的 Tool ID；首版 Planner `tool_hint` 直接引用具体 Tool ID | 注册、规划、路由、审计和执行必须使用同一身份 |
| `contract_version` | Tool 输入、输出和语义合同版本 | Tool ID 保持稳定，合同变化仍可精确回放和审计 |
| `description` | 面向 Planner/Router 的调用说明，必须说明用途、使用时机、返回内容和边界 | 首版只有少量工具，不额外建立 capability taxonomy |
| `input_model` | 模型可填写参数的 Pydantic Model | 生成 Function Calling/MCP input Schema，并执行参数校验 |
| `context_model` | Runtime 注入的 Scope、用户、会话、Assessment、Manifest 等 Pydantic Model | 系统权威字段不暴露给模型填写，防止越权和 Scope 漂移 |
| `output_model` | Tool 成功数据的 Pydantic Model | 强制验证结果结构，并生成统一的模型可见 output Schema |
| `execution` | `local` 或 `mcp` 判别联合绑定 | Executor 可以从同一 Registry 找到 Handler 或 MCP Tool，不维护第二份 Adapter Map |
| `safety` | 只读、破坏性、幂等、开放世界提示和静态风险下限 | Router/Gateway 用于审批、重试和安全 Guard，也可映射 MCP ToolAnnotations |

候选 Python 形状：

```python
class CanonicalToolDefinition:
    id: ToolId
    contract_version: SemVer
    description: str
    input_model: type[BaseModel]
    context_model: type[BaseModel]
    output_model: type[BaseModel]
    execution: LocalExecution | McpExecution
    safety: ToolSafety
```

`execution` 是判别联合。候选序列化形状为：

```json
{"kind":"local","handler_id":"bid_document_search"}
```

或：

```json
{"kind":"mcp","server_id":"bid-evidence","tool_name":"evidence.read"}
```

Function Calling 不属于 `execution.kind`。它是 Canonical Definition 向模型提供 `id + description + input_schema` 的协议投影；Tool 真正执行时仍由 `execution` 决定调用本地 Handler 或 MCP。

首版不建议加入以下字段：

- `allowed_tools`、Task Category 或固定 Workflow Stage；
- `enabled`、当前健康状态和动态可用性；这些属于 Runtime Projection；
- Provider 专用 Function Calling Schema；应从 `input_model` 生成；
- MCP URL、Token 或其他密钥；应放在安全配置中，由 `server_id` 解析；
- timeout、retry、预算和并发数；这些属于 Runtime Policy；
- `capability_ids`、tags、aliases；首批四个工具可直接按 Tool ID 和 description 路由；
- few-shot；属于 Router Prompt 或评测资产，不属于工具事实；
- examples；可在路由评测证明 description 不足后作为可选字段增加，不列入首版必填字段。

所有 Tool 返回值由统一 `ToolExecutionResult[T]` 包装。`output_model` 只定义成功时的 `data`，公共 Envelope 统一承担 `status`、`error`、`warnings`、`provenance`、`tool_call_id` 和版本信息，避免每个工具重复声明错误协议。

#### 7.7.1 六字段 Canonical ToolDefinition 基线

状态：**已确认；替代上方八字段推荐但不追溯删除讨论过程**

外部评审提出三点：Function Calling 需要模型可见 `name`、`context_model` 容易混入模型参数或完整 State、每 Tool SemVer 可能过重。逐项判断如下：

1. Function Calling 请求确实需要 `name`，但不必同时保留两套身份。首版推荐用一个 Provider-safe `name` 同时作为 Registry Key、Planner `tool_hint` 和模型可见 Function Name，删除 `id`，避免 `id ↔ name` 映射漂移；
2. 如果 `context_model` 被序列化到模型参数或完整 State，确实会造成越权、耦合和 Token 膨胀。首版将它移出 ToolDefinition，改为 Executor/Gateway 持有的统一 `ToolExecutionContext`，通过显式依赖注入传给 Handler，永不进入模型 Tool Schema；不采用闭包隐式捕获关键权限和 Scope；
3. 每 Tool 手工 SemVer 对首版过重。删除 `contract_version`，改由整个 Canonical Registry 的不可变 Snapshot Version/Hash、自动生成的 Tool Definition Hash 和 Input/Output Schema Hash 提供回放依据。外部 MCP 自身的版本可留在 `execution` 绑定元数据中，但不是所有 Tool 的必填字段。

修订后的必填核心字段为六个；`examples` 和 `few_shots` 是评测触发、默认空的可选选择辅助：

```python
class CanonicalToolDefinition:
    name: ToolName
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    execution: LocalExecution | McpExecution
    safety: ToolSafety
    examples: tuple[ToolExample, ...] = ()
    few_shots: tuple[ToolFewShot, ...] = ()
```

运行时调用签名候选：

```python
async def handler(
    args: InputModel,
    context: ToolExecutionContext,
) -> OutputModel:
    ...
```

其中 `ToolExecutionContext` 是 Runtime 控制面对象，不属于 Tool Schema，不发送给 LLM，不复制完整 Messages/State，只包含已经验证的身份、权限、会话和数据 Scope 引用。Canonical Registry Snapshot 自动保存 `name`、生成后的 Input/Output JSON Schema、Schema Hash、Execution Projection 和 Safety Projection，用于审计与恢复。

### 7.8 Tool Description 与模型可见合同

状态：**核心规则已确认；Description 精确文本、Example/Few-shot Schema 和 Provider Projection 细节待确认**

#### 7.8.1 Description 编写规则

首版使用中文、两句话、一个短段落；Tool Name 和参数字段保持英文。Description 只承担模型选择工具所需的稳定语义，不复制参数 Schema、权限规则或实现细节。两句话和具体长度仍需结合四个真实 Description 冻结。

固定语义模板：

```text
第 1 句：在什么权威范围执行什么动作，返回哪类结果。
第 2 句：列出具体正向使用场景；必要时补一条相邻工具窄边界；说明证据升级规则。
```

每个 description 必须覆盖：

1. `what`：工具实际完成的动作；
2. `source boundary`：招标资料、企业知识或已取得 Evidence 等数据边界；
3. `returns`：返回候选、原文、结构导航或其他结果语义；
4. `positive use cases`：明确列出允许且适合调用的具体信息需求，作为模型选择白名单；
5. `adjacent boundary`：只有与相邻工具存在直接混淆时才写一条窄边界，不枚举大范围黑名单；
6. `evidence rule`：搜索结果是否可引用、是否必须继续 `evidence_read`。

Description 禁止包含：

- BM25、向量、RRF、Reranker、模型名等可替换实现细节；
- 固定业务阶段、固定调用顺序或 Workflow 话术；
- `assessment_id`、`manifest_id`、权限 Scope 等 Runtime 注入字段；
- 参数类型、枚举和默认值的重复说明；这些由 Input Schema 表达；
- 未经评测触发的长篇 few-shot、多个例子或提示词规则；
- “一定准确”“权威结论”等超出实际数据保证的承诺。

候选长度约束：中文 60—220 字符、最多两句。Description 先依赖明确且互斥的正向场景；只有评测证明仍存在稳定误选或参数构造失败时，才启用 ToolDefinition 的可选 `examples` 或 `few_shots`，并只随本轮动态白名单中的相关工具投影给模型。

首批四个候选 Description：

| Tool Name | Description 候选 |
|---|---|
| `bid_document_search` | 在当前会话已绑定的招标资料中检索原文候选，返回排序后的 `evidence_ref`、简短片段和文档定位。适用于查找招标条件、关键日期、资格要求、否决条款、保证金、费用及评分规则；搜索结果只用于定位，引用或形成事实前必须调用 `evidence_read`。 |
| `enterprise_knowledge_search` | 在当前获授权企业范围的离线知识库中检索企业资料候选，返回排序后的 `evidence_ref`、简短片段和来源定位。适用于查找企业资质、人员证书、项目业绩、产能、财务能力和客户历史，以回答企业能力或招标匹配问题；引用或形成事实前必须调用 `evidence_read`。 |
| `evidence_read` | 按一个或多个已有 `evidence_ref` 读取已授权来源中的原文、页码或章节定位及有限上下文，返回可引用的证据内容。适用于核实搜索候选，以及在引用、提取事实、比较要求或判断风险前取得原文；内容发现应先使用对应 Search Tool。 |
| `documents_outline` | 读取当前招标资料中指定文档的章节层级、页码范围和结构导航信息。适用于长文档首次导航、定位可能相关的章节、判断目录结构或缩小后续检索范围；需要具体条款原文时继续使用 `bid_document_search`。 |

#### 7.8.2 Provider-neutral 模型可见合同

Canonical Registry 不保存 OpenAI、Anthropic 或 MCP 专用请求体。它按当前会话授权结果投影出最小模型合同：

```python
class ModelVisibleToolContract:
    name: ToolName
    description: str
    input_schema: dict[str, Any]
    examples: tuple[ToolExample, ...] = ()
    few_shots: tuple[ToolFewShot, ...] = ()
```

模型可见与不可见边界：

| 信息 | Function Calling 模型可见 | 说明 |
|---|---|---|
| `name` | 是 | 与 Canonical Registry Key 完全一致 |
| `description` | 是 | 使用上方稳定描述规则 |
| `input_schema` | 是 | 从 `input_model.model_json_schema()` 生成并经 Provider Schema Projector 规范化 |
| `examples` / `few_shots` | 条件可见 | 仅评测触发且 Tool 位于本轮动态白名单时，以独立紧凑选择指导投影；不是 Function Tool 原生字段 |
| `strict` | 协议层可见 | Provider 支持时由 Adapter 设置，不属于 Canonical ToolDefinition |
| `output_schema` | 默认否 | Function Calling 首版不重复发送完整输出 Schema；MCP 支持时可投影为 `outputSchema` |
| `execution` | 否 | Handler、MCP Server 和远端 Tool 绑定只对 Executor 可见 |
| `safety` | 默认否 | Runtime Guard 权威执行；MCP 可映射标准 annotations |
| `ToolExecutionContext` | 否 | 由 Gateway/Executor 显式依赖注入 |
| Registry/Schema Hash | 否 | 只进入调用账本、Checkpoint 和审计快照 |
| URL、Token、权限规则 | 否 | 只存在安全配置和 Runtime Policy |

协议投影候选：

```text
OpenAI/兼容 Function Calling
    name        ← definition.name
    description ← definition.description
    parameters  ← projected input_schema
    strict      ← true（Provider 支持时）

MCP
    name         ← definition.name 或 execution.remote_tool_name
    description  ← definition.description
    inputSchema  ← projected input_schema
    outputSchema ← projected output_schema（协议支持时）
    annotations  ← safety projection
```

Function Calling 模型调用后收到的是经过 `output_model` 和公共 Envelope 校验的实际 `ToolExecutionResult` JSON。首版不在每次工具定义中提前发送完整 Output Schema；description 只概述关键返回语义，精确结构由实际结果和 Runtime 校验保证。若后续采用 Programmatic Tool Calling，或评测证明模型必须在调用前知道精确返回字段，再增加按需 Output Contract Projection，而不是修改 Canonical ToolDefinition。

`examples` 和 `few_shots` 不是 OpenAI/Anthropic/MCP 通用原生 Tool 字段。Provider Adapter 必须把它们渲染为与本轮 Tool Contract 相邻的独立选择指导，不能塞入 `parameters`，也不能把长示例重复拼接进每个 description。两者候选边界：

- `examples`：1—3 个短的正向用户意图及合法 arguments，用于说明这个工具的典型白名单场景；
- `few_shots`：最多 2 个最小 `user → assistant tool_call` 对，用于纠正跨工具选择或复杂参数构造；不保存思维链；
- 启用条件：同一混淆在代表性路由评测中稳定复现，且仅修改 description/Input Schema 未解决；
- 禁止包含真实敏感资料、权限绕过示范、大段 Tool Result 或与当前合同不一致的历史调用；
- Example/Few-shot 内容随 Registry Snapshot 一起 Hash 和审计。

#### 7.8.3 Input Schema 生成规则

`input_model` 是唯一手工事实源，禁止同时维护独立 JSON Schema 文件。生成投影必须满足：

- 根节点是 object；
- 禁止未声明字段，投影后使用 `additionalProperties: false`；
- 每个字段使用稳定英文名，并提供简短中文 `description`；
- 格式、长度、枚举、数量、数值范围和默认值使用 Schema 关键字表达，不重复塞进 Tool description；
- 系统注入的身份、权限、会话和数据 Scope 字段不得出现在 Input Schema；
- 空字符串不得表示缺失；缺失、`null` 和空集合的语义必须明确区分；
- Provider Adapter 必须对 Pydantic JSON Schema 做兼容性投影，不允许静默删除无法支持的约束；
- Provider `strict` 不能替代 Gateway 的 Pydantic 校验和业务校验；任何模型参数仍需在执行前重新校验；
- Schema Projection、Schema Hash 和发送给模型的最终 JSON 必须进入审计快照。

首版优先使用简单、扁平的 Input Model。复杂 `$ref`、深层判别联合、递归结构和 Provider 不一致的 Schema 特性，只有真实工具需要且通过兼容性验证后才引入。

#### 7.8.4 每轮动态 Tool 白名单

Canonical Registry 保存全集，模型只看到当前 Turn 的白名单投影。候选计算公式：

```text
visible_tools =
    registered_tools
    ∩ enabled_tools
    ∩ provider_supported_tools
    ∩ permission_allowed_tools
    ∩ scope_available_tools
    ∩ data_ready_tools
    ∩ risk_approved_tools
    ∩ current_information_need_tools
```

规则：

- 白名单采用集合交集形成，不通过“先暴露全部工具，再维护大量 deny 条件”实现；
- 简单直接回答可以得到空白名单；只需要读取已有 Evidence 时只暴露 `evidence_read`；
- 当前问题明确只涉及招标文件时，优先只暴露 `bid_document_search`、必要时加 `documents_outline` 和后续 `evidence_read`；
- 当前问题明确只涉及企业能力时，优先只暴露 `enterprise_knowledge_search` 和后续 `evidence_read`；
- 同一问题确实需要招标要求与企业事实比较时，允许同时暴露两个 Search Tool，但 description 必须依靠来源边界保持可区分；
- Provider 支持 `allowed_tools` 时使用其原生白名单能力；不支持时只发送筛选后的 `tools` 数组；
- `auto` 是默认选择模式；只有 State Machine 已确认当前动作必须调用白名单内工具时才使用 `required`，不能仅因工具可用就强制调用；
- Tool Call 返回后必须以调用时冻结的 `visible_tool_names` 和授权快照再次校验，不能只信任 Provider 已限制；
- 白名单结果及形成原因进入审计，但不向模型暴露完整权限策略和内部拒绝规则。

### 7.9 首批四个 Tool 的 Input/Output Pydantic Model

状态：**最小合同原则已确认；下方详细字段仅作实践候选，不作为初版冻结要求**

初版推荐收敛为以下最小语义合同：

| Tool | 最小 Input | 最小成功 Output |
|---|---|---|
| `bid_document_search` | `query`；确有多文档限定需求时再加入 `document_refs` | 候选列表：`evidence_ref`、`excerpt`、`locator` |
| `enterprise_knowledge_search` | `query` | 候选列表：`evidence_ref`、`excerpt`、`locator` |
| `evidence_read` | `evidence_refs` | 证据列表：`evidence_ref`、`text`、`locator`、`citable` |
| `documents_outline` | `document_ref` | 有序结构条目：`title`、`level`、`locator` |

初版只要求：Input/Output 都由 Pydantic 校验、拒绝未声明字段、引用必须经过 Runtime 权限与 Scope 校验、Search Candidate 必须经 `evidence_read` 才能成为引用证据。`top_k`、`max_depth`、`context_mode`、Cursor、字符/数量上限、上下文片段角色和 Outline 分页均降级为实现候选：只有实现或小范围验证证明需要模型控制时才加入模型可见 Schema，否则由 Runtime 使用可配置的安全默认值。

以下 7.9.1—7.9.6 保留 v0.15 的详细候选，作为开发时可选参考和问题清单；它们不再需要在初版开发前逐字段确认，也不能因写在文档中就自动成为冻结合同。

#### 7.9.1 合同边界与公共基类

首版明确区分三层数据，不把它们混成一份 Schema：

1. `input_model`：模型可见的调用参数，也是 Gateway 执行前重新校验的参数合同；
2. `output_model`：Handler 成功返回的业务 `data`，运行时必须校验后才能回流模型；
3. `ToolExecutionResult[T]`：后续单独讨论的公共 Envelope，承担 `status`、`error`、`warnings`、`provenance`、`tool_call_id`、Registry/Schema Hash 等控制字段。

公共 Pydantic v2 基类候选：

```python
from pydantic import BaseModel, ConfigDict


class ToolInputModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        str_strip_whitespace=True,
    )


class ToolOutputModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
    )
```

输入模型统一拒绝额外字段和隐式类型转换，并清理用户/模型生成字符串两端的空白；输出模型不自动清理原文，避免改变证据内容。`frozen=True` 只阻止字段重新赋值，不替代深拷贝、不可变集合和审计快照。

模型可见字段首版不使用隐藏默认值：每个属性均进入 JSON Schema 的 `required`；“可以不限定”使用显式可空字段并要求模型传 `null`。这样 Provider strict 投影和 Runtime Pydantic 语义保持一致，避免 Provider 侧省略字段、Runtime 侧补默认值造成不可见行为。所有对象层级均投影 `additionalProperties: false`。

公共标量候选：

| 类型 | 候选约束 | 说明 |
|---|---|---|
| `QueryText` | `str`，去空白后 1—500 字符 | 表达当前单次信息需求；不在字段中暴露检索算法 |
| `DocumentRef` | `str`，1—160 字符 | Runtime 签发或解析的不透明引用；首版不假定 UUID 格式 |
| `SourceRef` | `str`，1—160 字符 | 招标文档或企业知识来源的统一不透明引用 |
| `EvidenceRef` | `str`，1—160 字符 | Search/Read 之间传递的不透明引用；格式正确不代表存在或有权访问 |
| `SectionRef` | `str`，1—160 字符 | Outline 返回的结构引用；不能直接替代 Evidence |
| `Cursor` | `str`，1—512 字符 | Runtime 生成的不透明分页游标；必须在业务层校验签名、Scope 和快照版本 |

引用字段只做长度、空值和集合数量等结构校验。引用是否存在、是否属于当前会话/文档/企业知识快照、是否过期以及是否有权访问，必须由执行前业务 Guard 校验，不能靠正则表达式伪装成业务有效性。

#### 7.9.2 Input Model 候选

```python
from typing import Annotated, Literal
from pydantic import Field, model_validator

QueryText = Annotated[str, Field(min_length=1, max_length=500)]
DocumentRef = Annotated[str, Field(min_length=1, max_length=160)]
EvidenceRef = Annotated[str, Field(min_length=1, max_length=160)]
Cursor = Annotated[str, Field(min_length=1, max_length=512)]
DocumentRefList = Annotated[list[DocumentRef], Field(min_length=1, max_length=20)]


class BidDocumentSearchInput(ToolInputModel):
    query: QueryText = Field(description="要在招标资料中查找的具体信息需求")
    document_refs: DocumentRefList | None = Field(
        description="限定检索的文档引用；null 表示当前会话全部已绑定招标文档"
    )
    top_k: Annotated[int, Field(ge=1, le=8)] = Field(description="最多返回的候选数量")


class EnterpriseKnowledgeSearchInput(ToolInputModel):
    query: QueryText = Field(description="要在企业知识库中查找的具体信息需求")
    top_k: Annotated[int, Field(ge=1, le=8)] = Field(description="最多返回的候选数量")


class EvidenceReadInput(ToolInputModel):
    evidence_refs: Annotated[
        list[EvidenceRef],
        Field(min_length=1, max_length=8),
    ] = Field(description="要读取的已有证据引用；不得重复")
    context_mode: Literal["exact", "neighbors", "section"] = Field(
        description="读取精确片段、相邻上下文或受限章节上下文"
    )


class DocumentsOutlineInput(ToolInputModel):
    document_ref: DocumentRef = Field(description="要读取结构导航的招标文档引用")
    max_depth: Annotated[int, Field(ge=1, le=6)] = Field(description="返回的最大章节层级")
    cursor: Cursor | None = Field(description="上一页返回的游标；首次读取传 null")
```

输入字段取舍：

| Tool | 保留字段 | 首版不加入的字段 | 原因 |
|---|---|---|---|
| `bid_document_search` | `query`、`document_refs`、`top_k` | `retrieval_mode`、`rrf_k`、`rerank`、`score_threshold` | 算法由检索策略和评测治理，不让模型操纵底层实现；文档限定对多文档资料包有直接业务价值 |
| `enterprise_knowledge_search` | `query`、`top_k` | `knowledge_type`、`entity_type`、`as_of` | 企业知识分类和快照规则尚未在 T04 冻结，避免先造不稳定枚举；授权企业范围和快照由 Context 注入 |
| `evidence_read` | `evidence_refs`、`context_mode` | `max_chars`、`page_range`、任意路径/URL | 模型决定需要哪种语义上下文，Runtime 决定安全大小上限；只能读取 Runtime 已登记且当前 Scope 可见的 Evidence 引用 |
| `documents_outline` | `document_ref`、`max_depth`、`cursor` | 文件路径、MinIO Key、任意页码范围 | 只接受不透明受控引用；游标支持长文档结构分页，不把整份目录一次塞入上下文 |

所有引用列表必须增加 `model_validator` 做去重校验；重复项不静默去重，因为静默修复会改变模型实际请求。`document_refs=[]` 不是“全部文档”，应作为结构校验错误；不限定必须显式传 `null`。

#### 7.9.3 公共来源与候选结果模型

Search Tool 返回的是待核实候选，不能直接作为最终引用。首版不向模型暴露原始向量分数、BM25 分数或 RRF 分数，只返回稳定排序 `rank`；不同检索器分数不可直接比较，且会把实现细节泄漏进推理。

```python
SourceKind = Literal["bid_document", "enterprise_knowledge"]
SourceRef = Annotated[str, Field(min_length=1, max_length=160)]


class SourceLocator(ToolOutputModel):
    source_kind: SourceKind
    source_ref: SourceRef
    source_title: Annotated[str, Field(min_length=1, max_length=300)]
    page_no: Annotated[int, Field(ge=1)] | None
    section_path: list[Annotated[str, Field(min_length=1, max_length=300)]]
    location_label: Annotated[str, Field(min_length=1, max_length=500)] | None


class EvidenceCandidate(ToolOutputModel):
    evidence_ref: EvidenceRef
    rank: Annotated[int, Field(ge=1, le=8)]
    excerpt: Annotated[str, Field(min_length=1, max_length=800)]
    locator: SourceLocator
    read_required: Literal[True]


class BidDocumentSearchOutput(ToolOutputModel):
    candidates: Annotated[list[EvidenceCandidate], Field(max_length=8)]


class EnterpriseKnowledgeSearchOutput(ToolOutputModel):
    candidates: Annotated[list[EvidenceCandidate], Field(max_length=8)]
```

`source_ref` 是统一来源引用，可以指向招标文档或企业知识来源；`source_kind` 决定解释方式。`page_no`、`location_label` 等可空字段仍然必须出现在 strict JSON 中并显式为 `null`，从而区分“来源没有页码”与“Handler 漏返回字段”。`section_path=[]` 表示来源确实没有可用章节层级。

#### 7.9.4 Evidence Read 输出模型

读取相邻或章节上下文时，一个请求引用可能展开为主片段和若干上下文片段，因此结果需要保留“哪个引用触发了读取”和“当前片段在结果中的角色”。每个返回片段都必须有自己的定位；只有 `citable=true` 的片段允许进入最终事实引用。

```python
class EvidenceContent(ToolOutputModel):
    requested_evidence_ref: EvidenceRef
    evidence_ref: EvidenceRef
    role: Literal["primary", "context"]
    citable: bool
    text: Annotated[str, Field(min_length=1, max_length=12_000)]
    locator: SourceLocator


class EvidenceReadOutput(ToolOutputModel):
    items: Annotated[list[EvidenceContent], Field(max_length=24)]
```

`context_mode` 的含义：

- `exact`：只返回请求引用对应的主片段；
- `neighbors`：返回主片段和 Runtime 限定数量的相邻片段；
- `section`：返回主片段和 Runtime 限定范围内的同章节片段，不承诺返回完整章节。

模型不能提交字符预算或任意页码扩大读取范围。单片段 12,000 字符、单次最多 24 个片段是首版防御性上限候选，不代表 Executor 必须用满；实际总字符数、Token 数和上下文片段数由 Runtime Budget 取更小值。若受预算截断、部分引用失效或部分读取失败，由后续 `ToolExecutionResult` 的结构化 warning/error 表达，不能伪造空文本。

#### 7.9.5 Documents Outline 输出模型

Outline 使用扁平、有序条目而不是递归树，降低 Provider/模型处理复杂度；`level` 和 `section_path` 足以重建当前页的层级关系。

```python
SectionRef = Annotated[str, Field(min_length=1, max_length=160)]


class OutlineEntry(ToolOutputModel):
    section_ref: SectionRef
    title: Annotated[str, Field(min_length=1, max_length=300)]
    level: Annotated[int, Field(ge=1, le=6)]
    section_path: list[Annotated[str, Field(min_length=1, max_length=300)]]
    page_start: Annotated[int, Field(ge=1)] | None
    page_end: Annotated[int, Field(ge=1)] | None
    has_children: bool


class DocumentsOutlineOutput(ToolOutputModel):
    document_ref: DocumentRef
    document_title: Annotated[str, Field(min_length=1, max_length=300)]
    entries: Annotated[list[OutlineEntry], Field(max_length=100)]
    next_cursor: Cursor | None
```

`section_ref` 只用于导航、后续查询构造和 UI 定位，不自动成为可引用 Evidence；需要条款原文时仍调用 `bid_document_search`，再通过 `evidence_read` 升级。`next_cursor=null` 明确表示当前深度下已无下一页。业务校验必须保证 `page_end >= page_start`、层级跳变合法、游标与 `document_ref`/`max_depth`/文档快照一致。

#### 7.9.6 三段校验与失败归属

四个工具统一经过三段校验：

```text
模型 Tool Arguments
    ↓ 1. Pydantic 结构校验
类型、required/null、长度、数量、枚举、范围、extra=forbid、集合去重
    ↓ 2. Runtime 业务与安全校验
引用存在性、会话/企业 Scope、权限、快照版本、游标签名、数据可用性、预算
    ↓ 3. Handler Output Pydantic + Provenance 校验
返回结构、来源一致性、Evidence 可追溯性、页码/章节关系、输出预算
    ↓
ToolExecutionResult 回流主 Agent
```

第一段失败属于 `invalid_arguments`，应把可修复的字段级问题返回主 Agent 供一次受控重试；第二段失败不得伪装成参数格式错误，其中缺少可由用户提供的信息可以生成 Slot 并进入 `pending`，权限拒绝、引用越界和篡改游标则直接结构化失败；第三段失败属于 Tool/Provider 合同违约，不把未校验原始结果回流模型。

精确 Pydantic 类和 JSON Schema Snapshot 在首轮实现时按上方最小合同生成。数值上限、`context_mode`、底层分数和 Outline 分页等内容不再作为开发前置讨论项；实践暴露需要后再回到本节修订。Safety Model、统一 Envelope 和错误码也只讨论首版运行必需字段。

### 7.10 最小 Safety Model 与 ToolExecutionResult

状态：**初版最小方案已收束；详细扩展继续由实践触发**

#### 7.10.1 Safety 只回答四个运行问题

首版 `safety` 不承担完整权限策略、风控规则或审批流程，只保存 Router/Gateway 判断工具能否进入本轮候选和能否执行所需的稳定属性：

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict


class ToolSafety(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effect: Literal["read_only", "mutating"]
    data_scope: Literal["context_bound", "explicit_resource"]
    external_egress: bool
    requires_approval: bool
```

四个字段分别回答：

| 字段 | 运行问题 |
|---|---|
| `effect` | 工具是否可能修改数据或产生外部副作用 |
| `data_scope` | 工具只能使用 Runtime 已授权的当前上下文数据，还是必须额外指定一个受控资源 |
| `external_egress` | 调用是否会把业务数据发送到当前受信 Runtime 边界之外 |
| `requires_approval` | 执行前是否必须取得用户或审批策略的明确许可 |

首批四个 Tool 统一采用最小只读配置：

```json
{
  "effect": "read_only",
  "data_scope": "context_bound",
  "external_egress": false,
  "requires_approval": false
}
```

这里的“当前受信 Runtime 边界”包括同一隔离开发环境内已授权的本地服务调用，不等于是否经过网络；向外部 MCP、互联网服务或第三方模型发送业务内容才属于外部数据流出。未来增加写入、外部调用或审批型 Tool 时，再基于真实需求扩展枚举和 Guard，不提前设计复杂风险矩阵。

`requires_approval=false` 只描述产品正常运行后是否需要对每一次只读 Tool Call 做业务审批，不构成当前开发阶段的执行授权。现行约束仍然优先：运行任何 Agent 测试、真实资料、检索评测、Embedding、Reranker、模型或外部 MCP 前，必须重新取得用户明确授权。

`safety` 不保存用户 ID、角色、企业 ID、文档列表或 Evidence 列表。这些动态信息仍由 `ToolExecutionContext` 和权限/Scope Guard 提供。`risk_level`、超时、重试次数、Token Budget 和并行数也不进入首版 Safety Model：它们分别属于 Planner 或 Runtime Policy。

#### 7.10.2 ToolExecutionResult 只区分成功和失败

首版返回 Envelope 只保留 `ok`、`data` 和 `error`：

```python
from enum import StrEnum
from typing import Generic, TypeVar
from pydantic import model_validator

T = TypeVar("T", bound=BaseModel)


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


class ToolError(ToolOutputModel):
    code: ToolErrorCode
    message: str
    retryable: bool


class ToolExecutionResult(ToolOutputModel, Generic[T]):
    ok: bool
    data: T | None
    error: ToolError | None

    @model_validator(mode="after")
    def validate_success_or_failure(self):
        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("success requires data and forbids error")
        if not self.ok and (self.data is not None or self.error is None):
            raise ValueError("failure requires error and forbids data")
        return self
```

成功和失败的唯一合法形态：

```json
{
  "ok": true,
  "data": { "...": "通过对应 output_model 校验的数据" },
  "error": null
}
```

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "not_found",
    "message": "未找到当前 Scope 内可读取的证据引用。",
    "retryable": false
  }
}
```

首版语义约束：

- Search 未召回内容属于成功，`data.candidates=[]`，不是 Tool Error；
- `evidence_read` 或 `documents_outline` 收到不存在、过期或不可见引用时返回失败；
- `message` 是可安全回流主 Agent 的简短说明，不包含堆栈、SQL、内部路径、凭证或权限策略细节；
- `retryable` 只表示同一动作是否值得 Runtime/Agent 重新尝试，不替 Agent 决定重试、重规划、追问用户或结束；
- 首版不支持部分成功：多引用读取中任一项失败时整次调用失败；实践证明需要保留成功子集后再增加 `warnings` 或部分结果语义；
- `pending` 不是 Tool Result 状态。工具只返回事实性失败，是否创建 Slot 并进入 `pending` 由主 Agent 和 State Machine 判断。

#### 7.10.3 不重复塞入模型结果的运行元数据

以下内容仍必须记录，但不放进首版模型可见 `ToolExecutionResult`：

- Provider `tool_call_id` 和内部 Call ID；
- Registry Snapshot、Tool Definition、Input/Output Schema Hash；
- 开始/结束时间、耗时、尝试次数和执行节点；
- 原始异常、堆栈和内部诊断；
- 权限与 Scope 决策摘要；
- Evidence 的存储级 Provenance。

这些内容由 Tool Call Ledger/Checkpoint 保存，并通过 Call ID 与 `ToolExecutionResult` 关联。模型需要使用的来源定位仍保留在具体 `output_model` 的 `locator` 中；安全审计元数据与回答所需证据语义不混为一份 JSON。

#### 7.10.4 最小执行边界

```text
Router 形成本轮 Tool 白名单
    ↓ 读取静态 Safety 属性
Gateway 校验 Tool Name、Arguments、权限、Scope 和 Approval
    ↓
Executor 调用 local/MCP Handler
    ↓
output_model 校验成功 data
    ↓
包装 ToolExecutionResult 并回流主 Agent
```

首批四个工具均是只读、本地受信边界、Context-bound、无需审批，因此初版不会产生审批分支。保留 Safety Model 是为了让“当前为何允许调用”由 Runtime 可检查，而不是依靠 description 或 LLM 自觉；它不规定 Agent 必须按什么顺序调用工具，因此不会形成 Workflow。

### 7.11 Tool Router 与 Permission Guard 的最小边界

状态：**已确认**

#### 7.11.1 一句话职责边界

| 组件 | 只回答的问题 | 不负责的事情 |
|---|---|---|
| Tool Router | 当前这个模型决策轮次，哪些已经具备执行资格的 Tool 值得暴露给主 Agent | 不做身份认证，不授予权限，不执行工具，不替 LLM 决定固定业务路径 |
| Permission Guard | 当前用户和 Runtime Context 是否允许看到或执行某个 Tool，以及具体参数引用的资源是否仍在授权 Scope 内 | 不判断工具是否最适合当前问题，不改写 Planner 计划，不选择业务调查顺序 |
| Gateway | 按顺序组织 Schema 校验、Guard、Executor 和结果校验，确保任何拒绝都不会到达 Handler | 不承担意图识别、检索策略或回答生成 |

核心不变量：

```text
Router 只能缩小 Guard 允许的集合，不能扩大权限；
Guard 只能判断 allow/deny，不能替 Agent 选择下一动作。
```

#### 7.11.2 最小运行顺序

```text
Canonical Tool Registry
    ↓ enabled / execution available / data ready
Permission Guard：visibility preflight
    ↓ 得到 eligible_tools
Tool Router：结合当前目标和有效 tool_hint 做最小投影
    ↓ 得到 visible_tools
Provider Adapter：只把 visible_tools 发给主 Agent
    ↓ LLM 自主选择回答、追问或发起 Tool Call
Gateway：执行前重新校验
    1. Tool Name 属于本次模型调用冻结的 visible_tools
    2. Arguments 通过 input_model
    3. Permission Guard 对具体资源和当前状态再次授权
    4. Safety/Approval 条件仍满足
    ↓
Executor → output_model → ToolExecutionResult
```

`visibility preflight` 只做工具级粗检查，因为此时还没有模型生成的 Arguments；执行前 Guard 才解析 `document_ref`、`evidence_ref` 等具体引用并做资源级检查。两次 Guard 使用同一权限事实源和 Scope Resolver，不维护两套权限规则。

#### 7.11.3 初版 Router 不增加第二个意图 LLM

首批只有四个 Tool，Router 先实现为轻量 Runtime Projector，不单独调用另一个 LLM 做意图分类。最小策略：

1. 从 Registry 中取得当前启用且执行绑定可用的 Tool；
2. 由 Permission Guard 去掉当前用户、会话、数据状态下不具备资格的 Tool；
3. 当前 Plan Step/Agent Action 提供有效 `tool_hint` 时，Router 优先把白名单缩小到该 Tool；
4. 没有 `tool_hint` 时，首版可以向主 Agent 暴露全部 eligible Tool，由 Tool Description 和 Function Calling 完成选择；
5. Router 不强制调用，Provider 默认仍使用 `auto`；没有可见 Tool 时，主 Agent仍可直接回答、说明缺少资料或向用户追问。

`tool_hint` 是路由建议，不是授权令牌。它无效、不可用或被 Guard 拒绝时，Router 不静默替换成另一个 Tool，而是把“建议工具当前不可用”的安全 Observation 返回主 Agent，由主 Agent决定改用其他路径、重规划或追问。

随着 Tool 数量和误选率增长，再根据路由评测决定是否增加 capability 标签、规则匹配或独立 Router 模型；初版不为尚未出现的路由问题增加组件。

#### 7.11.4 “每轮动态白名单”的精确定义

这里的“每轮”是一次 **模型决策轮次**，不是整个用户 Turn。一次用户问题内部可能出现多次：

```text
模型决策 → Tool Call → Observation → 重新形成白名单 → 下一次模型决策
```

每次调用模型前重新计算并冻结 `visible_tool_names`。例如取得 Search Candidate 后，下一模型决策轮次可以出现此前没有必要暴露的 `evidence_read`；这是根据新 Observation 更新可行动集合，不是固定的 Search→Read Workflow。模型仍可以停止、追问、改查其他来源或重规划。

模型返回 Tool Call 后必须绑定到产生该调用的冻结 Tool 集合。不能拿前一轮可见权限调用本轮已被撤销的 Tool，也不能把一次模型响应中伪造的未知 Tool Name 交给 Executor。

#### 7.11.5 Permission Guard 的两级最小检查

**Visibility Preflight** 检查：

- Tool 存在于当前 Registry Snapshot 且功能开关已启用；
- 当前用户已通过上游身份认证；
- 当前角色/租户原则上可以使用该 Tool；
- Tool 所需数据类型已经存在并处于可读取状态；
- Safety Profile 在当前运行环境允许执行。

**Execution Authorization** 再检查：

- Tool Name 属于本次模型调用冻结的 `visible_tool_names`；
- 当前用户、会话、租户和状态版本未失效；
- 每个 `document_ref`、`evidence_ref` 或 Cursor 都能通过受控 Resolver 解析；
- 解析后的真实资源属于当前授权 Scope，且用户仍有读取权限；
- Tool 的执行绑定、数据就绪状态和审批条件仍然满足。

Guard 采用默认拒绝：缺失身份、未知 Tool、无法解析引用、Scope 不明确、权限事实不可用或状态已过期时均不得执行。对模型只返回安全的 `access_denied`、`not_found` 或 `unavailable`；具体拒绝原因进入内部 Ledger，避免泄露资源是否存在和权限策略细节。

#### 7.11.6 首批四个 Tool 的最小 Scope

| Tool | Visibility 条件 | Execution Scope |
|---|---|---|
| `bid_document_search` | 当前会话至少有一份已就绪招标文档 | 只能检索当前用户可读且已绑定当前会话的招标文档 |
| `enterprise_knowledge_search` | 当前用户具有企业知识读取资格，离线知识索引可用 | 只能检索当前租户/企业授权知识范围，不接受模型填写租户或企业 ID 扩权 |
| `evidence_read` | 当前会话 Evidence Store 至少有一个可见引用 | 每个 Evidence Ref 必须解析到当前用户仍可读取的原始来源 |
| `documents_outline` | 当前会话至少有一份结构已就绪的招标文档 | `document_ref` 必须属于当前用户可读且已绑定当前会话的文档 |

Handler 不接收任意文件路径、对象存储 Key、数据库连接或未经解析的租户 ID。Gateway 通过受控 Resolver 得到 Scope-bound Handle 后再交给 Handler，避免只在入口检查一次、Handler 随后使用全库查询。

#### 7.11.7 最小输出与审计

Router 对 Runtime 的必要输出只有冻结的 `visible_tool_names`；Guard 的必要输出只有 `allow/deny` 和安全原因码。更详细的候选分数、路由解释和权限规则树不进入初版模型上下文。

Runtime Ledger 至少关联保存：本次模型决策使用的 Registry Snapshot、`visible_tool_names`、实际 Tool Name、Guard allow/deny、通用原因码和状态版本。初版不先冻结独立 Router/Guard Pydantic Schema；实现中出现跨进程传输或回放需要后再提取正式合同。

这个边界保持 Agent 性：主 Agent/Planner 决定想做什么，Router 控制本轮提供哪些合适动作，Guard 保证动作合法，Executor 执行动作；没有任何组件用固定任务类别或预设顺序替 Agent 编排业务流程。

### 7.12 Planner 的组件形态与最小规划粒度

状态：**已确认**

#### 7.12.1 初版组件形态：逻辑独立，部署不独立

Planner 初版作为 Main Agent Runtime 内部的结构化 LLM 调用模式：

```text
Main Agent 判断 execution_mode=planned
    ↓
Agent Runtime 调用 Planner Port
    ↓ 使用 Planner Prompt + Plan JSON Schema
同一默认 LLM Provider/Model 产生完整 Plan JSON
    ↓
Runtime 校验、持久化 Plan Version
    ↓
Main Agent 在 running 内根据 Plan 和 Observation 选择下一动作
```

Planner 具有独立代码接口、Prompt、输入投影、输出 Schema、审计记录和评测集，但初版：

- 不创建第二个持续存在的 Agent；
- 不部署独立 Planner 服务或维护独立 State Machine/Memory；
- 不注册成 Function Calling Tool 或 MCP Tool；
- 默认复用 Main Agent 的模型配置，但保留未来按评测替换 Planner 模型的接口；
- 只有 Complexity Gate 进入 `planned` 时才产生额外 Planner 模型调用。

这样既能让 Planner 输出受到独立 Schema 强约束，也避免在初版引入多 Agent 协调、上下文同步和额外部署面。若后续评测证明 Planner 需要专用模型、独立伸缩或隔离预算，再替换 Planner Port 的实现，不改变 Main Agent、State Machine 和 Plan 合同。

Planner 的概念输入只需要当前目标、经过 Context Assembler 选择的有效上下文/Evidence、当前允许的能力或 Tool Name，以及修订时的当前 Plan 和 Replan Reason。这里先不冻结独立 PlannerRequest 的全部字段，避免 Memory/Context 方案未确认前重复设计。

#### 7.12.2 最小规划粒度：有限步滚动计划

初版不采用两个极端：

| 方案 | 问题 |
|---|---|
| 一开始生成穷尽式完整路径 | 容易根据未知信息虚构远期步骤，并把动态任务锁成 LLM 生成的 Workflow |
| 每次只输出一个孤立 Tool Call | 无法表达多个目标、依赖和完成标准，Planner 与普通 Action 选择没有区别 |

推荐采用 **有限步滚动计划**：Planner 只规划当前已知、对完成目标确有必要的信息子目标，同时明确一个 `next_decision`；后续根据 Observation 继续执行或修订剩余部分，不承诺未知的远期路径。

已经确认保留的顶层字段继续使用：

```text
goal_summary
completion_criteria
steps
next_decision
replan_conditions
user_projection
```

首版粒度规则：

- `steps` 数量由当前任务复杂度动态决定，不设固定业务数量，也不对应 P0-P4；
- 一个 Step 描述一个可验证的信息子目标或决策子目标，不描述单次底层函数调用；
- Search、Evidence Read、Outline 等 Tool Call 是完成 Step 时的动态 Action，不默认拆成固定的 Search→Read 两个 Step；
- Step 可以声明真实依赖，但不得为了展示流程人为串行化互不依赖的工作；
- `tool_hint` 只是建议，Router/Guard 仍决定当前可见和允许的 Tool；一个 Step 可以零次、一次或多次调用 Tool；
- `next_decision` 只指明当前最值得推进的下一信息子目标或决策点，不提前锁定所有后续 Tool Call；
- 已完成 Step 和已接受 Observation 保持不可变；Plan 修订只修改未开始或仍进行中的剩余部分。

例如“核实某一保证金金额并解释条款”即使需要 Search 和 Evidence Read，仍是一个 direct 短证据循环，不触发 Planner；“比较招标资格、企业资质、类似业绩和关键风险”触发 planned，但 Step 应是“确认资格条件”“核实企业资质”“比较类似业绩”等信息目标，而不是把每个检索函数调用列成节点。

#### 7.12.3 Planner 不直接执行 Tool

Planner 只产生通过 Schema 校验的 Plan，不在生成 Plan 的同一次调用中执行 Tool。执行时仍由 Main Agent：

1. 读取当前 Plan、Step Runtime 和最新 Observation；
2. 提出下一 Action 或 Tool Call；
3. 经过 Tool Router、Permission Guard、Gateway 和 Executor；
4. 把通过校验的结果写成 Observation；
5. 判断继续当前 Plan、完成 Step、修订剩余 Plan、进入 pending 或提出完成。

这条边界防止 Planner 同时承担规划、路由、授权和执行，避免出现无法审计的隐藏调用链。Planner 输出的 `tool_hint` 不能绕过本轮动态白名单，也不能要求 Runtime 执行不存在的 Tool。

#### 7.12.4 最小 Plan 修订条件

正常获得预期 Tool Result 或完成一个 Step，不自动再次调用 Planner。只有以下情况才修订 Plan：

- 用户修改目标、范围或完成标准；
- 新 Observation 推翻关键假设或出现需要处理的证据冲突；
- 出现原计划未覆盖的重要子目标、依赖或未知项；
- 当前 Tool/数据源不可用，原剩余路径无法继续；
- direct 已通过 `planning_required` 升级为 planned；
- 当前 Plan 的完成条件已经不再适用。

修订产生新的 `plan_version`，保留已经完成的 Step、Observation 和副作用记录。一般 Tool Error、一次参数修复或同一路径内继续查证不必重写 Plan。是否需要向用户流式展示修订，取决于修订是否实质改变用户已经看到的计划。

#### 7.12.5 与 Query Decomposition/Expansion 的最小边界

Planner 可以把复杂用户目标拆成信息子目标，但初版不负责生成 BM25/向量查询、同义词集合或多路召回参数。每个 Step 的具体查询表达由 Main Agent 和 Search Tool 的检索入口在执行时形成；Query Expansion、Rewrite 和多查询召回在 T04/T05 结合真实检索评测后决定是否成为独立内部能力。

因此初版不注册 `query_decompose`、`query_expand` 等认知 Tool，也不让 Planner 输出底层检索算法参数。只有实践证明同一类查询稳定失败时，再提取独立模块。

### 7.13 主 Agent 的意图与信息需求理解边界

状态：**已确认**

#### 7.13.1 初版由 Main Agent 联合理解

初版保留逻辑独立的 `IntentUnderstandingPort`，便于单独校验、审计、评测和未来替换；其默认实现复用 Main Agent 模型和完整 Context，不增加独立固定标签分类模型、固定意图路由表或 `intent_classifier` Tool。Main Agent 在理解当前用户消息和有效 Context 时，一并完成：

- 用开放式自然语言概括当前目标；
- 判断现有信息是否足以回答或行动；
- 识别需要核实的信息子目标和可能的信息来源；
- 判断缺失信息是否阻塞；
- 选择直接回答、direct 行动、创建 Slot 澄清或触发 planned。

“意图”不是 `招标问答/企业问答/风险研判` 等固定业务枚举。一个问题可以同时需要招标资料、企业知识、已有 Evidence 和会话上下文；Main Agent 输出的是当前目标和信息需求，不是把用户永久归入某个类别。

以后只有在实践评测证明高频、稳定、封闭的意图仍存在稳定误判，且独立小模型能显著改善成本、延迟或准确率时，才在 `IntentUnderstandingPort` 前增加可回退的轻量分类快速路径。它只能提供建议；未命中或低置信时回到 Main Agent 开放式理解，也不能取代 Router 或 Permission Guard。

#### 7.13.2 最小 Understanding Decision 候选

初版可以使用一个很小的内部结构化决策，由 Pydantic 校验，但不要求把它展示给用户：

```json
{
  "goal_summary": "判断本项目的类似业绩要求以及企业是否具有匹配业绩",
  "information_needs": [
    {
      "question": "招标文件如何定义类似业绩？",
      "source_hints": ["bid_documents"],
      "blocking": true
    },
    {
      "question": "企业有哪些满足该定义的项目业绩？",
      "source_hints": ["enterprise_knowledge"],
      "blocking": true
    }
  ],
  "clarification_required": null,
  "execution_mode": "planned",
  "next_action": "plan"
}
```

最小语义字段：

| 字段 | 作用 |
|---|---|
| `goal_summary` | 当前 Task 想解决的问题，可随用户纠正产生新版本 |
| `information_needs` | 当前已识别的可验证信息子目标；允许空列表，不预设固定数量 |
| `question` | 用自然语言表达需要查清的事实或判断，不包含底层查询语法 |
| `source_hints` | `conversation`、`existing_evidence`、`bid_documents`、`enterprise_knowledge` 等来源建议；不是授权或固定路由 |
| `blocking` | 当前信息子目标不解决时，是否无法可靠完成用户目标 |
| `clarification_required` | 只有必须由用户补充且无法从允许来源取得时才非空；随后生成正式 Slot |
| `execution_mode` | 使用已确认的 `direct/planned`；不新增 Task 顶层状态 |
| `next_action` | 候选只需表达 `answer`、`act`、`clarify` 或 `plan` |

该结构是 Runtime 控制摘要，不包含思维链、固定业务标签、具体 Tool Arguments 或完整 Plan。`source_hints` 只能帮助 Planner/Router 理解来源需求，最终 Tool 可见性和资源访问仍由 Router/Guard 决定。

#### 7.13.3 何时直接继续，何时澄清

Main Agent 只有同时满足以下条件才创建 Slot 并进入 `pending`：

1. 缺失内容会实质改变答案、计划或安全边界；
2. 无法从当前会话、已有 Evidence、招标资料或企业知识中可靠取得；
3. 不适合用明确标注的合理假设或条件式回答代替；
4. 能定义可校验的用户输入合同和业务校验规则。

以下情况不应阻塞用户：

- 可从已授权来源检索的信息，不先反问用户手工提供；
- 只影响表达偏好、报告格式或非关键细节的信息；
- 可以分别说明多种解释并给出条件式结论的轻微歧义；
- 资料确实没有记载但结论可以明确标为 unknown 的信息；
- 权限拒绝、Tool 不可用或引用越界，这些属于安全/运行错误，不伪装成 Slot。

初版同一 Task 同时只激活一个 Slot，优先询问最能解除阻塞的最小信息。一个 Slot 的 Pydantic Input Model 可以包含一组逻辑上不可分割的字段，但不能借此一次性要求用户填写完整固定表单。Slot resolved 后 Main Agent 重新评估目标和信息需求，再决定继续 direct、触发 planned 或提出下一个真正必要的 Slot。

#### 7.13.4 问题拆解与 Planner 的边界

Main Agent 的信息需求拆解只回答“为了完成当前目标，需要查清哪些事实或判断”。是否生成正式 Plan 仍由 Complexity Gate 决定：

| 情况 | 处理方式 |
|---|---|
| 单一目标、单一路径、短证据循环 | 保持 direct；信息需求可以只有一个，不生成 Plan |
| 一个问题有多个简单检索表达，但仍服务同一事实 | 保持一个信息需求；具体 Query Rewrite/Expansion 留给检索层 |
| 多个独立可验证子目标、真实依赖或跨来源完整比较 | 进入 planned；Planner 把信息需求转换为有限步滚动 Plan |
| 用户目标本身不清楚且无法安全选择解释 | 创建 Slot 澄清，不先生成建立在猜测上的 Plan |

Main Agent 不在理解阶段生成 BM25 关键词、Embedding Query、RRF 参数或固定 Search→Read 调用序列。信息子目标是语义层拆解；查询改写、多查询召回和检索参数属于 T04/T05 的 Retrieval 层。

#### 7.13.5 多来源信息需求

`source_hints` 可以为空、单源或多源。多来源问题应先拆成来源内可核实事实，再由 Main Agent 比较和综合。例如：

```text
用户目标：判断企业是否满足类似业绩要求
    ├── 招标资料：类似业绩的类型、规模、时间和证明材料要求
    ├── 企业知识：候选项目的类型、规模、日期和证明材料
    └── 主 Agent：对齐维度，区分匹配、冲突和 unknown
```

“比较和综合”仍是 Main Agent 的认知任务，不注册成 `evidence_compare` Tool。Search Candidate 必须经过 Evidence Read 才能用于事实比较，source hint 也不能绕过当前用户和租户 Scope。

#### 7.13.6 意图修正与 Observation 反馈

Understanding Decision 不是一次生成后永久不变。以下事件允许在 `running` 内重新理解并生成新版本：

- 用户纠正、补充或改变目标；
- Slot resolved 提供了关键新信息；
- Tool Observation 暴露新的歧义、冲突或重要未知项；
- direct 升级 planned，或当前 Plan 因实质变化需要修订。

正常 Tool Result 不要求每次重跑完整意图理解。Runtime 保留目标版本和触发原因，已确认用户事实不得被新推断静默覆盖；如新消息与当前目标冲突，Main Agent 应明确说明理解变化，必要时请求澄清。

### 7.14 离线 RAG 的解析、切块、元数据和版本治理

状态：**初版已确认**

#### 7.14.1 边界：离线生产知识，在线 Agent 自主使用知识

离线 RAG 可以采用确定性数据生产管线，因为它处理的是文档派生产物，不替 Agent 决定用户问题的调查路径：

```text
Source Version
    -> Parse Run
    -> Structured Blocks
    -> Chunk Build
    -> Embedding / Index Build
    -> Ready Head
```

这条管线与 Agent Task 的 `running/pending/completed/failed/cancelled` 状态机完全分离。它可以有解析、构建和发布作业状态，但在线 Main Agent 不按上述节点依次运行，也不把它们变成 Planner Step。在线 Agent 只通过 Search Tool 查询当前授权范围内已经 Ready 的知识快照。

首版明确不让离线 RAG：

- 预先判断某份招标资料的投标结论或固定风险清单；
- 生成在线 Agent 必须遵循的调查步骤；
- 把 LLM 摘要当作可直接引用的原始事实；
- 把招标资料和企业资料无范围隔离地混入一个公共索引；
- 决定 BM25、向量、RRF、Reranker 和 Query Rewrite 的在线组合方式，这些属于 T05/T06。

#### 7.14.2 现有资产审计与复用原则

当前隔离的 Bid Assessment 数据域已经具备可复用的基础：

| 现有资产 | 已有能力 | 本轮处理 |
|---|---|---|
| Document Version / Parse Run / Parse Head | 不可变文档版本、解析尝试、质量、结果 Hash 和权威 Head | 作为招标资料解析事实源，不再新建平行解析域 |
| `bid.evidence.chunk.v2` | `section_parent -> retrieval_child -> evidence_atom` 三层确定性切块 | 作为首版切块起点，数值参数仍由后续实践调整 |
| Retrieval Index / Retrieval Head | 解析派生索引、Ready/Stale、Profile/Role Contract/Result Hash 和当前 Head | 继续承担检索快照和原子切换，不允许原地改写索引 |
| 历史 Tender Evidence Ingestion | 较早的证据版本与规范化块实现 | 只作历史参考，不恢复成 Pure Agent 的第二套事实源 |

企业知识沿用相同的**逻辑解析与 Chunk 合同**，但保留独立的 Source Domain、Scope 和索引命名空间。统一合同是为了让 Search/Read 行为一致，不代表把企业私有知识与某次招标会话的资料合并存储或共享权限。

#### 7.14.3 两类知识域

| 知识域 | 生命周期和范围 | 典型来源 | 在线用途 |
|---|---|---|---|
| 招标资料 | 绑定租户及当前会话、项目或 Assessment；每次上传形成不可变 Source Version | PDF、Word、Excel、图片及附件 | 查找本项目条款、时间、资格、商务技术要求和原始证据 |
| 企业知识 | 绑定租户和企业；可跨会话复用，并受有效期、停用和权限控制 | 资质、业绩、人员、财务、制度和企业说明材料 | 核实企业能力和资料，与招标要求做事实对齐 |

两个知识域可以使用相同 Parser/Chunk Builder 实现和元数据语义，但至少按 `source_kind + scope_ref + authorized_head` 隔离。在线 Tool 的 Runtime Context 注入当前授权 Scope，模型不能通过参数任意切换企业、项目或知识快照。

#### 7.14.4 解析产物的最小合同

Parser 不直接输出扁平全文，而是为一个不可变 Source Version 产生有序的 Structured Blocks。最小语义如下：

| 类别 | 最小内容 |
|---|---|
| 身份 | `source_version_ref`、`parse_run_ref`、文档内容 Hash |
| 结构 | Block Key、顺序、Block Type、标题/章节路径、前后边界 |
| 内容 | 规范化原文；表格仍保留表格/行/表头关系，不破坏性压平成普通段落 |
| 定位 | 页码、页内 BBox、Sheet/Cell Range、图片区域或 Section Locator，按文档类型选用 |
| 来源 | `native/ocr/mixed/none`，以及来源 Block/附件引用 |
| 质量 | Parser Profile/Version、质量等级或分数、Warnings、缺页/失败单元和结果 Hash |

解析规则候选：

- 优先保留标题、段落、条款、列表、表格、表格行、表单字段、图片、Caption 和附件边界；
- Native Text 与 OCR Text 不互相静默覆盖；OCR 是显式来源或补充来源，必须通过 `content_source` 和质量信息区分；
- 允许 Parse Run 为 `partial`，但缺失页、低质量和 Warning 必须可见；未经发布为 Ready 的产物不能进入在线检索 Head；
- Parser 的确定性清洗只处理编码、空白和明确版面噪声，不擅自改写事实、单位、日期或否定词；
- 首版不要求用 LLM 生成章节摘要、实体或风险标签；以后若增加，必须作为独立派生字段且默认不可引用。

#### 7.14.5 三层 Chunk 合同

推荐保留现有三层结构：

| 角色 | 作用 | 是否直接可引用 |
|---|---|---|
| `section_parent` | 保留章节或较大上下文，用于导航和补充阅读 | 否 |
| `retrieval_child` | 面向召回的检索单元，可以注入标题路径等检索上下文 | 否，必须升级读取 Evidence |
| `evidence_atom` | 最小但语义完整、带精确 Locator 的原文证据单元 | 是 |

基本关系：

```text
section_parent
    ├── retrieval_child
    │       ├── evidence_atom
    │       └── evidence_atom
    └── retrieval_child
            └── evidence_atom
```

这样 Search Tool 可以返回轻量 Candidate，Evidence Read 再沿 Source Atom 引用取回精确原文；Main Agent 不能把标题注入、拼接上下文或相似度命中的 Child 直接伪装成引用。

#### 7.14.6 切块和重叠原则

首版不冻结一个适用于所有文档的固定字符数，而是使用版本化 `chunk_profile` 保存可调参数。当前 `bid.evidence.chunk.v2` 的长度和重叠配置仅作为已有实现起点，后续根据真实召回评测调整。

切块顺序应为：

1. 优先遵守文档结构和语义边界；
2. 再按 Profile 的 Soft/Hard Limit 合并或拆分；
3. 只有单个长 Block 必须拆分时才使用重叠；
4. 每个派生块保存来源 Block 和内容 Hash，不允许无法追溯的文本拼接。

具体规则候选：

- 条款、表单字段、表格、表格行、Caption 和附件边界尽量保持原子隔离；
- 普通相邻段落可以在同一章节内合并，但不能跨越明确标题、附件或结构边界；
- 标题路径可以进入 `retrieval_text` 改善召回，但必须与 `normalized_text` 分开，引用仍回到原始 Evidence Atom；
- 不对每个 Chunk 机械使用固定重叠；重叠只修复长文本被迫切断时的上下文损失，并记录来源跨度或来源 Block；
- 表格不只保存整表文本：至少保留表格父上下文、表头语义和可定位的行级 Atom；跨页表格的续表关系作为结构信息，不猜测缺失单元；
- 同一事实在正文、目录和附件重复出现时不在离线阶段武断删除；保存各自来源，在线召回/重排再处理近重复和权威性。

#### 7.14.7 Chunk Metadata 最小集合

逻辑 Chunk 合同建议分为六组。字段名可以映射现有表结构，不要求立即新建一张万能表：

| 分组 | 最小字段语义 |
|---|---|
| 身份与版本 | `chunk_key`、`source_kind`、`source_version_ref`、`parse_run_ref`、`chunk_profile_version` |
| 层级 | `role`、`parent_ref`、`ordinal`、`section_path` |
| 定位 | `locator_type`、结构化 `locator`；按类型包含 page/bbox/sheet/cell/image/section |
| 内容 | `normalized_text` 或内容引用、`retrieval_text`、内容/定位/检索 Hash、估算 Token、`is_citable` |
| 溯源 | Source Block Refs、Atom Refs、Parser Profile/Version、派生关系 |
| 范围与质量 | 不透明 `scope_ref`、质量/Warning 引用、有效或撤销状态 |

模型可见的 Search Candidate 只是该合同的安全投影，不发送内部租户 ID、存储地址、完整权限属性或全部父块内容。`scope_ref` 由 Runtime/Guard 使用；引用只暴露经过授权、可展示的 Locator 和 Evidence 内容。

企业知识可按真实需要增加文档类型、业务标签、权威等级、`effective_at/expires_at` 等字段，但这些不在首版被提前固化为大而全的枚举。有效期会影响当前知识是否可检索，不能仅作为给模型看的自由文本。

#### 7.14.8 版本和 Head 治理

所有派生产物不可变；可变的是指向当前可用版本的 Head：

| 层级 | 版本身份至少由什么决定 |
|---|---|
| Source/Document Version | 原始内容 Hash、来源身份和创建事件 |
| Parse Run | Source Version、Parser Profile/Version、输入 Hash |
| Chunk Build | Parse Run、Chunk Contract/Profile、结果 Hash |
| Embedding Build | Chunk Set Hash、Embedding Model/Revision、维度、归一化和 Profile |
| Lexical/Vector Index | Chunk Set/Embedding Build、Index Profile 和结果 Hash |

治理规则候选：

- 参数、Parser、Chunk Profile、Embedding Model 或源文件变化时创建新派生版本，禁止原地覆盖旧结果；
- 构建完成并通过合同校验后，才原子切换 Ready Head；失败或 Partial 版本保留审计信息但不自动替换现行 Head；
- 上游权威 Head 变化后，下游旧索引标记为 Stale；在线 Search 必须证明所选索引与当前授权 Head 或 Task 冻结快照一致；
- Task 已读取的 Evidence 保存版本引用和内容 Hash；Head 后续变化不能静默改写历史回答的证据；
- 相同输入和 Profile 通过 Hash 实现幂等复用，避免重复解析、Embedding 和索引构建；
- 旧版本保留用于审计、比较和回滚，但不等于继续对在线 Search 可见。

#### 7.14.9 停用、删除与重建

知识撤销首先是在线可见性和授权问题，不应只依赖物理删除：

1. Source Version 被停用、撤销或删除后，立即从 Active/Ready Head 和可检索集合移除；
2. 对 Lexical/Vector Index 写入 Tombstone 或构建不含该版本的新索引，确保它不再被召回；
3. 历史 Evidence 引用保留“当时使用的版本已撤销”状态，不能悄悄替换成新内容；
4. 物理清理由数据保留策略、审计要求和明确授权决定，不在普通重建中顺带清除；
5. 若是权限或企业归属改变，Guard 必须先阻断访问，不能等待异步索引清理完成。

重建遵循“旁路构建 -> 校验 -> 原子切 Head -> 旧版本 Stale/退役”，不先清空当前可用索引。招标资料与企业知识分别维护 Head 和重建范围，避免一个知识域重建导致另一个知识域不可用。

#### 7.14.10 最小状态与发布门槛

离线作业可以复用现有的通用生命周期语义，例如 Parse Run 的 `queued/running/succeeded/partial/failed` 和 Index Build 的 `queued/building/ready/failed/stale`。这些只是数据作业状态，不是在线 Agent Workflow。

一个知识版本只有在以下条件满足时才可以成为在线 Ready Head：

- Source Version、Parse Run、Chunk Build 和索引派生链引用完整；
- Schema、Hash、父子引用、Source Atom 和 Locator 通过静态合同校验；
- Scope/Ownership 已绑定且不存在越权范围；
- 解析质量和 Warning 达到当前 Profile 的发布要求；
- 向量索引的 Model/Revision/Dimension 与检索配置一致；
- Head 切换使用原子更新并保留旧 Head 引用。

具体质量阈值、Chunk 长度、Embedding 批量参数、Index 参数和发布评测集不在本轮冻结；它们需要在获得用户单独授权后通过真实资料与检索评测确定。

#### 7.14.11 本轮推荐结论

初版推荐采用以下最小边界：

1. 复用现有不可变 Parse/Chunk/Retrieval Index 资产，不创建第四套 RAG 事实源；
2. 招标资料和企业知识共享逻辑合同，但 Scope、Head 和索引严格隔离；
3. 保留 `section_parent/retrieval_child/evidence_atom` 三层结构，只有 Atom 可直接引用；
4. 结构优先切块，Profile 管理长度；仅在长块强制拆分时使用可追溯重叠；
5. Native/OCR、表格、标题层级和精确 Locator 均作为一等元数据；
6. 全链路派生产物不可变，以 Hash 幂等，以 Ready Head 原子发布，以 Stale/Tombstone 处理重建和撤销；
7. 离线 RAG 不生成固定研判路径，在线召回、Query Rewrite、RRF 与重排留到 T05/T06。

### 7.15 在线召回的最小策略和组件边界

状态：**初版已确认**

#### 7.15.1 在线召回不是 Agent 业务 Workflow

在线召回是两个 Search Tool 的内部确定性能力：

```text
Main Agent / Planner
    └── 形成一个具体 Information Need，并选择信息源 Tool
            │
            ▼
bid_document_search / enterprise_knowledge_search
    ├── Permission Scope + Retrieval Snapshot
    ├── Query Strategy
    ├── Metadata Filter
    ├── Lexical Recall + Semantic Recall
    ├── Rank Fusion + Stable Deduplication
    └── Safe Candidate Projection
            │
            ▼
Main Agent 自主决定继续搜索、evidence_read、换信息源、澄清或回答
```

上图是一次 Search Tool 内部的数据处理顺序，不是 Agent Task 的预设步骤。Main Agent 可以直接回答，也可以调用一次或多次 Search、读取 Evidence、改查另一知识域或停止；Runtime 不强制每个问题经过固定的 Search→Read→Answer 链。

#### 7.15.2 现有资产审计

现有隔离实现已经提供以下可复用资产，但不把旧 Agent 的固定执行图带入 Pure Agent：

| 资产 | 已有能力 | Pure Agent 中的定位 |
|---|---|---|
| RQ1-C Query Optimizer | 原查询锚点、受限别名/并列主体/答案形状扩展、稳定去重和 Query Plan Hash | 作为初版 `RetrievalQueryStrategyPort` 的确定性实现起点 |
| RQ1-D BM25F | Child 正文、标题和表格等字段感知词法召回 | 作为 Lexical Recall Adapter 起点 |
| RQ2-A BCE Semantic | Retrieval Child-only 向量召回、不可变 Semantic Head | 作为 Semantic Recall Adapter 起点 |
| RQ2-B Candidate Fusion | 词法与语义候选按稳定 Child Key 去重，以 Rank-only Weighted RRF 融合 | 作为初版 Fusion Policy 起点 |
| Evidence MCP Search/Read | Search 只返回不可引用 Child，Read 回到可引用 Atom；Run/Manifest Scope fail-closed | 保留证据角色和 Scope 原则，通过新 Tool Handler/Port 适配，不复用旧 Workflow 控制面 |
| RQ2-C Reranker | 融合候选上的受控 Cross-Encoder 重排 | 属于 T06，不作为 T05 默认前置条件 |

历史真实基线中，Semantic-only 相比字段感知词法出现明显回退，而词法主导的 Hybrid Fusion 得到更稳的综合结果。因此首版不采用“语义检索替换 BM25”，也不让模型在每次调用时猜测 `exact/semantic/hybrid`。

当前成熟实现主要覆盖招标证据域；`enterprise_knowledge_search` 仍需基于 T04 的统一逻辑 Chunk 合同实现独立 Adapter 和索引。不能把旧的结构化企业查询接口直接宣称为企业离线 RAG，也不能通过复用招标索引混淆两个知识域的权限和 Head。

#### 7.15.3 Main Agent、Planner 与 Query Strategy 的职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| Main Agent | 理解用户目标、形成 Information Need、选择招标或企业知识 Tool、判断是否继续查证 | 不生成 BM25 权重、向量参数、RRF K 或索引表达式 |
| Task Planner | 复杂任务下拆分多个可验证的信息/决策子目标 | 不把同一事实的关键词变体拆成多个业务 Step |
| Retrieval Query Strategy | 把一个 Information Need 转换为受限、可审计的查询表达；保留原查询锚点 | 不改变用户目标，不选择其他 Tool，不扩大 Scope，不创建用户 Slot |
| Recall/Fusion | 在当前授权快照内执行多路召回、合并和稳定排序 | 不形成事实、风险结论或引用 |

例如“判断企业是否满足类似业绩要求”可以由 Planner 拆成“查清类似业绩定义”和“查清企业候选业绩”两个信息目标；每个 Search 内部为了召回使用“类似项目、同类工程、近三年业绩”等受限变体，仍只是一个信息目标的 Query Strategy，不新增 Planner Step。

#### 7.15.4 RetrievalQueryStrategyPort

初版保留逻辑独立的 `RetrievalQueryStrategyPort`，但默认使用确定性实现，不增加一次 LLM 调用，也不注册成模型可见 Tool。内部 Query Plan 至少表达：

- 原始查询及不可删除的 Original Anchor；
- 零个或多个有界 Query Variant；
- 每个 Variant 的稳定 ID、文本、来源类型/生成原因和 Profile 权重；
- 可用于召回的结构提示，但不包含权限 Scope；
- Query Plan Profile、输入 Hash 和结果 Hash。

初版允许的处理包括 NFKC/空白归一化、受控领域别名、并列主体拆分、金额/日期/编号等答案形状提示和稳定去重。以下能力暂不默认启用：

- 无边界的生成式同义改写；
- LLM 自动生成大量多维查询；
- 从查询猜测租户、企业、文档权限或任意 `as_of`；
- 把多个独立业务问题隐藏在一次 Search 内执行。

如果后续评测证明确定性扩展对自然语言表达覆盖不足，可以在同一 Port 后增加结构化 LLM Query Rewrite 实现。它必须保留原查询、限制变体预算、通过 Pydantic 校验和重复/语义漂移检查，并与确定性方案做 A/B 后才启用。

#### 7.15.5 召回策略候选比较

| 方案 | 优点 | 风险 |
|---|---|---|
| 模型每次选择 Lexical/Semantic/Hybrid | 理论上节省部分通道成本 | 模型需要理解底层索引，路由错误直接损失召回；Tool Schema 暴露过多实现参数 |
| Lexical-first，未命中再 Semantic | 简单查询成本低 | “未命中”不能证明词法已经充分；增加一次串行延迟，并容易形成机械两阶段路径 |
| Profile 驱动的 Hybrid Default | 单次并行覆盖精确词和语义表达；实现稳定、可审计，模型无需操作算法 | 每次会使用两个通道，需要控制候选与 Embedding 预算 |

**当前推荐：Profile 驱动的 Hybrid Default。**

- Lexical 使用字段感知 BM25F，负责编号、金额、日期、条款名、资质名、表格键值和精确术语；
- Semantic 只索引/召回 `retrieval_child`，负责措辞变化和概念相近表达；
- 两路可以并行执行；每路内部对 Query Variants 做有界融合，再做跨通道 Rank-only Weighted RRF；
- 词法、语义权重、RRF 参数、每路深度和最终返回数属于版本化 Retrieval Profile，不进入模型可见 Input；
- 初始 Profile 可以沿用“词法主导、语义补充”的 RQ2-B 经验，但具体数值不在架构阶段永久冻结；
- 只有评测证明查询形态路由能稳定改善质量、成本或延迟时，再增加可回退的 Retrieval Router。

这里的“默认 Hybrid”是 Search Tool 的检索算法基线，不是要求 Agent 每次必须搜索，因此不构成 Workflow。

#### 7.15.6 Scope、Snapshot 与 Metadata Filter

过滤顺序必须先于召回，不能先对全库搜索再在 Top-K 结果上隐藏越权项：

1. Permission Guard 确认当前用户可调用对应 Search Tool；
2. Runtime 从 Task Context 解析当前授权 Source Domain、Scope 和 Ready Head/Snapshot；
3. 将系统强制过滤与模型提供的合法业务限定求交集；
4. Lexical 和 Semantic Adapter 在同一个允许集合内召回；
5. Candidate Projection 再执行输出脱敏和引用签发。

强制规则：

- `bid_document_search` 只能读取当前会话/项目/Assessment 已绑定且当前用户可读的招标资料；
- `enterprise_knowledge_search` 只能读取当前租户和企业授权范围内处于有效状态的企业知识；
- `document_refs` 等模型参数只能缩小 Runtime 允许集合，不能扩大集合；
- Source Domain 由 Tool Name 决定，一次 Tool Call 不跨招标和企业知识域；跨域比较由 Main Agent 分别取证后完成；
- Lexical Index、Semantic Index 和 Chunk Head 必须属于同一授权快照；Stale 或 Hash 不匹配的通道不得参与召回；
- 有效期、撤销和 Tombstone 是系统过滤，不依赖模型记得填写；历史 `as_of` 查询在没有显式、可校验合同前不能伪装为当前资料查询成功。

Task 读取的 Candidate/Evidence 必须绑定 Source Version 和 Retrieval Snapshot。后续 Head 更新不能静默改变已经读取的内容；用户补充新资料时由新的 Context/Snapshot Version 显式进入后续决策。

#### 7.15.7 多查询、多通道融合与去重

初版逻辑顺序：

```text
Query Plan
    ├── BM25F：各 Query Variant -> 通道内有界 Rank Fusion
    └── Vector：各 Query Variant -> 通道内有界 Rank Fusion
                       │
                       ▼
             Cross-channel Weighted RRF
                       │
             Stable Child Key Deduplication
                       │
          Bounded Parent/Source Diversity Selection
```

融合约束：

- 不直接比较 BM25 分数与 Cosine 分数；跨后端只使用 Rank 或经过独立校准的分数；首版使用 Rank-only Weighted RRF；
- 使用稳定 `retrieval_child_key` 去重，不依赖数据库随机 UUID；
- 同一 Child 被多 Query/多通道命中时合并来源记录，不重复返回；
- Parent 只提供上下文/弱辅助信号，不作为 Search Candidate；Atom 也不直接参与召回排序；
- 可在最终 Candidate Selection 中限制同一 Parent 或同一来源过度占满结果，但具体上限由 Profile 和评测决定；
- 排名同分时使用稳定 Key 和版本引用做确定性 Tie-break，保证重放一致；
- Reranker 不属于本节 Fusion；T05 输出冻结 Candidate Window 后，T06 才决定是否以及如何重排。

#### 7.15.8 Search Candidate 与 Evidence Read

沿用已确认的最小模型可见合同：

```text
Search Candidate
    evidence_ref
    rank
    excerpt
    locator
    read_required = true
```

其中：

- `evidence_ref` 是绑定 Scope、Source Version、Retrieval Snapshot 和 Child 的不透明引用；
- `excerpt` 用于帮助 Main Agent选择要读取的候选，不是可直接引用原文；
- `locator` 是安全的粗定位，不暴露内部存储地址、租户 ID 或索引主键；
- Candidate 始终是 `retrieval_child + is_citable=false`；
- `evidence_read` 校验引用后回到 `evidence_atom + is_citable=true`，并返回精确 Locator；
- Main Agent 可以根据 Candidate 直接判断“不值得继续读取”，所以 Search→Read 不是强制流程；但凡要把 Candidate 内容当作事实或引用，就必须先 Read。

模型默认不需要看到 BM25/Cosine/RRF 原始分数、通道权重和内部 Profile Hash。Runtime Ledger 必须保留 Query Plan、各通道 Rank/Score、融合结果、Snapshot/Head/Profile Hash、过滤摘要和 Warnings，供审计与评测。

#### 7.15.9 No Result、降级和失败语义

`candidates=[]` 是一次成功的“未召回到候选”，不等于资料中不存在该事实。Main Agent 可以根据目标和剩余预算：

- 修改查询表达但保持同一 Information Need；
- 使用 Outline 缩小或改变文档范围；
- 查询另一个获准知识域；
- 对复杂任务修订剩余 Plan；
- 明确回答当前证据不足或 unknown。

通道故障不得伪装成完整 Hybrid 成功：

- Semantic Head Stale、模型版本不匹配或向量服务不可用时，禁止读取旧向量结果；
- 如果当前 Lexical Head 有效，可以返回明确标记的 Lexical-only Degraded Result 和 Warning；
- Main Agent 不得基于 Degraded/No Result 做强“资料不存在”结论；
- Scope 失效、Head/Hash 不一致、Evidence Ref 越权或合同校验失败应直接失败，不降级为全库搜索；
- Runtime 记录规范化 Query Fingerprint，重复等价查询没有新信息增益时应提示 Main Agent 停止循环；完整循环预算在 T13 冻结。

具体 Warning/Error Code 不在本轮穷举，继续使用 `ToolExecutionResult(ok/data/error)` 和 Ledger 分层：模型获得足以调整行为的安全信息，内部保留完整诊断。

#### 7.15.10 推荐的最小组件形态

```text
Search Tool Handler
    ├── RetrievalSnapshotResolver
    ├── RetrievalQueryStrategyPort
    ├── ScopeFilterCompiler
    ├── LexicalRecallAdapter
    ├── SemanticRecallAdapter
    ├── CandidateFusionPolicy
    ├── CandidateRefSigner / Projector
    └── RetrievalLedgerWriter
```

这些是一个 Search Handler 内可替换、可评测的逻辑接口，不要求首版拆成微服务、独立 Agent 或多个模型可见 Tool。招标和企业知识可以有不同 Adapter/Profile，但共用 Query Plan、Candidate 和 Evidence Ref 的逻辑合同。

#### 7.15.11 本轮推荐结论

1. Main Agent/Planner 负责业务问题和信息目标拆解；Search 内部 Query Strategy 只做单一 Information Need 的受限查询表达；
2. 保留 `RetrievalQueryStrategyPort`，初版复用确定性 Query Optimizer，不额外调用 LLM；
3. 采用 Profile 驱动、词法主导的 Hybrid Default，模型不填写 retrieval mode、权重、RRF K 或底层 Top-K；
4. Permission Scope、Ready Snapshot 和系统 Metadata Filter 在召回前生效，模型限定只能缩小范围；
5. BM25F 与 Child-only Vector 并行召回，使用 Rank-only Weighted RRF、稳定 Child Key 去重和确定性 Tie-break；
6. Search 只返回不可引用 Child Candidate，形成事实或引用前必须通过 `evidence_read` 升级为 Atom；
7. No Result 不等于事实不存在；Semantic 不可用时只允许显式 Lexical-only Degraded，不允许旧向量结果或静默降级；
8. 招标与企业知识使用独立 Adapter/Profile/Index，Main Agent 负责跨域取证和综合；
9. Reranker、证据权威性、冲突排序和引用验证进入 T06，具体参数通过后续授权评测调整。

### 7.16 重排与证据升级的最小边界

状态：**初版已确认**

#### 7.16.1 两类能力必须分开

T06 包含两个容易混淆但权力完全不同的能力：

| 能力 | 回答的问题 | 能做什么 | 不能做什么 |
|---|---|---|---|
| Reranker | 哪些已召回 Child 更可能与当前查询相关 | 在冻结候选池内调整候选选择或顺序 | 不能新增召回、读取原文件、判断事实真假、改变 Scope 或把 Child 变成可引用证据 |
| Evidence Upgrade/Assessment | 读取到的 Atom 是否可用、支持什么、是否冲突或不足 | 校验血缘和原文，形成支持/冲突/unknown 判断并约束引用 | 不能用相关性分数替代证据，不能把无证据推断伪装成来源事实 |

因此 `rerank_score` 只代表查询相关性信号，不是来源权威性、事实可信度或 Claim 支持度。即使一个 Candidate 排名第一，Main Agent 也必须经过 `evidence_read` 才能把其中内容作为事实或引用。

#### 7.16.2 现有资产审计

| 资产 | 可复用内容 | 不直接继承的内容 |
|---|---|---|
| RQ2-C Lightweight Reranker | 冻结 Fusion Candidate Window、固定模型身份、输入/结果 Hash、Parent 多样性、锚点保护、有界 Promotion、零 Promotion 保序 | 不把它设为每次 Search 默认步骤；既有数值阈值不作为 Pure Agent 永久参数 |
| Evidence MCP Read | Child→Source Atom 血缘、Atom-only、Scope/Manifest/Index 校验、Text/Locator Hash、受限上下文展开 | 不保留旧 Run/Manifest 作为唯一外部合同；通过新 Evidence Read Handler 适配 Pure Agent Task Context/Snapshot |
| Fact/Evidence Link、Claim/Citation | 只接受 Context Read Atom、冻结 Text/Locator Hash、Claim 与 Citation 血缘、unknown/conflicted 保守语义 | 不复用固定 Fact Slot、P0-P4 Task、Hard Gate 和固定 Report 作为所有知识问答的必经链 |
| Evidence Validator Schema | `entailed/partial/contradicted/not_supported/unverifiable` 等语义校验思路 | 初版不强制增加独立验证模型调用，是否单独验证由风险与评测决定 |

RQ2-C 历史 Development 结果表明：它能在冻结候选内修复少量 Top-8 挤出且保持逐题零退化，但跨项目增量时延门未通过，Citable Target Availability 门也未通过；最新真实业务 Run 实际使用的是 RQ2-B。因此“代码存在”不能推导出“Pure Agent 初版默认启用”。

#### 7.16.3 Reranker 触发方案

| 方案 | 优点 | 风险 |
|---|---|---|
| 每次 Hybrid Search 后固定执行 | 行为一致，可能改善尾部排序 | 增加实时问答延迟和资源占用；已有增益较小且跨项目准出未通过 |
| 由 Main Agent 传 `rerank=true` | 看似自主灵活 | 模型不了解模型快照、时延和评测门，容易滥用；污染 Tool Input |
| Runtime `RerankPolicyPort` 条件启用，初版默认关闭 | 不暴露底层参数；可按 Profile、预算、风险和质量信号逐步启用 | 需要记录触发理由，并通过评测校准条件 |

**当前推荐：第三种。**

- `RerankPolicyPort` 是 Search Handler 内部策略接口，不是 Tool、Agent 或 Task 状态；
- 初版策略返回 `skip`，除非当前知识域存在经过批准的 Rerank Profile，且 Runtime 条件和时延预算允许；
- Main Agent 不能通过 Tool Arguments 强制开启、选择模型或修改阈值；
- 后续可评估的触发信号包括高影响信息需求、冻结候选足够但通道分歧明显、Fusion 排名区分度低、需要更深 Candidate Window 等；具体信号和阈值不在架构阶段冻结；
- 招标资料和企业知识分别评测、分别发布 Profile，不能因招标资料 Reranker 可用就自动套用到企业知识。

这不会把 Search 变成固定 Workflow：大多数 Search 直接使用 T05 Fusion 结果；只有 Runtime Policy 决定存在预期净收益时，才在同一次 Search 内增加重排派生步骤。

#### 7.16.4 Reranker 的最小输入与权力边界

Reranker 只接收当前授权 Retrieval Snapshot 中已经冻结的 Candidate Window：

- 原始 Query Anchor，不使用整段对话、完整 Plan 或 Memory；
- `retrieval_child` 的稳定 Key、Parent Key、Fusion Rank 和通道 Rank；
- Child 的 `retrieval_text` 及其 Hash；
- Fusion Result Hash、Query Plan Hash、Retrieval Profile/Snapshot 引用；
- 固定 Provider/Model/Revision 和请求预算。

输出只允许表达：

- 每个输入 Child 的稳定 Key、相关性 Score 和 Input Hash；
- 是否被选中、最终 Rank，以及有限 Promotion/Replacement 审计；
- Rerank Profile、Model Revision、Baseline/Final Keys 和 Result Hash。

强制不变量：

- 输出集合必须是输入 Candidate Window 的子集，不能生成新 Candidate 或新文本；
- Reranker 不能改变 Source Domain、Scope、Document/Enterprise Filter、Child/Atom Role 或 Citation 标志；
- 使用原始 Query Anchor，避免生成式 Query Variant 再次漂移用户意图；
- 保护 Hybrid Top Anchor 和明确 Lexical Anchor，重排只允许有界调整，不做无条件全量洗牌；
- 没有满足 Promotion 条件时，最终有序结果必须与 Fusion Baseline 一致；
- Candidate Window、Promotion 数、分数/分差门、Parent 多样性和序列长度由版本化 Profile 治理，不进入模型可见 Tool Schema。

#### 7.16.5 Reranker 失败与降级

Reranker 是可选相关性增强，不是证据权威来源。只要 T05 Fusion Result 已经通过独立血缘校验：

- Provider 临时不可用、执行失败或超时：丢弃本次重排，原样返回冻结 Fusion Baseline，并产生 `rerank_skipped/degraded` Warning；
- Provider 返回缺项、重复、越界、非确定性结果或模型 Revision 不符：拒绝整个 Rerank Result，记录审计/熔断信号，仍可返回未修改的 Fusion Baseline；
- Fusion Result Hash、Query Plan Hash、Snapshot、Candidate Input Hash 或 Scope 不一致：说明上游血缘不能证明，整个 Search 必须 fail-closed，不能以 Baseline 掩盖；
- 如果未来某类受控任务明确把 Rerank 定义为 Required Quality Gate，则 Rerank 不可用时该次 Search 失败；初版不启用这种 Required 模式。

任何降级都不得标记为“已执行完整 Rerank”，也不能使用部分 Provider 分数修改 Baseline。

#### 7.16.6 Evidence Read 是证据升级边界

```text
Search Candidate
  retrieval_child / non-citable
        │ evidence_read(evidence_ref)
        ▼
Evidence Bundle
  evidence_atom(s) / individually validated
        │ Evidence Integrity Guard
        ▼
Evidence Assessment
  supported / partial / conflicted / unsupported / unknown
```

`evidence_read` 必须：

1. 重新校验当前用户权限、Task Context、Evidence Ref 签名和 Source Domain；
2. 校验 Candidate 绑定的 Source Version、Retrieval Snapshot、Child Key 和 Hash；
3. 沿冻结 Child→Source Atom 引用读取，不允许模型提交任意文件路径、对象 Key 或数据库 ID 扩大范围；
4. 验证每个 Atom 的 `is_citable=true`、Text Hash、Locator Hash、Parse/Chunk 血缘和未撤销状态；
5. 返回精确文本、Locator、Source Version、质量/Warning 和 Anchor/Context 角色；
6. 对上下文展开的每个 Atom 独立校验，不能因为与 Anchor 同 Parent 就自动可引用；
7. 在预算截断时明确标记完整性。若只返回 Atom 的部分文本，只能对有 Span/Hash 的已返回片段引用，不能把截断文本当作完整 Atom。

Evidence Bundle 是 Tool Observation，不要求立刻形成 Claim。Main Agent 可以认为证据不相关、继续检索、换来源、标记 unknown 或结束当前回答。

#### 7.16.7 三层证据判断

证据升级后分三层处理：

| 层 | 处理者 | 权威范围 |
|---|---|---|
| Integrity | 确定性 `EvidenceIntegrityGuard` | Scope、版本、Role、Hash、Locator、撤销、Span 和 Child→Atom 血缘；不通过即不能使用 |
| Authority/Precedence | 版本化 `SourceAuthorityPolicy` | 显式修订/替代关系、有效期、来源状态和企业资料验证等级；只处理可确定规则 |
| Semantic Support | Main Agent 的结构化 Evidence Assessment | Atom 对当前陈述是支持、部分支持、矛盾、无关还是无法验证，并说明缺失维度 |

初版不增加独立 Evidence Verifier 模型调用。Main Agent 在使用 Read Atom 形成重要陈述时，同时产生一个最小结构化 Assessment，由 Pydantic 校验；Runtime 的 Integrity/Authority Guard 始终是最终权威。未来只有高风险输出或评测证明自检不足时，才在同一 `EvidenceAssessmentPort` 后增加独立 Verifier 实现，它不是第二个持续 Agent。

#### 7.16.8 最小 Evidence Assessment

Evidence Assessment 是内部 Grounding Record，不是固定用户报告，也不要求每句寒暄、建议或过程说明都持久化。凡是把资料内容作为事实、比较前提或重要风险依据时，至少表达：

```text
statement_ref
statement_text
epistemic_kind       source_fact | inference | unknown
support_status       supported | partial | conflicted | unsupported | unknown
supporting_atom_refs
contradicting_atom_refs
limitations
assessment_reason
```

语义规则：

- `source_fact`：陈述必须由一个或多个有效 Atom 直接支持；数字、日期、主体、否定词和适用范围必须对齐；
- `inference`：引用支持的是前提，最终表达必须明确这是 Agent 推断，不能声称原文直接给出结论；
- `unknown`：说明缺失或冲突在哪里，可以记录已查询范围和限制，但不能用空 Candidate 伪造“资料明确没有”；
- `unsupported`：内部校验结果，陈述不得作为事实发送；Main Agent应删除、改写为假设/unknown 或继续取证；
- `partial`：只允许表达已支持的部分，同时公开未覆盖维度；
- `conflicted`：保留双方 Atom，不能根据 Rerank/Fusion Score 选择“更像真的”一方。

最终回答形式仍然开放。这个 Record 只固定证据责任，不固定用户必须收到哪些字段或章节。

#### 7.16.9 证据充分性不是固定“引用数量门”

初版不规定“一条 Atom 即充分”或“必须两条来源”。充分性取决于当前陈述：

- 简单日期、金额、资格名称可能由一条直接、清晰、当前有效的原文支持；
- 跨来源能力匹配需要分别支持招标要求和企业能力，并对齐主体、时间、规模、范围和证明材料；
- “资料没有规定”之类强缺失结论需要足够范围覆盖、解析质量和无缺页/低质 OCR 等前提，普通 No Result 只能得到 unknown；
- 高影响风险、否决条款、截止日期和硬门判断遇到 Partial/Conflict 时必须保守表达，不能用模型置信度补齐；
- Search/Rerank 分数、命中通道数和引用数量只能作为检索诊断，不直接决定 Evidence Sufficiency。

具体问题的 `completion_criteria` 可以要求哪些维度必须得到支持；Main Agent 根据这些标准形成 Assessment。若信息需求无法满足，Task 仍可完成为“当前证据不足/存在冲突”的回答，不要求强行继续到某个固定事实状态。

#### 7.16.10 来源权威性与冲突处理

来源权威性必须与检索相关性分离。初版采用以下规则：

- 相同 Source Version/Atom Hash 的重复命中只去重，不增加虚假“多来源信心”；
- 存在明确修订、替代、撤销或有效期关系时，`SourceAuthorityPolicy` 可以确定性选择当前有效来源，同时保留被替代证据血缘；
- “日期更新”本身不自动证明全面覆盖旧文件；招标澄清/补遗只有在适用范围和替代关系明确时才覆盖原条款；
- 企业资料的有效期、审核/验证状态和来源主体进入 Authority 判断；企业自述不能覆盖招标文件要求；
- Native/OCR、Parse Quality、缺页、截断和 Warning 影响可用性/充分性，但不由一个通用分数粗暴决定真假；
- 多个有效来源对同一可比陈述给出不兼容值，且不存在确定优先规则时标记 `conflicted`；不得取平均、按最新日期猜测、按检索排名选择或静默丢弃一方；
- Main Agent 可以解释冲突、查询更多资料或请求用户提供决定性信息，但不能绕过确定性撤销/Scope 规则。

招标资料和企业知识需要各自版本化的 Authority Policy；初版不建立一个包打天下的全局来源等级枚举。

#### 7.16.11 引用完整性 Guard

最终把 Evidence Atom 投影为用户可见引用前，Runtime 必须重新验证：

- Atom 已由当前 Task 或有效 Continuation 的 `evidence_read` 读取；
- 当前用户仍有权访问，Source Version/Snapshot 未被撤销或越权；
- `fragment_role=evidence_atom` 且 `is_citable=true`；
- Text Hash、Locator Hash、Source Version 和 Evidence Ref 绑定一致；
- 直接引语与返回文本 Span 精确匹配并有 Quote/Span Hash；转述不伪装成直接引语；
- Citation Display Locator 由 Runtime 根据受控 Locator 构造，不接受模型生成内部路径或页码；
- 引用与 Grounded Statement 的支持关系通过 Assessment，冲突证据不能被标成单边支持；
- 被截断、Stale、Superseded 或质量不足的证据按 Policy 明确限制或拒绝。

任何 Citation Guard 失败都不能把无效引用发送给用户。Runtime 可以让 Main Agent删除该陈述、改写为 unknown、重新 Read 或返回安全错误；具体响应修复协议在 T12 继续设计。

#### 7.16.12 推荐的最小组件形态

```text
Hybrid Candidate Window
    └── RerankPolicyPort (default skip)
            └── optional BoundedRerankerAdapter

Evidence Read Handler
    ├── EvidenceRefResolver
    ├── EvidenceIntegrityGuard
    ├── SourceAuthorityPolicy
    └── EvidenceBundleProjector

Main Agent
    └── EvidenceAssessmentPort

Response Boundary
    └── CitationIntegrityGuard
```

这些都是逻辑接口，不要求首版拆成微服务、额外 Tool 或多个常驻 Agent。旧 Fact/Claim/Citation 表可作为血缘设计参考，但 Pure Agent 的开放问答不经过固定 Fact Slot 或 Report Task 链。

#### 7.16.13 本轮推荐结论

1. Reranker 只做冻结 Candidate Window 内的相关性调整，与证据升级、权威性和事实判断严格分离；
2. 采用内部 `RerankPolicyPort` 条件启用，初版默认 `skip`；模型不能传 `rerank=true` 或底层参数；
3. 若启用，复用 RQ2-C 的冻结候选、固定模型血缘、锚点保护、有界 Promotion 和零 Promotion 保序原则，数值另由评测治理；
4. 可选 Reranker 失败时回到完全未修改的 Fusion Baseline；上游 Scope/Hash/Snapshot 血缘失败则 Search fail-closed；
5. `evidence_read` 是 Child Candidate 升级为 Atom Evidence 的唯一边界，每个 Atom 独立做 Scope/Role/Hash/Locator/撤销和 Span 完整性校验；
6. 使用 Integrity Guard、Source Authority Policy 和 Main Agent Evidence Assessment 三层判断，不用检索/重排分数判断真假；
7. 内部 Grounding Record 区分 `source_fact/inference/unknown` 及 `supported/partial/conflicted/unsupported/unknown`，但不固定用户输出格式；
8. 明确修订关系可确定性处理；无优先规则的有效证据冲突必须保留并标记 conflicted；
9. 最终 Citation 必须通过 Runtime Citation Integrity Guard，直接引语校验精确 Span/Hash；
10. 独立 Evidence Verifier 模型初版不启用，只在高风险场景和评测证明需要后接入 `EvidenceAssessmentPort`。

### 7.17 Function Calling 与 MCP 的统一调用协议

状态：**初版已确认**

#### 7.17.1 Function Calling 和 MCP 不是二选一

两者处在不同层：

```text
Main Agent Model
    │ Function Calling：提出“调用哪个 Tool、参数是什么”
    ▼
Provider Adapter
    │ 规范化 Tool Call Proposal
    ▼
Tool Gateway
    │ Schema / Guard / Safety / Idempotency
    ▼
Execution Binding
    ├── Local Handler
    └── MCP Client → MCP Server Tool
    ▼
Output Validation → ToolExecutionResult → Tool Message
    ▼
Main Agent Model
```

- **Function Calling** 是模型提出结构化动作的协议；
- **Local/MCP** 是 Runtime 真正执行动作的绑定方式；
- 模型始终只看到 Canonical Tool Name、Description 和 Input Schema，不知道该 Tool 最终由本地函数还是 MCP 执行；
- MCP Server 不直接向模型注册 Tool，也不通过动态发现绕过 Canonical Registry 和每轮白名单。

因此初版采用“Function Calling + 可选 MCP Execution Binding”，而不是建立 Function Tool Registry 与 MCP Tool Registry 两份事实源。

#### 7.17.2 现有资产与简化方向

| 现有资产 | 可复用 | Pure Agent 中不直接继承 |
|---|---|---|
| OpenAI-compatible Native Tool Calling | Assistant `tool_calls`、Provider Call ID、Tool Message 关联方式 | 硬编码 `TOOL_SCHEMAS`、旧 Tool Name 和固定全量 Tool 列表 |
| Evidence MCP/FastMCP | Structured Output、只读 Annotations、Streamable HTTP、Search/Read 隔离服务 | 旧 Run/Manifest 固定外部合同、服务端函数签名作为第二份 Canonical Schema |
| MCP Client Adapter | Session、认证、超时、`structuredContent` 读取 | 直接把 MCP 错误文本或未经 Canonical Output Model 校验的数据回流模型 |
| Durable Tool Dispatch | 调用账本、Envelope Hash、Scope Token、Lease/Fencing、幂等与不确定结果思路 | 首批四个只读实时工具全部排队、Worker Claim 和多层 Dispatch 状态机 |
| Tool Result Compact/Store | 有界模型投影、大结果不直接塞入 Context | 旧 24 KiB 常量和已退出 Active Registry 的通用 `tool_result.read_slice` Tool |

首批四个 Tool 都是只读、Context-bound、无外部数据流出和无需逐次业务审批。初版推荐直接在 Agent Runtime 内有界异步执行，并在执行前后写 Tool Call Ledger/Checkpoint；不为每次读取创建耐久队列。以后出现长时 Tool、写操作、第三方副作用或进程间可靠投递需求时，再接入 Durable Dispatch Port。

#### 7.17.3 Canonical Registry 是唯一工具事实源

已确认的六字段 `CanonicalToolDefinition` 保持不变：

```text
name
description
input_model
output_model
execution
safety
```

其中：

- `input_model/output_model` 是手工维护的 Pydantic 合同事实源；
- Provider Function Schema、内部 Local 调用参数和项目内 MCP Input/Output Schema 都从它们投影；
- `execution` 只绑定 `local(handler_id)` 或 `mcp(server_id, remote_tool_name)`；
- Registry Snapshot 保存最终 Definition/Input/Output/Execution/Safety Hash；
- Provider Adapter、Executor 和 MCP Client 不维护独立 Tool Name→Handler Map；
- MCP `list_tools` 只用于连接健康和合同兼容性检查，不能自动把远端 Tool 加入 Registry 或模型白名单。

项目自有 MCP Server 应从相同 Pydantic Model 生成 Schema，减少双写。外部 MCP 无法共享代码时，其远端 Schema 只是依赖合同；MCP Binding 必须在启用前做兼容性检查和 Adapter 映射，Canonical Definition 仍是 Agent 侧权威。

#### 7.17.4 模型侧 Function Calling 投影

每个模型决策轮次从冻结的 `visible_tools` 生成 Provider-neutral 合同：

```text
name        <- definition.name
description <- definition.description
parameters  <- ProviderSchemaProjector(input_model JSON Schema)
strict      <- Provider 支持且投影兼容时 true
```

首版规则：

- 默认 `tool_choice=auto`；只有 Main Agent 已形成“当前动作必须使用某个白名单 Tool”的结构化决定时才使用 `required/forced`；
- 没有必要 Tool 时发送空工具集合，而不是强迫模型调用一个无关 Tool；
- Function Calling 不提前发送完整 Output Schema，模型在执行后接收已校验的实际结果；
- Provider 不支持某些 JSON Schema 约束时，Projector 必须产生兼容性报告；关键约束无法表达时该 Provider/Tool 组合不可用，不能静默删除；
- 即使 Provider 开启 `strict`，Gateway 仍必须重新执行 Pydantic、业务和权限校验；
- 发送给 Provider 的最终 Tool JSON、Registry Snapshot Hash、Schema Hash 和 `visible_tool_names` 进入 Model Turn Ledger。

#### 7.17.5 规范化 Tool Call Proposal

Provider 返回的原始 `tool_calls` 先由 Adapter 规范化为内部 Proposal，不直接交给 Executor。最小语义：

```text
model_turn_ref
provider_tool_call_id
sequence
tool_name
raw_arguments_json / arguments
registry_snapshot_hash
visible_tools_hash
state_version
```

规范化规则：

- Provider Tool Call ID 必须在当前响应内非空且唯一，用于后续 Tool Message 关联；
- Tool Name 必须存在于产生该调用的冻结白名单，未知或过期 Name 不执行；
- Arguments 必须是一个 JSON Object；解析失败、非对象、重复关键字段或超出大小上限均为 `invalid_arguments`；
- 原始 Arguments Hash 与规范化后的 Arguments Hash 都进入 Ledger，禁止 Adapter 静默增加权限字段或改变模型语义；
- 一个响应可以包含多个 Tool Call Proposal，但每个调用独立校验、授权和记账；初版默认按响应顺序执行，只有相互独立的只读调用通过后续并发评测才允许有界并行；
- Provider 的自然语言内容不能覆盖同一响应中的 Tool Call 参数或 Runtime 权限。

Tool Call Proposal 是 `running` 内的一次动态 Action，不增加 `tool_calling/executing` 等 Task 顶层状态。

#### 7.17.6 Gateway 权威顺序

执行顺序固定为协议安全顺序，但不规定业务上必须调用哪些 Tool：

```text
1. Provider Envelope / Tool Call ID 校验
2. Registry Snapshot + Frozen Visible Tools 校验
3. input_model Pydantic 校验
4. 引用、Cursor、业务字段和数据就绪校验
5. Permission Guard Execution Authorization
6. Safety / Approval / Budget / Deadline 校验
7. 幂等登记与 Effect Fence
8. Execution Binding 调用
9. 传输结果大小/类型校验
10. output_model Pydantic + Provenance 校验
11. ToolExecutionResult 包装并写 Observation
```

权威顺序：

```text
Provider strict
    < Gateway input_model
    < Business/Permission/Safety Guard

MCP advertised Schema / server validation
    < Canonical output_model + Runtime Provenance Guard
```

Provider/MCP 校验是早期防错层，Runtime Gateway 始终是 Agent 是否接受调用和结果的最终权威。任何层失败都不能把 Handler 原始输出、MCP Stack Trace 或未经校验的部分数据写入模型 Context。

#### 7.17.7 Local 与 MCP 共用 ExecutionBindingPort

内部统一签名候选：

```python
async def execute(
    *,
    definition: CanonicalToolDefinition,
    args: BaseModel,
    context: ToolExecutionContext,
    call: AcceptedToolCall,
    deadline: Deadline,
) -> object:
    ...
```

两种 Binding：

| Binding | 行为 |
|---|---|
| Local | 根据 Registry 中的 `handler_id` 解析受信 Handler，直接传入已验证 Input Model 与最小 `ToolExecutionContext` |
| MCP | 根据 `server_id` 解析安全连接配置，将 Canonical Arguments 映射到 `remote_tool_name`，通过受控 MCP Client 调用并只接受 `structuredContent` |

共同不变量：

- Handler/Remote Tool 只收到当前调用所需的最小 Context，不接收完整 Message History、Plan、Memory 或 Agent State；
- `ToolExecutionContext` 不序列化进 Function Arguments；
- Local Handler 不依靠闭包偷偷捕获用户权限或全局 Scope；
- MCP URL、Token、密钥、连接池和传输类型只由 `server_id` 的安全配置解析，不进入 Registry Snapshot 的模型投影；
- Execution Binding 变化可以发布新 Registry Snapshot，但不改变模型 Tool Name 和业务语义。

#### 7.17.8 MCP Context、认证和合同漂移

MCP Binding 不能把完整 `ToolExecutionContext` 当作 Tool Arguments 发给远端。它只派生短期、最小权限的调用凭证或服务端 Auth Context，至少绑定：

- 调用主体/租户的不可伪造引用；
- 允许的远端 Tool；
- 当前 Source Scope/Snapshot 的不透明引用或 Scope Hash；
- Internal Call ID、Audience、到期时间和一次性/幂等约束；
- 是否允许外部数据流出及当前环境标识。

远端 MCP Server 必须再次验证凭证和资源 Scope，不能仅因为请求来自内网就信任。模型 Arguments 中出现用户 ID、租户 ID、Manifest ID 或 Scope Token 时也不能覆盖 Auth Context。

合同漂移处理：

- 连接预检可以读取远端 Tool Schema/Annotations 并计算 Remote Contract Hash；
- 缺少 Tool、Input 不兼容、Output Schema/Annotations 与 Canonical Safety 冲突时，Binding 标记 unavailable，不进入 `visible_tools`；
- 运行中返回的 `structuredContent` 仍必须经过 Canonical `output_model`；
- MCP 文本 `content`、日志、Resource Link 或自定义元数据默认不进入模型，除非某个 Canonical Output Model 明确声明并通过安全投影；
- 外部 MCP Tool 不能通过自己的 Description 改写 Canonical Description 或扩张用途。

#### 7.17.9 ToolExecutionResult 与 Tool Message

已确认的公共 Envelope 保持：

```json
{"ok": true, "data": {}, "error": null}
```

或：

```json
{"ok": false, "data": null, "error": {"code": "unavailable", "message": "...", "retryable": true}}
```

协议规则：

- Local/MCP 原始结果先映射并通过对应 `output_model`，再包装为 Envelope；
- `no_result` 是成功数据，例如 `candidates=[]`，不是传输错误；
- T05/T06 中模型行为必须知道的 Degraded/Limitations 放在具体 Search Output Model 的安全字段中；Provider/MCP 诊断、尝试次数和完整 Warning 留在 Ledger，不扩张公共 Envelope；
- Tool Message 的 `tool_call_id` 必须精确等于 Provider Proposal ID，`content` 是 Canonical JSON 序列化后的 `ToolExecutionResult`；
- Tool Message 不拼接 Handler 日志、Markdown、Stack Trace、MCP 原始错误或额外自然语言；
- 内部 Call ID、Registry/Schema Hash、Scope Decision、时延和 Attempt 信息通过 Ledger 与 Provider Tool Call ID 关联，不重复塞给模型。

若 Provider 发生 Failover，新模型只能接收已经接受并持久化的 Tool Observation；不得因切换 Provider 而重新执行已完成调用。重复交付相同 Tool Message 必须由 Call Ledger/Checkpoint 幂等处理。

#### 7.17.10 大结果和上下文预算

首批 Tool 的 `output_model` 本身必须有界；Search 返回少量 Candidate，Read 返回受限 Atom/Span，Outline 返回受限结构条目。初版不恢复通用 `tool_result.read_slice` Tool。

当 Handler 或 MCP 产生大结果时：

1. 先在受控 Result Store 保存通过完整合同与安全校验的结果；
2. 给模型的只是由具体 `output_model` 定义的有界投影和不透明 Result/Cursor Ref（仅当该 Tool 合同确实需要）；
3. 后续继续读取仍使用原 Domain Tool 的受控 Cursor/Ref，不接受任意 Offset、文件路径或对象 Key；
4. 超出传输硬上限且无法形成合法有界投影时整次调用失败，不能把截断 JSON 伪装成成功；
5. Context Assembler 再根据模型窗口预算选择 Observation 内容，不能改写已验证的业务字段或 Evidence 文本。

Result Store 的内容保留 Scope、Snapshot、Schema Hash、内容 Hash 和过期策略；一个会话的 Result Ref 不能在另一个越权 Context 中使用。

#### 7.17.11 Deadline、取消、重试与幂等

首版四个 Tool 使用 Runtime 统一 Deadline，并将剩余时间传给 Local Handler 或 MCP Client：

- Task `cancelled`、用户中止或 Deadline 到期后，不再接受新的执行结果写入当前 State Version；
- Local Handler 应响应取消信号；MCP Client 发送协议支持的取消并关闭等待，但 Runtime 不假设远端一定已经停止；
- 首批四个只读 Tool 在传输超时/暂时不可用时可以按 Runtime Policy 使用同一 Idempotency Key 有界重试；参数错误、权限拒绝、Not Found 和合同违约不自动重试；
- Idempotency Key 至少绑定 Task/State Version、Model Turn、Provider Call ID、Tool Name、Arguments Hash、Context/Scope/Snapshot Hash 和 Registry Snapshot Hash；
- 同一 Accepted Tool Call 重复投递只能产生一个权威 Observation；跨不同 Context 的相同 Query 不因文本相同而错误复用结果；
- 未来写 Tool 必须另行设计 `safe_idempotent/reconcile_required/no_replay` 和不确定结果处理，不能从“当前四个只读工具可重试”推导出所有 Tool 都可重放。

`pending` 仍只用于等待用户 Slot，不用于等待一个正常运行的 Tool。短时 Tool 调用保持 Task=`running`；真正长时异步操作的 Checkpoint/恢复在 T13 运行治理中设计。

#### 7.17.12 用户可见流式事件

Runtime 可以向 UI 流式展示安全事件：

```text
tool_call_started
tool_call_completed
tool_call_failed
```

事件只包含安全的 Tool Display Name、状态和必要的进度摘要，不暴露完整 Arguments、Query Expansion、权限决策树、MCP URL/Token、内部 ID 或原始异常。MCP Progress Notification 可以更新 UI 进度，但不作为模型 Observation；只有完整结果通过 Output/Provenance 校验后才发送 Tool Message 给 Main Agent。

这类事件是可观察性，不是 Agent Workflow 状态，也不改变五个 Task 顶层状态。

#### 7.17.13 首批四个 Tool 的绑定建议

以下只是初版实现建议，真正绑定由 Registry Snapshot 冻结：

| Canonical Tool | 初版建议 |
|---|---|
| `documents_outline` | Local Handler，直接读取已授权结构 Head |
| `bid_document_search` | 可以复用现有 Evidence MCP 的 `evidence.search`，由 Binding 完成 Canonical Name/Arguments/Output 适配 |
| `enterprise_knowledge_search` | 先使用 Local Adapter 接入 T04 企业知识索引；未来独立部署时可无感切换 MCP Binding |
| `evidence_read` | 使用 Local Facade 解析不透明 Evidence Ref，再调用招标/企业各自 Source Adapter；避免模型面对两个 Read Tool，也避免现有 Bid-only MCP 无法读取企业 Evidence |

如果实现时发现进程边界并无隔离收益，`bid_document_search` 也可以改为 Local Binding；这不改变 Canonical Tool 合同。相反，不能为了“使用 MCP”而把本地简单读取强行拆成新服务。

#### 7.17.14 本轮推荐结论

1. 模型侧统一使用 Function Calling，执行侧由 Canonical Definition 选择 Local 或 MCP；两者不是替代关系；
2. Canonical Registry/Pydantic Model 是唯一事实源，Provider/MCP Schema 都是投影或外部依赖合同；MCP Discovery 不自动注册 Tool；
3. 每个 Tool Call 先规范化并绑定 Model Turn、Provider Call ID、Registry/Visible Tools/State Version，再进入 Gateway；
4. Provider strict 和 MCP Schema 都是早期防错，Pydantic、业务/权限/Safety Guard 和 Output/Provenance 校验才是 Runtime 权威；
5. Local/MCP 共用 `ExecutionBindingPort`，Runtime Context 通过 DI 或短期最小权限 MCP Auth Context 传递，永不进入模型 Arguments；
6. MCP 只接受 `structuredContent`，并再次通过 Canonical `output_model`；原始文本、日志和异常不回流模型；
7. Tool Message 使用原 Provider Call ID 和 Canonical `ToolExecutionResult` JSON；运行元数据留在 Ledger；
8. 首批四个只读 Tool 直接有界异步执行，不复用旧 Durable Dispatch 作为每次调用必经队列；
9. Output Model 和 Context Projection 都必须有界，初版不恢复通用大结果 Slice Tool；
10. Deadline、取消、只读重试和 Idempotency 由 Runtime Policy/Effect Fence 管理，`pending` 不承担 Tool 等待；
11. 多 Tool Call 初版按响应顺序执行，独立只读并行由后续评测启用；
12. UI 可流式展示安全 Tool 事件，但只有完整验证结果才成为模型 Observation。

### 7.18 Memory 的分层、写入、读取和遗忘边界

> 状态：初版已确认。本节只定义 Memory 的职责和最小治理边界；窗口预算、近邻消息数量、摘要压缩和索引回取算法留到 T11。

Memory 需要解决的是“允许 Agent 在合适的时间和 Scope 下复用什么”，而不是“把所有历史内容再次塞回模型”。如果没有边界，Memory 会同时引入旧事实污染、跨租户泄露、提示注入、用户纠正失效和 Context 膨胀；如果完全不保存，又会丢失同一任务恢复、会话承接、项目结论和用户明确偏好。

#### 7.18.1 先分开四类不同资产

以下对象不能混成一张“Agent Memory”概念表：

| 对象 | 职责 | 是否是 Memory | 权威边界 |
|---|---|---|---|
| Agent Task State / Continuation Checkpoint | 精确恢复当前执行位置、Slot、Effect Fence 和 State Version | 否，是运行恢复状态 | 只对执行恢复权威，不证明业务事实 |
| Conversation Message | 保存用户与 Agent 实际说过什么 | 否，是不可变会话记录；可成为 Memory Source | 只证明消息发生过，不自动证明消息内容为真 |
| Tool Observation / Result Store | 保存一次已校验工具调用的结果和血缘 | 否，是当前任务观察资产 | 权威不超过其 Source、Snapshot 和 Grounding 状态 |
| Evidence / RAG Source / Business Record | 保存原文、企业知识、项目数据和可引用事实 | 否，是事实源 | 继续作为对应业务事实的权威来源 |
| Memory Record | 保存可在以后复用的偏好、状态摘要、结论索引和 Source 引用 | 是 | 是 Context 辅助，不会因被记住而升级为事实 |
| Context Projection | 为当前模型轮次选出的有界输入 | 否，是读时投影 | 必须保留各项来源和权威等级 |

现有 `BidCheckpoint.state_json`、Evidence/Fact/Claim、Tool Invocation/Result 和项目/研判数据可以继续作为 Source 或恢复资产，但不能直接改名后充当跨会话 Memory。旧 `BidIntakeAgentRun.state_summary_json`、LangGraph Checkpointer 和旧 Workflow Checkpoint 只保留历史兼容，不作为 Pure Agent Memory 权威。

#### 7.18.2 四个逻辑层，不等于四套服务

首版采用四个逻辑层：

| 层 | Scope 与生命周期 | 保存内容 | 默认写入 | 默认读取 |
|---|---|---|---|---|
| Working Memory | 单个 Agent Task；随 State Version 演进，终态后退出活动 Context | 当前目标、Understanding Decision、当前 Plan/Action 引用、Slot/Pending 引用、已接受 Observation/Evidence 引用、开放问题和限制 | Runtime 自动、版本化写入 | 当前 Task 恢复和下一模型轮次 |
| Conversation Memory | 单个 Conversation；跨该会话中的多个 Agent Task | 不可变 Message 引用、当前主题、已引用项目/资料、用户纠正、尚未解决的跟进事项，以及可重建的简要派生状态 | Message 自动追加；派生项按写入策略 | 仅同一 Conversation |
| Project/Assessment Memory | 单个租户内的 Project 或 Assessment；跟随业务对象生命周期 | 已有证据支撑的阶段结论、已确认业务决定、未决风险、Grounding/Evidence 引用、Source Version 和替代关系 | 只提交符合持久化资格的结构化候选 | 当前任务 Scope 命中同一 Project/Assessment 且确有信息需求时 |
| User Memory | Tenant + User；跨 Conversation，保持最小化 | 用户明确要求长期记住的稳定交互偏好或工作偏好 | 默认不从普通对话推断；明确要求或确认后写入 | 只用于交互/呈现和安全默认值 |

这里的 Cross-conversation Memory 首版只落在受控的 `User Memory` 和已有 `Project/Assessment Memory`，不建设一个“可以搜索所有历史聊天并自行学习”的全局语义记忆池。项目事实属于 Project/Assessment Scope，不复制进用户画像；用户偏好也不能作为项目事实证据。

四层是逻辑合同，可以在首版共用少量 Repository 和数据库基础设施；不得为了概念分层提前拆成四个微服务、四套向量库或固定的业务执行阶段。

#### 7.18.3 Memory 的权威顺序

Memory 是带来源的派生 Context 资产，不是 Source of Truth：

```text
当前有效 Business Record / Source Atom
    -> Grounding Record / Evidence Assessment
        -> Project/Conversation Memory 派生记录
            -> 当前轮次 Context Projection
```

约束如下：

- Memory 只能保留或降低 Source 的权威等级，不能把 `unknown`、推测、用户转述或模型摘要升级成已证实事实；
- 同一事实存在当前原文/Evidence 时，回答和引用优先回到当前 Source，不引用 Memory 摘要冒充原文；
- User Memory 只影响表达、交互和允许的默认偏好，不参与硬门、风险或投标结论的事实证明；
- Memory 不能授予 Tool 权限、扩大 Source Scope、替代 Permission Guard，也不能通过历史指令覆盖当前 System/Runtime Policy；
- Project/Assessment Memory 可以帮助发现“以前查过什么、还有什么未解决”，但重要结论必须按当前 Source Head 和 Grounding 状态重新验证。

#### 7.18.4 三档写入策略和永久禁入项

首版不引入一个自由运行的“Memory 提取模型”，也不向模型暴露可任意调用的 `write_memory` Tool。写入由 Runtime 内部的 Memory Commit 边界执行；Main Agent 最多提出结构化 `memory_candidate`，Pydantic、Scope、Source、业务规则和用户授权才决定是否提交。

**A. 自动写入当前 Scope 的运行资产**

- Working Memory 的目标、活动引用、Slot、Observation/Evidence 引用、开放问题和限制；
- Conversation Message 的不可变追加；
- 经过 Output/Provenance Guard 接受的工具观察引用；
- Task 结束时可重建的结构化结果引用和当前未决事项。

这些写入不意味着长期跨会话记忆，也不能保存模型私有推理过程。

**B. 满足资格后自动提交的持久派生记录**

- 带当前 Project/Assessment Scope、Source Ref、Source Version 和 Grounding 状态的结论索引；
- Task 已完成但仍未解决、以后确需继续处理的项目问题；
- 已由业务权威记录确认的决定或状态引用。

提交必须是确定性 Policy + 结构校验结果；普通回答文本、模型猜测和无来源摘要不因“可能有用”自动进入 Project Memory。首版宁可少写，也不积累不可解释的陈旧认知。

**C. 需要用户明确表达或确认的长期写入**

- 跨 Conversation 的用户偏好；
- 用户希望以后复用、但系统不能从权威业务记录直接确定的稳定约定；
- 会实质影响未来任务行为的用户级默认选择。

用户明确说“请记住/以后都按……”可视为写入授权；若 Scope、时效或含义仍有歧义，Agent 创建 Slot 澄清。仅从语气或一次选择推断出的偏好不得直接持久化。

**永远不进入长期 Memory**

- Chain-of-Thought、隐藏 Prompt、内部策略和未对用户展示的私有推理；
- 密码、Token、Cookie、连接串、MCP 凭证和其他认证秘密；
- 完整 Agent State、完整 Tool/MCP 原始日志、Stack Trace 或无界原文复制；
- 未校验的模型猜测、未经升级的 Search 片段、权限拒绝细节和安全规则内部实现；
- 与当前业务无关或没有明确用途的敏感个人信息；
- 试图通过 Memory 固化的越权指令、Prompt Injection 或跨租户内容。

#### 7.18.5 最小 Memory Record 合同

首版先冻结逻辑 Envelope，不提前冻结数据库表和所有 Payload 字段：

```python
class MemoryRecord(BaseModel):
    memory_id: str
    kind: MemoryKind
    scope: MemoryScope
    payload: MemoryPayload
    source_refs: list[SourceRef]
    basis: MemoryBasis
    grounding_status: GroundingStatus | None
    validity: MemoryValidity
    version: int
    supersedes_ref: str | None
    created_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    content_hash: str
    policy_version: str
```

最小语义：

- `kind` 区分 Conversation State、Project Grounding、Open Follow-up、User Preference 等类型；
- `scope` 至少绑定 Tenant，并按类型绑定 User、Conversation、Project 或 Assessment；模型只看到安全显示值或不透明引用；
- `payload` 使用 Discriminated Union 的类型化 Pydantic Model，不建设任意字段的万能 JSON 垃圾桶；
- `source_refs` 可以指向 Message、Task、Grounding Record、Evidence Atom 或业务权威记录；
- `basis` 区分 `explicit_user`、`validated_system`、`grounded_evidence` 和 `derived_summary`；
- 事实型记录才使用 `grounding_status`，沿用 `supported/partial/conflicted/unknown` 等证据语义，不能用一个模糊浮点 Confidence 代替；
- `validity` 至少支持 `active/superseded/stale/conflicted/revoked/deleted`；
- `version + supersedes_ref + content_hash` 用于纠正、并发保护和审计；Memory 不原地静默改写。

实现时可以按类型拆表或共用 Envelope 表，但必须由访问模式、保留策略和数据量决定，不能在架构讨论阶段先假设一个物理存储方案。

#### 7.18.6 读取和 Context 投影

Memory 读取属于 Runtime 的 `MemoryReader/Context Assembler` 内部能力，不是首批 Function Calling Tool。模型不会因为 Memory 存在就自动看到全部历史。

每次读取至少按以下顺序约束：

1. 先用当前 Auth Context 做 Tenant/User/Conversation/Project/Assessment Scope 过滤；语义相关性不能先于权限过滤；
2. 只选择 `active` 且未过期的当前版本，排除 `superseded/revoked/deleted`；`stale/conflicted` 只有在当前问题需要展示不确定性时才带明确标签进入 Context；
3. 根据当前 Understanding Decision 和 Information Need 决定是否需要 Conversation、Project 或 User Memory；
4. 按稳定业务 Key、Source Ref 和 Content Hash 去重，当前 Source 与直接 Evidence 优先于派生摘要；
5. 对 Project/Assessment Memory 校验 Source Head/Version 和访问权限；失效引用不能静默指向一个新的 Source 版本；
6. 生成有界、安全、带类型和来源标签的 Context Projection，明确其是“历史偏好”“派生摘要”还是“当前证据引用”；
7. Memory 内容始终作为不可信数据段进入模型，不能拼接进 System Prompt，也不能把历史用户指令提升为系统规则。

Working Memory 和当前恢复所需 State 引用是当前 Task 的基础输入；Conversation/Project/User Memory 是否进入某个模型轮次必须按相关性选择。具体保留最近几轮、每层 Token 预算、摘要层级和 Index + Content Block 回取方式在 T11 决定。

#### 7.18.7 用户纠正、Source 变化和冲突

- Conversation Message 保持不可变；用户纠正通过新 Message/Event 和新 Memory Version 表达，不回写历史文本；
- 对用户偏好的明确纠正可以直接生成新版本并 `supersedes` 旧记录；
- 用户声称某个项目事实有误时，先记录为 `explicit_user` 的纠正声明，不能在没有业务记录或 Evidence 支撑时直接改成 `supported`；
- Source Head、文档版本、权限或业务记录发生变化时，依赖旧 Source Ref/Hash 的派生 Memory 标为 `stale` 或 `revoked`，以后默认不作为当前事实读取；
- 新旧 Evidence 冲突时保留双方 Source Ref，记录为 `conflicted`，不静默挑选有利版本；
- 已经产生的历史回答仍是历史事件，但其派生 Memory 可以失效；后续回答必须显示当前版本和必要的变化说明；
- 更新使用 State/Record Version 或 Compare-and-Swap，避免两个并发 Task 相互覆盖。

#### 7.18.8 Pending、Checkpoint 和恢复

`pending` 恢复依赖 Continuation Checkpoint，不依赖“模型自己记得”。Checkpoint 保存暂停时 Working Memory/Context Snapshot 的精确引用；用户输入后按以下边界继续：

1. 追加新的 Conversation Message；
2. 通过 Slot 的 Pydantic 格式校验和业务校验；
3. 原子写入 Slot Result、消费 Checkpoint、生成新的 Working Memory/State Version，并恢复 `running`；
4. 默认从 suspended Action 继续，不重跑已完成 Tool Call；
5. 若恢复时 Source 已撤销、权限已变化或新输入实质改变目标，由 Main Agent 在 `running` 内重新判断剩余行动，而不是让旧 Memory 覆盖新事实。

任务处于 `pending` 期间，其他 Conversation/Project Memory 的变化不能静默改写已暂停 Action。恢复时以 Checkpoint Snapshot 为起点，再执行权限、Source 状态和新输入的必要再验证。

#### 7.18.9 遗忘、删除和保留期

用户可以用自然语言请求查看、纠正或遗忘 Memory；这属于 Runtime 的受控内部操作，不要求增加通用 Memory Tool：

- Scope 明确且用户有权限时，“忘记”立即把对应记录标记为不可检索并驱逐相关 Cache/Index；Scope 不明确时创建 Slot 澄清；
- 逻辑 Tombstone 先保证后续 Context 不再读取，物理清除按产品保留期、备份和合规策略异步完成；
- Working Memory 在 Task 终态后退出活动 Context，运行审计仅按独立审计策略保留必要引用；
- Conversation Memory 跟随 Conversation 的保留/删除策略；Project/Assessment Memory 跟随对应业务对象和审计义务；User Memory 必须支持用户查看、纠正和删除；
- “忘记一个记忆”不会越权删除招标原文、Evidence、企业知识或业务记录；删除权威 Source 是另一项有权限和审计要求的业务操作；
- 权威 Source 被删除或撤销时，所有派生 Memory 必须同步失效；
- 删除审计可以保留不含原始内容的删除事件、Hash 和时间，但不能继续让被删除内容进入模型；
- 首版不为 Memory 建独立向量索引。未来如增加索引，Tombstone、物理删除、Scope Filter 和 Cache 驱逐必须形成一致性闭环后才能启用。

#### 7.18.10 安全与隔离不变量

- 所有读写都先绑定 Tenant 和具体 Scope，禁止跨租户、跨用户或跨项目的默认检索；
- Memory Write 和 Read 都重新执行当前权限检查；用户角色或项目权限变化后，旧记录不能继续泄露内容；
- Memory 中的历史文本、摘要和用户指令均按不可信数据处理，不能覆盖 System Policy、Tool Guard 或 Safety Policy；
- Memory Ref、Scope Ref 和 Source Ref 对模型使用不透明或安全投影，不能成为枚举其他资源的入口；
- 不从私有会话自动生成“全企业共享知识”；共享知识必须走现有企业知识治理、审核和版本发布流程；
- 不在 Memory 中复制认证秘密或无界原始文档，敏感字段采用最小化存储、访问审计和适当加密。

#### 7.18.11 最小组件形态

首版只需要三个逻辑边界，可以同进程实现：

```text
Main Agent / Runtime Event
        |
        | optional memory_candidate / explicit remember-forget intent
        v
Memory Policy Guard + Committer
  - Pydantic / Scope / Source / Authorization / Retention
        |
        v
Memory Repository
        ^
        |
Memory Reader + Safe Projection
        |
Context Assembler
```

- `Memory Policy Guard + Committer` 决定能否写、写到哪个 Scope、是否需要用户确认，以及如何版本化；
- `Memory Repository` 保存类型化记录、版本、Tombstone 和 Source 依赖；
- `Memory Reader + Safe Projection` 只返回当前授权、有效、相关且有界的投影；它可以作为 Context Assembler 内部模块，不必独立部署；
- Source Version/权限变化通过事件或读时校验使派生记录失效，首版不建设复杂 Memory Graph；
- Memory Commit 是可选的运行副作用：一个 Task 可以写零条或多条记录，不要求每个 Task 固定经过“提取记忆 -> 写记忆”阶段，因此不形成 Workflow。

#### 7.18.12 初版确认结论

1. 采用 Working、Conversation、Project/Assessment、User 四个逻辑层，但不预设四套物理服务；
2. Checkpoint、Message、Observation、Evidence/业务事实、Memory 和 Context Projection 保持职责分离；
3. Memory 只是带来源、可版本化、可失效的 Context 辅助，事实权威仍是当前 Business Record、Source Atom 和 Grounding Record；
4. 首版不提供模型可任意调用的 `write_memory` Tool，也不运行独立的自由 LLM Memory Extractor；由 Main Agent 提候选、Runtime Policy Guard 最终提交；
5. 当前 Task/Message 自动写入；持久项目记忆必须有 Scope、Source 和资格；跨会话用户偏好需要明确表达或确认；敏感秘密、私有推理和未验证猜测永久禁入；
6. Memory 读取先做权限和 Scope，再做有效性和相关性选择，旧摘要不能覆盖当前原文，也不能成为 System 指令；
7. 用户纠正和 Source 变化通过新版本、Supersede、Stale/Revoked/Conflicted 表达，不静默覆写；
8. “忘记”立即停止未来检索，但不会越权删除独立的业务事实源；物理清除遵循保留和审计策略；
9. Pending 恢复以 Continuation Checkpoint 为准，Memory 只提供辅助 Context；恢复时重新验证新输入、权限和 Source 状态；
10. 首版不建设 Memory Vector Index；窗口压缩、摘要算法、Token Budget 和回取策略进入 T11。

### 7.19 Context Engineering 的窗口、选择、压缩和回取边界

> 状态：初版已确认。本节冻结首版 Context 的职责、安全不变量和降级顺序；具体 Token 数、近期消息数量、压缩模型和阈值保留为版本化 Profile 参数，后续通过获得授权的评测校准。

Context Engineering 解决的是“当前这一次模型调用应该看到哪些经过授权且真正有用的内容，以及以什么表示看到”。它不是把数据库、聊天历史和工具结果全部拼进 Prompt，也不是让模型自己决定哪些安全规则可以丢弃。

#### 7.19.1 现有资产可以复用什么

旧 `BidContextManifest` 有四个值得复用的概念：

- 每次模型调用前冻结 Context Manifest/Snapshot，并计算不可变 Hash；
- 记录 `included/excluded`、Source Ref、Bound Version 和 Token 使用；
- 在组装前校验 Task、Run、Tool Result 和 Evidence Scope；
- Model Call、Checkpoint 和 Context Snapshot 通过稳定引用关联，支持审计和恢复。

以下旧边界不能直接成为 Pure Agent 方案：

- 固定 Task Role、Dependency Output 和 P0/P1 业务优先级会把旧 Task DAG 带回执行主链；
- `chars / 4` 只能作为粗略诊断，不能作为是否允许调用模型的最终 Token 判定；
- 固定 32K 上限不能代表不同 Provider/Model 的实际 Context Window、Tool Schema 和输出预留；
- 把大量任务路径、工具循环和事实提取规则拼进一个持续增长的 System Prompt，会同时造成维护困难、Token 浪费和隐藏 Workflow；
- 旧 Context Manifest 表和 Schema 继续服务历史 Run，不直接被新 Pure Agent 写入。

Pure Agent 可以复用“Manifest + Hash + Scope + Included/Excluded Receipt”的治理思想，但需要新的逻辑合同和独立本地开发数据域。

#### 7.19.2 Context Assembler 的最小职责

`ContextAssemblerPort` 是每次实际模型调用前的 Runtime 基础设施边界，负责：

1. 根据调用消费者、当前 Task State、Information Need、可见工具和授权 Scope 收集候选内容；
2. 校验 Source、版本、权限、有效性和内容 Hash；
3. 按 Context Profile 计算输入预算、输出预留和安全余量；
4. 去重、选择、结构化投影，并在达到条件时执行有界压缩；
5. 通过 Provider Adapter 的最终序列化和 Tokenizer/Estimator 做调用前复核；
6. 生成不可变 Context Snapshot 和 Included/Excluded/Compression Receipt。

它不负责：

- 识别用户意图或选择业务目标；
- 生成 Task Plan、决定下一项业务行动或固定 Tool 顺序；
- 授予权限、扩大 Scope 或修改 Tool 白名单；
- 判断 Evidence 是否真实、把摘要升级为事实或替代 Citation Guard；
- 通过删掉关键约束来强行让一次模型调用成功。

`consumer` 只表示当前实际调用需要哪种投影，例如 Main Agent、Intent Understanding 或 Planner；它是 Context Profile 选择条件，不是 Task 顶层状态，也不要求这些消费者按固定顺序出现。

#### 7.19.3 六个 Context Lane

首版按职责划分六个逻辑 Lane。Lane 决定选择和压缩保护等级，不要求最终 Prompt 使用固定自然语言章节：

| Lane | 典型内容 | 默认保护 |
|---|---|---|
| Policy & Protocol | System/Runtime Policy、Provider 协议、安全边界、输出合同 | 必须使用当前有效版本，不允许摘要 |
| Active Control | 当前用户消息、Task 目标/状态、有效 Slot/Pending、当前 Plan 投影、当前权限/Scope 安全投影 | 当前消息和控制不变量必须精确保留 |
| Tool Contract & Active Calls | 本轮 Visible Tool Schema、尚在 Function Calling 循环中的 Tool Call/Result Pair | Schema 和活动协议对必须完整、顺序合法 |
| Observation & Grounding | 当前信息需求直接相关的 Tool Observation、Grounding Record、Evidence Atom/Parent Context、限制 | 可选择相关项；用于事实和引用的 Atom 不可摘要 |
| Relevant Interaction | 当前对话中相关的原始消息、用户纠正、未解决问题、明确决定 | 近期和关键项保留原文，较旧部分可结构化压缩 |
| Historical Memory | Project/Assessment Memory、User Preference、旧对话摘要和旧结果引用 | 按 Scope/相关性读取，最先退出弹性预算 |

Lane 的保护等级不是事实权威等级。事实权威仍由 T06/T10 的 Source、Grounding 和 Memory 规则决定；最终 Provider Message Role 仍严格保持 System、User、Assistant、Tool 的协议层级。Evidence、Memory 和历史用户文本即使被选中，也只能作为不可信数据进入，不能被提升为 System 指令。

#### 7.19.4 不可无声压缩的内容

以下内容要么精确保留，要么整次 Context Assembly 明确失败/降级，不能截掉尾部后继续调用：

- 当前有效的 System/Runtime Policy、Safety 和输出合同；
- 当前用户消息；若单条消息本身超限，应指导用户上传资料、选择范围或拆分请求，而不是只保留开头/结尾；
- 当前 Task ID/State Version、目标、运行状态以及活动 Slot/Pending/Continuation 不变量的结构化投影；
- 本次调用真正需要的当前 Plan Version、Active/Unresolved Step 和 Next Decision；不需要回放旧 Plan Version；
- 本轮 Visible Tool 的 Function Name、Description 和 Input Schema；
- 尚未结束的 Provider Tool Call 与对应 Tool Message 协议对；
- 当前回答要引用或据以形成事实的 Evidence Atom 文本、Locator、Source Version、Hash 和 Grounding 状态；
- 用户对当前目标、事实或偏好的最新有效纠正；
- Provider 要求的输出 Token 预留和 Runtime Safety Margin。

“不可压缩”不等于每次都必须带入。例如与当前问题无关的 Evidence Atom 可以不选择；但一旦某个 Atom 被作为当前结论的证据，就不能用摘要文本替代原文，也不能截成改变语义的半段。

#### 7.19.5 Context Profile 与预算模型

首版采用“硬保护区 + 动态弹性区”，不在架构中冻结固定百分比：

```text
Provider/Model Context Capacity
    - Reserved Output Budget
    - Provider/Runtime Safety Margin
    = Effective Input Budget

Effective Input Budget
    = Mandatory Protected Content
    + Dynamically Allocated Relevant Content
```

约束如下：

- Provider/Model Profile 保存 Context Capacity、Tokenizer/Estimator、消息/Tool Schema 计数规则和最大输出能力；
- Context Profile 保存 Runtime 输入上限、输出预留、安全余量、Lane 保护策略、压缩触发条件和单项内容上限；
- 有效输入预算取 Provider 能力、Runtime 上限和当前输出预留共同约束后的较小值；不能因模型宣称支持超长窗口就默认把窗口用满；
- System Message、User/Assistant/Tool Message、Function Schema、结构包装和分隔符全部计入最终输入预算；
- 先容纳 Mandatory Protected Content，剩余预算才按当前 Information Need 动态分配给 Evidence、相关对话和 Memory；
- 不为每个 Lane 固定永久配额，避免简单问题被空保留区浪费、复杂证据问题又无法利用剩余窗口；可以在 Profile 中设置最小保护或单项上限；
- 最终调用前必须对 Provider 实际序列化结果计数。Tokenizer 可用时使用匹配实现；不可用时使用经校准的保守 Estimator 和 Safety Margin，字符估算不能作为最终权威；
- 具体数值、软阈值和模型差异留给授权评测，变更通过版本化 Context Profile 发布，不写死在 Agent Prompt 或业务路径中。

#### 7.19.6 最小 Context Request、Entry 与 Snapshot

首版先冻结逻辑字段，不提前冻结数据库模型：

```python
class ContextAssemblyRequest(BaseModel):
    consumer: ContextConsumer
    task_ref: str
    state_version: int
    current_message_ref: str | None
    information_need_refs: list[str]
    required_resource_refs: list[str]
    visible_tool_snapshot_ref: str | None
    model_profile_ref: str
    context_profile_ref: str
    checkpoint_snapshot_ref: str | None


class ContextEntry(BaseModel):
    entry_ref: str
    kind: ContextEntryKind
    source_ref: str
    representation: ContextRepresentation
    authority_label: str
    protection_class: ContextProtectionClass
    content_hash: str
    token_count: int
    required: bool
```

`representation` 至少区分 `exact`、`structured_projection`、`structured_summary` 和 `ref_only`。`ref_only` 只帮助模型知道某项资产存在，不能让模型据此声称看过其内容。

不可变 `ContextSnapshot` 至少记录：

- Context Snapshot ID/Sequence/Hash、Task/State Version 和 Consumer；
- Policy、Prompt Template、Registry/Visible Tools、Model、Provider、Tokenizer/Estimator 和 Context Profile Version/Hash；
- Conversation Head、Memory Version、Source/Parse/Index Head 和 Grounding 依赖；
- 按最终顺序排列的 Included Entry、Representation、Source/Content Hash 和 Token Count；
- Excluded Entry、排除原因、原保护级别和是否造成 Material Limitation；
- Compression Receipt、Summary Ref/Hash、原始 Source Range 和压缩前后 Token；
- Reserved Output、Safety Margin、最终序列化 Input Token 和 Snapshot/Request Hash；
- 组装时的 Auth/Scope Decision Ref，但不保存凭证、Token 或可重放的授权秘密。

具体内容优先以不可变 Source Ref + Hash 重建，Snapshot 不默认复制整份敏感 Prompt。若产品以后需要保存完整请求，必须另行定义加密、权限、保留和删除策略。

#### 7.19.7 首版组装算法

Context Assembly 采用确定性的治理顺序：

1. 解析当前 Auth、Task/State Version、Consumer、Model/Context Profile 和有效 Source Head；
2. 构建 Mandatory Protected Content，并在压缩前先判断其是否已经超过有效输入预算；
3. 根据当前 Information Need、Current Plan/Action 和 T10 Memory 读取规则收集候选 Entry；
4. 在任何相关性排序前完成 Permission/Scope/Version/Revocation 过滤；
5. 按最新有效版本、Stable Business Key、Source Ref 和 Content Hash 去重，移除已 Supersede 或重复表达的记录；
6. 按保护级别、当前必要性、Source 权威、Grounding 状态、相关性和时效选择弹性内容；单一相似度分数不能覆盖这些约束；
7. 超过软预算时按 7.19.8 的压缩阶梯处理；每次压缩都产生 Receipt，不允许循环压缩直到信息不可辨认；
8. 由 Provider Adapter 生成最终 Message/Tool Schema 序列，使用 Provider-aware Tokenizer/Estimator 再次计数；
9. 若仍超限，只能移除最低级的完整可选 Entry 或返回结构化限制/失败，不能截断 Mandatory Entry；
10. 冻结 Context Snapshot，再创建 Model Call；Model Call Ledger 必须引用该 Snapshot。

这是一条输入安全与预算校验管线，作用类似 Tool Gateway，不规定 Agent 应先规划、再检索、再回答，因此不是 Workflow。

#### 7.19.8 分层压缩阶梯

首版按损失从小到大采用以下阶梯，短 Context 不触发任何摘要：

**L0：无损清理和去重**

- 删除 UI 进度事件、重复警告、重试日志、Stack Trace、私有推理和不会回流模型的 Ledger 字段；
- Visible Tool Schema 每次只投影一次；旧 Registry Snapshot 不回放；
- 删除已 Supersede 的 Plan/Memory 版本和完全重复的 Source/Observation；
- 保留原始内容引用、Hash 和 Included/Excluded Receipt。

**L1：结构化投影**

- Plan 只投影当前版本、Active/Unresolved Step、依赖状态和已完成输出引用，不回放完整 Plan 事件史；
- 已完成的旧 Tool Call/Result 在离开活动 Function Calling 协议对后，替换为通过 Output Model 的有界 Observation Projection；原始结果仍在 Result Store；
- Grounding Record、Fact 和 Gate 使用类型化字段，不重复附带生成它们的全部过程日志；
- RAG Parent 只选择当前 Atom 所需的相邻上下文块，不把整篇文档作为保险内容塞入。

**L2：旧 Conversation 的结构化摘要**

- 当前用户消息、最新纠正、未解决 Slot/问题和近期必要原始 Turn 保持原文；
- 较旧消息压缩为带 Source Message Ref 的类型化 Conversation Summary；
- Summary 只保留目标/约束、用户决定与纠正、未决问题、已引用资源、带 Grounding Ref 的事实结论和明确限制；
- Assistant 历史回答不能因进入 Summary 就变成事实，Chain-of-Thought 永不进入 Summary。

**L3：索引 + 原始内容块回取**

- Summary/结构化索引只帮助定位旧 Message、Observation、Evidence 或项目事项；
- 根据当前 Information Need 回取少量原始块，并保留其 Source Ref、Role、时间、版本和 Hash；
- 首版使用数据库元数据、Stable Key、主题字段和确定性词法选择，不建设 T10 已否决的 Memory Vector Index；
- 若以后通过评测增加语义索引，必须先执行 Scope Filter，并与 Tombstone/删除保持一致。

**L4：显式限制或拆分**

- 低价值旧 Assistant 叙述、无关 User Preference、冗余 Parent Context 和可回取的旧摘要先退出 Context；
- 如果被排除内容可能实质影响答案，Assembler 返回 `material_context_omitted`，Main Agent 必须说明限制、缩小问题、继续获取相关内容或动态规划多个有界模型决策；
- 若 Mandatory Protected Content 本身超限，返回 `context_budget_exceeded`，不得调用模型；只有确实需要用户选择范围或提供输入时才创建 Slot，不能把内部预算问题一律伪装成 `pending`。

直接按 Token 从头部、中间或尾部截断，只允许用于已经在合同中明确标记为非语义 Preview 的字段；不得用于当前用户消息、JSON/Tool 协议对、Plan 控制字段、Evidence Atom 或有业务含义的 Message。

#### 7.19.9 Conversation Summary 的最小合同

Conversation Summary 属于 T10 的派生 Conversation Memory，必须版本化并保留原始消息。候选最小结构：

```python
class ConversationSummary(BaseModel):
    summary_id: str
    conversation_ref: str
    covered_message_refs: list[str]
    source_range_hash: str
    valid_through_message_ref: str
    goals_and_constraints: list[SummaryItem]
    user_decisions_and_corrections: list[SummaryItem]
    unresolved_items: list[SummaryItem]
    resource_refs: list[str]
    grounded_outcome_refs: list[str]
    limitations: list[str]
    summary_version: int
    supersedes_ref: str | None
    content_hash: str
```

约束如下：

- 每个 `SummaryItem` 必须引用一个或多个 Source Message，不能只保存无法追溯的自然语言结论；
- Summary 不保存用户/Agent 私有推理，不复制密码或无关敏感内容；
- 新消息、用户纠正或 Source 状态变化可以生成新版本并 Supersede/Invalidate 旧 Summary；
- 不允许长期只做“摘要的摘要”而丢失 Source 范围。新版本必须保留原始 Message Ref/Hash；需要时从原始范围重建；
- 关键决定、最新纠正和待解决问题在选入 Context 时优先回取原始 Message，而不是只使用 Summary 文本；
- 首版优先使用确定性结构化投影。只有旧自由文本在无损/结构化投影后仍无法满足预算时，才通过内部 `ContextCompressionPort` 条件式生成 Pydantic Summary；它不是模型可调用 Tool，也不是每轮固定模型调用；
- 模型式摘要输出仍是低于原始 Message 的派生信息，必须通过 Schema、Source Coverage、禁入项和 Scope Guard。压缩模型/Provider 以后通过评测选择。

#### 7.19.10 各类内容的选择规则

| 内容 | 首版进入 Context 的方式 |
|---|---|
| System/Runtime Policy | 当前版本精确投影；拆分为稳定核心规则和当前调用所需的窄协议，禁止堆积废弃规则 |
| 当前 User Message | 原文精确保留；超限时调用前失败并请求上传/缩小范围 |
| Task/Slot/Checkpoint | Pydantic 安全投影；只包含当前 State Version 和恢复所需引用，不复制完整 State JSON |
| Plan | 当前 Plan Version 的目标、Active/Unresolved Step、依赖和 Next Decision；旧版本与完成事件只留引用 |
| Tool Definitions | 只包含本轮冻结的 Visible Tool；由 Canonical Registry 生成 Function Schema |
| Tool Calls/Results | 活动 Function Pair 完整保留；已提交旧调用只使用 Canonical Observation Projection/Result Ref，不附日志 |
| Search Candidate | 只作为 non-citable 候选和回取线索；不能冒充最终 Evidence |
| Evidence Parent/Atom | Parent 提供必要邻近语境；用于事实/引用的 Atom 保持精确文本、Locator、Version、Hash 和 Grounding |
| Conversation | 当前相关原始 Turn + 较旧结构化 Summary + 按需原文回取；不固定“永远保留最后 N 轮” |
| Project/User Memory | 按 T10 的 Scope、有效性和相关性读取；User Preference 不挤占事实证据的必要预算 |
| 历史 Assistant Answer | 只证明 Agent 曾经这样回答，不证明内容为真；需要复用结论时回到 Grounding/Evidence |
| 上传文档/真实 PDF | 通过离线解析、RAG 和 Evidence Ref 进入，不把完整二进制或整份原文直接放入 Prompt |

#### 7.19.11 降级、澄清和失败边界

Context Assembly 的结果需要区分：

| 结果 | 行为 |
|---|---|
| `ready` | Mandatory 完整、预算内且无实质遗漏，可以调用模型 |
| `ready_with_limits` | 只排除可选内容，但存在可能影响完整性的 Material Limitation；模型收到安全限制说明，最终回答需要恰当披露 |
| `needs_narrowing` | 当前目标可通过缩小 Scope、分解信息需求或后续有界模型决策处理；返回给 Main Agent 在 `running` 内决定，不自动创建固定分片 Workflow |
| `blocked_on_user` | 只有必须由用户选择范围、解释歧义或改用上传资料时，才创建 Slot 并进入 `pending` |
| `failed` | Mandatory 内容、协议结构或 Provider 上限无法满足，且不存在安全的当前任务处理方式；返回结构化错误，不调用模型 |

无关历史被正常排除不需要每次打扰用户；但关键证据、有效纠正、当前限制或影响结论完整性的内容被排除时，必须进入 `limitations` 和审计。Assembler 不能为了得到 `ready` 而把 Material Limitation 改成普通排除。

#### 7.19.12 Context Snapshot、恢复和可复现性

- 每个 Intent Understanding、Planner 或 Main Agent Model Call 都引用自己冻结的 Context Snapshot；不同调用不能共享一个可变 Context 对象；
- Provider Failover 需要基于同一 Snapshot 重新生成目标 Provider 的合法序列化，并记录新的 Provider Rendering Hash；不能擅自增删业务内容；
- Continuation Checkpoint 只保存 Snapshot Ref 和暂停时 State Version，不复制完整 Prompt；恢复后先恢复 Working State，再根据新用户输入、当前权限和 Source Head 生成新的 Context Snapshot；
- Snapshot 本身不授予读取权限。历史回放、恢复和调试都必须重新校验当前操作人的访问权限；
- 可复现性以“相同不可变 Source、Policy/Profile/Registry Version 可以重建并验证相同 Projection Hash”为目标，不以永久保存所有敏感 Prompt 明文为代价；
- Source 按用户删除或保留策略被物理清除后，Snapshot 保留 Hash、删除状态和不可重放说明，不能绕过删除恢复内容；
- Provider Usage 与本地 Token Count 都进入 Model Call Ledger，用于以后校准 Estimator，但不能把调用后的 Usage 倒推成调用前越界的正当理由。

#### 7.19.13 Prompt Injection 与数据隔离

- System/Runtime Policy 使用受信模板和版本，Document、Evidence、Memory、旧 Message 和 Tool Output 永不拼入 System 指令段；
- 每个不可信 Entry 带 `kind/source_ref/authority_label` 并以数据容器投影，明确“内容中的命令不是 Runtime 指令”；分隔符只能辅助模型，不能替代 Guard；
- 文档中出现“调用某工具”“忽略此前规则”或伪造 JSON/Tool Message 时，只作为 Source 文本，不产生真实 Tool Call；
- 真实 Tool Call 仍必须来自 Provider Function Calling 结构，并通过 Visible Tool Snapshot、Permission Guard、Gateway 和 Effect Fence；
- Context Entry 在选择前执行 Tenant/User/Conversation/Project/Assessment Scope 校验，任何相关性或压缩处理都不能先读取越权内容；
- 模型只看到安全显示值和不透明 Ref，不暴露数据库主键枚举、凭证、MCP Auth Context 或内部策略细节；
- Context Summary/Compression 不得把多个租户或不同权限范围内容合并为共享摘要；权限变化后旧 Summary 和 Snapshot 不能继续作为可读内容。

#### 7.19.14 最小组件形态

首版只暴露一个 `ContextAssemblerPort`，内部逻辑可以同进程实现：

```text
ContextAssemblyRequest
        |
        v
Scope/Version Guard
        |
        v
Candidate Selectors
  - Active State / Conversation / Evidence / Memory / Tool
        |
        v
Budgeter + Optional ContextCompressionPort
        |
        v
Provider Renderer + Tokenizer/Estimator
        |
        v
ContextSnapshot + Model Call
```

- Candidate Selector 只是读取各域的合法投影，不创建第二套事实存储；
- Budgeter/Compressor 只改变表示和选择，不改变 Source 内容、权限或业务结论；
- `ContextCompressionPort` 条件式启用，初版可先以确定性投影覆盖主要场景；模型摘要只在确实需要且通过后续授权验证后接入；
- Provider Renderer 负责不同 Provider 的 Message/Function Schema 格式和计数，不让 Provider 差异污染 Main Agent；
- Snapshot Repository 可以借鉴旧 Context Manifest 的 Hash/Receipt，但使用新 Pure Agent 数据域，且在实现前另行确认 Schema/Migration。

#### 7.19.15 初版确认结论

1. Context Assembler 是每次实际模型调用前的确定性 Runtime 边界，不是业务 Workflow 节点，也不决定 Agent 下一步；
2. 采用六个逻辑 Lane 与“硬保护区 + 动态弹性区”，不冻结永久百分比；Provider/Context Profile 管理预算参数；
3. 当前 Policy、User Message、Task/Slot 控制不变量、Visible Tool Schema、活动 Tool Pair 和当前结论所需 Evidence Atom 不允许无声截断；
4. 最终 Token 判定基于 Provider 实际序列化和匹配 Tokenizer/保守 Estimator，字符除以四只可作诊断；
5. 压缩按 L0 无损去重、L1 结构化投影、L2 旧对话摘要、L3 索引 + 原文回取、L4 显式限制/拆分逐级触发；短 Context 不摘要；
6. Conversation Summary 是带 Message Ref/Hash、可版本化和失效的派生 Memory；关键决定/纠正优先回取原文，不允许只做递归摘要；
7. 活动 Tool Call/Result 保持 Provider 协议完整；旧结果使用有界 Observation；Evidence Atom 原文、Locator 和 Grounding 不被摘要替代；
8. `ready/ready_with_limits/needs_narrowing/blocked_on_user/failed` 区分内部限制和真正需要用户输入，避免把预算问题一律变成 `pending`；
9. 每个模型调用冻结独立 Context Snapshot，记录 Included/Excluded/Compression/Token/Version/Hash，并由 Model Call 和 Checkpoint 引用；
10. 数据内容始终按不可信 Entry 投影，不能成为 System 指令、扩张 Tool 权限或跨越 Scope；
11. 首版先实现确定性选择和结构化投影，条件式模型摘要、数值预算和语义历史索引通过后续授权评测再启用或调整。

### 7.20 回答、引用和认知状态表达边界

> 状态：初版已确认。本节定义用户自由回答与内部结构化 Grounding/Citation 校验如何并存；不把完整研判、简单问答、澄清或追问固定成统一报告模板。

用户需要自然、直接、随问题变化的回答；Runtime 又必须知道哪些句子是事实、推断、建议、未知或冲突，并保证引用没有越权、过期或伪造。首版推荐“双层输出”：

```text
Main Agent
    -> structured AnswerDraft
        -> Pydantic / Grounding Binding / Citation Integrity / Scope Guard
            -> Runtime Response Renderer
                -> free-form user-visible answer
```

内部结构化合同只约束证据责任和安全，不规定回答必须包含“项目概况、七项硬门、风险清单、最终结论”等固定业务章节。

#### 7.20.1 现有资产的复用与隔离

可以复用的治理思想：

- `BidResolvedFact`、Hard Gate、Grounding Record 和 Evidence Atom 提供事实、状态和来源；
- Claim Candidate、Claim Citation 和 Report Validation 已证明“先形成候选、再校验、最后发布”是可审计边界；
- Citation 中保留 Document Version、Locator、Excerpt/Quote Hash 和不可变 Citation Hash；
- Report/Model Call/Context Snapshot 的 Version/Hash 可以关联回答产生时使用的事实快照；
- 已认证、可恢复的 SSE/Event 基础设施可以承载安全回答事件。

不能直接复用的旧行为：

- Preliminary Report 的固定 `decision/hard_gates/facts/claims/limitations` 结构不能成为所有问答输出；
- 旧 `ClaimCandidate` 只有 `fact/calculation/inference/recommendation + support_ids`，不能完整表达 partial、conflicted、unknown 和 User Assertion；
- 不能因为 Claim Type 是 inference/recommendation 且存在任意 Support ID 就自动判定有效；推断必须有可追溯前提，建议必须有触发条件或明确说明属于通用建议；
- Citation 不能用 Evidence 文本固定截取前 900 字代替当前陈述的精确支持范围；直接引语必须绑定确切 Span/Quote Hash；
- 旧 Citation 只覆盖文档 Evidence Fragment；Pure Agent 还需要安全表示企业知识、业务记录、公式/计算输入和用户明确陈述；
- 旧报告和验证表继续作为冻结历史 Run 的事实，不由新对话回答链写入或改写。

#### 7.20.2 用户自由输出与内部 AnswerDraft 分离

用户输出允许是：

- 一句话直接回答；
- 多段解释、列表、对比或表格；
- 完整投标机会研判；
- 风险项说明、缺失资料清单或下一步建议；
- 澄清问题、知识问答或对上一回答的修正。

内部 `AnswerDraft` 使用通用 Content Block，而不是固定业务字段。候选最小合同：

```python
class AnswerDraft(BaseModel):
    schema: Literal["bid.answer.draft.v1"]
    response_language: str
    blocks: list[AnswerBlock]
    context_snapshot_ref: str
    state_version: int


class NarrativeBlock(BaseModel):
    block_type: Literal["narrative"]
    block_id: str
    text: str
    presentation_hint: PresentationHint


class StatementBlock(BaseModel):
    block_type: Literal["statement"]
    block_id: str
    text: str
    presentation_hint: PresentationHint
    claim_type: Literal["fact", "calculation", "inference", "recommendation"]
    epistemic_status: Literal["supported", "partial", "conflicted", "unknown"]
    grounding_refs: list[str]
    premise_or_trigger: str | None
    quote_refs: list[str]


class LimitationBlock(BaseModel):
    block_type: Literal["limitation"]
    block_id: str
    code: AnswerLimitationCode
    text: str
    source_refs: list[str]


class InteractionBlock(BaseModel):
    block_type: Literal["interaction"]
    block_id: str
    text: str
    slot_ref: str | None
```

`AnswerBlock` 是上述类型的 Discriminated Union，所有 Model Output 使用 `extra="forbid"` 并通过 Pydantic/JSON Schema 校验。

边界如下：

- `NarrativeBlock` 只用于标题、过渡、结构说明或不含项目事实的交互文本；所有会影响用户判断的事实、计算、推断和建议都必须进入 `StatementBlock`；
- `presentation_hint` 只帮助 Renderer 选择段落、标题、列表项、表格单元或提示框，不强制业务章节顺序；
- `grounding_refs` 只能逐字引用当前 Context Snapshot 中对模型可见的 Grounding Ref，模型不能自造 Evidence ID、数据库 ID 或 Citation URL；
- `quote_refs` 只在使用直接引语时出现，并指向已经通过 Atom Read 的精确 Quote/Span Binding；
- `InteractionBlock.slot_ref` 只有当前 Task 已创建合法 Slot 时才允许存在；普通可选追问可以不绑定 Slot；
- 简单寒暄或纯交互回答可以只有 Narrative/Interaction；一旦回答项目事实或风险，就必须产生 Statement/Grounding Binding。

该 Schema 固定的是机器通信与证据责任，不固定用户输入和输出内容，因此不会把 Pure Agent 变成报告 Workflow。

#### 7.20.3 Claim Type、Epistemic Status 与 Source Basis 分离

三个维度不能混在一个枚举中：

| 维度 | 作用 | 候选值/来源 |
|---|---|---|
| Claim Type | 这段陈述是什么认知活动 | `fact/calculation/inference/recommendation` |
| Epistemic Status | 当前支持程度 | `supported/partial/conflicted/unknown`；来自 Grounding Record，不由文风决定 |
| Source Basis | 支持来自哪里 | Document/Enterprise/Business Record/System Rule/User Assertion/Formula；由 Grounding Ref 的 Source 决定，不让模型自由填写 |

这样可以表达：

- “根据招标文件，截止时间为……”：Fact + Supported + Document；
- “根据您刚才提供的信息，团队人数为 8 人，但尚未由企业资料核实”：Fact + Partial/Unknown + User Assertion；
- “由上述工期和人员缺口判断，履约风险较高”：Inference + Supported/Partial + Premise Grounding；
- “建议先向业主澄清验收口径”：Recommendation + Supported/Partial + Trigger；
- “当前可访问资料无法确认保证金金额”：Fact 的 Unknown 表达 + Retrieval/Scope Limitation；
- “两个有效版本对付款周期描述不一致”：Fact + Conflicted + 双方 Source Group。

`unsupported`、`stale` 和 `revoked` 可以存在于内部 Grounding Record，但不能作为肯定式用户 Claim 发布。Main Agent 必须删除该陈述、重新取证，或改写为 `unknown/limitation`。

#### 7.20.4 用户可见表达规则

Renderer 和 Prompt 共同遵守以下最低语义：

| 状态/类型 | 用户可见表达 |
|---|---|
| Supported Fact | 可以直接陈述，并在陈述后附当前有效 Citation |
| Partial | 明确限定已经确认的部分、尚缺部分和可能影响，不写成完整确定事实 |
| Conflicted | 同时展示冲突各方、Version/时间/范围和当前无法确定项；没有 Authority Rule 时不替用户静默选边 |
| Unknown | 使用“当前资料/当前可访问范围内无法确认”，说明缺少什么；不把 No Result 写成“不存在”或“没有要求” |
| Calculation | 展示结果时绑定输入事实和 Formula/Rule Version；无法验证输入时结果保持 Unknown/Conditional |
| Inference | 明确使用“判断、推测、可能、基于……推断”等表述，并展示关键前提；不得伪装为原文事实 |
| Recommendation | 明确使用“建议”，说明触发条件、预期作用和关键限制；项目级建议必须引用前提 |
| User Assertion | 使用“根据您提供的信息”等来源限定；除非另有 Grounding，不写成已由资料验证 |

对高影响风险、硬门、资格、金额、日期、范围、承诺和 Go/No-Go 建议，Renderer 可以使用稳定但不生硬的标签，例如“已确认”“部分确认”“存在冲突”“暂无法确认”“判断”“建议”。简单低风险问答不要求每句话都重复标签。

#### 7.20.5 Citation 由 Runtime 投影而不是模型手写

模型只选择合法 `grounding_refs/quote_refs`。`CitationProjector` 根据当前授权与 Source 类型生成用户可见 Citation：

```text
StatementBlock.grounding_refs
    -> Grounding Record / Evidence Link / Business Record Link
        -> Citation Integrity Guard
            -> Safe Citation Projection
                -> Renderer 插入陈述之后
```

用户可见 Citation 至少包含适用的：

- 安全 Source Title/Type；
- 页码、章节、表格、Sheet/Cell、条款号或其他 Locator；
- Source Version/生效时间或企业记录快照时间；
- 受控预览或受控文件查看链接；
- 多 Source Conflict Group；
- 直接引语对应的精确 Quote Span。

不得暴露：

- MinIO Object Key、内部文件路径、MCP URL/Auth、数据库枚举 ID；
- 用户无权访问的 Source 名称或资源是否存在；
- Search Candidate、RAG Parent、Conversation Summary 或 Memory Summary 作为原文 Citation；
- 模型自己生成的伪页码、伪链接或自由文本 Source Label。

同一陈述的重复 Citation 可以按稳定 Source/Locator 去重；多个证据共同支持或相互冲突时保留必要分组。引用格式可以是行内脚注、可点击标签或侧栏卡片，由前端 Renderer 决定，不写死进模型正文。

#### 7.20.6 Citation/Grounding Guard 的权威范围

首版确定性 Guard 至少检查：

1. AnswerDraft/Block 的 Pydantic Schema、Block ID 唯一性和跨字段规则；
2. `context_snapshot_ref/state_version` 与当前待提交回答完全一致；
3. 每个 Grounding/Quote Ref 确实在当前 Snapshot、Task、Conversation 和授权 Scope 内；
4. Grounding Record 为当前版本，未 Stale/Revoked，Source Head、Content/Locator/Quote Hash 一致；
5. Statement 的 Claim Type、Epistemic Status 和 Grounding Record 状态兼容；
6. Fact/Calculation/Inference/Recommendation 满足各自支持矩阵，Inference 有前提，Recommendation 有触发条件或被明确标记为通用建议；
7. Partial/Conflicted/Unknown 没有被 Renderer 当作确定事实；冲突陈述包含所需 Source Group；
8. 直接引语逐字匹配 Quote Span/Hash；普通转述不能冒充直接引语；
9. 所有需要 Citation 的 Material Statement 都能生成至少一个安全 Citation Projection；
10. Limitation Code 与 Retrieval、Context、Permission、Source 或 Tool Receipt 一致，不能用假限制掩盖模型错误。

确定性 Guard 可以验证引用完整性、状态绑定、Scope 和 Hash，但不能仅靠字符串规则完全证明自由文本对 Evidence 的语义蕴含。首版依赖 T06 已形成的 Proposition/Grounding Record，并要求 Fact/Calculation 尽量引用规范化 Value/Proposition；Inference/Recommendation 显式绑定前提。只有后续评测证明语义错配仍是主要风险时，才增加独立 Answer Verifier/Evaluator 模型，不能宣称 Pydantic 已经解决语义真实性。

#### 7.20.7 最小支持矩阵

| Statement | 最小支持 | Citation 行为 |
|---|---|---|
| Document/Enterprise Fact | 当前 Supported/Partial Grounding + 可引用 Atom/企业记录 Link | Material Fact 必须生成安全行内 Citation |
| Business/System Fact | 当前权威业务记录或版本化 System Rule | 生成记录/规则 Citation；不伪造文档页码 |
| User Assertion | 当前 Conversation Message Ref；如声称已核实，还需其他 Grounding | 默认显示“用户提供”；必要时可引用会话来源但不暴露内部 ID |
| Calculation | 已验证输入 Grounding + Formula/Rule Version + Calculation Result Hash | 引用输入和公式说明；输入 Unknown 时不能输出确定结果 |
| Inference | 一个或多个有效前提 Grounding + `premise_or_trigger` | 引用关键前提，并以推断语言呈现 |
| Recommendation | 有效触发条件/前提，或明确属于不依赖项目事实的通用建议 | 项目级建议引用前提；通用建议不得伪装成资料要求 |
| Conflicted | 至少两个仍有效且不能由 Authority Rule 消解的 Source Group | 同时展示各组 Citation，不按 Retrieval Score 选边 |
| Unknown | Unknown Grounding 或 No Result/Coverage/Source Availability Receipt | 不生成支持该事实的假 Citation；可展示检索范围和缺失来源 |

#### 7.20.8 No Result、Unknown、Conflict 和权限限制必须区分

首版 `AnswerLimitationCode` 至少区分以下语义，具体错误码名称可以在实现时收敛：

| 情况 | 用户表达 |
|---|---|
| Retrieval No Result | “本次在已检索范围内未找到相关片段”，不等于资料不存在 |
| Source Not Provided | “当前资料包未包含可确认该项的来源” |
| Evidence Insufficient | 找到相关内容但不足以支持明确结论，说明还缺什么 |
| Evidence Conflicted | 有效来源互相冲突，展示双方和待澄清点 |
| Source Stale/Unavailable | 说明当前版本或服务不可用导致无法确认，不使用旧内容冒充当前事实 |
| Permission Limited | “在当前可访问范围内无法核实”；不得泄露未授权资源是否存在或名称 |
| Tool/Index Degraded | 说明当前只完成了哪些通道、结果可能遗漏什么 |
| Context Limited | 说明本回答基于选定范围，未覆盖的 Material Context 是什么 |

这些限制可以成为回答的一部分，但不能用技术异常、Stack Trace、内部 Guard 规则或 Provider 名称直接面向用户。

#### 7.20.9 Draft 校验、修复和安全回退

推荐提交路径：

1. Main Agent 基于冻结 Context Snapshot 产生完整结构化 AnswerDraft；
2. Runtime 执行 Pydantic、Grounding Binding、Scope、Citation Integrity 和 Rendering Guard；
3. 通过后生成不可变 Rendered Response、Citation Projection 和 Hash；
4. 未通过时，不向用户发送失败 Draft；Runtime 可以把安全、结构化的 Validation Error 返回同一 Main Agent 做有界修复；
5. 修复不得默认重跑已经成功的 Tool，也不能改用 Snapshot 外的 Evidence Ref；确需新证据时回到 `running` 由 Main Agent 决定下一 Action；
6. 达到有界修复上限仍失败时，Runtime 从已经验证的 Grounding/Limitations 构造最小安全回退，或返回用户可理解的生成失败；不能发送“部分可能正确”的原 Draft。

修复次数、Timeout 和重复错误检测进入 T13 Runtime Governance。`draft -> validated -> committed/rejected/stale` 是 Response Artifact 的发布状态，不是 Agent Task 顶层状态，不会要求每个业务问题经过固定报告步骤。

#### 7.20.10 流式回答的首版边界

未经完整校验的模型 Token、Claim 或 Citation 不直接流给用户。首版采用“生成可流式、发布需提交”的模式：

- 模型到 Runtime 可以使用 Provider Streaming，但片段只进入受控缓冲区；
- UI 可以实时看到 `answer_generating`、Tool/检索进度等安全状态事件；
- 完整 AnswerDraft 解析并通过 Guard 后，Runtime 发送 `answer_committed`，随后可以把已经验证的 Rendered Response 分块展示；
- Citation 标记由 Renderer 在提交前生成，避免正文已展示后页码/引用才被判无效；
- 用户取消时，未提交 Draft 丢弃或只留受限审计 Hash，不产生半个权威回答；
- 首版不做 Claim-by-Claim 的抢跑式发布。未来只有每个 Block 能独立验证、跨 Block 结论不会被后文推翻且评测证明体验收益明显时才启用。

这会比原始 Token 直出稍晚，但用户看到的最终内容不会经历“先显示错误事实，再撤回引用”的抖动。

#### 7.20.11 回答修正、版本和历史状态

- 已发送 Message/Response 不原地静默修改；用户纠正或 Source 更新后产生新 Response，并通过 `supersedes_response_ref` 或 Conversation 关系说明替代；
- 用户纠正只先形成 User Assertion/Memory Version，涉及外部事实时仍需重新 Grounding；
- Source Head、权限、Grounding 或 Citation 撤销后，历史 Response 保持“当时曾发送”的记录，但其 Response Artifact 可以标记 `stale/revoked_support`；
- 后续 Context 默认不把 Stale 历史回答作为当前事实，必要时提示“依据已更新，以下为当前结论”；
- 用户打开历史 Citation 时重新授权；无权或 Source 已删除时返回受控不可用状态，不借旧 Response 绕过权限；
- 对已发布回答的主动更正通知、订阅和批量失效策略留到 T13/T14，不在首版问答合同中提前建设复杂通知系统。

#### 7.20.12 回答风格和 User Memory

- Main Agent 根据当前问题决定简短、详细、对比、列表或研判形式；不按固定 Intent Label 选择报告模板；
- 用户明确语言、长度、专业程度和格式偏好可以从 User Memory 读取，但不能让偏好覆盖 Grounding、Citation、未知和安全披露；
- 没有偏好时使用与用户语言一致、先回答核心问题、再给依据/限制/下一步的自然结构；这只是默认表达原则，不是固定字段；
- Citation 的显示可以简洁折叠，但 Material Fact 的 Citation Binding 仍然存在；
- 不展示 Chain-of-Thought、内部 Plan 原文、Query Expansion、Guard 决策树或未经安全投影的 Tool Arguments。

#### 7.20.13 最小组件形态

```text
Main Agent + Context Snapshot
        |
        v
AnswerDraft (Pydantic Discriminated Union)
        |
        v
Answer Contract / Grounding Binding Guard
        |
        v
Citation Integrity Guard + CitationProjector
        |
        v
Response Renderer
        |
        v
Committed Response + Safe SSE Projection
```

- 首版由 Main Agent 完成 AnswerDraft，不增加独立 Answer Writer Agent；
- Grounding/Citation Guard 是确定性 Runtime 组件，不是模型 Tool；
- CitationProjector 只从已验证 Source Link 生成安全显示信息，不让模型写 URL/页码；
- Renderer 只负责格式、标签、Citation 插入和安全展示，不自行创造业务结论；
- Answer Verifier Model、Claim-level Streaming 和复杂主动更正只有评测触发后再增加；
- 组件可以同进程实现，不要求拆分服务或新增固定队列。

#### 7.20.14 初版确认结论

1. 用户回答保持自由自然语言，内部使用通用 Content Block `AnswerDraft` Pydantic 合同；结构化的是证据责任，不是固定报告章节；
2. Narrative/Statement/Limitation/Interaction 分离，所有 Material Fact、Calculation、Inference 和 Recommendation 必须进入带 Grounding Ref 的 Statement；
3. Claim Type、Epistemic Status 和 Source Basis 分离，支持 Document/Enterprise/Business/User/Formula 等来源；
4. Supported、Partial、Conflicted、Unknown 具有不同的用户表达；Unsupported/Stale/Revoked 不得发布为肯定事实；
5. 模型只选择 Grounding/Quote Ref，用户可见 Citation 由 Runtime CitationProjector 生成；Search Candidate、Summary 和模型自写页码不能成为 Citation；
6. Material Fact 需要当前有效 Citation；Inference/Recommendation 绑定前提/触发条件；Conflict 同时展示双方；Unknown 不生成假 Citation；
7. 确定性 Guard 校验 Schema、Scope、Version、Hash、状态、支持矩阵和 Quote Span，但不夸大为完整语义蕴含证明；首版不增加独立 Answer Verifier 模型；
8. Retrieval No Result、Source Missing、Evidence Insufficient、Conflict、Stale、Permission、Degraded 和 Context Limited 使用不同限制语义；
9. Draft 未通过时先有界修复，再使用已验证 Grounding 构造安全回退；无效 Draft 不发送给用户；
10. 首版缓冲完整 Draft，校验提交后再向用户展示已验证内容和 Citation；不流式直出未经验证的模型 Token；
11. 已发送回答不可变；纠正和 Source 变化产生新版本/替代关系，旧回答可以标记 Stale 但不静默改写；
12. Main Agent、确定性 Guard、CitationProjector 和 Renderer 同进程即可，回答组件不形成第二个 Agent 或固定 Workflow。

### 7.21 Runtime Governance 的预算、循环、超时、取消和恢复边界

> 状态：最小护栏已确认；本节其余实现细节保留为历史讨论并降级为开发 Backlog，不进入 Architecture Baseline v0.1。具体次数、秒数、Token、费用、并发量和退避参数由后续获得授权的评测与运营数据校准。

Runtime Governance 的目标不是替 Main Agent 决定业务路径，而是保证自主循环始终满足：有界、可取消、可恢复、可审计、不会重复产生副作用，也不会因异常重试或上下文变化无限消耗资源。

#### 7.21.1 现有资产的复用与隔离

可以复用的治理思想：

- Task/Attempt Lease、Heartbeat、Fencing Token、Row/State Version 和 Compare-and-Swap；
- 不可变 Checkpoint、Context Snapshot、Effect Fence、Idempotency Key 和 Source/Result Hash；
- Model Call、Tool Invocation/Dispatch、Attempt、Result、Token 和 Cost Ledger；
- `reserved/actual` 资源记账、Deadline、Retry Count、Provider Receipt 和 `uncertain` 结果；
- 取消请求、迟到结果阻断、Checkpoint 恢复、Outbox/Audit/Event 和脱敏 Runtime Trace；
- Local Read-only、External Async、`safe_idempotent/reconcile_required/no_replay` 等执行差异。

不能直接复用的旧边界：

- 旧 Run/Task 的 `planning/queued/waiting_operation/validating/...` 状态链和固定 49 Task DAG 不进入 Pure Agent 顶层状态；
- `LOW/STANDARD/HIGH + max_iterations` 可以作为历史参数参考，但不能继续绑定固定 Task Type/Category 或预设业务动作数量；
- 首批本地只读 Tool 和普通 Model Call 不应为了“耐久”一律经过 Celery Durable Dispatch；
- `autoretry_for=(Exception,)` 式统一重试不能用于 Model/Tool Effect，重试必须由错误类型、Replay Policy、剩余预算和 Deadline 共同决定；
- 旧 Run Retry 会重新建立固定 Task Attempt；Pure Agent 的五个终态仍不可普通恢复，非终态 Crash Recovery 与用户发起新 Task 必须区分；
- 旧 Runtime 表和历史 Trace 保持冻结兼容，新 Pure Agent 不写入旧 `bid_intake_*` 或固定 Workflow 数据域。

#### 7.21.2 Conversation、Turn、Task、Action 和 Artifact 的关系

首版采用以下逻辑关系：

```text
Conversation
  ├─ ordered User/Assistant Messages
  └─ Agent Turn
       └─ links one Agent Task
            ├─ zero or more Model Calls
            ├─ zero or more Tool Calls
            ├─ zero or more Plan Versions
            ├─ Action/Event Ledger
            ├─ Checkpoint / Context Snapshot
            └─ zero or one committed final Response Artifact
```

定义：

- Conversation 是有序消息和会话 Scope；
- Agent Turn 是一个被接受的用户输入及其对应交互结果。Slot 回答也是新 Turn，但可以恢复同一个 `pending` Task；
- Agent Task 是围绕一个当前目标的耐久执行单元，可跨多个 Turn 等待 Slot；
- Action 是 `running` 内一次已经接受的动态行动，例如 Main Agent Model Call、Planner Call、Tool Call、Plan Revision、Answer Repair 或 Response Commit；
- Event 是不可变运行事实；Artifact 是 Plan、Observation、Grounding、AnswerDraft、Response 等版本化产物；
- 用户在已完成回答后继续提问时创建新 Agent Task，并通过 T10/T11 引用允许复用的历史信息；终态 Task 不重新进入 `running`。

Turn、Action 和 Artifact 的内部生命周期不是 Task 顶层状态。Task 继续只使用 `running/pending/completed/failed/cancelled`。

#### 7.21.3 动态 Agent Control Loop

Pure Agent 的运行形态是开放 Action 循环：

```text
while Task == running:
    接收当前 State/Context/Observation
    Main Agent 自主提出下一 Action 或 AnswerDraft
    Runtime 校验权限、预算、状态、重复和副作用边界
    执行或拒绝该 Action
    接受新 Observation/Event/Artifact
    由 Main Agent 决定继续、规划、澄清或回答
```

约束如下：

- Runtime 不要求依次经历 Intent → Plan → Search → Read → Rerank → Answer；简单问题可以直接回答，复杂任务可以动态规划和多轮取证；
- 每个 Action 必须绑定 Task ID、State Version、Turn/Action Sequence、Context/Registry Snapshot 和 Effect/Idempotency Key；
- Runtime 只判断 Action 是否可执行和可提交，不替模型选择招标调查内容；
- Action 成功只产生 Observation/Artifact，不自动决定下一业务步骤；
- 每个被接受或拒绝的 Action 都产生安全 Ledger Event；不保存 Chain-of-Thought；
- Task 处于 `running` 时可以有一个受控 In-flight Action；等待正常 Tool/Model/异步操作不进入 `pending`。

#### 7.21.4 Runtime Profile 与预算维度

Runtime Profile 是版本化配置，不属于模型输入参数，也不能由 Planner/Tool Arguments 提高。候选维度：

```python
class RuntimeProfile(BaseModel):
    profile_ref: str
    max_active_duration: Duration
    max_model_calls: int
    max_tool_calls: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_cost_microunits: int
    max_replans: int
    max_answer_repairs: int
    max_no_progress_actions: int
    max_retry_attempts: int
    max_parallel_read_calls: int
    model_timeout: Duration
    tool_timeout: Duration
```

这些字段是逻辑最小集合，精确字段和是否按 Turn/Task 双层拆分在实现时收敛。治理原则：

- Tenant/Product Policy 给出不可突破的上限；Runtime Profile 可以更严格，不能更宽；
- Task 使用一个冻结 Profile Snapshot，运行中若用户明确扩展 Scope/预算，应产生受审计的新 Profile Binding 或新 Task，不能静默增额；
- Direct → Planned 升级保留已经消费的全部资源，不重新获得一份预算；
- Model Call、Answer Repair、Planner Call 都计入 Model/Token/Cost；Tool 的传输重试虽不增加逻辑 Tool Call 数，也消耗 Attempt、时间和费用预算；
- `pending` 等待用户时暂停 Active Execution Duration，但受独立的 Pending 保留/过期策略约束；具体 Pending TTL 仍与 T15 交互策略共同实现；
- 预算是安全上限，不是 Agent 必须用完的配额。完成条件满足后应立即回答/结束。

#### 7.21.5 原子预留、实际结算和预算耗尽

每个有成本或副作用的 Action 在执行前进行原子 Budget Reservation：

```text
available = limit - spent - reserved
request <= available
    -> reserve + create accepted Action/Effect Fence
    -> execute
    -> settle actual + release unused reservation
```

要求：

- Reservation、Accepted Action、Effect Fence 和 State Version 使用同一事务或等价原子边界；
- 并发执行不能各自看到同一份可用余额并造成超卖；
- Actual Usage 来自 Provider/Tool Receipt；缺少可信 Usage 时按保守 Reservation 结算并记录 `usage_unverified`；
- Timeout、失败和取消仍记录已发生的 Token、费用和墙钟时间；失败不能退回已经实际消费的成本；
- 重试和 Failover 在每次 Attempt 前重新检查剩余预算与 Deadline；
- Budget Ledger 记录 Limit、Reserved、Spent、Released、Action Ref、Profile Version 和 Hash，不把费用明细塞进模型 Context。

预算耗尽不是统一的 `failed`：

- 已有足够 Grounding 时，Main Agent 可以提交带限制的回答并 `completed`；
- 证据不足时可以回答 Unknown/Limitations 后 `completed`；
- 只有无法产生任何安全交互结果且不具备可恢复路径时才 `failed`；
- 预算本身不构成用户 Slot。只有产品允许用户明确授权扩额且确实需要其决定时，才可以请求用户选择；首版不假设存在付费扩额交互。

#### 7.21.6 Progress Fingerprint 与循环检测

Runtime 不读取 Chain-of-Thought，而通过 Action/Observation 的结构化 Fingerprint 判断是否有进展：

**Action Fingerprint 候选组成**

- Action Type、Tool Name/Planner/Answer Repair 类型；
- 规范化 Arguments/Information Need Hash；
- 当前 State/Plan Version、Context/Visible Tool Snapshot Hash；
- Source Scope/Snapshot Hash 和预期输出类型。

**Progress Signal**

- 新的有效 User Message/Slot Result；
- 新的 Tool Observation/Model Artifact Content Hash；
- 新的 Grounding/Evidence/Source Version；
- Plan 发生实质子目标/依赖变化，而不只是改写措辞；
- Validation Error 集合减少或 AnswerDraft 产生有效的新 Grounding Binding；
- 权限、Source Availability 或目标发生实质变化。

至少检测：

- 相同 Tool + 相同 Arguments + 相同 Scope/Snapshot 重复调用；
- Search Query 只做同义改写但 Candidate/Observation Hash 不变化；
- A → B → A 的 Action 周期且没有新 Observation；
- Replan 产生相同 Plan Semantic Hash；
- Answer Repair 产生相同 Draft Hash 或相同 Validation Error Set；
- Provider Failover 后重复执行已经持久化的 Tool Effect；
- 连续 Action 没有新增 Governed Information。

触发后的最小策略：

1. Gateway 先以 Idempotency/Effect Fence 阻止重复 Effect；
2. Runtime 给 Main Agent 一次结构化 `no_progress` Observation，包含安全 Reason Code 和剩余预算，不泄露内部检测细节；
3. Main Agent 必须选择实质不同的 Action、提交 Unknown/Limitations、请求真正阻塞的 Slot 或安全结束；
4. 继续命中 Profile 阈值时停止新增 Model/Tool Action，优先构造安全回答；无法回答时 `failed`；
5. Loop Fingerprint、窗口和处置写入 Ledger，具体阈值由 Runtime Profile/评测确定。

#### 7.21.7 Deadline、Timeout、错误分类和 Retry

Deadline 分层：

```text
Task Active Deadline
  └─ Turn/Action Deadline
       └─ Model/Tool Attempt Timeout
```

子 Deadline 永远不能超过父级剩余时间；Backoff 也必须放入剩余时间。错误至少分为：

| 类别 | 默认处理 |
|---|---|
| Invalid Input / Contract | 不重试；修正参数、重新生成 Action 或友好提示 |
| Permission / Safety | 不重试同一 Action；不能靠 Failover 绕过 |
| Budget Exhausted | 不重试；按已有 Grounding 回答或失败 |
| Stale State / Fence Lost | 当前执行者停止；重新加载最新 State，由新 Lease 决定是否恢复 |
| Cancelled | 不重试；迟到结果全部拒绝提交 |
| Rate Limited / Transient Unavailable | 仅在 Replay Safe、预算和 Deadline 允许时退避重试或 Failover |
| Timeout Before Send | 通常可按 Replay Policy 重试 |
| Timeout/Disconnect After Send | 标记 `uncertain`；只读/幂等调用可按策略重试，外部写操作先 Reconcile |
| Provider/Tool Contract Violation | 当前结果拒绝；同一版本通常不重试，可按批准的兼容 Route Failover |
| Internal Runtime Error | 记录安全错误；只在确认未产生 Effect 或 Effect Fence 可复用时恢复 |

Retry Policy 同时检查：Error Class、Tool/Model Replay Policy、Attempt Budget、Task Budget、Circuit State、Deadline 和 Cancellation Fence。使用有上限的指数退避与 Jitter；不能用通用队列的“遇到任何 Exception 自动重试”代替语义判断。

#### 7.21.8 直接执行与 Durable Async 的触发边界

首版不把所有 Action 强制入队：

| 执行形态 | 适用条件 |
|---|---|
| Direct Bounded Async | 普通 Model Call；首批四个本地/受控 MCP 只读 Tool；预计在交互 Deadline 内完成；结果可由同一 Request/Runtime 接受并持久化 |
| Durable Async Operation | 预计超过交互 Deadline、需要 Worker 隔离/Heartbeat、远端先 ACK 后回执、长时资源处理、必须跨进程/重启恢复，或未来副作用操作需要 Reconcile |

共同要求：

- 两种形态都先写 Accepted Action、Budget Reservation、Idempotency/Effect Fence 和 Call Ledger；
- Direct 不等于无审计，Durable 不等于固定 Workflow；
- Durable Operation 等待期间 Task 顶层保持 `running`，通过 `in_flight_action_ref` 和 Checkpoint 表达；`pending` 仍只等待用户输入；
- Operation 完成事件通过 State Version/Fence 接受后产生 Observation，并唤醒 Main Agent；
- 只因“以后可能扩容”不能把一个短时本地读取提前拆成队列；真正触发 Durable 的指标由 Runtime Profile/Binding 声明和实践数据决定。

#### 7.21.9 Cancellation 和迟到结果

用户取消、系统 Deadline、策略撤销或上游 Goal 被替代时：

1. 使用 Expected State Version 原子把 Task 转为 `cancelled`，递增 State Version，并写 Cancellation Fence/Event；
2. 立即停止接受新的 Model/Tool/Answer Commit，不增加 `cancelling` 顶层状态；
3. 对 In-flight Model/Tool/Async Operation 发送 Best-effort Cancel，并释放未使用 Reservation；
4. 任何迟到结果必须再次检查 Task Status、State Version、Action Fence 和 Cancellation Epoch，不符合即写 `late_result_rejected`，不得进入 Context/Memory/Response；
5. 已实际发生的费用和不可撤回外部副作用保留 Ledger；未来写 Tool 若结果不确定，进入 Reconcile/人工处置，不伪装为已取消即未发生；
6. Response Commit 与 Cancel 通过同一 State Version/CAS 决定唯一胜者：取消先提交则 Draft 丢弃，Response 先提交则 Task 已 `completed`，后续取消不能改写已发送回答；
7. `pending` Task 也可取消；终态 Task 不普通恢复，用户重新开始时创建新 Task。

#### 7.21.10 Provider Failover

Failover 是 Model Routing Policy，不是 Main Agent Tool：

- 只有当前 Logical Model Call 没有已接受权威 Result、错误可 Failover、目标 Provider 已批准且剩余预算/Deadline 允许时启用；
- 复用同一业务 Context Snapshot、Accepted Tool Observations、State Version 和 Action Purpose；目标 Provider 只生成自己的安全序列化/Rendering Hash；
- 已完成 Tool Call 永不因 Provider 切换重跑；
- 新 Provider 的 Context Capacity、Function Calling、Strict/Output Schema 和数据地域/合规能力必须满足当前合同；不兼容时禁止自动切换；
- Failover Attempt 使用新的 Provider Request ID/Attempt Ledger，但归属同一 Logical Call/Idempotency Record；
- 前一 Provider 在发送后状态不确定时保留 `uncertain` 和可能成本，不能假定未发生；Model Call 没有业务副作用时可按批准策略继续，但仍计入预算；
- Permission/Safety/Contract/Invalid Arguments 不能通过换 Provider 规避；
- Failover 次数、优先级、熔断和恢复窗口由版本化 Model Routing Profile 管理。

#### 7.21.11 Conversation 并发、用户 Steering 和消息顺序

首版推荐同一 Conversation 内串行提交会改变会话/Task State 的事件：

- User/Assistant Message 使用单调 `message_seq`；Turn/Action 使用各自单调 Sequence；
- 同一 Task State Version 只允许一个未完成的 Main Agent 决策调用；T09 已确认的多 Tool Call 首版仍按顺序执行；
- Execution Lease 防止两个 Worker 同时推进同一 Task；State Version 和 Effect Fence 是最终写入权威；
- 用户在 Task `running` 时发送新消息，消息先可靠追加并标记为 Steering Candidate；Runtime 在安全 Action Boundary 使当前 In-flight Model Call 取消/失效，再由 IntentUnderstandingPort 判断它是补充、纠正、取消、目标替代还是新目标；
- 只有 Main Agent/Intent Understanding 判断为独立新目标时才创建新 Task；Runtime 不按关键词固定分类；
- 首版不让同一 Conversation 中多个会改变共享 Context 的 Task 无序并行。未来如需要并行独立 Task，必须先定义独立 Scope、响应排序和 Merge/Conflict 规则；
- Slot Answer 通过 Resume Token/State Version 优先关联原 `pending` Task，不能被误消费为无关新 Task；
- 重复 Message/Event 通过 Idempotency Key 去重，但同文本的新用户消息不能仅因内容相同就被误删。

#### 7.21.12 Checkpoint 和 Crash Recovery

首版在以下安全边界写 Checkpoint，而不是每个 Token 都写：

- 接受新的 User/Steering Event 并形成新 State Version；
- Plan Version 被接受/替代；
- 外部 Effect 执行前已经冻结 Effect Fence/Budget Reservation；
- Model/Tool Result 通过校验成为权威 Observation；
- 进入 `pending` 前保存 Continuation；
- AnswerDraft 通过验证并准备 Commit；
- Task 进入终态。

Crash Recovery：

1. Recovery Worker 只接管仍为 `running/pending` 且 Lease 过期的非终态 Task；
2. 读取最新有效 Checkpoint、State Version、Open Action、Budget Ledger 和 Effect Fence；
3. Result 已持久化则只消费 Observation，不重执行；Effect 尚未开始则从 Checkpoint 继续；
4. Safe Idempotent Read 可以使用相同 Effect/Idempotency Key 重试；`reconcile_required/no_replay` 先查询远端 Receipt/状态；
5. `uncertain` 外部写 Effect 无法 Reconcile 时不能猜测，保持 `running` 的受控处置或安全 `failed`，不得进入等待用户 Slot 来掩盖系统不确定；
6. 恢复前重新校验权限、Source Head、Registry/Profile 和 Cancellation；Snapshot/Checkpoint 本身不授予权限；
7. `completed/failed/cancelled` 不被 Recovery 重新打开。用户重试创建新 Task，并明确引用可复用的 Checkpoint/Observation。

#### 7.21.13 错误与五个 Task 状态的映射

| 情况 | 顶层状态 |
|---|---|
| 正常模型/工具/规划/修复/异步等待 | `running` |
| 仅等待合法用户 Slot | `pending` |
| 已提交满足当前目标的回答，包括明确 Unknown/Limitations/拒绝越权请求 | `completed` |
| 非重试错误或预算/恢复问题导致无法产生安全回答 | `failed` |
| 用户/系统明确取消且 Cancellation Fence 成功提交 | `cancelled` |

补充规则：

- Tool `no_result`、证据 Unknown、Permission Refusal 和 Context Limitation 不是 Runtime Failure；如果能形成诚实回答，Task 应 `completed`；
- Recoverable Error、Backoff、Failover、Replan 和 Answer Repair 保持 `running`；
- 只有真正缺少用户输入且满足 Slot 条件时进入 `pending`；Tool 等待、预算不足、Provider 故障和内部审批状态不能滥用 `pending`；
- `failed` 说明 Runtime 未能安全完成，不等于业务研判为“不投标”；业务结论属于 Answer/Grounding；
- 终态转换继续遵守 T15/T16 的 State Version、Event ID、Effect Fence 和原子 Guard。

#### 7.21.14 最小可观察性和事件投影

内部至少保存：

- Task/Turn/Action/State Transition Event；
- Context Snapshot、Plan Version、Checkpoint 和 Response Artifact Ref/Hash；
- Model Logical Call/Attempt/Provider/Model/Context Hash、Token、Cost、Latency、Retry/Failover 和 Finish/Error Class；
- Tool Logical Call/Attempt/Binding/Arguments Hash、Scope Hash、Result Hash、Latency、Retry/Reconcile 和 Error Class；
- Budget Reservation/Settlement、Deadline、Cancellation、Lease/Fence、Late Result 和 Loop Detection；
- Answer Validation/Citation Guard/Repair/安全回退；
- Source/Registry/Profile/Policy Version 和 Feature Flag Snapshot。

建议指标：

- 完成、Pending、失败、取消率和各错误类别；
- Model/Tool Call、Token、Cost、P50/P95 延迟和 Failover；
- No Result、Degraded、Context Limited、Citation Guard Failure、Answer Repair；
- 重复 Tool、No-progress、Loop Stop、预算耗尽和迟到结果；
- Checkpoint Recovery、Lease Lost、Uncertain/Reconcile 和用户 Steering。

用户可见事件只展示安全状态：已开始处理、计划安全投影、正在查找资料、等待用户输入、正在生成/校验回答、回答完成、受限、失败或取消。不得暴露 Chain-of-Thought、完整 Prompt、Query Expansion、Tool Arguments、Scope Token、权限决策树、内部 ID、原始异常或 Provider 凭证。

现有 Runtime Trace 的“control-plane metadata only”原则可以复用，但新 Trace 应围绕动态 Action/Event，而不是固定 Task DAG 节点图。

#### 7.21.15 最小组件形态

```text
Conversation Mailbox / Turn Acceptor
        |
        v
Agent Runtime Controller
  - State/Lease/Fence Guard
  - Runtime Profile + Budget Ledger
  - Progress/Loop Guard
  - Deadline/Retry/Failover Policy
        |
        +--> Main Agent / Planner / Answer Repair Call
        +--> Tool Gateway / Direct or Durable Binding
        +--> Slot / Continuation
        +--> Answer Commit
        |
        v
Checkpoint + Action/Event/Call Ledger + Safe Projection
```

- Runtime Controller 可以同进程运行，只有 Durable Operation/Recovery 才需要 Worker；
- Budget、Progress、Retry 和 Cancellation 是 Runtime Policy，不是模型可调用 Tool；
- Model/Tool Provider Adapter 只执行已经接受的 Call，不自行扩大重试、预算或 Scope；
- Ledger 是审计事实，不作为下一步固定路由；Main Agent 只接收必要的安全 Observation；
- 首版不需要复杂分布式编排引擎，优先使用数据库事务、State Version、唯一键、Lease/Fence 和事件唤醒实现；
- 具体表结构、队列和迁移在 Architecture Baseline 后另行设计，只允许进入隔离本地开发环境。

#### 7.21.16 最小确认结论

1. Conversation/Turn/Task/Action/Event/Artifact 分层；Task 仍只使用五个顶层状态，Action 生命周期不升格为 Workflow 状态；
2. Main Agent 在 `running` 内执行开放动态 Action Loop，Runtime 只做合法性、预算、进展、Effect 和提交控制；
3. Runtime Profile 管 Active Duration、Model/Tool Calls、Token、Cost、Replan、Repair、Retry、No-progress 和并发；具体数值后续评测；
4. 每个有成本/Effect 的 Action 先原子预留预算和写 Effect Fence，完成后按实际结算；失败、重试和 Failover 也计费计时；
5. 以结构化 Action/Observation/Plan/Error Fingerprint 检测重复和无进展，不读取 Chain-of-Thought；持续循环时优先安全回答/Unknown，再失败；
6. Retry 由 Error Class、Replay Policy、Budget、Deadline、Circuit 和 Cancellation 共同决定，禁止任意 Exception 通用自动重试；
7. 普通 Model/首批只读 Tool 直接有界异步执行；只有长时、跨进程、回执或副作用 Reconcile 场景使用 Durable Async，等待时 Task 保持 `running`；
8. Cancel 原子进入 `cancelled` 并建立 Fence，不新增 `cancelling` 顶层状态；迟到结果不能进入 Context/Memory/Response；
9. Model Failover 复用同一业务 Context Snapshot 和已接受 Observation，不重跑 Tool；Provider 合规/合同不兼容或非重试错误禁止切换；
10. 首版同一 Conversation 串行提交共享状态，用户新消息作为 Steering Candidate 在安全边界重新理解；不按关键词固定路由；
11. Checkpoint 写在 Effect、Observation、Pending、Answer Commit 和终态等安全边界；Crash Recovery 只接管非终态，并按 Replay/Reconcile Policy 决定是否重试；
12. Unknown、No Result、权限拒绝和带限制回答可以 `completed`；只有等待合法 Slot 才 `pending`，Runtime 无法安全完成才 `failed`；
13. 内部 Ledger 完整记录 State/Call/Budget/Loop/Cancel/Recovery，用户只看到脱敏进度和结果；
14. 首版以事务、State Version、Lease/Fence、唯一键和事件唤醒为主，不恢复固定 DAG 或强制所有 Action 入队。

## 8. 架构议题清单

| 议题 | 状态 | 需要解决的问题 |
|---|---|---|
| T01 使用场景与问答边界 | 部分确认 | 真实问题集合、跨项目范围、用户角色、典型失败场景 |
| T02 意图与信息需求理解 | 初版已确认 | 使用逻辑独立的 IntentUnderstandingPort，初版复用 Main Agent 模型做开放式目标和信息需求理解；仅阻塞性缺失创建 Slot，语义拆解与 Retrieval Query 分层，未来分类快速路径由评测触发 |
| T03 Task Planner | 初版已确认 | 按复杂度触发，使用 Runtime 内部结构化 LLM 调用和有限步滚动计划；Step 为信息/决策子目标，Planner 不执行 Tool，仅在实质变化时修订；精细 Schema/预算/并行在实践中补充 |
| T04 离线 RAG | 初版已确认 | 复用不可变 Parse/Chunk/Retrieval 资产；双知识域隔离，三层 Chunk、结构优先切块、可追溯重叠、精确 Locator，以及 Hash/Ready Head/Stale/Tombstone 版本治理；算法参数由后续授权评测调整 |
| T05 在线召回 | 初版已确认 | 采用确定性 RetrievalQueryStrategyPort、召回前 Scope/Snapshot/Metadata Filter、词法主导 Hybrid Default、BM25F+Child-only Vector、Rank-only Weighted RRF、稳定去重，以及显式 No Result/Degraded 语义；数值参数由授权评测调整 |
| T06 重排与证据升级 | 初版已确认 | 采用默认关闭的条件式 RerankPolicyPort、有界锚点保护重排和 Fusion Baseline 安全降级；以 Atom Read 为唯一证据升级边界，分离 Integrity、Authority、Semantic Assessment，并以 Citation Guard 约束最终引用 |
| T07 工具设计 | 初版已确认 | 首批四工具、六字段 ToolDefinition、场景去重与动态白名单、最小 Input/Output、Safety 四字段及 `ok/data/error` Envelope 已收束；精确参数与扩展错误语义在实践中迭代 |
| T08 Tool Router | 初版已确认 | Router 只做可见工具投影、Guard 只做授权、Gateway 编排 Visibility/Execution 两次检查；每个模型决策轮次重新冻结白名单，初版不增加独立路由 LLM |
| T09 调用协议 | 初版已确认 | 采用模型侧 Function Calling、执行侧 Local/MCP Binding 的统一协议；Canonical Registry/Pydantic 为唯一事实源，Gateway 统一校验，首批只读 Tool 直接有界异步执行，结果以 Canonical Tool Message 回流 |
| T10 Memory | 初版已确认 | 采用四层逻辑 Memory；与 Checkpoint/Message/Evidence/Context 分离，以受控写入、Source 依赖、版本失效、Scope 隔离和可遗忘边界治理；具体 Context 组装进入 T11 |
| T11 上下文工程 | 初版已确认 | 采用 Provider-aware Profile、六个 Context Lane、硬保护区 + 动态弹性区、分层压缩、索引 + 原文回取及不可变 Context Snapshot；数值参数留给授权评测 |
| T12 回答与引用 | 初版已确认 | 采用用户自由回答 + 内部 AnswerDraft、Grounding Binding、Runtime Citation Projection、缓冲校验后发布，以及事实/推断/未知/冲突/修正边界 |
| T13 运行治理 | 初版已确认 | 仅冻结资源/预算上限、重复/无进展循环防护、幂等/Effect Fence、取消/迟到结果、Direct/Durable 和 Checkpoint/Recovery 六项护栏；参数与高级策略进入开发 Backlog |
| T14 评测体系 | 未开始（非开发前置） | 在开发阶段按授权逐步建立意图、规划、召回、重排、工具、证据、回答和端到端评测；不再作为继续扩写长设计记录的前置项 |
| T15 Slot 与交互状态 | 初版已确认 | Slot 生成、等待输出、两阶段校验、友好重试、最小 Slot/PendingContext 字段和恢复行为已确认；超时策略在实践中补充 |
| T16 Agent State Machine | 讨论中 | 五状态、转换、Guard、Continuation Checkpoint、Effect Fence 和原子恢复已确认；State Context 其余字段和实现库仍待收束 |

## 9. 关键术语

### 9.1 Task Plan

Agent 针对当前目标形成的动态任务策略。它可以被观察结果、新用户输入、工具失败和权限约束修改。

### 9.2 Workflow

开发阶段预设业务步骤、节点和依赖，并由代码驱动任务按既定路径执行。新 Agent 不采用固定业务 Workflow。

### 9.3 Intent Recognition

理解用户当前想完成什么。它不等同于把问题强制归入单一固定标签。

### 9.4 Information Need

为了回答当前问题，需要确认哪些事实、比较哪些维度、达到什么证据要求，以及存在什么歧义。

### 9.5 Query Planning

把 Information Need 转换为一个或多个可执行检索目标，包括查询拆解、扩展、多维查询、过滤和来源选择。

### 9.6 Retrieval

针对已有索引执行候选召回。BM25、向量检索、混合检索和 RRF 属于 Retrieval 策略，不等同于完整 RAG。

### 9.7 RAG

从资料治理、解析、切块、索引、查询理解、召回、重排、上下文组装到生成和引用验证的完整知识增强体系。

### 9.8 Memory

Agent 在不同时间尺度和授权 Scope 下允许复用的对话派生状态、目标状态、证据引用、项目事项和用户偏好。Memory 是可版本化、可失效的 Context 辅助，不保存私有推理，也不替代事实权威。

### 9.9 Slot

Agent 在完成当前动态任务时发现缺失、并需要从用户或其他获准来源补充的结构化信息。Slot 在运行时按需生成，不是预设业务表单中的固定必填项。

### 9.10 Runtime State Machine

管理 Plan、Step 和 Slot 的暂停、恢复、完成、失败与替代等通用运行生命周期。它不规定投标研判必须经历哪些业务阶段，因此不等同于固定 Workflow。

### 9.11 Context Projection

Context Assembler 针对某一次实际模型调用，从当前授权资产中选择并安全表示的有界输入。它不是数据源，也不会因被投影而改变 Source 权威。

### 9.12 Context Snapshot

对一次 Context Projection 的不可变清单与审计收据，记录 Source/Version/Hash、Included/Excluded、Compression 和 Token 信息，并由对应 Model Call 或 Continuation Checkpoint 引用。

### 9.13 AnswerDraft

Main Agent 为一次用户回答产生的内部结构化候选，由通用 Content Block、Grounding Ref 和限制信息组成。它约束证据责任和发布安全，不规定用户回答必须具有固定业务章节。

### 9.14 Citation Projection

Runtime 根据已经验证的 Grounding/Evidence/Business Link、当前权限和 Source Locator 生成的用户可见引用。模型只能选择合法支持引用，不能直接生成 Citation URL、页码或内部资源标识。

### 9.15 Agent Turn

一个被接受的用户输入及其对应交互结果。Turn 可以创建新 Agent Task，也可以携带 Slot Answer 恢复已有 `pending` Task；它不是固定业务阶段。

### 9.16 Runtime Action

Task 在 `running` 内已经被 Runtime 接受的一次动态行动，例如 Model Call、Planner Call、Tool Call、Plan Revision、Answer Repair 或 Response Commit。Action 有独立 Ledger/Effect Fence，但不是 Task 顶层状态。

### 9.17 Runtime Profile

由产品/租户策略约束并按版本冻结的运行资源与可靠性配置，包括时间、调用、Token、费用、Replan、Repair、Retry、No-progress 和并发上限。模型和 Planner 不能自行扩大 Profile。

### 9.18 Progress Fingerprint

Runtime 根据 Action、Arguments、State/Plan/Context/Scope Hash 和 Observation 变化形成的结构化进展标识，用于发现重复调用、无效改写和循环；不依赖读取 Chain-of-Thought。

## 10. 决策状态规则

本文档中的设计项必须使用以下状态：

| 状态 | 含义 |
|---|---|
| 已确认 | 用户已经明确认可，可进入架构基线 |
| 候选方案 | 已提出并有初步理由，但尚未获得确认 |
| 讨论中 | 正在比较方案，尚未形成推荐或结论 |
| 未开始 | 已知需要设计，但尚未展开 |
| 已否决 | 明确不采用，并记录否决原因 |
| 已替代 | 曾经确认，后来由新决策替换 |

助手提出的“建议”“推荐”“更合适”一律只属于候选方案，除非用户明确确认。

## 11. 单项决策记录模板

后续每个关键设计采用以下模板：

```text
议题编号：
问题定义：
真实使用场景：
现有资产与约束：

候选方案 A：
优点：
缺点：
失败方式：

候选方案 B：
优点：
缺点：
失败方式：

候选方案 C：
优点：
缺点：
失败方式：

评判指标：
需要的实验或评测：
推荐方案：
用户确认结果：
最终决定：
未解决问题：
对其他组件的影响：
```

## 12. 协作和开发顺序

1. 一次集中讨论一个架构议题；
2. 先定义问题和真实场景；
3. 列出候选方案、成本、失败方式和评测方法；
4. 明确推荐只是候选，不自动视为决定；
5. 用户确认后更新 ADR 和议题状态；
6. 关键议题完成后冻结 Architecture Baseline；
7. 根据基线拆分开发阶段和最小验证计划；
8. 任何 Agent 测试或模型验证仍需单独取得用户明确授权。

## 13. 已否决方向

### R-001：固定 P0-P4 业务阶段

否决原因：下一步由代码预设，属于 Workflow。

### R-002：固定 27 Task DAG 作为 Agent 主控制结构

否决原因：任务数量和路径在运行前已确定，无法适应开放式问题。

### R-003：用固定业务输入和固定报告字段定义所有交互

否决原因：会迫使 Agent 围绕填充结构执行隐藏 Workflow。

### R-004：把知识问答按单一信息源固定路由

否决原因：具体问题可能需要组合招标资料、企业知识和会话上下文。

### R-005：把认知活动设计成 Agent Task 顶层状态链

否决方向：`received → planning → plan_validating → ready → executing → observing → replanning/responding`。

否决原因：它强制所有任务依次经过预设的认知阶段，会让简单直答和开放式工具选择重新落入 Workflow。Planning、Tool Call、Observation 和 Response 应作为 `running` 内的动态 Action/Event 记录。

## 14. 设计收束与开发入口

T13 已按六项最小运行护栏确认，Architecture Baseline v0.1 由此冻结。本文不再逐项扩写 T14 或其他细节议题。

后续开发使用以下两份短文档：

- `bid-assessment-pure-agent-architecture-baseline-v0.1-20260820.md`：实现时的架构权威；
- `bid-assessment-pure-agent-development-task-list-v0.1-20260820.md`：开发顺序、依赖、交付物与授权边界。

T14 评测体系转为开发工作流中的受控任务，不阻塞首批合同与骨架代码。任何 Agent 测试、模型调用、真实 PDF、OCR/视觉、Embedding、Reranker、检索评测或外部 MCP 仍须在执行前重新取得用户明确授权。

## 15. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-20 | v0.47 | 用户确认 T13 按六项最小护栏收束：资源/预算上限、重复/无进展循环防护、幂等/Effect Fence、取消/迟到结果隔离、Direct/Durable 触发边界、Checkpoint/Recovery；新增 P-021、ADR-028；长设计记录转为 Discussion Archive 并停止继续扩写，另行生成短版 Architecture Baseline v0.1 和开发任务清单；T14 精细设计转入开发阶段按授权推进 |
| 2026-08-20 | v0.46 | 提出 T13 Runtime Governance 候选：分离 Conversation/Turn/Task/Action/Event/Artifact，并在五状态内使用开放动态 Action Loop；提出版本化 Runtime Profile、原子 Budget Reservation/Settlement、Progress Fingerprint/Loop Guard、分层 Deadline 与类型化 Retry、Direct/Durable Async 分流、Cancellation/Late Result Fence、Provider Failover、Conversation Steering/串行提交、Checkpoint Recovery、状态映射和脱敏可观察性；首版复用事务/Version/Lease/Fence/Ledger 思想，不恢复固定 DAG、任意异常自动重试或所有 Action 强制入队，等待用户确认 |
| 2026-08-20 | v0.45 | 用户确认 T12 回答与引用初版边界：用户输出保持自由自然语言，内部使用通用 Block AnswerDraft；确认 Claim Type/Epistemic Status/Source Basis 分离、Material Statement Grounding、Runtime CitationProjector、支持矩阵、八类限制语义、确定性 Guard 权威边界、有界修复与安全回退、缓冲校验后发布、回答版本/失效和 User Memory 风格边界；首版不增加 Answer Writer/Verifier Agent 或 Claim-level 抢跑式 Streaming；新增 P-020、ADR-027，下一项进入 T13 Runtime Governance |
| 2026-08-19 | v0.44 | 提出 T12 回答与引用候选：用户输出保持自由自然语言，内部使用 Narrative/Statement/Limitation/Interaction 通用 Block 的 AnswerDraft；分离 Claim Type、Epistemic Status 和 Source Basis，并定义 Supported/Partial/Conflicted/Unknown 表达；模型只选择 Grounding/Quote Ref，Runtime CitationProjector 负责安全 Citation；提出支持矩阵、八类限制语义、确定性 Guard 权威边界、有界修复/安全回退、缓冲校验后发布、回答版本/失效和 User Memory 风格边界；首版不增加独立 Answer Verifier 或 Claim-level 抢跑式 Streaming，等待用户确认 |
| 2026-08-19 | v0.43 | 用户确认 T11 Context Engineering 初版边界：ContextAssemblerPort 作为每次模型调用前的确定性输入治理；确认六个 Context Lane、硬保护区 + 动态弹性区、Provider-aware Token 判定、不可无声截断项、L0-L4 分层压缩、带 Source Ref/Hash 的 Conversation Summary、五类 Assembly Result、不可变 Context Snapshot 和 Prompt Injection/Scope 隔离；首版优先确定性选择和结构化投影，条件式模型摘要与数值参数留给授权评测；新增 P-019、ADR-026，下一项进入 T12 回答与引用 |
| 2026-08-19 | v0.42 | 提出 T11 Context Engineering 候选：复用旧 Context Manifest 的 Scope/Hash/Included-Excluded 治理思想但不复用固定 DAG/P0-P1/字符估算；定义 ContextAssemblerPort、六个 Context Lane、硬保护区 + 动态弹性区、Provider-aware Profile 和最小 Request/Entry/Snapshot；提出 L0-L4 分层压缩、带 Message Source/Hash 的 Conversation Summary、各类内容选择规则、五类 Assembly 结果、Snapshot/恢复/可复现性及 Prompt Injection/Scope 边界；首版优先确定性选择和结构化投影，条件式模型摘要与数值参数等待授权评测，方案等待用户确认 |
| 2026-08-19 | v0.41 | 用户确认 T10 Memory 初版边界：采用 Working、Conversation、Project/Assessment、User 四个逻辑层，并与 Checkpoint、Message、Observation、Evidence/业务事实和 Context Projection 分离；确认 Memory 为带 Source、可版本化/失效/遗忘的 Context 辅助，采用受控写入、Scope-first 读取、纠正/冲突/Source 变化和遗忘治理；首版不增加通用 `write_memory` Tool、独立 LLM Memory Extractor 或 Memory Vector Index；新增 P-018、ADR-025，下一项进入 T11 Context Engineering |
| 2026-08-19 | v0.40 | 提出 T10 Memory 候选边界：区分 Working、Conversation、Project/Assessment 与 User 四个逻辑层，并与 Checkpoint、Message、Observation、Evidence/业务事实和 Context Projection 分离；Memory 定位为带 Source、可版本化/失效/遗忘的 Context 辅助；提出三档写入与永久禁入规则、类型化最小 Envelope、Scope-first 读取、纠正/冲突/Source 变化、Pending 恢复、遗忘/删除和安全隔离规则；首版不增加通用 `write_memory` Tool、独立 LLM Memory Extractor 或 Memory 向量索引，等待用户确认 |
| 2026-08-19 | v0.39 | 用户确认 T09 统一调用协议：模型侧使用 Function Calling，执行侧以 Canonical Definition 选择 Local/MCP Binding；Registry/Pydantic 为唯一事实源，Gateway 的 Input/业务/权限/Safety/Output/Provenance 校验为最终权威；确认短期 MCP Auth Context、Structured Content、Canonical Tool Message、首批只读 Tool 直接有界异步执行、Ledger/Effect Fence、有界大结果和安全流式事件边界；新增 ADR-024，下一项进入 T10 Memory |
| 2026-08-19 | v0.38 | 提出 T09 统一调用协议候选：模型侧使用 Function Calling，执行侧以 Canonical Definition 选择 Local/MCP Binding；Registry/Pydantic 为唯一工具事实源，Provider strict/MCP Schema 仅早期防错，Gateway 的 Input/业务/权限/Safety/Output/Provenance 校验为最终权威；提出首批只读 Tool 直接有界异步执行、Tool Call/Result Ledger 与 Effect Fence、短期 MCP Auth Context、Structured Content、Canonical Tool Message、有界大结果和安全 UI 事件边界 |
| 2026-08-19 | v0.37 | 用户确认 T06 最小边界：Reranker 与证据权威严格分离，采用初版默认关闭的 Runtime RerankPolicyPort、冻结候选/锚点保护/有界 Promotion 和 Fusion Baseline 安全降级；确认 Atom Read 为唯一证据升级边界，分层执行 Integrity、Source Authority 与 Main Agent Semantic Assessment，并以 Grounding Record/Citation Integrity Guard 约束事实、推断、unknown、冲突和最终引用；新增 ADR-023，下一项进入 T09 |
| 2026-08-19 | v0.36 | 提出 T06 重排与证据升级候选：Reranker 与证据判断严格分离，初版通过 Runtime RerankPolicyPort 默认关闭并仅对冻结候选做锚点保护、有界 Promotion；可选重排失败回到原样 Fusion Baseline，上游血缘异常 fail-closed；提出以 Atom Read 为唯一证据升级边界，分层执行 Integrity、Source Authority 和 Main Agent Semantic Assessment，并以 Grounding Record/Citation Integrity Guard 区分事实、推断、unknown、冲突和最终引用 |
| 2026-08-19 | v0.35 | 用户确认在线召回最小边界：Main Agent/Planner 与 Search Query Strategy 分层，初版使用确定性 Query Optimizer；确认召回前 Scope/Snapshot/Metadata Filter、词法主导 Hybrid Default、BM25F+Child-only Vector、Rank-only Weighted RRF、稳定 Key 去重，以及 Child Candidate→Atom Read 和 No Result/Degraded 语义；新增 ADR-022，下一项进入 T06 |
| 2026-08-19 | v0.34 | 提出在线召回最小边界候选：Main Agent/Planner 与 Search 内 Query Strategy 分层，初版复用确定性 Query Optimizer；推荐召回前强制 Scope/Snapshot/Metadata Filter，以及词法主导 Hybrid Default、BM25F+Child-only Vector、Rank-only Weighted RRF、稳定 Key 去重；保持 Search Child Candidate/Read Atom 证据边界，并明确 No Result 与 Lexical-only Degraded 语义 |
| 2026-08-19 | v0.33 | 用户确认离线 RAG 最小边界：复用不可变 Parse/Chunk/Retrieval 资产，双知识域共享逻辑合同但隔离 Scope/Head/索引；确认 Parent/Child/Atom 三层证据、结构优先和可追溯重叠、Native/OCR 与精确 Locator 元数据，以及 Hash 幂等、旁路重建、Ready Head、Stale/Tombstone 版本治理；新增 ADR-021，下一项进入 T05 在线召回 |
| 2026-08-19 | v0.32 | 提出离线 RAG 最小边界候选：复用现有不可变 Parse/Chunk/Retrieval Index 资产，招标与企业知识共享逻辑合同但隔离 Scope/Head/索引；保留 Parent/Child/Atom 三层证据，采用结构优先和可追溯重叠；明确 Native/OCR、表格、Locator 元数据，以及 Hash 幂等、旁路重建、Ready Head、Stale/Tombstone 版本治理 |
| 2026-08-19 | v0.31 | 用户确认意图理解边界：保留逻辑独立 IntentUnderstandingPort，初版复用 Main Agent 模型和完整 Context 生成开放式 Understanding Decision；不采用固定标签分类模型，未来封闭意图快速路径由评测触发；新增 ADR-020 |
| 2026-08-19 | v0.30 | 提出主 Agent 意图与信息需求理解候选：不设固定业务意图分类器，由 Main Agent 形成开放目标、信息需求、来源提示、澄清需求和 direct/planned 决策；仅阻塞且必须由用户提供的信息创建 Slot，语义子目标拆解与 Retrieval Query Rewrite/Expansion 分层 |
| 2026-08-19 | v0.29 | 用户确认 Planner 组件形态和有限步滚动规划：Planner 是 Main Agent Runtime 内逻辑独立的结构化 LLM 调用，默认复用模型配置；Step 表达信息/决策子目标，Planner 不执行工具，仅在实质变化时修订；新增 ADR-019 |
| 2026-08-19 | v0.28 | 提出 Planner 组件形态与最小粒度候选：Planner 作为 Main Agent Runtime 内逻辑独立的结构化 LLM 调用，默认复用同一模型配置；采用有限步滚动计划，Step 描述信息/决策子目标而非 Tool Call；Planner 不执行工具，只在实质变化时修订，Query Decomposition/Expansion 暂不独立成 Tool |
| 2026-08-19 | v0.27 | 用户确认 Slot、PendingContext、Continuation Checkpoint 最小字段和原子恢复机制；direct/planned 共用 suspended Action，恢复统一回 running；Slot resolved、Checkpoint consumed、Task 恢复和 State Version 更新原子提交；新增 ADR-018 |
| 2026-08-19 | v0.26 | 提出 Slot/Pending/Continuation 最小可恢复字段候选：Slot 保存请求与校验合同及 candidate/resolved 引用，PendingContext 保存当前 Slot/Checkpoint/校验阶段引用，Checkpoint 使用通用 suspended Action、Context Snapshot、Effect Fence 和一次性 Resume Token Hash；direct/planned 共用恢复结构，三项恢复写入原子提交 |
| 2026-08-19 | v0.25 | 用户确认五状态合法转换和最小 Guard：Task 创建进入 running，普通活动 running 自转换，只有有效 Slot/Checkpoint 才进入 pending，Slot resolved 后恢复 running；completed/failed/cancelled 为不可恢复终态；所有转换校验 Event ID、State Version、合法矩阵和副作用幂等 |
| 2026-08-19 | v0.24 | 用户确认 Agent Task 顶层只采用 `running/pending/completed/failed/cancelled` 五个状态；`active` 正式移除，规划、工具、Observation 和响应继续作为 running 内动态活动；下一步仅确认合法转换和最小 Guard |
| 2026-08-19 | v0.23 | 用户明确 Agent Task 需要 `running` 运行中状态；为避免 `active/running` 语义重叠和机械切换，当前推荐以 running 替代 active，五状态候选调整为 `running/pending/completed/failed/cancelled`，所有动态认知和工具活动留在 running 自转换内 |
| 2026-08-19 | v0.22 | 用户强调 Agent Task 必须避免进入 Workflow；确认状态机只管理通用运行生命周期，撤销 `received→planning→validating→executing→observing→responding` 候选链；提出 `active/pending/completed/failed/cancelled` 五状态、active 自转换和执行模式/动态活动与顶层状态分离的候选，并新增 ADR-017、R-005 |
| 2026-08-19 | v0.21 | 用户确认最小 Complexity Gate 与 direct→planned 升级：初版由主 Agent 判断，单一目标短证据循环保持 direct，多目标、依赖、跨来源完整研判、分支或高风险动作触发 planned；升级时保留已有 Evidence/Observation，只规划剩余任务 |
| 2026-08-19 | v0.20 | 用户确认 Planner 仅在任务复杂度达到条件时启用；新增 P-016、ADR-016，并提出最小 Complexity Gate 候选：初版由主 Agent 判断 `direct/planned`，单一目标短证据循环无需正式 Plan，多目标、依赖、跨来源完整研判和运行中分支触发 Planner，允许保留已有 Observation 后从 direct 升级 |
| 2026-08-19 | v0.19 | 用户确认 Tool Router 与 Permission Guard 的最小边界：Router 管相关性、Guard 管授权、Gateway 组织两次检查；动态白名单按模型决策轮次冻结，初版不增加独立路由 LLM；新增 ADR-015 |
| 2026-08-19 | v0.18 | 用户进入 Tool Router/Permission Guard 议题，上一项 Safety/Result 按最小方案收束；提出 Router 相关性投影与 Guard 授权分离、Visibility/Execution 两次 Guard、每个模型决策轮次重新冻结白名单、初版不增加独立路由 LLM 的候选边界 |
| 2026-08-19 | v0.17 | 提出初版最小 Safety 与 Result 候选：Safety 只回答副作用、数据范围、外部流出和逐次审批；四个首批 Tool 均为只读、Context-bound、无外部流出、无逐次业务审批；`ToolExecutionResult` 只保留 `ok/data/error` 和五类安全错误码，运行审计元数据留在 Ledger |
| 2026-08-19 | v0.16 | 用户确认初版架构不做穷尽式精细设计，采用最小可实施边界并在实践中发现问题后迭代；Tool Input/Output 收敛为最小语义合同，v0.15 的数值上限、Context Mode、分页和扩展结果字段降级为实现候选，不再作为开发前置确认项 |
| 2026-08-19 | v0.15 | 提出首批四个 Tool 的 Input/Output Pydantic Model 候选：严格公共基类、模型参数无隐藏默认值、Search/Read 的 Evidence 升级结构、扁平分页 Outline、结构/业务安全/输出溯源三段校验；字段、上限和结果结构待用户确认 |
| 2026-08-19 | v0.14 | 用户确认 Tool Description 使用具体、低重叠的正向场景并优先采用每轮动态白名单；确认仅在评测触发时增加可选 Example/Few-shot；确认 Function Calling 首版不预发完整 Output Schema，Provider 支持时开启 strict，但 Runtime 校验保持最终权威 |
| 2026-08-19 | v0.13 | 提出 Tool Description 与模型可见合同候选：中文两句描述覆盖动作、来源、返回、使用/禁用边界和证据升级；Function Calling 仅投影 name、description、input_schema，strict 由 Provider Adapter 设置；Output Schema、Execution、Safety、Runtime Context 和审计 Hash 默认不进入模型工具定义 |
| 2026-08-19 | v0.12 | 用户确认六字段 Canonical ToolDefinition：`name`、`description`、`input_model`、`output_model`、`execution`、`safety`；确认 Runtime Context 通过显式 DI 分离、版本由 Registry/Definition/Schema Hash 治理，并将首批 Tool Name 收敛为 Provider-safe snake_case |
| 2026-08-19 | v0.11 | 评审 `name`、`context_model` 和每 Tool SemVer 三项异议；将当前推荐收敛为六字段：一个 Provider-safe `name` 同时承担内部与模型身份，Runtime Context 改为 Executor 显式依赖注入，版本追溯改由 Registry Snapshot/Definition/Schema Hash 负责 |
| 2026-08-19 | v0.10 | 提出 Canonical ToolDefinition 首版八字段候选：ID、合同版本、描述、输入/上下文/输出 Pydantic Model、执行绑定和安全属性；明确 Function Calling 是模型协议投影，local/MCP 才是执行绑定；examples、capability taxonomy、few-shot 和动态策略不进入首版必填字段 |
| 2026-08-19 | v0.9 | 冻结首批 Active Registry 只包含 `bid.document.search`、`enterprise.knowledge.search`、`evidence.read` 和 `documents.outline`；其余旧合同不进入新 Agent，离线知识处理和模型比较推理不伪装成 Tool |
| 2026-08-19 | v0.8 | 确认新 Pure Agent 将旧 Schema Registry、数据库 Registry Version、Task `allowed_tools` 和 Adapter Map 四份工具注册事实收敛为一个 Canonical Tool Registry；旧版本记录仅保留历史只读兼容；首批工具和字段待确认 |
| 2026-08-19 | v0.7 | 确认 Slot resolved 后从原 `pending` 暂停点精确续跑；引入 Continuation Checkpoint、Resume Token、恢复 Guard 和副作用幂等要求；默认不重新规划或重跑已完成 Step |
| 2026-08-19 | v0.6 | 确认 Pending Slot 完整交互闭环；增加 `waiting_input`、格式校验、业务校验和恢复子状态；确认任一校验失败均友好指导用户重试且保持 `pending` |
| 2026-08-19 | v0.5 | 确认引入 Agent State Machine 管理行为、流转和状态数据；确认 `tool_hint` 引用 Tool Registry；确认 `expected_output` 加 `output_schema` 双轨输出约束；增加候选状态、事件、Guard 和 State Context |
| 2026-08-19 | v0.4 | 根据用户纠正将 Slot 从 Planner Step 中完全分离；确认 Step 使用 `id`、`title`、`description`、`dependencies`、`tool_hint`、`expected_output`、`risk_level`；调整 `pending` 为任务因运行时 Slot 挂起的状态 |
| 2026-08-19 | v0.3 | 确认 Planner 顶层核心字段；扩展详细 Step；确认关键字段使用 Pydantic/JSON Schema 等程序化校验；确认 `pending` 用于等待用户 Slot；增加 Plan、Step、Slot 候选状态机 |
| 2026-08-19 | v0.2 | 确认 Planner 机器输出必须通过版本化 JSON Schema；确认计划支持用户可见流式展示；增加候选 JSON 实例和流式事件协议 |
| 2026-08-19 | v0.1 | 建立设计记录；录入 Pure Agent、开放输入输出、动态信息源选择和 LLM Task Planning 四项已确认原则；登记完整开放议题清单 |
