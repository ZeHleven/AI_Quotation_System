# 报价资料研判 Agent：复合 Query 切分

## 当前实现

招标证据 MCP 在调用 repository 前增加确定性的轻量 Query Planner：

```text
Agent 原始 Query
  -> 识别招标研判主题或独立问句
  -> 保留原始 Query
  -> 最多生成 5 个原子 Query（总数最多 6）
  -> 每个 Query 先由轻量分类器选择词法、向量或混合检索
  -> MCP 对各 Query 结果再次做 RRF 融合
  -> 优先保留各原子 Query 的证据覆盖
  -> 去重后返回 Agent
```

不增加一次 LLM 调用。短查询或只涉及一个主题的查询保持一次检索，不会为了
形式而拆分。

## 可识别主题

首版覆盖：

- 资质、业绩和关键人员；
- 投标截止与开标；
- 付款、结算、审计和回款；
- 工期、进度节点和延期责任；
- 投标/履约保证金和担保；
- 评标、评分和否决投标；
- 报价范围、控制价和计价方式；
- 合同、违约、签证和索赔；
- 工程量清单、图纸和技术资料；
- 招标范围、承包内容和分包边界；
- 项目地点、现场条件和踏勘；
- 质量、验收和保修。

无法识别多个业务主题、但存在多个独立问句时，按标点切分；其他情况保持
原 Query。

## MCP Observation

`search_tender_evidence` 返回中新增：

- `query_plan.schema_version=tender-query-plan/v1`
- `strategy`
- `queries`
- `atomic_queries`
- `topics`
- `query_count`
- `query_tasks`（每个查询的分类、置信度、原因、实际执行通道）
- `routing_summary`（词法/语义/混合调用计数与兜底次数）
- 每条证据的 `matched_queries`
- 每条证据的 `query_rrf_score`

运行图谱的 Observation 会显示 Query 拆分数量和脱敏后的 Query Plan，不保存
资料长原文或模型私有思维链。

## 兼容边界

- MCP Tool 名称和输入参数不变；
- 单 Query 结果顺序保持 repository 原排序；
- 同时覆盖 Milvus 混合检索与数据库词法降级；
- 不修改 Milvus schema，不需要重建向量；
- 不新增数据库表或 Alembic；
- 不接报价系统。

## 当前环境验收

自动化：

- Query Planner、运行图谱、MCP、证据库和混合检索联合回归：
  `58 passed`。

真实资料：

- 项目：东莞香港中心装修专业分包工程；
- 当前索引：1869 块；
- 原始 Query：`项目的付款条件、工期风险和投标保证金分别是什么？`
- 拆分结果：1 个原始 Query + 3 个原子 Query；
- 主题：`payment / schedule / bond`；
- 返回证据：5 条；
- 多 Query 检索与 MinIO 权威正文回读总耗时约 5.5 秒。
