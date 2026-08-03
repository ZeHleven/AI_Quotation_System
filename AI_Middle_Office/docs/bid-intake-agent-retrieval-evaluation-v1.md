# 报价资料研判 Agent 检索评测 v1

## 1. 这套评测解决什么问题

本评测用于回答一个可量化的问题：

> 当 Query 拆分、检索路由、文本切块、Embedding、多路召回或重排策略发生变化后，Agent 找到正确招标证据的能力是否真的提升？

它不会调用研判 LLM，先把检索链路与最终研判解耦，避免最终结论的波动掩盖检索问题。

当前评测边界：

```text
用户问题
  -> Query Planner
  -> exact / semantic / hybrid 路由
  -> 招标证据检索
  -> Top K 证据
  -> 与人工 Gold Evidence 比对
  -> 指标与回归样本
```

暂不包含：

- 最终“建议参与 / 不参与 / 补资料”的准确率；
- LLM 答案忠实度与完整性；
- 总经办政策评分校准。

这些能力应分别使用现有政策金标评测和后续端到端研判评测，不能用一组分数混在一起。

## 2. 已落地产物

| 产物 | 作用 |
|---|---|
| `app/agents/bid_intake/retrieval_evaluation.py` | 数据契约、质量门、指标、基线对比和安全报告 |
| `scripts/bid_intake_retrieval_eval.py` | local、database、predictions 三种评测入口 |
| `evals/bid_intake/retrieval/v1/public_demo.jsonl` | 7 条公开合成样本，用于验证框架和路由 |
| `evals/bid_intake/retrieval/v1/historical_case_template.json` | 真实历史项目标注模板 |
| `evals/bid_intake/retrieval/v1/experiment_template.json` | 单变量 A/B 实验记录模板 |
| `tests/test_bid_intake_retrieval_evaluation.py` | 数据质量、指标、隐私和回归测试 |

## 3. 样本怎么设计

一个评测样本代表“在某个项目中提出一个问题，并期待检索到哪些证据”。

核心字段：

- `eval_case_id`：评测问题唯一 ID；
- `case_id`：项目 ID，用于连接真实证据库；
- `question`：经过脱敏的业务问题；
- `expected_routing`：人工预期的 Query 数、检索类型和主题；
- `gold_evidence`：人工核验的正确证据 ID、相关性等级及关键短语；
- `expected_no_result`：资料中本来就没有答案时设为 `true`；
- `dataset_split`：`development`、`holdout` 或 `challenge`；
- `difficulty`：`easy / medium / hard`；
- `annotation_status`：`draft / reviewed / approved`；
- `privacy`：公开合成、私有脱敏或私有受限。

相关性等级：

- `3`：能够直接回答问题的核心证据；
- `2`：对答案有重要支撑；
- `1`：背景相关，但不能独立回答。

草稿允许暂时没有 Gold Evidence，方便先建立标注队列；正式评测要求全部样本为 `approved`，且标注人与复核人不能相同。

## 4. 数据集质量门

正式对比必须满足：

- 至少有一个样本；
- `eval_case_id` 不重复；
- 同一项目不能同时出现在 Development 和 Holdout，防止项目内容泄漏；
- 所有样本均由不同人员完成标注与复核并达到 `approved`。

作品集就绪的最低建议：

- 总样本不少于 30；
- Development 不少于 20；
- Holdout 不少于 10；
- 精确匹配、语义查询、混合查询、多 Query、跨块条款、表格内容、缺失资料和冲突资料均有覆盖。

30 条只是可表达的最低线。真实优化趋于稳定后，建议逐步增加到 50–100 条。

### Development 与 Holdout

- Development：用于分析错误、调整切块和路由；
- Holdout：实验完成前不查看逐条结果，只用于最终验证泛化；
- 必须按“项目”分组后切分，不能把同一招标项目的不同问题分到两边。

### Challenge

- Challenge用于Holdout已经完成后收到的全新项目，检查当前方案能否泛化；
- Challenge不参与当前Development调参，也不计入原30题作品集门槛；
- Challenge问题可以先保存为`draft`，但正式运行和对外报告前仍须独立复核为`approved`；
- 同一项目不能跨Development、Holdout和Challenge，防止内容泄漏；
- Challenge暴露的问题进入下一轮实验假设，不能回头修改已经冻结的Holdout结果。

## 5. 核心指标

| 指标 | 含义 |
|---|---|
| Hit@K | Top K 中是否至少命中一个正确证据 |
| Recall@K | 应找到的 Gold Evidence 有多少进入 Top K |
| Precision@K | Top K 结果中正确证据的占比 |
| MRR | 第一个正确证据排得是否足够靠前 |
| nDCG@K | 同时考虑排序位置和证据相关性等级 |
| 路由完全正确率 | Query 数和 exact / semantic / hybrid 数量是否与预期一致 |
| Query 数量准确率 | Query Planner 是否拆成预期数量 |
| 主题召回率 | 规划结果是否覆盖人工要求的检索主题 |
| 负样本准确率 | 资料没有答案时是否避免返回伪证据 |
| P95 延迟 | 慢请求的延迟水平 |
| 回归样本数 | 候选方案比基线变差的样本数量 |

不能只看平均 Recall。优化可能提升多数简单样本，却让“截止时间、资质硬门槛”等关键样本退化，所以报告必须保留逐条回归清单。

## 6. 如何运行

在 `AI_Middle_Office` 目录执行。

### 6.1 公开合成基线

```powershell
.\.venv-agent\Scripts\python.exe .\scripts\bid_intake_retrieval_eval.py `
  --backend local `
  --experiment-name public-demo-baseline-v1 `
  --change-note "建立Query拆分、路由与评测框架基线。"
```

该模式使用本地词法 fixture，只能证明评测框架、Query Planner 和路由契约可运行，不能证明真实 Milvus 向量检索或 BM25 的效果。

### 6.2 真实分层检索评测

```powershell
.\.venv-agent\Scripts\python.exe .\scripts\bid_intake_retrieval_eval.py `
  --backend database `
  --dataset .\evals\bid_intake\retrieval\v1\private_historical_v1.jsonl `
  --experiment-name real-retrieval-baseline-v1 `
  --change-note "记录当前切块、Embedding、路由与RRF的真实基线。"
```

`database` 模式读取现有 MySQL 证据元数据、MinIO 正文和 Milvus/BM25 检索服务，不调用研判 LLM。

### 6.3 与旧基线做 A/B 对比

```powershell
.\.venv-agent\Scripts\python.exe .\scripts\bid_intake_retrieval_eval.py `
  --backend database `
  --dataset .\evals\bid_intake\retrieval\v1\private_historical_v1.jsonl `
  --experiment-name overlap-120-v1 `
  --change-note "只将chunk overlap从0调整为120。" `
  --baseline-report .\outputs\bid_intake_retrieval_eval\<旧基线_report.json>
```

只有数据集指纹相同时才允许比较，防止通过更换简单样本制造“提升”。

### 6.4 重放已捕获结果

```powershell
.\.venv-agent\Scripts\python.exe .\scripts\bid_intake_retrieval_eval.py `
  --backend predictions `
  --dataset .\evals\bid_intake\retrieval\v1\private_historical_v1.jsonl `
  --predictions .\outputs\bid_intake_retrieval_eval\<结果_predictions.jsonl>
```

该方式适合复核指标代码和离线展示，不会重新请求检索服务。

## 7. 真实样本标注 SOP

1. 选择已经完成人工研判的历史招标项目，优先覆盖不同资料结构和项目类型。
2. 先按项目分配 Development / Holdout / Challenge；一旦确定，不随实验结果移动。
3. 总经办或熟悉招标资料的人提出真实问题。
4. 标注人从原文件中找出直接证据和支撑证据，记录证据 ID 与相关性。
5. 如果资料确实没有答案，标记 `expected_no_result=true`，不要强行指定相似段落。
6. 另一位复核人独立确认问题、Gold Evidence、相关性和预期路由。
7. 去掉甲方名称、联系方式、金额等不必要敏感信息后，才允许进入受控评测文件。
8. 全部达到 `approved` 后运行正式基线。

评测报告默认不输出问题正文、证据正文或答案关键短语，只输出 case ID、指标和错误类型。预测 JSONL 中的 Query Plan 会移除原始问题和子 Query 文本，只保留路由模式、原因码和数量等技术元数据；默认也不持久化证据摘录。只有在受控环境排查失败样本时，才应显式使用 `--include-excerpts`，生成的文件不得进入公开作品集。

## 8. 每次优化怎么记录

本 Agent 的完整问题发现、方案判断、试错和结果链路统一记录在
`docs/bid-intake-agent-development-notes.md`；可复现的逐实验参数与指标继续记录在
`evals/bid_intake/retrieval/v1/experiment_ledger_v1.md`。前者解释为什么做，后者证明实际发生了什么。

一次实验只改变一个主要变量，例如：

- chunk size：1200 -> 800；
- chunk overlap：0 -> 120；
- Embedding 模型；
- exact / semantic / hybrid 分类规则；
- BM25 与向量召回数量；
- RRF 参数；
- 是否增加 CrossEncoder Reranker。

实验记录至少包含：

- 假设；
- 数据集指纹；
- 唯一变化项；
- 固定变量；
- Recall@5、MRR、nDCG@5、路由准确率、P95 延迟；
- 改善和退化的 case；
- 是否进入下一版的结论。

除指标外，还必须记录：

- 实际业务问题和观察到的现象；
- 支持根因判断的证据；
- 考虑过但暂未选择的替代方案；
- 过拟合与跨项目泛化检查；
- 下一步优化方向及原因；
- 尚未实施的内容必须明确标记为候选假设。

建议先后顺序：

1. 建立真实基线；
2. 分析失败类型；
3. 只优化占比最高的一类问题；
4. 在 Development 上调参；
5. 用 Holdout 做一次最终验证；
6. 保存报告和结论，不根据 Holdout 反复调参。

## 9. 面试时如何表达

不要只说“我做了 RAG”。可以按以下结构表达：

> 我把招标研判 Agent 的检索链路拆成 Query Planning、路由、召回和排序四层，建立项目级隔离的 Development/Holdout 金标集。评测覆盖精确、语义、混合、多 Query 和无答案场景，用 Recall@5、MRR、nDCG、路由准确率和 P95 延迟衡量，并通过数据集指纹保证 A/B 可比。每次只改变一个变量，记录总体指标和逐条回归，避免凭 Demo 观感调参。

等真实评测完成后，再替换为实际数字，例如：

> 在不增加 LLM 调用的情况下，某项路由优化使 Holdout Recall@5 从 A 提升到 B，P95 延迟变化为 C，同时没有引入硬门槛样本回归。

在没有真实历史金标结果前，不应把公开合成样本的高分描述为生产效果。
