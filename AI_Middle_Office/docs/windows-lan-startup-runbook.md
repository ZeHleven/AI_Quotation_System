# Windows 内网试运行启动 Runbook

更新时间：2026-06-03

## 1. 适用场景

本 Runbook 适用于当前“小范围内网试运行”部署方式：

- Windows 电脑作为临时内网服务器。
- FastAPI 主网关、旧报价工作台和 Vite 管理台运行在 Windows。
- CentOS 虚拟机 `192.168.88.128` 提供 MySQL、Redis、RAG、N8N、MinIO 等依赖。
- 其他电脑通过浏览器访问 Windows 电脑的内网 IP。

这不是正式生产方案。如果 Windows 电脑关机、断网、换网络、IP 变化或服务停止，其他电脑都会打不开。正式上线前仍需迁移到公司内网服务器或云服务器，并配置固定 IP/域名、HTTPS、备份、日志监控和运维机制。

## 2. 启动模式

### 本机开发模式

只允许本机访问，适合开发和单人排查：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

默认监听：

```text
127.0.0.1:9000
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

该模式会让 FastAPI 监听：

```text
0.0.0.0:9000
```

启动成功后，脚本会输出：

```text
Local URL: http://127.0.0.1:9000/
LAN URL:   http://<当前 Windows 内网 IP>:9000/
Access info: ...\logs\current_access_urls.txt
```

其他电脑应使用 `LAN URL` 访问。

### 指定固定绑定 IP

如果 Windows 当前有多个网卡，可以指定其中一个内网 IP：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -HostAddress 192.168.x.x
```

此方式适合多网卡环境，但每次换网络后都要重新确认 IP。

## 3. 当前访问地址在哪里看

启动成功后看终端输出中的 `LAN URL`。

也可以查看自动生成的访问地址文件：

```powershell
Get-Content C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office\logs\current_access_urls.txt
```

如果文件不存在，说明当前还没有通过新版 `start_all.ps1` 完成一次启动。

## 4. 健康检查

启动后先在 Windows 本机执行：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health/ready
```

正常时应看到：

```text
status          : ready
database        : ok
task_queue.ok   : True
```

`start_all.ps1` 默认会在第一次 ready 后再等待一个短稳定窗口并复查，避免服务刚 ready 就退出时被误判为启动成功。临时排查时可以调整：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -ReadyStabilitySeconds 3
```

再执行完整验收脚本：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_startup.ps1 -AppUrl http://127.0.0.1:9000
```

如果要从某个内网 IP 验收：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_startup.ps1 -AppUrl http://<当前 Windows 内网 IP>:9000
```

## 5. Windows 防火墙说明

内网试运行模式需要 Windows 允许其他电脑访问 TCP `9000` 端口。

建议只在“专用网络/公司内网”中放行，不建议对公网开放。是否放行防火墙应由操作者确认后执行，不由启动脚本自动修改。

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

检查规则：

```powershell
Get-NetFirewallRule -DisplayName "AI Middle Office FastAPI 9000"
```

临时禁用规则：

```powershell
Disable-NetFirewallRule -DisplayName "AI Middle Office FastAPI 9000"
```

## 6. 其他电脑打不开时的排查顺序

### 6.1 确认 Windows 本机是否可用

在 Windows 本机打开：

```text
http://127.0.0.1:9000/
```

如果本机打不开，先不要排查其他电脑，直接重启本机服务：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -Restart
```

### 6.2 确认 FastAPI 是否监听

```powershell
Test-NetConnection 127.0.0.1 -Port 9000
```

正常时：

```text
TcpTestSucceeded : True
```

### 6.3 确认 Windows 当前内网 IP

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -ne "127.0.0.1" -and -not $_.IPAddress.StartsWith("169.254.") } |
  Select-Object InterfaceAlias, IPAddress
```

用这里显示的 IP 拼接访问地址：

```text
http://<Windows 当前内网 IP>:9000/
```

### 6.4 确认防火墙

如果本机能打开，其他电脑打不开，优先检查 Windows 防火墙是否允许 TCP `9000`。

### 6.5 确认是否同一局域网

在其他电脑上测试：

```powershell
Test-NetConnection <Windows 当前内网 IP> -Port 9000
```

如果端口不通，通常是：

- 不在同一局域网。
- Windows IP 写错。
- Windows 防火墙未放行。
- FastAPI 没有以内网模式启动。
- Windows 电脑换网后 IP 已变化。

## 7. CentOS 依赖不可达时

从 Windows 执行：

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

如果这些端口不通，说明问题在 CentOS 虚拟机或 Docker 依赖侧，不要继续重启 Windows FastAPI。

## 8. 开机自启和看门狗

手动安装计划任务需要管理员 PowerShell：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_service.ps1 -Lan
```

该命令会注册 `AI_MiddleOffice` 计划任务，并让看门狗以内网试运行模式调用 `start_all.ps1`。

查看任务：

```powershell
Get-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
```

手动触发：

```powershell
Start-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
```

查看看门狗日志：

```powershell
Get-ChildItem C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office\logs\startup_watchdog_*.log |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  Get-Content -Tail 80
```

任务状态显示 `Ready` 不一定是异常。是否真正可用，以 `/health/ready` 和实际浏览器访问为准。

## 9. 日常恢复命令

### 内网模式重启 FastAPI、Celery 和健康检查

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -Restart
```

### 只重启 FastAPI

如果只需要重启 FastAPI，不需要重新等待全部依赖：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restart_backend.ps1 -HostAddress 0.0.0.0 -AppPort 9000
```

### 跳过数据库迁移排查启动

仅用于排查：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -SkipMigrations
```

## 10. 阶段 1 最低通过线

阶段 1 完成后，应至少满足：

- `start_all.ps1` 支持本机模式和内网试运行模式。
- 内网模式启动后输出当前 `LAN URL`。
- 当前访问地址写入 `logs/current_access_urls.txt`。
- `/health/ready` 可以作为统一可用性标准。
- 计划任务可配置为内网试运行模式。
- 文档说明 Windows 防火墙放行方式，但不会自动修改防火墙。
- 文档明确 Windows 个人电脑只是临时内网服务器，不是正式生产环境。
