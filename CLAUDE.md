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
│   ├── app/main.py                  # FastAPI 入口
│   ├── app/api/v1/chat.py           # 核心路由：GLM-4V、N8N、sync_milvus
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

## 账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | 123 | 管理员 |

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
