# AI 智能报价中台 — 项目上下文

@AI_Middle_Office/AGENTS.md
@ROADMAP.md

详细文档见 `AI_Middle_Office/AGENTS.md`，以下为快速摘要。

---

## 系统架构

```
用户前端 (Vue.js)
      ↓
FastAPI 主网关 (Windows, Port 9000)
      ↓              ↓
   N8N 工作流      GLM-4V 图像识别 (智谱 AI)
      ↓
  Dify + DeepSeek-R1（报价优化）
      ↓
RAG 检索服务 (CentOS 192.168.88.128, Port 8001)
      ↓
Milvus 向量数据库 (CentOS, Port 19530)
```

## 双机部署

| 机器 | 角色 | 运行方式 |
|------|------|---------|
| Windows | FastAPI 主网关、Vue.js 前端 | `C:\Users\12521\miniconda3\python.exe` + uvicorn |
| CentOS 7.9 (192.168.88.128) | N8N、Dify、RAGFlow、Milvus、RAG服务 | Docker Compose `/opt/rag_service/` |

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
    ├── docker-compose.yml           # CentOS 端服务编排
    ├── Dockerfile                   # python:3.10-slim + jieba
    ├── hybrid_searcher.py           # 混合检索（向量+BM25+RRF）
    └── rag_api_service.py           # RAG 服务入口
```

## 核心配置

- N8N: `http://192.168.88.128:5678/webhook/budget-calc` / `budget-push`
- Milvus: `192.168.88.128:19530`，集合别名 `enterprise_quotation_rag`（蓝绿：`quotation_blue` / `quotation_green`）
- 向量模型：`maidalun1020/bce-embedding-base_v1`，768维，COSINE，HNSW
- RAG 服务: `http://192.168.88.128:8001`

## 当前完成状态

- 后端重构 P0-P3、补充一致性优化、运维告警收敛已完成并冻结；后续仅按真实问题增量维护。
- 前端优化 P0-P3 已完成：验收清单、共享浏览器逻辑、admin 模块拆分、报价进度/失败恢复/上传推送状态均已落地并手工验证。
- 业务优化 P0-P4 已完成到代码层：报价反馈闭环、Admin 反馈分析、Prompt 回归、知识候选治理、真实用户体验优化均已落地。
- AI 平台架构升级 Phase 0 已完成开发与当前环境验证（2026-05-18）：Vite 壳、`/login`、`/admin/permissions`、RBAC、`role_version`、SPA fallback 已通过；旧 `index.html` / `admin.html` / `app.html` 保留。正式生产上线尚未发生，未来需单独 Runbook。
- AI 平台架构升级 Phase 1 报价速度看板已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_DASHBOARD_QUOTE`、报价速度聚合接口和 `/admin/dashboard` 看板视图；已修复新报价任务 `duration_ms` 实测写入，并在备份后回填历史 121 条成功任务的 0 耗时记录；页面、看板数据和新增真实报价统计均已确认正常，正式生产启用待单独 Runbook。
- AI 平台架构升级 Phase 2 响应速度追踪已完成当前环境运行态验收（2026-05-18）：新增 `FEATURE_CLIENT_INQUIRY`、`FEATURE_DASHBOARD_RESPONSE`、`client_inquiries`、报价任务咨询关联、咨询查询/修正接口和 `/admin/dashboard` 响应速度标签页；当前环境 Alembic 已升级到 `20260514_0012 (head)`，功能开关已打开且 `PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已展示 1 条可信响应样本，平均首次响应 15 分钟，Celery worker Phase 2 元数据加载问题已修复。
- AI 平台架构升级 Phase 2.5 管理员报价运营闭环已完成当前环境验证（2026-05-18）：复用 `quote_jobs`、`client_inquiries`、`quote_history`，在 `/admin/dashboard` 新增“报价运营”标签页；提交人列、筛选、任务详情、重试/取消/超时标记入口已落地；不新增数据库结构，不启动 Phase 3。
- AI 平台架构升级 Phase 3 执行速度追踪已完成当前环境验证（2026-05-19）：新增 `execution_tasks`、`execution_task_events`、`FEATURE_EXECUTION`、`FEATURE_DASHBOARD_EXECUTION`、执行任务 CRUD/取消接口、执行速度聚合接口、`/admin/execution` 任务页和 `/admin/dashboard` 执行速度标签页；当前环境已打开开关并验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已显示取消数量。
- AI 平台架构升级 Phase 4a 手动会议纪要 + 草稿确认已完成当前环境运行态验收（2026-05-19）：新增 `meeting_notes`、`task_drafts`、`meeting_note_revisions`、`FEATURE_MEETING_AI`、会议纪要接口和 `/admin/execution` 会议纪要标签页；当前环境已打开 `FEATURE_MEETING_AI=true` 且 `PUBLIC_ACCESS_ENABLED=false`，内网 smoke 已验证纪要提取草稿、确认写入 `execution_tasks`、人工补充后作废、revision 补充任务和 `/admin/execution` 访问。当前不启动 Phase 4b/4c/6，不迁移旧 `index.html` / `admin.html`。
- AI 平台升级 BIZ Track BIZ-1a 商务台账 v1 已完成当前环境验证（2026-05-20）：新增 `FEATURE_BUSINESS_LEDGER`、Alembic `20260520_0016`、商务台账接口和 `/admin/business-ledger` 页面；商务/市场部后续 BIZ-1b/BIZ-1c 暂停，BIZ-1d 外部项目源自动筛选与联系方式获取待定。
- AI 平台升级 BIZ Track BIZ-2a 成本数据库初始化已完成当前环境验证（2026-05-20）：新增 `FEATURE_COST_DB`、Alembic `20260520_0017` / `20260520_0018`、`cost_items`、`cost_item_history`、成本库接口、旧 Excel 导入预览/确认和 `/admin/cost-db` 页面；保留对甲税前综合单价、劳务发包综合单价、班组标底税前价三类价格，并补充人工费、主材费、辅材费等拆分价；2026-05-21 补充“撤回启用”和批量状态流转能力，支持单条/批量 `draft -> active`、`active -> draft` 并写入状态历史，`archived` 仍冻结不可撤回。
- AI 平台升级 BIZ Track BIZ-2b 报价时材料底价查询已完成代码层验证（2026-05-21）：新增报价结果成本参考匹配服务，接入同步 `/chat` 与异步 `quote_jobs` preview 结果；仅匹配 `active` 成本条目，优先 `item_name + spec` 精确匹配，其次 `item_name` 模糊匹配；旧 `index.html` 预审弹窗已展示“成本库参考价 vs AI 生成价”和价差提示。不新增 Alembic；当前环境成本库 `active=190`，已具备真实运行态成本参考匹配数据基础。
- AI 平台升级 BIZ Track BIZ-2c 成本库主库化 + active RAG 同步已完成当前环境验证（2026-05-21）：新增 active 成本条目 RAG 同步服务、`POST /api/v1/admin/cost-items/sync-rag` 管理员接口、Alembic `20260520_0019` 同步记录表 `cost_rag_sync_runs`、`GET /api/v1/admin/cost-items/sync-rag/runs` 和 Vite `/admin/cost-db`“同步 active 到 RAG / 同步记录”窗口；同步源只取 `cost_items.active`。旧 `materials` 作为报价/RAG 源已退役，70 条测试数据已备份后清空，旧 materials 写入/回滚、旧 `/admin/sync_milvus` 和旧知识候选 approve 均返回 410。
- AI 平台升级 BIZ Track BIZ-2d 成本库参考价命中率优化已完成代码层验证（2026-05-21）：补强 `quote_cost_matching` 的中文名称归一化、符号/连接词处理、词序无关 token 匹配、单位族兼容和动作词误命中保护；“窗帘盒/灯槽拆除”类写法可命中 active 成本库底价；编号换行清单在发送 N8N 前会自动清洗成分号清单，避免 `1. / 2. / 3.` 多行需求触发空响应。不新增 Alembic，不启动漏项检测。
- AI 平台升级 BIZ Track BIZ-2e 漏项检测已完成代码层验证（2026-05-21）：新增保守规则式漏项检测服务，复用同步 `/chat` 与异步 `quote_jobs` preview 成本库 enrichment；仅基于 `cost_items.active` 生成 `omission_summary` / `omission_suggestions`，旧 `index.html` 预审弹窗展示疑似漏项、触发行、原因和成本库参考价；不自动新增报价行、不改变合计、不新增 Alembic。
- AI 平台升级 BIZ Track BIZ-2f 报价需求单 Excel 解析已完成代码层验证（2026-05-21）：新增 `quote_excel_parser`，`index.html` 支持上传 `.xlsx/.xlsm` 需求单；同步 `/chat` 与异步 `quote_jobs` 会直接解析施工项目、数量、单位、规格/特征和备注后进入现有报价流程，不再把 Excel 交给 GLM-4V；旧 `.xls` 明确提示另存为 `.xlsx`。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2g 成本库底价兜底填价已完成代码层验证（2026-05-21）：当 AI 预审单给出空/0 单价但已命中 `cost_items.active` 底价且可解析数量时，报价预审会使用成本库参考价回填单价和合计，并标记“已用成本库底价兜底”；AI 已给出正常正数单价时不覆盖。不新增 Alembic，不改 N8N。
- AI 平台升级 BIZ Track BIZ-2h 成本库价格前置给 AI 报价链路已完成代码层验证（2026-05-22）：新增报价前置成本上下文服务，在 FastAPI 调用 N8N/Dify 前基于 `cost_items.active` 匹配需求项，把命中的底价、单位、数量、匹配类型和参考合计作为强参考文本追加到 `text.content`；同步 `/chat` 与异步 `quote_jobs` 已接入，BIZ-2g 事后兜底继续保留。不新增 Alembic，不改 N8N，不改旧 HTML 迁移策略。
- AI 平台升级 BIZ Track BIZ-2i 报价可解释性与审计记录已完成代码层验证（2026-05-22）：报价确认/打回后记录成本证据，包含 AI 原始报价、最终报价、成本库参考价、成本条目快照、行合计来源、整单合计来源、AI 报价来源和证据链接；后台报价反馈/报价运营可追溯。不新增 Alembic。
- AI 平台升级 BIZ Track BIZ-2j 报价依据与成本库证据链展示优化已完成代码层验证（2026-05-22）：`index.html` 预审“查看依据”弹窗已按 AI 报价来源、AI 报价依据、成本库参考、合计对照等分区展示，成本库详情链接改为按钮；后台报价任务详情展示 AI 来源和成本证据。
- BIZ-2 预审体验补强已完成当前环境手动验收并推送（2026-05-23）：品牌文案统一为“旗胜智价”；预审阶段支持同名不同规格成本条目切换，切换后同步采用新成本条目参考价重算单价/合计；AI 来源可区分采纳/偏离前置成本库、无成本库参考 AI 估算、成本库兜底和人工切换；报价运营详情展示预审打回原因。
- AI 平台升级 BIZ Track BIZ-2k 成本库数据质量体检 + 演示回归包已通过当前环境手工验收（2026-05-28，BIZ-2k-1 报告可读性补强后复验）：新增只读 `cost_items.active` 体检服务和 `scripts/biz2k_cost_quality_report.py`，可生成 Markdown/CSV/XLSX 与演示回归包，覆盖同名不同规格、价格为空/0、单位异常、规格备注缺失、相似条目和 RAG 同步数量提示；不新增 Alembic，不新增页面/API，不改报价逻辑，不写数据库。
- AI 平台升级 BIZ Track BIZ-2l-0 甲方需求单标准字段与典型场景确认已完成文档层确认（2026-05-25）：新增 `docs/biz-2l-requirement-standardization-biz2l0.md`，明确标准报价行字段、字段别名、单位数量规则、行类型、典型场景、置信度、警告码、人工确认口径和 BIZ-2l-1 输出合同；不编码、不改数据库、不改报价规则/价格口径、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2l-1 只读通用清洗解析器已完成代码层验证（2026-05-25）：新增 `app/services/requirement_standardizer.py`、`scripts/biz2l_requirement_standardization_preview.py` 和 `tests/test_requirement_standardizer_biz2l.py`，可输出标准化 JSON/CSV/Markdown 预览；不接报价、不写数据库、不改报价逻辑/价格口径。
- AI 平台升级 BIZ Track BIZ-2l-2 人工列映射与行确认已完成当前环境验收（2026-05-26）：新增 `/admin/requirement-standardization` Vite 页面和 `app/api/v1/requirement_standardization.py` 标准化 API，支持上传 `.xlsx/.xlsm` 解析预览、按 Sheet 人工列映射、按 Sheet 行确认、原始行追溯、标准数量来源与多工程量候选、搜索/筛选、确认清单生成、本地历史解析记录和版本回滚；历史进度保存在浏览器 IndexedDB，不写数据库、不新增 Alembic；仍不接报价、不改报价逻辑/价格口径、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2l-3 标准清单接入报价链路已完成代码层验证（2026-05-26）：需求单标准化确认页新增“发起报价”，发起前重新调用确认接口校验当前行，只将已确认且通过校验的标准行组装为 `quote_text` 并调用现有 `/api/v1/quote/jobs` 异步报价任务；阻断行、剔除行、说明/汇总/空白行不进入报价；创建任务后自动跳转旧报价工作台 `index.html` 接管进度并复用原有 AI 预审弹窗人工验收；行确认支持按当前筛选结果全选、取消选择、批量确认和批量撤回确认；若生成确认清单或发起报价前存在阻断行，界面展示校验问题面板、中文错误原因和原始行内容，并自动切换到“未通过校验”定位首条问题行；复用现有报价、成本库前置参考、成本库匹配、漏项检测、底价兜底和证据链；不新增数据库结构、不新增 Alembic、不改报价逻辑/价格口径、不自动沉淀成本库。
- AI 平台升级 BIZ Track BIZ-2l-4 预审对账与运营复核详情已完成当前环境业务验收（2026-05-27）：新增 Alembic `20260526_0022` 和 `quote_job_requirement_rows`，报价任务创建时持久化人工确认的标准需求行；新增 `/api/v1/quote/jobs/{job_id}/review-detail`，对账确认清单与 AI 预审条目，标出疑似未报价、额外预审行、无底价参考、成本库兜底、人工改动过大、偏离底价过大等复核项；169 行任务 `2557d7ba-f0ca-48ba-91c3-5c9c8f993a4c` 已验证 `review-detail` 返回 200，确认行 169、预审行 169、需复核 169、高风险 169，可用于旧预审弹窗和 Vite 报价运营详情继续追溯；不改报价规则、价格口径、无底价自动处理和成本库沉淀逻辑。
- AI 平台升级 BIZ Track BIZ-2l-5 确认清单逐行报价完整性保障已完成当前环境业务验收（2026-05-27）：异步报价执行时读取已持久化的确认需求行，将 `requirement_row_key`、Sheet、原始行号、项目名、规格、数量、单位和备注追加为结构化逐行报价要求，明确禁止 AI 合并、抽样或省略确认行；AI 预审结果会生成 `requirement_integrity` 完整性摘要，`/api/v1/quote/jobs/{job_id}/review-detail` 和报价运营详情展示完整/不完整状态；验收测试已覆盖不完整预审 `/confirm_push` 409 阻断、逐行 key 匹配、占位未补价阻断和占位不走成本库底价兜底。不新增数据库结构、不改报价规则/价格口径、不改无底价自动处理、不自动沉淀成本库。
- 产品边界：系统完善前不正式投入生产使用；后续阶段先按内网开发/验证推进，最后统一准备正式生产 Runbook。
- 当前未完成/暂缓项：P2 候选 prompt 自动重跑、P5 LangGraph 触发评估。
- 架构升级路线按 `docs/superpowers/specs/2026-05-14-ai-platform-upgrade-design.md` 和 `ROADMAP.md` 的 BIZ Track 分阶段执行；当前 BIZ-2 报价系统增强主链路已推进到 BIZ-2w-6，BIZ-2l-0/BIZ-2l-1/BIZ-2l-2 已完成，BIZ-2l-3/BIZ-2l-4/BIZ-2l-5/BIZ-2l-6 已通过当前环境业务验收，BIZ-2k、BIZ-2m、BIZ-2n、BIZ-2o、BIZ-2p、BIZ-2q、BIZ-2q-2、BIZ-2r、BIZ-2s、BIZ-2v-1、BIZ-2v-2、BIZ-2v-3、BIZ-2w-1、BIZ-2w-2、BIZ-2w-4、BIZ-2w-6 均已通过当前环境手工验收，BIZ-2w-3 和 BIZ-2w-5 已完成代码层验证并待当前环境手工验收，BIZ-2t 成本库数据治理执行包已生成当前环境只读治理基线，BIZ-2t-1 高风险整改交接清单已形成，BIZ-2t-2 高风险整改结果复核包已完成且结论为 `ready_with_known_risks`，BIZ-2u 小范围内网试运行准备包已完成文档层准备，BIZ-2u-1 小范围内网试运行执行模板包已形成，BIZ-2u-2 小范围内网试运行启动前登记与检查包已形成；不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 当前代码迁移 head：`20260528_0025`；正式报价成本与 RAG 源为 MySQL `cost_items.active`，旧 `materials` 已清空退役、`material_snapshots` 仅作旧审计回溯；报价反馈新增 `quote_feedback` / `quote_corrections` / `quote_rag_traces`，Prompt 回归评测新增 `prompt_regression_cases` / `prompt_regression_runs`，知识库治理新增 `knowledge_candidates`，Phase 0 RBAC 新增 `users.role_version` / `dingtalk_user_id` / `dingtalk_bound_at`、`user_roles`、`user_role_events`，Phase 2 响应速度新增 `client_inquiries` 和 `quote_jobs.client_inquiry_id`，Phase 3 执行速度新增 `execution_tasks` 和 `execution_task_events`，Phase 4a 会议纪要新增 `meeting_notes`、`task_drafts` 和 `meeting_note_revisions`，BIZ-1a 新增 `client_inquiries.direction/stage/next_followup_at/cancelled_*` 与 `client_inquiry_events`，BIZ-2a 新增 `cost_items` 和 `cost_item_history`，BIZ-2c 新增 `cost_rag_sync_runs`，BIZ-2l-4 新增 `quote_job_requirement_rows`，BIZ-2l-6 补强 `quote_jobs` / `quote_job_events` 大 JSON 字段为 `LONGTEXT`，BIZ-2q 新增 `quote_preview_drafts`，BIZ-2v-3 新增 `cost_access_audit_logs`。
- 内网验证数据库若低于 `20260528_0025`，需执行 Alembic 升级后启用完整报价反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 响应速度追踪、Phase 3 执行速度追踪、Phase 4a 会议纪要草稿确认、BIZ-1a 商务台账、BIZ-2a 成本数据库、BIZ-2c RAG 同步记录、BIZ-2l 确认需求行对账记录、大清单预审结果持久化能力、BIZ-2q 预审草稿保存能力和 BIZ-2v-3 成本库审计日志。
- 新增数据库字段/表必须走 Alembic revision，不能退回依赖 `AUTO_CREATE_TABLES` 或启动兼容迁移。
- `LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 不再自动导入旧 `rag_materials.json`；RAG 评测报告目录由 `RAG_EVAL_REPORT_DIR` 控制。
- 最新验证（2026-05-21，BIZ-2f/BIZ-2g 报价需求单 Excel 解析 + 成本库底价兜底填价）：`python -m alembic current` 显示 `20260520_0019 (head)`；`FEATURE_COST_DB=true`、`PUBLIC_ACCESS_ENABLED=false`；成本库当前 `total=197 / active=190 / archived=7`；`.xlsx/.xlsm` 需求单解析不新增数据库结构，上传 Excel 会先转成报价清单文本再进入现有报价、成本库参考、底价兜底和漏项检测链路；旧 `.xls` 提示另存为 `.xlsx`；底价兜底仅在 AI 单价空/0、成本库命中且有数量时生效；`python -m pytest` 为 `168 passed`，`ai-web` 的 `npm run build` 通过。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
- 最新验证（2026-05-22，BIZ-2h 成本库价格前置给 AI 报价链路）：已完成代码层验证；`FEATURE_COST_DB=true` 时，FastAPI 会在请求进入 N8N/Dify 前为命中 `cost_items.active` 的需求项追加 `[成本库底价强参考]` 上下文，包含底价、单位、数量、匹配类型、`cost_item_id` 和参考合计；`FEATURE_COST_DB=false` 或无命中时保持原请求文本不变；不新增数据库结构，不改 N8N，BIZ-2g 兜底仍作为后置安全网。当前不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。
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
- 最新验收（2026-05-28，BIZ-2m 无底价项目规则开发落地）：已通过当前环境手工验收；新增 `app/services/no_cost_draft_capture.py`、`FEATURE_NO_COST_DRAFT_CAPTURE`、`/confirm_push` 成功后无底价 draft 捕获、成本库来源筛选、旧预审弹窗无底价固定提示和下发成功 draft 摘要；自动生成项只写入 `cost_items.draft`，不自动 `active`，draft 不参与后续报价/RAG/兜底，未补价占位继续阻断，已补价占位可沉淀；BIZ-2p 后来源按手动改价/采用 AI 建议细分。新增 `docs/biz-2m-demo-and-acceptance.md` 记录演示与验收。专项与回归测试共 `10 passed`、`25 passed`、`35 passed`，`ai-web` build 通过，旧 `index.html` 脚本语法检查通过；重启后 `/health/ready=ready`，Celery worker_count=1，Alembic `20260526_0023 (head)`，`FEATURE_NO_COST_DRAFT_CAPTURE=True`；不新增 Alembic，不改报价价格口径。
- 最新验收（2026-05-28，BIZ-2n 预审人工改价字段与合计联动）：已通过当前环境手工验收；旧 `index.html` 预审弹窗新增可编辑“工程量/单位”和“人工改价(元)”列，人工改价默认取成本库参考价，无成本库参考则默认 0；修改工程量或人工改价后按工程量联动系统合计；AI 返回工程量 0 但源 Excel 有有效工程量时，后端回填源工程量；下发前写回 `unit_price/total_price`，未补有效人工改价或系统合计前阻断推送；详见 `docs/biz-2n-manual-price-preview.md`。旧 `index.html` 脚本语法检查通过，相关确认推送/报价历史/报价反馈/占位阻断/BIZ-2m draft 沉淀回归 `22 passed, 1 warning`，`ai-web` build 通过；不新增 Alembic，不改 N8N/Dify，不改成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2o 成本库状态与流向台账）：已通过当前环境手工验收；Vite `/admin/cost-db` 新增“状态与流向”入口，可按总览、新增 draft、active 记录、归档记录查看成本条目来源、当前去向、生命周期和报价引用；后端新增只读 lineage 汇总/列表/详情接口，复用 `cost_items`、`cost_item_history`、`quote_cost_evidence` 和 `cost_rag_sync_runs`；详见 `docs/biz-2o-cost-lineage.md`。成本库/同步/无底价沉淀/确认推送/成本匹配回归 `48 passed, 3 warnings`，清理后关键回归 `17 passed, 1 warning`，`ai-web` build 通过；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2p 预审人工改价来源判定与 AI 建议采纳）：已通过当前环境手工验收；旧 `index.html` 预审“人工改价(元)”列新增“采用AI建议”，点击后采用 AI 建议单价并按工程量重算系统合计；手动改价下发后无底价 draft 来源写“人工”，采用 AI 建议或沿用 AI 价则来源写“AI 建议”，状态与流向详情展示价格动作；详见 `docs/biz-2p-preview-price-source.md`。确认推送专项 `7 passed, 1 warning`、draft 捕获专项 `7 passed, 1 warning`、状态与流向专项 `1 passed, 1 warning`，旧 `index.html` 脚本语法检查通过，`ai-web` build 通过；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2q 报价预审草稿保存与恢复 / BIZ-2q-2 我的报价历史筛选与草稿清理）：已通过当前环境手工验收；新增 `quote_preview_drafts` 和 Alembic `20260527_0024`，旧 `index.html` 预审弹窗支持自动保存和手动保存草稿；人工改价、系统合计、工程量、单位、施工项目、备注、采用 AI 建议和成本库条目切换都会保存；预审弹窗新增“关闭”按钮，关闭前保存草稿但不打回、不下发；“我的报价历史”展示 `editing` 草稿，推送列显示“草稿”，操作列显示“编辑”，并已补充时间、报价内容、项目数、总价、状态筛选以及草稿批量删除；再次打开同一报价任务自动恢复 `editing` 草稿；打回重填标记 `discarded`，确认下发成功后标记 `pushed` 并阻止继续覆盖；详见 `docs/biz-2q-preview-draft-save.md`。历史筛选与草稿批量删除补充回归 `11 passed, 1 warning`，旧 `index.html` 脚本语法检查通过，in-app browser 加载无 console error，BIZ-2q 当时数据库已升级到 `20260527_0024 (head)`；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- 最新验证（2026-05-27，BIZ-2r 成本库重复 active 防护与报价多候选提示）：新增 `app/services/cost_duplicate_guard.py`，成本库单条/批量启用会阻断相同或高风险相似 active，允许同名不同规格共存；无底价 draft 沉淀前会跳过相同或相似 draft/active；报价命中多个 active 候选时在 `cost_reference` 标记候选数量和候选列表，旧预审单要求确认当前依据或切换成本条目，未确认前前端与 `/confirm_push` 均阻断下发；详见 `docs/biz-2r-cost-duplicate-active-guard.md`。专项回归 `53 passed, 3 warnings`，当前环境手动验收已通过；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
- 最新文档（2026-05-27，BIZ-2 成本价权限清单草案）：新增 `docs/biz-2-cost-price-permissions-draft.md`，明确普通业务员、成本部业务员、管理员和老板的成本库查看、编辑、启用、导出和 RAG 同步边界；上云前建议新增成本专项角色并收紧当前 `staff` 完整成本库只读能力；当前只是权限草案和后续开发建议，不改代码、不新增 Alembic。
- 最新验收（2026-05-28，BIZ-2s 成本价权限落地首版）：新增成本专项角色 `cost_viewer` / `cost_editor` / `cost_approver` / `cost_exporter`，收紧普通 `staff` 完整成本库访问，只保留报价预审受限 active 候选查询 `GET /api/v1/cost-items/quote-candidates`；Vite 成本库入口和操作按钮按查看、编辑、审批启用/归档/同步拆分；旧 `index.html` 预审切换成本条目改走受限候选接口并隐藏普通业务员完整成本库详情入口；详见 `docs/biz-2s-cost-price-permissions-implementation.md`。聚焦回归 `30 passed, 3 warnings`，`ai-web` build 通过，旧 `index.html` 脚本语法检查通过；当前环境手动验收已通过；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- 最新验证（2026-05-28，BIZ-2t 成本库数据治理执行包）：新增只读治理服务 `app/services/cost_governance.py` 和脚本 `scripts/biz2t_cost_governance_pack.py`，复用 BIZ-2k active 体检结果并结合 `quote_cost_evidence`、`cost_rag_sync_runs` 生成 `reports/biz2t/20260528_current/` 下的治理摘要、CSV、XLSX 和 raw JSON；当前基线为总 208 条、active 195、archived 13、draft 0，被报价引用 active 42，治理动作 126 条，其中高风险 5、中风险 27、低风险 94，最近 RAG 同步 success 且 195/195；详见 `docs/biz-2t-cost-data-governance-execution-pack.md`。专项测试 `4 passed, 1 warning`；不写数据库，不自动删除/合并/改价/启用 active，不新增 Alembic，不改报价规则或价格口径。
- 最新文档（2026-05-28，BIZ-2t-1 高风险整改交接清单）：新增 `docs/biz-2t-high-risk-cost-handoff.md` 和 `reports/biz2t/20260528_current/cost_governance_high_risk_handoff.csv`，将 BIZ-2t 的 5 条 `risk_level=high`、`trial_blocker=yes` active 条目整理为成本部逐条核价交接材料；本阶段只读整理，不写数据库、不自动改价/撤回/归档/启用 active，不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- 最新复核（2026-05-28，BIZ-2t-2 高风险整改结果复核包）：新增 `AI_Middle_Office/scripts/biz2t2_high_risk_handoff_review.py`、`docs/biz-2t-2-high-risk-handoff-review.md` 和 `reports/biz2t/20260528_current/high_risk_handoff_review.*`；当前 5 条高风险交接项均已标记 `accepted_risk`，`accepted_risk=5`、`trial_blocker_count=0`、建议 `ready_with_known_risks`；后续试运行需登记为已知风险；不写数据库、不自动改价/撤回/归档/启用 active、不触发 RAG 同步、不新增 Alembic、不改报价规则或价格口径。
- 最新文档（2026-05-28，BIZ-2u 小范围内网试运行准备包）：新增 `docs/biz-2u-internal-trial-preparation.md`，明确试运行准入门槛、首批人员角色、样例清单、每日流程、问题反馈表、验收口径和暂停条件；正式试运行尚未启动，启动前仍建议成本部处理或说明 BIZ-2t 的 5 条高风险 active 来源价问题，并保持 `PUBLIC_ACCESS_ENABLED=false`。本阶段只做文档准备，不新增代码、页面、数据库结构或 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- 最新文档（2026-05-28，BIZ-2u-1 小范围内网试运行执行模板包）：新增 `docs/biz-2u-1-internal-trial-execution-templates.md` 和 `reports/biz2u/20260528_trial_templates/` 下的样例登记表、问题反馈台账、每日检查清单和验收记录模板；正式试运行仍未启动，只提供可填写执行材料；不写数据库、不启动服务、不新增 Alembic、不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- 最新文档（2026-05-28，BIZ-2u-2 小范围内网试运行启动前登记与检查包）：新增 `docs/biz-2u-2-internal-trial-readiness-check.md` 和 `reports/biz2u/20260528_trial_readiness/` 下的已知风险登记表、启动前检查清单和摘要 JSON；5 条 `accepted_risk` 高风险项已登记为试运行已知风险，当前结论为 `ready_with_known_risks_pending_start_confirmation`；正式试运行仍未启动，后续需负责人单独确认是否进入 BIZ-2u-3；不写数据库、不启动服务、不触发 RAG 同步、不新增 Alembic、不改报价规则、价格口径、N8N/Dify 或成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2v-1 报价下发与成本价权限安全加固）：已通过当前环境手工验收；新增 `/confirm_push` 的 `quote_job_id` 归属校验，普通用户不能用他人任务下发或标记他人预审草稿；报价候选接口关键词最少 2 个字符，普通 `staff` 只返回预审切换所需 active 成本字段，成本专项角色和管理员仍可查看完整候选；单条/批量启用 active 必须填写核定原因并写入状态历史；详见 `docs/biz-2v-1-quote-push-permission-hardening.md`。后端全量测试 `248 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2v-2 RAG 同步一致性与滞后提示）：已通过当前环境手工验收；修正 active 到 RAG 同步失败/超时时 `synced_count` 误显示为请求条数的问题；新增 `GET /api/v1/admin/cost-items/sync-rag/status` 只读状态摘要，区分已同步、需同步、同步失败、从未同步和无 active 条目；Vite 成本库页展示同步状态、active 数量和最近成功同步时间；详见 `docs/biz-2v-2-rag-sync-consistency.md`。后端全量测试 `253 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过；不新增 Alembic，不自动触发 RAG 同步，不改报价规则、价格口径、N8N/Dify 或成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2v-3 成本库敏感操作审计与导出控制）：已通过当前环境手工验收；新增 Alembic `20260528_0025` 和 `cost_access_audit_logs`；成本库导出仅允许 `cost_exporter` / 管理员，导出成功写入审计；新增成本库审计记录查询，管理员和 `cost_approver` 可查看；完整列表、详情、导出、状态变更、导入确认、状态与流向和 RAG 同步动作均写入审计；Vite 成本库页新增“导出”和“审计记录”入口；详见 `docs/biz-2v-3-cost-audit-export-control.md`。后端全量测试 `257 passed, 5 warnings`，`compileall app tests` 通过，`alembic heads` 为 `20260528_0025 (head)`，`ai-web` build 通过；不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2w-1 系统完善审查与账号入口安全加固）：已通过当前环境手工验收；新增 `ALLOW_SELF_REGISTRATION=false` 默认关闭自注册，`/api/v1/auth/register` 未开启时返回 `403 SELF_REGISTRATION_DISABLED`；新增 `POST /api/v1/admin/users` 仅允许 `system_admin` 创建用户、设置额度和初始角色，Vite 权限管理页新增“新建用户”，旧 `index.html` 移除“注册并领取额度”入口提示；详见 `docs/biz-2w-1-system-risk-hardening.md`。后端全量测试 `260 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
- 最新验收（2026-05-28，BIZ-2w-2 RAG 同步状态时间口径误报修复）：已通过当前环境手工验收；修复 `cost_items.updated_at` 数据库本地时间与 `cost_rag_sync_runs.finished_at` 应用 UTC 直接比较导致的“刚同步仍提示有更新未同步”；后端按数据库 `NOW()` 与 `UTC_TIMESTAMP()` 差值归一化 active 更新时间，前端最近成功同步时间按本地时间展示；当前真实环境状态已恢复为 `synced / 已同步`；用户已使用 `outputs/biz2w2/biz2w2_acceptance_requirement.xlsx` 完成上传、预审修改、确认下发和追溯验收；详见 `docs/biz-2w-2-rag-sync-status-timezone-fix.md`。专项测试 `10 passed, 1 warning`，`compileall app tests` 通过，`ai-web` build 通过；不新增 Alembic，不改报价规则、价格口径、RAG 同步动作、N8N/Dify 或成本库 active 规则。
- 最新验证（2026-05-28，BIZ-2w-3 成本参考优先与 AI 改写防护）：已完成代码层验证，待当前环境手工验收；报价前基于原始需求命中的 active 成本参考会转为后置预审锁定依据，AI 返回项目名若改写到其他成本项会标记 AI 改写风险，未人工确认前旧预审弹窗和 `/confirm_push` 均阻断下发；真实库只读模拟已验证“600*600矿棉板吊顶，10㎡”优先保留 `#39 轻钢龙骨矿棉板吊顶` 成本依据并识别 AI 返回 `#35 轻钢龙骨石膏板平面天花` 风险；详见 `docs/biz-2w-3-cost-reference-priority-ai-rewrite-guard.md`。专项测试 `3 passed, 1 warning`，相关回归 `56 passed, 1 warning`，`compileall` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
- 最新验收（2026-05-29，BIZ-2w-4 AI 备注与成本依据一致性校验）：已通过当前环境手工验收；当预审行已命中 active 成本参考但 AI 原始备注仍声称“未包含相关条目、无法提供报价、建议补充”等时，系统保留 AI 原始备注作审计、替换预审可见备注为成本依据一致的系统建议备注，并要求人工确认备注处理；未确认前旧预审弹窗和 `/confirm_push` 均阻断下发；详见 `docs/biz-2w-4-ai-note-cost-basis-consistency.md`。专项与相关回归 `34 passed, 1 warning`，报价任务回归 `26 passed, 1 warning`，`compileall app tests`、`ai-web` build 和旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
- 最新验证（2026-05-29，BIZ-2w-5 自由文本同名不同规格拆行与工程量识别修复）：已完成代码层验证，待当前环境手工验收；手输“石膏板吊顶 9.5mm，8㎡、石膏板吊顶 12mm，8㎡”时，前置成本上下文会按顿号拆成两项，保留 `9.5mm` / `12mm` 规格并读取真实 `8㎡` 工程量，避免把 `9.5mm` 误当 `9.5m` 工程量；详见 `docs/biz-2w-5-text-multi-spec-quantity-guard.md`。专项相关测试 `28 passed, 1 warning`，`compileall app tests` 通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则，不自动新增报价行或改总价。
- 最新验收（2026-05-29，BIZ-2w-6 报价来源口径与预审列名调整）：已通过当前环境手工验收；旧预审弹窗已将“AI 建议单价”调整为“预审参考单价”，并明确“成本库依据 / 人工确认价 / 人工确认合计”，成本库命中时突出成本库依据，无成本库时才显示 AI 估价；补充修复 AI 返回 `item_1` / `item_2` 等占位项目名时预审项目名丢失的问题；详见 `docs/biz-2w-6-quote-source-wording.md`、`docs/biz-2w-6-placeholder-project-name-hotfix.md`。旧 `index.html` inline script 检查通过，AI 占位项目名保护专项并入成本上下文/成本匹配回归 `30 passed, 1 warning`；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。

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
