# 试运行故障排查手册

更新时间：2026-06-03

## 1. 排查原则

先判断问题属于哪一层：

1. Windows 服务是否启动。
2. 其他电脑是否能访问 Windows。
3. CentOS 依赖是否可达。
4. 账号权限是否正确。
5. 业务页面是否有错误。
6. 数据或业务规则是否不清。

不要一上来就改业务规则或改数据库。

## 2. 网页打不开

### 本机打不开

检查：

```powershell
Test-NetConnection 127.0.0.1 -Port 9000
```

如果失败，重启：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -Restart
```

### 本机能打开，其他设备打不开

检查：

- 其他设备是否同一 Wi-Fi/局域网。
- 使用的是否是最新 `LAN URL`。
- Windows 防火墙是否放行 TCP `9000`。
- Windows 是否换网导致 IP 变化。

查看最新访问地址：

```powershell
Get-Content C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office\logs\current_access_urls.txt
```

## 3. 健康检查不是 ready

执行：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health/ready
```

常见情况：

| 现象 | 可能原因 | 处理 |
|---|---|---|
| database 不是 ok | MySQL 不可达 | 检查 CentOS、Docker、5455 |
| task_queue.ok=False | Redis 或 worker 异常 | 检查 Redis 6380，重启 Celery |
| worker_count=0 | Celery worker 未启动 | 运行 `start_celery_worker.ps1` 或 `start_all.ps1 -Lan -Restart` |
| RAG 不可达 | RAG 服务异常 | 检查 `192.168.88.128:8001` |

完整检查：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_startup.ps1 -AppUrl http://127.0.0.1:9000 -TimeoutSeconds 15
```

## 4. CentOS 依赖不可达

检查端口：

```powershell
Test-NetConnection 192.168.88.128 -Port 5455
Test-NetConnection 192.168.88.128 -Port 6380
Test-NetConnection 192.168.88.128 -Port 8001
Test-NetConnection 192.168.88.128 -Port 5678
Test-NetConnection 192.168.88.128 -Port 9002
```

含义：

```text
5455 = MySQL
6380 = Redis
8001 = RAG
5678 = N8N
9002 = MinIO
```

如果多项不通，优先检查 CentOS 虚拟机是否启动。

## 5. 登录失败

常见原因：

- 用户名或密码错误。
- 账号被停用。
- 临时密码已改过。
- 用户误用了旧共享账号。

处理：

1. 确认账号在 `/admin/permissions` 中存在。
2. 确认账号启用状态。
3. 确认是否必须改密。
4. 不使用 `admin / 123`。
5. 如 system_admin 不可用，才使用 `create_admin.py --confirm-rescue`。

## 6. 登录后看不到模块

可能原因：

- 角色未分配。
- 用户需要重新登录刷新 `role_version`。
- 功能开关未开启。
- 当前账号不是对应角色。

处理：

1. 退出重新登录。
2. 管理员查看 `/admin/permissions`。
3. 查看用户角色和可用模块。
4. 需要变更角色时写明原因。

## 7. 报价任务异常

常见现象：

- 报价任务长时间不结束。
- AI 预审为空。
- 确认下发被阻断。
- 草稿恢复异常。

处理：

1. 记录任务创建时间和用户。
2. 查看 `/health/ready` 的 queue 状态。
3. 查看 Celery worker 日志。
4. 查看报价历史或报价运营详情。
5. 未补价占位、完整性不通过、无权限下发属于正常阻断，不应绕过。

## 8. 需求单标准化异常

常见现象：

- Excel 无法上传。
- Sheet 识别不正确。
- 列映射错误。
- 行校验阻断。

处理：

1. 确认文件是 `.xlsx` 或 `.xlsm`。
2. 检查表头和合并单元格。
3. 人工调整列映射。
4. 阻断行不要发起报价。
5. 记录原始文件名和 Sheet 名。

## 9. 成本库异常

常见现象：

- 看不到成本库。
- 不能启用 active。
- 不能导出。
- RAG 同步提示异常。

处理：

1. 先确认账号角色。
2. `staff` 不应查看完整成本库。
3. 启用 active 需要 `cost_approver` 或管理员。
4. 导出需要 `cost_exporter`、`admin` 或 `system_admin`。
5. 敏感操作会写审计，不用共享账号。

## 10. 备份异常

常见现象：

- SSH 登录失败。
- MySQL skipped。
- `PROCESS privilege` 报错。
- Milvus 在线备份失败。

处理：

- SSH 失败：确认密钥口令、root 密码和 CentOS 网络。
- MySQL skipped：确认 `/opt/rag_service/.env` 中 `MYSQL_CONTAINER/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DATABASE`。
- `PROCESS privilege`：确认备份脚本已包含 `--no-tablespaces`。
- Milvus 在线失败：真实试运行前低峰期执行 `-ColdMilvusSnapshot`。

## 11. 问题登记

所有试运行问题写入：

```text
reports/trial_readiness/20260603_stage3/trial_issue_log.csv
```

问题记录最少包含：

- 时间。
- 操作人。
- 页面。
- 错误提示。
- 是否阻断。
- 处理人。
- 当前状态。
