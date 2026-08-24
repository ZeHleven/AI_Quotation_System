# 旗胜投标机会研判 Agent RQ1-A：结构聚合、页眉页脚降噪与标题事实可引用化

版本：v0.1-r38

日期：2026-08-15

状态：代码、机器合同与本地隔离专项验证完成；真实 Silver A/B 显著改善但仍未达到检索可用门槛

## 1. 目标

RQ1-A 直接处理真实资料 Silver 基线暴露的表示层缺口，不引入 Query Optimizer、embedding、reranker、OCR、视觉或模型：

1. 降低跨页重复页眉、页脚、页码和项目名对 BM25 排序的污染；
2. 把同一顶层章节内的微型小节聚合到共享 Parent，并把相邻短内容聚合为更完整的 Retrieval Child；
3. 把矢量表格的连续行聚合为一个 Child，同时保留每行独立可引用 Atom；
4. 标题原文不再只存在于不可引用 Parent，而是同时形成带原始页码/bbox/Hash 的可引用 Atom；
5. 保持 PDF-C2 v2/C1 v1 历史 Profile 的输出和 Hash 语义不变。

本阶段仍不从文件名、MIME、parser_hint 或业务关键词推断 Scope、标段或标题类型。

## 2. 冻结版本

| 对象 | 版本 |
|---|---|
| Layout contract | `bid.pdf.native-layout.v1` |
| RQ1-A layout profile | `bid-pdf-native-layout-profile-v2-rq1a` |
| RQ1-A parser profile | `bid-document-parser-profile-v3-pdf-structure-rq1a` |
| Chunk role contract | `bid.evidence.chunk.v2` |
| RQ1-A chunk profile | `bid-evidence-chunk-profile-v2-rq1a` |
| C3 retrieval profile | `bid-evidence-retrieval-profile-v2-role-aware` |
| Machine profile | `bid-rq1a-structure-profile-v1` |

旧 `bid-document-parser-profile-v2-pdf-native-layout`、`bid-pdf-native-layout-profile-v1` 和 `bid-evidence-chunk-profile-v1` 保留原行为，不在同一版本下改变算法。

## 3. 重复页边元素降噪

降噪发生在 PDF-C2 已完成单页原生布局提取、尚未生成稳定 Block Key 和 Section Path 之前。候选必须同时满足：

- 位于页面顶部或底部 10% 几何区域；
- 非表格行、表单、图像等结构块；
- 归一化文本不超过 160 字符；
- 归一化签名出现在至少 `max(3 页, 全文 15%)` 的不同页面；
- 首末出现页跨度至少 2 页。

签名只做空白折叠、大小写归一和连续数字折叠，使 `Page 1/307`、`Page 2/307` 等页码可归为同类；不使用项目名、招标、合同、页眉等业务词表。为了避免封面或首次出现的身份信息被完全删除，每个签名保留第一次出现，其余重复实例从 Evidence 输入剔除。

结果记录剔除 Block/字符/签名/页面数量和不可逆签名 Hash，不把被剔除正文写入警告。若某页剔除后只剩页边重复元素，该页标为 `partial + not_requested`，等待 OCR 或人工复核；本阶段不会自行触发 OCR。

## 4. 层级与表格聚合

RQ1-A 的 Section Parent 只取完整 `section_path` 的第一层。更深层标题仍保留其完整路径和 bbox，但作为同一 Parent 下的结构 Atom 参与 Child 聚合。这样可以减少“每个短标题一个 Parent/一个超短 Child”，同时维持顶层章节硬边界。

Child 仍使用 220/380/500/600 Token Profile：

- 新标题到来时，若当前 Child 尚未达到 220 tokens，则可与前一微型小节一起聚合；达到下限后再开启新 Child；
- 标题后紧邻的段落、Clause 或表格可以共享同一 Child，但每个来源块仍是独立 Atom；
- 连续矢量 `table_row` 在同一表格 boundary 内聚合为 Child，不再每行创建一个检索 Child；
- 任何 Child 仍不得超过 600 estimated tokens；超长原始块仍只使用冻结的 80 Token 左 overlap；
- Child 和 Parent 均保持不可引用，只有 Atom 可引用。

## 5. 标题事实 Atom 化

在旧 Profile 中，heading 只形成不可引用 Section Parent，标题内的面积、工期、比例等原文可能无法进入 Fact/Citation。RQ1-A 对所有原生 heading 保留双重表示：

- Parent：章节导航与低权重辅助检索，`is_citable=false`；
- Atom：原始 heading 文本、页码、bbox、完整 section path、source block id、char span 与 Hash，`is_citable=true`。

这里不判断标题是否“像业务事实”。是否形成事实仍由后续 Fact Slot、证据充分性与冲突门禁决定；RQ1-A 只保证真实标题文本具备可引用载体。

## 6. C3 与失效边界

Evidence MCP v2 和 C3 的 Child-only Search / Atom-only Read 规则不变。C3 Index Coordinator 现在接受两个明确列举的 role-aware Parser Profile；索引 input 仍绑定不可变 ParseRun/result hash，ParseHead 变化仍立即 fail-closed 并重建 Head。

RQ1-A 不修改既有 ParseRun、EvidenceFragment、RetrievalIndex 或 Head。启用新 Parser Profile 会创建新的 ParseRun；其成为 ParseHead 后，旧 C3 Index 按既有规则 stale，不存在原地重写或旧索引回退。

## 7. 配置与迁移门禁

新增默认关闭配置：

```text
FEATURE_BID_ASSESSMENT_RQ1A_STRUCTURE_AGGREGATION=false
```

启用时必须同时满足：

```text
FEATURE_BID_ASSESSMENT_V1_RUNTIME=true
FEATURE_BID_ASSESSMENT_PHASE2_DOCUMENT_WORKER=true
FEATURE_BID_ASSESSMENT_PDF_C2_NATIVE_LAYOUT=true
FEATURE_BID_ASSESSMENT_RQ1A_STRUCTURE_AGGREGATION=true
BID_DOCUMENT_PARSER_PROFILE_VERSION=bid-document-parser-profile-v3-pdf-structure-rq1a
```

若同时启用 Evidence MCP，仍必须满足 PDF-C3 role-aware Retrieval Profile 门禁。缺少任一依赖均在配置加载或 Adapter 调用时 fail-closed。

RQ1-A 复用现有 Parse/Evidence/Retrieval 权威表，不新增表、字段或枚举，因此不需要 Alembic revision，代码唯一 head 保持 `20260814_0102`。本增量不得应用到 ECS 或正式发布候选。

## 8. 本地隔离验证结果

经用户明确授权，已完成 RQ1-A 合同/配置、旧 Profile 兼容、重复页边降噪、稳定 Hash、层级/表格聚合、标题 Atom/证据门，以及 Phase 2、C3、Evidence MCP 相邻回归；无重复矩阵共 `48 passed / 0 failed`。验证只使用合成 PDF、隔离 SQLite 和本地真实 PDF，不连接外部环境，不调用 OCR、视觉、模型、向量服务或外部 MCP。

同一份 307 页真实资料和同一 25 题 Silver v2 题集的 v2 → RQ1-A A/B 结果：

| 指标 | PDF-C2/C1 v2 | RQ1-A | 变化 |
|---|---:|---:|---:|
| Warning | 1432 | 588 | -844（-58.9%） |
| Section Parent | 1499 | 804 | -695（-46.4%） |
| Retrieval Child | 1609 | 1244 | -365（-22.7%） |
| Evidence Atom | 4237 | 5276 | +1039 |
| Child token P50 | 39 | 85 | +46 |
| Child <220 token 比例 | 88.01% | 73.23% | -14.78 个百分点 |
| 可引用目标可用率 | 88.10% | 100% | 5 个不可引用目标归零 |
| Hit@5 | 0.32 | 0.44 | +0.12 |
| Target Recall@5 | 0.24 | 0.38 | +0.14 |
| MRR@5 | 0.1667 | 0.2180 | +0.0513 |
| nDCG@5 | 0.1717 | 0.2383 | +0.0666 |
| Atom Read Target Recall@5 | 0.16 | 0.38 | +0.22 |

页边降噪识别 6 个稳定签名，在 259 页抑制 460 个重复 Block、7003 个字符；警告仅持久化数量和不可逆签名 Hash，不记录被抑制正文。确定性重放在两组均一致，Atom-only Read 违规均为 0。逐题审计显示：4 题 Recall@5 提升、7 题 Atom Read Recall 提升、3 题由零命中变为命中，没有题目在上述三项指标发生回退。

RQ1-A 已达到本阶段“结构与可引用性修复”的目标，但没有达到 Silver 建议的最低检索门槛（Hit@5 ≥0.80、Target Recall@5 ≥0.70、Atom Read Recall@5 ≥0.65）。此外，RQ1-A 结果仍为 `partial + 588 warnings`，质量投影却仍是 `high / 100`；因此下一增量必须先进入 RQ1-B Parse Quality Gate，不能把 RQ1-A 验证通过解释为 Agent 已具备业务可用质量。
