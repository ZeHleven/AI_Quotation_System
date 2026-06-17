# 试运行版管理员运维手册

更新时间：2026-06-03

## 1. 管理员职责

试运行管理员负责：

- 启动 Windows 内网服务。
- 确认 CentOS 依赖可达。
- 确认访问地址。
- 创建和维护试运行账号。
- 执行备份。
- 记录问题。
- 向参与人员说明风险边界。

管理员不负责临时决定跨部门业务规则。

## 2. 每次启动步骤

进入目录：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
```

启动内网模式：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan
```

如端口已被旧服务占用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -Restart
```

启动成功后记录：

```text
Local URL:
LAN URL:
Queue:
Logs:
```

访问地址文件：

```powershell
Get-Content .\logs\current_access_urls.txt
```

## 3. 健康检查

本机检查：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health/ready
```

完整检查：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_startup.ps1 -AppUrl http://127.0.0.1:9000 -TimeoutSeconds 15
```

最低通过：

```text
status=ready
database=ok
task_queue.ok=True
worker_count>=1
```

## 4. 账号维护

正常入口：

```text
/admin/permissions
```

原则：

- 只有 `system_admin` 创建用户和分配角色。
- 不共用管理员账号。
- 初始密码只用于首次登录。
- 试运行人员使用个人账号。
- 角色变更必须写备注。

账号初始化模板：

```text
reports/trial_readiness/20260603_stage2/account_initialization_template.csv
```

账号变更记录：

```text
reports/trial_readiness/20260603_stage2/account_change_log.csv
```

应急恢复 `system_admin`：

```powershell
$env:RESCUE_ADMIN_PASSWORD="临时强密码"
python create_admin.py --confirm-rescue --username admin
```

救援脚本只在权限页面不可用、且确实无法进入系统时使用。

## 5. 备份

推荐备份：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1
```

已知基线：

```text
20260603_153602：MySQL 业务数据库备份已通过，Milvus 在线 volume 备份仍有 warning。
```

备份验收记录：

```text
reports/trial_readiness/20260603_stage2/backup_acceptance_record.md
```

真实数据录入前后建议各备份一次。

Milvus 冷备只在低峰期执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1 -ColdMilvusSnapshot
```

## 6. 日志位置

Windows 日志目录：

```text
AI_Middle_Office/logs/
```

常看文件：

```text
fastapi_YYYYMMDD.out.log
fastapi_YYYYMMDD.err.log
celery_worker_YYYYMMDD.log
startup_watchdog_YYYYMMDD.log
current_access_urls.txt
```

查看最新看门狗日志：

```powershell
Get-ChildItem .\logs\startup_watchdog_*.log |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  Get-Content -Tail 80
```

## 7. 当天运行记录

每天启动后填写：

```text
reports/trial_readiness/20260603_stage3/trial_daily_operation_log.md
```

发现问题填写：

```text
reports/trial_readiness/20260603_stage3/trial_issue_log.csv
```

## 8. 什么时候暂停试运行

出现以下情况建议暂停：

- `/health/ready` 不稳定。
- MySQL 不可达。
- Celery worker 不可用。
- 备份没有可用基线。
- 多个用户无法登录。
- 报价下发出现无法解释的异常。
- 成本库数据被误改或误导出。
- 参与人员误把试运行当正式生产。

暂停后先记录问题，再排查，不现场临时修改业务规则。

## 9. 管理员当天收尾

每天结束前：

1. 记录当天是否有人使用系统。
2. 记录新增账号和角色变更。
3. 记录发生的问题。
4. 备份真实新增数据。
5. 确认是否需要第二天继续开放。
6. 如系统不再使用，关闭前告知参与人员。
