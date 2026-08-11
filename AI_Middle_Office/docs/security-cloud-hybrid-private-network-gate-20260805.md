# AI 智能报价中台混合私网验收记录（2026-08-05）

## 1. 结论

安全上云第四阶段“ECS ↔ 本地 CentOS IPsec 混合私网”已完成运行态定位、最小规则验证、持久化和两端重启复验。

- `PUBLIC_ACCESS_ENABLED=false` 的边界未改变。
- 未启动 ECS 业务 Compose、API 或 Worker，未开放公网业务入口。
- ECS 测试容器固定使用 `10.240.10.11`，测试结束自动删除。
- 允许端口 `3306/5678/6380/8001/9002` 全部连通。
- MinIO Console `9001` 与 Milvus `19530` 继续阻断。
- ECS 与 CentOS 重启后，防火墙、IPsec 和 NAT 映射保活均自动恢复。

第四阶段状态：`passed`。第五阶段 ECS 内部 API/Worker 启动与业务冒烟也已于
2026-08-05 通过，详见 `security-cloud-phase5-dark-runtime-gate-20260805.md`。

## 2. 拓扑与安全边界

| 项目 | 值 |
|---|---|
| ECS 私网地址 | `172.18.138.198` |
| ECS 应用网段 | `10.240.10.0/24` |
| ECS API / Worker | `10.240.10.10` / `10.240.10.11` |
| CentOS 后端 | `192.168.88.128` |
| 当前 CGNAT 出口 | `14.218.34.192/32`，可能变化 |
| 隧道 | Libreswan、IKEv2、AES-256-GCM、PSK、精确 selector、passive liveness |

当前 ECS firewalld 的 SSH、UDP 500 和 UDP 4500 仅允许当前出口地址；旧出口 `120.229.193.76/32` 已从运行态和永久态删除。阿里云安全组中的 UDP 500/4500 来源也已由用户更新为当前出口地址。若 CGNAT 出口再次变化，必须同步更新安全组和 ECS firewalld 后再恢复隧道，不能扩大为任意来源。

## 3. 根因闭环

本轮故障包含三个独立层次：

1. CentOS 重启后原临时 NAT 保活消失，同时 CGNAT 出口变化，ECS 安全组和 firewalld 仍允许旧地址，IKE 报文在 ECS `filter_IN_public` 被拒绝。
2. 隧道恢复后，CentOS TCP 回包已到达 ECS `eth0`，但 Docker 在 `ip raw PREROUTING` 中为容器地址动态添加直连保护 DROP。原 NOTRACK 规则不是终止 verdict，回包继续命中该 DROP。
3. ECS raw 路径修复后，Redis `6380` 和 MinIO API `9002` 仍失败。CentOS 在 PREROUTING 已将它们分别 DNAT 为 `6379` 和 `9000`，而原 `AI_MO_DOCKER` IPsec 放行仍按宿主机端口匹配，随后命中受保护端口 DROP。

最终规则仅覆盖已验证来源、目的、端口、接口和 IPsec policy；未放宽 9001、19530 或其他受保护端口。

## 4. 正式持久配置

### 4.1 ECS

- 脚本：`/usr/local/sbin/ai-middle-office-private-forward-firewall.sh`
- 服务：`/etc/systemd/system/ai-middle-office-private-forward-firewall.service`
- 服务状态：`enabled`、`active (exited)`
- 启动顺序：在 firewalld、Docker 和 IPsec 之后执行，并随 firewalld/Docker 生命周期重放。

持久规则共五条：

- raw 入站：后端 `192.168.88.128` → `10.240.10.10/31`、允许源端口、`eth0`、IPsec 入站 policy，NOTRACK。
- raw 入站：同一范围的早期 ACCEPT，终止 Docker 后续 raw DROP。
- raw 出站：`br-ai-app` 上 `10.240.10.10/31` → 后端允许目的端口，NOTRACK。
- DOCKER-USER 回包：`eth0 → br-ai-app`，分别限定 API `.10` 与 Worker `.11`、后端源地址、允许源端口和 IPsec 入站 policy。

firewalld 当前出口地址的 SSH/UDP 500/UDP 4500 规则已同时存在于运行态和永久态；旧出口规则已清除。既有 IPsec NAT POSTROUTING 直连规则保持不变。

### 4.2 CentOS

- 防火墙脚本：`/usr/local/sbin/ai-middle-office-firewall.sh`
- 防火墙服务：`ai-middle-office-firewall.service`
- 保活脚本：`/usr/local/sbin/centos-ipsec-nat-keepalive-probe.sh`
- 保活服务：`ai-ipsec-nat-keepalive.service`
- 防火墙、保活、IPsec、Docker：均为 `enabled`、`active`
- 原临时单元 `ai-ipsec-keepalive-live` 已停止并消失。

`AI_MO_DOCKER` 为 API/Worker 各增加两条 DNAT 后放行：

- 原始目的 `192.168.88.128:6380`、DNAT 后端口 `6379`。
- 原始目的 `192.168.88.128:9002`、DNAT 后端口 `9000`。

规则同时限定 ECS 源地址和 IPsec 入站 tunnel policy，不硬编码可能随容器重建变化的容器 IP。

## 5. 最终验证证据

### 5.1 持久化前后

- CentOS 持久化后、ECS 持久化前：`/home/aiadmin/ai-hybrid-traces/20260805_101041`
- ECS 持久化后：`/home/aiadmin/ai-hybrid-traces/20260805_101138`
- ECS 重启前审计：`/home/aiadmin/ai-ecs-pre-reboot-audit.txt`
- ECS 重启前门禁：`/home/aiadmin/ai-hybrid-traces/20260805_102106`

### 5.2 两端重启后

- ECS 最终报告：`/home/aiadmin/ai-ecs-post-reboot-validation.txt`
- ECS 最终门禁：`/home/aiadmin/ai-hybrid-traces/20260805_102533`
- 最终报告结论：`post_reboot_validation=passed`
- 最终门禁结论：`private_connectivity_gate=passed`、`connectivity_gate_rc=0`
- ESP 双向字节持续增长；ECS raw 三条规则、Worker 回包规则以及 CentOS Redis/MinIO DNAT 规则均有命中。

最终逐端口结果：

| 类型 | 端口 | 结果 |
|---|---:|---|
| MySQL | 3306 | PASS allowed |
| N8N | 5678 | PASS allowed |
| Redis | 6380 | PASS allowed |
| RAG | 8001 | PASS allowed |
| MinIO API | 9002 | PASS allowed |
| MinIO Console | 9001 | PASS blocked |
| Milvus | 19530 | PASS blocked |

## 6. 备份与回滚

### ECS

- 备份：`/home/aiadmin/ai-hybrid-backups/pre-private-forward-persistence-20260805_101131`
- 备份清单 SHA-256 已通过最终重启后校验。
- 仅在确认需要回滚时执行：

```bash
sudo bash /home/aiadmin/ecs-install-private-forward-persistence.sh rollback \
  /home/aiadmin/ai-hybrid-backups/pre-private-forward-persistence-20260805_101131
```

### CentOS

- 备份：`/opt/rag_service/backups/pre-hybrid-persistence-20260805_100501`
- 备份清单 SHA-256 已校验。
- 仅在确认需要回滚时执行：

```bash
sudo bash /root/centos-install-hybrid-persistence.sh rollback \
  /opt/rag_service/backups/pre-hybrid-persistence-20260805_100501
```

回滚后必须重新运行完整 7 项门禁；不得直接进入第五阶段。

## 7. 第五阶段准入条件

进入 ECS 内部 API/Worker 启动与业务冒烟前，至少保持：

1. `PUBLIC_ACCESS_ENABLED=false`，不启动公网业务，不开放额外安全组端口。
2. 使用修复发布边界并重新通过镜像门的 `ai-middle-office-app:20260805_161737`，运行用户保持 `10001:10001`。
3. 只在 `10.240.10.10/.11` 启动 API/Worker；不把业务端口发布到 ECS 公网接口。
4. 启动前只验证所需配置项是否存在，不输出数据库密码、PSK、Webhook、私钥或其他秘密。
5. 依次验证 MySQL TLS、Redis、RAG、MinIO API、Celery broker/worker 与 `/health/ready`；9001/19530 必须继续阻断。
6. SELinux 仍为 permissive，属于后续上线门的未完成项，不能在本阶段误记为已加固完成。

第五阶段通过前，不进入 Nginx HTTPS/WAF/公网 443 阶段。
