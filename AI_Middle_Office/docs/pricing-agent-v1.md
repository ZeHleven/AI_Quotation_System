# 智能组价 Agent v1.1（准确＋近似混合检索）

## 目标

本阶段用于内部开发验收，在不修改现有 `/chat`、`/api/v1/quote/jobs`、预算报价草稿和确认下发链路的前提下，验证三种组价依据，并补齐“准确＋近似”的关键词与向量混合召回：

- 存档数据：用户历史带价 `.xlsx` / `.xlsm` 清单。
- 企业数据：当前唯一启用且健康的企业定额版本，只读。
- 行业数据：真实模型根据地区、业态和装修档次进行 AI 推算；证据标签固定显示为“行业数据·AI推算”。

数据库表、API、前端路由和文件存储前缀均使用独立 `pricing_agent` 命名空间。当前内网开发环境已启用 Agent、近似匹配和混合检索；行业数据仍保持关闭。

## 第一版流程

1. 用户导入历史带价清单。
2. 系统按固定系统字段自动识别项目编码、项目名称、规格/特征、单位、工程量、综合单价和合价，不要求用户人工映射。
3. 原文件保存到本机目录或 MinIO；可检索报价行保存到 MySQL。
4. 用户导入当前待套价清单，选择地区（市）、行业/业态、装修程度、组价依据和匹配形式。
5. Agent 先执行准确匹配；选择“准确＋近似”时，再调用关键词与向量混合检索，按 RRF 融合、业务阈值、单位兼容和动作词保护生成逐行候选。
6. 用户在证据链中人工采用其他候选时，系统只接受本次运行中已保存的候选标识，由后端重新取价、计算合价并持久化选择人、选择时间、版本和最近 20 次选择记录；刷新页面后可通过 `run_uuid` 恢复。
7. 未匹配项目可在组价结果中直接人工补充单价。后端按工程量计算合价，并保存操作者、操作时间、补价原因、决策版本和最近 20 次变更记录。
8. 用户整单确认后，系统创建一条已完成的现有 `QuoteJob`，并通过原 `save_preview_draft` 服务写入 `QuotePreviewDraft`。尚未补价的项目也可作为明确的“待补价占位行”进入草稿；现有确认下发接口会阻断任何单价或合价未补齐的占位行。同一运行重复确认保持幂等，不会生成多个草稿；确认后 Agent 结果冻结，报价草稿仍可继续编辑、保存、进入详细报价工作台和后续下发流程。

### 匹配形式

- `exact`（准确）：只进行编码/名称、规格和单位的精准匹配，不调用词法近似、向量检索或行业 AI。无法命中的行保持未计价。
- `expanded`（准确+近似）：精准匹配后增加 BM25 关键词检索与 BCEmbedding 向量检索，经 RRF 融合后生成最多 5 条候选。近似候选永不自动采用，必须由人工明确选择；只有没有存档/企业候选且勾选行业数据时，才调用真实 AI 模型。

地区、业态和装修程度会形成上下文查询，只用于软排序和行业 AI 输入，不作为存档文件具备这些事实的声明，也不作硬过滤。

### 混合检索架构

- Windows FastAPI 不加载向量模型，也不直接连接 Milvus；它复用 CentOS RAG 服务已认证的混合检索契约。
- 每个存档文件、每个当前企业定额版本使用独立索引作用域，首次查询按需建立索引。
- RAG 索引只保存检索文本和记录标识，候选价格、单位、规格等业务事实始终从当前 MySQL 源记录重新读取；MySQL 是价格权威源。
- 候选证据保存关键词分数、向量分数、BM25/RRF 分数、召回通道和匹配类型，便于人工复核。
- 单位不兼容或“安装/拆除”等动作语义冲突的结果会被过滤。混合服务不可用时降级到本地关键词近似，不影响准确匹配。

## 存储

本机开发默认使用：

```text
AI_Middle_Office/data/pricing_agent_archives/
```

可通过 `PRICING_AGENT_ARCHIVE_LOCAL_ROOT` 指向其他本机磁盘目录。`PRICING_AGENT_ARCHIVE_STORAGE_BACKEND=auto` 时，`MINIO_ENABLED=false` 使用本地目录，`MINIO_ENABLED=true` 使用 MinIO。

第一版默认限制：

- 单文件 30 MB；
- 单账户原文件总量 20 GB；
- 单文件最多索引 100,000 条报价行；
- 账户内按文件 SHA-256 去重；
- 停用存档后不参与检索，但原文件保留，因此仍计入存储占用。

对当前 100 GB 数据盘，建议内部试运行先保留 20 GB 账户上限并监控实际增长，不要把 MySQL、日志、备份和原文件共同视为可使用全部 100 GB。

## 开关

```dotenv
FEATURE_PRICING_AGENT=true
FEATURE_PRICING_AGENT_EXPANDED_MATCH=true
FEATURE_PRICING_AGENT_HYBRID_SEARCH=true
FEATURE_PRICING_AGENT_INDUSTRY_ESTIMATE=false
PRICING_AGENT_HYBRID_TOP_K=20
PRICING_AGENT_HYBRID_SHARD_ROWS=5000
PRICING_AGENT_HYBRID_MIN_VECTOR_SCORE=0.72
PRICING_AGENT_ARCHIVE_STORAGE_BACKEND=auto
PRICING_AGENT_ARCHIVE_LOCAL_ROOT=./data/pricing_agent_archives
PRICING_AGENT_ARCHIVE_MAX_UPLOAD_MB=30
PRICING_AGENT_ARCHIVE_ACCOUNT_QUOTA_GB=20
PRICING_AGENT_ARCHIVE_MAX_INDEXED_ROWS=100000
```

行业数据只有在 `FEATURE_PRICING_AGENT_INDUSTRY_ESTIMATE=true`、`BUDGET_PRICING_AI_PROVIDER=deepseek` 且配置真实 `DEEPSEEK_API_KEY` 时返回。未配置真实模型时保持未计价，不生成伪造的“行业价”。

## 数据库与入口

- Alembic：`20260730_0075`（Agent 基础表）、`20260731_0077`（人工候选决策与报价草稿确认）
- 表：`pricing_archive_files`、`pricing_archive_lines`、`pricing_agent_runs`、`pricing_agent_run_lines`
- API 前缀：`/api/v1/pricing-agent`
- Vite 页面：`/admin/pricing-agent`

新增闭环接口：

- `PUT /api/v1/pricing-agent/runs/{run_uuid}/lines/{line_uuid}/selection`
- `PUT /api/v1/pricing-agent/runs/{run_uuid}/lines/{line_uuid}/manual-price`
- `POST /api/v1/pricing-agent/runs/{run_uuid}/confirm-to-quote-draft`

本阶段不新增 Alembic，功能所需结构在 `20260731_0077` 已具备。当前内网数据库已随系统退役维护升级到 `20260731_0080`，包含本功能所需迁移；仓库后续出现的 `20260801_0081` 不属于组价 Agent v1.1，是否升级应按对应功能单独验收。

## 回归与当前环境验收

固定回归集覆盖准确命中、存档近似、企业近似、动作冲突和完全无关五类场景：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe scripts/pricing_agent_v1_1_eval.py --account-id 1
```

当前结果：

- 准确匹配准确率：`1.0`
- 近似候选 `Recall@5`：`1.0`
- 近似候选自动采用数：`0`
- 无关项目候选数、自动计价数：均为 `0`
- 组价 Agent 专项测试：`29 passed`
- 相邻报价/RAG 回归：`63 passed`
- Vite production build：通过

真实存档数据已验证“拆除单扇木质门”召回“拆除单开实木门”；“安装单扇木质门”不会误收拆除候选。真实企业定额已验证“墙面乳胶漆涂刷”召回 `ZS00357 墙面乳胶漆（2遍）`。这些近似结果均保持未选择、未计价，等待人工采用。

2026-08-01，用户已完成登录后的业务验收并确认通过。v1.1 的准确匹配、近似候选必须人工采用、人工补价、决策持久化、确认写入现有报价草稿、占位行下发阻断以及不修改企业定额 active 等既定口径自此冻结；后续只按真实缺陷增量维护。

下一阶段为 v1.2“行业数据 AI 估价”：仅在存档数据和企业数据均无可用候选时，根据用户选择的城市、行业/业态和装修程度生成可审计的行业估价建议，并继续执行人工确认后才能进入报价草稿的安全边界。

## 第一版已知边界

- 新来源首次查询需要建立索引，大型企业定额首轮可能较慢；正式开放前应增加异步预索引、进度和失败重试。
- 上传和解析当前为同步执行，适合内部、小文件试用；正式开放前应改为异步任务并补进度。
- 尚未启用 GraphRAG。只有普通混合检索无法解决可证明的多跳关系问题时，才单独评估。
- 第一版运行结果仍是旁路建议，不自动写回任何现有报价、预算草稿、企业定额或账户定额；只有用户显式整单确认时写入现有报价预审草稿。
- 未补价项目允许进入报价草稿是为了不中断整单复核，不代表可以跳过价格确认；最终下发继续执行“单价和系统合计必须均大于 0”的硬门禁。
- 写入报价草稿不会同步修改企业定额、账户定额或任何 active 价格源。
