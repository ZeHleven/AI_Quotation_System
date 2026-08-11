# RAGFlow / Elasticsearch 可回滚停机观察记录

日期：2026-08-06
状态：`QUARANTINE_ACTIVE`

## 1. 目的和边界

确认不参与正式业务链路的 RAGFlow 应用与其 Elasticsearch 后端能否从后续云迁移范围中排除。

本次只停止以下两个精确容器，不删除容器、镜像、卷或数据，也不修改 Compose、restart policy、防火墙或公网规则：

- `ragflow-ragflow-cpu-1`
- `ragflow-es01-1`

以下正式服务不在变更范围内：

- `ragflow-mysql-1`（正式业务 MySQL，必须保留）
- `n8n`
- Dify API/Worker
- `rag-api-service`
- `milvus-standalone`、Milvus etcd/MinIO
- `quote-redis`、`quote-minio`
- ECS API/Worker、Nginx、IPsec 与监控

## 2. 依赖审计结论

正式报价链路为：

`ECS API/Worker -> N8N no-RAG 工作流 -> Dify「预算自动化测算核心」-> LLM`

- N8N 正式 no-RAG 工作流 `jiXOrZ7NZgl2Megd` 不包含 RAGFlow、Elasticsearch 或 `/api/v1/retrieve`。
- 正式 Dify 应用“预算自动化测算核心”的当前图只有 start、LLM、end 三类节点，不包含 RAGFlow/Elasticsearch。
- 2026-07-25 的 7 次 Dify 运行与 N8N no-RAG 执行时间对应。
- 独立历史应用 `dify--ragflow` 最后运行于 2026-03-26；它调用 RAGFlow，但不属于当前正式报价链路。
- 停机前没有发现到 RAGFlow/Elasticsearch 监听端口的活动 TCP 连接。

## 3. 变更与备份

停机开始时间：2026-08-06 14:16 CST。

root-only 回滚证据目录：

`/root/ai-middle-office-ragflow-quarantine-backups/20260806T061618Z`

- 目录权限：`700 root:root`
- 文件权限：`600 root:root`
- 包含停止前后容器清单、两个目标容器的 inspect 元数据、Compose 配置副本、内存状态和停机状态记录。
- `docker-compose.yml.sha256` 校验通过。
- inspect 元数据可能包含运行环境信息，只允许 root 读取，不得复制到聊天、仓库或普通用户目录。

停止结果：

- `ragflow-ragflow-cpu-1`: `exited`, restart policy `unless-stopped`
- `ragflow-es01-1`: `exited`, restart policy `unless-stopped`

## 4. 停机后门禁

即时门禁全部通过：

- MySQL：running + healthy；只读查询成功
- N8N：HTTP 200
- Dify API：HTTP 200；Dify Worker running
- RAG `/api/v1/retrieve`：HTTP 200
- Milvus：running
- Quote Redis/MinIO：healthy
- ECS `/health/ready`：HTTP 200
- 本机与公网 HTTPS：HTTP 307，TLS 校验成功
- ECS Nginx、firewalld、Docker、IPsec、监控 timer：active
- 停机后首轮自动监控：2026-08-06 14:19:04 CST，`Result=success / ExecMainStatus=0`

CentOS 可用内存由停机前约 `2.7 GiB` 提升到约 `10 GiB`，停止后系统内存使用约 `4.8 GiB`。

## 5. 观察和回滚

观察期：至少 24 小时，建议 48 小时。期间继续依赖现有 5 分钟监控与钉钉故障/恢复告警，并人工验证真实登录、报价预审、确认下发、成本库、文件上传和管理员页面。

任一正式业务异常被证实与本次停机有关时，在 CentOS root 终端执行：

```bash
docker start ragflow-es01-1 ragflow-ragflow-cpu-1
```

回滚后必须复验 MySQL、N8N、Dify、RAG/Milvus、ECS `/health/ready` 和 HTTPS。

观察期结束且无回归后，可把这两个容器排除出正式云迁移范围。`ragflow-redis-1` 与 `ragflow-minio-1` 是否一并归档需要单独审计；`ragflow-mysql-1` 不得因名称相似而停用或删除。
