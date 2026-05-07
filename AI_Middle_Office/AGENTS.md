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
- 当前未完成/暂缓项：P2 候选 prompt 自动重跑、P3 Admin 知识候选审核面板、P4 运行态手工验收、P5 LangGraph 触发评估。
- 当前数据库迁移 head：`20260507_0006`；生产数据库若仍低于 head，需执行 Alembic 升级后启用完整反馈、Prompt 回归和知识候选记录。
- 最新自动化验证：`python -m compileall app` 通过，`python -m pytest` 为 `64 passed`

## 关键模块

- `app/main.py`：FastAPI 入口、HTML 托管、路由注册、健康检查、启动期数据库兼容迁移。
- `app/core/config.py`：集中读取 `.env` 配置，包含 n8n、RAG、Celery、MinIO、代理、数据库和模型网关参数。
- `app/api/v1/auth.py`：JWT 登录、当前用户、改密。
- `app/api/v1/chat.py`：旧兼容导出层，核心路由已拆分，保留历史 import 路径。
- `app/api/v1/quote.py`：`/chat` SSE 报价流与 `/confirm_push`。
- `app/api/v1/quote_feedback.py`：报价反馈闭环与 admin 反馈分析接口，包括 summary / list / detail。
- `app/api/v1/prompt_regression.py`：Prompt 回归评测接口，包括黄金案例 build/list 和回归报告 run/latest/history。
- `app/api/v1/knowledge_candidates.py`：知识库治理接口，包括候选 build/list/summary、RAG trace 洞察、approve/reject。
- `app/api/v1/materials.py`：物料库 CRUD、快照、回滚、CSV 导入和 RAG reload。
- `app/api/v1/history.py`：报价历史记录。
- `app/api/v1/users.py`：用户配额管理。
- `app/api/v1/quote_jobs.py`：新版异步报价任务 API，创建、查询、事件流、取消、重试、超时标记。
- `app/services/quote_job_runner.py`：后台任务执行链路，负责文件读取、GLM-4V、n8n 调用、结果落库和额度扣减。
- `app/services/quote_feedback.py`：报价反馈闭环服务，记录 AI 初稿、人工确认稿、字段级修正、Dify/prompt 版本和 RAG trace。
- `app/services/prompt_regression.py`：从真实报价反馈固化黄金案例，并计算 prompt 版本的总价偏差、格式错误率、遗漏率、打回率和综合分。
- `app/services/knowledge_candidates.py`：从真实反馈生成知识候选，人工确认后先快照再新增或更新物料库条目。
- `app/services/quote_dispatcher.py`：按 `TASK_QUEUE_MODE` 分发到 Celery/local/inline/disabled。
- `app/services/model_gateway.py`：统一模型与 n8n 调用日志、耗时、错误、熔断。
- `app/services/file_storage.py`：MinIO 文件上传、读取、临时下载链接和健康检查。
- `app/api/v1/ops.py` + `app/services/ops_monitor.py`：管理员运维面板，聚合基础服务、日志和卡住任务。
- `app/tasks/`：Celery app 与 worker task 入口。
- `alembic/`：数据库迁移基线。
- `verify_startup.ps1`：重启后固定验收脚本，检查 FastAPI、worker、RAG、n8n、MinIO、MySQL、Redis。
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
- 正式知识库条数为 70 条
- 物料库主存储为 MySQL `materials` / `material_snapshots`
- `LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 仅作为旧 `rag_materials.json` 空库导入源
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

- Main material storage is MySQL `materials` / `material_snapshots`; `LEGACY_MATERIALS_FILE` and the older `MATERIALS_FILE` env var are only used to import an existing `rag_materials.json` into an empty database.
- RAG eval report output is controlled by `RAG_EVAL_REPORT_DIR` and no longer depends on the legacy material JSON file path.
