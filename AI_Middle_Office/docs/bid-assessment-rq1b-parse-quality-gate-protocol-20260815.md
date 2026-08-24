# 旗胜投标机会研判 Agent RQ1-B：Parse Quality Gate

版本：v0.1-r39

日期：2026-08-15

状态：协议、代码、机器合同、专项回归与“香港中心”隔离 A/B 已完成

## 1. 目标

RQ1-B 修复“解析状态为 `partial`、存在大量结构警告，却仍投影为 `high / 100`”的问题。它把两类语义拆开：

- Parse status：描述字节是否已完成本 Profile 的解析，以及是否存在 partial 页；
- Parse quality gate：描述该解析结果是否足以被 Retrieval Index、Lot Detection 和自动研判消费。

本阶段不改变 PDF-C2/RQ1-A 的版面与 Chunk 算法，不调用 OCR、视觉、模型、向量服务或外部 MCP。

## 2. 冻结版本

| 对象 | 版本 |
|---|---|
| Quality contract | `bid.parse.quality.v1` |
| Quality profile | `bid-parse-quality-profile-v1` |
| RQ1-B parser profile | `bid-document-parser-profile-v4-pdf-quality-gated-rq1b` |
| 继承的 Layout profile | `bid-pdf-native-layout-profile-v2-rq1a` |
| 继承的 Chunk profile | `bid-evidence-chunk-profile-v2-rq1a` |
| 质量报告警告码 | `PDF_PARSE_QUALITY_GATE_EVALUATED` |

RQ1-B 使用新 Parser Profile，因为质量分、警告与 Parse result hash 都是权威输出的一部分。不得在已冻结的 RQ1-A v3 Profile 下原地修改评分算法。

## 3. 四维确定性评分

总分100分，全部来自原生布局、Chunk 和证据角色的聚合指标，不读取文件名、MIME、parser_hint 或业务关键词。

| 维度 | 权重 | 输入 |
|---|---:|---|
| Native readiness | 30 | missing/partial 页比例 |
| Structural coherence | 35 | 非结构隔离 Child 的低于220 Token比例 |
| Citable integrity | 25 | Retrieval Child、Evidence Atom、heading Atom覆盖 |
| Warning hygiene | 10 | 去除已独立计分项和页边抑制信息后的 actionable warning/page |

等级仍复用现有数据库枚举：`high >=85`、`medium >=60`、`low <60`。只要 Parse status 为 `partial`，分数最高为84，不能再投影为 `high`；命中硬阻断条件时分数最高为39。

## 4. Gate 状态与理由码

质量状态为：

- `pass`：无阻断、无复核理由且分数至少85；
- `review_required`：不存在硬阻断，但有 partial/OCR pending、碎片率、警告密度或分数不足；
- `blocked`：缺少 Retrieval Child/可引用 Atom，missing/partial 页比例达到20%，或 heading 可引用覆盖低于90%。

报告只保存聚合数量、比例、维度分、稳定理由码、消费门和 SHA-256，不保存文档原文或被抑制文本。报告作为第一条安全 Warning 持久化，并进入 Parse result hash；Worker 在写入任何 Unit/Evidence 前校验报告唯一性、Profile/分数/等级血缘、状态不变量与 Hash。

## 5. 下游阻断规则

| 消费者 | 放行条件 | 阻断行为 |
|---|---|---|
| C3 Retrieval Index | Gate 非 `blocked` | 不创建 Index；事件消费记录 `parse_quality_gate_blocked`，无旧索引回退 |
| Lot Detection | Gate 非 blocked、missing 页比例不高于10%、heading 可引用率至少95% | Manifest ParseSet 为 `failed`，不创建 LotDetectionRun |
| Phase 3 Run Bootstrap | Gate 非 blocked 且分数至少60 | `BID_RUN_INPUT_NOT_READY`，不创建占位 Run |

`review_required` 不等于自动失败。它可在满足消费者最低条件时继续受控检索或研判，但必须通过 API Warning 暴露复核状态。报告缺失、重复、Hash 漂移或字段不合法一律 fail-closed。

## 6. 配置与兼容门禁

新增默认关闭配置：

```text
FEATURE_BID_ASSESSMENT_RQ1B_PARSE_QUALITY_GATE=false
```

启用 RQ1-B 必须同时启用 V1 Runtime、Phase 2 Document Worker、PDF-C2 和 RQ1-A，并精确选择 v4 Parser Profile。C3/Evidence MCP 仍使用既有独立门禁。

旧 v1、PDF-C2 v2 和 RQ1-A v3 Profile 不要求 RQ1-B 报告，不改变其质量分、Warning、Parse result hash 或 Retrieval input hash。C3 仅把 v4 增加到明确列举的 role-aware Profile 集。

## 7. 数据与迁移边界

质量报告复用 `BidDocumentParseRun.quality_grade/quality_score/warnings_json/result_hash`、既有 Outbox 和公共 Warning 投影，不新增表、字段或受约束枚举，因此不需要 Alembic revision。代码唯一 head 保持 `20260814_0102`；本增量不得应用到 ECS 或正式发布候选。

## 8. 本地隔离验证

经用户明确授权，RQ1-B 完成无重复专项矩阵 `179 passed / 0 failed`：合同/Schema/配置 `84`，Parser/Worker/C3/旧 Hash 兼容 `32`，Phase 3A/API-30/Evidence MCP 相邻链 `5`，0083—0102 迁移拓扑 `58`。Python 语法编译、JSON 解析与 diff 静态检查同时通过。所有数据库均为隔离 SQLite/Alembic 环境，未连接 ECS、CentOS、真实 MinIO/Redis 或外部服务。

同一“香港中心”真实 PDF、同一25题 Silver、同一 C3 Retrieval Profile 的 RQ1-A→RQ1-B A/B 结果如下：

| 指标 | RQ1-A v3 | RQ1-B v4 |
|---|---:|---:|
| Parse status | `partial` | `partial` |
| Quality | `high / 100` | `medium / 84` |
| Gate | 无 | `review_required` |
| Warning | 588 | 589（新增1条质量报告） |
| Parent / Child / Atom | 804 / 1244 / 5276 | 804 / 1244 / 5276 |
| Hit@5 | 0.44 | 0.44 |
| Target Recall@5 | 0.38 | 0.38 |
| Atom Read Recall@5 | 0.38 | 0.38 |

RQ1-B 的四维分为 `30 + 19 + 25 + 10 = 84`。复核理由为 partial 页、OCR pending 页、非隔离短 Child 比例高于35%及未达到 high gate；无 blocking reason，Retrieval Index、Lot Detection 和 automated assessment 三个消费门均放行。质量报告 Hash 为 `2e126214dc053186d21aff7471b3317fb1acbaad2c0171ad082a57ba5aa15ecb`。逐题除耗时外的排名与指标完全一致，证明 RQ1-B 只修正质量判定和 v4 血缘，没有偷偷调整 Chunk 或检索排序。

本轮没有调用 OCR、视觉、模型、向量服务或外部 MCP。RQ1-B 已关闭“质量分失真与下游 fail-closed 门禁”问题，但 `Hit@5=0.44 / Recall@5=0.38` 仍低于业务建议门槛；下一增量进入 RQ1-C 确定性 Query Optimizer。
