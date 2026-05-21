# AI 智能报价中台 — 项目上下文

@AI_Middle_Office/AGENTS.md
@ROADMAP.md

详细文档见 `AI_Middle_Office/AGENTS.md`，以下为快速摘要。

---

## 系统架构

```
用户前端 (Vue.js)
      ↓
FastAPI 主网关 (Windows, Port 9000)
      ↓              ↓
   N8N 工作流      GLM-4V 图像识别 (智谱 AI)
      ↓
  Dify + DeepSeek-R1（报价优化）
      ↓
RAG 检索服务 (CentOS 192.168.88.128, Port 8001)
      ↓
Milvus 向量数据库 (CentOS, Port 19530)
```

## 双机部署

| 机器 | 角色 | 运行方式 |
|------|------|---------|
| Windows | FastAPI 主网关、Vue.js 前端 | `C:\Users\12521\miniconda3\python.exe` + uvicorn |
| CentOS 7.9 (192.168.88.128) | N8N、Dify、RAGFlow、Milvus、RAG服务 | Docker Compose `/opt/rag_service/` |

## 关键文件


```
Clear_test/
├── AGENTS.md                        # 本文件（根目录自动加载）
├── app.html                         # 登录门户（JWT鉴权）
├── index.html                       # 业务工作台（AI测算）
├── admin.html                       # 知识库管理（仅admin）
├── AI_Middle_Office/
│   ├── AGENTS.md                    # 完整项目文档
│   ├── app/main.py                  # FastAPI 入口；启动副作用集中于 lifespan
│   ├── app/dependencies.py          # 统一鉴权依赖：get_current_user / require_admin
│   ├── app/core/responses.py        # api_ok / api_page 统一响应工具函数
│   ├── app/api/v1/chat.py           # 旧兼容导出层（核心路由已拆分）
│   ├── app/api/v1/quote.py          # /chat SSE + confirm_push
│   ├── app/api/v1/materials.py      # 旧 materials 只读/退役保护；写入、回滚、sync_milvus 已废弃
│   ├── app/api/v1/auth.py           # 登录接口
│   └── .env                        # ZHIPU_API_KEY（不提交 git）
└── rag_docker/
    ├── docker-compose.yml           # CentOS 端服务编排
    ├── Dockerfile                   # python:3.10-slim + jieba
    ├── hybrid_searcher.py           # 混合检索（向量+BM25+RRF）
    └── rag_api_service.py           # RAG 服务入口
```

## 核心配置

- N8N: `http://192.168.88.128:5678/webhook/budget-calc` / `budget-push`
- Milvus: `192.168.88.128:19530`，集合别名 `enterprise_quotation_rag`（蓝绿：`quotation_blue` / `quotation_green`）
- 向量模型：`maidalun1020/bce-embedding-base_v1`，768维，COSINE，HNSW
- RAG 服务: `http://192.168.88.128:8001`

## 当前完成状态

- 后端重构 P0-P3、补充一致性优化、运维告警收敛已完成并冻结；后续仅按真实问题增量维护。
- 前端优化 P0-P3 已完成：验收清单、共享浏览器逻辑、admin 模块拆分、报价进度/失败恢复/上传推送状态均已落地并手工验证。
- 业务优化 P0-P4 已完成到代码层：报价反馈闭环、Admin 反馈分析、Prompt 回归、知识候选治理、真实用户体验优化均已落地。
- AI 平台架构升级 Phase 0 已完成开发与当前环境验证（2026-05-18）：Vite 壳、`/login`、`/admin/permissions`、RBAC、`role_version`、SPA fallback 已通过；旧 `index.html` / `admin.html` / `app.html` 保留。正式生产上线尚未发生，未来需单独 Runbook。
- AI 平台架构升级 Phase 1 报价速度看板已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_DASHBOARD_QUOTE`、报价速度聚合接口和 `/admin/dashboard` 看板视图；已修复新报价任务 `duration_ms` 实测写入，并在备份后回填历史 121 条成功任务的 0 耗时记录；页面、看板数据和新增真实报价统计均已确认正常，正式生产启用待单独 Runbook。
- AI 平台架构升级 Phase 2 响应速度追踪已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_CLIENT_INQUIRY`、`FEATURE_DASHBOARD_RESPONSE`、`client_inquiries`、报价任务咨询关联、咨询查询/修正接口和 `/admin/dashboard` 响应速度标签页；当前环境 Alembic 已升级到 `20260514_0012 (head)`，功能开关已打开且 `PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已展示 1 条可信响应样本，平均首次响应 15 分钟，Celery worker Phase 2 元数据加载问题已修复。
- AI 平台架构升级 Phase 2.5 管理员报价运营闭环已完成当前环境验证（2026-05-18）：复用 `quote_jobs`、`client_inquiries`、`quote_history`，在 `/admin/dashboard` 新增“报价运营”标签页；提交人列、筛选、任务详情、重试/取消/超时标记入口已落地；不新增数据库结构，不启动 Phase 3。
- AI 平台架构升级 Phase 3 执行速度追踪已完成当前环境验证（2026-05-19）：新增 `execution_tasks`、`execution_task_events`、`FEATURE_EXECUTION`、`FEATURE_DASHBOARD_EXECUTION`、执行任务 CRUD/取消接口、执行速度聚合接口、`/admin/execution` 任务页和 `/admin/dashboard` 执行速度标签页；当前环境已打开开关并验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已显示取消数量。
- AI 平台架构升级 Phase 4a 手动会议纪要 + 草稿确认已完成当前环境运行态验收（2026-05-19）：新增 `meeting_notes`、`task_drafts`、`meeting_note_revisions`、`FEATURE_MEETING_AI`、会议纪要接口和 `/admin/execution` 会议纪要标签页；当前环境已打开 `FEATURE_MEETING_AI=true` 且 `PUBLIC_ACCESS_ENABLED=false`，内网 smoke 已验证纪要提取草稿、确认写入 `execution_tasks`、人工补充后作废、revision 补充任务和 `/admin/execution` 访问。当前不启动 Phase 4b/4c/6，不迁移旧 `index.html` / `admin.html`。
- AI 平台升级 BIZ Track BIZ-1a 商务台账 v1 已完成当前环境验证（2026-05-20）：新增 `FEATURE_BUSINESS_LEDGER`、Alembic `20260520_0016`、商务台账接口和 `/admin/business-ledger` 页面；商务/市场部后续 BIZ-1b/BIZ-1c 暂停，BIZ-1d 外部项目源自动筛选与联系方式获取待定。
- AI 平台升级 BIZ Track BIZ-2a 成本数据库初始化已完成当前环境验证（2026-05-20）：新增 `FEATURE_COST_DB`、Alembic `20260520_0017` / `20260520_0018`、`cost_items`、`cost_item_history`、成本库接口、旧 Excel 导入预览/确认和 `/admin/cost-db` 页面；保留对甲税前综合单价、劳务发包综合单价、班组标底税前价三类价格，并补充人工费、主材费、辅材费等拆分价；2026-05-21 补充“撤回启用”和批量状态流转能力，支持单条/批量 `draft -> active`、`active -> draft` 并写入状态历史，`archived` 仍冻结不可撤回。
- AI 平台升级 BIZ Track BIZ-2b 报价时材料底价查询已完成代码层验证（2026-05-21）：新增报价结果成本参考匹配服务，接入同步 `/chat` 与异步 `quote_jobs` preview 结果；仅匹配 `active` 成本条目，优先 `item_name + spec` 精确匹配，其次 `item_name` 模糊匹配；旧 `index.html` 预审弹窗已展示“成本库参考价 vs AI 生成价”和价差提示。不新增 Alembic；当前环境成本库 `active=190`，已具备真实运行态成本参考匹配数据基础。
- AI 平台升级 BIZ Track BIZ-2c 成本库主库化 + active RAG 同步已完成当前环境验证（2026-05-21）：新增 active 成本条目 RAG 同步服务、`POST /api/v1/admin/cost-items/sync-rag` 管理员接口、Alembic `20260520_0019` 同步记录表 `cost_rag_sync_runs`、`GET /api/v1/admin/cost-items/sync-rag/runs` 和 Vite `/admin/cost-db`“同步 active 到 RAG / 同步记录”窗口；同步源只取 `cost_items.active`。旧 `materials` 作为报价/RAG 源已退役，70 条测试数据已备份后清空，旧 materials 写入/回滚、旧 `/admin/sync_milvus` 和旧知识候选 approve 均返回 410。
- AI 平台升级 BIZ Track BIZ-2d 成本库参考价命中率优化已完成代码层验证（2026-05-21）：补强 `quote_cost_matching` 的中文名称归一化、符号/连接词处理、词序无关 token 匹配、单位族兼容和动作词误命中保护；“窗帘盒/灯槽拆除”类写法可命中 active 成本库底价；编号换行清单在发送 N8N 前会自动清洗成分号清单，避免 `1. / 2. / 3.` 多行需求触发空响应。不新增 Alembic，不启动漏项检测。
- 产品边界：系统完善前不正式投入生产使用；后续阶段先按内网开发/验证推进，最后统一准备正式生产 Runbook。
- 当前未完成/暂缓项：P2 候选 prompt 自动重跑、P5 LangGraph 触发评估。
- 架构升级路线按 `docs/superpowers/specs/2026-05-14-ai-platform-upgrade-design.md` 和 `ROADMAP.md` 的 BIZ Track 分阶段执行；当前已完成到 BIZ-2d 成本库参考价命中率优化代码层验证，不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 当前代码迁移 head：`20260520_0019`；正式报价成本与 RAG 源为 MySQL `cost_items.active`，旧 `materials` 已清空退役、`material_snapshots` 仅作旧审计回溯；报价反馈新增 `quote_feedback` / `quote_corrections` / `quote_rag_traces`，Prompt 回归评测新增 `prompt_regression_cases` / `prompt_regression_runs`，知识库治理新增 `knowledge_candidates`，Phase 0 RBAC 新增 `users.role_version` / `dingtalk_user_id` / `dingtalk_bound_at`、`user_roles`、`user_role_events`，Phase 2 响应速度新增 `client_inquiries` 和 `quote_jobs.client_inquiry_id`，Phase 3 执行速度新增 `execution_tasks` 和 `execution_task_events`，Phase 4a 会议纪要新增 `meeting_notes`、`task_drafts` 和 `meeting_note_revisions`，BIZ-1a 新增 `client_inquiries.direction/stage/next_followup_at/cancelled_*` 与 `client_inquiry_events`，BIZ-2a 新增 `cost_items` 和 `cost_item_history`，BIZ-2c 新增 `cost_rag_sync_runs`。
- 内网验证数据库若低于 `20260520_0019`，需执行 Alembic 升级后启用完整报价反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 响应速度追踪、Phase 3 执行速度追踪、Phase 4a 会议纪要草稿确认、BIZ-1a 商务台账、BIZ-2a 成本数据库和 BIZ-2c RAG 同步记录。
- 新增数据库字段/表必须走 Alembic revision，不能退回依赖 `AUTO_CREATE_TABLES` 或启动兼容迁移。
- `LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 不再自动导入旧 `rag_materials.json`；RAG 评测报告目录由 `RAG_EVAL_REPORT_DIR` 控制。
- 最新验证（2026-05-21，BIZ-2d 成本库参考价命中率优化）：`python -m alembic current` 显示 `20260520_0019 (head)`；`FEATURE_COST_DB=true`、`PUBLIC_ACCESS_ENABLED=false`；成本库当前 `total=197 / active=190 / archived=7`；真实库 smoke 已确认“窗帘盒灯槽拆除”命中 active `cost_item_id=180`、参考价 `6.0`；编号换行清单预清洗已覆盖同步 `/chat` 与异步 `quote_jobs`；`python -m compileall app tests` 通过；`python -m pytest` 为 `153 passed`；`cmd /c npm.cmd run build` 通过。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d，不启动漏项检测。

## 账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | 已强制修改（初始 123）| 管理员 |

## 冷启动

**CentOS（先启动）**
```bash
cd /opt/rag_service && docker compose up -d
```

**Windows（自动）** — 任务计划程序 `AI_MiddleOffice` 开机自启
```
http://localhost:9000/
```

## 技术栈

| 分类 | 组件 |
|------|------|
| 后端 | FastAPI · uvicorn · SQLAlchemy · python-jose · bcrypt |
| AI | GLM-4V · DeepSeek-R1 · BCEmbedding bce-embedding-base_v1 |
| 检索 | Milvus v2.3.1 · pymilvus 2.3.6 · rank-bm25 · jieba · RRF融合 |
| 自动化 | N8N · Dify · 钉钉 Webhook |
| 基础设施 | Docker Compose · CentOS 7.9 · Miniconda · MinIO · etcd |
| 前端 | Vue.js 3 (CDN) · Element Plus · Axios |

## 协作规范

- 所有文件修改、脚本执行由 AI 自行完成并检查，只汇报结果。
- 破坏性操作（删文件、强制推送、清空数据库等）或需在 CentOS 执行的命令，先向用户申请许可。
- 操作结束简短告知"做了什么"，不做多余解释。
