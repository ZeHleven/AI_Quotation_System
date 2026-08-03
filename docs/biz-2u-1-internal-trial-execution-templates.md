# BIZ-2u-1 小范围内网试运行执行模板包

> 状态：已完成文档层准备（2026-05-28）  
> 前置：BIZ-2u 小范围内网试运行准备包已完成；BIZ-2t-1 已形成高风险整改交接清单  
> 结论：本阶段只提供试运行执行模板，不代表正式试运行已经启动  
> 边界：不启动生产、不上云、不开放公网；不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则；不写数据库、不自动删除/合并/改价/启用 active；不新增 Alembic；不新增页面

## 1. 阶段目标

BIZ-2u-1 的目标是把 BIZ-2u 的试运行准备方案进一步落成可填写、可追踪、可复盘的执行材料。

本阶段产出：

1. 试运行样例登记表。
2. 试运行问题反馈台账。
3. 每日试运行检查清单。
4. 试运行验收记录模板。

这些材料用于后续真实小范围内网试运行，但本阶段不发起真实报价、不启动服务、不修改数据库。

## 2. 产物清单

| 文件 | 用途 |
|------|------|
| `reports/biz2u/20260528_trial_templates/trial_sample_register.csv` | 登记首批试运行样例、覆盖场景、参与人员和执行状态 |
| `reports/biz2u/20260528_trial_templates/trial_feedback_log.csv` | 记录试运行问题、严重级别、责任人、处理状态和结论 |
| `reports/biz2u/20260528_trial_templates/trial_daily_checklist.md` | 每天启动前、执行中和日终复盘的检查清单 |
| `reports/biz2u/20260528_trial_templates/trial_acceptance_record.md` | 试运行阶段验收记录、角色确认和是否扩大范围的结论模板 |

## 3. 使用顺序

1. 成本部先处理或说明 `docs/biz-2t-high-risk-cost-handoff.md` 中的 5 条高风险 active 来源价问题。
2. 管理员复制或打开 `trial_sample_register.csv`，为首批 3 到 5 个样例填写操作人、成本复核人、预期覆盖场景和样例状态。
3. 每天开始前按 `trial_daily_checklist.md` 做启动检查，确认 `PUBLIC_ACCESS_ENABLED=false`、账号角色、RAG 同步状态和高风险项说明。
4. 试运行过程中，所有问题统一记录到 `trial_feedback_log.csv`，并标记是否阻断继续试运行。
5. 每日结束后，由管理员按检查清单做日终复盘，必要时暂停相关样例。
6. 首轮样例完成后，使用 `trial_acceptance_record.md` 汇总业务员、成本部、管理员和负责人的验收结论。

## 4. 样例登记口径

样例登记表建议至少覆盖以下场景：

| 样例 | 场景 | 验收重点 |
|------|------|----------|
| TRIAL-01 | 普通 active 成本库命中 | 成本参考价、AI 价差、证据链、合计来源显示正常 |
| TRIAL-02 | 无底价项目 | 必须人工确认价格；下发成功后只沉淀为 draft，不自动 active |
| TRIAL-03 | 多个 active 候选 | 必须确认当前成本依据或切换条目；未确认前阻断下发 |
| TRIAL-04 | 标准清单发起报价 | 已确认行进入报价；阻断行、说明行和空白行不进入报价 |
| TRIAL-05 | 大清单或 AI 缺失行 | 缺失行生成占位；未补价前前端和 `/confirm_push` 阻断下发 |
| TRIAL-06 | 权限边界抽查 | `staff` 不能浏览完整成本库；成本专项角色按钮边界符合 BIZ-2s |

如果样例包含 BIZ-2t 高风险项，必须在 `notes` 中写明成本部处理结论或临时风险说明。

## 5. 问题反馈口径

问题类型建议使用：

| issue_type | 含义 |
|------------|------|
| `cost_match` | 成本库命中、候选、证据链或价差问题 |
| `no_cost` | 无底价、人工确认、draft 沉淀问题 |
| `multi_candidate` | 多 active 候选确认或阻断问题 |
| `permission` | 角色权限、成本价可见性或按钮边界问题 |
| `draft` | 预审草稿保存、恢复或清理问题 |
| `ai_output` | AI 漏行、合并行、占位或报价内容异常 |
| `rag_sync` | active 到 RAG 同步状态或一致性问题 |
| `system_error` | 服务、任务、页面或接口异常 |
| `other` | 其他问题 |

严重级别建议使用：

| severity | 判断标准 | 处理要求 |
|----------|----------|----------|
| `blocker` | 成本价泄露、错误下发、数据损坏、公开访问打开 | 立即暂停试运行 |
| `high` | 影响报价下发或成本依据判断 | 当日定位，必要时暂停相关样例 |
| `medium` | 影响效率但有人工绕行方式 | 进入修复列表 |
| `low` | 文案、排序、易用性建议 | 试运行后集中优化 |

## 6. 每日检查口径

每日检查清单分为四段：

1. 启动前：确认环境、权限、高风险项、样例和 RAG 同步状态。
2. 报价中：确认预审、依据、多候选、无底价、占位、草稿和下发阻断。
3. 成本复核：确认新增 draft、active 变更、重复风险和是否需要 RAG 同步。
4. 日终复盘：确认任务数量、问题数量、阻断项、负责人和次日是否继续。

只要出现 blocker，建议当天暂停试运行并保留任务号、截图和接口证据。

## 7. 验收口径

本模板包自身的验收口径：

1. 所有模板文件存在并可打开。
2. CSV 表头可被工具正常解析。
3. 模板明确区分试运行准备、执行记录和验收结论。
4. 文档明确本阶段不启动真实试运行。
5. 文档明确系统继续保持 `PUBLIC_ACCESS_ENABLED=false`。
6. 文档明确不改报价规则、不改价格口径、不自动治理成本库、不触发 RAG/N8N/Dify。

后续真实试运行的验收口径见 `trial_acceptance_record.md`。

## 8. 风险和回滚

主要风险：

1. 模板被误认为试运行已经启动。
2. BIZ-2t 高风险项未处理就开始试运行。
3. 反馈台账字段填写不完整，后续无法复盘。
4. 模板字段过多，业务人员不愿意填写。

规避方式：

1. 所有模板顶部都写明“正式试运行未启动”。
2. 每日检查清单把 BIZ-2t-1 高风险项列为启动前检查项。
3. 反馈台账保留最少必填项：反馈编号、样例编号、问题类型、严重级别、是否阻断、负责人、状态。
4. 验收记录只要求角色结论和阻断问题，避免变成额外流程负担。

回滚方式：

1. 删除 `docs/biz-2u-1-internal-trial-execution-templates.md`。
2. 删除 `reports/biz2u/20260528_trial_templates/` 下的模板文件。
3. 回退 `AGENTS.md`、`AI_Middle_Office/AGENTS.md`、`ROADMAP.md`、`PROJECT_PROGRESS_AND_PLAN.md` 和 `docs/biz-2u-internal-trial-preparation.md` 中对 BIZ-2u-1 的引用。

本阶段不改代码、不写库、不启动服务，因此无需数据库回滚、服务回滚或数据修复。

## 9. 后续动作

建议下一步仍然按以下顺序推进：

1. 成本部处理或说明 BIZ-2t 的 5 条高风险 active 来源价问题。
2. 如成本库 active 有人工变更，由 `cost_approver` 判断是否重新同步 active 到 RAG。
3. 用本阶段模板登记首批样例、人员和反馈口径。
4. 满足准入条件后，再单独启动第一天小范围内网试运行。

## 10. BIZ-2u-2 启动前登记与检查

BIZ-2u-2 已补充启动前登记与检查材料，用于把 5 条 `accepted_risk` 高风险项正式登记为试运行已知风险。

新增材料：

| 文件 | 用途 |
|------|------|
| `docs/biz-2u-2-internal-trial-readiness-check.md` | 启动前登记与检查说明 |
| `reports/biz2u/20260528_trial_readiness/known_risk_register.csv` | 已知风险登记表 |
| `reports/biz2u/20260528_trial_readiness/trial_readiness_checklist.md` | 启动前检查清单 |
| `reports/biz2u/20260528_trial_readiness/trial_readiness_summary.json` | 启动前摘要 |

当前结论为 `ready_with_known_risks_pending_start_confirmation`，正式小范围内网试运行仍未启动。
