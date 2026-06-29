# BIZ-2x Phase 4B：企业定额正式激活验收记录

## 执行结论

2026-06-26 已完成“清空旧成本库 + 激活 draft 企业定额版本”的正式提交。

本次激活目标：

- 版本 ID：`3`
- 版本编码：`qs-enterprise-quota-20260626-v1`
- 版本名称：`广东旗胜企业定额 1.0（20260626）`
- 源文件：`广东旗胜-企业定额1.0（20260626）.xls`
- 确认码：`ACTIVATE-QS-ENTERPRISE-QUOTA-20260626-V1`

## 正式提交命令

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_enterprise_quota_phase4_activate.py `
  --version-id 3 `
  --clear-old-cost-db `
  --acknowledge-warnings `
  --confirm-code ACTIVATE-QS-ENTERPRISE-QUOTA-20260626-V1 `
  --reason "Activate Guangdong Qisheng enterprise quota 1.0 and retire legacy cost_items" `
  --commit
```

## 提交结果

正式提交返回：

- `ok=true`
- `dry_run=false`
- 目标版本状态：`active`
- 目标版本 `is_active=true`
- 旧成本条目删除：`cost_items=233`
- 旧成本历史删除：`cost_item_history=1460`
- 旧 active 企业定额版本归档：`0`

## 提交前备份

正式提交前已生成旧成本库 JSON 备份：

```text
AI_Middle_Office/outputs/biz2x_enterprise_quota_phase4_backups/old_cost_db_before_enterprise_quota_v3_20260626T055647Z.json
```

备份信息：

- `cost_items=233`
- `cost_item_history=1460`
- 文件大小：`2345159` bytes
- SHA256：`4C3599A2089C602DED65CF8EA575AEB4E762825A79F68FF9EBEFC354C7EE90A8`

## 提交后核验

提交后只读核验结果：

```json
{
  "version": {
    "id": 3,
    "version_code": "qs-enterprise-quota-20260626-v1",
    "status": "active",
    "is_active": true,
    "activated_at": "2026-06-26T05:56:48"
  },
  "import_batch": {
    "id": 3,
    "status": "activated"
  },
  "enterprise_quota_counts": {
    "sections": 24,
    "items": 474,
    "components": 2353,
    "resources": 1197
  },
  "old_cost_db_counts": {
    "cost_items": 0,
    "cost_item_history": 0
  },
  "preserved_audit_counts": {
    "quote_cost_evidence_with_cost_item_id": 413,
    "cost_rag_sync_runs": 6
  },
  "active_quote_jobs": 0,
  "active_enterprise_versions": 1
}
```

## 保留边界

本次只清空旧成本主库：

- 已清空：`cost_items`
- 已清空：`cost_item_history`

以下历史审计/证据数据已保留：

- `quote_cost_evidence`
- `quote_history`
- `quote_feedback`
- `cost_access_audit_logs`
- `cost_rag_sync_runs`

## 后续建议

Phase 4B 后，旧 `cost_items.active` 已不再可作为报价底价来源。下一步应进入 Phase 4C/5，改造报价链路的成本参考来源，让报价前置参考、预审匹配、兜底填价、漏项检测和证据链读取新的 active 企业定额版本。
