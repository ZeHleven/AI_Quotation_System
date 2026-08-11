# API-15 上传批次提交协议

日期：2026-08-11
状态：v1 冻结；代码与本地隔离测试已实现
接口：`POST /api/v1/bid-upload-batches/{batch_id}/commit`

## 1. 目标与边界

API-15 把一个已完成上传的草稿批次原子固化为新的不可变资料版本。它负责：

- 合并 `add`、`replace`、`deactivate`；
- 登记不可变 DocumentVersion 和 Manifest；
- 切换 Assessment 的当前 Manifest；
- 使基于被替换 Manifest 的旧 Run 失效；
- 写入后续解析所需的 Outbox、幂等结果和审计。

API-15 不读取、复制、移动或删除 MinIO 对象，不创建 Scope、Run、Plan 或 Task，也不伪造
规划已具备前置条件。API-12 已登记且状态为 `available` 的 FileObject 是本接口唯一文件输入。

## 2. 请求并发合同

请求必须同时提供：

- `Idempotency-Key`：16–128 个可打印 ASCII 字符；
- `If-Match`：API-11 返回的单个强批次 ETag；
- `expected_file_count`：当前 `ready` BatchFile 数，可为 0；
- `expected_deactivation_count`：当前停用操作数，可为 0；
- `change_note`：显式字符串或 `null`，字符串去除首尾空白，空白归一为 `null`；
- `confirm_start_analysis=true`。

ETag 防止批次版本丢失更新，两个显式计数防止页面遗漏并发完成的文件或停用操作。请求体和
ETag 都进入幂等请求哈希。同一幂等键、同一请求精确重放原 202；同键异请求返回
`BID_IDEMPOTENCY_KEY_REUSED`。批次提交后使用其他幂等键返回
`BID_UPLOAD_BATCH_ALREADY_COMMITTED`。

## 3. 冻结合并算法

### 3.1 initial

initial 批次只允许 `add`，且 Assessment 当前不能已有 Manifest。每个有效 BatchFile：

1. 创建新的逻辑 BidDocument；
2. 复用其已有 FileObject；
3. 创建 `version_no=1` 的不可变 BidDocumentVersion；
4. 按 BatchFile 的稳定顺序追加为 Manifest 成员。

### 3.2 change

change 批次的 `base_manifest_id` 必须仍等于 Assessment 的 `current_manifest_id`。服务从基础
Manifest 的有序成员开始逐项合并：

- 未变化：原 DocumentVersion、role、order 原样携带；
- replace：在同一逻辑 Document 下创建递增 DocumentVersion，保留原 role 和 order；
- deactivate：从新 Manifest 排除整个逻辑 Document；
- add：创建新 Document 与首版 DocumentVersion，从基础 Manifest 最大 order 后按稳定批次
  文件顺序追加。

下列情况整单冲突且不产生任何写入：目标不属于基础 Manifest、同一 Document 多次 replace、
同一 Document 同时 replace/deactivate、replace 内容与基础 Manifest 当前版本内容相同。

合法的仅停用 change 批次可以提交。若最终成员为空，仍创建 `document_count=0` 的权威空
Manifest，并把 Assessment 置为 `awaiting_files`；非空 Manifest 进入 `preparing`。

## 4. 不可变 Manifest 与数据库血缘

Manifest `version` 在 Assessment 行锁内按 `max(version)+1` 分配。`manifest_hash` 对以下内容
做规范化 JSON SHA-256：

- `assessment_id`；
- 最终有序成员的 `document_id`；
- `document_version_id`；
- `file_object_id`；
- `role`；
- `order_no`。

旧 Manifest、ManifestDocument、DocumentVersion 和 FileObject 永不修改。Batch 固化
`committed_manifest_id` 与 `committed_at`，Manifest 固化规范化后的 `change_note`。
`20260811_0090` 使用同 Assessment 复合外键、Manifest 唯一提交批次约束和 Check Constraint
保证：只有两个提交血缘字段同时存在时状态才能为 `committed`，非 committed 状态不得持有
这两个字段。

## 5. Assessment 指针与旧 Run

同一事务中：

1. `current_manifest_id` 切换到新 Manifest；
2. `active_run_id` 清空；
3. Assessment `row_version + 1`；
4. Batch 置为 `committed`、清空开放槽并 `row_version + 1`。

仅检查冻结输入等于被替换当前 Manifest 的 Run：`created/planning/queued/running/`
`waiting_input/waiting_operation/validating` 以及 `retryable=true` 的 failed Run 直接变为终态
`stale`，关闭 retryable、记录 `input_manifest_superseded`、完成时间并推进各自版本。已经
`succeeded/cancelled/stale` 或不可重试 failed 的历史 Run 不改写。

## 6. 原子事件顺序

业务写入、事件、审计和完成幂等记录位于同一个 MySQL 事务。Outbox 事件使用严格单调的
`occurred_at`，并以前一事件 `event_id` 形成 `causation_event_id` 链：

```text
document.version_registered *
  -> manifest.committed
  -> assessment.input_stale ?
  -> document.parse_requested *
```

`manifest.committed` 是工作流门闩，并投影为新的权威 `assessment.snapshot`。API-15 不写
`bid.plan.requested.v1`：此时尚无 API-15 创建的 Run，也尚无解析结果和有效 Scope。真正的
规划事件必须由后续“所有必需解析完成 + Scope 就绪”消费者在自己的业务事务中写入，且
通过消费者去重表保证重复投递无副作用。

该顺序是持久化因果顺序；消息系统仍按至少一次交付，消费者不能以到达先后代替
`causation_event_id`、业务前置条件和事务去重校验。

## 7. HTTP 结果与恢复

成功返回 202，包含：

- Manifest id/version/hash/document_count/committed_at；
- accepted operation 及 API-03 状态地址；
- `run=null`；
- 完整 Assessment Snapshot；
- committed Batch id/status/row_version/ETag。

响应 `Location` 和主 `ETag` 指向 API-03 权威 Assessment；`X-Batch-ETag` 与
`X-Batch-Resource-Version` 用于批次对账。`Cache-Control` 固定为 `private, no-store`。

主要恢复码：缺少 If-Match 为 428，弱/列表/通配或格式错误为 400，旧 ETag 为 412；文件数、
停用数、陈旧基线、合并冲突或批次未就绪为 409。任何事务步骤失败都回滚到原 ready 批次，
不留下 Document、Version、Manifest、stale Run、Outbox、审计或完成幂等记录，也不触碰对象
存储。

## 8. 启用与验证边界

- 功能继续由 `FEATURE_BID_ASSESSMENT_V1_RUNTIME=false` 默认关闭；
- 代码 Alembic 前提为 `20260811_0090`；
- 目标 ECS 仍停在只读确认的 `20260808_0082`，本阶段没有连接或升级；
- 本地测试只使用临时 SQLite 和假对象存储/发布器，不使用本机 CentOS、MinIO、Redis 或 ECS。
