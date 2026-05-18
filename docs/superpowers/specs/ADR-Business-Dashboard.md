# ADR-Business-Dashboard｜经营驾驶舱数据底座
> 创建日期：2026-05-14
> 状态：规划中
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 决策摘要

经营驾驶舱分阶段建设。当前先建立项目、报价关联、合同、回款、成本、合同调整项和审计数据底座，支持手工录入与 Excel 导入。后续若接入财务软件或合同系统，只替换采集方式，不改变核心模型。

报价单不能直接等同合同或项目。真实业务中可能出现多个报价合并成一个项目，也可能一个报价拆成多个项目，因此 `QuoteHistory` 与 `Project` 使用 `project_quotes` 多对多关系。

## 数据模型

```
projects:
- id, created_at, updated_at
- project_name
- client_name, client_phone
- status: lead / active / completed / cancelled
- archived_at: nullable
- owner_id: FK -> users.id
- notes

project_quotes:
- id, created_at
- project_id: FK -> projects.id
- quote_history_id: FK -> quote_history.id
- relation_type: primary / alternative / partial / merged
- allocation_amount: Numeric(12,2), nullable
- allocation_ratio: Numeric(8,4), nullable
- created_by: FK -> users.id
- notes

contracts:
- id, created_at, updated_at
- project_id: FK -> projects.id
- contract_no: unique
- contract_amount: Numeric(12,2), CHECK (contract_amount > 0)
- signed_at: nullable
- archived_at: nullable
- cancelled_at: nullable
- payment_terms: Text, nullable
- file_object_id: FK -> file_objects.file_id, nullable
- status: draft / signed / archived / cancelled

contract_adjustments:
- id, created_at, updated_at
- project_id: FK -> projects.id
- contract_id: FK -> contracts.id
- adjustment_type: add_item / deduction / discount / change_order
- amount: Numeric(12,2), CHECK (amount > 0)
- reason
- reverses_adjustment_id: FK -> contract_adjustments.id, nullable
- created_by: FK -> users.id
- confirmed_by: FK -> users.id, nullable
- confirmed_at: nullable
- file_object_id: FK -> file_objects.file_id, nullable
- status: draft / confirmed / cancelled

payments:
- id, created_at, updated_at
- project_id: FK -> projects.id
- contract_id: FK -> contracts.id
- amount: Numeric(12,2), CHECK (amount > 0)
- due_at
- paid_at: nullable
- status: pending / paid / cancelled
- notes

project_costs:
- id, created_at, updated_at
- project_id: FK -> projects.id
- cost_type: material / labor / subcontract / rework / other
- amount: Numeric(12,2), CHECK (amount > 0)
- occurred_at
- related_adjustment_id: FK -> contract_adjustments.id, nullable
- replaces_cost_id: FK -> project_costs.id, nullable
- created_by: FK -> users.id
- cancelled_by: FK -> users.id, nullable
- cancelled_at: nullable
- status: active / cancelled
- notes

business_events:
- id, created_at
- entity_type: project / contract / contract_adjustment / payment / cost / import_batch / export
- entity_id
- event_type: created / updated / cancelled / imported / import_confirmed / import_rolled_back / import_rollback_failed / confirmed / paid / signed / archived / unarchived / exported
- operator_id: FK -> users.id, nullable
- before_json, after_json
- ip_address
- user_agent
- trace_id
- notes

business_import_batches:
- id, created_at, updated_at
- batch_id: unique
- import_type: project / contract / contract_adjustment / payment / cost
- source_file
- status: preview / confirmed / cancelled / rolled_back / rollback_failed
- rollback_error: nullable
- rollback_failed_at: nullable
- row_count
- error_count
- created_by: FK -> users.id
```

引用一致性规则：

- `payments.project_id` 是面向看板查询的冗余字段，必须等于 `payments.contract_id -> contracts.project_id`
- 创建或更新回款时，后端必须校验 `payments.project_id = contracts.project_id`；不一致返回 422
- 如数据库能力允许，后续可用复合外键或触发器强化该约束；当前阶段不得允许跨项目合同回款落库

`contracts.payment_terms` 是合同付款条款的自由文本摘要，仅用于展示和审计，不参与回款金额或逾期计算。实际回款计划以 `payments` 表中的 `due_at`、`amount` 和 `status` 为准。后续若需要结构化分期模板，再新增独立字段或表，不复用该文本字段承载计算逻辑。

## 合同调整项口径

增项、减项、优惠、签证和合同修正必须单独追踪，不直接覆盖 `contracts.contract_amount`。原因是老板后续会关心合同变化过程，直接改合同金额会丢失经营解释。

合同状态转换使用独立动作接口，不通过普通 PATCH 完成：

- `POST /api/v1/contracts/{id}/sign`：`draft -> signed`
- `POST /api/v1/contracts/{id}/archive`：`signed -> archived`
- `POST /api/v1/contracts/{id}/cancel`：`draft / signed / archived -> cancelled`，`reason` 必填
- 普通 `PATCH /api/v1/contracts/{id}` 仅用于编辑 `draft` 合同基础信息，不承担 sign / archive / cancel 语义

合同 sign / archive / cancel 必须写入 `business_events`。
合同 cancel 时必须同时处理关联经营数据：

- 关联 `status='pending'` 的 `payments` 自动同步为 `cancelled`，并写入 `business_events`
- 关联 `status='paid'` 的 `payments` 保留原状态用于审计，但经营统计公式必须排除 cancelled 合同
- 关联 `draft` 的 `contract_adjustments` 自动同步为 `cancelled`
- 关联 `confirmed` 的 `contract_adjustments` 保留原状态用于审计，但经营统计公式必须排除 cancelled 合同
- 合同作废不得物理删除合同、回款或调整项记录

调整项金额统一存正数，正负方向由 `adjustment_type` 决定：

- `add_item`：加合同额
- `change_order`：加合同额，适用于签证或确认变更
- `deduction`：减合同额
- `discount`：减合同额

`contract_adjustments.amount` 必须满足 `CHECK (amount > 0)`，零金额和负数均不得落库。负向调整不得用负数表示，必须使用正数 `amount` + `adjustment_type in (deduction, discount)`。

已确认调整项如需冲销，必须新建反向调整项，并写入 `reverses_adjustment_id` 指向原调整项。反向调整项的 `adjustment_type` 必须与原调整项方向相反，例如原 `add_item` 用 `deduction` 冲销，原 `deduction` 用 `add_item` 冲销。后端必须限制同一原调整项在 `draft / confirmed` 状态下最多存在一条反向调整项，避免重复冲销。

只有 `status='confirmed'` 的调整项进入经营指标。`draft` 和 `cancelled` 不参与计算。

当前阶段确认权限先放给 `admin` / `system_admin`，后续再结合业务审批流细化。`staff` 可提出草稿但不得确认；`viewer` 不可创建或确认。

状态规则：

- `draft`：草稿，可修改
- `confirmed`：已确认，进入有效合同金额；确认后不得直接修改金额，如需变更，新增一条反向调整项
- `cancelled`：已作废，不进入指标；作废必须填写原因并写入 `business_events`

附件规则：增项、签证、减项或优惠如有书面凭证，应上传 `file_object_id`。当前阶段不强制上传附件，但确认时必须填写 `reason`。

动作接口：

- `POST /api/v1/contract-adjustments/{id}/confirm`：仅 `admin` / `system_admin` 可用；确认后金额锁定，写入 `business_events`
- `POST /api/v1/contract-adjustments/{id}/cancel`：仅 `admin` / `system_admin` 可用；`reason` 必填，写入 `business_events`
- 普通 `PATCH /api/v1/contract-adjustments/{id}` 仅用于编辑 `draft`，不得承担 confirm / cancel 语义
- 已 `confirmed` 再次 confirm 返回当前对象；已 `cancelled` 后 confirm 返回 409

有效合同金额：

```text
base_contract_amount = sum(contracts.contract_amount where status in signed, archived)
confirmed_additions = sum(contract_adjustments.amount join contracts where contracts.status in signed, archived and contract_adjustments.type in add_item, change_order and contract_adjustments.status=confirmed)
confirmed_deductions = sum(contract_adjustments.amount join contracts where contracts.status in signed, archived and contract_adjustments.type in deduction, discount and contract_adjustments.status=confirmed)
effective_contract_amount = base_contract_amount + confirmed_additions - confirmed_deductions
```

## 成本与返工

返工成本常见，使用 `project_costs.cost_type='rework'` 单独追踪，不另建返工表。若返工与某个变更单相关，可写入 `related_adjustment_id`。

所有成本记录必须写入 `created_by`，用于追溯录入人。`created_by` 创建后不得通过普通编辑修改；如录入错误，应通过 `business_events` 记录修正过程。取消成本记录必须写明原因并保留原记录，不物理删除。

`project_costs.updated_at` 是技术更新时间，只允许在备注、附件引用或取消元信息变化时更新，不代表金额 / 类型 / 项目的普通覆盖编辑。取消成本时同时写入 `status='cancelled'`、`cancelled_at`、`cancelled_by` 和 `updated_at`。

成本修正规则：

- 普通 `PATCH /api/v1/project-costs/{id}` 不允许修改 `created_by`
- 已创建成本的 `amount`、`cost_type`、`project_id` 原则上不得直接覆盖修改
- 金额或类型录错时，使用 `POST /api/v1/project-costs/{id}/cancel` 作废原记录，`reason` 必填
- 如需保留正确成本，重新创建一条替代 `project_costs` 记录，并通过 `replaces_cost_id` 关联原记录
- 同一原成本在 `active` 状态下最多允许一条替代记录，避免重复替代
- `cancelled` 成本不进入 `effective_cost`
- cancel 和替代创建都必须写入 `business_events`，保留 before / after 快照

有效成本：

```text
effective_cost = sum(project_costs.amount where status='active')
```

## 毛利与回款口径

毛利润按合同金额口径计算，不按实际回款计算。

```text
gross_profit = effective_contract_amount - effective_cost
gross_margin = gross_profit / effective_contract_amount
```

回款只用于现金流、回款率和逾期应收，不参与毛利计算。

```text
paid_amount = sum(payments.amount join contracts where payments.status='paid' and contracts.status in signed, archived)
collection_rate = paid_amount / effective_contract_amount
overdue_receivable = sum(payments.amount join contracts where due_at < now and paid_at is null and payments.status='pending' and contracts.status in signed, archived)
```

税费暂不拆分，合同金额按录入金额原样统计。若财务后续要求不含税口径，再新增税额 / 不含税金额字段。

## 项目归档规则

项目完成后可归档。归档不是删除，也不改变经营统计口径。

- `projects.status='completed'` 表示项目已完成
- `projects.archived_at IS NOT NULL` 表示已归档
- 默认列表、默认项目看板隐藏已归档项目
- 经营驾驶舱汇总统计仍计入已归档项目
- 明细查询提供 `include_archived=true` 参数
- 归档和取消归档都必须写入 `business_events`

动作接口：

- `POST /api/v1/projects/{id}/archive`：设置 `archived_at`，写入 `business_events`
- `POST /api/v1/projects/{id}/unarchive`：清空 `archived_at`，写入 `business_events`
- 已归档项目再次 archive 返回当前对象；未归档项目再次 unarchive 返回当前对象
- 普通 `PATCH /api/v1/projects/{id}` 不承担归档 / 取消归档语义

这样既保持老板看经营总账时数字完整，也避免日常操作界面被已完结项目干扰。

## 导入与审计

Excel / CSV 导入必须先生成预览，不得直接落库。每次导入生成 `batch_id`，支持重复导入检测、确认、取消和回滚。

经营数据所有创建、修改、取消、导入确认、回滚都写入 `business_events`。事件只追加不修改。审计事件必须包含 `operator_id`、`ip_address`、`user_agent`、`trace_id` 和变更前后快照。

取消原因统一写入审计事件，不在每个业务实体上重复新增 `cancel_reason` 字段。合同、回款、成本、合同调整项的 cancel 请求必须携带 `reason`，后端写入 `business_events.notes`，并在 `before_json` / `after_json` 中保存取消前后的状态快照。

批次回滚失败时，`business_import_batches.status` 置为 `rollback_failed`，同时写入 `business_events.event_type='import_rollback_failed'`，并在 `business_import_batches.rollback_error` / `rollback_failed_at` 保留结构化错误信息。

## 权限、脱敏与导出

viewer 可以看经营汇总、项目排行和员工效率排行，但不得看到客户姓名、手机号、合同文件、合同明细、回款明细。viewer 不得绕过驾驶舱聚合接口直接访问 `contracts` / `payments` 明细。

viewer 只能访问专用聚合接口，例如 `GET /api/v1/admin/dashboard/business`。该接口对 viewer 使用白名单响应，不得返回原始 `projects`、`contracts`、`payments` 或 `project_costs` 实体对象。

viewer 经营汇总允许字段：

- 汇总数值：项目数、签约额合计、有效合同额、已回款合计、回款率、成本合计、毛利、毛利率、逾期应收合计
- 趋势数据：按日 / 周 / 月聚合的金额和数量
- 排行数据：脱敏项目排行、员工效率排行；项目名称可返回内部项目编号或脱敏名称，不返回 `client_name` / `client_phone`
- 状态分布：项目状态、合同状态、回款状态、成本类型分布

viewer 经营汇总禁止字段：

- 客户姓名、手机号、详细地址、合同编号、合同文件、录音、效果图原文件
- 单笔合同明细、单笔回款明细、单笔成本明细
- `file_object_id`、对象存储 key、签名下载 URL
- 可反推出具体客户身份的备注、附件名、导入原始行内容

合同文件下载属于高风险操作，公网访问时 admin / system_admin 需通过钉钉二次验证。

经营数据和合同明细导出 Excel 仅允许 `admin` / `system_admin`，viewer 不提供脱敏版明细导出。原因是脱敏导出仍可能通过项目名称、金额、时间组合反推出客户和经营信息。

导出要求：

- 当前阶段导出统一写入 `business_events`，`entity_type='export'`，`event_type='exported'`
- 记录导出人、角色、导出类型、筛选条件、行数、IP、时间
- 公网访问时，导出前必须存在当天有效的钉钉登录验证
- 导出文件必须带水印字段：导出人、导出时间、导出用途、系统名称
- 导出文件不长期保存在服务器，生成后短时有效；如需留存，作为 `file_objects` 受权限控制

只有当导出审计出现独立检索、独立保留周期、独立归档或审计量明显高于其他经营事件时，才新增专用 `export_events` 表。新增前不得让部分导出写 `business_events`、部分导出写 `export_events`，避免审计查询口径分裂。

## 关键索引

| 表 | 索引字段 | 说明 |
|----|---------|------|
| projects | `(status, created_at)` | 项目状态聚合 |
| projects | `(archived_at, status)` | 默认隐藏归档项目 |
| project_quotes | `(project_id, quote_history_id)` | 多对多关联 |
| contracts | `(contract_no)` UNIQUE | 合同编号幂等 |
| contracts | `(project_id, signed_at)` | 签约统计 |
| contract_adjustments | `(contract_id, status, adjustment_type)` | 有效调整项统计 |
| contract_adjustments | `(reverses_adjustment_id)` | 反向调整追溯；建议对 active draft/confirmed 反向记录加唯一约束 |
| payments | `(project_id, due_at, paid_at, status)` | 回款与逾期 |
| project_costs | `(project_id, status, occurred_at, cost_type)` | 有效成本 |
| project_costs | `(replaces_cost_id)` | 成本替代追溯；建议对 active 替代记录加唯一约束 |
| business_events | `(entity_type, entity_id, created_at)` | 审计追踪 |
| business_import_batches | `(batch_id)` UNIQUE | 导入幂等 |

## 验收要求

- 一个报价可拆到多个项目，一个项目可合并多个报价
- 拆分 / 合并时记录 `allocation_amount` 或 `allocation_ratio`
- 增项、减项、优惠、签证单独记录，不直接覆盖原合同金额
- 当前阶段 `admin` / `system_admin` 可确认合同调整项；确认后不得直接改金额
- 合同调整项 confirm / cancel 使用独立动作接口，cancel 必须填写 reason
- 毛利率按 `effective_contract_amount - effective_cost` 计算
- 返工成本可按 `cost_type='rework'` 单独统计
- viewer 只能看到脱敏汇总，访问合同 / 回款明细返回 403
- 经营数据和合同明细导出仅允许 admin / system_admin，并写入审计事件
- 导出 Excel 带水印字段：导出人、导出时间、导出用途、系统名称
- 项目归档后默认列表隐藏，但经营统计仍计入
- 项目 archive / unarchive 使用独立动作接口并写审计
- 导入支持预览、确认、重复检测和批次回滚
