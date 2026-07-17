# 成本预算对标阶段二：账号隔离与双模式计价草稿（P2-2A）

更新日期：2026-07-16

## 1. 阶段结论

P2-2A 已完成代码、Alembic 迁移、存量数据回填、真实联昇项目运行态验收和 Chrome 人工验收。

本阶段在 P2-1 的不可变企业定额计价 run 之外，新增一条账号隔离的“可变计价草稿”链路。用户可以选择“基础定额”或“账户定额”模式，在正式确认前反复切换模式和人工调整价格，而不会为每次编辑创建新的正式计价版本。

核心边界保持不变：

- `enterprise_quota_versions` 中严格唯一的 `active` 版本仍是平台唯一正式企业成本主库；
- 企业定额主库不会被账号、普通用户、计价草稿或后续 LLM 改写；
- P2-1 的不可变 run/line/candidate/event 不允许原地修改；
- P2-2A 不生成正式 run，不实现“确认成本”或“同步到账户定额”；
- “项目测算/采购结果”继续冻结；
- 新预算链不读取、不回退、不写入旧报价成本库、RAG、N8N 或 Dify 链路；
- P2-2B 账户定额库和 P2-2C AI 估价均未启动。

## 2. 草稿与正式版本的职责分离

```text
正式工程量清单（active batch + confirmed revision）
                         |
                         v
              账号内唯一当前计价草稿
              /                     \
      enterprise_ai             account_strict
       基础定额模式               账户定额模式
              \                     /
               人工编辑、自动保存、模式切换
                         |
                  仅递增 draft revision
                         |
            P2-2A 不生成正式 pricing run
```

草稿是用户确认前的工作区，可以原地修改；正式 run 是不可变证据。两者使用独立表和独立 API，避免把每次输入、失焦保存或模式切换都膨胀成新的历史版本。

每个 `account_id + project_id` 恰好只有一个当前草稿。模式切换会保留同一个 `draft.id`，清理并重建草稿行，同时把 `draft.revision` 加 1。草稿行人工编辑还使用独立的 `line_revision`，服务端同时校验草稿版本和行版本，过期请求返回 409。

## 3. 账号隔离模型

Alembic `20260716_0052` 新增三张账号基础表：

- `accounts`：客户/公司账号；
- `account_memberships`：用户与账号的 active/default 成员关系；
- `account_budget_projects`：预算项目与账号的唯一绑定。

隔离规则：

1. API 不接受客户端传入任意 `account_id`；Pydantic `extra="forbid"` 会对注入字段返回 422；
2. 当前账号只能由登录用户的 active 默认成员关系在服务端解析；
3. 无成员关系、多个默认成员关系或无法唯一解析时一律 fail-closed；
4. 预算项目查询先按账号过滤，再应用既有角色、创建人、项目经理和任务成员权限；
5. 管理员的“查看全部”只表示查看当前账号内全部项目，不能跨账号；
6. 跨账号访问 current、lines 或 PATCH 接口统一返回 404，避免泄露其他账号项目是否存在；
7. system_admin 创建新用户时，用户、角色、角色事件和账号成员关系在同一事务提交；账号分配失败时整体回滚，不残留半创建用户。

为兼容既有项目进度与大量旧测试，本阶段没有给全局 `projects` 强加新的非空账号列，而是用 `account_budget_projects.project_id` 唯一约束实现预算项目强绑定。生产开关开启时，缺少绑定的预算项目同样不可访问。

## 4. 双模式语义

| 界面模式 | 后端值 | 当前价格来源 | 未匹配处理 | P2-2A 状态 |
|---|---|---|---|---|
| 基础定额 | `enterprise_ai` | 严格唯一 active 企业定额 | 保持 `NULL`；后续才接自动 LLM 估价 | 企业匹配已实现，LLM 未接入 |
| 账户定额 | `account_strict` | 后续账号专属定额库 | 保持 `NULL`；不自动调用 LLM | 模式和草稿合同已实现，账户定额库未建立 |

### 4.1 基础定额模式

- 只读取严格唯一 active 企业定额；
- 复用 P2-1 的确定性名称、规格、编码和单位匹配规则；
- 唯一匹配且有价格时写入草稿基础单价；
- 未匹配、歧义、单位冲突或缺价时，单价和金额保持 `NULL`；
- P2-2A 不调用 LLM，响应摘要明确返回 `llm_auto_estimation_connected=false`；
- 人工调整只写当前账号的项目草稿，不反写企业定额。

### 4.2 账户定额模式

- P2-2A 不查询企业定额，也不以企业定额兜底；
- 因账户定额库尚未启动，重建后的所有 198 行价格保持 `NULL`；
- 系统不自动调用 LLM；未来只提供用户主动触发的 AI 估价；
- 用户仍可在草稿中手工填写或清空单价；
- 响应摘要明确返回 `account_quota_connected=false`。

### 4.3 人工价与工程量

- 人工单价使用 `NUMERIC(20,6)` 和 `Decimal` 计算；
- 正数人工价覆盖当前草稿基础价，`price_source=manual`；
- `manual_unit_price=null` 表示清空人工覆盖；基础定额模式恢复企业基础价，账户定额模式恢复为空值；
- 工程量有效时按六位定点数计算行合计；
- 工程量为空、0、非数字或异常时仍保留有效单位价，行合计安全为 0、`amount_included=false`，整单保持 partial；
- 未计价是 `NULL`，不是 0 元完整成本。

## 5. 数据表

`20260716_0052` 新增草稿表：

- `budget_project_pricing_drafts`：账号、项目、模式、正式清单快照、可选企业定额版本、revision 和汇总；
- `budget_project_pricing_draft_lines`：逐行来源快照、匹配证据、基础价、人工价、有效价、金额和 `line_revision`；
- `budget_project_pricing_draft_events`：创建、重建、模式切换、人工改价和清空事件。

关键约束：

- `account_id + project_id` 唯一，防止同一账号项目出现多个当前草稿；
- 草稿固定绑定正式批次、正式 revision 和源快照哈希；
- 基础定额模式额外固定企业定额版本和目录哈希；
- 账户定额模式的企业定额版本字段必须为空；
- 草稿编辑不写 `budget_project_pricing_runs` 及其三个子表。

## 6. API 合同

- `GET /api/v1/admin/budget-projects/{project_id}/pricing-draft/current`
- `POST /api/v1/admin/budget-projects/{project_id}/pricing-draft`
- `GET /api/v1/admin/budget-projects/{project_id}/pricing-draft/lines`
- `PATCH /api/v1/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}`

创建或切换模式示例：

```json
{
  "pricing_mode": "account_strict",
  "source_import_batch_id": 16,
  "source_import_revision_id": 19,
  "expected_revision": 2,
  "reason": "切换到账户定额模式"
}
```

人工改价示例：

```json
{
  "expected_revision": 1,
  "expected_line_revision": 1,
  "manual_unit_price": "12.345678",
  "reason": "人工复核"
}
```

清空人工价时显式传 `"manual_unit_price": null`。API 从不接收 `account_id`。

## 7. 迁移、备份与回填

0052 迁移前已生成 MySQL 完整备份：

- 文件：`output/pre_budget_0052_20260716_p2_2a/ai_quotation_before_0052.sql`；
- 大小：`168194886` bytes；
- SHA256：`3D8E33324EADD446141EDBE2797E2617E56A4BA7908032DE3CCA5CE701660D84`。

迁移完成后：

- `accounts=1`，默认内部账号 ID 为 1；
- `account_memberships=56`，存量用户均建立 active/default 成员关系；
- `account_budget_projects=12`，存量预算项目全部绑定默认账号；
- 新建用户会在同一事务中加入操作者当前默认账号；
- Alembic metadata 已显式注册账号和计价草稿模型；
- 当前环境迁移 head 为 `20260716_0052`，`FEATURE_BUDGET_PRICING_DRAFTS=true`。

## 8. 联昇项目运行态验收

验收对象保持为联昇预算项目：

- project：`15`；
- 正式导入批次：`16`；
- 正式 revision：`19`；
- 草稿 ID：`1`；
- 草稿 UUID：`ecb800c4-35cd-47f7-823f-aa077301b52f`。

运行态版本过程：

1. `enterprise_ai` 创建 Rev 1，同一个草稿生成 198 条正式清单行；未形成唯一有效价格的行保持空值；
2. 人工改价生成 Rev 2，人工单价写入 `12.345678`，Decimal 联动行合计为 `790.37`；
3. 切换 `account_strict` 生成 Rev 3，仍是同一个 `draft.id=1`，198 行价格全部为 `NULL`，证明没有回退企业定额或自动调用 LLM；
4. 模式切换、人工改价和清空均只递增草稿 revision，没有创建新的正式 run。

P2-1 不可变历史计数在整个验收前后保持：

- pricing run：`3`；
- run line：`594`；
- match candidate：`165`；
- pricing event：`3`。

因此，P2-2A 没有修改既有三个正式 run，也没有把草稿变化误记为 child run。

## 9. 验证结果

- 后端全量回归：`876 passed / 2 个冻结旧 BIZ-2c 口径失败`，无 P2-2A 新增失败；
- P1、RBAC、P2-2A 聚焦回归通过；测试环境通过 autouse fixture 隔离真实 `.env`，生产账号门禁未放松；
- 后端目标文件 compileall 通过；
- `ai-web` production build 通过；
- Chrome 真实登录态完成双模式、198 行草稿、人工改单价、模式切换和空值语义验收；
- 跨账号 current、lines、PATCH 均实测 404；注入 `account_id` 实测 422；
- 账户模式运行 SQL 审计未查询企业定额版本或条目；
- 正式 run 和企业定额主库计数保持不变。

## 10. 后续边界

### P2-2B：账户定额库（未启动）

后续单独设计账号隔离的账户定额主表、版本/状态、编辑权限、匹配规则，以及“同步到账户定额”的人工确认流程。账户定额不得写回平台企业定额，也不得跨账号读取。

### P2-2C：AI 估价（未启动）

后续单独接入模型调用与审计：

- 基础定额模式可对未匹配行自动触发 LLM 估价；
- 账户定额模式只在用户主动点击“AI 估价”时调用；
- AI 结果必须带来源、模型、prompt、输入证据和置信信息；
- AI 价只进入可变草稿，不能直接成为企业定额或账户定额 active 数据；
- 模型失败或未返回价格时继续保持 `NULL`，不能伪装为 0。

在 P2-2B/P2-2C 完成前，当前双模式页面只代表“计价草稿能力已建立”，不代表账户定额匹配或 AI 估价已经上线。
