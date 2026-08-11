# AI 智能报价中台安全上云第五阶段验收记录（2026-08-05）

## 1. 结论

安全上云第五阶段“ECS 内部 API/Worker 启动与业务冒烟”已完成，状态为
`passed`。

本结论只授权继续进行第六阶段 HTTPS 入口的准备与受控验证，不代表系统已
开放公网或已达到生产上线条件：

- `PUBLIC_ACCESS_ENABLED=false`；
- Nginx 仍为 `inactive`；
- API 仅监听 `127.0.0.1:9000`，ECS 私网地址 `172.18.138.198:9000`
  不可连接；
- 未开放公网业务端口，未启动 HTTPS/WAF；
- 自动建表、启动兼容迁移、自动 Alembic 迁移和 ECS 日审调度均保持关闭；
- SELinux 仍为 `permissive`，属于后续上线门未完成项。

## 2. 最终应用镜像

原候选镜像在首次运行时发现发布边界漏装运行时需要的 `mcp_servers` 包。修复
`.dockerignore` 和 Dockerfile 后重新构建、导入检查并扫描，最终运行镜像为：

- 镜像：`ai-middle-office-app:20260805_161737`
- 镜像 ID：
  `sha256:627e0cb17a1ba09019749149735bb4e8c35d32ab7f0a73f6efda10b75c6ed91e`
- 运行用户：`10001:10001`
- 源码发布包 SHA-256：
  `568497e21d88b9ddd552d0b5084b1a256d869a822fa1c65e5d4327b8951d4521`
- 导出镜像 tar SHA-256：
  `3a33b63198d1f2c980ac7d21b46f49f471ff2e457f8aba1ad5c4ddb5ae430b92`
- Compose SHA-256：
  `3e0fea7e3486c8593f1ba56830e11664f42ca5c480e345bb609d58c4a1e93aa2`

最终 Trivy `0.72.0` 使用固定 scanner digest 和独立数据库快照，对镜像 tar
执行无网络漏洞/秘密扫描并生成 CycloneDX SBOM：

- 扫描目标：`2`
- 漏洞：`0`
- 秘密命中：`0`
- SBOM 组件：`109`
- 镜像元数据 SHA-256：
  `1e17e8304d1cc7edbcd89b9696c06bdf9d142b5987ca45a8d53274dbef9b2f20`
- Trivy JSON SHA-256：
  `16d262af08ef0ee78f178953b44829e462ab892a1b27d21eb6d57c4a8bde3a25`
- CycloneDX SBOM SHA-256：
  `a1bf79b0ac5c1b2e3b3625a980a21ca93d37b391d600bd777283ac140a25da72`
- Trivy DB metadata SHA-256：
  `e90a901b39dd64d3e50ed68bd0475f764f428cbe905c7d085611b7feb60ca2ec`
- Trivy DB SHA-256：
  `098160a36a49825989724844beabcb6e1f5b37884cce8369dbac5d7d41fa51b9`

扫描数据库在离线扫描前后哈希一致。零命中只表示该 scanner 和数据库快照对
该精确镜像没有识别到漏洞或秘密，并不替代应用逻辑审计。

## 3. 暗部署运行边界

ECS 最终运行拓扑：

| 组件 | 容器地址 | 宿主机发布 | 运行状态 |
|---|---|---|---|
| API | `10.240.10.10` | `127.0.0.1:9000` | healthy |
| Worker | `10.240.10.11` | 无端口 | running |

API 和 Worker 均已验证：

- 精确使用最终镜像 ID；
- 非 root `10001:10001`；
- 根文件系统只读；
- `cap_drop=ALL`；
- `no-new-privileges=true`；
- 未启用 privileged；
- 只连接外部网络 `ai-middle-office-app-net`；
- MySQL CA 只读挂载；
- 固定地址分别为 `.10/.11`；
- restart policy 为 `unless-stopped`。

启动前完整私网门禁再次通过：MySQL TLS、Redis、RAG、N8N、MinIO API
正常，MinIO Console `9001` 与 Milvus `19530` 继续阻断。MySQL 运行连接使用
`ai_runtime` 精确容器来源账号、CA 证书链校验和 `20260801_0081` schema；
没有向运行容器提供 migrator URL。

## 4. 业务与恢复冒烟

### 4.1 认证只读冒烟

脚本只在 API 容器内存中签发短期内部令牌，不读取或输出账号密码、JWT 密钥
或令牌内容。受控重启前后各执行一次，结果均通过：

- 当前用户与管理员 RBAC；
- 报价历史分页；
- 异步报价任务分页；
- 文件索引分页；
- 成本库分页；
- 报价 Agent capabilities；
- MinIO 认证健康检查；
- MySQL、Redis、Celery、RAG、MinIO、N8N 联合服务探测；
- 真实 RAG `/api/v1/retrieve` 检索并返回非空证据。

匿名边界同时验证：

- `/health/live` 与 `/health/ready` 为 200/ready；
- 自助注册返回 403；
- 未认证访问报价历史返回 401；
- `172.18.138.198:9000` 阻断；
- `127.0.0.1:9000` 可达。

API/Worker 完成一次受控 Compose restart；API 在第 2 轮检查恢复 healthy，
Worker 恢复 running，ECS Worker 定向 Celery ping、容器安全边界、认证只读
冒烟和匿名边界均再次通过。

### 4.2 真实异步报价闭环

在确认默认队列无 active/reserved/scheduled/queued 任务后，临时让旧 Windows
Worker 停止消费默认 `celery` 队列，仅保留 ECS Worker 消费；任务结束后旧
Worker 已恢复消费。

- 审计标记：`phase5_cloud_smoke`
- Quote job：`7023d2d1-290c-4393-8ed9-d1150e0551d6`
- 执行 Worker：ECS Worker（默认队列独占期间）
- 状态：`queued -> succeeded/completed`
- 预审条目：`1`
- duration：已记录
- 额度：准确扣减 `1`
- 确认下发：未执行
- 钉钉推送：未执行
- 旧 Windows Worker：已恢复默认队列
- 冒烟后 `/health/ready`：ready

该记录有意保留在业务库中作为迁云审计证据，不执行直接数据库删除。

## 5. 备份、回滚与证据

成功部署前备份：

- Compose：
  `/home/aiadmin/ai-phase5-backups/pre-compose-20260805_161737-20260805_170942`
- app.env/Compose/运行前状态：
  `/home/aiadmin/ai-phase5-backups/pre-internal-runtime-start-20260805_170942`

备份目录可能包含 root-only 运行配置副本，不得复制到 Git、聊天、普通日志或
非受控存储。此前两次失败尝试均由事务脚本删除容器并还原 app.env/Compose：
一次发现镜像漏装 `mcp_servers`，一次发现验收脚本错误使用
`HostConfig.NetworkMode` 口径；后者已改为核验实际唯一网络和固定 IP。

主要 ECS 证据及 SHA-256：

| 证据 | SHA-256 |
|---|---|
| `ai-phase5-image-scan-20260805_161737.txt` | `c7f3c9af933fa1e20ea240eff2819a62f790f339a4405ca07ea38d342106f862` |
| `ai-phase5-compose-deploy-and-start-20260805_161737.txt` | `154f744478c6080a03df4ee3b1ee0eddd3a1fd59bb487766290a71cb757dcc47` |
| `ai-phase5-internal-runtime-start-20260805_161737-r2.txt` | `acb61c78f16c7f777d20678c585966aee1f1c0ce064a794af9196d55ecedaebf` |
| `ai-phase5-anonymous-boundary-smoke.txt` | `a4eeb438692b39ce3a54342e905c9db72d219a7b799df4fb7c55112078612c27` |
| `ai-phase5-authenticated-smoke-and-restart-20260805_161737.txt` | `985a085f58e673a89d733563e96180f49409bae1b678e0768a4232ab86b70ff8` |
| `ai-phase5-controlled-quote-smoke-20260805_161737.txt` | `e50579eb92efe7ad409064df2f3f2b58b743a398a19b6ddc70ede3b5db563b9a` |

## 6. 第六阶段准入与未完成项

第五阶段通过后可以开始第六阶段的只读盘点和配置准备，但在以下事项全部通过
前不得启用公网入口：

1. 明确正式域名、备案状态、证书签发/续期方式和 WAF 方案；
2. 备份当前 Nginx/firewalld/安全组配置后，只部署最小 HTTPS 反向代理；
3. 公网仅允许 443，继续禁止直接访问 9000、MySQL、Redis、RAG、N8N、
   MinIO 和 Milvus；
4. 完成 Windows API/Worker 到 ECS 的正式切换与回滚窗口；当前 broker 显示
   `worker_count=2` 是迁移重叠状态，ECS 日审调度已关闭以避免重复调度；
5. 对 HTTPS、认证、上传大小、限流、安全响应头和非白名单阻断执行外部验证；
6. 解决或书面接受 SELinux permissive 风险，并在完整拓扑中验证策略；
7. 完成第七阶段异机加密备份、监控告警、外部扫描和恢复演练后，才能进行
   最终 go/no-go。
