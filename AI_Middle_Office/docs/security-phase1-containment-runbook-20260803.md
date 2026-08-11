# 第一阶段安全止血与上线前执行手册

日期：2026-08-03

## 1. 阶段目标与边界

本阶段只处理“系统尚未开放公网前必须完成”的高风险止血项：关闭公网模式下的试验接口、管理接口鉴权失败时默认拒绝、减少 CentOS 宿主机暴露端口、避免备份默认携带明文环境变量，并增加只读安全预检。

在用户确认维护窗口并授权后，本阶段已完成 CentOS 配置部署、容器重建、主机防火墙收口、指定凭据轮换和 MySQL 账号加固。Alembic 只执行了 `current` 只读校验，没有迁移或修改业务表；N8N/Dify 工作流、Milvus 业务数据均未改动。云安全组、公网反向代理、HTTPS/WAF 和第三方平台凭据撤销不在本次内网实施范围内。

## 2. 已完成的代码止血

- 当 `PUBLIC_ACCESS_ENABLED=true` 时，不再挂载 Codex Worker 和 DWG Quantity Trial 两组试验/POC 路由；当前内网模式仍保持兼容。
- RAG `/admin/reload` 在 `RELOAD_SECRET` 缺失、弱口令或不匹配时一律拒绝，避免未配置密钥时绕过鉴权。
- Milvus `19530/9091` 不再发布到宿主机；RAG、Redis、MinIO 管理端口必须绑定显式的 `INTERNAL_BIND_ADDRESS`。
- Compose 中关键密钥取消默认值，缺少配置时直接拒绝启动。
- 备份脚本启用 `umask 077`，并默认不复制 `.env`。只有显式设置 `BACKUP_INCLUDE_ENV=true` 才会包含环境文件。
- 新增 `scripts/security_phase1_preflight.py`，只输出检查结果，不输出密钥内容，不修改任何环境。
- 应用日志新增 URL 查询参数、Authorization/Bearer 和常见 secret 赋值脱敏，`httpx` 日志级别收口到 `WARNING`；历史日志已执行一次性脱敏。
- 钉钉告警新增官方 `oapi.dingtalk.com` HTTPS 端点限制和 HMAC-SHA256 时间戳加签；Webhook 已配置但加签密钥缺失/过短时拒绝发送。
- 新增 systemd 持久化主机防火墙脚本/服务、Windows 安全重启脚本和告警 Webhook 禁用/日志脱敏运维脚本。

## 3. 当前环境实施结论

- `PUBLIC_ACCESS_ENABLED=false`，公网访问仍未开启；`ALLOW_SELF_REGISTRATION` 未配置，按默认值关闭。
- 报价 MinIO 和 Milvus 对象存储凭据已轮换，Windows 应用与 CentOS 配置已同步；旧值未写入 Git 或运行日志。
- RAG reload 使用强密钥并已验证错密钥返回 `403`；服务端密钥缺失或弱口令时按未配置处理并返回 `503`。
- `INTERNAL_BIND_ADDRESS=192.168.88.128`：RAG `8001`、Redis 映射 `6380`、报价 MinIO `9002/9003` 只监听该私网地址；Milvus `19530/9091` 不再发布到宿主机。
- `ai-middle-office-firewall.service` 已启用并处于 active，仅允许 Windows 应用机 `192.168.88.1/32` 访问受保护端口；变更后的新 SSH 连接和允许来源连通性已复核。
- FastAPI 当前仅监听 `127.0.0.1:9000`；`/health/ready=ready`，数据库、Celery broker/worker 正常，MinIO 认证和真实 RAG 检索通过。
- 部署时发现钉钉机器人 Webhook 曾被 `httpx` INFO 日志记录完整查询参数。390 个历史日志文件已扫描、29 个文件完成脱敏，复扫剩余命中为 0；用户已在钉钉平台删除旧机器人。新机器人已手动写入受控 `.env`，通过官方 HTTPS 端点、`access_token` 和 HMAC-SHA256 加签校验；重启后测试消息获钉钉 API 成功响应，用户已确认群内收到测试消息，日志复扫仍为 0 个待脱敏文件。
- MySQL 已完成账号拆分和强密码轮换：`ai_runtime@192.168.88.1` 仅有 `SELECT/INSERT/UPDATE/DELETE`，`ai_migrator@192.168.88.1` 仅在业务库拥有上述 DML 与 `CREATE/ALTER/DROP/INDEX`；两者均无全局权限、`ALL PRIVILEGES` 或 `GRANT OPTION`，使用 `caching_sha2_password`、随机 43 字符密码和 `REQUIRE SSL`。Windows 连接通过固定 CA 验证，实测 TLS 1.3 `TLS_AES_256_GCM_SHA384`；运行与 Alembic URL 已分离，`20260801_0081 (head)` 校验通过。旧 `ai_app@192.168.88.1` 已先锁定、清空休眠连接并验证旧凭据被拒绝，随后删除。
- 当前 MySQL 自动生成的服务端证书没有 IP SAN，因此客户端使用固定 CA 验证证书链，但暂未启用主机名/IP 身份校验；服务端全局 `require_secure_transport` 仍为 `0`，避免在未盘点完 MySQL 容器其他内部消费者前造成中断。新建的两个应用账号已各自通过 `REQUIRE SSL` 强制加密，不受该全局值影响。
- 当前没有可接收公网 HTTPS 的服务，云安全组/WAF 也未配置；本环境仍不具备公网开放条件。

## 4. 部署前只读预检

在 Windows 项目目录执行：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
C:\Users\12521\miniconda3\python.exe scripts\security_phase1_preflight.py
```

部署机配置复核时，使用仅管理员可读的环境文件路径：

```bash
python scripts/security_phase1_preflight.py --rag-env /opt/rag_service/.env
```

验收要求：所有项目必须为 `PASS`；`WARN` 必须有人确认原因；任何 `FAIL` 都必须停止部署。脚本不得复制到日志中包含密钥的环境文件内容。

## 5. 密钥轮换顺序

密钥轮换会影响运行中的组件，必须在维护窗口执行，并准备旧密钥的短期回滚副本。禁止把新旧密钥写入 Git、聊天记录、工单正文或普通日志。

1. 在受控密钥管理器中生成随机高强度密钥；建议至少 32 个随机字符。
2. 报价 MinIO 和 Milvus 对象存储凭据已在本次维护窗口轮换并验证。后续轮换仍须同时更新所有调用端，验证已有对象可读写后再撤销旧凭据。
3. 新建钉钉自定义机器人时必须选择“加签”，使用 `scripts/security_configure_dingtalk_alert.py` 在本机隐藏输入 Webhook 和 `SEC...` 密钥；不得在聊天、命令行参数或普通日志中传递。
4. 轮换 `RELOAD_SECRET`、`WEBHOOK_SECRET`、JWT 密钥及 N8N/Dify 中对应凭据；JWT 轮换会使现有登录令牌失效，应提前通知用户。
5. 审核并轮换智谱、DeepSeek 等第三方 API 密钥；确认旧密钥已在供应商控制台撤销。
6. RAG Compose 内部 MinIO 凭据应先保持当前有效值完成网络收口；如需轮换，单独做数据访问回归，不能只修改一端。
7. 每次只轮换一组依赖，完成健康检查和业务冒烟后再继续下一组。

## 6. CentOS 与云网络收口

目标访问矩阵：

| 端口 | 允许来源 | 公网策略 |
|---|---|---|
| 443 | 互联网用户 | 仅经 HTTPS 入口/WAF 放行 |
| 8001 RAG | Windows 应用服务器私网 IP | 禁止公网 |
| 6380 Redis 映射 | 指定内部服务私网 IP | 禁止公网 |
| 9002 MinIO API | 指定内部服务私网 IP | 禁止公网 |
| 9003 MinIO Console | 仅运维私网/VPN | 禁止公网 |
| 5678 N8N | Windows 应用服务器或运维 VPN | 禁止公网 |
| 19530/9091 Milvus | 不发布宿主机 | 禁止公网及普通内网直连 |

当前内网实施基线：Compose 已完成上述私网绑定；主机防火墙由 `/usr/local/sbin/ai-middle-office-firewall.sh` 和 `ai-middle-office-firewall.service` 持久化执行，允许来源为 `192.168.88.1/32`。迁移到云服务器时必须先把私网地址和允许来源替换为云内网实际地址，并在云安全组复制同等或更严格策略，不能沿用当前局域网地址。

执行前必须：

1. 确认 `INTERNAL_BIND_ADDRESS` 是 CentOS 的真实私网 IP，禁止使用 `0.0.0.0`、公网 IP 或空值。
2. 同时配置云安全组和主机防火墙；云安全组只允许上述来源，主机防火墙再做一层同等或更严格限制。
3. 在 `/opt/rag_service/` 先运行 `docker compose config`，确认没有端口误绑定和缺失变量，再执行更新。
4. 从 Windows 应用服务器验证允许访问；再从一台非允许来源主机验证连接被拒绝。
5. 不得把 MySQL、Milvus、Redis、MinIO Console、N8N 编辑器直接暴露到公网。

## 7. 备份与回滚

- 本次部署前备份位于 `/opt/rag_service/backups/20260803_164452`，约 `1.2 GiB`，包含 MySQL、N8N、Milvus etcd/MinIO/data 和报价 MinIO；SQL 结束标记、N8N JSON、tar 列表及全部 SHA-256 已校验通过，未包含 `.env`。
- MySQL 账号切换前另完成冷全量备份 `/opt/rag_service/backups/20260803_210859`：MySQL SQL 约 801 MB，并包含 N8N、Milvus data/etcd/MinIO 和报价 MinIO；SQL dump 完成标记、N8N JSON、全部 tar 列表和 `SHA256SUMS` 均校验通过，未包含 `.env`。`20260803_210746` 是 Milvus 文件变化期间产生的不完整预备副本，不作为恢复基线。
- 该副本当前仍位于同一台 CentOS 主机，只能用于本次短期回滚，不能替代加密异机/离线备份；迁云或开放公网前必须再制作并验证异机副本。
- 部署前执行完整数据库和对象存储备份，校验备份结束标记和哈希；备份副本应加密并存放到异机或受控对象存储。
- 默认保持 `BACKUP_INCLUDE_ENV=false`。如果合规要求必须备份环境文件，应使用独立加密流程，且限制文件权限和访问审计。
- 保存当前有效的 Compose 和环境变量版本到受控密钥/配置库，不放入 Git。
- 回滚时恢复上一版 Compose 与匹配的密钥配置，重启相关容器并验证 `/health/ready`、RAG 检索、MinIO 读写和一次只读报价冒烟。
- 不得通过数据库回退、删除 volume 或重置 Milvus 数据完成本阶段回滚。

## 8. 第一阶段验收状态

已完成：

- 本地与部署机安全预检全部通过；聚焦回归 `93 passed`，Compose 解析和 Python/shell 语法检查通过。
- 报价 MinIO 与 Milvus 对象存储弱凭据已轮换，RAG reload 已 fail-closed。
- 当前内网绑定和主机防火墙已收口；临时 SSH 授权、本机临时私钥、远端明文暂存和回滚目录中的旧 `.env` 已删除。
- 公网模式下试验路由不挂载的自动化测试通过。
- 本次备份不含明文 `.env`，内容和哈希可验证。
- 当前 `/health/ready`、数据库、Celery、RAG 检索和 MinIO 认证通过。
- 旧钉钉机器人已删除，新机器人加签配置与真实测试发送通过。
- MySQL 运行/迁移账号已拆分，强密码、来源主机限制、`REQUIRE SSL` 和 CA 校验已启用；旧弱密码账号已删除。服务重启后 `/health/ready=ready`，数据库、Celery broker/worker 正常，Alembic 为 `20260801_0081 (head)`；MySQL 专项安全回归 `43 passed`。

进入公网网关/WAF 与正式生产部署前仍必须完成：

- 建立云安全组和 HTTPS 反向代理/WAF，只向公网暴露 `443`；不得直接开放 `80/9000/8001/5678/5455/6380/9002/9003/19530/9091`。
- 迁云时为 MySQL 配置带私网 DNS/IP SAN 的受控证书并启用客户端身份校验；盘点同容器所有内部账号均支持 TLS 后，再评估开启全局 `require_secure_transport=ON`。
- 从真实非白名单外部网络验证受保护端口被拒绝，并完成公网 HTTPS、限流、上传大小和认证回归。
- 建立可验证的加密异机/离线备份，并演练恢复。
- 完成内网登录、只读报价链路以及迁云后的完整业务冒烟。当前 443 没有接收服务，严禁直接宣告公网可用。

## 9. 不属于本阶段的后续项

HTTPS 证书/WAF/限流、统一反向代理、Redis ACL/TLS、管理员 MFA、集中日志审计、依赖/镜像/主机漏洞扫描和系统基线加固属于后续阶段。基础日志脱敏已完成，但不能替代集中审计和凭据撤销。本阶段的网络收口和 fail-closed 改动是这些工作的前置条件，不能替代后续建设。
