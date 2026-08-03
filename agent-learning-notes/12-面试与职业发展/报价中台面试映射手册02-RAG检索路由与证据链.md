---
title: 报价中台面试映射手册 02：RAG 检索路由与证据链
category: 面试与职业发展
tags:
  - FDE
  - Agent 工程
  - RAG
  - 混合检索
  - 证据链
  - 评测
reviewed_at: 2026-07-29
status: 持续更新
---

# 报价中台面试映射手册 02：RAG 检索路由与证据链

## 使用方法

每道题只记一条主线：

```text
问题 → 路由与检索 → 证据校验 → 评测与边界
```

先讲 30 秒版本；面试官追问后，再补算法、数据隔离、降级和真实验证。

## 考前 2 分钟速记

| 问题 | 记忆句 |
|---|---|
| 为什么混合检索 | 向量找语义，BM25 找术语和编号，RRF 融合名次 |
| 为什么要路由 | 先拆 Query，再按 exact / semantic / hybrid 只跑需要的通道 |
| 谁是事实源 | MySQL 当前 active manifest 是事实源，Milvus 只是召回索引 |
| 怎样防幻觉 | 结论绑定 `EvidenceRef`，高风险结论必须读过上下文并通过证据门 |
| 怎样证明有效 | 用人工 Gold Evidence 测 Recall、MRR、nDCG、路由和 P95 |

---

## 先讲清：系统里不是只有一条“RAG 链”

| 业务链路 | 当前数据与执行方式 | 边界 |
|---|---|---|
| 统一报价 | 账户定额 → 唯一启用企业定额 → AI 估价 | 同步、异步报价均已关闭 RAG/Milvus 调用 |
| 预算项目正式计价 | 严格校验唯一 active 企业定额版本 | 结构化价格必须查数据库，不能用向量相似度定价 |
| 招标资料研判 Agent | MySQL 证据块 → Milvus/HNSW 向量召回 + BM25 关键词召回 → RRF 融合 | 与报价集合、报价价格源完全隔离 |
| 保留的成本 RAG 能力 | 优先同步 active 企业定额；无 active 版本时兼容旧 `cost_items.active` | 保留同步、评测和运维能力，但不在当前报价主链中 |

面试表达：

> 我没有把 RAG 用在所有地方。结构化价格由数据库和确定性规则负责；RAG 用于合同、招标文件等非结构化证据检索。这样可以避免把“相似内容”误当成“权威价格”。

---

## 卡片 1：为什么使用向量 + BM25 + RRF？

**一句话：** 向量检索擅长语义近似，BM25 擅长专业术语、条款号和文件名，RRF 用名次融合两种不可直接比较的分数。

### 项目映射

- 向量通道：`bce-embedding-base_v1`，768 维，COSINE，HNSW。
- 词法通道：BM25，中文使用 jieba，并补充连续双字 token。
- 检索文本：文档键、证据正文和解析关键词。
- 融合方式：`RRF(d) = Σ 1 / (k + rank(d))`，当前 `k=60`。
- 招标证据使用独立集合 `tender_evidence_blocks_v1`。

准确顺序是：

```text
Query
├─ Embedding → Milvus/HNSW/COSINE 向量召回
└─ BM25 关键词召回
→ RRF 融合
→ 可选 Reranker 精排
```

### 关键取舍

- 纯向量可能漏掉编号、日期、金额和罕见术语。
- 纯 BM25 不擅长同义表达、风险解释和语义改写。
- RRF 不依赖两路分数标度一致，稳定、简单，适合作为第一版融合方案。
- 当前没有上线 CrossEncoder Reranker，不能把“可选优化”说成“已经实现”。

### 30 秒回答

> 招标文件既有“第 12.3 条、保证金、日期”这类精确信息，也有“付款条件对乙方有什么风险”这类语义问题，所以我使用向量和 BM25 双路召回。两路原始分数不在同一尺度，我没有直接加权，而是用 RRF 按排名融合。这样能同时兼顾语义召回和精确术语召回，而且实现简单、容易解释和回归。

---

## 卡片 2：怎样做自适应检索路由？

**一句话：** Query Planner 先拆复合问题，确定性分类器再为每个原子 Query 选择 exact、semantic 或 hybrid。

### 路由规则

| 模式 | 适用问题 | 执行通道 |
|---|---|---|
| `exact` | 事实、条款号、日期、金额、比例、文件名 | 标识符匹配 + BM25 |
| `semantic` | 风险、原因、影响、建议、判断、解读 | Milvus 向量 |
| `hybrid` | 同时包含精确标识与语义研判 | 向量 + BM25 + RRF |

示例：

```text
“投标保证金是多少”              → exact
“付款条件对乙方有哪些风险”      → semantic
“第 12.3 条延期责任有什么风险”  → hybrid
```

### 安全兜底与审计

- 分类器不调用 LLM，不增加模型费用。
- `exact` 或 `semantic` 零结果时，只补跑一次 `hybrid`。
- 每个 Query 保留 `requested_mode`、`executed_mode`、置信度、命中信号、原因码、是否兜底和结果数。
- 运行图谱展示路由摘要，不展示模型私有思维链或资料长原文。

### 真实验证

- 聚焦回归：`70 passed`。
- 真实索引：东莞香港中心项目 1,869 个证据块。
- 直接通道验证：exact 只跑 BM25，semantic 只跑向量，hybrid 两路都跑并使用 RRF。
- 普通复合问题路由为 `1 semantic + 3 exact`；混合问题路由为 `1 hybrid + 2 exact`，均返回 5 条证据。

### 30 秒回答

> 我不会让每个问题都默认跑最贵的混合检索。系统先把复合问题拆成原子 Query，再用无 LLM 的确定性规则路由：事实和编号走 exact，风险分析走 semantic，两种意图同时存在才直接走 hybrid。单通道零结果时再补跑 hybrid，并记录请求路由、实际路由和兜底原因，因此成本、延迟和召回过程都可解释。

---

## 卡片 3：为什么说 Milvus 不是事实主库？

**一句话：** 向量库可能延迟、残缺或保留旧版本，所以它只负责召回；证据的有效性必须回到 MySQL 当前版本校验。

### 项目映射

```text
MySQL 当前 active evidence blocks
→ 事务内 outbox
→ 可重试索引 Worker
→ tender_evidence_blocks_v1
→ 混合召回
→ 回 MySQL 校验
→ Agent
```

- `bid_evidence_index_jobs` 记录 manifest、schema 版本、请求数、成功数、状态和尝试次数。
- 唯一键为 `manifest_id + index_schema_version`，同一快照重复执行保持幂等。
- 每次索引和检索都携带 `case_id + manifest_version + manifest_hash`。

### 两道确定性门

1. 当前 manifest 的索引任务必须 `completed`，且 `requested_block_count == indexed_block_count`。
2. 检索结果必须回 MySQL 命中同一项目的 active 文档，且 `block_id` 完全一致。

不满足时自动回退数据库关键词检索；跨项目 ID 和错误 `block_id` 直接丢弃。

### 关键取舍

- 多一次回表会增加少量延迟，但防止旧索引、半成品索引和跨项目数据进入 Agent。
- Milvus 故障时降低的是语义召回能力，不降低事实边界和证据门正确性。
- 报价集合 `enterprise_quotation_rag` 与招标证据集合不混用。

### 30 秒回答

> 我把 MySQL 当前 active manifest 定义为事实源，Milvus 只做可重建的召回索引。只有索引任务完整成功才允许查询，召回结果还必须回 MySQL 校验项目、文档状态和 block ID。索引未完成、超时或版本不一致时回退数据库关键词检索。这个设计牺牲一点延迟，换来版本一致性、租户隔离和可控降级。

---

## 卡片 4：怎样把“引用”升级为证据链？

**一句话：** 证据链不是答案末尾放几个链接，而是每个重要结论都绑定可校验、可定位、可追溯的证据对象。

### 证据对象

`EvidenceRef` 至少保存：

- `evidence_id`、`block_id`；
- `document_id`、`document_version`；
- 页码、Sheet、单元格或章节位置；
- `content_hash`；
- 是否已读取上下文；
- 必要的短摘录。

### 证据门

- 引用必须属于当前 manifest 的 active 文档，版本和内容哈希必须匹配。
- 高风险、严重风险结论必须有证据。
- 高风险证据必须完成上下文读取，不能只凭检索摘要下结论。
- 招标证据派生的政策因子也必须携带证据。
- 必要资料缺失时返回 `supplement_required`；证据可修复时先修复，超过预算后转人工审核。

### 关键取舍

- Agent 可以负责发现和解释，但不能自行放宽证据门。
- “没有证据”应输出缺资料或待人工确认，不能用语言流畅度掩盖不确定性。
- 结构化价格使用数据库证据；非结构化判断使用 RAG 证据，两类证据可信等级不同。

### 30 秒回答

> 我们的证据链做到结论级绑定。每个高风险 finding 都必须携带完整 EvidenceRef，包括文档版本、定位信息和内容哈希；证据门还会验证它是否属于当前 active manifest，以及 Agent 是否真正读取过上下文。缺少关键资料时系统返回补资料，证据无效则修复或转人工，而不是让模型继续生成一个看似合理的结论。

---

## 卡片 5：怎样评测和定位 RAG 问题？

**一句话：** 先把检索与最终 LLM 研判拆开，用人工 Gold Evidence 分别评估 Query、路由、召回和排序。

### 分层定位

```text
问题理解错误 → Query Planner
路由选错     → exact / semantic / hybrid Router
正确证据没召回 → Chunk、Embedding、BM25、Top K
召回但排序靠后 → RRF / Reranker
证据正确但结论错 → Prompt、模型或证据门
```

### 核心指标

- 检索：Hit@K、Recall@K、Precision@K、MRR、nDCG@K。
- 路由：路由完全正确率、Query 数量准确率、主题召回率。
- 安全：负样本准确率、关键样本回归数。
- 性能：平均延迟、P95 延迟。

### 实验规则

- Development 用于分析和调参，Holdout 只做最终泛化验证。
- 必须按项目切分，不能把同一项目的问题分到两边。
- A/B 必须使用同一数据集指纹，每次只改变一个主要变量。
- 除总体指标外，必须保留“哪些关键样本变差”的清单。

### 当前真实边界

- 已有 7 条公开合成样本，可证明评测框架、Query Planner 和路由契约可运行。
- 公开合成样本不能证明真实 Milvus/BM25 检索质量。
- 真实历史 Gold Evidence 和正式基线仍待建立；不能虚构 Recall、MRR 提升数据。
- 作品集就绪的最低建议是 30 条：Development 至少 20 条、Holdout 至少 10 条，并覆盖精确、语义、混合、多 Query、表格、跨块和无答案场景。

### 30 秒回答

> 我不会只用“答案看起来不错”评估 RAG，而是把 Query Planning、路由、召回、排序和最终生成分层。检索层用人工 Gold Evidence 测 Recall、MRR、nDCG、路由准确率和 P95，并按项目划分 Development 与 Holdout。当前评测框架和合成基线已经完成，但真实历史金标基线还没做，所以我会明确说框架已就绪，不会把合成样本成绩包装成生产效果。

---

## 高频追问

| 追问 | 回答 |
|---|---|
| 为什么不总用 hybrid | 单路可解决的问题无需付出双路计算和额外噪声 |
| RRF 和 Reranker 区别 | RRF 融合排名、不调用模型；Reranker 重算 Query-文档相关性，通常更准但更慢 |
| 向量库能否直接保存事实 | 可以存副本用于检索，但不能替代权威事实源和版本校验 |
| 零结果怎么办 | 单路零结果补跑 hybrid；仍无结果则补资料或人工确认 |
| 为什么报价不用 RAG | 价格是结构化、强一致数据，应使用数据库和确定性规则 |
| 如何防跨项目泄露 | 项目、manifest 版本和哈希过滤，再回 MySQL 校验 active 文档与 block ID |

记忆口诀：

> 向量找意思，BM25 找字面，RRF 合名次；MySQL 管事实，证据门管结论，Gold 集管效果。

---

## 当前项目结论

> 这套系统最重要的 RAG 工程能力，不是“接了 Milvus”，而是把数据边界、检索路由、索引一致性、证据校验、故障降级和离线评测串成了闭环。当前强项是工程控制和真实检索部署；下一步最有价值的工作是建立真实历史 Gold Evidence，得到可信的 Recall、MRR、nDCG 和 P95 基线后，再决定是否引入 Reranker 或调整切块。

## 代码证据

- `rag_docker/tender_evidence_search.py`
- `AI_Middle_Office/mcp_servers/tender_evidence/retrieval_router.py`
- `AI_Middle_Office/mcp_servers/tender_evidence/query_planner.py`
- `AI_Middle_Office/mcp_servers/tender_evidence/service.py`
- `AI_Middle_Office/mcp_servers/tender_evidence/sqlalchemy_repository.py`
- `AI_Middle_Office/app/agents/bid_intake/contracts.py`
- `AI_Middle_Office/app/agents/bid_intake/evidence_gate.py`
- `AI_Middle_Office/app/agents/bid_intake/retrieval_evaluation.py`
- `AI_Middle_Office/app/services/cost_rag_sync.py`
- `AI_Middle_Office/tests/test_tender_query_planner.py`
- `AI_Middle_Office/tests/test_tender_hybrid_service_phase3b.py`
- `AI_Middle_Office/tests/test_bid_intake_retrieval_evaluation.py`

## 深入阅读

- [RAG 综合复习与面试手册](./RAG综合复习与面试手册.md)
- [Embedding 模型选型：基准评测、索引迁移与生产治理](../08-RAG与Embedding/Embedding模型选型-基准评测索引迁移与生产治理.md)
- [RAG 多格式文档摄取：Excel、图片与统一解析合同](../08-RAG与Embedding/RAG多格式文档摄取-Excel图片与统一解析合同.md)
- [RAG 检索策略：混合召回、融合、Rerank 与动态 Top-K](../08-RAG与Embedding/RAG检索策略-混合召回融合重排与动态TopK.md)
- [RAG 引用与证据链：从片段溯源到结论级对齐](../08-RAG与Embedding/RAG引用与证据链-从片段溯源到结论级对齐.md)
- [生产级 RAG 工程闭环：数据治理、检索、生成、评测与反馈](../08-RAG与Embedding/生产级RAG工程闭环-数据治理检索生成评测与反馈.md)
- [报价中台面试映射手册 01：异步任务可靠性](./报价中台面试映射手册01-异步任务可靠性.md)
