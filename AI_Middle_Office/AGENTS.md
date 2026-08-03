# AI 智能报价中台 — 后端项目上下文

本文件补齐根目录 `AGENTS.md` 的引用，记录当前后端、异步任务、RAG 与部署约定。根目录摘要用于快速进入项目，本文件用于后续开发交接。

- 执行系统已按产品决定完整退役（2026-07-31）：移除执行任务、会议纪要、执行速度看板的前端入口、API、服务、模型、RBAC 模块和功能开关；新增 Alembic `20260731_0078` 仅删除专用表 `task_drafts`、`meeting_note_revisions`、`meeting_notes`、`execution_task_events`、`execution_tasks`。历史迁移 `20260514_0013` 至 `0015` 保留以维持迁移链；`project_*` 项目进度与项目任务是独立功能，继续保留。应用迁移前必须按环境规范备份数据库。
- 商务台账已按产品决定完整退役（2026-07-31）：移除 Vite 入口、商务台账 API/service/schema、事件模型、RBAC 模块和 `FEATURE_BUSINESS_LEDGER`；Alembic `20260731_0079` 删除 outbound 台账记录、`client_inquiry_events` 及 `direction/stage/next_followup_at/cancelled_*` 专用字段，并将保留的 `client_inquiries` 恢复为 Phase 2 响应速度追踪的纯 inbound 模型。迁移会清除台账历史数据，应用前必须备份数据库。
- 成本测算闭环已按产品决定完整退役（2026-07-31）：仅移除 `cost_measurement*` Vite 页面、API/service/model、RBAC 模块和 `FEATURE_COST_MEASUREMENT`；Alembic `20260731_0080` 删除 `cost_measurements`、`cost_measurement_lines`、`cost_measurement_events` 三张专属表。项目/对话报价、成本库、企业/账户定额、预算计价、报价资料研判 Agent 和智能助手不在本次范围内。迁移会清除测算历史数据，应用前必须备份数据库。
- 三项退役迁移已完成当前环境上线（2026-07-31）：升级前全量 SQL 备份为 `backups/retired_modules_pre_0080_20260731_182547.sql`（759.31 MB，SHA-256 `732CEB33858B5E076BADBE975DD9868720E543D57E8E6394A751E66A9BAE12DF`）；MySQL 已由 `20260731_0077` 升级到 `20260731_0080`，FastAPI 重启后的 9000 实际监听 PID 为 `28460`，`/health/ready=ready`，数据库和 Celery worker 均为 `ok`。PID `42692` 为已退出的启动进程。退役入口 404，报价、成本库、定额、预算计价、投标和 Agent 页面运行态 smoke 正常。仓库当前另有 `20260801_0081`，数据库尚未应用，需按对应功能单独验收。
- 组价 Agent v1.1“准确＋近似混合检索”已完成代码层、真实数据检索、重启后运行态和登录后业务验收（2026-08-01，用户确认通过）：`exact` 继续只执行精准规则，不调用向量或行业 AI；`expanded` 在精准结果后复用 CentOS RAG 服务的 BCEmbedding + BM25 + Milvus + RRF 混合通道，按存档文件和企业定额版本隔离索引，价格始终从 MySQL 权威源重新读取。近似候选受单位、动作词和分数阈值保护，必须人工采用，不能自动计价；有存档/企业候选时行业 AI 不覆盖。真实存档已验证“拆除单扇木质门”召回“拆除单开实木门”，安装查询不会误收拆除候选；真实企业定额已验证“墙面乳胶漆涂刷”召回 `ZS00357`。固定回归门结果为准确率 `1.0`、近似 `Recall@5=1.0`、自动采用 `0`、无关候选/自动计价 `0`；专项 `29 passed`、相邻报价/RAG `63 passed`，Vite build 通过。本阶段自身无 Alembic；当前 MySQL 为 `20260731_0080`，9000 已加载新版静态包与 `/api/v1/pricing-agent/capabilities` 鉴权路由。v1.1 业务口径现已冻结，后续只按真实缺陷增量维护；下一阶段为 v1.2“行业数据 AI 估价”。详见 `docs/pricing-agent-v1.md`。
- 组价 Agent 人工决策、未计价闭环与现有报价草稿写入已完成代码层验证（2026-07-31）：Alembic `20260731_0077` 逐行保存人工候选、人工直接补价、操作者、时间、决策版本和最近 20 次选择历史；刷新页面可按 `run_uuid` 恢复。未匹配项目既可在 Agent 页面直接补单价，也可作为“待补价占位行”写入现有 `QuoteJob` + `QuotePreviewDraft`；草稿可继续编辑，但原确认下发接口会阻断单价或合价未补齐的占位行。整单确认保持幂等并冻结 Agent 结果，不修改企业/账户定额。专项回归 `24 passed`，相邻报价回归 `54 passed`；另有 2 个旧前端契约仍要求已按验收删除的字段而失败，未为旧断言恢复字段。Vite build 通过，该能力自身所需迁移为 `20260731_0077`，当前内网数据库后续已升级到 `20260731_0080`。详见 `docs/pricing-agent-v1.md`。
- 报价审阅字段、定额行交互与统计导出已完成当前环境验收（2026-07-31）：快速审核移除“施工提示”；专业全字段固定为“序号、名称、项目特征、单位、工程量、不含税综合单价、不含税综合合价、人工费、主材费、辅材费、机械费、措施费、管理费、税费”14 列。展开后的定额主项只保留无表头数据行，首列用定额编码替换序号，其余字段与外层项目列按真实渲染宽度逐项对齐；移除重复标题、来源标签、分级路径、具体匹配标题和行内工料机按钮。统计主材/辅材明细移除项目特征及工作内容，并按材料分类、编码、名称、规格、品牌、单位和单价重新聚合；报价统计 Excel 包含统计汇总、主材明细、辅材明细和人工明细四个 Sheet。不新增 Alembic、不改变报价金额或企业定额 active 数据。
- 项目定额与工料机工作台第一版已完成独立模块交互验收（2026-07-31）：原右侧抽屉改为“报价草稿”和“导入甲方清单”之间的常驻“工料机明细”模块；点击专业全字段中定额数据行的任意位置，只加载当前定额工料机并保持页面当前滚动位置。模块继续支持新增、编辑、删除全部业务字段；变更只重算项目定额和当前项目计价草稿，不影响企业定额 active。显式勾选同步且具备 `cost_approver` / 管理员权限时，项目快照写入企业定额 draft 并重算；无权限返回明确 403，active 仍需原审核启用流程。真实项目 23 的 `ZS00023` 已验证加载 1 条工料机；联合回归 `72 passed`，Vite build 通过。详见 `docs/project-quota-resource-workbench-20260731.md`。
- 报价资料研判 Agent 后续“选择性跨文档图扩展/长期项目记忆”已完成实验边界判断（2026-07-30）：当前Development A manifest只有1份文档，旧多文档Challenge已锁定，无法合法评估跨文档图收益；已冻结`RET-GRAPH-EXPAND-001`契约，要求“至少两类资料角色+跨来源关系意图”同时触发，只沿有来源的case/document/section/evidence/exact-reference边扩展，预算1跳/2种子/4证据，结果不重排Top5且继续过证据门；当前状态`blocked_by_evaluation_data`，不编码、不新增图数据库、不宣称收益。长期Memory状态`deferred_not_justified`：现有LangGraph State、SQL Checkpoint和RAG已分别承担运行状态、恢复审计与原始知识，当前质量瓶颈不是跨会话遗忘；待真实出现人工修正跨会话复用需求且来源/版本/有效期/撤回/租户规则齐备后再开独立实验。详见`evals/bid_intake/retrieval/v1/ret_graph_expand_001_experiment_contract.md`与`docs/bid-intake-agent-long-term-memory-decision-20260730.md`。
- 报价资料研判 Agent 检索优化第三项“未覆盖事实的受控第二轮检索”已完成三轮Development A单变量试验（2026-07-30）：触发边界为仅部分覆盖时补查、零覆盖/已充分不补查、最多1轮2条Query。`RET-CONTROLLED-RETRY-001`虽将Recall 90%→95%，但重新RRF使nDCG 63.27%→57.91%，拒绝；002保留首轮锚点后无退化也无收益，拒绝；003保留首轮Top5和顺序，只用补查首个真实表格父节点导航同父兄弟证据组，Recall 90%→95%、MRR/nDCG保持63.33%/63.27%，Q003 75%→100%、无退化，只执行1条补查且3个负样本均不触发，P95 687ms、错误0，Development A检索门通过。Q003新增Gold后shadow事实门仍为`insufficient`，说明关系谓词确认尚未解决；003不通过硬门、不上线。`TENDER_EVIDENCE_CONTROLLED_SECOND_ROUND=false`、结构开关默认false、事实门继续shadow；必须用全新项目Development B验证。下一项选择性跨文档图扩展需要新的跨文档Development样本。详见`evals/bid_intake/retrieval/v1/ret_controlled_retry_003_summary.md`。
- 报价资料研判 Agent 检索优化第二项“表格/章节父子结构与上下文证据组”已完成两轮Development A单变量试验（2026-07-30）：`RET-STRUCT-CONTEXT-001`只补真实父章节/表头，Recall 65%→71.67%但仅改善1题，按预设门槛拒绝；`RET-STRUCT-CONTEXT-002`在同一真实表格父节点下，从现有Top20候选池为一个Top5锚点附着最多3条查询相关兄弟子行，不增加Query、不启用第二轮/图扩展/Memory。唯一正式结果为Hit@5锚点组100%、Recall@5锚点组90%、MRR63.33%、nDCG63.27%，Q002与Q004改善且无正向退化，事实门误充分20%、误拒答33.33%、负样本准确率100%、P95 696ms、错误0，Development A预设门槛通过。`@5`以5个锚点证据组为单位，组内成员可能使实际文本块超过5。当前只有单项目8题，结构开关仍保持默认关闭，必须用全新项目Development B验证；旧Holdout和Challenge不重跑。下一项为只针对未覆盖事实的一次受控第二轮检索。详见`evals/bid_intake/retrieval/v1/ret_struct_context_002_summary.md`。
- 报价资料研判 Agent 检索优化 `RET-FACT-GATE-001` 已完成shadow代码、评测契约和Development A唯一一次正式评测（2026-07-30）：LangGraph新增`update_fact_coverage`节点，记录`uncovered / candidate_covered / context_verified`事实状态；当前环境继续保持`shadow`。未见项目“总部基地设计任务书”8题经业务复核后冻结，结果为评估覆盖率100%、对齐62.5%、负样本准确率100%、误充分33.33%、误拒答50%、P95 982ms、错误0；预设硬门未通过。结论是保留事实状态和图谱可观测性，但拒绝启用`enforced`：表层事实槽会把“提到对象”误当成“回答属性”，关系型答案即使召回也可能无法确认。下一步按既定顺序进入表格/章节父子结构检索与上下文证据组；旧Holdout和Challenge不重跑，不从本项目添加专属关键词。详见`evals/bid_intake/retrieval/v1/ret_fact_gate_001_shadow_summary.md`。

## 架构总览

```
用户前端 (Vue.js 3 + Element Plus CDN)
      ↓
FastAPI 主网关 (Windows, http://localhost:9000)
      ↓                       ↓
异步报价任务 / Celery Worker   GLM-4V 图像识别 (智谱 AI)
      ↓
n8n budget-calc / budget-push
      ↓
Dify + DeepSeek-R1 报价优化
      ↓
RAG 检索服务 (CentOS 192.168.88.128:8001)
      ↓
Milvus 向量数据库 (192.168.88.128:19530)
```

## 当前运行基线

- 报价资料研判 Agent `DATA-CHALLENGE-001` 新项目接入、通用 XLSX 安全解析和唯一一次盲测已完成（2026-07-29）：针对约 43.5 MB、25 Sheet 的真实工程量清单，将解析改为按 OOXML 真实单元格流式读取并计算有效区域，增加通用资源上限、异常诊断、重复 Sheet 隔离和期次冲突隔离；24 个 Sheet 进入证据链、1 个“一期零星工程”冲突 Sheet 隔离，三份资料共形成 3,202 个有效证据块并完成 manifest v7 索引。10 题 Challenge 经业务复核后冻结，`RET-EXP-003B` 唯一一次盲测为 Hit@5 88.89%、Recall@5 47.59%、MRR 68.52%、nDCG@5 49.73%、路由与 Query 数量准确率 100%、负样本准确率 0%、P95 1677ms、错误 0；完整门槛未通过。Challenge 已锁定，不重跑、不据此调参；后续必须用全新 Development 项目验证拒答门、长表结构召回和 Top5 多事实覆盖。详见 `docs/bid-intake-agent-data-challenge-001-ingestion.md` 与 `evals/bid_intake/retrieval/v1/data_challenge_001_blind_summary.md`。
- 对话报价摘要到预算项目详细工作台的同草稿数据闭环已完成（2026-07-29）：新增 `POST /api/v1/quote/jobs/{job_id}/budget-workspace` 和 Alembic `20260729_0073`；进入明细前保存当前预审，按原 Excel SHA-256 复用有权限的预算项目，否则自动建立项目/正式导入，并把项目名、特征、工程量、单位、确认价、三级价格来源、费用拆分和工艺备注写入现有 `enterprise_ai` 计价草稿。任务持久化项目/草稿关联与内容哈希，相同内容重复进入不重建、不增加 revision；对话页已移除固定项目 21 兜底。28 行联昇真实联调复用项目 21 / 草稿 12，总额两端均为 `¥1,133,009.73`，账户定额 1 / 企业定额 0 / AI 估价 27；详细页快速审核、专业全字段和工艺备注已完成浏览器验收。当前数据库为 `20260729_0073 (head)`；9000 高权限旧进程仍需管理员重启后加载新路由。详见 `docs/unified-quotation-chat-entry-correction-20260728.md`。
- 报价资料研判 Agent 自适应 Tool 预算已完成代码层验证（2026-07-28）：ReAct 不再按静态上限每轮连续调用 3 个 Tool，改为“首轮 1 次主检索 → 检索后最多读取 2 条关键证据 → 上下文读取后最多 1 次定向补查”；Runtime 会按阶段优先级确定性裁剪模型的冗余 Tool 请求，同时保留总调用预算、重复参数预算与白名单保护。运行图谱新增动态预算阶段、上限、原因、模型原始请求数、实际保留数和裁剪数，仍不展示模型私有思维链。Agent/MCP/持久化/前端契约联合回归 `67 passed`，Query Planner 回归 `9 passed`，compileall 与 diff check 通过；不新增 Alembic，不改变 MCP Tool 契约、证据门或人工审核。详见 `docs/bid-intake-agent-adaptive-tool-budget.md`。
- 报价资料研判 Agent 自适应检索路由已完成代码层与当前环境运行态验证（2026-07-28）：Query Planner 后新增不调用 LLM 的轻量分类器，逐个原子 Query 分为 `exact / semantic / hybrid`；事实与关键词查找走直接标识匹配 + BM25，风险/影响/建议等研判问题走 Milvus 向量，只有“精确标识 + 语义研判”混合意图直接走向量 + BM25 + RRF，单通道零结果时允许补跑 hybrid 安全兜底。MCP Observation 记录每个 Query 的分类、置信度、原因、实际通道与兜底状态，运行图谱可显示词法/语义/混合计数；Tool 对外契约、数据库与 Milvus schema 不变，无新增 LLM 调用。Agent/Tender 聚焦回归 `70 passed`；CentOS `rag-service` 已备份、重建并恢复可用，直接通道验收为 exact `vector=0 / bm25=20`、semantic `vector=20 / bm25=0`、hybrid `vector=20 / bm25=20 / rrf`；东莞香港中心 1869 块真实索引的复合问题按 `1 semantic + 3 exact` 返回 5 条证据，混合意图按 `1 hybrid + 2 exact` 返回 5 条证据；MCP/Worker 已重启，readiness `ready_to_start=true`、Worker 1 个在线、模型 `deepseek-v4-flash`、索引 completed。详见 `docs/bid-intake-agent-adaptive-retrieval-router.md`。
- 旧对话报价流式进度已完成普通用户业务化（2026-07-28）：聊天气泡不再直接展示网关、模块、队列、任务号、追踪号等后台事件，只显示“接收需求、读取内容、整理清单、计算报价、准备核对”五个业务阶段和自然语言状态；新增按真实阶段受控、阶段内持续增长的进度条，完成后到 100%，错误提示同步去技术化。前端与相邻报价回归 `45 passed`、旧页脚本解析和 Vite build 通过；浏览器用 1 行不下发验收报价确认计算阶段 `77%`、进度条 `76.8%`、聊天正文无原始技术事件，并正常进入报价核对。无 Alembic、无报价规则或定额数据变更。详见 `docs/unified-quotation-chat-entry-correction-20260728.md`。
- 统一报价工作台第一版界面融合已完成（2026-07-28）：预算项目计价页与旧对话预审弹窗统一提供“快速审核、专业全字段、费用汇总、报价依据、版本记录”五个视图，全部共用原有草稿；专业视图保留项目、特征、区域、采购方式、工程量、不含税单价/合价以及人工、主材、辅材、税金、损耗、机械、综合费、管理费、利润、措施费、甲供材和备注等完整字段。预算项目原双模式对比收纳为高级计价策略，风险复核、证据、草稿、导出和下发能力均保留。无 Alembic、无报价规则和定额数据变更；本轮联合回归 `44 passed`、Vite build 与旧页脚本解析通过，预算项目 20 浏览器验收确认五视图和 `198/198` 工程量。旧对话 299 行大预审自动化浏览器响应超时，已由源码合同和回归覆盖，仍待实际弹窗走读。详见 `docs/unified-quotation-chat-entry-correction-20260728.md`。
- 统一报价当前进入双入口调试阶段（2026-07-28）：保留旧对话报价 `/index.html?entry=new-quote&mode=quick`，同时恢复“项目报价” `/quotes` 与 `/quotes/new`；待两条流程全部调通并验收后再隐藏其中一个入口。对话报价支持文字、图片、`.xlsx/.xlsm` 自动标准化，并按“当前账户 active 定额 → 严格唯一 active 企业定额 → 仅未命中行 AI 估价”逐行报价，继续复用原预审、草稿恢复、完整性检查和确认下发；项目报价聚合任务、历史和有权限可见的预算项目。三级报价与相邻回归 `115 passed`，双入口聚焦测试 `38 passed`，报价任务/历史/预审草稿/标准化/预算项目联合回归 `134 passed`，Vite build 和当前 9000 双入口路由/健康检查通过。详见 `docs/unified-quotation-chat-entry-correction-20260728.md`。
- 报价资料研判 Agent Phase 5d-2 运行时卡死修复已完成代码层与当前任务恢复验证（2026-07-28）：根因是 Windows 启动脚本记录了 Python 包装进程 PID，重启后遗留旧 Worker；旧 Worker 使用旧的进程内 MCP JWT secret 调用新 MCP，收到 401，但运行租约未被失败释放，前端因此长期停留在 `running/queued`。现已改为 Worker 自报真实 PID，启动时先执行 MCP 鉴权会话预检，并将进入 LangGraph 前的 MCP/模型初始化异常持久化为 `run_failed`、释放租约并允许预算内重试。报价 Agent 聚焦回归 `45 passed`。受影响任务 `a9405ed9-22f9-4ef8-bb70-374602b92b48` 已安全释放旧租约并由 Worker 41676 以第 2 次尝试接管；MCP 返回由 401 恢复为 200，动态图谱累计 75 个事件并于 11:22:04 正常进入 `waiting_human / human_review`，结论为 `recommend_no_quote`、证据门为 `supplement_required`。
- 报价资料研判 Agent Phase 5d-1 细粒度执行链与紧凑图谱已完成代码层和前端视觉验证（2026-07-28）：在原 trace 上新增 `llm_input`、`plan`、`loop` 虚拟可审计节点，真实表达“组装 LLM 安全输入 → LLM 研判 → 行动计划/工具选择 → 授权 → Tool → Observation 回写 → 继续/停止 ReAct”；只记录输入结构、工具动作和决策摘要，不记录模型私有思维链。D3 节点核心半径从 26px 缩至 18px，自动适配只缩小不放大，并增加边语义标签和详细检查面板。聚焦回归 `19 passed`、Vite build 通过，Chrome 已确认紧凑节点、连线标签和旧记录回放正常；新细粒度 trace 仍需重启 FastAPI 与 Agent Worker 后用新研判验收。

- 报价资料研判 Agent Phase 5d 实时运行图谱已完成代码层与前端视觉验证（2026-07-28）：`PersistentBidIntakeExecutor` 已从 LangGraph `tasks` stream 投影并持久化 `bid-intake-agent-trace/v1` 安全执行事件，覆盖 ReAct、工具授权、Tool、Observation、结构化草稿、总经办标准、证据门和 Human-in-the-loop；Vite `/admin/bid-intake-agent` 新增 D3 力导向运行图谱、节点详情、自动跟随、缩放拖拽及旧记录生命周期降级回放，轮询间隔收敛为 1.2 秒。仅展示可审计摘要，不展示模型私有思维链，敏感字段脱敏；复用现有 `bid_intake_run_events`，不新增 Alembic。聚焦回归 `18 passed`、Vite build 通过、Chrome 已确认旧记录图谱节点/连线/详情正常；完整动态 trace 需重启 FastAPI 与 Agent Worker 后新发起研判验收。详见 `docs/bid-intake-agent-phase5d-live-runtime-graph.md`。

- 报价资料研判 Agent Phase 5c 招标资料类型自动识别已完成代码层验证（2026-07-28）：`/admin/bid-intake-agent` 已移除“本批资料类型”人工下拉框，上传统一提交 `file_type=auto`；证据解析管线在文本抽取后根据文件名与正文结构识别招标文件、答疑/澄清、补遗/变更、图纸、工程量清单或其他资料，并将结果写入 parsed file、evidence document 和解析事件。低置信度资料归入 `other`，不强行分类、不阻断证据入库；不新增 Alembic，不改变 ReAct、总经办标准或报价链路。联合聚焦回归 `32 passed`，compileall、Vite build 和 diff check 通过；详见 `docs/bid-intake-agent-phase5c-auto-file-type-classification.md`，运行态生效需重启 FastAPI 与 Celery Worker 后人工验收。

- 报价资料研判 Agent Phase 5b 前端资料入库闭环已完成代码层与当前运行态验证（2026-07-28）：Vite `/admin/bid-intake-agent` 已新增“上传并解析招标资料”，支持 PDF、DOCX、XLSX、XLSM、TXT、MD 多文件入队、解析任务进度、错误提示与可恢复任务重试；Phase 5c 后资料类型改为系统自动识别。解析完成后自动刷新 evidence manifest 和 readiness，资料未就绪时仍保留“发起研判”证据门。复用现有 Tender Evidence Parse Pipeline，不新增 Alembic，不改变 Agent、总经办标准或报价链路；Vite build 通过，证据上传前端契约、解析管线、证据存储、Runtime 与 SPA 路由聚焦回归 `25 passed`；`/health/ready=ready`、Celery Worker 2 个在线，运行中的 `/admin/bid-intake-agent` 已引用最新构建资源。详见 `docs/bid-intake-agent-phase5b-evidence-upload-workbench.md`，待前端业务人工验收。

- 报价资料研判 Agent Phase 5a 运行态启用与首个项目闭环已完成当前环境验收（2026-07-27）：新增独立 `.venv-agent`、`start_bid_intake_agent.ps1` 和 `start_all.ps1` 可选编排；运行态复用现有 DeepSeek 配置但不重复保存密钥，MCP secret 仅在进程内生成；修复自定义研判目标透传、Tool 预算可见、精确 `AssessmentDraft` JSON Schema、一次输出修复和 `unknown` 来源归一化。受控项目 `BIZ-4a smoke tender d2c269f9` 已完成“证据入库 -> MCP -> ReAct -> PolicyEngine -> 证据门 -> Human-in-the-loop -> SQL Checkpoint 恢复”，最终保留 assessment `61b98bd2-fbbe-474a-8514-fa08e6b5d25c`，状态 `waiting_supplement`；专项回归 `50 passed`，详见 `docs/bid-intake-agent-phase5a-runtime-activation-acceptance.md`。9000 FastAPI 已由用户完成重启，Phase 5b 继续补齐前端资料入库验收。

- FastAPI: `http://localhost:9000`
- CentOS: `192.168.88.128`
- RAG: `http://192.168.88.128:8001`
- n8n: `http://192.168.88.128:5678`
- Redis: `192.168.88.128:6380`
- MySQL: `192.168.88.128:5455`
- MinIO: `192.168.88.128:9002/9003`
- 健康检查：`/health/live`、`/health/ready`
- 当前任务队列模式：生产使用 `TASK_QUEUE_MODE=celery`
- 当前后端优化状态：基础设施 P0-P3 与配置收尾已完成并冻结；业务优化 P0 报价反馈闭环、P1 Admin 反馈分析、P2 Prompt 回归评测、P3 知识库候选治理和 P4 真实用户体验优化已落地到代码层。
- AI 平台架构升级 Phase 0 已完成开发与当前环境验证（2026-05-18）：RBAC、`role_version`、Vite 壳、`/login`、`/admin/permissions`、SPA fallback 已通过；旧 `index.html` / `admin.html` / `app.html` 保留。正式生产上线尚未发生，未来需单独 Runbook。
- AI 平台架构升级 Phase 1 报价速度看板已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_DASHBOARD_QUOTE`、`/api/v1/admin/dashboard/quote-speed` 和 `/admin/dashboard` 看板视图；已修复新报价任务 `duration_ms` 实测写入，并在备份后回填历史 121 条成功任务的 0 耗时记录；页面、看板数据和新增真实报价统计均已确认正常，正式生产启用待单独 Runbook。
- AI 平台架构升级 Phase 2 响应速度追踪已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_CLIENT_INQUIRY`、`FEATURE_DASHBOARD_RESPONSE`、`client_inquiries`、报价任务咨询关联、咨询查询/修正接口和 `/admin/dashboard` 响应速度标签页；当前环境 Alembic 已升级到 `20260514_0012 (head)`，功能开关已打开且 `PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已展示 1 条可信响应样本，平均首次响应 15 分钟，Celery worker Phase 2 元数据加载问题已修复。
- AI 平台架构升级 Phase 2.5 管理员报价运营闭环已完成当前环境验证（2026-05-18）：复用 `quote_jobs`、`client_inquiries`、`quote_history`，在 `/admin/dashboard` 新增“报价运营”标签页；提交人列、筛选、任务详情、重试/取消/超时标记入口已落地；不新增数据库结构。
- 原 Phase 3 执行任务/执行速度与 Phase 4a 会议纪要功能曾于 2026-05-19 完成验证，已于 2026-07-31 按产品决定退役；当前代码和导航不再提供该功能。
- 原 AI 平台升级 BIZ Track BIZ-1a 商务台账 v1 曾完成当前环境验证，已于 2026-07-31 退役；当前代码和导航不再提供该功能。
- AI 平台升级 BIZ Track BIZ-2a 成本数据库初始化已完成当前环境验证（2026-05-20）：新增 `FEATURE_COST_DB`、Alembic `20260520_0017` / `20260520_0018`、`cost_items`、`cost_item_history`、成本库接口、旧 Excel 导入预览/确认和 `/admin/cost-db` 页面；保留对甲税前综合单价、劳务发包综合单价、班组标底税前价三类价格，并补充人工费、主材费、辅材费等拆分价；2026-05-21 补充“撤回启用”和批量状态流转能力，支持单条/批量 `draft -> active`、`active -> draft` 并写入状态历史，`archived` 仍冻结不可撤回。
- AI 平台升级 BIZ Track BIZ-2b 报价时材料底价查询已完成代码层验证（2026-05-21）：新增报价结果成本参考匹配服务，接入同步 `/chat` 与异步 `quote_jobs` preview 结果；仅匹配 `active` 成本条目，优先 `item_name + spec` 精确匹配，其次 `item_name` 模糊匹配；旧 `index.html` 预审弹窗已展示“成本库参考价 vs AI 生成价”和价差提示。不新增 Alembic；当前环境成本库 `active=190`，已具备真实运行态成本参考匹配数据基础。
- AI 平台升级 BIZ Track BIZ-2c 成本库主库化 + active RAG 同步已完成当前环境验证（2026-05-21）：新增 active 成本条目 RAG 同步服务、`POST /api/v1/admin/cost-items/sync-rag` 管理员接口、Alembic `20260520_0019` 同步记录表 `cost_rag_sync_runs`、`GET /api/v1/admin/cost-items/sync-rag/runs` 和 Vite `/admin/cost-db`“同步 active 到 RAG / 同步记录”窗口；同步源只取 `cost_items.active`。旧 `materials` 作为报价/RAG 源已退役，70 条测试数据已备份后清空，旧 materials 写入/回滚、旧 `/admin/sync_milvus` 和旧知识候选 approve 均返回 410。
- AI 平台升级 BIZ Track BIZ-2d 成本库参考价命中率优化已完成代码层验证（2026-05-21）：补强 `quote_cost_matching` 的中文名称归一化、符号/连接词处理、词序无关 token 匹配、单位族兼容和动作词误命中保护；“窗帘盒/灯槽拆除”类写法可命中 active 成本库底价；编号换行清单在发送 N8N 前会自动清洗成分号清单，避免 `1. / 2. / 3.` 多行需求触发空响应。不新增 Alembic，不启动漏项检测。
- AI 平台升级 BIZ Track BIZ-2e 漏项检测已完成代码层验证（2026-05-21）：新增保守规则式漏项检测服务，复用同步 `/chat` 与异步 `quote_jobs` preview 成本库 enrichment；仅基于 `cost_items.active` 生成 `omission_summary` / `omission_suggestions`，旧 `index.html` 预审弹窗展示疑似漏项、触发行、原因和成本库参考价；不自动新增报价行、不改变合计、不新增 Alembic。
- AI 平台升级 BIZ Track BIZ-2f 报价需求单 Excel 解析已完成代码层验证（2026-05-21）：新增 `quote_excel_parser`，`index.html` 支持上传 `.xlsx/.xlsm` 需求单；同步 `/chat` 与异步 `quote_jobs` 会直接解析施工项目、数量、单位、规格/特征和备注后进入现有报价流程，不再把 Excel 交给 GLM-4V；旧 `.xls` 明确提示另存为 `.xlsx`。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2g 成本库底价兜底填价已完成代码层验证（2026-05-21）：当 AI 预审单给出空/0 单价但已命中 `cost_items.active` 底价且可解析数量时，报价预审会使用成本库参考价回填单价和合计，并标记“已用成本库底价兜底”；AI 已给出正常正数单价时不覆盖。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2h 成本库价格前置给 AI 报价链路已完成代码层验证（2026-05-22）：新增 `quote_cost_context`，在 FastAPI 调用 N8N/Dify 前基于 `cost_items.active` 匹配需求项，把命中的底价、单位、数量、匹配类型、`cost_item_id` 和参考合计作为 `[成本库底价强参考]` 追加到 `text.content`；同步 `/chat` 与异步 `quote_jobs` 已接入，BIZ-2g 后置兜底继续保留。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2i 报价可解释性与审计记录已完成代码层验证（2026-05-22）：报价确认/打回后记录成本证据，包含 AI 原始报价、最终报价、成本库参考价、成本条目快照、行合计来源、整单合计来源、AI 报价来源和证据链接；后台报价反馈/报价运营可追溯。不新增 Alembic。
- AI 平台升级 BIZ Track BIZ-2j 报价依据与成本库证据链展示优化已完成代码层验证（2026-05-22）：`index.html` 预审“查看依据”弹窗已按 AI 报价来源、AI 报价依据、成本库参考、合计对照等分区展示，成本库详情链接改为按钮；后台报价任务详情展示 AI 来源和成本证据。
- BIZ-2 预审体验补强已完成当前环境手动验收并推送（2026-05-23）：品牌文案统一为“旗胜智价”；预审阶段支持同名不同规格成本条目切换，切换后同步采用新成本条目参考价重算单价/合计；AI 来源可区分采纳/偏离前置成本库、无成本库参考 AI 估算、成本库兜底和人工切换；报价运营详情展示预审打回原因。
- AI 平台升级 BIZ Track BIZ-2k 成本库数据质量体检 + 演示回归包已通过当前环境手工验收（2026-05-28，BIZ-2k-1 报告可读性补强后复验）：新增只读 `cost_items.active` 体检服务和报告脚本，可生成 Markdown/CSV/XLSX 与演示回归包，覆盖同名不同规格、价格为空/0、单位异常、规格备注缺失、相似条目和 RAG 同步数量提示；不新增 Alembic，不新增页面/API，不改报价逻辑，不写数据库。
- AI 平台升级 BIZ Track BIZ-2l-0 甲方需求单标准字段与典型场景确认已完成文档层确认（2026-05-25）：新增 `docs/biz-2l-requirement-standardization-biz2l0.md`，明确标准报价行字段、字段别名、单位数量规则、行类型、典型场景、置信度、警告码、人工确认口径和 BIZ-2l-1 输出合同；不编码、不改数据库、不改报价规则/价格口径、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2l-1 只读通用清洗解析器已完成代码层验证（2026-05-25）：新增 `app/services/requirement_standardizer.py`、`scripts/biz2l_requirement_standardization_preview.py` 和 `tests/test_requirement_standardizer_biz2l.py`，可输出标准化 JSON/CSV/Markdown 预览；不接报价、不写数据库、不改报价逻辑/价格口径。
- AI 平台升级 BIZ Track BIZ-2l-2 人工列映射与行确认已完成当前环境验收（2026-05-26）：新增 `/admin/requirement-standardization` Vite 页面和 `app/api/v1/requirement_standardization.py` 标准化 API，支持上传 `.xlsx/.xlsm` 解析预览、按 Sheet 人工列映射、按 Sheet 行确认、原始行追溯、标准数量来源与多工程量候选、搜索/筛选、确认清单生成、本地历史解析记录和版本回滚；历史进度保存在浏览器 IndexedDB，不写数据库、不新增 Alembic；仍不接报价、不改报价逻辑/价格口径、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2l-3 标准清单接入报价链路已完成代码层验证（2026-05-26）：需求单标准化确认页新增“发起报价”，发起前重新调用确认接口校验当前行，只将已确认且通过校验的标准行组装为 `quote_text` 并调用现有 `/api/v1/quote/jobs` 异步报价任务；阻断行、剔除行、说明/汇总/空白行不进入报价；创建任务后自动跳转旧报价工作台 `index.html` 接管进度并复用原有 AI 预审弹窗人工验收；行确认支持按当前筛选结果全选、取消选择、批量确认和批量撤回确认；若生成确认清单或发起报价前存在阻断行，界面展示校验问题面板、中文错误原因和原始行内容，并自动切换到“未通过校验”定位首条问题行；复用现有报价、成本库前置参考、成本库匹配、漏项检测、底价兜底和证据链；不新增数据库结构、不新增 Alembic、不改报价逻辑/价格口径、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2l-4 预审对账与运营复核详情已完成当前环境业务验收（2026-05-27）：新增 Alembic `20260526_0022` 和 `quote_job_requirement_rows`，报价任务创建时持久化人工确认的标准需求行；新增 `/api/v1/quote/jobs/{job_id}/review-detail`，对账确认清单与 AI 预审条目，标出疑似未报价、额外预审行、无底价参考、成本库兜底、人工改动过大、偏离底价过大等复核项；169 行任务 `2557d7ba-f0ca-48ba-91c3-5c9c8f993a4c` 已验证 `review-detail` 返回 200，确认行 169、预审行 169、需复核 169、高风险 169，可用于旧预审弹窗和 Vite 报价运营详情继续追溯；不改报价规则、价格口径、无底价自动处理和成本库沉淀逻辑。
- AI 平台升级 BIZ Track BIZ-2l-5 确认清单逐行报价完整性保障已完成当前环境业务验收（2026-05-27）：异步报价执行时读取已持久化的确认需求行，将 `requirement_row_key`、Sheet、原始行号、项目名、规格、数量、单位和备注追加为结构化逐行报价要求，明确禁止 AI 合并、抽样或省略确认行；AI 预审结果会生成 `requirement_integrity` 完整性摘要，`/api/v1/quote/jobs/{job_id}/review-detail` 和报价运营详情展示完整/不完整状态；专项测试已覆盖不完整预审 `/confirm_push` 409 阻断、逐行 key 匹配、占位未补价阻断和占位不走成本库底价兜底。不新增数据库结构、不改报价规则/价格口径、不改无底价自动处理、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2l-6 确认清单分批报价与缺失占位已完成当前环境业务验收（2026-05-27）：确认清单超过阈值时异步报价按批进入现有 N8N/Dify 链路，默认每批 20 行并对缺失行补报 1 次；仍未返回的确认行会按原顺序生成需人工补价的占位预审行，保留数量、单位、Sheet 和原始行号；占位行不触发成本库底价自动兜底，未人工填写单价和系统合计前，旧 `index.html` 和 `/confirm_push` 都会阻断下发；重启后 169 行任务 `2557d7ba-f0ca-48ba-91c3-5c9c8f993a4c` 已验证预审行数 169、占位行 169、`missing_count=0`、未补价推送 409 阻断，前端人工验收通过；补充 Alembic `20260526_0023` 将 `quote_jobs` / `quote_job_events` 大 JSON 字段扩为 `LONGTEXT`，避免大清单预审结果写库超长。不改报价规则/价格口径、不改无底价自动处理、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2m 无底价项目规则开发落地已通过当前环境手工验收（2026-05-28）：新增 `app/services/no_cost_draft_capture.py`、`FEATURE_NO_COST_DRAFT_CAPTURE`、`/confirm_push` 成功后无底价 draft 捕获、成本库来源筛选、旧预审弹窗无底价固定提示和下发成功 draft 摘要；自动生成项只写入 `cost_items.draft`，不自动 `active`，draft 不参与后续报价/RAG/兜底，未补价占位继续阻断，已补价占位可沉淀；BIZ-2p 后来源按手动改价/采用 AI 建议细分。新增 `docs/biz-2m-demo-and-acceptance.md` 记录演示与验收。重启后 `/health/ready=ready`，Celery worker_count=1，Alembic `20260526_0023 (head)`，`FEATURE_NO_COST_DRAFT_CAPTURE=True`；不新增 Alembic，不改报价价格口径。
- AI 平台升级 BIZ Track BIZ-2n 预审人工改价字段与合计联动已通过当前环境手工验收（2026-05-28）：旧 `index.html` 预审弹窗新增可编辑“工程量/单位”和“人工改价(元)”列，人工改价默认取成本库参考价，无成本库参考则默认 0；修改工程量或人工改价后按工程量联动系统合计；AI 返回工程量 0 但源 Excel 有有效工程量时，后端回填源工程量；下发前写回 `unit_price/total_price`，未补有效人工改价或系统合计前阻断推送；详见 `docs/biz-2n-manual-price-preview.md`。不新增 Alembic，不改 N8N/Dify，不改成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2o 成本库状态与流向台账已通过当前环境手工验收（2026-05-28）：Vite `/admin/cost-db` 新增“状态与流向”入口，可按总览、新增 draft、active 记录、归档记录查看成本条目来源、当前去向、生命周期和报价引用；后端新增只读 lineage 汇总/列表/详情接口，复用 `cost_items`、`cost_item_history`、`quote_cost_evidence` 和 `cost_rag_sync_runs`；详见 `docs/biz-2o-cost-lineage.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2p 预审人工改价来源判定与 AI 建议采纳已通过当前环境手工验收（2026-05-28）：旧 `index.html` 预审“人工改价(元)”列新增“采用AI建议”，点击后采用 AI 建议单价并按工程量重算系统合计；手动改价下发后无底价 draft 来源写“人工”，采用 AI 建议或沿用 AI 价则来源写“AI 建议”，状态与流向详情展示价格动作；详见 `docs/biz-2p-preview-price-source.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2q 报价预审草稿保存与恢复、BIZ-2q-2 我的报价历史筛选与草稿清理已通过当前环境手工验收（2026-05-28）：新增 `quote_preview_drafts` 和 Alembic `20260527_0024`，旧 `index.html` 预审弹窗支持自动保存和手动保存草稿，并新增“关闭”按钮；“我的报价历史”展示 `editing` 草稿，推送列显示“草稿”，操作列显示“编辑”，并已补充时间、报价内容、项目数、总价、状态筛选和草稿批量删除；再次打开同一报价任务会恢复 `editing` 草稿；打回重填标记 `discarded`，确认下发成功后标记 `pushed` 并阻止继续覆盖；详见 `docs/biz-2q-preview-draft-save.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2r 成本库重复 active 防护与报价多候选提示已通过当前环境手动验收（2026-05-27）：新增 `app/services/cost_duplicate_guard.py`，成本库单条/批量启用会阻断相同或高风险相似 active，允许同名不同规格共存；无底价 draft 沉淀前会跳过相同或相似 draft/active；报价命中多个 active 候选时在 `cost_reference` 标记候选数量和候选列表，旧预审单要求确认当前依据或切换成本条目，未确认前前端与 `/confirm_push` 均阻断下发；详见 `docs/biz-2r-cost-duplicate-active-guard.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2s 成本价权限落地首版已通过当前环境手动验收（2026-05-28）：新增成本专项角色 `cost_viewer` / `cost_editor` / `cost_approver` / `cost_exporter`，收紧普通 `staff` 完整成本库访问，只保留报价预审受限 active 候选查询 `GET /api/v1/cost-items/quote-candidates`；Vite 成本库入口和操作按钮按查看、编辑、审批启用/归档/同步拆分；旧 `index.html` 预审切换成本条目改走受限候选接口并隐藏普通业务员完整成本库详情入口；详见 `docs/biz-2s-cost-price-permissions-implementation.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2t 成本库数据治理执行包已生成当前环境只读治理基线（2026-05-28）：新增 `app/services/cost_governance.py` 和 `scripts/biz2t_cost_governance_pack.py`，复用 BIZ-2k active 体检结果并结合报价引用证据和 RAG 同步记录，输出治理摘要、人工整改 CSV/XLSX 和 raw JSON；当前基线写入 `reports/biz2t/20260528_current/`，总 208 条、active 195、archived 13、draft 0，被报价引用 active 42，治理动作 126 条，高风险 5、中风险 27、低风险 94，最近 RAG 同步 success 且 195/195；详见 `docs/biz-2t-cost-data-governance-execution-pack.md`。不写数据库，不自动删除/合并/改价/启用 active，不新增 Alembic，不改报价规则或价格口径。
- AI 平台升级 BIZ Track BIZ-2t-1 高风险整改交接清单已完成文档层准备（2026-05-28）：新增 `docs/biz-2t-high-risk-cost-handoff.md` 和 `reports/biz2t/20260528_current/cost_governance_high_risk_handoff.csv`，将 5 条试运行阻断 active 条目转为成本部逐条核价交接材料；只读整理，不写数据库、不自动改价/撤回/归档/启用 active，不新增 Alembic，不改报价规则或价格口径。
- AI 平台升级 BIZ Track BIZ-2t-2 高风险整改结果复核包已完成只读复核（2026-05-28）：新增 `scripts/biz2t2_high_risk_handoff_review.py`、`docs/biz-2t-2-high-risk-handoff-review.md` 和 `reports/biz2t/20260528_current/high_risk_handoff_review.*`；当前交接 CSV 的 5 条高风险项均由管理员标记为 `accepted_risk`，理由为“临时试运行允许作为已知风险，后续补供应商报价”，复核结论为 `ready_with_known_risks`、`trial_blocker_count=0`；后续试运行需登记为已知风险；不写数据库、不自动改价/撤回/归档/启用 active、不触发 RAG 同步、不新增 Alembic、不改报价规则或价格口径。
- AI 平台升级 BIZ Track BIZ-2u 小范围内网试运行准备包已完成文档层准备（2026-05-28）：新增 `docs/biz-2u-internal-trial-preparation.md`，明确试运行准入条件、首批角色、样例清单、每日流程、问题反馈表、验收口径和暂停条件；正式试运行尚未启动，启动前仍建议成本部先处理或说明 BIZ-2t 的 5 条高风险 active 来源价问题，并保持 `PUBLIC_ACCESS_ENABLED=false`。本阶段不写数据库、不新增页面、不新增 Alembic、不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2u-1 小范围内网试运行执行模板包已完成文档层准备（2026-05-28）：新增 `docs/biz-2u-1-internal-trial-execution-templates.md` 和 `reports/biz2u/20260528_trial_templates/` 下的样例登记表、问题反馈台账、每日检查清单和验收记录模板；正式试运行仍未启动，只提供可填写执行材料；不写数据库、不启动服务、不新增 Alembic、不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2u-2 小范围内网试运行启动前登记与检查包已完成文档层准备（2026-05-28）：新增 `docs/biz-2u-2-internal-trial-readiness-check.md` 和 `reports/biz2u/20260528_trial_readiness/` 下的已知风险登记表、启动前检查清单和摘要 JSON；5 条 `accepted_risk` 高风险项已登记为试运行已知风险，当前结论为 `ready_with_known_risks_pending_start_confirmation`；正式试运行仍未启动，后续需负责人单独确认是否进入 BIZ-2u-3；不写数据库、不启动服务、不触发 RAG 同步、不新增 Alembic、不改报价规则、价格口径、N8N/Dify 或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2v-1 报价下发与成本价权限安全加固已通过当前环境手工验收（2026-05-28）：新增 `/confirm_push` 的 `quote_job_id` 归属校验，普通用户不能用他人任务下发或标记他人预审草稿；报价候选接口关键词最少 2 个字符，普通 `staff` 只返回预审切换所需 active 成本字段，成本专项角色和管理员仍可查看完整候选；单条/批量启用 active 必须填写核定原因并写入状态历史；详见 `docs/biz-2v-1-quote-push-permission-hardening.md`。后端全量测试 `248 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2v-2 RAG 同步一致性与滞后提示已通过当前环境手工验收（2026-05-28）：修正 active 成本条目同步到 RAG 失败/超时时 `synced_count` 误显示为请求条数的问题；新增 `GET /api/v1/admin/cost-items/sync-rag/status`，可只读判断当前 RAG 是否已同步、需同步、失败、从未同步或无 active 条目；Vite `/admin/cost-db` 展示同步状态、active 数量和最近成功同步时间；详见 `docs/biz-2v-2-rag-sync-consistency.md`。后端全量测试 `253 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过；不新增 Alembic，不自动触发 RAG 同步，不改报价规则、价格口径、N8N/Dify 或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2v-3 成本库敏感操作审计与导出控制已通过当前环境手工验收（2026-05-28）：新增 Alembic `20260528_0025` 和 `cost_access_audit_logs`；成本库 CSV 导出仅允许 `cost_exporter`、`admin`、`system_admin`，导出、完整列表查看、详情查看、状态变更、导入确认、状态与流向和 RAG 同步动作均写入审计；新增 `GET /api/v1/admin/cost-items/audit-logs`，管理员和 `cost_approver` 可查审计；Vite `/admin/cost-db` 新增“导出”和“审计记录”入口；详见 `docs/biz-2v-3-cost-audit-export-control.md`。后端全量测试 `257 passed, 5 warnings`，`compileall app tests` 通过，`alembic heads` 为 `20260528_0025 (head)`，`ai-web` build 通过；不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2w-1 系统完善审查与账号入口安全加固已通过当前环境手工验收（2026-05-28）：新增 `ALLOW_SELF_REGISTRATION=false` 默认关闭自注册，`/api/v1/auth/register` 未开启时返回 `403 SELF_REGISTRATION_DISABLED`；新增 `POST /api/v1/admin/users` 仅允许 `system_admin` 创建用户、设置额度和初始角色，Vite 权限管理页新增“新建用户”，旧 `index.html` 移除“注册并领取额度”入口提示；详见 `docs/biz-2w-1-system-risk-hardening.md`。后端全量测试 `260 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2w-2 RAG 同步状态时间口径误报修复已通过当前环境手工验收（2026-05-28）：修复 `cost_items.updated_at` 数据库本地时间与 `cost_rag_sync_runs.finished_at` 应用 UTC 直接比较导致的“刚同步仍提示有更新未同步”；后端按数据库 `NOW()` 与 `UTC_TIMESTAMP()` 差值归一化 active 更新时间，前端最近成功同步时间按本地时间展示；当前真实环境状态已恢复为 `synced / 已同步`；用户已使用 `outputs/biz2w2/biz2w2_acceptance_requirement.xlsx` 完成上传、预审修改、确认下发和追溯验收；详见 `docs/biz-2w-2-rag-sync-status-timezone-fix.md`。专项测试 `10 passed, 1 warning`，`compileall app tests` 通过，`ai-web` build 通过；不新增 Alembic，不改报价规则、价格口径、RAG 同步动作、N8N/Dify 或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2w-3 成本参考优先与 AI 改写防护已完成代码层验证，待当前环境手工验收（2026-05-28）：报价前基于原始需求命中的 active 成本参考会转为后置预审锁定依据，AI 返回项目名若改写到其他成本项会标记 AI 改写风险，未人工确认前旧预审弹窗和 `/confirm_push` 均阻断下发；真实库只读模拟已验证“600*600矿棉板吊顶，10㎡”优先保留 `#39 轻钢龙骨矿棉板吊顶` 成本依据并识别 AI 返回 `#35 轻钢龙骨石膏板平面天花` 风险；详见 `docs/biz-2w-3-cost-reference-priority-ai-rewrite-guard.md`。专项测试 `3 passed, 1 warning`，相关回归 `56 passed, 1 warning`，`compileall` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2w-4 AI 备注与成本依据一致性校验已通过当前环境手工验收（2026-05-29）：当预审行已命中 active 成本参考但 AI 原始备注仍声称“未包含相关条目、无法提供报价、建议补充”等时，系统保留 AI 原始备注作审计、替换预审可见备注为成本依据一致的系统建议备注，并要求人工确认备注处理；未确认前旧预审弹窗和 `/confirm_push` 均阻断下发；详见 `docs/biz-2w-4-ai-note-cost-basis-consistency.md`。专项与相关回归 `34 passed, 1 warning`，报价任务回归 `26 passed, 1 warning`，`compileall app tests`、`ai-web` build 和旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-2w-5 自由文本同名不同规格拆行与工程量识别修复已完成代码层验证，待当前环境手工验收（2026-05-29）：修复“石膏板吊顶 9.5mm，8㎡、石膏板吊顶 12mm，8㎡”这类手输需求没有按顿号拆成两条前置成本参考、且 `9.5mm` 被误识别为 `9.5m` 工程量的问题；前置成本上下文现在会分别保留 `9.5mm` / `12mm` 规格，并取末尾真实 `8㎡` 作为工程量；详见 `docs/biz-2w-5-text-multi-spec-quantity-guard.md`。专项相关测试 `28 passed, 1 warning`，`compileall app tests` 通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则，不自动新增报价行或改总价。
- AI 平台升级 BIZ Track BIZ-2w-6 报价来源口径与预审列名调整已通过当前环境手工验收（2026-05-29）：旧预审弹窗已将“AI 建议单价”调整为“预审参考单价”，并明确“成本库依据 / 人工确认价 / 人工确认合计”，成本库命中时突出成本库依据，无成本库时才显示 AI 估价；补充修复 AI 返回 `item_1` / `item_2` 等占位项目名时预审项目名丢失的问题；详见 `docs/biz-2w-6-quote-source-wording.md`、`docs/biz-2w-6-placeholder-project-name-hotfix.md`。旧 `index.html` inline script 检查通过，AI 占位项目名保护专项并入成本上下文/成本匹配回归 `30 passed, 1 warning`；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
- AI 平台升级 BIZ Track BIZ-3 企业内部工程项目进度中台已推进至 BIZ-3c-3 经营驾驶舱轻量 MVP 趋势图 + 风险规则精化（2026-06-02）：项目进度底座、单人试运行模板、旗胜 EPC 流程模板、任务成果证据 MVP、无证据软提醒、缺证据汇总、证据策略显式字段落库与存量回填、A 级 `complete_required` 硬门禁与放行事件均已完成并通过当前环境业务验收；当前库已激活 8 条 A 级硬门禁任务。BIZ-3c-1 新增 `FEATURE_DASHBOARD_BUSINESS_LITE`、`GET /api/v1/admin/dashboard/business-lite` 和 `app/services/business_lite_dashboard.py`，只读聚合报价、成本库、项目进度和系统健康摘要，不新增经营数据表、不新增 Alembic、不展示成本敏感明细。BIZ-3c-2 已在 Vite `/admin/dashboard` 新增“经营总览”标签页；BIZ-3c-3 已补报价趋势、项目证据趋势、成本/项目分布概览，并将硬门禁风险精化为“放行后当前仍缺 active 证据”；后端聚焦测试 `5 passed, 1 warning`，交叉依赖测试 `31 passed, 6 warnings`，`npm.cmd run build` 通过。下一步建议 BIZ-3c 轻量 MVP 进入小范围试运行观察，完整经营模型留到 BIZ-3d。
- FE-UX-1 前端保守型体验重构一期已完成并通过人工验收（2026-06-02）：新增 `AI_Middle_Office/docs/fe-ux-1-admin-experience-refactor-planning.md` 和 `AI_Middle_Office/docs/fe-ux-1-trial-experience-acceptance.md`，定位为小范围试运行前的中后台体验补强；主参考 `vue-pure-admin` / `Vue3 Element Admin`，信息架构参考 `Ant Design Pro`，视觉干净度参考 `PrimeVue Sakai`。本阶段不换 Vue3 + Vite + Element Plus 技术栈、不搬模板工程、不整站重写、不一次性迁移旧 `index.html`、不新增数据库结构、不改变报价/成本库/项目进度/权限/审计规则。FE-UX-1-1 至 FE-UX-1-5 均已通过人工验收；FE-UX-1-6 已形成试运行体验验收包；脚本解析、9000 页面可达和 `npm.cmd run build` 通过。后续如需更明显的界面变化，可单独规划视觉统一增强专项。
- FE-UX-2 Apple-like 正式视觉增强第一版已完成并通过人工视觉验收（2026-06-02）：新增 `AI_Middle_Office/docs/fe-ux-2-apple-like-visual-upgrade.md`，参考浅色、精致、正式、克制的 Apple-like 气质，但不套 Apple 官网模板、不复刻品牌、不做营销页；Vite 管理台已新增品牌 lockup、登录页品牌介绍区、浅色磨砂 topbar、Element Plus 通用控件增强和卡片统一；旧 `index.html` 报价工作台已同步浅色导航、流程条、消息区、输入区和 AI 预审弹窗卡片质感。不新增数据库结构、不新增 Alembic、不改变报价、成本库、项目进度、RAG、推送、草稿或审计规则；`npm.cmd run build`、旧页内联脚本解析、9000 关键页面可达验证通过，本轮 Browser 截图验收未完成，用户已确认人工验收通过。
- 产品边界：系统完善前不正式投入生产使用；后续阶段先按内网开发/验证推进，最后统一准备正式生产 Runbook。
- 当前未完成/暂缓项：P2 候选 prompt 自动重跑、P5 LangGraph 触发评估。
- 当前代码迁移 head：`20260731_0080`；`0078` 退役执行系统专用表，`0079` 退役商务台账数据与专用 schema，`0080` 退役成本测算闭环三张专属表。内网环境升级前必须先备份数据库；升级后继续保留完整反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 inbound 响应速度追踪、BIZ-2 成本/报价增强、BIZ-3 项目进度、企业/账户定额、预算项目计价和报价资料研判 Agent 等现有能力。
- 最新验证（2026-05-21，BIZ-2f/BIZ-2g 报价需求单 Excel 解析 + 成本库底价兜底填价）：`python -m alembic current` 显示 `20260520_0019 (head)`；`FEATURE_COST_DB=true`、`PUBLIC_ACCESS_ENABLED=false`；成本库当前 `total=197 / active=190 / archived=7`；`.xlsx/.xlsm` 需求单解析不新增数据库结构，上传 Excel 会先转成报价清单文本再进入现有报价、成本库参考、底价兜底和漏项检测链路；旧 `.xls` 提示另存为 `.xlsx`；底价兜底仅在 AI 单价空/0、成本库命中且有数量时生效；`python -m pytest` 为 `168 passed`，`ai-web` 的 `npm run build` 通过。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 最新验证（2026-05-22，BIZ-2h 成本库价格前置给 AI 报价链路）：已完成代码层验证；`FEATURE_COST_DB=true` 时，报价请求进入 N8N/Dify 前会追加 active 成本库强参考上下文；`FEATURE_COST_DB=false` 或无命中时请求文本保持不变；不新增数据库结构，不改 N8N，BIZ-2g 兜底仍作为后置安全网。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 最新验证（2026-05-23，BIZ-2i/BIZ-2j 与预审体验补强）：已完成代码层验证和当前环境手动验收；`index.html` 可展示报价依据与成本库证据链、AI 报价来源、成本库详情按钮、同名不同规格成本条目切换和打回原因追溯；`ai-web` 报价运营详情可展示成本证据、AI 来源和预审打回原因；不新增数据库结构，不改 N8N/Dify，不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 最新验收（2026-05-28，BIZ-2k 成本库数据质量体检 + 演示回归包）：BIZ-2k-1 已补强业务可读版报告和验收指引，用户确认 BIZ-2k 当前环境手工验收通过；原 BIZ-2k 新增 `app/services/cost_data_quality.py`、`scripts/biz2k_cost_quality_report.py` 和 `tests/test_cost_data_quality_biz2k.py`，只读分析 `cost_items.active` 并生成 Markdown/CSV/XLSX/演示回归包；不新增数据库结构，不写数据库，不触发 RAG 同步，不改 N8N/Dify/报价规则。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 最新规划（2026-05-25，BIZ-2l-0 甲方需求单标准字段与典型场景确认）：已完成文档层确认，产物为 `docs/biz-2l-requirement-standardization-biz2l0.md`；首版目标是把不固定甲方需求单整理为标准报价输入，保留原始行追溯、字段映射、置信度、缺失提示和成本库候选。当前不编码、不新增 Alembic、不改报价逻辑。
- 最新验证（2026-05-25，BIZ-2l-1 只读通用清洗解析器）：已完成代码层验证；新增 `app/services/requirement_standardizer.py`、`scripts/biz2l_requirement_standardization_preview.py` 和 `tests/test_requirement_standardizer_biz2l.py`，支持 `.xlsx/.xlsm` 标准化预览 JSON/CSV/Markdown；真实甲方清单“联昇集团办公楼装饰工程清单.xlsx”可解析 8 个 Sheet、299 个标准行，并修复“项目特征误判为项目名称”的表头优先级问题；后端全量测试 `196 passed, 3 warnings`。当前不接报价、不写数据库、不改报价逻辑。
- 最新验证（2026-05-26，BIZ-2l-2 人工列映射与行确认）：已完成当前环境验收；需求单标准化确认界面支持按 Sheet 列映射/行确认、原始行追溯、搜索筛选、标准数量来源、多工程量候选、本地历史解析记录和版本回滚；后端全量测试 `202 passed, 3 warnings`，`ai-web` build 通过。当前不接报价、不写数据库、不改报价逻辑/价格口径。
- 最新验证（2026-05-26，BIZ-2l-3 标准清单接入报价链路）：已完成代码层验证并通过业务验收；确认清单可一键发起现有异步报价任务，创建前重新校验当前确认行，阻断行不进入报价；未通过校验行会展示问题面板并自动定位到对应原始行；后端全量测试 `203 passed, 3 warnings`，`ai-web` build 通过。仍不新增数据库结构、不改报价逻辑/价格口径。
- 最新验证（2026-05-27，BIZ-2l-4 预审对账与运营复核详情）：已完成当前环境业务验收；运行中后端 `/health/ready` 为 ready，169 行任务 `2557d7ba-f0ca-48ba-91c3-5c9c8f993a4c` 的 `/api/v1/quote/jobs/{job_id}/review-detail` 返回 200，确认行 169、预审行 169、需复核 169、高风险 169、无底价参考 153，可继续支撑旧预审弹窗和报价运营详情复核；新增 Alembic `20260526_0022`，不改报价规则/价格口径/无底价自动处理。
- 最新验证（2026-05-27，BIZ-2l-5 确认清单逐行报价完整性保障）：已完成当前环境业务验收；专项测试覆盖确认需求行逐行传给 AI、`requirement_integrity` 完整性摘要、不完整预审 `/confirm_push` 409 阻断、未补价占位行 `/confirm_push` 409 阻断，以及占位行不触发成本库底价兜底；本次针对 BIZ-2l-4/BIZ-2l-5/BIZ-2l-6 的报价任务验收测试为 `7 passed, 1 warning`，不新增数据库结构，不改报价规则/价格口径/无底价自动处理。
- 最新验证（2026-05-27，BIZ-2l-6 确认清单分批报价与缺失占位）：已完成当前环境业务验收；重启后 `/health/ready` 显示 database ok、Celery broker/worker ok，Alembic 为 `20260526_0023 (head)`；169 行确认清单任务 `2557d7ba-f0ca-48ba-91c3-5c9c8f993a4c` 完成，默认每批 20 行切为 9 批，缺失行自动补报 1 次，最终预审保留 169 行并生成 169 行“AI 未返回，需人工补价”占位行，`requirement_integrity.status=complete_with_placeholders`、`missing_count=0`；占位行不触发成本库底价自动兜底，`/confirm_push` 未补价前返回 409 阻断；旧 `index.html` 前端人工验收通过。不改报价规则/价格口径/无底价自动处理。
- 最新文档（2026-05-27，BIZ-2l 验收记录与操作 SOP）：新增 `docs/biz-2l-acceptance-and-sop.md`，整理环境基线、阶段验收记录、169 行大清单验收结果、业务员操作步骤、管理员复核步骤、阻断条件、异常处理和交接检查表；不新增功能、不改代码、不改变 BIZ-2l 边界。
- 最新文档（2026-05-27，BIZ-2 无底价项目处理规则草案）：新增 `docs/biz-2-no-cost-reference-rule-draft.md`，明确无 `cost_items.active` 参考价时允许 AI 估价但必须人工确认，确认下发成功后沉淀为成本库 `draft` 待审核，不能自动 `active`，只有人工启用 `active` 后才参与后续报价；当前只是规则草案和后续开发建议，不改代码、不新增 Alembic。
- 最新验收（2026-05-28，BIZ-2m 无底价项目规则开发落地）：当前环境手工验收已通过；专项与回归测试共 `10 passed`、`25 passed`、`35 passed`，`ai-web` build 通过，旧 `index.html` 脚本语法检查通过；演示与验收记录见 `docs/biz-2m-demo-and-acceptance.md`。
- 最新验收（2026-05-28，BIZ-2n 预审人工改价字段与合计联动）：当前环境手工验收已通过；旧 `index.html` 预审弹窗支持可编辑“工程量/单位”和“人工改价(元)”并联动系统合计；AI 返回工程量 0 但源 Excel 有有效工程量时，后端回填源工程量；脚本语法检查通过；相关确认推送、报价历史、报价反馈、占位阻断和 BIZ-2m draft 沉淀回归 `22 passed, 1 warning`；`ai-web` build 通过。
- 最新验收（2026-05-28，BIZ-2o 成本库状态与流向台账）：当前环境手工验收已通过；成本库/同步/无底价沉淀/确认推送/成本匹配回归 `48 passed, 3 warnings`；清理后关键回归 `17 passed, 1 warning`；`ai-web` build 通过。
- 最新验收（2026-05-28，BIZ-2p 预审人工改价来源判定与 AI 建议采纳）：当前环境手工验收已通过；确认推送专项 `7 passed, 1 warning`；draft 捕获专项 `7 passed, 1 warning`；状态与流向专项 `1 passed, 1 warning`；旧 `index.html` 脚本语法检查通过；`ai-web` build 通过。
- 最新验收（2026-05-28，BIZ-2q 报价预审草稿保存与恢复 / BIZ-2q-2 我的报价历史筛选与草稿清理）：当前环境手工验收已通过；新增 `quote_preview_drafts` 和 Alembic `20260527_0024`；旧 `index.html` 预审弹窗支持人工改价、系统合计、工程量、单位、施工项目、备注、采用 AI 建议和成本库条目切换后的草稿保存与恢复，并支持关闭后从“我的报价历史”的“草稿/编辑”入口继续；“我的报价历史”已补充时间、报价内容、项目数、总价、状态筛选和草稿批量删除，草稿不再固定置顶；打回重填标记 `discarded`，确认下发成功后标记 `pushed`。历史筛选与草稿批量删除补充回归 `11 passed, 1 warning`，旧 `index.html` 脚本语法检查通过，in-app browser 加载无 console error，BIZ-2q 当时数据库已升级到 `20260527_0024 (head)`。
- 最新验证（2026-05-27，BIZ-2r 成本库重复 active 防护与报价多候选提示）：成本库启用前查重、无底价沉淀去重、报价多候选提示和 `/confirm_push` 阻断已完成；专项回归 `53 passed, 3 warnings`，当前环境手动验收已通过。
- 最新文档（2026-05-27，BIZ-2 成本价权限清单草案）：新增 `docs/biz-2-cost-price-permissions-draft.md`，明确普通业务员、成本部业务员、管理员和老板的成本库查看、编辑、启用、导出和 RAG 同步边界；上云前建议新增成本专项角色并收紧当前 `staff` 完整成本库只读能力；当前只是权限草案和后续开发建议，不改代码、不新增 Alembic。
- 最新验收（2026-05-28，BIZ-2s 成本价权限落地首版）：普通 `staff` 已不能浏览完整成本库，但仍可通过报价预审受限候选接口查询必要 active 成本参考；成本专项角色已接入 RBAC、后端接口和 Vite 成本库按钮权限；聚焦回归 `30 passed, 3 warnings`，`ai-web` build 通过，旧 `index.html` 脚本语法检查通过；当前环境手动验收已通过。
- 最新验证（2026-05-28，BIZ-2t 成本库数据治理执行包）：当前环境只读治理报告已生成到 `reports/biz2t/20260528_current/`；高风险 5 条均为已被报价引用但缺少业务可解释来源价的 active 条目，试运行建议为 `cleanup_before_trial`；专项测试 `4 passed, 1 warning`。
- 最新文档（2026-05-28，BIZ-2t-1 高风险整改交接清单）：已形成成本部逐条核价交接文档和可填写 CSV，正式试运行前仍需人工处理或说明。
- 最新复核（2026-05-28，BIZ-2t-2 高风险整改结果复核包）：已只读复核高风险交接 CSV，5 条均为 `accepted_risk`，`trial_blocker_count=0`，建议 `ready_with_known_risks`；正式试运行仍未启动，下一步可登记样例和已知风险。
- 最新文档（2026-05-28，BIZ-2u 小范围内网试运行准备包）：试运行准备文档已形成，覆盖准入门槛、人员角色、样例清单、反馈机制、验收口径和暂停条件；正式试运行未启动。
- 最新文档（2026-05-28，BIZ-2u-1 小范围内网试运行执行模板包）：已形成样例登记、问题反馈、每日检查和验收记录模板；正式试运行仍未启动。
- 最新文档（2026-05-28，BIZ-2u-2 小范围内网试运行启动前登记与检查包）：5 条 `accepted_risk` 已登记为试运行已知风险，启动前检查材料已形成，当前结论为 `ready_with_known_risks_pending_start_confirmation`；正式试运行仍未启动。
- 最新验收（2026-05-28，BIZ-2v-1 报价下发与成本价权限安全加固）：当前环境手工验收已通过；`/confirm_push` 已校验 `quote_job_id` 归属；普通业务员报价候选查询已收紧为受限 active 字段和 2 字以上关键词；active 启用/批量核定要求填写核定原因；后端全量测试 `248 passed, 5 warnings`，前后端构建检查通过；详见 `docs/biz-2v-1-quote-push-permission-hardening.md`。
- 最新验收（2026-05-28，BIZ-2v-2 RAG 同步一致性与滞后提示）：当前环境手工验收已通过；active 到 RAG 同步失败/超时时返回 `synced_count=0`；成本库后台新增 RAG 同步状态摘要与页面提示，能区分已同步、滞后、失败、从未同步和无 active 条目；后端全量测试 `253 passed, 5 warnings`，前后端构建检查通过；详见 `docs/biz-2v-2-rag-sync-consistency.md`。
- 最新验收（2026-05-28，BIZ-2v-3 成本库敏感操作审计与导出控制）：当前环境手工验收已通过；成本库导出已收紧为 `cost_exporter` / 管理员，敏感查看、导出、状态变更、导入确认和 RAG 同步动作写入 `cost_access_audit_logs`，管理员和 `cost_approver` 可查审计；后端全量测试 `257 passed, 5 warnings`，前端 build 通过；详见 `docs/biz-2v-3-cost-audit-export-control.md`。
- 最新验收（2026-05-28，BIZ-2w-1 系统完善审查与账号入口安全加固）：当前环境手工验收已通过；默认关闭自注册并提供 `system_admin` 新建用户入口；详见 `docs/biz-2w-1-system-risk-hardening.md`。
- 最新验收（2026-05-28，BIZ-2w-2 RAG 同步状态时间口径误报修复）：当前真实环境手工验收已通过，验收需求单闭环通过；详见 `docs/biz-2w-2-rag-sync-status-timezone-fix.md`。
- 最新验证（2026-05-28，BIZ-2w-3 成本参考优先与 AI 改写防护）：已完成代码层验证，待当前环境手工验收；原始需求成本依据优先于 AI 返回项目名，AI 改写成本依据时提示并阻断下发；详见 `docs/biz-2w-3-cost-reference-priority-ai-rewrite-guard.md`。
- 最新验收（2026-05-29，BIZ-2w-4 AI 备注与成本依据一致性校验）：已通过当前环境手工验收；成本依据已命中但 AI 原始备注声称无数据/无法报价时，预审可见备注会改为系统建议备注并要求人工确认；详见 `docs/biz-2w-4-ai-note-cost-basis-consistency.md`。
- 最新验证（2026-05-29，BIZ-2w-5 自由文本同名不同规格拆行与工程量识别修复）：已完成代码层验证，待当前环境手工验收；自由文本同名不同规格需求会按顿号拆行，`9.5mm` / `12mm` 保留为规格，工程量读取真实 `8㎡`，避免工程量 0 和规格串项；详见 `docs/biz-2w-5-text-multi-spec-quantity-guard.md`。
- 最新验收（2026-05-29，BIZ-2w-6 报价来源口径与预审列名调整）：已通过当前环境手工验收；预审展示改为成本库依据优先、AI 仅无底价估价、人工确认下发的表达，并补充 AI 占位项目名回填原始需求行保护；详见 `docs/biz-2w-6-quote-source-wording.md`、`docs/biz-2w-6-placeholder-project-name-hotfix.md`。

## 关键模块

- `app/main.py`：FastAPI 入口、HTML 托管、路由注册、健康检查、启动期数据库兼容迁移。
- `app/core/config.py`：集中读取 `.env` 配置，包含 n8n、RAG、Celery、MinIO、代理、数据库和模型网关参数。
- `app/api/v1/auth.py`：JWT 登录、当前用户、改密；Phase 0 token 包含 `roles` / `role_version`。
- `app/api/v1/chat.py`：旧兼容导出层，核心路由已拆分，保留历史 import 路径。
- `app/api/v1/quote.py`：`/chat` SSE 报价流与 `/confirm_push`。
- `app/api/v1/quote_feedback.py`：报价反馈闭环与 admin 反馈分析接口，包括 summary / list / detail。
- `app/api/v1/prompt_regression.py`：Prompt 回归评测接口，包括黄金案例 build/list 和回归报告 run/latest/history。
- `app/api/v1/knowledge_candidates.py`：知识库治理接口，包括候选 build/list/summary、RAG trace 洞察、approve/reject。
- `app/api/v1/materials.py`：旧 materials 只读/退役保护；写入、回滚、旧 sync_milvus 已废弃。
- `app/api/v1/requirement_standardization.py`：BIZ-2l-2 需求单标准化 API，提供解析预览、人工重映射和确认清单生成；无数据库写入。
- `app/api/v1/history.py`：报价历史记录。
- `app/api/v1/users.py`：用户配额管理、system_admin 新建用户、Phase 0 角色授权/撤销和权限历史。
- `app/api/v1/quote_jobs.py`：新版异步报价任务 API，创建、查询、事件流、取消、重试、超时标记；报价运营详情会带出关联报价反馈摘要，用于展示预审打回原因。
- `app/services/quote_cost_matching.py`：BIZ-2b/BIZ-2d/BIZ-2g/BIZ-2h/BIZ-2j/BIZ-2w-3/BIZ-2w-4 成本底价匹配基础能力，给 preview 明细附加 `cost_reference` 与匹配汇总，处理中文符号、单位族和词序差异，并在 AI 单价空/0 且命中底价时保守回填；同时向前置上下文服务提供 active 匹配 helper，补充报价来源解释字段，识别 AI 改写成本依据和 AI 备注与成本依据冲突。
- `app/services/quote_cost_context.py`：BIZ-2h/BIZ-2w-5 报价前置成本上下文，在调用 N8N/Dify 前把命中的 `cost_items.active` 底价、单位、数量和匹配类型追加为 AI 强参考文本；同时处理手输清单的顿号拆行、毫米规格识别和真实工程量抽取。
- `app/services/quote_cost_evidence.py`：BIZ-2i 报价成本证据审计服务，记录并序列化 AI 原始报价、最终报价、成本库参考价、行/整单合计来源、AI 报价来源、成本条目快照和证据链接。
- `app/services/quote_omission_detection.py`：BIZ-2e 保守规则式漏项检测，基于当前报价行和 `cost_items.active` 生成 `omission_summary` / `omission_suggestions`。
- `app/services/quote_excel_parser.py`：BIZ-2f 报价需求单 Excel 解析，支持 `.xlsx/.xlsm` 表头识别并输出报价清单文本。
- `app/services/requirement_standardizer.py`：BIZ-2l-1/BIZ-2l-2/BIZ-2l-3 甲方需求单清洗与人工确认服务，输出标准化 JSON/CSV/Markdown 预览，支持列映射重算、行确认、标准数量来源、多工程量候选和确认清单报价文本；报价文本只作为现有报价任务输入，不改变报价规则。
- `app/services/quote_review.py`：BIZ-2l-4/BIZ-2l-5/BIZ-2l-6/BIZ-2w-4 预审对账与完整性保障服务，读取确认需求行、AI 预审结果和成本证据，输出疑似漏报价、额外预审行、无底价参考、成本库兜底、人工改动、偏离底价和 AI 备注确认等复核检查项，并生成逐行报价提示、分批合并结果、缺失占位行与 `requirement_integrity` 完整性摘要。
- `app/services/quote_helpers.py`：报价通用工具，包含 N8N 签名、报价文件名和编号换行清单输入清洗。
- `app/services/cost_rag_sync.py`：BIZ-2c/BIZ-2v-2 active 成本条目 RAG 同步，将 `cost_items.active` 转换为 RAG `/admin/reload` 兼容 payload，并提供同步失败/滞后状态摘要。
- `app/services/cost_audit.py`：BIZ-2v-3 成本库敏感操作审计服务，记录完整成本库查看、导出、状态变更、导入确认和 RAG 同步等动作。
- `app/services/cost_data_quality.py`：BIZ-2k 成本库只读体检服务，分析 `cost_items.active` 的重复/同名多规格/价格异常/单位异常/规格备注缺失/相似条目，并生成 Markdown/CSV/XLSX/演示回归包数据。
- `app/services/quote_job_runner.py`：后台任务执行链路，负责文件读取、GLM-4V、n8n 调用、结果落库和额度扣减。
- `app/services/quote_feedback.py`：报价反馈闭环服务，记录 AI 初稿、人工确认稿、字段级修正、Dify/prompt 版本和 RAG trace。
- `app/services/prompt_regression.py`：从真实报价反馈固化黄金案例，并计算 prompt 版本的总价偏差、格式错误率、遗漏率、打回率和综合分。
- `app/services/knowledge_candidates.py`：从真实反馈生成知识候选，人工确认后先快照再新增或更新物料库条目。
- `app/services/quote_dispatcher.py`：按 `TASK_QUEUE_MODE` 分发到 Celery/local/inline/disabled。
- `app/services/model_gateway.py`：统一模型与 n8n 调用日志、耗时、错误、熔断。
- `app/services/file_storage.py`：MinIO 文件上传、读取、临时下载链接和健康检查。
- `app/services/rbac.py`：Phase 0 多角色权限、`users.role` 兼容同步、`role_version` 递增和可用模块序列化。
- `app/api/v1/ops.py` + `app/services/ops_monitor.py`：管理员运维面板，聚合基础服务、日志和卡住任务。
- `app/tasks/`：Celery app 与 worker task 入口。
- `alembic/`：数据库迁移基线。
- `verify_startup.ps1`：重启后固定验收脚本，检查 FastAPI、worker、RAG、n8n、MinIO、MySQL、Redis。
- `FRONTEND_ACCEPTANCE.md` / `P4_ACCEPTANCE.md`：手工验收清单。前者覆盖通用前端回归，后者聚焦 P4 业务体验运行态验收（失败可操作原因、预审风险标记、人工修改沉淀知识入口）。
- `run_centos_backup.ps1`：Windows 侧触发 CentOS 备份的入口，实际备份逻辑在 `rag_docker/backup_production.sh`。

## CentOS 侧服务

CentOS 服务由 `/opt/rag_service/docker-compose.yml` 管理，仓库内对应 `rag_docker/docker-compose.yml`：

- `milvus-etcd`
- `milvus-minio`
- `milvus-standalone`
- `rag-api-service`
- `quote-redis`
- `quote-minio`

RAG 服务当前使用离线 HuggingFace 模型路径，挂载：

- `/opt/rag_service/model_cache:/model_cache`
- `/opt/rag_service/hybrid_searcher.py:/app/hybrid_searcher.py:ro`
- `/opt/rag_service/rag_materials.json:/app/rag_materials.json:ro`

当前确认状态：

- `RAG_VECTOR_ENABLED=true`
- RAG 混合检索为向量 + BM25 + RRF
- Milvus alias `enterprise_quotation_rag` 指向 `quotation_blue`
- 正式 RAG/报价成本源为 `cost_items.active`，当前环境 active 190 条
- 正式报价成本价格主库为 MySQL `cost_items` 的 `active` 条目
- `materials` 已清空并退役，不再作为报价/RAG 源；`material_snapshots` 仅作旧审计回溯
- `LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 不再自动导入旧 `rag_materials.json`
- RAG 评测报告目录由 `RAG_EVAL_REPORT_DIR` 控制

## 启动方式

Windows 侧统一启动：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

CentOS 侧如需手动启动，先征求用户许可：

```bash
cd /opt/rag_service && docker compose up -d
```

## 协作约定

- `.env`、真实密钥、数据库文件、缓存和运行日志不提交。
- 破坏性操作、强制推送、清空数据库、删除虚拟机残留、远程 CentOS 命令必须先向用户申请许可。
- 后续提交应排除明显外来目录，例如 `planning-with-files-master/`、`superpowers-main/`、`andrej-karpathy-skills-main/`，除非用户明确要求纳入。
- 代码改动优先保持现有 FastAPI + SQLAlchemy + Vue CDN + Element Plus 的结构，不引入额外前端构建链。

## Backend Refactor Notes

- 既有报价/RAG 链的正式价格源仍是 `cost_items.active`，旧 MySQL `materials` 已退役且不再自动导入 `rag_materials.json`；该链路按现状冻结维护，不得被新预算项目链路复用。未来预算链的唯一正式成本主库是企业定额恰好一个 `active` 版本，若 `active` 数量不为 1 必须阻断。
- RAG eval report output is controlled by `RAG_EVAL_REPORT_DIR` and no longer depends on the legacy material JSON file path.

## 2026-07-28 最新交接

- 旧对话报价的流式输出已改为业务进度卡：后台原始事件只用于内部阶段判断，不进入聊天正文；用户只看到五个业务阶段、当前自然语言动作、友好耗时和持续变化的阶段受控进度条。失败提示也不再暴露内部服务名、任务号或追踪号。联合回归 `45 passed`，旧页脚本解析和 Vite build 通过；浏览器实际验收观察到计算阶段 `77%`、进度宽度 `76.8%`、正文无技术事件，随后正常打开 1 行核对窗口；验收报价未下发。无 Alembic、无业务规则变更。详见 `docs/unified-quotation-chat-entry-correction-20260728.md`。
- 统一报价工作台第一版界面融合已完成：预算项目计价页和旧对话预审弹窗都提供快速审核、专业全字段、费用汇总、报价依据、版本记录五视图，并共用原草稿；完整报价字段、双模式高级对比、风险复核、证据、草稿、导出和下发均未删除。联合回归 `44 passed`，Vite build 和旧页内联脚本解析通过；项目 20 浏览器验收确认五视图及 `198/198` 工程量。旧对话 299 行大预审的自动化浏览器走读因 DOM 响应超时未完成，需由业务人员刷新后补一次实际弹窗走读。无 Alembic、无定额数据或报价规则变更。详见 `docs/unified-quotation-chat-entry-correction-20260728.md`。
- 统一报价双入口的联昇工程量识别差异已修正。旧对话入口上传 Excel 时复用预算项目的工作簿语义预处理、Sheet 角色和工程量选择规则，只把 `bill` 正式清单行送入“账户定额 → 企业定额 → AI估价”三级链路，主材参考表和重复表头不进入报价。真实项目 20 原文件只读内存复验为正式清单 `198=121+73+4`、有效工程量 `198`、缺失 `0`，装饰/机电/措施费分别取 `K/J/E`；联合回归 `138 passed`。本修正不新增 Alembic、不写业务数据，报价 Worker 已重启且 `/health/ready=ready`；修正前旧任务不会回写，必须重新上传文件。详见 `docs/unified-quotation-chat-entry-correction-20260728.md`。

## 2026-07-16 最新交接

- 成本预算对标 Phase 2 P2-2B-1“账户定额库底座”已完成代码、0053 迁移、专项/全量回归、真实 API/数据库闭环和 Chrome 登录态人工验收。新增 `FEATURE_ACCOUNT_QUOTAS`、`account_quota_items` / `account_quota_item_history`、当前账号管理员 CRUD/状态/历史 API 和 Vite `/admin/account-quotas` 页面；API 不接受 `account_id`，跨账号按 404 处理。生命周期为 `draft <-> active -> archived`，archived 冻结；单价使用正数 `NUMERIC(18,6)` / Decimal；账号内以规范化名称、特征、规格、单位的 SHA-256 指纹去重；人工 CRUD 只能写 `manual` 来源，每次修改和状态流转递增 revision 并保存审计快照。0053 前备份为 `172461281` bytes，SHA256 `BFC58D4DB32AC39415B14181D513D90124A7A02237DF2296A76C0E4E7353BD46`；当前环境为 `20260716_0053 (head)`，`/health/ready=ready`、Celery `worker_count=2`。Chrome 完成 `R1 draft 123.456789 -> R2 130.000001 -> R3 active -> R4 draft -> R5 archived`，5 条历史均展示 admin、原因和精确单价，最终新表 `1 item / 5 history / manual / archived / revision 5`。受保护计数始终一致：企业定额 `3/1 active/1422`，正式计价 `4/792/220/4`，计价草稿 `1/198/8`。联合专项 `29 passed`、前端契约 `5 passed`、Vite build `1627 modules`、compileall 通过；全量 `887 passed / 2 个冻结旧 BIZ-2c 失败 / 39 warnings`，无 P2-2B-1 新增失败。本阶段不接 `account_strict` 匹配、不实现“同步到账户定额”、不调用 LLM、不修改企业定额或正式 run；P2-2B-2/P2-2B-3/P2-2C 均未开始。完整合同见 `docs/cost-budget-benchmark-phase2-p2-2b1-account-quota-catalog.md`。
- 成本预算对标 Phase 2 P2-2A“账号隔离 + 双模式计价草稿”已完成代码、Alembic `20260716_0052`、存量回填、联昇运行态和 Chrome 验收。新增 `accounts` / `account_memberships` / `account_budget_projects` 与 account-scoped draft/line/event，预算项目先按当前账号隔离，再应用旧角色与对象权限；API 不接收 `account_id`，跨账号 current/lines/PATCH 返回 404，无成员关系 fail-closed。每个 `account + project` 只有一个可变草稿；`enterprise_ai` 只匹配严格唯一 active 企业定额，未匹配行保持 `NULL` 且 P2-2A 不调用 LLM；`account_strict` 当前不查企业定额，198 行价格保持 `NULL`，账户定额库尚未建立。模式切换和人工改价只递增 draft/line revision，不创建或修改正式 run。0052 前备份 `168194886` bytes，SHA256 `3D8E33324EADD446141EDBE2797E2617E56A4BA7908032DE3CCA5CE701660D84`；迁移回填 `accounts=1 / memberships=56 / budget bindings=12`。联昇 project 15、batch 16、revision 19 使用草稿 `1 / ecb800c4-35cd-47f7-823f-aa077301b52f` 完成 `enterprise_ai Rev1 -> manual Rev2 -> account_strict Rev3`，正式 `3 run / 594 line / 165 candidate / 3 event` 全程未变。全量回归 `876 passed / 2 个冻结旧 BIZ-2c 口径失败`，Vite build 与 Chrome 验收通过。P2-2B 账户定额库、P2-2C AI 估价均未启动；完整契约见 `docs/cost-budget-benchmark-phase2-p2-2a-account-dual-mode-draft.md`。

## 2026-07-15 最新交接

- 成本预算对标 Phase 2 P2-1 企业定额计价底座已获用户明确授权并完成代码、迁移、真实数据和当前环境验收：新增 `FEATURE_BUDGET_PRICING`、Alembic `20260716_0051`、不可变 pricing run/line/candidate/event、严格唯一 `enterprise_quota active` 门禁、Decimal 六位金额、partial/complete 语义、计价 API 和预算项目详情计价页。联昇项目 15 固定批次 16、revision 19、定额版本 3，已形成 3 个不可变 run；最终 run `3 / dc0cb938-6943-4920-bc89-3b708302efbb` 的 `parent_run_id=2`，且使用数据库统一时钟保证 `created_at <= ready_at`。正式清单 198 行，当前不完整定额中健康可用 65/474 条，每个 run 均为 `matched=0 / ambiguous=14 / unmatched=182 / unit_conflict=2`、候选 55、覆盖率 0%、`total_cost=NULL`、状态 `partial`，正确暴露定额覆盖不足。22 张受保护业务表计数未变；旧 `cost_items`、项目测算、采购结果、报价/RAG 链均未读取或写入。最终聚焦审计 `47 passed`（此前 P2 聚焦集 `68 passed`），全量回归 `868 passed / 2 个冻结旧 BIZ-2c 口径失败 / 29 warnings`，无新增失败；`ai-web` build、后端 compileall 和 Chrome run 2 运行态验收通过。证据见 `output/pre_budget_0051_20260715_p2_pricing_foundation/runtime_acceptance_project15.json`，契约见 `docs/cost-budget-benchmark-phase2-pricing-foundation.md`。下一增量是 P2-2 人工选择候选并创建 child run，不允许脱离 active 企业定额手填正式单价。
- 成本预算对标 Phase 1 P1 已完成代码、迁移、真实样本和当前环境运行态 API 闭环验收：在原 `20260714_0049` 底座上新增 Alembic `20260715_0050`、不可变 revision、生命周期事件、`parsed -> confirmed -> active -> superseded` 状态机、项目活动批次/revision 双指针、409 并发保护、对象 capabilities 和统计不污染保护。联昇验收项目 `15 / PRJ-20260715-003` 共 8 个 Sheet、434 行输出；正式清单 198=`121+73+4` 且 `valid=198 / zero=0 / missing=0`，主材参考 99=`86+11+2`，装饰/机电/措施费取 `K/J/E`，F78/F80 公式错误留证，row 74 名称完整且全表名称不一致为 0。活动批次 16（UUID `5bdcd778-1d70-40e2-9206-ccf23d575ca1`），initial revision 18、confirmed revision 19；confirm/activate 均 200，confirmed remap 返回 409 frozen、过期修订返回 409 conflict；第二批次 17 保持 `parsed`，活动指针仍为 16/19，项目内恰好 1 个 active 批次，事件完整记录 `parsed -> confirmed -> active`。批次 15 为预修复 `parsed` 批次且未启用，验收项目保留未归档。运行态证据快照为 `output/pre_budget_0050_20260715_p1_closeout/runtime_acceptance_project15.json`。
- Phase 1 P1 的信达 127 项空工程量置 0 原验收事实继续有效；0050 前已备份，迁移/运行态验收后 16 张受保护表逐项未变，企业定额仍恰好 1 个 `active`。验证为 P1 聚焦 `42 passed`、统计不污染 `19 passed`、前端契约 `30 passed`、`ai-web` build 通过、后端全量 `839/841`，另 2 项为冻结旧 BIZ-2c 非阻断遗留。本 P1 对企业定额零条目读取、零写入、零匹配，只校验 `active` 版本数量；未来预算链只允许企业定额唯一 `active` 作为正式成本主库；旧 `cost_items` 报价/RAG 链冻结不得复用；“项目测算/采购结果”冻结；不接计价或四库；原件仍为 `metadata-only`。P1 可以进入 Phase 2 数据契约/方案设计，但不自动授权计价实现，仍需用户明确决定。详见 `docs/cost-budget-benchmark-phase1-budget-project-foundation.md`。
