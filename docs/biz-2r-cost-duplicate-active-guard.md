# BIZ-2r 成本库重复 active 防护与报价多候选提示

## 1. 阶段目标

BIZ-2r 解决成本库中“同名、同单位、同规格或高度相似”的成本条目被重复启用为 `active` 后，真实报价静默采用其中一条的问题。

本阶段只做防护和提示，不改变报价价格口径：

- 成本库启用 `draft -> active` 前检查重复 active。
- 批量启用时逐条检查，合格项继续启用，冲突项返回原因。
- 无底价项目下发后沉淀 draft 前先查重，避免同一项目反复沉淀。
- 报价命中多个 active 候选时，预审单提示并要求人工确认成本依据。
- `/confirm_push` 对未确认的多候选成本依据做后端阻断。

## 2. 不做范围

- 不自动删除历史重复 active。
- 不自动合并成本条目。
- 不自动归档旧条目。
- 不改变 AI 报价规则、成本库匹配价格口径、底价兜底口径。
- 不新增数据库表或字段。
- 不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。

## 3. 重复判断规则

### 3.1 阻断启用

启用 draft 时，如果已存在满足以下条件的 `active` 成本项，则阻断：

- 项目名称归一化后相同。
- 单位归一化后相同。
- 价格类型相同。
- 规格/特征完全相同，或至少一方规格为空，或规格高度相似。

返回 `409`，错误码：

```text
COST_ACTIVE_DUPLICATE_CONFLICT
```

### 3.2 允许同名不同规格

如果名称和单位相同，但规格/特征清晰不同，例如 `300x300` 与 `600x600`，允许同时启用为 active。

### 3.3 批量启用

批量启用不会因为一条冲突导致整批失败：

- 可启用项正常变为 `active`。
- 重复冲突项进入 `conflicts`，原因是 `duplicate_active_conflict`。
- 前端可提示冲突数量，管理员再手工处理。

## 4. 无底价沉淀去重

确认下发后，BIZ-2m 无底价项目沉淀为 `cost_items.draft` 前，会使用同一套重复规则：

- 已有相同或相似 `active`：跳过新增，返回 `skipped_active_duplicate`。
- 已有相同或相似 `draft`：跳过新增，返回 `skipped_existing_draft`。
- 已有相同或相似 `archived`：允许新增 draft，但记录 archived duplicate id。
- 同名同单位但规格明确不同：允许新增 draft。

## 5. 报价多候选提示

报价阶段仍按现有排序选择默认成本项，不改变价格计算。但当一行命中多条 active 候选时，`cost_reference` 会新增：

```json
{
  "candidate_count": 2,
  "alternative_cost_items": [],
  "requires_manual_cost_candidate_confirmation": true,
  "manual_cost_candidate_confirmed": false,
  "ambiguity_reason": "存在多条 active 成本候选，需人工确认采用哪条成本依据。"
}
```

旧 `index.html` 预审单会显示“多候选 N 条，需确认”，并提供：

- “确认当前依据”：接受系统当前选中的成本条目。
- “切换条目”：改选其它 active 成本条目。

未处理前，确认推送按钮禁用；即使绕过前端，`/confirm_push` 也会返回 `409`。

## 6. 验收标准

1. 已有相同 active，再启用完全相同 draft：返回 `409 COST_ACTIVE_DUPLICATE_CONFLICT`。
2. 同名同单位但规格明确不同：允许启用。
3. 批量启用包含重复项：合格项启用，重复项进入 `conflicts`。
4. 无底价项目重复下发：不会重复新增相同或相似 draft。
5. 报价命中多个 active 候选：预审单显示多候选风险。
6. 多候选未确认时 `/confirm_push` 阻断。
7. 点击“确认当前依据”或切换成本条目后允许继续下发。

## 7. 已验证用例

代码层验证覆盖：

- 成本库启用重复 active 防护。
- 同名不同规格启用放行。
- 批量启用冲突返回。
- 无底价相似 active 去重。
- 报价多 active 候选标记。
- 精确规格命中不误报多候选。
- `/confirm_push` 阻断未确认多候选。

验证命令：

```powershell
C:\Users\12521\miniconda3\python.exe -m pytest tests\test_cost_db_biz2a.py tests\test_no_cost_draft_capture_biz2m.py tests\test_quote_cost_matching_biz2b.py tests\test_quote_confirm_push_biz2m.py -q
```

结果：

```text
53 passed, 3 warnings
```

当前环境手动验收已通过（2026-05-27）：重复 active 防护、多候选提示、确认当前依据、切换成本条目和未确认阻断下发均按预期工作。
