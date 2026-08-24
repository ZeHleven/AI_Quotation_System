# 旗胜投标机会研判 Pure Agent — C08 本地日常入口

## 当前边界

- 入口只允许 `127.0.0.1`、`APP_ENV=local`、`PUBLIC_ACCESS_ENABLED=false` 和显式本地 SQLite；
- 页面开关、Runtime 开关、Continuation Secret、数据库 head、Vite 构建、冻结输入、BCE 版本、官方 DeepSeek 白名单和外部能力禁用项必须同时通过；
- Preflight 只读取文件存在性、模型快照元数据、Python 包版本和 SQLite `alembic_version`，不读取 PDF/SecretEnvFile 内容，不加载模型，不联网；
- Runtime 只有在 Preflight 全部通过后才读取冻结 PDF、冻结企业基线、加载本地 BCE，并安装动态 Action Loop；不接入旧 `bid_intake_*`，不启动 Workflow、Worker、MCP、Milvus、OCR/视觉或 ECS 依赖；
- 启动器不会修改 `app/main.py` 的生产生命周期，也不会自动应用迁移。历史生产迁移包含 SQLite 不支持的约束变更，因此只有显式传入 `-InitializeLocalDatabase` 时，才在同目录临时库中用当前 ORM Metadata 创建隔离 Schema Snapshot、验证 Pure Agent 表、标记 `20260821_0110` 并原子落盘；该机制不用于 ECS 或生产数据库。

## 日常命令

在 `AI_Middle_Office` 目录执行只读检查：

```powershell
.\scripts\start_bid_pure_agent_local.ps1 `
  -SecretEnvFile "C:\Users\12521\.secrets\bid-agent.env" `
  -PreflightOnly
```

首次创建专用本地数据库后仅检查（会写入专用 SQLite，须另行明确授权）：

```powershell
.\scripts\start_bid_pure_agent_local.ps1 `
  -SecretEnvFile "C:\Users\12521\.secrets\bid-agent.env" `
  -InitializeLocalDatabase `
  -PreflightOnly
```

显式启动（会读取冻结资料并加载本地 BCE；须在当次操作前取得模型/真实资料授权）：

```powershell
.\scripts\start_bid_pure_agent_local.ps1 `
  -SecretEnvFile "C:\Users\12521\.secrets\bid-agent.env"
```

页面：`http://127.0.0.1:9018/admin/bid-assessment-pure-agent`

停止：

```powershell
.\scripts\stop_bid_pure_agent_local.ps1
```

启动器/停止器只认 PID 文件与 `127.0.0.1:9018` 的精确监听进程，并要求进程名为 Python；不会按模糊名称终止其他服务。Windows venv 启动器产生包装进程时，启动器会把 PID 文件收敛为真实监听 PID。

## C08-1 验收状态

- 已完成 Preflight 合同/探针、显式 Uvicorn Factory、冻结输入 Runtime 装配、启停脚本、安全 Runtime Status API 和页面禁用态；
- Python/JavaScript/PowerShell 静态检查通过，Vite production build 通过（2241 modules）；
- 授权后 C08-1 确定性专项 `3 passed / 0 failed`，覆盖 Ready、远程/公网/版本偏移拒绝和 Report 防篡改；
- 首次只读 Preflight 发现启动器未注入已验收的 `.tmp/rq2-locked-runtime`，导致 Python 包版本门失败；已改为显式前置冻结依赖目录，并补充 UTF-8 输出环境；
- 修正后只读 Preflight 为 `18/19`：除 `DATABASE_SCHEMA_HEAD` 外全部通过。专用 `.local-pure-agent-daily/runtime.db` 尚未创建，故 `ready=false`、`runtime_install_allowed=false`；检查后确认未创建日常目录、数据库或 Continuation Secret；
- 经授权初始化专用 SQLite 时，历史迁移首先在 `0011` 发现缺少 system admin、随后在 `0012` 暴露 SQLite 不支持 `ALTER ... ADD CONSTRAINT`；未覆盖半成品库，已将其保留为 `runtime.failed-alembic-0012-20260821.db`；
- 新增本地 Schema Snapshot 初始化器：只接受项目内 `.local-pure-agent-daily*/runtime.db`，在临时库创建当前完整 ORM Schema，写入随机不可恢复密码且 `is_active=false` 的迁移所有者，验证关键 Pure Agent 表并标记 `20260821_0110` 后原子发布；已存在且非目标 head 的数据库一律拒绝覆盖；
- 最终 Preflight `19/19` 全部通过，Report Hash 为 `sha256:2527472b76457faa83165854efc26873dee4bd8442a3b769b9d0448042b24642`，`ready=true`、`runtime_install_allowed=true`；只读复核确认 18 张 `bid_pa_*` 表、禁用 admin 及 `admin/system_admin` 迁移角色完整；
- 本轮未启动服务，未读取真实资料正文或 SecretEnvFile 内容，未加载 Embedding，也未调用模型。下一步需单独授权显式启动时读取冻结资料、读取 SecretEnvFile 白名单字段并加载本地 BCE。

## C08-2 验收状态（2026-08-22）

- 已按单次授权执行显式本地启动：读取冻结香港中心 PDF、冻结企业基线和 SecretEnvFile 白名单字段，加载本地 BCE；没有提交问题，没有调用 DeepSeek；
- 首次启动在 Runtime materialization 前因默认 Python 缺少 `slowapi` 而退出。启动器现默认使用项目已隔离的 `.tmp/phase4d2-test-venv`，Preflight 增加 FastAPI/Uvicorn/SlowAPI/SQLAlchemy/Alembic/Bcrypt 应用运行依赖门，缺依赖会在读取资料或加载模型前 fail-closed；
- C08 确定性专项复跑 `3 passed / 0 failed`（2.67 秒），Ruff 和 PowerShell Parser 通过；
- 修正后的 Preflight 为 `20/20`，Report Hash `sha256:1a495184bb01df05b91e1eec0fa4b15cd98aedc99598336dca9e6b653439bd39`，`ready=true`、`runtime_install_allowed=true`；
- 服务完成 Runtime materialization 与 Bootstrap 后在 `127.0.0.1:9018` 就绪；`/health/live` 和管理页均返回 HTTP 200，端口不存在非回环监听；
- Windows 包装进程 PID 已收敛为真实监听 PID。最终只读核验中 PID 文件与监听 PID 均为 `33972`；
- 专用 SQLite 只读核验：`bid_pa_conversations/messages/tasks/actions/calls/events` 均为 `0`，证明本轮未创建 Agent 会话或执行记录，也未产生 Provider 调用；
- 服务保持运行，页面为 `http://127.0.0.1:9018/admin/bid-assessment-pure-agent`。本轮未启用 Reranker、OCR/视觉、MCP、Milvus、ECS 或任何生产依赖。

## C08-3 验收状态（2026-08-22）

- 在专用 SQLite 创建并激活隔离验收用户 `c08_acceptance_20260822`，只分配 `staff` 角色，`must_change_password=false`；没有启用或修改禁用的 bootstrap admin；
- 本机 API 登录成功签发 Token；`/api/v1/auth/me` 返回相同用户、有效 `staff` 角色和 `bid_assessment_pure_agent=available`；
- 鉴权后的 `/api/v1/bid-assessment-pure-agent/runtime-status` 返回 Schema `bid.pure-agent.runtime-status.v1`、`startup_status=ready`、`runtime_available=true` 和安全原因码 `LOCAL_RUNTIME_COMPOSITION_READY`；
- 真实浏览器登录后正确回到 `/admin/bid-assessment-pure-agent`，页面显示验收账号、Pure Agent 导航和“投标机会研判 Agent”；刷新后登录会话与目标页面保持有效；
- 页面没有 Runtime 未就绪/Preflight 阻断告警，也没有浏览器 Console warning/error；空输入时“发送”按钮禁用，输入本地检查文本后启用，清空后恢复禁用；检查文本从未提交；
- 最终只读核验中 `bid_pa_conversations/messages/tasks/actions/calls/events` 仍全部为 `0`，因此未创建对话、消息、Task、Action、Provider Call 或事件，也未调用 DeepSeek；
- 服务继续保持在 `127.0.0.1:9018` 运行。C08-3 只验证鉴权和页面用户态，不授权正式发布、ECS、生产迁移或外部能力。

## C08-4 验收状态（2026-08-22）

- 已按单次授权从页面提交限定问题，范围只包含冻结香港中心 PDF、冻结企业基线、本地 BCE 与官方 DeepSeek；未启用 OCR/视觉、外部 MCP、Milvus、ECS 或生产依赖；
- 首次运行在 Provider 输入最终序列化门发现 Context 超限，同时暴露 Dispatcher 未把未处理异常可靠落为顶层失败状态。Runtime 新增安全失败收束，任何活动 Action 的未处理异常都会结清 Effect/Budget、写入安全 Observation 并进入 `failed`；该中间 Task 后续保留为 `cancelled/v27`；
- Context Profile 预留改为覆盖完整 Provider Schema/Runtime Envelope，使 ContextAssembler 在 96k 最终线前主动压缩。后续一次无效 JSON 运行安全终止为 `failed/v12`，一次回答正文自行包含 Citation Locator 的运行由权威引用护栏连续拒绝并最终安全终止为 `failed/v38`，均未提交回答；
- Provider-visible Answer 合同现明确规定 `block.text` 只写业务内容，不得自行写引用编号、页码定位、URL、文件路径或内部 Ref；模型只选择 `grounding_refs`，最终编号与 Locator 继续由 Runtime `CitationProjector` 权威生成，引用 Guard 没有放宽；
- 最终页面 Task `58a39d92-ca3c-457f-9b31-13a590378740` 在 `completed/v18` 终止：4 次模型决策、3 个 Tool Batch（共 6 个只读 Tool Call）和 1 次 Answer Action 全部成功，无悬挂 Action、错误或预算预留；
- 最终回答为 4 个 Block、3 个受支持 Statement、1 个 `evidence_insufficient` Limitation，绑定 5 个已验证 Grounding Ref 并由 Runtime 投影 5 条 Citation。结论为“不建议立即投标”，两项关键风险为工期延误高额违约责任和投标/履约担保能力未获核验；
- Answer Validation 与 Citation Projection 均 `accepted=true`、Issues 为空；模型原始正文不含引用编号、页码、URL，发布态引用编号及第 12/34 页 Locator 均由 Runtime 生成；Response 已 `committed`，不存在未验证回答抢跑；
- C01 失败终态专项 `2 passed / 0 failed`，C07 Provider Answer Projection 专项 `11 passed / 0 failed`，相关 Ruff、AST 和差异格式检查通过；重启后 Preflight 保持 `20/20`，Report Hash 仍为 `sha256:1a495184bb01df05b91e1eec0fa4b15cd98aedc99598336dca9e6b653439bd39`。

C08 已证明本地日常入口能够从登录、自然语言提交、动态 Action Loop、只读检索与证据升级一直走到受控回答提交和引用展示。该结论仅适用于隔离本地使用，不构成 B07、ECS、生产迁移、发布候选或默认开关启用授权。
