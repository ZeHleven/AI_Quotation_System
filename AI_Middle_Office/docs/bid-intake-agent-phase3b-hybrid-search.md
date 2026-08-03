# 报价资料研判 Agent Phase 3b：项目隔离的混合证据检索

## 本阶段结论

Phase 3b 已完成代码层开发：

```text
MySQL active evidence blocks（事实源）
          ↓ 事务内 outbox
bid_evidence_index_jobs
          ↓ 可重试 worker
独立 Milvus 集合 tender_evidence_blocks_v1
          ↓
向量召回 + BM25 召回 + RRF 融合
          ↓
MCP Repository 回 MySQL 校验 active / project / block_id
          ↓
ReAct Agent
```

成本报价集合 `enterprise_quotation_rag` 没有被修改或混用。招标证据只复用现有 CentOS RAG 容器的 Embedding 模型和 Milvus 基础设施。

## 为什么不让 MCP 直接相信 Milvus

Milvus 是召回索引，不是事实源。索引可能：

- 尚未同步；
- 只写入了一部分；
- 仍保留旧 manifest；
- 因程序错误返回其他项目的 ID。

因此 MCP 使用两道确定性门：

1. 只有当前 manifest 对应的 `bid_evidence_index_jobs` 已 `completed`，且请求数量等于成功索引数量时，才调用混合检索。
2. 混合检索返回的 ID 必须回 MySQL 命中同一项目的 active 文档，并且 `block_id` 完全一致，才能交给 Agent。

任意条件不满足时自动回退数据库关键词检索。

## 数据结构

Alembic revision：`20260727_0065`

新增 `bid_evidence_index_jobs`：

- 项目、manifest ID、版本和哈希；
- 索引 schema 版本；
- queued / running / retryable / completed / failed / cancelled；
- 请求块数和成功索引块数；
- 尝试次数、服务地址、HTTP 状态和安全错误信息；
- Celery task ID 和执行时间。

唯一键为：

```text
manifest_id + index_schema_version
```

相同 manifest 重复入库不会重复创建同步任务。未来索引 schema 升级时，可以为同一个 manifest 重新建立新版本索引。

## 检索算法

### 向量通道

- 模型：继续复用 `maidalun1020/bce-embedding-base_v1`
- 维度：768
- Milvus：HNSW
- 距离：COSINE
- 集合：`tender_evidence_blocks_v1`

### BM25 通道

索引文本包括：

- `document_key`
- 证据正文
- 解析器提取的 keywords

中文使用 jieba，并补充连续双字 token，兼顾专业词语、日期和招标短语。

### 融合

使用 Reciprocal Rank Fusion：

```text
RRF(d) = Σ 1 / (k + rank(d))
```

默认 `k=60`。同一证据同时被向量和 BM25 召回时，排序会自然提升。

## 项目与版本隔离

每次 reindex 和 search 都必须携带：

- `case_id`：项目 UUID；
- `manifest_version`；
- `manifest_hash`。

Milvus 查询表达式同时过滤这三个字段。服务端只接受规范 UUID 和 SHA-256，不能通过输入拼接改变过滤表达式。

旧 manifest 数据即使仍保留在 Milvus，也不会进入当前检索结果。

## 索引服务接口

现有 CentOS RAG 服务新增两个内部接口：

```text
POST /api/v1/tender-evidence/reindex
POST /api/v1/tender-evidence/search
```

都必须携带：

```text
X-Tender-Index-Secret
```

未设置 `TENDER_EVIDENCE_INDEX_SECRET` 时接口保持拒绝访问。

相关实现：

- `rag_docker/tender_evidence_search.py`
- `rag_docker/rag_api_service.py`
- `rag_docker/Dockerfile`
- `rag_docker/docker-compose.yml`

## Windows / MCP 配置

```dotenv
TENDER_EVIDENCE_HYBRID_ENABLED=false
TENDER_EVIDENCE_SEARCH_URL=http://192.168.88.128:8001
TENDER_EVIDENCE_INDEX_SECRET=<独立强随机密钥>
TENDER_EVIDENCE_SEARCH_TIMEOUT_SECONDS=30
TENDER_EVIDENCE_INDEX_MAX_ATTEMPTS=3
```

CentOS `rag_docker/.env` 使用相同的：

```dotenv
TENDER_EVIDENCE_INDEX_SECRET=<独立强随机密钥>
```

默认关闭混合检索，完成 0065 迁移和 CentOS 容器升级后再显式开启。

MCP 数据库模式：

```powershell
python scripts/tender_evidence_mcp_server.py `
  --repository database `
  --search-mode auto `
  --transport streamable-http
```

- `auto`：环境开关打开时使用混合检索，否则关键词检索。
- `hybrid`：强制创建混合检索客户端，配置不完整时启动失败。
- `lexical`：明确只使用数据库关键词检索。

## 管理与维护入口

投标项目接口新增：

```text
GET  /api/v1/admin/bidding/projects/{project_uuid}/evidence/index-jobs
GET  /api/v1/admin/bidding/projects/{project_uuid}/evidence/index-status
POST /api/v1/admin/bidding/projects/{project_uuid}/evidence/index-jobs/{job_uuid}/retry
```

维护命令：

```powershell
python scripts/tender_evidence_index.py `
  --project-uuid "<bid_projects.project_uuid>" `
  --run
```

## 故障语义

- hybrid 未启用：索引任务保留 queued，MCP 使用数据库检索。
- 网络或 RAG 服务临时失败：任务进入 retryable。
- manifest 已被新版本取代：旧任务进入 cancelled/superseded。
- manifest 与块数不一致：永久失败，禁止发布不完整索引。
- hybrid 搜索超时、返回旧 manifest 或无完成索引：MCP 自动回退数据库检索。
- 跨项目 evidence ID 或错误 block ID：MySQL 回表时丢弃。

因此向量服务故障不会让 Agent 完全失去证据检索能力，也不会降低证据门的正确性。

## 已完成验证

- 证据入库会幂等创建索引 outbox。
- active manifest 可生成完整索引快照。
- 相同索引任务重复执行不会重复远程写入。
- 旧 manifest 任务会取消，不覆盖当前索引。
- 远程服务失败进入 retryable。
- 未完成索引不会被 MCP 查询。
- 混合结果保持 RRF 顺序。
- 跨项目 evidence ID 和错误 block ID 被回表校验丢弃。
- hybrid 服务异常时数据库关键词检索正常接管。
- reindex 对相同 manifest 幂等。
- case ID 表达式注入和正文哈希篡改会被拒绝。

## 当前边界

- 尚未迁移真实数据库。
- 尚未重建并部署 CentOS RAG 容器。
- 尚未对真实招标文件执行向量召回质量评测。
- 未开启 `TENDER_EVIDENCE_HYBRID_ENABLED`。
- 当前 BM25 为 RAG 服务进程内按 manifest 缓存；进程重启后从 Milvus 正文自动重建。

下一阶段建议进入 Phase 4a：把 LangGraph 研判图包装为可持久执行的 Agent Runtime，接入 assessment/run/checkpoint、异步 MCP session 和 Human-in-the-loop 恢复。
