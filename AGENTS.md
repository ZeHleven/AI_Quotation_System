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
│   ├── app/api/v1/materials.py      # 物料库 CRUD、快照、CSV、sync_milvus
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
- 产品边界：系统完善前不正式投入生产使用；后续阶段先按内网开发/验证推进，最后统一准备正式生产 Runbook。
- 当前未完成/暂缓项：P2 候选 prompt 自动重跑、P5 LangGraph 触发评估。
- 架构升级路线按 `docs/superpowers/specs/2026-05-14-ai-platform-upgrade-design.md` 分阶段执行；当前已完成到 Phase 4a 手动会议纪要 + 草稿确认当前环境运行态验收，不启动 Phase 4b/4c/6 的语音转写、钉钉会议导入或经营驾驶舱。
- 当前代码迁移 head：`20260514_0015`；物料库主存储为 MySQL `materials` / `material_snapshots`，报价反馈新增 `quote_feedback` / `quote_corrections` / `quote_rag_traces`，Prompt 回归评测新增 `prompt_regression_cases` / `prompt_regression_runs`，知识库治理新增 `knowledge_candidates`，Phase 0 RBAC 新增 `users.role_version` / `dingtalk_user_id` / `dingtalk_bound_at`、`user_roles`、`user_role_events`，Phase 2 响应速度新增 `client_inquiries` 和 `quote_jobs.client_inquiry_id`，Phase 3 执行速度新增 `execution_tasks` 和 `execution_task_events`，Phase 4a 会议纪要新增 `meeting_notes`、`task_drafts` 和 `meeting_note_revisions`。
- 内网验证数据库若低于 `20260514_0015`，需执行 Alembic 升级后启用完整报价反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 响应速度追踪、Phase 3 执行速度追踪和 Phase 4a 会议纪要草稿确认。
- 新增数据库字段/表必须走 Alembic revision，不能退回依赖 `AUTO_CREATE_TABLES` 或启动兼容迁移。
- `LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 仅保留为旧 `rag_materials.json` 自动导入源；RAG 评测报告目录由 `RAG_EVAL_REPORT_DIR` 控制。
- 最新验证（2026-05-19，Phase 4a 运行态收尾）：`python -m alembic current` 显示 `20260514_0015 (head)`；`scripts\phase4a_meeting_smoke.ps1` 通过，覆盖自动提取并确认任务、人工补充后作废、revision 补充任务和 `/admin/execution` 200；`python -m compileall app scripts` 通过；`python -m pytest` 为 `96 passed`；`npm.cmd run build` 通过。当前不启动 Phase 4b/4c/6，`PUBLIC_ACCESS_ENABLED=false` 边界保持不变。

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
