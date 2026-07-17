# 成本预算对标 Phase 1：预算项目与清单标准化底座交接记录

> 状态：Phase 1 P1 已完成代码、迁移、真实样本与运行态 API 闭环验收
> 日期：2026-07-15
> 功能开关：`FEATURE_BUDGET_PROJECTS`
> 数据库迁移：`20260714_0049` + `20260715_0050`

## 1. 阶段目标与完成结论

本阶段建立“预算项目 + Excel 清单导入 + 人工列映射 + 标准行持久化”的最小底座，使一份来源不固定的预算清单能够归属到明确项目、按批次留痕，并形成可追溯的标准化工程量输入。

Phase 1 已完成项目工作区、导入批次、Sheet 映射、标准行、前后端页面/API、对象级权限和安全工程量规则。原 Phase 1 信达样本验收事实继续有效：标准化结果 127 项，同一文件连续导入形成 2 个互不覆盖的独立批次，5 条缺失的 `m³` 单位从映射原始单元格安全恢复，1 条重复表头被识别且未作为正式清单项；127 项原始工程量均为空，计算工程量均安全置为 0。

2026-07-15 的 P1 收口以“联昇集团办公楼装饰工程清单.xlsx”完成第二类真实样本复验。工作簿共 8 个 Sheet，按用途分为元数据/说明、汇总分析、正式清单和主材参考。正式清单共 198 行，其中装饰 121 行、机电 73 行、措施费 4 行；工程量分别从装饰 `K`、机电 `J`、措施费 `E` 的总量列提取，结果为 `valid=198 / zero=0 / missing=0`。主材表单独归类为 `material_reference` 99 行，工程量状态为 `valid=86 / zero=11 / missing=2`，不并入正式清单统计。两条二级/重复表头被排除，显式项目特征列存在时项目名称保持原文，不再按名称中的规格数字误切分。

P1 同时完成导入修订和正式版本治理：每次初始解析/重映射都形成不可变 revision 快照；批次按 `parsed -> confirmed -> active -> superseded` 流转；项目档案使用显式活动批次和活动 revision 双指针，不再把“最新上传”误当正式版本；重映射使用期望修订号做乐观并发校验，冲突返回 `409`；后端返回项目/批次对象级 capabilities，前端按能力收敛操作；预算项目继续复用唯一 `projects` 身份，但从既有项目进度和经营统计口径中排除，避免导入测试项目污染业务看板。

最终运行态 API 闭环已在项目 ID `15`（`PRJ-20260715-003`）通过。正式活动批次 ID `16`、UUID `5bdcd778-1d70-40e2-9206-ccf23d575ca1`，初始 revision `18` 经重映射形成并确认 revision `19`；确认和激活均返回 200，项目活动指针明确指向批次 16/revision 19。随后上传的批次 17 保持 `parsed`，未替换活动指针；项目内恰好 1 个 `active` 批次。批次 15 是修复前形成的 `parsed` 批次，未确认、未激活，不参与正式版本。项目为验收留痕而保留在活动工作区，未归档。

## 2. 本期范围

- 复用既有 `projects` 作为唯一项目主实体，在其上扩展预算项目档案。
- 提供 Vite `/admin/budget-projects` 项目工作区，支持项目创建、查询、编辑、归档、Excel 导入、批次查看、标准行查看和 Sheet 列映射修正。
- 接受 `.xlsx` / `.xlsm`，执行文件、Sheet 行列数、空文件和损坏文件校验。
- 每次上传生成独立导入批次；即使文件 SHA-256 相同，也不会覆盖已有批次或标准行。
- 每次初始解析和重映射追加不可变 revision；确认后锁定该 revision，只有显式激活才成为项目正式活动版本。
- 支持查看 revision 列表与单个不可变快照，并记录确认、激活、替换和重新激活的生命周期事件。
- 持久化原始单元格快照、来源 Sheet/行号、字段映射、解析值、工程量判定结果和批次追溯关系。
- 保存公式原文、缓存值和公式错误证据；价格、金额、分层工程量和公式列被锁定为不可映射字段。
- 对预算项目、导入批次和标准行执行角色权限与对象级项目范围校验。
- 将不可信工程量统一降为 `calculation_quantity=0`，保留原值和明确原因，供人工复核。

## 3. 明确边界

本期只建立标准化输入底座，以下能力未接入：

- 不计算价格、综合单价、合价或预算总价。
- “企业定额 `active`”已经确定为未来预算链唯一正式成本主库；当前环境必须且已经校验为恰好 1 个 `active` 版本。本 P1 对其零条目读取、零写入、零匹配，只在迁移/验收前后校验 `active` 版本数量没有变化。
- 既有 `cost_items.active` 报价/RAG 链按原业务冻结维护，不得复用为新预算链的正式成本源；本 P1 不查询或写入 `cost_items`，不触发成本 RAG 同步或无底价 draft 沉淀。
- 不接“四库”及其检索、联动、同步或写回链路。
- 不改变现有报价、成本库、企业定额、RAG、N8N/Dify 逻辑和价格口径。
- “项目测算/采购结果”保持冻结，本阶段不修改、不扩展、不接入。
- 不实现项目冻结/锁定；归档仅作为工作区状态控制。
- 不保存或提供原始 Excel 二进制下载；原件能力当前为 `metadata-only`，详见第 9 节。

后续若开展计价、定额匹配或“四库”联动，必须作为独立阶段重新设计数据契约、权限、审计、回滚和验收。新预算链只能读取经明确激活的企业定额正式版本，不得回退或旁路到旧 `cost_items` 报价/RAG 链；不得把 Phase 1 的“标准化完成”解释为计价能力已经启用。

## 4. 功能架构

```text
Vite /admin/budget-projects
        |
        v
FastAPI /api/v1/admin/budget-projects/*
        |
        +-- 复用 projects：项目身份、负责人和既有项目范围权限
        |
        +-- budget_project_profiles：预算项目扩展档案
        |      +-- active_import_batch_id / active_import_revision_id
        |
        +-- budget_project_import_batches：一次上传一个批次及其状态机
        |
        +-- budget_project_import_revisions：初始解析/重映射不可变快照
        |
        +-- budget_project_import_lifecycle_events：确认/激活/替换审计
        |
        +-- budget_project_import_sheet_mappings：逐 Sheet 字段映射
        |
        +-- budget_project_standard_rows：来源可追溯的标准行
```

核心数据流为：选择或创建项目 → 上传 Excel → 校验文件 → 识别 Sheet 角色/表头 → 锁定价格、公式和非正式数量列 → 保存字段映射 → 标准化逐行数据 → 执行工程量安全判定 → 追加不可变 revision → 页面复核或在 `parsed` 状态重新映射 → 确认 revision → 显式激活为项目正式活动版本。后续上传只产生新的 `parsed` 批次，不会自动替换活动指针。

## 5. 数据模型

| 实体 | 作用 | 关键关系与约束 |
|---|---|---|
| `projects` | 系统既有项目主实体，也是预算项目的唯一身份来源 | 不另建平行“预算项目主表”；沿用既有负责人、状态和项目范围语义 |
| `budget_project_profiles` | 预算项目扩展档案 | 与 `projects` 一一关联；显式保存 `active_import_batch_id` 和 `active_import_revision_id`，两者共同确定正式活动版本 |
| `budget_project_import_batches` | 一次 Excel 上传的追溯单元 | 保存批次 UUID、文件元数据、SHA-256、统计、`current_revision_id`、`confirmed_revision_id` 和 `parsed/confirmed/active/superseded` 状态；相同文件仍生成独立批次 |
| `budget_project_import_revisions` | 不可变导入修订 | 追加保存预览、Sheet 映射、标准行、汇总、快照 SHA-256 和创建人；初始解析、重映射和 0049 存量回填均保留独立 revision |
| `budget_project_import_lifecycle_events` | 批次状态审计 | 追加记录确认、激活、替换和重新激活的前后状态、操作者、revision 与事件数据 |
| `budget_project_import_sheet_mappings` | 每个 Sheet 的当前映射 | 保存 `sheet_role`、表头、映射、列锁定原因和映射修订号；仅 `parsed` 批次允许修改 |
| `budget_project_standard_rows` | 当前标准化清单行 | 保存 `sheet_role`、来源 Sheet/原始行号、原始单元格、标准字段、公式证据、解析工程量、可计算工程量、判定状态与原因；正式消费以已确认 revision 快照为准 |

迁移 `20260714_0049` 建立 4 张预算底座表。增量迁移 `20260715_0050` 新增不可变 revision 表、生命周期事件表、批次 revision/确认/激活字段、项目活动批次/修订双指针以及映射/标准行 `sheet_role`；迁移先严格校验 0049 存量 JSON，再为每个存量批次生成 `backfill` revision，不静默把损坏 JSON 冻结为空快照。两次迁移均不对成本库或企业定额表执行数据写入、更新、删除或状态迁移。

## 6. API 清单

以下路径均位于 `/api/v1`：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/admin/budget-projects` | 按当前用户可见范围查询预算项目 |
| `POST` | `/admin/budget-projects` | 创建项目及预算项目档案 |
| `GET` | `/admin/budget-projects/{project_id}` | 查询项目工作区详情 |
| `PATCH` | `/admin/budget-projects/{project_id}` | 更新预算项目档案 |
| `PATCH` | `/admin/budget-projects/{project_id}/archive` | 归档项目工作区；本阶段不提供恢复接口 |
| `GET` | `/admin/budget-projects/{project_id}/imports` | 查询项目的导入批次 |
| `POST` | `/admin/budget-projects/{project_id}/imports` | 上传 Excel 并创建独立导入批次 |
| `GET` | `/admin/budget-projects/{project_id}/active-import` | 查询项目当前活动批次 |
| `GET` | `/admin/budget-projects/imports/{batch_identifier}` | 查询批次详情和 Sheet 映射 |
| `GET` | `/admin/budget-projects/imports/{batch_identifier}/revisions` | 分页查询不可变 revision |
| `GET` | `/admin/budget-projects/imports/{batch_identifier}/revisions/{revision_identifier}` | 查询单个 revision 及完整快照 |
| `GET` | `/admin/budget-projects/imports/{batch_identifier}/rows` | 分页查询标准行 |
| `POST` | `/admin/budget-projects/imports/{batch_identifier}/remap` | 提交 Sheet 映射修正并重新标准化 |
| `POST` | `/admin/budget-projects/imports/{batch_identifier}/confirm` | 确认并锁定当前 revision |
| `POST` | `/admin/budget-projects/imports/{batch_identifier}/activate` | 显式激活已确认批次；原活动批次转为 `superseded` |

归档项目仍可读取历史批次、revision 和标准行，但更新、上传、重映射、确认和激活会被阻断。只有 `parsed` 批次可以重映射；请求携带 `expected_remap_revision`，过期页面或并发修改返回 `409 BUDGET_IMPORT_REVISION_CONFLICT`。已确认/已激活批次的重映射返回 `409 BUDGET_IMPORT_REMAP_FROZEN`。批次详情、revision、标准行和变更接口均执行对象级项目范围校验，避免通过批次 UUID 越权访问其他项目。

## 7. RBAC 与对象级权限

| 用户类别 | 可见范围 | 创建/修改/导入 | 说明 |
|---|---|---|---|
| `system_admin` / `admin` / `manager` / `project_manager` | 按既有管理语义查看全部或管理范围 | 允许 | 沿用现有项目管理语义，不新增成本权限依赖 |
| `viewer` | 全局只读 | 禁止 | 可查看项目、批次和标准行，不可产生变更 |
| `staff` | 按既有项目归属范围 | 按既有项目权限 | 不因本功能获得跨项目访问能力 |
| `project_viewer` / `project_member` | 仅授权项目 | 禁止创建和管理变更 | 即使能读取项目，也不能上传、重映射或归档 |
| `quote_user` / `quote_operator` | 仅本人创建/归属的项目 | 可创建并管理本人范围 | 不能查看或修改他人项目 |

`cost_viewer`、`cost_editor`、`cost_approver`、`cost_exporter` 等成本专项角色不会因为成本权限自动获得预算项目权限；预算项目权限与成本敏感权限保持解耦。用户角色降级后，详情、上传、重映射等接口会按当前权限即时返回 `403`，不依赖前端按钮隐藏。

项目对象返回 `can_edit`、`can_archive`、`can_upload`、`can_remap`、`can_activate_import`，批次对象返回 `can_remap`、`can_confirm`、`can_activate`。前端默认按“缺失能力即不允许”处理；归档/只读对象的所有变更控件统一禁用。能力字段只用于界面收敛，后端权限、工作区状态和批次状态机仍是最终边界。

## 8. Excel 导入与工程量安全规则

### 8.1 文件与批次

- 支持 `.xlsx` / `.xlsm`；不接受旧 `.xls`。
- 单文件上限 30 MB，单 Sheet 最多 800 行、80 列。
- 损坏、空内容、超限或不支持的文件在创建有效标准行前被拒绝。
- 每次上传使用新的批次 UUID；文件名和 SHA-256 相同也保留为两次独立业务操作。
- 表头和数据行保留来源位置；重复表头按行类型识别，不能伪装成有效清单行或工程量。
- Sheet 必须先定角色：`bill` 才计入正式清单；`material_reference` 只保留主材参考；封面/说明和汇总分析不计入正式清单。
- 多楼层清单优先识别语义明确且可校验为各分层之和的“工程量小计/总量”列；分层工程量只作证据，不允许自动抢占正式工程量映射。
- 价格、合价、金额分解、供应商价和公式辅助列标记为锁定忽略，API 和前端均不允许把它们改映射为工程量。
- 若源表已有独立项目特征/规格列，项目名称保持原单元格全文，不再对名称中的数字、尺寸、型号或连接符做二次拆分。

### 8.2 工程量字段

- `raw_quantity`：原始单元格表达，不因解析失败而丢失。
- `parser_quantity`：解析器提取的候选数值；无法唯一、可靠提取时允许为空。
- `calculation_quantity`：唯一允许后续计算链路消费的安全数值。
- `quantity_status` / 原因：记录为何可用、置零或需人工复核。

### 8.3 `calculation_quantity=0` 规则

只有严格、唯一、有效且精度可安全保存的正数工程量才能进入 `calculation_quantity`。以下情况一律置 `0`，同时保留原始值和判定原因：

- 空值、零值、负数或超出允许范围。
- 非数字文本、区间值、约数、包含多个候选数字或无法唯一判断的表达。
- 序号列、连续编号列或表头误映射产生的 `1/2/3...`，不得当作工程量。
- 小数精度超出支持范围，或量级过小导致按支持精度保存后下溢为零。
- 任何解析结果与原始表达不一致、单位/字段上下文不足或需人工确认的情况。

该规则的目的不是修改原始清单，而是保证未来计价链路只能消费已确认的安全工程量。合法的较小数量（包括正常工程量 `1`、`2`、`3`）在真实工程量列中仍可保留；规则阻断的是“序号被误当工程量”，而不是阻断数值本身。

### 8.4 联昇样本数量与公式证据

| Sheet/角色 | 正式/参考行 | 工程量列 | 状态 |
|---|---:|---|---|
| 装饰清单 / `bill` | 121 | `K` 工程量小计，且逐行等于 `F:J` 分层之和 | `valid=121 / zero=0 / missing=0` |
| 机电清单 / `bill` | 73 | `J` 总量，且逐行等于 `E:I` 分层之和 | `valid=73 / zero=0 / missing=0` |
| 措施费 / `bill` | 4 | `E` 工程量 | `valid=4 / zero=0 / missing=0` |
| 主材表 / `material_reference` | 99 | `F` 参考数量 | `valid=86 / zero=11 / missing=2` |

正式清单合计 198 行全部有安全正工程量。主材表 F78、F80 的源公式包含 `#REF!`，系统保留 `raw_formula`、`cached_value`、`formula_error` 并标记 `BROKEN_FORMULA_REF`；这两行保持 `missing`，不得用猜测值补齐。工作簿中其他中文跨 Sheet 公式和 `DISPIMG` 等表达可能不被第三方只读工具计算，但系统以源公式和工作簿缓存值双轨留证，不把工具显示错误直接改写为源文件错误。

## 9. 原件 `metadata-only` 限制

当前阶段保存文件名、扩展名、字节数、SHA-256、上传人、上传时间、Sheet 映射、原始单元格快照和解析结果，但不保存 Excel 原始二进制对象，也不提供原件下载 API。

因此当前可以证明“某个哈希文件在某时由某人形成了某批标准化结果”，但不能仅依赖系统重新下载原件、做原文件级复算或进行原件长期归档。若后续需要原件留存，应单独引入对象存储键、下载授权、病毒扫描、保留期、删除策略、审计日志和哈希复核，不能把 `metadata-only` 当成已完成的原件归档能力。

## 10. P1 收口验证

| 验收项 | 结果 |
|---|---|
| 迁移 | Alembic 已从 `20260714_0049` 升级到 `20260715_0050`；14 个存量批次均生成当前 revision，完整性校验无悬空批次/跨批次指针 |
| 迁移前备份 | `output/pre_budget_0050_20260715_p1_closeout/mysql_pre_0050.sql`，SHA-256 `a82c8d2aef69082fda0077bce6cb25cac9a81da0daffe64a79489ea0710f7192` |
| 数据隔离 | 迁移及运行态验收前后 16 张受保护的既有项目、成本、企业定额、报价证据表行数逐表一致；0050 只新增/回填预算导入治理结构 |
| 企业定额状态 | `enterprise_quota` 恰好 1 个 `active`，迁移前后不变；P1 未读取其条目用于匹配，也未写入任何版本数据 |
| 信达样本 | 127 项空工程量安全置 0、5 条 `m³` 恢复、1 条重复表头排除、同文件双批次均保留（原 Phase 1 验收事实） |
| 联昇运行态样本 | 项目 `15 / PRJ-20260715-003`；8 个 Sheet、总输出 434 行；正式清单 198=`121+73+4`，`valid=198 / zero=0 / missing=0`；主材参考 99=`86+11+2`；装饰/机电/措施费取 `K/J/E` |
| 名称与公式证据 | row 74 项目名称保持完整，全表名称不一致数为 0；主材 F78/F80 保留 `BROKEN_FORMULA_REF` 警告，不猜值补齐 |
| 正式版本 | 批次 16（UUID `5bdcd778-1d70-40e2-9206-ccf23d575ca1`），initial revision 18、remap/confirmed revision 19；confirm 200、activate 200；生命周期事件完整记录 `parsed -> confirmed -> active` |
| 冲突与活动指针 | 已确认批次重映射返回 `409 BUDGET_IMPORT_REMAP_FROZEN`；过期修订号返回 `409 BUDGET_IMPORT_REVISION_CONFLICT`；第二批次 17 上传后仍为 `parsed`，`active-import` 继续返回批次 16/revision 19，项目内 `active` 批次计数为 1 |
| 验收留痕 | 批次 15 是预修复 `parsed` 批次且从未启用；验收项目保留未归档，便于后续审计复核 |
| 后端 P1 聚焦 | `42 passed` |
| 统计不污染回归 | `19 passed`，预算项目不会进入既有项目进度/经营统计 |
| 前端契约 | `30 passed`，对象 capabilities、批次状态与控件边界纳入静态契约 |
| 前端构建 | `ai-web` 构建通过 |
| 后端全量 | `839 / 841` 通过；其余 2 项为冻结的旧 BIZ-2c 测试，不在 P1 预算项目代码路径内，按本次 P1 收口记录为非阻断遗留项 |
| 最终运行态 API 闭环 | **通过**：上传、重映射、确认、激活、显式活动指针、新批次不抢占、两类 409 冲突和对象状态均已验证 |

“受监控旧表行数不变”只能证明本次迁移执行未对这些表产生增删；结合 0050 DDL/回填范围、服务调用边界、企业定额 `active=1` 复核和 P1 测试，确认本阶段没有暗接计价、企业定额匹配、旧 `cost_items` 报价/RAG 链或“四库”写入链路。

## 11. 运维与后续交接

- 新环境上线前执行 Alembic 升级到 `20260715_0050`，再按环境显式启用 `FEATURE_BUDGET_PROJECTS`。
- 功能关闭时不得暴露半可用页面或绕过后端校验；权限判断以 API 为准。
- 批次确认/激活已形成正式导入版本治理；项目归档仍只是工作区只读状态，不等同于预算计价结果冻结或审批完成。
- 当前验收已证明：新上传只产生 `parsed` 批次且不移动活动指针；确认后禁止重映射；过期 revision 冲突返回 409；活动版本读取严格使用批次/revision 双指针。`superseded` 回切、归档只读和低权限行为继续由聚焦/契约测试覆盖，后续若调整状态机必须重新做运行态回归。
- 计价前必须定义“标准行 → 定额/成本候选 → 人工确认 → 价格版本 → 合价”的独立合同，并继续坚持不可信工程量置零阻断。
- 第二阶段若启动计价，只允许以企业定额恰好一个 `active` 版本作为唯一正式成本主库；若 `active` 不为 1 必须硬阻断。旧 `cost_items` 报价/RAG 链继续冻结隔离，不得作为预算链 fallback。
- 企业定额匹配和“四库”联动必须有只读试运行、命中证据、权限隔离及验收前后数据快照，不得直接修改现有 `active` 数据；“项目测算/采购结果”继续冻结。

Phase 1 P1 已达到进入 Phase 2 **数据契约与方案设计**的门槛；该结论不等于自动授权启动计价实现。是否进入 Phase 2、何时开始编码，以及首批企业定额只读匹配范围，仍需用户明确决定。
