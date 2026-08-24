# 旗胜投标机会研判 Agent RQ1-D：字段感知词法召回

版本：v0.1-r41

日期：2026-08-15

状态：协议、代码、机器合同、本地隔离专项验证与“香港中心”共享 Index 消融 A/B 已完成

## 1. 问题与目标

RQ1-C 将平均计划查询数从1.08提升到3.84，并把“香港中心”Silver 的 Hit@5/Recall@5 从 `0.44/0.38` 提升到 `0.72/0.66`，但当前 Evidence MCP 仍把 Child 的标题上下文、表格字段、表格值和正文拼成一段 `retrieval_text` 后统一 BM25。结构来源不同却同权计分，扩展 Query 还可能把原查询的正确结果挤出 Top-5；P95 Search 也从约397ms升到1065ms。

RQ1-D 的目标是在不改变 C1/C2 Parse、不重建 C3 Index、不调用模型或向量服务的前提下，从现有不可变 Child/Atom locator 派生字段通道，执行确定性 BM25F、文档内高频模板抑制、原查询锚定和 Parent 多样性控制。Search 仍只返回不可引用 Child，Read 仍只返回可引用 Atom。

## 2. 冻结版本

| 对象 | 版本 |
|---|---|
| Lexical contract | `bid.evidence.lexical-search.v1` |
| Lexical profile | `bid-evidence-lexical-profile-v1-rq1d` |
| Source C3 Index | `bid-evidence-retrieval-profile-v2-role-aware` |
| Source Query Plan | `bid-evidence-query-optimizer-profile-v1-rq1c` |
| Evidence MCP role contract | `bid-assessment-evidence-mcp/v2` |
| Search Adapter | `bid-evidence-mcp-rq1d-search@v4-field-aware-lexical` |

默认关闭配置：

```text
FEATURE_BID_ASSESSMENT_RQ1D_FIELD_AWARE_LEXICAL=false
BID_EVIDENCE_LEXICAL_SEARCH_PROFILE_VERSION=bid-evidence-lexical-profile-v0-single-field
```

启用时必须同时启用 RQ1-C、RQ1-B、PDF-C3 role-aware Retrieval 和 Evidence MCP，并精确选择 `bid-evidence-lexical-profile-v1-rq1d`；否则 fail closed。历史 v1/v2/v3 Dispatch 继续执行各自冻结的旧 Adapter。

## 3. 字段通道投影

RQ1-D 不读取原始文件。每个 C3 Retrieval Entry 只从当前 Index 的 Child 和其 `source_atom_ids_json` 指向的可引用 Atom 派生五个通道：

1. `section_heading`：Child `section_path` 与 heading Atom；
2. `table_key`：结构可信的表格字段名单元格；
3. `table_value`：与可信字段名成对的表格值单元格；
4. `table_row`：完整表格行，任何不确定拆分均保留在此通道；
5. `body`：paragraph/form/其他正文 Atom。

表格 Key/Value 只使用 PDF-C2 已生成的稳定 `|` 单元格分隔、单元格数量、长度和数字比例判断，不使用项目名或具体投标业务答案。无法确认时不得猜测，只写完整 `table_row`。投影按 NFKC/空白规范化、稳定去重和固定通道顺序生成，每个 Child 冻结 `source_entry_hash + channels + profile` 的 projection hash。

## 4. BM25F 与模板抑制

Token 规则保持确定性：ASCII run 加 CJK overlapping bigram/trigram。每个投影只预分词一次，查询计划的每条 Query 复用同一语料对象。

基础通道权重为 Heading 1.35、Table Key 1.80、Table Value 1.35、Table Row 1.10、Body 1.00；`field_codes` 提升 Key/Heading，数值/日期/比例等 `answer_shapes` 提升 Value/Row，list/method/text 提升 Body/Heading。完整倍数冻结在机器 Profile 中。

真实消融证明，字段 BM25F 不能替换已验证的 RQ1-C Child BM25。最终 Profile 固定以旧 Child BM25 `1.0` 为基线，Body/Heading 字段结果只以 `0.005` 参与并列细排；Table Key/Value/Row 强结构命中以 `0.10` 参与加权 RRF。`0.20` 在单文档上更高，但因缺少跨项目证据而不采用，避免按“香港中心”过拟合。

文档内出现于至少65% Child 的 Token 乘0.45，至少85%乘0.20；小于4个 Child 时不启用。该规则只改变排名分数，不删除 Child、Atom 或原文，也不把项目专有词写入规则。

## 5. 融合与排序保护

- 每条 RQ1-C Query 先保留 Child BM25 `1.0` 基线，再按 `k=60` 融合字段 BM25F 的弱 `0.005`/表格强结构 `0.10` tie-breaker；
- q1 原查询额外对基线 Child BM25 获得0.45 anchor RRF 权重；
- Parent 仍是辅助导航，但权重从0.35降为0.20；
- 首轮 Top-K 同一 Parent 最多选择2个 Child，候选不足时再按原总分回填；
- Hit 返回 `matched_channels` 和 projection hash 供审计，不返回内部 Token 列表或文档高频词正文。

完整 Lexical Projection metadata、Query Plan、检索路由、Hit 和警告都进入 Search result hash。证据角色合同不变。

## 6. 内容寻址缓存与失效

投影缓存是进程内最多8个 corpus 的只读 LRU，键为 `profile_version + index_set_hash + allowed_document_versions`。每次命中还必须逐项匹配当前 Entry hash；不匹配立即丢弃并重建。ParseHead、RetrievalIndexHead、Manifest 或文档过滤发生变化都会得到新键，不允许使用 stale projection。投影 Hash 和 BM25 并列排序只使用 C3 `retrieval_child_key/section_parent_key`，不得使用每次持久化随机生成的 Fragment UUID；因此同一结构在不同隔离 authority 中仍得到同一 projection set hash。

缓存不是新权威，不跨进程写入，不进入数据库，不改变 Worker/Dispatch/Checkpoint 恢复语义。冷启动可从现有 C3 Entry/Atom 完全重建。

## 7. 迁移门禁

首版复用 `bid_evidence_retrieval_indexes/entries/heads` 和 `bid_evidence_fragments`，不新增表、字段、枚举或事件，因此不需要 Alembic revision，唯一 head 保持 `20260814_0102`。只有授权压测证明查询时投影仍无法满足延迟门槛，才评审独立 `0103` immutable lexical sidecar；不得把结构 JSON 塞进旧 `retrieval_text`，也不得修改历史 v2 Entry。

## 8. 本地隔离专项验证

最终无重复矩阵共 `205 passed / 0 failed`：RQ1-D 核心、机器合同/Schema/配置、五通道投影、保守 Table Key/Value、NFKC/稳定 Key/Hash、BM25F/答案形状、高频模板抑制、q1 Anchor、Parent 权重/多样性、缓存失效、Evidence MCP v4 和历史 Adapter 共127项；Phase 3E/3F、Dispatch、事务/幂等、Checkpoint/恢复、API-41/SSE 相邻链20项；0083—0102 迁移拓扑58项。唯一发现的合同问题是新 Schema 未加入测试文件全集，补齐清单后完整矩阵通过；生产逻辑未因此改变。

## 9. “香港中心”25题共享 Index A/B

正式验收复用同一307页 PDF、同一25题/42目标 Silver、RQ1-B v4 ParseHead 和 C3 IndexHead。主组为 RQ1-C + 单字段 Child BM25，比较组只切换 RQ1-D Lexical Profile；两组共享数据库、Parse Run、Retrieval Index ID/Input Hash/Result Hash。

| 指标 | RQ1-C 主组 | RQ1-D | 变化 |
|---|---:|---:|---:|
| Hit@5 | 0.68 | 0.92 | +0.24 |
| Target Recall@5 | 0.62 | 0.86 | +0.24 |
| Atom Read Recall@5 | 0.62 | 0.82 | +0.20 |
| MRR@5 | 0.3860 | 0.5840 | +0.1980 |
| nDCG@5 | 0.4238 | 0.6342 | +0.2104 |
| Hit@8 / Recall@8 | 0.84 / 0.78 | 0.96 / 0.92 | +0.12 / +0.14 |
| Top-5 零命中题 | 8 | 2 | -6 |
| Search P50 / P95 | 825 / 1195 ms | 1160 / 1708 ms | +335 / +513 ms |

6题 Recall@5 提升，逐题无回退；Atom-only 违规为0、可引用目标可用率100%、重放一致。25题的 projection set hash 唯一且一致为 `8c1692a630ad868f32c787afb7f2ee72fa1fe06c9b91fd5a181bc2cd0a252c07`。质量门全部通过，但约43%的 P95 增幅需要在后续 RQ1-E 前先做预分词/基线语料缓存与性能预算优化。

本阶段未调用 OCR、视觉、模型、Embedding、向量数据库或外部 MCP，未连接 ECS/CentOS、真实 MinIO/Redis，未修改旧 `bid_intake_*`。
