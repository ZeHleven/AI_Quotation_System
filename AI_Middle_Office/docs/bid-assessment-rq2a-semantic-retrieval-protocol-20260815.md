# 报价资料研判 Agent RQ2-A：语义索引与召回协议（2026-08-15）

状态：**代码、机器合同、0103 迁移、本地隔离专项和真实 BCE semantic-only Silver A/B 已完成；生产 Milvus Adapter 尚未在本机真实 daemon 上执行。**

## 1. 目标与非目标

RQ2-A 在 PDF-C3 不可变 RetrievalIndex 之上建立 Child-only 语义索引，并通过 Evidence MCP 冻结一个独立的 Semantic-only Search Adapter。它只回答“语义通道能召回哪些 Child”，不提前完成 RQ2-B 的词法/语义候选融合，也不实现 RQ2-C 重排。

保持不变：

- Search 只返回 `retrieval_child`，`is_citable=false`；
- Parent 不建向量、不成为语义候选；
- Atom 不建向量，`evidence.read` 仍只返回 `evidence_atom`；
- 查询继续使用 RQ1-C 确定性 Query Plan，不引入 LLM Query Rewrite/Clarification；
- RQ1-D v4 Adapter 和全部历史 Adapter 保持冻结；
- 默认开关关闭，不连接 ECS/CentOS/真实 MinIO/Redis/Milvus。

## 2. 旧组件复用结论

可复用：

- 模型：`maidalun1020/bce-embedding-base_v1`；
- 固定模型快照：`9c0d82af44af61abe171ffae23fde5740c0ec1a8`；
- 768 维、L2 归一化、COSINE；
- Milvus HNSW：`M=32`、`efConstruction=200`、查询 `ef=128`；
- 旧 `retrieval_router.py` 的确定性 exact/semantic/hybrid 路由元数据；
- RQ1-C weighted RRF 方法，但 RQ2-A 只用于同一语义通道内的 Query expansion 合并；
- 旧评测的 Hit/Recall/MRR/nDCG、逐题回退和 zero-hit 方法。

不可复用：

- `enterprise_quotation_rag`、旧 tender/bid_intake collection；
- 旧 `bid_intake_*` Repository、Manifest、Checkpoint、IndexJob；
- 旧 HTTP Hybrid Client 的 `case_id/manifest_version` 权威；
- 旧服务中进程启动即加载模型、连接 Milvus 的副作用；
- 旧混合结果直接作为可引用事实的行为。

RQ2-A 使用独立 collection `bid_assessment_evidence_semantic_v1`，Provider 在真正执行时才懒加载模型和连接 Milvus。

## 3. 冻结合同

| 项 | 冻结值 |
|---|---|
| Semantic Index 合同 | `bid.evidence.semantic-index.v1` |
| Semantic Profile | `bid-evidence-semantic-profile-v1-rq2a-bce` |
| 默认 Profile | `bid-evidence-semantic-profile-v0-disabled` |
| Provider 合同 | `bid.semantic-vector-provider.v1` |
| Provider | `bce-milvus` |
| Embedding 输入 | PDF-C3 `retrieval_text` |
| 模型 | BCE snapshot，768维、Normalize、COSINE |
| Adapter | `bid-evidence-mcp-rq2a-search@v5-child-semantic-recall` |
| 最大 Search 输出 | 8 个 Child |
| 单 Query 语义候选深度 | 40 |
| Query expansion 合并 | weighted RRF，`k=60` |
| Provider 写入批次 | 每批最多 64 个 Child，批次前后 Heartbeat |
| 词法/语义融合 | RQ2-A 禁止，留给 RQ2-B |
| Reranker | RQ2-A 禁止，留给 RQ2-C |

机器合同：

- `contracts/bid_assessment/v1/rq2a-semantic-retrieval-profile.json`
- `schemas/bid_assessment/v1/semantic-retrieval.schema.json`

## 4. 数据权威与迁移

RQ2-A 必须新增 Alembic `20260815_0103`，因为模型版本、Provider 结果和 RetrievalHead 对齐需要可恢复、可审计的持久权威，不能只依赖 Milvus collection 状态。

### `bid_evidence_semantic_indexes`

每个 PDF-C3 RetrievalIndex + Semantic Profile 唯一的不可变语义快照，冻结：

- RetrievalIndex ID/Profile/result hash/entry count；
- Provider/model/revision/dimension/metric/normalize；
- content-addressed namespace、provider request ID、input hash；
- queued/building/ready/failed/stale；
- attempt、Lease、Heartbeat、Fencing、result hash。

### `bid_evidence_semantic_entries`

每个 Retrieval Child 一条不可变语义条目，只保存：

- RetrievalEntry/Child ID 与稳定 Child key；
- source entry hash、embedding text hash；
- provider record ID、vector hash、dimension；
- ordinal 与 entry hash。

数据库不存向量正文。Milvus 返回的 record ID、Child key、source hash、text hash、vector hash 必须与数据库逐项一致，否则 fail-closed。

### `bid_evidence_semantic_heads`

按 `document_version_id + semantic_profile_version` 选择唯一当前 ready SemanticIndex，并同时冻结当前 RetrievalIndex ID。

0103 downgrade 为在线保护式降级：三张表有任一数据即拒绝，以避免丢失语义血缘和未清理的 Provider namespace。

## 5. 构建事务与恢复

```text
RetrievalHead ready
  -> reconcile 创建 queued SemanticIndex
  -> Worker 领取 building + lease + fencing_token
  -> 事务外按 64 Child 分批 BCE encode + Milvus content-addressed upsert
  -> 每批前后短事务 Heartbeat，过期 Lease 立即失去写权
  -> 事务内校验完整 receipts
  -> 原子写 SemanticEntries + ready Index + SemanticHead
```

关键规则：

- Provider I/O 不占用数据库事务；
- namespace、record ID、provider request ID 由冻结输入确定；record ID 同时纳入 semantic input hash/namespace，避免跨文档或重建主键碰撞；
- Provider collection 必须与冻结字段、768维 FLOAT_VECTOR、HNSW/COSINE 完全一致，否则 fail-closed；
- 发送后结果未知时，下一 Attempt 使用更高 Fence 对同一 namespace/record ID 幂等 upsert；
- 数据库不允许部分 SemanticEntry；
- 旧 Attempt 完成时 Fence 不匹配，禁止提交；
- Provider 暂时不可用且未到最大尝试次数时回到 queued；
- 协议/Hash 错误直接 failed；
- RetrievalHead 漂移直接 stale，并删除可变 SemanticHead，不允许旧语义索引兜底。

## 6. Search 行为

RQ2-A v5 Adapter：

1. 重建当前 Run 的 Manifest/C3 IndexSet 并验证 ParseHead/C3 Hash；
2. 按 Document Version/Role/Type 收窄范围；
3. 验证每个文档的 SemanticHead 与当前 RetrievalHead 完全一致；
4. 执行 RQ1-C Query Plan；
5. 每条 Query 对每个不可变 semantic namespace 做 Child 向量召回；
6. 在语义通道内部做 weighted RRF；
7. Provider Hit 对数据库 SemanticEntry 做稳定身份和 Hash 校验；
8. 输出不可引用 Child，后续仍须 `evidence.read` 获取 Atom。

RQ2-A 不与 RQ1-D 合并。真实效果评测应分别比较 RQ1-D lexical baseline 与 RQ2-A semantic-only；只有 RQ2-B 才能冻结跨通道加权 RRF。

## 7. 配置门禁

总开关：`FEATURE_BID_ASSESSMENT_RQ2A_SEMANTIC_RECALL=false`。

启用必须同时满足：

- RQ1-D 及其全部前置开关开启；
- Semantic Profile 为冻结 v1；
- Provider 为 `bce-milvus`；
- 模型 ID、snapshot、768 维与 Profile 一致；
- 独立 Milvus collection、Host/Port、Lease/Attempt 参数合法；
- 模型默认 offline，禁止运行时公网下载。

任一不一致都在配置校验阶段 fail-closed。

## 8. 本地隔离验证结果

经用户明确授权，已完成以下矩阵：

- 合同/Schema/Profile/配置门禁；
- 0103 upgrade/downgrade 与 0083—0103 线性拓扑；
- Index reconcile/claim/Lease/Heartbeat/Fencing；
- Provider upsert 幂等、发送后未知结果恢复、事务回滚；
- RetrievalHead/Profile/model/hash/Manifest 失效；
- Child-only Search、Atom-only Read、Provider 越权 Hit 拒绝；
- 历史 v2/v3/v4 Adapter 与 Phase 3E/3F/API-41/SSE 相邻回归；
- “香港中心”25题 RQ1-D→RQ2-A semantic-only 隔离 A/B。

最终无重复专项矩阵为 `190 passed / 0 failed`：Semantic Index 核心与状态机 7、配置门禁1、合同/Schema 74、0083—0103 迁移拓扑与隔离 upgrade/downgrade 60、PDF-C3/Evidence MCP v5及历史 Adapter 20、运行服务/API-41/SSE 17、Phase 3E/3F/API-41 全链11。测试只使用隔离 SQLite 和注入式确定性 Provider，不连接外部环境。

真实质量 A/B 使用固定本地快照 `9c0d82af...`、CPU、768维归一化 BCE embedding，完整经过 SemanticIndex/Entry/Head、Lease/Fencing、Evidence MCP v5 和 Atom-only Read。由于本机 Docker daemon 停止、Windows 未安装 Milvus Lite，且启动 Docker Desktop 可能自动拉起既有容器，评测端明确使用 `isolated-bce-exact-cosine` 进程内精确 COSINE；输出中的 `production_milvus_adapter_executed=false`，不得将该结果表述为真实 Milvus 性能或 HNSW 召回验证。没有安装依赖、没有连接公网，也没有启动 Docker Desktop。生产 `MilvusBceSemanticProvider` 的 Schema/身份/Hash/Fence 合同已通过隔离测试，但首次真实 daemon 联调仍是后续部署前门禁。

## 9. “香港中心”semantic-only A/B

同一 307 页 PDF、同一25题/42目标 Silver、同一 RQ1-B ParseHead 与 PDF-C3 RetrievalIndexHead：

| 指标 | RQ1-D lexical | RQ2-A semantic-only | 变化 |
|---|---:|---:|---:|
| Hit@5 | 0.92 | 0.68 | -0.24 |
| Target Recall@5 | 0.86 | 0.60 | -0.26 |
| Precision@5 | 0.208 | 0.136 | -0.072 |
| MRR@5 | 0.584 | 0.478 | -0.106 |
| nDCG@5 | 0.634161 | 0.476004 | -0.158157 |
| Hit@8 | 0.96 | 0.76 | -0.20 |
| Target Recall@8 | 0.92 | 0.68 | -0.24 |
| Atom Read Recall@5 | 0.82 | 0.60 | -0.22 |
| Atom-only 违规 | 0 | 0 | 0 |

Semantic authority 为1个 ready Index、1个 Head、1244个唯一 Child Entry；所有 vector dimension 为768，vector hash 长度为64，未映射 Child 为0，确定性重放一致。语义索引构建约84.9秒（含真实 BCE CPU embedding）。

逐题上，semantic-only 新命中词法 Top-5 零命中的 `HKC-C3-012`、`HKC-C3-025`，但对11题回退且8题 Top-5 零命中。因此 RQ2-A 证明了语义通道有互补召回价值，也证明它不能替代 RQ1-D。下一步进入 RQ2-B，只做受控 BM25F + Semantic 候选融合、权重/去重/多样性消融；在融合验证前，v5 semantic-only Adapter 不作为默认检索入口。

私有输出位于 `.gitignore` 覆盖的 `outputs/bid_assessment_pdf_c3_quality/private_hkc_silver_v1_20260815_ab_shared_index_rq2a_r42_validated/`，包含共享隔离 SQLite、摘要和逐题报告。未调用 OCR、视觉、生成模型或外部 MCP。
