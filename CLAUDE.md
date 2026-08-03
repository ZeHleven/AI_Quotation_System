# AI 智能报价中台 — 项目上下文

@AI_Middle_Office/CLAUDE.md
@ROADMAP.md

详细文档见 `AI_Middle_Office/CLAUDE.md`，以下为快速摘要。

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
├── CLAUDE.md                        # 本文件（根目录自动加载）
├── app.html                         # 登录门户（JWT鉴权）
├── index.html                       # 业务工作台（AI测算）
├── admin.html                       # 知识库管理（仅admin）
├── AI_Middle_Office/
│   ├── CLAUDE.md                    # 完整项目文档
│   ├── app/main.py                  # FastAPI 入口（启动副作用已移入 lifespan）
│   ├── app/dependencies.py          # 统一鉴权依赖：get_current_user / require_admin
│   ├── app/api/v1/quote.py          # SSE 报价流 + confirm_push
│   ├── app/api/v1/materials.py      # 知识库管理（原 chat.py 拆分）
│   ├── app/api/v1/chat.py           # 兼容层（仅 re-export，API 路径不变）
│   ├── app/api/v1/auth.py           # 登录接口
│   ├── app/core/responses.py        # api_ok / api_page 统一响应工具
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
- 异步报价任务：`index.html` 已使用 `/api/v1/quote/jobs`，支持任务列表/取消/重试/超时标记；生产已按 `AI_Middle_Office/DEPLOY_CELERY.md` 切换 Celery + Redis
- 模型网关：GLM-4V 与 n8n/Dify/DeepSeek 调用已统一经过 `app/services/model_gateway.py`，admin 可查看调用统计和熔断状态
- 文件存储：报价附件与通用文件已接入独立 `quote-minio`，临时下载链接由 `/api/v1/files/{file_id}/download_url` 生成
- 运维监控：`/api/v1/admin/ops/dashboard` 聚合 MySQL、Redis、Celery、RAG、MinIO、n8n 探活、异常日志和卡住任务提醒
- 数据库迁移：第 22 步已引入 Alembic，`start_all.ps1` 启动前执行 `alembic upgrade head`，手动入口为 `AI_Middle_Office/upgrade_database.ps1`

## 账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | （已强制修改，初始值勿写入文档）| 管理员 |

## 冷启动

**CentOS（先启动虚拟机）**
- `ens33` 已配置为开机 DHCP 自动联网，正常情况下不再需要手动执行 `sudo dhclient ens33`
- Docker 已启用开机自启，`/opt/rag_service` 内服务使用 `restart: unless-stopped`
- 如需手动恢复：
```bash
cd /opt/rag_service && docker compose up -d
```

**Windows（自动）** — 任务计划程序 `AI_MiddleOffice` 开机执行 `start_watchdog.ps1`
```
http://localhost:9000/
```

`start_watchdog.ps1` 会每 3 分钟重试一次 `start_all.ps1 -NoBrowser`，最多持续 60 分钟，适配 Windows 先启动、CentOS 后启动的顺序。

**手动一键启动**
```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

详细说明见 `STARTUP.md` 和 `AI_Middle_Office/DEPLOY_STARTUP.md`。

## 技术栈

| 分类 | 组件 |
|------|------|
| 后端 | FastAPI · uvicorn · SQLAlchemy · Alembic · Celery · Redis · python-jose · bcrypt |
| AI | GLM-4V · DeepSeek-R1 · BCEmbedding bce-embedding-base_v1 |
| 检索 | Milvus v2.3.1 · pymilvus 2.3.6 · rank-bm25 · jieba · RRF融合 |
| 自动化 | N8N · Dify · 钉钉 Webhook |
| 基础设施 | Docker Compose · CentOS 7.9 · Miniconda · MinIO · etcd |
| 前端 | Vue.js 3 (CDN) · Element Plus · Axios |

## 协作规范

- 所有文件修改、脚本执行由 AI 自行完成并检查，只汇报结果。
- 破坏性操作（删文件、强制推送、清空数据库等）或需在 CentOS 执行的命令，先向用户申请许可。
- 操作结束简短告知"做了什么"，不做多余解释。

# 2026-04-27 升级补充

- 第 17 步知识库变更快照与回滚基础版已落地：保存材料库前自动快照，管理员可在 `admin.html` 查看快照并一键回滚。
- 第 18 步 MinIO 文件存储与临时下载链接已落地：新增 `quote-minio` 编排、`/api/v1/files` 文件接口和 `admin.html` 文件存储面板。
- 第 19 步报价任务附件接入 MinIO 已落地：`MINIO_ENABLED=true` 时异步报价上传附件写入 MinIO，任务表只保存 `file_object_id`。

# 2026-04-28 升级补充

- 第 20 步一键启动与自愈编排已落地：CentOS `ens33` 自动 DHCP，Docker 服务自恢复，Windows `start_watchdog.ps1` 开机后台重试，`start_all.ps1` 启动前等待 MySQL/Redis/RAG/n8n/MinIO，再启动 Celery 和 FastAPI。
- 第 21 步运维监控与告警已落地：新增 `/api/v1/admin/ops/dashboard`，管理员页面顶部可查看基础服务探活、异常日志聚合和卡住任务提醒。
- 第 22 步数据库迁移治理已落地：新增 Alembic 迁移体系、基线迁移、`upgrade_database.ps1` 和启动前自动迁移，后续表结构变更不再继续堆到 `main.py`。

# 2026-05-02 升级补充

- P2 报价一致性治理已落地：`admin.html` 报价任务队列新增"详情"弹窗，含事件流、AI 结果、完整消息三 tab，可精准定位报价差异根因。
- P4 知识库发布流程增强已落地：热更新后自动触发 RAG 评测，结果写入 `rag_eval_reports` 表，知识库面板内嵌质量报告，质量下滑时橙色警告。

# 2026-05-05 升级补充（企业基线四步 + 安全治理）

- **P0 安全止血**：明文密钥全部替换，`WEBHOOK_SECRET`/`RELOAD_SECRET` 已轮换，n8n 密钥文件已解除 Git 追踪。
- **P1 n8n 黑盒消除**：两条 workflow 脱敏后纳入 `n8n_workflows/` 版本管理。
- **P3 主动告警**：`ops_monitor.py` 新增钉钉告警推送，去重限流，配置项 `ALERT_DINGTALK_WEBHOOK`。
- **F1 前端步骤动态适配**：图片/文字输入显示不同进度步骤。
- **E1 MySQL 迁移**：FastAPI 数据库从 SQLite 全面切换至 CentOS MySQL `ai_quotation`。
- **E2 HTTPS**：Caddy 反向代理，`tls internal` 自签 CA，访问入口改为 `https://<局域网IP>/`。
- **E3 定时备份**：`backup_all.ps1` 每日 03:00 备份，滚动保留 7 天。
- **E4 登录限流**：`slowapi` 集成，每 IP 每 5 分钟最多 10 次登录，Redis 不可用时内存降级。

# 2026-05-06 升级补充（后端架构重构）

- **P0 chat.py 拆分**：643 行 God File 按职责拆为 `quote.py`、`materials.py`、`history.py`、`users.py`；chat.py 保留兼容层，API 路径全部不变。
- **P0 lifespan 启动治理**：启动副作用（数据库等待、schema 迁移、默认密码检测）集中为 `_run_startup_database_tasks()`，在 `lifespan` 内执行，消除模块级副作用。
- **P0 统一鉴权依赖**：新增 `app/dependencies.py`，`get_current_user` / `require_admin` 所有路由共用。
- **P1 confirm_push schema**：请求体改为 Pydantic schema，`schemas/quote.py` 明确字段约束。
- **P1 物料库入库**：新增 `models/material.py`，知识库条目持久化至 MySQL。
- **P2 requests → httpx**：对外 HTTP 调用全改为 `httpx.AsyncClient`，消除异步路由同步阻塞。
- **P3 统一响应格式**：新增 `core/responses.py`，`api_ok` / `api_page` 统一 REST 接口响应结构。
- **P3收口 前端统一读取**：前端三页面统一通过 `res.data` 读取响应。
- **收尾配置清理**：新增 `LEGACY_MATERIALS_FILE` 和 `RAG_EVAL_REPORT_DIR`；`MATERIALS_FILE` 仅作为旧 JSON 导入兼容 alias。
- **验收状态**：Alembic 当前为 `20260505_0003`；旧物料已入库 70 条；RAG eval `quality_ok=True`；`pytest` 为 `55 passed`。
- **Git 状态**：最新提交 `6e54c09 clarify legacy materials config` 已推送至 GitHub `main`。

# 2026-05-20 升级补充（BIZ-1a 商务台账 v1）

- **当前环境验证完成**：BIZ Track BIZ-1a 商务台账 v1 已完成代码、测试、前端 build 和当前 9000 环境 smoke；`python -m pytest` 为 `124 passed`，`npm.cmd run build` 通过，`scripts/biz1a_business_ledger_smoke.ps1` 输出 `created=2 cancelled=1 feature_flag=true`、`event_count=5`、XFF 审计 `ip_address=1.2.3.4`。
- **数据库结构**：新增 Alembic `20260520_0016`；`client_inquiries` 增加 `direction`、`stage`、`next_followup_at`、`cancelled_at`、`cancelled_by_id`、`cancel_reason`，并将 `first_response_time` 改为 nullable；新增 `client_inquiry_events` 审计表。
- **功能开关与接口**：新增 `FEATURE_BUSINESS_LEDGER`（默认关闭）；接口为 `POST /api/v1/business-ledger`、`GET /api/v1/business-ledger`、`GET /api/v1/business-ledger/{id}`、`PATCH /api/v1/business-ledger/{id}`、`POST /api/v1/business-ledger/{id}/cancel`。
- **前端入口**：Vite 壳内联新增 `/admin/business-ledger`，未新增 `views/BusinessLedger.vue`，未迁移旧 `index.html` / `admin.html` / `app.html`。
- **权限与审计**：staff 仅查看/编辑自己负责的 outbound 记录；admin / system_admin 可查看全员、转交负责人并作废；审计上下文按 X-Forwarded-For 首段优先记录真实终端 IP，以适配 Caddy 反向代理。
- **字段约定**：BIZ-1a v1 已落地字段为 `client_name` / `client_phone`；项目名 / 公司名暂由 `notes` 承载，是否拆字段待真实台账数据积累后评估。
- **时间口径**：BIZ-1a 时间字段沿用 Phase 2 `client_inquiries` 的 naive Asia/Shanghai 口径，与 response-speed 当前统计保持一致；后续若 BIZ-3 经营驾驶舱需要跨时区聚合，再统一切换到 timezone-aware UTC。

# 2026-06-01 升级补充（BIZ-3b-3-0 关键节点证据硬门禁实现规划）

- **定位**：BIZ-3 已落地的是「企业内部工程项目进度中台」（非旧称「经营驾驶舱」），文档总目录为 `AI_Middle_Office/docs/biz-3-project-progress-index.md`。本阶段为硬门禁开发前的规划，仅出规划文档 `docs/biz-3b-3-0-key-node-hard-gate-planning.md`，**不改库、不改代码、不新增 Alembic**。
- **节点分级**：A 级首批升级为 `complete_required`（竣工精装验收、隐蔽工程验收、结算确认、设计成果交付）；B 级待确认（含 `compact=1` 的收款节点，`compact=0` 的竣工款收款不在内）；C 级保持 `soft_reminder`。
- **有条件硬门禁**：缺证据时普通成员阻断返回 `409 EVIDENCE_HARD_GATE_BLOCKED`，真正无操作权限返回 `403 PERMISSION_DENIED`；项目经理 / 管理员可填 `bypass_reason`（≥6 字，缺则 `422 EVIDENCE_BYPASS_REASON_REQUIRED`）放行。门禁只卡完成（→100%），不卡提交（→80%）。
- **放行审计**：放行写事件 `task_completed_bypass_gate`，决策快照冻结进现有 `project_task_events.payload_json`（Text），不新增列。
- **实现两步走**：BIZ-3b-3-1 先落显式字段 `evidence_requirement` / `evidence_policy` / `is_key_node`（新增 Alembic revision）并回填存量任务、运行时改为只读显式列，本步不引入阻断、完成仍走 3b-2a 软提醒；BIZ-3b-3-2 再实现硬门禁、放行接口、放行事件与前端三态交互。两步均完成后硬门禁才正式生效。
- **验收基线（规划阶段沿用 BIZ-3b-2b）**：后端 `289 passed`、前端 `npm run build` 通过、数据库 head `20260601_0027`。后续已由 BIZ-3b-3-1 更新基线。

# 2026-06-01 升级补充（BIZ-3b-3-1 显式字段落库与存量回填）

- **定位**：本阶段已把 3b-2c/3b-3-0 规划中的数据底座落地，但仍不启用硬门禁；完成动作继续走 BIZ-3b-2a 软提醒。
- **数据库**：新增 Alembic `20260601_0028`，`project_tasks` 新增 `evidence_requirement` / `evidence_policy` / `is_key_node`，并为 `evidence_policy`、`is_key_node` 建索引；当前数据库 head 为 `20260601_0028`。
- **读写侧**：EPC 模板、单人试运行模板、手工任务创建时直接写入显式字段；运行时优先读取显式列，EPC `description` 解析仅作为旧数据兜底。
- **存量回填**：当前环境任务总数 `146`，回填更新 `123`；EPC compact `41`、EPC full `82`、单人模板 `18`、手工/其他 `5`；`soft_reminder=123`、`none=23`、`complete_required=0`；回填后 dry-run `updated_task_count=0`，无模板匹配失败、无成果解析失败。
- **明确未做**：无 `409 EVIDENCE_HARD_GATE_BLOCKED`，无 `bypass_reason`，无 `task_completed_bypass_gate`，A 级 4 个节点本阶段仍为 `soft_reminder`。
- **验收基线**：后端全量 `290 passed, 9 warnings`，`ai-web npm run build` 通过。下一步进入 BIZ-3b-3-2 硬门禁与放行事件。

# 2026-06-02 升级补充（BIZ-3b-3-2 complete_required 硬门禁与放行事件）

- **定位**：BIZ-3b-3-2 已把 A 级 4 个 EPC 节点启用为 `complete_required`，并已通过当前环境业务验收；硬门禁只卡完成到 100%，提交到 80% 仍沿用软提醒。
- **节点范围**：首批仅包含 `设计成果交付`、`隐蔽工程验收`、`竣工精装验收`、`结算确认`；B 级节点仍保持 `soft_reminder`，不自动扩展。
- **后端行为**：缺证据的 `complete_required` 节点，普通任务负责人返回 `409 EVIDENCE_HARD_GATE_BLOCKED`；项目经理 / 管理员必须填写 `bypass_reason`（至少 6 字），否则 `422 EVIDENCE_BYPASS_REASON_REQUIRED`；放行成功写 `task_completed_bypass_gate` 和决策快照。
- **当前库激活**：已执行 `scripts/biz3b32_activate_project_task_hard_gates.py --apply`；当前库 `total_task_count=146`、`a_level_candidate_count=8`、`complete_required_count=8`、`soft_reminder_count=115`、`none_count=23`，再次 dry-run `updated_task_count=0`。
- **前端交互**：Vite 项目任务表显示 `关键节点` / `需证据` 标签；硬门禁缺证据完成时，无放行权限用户看到阻断提示，有权限用户填写放行原因。
- **验收基线**：后端全量 `292 passed, 11 warnings`，`ai-web npm run build` 通过；不新增 Alembic，数据库 head 仍为 `20260601_0028`。重启后业务走读已确认普通成员阻断、项目经理/管理员放行、项目动态事件、补证据后正常完成、提交到 80% 不阻断和 B 级节点保持软提醒均无问题。

# 2026-06-02 升级补充（BIZ-3c 经营驾驶舱轻量 MVP）

- **规划文档**：`AI_Middle_Office/docs/biz-3c-business-dashboard-lite-mvp-planning.md`。
- **定位**：BIZ-3c 先做只读经营总览，复用现有报价、成本库、项目进度和系统健康数据，用于内网试运行和管理层汇报；完整合同/回款/项目成本/毛利模型留到 BIZ-3d。
- **BIZ-3c-1 状态**：后端只读聚合接口已完成代码层验证；新增 `FEATURE_DASHBOARD_BUSINESS_LITE`、`GET /api/v1/admin/dashboard/business-lite` 和 `app/services/business_lite_dashboard.py`，复用 `require_dashboard_viewer`，默认 `range=last_30_days`，沿用 `today/week/month/last_30_days`。
- **BIZ-3c-2 状态**：前端经营总览标签页已完成代码层验证；`/admin/dashboard` 新增“经营总览”，展示 8 个经营指标卡、风险与待处理列表、运行摘要、跳转入口和 `section_errors` 局部降级提示。
- **BIZ-3c-3 状态**：趋势图 + 风险规则精化已完成代码层验证；后端新增 `quote.daily_trend`、`cost.status_distribution`、`cost.source_distribution`、`project_progress.daily_trend`、`project_progress.hard_gate_bypassed_missing_evidence_count`，前端经营总览展示报价趋势、项目证据趋势和分布概览。
- **边界**：BIZ-3c-1 / BIZ-3c-2 / BIZ-3c-3 不新增数据库结构、不新增 Alembic、不计算毛利/回款率/逾期应收、不展示成本敏感明细；成本审计事件使用 `cost_access_audit_logs`；完整经营模型留到 BIZ-3d。
- **验证**：BIZ-3c-3 后端聚焦测试 `5 passed, 1 warning`；经营总览 / 成本 RAG 同步 / 成本审计 / 项目进度交叉依赖测试 `31 passed, 6 warnings`；`ai-web` 执行 `npm.cmd run build` 通过。
- **建议下一步**：BIZ-3c 轻量 MVP 进入小范围试运行观察；完整合同/回款/项目成本/毛利驾驶舱等 BIZ-3d 业务口径确认后再启动。

# 2026-06-02 升级补充（FE-UX-1 前端体验重构一期）

- **规划文档**：`AI_Middle_Office/docs/fe-ux-1-admin-experience-refactor-planning.md`。
- **定位**：小范围内网试运行前的中后台体验补强，不是新业务模块，也不是整站前端重写。
- **模板参考**：主参考 `vue-pure-admin` / `Vue3 Element Admin`，信息架构参考 `Ant Design Pro`，视觉干净度参考 `PrimeVue Sakai`；不搬模板工程、不换 UI 库、不迁移到 React。
- **边界**：继续使用 Vue3 + Vite + Element Plus；不新增数据库结构、不新增 Alembic、不改变报价、成本库、项目进度、权限和审计规则；不一次性迁移旧 `index.html`。
- **分期**：FE-UX-1-1 应用外壳与视觉基础；FE-UX-1-2 报价工作台流程优化；FE-UX-1-3 AI 预审弹窗重构；FE-UX-1-4 成本库高级表格优化；FE-UX-1-5 经营总览与项目进度体验补强；FE-UX-1-6 试运行体验验收包。
- **当前进度**：FE-UX-1 前端保守型体验重构一期已完成并通过人工验收。FE-UX-1-1 应用外壳与视觉基础、FE-UX-1-2 报价工作台流程优化、FE-UX-1-3 AI 预审弹窗重构、FE-UX-1-4 成本库高级表格优化、FE-UX-1-5 经营总览与项目进度体验补强均已验收通过；FE-UX-1-6 已形成 `AI_Middle_Office/docs/fe-ux-1-trial-experience-acceptance.md` 试运行体验验收包。不改报价接口、价格口径、成本库 active 规则、项目硬门禁、草稿保存、推送或审计规则。
- **验证**：`index.html` 内联脚本解析通过；9000 的 `/admin/cost-db`、`/admin/dashboard`、`/admin/projects` 返回 200；`ai-web` 执行 `npm.cmd run build` 通过。
- **建议下一步**：FE-UX-1 暂时收束；后续如需更明显的界面变化，可单独规划“视觉统一增强”专项。

# 2026-06-02 升级补充（FE-UX-2 Apple-like 正式视觉增强第一版）

- **规划文档**：`AI_Middle_Office/docs/fe-ux-2-apple-like-visual-upgrade.md`。
- **定位**：在 FE-UX-1 保守型体验重构后，进行第一版更明显的视觉统一增强；参考“浅色、精致、正式、克制”的 Apple-like 气质，但不套 Apple 官网模板、不复刻品牌、不做营销页。
- **边界**：不新增数据库结构、不新增 Alembic、不改变报价接口、价格口径、成本库 active 规则、项目硬门禁、草稿保存、推送、RAG 同步或审计规则；不引入新 UI 库、不换技术栈、不整站重写。
- **已落地**：Vite 管理台新增品牌 lockup、登录页品牌介绍区、浅色磨砂 topbar、Element Plus 通用控件增强、指标卡/经营总览/成本库/项目概览卡片统一；旧 `index.html` 报价工作台同步浅色导航、流程条、消息区、输入区和 AI 预审弹窗卡片质感。
- **当前进度**：FE-UX-2-1 第一版已完成并通过人工视觉验收；后续可做 FE-UX-2-2 逐页精修报价工作台、预审弹窗、经营总览和成本库表格。
- **验证**：`ai-web` 执行 `npm.cmd run build` 通过；旧 `index.html` 内联脚本解析通过；`git diff --check` 未发现空白错误；9000 的 `/login`、`/admin/dashboard`、`/admin/cost-db`、`/admin/projects`、`/index.html` 返回 200。本轮内置 Browser 截图验收未完成，用户已确认人工视觉验收通过。
