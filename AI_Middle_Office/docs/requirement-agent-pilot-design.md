# Requirement Agent Pilot 设计

日期：2026-06-10

## 定位

Requirement Agent Pilot 是 QuoteEval V1 之后的第二阶段：只在“需求进入报价系统之前”的高方差理解环节旁路试验 Agent 能力。它不替换报价系统，不接管价格、成本库状态、占位阻断、审计、推送或任务状态机。

第一刀先落在 `eval`，但必须把“规则增强”和“Agent 推理”拆开归因：

- 2a：确定性 harness。新增 `text_v2` 规则 adapter 和 `agent_pilot` trace harness，用来验证 adapter 接口、trace 结构、指标和报告能跑通。2a 的预期结果是 `agent_pilot` 与 `text_v2` 持平；这只能证明 harness 可用，不能证明 Agent 已产生业务增量。
- 2b：LLM-in-the-loop。新增可复现的 LLM adapter，让模型在受控边界内直接提出行拆分、规格抽取和澄清判断。只有 2b 相比 `text_v2` 的提升，才算 Agent 推理增量。

只有当离线报告证明 2b 收益稳定后，才考虑把 `app/services/requirement_agent.py` 接到真实链路的 shadow 入口。

## 目标

- 改进脏自由文本和甲方需求单的前端理解。
- 识别同名不同规格、数量/单位粘连、连接词变体、疑似合并行和疑似遗漏。
- 为成本库和 RAG 生成更好的检索 query。
- 在不确定时生成澄清问题，而不是猜价格、猜状态或自动下发。
- 用 QuoteEval 报告区分“更好的规则”和“Agent 推理”各自带来的增量。

## 严格边界

Agent Pilot 可以做：

- 拆行和字段理解：`item_name`、`spec`、`quantity`、`unit`、`remark`。
- 歧义识别：缺单位、缺数量、范围值、多工程量候选、同名多规格。
- 成本/RAG 检索 query 生成。
- 漏项候选提示和人工澄清问题生成。
- 输出 trace：plan、tool_calls、reflection，便于复盘。

Agent Pilot 绝对不做：

- 不计算价格、合计或利润。
- 不决定 `cost_items.active/draft/archived` 资格。
- 不启用、归档、沉淀或同步成本库。
- 不绕过占位阻断、人工确认或 `/confirm_push`。
- 不写数据库，不新增 Alembic。
- 不调用 N8N、Dify、DeepSeek、GLM、RAG 同步或钉钉推送。

## 阶段拆分

### 2a：确定性 Harness

2a 只证明三件事：

- QuoteEval 能跑第三类 adapter。
- Agent trace 能记录 `plan/tool_calls/reflection`。
- 现有规则工具可以被统一编排成同一份评测输出。

2a 不证明 Agent 价值。若 `agent_pilot` 与 `text_v2` 指标一致，这是预期结果。

```text
baseline  -> 当前确定性解析和匹配
text_v1   -> 已落地的自由文本规格尾巴归一化
text_v2   -> 更完整的纯规则需求理解层：规格/数量归一化、澄清问题、检索 query
agent_pilot -> text_v2 输出 + plan/tool_calls/reflection trace
```

### 2b：LLM-in-the-loop

2b 才是 Agent 能力测试。LLM 被允许在严格边界内直接产出或改写：

- 行拆分结果。
- `item_name/spec/quantity/unit/remark`。
- 是否需要澄清与澄清问题。
- 成本/RAG 检索 query。

LLM 不允许产出或改写：

- 单价、合计、利润。
- 成本库 `active/draft/archived` 状态。
- 占位阻断、审计、推送、任务状态机。

LLM 结果必须通过 schema 校验、字段归一化和 QuoteEval 指标；接入真实系统时只能先 shadow，不直接覆盖现有报价链路。

## 当前落地形态

本阶段只新增离线评测 adapter：

```text
QuoteEval case
  -> text_v2 / agent_pilot adapter
  -> requirement understanding rows
  -> cost snapshot matching
  -> omission detection
  -> integrity summary
  -> QuoteEval report
```

`text_v2` 和 `agent_pilot` adapter 与现有 adapter 输出同一个结构：

```python
{
    "case_id": "...",
    "actual_rows": [...],
    "actual_cost_hits": [...],
    "actual_cost_context_refs": [],
    "actual_omissions": [...],
    "actual_integrity": {...},
    "debug": {
        "adapter": "agent_pilot",
        "agent_trace": {
            "plan": [...],
            "tool_calls": [...],
            "reflection": {...}
        }
    }
}
```

因此对比只比较 adapter 名称，不改主指标层：

```powershell
C:\Users\12521\miniconda3\python.exe eval\run_quote_eval.py --adapter baseline --write-report
C:\Users\12521\miniconda3\python.exe eval\run_quote_eval.py --adapter text_v1 --write-report
C:\Users\12521\miniconda3\python.exe eval\run_quote_eval.py --adapter text_v2 --write-report
C:\Users\12521\miniconda3\python.exe eval\run_quote_eval.py --adapter agent_pilot --write-report
```

## Pilot Loop

2a loop 是受控的 deterministic shell，后续 2b 才接入 LLM / LangGraph：

```text
plan
  -> tool: requirement_text_parser / requirement_standardizer
  -> tool: spec_quantity_normalizer
  -> tool: clarification_question_builder
  -> tool: cost_query_builder
  -> tool: cost_snapshot_matcher
  -> tool: omission_detector
reflect
```

说明：

- `plan` 固定列出本次要检查的理解风险。
- `tool_calls` 只记录只读工具调用结果，不写业务表。
- `reflect` 给出 `safe_to_forward` / `needs_human_clarification`，但在本阶段只作为评测 debug，不阻断真实流程。
- 2a 的 `agent_pilot` 不声称比 `text_v2` 更聪明；它只证明 trace harness 存在。

## 工具映射

| Agent 工具 | 当前复用模块 | 用途 |
| --- | --- | --- |
| `requirement_standardizer` | `app/services/requirement_standardizer.py` | Excel 标准化、排除行、警告码 |
| `requirement_text_parser` | 2a 临时复用 `app/services/quote_cost_context.py::_candidate_rows`；2b 前应抽成公开 helper | 自由文本候选行拆分 |
| `spec_quantity_normalizer` | `text_v2` 纯规则 adapter | 规格尾巴拆出、数量单位粘连修正 |
| `clarification_question_builder` | `text_v2` 纯规则 adapter；2b 由 LLM 产出候选后再校验 | 缺数量、缺单位、范围值等澄清问题 |
| `cost_query_builder` | `text_v2` 纯规则 adapter；2b 由 LLM 产出候选后再校验 | 为成本库/RAG 生成检索 query |
| `cost_snapshot_matcher` | `app/services/quote_cost_matching.py` | 离线成本命中评测 |
| `omission_detector` | `app/services/quote_omission_detection.py` | 疑似漏项提示 |
| `integrity_checker` | `app/services/quote_review.py` | 确认清单完整性摘要 |

## 真实链路接入条件

只有当离线 QuoteEval 报告满足以下条件，才进入真实系统 shadow：

- 同一批黄金集上，`agent_pilot` 不低于 `text_v2`，用于证明 trace harness 无回退。
- 对 `agent_target` 难例，2b LLM adapter 相比 `text_v2` 有明确 delta。
- 无新增 `unexpected_warning`、`unexpected_omission`、`extra_row_count` 明显回退。
- trace 能解释每个改进来自哪个工具步骤。

真实链路接入仍然只做旁路：

```text
FEATURE_REQUIREMENT_AGENT=false 默认关闭

关闭：现有确定性流水线
开启 shadow：现有确定性流水线照常输出 + Agent 旁路输出只记录对比
开启 controlled：仅需求理解前端可选采用 Agent 输出，后端确定性规则不变
```

## 后续文件规划

本阶段已做：

- `eval` 新增 `agent_pilot` adapter。
- QuoteEval 可跑 `baseline/text_v1/text_v2/agent_pilot`。

后续真实链路再做：

- `app/services/requirement_agent.py`
- `FEATURE_REQUIREMENT_AGENT`
- `requirement_agent_runs` 或复用现有 `agent_runs` 记录 shadow trace
- 只读 API：查看 Agent 输出与规则输出差异

## 新增指标

Agent Pilot 的目标必须进入记分牌，否则报告无法证明目标是否达成。

### 澄清问题指标

黄金集新增可选字段：

```json
{
  "expected_clarification": {
    "required": true,
    "reason_codes": ["missing_quantity"]
  }
}
```

指标：

- `clarification_accuracy`：标注样本上，是否正确决定该不该问。
- `clarification_required_recall`：该问澄清的样本中，是否实际生成问题。
- `clarification_false_positive_count`：不该问的样本中，是否误问。

### Query 质量指标

adapter 需要填充 `actual_cost_context_refs`，至少包含：

```python
{
    "row": 0,
    "query": "墙面乳胶漆 两遍 ㎡",
    "cost_item_id": 105,
    "match_family": "exact"
}
```

指标：

- `query_ref_hit_accuracy`：query 产生的成本命中是否对齐黄金集。
- `query_ref_rate`：应有 query 的行是否实际生成 query。

2a 的 query 质量主要归因于 `text_v2` 规则；2b 若提升，才归因于 LLM 推理。

## 2b 可复现策略

LLM 一接入就会破坏 QuoteEval V1 的“同 commit + 同快照 -> 可复跑”合同，因此 2b 必须满足：

- `temperature=0`。
- 固定 `model_id`、prompt 版本和工具 schema 版本。
- LLM 响应按 `case_id + input_hash + model_id + prompt_version + tool_schema_version` 写入 fixture cache。
- 默认重跑先读 cache；只有显式 `--refresh-llm-cache` 才重新请求模型。
- 报告中记录 cache 命中率、模型、prompt 版本和未缓存样本。

如需评估模型漂移，另开稳定性报告，对每个 case 跑 N 次并报告方差，不混入主 baseline。

## Trace 质量标准

每条 trace 至少包含：

- `plan`：本次要检查的理解风险。
- `tool_calls[].tool`：工具名。
- `tool_calls[].status`：`ok/skipped/error`。
- `tool_calls[].input_summary`：不含敏感原文的输入摘要或哈希。
- `tool_calls[].output_summary`：行数、命中数、澄清数、漏项数等。
- `tool_calls[].decision`：这步带来的字段变化或判断。
- `reflection.status`：`safe_to_forward` / `needs_human_clarification`。
- `reflection.blocked_actions`：明确列出禁止动作。

trace 是面试展示材料，不能只是“跑过工具”的流水账。

## 范围切分

现在做：

- `text_v2` 规则归因 adapter。
- `agent_pilot` 2a trace harness。
- 澄清问题和 query 质量指标。
- A/B/C 报告：`baseline -> text_v1 -> text_v2 -> agent_pilot`。

推迟做：

- 真实 `app/services/requirement_agent.py`。
- `FEATURE_REQUIREMENT_AGENT`。
- 数据库 agent run 表或复用现有 agent 表。
- 真实报价链路 shadow API。
- 2b LLM adapter、LLM cache、模型成本统计。

## 模型与成本占位

2b 才需要选模型。候选策略：

- 首选复用现有 DeepSeek/Dify 通道，降低新增依赖。
- 若使用外部 API，必须在报告记录模型名、单 case token、单轮成本和 40 条 case 总成本估算。
- 评测成本超过可接受范围时，先跑 `agent_target` 子集，再跑全量。

## 面试叙事口径

这不是“把报价系统 agent 化”，而是“用受控 Agent 改进需求理解前端”。确定性后端继续由规则保证安全、审计和回退；Agent 只负责高方差理解。

更严格地说：2a 只证明 harness，2b 才证明 Agent 推理；面试叙事必须用 `text_v2 -> LLM adapter` 的 delta 证明价值，不能用 `agent_pilot` 2a 的 trace 包装成收益。
