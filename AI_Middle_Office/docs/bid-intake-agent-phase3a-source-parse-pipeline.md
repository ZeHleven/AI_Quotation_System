# 报价资料研判 Agent Phase 3a：原文件与可靠解析链路

## 本阶段结论

Phase 3a 已补齐：

```text
上传原始招标文件
  -> MinIO 不可变对象
  -> 可重试解析任务
  -> BidProjectFile 解析结果
  -> 版本化证据文档、证据块与 manifest
  -> Tender Evidence MCP
```

现有 `/admin/bidding` 同步上传接口保持不变。本阶段新增独立接口进行小范围验证，避免改变当前投标业务链。

## 为什么需要这一层

Phase 2 只有“解析结果 -> 证据”，原始文件没有形成稳定引用。如果解析器出错、worker 中断或以后升级解析器，无法可靠地重新处理同一份原件。

现在原文件、解析任务和证据版本分别持久化：

- 原件是事实源，可校验 SHA-256。
- 任务状态可查询、可重试、可审计。
- 解析结果和证据版本可以重建，但不会静默覆盖历史版本。
- Agent 引用的证据可以继续追溯到具体项目、文件版本和位置。

## 新增数据结构

Alembic revision：`20260727_0064`

### `bid_tender_source_objects`

保存项目与 `file_objects` 原始对象之间的不可变关联：

- 项目
- 稳定 `document_key`
- 文件类型和原始文件名
- MinIO `FileObject` 引用
- 文件大小与 SHA-256
- 当前状态

`project_id + document_key + sha256` 唯一。相同项目、逻辑文档和内容重复上传时，不会再写一个 MinIO 对象。

### `bid_tender_parse_jobs`

保存一份原文件在某个解析器版本下的执行状态：

- `queued`
- `running`
- `retryable`
- `completed`
- `failed`
- `cancelled`（预留）

并记录当前阶段、尝试次数、错误码、解析结果、证据文档 UUID 和 Celery task ID。

`source_object_id + parser_version` 唯一。解析器升级后复用原始对象，新建解析任务，并允许生成新的证据版本。

### `bid_tender_parse_job_events`

追加式记录：

- 任务创建
- 尝试开始
- 原件校验完成
- 证据入库完成
- 调度或执行失败
- 人工重新入队

事件不覆盖，便于后续排障和审计。

## 可靠性规则

1. 原始字节先写 MinIO，再在一个数据库事务中创建 `FileObject`、source 和 parse job。
2. 如果数据库写入失败，只删除本次刚创建的精确 MinIO 对象，避免孤儿对象。
3. worker 读取原件后重新计算 SHA-256；不一致时永久失败，不继续向 Agent 提供证据。
4. 存储暂时不可用属于可恢复失败，最多尝试 3 次。
5. 文件格式不支持、内容无法解析、证据无法满足入库合同属于永久失败。
6. worker 解析器版本必须与任务记录一致，避免部署切换后产生无法解释的证据。
7. `BidProjectFile.file_uuid` 由 source UUID 和 parser version 确定，重复执行不会重复创建解析结果。
8. 证据入库仍使用 Phase 2 的版本、manifest 和幂等规则。
9. 非管理角色只能查看自己创建或负责的投标项目；越权查询返回 404，避免暴露项目存在性。

## 统一证据位置

`app/services/tender_evidence_locator.py` 把解析器产生的不同位置字段归一为：

- PDF：`page`
- Excel：`sheet + cell_range`
- Word/文本：`section`
- 无明确位置：`block`

原字段仍保留在 `source_location`，归一结果写入 `bid_evidence_blocks.locator_json`。后续 OCR 或新解析器只需提供相同 locator 合同，不需要修改 MCP Tool。

## API

在 `FEATURE_BIDDING_MVP=true` 时：

```text
POST /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs
GET  /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs
GET  /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs/{job_uuid}
POST /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs/{job_uuid}/retry
```

POST 使用 multipart：

- `file`
- `file_type`
- `document_key`（建议显式提供跨版本稳定值）

任务执行复用现有 `TASK_QUEUE_MODE`：

- `disabled`
- `inline`
- `local`
- `celery`

Celery worker 新增任务：`tender_evidence.run_parse_job`。

## 已完成验证

- 上传 API 能保存原件并创建任务。
- 正常任务完成解析和证据入库。
- 相同内容重复创建时不重复写对象或任务。
- 解析器升级时复用原始对象并产生新证据版本。
- 存储暂时不可用时进入 `retryable`，达到上限后进入 `failed`。
- SHA-256 不一致时永久失败。
- 数据库提交失败时执行精确对象补偿删除。
- 普通用户无法读取其他用户的项目任务。
- `0063 -> 0064 -> 0063 -> 0064` 迁移验证通过。
- 既有 BIZ-4a 招标模块回归通过。

## 当前边界

- 没有迁移当前真实数据库，也没有自动改造旧同步上传入口。
- 没有删除原始对象的业务接口。
- 没有接 OCR。
- 没有建立 Milvus/BM25 混合检索索引。
- 没有在本阶段启动研判 Agent 的生产 worker。

Phase 3b 已完成代码层开发：active evidence blocks 已具备项目隔离的混合检索索引、可靠同步任务和数据库关键词回退。
