# BIZ-2v-1 报价下发与成本价权限安全加固

日期：2026-05-28

## 阶段目标

- 修复 `/confirm_push` 使用 `quote_job_id` 时的任务归属校验缺口。
- 收紧普通业务员的报价预审成本候选查询，保留必要 active 参考价能力，但不暴露完整成本库详情。
- 要求成本条目从 `draft` 启用为 `active` 时填写核定原因，增强状态流转审计。

## 不做事项

- 不改报价规则和价格口径。
- 不改成本匹配算法、不改多候选阻断规则。
- 不自动改价、合并、删除、撤回或启用成本条目。
- 不改 N8N、Dify、RAG 服务和 active 到 RAG 的同步逻辑。
- 不新增数据库结构，不新增 Alembic revision。

## 代码范围

- `AI_Middle_Office/app/api/v1/quote.py`
- `AI_Middle_Office/app/api/v1/cost_items.py`
- `AI_Middle_Office/app/schemas/cost_item.py`
- `AI_Middle_Office/app/services/cost_items.py`
- `AI_Middle_Office/tests/test_cost_db_biz2a.py`
- `AI_Middle_Office/tests/test_quote_preview_drafts_biz2q.py`
- `index.html`
- `ai-web/src/App.vue`

## 具体改动

1. `/confirm_push` 若携带 `quote_job_id`，必须先确认该任务存在且属于当前用户；管理员仍可处理全员任务。
2. 越权或不存在的 `quote_job_id` 返回“报价任务不存在”，避免任务枚举。
3. 普通 `staff` 调用 `/cost-items/quote-candidates` 时，只返回预审切换所需字段：条目 ID、名称、规格、单位、主参考价、价格类型、分类和更新时间。
4. 成本专项角色和管理员仍可通过候选接口获得完整成本条目信息。
5. 候选查询关键词最少 2 个字符，且普通候选搜索不再扫描 `notes` 字段。
6. 单条启用 active、批量核定 active 均要求填写核定原因，并写入 `cost_item_history.change_reason`。
7. Vite 成本库页面启用和批量核定 active 改为输入原因后提交。
8. 旧预审弹窗切换候选时对 2 字以下关键词给出前端提示。

## 验收口径

- 普通用户不能用他人的 `quote_job_id` 完成下发，也不能把他人的预审草稿标记为已下发。
- 普通业务员仍可在预审阶段搜索并选用必要 active 成本候选。
- 普通业务员候选接口不返回 notes、拆分价、创建人等完整成本库字段。
- 成本专项角色和管理员候选接口仍保留完整成本条目字段。
- draft 启用 active 时，无核定原因会返回 `REASON_REQUIRED`。
- 原有占位未补价阻断、多候选未确认阻断、报价草稿恢复和无底价 draft 沉淀规则不变。

## 验证记录

- `C:\Users\12521\miniconda3\python.exe -m pytest tests/test_cost_db_biz2a.py tests/test_quote_preview_drafts_biz2q.py tests/test_confirm_push_schema.py -q`
  - 结果：`28 passed, 5 warnings`
- `C:\Users\12521\miniconda3\python.exe -m pytest tests/test_cost_db_biz2a.py tests/test_quote_preview_drafts_biz2q.py tests/test_confirm_push_schema.py tests/test_quote_confirm_push_biz2m.py tests/test_quote_jobs.py tests/test_quote_feedback.py -q`
  - 结果：`67 passed, 5 warnings`
- `C:\Users\12521\miniconda3\python.exe -m pytest -q`
  - 结果：`248 passed, 5 warnings`
- `C:\Users\12521\miniconda3\python.exe -m compileall app tests`
  - 结果：通过
- `cmd /c npm.cmd run build`（`ai-web`）
  - 结果：通过；Vite 仍提示单个 chunk 超过 500 kB，为既有构建体积提示
- 旧 `index.html` inline script 语法检查
  - 结果：`checked 1 inline scripts`

## 风险和回滚

- 候选关键词门槛提高后，业务员输入单字搜索会被提示补充关键词；可回滚为只在前端提示、后端继续兼容，但不建议。
- 普通业务员候选字段收紧后，预审切换仍可用，但看不到完整拆分价；完整成本详情仍由成本专项角色和管理员查看。
- 本阶段无数据库结构变更，回滚为代码级回退。
