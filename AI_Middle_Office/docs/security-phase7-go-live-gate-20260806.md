# AI 智能报价中台阶段 7 上线门禁记录

日期：2026-08-06
范围：ECS 应用节点、CentOS 数据/RAG 节点、IPsec 混合私网、HTTPS 公网入口、备份恢复与监控告警。

## 1. 当前裁决

当前结论为：`CONTROLLED_GO_WITH_ACCEPTED_RISKS`。

业务负责人于 2026-08-06 明确确认“受控上线 GO”，接受第 3 节所列已知残余风险，并同意继续遵守本记录中的运行要求与 `NO-GO` 回退条件。

- 可以进入受控、小流量生产试运行。
- 本裁决只批准受控、小流量生产试运行，不批准扩大为无约束全量生产。
- `PUBLIC_ACCESS_ENABLED=false` 必须继续保持；该开关用于关闭公网实验/管理能力，不等同于关闭 Nginx 登录入口。

## 2. 已通过门禁

### 2.1 加密异机备份

- CentOS 完整冷备：`/opt/rag_service/backups/20260806_105418`
- 冷备大小：约 `1.2 GiB`
- MySQL dump 大小：`801338167` bytes
- Milvus/etcd/MinIO 在一致性冷快照期间停止并自动恢复。
- 备份包含 MySQL、N8N、Milvus、etcd、Milvus MinIO、报价 MinIO、Compose 配置和 SHA-256 清单。
- 运行 `.env` 未包含在备份中；秘密必须从批准的秘密存储恢复。
- AES-256 加密包：
  `D:\AI_Middle_Office_Offsite_Backups\ai-middle-office-centos-20260806_105418.tar.gpg`
- 加密包大小：`1275242549` bytes
- 加密包 SHA-256：
  `92db1740f71361b4616ac8b1cbda7e0b5b607c56bc8b4a3ff1568deb694388b2`
- 源端解密/tar 完整性检查和 Windows 双端哈希比对均通过。

### 2.2 恢复演练

- 报告：`/opt/rag_service/backups/phase7-mysql-restore-20260806_111439.report`
- 使用 `mysql:8.0.39`、`--network none` 临时容器实际导入新 dump。
- 恢复出 `117` 张基础表。
- `quote_jobs`、`cost_items`、`budget_project_pricing_run_draft_snapshots` 均存在。
- Alembic 版本为 `20260801_0081`。
- 临时容器、匿名卷和临时密码文件均已清理。
- 生产数据库未被写入或替换。

### 2.3 免费监控与证书提醒

- 安装报告：`/home/aiadmin/ai-phase7-monitoring-install-20260806_114509.txt`
- `ai-middle-office-monitor.timer` 已启用，每 5 分钟运行。
- 检查项包括：Nginx、Docker、firewalld、IPsec、API/Worker、API readiness、TLS、证书剩余天数、磁盘、RAG、MySQL、N8N、Redis 和 MinIO 私网连通性。
- 证书少于 45 天时进入告警状态；相同失败状态每 6 小时最多重复发送一次，恢复时发送恢复消息。
- 钉钉测试消息已由操作人确认收到。
- 应用内置 ops 告警循环同时使用同一批准的钉钉机器人配置。

### 2.4 顺序重启验证

- ECS 重启前 boot ID：`bc511a22-2ec3-4d01-994a-c67335bbaf3a`
- ECS 重启后 boot ID：`49a66d06-963c-4bc9-9626-98c61ca189b1`
- CentOS 重启前 boot ID：`06cb5de0-1324-4f1c-9eb3-d348da38c166`
- CentOS 重启后 boot ID：`c1048802-2348-4ca3-b391-cafb6ba1f837`
- 两台机器均完成真实 reboot，而非仅服务重启。
- 重启后 Nginx、Docker、ECS firewalld、两端 IPsec、监控 timer、CentOS 自定义防火墙、VPN MySQL DNAT 和 NAT-T 保活均自动恢复。
- CentOS `23` 个容器恢复运行；带 healthcheck 的容器最终全部健康。
- 自动监控在 CentOS 启动早期记录一次失败，后续自动恢复为 `Result=success / ExecMainStatus=0`，证明故障/恢复状态机生效。

### 2.5 公网入口与端口面

- `www.qskingship.com` 的三个公共 DNS 解析器均返回 `8.163.58.211`。
- TLS 1.3 和证书校验通过；证书到期日为 `2026-11-04`。
- 首页返回 307 并进入登录流程。
- HSTS、X-Content-Type-Options、X-Frame-Options、Referrer-Policy、Permissions-Policy、CSP 均存在。
- `/docs`、`/redoc`、`/openapi.json` 和两个实验管理路由公网返回 404。
- 公网 `/health/ready` 返回 403。
- 异常 SNI 不返回 HTTP 内容。
- 登录与通用限流均在 ECS reboot 后通过，限流窗口结束后恢复 307。
- 最终 TCP 端口扫描仅 `443` 开放；`22`、`80`、`3306`、`5678`、`6380`、`8001`、`9000`、`9001`、`9002`、`19530` 均阻断。

## 3. 当前残余风险

### 3.1 SELinux

- ECS：`enabled / targeted / permissive`，配置文件同为 permissive。
- CentOS：SELinux disabled。
- 当前以非 root 容器、只读根文件系统、cap drop、no-new-privileges、精确防火墙和私网隔离补偿，但不等同于 SELinux enforcing。
- 切换 enforcing 前必须先审计 AVC、准备策略/标签并安排独立维护窗口，不得直接强制切换。

### 3.2 无付费 WAF

- 免费方案没有云 WAF。
- 当前补偿措施为只开放 443、Nginx 两级限流、安全头、隐藏敏感路由、异常 SNI 关闭和外部端口监控。
- 遭遇高强度 DDoS、复杂 Bot 或应用层 0-day 时，补偿能力弱于云 WAF。

### 3.3 手工 DNS-01 证书

- 当前证书由手工 DNS-01 签发，不能由普通 Certbot timer 自动续期。
- 监控在剩余 45 天时告警；按当前证书应在 `2026-09-20` 前后开始续期，不得等待最后 7 天。
- 续期时必须重新添加 `_acme-challenge.www.qskingship.com` TXT、验证公共 DNS、完成签发、执行 `nginx -t`、reload 和外部门禁，再删除一次性 TXT。

### 3.4 CGNAT 动态公网 IP

- IPsec 对端位于 CGNAT 后，出口 IP 变化会要求同步 ECS 安全组和主机 UDP 500/4500 精确来源规则。
- CentOS `ai-ipsec-nat-keepalive.service` 已 `active + enabled`，每 10 秒从 `192.168.88.128` 探测 `10.240.10.1`，降低 NAT 映射过期概率。
- NAT 保活不能解决运营商主动更换公网 IP；监控会发现私网失败，但安全组更新仍需人工处理。

### 3.5 备份物理独立性与秘密托管

- Windows D 盘副本已实现离开 CentOS 文件系统的加密副本。
- 若 D 盘与 CentOS 虚拟机属于同一物理主机或同一地点，它不能抵御整机损坏、盗窃或同站点灾害；应再保留一份离线移动介质或独立地点副本。
- `.env`、IPsec PSK、数据库密码、钉钉 Webhook/加签密钥和私钥不在数据备份中。恢复能力依赖这些秘密已存入独立密码管理器/离线托管。
- 加密包口令不得只保存在运行服务器或同一 D 盘中。

## 4. 运行要求

- 每日关注钉钉告警；告警恢复不代表可以忽略首次失败原因。
- 每次重要数据导入、成本库批量变更和 RAG 同步前后制作备份。
- 至少每月制作一次新的冷备、加密异机复制并做哈希核对。
- 至少每季度进行一次实际隔离恢复演练。
- 证书告警出现后立即启动手工 DNS-01 续期流程。
- 公网安全组继续只开放 443；SSH 继续仅使用 IPsec 私网。
- `PUBLIC_ACCESS_ENABLED=false` 继续保持，除非另有经过审计的正式变更单。

## 5. 最终上线口径

在业务负责人明确接受第 3 节风险，并确认备份口令及运行秘密已独立托管后，可以进入受控生产试运行。

以下任一条件发生时立即回到 `NO-GO`：

- 公网除 443 外出现其他监听/安全组放行；
- IPsec 或 NAT 保活无法恢复；
- API readiness、MySQL、RAG、Redis、MinIO、N8N 或 Worker 持续失败；
- 监控 timer/钉钉告警失效；
- 证书不足 30 天且续期未完成；
- 最新备份哈希失败、无法解密或恢复演练失败；
- `PUBLIC_ACCESS_ENABLED` 被改为 true；
- 出现未评估的高危漏洞或凭据泄露。
