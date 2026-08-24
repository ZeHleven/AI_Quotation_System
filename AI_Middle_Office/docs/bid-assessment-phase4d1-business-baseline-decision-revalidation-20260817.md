# Phase 4D-1：真实企业能力基线与业务决策复验协议

> 版本：v0.1-r59  
> 状态：代码、机器合同、迁移、隔离动态与浏览器专项验证完成  
> 边界：仅隔离本地开发；默认关闭；不得应用到 ECS

## 1. 要解决的问题

Phase 4C 已经能冻结 I01—I11 企业快照、跑七项硬门并冻结 MVP RC，但“企业快照已冻结”只说明数据结构和 Hash 受治理，不等于真实来源已由业务负责人核验。因此 Phase 4D-1 增加一层不可变业务核验权威，并用它重新运行同一 Assessment，比较新旧 Decision 与 HG01—HG07。

## 2. 权威边界

- `BidEnterpriseSnapshot`：保存 I01—I11 的结构化值、覆盖状态、来源元数据和内容寻址对象；
- `BidEnterpriseBusinessBaseline`：逐槽保存业务复核结论、来源类别、逻辑来源编号、可选来源 SHA-256、说明和核验人；
- `BidAnalysisRun`：在新开关开启时只选择已冻结业务基线对应的企业快照，`input_fingerprint` 同时绑定 Baseline Version/Hash；
- `BidMvpReleaseCandidate`：继续作为唯一 RC 权威，不新增第二套决策表；新的 RC Manifest 绑定旧 RC、真实 Baseline 以及 Decision/七项硬门差异。

网页上的“真实”标签没有授权效力。Validate 只生成 Candidate Hash，不持久化；Freeze 必须由服务端重算同一 Hash，并经过 admin、execute、view-only 硬阻断和幂等事务门。

## 3. I01—I11 来源核验

允许的来源类别：

- `official_document`：政府、发证机构或正式签发文件；必须提供逻辑引用和 SHA-256；
- `internal_system`：企业内部权威系统记录；必须提供逻辑引用和 SHA-256；
- `audited_record`：审计或已复核台账；必须提供逻辑引用和 SHA-256；
- `management_attestation`：业务负责人确认；必须说明，结果为 `verified_with_follow_up`；
- `not_available`：当前无可用来源，只允许用于显式 `unknown`，必须说明。

绝对路径、URL 和来源正文不得进入 API 投影或运行轨迹。`partial`、`unknown`、`self_reported` 和管理层确认不会被提升成完全核验；它们只能形成带跟进项的业务基线。

核验命令必须携带 15 分钟内的 `reviewed_as_of`。服务端在核验时检查有效期，在 Run Bootstrap 时按数据库 evaluation time 再检查；过期或尚未生效的来源会阻断新 Run。

## 4. 决策复验

复验不是调用第二个模型比较文本，而是复用原 Phase 3/4 运行链：

1. 选择同一 Assessment 已冻结的历史 MVP RC 作为来源；
2. 以经业务核验的新企业快照创建新 Run；
3. 复用事实权威、HG01—HG07、Decision、Claim、Report 和 Run Validation；
4. RC Validate 比较来源/目标 Decision，以及 HG01—HG07 的逐项状态；
5. Candidate Hash 绑定 Source Release Hash、Business Baseline Hash 和全部差异；
6. 人工确认后仍冻结到 `bid_mvp_release_candidates`，保留单一发布权威。

来源 RC 缺失、跨 Assessment、目标 Run 未绑定真实 Baseline、任何权威 Hash 漂移或人工复核未完成都会阻断冻结。

## 5. 数据与迁移

- 新表：`bid_enterprise_business_baselines`；每个企业快照最多一个不可变业务基线；
- 新 revision：`20260817_0106`，线性下接 `20260817_0105`；
- 降级门：表内存在任何核验血缘时拒绝 downgrade；offline downgrade 也拒绝；
- 新开关：`FEATURE_BID_ASSESSMENT_PHASE4_BUSINESS_BASELINE=false`；关闭时 Phase 4C 行为不变；
- 不修改旧 `bid_intake_*`，不增加 Worker、模型、Tool 或 Outbox 类型。

## 6. Runtime Lab

新增三项本地 admin 能力：

- GET 当前企业快照对应的业务基线；
- POST 零持久化 Validate；
- POST 带 Idempotency-Key 和 Candidate Hash 的不可变 Freeze。

界面逐项展示 I01—I11 的覆盖状态、来源类别、引用、Hash 和说明；RC 校验面板展示新旧 Decision 与 Baseline Hash。启动器仅在显式 `-EnableBusinessBaseline` 且 execute 模式下启用 Phase 4D-1，默认 Lab 名称隔离为 `phase4d1`。

## 7. 本地隔离验证结果

经用户明确授权，完成以下互不重复的自动化矩阵，共 `239 passed / 0 failed`：

- Phase 4D-1 合同/Schema、0106 upgrade/downgrade、0083—0106 线性迁移拓扑、核心服务与状态矩阵：188项；
- Phase 4C-1/4C-2/4C-3 相邻回归及历史 RC → 新 Run → Decision/HG01—HG07 差异绑定：24项；
- Execute Preflight 相邻回归：4项；
- Runtime/API-41/SSE 相邻回归：19项；
- API-40/API-41 与 Run Bootstrap 相邻回归：4项。

一次性本地 `9010` execute Lab 的动态结果：

- 企业快照首次冻结与幂等重放均返回成功；业务基线首次冻结与幂等重放均返回成功；
- 修改快照 `source_version` 后以旧 Candidate Hash 冻结，返回 `409 BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH`；修改业务复核结论后以旧 Candidate Hash 冻结，返回 `409 BID_ENTERPRISE_BUSINESS_CANDIDATE_HASH_MISMATCH`；失败请求没有污染现有 Preflight；
- 新 Run 的 Bootstrap 审计同时固化 `enterprise_business_baseline_version` 与 `enterprise_business_baseline_hash`，与不可变业务基线权威行逐字一致；
- 合成确定性全链 `Run=succeeded`、`Report=ready`、`Run Validation=passed`，27个 Task、78个 Attempt、31次确定性 Model Call、20次 Tool Call、89个 Checkpoint；由于 I01—I11 均被明确核验为 unknown，HG01—HG07 全部保持 unknown，Decision 为 `insufficient`，没有把缺失企业能力误推成 pass；
- Runtime Lab 从两项阻断依次降为零阻断，显示 `EXECUTE · 可运行`；实际 Run Trace、SSE、Report 和七项硬门均可读，浏览器控制台无错误；
- Phase 4D-1 RC 复验对没有历史 Phase 4C-3 RC、且引用不满足 Atom-only 的合成旧 Run 分别以 `DECISION_REVALIDATION_SOURCE_BOUND`、`CITATIONS_ATOM_ONLY` 拒绝冻结，符合 fail-closed 设计。历史 RC 绑定与 Decision/HG 差异算法由上述自动化相邻测试覆盖。

验证后已停止一次性 `9010`；原本地 `9003` 仍在监听且未被改动。自动化有一条既有 Pydantic `schema` 字段遮蔽告警，不影响结果。

## 8. 仍未解除的边界

- 本轮只使用隔离 SQLite、本地对象目录、合成文本和确定性测试 Provider；没有使用真实企业来源、真实 PDF、BCE、OCR/视觉、生成模型、外部 MCP、生产 Milvus 或任何外部环境；
- “真实企业能力基线”表示协议与权威结构已具备，当前动态数据仍是显式 unknown 的隔离验收数据，不构成真实投标结论；
- 所有开关默认关闭，Alembic head 为 `20260817_0106`；不得将本阶段代码或迁移应用到 ECS。
