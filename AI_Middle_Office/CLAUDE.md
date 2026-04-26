# AI 智能报价中台 — 项目上下文文档
> 最后更新：2026-04-22

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
C:\Users\12521\Desktop\Clear_test\
├── app.html                     # 登录门户（Vue3 + ElementPlus，JWT鉴权 + 强制改密弹窗）
├── index.html                   # 业务工作台（AI测算 + 重试按钮 + 历史记录抽屉）
├── admin.html                   # 知识库管理（仅admin，含用户配额管理面板）
├── eval_rag.py                  # RAG检索效果评测脚本（30条测试集，Hit@K + MRR）
└── AI_Middle_Office\
    ├── app\
    │   ├── main.py              # FastAPI 入口 + 启动时自动迁移 must_change_password 列
    │   ├── api\v1\
    │   │   ├── chat.py          # 核心路由：GLM-4V、N8N、sync_milvus、历史记录、用户管理
    │   │   └── auth.py          # 登录/注册/change_password 接口
    │   ├── models\
    │   │   ├── user.py          # User 表（含 must_change_password 字段）
    │   │   └── quote_history.py # QuoteHistory 表
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
ZHIPU_API_KEY=...
WEBHOOK_SECRET=019483697d32bcfe5ba55084d4ad23d5f244fb1662b92db9e0c9b0f871c832d9
RELOAD_SECRET=rag_reload_7f3a9d2e1b4c8f6a
```

---

## 四、已完成的所有优化项

### ✅ 任务10：第一轮稳定性升级（2026-04-25）
- `app/core/config.py`：集中读取 `.env` 配置，统一数据库、JWT、N8N、RAG、代理、CORS、物料库路径
- `app/core/logging.py`：新增 JSON 结构化日志与 `trace_id` 上下文
- `main.py`：新增请求追踪中间件、`X-Trace-Id` 响应头、`/health/live` 与 `/health/ready`
- `auth.py`：新增 `GET /api/v1/auth/me`，刷新页面时可校验 Token 有效性
- `chat.py`：报价 SSE 事件统一附带 `trace_id`，关键异常写入结构化日志，物料库路径不再依赖启动目录
- `app.html`：刷新后通过 `/auth/me` 恢复登录态，退出时清理完整本地状态
- `index.html`：报价失败时显示后端错误详情和追踪 ID，401 自动清理登录态
- `requirements.txt` 与 `.env.example`：补齐后端依赖和配置模板

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

### CentOS（先启动）
```bash
cd /opt/rag_service && docker compose up -d
# 验证
docker ps --format "table {{.Names}}\t{{.Status}}"
curl -s -X POST http://localhost:8001/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "地砖", "top_k": 2}'
```

### Windows（自动）
开机后任务计划程序自动启动 FastAPI，直接访问：`http://localhost:9000/`

手动控制：
```powershell
Start-ScheduledTask -TaskName AI_MiddleOffice   # 启动
Stop-ScheduledTask  -TaskName AI_MiddleOffice   # 停止
```

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
    json={'materials': materials, 'secret': 'rag_reload_7f3a9d2e1b4c8f6a'}, timeout=120)
print(r.text)
"

# 磁盘使用检查
df -h / && docker system df
```

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
| 后端框架 | FastAPI · uvicorn · SQLAlchemy · bcrypt · python-dotenv · python-jose |
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
发送 → GLM-4V（图片）→ N8N → RAG 检索底价 → Dify+DeepSeek-R1 优化 → 预审弹窗
```

**失败时**：错误气泡下方显示"🔄 重试"按钮，点击自动重发。

### 9.4 人工预审面板（Human-in-the-Loop）
所有字段可手动修改 → "确认无误，下发生成 Excel 并推钉钉" / "打回重填"

### 9.5 知识库管理（仅 admin）
标准流程：`导入 CSV 提炼草稿 → 审核修改 → 保存核定 → 一键热更新至 Milvus`

### 9.6 历史记录
点击输入区"📋 历史记录"按钮，右侧抽屉展示历史列表，支持分页和查看明细。
