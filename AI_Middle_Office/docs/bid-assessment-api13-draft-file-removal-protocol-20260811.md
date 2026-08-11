# API-13 草稿文件移除冻结协议

日期：2026-08-11
状态：v1 合同已冻结，代码实现与本地隔离验证完成；未连接 MinIO、ECS 或旧 CentOS

## 1. 文件 ETag

- API-13 路径固定为
  `DELETE /api/v1/bid-upload-batches/{batch_id}/files/{file_id}`。
- 必须同时提供 `Idempotency-Key` 和 BatchFile 的 `If-Match`。
- 文件 ETag 固定为 `"bid-upload-file:{file_id}:{row_version}"`，只表达 BatchFile 资源版本，
  不混入批次版本、上传限制或对象存储 ETag。
- API-12 首次接收通过响应 `ETag` 返回文件版本；API-11 的每个文件条目通过
  `row_version/etag` 返回页面恢复后的权威版本。
- `If-Match` 只接受单个强 ETag。缺失为 428，弱标签、`*`、列表和格式错误为 400，
  与当前文件版本不一致为 412，并返回当前文件 ETag/版本及 API-11 恢复地址。

成功 204 不返回已删除文件的 ETag，而返回新的 `X-Batch-ETag` 和
`X-Batch-Resource-Version`。相同幂等请求重放原 204 与原批次版本，并增加
`Idempotent-Replay: true`；重放不得再次修改批次。

## 2. 数据库事务

API-13 在一个事务中：

1. 锁定 Batch、Assessment、BatchFile 和关联 FileObject；
2. 校验所有者/管理员、批次未提交、未过期和文件 ETag；
3. 仅删除本批次的 BatchFile 引用；
4. 按剩余文件重算批次 `draft/ready/uploading` 状态，并将批次 `row_version` 精确加一；
5. 固化内部 204 删除回执、`bid.upload_file.removed.v1` Outbox 和
   `upload_file.remove_draft` 审计。

事件使用新的批次聚合版本，并投影为 `upload_batch.changed`。审计、Outbox、幂等或提交
任一环节失败时整体回滚，BatchFile、FileObject、批次版本和对象存储均保持原状。

## 3. 共享引用

BatchFile 是批次引用，不拥有共享内容。删除 BatchFile 后，必须重新检查：

- 其他 `bid_upload_batch_files.file_object_id` 引用；
- 其他 BatchFile 的 `temporary_object_ref`；
- `bid_document_versions.file_object_id` 历史文档引用。

任一引用存在时，保留 FileObject 元数据和物理对象。只有以上引用全部为零且对象键位于
`bid-assessment/uploading/v1/` 受管前缀内，才在事务中删除 FileObject 元数据并产生一个
精确物理删除候选。非受管前缀采用保守保留策略，API-13 不删除。

因此，同内容去重形成的多个草稿文件可以分别移除；最后一个草稿引用消失前不会删除对象。
即使草稿 BatchFile 与已提交 DocumentVersion 共享 FileObject，移除草稿也不会影响历史
DocumentVersion、Manifest 或下载能力。

## 4. 物理删除时机

MinIO `remove_object` 只能发生在数据库事务确认提交之后，且目标只能是事务返回的完整对象
键。禁止在提交前删除、按批次前缀删除、从文件名拼接删除目标或在回滚路径执行删除。

提交后的精确删除失败不会把已完成的逻辑移除改成 5xx，也不会恢复 BatchFile。此时数据库
已没有权威引用，受管对象由 `bid.cleanup_upload_orphans` 在宽限期后再次确认零引用并收敛。
相同幂等请求重放不再次触发物理删除，避免把重放变成额外副作用。

## 5. 状态与错误

| 条件 | 结果 |
|---|---|
| `draft/uploading/ready` 且版本匹配 | 204，批次版本加一 |
| `committing/committed` | 409 `BID_UPLOAD_BATCH_ALREADY_COMMITTED` |
| `abandoned/expired/failed` 或已过期 | 409 `BID_UPLOAD_BATCH_NOT_READY` |
| 文件/批次不存在或不可见 | 404 `BID_RESOURCE_NOT_FOUND` |
| 文件 ETag 过期 | 412 `BID_RESOURCE_VERSION_MISMATCH` |
| 相同 Key、不同文件或 ETag | 409 `BID_IDEMPOTENCY_KEY_REUSED` |

## 6. 上线门禁

- API-13 的最低 Alembic 前提为 `20260811_0088`；0088 只把
  `bid.upload_file.removed.v1` 加入 Outbox 数据库事件约束。后续 API-14 已把当前代码 head
  推进为 `20260811_0089`，不改变本协议的文件移除语义。
- 目标 ECS 最后一次只读确认仍为 `20260808_0082`，本次未连接、备份或升级 ECS。
- 正式启用前必须重新确认 head，完成全量备份、SHA-256、影子恢复演练，并同步发布 API、
  Worker 和孤儿清理调度。
- 首次真实 MinIO 验收只允许使用隔离测试批次和受管测试前缀，覆盖共享引用保留、最后引用
  精确删除和删除失败后的孤儿收敛；不得复用旧 CentOS 数据卷。
