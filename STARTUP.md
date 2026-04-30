# AI 智能报价中台启动说明

适用路径：

```powershell
C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test
```

系统访问地址：

```text
http://localhost:9000/
```

管理员账号：

```text
admin / 123
```

---

## 一、正常重启电脑后怎么启动

现在系统已经做了第 20 步“一键启动与自愈编排”，正常情况下重启电脑后不需要手动启动很多东西。

推荐顺序：

1. 先打开 CentOS 虚拟机。
2. 等 CentOS 进入系统后，`ens33` 会自动获取 `192.168.88.128`。
3. Docker 会自动恢复 RAG、MySQL、Redis、MinIO 等服务。
4. Windows 的任务计划程序 `AI_MiddleOffice` 会自动执行启动看门狗。
5. 启动看门狗会每 3 分钟尝试一次启动编排，最多持续 60 分钟。
6. 直接打开：

```text
http://localhost:9000/
```

---

## 二、如果网页打不开，手动一键启动

在 Windows PowerShell 中执行：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

看到下面类似输出，就表示系统已就绪：

```text
System is ready
URL: http://localhost:9000/
Queue: celery / ok=True
```

说明：`start_all.ps1` 会在启动 FastAPI 前自动执行数据库迁移：

```text
python -c "from alembic.config import main; main()" -c alembic.ini upgrade head
```

如果只是临时排查启动问题，可以跳过迁移：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -SkipMigrations
```

---

## 三、健康检查

启动后检查后端、数据库、队列是否正常：

```powershell
Invoke-RestMethod http://localhost:9000/health/ready
```

正常结果应包含：

```text
status          : ready
database        : ok
task_queue_mode : celery
task_queue.ok   : True
```

也可以运行固定验收脚本，一次性检查 FastAPI、worker、RAG、n8n、MinIO、MySQL、Redis：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_startup.ps1
```

正常结果：

```text
[PASS] Acceptance check passed
```

---

## 四、检查 CentOS 服务是否可达

如果系统打不开，先在 Windows PowerShell 检查这些端口：

```powershell
Test-NetConnection 192.168.88.128 -Port 5455
Test-NetConnection 192.168.88.128 -Port 6380
Test-NetConnection 192.168.88.128 -Port 8001
Test-NetConnection 192.168.88.128 -Port 5678
Test-NetConnection 192.168.88.128 -Port 9002
```

含义：

```text
5455  = MySQL
6380  = Redis / Celery broker
8001  = RAG 服务
5678  = n8n
9002  = MinIO 文件存储
```

如果 `TcpTestSucceeded : True`，说明该服务端口可达。

---

## 五、如果 CentOS 没有拿到 IP

正常情况下已经配置了自动 DHCP，不需要再手动执行。

如果 `192.168.88.128` 不通，可以进入 CentOS 临时修复：

```bash
sudo dhclient ens33
```

然后在 Windows 再检查：

```powershell
Test-NetConnection 192.168.88.128 -Port 5455
```

---

## 六、如果 Docker 服务没起来

进入 CentOS：

```bash
cd /opt/rag_service
docker compose up -d
docker compose ps
```

如果只需要检查 Redis：

```bash
docker exec quote-redis redis-cli ping
```

正常返回：

```text
PONG
```

---

## 七、如果 Celery 队列异常

先看健康检查：

```powershell
Invoke-RestMethod http://localhost:9000/health/ready
```

如果看到：

```text
worker=no_reply
worker_count=0
```

手动启动 Celery：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_celery_worker.ps1
```

如果提示 pid 文件已存在，当前脚本已经会自动识别并清理大多数 stale pid 问题。

---

## 八、任务计划程序

Windows 开机自启任务名：

```text
AI_MiddleOffice
```

查看状态：

```powershell
Get-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
```

手动触发：

```powershell
Start-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
```

重新安装开机自启任务，需要用管理员 PowerShell：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_service.ps1
```

说明：任务状态显示 `Ready` 不一定是异常。`AI_MiddleOffice` 是启动看门狗任务，会在后台反复调用 `start_all.ps1 -NoBrowser`，直到系统 ready 或 60 分钟超时。是否真正可用，以 `/health/ready` 为准。

看门狗日志：

```powershell
Get-ChildItem C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office\logs\startup_watchdog_*.log |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  Get-Content -Tail 80
```

---

## 九、最短应急启动命令

如果不想逐项排查，直接执行这一条：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

然后打开：

```text
http://localhost:9000/
```
