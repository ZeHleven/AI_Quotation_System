# BIZ-2m 无底价项目规则开发落地计划

> 状态：详细开发计划已执行，代码层验证与模拟演示已完成（2026-05-27）
> 上游依据：`docs/biz-2-no-cost-reference-rule-draft.md`
> 执行记录：见 `docs/biz-2m-demo-and-acceptance.md`。

## 1. 要做什么

BIZ-2m 要把“无底价项目处理规则草案”落成可验证的系统闭环：

1. 报价预审行如果没有命中 `cost_items.active` 成本库参考价，允许保留 AI 估价或人工改价。
2. 预审单必须明确提示：

```text
无成本库参考价，AI 估价，仅供参考，请人工确认价格依据
```

3. 人工确认报价并成功下发后，这些无底价条目自动进入成本库待审核区。
4. 自动进入成本库时只能写成 `draft`，来源为 `ai_suggested`，不能自动成为 `active`。
5. `draft` 不参与后续报价成本参考、不参与成本库底价兜底、不进入 active RAG 同步。
6. 成本部业务员、管理员或老板人工复核并启用为 `active` 后，后续同类条目才允许出现在预审单成本库参考价中。

首版落地以“确认下发成功后的 draft 沉淀”为核心，不做新的自动定价策略。

## 2. 为什么做

当前 BIZ-2 已经完成成本库主库化、报价预审、证据链、确认清单对账和大清单完整性保障，但无底价项目仍存在一个断点：

- 业务员可以在预审单看到无底价风险，也可以人工填价后下发。
- 但这些人工确认过的无底价项目还没有自动进入成本库待审核流程。
- 下一次遇到同类需求时，如果成本部没有手工补库，系统仍然会重复出现无底价。

BIZ-2m 的目的不是让 AI 估价变成正式成本价，而是把“已经被人工确认并实际下发过的无底价项目”沉淀成待审核候选，让成本部有清单可复核、可启用、可归档。

这样做的收益：

- 把无底价问题从一次性人工判断变成可追踪的成本库治理流程。
- 让成本部逐步把高频无底价项目转成 `active`，提升后续报价命中率。
- 避免 AI 估价未经审核就进入正式成本库，保护价格口径。
- 保持现有 `cost_items.active` 作为唯一正式报价成本源。

## 3. 怎么做

### 3.1 总体链路

```text
报价任务生成预审单
  -> 无底价行展示风险提示
  -> 业务员人工确认或修改单价/合计
  -> /confirm_push 校验通过
  -> 钉钉/N8N 下发成功
  -> 记录 quote_history / quote_feedback / cost evidence
  -> 扫描本次最终预审行
  -> 提取无底价且已确认价格的行
  -> 写入 cost_items.draft(source=ai_suggested)
  -> 写入 cost_item_history 状态历史
  -> 返回本次生成/跳过/重复的摘要
```

关键原则：只有下发成功后才生成 `draft`。如果钉钉/N8N 推送失败，不生成成本库候选，避免“未成交、未下发”的估价进入待审核池。

### 3.2 建议新增后端服务

新增独立服务模块：

```text
AI_Middle_Office/app/services/no_cost_draft_capture.py
```

职责：

- 从 `final_payload.project_details` 提取无底价候选行。
- 判断候选是否有效、是否重复、是否应跳过。
- 创建 `CostItem(status=draft, source=ai_suggested)`。
- 写入 `CostItemHistory(change_type=status_change, old_status=None, new_status=draft)`。
- 输出结构化摘要，供 API 返回、日志记录和测试断言使用。

建议核心函数：

```python
analyze_no_cost_draft_candidates(final_payload: dict) -> NoCostDraftAnalysis

create_no_cost_draft_items(
    db: Session,
    user: User,
    final_payload: dict,
    quote_job_id: str | None,
    quote_history_id: int | None,
) -> NoCostDraftCaptureResult
```

分析函数必须可纯内存测试；写库函数只负责数据库落地和去重。

### 3.3 接入点

接入 `AI_Middle_Office/app/api/v1/quote.py` 的 `/confirm_push` 成功分支。

现有成功路径为：

```text
N8N push 200
  -> create_quote_history_record
  -> record_confirmed_quote
  -> return api_ok
```

BIZ-2m 后建议改为：

```text
N8N push 200
  -> create_quote_history_record
  -> record_confirmed_quote
  -> create_no_cost_draft_items
  -> return api_ok(data={"no_cost_draft_summary": ...})
```

如果 draft 生成失败：

- 不能把已经成功下发的报价改成失败，因为外部推送已经发生。
- 后端应记录异常日志，并在成功响应中返回 `warning` 摘要。
- 验收时必须把这种情况当成待修复问题，不能静默通过。

### 3.4 候选行判断规则

一行进入无底价 draft 候选，必须同时满足：

| 条件 | 说明 |
|------|------|
| 没有命中 active 成本参考 | `cost_reference.matched` 不是 `true` |
| 有有效项目名称 | `project_name` / `item_name` / `name` 至少一个非空 |
| 有有效单位 | `unit` 非空 |
| 有正数单价 | `unit_price` / `price` 能解析为大于 0 |
| 有正数合计 | `total_price` / `amount` / `subtotal` 能解析为大于 0 |
| 已经通过 `/confirm_push` | 只在推送成功后执行 |
| 不是仍未补价的占位行 | 未补单价/合计的 `requirement_placeholder` 已被现有 409 阻断 |

对于 BIZ-2l-6 生成的“AI 未返回，需人工补价”占位行：

- 未补价：继续由现有 `/confirm_push` 409 阻断，不生成 draft。
- 已人工补单价和合计：视为人工确认的无底价项目，可以进入 draft 候选。

这能覆盖“AI 没有返回，但业务员人工补价并下发”的真实场景。

### 3.5 字段映射

首版不新增数据库字段，复用现有 `cost_items`。

| `cost_items` 字段 | BIZ-2m 建议值 |
|-------------------|---------------|
| `status` | `draft` |
| `source` | `ai_suggested` |
| `category` | `待审核报价沉淀` |
| `subcategory` | `无底价项目` |
| `item_name` | 预审行项目名称 |
| `spec` | 规格/项目特征/备注合并后的简要文本 |
| `unit` | 预审行单位 |
| `price` | 人工确认后的单价 |
| `price_type` | `combined` |
| `notes` | 记录来源任务、历史 ID、确认行 key、Sheet、原始行号、AI 价、最终价、合计、提示文案 |
| `created_by` | 确认下发用户 ID |

`notes` 建议使用稳定文本格式，便于人工查看和后续脚本解析：

```text
[BIZ-2m 无底价报价沉淀]
quote_job_id: ...
quote_history_id: ...
requirement_row_key: ...
source_sheet: ...
raw_row_index: ...
ai_unit_price: ...
confirmed_unit_price: ...
confirmed_total_price: ...
quote_source: ...
notice: 无成本库参考价，AI 估价，仅供参考，请人工确认价格依据
```

如果后续需要按来源任务、来源行号、确认备注做结构化查询，再单独新增 Alembic，不在首版计划内偷加字段。

### 3.6 去重规则

首版去重键：

```text
item_name + spec + unit
```

去重处理：

| 情况 | 动作 |
|------|------|
| 已有相同 `active` | 不新建 draft，记录 `skipped_active_duplicate` |
| 已有相同 `draft` | 不重复新建，记录 `skipped_existing_draft`，可追加来源摘要到日志 |
| 已有相同 `archived` | 可以新建 draft，但在 `notes` 标记“存在已归档相似条目” |
| 无相同条目 | 新建 draft |

为什么不覆盖已有 active：如果相同 active 已存在但本次报价没命中，说明可能是匹配规则、名称规格或单位表达问题，不能用本次 AI/人工价直接覆盖正式成本条目。

### 3.7 权限与角色

本阶段区分两个动作：

1. 系统在确认下发成功后创建 `draft`。
2. 人工在成本库中复核并启用 `active`。

创建 `draft` 是系统侧沉淀动作，不应要求当前报价确认用户具备成本库管理员权限。否则普通业务员确认报价后，系统无法把无底价条目送入待审核池。

启用 `active` 仍沿用当前成本库管理权限，不在 BIZ-2m 首版里扩展新 RBAC 角色。成本部业务员、管理员、老板的细分权限已在 `docs/biz-2-cost-price-permissions-draft.md` 中规划，后续可作为独立权限阶段落地。

### 3.8 功能开关

建议新增配置：

```text
FEATURE_NO_COST_DRAFT_CAPTURE=false
```

原因：

- 便于在内网环境按阶段打开验证。
- 避免未经验收时影响真实确认推送流程。
- 允许先完成代码和测试，再由 `.env` 明确启用。

打开条件：

- Alembic 已在 `20260526_0023` 或更高版本。
- `FEATURE_COST_DB=true`。
- 已完成 BIZ-2m 后端测试和模拟演示。

### 3.9 前端显示

首版只做必要展示，不迁移旧 HTML，不新增页面。

旧 `index.html` 预审弹窗：

- 对 `cost_reference.matched=false` 的行展示固定提示文案。
- 对无底价行保留单价/合计人工编辑能力。
- 对未补价占位行继续保持现有阻断。
- 确认推送成功后，如后端返回 `no_cost_draft_summary`，展示“已生成 X 条成本库待审核草稿，跳过 Y 条”。

Vite 报价运营详情：

- 继续展示无底价参考、占位、人工改价和风险检查。
- 如接口已有确认结果摘要，可展示 no-cost draft 生成情况。

成本库页面：

- 现有 `status=draft` 可作为第一入口。
- 如果当前 API/页面没有 `source=ai_suggested` 筛选，BIZ-2m 可补充 source 筛选，不新增数据库。
- 不新增独立“无底价候选库”页面。

### 3.10 与现有逻辑的关系

BIZ-2m 不改变以下既有逻辑：

- 报价前置成本上下文仍只取 `cost_items.active`。
- 预审成本参考匹配仍只取 `cost_items.active`。
- 成本库底价兜底仍只对已命中 active 的行生效。
- active RAG 同步仍只同步 `cost_items.active`。
- `draft` 和 `archived` 不参与报价、不参与兜底、不参与 active RAG。
- BIZ-2l-6 占位行未补价前继续阻断 `/confirm_push`。

## 4. 阶段拆分

### BIZ-2m-0 计划确认

产物：

- 本计划文档。
- 路线图和上下文文档索引更新。

验收：

- 业务能看懂“做什么、为什么做、怎么做”。
- 明确哪些行为本阶段不做。
- 明确开发完成后的测试和演示标准。

### BIZ-2m-1 后端规则落地

内容：

- 新增无底价 draft 提取/写库服务。
- 接入 `/confirm_push` 成功分支。
- 增加 `FEATURE_NO_COST_DRAFT_CAPTURE`。
- 返回生成摘要。
- 写入状态历史。

验收：

- 推送成功后生成 `cost_items.draft(source=ai_suggested)`。
- 推送失败不生成 draft。
- 匹配 active 的行不生成 draft。
- 未补价占位行仍 409 阻断。
- 已补价占位行可以生成 draft。
- 重复 draft 不重复生成。

### BIZ-2m-2 前端提示与复核入口

内容：

- 旧预审弹窗展示固定无底价提示。
- 推送成功后展示 draft 生成摘要。
- 成本库页面补充 source 筛选或至少保证可通过 `draft + keyword` 定位。

验收：

- 业务员能在预审时清楚知道该行无成本库参考。
- 业务员确认后能知道哪些条目进入待审核。
- 成本部能筛出无底价沉淀的 draft。

### BIZ-2m-3 测试、模拟演示和验收闭环

内容：

- 补后端单元测试和 API 测试。
- 编写模拟演示规划。
- 用模拟数据跑完整流程。
- 记录验收结果和不顺畅点。

验收：

- 自动化测试覆盖核心分支。
- 模拟演示能从报价确认走到成本库 draft。
- draft 未启用前不会参与下一次报价。
- 启用为 active 后，后续报价可以命中。
- 文档记录问题和改进项。

## 5. 验收样例设计

### 样例 1：无底价 AI 估价生成 draft

输入：

- 项目：定制异形收口条
- 单位：m
- AI 单价：45
- 合计：450
- `cost_reference.matched=false`

预期：

- `/confirm_push` 成功后生成 1 条 `cost_items.draft`。
- `source=ai_suggested`。
- `price=45`。
- `notes` 包含 quote_job_id / quote_history_id / requirement_row_key。

### 样例 2：有 active 成本参考不生成 draft

输入：

- 项目：窗帘盒灯槽拆除
- 单位：m
- `cost_reference.matched=true`
- `cost_item_id` 指向 active 条目

预期：

- `/confirm_push` 成功。
- 不生成无底价 draft。

### 样例 3：未补价占位行继续阻断

输入：

- `requirement_placeholder=true`
- 单价为空或 0
- 合计为空或 0

预期：

- `/confirm_push` 返回 409。
- 不推送钉钉。
- 不生成 draft。

### 样例 4：已人工补价占位行生成 draft

输入：

- `requirement_placeholder=true`
- 人工单价：88
- 系统合计：880
- `cost_reference.matched=false`

预期：

- `/confirm_push` 成功。
- 生成 `draft(source=ai_suggested)`。
- `notes` 标记来源为人工补价占位行。

### 样例 5：重复 draft 不重复创建

输入：

- 同一 `item_name + spec + unit` 连续确认两次。

预期：

- 第一次创建 draft。
- 第二次返回 `skipped_existing_draft=1`。
- 成本库中不出现重复待审核条目。

### 样例 6：推送失败不生成 draft

输入：

- N8N push 返回非 200 或模拟异常。

预期：

- `/confirm_push` 返回失败。
- 不生成 `cost_items.draft`。
- 不写成本库历史。

### 样例 7：draft 不参与后续报价

步骤：

1. 样例 1 生成 draft。
2. 不启用 active。
3. 再次提交相同项目报价。

预期：

- 仍显示无成本库参考。
- 不触发成本库底价兜底。
- 不进入 active RAG 同步 payload。

### 样例 8：启用 active 后参与后续报价

步骤：

1. 成本部人工核定样例 1 draft。
2. 将其启用为 `active`。
3. 再次提交相同项目报价。

预期：

- 预审单命中成本库参考价。
- 成本库前置上下文可把该 active 价传给 AI。
- 如果 AI 返回 0 且数量有效，既有 BIZ-2g 底价兜底可生效。

## 6. 自动化测试计划

建议新增或补充：

```text
AI_Middle_Office/tests/test_no_cost_draft_capture_biz2m.py
AI_Middle_Office/tests/test_quote_confirm_push_biz2m.py
```

覆盖点：

- 提取无底价候选。
- 过滤 active 命中行。
- 过滤无项目名、无单位、非正数单价/合计。
- 未补价占位行由现有 409 阻断。
- 已补价占位行进入候选。
- 创建 draft 字段映射正确。
- 写入 `cost_item_history`。
- 已有 draft 不重复创建。
- 已有 active 不创建重复 draft。
- push 失败不创建 draft。
- feature flag 关闭时不创建 draft。
- `draft` 不被 `quote_cost_matching.load_active_cost_items` 返回。

执行标准：

```text
python -m pytest AI_Middle_Office/tests/test_no_cost_draft_capture_biz2m.py
python -m pytest AI_Middle_Office/tests/test_quote_confirm_push_biz2m.py
python -m pytest AI_Middle_Office/tests/test_quote_jobs.py
python -m pytest AI_Middle_Office/tests/test_quote_cost_matching_biz2b.py
npm run build
```

如改动旧 `index.html`，还要做脚本语法检查。

## 7. 模拟演示规划

执行完成后，需要单独形成演示记录。建议演示包含四段：

### 7.1 演示准备

- 后端、Celery worker 正常。
- Alembic 为 `20260526_0023` 或更高。
- `FEATURE_COST_DB=true`。
- `FEATURE_NO_COST_DRAFT_CAPTURE=true`。
- 准备 4 行模拟报价：
  - 1 行命中 active。
  - 1 行无底价 AI 正常估价。
  - 1 行无底价人工改价。
  - 1 行 AI 未返回占位，先不补价，再补价。

### 7.2 业务员视角演示

- 发起报价。
- 查看预审单。
- 确认无底价提示是否明确。
- 对无底价行人工确认或改价。
- 尝试下发未补价占位行，确认被阻断。
- 补齐占位行价格后再次下发。
- 查看成功提示中的 draft 生成摘要。

### 7.3 成本部视角演示

- 打开成本库。
- 筛选 `status=draft` 和 `source=ai_suggested`。
- 查看新生成条目的名称、规格、单位、价格、来源任务和备注。
- 选择一条复核启用为 `active`。

### 7.4 回归演示

- 启用前再次报价：仍无底价参考。
- 启用后再次报价：命中 active 成本参考。
- 同步 RAG 前确认 draft 不进入 active 同步 payload。
- 同步 active 后确认只有 active 进入 RAG。

演示完成后要记录：

- 操作是否顺畅。
- 提示文案是否够清楚。
- 成本部是否容易找到待审核条目。
- 重复 draft 是否造成困扰。
- 是否需要后续新增结构化来源字段。

## 8. 明确不做什么

BIZ-2m 不做：

- 不让 AI 估价自动成为 `active`。
- 不让 `draft` 参与报价成本参考。
- 不让 `draft` 触发成本库底价兜底。
- 不把 `draft` 同步到 active RAG。
- 不自动覆盖已有 active 成本条目。
- 不改变报价价格口径。
- 不改变 N8N/Dify 工作流。
- 不迁移旧 `index.html` / `admin.html` / `app.html`。
- 不生成新的 HTML 页面。
- 不启动 Phase 4b/4c/6。
- 不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 不做成本价权限完整改造；权限落地另按成本价权限草案执行。
- 不做大量历史 quote_history 回填；如需回填必须单独规划脚本和验收。

## 9. 是否需要 Alembic

首版推荐不新增数据库结构，原因：

- `cost_items` 已有 `status`、`source`、`notes`、`created_by`。
- `cost_item_history` 已能记录状态变化。
- `COST_SOURCE_AI_SUGGESTED="ai_suggested"` 已存在。
- 当前目标是把规则闭环跑通，不先扩展来源追溯模型。

必须暂停并补 Alembic 的情况：

- 要新增 `source_quote_job_id`、`source_quote_history_id` 等结构化字段。
- 要新增独立候选表。
- 要把人工价格依据做成独立字段并强制校验。
- 要做历史回填状态和审核流。

## 10. 最终验收标准

开发完成后，BIZ-2m 只有同时满足以下条件才算通过：

- 无底价行在预审中有明确提示文案。
- 人工确认并下发成功后，无底价条目进入成本库 `draft`。
- 自动生成条目全部为 `source=ai_suggested`，没有任何自动 `active`。
- 未补价占位行仍然阻断下发。
- 已补价占位行可以进入待审核 draft。
- 相同 draft 不重复堆积。
- 有 active 成本参考的行不生成无底价 draft。
- 推送失败不生成成本库 draft。
- draft 在启用前不参与后续报价、不参与兜底、不参与 RAG active 同步。
- draft 经人工启用为 active 后，后续报价可以命中。
- 后端测试、相关报价测试和前端 build 通过。
- 已完成模拟演示，并记录不清晰、不流畅或需要后续改进的地方。
