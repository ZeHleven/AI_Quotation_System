# QuoteEval Agent Pilot Phase 2 进展复盘

日期：2026-06-09

## 当前结论

Phase 2 已推进到 2b 前置状态：

- 2a 已完成：`agent_pilot` 是 `text_v2 + trace harness`，用于证明 Agent 接入、trace 和评测框架无回退。
- 2b 骨架已完成：`agent_llm` 是 cache-only adapter，默认只读 `eval/fixtures/llm_cache`，不会在评测时调用外部模型。
- 2b 真实对比报告暂未完成：缺少正式 LLM cache，且本轮外部网络刷新审批超时。

## 已完成

### 1. 判别力数据集

QuoteEval 数据集已扩展到 24 条 case。新增 6 条专门用于暴露 `text_v2` 短板的困难样本：

- `case_019_text_multi_spec_each_quantity`
- `case_020_text_range_quantity_clarification`
- `case_021_text_included_items_split`
- `case_022_text_waterproof_range_omission`
- `case_023_text_missing_unit_number`
- `case_024_text_inherited_item_name_multi_segment`

这些样本覆盖多规格压缩写法、范围工程量、包含项拆分、缺单位、继承项目名、漏项与澄清判断。

### 2. 规则基线重新确认

24 条 case 上，`text_v2` 的当前指标为：

| Metric | Value |
| --- | ---: |
| standard_row_accuracy | 0.7500 |
| matched_row_rate | 0.8681 |
| clarification_required_recall | 0.4000 |
| cost_match_type_accuracy | 0.7963 |
| query_ref_hit_accuracy | 0.8796 |

这说明当前规则基线已经有明确失败空间，后续 `agent_llm` 不会陷入“没有 delta 可证明”的尴尬。

### 3. 2a Harness 验证

`text_v2 -> agent_pilot` 的 24 条 case 对比为全指标 `0 delta`。

这符合 2a 定位：`agent_pilot` 不宣称比规则更强，只证明 trace harness 不引入回退。

### 4. 2b Cache-only Adapter

已新增：

- `eval/refresh_agent_llm_cache.py`
- `eval/fixtures/llm_cache/README.md`
- `eval/fixtures/llm_cache/case_llm_cached_multi_spec.json`

`agent_llm` 的行为：

- 有 cache：读取缓存并进入 QuoteEval 同一套指标。
- 缺 cache：失败并提示缺少对应 `eval/fixtures/llm_cache/<case_id>.json`。
- 不会自动调用模型。
- 不会回退到规则结果。

这保证了 QuoteEval 的可复现合同：同 commit、同 case、同 cache、同成本快照，报告可重跑。

## 当前阻塞

2b 的真实 LLM cache 尚未生成。

阻塞原因：

- 已两次请求使用现有 `DEEPSEEK_API_KEY` 调用 DeepSeek 刷新 6 条困难 case 的 cache。
- 两次审批均超时。
- 因此未发生外部模型调用，也未生成正式 LLM cache。

记录文件：

- `eval/reports/quoteeval_2b_issue_log_20260609.md`

## 已生成报告

- `eval/reports/baseline_text_v2_20260609_225857.md`
- `eval/reports/baseline_agent_pilot_20260609_230159.md`
- `eval/reports/compare_baseline_baseline_20260609_230159_vs_baseline_text_v2_20260609_225857.md`
- `eval/reports/compare_baseline_text_v2_20260609_225857_vs_baseline_agent_pilot_20260609_230159.md`

## 下一步仍按原计划

不新增方向。解除网络/API 审批阻塞后，继续：

1. 用 `eval/refresh_agent_llm_cache.py` 为 6 条困难 case 生成正式 LLM cache。
2. 跑 `agent_llm` 子集报告。
3. 跑 `text_v2 -> agent_llm` 对比报告。
4. 判断 LLM 是否真的提升复杂拆行、澄清召回和 query 命中。

