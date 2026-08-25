# Provider Boundary V2-D 合同矩阵

日期：2026-08-24
范围：本地确定性合同验证；不调用模型、真实资料、Embedding、Reranker、OCR 或外部 MCP。

| 场景族 | Provider 分支 | 核心断言 |
|---|---|---|
| 正常回答 | Next Action → Answer | 恰好两次调用；回答只引用 Answer Context |
| 跨域比较 | Next Action → Answer | 招标与企业证据同时保留；推断绑定双域前提 |
| 多工具调用 | Function Calls | 一次动态决策；每个参数对象有独立 Ingress 收据 |
| 畸形 JSON | Ingress reject | 重复键、多对象、截断、非对象、NaN 使用稳定失败码 |
| 无损结构恢复 | Ingress accept | fence 删除和唯一对象提取有明确收据且字段值不变 |
| 上下文过大 | pre-call reject | `context_not_model_ready`；零 Provider 调用 |
| Guard 拒绝 | Answer reject | 越权 Grounding 与回答 Schema 错误使用不同失败码 |
| 请求澄清 | Next Action | 只调用一次；不提前生成回答 |
| 默认禁用 | pre-call reject | `boundary_disabled`；零 Provider 调用 |
| 可见性与证据资格 | Answer eligibility | Policy、Task、用户消息和历史对话可见但不能支持事实 |
| 普通交流 | Next Action → Answer | `general_advice` 零 Grounding、零伪引用 |
| 跨轮追问 | Answer lineage | 只继承上一 committed Answer 已验证的原始 Evidence Atom |
| 跨资源/过期回答 | lineage reject/skip | superseded、stale、跨 scope 证据不获得 Answer Authority |
| Registry 外工具名 | typed recovery | `tool_name_not_visible`；只允许一次精确 Registry 约束恢复 |
| Registry 恢复到零工具 | Next Action → Answer | 普通交流可改为合法 Answer，不猜测或映射工具别名 |
| Registry 恢复到合法工具 | Function Calls | 只能选择 `allowed_tool_names` 中的精确名称 |
| 重复/交叉 Provider 违规 | typed reject | 工具名与数量恢复共享一次总上限，不叠加恢复 |
| 当前加载资源身份 | Next Action → Runtime Fact | 只使用授权资源身份回执；不要求读取业务正文或伪造 Citation |
| 资源身份与业务事实分离 | Answer eligibility | `allowed_runtime_fact_refs` 与 citable Evidence Atom 白名单互斥 |
| Grounding 类别选错 | Answer-only repair | 一次有界语义修复；结构与 Grounding 修复共享总上限 |
| 普通资源回执冒充身份 | Grounding Guard reject | 只有受控 `RESOURCE_IDENTITY_RECEIPT` 可支持 Runtime Fact |
| 非法来源提示 | Next Action accept | advisory-only 值被确定性过滤；不消耗 Provider 修复次数 |
| 混合来源提示 | Next Action accept | 只保留精确、唯一、顺序稳定的 Canonical `SourceBasis` |
| 来源提示与权威错误并存 | bounded repair/reject | 仅提示字段降级；权威字段仍严格校验且不被掩盖 |
| 唯一动作权威 | Next Action → locked payload | V2 首次选择动作；后续载荷调用不能再次选择或改变动作类型 |
| Plan/Slot 载荷越权 | bounded repair/reject | 夹带 `action_kind` 或错误载荷形状时只修复一次，耗尽后类型化失败 |
| 无信息需求的 Tool 探测 | Next Action without Tool | Function Call 必须绑定当前请求或已接受 Context 的具体未决需求 |
| 文档目录导航 | Function Call arguments | `documents_outline` 必须提供具体 `navigation_goal`，禁止预防性目录探测 |
| 普通问候与闲聊 | zero-Tool Next Action → Answer | 首次控制决策与 Answer 调用均不发送 Tool Definition，不可能误选 Registry 外工具 |
| 结构化检索申请 | Next Action `retrieve` | 必须同时给出具体未决信息需求和最小 Canonical Tool 名称集合 |
| 最小工具投影 | bounded Function Calls | 仅投影已接受 `requested_tool_names`，完整 Registry/Context 绑定保持不变 |
| 检索申请越权 | pre-call reject | 未知工具或不可见工具不能进入 Provider Function Calling，也不做别名猜测 |
| 最小集合内恢复 | bounded recovery | 工具名越界或数量溢出只允许一次同一 Tool 子集内的恢复 |
| non-strict JSON Object | Structured Output | Provider 支持普通 Structured Output 即启用；strict 只作可选增强 |
| Decision 非 JSON | bounded envelope recovery | 同合同、零工具重新生成一次，成功后保留响应哈希与安全恢复收据 |
| Locked Payload 非 JSON | bounded envelope recovery | 同一已锁定动作重新生成，不允许借恢复重新选择动作类型 |
| Answer 非 JSON | bounded envelope recovery | 同一 Answer Authority 和 Grounding 白名单下重新生成一次 |
| Envelope 连续失败 | typed fail-closed | 第二次仍非 JSON 即终止；不回退 V1、不启发式改写、不追加 Schema/Grounding 重试 |
| 跨恢复类型叠加 | bounded reject | Envelope、Schema、Tool 和 Grounding 共享一次 Provider projection recovery 上限 |

该矩阵测试的是问题形态和边界性质，不绑定具体问题文本、PDF 页码、企业名称或某个工具查询词。它用于发现合同层系统性缺陷，不作为真实检索质量或模型效果评测的替代品。

V2-J 增量行已由对应合成测试覆盖。2026-08-24 执行 V2-J 专项 `5 passed`、V2-D/V2-E/V2-G/V2-H/V2-I 相邻合同回归 `44 passed`，并补跑 C04 Persisted Evidence Authority 完整回归 `8 passed`；共覆盖 52 个唯一用例，未调用模型或读取真实资料正文。

2026-08-25 执行 V2-K 增量专项 `6 passed`，随后执行 V2-D/V2-E/V2-G/V2-H/V2-I/V2-J 相邻合同回归 `44 passed`。全部使用本地合成输入和队列 Provider，未调用模型或读取真实资料正文。

2026-08-25 执行 V2-L 专项 `6 passed`，覆盖资源身份独立白名单、Canonical Runtime Fact、一次类别修复、无 Citation 渲染和普通资源回执拒绝；随后 V2-D/V2-E/V2-G/V2-H/V2-I/V2-J/V2-K 完整合同回归 `53 passed`，C03/C04/V606 共享边界回归 `35 passed`，共覆盖 88 个唯一用例。首轮发现并修复通用有界修复收据对 Grounding recovery 的语义缺口；全部复跑通过。未调用模型或读取真实资料正文。

2026-08-25 V2-M 专项 `9 passed`，覆盖全非法值、混合值、重复值、错误形状、检索饱和终态、权威字段同时失败和模型可见精确白名单；随后 V2-D/V2-E/V2-G/V2-H/V2-I/V2-J/V2-K/V2-L 相邻合同回归 `53 passed`，本轮共执行 62 个合成合同用例。未调用模型、读取真实资料正文或加载 BCE。

2026-08-25 V2-N 专项 `7 passed`，覆盖唯一动作权威、锁定 Plan/Replan/Slot 载荷、越权改判修复耗尽、Tool 信息需求绑定和目录导航目标。首次相邻回归发现并更新 V2-I 对扩展 Tool 约束合同的旧精确字典断言；修正后 V2-D/V2-E/V2-G/V2-H/V2-I/V2-J/V2-K/V2-L/V2-M 相邻合同回归 `65 passed`，受影响 V602 Registry/Gateway 回归 `23 passed`，共覆盖 88 个唯一合成用例。未调用模型或读取真实资料正文。

2026-08-25 V2-O 专项 `5 passed`，覆盖零工具首决策、普通问候、结构化 `retrieve`、最小 Tool 投影、未知工具拒绝和同一最小集合内的有界恢复；随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `69 passed`。专项包含于完整回归，因此共覆盖 69 个唯一合成用例。测试未调用模型或读取真实资料正文；后续按独立授权重启 9019 加载 V2-O，Preflight `21/21`、健康状态 `ok`、登录页 HTTP 200，PID `43060`。

2026-08-25 V2-P 增量代码与合成合同用例已完成，覆盖 non-strict Structured Output 激活、Decision/Locked Payload/Answer JSON Envelope 一次恢复、连续失败耗尽和跨恢复类型不叠加。按授权执行 V2-P 专项 `5 passed`；随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `73 passed`。首轮仅发现 V2-D 旧用例仍要求畸形 Decision JSON 零重试；更新为一次有界重试、耗尽即拒绝且不进入 Answer 后全量通过。测试未调用模型或读取真实资料正文；后续按独立授权重启 9019 加载 V2-P，Preflight `21/21`、健康状态 `ok`、登录页 HTTP 200，PID `45824`。

2026-08-25 V2-Q 代码与 6 个真实边界合成用例已完成。新增用例不再使用 QueueAdapter 模拟 Codec 后结果，而是直接覆盖 `OfficialDeepSeekChatCodec + ProviderAdapter + 合成 Transport`，验证 Structured Output JSON 类型化、Decision/Locked Payload/Answer 的 Adapter-to-Ingress 一次恢复、恢复耗尽、V1 零回退和 size limit 零重试。相关 Python 文件 AST 静态检查 `4/4` 通过。按授权执行 V2-Q 专项 `6 passed`；随后执行 V2-D 合同矩阵与 V2-E Runtime 集成完整相邻回归 `79 passed`。专项包含于完整回归，因此共覆盖 79 个唯一合成用例；未调用模型或读取真实资料正文。后续按独立授权重启 9019 加载 V2-Q，Preflight `21/21`、健康接口与登录页 HTTP 200，PID `7768`；启动期间未提交问题或调用 DeepSeek。

## 本地结果

- V2-D 专项：15/15 通过。
- 首轮发现并修复一项通用合同问题：原始 JSON 无损哈希与 Pydantic 默认值补齐后的合同哈希不能混为同一个哈希。当前收据分别保存 `normalized_payload_hash` 和 `validated_contract_hash`。
