# 执行系统退役说明

## 结论

2026-07-31 起，产品不再提供原“执行系统”。退役范围包括：

- 执行任务；
- 会议纪要与任务草稿；
- 执行速度看板；
- `/admin/execution` 前端入口；
- `/api/v1/execution-tasks*`、`/api/v1/meetings*` 和 `/api/v1/admin/dashboard/execution-speed`；
- 对应服务、SQLAlchemy 模型、RBAC 模块、功能开关和专项 smoke/test。

`project_*` 项目进度、项目阶段、项目任务和任务成果证据是独立业务模块，不在退役范围内。

## 数据库处理

历史迁移 `20260514_0013`、`20260514_0014`、`20260514_0015` 必须保留，保证既有环境和新环境的 Alembic 链连续。

新增迁移 `20260731_0078` 按外键依赖顺序删除：

1. `task_drafts`
2. `meeting_note_revisions`
3. `meeting_notes`
4. `execution_task_events`
5. `execution_tasks`

升级迁移会永久删除这些表中的历史数据。任何环境执行 `alembic upgrade head` 前都必须先完成数据库备份；本次代码变更不直接升级当前运行数据库。

## 兼容性边界

- 旧执行系统 URL 统一返回 404，不再保留“功能未开启”的空壳。
- 旧环境变量即使仍残留在真实 `.env` 中，也不会再被应用读取。
- 经营总览、报价速度、响应速度、项目进度、成本库、预算项目和报价资料研判 Agent 不读取上述 5 张表，退役迁移不会触碰其数据。
- Alembic downgrade 只恢复空表结构，无法恢复 upgrade 时删除的历史记录。
