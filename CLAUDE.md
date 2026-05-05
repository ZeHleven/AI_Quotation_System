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
