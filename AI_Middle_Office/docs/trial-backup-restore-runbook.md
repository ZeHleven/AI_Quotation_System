# 试运行备份与恢复 Runbook

更新时间：2026-06-03

## 1. 适用范围

本 Runbook 用于小范围内网试运行阶段的数据备份、恢复准备和恢复验收说明。

当前系统的关键数据主要在 CentOS `/opt/rag_service/` 侧，包括 MySQL、MinIO、Milvus/N8N 等；Windows 侧主要运行 FastAPI、前端和启动脚本。

## 2. 备份口径

试运行阶段推荐备份入口：

```powershell
AI_Middle_Office/run_centos_backup.ps1
```

该脚本会通过 SSH 调用 CentOS 侧：

```text
/opt/rag_service/backup_production.sh
```

备份输出目录默认在 CentOS：

```text
/opt/rag_service/backups/<timestamp>
/opt/rag_service/backups/latest
```

## 3. 推荐备份命令

在 Windows PowerShell 中执行：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1
```

如果已经确认 CentOS 上的脚本是最新版本，可跳过上传：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1 -SkipUpload
```

如果要做更一致的 Milvus 冷备快照：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1 -ColdMilvusSnapshot
```

`-ColdMilvusSnapshot` 会短暂停止 Milvus 相关容器，不建议在业务员正在使用系统时执行。

## 4. 备份包含内容

CentOS 备份脚本当前覆盖：

- `/opt/rag_service/.env`，保存为 `rag_service.env`。
- `/opt/rag_service/docker-compose.yml`。
- MySQL dump，保存为 `mysql.sql`。
- N8N workflow 导出或备份文件。
- Milvus 相关 volumes 压缩包。
- quote MinIO volume 压缩包。
- `SHA256SUMS` 校验文件。
- `latest` 软链接指向最新备份。

MySQL 备份默认使用 `--no-tablespaces`，用于避免普通应用账号缺少 MySQL `PROCESS` 权限时出现：

```text
Access denied; you need (at least one of) the PROCESS privilege(s) for this operation
```

如后续确实需要导出 tablespace 信息，应改用具备相应权限的专用备份账号，并由管理员单独确认。

注意：

- `.env` 中可能包含密钥，备份文件不能随意发给无关人员。
- 备份不是正式生产容灾，只是试运行级数据安全保障。
- 当前不建议继续使用旧 `backup_all.ps1` 作为主备份入口。

## 5. 旧备份脚本口径

`backup_all.ps1` 是旧的 Windows 直连备份脚本，当前只保留为应急历史工具。

安全收口后：

- 默认运行会中止。
- 必须显式传入 `-LegacyDirectBackup`。
- 不再内置数据库默认密码。
- 必须传 `-MysqlPassword` 或设置当前进程环境变量 `MIDDLE_OFFICE_MYSQL_PASSWORD`。

应急示例：

```powershell
$env:MIDDLE_OFFICE_MYSQL_PASSWORD="数据库密码"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backup_all.ps1 -LegacyDirectBackup
```

试运行和生产相近场景仍优先使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1
```

## 6. 备份频率建议

小范围试运行建议：

- 试运行开始前：完整备份 1 次。
- 每个试运行日结束后：完整备份 1 次。
- 重要演示前：完整备份 1 次。
- 真实样本导入前后：各备份 1 次。
- 成本库批量导入、批量启用、RAG 同步前：至少备份 1 次。

## 7. 备份验收

每次备份完成后记录：

- 备份时间。
- 执行人。
- 备份目录。
- 是否生成 `mysql.sql`。
- 是否生成 `SHA256SUMS`。
- 是否更新 `latest`。
- 是否有警告。
- 是否需要补救。

记录模板：

```text
reports/trial_readiness/20260603_stage2/backup_acceptance_record.md
```

## 8. 恢复原则

试运行期间恢复数据必须谨慎：

- 不在业务员正在操作时恢复。
- 恢复前先停止相关服务或确认无写入。
- 恢复前先再做一次当前状态备份。
- 恢复操作前通知试运行负责人。
- 恢复后必须跑健康检查和关键页面检查。

当前 Runbook 不直接提供一键恢复命令，因为恢复动作可能覆盖 MySQL、MinIO、Milvus、N8N 数据。正式恢复前应先确认：

- 恢复哪个备份点。
- 恢复哪些组件。
- 是否允许覆盖当前数据。
- 谁审批。
- 谁执行。
- 谁验收。

## 9. 恢复桌面推演

当前阶段建议先做桌面推演，不一定立刻恢复真实环境：

1. 找到最新备份目录。
2. 确认 `mysql.sql` 存在。
3. 确认 `SHA256SUMS` 存在。
4. 确认 quote MinIO、Milvus、N8N 相关文件存在。
5. 写下如果需要恢复，哪些服务要先停。
6. 写下恢复后要检查哪些页面。

恢复后最低检查：

- Windows FastAPI `/health/ready`。
- 登录页 `/login`。
- 报价工作台 `/index.html`。
- 权限页 `/admin/permissions`。
- 成本库 `/admin/cost-db`。
- 项目进度 `/admin/projects`。
- 经营总览 `/admin/dashboard`。

## 10. 试运行备份最低通过线

进入真实试运行前，至少满足：

- 能执行 `run_centos_backup.ps1`。
- 能找到 CentOS 最新备份目录。
- 能说明备份包含哪些内容。
- 能说明 `.env` 备份是敏感文件。
- 有一份备份验收记录。
- 有一份恢复桌面推演记录。
- 旧 `backup_all.ps1` 不再作为默认备份入口。

## 11. 当前已知缺口

- 还没有完整自动化恢复脚本。
- 还没有正式备份保留周期和异地备份策略。
- 还没有生产级日志监控和恢复 SLA。
- 这些属于正式上线前清单，不属于当前小范围内网试运行必须完成项。
