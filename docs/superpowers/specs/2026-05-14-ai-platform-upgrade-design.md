# 旗胜 AI 平台架构升级路线
> 创建日期：2026-05-14
> 最后更新：2026-05-14（含 Codex 审查意见 v5 + 实施保障章节）
> 状态：规划中
> 负责人：待定

---

## 背景与目标

基于旗胜智能装饰"AI 落地方案"战略架构图，当前报价系统（03 AI 报价系统）已基本完整，需要在此基础上扩展完成另外三个模块：

| 模块 | 当前状态 | 本路线目标 |
|------|---------|-----------|
| 01 AI 知识库系统 | 🟡 基础已有，等待历史数据 | 数据到位后批量治理导入 |
| 02 AI 执行系统 | ❌ 完全空白 | 会议纪要 → 任务拆解 → 跟踪闭环 |
| 03 AI 报价系统 | ✅ 已完整 | 持续优化准确率 |
| 04 AI 管理驾驶舱 | 🟡 仅有运维监控 | 三类效率指标全覆盖 |

**核心价值观**：团队效率，"快"是最重要的衡量标准。三类"快"的定义如下：

| 快的类型 | 定义 | 数据来源 |
|---------|------|---------|
| 报价快 | 客户需求到报价单交付的时长 | QuoteJob 时间戳 |
| 响应快 | 客户咨询到有人接单的时长 | ClientInquiry（新建） |
| 执行快 | 会议决定到任务落地的时长 | ExecutionTask（新建） |

**架构决策**：所有模块在现有系统上扩展，不新建项目。共用用户体系（JWT）、数据库（MySQL）、基础设施（Redis / Celery / 钉钉 Webhook）。

---

## 指标口径定义（先于看板开发）

驾驶舱开发前必须明确每个指标的计算口径，避免管理层与业务员理解不一致。

### 报价耗时（三层，分区展示）

| 指标名 | 计算方式 | 面向受众 |
|--------|---------|---------|
| AI 生成耗时 | `QuoteJob.duration_ms`（已有字段），或 `created_at → finished_at` | 工程侧（系统性能） |
| 人工确认耗时 | `QuoteJob.finished_at → QuoteFeedback.confirmed_at`；无反馈记录时 fallback 到 `QuoteHistory.created_at` | 管理层（业务效率） |
| 总交付耗时 | `QuoteJob.created_at → QuoteFeedback.confirmed_at`（同上 fallback） | 管理层（端到端） |

> 注1：`QuoteJob` 当前无 `started_at` 字段（只有 `created_at` / `finished_at` / `duration_ms`）。如将来需要区分"排队等待"与"真正 AI 执行"时间，再新增 `started_at`。
> 注2：三个数字不要混在同一张图里，否则"系统很快但人工很慢"的真实问题会被平均数掩盖。

### 响应耗时
- 首次响应时长 = `ClientInquiry.first_response_time - ClientInquiry.inquiry_time`
- SLA 状态：按渠道设定阈值（如电话 2 小时、微信 4 小时）

### 执行耗时
- 任务完成时长 = `ExecutionTask.completed_at - ExecutionTask.created_at`
- 逾期判断 = 动态计算（`due_at < now() AND completed_at IS NULL`），不作为持久状态存储

---

## 实施路径：路径 C（先可见、再闭环、最后外部集成）

```
第 1-2 周   阶段 0+1   Vite 登录鉴权统一 + 报价速度看板
第 3-4 周   阶段 2     响应速度追踪（嵌入报价流程）
第 5-6 周   阶段 3     ExecutionTask 模型 + 执行速度看板
第 7-9 周   阶段 4a    手动会议纪要 + AI 任务草稿确认
第 10-12 周 阶段 4b    语音转写接入
第 13周+    阶段 4c    钉钉会议导入（等权限到位）
等数据到位   阶段 5     01 知识库历史数据批量治理导入
```

---

## 阶段 0｜Vite 登录鉴权统一

**时间**：第 1 周（与阶段 1 并行）
**策略**：不全量迁移，新建 Vite 壳，优先统一鉴权层，旧页面保留逐步迁移。

### 任务清单
- [ ] 初始化 Vite + Vue 3 + TypeScript 工程
- [ ] 配置 FastAPI 托管 Vite 编译产物，设置 SPA fallback 路由：
  - 未匹配的路径返回 Vite 的 `index.html`（交由 vue-router 处理）
  - 例外路由：`/index.html`、`/admin.html`、`/app.html` 继续静态服务旧页面
  - 避免 `/admin/dashboard` 刷新后出现 FastAPI 404
- [ ] 优先迁移 `/login` 页面（原 app.html 登录部分）
- [ ] 统一封装 `apiClient`（Axios 实例）：
  - JWT 存取（localStorage）
  - 401 自动跳转登录
  - admin 路由守卫
  - 过期处理
- [ ] 接入 vue-router，规划路由结构：
  - `/login` — 登录页
  - `/quote` — 报价工作台（原 index.html，后续迁移）
  - `/admin/knowledge` — 知识库管理（原 admin.html 部分，后续迁移）
  - `/admin/dashboard` — 管理驾驶舱（新，走 Vite）
  - `/admin/execution` — 执行系统（新，走 Vite）
- [ ] 旧的 index.html / admin.html 暂时保留，入口逐步收拢到新壳

### 为什么这么做
鉴权层如果新旧并存，会出现"旧页面能进、新页面被踢回登录"的问题，以及 JWT 过期逻辑不一致的 bug。登录和 Axios 拦截器优先统一，后续旧页面迁移时只需接入已有的 apiClient，不用重写鉴权逻辑。

### 预期成果
新驾驶舱和执行系统页面走 Vite，鉴权行为与旧页面完全一致，用户无感知切换。

---

## 阶段 1｜报价速度看板

**时间**：第 1-2 周

### 任务清单
- [ ] 新增聚合接口 `GET /api/v1/admin/dashboard/quote-speed`
  - 返回三层耗时指标（AI 生成 / 人工确认 / 总交付）
  - 数据来源：QuoteJob（时间戳）+ QuoteHistory（confirm 时间）+ QuoteFeedback（修改标记）
- [ ] 驾驶舱前端"报价速度"面板（Vite 新页面）：
  - 三张独立指标卡片（分区展示，不合并）
  - 折线图：近 30 天日均各阶段耗时趋势
  - AI 修改率趋势（来自 QuoteFeedback.was_modified）

### 为什么这么做
数据已在数据库中（QuoteJob + QuoteFeedback），无需新增采集。2 周内让管理层看到报价环节的效率基线，有基线才能评估后续优化效果。

### 预期成果
管理层一屏看到：AI 生成平均多快、人工确认拖了多久、端到端交付时长——三个数字分开展示，哪个环节慢一目了然。

---

## 阶段 2｜响应速度追踪

**时间**：第 3-4 周

### 设计原则
响应速度追踪**嵌入报价流程**，而非独立填报表单。业务员创建报价任务时顺手填写客户信息，系统自动生成 ClientInquiry 记录，降低登记成本至接近零。

### 新增数据模型 `client_inquiries`

```
字段：
- id, created_at, updated_at
- source: 来源渠道（微信 / 电话 / 现场 / 老客户 / 其他）
- inquiry_time: 客户最早咨询时间；用户可手填，不填则默认写入当前时间
- first_response_time: 首次响应时间；报价任务创建时自动写入当前时间（创建即代表接单）；
  后续接入钉钉/微信后可用真实首次回复时间覆盖
- responder_id: 接单人（关联 users 表，默认为创建报价的用户）
- client_name: 客户姓名/公司（可选）
- client_phone: 客户电话（可选）
- status: 业务状态（pending / responded / converted / abandoned）
  ⚠️ 不与 SLA 状态混用：sla_status 按渠道阈值动态计算返回，不持久化存储
- quote_job_id: 关联 QuoteJob（外键）
- notes: 备注
```

> SLA 阈值配置：初期写入 `.env`（如 `SLA_PHONE_HOURS=2`、`SLA_WECHAT_HOURS=4`、`SLA_ONSITE_HOURS=8`），由接口读取后按渠道动态返回。如将来需要多客户/多项目差异化阈值，再迁移到 `sla_config` 数据库表；当前阶段不过度设计。超时由 `first_response_time - inquiry_time > 阈值` 实时计算，不写入数据库。

### 任务清单
- [ ] 新增 `client_inquiries` 表 + Alembic migration
- [ ] 修改报价任务创建流程：`POST /api/v1/quote/jobs` 新增可选字段（来源渠道 / 客户信息），创建任务时自动生成 ClientInquiry
- [ ] 新增接口 `GET /api/v1/admin/dashboard/response-speed`（聚合统计）
- [ ] 报价工作台前端：发起报价时新增"来源渠道"下拉选择（不强制填写，降低阻力）
- [ ] 驾驶舱"响应速度"面板：
  - 平均首次响应时长（按渠道分组）
  - SLA 达标率趋势
  - 各业务员响应速度对比
  - 逾期未响应数量预警
  - ⚠️ 面板须标注"数据起始日期（Phase 2 上线日期）"，避免管理层将历史空白误读为系统故障；历史 QuoteJob 无对应 ClientInquiry 记录，不纳入统计范围

### 为什么这么做
独立的登记入口业务员大概率不会坚持填。嵌入报价创建流程后，登记是报价的自然副产品，数据质量有保证。预留 `first_response_time` 和自动化字段，后续可从钉钉/微信自动回填，不需要改数据模型。

### 预期成果
每笔报价自动关联一条咨询记录，管理层看到各渠道和各业务员的响应速度对比，可以定向改进最慢的环节。

---

## 阶段 3｜执行速度追踪

**时间**：第 5-6 周

### 新增数据模型 `execution_tasks`

> 命名说明：不使用 `tasks`，避免与 Celery task 和 `app/tasks/` 目录概念混淆。

```
字段：
- id, created_at, updated_at
- title: 任务标题
- source: 来源类型（meeting / quote / manual）
- source_ref_id: 来源关联 ID（如 MeetingNote.id 或 QuoteJob.id）
- assignee_id: 负责人（关联 users 表）
- due_at: 截止时间
- completed_at: 完成时间（NULL 表示未完成）
- status: 状态（pending / in_progress / done / cancelled）
- notes: 备注
```

> 逾期状态不存储为持久字段，统一由 `due_at < now() AND completed_at IS NULL` 动态计算，避免定时任务未跑时状态脏数据。

### 任务清单
- [ ] 新增 `execution_tasks` 表 + Alembic migration
- [ ] 新增接口：
  - `POST /api/v1/execution-tasks` — 创建任务
  - `GET /api/v1/execution-tasks` — 列表查询
  - `PATCH /api/v1/execution-tasks/{id}` — 更新任务（含 status=cancelled 软删除）
  - ⚠️ 不提供硬删除接口；admin 可将 status 置为 cancelled，保留审计链
  - `GET /api/v1/admin/dashboard/execution-speed` — 聚合统计
- [ ] 任务创建/分配后，通过现有钉钉 Webhook 推送通知给负责人
  - 复用现有 `DINGTALK_WEBHOOK` + HMAC 签名机制，无需企业应用权限
  - 推送内容：任务标题、截止时间、来源（会议/报价/手动）
- [ ] 新增 Celery beat 定时任务：每小时扫描逾期任务
  - 条件：`due_at < now() AND completed_at IS NULL AND status NOT IN ('done','cancelled')`
  - 触发钉钉逾期提醒（同上 Webhook），限制每任务每天最多提醒 1 次，避免轰炸
  - ⚠️ beat 是**独立进程**，需加入 `start_all.ps1` 启动编排，并在 `install_celery_worker_service.ps1` 注册独立任务计划程序
- [ ] 新增 `execution_task_events` 审计表，记录创建、分配、推送、提醒、完成、取消等事件
  - 字段：id、task_id、event_type、operator_id（NULL=系统自动）、notes、created_at
  - 事件只追加不修改，支持审计和后续自动化
- [ ] 驾驶舱"执行速度"面板：
  - 任务完成率（本周 / 本月）
  - 逾期任务数量（动态计算）预警
  - 平均任务完成时长
  - 各负责人任务量与完成率对比

### 为什么这么做
先建好数据模型，阶段 4 的 AI 会议纪要功能只需往 `execution_tasks` 自动写入，驾驶舱不用再改。这是阶段 4 的技术铺垫。

### 预期成果
驾驶舱三块面板（报价速度 / 响应速度 / 执行速度）全部上线，管理层一屏看全三类"快"，红黄灯预警哪个环节在拖。

---

## 阶段 4a｜AI 执行系统——手动纪要 + 草稿确认

**时间**：第 7-9 周

### 设计原则
AI 从会议纪要提取的任务**不直接写入** `execution_tasks`，必须经过草稿确认层（与报价预审弹窗逻辑一致）：

```
会议纪要录入 → AI 提取任务草稿 → 人工确认/修改 → 正式写入 execution_tasks
```

避免 AI 把"讨论事项"误判为"责任任务"。

### 新增数据模型 `meeting_notes` + `task_drafts`

```
meeting_notes:
- id, created_at
- content: 原始纪要文本
- source: 来源类型（manual / audio / dingtalk）
- created_by: 录入人

task_drafts:
- id, meeting_note_id（关联 meeting_notes）
- title: AI 提取的任务标题
- suggested_assignee: AI 建议负责人（存储原始名字字符串，非 user_id）
- suggested_due_at: AI 建议截止时间
- status: 草稿状态（pending_review / accepted / rejected）
- accepted_task_id: 确认后关联的 ExecutionTask.id

> `suggested_assignee` 存储 AI 输出的原始文字（如"张总"、"小李"），草稿确认界面展示此字符串，由人工在确认时从下拉列表选择实际 `assignee_id`，**不做自动模糊匹配**——避免"张三"静默错配到"张三丰"，造成任务推送错误。
```

### 任务清单
- [ ] 新增 `meeting_notes` / `task_drafts` 表 + Alembic migration
- [ ] 新增接口 `POST /api/v1/meetings`（保存纪要 + 触发 AI 提取）
  - 调用 DeepSeek 解析纪要，返回结构化任务草稿列表
- [ ] 新增接口 `POST /api/v1/meetings/{id}/confirm-tasks`（人工确认后批量写入 execution_tasks）
- [ ] 前端执行系统页面：
  - 富文本会议纪要录入框
  - AI 提取结果草稿列表（支持逐条确认、修改、删除）
  - 一键确认写入正式任务

### 预期成果
业务员录入一次会议纪要，AI 自动提取任务草稿，人工二次确认后进入执行追踪，钉钉推送给负责人。

---

## 阶段 4b｜语音转写接入

**时间**：第 10-12 周

### 任务清单
- [ ] 评估并接入语音转写服务（讯飞实时语音 API 或阿里云语音识别）
- [ ] 新增接口 `POST /api/v1/meetings/transcribe`（上传音频 → 返回转写文本）
- [ ] 转写结果自动进入 AI 任务提取流程（复用阶段 4a 的逻辑）
- [ ] 前端：上传录音按钮 + 转写进度展示

---

## 阶段 4c｜钉钉会议导入（待权限就绪）

**时间**：第 13 周起，等企业应用权限审批通过后启动

> 注意：钉钉"读取会议记录"涉及企业应用权限审批，周期不可控。不应让此项卡住主闭环。阶段 4a 完成后主流程已完整，本阶段是增量自动化。

### 任务清单
- [ ] 提前申请钉钉企业应用权限（会议记录读取）
- [ ] 新增接口 `POST /api/v1/meetings/import-dingtalk`
- [ ] 拉取结果进入相同的纪要 → 草稿确认流程

---

## 阶段 5｜01 知识库历史数据治理导入

**时间**：等工程部提供历史成交数据后启动

### 设计原则
历史成交数据结构混杂（材料价格、工艺做法、项目案例、报价策略、地区差异），不能直接导入 `materials` 表，必须经过暂存审核和分类治理。

### 扩展现有 `knowledge_candidates` 表支持批次追踪

在现有知识候选字段基础上新增：

```
来源追踪字段：
- source_batch_id: 批次标识（同一次导入共享，用于整批回滚）
- source_file: 来源文件名
- source_line: 原始行号/页码
- data_type: 数据类型（material / technique / case / pricing_sample / risk_rule）
- confidence: 置信度（0.0-1.0，人工标注或模型输出）

落库目标追踪字段（审核通过后写入）：
- target_type: 写入目标类型（material / rag_doc / case / pricing_sample）
- target_id: 写入目标记录 ID
- snapshot_id: 写入时的材料库快照 ID（关联 materials_audit）
```

> 仅有来源（source_batch_id）而没有去向（target_type / target_id / snapshot_id），整批回滚时无法精确定位要撤销哪些记录。来源和去向必须同时记录。

### 任务清单
- [ ] 与工程部确认数据格式（Excel / CSV / 纸质扫描）
- [ ] 制定数据模板标准，工程部按模板整理后交付
- [ ] 如为非结构化格式：用 GLM-4V 或 OCR 提取结构化字段
- [ ] 批量导入至 `knowledge_candidates`（暂存，不直接入库）
- [ ] 人工审核分类：分流至 materials / 案例库 / RAG 文档
- [ ] 审核完成后触发 RAG 热更新（`/admin/reload`）
- [ ] 执行 RAG 评测（eval_rag.py），对比导入前后 Hit@K 和 MRR
- [ ] 如质量下滑，按 `source_batch_id` 整批回滚

### 为什么这么做
直接批量写入 materials 会污染现有 70 条精标数据，且无法追溯问题数据来源。经过暂存→审核→分类的治理流程，发现问题可按批次追溯或整批回滚，知识库质量可控。

### 预期成果
知识库从 70 条扩展至数百条真实成交行情，RAG 检索准确率显著提升，每批数据来源可查、可回滚。

---

## 整体时间线

```
Week 1     Vite 壳 + 登录鉴权统一 + 报价速度接口开发
Week 2     报价速度看板上线（可演示给管理层）
Week 3     client_inquiries 模型 + 报价流程嵌入登记
Week 4     响应速度面板上线
Week 5     execution_tasks 模型 + 任务 CRUD 接口
Week 6     执行速度面板上线（驾驶舱三类指标齐全，完整演示）
Week 7-9   meeting_notes + task_drafts + AI 拆任务 + 草稿确认流程
Week 10-12 语音转写接入
Week 13+   钉钉会议导入（等权限）
等数据到位  知识库历史数据批量治理导入
```

---

## 技术依赖与风险

| 依赖项 | 风险等级 | 缓解措施 |
|--------|---------|---------|
| Vite 分批迁移 | 低 | 鉴权层优先统一，旧页面逐步迁移，不影响现有功能 |
| 响应速度手动录入习惯 | 中 | 嵌入报价流程，降低录入成本至接近零 |
| DeepSeek 任务拆解质量 | 中 | 草稿确认层兜底，AI 误判不会直接入库 |
| 讯飞/阿里云语音 API | 低 | 商业 API，接入文档成熟，风险可控 |
| 钉钉企业应用权限 | 高 | 提前申请，不等此项上线主闭环 |
| 工程部历史数据质量 | 高 | 先定数据模板标准，交付前对齐格式，批次追踪支持整批回滚 |

---

## 实施保障

> 本章不扩展功能范围，只明确权限、接口规范、事件追踪、验收标准、回滚开关、测试要求和外部依赖，防止实施阶段跑偏。

### 1. 权限矩阵

系统目前只有 admin / 普通用户两个角色，无法满足新模块的访问控制需求。需在 `users` 表新增 `role` 字段（枚举：`admin` / `staff` / `manager` / `viewer`），通过 Alembic migration 将现有 `is_admin=true` 迁移为 `role='admin'`，其余迁移为 `role='staff'`。

| 功能 | admin | staff（业务员） | manager（项目负责人） | viewer（管理层只读） |
|------|:-----:|:---------------:|:---------------------:|:--------------------:|
| 创建 / 取消报价任务 | ✅ | ✅ 自己 | ❌ | ❌ |
| 查看报价任务 | ✅ 全部 | ✅ 自己 | ❌ | ❌ |
| 创建 ClientInquiry | ✅ | ✅ | ❌ | ❌ |
| 查看 ClientInquiry | ✅ 全部 | ✅ 自己 | ❌ | ❌ |
| 手动创建 ExecutionTask | ✅ | ❌ | ❌ | ❌ |
| 查看 ExecutionTask | ✅ 全部 | ❌ | ✅ 分配给自己的 | ❌ |
| 更新 ExecutionTask | ✅ | ❌ | ✅ 分配给自己的 | ❌ |
| 取消 ExecutionTask | ✅ | ❌ | ❌ | ❌ |
| 录入会议纪要 / 确认草稿任务 | ✅ | ✅ | ✅ | ❌ |
| 查看驾驶舱 | ✅ | ❌ | ❌ | ✅ |
| 管理知识库 / 用户 | ✅ | ❌ | ❌ | ❌ |

### 2. 用户与钉钉账号映射

任务推送若只往群发消息，负责人很容易忽略。需要绑定个人钉钉账号才能 @ 到人。

**新增字段**：`users.dingtalk_user_id`（VARCHAR 64, nullable），Alembic migration 新增。

**推送路由策略**：
- 有 `dingtalk_user_id`：发送 ActionCard 类型消息并 @负责人
- 无 `dingtalk_user_id`：推送到任务专用群 Webhook（`DINGTALK_TASK_WEBHOOK`，与报价审核群的 `DINGTALK_WEBHOOK` **物理分离**，避免互相污染）
- 推送失败：60 秒后重试一次；仍失败则写入 `execution_task_events`（event_type=push_failed）并继续，不阻断任务创建
- 推送记录：每次推送无论成功失败均写入 `execution_task_events`

> 新增配置项 `DINGTALK_TASK_WEBHOOK`，区别于现有报价推送用的 `DINGTALK_WEBHOOK`。

### 3. 事件流水表

`execution_task_events` 已在阶段 3 中列出。对称补充 `client_inquiry_events`：

```
client_inquiry_events:
- id, created_at
- inquiry_id:   外键 → client_inquiries.id
- event_type:   created | responded | converted | abandoned | note_added | push_failed
- operator_id:  外键 → users.id（NULL 表示系统自动触发）
- notes:        备注
```

统一原则：事件只追加不修改，event_type 枚举固定，operator_id=NULL 表示系统自动动作。

### 4. 阶段验收标准（Definition of Done）

| 阶段 | 完成定义 |
|------|---------|
| 阶段 0 | `/login` 可正常登录；旧 `/index.html`、`/admin.html` 仍可访问；刷新 `/admin/dashboard` 不出现 FastAPI 404 |
| 阶段 1 | `GET /api/v1/admin/dashboard/quote-speed` 有数据；管理员看到三类耗时分区趋势折线图 |
| 阶段 2 | 创建报价任务时自动生成 ClientInquiry 记录；响应速度面板标注数据起始日期且不为空 |
| 阶段 3 | ExecutionTask 可创建、分配、完成、取消；逾期动态计算与数据库一致；Celery beat 已纳入 `start_all.ps1`；钉钉推送在分配后可发出 |
| 阶段 4a | 录入会议纪要后生成草稿列表；确认后 ExecutionTask 正确写入；钉钉推送给负责人成功 |
| 阶段 4b | 上传音频后返回转写文本，自动进入草稿提取流程 |
| 阶段 5 | 批量导入后 RAG 评测 Hit@K ≥ 导入前基线；每条来源可追溯到 source_batch_id |

### 5. 功能开关与回滚策略

新功能上线出问题时可快速关闭，不影响现有报价系统。在 `.env` 中新增以下开关（默认 `false`，逐阶段上线后置 `true`）：

```
FEATURE_DASHBOARD=false         # 驾驶舱面板（阶段 1-3）
FEATURE_CLIENT_INQUIRY=false    # 响应速度追踪（阶段 2）
FEATURE_EXECUTION=false         # 执行任务模块（阶段 3-4）
FEATURE_MEETING_AI=false        # AI 会议纪要提取（阶段 4a）
FEATURE_VITE_FRONTEND=false     # Vite 新前端（阶段 0，稳定后置 true）
```

开关在启动时读取，修改需重启服务。未启用的功能接口返回 `HTTP 503 Feature not enabled`，前端路由守卫同步屏蔽对应入口。

### 6. 测试清单

遵循现有测试规范（pytest + SQLite 测试库，不访问真实外部服务，mock Webhook）：

**后端最小覆盖**：
- `execution_tasks`：status 非法值拒绝；逾期动态计算（有 due_at 无 completed_at → overdue；有 completed_at → not overdue）
- `client_inquiries`：创建报价任务时自动生成记录；inquiry_time 默认为创建时间；first_response_time 自动填入
- 权限边界：staff 创建 ExecutionTask → 403；manager 更新他人任务 → 403；viewer 访问非驾驶舱接口 → 403
- 驾驶舱聚合：有报价记录时三类耗时接口返回非空且分区数据
- 钉钉推送：mock Webhook，验证调用时机与 payload 结构；推送失败后写入 event_type=push_failed

**业务流手工验收**：
- 创建报价 → ClientInquiry 自动生成 → 响应速度看板统计更新
- 会议纪要录入 → AI 草稿生成 → 人工确认选择负责人 → ExecutionTask 写入 → 钉钉推送
- 逾期场景：due_at 设为近未来 → Celery beat 触发 → 验证钉钉提醒发出且 event 记录写入

### 7. 数据字典（枚举值固化）

所有枚举值在后端 Pydantic schema 中定义为 `Literal` 类型，前端从同名常量文件读取，禁止字符串硬编码散落在业务代码中。

```
client_inquiries.source:
  wechat | phone | onsite | referral | other

client_inquiries.status:
  pending | responded | converted | abandoned
  状态转移：
    pending     → responded:  报价任务创建时系统自动置（first_response_time 同步写入）
    responded   → converted:  confirm_push 成功后系统自动置
    pending/responded → abandoned: 手动 PATCH status=abandoned

execution_tasks.source:
  meeting | quote | manual

execution_tasks.status:
  pending | in_progress | done | cancelled

task_drafts.status:
  pending_review | accepted | rejected

meeting_notes.source:
  manual | audio | dingtalk

knowledge_candidates.data_type:
  material | technique | case | pricing_sample | risk_rule

knowledge_candidates.target_type:
  material | rag_doc | case | pricing_sample

users.role:
  admin | staff | manager | viewer
```

### 8. 外部依赖准备清单

| 依赖 | 用于阶段 | 需准备内容 | 建议启动时间 |
|------|---------|-----------|------------|
| 钉钉任务通知 Webhook | 3 | 建"任务通知"专用群、创建机器人、获取 `DINGTALK_TASK_WEBHOOK` URL | Phase 3 开始前 1 周 |
| 语音转写 API（讯飞 / 阿里云）| 4b | 注册账号、实名认证、申请 AppID/Secret、充值测试额度、确认并发限制 | Phase 4a 开始时（提前 2 周）|
| 钉钉企业应用（会议记录权限）| 4c | 企业管理员审批、提交应用审核材料、准备测试会议样本、确认 API 调用频率限制 | Phase 4a 开始时（审批周期不可控，越早越好）|

---

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-05-14 | 初版创建 | AI 辅助规划 |
| 2026-05-14 | v2 更新：Vite 分批迁移策略、ClientInquiry 嵌入报价流程、ExecutionTask 命名、逾期动态计算、草稿确认层、知识导入批次追踪、指标口径定义 | Codex 审查 + Claude 确认 |
| 2026-05-14 | v3 更新：修正 QuoteJob 字段（无 started_at，使用 duration_ms）、人工确认耗时口径（confirmed_at fallback QuoteHistory）、ClientInquiry SLA 拆分动态计算、ExecutionTask 无硬删除、知识导入新增落库目标追踪字段（target_type/target_id/snapshot_id）、Vite SPA fallback 路由配置 | Codex 审查 v3 + Claude 确认 |
| 2026-05-14 | v4 更新：ClientInquiry.first_response_time 改为报价任务创建时自动写入（创建即代表接单）；阶段 3 新增钉钉任务推送通知（复用现有 Webhook，无需企业权限）、Celery beat 逾期扫描与钉钉提醒、可选 execution_task_events 审计表 | Codex 审查 v4 + Claude 确认 |
| 2026-05-14 | v5 更新：新增"实施保障"章节（权限矩阵、用户钉钉映射、事件流水表、验收标准、功能开关、测试清单、数据字典、外部依赖准备清单）；修正 SLA 阈值存储策略、ClientInquiry 历史数据边界标注、Celery beat 为独立进程说明、Phase 4a suggested_assignee 不做自动模糊匹配 | Codex 审查 v5 + Claude 确认 |
