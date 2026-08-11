# 报价资料研判 Agent Phase 1 数据迁移设计

日期：2026-08-10；更新：2026-08-11
状态：已解除迁移设计门禁；`0083`—`0091` 与 API-01/API-03/API-10/API-11/API-12/API-13/API-14/API-15/API-16
已完成代码层实现和本地隔离验证

## 1. 已确认基线

- 目标 ECS 数据库 `alembic_version` 已只读确认：`20260808_0082`。
- 仓库代码迁移唯一 head：`20260811_0091`。
- 候选 `0082` 保留，作为本系列迁移的正式前驱，不撤回、不复用编号。
- 本阶段只提交代码、迁移和测试；不连接、不备份、不升级 ECS 数据库。
- 正式环境应用任何新 revision 前，仍必须重新查询 head、完成全量备份与恢复演练。

## 2. 表名碰撞修正

旧投标模块已经拥有 `bid_parse_runs`，它使用自增整数主键并绑定旧
`bid_projects`。新规范中的同名对象使用 UUID 主键并绑定
`bid_document_versions`，两者不是同一聚合，也不能通过扩列安全兼容。

因此新数据域采用：

- 规范草案名：`bid_parse_runs`
- 实际新表名：`bid_document_parse_runs`

该表属于 Phase 2 文档证据能力，不在本次 Phase 1 迁移中创建。旧
`bid_parse_runs`、`bid_intake_*`、`bid_evidence_*` 和其他旧投标表均保持不变。

## 3. Revision 拆分

### `20260810_0083`：Assessment 输入与范围基础

创建：

- `bid_assessments`
- `bid_file_objects`
- `bid_documents`
- `bid_document_versions`
- `bid_document_manifests`
- `bid_manifest_documents`
- `bid_upload_batches`
- `bid_upload_batch_files`
- `bid_upload_batch_deactivations`
- `bid_lot_candidates`
- `bid_assessment_scopes`

`bid_assessments.current_manifest_id` 在 Manifest 表创建后补外键；
`active_run_id` 只先保留可空 UUID 指针，在 `0085` 创建 Run 后补外键。
上传批次使用可空 `open_slot_key` 和唯一键保证同一 Assessment、同一
purpose 最多一个开放批次；终态批次必须在状态事务中清空该键。

### `20260810_0084`：冻结企业与配置版本

创建：

- `bid_enterprise_snapshots`
- `bid_enterprise_snapshot_records`
- `bid_rule_sets`
- `bid_fact_catalog_versions`
- `bid_prompt_bundles`
- `bid_tool_registry_versions`
- `bid_model_profile_versions`
- `bid_formula_catalog_versions`

所有已发布/冻结版本只追加，不原地修改业务内容。每张版本表同时保留
人类可读 `version` 和内容哈希，Run 通过外键绑定具体版本记录。

实现补充：六类可激活配置使用可空 `active_slot_key`。只有 `active` 状态允许
该字段取固定值 `active`，单列唯一约束据此保证每类配置最多一个 active 版本；
`draft/retired` 必须为 NULL。active 版本还必须具有 reviewer、reviewed_at 和
activated_at，retired 版本必须具有 retired_at。企业快照只有在 hash、冻结人和
冻结时间齐备后才能进入 `frozen/retired`。

### `20260810_0085`：运行、租约、Checkpoint 与问答骨架

创建：

- `bid_analysis_runs`
- `bid_plan_revisions`
- `bid_tasks`
- `bid_task_dependencies`
- `bid_task_attempts`
- `bid_checkpoints`
- `bid_async_operations`
- `bid_question_rounds`
- `bid_questions`
- `bid_answer_drafts`
- `bid_answer_sets`
- `bid_answers`

实现补充：Run 以外键冻结 Manifest、Scope、企业快照、规则集、Fact Catalog、Prompt、
工具注册表、模型 Profile 和公式目录版本；Run 的 Scope/Manifest 复合外键保证它们与
Assessment 同域。`bid_assessments.active_run_id` 使用 `(id, active_run_id)` 复合外键，
不能指向其他 Assessment 的 Run。

Task 的 `current_attempt_id` 使用 `(id, current_attempt_id)` 复合外键，只能指向本 Task
的 Attempt。Attempt 在创建时原子分配第一次租约和大于等于 1 的 fencing token；每次
重新获取租约必须创建新 Attempt 并使用更大的 token。Checkpoint 以
`(task_attempt_id, action_seq)` 唯一并保持不可变，实际写入服务还必须以当前 fencing
token 做 compare-and-swap，数据库结构本身不替代该运行时校验。

同一 Run 最多一个已提交 Plan、最多一个已发布 Question Round；单轮问题通过
`question_order BETWEEN 1 AND 3` 限制为最多 3 个。Answer Draft 是可变草稿，不触发
运行唤醒；AnswerSet 和 Answer 是不可变提交记录。异步操作的幂等键限定在 Task 内，
避免相同输入哈希跨 Assessment 复用。

随后为 `bid_assessments.active_run_id` 补外键。租约所有权、心跳时间、
`lease_until` 和单调递增 `fencing_token` 位于 Attempt；Task 只保存当前
状态和当前 Attempt 指针。状态转换必须由状态服务在行锁事务中执行。

Context Manifest、模型调用、工具调用和结果属于 Phase 3 工具网关与上下文
能力，届时单独创建；Checkpoint 的 Context 引用在此之前保持可空。

### `20260810_0086`：事件、幂等、兼容映射与审计

创建：

- `bid_outbox_events`
- `bid_processed_events`
- `bid_public_events`
- `bid_idempotency_records`
- `bid_legacy_resource_links`
- `bid_audit_log`

Outbox 必须与业务变更同事务写入；消费者必须与业务处理同事务写入
`bid_processed_events`。Public Event 是 SSE 至少一次投影的顺序真相源。

实现补充：Outbox 保存完整小型事件信封、投递状态、重试时间和短租约；同一生产者的
`dedupe_key` 唯一。消费者以 `(consumer_name, event_id)` 作为不可变处理边界。
Public Event 以 `(assessment_id, sequence_no)` 固化 Assessment 内顺序，并用
`(source_event_id, projection_key)` 保证同一内部事件的每个公共投影只写一次；一个
Outbox Event 可以合法投影多个不同公共事件。SSE 只能读取该表，Redis 只负责唤醒。
Public Event 进一步用 `origin_type` 区分 `outbox` 与 `stream_control`：Outbox 投影必须
保存 `source_event_id`；首次连接 Snapshot、过期游标 Reset 和正常关闭等连接控制事件
允许没有伪造的内部事件来源，但只能使用冻结的三类控制事件。

API 幂等作用域固定为 actor、HTTP method 与规范化 route template，表中同时保存请求
哈希、处理状态、原 HTTP 状态、响应快照/引用和保留期。旧资源映射首版只开放已经存在
且可由真实外键验证的 Assessment、Manifest 和 Run；Evidence/Report 在对应数据表创建
后通过后续 revision 扩展。审计日志是带 before/after/metadata/record 哈希的不可变追加
记录，不提供 `updated_at` 或 `row_version`。

### `20260810_0087`：允许 API-12 文件接收 Outbox 事件

不创建新表或业务字段，只重建 `bid_outbox_events.event_type` Check Constraint，把
`bid.upload_file.received.v1` 加入冻结事件集合。升级后 API-12 才能在最终文件登记事务中
提交该事件；降级会在仍存在此类事件时明确失败，禁止静默删除历史 Outbox 记录。

### `20260811_0088`：允许 API-13 草稿文件移除 Outbox 事件

不创建新表或业务字段，只重建 `bid_outbox_events.event_type` Check Constraint，把
`bid.upload_file.removed.v1` 加入冻结事件集合。升级后 API-13 才能在草稿文件移除事务中
提交该事件；降级会在仍存在此类事件时明确失败，禁止静默删除历史 Outbox 记录。

### `20260811_0089`：允许 API-14 基线文档停用 Outbox 事件

不创建新表或业务字段，只重建 `bid_outbox_events.event_type` Check Constraint，把
`bid.upload_batch.deactivation_added.v1` 加入冻结事件集合。升级后 API-14 才能在停用集合
真实变化的事务中提交该事件；同原因重复无操作不写事件。降级会在仍存在此类事件时明确
失败，禁止静默删除历史 Outbox 记录。

### `20260811_0090`：固化 API-15 提交血缘

为 `bid_document_manifests` 增加可空 `change_note`；为 `bid_upload_batches` 增加
`committed_manifest_id` 与 `committed_at`。提交 Manifest 通过
`(assessment_id, committed_manifest_id)` 复合外键保证与 Batch 属于同一 Assessment，并用
`committed_manifest_id` 唯一约束保证一个不可变 Manifest 只由一个批次产生。Check Constraint
要求 `committed` 状态必须同时具有两个提交血缘字段，所有非 committed 状态必须同时为空。

降级前若存在任一提交血缘或非空 Manifest change note，迁移明确失败，避免静默抹除 API-15
审计来源。该门禁必须在线读取真实数据，因此 `0090` 的 `--sql` 离线降级按设计拒绝；正式
降级演练只能在一次性 MySQL 8 实例执行。该 revision 不新建解析、Scope、Run 或 Plan 表，也
不改变旧 `bid_intake_*`。

### `20260811_0091`：固化 API-16 放弃与延迟清理时间轴

为 `bid_upload_batches` 增加 `abandon_reason`、`abandoned_at`、`cleanup_after` 和
`cleanup_completed_at`，并增加 `status + cleanup_completed_at + cleanup_after` 到期扫描组合
索引。Check Constraint 要求 abandoned 状态必须同时固化 reason、放弃时间和计划清理时间；
所有非 abandoned 状态不得携带这些字段，清理完成时间不得早于放弃时间。

revision 同时重建 Outbox 事件约束，允许 `bid.upload_batch.abandoned.v1`。升级会为历史人工标记
的 abandoned 批次写入明确 legacy reason，并从原更新时间起延后一天清理，禁止升级后立即误删。
若存在任一放弃血缘或对应事件，降级明确失败；该门禁必须在线读取真实数据，因此 `0091`
离线降级按设计拒绝。revision 不删除 BatchFile、FileObject 或物理对象，也不改变旧
`bid_intake_*`。

## 4. 本阶段明确延期的表

- Phase 2：`bid_document_parse_runs`、`bid_evidence_fragments`、
  `bid_evidence_scope_links`、`bid_lot_candidate_evidence` 及事实/计算表。
- Phase 3：`bid_context_manifests`、`bid_model_calls`、`bid_tool_calls`、
  `bid_tool_results`。
- Phase 4–6：维度、门槛、决策、报告和 `bid_owner_overrides`。

延期对象不会用无外键字符串或 JSON 临时替代表关系，避免形成需要长期兼容的
过渡 schema。

## 5. 数据库约束

- 所有新表使用 `bid_` 前缀、InnoDB、`utf8mb4`。
- 业务主键使用应用生成的 `VARCHAR(36)` UUID；不使用自增业务 ID。
- 所有表具有 `created_at`；可变实体增加 `updated_at` 和 `row_version`。
- 哈希统一使用 64 字符十六进制 SHA-256 字段。
- 金额使用 `DECIMAL(20,4)`，比例/置信度使用 `DECIMAL(10,6)`。
- 发布对象默认 `RESTRICT` 删除；普通业务流程不级联删除证据链。
- 状态字段受机器合同和数据库 Check Constraint 双重约束。
- MySQL 不支持通用的条件唯一索引；活跃 Run 和开放上传批次同时使用显式
  指针/槽位键、唯一约束与行锁事务保证。

## 6. 验证与上线门禁

每个 revision 必须通过：

1. `python -m alembic heads` 只有一个 head。
2. 从 `0082` 生成 MySQL 离线升级 SQL。
3. 迁移合同测试验证表名、前驱、外键、唯一键、索引、状态约束和降级顺序。
4. 一次性空 MySQL 8 实例执行完整 upgrade/downgrade 演练；不得使用现有本机
   MySQL、旧 CentOS 数据卷或生产备份。
5. 正式上线前重新确认 ECS head，完成备份、SHA-256、影子恢复和 API/Worker
   同版本发布；应用自动迁移保持关闭。

当前运行服务、默认关闭开关、事务边界与本地验证记录见
`docs/bid-assessment-runtime-services-phase1-20260810.md`。当前没有连接或升级 ECS，
实际 ECS head 仍按已确认的 `20260808_0082` 管理。
