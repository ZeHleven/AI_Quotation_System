# 成本预算对标阶段二：账户定额库底座（P2-2B-1）

更新日期：2026-07-16

## 1. 阶段目标

P2-2B-1 在 P2-2A“账号隔离 + 双模式计价草稿”之后，建立一套真正按账号隔离、可人工维护、可审计的账户定额目录。

本阶段只解决“账户自己认可的定额数据存在哪里、怎样维护、怎样启用、怎样追溯”四个问题：

- 同一平台内，不同账号可以维护名称和特征相同、但价格不同的账户定额；
- 新建数据先进入 `draft`，人工确认后才能变为 `active`；
- 名称、特征、规格、单位、价格等修改均有 revision 和历史快照；
- 企业定额唯一 `active` 主库、P2-1 正式计价 run 和 P2-2A 计价草稿均不被本阶段写入或改变。

P2-2B-1 不是完整的账户定额计价闭环。`active` 在本阶段只表示“已通过账号内人工启用、具备后续参与匹配的资格”，并不表示已经接入 `account_strict` 计价草稿。

阶段结论（2026-07-16）：P2-2B-1 已完成代码、Alembic 迁移、专项与全量回归、当前环境 API/数据库闭环和 Chrome 登录态人工验收，可以收口。P2-2B-2、P2-2B-3 和 P2-2C 仍未开始，也未被本次验收隐式启用。

## 2. 不可突破的产品边界

1. `enterprise_quota_versions` 中恰好一个 `active` 版本仍是平台唯一正式企业成本主库；账户定额与企业定额使用独立数据表，不允许反向写入企业定额。
2. 账户定额 API 不接受客户端指定的 `account_id`。账号只能由当前登录用户的 active/default 成员关系在服务端解析。
3. 管理员的管理范围也只限当前账号；不能因为拥有平台管理员角色而跨账号读取或修改账户定额。
4. 跨账号按 ID 或 UUID 访问详情、编辑、状态流转和历史，一律按“本账号内不存在”返回 404，避免泄露其他账号的数据存在性。
5. 本阶段不读取、不回退、不写入旧 `cost_items` 报价/RAG 链，不调用 N8N、Dify 或任何 LLM/视觉模型。
6. 本阶段不创建、修改或删除 P2-1 的 `budget_project_pricing_runs` 及其 line/candidate/event。
7. 本阶段不重建 P2-2A 计价草稿，不把账户定额价格匹配到 `account_strict` 草稿，也不生成正式 child run。
8. “项目测算/采购结果”继续冻结。

## 3. 生命周期

账户定额采用以下状态机：

```text
                  人工启用
       draft --------------------> active
         ^                            |
         |                            |
         +-------- 人工撤回 ----------+
         |                            |
         +-----------> archived <-----+
                    人工归档
```

状态语义：

| 状态 | 含义 | 可编辑 | 后续是否可参与账户定额匹配 |
|---|---|---:|---:|
| `draft` | 待人工复核的账号私有草稿 | 是 | 否 |
| `active` | 已由账号管理员明确启用 | 是，可先编辑后继续保留 active，也可撤回 | P2-2B-2 才接入；本阶段尚不参与 |
| `archived` | 永久停止使用的历史数据 | 否 | 否 |

允许的状态流转：

- `draft -> active`：审核启用；
- `active -> draft`：撤回修订；
- `draft -> archived`、`active -> archived`：归档；
- `archived` 是终态，不能恢复、编辑或再次流转；
- 相同状态重复提交和其他越界流转返回 409，不静默成功。

启用和归档都必须是显式人工动作。P2-2B-1 不存在“创建后自动 active”、AI 自动启用或同步后自动启用。

## 4. 账号隔离合同

账户定额复用 P2-2A 的账号底座：

- `accounts`：客户/公司的账号边界；
- `account_memberships`：用户与账号的 active/default 成员关系；
- `resolve_current_account(...)`：从当前登录用户解析唯一当前账号，无法唯一解析时 fail-closed。

隔离规则：

1. 所有列表和明细查询在 SQL 层首先增加 `account_id == current_account.id`；
2. API 输入模型使用 `extra="forbid"`，注入 `account_id` 返回 422；
3. API 响应不返回可由客户端复用或篡改的 `account_id`；
4. 指纹唯一约束是 `account_id + fingerprint`，同一指纹在不同账号内合法；
5. 同一项目在账号 A 可以是 100 元，在账号 B 可以是 120 元，二者独立维护、独立修订、独立启用；
6. 列表、详情、历史、编辑和状态流转均使用相同隔离口径；
7. 当前接口使用 `require_admin`，普通成员不能查看或维护账户定额库。

## 5. 字段与定额身份指纹

### 5.1 主字段

| 字段 | 约束 | 说明 |
|---|---|---|
| `item_uuid` | 全局唯一 UUID | 对外稳定标识，避免依赖自增 ID |
| `account_id` | 必填、外键 | 仅服务端写入，不从 API 接收 |
| `quota_code` | 可空，最长 64 | 账号自定义编码，不作为定额身份 |
| `item_name` | 必填，最长 255 | 项目/定额名称 |
| `item_features` | 可空长文本 | 工艺、做法、材质等项目特征 |
| `spec` | 可空长文本 | 规格型号 |
| `unit` | 必填，最长 64 | 计量单位 |
| `unit_price` | `NUMERIC(18,6)`、大于 0 | 账号认可的单价 |
| `fingerprint` | SHA-256，64 字符 | 账号内重复冲突判断键 |
| `source` | 受信来源枚举 | 数据如何进入账户定额库 |
| `status` | `draft/active/archived` | 生命周期状态 |
| `notes` | 可空长文本 | 管理备注 |
| `revision` | 从 1 开始递增 | 乐观锁与审计版本 |
| `created_by/updated_by` | 用户外键 | 创建人和最近修改人 |
| `created_at/updated_at` | 数据库时间 | 创建与最近更新时间 |

### 5.2 指纹规则

指纹只由以下字段决定：

```text
item_name + item_features + spec + unit
```

每个组成字段先执行 Unicode NFKC、大小写折叠，并去除空白和标点，仅保留字母与数字；随后按固定 JSON 键顺序序列化并计算 SHA-256。

指纹明确不包含：

- `quota_code`：改编码不应制造一条新定额；
- `unit_price`：改价应形成同一条定额的新 revision；
- `notes`：备注不定义定额身份；
- `status` 和 `source`：生命周期与来源不定义项目本身。

数据库通过 `UNIQUE(account_id, fingerprint)` 阻止同一账号内的语义重复条目；重复创建或把现有条目编辑成另一条相同指纹时返回 409，并携带已存在条目的标识供前端提示。不同账号可以拥有相同 fingerprint。

该指纹用于“账号内重复治理”，不是 P2-2B-2 的完整计价匹配算法。后续匹配仍需单独定义名称、特征、规格、单位兼容和唯一候选规则。

## 6. Decimal 金额口径

- 数据库存储使用 `NUMERIC(18,6)`；
- 服务端计算使用 `Decimal`，量化单位为 `0.000001`，舍入方式为 `ROUND_HALF_UP`；
- 新建和编辑只接受有限正数，`0`、负数、NaN、Infinity 和超出 `999999999999.999999` 的数值均被拒绝；
- API 将单价序列化为六位小数文本，避免 JSON 浮点误差；
- 前端输入最多六位小数，并在提交前执行正数校验。

账户定额“没有价格”的语义不是 0。需要沉淀但尚未获得可信价格的项目应继续留在计价草稿中，不能用 0 元伪造一条账户定额。

## 7. 可信来源规则

数据模型预留四种来源：

| `source` | 含义 | P2-2B-1 是否可由当前 CRUD 写入 |
|---|---|---:|
| `manual` | 账号管理员人工新建 | 是 |
| `imported` | 后续受控导入 | 否，仅预留 |
| `pricing_draft_sync` | 后续从计价草稿确认同步 | 否，P2-2B-3 才实现 |
| `ai_estimate` | 后续带模型证据的 AI 估价沉淀 | 否，P2-2C 之后才可能使用 |

P2-2B-1 的公开新建模型将 `source` 固定为 `manual`，更新模型不开放 `source` 字段。因此客户端不能把人工录入伪装成“导入”“计价草稿同步”或“AI 估价”。后续新增其他来源时，必须通过对应的受控服务入口写入，并同时保存来源对象和证据；不能仅靠前端传一个枚举值。

## 8. Revision、并发保护与历史

### 8.1 Revision

- 新建条目为 `revision=1`；
- 每次字段修改或状态流转成功后 revision 加 1；
- PATCH 和状态流转必须提交 `expected_revision`；
- 服务端加行锁并校验 revision，过期页面提交返回 409 `ACCOUNT_QUOTA_REVISION_CONFLICT`；
- 冲突时不覆盖数据库当前值，前端提示用户刷新最新数据后再决定是否重做修改。

### 8.2 历史记录

每次成功变更同步写入 `account_quota_item_history`：

- `created`：记录 revision 1 的完整 after snapshot；
- `updated`：记录修改前、修改后的完整快照；
- `status_changed`：记录 from/to status 及前后完整快照；
- 所有事件保存 `reason`、`actor_id` 和数据库时间；
- 历史同样保存 `account_id` 并按当前账号过滤；
- `UNIQUE(account_quota_item_id, revision)` 保证每条定额每个 revision 只有一个审计事件。

历史是只读证据，不提供“直接覆盖恢复旧版本”的接口。需要恢复旧内容时，应在当前非 archived 条目上重新编辑，从而产生新的 revision；归档条目不得恢复。

## 9. 数据表

Alembic `20260716_0053` 计划新增两张表：

### `account_quota_items`

保存每个账号当前可维护的账户定额状态，核心约束为：

- `item_uuid` 全局唯一；
- `account_id + fingerprint` 账号内唯一；
- `account_id + status`、`account_id + updated_at`、`account_id + item_name + unit` 建组合索引；
- 账号、创建人、修改人均使用 `RESTRICT` 外键，避免删除上游对象导致审计链断裂。

### `account_quota_item_history`

保存每个 revision 的不可变历史快照，核心约束为：

- `account_quota_item_id + revision` 唯一；
- 明确保存 `account_id`，便于在历史查询层继续执行账号隔离；
- before/after snapshot 使用长文本 JSON，避免未来字段扩展破坏旧审计证据；
- 条目、账号和操作人均使用 `RESTRICT` 外键。

新增表必须通过 Alembic 升级，不能依赖 `AUTO_CREATE_TABLES` 或启动时补表。

## 10. API 合同

所有接口均位于 `/api/v1` 下、受功能开关 `FEATURE_ACCOUNT_QUOTAS` 和管理员权限保护：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/admin/account-quotas` | 当前账号分页列表；支持 `status/source/keyword/page/page_size` |
| `POST` | `/admin/account-quotas` | 人工创建当前账号 `draft` 定额 |
| `GET` | `/admin/account-quotas/{id-or-uuid}` | 当前账号定额详情 |
| `PATCH` | `/admin/account-quotas/{id-or-uuid}` | 按 `expected_revision` 编辑 |
| `POST` | `/admin/account-quotas/{id-or-uuid}/status` | 按 `expected_revision` 执行状态流转 |
| `GET` | `/admin/account-quotas/{id-or-uuid}/history` | 当前账号修订历史 |

新建示例：

```json
{
  "quota_code": "USR-001",
  "item_name": "石材地面铺装",
  "item_features": "20mm 厚花岗岩，水泥砂浆结合层",
  "spec": "600×600×20mm",
  "unit": "㎡",
  "unit_price": "123.000001",
  "source": "manual",
  "notes": "账户人工核定",
  "reason": "首次录入"
}
```

编辑示例：

```json
{
  "expected_revision": 1,
  "unit_price": "128.500000",
  "reason": "根据最新采购复核"
}
```

启用示例：

```json
{
  "target_status": "active",
  "expected_revision": 2,
  "reason": "审核通过，允许后续参与账号定额匹配"
}
```

主要失败语义：

- 功能开关关闭：403 `FEATURE_DISABLED`；
- 非管理员：403；
- 注入额外字段或金额/状态不合法：422；
- 当前账号内不存在或跨账号访问：404 `ACCOUNT_QUOTA_NOT_FOUND`；
- revision 过期：409 `ACCOUNT_QUOTA_REVISION_CONFLICT`；
- 账号内指纹重复：409 `ACCOUNT_QUOTA_DUPLICATE_FINGERPRINT`；
- 状态流转不合法或条目已归档：409。

## 11. 前端范围

Vite 管理台新增 `/admin/account-quotas`“账户定额库”页面，并通过 RBAC 可用模块与 `FEATURE_ACCOUNT_QUOTAS` 双重门禁控制入口。页面范围包括：

- 按关键词和状态筛选、分页查看当前账号定额；
- 新建账户定额草稿；
- 编辑编码、名称、特征、规格、单位、单价和备注；
- 人工启用、撤回为草稿、归档；
- 查看 revision、来源、状态和修订历史；
- 409 冲突时阻止覆盖并提示刷新；
- 固定展示边界说明：账户定额不会读取或修改企业定额主库，P2-2B-1 的 active 尚未接入计价草稿。

页面不提供批量导入、从计价草稿同步、AI 估价或账户定额匹配结果展示。

## 12. 明确非目标

### P2-2B-2：账户定额严格匹配（未开始）

后续才把当前账号的 `active` 账户定额接入 P2-2A `account_strict` 草稿。目标语义是：唯一可信匹配自动带价，多候选明确展示冲突，未匹配保持 `NULL`，并且不读取企业定额、不自动调用 LLM。

### P2-2B-3：同步到账户定额（未开始）

后续才实现从人工微调后的计价草稿发起同步预览，逐项区分新增、更新、冲突和跳过，经人工确认后写入账户定额 `draft`。同步不能自动 active，也不能修改企业定额或正式 run。

### P2-2C：LLM 估价（未开始）

后续才实现：

- 基础定额模式对未匹配行自动估价；
- 账户定额模式只允许用户主动点击“AI 估价”；
- 记录模型、prompt、输入证据、置信度、失败信息和价格来源；
- AI 价格先进入可变计价草稿，不能直接成为企业定额或账户定额 active 数据。

此外，本阶段不实现 Excel 批量导入、定额导出、批量启用、回滚旧 revision、账户定额 RAG 同步或账户定额向企业定额升级。

## 13. 验收清单

### 13.1 代码与契约验收

- [x] `FEATURE_ACCOUNT_QUOTAS=false` 时后端和前端入口均 fail-closed；
- [x] 普通成员不能访问管理员账户定额接口；
- [x] API 注入 `account_id` 返回 422；
- [x] 两个账号可以创建相同 fingerprint、不同价格的条目；
- [x] 同一账号内重复 fingerprint 返回 409；
- [x] 跨账号详情、编辑、状态和历史均返回 404；
- [x] 单价以六位 Decimal 保存和返回，0/负数/非有限数被拒绝；
- [x] 过期 `expected_revision` 返回 409 且不覆盖新数据；
- [x] `draft -> active -> draft -> archived` 全链路写入连续历史；
- [x] archived 条目不可编辑、不可恢复；
- [x] 当前人工 CRUD 不能伪造 `imported/pricing_draft_sync/ai_estimate` 来源；
- [x] 企业定额版本/条目和正式 pricing run 计数前后不变；
- [x] 账户定额模块不调用 LLM、N8N、Dify 或旧成本库；
- [x] `account_strict` 计价草稿仍未接入账户定额，P2-2A 现有语义不被偷偷改变。

### 13.2 迁移、回归与运行态验收

已获得的真实当前环境结果：

- 0053 前数据库备份文件：`output/pre_budget_0053_20260716_p2_2b1/ai_quotation_before_0053.sql`；
- 备份大小：`172461281` bytes；SHA256：`BFC58D4DB32AC39415B14181D513D90124A7A02237DF2296A76C0E4E7353BD46`；
- Alembic 升级成功，当前 head 为 `20260716_0053 (head)`；
- 新表刚迁移完成时为空库：`account_quota_items=0 / account_quota_item_history=0`；完成 Chrome 验收后为 `1/5`；
- 迁移与验证前后受保护计数完全一致：企业定额 `versions=3 / active versions=1 / items=1422`，P2-1 正式计价 `run=4 / line=792 / candidate=220 / event=4`，P2-2A 计价草稿 `draft=1 / line=198 / event=8`；
- 后端 P2-2B-1 联合专项：`29 passed`；
- 后端相关文件 compileall：通过；
- 后端全量回归：`887 passed / 2 failed / 39 warnings`；两项失败均为冻结旧 BIZ-2c 口径（单位 `m2` vs `㎡`、RAG 同步状态 `synced` vs `stale`），无 P2-2B-1 新增失败；
- 前端契约测试：`5 passed`；
- Vite production build：通过，`1627 modules`，仅既有大 chunk 警告；
- 当前服务健康检查：`/health/ready=ready`，database ok，Celery broker/worker ok，`worker_count=2`；
- Chrome 当前环境完整闭环：R1 新建 `draft`，单价 `123.456789`、原因为“P2-2B1当前环境Chrome闭环验收”；R2 编辑为 `130.000001`；R3 启用为 `active`；R4 撤回为 `draft`；R5 归档为 `archived`；
- 归档后页面仅保留“历史”操作，5 条修订历史均展示 `admin`、变更原因和精确单价；
- 最终数据库验收条目：`account_quota_items=1 / account_quota_item_history=5 / source=manual / status=archived / revision=5`；
- 最终受保护计数仍与迁移前一致：企业定额 `versions=3 / active versions=1 / items=1422`，正式计价 `run=4 / line=792 / candidate=220 / event=4`，计价草稿 `draft=1 / line=198 / event=8`；
- 当前环境功能已开启；需要回退时将 `FEATURE_ACCOUNT_QUOTAS=false` 并重启服务即可隐藏入口并使 API fail-closed，已写入的数据不删除。

## 14. 进入下一阶段的门槛

上述迁移、账号隔离、revision/history、非污染、前端页面和真实运行态验收均已完成，P2-2B-1 可以标记完成。

下一步可以在用户明确授权后进入 P2-2B-2。进入 P2-2B-2 不自动授权 P2-2B-3 或 P2-2C；每个阶段继续独立实现、独立迁移（如需要）、独立验收，避免把账户定额目录、匹配、同步和 AI 估价一次性耦合上线。
