# BIZ-2w-2 RAG 同步状态时间口径误报修复

> 日期：2026-05-28  
> 状态：已完成代码层验证，并已通过当前环境手工验收（2026-05-28）  
> 范围：成本库后台 RAG 同步状态提示  
> 边界：不新增 Alembic，不改报价规则、价格口径、RAG 同步动作、N8N/Dify 或成本库 active 规则。

## 问题

成本数据库页面显示：

`RAG 同步状态：有更新未同步 · active 195 条 · 最近成功 2026-05-28 09:15:24`

但实际最近一次同步记录为 `success`，且 `requested_count=195 / synced_count=195`。

根因是状态判断直接比较了两个不同时间来源：

- `cost_items.updated_at`：由数据库 `func.now()` 写入，使用数据库本地时间口径。
- `cost_rag_sync_runs.finished_at`：由应用 `_utcnow()` 写入，使用 UTC 口径。

当数据库本地时间和 UTC 相差 8 小时时，系统会把已经被最近成功同步覆盖的 active 更新误判为“晚于同步时间”。

## 修复

1. 后端 `cost_rag_sync_status_summary` 查询数据库 `NOW()` 与 `UTC_TIMESTAMP()` 的差值。
2. 将 active 成本库最新更新时间按数据库本地时区归一化为 UTC 后，再和同步记录完成时间比较。
3. 同步状态响应新增 `latest_active_updated_at_utc`，用于排查时间口径。
4. 前端成本库页的“最近成功”改为本地时间展示，避免把 UTC 时间直接显示成业务时间。

## 当前环境复核

修复后，当前真实环境状态为：

- `status=synced`
- `status_label=已同步`
- `active_count=195`
- `latest_active_updated_at=2026-05-28T09:46:53`
- `latest_active_updated_at_utc=2026-05-28T01:46:53+00:00`
- `latest_successful_run.finished_at=2026-05-28T09:15:24`

因此最近一次成功同步已经覆盖 active 成本库，不应继续提示“有更新未同步”。

## 验证

- `python -m pytest tests/test_cost_rag_sync_biz2c.py -q`：`10 passed, 1 warning`
- `python -m compileall app tests`：通过
- `cmd /c npm.cmd run build`：通过，仅保留 Vite chunk size 警告

## 手工验收结论

用户已使用 `outputs/biz2w2/biz2w2_acceptance_requirement.xlsx` 完成当前环境验收（2026-05-28）：

- 需求单可提交并进入预审。
- 预审单中已完成必要的人工修改。
- 多候选、偏离底价、无底价等阻断/风险项已按人工判断处理。
- 预审单已确认下发。
- 用户确认验收项均通过。

本次通过仅代表当前内网验证环境的 BIZ-2w-2 验收需求单闭环通过，不代表正式试运行或生产启用。

## 回滚方式

如需回滚，撤销 `app/services/cost_rag_sync.py` 的数据库时间归一化逻辑、撤销 `ai-web/src/App.vue` 同步状态时间展示变更即可；不涉及数据库结构回滚。
