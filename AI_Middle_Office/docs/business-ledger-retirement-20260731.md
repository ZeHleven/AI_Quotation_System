# 商务台账退役说明（2026-07-31）

## 退役范围

商务台账 BIZ-1a 不再属于当前产品范围，已移除：

- Vite `/admin/business-ledger` 导航、页面、弹窗、状态与样式
- `/api/v1/business-ledger*` API
- business ledger service、schema、事件模型与 smoke 脚本
- RBAC `business_ledger` 模块
- `FEATURE_BUSINESS_LEDGER` 配置

历史迁移 `20260520_0016` 继续保留，以维持既有 Alembic 迁移链。

## 数据库迁移

新增 Alembic `20260731_0079`，升级时：

1. 删除 `client_inquiry_events`。
2. 将异常关联 outbound 台账记录的 `quote_jobs.client_inquiry_id` 置空，保留报价任务。
3. 删除 `client_inquiries.direction='outbound'` 的商务台账记录。
4. 从 `client_inquiries` 删除 `direction`、`stage`、`next_followup_at`、`cancelled_at`、`cancelled_by_id`、`cancel_reason`。
5. 将保留记录的 `first_response_time` 恢复为非空字段；历史异常空值使用咨询时间、创建时间或数据库当前时间补齐。

该迁移是破坏性迁移。downgrade 只能恢复空 schema，不能恢复已删除的 outbound 台账记录或事件。

## 保留边界

以下能力和数据继续保留：

- Phase 2 inbound 客户咨询 `client_inquiries`
- `quote_jobs.client_inquiry_id` 与正常 inbound 咨询关联
- 客户咨询查询/修正 API
- 响应速度看板
- 报价、成本库、项目进度、预算项目、企业/账户定额和 Agent 数据

退役后 `client_inquiries` 恢复为纯 inbound 模型，因此保留链路不再需要 `direction='inbound'` 过滤。

## 上线顺序

当前只完成代码和迁移，不操作真实数据库。继续删除其他模块时，可先保留本迁移；最终统一维护窗口中：

1. 完整备份数据库并校验备份可用。
2. 停止后端与 Celery/Agent Worker 写入。
3. 检查 `alembic current` 与 `alembic heads`。
4. 执行 `alembic upgrade head`。
5. 重启服务并检查 `/health/ready`。
6. 回归 inbound 客户咨询、响应速度、报价任务与其他保留模块。
