# BIZ-3b-3-1 显式字段落库与存量回填

> 状态：已完成当前环境实现与回填验证（2026-06-01）。本阶段已新增 Alembic `20260601_0028`，完成显式字段落库、存量回填、读写侧切换和测试验证；仍未启用硬门禁。
> 验证：当前环境 Alembic `20260601_0028 (head)`；回填后 dry-run `updated_task_count=0`、`complete_required_count=0`、`requirement_parse_missing_count=0`、`template_match_missing_count=0`；后端全量 `290 passed`；`ai-web npm run build` 通过。
> 前置依据：`biz-3b-2c-evidence-policy-explicit-fields-design.md`（字段设计契约）、`biz-3b-3-0-key-node-hard-gate-planning.md`（硬门禁总规划，§六 阶段拆分）。
> 上游确认：B 级节点（含 `compact=1` 收款节点）在本步落 `evidence_policy = soft_reminder`，本步不做任何阻断。

---

## 零、实现结果（Done）

本阶段已经从“设计规划”推进到当前环境落地：

1. 数据库：新增 `project_tasks.evidence_requirement` / `evidence_policy` / `is_key_node` 三列，并为 `evidence_policy`、`is_key_node` 建索引；迁移文件为 `alembic/versions/20260601_0028_add_project_task_evidence_policy_fields.py`。
2. 写侧：EPC 模板、单人试运行模板、手工任务创建时都会直接写入显式字段，`description` 只保留展示文本角色。
3. 读侧：`task_evidence_requirement()` 优先读取 `evidence_requirement` 显式列；仅对未回填的 EPC 旧数据保留 `description` 解析兜底。
4. 回填：当前环境真实任务共 `146` 个，回填更新 `123` 个；其中 EPC compact `41` 个、EPC full `82` 个、单人模板 `18` 个、手工/其他 `5` 个；`soft_reminder=123`、`none=23`、`complete_required=0`。
5. 边界：本阶段没有新增硬阻断、没有 `bypass_reason`、没有 `task_completed_bypass_gate`；完成动作仍沿用 BIZ-3b-2a 的无证据软提醒。

---

## 一、本步目标（What）

把"任务是否关键节点、需要什么成果、证据策略是什么"从**运行时解析 `description` 文本**改为**显式列存储**，并完成存量任务的一次性回填。本步是 3b-3-2 硬门禁的**纯数据底座**，本身**不改变任何完成/提交行为**。

完成后系统达到：

1. `project_tasks` 新增 3 个显式列（`evidence_requirement` / `evidence_policy` / `is_key_node`）。
2. 模板实例化时直接写入这 3 列（写侧单一真源）。
3. 存量任务一次性回填这 3 列。
4. 运行时判定（`task_evidence_requirement()` 及证据汇总）改读显式列。
5. 完成校验仍沿用 BIZ-3b-2a 的 soft reminder，**无硬阻断**。

---

## 二、明确的非目标（Not in this step）

以下全部属于 **BIZ-3b-3-2**，本步不做：

- 不做 `complete_required` 硬门禁阻断（不返回 `409 EVIDENCE_HARD_GATE_BLOCKED`）。
- 不做放行机制（不接收 `bypass_reason`，不返回 `422 EVIDENCE_BYPASS_REASON_REQUIRED`）。
- 不写 `task_completed_bypass_gate` 审计事件。
- 不改 `project_task_events` 表结构。
- A 级 4 个关键节点（竣工精装验收 / 隐蔽工程验收 / 结算确认 / 设计成果交付）本步**只作为数据落库**，`evidence_policy` 先落 `soft_reminder`，不在本步升级为 `complete_required`、不阻断。
  > 说明：3b-3-0 设计中 A 级最终目标是 `complete_required`，但其"硬门禁"动作归属 3b-3-2。本步若直接落 `complete_required` 会与"无阻断"非目标矛盾（读侧一旦读到 `complete_required`，3b-3-2 上线即生效，无法分步验收）。因此本步 A 级统一落 `soft_reminder`，3b-3-2 再批量升级为 `complete_required` 并同时上线阻断逻辑，保证"字段落库"与"硬门禁生效"两步可独立验收、独立回滚。

---

## 三、数据库变更

### 3.1 新增列（`project_tasks`）

| 列名 | 类型 | 约束 | 默认 | 含义 |
|------|------|------|------|------|
| `evidence_requirement` | Text | nullable | NULL | 该任务需要交付的成果说明文本（展示用，回填自模板"成果文件"） |
| `evidence_policy` | String(32) | NOT NULL | `none` | 证据策略：`none` / `soft_reminder` / `complete_required` |
| `is_key_node` | Boolean | NOT NULL | false | 是否关键节点（来源于模板 `compact` 标记） |

字段类型与约束与 `biz-3b-2c` 契约一致。`evidence_policy` 取值采用规范三值，不使用已废弃的 `optional`/`submit_remind`。

### 3.2 模型变更（`app/models/project_progress.py`）

在 `ProjectTask`（约 L99）补充上述 3 列定义，与现有 `description`(Text)/`source_type`/`source_id` 同级。`is_key_node` 与 `evidence_policy` 加索引（用于后续门禁/看板筛选），`evidence_requirement` 不加索引。

### 3.3 Alembic 迁移

- 新建 revision，`down_revision="20260601_0027"`（当前 head）。
- 沿用 0027 的幂等模式：用 `sa.inspect(op.get_bind())` 检查列是否已存在，逐列 `op.add_column`，已存在则跳过；`downgrade` 同样逐列 `op.drop_column` 并做存在性保护。
- 服务端默认值：`evidence_policy` 用 `server_default="none"`、`is_key_node` 用 `server_default=sa.false()`，保证存量行迁移即合法；回填脚本随后覆盖真实值。
- **迁移只加列，不做数据回填**（数据回填走 §四 独立步骤，便于单独验证与重跑）。

---

## 四、存量回填（一次性）

### 4.1 回填范围

对所有未取消（`status != "cancelled"`）任务执行；取消任务可一并回填，不影响结果（保持列非空合法即可）。

### 4.2 回填规则

回填依据 `task.source_id`（任务级，反推模板族；**不引入** `project_template_type`）：

| 任务来源 | `is_key_node` | `evidence_requirement` | `evidence_policy` |
|----------|---------------|------------------------|-------------------|
| EPC full 模板（`source_id = qisheng_epc_full_v1`） | 按该节点 `compact` 行标记：`compact=1`→true，否则 false | `parse_epc_task_meta(description)["deliverable"]`，无则 NULL | 有成果要求→`soft_reminder`；无成果要求→`none` |
| EPC compact 模板（`source_id = qisheng_epc_compact_v1`） | 全部 true（compact 模板即关键节点子集） | 同上 | 同上 |
| 单人试运行模板（`source_id = single_user_fitout_v1`） | false | NULL | `none` |
| 手工任务 / 其他 `source_id` | false | NULL | `none` |

要点：

- **`evidence_requirement` 仅初始化"成果文本"**，用一次性 `parse_epc_task_meta()` 解析 `description` 的"成果文件"字段填入。这是文本解析的**唯一一次**使用。
- **`is_key_node` 来源于模板 `compact` 标记**，复用 `QISHENG_EPC_TEMPLATE_TASKS` 中每行的 `item["compact"]`，按节点名（`node`/`title`）匹配回填；不依赖文本解析。
- **`evidence_policy` 本步只落 `none` / `soft_reminder`**：EPC 有成果要求的节点（含 A 级 4 节点、B 级 `compact=1` 收款节点、C 级）统一 `soft_reminder`；无成果要求与非模板任务为 `none`。A 级升级 `complete_required` 留给 3b-3-2。

### 4.3 回填实现形式

- 放在新 Alembic revision 的 `upgrade()` 内，加列之后用 `op.get_bind()` 执行更新；或拆为独立可重跑脚本（`scripts/biz3b31_backfill_evidence_fields.py`）。
- 倾向**独立脚本**：迁移只负责结构，回填可单独执行、可重跑、可在当前环境核对数量后再跑，降低迁移风险。最终形式评审时确认。
- 回填逻辑须与模板实例化写侧（§五）共用同一套规则函数，避免两处规则漂移。

---

## 五、写侧改造（模板实例化单一真源）

在任务创建时直接写入 3 列，使新建任务不再依赖回填：

- `create_qisheng_epc_tasks()`（L853）：在构造 `ProjectTask` 时，按当前 `item` 写 `is_key_node=item["compact"]`、`evidence_requirement=clean_text(item["deliverable"], 2000)`、`evidence_policy="soft_reminder" if item["deliverable"] else "none"`。
- `create_single_user_trial_tasks()`（L809）：写 `is_key_node=False`、`evidence_requirement=None`、`evidence_policy="none"`。
- 手工建任务入口：默认 `is_key_node=False`、`evidence_policy="none"`、`evidence_requirement=None`（除非未来显式传入，本步不开放手工设置入口）。

抽取一个共用的"成果/策略派生"辅助函数（如 `derive_task_evidence_fields(item)`），回填脚本与实例化写侧都调用它，保证单一真源。

---

## 六、读侧改造（运行时判定改读显式列）

`task_evidence_requirement(task)`（L625）当前实现：

```
epc_meta = parse_epc_task_meta(task.description)
if epc_meta and epc_meta.get("deliverable"): return clean_text(...)
return None
```

改为优先读显式列：

```
if task.evidence_requirement: return clean_text(task.evidence_requirement, 2000)
# 回填确认前的兜底：旧行尚未回填时仍解析 description
epc_meta = parse_epc_task_meta(task.description)
...
```

- 显式列有值即直接返回，不再解析文本。
- **保留 `parse_epc_task_meta` 解析作为兜底**，仅在回填完成确认前生效；回填核对通过后，可在后续清理中移除兜底分支（本步不强制移除，列为后续清理项）。
- `task_evidence_summary()`（L632）、`project_evidence_summary()`（L644）依赖 `task_evidence_requirement()`，改造后自动跟随，无需单独改。
- `task_snapshot()`（L610 附近）增加 `evidence_requirement` / `evidence_policy` / `is_key_node` 字段输出，供前端与事件快照使用。

本步**不**在完成接口增加任何基于 `evidence_policy` 的阻断分支——完成校验仍是 3b-2a soft reminder。

---

## 七、前端（可选，最小改动）

- 任务详情/列表若已展示 `evidence_summary.requirement`，本步数据来源切换后展示不变，无需改动。
- 可选：在任务卡片显示"关键节点"标记（`is_key_node`）。本步可不做，留给 3b-3-2 配合门禁提示一起做。
- 若改了 `App.vue`：遵守 Vue DOM 模板禁忌（非 void 元素不自闭合、禁 `<template #slot>`、禁被元素分隔的 v-if/v-else），改完跑 `npm run build`。

---

## 八、验收标准

- [x] 新 Alembic revision `down_revision=20260601_0027`，`alembic upgrade head` 后 `project_tasks` 含 3 新列，`alembic current` 指向 `20260601_0028 (head)`。
- [x] 迁移具备幂等保护：按 0027 风格检查列/索引是否存在，已存在则跳过。
- [x] 回填后：EPC compact 任务 `is_key_node=true`；EPC full 任务按 `compact` 行正确标记；单人/手工任务 `is_key_node=false`、`evidence_policy=none`。
- [x] 回填后：有"成果文件"的 EPC 节点 `evidence_requirement` 非空且 `evidence_policy=soft_reminder`；A 级 4 节点本步也是 `soft_reminder`（不是 `complete_required`）。
- [x] 读侧 `task_evidence_requirement()` 在显式列有值时优先读取显式列，不再依赖 `description` 作为主来源。
- [x] 完成行为未变：有成果要求 + 0 证据完成时仍触发 3b-2a soft reminder（需 `confirm_without_evidence_reason`），**没有** 409 硬阻断。
- [x] 新建 EPC/单人模板任务直接带正确的 3 列值（写侧单一真源生效）。
- [x] 后端测试通过：`290 passed, 9 warnings`；`ai-web npm run build` 通过。

---

## 九、风险与回滚

- **风险：回填规则与写侧漂移** → 用共用派生函数 §五，单一真源。
- **风险：旧行未回填导致读侧返回空** → 读侧保留 `description` 解析兜底（§六），回填核对通过后再清理。
- **风险：A 级误落 `complete_required` 提前触发阻断** → 本步显式约束 A 级落 `soft_reminder`，验收项专门核对。
- **回滚**：迁移 `downgrade` 逐列删除（幂等保护）；回填若用独立脚本，回滚即删列；列删除前确认无 3b-3-2 依赖。

---

## 十、后续衔接（BIZ-3b-3-2，不在本步）

3b-3-1 验收通过后再启动 3b-3-2：A 级 4 节点 `evidence_policy` 批量升级为 `complete_required` 并上线硬门禁（`409 EVIDENCE_HARD_GATE_BLOCKED` / `403 PERMISSION_DENIED` / `bypass_reason` + `422 EVIDENCE_BYPASS_REASON_REQUIRED` / `task_completed_bypass_gate` 快照入 `payload_json`），详见 `biz-3b-3-0-key-node-hard-gate-planning.md`。

---

## 十一、文档同步

实现完成后已同步：`AI_Middle_Office/docs/` 状态、`ROADMAP.md`、`CLAUDE.md`（及 BIZ-3 文档总目录）。
