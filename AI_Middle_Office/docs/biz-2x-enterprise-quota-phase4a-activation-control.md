# BIZ-2x Phase 4A：企业定额受控激活工具链

## 目标

Phase 4A 只建设“清空旧成本库 + 激活 draft 企业定额版本”的受控执行能力，不直接完成生产激活。

本阶段交付：

- 激活计划：列出目标版本、旧成本库数量、运行中报价任务、阻断项、警告项和确认码。
- dry-run：真实执行删除/激活动作并 `flush`，随后回滚事务，用于验证约束。
- 备份：在清空旧成本库前导出 `cost_items` 与 `cost_item_history`。
- 受控脚本：只有提供 `--commit`、正确确认码、警告确认和备份目录时才允许提交。

## 清空范围

会清空：

- `cost_item_history`
- `cost_items`

会保留：

- `quote_cost_evidence`
- `quote_history`
- `quote_feedback`
- `cost_access_audit_logs`
- `cost_rag_sync_runs`

保留原因：历史报价证据已经存有成本条目快照，`quote_cost_evidence.cost_item_id` 不是外键绑定旧 `cost_items`，因此旧主库清空不会破坏历史报价审计链。

## 安全门

提交激活前必须全部通过：

- 目标版本存在。
- 目标版本状态为 `draft`，且 `is_active=false`。
- Phase 0 `error_count=0`。
- 目标版本至少包含主项、组成明细和资源价格。
- 不存在 `queued` 或 `running` 报价任务。
- 如存在 Phase 0 warnings、旧成本库数据、历史证据引用或旧 RAG 同步记录，提交时必须加 `--acknowledge-warnings`。
- 如清空旧成本库并提交，必须先生成备份。
- 提交必须提供脚本输出的确认码。

## 查看激活计划

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_enterprise_quota_phase4_activate.py `
  --version-id 3 `
  --clear-old-cost-db `
  --plan
```

计划输出中的关键字段：

- `plan.ok`：是否无阻断项。
- `plan.confirmation_code`：正式提交时必须原样传入。
- `plan.blockers`：阻断项，不允许提交。
- `plan.warnings`：非阻断风险，提交时需显式确认。
- `plan.old_cost_db.clear_tables`：本次会清空的旧表。
- `plan.old_cost_db.preserve_tables`：本次保留的审计/证据表。

当前 Phase 3 draft 版本的确认码预期为：

```text
ACTIVATE-QS-ENTERPRISE-QUOTA-20260626-V1
```

以脚本实际输出为准。

## 执行 dry-run

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_enterprise_quota_phase4_activate.py `
  --version-id 3 `
  --clear-old-cost-db `
  --acknowledge-warnings `
  --reason "Phase 4A dry-run before controlled activation"
```

dry-run 行为：

- 会生成旧成本库 JSON 备份。
- 会在事务内执行删除旧成本库、激活目标版本、更新导入批次。
- 最后自动 `rollback`，数据库不会被改变。
- 备份文件会保留，便于人工核验。

默认备份目录：

```text
AI_Middle_Office/outputs/biz2x_enterprise_quota_phase4_backups/
```

## 正式提交命令模板

正式激活属于 Phase 4B/4 执行动作。只有在 dry-run 输出无阻断、备份核验通过、业务确认停止报价任务后，才执行：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_enterprise_quota_phase4_activate.py `
  --version-id 3 `
  --clear-old-cost-db `
  --acknowledge-warnings `
  --confirm-code ACTIVATE-QS-ENTERPRISE-QUOTA-20260626-V1 `
  --reason "Activate Guangdong Qisheng enterprise quota 1.0 and retire legacy cost_items" `
  --commit
```

提交成功后预期状态：

- `cost_items = 0`
- `cost_item_history = 0`
- 目标 `enterprise_quota_versions.id=3` 为 `status=active, is_active=true`
- 对应 `cost_import_batches.status=activated`
- 旧报价证据和历史报价仍保留

## 停止条件

出现以下任一情况，不进入正式提交：

- `plan.blockers` 非空。
- 备份文件无法打开、数量不一致或 SHA256 为空。
- 仍有 `queued/running` 报价任务。
- 目标版本不是期望的 draft 版本。
- 业务方未确认旧成本库可清空。
