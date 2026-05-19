# 系统升级路线图

> 最后更新：2026-05-19

> 业务优化 P4 更新：真实用户体验优化代码层已落地，`index.html` 新增失败原因建议、预审风险提示和人工修改沉淀知识入口；本阶段不新增 Alembic revision。
> P4 运行态手工验收已完成（2026-05-08）：22/22 项全部通过，详见 `AI_Middle_Office/P4_ACCEPTANCE.md`。
> AI 平台架构升级 Phase 0 已完成开发与当前环境验证（2026-05-18）：RBAC、`role_version`、Vite 壳、`/login`、`/admin/permissions`、SPA fallback 已通过；旧页面保留。正式生产上线尚未发生，未来需单独 Runbook。总控文档见 `docs/superpowers/specs/2026-05-14-ai-platform-upgrade-design.md`。
> AI 平台架构升级 Phase 1 报价速度看板已完成当前环境运行态验收（2026-05-18）：新增报价速度聚合接口、`FEATURE_DASHBOARD_QUOTE` 和 `/admin/dashboard` 看板视图；已修复 AI 生成耗时写入与历史成功任务耗时回填；页面、看板数据和新增真实报价统计均已确认正常。正式生产启用仍需单独 Runbook。
> AI 平台架构升级 Phase 2 响应速度追踪已完成当前环境运行态验收（2026-05-18）：`FEATURE_CLIENT_INQUIRY` 与 `FEATURE_DASHBOARD_RESPONSE` 已在内网环境打开，响应速度看板展示 1 条可信样本，平均首次响应 15 分钟；已修复 Celery worker Phase 2 元数据加载问题。正式生产启用仍待系统整体完善后统一 Runbook。
> AI 平台架构升级 Phase 2.5 管理员报价运营闭环已完成当前环境验证（2026-05-18）：复用现有报价任务、咨询记录和报价历史数据，在 `/admin/dashboard` 增加“报价运营”视图；支持状态/来源/客户关键词/提交人筛选、提交人独立列、任务详情、重试、取消和超时标记。该阶段为后台体验补强，不启动 Phase 3 的执行任务模型。
> AI 平台架构升级 Phase 3 执行速度追踪已完成当前环境验证（2026-05-19）：新增 `execution_tasks` / `execution_task_events`、`FEATURE_EXECUTION` / `FEATURE_DASHBOARD_EXECUTION`、执行任务 CRUD/取消接口、执行速度聚合接口、`/admin/execution` 任务页和 `/admin/dashboard` 执行速度标签页；当前环境已打开开关并验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已显示取消数量。
> 产品边界：系统完善前不正式投入生产使用；后续各阶段先按内网开发/验证推进，等平台能力完整后再统一准备正式生产 Runbook。

---

## 当前冻结状态（2026-05-18）

- **后端基础设施优化已收束**：后端重构 P0-P3、补充一致性优化、运维告警收敛、Alembic 迁移纪律均已落地；后续不再做无触发的基础设施打磨。
- **数据库规范**：当前代码迁移 head 为 `20260514_0013`；内网验证数据库若低于 head，需执行 Alembic 升级后启用完整报价反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 响应速度追踪和 Phase 3 执行速度追踪。后续新增字段或表必须新增 Alembic revision，不能退回依赖 `AUTO_CREATE_TABLES` / 启动兼容迁移。
- **业务优化 P0-P4 已完成到代码层**：P0 报价反馈闭环、P1 Admin 反馈分析、P2 Prompt 回归评测、P3 知识库候选治理、P4 真实用户体验优化均已落地。
- **前端优化 P0-P3 已完成**：已完成 `FRONTEND_ACCEPTANCE.md`、`static/js/shared.js`、`admin.html` 模块拆分，以及报价进度、失败恢复、上传/推送状态优化。
- **AI 平台升级 Phase 0 已完成开发与当前环境验证**：Vite 壳、登录鉴权统一、RBAC 与 SPA fallback 已通过；旧 `index.html` / `admin.html` / `app.html` 继续保留，后续按设计文档逐步收拢入口。正式生产上线待单独 Runbook。
- **AI 平台升级 Phase 1 报价速度看板已完成当前环境运行态验收**：已新增报价速度聚合接口和 Vite 看板入口，功能开关已在当前环境打开；已修复新报价任务 `duration_ms` 实测写入，并在备份后回填历史 121 条成功任务的 0 耗时记录；页面、看板数据和新增真实报价统计均已确认正常；未迁移旧业务页面，正式生产启用待单独 Runbook。
- **AI 平台升级 Phase 2 响应速度追踪已完成当前环境运行态验收**：已新增 `client_inquiries`、`quote_jobs.client_inquiry_id`、咨询查询/修正接口、响应速度聚合接口和 Vite 响应速度视图；当前环境 Alembic 已升级到 `20260514_0012 (head)`，`FEATURE_CLIENT_INQUIRY=true`、`FEATURE_DASHBOARD_RESPONSE=true`、`PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已展示 1 条可信样本，平均首次响应 15 分钟，SLA 达标率 100%，并修复 Celery worker Phase 2 元数据加载问题。
- **AI 平台升级 Phase 3 执行速度追踪已完成当前环境验证**：已新增执行任务 schema、任务接口、事件记录、执行速度聚合和 Vite 执行任务页面；当前环境已打开 `FEATURE_EXECUTION=true`、`FEATURE_DASHBOARD_EXECUTION=true` 且 `PUBLIC_ACCESS_ENABLED=false`，已验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已补充取消数量。
- **正式生产策略**：系统整体完善前不正式投入生产使用；不再为单一阶段单独做正式生产上线闭环。
- **维护策略**：进入问题驱动维护阶段，优先投入报价准确率、知识库质量、RAG 评测和真实用户体验问题。

## 业务优化路线（2026-05-07）

### 状态总览

| 状态 | 事项 | 后续处理 |
|------|------|----------|
| 已完成 | 后端基础设施 P0-P3、补充一致性优化、运维告警收敛 | 冻结，后续只按真实问题增量维护。 |
| 已完成 | 前端优化 P0-P3 | 冻结，后续只按真实体验问题调整。 |
| 已完成 | 业务优化 P0-P4 | 已完成到代码层，真实运行验收随下一轮报价继续确认。 |
| 未完成 | P2 候选 prompt 自动重跑 | 等样本量、Dify 调用路径和成本策略明确后再做。 |
| 已完成 | P3 Admin 知识候选审核面板 | 已接入 admin.html 并完成手工验收（2026-05-08）。 |
| 已完成 | P4 真实用户体验运行态手工验收 | 2026-05-08 手工验收通过，22/22 项全部通过，详见 `AI_Middle_Office/P4_ACCEPTANCE.md`。 |
| 暂不启动 | P5 LangGraph | 达到触发条件后再评估。 |
| 已完成 | AI 平台升级 Phase 0：Vite 壳 + 登录鉴权统一 + RBAC | 2026-05-18 开发与当前环境验证完成；旧页面保留；正式生产准备推迟到系统整体完善后统一处理。 |
| 已完成当前环境运行态验收 | AI 平台升级 Phase 1：报价速度看板 | 已新增报价速度聚合接口、功能开关和 Vite 看板视图；已修复 AI 生成耗时口径并回填历史成功任务；页面、看板数据和新增真实报价统计均正常；正式生产准备推迟到系统整体完善后统一处理。 |
| 已完成当前环境运行态验收 | AI 平台升级 Phase 2：响应速度追踪 | 已新增咨询记录模型、报价任务关联、咨询查询/修正接口、响应速度聚合和 Vite 响应速度视图；当前环境 Alembic 已升级到 `20260514_0012 (head)`，内网 smoke 样本已进入响应看板，平均首次响应 15 分钟，Celery worker 元数据加载问题已修复；正式生产准备推迟到系统整体完善后统一处理。 |
| 已完成当前环境验证 | AI 平台升级 Phase 2.5：管理员报价运营闭环 | 已在 Vite 管理台新增“报价运营”标签页，串联报价任务、客户咨询、确认报价和钉钉推送状态；后端报价任务列表补充客户/历史摘要和筛选能力；提交人独立列、筛选和任务详情已验证；不新增数据库结构，不启动 Phase 3。 |
| 已完成当前环境验证 | AI 平台升级 Phase 3：执行速度追踪 | 已新增执行任务 schema、任务接口、事件记录、执行速度聚合和 Vite 执行任务页面；当前环境已打开 `FEATURE_EXECUTION` / `FEATURE_DASHBOARD_EXECUTION` 并完成任务创建、开始、完成、取消、详情事件和执行速度看板验证，执行趋势已显示取消数量。 |

- [x] **P0 报价反馈闭环 + 版本追踪**：新增 `quote_feedback`、`quote_corrections`、`quote_rag_traces`，在异步报价预审完成时记录 AI 初稿，在 `/confirm_push` 成功后记录最终人工确认稿和字段差异，在预审打回时记录 rejection；新增 `DIFY_APP_VERSION`、`DIFY_WORKFLOW_VERSION`、`DIFY_PROMPT_VERSION`、`DIFY_RELEASE_ID`、`RAG_COLLECTION_ALIAS` 配置。
- [x] **P0 前端上下文传递**：`index.html` 在预审弹窗保存 `quote_job_id` / `trace_id`，确认推送时带回后端，打回重填时调用 `/api/v1/quote/feedback/reject` 做轻量记录。
- [x] **P0 测试覆盖**：新增 `tests/test_quote_feedback.py`，覆盖确认推送后的反馈、修正、RAG trace 记录，以及人工打回记录。
- [x] **P0 验证结果**：`python -m compileall app` 通过，`python -m pytest` 为 `59 passed`；仍有已知 `.pytest_cache` 权限 warning，不影响测试结果。
- [x] **P1 Admin 反馈分析页面**：新增 admin summary/list/detail 接口和 `admin.html` 反馈分析模块，展示最近报价数量、确认/打回、平均总价偏差、字段修正、RAG trace、prompt 版本表现和高频召回知识条目。
- [x] **P1 验证结果**：`python -m compileall app` 通过，`python -m pytest` 为 `60 passed`；`node --check static/js/admin/feedback.js` 通过。
- [x] **P2 验证结果**：`python -m compileall app` 通过，`python -m alembic heads` 显示 `20260507_0005 (head)`，`python -m pytest` 为 `62 passed`。
- [x] **P2 Prompt 回归评测**：新增 `prompt_regression_cases` / `prompt_regression_runs`，可从真实反馈固化黄金案例，并生成按 prompt 版本统计的总价偏差、明细遗漏率、格式错误率、打回率、人工修改率和综合分。
- [x] **P3 知识库质量治理**：新增 `knowledge_candidates`，可从真实反馈、字段级修正和打回原因生成候选；admin approve 前自动快照，再新增或更新 `materials`；新增 RAG trace 聚合洞察接口。
- [x] **P3 验证结果**：`python -m compileall app` 通过，`python -m alembic heads` 显示 `20260507_0006 (head)`，`python -m pytest` 为 `64 passed`。
- [x] **P4 真实用户体验优化**：失败可操作原因、预审高风险标记、人工修改一键沉淀知识入口。
- [x] **P4 验收清单**：新增 `AI_Middle_Office/P4_ACCEPTANCE.md`，包含前置确认、金路径、打回路径、失败提示、trace_id 一致性 5 个章节共 22 项检查点。
- [x] **P4 运行态手工验收**：2026-05-08 完成，22/22 项全部通过，见 `AI_Middle_Office/P4_ACCEPTANCE.md` 验收记录。
- [x] **AI 平台升级 Phase 0 开发与当前环境验证**：2026-05-18 完成，Alembic `20260514_0011 (head)`，当前环境 `FEATURE_VITE_FRONTEND=true`，`PUBLIC_ACCESS_ENABLED=false`；`/login`、`/admin/permissions`、`/admin/dashboard`、旧 HTML 入口均冒烟通过。正式生产上线尚未发生。
- [x] **AI 平台升级 Phase 1 报价速度看板当前环境运行态验收完成**：2026-05-18 完成，新增 `FEATURE_DASHBOARD_QUOTE`、`GET /api/v1/admin/dashboard/quote-speed`、`/admin/dashboard` 报价速度视图和自动化测试；`quote_job_runner` 已改为记录单次运行实测耗时，历史 121 条成功任务的 `duration_ms=0` 已在 `C:\AI_Backups\20260517_205242\db\ai_quotation.sql` 备份后完成回填；`python -m compileall app scripts`、`python -m pytest`（85 passed）和 `npm.cmd run build` 均通过；用户手工确认 `/admin/dashboard` 页面无 404 / 403 / 控制台接口错误，三类耗时显示正常，新增真实报价任务已进入统计且 AI 生成耗时大于 0；旧 `index.html` / `admin.html` 业务功能未迁移，正式生产准备推迟到系统整体完善后统一处理。
- [x] **AI 平台升级 Phase 2 响应速度追踪当前环境运行态验收完成**：2026-05-18 完成，新增 Alembic `20260514_0012`、`client_inquiries`、`quote_jobs.client_inquiry_id`、`FEATURE_CLIENT_INQUIRY`、`FEATURE_DASHBOARD_RESPONSE`、`GET/PATCH /api/v1/client-inquiries`、`GET /api/v1/admin/dashboard/response-speed` 和 Vite 驾驶舱“响应速度”标签页；默认时间样本仅计数量不计平均响应耗时；当前环境 Alembic 已升级到 `20260514_0012 (head)`，功能开关已打开且 `PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已创建带 `inquiry_time` 的报价任务并在看板展示 1 条可信样本，平均首次响应 15 分钟、SLA 达标率 100%；已修复 Celery worker 单独运行时未加载 `client_inquiries` 元数据导致任务卡在 `queued` 的问题，并新增 `scripts/phase2_response_smoke.ps1` 固化中国时区造数方式；`python -m alembic heads` 显示 `20260514_0012 (head)`，`python -m compileall app scripts`、`python -m pytest`（86 passed）和 `npm.cmd run build` 均通过。
- [x] **AI 平台升级 Phase 2.5 管理员报价运营闭环当前环境验证完成**：2026-05-18 完成，复用现有 `quote_jobs`、`client_inquiries`、`quote_history`，在 `/admin/dashboard` 新增“报价运营”标签页；当前环境已验证提交人列、筛选、任务详情和页面显示正常；该阶段已封板，不新增数据库结构，不启动 Phase 3。
- [x] **AI 平台升级 Phase 3 执行速度追踪当前环境验证完成**：2026-05-19 完成，新增 Alembic `20260514_0013`、`execution_tasks`、`execution_task_events`、`FEATURE_EXECUTION`、`FEATURE_DASHBOARD_EXECUTION`、执行任务 CRUD/取消接口、执行速度聚合接口、`/admin/execution` 和 `/admin/dashboard` 执行速度标签页；当前环境已打开开关并验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已显示取消数量；`python -m compileall app`、`python -m pytest`（92 passed）、`python -m pytest tests\test_execution_tasks_phase3.py` 和 `npm.cmd run build` 均通过。
- [ ] **P5 LangGraph 触发项**：仅在需要自适应多轮检索、主动澄清、多模型状态机或替换 N8N 核心编排时再评估。

## 高优先级

### ~~1. N8N conversationId 硬编码~~（✅ 2026-04-21 已完成）
- **位置**：`AI_Middle_Office/app/api/v1/chat.py`
- **修复**：改为每次请求生成 `str(uuid.uuid4())`，彻底隔离多用户上下文

### ~~2. N8N Webhook 鉴权不统一~~（✅ 2026-04-26 已兼容升级）
- **修复**：FastAPI 保留 `X-Webhook-Secret` 以兼容现有 N8N 校验，同时新增 `X-Webhook-Signature`
- **签名算法**：`HMAC-SHA256(WEBHOOK_SECRET, canonical_json_body)`，其中 `canonical_json_body` 使用 key 排序和紧凑 JSON
- **N8N 侧**：现有 workflow 可继续校验 `X-Webhook-Secret`；后续可平滑升级为校验 `X-Webhook-Signature`
- **密钥存储**：`AI_Middle_Office/.env` 的 `WEBHOOK_SECRET` 字段

### ~~3. 用户配额无管理界面~~（✅ 2026-04-21 已完成）
- **后端**：`chat.py` 新增 `GET /admin/users` 和 `PATCH /admin/users/{id}/quota` 两个接口
- **前端**：`admin.html` 顶部新增员工账号 & 额度管理面板，支持直接在表格内输入新额度并一键确认

### ~~10. 第一轮稳定性升级~~（✅ 2026-04-25 已完成）
- **配置治理**：新增集中配置读取，N8N/RAG/JWT/数据库/代理/CORS/物料库路径统一走 `.env`
- **可观测性**：新增 JSON 结构化日志、请求级 `trace_id`、`X-Trace-Id` 响应头
- **健康检查**：新增 `/health/live` 与 `/health/ready`
- **登录态**：`app.html` 刷新时通过 `/api/v1/auth/me` 校验 Token，退出时清理完整本地状态
- **错误提示**：`index.html` 报价失败展示后端错误详情与追踪 ID，便于排查

### ~~11. 钉钉 Excel 文件名与表头乱码~~（✅ 2026-04-26 已完成并验证）
- **后端修复**：`confirm_push` 推送前生成 ASCII 安全文件名，例如 `quote_20260426_142030_admin.xlsx`
- **兼容字段**：同时下发 `excel_filename`、`download_filename`、`filename`、`fileName`、`file_name`、`attachment_name`
- **N8N 修复**：`Convert to File` 使用 Webhook 中的安全文件名，最后发送文件消息节点修复 `msgParam` JSON Body
- **Excel 表头**：`Code in JavaScript1` 已将乱码表头修复为 `施工项目`、`AI核准单价(元)`、`项目合计(元)`、`工艺备注`
- **验证结果**：钉钉 markdown 报价单、Excel 附件、文件名、表头均已验证通过
- **备份文件**：`n8n_budget_push_fixed.json`

### ~~12. 后端自动化测试与 CI 基线~~（✅ 2026-04-26 已落地）
- **测试框架**：新增 `pytest.ini`、`requirements-dev.txt` 与 `AI_Middle_Office/tests/`
- **覆盖范围**：健康检查、登录/鉴权、报价文件名兼容字段、Webhook HMAC 签名
- **隔离策略**：测试强制使用本地 SQLite 测试库，不访问真实 MySQL、N8N、RAG 或外部模型
- **CI**：新增 `.github/workflows/backend-ci.yml`，在 push/PR 时执行依赖安装、`compileall` 和 `pytest`

### ~~13. 报价任务异步化基线~~（✅ 2026-04-26 已落地）
- **任务模型**：新增 `QuoteJob` 表，持久化 job_id、状态、阶段、trace_id、事件流、结果和错误信息
- **接口**：新增 `POST /api/v1/quote/jobs`、`GET /api/v1/quote/jobs/{job_id}`、`GET /api/v1/quote/jobs/{job_id}/events`
- **调度层**：新增 `TASK_QUEUE_MODE`，支持 `local` 本地后台线程、`celery` Celery+Redis、`disabled` 测试隔离模式
- **Worker 入口**：新增 `app/tasks/celery_app.py` 与 `app/tasks/quote_tasks.py`，生产可通过 Celery worker 消费报价任务
- **前端切换**：`index.html` 已改为先创建异步任务，再通过任务事件流订阅进度和预审结果
- **兼容策略**：保留原 `/api/v1/chat` SSE 接口，作为回退兼容入口
- **测试覆盖**：新增异步任务创建、状态查询、鉴权测试；本地验证 `9 passed`

### ~~14. Redis/Celery 正式生产化部署配置~~（✅ 2026-04-27 已落地）
- **Redis 编排**：`rag_docker/docker-compose.yml` 新增 `quote-redis` 服务，宿主机暴露 `6380`，开启 AOF 持久化、内存上限和日志轮转
- **Worker 启动**：新增 `start_celery_worker.ps1`，使用 `--pool=solo --concurrency=1` 适配 Windows
- **开机自启**：新增 `install_celery_worker_service.ps1`，注册 `AI_MiddleOffice_CeleryWorker` 任务计划程序
- **健康检查**：`/health/ready` 新增 `task_queue` 状态，Celery 模式检查 Redis broker 与 worker ping
- **部署手册**：新增 `AI_Middle_Office/DEPLOY_CELERY.md`
- **保守切换**：`.env.example` 保持 `TASK_QUEUE_MODE=local` 默认值，生产部署时手动改为 `celery`

### ~~15. 报价任务管理增强~~（✅ 2026-04-27 已落地）
- **任务列表**：新增 `GET /api/v1/quote/jobs`，普通用户只看本人任务，admin 可按 `username` / `status` 查看全队列
- **任务取消**：新增 `POST /api/v1/quote/jobs/{job_id}/cancel`，支持取消 queued/running 任务，并尝试 revoke Celery task
- **失败重试**：新增 `POST /api/v1/quote/jobs/{job_id}/retry`，失败、取消、超时任务可重新创建并派发
- **超时标记**：新增 `POST /api/v1/admin/quote/jobs/mark_timeouts`，管理员可批量将长时间 queued/running 任务标记为 `timed_out`
- **管理界面**：`admin.html` 新增报价任务队列面板，支持筛选、刷新、取消、重试和超时标记
- **执行器保护**：Worker 执行过程中会检查终态，避免已取消/超时任务继续落预审结果
- **测试覆盖**：新增列表、取消、重试、管理员超时标记测试

### ~~16. 模型调用统一网关~~（✅ 2026-04-27 已落地）
- **统一入口**：新增 `app/services/model_gateway.py`，GLM-4V 图像识别与 n8n/Dify/DeepSeek 工作流调用统一经过网关
- **调用日志**：新增 `ModelCallLog` 表，记录 provider、model、endpoint、状态、HTTP 状态、耗时、输入/输出字符、估算费用和 trace_id
- **熔断保护**：按 `provider:endpoint` 做内存级失败计数，超过阈值后短时间熔断，避免连续打爆外部模型/工作流
- **统计接口**：新增 `GET /api/v1/admin/model_gateway/stats` 与 `/admin/model_gateway/circuits`
- **管理界面**：`admin.html` 新增模型网关观测面板，展示调用次数、平均耗时、估算费用和熔断状态
- **测试覆盖**：新增模型网关统计和管理员权限测试

### ~~17. 知识库变更快照与回滚基础版~~（✅ 2026-04-27 已落地）
- **自动快照**：`POST /api/v1/admin/materials` 保存覆盖材料库前，自动将旧版 `rag_materials.json` 写入 `materials_audit/`
- **审计列表**：新增 `GET /api/v1/admin/materials/audit`，管理员可查看快照时间、操作者、动作和条目数
- **一键回滚**：新增 `POST /api/v1/admin/materials/rollback/{snapshot_id}`，回滚前会再次自动备份当前版本
- **管理界面**：`admin.html` 左侧新增“知识库变更快照”面板，支持刷新快照和回滚
- **测试覆盖**：新增 `tests/test_material_audit.py`，覆盖保存生成快照、列表读取和回滚恢复

### ~~18. MinIO 文件存储与临时下载链接~~（✅ 2026-04-27 已落地）
- **独立对象存储**：`rag_docker/docker-compose.yml` 新增 `quote-minio`，宿主机端口 `9002/9003`，避免与 Milvus/RAGFlow MinIO 冲突
- **后端存储服务**：新增 `app/services/file_storage.py`，封装 bucket 初始化、对象上传、临时下载链接和健康检查
- **文件元数据**：新增 `FileObject` 表，记录 file_id、上传用户、用途、bucket、object_name、原始文件名、类型、大小和上传时间
- **文件接口**：新增 `POST /api/v1/files`、`GET /api/v1/files`、`GET /api/v1/files/{file_id}/download_url`
- **健康检查**：新增 `GET /api/v1/admin/files/storage/health`
- **管理界面**：`admin.html` 新增 MinIO 文件存储面板，支持检测、上传、刷新列表和生成临时下载链接
- **部署手册**：新增 `AI_Middle_Office/DEPLOY_MINIO.md`
- **测试覆盖**：新增 `tests/test_file_storage.py`，覆盖上传元数据落库、列表查询和临时链接生成

### ~~19. 报价任务附件接入 MinIO~~（✅ 2026-04-27 已落地）
- **任务轻量化**：`POST /api/v1/quote/jobs` 在 `MINIO_ENABLED=true` 时会先将上传附件写入 MinIO，只在 `QuoteJob.file_object_id` 保存引用
- **兼容回退**：MinIO 未启用时继续使用原 `file_base64` 存储逻辑，保证本地和测试环境不受影响
- **执行器读取**：Worker 执行报价任务时优先根据 `file_object_id` 从 MinIO 拉取附件 bytes，再交给 GLM-4V 识别
- **失败保护**：附件从 MinIO 读取失败时，任务标记为 `failed`，阶段为 `file_load`，事件流返回明确错误
- **启动迁移**：`main.py` 增加 `quote_jobs.file_object_id` 保守迁移，兼容已有 MySQL 表
- **测试覆盖**：`tests/test_file_storage.py` 新增 MinIO 开启时创建报价任务的附件引用测试

---

### ~~20. 一键启动与自愈编排~~（✅ 2026-04-28 已落地）
- **CentOS 网络自愈**：新增 `rag_docker/enable_centos_autostart.sh`，将 `ens33` 配置为 DHCP + 开机自动连接，并启用 Docker/n8n/Compose 服务自恢复
- **远程安装脚本**：新增 `AI_Middle_Office/install_centos_autostart.ps1`，从 Windows 一键上传并执行 CentOS 自愈脚本，支持 SSH passphrase 交互
- **Windows 一键启动**：新增 `AI_Middle_Office/start_all.ps1`，启动前等待 MySQL、Redis、RAG、n8n、MinIO 端口就绪，再拉起 Celery Worker 和 FastAPI
- **开机任务升级**：`install_service.ps1` 改为注册 `start_watchdog.ps1`，开机后每 3 分钟重试一次启动编排，最多持续 60 分钟
- **数据库启动保护**：FastAPI 启动时会等待 MySQL 可用后再建表和执行启动迁移，降低冷启动 MySQL 尚未 ready 导致崩溃的风险
- **部署文档**：新增 `AI_Middle_Office/DEPLOY_STARTUP.md`，记录 CentOS 和 Windows 冷启动/自启动验证流程

### ~~21. 运维监控与告警~~（✅ 2026-04-28 已落地）
- **统一监控接口**：新增 `GET /api/v1/admin/ops/dashboard`，聚合基础服务探活、卡住任务、异常日志和告警列表
- **基础服务探活**：新增 MySQL、Redis、Celery、RAG、MinIO、n8n 状态检查，支持独立接口 `/admin/ops/services`
- **异常日志聚合**：新增 `/admin/ops/logs`，扫描 `AI_Middle_Office/logs/*.log` 最近日志，聚合 ERROR、Traceback、数据库断连和任务崩溃线索
- **卡住任务提醒**：新增 `/admin/ops/jobs`，按阈值统计 queued/running 长时间未更新任务，并在 dashboard 中生成提醒
- **管理员面板**：`admin.html` 顶部新增“运维监控与告警”面板，展示服务状态、卡住任务、异常日志，并每 60 秒自动刷新提醒
- **部署文档**：新增 `AI_Middle_Office/DEPLOY_OPS.md`，记录探活范围、接口和可配置项
- **测试覆盖**：新增 `tests/test_ops_monitor.py`，覆盖管理员权限、dashboard 结构和卡住任务提醒

### ~~22. 数据库迁移治理 Alembic 化~~（✅ 2026-04-28 已落地）
- **迁移框架**：新增 `AI_Middle_Office/alembic.ini`、`alembic/env.py`、`alembic/script.py.mako` 和 `alembic/versions/`
- **基线迁移**：新增 `20260428_0001_initial_schema`，覆盖 `users`、`quote_history`、`quote_jobs`、`model_call_logs`、`file_objects`，并兼容已有 MySQL 表只补缺失字段
- **启动编排接入**：`start_all.ps1` 在启动 FastAPI 前自动执行 `alembic upgrade head`，并提供 `-SkipMigrations` 临时跳过参数
- **兼容开关**：新增 `AUTO_CREATE_TABLES`、`STARTUP_COMPAT_MIGRATIONS`、`AUTO_RUN_DB_MIGRATIONS`，生产稳定后可关闭启动时建表和保守补列
- **迁移脚本**：新增 `upgrade_database.ps1`，支持手动执行数据库升级
- **部署文档**：新增 `AI_Middle_Office/DEPLOY_DB_MIGRATIONS.md`，记录安装依赖、执行迁移、版本查看和新增 revision 流程

### ~~后端重构 P0：chat.py 拆分 + lifespan 启动治理 + 统一鉴权依赖~~（✅ 2026-05-06 已完成）
- **chat.py 拆分**：643 行 God File 拆为 `quote.py`（201行）、`materials.py`（410行）、`history.py`（47行）、`users.py`（41行）；辅助逻辑提取至 `services/excel_service.py` 和 `services/quote_helpers.py`；chat.py 保留兼容层（72行），所有 API 路径不变
- **lifespan 启动治理**：`_wait_for_database_ready`、`_run_schema_compat_migrations`、`_mark_default_admin_password` 合并为 `_run_startup_database_tasks()`，通过 `asyncio.to_thread()` 在 `lifespan` 内执行，消除模块级副作用
- **统一鉴权依赖**：新增 `app/dependencies.py`，`get_current_user` / `require_admin` 统一维护，所有路由模块从此导入

### ~~后端重构 P1：confirm_push schema + 物料库入库~~（✅ 2026-05-06 已完成）
- **Pydantic schema**：新增 `app/schemas/quote.py`，`confirm_push` 请求体字段类型与必填约束明确，消除裸 `dict` 传递
- **物料库入库**：新增 `app/models/material.py`，知识库条目持久化至 MySQL `materials` 表
- **旧 JSON 兼容**：`LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 仅作为旧 `rag_materials.json` 自动导入源保留，RAG 评测报告目录独立为 `RAG_EVAL_REPORT_DIR`

### ~~后端重构 P2：requests → httpx~~（✅ 2026-05-06 已完成）
- 所有对外 HTTP 调用（`quote.py`、`materials.py`、`rag_eval.py` 等）改为 `httpx.AsyncClient`，消除异步路由中的同步阻塞

### ~~后端重构 P3：统一响应格式 + 前端收口~~（✅ 2026-05-06 已完成）
- **统一响应**：新增 `app/core/responses.py`，`api_ok` / `api_page` 统一所有 REST 接口返回 `{"code": 200, "message": "ok", "data": ...}`
- **前端收口**：`index.html`、`admin.html`、`app.html` 统一通过 `res.data` 读取响应，与后端格式一一对应

### ~~后端重构收尾：配置语义清理 + 验收闭环~~（✅ 2026-05-06 已完成）
- **配置清理**：`MATERIALS_FILE` 保留为兼容 alias，新增 `LEGACY_MATERIALS_FILE` 明确旧 JSON 导入语义，新增 `RAG_EVAL_REPORT_DIR` 独立控制评测报告目录
- **运行态验收**：FastAPI `/health/ready`、Celery worker、RAG 服务均已确认 ready；旧 `rag_materials.json` 已导入数据库 70 条，RAG eval `quality_ok=True`
- **测试闭环**：`python -m compileall app` 通过，`python -m pytest` 为 `55 passed`
- **Git 状态**：最新后端重构收尾提交 `6e54c09 clarify legacy materials config` 已推送到 GitHub `main`

### 后端补充优化路线（进行中，2026-05-06）
> 本轮处理 P0-P3 后遗留的一致性与低阻塞风险；每完成一项后在本清单打勾，并补充验证结果。

- [x] **B1 模型网关日志异步化**：为 `record_model_call` 增加 async 包装，`call_glm_vision_extract` / `post_json_via_gateway` 中的 DB 写入改为 `asyncio.to_thread`，避免 async 调用链被 MySQL commit 阻塞。验证：`pytest tests\test_model_gateway.py` 为 `5 passed`。
- [x] **B2 运维监控阻塞治理 + httpx 统一**：`ops.py` 的 async 路由通过 `asyncio.to_thread` 调用同步探活/日志聚合；`ops_monitor.py` 中 HTTP 探活和钉钉告警从 `urllib` 统一到 `httpx`。验证：`pytest tests\test_ops_monitor.py` 为 `4 passed`。
- [x] **B3 响应冗余字段收口**：优先清理 `quote_jobs.py` 的 `api_ok(data, **data)` 为 `api_ok(data)`；`auth.py` 顶层 token / 用户字段因 PowerShell 运维脚本兼容需求暂保留。验证：`pytest tests\test_quote_jobs.py tests\test_file_storage.py tests\test_api_response_format.py` 为 `13 passed`。
- [x] **B4 兼容别名说明**：保留 `DATA_FILE = LEGACY_DATA_FILE` 作为 `chat.py` 旧 import 兼容 alias，并补充代码注释，避免后续误删。
- [x] **B5 低风险项观察**：`/health/ready` 保持同步 `def` 路由；SSE 每秒短 session 轮询暂不调整，等并发压力或连接池指标证明有必要再改。

---

## 中优先级

### ~~4. 报价历史记录~~（✅ 2026-04-21 已完成）
- **后端**：新增 `QuoteHistory` 表（id/username/created_at/total_amount/item_count/payload_json），confirm_push 成功后自动写入
- **接口**：`GET /api/v1/history`，普通用户查自己，admin 可按 username 筛选全员
- **前端**：`index.html` 输入区新增"历史记录"按钮，点击弹出右侧抽屉展示历史列表，支持翻页和查看明细

### ~~5. RAG 检索效果评估~~（✅ 2026-04-21 已完成）
- **脚本**：`eval_rag.py`，30 条人工标注测试集（4 个难度级别：直接命名/同义口语/多意图/描述性）
- **指标**：Hit@K（命中率）+ MRR（平均倒数排名），按难度级别分解输出
- **用法**：`python eval_rag.py [--url http://192.168.88.128:8001] [--top_k 5]`
- **输出**：控制台报告 + 自动保存 `rag_eval_时间戳.json`

### ~~6. sync_milvus 每次重载 embedding 模型~~（✅ 2026-04-21 已完成）
- **RAG 服务**：`rag_api_service.py` 新增 `POST /admin/reload`，接收物料数据后复用常驻内存的 `_GLOBAL_MODEL` 完成向量化和蓝绿切换，同时热更新 BM25 索引
- **chat.py**：`sync_milvus` 改为直接 POST 到 RAG 服务，不再在 Windows 端加载大模型
- **鉴权**：`RELOAD_SECRET` 环境变量，`.env` 和 `docker-compose.yml` 均已配置

---

## 低优先级

### ~~7. 前端无重试机制~~（✅ 2026-04-22 已完成）
- **方案**：N8N 超时/GLM-4V 失败/网络异常时，在错误消息气泡下方显示"🔄 重试"按钮
- **逻辑**：保存最后一次发送的文本和文件，重试时还原输入并重新调用 `sendMessage()`

### ~~8. Docker 日志无集中收集~~（✅ 2026-04-22 已完成）
- **方案**：docker-compose.yml 所有容器加 `json-file` 日志驱动，限制单文件 20-50MB、最多 3-5 个滚动文件
- **工具**：`/opt/rag_service/show_logs.sh` 一键聚合查看所有容器日志，支持按容器过滤

### ~~9. admin 初始密码过于简单~~（✅ 2026-04-22 已完成）
- **方案**：启动时检测 admin 密码是否仍为 `123`，若是则标记 `must_change_password=True`
- **后端**：`auth.py` 新增 `POST /api/v1/auth/change_password`，登录响应含 `must_change_password` 字段
- **前端**：`app.html` 登录后若 `must_change_password=true` 弹出强制修改弹窗，新密码不少于6位

---

## 已完成

- [x] RAG 微服务迁移 CentOS（Docker 容器化）
- [x] Milvus 蓝绿零停机切换
- [x] GLM-4V 静默假数据修复（改为返回 None）
- [x] N8N SQL 注入修复 + ZHIPU_API_KEY 环境变量化
- [x] BM25 升级真混合检索（jieba 分词 + RRF 融合 + 低分过滤）
- [x] 知识库数据扩充至 40 条（北京 2024 年市场行情）
- [x] 前端统一入口 + FastAPI 托管三个 HTML 页面
- [x] 真实 JWT 鉴权替换 Mock 登录
- [x] Windows 任务计划程序自启服务
