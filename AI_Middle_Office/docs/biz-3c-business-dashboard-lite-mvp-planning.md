# BIZ-3c 经营驾驶舱轻量 MVP 执行规划

> 状态：规划已制定；BIZ-3c-1 后端只读聚合接口、BIZ-3c-2 前端经营总览标签页、BIZ-3c-3 趋势图 + 风险规则精化均已完成代码层验证（2026-06-02）。BIZ-3c-1 / BIZ-3c-2 / BIZ-3c-3 不新增数据库结构、不新增 Alembic；完整经营模型留到 BIZ-3d。
> 定位：在成本数据库、报价功能、项目进度功能 MVP 都已完成后，先做一个基于现有数据的只读经营驾驶舱轻量 MVP，用于内网试运行和管理层汇报；完整合同、回款、项目成本、毛利模型留到后续 BIZ-3d。

## 一、为什么现在做

当前系统已有三条可运行主链路：

1. **成本数据库**：`cost_items.active` 已成为报价成本主库，具备导入、状态流转、RAG 同步、权限、审计、状态与流向、治理报告能力。
2. **报价功能**：已覆盖需求单解析/标准化、异步报价、成本库前置参考、AI 预审、人工改价、草稿、确认下发、无底价 draft 沉淀、证据链和大清单完整性保障。
3. **项目进度功能**：已覆盖项目、阶段、任务、2580 进度、EPC 模板、成果证据、缺证据汇总、A 级关键节点硬门禁与放行审计。

经营驾驶舱轻量 MVP 的目的不是新增业务流程，而是把这些已存在的数据集中展示出来，回答管理层和试运行负责人最关心的几个问题：

- 今天系统整体是否健康？
- 报价任务是否顺畅？卡在哪里？
- 成本库是否可用？RAG 是否同步？
- 项目进度是否有阻塞、缺证据、关键节点放行？
- 试运行还有哪些风险和待处理事项？

## 二、阶段命名与边界

本规划将“经营驾驶舱”拆成两层：

| 阶段 | 名称 | 目标 | 数据结构 |
|---|---|---|---|
| BIZ-3c | 经营驾驶舱轻量 MVP | 复用现有成本、报价、项目进度和运维数据，提供只读总览 | 原则上不新增表 |
| BIZ-3d | 完整经营驾驶舱 | 增加合同、回款、项目成本、毛利、逾期应收等经营指标 | 需要新增合同/回款/成本等表 |

BIZ-3c 不做以下内容：

- 不新增合同、回款、项目成本、毛利模型。
- 不做财务系统、合同系统或外部软件对接。
- 不做复杂 BI 报表或自定义图表配置。
- 不开放敏感明细导出。
- 不把试运行风险自动判定为生产准入结论，只做提示和汇总。

## 三、BIZ-3c 目标用户

| 角色 | 使用目标 | 可见范围 |
|---|---|---|
| 老板 / 管理层 | 快速判断系统试运行和业务状态 | 汇总指标、趋势、风险，不看成本敏感明细 |
| 管理员 | 定位报价、成本库、项目进度和系统健康问题 | 汇总 + 可跳转到现有管理页面 |
| 成本部负责人 | 关注成本库 active、draft、无底价、RAG 同步和治理风险 | 成本库汇总、待处理数量、同步状态 |
| 项目管理负责人 | 关注项目进度、阻塞、缺证据、关键节点放行 | 项目进度汇总、缺证据和阻塞任务数量 |

权限口径：

- 轻量 MVP 先复用现有 dashboard 查看权限。
- 成本敏感明细不在驾驶舱直接展开，只展示数量、状态和跳转入口。
- 若后续给老板单独开只读账号，可使用 `viewer` 或新增更细角色，但本阶段不强依赖。

## 四、数据来源

### 1. 报价运行数据

优先复用：

- `quote_jobs`
- `quote_job_events`
- `quote_history`
- `quote_preview_drafts`
- `quote_feedback`
- `quote_cost_evidence`
- `quote_job_requirement_rows`

可展示指标：

- 今日 / 本周 / 本月报价任务数。
- 成功、失败、取消、超时、草稿、待预审数量。
- 平均报价耗时、失败率、打回率。
- 已确认下发数量和总价概览。
- 无底价 draft 沉淀数量。
- 大清单完整性异常数量。

### 2. 成本库运行数据

优先复用：

- `cost_items`
- `cost_item_history`
- `cost_rag_sync_runs`
- `cost_access_audit_logs`
- 成本库治理报告脚本输出（只作为提示来源，不作为强依赖）

可展示指标：

- `active` / `draft` / `archived` 数量。
- 由无底价报价沉淀的 draft 数量。
- 最近一次 RAG 同步状态、成功时间、active 同步数量。
- 成本库审计事件数量。
- 待成本部处理的高风险/中风险数量（若治理报告存在）。

### 3. 项目进度运行数据

优先复用：

- `projects`
- `project_stages`
- `project_tasks`
- `project_task_events`
- `project_task_evidences`

可展示指标：

- 项目总数、进行中项目数、已完成项目数。
- 阻塞任务数、逾期/停滞任务数（如当前模型可判断）。
- 缺成果证据任务数。
- `complete_required` 任务数。
- A 级硬门禁放行事件数。
- 无证据完成软提醒事件数。

### 4. 系统健康与试运行风险

优先复用：

- `/health/live`
- `/health/ready`
- `/api/v1/admin/ops/dashboard`
- 现有功能开关状态
- 当前文档记录的待验收/待定项

可展示指标：

- FastAPI、数据库、Celery、Redis、RAG、MinIO 等健康状态。
- 当前功能开关是否打开。
- 当前未完成但不阻塞 MVP 的事项。
- 生产 Runbook 是否已准备。

## 五、页面结构

建议在现有 Vite `/admin/dashboard` 中新增一个标签页：

> **经营总览**

页面不做营销式大屏，采用安静的管理台布局，分为四个区块。

### 1. 顶部状态条

展示：

- 系统状态：正常 / 有风险 / 需处理。
- 数据更新时间。
- 当前环境：内网试运行 / 生产待上线。
- 数据库 head：`20260601_0028`。
- 入口按钮：报价运营、成本库、项目进度、运维面板。

### 2. 核心指标卡

建议首版 8 张卡：

| 卡片 | 指标 |
|---|---|
| 今日报价任务 | 今日创建、成功、失败、草稿 |
| 报价下发 | 今日确认下发数、下发总价 |
| 成本库状态 | active、draft、archived |
| RAG 同步 | 最近同步状态、同步时间 |
| 无底价待审 | 无底价沉淀 draft 数 |
| 项目进度 | 进行中项目、阻塞任务 |
| 成果证据 | 缺证据任务、硬门禁任务 |
| 系统健康 | ready 状态、关键服务异常数 |

### 3. 风险与待处理

按优先级展示：

- 报价失败/超时。
- 预审草稿长时间未处理。
- 无底价 draft 待审核。
- RAG 未同步或同步失败。
- 项目阻塞任务。
- A 级硬门禁放行后仍未补证据。
- 当前仍待人工验收的补强项。

每条风险给出：

- 风险名称。
- 数量。
- 严重级别。
- 建议动作。
- 跳转入口。

### 4. 趋势与分布

轻量 MVP 首版只做基础趋势，不做复杂 BI：

- 近 7 / 30 天报价任务趋势。
- 近 7 / 30 天报价成功率。
- 成本库 active / draft 趋势（若无历史快照，先显示当前分布）。
- 项目任务状态分布。
- 缺证据任务分布。

## 六、后端接口规划

建议新增只读聚合接口：

```text
GET /api/v1/admin/dashboard/business-lite?range=today|week|month|last_30_days
```

返回结构建议：

```json
{
  "range": "last_30_days",
  "generated_at": "2026-06-02T10:00:00+08:00",
  "environment": {
    "database_head": "20260601_0028",
    "mode": "internal_trial",
    "overall_status": "ok"
  },
  "quote": {
    "task_count": 0,
    "success_count": 0,
    "failed_count": 0,
    "draft_count": 0,
    "pushed_count": 0,
    "avg_duration_ms": null
  },
  "cost": {
    "active_count": 0,
    "draft_count": 0,
    "archived_count": 0,
    "rag_status": "unknown",
    "last_success_sync_at": null
  },
  "project_progress": {
    "project_count": 0,
    "active_project_count": 0,
    "blocked_task_count": 0,
    "missing_evidence_task_count": 0,
    "complete_required_task_count": 0,
    "bypass_gate_event_count": 0
  },
  "risks": [
    {
      "key": "cost_rag_not_synced",
      "severity": "warning",
      "title": "RAG 同步可能滞后",
      "count": 1,
      "action": "进入成本库同步状态查看"
    }
  ]
}
```

接口原则：

- 只读。
- 不写数据库。
- 不生成新的审计记录。
- 不展示成本敏感明细。
- 聚合失败时局部降级，尽量返回其他区块数据。

## 七、功能开关

建议新增：

```text
FEATURE_DASHBOARD_BUSINESS_LITE=false
```

原因：

- 与旧设计中的完整 `FEATURE_DASHBOARD_BUSINESS` 区分。
- 轻量 MVP 不包含合同、回款、项目成本、毛利模型。
- 后续完整经营驾驶舱上线时，可保留轻量总览，或迁移到完整开关下。

前端可见规则：

- 开关关闭：不显示“经营总览”标签页。
- 开关打开：有 dashboard 查看权限的用户可进入。

## 八、实施步骤

### BIZ-3c-0：规划确认

本阶段，即本文档。

交付物：

- 明确轻量 MVP 目标、边界、数据来源、页面结构、接口结构和验收标准。
- 不改代码。
- 不新增 Alembic。

### BIZ-3c-1：后端只读聚合接口

当前状态：已完成代码层验证（2026-06-02）。

目标：

- 新增 `business_lite_dashboard` 服务。
- 新增 `/api/v1/admin/dashboard/business-lite` 接口。
- 复用现有 dashboard 权限。
- 聚合报价、成本库、项目进度和系统健康摘要。
- 新增 `FEATURE_DASHBOARD_BUSINESS_LITE` 开关，并纳入 RBAC dashboard 可用状态。
- 默认 `range=last_30_days`；沿用既有 dashboard 范围 `today` / `week` / `month` / `last_30_days`。
- 首版只返回汇总、风险和跳转入口，不展示成本单价明细。
- 分块聚合，局部失败时返回 `section_errors` 并保留其他区块数据。

建议测试：

- 聚合接口 feature flag 关闭时返回 403 或明确错误码。
- feature flag 打开时返回完整结构。
- 无数据时返回 0 / null，不报错。
- 成本 RAG 最近同步状态可正确透出。
- 项目硬门禁放行事件数可统计。
- dashboard viewer 可访问，staff 无 dashboard 查看权限时返回 `PERMISSION_DENIED`。

已验证：

- `C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office/tests/test_business_lite_dashboard_biz3c.py`：`4 passed, 1 warning`。
- `C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office/tests/test_dashboard_phase1.py AI_Middle_Office/tests/test_business_lite_dashboard_biz3c.py`：`8 passed, 1 warning`。
- `C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office/tests/test_business_lite_dashboard_biz3c.py AI_Middle_Office/tests/test_cost_rag_sync_biz2c.py AI_Middle_Office/tests/test_cost_audit_biz2v3.py AI_Middle_Office/tests/test_project_progress_biz3a.py`：`30 passed, 6 warnings`。

### BIZ-3c-2：前端经营总览标签页

当前状态：已完成代码层验证（2026-06-02）。

目标：

- 在 `/admin/dashboard` 新增“经营总览”标签页。
- 展示顶部状态条、核心指标卡、风险与待处理。
- 卡片可跳转到现有功能页。
- 首版暂不做趋势图，避免图表库和复杂移动端适配拖慢 MVP；趋势图后移到 BIZ-3c-3。

详细规划：

1. 入口与开关
   - 在现有 Vite `/admin/dashboard` 的 `el-tabs` 中新增 `经营总览` 标签页，建议排在“报价速度”之前。
   - 前端新增 `businessDisabled` 状态，接口返回 `FEATURE_DISABLED` 时禁用该标签页。
   - `loadDashboards()` 增加对 `/api/v1/admin/dashboard/business-lite` 的请求；若开关关闭，不影响其他看板加载。
   - 可用标签切换逻辑加入 `business`，优先显示经营总览；若开关关闭则回退到报价速度、响应速度等现有可用标签。

2. 顶部状态条
   - 展示整体状态：`ok` / `warning` / `degraded`。
   - 展示数据更新时间 `generated_at`。
   - 展示环境模式：`internal_trial` / `public_access`。
   - 展示数据库 head。
   - 展示快捷入口：旧报价工作台、成本数据库、项目进度、运维接口。

3. 核心指标卡
   - 报价任务：`quote.task_count`，附成功/失败/草稿。
   - 报价下发：`quote.pushed_count`，附下发总价。
   - 成本库状态：`cost.active_count` / `cost.draft_count` / `cost.archived_count`。
   - RAG 同步：`cost.rag_status_label`，附最近成功同步时间。
   - 无底价待审：`cost.no_cost_draft_count`。
   - 项目进度：`project_progress.active_project_count`，附阻塞任务。
   - 成果证据：`project_progress.missing_evidence_task_count`，附硬门禁任务。
   - 系统健康：`environment.overall_status`，附局部降级区块数。

4. 风险与待处理
   - 使用后端 `risks` 数组直接展示，不在前端重新推导业务风险。
   - 每条展示：风险名称、数量、严重级别、建议动作。
   - 若存在 `target_path`，显示跳转按钮。
   - 若风险为空，展示“暂无待处理风险”空状态。

5. 局部降级提示
   - 若 `section_errors` 非空，在页面顶部显示 warning alert。
   - 不因为单个区块失败阻断整个页面。

6. 敏感信息控制
   - 仅展示成本数量、状态和同步结果。
   - 不展示成本单价、成本项名称、成本条目明细、导出入口。

7. 不做项
   - 不引入 ECharts 或新的图表库。
   - 不做趋势图、分布图和复杂 BI 配置。
   - 不新增页面路由；仅复用 `/admin/dashboard`。
   - 不新增后端接口或数据库结构。
   - 不改报价、成本库、项目进度业务逻辑。

建议验收：

- 页面加载无 console error。
- 手机/桌面不溢出。
- 数据为空时有空状态。
- 风险列表可读，不泄露敏感成本明细。
- `FEATURE_DASHBOARD_BUSINESS_LITE=false` 时，经营总览标签页显示为禁用或不作为默认可用页，其他看板不受影响。
- `FEATURE_DASHBOARD_BUSINESS_LITE=true` 时，`/admin/dashboard` 可加载经营总览。
- 默认时间范围为 `last_30_days`，切换 `today/week/month/last_30_days` 后经营总览会刷新。
- 指标卡能展示报价、成本库、项目进度、系统健康四类摘要。
- 风险列表能展示后端 `risks`，并可跳转到对应入口。
- `section_errors` 出现时页面提示局部降级，不空白、不崩溃。
- `ai-web npm run build` 通过。

已验证：

- 前端已在 Vite `/admin/dashboard` 增加“经营总览”标签页，默认优先展示；开关关闭时会降级到其他可用看板。
- 页面已展示 8 个经营指标卡、风险与待处理列表、运行摘要、局部降级告警和跳转入口。
- 前端只消费 `GET /api/v1/admin/dashboard/business-lite` 的汇总数据与 `risks`，不在页面二次计算复杂业务规则，不展示成本敏感明细。
- `ai-web` 执行 `npm.cmd run build` 通过。
- 后端聚焦测试 `AI_Middle_Office/tests/test_business_lite_dashboard_biz3c.py` 执行结果为 `4 passed, 1 warning`。
- 当前环境未完成浏览器可视化截图复验，原因是本线程未暴露可用的 in-app browser 控制工具；已用构建和代码层检查完成本轮验证。

### BIZ-3c-3：趋势图 + 风险规则精化

当前状态：已完成代码层验证（2026-06-02）。

目标：

- 把 BIZ-3c-1 / BIZ-3c-2 已落地的“经营总览”从静态指标卡升级为可观察的轻量趋势与分布视图。
- 精化 `hard_gate_bypassed_missing_evidence`：从“硬门禁放行事件数”升级为“放行事件后当前仍无 active 成果证据”的精确风险。
- 继续保持轻量 MVP 边界：不新增数据库结构、不新增 Alembic、不引入完整合同/回款/毛利模型、不展示成本敏感单价明细。

执行原则：

1. **后端优先补数据合同**  
   在 `GET /api/v1/admin/dashboard/business-lite` 原响应中追加趋势/分布字段，保持旧字段兼容；前端仍只消费一个经营总览接口。

2. **首版不用重型图表库**  
   由于 Vite 当前未引入 ECharts，本阶段先用 Element 表格 + CSS 条形图展示趋势/分布。这样能先完成管理层可读和内网验收，避免图表依赖拖慢 MVP。

3. **风险规则由后端统一判断**  
   前端只展示后端 `risks`，不在页面重复实现复杂业务判断；精确风险字段写入 `project_progress.hard_gate_bypassed_missing_evidence_count`。

4. **所有新增统计只读聚合**  
   本阶段不写数据库、不回填、不新增定时任务。趋势来自现有 `created_at` / `updated_at` / 事件时间字段。

后端数据合同新增：

- `quote.daily_trend[]`
  - `date`
  - `task_count`
  - `success_count`
  - `failed_or_timeout_count`
  - `pushed_count`
- `cost.status_distribution[]`
  - `status`
  - `count`
  - `label`
- `cost.source_distribution[]`
  - `source`
  - `count`
  - `label`
- `project_progress.daily_trend[]`
  - `date`
  - `bypass_gate_event_count`
  - `bypassed_missing_evidence_count`
  - `soft_reminder_event_count`
- `project_progress.hard_gate_bypassed_missing_evidence_count`
  - 口径：统计当前时间范围内发生过 `task_completed_bypass_gate` 的 A 级 `complete_required` 任务，且该任务当前仍没有 active 成果证据的去重任务数。
- `risks[]`
  - 将原 `project_hard_gate_bypass_events` 信息提示升级为 `hard_gate_bypassed_missing_evidence` 风险项。
  - 若放行后已补 active 证据，则不进入风险列表，只保留统计摘要。

前端新增展示：

1. **经营趋势区块**
   - 放在经营总览指标卡下方。
   - 左侧展示近 12 条报价趋势：报价任务、成功、失败/超时、已下发。
   - 右侧展示近 12 条项目证据趋势：硬门禁放行、放行后仍缺证据、软提醒完成。
   - 空数据时展示空状态，不影响风险列表和运行摘要。

2. **分布区块**
   - 展示成本库状态分布：active / draft / archived。
   - 展示成本库来源分布：manual / ai_suggested / import 等来源。
   - 展示项目状态与任务状态分布，可复用后端已有 `project_status_distribution` / `task_status_distribution`。

3. **硬门禁风险可读性**
   - 成果证据指标卡副文案增加“放行未补证据”数量。
   - 风险列表出现 `hard_gate_bypassed_missing_evidence` 时，标题和动作明确为“补证据”而不是泛泛查看放行原因。

测试范围：

- 后端专项测试补充：
  - 响应包含 `quote.daily_trend`、`cost.status_distribution`、`project_progress.daily_trend`。
  - 有放行事件且当前无 active 证据时，返回 `hard_gate_bypassed_missing_evidence_count >= 1` 且风险列表包含 `hard_gate_bypassed_missing_evidence`。
  - 给同一任务补 active 证据后，精确风险不再出现。
  - 旧风险、局部降级、权限和 feature flag 行为保持不变。
- 前端验证：
  - `npm.cmd run build` 通过。
  - 经营总览趋势/分布区块在空数据和有数据下不溢出。

验收标准：

- 不新增 Alembic，数据库 head 保持 `20260601_0028`。
- `FEATURE_DASHBOARD_BUSINESS_LITE=true` 时，经营总览可查看指标卡、趋势、分布、风险、运行摘要。
- `FEATURE_DASHBOARD_BUSINESS_LITE=false` 时，经营总览仍按 BIZ-3c-2 口径禁用或降级，不影响其他看板。
- `hard_gate_bypassed_missing_evidence` 不再只等同于放行事件总数，而是精确到“放行后当前仍缺 active 证据”的任务数。
- 页面不展示成本单价、成本明细和敏感导出入口。

已验证：

- 后端 `business-lite` 响应已新增 `quote.daily_trend`、`cost.status_distribution`、`cost.source_distribution`、`project_progress.daily_trend` 和 `project_progress.hard_gate_bypassed_missing_evidence_count`。
- 风险列表已将硬门禁风险精化为 `hard_gate_bypassed_missing_evidence`，仅统计放行后当前仍缺 active 证据的任务。
- 前端 `/admin/dashboard` 经营总览已新增报价趋势、项目证据趋势和分布概览，使用 Element + CSS 条形图呈现，不引入图表库。
- 后端聚焦测试 `AI_Middle_Office/tests/test_business_lite_dashboard_biz3c.py` 执行结果为 `5 passed, 1 warning`。
- 经营总览 / 成本 RAG 同步 / 成本审计 / 项目进度交叉依赖测试执行结果为 `31 passed, 6 warnings`。
- `ai-web` 执行 `npm.cmd run build` 通过。

### BIZ-3d：完整经营驾驶舱后续

触发条件：

- 轻量 MVP 试运行稳定。
- 项目进度数据持续维护。
- 合同、回款、项目成本字段口径确认。
- 管理层确实需要经营指标而不只是试运行总览。

后续才考虑：

- `contracts`
- `payments`
- `project_costs`
- `contract_adjustments`
- 经营数据导入批次和回滚。
- 毛利、回款率、逾期应收。
- 脱敏汇总和导出水印。

## 九、验收标准

BIZ-3c 轻量 MVP 完成时，应满足：

- 后端聚合接口返回 200，结构稳定。
- 前端 `/admin/dashboard` 可查看“经营总览”。
- 报价、成本库、项目进度、系统健康四类数据至少能展示当前值。
- 风险与待处理列表能指出当前试运行最重要的问题。
- 不新增经营数据表。
- 不展示成本敏感明细。
- 不影响现有报价、成本库、项目进度功能。
- 后端测试通过。
- `ai-web npm run build` 通过。
- 当前环境业务验收通过。

## 十、风险与控制

| 风险 | 控制 |
|---|---|
| 管理层误以为这是完整经营驾驶舱 | 页面和文档明确标注“轻量 MVP / 内网试运行总览” |
| 成本敏感数据泄露 | 只展示数量和状态，不展示单价明细 |
| 聚合口径与后续完整经营模型冲突 | 本阶段不计算毛利、回款率、合同额 |
| 数据为空导致页面难看 | 设计空状态和 0 值口径 |
| 读取多个模块导致接口慢 | 分块聚合，必要时局部降级 |
| 试运行风险规则过硬 | 首版只提示，不自动阻断业务 |

## 十一、当前推荐决策

建议立即进入：

> **BIZ-3c 轻量 MVP 进入小范围试运行观察；BIZ-3d 等完整经营口径确认后再启动**

原因：

- BIZ-3c-1 后端只读聚合接口已完成代码层验证。
- BIZ-3c-2 已形成可见经营总览：指标卡、风险列表、运行摘要、跳转入口和局部降级提示均已落地。
- BIZ-3c-3 已补趋势/分布图，并把 `hard_gate_bypassed_missing_evidence` 从“A 级硬门禁放行事件总数”精化为“放行后当前仍缺证据”的风险规则。
- 当前轻量 MVP 已满足内网试运行总览用途；完整合同、回款、项目成本、毛利模型需要业务口径和数据结构另行确认。

暂不进入完整 BIZ-3d，因为合同、回款、项目成本、毛利口径还没有进入真实稳定录入阶段，过早做完整经营模型会放大口径风险。
