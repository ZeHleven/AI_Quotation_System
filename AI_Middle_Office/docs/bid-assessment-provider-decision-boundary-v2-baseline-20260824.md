# Provider Decision Boundary V2 短基线

日期：2026-08-24
状态：V2-A 至 V2-Q 代码边界已建立；V2 仍由同一默认关闭开关隔离。V2-Q 专项与 V2-D 至 V2-P 相邻合同回归均已通过。9019 当前运行 V2-Q 实例（PID `7768`）；尚未提交 V2-Q 真实问题或调用 DeepSeek。

## 为什么改

V1 把动作选择和动作内容放在同一次模型输出中，并用
`ProviderDecisionProjection.payload: dict` 承载 Plan、Slot 请求或完整回答。这使单次输出合同过大，也让格式恢复、上下文容量和业务校验互相影响。

V2 不针对某个问题写特例，而是收窄两个通用边界：统一所有 Provider JSON 入站处理；把“决定下一步做什么”和“生成回答内容”拆成两个小合同。

## V2-A：统一 Provider Ingress

所有 assistant JSON、Provider structured output 和 Function Call arguments 都先经过同一个 fail-closed 入站端口：

```text
Provider 原始值
    -> 大小/编码/JSON 对象边界检查
    -> 有界且不改变 JSON 值的结构恢复
    -> Pydantic 合同校验
    -> hash-bound receipt 或 typed failure
```

核心约束：

- 原始 Provider 内容不进入持久化合同、安全异常或诊断快照；只保存哈希和字节数。
- 只允许删除 Markdown fence、提取唯一 JSON 对象等确定性恢复；不得静默改写字段值。
- 重复键、多对象、截断、非对象、越界和 Schema 错误使用统一失败码。
- Runtime 校验始终是最终权威；Provider strict mode 只是前置帮助。
- `ProviderBoundaryV2Config.enabled` 默认 `false`。

## V2-B：控制决策与回答生成分离

```text
Main Agent Decision Context
    -> Provider native Function Call -----------------> Tool Gateway
    -> ProviderNextActionDecision
         |- plan/replan ------------------------------> Planner Capability
         |- request_information ----------------------> Runtime Slot Boundary
         `- answer -> 独立 Answer Context
                        -> ProviderAnswerProjectionV2（最小合同）
                        -> Grounding/Citation/Commit Guards
```

`ProviderNextActionDecision` 只包含：

- `action_kind`
- `concise_basis`
- 仅在请求用户信息时出现的 `information_needs`
- 非授权性的 `target_source_bases` 提示

它不能携带任意 payload、Plan、Slot Schema、Tool arguments 或回答正文。Function Call 继续走 Provider 原生工具分支；选择 `answer` 后，Runtime 才建立独立、可控的回答请求，并使用最小 `ProviderAnswerProjectionV2`。

## V2-C：默认关闭的隔离实现

V2-C 新增两个可注入组件：

- `DeterministicProviderJsonIngressAdapter`：校验原始 UTF-8 字节哈希和大小，拒绝重复键、多对象、截断、非对象与跨请求漂移；只允许保持 JSON 字段值不变的 fence 删除和唯一对象提取。
- `ProviderDecisionAnswerOrchestratorV2`：第一次调用动态选择原生 Function Call 或小型 Next Action；只有选择 `answer` 才通过 `ProviderAnswerContextProvider` 获取独立 Answer Context 并执行第二次回答投影调用。

编排正常最多进行一次决策调用和一次条件式回答调用；仅当回答通过 JSON Ingress 但未通过 Pydantic 结构校验时，允许再进行一次 Answer-only 修复。它不包含固定业务步骤，也不循环执行工具。Tool Gateway、Planner、Slot、Grounding、Citation 和 Commit 仍由各自 Runtime 权威边界处理。

默认构造使用 `ProviderBoundaryV2Config(enabled=false)`。关闭时会在发起任何 Provider 调用前拒绝；显式装配函数只创建组件，不修改应用 Composition Root 或 C08 开关。

## V2-E：默认关闭的隔离 Runtime 接入

新增独立开关：

`FEATURE_BID_ASSESSMENT_PURE_AGENT_PROVIDER_BOUNDARY_V2=false`

接入规则只有三条：

- 开关关闭时，C08 Composition Root 原样选择 V1，不改变现有页面、API 和 Action Loop 合同。
- 开关开启时，V2 原生承载 Tool Calls 和 Answer 双调用；Answer Context 从当前 Decision Context 派生，但移除工具合同、活动 Tool Protocol Pair 和 V1 输出合同。
- V2 暂未携带 Plan/Slot 的完整 Runtime 载荷，因此由 V1 兼容物化器补齐；只有 V1 返回的动作类型与 V2 选择完全一致才接受。V2 解析、绑定或 Guard 失败时 fail-closed，不进行无声 V1 降级。

现有 `MainAgentProviderOutcome`、Dynamic Action Loop、Tool Gateway、Planner、Slot、Grounding、Citation、Commit 和 Conversation API 均保持兼容。Runtime Status 增加当前 Provider Boundary 模式，便于确认实际选择的是 `v1` 还是 `v2`。本地启动脚本只有显式传入 `-EnableProviderBoundaryV2` 才会开启 V2。

V2-E 本地专项测试覆盖：默认关闭与 V1 身份保持、显式 V2 选择、Answer 双调用与 V1 Action 合同投影、原生 Tool Call、Answer Context 去除 V1/Tool Protocol、Plan/Slot 动作一致性兼容、动作不一致拒绝、V2 Ingress 失败不触发 V1。结果为 `8 passed`，未调用模型或读取真实资料正文。

随后执行 V2-D/V2-E 相邻合同回归，结果为 `23 passed`。该回归覆盖跨问题形态的 Ingress/Decision/Answer 合同矩阵与 V2-E Runtime 兼容桥；未调用模型、未读取真实资料正文，也未切换当前本地服务。

## V2-F：回答合同韧性收口

真实复验暴露的共性问题不是某一道业务问题，而是模型被要求直接构造 Runtime 内部 Answer 图：block id、epistemic status、limitation code 和双向引用任一不一致，整次任务都会以 `answer_schema_invalid` 终止。

V2-F 采用以下通用边界：

- 模型只返回 `response_language` 与 `items`；每项只含 `kind`、`text`、`grounding_refs`，以及按类型需要的 `basis` 或 `limitation`。
- Runtime 确定性生成 block id、claim/epistemic 状态、presentation hint、limitation code 与双向引用，再交给原有 Canonical Answer、Grounding、Citation 和 Commit Guard。
- 首次 Pydantic 结构失败时只允许一次 Answer-only 修复；修复携带脱敏的字段路径/错误类型，拒绝回答回传上限为 64 KiB，超限只传哈希。
- 第二次仍失败即 fail-closed，不回退 V1；失败工件只持久化安全错误码和脱敏诊断码，不保存原始 Provider 内容、Pydantic message 或字段值。
- 正常回答仍为两次模型调用；只有结构失败分支最多增加一次修复调用。

已补充正常回答、跨域比较、Runtime 图升级、修复成功、修复耗尽、V1 不降级、畸形 JSON、上下文过大与 Grounding 越界等合同用例。2026-08-24 使用冻结本地依赖环境执行 V2-D/V2-E/V2-F 专项与相邻回归，结果为 `27 passed in 0.59s`；未调用模型、未读取真实资料正文，也未重启 9018/9019。

同日按单独授权替换 9019 V2 独立实例：Preflight `21/21` 通过，9019 `/health/live` 返回 `ok`、登录页返回 HTTP 200；9018 原 PID 与健康状态保持不变。启动过程按授权读取冻结资料、SecretEnvFile 白名单字段并加载本地 BCE，但未提交问题、未调用 DeepSeek。

### uncertainty 与本地 Dispatcher 终态收口

9019 的一次真实问题暴露了两个通用合同缺口：V2 把 `uncertainty` 机械升级为 UNKNOWN Statement，却绑定了普通 Evidence Atom，必然被 Canonical Grounding 的 UNKNOWN 支持矩阵拒绝；同一任务继续调用工具和重试回答，最终耗尽 64 个 pulse，而本地 Dispatcher 只返回 STOP、没有写入终态，形成 `running + 无 in-flight Action + 无后续唤醒` 的静默悬挂。

本次按通用语义修复：

- `uncertainty` 只升级为 Runtime-owned `EVIDENCE_INSUFFICIENT` Limitation，保留模型给出的“不确定内容 + 缺失原因”和已选择的证据前提，不再制造不兼容的 UNKNOWN Statement；Ingress 同时约束合并文本与 Grounding 数量不得超过 Canonical Limitation 上限。
- Dispatcher 达到 `max_pulses_per_dispatch` 后调用统一 fatal settlement，将 Task 原子收口为 `failed`，错误码为 `runtime_pulse_limit_exceeded`；无论当时是否仍有 active Action，都不能留下静默 `running`。
- 合同测试连续两次将相同 uncertainty 草稿送入 Grounding Guard，均应稳定接受；终态测试覆盖“最后一个 Action 已成功、当前无 in-flight Action”的真实耗尽形态。

2026-08-24 使用冻结本地依赖执行针对性测试 `3 passed`，随后执行 V2-D/V2-E、Controller、V606 合成回答引用相邻回归，最终结果为 `48 passed`。全程未调用模型、未读取冻结资料正文、未重启 9018/9019，也未修改已悬挂 Task。

## V2-G：Retrieval Convergence 收敛边界

后续真实问题证明，安全终态只能避免 Task 静默悬挂，不能阻止主 Agent 在 Answer 之前持续换关键词检索。失败 Task 的 16 次主 Agent 决策全部选择 Tool Batch；Evidence Atom 已稳定后，Tool Result 仍因每次生成新的 `call_ref` 而被误判为 `material_progress=true`，最终由 64 pulse 上限收口为 `failed`。

V2-G 采用以下通用边界：

- Tool 进度信号由 Tool Result 内容确定性生成：Search 使用候选 `evidence_ref`，`evidence_read` 使用 Evidence Atom ref，目录读取使用结构条目标识；新的 `call_ref` 不再代表新信息。
- `RetrievalConvergenceGateV2` 从已持久化、已压缩的 Tool Observation 计算全局语义新颖度。默认连续 2 个 Tool Batch 没有新增语义信号，或累计达到 8 个 Tool Batch，即标记 `saturated`；阈值是显式 Pydantic 策略合同，不写入问题关键词或业务阶段。
- 饱和后生成新的 hash-bound Terminal Decision Context，移除 Registry、Tool Contract、活动 Tool Call/Result 和 V1 Output Contract；Provider 的 `tool_choice` 强制为 `none`，只允许 `answer` 或 `request_information`。
- Terminal/Answer Context 最多保留最近 4 个 Tool Observation；既有 Evidence Atom 独立保留，旧检索过程由排除回执记录，避免 Context 随检索轮次无界增长。
- 64 pulse 终态上限继续保留为异常兜底，不再承担正常检索收敛职责。

2026-08-24 已使用合成数据和队列 Provider 完成本地验证：V2-G 4 个针对性合同用例全部通过；V2-D/V2-E 合同矩阵 `32 passed`；C02 装配、C03 Observation Protocol 和 C04 持久化 Capability Adapter 相邻回归 `24 passed`。覆盖语义信号不依赖 call identity、连续无新信息、绝对 Tool Batch 上限、Terminal Context Observation 压缩，以及饱和后 Provider 请求零工具可见性。全程未调用模型、未读取冻结资料正文、未重启 9018/9019。

## V2-H：Provider Recovery 与 Grounding-aware 终态收口

9019 的后续真实任务证明 V2-G 已在 8 个 Tool Batch 后停止检索，但此前被 Grounding Guard 拒绝的 Answer 未形成终态恢复约束；饱和后的 Next-Action 又因 Schema 不合法直接触发 `decision_schema_invalid`。V2-H 按通用 Provider 合同修复：

- Next-Action Schema 或终态 action 语义不合法时，只允许一次零 Tool 的有界结构修复；成功结果携带原响应哈希和脱敏 `path + error_type` 修复回执，修复耗尽时同一诊断码可由 Runtime Failure v2 持久化。
- 检索饱和且 Context 中存在被拒 Answer 的 Guard Feedback 时，Runtime 签发 hash-bound Terminal Answer Authorization，跳过多余的终态选择调用；Answer Request 显式携带 feedback refs，并要求修复支持矩阵或降级为带 limitation 的 uncertainty。
- Grounding-aware 模型重试最多一次；若第二个 Answer 仍被 Guard 拒绝，Runtime 返回不含事实断言的可行动降级说明并终止，不再继续检索、模型生成或消耗 pulse。
- 上述边界只依赖已持久化 Observation、Convergence Receipt 和 Pydantic 合同，不包含问题关键词或业务阶段分支。

2026-08-24 使用合成数据和队列 Provider 执行 V2-H 专项 `5 passed`；随后复跑 V2-D/V2-E/V2-G 完整合同矩阵，结果为 `37 passed`。全程未调用模型、未读取冻结资料正文、未重启 9018/9019。

## V2-I：Provider Tool Call 数量合同与有界压缩恢复

9019 的最新真实失败不是业务问题或 Grounding 失败：DeepSeek Provider 能力合同规定单次最多返回 4 个 Tool Call，但 V2 决策输入没有向模型展示该上限；模型返回更多调用后，Adapter 又把数量溢出折叠为普通 `response_contract_violation`，V2 因而无法执行针对该合同的安全恢复。

V2-I 在公共 Provider/Decision 边界完成以下收口：

- 每次 Next-Action 调用都显式携带 `tool_call_constraints`，包含单次调用上限、是否允许并行、优先选择不重叠高价值调用以及禁止静默截断的溢出策略；本地系统策略只在该字段存在时引用它，不改变 V1 输入合同。
- Provider Adapter 将数量溢出类型化为 `tool_call_limit_exceeded`，安全错误只包含实际数量与能力上限；Runtime Dispatcher 可按现有安全失败机制持久化该错误码，不再误报为普通响应合同错误。
- V2 只对这一精确错误执行一次有界压缩恢复：要求模型重新选择不超过上限的、不重叠且价值最高的一批调用，剩余工作留给后续动态决策。Runtime 不提高上限，也不按返回位置静默截断。
- 恢复后可以正常进入 Tool Call 分支，也可以收敛到合法 Answer；若第二次仍然溢出，原类型化错误直接失败。若恢复结果又违反 Next-Action Schema，也在第二次调用后终止，不再叠加一次结构修复。

2026-08-24 使用合成 Context、队列 Provider 和真实 `ProviderAdapter` 编解码边界执行 V2-I 专项 `5 passed`，覆盖 5 个 Tool Call 对 4 个上限的类型化、模型可见合同、恢复到 Tool、恢复到 Answer、重复溢出以及总恢复次数上限；随后执行 V2-D/V2-E/V2-G/V2-H/V2-I 相邻回归，结果为 `42 passed`。全程未调用模型、未读取真实资料正文、未重启 9018/9019。

## V2-J：Context 可见性、Grounding 资格与跨轮证据血缘

9019 的普通交流失败暴露的根因是 Answer 请求把所有 Context ref 都当成可引用 Grounding：Policy、Task State、当前用户消息和历史对话虽然应当对模型可见，却不是业务事实证据；同时，追问若直接引用历史回答文本，又会绕开原始 Evidence Atom 的持久化权威。

V2-J 采用四条通用边界：

- `Context visibility` 与 `Grounding eligibility` 分离。事实、推断和项目建议只能选择当前 Answer Context 中的 `EVIDENCE_ATOM`；Policy、Task、用户消息、对话历史、Plan 和 Tool Protocol 只提供理解上下文。
- `uncertainty` 使用独立 `allowed_limitation_refs`，可选择 Evidence Atom、授权资源回执或限制回执；资源回执只能证明“已授权但证据尚未加载”，不能支持事实断言。
- 寒暄、普通交流和不新增项目事实的表达转换使用 `general_advice`，不携带 Grounding，也不触发伪造引用。
- 追问或改写只继承上一已提交 Answer 实际通过 Guard 的 Evidence Atom。Runtime 从 committed Response、Answer Observation、accepted validation 和原始 Tool Observation 逐层复核哈希、Task、会话、资源范围及版本；历史回答正文自身不升级为证据，已 superseded/stale 或跨资源范围的证据不得继承。

Persisted Answer Authority 同时增加稳定类型化失败码，区分 Context 过期、Grounding 越界、Evidence 权威不可验证和 Context Evidence 漂移；本地 Dispatcher 可继续使用现有安全诊断回执，不再把这类失败统一折叠成 `runtime_dispatch_failed`。

V2-J 已补充合成合同用例：可见控制 Context 不进入事实 Grounding 白名单、普通交流零 Grounding、控制项冒充事实证据被拒、已提交 Answer 只恢复其 validated Evidence Atom、跨资源范围不继承，以及 Evidence Authority 冲突返回类型化错误。

2026-08-24 按单独授权执行时，首轮专项发现 `PersistedPriorAnswerEvidenceLineage.build()` 构造哈希未包含默认 `schema_name`，而模型校验包含该字段，形成确定性哈希漂移；修正为构造和校验共用完整 payload 后，V2-J 专项 `5 passed`。随后 V2-D/V2-E/V2-G/V2-H/V2-I 相邻合同回归 `44 passed`，C04 Persisted Evidence Authority 完整回归 `8 passed`，共覆盖 52 个唯一用例。测试阶段未调用模型、未读取真实资料正文、未重启 9018/9019。

同日按后续单独授权替换启动 9019 V2 独立实例：Preflight `21/21` 通过，新 9019 健康接口返回 `ok`、登录页返回 HTTP 200，9018 原监听进程与健康状态保持不变。启动过程读取冻结资料与 SecretEnvFile 白名单字段并加载本地 BCE，但未提交业务问题、未调用 DeepSeek。

## V2-K：不可见 Tool 名称的类型化与有界 Registry 恢复

9019 的普通交流问题在首次 Decision 调用即失败。安全诊断确认 Provider 返回了 Registry 外工具名；Adapter 将其折叠成通用 `response_contract_violation`，而 V2 只识别工具数量溢出，因此没有机会让模型改为零工具 Answer 或合法工具调用。

V2-K 在公共 Provider/Decision 边界完成以下收口：

- Provider Adapter 将 Registry 外工具名类型化为 `tool_name_not_visible`；安全错误不持久化或回显模型编造的工具名。
- V2 对该精确错误最多执行一次 Registry 约束恢复，显式提供当前 `allowed_tool_names`，允许模型改为合法 Function Call 或零工具 Next Action。
- Runtime 不猜测别名、不做相似名映射、不把未知工具静默替换成任一真实工具；第二次仍违规时保留类型化失败。
- 工具名恢复、工具数量恢复和 Next-Action 结构恢复共享同一总恢复上限；一次 Provider 合同恢复后不再叠加另一类恢复。

已编写合成合同用例，覆盖 Adapter 类型化、未知工具转 Answer、未知工具转合法 Tool Call、重复未知工具，以及未知工具与数量溢出互相叠加时的终止行为。

2026-08-25 按单独授权执行 V2-K 专项，结果为 `6 passed`；随后执行 V2-D/V2-E/V2-G/V2-H/V2-I/V2-J 相邻合同回归，结果为 `44 passed`。全程未调用模型、未读取真实资料正文、未加载 Embedding/Reranker/OCR、未访问外部 MCP，也未重启 9018/9019。

同日按后续单独授权替换启动 9019 V2 独立实例：Preflight `21/21` 通过，Provider Boundary V2 显式选择，服务进程 PID `41368`，健康接口返回 `ok`、登录页返回 HTTP 200。启动过程按授权读取冻结资料、SecretEnvFile 白名单字段并加载本地 BCE；未提交问题、未调用 DeepSeek。启动脚本只处理 9019；验收时 9018 未监听，本轮未对其启动或修改。

## V2-L：资源身份事实与 Answer Grounding 有界恢复

9019 的“当前加载了哪份招标文件”失败并非文件未加载：`documents_outline` 已成功返回目录，但目录 Tool Result 不是可引用 Evidence Atom；Answer Provider 随后把普通可见 Context 当成事实 Grounding，因而被 `answer_grounding_rejected` 拒绝。资源名称属于 Runtime 装配时已知的资源身份，既不应要求搜索业务正文，也不能把任意 Tool Result 升格为事实证据。

V2-L 采用以下通用边界：

- Runtime 在显式本地装配时创建 `AuthorizedResourceIdentity`，只含资源 ref、类型、展示名和版本 ref，并与本次 authorization snapshot 绑定。它不包含资源正文，也不能支持资格、条款、风险或企业能力等业务结论。
- Answer Request 将资源身份 ref 放入独立的 `allowed_runtime_fact_refs`；事实、推断和项目建议仍只能选择 `allowed_grounding_refs` 中的 citable Evidence Atom，二者不能混用。
- Provider 使用 `runtime_fact` 表达当前加载资源的名称、类型、版本或加载状态。Runtime 将其升级为独立 `RuntimeFactBlock`，只接受 `RESOURCE_IDENTITY_RECEIPT + RUNTIME_RECEIPT + SUPPORTED + non-citable` 的精确组合。
- `RuntimeFactBlock` 不作为字段追加到既有 `StatementBlock`，避免历史 AnswerDraft 反序列化后产生默认字段并改变 canonical hash。
- Answer 首次选择错误 Grounding 类别时允许一次 Answer-only 语义修复；修复只返回各类别允许的 ref 和脱敏错误类型，不回显拒绝内容。结构修复与 Grounding 修复共享一次总上限，第二次仍错误即 fail-closed。
- Citation Projector 为 Runtime Fact 生成空 Citation binding，Renderer 可发布其文本但不得生成业务引用；普通资源可用性回执、目录 Tool Result和其他 Context receipt 仍不能冒充资源身份事实。

代码骨架和合成合同用例已写入工作区，最终扩大 AST 静态检查 `194/194` 通过。2026-08-25 按单独授权执行 V2-L 专项，首轮发现安全失败收据把 `repair_attempt` 错误限定为结构修复，导致 Grounding 类别修复耗尽后无法生成合法诊断；保持旧字段名兼容并将其语义明确为通用有界 Provider projection recovery 后，专项 `6 passed`。随后 V2-D/V2-E/V2-G/V2-H/V2-I/V2-J/V2-K 完整合同回归 `53 passed`，C03 Context、C04 Capability/Evidence Authority 与 V606 Answer/Citation 共享边界回归 `35 passed`，共覆盖 88 个唯一用例。全程未调用模型、未读取真实资料正文，也未重启 9019。

同日按后续单独授权替换启动 9019 V2 独立实例：Preflight `21/21` 通过，Provider Boundary V2 显式选择，服务进程 PID `42588`，健康接口返回 `ok`、登录页返回 HTTP 200。启动过程按授权读取冻结资料、SecretEnvFile 白名单字段并加载本地 BCE；未提交问题、未调用 DeepSeek。9018 当前未监听，本轮没有对其启动或修改。

## V2-M：非权威来源提示的确定性容错

9019 的“对这份招标文件，有什么风险，有哪些重点信息”在 8 个 Tool Batch、20 次成功只读 Tool Call 后进入饱和终态；Provider 的 `target_source_bases` 两个成员连续两次不符合 `SourceBasis` 枚举，导致 `decision_schema_invalid`，Answer 调用尚未开始任务就失败。该字段在合同中明确为 advisory only，应用代码也不使用它进行工具、权限、证据或 Answer Authority 决策，因此让它阻断任务属于权威等级错配。

V2-M 采用以下通用边界：

- 模型可见合同显式给出 `target_source_bases` 的精确允许值、非授权属性、空数组能力和未知值省略规则。
- Runtime 在完整 JSON 安全入站后、权威 Pydantic 校验前，只对该字段执行确定性过滤：保留顺序、只接受精确枚举、去重、限制长度；错误类型、未知值和别名均被删除，不猜测、不做相似映射。
- 该过滤不消耗 Provider 修复次数。`action_kind`、`concise_basis`、`information_needs`、额外字段、终态动作限制、Tool、权限和 Grounding 仍按原合同严格校验；混合错误中的权威错误仍会触发一次有界修复或 fail-closed。
- Ingress Receipt 继续保留 Provider 原始规范化 JSON 的哈希和大小，接受后的 Pydantic 合同使用独立哈希；发生过滤时追加 `advisory_source_hints_filtered` 审计标记，不持久化被删除的原始值。
- 合同用例覆盖全非法值、合法与非法混合、重复值、错误字段形状、权威字段同时失败，以及 Provider 可见精确白名单；不绑定问题文本、PDF 或企业资料。

V2-M 代码和合成合同用例已写入工作区，相关 Python 文件 AST 静态检查 `4/4` 通过。2026-08-25 按授权执行 V2-M 专项 `9 passed`，随后执行 V2-D/V2-E/V2-G/V2-H/V2-I/V2-J/V2-K/V2-L 相邻合同回归 `53 passed`，本轮共执行 62 个合成合同用例；未调用模型、读取真实资料正文、加载 Embedding/Reranker/OCR 或访问外部 MCP。既有失败 Task 不自动改写或续跑。

同日按后续授权启动 9019 V2 独立实例加载 V2-M：允许读取冻结香港中心 PDF、SecretEnvFile 白名单字段并加载本地 BCE；Preflight `21/21` 通过，`/health/live` 返回 `ok`，登录页返回 HTTP 200，服务进程 PID `13180`。启动过程未提交业务问题、未调用 DeepSeek，也未操作 9018。

## V2-N：唯一动作权威与 Tool 信息需求绑定

9019 的普通交流“你好”并不是无法回答。真实诊断显示，V2 首次 Decision 已选择 `documents_outline` 并成功执行；后续一次 Decision 选择了非 Answer 动作后，Runtime 又调用 V1 兼容 Provider 重新生成完整动作。V1 返回的动作类型与 V2 已选类型不一致，最终触发 `V1 compatibility payload did not match the V2 next action`。根因是同一轮存在两个能够决定动作类型的模型调用，而非某个问法本身。

V2-N 收敛为以下通用边界：

- V2 Next Action 是本轮唯一动作权威。V1 兼容 Provider 参数只为保留既有本地装配签名，不再调用，也不能重新选择动作。
- `answer` 继续进入独立 Answer Projection；`plan`、`replan` 和 `request_information` 的第二次调用只能生成已锁定动作的专用 Pydantic 载荷，输出合同不包含 `action_kind`、工具或回答正文。
- 锁定载荷与首次 V2 选择的 outcome ref/hash、请求、Task、Context 和 Registry 绑定，并生成独立 Ingress Receipt 和 hash-bound outcome。模型试图夹带另一动作类型时按额外字段拒绝，只允许一次有界载荷修复；耗尽后使用 `locked_action_payload_invalid`。
- Tool 决策合同显式要求每个 Function Call 绑定当前用户请求或已接受未决 Context 中的具体信息需求；没有未决信息需求时必须返回零 Tool 的 Next Action，禁止因为工具可见而预防性调用。
- Search Tool 已通过 `query` 表达信息需求，`evidence_read` 通过既有候选 ref 表达证据依赖；`documents_outline` 补充必填 `navigation_goal`，避免无具体导航目标的目录探测。该约束不使用问候词、问题关键词或意图分类特例。

已编写合成合同用例，覆盖 Plan/Replan/Slot 锁定载荷、V1 零调用、载荷越权改判的一次修复与类型化耗尽、模型可见 Tool 信息需求合同，以及目录工具缺少具体导航目标时的 Pydantic 拒绝。相关 Python 文件 AST 静态检查 `10/10` 通过。

2026-08-25 按授权执行 V2-N 专项 `7 passed`。首次扩大相邻回归时，V2-I 的旧断言仍要求四字段 Tool 约束字典，未包含 V2-N 新增的 `information_need_binding`；更新该合同预期后，失败用例复跑 `1 passed`，V2-D/V2-E/V2-G/V2-H/V2-I/V2-J/V2-K/V2-L/V2-M 相邻合同回归最终 `65 passed`，受影响 V602 Registry/权限与 Gateway/Adapter 回归 `23 passed`。最终覆盖 88 个唯一合成用例；未调用模型、未读取真实资料正文，也未重启 9019。

同日按后续授权替换启动 9019 V2 独立实例加载 V2-N：Preflight `21/21` 通过，Provider Boundary V2 显式选择，服务进程 PID `37764`，`/health/live` 返回 `ok`，登录页返回 HTTP 200，监听地址仅为 `127.0.0.1:9019`。启动过程按授权读取冻结资料、SecretEnvFile 白名单字段并加载本地 BCE；未提交问题、未调用 DeepSeek，也未操作 9018。

## V2-O：零工具控制决策与最小 Tool 投影

V2-N 真实复验中，“你好”在第一次 Decision 即因 Provider 选择 Registry 外工具而失败。诊断确认本地 Composition Root 将四个工具全部冻结进同一个 Registry Snapshot，首次模型调用同时承担“判断是否需要检索”和“从全部工具中 Function Calling”两项职责；信息需求提示只是软约束，不能阻止普通交流误选工具。

V2-O 将其改为两个动态、非业务工作流的权威边界：

- 第一次始终是零工具控制决策。模型只能选择 `answer`、`plan`、`replan`、`request_information` 或结构化 `retrieve`，Provider 请求中不发送任何 Tool Definition。
- `retrieve` 必须给出具体未决 `information_needs` 和最小 `requested_tool_names`；工具名使用 Canonical Literal 合同，并再次校验为当前 Registry 可见集合的子集。
- 只有接受 `retrieve` 后，Runtime 才发起一次独立原生 Function Calling，并通过 `tool_name_filter` 只投影申请的工具；完整 Registry/Context 哈希绑定仍保留，Tool Proposal 因而继续兼容稳定 Action Loop、权限 Guard 和 Tool Gateway。
- 检索调用使用 `tool_choice=required`；工具数量溢出或越界工具名只允许一次同一最小集合内的有界恢复。普通问候和已可回答问题只走控制决策与 Answer Projection，两次调用均不可见工具。
- 未增加关键词分类器、固定问题路由或业务步骤；是否检索、需要什么信息以及申请哪些工具仍由主 Agent 根据当前 Context 动态决定，Runtime 只收窄和校验能力面。

已补充合成用例覆盖普通问候零工具、结构化检索请求、最小工具投影、未知工具拒绝、同一最小集合内的有界恢复及既有 V2-I/V2-K 直接兼容边界。2026-08-25 按授权执行 V2-O 专项 `5 passed`，随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `69 passed`；专项包含于完整回归，因此共覆盖 69 个唯一合成用例。测试全程未调用模型或读取真实资料正文；既有失败 Task 不自动改写或续跑。

同日按后续授权替换启动 9019 V2 独立实例加载 V2-O：Preflight `21/21` 通过，Provider Boundary V2 显式选择，服务进程 PID `43060`，`/health/live` 返回 `ok`、登录页返回 HTTP 200。启动过程按授权读取冻结资料、SecretEnvFile 白名单字段并加载本地 BCE；未提交问题、未调用 DeepSeek，也未操作 9018。

## V2-P：non-strict Structured Output 与 JSON Envelope 有界恢复

V2-O 实例中，同一对话的“你好”已正常完成，但下一句普通交流“好困”在首次 `main_agent_decision` 以 `json_envelope_invalid` 失败，且没有 Tool Call 或 Answer 调用。只读诊断确认 DeepSeek Capability 为 `supports_structured_output=true`、`supports_strict_structured_output=false`；Orchestrator 却只在 strict 可用时创建 Structured Output Spec，导致 Official DeepSeek Codec 没有发送 `response_format={"type":"json_object"}`。模型偶尔自愿返回 JSON，偶尔直接返回自然语言，因此形成与问题措辞相关的随机失败。

V2-P 收敛为以下通用边界：

- Decision、锁定 Plan/Slot Payload 和 Answer 只要 Provider 支持普通 Structured Output 就创建 Schema Spec；`strict` 仍为 `preferred`，不再被错误当作启用 JSON Object 模式的前提。
- Official DeepSeek Codec 因而可稳定发送 JSON Object response format 和对应 Schema 指令；Runtime Pydantic、Grounding 与权限校验仍是最终权威。
- 首次返回非 JSON 对象、重复键、多对象、截断、非对象或非法编码时，Runtime 允许一次同合同、同 Context、同动作/Answer Authority 的重新生成；不得启发式改写模型文本。
- JSON Envelope、Pydantic Schema、Tool Registry/数量和 Answer Grounding 恢复共享同一个一次上限。Envelope 恢复后的对象若仍不满足 Schema 或 Grounding，立即 fail-closed，不追加第二类恢复。
- JSON size limit 不进入重试集合，避免对确定性超限响应做无效重复调用；失败只保留安全错误码、响应哈希与脱敏字段路径，不持久化原始 Provider 内容。

已补充合成用例覆盖 non-strict Structured Output 激活、Next Action Envelope 恢复、锁定 Payload 恢复不重新选择动作、Answer 在同一 Authority 下恢复，以及连续非 JSON 的一次耗尽与 V1 零回退。相关 Python 文件 AST 静态检查 `3/3` 通过。2026-08-25 按授权执行 V2-P 专项 `5 passed`；随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `73 passed`。首轮相邻回归仅发现 V2-D 旧用例仍断言畸形 Decision JSON 不重试；已按 V2-P 合同更新为只允许一次重试、耗尽后拒绝且不进入 Answer，复跑全部通过。测试未调用模型或读取真实资料正文，也未重启 9019。

同日按后续授权重启 9019 加载 V2-P：Preflight `21/21`、健康接口和登录页均返回 HTTP 200，新进程 PID `45824`。随后页面真实使用发现一次跨层缺口，并进入 V2-Q。

## V2-Q：Provider Codec 到 V2 Ingress 的恢复桥

V2-P 实例中的 Task `ffd4120d-1ae4-499b-b7c8-cd6d5f6ff537` 先完成首次决策和 4 次只读检索，第二次 `main_agent_decision` 才以 `response_contract_violation: value is not valid JSON` 失败。根因不是检索或 Answer Guard，而是 `OfficialDeepSeekChatCodec` 在 `ProviderAdapter` 内解析 Structured Output 时先拒绝了无效 JSON；V2-P 的恢复只接收已返回的 `ProviderModelResult`，因此该异常从未到达 Ingress 恢复逻辑。原 V2-P QueueAdapter 合成用例没有覆盖真实 Codec/Adapter 层。

V2-Q 收敛为以下通用边界：

- JSON Object 解析失败按内容无关的枚举分类；Structured Output 只暴露类型化安全错误、Provider 回执和响应哈希，不暴露原始响应。
- 只有 `response_json_envelope_invalid` 可进入一次恢复；`response_json_size_limit` 明确不可重试，HTTP Envelope、Tool 参数和其他合同错误仍立即失败关闭。
- Decision、锁定 Plan/Slot Payload 和 Answer 在 Adapter 层遇到可恢复 JSON 错误时，进入与 V2-P 相同的 JSON Envelope 恢复输入；Schema、Tool、Grounding 与 Envelope 继续共享一次上限。
- 恢复调用若再次在 Codec、Ingress、Schema 或 Grounding 失败，生成带 `repair_attempt=1` 的安全拒绝；不回退 V1、不启发式改写响应、不重新选择已锁定动作或 Answer Authority。
- V1 兼容 Provider 继续把该新类型视作原有结构化修复候选，避免共享 Adapter 错误细化破坏 V1 既有有界恢复。

已新增 6 个真实边界合成用例，直接使用 `OfficialDeepSeekChatCodec + ProviderAdapter + 合成 Transport`，覆盖错误类型化、Decision 恢复、Locked Payload 恢复、Answer 恢复、连续失败耗尽、size limit 零重试。相关 Python 文件 AST 静态检查 `4/4` 通过。2026-08-25 按授权执行 V2-Q 专项 `6 passed`；随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `79 passed`。专项包含于完整回归，因此共覆盖 79 个唯一合成用例；未调用模型或读取真实资料正文。后续按独立授权重启 9019 加载 V2-Q，Preflight `21/21`、健康接口与登录页 HTTP 200，PID `7768`；启动期间未提交问题或调用 DeepSeek。

## V2-R：独立 Guard 收敛与 Runtime-owned Slot Capability

V2-Q 真实使用中，同一 Task 连续四次产生 `limitation_receipt_invalid`，但因为没有 Tool Batch，检索收敛门始终未进入 `saturated`，重复 Answer Guard 拒绝无法触发终止回执；随后模型选择 `request_information`，并在锁定载荷中自行生成 `input_model_ref`，最终以 `locked_action_payload_invalid` 失败。问题不是某个提问文本，而是 Guard 重试权威和 Slot 校验权威落错了层级。

V2-R 收敛为以下通用边界：

- Answer Guard 拒绝次数独立于检索饱和度。相同 Task 的拒绝回执超过一次有界重试后，Runtime 直接生成不含事实主张的可行动限制回答，不再调用模型、继续检索或静默悬挂。
- `SlotValidatorRegistry` 增加显式 `SlotRequestDefinition`。定义只有在其 Pydantic 输入模型和全部业务校验器已注册后才能进入不可变 `SlotCapabilitySnapshot`；空注册表产生空快照，不代表任意 Slot 可用。
- Next Action 的 `request_information` 资格由快照动态控制。没有可执行 Slot 定义时，模型可见允许动作列表不包含该动作，Runtime 即使收到该选择也按 `action_not_available` 最多修复一次。
- 锁定 Slot 载荷只允许模型返回白名单 `slot_kind`、面向用户的请求文本和阻塞原因。`slot_name`、`input_model_ref` 与 `business_validator_refs` 全部由 Runtime 根据快照注入，模型不可读取、发明或覆盖这些内部引用。
- 未知 `slot_kind` 使用 `slot_kind_not_available` 类型化拒绝并共享既有一次有界恢复；Runtime 不猜测别名、不自动创建通用输入模型，也不把格式正确但不可执行的 Slot 写入 pending 状态。
- 本地 Composition Root 从实际 `SlotValidatorRegistry` 冻结快照并交给 V2 Orchestrator。当前 9019 注册表为空，因此 `request_information` 会被安全移出可执行动作面；后续只有显式注册完成格式与业务校验的 Slot 才能重新开放。

已补充合成合同定义，覆盖无检索饱和时的重复 Guard 收敛、空 Slot 快照的动作禁用与修复、不可执行定义拒绝、未知 Slot kind 修复，以及 Runtime 注入内部校验引用。相关代码与测试文件 AST 静态检查 `8/8` 通过。2026-08-25 按授权执行 V2-R 专项 `4 passed`；随后执行 V2-D 至 V2-Q 相邻合同回归 `83 passed`。专项包含于完整回归，因此本轮覆盖 83 个唯一合成用例；测试阶段未调用模型或读取真实资料正文。后续按独立授权替换启动 9019 加载 V2-R：Preflight `21/21` 通过，新进程 PID `39892`，仅监听 `127.0.0.1:9019`，健康状态为 `ok`，登录页返回 HTTP 200。启动过程读取冻结资料和 SecretEnvFile 白名单字段并加载本地 BCE，但未提交业务问题或调用 DeepSeek。既有失败 Task 不自动改写或续跑。

## V2-S：Limitation 权威对齐与 Guard 驱动的证据升级

V2-R 真实使用中的 Task `64503cd3-cba0-488e-8bea-d47a93baefd1` 已完成两类搜索并获得候选片段，但候选均未升级为可引用 Evidence Atom。模型随后用 `resource_identity_receipt` 支撑“企业材料不足”这一 Limitation；Provider Answer 白名单把资源身份回执同时开放为 Runtime Fact 和 Limitation，而 Canonical Grounding Guard 正确地认为该回执只能证明资料身份与加载状态，不能证明某项业务内容缺失，因此连续产生 `limitation_receipt_invalid`，最后进入 Runtime 通用终止回答。问题是同一语义在 Provider 可见合同、Canonical Guard 与恢复动作之间不一致，不是某一个问题文本。

V2-S 收敛为以下通用边界：

- `authorized-resource-identity-receipt` 只保留在 `allowed_runtime_fact_refs`，不再进入 `allowed_limitation_refs`；资源已加载不等于某项材料缺失。
- `limitation_receipt_invalid` 的恢复动作改为“升级现有搜索候选，或使用与 Limitation 语义兼容的回执”，不再要求模型创建其无权创建的 Runtime 双向关系。
- Guard 反馈明确告知模型：Resource Identity Receipt 不能证明业务内容缺失；存在 `search_candidates` 时应先调用 `evidence_read`，把候选升级为 citable Evidence Atom。
- Runtime 新增严格的 `ProviderNextActionRecoveryConstraintV2`。当最新 Answer Guard 拒绝属于证据不足类问题、当前 Context 仍有候选引用且 `evidence_read` 可见时，下一次动作只允许 `retrieve`，且 `requested_tool_names` 必须精确为 `evidence_read`。
- 如果模型仍直接 Answer、Plan、请求信息或选择其他工具，Pydantic/Runtime 返回类型化的 `guard_recovery_action_required` 或 `guard_recovery_tool_required`，并共享既有的一次有界修复；不原样生成第二遍 Answer，也不引入关键词路由或固定业务步骤。
- 重复 Guard 拒绝耗尽后的 Runtime 文案改为面向用户的证据不足说明，不再暴露“支持矩阵”等内部实现术语。

已新增合成合同定义，覆盖资源身份与 Limitation 权威分离、Guard 反馈动作更新，以及“已拒绝 Answer + 可读候选片段”强制进入最小 `evidence_read` Tool 投影的完整边界。相关 6 个 Python 文件 AST 静态检查 `6/6` 通过。2026-08-25 按授权执行 V2-S 专项：首轮业务路径已经正确只生成 `evidence_read`，但一条测试误把 `tool_name_filter` 最小投影视为改写完整 Registry Snapshot，结果为 `2 passed / 1 failed`；修正测试合同后专项 `3 passed`。随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `84 passed`，覆盖既有 V2-D 至 V2-R 及新增 V2-S 用例。测试未调用模型或读取真实资料正文。后续按独立授权替换启动 9019 加载 V2-S：Preflight `21/21` 通过，新进程 PID `37444`，仅监听 `127.0.0.1:9019`，健康接口和登录页均返回 HTTP 200；启动过程读取冻结资料和 SecretEnvFile 白名单字段并加载本地 BCE，未提交问题、未调用 DeepSeek。9018 在启动前后均未监听，本次 V2 专用脚本未操作 9018。既有 Task 不自动改写或续跑。

## V2-T：Pre-Answer Evidence Readiness 与证据升级收敛

V2-S 受控真实矩阵的第 2 题在 5 个检索批次、9 次成功 Tool Call 后失败。Runtime 已持有 37 个搜索候选，但没有调用 `evidence_read`，最终 Answer Context 中为 0 个 Evidence Atom。Provider Answer 及一次结构恢复均生成了缺少 `grounding_refs` 的事实项，因而在 Canonical Guard 之前以 `answer_schema_invalid / grounding_refs.required_for_kind` 终止。V2-S 的恢复只覆盖 Answer Guard 之后，无法处理这一前置缺口。

V2-T 采用以下通用边界：

- Runtime 从已持久化 Tool Observation 计算候选引用、当前 Evidence Atom 数量和已执行的 `evidence_read` 次数；只有搜索候选而没有 Evidence Atom 时，Answer 资格尚未就绪。
- 首次未就绪时签发 `pre_answer_evidence_readiness` 约束，下一动作只允许 `retrieve + evidence_read`；该一次证据升级可以跨过“Search 已饱和”状态，因为 Search 收敛不等于候选已经成为证据。
- `answer_schema_invalid` 若精确属于 `grounding_refs.required_for_kind`，且仍有未升级候选，则转为 `answer_schema_evidence_upgrade`，不再直接把 Task 终止为通用失败。
- `evidence_read` 最多尝试一次；能力不可见、尝试后仍没有 Evidence Atom，或 Answer 仍无法绑定证据时，Runtime 返回不含业务事实的可行动诊断回执，不继续换关键词搜索或无限恢复。
- 该边界只依赖 Context Entry 类型、Tool Observation、Registry 可见性和类型化 Provider 错误，不包含问题关键词、资格分类或固定业务步骤。

已写入 4 个合成合同用例，覆盖候选优先升级、Search 饱和后的单次升级、升级失败的安全回执和 Answer Schema 到证据升级的恢复。2026-08-25 按授权执行 V2-T 专项 `4 passed`；随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `88 passed`，覆盖既有 V2-D 至 V2-S 及新增 V2-T 用例。测试使用合成 Context 与队列 Provider，未调用模型、读取真实资料正文或重启 9019。

2026-08-25 按独立授权在 9019 使用全新对话，从原失败矩阵第 2 题开始执行 V2-T 受控真实复验。冻结香港中心 PDF、冻结企业基线、本地 BCE 和官方 DeepSeek 均在授权范围内使用，Q2—Q5 全部完成，未触发“当前任务未能安全完成”：

- Q2 投标资格综合判断：Task `f22418f0-aa79-448a-b7e4-fe77229fe5e4`，`completed/v14`；招标资料、企业资料和 `evidence_read` 均成功，形成 4 个 Evidence Atom 和 4 条页面引用。
- Q3 工期、投标担保、履约担保与违约责任：Task `e75fa827-0296-46b0-88f1-113700b10315`，`completed/v14`；形成 4 个 Evidence Atom 和 4 条页面引用，对未检得的履约担保及违约责任明确保留限制。
- Q4 完成资格判断仍缺哪些企业材料：Task `5fefeee1-f993-449c-8d9f-60f968d62944`，`completed/v18`；招标、企业检索和 `evidence_read` 均成功，形成 3 个 Evidence Atom 和 3 条页面引用，并明确列出资料不足项。
- Q5 能力介绍：Task `861ce273-06cf-4c26-9b03-e45ad1d53d21`，`completed/v6`；仅执行一次 Decision 和一次 Answer，Tool Call 为 0，符合普通交流零工具边界。

本轮各题均使用独立 Conversation；Q2—Q4 的证据型回答均经过 Runtime 引用投影，Q5 未误触发检索。复验未影响 9018、ECS 或生产环境。

## V2 独立真实复验入口

V2 使用独立启动脚本 `scripts/start_bid_pure_agent_v2_validation.ps1`：

- 默认绑定 `127.0.0.1:9019`；
- 独立数据目录为 `.local-pure-agent-daily-v2-validation`，其中 SQLite、Continuation Secret、日志和 PID 均不与 9018 共用；
- 启动时强制选择 `FEATURE_BID_ASSESSMENT_PURE_AGENT_PROVIDER_BOUNDARY_V2=true`；
- 仍沿用 C08 的本地 SQLite、回环地址、禁用外部 MCP/Milvus/OCR 和显式 Preflight 护栏；
- 登录入口为 `http://127.0.0.1:9019/login`，工作台为 `http://127.0.0.1:9019/admin/bid-assessment-pure-agent`；
- 停止脚本为 `scripts/stop_bid_pure_agent_v2_validation.ps1`，只处理 9019 与 V2 专用 PID 文件。

首次启动需显式传入 `-InitializeLocalDatabase`；已有独立实例需要替换时传入 `-ReplaceLocalInstance`。入口代码已完成 PowerShell 静态语法检查。

2026-08-24 本地启动验收：V2 独立 SQLite 已初始化，Preflight `21/21` 通过，9019 健康检查通过；原 9018 同时保持健康且 PID 未改变。启动过程读取了已授权冻结资料、SecretEnvFile 白名单字段并加载本地 BCE，但未提交问题、未调用 DeepSeek。由于 9019 使用独立账户库，首次使用需在 9019 注册新的本地账号，9018 账号不会被复制。

## 本次明确未做

- 未删除或改写 V1 Provider；默认启动行为仍为 V1。
- 未实现基于模型的 JSON 猜测修复或无限重试。
- 未改数据库、迁移、旧 `bid_intake_*`、功能开关或生产配置。
- V2-T 已完成代码、合同测试定义、短版文档、静态检查、`4/4` 专项、`88/88` 相邻合同回归，以及经独立授权执行的 9019 真实矩阵 Q2—Q5 复验。

## 下一开发切片

V2-T 已完成本地合同验证与受控真实复验。下一步应先完成提交前审计并提交 V2-T 增量；后续若扩大真实试用范围，继续采用“全新独立对话、逐题执行、首个失败停止”的验收边界。既有失败 Task 不自动改写或续跑。
