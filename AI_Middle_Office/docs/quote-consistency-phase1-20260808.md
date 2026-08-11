# 报价业务正确性与数据一致性第一阶段

日期：2026-08-08

## 状态

- 代码与自动化测试已完成。
- 新代码迁移 head：`20260808_0082`。
- 当前正式 ECS 数据库仍为 `20260801_0081`；本阶段未连接、备份或升级正式环境。
- 正式升级前必须先完成数据库备份，并在维护窗口依次执行迁移、API/Worker 同版本发布和运行态验收。

## 已实现范围

1. 报价任务使用 `queued -> running` 条件更新和唯一 `attempt_id` 原子领取，重复 Celery 投递不会再次执行报价。
2. 新增额度预占模型：创建或重试任务时原子预占，成功时消费，失败、取消、超时或派发失败时释放；`users.quota` 继续表示剩余额度，`quota_reserved` 表示执行中预占。
3. 报价重试记录 `source_job_id`，并逐字段复制原任务的确认需求行快照。
4. 确认下发使用基于标准化 payload SHA-256 的幂等键；状态为 `sending -> external_delivered -> delivered`。外部推送成功但本地事务失败时，原请求重试只补写本地历史、反馈、无底价草稿和预审草稿状态，不再次调用外部推送。
5. 报价历史、确认反馈、无底价成本草稿、预审草稿 pushed 状态和推送完成状态在同一个本地事务中提交。
6. 取消、超时和 Worker 完成路径使用行锁/条件状态校验，避免终态被并发覆盖。

## 数据结构

迁移 `20260808_0082` 新增：

- `users.quota_reserved`
- `quote_jobs.source_job_id`
- `quote_jobs.attempt_id`
- `quote_jobs.started_at`
- `quote_quota_reservations`
- `quote_push_attempts`

迁移不删除现有表或字段。

## 自动化验证

- 第一阶段专项：`6 passed`
- 报价、历史、反馈、成本草稿、预审草稿、RBAC 联合回归：`68 passed`
- 全量后端：`1207 passed / 15 failed`
- 15 个失败全部是已按公网安全策略隐藏的 Codex Worker / DWG Quantity Trial 试验路由返回 403，与本阶段报价改动无关。
- `python -m alembic heads`：`20260808_0082 (head)`
- MySQL 离线迁移 SQL 生成通过；`git diff --check` 无空白错误。

## 正式上线门槛

1. 48 小时旧环境回滚观察期结束后再安排维护窗口。
2. 先做 MySQL 全量备份并校验完成标记、文件大小和 SHA-256。
3. 在影子/恢复库执行 `0081 -> 0082`，验证新增列、唯一约束、外键和索引。
4. API 与 Celery Worker 必须使用同一版本镜像；不可出现新代码连接旧 schema。
5. 运行 MySQL 并发验收：额度为 1 的双请求、同 job 双 Celery 投递、确认下发双请求、取消与完成竞态。
6. 确认 N8N 按 `idempotency_key` 去重；在此之前，应用可以阻止正常重复请求，但无法完全消除“外部已成功、进程在回写状态前崩溃”的跨系统极小窗口。
7. 验收通过后观察 `quote_quota_reservations` 的长期 reserved 记录和 `quote_push_attempts.external_delivered` 未完成记录，并配置告警/对账。
