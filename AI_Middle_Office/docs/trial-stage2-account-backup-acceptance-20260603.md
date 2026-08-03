# 阶段 2 账号权限、安全口径与备份恢复收口验收记录

验收日期：2026-06-03

## 1. 验收结论

阶段 2 当前结论：

```text
account_security_wording_passed
admin_rescue_script_hardened
backup_entry_standardized
mysql_backup_baseline_passed
trial_templates_created
ready_for_stage3_with_known_gaps
```

说明：

- 账号正常维护入口已明确为 `/admin/permissions`。
- `admin / 123` 不再作为正常账号口径。
- `create_admin.py` 已收口为应急救援脚本，必须显式确认，不再内置默认密码。
- `backup_all.ps1` 已降级为旧直连备份工具，默认运行会中止，不再内置数据库密码。
- 试运行备份推荐入口明确为 `run_centos_backup.ps1`。
- 已补齐 CentOS `.env` 中 MySQL 备份配置，并通过 `20260603_153602` 备份验证 MySQL dump 不再报错。
- 已形成账号初始化、账号变更、备份验收模板。

## 2. 本阶段产物

账号权限 Runbook：

```text
AI_Middle_Office/docs/trial-account-permission-runbook.md
```

备份恢复 Runbook：

```text
AI_Middle_Office/docs/trial-backup-restore-runbook.md
```

账号初始化模板：

```text
reports/trial_readiness/20260603_stage2/account_initialization_template.csv
```

账号变更记录模板：

```text
reports/trial_readiness/20260603_stage2/account_change_log.csv
```

备份验收记录模板：

```text
reports/trial_readiness/20260603_stage2/backup_acceptance_record.md
```

## 3. 脚本安全收口

### 3.1 `create_admin.py`

新口径：

- 只用于应急恢复 `system_admin`。
- 必须传入 `--confirm-rescue`。
- 不再默认使用 `123`。
- 密码必须显式传入或通过 `RESCUE_ADMIN_PASSWORD` 提供。
- 恢复后账号会被标记为必须改密。

示例：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
$env:RESCUE_ADMIN_PASSWORD="临时强密码"
python create_admin.py --confirm-rescue --username admin
```

### 3.2 `backup_all.ps1`

新口径：

- 不再作为试运行推荐备份入口。
- 默认运行会中止。
- 只保留为应急旧直连备份工具。
- 必须传 `-LegacyDirectBackup`。
- 不再内置 MySQL 默认密码。

试运行推荐：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1
```

## 4. 最低通过线检查

| 检查项 | 状态 | 说明 |
|---|---|---|
| 有明确账号维护入口 | 通过 | `/admin/permissions` |
| 不再宣传 `admin / 123` | 通过 | STARTUP 与 Runbook 已收口 |
| 新建用户走 `system_admin` | 通过 | Runbook 已明确 |
| 新用户强制改密口径 | 通过 | Runbook 已明确 |
| 成本库角色边界说明 | 通过 | Runbook 已整理 |
| 救援脚本不再默认弱密码 | 通过 | `create_admin.py` 已修改 |
| 推荐备份入口明确 | 通过 | `run_centos_backup.ps1` |
| MySQL 业务数据库备份基线 | 通过 | `20260603_153602` 无 MySQL 报错 |
| 旧备份脚本降级 | 通过 | `backup_all.ps1` 默认中止 |
| 备份验收模板 | 通过 | 已新增 |
| 恢复桌面推演口径 | 通过 | Runbook 已新增 |

## 5. 当前已知缺口

以下缺口不阻断进入阶段 3，但应进入后续账号维护补强清单：

- 管理员日常重置普通用户密码的正式 UI/API 仍待补强。
- 管理员日常停用账号的正式 UI/API 仍待补强。
- 完整自动化恢复脚本仍待正式上线前设计。
- Milvus 在线 volume 备份仍有文件变化 warning，真实试运行前建议低峰期补一次冷备。
- 备份保留周期、异地备份和生产 SLA 不属于当前个人电脑内网试运行范围。

## 6. 下一步建议

可以进入阶段 3：试运行启动当天检查与交付文档收口。

阶段 3 应把阶段 1 和阶段 2 的结果合并成一张启动当天检查表：

- 当前访问地址。
- `/health/ready`。
- Celery worker。
- CentOS 依赖。
- 最近一次备份。
- 试运行账号准备状态。
- 风险边界确认。
