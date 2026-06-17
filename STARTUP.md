# AI 智能报价中台启动说明（内网试运行版）

更新时间：2026-06-03

适用路径：

```powershell
C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test
```

详细说明见：

```text
AI_Middle_Office/docs/internal-trial-delivery-index.md
AI_Middle_Office/docs/windows-lan-startup-runbook.md
AI_Middle_Office/docs/trial-account-permission-runbook.md
AI_Middle_Office/docs/trial-backup-restore-runbook.md
AI_Middle_Office/docs/trial-stage4-frontend-demo-sandbox-runbook.md
AI_Middle_Office/docs/trial-stage5-rule-template-and-production-readiness-runbook.md
```

## 1. 当前部署边界

当前 Windows 电脑只是临时内网服务器。其他电脑通过浏览器访问这台 Windows 电脑的内网 IP。

这只适合小范围内网试运行，不是正式生产环境。如果 Windows 电脑关机、断网、换网络、IP 变化或服务停止，其他电脑都会打不开。正式上线前需要迁移到公司内网服务器或云服务器，并配置固定 IP/域名、HTTPS、备份、日志监控和基础运维机制。

## 2. 推荐启动方式

### 本机开发模式

只允许本机访问：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

本机访问：

```text
http://127.0.0.1:9000/
```

### 内网试运行模式

允许同一局域网其他电脑访问：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan
```

启动成功后看终端里的：

```text
LAN URL: http://<当前 Windows 内网 IP>:9000/
```

也可以查看：

```powershell
Get-Content C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office\logs\current_access_urls.txt
```

## 3. 健康检查

本机检查：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health/ready
```

完整检查：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_startup.ps1 -AppUrl http://127.0.0.1:9000
```

通过标准：

```text
status        = ready
database      = ok
task_queue.ok = True
```

`start_all.ps1` 会在第一次 ready 后短暂等待并复查，避免刚启动就退出的情况被误判为成功。

## 4. Windows 防火墙

内网试运行模式需要 Windows 允许 TCP `9000` 入站。建议只在公司内网/专用网络放行，不要对公网开放。

管理员 PowerShell 示例：

```powershell
New-NetFirewallRule `
  -DisplayName "AI Middle Office FastAPI 9000" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 9000 `
  -Profile Private
```

启动脚本不会自动修改防火墙。是否放行由操作者确认后执行。

## 5. 常用恢复命令

### 内网模式完整重启

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -Restart
```

### 只重启 FastAPI

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restart_backend.ps1 -HostAddress 0.0.0.0 -AppPort 9000
```

### 查看 CentOS 依赖

```powershell
Test-NetConnection 192.168.88.128 -Port 5455
Test-NetConnection 192.168.88.128 -Port 6380
Test-NetConnection 192.168.88.128 -Port 8001
Test-NetConnection 192.168.88.128 -Port 5678
Test-NetConnection 192.168.88.128 -Port 9002
```

端口含义：

```text
5455 = MySQL
6380 = Redis / Celery broker
8001 = RAG service
5678 = N8N
9002 = MinIO
```

## 6. 开机自启

管理员 PowerShell 安装计划任务：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_service.ps1 -Lan
```

查看任务：

```powershell
Get-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
```

手动触发：

```powershell
Start-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
```

看门狗日志：

```powershell
Get-ChildItem C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office\logs\startup_watchdog_*.log |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  Get-Content -Tail 80
```

## 7. 重要说明

- 不再把 `admin / 123` 作为正常账号口径。
- 账号创建、权限分配和密码维护以 `AI_Middle_Office/docs/trial-account-permission-runbook.md` 为准。
- 备份与恢复以 `AI_Middle_Office/docs/trial-backup-restore-runbook.md` 为准。
- 核心前端体验、演示脚本和沙盒样例以 `AI_Middle_Office/docs/trial-stage4-frontend-demo-sandbox-runbook.md` 为准。
- 规则模板、正式上线清单和试运行转生产差距说明以 `AI_Middle_Office/docs/trial-stage5-rule-template-and-production-readiness-runbook.md` 为准。
- `PUBLIC_ACCESS_ENABLED` 在内网试运行阶段应保持 `false`。
- 内网试运行只验证系统可用性和流程体验，不代表正式生产 SLA。
