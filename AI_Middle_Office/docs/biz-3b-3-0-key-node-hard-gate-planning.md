# BIZ-3b-3-0 关键节点证据硬门禁实现规划

更新时间：2026-06-01

## 定位

本文是 BIZ-3b-3「关键节点证据硬门禁」开发前的**实现规划**，只做规划与口径决策，不写代码、不新增 Alembic、不改库。

前置依赖：

- BIZ-3b-2a 无证据软提醒（已落地）：有成果要求且无证据时，提交/完成会提醒，完成必须填写 `confirm_without_evidence_reason`，否则 `422 EVIDENCE_CONFIRM_REASON_REQUIRED`。
- BIZ-3b-2b 缺证据汇总（已落地）：项目详情已能统计缺证据节点、无证据已完成节点。
- BIZ-3b-2c 证据策略与 EPC 字段显式化设计（已完成设计，未改库）：锁定 `evidence_policy ∈ {none, soft_reminder, complete_required}`、显式列 `evidence_requirement` / `evidence_policy` / `is_key_node`、`is_key_node` 播种规则、放行决策快照写入 `project_task_events.payload_json`。

本阶段聚焦三件事：

1. 哪些 EPC 节点从 `soft_reminder` 升级为 `complete_required`。
2. 缺证据时是绝对禁止完成，还是允许项目经理 / 管理员填写原因后放行。
3. 放行事件的权限、原因、快照字段和前端交互。

---

## 一、哪些节点升级为 `complete_required`

### 1.1 取值与候选池关系

- `complete_required` 必须是 `is_key_node=true` 的子集：先是关键节点，才谈得上硬门禁。
- `is_key_node` 的播种口径已在 BIZ-3b-2c 锁定（`qisheng_epc_compact_v1` 全 true；`qisheng_epc_full_v1` 按行 `compact` 标记；`single_user_fitout_v1` 默认 false）。
- 但**不是所有关键节点都要硬门禁**。关键节点约 40+ 个（完整模板里 `compact=1` 的行），若全部硬门禁，单人试运行阶段会被频繁卡住，违背「先透明、后管控」的推进节奏。
- 因此 `complete_required` 取关键节点中**成果客观、不可逆、对外交付/收款/签约**的小子集，其余关键节点仍留 `soft_reminder`。

### 1.2 分级建议（待成本部 / 项目管理部确认）

> 节点名称取自 `QISHENG_EPC_PROCESS_ROWS`，括号内为该行成果文件（`deliverable`）。最终硬门禁清单由成本部 / 项目管理部逐个签字确认，本文只给保守建议。

**A 级——首批建议直接设为 `complete_required`**（成果客观、可外部核对、漏证后果重）

| 阶段 | 节点 | 成果文件 | 设硬门禁理由 |
|---|---|---|---|
| 生产交付 | 竣工精装验收 | 精装验收表甲方签字确认及备案 | 竣工里程碑，甲方签字，缺证据等于无法证明交付 |
| 生产交付 | 隐蔽工程验收 | 隐蔽工程验收报告及备案 | 隐蔽工程一旦覆盖不可逆，必须留验收报告 |
| 生产交付 | 结算确认 | 结算确认书 | 结算金额对账依据，缺证据直接影响回款 |
| 设计方案 | 设计成果交付 | 移交成果确认表 | 设计向甲方移交的交付凭证 |

**B 级——建议设为 `complete_required`，但请部门确认能否稳定留证**（收款 / 签约 / 关键验收）

| 阶段 | 节点 | 成果文件 |
|---|---|---|
| 生产交付 | 基层验收 | 基层验收表甲方签字确认 |
| 生产交付 | 精装预验收 | 精装预验收报告备案 |
| 招投标 | 中标/合约 | 中标通知书 |
| 招投标 | 分包合约签署 | 合约签署备案 |
| 市场开发 | 设计合同签署 | 全案合同及附件 |
| compact=1 的收款节点 | 设计定金收取 / 设计进度款收取 / 工程预付款收取 / 月进度款申请 / 结算款收款 / 工程质保金回款 | 到账水单 |

> 收款节点范围说明：仅纳入模板里 `compact=1` 的收款节点，**以上表列出的具体节点为准**。EPC 模板里并非所有收款节点都是关键节点，例如 `竣工款收款` 当前 `compact=0`，本期不进硬门禁，避免以后误以为「所有收款节点都设硬门禁」。

**C 级——保持 `soft_reminder`，本期不硬门禁**

- 其余 `is_key_node=true` 的过程性节点（如各类会议纪要、调研、物料手册、日报周报等）。这些成果以内部文档为主，硬门禁收益低、阻断成本高。

**默认口径**

- `single_user_fitout_v1` 单人模板：本期不设任何 `complete_required`，全部 `soft_reminder` 或 `none`。单人试运行优先顺畅，不引入硬阻断。
- 手工任务（`source_type=manual`）：默认 `evidence_policy=none`，不参与门禁。

### 1.3 落地方式（实现期，本文不执行）

- 硬门禁清单不写死在判定逻辑里，而是落到模板侧：实例化 A/B 级节点时把 `evidence_policy` 种为 `complete_required`。
- 用「节点名 + 阶段」白名单或在 `QISHENG_EPC_PROCESS_ROWS` 增加一列 `gate` 标记，BIZ-3b-3 实现时二选一，规划阶段先确认清单内容。
- 运行时只读 `project_tasks.evidence_policy`，不再解析 `description`（遵守 BIZ-3b-2c 单一事实来源契约）。

---

## 二、绝对禁止 vs 允许放行

### 2.1 结论：有条件硬门禁，允许放行（推荐）

不采用「绝对禁止完成」，采用「默认阻断 + 有权限者填原因放行」：

- 普通成员（任务负责人 / `project_member`）：`complete_required` 节点缺证据时**不能完成**，返回明确错误，引导先补证据。
- 项目经理 / 管理员（`project_manager` / `admin` / `system_admin`）：可在缺证据时填写放行原因后**强制完成**，并留下放行审计。

### 2.2 理由

- 绝对禁止在单人试运行和线下纸质留证场景下会卡死流程（例如甲方签字件还没扫描回传）。
- BIZ-3b-2a 已确立「完成必须填原因」的软门禁范式，硬门禁只是把它升级为「有权限才放行 + 留快照」，演进连续、用户可理解。
- 放行有审计快照，事后可在缺证据汇总和项目动态中追溯，管理上可控。

### 2.3 与软提醒的区别

| 维度 | `soft_reminder`（现状） | `complete_required`（本期目标） |
|---|---|---|
| 普通成员缺证据完成 | 填原因即可完成 | **禁止**，必须先补证据 |
| 项目经理/管理员缺证据完成 | 填原因即可完成 | 填原因放行，写 `task_completed_bypass_gate` |
| 提交到 80% | 软提醒，可继续 | 本期**不阻断提交**，只阻断完成（见 2.4） |
| 事件 | `task_completed_without_evidence` | `task_completed_bypass_gate` |

### 2.4 门禁只卡「完成」，不卡「提交」

- 硬门禁只作用于 `complete`（→100%），不阻断 `submit`（→80%）。
- 提交阶段沿用 BIZ-3b-2a 软提醒，允许先把任务推到「待确认」，给补证据留出窗口。
- 这样避免门禁过早介入打断正常推进节奏。

---

## 三、放行事件设计

### 3.1 权限

| 角色 | `complete_required` 缺证据时能否完成 |
|---|---|
| 任务负责人 / `project_member` | 否，返回阻断错误 |
| `project_manager`（该项目经理） | 是，填原因放行 |
| `admin` / `system_admin` | 是，填原因放行 |
| `project_viewer` | 否，本就无完成权限 |

放行权限复用现有项目权限判定（项目经理或平台管理员），不新增角色。

### 3.2 错误与原因

- 普通成员触发阻断：返回 `409 EVIDENCE_HARD_GATE_BLOCKED`，消息提示「该关键节点要求成果证据，请先登记证据再完成，或联系项目经理放行」。普通成员本身**有任务操作权限**，只是被业务门禁拦住，属于状态冲突，用 `409 Conflict` 比 `403 Forbidden` 更准确。
- 真正没有任务操作权限的用户（如 `project_viewer`）仍按现状返回 `403 PERMISSION_DENIED`，与门禁阻断区分。
- 有权限者放行：请求体必须带 `bypass_reason`（非空，建议 ≥ 6 字），缺失时返回 `422 EVIDENCE_BYPASS_REASON_REQUIRED`。
- `bypass_reason` 与 BIZ-3b-2a 的 `confirm_without_evidence_reason` 区分命名，避免语义混淆（一个是软提醒确认，一个是硬门禁放行）。

### 3.3 快照字段

放行完成时写事件 `task_completed_bypass_gate`，快照存入 `project_task_events.payload_json`（Text，已存在），建议字段：

```json
{
  "is_key_node": true,
  "evidence_policy": "complete_required",
  "evidence_requirement": "精装验收表甲方签字确认及备案",
  "evidence_count_at_decision": 0,
  "bypass_reason": "甲方签字件已线下确认，扫描件次日补传",
  "decided_by_user_id": 12,
  "decided_by_username": "admin",
  "decided_by_role": "system_admin",
  "task_status_before": "submitted",
  "task_status_after": "done",
  "decided_at": "2026-06-01 18:30:00"
}
```

口径：

- `evidence_count_at_decision` 记录放行当下的有效证据数（正常为 0，留作事后核对）。
- 快照是「决策当时」的冻结值，后续即使补了证据或改了策略，也不回写本事件。
- 不新增列，全部塞进现有 `payload_json`，与 BIZ-3b-2c 设计一致。

### 3.4 策略变更事件（可选，纳入本期规划范围确认）

- 若实现期允许在后台手工调整某任务 `evidence_policy`（如临时降级），需配套事件 `task_evidence_policy_changed`，payload 记录 `from` / `to` / 操作人 / 原因。
- 本期建议**先不开放手工改策略入口**，`evidence_policy` 只由模板播种 + 回填决定，降低复杂度；`task_evidence_policy_changed` 留作后续阶段。

### 3.5 前端交互

完成按钮点击后，按策略和权限分三种表现：

1. **非 `complete_required` 节点**：沿用 BIZ-3b-2a 现有行为（有成果要求且无证据时填确认原因）。
2. **`complete_required` 且当前用户无放行权限**：弹阻断提示框，不提供「强制完成」入口，引导「去补证据」或「请项目经理放行」。
3. **`complete_required` 且当前用户有放行权限**：弹放行框，标题明确「关键节点缺成果证据」，展示成果要求文字，必须填写 `bypass_reason`，确认后调用完成接口并带 `bypass_reason`。

其它：

- 项目动态 / 缺证据汇总中，`task_completed_bypass_gate` 显示为「关键节点放行完成」，可点开看放行原因。
- 关键节点在任务表 / 证据抽屉用标记（如「关键节点·需证据」徽标）提示，避免用户到完成时才发现被卡。

---

## 四、本期边界

- 只规划，不写代码、不新增 Alembic、不改 N8N / 钉钉 / 企微。
- 提交（→80%）不做硬阻断，只卡完成（→100%）。
- 单人试运行模板与手工任务本期不进硬门禁。
- 不开放手工改 `evidence_policy` 入口（`task_evidence_policy_changed` 留后续）。
- A/B 级硬门禁节点清单为保守建议，最终以成本部 / 项目管理部确认为准。

## 五、进入 BIZ-3b-3 开发的前置确认项

1. 确认 A 级 4 个节点直接设 `complete_required`；确认 B 级清单中哪些纳入首批。
2. 确认「有条件硬门禁 + 有权限放行」方案，而非绝对禁止。
3. 确认放行权限为项目经理 + 平台管理员，普通成员一律阻断。
4. 确认 `bypass_reason` 必填、阻断错误码命名、放行事件 `task_completed_bypass_gate` 与快照字段集。
5. 确认本期不开放手工改策略入口。

## 六、实现阶段拆分（建议两步走，不一口气做完）

BIZ-3b-3 开发不一次性做完硬门禁，拆成两个可独立验收的子阶段，降低风险：先验字段和汇总，确认回填无误后，再上线会卡住任务完成的硬门禁。

### BIZ-3b-3-1：显式字段落库与存量回填

- 新增 Alembic revision，落 BIZ-3b-2c 三个显式列：`evidence_requirement` / `evidence_policy` / `is_key_node`。
- 回填历史任务：按 BIZ-3b-2c 播种规则回填 `is_key_node`，按本文 A/B 级清单回填 `complete_required`，其余按现状种 `soft_reminder` / `none`。
- 把运行时判断从解析 `description` 切换为只读显式列（遵守 BIZ-3b-2c 单一事实来源契约）。
- 本子阶段**不引入任何阻断**：完成行为仍沿用 BIZ-3b-2a 软提醒，只是数据来源换成显式列，便于先验证字段和缺证据汇总是否正确。

### BIZ-3b-3-2：`complete_required` 硬门禁与放行事件

- 在 3b-3-1 字段稳定后，再实现完成判定的硬阻断、放行接口、`task_completed_bypass_gate` 事件与快照、前端三态交互。
- 这样若 3b-3-1 回填有问题，可先回退/修字段，不会一上来就卡住任务完成。

两步均完成后，硬门禁才正式生效。
