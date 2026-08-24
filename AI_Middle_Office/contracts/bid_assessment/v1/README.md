# 投标机会研判 Agent v1 机器合同

本目录是 `docs/bid-intake-agent-redesign-master-spec-v0.1.md` 的机器可读派生产物。Phase 1 路由与数据域已经分批实现；v0.1-r25 冻结 Phase 3A—3G 完整运行 Profile，v0.1-r28 完成 Phase 4A-1，v0.1-r30 完成 Phase 4A-2；v0.1-r33 已完成 Evidence MCP、事实/门禁/Decision/Claim/Citation/初筛报告与可视化工作流的 MVP-1 隔离本地运行验证，v0.1-r35—r41 依次完成 PDF-C2/C3 与 RQ1-A—D，v0.1-r42—r44 完成 RQ2-A 语义召回、RQ2-B 候选融合和 RQ2-C 轻量重排，v0.1-r45 完成三项目 Development 正式 A/B，v0.1-r46—r50 完成 DeepSeek 接入、真实资料演示与本地安全运行面，v0.1-r52—r59 完成企业能力快照、七项硬门、业务验收、MVP RC、真实来源业务基线和决策复验。v0.1-r60 开始 Phase 4D-2，以内容寻址 Evidence Item、显式 I01—I11 Evidence Package 和 Baseline/Run/RC 全血缘绑定接入真实企业资料；当前仅完成静态实现，尚未运行专项或导入真实企业资料。该结果不表示生产上线已经完成，也不替换现有 `app/agents/bid_intake` 运行时。

## 产物

- `manifest.json`：合同版本、来源和文件入口；
- `phase3-runtime-profile.json`：Phase 3A—3G 完整运行链、主开关依赖闭包、终态不变量和外部执行边界；
- `phase4a1-runtime-profile.json`：Plan Continuation + SkillBinding 实现边界、开关、版本和迁移 head；
- `phase4a2-runtime-profile.json`：Model Gateway、单 Task 有界 LangGraph、权威表、验证器和外部执行关闭边界；
- `phase4b1-deepseek-v4-flash-profile.json`：官方 DeepSeek V4 Flash Provider、精确模型/Host、非思考 JSON、成本与本地显式启用边界；
- `phase4b2-deepseek-isolated-mvp1-profile.json`：真实 DeepSeek + 本地 Evidence MCP 合成全链、控制字段规范化、失败响应账本和隔离边界；
- `phase4c1-enterprise-hard-gates-profile.json`：Phase 4C-1 I01—​I11 不可变企业能力快照、P1 事实物化、企业事实血缘与 HG01—​HG07 确定性业务闭环；
- `phase4c2-enterprise-baseline-acceptance-profile.json`：Phase 4C-2 无持久化基线校验/差异、候选 Hash 冻结围栏、真实来源与有效期治理、七项硬门验收解释；
- `phase4c3-mvp-release-candidate-profile.json`：Phase 4C-3 业务复核、Candidate Hash、不可变 RC、ACL/view-only 与迁移边界；
- `phase4d1-business-baseline-revalidation-profile.json`：Phase 4D-1 I01—I11 真实来源核验、不可变业务基线和历史 RC 决策复验边界；
- `phase4d2-enterprise-evidence-import-profile.json`：Phase 4D-2 内容寻址企业资料、显式槽位映射、资料包、业务基线及 Run/RC 血缘边界；
- `phase4d3-fact-verification-comparability-profile.json`：Phase 4D-3 招标/企业事实核验、Atom/Item 证据门、Comparison Baseline、Run 绑定和 HG01—HG07 可比化边界；
- `fact-catalog-mvp1.json`：MVP-1 招标事实与企业事实槽位；
- `pdf-c1-chunk-profile.json`：PDF-C1 三层 Chunk 边界、Token 和引用权威；
- `pdf-c2-native-layout-profile.json`：PDF-C2 原生布局、坐标、质量和启用边界；
- `pdf-c3-role-aware-retrieval-profile.json`：PDF-C3 检索角色、索引权威、读扩展和失效规则；
- `rq1a-structure-profile.json`：RQ1-A 重复页边降噪、层级/表格聚合、标题可引用化和兼容门禁；
- `rq1b-parse-quality-profile.json`：RQ1-B 四维质量评分、review/blocked 状态和下游消费阻断门禁；
- `rq1c-query-optimizer-profile.json`：RQ1-C 确定性拆解、字段/答案形状扩展、查询预算、加权 RRF 和冻结 Search Adapter；
- `rq1d-field-aware-lexical-profile.json`：RQ1-D 字段通道投影、BM25F、模板高频抑制、原查询保护、内容寻址缓存和冻结 v4 Search Adapter；
- `rq2a-semantic-retrieval-profile.json`：RQ2-A Child-only BCE/Milvus 语义索引、Lease/Fencing、Provider Hash 验证和冻结 v5 Semantic-only Search Adapter；
- `rq2b-candidate-fusion-profile.json`：RQ2-B BM25F/Semantic Top-40 候选、稳定 Key 去重、加权 RRF、双通道重合奖励和冻结 v6 Search Adapter；
- `rq2c-lightweight-rerank-profile.json`：RQ2-C 冻结 Top-20 BCE Cross-Encoder、词法锚点保护、正分差尾部替换和冻结 v7 Search Adapter；
- `rq2-closeout-cross-project-profile.json`：RQ2 总收口的跨项目 Gold/Holdout 规模、反泄漏、冻结快照、一次性 Holdout 和质量/时延/安全准出门；
- `skills/catalog-1.0.0.json` 与 `skills/*`：只追加、内容寻址的历史 Skill catalog/artifact；
- `skills/catalog-1.1.0.json`：追加 `bid-enterprise-capability` 确定性 Skill 的 Phase 4C-1 目录；
- `task-catalog-1.1.0-phase4c1.json`：冻结 49 项标准任务的 Phase 4C-1 目录版本，并把既有 `build_enterprise_snapshot` 绑定到新的确定性物化语义；
- `fact-catalog-mvp1-phase4c1.json`：在历史 1.0 Fact Catalog 之外增加 I01—​I11 与结构化项目主体，只有 Phase 4C-1 开关开启时读取；
- `state-transitions.json`：Assessment、Run、Upload Batch 等状态机；
- `error-codes.json`：稳定业务错误码与 HTTP 映射；
- `event-catalog.json`：Public SSE 与内部 Outbox 事件目录；
- `decision-compatibility.json`：最终决策和投入等级兼容矩阵；
- `task-catalog.json`：第 7.6 节冻结的 49 个标准任务类型；
- `task-catalog-1.0.0-draft.1.json`：Phase 4 Plan 引用的只追加历史 Task catalog；
- `../../../schemas/bid_assessment/v1/contracts.schema.json`：JSON Schema 2020-12 合同包；
- `../../../schemas/bid_assessment/v1/tools.schema.json`：25 个模型可见工具的参数 Schema；
- `../../../schemas/bid_assessment/v1/task.schema.json`：TaskDefinition、TaskContract、Lease、Checkpoint 与完成回执；
- `../../../schemas/bid_assessment/v1/planner.schema.json`：PlannerInput、PlanProposal 与可复现 Plan Commit envelope；
- `../../../schemas/bid_assessment/v1/fact.schema.json`：Assertion、Slot Coverage 与 Resolved Fact；
- `../../../schemas/bid_assessment/v1/dimension.schema.json`：七维统一输出；
- `../../../schemas/bid_assessment/v1/decision.schema.json`：确定性决策输出与兼容矩阵；
- `../../../schemas/bid_assessment/v1/report.schema.json`：不可变报告、Claim、Citation 与 Delta；
- `../../../schemas/bid_assessment/v1/context.schema.json`：可复现 Context Manifest；
- `../../../schemas/bid_assessment/v1/model-roles.schema.json`：Local Research、Synthesizer、Evidence Validator 边界；
- `../../../schemas/bid_assessment/v1/model-execution.schema.json`：Local Agent State、受控动作、模型请求/Claim/Provider 结果与回执；
- `../../../schemas/bid_assessment/v1/execute-preflight-v2.schema.json`：增加企业能力快照阻断项的 Execute Preflight v2；
- `../../../schemas/bid_assessment/v1/enterprise-capability.schema.json`：企业快照命令/投影、无持久化 Baseline Validation、企业事实血缘与硬门比较摘要；
- `../../../schemas/bid_assessment/v1/mvp-release-candidate.schema.json`：业务验收命令、零持久化校验与不可变 MVP Release Candidate 投影；
- `../../../schemas/bid_assessment/v1/evidence-chunk.schema.json`：PDF-C1 Parent/Child/Atom 输出；
- `../../../schemas/bid_assessment/v1/pdf-native-layout.schema.json`：PDF-C2 页面与结构 Block 输出；
- `../../../schemas/bid_assessment/v1/evidence-retrieval.schema.json`：PDF-C3 Child→Atom 检索索引快照；
- `../../../schemas/bid_assessment/v1/semantic-retrieval.schema.json`：RQ2-A Child Embedding 索引、模型血缘与向量 Hash 快照；
- `../../../schemas/bid_assessment/v1/candidate-fusion.schema.json`：RQ2-B 双通道候选、排名、融合分与 Hash 合同；
- `../../../schemas/bid_assessment/v1/lightweight-rerank.schema.json`：RQ2-C 模型血缘、候选输入、分数、锚点、promotion 与 Hash 合同；
- `../../../schemas/bid_assessment/v1/retrieval-benchmark.schema.json`：RQ2 跨项目 Gold/Holdout Dataset、Development Snapshot、Pre-Holdout Freeze、单次执行 Ledger 与 Report Manifest 合同；
- `../../../openapi/bid-assessment-v1.openapi.json`：OpenAPI 3.1 外部接口合同。

## 约束

1. 新合同版本只能向后兼容地增加可选响应字段；删除字段、改变语义或改变资源身份必须发布新版本。
2. 金额、数量和高精度比例在 JSON 边界使用十进制字符串，禁止使用 JSON number 作为正式计算输入。
3. 所有请求对象默认 `additionalProperties: false`。
4. 现有 `/api/v1/admin/bidding/projects/.../bid-intake/...` 继续属于旧运行时；本合同的资源前缀为 `/api/v1/bid-assessments/...`。
5. `FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME=false` 默认关闭；启用时必须同时启用 V1 Runtime、Phase 3A—3G 七个阶段开关并满足 Tool scope signing key 门禁，缺少任一依赖都在配置加载时 fail closed。
   `FEATURE_BID_ASSESSMENT_PHASE4_MVP=false`、`FEATURE_BID_ASSESSMENT_PHASE4_PLAN_CONTINUATION=false`、`FEATURE_BID_ASSESSMENT_PHASE4_LOCAL_AGENT=false` 和 `FEATURE_BID_ASSESSMENT_PHASE4_MODEL_EXECUTOR=false` 同样默认关闭；A-2 两个子开关必须成对启用并依赖 Phase 3 完整 Profile 与 A-1，总开关仍要求后续 Phase 4 所有切片全部就绪，禁止半链进入运行态。
6. 代码迁移唯一 head 为 `20260817_0106`；0100 新增 FactAssertion/EvidenceLink/Coverage/ResolvedFact/Head，0101 新增 HG01—HG07/Decision/Claim/Citation/Report 权威，0102 新增 role-aware RetrievalIndex/Entry/Head，0103 新增 SemanticIndex/Entry/Head，0104 新增不可变 Enterprise SnapshotRecord→FactAssertion 血缘，0105 仅新增不可变 MVP Release Candidate，0106 仅新增不可变 Enterprise Business Baseline。RQ2-B/RQ2-C 与 RQ2 总收口都只派生请求级排序或离线评测产物。Evidence MCP 只读取当前 Run Manifest 中与 ParseHead/RetrievalHead/SemanticHead 一致的就绪索引；真实 OCR/视觉、生成模型、公网检索和生产对象存储默认关闭。所有 Agent migration 在用户确认全部开发完成并允许上线前不得进入正式发布候选或应用到目标 ECS。

## 验证门禁

以下命令属于报价资料研判 Agent 测试，必须先取得用户明确许可；未获许可时只允许进行 JSON/Schema 的静态解析检查。

```powershell
& .\.venv-agent\Scripts\python.exe -m pytest tests\test_bid_assessment_contracts_v1.py -q
```
