# BIZ-2v-2 RAG 同步一致性与滞后提示

> 日期：2026-05-28  
> 状态：已完成代码层验证  
> 边界：不新增 Alembic，不改报价规则，不改价格口径，不改 N8N/Dify，不自动触发 RAG 同步

## 阶段目标

BIZ-2v-2 解决的是 active 成本库同步到 RAG 的可见性和失败口径问题：

- RAG 同步失败时，接口返回的 `synced_count` 必须为 0，不能显示为请求同步条数。
- 成本库后台能看到当前 active 成本库与最近成功 RAG 同步之间是否一致。
- 管理员能区分已同步、需同步、同步失败、从未同步和无 active 条目的状态。

## 本阶段不做

- 不做失败自动重试。
- 不做 RAG 数据版本表或 Milvus 版本追踪。
- 不做 RAG 检索质量评估。
- 不自动同步 RAG。
- 不写入或改动成本条目数据。
- 不改变报价时的成本库匹配、底价兜底、漏项检测、AI 前置成本参考和无底价 draft 沉淀规则。

## 涉及文件

- `app/services/cost_rag_sync.py`
  - 修正失败返回 `synced_count=0`。
  - 新增 `cost_rag_sync_status_summary`。
- `app/api/v1/cost_items.py`
  - 新增 `GET /api/v1/admin/cost-items/sync-rag/status`。
- `ai-web/src/App.vue`
  - 成本数据库页面新增 RAG 同步状态提示。
- `tests/test_cost_rag_sync_biz2c.py`
  - 补充失败返回、超时返回、状态摘要和权限测试。

## 状态判断口径

| 状态 | 含义 | 是否建议同步 |
|------|------|--------------|
| `synced` | 最近成功同步数量等于当前 active 数量，且 active 成本条目没有晚于成功同步时间的更新 | 否 |
| `stale` | active 数量变化，或 active 条目更新时间晚于最近成功同步时间 | 是 |
| `failed` | 最近一次同步失败，或没有成功记录且最近记录失败 | 是 |
| `never_synced` | 有 active 成本条目，但没有成功同步记录 | 是 |
| `empty_active` | 当前没有 active 成本条目 | 否 |

## 权限口径

- 查看同步状态和同步记录：成本库查看权限及以上。
- 执行同步 active 到 RAG：成本审批权限及管理员。
- 普通 `staff` 不能访问完整成本库同步状态。

## 验证结果

- `python -m pytest tests/test_cost_rag_sync_biz2c.py -q`：`9 passed, 1 warning`
- `python -m compileall app tests`：通过
- `python -m pytest -q`：`253 passed, 5 warnings`
- `cmd /c npm.cmd run build`：通过，仅保留 Vite chunk size 警告

## 2026-05-28 补充修复：时间口径误报

BIZ-2w-2 审查中发现：`cost_items.updated_at` 使用数据库本地时间口径，而 `cost_rag_sync_runs.finished_at` 使用应用 UTC 口径，直接比较会导致“刚同步成功仍显示有更新未同步”的误报。已在 `docs/biz-2w-2-rag-sync-status-timezone-fix.md` 记录并修复：后端按数据库 `NOW()` 与 `UTC_TIMESTAMP()` 差值归一化 active 更新时间，前端将最近成功同步时间按本地时间展示。

## 回滚方式

如需回滚，删除本阶段新增状态接口、前端状态提示和 `cost_rag_sync_status_summary` 即可；同步记录表和成本库表结构未变，不需要数据库回滚。
