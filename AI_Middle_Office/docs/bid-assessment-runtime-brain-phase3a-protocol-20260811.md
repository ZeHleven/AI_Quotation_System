# 报价资料研判 Agent Phase 3A：运行输入冻结与 Run Bootstrap 协议

版本：`v0.1`  
日期：`2026-08-11`  
对应主规范：`v0.1-r17`  
实现边界：Run Bootstrap、`bid.plan.requested.v1` 消费、API-40、API-41

## 1. 目标与非目标

Phase 3A 把 Phase 2 已持久化的“当前 Manifest + 不可变 Scope + 规划请求”转换为可复现的
`BidAnalysisRun`。本阶段只建立运行大脑的输入和入口，不执行 Planner、模型、工具、事实提取、
Context Assembler 或 Agent Task。

本阶段明确禁止：

- 创建缺少任一冻结版本的占位 Run；
- 在 Run 创建事务中直接查询可变企业业务表；
- 用环境变量、文件名、MIME、parser hint 或客户端参数替代配置版本；
- 在 API 线程或 Outbox 消费者中直接调用模型、OCR、解析器或 Tool；
- 修改旧 `bid_intake_*` 表、路由、状态机或 Worker；
- 连接、迁移或改动 ECS/CentOS/真实 MinIO/Redis。

## 2. 开关与部署隔离

Phase 3A 使用独立开关：

```text
FEATURE_BID_ASSESSMENT_PHASE3_RUN_BOOTSTRAP=false
```

只有 `FEATURE_BID_ASSESSMENT_V1_RUNTIME=true` 且该开关为 true 时，才允许：

- 消费 `bid.plan.requested.v1`；
- 周期扫描尚未完成 Bootstrap 的规划请求；
- 暴露 API-40/API-41。

默认 false。关闭时不得写 Run 或 processed marker，确保以后启用时仍能重放已持久化规划请求。

## 3. 冻结输入集合

### 3.1 权威输入

每个 Run 必须在同一事务内绑定：

1. 当前 Assessment；
2. 当前不可变 Scope；
3. 当前不可变 Manifest；
4. `status=frozen` 且 `as_of/frozen_at <= evaluation_time` 的最新企业快照；
5. 唯一 `status=active AND active_slot_key=active` 的 Rule Set；
6. 唯一 active Fact Catalog；
7. 唯一 active Prompt Bundle；
8. 唯一 active Tool Registry；
9. 唯一 active Model Profile；
10. 唯一 active Formula Catalog；
11. Run 创建事务从数据库读取的 UTC `evaluation_time`。

Rule Set 还必须满足：

```text
effective_from <= evaluation_time
effective_to is null OR evaluation_time < effective_to
```

六类 active 配置的 `reviewed_at/activated_at` 均不得晚于 `evaluation_time`；active slot 只表示当前
选择，不允许把未来才评审或激活的配置倒灌进当前 Run。

任一项缺失、未评审激活、已过期、来自未来或不再是当前 Scope/Manifest 时，返回或保持
`BID_RUN_INPUT_NOT_READY`，不得部分创建 Run。

### 3.2 企业快照边界

Run Bootstrap 只选择 `bid_enterprise_snapshots` 中已经完成治理和冻结的版本，并通过
`bid_enterprise_snapshot_records` 间接绑定企业数据。它不负责从资质、人员、案例、产能、
定额、费率卡或历史投标等可变表构造快照。

企业快照构建器是独立治理接口；后续实现时必须使用显式 source catalog、字段白名单、来源版本、
有效期和对象引用。本阶段没有合法冻结快照时，规划请求保持待处理。

### 3.3 规范哈希

`input_fingerprint` 对以下对象做规范 JSON SHA-256：

```json
{
  "assessment_id": "...",
  "assessment_scope_id": "...",
  "scope_version": 1,
  "document_manifest_version": 1,
  "enterprise_snapshot_version": "...",
  "rule_set_version": "...",
  "fact_catalog_version": "...",
  "prompt_bundle_version": "...",
  "tool_registry_version": "...",
  "model_profile_version": "...",
  "formula_catalog_version": "..."
}
```

`input_hash` 使用同一对象并增加 UTC RFC 3339 微秒格式的 `evaluation_time`。字段按名称排序，
JSON 使用 UTF-8、无多余空白、稳定字符串/整数表达。`input_fingerprint` 只用于识别相同业务资料与
配置；完整复现和唯一约束必须使用 `input_hash`。

## 4. Run Bootstrap 原子事务

### 4.1 自动 Bootstrap

`bid.plan.requested.v1` 是唯一自动入口。消费者必须校验：

- `aggregate_type=scope`；
- aggregate ID 等于 Payload `scope_id`；
- event/Assessment/Scope/Manifest 所属关系一致；
- Payload `lot_id` 等于不可变 Scope 快照绑定的标段，`resource_version` 为正整数；
- 请求 Scope 和 Manifest 仍为 Assessment 当前版本；
- `requested_run_kind` 为 `preliminary|deep|reanalysis`；
- Assessment `lifecycle_status=active`；
- 当前不存在非终态 Run。

满足条件后，一个事务完成：

1. 锁定 Assessment；
2. 锁定当前 Scope、Manifest、企业快照和六个 active 配置版本；
3. 读取数据库 UTC 时间并计算两个哈希；
4. 创建 `BidAnalysisRun(status=created,current_stage=planning)`；
5. 将 Assessment `active_run_id` 指向新 Run 并递增 `row_version`；
6. 写 `bid.run.created.v1`；
7. 写 `run.bootstrap.create` 审计；
8. 写 `bid-run-bootstrap-v1 + source event_id` processed marker。

事务失败时以上内容全部回滚。

### 4.2 输入未就绪恢复

输入未就绪时消费者不写 processed marker，也不创建失败 Run。Celery 单次 fan-out 返回
`input_not_ready`，独立 Maintenance 扫描按源事件顺序重新尝试。配置或企业快照完成治理后，原规划
请求即可生成 Run，无需伪造第二个业务事件。

扫描只处理 `bid.plan.requested.v1` 且不存在 `bid-run-bootstrap-v1` processed marker 的事件。
同一 Assessment 行锁、Run 非终态检查、Run 唯一约束和 processed marker 共同防止重复创建。

若事件完成等待期间 Assessment 已归档，或业务旅程已明确 `cancelled/superseded`，原规划意图已不可
恢复：消费者写入 ignored 结果和 processed marker，维护扫描不再重试。`cancelled` 后如需重新开始，
必须由用户通过 API-40 显式创建新 Run；`superseded` 不允许再创建 Run。

### 4.3 活跃与重复 Run

非终态集合：

```text
created | planning | queued | running | waiting_input |
waiting_operation | validating
```

此外，`failed AND retryable=true` 属于条件非终态，仍由 API-43 在原逻辑 Run 下创建新 Attempt，
API-40 不得绕过该恢复路径创建第二个 Run；只有 `failed AND retryable=false` 才按终态处理。

- 存在非终态 Run：API-40 返回 `BID_ACTIVE_RUN_EXISTS`；自动消费者若该 Run 已绑定同一
  Scope/Manifest，则把源事件视为已满足并完成 processed marker。
- 相同 `assessment_id + input_hash + run_kind` 已存在：API-40 返回
  `BID_RUN_ALREADY_EXISTS_FOR_INPUT`。
- 相同 fingerprint 的 cancelled Run 可创建新 Run；新 Run 固定新 `evaluation_time/input_hash`，
  `restart_of_run_id` 指向最近 cancelled Run。
- Run 序号在 Assessment 行锁内按 `max(run_sequence)+1` 计算。

## 5. `bid.run.created.v1`

Aggregate：`run`。必需 Payload：

```text
run_id, assessment_id, scope_id, manifest_id,
run_kind, run_sequence, from, to, retryable,
resource_version, assessment_resource_version,
input_fingerprint, input_hash, evaluation_time, progress_url
```

固定 `from=not_created`、`to=created`、`retryable=false`。事件只携带路由、版本与进度读取所需的小
数据，不包含配置正文、企业记录、文档正文、Prompt、对象引用或内部异常。

该事件继续投影为脱敏 `run.status.changed` Public Event。

## 6. API-40 手动创建 Run

```http
POST /api/v1/bid-assessments/{assessment_id}/runs
Idempotency-Key: ...
If-Match: "bid-assessment:{assessment_id}:{row_version}"
```

请求：

```json
{
  "manifest_id": "...",
  "reason": "manual_restart|new_enterprise_snapshot|rule_reanalysis",
  "note": null
}
```

规则：

- Assessment 必须对 actor 可见、active、已有当前 Scope；
- `manifest_id` 必须仍是当前 Manifest；
- 必须通过完整冻结输入门；
- 客户端不能指定 run_kind、模型、工具、规则版本或跳过阶段；
- API-40 固定创建 `run_kind=reanalysis`，后续 Planner 再确定合法执行目标；
- 相同幂等键、ETag 和 body 重放原 202 响应；
- 成功返回 Run `Location/ETag/X-Resource-Version` 和 `Cache-Control: private, no-store`。

稳定错误：428 缺 If-Match、412 版本冲突、409 active Run、409 输入未就绪、409 完全相同输入、
404 不存在或不可见。

## 7. API-41 Run 进度

```http
GET /api/v1/bid-assessments/{assessment_id}/runs/{run_id}
If-None-Match: optional
```

返回 `RunProgressSnapshot`：

- Run 身份、kind、status、row version；
- `input_fingerprint/input_hash`；
- Manifest/Scope 版本和九类冻结版本；
- 面向用户的阶段投影、当前阶段、等待原因；
- 最近一次对用户可见 Run 事件；
- 等待对象和当前已实现的允许操作；
- checkpoint/start/finish 时间。

禁止返回内部 Task ID、DAG、租约、fencing token、Prompt、工具参数、模型原始输出或对象存储引用。

ETag 覆盖完整 actor-visible 投影，格式：

```text
"bid-run:{run_id}:{row_version}:{projection_hash_12}"
```

因此最近公共事件变化即使 Run row version 未变化，也会改变 ETag。200/304 都返回私有强 ETag、
`X-Resource-Version`、`Cache-Control: private, no-cache, max-age=0, must-revalidate` 和
`Vary: Authorization`。

## 8. Planner 边界

`bid.run.created.v1` 是后续 Planner Service 的持久入口，但 Phase 3A 不消费它。下一增量只有在
标准任务注册表、PlanProposal Schema 和确定性 DAG 校验冻结后，才允许：

1. `created -> planning`；
2. 生成但不直接提交 PlanProposal；
3. 确定性校验后原子写 PlanRevision/Task/Dependency；
4. 写 `bid.plan.committed.v1` 和首批 `bid.task.ready.v1`。

模型不得直接创建 Run、提交 Plan、写事实或绕过冻结版本。

## 9. 迁移门禁

Phase 3A 复用：

- `bid_enterprise_snapshots` / records；
- 六个配置版本表；
- `bid_analysis_runs`；
- `bid_assessments.active_run_id`；
- Outbox、processed event、幂等和审计表。

所需字段、FK、唯一约束和事件枚举已由 `0084`—`0086` 提供，因此不新增 Alembic revision，代码
唯一 head 保持 `20260811_0093`。

后续 Fact/Context/Tool/Model 审计表仍需单独冻结后创建新 revision，不得提前塞入 Phase 3A，也不得
应用到 ECS。

## 10. 验证门禁

允许在未取得专项许可时执行：Python 编译、JSON 解析、静态导入审查和 `git diff --check`。

以下均属于报价资料研判 Agent 测试，运行前必须取得用户明确许可：

- Phase 3A 合同/OpenAPI；
- Run Bootstrap 事务、版本选择、ACL、幂等和并发；
- API-03/API-31/API-32/API-40/API-41/SSE 相邻回归；
- 状态机、Outbox/processed marker 和迁移拓扑；
- 任何真实样例、OCR/视觉解析、评测或模型调用。

2026-08-11 获用户明确许可后，已完成合同 64、Phase 3A API/Bootstrap 核心 3、
API-03/API-31/API-32 相邻 8、事务/Outbox/SSE 运行服务 7，共 `82 passed`。本次未运行迁移
拓扑、真实样例、OCR/视觉解析、评测或模型调用，未连接外部环境。
