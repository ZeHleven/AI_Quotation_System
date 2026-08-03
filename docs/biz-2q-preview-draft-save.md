# BIZ-2q 报价预审草稿保存与恢复

> 状态：代码层验证完成（2026-05-27）  
> 范围：旧 `index.html` 预审弹窗、异步报价任务、确认下发 `/confirm_push`  
> 边界：不改报价规则、价格口径、N8N/Dify、RAG 同步、成本库 active 规则；新增数据库结构仅用于保存预审编辑草稿。

## 1. 为什么做

当新甲方发来全新需求单，很多条目没有成本库底价时，业务员需要在预审单逐项补人工改价。如果编辑过程中离开页面、刷新浏览器或误关窗口，原来需要重新发起报价并重新填价，操作成本高，也容易出错。

BIZ-2q 的目标是把“预审单编辑中”的人工确认结果保存下来，让业务员可以继续同一个报价任务，不因为页面关闭而丢失人工填写的价格。

## 2. 已完成能力

- 新增 Alembic `20260527_0024`，创建 `quote_preview_drafts` 表。
- 每个异步报价任务最多保留一份预审草稿。
- 旧 `index.html` 预审弹窗支持：
  - 修改工程量、单位、施工项目、备注后自动保存。
  - 修改“人工改价(元)”或“系统合计(元)”后自动保存。
  - 点击“采用AI建议”后自动保存。
  - 切换成本库条目后自动保存。
  - 底部新增“保存草稿”按钮。
  - 底部新增“关闭”按钮，关闭前会保存草稿，不等同于打回重填。
  - 弹窗中展示草稿保存/恢复状态。
- “我的报价历史”会展示 `editing` 状态的预审草稿：
  - “推送”列显示“草稿”。
  - “操作”列显示“编辑”。
  - 点击“编辑”会重新打开同一报价任务的预审单，并恢复草稿。
- 再次打开同一个报价任务预审结果时，自动恢复 `editing` 状态的草稿。
- 点击“打回重填”后，草稿标记为 `discarded`。
- 确认下发成功后，草稿标记为 `pushed`，后续不能再覆盖保存。

## 3. 后端接口

- `GET /api/v1/quote/jobs/{job_id}/preview-draft`：查询预审草稿。
- `PUT /api/v1/quote/jobs/{job_id}/preview-draft`：保存或更新预审草稿。
- `POST /api/v1/quote/jobs/{job_id}/preview-draft/discard`：放弃预审草稿。
- `POST /api/v1/quote/jobs/{job_id}/preview-draft/mark-pushed`：标记草稿已下发，主要供系统内部或管理操作使用。

权限沿用报价任务权限：普通用户只能访问自己的报价任务，管理员可访问全部报价任务。

## 4. 草稿状态

| 状态 | 含义 | 后续行为 |
|------|------|----------|
| `editing` | 预审正在编辑或可继续编辑 | 再次打开任务时自动恢复 |
| `discarded` | 已打回重填或主动放弃 | 不自动恢复 |
| `pushed` | 已确认下发成功 | 不允许继续覆盖保存 |

## 5. 手动验收流程

1. 用一份包含无底价项目的需求单发起异步报价。
2. 等待旧预审弹窗打开。
3. 在某一行填写“人工改价(元)”，确认“系统合计(元)”自动变化。
4. 等 1 到 2 秒，观察弹窗底部出现“已自动保存”提示，或手动点击“保存草稿”。
5. 关闭预审弹窗或刷新页面。
6. 打开“我的报价历史”，找到“推送=草稿”的记录。
7. 点击“编辑”，重新打开预审单。
8. 确认刚才填写的人工改价、系统合计、备注仍然存在。
9. 继续补齐所有无底价行，确认推送。
10. 推送成功后再打开“我的报价历史”，草稿记录不应继续显示为可编辑草稿，应由正式历史记录展示为“已推送”。

补充边界：

1. 点击“关闭”只保存草稿，不记录打回原因，不生成成本库 draft。
2. 点击“打回重填”后，草稿标记为 `discarded`，再次打开历史不应显示为草稿。
3. 确认下发成功后，草稿标记为 `pushed`，后续不能再覆盖保存。

## 6. 验证结果

已执行：

```text
C:\Users\12521\miniconda3\python.exe -m pytest tests\test_quote_preview_drafts_biz2q.py
C:\Users\12521\miniconda3\python.exe -m pytest tests\test_quote_preview_drafts_biz2q.py tests\test_quote_history.py
C:\Users\12521\miniconda3\python.exe -m pytest tests\test_quote_preview_drafts_biz2q.py tests\test_quote_confirm_push_biz2m.py tests\test_quote_jobs.py::test_confirm_push_rejects_incomplete_requirement_preview tests\test_quote_jobs.py::test_confirm_push_rejects_unpriced_requirement_placeholders
node -e "... index.html script syntax check ..."
npm.cmd run build
C:\Users\12521\miniconda3\python.exe -m alembic upgrade head
C:\Users\12521\miniconda3\python.exe -m alembic current
```

结果：

```text
2 passed, 1 warning
3 passed, 1 warning
11 passed, 1 warning
index.html script syntax ok
ai-web build passed
current database: 20260527_0024 (head)
```

其中 pytest warning 为本地 `.pytest_cache` 写入被拒绝，不影响功能断言。

## 7. BIZ-2q-2 历史筛选与草稿清理补充

> 状态：代码层验证完成（2026-05-27）
> 范围：旧 `index.html` 的“我的报价历史”抽屉、`/api/v1/history`、预审草稿批量删除接口。
> 边界：不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步、成本库 active 规则，不删除已推送历史记录或报价任务。

本补充解决两个问题：

1. 草稿不再固定置顶，历史列表统一按显示时间倒序排列。
2. 历史记录过多时，业务员可以筛选并批量删除无用草稿，避免 `quote_preview_drafts.draft_json` 长期占用空间。

已完成能力：

- `/api/v1/history` 新增筛选参数：
  - `start_date` / `end_date`：按时间范围筛选。
  - `keyword`：按报价内容、摘要、需求文本、来源文件、任务编号、报价编号、Trace、首批项目名、客户信息等关键词筛选。
  - `min_item_count` / `max_item_count`：按项目数区间筛选。
  - `min_total_amount` / `max_total_amount`：按总价区间筛选。
  - `push_status`：按 `draft`、`pushed`、`not_pushed` 筛选。
- “我的报价历史”新增筛选栏，支持时间、报价内容、项目数、总价、状态筛选。
- “我的报价历史”新增草稿多选与“删除草稿”按钮。
- 只有 `push_status=draft` / `record_type=preview_draft` 的行可以被勾选删除。
- 新增 `POST /api/v1/quote/preview-drafts/batch-delete`：
  - 普通用户只能删除自己的 `editing` 草稿。
  - 管理员可以删除全部用户的 `editing` 草稿。
  - `pushed`、`discarded`、不存在或无权限的草稿会跳过并返回原因。
  - 删除仅删除预审草稿快照，不删除原报价任务，不删除已推送历史。

验收要点：

1. 打开“我的报价历史”，列表按时间倒序显示，草稿不固定置顶。
2. 选择状态“草稿”，只显示可继续编辑的预审草稿。
3. 输入报价内容关键词，可筛出对应草稿或正式历史记录。
4. 设置项目数或总价区间，可筛出符合条件的记录。
5. 勾选多份草稿后点击“删除草稿”，确认后草稿从列表消失。
6. 已推送历史记录不可勾选，不会被批量删除。
7. 删除草稿后，原报价任务仍可在任务接口或运营详情中追溯。

本次验证：

```text
C:\Users\12521\miniconda3\python.exe -m pytest tests\test_quote_preview_drafts_biz2q.py tests\test_quote_history.py tests\test_quote_confirm_push_biz2m.py -q
node -e "... index.html script syntax check ..."
Browser: http://127.0.0.1:9000/index.html 加载无 console error
```

结果：

```text
11 passed, 1 warning
index.html script syntax ok
in-app browser errorCount: 0
```

pytest warning 仍为本地 `.pytest_cache` 写入被拒绝，不影响功能断言。
