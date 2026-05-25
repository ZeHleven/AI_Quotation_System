# AI 智能报价中台 — 后端项目上下文

本文件补齐根目录 `AGENTS.md` 的引用，记录当前后端、异步任务、RAG 与部署约定。根目录摘要用于快速进入项目，本文件用于后续开发交接。

## 架构总览

```
用户前端 (Vue.js 3 + Element Plus CDN)
      ↓
FastAPI 主网关 (Windows, http://localhost:9000)
      ↓                       ↓
异步报价任务 / Celery Worker   GLM-4V 图像识别 (智谱 AI)
      ↓
n8n budget-calc / budget-push
      ↓
Dify + DeepSeek-R1 报价优化
      ↓
RAG 检索服务 (CentOS 192.168.88.128:8001)
      ↓
Milvus 向量数据库 (192.168.88.128:19530)
```

## 当前运行基线

- FastAPI: `http://localhost:9000`
- CentOS: `192.168.88.128`
- RAG: `http://192.168.88.128:8001`
- n8n: `http://192.168.88.128:5678`
- Redis: `192.168.88.128:6380`
- MySQL: `192.168.88.128:5455`
- MinIO: `192.168.88.128:9002/9003`
- 健康检查：`/health/live`、`/health/ready`
- 当前任务队列模式：生产使用 `TASK_QUEUE_MODE=celery`
- 当前后端优化状态：基础设施 P0-P3 与配置收尾已完成并冻结；业务优化 P0 报价反馈闭环、P1 Admin 反馈分析、P2 Prompt 回归评测、P3 知识库候选治理和 P4 真实用户体验优化已落地到代码层。
- AI 平台架构升级 Phase 0 已完成开发与当前环境验证（2026-05-18）：RBAC、`role_version`、Vite 壳、`/login`、`/admin/permissions`、SPA fallback 已通过；旧 `index.html` / `admin.html` / `app.html` 保留。正式生产上线尚未发生，未来需单独 Runbook。
- AI 平台架构升级 Phase 1 报价速度看板已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_DASHBOARD_QUOTE`、`/api/v1/admin/dashboard/quote-speed` 和 `/admin/dashboard` 看板视图；已修复新报价任务 `duration_ms` 实测写入，并在备份后回填历史 121 条成功任务的 0 耗时记录；页面、看板数据和新增真实报价统计均已确认正常，正式生产启用待单独 Runbook。
- AI 平台架构升级 Phase 2 响应速度追踪已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_CLIENT_INQUIRY`、`FEATURE_DASHBOARD_RESPONSE`、`client_inquiries`、报价任务咨询关联、咨询查询/修正接口和 `/admin/dashboard` 响应速度标签页；当前环境 Alembic 已升级到 `20260514_0012 (head)`，功能开关已打开且 `PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已展示 1 条可信响应样本，平均首次响应 15 分钟，Celery worker Phase 2 元数据加载问题已修复。
- AI 平台架构升级 Phase 2.5 管理员报价运营闭环已完成当前环境验证（2026-05-18）：复用 `quote_jobs`、`client_inquiries`、`quote_history`，在 `/admin/dashboard` 新增“报价运营”标签页；提交人列、筛选、任务详情、重试/取消/超时标记入口已落地；不新增数据库结构，不启动 Phase 3。
- AI 平台架构升级 Phase 3 执行速度追踪已完成当前环境验证（2026-05-19）：新增 `execution_tasks`、`execution_task_events`、`FEATURE_EXECUTION`、`FEATURE_DASHBOARD_EXECUTION`、执行任务 CRUD/取消接口、执行速度聚合接口、`/admin/execution` 任务页和 `/admin/dashboard` 执行速度标签页；当前环境已打开开关并验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已显示取消数量。
- AI 平台架构升级 Phase 4a 手动会议纪要 + 草稿确认已完成当前环境运行态验收（2026-05-19）：新增 `meeting_notes`、`task_drafts`、`meeting_note_revisions`、`FEATURE_MEETING_AI`、会议纪要接口和 `/admin/execution` 会议纪要标签页；当前环境已打开 `FEATURE_MEETING_AI=true` 且 `PUBLIC_ACCESS_ENABLED=false`，内网 smoke 已验证纪要提取草稿、确认写入 `execution_tasks`、人工补充后作废、revision 补充任务和 `/admin/execution` 访问。当前不启动 Phase 4b/4c/6，不迁移旧 `index.html` / `admin.html`。
- AI 平台升级 BIZ Track BIZ-1a 商务台账 v1 已完成当前环境验证（2026-05-20）：新增 `FEATURE_BUSINESS_LEDGER`、Alembic `20260520_0016`、商务台账接口和 `/admin/business-ledger` 页面；商务/市场部后续 BIZ-1b/BIZ-1c 暂停，BIZ-1d 外部项目源自动筛选与联系方式获取待定。
- AI 平台升级 BIZ Track BIZ-2a 成本数据库初始化已完成当前环境验证（2026-05-20）：新增 `FEATURE_COST_DB`、Alembic `20260520_0017` / `20260520_0018`、`cost_items`、`cost_item_history`、成本库接口、旧 Excel 导入预览/确认和 `/admin/cost-db` 页面；保留对甲税前综合单价、劳务发包综合单价、班组标底税前价三类价格，并补充人工费、主材费、辅材费等拆分价；2026-05-21 补充“撤回启用”和批量状态流转能力，支持单条/批量 `draft -> active`、`active -> draft` 并写入状态历史，`archived` 仍冻结不可撤回。
- AI 平台升级 BIZ Track BIZ-2b 报价时材料底价查询已完成代码层验证（2026-05-21）：新增报价结果成本参考匹配服务，接入同步 `/chat` 与异步 `quote_jobs` preview 结果；仅匹配 `active` 成本条目，优先 `item_name + spec` 精确匹配，其次 `item_name` 模糊匹配；旧 `index.html` 预审弹窗已展示“成本库参考价 vs AI 生成价”和价差提示。不新增 Alembic；当前环境成本库 `active=190`，已具备真实运行态成本参考匹配数据基础。
- AI 平台升级 BIZ Track BIZ-2c 成本库主库化 + active RAG 同步已完成当前环境验证（2026-05-21）：新增 active 成本条目 RAG 同步服务、`POST /api/v1/admin/cost-items/sync-rag` 管理员接口、Alembic `20260520_0019` 同步记录表 `cost_rag_sync_runs`、`GET /api/v1/admin/cost-items/sync-rag/runs` 和 Vite `/admin/cost-db`“同步 active 到 RAG / 同步记录”窗口；同步源只取 `cost_items.active`。旧 `materials` 作为报价/RAG 源已退役，70 条测试数据已备份后清空，旧 materials 写入/回滚、旧 `/admin/sync_milvus` 和旧知识候选 approve 均返回 410。
- AI 平台升级 BIZ Track BIZ-2d 成本库参考价命中率优化已完成代码层验证（2026-05-21）：补强 `quote_cost_matching` 的中文名称归一化、符号/连接词处理、词序无关 token 匹配、单位族兼容和动作词误命中保护；“窗帘盒/灯槽拆除”类写法可命中 active 成本库底价；编号换行清单在发送 N8N 前会自动清洗成分号清单，避免 `1. / 2. / 3.` 多行需求触发空响应。不新增 Alembic，不启动漏项检测。
- AI 平台升级 BIZ Track BIZ-2e 漏项检测已完成代码层验证（2026-05-21）：新增保守规则式漏项检测服务，复用同步 `/chat` 与异步 `quote_jobs` preview 成本库 enrichment；仅基于 `cost_items.active` 生成 `omission_summary` / `omission_suggestions`，旧 `index.html` 预审弹窗展示疑似漏项、触发行、原因和成本库参考价；不自动新增报价行、不改变合计、不新增 Alembic。
- AI 平台升级 BIZ Track BIZ-2f 报价需求单 Excel 解析已完成代码层验证（2026-05-21）：新增 `quote_excel_parser`，`index.html` 支持上传 `.xlsx/.xlsm` 需求单；同步 `/chat` 与异步 `quote_jobs` 会直接解析施工项目、数量、单位、规格/特征和备注后进入现有报价流程，不再把 Excel 交给 GLM-4V；旧 `.xls` 明确提示另存为 `.xlsx`。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2g 成本库底价兜底填价已完成代码层验证（2026-05-21）：当 AI 预审单给出空/0 单价但已命中 `cost_items.active` 底价且可解析数量时，报价预审会使用成本库参考价回填单价和合计，并标记“已用成本库底价兜底”；AI 已给出正常正数单价时不覆盖。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2h 成本库价格前置给 AI 报价链路已完成代码层验证（2026-05-22）：新增 `quote_cost_context`，在 FastAPI 调用 N8N/Dify 前基于 `cost_items.active` 匹配需求项，把命中的底价、单位、数量、匹配类型、`cost_item_id` 和参考合计作为 `[成本库底价强参考]` 追加到 `text.content`；同步 `/chat` 与异步 `quote_jobs` 已接入，BIZ-2g 后置兜底继续保留。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2i 报价可解释性与审计记录已完成代码层验证（2026-05-22）：报价确认/打回后记录成本证据，包含 AI 原始报价、最终报价、成本库参考价、成本条目快照、行合计来源、整单合计来源、AI 报价来源和证据链接；后台报价反馈/报价运营可追溯。不新增 Alembic。
- AI 平台升级 BIZ Track BIZ-2j 报价依据与成本库证据链展示优化已完成代码层验证（2026-05-22）：`index.html` 预审“查看依据”弹窗已按 AI 报价来源、AI 报价依据、成本库参考、合计对照等分区展示，成本库详情链接改为按钮；后台报价任务详情展示 AI 来源和成本证据。
- BIZ-2 预审体验补强已完成当前环境手动验收并推送（2026-05-23）：品牌文案统一为“旗胜智价”；预审阶段支持同名不同规格成本条目切换，切换后同步采用新成本条目参考价重算单价/合计；AI 来源可区分采纳/偏离前置成本库、无成本库参考 AI 估算、成本库兜底和人工切换；报价运营详情展示预审打回原因。
- 产品边界：系统完善前不正式投入生产使用；后续阶段先按内网开发/验证推进，最后统一准备正式生产 Runbook。
- 当前未完成/暂缓项：P2 候选 prompt 自动重跑、P5 LangGraph 触发评估。
- 当前数据库迁移 head：`20260520_0019`；内网验证数据库若仍低于 head，需执行 Alembic 升级后启用完整反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 响应速度追踪、Phase 3 执行速度追踪、Phase 4a 会议纪要草稿确认、BIZ-1a 商务台账、BIZ-2a 成本数据库和 BIZ-2c RAG 同步记录。
- 最新验证（2026-05-21，BIZ-2f/BIZ-2g 报价需求单 Excel 解析 + 成本库底价兜底填价）：`python -m alembic current` 显示 `20260520_0019 (head)`；`FEATURE_COST_DB=true`、`PUBLIC_ACCESS_ENABLED=false`；成本库当前 `total=197 / active=190 / archived=7`；`.xlsx/.xlsm` 需求单解析不新增数据库结构，上传 Excel 会先转成报价清单文本再进入现有报价、成本库参考、底价兜底和漏项检测链路；旧 `.xls` 提示另存为 `.xlsx`；底价兜底仅在 AI 单价空/0、成本库命中且有数量时生效；`python -m pytest` 为 `168 passed`，`ai-web` 的 `npm run build` 通过。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 最新验证（2026-05-22，BIZ-2h 成本库价格前置给 AI 报价链路）：已完成代码层验证；`FEATURE_COST_DB=true` 时，报价请求进入 N8N/Dify 前会追加 active 成本库强参考上下文；`FEATURE_COST_DB=false` 或无命中时请求文本保持不变；不新增数据库结构，不改 N8N，BIZ-2g 兜底仍作为后置安全网。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 最新验证（2026-05-23，BIZ-2i/BIZ-2j 与预审体验补强）：已完成代码层验证和当前环境手动验收；`index.html` 可展示报价依据与成本库证据链、AI 报价来源、成本库详情按钮、同名不同规格成本条目切换和打回原因追溯；`ai-web` 报价运营详情可展示成本证据、AI 来源和预审打回原因；不新增数据库结构，不改 N8N/Dify，不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。

## 关键模块

- `app/main.py`：FastAPI 入口、HTML 托管、路由注册、健康检查、启动期数据库兼容迁移。
- `app/core/config.py`：集中读取 `.env` 配置，包含 n8n、RAG、Celery、MinIO、代理、数据库和模型网关参数。
- `app/api/v1/auth.py`：JWT 登录、当前用户、改密；Phase 0 token 包含 `roles` / `role_version`。
- `app/api/v1/chat.py`：旧兼容导出层，核心路由已拆分，保留历史 import 路径。
- `app/api/v1/quote.py`：`/chat` SSE 报价流与 `/confirm_push`。
- `app/api/v1/quote_feedback.py`：报价反馈闭环与 admin 反馈分析接口，包括 summary / list / detail。
- `app/api/v1/prompt_regression.py`：Prompt 回归评测接口，包括黄金案例 build/list 和回归报告 run/latest/history。
- `app/api/v1/knowledge_candidates.py`：知识库治理接口，包括候选 build/list/summary、RAG trace 洞察、approve/reject。
- `app/api/v1/materials.py`：旧 materials 只读/退役保护；写入、回滚、旧 sync_milvus 已废弃。
- `app/api/v1/history.py`：报价历史记录。
- `app/api/v1/users.py`：用户配额管理、Phase 0 角色授权/撤销和权限历史。
- `app/api/v1/quote_jobs.py`：新版异步报价任务 API，创建、查询、事件流、取消、重试、超时标记；报价运营详情会带出关联报价反馈摘要，用于展示预审打回原因。
- `app/services/quote_cost_matching.py`：BIZ-2b/BIZ-2d/BIZ-2g/BIZ-2h/BIZ-2j 成本底价匹配基础能力，给 preview 明细附加 `cost_reference` 与匹配汇总，处理中文符号、单位族和词序差异，并在 AI 单价空/0 且命中底价时保守回填；同时向前置上下文服务提供 active 匹配 helper，并补充报价来源解释字段。
- `app/services/quote_cost_context.py`：BIZ-2h 报价前置成本上下文，在调用 N8N/Dify 前把命中的 `cost_items.active` 底价、单位、数量和匹配类型追加为 AI 强参考文本。
- `app/services/quote_cost_evidence.py`：BIZ-2i 报价成本证据审计服务，记录并序列化 AI 原始报价、最终报价、成本库参考价、行/整单合计来源、AI 报价来源、成本条目快照和证据链接。
- `app/services/quote_omission_detection.py`：BIZ-2e 保守规则式漏项检测，基于当前报价行和 `cost_items.active` 生成 `omission_summary` / `omission_suggestions`。
- `app/services/quote_excel_parser.py`：BIZ-2f 报价需求单 Excel 解析，支持 `.xlsx/.xlsm` 表头识别并输出报价清单文本。
- `app/services/quote_helpers.py`：报价通用工具，包含 N8N 签名、报价文件名和编号换行清单输入清洗。
- `app/services/cost_rag_sync.py`：BIZ-2c active 成本条目 RAG 同步，将 `cost_items.active` 转换为 RAG `/admin/reload` 兼容 payload。
- `app/api/v1/execution_tasks.py`：Phase 3 执行任务 API，创建、列表、详情、进度更新和取消。
- `app/api/v1/meetings.py`：Phase 4a 会议纪要 API，创建/查询/详情/草稿阶段更正、人工补充草稿、取消、确认草稿和纪要 revision。
- `app/services/execution_tasks.py` / `app/services/execution_dashboard.py`：执行任务状态机、事件审计和执行速度看板聚合。
- `app/services/meetings.py`：会议纪要提取、任务草稿治理、确认写入 `execution_tasks` 和模型调用审计。
- `app/services/quote_job_runner.py`：后台任务执行链路，负责文件读取、GLM-4V、n8n 调用、结果落库和额度扣减。
- `app/services/quote_feedback.py`：报价反馈闭环服务，记录 AI 初稿、人工确认稿、字段级修正、Dify/prompt 版本和 RAG trace。
- `app/services/prompt_regression.py`：从真实报价反馈固化黄金案例，并计算 prompt 版本的总价偏差、格式错误率、遗漏率、打回率和综合分。
- `app/services/knowledge_candidates.py`：从真实反馈生成知识候选，人工确认后先快照再新增或更新物料库条目。
- `app/services/quote_dispatcher.py`：按 `TASK_QUEUE_MODE` 分发到 Celery/local/inline/disabled。
- `app/services/model_gateway.py`：统一模型与 n8n 调用日志、耗时、错误、熔断。
- `app/services/file_storage.py`：MinIO 文件上传、读取、临时下载链接和健康检查。
- `app/services/rbac.py`：Phase 0 多角色权限、`users.role` 兼容同步、`role_version` 递增和可用模块序列化。
- `app/api/v1/ops.py` + `app/services/ops_monitor.py`：管理员运维面板，聚合基础服务、日志和卡住任务。
- `app/tasks/`：Celery app 与 worker task 入口。
- `alembic/`：数据库迁移基线。
- `verify_startup.ps1`：重启后固定验收脚本，检查 FastAPI、worker、RAG、n8n、MinIO、MySQL、Redis。
- `FRONTEND_ACCEPTANCE.md` / `P4_ACCEPTANCE.md`：手工验收清单。前者覆盖通用前端回归，后者聚焦 P4 业务体验运行态验收（失败可操作原因、预审风险标记、人工修改沉淀知识入口）。
- `run_centos_backup.ps1`：Windows 侧触发 CentOS 备份的入口，实际备份逻辑在 `rag_docker/backup_production.sh`。

## CentOS 侧服务

CentOS 服务由 `/opt/rag_service/docker-compose.yml` 管理，仓库内对应 `rag_docker/docker-compose.yml`：

- `milvus-etcd`
- `milvus-minio`
- `milvus-standalone`
- `rag-api-service`
- `quote-redis`
- `quote-minio`

RAG 服务当前使用离线 HuggingFace 模型路径，挂载：

- `/opt/rag_service/model_cache:/model_cache`
- `/opt/rag_service/hybrid_searcher.py:/app/hybrid_searcher.py:ro`
- `/opt/rag_service/rag_materials.json:/app/rag_materials.json:ro`

当前确认状态：

- `RAG_VECTOR_ENABLED=true`
- RAG 混合检索为向量 + BM25 + RRF
- Milvus alias `enterprise_quotation_rag` 指向 `quotation_blue`
- 正式 RAG/报价成本源为 `cost_items.active`，当前环境 active 190 条
- 正式报价成本价格主库为 MySQL `cost_items` 的 `active` 条目
- `materials` 已清空并退役，不再作为报价/RAG 源；`material_snapshots` 仅作旧审计回溯
- `LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 不再自动导入旧 `rag_materials.json`
- RAG 评测报告目录由 `RAG_EVAL_REPORT_DIR` 控制

## 启动方式

Windows 侧统一启动：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

CentOS 侧如需手动启动，先征求用户许可：

```bash
cd /opt/rag_service && docker compose up -d
```

## 协作约定

- `.env`、真实密钥、数据库文件、缓存和运行日志不提交。
- 破坏性操作、强制推送、清空数据库、删除虚拟机残留、远程 CentOS 命令必须先向用户申请许可。
- 后续提交应排除明显外来目录，例如 `planning-with-files-master/`、`superpowers-main/`、`andrej-karpathy-skills-main/`，除非用户明确要求纳入。
- 代码改动优先保持现有 FastAPI + SQLAlchemy + Vue CDN + Element Plus 的结构，不引入额外前端构建链。

## Backend Refactor Notes

- Formal pricing/RAG source is `cost_items.active`; legacy MySQL `materials` is retired and no longer auto-imports `rag_materials.json`.
- RAG eval report output is controlled by `RAG_EVAL_REPORT_DIR` and no longer depends on the legacy material JSON file path.
