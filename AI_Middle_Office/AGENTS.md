# AI 智能报价中台 — 后端项目上下文

本文件补齐根目录 `AGENTS.md` 的引用，记录当前后端、异步任务、RAG 与部署约定。根目录摘要用于快速进入项目，本文件用于后续开发交接。

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
- 产品边界：系统完善前不正式投入生产使用；后续阶段先按内网开发/验证推进，最后统一准备正式生产 Runbook。
- 当前未完成/暂缓项：P2 候选 prompt 自动重跑、P5 LangGraph 触发评估。
- 当前数据库迁移 head：`20260528_0025`；内网验证数据库若仍低于 head，需执行 Alembic 升级后启用完整反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 响应速度追踪、Phase 3 执行速度追踪、Phase 4a 会议纪要草稿确认、BIZ-1a 商务台账、BIZ-2a 成本数据库、BIZ-2c RAG 同步记录、BIZ-2l 确认需求行对账记录、大清单预审结果持久化能力、BIZ-2q 预审草稿保存能力和 BIZ-2v-3 成本库审计日志。
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
- `app/api/v1/execution_tasks.py`：Phase 3 执行任务 API，创建、列表、详情、进度更新和取消。
- `app/api/v1/meetings.py`：Phase 4a 会议纪要 API，创建/查询/详情/草稿阶段更正、人工补充草稿、取消、确认草稿和纪要 revision。
- `app/services/execution_tasks.py` / `app/services/execution_dashboard.py`：执行任务状态机、事件审计和执行速度看板聚合。
- `app/services/meetings.py`：会议纪要提取、任务草稿治理、确认写入 `execution_tasks` 和模型调用审计。
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

- Formal pricing/RAG source is `cost_items.active`; legacy MySQL `materials` is retired and no longer auto-imports `rag_materials.json`.
- RAG eval report output is controlled by `RAG_EVAL_REPORT_DIR` and no longer depends on the legacy material JSON file path.
