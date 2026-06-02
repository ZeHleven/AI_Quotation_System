# BIZ-3b-3-2 complete_required 硬门禁与放行事件

> 状态：已通过当前环境业务验收（2026-06-02）。本阶段不新增数据库结构，继续沿用 Alembic head `20260601_0028`。
> 验证：后端全量 `292 passed, 11 warnings`；`ai-web npm run build` 通过。激活脚本执行后当前库 `total_task_count=146`、`complete_required_count=8`、`soft_reminder_count=115`、`none_count=23`，再次 dry-run `updated_task_count=0`；重启后业务走读已确认缺证据阻断、项目经理/管理员放行、项目动态事件和补证据后正常完成均无问题。

## 一、实现范围

BIZ-3b-3-2 在 BIZ-3b-3-1 显式字段稳定后正式启用首批硬门禁：

1. 新建 EPC 模板任务时，A 级 4 个节点直接写入 `evidence_policy=complete_required`。
2. 存量任务通过 `scripts/biz3b32_activate_project_task_hard_gates.py` 批量升级 A 级节点，不新增 Alembic。
3. 完成任务时，若 `complete_required` 节点缺成果证据：
   - 普通任务负责人返回 `409 EVIDENCE_HARD_GATE_BLOCKED`。
   - 项目经理 / 管理员必须填写 `bypass_reason`，缺失或少于 6 个字返回 `422 EVIDENCE_BYPASS_REASON_REQUIRED`。
   - 放行成功后写入 `task_completed_bypass_gate` 事件和决策快照。
4. 前端任务表显示关键节点 / 需证据标记；完成时按权限进入阻断提示或放行原因弹窗。

## 二、首批硬门禁节点

当前只启用 A 级 4 个节点：

| 阶段 | 节点 | 成果要求 |
|---|---|---|
| 设计方案 | 设计成果交付 | 移交成果确认表 |
| 生产交付 | 隐蔽工程验收 | 隐蔽工程验收报告及备案 |
| 生产交付 | 竣工精装验收 | 精装验收表甲方签字确认及备案 |
| 生产交付 | 结算确认 | 结算确认书 |

B 级节点仍保持 `soft_reminder`，待成本部 / 项目管理部确认后再决定是否纳入硬门禁。

## 三、后端行为

`POST /api/v1/admin/project-tasks/{task_id}/complete`

非硬门禁节点保持 BIZ-3b-2a 行为：有成果要求但无证据时必须填写 `confirm_without_evidence_reason`，写入 `task_completed_without_evidence`。

硬门禁节点缺证据时：

```json
{
  "bypass_reason": "甲方签字件已线下确认，扫描件次日补传"
}
```

放行事件 `task_completed_bypass_gate` 的 `payload_json` 包含：

- `is_key_node`
- `evidence_policy`
- `evidence_requirement`
- `evidence_count_at_decision`
- `bypass_reason`
- `decided_by_user_id`
- `decided_by_username`
- `decided_by_role`
- `decided_by_roles`
- `task_status_before`
- `task_status_after`
- `decided_at`

快照是放行当时的冻结值，后续补证据或调整策略不回写该事件。

## 四、当前环境数据激活

本阶段新增脚本：

```powershell
C:\Users\12521\miniconda3\python.exe scripts\biz3b32_activate_project_task_hard_gates.py
C:\Users\12521\miniconda3\python.exe scripts\biz3b32_activate_project_task_hard_gates.py --apply
```

当前环境执行结果：

```json
{
  "total_task_count": 146,
  "a_level_candidate_count": 8,
  "updated_task_count": 8,
  "complete_required_count": 8,
  "soft_reminder_count": 115,
  "none_count": 23,
  "template_match_missing_count": 0
}
```

再次 dry-run：

```json
{
  "updated_task_count": 0,
  "complete_required_count": 8,
  "soft_reminder_count": 115,
  "none_count": 23
}
```

说明：当前库存在两个 EPC 项目，所以 A 级 4 个节点共激活 8 条任务。

## 五、前端交互

- 任务表展示 `关键节点` 和 `需证据` 标签。
- 缺证据的硬门禁节点，证据按钮显示为危险色。
- 无放行权限用户点击完成时，只展示阻断提示，不提供强制完成入口。
- 项目经理 / 管理员点击完成时，必须填写 `bypass_reason`，确认后调用完成接口。
- 项目动态将 `task_completed_bypass_gate` 显示为「关键节点放行完成」。

## 六、当前环境业务验收

2026-06-02 用户反馈验收没问题，确认以下运行态场景通过：

- 缺证据的 A 级 `complete_required` 节点，普通任务成员完成时会被硬门禁阻断。
- 项目经理 / 管理员缺证据完成时，必须填写放行原因后才能完成。
- 放行完成后，项目动态可见 `关键节点放行完成` / `task_completed_bypass_gate`。
- 补上传成果证据后，同类节点可正常完成，不需要放行。
- 提交到 80% 不被硬门禁阻断；B 级与普通成果节点仍保持软提醒。

## 七、边界

- 不新增数据库结构，Alembic head 仍为 `20260601_0028`。
- 不接钉钉 / 企微自动同步。
- 不开放手工调整 `evidence_policy` 的后台入口。
- 单人试运行模板与手工任务默认不进入硬门禁。
- 提交到 80% 不做硬阻断，只在完成到 100% 时触发硬门禁。
