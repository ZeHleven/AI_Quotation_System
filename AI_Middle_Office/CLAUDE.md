# AI 智能报价中台 — 项目上下文文档
> 每次开启新会话时，将此文件上传给 AI，即可恢复完整项目认知。
> 最后更新：2026-04-20

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

---

## 二、关键文件路径

### Windows 端
```
C:\Users\12521\Desktop\Clear_test\
├── app.html                     # 登录门户（Vue3 + ElementPlus，真实JWT鉴权）
├── index.html                   # 业务工作台（AI测算，iframe嵌入）
├── admin.html                   # 知识库管理（仅admin角色可见，iframe嵌入）
└── AI_Middle_Office\
    ├── app\
    │   ├── main.py              # FastAPI 入口，load_dotenv() + 托管前端HTML
    │   ├── api\v1\
    │   │   ├── chat.py          # 核心路由：GLM-4V、N8N调用、sync_milvus、require_admin
    │   │   └── auth.py          # 登录接口，返回 access_token + role + username
    │   └── core\
    │       ├── database.py
    │       └── security.py
    ├── create_admin.py          # 一次性脚本：创建/更新admin用户（username=admin, pwd=123）
    ├── install_service.ps1      # Windows自启安装脚本（任务计划程序，无需NSSM）
    ├── .env                     # ZHIPU_API_KEY 存放位置（不提交 git）
    ├── .gitignore               # 排除 .env、__pycache__、sql_app.db
    └── logs\                    # 服务日志目录（service.log / service_error.log）
```

### CentOS 端
```
/opt/rag_service/
├── docker-compose.yml           # 编排：etcd、minio、milvus、rag-api-service
├── Dockerfile                   # python:3.10-slim，含 rank-bm25
├── hybrid_searcher.py           # 混合检索核心（向量 + BM25 + RRF）
├── rag_api_service.py           # FastAPI RAG 服务入口
├── rag_materials.json           # 知识库数据（40条，北京2024年市场行情）
└── seed_milvus.py               # 一次性数据初始化脚本（蓝绿切换模式）
```

---

## 三、核心配置参数

### FastAPI (chat.py)
```python
N8N_WEBHOOK_URL_CALC = "http://192.168.88.128:5678/webhook/budget-calc"
N8N_WEBHOOK_URL_PUSH = "http://192.168.88.128:5678/webhook/budget-push"
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")  # 读自 .env
```

### Milvus 连接
```python
host = "192.168.88.128"
port = "19530"
COLLECTION_NAME = "enterprise_quotation_rag"   # 实际为 Alias
BLUE  = "quotation_blue"
GREEN = "quotation_green"
ALIAS = "enterprise_quotation_rag"
# 向量模型：maidalun1020/bce-embedding-base_v1，768维，COSINE距离，HNSW索引
```

### Docker Compose 服务（/opt/rag_service/docker-compose.yml）
| 服务名 | 镜像 | 端口 | 说明 |
|--------|------|------|------|
| milvus-etcd | quay.io/coreos/etcd:v3.5.5 | 内网 | 元数据存储 |
| milvus-minio | minio/minio | 内网（不对外） | 对象存储 |
| milvus-standalone | milvusdb/milvus:v2.3.1 | 19530 | 向量数据库 |
| rag-api-service | 本地构建 | 8001 | RAG 检索服务 |

---

## 四、已完成的优化项

### ✅ 任务10：N8N conversationId 去硬编码（2026-04-21）
- 原代码所有用户共享同一个固定 conversationId，多用户并发时 Dify 上下文互相污染
- 修复：`chat.py` 改为每次请求生成 `str(uuid.uuid4())`，每个请求独立隔离

### ✅ 任务11：N8N Webhook HMAC-SHA256 签名验证（2026-04-21）
- 原 Webhook 端点无任何鉴权，任何人知道地址即可调用算价引擎
- FastAPI 侧：`chat.py` 新增 `_sign_payload()` 函数，对请求体计算 HMAC-SHA256，以 `X-Webhook-Signature: sha256=<hex>` 头附加发出
- N8N 侧：通过 N8N API 在 budget-calc / budget-push 两个 workflow 的 Webhook 节点后插入 Code 节点，验签失败直接抛出异常终止流程
- 密钥：`AI_Middle_Office/.env` → `WEBHOOK_SECRET`（64位随机十六进制）

### ✅ 任务1：RAG 微服务迁移 CentOS
- CentOS 7.9 无 Python3，采用 Docker 容器化
- bitnami/etcd 镜像无法从国内拉取，通过 Windows `docker save` → `scp` → CentOS `docker load` 方式转移
- 修复 marshmallow 4.x 与 pymilvus 2.3.6 不兼容问题（固定 `>=3.13.0,<4.0.0`）

### ✅ 任务2：Milvus 蓝绿零停机切换
- 维护 quotation_blue / quotation_green 两个物理集合
- 对外暴露 enterprise_quotation_rag 别名，切换为原子操作
- 兼容新旧 pymilvus 的 `list_aliases()` API 差异
- sync_milvus 使用 `alter_alias` 优先、`create_alias` 兜底策略，规避别名冲突报错

### ✅ 任务3：GLM-4V 静默假数据修复
- 原代码在识别失败时返回硬编码假数据字符串，静默失败
- 修复：改为 `return None`，让上层业务逻辑感知失败

### ✅ 任务4：N8N SQL 注入修复 + API Key 环境变量化
- N8N Execute SQL 改为参数化查询（`?` 占位符 + Query Parameters）
- ZHIPU_API_KEY 从硬编码迁移至 `.env` 文件，`main.py` 用 `load_dotenv()` 加载

### ✅ 任务5：hybrid_searcher 升级真混合检索
- 新增 BM25 关键词检索通道（rank-bm25，字符级中文分词）
- RRF 融合算法：`score = Σ 1/(60 + rank)`
- 修复 BM25 字段名不匹配（price_total vs unit_price）
- 结合原有多意图拆分召回（按标点符号拆分长查询）

### ✅ 任务6：知识库数据扩充
- rag_materials.json 从4条测试数据扩充至40条
- 涵盖拆除、水电、墙面、地面、吊顶、门窗、厨卫、系统设备、隔墙等全流程工序
- 价格参考北京2024年市场行情

### ✅ 任务7：前端统一入口 + FastAPI 托管
- 三个 HTML 文件（app.html/index.html/admin.html）放于项目根目录
- FastAPI main.py 新增路由托管这三个页面
- 统一从 `http://localhost:9000/` 访问，无需打开本地 HTML 文件

### ✅ 任务8：真实鉴权替换 Mock 登录
- app.html 登录改为调用 `/api/v1/auth/login`，存储 JWT token
- auth.py 登录响应新增 `role` 和 `username` 字段
- admin.html 所有请求加 Authorization 头
- 4个管理员接口加 `require_admin` 依赖，非admin返回403

### ✅ 任务9：Windows 自启服务
- 通过任务计划程序（Task Scheduler）注册 `AI_MiddleOffice` 任务
- 使用 `C:\Users\12521\miniconda3\python.exe`，SYSTEM身份运行，开机自启
- 崩溃自动重启（最多5次，间隔1分钟）
- 安装脚本：`AI_Middle_Office\install_service.ps1`

---

## 五、冷启动步骤

### CentOS（先启动）
```bash
cd /opt/rag_service
docker compose up -d
# 验证
docker ps --format "table {{.Names}}\t{{.Status}}"
curl -X POST http://localhost:8001/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "地砖", "top_k": 1}'
```

### Windows（自动）
开机后任务计划程序自动启动 FastAPI，直接访问：
```
http://localhost:9000/
```

如需手动控制：
```powershell
Start-ScheduledTask -TaskName AI_MiddleOffice   # 启动
Stop-ScheduledTask  -TaskName AI_MiddleOffice   # 停止
```

### 账号信息
| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | 123 | 管理员（可访问知识库管理） |

---

## 六、协作规范

### AI 操作准则
- 所有文件修改、脚本执行、配置变更由 AI 自行完成后自行检查，无需用户逐步确认。
- 涉及破坏性操作（删除文件/分支、强制推送、清空数据库等）或需要用户执行的命令（如 CentOS 端命令），向用户提出许可申请后再执行。
- 每次操作结束只需简短告知用户"做了什么更新"，不做多余的解释和询问。
- 本文件为项目主上下文文档，每次新会话开始时上传以恢复完整项目认知。

---

## 七、已知问题 & 待优化项

| 优先级 | 问题 | 建议方案 |
|--------|------|---------|
| 中 | BM25 字符级分词精度有限 | 引入 jieba 分词提升术语识别 |
| 中 | RAG 检索效果无量化评估 | 引入 RAGAS 框架自动化评测 |
| 低 | N8N Webhook 无签名验证 | 加 HMAC 签名防伪造请求 |
| 低 | CentOS Docker 容器未设开机自启 | `docker compose` 加 `restart: always` 或 systemd 服务 |

---

## 八、技术栈速查

| 分类 | 组件 |
|------|------|
| 后端框架 | FastAPI · uvicorn · SQLAlchemy · bcrypt · python-dotenv · python-jose |
| AI/大模型 | GLM-4V（图像识别）· DeepSeek-R1（报价优化）· BCEmbedding bce-embedding-base_v1（768维）|
| 向量数据库 | Milvus v2.3.1 · pymilvus 2.3.6 · HNSW索引 · COSINE距离 |
| 混合检索 | sentence-transformers · rank-bm25 · RRF融合 · 多意图拆分 |
| 自动化 | N8N · Dify · 钉钉 Webhook |
| 基础设施 | Docker · Docker Compose · CentOS 7.9 · Miniconda · MinIO · etcd |
| 前端 | Vue.js 3 (CDN) · Element Plus · Axios |
| 数据库 | MySQL (N8N业务数据) · SQLite (FastAPI用户表) |
| 自启 | Windows 任务计划程序 (AI_MiddleOffice 任务) |

---

## 九、系统使用说明

### 9.1 访问入口

浏览器打开：`http://localhost:9000/`

---

### 9.2 登录

1. 输入用户名和密码，点击【登 录】
2. 登录成功后自动进入系统主框架，Token 保存在 `localStorage`，刷新页面不需重新登录
3. 右上角显示当前用户名和角色（管理员 / 业务员），点击可退出登录

| 账号 | 密码 | 角色 | 权限 |
|------|------|------|------|
| admin | 123 | 管理员 | 业务工作台 + 知识库管理 |
| 其他员工 | 注册时设置 | 业务员 | 仅业务工作台 |

---

### 9.3 业务工作台（AI 测算）

**入口**：左侧菜单 → 📊 业务工作台 (AI测算)

#### 两种使用方式

**方式一：上传装修清单图片**
1. 点击输入框左侧的 📎 按钮，选择清单截图（JPG/PNG，不支持 PDF）
2. 可同时在输入框补充文字说明（如"客厅+卧室"）
3. 点击【发送至 AI 中枢】

**方式二：直接输入文字清单**
1. 在输入框中描述施工内容，例如：
   ```
   客厅直线型吊顶50平米，铺地砖80平米，墙面刮腻子120平米
   ```
2. 支持用中文逗号、顿号、分号分隔多个项目
3. 点击【发送至 AI 中枢】或按 Enter 发送

#### 处理流程（约 15~30 秒）

```
发送 → GLM-4V 图像识别（如有图片）→ N8N 工作流 → RAG 知识库检索底价
     → Dify + DeepSeek-R1 报价优化 → 弹出预审面板
```

对话框会实时显示各阶段进度，最终弹出【AI 报价明细预审】弹窗。

---

### 9.4 人工预审面板（Human-in-the-Loop）

AI 完成测算后弹出预审弹窗，**所有数据均可手动修改**：

| 列 | 说明 |
|----|------|
| 施工项目 | 可直接点击编辑名称 |
| AI 建议单价(元) | 可手动调整 |
| 系统合计(元) | 可手动调整，右下角实时汇总总价 |
| 工艺与避坑备注 | 可补充施工要求 |

**两个操作按钮：**
- **打回重填**：关闭弹窗，流程打回，可重新输入指令
- **确认无误，下发生成 Excel 并推钉钉**：确认后自动生成报价 Excel 并推送至钉钉群

---

### 9.5 知识库管理（仅管理员）

**入口**：左侧菜单 → ⚙️ 知识库管理 (价格中枢)（非 admin 账号不可见）

#### 标准工作流

```
导入 CSV 提炼草稿 → 表格中审核修改 → 保存核定 → 一键热更新至 Milvus
```

#### 具体操作

**① 手动新增物料**
- 点击【+ 新增自定义物料】，在表格末尾追加一行空白条目
- 填写：施工项目名称 / 底层刚性单价 / 计价单位 / 标准施工工艺约束

**② 从历史成交记录导入（CSV）**
- 点击【导入新历史记录(CSV)提取草稿】，上传 CSV 文件
- CSV 需包含以下列（支持多种表头格式）：
  - 施工项目 / 项目名称 / item_name
  - AI核准单价(元) / 综合单价(元) / 单价 / unit_price
  - 单位 / unit
  - 规格/工艺/材料说明 / 备注说明 / 备注 / notes
- 系统自动过滤：**新工艺**（库中没有的）和**异动项**（价格偏差 >20%）追加到表格末尾，标红显示为"新提炼草稿"
- ⚠️ 必须是真实 CSV，不能把 Excel 文件改后缀名上传

**③ 审核草稿**
- 标红的草稿行即为待审项目，在表格中直接修改价格和备注
- 确认无误后点击【💾 保存表格并核定草稿】，草稿标记消除，数据写入本地 JSON

**④ 同步至 Milvus 向量库**
- 点击左侧【一键热更新至 Milvus】
- 采用蓝绿切换，同步期间 AI 检索服务不中断
- 同步成功后左侧面板显示最后同步时间，新数据立即对 AI 生效

---

### 9.6 服务管理

**手动启动 FastAPI（Windows 终端）**
```bash
cd C:\Users\12521\Desktop\Clear_test\AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 9000
```

**CentOS RAG 服务**
```bash
# 在 CentOS 上执行
cd /opt/rag_service
docker compose up -d
# 验证
curl -X POST http://localhost:8001/api/v1/retrieve -H "Content-Type: application/json" -d '{"query":"地砖","top_k":1}'
```
