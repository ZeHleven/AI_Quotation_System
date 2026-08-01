# RET-CANDIDATE-COVERAGE-005 — 谓词感知边际增益正式结果

## 结论

005按冻结纪律完成一次Baseline和一次Candidate，未重跑。正式结论为：

`rejected_candidate_pool_order_equivalence_and_gold_retention_gates`

谓词感知选择器自身的零promotion等价、正净收益、Top-1、辅助Query多样性、独占事实保护和调用成本不变量全部通过；但正式A/B仍有两个冻结门失败：

1. `HK-CENTER-DEV-Q009`候选池第9/10位发生互换，ID集合与Top-5相同、该题零promotion且质量不变，但候选池有序等价只有37/38；
2. Gold保留率提升7.381个百分点，低于预注册的10个百分点。

因此005不得接受为Development通过候选，也不得启用生产。不能因总体指标向好而降低门槛或重跑。

## 执行纪律

- 固定Development：4项目38题，指纹`c1b1dc434353aee5b8e19aed30be204c43e8385195f5d0fb94afb918bce81c87`；
- 4个项目Gold、active manifest和当前索引在冻结前全部通过；
- Baseline执行1次，Candidate执行1次；
- 没有Agent运行，没有重跑；
- 没有运行泰丰Holdout、蓝城Challenge或惠州Holdout；
- 没有新增检索、上下文读取、图调用、LLM或Memory；
- 生产候选覆盖开关保持关闭。

冻结与机器可读结果：

- `ret_candidate_coverage_005_fixed_development_freeze_v1.json`
- `ret_candidate_coverage_005_fixed_development_execution_v1.json`

## 总体指标

| 指标 | Baseline | Candidate | 变化 |
|---|---:|---:|---:|
| Hit@5 | 88.57% | 94.29% | +5.71pp |
| Recall@5 | 70.67% | 76.62% | +5.95pp |
| Precision@5 | 28.00% | 30.29% | +2.29pp |
| MRR | 79.29% | 80.90% | +1.62pp |
| nDCG@5 | 69.04% | 72.37% | +3.33pp |
| 候选池Recall | 87.14% | 87.14% | 0 |
| Gold保留率 | 80.05% | 87.43% | +7.38pp |
| 被Top-5淘汰Gold | 17 | 13 | -4 |
| 平均延迟 | 619ms | 521ms | -98ms |
| P95延迟 | 1541ms | 1172ms | -369ms |
| 搜索调用 / 错误 | 62 / 0 | 62 / 0 | 0 / 0 |

绝对指标、Recall增量、改善题数、无退化、排序指标、候选池Recall、淘汰Gold下降和成本门均通过。Gold保留率增量未达到10个百分点。

## 项目分布

| 项目 | Baseline Recall | Candidate Recall | 变化 | 淘汰Gold |
|---|---:|---:|---:|---:|
| 香港中心 | 63.33% | 78.33% | +15.00pp | 5→3 |
| 深圳丰隆 | 91.67% | 95.00% | +3.33pp | 1→0 |
| 总部基地 | 65.00% | 70.00% | +5.00pp | 6→5 |
| 陵水 | 59.83% | 59.83% | 0 | 5→5 |

3/4项目改善，且非起源项目总部基地改善，跨项目门通过。陵水没有收益。

## 选择器审计

- 8题输出变化，合计9次promotion；
- 每次promotion的净覆盖增量至少1，累计净增益10；
- 30道零promotion题全部与Baseline有序Top-5逐项一致；
- 没有无promotion变化；
- Top-1全部保持原位；
- 未替换位置全部不变；
- Query任务38/38一致；
- 搜索调用均为62；
- 上下文读取、图调用和新增LLM Token均为0；
- 4题Recall改善，4题质量中性promotion，没有Recall或排序退化；
- 每题最多2次promotion。

改善题：

- `HK-CENTER-DEV-Q003`
- `HK-CENTER-DEV-Q004`
- `SZ-FENGLONG-DEV-Q010`
- `FACT-COVERAGE-DEV-A-Q003`

质量中性promotion：

- `FACT-COVERAGE-DEV-A-Q002`
- `HK-CENTER-DEV-Q010`
- `RET-GRAPH-DEV-B-Q008`
- `RET-GRAPH-DEV-B-Q009`

## 候选池顺序不一致

`HK-CENTER-DEV-Q009`的候选池ID集合完全相同，但第9/10位互换：

- Baseline：第9位`EV-207a...`，第10位`EV-030d...`
- Candidate：第9位`EV-030d...`，第10位`EV-207a...`

该题因`unsupported_relation_shape`失败关闭，promotion为0，Top-5完全一致，质量不变。现象更像实时检索的低位并列顺序波动，而不是选择器修改候选池；但冻结契约明确要求有序候选池38/38一致，所以正式实现门仍判失败。

## 判断与下一步

005相较004显示了明显更强的关系感知选择收益：Recall达到预注册增量，改善扩展到3个项目且没有质量退化。但正式契约不能只看均值：

- 候选池顺序等价37/38，未达到100%；
- Gold保留率增量7.38pp，未达到10%。

保留代码、不启用生产、不重跑005。若继续，应建立独立新候选，先把候选池冻结或确定性回放后再比较最终选择器，从实验基础设施层消除实时低位排序波动；不得使用泰丰、蓝城或惠州数据调参。
