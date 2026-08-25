# 旗胜投标机会研判 Pure Agent — 开发任务清单 v0.1

| 项目 | 内容 |
|---|---|
| 状态 | C08-4 本地日常页面真实闭环通过：最终 Task `completed/v18`，动态 Action、回答提交与 5 条 Runtime Citation 均通过权威校验；中间失败产物全部保留且未提交无效回答 |
| 版本 | v0.1 |
| 日期 | 2026-08-22 |
| 架构依据 | `bid-assessment-pure-agent-architecture-baseline-v0.1-20260820.md` |
| 讨论归档 | `bid-assessment-pure-agent-architecture-design-record-20260819.md` |
| 推荐下一指令 | `进入 C09：仅在隔离本地开展小规模业务试用与缺陷登记；每次涉及新真实资料、模型、Embedding、Reranker、OCR/视觉或外部能力时仍需重新明确授权` |

## 1. 开发护栏

所有任务默认遵循：

- 使用确切工作区 `C:\Users\12521\.codex\worktrees\a73d\Clear_test`；
- 保留全部未提交改动，不执行 reset、checkout、clean 或覆盖；
- Pure Agent 新代码、API、配置、任务和迁移只用于完全隔离的本地开发；
- 所有 Pure Agent 功能开关默认关闭，不修改旧 `bid_intake_*`；
- 当前隔离开发迁移唯一 head 为 `20260821_0110`，由 `20260820_0109` 延续；该迁移尚未应用，只允许完全隔离的本地开发数据库，不得应用到 ECS；
- 不连接、迁移、备份、重启或修改 ECS、生产 MySQL、Redis、MinIO、Milvus 和旧 CentOS；
- 在运行任何 Agent 测试、模型、真实 PDF、OCR/视觉、Embedding、Reranker、检索评测或外部 MCP 前，必须重新取得用户明确授权；
- 未取得上述授权时，只执行设计、代码修改以及不触发 Agent 链路的静态语法、类型、导入和 Schema 检查。

## 2. 开发批次总览

| 批次 | 目标 | 主要交付 | 授权边界 | 状态 |
|---|---|---|---|---|
| B00 | 收束架构 | 短版 Baseline、开发任务清单、长记录归档 | 文档与只读核对 | 已完成 |
| B01 | 合同与禁用态骨架 | 包结构、Pydantic 合同、状态机、Ports、Registry、默认关闭开关 | 代码修改 + 静态检查 | 已完成 |
| B02 | 本地持久化与恢复 | 本地迁移、Repository、Task/Slot/Checkpoint/Effect/Budget | 迁移仅隔离本地；Agent 测试另行授权 | 已完成 |
| B03 | Tool Gateway 与 RAG Adapter | Registry/Router/Guard/Gateway/Executor、四工具 Adapter | 不调用真实 Tool/RAG/MCP | 已完成 |
| B04 | Agent Runtime | Intent、Complexity Gate、Planner、Context、Memory、Provider、动态 Action Loop、Runtime Guard | 不调用模型；Agent 测试另行授权 | 已完成（B04-1—B04-5） |
| B05 | 回答、API 与交互界面 | AnswerDraft、Citation、SSE、Pending/Resume、取消和管理视图 | 保持功能开关关闭 | 已完成（B05-1—B05-7） |
| B06 | 授权后的验证与校准 | 合同、状态、RAG、模型、真实资料和端到端评测 | 每类运行前重新明确授权 | 已完成（V601—V608） |
| C01 | 本地产品集成 | Runtime Controller、单 Action Pulse、Conversation API 唤醒、服务端 Continuation | Runtime 默认关闭；专项测试已授权 | 已完成（6/6） |
| C02 | 动态能力装配 | Composition Root、Main Agent Boundary、Capability Executors、显式 Bootstrap | 默认关闭；模型/Tool/Agent 测试分别授权 | 已完成（C02-1—C02-4，专项 8/8） |
| B07 | 发布准备 | RC、发布清单、迁移/回滚手册 | 未获“全部开发完成并允许上线”前不得开始 | 阻塞 |

## 3. B01 — 合同与禁用态骨架

本批次是推荐的首个实现批次，不涉及数据库迁移、模型调用、真实工具执行或 Agent 测试。

| ID | 任务 | 交付物 | 依赖 | 完成条件 |
|---|---|---|---|---|
| D101 | 建立 Pure Agent 独立包 | 新包目录、清晰模块边界、无启动副作用 | B00 | 与旧执行链隔离，可被静态导入 |
| D102 | 定义 Task 与状态合同 | 五状态 Enum、Transition Event/Decision、State Version、最小 Guard 输入输出 | D101 | 合法转换矩阵由纯函数表达，顶层无 Workflow 阶段 |
| D103 | 定义 Planner 合同 | Plan、Step、Plan Revision、Complexity Decision 的 Pydantic 模型 | D101 | 顶层六字段和 Step 八字段可生成 JSON Schema；依赖和 Registry 引用可验证 |
| D104 | 定义 Slot 与 Continuation 合同 | Slot、Validation Issue、PendingContext、Continuation Checkpoint、Resume Decision | D102 | 支持格式校验、业务校验和原挂起点恢复，不把 Slot 放入 Step |
| D105 | 定义 Tool 合同 | 六字段 ToolDefinition、Execution/Safety、Input/Output Envelope、Runtime Context | D101 | Runtime Context 不进入模型 Schema；Pydantic 是唯一合同源 |
| D106 | 建立最小 Active Registry | 四个 Tool Definition 与互斥正向 description，Binding 先用禁用/未配置占位 | D105 | Registry 只含四工具，不执行真实 Tool |
| D107 | 定义 Runtime Ports | Intent、Planner、Context、Memory、Model、Tool Gateway、Answer、Checkpoint 等 Protocol | D102-D105 | 组件可替换，无组件调用链被固化为 Workflow |
| D108 | 增加默认关闭配置 | Pure Agent 独立 Feature Flag 与安全配置读取 | D101 | 默认 false，不修改旧 `bid_intake_*`，不开启 Worker/模型 |
| D109 | 静态验收 | 仅做格式、语法、导入、类型或 Schema 生成检查 | D101-D108 | 不触发 Agent 测试、模型、数据库或工具链 |

### B01 明确不做

- 不创建或应用 Alembic migration；
- 不调用任何 Provider、Embedding、Reranker、RAG、MCP 或真实 Tool；
- 不运行 Agent 单元、集成、评测或端到端测试；
- 不接入现有 Runtime Lab 的活动执行；
- 不改变现有 view-only 演示和任何发布候选。

### B01 完成记录（2026-08-20）

- D101—D108 已在独立 `app/agents/bid_assessment_pure/` 包和独立默认关闭配置中完成；
- D109 静态验收通过：12 个相关 Python 文件 AST 正常，包可导入，Plan/Slot/State JSON Schema 可生成；
- 已确认 Plan 顶层 6 个核心字段、Step 8 个字段、5 个顶层状态和 4 个首批 Tool；
- 四个 Tool 的 Execution Binding 全部为 `disabled`，Pure Agent Feature Flag 默认 `false`；
- 未创建迁移，未运行 Agent 测试、模型、真实资料、RAG、MCP 或 Tool 执行。

## 4. B02 — 本地持久化与恢复

| ID | 任务 | 交付物 | 依赖 | 完成条件 |
|---|---|---|---|---|
| D201 | 核对本地迁移事实 | 当前唯一 head、受影响模型和命名冲突记录 | B01 | 只读确认后再确定新 revision |
| D202 | 设计物理 Schema | Conversation/Message/Task/Action/Event、Plan、Slot、Checkpoint、Call、Effect、Budget、Context、Answer 关系 | D201 | 满足租户隔离、版本、不可变 Ledger 和外键边界 |
| D203 | 新增隔离本地迁移 | 从实际唯一 head 延续的 Alembic revision | D202 | 只进入本地开发链，不应用 ECS |
| D204 | Repository 与原子状态转换 | CAS/State Version、合法转换、终态阻断、事件幂等 | D203 | 一次事务完成状态、事件和版本更新 |
| D205 | Slot Pending/Resume | 两阶段 Validation、Validation Ledger、Checkpoint consume | D204 | 成功填槽后原子恢复 `running`，失败时友好继续等待 |
| D206 | Effect/Budget/Cancel Fence | Reservation/Settlement、Idempotency、Cancellation/Late Result Fence | D204 | 重复或迟到结果不能产生 Effect 或提交回答 |
| D207 | Checkpoint Recovery | 安全边界保存、Lease/Fence、Replay/Reconcile 决策接口 | D204-D206 | 恢复复用已接受 Observation，不重启整个任务 |

数据库测试和任何 Agent 运行仍需用户明确授权；未授权阶段只完成代码与静态核对。

### B02 完成记录（2026-08-20）

- D201—D207 已完成；新增独立 `bid_pa_*` 持久化域，共 16 张表，未导入公共 Model Registry；
- 新增隔离开发迁移 `20260820_0109`，由 `20260818_0108` 延续，静态核对为当前唯一开发 head；迁移未应用到任何数据库；
- Repository 已覆盖调用方事务内的 CAS/State Version、不可变事件、计划版本、Action/Effect 幂等围栏、预算预留/结算、取消与迟到结果阻断；
- Slot 已覆盖 `waiting_input → validating_format → validating_business` 两阶段校验账本，成功填槽时原子消费 Checkpoint 并恢复 `running`，失败时保留友好重填信息；
- Checkpoint 已具备 Resume Token Hash、Lease、Fencing Token 和 `resume/reconcile/blocked` 恢复决策接口，不执行自动重放；
- 静态验收通过：15 个相关 Python 文件 AST 正常，16 张 ORM 表与迁移列/索引一致，SQLite/MySQL DDL 均可编译，唯一 head 为 `20260820_0109`；
- 未连接数据库、未应用迁移、未运行 Agent 测试，也未调用模型、真实资料、RAG、Embedding、Reranker、OCR/视觉、Tool 或外部 MCP。

## 5. B03 — Tool Gateway 与 RAG Adapter

| ID | 任务 | 交付物 | 依赖 | 完成条件 |
|---|---|---|---|---|
| D301 | Registry Snapshot | Definition/Input/Output/Safety/Binding Hash 与动态可见集合 | B01 | 每个模型决策轮次可冻结 Registry Snapshot |
| D302 | Router | 基于目标/状态的相关工具投影 | D301 | 只管相关性，无独立路由 LLM，不做权限判断 |
| D303 | Permission Guard | Visibility Guard 与 Execution Guard | D301 | Scope 由 Runtime 注入，模型不能填租户/权限字段扩权 |
| D304 | Gateway/Executor | 输入、业务、权限、安全、执行、输出、溯源校验；Local/MCP Binding | D302-D303 | 所有结果统一为 Canonical Tool Message |
| D305 | `documents_outline` Adapter | 受控文档结构读取 | D304 | 只接受不透明 Document Ref，结果不可引用 |
| D306 | `bid_document_search` Adapter | 招标资料 Candidate 检索 | D304 | Search 返回 `is_citable=false` Evidence Ref |
| D307 | `enterprise_knowledge_search` Adapter | 企业离线知识 Candidate 检索 | D304 | 严格企业/租户/Head 隔离，不复用招标索引混域 |
| D308 | `evidence_read` Adapter | 双知识域统一 Evidence Ref 解析与 Atom Read | D304 | 通过 Scope/Version/Hash/Locator 校验后才 `citable=true` |
| D309 | 结果限界与 Ledger | 大结果投影、错误语义、Call/Result 事实 | D304-D308 | 原始异常、权限信息和内部地址不回流模型或用户 |

本批次未授权时只写 Adapter 和 Fake/Disabled Binding，不调用真实 RAG、MCP 或资料。

### B03 完成记录（2026-08-20）

- D301—D309 已完成；Registry Snapshot 冻结 Definition/Input/Output/Binding/Safety Hash、最终动态可见集合和 Visible Tools Hash，模型投影仍只有 `name/description/input_schema`；
- Router 只依据当前 Task 状态、澄清状态和信息来源提示做相关性投影，不做权限判断、不调用独立路由模型，也不规定 Tool 顺序；
- Visibility Guard 与 Execution Guard 已分离；Runtime 注入的用户、租户、Task、Authorization Snapshot、文档和企业 Scope 不能被模型参数覆盖；
- Gateway 已按 Envelope、冻结白名单、Pydantic、业务/Scope、权限、安全、幂等 Ledger、Binding、Output 和 Provenance 的权威顺序校验，并统一生成安全 Canonical Tool Message；
- Local/MCP 共用 Executor；MCP 仅允许受控 Client 返回 Structured Content，服务地址、密钥、原始文本、日志和异常不进入模型合同；
- 四个 Canonical Adapter 与显式 Static Fixture Source 已实现；默认 Registry 的四个 Binding 仍全部为 `disabled`，Fake Local Registry 必须显式构建；
- Search Candidate 保持 `citable=false`，`evidence_read` 只有在 Scope、版本、内容 Hash、Locator 和 Provenance 全部通过后才接受 `citable=true` Atom；
- 0109 尚未应用，因此在同一隔离开发 revision 内补齐 Call Ledger 的 Provider Call Identity、Sequence、Registry/Visible/Authorization/State Version、Guard 决策和有界 Output 字段；唯一开发 head 仍为 `20260820_0109`；
- 静态验收通过：22 个相关 Python 文件 AST/导入正常，四工具模型投影不含 Output Schema，16 张 ORM 表与迁移保持 16 个索引、113 个命名约束一致，SQLite/MySQL DDL 均可编译；
- 未连接数据库、未应用迁移、未运行 Agent 测试，也未调用真实 Tool、RAG、MCP、模型、资料、Embedding、Reranker、OCR 或视觉链路。

## 6. B04 — Agent Runtime

| ID | 任务 | 交付物 | 依赖 | 完成条件 |
|---|---|---|---|---|
| D401 | IntentUnderstandingPort 实现 | 开放目标、信息需求、来源提示、澄清与复杂度输出 | B01-B03 | 无固定业务标签分类器 |
| D402 | Complexity Gate | Direct/Planned 选择与 Direct→Planned 升级 | D401 | 简单任务不生成正式 Plan，升级保留已接受证据 |
| D403 | PlannerPort 实现 | 结构化有限步滚动 Plan、校验、修订 | D402 | Planner 不执行 Tool，不成为持续 Agent |
| D404 | ContextAssembler | 六 Lane、保护区、L0-L4、Context Snapshot | B02-B03 | 关键内容不可无声截断，数据按不可信内容投影 |
| D405 | Memory 管理 | 四层逻辑 Memory、受控写入、Source 依赖、失效/遗忘 | D404 | Memory 不升级为事实或证据权威 |
| D406 | Provider Adapter | Function Calling、strict 能力、结构化输出与安全错误适配 | D401-D405 | 不预发完整 Tool Output Schema，Runtime 校验最终权威 |
| D407 | 动态 Action Loop | 回答、澄清、计划、工具、观察、重规划的开放循环 | D402-D406 | 所有认知活动留在 `running`，无固定调用顺序 |
| D408 | 六项 Runtime Guard | Budget、Loop、Effect、Cancel、Direct/Durable、Recovery | B02,D407 | 超限或无进展能安全收束，迟到结果不可提交 |

未授权时可以实现接口、纯逻辑和 Provider Stub，但不能发起任何实际模型或工具调用。

### B04-1 完成记录（2026-08-20）

- D401—D403 已完成：新增开放式 `IntentUnderstandingRuntime`、`DefaultComplexityGate` 和有限滚动 `PlannerRuntime`；
- Intent 不引入固定业务标签分类器；Complexity Gate 以结构化理解建议为主，并强制 Planned 不降级、Direct→Planned 保留全部已接受 Observation；
- Planner 是一次结构化能力调用，不执行 Tool；Plan ID、版本和 supersede 关系由 Runtime 生成，`tool_hint` 必须引用当前 Registry Snapshot 的模型可见工具；
- 只有显式的目标、范围、假设、子目标、证据冲突、路径不可用或用户重规划等实质原因才调用修订；内容不变时复用原 Plan；
- `MainAgentDecisionRuntime` 仅暴露三项独立能力，没有统一 `run` 或固定 pipeline。Intent/Planner 默认 Provider 均为 disabled，仅提供 Static Stub；未运行 Agent 测试，未调用模型、真实资料、Tool、RAG 或 MCP。

### B04-2 完成记录（2026-08-20）

- D404 基础已完成：六个 Context Lane、Provider/Context Profile、硬保护区与动态弹性区、Provider 预计数接口、L0—L4 Compression Receipt、五类 Assembly Result 和不可变 Context Snapshot 已落地；
- 当前 Policy/Output Contract、User Message、Task State、Visible Tool Contract、活动 Tool Pair 和显式 required resource 必须完整存在，不能通过字符截断换取 `ready`；所有数据 Lane 均保持 `untrusted_data`；
- D405 基础已完成：Working、Conversation、Project/Assessment、User 四层 Memory 使用类型化 Payload；写入经过 Task、Tenant/Scope、授权、内容策略、Source Version、Grounding 和用户确认 Guard；读取先 Scope 后相关性，支持版本、Supersede、Stale/Conflicted/Revoked、Tombstone 遗忘和 Source Head 失效；
- 首版只提供 Disabled/Static/InMemory Adapter，不新增 Memory 数据表或迁移，不增加 Memory Vector Index、独立摘要模型或模型可调用的 `write_memory` Tool；持久化 Adapter 留在真实集成前单独确认；
- 新增 `MainAgentRuntimeCapabilities` 仅聚合独立能力，没有 `run`、节点边或固定执行阶段；Context Snapshot 使用与既有 0109 本地 Schema 对齐的 36 字符确定性 UUID；
- 静态验收通过：28 个 Pure Agent Python 文件 AST 正常，完整包导入正常，12 个 Context/Memory Pydantic Schema 可生成，默认 Candidate Source、Token Counter、Snapshot Store、Memory Reader/Committer 均 fail-closed。未运行 Agent 测试，未连接数据库，未调用模型、资料、Tool、RAG 或 MCP。

### B04-3 完成记录（2026-08-20）

- D406 骨架已完成：新增冻结的 Provider 能力快照、Pydantic JSON Schema 兼容性投影、`disabled/preferred/required` strict 协商、Context/Registry 到 Provider Message/Function 合同的确定性渲染，以及 OpenAI-compatible 纯编解码边界；
- Function Definition 只包含 `name/description/parameters/strict`，不发送 Tool Output Schema；4/4 首批 Tool Input Schema 可兼容投影并开启 strict。Intent/TaskPlan Schema 在 preferred 模式下因可选或默认字段安全降级为非 strict，最终输出仍由对应 Pydantic 模型重新校验；
- Provider Tool Calls 先校验响应内唯一 ID、冻结白名单、JSON Object、重复 Key、大小和 Hash，再规范化为 Proposal；只桥接为 `ToolCallRequest`，不直接执行 Tool，Gateway 继续负责 Pydantic、业务、权限、安全、输出和溯源权威校验；
- 最终 Wire Payload 必须经过注入的匹配 Token Counter 再校验 Context Budget。Provider、Token Counter 和 Transport 默认均 disabled；只提供 Static Stub，没有 API Key、Endpoint、SDK 或网络客户端；
- Intent/Planner 已接入同一个通用结构化调用桥接，不新增分类器 Agent 或 Planner Agent；`MainAgentRuntimeCapabilities` 仍无统一 `run`、节点边或固定调用顺序；
- 静态验收通过：30 个 Pure Agent Python 文件 AST 正常，29 个子模块完整导入，Provider 核心 Schema、默认禁用态、Tool Output Schema 隐藏和无 `run` 均通过静态检查。未运行 Agent 测试，未连接数据库，未调用模型、资料、Tool、RAG 或 MCP；0109 迁移未修改、未应用。

### B04-4 完成记录（2026-08-20）

- D407 骨架已完成：新增 `DynamicActionLoopRuntime`，每次只处理一个已接受的 Main Agent Decision Action，形成一个受校验 Decision/Observation/下一 Action Reservation 后即归还控制；没有外层 `run`、`while`、节点边或固定阶段顺序；
- 模型可见普通结构化决定只允许 `plan/replan/request_information/answer`，Tool Batch 不能出现在该枚举中，只能由 Provider Function Calling 产生；Provider 必须显式声明同时支持 Tool Calls 与 Structured Output，否则 fail-closed；
- Plan/Replan 提案携带开放 `IntentUnderstanding`，并由同一 Runtime 的 `DefaultComplexityGate` 权威复核；重规划必须带实质 Revision Reason，Planner 仍是按需能力而非持续 Agent；
- Decision、Observation 和 Reservation Intent 均绑定 Task、State Version、Turn/Action Sequence、Context/Registry Snapshot 与内容 Hash；Decision Observation 被接受后只清除 In-flight Action 并加入 Observation Ref，不自动触发 Search、Read、Plan 或 Answer；
- Tool Proposal 在新的 Tool Action 被接受后才重绑定当前 State Version，并继续交 Canonical Gateway 做输入、业务、权限、安全、输出和溯源校验；本层不执行 Tool。请求信息只形成 Slot Action 意图，真正建立 PendingContext/Checkpoint 后才允许进入 `pending`；B04 的 Answer 通用占位已在 B05-1 被正式 Block Schema 替换；
- `MainAgentRuntimeCapabilities` 已加入独立 Action Loop 能力，默认 `DisabledMainAgentActionProvider`。静态验收通过：31 个 Python 文件 AST 正常、30 个子模块完整导入、10 个 Action Pydantic Schema 可生成，Main Agent Schema 可安全投影且 strict 不兼容时降级；未运行 Agent 测试，未连接数据库，未调用模型、资料、Tool、RAG 或 MCP，0109 迁移未修改、未应用。

### B04-5 完成记录（2026-08-20）

- D408 骨架已完成：新增冻结 `RuntimePolicyCeiling / RuntimeProfileSnapshot`，覆盖 Active Duration、Model/Tool Call、Input/Output Token、Cost、Replan、Answer Repair、No-progress、Retry、并行读取和 Model/Tool Timeout 上限；Profile 只能比 Policy 更严格，不能静默扩额；
- Budget Guard 使用冻结账户 Snapshot 生成逐资源 Reservation Directive；现有 Repository 新增 Guard-approved Action/Effect/Budget 同事务预留入口。实际 Usage 可按 Receipt 结算，缺可信 Usage 时按 Reservation 保守结算；取消时未启动 Effect 释放预留，已启动但未验证 Usage 保守结算，迟到可信成本可继续记账；
- Progress/Loop Guard 只使用 Action/Arguments/Binding/Scope/Registry/Expected Output 与 Observation/Signal Hash，不读取 Chain-of-Thought；支持相同语义重复、A→B→A 和连续无新增 Governed Information，首次要求结构化 Guard Observation，继续超限则要求安全收束；
- Effect Guard 使用跨 State Version 稳定的 Task/Action/Arguments/Binding/Scope Key，返回 reserve/reuse/await/reconcile/reject；Repository 对 Effect 合同重用、State Version、当前 In-flight Action 和 Fencing Token 做最终权威校验，迟到结果记录但不得进入 Context；
- Direct/Durable Guard 只依据冻结 Binding 的预计时长、Worker/Heartbeat/Restart/Remote Receipt/Outlive Request 要求和并行读取上限选择执行形态；两者都保持 Task `running`，Durable 不变成 Workflow 节点，`pending` 仍只等待合法用户 Slot；
- Recovery Guard 只接管非终态且 Lease 过期的 Task，重新校验 Profile、Registry、Authorization、Source Head、Cancellation、Checkpoint、Action 和 Effect；已持久化结果只消费不重跑，安全幂等且仍有 Retry 余额时才重试，不确定 Effect 必须 Reconcile，终态不重开；
- Main Agent 决策模型调用现在也先生成内部 `main_agent_decision` Reservation Intent 并经过同一 Guard，模型可见动作枚举仍只有 `plan/replan/request_information/answer`，Tool 仍只能走 Function Calling。`MainAgentRuntimeCapabilities` 已加入纯逻辑 Guard Suite，无统一 `run`、`while` 或固定业务顺序；
- 静态验收通过：32 个 Pure Agent Python 文件 AST 正常、31 个子模块完整导入、11 个 Action 和 26 个 Runtime Guard Pydantic Schema 可生成，默认 Provider/Action Provider/Tool 仍关闭。未运行 Agent 测试，未连接数据库，未调用模型、资料、Tool、RAG 或 MCP；0109 迁移未修改、未应用。

## 7. B05 — 回答、API 与交互界面

| ID | 任务 | 交付物 | 依赖 | 完成条件 |
|---|---|---|---|---|
| D501 | AnswerDraft 合同与 Guard（已完成） | Block Schema、支持矩阵、Grounding/Citation Integrity | B03-B04 | Material Statement 必须绑定合法 Grounding |
| D502 | CitationProjector/Renderer（已完成） | 权限感知 Citation 与自由自然语言输出 | D501 | 模型不能手写受信 Citation、内部 URL 或页码 |
| D503 | Answer Commit/Version（已完成） | 缓冲校验、不可变 Response、Supersede/Stale | B02,D502 | 无效 Draft 不发布，旧回答不静默改写 |
| D504 | Conversation API（已完成） | 开放消息、资料引用、Slot 补充、取消、状态读取 | D407,D503 | 全部位于独立默认关闭开关后 |
| D505 | 安全事件流（已完成） | 计划投影、进度、Pending 请求、回答、Citation、终态 | D504 | 不暴露 Chain-of-Thought、Prompt、原始 Arguments/异常 |
| D506 | 交互界面（已完成） | 实时问答、计划/进度、Slot 重填、引用查看、取消 | D505 | 不要求固定输入表单或固定报告输出 |
| D507 | 管理与诊断视图（已完成） | 脱敏 State/Call/Budget/Loop/Cancel/Recovery Trace | D505 | 仍保持隔离、本地、默认关闭 |

### B05-1 完成记录（2026-08-20）

- D501 骨架已完成：新增自由 `AnswerDraft` 的 Narrative/Statement/Limitation/Interaction Discriminated Union，结构化的是证据责任，不固定研判章节、用户输入或用户可见输出；
- Claim Type、Epistemic Status 与 Runtime 派生 Source Basis 分离；支持 Supported/Partial/Conflicted/Unknown、八类 Limitation、项目推断/建议前提、通用建议例外、计算输入/公式版本/结果回执和多 Conflict Group；
- 新增不可变 `GroundingSnapshot / GroundingRecord / QuoteBinding`，模型只选择当前 Context 中的 Grounding/Quote Ref，Schema 不提供 URL、页码、Locator 或内部 Source ID 的自由填写入口；搜索候选继续不可引用，只有 Runtime 升级并冻结的 Grounding 才可进入支持矩阵；
- `GroundingIntegrityGuard` 以纯逻辑校验 Task/Context/Authorization、Scope、Source Head/Version/Content/Locator/Projection Hash、Grounding Status、支持矩阵、Limitation Receipt、Conflict Group、精确 Quote Span 和 Slot Ref；不宣称可仅靠确定性规则证明自由文本语义蕴含，也未增加 Answer Writer/Verifier Agent；
- Main Agent `answer` 已从 B04 通用字典替换为强类型 `AnswerDraft`；Action Loop 只允许与当前已接受 Answer Action、Context 和 Draft Hash 完整匹配且已通过 Guard 的 Draft 形成 `answer_draft` Observation。`MainAgentRuntimeCapabilities` 新增独立纯逻辑 Answer Guard，仍无统一 `run`、`while`、固定阶段链或 Workflow DAG；
- Provider 投影仅剥离 Pydantic 判别联合产生的非权威 OpenAPI `discriminator` 注解，保留 `oneOf + block_type const`；原始 Pydantic Discriminated Union 仍是 Runtime 最终权威。Main Agent Schema 可兼容 Provider JSON Schema 子集，可选/默认字段继续按 preferred 模式安全降级为非 strict；
- 静态验收通过：34 个 Pure Agent Python 文件 AST 正常、33 个子模块完整导入、4 个关键 Answer/Main Agent Pydantic JSON Schema 可生成，Main Agent Provider Schema 兼容投影通过。未运行 Agent 测试，未连接数据库，未调用模型、真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路；0109 迁移未修改、未应用。

### B05-2 完成记录（2026-08-20）

- D502 骨架已完成：新增 Runtime-only `CitationAuthoritySnapshot`，内部权限引用与用户可见字段分离；仅允许安全标题、来源类型、Locator、版本标签和不透明受控访问引用进入 Citation 投影，不暴露对象键、文件路径、数据库 ID、MCP 地址或未授权来源；
- `CitationProjector` 仅接受 B05-1 已通过 Guard 的 Draft，并按当前 Task/Context/Authorization、Scope、Source Head/Version/Content/Locator Hash 重新校验；任一引用失败即拒绝整个 Bundle，不产生部分可信的 Citation；
- 精确引文必须来自冻结的 EXACT Context Quote Span，且引文原文必须实际出现在对应 Statement 中；冲突 Statement 必须投影至少两个 Conflict Group；
- 模型输出中的 URL、Markdown 链接、页码、Citation 编号或内部 Source/Grounding Ref 会被拒绝；用户可见 Citation 编号、显示字段和来源列表均由 Runtime 确定性生成；
- 新增通用 `AnswerBlockRenderer`，仅渲染 Narrative/Statement/Limitation/Interaction Block、安全状态标签、行内 Citation 与受控来源列表，不引入固定研判报告结构；输出为待提交的 `RenderedAnswerCandidate`，不负责发布、SSE、Task 状态迁移或回答版本；
- 静态验收通过：36 个 Pure Agent Python 文件 AST 正常、35 个子模块完整导入、4 个关键 Citation/Render Pydantic JSON Schema 可生成；用户可见 Rendered Schema 不含 Source/Grounding Ref、Locator/Content Hash、URL、对象键或文件路径，且未引入统一 `run` 或固定循环。未运行 Agent 测试，未连接数据库，未调用模型、真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路；0109 迁移未修改、未应用。

### B05-3 完成记录（2026-08-20）

- D503 骨架已完成：新增 `PublishedAnswerMessage`、不可变 `CommittedResponseArtifact`、`ResponseVersionHead`、`ResponseCommitDecision`、Stale Intent，以及带前序 Hash 的 `ResponseLifecycleEvent / ResponsePersistenceEnvelope`；用户消息与内部审计 Artifact 分离；
- `AnswerCommitRuntime` 只接受已被当前 Task 接受的 AnswerDraft Observation，要求 Task 仍为 `running`、没有 In-flight Action，且当前 State Version 恰为回答版本加一；再次校验 Context、Draft Validation、Citation Bundle、Authorization 和 Rendered Hash 后才生成 `completion.accepted` 事件；
- `completion.accepted` 与取消共享 Task State Version/CAS：取消先提交则回答失去 Fence，回答先提交则 Task 进入 `completed`，后续取消不能改写已发送内容；
- B02 Repository 新增最小 Context Snapshot 持久化、Response Head 读取、回答原子提交和 Stale 变更；同一调用者事务内锁定 Conversation/Task，追加安全 Assistant Message、Response Artifact、可选旧版本 Supersede 事件与 Task 完成事件，幂等重放不重复追加消息；
- 修正或来源更新必须创建新 Response Version 并显式引用被替代 Artifact；旧 Message、正文、Block 和 Citation 不原地改写。Stale/Supersede 只追加哈希链式生命周期事件并更新当前状态投影，不把历史回答重新当作当前事实；
- 复用现有 16 张 `bid_pa_*` 表和 `bid_pa_responses.draft_json` 保存“不可变 Artifact + 生命周期 Envelope”，未修改或新增迁移。静态验收通过：38 个 Pure Agent Python 文件 AST 正常、37 个子模块完整导入、7 个关键 Commit/Version Pydantic JSON Schema 可生成；用户消息 Schema 不含 Task/Context/Draft/Grounding/Authorization/Source Ref、URL、对象键或文件路径，且未引入统一 `run` 或固定循环。未运行 Agent 测试，未连接数据库，未调用模型、真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路；0109 迁移未修改、未应用。

### B05-4 完成记录（2026-08-20）

- D504 骨架已完成：新增独立 `/api/v1/bid-assessment-pure-agent` 命名空间，提供 Conversation 创建/状态、公开消息分页、开放消息提交、Task 状态、Slot Response 和 Cancel 共 7 个 Operation；所有写入口强制 `Idempotency-Key`；
- 普通消息保持自然语言与资料引用开放，只根据 Conversation 当前是否存在唯一 `running/pending` Task 记录为新 Task Trigger 或 Steering Candidate；不在 API 层做关键词意图分类、Goal 替换判断或固定步骤编排，后续仍由 Main Agent 理解；
- 资料引用首版只接受对话绑定 Assessment 及其当前 Manifest 内的 Document Version，不接收路径、对象键、URL 或客户端自报权限；Conversation 仅所有者可见，Assessment 沿用现有所有者/Admin 可见性；
- Slot Response 复用 B02 的两阶段验证账本：Pydantic 格式校验失败或业务校验失败均回到 `pending/waiting_input` 并返回安全 Guidance；成功时校验 Resume Token、Checkpoint、Effect Fence 和 State Version 后恢复原 Task，不新建 Task；未注册具体 Slot Validator 时安全返回“校验能力不可用”；
- Cancel 新增请求 State Version 校验，并与 Answer Commit 统一为 Conversation→Task 锁顺序；Pending 取消时原子失效开放 Checkpoint，迟到结果继续由既有 Fence 阻断；
- `main.py` 仅在独立开关显式启用时动态导入 Router，因此默认关闭时既无 Conversation 路由，也不向公共 SQLAlchemy Metadata 注册 16 张 `bid_pa_*` 表。B05-4 不调度 Worker，响应明确为 `not_dispatched`，SSE/安全事件流属于 B05-5；
- 静态验收通过：42 个相关 Python 文件 AST 正常、40 个 Pure Agent 子模块/API 完整导入、8 个 Conversation Pydantic Schema 和 7 个 OpenAPI Operation 可生成；4 个写 Operation 均要求幂等键，公开响应 Schema 不含 Tenant/Owner/Authorization/Resume Token/内部 Target/Object Key/路径/URL。未运行 Agent 测试，未连接数据库，未调用模型、真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路；0109 迁移未修改、未应用。

### B05-5 完成记录（2026-08-20）

- D505 骨架已完成：在 Task 状态读取旁新增安全事件分页与 SSE 两个 Operation；分页使用 `after_version`，SSE 使用 `Last-Event-ID`，都以 Task `state_version` 为唯一连续游标，断线恢复不依赖时间排序或内部 Event 主键；
- Task 创建安全投影为 version 1，现有 `bid_pa_events` 的每次 CAS Transition 对应后续唯一版本；投影读取检测版本超前、跨 Task Cursor、Ledger 缺口和内部合同绑定漂移并 fail-closed，未增加公共事件表、Outbox、Worker 或迁移；
- 首版冻结 11 个用户可见事件类型：`task.started`、`plan.updated`、`progress.updated`、`input.required/validating/rejected/accepted`、`answer.preparing/completed`、`task.failed/cancelled`；完整回答事件直接携带 B05-3 已验证的不可变 Answer Block 和 Citation；
- Planner 事件只发布通过完整 `PlanRevision` 校验后的 `user_projection.summary` 与显式 visible Step 的 ID/Title，不发布 Description、Tool Hint、Output Schema、Next Decision、Replan Reason 或模型原始 JSON；
- Action 事件只按允许集合映射为 Understanding/Planning/Retrieving/Input/Answer/Continuing 等通用进度，不读取或发布 Arguments、Tool Name、Query Expansion、Scope、Effect、Context、Provider 或内部 Result；失败事件只返回固定安全文案，不返回 Error Ref/Code、异常或 Stack Trace；
- Pending 事件只发布 Slot Request Message、阶段和安全的 Pydantic/业务校验 Guidance；Resume Token、Checkpoint、Validation Attempt 和 Effect Fence 保持内部；SSE Keepalive/Poll 只属于传输层，不驱动 Main Agent 或形成 Workflow；
- 静态验收通过：44 个相关 Python 文件 AST 正常、42 个 Pure Agent 子模块/API 完整导入、7 个关键 Event Pydantic Schema 和 9 个 OpenAPI Operation 可生成；公开 Event Schema 不含 Chain-of-Thought、Prompt、原始 Arguments、Tool Name、Query Expansion、Context/Authorization/Resume/Effect/Error/Provider、对象键、路径或 URL。默认关闭时 Router 和 16 张 `bid_pa_*` 表仍不注册。未运行 Agent 测试，未连接数据库，未启动 SSE 服务，也未调用模型、真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路；0109 迁移未修改、未应用。

### B05-6 完成记录（2026-08-20）

- D506 骨架已完成：新增独立 Vite 页面 `/admin/bid-assessment-pure-agent`，入口同时受 `FEATURE_VITE_FRONTEND` 与 `FEATURE_BID_ASSESSMENT_PURE_AGENT` 保护；RBAC 模块在专用开关关闭时保持 `pending`，不会出现在可用导航；
- 对话输入保持开放自然语言，可按需附加服务端受控的 Assessment/Document Version 引用，不要求固定表单、固定研判路径或固定报告章节；对话 Ref 写入同源 URL，可刷新恢复已发布消息与最新 Task；
- 页面使用带 Bearer 鉴权的 Fetch Stream 消费 B05-5 SSE，按公开 `Last-Event-ID` 在 Session Storage 恢复；只显示安全 Task 状态、按需 Plan 用户投影、白名单进度、Pending/校验 Guidance、不可变 Answer Block/Citation 和安全终态，不显示思维链、Prompt、Tool Arguments、Query、内部异常或控制数据；
- Pending 区允许普通文本或 JSON 候选，格式/业务校验问题由服务端 Pydantic/Validator 合同返回并原位引导重填；Resume Token 不进入 DOM、日志或 Web Storage，仅提供同页内存绑定适配点，未由后续 Runtime 调度器绑定时安全禁用提交；
- Message、Slot 和 Cancel 继续发送幂等键；Slot/Cancel 使用当前 Task State Version。取消不会由页面驱动其他 Agent 动作，SSE 也只负责传输；回答按通用 Block 渲染为纯文本，不使用 `v-html`，Citation 只显示 Runtime 已投影的安全文本和受控访问提示；
- 静态验收通过：新增 API 模块 `node --check`、2 个相关 Python 文件 AST、前后端合同文本护栏均通过；Vite production build 成功，2238 modules transformed。未运行 Agent 测试，未启动服务或 SSE，未连接数据库，未调用模型、真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路；0109 迁移未修改、未应用。

### B05-7 完成记录（2026-08-20）

- D507 骨架已完成：新增管理员专用 `diagnostic_contracts.py` 与只读 `PureAgentDiagnosticProjector`，只从现有 16 张 `bid_pa_*` 表投影已经持久化的控制面事实，不创建新表、不修改 `20260820_0109`；
- 新增两条 `GET /api/v1/bid-assessment-pure-agent/admin/diagnostics/tasks*` 路由，提供 Task 分页和单 Task 脱敏诊断快照；两条路由均强制 `require_admin`，与现有 Pure Agent 一同受默认关闭开关保护，没有重试、恢复、取消、调度或其他写入口；
- State Trace 只展示版本、五态、通用活动和 Action 序号；Call Trace 只展示模型/工具类别、安全操作名、状态、Guard 汇总、Token/成本/耗时与类型化错误分类；不返回 Event/Call/Action 内部主键、原始 Payload、Input/Output Hash、Provider Binding/Receipt 或权限快照；
- Budget Trace 只展示资源账户、上限/预留/实际/剩余和脱敏流水；Loop Trace 首版只报告可由 `action_type + arguments_hash` 与 `result_hash` 证明的精确重复信号，并显式标记 `semantic_progress_decision_available=false`，不把未持久化的语义无进展 Guard 结论伪造出来；
- Cancel Trace 只展示 Fence 是否存在、版本、操作者类型、原因是否留存、已取消 Effect 与迟到隔离数量，不返回取消原因正文或操作者引用；Recovery Trace 只展示 Checkpoint 状态、挂起版本、Replay Policy、Lease 状态、领取次数和安全处置，不返回 Resume Token Hash、Lease Owner、Context/Action/Effect Ref；缺失权威关系通过 `integrity_warnings` 明示；
- 新增独立管理员 Vite 页面 `/admin/bid-assessment-pure-agent-diagnostics`，提供 Task 状态总览、筛选、State/Call/Budget/Loop/Cancel/Recovery 和 Redaction 标签页；页面只读且不会从 Trace 推导或驱动下一 Agent Action，不形成固定 Workflow；
- 静态验收通过：4 个相关 Python 文件 AST 正常，2 个诊断 Pydantic Schema 可生成，FastAPI OpenAPI 仅包含 2 个 GET Operation 且均绑定 `require_admin`，公开 Schema 敏感属性名检查通过，诊断 API `node --check` 通过；Vite production build 成功，2241 modules transformed。未运行 Agent 测试，未启动服务，未连接数据库，也未调用模型、真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路；0109 迁移未修改、未应用。

## 8. B06 — 授权后的验证与校准

以下每类运行开始前都必须重新取得用户明确授权，不能因代码已完成而默认执行：

| ID | 验证域 | 最小目标 | 当前状态 |
|---|---|---|---|
| V601 | Pydantic/状态/Slot/Checkpoint/Guard Agent 测试 | 合同、转换、恢复和 Fence 正确性 | 已完成（20 passed） |
| V602 | Tool/权限/Adapter 测试 | 动态白名单、双 Guard、Scope、错误语义 | 已完成（22 passed） |
| V603 | Planner/Intent 模型评测 | Direct/Planned、澄清、计划可执行性 | 已完成（合同 11 passed；模型 8/8） |
| V604 | 离线 RAG 与召回评测 | 解析、切块、BM25/向量/RRF、Atom 升级 | 已完成（新增 8 passed；合计 42 passed；四项指标 1.0） |
| V605 | Reranker/Embedding/OCR/视觉评测 | 条件启用价值和安全降级 | 已完成（V605-1、V605-2） |
| V606 | 回答与引用评测 | Grounding、Unknown、Conflict、Citation 完整性 | 已完成（合同 11 passed；模型 4/4） |
| V607 | 真实 PDF 业务 Run | 端到端自主研判和连续问答 | 已完成（Silver Recall@8 0.88/0.88；对话 4/4；专项 12 passed） |
| V608 | 恢复/取消/循环/预算评测 | T13 六项护栏及参数校准 | 已完成（41/41；专项 11 passed；相邻 31 passed） |

评测结果用于调整 Runtime Profile、Prompt、Description 和 RAG 参数；不得反向把高频路径固化为 Workflow。

V601 已在用户明确授权后完成：新增三份隔离专项测试，覆盖闭合 Pydantic 合同、五态合法转换、Event/Effect 防重、两阶段 Slot 格式/业务校验及安全重填、Continuation Checkpoint、Budget/Loop/Effect/Cancel/Direct-Durable/Recovery 六项 Guard，以及进程内 SQLite 上的 Checkpoint 消费、恢复 fencing token、Pending 取消失效和迟到结果隔离。测试发现并修复 `bid_pa_slot_validations` 可空 JSON 将 Python `None` 持久化为 JSON `null`、与 SQL `IS NULL` 约束冲突的问题；模型改为 `JSON(none_as_null=True)`，未修改或应用 0109。最终隔离专项 `20 passed / 0 failed`（2.08 秒）；当前 Anaconda 环境缺少公共 FastAPI `conftest.py` 所需的 `slowapi`，因此使用 `--noconftest` 和关闭插件自动加载的纯 V601 运行方式。未启动服务、Worker 或模型，未连接现有本地业务库、ECS 或任何外部依赖，也未调用真实资料、Tool、RAG、MCP、Embedding、Reranker、OCR 或视觉链路。

V602 已在用户明确授权后完成：新增两份隔离专项测试，覆盖四工具默认禁用合同、模型可见最小合同、Registry/Visible Set 稳定 Hash、Intent Source Hint 动态 Router、Visibility/Execution 双 Guard、Authority/Document/Enterprise/Evidence Scope、disabled/local/MCP Binding 和 mutating/egress/approval Safety 白名单；同时以静态内存源贯通四个 Adapter、Canonical Gateway、Provenance、Canonical Tool Message、InMemory Ledger 幂等重放、Provider Call Identity 防重、Deadline，以及 unavailable/internal/contract violation 的安全错误语义。最终专项 `22 passed / 0 failed`（1.31 秒），未发现需要修改生产 Tool 代码的新缺陷。测试中的 Local/MCP 均为进程内假 Handler/Client，未连接或调用真实 Tool、RAG、MCP、模型、资料、数据库或外部服务，未启动应用/Worker，也未修改或应用 0109。

V603 已在用户明确授权后完成：隔离专项覆盖 Intent 输出二次校验、Direct/Planned 与澄清门、跨来源/多信息需求升级、Planned 不降级和 Observation 保留、有限可执行 Plan、Registry `tool_hint`、无实质原因不重规划、版本化重规划，以及 OpenAI-compatible 结构化 Provider Bridge 的 `tool_choice=none`、非 strict 降级和 Runtime 最终权威校验，结果 `11 passed / 0 failed`。DeepSeek V4 Flash 纯合成模型评测首先发现评测器遗漏 `thinking=disabled` 与非流式 JSON 参数，修正后完整首轮 Planner `2/2`、Intent `4/6`；随后明确“available_context 已授权绑定，不应因尚未检索正文重复向用户索要资料”，Intent v2 达到 `5/6`。最后按“知识问答只选择必要信息来源”原则，将 I04 的 `existing_evidence` 从强制来源改为可选，并做单例复验通过。最终接受结果为 Intent `6/6`、Planner `2/2`、总体 `8/8`。模型校准阶段已计量响应共 `15` 次、`15,906` Token（输入 `11,147`、输出 `4,759`），另有首次非 JSON 请求首错停止且未取得 Usage；不含业务数据、真实资料、数据库、RAG、Tool 或 MCP。0109 未修改、未应用。

V604 已在用户明确授权后完成：新增版本化纯合成离线 RAG 数据集、确定性评测器和 8 项专项测试，覆盖结构化文本块输入、Parent/Child/Atom 层级、80-token 有界可追溯重叠、BM25F、注入式预计算语义排名、rank-only 加权 RRF、招标/企业双域 Scope、撤销内容召回前过滤、安全空结果、权限拒绝、Search Child 非引用候选和 Evidence Read Atom 引用升级。8 个案例的 Recall@3、MRR、安全空结果精度、Atom 升级成功率均为 `1.0`；新增专项 `8 passed`，连同切块、BM25F、RRF、Query Optimizer 相邻确定性回归共 `42 passed / 0 failed`（1.83 秒）。未读取真实 PDF，未加载 Embedding/Reranker，未调用 OCR/视觉、模型、外部 MCP、数据库或 ECS；0109 未修改、未应用。

V605-1 已在用户明确授权后完成：新增版本化纯合成真实模型数据集、离线评测器和 6 项合同测试，固定加载 `bce-embedding-base_v1@9c0d82af...` 与 `bce-reranker-base_v1@eb7650fc...`，冻结环境为 `numpy 1.26.4 / torch 2.6.0+cpu / transformers 4.44.2 / sentence-transformers 3.1.0`。Embedding 在招标/企业双域 6 个 Child、4 个 Query 上 Recall@3=`1.0`、MRR=`0.875`，补回 1 个 BM25F 漏召回；向量 768 维、归一化、Hash、幂等构建和查询重放均通过，冷构建约 `4.78s`、热查询 P95 约 `0.033s`。Reranker 在 3 组冻结候选上将 Top-K Recall 从 `0.333333` 提升到 `1.0`，2 例提升、0 退化、共 4 次有界 promotion，零 promotion 案例保持有序结果逐项不变，热 Case P95 约 `0.081s`。模型不可用时均显式标记 `degraded` 并保留真实冻结基线，合同错误不静默降级；V605-1 合同与相邻确定性回归 `39 passed / 0 failed`，最终 V604+V605-1 联合及相邻矩阵 `54 passed / 0 failed`。这些是极小纯合成 CPU 指标，只证明“真实模型可用且具备条件启用价值”，不代表真实业务质量或生产容量；Embedding 使用进程内 Exact Cosine，`production_milvus_executed=false`。未读取真实 PDF，未执行 OCR/视觉、生成模型、外部 MCP、数据库或 ECS；0109 未修改、未应用，所有开关仍默认关闭。

V605-2 已在用户明确授权后完成：新增版本化纯合成 OCR/视觉数据集、可复跑评测器和 10 项合同测试，固定使用本地 `PP-OCRv5_mobile_det`、`PP-OCRv5_mobile_rec`、PaddleOCR `3.7.0` 与 OpenCV `4.10.0`，运行时生成清晰文本页、3×3 表格页、低对比/模糊页和空白页。清晰文本与表格片段召回、坐标完整性、表格网格准确率、逐单元格片段召回、空白页零误报和 Review Gate 准确率均为 `1.0`，清晰文本平均置信度约 `0.999514`；复跑 Case P95 约 `2.764s`。低质页只识别出残缺内容且模型局部置信度仍高，组合图像对比度/清晰度门正确将其标记为 `review_required`，证明不能只信 OCR 自报置信度。Native 文本优先、OCR Provider 不可用显式 degraded 且不伪造内容、OCR 仅为不可引用 Observation Candidate 并要求后续 Evidence Read 的合同均通过。V605-2 专项 `10 passed / 0 failed`，V604/V605-1 及相邻检索矩阵复跑 `56 passed / 0 failed`。评测仅使用临时合成 PNG 和本地 CPU 模型/确定性表格线算法，未读取真实 PDF，未调用生成式视觉模型、外部网络/MCP、数据库、Milvus 或 ECS；0109 未修改、未应用，所有开关仍默认关闭。

V606 已在用户明确授权后完成：新增版本化纯合成 Answer/Citation 数据集、可复跑评测器和 11 项合同测试，覆盖 Supported、Unknown、Conflicted、Partial 四种认识状态。4/4 Reference Draft 完整贯通现有 `GroundingIntegrityGuard → CitationProjector → AnswerBlockRenderer`；5/5 负向案例正确阻断 Unknown 过度断言、Citation Authority 缺失、模型手写 `[1]`、Stale Source 和只引用冲突一侧。DeepSeek V4 Flash 在 4 个纯合成回答案例中首轮 `4/4` 通过：Supported 绑定两项证据并由 Runtime 生成 2 条引用，Unknown 绑定检索空结果 Receipt、保持 0 引用并披露未知，Conflict 同时绑定两个冲突组并投影 2 条来源，Partial 引用现有企业资料并披露证书有效期缺口；全部 Draft 通过 Pydantic、Grounding、Citation 完整性及安全渲染。模型共 4 次调用、输入 `9,085` Token、输出 `1,159` Token、合计 `10,244` Token。V606 专项 `11 passed / 0 failed`，与 V603 结构化 Provider Bridge 相邻回归 `22 passed / 0 failed`。未读取真实 PDF，未执行 RAG、Tool、外部 MCP、数据库、Milvus 或 ECS；0109 未修改、未应用，所有开关仍默认关闭。

V607 已在用户明确授权真实 PDF、冻结企业基线和必要检索片段可发送官方 DeepSeek API 后完成：固定读取 307 页、`4,063,429` 字节香港中心 PDF（SHA-256 `3e2d7a42...d1a0ad8f`），Native 解析得到 5,275 Block、1,244 Retrieval Child、5,276 Evidence Atom；使用本地 BCE Embedding/Reranker 和既有 BM25F、多查询、RRF/RQ2-B 检索，25 个 Silver Case 的 Page Recall@8 与 Phrase Recall@8 均为 `0.88`，通过 `0.84/0.80` 门槛。4 轮连续业务对话全部通过：T01 自主升级 Planned，T02/T03 Direct，T04 由 Complexity Gate 自主选择 Planned；实际调用 `bid_document_search / enterprise_knowledge_search / evidence_read` 三项只读 Function Calling Tool 共 25 次，所有 Search Child 均经 Evidence Read 升级后才可引用。DeepSeek 共 24 次调用，输入 `166,867`、输出 `12,605`、合计 `179,472` Token；Grounding、Citation、连续上下文、对话、Silver 双召回和 Unknown Safety 七个总门全部通过，T03 对未知截止时间保留 Unknown 且未知陈述零引用。真实 Run 用时约 `299.17s`，最终产物 SHA-256 `a53b0175...f2475dba`，V607 专项 `12 passed / 0 failed`。评测期间仅发送当前任务必要条款、候选片段和证据片段，未发送整份 PDF、文件路径或密钥；未重解析企业原始文件，未使用 OCR、外部 MCP、数据库、Milvus 或 ECS，0109 未修改、未应用，所有开关仍默认关闭。

V608 已在用户明确授权后完成：新增版本化 41 场景六护栏数据集、确定性评测器和 11 项专项测试，覆盖 Budget 边界预留/保守结算/Profile Ceiling，重复语义、相同 Observation 与 A→B→A 循环，Effect Reserve/Reuse/Await/Reconcile/Reject，取消前后 CAS 与迟到结果隔离，Direct/Durable/并行读取边界，以及终态、Lease、Pending、已接受 Observation、已持久化结果、Uncertain、Retry、Registry/Authorization 变化和未启动 Effect 的 Recovery。结果为 Budget `6/6`、Loop `6/6`、Effect `6/6`、Cancellation `8/8`、Direct/Durable `4/4`、Recovery `11/11`，总计 `41/41`；4 个进程内 SQLite 原子持久化竞态 `4/4`，八个总门全部通过。预算超分配、重复 Effect Commit、取消/过期结果接纳、危险恢复重放和 Direct/Durable 误分类均为 `0`；`max_no_progress_actions=2` 校准为第 2 次无进展 Warning、第 3 次 Stop；`max_retry_attempts=2` 仅允许计数 `<2` 的安全幂等重试。V608 专项 `11 passed / 0 failed`，与 V601 状态/Slot/Checkpoint/Guard 相邻回归 `31 passed / 0 failed`；最终产物 SHA-256 `4640a7c9...1fda56e`。未调用模型、业务资料、Embedding/Reranker、OCR/视觉或外部 MCP，仅使用事务内存 SQLite；未连接外部数据库、Milvus 或 ECS，0109 未修改、未应用，所有开关仍默认关闭。

## 9. C01 — Runtime Controller 与 Conversation API 本地集成

C01 已完成代码装配和用户授权的本地隔离专项测试：

- 新增事件驱动 `PureAgentRuntimeController`，每个 Pulse 只预约或完成一个已接受 Action；下一 Action 继续由 Main Agent 决定，没有统一业务 `run()`、固定节点链或 DAG；
- 现有 `DynamicActionLoopRuntime` 已通过 Driver 接入 Controller；Runtime Guard Suite 仍是唯一 Action Admission 权威，非 Decision Action 通过显式 Capability Handler Registry 注入；
- Action Intent、Context/Registry 冻结边界和最小 Driver Payload 使用版本化 Envelope 持久化到现有 Action JSON 字段，跨事务不依赖进程内对象；未新增或修改迁移；
- 每个有成本 Action 继续执行 Effect Fence 与 Budget Reservation；Action 完成时按可信 Usage 结算，缺失或未验证 Usage 按预约上限保守结算；
- 新增本地 `LocalRuntimePulseDispatcher`，每个 Action 使用新事务，并只依据 Controller 的 `continue/wait/stop` 动态指令推进；没有 Worker、队列或 Workflow 编排引擎；
- Conversation API 在消息提交和 Slot 成功恢复事务提交后发送安全 Wakeup；Task 公开 `dispatch_status` 收束为 `disabled/ready/active/waiting_input/finished`；
- Pending 恢复改为服务端 HMAC Continuation Token：浏览器不读取、不保存、不回传令牌，API 可从持久化 Checkpoint 重建；旧显式令牌仅保留为隔离兼容输入；
- 新增独立 `FEATURE_BID_ASSESSMENT_PURE_AGENT_RUNTIME=false` 和空值 Continuation Secret；API 可见开关、执行权限、Dispatcher 装配和 Secret 四门缺一均不会调度；
- 前端已移除 B05 的进程内 Continuation Token 占位桥，并准确显示 Runtime disabled 状态；
- 静态检查已通过：8 个相关 Python 文件 AST/导入、6 个新增/变更 Pydantic Schema、Conversation OpenAPI 8 个路径和前端 API JavaScript 语法；
- C01 专项 `6 passed / 0 failed`：验证 Continuation Token 的 Checkpoint 绑定与缺省禁用、Slot 请求不再要求浏览器令牌、单 Action 在新事务中预约/执行/观察/挂起、Budget 缺失 Usage 时按预约上限保守结算、服务端从持久化 Checkpoint 重建令牌并恢复原挂起点、`disabled/ready/active/waiting_input/finished` 状态投影、四重 Runtime 门控，以及 HTTP 用户消息提交成功后才执行 Background Wakeup；
- 测试仅使用内存 SQLite、FastAPI TestClient 和静态输入，未调用模型、真实资料、RAG、Tool、外部 MCP、外部数据库、Milvus 或 ECS，未运行迁移，0109 未修改、未应用。

## 10. C02 — 动态能力装配

C02-1 已完成，未引入任何业务 Action 顺序：

- 新增 `runtime_composition.py`，由唯一 Composition Root 组装 Repository、Boundary Provider、Admission Provider、Dynamic Action Loop、Capability Executor、Controller 与本地 Dispatcher；
- Capability Handler Registry 只允许 `plan/replan/tool_call_batch/request_information/answer`，明确禁止注册 `main_agent_decision`，不承担意图识别、工具路由或流程编排；
- Registry 在 Bootstrap 阶段一次性注册并冻结，重复、未知、缺项或冻结后修改均拒绝；Runtime Handler 由事务级 Factory 创建，不把 Session/Repository 跨事务共享；
- Root 同时检查 API 开关、Runtime 开关、Continuation Secret、Session Factory、三类组件 Factory 与完整 Handler Registry；不满足时返回 `disabled/incomplete` 和禁用 Dispatcher；
- `compose_and_install()` 是唯一显式安装边界；禁用或不完整装配也会覆盖为禁用 Dispatcher，避免残留旧执行权限；
- 静态导入、默认关闭、AST 与 `git diff --check` 已通过；未运行 Agent 测试，未调用模型、RAG、Tool、真实资料、外部 MCP、外部数据库或 ECS，0109 未修改、未应用。

C02-2 已完成，Boundary 仍不决定下一 Action：

- 新增 `main_agent_boundary.py`，定义逻辑 `MainAgentTurn`、授权 `MainAgentBoundaryAssemblyInputs`、Inputs Provider Port、持久化 Turn Resolver 与 Decision Boundary Provider；
- Repository 新增有界 Turn 查询，只接受内部 Schema 与内容 Hash 完整、且通过 Trigger Message、`target_task_ref` 或 `task_ref` 明确绑定当前 Task 的用户消息；
- `USER_MESSAGE / STEERING_MESSAGE / SLOT_RESUMED` 分别绑定对应输入，`ACTION_CONTINUATION / RECOVERY` 复用该 Task 最新逻辑用户 Turn，不靠关键词或意图分类；
- Inputs Provider 负责提供已经授权的 Policy、Prompt、Model/Context Profile、Registry Snapshot、必要资源与 Checkpoint Ref；Boundary 不自行扩大权限或可见 Tool；
- Boundary 调用现有 ContextAssembler，只有 Snapshot 与 running Task 的版本一致、Consumer 为 `main_agent`、状态 model-ready 且包含当前用户消息时才返回；Driver 仍负责在 Action 预约事务中持久化 Snapshot；
- AST、公共导入和 diff 静态检查通过；未运行 Agent 测试，未调用模型、RAG、Tool、真实资料、外部 MCP、外部数据库或 ECS，0109 未修改、未应用。

C02-3 已完成，五类 Handler 之间仍没有运行顺序：

- 新增 `capability_executors.py`，实现 `PlanCapabilityExecutor`、`ReplanCapabilityExecutor`、`ToolCallBatchCapabilityExecutor`、`RequestInformationCapabilityExecutor` 和 `AnswerCapabilityExecutor`；每个 Executor 都只验证并执行一种已预约 Action；
- Plan/Replan 通过授权 Boundary 调用现有 Planner，持久化 Context 与 Plan Revision；初次计划升级为 planned，Replan 只允许用新 Revision 原子替换滚动 Plan head，之后继续交回 Main Agent；
- Tool Call Batch 将冻结的 Provider proposals 重绑到当前 Action fence，经授权 Registry、Execution Context、Policy 与 Deadline 逐项进入现有 Tool Gateway；结果可以 succeeded/degraded/no_result，但不预设下一 Tool 或 Answer；
- Request Information 将信息需求固化为 Slot 请求观察，并按 Action/Intent 生成确定性 Slot/Checkpoint，Controller 原子挂起为 pending；后续仍从原 Checkpoint 恢复；
- Answer 在执行时取得最新授权 Context/Grounding/Citation Authority，只重绑 Draft 的 Context ref 与 state version，不改变模型正文；Guard、Citation Projector、Renderer 全部通过才生成 Answer Observation，观察被接受后再由 Answer Commit 形成 completed，安全拒绝则回到 Main Agent；
- `CapabilityExecutorFactories` 生成五项完整 Handler Factory 映射，供 C02-1 Registry 在 C02-4 显式 Bootstrap 时冻结；它不安装 Dispatcher，也不声明 Action 顺序；
- AST、导入、7 个新增/相关 Pydantic Schema、公共导出和 diff 静态检查通过；未运行 Agent 测试，未调用模型、RAG、Tool、真实资料、外部 MCP、外部数据库或 ECS，0109 未修改、未应用。

C02-4 已完成，Bootstrap 是安装边界而不是运行工作流：

- 新增 `local_bootstrap.py`，定义显式 Activation、Isolation Decision、可审计 Bootstrap Result、完整 Local Adapter Factories 和唯一核心 Bootstrap 函数；导入时无数据库、模型、Tool 或 Dispatcher 副作用；
- Bootstrap 总是先安装 `DisabledRuntimeDispatcher` 清除旧执行权限；只有 APP_ENV 为 dev/development/local/test、`PUBLIC_ACCESS_ENABLED=false` 且数据库为 SQLite 或 localhost/127.0.0.1/::1 时才继续，生产、公开访问、ECS 服务名、内网或其他远程数据库均拒绝；
- `LocalPureAgentRuntimeAdapters` 显式要求 ContextAssembler、Main Agent Inputs、Admission Context、Action Loop、C02-3 Capability Factories 和 Slot Validator Registry；任何依赖都不能从全局状态隐式猜测；
- 通过 Guard 后复用 C02-1 Composition Root 冻结五类 Handler Registry，装配 Repository、Boundary、Action Loop、Guard、Executor、Controller 与 Dispatcher；Bootstrap 本身不打开 Session，也不执行 Agent Pulse；
- Conversation API 新增唯一手工入口 `bootstrap_pure_agent_local_runtime()`，在同一锁边界内安装 Dispatcher 与 Slot Validator Registry，并保存不含 Adapter/Secret 的 Bootstrap Receipt；`main.py` 与 FastAPI lifespan 未接入该函数，因此应用启动不会自动获得执行权限；
- AST、模块/应用 API 导入、3 个 Bootstrap Pydantic Schema、公共导出、默认 `DisabledRuntimeDispatcher` 和文本格式静态检查通过；获授权后新增独立 C02 专项测试，静态 Never Provider/Gateway 与零调用 Session Factory 覆盖隔离拒绝、显式 Activation、默认禁用、不完整、Ready、五 Handler 注册、Dispatcher/Slot 原子安装和 Ready 旁路阻断，共 `8 passed / 0 failed`、0.61 秒；未调用模型、RAG、真实 Tool、真实资料、外部 MCP、外部数据库或 ECS，0109 未修改、未应用。

C02-1—C02-4 的开发顺序只是实现依赖，Agent 运行时仍由 Main Agent 每轮动态选择 Action。

C03-1 已完成，两个具体 Adapter 只冻结持久化边界，不决定 Action：

- 新增 `persisted_local_adapters.py`，以显式 `LocalBoundaryInputPolicy` 和 `LocalAdmissionPolicy` 作为本地策略输入；六类 Action 的 Binding、Budget Demand 与 Output Contract 必须完整声明，缺项直接拒绝，不从环境或用户文本猜测；
- `PersistedLocalBoundaryInputsProvider` 从 Conversation、Task、Turn 和最新 Checkpoint 形成授权快照引用，并把 Goal 与 Assessment 资源纳入 Context 请求；它不做意图分类、查询规划、Tool 选择或权限扩张；
- `PersistedRuntimeAdmissionContextProvider` 重新校验持久化 Context receipt，读取 Budget ledger head、Cancellation Fence 和既有 Effect Fence，形成确定性的 scope/semantic/output hash 与 Guard 输入；缺少预算账户、上下文收据或跨域/过期记录时 fail closed；
- Repository 新增只读 Local Task Scope、Context Snapshot、Budget Balance 和 Effect Fence 投影；未新增表、字段或迁移，也不自动创建预算账户；Governed Action 的 Effect request hash 与 Controller envelope hash 已分离，避免幂等 Effect 被 envelope 存储格式污染；
- C03-1 尚未新增独立 Progress 表：Progress Window 只绑定当前持久化 Observation head，records 保持空，不虚构“有进展/无进展”事实；后续持久化进展 receipt 另行小步实现；
- Pure Agent 目录 50 个 Python 文件 AST 与新增文件尾随空白静态检查通过；经用户授权新增 C03-1 独立 SQLite 专项 6 项，并与直接受影响的 C01 Controller、C02 Bootstrap 合并回归，共 `20 passed / 0 failed`（2.27 秒）。未调用模型、RAG、Tool、真实资料、外部 MCP、外部数据库或 ECS，0109 未修改、未应用。

C03-2 代码已完成，Context 持久化接线仍是数据边界而不是 Workflow 节点：

- 新增 `persisted_context_adapters.py`，提供显式 `PersistedContextProjectionPolicy`、`PersistedContextCandidateSource`、保守 Token Counter、`PersistedContextSnapshotStore` 和 Repository-scoped Assembler Factory；
- Candidate Source 只投影策略/输出合同、Task/Plan/Checkpoint、当前用户 Turn、冻结 Tool 合同、资源/Observation 收据与最多 50 条有界对话历史；不做检索、摘要、意图判断、Tool 路由或 Action 决策，historical-memory lane 首版保持空；
- 资源收据显式标记 `evidence_loaded=false`，只证明引用已纳入当前授权快照，不能作为事实证据；Observation 仅有引用时也明确标记正文未加载，避免把引用伪装成结果；
- 持久化 Call Ledger 没有原始 Tool arguments，故本阶段不伪造 `ACTIVE_TOOL_CALL/ACTIVE_TOOL_RESULT`。只有未来能取得完整、配对、仍可见且重新授权的协议记录时才允许投影；
- Snapshot Store 复用 0109 的不可变 Context Snapshot 表，只在调用者现有事务内 flush，引用复用、Task 越界和 Hash 漂移均 fail closed；`LocalPureAgentRuntimeAdapters` 新增二选一的 Repository-scoped Context Assembler Factory，不改变旧零参数 Factory；
- Pure Agent 目录 51 个 Python 文件 AST 与改动文件尾随空白静态检查通过；经用户授权，C03-2 独立 SQLite 专项 `6 passed / 0 failed`（1.18 秒），连同 C03-1、C02 Bootstrap、C01 Controller 相邻回归共 `26 passed / 0 failed`（2.06 秒）。未调用模型、RAG、Tool、真实资料、外部 MCP、外部数据库或 ECS，0109 未修改、未应用。

C03-3 代码已完成，可恢复性仍是持久化边界而不是新 Workflow 节点：

- 新增隔离开发迁移 `20260821_0110`：建立不可变 `bid_pa_observation_artifacts`，并为 `bid_pa_calls` 增加可空 `input_json`；旧 Call 不回填、不推测原始参数，迁移尚未应用且不得进入 ECS；
- Controller 在 Effect 与 Budget 已结算后、提交 `OBSERVATION_ACCEPTED` 前保存完整 Observation 与结果 Artifact；Artifact、Action 结果、Context Snapshot、Task State Version 和状态转换受同一调用者事务及 Hash/Fence 约束；
- SQL Tool Ledger 对新 Call 保存经过规范化的原始参数；Repository 只在最新 Tool Observation 对应整批 Call/Result 全部终态、均被接受、顺序连续，并且 Action、Context、参数/结果 Hash、Registry、Visible Set、Authorization 与 Canonical Tool Message 全部匹配时恢复协议，任何缺口都整批省略；
- Persisted Context Candidate Source 将已验证 Artifact 投影为不可信数据；超过 Context 单项上限时完整 Artifact 仍保存在本地，只投影 Observation 与带 Hash 的有界收据。协议 Pair 采用 Mandatory Exact，并在 Context 中按 Call→Result 成对交错，不改变 Main Agent 动态选择下一 Action；
- 用户授权后新增 6 项进程内 SQLite 专项，覆盖 0110 ORM Schema、完整 Artifact/双 Tool Pair 往返、Call→Result 交错顺序、旧空参数与残缺批次安全省略、Registry/Visible/Authorization 漂移、Artifact Hash/Task Fence、幂等重放及超大 Artifact 有界收据；首轮发现并修复 Repository 漏导入 `BidPureAgentCall`，第二轮发现并修复相同 Tool 结果跨 `protocol_pair_ref` 错误去重。最终 C03-3 独立 `6 passed / 0 failed`（2.32 秒），C03-1/C03-2/C02/C01 合并回归 `32 passed / 0 failed`（4.66 秒）；C01 旧夹具同步改为真实 Context Snapshot 与正确 Artifact Result Ref，并确认 Controller 路径完成 Artifact/Context 持久化。0110 未执行，未调用模型、RAG、真实 Tool、真实资料、外部 MCP、外部数据库或 ECS。

C03-4 代码已完成，恢复仍是动态 Action Controller 的保护边界而不是业务 Workflow：

- Action Envelope 新增向后兼容的 Recovery Binding，冻结 Action 入场时的 Runtime Profile、授权策略引用和 Scope Snapshot Hash；旧 Envelope 可继续读取，但没有绑定时禁止自动恢复；
- 新增 Running Action Recovery Context/Plan/Controller。它复用现有 `RuntimeRecoveryGuard`，每次恢复重新校验 Profile、Registry、Authorization、Source Heads、Cancellation、Lease、Effect Fence 与当前 Task/Action；
- 新增当前 Running Action 专用 Artifact 读取边界及 Budget Settlement 完整性断言。只有完整结果正文、Action/Effect/Observation Hash、Context、State Version 和所有预算结算都一致，才构造原 `RuntimeActionExecution`；不从内存猜测 Budget Usage；
- `PureAgentRuntimeController` 对终态已持久化结果跳过 Effect 执行与重复预算结算，直接提交原 Observation，再调用既有 `after_observation` 继续由 Main Agent/Action Loop 动态决定；`retry_safe`、`reconcile`、`wait_for_lease`、`blocked` 只安全停止并报告，不自动重放；
- 本地持久化 Admission Adapter 同时提供恢复时的新鲜边界，Composition 只在该端口显式存在时注入；默认 Provider 仍禁用。用户授权后的 6 项本地专项覆盖终态消费、缺正文 fail closed、safe retry 不执行、Registry 漂移、Controller 跳过 Effect 后接纳持久化 Observation，以及预算未结算阻断，结果为 `6 passed / 0 failed`（1.65 秒）；随后 C03-1—C03-4、C02 Bootstrap 与 C01 Controller/API 相邻回归 `38 passed / 0 failed`（3.89 秒）。未运行迁移或任何模型/RAG/真实 Tool/真实资料链路。

## 11. C04-1 — Persisted Capability Boundary Adapters

C04-1 已完成：

- 新增 Plan、Tool Call Batch、Answer 三类持久化 Capability Boundary Provider。每次只处理主 Agent 已经选择并经 Guard 接受的一个 Action，重新校验当前 Task、Conversation、Context、Registry、Authorization Policy、Recovery Binding 与 Scope Snapshot Hash，不创建能力顺序或业务阶段图；
- Plan Boundary 使用 fresh Planner Context、当前 Complexity Gate 和持久化 active Plan Head；初次 Plan 与 Replan 分别校验 Direct/Planned 约束，不推测缺失 Plan；
- Tool Boundary 从显式 `PersistedToolBoundaryPolicy` 生成 Runtime-only Execution Context、白名单 Guard Policy 和有界 Deadline。默认 `runtime_enabled=false`，且 disabled 状态禁止授予 Local/MCP/External Egress 权限；真实 Tool Gateway 仍必须由外部显式注入；
- Answer Boundary 重新装配 fresh Main Agent Context。默认 `ReceiptOnlyAnswerAuthorityProjector` 只把草稿实际引用且仍存在于 fresh Context 的条目投影为 `unknown`、`non-citable` Runtime Receipt，不生成 Citation Authority Record，避免把普通 Context、资源引用或 Tool 收据误升级为可发布事实证据；后续真正的 Evidence Authority Adapter 仍需独立实现；
- 新增 `PersistedCapabilityAdapterFactories.capability_executors(...)`，可显式生成 C02-3 的完整 Factory 合同，但构造本身不打开 Provider、RAG、Tool、Dispatcher 或数据库事务；
- 6 项本地专项覆盖默认禁用、Tool 当前权限重绑定、Plan fresh Context/Complexity Gate、Answer 不可引用收据、越界 Grounding 拒绝和缺 Recovery Binding fail closed，结果为 `6 passed / 0 failed`（0.59 秒）；
- 新增代码与测试文件 AST 检查、模块导入检查和 Ruff `--no-cache` 静态检查均通过。未运行迁移、模型、RAG、真实 PDF、真实 Tool、外部 MCP、外部数据库或 ECS。

## 12. B07 — 发布准备

B07 只有在用户明确确认“全部开发完成并允许上线”后才能展开。届时另行形成：

- 受影响 API/Worker/迁移/配置清单；
- 专项回归、相邻契约与必要全量验证范围；
- RC 冻结与 SHA-256 产物清单；
- ECS 手工备份、恢复演练、迁移、发布、停止条件和回滚步骤；
- 功能开关分阶段启用方案。

所有 ECS 操作仍由用户手动执行。

## 13. C07 — Provider-visible Answer Projection

C07 代码已完成：

- 新增独立 `ProviderDecisionProjection` 与 `ProviderAnswerProjection`。模型无 Tool 分支的顶层输出从五字段联合合同缩小为 `action_kind + concise_basis + payload`，Answer 只写语言、自由 blocks 与证据引用；
- `context_snapshot_ref`、`state_version`、`schema_name` 和空 Quote 绑定由 Adapter 从当前权威请求注入，模型不能提供或覆盖；升级后仍必须通过原 `MainAgentModelDecision / AnswerDraft` Pydantic 合同、Grounding Guard、CitationProjector 和 Answer Commit；
- Answer block 使用单层按 `block_type` 校验的投影，保留 Narrative、Statement、Limitation、Interaction 的自由组合，没有固定报告章节或 Action 顺序；
- DeepSeek 首次无 Tool 输出与最多两次修复均使用精简合同，保留 Function Calling Tool 分支、动态 Evidence Atom few-shot 和字段级 `loc/type` 反馈；
- 静态检查通过：Ruff 无问题，新增模块/DeepSeek Adapter/包入口可导入并生成 Schema。最终模型可见 Answer Schema 为 3639 字符，原权威 Answer Schema 为 5546；精简 Decision Schema 为 704，原权威 Decision Schema 为 9560；
- 真实复验补齐 Provider Schema 兼容门、白名单 `loc/type/reason_code` 反馈及 Answer 条件业务规则投影；两次结构修复上限、Runtime Pydantic 最终权威和 fail-closed 均未放宽；
- C07 独立专项 `11 passed / 0 failed`，C01—C07 影响域相邻回归 `60 passed / 0 failed`（5.59 秒）。确定性测试只使用 Fake Provider 与进程内 SQLite；
- 官方 DeepSeek 隔离真实复验最终 `passed`：冻结香港中心 PDF `307` 页、冻结企业基线、本地 BCE 均通过 Hash/版本校验，Task `completed`，状态版本 `18`，安全事件 `18`，提交回答 `1` 份、引用 `5` 条；结果为 `.local-c07-pure-agent-acceptance/result.json`；
- 真实复验未使用 OCR/视觉、外部 MCP、Milvus、ECS 或生产数据库。四个 fail-closed 中间产物单独保留，未覆盖 C06 历史失败产物；未修改迁移与默认关闭开关。

C04-2/C05/C06 已有确定性专项与相邻回归基线 `90 passed / 0 failed`；C06 真实失败产物仍保存在 `.local-c06-pure-agent-acceptance/result.json`，C07 的独立通过产物没有覆盖该历史记录。

C07 本地真实业务闭环已验收通过，可以进入“本地可用性收口清单”的确认，不等同于生产发布授权。B07、ECS、迁移、发布候选和开关启用仍须用户另行明确确认。唯一隔离开发 head 仍为尚未应用的 `20260821_0110`，所有开关继续默认关闭。

## 15. C08 — 本地可用性收口

### C08-1 完成记录（2026-08-21）

- 新增 `local_preflight.py`，以 Hash 绑定的 Pydantic Report 统一检查显式 Activation、回环绑定、本地环境、公网关闭、双开关、Continuation Secret、专用 SQLite/head、Vite、冻结输入存在性、BCE 快照元数据、官方 Provider 白名单、已验收 Python 版本和 MCP/Milvus/OCR 禁用态；公开结果不包含任何路径、Secret 或 Adapter；
- Preflight 的 SQLite 仅以只读 URI 查询 `alembic_version`，文件只检查存在性/快照元数据，不读取 PDF 或 SecretEnvFile 内容，不导入/加载模型，不联网；任一必需项失败时 `runtime_install_allowed=false`；
- 新增 `app.pure_agent_local:create_app` 显式 Uvicorn Factory。只有专用启动脚本设置隔离环境且 Preflight 全通过后，才调用独立的冻结输入 Materializer 和既有唯一 Bootstrap；没有修改 `main.py` 生命周期，没有导入旧 Workflow/`bid_intake_*`；
- 新增 `start_bid_pure_agent_local.ps1` / `stop_bid_pure_agent_local.ps1`。默认端口 `9018`，只绑定 `127.0.0.1`，专用数据目录限定为 `.local-pure-agent-daily*`；迁移只在显式 `-InitializeLocalDatabase` 时应用到专用 SQLite，停止器只接受自身 PID/命令行双重归属；
- Conversation API 新增鉴权只读 `/runtime-status`，只投影 `not_configured/preflight_blocked/bootstrap_disabled/bootstrap_incomplete/ready` 和安全原因代码；页面在 Runtime 未就绪时禁止创建新 Task，并引导先修复 Preflight；
- 3 项确定性专项已在授权后执行，覆盖 Ready、远程/公网/版本偏移拒绝、Report 防篡改，结果 `3 passed / 0 failed`（2.22 秒）；
- 首轮只读 Preflight 正确发现新入口没有前置已验收 `.tmp/rq2-locked-runtime`，因此冻结 Python 版本门拒绝；启动器现显式注入该只读依赖目录，并固定 UTF-8 控制台输出。修正后 `FROZEN_PYTHON_RUNTIME` 通过；
- 授权初始化时，历史迁移在 `0011` 正确拒绝缺少 system admin 的全新库；补入禁用迁移所有者后，`0012` 又暴露 SQLite 不支持 `ALTER ... ADD CONSTRAINT`。两次均 fail-closed，半成品库未覆盖，最终保留为 `runtime.failed-alembic-0012-20260821.db`；
- 新增受限 Schema Snapshot 初始化器，复用 C07 已验收的 `Base.metadata.create_all` 本地边界：只接受项目内 `.local-pure-agent-daily*/runtime.db`，在同目录临时 SQLite 创建当前 ORM Schema、写入随机丢弃密码且禁用的迁移所有者、验证关键 `bid_pa_*` 表、标记 `20260821_0110` 后原子落盘；已有非目标 head 数据库拒绝覆盖，不修改任何 Alembic revision；
- 最终只读 Preflight `19/19`，Report Hash `sha256:2527472b76457faa83165854efc26873dee4bd8442a3b769b9d0448042b24642`，`ready=true`、`runtime_install_allowed=true`。只读结构复核：head=`20260821_0110`、18 张 `bid_pa_*` 表、bootstrap admin `is_active=false/must_change_password=true`，角色为 `admin/system_admin`，无服务 PID；
- 静态验收：相关 Python Ruff 通过（忽略同一 API 文件既存 F841）、初始化器 Ruff 与 PowerShell Parser 通过、JavaScript `node --check` 和 Vite production build 通过，2241 modules transformed；本轮未启动服务，未读取真实资料正文/SecretEnvFile 内容，未加载 Embedding/Reranker/OCR/视觉或调用模型。

详细日常命令见 `docs/bid-assessment-pure-agent-local-daily-entry-c08-20260821.md`。C08-1 本地环境门已收口，可进入显式启动验收；这不构成 B07、ECS、正式迁移或上线授权，所有仓库默认开关仍保持关闭。

### C08-2 完成记录（2026-08-22）

- 按授权读取冻结香港中心 PDF、冻结企业基线与 SecretEnvFile 白名单字段，加载本地 BCE，并完成 Runtime materialization、Bootstrap 和回环服务启动；未提交问题、未调用 DeepSeek；
- 首次尝试在读取资料/加载 BCE 前因默认 Python 缺少 `slowapi` 而安全退出。Preflight 已增加应用运行依赖检查，启动脚本默认改用项目隔离 venv；C08 专项复跑 `3 passed / 0 failed`（2.67 秒），Ruff 与 PowerShell Parser 通过；
- 最终 Preflight `20/20`，Report Hash `sha256:1a495184bb01df05b91e1eec0fa4b15cd98aedc99598336dca9e6b653439bd39`，`ready=true`、`runtime_install_allowed=true`；
- 服务保持运行于 `127.0.0.1:9018`。只读核验确认 PID 文件与真实监听 PID 一致、没有非回环监听，Health 与管理页均为 HTTP 200；
- `bid_pa_conversations/messages/tasks/actions/calls/events` 六张运行表计数均为 `0`。本轮没有 Agent Action、Provider Call、Reranker、OCR/视觉、MCP、Milvus 或外部基础设施访问；
- Windows venv 包装进程的 PID 归属已修正：启动器落盘真实监听 PID，停止器使用 PID 文件、精确回环端口和 Python 进程三重边界，不进行模糊进程终止。

C08-2 证明“本地启动至可访问页面且 Runtime 已装配”的路径可用，但尚未验证鉴权后的 `/runtime-status` 和页面用户态，也未验证提交消息后的动态 Action Loop。下一步只做无模型登录/状态验收仍需单独授权；DeepSeek 或真实问题提交继续需要再次明确授权。

### C08-3 完成记录（2026-08-22）

- 在专用 SQLite 创建隔离验收用户 `c08_acceptance_20260822`，账号激活、无需改密，只授予 `staff`；禁用的 bootstrap admin 未修改；
- 本机 API 验证通过：登录签发 Token，`/auth/me` 返回相同身份与 `bid_assessment_pure_agent=available`；鉴权后的 `/runtime-status` 返回 `startup_status=ready`、`runtime_available=true` 和 `LOCAL_RUNTIME_COMPOSITION_READY`；
- 浏览器验证通过：未登录态正确阻断，登录后返回 Pure Agent 页面并显示验收账号和模块导航；刷新后登录会话保持，页面无 Runtime 阻断告警，无 Console warning/error；
- 页面安全态通过：空输入时发送按钮禁用；仅填写未提交的本地检查文本时按钮启用；清空后恢复禁用；
- 最终只读核验 `bid_pa_conversations/messages/tasks/actions/calls/events` 均为 `0`。没有创建 Agent 对话或 Task，没有 Provider Call，没有调用 DeepSeek；
- 服务继续运行于 `127.0.0.1:9018`。C08-3 不构成真实问题提交、模型调用、生产发布、ECS 或迁移授权。

C08-3 已证明日常入口从登录、身份恢复、模块可见性到 Runtime 就绪页面的无模型路径可用。下一步仅剩从页面提交一次受控真实问题并核验动态 Action Loop、回答与引用；该步骤涉及官方 DeepSeek 和真实资料，必须重新取得明确授权。

### C08-4 完成记录（2026-08-22）

- 按授权从页面提交同一限定问题，只使用冻结香港中心 PDF、冻结企业基线、本地 BCE 和官方 DeepSeek；OCR/视觉、外部 MCP、Milvus、ECS、生产数据库与旧 `bid_intake_*` 均未启用；
- 真实运行发现并修复两个日常边界问题：Dispatcher 对活动 Action 未处理异常的顶层失败收束，以及 Provider 完整序列化 Envelope 未在 Context 预算中提前预留。失败收束会结清 Effect/Budget、持久化安全 Observation 并进入 `failed`，不会留下 `running` 假象；
- 中间产物如实保留：Context 超限任务在修复前被取消为 `cancelled/v27`；无效 JSON 运行安全终止为 `failed/v12`；模型正文自行写定位符的运行被权威引用 Guard 拒绝后安全终止为 `failed/v38`。三者均无已提交回答；
- Provider Answer Projection、模型可见业务规则和本地 System/Output Contract 已明确：模型正文不得自行生成 Citation 编号、页码、URL、路径或内部 Ref，只能选择当前 Context 的 `grounding_refs`，Runtime CitationProjector 仍是唯一引用显示权威；
- 最终页面 Conversation `73f9a488-3e27-5d60-96d5-b4edae09938b`、Task `58a39d92-ca3c-457f-9b31-13a590378740` 在 `completed/v18` 终止。8 个 Action 为 4 次 Main Agent 决策、3 个 Tool Batch 和 1 次 Answer，全部成功；3 个 Batch 内共 6 个只读 Tool Call；
- 最终 Answer Commit 成功：4 个 Block、3 个 Statement、1 个 `evidence_insufficient` Limitation、5 个验证通过的 Grounding Ref 和 5 条 Runtime Citation。Grounding/Citation Validation `accepted=true`、Issues 为空，模型原始正文不含引用编号、页码或 URL；
- 回答明确给出“不建议立即投标”，并列出工期延误高额违约责任、投标/履约担保能力未获核验两项关键风险；证据不足项明确保留为限制，不把未知包装成已知；
- C01 失败终态专项 `2 passed / 0 failed`，C07 Answer Projection 专项 `11 passed / 0 failed`，Ruff、AST、差异格式检查通过；Preflight 保持 `20/20` 和原 Report Hash。

C08 本地可用性收口完成，可以进入隔离本地的小规模真实业务试用与缺陷迭代。B07、ECS、生产迁移、发布候选和默认开关启用仍未授权。

## 16. V2-T — Pre-Answer Evidence Readiness

状态：代码、合成合同验证和 9019 受控真实复验均已完成。

- [x] 候选存在但 Evidence Atom 为零时，Answer 前强制一次 `evidence_read`。
- [x] 允许证据升级跨过 Search 饱和门，但不允许继续普通 Search。
- [x] 将 `answer_schema_invalid / grounding_refs.required_for_kind` 转为类型化证据升级。
- [x] 证据读取不可用或一次尝试后仍未就绪时，返回 Runtime-owned 可行动诊断回执。
- [x] 补充候选就绪、饱和升级、升级失败和 Schema 恢复四类合成合同。
- [x] 经用户授权执行 V2-T 专项 `4 passed`，V2-D 至 V2-S 相邻合同回归 `88 passed`。
- [x] 经用户另行授权加载 9019，并从真实矩阵第 2 题开始逐题使用全新对话复验。
- [x] Q2—Q4 证据型问题均完成并生成 Runtime Citation；Q5 普通交流以零 Tool Call 完成。
- [x] 真实矩阵未出现通用安全失败，且未影响 9018、ECS 或生产环境。

本切片不修改数据库迁移、默认开关、V1、旧 `bid_intake_*`、9018、ECS 或生产环境。
