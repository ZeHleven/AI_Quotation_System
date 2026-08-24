# Phase 4C-2：真实企业能力基线与七项硬门验收

> 版本：v0.1-r54  
> 日期：2026-08-17  
> 状态：代码、机器合同、隔离动态与浏览器专项均已通过  
> 环境边界：完全隔离本地；默认关闭；禁止应用到 ECS 或生产依赖

## 1. 目标

Phase 4C-1 已经打通 I01—​I11 不可变企业快照、企业 Fact/Link 和 HG01—​HG07。Phase 4C-2 不再增加新的业务权威表，而是解决“真实企业数据如何安全进入现有权威链、冻结前如何验收、报告如何解释未知项”三个落地问题：

1. 冻结前先进行不写数据库、不写对象存储、不写 Audit/Outbox 的服务端校验与版本差异预览；
2. unknown、partial、过期和尚未生效必须保持显式状态，禁止把未核实金额或人天自动写成零；
3. 校验结果生成稳定 Candidate Snapshot Hash，正式冻结时服务端重新计算并拒绝表单漂移；
4. 七项硬门结果给出业务名称、所需 Fact、未解析 Fact 和下一步动作，不把 unknown 推成 pass。

## 2. 数据与迁移边界

- 继续复用 `BidEnterpriseSnapshot`、`BidEnterpriseSnapshotRecord` 和 0104 的 SnapshotRecord→FactAssertion 血缘；
- 不新增表、字段、约束枚举或 Alembic revision；唯一开发 head 保持 `20260817_0104`；
- 真实企业能力正文仍只在冻结动作中写入本地内容寻址对象目录；校验动作不写任何持久化介质；
- 旧 `bid_intake_*`、旧 Agent 权威表和生产 ECS 均不修改；
- `FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_CAPABILITY=false` 默认关闭。

## 3. Baseline Validation 合同

### 3.1 接口

`POST /api/v1/bid-assessment-runtime-lab/enterprise-baseline/validate`

- 仅 localhost Runtime Lab、execute 模式、admin 可见；
- view-only 在中间件和路由两层返回 `BID_MVP1_VIEW_ONLY`；
- 请求复用 Phase 4C-1 的 11 槽 Snapshot Command；
- 响应 Schema 为 `bid.enterprise.baseline-validation.v1`。

### 3.2 校验输出

每个 I 槽输出：

- 原始 `coverage_status` 与考虑有效期后的 `effective_status`；
- `ready/review_required`；
- 来源状态与原因码；
- 相对最新冻结快照的 `added/changed/unchanged`；
- 候选和历史 Payload Hash。

聚合输出：

- supported/partial/unknown/not_yet_valid/expired 计数；
- Candidate Snapshot Hash；
- 当前基线版本和差异数量；
- HG01—HG07 企业侧输入 readiness。HG01 必须等 Run 绑定招标截止事实，因此在基线阶段固定为 `deferred_tender`。

`can_freeze` 表示候选满足机器合同、允许作为带 unknown/partial 的不可变事实基线；`acceptance_ready` 表示 I01 及 HG02—HG07 所需企业侧槽均为有效 supported。两者不可混用。

其中 `verified/imported` 的有效 supported 槽可进入企业侧验收；`self_reported` 可以冻结以保留原始来源状态，但必须保持 `review_required`，不能直接把七项硬门输入标为就绪。版本差异会忽略每次观察必然变化的 `as_of/checked_at`，但来源身份/版本、有效期、coverage 和能力值的变化仍会被识别；冻结 Hash 始终包含完整时间血缘。

## 4. Candidate Hash 冻结围栏

Runtime Lab 前端完成校验后保存 `candidate_snapshot_hash`。任何表单变化都会清除校验结果，冻结按钮随即失效。

正式冻结调用：

`POST /api/v1/bid-assessment-runtime-lab/enterprise-snapshots`

并携带：

`X-Enterprise-Candidate-Hash: <sha256>`

服务端从请求正文重新规范化 I01—​I11、来源、有效期和 `as_of`，重新计算 Snapshot Hash。若与 Header 不同，返回 `BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH`，且不会写对象、快照、审计或 Outbox。原有幂等键继续生效。

## 5. Runtime Lab 录入治理

- 来源名称、来源版本和来源状态必须显式填写；
- 来源状态仅允许 `verified/self_reported/imported`，unknown 只能用于未知槽；
- 统一有效起止时间用于当前候选中所有已知槽；
- I06 资金、I07 保函、I08 投标准备能力增加“已核实”开关；未开启时值为 null/unknown，而不是数值 0；
- 可标记 partial 槽。partial 可以冻结以保留真实数据状态，但不能通过硬门企业侧验收；
- 新版本默认从当前冻结快照回填，必须填写新的来源版本和变更说明后重新校验。

当前页面不会连接 ERP、财务、资质平台或生产数据源。所谓“真实企业能力基线”是指由用户在完全隔离本地环境中录入并确认的真实企业数据，不表示已经完成外部系统集成。

## 6. 七项硬门验收解释

新生成的 HardGate details/Report projection 增加 `acceptance`：

- `label`：业务名称；
- `required_fact_slots`：确定性比较需要的招标/企业 Fact；
- `unresolved_fact_slots`：当前 Run 中不为 supported 的 Fact；
- `enterprise_slot_codes`：对应 I 槽；
- `explanation`：pass/fail/unknown/not_applicable 的确定性解释；
- `next_actions`：补齐企业基线、复核招标事实或负责人决策。

比较权威升级为 `bid-hard-gate-phase4c2-v3`。历史已生成报告保持不可变；只有新 Run/新报告包含该投影。

## 7. 安全不变量

1. 校验接口零持久化，冻结接口仍是唯一企业基线写入口；
2. 浏览器状态不能提升 view-only 权限；
3. unknown/partial/过期/未生效均不能被转换为硬门 pass；
4. Candidate Hash 不一致时 fail closed；
5. 企业正文、密钥、绝对路径和模型上下文不进入 Capability、Preflight、Trace 或校验响应；
6. 本阶段不调用 OCR、视觉、Embedding、Reranker、生成模型或外部 MCP。

## 8. 验证结果

在用户明确授权后，完成以下完全隔离本地验证：

- Phase 4C-2 合同/Schema、Baseline Validate 零持久化、Diff/Hash、来源/partial/unknown/有效期、Candidate Hash、ACL/view-only/幂等和 HG01—HG07 Acceptance：`89 passed / 0 failed`；
- Phase 4C-1、Preflight v2、双模式和 SSE 运行服务相邻回归：`38 passed / 0 failed`；
- API-40/41 全链与 API-41 ACL/幂等/ETag 相邻用例：`2 passed / 0 failed`；
- 0083—0104 单线性迁移拓扑与隔离 upgrade/downgrade：`62 passed / 0 failed`；
- 合计自动矩阵：`191 passed / 0 failed`；Vite 生产构建仍为 `2235 modules transformed`。

一次性 9005 execute 动态中，Baseline Validate 前后均无活动快照，错误 Candidate Hash 返回 `409/BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH`；正确 Hash 冻结成功且同幂等键安全重放。重复校验得到 `no_change=true`、11/11 槽 ready、HG02—HG07 ready，HG01 正确保留 `deferred_tender`。浏览器验证了“校验后可冻结、候选任一字段变化后立即禁用冻结”。

同库重启为 view-only 后，Worker、模型调用与写权限均关闭，历史 11 槽快照可读；Baseline Validate 和 Snapshot Freeze 两个 POST 均返回 `403/BID_MVP1_VIEW_ONLY`。浏览器显示 view-only 告警，上传与“创建新版本”按钮禁用，控制台 `0 error`。一次性 9005 实例已停止，既有 9003 未修改。

本次动态只使用合成企业能力值和确定性本地链；未使用真实 PDF、OCR、视觉、Embedding、Reranker、生成模型、外部 MCP 或任何外部环境。Phase 4C-2 已达到本地业务录入与安全验收门槛，但真实企业台账仍需业务负责人录入/复核，正式发布与 ECS 迁移继续禁止。
