# 报价资料研判 Agent 内部试运行冻结包：阶段2数据库收口

> 记录ID：`BID-INTAKE-INTERNAL-PILOT-STAGE2-DB-CLOSEOUT-20260801-V1`
>
> 核验时间：2026-08-01 12:11:41 +08:00
>
> 状态：`stage2_database_alignment_verified_head_0081`

## 1. 本阶段结论

数据库已经从阶段1记录的`20260731_0077`推进到当前代码头
`20260801_0081`，主服务和报价资料研判Agent均正常。

本任务准备执行阶段2时，环境已经由其他当前工作完成0081前备份和迁移，
因此没有重复生成大备份、没有重复执行迁移，也没有再次重启服务；本阶段
只核验现有备份、迁移版本、表结构、服务健康、Agent readiness和回归测试。

0078—0080删除旧执行系统、商务台账和成本测算专用结构；0081只新增预算
计价运行的不可变草稿快照表。四个迁移均不删除或改写报价资料研判Agent、
Evidence、Checkpoint或Policy表。

## 2. 备份证据

0081前备份：

| 项目 | 值 |
|---|---|
| 文件 | `backups/pre_0081_pricing_snapshot_recovery_20260801_092701.sql` |
| 大小 | 809,317,170 bytes / 771.82 MiB |
| SHA-256 | `64DC1AA14C68DBB474B1C02031C051551FFE1EB4991CB88AFD3CCADF891580A3` |
| 格式 | MySQL 8.0 mysqldump |
| 数据库 | `ai_quotation` |
| 完成标记 | `Dump completed on 2026-08-01 09:27:13` |

该文件包含业务数据库完整内容和认证数据，只能作为受控恢复备份，禁止
提交Git、复制到公开目录或在文档中展开数据正文。

0078—0080前备份继续保留：

`backups/retired_modules_pre_0080_20260731_182547.sql`

## 3. 迁移状态

```text
alembic current: 20260801_0081 (head)
alembic heads:   20260801_0081 (head)
```

0081迁移文件：

- `alembic/versions/20260801_0081_add_pricing_run_draft_snapshots.py`
- SHA-256：
  `e512b17b66b150b31b43057581bfab626d1938bbbf0dbf635dd75db1ab867acf`
- 只创建`budget_project_pricing_run_draft_snapshots`；
- 当前表存在，核验时为2条记录；
- 不引用报价资料研判Agent表。

## 4. 数据结构核验

以下14张关键表全部存在：

- `bid_intake_assessments`
- `bid_intake_agent_runs`
- `bid_intake_human_decisions`
- `bid_intake_run_events`
- `bid_intake_worker_heartbeats`
- `bid_intake_checkpoints`
- `bid_intake_checkpoint_blobs`
- `bid_intake_checkpoint_writes`
- `bid_evidence_documents`
- `bid_evidence_blocks`
- `bid_evidence_manifests`
- `bid_evidence_read_audits`
- `bid_evidence_index_jobs`
- `budget_project_pricing_run_draft_snapshots`

以下9张退役表全部不存在：

- `execution_tasks`
- `execution_task_events`
- `meeting_notes`
- `task_drafts`
- `meeting_note_revisions`
- `client_inquiry_events`
- `cost_measurements`
- `cost_measurement_lines`
- `cost_measurement_events`

结论：退役迁移已生效，Agent和Evidence持久化结构未受损。

## 5. 运行状态核验

主服务：

- 9000监听进程：PID `3156`；
- `/health/ready=ready`；
- database：`ok`；
- Celery broker/worker：`ok`；
- Celery Worker：1。

Agent专属预检：

- `ready_to_start=true`；
- blockers：空；
- Agent Worker：`小智:24268`，状态`online`；
- Runtime：`bid_intake_runtime_phase5a`；
- 模型：`deepseek-v4-flash`；
- Policy：`qs_bid_decision_policy_2026_01`；
- Checkpoint：`sqlalchemy`；
- MCP session：`persistent`；
- Evidence manifest：v1；
- 搜索后端：`hybrid_rrf`；
- 索引：`completed`。

本阶段仍是配置预检，`mcp_probe`和`model_probe`为`skipped`，没有增加
远程调用或模型费用。

## 6. 安全开关

迁移后开关保持不变：

```text
PUBLIC_ACCESS_ENABLED=false
BID_INTAKE_AGENT_RUNTIME_ENABLED=true
BID_INTAKE_FACT_COVERAGE_MODE=shadow
TENDER_EVIDENCE_CANDIDATE_COVERAGE_SELECTION=false
TENDER_EVIDENCE_SUFFICIENCY_ASSESSMENT=false
```

本阶段没有启用拒答门，也没有启用任何被拒绝的候选召回策略。

## 7. 测试

重新执行：

```text
tests/test_bid_intake_fact_coverage.py
tests/test_bid_intake_runtime_phase4a.py
tests/test_bid_intake_runtime_config.py
tests/test_bid_intake_retrieval_evaluation.py
tests/test_tender_query_planner.py
```

结果：`89 passed, 1 warning in 4.95s`。警告仍是既有requests依赖组合
提示；没有运行固定38题、Agent正式研判、Holdout、Challenge或A/B。

## 8. 阶段判断与下一步

阶段2数据库阻塞已关闭：

`stage2_database_alignment_verified_head_0081`

仍未关闭的发布风险：

1. Agent主体和依赖大多未进入Git；
2. `app/main.py`、`app/models/bidding.py`、`start_all.ps1`和
   `App.vue`等共享文件混有其他业务改动；
3. 拒答门仍未实际启用；
4. 真实MCP/模型canary尚未执行；
5. 当前只有1个Agent Worker，未配置备用模型。

下一阶段为阶段3“可复现发布版本整理”：先拆分Agent专属文件和共享
集成补丁、生成精确发布清单与哈希，不改开关、不运行正式A/B。阶段3
完成并报告后，才申请进入拒答门受控启用。
