# BIZ-3a 企业内部工程项目进度中台基础版

更新时间：2026-06-01

## 目标

BIZ-3a 建立平台内的工程项目进度事实源，先解决“谁负责、做到哪、卡在哪、下一步是谁”的透明化问题。

首版只做平台内项目、阶段、任务、2580 自动进度、阻塞、日志、基础看板和权限控制；不接钉钉/企微，不自动关联报价任务，不由 AI 自动生成周报。

## 功能开关

- `FEATURE_PROJECT_PROGRESS`
- `FEATURE_DASHBOARD_PROJECT`

关闭 `FEATURE_PROJECT_PROGRESS` 时，项目进度 API 返回 `404 NOT_FOUND`，前端隐藏或提示项目进度功能未开启。关闭 `FEATURE_DASHBOARD_PROJECT` 时，项目看板返回 `403 FEATURE_DISABLED`。

## 数据结构

新增 Alembic：

- `20260601_0026_add_project_progress.py`

新增表：

- `projects`
- `project_stages`
- `project_tasks`
- `project_task_events`

默认项目阶段：

| 阶段 | 权重 | 默认责任岗位 |
|---|---:|---|
| 立项 | 5% | 商务/市场 |
| 报价 | 15% | 预算/成本 |
| 合同 | 10% | 商务/财务 |
| 设计深化 | 15% | 设计 |
| 材料确认 | 10% | 成本/采购/项目经理 |
| 采购 | 10% | 采购 |
| 施工 | 25% | 项目经理/施工 |
| 验收结算 | 10% | 项目经理/财务 |

权重合计固定为 100%。BIZ-3a 暂不提供模板配置页。

## 2580 进度规则

员工只更新任务动作，不直接填写百分比。

| 任务状态 | 进度 |
|---|---:|
| `todo` 未开始 | 0% |
| `started` 已开始 | 25% |
| `progressing` 进行中 | 50% |
| `submitted` 已提交/待确认 | 80% |
| `done` 已完成 | 100% |
| `blocked` 已阻塞 | 保持阻塞前进度 |
| `cancelled` 已取消 | 不参与阶段进度 |

阶段进度取未取消任务平均值；阶段无任务时按阶段自身状态映射。项目总进度按阶段权重加权汇总。

进度支持有原因回退。任务从 `started` / `progressing` / `submitted` 可逐级回退；`done` 需要项目管理权限回退。每次回退写入 `project_task_events`，记录操作人、回退前后状态和回退原因。

## 风险规则

项目风险自动计算：

- 有阻塞任务：`blocked`
- 有逾期未完成任务：`delayed`
- 未来 2 天内到期且进度低于 80%：`warning`
- 无异常：`normal`

阻塞优先级高于延期。

阻塞任务会保留 `blocked_reason` 和 `next_action`。解除阻塞必须填写解决说明，系统写入 `task_unblocked` 事件，记录谁在何时如何解除阻塞。

## API

项目：

- `GET /api/v1/admin/projects`
- `POST /api/v1/admin/projects`
- `GET /api/v1/admin/projects/{project_id}`
- `PATCH /api/v1/admin/projects/{project_id}`
- `POST /api/v1/admin/projects/{project_id}/start`
- `POST /api/v1/admin/projects/{project_id}/pause`
- `POST /api/v1/admin/projects/{project_id}/complete`
- `POST /api/v1/admin/projects/{project_id}/cancel`

阶段：

- `GET /api/v1/admin/projects/{project_id}/stages`
- `PATCH /api/v1/admin/project-stages/{stage_id}`

任务：

- `GET /api/v1/admin/projects/{project_id}/tasks`
- `POST /api/v1/admin/projects/{project_id}/tasks`
- `PATCH /api/v1/admin/project-tasks/{task_id}`
- `POST /api/v1/admin/project-tasks/{task_id}/start`
- `POST /api/v1/admin/project-tasks/{task_id}/progress`
- `POST /api/v1/admin/project-tasks/{task_id}/submit`
- `POST /api/v1/admin/project-tasks/{task_id}/complete`
- `POST /api/v1/admin/project-tasks/{task_id}/block`
- `POST /api/v1/admin/project-tasks/{task_id}/unblock`
- `POST /api/v1/admin/project-tasks/{task_id}/cancel`
- `GET /api/v1/admin/project-tasks/my`

日志与看板：

- `GET /api/v1/admin/projects/{project_id}/events`
- `GET /api/v1/admin/dashboard/projects`

项目人员：

- `GET /api/v1/admin/projects/users`

## 前端入口

新增 Vite 路由：

- `/admin/projects`
- `/admin/projects/:id`
- `/admin/project-tasks/my`
- `/admin/dashboard` 的“项目进度”标签页

页面能力：

- 项目列表筛选：项目状态、风险状态、关键词
- 项目详情：摘要、阶段进度、岗位任务、项目动态
- 我的任务：本人负责的项目任务，支持开始、推进、提交、阻塞
- 项目经理/管理员：创建项目、创建任务、确认完成

## 权限

新增角色：

- `project_viewer`
- `project_member`
- `project_manager`

兼容旧角色：

- `staff` 默认具备 `project_viewer` / `project_member`
- `manager` 默认具备 `project_manager`
- `admin` / `system_admin` 默认具备项目全量管理能力

普通成员只能更新自己负责的项目任务。任务从 `submitted` 到 `done` 必须由项目经理、管理员或具备项目管理权限的用户确认。

## 验证

代码层验证：

- `C:\Users\12521\miniconda3\python.exe -m compileall app`
- `C:\Users\12521\miniconda3\python.exe -m pytest tests\test_project_progress_biz3a.py tests\test_rbac_phase0.py tests\test_dashboard_phase1.py -q`
- `npm.cmd run build`

当前结果：

- BIZ-3a 专项与相关回归：`15 passed, 1 warning`
- Vite build：通过

警告为 pytest cache 写入受限，不影响测试结论。
