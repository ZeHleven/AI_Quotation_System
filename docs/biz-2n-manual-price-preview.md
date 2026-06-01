# BIZ-2n 预审人工改价字段与合计联动

> 状态：代码层验证完成（2026-05-27）
> 范围：旧 `index.html` 报价预审弹窗
> 边界：不新增数据库结构，不改报价规则、价格口径、N8N/Dify 和成本库 active 规则。

## 1. 背景

BIZ-2m 人工验收暴露出一个真实操作问题：业务员在预审单中可能只修改“系统合计”，但原 `unit_price` 仍为 0，导致无底价 draft 沉淀时无法稳定识别最终确认单价。

BIZ-2n 将“AI 建议单价”和“最终人工确认单价”拆开展示，避免业务员误把 AI 单价当作最终价，也让后续 `/confirm_push`、报价历史、证据链和无底价 draft 继续读取稳定的 `unit_price/total_price`。

## 2. 规则

预审弹窗核心列顺序：

```text
施工项目 -> 工程量 -> 单位 -> AI 建议单价 -> 成本库参考 -> 人工改价 -> 系统合计(元) -> 备注
```

人工改价默认值：

- 命中 `cost_reference.reference_price` 时，默认取成本库参考单价。
- 未命中成本库参考时，默认 0，要求业务员人工填写。
- 手动切换成本库条目后，人工改价同步为切换后 active 成本条目的参考价。

系统合计联动：

- 有有效工程量时：`系统合计 = 人工改价 × 工程量`。
- 工程量为空或小于等于 0 时，保留当前可编辑合计并提示“工程量异常”，业务员可手动修正系统合计。
- 若 AI 预审返回工程量为 0，但源 Excel 解析结果中同一行有有效工程量，后端优先用源 Excel 工程量回填。
- 下发前，若仍有行的人工改价或系统合计不为正数，前端阻断确认推送。

最终下发口径：

- 前端提交 `/confirm_push` 前，把 `manual_unit_price` 写回现有 `unit_price`。
- `total_price` 使用联动或人工修正后的系统合计。
- AI 原始建议价保留到 `ai_suggested_unit_price` / `ai_suggested_total_price`，用于前端展示和后续追溯。

## 3. 已完成改动

- 旧预审弹窗新增可编辑“工程量/单位”和“人工改价(元)”列。
- “AI 建议单价”改为只读展示。
- 预审数据打开时自动初始化 `manual_unit_price`。
- 预审弹窗补充可编辑“工程量”和“单位”，AI 返回 0 或缺失时可人工修正工程量。
- 后端补强源 Excel 工程量回填：AI 返回 `quantity=0` 时，若源 Excel 有正数工程量，则自动恢复源工程量。
- 修改人工改价或工程量后自动重算系统合计。
- 确认下发前统一归一化 `unit_price/total_price`。
- 新增前端阻断：人工改价或系统合计无有效正数时不能下发。
- 成本库条目切换后同步更新人工改价与系统合计。

## 4. 验收样例

| 场景 | 预期 |
| --- | --- |
| 有成本库参考，工程量 18，参考价 6 | 人工改价默认 6，系统合计自动为 108 |
| 无成本库参考 | 人工改价默认 0，确认下发前必须人工填写 |
| 人工改价 100，工程量 35 | 系统合计自动为 3500 |
| 源 Excel 工程量为 35，但 AI 返回工程量 0 | 后端回填工程量 35，预审不再显示工程量异常 |
| 工程量为 0 或缺失 | 提示工程量异常，可先修正工程量，再由人工改价自动联动系统合计 |
| 未填写人工改价或系统合计 | 前端阻断下发 |
| 已填写无底价人工改价并下发成功 | BIZ-2m 继续生成 `cost_items.draft`；BIZ-2p 后手动改价来源为“人工”，采用 AI 建议来源为“AI 建议” |
| 有 active 成本参考 | 不生成无底价 draft |

## 5. 验证结果

已执行：

```text
index.html script syntax ok
```

已执行：

```text
python -m pytest AI_Middle_Office/tests/test_confirm_push_schema.py AI_Middle_Office/tests/test_quote_history.py AI_Middle_Office/tests/test_quote_feedback.py AI_Middle_Office/tests/test_quote_jobs.py::test_confirm_push_rejects_unpriced_requirement_placeholders AI_Middle_Office/tests/test_quote_jobs.py::test_cost_fallback_does_not_price_requirement_placeholder AI_Middle_Office/tests/test_no_cost_draft_capture_biz2m.py AI_Middle_Office/tests/test_quote_confirm_push_biz2m.py
```

结果：

```text
22 passed, 1 warning
```

说明：warning 仍为 Windows `.pytest_cache` 写入权限问题，不影响功能验证。

已执行：

```text
npm.cmd run build
```

结果：

```text
Vite build passed
```
