# 旗胜投标机会研判 Agent Phase 3B：Planner、确定性 DAG 校验与 Plan Commit

版本：`v0.1-r18`  
日期：`2026-08-11`  
状态：协议、代码与本地隔离专项验证已完成  
隔离要求：不得连接或改动 ECS、CentOS、真实 MinIO/Redis；不得进入正式发布候选

## 1. 本阶段目标

Phase 3B 消费 Phase 3A 产生的 `bid.run.created.v1`，完成以下闭环：

1. 从 Run 已冻结的 Scope、Manifest、企业快照和配置版本构造 `PlannerInput`；
2. 生成受限 `PlanProposal`；
3. 执行确定性 DAG、权限、预算、顺序和版本一致性校验；
4. 在一个数据库事务内写入 PlanRevision、Task、Dependency、Outbox、审计和 processed marker；
5. 将 Run 按 `created -> planning -> queued` 推进并发布首个 `bid.task.ready.v1`；
6. 用独立维护扫描恢复未完成的 `bid.run.created.v1` 消费。

本阶段不执行 Task、不创建 Attempt、不读取文档正文、不调用 OCR/视觉/模型、不生成事实、维度、决策或报告。模型 Planner 仍是未来适配器边界；即使以后启用模型，模型也只能产生候选 `PlanProposal`，不能直接提交计划或改变运行状态。

## 2. Planner 权限边界

Planner 只能：

- 从标准任务目录选择 `task_type`；
- 提议最多 8 个新任务；
- 声明任务依赖、目标、事实槽、受治理执行配置；
- 提议最多 3 个问题候选；
- 给出 `expected_stage_after`、原因码和置信度。

Planner 不能：

- 调用工具或模型工具链；
- 写事实、证据、门槛、维度、决策或报告；
- 创建 Run、TaskAttempt 或 AsyncOperation；
- 自定义工具权限、上下文配置、预算配置或完成合同；
- 跳过硬门槛、报告校验或冻结版本；
- 直接提交 PlanRevision 或修改 Run/Assessment 状态。

Phase 3B 的初始 Planner 是 `bid-deterministic-bootstrap-planner-v1`，没有模型调用路径。未来模型产生的 proposal 必须经过同一个 `bid-plan-validator-v1`。

## 3. PlannerInput 权威来源

`PlannerInput` 只从 Run 的冻结外键和所属数据域构造：

- Assessment：`id + goal=bid_go_no_go + scope_id`；
- `bound_versions`：Manifest/Scope 版本、企业快照版本、Rule/Fact Catalog/Prompt/Tool/Model/Formula 版本和 Run `evaluation_time`；
- 文档清单：当前 Manifest 成员、Document 的治理类型、DocumentVersion ID、当前 ParseHead 指向的权威 ParseRun 状态；
- Task 摘要：同一 Run 已持久化任务的 key 和状态；
- Fact/Gate/Question 摘要：本阶段尚无运行输出时使用全零或空集合；
- 允许任务：标准目录中的 49 个类型，顺序和集合均冻结；
- 规划限制：`max_dynamic_tasks=8`、`max_dependency_depth=3`。

禁止从文件名、MIME、扩展名或 `parser_hint` 推断标段或重新执行 Phase 2 标段检测。Phase 3B 只复用已提交 Manifest、ParseHead 和不可变 Scope。

## 4. 标准任务注册表

机器目录仍为 `contracts/bid_assessment/v1/task-catalog.json`，包含 49 个标准任务类型。运行时注册表为每个类型确定：

- category；
- objective 默认值；
- `tool_profile`；
- `context_profile`；
- `budget_profile`；
- `completion_contract`；
- `allowed_tools`；
- priority。

proposal 可以选择任务类型和合法依赖，但上述执行字段必须与注册表完全一致。注册表以 canonical JSON 计算 SHA-256，并与 catalog version 一起写入 Plan envelope 和审计。冻结版本：

```text
bid-assessment-standard-tasks-v1@1.0.0-draft.1
```

这一定义不替代 Run 已冻结的 `tool_registry_version_id`：前者约束标准任务模板，后者约束该 Run 实际工具注册表版本，两者都进入校验绑定。

## 5. 初始确定性 DAG

初始 proposal 固定为 8 个任务：

```text
phase3b.01.bind_assessment_snapshot
  -> phase3b.02.inventory_documents
    -> phase3b.03.build_coverage_baseline
      -> phase3b.04.extract_tender_overview
      -> phase3b.05.extract_critical_dates
      -> phase3b.06.extract_qualification_requirements
      -> phase3b.07.extract_rejection_clauses
      -> phase3b.08.extract_guarantees_and_fees
```

根节点深度为 0，最大动态依赖深度为 3。Commit 后只有 `bind_assessment_snapshot` 为 `ready`，其他 7 个任务为 `blocked`；共写 7 条依赖。该阶段不创建 TaskAttempt。

初始 proposal 不包含 `classify_documents`、`parse_*`、`detect_lots` 或 `bind_selected_lot`，因为这些权威输入已经由 Phase 2 和 Scope 冻结，运行大脑不得重复推断或覆盖。

## 6. 确定性校验门

`bid-plan-validator-v1` 必须全部通过以下 9 项检查：

1. `task_type_allowlist`：任务类型属于冻结的 49 项目录，目录集合和顺序不得漂移；
2. `acyclic_dependencies`：任务 key 唯一、依赖存在、无自依赖、组合图无环；
3. `scope_version_consistency`：Assessment/Scope/Manifest 所属关系和 Run 冻结输入一致，validated hash 绑定 Run `input_hash`；
4. `tool_profile_permissions`：tool/context profile 与标准注册表完全一致；
5. `budget_limits`：budget profile 与注册表一致；
6. `max_8_dynamic_tasks`：单次 proposal 最多新增 8 个任务；
7. `max_dependency_depth_3`：仅计算本次动态子图，已提交任务可作为深度 0 的外部根；
8. `hard_gate_ordering`：硬门槛必须位于 `resolve_fact_conflicts` 之后，维度任务必须位于全部七项硬门槛之后；
9. `report_validation_ordering`：综合、最终决策、Claim/Evidence 校验、报告一致性校验和报告生成必须按冻结顺序依赖。

校验器同时拒绝未知依赖、重复 task key、伪造 completion contract、非法 supersede target 和非冻结规划限制。Bootstrap proposal 的 `supersede_tasks` 必须为空；动态重规划与已提交 Plan 的 supersede 原子协议留到后续阶段。

## 7. 可复现 Plan envelope

`BidPlanRevision.proposal_json` 保存完整 envelope，而不是只保存模型文本：

```json
{
  "schema": "bid.plan.commit.envelope.v1",
  "generator_version": "bid-deterministic-bootstrap-planner-v1",
  "validator_version": "bid-plan-validator-v1",
  "task_registry_version": "bid-assessment-standard-tasks-v1@1.0.0-draft.1",
  "task_registry_hash": "...",
  "run_input_hash": "...",
  "planner_input_hash": "...",
  "proposal_hash": "...",
  "planner_input": {},
  "proposal": {},
  "validation": {
    "status": "accepted",
    "checks": [],
    "validated_hash": "..."
  }
}
```

`validated_hash` 绑定 generator/validator/registry 版本和 hash、Run input hash、PlannerInput hash 与 proposal hash。Task `input_hash` 绑定 Run input hash 和完整 TaskDefinition。

## 8. Plan Commit 原子事务

消费者：`bid-plan-commit-v1`。输入：`bid.run.created.v1`。

消费者首先校验 event aggregate、Assessment/Run 所属关系，以及 payload 中 Run/Scope/Manifest/run kind/run sequence/input fingerprint/input hash 与数据库 Run 完全一致；随后锁定 Run、Assessment 和已提交 Plan slot。

一个成功事务依次完成：

1. 构造 PlannerInput 和确定性 PlanProposal；
2. 运行全部确定性校验；
3. PlanRevision 在事务内依次经过 `proposed -> validating -> committed`，最终 `row_version=3`、`committed_slot_key=committed`；
4. Run 在事务内依次经过 `created -> planning -> queued`；
5. 创建 8 个 Task 和 7 条 Dependency；
6. 写 `bid.plan.committed.v1`；
7. 对首个 ready Task 写 `bid.task.ready.v1`；
8. 写 `plan.commit` 审计；
9. 写 `bid-plan-commit-v1 + source event_id` processed marker。

事务失败时上述写入全部回滚。Run 行锁、每 Run 唯一 committed slot、Task 逻辑输入唯一约束、Outbox dedupe 和 processed marker 共同提供并发幂等。

## 9. 事件边界

### `bid.plan.committed.v1`

Aggregate：`plan_revision`。必需 payload：

```text
plan_revision_id, run_id, revision_no, validated_hash,
task_count, ready_task_count, task_registry_version,
validator_version, resource_version
```

该事件仅供内部运行服务消费，不投影内部 DAG 给用户。

### `bid.task.ready.v1`

Aggregate：`task`。必需 payload：

```text
task_id, task_key, task_type, run_id, plan_revision_id,
stage_code, status, message, completed_units, total_units,
resource_version
```

Outbox payload 不包含 prompt、文档正文、企业记录、工具参数或对象存储引用。Public Projector 只投影既有脱敏字段为 `run.stage.changed`；Run row version 对每个 ready 投影递增，避免同一 Run 的公开 projection key 冲突。

## 10. 恢复与停止条件

- 单次 fan-out 在独立事务消费 `bid.run.created.v1`；
- 每 30 秒的维护任务只扫描该事件类型且不存在 `bid-plan-commit-v1` marker 的事件；
- 已有 committed Plan 时，消费者返回已满足结果并补 processed marker，不重复写 Task/Event；
- Run 已进入不可恢复终态或不再是 Assessment active Run 时，写 ignored marker 后停止；
- 事件与冻结 Run 不一致属于协议错误，不得自动猜测或修复绑定；
- 校验失败不得降级为跳过校验或直接建 Task。

独立默认关闭开关：

```text
FEATURE_BID_ASSESSMENT_PHASE3_PLANNER=false
```

该开关不隐式开启 Phase 3A、Phase 2 或总运行时开关。

## 11. 迁移门禁

Phase 3B 复用 `0085/0086` 已有结构：

- `bid_plan_revisions`；
- `bid_tasks`；
- `bid_task_dependencies`；
- `bid_analysis_runs`；
- Outbox、processed event、幂等和审计表。

现有字段、约束和事件枚举足以实现本阶段，因此不新增 Alembic revision；代码唯一 head 保持 `20260811_0093`。目标 ECS 最近只读基线仍为 `20260808_0082`，本阶段不得连接、迁移、备份、重启或改动目标环境。

## 12. 实现与验证门禁

实现文件：

- `app/services/bid_task_registry.py`；
- `app/services/bid_plan_commit.py`；
- `app/tasks/bid_assessment_tasks.py`；
- `schemas/bid_assessment/v1/planner.schema.json`；
- `contracts/bid_assessment/v1/event-catalog.json`。

专项测试定义：

- 49 项注册表与确定性 proposal；
- allowlist、环、深度、预算/profile、硬门槛和报告顺序拒绝；
- Run/Plan/Task/Dependency 原子提交；
- Outbox、processed marker、审计和重放幂等；
- API-41/SSE 相邻投影；
- 合同与迁移拓扑相邻回归。

上述测试属于报价资料研判 Agent 测试，必须取得用户对 Phase 3B 的明确许可后才能运行。2026-08-11 获得许可后，完成合同 65、Planner/DAG 5、Plan Commit 与 API-40/API-41 相邻 4、迁移拓扑 47、事务回滚/维护恢复 2、Outbox/processed marker/SSE 运行服务 10，共 `133 passed`。测试仅使用本地隔离 SQLite 与假消息/存储边界；未运行真实样例、OCR/视觉解析、评测或模型调用，未连接外部环境。
