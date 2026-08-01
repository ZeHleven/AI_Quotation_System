# 报价资料研判 Agent Phase 3c：证据分层存储

## 结论

招标资料不再把完整解析正文长期保存在 MySQL。当前链路为：

```text
原始上传文件
  -> MinIO: bid_tender_source
  -> 解析器
  -> MinIO: 不可变证据包（完整文本 + 原始分段 + 标准证据块）
  -> MySQL: 项目、文档、证据 ID、顺序、定位、哈希、长度、对象指针
  -> 索引 outbox
  -> Milvus: 项目/manifest 隔离的向量与检索副本
  -> MCP: 只接受召回 ID，回 MySQL 校验范围，再从 MinIO 读取权威正文
```

Milvus 是召回索引，不是事实源。即使索引返回了错误项目、旧 manifest
或错误 block ID，MCP 的 MySQL 范围门也会丢弃它；最终交给 Agent 的正文
必须来自 MinIO，并通过证据包 SHA-256、文档身份、块身份和正文哈希校验。

## MinIO 证据包

一个解析文档只生成一个不可变 JSON 证据包，避免为每个文本块创建小对象。
对象键采用内容寻址：

```text
bid_tender_evidence_body/{case_id}/{document_id}/{package_sha256}.json
```

证据包包含：

- schema 版本；
- 项目、文档、源文件和解析器身份；
- 完整解析文本；
- 原始解析 segments；
- 标准化证据块、定位、关键词、块哈希和正文。

## MySQL 轻量索引

Alembic：`20260728_0072`

`bid_evidence_documents` 新增 MinIO 正文对象元数据；`bid_evidence_blocks`
的 `content` 改为可空，并新增 `content_length`；`bid_project_files` 新增
解析产物对象指针。

新资料在
`TENDER_EVIDENCE_BODY_STORAGE_ENABLED=true` 且 MinIO 启用时：

- 写入 MinIO 证据包；
- `bid_evidence_blocks.content = NULL`；
- `bid_project_files.extracted_text/segments_json = NULL`；
- 仅保留轻量索引与完整性哈希。

开关关闭或 MinIO 未启用时仍支持旧 MySQL 正文模式，便于测试和分阶段部署。

## 兼容与迁移

维护命令：

```powershell
# 只盘点，不写入
python scripts/tender_evidence_body_migrate.py

# 先复制到 MinIO，保留 MySQL 正文
python scripts/tender_evidence_body_migrate.py --run

# 逐块读取并校验 MinIO
python scripts/tender_evidence_body_migrate.py --verify

# 只对已写入且现场校验通过的文档清理 MySQL 正文
python scripts/tender_evidence_body_migrate.py --run --purge-mysql-content
```

MCP 正文读取器同时支持：

- `minio`：从不可变证据包读取并做完整性校验；
- `mysql_legacy`：读取旧 `content`，同样校验正文哈希。

因此可以逐文档迁移，不需要一次性停机切换。

## 当前环境验收（2026-07-28）

- 数据库升级至 `20260728_0072 (head)`；
- 5 个历史证据文档复制到 MinIO；
- 2373/2373 个证据块逐块完整性校验通过；
- 清理后 MySQL 中仍保存正文的证据块为 0；
- 再次从 MinIO 校验 2373/2373 块通过；
- 真实数据库 repository 搜索和上下文读取能从 MinIO 正确回填正文；
- CentOS RAG 已发布 `/api/v1/tender-evidence/reindex` 与
  `/api/v1/tender-evidence/search`；
- 独立 Milvus 集合 `tender_evidence_blocks_v1` 已建立，未改动报价
  RAG 别名 `enterprise_quotation_rag`；
- 4 个 active manifest 索引任务全部完成，块数分别为
  `1 / 1869 / 381 / 122`，合计 2373；
- 真实混合检索已验证“Milvus 返回 ID -> MySQL 项目/manifest 范围校验
  -> MinIO 回读权威正文 -> 正文哈希校验”；
- 大资料索引已改为线程池执行、每 128 块分批写入并记录进度；
  检索超时保持 30 秒，索引超时独立为 900 秒；
- 旧报价 `/api/v1/retrieve` 回归返回 200；
- FastAPI `/health/ready` 为 `ready`；
- MCP 8012 和 Agent Worker 均已重启，Worker 心跳为 `online`；
- 解析、证据库、索引服务与分层混合检索联合回归 `25 passed`。

CentOS 变更前备份位于
`/opt/rag_service/backups/tender-evidence-phase3c-20260728-1732`。
RAG 当前使用轻量覆盖镜像
`rag_service-rag-service-tender:phase3c`；MinIO 正文、MySQL 轻量元数据
和 Milvus 检索副本三层均已进入当前内网开发运行态。
