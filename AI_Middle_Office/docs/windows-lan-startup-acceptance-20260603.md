# Windows 内网启动阶段 1 验收记录

验收日期：2026-06-03

## 1. 验收结论

阶段 1 当前结论：

```text
local_ready_passed
lan_bind_passed
cross_device_lan_pending
```

说明：

- Windows 本机启动成功。
- FastAPI 已以内网试运行模式启动。
- 本机 `127.0.0.1` 登录页可打开。
- Windows 当前内网 IP `192.168.110.138` 登录页可打开。
- `/health/ready` 返回 ready。
- Celery 队列状态正常。
- 当前没有另一台连接同一 Wi-Fi/局域网的电脑，因此“其他设备访问”暂记为待补验。

## 2. 已确认访问地址

本机访问：

```text
http://127.0.0.1:9000/login
```

当前可用内网访问：

```text
http://192.168.110.138:9000/login
```

启动脚本输出的候选地址：

```text
Local URL: http://127.0.0.1:9000/
LAN URL:   http://192.168.198.1:9000/
LAN URL:   http://192.168.88.1:9000/
LAN URL:   http://192.168.110.138:9000/
LAN URL:   http://192.168.1.124:9000/
```

当前建议优先给其他设备使用：

```text
http://192.168.110.138:9000/login
```

原因：该地址已被当前 Windows 本机验证可打开，且更像当前 Wi-Fi/局域网地址。`192.168.88.1`、`192.168.198.1` 更可能是虚拟网卡地址，不建议优先对外提供。

## 3. 健康检查结果

执行：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health/ready
```

返回关键信息：

```text
status          : ready
database        : ok
rag_service_url : http://192.168.88.128:8001
task_queue_mode : celery
task_queue.ok   : True
task_queue.broker : ok
task_queue.worker : ok
task_queue.worker_count : 1
```

结论：FastAPI、数据库连接、RAG 配置、Celery broker 和 worker 均处于可用状态。

## 4. 仍待补验

### 4.1 其他设备访问

当前没有另一台连接同一 Wi-Fi/局域网的电脑，因此暂未验证：

```text
其他设备 -> http://192.168.110.138:9000/login
```

补验方式：

1. 让另一台电脑或手机连接同一个 Wi-Fi/局域网。
2. 浏览器打开：

```text
http://192.168.110.138:9000/login
```

3. 如果打不开，在 Windows 本机确认防火墙是否放行 TCP `9000`。

### 4.2 Windows 防火墙

当前只能确认本机和本机访问内网 IP 可用，不能完全证明外部设备已经被防火墙放行。

如后续外部设备打不开，可在管理员 PowerShell 中执行：

```powershell
New-NetFirewallRule `
  -DisplayName "AI Middle Office FastAPI 9000" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 9000 `
  -Profile Private
```

## 5. 当前阶段判断

阶段 1 可以进入“有条件完成”：

- 本机开发访问：通过。
- 内网绑定启动：通过。
- 健康检查：通过。
- 当前访问地址输出：通过。
- 访问地址文件生成：通过。
- 跨设备访问：待后续有设备时补验。

在不具备第二台局域网设备的情况下，可以继续推进阶段 2：账号权限、安全口径与备份恢复收口。
