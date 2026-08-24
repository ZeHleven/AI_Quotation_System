# AI 智能报价中台 — 项目上下文

@AI_Middle_Office/AGENTS.md
@ROADMAP.md

详细文档见 `AI_Middle_Office/AGENTS.md`，以下为快速摘要。

---

## 系统架构

```
公网用户 → ECS Nginx (HTTPS 443)
              ↓
      FastAPI API + Celery Worker
              ↓
 MySQL / Redis / MinIO / n8n / Dify / RAG / Milvus
      （同一 ECS 私有 Docker 网络，无依赖宿主机端口）
```

## 单 ECS 正式部署

| 机器 | 角色 | 运行方式 |
|------|------|---------|
| 当前 ECS | Nginx、FastAPI、Celery、MySQL、Redis/MinIO、n8n、Dify、Milvus、RAG | Docker Compose；持久化数据位于 `/data/ai-middle-office` |
| Windows | 本地开发与维护，不再承载正式运行依赖 | 本地源码/测试环境 |
| 原 CentOS 7.9 (`192.168.88.128`) | 48 小时回滚源，正式服务保持停止 | 最终冷备份 `20260807_111606`，观察期内不得删除 |

## 关键文件


```
Clear_test/
├── AGENTS.md                        # 本文件（根目录自动加载）
├── app.html                         # 登录门户（JWT鉴权）
├── index.html                       # 业务工作台（AI测算）
├── admin.html                       # 知识库管理（仅admin）
├── AI_Middle_Office/
│   ├── AGENTS.md                    # 完整项目文档
│   ├── app/main.py                  # FastAPI 入口；启动副作用集中于 lifespan
│   ├── app/dependencies.py          # 统一鉴权依赖：get_current_user / require_admin
│   ├── app/core/responses.py        # api_ok / api_page 统一响应工具函数
│   ├── app/api/v1/chat.py           # 旧兼容导出层（核心路由已拆分）
│   ├── app/api/v1/quote.py          # /chat SSE + confirm_push
│   ├── app/api/v1/materials.py      # 旧 materials 只读/退役保护；写入、回滚、sync_milvus 已废弃
│   ├── app/api/v1/auth.py           # 登录接口
│   └── .env                        # ZHIPU_API_KEY（不提交 git）
└── rag_docker/
    ├── docker-compose.yml           # RAG 镜像构建/旧环境参考编排
    ├── Dockerfile                   # python:3.10-slim + jieba
    ├── hybrid_searcher.py           # 混合检索（向量+BM25+RRF）
    └── rag_api_service.py           # RAG 服务入口
```

## 核心配置

- 应用 API / Worker：`10.240.10.10` / `10.240.10.11`；API 仅发布 `127.0.0.1:9000`
- N8N：`http://n8n:5678/webhook/budget-calc-no-rag` / `budget-push`，固定容器地址 `10.240.10.12`
- MySQL / Redis / MinIO / RAG：`ai-mysql` / `quote-redis` / `quote-minio` / `rag-service`
- Milvus：只在内部 Docker 网络提供服务，集合别名 `enterprise_quotation_rag`（蓝绿：`quotation_blue` / `quotation_green`）
- 向量模型：`maidalun1020/bce-embedding-base_v1`，768维，COSINE，HNSW
- 正式依赖不得重新写回 `192.168.88.128`；原 CentOS 仅用于观察期回滚

## 测试与上线验证规则

- 本地功能修改完成、准备上线前，默认只执行与本次改动直接相关的专项测试、必要的相邻接口/契约回归，以及受影响前端的构建或后端语法检查；非必要不得运行后端全量测试。
- 只有在改动涉及共享核心配置、鉴权/RBAC、数据库公共底座、跨业务迁移、全局依赖升级、大范围重构，专项测试暴露系统性风险，或用户明确要求时，才运行全量测试；运行前说明必要性和预计范围。
- 任何涉及识图、OCR/视觉解析、图纸识别，或报价资料研判 Agent 的测试、评测、真实样例运行和模型调用，必须先询问用户是否执行；得到明确确认后才能开始。未确认时只允许完成代码修改和不触发上述链路的静态检查。
- 报价资料研判 Agent 在用户明确确认“全部开发完成并允许上线”之前必须与正式发布隔离：Agent 的功能代码、API、配置、任务、数据库表/字段、Alembic revision、功能开关和相关基础设施改动均不得进入任何正式发布候选，也不得应用到 ECS。Agent 开发中需要的数据库变更只允许用于独立本地/开发数据库；即使功能开关默认关闭，也不能提前把其迁移部署到正式数据库。
- 凡涉及阿里云 ECS 控制台、正式容器、正式数据库、备份、迁移、重启、镜像装载或服务切换，均由用户手动操作；Codex 只提供经过核对的命令、预期结果、停止条件和回滚步骤，并根据用户返回的输出继续验收。

## 当前完成状态

- 旗胜投标机会研判 Pure Agent 的 B01—B05、V601—V608、C01、C02-1—C02-4 与 C03-1—C03-4 已完成；C03-4 独立专项 `6 passed / 0 failed`（1.65 秒），C03/C02/C01 进程内 SQLite 相邻回归 `38 passed / 0 failed`（3.89 秒）。恢复仅消费通过 Profile、Registry、Authorization、Source Heads、Cancellation、Effect/Action/Artifact Hash 与 Budget Settlement 复验的原结果；`retry_safe`/`reconcile` 只报告、不自动重放。旧 Action、缺结果正文或预算未结算均 fail closed。隔离开发 head 仍为未应用的 `20260821_0110`，默认开关保持关闭；未运行迁移、模型、资料、RAG、真实 Tool、MCP 或外部数据库/ECS。下一开发目标待用户指定。

- 报价资料研判 Agent Phase 4D-3 v0.1-r62 已完成真实业务闭环：完全隔离副本使用资料包v3、香港中心307页真实PDF、RQ2-B/本地BCE和DeepSeek运行成功；16槽Comparison Baseline为`1 supported / 6 partial / 9 unknown`，经真实失败与API-43 Checkpoint恢复后`27/27 Task`、`34 Model`、`23 Tool`、`95 Checkpoint`、Run Validation `52/52`通过，6 Claim/6 Atom引用，模型成本`19546` micro-USD。决策由历史合成数据`no_bid`纠正并稳定为真实证据下`insufficient/hold`，七项硬门均unknown；新RC `mvp-rc-20260818091440-c21d8d4b3bf9`以`accepted_with_follow_up`冻结。修复Executor旧`enterprise_snapshot_id`与新`comparison_baseline_id`输出引用不兼容，Phase4D-3专项`12 passed`。9017现为view-only、写请求403且数据库Hash不变；未调用OCR/视觉/外部MCP，9015/ECS未改，开关默认false，0108不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4d3-fact-verification-comparability-20260818.md`。

- 报价资料研判 Agent Phase 4D-2 v0.1-r60 已完成真实企业资料包v3、Business Baseline与“香港中心”七项硬门复验：19个Evidence Item明确映射I01—I05、I06—I11保持unknown；真实Snapshot将I01—I05保守冻结为partial，Business Baseline `enterprise-business-20260818030437-cb6c17be2ff6` / Hash `66d001bb...c10c`为`verified_with_follow_up`。同一307页真实PDF以RQ2-B、本地BCE和DeepSeek V4 Flash重跑成功：27/27 Task、33 Model、22 Tool、93 Checkpoint、Run Validation 51/51通过，10 Claim/28条权威引用，成本`18733` micro-USD。Decision由合成企业数据的`no_bid`纠正为`insufficient`：HG02由fail改unknown，HG04/HG07由pass改unknown，七项均unknown且无明确fail，准确反映现有资料仍缺官方状态、严格近五年/履约相似性、逐人证书、资金/担保、合规与禁投风险权威核验。9015已恢复view-only，Worker/模型关闭、写请求403；9014源资料包和ECS未改，未调用OCR/视觉/外部MCP。详见 `AI_Middle_Office/docs/bid-assessment-phase4d2-enterprise-evidence-import-20260817.md`。

- 报价资料研判 Agent Phase 4D-1 v0.1-r59 已完成本地隔离收口：I01—I11逐槽业务核验、不可变Enterprise Business Baseline、Run Bootstrap Baseline Hash和历史RC→新Decision/HG01—HG07复验完成合同/Schema、0106升降级与0083—0106拓扑、核心与相邻回归，共`239 passed / 0 failed`。一次性9010动态验证冻结/幂等、Candidate Hash漂移409和Bootstrap权威Hash绑定；合成确定性全链Run/Report/Validation成功，11槽显式unknown时七项硬门不误判、Decision保持`insufficient`。浏览器Preflight/SSE/Trace/Report正常，缺历史RC或Atom权威按设计fail-closed；9010已停止、9003未改。未用真实企业数据/PDF/BCE/OCR/视觉/生成模型/外部MCP/ECS，head为`20260817_0106`，默认关闭且不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4d1-business-baseline-decision-revalidation-20260817.md`。

- 报价资料研判 Agent Phase 4C-3 v0.1-r57 已完成首次真实 PDF 业务验收并实际冻结本地 MVP RC：“香港中心”307页真实 PDF 以 RQ2-B、固定本地 BCE 和 DeepSeek V4 Flash 跑通，Run/Report/Validation 成功，27 Task、88 Attempt、36 Model、25 Tool、99 Checkpoint，生成3 Claim/12 Atom 引用；模型账本`281339/8556` Token、`26401` micro-USD。RC 14项系统检查全通过、Atom-only违规0，以`accepted_with_follow_up`冻结为`mvp-rc-20260817084759-f77d02eded07`；幂等重放和同库view-only 403/前端禁写已验证，本轮直接专项`31 passed / 0 failed`。首次实跑中 Search Child 混入候选被事实权威正确拒绝，已以 Gateway citable-candidate 整条过滤修复，未放宽 Atom 证据门。当前企业快照仍是隔离演示数据，Parse 84分/`review_required`且未做OCR/视觉，因此`no_bid`不是真实投标决策；未调用外部MCP/生产Milvus/ECS，head保持`20260817_0105`，默认关闭且不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4c3-real-business-acceptance-mvp-rc-20260817.md`。

- 报价资料研判 Agent Phase 4C-2 v0.1-r54 已完成本地隔离收口：零持久化 Baseline Validate、来源/有效期/partial/unknown、Diff/稳定Hash、Candidate Hash漂移围栏和HG01—HG07 Acceptance已接入Runtime Lab。合同/Schema、核心、Phase4C-1/Preflight/API-41/SSE相邻及0083—0104迁移拓扑自动矩阵共`191 passed / 0 failed`。一次性9005 execute动态验证预览不落库、错误Hash 409、正确Hash冻结与幂等重放、11/11槽ready、HG02—HG07 ready且HG01 deferred_tender；浏览器验证候选变化立即使冻结失效。同库view-only的Worker/模型/写权限关闭，两个写接口403、历史快照可读、前端写按钮禁用且控制台0 error；9005已停止、9003未改动。无新迁移，唯一head保持`20260817_0104`，默认关闭，不改旧`bid_intake_*`，不得应用到ECS；未使用真实PDF/OCR/视觉/Embedding/Reranker/生成模型/外部MCP或外部环境。详见 `AI_Middle_Office/docs/bid-assessment-phase4c2-enterprise-baseline-acceptance-20260817.md`。

- 报价资料研判 Agent Phase 4C-1 v0.1-r52 已完成代码、机器合同与本地隔离专项验证：I01—I11 以内容寻址对象冻结，0104 记录 SnapshotRecord→FactAssertion 血缘；P1 确定性企业事实物化、统一 Resolve、HG01—HG07、Decision/Claim/Report 与 Preflight v2 已闭环。最终合同/迁移/核心及相邻自动矩阵 `31 passed / 0 failed`；两次合成 TXT 全链均为 27/27 Task、Run/Report/Validation succeeded。supported 快照生成11 Fact/Link；partial+过期快照仅生成10 Fact/Link，I02保持partial、I05保持unknown。动态 view-only POST 返回403且数据库SHA-256不变，浏览器上传/快照写按钮禁用且无错误。唯一开发head为`20260817_0104`，开关默认false，不改旧`bid_intake_*`；未使用真实PDF、OCR/视觉、Embedding/Reranker、生成模型或外部MCP，不得应用到ECS。9004当前以完全隔离view-only运行。详见 `AI_Middle_Office/docs/bid-assessment-phase4c1-enterprise-capability-hard-gates-20260817.md`。

- 报价资料研判 Agent Phase 4B-5 v0.1-r50 已完成 Execute Preflight、前端 readiness、提交前进程级 Authority Fingerprint 再确认及 API-41/42/43 取消/Checkpoint重试操作面。Preflight 不返回密钥、绝对路径或正文；view-only 用禁用哨兵阻断父进程与`.env`真实Key，浏览器不能提升权限。自动矩阵`22 passed / 0 failed`，Python/PowerShell/JSON/Vite通过；动态 execute 仅创建1 Assessment且0 Run/Model/Tool，同库view-only四种写方法403且哈希不变。9003已升级为新版RQ2-B view-only，0 blocker、密钥围栏ready、5个历史Run可读，历史库SHA-256仍为`1EFC35CB...53942`；浏览器写按钮禁用且无错误。无新迁移，head保持`20260815_0103`；未运行PDF/OCR/视觉/Embedding/Reranker/生成模型/外部MCP，不连接外部环境，不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4b5-runtime-preflight-operations-20260817.md`。

- 报价资料研判 Agent Phase 4B-4 v0.1-r49 已完成本地 Runtime Lab `view-only/execute` 双模式收口：默认 view-only，服务端硬阻断非安全 HTTP 方法，不建表、不启 Worker、不要求模型密钥；execute 需显式选择且 DeepSeek Key fail-closed。Capability/前端冻结 access/write/worker/model/retrieval readiness，启停脚本增加隔离 Lab、健康自校验和 PID 缺失安全回退。自动矩阵 `8 passed / 0 failed`，Vite `2235 modules`；动态专项验证 execute 201且0 Run/0 ModelCall、view-only四种写方法403、Profile不匹配拒绝以及临时库/历史库SHA-256不变。当前9003为view-only；未调用PDF/OCR/视觉/模型/外部MCP，无新迁移，head保持`20260815_0103`，不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4b4-local-runtime-access-modes-20260817.md`。

- 报价资料研判 Agent Phase 4B-3 v0.1-r48 已完成本地真实资料可演示 MVP：隔离 localhost/SQLite/本地对象目录控制平面中，以三份 Development 真实 PDF（383页）、固定本地 BCE exact-COSINE、RQ2-B 融合、Evidence MCP 与获授权联网的 DeepSeek V4 Flash 跑通 P0—P4 全链；3/3 Run succeeded、3/3 Report ready、78/78 Task succeeded、3/3 Run Validation passed，形成33条 FactAssertion、21条Claim、83个Atom引用与288个Checkpoint。105次模型调用总账`765433/21158` Token、`62962` micro-USD（约`$0.062962`）。Search/Read 24KiB边界、Atom-only引用、空候选安全finish和Checkpoint恢复已收口。三份Parse均因禁用OCR/视觉而为review_required；企业快照为合成数据，实时链因本机缺冻结Reranker snapshot使用RQ2-B而非RQ2-C，正式Holdout未运行。当前可用于本地演示/简历，不是生产发布；无新迁移，head保持`20260815_0103`，默认关闭，不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4b3-real-pdf-resume-mvp-20260816.md`。

- 报价资料研判 Agent Phase 4B-2 v0.1-r47 已完成真实 DeepSeek V4 Flash + 本地 Evidence MCP 的隔离合成 MVP-1 全链：26/26 Task、31/31 ModelCall、20/20 Tool、51/51 Run Validation Check 通过，Run succeeded、Report ready；12 FactAssertion/12 EvidenceLink、18 ResolvedFact、88 Checkpoint。Gateway 权威 ToolCall ID、检索提示元字段剥离、RFC3339 Z、CNY无损表示规范化和失败响应安全Token/费用账本已收口；总账`170165/5730` Token、`11430` micro-USD，一次被拒Fact响应费用也被保留并安全重试。直接相关专项`110 passed / 0 failed`。仅合成TXT，不含真实PDF/OCR/视觉/外部MCP/生产Milvus/ECS；默认关闭，无新迁移，head仍为`20260815_0103`，不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4b2-deepseek-isolated-mvp1-full-chain-20260816.md`。

- 报价资料研判 Agent Phase 4B-1 v0.1-r46 已完成 DeepSeek V4 Flash 受控 Model Provider、配置、合同、本地专项与最小官方真实复烟：冻结官方 Host、`deepseek-v4-flash`、非思考 JSON 与价格版本，复用 Phase 4A-2 Model Gateway/预算/Lease/Fencing/Checkpoint/恢复；完整授权矩阵 `141 passed / 0 failed`。首烟暴露 Action 分支污染后，已注入完整 JSON Schema、分支互斥和精确 finish 示例；强化复烟返回合法 `finish`，Schema 通过，输入/输出 `2240/42` Token，成本 `326` micro-USD，未包含业务资料。Provider 现可进入完全隔离本地 MVP-1 联调，但不代表真实文件端到端质量或生产上线完成。默认开关关闭，无新迁移，head 保持 `20260815_0103`；未使用 PDF/OCR/视觉/外部 MCP，未连接 ECS，不得应用到 ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4b1-deepseek-v4-flash-provider-protocol-20260816.md`。

- 报价资料研判 Agent RQ2 总收口 v0.1-r45 已完成合同/Schema 与 PDF-C3/RQ2-A/B/C/Evidence MCP 相邻专项 `126 passed / 0 failed`；用户已批准其余33题，三项目 Development Gold 现为60题/156目标/19类别，Dataset Hash `50857d3f...c963`，并建立不可变 Development Snapshot `8ad34718...0a`。在完全隔离本地环境以固定 BCE Embedding `9c0d82af...` / Reranker `eb7650fc...` 正式运行 RQ2-B→RQ2-C：Candidate Macro Hit@5/Recall@5/MRR@5/NDCG@5 为 `0.966667/0.886667/0.763055/0.731853`，Hit@8/Recall@8/Atom Read 为 `0.983333/0.9225/0.838055`，相对 RQ2-B 逐题质量退化0、Top-8恢复1、Atom-only违规0、全不变量通过。但 Development 准出失败：Macro Citable Target Availability `0.955855 < 0.98`（6/156目标未被正式评分器映射为可引用 Atom），Paired Search Delta P95 `3705.179ms > 2500ms`；Candidate Search P95 `4044.799ms` 本身仍低于4500ms。禁止建立 Pre-Holdout Freeze 或运行 Holdout；下一步须在 Development 上修复引用覆盖与重排增量延迟，产生新 Candidate/Snapshot 后重新验证。泰丰是历史已暴露 Development，不得再作未见集。未调用OCR、视觉、生成模型、外部MCP、生产Milvus或外部环境；无新迁移，head保持 `20260815_0103`，不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-rq2-closeout-cross-project-gold-holdout-protocol-20260816.md`。

- 报价资料研判 Agent RQ2-C 已完成 v0.1-r44 代码与本地隔离专项验证：冻结 RQ2-B Top-20 Child，以固定 revision 本地 BCE Cross-Encoder 对 q1 原查询与 C3 retrieval_text 打分；保护Top-1/词法锚点，只允许最多2次正分差尾部替换，零promotion有序结果不变。Evidence MCP v7 Search不可引用、Read仍Atom-only，历史v4/v5/v6 Adapter冻结。核心与相邻运行/迁移无重复矩阵共 `367 passed / 0 failed`；“香港中心”25题四臂A/B中，RQ2-B→RQ2-C Top-5保持 Hit/Recall `0.96/0.90`，Top-8由 `0.96/0.90` 提升为 `1.00/0.94`，`HKC-C3-020` 正确Child从Fusion rank11提升到最终rank7，4次promotion、其余21题恒等、逐题零退化，Atom-only违规0、重放一致；冻结Worker依赖下CPU Search P95 `1526→3584ms` 留作性能优化。无新迁移，head保持 `20260815_0103`，开关默认关闭；未调用OCR/视觉/生成模型/外部MCP或生产Milvus，未连接外部环境，不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-rq2c-lightweight-rerank-protocol-20260815.md`。

- 报价资料研判 Agent RQ2-B 已完成 v0.1-r43 代码与本地隔离专项验证：同一 RQ1-C Query Plan 下取得 RQ1-D/RQ2-A Top-40 Child，以稳定 `retrieval_child_key` 去重，按词法1.00/语义0.35/重合奖励0.20/k=60做 rank-only weighted RRF；Fusion Hash 纳入 Query Plan/C3 IndexSet/Lexical ProjectionSet/Semantic IndexSet，真实资料修正了合成 `child:` 与 PDF-C1 `chunk:` Key 兼容缺口。Evidence MCP v6 Search仍不可引用、Read仍Atom-only，历史v4/v5 Adapter冻结；合同、C3、语义/词法、Phase 3E/3F/API-41/SSE和迁移拓扑无重复矩阵 `245 passed / 0 failed`。“香港中心”同一25题共享权威三组 A/B：RQ1-D/RQ2-A/RQ2-B Hit@5 `0.92/0.68/0.96`、Recall@5 `0.86/0.60/0.90`、Atom Read `0.82/0.60/0.86`、MRR `0.584/0.478/0.651333`；融合补回2题但1题Top-5/Top-8回退，下一步进入RQ2-C轻量重排。无新迁移，唯一head保持 `20260815_0103`，默认开关关闭；A/B使用固定本地BCE exact-COSINE，未执行生产Milvus、OCR/视觉、生成模型或外部MCP，未连接外部环境，不得应用到ECS。详见 `AI_Middle_Office/docs/bid-assessment-rq2b-candidate-fusion-protocol-20260815.md`。

- 报价资料研判 Agent RQ2-A 已完成 v0.1-r42 代码与本地隔离专项验证：新增默认关闭的 Child-only SemanticIndex/Entry/Head、64 Child 分批 Heartbeat、Lease/Fencing、稳定 namespace/request id、Provider Hit 身份/Hash/去重门、发送后未知结果恢复，以及 semantic-only Evidence MCP v5；Search 仍只返回不可引用 Child，Read 仍 Atom-only，RQ2-B/RQ2-C 才做词法语义融合和轻量重排。合同/Schema/配置、0103迁移、状态机、历史 Adapter、Phase 3E/3F/API-41/SSE无重复矩阵共 `190 passed / 0 failed`。“香港中心”同一25题/共享 ParseHead/IndexHead 的真实固定 BCE exact-COSINE A/B 中，RQ1-D→RQ2-A semantic-only 的 Hit@5 `0.92→0.68`、Recall@5 `0.86→0.60`、Atom Read `0.82→0.60`；语义补回2个词法零命中题但11题回退，证明下一步必须进入 RQ2-B 受控融合而非替换词法。1244个 Child 均生成768维/64位Hash Entry，Atom-only违规0、重放一致。线性 revision/head 为 `20260815_0103`，所有开关默认关闭；本机无安全可用的独立 Milvus daemon，真实 A/B 明确使用 `isolated-bce-exact-cosine`，生产 Milvus Adapter 首次真实 daemon 联调仍为部署前门禁。未调用 OCR/视觉/生成模型/外部 MCP，未连接外部环境，不得应用到 ECS。详见 `AI_Middle_Office/docs/bid-assessment-rq2a-semantic-retrieval-protocol-20260815.md`。

- 报价资料研判 Agent RQ1-D 已完成 v0.1-r41 代码与本地隔离专项验证：从 C3 Child/Atom locator 派生 Heading/Table Key/Table Value/Table Row/Body 五通道，以稳定 evidence key 生成 projection hash/tie-break；RQ1-C `field_codes/answer_shapes` 驱动 BM25F，Child BM25 1.0 保留为基线，弱字段0.005/表格强结构0.10作有界 RRF tie-breaker，并加入65%/85%模板降权、q1 0.45 Anchor、Parent 0.20和同 Parent最多2个 Child。Evidence MCP v4 与历史 v2/v3 Adapter、C3 Index/Atom-only Read 隔离兼容；合同/C3/配置、Phase 3E/3F/API-41/SSE及0083—0102迁移拓扑共 `205 passed / 0 failed`。“香港中心”同一25题/共享 ParseHead/IndexHead A/B：Hit@5 `0.68→0.92`、Recall@5 `0.62→0.86`、Atom Read `0.62→0.82`、零命中 `8→2`，6题提升、逐题零回退、Atom-only违规0、重放一致；P95 `1195→1708ms` 留作后续性能优化。无新迁移，唯一 head保持 `20260814_0102`，所有开关默认关闭；未调用 OCR/视觉/模型/Embedding/向量服务/外部 MCP，未连接外部环境。详见 `AI_Middle_Office/docs/bid-assessment-rq1d-field-aware-lexical-protocol-20260815.md`。

- 报价资料研判 Agent RQ1-C 已完成 v0.1-r40 代码与本地隔离专项验证：新增默认关闭的 `bid.evidence.query-plan.v2` / `bid-evidence-query-optimizer-profile-v1-rq1c`，原查询固定 q1，按并列主体、通用投标字段别名与答案形状扩展并执行旧 Planner atomic/fact-slot；最多6条、稳定指纹去重和分类型权重，经 Child BM25 + Parent 0.35 辅助 weighted RRF。新 Search Dispatch 冻结为 `bid-evidence-mcp-rq1c-search@v3-rq1c-query-optimizer`，历史 v1 Query Plan/v2 Adapter/C3 Index 不变；合同、配置、Evidence MCP、Adapter、Phase 3E/3F/API-41/SSE 与迁移拓扑无重复矩阵共 `202 passed / 0 failed`。同一“香港中心”25题共享 ParseHead/IndexHead A/B 中平均查询 `1.08→3.84`、Hit@5 `0.44→0.72`、Recall@5/Atom Read Recall@5 `0.38→0.66`、Hit@8 `0.48→0.84`，零命中题 `14→7`；Atom-only 违规保持0、重放一致，但仍低于 Top-5 建议门槛且 P95 Search `397→1065ms`，下一步进入 RQ1-D 字段感知词法召回。复用现有表，不新增迁移，唯一 head 保持 `20260814_0102`；未调用 OCR/视觉/模型/向量服务/外部 MCP，未连接外部环境。详见 `AI_Middle_Office/docs/bid-assessment-rq1c-deterministic-query-optimizer-protocol-20260815.md`。

- 报价资料研判 Agent RQ1-B 已完成 v0.1-r39 代码与本地隔离专项验证：新增默认关闭的 v4 Parser Profile 与 `bid.parse.quality.v1`，按 Native readiness/Structural coherence/Citable integrity/Warning hygiene 四维确定性评分生成 pass/review_required/blocked；partial 最高84分、硬阻断最高39分。报告作为首条安全 Warning 进入 Parse result hash，Worker 校验血缘/状态/Hash；blocked 阻断 C3 Index、Lot Detection 和 Phase 3 Run Bootstrap。旧 v1/v2/v3 Profile 和 Retrieval input hash 分支不变；复用现有表，不新增迁移，唯一 head 保持 `20260814_0102`。合同/Schema/配置、Parser/Worker/C3、Phase 3A/API/Evidence MCP 与迁移拓扑无重复矩阵共 `179 passed / 0 failed`；同一“香港中心”25题 A/B 将失真的 `partial + high/100` 修正为 `partial + medium/84 / review_required`，Parent/Child/Atom 和检索指标保持不变，Hit@5/Recall@5/Atom Read Recall@5 仍为 `0.44/0.38/0.38`。未调用 OCR/视觉/模型/向量服务/外部 MCP，未连接外部环境；下一步进入 RQ1-C Query Optimizer。详见 `AI_Middle_Office/docs/bid-assessment-rq1b-parse-quality-gate-protocol-20260815.md`。

- 报价资料研判 Agent RQ1-A 已完成 v0.1-r38 代码与本地隔离专项验证：新增独立默认关闭的 v3 Parser/v2 Layout+Chunk Profile，以跨页重复、页边几何和通用数字折叠抑制页眉页脚，按顶层章节聚合微型小节、聚合连续表格行，并将 heading 原文保留为可引用 Atom。合同/配置/降噪/Hash/聚合/证据门/旧 Profile 兼容及 Phase 2/C3/Evidence MCP 相邻回归无重复矩阵共 `48 passed / 0 failed`；“香港中心”25 题 A/B 中可引用目标 `88.10%→100%`、Hit@5 `0.32→0.44`、Recall@5 `0.24→0.38`、Atom Read Recall@5 `0.16→0.38`，逐题无上述指标回退但仍未达最低建议门槛，下一步进入 RQ1-B Parse Quality Gate。复用现有表，不新增迁移，唯一 head 保持 `20260814_0102`；未调用 OCR/视觉/模型/向量服务/外部 MCP，未连接外部环境。详见 `AI_Middle_Office/docs/bid-assessment-rq1a-structure-aggregation-protocol-20260815.md`。

- 报价资料研判 Agent PDF-C2/C3 真实资料 Silver 检索质量基线已完成 v0.1-r37：经用户授权，在完全隔离的本地 SQLite 环境对 1 份 307 页真实招标 PDF 运行 PDF-C2→C1→Phase 2→C3 Index→Evidence MCP v2 全链，并建立 25 题/42 个 phrase-anchored 目标的单审阅者 Silver 集。结果 `Hit@5=0.32`、`Target Recall@5=0.24`、`MRR@5=0.1667`、`Atom Read Target Recall@5=0.16`；5/42 目标没有可引用 Atom，88.01% Child 低于 220 token，1432 条结构警告仍被投影为 `high / 100`。Atom-only Read 违规为 0、确定性重放一致，说明协议安全通过但业务检索质量未通过。下一步 Retrieval Quality-1 按结构/可引用性、质量分、确定性 Query Optimizer、字段感知词法召回、语义召回/重排推进；Silver 需业务复核为 Gold。未调用 OCR/视觉/模型/向量服务/外部 MCP，未连接 ECS/CentOS/真实 MinIO/Redis。详见 `AI_Middle_Office/docs/bid-assessment-pdf-c3-real-document-silver-baseline-20260815.md`。

- 报价资料研判 Agent PDF-C3 已完成 v0.1-r36 代码增量与本地隔离专项验证：新增 `bid.evidence.retrieval-index.v1` / `bid-evidence-retrieval-profile-v2-role-aware` / Evidence MCP v2，把 PDF-C1/C2 的 Parent→Child→Atom 角色提升为检索强协议；Search 只返回不可引用 Child，Parent 仅以 0.35 权重辅助 BM25+RRF，Read 只返回同 Parent 范围内可引用 Atom。新增文档级不可变 RetrievalIndex/Entry/Head 权威、ParseHead/Profile/Manifest/Hash fail-closed 失效规则、C3 配置门禁、历史 v1 Adapter 围栏，以及线性 revision `20260814_0102`；默认开关关闭，不改变既有 9001 行为。PDF-C3/合同/配置/0102 迁移主矩阵 `165 passed`，PDF-C2/C1/Phase 2 相邻回归 `28 passed`，Phase 3E/3F/MVP-1/API-41/SSE 相邻回归 `18 passed`，授权矩阵共 `211 passed / 0 failed`。全部仅使用合成结构数据和隔离 SQLite/Alembic 环境；未读取真实 PDF，未调用 OCR/视觉/模型/向量服务或外部 MCP，未连接外部环境。详见 `AI_Middle_Office/docs/bid-assessment-pdf-c3-role-aware-retrieval-protocol-20260814.md`。

- 报价资料研判 Agent PDF-C2 已完成 v0.1-r35 代码增量与本地隔离专项验证：新增 `bid.pdf.native-layout.v1` / `bid-pdf-native-layout-profile-v1` / `bid-document-parser-profile-v2-pdf-native-layout`，以 pdfplumber 原生字符/Word/矢量表格层生成带 page/bbox/reading order/section path 的结构块，再唯一调用 PDF-C1 Builder 映射为现有 Phase 2 Parent→Child→Atom Evidence；结构判断只用字体、居中、通用编号、几何间距和缩进，不用业务关键词，不从文件名/MIME/parser_hint 推断 Scope。空白、扫描、低原生文本和低覆盖页只标记待 OCR，不调用 OCR/视觉/模型；新增默认关闭子开关和 v2 Profile 四项配置门禁，旧 v1 Profile 行为保持不变。复用现有 ParseUnit/EvidenceFragment/parent_id/locator_json，不新增迁移，head 保持 `20260813_0101`。PDF-C2 合成布局/配置/映射/Lot Atom 门禁 `13 passed`、机器合同 `74 passed`、Phase 2 Parse/Lot Worker 相邻回归 `4 passed`，完整授权矩阵共 `91 passed / 0 failed`；只生成并解析内存合成 PDF，未读取或渲染真实 PDF，未调用 OCR/视觉/模型/MCP，未连接外部环境。详见 `AI_Middle_Office/docs/bid-assessment-pdf-c2-native-layout-protocol-20260814.md`。

- 报价资料研判 Agent PDF-C1 已完成 v0.1-r34 代码增量与本地隔离专项验证：新增 `bid.evidence.chunk.v2` / `bid-evidence-chunk-profile-v1` 机器合同、保守确定性 Token 估算、Section Parent→Retrieval Child→Evidence Atom 三层构建、章节/Clause/表格硬边界、正文动态聚合、仅超长块 80 Token overlap、确定性上下文索引文本及稳定 Key/Hash；只有 Atom 可引用，Parent/Child/context prefix 均不是事实证据。PDF-C1 本身仍是不读取 PDF 的纯结构块 Builder，现由默认关闭的 PDF-C2 v2 Profile 单向调用；不调用 OCR/视觉/模型/MCP，不改变默认 v1/9001 运行结果。未新增迁移，唯一 head 保持 `20260813_0101`。合同/Token/边界/overlap/Parent-Child-Atom/Span 覆盖/稳定 Hash 专项 `12 passed`，Phase 2 Parse Worker 结果合同相邻回归 `4 passed`；全部只使用合成结构块和隔离测试数据库。详见 `AI_Middle_Office/docs/bid-assessment-pdf-c1-structure-chunk-protocol-20260814.md`。

- 报价资料研判 Agent 可运行 MVP-1 已完成 v0.1-r33 完全隔离本地运行与专项验证：保留 Assessment→SHA-256 上传→不可变 Manifest→解析/标段→P0—P4→Evidence MCP→Fact/HG01—HG07/Decision/Claim/Report→Run Validation 的新数据域闭环，并新增 localhost-only `app.mvp1_local:app`、独立 SQLite/对象目录、进程内 Outbox/Worker、注入式确定性 Provider、合成样例和一键验证器；修正 MCP Adapter 持久化模式及 Provider/Task 错误码恢复。合同/迁移/Planner/LangGraph/配置 160 项与 API/Worker/事务/恢复 150 项，共 `310 passed / 0 failed`；合成 TXT HTTP 全链 `Run=succeeded`、`Report=ready`。当前代码唯一 Alembic head 仍为 `20260813_0101`，默认开关仍关闭；9001 仅为本地实验环境，未运行真实资料、OCR/视觉、真实模型或外部 Tool，未连接 ECS/CentOS/真实 MinIO/Redis，不得应用到 ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4-mvp1-vertical-slice-protocol-20260813.md`。

- 报价资料研判 Agent 工程可视化 MVP-0 已完成 v0.1-r31 代码增量与允许范围内静态检查：新增独立 `/admin/bid-assessment-runtime-lab` 实验台、`bid.runtime.trace.v1` 机器合同和 owner/admin 只读 Trace API，把 Run、Plan/SkillBinding、Task DAG、Attempt/Lease/Fencing、Context Manifest、Model/Tool Gateway、Result Store、Checkpoint 与 Validation/Convergence 投影为图谱、时间线和检查面板；Prompt/Context/模型动作/Tool 参数与结果正文及思维链均不返回。页面无 Run 时只展示明确标记的协议预览，不冒充真实执行；真实快照由默认关闭 `FEATURE_BID_ASSESSMENT_PHASE4_MVP0_TRACE=false` 隔离，SSE 可选且执行器子开关可保持关闭。不新增迁移，当前代码 head 保持 `20260813_0099`；Python 语法/Schema JSON/diff check 通过，Vite 生产构建 `2235 modules` 通过。尚未获准运行 MVP-0 API/ACL/ETag/SSE/浏览器专项测试，不连接数据库或外部环境，不调用模型、MCP、OCR/视觉或 Tool，不修改旧 `bid_intake_*`，不得应用到 ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4-mvp0-runtime-lab-20260813.md`。

- 报价资料研判 Agent Phase 4A-2 已完成 v0.1-r30 代码增量与本地隔离专项验证：新增受控 ModelCall/Provider Attempt/不可变 ModelResult 权威、冻结 ModelProfile/Prompt/Context/TaskContract/SkillBinding 的请求 Envelope、成本/Token/迭代预算、Lease/Heartbeat/Fencing、发送后未知结果与未领取/过期调用恢复，以及单 Task 单动作、无内置 Checkpointer 的有界 LangGraph Executor；模型动作只能交给 Model Gateway、Phase 3E Tool Gateway 或形成候选 Checkpoint，不直接写 Fact/Claim/Decision/Report。Run Validator v4 纳入模型调用全血缘。合同/0099/有界执行、Phase 4A-1 与 Phase 3C—3G/API-41、SSE/Outbox/事务/幂等共 `189 passed / 0 failed`；新增默认关闭子开关与线性 revision `20260813_0099`，当前代码 head 为 `20260813_0099`。仅使用本地 SQLite 和显式注入测试 Provider，未调用真实模型、MCP、OCR/视觉、外部工具、真实样例或真实存储，未连接外部环境，不修改旧 `bid_intake_*`，不得应用到 ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4a2-model-langgraph-executor-protocol-20260813.md`。

- 报价资料研判 Agent Phase 4A-1 已完成 v0.1-r28 代码增量与本地隔离专项验证：实现 preliminary/reanalysis Run 的 P0—P4 确定性 Plan Continuation、`bid.plan.continuation_requested.v1`、Revision 同事务 supersede/commit、跨 Revision Dependency、最终阶段 Validation 门禁，以及仓库版本化 8 个 Skill artifact 和 Plan/Task 的 SkillBinding/allowed-tools Hash 冻结；Run Validator v3 覆盖所有 Revision 联合血缘。合同/Planner/事务恢复/历史 TaskContract/API-41/SSE/迁移拓扑共 `173 passed / 0 failed`。新增默认关闭 Phase 4 总/子开关与线性 revision `20260812_0098`，当前代码 head 为 `20260812_0098`；未调用模型、MCP、OCR/视觉、外部工具、真实样例或真实存储，未连接外部环境，不修改旧 `bid_intake_*`，不得应用到 ECS。详见 `AI_Middle_Office/docs/bid-assessment-phase4a1-plan-continuation-skill-binding-protocol-20260812.md`。

- 报价资料研判 Agent Phase 4 可落地执行架构 v0.1-r26 已冻结：继续以 Phase 3 作为唯一外层控制平面，以 LangGraph 执行单 Task 有界动作，以 MCP 作为 Tool Gateway 后的新数据域只读 Adapter；优先复用旧 Agent 的确定性 Query Planner/检索路由、BM25 + 向量 + RRF、MCP transport、证据门和评测方法，不复用旧 `bid_intake_*` 权威表或 Checkpoint。Phase 4A-1 Plan Continuation + SkillBinding 与 Phase 4A-2 Model Gateway + LangGraph Executor 均已完成本地隔离专项验证；后续按 Evidence MCP/检索、事实权威、HG01—HG07/Decision/Claim/初筛报告 MVP-1 推进。详见 `AI_Middle_Office/docs/bid-assessment-phase4-landable-agent-architecture-20260812.md`。
- 正式部署已收敛为单台阿里云 ECS：Nginx、FastAPI API、Celery Worker、MySQL、Redis、MinIO、n8n、Dify、RAG 与 Milvus 运行在同一私有 Docker 网络，API 仅发布到 `127.0.0.1:9000`。原 CentOS 正式服务保持停止，只保留冷备/回滚数据，正式依赖不得重新写回原 CentOS。
- 当前保留的业务范围：项目/对话报价、成本数据库、企业定额与账户定额、智能组价、预算项目计价、项目进度/项目任务、报价资料研判 Agent 和智能助手。执行任务/执行速度、会议纪要、商务台账和成本测算闭环已退役，不得恢复旧入口、旧表或旧权限。
- 当前仓库数据库迁移唯一 head 为 `20260817_0106`；新增数据库字段、表或受约束枚举必须继续使用 Alembic，禁止依赖 `AUTO_CREATE_TABLES` 或启动时兼容迁移。应用自动迁移保持关闭。0104—0106 仅属于隔离开发中的报价资料研判 Agent，不得应用到 ECS。
- 报价资料研判 Agent 新数据域 Phase 3A 已完成 v0.1-r17 协议、Run Bootstrap 代码与本地隔离专项验证：从 `bid.plan.requested.v1` 原子选择当前 Scope/Manifest、最新合法 frozen 企业快照和六类唯一 active 配置，使用数据库 UTC 时间固化 `input_fingerprint/input_hash`，创建 Run、更新 Assessment active 指针并写 `bid.run.created.v1`、审计和 processed marker；输入未就绪时不创建占位 Run且不写 marker，由维护扫描恢复。API-40/API-41 已实现手动 reanalysis、幂等/ETag/ACL 与不暴露内部 Task DAG 的进度投影。Phase 3A 不执行 Planner、模型或 Tool，复用既有 `0084`—`0086` 骨架且不新增迁移，代码 head 保持 `20260811_0093`；四个相关运行开关默认 false，旧 `bid_intake_*` 未修改。获准专项共 `82 passed`（合同 64、Phase 3A 核心 3、API-03/API-31/API-32 相邻 8、事务/Outbox/SSE 运行服务 7）；未运行迁移拓扑、真实样例、OCR/视觉解析或模型调用。
- 报价资料研判 Agent 新数据域 Phase 3B 已完成 v0.1-r18 协议、代码与本地隔离专项验证：新增 49 项标准任务运行时注册表、无模型初始 PlanProposal、九项确定性 DAG 校验、可复现 Plan envelope、`bid.run.created.v1` 幂等消费、PlanRevision/8 Task/7 Dependency 原子提交、`bid.plan.committed.v1` / 首个 `bid.task.ready.v1`、审计和维护扫描恢复。首批 DAG 最大动态深度 3，只复用 Phase 2 权威 Manifest/ParseHead/Scope，不从文件名、MIME 或 parser_hint 推断标段；不创建 Attempt、不调用 OCR/视觉/模型。独立开关 `FEATURE_BID_ASSESSMENT_PHASE3_PLANNER=false` 默认关闭，复用 `0085/0086` 且不新增迁移，代码 head 保持 `20260811_0093`，旧 `bid_intake_*` 未修改。获准专项共 `133 passed`（合同 65、Planner/DAG 5、Plan Commit 与 API-40/API-41 相邻 4、迁移拓扑 47、事务回滚/维护恢复 2、Outbox/processed marker/SSE 运行服务 10）；未运行真实样例、OCR/视觉解析、评测或模型调用，不得将本增量应用到 ECS。
- 报价资料研判 Agent 新数据域 Phase 3C 已完成 v0.1-r19 协议、Task Runtime Control Plane 代码增量和本地隔离专项验证：从 committed Plan、Run frozen versions、Scope 和 49 项注册表 fail-closed 重构 TaskContract；实现 ready Task 行锁领取、单调 Attempt/fencing、180 秒 Lease、启动/心跳、不可变连续 Checkpoint、完成回执、下游依赖释放、全 DAG 完成后的 validation request、受控失败重试、过期租约恢复和 terminal Run fence。独立开关 `FEATURE_BID_ASSESSMENT_PHASE3_TASK_RUNTIME=false` 默认关闭；维护任务只回收/围栏，不主动领取任务。复用 `0085/0086`，无需新迁移，代码 head 保持 `20260811_0093`；不执行模型、OCR、视觉、工具或真实对象存储，不新增外部 API，不修改旧 `bid_intake_*`。Phase 3C 合同、状态机、API-41/SSE 相邻回归与迁移拓扑共 `123 passed`，未连接或改动外部环境。
- 报价资料研判 Agent 新数据域 Phase 3D 已完成 v0.1-r21 协议、代码增量与本地隔离专项验证：API-42 以强 Run ETag、ACL 和幂等事务持久化取消意图，由 30 秒维护任务原子取消非终态 Task、活跃 Attempt/AsyncOperation 并收敛 Run/Assessment；API-43 只允许当前 failed/retryable 且冻结输入未 stale 的原 Run，围栏旧执行后创建 attempt_no/fencing 单调递增的 `created` Attempt，下一次 Lease 复用该 Attempt 并返回最近不可变 Checkpoint。新增独立默认关闭开关 `FEATURE_BID_ASSESSMENT_PHASE3_RUN_LIFECYCLE=false`；复用 `0085/0086` 已有结构，并新增 `20260811_0094` 仅扩展 `bid.run.retry_requested.v1` 的数据库 Outbox CHECK，代码唯一 head 为 `20260811_0094`。授权范围内机器合同、迁移、API/状态机相邻链和运行服务共 `137 passed`；未执行模型、OCR、视觉、工具、真实样例或真实对象存储，不修改旧 `bid_intake_*`，未连接或改动外部环境。完整协议见 `AI_Middle_Office/docs/bid-assessment-runtime-brain-phase3d-run-lifecycle-protocol-20260811.md`。
- 报价资料研判 Agent 新数据域 Phase 3E 已完成 v0.1-r22 协议、Tool/Context Control Plane 代码增量与本地隔离专项验证：实现确定性 Context Manifest、服务端 Scope/冻结版本注入、严格 Tool Schema/profile/预算/幂等/HMAC scope token、不可变 Result Store、同步与 AsyncOperation/Checkpoint/新 Attempt-Fence 恢复，以及取消/重试对未完成 Invocation 的硬围栏。新增独立默认关闭开关 `FEATURE_BID_ASSESSMENT_PHASE3_TOOL_CONTEXT=false`；线性 revision `20260812_0095` 新增三张权威表及 Checkpoint Context 外键，升级和降级均有不可变血缘保护，唯一 head 为 `20260812_0095`。授权范围内合同与迁移拓扑 117、API/Phase 3C/3D 相邻链 16、Outbox/SSE/维护恢复 13，共 `146 passed`；不开放新外部 API 或执行器，未执行模型、OCR、视觉、工具、真实样例或真实对象存储，不修改旧 `bid_intake_*`，未连接或改动外部环境。完整协议见 `AI_Middle_Office/docs/bid-assessment-runtime-brain-phase3e-tool-context-protocol-20260812.md`。
- 报价资料研判 Agent 新数据域 Phase 3F 已完成 v0.1-r23 协议、受控 Tool Adapter/Executor 代码增量与本地隔离专项验证：新增唯一持久 Dispatch、DispatchAttempt Lease/Fence、稳定 provider request id、安全重放/发送后未知结果和取消/超时联动；首个 Adapter 仅为本地只读 `documents.outline`，只读取 Run Manifest 当前 ParseHead/结构化 ParseUnit，不读取原始文件或触发解析。独立开关 `FEATURE_BID_ASSESSMENT_PHASE3_TOOL_EXECUTOR=false` 默认关闭；线性 revision `20260812_0096` 新增两张派发权威表及 AsyncOperation 复合血缘，唯一 head 为 `20260812_0096`。合同、迁移、Dispatch/Adapter、事务/幂等/Lease/Fencing、Checkpoint/超时/取消恢复和 Phase 3C—3E/API-41/SSE 相邻回归共 `149 passed`。不开放新外部 API，不调用真实模型、OCR/视觉、公网、外部工具或真实对象存储，不修改旧 `bid_intake_*`，未连接或改动外部环境。详见 `AI_Middle_Office/docs/bid-assessment-runtime-brain-phase3f-tool-executor-protocol-20260812.md`。
- 报价资料研判 Agent 新数据域 Phase 3G 已完成 v0.1-r24 协议、Run Validation/Convergence 代码与本地隔离专项验证：每 Run 唯一 Validation、ValidationAttempt Lease/Fence、确定性 frozen input/Plan/Task/Dependency/Attempt/Checkpoint/AsyncOperation/Tool 血缘检查、不可变 result hash，以及 Run/Assessment/Outbox/Audit 原子 succeeded/failed/stale 收敛。独立开关 `FEATURE_BID_ASSESSMENT_PHASE3_RUN_VALIDATION=false` 默认关闭；线性 revision `20260812_0097` 新增两张验证权威表并扩展 `bid.run.stale.v1` Outbox CHECK，唯一 head 为 `20260812_0097`。Phase 3G 核心与 Phase 3C—3F/API-41/SSE 相邻回归共 `158 passed / 0 failed`；不调用模型、OCR/视觉、公网、外部工具或真实对象存储，不修改旧 `bid_intake_*`，未连接或改动外部环境。详见 `AI_Middle_Office/docs/bid-assessment-runtime-brain-phase3g-validation-convergence-protocol-20260812.md`。
- 报价资料研判 Agent Phase 3 总收口 v0.1-r25 协议、代码与本地隔离综合验证已完成：新增完整运行 Profile 与默认关闭总开关 `FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME=false`，启用时强制 V1 Runtime、Phase 3A—3G 全依赖和 Tool scope signing key；Run Validator 升级为 v2，逐行检查 Task/Checkpoint/Context/Invocation/AsyncOperation/DispatchAttempt/Result 的 Hash/Fence 血缘；API-40→本地只读 Tool Adapter→Validation→API-41/SSE 确定性端到端及综合矩阵共 `175 passed / 0 failed`。不新增迁移，唯一 head 保持 `20260812_0097`；不调用真实模型、OCR/视觉、公网、外部工具或真实对象存储，不修改旧 `bid_intake_*`，不得应用到 ECS。详见 `AI_Middle_Office/docs/bid-assessment-runtime-brain-phase3-closeout-protocol-20260812.md`。
- 目标 ECS 数据库最近一次只读确认（2026-08-10）仍为 `20260808_0082`，尚未应用 `0083`—`0101`，当前本地增量也尚未部署到正式环境。
- 任何环境升级到代码 head 前，必须重新确认实际 head，完成全量备份、SHA-256 校验和恢复演练，并以同一版本发布 API/Worker；涉及 ECS 的备份、迁移、镜像装载、重启和服务切换继续由用户手动执行。

## 账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | 已强制修改（初始 123）| 管理员 |

## 冷启动

**CentOS（先启动）**
```bash
cd /opt/rag_service && docker compose up -d
```

**Windows（自动）** — 任务计划程序 `AI_MiddleOffice` 开机自启
```
http://localhost:9000/
```

## 技术栈

| 分类 | 组件 |
|------|------|
| 后端 | FastAPI · uvicorn · SQLAlchemy · python-jose · bcrypt |
| AI | GLM-4V · DeepSeek-R1 · BCEmbedding bce-embedding-base_v1 |
| 检索 | Milvus v2.3.1 · pymilvus 2.3.6 · rank-bm25 · jieba · RRF融合 |
| 自动化 | N8N · Dify · 钉钉 Webhook |
| 基础设施 | Docker Compose · CentOS 7.9 · Miniconda · MinIO · etcd |
| 前端 | Vue.js 3 (CDN) · Element Plus · Axios |

## 协作规范

- 所有文件修改、脚本执行由 AI 自行完成并检查，只汇报结果。
- 破坏性操作（删文件、强制推送、清空数据库等）或需在 CentOS 执行的命令，先向用户申请许可。
- 操作结束简短告知"做了什么"，不做多余解释。
