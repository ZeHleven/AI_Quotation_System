# BIZ-2w-3 成本参考优先与 AI 改写防护

> 状态：已完成代码层验证，待当前环境手工验收  
> 日期：2026-05-28  
> 阶段：内网验证阶段，`PUBLIC_ACCESS_ENABLED=false`  
> Alembic：不需要

## 1. 背景

验收样例 `600*600矿棉板吊顶，10㎡` 暴露出一个报价安全问题：

- 原始需求可以命中 `cost_items.active` 中的 `#39 轻钢龙骨矿棉板吊顶`；
- AI 返回预审行时把项目改写为“轻钢龙骨石膏板平面天花”；
- 后置成本匹配按 AI 返回项目名重新匹配，导致成本参考被带到 `#35 轻钢龙骨石膏板平面天花`；
- 预审单看起来像“采纳前置成本库”，但实际成本依据已经被 AI 改写后的项目名带偏。

因此本阶段把成本库参考的优先级提升到 AI 报价之前：先基于原始需求识别成本参考，再让 AI 报价；AI 返回后不得自动覆盖原始需求已锁定的成本依据。

## 2. 本阶段目标

- 原始需求命中 active 成本项后，后置预审优先沿用原始需求成本依据。
- AI 返回项目名与原始需求命中的成本项不一致时，标记 `AI 改写风险`。
- 未人工确认 AI 改写风险前，旧预审弹窗和 `/confirm_push` 均阻断下发。
- 查看依据弹窗展示原始需求、AI 返回项目和风险说明。
- 不改报价规则、不改价格口径、不改 N8N/Dify、不新增数据库结构。

## 3. 实现内容

### 3.1 原始需求成本依据锁定

`quote_cost_context` 新增 `cost_context_references_as_source_rows()`，把报价前已命中的成本参考转换为后置 enrichment 可复用的 source rows。

纯文本报价场景下，例如：

```text
600*600矿棉板吊顶，10㎡
```

报价前命中结果会被带到后置预审：

```text
cost_item_id = 39
item_name = 轻钢龙骨矿棉板吊顶
spec = 8厘拉杆、600mm*600mm
reference_price = 50.19
```

### 3.2 后置匹配优先原始需求

`quote_cost_matching` 后置 enrichment 顺序调整为：

1. 优先读取 source row 中的 `locked_cost_item_id`；
2. 若无锁定项，则基于原始 source row 匹配 active 成本库；
3. 只有原始 source row 无成本参考时，才按 AI 返回项目名匹配。

这避免 AI 把“矿棉板”改成“石膏板”后，系统跟着切换成本参考。

### 3.3 AI 改写风险识别

当原始需求成本项和 AI 返回项目名命中的成本项不一致时，预审行的 `cost_reference` 会增加：

- `ai_rewrite_risk=true`
- `requires_manual_ai_rewrite_confirmation=true`
- `source_requirement_project_name`
- `ai_returned_project_name`
- `ai_returned_cost_item_id`
- `ai_rewrite_reason`

### 3.4 前后端阻断

旧 `index.html`：

- 成本库参考列展示 `AI改写风险：原始需求 -> AI返回项目`；
- 提供“确认原始依据”按钮；
- 未确认前禁用下发按钮；
- 查看依据弹窗展示原始需求、AI 返回项目和风险说明。

后端 `/confirm_push`：

- 若存在 `requires_manual_ai_rewrite_confirmation=true` 且未确认，返回 409；
- 人工确认或手动切换成本条目后才允许下发。

## 4. 验证结果

### 4.1 专项测试

```text
3 passed, 1 warning
```

覆盖：

- AI 把矿棉板改写为石膏板时，后置预审仍采用原始矿棉板成本项；
- 纯文本报价前置成本上下文可锁定原始成本项；
- 未确认 AI 改写风险时 `/confirm_push` 返回 409。

### 4.2 相关回归

```text
56 passed, 1 warning
```

覆盖：

- 成本库匹配；
- 多 active 候选确认；
- 无底价 draft 沉淀；
- 报价任务完整性；
- `/confirm_push` 阻断规则。

### 4.3 前端验证

- `ai-web` build 通过；
- 旧 `index.html` inline script 语法检查通过。

### 4.4 当前真实库只读模拟

输入：

```text
600*600矿棉板吊顶，10㎡
```

前置命中：

```text
#39 轻钢龙骨矿棉板吊顶 / 8厘拉杆、600mm*600mm / 50.19 元/㎡
```

模拟 AI 返回：

```text
轻钢龙骨石膏板平面天花 (不含乳胶漆)间距300*600
```

后置预审结果：

```text
selected_cost_item_id = 39
ai_rewrite_risk = true
requires_manual_ai_rewrite_confirmation = true
ai_returned_cost_item_id = 35
```

## 5. 不做事项

- 不新增 Alembic；
- 不写成本库；
- 不自动沉淀 active；
- 不自动新增报价行；
- 不自动改报价规则或价格口径；
- 不改 N8N/Dify；
- 不改 RAG 同步逻辑；
- 不启动试运行；
- 不启动 Phase 4b/4c/6 或 BIZ-1b/BIZ-1c/BIZ-1d。

## 6. 回滚方式

本阶段不涉及数据库结构和数据写入，回滚方式为代码回退：

- 回退 `quote_cost_context.py` 中 source rows 转换；
- 回退 `quote_cost_matching.py` 中原始需求优先匹配和 AI 改写标记；
- 回退 `quote.py`、`quote_job_runner.py` 的 source rows 传递；
- 回退旧 `index.html` 的 AI 改写提示和阻断。

## 7. 手工验收建议

重点样例：

```text
600*600矿棉板吊顶，10㎡
```

验收标准：

- 成本库参考应优先显示 `#39 轻钢龙骨矿棉板吊顶`；
- 不应自动显示为 `#35 轻钢龙骨石膏板平面天花`；
- 如果 AI 返回项目仍出现“石膏板”，预审单必须显示 AI 改写风险；
- 未点击“确认原始依据”或未切换成本条目前，下发应被阻断；
- 确认后可下发，并在查看依据中看到原始需求和 AI 返回项目的差异。
