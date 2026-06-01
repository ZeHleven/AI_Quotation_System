# BIZ-2u-2 小范围内网试运行启动前登记与检查包

> 日期：2026-05-28  
> 状态：已完成启动前登记与检查材料；正式小范围内网试运行仍未启动  
> 前置：BIZ-2t-2 高风险整改结果复核结论为 `ready_with_known_risks`  
> 边界：不写数据库、不启动服务、不触发 RAG 同步、不改报价规则、不改价格口径、不改 N8N/Dify、不自动治理成本库、不新增 Alembic、不新增页面

## 1. 阶段目标

BIZ-2u-2 用来承接 BIZ-2t-2 的复核结论，把 5 条 `accepted_risk` 高风险成本条目登记为小范围内网试运行的已知风险，并形成启动前检查清单。

本阶段只回答三个问题：

1. 高风险项是否已有明确人工结论。
2. 是否已经把这些结论登记为试运行已知风险。
3. 若后续要启动试运行，启动当天还需要检查什么。

## 2. 输入材料

| 材料 | 路径 | 用途 |
|------|------|------|
| 高风险整改交接表 | `reports/biz2t/20260528_current/cost_governance_high_risk_handoff.csv` | 读取 5 条高风险项的处理结论 |
| 高风险复核报告 | `reports/biz2t/20260528_current/high_risk_handoff_review.json` | 确认 `ready_with_known_risks` 与阻断数量 |
| 试运行样例模板 | `reports/biz2u/20260528_trial_templates/trial_sample_register.csv` | 后续登记真实样例 |
| 问题反馈模板 | `reports/biz2u/20260528_trial_templates/trial_feedback_log.csv` | 后续记录试运行问题 |
| 每日检查模板 | `reports/biz2u/20260528_trial_templates/trial_daily_checklist.md` | 后续试运行当天检查 |

## 3. 输出材料

| 材料 | 路径 | 说明 |
|------|------|------|
| 已知风险登记表 | `reports/biz2u/20260528_trial_readiness/known_risk_register.csv` | 登记 5 条 `accepted_risk` 成本项 |
| 启动前检查清单 | `reports/biz2u/20260528_trial_readiness/trial_readiness_checklist.md` | 汇总启动前必检项 |
| 启动前摘要 JSON | `reports/biz2u/20260528_trial_readiness/trial_readiness_summary.json` | 给后续脚本或人工复核使用 |

## 4. 已知风险登记结果

| 指标 | 结果 |
|------|------|
| 高风险输入数量 | 5 |
| 已登记已知风险数量 | 5 |
| `accepted_risk` 数量 | 5 |
| `trial_blocker_count` | 0 |
| `need_rag_sync_count` | 0 |
| 当前启动建议 | `ready_with_known_risks_pending_start_confirmation` |

登记的成本条目：

| issue_id | cost_item_id | 名称 | 单位 | 当前价格 | 结论 |
|----------|--------------|------|------|----------|------|
| BIZ2T-0001 | 203 | 临时静音保护棉铺设 | ㎡ | 110.0 | accepted_risk |
| BIZ2T-0002 | 204 | 定制异形铝合金收口条安装 | ㎡ | 50.0 | accepted_risk |
| BIZ2T-0003 | 206 | 高空局部防尘围挡加固 | 处 | 100.0 | accepted_risk |
| BIZ2T-0004 | 207 | 高空局部防尘围挡加固 | 处 | 100.0 | accepted_risk |
| BIZ2T-0005 | 208 | 甲方指定品牌成品检修口更换 | 处 | 100.0 | accepted_risk |

统一原因：

`临时试运行允许作为已知风险，后续补供应商报价`

统一控制口径：

命中这些成本条目时，报价人员和成本复核人员必须人工复核成本依据、AI 来源、系统合计和最终下发价格。该登记不代表成本数据已经被修正，也不代表可自动启用、改价、归档或同步 RAG。

## 5. 启动前检查结论

当前结论为：`ready_with_known_risks_pending_start_confirmation`。

含义：

1. 5 条高风险项已经有管理员人工说明，不再阻断样例登记。
2. 5 条高风险项仍是已知风险，后续需要补供应商报价或其他业务可解释来源价。
3. 正式试运行尚未启动，必须由负责人单独确认启动。
4. 启动当天仍需检查环境、人员、账号角色、样例、健康状态、RAG 同步记录和反馈台账。

## 6. 不做事项

本阶段不做以下事项：

1. 不启动正式小范围内网试运行。
2. 不写数据库。
3. 不修改、删除、合并、撤回、归档或启用任何成本条目。
4. 不自动沉淀 active 成本库。
5. 不触发 active 到 RAG 同步。
6. 不改报价规则、价格口径和无底价处理原则。
7. 不改 N8N / Dify 工作流。
8. 不新增页面，不迁移旧 `index.html` / `admin.html` / `app.html`。
9. 不新增 Alembic。
10. 不开放公网，继续要求 `PUBLIC_ACCESS_ENABLED=false`。

## 7. 验收方式

本阶段验收只看文件和数据一致性：

1. `known_risk_register.csv` 可正常解析。
2. 已知风险登记表包含 5 条记录。
3. 5 条记录均为 `accepted_risk`。
4. 5 条记录的 `need_rag_sync` 均为 `no`。
5. `trial_readiness_summary.json` 中 `formal_trial_started=false`。
6. 文档明确正式试运行仍未启动。
7. 文档明确不写数据库、不改报价规则、不触发 RAG、不改 N8N/Dify。

## 8. 风险和回滚

主要风险：

1. `accepted_risk` 是带风险进入试运行，不是成本数据已清理完成。
2. 若真实样例命中这些条目，必须依赖人工复核。
3. 后续补供应商报价后，仍需成本部按现有流程人工决定是否改价、撤回、归档、启用 active 或同步 RAG。

回滚方式：

本阶段只新增文档和报告文件。如需回滚，恢复或删除以下文件即可：

1. `docs/biz-2u-2-internal-trial-readiness-check.md`
2. `reports/biz2u/20260528_trial_readiness/known_risk_register.csv`
3. `reports/biz2u/20260528_trial_readiness/trial_readiness_checklist.md`
4. `reports/biz2u/20260528_trial_readiness/trial_readiness_summary.json`

不涉及数据库回滚、Alembic downgrade、服务重启或 RAG 回滚。

## 9. 下一步

下一步可由负责人单独确认是否进入 BIZ-2u-3：小范围内网试运行启动记录。

BIZ-2u-3 若启动，应只记录启动时间、参与人员、首批样例、当天检查结论和反馈台账路径；仍应保持 `PUBLIC_ACCESS_ENABLED=false`，且不扩大到正式生产。
