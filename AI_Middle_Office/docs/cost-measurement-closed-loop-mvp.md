# 成本测算闭环 MVP（COST-MEASURE-1）

## 目标

把“历史人工测算 Excel”从一次性文件转为可复核、可重算、可锁定、可追溯的项目成本测算数据，同时复用现有企业定额主库，不重复建设成本科目与组价底座。

本阶段默认关闭，使用功能开关：

```env
FEATURE_COST_MEASUREMENT=false
```

## 已实现范围

- 上传 `.xlsx/.xlsm` 并预览识别结果；旧 `.xls` 明确要求另存为 `.xlsx`。
- 从工程量清单式报价表识别项目名称、特征、单位、工程量、人工、主材、损耗、辅材及机械、分包、综合单价和合计。
- 新建测算草稿，保存历史综合单价、历史合计和原始行快照。
- 对“项目名称 + 单位”精确一致的行，关联当前 active 企业定额主项；导入时不静默覆盖历史价格。
- 支持逐行调整工程量和组成单价、统一重算、人工复核、版本锁定。
- 锁定后禁止改价；仍有待复核行时，必须填写至少 6 个字的复核结论。
- 锁定版本可预览“成本库 draft 候选”，逐行展示可创建、待复核阻断、现有 active、现有 draft、本批重复和已归档历史。
- 只把用户勾选且已复核的合格行写入 `cost_items.draft`；不自动启用、不覆盖现有 active/draft。
- draft 写入时同步保存成本组成、原项目/文件/Sheet/行号、历史工程量、费率、复核状态和锁定测算版本，并记录成本历史、测算事件和成本敏感操作审计。
- 导出“测算汇总 + 分部分项明细”Excel。
- 全流程记录导入、参数修改、行修改、定额套用、重算和锁定事件。

## 统一计算口径

拆分组价行：

```text
直接费单价 = 人工单价
           + 主材单价 × (1 + 损耗率)
           + 辅材及机械单价
           + 分包单价

管理费单价 = 直接费单价 × 管理费率
利润单价   = (直接费单价 + 管理费单价) × 利润率
综合单价   = 直接费单价 + 管理费单价 + 利润单价
行合计     = 综合单价 × 工程量
```

仅有综合价的措施项按 `composite` 保存，不重复叠加管理费和利润，并强制进入人工复核队列。

## 信达职工食堂样本验证

样本：

- `tmp/xinda_boq.xlsx`
- `tmp/xinda_cost_quote.xlsx`

解析结果：

| 指标 | 结果 |
|---|---:|
| 有效测算行 | 127 |
| 装修清单 | 80 |
| 机电清单 | 47 |
| 管理费率 | 3% |
| 利润率 | 5% |
| 税率 | 9% |
| 历史行合计（税前） | 377,660.74 |
| 统一重算（税前） | 377,110.29 |
| 统一重算与历史差额 | -550.45 |
| 历史公式差异行 | 17 |
| 仅综合价措施行 | 3 |
| 合计待复核行 | 20 |

说明：

- 历史汇总表漏引了给排水小计，不能作为可信总价来源；系统改为按 127 条有效行汇总。
- 给排水部分存在把损耗系数 `1.05` 再按 `1 + 1.05` 使用的公式差异，系统不会继承该异常公式，而是保留历史价并生成复核告警。
- 样本只作为历史成果验证数据，不会自动写入 active 企业定额主库。

## 历史测算沉淀为企业成本库 draft

业务顺序：

```text
导入历史成本 Excel
→ 统一重算并逐行复核
→ 锁定测算版本
→ 预览成本库候选与重复项
→ 勾选合格行
→ 生成 cost_items.draft
→ 成本审批人另行核定为 active
```

价格字段映射：

| 测算值 | 成本库字段 | 口径 |
|---|---|---|
| 重算综合单价 | `price` / `client_tax_excluded_price` | 不含税，含直接费、管理费和利润 |
| 人工单价 | `client_labor_price` | 原测算人工组成 |
| 主材单价 ×（1 + 损耗率） | `client_main_material_price` | 把损耗计入主材成本，原始主材价和损耗率保留在 notes |
| 辅材及机械单价 | `client_auxiliary_material_price` | 当前成本库无独立机械字段，合并保留 |
| 直接费单价 | `client_direct_fee` | 人工、含损耗主材、辅材机械、专业分包之和 |
| 管理费 + 利润 | `client_management_profit` | 两项分别保留在来源 notes |
| 专业分包单价 | `subcontract_composite_price` | 仅拆分组价行且真实分包组成大于 0 时写入；仅综合价措施项不误标为分包 |

重复保护：

- 同名、同规格/特征、同单位的 active：阻断并保留原 active，不覆盖。
- 同名、同规格/特征、同单位的 draft：跳过，不重复创建。
- 同一测算内重复候选：默认选择首条，其他重复行作为可改选候选；提交时同组最多创建一条。
- 已归档历史：允许创建新 draft，但记录归档条目 ID。
- 重复提交同一批次：重新查重并返回跳过结果，保持幂等。

## 数据结构

Alembic：

- `20260713_0046_add_cost_measurement_tables.py`：创建测算主表、明细表和事件表。
- `20260713_0048_upgrade_cost_measurement_precision.py`：将费率、工程量、单价和合计统一为 MySQL `DOUBLE`，避免无精度 `FLOAT` 在驱动读取时丢失金额小数。

- `cost_measurements`：测算项目、费率、金额汇总、锁定信息。
- `cost_measurement_lines`：清单行、历史价格快照、组成价、重算价、复核状态、企业定额关联。
- `cost_measurement_events`：导入、改价、套定额、重算、锁定审计事件。

## 页面与权限

页面：`/admin/cost-measurement`

复用成本角色：

- `cost_viewer`：查看和导入预览。
- `cost_editor`：创建草稿、改价、重算、套用企业定额。
- `cost_approver`：以上权限 + 锁定版本。
- `cost_exporter`：导出测算成果。
- `admin/system_admin`：完整权限。

## 主要 API

- `POST /api/v1/admin/cost-measurements/import-preview`
- `POST /api/v1/admin/cost-measurements/import`
- `GET /api/v1/admin/cost-measurements`
- `GET/PATCH /api/v1/admin/cost-measurements/{id}`
- `PATCH /api/v1/admin/cost-measurements/{id}/lines/{line_id}`
- `POST /api/v1/admin/cost-measurements/{id}/lines/{line_id}/apply-quota/{quota_item_id}`
- `POST /api/v1/admin/cost-measurements/{id}/recalculate`
- `POST /api/v1/admin/cost-measurements/{id}/lock`
- `POST /api/v1/admin/cost-measurements/{id}/cost-drafts/preview`
- `POST /api/v1/admin/cost-measurements/{id}/cost-drafts`
- `GET /api/v1/admin/cost-measurements/{id}/export`

## 启用与验证

```powershell
# 先在目标环境完成备份，再执行
python -m alembic upgrade head

# 内网试运行时同时打开
FEATURE_COST_MEASUREMENT=true
FEATURE_COST_DB=true
```

当前内网验证环境已完成数据库升级并打开功能开关；正式生产环境仍需独立 Runbook，不因本次内网验证自动启用。

## 当前边界

- BOQ 只有工程量、完全没有历史组价或有效企业定额时，仍需要人工询价/定额组价，系统不会凭空生成可信成本。
- 首版自动匹配采用“标准化项目名称 + 单位”精确匹配，模糊推荐和批量确认留到下一阶段。
- 首版导入面向与样本类似的横向报价表；非标多行表头和竖向组价表后续通过人工列映射扩展。
- 当前导出以系统计算结果为权威值，Excel 用于查看与交付，不作为第二套计算引擎。
- 历史测算只自动沉淀为 `draft`；仍需成本审批人核价后才能转为 `active`，只有 active 才参与后续报价与 RAG。
