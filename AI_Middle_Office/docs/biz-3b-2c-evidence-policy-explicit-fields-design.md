# BIZ-3b-2c 证据策略与 EPC 字段显式化设计

> 本阶段只做设计，不一定马上改库。设计通过后，再决定是否用 Alembic 加字段、何时迁移。
> 本文不新增任何 Alembic revision，不改动代码，不迁移旧 HTML。

## 背景

BIZ-3b-1 到 BIZ-3b-2b 已落地成果证据的最小可用版、无证据软提醒和缺证据汇总。下一步 BIZ-3b-3 想做的是「关键节点证据硬门禁」——某些 EPC 节点缺证据时不允许完成，或需要项目经理填原因放行。

但要让硬门禁正确，必须先回答一个数据层面的问题：**系统怎么知道一个任务"需要什么成果证据""是不是关键节点""门禁有多硬"？**

当前的真实情况是：

1. EPC 模板任务的成果要求并不是结构化字段，而是由 `epc_task_description()` 把「流程内容/执行标准/辅助部门/监督部门/成果文件」五行文本拼进 `project_tasks.description`。
2. 运行时再由 `parse_epc_task_meta()` 从 `description` 里按中文标签前缀解析出来，`task_evidence_requirement()` 取其中的「成果文件」作为成果要求。
3. `project_tasks` 表里**没有** `evidence_requirement` / `evidence_policy` / `is_key_node` / `epc_content` / `epc_standard` / `epc_deliverable` 任何显式列。
4. 是否「关键节点」目前完全没有数据来源，只能靠 BIZ-3a-2 精简模板"恰好只放了关键节点"这一约定隐含表达，系统本身无法区分。

也就是说，成果要求是**写进自由文本、再用字符串前缀反解**出来的。这在 MVP 阶段（只提醒、不阻断）是可接受的，但作为硬门禁的判定依据有结构性风险：

- 用户在前端编辑了 `description`（哪怕只是补一句说明），就可能破坏标签前缀，导致 `parse_epc_task_meta()` 解析不到成果要求，门禁随之静默失效。
- 「关键节点」无字段承载，硬门禁无法精确锁定到「竣工验收/结算移交」这类必须留证的节点，要么一刀切全管，要么全不管。
- 门禁强度（软提醒 / 必须有证据）和成果要求耦合在同一段文本里，无法分别配置。

本阶段的目标，是在动手做硬门禁之前，先把这些隐含语义升级为显式、可配置、可审计的字段，并锁定"单一事实来源"口径。

## 设计目标

1. 把"成果要求是什么"从 `description` 解析升级为显式字段 `evidence_requirement`。
2. 新增"证据策略" `evidence_policy`，明确每个任务的门禁强度。
3. 新增"是否关键节点" `is_key_node`，让硬门禁能精确锁定到关键节点。
4. 确立单一事实来源：模板生成时写入显式列，运行时只读显式列，`description` 退化为展示用途。
5. 策略由模板初始化时种入，支持单任务覆盖。
6. 为 BIZ-3b-3 的门禁放行事件预留决策快照口径。
7. 统一历史文档中 `evidence_policy` 的取值命名分歧。

## 非目标

- 不在本阶段执行 Alembic 迁移或改库。
- 不实现硬门禁逻辑本身（属于 BIZ-3b-3）。
- 不改动 2580 进度规则。
- 不改动证据登记/删除/下载链路（BIZ-3b-1 已落地）。
- 不接钉钉/企微自动同步。
- 不迁移旧 `index.html` / `admin.html` / `app.html`。

## 取值命名统一（先解决历史分歧）

历史上出现过两套 `evidence_policy` 取值命名：

| 来源 | 取值 |
|---|---|
| BIZ-3b-0 草案 | `optional` / `submit_remind` / `complete_required` |
| 后续讨论（Codex / Claude） | `none` / `soft_reminder` / `complete_required` |

本设计**以后者为准**，并废弃前者命名。理由：`none` / `soft_reminder` / `complete_required` 与当前 BIZ-3b-2a 已落地的运行行为一一对应，语义更直白。

最终口径（规范）：

| `evidence_policy` | 含义 | 提交到 80% | 完成到 100% | 当前对应行为 |
|---|---|---|---|---|
| `none` | 无证据要求 | 不提醒 | 不要求 | 普通手工任务默认值 |
| `soft_reminder` | 软提醒（当前默认软门禁） | 弹提醒，可继续 | 必须填确认原因，可继续 | BIZ-3b-2a 已落地行为 |
| `complete_required` | 完成硬门禁 | 弹提醒，可继续 | 无证据不允许完成，需放行 | BIZ-3b-3 待实现 |

说明：`soft_reminder` 完整复刻 BIZ-3b-2a 当前行为，本阶段不改变任何运行逻辑，只是把"是否软提醒"从"有没有成果要求"这一隐含判断，升级为显式 `evidence_policy` 字段。

## 字段设计建议（仅设计，不迁移）

建议在 `project_tasks` 上新增以下列。**本阶段不创建 Alembic revision**，仅锁定字段语义，待设计通过后由 BIZ-3b-3 实现阶段统一迁移。

| 字段 | 类型建议 | 默认 | 说明 |
|---|---|---|---|
| `evidence_requirement` | `Text` nullable | `NULL` | 成果要求显式文本。模板生成时由「成果文件」种入；手工任务可留空或人工填写。 |
| `evidence_policy` | `String(32)` not null | `none` | 证据策略，取值 `none` / `soft_reminder` / `complete_required`。 |
| `is_key_node` | `Boolean`（或 `String(8)` 兼容口径）not null | `false` | 是否关键节点。播种规则按模板区分：`qisheng_epc_compact_v1` 全部 `true`，`qisheng_epc_full_v1` 按行 `compact` 标记，`single_user_fitout_v1` 默认 `false`（详见「`is_key_node` 播种规则」）。 |

可选拆分字段（按需，非必须）：

| 字段 | 说明 |
|---|---|
| `epc_content` | EPC「流程内容」，目前在 description 里 |
| `epc_standard` | EPC「执行标准」 |
| `epc_deliverable` | EPC「成果文件」 |

关于可选拆分字段的判断：当前 `parse_epc_task_meta()` 已能稳定解析这五行，且除「成果文件 → 成果要求」外，其余字段只用于展示。**建议本期先不拆 `epc_content` / `epc_standard` / `epc_deliverable`**，只把真正参与判定的「成果要求」显式化为 `evidence_requirement`，把策略和关键节点显式化为 `evidence_policy` / `is_key_node`。其余 EPC 元数据继续保留在 `description` 用于展示，等真正出现"需要按执行标准做结构化校验"的需求时再拆，避免一次性引入低收益冗余列。

## 单一事实来源口径（核心契约）

这是本阶段最重要的约定，硬门禁的可靠性完全依赖它：

1. **写入侧**：模板实例化（`single_user_fitout_v1` / `qisheng_epc_compact_v1` / `qisheng_epc_full_v1`）时，由模板定义直接写入 `evidence_requirement` / `evidence_policy` / `is_key_node` 三个显式列。`description` 仍可继续拼接五行 EPC 文本，但仅作展示。
2. **读取侧**：运行时判定（成果要求、是否软提醒、是否硬门禁、是否关键节点）**只读显式列**，不再调用 `parse_epc_task_meta()` 反解 `description`。
3. **`description` 角色**：退化为纯展示文本。用户编辑 `description` 不再影响门禁判定。
4. **迁移期兼容（设计预案）**：迁移落地时，对存量 EPC 任务做一次性回填——用现有 `parse_epc_task_meta()` 把「成果文件」写进 `evidence_requirement`，按下文「`is_key_node` 播种规则」种入 `is_key_node`，按当前"有成果要求即软提醒"口径种入 `evidence_policy`。回填后 `task_evidence_requirement()` 改为优先读显式列，解析逻辑保留为兜底直至回填确认完成，再移除对解析的运行时依赖。

## 策略种入与覆盖

- **种入**：策略来源于模板。关键节点种入 `is_key_node=true`（播种规则见下）；成果要求节点种入 `evidence_policy=soft_reminder`（与当前行为一致），`evidence_requirement` 取自模板「成果文件」。BIZ-3b-3 上线后，竣工验收 / 结算移交等明确关键节点可在模板侧升级为 `complete_required`。
- **覆盖**：单个任务允许人工调整 `evidence_policy` / `evidence_requirement` / `is_key_node`，覆盖模板默认值。例如某个非模板手工任务也想要硬门禁，可单独设 `complete_required`。
- **覆盖审计**：人工修改证据策略应写入 `project_task_events`（事件类型建议 `task_evidence_policy_changed`），记录 from/to 策略，便于追溯"门禁是谁、何时改的"。

### `is_key_node` 播种规则（按模板分别处理）

`is_key_node` 不能简单按"是不是 EPC 模板"种入，必须区分到具体模板：

- **`qisheng_epc_compact_v1`（EPC 精简模板）**：全部实例化任务 `is_key_node=true`。精简模板本身就是"关键节点优先"筛出来的子集，落库的每个任务都是关键节点。
- **`qisheng_epc_full_v1`（EPC 完整模板）**：按模板行的 `compact` 标记播种——`compact=true` 的节点 `is_key_node=true`，其余节点 `is_key_node=false`。注意完整模板包含全部 82 个节点，其中既有原本 `compact=true` 的关键节点，也有大量普通节点，不能整批种为 true。
- **`single_user_fitout_v1`（单人试运行八阶段模板）**：默认 `is_key_node=false`，除非后续模板单独标定关键节点。

> 实现要点：`qisheng_epc_template_tasks(mode)` 中 compact 模式正是用 `item["compact"]` 过滤完整节点表得到的，因此完整模板回填 `is_key_node` 时直接复用同一 `compact` 标记即可，口径与精简模板筛选保持一致。

## 模板族口径：先确认 source_id 是否够用

讨论中提过是否需要新增 `project_template_type` 或"阶段族"字段。结论：**先不新增，复用现有 `source_type` / `source_id`**。

理由：`project_tasks` 已有 `source_type`（默认 `manual`）和 `source_id`（String(64)），模板实例化时即可把模板身份（`single_user_fitout_v1` / `qisheng_epc_compact_v1` / `qisheng_epc_full_v1`）写入 `source_id`。要区分"这是 EPC 五阶段族还是默认八阶段族""是不是关键节点优先的精简模板"，理论上 `source_id` 已携带足够信息。

**重要口径：`source_id` 是任务级字段（`project_tasks.source_id`），不是项目级字段。** `projects` 表本身没有模板族字段，"这个项目属于哪个模板族"是从其任务的 `source_id` **反推**出来的。这一点必须在后续 BIZ-3 经营驾驶舱聚合时特别注意：聚合"按模板族统计"时，要从任务 `source_id` 归并到项目，而不是误以为 `projects` 上有现成字段。如果将来驾驶舱确实需要稳定的项目级模板族口径，再评估是否在 `projects` 上加一个冗余字段，但本期不加。

行动建议：在 BIZ-3b-3 实现前，先核对模板实例化代码是否确实把模板 key 落到了 `project_tasks.source_id`。

- 若已落到 `source_id`：不新增 `project_template_type`，阶段族判定直接从任务 `source_id` 前缀/枚举反推。
- 若未落到 `source_id` 或落得不规范：优先补齐 `source_id` 写入，仍不单独加 `project_template_type`，除非后续出现 `source_id` 无法表达的多维度模板分类需求。

## 门禁放行事件决策快照（为 BIZ-3b-3 预留）

BIZ-3b-3 硬门禁放行时，`project_task_events.payload_json` 应记录一份决策快照，便于事后审计"为什么这个无证据任务被允许完成"。建议快照字段：

```json
{
  "is_key_node": true,
  "evidence_policy": "complete_required",
  "evidence_requirement": "竣工验收报告",
  "evidence_count_at_decision": 0,
  "bypass_reason": "纸质验收单已线下归档，扫描件次日补传",
  "bypassed_by_role": "project_manager",
  "source_id": "qisheng_epc_compact_v1"
}
```

要点：

- 快照记录"决策当时"的策略与关键节点标记，而非事后读取（事后字段可能被改）。
- `event_type` 建议 `task_completed_bypass_gate`，与 BIZ-3b-2a 的 `task_completed_without_evidence` 区分：前者是硬门禁放行，后者是软门禁确认。
- 放行权限边界（谁能放行）由 BIZ-3b-3 定义，本阶段只锁定快照口径。

## 预计涉及文件（迁移落地时，非本阶段）

仅作前瞻记录，本阶段不改动：

| 文件 | 预期改动 |
|---|---|
| `app/models/project_progress.py` | `ProjectTask` 新增三列 |
| `alembic/versions/` | 新增 revision（BIZ-3b-3 实现阶段） |
| `app/services/project_progress.py` | 模板种入写显式列；`task_evidence_requirement()` 改读显式列；新增策略变更/门禁放行事件 |
| `app/api/v1/project_progress.py` | 任务创建/更新接收 `evidence_policy` 等字段；硬门禁校验 |
| `ai-web/src/App.vue` | 任务编辑显式策略/关键节点；门禁放行交互 |

## 风险与取舍

- **不立即改库的风险**：当前门禁判定仍依赖 `description` 解析，存量行为不变；本阶段只锁口径，不引入新风险。
- **回填准确性**：迁移期一次性回填依赖现有解析逻辑，若历史 `description` 已被人工破坏，回填可能漏判，需在迁移脚本中对解析失败的任务输出清单人工复核。
- **关键节点判定**：`is_key_node` 初值按模板播种（compact 全 true、full 按行 `compact` 标记、单人模板默认 false），是保守近似；真正哪些节点必须硬门禁，仍需成本/项目管理部确认后在模板侧逐个标定。
- **过度结构化**：拆分 `epc_content` / `epc_standard` 等收益低，本期不做，避免冗余列蔓延。

## 验收标准（设计阶段）

本阶段为设计文档，验收标准为设计被确认，不涉及代码/迁移：

1. `evidence_policy` 取值统一为 `none` / `soft_reminder` / `complete_required`，废弃 `optional` / `submit_remind`。
2. 明确单一事实来源：模板写显式列、运行时只读显式列、`description` 仅展示。
3. 明确策略种入 + 单任务覆盖 + 覆盖审计事件。
4. 明确先复用 `source_id` 表达模板族，不新增 `project_template_type`（待核对落库情况）。
5. 明确 BIZ-3b-3 门禁放行的决策快照口径与事件类型。
6. 明确本阶段不创建 Alembic revision、不改代码。

## 结论

BIZ-3b-2c 不写代码、不迁移，只把"成果要求/证据策略/关键节点"三类隐含语义升级为显式、可配置、可审计的字段设计，并锁定单一事实来源口径。设计通过后，由 BIZ-3b-3 在硬门禁实现时统一新增 Alembic revision、回填存量任务，并把运行时判定切换为只读显式列。

## 后续

设计确认后进入 BIZ-3b-3：

1. 新增 `evidence_requirement` / `evidence_policy` / `is_key_node` 列的 Alembic revision。
2. 模板种入显式列 + 存量回填。
3. `complete_required` 关键节点硬门禁与放行决策快照。
