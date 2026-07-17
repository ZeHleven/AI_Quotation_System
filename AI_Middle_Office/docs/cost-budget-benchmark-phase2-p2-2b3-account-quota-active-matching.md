# 成本预算对标 Phase 2 P2-2B-3：账户定额 active 严格匹配

状态：已完成当前环境运行态验收（2026-07-16）

## 目标与边界

本阶段将 P2-2B-1 的账户定额库接入 P2-2A 的“账户定额”可变计价草稿，但仅允许使用当前账号已启用的 `active` 条目。

- 只读当前登录用户服务端解析出的当前账号；请求体不接收 `account_id`。
- 只读取 `account_quota_items.status=active`，`draft`、`archived`、其他账号条目都不参与匹配。
- 按定额编码或项目名称精确匹配并要求单位兼容；同名多条时仅在项目特征/规格唯一消歧时自动命中，否则保持待人工处理。
- 未匹配、单位冲突或歧义行保持空价；不读取企业定额，不调用 LLM，不生成正式计价 run。
- 企业定额 `active` 仍是唯一正式成本主库；账户定额只服务当前账号的可变草稿。
- “项目测算/采购结果”保持冻结，未触及。

## 实现

- `BudgetProjectPricingDraft` 新增账户定额目录哈希，草稿行新增 `selected_account_quota_item_id` 与候选/匹配证据。
- 账户模式匹配引擎版本为 `account-quota-strict-p2-2b3`；匹配结果价格来源为 `account_quota`。
- 前端明确提示“只匹配当前账号 active 条目”，未匹配保持空价，手动 AI 估价仍未接入。
- Alembic `20260716_0055` 增加账户定额匹配证据字段和外键。
- 运行态发现 P2-2B-2 审计行以 `RESTRICT` 关联可变草稿行会阻断重建；Alembic `20260716_0056` 将该实时链接改为可空 `SET NULL`。同步审计仍保留不可变的来源行 UUID、来源行版本和完整来源快照，因此不会丢失审计事实。

## 当前环境验收

- 0055 前完整“表结构 + 业务数据”备份：`output/pre_budget_0055_20260716_194902_p2_2b3/ai_quotation_before_0055_schema_data.sql`，`245264848` bytes，SHA256 `C3219DE5C0A3629F909F263070B24F79F5F3F55F9725DE60332A47B1438B2D22`。
- 0056 前完整“表结构 + 业务数据”备份：`output/pre_budget_0056_20260716_200721_p2_2b3_rebuild_fix/ai_quotation_before_0056_schema_data.sql`，`245269702` bytes，SHA256 `32A94FC150C429FFDB68B627E2144F65922EAF0F614AACDA281502F470AE2E66`。
- 应用数据库账号无 `PROCESS` / `EVENT` 权限，备份明确禁用 tablespaces 和 events；未提升或绕过数据库权限。
- 当前数据库为 `20260716_0056 (head)`，同步审计到草稿行的外键删除规则已核验为 `SET NULL`。
- 项目 15：账户定额条目 `2` 通过正常状态流转由 `draft/R3` 启用到 `active/R4`，价格 `50.000000`。
- 项目 15 草稿由 Rev8 重建为 Rev9：`row_count=198`、`matched_count=1`、`priced_count=1`；唯一命中条目为账户定额 `2`，价格来源 `account_quota`、有效单价 `50.000000`、行合计 `3201.000000`；`enterprise_quota_version_id=null`。
- 专项回归：`19 passed`（P2-2A、P2-2B-1、P2-2B-2 与 P2-2B-3 覆盖）；存在既有 SQLite 删除顺序与 pytest cache 权限告警，无失败。

## 后续

P2-2C 才考虑账户模式下由用户手动触发 LLM 估价；本阶段不自动估价，也不自动把未匹配项沉淀到账户定额。
