# 报价资料研判 Agent Phase 4A-1：Plan Continuation + SkillBinding 协议

版本：v0.1-r28  
日期：2026-08-13  
状态：代码增量与本地隔离专项验证完成

## 1. 交付边界

本增量把 Phase 3 单段 8 Task 运行扩展为同一 Run 内的 P0—P4 五段确定性计划，并为每个新 Task 冻结版本化 SkillBinding。本增量不调用模型、LangGraph、MCP、OCR、视觉解析、外部 Tool、真实对象存储或公网。

总开关 `FEATURE_BID_ASSESSMENT_PHASE4_MVP` 和子开关 `FEATURE_BID_ASSESSMENT_PHASE4_PLAN_CONTINUATION` 默认均为 `false`。子开关只能在 Phase 3 Complete Runtime 已启用时开启；Phase 4 代码和 `0098` 不得进入 ECS 正式发布候选。

## 2. Plan Continuation

初筛与 reanalysis Run 使用固定五段模板：

| Stage | 内容 | 新 Task 数 | 最终段 |
|---|---|---:|---|
| P0 | 快照/文档/覆盖基线 + overview/dates/qualification/rejection/fees | 8 | 否 |
| P1 | evaluation/scope/deliverables/contract/schedule + conflict resolution | 6 | 否 |
| P2 | HG01—HG07 | 7 | 否 |
| P3 | synthesis/decision/claim validation/report consistency | 4 | 否 |
| P4 | preliminary report | 1 | 是 |

阶段最后一个 Task 完成且 Run 中没有失败/非终态 Task 时：

- P0—P3：`running -> planning`，写 `bid.plan.continuation_requested.v1`；
- P4：`running -> validating`，沿用 `bid.run.validation_requested.v1`。

Continuation 消费者锁定 Run、Assessment 和 current committed Plan，校验 active 指针、事件 resource version、阶段顺序、当前段全部成功，再在单一事务中：

1. 将旧 current Revision 改为 `superseded` 并清除 current slot；
2. 提交递增 `revision_no` 的新 Revision；
3. 创建新 Task/跨 Revision Dependency；
4. 更新 Run 为 `queued` 和下一 stage；
5. 写 Plan/Task Outbox 与审计；
6. 写 processed marker。

`superseded` 只关闭旧 Revision 的新增任务入口，不否定其 Task/Attempt/Checkpoint/结果。TaskContract 允许从自己的 committed 或 superseded 历史 Revision 重构。

## 3. SkillBinding

Skill 目录位于 `contracts/bid_assessment/v1/skills/`，采用只追加、仓库版本化 JSON artifact；Plan 固定引用 `catalog-1.0.0.json`，不建立可变数据库 active Skill 表，也不允许配置动态 Python import。

每个 Phase 4 TaskDefinition 冻结：

```json
{
  "skill_binding": {
    "skill_id": "bid-tender-fact-extraction",
    "skill_version": "1.0.0",
    "skill_hash": "<canonical artifact sha256>",
    "executor_kind": "langgraph",
    "action_contract": "bid.task.action.v1",
    "output_schema": "fact_assertion_candidates_v1"
  },
  "allowed_tools": ["facts.query", "evidence.search"]
}
```

Plan v2 envelope 同时冻结版本化 `task_catalog_ref/version/hash` 与 `skill_catalog_ref/version/hash`；Task `input_hash` 包含完整 TaskDefinition。Task catalog 采用只追加文件 `task-catalog-1.0.0-draft.1.json`，不引用未来可覆盖的 active 文件。TaskContract 重构时按 frozen catalog ref 加载保留 artifact，校验 catalog hash、artifact hash、TaskType 支持、binding 字段、allowed tools 与完成合同，任何缺失或漂移均 fail closed。

首批目录包含：document scope、tender fact extraction、fact resolution、preliminary gates、preliminary synthesis、preliminary decision、report validation、preliminary report 共 8 个 Skill artifact。`langgraph` 仅是冻结的 executor kind；真实 bounded LangGraph Executor 属于 Phase 4A-2，本增量不执行它。

## 4. 验证与迁移

Phase 4 Run Validator 升级为 v3：保留当前 P4 committed Revision 作为 Validation 外键，同时把 P0—P4 committed/superseded Revision 列表、联合 Task/Dependency 和运行血缘写入 validation input hash，并校验：

- P0—P4 顺序完整且 current 为最终 P4；
- 联合 Plan task keys 与 Run task keys 一致；
- 所有 SkillBinding 可由冻结 artifact 重构；
- 所有历史 TaskContract 可重构；
- 原 Phase 3 Attempt/Checkpoint/Context/Tool/Fence 血缘继续成立。

线性 Alembic `20260812_0098` 只扩展 Outbox CHECK 以允许 `bid.plan.continuation_requested.v1`。降级前若存在 continuation event 或 v2 Plan envelope 会拒绝，防止删除不可变血缘。迁移仅允许独立本地/开发数据库，不得应用到 ECS。

## 5. 本地隔离专项验证

已在用户授权范围内完成 `173 passed / 0 failed`：

- 合同、SkillBinding、P0—P4 Planner/DAG 与配置门禁：`101 passed`；
- Plan Continuation 原子 supersede/commit、幂等消费、审计失败整事务回滚、processed marker、维护扫描恢复、历史 TaskContract、Run Validator v3、API-41/SSE 相邻链：`20 passed`；
- `0083`—`0098` 单线 Alembic 拓扑、模型约束、0098 范围与降级血缘门禁：`52 passed`。

验证仅使用本地 SQLite/静态 Alembic 拓扑，不连接或迁移 ECS/正式数据库，不调用真实样例、OCR/视觉、模型、MCP、外部 Tool 或真实对象存储。真实 bounded LangGraph Executor 仍属于下一增量 Phase 4A-2。
