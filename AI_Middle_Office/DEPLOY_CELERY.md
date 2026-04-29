# Redis/Celery 生产化部署 Runbook

> 适用于第 14 步：把报价任务从 FastAPI 本地线程切换到 Celery Worker。

## 1. CentOS 启动 Redis

在 CentOS `192.168.88.128` 上执行：

```bash
cd /opt/rag_service
docker compose up -d redis
docker ps --filter name=quote-redis
docker exec quote-redis redis-cli ping
```

期望输出：

```text
PONG
```

Windows 侧可验证端口：

```powershell
Test-NetConnection 192.168.88.128 -Port 6379
```

## 2. Windows 安装依赖

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m pip install -r requirements.txt
```

## 3. 切换队列模式

编辑 `AI_Middle_Office/.env`：

```env
TASK_QUEUE_MODE=celery
CELERY_BROKER_URL=redis://192.168.88.128:6380/0
CELERY_RESULT_BACKEND=redis://192.168.88.128:6380/1
QUOTE_TASK_TIME_LIMIT_SECONDS=240
QUEUE_HEALTH_TIMEOUT_SECONDS=1.5
```

## 4. 手动启动 Celery Worker

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
.\start_celery_worker.ps1
```

## 5. 注册 Worker 开机自启

以管理员身份打开 PowerShell：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
.\install_celery_worker_service.ps1
```

常用控制命令：

```powershell
Start-ScheduledTask -TaskName AI_MiddleOffice_CeleryWorker
Stop-ScheduledTask  -TaskName AI_MiddleOffice_CeleryWorker
Get-ScheduledTask   -TaskName AI_MiddleOffice_CeleryWorker
```

## 6. 健康检查

启动 FastAPI 后访问：

```powershell
Invoke-RestMethod http://localhost:9000/health/ready
```

Celery 生产模式下，期望：

```json
{
  "status": "ready",
  "database": "ok",
  "task_queue_mode": "celery",
  "task_queue": {
    "mode": "celery",
    "broker": "ok",
    "worker": "ok"
  }
}
```

如果 `broker=error`，优先检查 CentOS Redis；如果 `worker=no_reply`，优先检查 Windows 任务计划程序中的 Celery Worker。
