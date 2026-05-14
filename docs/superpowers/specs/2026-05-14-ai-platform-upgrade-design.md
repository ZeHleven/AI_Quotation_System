# 旗胜 AI 平台架构升级路线
> 创建日期：2026-05-14
> 最后更新：2026-05-14（含 Codex 审查意见 v2）
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
| AI 生成耗时 | `QuoteJob.started_at → QuoteJob.finished_at` | 工程侧（系统性能） |
| 人工确认耗时 | `QuoteJob.finished_at → QuoteHistory.created_at` | 管理层（业务效率） |
| 总交付耗时 | `QuoteJob.created_at → QuoteHistory.created_at` | 管理层（端到端） |

> 注：三个数字不要混在同一张图里，否则"系统很快但人工很慢"的真实问题会被平均数掩盖。

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
- inquiry_time: 客户咨询时间（默认为报价任务创建时间）
- first_response_time: 首次响应时间（可选填，后续自动化后从钉钉读取）
- responder_id: 接单人（关联 users 表，默认为创建报价的用户）
- client_name: 客户姓名/公司（可选）
- client_phone: 客户电话（可选）
- status: SLA 状态（待响应 / 已响应 / 已成单 / 已放弃）
- quote_job_id: 关联 QuoteJob（外键）
- notes: 备注
```

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
  - `POST/GET/PATCH/DELETE /api/v1/execution-tasks` — 任务 CRUD
  - `GET /api/v1/admin/dashboard/execution-speed` — 聚合统计
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
- suggested_assignee: AI 建议负责人
- suggested_due_at: AI 建议截止时间
- status: 草稿状态（pending_review / accepted / rejected）
- accepted_task_id: 确认后关联的 ExecutionTask.id
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
新增字段：
- source_batch_id: 批次标识（同一次导入共享）
- source_file: 来源文件名
- source_line: 原始行号/页码
- data_type: 数据类型（material / technique / case / pricing_sample / risk_rule）
- confidence: 置信度（0.0-1.0，人工标注或模型输出）
```

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

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-05-14 | 初版创建 | AI 辅助规划 |
| 2026-05-14 | v2 更新：Vite 分批迁移策略、ClientInquiry 嵌入报价流程、ExecutionTask 命名、逾期动态计算、草稿确认层、知识导入批次追踪、指标口径定义 | Codex 审查 + Claude 确认 |
