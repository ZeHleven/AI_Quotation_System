# 报价资料研判 Agent：自适应检索路由

## 目标

Query Planner 切分复合问题后，对每个原子 Query 使用确定性轻量分类器：

```text
原子 Query
  -> 识别精确标识信号和语义研判信号
  -> exact：直接标识匹配 + BM25
  -> semantic：Milvus 向量检索
  -> hybrid：向量 + BM25 + RRF
  -> 单通道无结果时，自动升级 hybrid
  -> 多 Query 结果再次融合
```

分类器不调用 LLM，不产生模型费用。

## 分类口径

- `exact`：事实查找、业务关键词、证据 ID、条款号、日期、金额、比例、
  工期、文件名或带引号短语。
- `semantic`：风险、影响、原因、建议、判断、评估、权衡、解读等研判意图。
- `hybrid`：同一 Query 同时含有精确标识和语义研判意图，例如
  “第 12.3 条延期责任对乙方有什么风险”。

普通业务关键词不被当作“精确标识符”，因此“付款风险”走语义检索，
“付款条件是什么”走词法检索。

## 安全兜底

`exact` 或 `semantic` 返回零条结果时，允许自动补跑一次 `hybrid`。这是召回
安全网，不改变“混合意图才直接走 hybrid”的主路由原则。

## 可审计输出

MCP Observation 为每个 Query 保留：

- `requested_mode`、`executed_mode`
- `confidence`
- `exact_signals`、`semantic_signals`
- `reason_codes`
- `fallback_triggered`
- `result_count`

运行图谱只展示这些决策摘要，不展示模型私有思维链或招标资料长原文。

## 技术边界

- Agent Tool 名称和参数不变；
- 不新增 LLM 调用；
- 不改变 Milvus schema，不需要重新建立向量索引；
- 不新增数据库表或 Alembic；
- 不连接报价系统；
- RAG 服务只按请求执行被选择的检索通道，避免每次都计算查询向量。

## 当前环境验收

- Agent/Tender 聚焦回归：`70 passed`；
- CentOS 直接通道：
  - exact：`vector_count=0 / bm25_count=20`；
  - semantic：`vector_count=20 / bm25_count=0`；
  - hybrid：`vector_count=20 / bm25_count=20 / fusion=rrf`；
- 东莞香港中心真实索引：1869 块；
- 普通复合问题：`1 semantic + 3 exact`，返回 5 条证据；
- “第 12.3 条 + 风险”混合问题：`1 hybrid + 2 exact`，返回 5 条证据；
- MCP 与 Agent Worker 已重启，运行时 readiness 无 blocker。
