# 当前开发冲刺任务路线

> 生成时间：2026-05-01
> 项目：AI 智能报价中台（Clear_test / AI_Middle_Office）
> 本文件供 Claude Code 读取，作为下一步开发的完整上下文

---

## 一、P0 安全止血（✅ 已完成，2026-05-01）

### ✅ 已完成
- `CLAUDE.md` 和 `AI_Middle_Office/CLAUDE.md` 中的明文密钥替换为占位符
- `n8n_import.json` 和 `n8n_workflows_backup.json` 解除 Git 追踪，加入 `.gitignore`
- `git commit + push` 已推送到 GitHub（commit: `d8508f1`）
- `AI_Middle_Office/.env` 密钥已轮换：
  - `WEBHOOK_SECRET`：旧值已废弃，新值已写入 `.env`
  - `RELOAD_SECRET`：旧值已废弃，新值已写入 `.env`
- CentOS `docker-compose.yml` 中的 `RELOAD_SECRET` 已更新，`rag-api-service` 已用新密钥重启
- n8n 端 `budget-push Code in JavaScript` 节点已确认无明文密钥，无需修改
- 完整报价流程端到端测试通过（GLM-4V → N8N → RAG → Dify → 钉钉推送）

---

## 二、P1 消除 n8n 黑盒（✅ 已完成，2026-05-01）

**目标**：把 n8n 工作流纳入 Git 版本管理，消除当前唯一的黑盒风险。

### ✅ 已完成
- 从 n8n API 导出 `【新】1-智能预审流`（budget-calc）和 `【新】2-制表推钉钉流`（budget-push）
- 脱敏处理：去除 credential ID、DingTalk appkey/appsecret/access_token、Dify API key、webhookId、openConversationId、n8n 用户/项目 ID
- 保存为 `n8n_workflows/budget_calc.json` 和 `n8n_workflows/budget_push.json`
- 创建 `n8n_workflows/README.md`：含脱敏规则说明、凭据恢复指南、SHA-256 hash 记录

**注意**：
- `n8n_import.json` 和 `n8n_workflows_backup.json` 已从 Git 移除（含旧密钥），不要恢复追踪
- 新的工作流文件夹 `n8n_workflows/` 只保存脱敏后的版本
- n8n API Key 由用户提供，不写入任何文件

---

## 三、P2 报价一致性治理（✅ 已完成，2026-05-02）

**目标**：每次报价形成完整可追溯链路，出现"结果和历史不一致"时能精准定位原因。

### ✅ 已完成
- `admin.html`：报价任务队列管理面板新增"详情"按钮
- 详情弹窗展示：处理事件流（含 RAG 检索上下文）、AI 报价结果（result_json）、完整消息
- Codex QuoteJob 已有 `events_json` / `result_json` 字段贯穿全链路，无需新增表结构
- 端到端验证通过：提交报价 → 查看详情 → 事件流和价格均正确

**诊断方法**：admin.html → 报价任务队列管理 → 清空状态筛选 → 点"详情" → 对比两次任务的事件流即可定位价格差异根因

---

## 四、P3 主动运维告警（✅ 已完成）

**目标**：把现有被动监控面板改为主动钉钉推送告警。

**已有基础**：
- `AI_Middle_Office/app/services/ops_monitor.py`：服务探活、卡住任务、异常日志聚合已实现
- `AI_Middle_Office/app/api/v1/ops.py`：`/api/v1/admin/ops/dashboard` 接口已实现

**已完成**：
- 告警触发条件：worker 离线、RAG degraded、MySQL/Redis 断连、任务卡住超阈值、连续错误日志
- 钉钉 Webhook 推送（复用现有钉钉推送逻辑）
- 去重机制：同一问题 30 分钟内只发一次
- 限流机制：任意 5 分钟内最多发 3 条告警

**涉及文件**：
- `AI_Middle_Office/app/services/ops_monitor.py`：新增告警推送逻辑
- `AI_Middle_Office/app/core/config.py`：新增告警钉钉 Webhook 配置项
- `AI_Middle_Office/.env.example`：补充 `ALERT_DINGTALK_WEBHOOK` 配置示例

---

## 五、P4 知识库发布流程增强（✅ 已完成，2026-05-02）

**目标**：知识库变更后自动验证质量，防止错误发布降低 RAG 准确率。

**已完成**：
- 热更新至 Milvus 成功后自动触发 RAG 检索评测（后台线程，不阻塞操作）
- 评测结果持久化至 `rag_eval_reports` 表，含 Hit@K、MRR、各难度级别分解
- 指标低于阈值（Hit@K < 0.70 或 MRR < 0.50）时在知识库面板显示橙色警告
- `admin.html` 知识库面板内嵌评测结果区，热更新后自动轮询至完成
- 历史评测记录可通过 `GET /api/v1/admin/rag_eval/history` 查询

---

## 六、F1 前端进度步骤动态适配（✅ 已完成）

**目标**：进度步骤根据输入类型动态显示，避免纯文字报价时出现无意义的"图像识别"步骤。

**问题描述**：当前 `STAGES` 为硬编码三步，无论输入图片还是文字，始终显示 `图像识别 → 知识库检索 → 生成报价`，误导用户。

**方案**：

| 输入类型 | 步骤显示 |
|----------|----------|
| 图片上传 | `🖼 图像识别 → 📚 知识库检索 → ✏️ 生成报价` |
| 纯文字输入 | `📝 需求解析 → 📚 知识库检索 → ✏️ 生成报价` |

**涉及文件**：`index.html`

**改动范围（仅前端）**：
1. 定义两套常量 `STAGES_WITH_IMAGE` 和 `STAGES_TEXT_ONLY`
2. 将 `stages` 从常量改为 `ref`
3. `sendMessage` 时根据 `selectedFile` 是否为图片赋值 `stages.value`
4. 后端 SSE 结构和 `currentStage` 计数逻辑不变

---

## 七、企业基线四步升级（✅ 全部完成）

> 目标：以最小改动量达到可在受控内网稳定运行的企业基线。  
> 四步均互相独立，可并行或分批推进；建议按顺序执行以降低风险。

---

### ✅ E1. SQLite → MySQL 数据库迁移（已完成）

**目标**：消除多进程写冲突风险，为后续扩容和备份打好基础。

**已有基础**
- `requirements.txt` 已包含 `pymysql`
- `core/config.py` 读取 `DATABASE_URL`，支持任意 SQLAlchemy 连接串
- Alembic 已配置，`20260428_0001_initial_schema` 覆盖所有表
- `create_admin.py` 可在新库重建 admin 账号

**实施步骤**

1. **在 CentOS MySQL 中建库建用户**（在 CentOS 上执行）
   ```bash
   docker exec -it <n8n-mysql-container> mysql -uroot -p
   ```
   ```sql
   CREATE DATABASE ai_quotation CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'ai_app'@'%' IDENTIFIED BY '<strong-password>';
   GRANT ALL PRIVILEGES ON ai_quotation.* TO 'ai_app'@'%';
   FLUSH PRIVILEGES;
   ```
   > MySQL 容器名和 root 密码见 CentOS `docker-compose.yml`。

2. **更新 Windows `.env`**
   ```
   DATABASE_URL=mysql+pymysql://ai_app:<strong-password>@192.168.88.128:5455/ai_quotation
   AUTO_CREATE_TABLES=false
   STARTUP_COMPAT_MIGRATIONS=false
   ```
   > 切换后由 Alembic 全权管理表结构，关闭旧的自建表逻辑。

3. **执行迁移、重建 admin**
   ```powershell
   cd AI_Middle_Office
   C:\Users\12521\miniconda3\python.exe -m alembic upgrade head
   C:\Users\12521\miniconda3\python.exe create_admin.py
   ```

4. **重启服务并验证**
   - `GET /health/ready` → `database: ok`
   - 登录 admin，确认用户表、任务队列正常

**注意**：现有 SQLite 数据（用户配额、历史记录）不会自动迁移。若需保留，实施前告知 Claude Code 编写数据迁移脚本。

**预计工时**：1 天（含 CentOS MySQL 确认和端到端验证）

---

### ✅ E2. HTTPS 接入（Caddy 反向代理，Windows 端）（已完成）

**目标**：为 LAN 内所有用户提供加密访问，避免 JWT Token 和报价数据明文传输。

**选型说明**：选用 Caddy 而非 nginx，原因是单二进制文件、内置自动 TLS（含自签证书）、Windows 服务注册一条命令。

**实施步骤**

1. **下载 Caddy**
   ```powershell
   # 从 https://caddyserver.com/download 下载 Windows AMD64 版本
   # 放到 C:\caddy\caddy.exe
   ```

2. **创建 `C:\caddy\Caddyfile`**
   ```
   {
     local_certs
     auto_https disable_redirects
   }

   :443 {
     tls internal
     reverse_proxy localhost:9000
   }

   :80 {
     redir https://{host}{uri} permanent
   }
   ```
   > `tls internal` 让 Caddy 生成本地自签 CA，自动续签，无需手动管理证书。

3. **将 Caddy 自签 CA 加入系统信任（Windows）**
   ```powershell
   C:\caddy\caddy.exe trust
   ```
   > 每台访问该系统的员工电脑也需执行此命令一次，或由管理员通过域策略统一推送。

4. **注册为 Windows 服务**
   ```powershell
   C:\caddy\caddy.exe service install --config C:\caddy\Caddyfile
   Start-Service CaddyServer
   ```

5. **收紧 FastAPI CORS**（修改 `.env`）
   ```
   CORS_ALLOW_ORIGINS=https://<本机IP或内网域名>
   ```
   > 从 `*` 改为实际访问来源，防止跨域滥用。

6. **更新 `start_all.ps1`**：在 FastAPI 启动前确认 Caddy 已运行。

**访问方式变更**：`http://localhost:9000/` → `https://<局域网IP>/`

**预计工时**：半天（含证书信任推送）

---

### ✅ E3. 定时自动备份（PowerShell + 任务计划程序）（已完成）

**目标**：保障 MySQL 数据、知识库、MinIO 附件每日可恢复，保留 7 天滚动窗口。

**备份范围**

| 数据 | 方式 | 目标路径 |
|------|------|---------|
| MySQL `ai_quotation` | `docker exec mysqldump` | `backups/db/` |
| `rag_materials.json` + `materials_audit/` | robocopy | `backups/rag/` |
| MinIO `quote-files` bucket | `mc mirror`（可选） | `backups/minio/` |
| `.env` 加密副本 | AES 加密存储 | `backups/env/` |

**新增文件**：`AI_Middle_Office/backup_all.ps1`

```powershell
# 核心逻辑（Claude Code 实施时补全细节）
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dest = "C:\AI_Backups\$timestamp"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# 1. MySQL dump
docker -H tcp://192.168.88.128:2375 exec <mysql-container> `
  mysqldump -uai_app -p<password> ai_quotation > "$dest\ai_quotation.sql"

# 2. 知识库
robocopy "$PSScriptRoot\..\" "$dest\rag" rag_materials.json /E
robocopy "$PSScriptRoot\..\materials_audit" "$dest\rag\materials_audit" /E

# 3. 清理 7 天前的备份
Get-ChildItem "C:\AI_Backups" -Directory |
  Where-Object { $_.CreationTime -lt (Get-Date).AddDays(-7) } |
  Remove-Item -Recurse -Force

Write-Output "[$timestamp] 备份完成 → $dest"
```

**注册定时任务**（每天 03:00 执行）
```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\...\backup_all.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
Register-ScheduledTask -TaskName "AI_MiddleOffice_Backup" `
  -Action $action -Trigger $trigger -RunLevel Highest -Force
```

**验证方法**：手动运行脚本，检查 `C:\AI_Backups\` 目录，在测试 MySQL 实例中执行 `source ai_quotation.sql` 确认可恢复。

**预计工时**：1 天（含 docker exec 权限确认和恢复演练）

---

### ✅ E4. 登录接口限流（已完成）

**目标**：防止暴力破解 admin 密码，5 分钟内同一 IP 超过 10 次登录失败后锁定。

**选型**：使用 `slowapi`（FastAPI 官方推荐的限流中间件），已有 Redis 可用作分布式存储。

**实施步骤**

1. **新增依赖**（`requirements.txt`）
   ```
   slowapi
   ```

2. **修改 `app/main.py`**：注册限流器
   ```python
   from slowapi import Limiter, _rate_limit_exceeded_handler
   from slowapi.util import get_remote_address
   from slowapi.errors import RateLimitExceeded

   limiter = Limiter(key_func=get_remote_address,
                     storage_uri=settings.celery_broker_url)  # 复用 Redis
   app.state.limiter = limiter
   app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
   ```

3. **修改 `app/api/v1/auth.py`**：在登录接口加装饰器
   ```python
   from app.main import limiter

   @router.post("/login")
   @limiter.limit("10/5minutes")   # 每 IP 每 5 分钟最多 10 次
   async def login(request: Request, ...):
       ...
   ```

4. **新增配置项**（`core/config.py` + `.env.example`）
   ```
   LOGIN_RATE_LIMIT=10/5minutes
   ```
   > 便于不重启代码调整阈值。

5. **验证方法**：用脚本连续发送 11 次错误登录，第 11 次应返回 `HTTP 429 Too Many Requests`。

**注意**：`TASK_QUEUE_MODE=local` 时 Redis 不一定在线，代码中需做 fallback：Redis 不可用时降级为内存存储（`slowapi` 默认支持）。

**预计工时**：半天

---

### 四步完成后的检查清单

```
[✅] /health/ready → database: mysql, ok
[✅] https://localhost 可访问（本机）
[✅] C:\AI_Backups\ 有备份，手动验证通过
[✅] 连续 11 次错误登录返回 429（slowapi 已集成）
[✅] CORS_ALLOW_ORIGINS 已收紧（https://localhost,https://127.0.0.1,https://192.168.1.21,http://localhost:9000）
[✅] JWT_SECRET_KEY 已是随机强密码
```

---

## 六、协作规范（Claude Code 必读）

- **破坏性操作**（删文件、清库、强制推送等）必须先向用户申请许可，得到明确授权后再执行
- **数据库表结构变更**必须通过 `alembic/versions/` 新增 revision，禁止手动堆到 `main.py`
- **`.env` 文件**不能提交到 Git，不能在任何文档文件中写入真实密钥值
- **Git 操作**（commit、push）由用户手动执行，Claude Code 只负责修改文件
- 每次修改前告知改哪个文件、改什么、为什么改
- GitHub 仓库已设为**私有**：`https://github.com/ZeHleven/AI_Quotation_System.git`
