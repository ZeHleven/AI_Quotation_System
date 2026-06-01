# BIZ-2p 预审人工改价来源判定与 AI 建议采纳

> 状态：代码层验证完成（2026-05-27）
> 范围：旧 `index.html` 预审弹窗、无底价 draft 沉淀、Vite 成本库状态与流向详情
> 边界：不新增数据库结构，不改报价规则、价格口径、N8N/Dify、RAG 同步和成本库 active 规则。

## 1. 目标

让无底价项目沉淀到成本库 draft 时，来源更符合业务语义：

- 业务员手动改过“人工改价(元)”：成本库来源为 `manual`，前端显示“人工”。
- 业务员点击“采用AI建议”：成本库来源仍为 `ai_suggested`，但记录“人工确认采纳AI建议”。
- 未人工改价且沿用 AI 价格的旧兼容数据：成本库来源为 `ai_suggested`。
- Excel 导入仍为 `imported`。

## 2. 已完成能力

- 预审单“人工改价(元)”列新增“采用AI建议”按钮。
- 点击“采用AI建议”后，系统会把 `AI建议单价(元)` 写入人工改价，并按工程量重算系统合计。
- 系统合计仍标记为“人工确认价”，因为采纳动作由业务员触发。
- 手动输入人工改价会标记为 `manual_override`。
- 确认下发后，无底价 draft 根据价格动作写入来源：
  - `manual_override` -> `source=manual`
  - `accepted_ai_suggestion` -> `source=ai_suggested`
- 无底价 draft notes 会记录 `manual_price_action`、`final_price_source`、`price_confirmation_label` 和 `cost_item_source`。
- 成本库来源标签统一显示“人工 / 导入 / AI 建议”。
- “状态与流向”详情新增“价格动作”展示。

## 3. 验收样例

1. 无底价行手动输入人工改价并确认下发。
   - 成本库新增 draft。
   - 来源显示“人工”。
   - 状态仍为 draft，不参与报价/RAG/兜底。

2. 无底价行点击“采用AI建议”并确认下发。
   - 人工改价自动等于 AI 建议单价。
   - 系统合计按工程量重算。
   - 系统合计来源显示“人工确认价”。
   - 成本库新增 draft，来源显示“AI 建议”。
   - 状态与流向详情显示“人工确认采纳AI建议”。

3. 没有 AI 建议单价的行。
   - “采用AI建议”按钮不可用。
   - 仍可手动输入人工改价。
   - 未补有效人工改价和系统合计前仍阻断确认推送。

4. active 成本库命中行。
   - 不生成无底价 draft。
   - 不改变原成本库 active、RAG 同步和报价匹配规则。

## 4. 验证结果

已执行：

```text
python -m pytest tests\test_quote_confirm_push_biz2m.py
python -m pytest tests\test_no_cost_draft_capture_biz2m.py
python -m pytest tests\test_cost_db_biz2a.py::test_cost_item_lineage_summary_list_and_detail
npm.cmd run build
node -e "... index.html script syntax check ..."
```

结果：

```text
7 passed, 1 warning
7 passed, 1 warning
1 passed, 1 warning
ai-web build passed
index.html script syntax ok
```
