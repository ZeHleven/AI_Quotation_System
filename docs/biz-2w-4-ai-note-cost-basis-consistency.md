# BIZ-2w-4 AI 备注与成本依据一致性校验

> 状态：已通过当前环境手工验收  
> 日期：2026-05-29  
> 阶段：内网验证阶段，`PUBLIC_ACCESS_ENABLED=false`  
> Alembic：不需要

## 1. 背景

BIZ-2w-3 已修复“600*600矿棉板吊顶，10㎡”被 AI 改写后带偏成本参考的问题：预审成本依据能优先采用 `#39 轻钢龙骨矿棉板吊顶`。

但真实预审中仍可能出现另一类误导：成本库依据已经正确命中，AI 原始备注却仍显示“底层数据集中未包含相关条目、无法提供报价、建议补充施工项或联系客服”等话术。此时价格和成本依据是正确的，但备注会误导业务员和客户。

## 2. 本阶段目标

- 检测“已命中 active 成本库参考”与“AI 原始备注声称未找到/无法报价”的冲突。
- 保留 AI 原始备注作为审计字段，不直接丢弃证据。
- 将预审可见备注替换为系统建议备注，明确以成本库依据和人工确认价为准。
- 标记 `AI 备注冲突` 并要求人工确认备注处理。
- 未确认前，旧预审弹窗和 `/confirm_push` 均阻断下发。

## 3. 实现内容

### 3.1 后端一致性校验

`quote_cost_matching` 在成本参考 enrichment 后检查备注：

- 已命中 `cost_items.active`；
- AI 原始备注包含“未包含、未找到、无相关、未检索到”等缺参考语义；
- 同时包含“无法提供报价、建议补充、联系客服、定制报价”等不可报价语义。

命中冲突后：

- `cost_reference.ai_note_cost_basis_conflict=true`
- `cost_reference.requires_manual_ai_note_confirmation=true`
- `cost_reference.ai_original_notes` 保留 AI 原始备注
- `cost_reference.system_suggested_notes` 给出系统建议备注
- `row.notes` 替换为系统建议备注
- `quote_explanation` 增加备注冲突说明

### 3.2 下发兜底阻断

`/confirm_push` 新增校验：

- 若存在 `requires_manual_ai_note_confirmation=true` 且 `manual_ai_note_confirmed` 未确认，返回 409；
- 人工确认备注处理或人工编辑备注后才允许下发。

### 3.3 旧预审弹窗

旧 `index.html` 预审弹窗新增：

- 成本参考列展示 `AI备注冲突：需确认`；
- 提供“确认备注处理”按钮；
- 修改备注文本时自动视为已人工确认备注处理；
- 查看依据弹窗展示 AI 原始备注、系统建议备注和冲突原因；
- 未确认前禁用确认下发。

### 3.4 运营复核详情

`quote_review` 的预审检查项新增 `ai_note_confirmed`，并在摘要中输出 `ai_note_conflict_count`，便于后续报价运营详情继续复核。

## 4. 不做事项

- 不新增 Alembic；
- 不新增表或字段；
- 不改报价规则；
- 不改价格口径；
- 不改无底价自动处理原则；
- 不自动新增报价行；
- 不自动改总价；
- 不自动沉淀 active 成本库；
- 不改 RAG 同步逻辑；
- 不改 N8N/Dify 工作流；
- 不启动 Phase 4b/4c/6 或 BIZ-1b/BIZ-1c/BIZ-1d；
- 不迁移旧 `index.html` / `admin.html` / `app.html`。

## 5. 验证结果

### 5.1 专项与相关回归

```text
34 passed, 1 warning
26 passed, 1 warning
```

覆盖：

- AI 原始备注声称无数据/无法报价，但本行已命中成本库参考时，预审可见备注会替换为系统建议备注；
- AI 原始备注保留到审计字段；
- 成本参考摘要统计 `ai_note_conflict_count`；
- 正常工艺备注不会误判为 AI 备注冲突；
- 未确认 AI 备注冲突时 `/confirm_push` 返回 409；
- 已确认后 `/confirm_push` 可继续下发，且不会生成无底价 draft。
- 报价任务与 `/review-detail` 复核详情保持通过。

### 5.2 语法检查

```text
compileall app tests 通过
旧 index.html inline script 语法检查通过
ai-web npm.cmd run build 通过
```

## 6. 手工验收建议

核心样例：

```text
600*600矿棉板吊顶，10㎡
```

验收重点：

- 成本参考仍应显示 `#39 轻钢龙骨矿棉板吊顶`；
- 若 AI 原始备注出现“未包含相关条目、无法提供报价、建议补充”等话术，预审可见备注不应继续展示该误导文本；
- 成本参考列应提示 `AI备注冲突：需确认`；
- 查看依据弹窗能看到 AI 原始备注和系统建议备注；
- 未点击“确认备注处理”或未人工修改备注前不能下发；
- 确认备注处理后可继续下发。

## 7. 当前环境手工验收结论

2026-05-29，用户确认 BIZ-2w-4 手工验收已通过。

通过结论：

- AI 原始备注与成本依据冲突时，预审可见备注不再误导业务员或客户；
- AI 原始备注可在查看依据中追溯；
- 未人工确认备注处理前，前端和 `/confirm_push` 均阻断下发；
- 确认备注处理后阻断解除；
- 未发现自动改单价、改总价、新增报价行或改变无底价处理原则的问题。

## 8. 回滚方式

本阶段不涉及数据库结构和数据写入，回滚方式为代码回退：

- 回退 `quote_cost_matching.py` 的 AI 备注冲突检测和备注替换；
- 回退 `quote.py` 的 `/confirm_push` 备注确认阻断；
- 回退 `quote_review.py` 的 `ai_note_confirmed` 检查项；
- 回退旧 `index.html` 的 AI 备注冲突提示、确认按钮和下发阻断。
