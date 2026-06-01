# BIZ-2t-1 成本库高风险项整改交接清单

> 状态：已完成文档层交接清单（2026-05-28）  
> 来源：`reports/biz2t/20260528_current/cost_governance_actions.csv` 中 `risk_level=high` 且 `trial_blocker=yes` 的 5 条记录  
> 目的：把 BIZ-2t 治理报告里的试运行阻断项整理成成本部可直接逐条处理的清单  
> 边界：本清单只用于人工整改交接；不写数据库、不自动改价、不自动撤回、不自动归档、不自动启用 active；不新增 Alembic；不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则

## 1. 当前结论

BIZ-2t 当前存在 5 条试运行阻断项。

共同问题：

- 条目状态均为 `active`。
- 均已被报价引用过。
- 均缺少至少一个业务可解释来源价：对甲税前综合单价、劳务发包综合单价或班组标底税前价。
- 在成本部处理或出具说明前，不建议直接进入正式小范围内网试运行。

允许的处理方式只有人工处理：

1. 价格可信：由成本部补齐至少一个来源价，并补充备注或依据说明。
2. 价格暂不可确认：由成本部给出人工说明，标记为试运行已知风险。
3. 价格不可信：由具备权限的人员人工撤回为 draft 或归档，避免继续作为 active 成本依据。

## 2. 整改明细

| issue_id | cost_item_id | 来源 | 项目名称 | 规格/特征 | 单位 | 主参考价 | 报价引用次数 | 最近引用时间 | 必填整改动作 |
|----------|--------------|------|----------|-----------|------|----------|--------------|--------------|--------------|
| BIZ2T-0001 | 203 | ai_suggested | 临时静音保护棉铺设 | 夜间施工降噪保护，含铺设与回收；无底价项目，可测试人工改价后下发生成draft。 | ㎡ | 110.0 | 13 | 2026-05-27T17:29:23 | 补齐至少一个业务可解释来源价，或给出人工风险说明 |
| BIZ2T-0002 | 204 | ai_suggested | 定制异形铝合金收口条安装 | 弧形转角，颜色按现场定制，含辅材；无底价项目，需人工确认价格依据 | ㎡ | 50.0 | 10 | 2026-05-27T17:29:23 | 补齐至少一个业务可解释来源价，或给出人工风险说明 |
| BIZ2T-0003 | 206 | manual | 高空局部防尘围挡加固 | 4m以上作业面，含膨胀螺栓、收边和二次固定；未在底层成本库中匹配到该项目，建议人工补充单价 | 处 | 100.0 | 2 | 2026-05-27T17:29:23 | 补齐至少一个业务可解释来源价，或给出人工风险说明 |
| BIZ2T-0004 | 207 | manual | 高空局部防尘围挡加固 | 4m以上作业面，含膨胀螺栓、收边和二次固定；4m以上作业面，含膨胀螺栓、收边和二次固定；无底价新项，需人工定价 | 处 | 100.0 | 1 | 2026-05-27T17:27:49 | 补齐至少一个业务可解释来源价，或给出人工风险说明 |
| BIZ2T-0005 | 208 | ai_suggested | 甲方指定品牌成品检修口更换 | 300x300，品牌待甲方确认，含拆旧与安装；未在现有成本库中找到匹配项，需人工估价；300x300，品牌待甲方确认，含拆旧与安装 | 处 | 100.0 | 2 | 2026-05-27T17:29:23 | 补齐至少一个业务可解释来源价，或给出人工风险说明 |

## 3. 成本部处理表

处理时建议逐条填写以下字段：

| 字段 | 填写要求 |
|------|----------|
| cost_item_id | 对应成本库条目 ID |
| reviewer | 成本部复核人 |
| decision | keep_active / withdraw_to_draft / archive / accepted_risk |
| source_price_type | client_tax_excluded_price / subcontract_composite_price / crew_benchmark_price / other |
| source_price | 人工确认的来源价 |
| reason | 价格依据、供应商依据、历史项目依据或保留原因 |
| need_rag_sync | yes / no |
| done_at | 完成时间 |

## 4. 推荐处理顺序

1. 先处理引用次数最多的 `#203` 和 `#204`。
2. 再处理两个同名的 `#206` 和 `#207`，确认它们是否为合理拆分，还是应人工撤回其中一条。
3. 最后处理 `#208`，确认品牌待定场景下是否适合作为 active 成本依据。
4. 如果任意条目改为 draft 或 archived，需要确认后续试运行样例不再依赖该 active 条目。
5. 如果任意 active 来源价被补齐或状态发生变化，由 `cost_approver` 判断是否重新同步 active 到 RAG。

## 5. 禁止动作

以下动作不能由系统自动执行：

1. 自动把高风险 active 改价。
2. 自动把高风险 active 撤回为 draft。
3. 自动归档高风险 active。
4. 自动合并 `#206` 和 `#207`。
5. 自动把人工说明视为新的成本价。
6. 自动同步 RAG。
7. 自动开始小范围试运行。

## 6. 复核与再生成报告

人工处理完成后，重新运行 BIZ-2t 治理脚本：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe scripts\biz2t_cost_governance_pack.py --output-dir ..\reports\biz2t\20260528_current
```

复核标准：

1. `risk_counts.high` 下降到 0，或 5 条高风险项均有成本部人工说明。
2. `trial_blocker_count` 下降到 0，或已在 BIZ-2u 试运行准备包中登记为已知风险。
3. 最近 RAG 同步仍为 success；如 active 发生变化，需要由 `cost_approver` 决定是否重新同步。
4. `PUBLIC_ACCESS_ENABLED=false` 保持不变。

## 7. BIZ-2t-2 只读复核结果

BIZ-2t-2 已新增只读复核脚本和报告：

- `AI_Middle_Office/scripts/biz2t2_high_risk_handoff_review.py`
- `docs/biz-2t-2-high-risk-handoff-review.md`
- `reports/biz2t/20260528_current/high_risk_handoff_review.md`
- `reports/biz2t/20260528_current/high_risk_handoff_review.csv`
- `reports/biz2t/20260528_current/high_risk_handoff_review.json`

当前复核结果：

| 指标 | 当前值 |
|------|--------|
| 输入记录数 | 5 |
| 已闭环 | 0 |
| 已接受风险 | 5 |
| 待处理 | 0 |
| 填写无效 | 0 |
| 仍阻断试运行 | 0 |
| 建议 | `ready_with_known_risks` |

结论：当前 5 条高风险项均已由管理员标记为 `accepted_risk`，理由为“临时试运行允许作为已知风险，后续补供应商报价”。可以进入 BIZ-2u-1 样例登记和首日试运行准备，但必须把这些条目作为已知风险持续观察。

后续成本部填写完成后，可重新运行：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2t2_high_risk_handoff_review.py
```

## 8. 与 BIZ-2u 的关系

BIZ-2u 已经完成小范围内网试运行准备包，但正式试运行尚未启动。

本清单是 BIZ-2u 正式启动前的成本部交接材料。处理完成后，才建议按 `docs/biz-2u-internal-trial-preparation.md` 组织首日试运行。
