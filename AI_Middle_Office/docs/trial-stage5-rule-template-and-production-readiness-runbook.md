# 阶段 5：规则模板与正式上线清单

更新时间：2026-06-03

## 1. 阶段目标

阶段 5 的目标是把系统进入真实试运行前需要老板或部门拍板的事项整理成模板，并把“小范围内网试运行”和“正式生产上线”之间的差距讲清楚。

本阶段不新增业务功能，不改数据库，不改报价规则，不改项目进度规则，不把模板直接写成系统强校验。

## 2. 阶段边界

### 可以做

- 准备规则模板。
- 准备正式上线清单。
- 准备每周复盘模板。
- 明确哪些规则需要老板或部门负责人确认。
- 明确哪些事项属于正式上线前必须补齐。

### 不做

- 不替公司决定最终跨部门流程。
- 不新增采购、合同、回款、复杂经营模型。
- 不把项目节点证据规则继续写死。
- 不把演示数据当真实 ROI。
- 不承诺当前 Windows 临时内网方案具备正式生产 SLA。

## 3. 阶段 5 材料清单

| 材料 | 用途 |
|---|---|
| `reports/trial_readiness/20260603_stage5/effective_requirement_rule_template.md` | 有效需求单规则模板 |
| `reports/trial_readiness/20260603_stage5/quote_rejection_reason_template.csv` | 报价退回原因模板 |
| `reports/trial_readiness/20260603_stage5/no_cost_reference_decision_template.md` | 无底价项目处理模板 |
| `reports/trial_readiness/20260603_stage5/project_node_evidence_template.csv` | 项目节点证据要求模板 |
| `reports/trial_readiness/20260603_stage5/exception_escalation_template.csv` | 异常上报模板 |
| `reports/trial_readiness/20260603_stage5/weekly_business_metrics_template.md` | 每周经营指标模板 |
| `reports/trial_readiness/20260603_stage5/production_readiness_checklist.md` | 正式上线前检查清单 |
| `reports/trial_readiness/20260603_stage5/trial_to_production_gap_statement.md` | 试运行转正式生产差距说明 |
| `AI_Middle_Office/docs/trial-stage5-acceptance-20260603.md` | 阶段 5 验收记录 |

## 4. 使用方式

建议使用顺序：

1. 先用 `trial_to_production_gap_statement.md` 对老板说明当前边界。
2. 再用 `effective_requirement_rule_template.md` 和 `quote_rejection_reason_template.csv` 组织报价相关拍板。
3. 用 `no_cost_reference_decision_template.md` 明确无底价项目当前只沉淀 draft。
4. 用 `project_node_evidence_template.csv` 让项目相关负责人确认节点证据要求。
5. 用 `exception_escalation_template.csv` 规范试运行期间异常登记。
6. 用 `weekly_business_metrics_template.md` 做每周复盘。
7. 真正准备扩大使用前，再逐条检查 `production_readiness_checklist.md`。

## 5. 模板状态口径

所有阶段 5 模板默认状态都是：

```text
draft_for_business_decision
```

含义：

- 可以拿去讨论。
- 可以拿去让老板或部门负责人确认。
- 不代表已经成为公司制度。
- 未确认前不应写成系统强规则。

## 6. 进入真实小范围试运行前建议

真实试运行前建议至少完成：

- 明确 1 个试运行负责人。
- 明确 1 到 3 个试运行账号。
- 明确试运行范围是演示、沙盒还是真实需求单。
- 试运行前做一次 MySQL 备份。
- 如要录入真实数据，低峰期补一次 Milvus 冷备。
- 用启动当天检查表确认系统 ready。
- 用异常上报模板记录问题，不现场临时改规则。

## 7. 阶段 5 完成标准

阶段 5 可认为完成，需要满足：

- 规则模板包齐全。
- 正式上线清单齐全。
- 试运行转正式生产差距说明齐全。
- 交付总入口已链接阶段 5 材料。
- 所有模板均明确“待业务拍板”，未写死最终制度。
