# API-14 基线文档停用冻结协议

日期：2026-08-11
状态：v1 合同已冻结，代码实现与本地隔离验证完成；未连接 MinIO、ECS 或旧 CentOS

## 1. 请求与批次版本

- 路径固定为 `POST /api/v1/bid-upload-batches/{batch_id}/deactivations`。
- 必须提供 `Idempotency-Key` 和 API-11 返回的批次强 `If-Match`。
- 请求固定为 `document_ids` 数组和一个共享 `reason`；数组包含 1—100 个不重复文档 ID。
- 服务端排序 `document_ids`、去除 `reason` 首尾空白后计算幂等请求哈希，因此相同集合不同
  顺序可使用同一幂等键精确重放。
- `If-Match` 缺失返回 428；弱标签、通配符、列表或格式错误返回 400；版本不一致返回 412，
  并携带当前批次 ETag、版本及 API-11 恢复地址。

成功 HTTP 201，返回完整 `UploadBatchSnapshot`、批次 Location、强 ETag、
`X-Resource-Version` 和 `Cache-Control: private, no-store`。相同幂等键重放原始 201、响应体
和 ETag，并增加 `Idempotent-Replay: true`。

## 2. 基线 Manifest 成员验证

API-14 只允许未过期的 `purpose=change` 且处于 `draft/uploading/ready` 的批次。批次
`base_manifest_id` 必须仍等于 Assessment 的当前 Manifest。

每个停用目标必须通过以下不可变关系证明属于该基线：

```text
base Manifest
  -> BidManifestDocument
  -> BidDocumentVersion
  -> BidDocument
```

文档只是在企业文档库存在、属于同一 Assessment 的其他历史 Manifest，或者由其他 Assessment
引用，都不构成合法停用目标。任一目标不属于基线时，整个请求返回 409
`BID_UPLOAD_DEACTIVATION_TARGET_INVALID`，不登记部分结果。批次基线已经过期时返回 409
`BID_BASE_MANIFEST_STALE`。

## 3. 重复停用

- 相同文档、相同规范化原因已经登记：视为业务无操作，不新增停用行。
- 请求同时包含重复项和新目标：忽略同原因重复项，原子新增剩余目标。
- 任一文档已使用不同原因登记：整个请求返回 409
  `BID_UPLOAD_DEACTIVATION_CONFLICT`，不新增其他目标。
- 全部目标均为同原因重复项时仍返回当前 201 快照，并写一条 `operation_noop=true` 审计；
  批次版本和 Outbox 数量不变。

同一批次、同一文档由数据库唯一约束提供最终并发保护。客户端在响应丢失后必须使用原
`Idempotency-Key` 重试，不能用新键绕过原请求结果。

## 4. 状态、事务与事件

每次停用集合真实变化在一个批次行锁事务中：

1. 锁定 Batch 与 Assessment，验证所有权、状态、有效期、批次 ETag 和当前基线；
2. 验证全部目标均属于 `base_manifest_id`；
3. 原子写入尚未存在的 `BidUploadBatchDeactivation`；
4. 按文件状态与停用数量重算 `draft/uploading/ready`，批次 `row_version` 只增加一次；
5. 固化 HTTP 201 幂等响应、`bid.upload_batch.deactivation_added.v1` Outbox 和
   `upload_batch.add_deactivations` 审计。

事件使用新的批次聚合版本，投影为既有 `upload_batch.changed`。只有停用、没有上传新文件的
change 批次是合法 `ready` 变更；API-11 的 `validation.can_commit` 为 true。若仍有接收、检查、
拒绝或失败文件，则停用操作不能掩盖文件阻断状态。

## 5. 历史数据和物理对象保护

停用表示“API-15 生成下一 Manifest 时不再包含这些逻辑文档”，不表示删除文档。API-14：

- 不更新或删除 BidDocument、BidDocumentVersion、BidManifestDocument 或历史 Manifest；
- 不更新或删除 FileObject；
- 不删除历史 Evidence、Report 或 Run 输入；
- 不读取、不写入、不删除 MinIO 对象；
- 不触发 API-12/API-13 的对象补偿或孤儿清理路径。

因此旧 Manifest、旧报告和证据链继续可复现，停用只在下一次提交批次时生效。

## 6. 上线门禁

- API-14 的最低 Alembic 前提为 `20260811_0089`；0089 只把
  `bid.upload_batch.deactivation_added.v1` 加入 Outbox 数据库事件约束。
- 目标 ECS 最后一次只读确认仍为 `20260808_0082`；本次未连接、备份或升级 ECS。
- 正式启用前必须重新确认实际 head，完成全量备份、SHA-256、恢复演练，并同步发布 API、
  Worker 与事件消费者；应用自动迁移继续关闭。
- `FEATURE_BID_ASSESSMENT_V1_RUNTIME=false` 继续默认关闭。
