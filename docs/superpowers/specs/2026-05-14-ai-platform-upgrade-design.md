# 旗胜 AI 平台架构升级路线
> 创建日期：2026-05-14
> 最后更新：2026-05-18（Phase 0 开发与当前环境验证完成；Phase 1 当前环境运行态验收完成；Phase 2 当前环境运行态验收完成）
> 状态：Phase 0 当前环境验证完成；Phase 1 报价速度看板当前环境运行态验收已完成；Phase 2 响应速度追踪当前环境运行态验收已完成。系统完善前不正式投入生产使用，正式生产 Runbook 推迟到平台能力完整后统一准备。
> 负责人：待定

---

## 1. 背景与目标

基于旗胜智能装饰“AI 落地方案”战略架构图，当前 03 AI 报价系统已基本完整，需要在现有系统上扩展另外三个模块：01 AI 知识库系统、02 AI 执行系统、04 AI 管理驾驶舱。

所有模块在现有项目内扩展，不新建项目。共用用户体系、MySQL、Redis/Celery、钉钉 Webhook、RAG 服务和现有 FastAPI 网关。

| 模块 | 当前状态 | 路线目标 |
|------|---------|---------|
| 01 AI 知识库系统 | 基础已有，等待历史数据 | 数据到位后批量治理导入 |
| 02 AI 执行系统 | 空白 | 会议纪要 -> 任务拆解 -> 跟踪闭环 |
| 03 AI 报价系统 | 已完整 | 持续优化准确率，并新增样板图入口 |
| 04 AI 管理驾驶舱 | 仅有运维监控 | 效率指标 + 经营指标分阶段覆盖 |

核心价值观：团队效率，“快”是最重要的衡量标准。

| 快的类型 | 定义 | 数据来源 |
|---------|------|---------|
| 报价快 | 客户需求到报价单交付的时长 | QuoteJob / QuoteHistory |
| 响应快 | 客户咨询到有人接单的时长 | ClientInquiry |
| 执行快 | 会议决定到任务落地的时长 | ExecutionTask |

部署边界：当前只服务旗胜一家企业，暂不引入 `tenant_id`。但公司名称、钉钉 Webhook、模型开关、SLA 阈值、经营数据模板等不得硬编码，应放入 `.env` 或后续配置表。系统完善前只按内网开发/验证推进，不正式投入生产使用；正式生产准备在 Phase 0-6 主要能力完成后统一 Runbook 化。

公网边界：系统未来可能公网访问。公网入口启用前必须通过安全门槛，详见 [ADR-RBAC.md](ADR-RBAC.md)。

容量边界：初期约 20 名员工使用，每天报价任务 10 单以内。合同、录音、效果图当前可继续使用 MinIO，后续预留迁移到阿里云 OSS / 腾讯云 COS 的对象存储抽象。

---

## 2. 专题文档索引

本主文档只保留路线、阶段、验收和跨模块决策。细节拆分到以下文档：

| 文档 | 范围 |
|------|------|
| [ADR-RBAC.md](ADR-RBAC.md) | 多角色权限、`role_version`、钉钉二次验证、公网安全、附件权限 |
| [ADR-Business-Dashboard.md](ADR-Business-Dashboard.md) | 项目、合同、回款、成本、增减项、返工成本、毛利率 |
| [ADR-AI-Governance.md](ADR-AI-Governance.md) | AI 数据外发、调用日志、原始输入输出保留、脱敏、Prompt 版本 |
| [ADR-AsyncJob.md](ADR-AsyncJob.md) | `ExecutionTask` 与后台异步任务边界、`quote_jobs.job_type`、未来任务中心 |
| [APPENDIX-Image-to-Quote.md](APPENDIX-Image-to-Quote.md) | 样板图驱动报价、视觉识别、需求表、适配现有报价流程 |
| [RUNBOOK-Phase0-Launch.md](RUNBOOK-Phase0-Launch.md) | 阶段 0 上线步骤、备份、迁移、冒烟、开关、回滚 |
| [API-CONTRACTS.md](API-CONTRACTS.md) | 新增接口契约、错误码、权限、功能开关行为 |
| [STATE-MACHINES.md](STATE-MACHINES.md) | 关键实体状态机、重复调用语义、禁止转换 |
| [FEATURE-FLAGS.md](FEATURE-FLAGS.md) | 功能开关依赖矩阵、Vite 路由阶段、登录后落地页 |

---

## 3. 指标口径

### 全局时间口径

所有业务时间口径统一按中国时区 `Asia/Shanghai` 解释，包括“今日 / 本周 / 本月 / 当天有效 / 逾期 / SLA”。数据库字段优先使用 timezone-aware datetime；如底层仍存 naive datetime，后端必须按 `Asia/Shanghai` 统一转换后再聚合。

看板按自然日、自然周、自然月统计：

- 今日：`Asia/Shanghai` 当天 00:00:00 至 23:59:59
- 本周：周一 00:00:00 至周日 23:59:59
- 本月：自然月第一天 00:00:00 至最后一天 23:59:59

`trace_id`、审计时间、AI 调用时间、Celery 任务时间可同时保留 UTC 时间戳，但展示给业务用户时统一显示为中国时区。

### 报价耗时

| 指标名 | 计算方式 | 面向受众 |
|--------|---------|---------|
| AI 生成耗时 | `QuoteJob.duration_ms`；新任务由 worker 基于单次运行实测耗时写入，必要时回退 `QuoteJob.created_at -> QuoteJob.finished_at` | 工程侧 |
| 人工确认耗时 | `QuoteHistory.created_at - QuoteJob.finished_at`；无 QuoteHistory 不纳入统计；旧数据如存在 `finished_at` UTC/本地时区偏移，展示聚合层做只读校正 | 管理层 |
| 总交付耗时 | `QuoteHistory.created_at - QuoteJob.created_at`；无 QuoteHistory 不纳入统计 | 管理层 |

三个数字分区展示，不合并平均。

### 响应耗时

首次响应时长 = `ClientInquiry.first_response_time - ClientInquiry.inquiry_time`。

统计范围仅限已经进入报价流程并生成 QuoteJob 的客户咨询。未形成报价的零散咨询当前不纳入响应速度指标，也不建设独立线索模块。

初期 SLA 统一按自然时间计算，不引入工作日、上班时间、节假日。后续如果老板要求按工作时间统计，再新增工作日历和节假日配置，不在 Phase 2 先做复杂排班。

### 执行耗时

任务完成时长 = `ExecutionTask.completed_at - ExecutionTask.created_at`。

逾期判断动态计算：`due_at < now() AND completed_at IS NULL AND status NOT IN ('done','cancelled')`。

### 经营指标

经营口径详见 [ADR-Business-Dashboard.md](ADR-Business-Dashboard.md)。

核心公式：

```text
有效合同金额 = 原合同金额 + 已确认增项 + 已确认签证 - 已确认减项 - 已确认优惠
有效成本 = active 成本汇总，含返工成本
毛利润 = 有效合同金额 - 有效成本
毛利率 = 毛利润 / 有效合同金额
回款率 = 已回款金额 / 有效合同金额
```

回款不参与毛利计算，只用于现金流、回款率和逾期应收。

### 空数据与低可信数据展示

所有驾驶舱页面必须区分以下状态：

- 功能未开启：显示“功能未开启”，不展示空白页
- 无数据：显示“暂无数据，数据从上线后开始统计”
- 样本量过小：显示“样本量较少，仅供参考”
- 低可信数据：例如 `time_source='default'`，只计入数量，不计入平均响应时长

响应速度看板必须同时展示纳入均值的样本量和被排除的默认时间样本量，避免默认时间把平均响应时长压成 0。

### API Schema 优先级

`API-CONTRACTS.md` 当前是接口骨架。进入代码实现前，Phase 0 / 1 / 2 必须先补齐字段级请求 / 响应 Schema，形成 Pydantic 模型和前端类型。优先级如下：

1. Phase 0：`auth/me`、角色授予 / 撤销、权限历史、钉钉验证
2. Phase 1：报价速度看板聚合响应
3. Phase 2：报价创建附带咨询字段、`client_inquiries` 查询 / 修正、响应速度看板聚合响应

Phase 3 以后可在各阶段开工前补齐对应字段级 Schema，不要求在 Phase 0 一次性写完全部后续接口。

---

## 4. 实施路径

```text
第 1-2 周    阶段 0（已完成）+ 阶段 1   Vite 登录鉴权统一 + 报价速度看板
第 3-4 周    阶段 2     响应速度追踪（嵌入报价流程）
第 5-6 周    阶段 3     ExecutionTask 模型 + 执行速度看板
第 7-9 周    阶段 4a    手动会议纪要 + AI 任务草稿确认
第 10-12 周  阶段 4b    语音转写接入
第 13 周+    阶段 4c    钉钉会议导入（等权限到位）
等数据到位   阶段 5     知识库历史数据批量治理导入
数据模板确认后 阶段 6   经营驾驶舱数据底座
并行附录      附录 A     样板图驱动报价
```

节奏原则：先驾驶舱可见，再业务闭环，最后外部集成。

---

## 5. 阶段概要

### 阶段 0｜Vite 登录鉴权统一（✅ 2026-05-18 已完成开发与当前环境验证）

策略：不全量迁移旧页面。先搭 Vite 壳并统一登录、鉴权、路由守卫和 API client。

内部拆分：

- 0a：RBAC、多角色权限管理、登录鉴权统一
- 0b：Vite 壳、SPA fallback、新页面入口

关键决策：

- `users.role` 已存在，仅保留兼容
- 新增 `users.role_version`、`users.dingtalk_user_id`、`users.dingtalk_bound_at`、`user_roles`、`user_role_events`
- 真实权限以 `user_roles` 为准
- 公网访问时 `system_admin` / `admin` 每次登录需钉钉二次验证，当天有效
- Vite 新前端预留 HttpOnly Cookie + CSRF 迁移点

详见 [ADR-RBAC.md](ADR-RBAC.md)。

上线步骤详见 [RUNBOOK-Phase0-Launch.md](RUNBOOK-Phase0-Launch.md)。

完成记录（当前环境验证，非正式生产上线）：

- Alembic 已升级到 `20260514_0011 (head)`。
- 已新增并验证 `users.role_version`、`users.dingtalk_user_id`、`users.dingtalk_bound_at`、`user_roles`、`user_role_events`。
- 已完成 `users.role` 到 `user_roles` 的兼容迁移；`SYSTEM_ADMIN_USERNAME=admin` 已具备 `system_admin + admin`。
- JWT / 当前会话已写入并校验 `role_version`；旧 token 访问 `/api/v1/auth/me` 返回 401。
- `/login`、`/admin/permissions`、`/admin/dashboard`、`/index.html`、`/admin.html`、`/app.html` 当前环境冒烟均返回 200。
- 当前环境 `FEATURE_VITE_FRONTEND=true` 已打开，`PUBLIC_ACCESS_ENABLED=false` 保持关闭。
- 正式生产上线尚未发生，未来需要单独 Runbook。
- Phase 1 报价速度看板未在 Phase 0 启动，已在后续 Phase 1 完成运行态验收；Phase 2 响应追踪已在后续完成代码层交付；Phase 3+ 的执行任务、经营驾驶舱仍未启动。

### 阶段 1｜报价速度看板（✅ 当前环境运行态验收完成）

新增 `GET /api/v1/admin/dashboard/quote-speed`。

看板展示：

- AI 生成耗时
- 人工确认耗时
- 总交付耗时
- AI 修改率趋势

冷启动时如果数据不足，页面必须提示“数据从上线后开始统计”。

完成记录（当前环境运行态验收，非正式生产上线）：

- 已新增 `FEATURE_DASHBOARD_QUOTE`，默认关闭；关闭时接口返回 `403 FEATURE_DISABLED`，前端展示“功能未开启”。
- 已新增报价速度聚合接口，按 `Asia/Shanghai` 支持 `today` / `week` / `month` / `last_30_days`。
- 已接入 `/admin/dashboard` Vite 页面，展示报价任务数、AI 生成耗时、人工确认耗时、总交付耗时、AI 修改率、趋势和状态分布。
- 已限制访问角色为 `admin` / `system_admin` / `viewer`；`staff` 不可访问看板接口。
- `quote_job_runner` 已改为基于 `time.perf_counter()` 写入单次报价运行实测 `duration_ms`，避免成功任务显示 0 秒。
- 已新增 `scripts/diagnose_quote_job_durations.py`，支持只读诊断和显式 `--apply` 回填；历史 121 条成功任务的 `duration_ms=0` 已在 `C:\AI_Backups\20260517_205242\db\ai_quotation.sql` 备份后完成回填。
- 报价速度聚合层已对旧数据中 `finished_at` 比 `created_at` 早 8 小时的记录做只读展示校正，避免人工确认耗时被系统时区差放大。
- 自动化验证已通过：`python -m compileall app scripts`、`python -m pytest`（85 passed）和 `npm.cmd run build`。
- 当前环境运行态验收已通过：`/admin/dashboard` 页面无 404 / 403 / 控制台接口错误，三类耗时显示正常，新增真实报价任务已进入统计且 AI 生成耗时大于 0。
- 未迁移旧 `index.html` / `admin.html` 业务功能；Phase 2 已在后续完成代码层交付，Phase 3+ 未启动。

### 阶段 2｜响应速度追踪（✅ 当前环境运行态验收完成）

新增 `client_inquiries`，但不建设独立线索系统。报价任务创建时可填写来源渠道 / 客户信息，并自动生成 ClientInquiry。

统计边界：只统计已进入报价流程的咨询。

`ClientInquiry` 表示一次业务咨询，`QuoteJob` 表示一次技术报价尝试。新增 `quote_jobs.client_inquiry_id` 支持一次咨询对应多次报价尝试，例如原始报价、重试报价和后续修正版报价。

创建时机：报价任务参数校验、文件基础校验通过后创建或复用 `ClientInquiry`。请求不合法、用户未授权或附件完全不可读时不创建；AI / RAG / N8N 后续失败仍保留咨询记录。

响应时间可信度使用 `time_source` 标注：

- `manual`：员工填写的真实首次咨询时间
- `default`：系统默认用报价创建时间，仅计入咨询数量，不计入平均响应时长
- `integration`：未来由钉钉 / 微信等集成回填

报价重试必须继承原 `client_inquiry_id`、`inquiry_time` 和 `time_source`，不得重置响应耗时。

完成记录（当前环境运行态验收，非正式生产上线）：

- 已新增 Alembic `20260514_0012`，创建 `client_inquiries` 并为 `quote_jobs` 增加 `client_inquiry_id`。
- 已新增 `FEATURE_CLIENT_INQUIRY`、`FEATURE_DASHBOARD_RESPONSE` 和 `RESPONSE_SLA_MINUTES`，默认关闭。
- 已在 `POST /api/v1/quote/jobs` 接入可选咨询字段；开关关闭时保持旧行为，不写咨询记录。
- 已新增 `GET /api/v1/client-inquiries` 和 `PATCH /api/v1/client-inquiries/{id}`；staff / manager 仅能查看和修正自己的咨询记录，admin / system_admin 可查看全部。
- 已新增 `GET /api/v1/admin/dashboard/response-speed`，默认时间样本只计数量、不计入平均首次响应耗时。
- 已在 `/admin/dashboard` 接入“响应速度”标签页，与报价速度共用时间范围控件；所有看板开关关闭时仍显示“功能未开启”。
- 自动化验证已通过：`python -m alembic heads` 显示 `20260514_0012 (head)`，`python -m compileall app scripts`、`python -m pytest`（更新后 86 passed）和 `npm.cmd run build` 均通过。
- 当前环境 Alembic 已升级到 `20260514_0012 (head)`，`FEATURE_CLIENT_INQUIRY=true`、`FEATURE_DASHBOARD_RESPONSE=true`、`PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已创建带 `inquiry_time` 的报价任务并在响应速度看板展示 1 条样本，平均首次响应 15 分钟、SLA 达标率 100%。
- 已修复 Celery worker 单独启动时未加载 `client_inquiries` 元数据导致 Phase 2 报价任务停留 `queued` 的问题；`scripts/phase2_response_smoke.ps1` 使用 Windows `China Standard Time` 生成测试询价时间，避免本机时区造成响应耗时偏差。
- 系统完善前不正式投入生产使用，正式生产启用待统一 Runbook。

### 阶段 2.5｜管理员报价运营闭环（后台补强，不启动 Phase 3）

本阶段不是独立大模块，不新增 `execution_tasks`，也不迁移旧 `admin.html` 的知识库功能。目标是在 Vite 管理台把现有报价任务、咨询记录和确认推送状态串起来，让管理员能从一个入口追踪报价闭环。

范围：

- `/api/v1/quote/jobs` 返回报价任务时补充 `client_inquiry` 和已确认 `history` 摘要。
- 报价任务列表支持按状态、需求来源、关键词、提交人和时间范围筛选。
- `/admin/dashboard` 新增“报价运营”标签页，展示客户、电话、需求来源、提交人、任务状态、AI 耗时、确认金额、钉钉推送状态和异常信息。
- 管理员可从该视图查看任务详情、重试失败/取消/超时任务、取消排队/运行中的任务，并触发现有的超时标记接口。
- 权限边界沿用现有 `quote_jobs` 接口：`admin` / `system_admin` 可看全量，普通用户仍只能看本人任务；Vite 管理台仅向管理员展示该标签页。

完成记录（当前环境验证完成，非正式生产上线）：

- 已复用现有 `quote_jobs`、`client_inquiries`、`quote_history` 数据，不新增数据库结构。
- 已补充后端列表/详情上下文字段和自动化测试。
- 已在 Vite 管理台接入“报价运营”视图。
- 当前环境已验证提交人列、筛选、任务详情和页面显示正常。
- 该阶段已封板，后续若进入任务执行体系，按 Phase 3 的 `execution_tasks` 路线单独推进。

### 阶段 3｜执行速度追踪

新增 `execution_tasks` 与 `execution_task_events`。

命名不使用 `tasks`，避免与 Celery task 和 `app/tasks/` 混淆。

`execution_tasks` 最低字段：

- `id, created_at, updated_at`
- `title`
- `source: meeting / quote / manual`
- `source_ref_id`
- `assignee_id: FK -> users.id`
- `due_at`
- `completed_at: nullable`
- `status: pending / in_progress / done / cancelled`
- `notes`

`completed_at` 在任务进入 `done` 时同事务写入；取消任务不写 `completed_at`。

逾期状态不持久化，接口动态返回 `is_overdue`。

钉钉提醒先走任务通知群 Webhook，个人 @ 等企业应用权限到位后再做。

### 阶段 4a｜AI 执行系统：手动纪要 + 草稿确认

新增 `meeting_notes` 与 `task_drafts`。

已确认纪要后续更正使用独立 `meeting_note_revisions` 表，不直接覆盖原始纪要。revision 可重新提取补充草稿，但不得自动修改已确认的 `execution_tasks`。

`meeting_note_revisions` 最低字段：

- `id, created_at`
- `meeting_note_id`
- `content`
- `reason`
- `created_by`
- `previous_content_sha256`
- `trace_id`

流程：

```text
会议纪要录入 -> AI 提取任务草稿 -> 人工确认 / 修改 -> 正式写入 execution_tasks
```

AI 输出必须是固定 JSON Schema，不接受自由文本解析。任务草稿必须回显 `source_sentence`，帮助人工判断 AI 理解依据。

AI 调用治理详见 [ADR-AI-Governance.md](ADR-AI-Governance.md)。

### 阶段 4b｜语音转写接入

主选讯飞录音文件识别，阿里云作为备选。转写任务临时复用 `quote_jobs + job_type='transcription'`，状态机与报价任务一致。

音频上传后：

```text
上传音频 -> 转写文本 -> 写回 meeting_notes.transcript_text -> 自动触发阶段 4a 任务提取
```

异步任务边界详见 [ADR-AsyncJob.md](ADR-AsyncJob.md)。

阶段 4 依赖阶段 3 的 schema，但不依赖 `FEATURE_EXECUTION=true`。即 `execution_tasks` 表必须已迁移，`FEATURE_MEETING_AI=true` 时 `confirm-tasks` 可正常写入执行任务；`FEATURE_EXECUTION` 只控制独立任务管理 UI 和任务 CRUD 入口。

### 阶段 4c｜钉钉会议导入

等钉钉企业应用权限审批后启动。不允许卡住主闭环。

拉取结果进入同一条流程：

```text
钉钉会议记录 -> meeting_notes -> task_drafts -> execution_tasks
```

### 阶段 5｜知识库历史数据治理导入

历史成交数据不得直接导入 `materials`，必须先进入 `knowledge_candidates`。

扩展字段：

- `source_batch_id`
- `source_file`
- `source_line`
- `data_type`
- `confidence`
- `target_type`
- `target_id`
- `snapshot_id`

导入后先跑 RAG 评测，若 Hit@K 或 MRR 下滑，按 `source_batch_id` 回滚。

批次确认后如有数据进入 `materials` 或 RAG 文档，自动 enqueue 一次 `rag_reload`。reload 成功后自动 enqueue `rag_eval` 并写入评测报告；reload / eval 失败不回滚导入，但必须告警并在知识库页面显示待处理状态。

### 阶段 6｜经营驾驶舱数据底座

新增：

- `projects`
- `project_quotes`
- `contracts`
- `contract_adjustments`
- `payments`
- `project_costs`
- `business_events`
- `business_import_batches`

增项、减项、优惠、签证必须单独追踪，不直接覆盖原合同金额。返工成本用 `project_costs.cost_type='rework'` 追踪。

项目归档后默认隐藏，但仍计入经营统计。经营数据和合同明细导出仅允许 `admin` / `system_admin`，并必须带水印。

详见 [ADR-Business-Dashboard.md](ADR-Business-Dashboard.md)。

### 附录 A｜样板图驱动报价

新增效果图报价入口，与文字 / 清单图入口并列，不替换现有流程。详见 [APPENDIX-Image-to-Quote.md](APPENDIX-Image-to-Quote.md)。

---

## 6. 功能开关

```env
FEATURE_DASHBOARD_QUOTE=false
FEATURE_DASHBOARD_RESPONSE=false
FEATURE_DASHBOARD_EXECUTION=false
FEATURE_DASHBOARD_BUSINESS=false
FEATURE_CLIENT_INQUIRY=false
RESPONSE_SLA_MINUTES=30
FEATURE_EXECUTION=false
FEATURE_MEETING_AI=false
FEATURE_AUDIO_TRANSCRIPTION=false
FEATURE_VITE_FRONTEND=false
PUBLIC_ACCESS_ENABLED=false

FEATURE_IMAGE_QUOTE=false
FEATURE_IMAGE_QUOTE_GLM=true

AI_RAW_LOG_ENABLED=false
AI_RAW_LOG_RETENTION_DAYS=7
AI_RAW_LOG_MAX_RETENTION_DAYS=30
```

开关启动时读取，修改需重启服务。未启用接口返回 `HTTP 403 FEATURE_DISABLED`。依赖矩阵和 Vite 路由阶段详见 [FEATURE-FLAGS.md](FEATURE-FLAGS.md)。

---

## 7. Alembic 迁移顺序

```text
① users.dingtalk_user_id / dingtalk_bound_at / role_version 补列 + user_roles / user_role_events 建表
   同步迁移 users.role='user' -> staff；users.role='admin' -> admin
        ↓
② client_inquiries
   execution_tasks
   quote_image_analyses（含 model_provider / model_name / confidence_avg / timed_out / canceled）
        ↓
②-b ALTER TABLE quote_jobs ADD client_inquiry_id
     ALTER TABLE quote_jobs ADD timeout_at
     ALTER TABLE quote_jobs ADD source_type, image_analysis_id
        ↓
③ client_inquiry_events
   execution_task_events
   审计表补 ip_address / user_agent / trace_id
        ↓
④ meeting_notes
        ↓
⑤ task_drafts
   meeting_note_revisions
        ↓
⑤-b ALTER TABLE meeting_notes ADD transcript_text, audio_object_id
     ALTER TABLE quote_jobs ADD job_type
        ↓
⑥ ALTER TABLE knowledge_candidates ADD source_batch_id, target_type 等字段
   rag_reload / rag_eval 任务记录与知识状态查询所需字段
        ↓
⑦ projects / project_quotes / contracts / contract_adjustments / payments / project_costs / business_events / business_import_batches
   project_costs.created_by 必填；payments.amount / project_costs.amount 加 CHECK (amount > 0)
   business_events 增补 exported / import_rollback_failed 等事件类型
        ↓
⑧ ai_invocations / ai_call_logs 原文对象、raw_log_status、raw_log_error、raw_logs_cleaned_at、prompt/workflow/dify 版本字段
```

所有新增表和字段必须走 Alembic revision，不允许回退到 `AUTO_CREATE_TABLES` 或启动兼容迁移。`quote_jobs.timeout_at` 用于 `mark_async_job_timeouts`，索引建议为 `(job_type, status, timeout_at)`。

---

## 8. Definition of Done

| 阶段 | 完成定义 |
|------|---------|
| 阶段 0 | 已完成当前环境验证：登录、Vite fallback、RBAC、`role_version`、权限管理页面、公网安全门槛清单可验收；正式生产准备推迟到系统整体完善后统一处理 |
| 阶段 1 | 已完成当前环境运行态验收：报价速度接口有数据，三类耗时分区展示，新增真实报价任务可进入统计；正式生产准备推迟到系统整体完善后统一处理 |
| 阶段 2 | 已完成当前环境运行态验收：创建报价可自动生成 ClientInquiry，响应看板标注统计边界、样本量和低可信数据排除规则，内网 smoke 可展示 15 分钟响应样本；正式生产准备推迟到系统整体完善后统一处理 |
| 阶段 3 | ExecutionTask 可创建、分配、完成、取消；逾期动态计算；Celery beat 开机自启和健康检查可用 |
| 阶段 4a | 纪要生成草稿，人工确认后写入 ExecutionTask，AI 失败可降级手动添加 |
| 阶段 4b | 上传音频后返回转写文本，并自动进入任务草稿流程；精度评测达标 |
| 阶段 4c | 钉钉会议导入进入同一纪要 -> 草稿 -> 任务流程 |
| 阶段 5 | 导入后 RAG 评测不低于基线；可按批次追溯和回滚 |
| 阶段 6 | 经营数据可导入和回滚；增减项、返工成本、毛利率口径正确；viewer 只能看脱敏汇总 |
| 附录 A | 效果图识别达标，失败可降级，现有文字报价路径无回归 |

所有新增表、MinIO 对象和文件引用必须纳入备份。阶段 0、阶段 4b、阶段 6 上线前各做一次恢复演练。

高风险阶段上线前必须补 Runbook：

- Phase 3：执行任务上线 Runbook，覆盖任务创建、钉钉提醒、逾期扫描、权限回滚
- Phase 5：知识批量导入 Runbook，覆盖预览、确认、reload、eval、批次回滚
- Phase 6：经营数据导入 / 回滚 Runbook，覆盖模板、预览、确认、导出水印、权限脱敏、归档统计

每个看板上线前必须准备 5-10 条验收种子数据，用于验证聚合、空数据、低可信数据、逾期、归档、导出水印和权限脱敏。

---

## 9. 测试要求

最低自动化覆盖：

- RBAC 迁移、角色叠加、撤权旧 token 失效
- 钉钉登录二次验证：管理员每次登录验证，当天有效，跨自然日失效
- 公网安全门槛配置检查
- ClientInquiry 自动创建和统计边界
- `time_source='default'` 不计入平均响应时长，但计入咨询数量
- 今日 / 本周 / 本月 / 当天有效均按 `Asia/Shanghai` 计算
- Quote retry 继承原 ClientInquiry，不重置响应耗时
- ExecutionTask 逾期动态计算
- 状态机禁止转换和重复调用语义
- Feature Flag 依赖矩阵和功能未开启页面
- AI 调用日志不泄露原始敏感内容
- 语音转写 mock 成功 / 失败 / 超限
- 经营数据导入预览、确认、重复检测、回滚
- 合同调整项和返工成本参与毛利率计算
- 项目归档默认隐藏但仍计入经营统计
- Excel 导出仅 admin/system_admin 可用，并包含水印字段
- 文件访问权限、签名 URL 过期、离职撤权不可访问
- 样板图路径不影响文字报价路径

---

## 10. 外部依赖准备

| 依赖 | 用于阶段 | 准备内容 | 建议启动时间 |
|------|---------|---------|-------------|
| 钉钉任务通知 Webhook | 3 | 任务通知群、机器人、`DINGTALK_TASK_WEBHOOK` | Phase 3 前 1 周 |
| 钉钉企业应用 | 0 / 4c / 公网 | 二次验证、个人 @、会议记录权限 | 越早越好 |
| 语音转写 API | 4b | 讯飞 / 阿里云账号、热词、额度、并发限制 | Phase 4a 开始 |
| 经营数据模板 | 6 | 项目、合同、调整项、回款、成本 Excel 模板 | Phase 6 前 |
| 公网基础设施 | 公网前 | 域名、HTTPS、反向代理、CORS、CSRF、登录防爆破、文件签名 URL | 公网前至少 1 周 |

---

## 11. 文档维护

本文件是总控路线图。任何单个主题超过 200 行，必须进入 ADR 或附录，不再继续堆叠到主文档。

根目录 `AGENTS.md` / `ROADMAP.md` 已同步更新，后续以本总控路线图和 ADR/附录为准。

---

## 12. 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-05-14 | 初版创建 | AI 辅助规划 |
| 2026-05-14 | v2-v12：逐轮补齐 Vite 分批迁移、指标口径、响应追踪、ExecutionTask 命名、逾期动态计算、草稿确认层、知识导入批次追踪、SPA fallback、Celery beat、语音转写、附录 A 等细节 | Codex / Claude / 用户反馈 |
| 2026-05-14 | v13：补充多角色 RBAC、system_admin、经营驾驶舱数据底座、QuoteHistory 与 Project 多对多关系 | Codex 架构补丁 |
| 2026-05-14 | v14：补充 `role_version`、异步任务边界、导入幂等、经营审计、批次回滚、文档拆分规则 | Codex 架构硬化 |
| 2026-05-14 | v15：补充公网安全门槛、响应统计边界、毛利率口径、文件永久保存、离职撤权不可访问 | 用户业务口径 + Codex |
| 2026-05-14 | v16：按专题拆分为 ADR/附录，并补齐钉钉二次验证、合同调整项、AI 调用治理 | 用户确认 + Codex |
| 2026-05-14 | v17：补齐钉钉登录二验（每次登录、当天有效）、合同调整项确认权限、经营导出控制、AI 原文 7 天游标清理、阶段 0 上线 Runbook | 用户业务口径 + Codex |
| 2026-05-15 | v18：补齐容量假设、对象存储演进、项目归档口径、导出水印和 API 契约骨架 | 用户业务口径 + Codex |
| 2026-05-15 | v19：补齐 staff / manager 查看和更新本人执行任务的权限边界，限制其不得修改负责人、截止时间和来源字段 | Codex |
| 2026-05-15 | v20：新增状态机和功能开关矩阵，补齐 HTTP 状态码、动作接口、咨询与报价重试关系、timeout_at、RAG reload/eval、Phase 3/4 解耦和上线 Runbook 说明 | Claude 建议 + Codex |
| 2026-05-15 | v21：补齐 Asia/Shanghai 时间口径、自然时间 SLA、空数据与低可信数据展示、Phase 0-2 API Schema 优先级、后续 Runbook 和验收种子数据要求 | Codex |
| 2026-05-15 | v22：补齐状态机审计字段统一口径、转写超时引用、PUBLIC_ACCESS_ENABLED 前置清单、导出审计写入 business_events、Phase 0 viewer 访问旧页面边界 | Claude 建议 + Codex |
| 2026-05-15 | v23：在状态机中内联转写 timeout_at 公式，将 PUBLIC_ACCESS_ENABLED 前置清单改为前置检查，并明确只分配 viewer / manager 的用户访问旧页面返回 401 / 403 属于预期行为 | Claude 建议 + Codex |
| 2026-05-15 | v24：补齐 contracts 状态机、FEATURE_AUDIO_TRANSCRIPTION 对 FEATURE_MEETING_AI 的依赖、用户列表接口字段、钉钉不可用时高安全 break-glass 策略和 Beat 任务调度表 | Claude 建议 + Codex |
| 2026-05-15 | v25：明确钉钉验证按 Asia/Shanghai 截止，补 ExecutionTask cancel 动作接口、timeout_at 旧数据回填基准、旧页面迁移准出条件和 project_costs.created_by | Claude 建议 + Codex |
| 2026-05-15 | v26：将 RBAC 中“用户本地自然日”改为 Asia/Shanghai，补合同 sign/archive/cancel 动作接口，并明确 pending_reload 是标记不是 rag_reload 持久状态 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v27：补齐 business_events.entity_type=export、project_costs 修正流程和 cancel 动作、quote_image_analyses timed_out/canceled 同步、AI raw log cleaned 状态与 00:30 清理时间 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v28：将撤权从 DELETE 改为 revoke 动作接口，补合同 signed/archived/cancelled 时间戳、rag_reload 超时机制、break_glass_sessions 模型、图片识别 provider 开关语义和 AI 调用 user_id 归属 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v29：补齐 payments / project_costs 状态机、回款动作接口、钉钉验证过期懒更新、合同作废对回款和调整项的统计排除、图片置信度映射和 rag_reload 调用日志类型 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v30：补齐 business_events signed/archived/unarchived 事件、contract_adjustments confirmed_by/confirmed_at 口径、admin_action_challenges 目标字段类型、会议纪要 revision 权限和 rollback 幂等语义 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v31：补齐墙面层高系数与洞口扣减公式、project_quotes created_at/created_by、import_type 单数命名、合同全文外发高风险操作、challenge 过期懒更新和 viewer 经营汇总白名单 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v32：补齐 users.dingtalk_user_id 字段约束、payment_terms 类型、合同和调整项金额 CHECK、project_costs updated_at 语义、execution_tasks.completed_at 写入规则和 AI 原文对象存储路径 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v33：补齐 quote_image_analyses.model_provider 枚举、视觉模型持续低召回阈值、AI raw log save_failed 与删除失败重试、合同 archived 不回退、反向调整追溯和导入 rollback_failed 状态 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v34：补齐 execution_tasks 取消权限、meeting_notes draft 作废、系统自动审计填充值、project_costs replaces_cost_id、rag_reload 分布式锁、知识候选审批审计、prompt_version 格式和统一脱敏策略 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v35：补齐图片分析重试与文件有效性、confidence_avg 类型、adapter 去重和 notes 保留、RAG eval 状态机、知识状态接口、AI 原文查看接口、钉钉验证边界和公网判定规则 | Codex 判断 + Claude 建议 |
| 2026-05-15 | v36：补齐实现前规格收口：执行任务 PATCH 进度白名单、business_events 导出和回滚失败事件、取消原因统一落审计、AI 原文短链响应体、FEATURE_IMAGE_QUOTE_GLM 矩阵、settings 路由边界、Phase 0 Runbook 字段检查和旧 RAG 方案历史标记 | Codex |
| 2026-05-18 | v37：记录 Phase 0 开发与当前环境验证完成，Alembic `20260514_0011 (head)`、RBAC / `role_version`、Vite 壳、SPA fallback 和当前环境冒烟均通过；正式生产上线待单独 Runbook；Phase 1 随后启动 | Codex |
| 2026-05-18 | v38：完成 Phase 1 报价速度看板代码层交付，新增 `FEATURE_DASHBOARD_QUOTE`、`/api/v1/admin/dashboard/quote-speed`、`/admin/dashboard` 看板视图与自动化测试；`compileall`、全量 `pytest` 和 Vite build 通过；正式生产启用仍待单独 Runbook | Codex |
| 2026-05-18 | v39：修复 Phase 1 报价耗时口径，worker 改为写入实测 `duration_ms`，历史 121 条成功任务 0 耗时已在当前数据库备份后回填，聚合层只读校正旧 `finished_at` 时区偏移；全量 `pytest` 更新为 80 passed | Codex |
| 2026-05-18 | v40：记录 Phase 1 当前环境运行态验收通过，`/admin/dashboard` 页面、看板数据和新增真实报价统计均正常；正式生产启用仍待单独 Runbook，Phase 2+ 未启动 | Codex |
| 2026-05-18 | v41：记录系统完善前不正式投入生产使用；完成 Phase 2 响应速度追踪代码层，新增 `client_inquiries`、`quote_jobs.client_inquiry_id`、咨询查询/修正接口、响应速度聚合接口、`FEATURE_CLIENT_INQUIRY` / `FEATURE_DASHBOARD_RESPONSE` 和 Vite 响应速度标签页；当前环境 Alembic 已升级到 `20260514_0012 (head)`，`pytest` 更新为 85 passed，Phase 3+ 未启动 | Codex |
| 2026-05-18 | v42：完成 Phase 2 当前环境运行态验收，功能开关已在内网环境打开且 `PUBLIC_ACCESS_ENABLED=false`；修复 Celery worker 缺少 `ClientInquiry` 元数据导入导致测试任务卡在 `queued` 的问题；新增 `scripts/phase2_response_smoke.ps1`，按中国时区生成验收样本，响应速度看板已显示 1 条样本、平均首次响应 15 分钟；`pytest` 更新为 86 passed，Phase 3+ 未启动 | Codex |
