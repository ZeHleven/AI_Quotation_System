# 报价资料研判 Agent Phase 2：真实证据数据底座

## 本阶段结论

Phase 2 已把 MCP 的证据后端从演示 JSON 扩展为可使用现有 MySQL 数据库的真实持久化仓库。

本阶段复用了现有：

- `bid_projects`
- `bid_project_files`
- 已解析的 `extracted_text`
- 已结构化的 `segments_json`

没有重复建设另一套招标项目、用户或上传表，也没有复用报价成本 RAG。

## 新增数据结构

Alembic revision：`20260727_0063`

### `bid_evidence_documents`

保存不可变的招标文档版本：

- 所属投标项目
- 来源 `BidProjectFile`
- 稳定 `document_key`
- 递增版本号
- 原文件 SHA-256
- 解析器版本
- active / superseded 状态

同一个 `project + document_key` 只应有一个 active 版本。

### `bid_evidence_blocks`

保存供 MCP 检索和引用的不可变证据块：

- `evidence_id`
- `block_id`
- 文档版本
- 原始顺序
- 页码、Sheet、单元格范围、章节
- 正文
- 正文 SHA-256
- 可选关键词

正文过长时按最多 12,000 字符拆块，避免把整份文件直接塞给模型。

### `bid_evidence_manifests`

每次真实文档版本变化后生成一份 append-only manifest：

- 项目 UUID 作为 MCP `case_id`
- 递增 manifest 版本
- 当前及历史文档版本清单
- active 状态
- manifest SHA-256

重复入库相同文件不会生成新 manifest。

### `bid_evidence_read_audits`

保存 Agent 对高风险证据执行上下文读取的审计事件：

- 项目
- assessment
- agent run
- evidence block
- subject
- MCP trace
- 读取时间

证据门不再依赖 MCP 进程内存判断“是否读过上下文”，MCP 进程重启后仍可验证。

## 入库规则

入口：`app/services/tender_evidence_ingestion.py`

核心规则：

1. 只能入库当前项目中 `parser_status=parsed` 的 `BidProjectFile`。
2. 原文件必须具有合法 SHA-256。
3. 同一个来源文件重复调用时幂等返回。
4. 同一个 `document_key + sha256 + parser_version` 不重复生成版本；解析器升级后允许基于同一原文件生成新的可审计证据版本。
5. 同一个来源文件不能被重新绑定为其他 `document_key`。
6. 新版本入库后，旧版本保留但变为 inactive。
7. 证据块、旧 manifest 和旧文档版本不覆盖、不删除。
8. service 只 `flush`，由调用方统一 commit/rollback。

本地维护命令：

```powershell
python AI_Middle_Office/scripts/tender_evidence_ingest.py `
  --project-uuid "<bid_projects.project_uuid>" `
  --file-uuid "<bid_project_files.file_uuid>" `
  --document-key "tender-notice" `
  --document-type "tender_notice"
```

`document-key` 表示跨版本不变的逻辑文档身份。比如“招标公告.pdf”和“招标公告澄清01.pdf”都应使用 `tender-notice`，系统才知道后者是前者的新版本。

## MCP 数据库模式

启动时切换仓库：

```powershell
python AI_Middle_Office/scripts/tender_evidence_mcp_server.py `
  --repository database `
  --transport streamable-http `
  --host 127.0.0.1 `
  --port 8012
```

数据库连接继续读取现有 `DATABASE_URL`。服务令牌中的 `case_id` 必须是 `bid_projects.project_uuid`。

数据库仓库实现：

`mcp_servers/tender_evidence/sqlalchemy_repository.py`

它已经支持：

- 读取 active manifest
- 只检索当前 active 文档
- 读取同一文档版本的相邻块
- 查看全部文档版本
- 校验 manifest、文档版本和内容哈希
- 持久化上下文读取审计
- 按项目 UUID 强制隔离

## 当前检索边界

Phase 2 的数据库 `search` 是有上限的确定性关键词回退：

- 最多读取 2,000 个候选块，配置上限 10,000
- 仅搜索当前项目
- 仅搜索 active、非 failed 文档
- 在 Python 中计算可解释的关键词分数

它适合验证真实数据链路，不适合大规模资料库。当前实现没有声称已经接通向量检索。

后续接入 Milvus/BM25 时，只替换 Repository 的 `search` 实现，不改变 MCP Tool、Agent 或证据门契约。

## 已完成验证

- 相同文件重复入库不新增版本
- 同一逻辑文档 v1 -> v2 后，v1 保留且 inactive
- manifest 版本随真实变更递增
- inactive 旧证据不能通过证据门
- 项目 A 无法读取项目 B 的证据 ID
- 数据库读取审计可跨 MCP 调用持久化
- FastMCP Streamable HTTP + SQLAlchemy 仓库 + Bearer Token 实际联调通过
- migration `0063` 在临时 SQLite 上完成 upgrade、downgrade、再次 upgrade

## 尚未做的事情

现有投标文件上传链路会保存解析文本，但没有为每份 `BidProjectFile` 持久化原始文件的 MinIO 对象引用。因此本阶段的“真实证据”是数据库中的真实解析结果，尚不是“原文件—解析结果—证据块”的完整三层链路。

下一阶段建议：

Phase 3a 已完成上述原文件、异步解析任务和统一 locator。下一步是：

1. 为 active 证据块建立独立招标资料混合检索索引。
2. 再把 LangGraph Worker 改为持久 async MCP session。
