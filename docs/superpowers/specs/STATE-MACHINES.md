# STATE-MACHINES｜关键实体状态机
> 创建日期：2026-05-15
> 状态：Phase 4a 任务草稿与会议纪要状态机已完成当前环境运行态验收；Phase 4b/4c/6 仍未启动。
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 目标

本文件固定关键实体的状态转换、重复调用语义和禁止转换。后端实现时不得绕过状态机直接改状态字段。

## 通用规则

- 所有状态转换必须写审计事件或领域事件。
- 重复调用如果不会改变业务结果，优先返回 `200 + 当前对象`，方便前端重试。
- 已进入终态后，任何非幂等的状态转换请求均返回 `409 CONFLICT`；重复执行同一终态动作且不改变业务结果时，可返回 `200 + 当前对象`。
- 所有异步任务必须有 `trace_id`，用于串联 API 请求、Celery 任务、AI 调用、RAG reload 和审计日志。
- 所有审计事件必须包含 `ip_address`、`user_agent`、`trace_id`，与 `user_role_events`、`admin_login_verifications`、`business_events` 等审计表字段保持一致。
- Celery beat 或系统自动任务触发的审计事件使用固定系统填充值：`operator_id=NULL`、`ip_address='0.0.0.0'`、`user_agent='system:<task_name>'`、`trace_id='system-<task_name>-<run_id>'`。不得伪造为真实用户 IP / UA。

## quote_jobs / transcription / image_analysis

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> running -> timed_out
queued -> canceled
running -> canceled
```

终态：`succeeded` / `failed` / `timed_out` / `canceled`。

规则：

- `mark_async_job_timeouts` 只处理 `queued` / `running` 且 `timeout_at < now()` 的任务。
- 各 `job_type` 使用独立 `timeout_at`，在任务创建时写入。转写任务：`timeout_minutes = clamp(audio_duration_minutes * 3 + 30, min=60, max=480)`；无法读取音频时长时使用 `TRANSCRIPTION_DEFAULT_TIMEOUT_MINUTES=240`。详见 [ADR-AsyncJob.md](ADR-AsyncJob.md)。
- 终态任务不得重新进入 `queued` / `running`。
- 重试必须创建新 job，原 job 保留终态。
- quote retry 继承原 `client_inquiry_id`，不得创建新的 `ClientInquiry`。
- `image_analysis` 子记录必须跟随父 `QuoteJob` 的 `timed_out` / `canceled` 终态同步，避免父任务已结束但 `quote_image_analyses.status` 仍为 `pending`。
- image QuoteJob 重试不复用 failed / timed_out / canceled 的 `quote_image_analyses` 记录。若原图片文件仍有效，创建新的 `quote_image_analyses` 并关联新 QuoteJob；若文件已失效，返回 409 并要求重新上传。
- 原 `quote_image_analyses.status='done'` 时，可复用 `materials_json` 生成新的需求表草稿，但不得改写原分析记录。

## execution_tasks

```text
pending -> in_progress -> done
pending -> done
pending -> cancelled
in_progress -> cancelled
```

规则：

- `cancelled` 和 `done` 为终态。
- `completed_at` 是 `execution_tasks` 的 nullable 时间戳，进入 `done` 时必须与状态变更同事务写入；`pending` / `in_progress` / `cancelled` 时保持 NULL。
- 逾期不作为持久状态，由 `due_at < now() AND completed_at IS NULL AND status NOT IN ('done','cancelled')` 动态计算。`completed_at IS NULL` 是业务完成时间保护，`status NOT IN` 是终态保护；若二者不一致，视为数据质量问题并告警。
- `staff` / `manager` 只能更新分配给自己的进度字段，不得修改负责人、截止时间和来源字段。
- 进度字段白名单固定为：`status`（仅 `pending -> in_progress`、`in_progress -> done`、`pending -> done`）、`completed_at`（仅进入 `done` 时写入或由后端自动写入）、`notes`。后续如新增 `progress_percent`，必须在本状态机和 API 契约中同时补白名单。
- `pending -> cancelled` 和 `in_progress -> cancelled` 仅允许 `admin` / `system_admin` 执行。`staff` / `manager` 不得取消分配给自己的任务，只能更新进度或备注。
- 已 `done` 再次标记完成返回 `200 + 当前对象`。
- `cancelled` 后再完成返回 `409 CONFLICT`。

## task_drafts

```text
pending_review -> accepted
pending_review -> rejected
```

规则：

- `accepted` 后写入 `execution_tasks` 并记录 `accepted_task_id`。
- accept 写入 `execution_tasks` 的字段映射固定为：`title -> title`、确认后的 `assignee_id -> assignee_id`、确认后的 `due_at -> due_at`、`source='meeting'`、`source_ref_id=meeting_note_id`、`notes` 合并草稿备注和确认备注、`status='pending'`。AI 的 `suggested_assignee` / `suggested_due_at` 只能作为默认值，最终以确认请求体为准。
- 已 `accepted` 的草稿再次确认返回 `200 + 已创建任务`，不得重复创建任务。
- `rejected` 后再次确认返回 `409 CONFLICT`。

当前 Phase 4a 实现已落地：

- `POST /api/v1/meetings/{id}/confirm-tasks` 对 `accepted` 草稿重复确认返回已创建任务，不重复创建。
- `rejected` 草稿再次确认返回 `409`。
- AI 未提取到草稿时，可通过 `/api/v1/meetings/{id}/drafts` 人工补充 `pending_review` 草稿。
- 当前环境 smoke 已验证自动提取草稿确认写入 `execution_tasks`，以及人工补充草稿后作废。

## meeting_notes

```text
draft -> confirmed
draft -> cancelled
confirmed -> revised
revised -> confirmed
```

规则：

- `draft` 阶段允许编辑正文并重新提取任务草稿。
- `draft` 录入错误时不物理删除，走 `draft -> cancelled` 逻辑作废，并写入领域事件。
- `draft -> cancelled` 时，关联 `pending_review` 的 `task_drafts` 自动置为 `rejected`，原因标记为 `meeting_cancelled`；不得创建正式 `execution_tasks`。
- 一旦 `confirm-tasks` 成功写入正式任务，原纪要进入 `confirmed`。
- `confirmed` 后不得直接覆盖原始正文，必须写入 `meeting_note_revisions`。
- revision 后可重新提取补充草稿，但不得自动修改已确认的 `execution_tasks`。
- `revised -> confirmed` 只确认本次 revision 新增或修改出的 `task_drafts`。后端必须基于 `meeting_note_id + normalized_title + assignee_id + due_at` 做重复检测，疑似重复时返回草稿冲突供人工处理，不自动创建重复 `execution_tasks`。
- revision 确认不得覆盖原 `meeting_notes.confirmed_at`；每次更正时间写入 `meeting_note_revisions.created_at`，并在 revision 记录中保存本次确认生成的 task ids。

当前 Phase 4a 实现已落地：

- `draft` 纪要可编辑并重新提取，旧 `pending_review` 草稿自动置为 `rejected`，原因 `meeting_reextracted`。
- `draft -> cancelled` 会将关联 `pending_review` 草稿置为 `rejected`，原因 `meeting_cancelled`。
- `confirmed` 后只能通过 `meeting_note_revisions` 记录更正内容，原始 `content` 不覆盖。
- revision 生成的补充草稿确认后不覆盖原 `confirmed_at`。
- 当前环境 smoke 已验证 `confirmed -> revised -> confirmed` 的补充任务路径，并确认 `/admin/execution` 入口可访问。

## client_inquiries（BIZ-1a 商务台账）

`direction` 区分来源方向：`outbound` 主动开拓（BIZ-1a 商务台账），`inbound` 被动接单（Phase 2 历史记录保留）。

`stage` 表示跟进阶段，由操作人手动更新：

```text
初步接触 -> 需求确认
需求确认 -> 报价中
报价中 -> 跟进议价
跟进议价 -> 成单
跟进议价 -> 丢单
任意阶段 -> 成单
任意阶段 -> 丢单
```

终态：`成单` / `丢单`。

规则：

- `direction` 创建后不可修改；`outbound` 受 `FEATURE_BUSINESS_LEDGER` 控制，`inbound` 受 `FEATURE_CLIENT_INQUIRY` 控制。
- `stage` 由负责人或 `admin` 手动更新，不触发自动状态流转。
- `成单` / `丢单` 为终态；进入终态后不允许切换回其他阶段。终态记录如需修正，由 `admin` 软删除后重新创建。
- `outbound` 记录的 `next_followup_at` 超期后不自动变更 `stage`，由 Celery beat 标记"逾期未跟进"并推送钉钉提醒（BIZ-1b 实现）。
- `inbound` 记录不参与跟进提醒逻辑。
- 所有 `stage` 变更必须写 `client_inquiry_events` 审计表（BIZ-1a Alembic revision 同步新建）；创建和软删除同样写审计。`client_inquiry_events` 字段：`id, inquiry_id, event_type, old_value, new_value, operator_id, operated_at, ip_address, user_agent, trace_id`；可选扩展字段 `before_json, after_json`（记录变更前后完整快照）。与通用规则第 16/17 条一致：系统自动任务触发时 `operator_id=NULL`、`ip_address='0.0.0.0'`、`user_agent='system:<task_name>'`、`trace_id='system-<task_name>-<run_id>'`。
- 软删除字段：`cancelled_at`（时间戳）、`cancelled_by_id`（操作人）、`cancel_reason`（必填文本）；进入软删除态后记录不参与列表查询，但审计事件保留。
- 负责人字段：`outbound` 记录复用已有 `responder_id` 字段表示台账负责人；不新增 `assignee_id`，避免与 `inbound` 场景的"首响应人"语义混淆。
- `outbound` 创建时：`inquiry_time` 自动设为 `created_at`；`first_response_time` 设为 `NULL`（主动开拓无"首响应"概念）。
- 历史 `inbound` 数据迁移默认：现有记录通过 Alembic revision 的 `UPDATE` 语句将 `direction` 默认填充为 `'inbound'`。
- BIZ-1a 当前实现：`client_inquiries` 已存在（Alembic `20260514_0012`），`direction`、`stage`、`next_followup_at`、`cancelled_at`、`cancelled_by_id`、`cancel_reason` 和 `client_inquiry_events` 已通过 Alembic `20260520_0016` 落地。

## cost_items（BIZ-2a 成本数据库）

```text
draft -> active
draft -> archived
active -> archived
```

终态：`archived`（已停用）。

规则：

- `draft` 为新建或批量导入后的默认状态（待核定），不进入报价底价参考。
- `active` 为核定后的正式状态，进入报价底价参考和 RAG 知识联动（BIZ-2d）。
- `archived` 表示已停用，不再进入报价参考；已停用条目不可恢复，如需重新启用须创建新条目。
- `draft -> active`（核定）/ `draft -> archived`（直接停用）/ `active -> archived`（停用正式条目）仅允许 `admin` / `system_admin` 执行。
- `active -> archived` 必须提交 `reason`，不依赖 `business_events`（BIZ-3 表，尚未存在）。
- 所有状态变更和价格修改同步写入 `cost_item_history`，字段包含：`old_price`、`new_price`、三类最终价格及人工/主材/辅材等拆分价的 `old_*` / `new_*` 快照、`old_status`、`new_status`、`change_type`（`price_change` / `status_change`）、`changed_by`、`change_reason`、`changed_at`。状态变更时价格快照可为 `NULL`；价格修改时 `old_status` / `new_status` 可为 `NULL`。
- 已 `active` 再次核定返回 `200 + 当前对象`。
- 已 `archived` 再次停用返回 `200 + 当前对象`。
- 已 `archived` 后核定返回 `409 CONFLICT`。
- BIZ-2a 当前实现：`cost_items` / `cost_item_history` 已通过 Alembic `20260520_0017` 创建，并通过 `20260520_0018` 补充人工费、主材费、辅材费等拆分价字段，接在 BIZ-1a `20260520_0016` 之后；`scripts\biz2a_cost_db_smoke.ps1` 已验证 `draft -> active -> archived`、`active -> archived` 必填原因、`archived -> active` 返回 `409 STATE_CONFLICT`、拆分价导入映射，以及价格变更写入 `cost_item_history`。

## contract_adjustments

```text
draft -> confirmed
draft -> cancelled
confirmed -> cancelled
```

规则：

- `confirm` 是独立动作接口，不通过普通 PATCH 完成。
- `cancel` 必须提交 `reason`，并写入 `business_events`。
- `confirmed` 后金额不得直接修改；如需变更，新增一条反向调整项，并通过 `reverses_adjustment_id` 指向原调整项。
- 同一原调整项在 `draft / confirmed` 状态下最多只能有一条反向调整项，避免重复冲销。
- 已 `confirmed` 再次 confirm 返回 `200 + 当前对象`。
- 已 `cancelled` 后 confirm 返回 `409 CONFLICT`。

## contracts

```text
draft -> signed
draft -> cancelled
signed -> archived
signed -> cancelled
archived -> cancelled
```

规则：

- `draft` 可编辑合同基础信息和附件。
- `signed` 表示合同已签约，进入有效合同金额计算。
- `archived` 表示合同文件或合同记录归档，仍进入有效合同金额计算。
- `cancelled` 不进入经营指标。
- `sign` / `archive` / `cancel` 均必须写入 `business_events`。
- `cancel` 必须提交 `reason`，并写入 `business_events`。
- 已 `signed` 再次签约返回 `200 + 当前对象`。
- 已 `archived` 再次归档返回 `200 + 当前对象`。
- 已 `cancelled` 后再签约或归档返回 `409 CONFLICT`。
- 不允许 `signed` / `archived` 回退到 `draft`；不允许 `archived -> signed` 反向转换。
- 归档错误不通过状态回退修正；应通过补充审计备注或后续专用 unarchive 需求评审处理。当前阶段如归档后确实不应继续有效，只能走 `archived -> cancelled`，并写明原因。
- 如需修正金额，使用 `contract_adjustments`。

## payments

```text
pending -> paid
pending -> cancelled
paid -> cancelled
```

规则：

- `paid` 表示已确认回款，`paid_at` 必须有值。
- `cancel` 必须提交 `reason`，并写入 `business_events`。
- 已 `paid` 的回款不得回退为 `pending`；录入错误时只能 `paid -> cancelled`，再重新创建正确回款记录。
- 已 `cancelled` 的回款不得恢复为 `pending` 或 `paid`。
- 已 `paid` 再次 mark-paid 返回 `200 + 当前对象`。
- 已 `cancelled` 后 mark-paid 返回 `409 CONFLICT`。
- 已 `cancelled` 再次 cancel 返回 `200 + 当前对象`。

## project_costs

```text
active -> cancelled
```

规则：

- `active` 成本进入 `effective_cost`。
- `cancelled` 成本不进入经营指标，且不得恢复为 `active`。
- `cancel` 必须提交 `reason`，并写入 `business_events`。
- 成本金额、类型或项目录入错误时，不直接覆盖原记录；作废原记录后重新创建替代记录，并通过 `replaces_cost_id` 指向被替代的原成本。
- 同一原成本在 `active` 状态下最多只能有一条替代记录，避免重复替代。
- 已 `cancelled` 再次 cancel 返回 `200 + 当前对象`。

## projects archive

归档不是 `projects.status`，而是 `archived_at`。

规则：

- archive 已归档项目返回 `200 + 当前对象`。
- unarchive 未归档项目返回 `200 + 当前对象`。
- archive / unarchive 必须写入 `business_events`。
- 默认列表隐藏已归档项目，经营统计仍计入。

## business_import_batches

```text
preview -> confirmed
preview -> cancelled
confirmed -> rolled_back
confirmed -> rollback_failed
rollback_failed -> rolled_back
```

规则：

- `confirm` 成功后不得再次落库；重复 confirm 返回 `200 + 已确认结果`。
- `rolled_back` 后再次 confirm 返回 `409 CONFLICT`。
- `rollback` 必须按 `batch_id` 撤销本批次落库结果。
- 已 `rolled_back` 再次 rollback 返回 `200 + 当前对象`。
- rollback 失败时状态置为 `rollback_failed`，写入 `rollback_error` 和 `rollback_failed_at`，并告警。
- `rollback_failed` 后再次 rollback 表示重试回滚；成功后进入 `rolled_back`。

## rag_reload

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> running -> timed_out
```

规则：

- 同一时间只允许一个 RAG reload 运行。
- `pending_reload` 不是 `rag_reload` 的持久状态，而是 Redis / 数据库中的布尔标记。
- 并发控制必须使用 Redis 分布式锁和数据库状态双保险。启动前先获取 `rag_reload_lock`，再检查是否已存在 `queued` / `running` reload；任一条件不满足则不得启动新 reload。
- `rag_reload_lock` TTL 必须大于 `RAG_RELOAD_TIMEOUT_MINUTES`，reload 结束或超时处理后释放；锁丢失时仍以数据库状态作为最终保护。
- reload 运行中收到新的 reload 请求，不并发启动，只标记 `pending_reload=true`。
- 当前 reload 完成后必须立即检查 `pending_reload`，若存在则马上启动下一次 reload，不等待下一个 Celery beat 周期。
- `rag_reload` 创建时必须写入独立 `timeout_at`，默认由 `RAG_RELOAD_TIMEOUT_MINUTES` 控制。
- `mark_async_job_timeouts` 同时处理超时的 `rag_reload` 记录；超时后置为 `timed_out`，并保留 `pending_reload` 兜底标记供后续重新触发。
- reload 成功后自动 enqueue `rag_eval`。
- reload / eval 失败不回滚导入，但必须告警并在知识库页面显示“待重新加载”或“评测失败”。
- 知识库页面状态由最新 `rag_reload` / `rag_eval` 记录派生，并通过 `GET /api/v1/admin/knowledge/status` 返回：`reload_status`、`eval_status`、`pending_reload`、`last_reload_error`、`last_eval_error`、`last_successful_reload_at`、`last_successful_eval_at`。

## rag_eval

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> running -> timed_out
```

规则：

- `rag_eval` 由 `rag_reload` 成功后自动 enqueue，也可由 `admin` / `system_admin` 手动触发。
- 当前阶段可复用未来 `async_jobs` 结构；若尚未拆表，则使用与 `rag_reload` 相同的后台任务记录表，不得混入业务 `execution_tasks`。
- `rag_eval` 必须写入独立 `timeout_at`，默认由 `RAG_EVAL_TIMEOUT_MINUTES` 控制。
- `rag_eval` 失败不回滚知识导入和 reload 成果，但知识库页面必须显示 `eval_status='failed'` 或 `timed_out`。
- 成功后写入 `rag_eval_reports`，包含评测集版本、Hit@K、MRR、样本数和 `prompt_version` / embedding 模型版本。
