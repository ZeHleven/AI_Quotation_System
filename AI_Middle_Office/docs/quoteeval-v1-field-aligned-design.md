# QuoteEval V1 字段对齐设计

日期：2026-06-10

## 目标

QuoteEval V1 的目标是把“报价漏斗前端理解层是否变好”变成可复跑、可对比、有 ground truth 的分数表。V1 不评测 AI 出价本身，不触发 N8N、Dify、DeepSeek、GLM、RAG 同步、钉钉推送或成本库沉淀。

首版只覆盖确定性链路：

```text
需求标准化解析 -> 成本库匹配 -> 成本前置参考 -> 漏项检测 -> 确认清单完整性判定
```

这也是后续受控 Agent Pilot 的改造边界：Agent 只尝试改进高方差的需求理解、拆行、澄清、分批规划和检索 query 生成；金额计算、active/draft 规则、占位阻断、审计和最终下发仍由确定性规则控制。

## 已读代码入口

本设计按当前真实字段对齐，而不是按草案字段猜测。

| 模块 | 代码入口 | QuoteEval 用途 |
| --- | --- | --- |
| 需求标准化 | `app/services/requirement_standardizer.py:193` `standardize_requirement_excel_bytes` | Excel 标准化输出、`rows`、`summary`、`issues` |
| 行确认 | `app/services/requirement_standardizer.py:386` `confirm_standardized_rows` | 已确认行与阻断行口径 |
| 标准化行结构 | `app/services/requirement_standardizer.py:718` `_standardize_row`、`:810` `_base_row`、`:1277` `_clean_confirmed_row` | `row_type`、`warnings`、`quantity_candidates`、`requires_confirmation` |
| 成本匹配 | `app/services/quote_cost_matching.py:1050` `match_quote_row_cost_reference` | 单行成本命中评测 |
| 成本引用结构 | `app/services/quote_cost_matching.py:575` `_cost_item_reference`、`:620` `_no_match_reference`、`:1007` `_reference_summary` | `cost_reference` 字段对齐 |
| 漏项检测 | `app/services/quote_omission_detection.py:142` `detect_quote_omissions` | `omission_summary`、`omission_suggestions` |
| 确认需求行 | `app/models/quote_requirement_row.py:12` `QuoteRequirementRow` | 完整性判定的标准需求行模型 |
| 完整性摘要 | `app/services/quote_review.py:280` `attach_requirement_integrity_summary` | `requirement_integrity` 字段和状态枚举 |
| 预审合并与占位 | `app/services/quote_review.py:200` `merge_requirement_preview_rows`、`:781` `_build_requirement_placeholder_row` | 缺失行占位生成 |
| 对账匹配 | `app/services/quote_review.py:660` `_match_requirement_to_preview`、`:687` `_requirement_reconciliation_rows` | requirement vs preview 行对齐 |

## V1 不做什么

- 不评测模型最终报价是否“价格正确”。
- 不在 CI 中连接生产数据库。
- 不把 online business metrics 作为 V1 必交付。
- 不引入 LLM-as-judge。
- 不新增数据库结构，不新增 Alembic。
- 不改报价链路、价格口径、RAG 同步或成本库 active 规则。

## 目录建议

```text
eval/
├── quote_eval_dataset/
│   ├── text/
│   │   └── case_001.json
│   ├── excel/
│   │   ├── case_021.json
│   │   └── fixtures/
│   └── README.md
├── fixtures/
│   └── cost_snapshot.json
├── run_quote_eval.py
├── reports/
│   ├── baseline_YYYYMMDD.md
│   └── compare_<A>_vs_<B>.md
└── lib/
    ├── adapters.py
    ├── schema.py
    ├── matching.py
    └── metrics.py
```

`eval/` 放仓库根目录，作为跨服务评测资产。`AI_Middle_Office/docs/` 只放设计与报告说明。

## 统一 Adapter 输出

不要让指标直接依赖现有服务的临时返回结构。V1 需要先定义 adapter，把当前流水线和未来 Agent Pilot 都转换成同一个评测输出。

```python
{
    "case_id": "case_017",
    "actual_rows": [...],
    "actual_cost_hits": [...],
    "actual_cost_context_refs": [...],
    "actual_omissions": [...],
    "actual_integrity": {...},
    "debug": {...},
}
```

这样后续 A/B 只需要比较：

```text
baseline pipeline -> adapter -> normalized output
agent pilot       -> adapter -> normalized output
```

## 标准化行真实字段

`requirement_standardizer` 当前标准化行包含以下关键字段：

| 字段 | 来源 | V1 评测用途 |
| --- | --- | --- |
| `source_sheet` | `_base_row` | Excel 溯源与分 Sheet 指标 |
| `raw_row_index` | `_base_row` | 原始行号追溯 |
| `row_type` | `_standardize_row` | data/header/summary/note/section/ambiguous 等行分类 |
| `item_name` | `_standardize_row` | 标准行名称准确率 |
| `spec` | `_standardize_row` | 同名不同规格拆行、规格保留 |
| `quantity` | `_standardize_row` / `_clean_confirmed_row` | 工程量识别 |
| `quantity_source` | `_standardize_row` | 数量来源解释 |
| `quantity_candidates` | `_standardize_row` | 多工程量候选评测 |
| `unit` | `_standardize_row` / `_clean_confirmed_row` | 单位识别 |
| `unit_raw` | `_standardize_row` | 单位归一化前值 |
| `unit_family` | `_standardize_row` | 单位族兼容 |
| `remark` | `_standardize_row` | 备注/项目特征保留 |
| `location` / `work_area` | `_standardize_row` | 区域/部位字段保留 |
| `raw_fields` | `_base_row` | 原始列值追溯 |
| `raw_cells` | `_base_row` | 原始单元格追溯 |
| `raw_text` | `_base_row` | badcase 展示 |
| `confidence` | `_standardize_row` | 低置信度样本分层 |
| `warnings` | `_standardize_row` | 警告码召回/误报 |
| `requires_confirmation` | `_standardize_row` | 人工确认阻断口径 |
| `normalized_name` | `_standardize_row` | 行对齐辅助 |

V1 不只评 `data_row`，还要评“哪些行不该进入报价”。因此黄金集必须支持 `expected_excluded_rows` 和 `expected_warnings`。

## 黄金集样本 schema

每条样本一个 JSON。字段名与现有代码对齐，评测层再额外补充 expected 字段。

```json
{
  "id": "case_017",
  "input_type": "text",
  "raw_input": "石膏板吊顶 9.5mm 8㎡、石膏板吊顶 12mm 8㎡",
  "excel_fixture": null,
  "difficulty": ["same_name_diff_spec", "quantity_trap"],
  "source_note": "真实 badcase 脱敏：9.5mm 曾被误读为工程量",
  "frozen": true,
  "cost_snapshot_hash": "sha256:...",

  "expected_rows": [
    {
      "source_sheet": null,
      "raw_row_index": null,
      "row_type": "data_row",
      "item_name": "石膏板吊顶",
      "spec": "9.5mm",
      "quantity": 8,
      "unit": "㎡",
      "remark": "",
      "warnings": []
    },
    {
      "source_sheet": null,
      "raw_row_index": null,
      "row_type": "data_row",
      "item_name": "石膏板吊顶",
      "spec": "12mm",
      "quantity": 8,
      "unit": "㎡",
      "remark": "",
      "warnings": []
    }
  ],

  "expected_excluded_rows": [],
  "expected_warnings": [],
  "expected_cost_hits": [
    {
      "row": 0,
      "cost_item_id": 35,
      "expected_match_family": "exact",
      "allowed_raw_match_types": ["exact_item_spec", "source_row_locked"]
    },
    {
      "row": 1,
      "cost_item_id": 36,
      "expected_match_family": "exact",
      "allowed_raw_match_types": ["exact_item_spec", "source_row_locked"]
    }
  ],
  "expected_omissions": [],
  "expected_integrity": {
    "status": "complete",
    "missing_count": 0,
    "placeholder_count": 0
  }
}
```

说明：

- `cost_snapshot_hash` 必填，保证成本命中评测可复现。
- `expected_match_family` 是评测简化枚举：`exact`、`fuzzy`、`locked`、`none`。
- `allowed_raw_match_types` 保留真实代码枚举：`exact_item_spec`、`fuzzy_item_name`、`source_row_locked`、`manual_selected` 等。
- `expected_excluded_rows` 用来评 summary/note/section/blank/header 行是否被正确排除。
- `expected_warnings` 用来评警告码，不把警告召回混进标准化行准确率。

## 成本库快照

V1 离线评测必须默认使用 `eval/fixtures/cost_snapshot.json`，不能默认连接生产库。快照字段只保留评测必要信息：

```json
{
  "snapshot_id": "cost-active-20260610",
  "snapshot_hash": "sha256:...",
  "source": "cost_items.active",
  "active_count": 195,
  "items": [
    {
      "id": 35,
      "item_name": "石膏板吊顶",
      "spec": "9.5mm",
      "unit": "㎡",
      "category": "...",
      "subcategory": "...",
      "price_type": "...",
      "price": 88.0,
      "client_tax_excluded_price": 88.0,
      "subcontract_composite_price": null,
      "crew_benchmark_price": null,
      "notes": ""
    }
  ]
}
```

生成快照时必须脱敏，不保留客户专属定价备注、联系人、项目名、合同信息或任何密钥。

## 成本匹配字段

当前 `cost_reference` 的关键字段：

| 字段 | 含义 |
| --- | --- |
| `matched` | 是否命中 active 成本项 |
| `match_type` | `exact_item_spec`、`fuzzy_item_name`、`source_row_locked` 等真实枚举 |
| `cost_item_id` | 命中的成本库主键 |
| `item_name` / `spec` / `unit` | 成本项快照 |
| `reference_price` | 主参考价 |
| `reference_price_source` | 主参考价来源字段 |
| `candidate_count` | 同名不同规格候选数量 |
| `alternative_cost_items` | 备选成本项快照 |
| `requires_manual_cost_candidate_confirmation` | 是否要求人工确认成本候选 |
| `fallback_applied` | 是否触发成本底价兜底 |
| `requires_manual_ai_rewrite_confirmation` | AI 改写成本依据风险 |
| `requires_manual_ai_note_confirmation` | AI 备注与成本依据冲突风险 |

QuoteEval V1 对成本命中的核心判分：

- 命中正确：`cost_item_id == expected.cost_item_id`。
- 精确度退化：expected 为 `exact`，但 raw `match_type` 不在 allowed exact 集合。
- 候选歧义：`requires_manual_cost_candidate_confirmation == true` 单独统计，不直接算错，除非 expected 明确要求无歧义。

## 漏项检测字段

`detect_quote_omissions` 返回：

```python
{
    "omission_summary": {
        "enabled": True,
        "rule_count": int,
        "suggestion_count": int,
        "high_confidence_count": int,
    },
    "omission_suggestions": [
        {
            "rule_id": "...",
            "severity": "notice",
            "confidence": 0.78,
            "trigger_row_no": 1,
            "trigger_item": "...",
            "suggested_item_name": "...",
            "suggested_label": "...",
            "cost_item_id": 123,
            "unit": "㎡",
            "reference_price": 10.0,
            "reason": "..."
        }
    ]
}
```

V1 以 `rule_id` 和 `cost_item_id` 作为主要判分锚点。`suggested_item_name` 只做报告展示，不作为唯一判定依据。

## 完整性判定字段

完整性摘要由 `attach_requirement_integrity_summary` 生成：

```python
{
    "required": True,
    "status": "complete | complete_with_placeholders | incomplete",
    "is_complete": bool,
    "requirement_row_count": int,
    "preview_row_count": int,
    "matched_count": int,
    "missing_count": int,
    "extra_count": int,
    "placeholder_count": int,
    "message": "..."
}
```

状态口径：

- `incomplete`：仍有确认需求行没有匹配到预审行，`missing_count > 0`。
- `complete_with_placeholders`：确认需求行都已被覆盖，但至少一行是系统生成占位，`placeholder_count > 0`。
- `complete`：确认需求行全部匹配，且没有占位行。

占位行真实标记：

```python
row.get("requirement_placeholder") or row.get("quote_source") == "requirement_placeholder"
```

占位行由 `_build_requirement_placeholder_row` 生成，关键字段包括：

- `requirement_row_key`
- `source_sheet`
- `raw_row_index`
- `project_name`
- `spec`
- `quantity`
- `unit`
- `unit_price = 0`
- `total_price = 0`
- `requirement_placeholder = True`
- `quote_source = "requirement_placeholder"`
- `cost_reference.matched = False`
- `cost_reference.match_type = "missing_ai_preview"`

QuoteEval V1 可以用现有匹配逻辑复用完整性判定，但要通过 adapter 构造 `QuoteRequirementRow` 等价对象，避免评测必须写数据库。

## V1 指标

| 指标 | 口径 |
| --- | --- |
| 标准化行准确率 | actual 与 expected 对齐后，`item_name/spec/quantity/unit` 全对的 expected 行占比 |
| 排除行准确率 | summary/note/section/header/blank 等非报价行是否没有进入 confirmed rows |
| 拆行正确率 | `same_name_diff_spec` 子集：期望 N 行是否拆成 N 行且规格不丢 |
| 工程量识别错误率 | 对齐行中 `quantity` 不一致的占比，重点看 `quantity_trap` |
| 警告码准确率 | `warnings` 的召回和误报，重点看 `MULTIPLE_NUMBERS`、`MULTIPLE_QUANTITY_CANDIDATES`、`LOW_CONFIDENCE` |
| 成本命中正确率 | 命中且 `cost_item_id` 正确的行占比 |
| 成本匹配类型正确率 | raw `match_type` 是否落在样本允许集合 |
| 漏项召回率 / 误报率 | expected `rule_id` 是否召回；easy/no_omission 子集是否误报 |
| 完整性判定准确率 | `status/missing_count/placeholder_count` 是否符合 expected |

行对齐需要先写单测。建议按 `normalized item_name + spec` 优先匹配，再用 `quantity/unit` 辅助打分；未匹配 expected 计漏，未匹配 actual 计多报。

## V1 报告

`run_quote_eval.py` 输出：

```text
eval/reports/baseline_YYYYMMDD.md
eval/reports/baseline_YYYYMMDD.json
```

Markdown 报告必须包含：

- 样本数、text/excel 分布、difficulty 分布。
- 成本快照 ID/hash。
- 被测 git sha。
- 总体指标表。
- 分 difficulty 指标表。
- Top Badcase：失败样本、失败指标、actual vs expected 摘要。
- 明确声明：V1 未评测最终 AI 出价，未使用 LLM-as-judge。

`compare_<A>_vs_<B>.md` 只比较离线黄金集结果，避免把在线业务波动误当作因果提升。

## 两周内最小验收

第一周：

1. 完成 `schema.py`、`adapters.py`、`matching.py`。
2. 固化 `cost_snapshot.json` 生成方式和脱敏规则。
3. 写行对齐单测、成本 match type 映射单测、完整性状态单测。
4. 标注 15-20 条样本，优先 `easy_clean`、`same_name_diff_spec`、`quantity_trap`。

第二周：

1. 样本补到 30-40 条即可，不追求 50 条。
2. 实现 V1 指标和分 difficulty 聚合。
3. 生成首版 baseline Markdown/JSON。
4. 自动列出 Top Badcase。
5. 不做 online business metrics，不做 LLM-as-judge。

## 封板线

QuoteEval V1 完成后，只允许围绕以下目标继续一轮：

- 用 baseline 找出最弱 difficulty。
- 为旁路 Agent Pilot 定义输入和输出 adapter。
- 用 compare 报告证明 Agent Pilot 是否真的提升。

除此之外，不继续把 QuoteEval 扩成新平台，不新增看板，不新增业务 CRUD。这个阶段的求职证据是“可复跑、有 ground truth、能对比版本的评测闭环”，不是又一个管理系统。

