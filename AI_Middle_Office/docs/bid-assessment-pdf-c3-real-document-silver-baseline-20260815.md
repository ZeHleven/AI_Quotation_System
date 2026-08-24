# 旗胜投标机会研判 Agent：PDF-C2/C3 真实文档检索质量 Silver 基线

版本：v0.1-r37

日期：2026-08-15

状态：单文档、单审阅者 Silver 基线完成；**不满足可用性门槛**

## 1. 目的与边界

本基线用一份 307 页真实招标 PDF（内部代号 `HKC`，文件 SHA-256 前缀 `3e2d7a428df8`）验证当前 PDF-C2/C1/C3 生产合同链，而不是继续用合成结构数据证明协议正确性。运行路径为：

`PDF bytes → PDF-C2 原生布局 → PDF-C1 Parent/Child/Atom → Phase 2 Parse 权威 → PDF-C3 Index/Head → Evidence MCP v2 Search/Read`

运行环境为独立本地 SQLite 和私有输出目录。没有连接 ECS/CentOS、真实 MinIO/Redis、向量服务或外部 MCP；没有调用 OCR、视觉或模型。原始文件、逐题答案锚点、检索正文和 SQLite 权威库均留在 `.gitignore` 覆盖的本地私有目录，不进入仓库。

本轮建立的是 `draft_single_reviewer` Silver 题集，不是经业务双人复核的 Gold 集，也不能代表跨项目泛化结果。

## 2. 数据集与复现入口

- 私有题集：`evals/bid_intake/retrieval/v1/private_pdf_c3_hong_kong_centre_silver_v1.json`
- 通用运行器：`scripts/bid_pdf_c3_quality_baseline.py`
- 最终私有输出：`outputs/bid_assessment_pdf_c3_quality/private_hkc_silver_v1_20260815_r4/`
- 数据集规模：25 个业务问题、42 个 phrase-anchored 目标事实
- 覆盖面：项目身份、范围/界面、资质、保证金、投标提交、工期、税率、付款、质保、风险、争议、质量和评审

运行器会校验文件 SHA-256，创建全新 SQLite，按生产服务接口持久化 Parse/Index 权威，通过 Evidence MCP v2 执行 Search 与 Atom-only Read，并对同一请求做确定性重放校验。

## 3. PDF-C2/C1 结构结果

| 指标 | 结果 |
|---|---:|
| 页数 | 307 |
| 原生文本页 | 307 |
| partial 页 | 1 |
| 表格 | 37 |
| 图片 | 1 |
| 双栏页 | 13 |
| Section Parent | 1499 |
| Retrieval Child | 1609 |
| Evidence Atom | 4237 |
| Parent / 页 | 4.88 |
| Child token P50 / P95 | 39 / 393 |
| Child 低于 220 token 比例 | 88.01% |
| Atom token P50 / P95 | 25 / 109 |
| Parse 状态 | `partial` |
| OCR 状态 | `not_requested` |

共记录 1432 条结构警告：1002 条 `BID_CHUNK_CHILD_BELOW_SOFT_MIN`、429 条 `BID_CHUNK_SECTION_WITHOUT_BODY`、1 条 `PDF_PAGE_NATIVE_TEXT_INSUFFICIENT`。当前质量投影却仍为 `high / 100`，说明质量评分没有反映结构警告密度、碎片化和事实可引用性，不能作为上线门禁。

## 4. PDF-C3 检索与证据指标

| 指标 | 结果 |
|---|---:|
| Hit@5 | 0.32 |
| Target Recall@5 | 0.24 |
| Precision@5 | 0.072 |
| MRR@5 | 0.1667 |
| nDCG@5 | 0.1717 |
| Hit@8 | 0.44 |
| Target Recall@8 | 0.38 |
| MRR@8 | 0.1848 |
| nDCG@8 | 0.2220 |
| Atom Read Target Recall@5 | 0.16 |
| 可引用目标可用率 | 37 / 42 = 88.10% |
| 不可引用目标 | 5 |
| Atom-only Read 违规 | 0 |
| 确定性重放 | 一致 |

路由共执行 27 条计划查询，其中 `exact=26`、`semantic=1`，平均每个业务问题只有 1.08 条计划查询。语义后端按本轮边界关闭，唯一 semantic 路由明确降级到 BM25。

性能不是当前首要瓶颈：解析约 15.09 秒、索引约 0.82 秒，Search P50/P95 约 350/465 ms，Read P95 约 45 ms。以上仅是本机单文档 SQLite/词法检索数据，不能外推并发容量。

## 5. 结论

### 5.1 已证明有效的部分

1. 307 页真实 PDF 可以不依赖 OCR 完成原生布局解析、Phase 2 权威持久化和 C3 索引构建。
2. Retrieval Head、Manifest/Profile/Parse 血缘和 Evidence MCP v2 可以在真实数据量下工作。
3. Search 仍只返回不可引用 Child，Read 仍只返回可引用 Atom；本轮没有证据角色越界。
4. 相同请求的结果顺序和内容 Hash 可确定性重放。

### 5.2 未达到可用门槛的部分

1. **结构过碎。** 1609 个 Child 中 88.01% 低于软下限，章节标题、表格字段和值、跨行条款没有稳定聚合成适合召回的语义单元。
2. **存在不可引用事实。** 5 个目标只出现在 Parent/heading 表示中，Search 能导航到附近 Child，但 Atom Read 无法引用事实本身。
3. **页眉页脚和模板条款污染排序。** 重复项目名、页码和通用合同文本压过首页表格、专用条款和具体付款/工期事实。
4. **Query Optimizer 尚弱。** 大多数长自然语言问题只生成一个原样 exact 查询，多事实问题没有按槽位拆解，术语别名、字段名和值约束没有展开。
5. **当前 BM25/RRF 不区分字段。** 标题、表格键值、正文、页眉页脚使用近似同一词法权重；Parent 0.35 辅助也没有形成可测的独立增益。
6. **质量分失真。** `partial + 1432 warnings + 5/42 不可引用` 仍被评为 `high / 100`。

因此，PDF-C3 的协议安全和运行完整性已经通过，但本轮真实资料的检索业务质量没有通过。不得把此前 `211 passed / 0 failed` 的合同回归解释为真实检索准确率达标。

## 6. 下一增量：Retrieval Quality-1（P0 顺序）

1. **RQ1-A 结构与证据修复**：通用页眉页脚识别/降噪；表格键值与标题-正文聚合；保证标题中的事实值落入可引用 Atom；减少空 Section 和超短 Child。
2. **RQ1-B Parse Quality Gate**：把警告密度、碎片率、不可引用 heading-value、原生文本覆盖率纳入质量分，禁止 `partial` 被无条件投影为 `high / 100`。
3. **RQ1-C 确定性 Query Optimizer**：按业务槽位拆解多事实问题，生成字段词、同义词、实体/关系/数值约束，保留原查询并限制扩展数量。
4. **RQ1-D 字段感知词法召回**：区分 heading/table-key/table-value/body/header-footer；对重复模板做 document-frequency/boilerplate 抑制；记录各通道消融指标。
5. **RQ1-E 语义召回与重排**：只有 A—D 的表示和词法基线修复后再接 embedding/向量与受控 reranker，避免用模型掩盖不可引用和结构错误。
6. **Gold 门禁**：由业务人员复核题目、答案事实、页码和缺失字段，形成至少双人审阅的 Gold；再扩充不同项目/版式/扫描比例的 holdout 文档。

下一轮最低验收建议：可引用目标可用率 `100%`、Atom-only 违规 `0`、Hit@5 `≥0.80`、Target Recall@5 `≥0.70`、Atom Read Target Recall@5 `≥0.65`，并对每个改动提供相同 Silver/后续 Gold 集的前后对比和消融结果。

## 7. RQ1-A 隔离 A/B 复测（v0.1-r38）

经用户授权，在不调用 OCR、视觉或模型的边界内，使用同一真实 PDF、同一 25 题 Silver v2、同一 C3 Retrieval Profile 对旧 v2 Parser 与 RQ1-A Parser 做隔离 A/B。RQ1-A 将 Warning 从 1432 降至 588、Parent 从 1499 降至 804、Child 从 1609 降至 1244；Child token P50 从 39 提升至 85，低于 220 token 比例从 88.01% 降至 73.23%。

5 个不可引用目标全部转为可引用 Atom，可引用目标可用率达到 100%；Hit@5/Target Recall@5/Atom Read Recall@5 分别由 `0.32/0.24/0.16` 提升为 `0.44/0.38/0.38`。逐题没有 Recall@5、Atom Read Recall@5 或 Hit@5 回退，但总体指标仍显著低于本文件建议门槛。因此 RQ1-A 只关闭“结构聚合、页边降噪、标题可引用化”问题，不关闭检索质量问题。

## 8. RQ1-B 隔离评分 A/B（v0.1-r39）

经用户授权，继续使用同一真实 PDF、同一25题 Silver 和同一 C3 Retrieval Profile，对 RQ1-A v3 与 RQ1-B v4 做隔离评分 A/B。RQ1-B 将失真的 `partial + high/100` 修正为 `partial + medium/84 / review_required`；四维分为 Native readiness 30、Structural coherence 19、Citable integrity 25、Warning hygiene 10。

RQ1-B 的 review reasons 为 partial 页、OCR pending 页、非隔离短 Child 比例高于35%以及未达到 high gate；没有 blocking reason，当前 Retrieval Index、Lot Detection 和 automated assessment 三个消费门均放行。Warning `588→589` 仅增加首条质量报告；Parent/Child/Atom 仍为 `804/1244/5276`，Hit@5、Target Recall@5、Atom Read Recall@5 仍为 `0.44/0.38/0.38`，25题除耗时外的排名和指标完全一致。

因此 RQ1-B 已关闭质量分失真和下游 fail-closed 门禁问题，没有掩盖现有检索质量缺口。下一步进入 RQ1-C 确定性 Query Optimizer；本轮仍未调用 OCR、视觉、模型、向量服务或外部 MCP。

## 9. RQ1-C 共享 Index Query A/B（v0.1-r40）

经用户授权，使用同一 RQ1-B ParseHead、同一 C3 RetrievalIndexHead 和同一 Index result hash，顺序执行旧 `tender-query-planner-v1` 与 RQ1-C Query Plan v2。平均查询数 `1.08→3.84`，Hit@5 `0.44→0.72`，Target Recall@5 与 Atom Read Recall@5 均从 `0.38→0.66`，Hit@8/Recall@8 从 `0.48/0.44→0.84/0.78`，Top-5 零命中题 `14→7`；Atom-only Read 违规保持0，两组确定性重放均一致。

代价是词法 Search P50/P95 从约 `290/397ms` 增至 `690/1065ms`。逐题审查还发现 `HKC-C3-011` 从 Top-5 移至第6位、`HKC-C3-009` Recall 不变但首命中排名下降；整体 Hit@5/Recall@5 也仍低于 `0.80/0.70` 建议门槛。因此 RQ1-C 关闭“查询扩展未真正执行”的问题，但不关闭字段权重、模板高频词污染和召回效率问题；下一步进入 RQ1-D 字段感知词法召回与通道消融。本轮仍未调用 OCR、视觉、模型、向量服务或外部 MCP。
