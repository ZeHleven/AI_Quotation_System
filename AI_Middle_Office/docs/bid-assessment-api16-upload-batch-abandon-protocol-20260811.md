# API-16 未提交上传批次放弃协议

日期：2026-08-11
状态：v1 冻结；代码与本地隔离测试已实现
接口：`POST /api/v1/bid-upload-batches/{batch_id}/abandon`

## 1. 目标与边界

API-16 让用户明确结束一个不再准备提交的上传批次，释放该 Assessment 对应 purpose 的开放
批次槽位。它只终态化未提交批次，不撤销已提交 Manifest，不删除历史 Document、
DocumentVersion 或 Manifest。

HTTP 请求事务不调用 MinIO，也不立即解除 BatchFile 对 FileObject/临时对象的引用。对象引用
与物理对象由后台任务在冻结宽限期后分两阶段清理，避免客户端误操作、事务失败或共享内容引用
导致数据丢失。

## 2. 请求与 reason

请求必须同时提供：

- `Idempotency-Key`：16–128 个可打印 ASCII 字符；
- `If-Match`：API-11 返回的单个强批次 ETag；
- JSON 请求体 `{"reason":"用户重新整理资料"}`。

`reason` 必填，必须是字符串；服务端先去除首尾空白，再校验非空且不超过 500 字符。未知字段
拒绝。规范化后的 reason、batch id 和原始强 ETag 一同进入幂等请求哈希。

同一幂等键与同一规范化请求精确重放原 200，并返回 `Idempotent-Replay: true`；同键不同
reason、批次或 ETag 返回 `BID_IDEMPOTENCY_KEY_REUSED`。批次已放弃后换用新幂等键返回
`BID_UPLOAD_BATCH_ALREADY_ABANDONED`，不能用新请求改写原 reason 或清理时间。

## 3. ETag 与终态门禁

只有未过期的 `draft/uploading/ready` 批次允许放弃。服务在批次行锁内先校验所有权和强 ETag，
再校验状态：

- 旧 ETag：412 `BID_RESOURCE_VERSION_MISMATCH`，返回最新 ETag/版本；
- `committing/committed`：409 `BID_UPLOAD_BATCH_ALREADY_COMMITTED`；
- `abandoned`：409 `BID_UPLOAD_BATCH_ALREADY_ABANDONED`；
- `expired/failed` 或已超过 `expires_at`：409 `BID_UPLOAD_BATCH_NOT_READY`。

成功事务把批次置为 `abandoned`，清空 `open_slot_key`，固化规范化 reason、`abandoned_at`、
`cleanup_after`，并只推进一次批次 `row_version`。`cleanup_after` 固定为放弃时刻加
`max(3600, BID_UPLOAD_ORPHAN_GRACE_SECONDS)`；当前默认宽限期为 86400 秒。

## 4. 原子请求闭环

下列内容位于同一个数据库事务：

1. 批次状态、开放槽、reason、放弃/清理时间和版本；
2. `bid.upload_batch.abandoned.v1` Outbox；
3. `upload_batch.abandon` 审计；
4. HTTP 200 完整 `UploadBatchSnapshot` 幂等结果。

Outbox 投影为 `upload_batch.changed` Public Event，SSE 可观察终态。任一步失败都回滚为原开放
批次，不留下完成幂等记录。成功响应返回 API-11 Location、新强 ETag、
`X-Resource-Version` 和 `Cache-Control: private, no-store`。快照显式公开
`abandon_reason/abandoned_at/cleanup_after/cleanup_completed_at`，供页面恢复与运维对账。

## 5. 延迟、共享引用与物理删除

Celery Beat 每 300 秒触发 `bid.cleanup_abandoned_upload_batches`；运行时功能开关关闭时任务
fail-closed 为空操作。启用后任务按
`status + cleanup_completed_at + cleanup_after` 组合索引扫描到期批次，并对每个批次独立事务：

1. 重新锁定批次并确认仍为到期 abandoned 且未完成清理；
2. 保留 BatchFile 行、文件元数据和历史审计，只解除由服务端上传前缀管理的
   `file_object_id/temporary_object_ref`，发生真实引用变化的文件推进 `row_version`；
3. 对每个 FileObject 重新统计全部 BatchFile、DocumentVersion 和临时对象引用；
4. 仅在总引用为零时删除 FileObject 元数据并记录精确物理对象键；
5. 标记 `cleanup_completed_at`、推进批次 `row_version`、写系统审计并提交数据库；
6. 数据库提交后才按精确键删除物理对象。

同一 FileObject 被其他草稿批次或已提交 DocumentVersion 引用时，首个批次清理只解除自己的
引用，物理对象与 FileObject 必须保留，直到最后一个引用释放。非受管对象键保持引用且不自动
删除。物理删除失败不回滚已经提交的引用清理；对象随后成为无数据库引用的受管孤儿，由既有
引用感知孤儿清理器重试。重复任务看到 `cleanup_completed_at` 后无操作。

## 6. 迁移与启用边界

`20260811_0091` 增加四个时间轴字段、abandoned 一致性/时间顺序约束、到期扫描组合索引，并
把 `bid.upload_batch.abandoned.v1` 加入 Outbox 数据库事件约束。若存在放弃血缘或对应事件，
降级明确失败；离线降级同样拒绝，避免绕过数据保护门禁。

- 功能继续由 `FEATURE_BID_ASSESSMENT_V1_RUNTIME=false` 默认关闭；
- 代码 Alembic head 为 `20260811_0091`；
- 目标 ECS 仍停在只读确认的 `20260808_0082`，本阶段没有连接、备份或升级；
- 本地验证只使用临时 SQLite、假对象存储和假消息投影，不使用 MinIO、Redis、CentOS 或 ECS。
