# BIZ-2k-1 成本库治理报告可读性与验收指引补强

> 日期：2026-05-28  
> 前置：BIZ-2k/BIZ-2t 已生成只读成本库治理报告；用户手工验收反馈为“能打开，但可读性差，不知道风险分类、只读边界和不影响报价在哪里验”。  
> 边界：只读生成报告，不连接数据库，不写数据库，不启动服务，不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。

## 1. 本阶段目标

BIZ-2k-1 只解决一个问题：让成本库治理报告能被业务人员直接验收。

本阶段不改变原 BIZ-2k/BIZ-2t 体检逻辑，只基于已有 raw JSON 生成业务可读版材料：

1. 中文字段名。
2. 风险拆分为高风险、中风险、低风险三个 Sheet。
3. Excel 长文本自动换行，列宽加大。
4. 明确“风险分类在哪里看”。
5. 明确“只读边界怎么验”。
6. 明确“不影响报价怎么验”。

## 2. 新增脚本

```text
AI_Middle_Office/scripts/biz2k1_cost_governance_readable_pack.py
```

默认输入：

```text
reports/biz2t/20260528_current/cost_governance_raw.json
```

默认输出：

```text
reports/biz2t/20260528_current/cost_governance_business_readable.md
reports/biz2t/20260528_current/cost_governance_business_readable.xlsx
```

运行方式：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2k1_cost_governance_readable_pack.py
```

该脚本只读取本地 JSON 文件，不查询数据库，因此不会新增、删除、改价、改状态或触发 RAG 同步。

## 3. 业务可读版 Excel 怎么看

打开：

```text
reports/biz2t/20260528_current/cost_governance_business_readable.xlsx
```

Sheet 说明：

| Sheet | 用途 |
|---|---|
| 验收说明 | 告诉验收人风险分类在哪里看、只读边界怎么验、不影响报价怎么验 |
| 总览 | 成本条目总数、active、archived、draft、风险数量、RAG 同步状态 |
| 高风险 | 试运行前必须处理或说明的项目 |
| 中风险 | 试运行前建议复核的项目 |
| 低风险 | 可在试运行中观察的项目 |
| 全部明细 | 所有治理动作明细 |

## 4. 当前业务结论

根据原始治理包：

| 指标 | 当前值 |
|---|---:|
| 成本条目总数 | 208 |
| active | 195 |
| archived | 13 |
| draft | 0 |
| 治理动作总数 | 126 |
| 高风险 | 5 |
| 中风险 | 27 |
| 低风险 | 94 |
| 最近 RAG 同步 | success，195 / 195 |

5 条高风险均为：

```text
active 条目已有主参考价，但缺少对甲价、劳务价或班组价等业务可解释来源价，且已经被报价引用。
```

BIZ-2t-2 已按用户给出的结论将 5 条高风险登记为 `accepted_risk`，理由为：

```text
临时试运行允许作为已知风险，后续补供应商报价
```

## 5. 手工验收建议

建议重新验收 BIZ-2k 时按下面顺序：

1. 打开 `cost_governance_business_readable.xlsx`。
2. 先看 `验收说明` Sheet。
3. 再看 `总览` Sheet，确认总数和风险数量能看懂。
4. 打开 `高风险` Sheet，确认 5 条高风险的原因、建议处理和报价引用次数能看懂。
5. 打开 `中风险`、`低风险` Sheet，确认可以按风险分层查看。
6. 打开 `cost_governance_business_readable.md`，确认 Markdown 版说明能独立看懂。

通过标准：

- 业务人员能知道风险分类在哪里看。
- 业务人员能看懂每条风险为什么被提示。
- 业务人员能知道哪类风险必须处理或说明，哪类可观察。
- 业务人员能知道如何验证“只读边界”和“不影响报价”。

## 6. 不做事项

- 不写数据库。
- 不新增 Alembic。
- 不改成本库数据。
- 不改报价规则。
- 不改价格口径。
- 不触发 RAG 同步。
- 不改 N8N/Dify。
- 不新增页面。
- 不启动服务。

## 7. 验证记录

已生成业务可读版报告：

```text
status=ok
cost_governance_business_readable.md
cost_governance_business_readable.xlsx
```

已检查 Excel Sheet：

```text
['验收说明', '总览', '高风险', '中风险', '低风险', '全部明细']
高风险：5 条
中风险：27 条
低风险：94 条
全部明细：126 条
```

