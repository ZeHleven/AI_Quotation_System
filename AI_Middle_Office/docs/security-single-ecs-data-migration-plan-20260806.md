# 单 ECS 全栈迁移执行方案

日期：2026-08-06
状态：`CAPACITY_GO_WITH_GUARDRAILS`
范围：把本地 CentOS 上仍参与正式业务的 MySQL、Redis、N8N、Dify、RAG/Milvus、MinIO 迁移到当前公网应用 ECS；公网继续只开放 443。

## 1. 当前裁决

当前 ECS 可以承载现有小流量全栈，不需要先扩容，但必须按本方案执行资源限制、内部网络隔离、冷备、短维护窗口和回滚门禁。

本裁决不授权立即停止本地 CentOS，不授权删除任何源数据，不授权开放数据库或中间件公网端口。

## 2. 容量证据

### 2.1 当前 ECS

- CPU：4 vCPU。
- 内存：15 GiB，当前可用约 14 GiB，无 Swap。
- 系统盘：79 GiB，已用 6.3 GiB，可用 69 GiB。
- 数据盘：98 GiB，挂载 `/data`，已用 5.0 GiB，可用 92 GiB。
- 应用 API：约 202 MiB。
- 应用 Worker：约 108 MiB。
- Docker 镜像：约 2.4 GiB。

### 2.2 本地 CentOS 正式依赖

- 内存：15 GiB，当前使用 5.4 GiB，可用 9.8 GiB，Swap 2 GiB。
- 当前运行容器合计约 4.6 GiB；扣除待继续审计的 RAGFlow Redis/MinIO 后约 4.35 GiB。
- `/opt/rag_service`：13 GiB。
- `/var/lib/docker`：28 GiB。
- RAG 专用持久卷：约 1.26 GiB；其余空间主要由镜像、Dify、MySQL、N8N、模型缓存和备份占用。
- Docker 命名卷盘点显示 `ragflow_mysql_data` 约 2.0 GiB，另有旧/其他 MySQL 命名卷 `docker_mysql_data` 约 224 MiB；其余已列出的命名卷大多为空或小于 1 MiB。
- 容器 Mounts 已完成逐项盘点：Dify PostgreSQL 约 97 MiB、Plugin Daemon 约 224 MiB、Redis 约 8.4 MiB、Weaviate 约 944 KiB、应用 storage 约 48 KiB；N8N `/root/.n8n` 约 47 MiB；RAG 模型缓存约 2.1 GiB，Milvus/etcd/两个 MinIO/Redis 运行数据合计约 1.26 GiB。
- `milvus-minio` 同时存在 `/minio_data` 绑定目录和一个挂到 `/data` 的空匿名卷；实际 Compose 使用 `/minio_data`，迁移只复制 `/opt/rag_service/volumes/minio`，不复制空匿名卷。
- 当前需要冷迁移的业务持久状态约 5–6 GiB。`/var/lib/docker` 的其余占用主要是镜像和可重建容器层，目标 ECS 必须重新导入固定镜像，不得整体复制源端 `/var/lib/docker`。

按现有实际使用量合并后，目标 ECS 稳态预计使用约 5.5–8 GiB 内存；RAG 重建、Dify Worker 和报价并发期间可能更高。目标数据盘只接收业务状态、模型缓存和迁移暂存，初始预计新增约 6–12 GiB；Docker 镜像和容器层留在仍有 69 GiB 可用空间的系统盘，不迁移旧 Docker 根目录。

## 3. 迁移范围

必须迁移：

- 正式业务 MySQL；源容器当前名为 `ragflow-mysql-1`，目标必须改为独立业务 MySQL，不能继续依赖 RAGFlow Compose 生命周期。
- `quote-redis`、`quote-minio`。
- `milvus-standalone`、`milvus-etcd`、`milvus-minio`。
- `rag-api-service` 及其离线模型缓存、索引密钥运行挂载。
- Dify API、Worker、Beat、Plugin Daemon、PostgreSQL、Redis、Weaviate、Sandbox、SSRF Proxy、Web/Nginx 及其持久数据。
- N8N 及其工作流、凭据加密配置和持久数据。

暂不迁移：

- `ragflow-ragflow-cpu-1`。
- `ragflow-es01-1`。

待只读依赖审计后决定：

- `ragflow-redis-1`。
- `ragflow-minio-1`。

上述四项不能因名称相似影响正式业务 MySQL 的迁移。

## 4. 目标 ECS 布局

持久化根目录统一为：

```text
/data/ai-middle-office/
├── mysql/
├── redis/
├── n8n/
├── dify/
├── milvus/
├── etcd/
├── milvus-minio/
├── quote-minio/
├── model-cache/
├── runtime-secrets/
└── migration-staging/
```

约束：

- `/data/ai-middle-office` 及秘密目录由 root 管理；秘密不进入 Git、聊天、普通日志或 Compose 展开输出。
- 继续保留宿主机 Nginx 作为唯一公网入口，公网安全组只开放 443。
- 新建仅内部使用的 Docker 网络 `ai-middle-office-data-net`。
- MySQL、Redis、N8N、Dify、RAG、Milvus、MinIO 不发布到 ECS 公网地址。
- API/Worker 在切换阶段同时连接现有应用网络和新数据网络；验证完成后再移除对本地 IPsec 后端的依赖。
- 不在迁移前清理 ECS 现有镜像；只有在完整回滚窗口结束后才评估可回收镜像。

## 5. 资源保护

- 当前 4 vCPU / 15 GiB 属可运行但余量有限配置；所有服务必须设置 CPU、内存和日志轮转限制。
- 迁移前在 ECS 配置约 4 GiB 低优先级应急 Swap，并设置低 `swappiness`；Swap 只用于降低突发 OOM 风险，不作为日常容量。
- RAG/Milvus 重建、Dify 批任务和大批量报价不得同时执行。
- 监控增加宿主机内存、Swap、系统盘、数据盘、容器重启和 OOM 检查。
- 数据盘使用达到 70% 告警；80% 前扩容。

## 6. 执行阶段

### 阶段 A：只读依赖与挂载审计

1. 记录所有源容器挂载、网络、镜像 digest、restart policy 和持久卷大小，不读取或输出环境变量值。
2. 确认 N8N 的实际持久目录和加密密钥来源。
3. 确认 Dify PostgreSQL、Redis、Weaviate、Plugin Daemon 与应用 storage 的全部持久目录。
4. 确认 `ragflow-redis-1` / `ragflow-minio-1` 无正式业务连接后再排除。

已完成的挂载结论：

- MySQL：`ragflow_mysql_data -> /var/lib/mysql`。
- Dify：`/opt/dify/docker/volumes/{app,plugin_daemon,redis,sandbox,db,weaviate}`。
- N8N：`/root/.n8n -> /home/node/.n8n`。
- RAG/Milvus/Redis/MinIO：`/opt/rag_service/model_cache` 与 `/opt/rag_service/volumes/*`。
- 已归档源容器 image ID/restart policy、N8N 非秘密运行参数和 Dify 1.13.2 原始未展开 Compose；注册表 digest 仍需在镜像装载门禁中核对。不得保存会展开秘密的 `docker compose config` 输出。

### 阶段 B：迁移前备份

1. 复用 2026-08-06 已验证的 CentOS 冷备流程。
2. 补充 Dify PostgreSQL、Weaviate、应用 storage、Plugin Daemon 和 N8N 持久数据备份。
3. 备份 Compose 配置，但排除运行 `.env`；秘密从独立托管恢复。
4. 对备份制作 SHA-256 清单，并在目标 ECS 做解包/内容检查。
5. 备份完成前不得执行数据库迁移、源服务停止或目标服务写入。

### 阶段 C：目标 ECS 暗部署

1. 创建数据目录、内部网络和 root-only 运行配置。
2. 导入或拉取固定 digest 镜像。
3. 先恢复 MySQL、Dify PostgreSQL、MinIO、Milvus/etcd、Redis、N8N 和 Dify 持久数据。
4. 服务只在内部网络启动；应用 API/Worker 仍指向本地 CentOS。
5. 完成数据库版本、对象数量、Bucket、Milvus collection、N8N workflow 和 Dify 应用只读核对。

### 阶段 D：受控切换

1. 安排短维护窗口，暂停新报价、文件上传、成本库批量变更和 RAG 同步。
2. 确认 Celery 无 active/reserved/scheduled 任务。
3. 停止源端写服务并制作最终一致性备份；源端保持停止，防止双写。
4. 在目标 ECS 恢复最终增量，修改 API/Worker 运行配置为内部服务名。
5. 受控重启 API/Worker，依次验证 readiness、登录/RBAC、报价预审、文件上传、成本库、N8N、Dify、RAG/Milvus、MinIO 和确认下发。
6. 公网再次验证只开放 443，敏感路由与后端端口继续阻断。

### 阶段 E：观察与退役

1. 保持本地 CentOS 开机但源写服务停止，观察至少 48 小时。
2. 观察期内保留 IPsec、源数据、备份和回滚脚本。
3. 观察期通过后再停用 IPsec、移除旧后端地址和本地启动依赖。
4. 本地数据只在新的异机备份与恢复演练均通过后归档；不得直接删除虚拟机或卷。

## 7. 回滚原则

- 切换前失败：停止目标暗部署，线上继续使用本地 CentOS，无业务切换。
- 切换后失败：先冻结目标写入并保存目标侧增量，再把 API/Worker 配置恢复为本地 IPsec 地址，启动源服务并复验。
- 不允许目标和源 MySQL、N8N、MinIO、Redis 同时接受正式写入。
- 回滚不得直接覆盖任一侧数据；每次恢复前必须保留故障现场备份和哈希清单。

## 8. 下一门禁

进入备份与暗部署前，以下证据已取得：

- 源镜像 tag/image ID 和 restart policy 清单；注册表 digest 在目标镜像装载时再核对。
- Dify 1.13.2 原始未展开 Compose 文件，未附带 `.env`。
- N8N 的镜像 ID、命令、端口、网络模式和 restart policy，以及 `/root/.n8n` 持久目录。
- Dify `.env`、Compose 和各持久目录的准确位置；秘密值只允许由 root-only 冷备包传输，不复制到工作区或聊天。

仍需在源端只读预检中确认：

- N8N workflow 是否仍硬编码 `192.168.88.128` 或其他本地地址。
- `ragflow-redis-1` / `ragflow-minio-1` 的连接与依赖审计结果。
