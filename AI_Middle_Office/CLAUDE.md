# AI 智能报价中台 — 项目上下文文档
> 最后更新：2026-04-28

---

## 一、系统架构

### 整体拓扑
```
用户前端 (Vue.js)
      ↓
FastAPI 主网关 (Windows, Port 9000)
      ↓              ↓
   N8N 工作流      GLM-4V 图像识别
 (CentOS, 5678)   (智谱 AI API)
      ↓
  Dify + DeepSeek-R1（报价优化）
      ↓
RAG 检索服务 (CentOS, Port 8001)
      ↓
Milvus 向量数据库 (CentOS, Port 19530)
```

### 双机部署
| 机器 | 角色 | 运行方式 |
|------|------|---------|
| Windows | FastAPI 主网关、Vue.js 前端 | Miniconda (`C:\Users\12521\miniconda3\python.exe`) + uvicorn |
| CentOS 7.9 (192.168.88.128) | N8N、Dify、RAGFlow、Milvus、RAG服务 | Docker Compose |

### CentOS 磁盘状态
- 总容量：80GB（2026-04-22 从 48GB 扩容）
- 当前使用：约 42GB（54%），剩余 ~36GB

---

## 二、关键文件路径

### Windows 端
```
C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\
├── app.html                     # 登录门户（Vue3 + ElementPlus，JWT鉴权 + 强制改密弹窗）
├── index.html                   # 业务工作台（AI测算 + 重试按钮 + 历史记录抽屉）
├── admin.html                   # 知识库管理（仅admin，含用户配额管理面板）
├── eval_rag.py                  # RAG检索效果评测脚本（30条测试集，Hit@K + MRR）
└── AI_Middle_Office\
    ├── app\
    │   ├── main.py              # FastAPI 入口 + 启动时自动迁移 must_change_password 列
    │   ├── api\v1\
    │   │   ├── chat.py          # 核心路由：GLM-4V、N8N、sync_milvus、历史记录、用户管理
    │   │   ├── quote_jobs.py    # 异步报价任务：创建、状态查询、SSE 事件订阅
    │   │   └── auth.py          # 登录/注册/change_password 接口
    │   ├── models\
    │   │   ├── user.py          # User 表（含 must_change_password 字段）
    │   │   ├── quote_history.py # QuoteHistory 表
    │   │   └── quote_job.py     # QuoteJob 异步任务表
    │   ├── services\
    │   │   ├── quote_dispatcher.py   # local/celery/disabled 调度入口
    │   │   └── quote_job_runner.py   # 报价任务执行器
    │   ├── tasks\
    │   │   ├── celery_app.py         # Celery 应用配置
    │   │   └── quote_tasks.py        # Celery 报价任务
    │   └── core\
    │       ├── database.py
    │       └── security.py
    ├── create_admin.py          # 一次性脚本：创建 admin 用户
    ├── install_service.ps1      # Windows 任务计划程序安装脚本
    ├── .env                     # ZHIPU_API_KEY / WEBHOOK_SECRET / RELOAD_SECRET
    └── logs\                    # 服务日志目录
```

### CentOS 端
```
/opt/rag_service/
├── docker-compose.yml           # 编排：etcd/minio/milvus/rag-api-service，含日志轮转配置
├── Dockerfile                   # python:3.10-slim，含 jieba + rank-bm25（via docker commit）
├── hybrid_searcher.py           # 混合检索：向量 + BM25(jieba) + RRF + 低分过滤
├── rag_api_service.py           # FastAPI RAG 服务，含 /admin/reload 热更新接口
├── rag_materials.json           # 知识库（40条，北京2024年市场行情）
├── seed_milvus.py               # 一次性初始化脚本（仅写4条测试数据，正式数据用 reload）
└── show_logs.sh                 # 日志聚合脚本：./show_logs.sh 查看所有容器日志
```

---

## 三、核心配置参数

### FastAPI (chat.py)
```python
N8N_WEBHOOK_URL_CALC = "http://192.168.88.128:5678/webhook/budget-calc"
N8N_WEBHOOK_URL_PUSH = "http://192.168.88.128:5678/webhook/budget-push"
RAG_SERVICE_URL = "http://192.168.88.128:8001"
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")   # HMAC-SHA256 签名密钥
RELOAD_SECRET  = os.environ.get("RELOAD_SECRET", "")    # RAG 热更新鉴权
TASK_QUEUE_MODE=local                                   # local / celery / disabled
CELERY_BROKER_URL=redis://192.168.88.128:6380/0
CELERY_RESULT_BACKEND=redis://192.168.88.128:6380/1
```

### Milvus 连接
```python
host = "192.168.88.128"
port = "19530"
ALIAS = "enterprise_quotation_rag"   # 蓝绿别名（对外）
BLUE  = "quotation_blue"             # 物理集合
GREEN = "quotation_green"            # 物理集合
# 向量模型：maidalun1020/bce-embedding-base_v1，768维，COSINE，HNSW
```

### Docker Compose 服务
| 服务名 | 镜像 | 端口 | 日志限制 |
|--------|------|------|---------|
| milvus-etcd | quay.io/coreos/etcd:v3.5.5 | 内网 | 20MB × 3 |
| milvus-minio | minio/minio | 内网 | 20MB × 3 |
| milvus-standalone | milvusdb/milvus:v2.3.1 | 19530 | 50MB × 3 |
| rag-api-service | 本地构建 | 8001 | 50MB × 5 |

### .env 配置（Windows AI_Middle_Office/.env）
```
ZHIPU_API_KEY=<your-zhipu-api-key>
WEBHOOK_SECRET=<your-webhook-secret>
RELOAD_SECRET=<your-reload-secret>
TASK_QUEUE_MODE=local
CELERY_BROKER_URL=redis://192.168.88.128:6380/0
CELERY_RESULT_BACKEND=redis://192.168.88.128:6380/1
```

---

## 四、已完成的所有优化项

### ✅ P2 报价一致性治理（2026-05-02）
- `admin.html`（Codex 路径）：报价任务队列管理面板新增"详情"按钮
- 弹窗三个 tab：**处理事件流**（含 RAG 检索上下文）、**AI 报价结果**（result_json）、**完整消息**
- 无需新增表结构，直接复用 `QuoteJob.events_json` / `result_json` 字段
- 诊断方法：admin.html → 报价任务队列管理 → 清空状态筛选 → 点"详情" → 对比事件流

### ✅ 任务10：第一轮稳定性升级（2026-04-25）
- `app/core/config.py`：集中读取 `.env` 配置，统一数据库、JWT、N8N、RAG、代理、CORS、物料库路径
- `app/core/logging.py`：新增 JSON 结构化日志与 `trace_id` 上下文
- `main.py`：新增请求追踪中间件、`X-Trace-Id` 响应头、`/health/live` 与 `/health/ready`
- `auth.py`：新增 `GET /api/v1/auth/me`，刷新页面时可校验 Token 有效性
- `chat.py`：报价 SSE 事件统一附带 `trace_id`，关键异常写入结构化日志，物料库路径不再依赖启动目录
- `app.html`：刷新后通过 `/auth/me` 恢复登录态，退出时清理完整本地状态
- `index.html`：报价失败时显示后端错误详情和追踪 ID，401 自动清理登录态
- `requirements.txt` 与 `.env.example`：补齐后端依赖和配置模板

### ✅ 任务11：钉钉文件名、Excel 表头与 Webhook 鉴权兼容升级（2026-04-26）
- `confirm_push` 推送前会生成 ASCII 安全 Excel 文件名，避免中文文件名在钉钉链路中显示为 `???`
- 同一文件名会写入 `excel_filename`、`download_filename`、`filename`、`fileName`、`file_name`、`attachment_name`
- N8N `Convert to File` 已绑定 Webhook 中的安全文件名，最后发送文件消息节点已修复 `msgParam` JSON Body
- N8N `Code in JavaScript1` 已将 Excel 表头修复为 `施工项目`、`AI核准单价(元)`、`项目合计(元)`、`工艺备注`
- 已验证：钉钉 markdown 报价单、Excel 附件、文件名、表头均正常
- 修复后工作流备份：`C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\n8n_budget_push_fixed.json`
- `_sign_payload()` 保留旧 `X-Webhook-Secret`，并新增 `X-Webhook-Signature` HMAC-SHA256 签名头，便于后续平滑升级 N8N 验签逻辑

### ✅ 任务12：后端自动化测试与 CI 基线（2026-04-26）
- 新增 `pytest.ini`、`requirements-dev.txt`、`AI_Middle_Office/tests/`
- 测试覆盖健康检查、注册/登录/`/auth/me`、报价文件名字段、Webhook HMAC 签名
- 测试环境强制使用本地 SQLite 测试库，不访问真实 MySQL、N8N、RAG 或外部模型
- 新增 GitHub Actions：`.github/workflows/backend-ci.yml`
- CI 在 push/PR 时运行依赖安装、`python -m compileall app`、`pytest -q`

### ✅ 任务13：报价任务异步化基线（2026-04-26）
- 新增 `QuoteJob` 表，持久化异步报价任务的 job_id、状态、阶段、trace_id、事件流、结果和错误
- 新增接口：`POST /api/v1/quote/jobs` 创建任务，`GET /api/v1/quote/jobs/{job_id}` 查询状态，`GET /api/v1/quote/jobs/{job_id}/events` 订阅事件
- 新增 `TASK_QUEUE_MODE`：`local` 使用 Windows 本地后台线程，`celery` 使用 Celery + Redis，`disabled` 用于测试隔离
- 新增 Celery worker 入口：`app/tasks/celery_app.py`、`app/tasks/quote_tasks.py`
- `index.html` 已切换为任务化调用：先 `POST /quote/jobs`，再用带 Authorization 头的 `fetch` 读取 `/quote/jobs/{job_id}/events`
- 保留原 `/api/v1/chat` SSE 接口，作为回退兼容入口
- 新增测试 `tests/test_quote_jobs.py`；本地验证 `python -m compileall app` 与 `pytest -q`，结果 `9 passed`

### ✅ 任务14：Redis/Celery 正式生产化部署配置（2026-04-27）
- `rag_docker/docker-compose.yml` 新增 `quote-redis` 服务，宿主机端口 `6380`，数据目录 `/opt/rag_service/volumes/redis`
- 新增 `start_celery_worker.ps1`，Windows 下用 `--pool=solo --concurrency=1` 启动报价 Worker
- 新增 `install_celery_worker_service.ps1`，注册开机自启任务 `AI_MiddleOffice_CeleryWorker`
- `install_service.ps1` 和 `start_server.vbs` 的工作目录改为脚本所在目录，避免旧路径导致自启失败
- `/health/ready` 新增 `task_queue` 字段，Celery 模式检查 Redis broker 和 worker ping
- 新增 `DEPLOY_CELERY.md`，记录 CentOS Redis、Windows Worker、`.env` 切换与健康检查步骤

### ✅ 任务15：报价任务管理增强（2026-04-27）
- `GET /api/v1/quote/jobs`：任务列表，普通用户仅本人，admin 可按 `username` / `status` 查看全队列
- `POST /api/v1/quote/jobs/{job_id}/cancel`：取消 queued/running 任务，并尝试 revoke Celery task
- `POST /api/v1/quote/jobs/{job_id}/retry`：失败、取消、超时任务可重新创建并派发
- `POST /api/v1/admin/quote/jobs/mark_timeouts`：管理员批量标记超时任务，默认按 30 分钟阈值
- `admin.html` 新增报价任务队列面板，可筛选状态/用户并执行取消、重试、超时标记
- Worker 执行过程中会检查终态，避免已取消/超时任务继续写入预审结果
- 测试新增列表、取消、重试、管理员超时标记覆盖

### ✅ 任务16：模型调用统一网关（2026-04-27）
- 新增 `app/services/model_gateway.py`，统一封装 GLM-4V 图像识别与 n8n/Dify/DeepSeek 工作流调用
- 新增 `ModelCallLog` 表，记录 provider/model/endpoint/status/http_status/latency/input_chars/output_chars/estimated_cost/trace_id
- 新增内存级熔断保护，按 `provider:endpoint` 统计连续失败，超过阈值后短时间拒绝调用
- 新增配置：`GLM_VISION_MODEL`、`GLM_VISION_URL`、`MODEL_GATEWAY_TIMEOUT_SECONDS`、`MODEL_GATEWAY_FAILURE_THRESHOLD`、`MODEL_GATEWAY_CIRCUIT_RESET_SECONDS`、`MODEL_GATEWAY_COST_PER_1K_CHARS`
- 新增接口：`GET /api/v1/admin/model_gateway/stats`、`GET /api/v1/admin/model_gateway/circuits`
- `admin.html` 新增模型网关观测面板，展示调用次数、平均耗时、估算费用和熔断状态
- 测试新增模型网关统计与 admin 权限覆盖

### ✅ 任务9：admin 强制修改初始密码（2026-04-22）
- `main.py` 启动时检测 admin 密码是否仍为 `123`，是则设 `must_change_password=True`
- `auth.py` 新增 `POST /api/v1/auth/change_password`，新密码不少于6位
- 登录响应含 `must_change_password` 字段；`app.html` 检测到该字段为 true 时弹出强制修改弹窗

### ✅ 任务8：Docker 日志集中收集（2026-04-22）
- `docker-compose.yml` 所有容器加 `json-file` 日志驱动，带 max-size/max-file 滚动限制
- `/opt/rag_service/show_logs.sh`：一行命令聚合查看所有容器日志

### ✅ 任务7：前端重试机制（2026-04-22）
- 发送前保存 `lastInputText` / `lastInputFile`
- N8N 超时、GLM-4V 失败、网络异常时，错误气泡下方出现"🔄 重试"按钮
- 重试自动还原上次输入（含文件附件）并重发

### ✅ 任务6：sync_milvus 向量化迁移至 CentOS（2026-04-21）
- `rag_api_service.py` 新增 `POST /admin/reload`：接收物料列表，复用常驻 `_GLOBAL_MODEL` 完成向量化 + 蓝绿切换 + BM25 热更新
- `chat.py` 的 `sync_milvus` 改为向 RAG 服务发 POST，不再本地加载大模型

### ✅ 任务5：RAG 检索效果评测（2026-04-21）
- `eval_rag.py`：30 条测试集，4 个难度级别，Hit@K + MRR
- 用法：`python eval_rag.py [--url http://192.168.88.128:8001] [--top_k 5]`

### ✅ 任务4：报价历史记录（2026-04-21）
- `QuoteHistory` 表：confirm_push 成功后自动写入
- `GET /api/v1/history`：普通用户看自己，admin 可按 username 过滤
- `index.html` 历史记录抽屉 + 分页 + 明细查看

### ✅ 任务3：用户配额管理界面（2026-04-21）
- `GET /api/v1/admin/users` + `PATCH /api/v1/admin/users/{id}/quota`
- `admin.html` 顶部面板：表格内直接修改额度并确认

### ✅ 任务2：N8N Webhook HMAC-SHA256 签名（2026-04-21）
- FastAPI `_sign_payload()` 计算签名附加 `X-Webhook-Signature` 头
- N8N budget-calc / budget-push 两个 workflow 均有 Code 节点验签

### ✅ 任务1：N8N conversationId 去硬编码（2026-04-21）
- 每次请求生成 `str(uuid.uuid4())`，多用户上下文隔离

### ✅ 早期基础任务
- RAG 微服务容器化迁移 CentOS
- Milvus 蓝绿零停机切换（quotation_blue / quotation_green）
- GLM-4V 识别失败返回 None（不再静默假数据）
- N8N SQL 注入修复 + ZHIPU_API_KEY 环境变量化
- BM25 升级：jieba 分词 + RRF 融合 + 低分过滤
- 知识库扩充至 40 条（北京2024年市场行情）
- 前端统一入口 + FastAPI 托管三个 HTML
- 真实 JWT 鉴权替换 Mock 登录
- Windows 任务计划程序开机自启

---

## 五、冷启动步骤

### CentOS（先启动虚拟机）
`ens33` 已通过第 20 步配置为开机 DHCP 自动联网，正常情况下不再需要手动执行 `sudo dhclient ens33`。

Docker 已启用开机自启，Compose 服务使用 `restart: unless-stopped`。如需手动恢复：

```bash
cd /opt/rag_service && docker compose up -d
# 验证
docker ps --format "table {{.Names}}\t{{.Status}}"
curl -s -X POST http://localhost:8001/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "地砖", "top_k": 2}'
```

### Windows（自动）
开机后任务计划程序 `AI_MiddleOffice` 执行 `start_watchdog.ps1`。

`start_watchdog.ps1` 会每 3 分钟重试一次 `start_all.ps1 -NoBrowser`，最多持续 60 分钟，适配 Windows 先启动、CentOS 后启动的顺序。

`start_all.ps1` 每次启动会：

1. 等待 MySQL `5455`
2. 等待 Redis `6380`
3. 等待 RAG `8001`
4. 等待 n8n `5678`
5. 等待 MinIO `9002`
6. 启动 Celery Worker
7. 启动 FastAPI

直接访问：`http://localhost:9000/`

手动一键启动：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

手动控制：
```powershell
Start-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice   # 启动
Stop-ScheduledTask  -TaskPath "\" -TaskName AI_MiddleOffice   # 停止
```

详细说明见根目录 `STARTUP.md` 与 `AI_Middle_Office/DEPLOY_STARTUP.md`。

### 账号信息
| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | 已强制修改（初始 123） | 管理员 |

---

## 六、常用运维命令

```bash
# 查看所有容器日志（CentOS）
/opt/rag_service/show_logs.sh

# 只看 RAG 服务日志
/opt/rag_service/show_logs.sh rag

# 手动触发知识库热更新（在容器内执行）
docker exec rag-api-service python -c "
import json, requests
materials = json.load(open('/app/rag_materials.json'))
r = requests.post('http://localhost:8001/admin/reload',
    json={'materials': materials, 'secret': '<RELOAD_SECRET>'}, timeout=120)
print(r.text)
"

# 磁盘使用检查
df -h / && docker system df
```

### 异步报价 Worker（Windows，启用 Celery 模式时）
```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
$env:TASK_QUEUE_MODE="celery"
C:\Users\12521\miniconda3\python.exe -m celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

默认 `TASK_QUEUE_MODE=local` 不要求 Redis，会在 FastAPI 进程内用后台线程执行报价任务；生产切换为 `celery` 前需先部署 Redis。完整步骤见 `DEPLOY_CELERY.md`。

---

## 七、协作规范

- 所有文件修改、脚本执行由 AI 自行完成并检查，只汇报结果。
- 破坏性操作（删文件、强制推送、清空数据库等）或需在 CentOS 执行的命令，先向用户申请许可。
- 操作结束简短告知"做了什么"，不做多余解释。
- 每次完成重要改动后同步更新本文件和 ROADMAP.md。

---

## 八、技术栈速查

| 分类 | 组件 |
|------|------|
| 后端框架 | FastAPI · uvicorn · SQLAlchemy · Alembic · Celery · Redis · bcrypt · python-dotenv · python-jose |
| AI/大模型 | GLM-4V（图像识别）· DeepSeek-R1（报价优化）· BCEmbedding bce-embedding-base_v1（768维）|
| 向量数据库 | Milvus v2.3.1 · pymilvus 2.3.6 · HNSW · COSINE |
| 混合检索 | sentence-transformers · rank-bm25 · jieba · RRF 融合 |
| 自动化 | N8N · Dify · 钉钉 Webhook |
| 基础设施 | Docker · Docker Compose · CentOS 7.9 (80GB) · Miniconda · MinIO · etcd |
| 前端 | Vue.js 3 (CDN) · Element Plus · Axios |
| 数据库 | MySQL (N8N业务数据) · SQLite (FastAPI用户表 + 报价历史) |
| 自启 | Windows 任务计划程序 (AI_MiddleOffice 任务) |

---

## 九、系统使用说明

### 9.1 访问入口
浏览器打开：`http://localhost:9000/`

### 9.2 登录
- 登录成功后 Token 保存在 `localStorage`，刷新不需重新登录
- admin 首次登录（密码仍为 `123` 时）会弹出强制修改密码弹窗

### 9.3 业务工作台（AI 测算）
**两种方式：**
1. 上传装修清单图片（JPG/PNG）→ GLM-4V 识别
2. 直接输入文字清单，如：`客厅吊顶50平米，铺地砖80平米`

**处理流程（约 15~30 秒）：**
```
发送 → 创建异步报价任务 → GLM-4V（图片）→ N8N → RAG 检索底价 → Dify+DeepSeek-R1 优化 → 事件流返回进度 → 预审弹窗
```

**失败时**：错误气泡下方显示"🔄 重试"按钮，点击自动重发。

### 9.4 人工预审面板（Human-in-the-Loop）
所有字段可手动修改 → "确认无误，下发生成 Excel 并推钉钉" / "打回重填"

### 9.5 知识库管理（仅 admin）
标准流程：`导入 CSV 提炼草稿 → 审核修改 → 保存核定 → 一键热更新至 Milvus`

### 9.6 历史记录
点击输入区"📋 历史记录"按钮，右侧抽屉展示历史列表，支持分页和查看明细。

# 2026-04-27 升级补充

- 第 17 步知识库变更快照与回滚基础版已落地：保存材料库前自动快照，管理员可通过 `GET /api/v1/admin/materials/audit` 查看快照，并通过 `POST /api/v1/admin/materials/rollback/{snapshot_id}` 回滚。
- 第 18 步 MinIO 文件存储与临时下载链接已落地：新增 `quote-minio` 编排、`FileObject` 元数据表、`/api/v1/files` 文件接口和 `admin.html` 文件存储面板。
- 第 19 步报价任务附件接入 MinIO 已落地：`MINIO_ENABLED=true` 时异步报价上传附件写入 MinIO，Worker 通过 `file_object_id` 拉取附件，未启用时回退 `file_base64`。

# 2026-04-28 升级补充

- 第 20 步一键启动与自愈编排已落地：新增 `start_all.ps1`、`start_watchdog.ps1`、`install_centos_autostart.ps1`、`rag_docker/enable_centos_autostart.sh`，冷启动时自动等待 MySQL/Redis/RAG/n8n/MinIO，再拉起 Celery 和 FastAPI。
- 第 21 步运维监控与告警已落地：新增 `app/services/ops_monitor.py`、`app/api/v1/ops.py` 和管理员页“运维监控与告警”面板，支持 MySQL/Redis/Celery/RAG/MinIO/n8n 探活、异常日志聚合与卡住任务提醒。
- 第 22 步数据库迁移治理已落地：新增 Alembic 迁移体系、`20260428_0001_initial_schema` 基线迁移、`upgrade_database.ps1` 和启动前自动迁移；后续表结构变更统一通过 `alembic/versions/` 管理。

# 2026-05-02 升级补充

- 第 P4 步知识库发布流程增强已落地：热更新至 Milvus 成功后自动触发 RAG 检索评测（后台线程），结果写入 `rag_eval_reports` 表；新增接口 `GET /api/v1/admin/rag_eval/latest` 和 `/history`；`admin.html` 知识库面板内嵌评测结果展示区，质量下滑时显示橙色警告；阈值通过 `RAG_EVAL_WARN_HIT_RATE`（默认 0.70）和 `RAG_EVAL_WARN_MRR`（默认 0.50）配置。
