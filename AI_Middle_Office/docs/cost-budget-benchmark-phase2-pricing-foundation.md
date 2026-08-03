# 成本预算对标阶段二：企业定额计价底座（P2-1）

更新日期：2026-07-15

## 1. 阶段目标

阶段二把第一阶段形成的“预算项目 + 正式清单快照”接到唯一正式成本主库，形成可追溯、可重复、可部分完成的项目成本计价结果。

P2-1 的交付边界是：

- 只读取项目当前正式清单，即 `budget_project_profiles.active_import_batch_id` 与 `active_import_revision_id` 共同指向的已确认不可变修订；
- 只读取 `enterprise_quota_versions` 中严格唯一的 `status=active AND is_active=true` 版本；
- 对正式清单中的全部 `bill + is_standard_item=true` 行执行保守、确定性匹配；
- 保存不可变的计价 run、逐行结果、候选证据和生命周期事件；
- 提供部分计价概览、逐行结果和候选证据的只读界面；
- 企业定额或正式清单更新后，重新计价生成新 run，不覆盖旧结果。

本阶段不做：

- 不优化“项目测算/采购结果”；
- 不读取、回退或写入 `cost_items`、旧 materials、RAG、N8N、Dify；
- 不复用 `cost_measurements` 或 `project_cost_import_*`；
- 不把未匹配项、缺价项伪装为 0 元完整成本；
- P2-1 不提供脱离企业定额主库的手工单价覆盖；
- P2-1 的候选与证据只读，人工改选和正式确认留在后续增量。

## 2. 唯一正式数据链

```text
预算项目
  -> active_import_batch_id
  -> active_import_revision_id
  -> immutable standard_rows_json
  -> bill + is_standard_item=true

严格唯一 enterprise_quota active
  -> enterprise_quota_items.unit_price
  -> 自动匹配和成本拆分证据

两份快照
  -> 新建不可变 pricing run
  -> 逐行结果、候选、审计事件
```

“最新上传批次”不等于正式清单。页面可以查看未确认批次，但创建计价 run 时必须显式提交正式批次、正式修订和预期定额版本，服务端在同一事务中重新校验，禁止静默切换到“最新”数据。

## 3. active 企业定额硬门禁

计价服务单独执行严格门禁，不复用任何通过 `.first()` 选择 active 版本的旧帮助函数。

- `status=active AND is_active=true` 必须恰好一条；
- 0 条时返回 `BUDGET_PRICING_ACTIVE_QUOTA_REQUIRED`；
- 多于 1 条时返回 `BUDGET_PRICING_ACTIVE_QUOTA_AMBIGUOUS`；
- `status` 与 `is_active` 出现分裂状态时返回 `BUDGET_PRICING_ACTIVE_QUOTA_INCONSISTENT`；
- run 固定保存定额版本 ID、编码、名称、源文件哈希和目录哈希；
- 历史 run 永远按自身固定的版本回看，不随当前 active 自动改写。

## 4. 行状态与部分计价语义

匹配、工程量和价格是三个独立维度，不能混成一个状态。

### 4.1 匹配状态

- `auto_matched`：唯一、可解释且单位兼容的自动命中；
- `manual_matched`：后续人工选择产生的新 run；
- `ambiguous`：有候选但不能唯一确定；
- `unmatched`：没有足够证据的候选；
- `unit_conflict`：名称候选存在但单位不兼容。

### 4.2 价格状态

- `priced`：已命中有效正数 `enterprise_quota_items.unit_price` 且工程量有效；
- `quantity_unresolved`：已命中有效单位成本，但工程量为空、异常或安全值为 0；
- `missing_unit_price`：匹配定额的综合单价为空或不大于 0；
- `pending_match`：未匹配或仍有歧义；
- `unit_conflict`：单位冲突，未计价。

### 4.3 工程量异常口径

工程量为空、0、非数字或异常时：

- 仍然识别项目并执行定额匹配；
- 匹配成功时仍展示定额、单位和单位成本；
- 行金额安全保存为 `0`；
- `amount_included=false`；
- 标记 `quantity_unresolved`；
- 整单必须保持 `partial`，不能显示为完整预算。

### 4.4 汇总口径

- `priced_subtotal` 只表示已完成有效金额行的小计；
- 任一行未匹配、歧义、单位冲突、缺价或工程量未解决时，`completeness_status=partial`；
- 部分计价时 `total_cost=NULL`；
- 只有全部正式清单行完整计价时，`completeness_status=complete` 且 `total_cost=priced_subtotal`；
- API 金额使用定点十进制字符串，未计价使用 `null`，前端显示“—”。

## 5. 首版匹配与计算规则

首版采用保守、确定性、可解释规则：

1. 定额编码精确且单位兼容，唯一时自动匹配；
2. 规范化名称、规格/工作内容和单位共同命中，唯一时自动匹配；
3. 规范化名称与单位唯一命中时自动匹配；
4. 模糊名称只形成候选，不自动采用；
5. 同名多规格候选保持 `ambiguous`；
6. 单位缺失或不兼容不自动采用；
7. 候选按分数降序，再按定额编码和 ID 排序，保证同输入同结果；
8. `enterprise_quota_items.unit_price` 是 P2-1 唯一有效综合单价来源；
9. 人工、主材、辅材、机械拆分只作证据和汇总，不替代缺失的综合单价；
10. 所有计算先用 `Decimal(str(value))` 转换，以 `ROUND_HALF_UP` 保留 6 位后持久化到 `NUMERIC`。

## 6. 不可变数据模型

Alembic `20260716_0051` 新增：

- `budget_project_pricing_runs`：一次输入快照与计价结果版本；
- `budget_project_pricing_run_lines`：逐行输入、匹配、价格、拆分与警告快照；
- `budget_project_pricing_match_candidates`：候选排名、定额快照和匹配证据；
- `budget_project_pricing_events`：追加式生命周期审计；
- `budget_project_profiles.active_pricing_run_id`：后续正式确认时显式指向当前计价版本。

run、line 和 candidate 不提供原地更新/删除接口。重新匹配、人工改选或切换定额版本都应创建 child run，并通过 `parent_run_id` 保留血缘。

## 7. P2-1 API

- `GET /api/v1/admin/budget-projects/{project_id}/pricing-readiness`
- `POST /api/v1/admin/budget-projects/{project_id}/pricing-runs`
- `GET /api/v1/admin/budget-projects/{project_id}/pricing-runs`
- `GET /api/v1/admin/budget-projects/pricing-runs/{run_identifier}`
- `GET /api/v1/admin/budget-projects/pricing-runs/{run_identifier}/lines`
- `GET /api/v1/admin/budget-projects/pricing-runs/{run_identifier}/lines/{line_identifier}/candidates`
- `GET /api/v1/admin/budget-projects/pricing-runs/{run_identifier}/events`

创建请求必须显式带：

```json
{
  "source_import_batch_id": 16,
  "source_import_revision_id": 19,
  "expected_active_quota_version_id": 3,
  "reason": "基于当前正式清单创建首版计价"
}
```

## 8. 功能开关与权限

- 新增独立开关 `FEATURE_BUDGET_PRICING=false`；
- 只有 `FEATURE_BUDGET_PROJECTS` 与 `FEATURE_BUDGET_PRICING` 同时开启时，计价接口和界面才可用；
- 开关缺失或模块状态缺失一律 fail-closed；
- 查看成本结果需同时具备预算项目对象访问权和成本查看角色；
- 创建 run 需成本编辑/核定角色；
- 归档项目可回看历史，但不能创建新 run；
- 成本金额只从独立计价接口返回，不进入普通预算项目列表响应。

## 9. P2-1 验收基线

1. 联昇正式清单只取 198 条 `bill` 行，99 条主材参考不进入 run；
2. active 定额为 0、2 或状态标志分裂时均硬阻断；
3. 服务不读取 `cost_items`、`cost_measurements`、`project_cost_import_*`；
4. 未匹配行的单位成本和行金额为 `NULL`；
5. 工程量异常但匹配成功时展示单位成本、行金额为 0、整单 partial；
6. 同名多规格不自动误选，单位冲突不自动采用；
7. 部分结果有 `priced_subtotal`，但 `total_cost IS NULL`；
8. 历史 run 固定保存原清单和定额版本快照；
9. 前端创建 payload 只取正式批次/修订，不取当前查看批次；
10. `null` 金额显示“—”，partial 明确提示“成本尚不完整”；
11. 0051 迁移前后企业定额、旧成本、报价、项目测算和采购相关表行数不变；
12. 原第一阶段解析、确认、启用和版本保护测试继续通过。

## 10. 后续增量

P2-1 验收后再按独立增量推进：

- P2-2：人工选择定额并创建 child run；
- P2-3：部分结果显式确认、版本 supersede 与项目 active 指针；
- P2-4：费率、取费和成本分析报表；
- P2-5：完整定额库导入后的批量重匹配与覆盖率回归。

上述增量继续遵守同一主库边界：`enterprise_quota active` 是唯一正式成本来源。

## 11. 当前环境验收（2026-07-15）

- 当前 MySQL 已升级到 `20260716_0051 (head)`，`FEATURE_BUDGET_PROJECTS=true`、`FEATURE_BUDGET_PRICING=true`；
- 严格唯一 active 定额为版本 3：`qs-enterprise-quota-20260626-v1 / 广东旗胜企业定额 1.0（20260626）`；
- 版本 3 物理含 474 条定额，健康门禁后 65 条可参与匹配且均有综合单价；409 条被拒绝，其中 407 条缺少定额名称、2 条文本异常；
- 联昇项目 15 使用正式批次 16、确认 revision 19 完成 3 个不可变 run；最终 run 3（UUID `dc0cb938-6943-4920-bc89-3b708302efbb`）以 `parent_run_id=2` 保留完整父子血缘；
- run 固定 198 条正式清单，结果为 `matched=0 / ambiguous=14 / unmatched=182 / unit_conflict=2`，保存 55 条只读候选快照；
- 当前覆盖率 0%，`priced_subtotal=0.000000`、`total_cost=NULL`、`completeness_status=partial`。这不是把成本算成 0，而是明确表示当前不完整定额尚未产生任何可自动采纳的唯一匹配；
- 0051 前后 22 张受保护业务表计数一致；运行态共新增 3 个 run、594 条 run line、165 条候选和 3 条事件，项目 active 计价指针仍为空；
- Chrome 真实登录态已验证 run 2 的 readiness、版本信息、partial 警告、逐行空值显示、匹配/计价筛选和候选定额编码检索；run 3 另以运行态服务验证数据库统一时钟，`created_at <= ready_at`；
- 最终聚焦审计回归 `47 passed`（此前 P2 聚焦集 `68 passed`），全量回归 `868 passed / 2 个冻结旧 BIZ-2c 口径失败 / 29 warnings`，没有新增失败；后端 compileall 与 `ai-web` production build 通过；
- 运行态证据：`output/pre_budget_0051_20260715_p2_pricing_foundation/runtime_acceptance_project15.json`。

结论：P2-1 已达到当前不完整定额条件下的功能验收目标，可以进入 P2-2；当前 0% 覆盖率同时说明，完整企业定额导入前不能把本次 partial 结果当作正式完整项目成本。
