# 成本测算闭环退役记录（2026-07-31）

## 结论

按产品决定，原“成本测算闭环”（COST-MEASURE-1 / COST-MEASURE-2）完整退役。此次只处理 `cost_measurement*` 功能链及其三张专属表，不扩大到其他成本、报价、定额或 Agent 模块。

## 已移除范围

- Vite 管理台“数据资产 → 成本测算”入口和 `/admin/cost-measurement` 页面。
- `/api/v1/admin/cost-measurements/*` API。
- `cost_measurement`、`cost_measurement_drafts` 服务及专属模型注册。
- `FEATURE_COST_MEASUREMENT` 功能开关。
- RBAC 模块 `cost_measurement`。
- 原成本测算闭环专项测试，改为退役边界与迁移保护测试。

## 数据库迁移

新增 Alembic `20260731_0080`。升级时仅按外键依赖顺序删除：

1. `cost_measurement_events`
2. `cost_measurement_lines`
3. `cost_measurements`

移除前的只读盘点为：1 条草稿测算、127 条测算明细、1 条导入事件；无企业定额条目关联，也没有由该闭环标记沉淀的成本库 draft。迁移的 `downgrade` 只恢复空表结构，无法恢复已删除数据。

## 明确保留范围

以下能力及其数据结构不属于本次退役范围，继续保留：

- 项目报价、对话报价及现有报价预审/下发链路。
- 企业定额主库 `enterprise_quota_*`。
- 账户定额库 `account_quota_*`。
- 智能组价实验室及预算项目计价 `budget_project_*` / `budget_project_pricing_*`。
- 报价资料研判 Agent、智能助手及其他 Agent 数据结构。
- 成本数据库 `cost_items`、`cost_item_history`、成本审计及 active RAG 同步链路。
- 项目进度 `project_*`、Phase 2 inbound 客户咨询及其他业务模块。

## 当前环境执行结果

2026-07-31 已在用户确认没有其他待删除模块后完成统一上线操作：

- 升级前全量备份：`backups/retired_modules_pre_0080_20260731_182547.sql`。
- 备份大小：796,196,710 bytes（759.31 MB）。
- SHA-256：`732CEB33858B5E076BADBE975DD9868720E543D57E8E6394A751E66A9BAE12DF`。
- 备份包含 125/125 张升级前业务表，三张成本测算专属表的 1/127/1 条记录均已写入备份。
- 当前数据库已由 `20260731_0077` 依次升级到 `20260731_0080 (head)`。
- 升级后数据库为 116 张表；执行系统 5 张专属表、商务台账事件表/专用字段、成本测算 3 张专属表均已移除。
- FastAPI 已从旧 PID `39808` 切换到新 PID `42692`，`/health/ready=ready`，数据库与 Celery worker 均为 `ok`。
- `/admin/cost-measurement`、`/admin/execution`、`/admin/business-ledger` 均返回 404；保留的报价、成本库、账户定额、预算计价、投标和 Agent 页面均返回 200。
