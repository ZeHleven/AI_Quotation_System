# BIZ-2o 成本库状态与流向台账

> 状态：代码层验证完成（2026-05-27）
> 范围：Vite `/admin/cost-db` 成本数据库页面、成本库只读追踪接口
> 边界：不新增数据库结构，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑和成本库 active 规则。

## 1. 目标

在成本数据库页面新增“状态与流向”入口，集中查看成本条目的来源、当前状态、生命周期和后续引用情况。

首版复用现有数据：

- `cost_items`：条目状态、来源、价格、创建更新时间。
- `cost_item_history`：价格变更、状态流转、操作人和原因。
- `quote_cost_evidence`：报价中引用过哪些成本条目。
- `cost_rag_sync_runs`：active 成本库同步到 RAG 的批次记录。

## 2. 已完成能力

- 成本数据库顶部新增“状态与流向”按钮。
- 抽屉内提供“总览 / 新增 draft / active 记录 / 归档记录”四个视图。
- 总览展示 draft、active、archived、AI 建议草稿、被报价引用条目、active RAG 范围和最近 RAG 同步。
- 列表支持按来源、是否被报价引用和关键词筛选。
- 单条详情展示来源信息、当前去向、生命周期和最近 50 条报价引用。
- AI 建议来源会解析 BIZ-2m notes 中的 `quote_job_id`、`quote_history_id`、`line_no`、`source_sheet`、`confirmed_unit_price` 等追溯字段。

## 3. 数据口径

| 字段 | 口径 |
| --- | --- |
| draft | `cost_items.status = draft`，待审核，不参与报价/RAG/兜底 |
| active | `cost_items.status = active`，参与报价匹配、底价兜底，可同步到 RAG |
| archived | `cost_items.status = archived`，冻结，不参与报价/RAG/兜底 |
| 来源 | `cost_items.source`，包含 `manual`、`imported`、`ai_suggested` |
| 生命周期 | `cost_item_history` 的价格和状态变更记录 |
| 报价引用 | `quote_cost_evidence.cost_item_id` 命中的报价证据 |
| RAG 去向 | 首版按 active 范围和最近成功同步批次推断，不记录单条同步明细 |

## 4. 验收样例

1. 无底价报价确认下发后，成本库生成 `draft`；BIZ-2p 后来源按价格动作显示为“人工”或“AI 建议”。
2. 打开“状态与流向”，在“新增 draft”中能看到该条目。
3. 条目详情能看到来源报价任务、报价历史 ID、报价行号和确认价格。
4. 启用该 draft 后，生命周期能看到 `draft -> active`。
5. 重新发起相同需求并命中该 active 条目后，详情能看到报价引用记录。
6. 归档条目后，“归档记录”能看到该条目，当前去向显示不参与后续报价/RAG/兜底。

## 5. 验证结果

已执行：

```text
python -m pytest tests\test_cost_db_biz2a.py tests\test_cost_rag_sync_biz2c.py tests\test_no_cost_draft_capture_biz2m.py tests\test_quote_confirm_push_biz2m.py tests\test_quote_cost_matching_biz2b.py
```

结果：

```text
48 passed, 3 warnings
```

清理后补充关键回归：

```text
python -m pytest tests\test_cost_db_biz2a.py::test_cost_item_lineage_summary_list_and_detail tests\test_cost_rag_sync_biz2c.py tests\test_no_cost_draft_capture_biz2m.py tests\test_quote_confirm_push_biz2m.py
```

结果：

```text
17 passed, 1 warning
```

已执行：

```text
npm.cmd run build
```

结果：

```text
Vite build passed
```
