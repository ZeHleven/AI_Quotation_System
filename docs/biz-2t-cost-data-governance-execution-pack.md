# BIZ-2t 成本库数据治理执行包

> 状态：已完成代码层验证并生成当前环境只读治理报告（2026-05-28）  
> 前置：BIZ-2s 成本价权限落地首版已通过当前环境手动验收  
> 边界：只读分析，不写数据库；不自动删除、不自动合并、不自动改价、不自动启用 active；不新增页面；不新增 Alembic；不改报价规则、价格口径、N8N/Dify、无底价 draft 原则和 active 生效原则

## 1. 本阶段目标

BIZ-2t 的目标是把成本库从“功能可用”推进到“适合小范围内网试运行”。

本阶段交付的是治理执行包，而不是自动治理功能：

1. 读取当前成本库、报价引用证据和 RAG 同步记录。
2. 复用 BIZ-2k active 成本库只读体检结果。
3. 生成成本部可逐行处理的人工整改清单。
4. 标记试运行前必须优先处理的高风险项。
5. 给出当前是否适合进入小范围试运行的判断。

## 2. 新增脚本与服务

新增只读治理服务：

```text
AI_Middle_Office/app/services/cost_governance.py
```

新增命令脚本：

```text
AI_Middle_Office/scripts/biz2t_cost_governance_pack.py
```

运行方式：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe scripts\biz2t_cost_governance_pack.py --output-dir ..\reports\biz2t\20260528_current
```

脚本只查询：

- `cost_items`
- `cost_rag_sync_runs`
- `quote_cost_evidence`

不会写入或修改任何数据库记录。

## 3. 当前环境治理基线

本次生成目录：

```text
reports/biz2t/20260528_current/
```

输出文件：

| 文件 | 用途 |
|------|------|
| `cost_governance_summary.md` | 治理摘要和试运行判断 |
| `cost_governance_actions.csv` | 成本部人工整改清单 |
| `cost_governance_actions.xlsx` | 可筛选、可标记完成状态的 Excel 清单 |
| `cost_governance_raw.json` | 完整原始治理结果，供后续脚本复用 |
| `cost_governance_high_risk_handoff.csv` | BIZ-2t-1 高风险项人工整改交接表 |
| `cost_governance_business_readable.md` | BIZ-2k-1 补强后的业务可读版治理说明 |
| `cost_governance_business_readable.xlsx` | BIZ-2k-1 补强后的中文分 Sheet 验收表 |

BIZ-2k-1 已补充业务可读版材料，优先打开：

```text
reports/biz2t/20260528_current/cost_governance_business_readable.xlsx
```

该 Excel 按“验收说明、总览、高风险、中风险、低风险、全部明细”分 Sheet 展示，适合业务人员复验 BIZ-2k。

当前基线：

| 指标 | 数值 |
|------|------|
| 成本条目总数 | 208 |
| active | 195 |
| archived | 13 |
| draft | 0 |
| imported 来源 | 190 |
| manual 来源 | 10 |
| ai_suggested 来源 | 8 |
| 被报价引用过的 active | 42 |
| 治理动作总数 | 126 |
| 高风险动作 | 5 |
| 中风险动作 | 27 |
| 低风险动作 | 94 |
| 最近 active 到 RAG 同步 | success |
| 最近同步数量 | 195 / 195 |

试运行判断：

```text
cleanup_before_trial
```

原因：仍有 5 条高风险治理项需要优先处理。RAG 同步本身正常。

## 4. 高风险项

本次高风险项均为：active 条目已被报价引用，但缺少至少一个业务可解释的来源价。

| issue_id | cost_item_id | 项目名称 | 单位 | 主参考价 | 报价引用次数 | 建议动作 |
|----------|--------------|----------|------|----------|--------------|----------|
| BIZ2T-0001 | 203 | 临时静音保护棉铺设 | ㎡ | 110.0 | 13 | 补齐至少一个业务可解释的来源价 |
| BIZ2T-0002 | 204 | 定制异形铝合金收口条安装 | ㎡ | 50.0 | 10 | 补齐至少一个业务可解释的来源价 |
| BIZ2T-0003 | 206 | 高空局部防尘围挡加固 | 处 | 100.0 | 2 | 补齐至少一个业务可解释的来源价 |
| BIZ2T-0004 | 207 | 高空局部防尘围挡加固 | 处 | 100.0 | 1 | 补齐至少一个业务可解释的来源价 |
| BIZ2T-0005 | 208 | 甲方指定品牌成品检修口更换 | 处 | 100.0 | 2 | 补齐至少一个业务可解释的来源价 |

处理建议：

1. 由成本部核对这 5 条的对甲价、劳务价或班组价来源。
2. 如果价格可信，补齐对应来源价和备注说明。
3. 如果价格不可信，撤回为 draft 或归档，避免继续作为试运行样例。
4. 处理完成后重新运行 BIZ-2t 脚本，确认高风险项下降到 0 或全部有人工说明。

高风险项交接材料：

- `docs/biz-2t-high-risk-cost-handoff.md`
- `reports/biz2t/20260528_current/cost_governance_high_risk_handoff.csv`

## 5. 中低风险治理方向

中风险主要用于试运行前复核：

- 同名多规格但部分规格不清。
- 单位需要人工确认。
- 高相似 active 条目需要确认是否为合理拆分。

低风险主要用于试运行观察：

- 同名不同规格的正常候选。
- 规格/备注可进一步补强但不直接阻断试运行。
- 相似 active 可作为后续成本库标准化优化项。

## 6. 成本部处理 SOP

1. 打开 `reports/biz2t/20260528_current/cost_governance_actions.xlsx`。
2. 先筛选 `risk_level=high`。
3. 对高风险条目逐条核价：
   - 价格可信：补齐业务来源价、备注或依据。
   - 价格不可信：撤回 draft 或归档。
4. 再筛选 `risk_level=medium`：
   - 补规格。
   - 统一单位口径。
   - 确认相似 active 是否合理共存。
5. 低风险项可在试运行中观察，不要求全部处理完。
6. 每处理一批后重新运行 BIZ-2t 脚本，生成新的治理清单。
7. 若 active 发生变化，由 `cost_approver` 判断是否需要重新同步 active 到 RAG。

## 7. 小范围试运行准入建议

建议满足以下条件后再进入 BIZ-2u 小范围内网试运行准备：

- 高风险项处理到 0，或每条都有成本部人工说明。
- 最近 active 到 RAG 同步成功。
- 成本部确认中风险项不会影响试运行样例。
- BIZ-2s 角色账号已配置好。
- 已准备 3-5 份试运行需求单。
- 已准备问题反馈表，用于记录成本命中、无底价、权限、草稿和报价结果问题。

## 8. 验证记录

已执行：

```text
C:\Users\12521\miniconda3\python.exe -m pytest tests/test_cost_governance_biz2t.py tests/test_cost_data_quality_biz2k.py
4 passed, 1 warning

C:\Users\12521\miniconda3\python.exe scripts\biz2t_cost_governance_pack.py --output-dir ..\reports\biz2t\20260528_current
status=ok
```

已知 warning：`.pytest_cache` 写入权限受限，不影响测试结果。
