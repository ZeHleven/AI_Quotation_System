# Step 21 - Operations Monitoring

管理员页面 `http://localhost:9000/admin.html` 顶部已经增加“运维监控与告警”面板。

## API

```text
GET /api/v1/admin/ops/dashboard
GET /api/v1/admin/ops/services
GET /api/v1/admin/ops/logs
GET /api/v1/admin/ops/jobs
```

均要求 admin JWT。

## Dashboard 覆盖范围

- MySQL：执行 `SELECT 1`
- Redis：对 Celery broker 执行 `PING`
- Celery：复用 worker `ping`
- RAG：访问 RAG 服务 `/docs`
- MinIO：复用文件存储健康检查
- n8n：对 webhook 所在 host/port 执行 TCP 探活
- 报价任务：统计 queued/running 长时间未更新任务
- 异常日志：扫描 `AI_Middle_Office/logs/*.log` 最近日志中的 ERROR、Traceback、数据库断连和任务崩溃线索

## 可配置项

```env
OPS_PROBE_TIMEOUT_SECONDS=2
OPS_STUCK_JOB_MINUTES=30
OPS_LOG_SCAN_LINES=800
OPS_LOG_MAX_FILES=6
```

## 判断方式

`overall_status=ready` 表示基础服务均可达，且没有卡住任务或异常日志提醒。

`overall_status=degraded` 表示至少存在一条告警，需要查看 dashboard 中的 alerts、服务状态、卡住任务或异常日志。
