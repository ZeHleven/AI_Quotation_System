# 成本预算对标 Phase 2 P2-2C-1：单行 AI 估价进入计价草稿

状态：已完成当前环境运行态验收（2026-07-16）

## 目标与边界

P2-2C-1 在 P2-2A 可变计价草稿、P2-2B 账户定额闭环之后，补上“缺价行由用户手动触发 AI 估价”的最小闭环。

- AI 估价只写入当前账号、当前项目的可变计价草稿行。
- AI 估价不生成正式 `budget_project_pricing_runs`。
- AI 估价不写入企业定额，不写入账户定额 `active`。
- 已有人工价的行不允许被 AI 覆盖。
- 已有企业定额或账户定额基础价的行不允许被 AI 覆盖。
- 若已有 AI 建议价且仍无人工价/基础价，后续可以再次触发估价刷新。
- 人工价优先级最高：有效单价 = 人工价 > AI 建议价 > 定额基础价。
- 清空人工价后，如果该行已有 AI 建议价，有效单价恢复为 AI 建议价。

## 实现

- 新增开关：`FEATURE_BUDGET_PRICING_AI_ESTIMATE`。
- 新增配置：`BUDGET_PRICING_AI_PROVIDER`、`BUDGET_PRICING_AI_PROMPT_VERSION`、`BUDGET_PRICING_AI_TIMEOUT_SECONDS`。
- 新增 Alembic `20260716_0057`：
  - `budget_project_pricing_draft_lines.ai_estimated_unit_price`
  - `budget_project_pricing_draft_lines.ai_estimate_snapshot_json`
- 新增服务 `app/services/budget_pricing_ai_estimates.py`。
- 新增接口：
  - `POST /api/v1/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/ai-estimate`
- 前端 `BudgetProjectPricing.vue` 在草稿行操作区新增“AI估价”按钮。

当前环境 `BUDGET_PRICING_AI_PROVIDER=rule` 时会使用规则兜底估价，快照中明确标记 `provider=rule`、`mode=rule_fallback`。配置为 `deepseek` 且具备 API Key 后，可通过模型网关调用 DeepSeek，返回值仍只进入草稿。

## 当前环境验收

- 0057 前完整“表结构 + 业务数据”备份：
  - `output/pre_budget_0057_20260716_223519_p2_2c1_ai_estimate/ai_quotation_before_0057_schema_data.sql`
  - bytes: `162738186`
  - SHA256: `2E147ADA820A48636C1C1CE0DC2107C77A566D36D3CD47356B41DB5802D0830A`
- 当前数据库：`20260716_0057 (head)`。
- 后端已重启，`/health/ready` ready。
- 专项回归：`22 passed`。
- Vite build 通过。

项目 15 运行态验收：

- 原草稿：Rev9。
- 验收行：`line_id=992`，项目名称“砌体墙拆除”。
- 触发后草稿：Rev10。
- 行版本：R1 -> R2。
- `price_source=ai_estimate`。
- `ai_estimated_unit_price=46.750000`。
- `effective_unit_price=46.750000`。
- `line_total=88372.460000`。
- `manual_unit_price=null`。
- 估价来源：`provider=rule`、`mode=rule_fallback`。
- 项目 15 正式计价 run 数量：`4 -> 4`，未新增正式版本。

## 后续

P2-2C-1 只完成“单行手动 AI 估价”。后续可继续拆为：

- P2-2C-2：基础定额模式下未匹配行的批量/自动估价策略。
- P2-2C-3：账户定额模式下多行手动估价、批量选择和风险提示。
- P2-2C-4：AI 估价依据展示增强、人工确认后同步到账户定额 draft 的体验打通。
