# 旗胜投标机会研判 Agent Phase 4 MVP-1 垂直闭环协议

版本：v0.1-r33  
日期：2026-08-13  
状态：完全隔离的本地运行与专项验证完成；真实模型/真实资料/OCR/视觉仍未执行

## 1. 交付目标

MVP-1 把新数据域第一次闭合为可操作产品路径：用户创建 Assessment、上传资料并提交不可变 Manifest，Phase 2 Worker 解析正文并生成正文证据和标段候选；用户选择标段后，Phase 3/4 控制平面执行 P0—P4 DAG，通过 Evidence MCP 检索和精读证据，形成事实、HG01—HG07、确定性 Decision、Claim/Citation 和不可变初筛报告。运行轨迹和报告在同一工作台展示。

MVP-1 不读取旧 `bid_intake_*` 权威表，不根据文件名、MIME 或 `parser_hint` 推断标段，不允许模型直接写 Decision、Report 或 Run 终态。

## 2. 运行链

```text
API-01 → API-10/12/15 → Parse Worker → Lot Worker → API-31
  → Run Bootstrap → P0—P4 Plan Continuation
  → Task Lease/Fence → 单 Task LangGraph
  → Model Gateway → Tool Gateway → Evidence MCP search/read
  → FactAssertion → Coverage → ResolvedFact Head
  → HG01—HG07 → deterministic Decision
  → Claim/Citation Validation → immutable Report
  → Run Validator → Run/Assessment convergence → API-60/61 + SSE
```

## 3. Evidence MCP 与 Query 处理

- `evidence.search` 只检索 Run 冻结 Manifest 中、当前 ParseHead 指向 ParseRun 的 `BidEvidenceFragment.normalized_text`。
- 查询先复用旧 Agent 的确定性 Query Planner 与检索路由；当前进程内 Adapter 使用 BM25 + RRF。语义/混合路由在语义后端未配置时显式降级，并返回 `SEMANTIC_BACKEND_UNAVAILABLE_BM25_FALLBACK`，不伪装向量召回。
- `evidence.read` 最多读取 12 条、总计 12,000 字符，支持当前片段、邻近片段、父节点和受限页范围。
- `evidence.search` 结果的 `context_read=false`，只有 `evidence.read` 结果才能令片段成为 Fact/Claim 证据。
- Adapter 位于 Phase 3 Tool Gateway 之后，沿用 Tool Schema、预算、Invocation、AsyncOperation、Dispatch/Attempt、Result Store、Checkpoint 和 Fencing。
- 同一服务可通过 MCP transport 工厂独立承载；MVP-1 默认采用等价的进程内只读 Adapter，避免开放未经签名的旁路。
- `facts.query` 只读取当前 Run 的 `ResolvedFactHead`、Coverage 和可选的已接受 Assertion；模型收到的 `runtime_tools` 是 TaskContract `allowed_tools` 与已注册 Adapter 的交集，合同上限不会被误当成实际可执行能力。

## 4. 事实与报告权威

`20260813_0100` 新增：

- `bid_fact_assertions`
- `bid_fact_evidence_links`
- `bid_fact_coverages`
- `bid_resolved_facts`
- `bid_resolved_fact_heads`

事实候选必须同时通过 TaskType→FactSlot、Run Scope、冻结评估时刻、当前 Manifest/ParseHead、当前 Task Checkpoint、ModelResult 和已观察 `evidence.read` Result 血缘校验。冲突不覆盖，Resolution 只追加并由 Head 指向当前版本。

`20260813_0101` 新增：

- `bid_hard_gate_results`
- `bid_preliminary_decisions`
- `bid_report_claims`
- `bid_claim_citations`
- `bid_report_validations`
- `bid_preliminary_reports`

HG01—HG07 均由确定性代码读取 ResolvedFact；缺失或冲突输入保持 `unknown`。Decision 只按 Gate/Facts 的冻结输入计算。Claim 必须引用 Run 内的 Fact/Gate；事实型 Claim 必须有 Citation。报告正文和 Hash 不可变，新 Manifest 只将旧报告标记为 `stale`。

首版企业资格、人员业绩、合规、保证金资金、最低投标能力和禁投风险尚无新数据域读 Adapter，因此 HG02—HG07 在没有权威企业事实时为 `unknown`，Decision 保守输出 `insufficient/hold`。该行为是安全边界，不代表误判为通过。

## 5. API 与页面

- API-60：`GET /api/v1/bid-assessments/{assessment_id}/reports`
- API-61：`GET /api/v1/bid-reports/{report_id}`，返回强 ETag
- 工作台：`/admin/bid-assessment-runtime-lab`

工作台已接通创建 Assessment、上传多文件、SHA-256、提交 Manifest、轮询解析/标段、选择标段、观察 Run/SSE、读取报告和 Citation 的真实 API。`sessionStorage` 仅保存最近 Assessment ID，权威状态始终重新从 API 获取。

## 6. 开关与执行边界

以下开关全部默认 `false`：

- `FEATURE_BID_ASSESSMENT_PHASE4_MVP`
- `FEATURE_BID_ASSESSMENT_PHASE4_EVIDENCE_MCP`
- `FEATURE_BID_ASSESSMENT_PHASE4_FACT_AUTHORITY`
- `FEATURE_BID_ASSESSMENT_PHASE4_PRELIMINARY_REPORT`

它们依赖 V1 Runtime、Phase 3 完整运行 Profile、Phase 4A-1/A-2 和 Tool scope signing key。Model Executor 还要求受控 Provider key 与 HTTPS Endpoint。缺少任一依赖时配置加载 fail closed。

`127.0.0.1:9001` 现在可运行专用 `app.mvp1_local:app`：只绑定 localhost，使用 `.local-mvp1/runtime.db`、`.local-mvp1/objects`、进程内 Outbox/Worker 和 `DeterministicMvp1LocalProvider`。它不导入正式应用 lifespan，不使用 Celery/Redis/MinIO，不调用公网、真实模型、OCR 或视觉。启动脚本为 `scripts/start_bid_assessment_mvp1_local.ps1`，停止脚本为 `scripts/stop_bid_assessment_mvp1_local.ps1`；合成样例和验证器分别为 `samples/mvp1-local-demo-tender.txt`、`scripts/verify_bid_assessment_mvp1_local.py`。不得把 0100/0101、开关或该实验环境应用到 ECS。

## 7. 迁移门禁

代码唯一线性 head：`20260813_0101`。目标 ECS 最近只读 head 仍为 `20260808_0082`，禁止连接或迁移。

只允许在新建的独立本地/开发数据库执行 0083→0101。运行前必须确认数据库 URL 未指向 ECS/旧 CentOS/正式 MinIO/Redis；升级与降级拓扑、空库/有数据 downgrade 保护、API/Worker 同版本和 0100/0101 权威血缘需经专项验证。两次 downgrade 在任一新权威表有数据时拒绝执行。实验台 SQLite 由测试 Harness 从 SQLAlchemy metadata 显式物化且不写 `alembic_version`，不能作为部署迁移证据；0083—0101 生产迁移仍须在独立 MySQL 开发库验证后才可进入后续上线门禁。

## 8. 当前验证状态

已完成：

- Python `compileall`：通过；
- Vite 生产构建：2235 modules，通过；
- 机器 JSON 静态解析：通过。
- 合同、迁移拓扑、0100/0101 隔离执行、Planner/LangGraph/配置门禁：`160 passed / 0 failed`。
- 新数据域 API、Phase 2 Worker、Phase 3 运行服务、事务/ACL/幂等/ETag、Lease/Fencing、Checkpoint、取消/超时/恢复、API-41/SSE 与 MVP-1 报告链：`150 passed / 0 failed`。
- 合成 TXT 本地 HTTP 全链：Assessment→SHA-256 上传→Manifest→原生文本解析→标段选择→P0—P4→Report→Run Validation，结果 `Run=succeeded`、`Report=ready`；Trace 为 474 nodes / 500 timeline items。

累计专项 `310 passed / 0 failed`。未执行真实资料、OCR/视觉解析、真实模型、外部 Tool、真实 MinIO/Redis 或任何 ECS/CentOS 操作；合成 Provider 的结论只用于验证运行链，不能作为真实投标结论。
