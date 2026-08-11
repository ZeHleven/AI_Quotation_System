# API-12 单文件流式上传冻结协议

日期：2026-08-10
状态：v1 合同已冻结，代码实现与本地隔离验证完成；未连接 MinIO、ECS 或旧 CentOS

## 1. 对象命名

临时对象键只由服务端生成：

```text
bid-assessment/uploading/v1/YYYY/MM/DD/{batch_id}/{batch_file_id}
```

- 日期使用 UTC；`batch_id` 与 `batch_file_id` 均来自服务端。
- 文件名、`relative_path`、`client_file_id`、用户 ID 和租户输入不得进入对象键。
- `BID_UPLOAD_OBJECT_PREFIX` 只能配置安全路径段，默认
  `bid-assessment/uploading/v1`。
- 补偿删除必须携带本次请求保存的完整对象键，禁止拼接用户输入、通配删除或批次前缀删除。

## 2. 流式与大小限制

| 项目 | 默认值 | 约束 |
|---|---:|---|
| 有界读取块 | 1 MiB | 可配 64 KiB–8 MiB；读取时累计字节并计算 SHA-256 |
| MinIO multipart part | 10 MiB | 可配 5–64 MiB |
| 单文件上限 | 200 MiB | 超过立即返回 413 `BID_FILE_TOO_LARGE` |
| 单批文件数 | 100 | 写对象前预检，最终事务持批次行锁再检 |
| 单批总字节 | 1 GiB | 写对象前预检，最终事务持批次行锁再检 |
| 处理占位超时 | 1 小时 | 超时后相同幂等请求可接管重试 |

文件先写入有界临时流，而不是整文件载入 Python 内存；在对象写入前完成扩展名、声明
MIME、magic bytes、Office ZIP 结构与加密标志、文本 UTF-8/NUL、实际大小和 SHA-256
检查。可选 `X-Content-SHA256` 必须等于实算值。扩展名配置只能收窄内置识别类型，不能
通过配置放行未实现检查的格式。

## 3. 幂等与事务边界

1. 解析并检查文件后，以 actor、HTTP method、规范化 route、`Idempotency-Key` 和规范化
   请求哈希建立短事务处理占位。
2. 持久化对象前读取当前批次计数与字节数，尽早拒绝已超限请求。
3. MinIO `put_object` 在数据库事务之外执行；对象键预先由服务端生成。
4. 最终事务锁定批次与 Assessment，重新校验所有权、批次状态、过期时间、替换目标、
   文件数和总字节数，然后原子写入 FileObject、BatchFile、批次新版本、幂等响应快照、
   `bid.upload_file.received.v1` Outbox 和 `upload_file.receive` 审计。
5. 相同 `client_file_id + SHA-256 + 规范化元数据` 返回原文件；同一 `client_file_id` 绑定
   不同内容或元数据返回 409 `BID_UPLOAD_CLIENT_FILE_CONFLICT`。同一幂等键改传其他内容
   返回 409 `BID_IDEMPOTENCY_KEY_REUSED`。

全局内容去重只复用 `storage_status=available` 且 `SHA-256 + size_bytes` 相同的 FileObject；
因此重传和并发恢复不会产生第二份权威对象引用。

## 4. 失败补偿与孤儿清理

| 失败点 | 数据库结果 | 对象处理 | 幂等结果 |
|---|---|---|---|
| 基础检查失败 | 不创建文件状态 | 不写对象 | 尚未占用幂等记录；修正请求后可复用或更换 Key |
| MinIO 写入失败 | 不创建文件状态 | MinIO 自身失败 | 标记 retryable failed，相同请求可重试 |
| 最终事务业务冲突 | 整体回滚 | 精确删除本请求对象 | 固化确定性 409/413/422 |
| 最终事务/审计异常 | 整体回滚 | 精确删除本请求对象 | 标记 retryable failed |
| 精确删除失败或写对象后进程崩溃 | 无权威引用 | 留给孤儿清理 | 不扩大删除范围 |

孤儿清理任务为 `bid.cleanup_upload_orphans`。默认只扫描对象前缀下修改时间超过 24 小时
的候选，并在数据库中同时排除被 `bid_file_objects.object_key` 或 BatchFile 临时引用的
对象；只删除确定无引用的完整对象键。扫描/删除失败返回计数并供运维告警，任务本身不
修改批次或文件状态。禁止按“前缀 + 超时”直接删除全部对象。任务已注册但未擅自修改
现有 Celery Beat 计划；启用 API-12 前须在正式发布配置中明确调度周期和失败告警。

## 5. 批次版本硬约束

BatchFile 的创建、`inspecting/ready/rejected/failed` 状态变化、重试结果和草稿移除，都必须：

1. 锁定对应 `bid_upload_batches` 行；
2. 在同一事务将 `bid_upload_batches.row_version` 精确加一；
3. 用新聚合版本写 Outbox，并投影为 `upload_batch.changed`；
4. 返回新的 `X-Batch-ETag` 与 `X-Batch-Resource-Version`。

API-12 当前同步完成基础检查后直接创建 `ready` 文件，并在同一事务把批次版本加一。
相同请求重放或相同 `client_file_id` 的精确重传不改变文件状态，因此不得再次推进批次
版本。后续 API-13 删除和异步安全检查也必须复用本约束；只改 BatchFile 而不推进批次
版本属于实现错误，因为会破坏 API-11 的权威对账和条件读取。

## 6. 上线门禁

- API-12 的最低 Alembic 前提为 `20260810_0087`；0087 只把
  `bid.upload_file.received.v1` 加入 Outbox 数据库事件约束。后续 API-13/API-14 的事件扩展
  不改变本协议的上传语义；当前代码 head 以 Phase 1 运行服务文档为准。
- 目标 ECS 最后一次只读确认仍为 `20260808_0082`，本次未连接、备份或升级 ECS。
- 启用 `FEATURE_BID_ASSESSMENT_V1_RUNTIME` 前，必须重新确认实际 head，完成全量备份、
  SHA-256、影子恢复演练，并以同一版本发布 API 与 Worker。
- 首次真实 MinIO 验收必须在隔离前缀和测试批次内覆盖上传、精确补偿删除、引用保留及
  孤儿清理；不得复用旧 CentOS 数据卷，也不得对正式前缀做试验性清理。
