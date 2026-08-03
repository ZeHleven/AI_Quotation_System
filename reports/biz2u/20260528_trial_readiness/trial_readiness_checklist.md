# BIZ-2u-2 小范围内网试运行启动前检查清单

> 状态：启动前检查材料。正式小范围内网试运行仍未启动。  
> 输入：BIZ-2t-2 复核结论 `ready_with_known_risks`，5 条高风险项均为 `accepted_risk`。  
> 边界：不写数据库、不启动服务、不触发 RAG 同步、不改报价规则、不改价格口径、不改 N8N/Dify、不自动治理成本库。

## 1. 已知风险登记

| 检查项 | 结果 |
|------|------|
| 已知风险登记表是否生成 | yes |
| 登记表路径 | `reports/biz2u/20260528_trial_readiness/known_risk_register.csv` |
| 登记风险数量 | 5 |
| 处理结论 | 5 条均为 `accepted_risk` |
| 试运行阻断数量 | 0 |
| 是否需要 RAG 同步 | no |
| 试运行控制口径 | 命中这些成本条目时必须人工复核成本依据和最终价格 |
| 后续动作 | 补供应商报价或其他业务可解释来源价 |

## 2. 启动前必检项

| 序号 | 检查项 | 当前判断 | 说明 |
|------|--------|----------|------|
| 1 | `PUBLIC_ACCESS_ENABLED=false` | 待启动当天确认 | 必须继续保持内网验证 |
| 2 | 正式生产 Runbook | 不启动 | 本阶段不是生产上线 |
| 3 | Phase 4b / Phase 4c / Phase 6 | 不启动 | 保持既定边界 |
| 4 | BIZ-1b / BIZ-1c / BIZ-1d | 不启动 | 保持既定边界 |
| 5 | 数据库迁移版本 | 待启动当天确认 | 基线不低于 `20260527_0024` |
| 6 | `/health/ready` | 待启动当天确认 | 由管理员启动前检查 |
| 7 | 最近 active 到 RAG 同步状态 | 待启动当天确认 | 本阶段不触发同步 |
| 8 | 试运行人员 | 待人工填写 | 建议报价部 1-2 人、成本部 1 人、管理员 1 人 |
| 9 | 账号角色 | 待人工填写 | 普通业务员为 `staff`，成本复核按需授予成本专项角色 |
| 10 | 试运行样例 | 模板已准备 | 使用 `reports/biz2u/20260528_trial_templates/trial_sample_register.csv` |
| 11 | 问题反馈台账 | 模板已准备 | 使用 `reports/biz2u/20260528_trial_templates/trial_feedback_log.csv` |
| 12 | 每日检查清单 | 模板已准备 | 使用 `reports/biz2u/20260528_trial_templates/trial_daily_checklist.md` |
| 13 | 5 条 accepted_risk | 已登记 | 作为已知风险进入启动前判断 |
| 14 | 暂停条件 | 已有口径 | 见 `docs/biz-2u-internal-trial-preparation.md` |

## 3. 启动判断

当前结论：`ready_with_known_risks_pending_start_confirmation`。

含义：

1. BIZ-2t-2 的 5 条高风险项已经有管理员结论，不再作为样例登记阻断。
2. 这些条目仍是已知风险，不代表数据已经被修正。
3. 正式试运行仍需要单独确认启动，并在启动当天完成环境、人员、账号、样例和健康检查。

## 4. 试运行当天要求

1. 把当日样例写入 `trial_sample_register.csv`。
2. 如果样例命中 `#203`、`#204`、`#206`、`#207`、`#208`，在样例备注写明“已知风险，临时试运行允许，后续补供应商报价”。
3. 报价预审时人工查看成本依据、AI 来源、系统合计和最终下发价格。
4. 所有异常写入 `trial_feedback_log.csv`。
5. 如果出现成本价泄露、错误下发、公开访问打开、未确认多候选仍能下发等问题，立即暂停相关试运行。

## 5. 回滚方式

本阶段只新增报告和文档。如果结论需要撤回，删除或恢复以下文件即可：

- `reports/biz2u/20260528_trial_readiness/known_risk_register.csv`
- `reports/biz2u/20260528_trial_readiness/trial_readiness_checklist.md`
- `reports/biz2u/20260528_trial_readiness/trial_readiness_summary.json`
- `docs/biz-2u-2-internal-trial-readiness-check.md`

不涉及数据库回滚、Alembic downgrade、服务重启或 RAG 回滚。
