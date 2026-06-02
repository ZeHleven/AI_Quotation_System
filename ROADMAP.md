# 系统升级路线图

> 最后更新：2026-06-02

> 业务优化 P4 更新：真实用户体验优化代码层已落地，`index.html` 新增失败原因建议、预审风险提示和人工修改沉淀知识入口；本阶段不新增 Alembic revision。
> P4 运行态手工验收已完成（2026-05-08）：22/22 项全部通过，详见 `AI_Middle_Office/P4_ACCEPTANCE.md`。
> AI 平台架构升级 Phase 0 已完成开发与当前环境验证（2026-05-18）：RBAC、`role_version`、Vite 壳、`/login`、`/admin/permissions`、SPA fallback 已通过；旧页面保留。正式生产上线尚未发生，未来需单独 Runbook。总控文档见 `docs/superpowers/specs/2026-05-14-ai-platform-upgrade-design.md`。
> AI 平台架构升级 Phase 1 报价速度看板已完成当前环境运行态验收（2026-05-18）：新增报价速度聚合接口、`FEATURE_DASHBOARD_QUOTE` 和 `/admin/dashboard` 看板视图；已修复 AI 生成耗时写入与历史成功任务耗时回填；页面、看板数据和新增真实报价统计均已确认正常。正式生产启用仍需单独 Runbook。
> AI 平台架构升级 Phase 2 响应速度追踪已完成当前环境运行态验收（2026-05-18）：`FEATURE_CLIENT_INQUIRY` 与 `FEATURE_DASHBOARD_RESPONSE` 已在内网环境打开，响应速度看板展示 1 条可信样本，平均首次响应 15 分钟；已修复 Celery worker Phase 2 元数据加载问题。正式生产启用仍待系统整体完善后统一 Runbook。
> AI 平台架构升级 Phase 2.5 管理员报价运营闭环已完成当前环境验证（2026-05-18）：复用现有报价任务、咨询记录和报价历史数据，在 `/admin/dashboard` 增加“报价运营”视图；支持状态/来源/客户关键词/提交人筛选、提交人独立列、任务详情、重试、取消和超时标记。该阶段为后台体验补强，不启动 Phase 3 的执行任务模型。
> AI 平台架构升级 Phase 3 执行速度追踪已完成当前环境验证（2026-05-19）：新增 `execution_tasks` / `execution_task_events`、`FEATURE_EXECUTION` / `FEATURE_DASHBOARD_EXECUTION`、执行任务 CRUD/取消接口、执行速度聚合接口、`/admin/execution` 任务页和 `/admin/dashboard` 执行速度标签页；当前环境已打开开关并验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已显示取消数量。
> AI 平台架构升级 Phase 4a 手动会议纪要 + 草稿确认已完成当前环境运行态验收（2026-05-19）：新增 `meeting_notes`、`task_drafts`、`meeting_note_revisions`、`FEATURE_MEETING_AI`，会议纪要可提取任务草稿，人工确认后写入 `execution_tasks`；当前环境已打开 `FEATURE_MEETING_AI=true` 且 `PUBLIC_ACCESS_ENABLED=false`，内网 smoke 已验证自动提取并确认任务、人工补充后作废、revision 补充任务和 `/admin/execution` 访问。当前不启动 Phase 4b/4c/6，不迁移旧 `index.html` / `admin.html`。
> 产品边界：系统完善前不正式投入生产使用；后续各阶段先按内网开发/验证推进，等平台能力完整后再统一准备正式生产 Runbook。
> 业务提效路线（BIZ Track）已加入（2026-05-19）：基于商务部和成本报价部实地访谈，新增 BIZ-1 商务获客情报、BIZ-2 报价系统增强、BIZ-3 经营驾驶舱、BIZ-4 知识库治理四条业务线；Phase 4b/4c 同步暂缓；`client_inquiries` 已改造为双向模型以支持主动开拓。详见"业务提效路线（BIZ Track）"章节。
> AI 平台升级 BIZ Track BIZ-1a 商务台账 v1 已完成当前环境验证（2026-05-20）：新增 Alembic `20260520_0016`，`client_inquiries` 已支持 `direction='outbound'`、阶段、下次跟进、作废字段和 `client_inquiry_events` 审计；新增 `FEATURE_BUSINESS_LEDGER`、5 个 `/api/v1/business-ledger` 接口和 Vite `/admin/business-ledger` 页面；后端 `124 passed`、前端 build 通过，`scripts/biz1a_business_ledger_smoke.ps1` 已在当前 9000 环境验证创建、筛选、PATCH、作废、Phase 2 隔离和 XFF 审计。
> AI 平台升级 BIZ Track BIZ-2a 成本数据库初始化已完成当前环境验证（2026-05-20）：新增 Alembic `20260520_0017` / `20260520_0018`，创建并补全 `cost_items` / `cost_item_history`；新增 `FEATURE_COST_DB`、8 个 `/api/v1/admin/cost-items*` 接口、旧 Excel 预览/确认导入、Vite `/admin/cost-db` 页面和 `scripts/biz2a_cost_db_smoke.ps1`；保留对甲税前综合单价、劳务发包综合单价、班组标底税前价三类价格，并补充对甲人工/主材/辅材/直接费/管理费利润与劳务人工/主材/辅材拆分价。
> AI 平台升级 BIZ Track BIZ-2b 报价时材料底价查询已完成代码层验证（2026-05-21）：新增 `quote_cost_matching` 服务并接入同步 `/chat` 与异步 `quote_jobs` preview；仅匹配 `active` 成本条目，按 `item_name + spec` 精确优先、`item_name` 模糊兜底，旧 `index.html` 预审弹窗展示成本库参考价、匹配类型、AI 价差和偏离底价风险。不新增 Alembic；当前环境 `cost_items active=190`，报价匹配和 RAG 同步已进入成本库 active 数据运行态。
> BIZ-2a 成本数据库状态机补强已完成代码层验证（2026-05-21）：新增“撤回启用”和批量状态流转能力，支持单条/批量 `draft -> active`、`active -> draft` 并写入 `cost_item_history`，`draft` 恢复幂等，`archived` 仍作为作废冻结态不可撤回；Vite `/admin/cost-db` 增加“撤回启用”“批量核定 active”“批量恢复 draft”入口。
> AI 平台升级 BIZ Track BIZ-2c 成本库主库化 + active RAG 同步已完成当前环境验证（2026-05-21）：新增 `app/services/cost_rag_sync.py`、`POST /api/v1/admin/cost-items/sync-rag`、Alembic `20260520_0019` 的 `cost_rag_sync_runs`、`GET /api/v1/admin/cost-items/sync-rag/runs` 和 Vite `/admin/cost-db`“同步 active 到 RAG / 同步记录”窗口；同步源只取 `cost_items.active` 并转换为 RAG `/admin/reload` 兼容 payload。旧 `materials` 已退役并清空 70 条测试数据，旧 `/admin/sync_milvus`、旧 materials 写入/回滚和旧知识候选 approve 均返回 410。当前环境 active 190 条已同步可检索。
> AI 平台升级 BIZ Track BIZ-2d 成本库参考价命中率优化已完成代码层验证（2026-05-21）：补强 `quote_cost_matching` 的中文名称归一化、符号/连接词处理、词序无关 token 匹配、单位族兼容和动作词误命中保护；已验证“窗帘盒/灯槽拆除”与“窗帘盒灯槽拆除/延米/米”等写法能命中 active 成本库底价；补充编号换行清单输入清洗，避免 `1. / 2. / 3.` 多行需求触发 N8N 空响应。不新增 Alembic，不启动漏项检测。
> AI 平台升级 BIZ Track BIZ-2e 漏项检测已完成代码层验证（2026-05-21）：新增保守规则式漏项检测服务，复用报价预审成本库 enrichment，仅基于 `cost_items.active` 给出“疑似漏项”提示；旧 `index.html` 预审弹窗展示提示、触发行和成本库参考价，不自动新增报价行、不改变合计、不新增 Alembic。
> AI 平台升级 BIZ Track BIZ-2f 报价需求单 Excel 解析已完成代码层验证（2026-05-21）：新增 `quote_excel_parser`，`index.html` 支持上传 `.xlsx/.xlsm` 需求单；同步 `/chat` 与异步 `quote_jobs` 会直接解析施工项目、数量、单位、规格/特征、备注后进入现有报价流程，不再把 Excel 交给 GLM-4V；旧 `.xls` 明确提示另存为 `.xlsx`。不新增 Alembic，不改 N8N。
> AI 平台升级 BIZ Track BIZ-2g 成本库底价兜底填价已完成代码层验证（2026-05-21）：当 AI 预审单给出空/0 单价但已命中 `cost_items.active` 底价且可解析数量时，报价预审会使用成本库参考价回填单价和合计，并标记“已用成本库底价兜底”；AI 已给出正常正数单价时不覆盖。不新增 Alembic，不改 N8N。
> AI 平台升级 BIZ Track BIZ-2h 成本库价格前置给 AI 报价链路已完成代码层验证（2026-05-22）：新增 `quote_cost_context`，报价请求进入 N8N/Dify 前先基于 `cost_items.active` 匹配需求项，把命中的底价、单位、数量、匹配类型、`cost_item_id` 和参考合计作为 `[成本库底价强参考]` 追加给 AI；同步 `/chat` 与异步 `quote_jobs` 均已接入。不新增 Alembic，不改 N8N，BIZ-2g 后置兜底继续保留。
> AI 平台升级 BIZ Track BIZ-2i 报价可解释性与审计记录已完成代码层验证（2026-05-22）：新增报价成本证据审计记录，确认/打回时保存 AI 原始报价、最终报价、成本库命中条目、参考价、行合计来源、整单合计来源、价差和证据链接；后台报价反馈/报价运营可追溯成本证据。不新增 Alembic。
> AI 平台升级 BIZ Track BIZ-2j 报价依据与成本库证据链展示优化已完成代码层验证（2026-05-22）：旧 `index.html` 预审“查看依据”弹窗已重排为业务员可读的 AI 报价来源、AI 报价依据、成本库参考、合计对照等分区，并将成本库详情 URL 改为按钮跳转；后台报价任务详情同步展示 AI 来源和成本证据。
> BIZ-2 后续体验补强已完成（2026-05-22 至 2026-05-23）：所有“闪筑智价”文案已改为“旗胜智价”；预审阶段支持同名不同规格成本条目切换，切换后同步采用新成本条目参考价重算本行单价/合计；AI 报价来源显式化，支持“采纳前置成本库 / 偏离前置成本库 / 无成本库参考 AI 估算 / 成本库兜底 / 人工切换成本条目”；报价运营详情新增预审打回原因展示。
> AI 平台升级 BIZ Track BIZ-2k 成本库数据质量体检 + 演示回归包已通过当前环境手工验收（2026-05-28，BIZ-2k-1 报告可读性补强后复验）：新增只读 `cost_items.active` 体检服务和报告脚本，可生成 Markdown/CSV/XLSX 与演示回归包，覆盖同名不同规格、价格为空/0、单位异常、规格备注缺失、相似条目和 RAG 同步数量提示；不新增 Alembic，不新增页面/API，不改报价逻辑，不写数据库。
> AI 平台升级 BIZ Track BIZ-2l-0 甲方需求单标准字段与典型场景确认已完成文档层确认（2026-05-25）：已形成 `docs/biz-2l-requirement-standardization-biz2l0.md`，明确标准报价行字段、字段别名、单位数量规则、行类型、典型场景、置信度、警告码、人工确认口径和 BIZ-2l-1 输出合同；不编码、不改数据库、不改报价逻辑、不改价格口径、不自动沉淀成本库。
> AI 平台升级 BIZ Track BIZ-2l-1 只读通用清洗解析器已完成代码层验证（2026-05-25）：新增 `app/services/requirement_standardizer.py` 和 `scripts/biz2l_requirement_standardization_preview.py`，可将 `.xlsx/.xlsm` 需求单输出为标准化 JSON/CSV/Markdown 预览，覆盖字段别名、无表头混写、数量单位粘连、说明/合计/区域行、价格列只读提示、多 Sheet 和合并单元格；真实甲方清单“联昇集团办公楼装饰工程清单.xlsx”可解析 8 个 Sheet、299 个标准行，并修复“项目特征误判为项目名称”的表头优先级问题；不接报价、不写数据库、不改报价逻辑/价格口径。
> AI 平台升级 BIZ Track BIZ-2l-2 人工列映射与行确认已完成当前环境验收（2026-05-26）：新增 `/admin/requirement-standardization` Vite 页面和需求单标准化 API，支持上传 `.xlsx/.xlsm` 解析预览、按 Sheet 人工列映射、按 Sheet 行确认、原始行追溯、标准数量来源与多工程量候选、搜索/筛选、确认清单生成、本地历史解析记录和版本回滚；历史进度使用浏览器 IndexedDB 保存，不写数据库、不新增 Alembic；仍不接报价、不改报价逻辑/价格口径、不自动沉淀成本库。后端全量测试 `202 passed, 3 warnings`，`ai-web` build 通过。
> AI 平台升级 BIZ Track BIZ-2l-3 标准清单接入报价链路已完成代码层验证并通过业务验收（2026-05-26）：需求单标准化确认页新增“发起报价”，发起前会重新调用确认接口校验当前行，只将已确认且通过校验的标准行组装为 `quote_text` 并调用现有 `/api/v1/quote/jobs` 异步报价任务；阻断行、剔除行、说明/汇总/空白行不进入报价；行确认支持按当前筛选结果全选、取消选择、批量确认和批量撤回确认；若生成确认清单或发起报价前存在阻断行，界面展示校验问题面板、中文错误原因和原始行内容，并自动切换到“未通过校验”定位首条问题行；创建成功后自动跳转到旧报价工作台 `index.html`，由旧工作台接管任务进度并复用原有 AI 预审弹窗进行人工验收。复用现有报价、成本库前置参考、成本库匹配、漏项检测、底价兜底、证据链、预审确认和打回流程；不新增数据库结构、不新增 Alembic、不改报价逻辑/价格口径、不自动沉淀成本库。后端全量测试 `203 passed, 3 warnings`，`ai-web` build 通过。
> AI 平台升级 BIZ Track BIZ-2l-4 预审对账与运营复核详情已完成当前环境业务验收（2026-05-27）：新增 Alembic `20260526_0022` 和 `quote_job_requirement_rows`，报价任务创建时持久化人工确认的标准需求行；新增 `/api/v1/quote/jobs/{job_id}/review-detail`，对账确认清单与 AI 预审条目，标出疑似未报价、额外预审行、无底价参考、成本库兜底、人工改动过大、偏离底价过大等复核项；运行中后端已验证 169 行任务 `2557d7ba-f0ca-48ba-91c3-5c9c8f993a4c` 的 `review-detail` 返回 200，确认行 169、预审行 169、需复核 169、高风险 169、无底价参考 153，可继续支撑旧预审弹窗和 Vite 报价运营详情复核。不改报价规则、价格口径、无底价自动处理和成本库沉淀逻辑。
> AI 平台升级 BIZ Track BIZ-2l-5 确认清单逐行报价完整性保障已完成当前环境业务验收（2026-05-27）：异步报价会把已确认需求行以 `requirement_row_key` 逐行传给 AI，并要求 `project_details` 与确认清单逐条对应，禁止合并、抽样或省略；后端会在预审结果写入 `requirement_integrity` 完整性摘要，报价运营详情继续可追溯确认行/预审行对账；专项测试已覆盖确认需求行逐行传给 AI、不完整预审 `/confirm_push` 409 阻断、未补价占位行 `/confirm_push` 409 阻断，以及占位行不触发成本库底价兜底。不新增数据库结构、不新增 Alembic、不改报价规则、价格口径、无底价自动处理和成本库沉淀逻辑。
> AI 平台升级 BIZ Track BIZ-2l-6 确认清单分批报价与缺失占位已完成当前环境业务验收（2026-05-27）：确认清单超过阈值时，异步报价按批进入现有 N8N/Dify 链路，默认每批 20 行，缺失行自动补报 1 次；仍未返回的确认行会按原确认清单顺序生成“AI 未返回，需人工补价”的占位预审行，保留数量、单位、Sheet、原始行号和原始备注；占位行不触发成本库底价自动兜底，未人工填写单价和系统合计前，旧 `index.html` 与 `/confirm_push` 都会阻断下发；重启后 169 行任务 `2557d7ba-f0ca-48ba-91c3-5c9c8f993a4c` 已验证预审行数 169、占位行 169、`requirement_integrity.status=complete_with_placeholders`、`missing_count=0`、未补价推送 409 阻断，前端人工验收通过；补充 Alembic `20260526_0023` 将异步报价大 JSON 字段扩为 `LONGTEXT`，避免 169 行预审结果写库超长。不改报价规则、价格口径、无底价自动处理和成本库沉淀逻辑。后端报价任务测试 `26 passed`，`ai-web` build 通过，旧 `index.html` 脚本语法检查通过。
> BIZ-2 无底价项目处理规则草案已完成（2026-05-27）：新增 `docs/biz-2-no-cost-reference-rule-draft.md`，明确无 `cost_items.active` 参考价时允许 AI 估价，但必须提示“无成本库参考价，AI 估价，仅供参考，请人工确认价格依据”；人工确认单价/合计后可下发；下发成功后无底价条目进入成本库 `draft` 待审核，不能自动 `active`，只有成本部业务员、管理员或老板人工启用为 `active` 后，后续同类需求才允许展示成本库参考价。本文档为规则草案和开发建议，不改代码、不新增 Alembic。
> BIZ-2m 无底价项目规则开发落地已通过当前环境手工验收（2026-05-28）：新增 `app/services/no_cost_draft_capture.py`、`FEATURE_NO_COST_DRAFT_CAPTURE`、`/confirm_push` 成功后无底价 draft 捕获、成本库来源筛选、旧预审弹窗无底价固定提示和下发成功 draft 摘要；自动生成项只写入 `cost_items.draft`，不自动 active，draft 不参与后续报价/RAG/兜底，未补价占位继续阻断，已补价占位可沉淀；BIZ-2p 后来源按手动改价/采用 AI 建议细分。演示与验收记录见 `docs/biz-2m-demo-and-acceptance.md`。不新增 Alembic，不改报价价格口径。
> BIZ-2n 预审人工改价字段与合计联动已通过当前环境手工验收（2026-05-28）：旧 `index.html` 预审弹窗新增可编辑“工程量/单位”和“人工改价(元)”列，人工改价默认取成本库参考价，无成本库参考则默认 0；修改工程量或人工改价后联动系统合计；若 AI 返回工程量 0 但源 Excel 有有效工程量，后端会回填源工程量；下发前归一写回 `unit_price/total_price`，未补有效人工改价或系统合计前阻断推送；详见 `docs/biz-2n-manual-price-preview.md`。不新增 Alembic，不改 N8N/Dify，不改成本库 active 规则。

> BIZ-2o 成本库状态与流向台账已通过当前环境手工验收（2026-05-28）：Vite `/admin/cost-db` 新增“状态与流向”入口，可按总览、新增 draft、active 记录、归档记录查看成本条目来源、当前去向、生命周期和报价引用；后端新增只读 lineage 汇总/列表/详情接口，复用 `cost_items`、`cost_item_history`、`quote_cost_evidence` 和 `cost_rag_sync_runs`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则；详见 `docs/biz-2o-cost-lineage.md`。
> BIZ-2p 预审人工改价来源判定与 AI 建议采纳已通过当前环境手工验收（2026-05-28）：旧 `index.html` 预审“人工改价(元)”列新增“采用AI建议”，点击后采纳 AI 建议单价并按工程量重算系统合计；手动改价下发后无底价 draft 来源写“人工”，采纳 AI 建议或沿用 AI 价则来源写“AI 建议”，状态与流向详情展示价格动作。详见 `docs/biz-2p-preview-price-source.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。

> BIZ-2q 报价预审草稿保存与恢复已通过当前环境手工验收（2026-05-28）：新增 `quote_preview_drafts` 和 Alembic `20260527_0024`，旧 `index.html` 预审弹窗会在人工改价、系统合计、工程量、单位、施工项目、备注、采用 AI 建议和成本库条目切换后保存草稿；预审弹窗新增“关闭”按钮，关闭前保存草稿但不打回、不下发；“我的报价历史”会显示“草稿”状态并在操作列提供“编辑”入口，再次打开同一报价任务会恢复 `editing` 草稿；打回重填标记为 `discarded`，确认下发成功后标记为 `pushed` 并阻止继续覆盖。详见 `docs/biz-2q-preview-draft-save.md`。不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
> BIZ-2q-2 我的报价历史筛选与草稿清理已通过当前环境手工验收（2026-05-28）：历史列表不再将草稿固定置顶，统一按时间倒序排列；`/api/v1/history` 支持时间、报价内容、项目数、总价和推送状态筛选；旧 `index.html` 的“我的报价历史”新增筛选栏、草稿多选和“删除草稿”批量操作；新增 `POST /api/v1/quote/preview-drafts/batch-delete`，仅删除 `editing` 草稿快照，不删除报价任务或已推送历史。详见 `docs/biz-2q-preview-draft-save.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
> BIZ-2r 成本库重复 active 防护与报价多候选提示已通过当前环境手动验收（2026-05-27）：新增统一重复判断服务，成本库单条/批量启用会阻断相同或高风险相似 active，允许同名不同规格共存；无底价 draft 沉淀前会跳过相同或相似 draft/active；报价命中多个 active 候选时在 `cost_reference` 标记多候选，旧预审单要求“确认当前依据”或切换成本条目，未确认前前端与 `/confirm_push` 均阻断下发。详见 `docs/biz-2r-cost-duplicate-active-guard.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步或成本库 active 规则。
> BIZ-2 成本价权限清单草案已完成（2026-05-27）：新增 `docs/biz-2-cost-price-permissions-draft.md`，明确普通业务员只在自己报价预审中查看必要成本参考，成本部业务员、管理员和老板可查看完整成本库；`draft -> active`、价格修改、导出、RAG 同步等属于高风险操作，需审计；上云前建议新增成本专项角色并收紧当前 `staff` 完整成本库只读能力。本文档为权限草案和开发建议，不改代码、不新增 Alembic。
> BIZ-2s 成本价权限落地首版已通过当前环境手动验收（2026-05-28）：新增 `cost_viewer` / `cost_editor` / `cost_approver` / `cost_exporter`，普通 `staff` 不再浏览完整成本库，只保留报价预审受限 active 成本候选接口 `GET /api/v1/cost-items/quote-candidates`；Vite `/admin/cost-db` 按查看、编辑、审批启用/归档/同步拆分按钮权限；旧 `index.html` 预审切换成本条目改走受限候选接口。详见 `docs/biz-2s-cost-price-permissions-implementation.md`。不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
> BIZ-2t 成本库数据治理执行包已生成当前环境只读治理基线（2026-05-28）：新增 `app/services/cost_governance.py` 和 `scripts/biz2t_cost_governance_pack.py`，复用 BIZ-2k active 体检结果并结合报价引用证据和 RAG 同步记录，生成 `reports/biz2t/20260528_current/` 下的治理摘要、人工整改 CSV/XLSX 和 raw JSON。当前基线：总 208 条、active 195、archived 13、draft 0，被报价引用 active 42，治理动作 126 条，高风险 5、中风险 27、低风险 94，最近 RAG 同步 success 且 195/195；试运行建议为 `cleanup_before_trial`。详见 `docs/biz-2t-cost-data-governance-execution-pack.md`。只读分析，不写库、不自动删除/合并/改价/启用 active，不新增 Alembic，不改报价规则或价格口径。
> BIZ-2t-1 高风险整改交接清单已完成文档层准备（2026-05-28）：新增 `docs/biz-2t-high-risk-cost-handoff.md` 和 `reports/biz2t/20260528_current/cost_governance_high_risk_handoff.csv`，将 BIZ-2t 的 5 条高风险试运行阻断项整理为成本部逐条核价表，明确允许处理方式、禁止自动动作和重新生成治理报告的复核标准。本阶段只读整理，不写数据库、不自动改价/撤回/归档/启用 active，不新增 Alembic，不改报价规则或价格口径。
> BIZ-2t-2 高风险整改结果复核包已完成只读复核（2026-05-28）：新增 `scripts/biz2t2_high_risk_handoff_review.py`、`docs/biz-2t-2-high-risk-handoff-review.md` 和 `reports/biz2t/20260528_current/high_risk_handoff_review.*`；当前 5 条高风险交接项均由管理员标记为 `accepted_risk`，`accepted_risk=5`、`trial_blocker_count=0`、建议 `ready_with_known_risks`；正式试运行仍未启动，下一步可登记试运行样例并将 5 条记录为已知风险。本阶段不写数据库、不自动整改、不触发 RAG 同步、不新增 Alembic，不改报价规则或价格口径。
> BIZ-2u 小范围内网试运行准备包已完成文档层准备（2026-05-28）：新增 `docs/biz-2u-internal-trial-preparation.md`，明确正式试运行启动前的准入门槛、首批人员角色、样例清单、每日流程、问题反馈表、验收口径和暂停条件；正式试运行尚未启动，启动前仍建议先处理或说明 BIZ-2t 的 5 条高风险 active 来源价问题，并继续保持 `PUBLIC_ACCESS_ENABLED=false`。本阶段不新增代码、页面、数据库结构或 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
> BIZ-2u-1 小范围内网试运行执行模板包已完成文档层准备（2026-05-28）：新增 `docs/biz-2u-1-internal-trial-execution-templates.md` 和 `reports/biz2u/20260528_trial_templates/` 下的样例登记表、问题反馈台账、每日检查清单和验收记录模板；正式试运行仍未启动。本阶段只提供可填写执行材料，不写数据库、不启动服务、不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
> BIZ-2u-2 小范围内网试运行启动前登记与检查包已完成文档层准备（2026-05-28）：新增 `docs/biz-2u-2-internal-trial-readiness-check.md` 和 `reports/biz2u/20260528_trial_readiness/` 下的已知风险登记表、启动前检查清单和摘要 JSON；5 条 `accepted_risk` 高风险项已登记为试运行已知风险，当前结论为 `ready_with_known_risks_pending_start_confirmation`；正式试运行仍未启动，后续需负责人单独确认是否进入 BIZ-2u-3。本阶段不写数据库、不启动服务、不触发 RAG 同步、不新增 Alembic，不改报价规则、价格口径、N8N/Dify 或成本库 active 规则。
> BIZ-2v-1 报价下发与成本价权限安全加固已通过当前环境手工验收（2026-05-28）：新增 `/confirm_push` 的 `quote_job_id` 归属校验，普通用户不能用他人任务下发或标记他人预审草稿；报价候选接口关键词最少 2 个字符，普通 `staff` 只返回预审切换所需 active 成本字段，成本专项角色和管理员仍可查看完整候选；单条/批量启用 active 必须填写核定原因并写入状态历史；详见 `docs/biz-2v-1-quote-push-permission-hardening.md`。后端全量测试 `248 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
> BIZ-2v-2 RAG 同步一致性与滞后提示已通过当前环境手工验收（2026-05-28）：修正 active 到 RAG 同步失败/超时时 `synced_count` 误显示为请求条数的问题；新增 `GET /api/v1/admin/cost-items/sync-rag/status`，按当前 active 数量、最近 active 更新时间、最近成功同步记录和最近同步记录判断已同步、需同步、失败、从未同步或无 active 条目；Vite `/admin/cost-db` 展示同步状态、active 数量和最近成功同步时间；详见 `docs/biz-2v-2-rag-sync-consistency.md`。后端全量测试 `253 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过；不新增 Alembic，不自动触发 RAG 同步，不改报价规则、价格口径、N8N/Dify 或成本库 active 规则。
> BIZ-2v-3 成本库敏感操作审计与导出控制已通过当前环境手工验收（2026-05-28）：新增 Alembic `20260528_0025` 和 `cost_access_audit_logs`；成本库导出仅允许 `cost_exporter` / 管理员，完整成本库查看、详情、导出、状态变更、导入确认、状态与流向和 RAG 同步动作均写入审计；新增审计查询接口，管理员和 `cost_approver` 可查；Vite `/admin/cost-db` 新增“导出”和“审计记录”入口；详见 `docs/biz-2v-3-cost-audit-export-control.md`。后端全量测试 `257 passed, 5 warnings`，`compileall app tests` 通过，`alembic heads` 为 `20260528_0025 (head)`，`ai-web` build 通过；不改报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。
> BIZ-2w-1 系统完善审查与账号入口安全加固已通过当前环境手工验收（2026-05-28）：新增 `ALLOW_SELF_REGISTRATION=false` 默认关闭自注册，`/api/v1/auth/register` 未开启时返回 `403 SELF_REGISTRATION_DISABLED`；新增 `POST /api/v1/admin/users` 仅允许 `system_admin` 创建用户、设置额度和初始角色，Vite 权限管理页新增“新建用户”，旧 `index.html` 移除“注册并领取额度”入口提示；详见 `docs/biz-2w-1-system-risk-hardening.md`。后端全量测试 `260 passed, 5 warnings`，`compileall app tests` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。

> BIZ-2w-2 RAG 同步状态时间口径误报修复与验收需求单闭环已通过当前环境手工验收（2026-05-28）：修复 `cost_items.updated_at` 数据库本地时间与 `cost_rag_sync_runs.finished_at` 应用 UTC 直接比较导致的“刚同步仍提示有更新未同步”；后端按数据库 `NOW()` 与 `UTC_TIMESTAMP()` 差值归一化 active 更新时间，前端最近成功同步时间按本地时间展示；当前真实环境状态已恢复为 `synced / 已同步`；用户已使用 `outputs/biz2w2/biz2w2_acceptance_requirement.xlsx` 完成上传、预审修改、确认下发和追溯验收；详见 `docs/biz-2w-2-rag-sync-status-timezone-fix.md`。专项测试 `10 passed, 1 warning`，`compileall app tests` 通过，`ai-web` build 通过；不新增 Alembic，不改报价规则、价格口径、RAG 同步动作、N8N/Dify 或成本库 active 规则。
> BIZ-2w-3 成本参考优先与 AI 改写防护已完成代码层验证，待当前环境手工验收（2026-05-28）：报价前基于原始需求命中的 active 成本参考会转为后置预审锁定依据，AI 返回项目名若改写到其他成本项会标记 AI 改写风险，未人工确认前旧预审弹窗和 `/confirm_push` 均阻断下发；真实库只读模拟已验证“600*600矿棉板吊顶，10㎡”优先保留 `#39 轻钢龙骨矿棉板吊顶` 成本依据并识别 AI 返回 `#35 轻钢龙骨石膏板平面天花` 风险；详见 `docs/biz-2w-3-cost-reference-priority-ai-rewrite-guard.md`。专项测试 `3 passed, 1 warning`，相关回归 `56 passed, 1 warning`，`compileall` 通过，`ai-web` build 通过，旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
> BIZ-2w-4 AI 备注与成本依据一致性校验已通过当前环境手工验收（2026-05-29）：当预审行已命中 active 成本参考但 AI 原始备注仍声称“未包含相关条目、无法提供报价、建议补充”等时，系统保留 AI 原始备注作审计、替换预审可见备注为成本依据一致的系统建议备注，并要求人工确认备注处理；未确认前旧预审弹窗和 `/confirm_push` 均阻断下发；详见 `docs/biz-2w-4-ai-note-cost-basis-consistency.md`。专项与相关回归 `34 passed, 1 warning`，报价任务回归 `26 passed, 1 warning`，`compileall app tests`、`ai-web` build 和旧 `index.html` inline script 检查通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。
> BIZ-2w-5 自由文本同名不同规格拆行与工程量识别修复已完成代码层验证，待当前环境手工验收（2026-05-29）：修复“石膏板吊顶 9.5mm，8㎡、石膏板吊顶 12mm，8㎡”这类手输需求没有按顿号拆成两条前置成本参考、且 `9.5mm` 被误识别为 `9.5m` 工程量的问题；前置成本上下文现在会分别保留 `9.5mm` / `12mm` 规格，并取末尾真实 `8㎡` 作为工程量；详见 `docs/biz-2w-5-text-multi-spec-quantity-guard.md`。专项相关测试 `28 passed, 1 warning`，`compileall app tests` 通过；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则，不自动新增报价行或改总价。
> BIZ-2w-6 报价来源口径与预审列名调整已通过当前环境手工验收（2026-05-29）：旧预审弹窗将“AI 建议单价”调整为“预审参考单价”，将“成本库参考 / 人工改价 / 系统合计”调整为“成本库依据 / 人工确认价 / 人工确认合计”，并按行显示“成本库依据价 / 无成本库参考，AI估价 / 偏离成本库依据”等来源标签；补充修复 AI 返回 `item_1` / `item_2` 等占位项目名时预审项目名丢失的问题；详见 `docs/biz-2w-6-quote-source-wording.md`、`docs/biz-2w-6-placeholder-project-name-hotfix.md`。旧 `index.html` inline script 检查通过，AI 占位项目名保护专项并入成本上下文/成本匹配回归 `30 passed, 1 warning`；不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则，不自动新增报价行或改总价。
> BIZ-3b-3-0 关键节点证据硬门禁实现规划已评审通过（2026-06-01）：新增 `docs/biz-3b-3-0-key-node-hard-gate-planning.md`，硬门禁开发前先定三件事——A 级节点（竣工精装验收、隐蔽工程验收、结算确认、设计成果交付）首批升级为 `complete_required`，B 级（含 `compact=1` 收款节点）待确认，C 级保持 `soft_reminder`；缺证据为有条件硬门禁，普通成员阻断返回 `409 EVIDENCE_HARD_GATE_BLOCKED`、真正无操作权限返回 `403 PERMISSION_DENIED`、项目经理/管理员可填 `bypass_reason`（缺则 `422 EVIDENCE_BYPASS_REASON_REQUIRED`）放行，门禁只卡完成（100%）不卡提交；放行事件 `task_completed_bypass_gate` 复用 `project_task_events.payload_json` 记录决策快照，不新增列。实现拆两步：BIZ-3b-3-1 显式字段（`evidence_requirement`/`evidence_policy`/`is_key_node`）落库与存量回填，不引入阻断、完成仍走 3b-2a 软提醒；BIZ-3b-3-2 再实现 `complete_required` 硬门禁与放行事件，两步均完成后硬门禁才正式生效。本阶段只规划、不改库、不改代码、不新增 Alembic。BIZ-3 工程项目进度文档总目录见 `AI_Middle_Office/docs/biz-3-project-progress-index.md`。
> BIZ-3b-3-1 显式字段落库与存量回填已完成当前环境验证（2026-06-01）：新增 Alembic `20260601_0028`，`project_tasks` 新增 `evidence_requirement` / `evidence_policy` / `is_key_node`，模板实例化与手工任务创建已直接写入显式字段，运行时优先读取显式列并保留 EPC `description` 解析兜底；存量回填真实环境任务 `146` 个，其中 EPC compact `41`、EPC full `82`、单人模板 `18`、手工/其他 `5`，回填更新 `123` 个，`soft_reminder=123`、`none=23`、`complete_required=0`，回填后 dry-run `updated_task_count=0`，无模板匹配失败/成果解析失败。本阶段不启用硬门禁，不新增 `bypass_reason` / `task_completed_bypass_gate`，完成动作仍沿用 BIZ-3b-2a 软提醒。后端全量 `290 passed`，`ai-web npm run build` 通过，当前数据库 head `20260601_0028`。
> BIZ-3b-3-2 complete_required 硬门禁与放行事件已通过当前环境业务验收（2026-06-02）：新建 EPC 模板任务会将 A 级 4 个节点（设计成果交付、隐蔽工程验收、竣工精装验收、结算确认）写为 `complete_required`；存量任务通过 `scripts/biz3b32_activate_project_task_hard_gates.py --apply` 激活，当前库 `total=146`、`a_level_candidate=8`、`complete_required=8`、`soft_reminder=115`、`none=23`，再次 dry-run `updated_task_count=0`；完成接口对缺证据硬门禁节点返回 `409 EVIDENCE_HARD_GATE_BLOCKED`，项目经理/管理员必须填 `bypass_reason`（少于 6 字返回 `422 EVIDENCE_BYPASS_REASON_REQUIRED`）才可放行，放行写 `task_completed_bypass_gate` 决策快照；Vite 项目任务表已展示关键节点/需证据标记和三态完成交互；重启后业务走读已确认普通成员阻断、项目经理/管理员放行、项目动态事件、补证据后正常完成、提交到 80% 不阻断和 B 级节点保持软提醒均无问题。后端全量 `292 passed, 11 warnings`，`ai-web npm run build` 通过；不新增 Alembic，数据库 head 仍为 `20260601_0028`，不接钉钉/企微自动同步。
> BIZ-3c 经营驾驶舱轻量 MVP 已推进至 BIZ-3c-3 趋势图 + 风险规则精化代码层验证（2026-06-02）：BIZ-3c-1 新增 `AI_Middle_Office/app/services/business_lite_dashboard.py`、`FEATURE_DASHBOARD_BUSINESS_LITE` 和 `GET /api/v1/admin/dashboard/business-lite`，复用 dashboard viewer 权限，默认 `range=last_30_days`，沿用 `today/week/month/last_30_days` 范围；接口只读聚合报价、成本库、项目进度和系统健康摘要，返回风险列表、跳转入口和 `section_errors` 局部降级信息。BIZ-3c-2 已在 Vite `/admin/dashboard` 新增“经营总览”标签页，展示 8 个经营指标卡、风险与待处理列表、运行摘要、跳转入口和局部降级告警。BIZ-3c-3 已补 `quote.daily_trend`、`cost.status_distribution`、`cost.source_distribution`、`project_progress.daily_trend` 和 `project_progress.hard_gate_bypassed_missing_evidence_count`，前端展示报价趋势、项目证据趋势和分布概览，并将硬门禁风险精化为“放行后当前仍缺 active 证据”；不新增经营数据表、不新增 Alembic、不计算合同额/回款率/毛利、不展示成本敏感明细，不引入重型图表库。BIZ-3c-3 后端聚焦测试 `5 passed, 1 warning`，经营总览 / 成本 RAG 同步 / 成本审计 / 项目进度交叉依赖测试 `31 passed, 6 warnings`，前端 `npm.cmd run build` 通过。下一步建议 BIZ-3c 轻量 MVP 进入小范围试运行观察，完整合同/回款/成本/毛利驾驶舱留到 BIZ-3d。
> FE-UX-1 前端保守型体验重构一期已完成并通过人工验收（2026-06-02）：新增 `AI_Middle_Office/docs/fe-ux-1-admin-experience-refactor-planning.md` 和 `AI_Middle_Office/docs/fe-ux-1-trial-experience-acceptance.md`；不更换 Vue3 + Vite + Element Plus 技术栈，不整站套模板，不一次性迁移旧 `index.html`。FE-UX-1-1 应用外壳与视觉基础、FE-UX-1-2 报价工作台流程优化、FE-UX-1-3 AI 预审弹窗重构、FE-UX-1-4 成本库高级表格优化、FE-UX-1-5 经营总览与项目进度体验补强均已通过人工验收；FE-UX-1-6 已形成试运行体验验收包。不改报价接口、价格口径、成本库 active 规则、项目硬门禁、草稿保存、推送或审计规则。`index.html` 内联脚本解析通过，9000 的 `/admin/cost-db`、`/admin/dashboard`、`/admin/projects` 返回 200，`npm.cmd run build` 通过。
> FE-UX-2 Apple-like 正式视觉增强第一版已完成并通过人工视觉验收（2026-06-02）：新增 `AI_Middle_Office/docs/fe-ux-2-apple-like-visual-upgrade.md`，在不套 Apple 官网模板、不引入新 UI 库、不改业务逻辑的前提下，把 Vite 管理台和旧 `index.html` 报价工作台的视觉底座调整为浅色、克制、正式风格；Vite 管理台新增品牌 lockup、登录页品牌介绍区、浅色磨砂 topbar、Element Plus 通用控件增强和指标/工作台卡片统一；旧报价页同步浅色导航、流程条、消息区、输入区和 AI 预审弹窗卡片质感。`npm.cmd run build` 通过，旧 `index.html` 内联脚本解析通过，9000 的 `/login`、`/admin/dashboard`、`/admin/cost-db`、`/admin/projects`、`/index.html` 返回 200；本轮 Browser 截图验收未完成，但用户已确认人工视觉验收通过。
> 商务/市场部提效路线阶段性收束（2026-05-20）：BIZ-1a 台账已完成，但后续提醒、流水等低收益延展暂不继续推进；真正高价值方向调整为"外部项目源自动搜索/收集 + 公司标准筛选 + 联系方式输出"，因外部项目搜索引擎调用成功率未知、外部项目收集平台合作和 API 权限尚未确定，标记为待定。

---

## 当前冻结状态（2026-06-01）

- **后端基础设施优化已收束**：后端重构 P0-P3、补充一致性优化、运维告警收敛、Alembic 迁移纪律均已落地；后续不再做无触发的基础设施打磨。
- **数据库规范**：当前代码迁移 head 为 `20260601_0028`；内网验证数据库若低于 head，需执行 Alembic 升级后启用完整报价反馈、Prompt 回归、知识候选记录、Phase 0 RBAC、Phase 2 响应速度追踪、Phase 3 执行速度追踪、Phase 4a 会议纪要草稿确认、BIZ-1a 商务台账、BIZ-2a 成本数据库、BIZ-2c RAG 同步记录、BIZ-2l 确认需求行对账记录、大清单预审结果持久化能力、BIZ-2q 预审草稿保存能力、BIZ-2v-3 成本库审计日志、BIZ-3a 项目进度底座、BIZ-3b-1 任务成果证据表和 BIZ-3b-3-1 显式证据策略字段。后续新增字段或表必须新增 Alembic revision，不能退回依赖 `AUTO_CREATE_TABLES` / 启动兼容迁移。
- **业务优化 P0-P4 已完成到代码层**：P0 报价反馈闭环、P1 Admin 反馈分析、P2 Prompt 回归评测、P3 知识库候选治理、P4 真实用户体验优化均已落地。
- **前端优化 P0-P3 已完成**：已完成 `FRONTEND_ACCEPTANCE.md`、`static/js/shared.js`、`admin.html` 模块拆分，以及报价进度、失败恢复、上传/推送状态优化。
- **AI 平台升级 Phase 0 已完成开发与当前环境验证**：Vite 壳、登录鉴权统一、RBAC 与 SPA fallback 已通过；旧 `index.html` / `admin.html` / `app.html` 继续保留，后续按设计文档逐步收拢入口。正式生产上线待单独 Runbook。
- **AI 平台升级 Phase 1 报价速度看板已完成当前环境运行态验收**：已新增报价速度聚合接口和 Vite 看板入口，功能开关已在当前环境打开；已修复新报价任务 `duration_ms` 实测写入，并在备份后回填历史 121 条成功任务的 0 耗时记录；页面、看板数据和新增真实报价统计均已确认正常；未迁移旧业务页面，正式生产启用待单独 Runbook。
- **AI 平台升级 Phase 2 响应速度追踪已完成当前环境运行态验收**：已新增 `client_inquiries`、`quote_jobs.client_inquiry_id`、咨询查询/修正接口、响应速度聚合接口和 Vite 响应速度视图；当前环境 Alembic 已升级到 `20260514_0012 (head)`，`FEATURE_CLIENT_INQUIRY=true`、`FEATURE_DASHBOARD_RESPONSE=true`、`PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已展示 1 条可信样本，平均首次响应 15 分钟，SLA 达标率 100%，并修复 Celery worker Phase 2 元数据加载问题。
- **AI 平台升级 Phase 3 执行速度追踪已完成当前环境验证**：已新增执行任务 schema、任务接口、事件记录、执行速度聚合和 Vite 执行任务页面；当前环境已打开 `FEATURE_EXECUTION=true`、`FEATURE_DASHBOARD_EXECUTION=true` 且 `PUBLIC_ACCESS_ENABLED=false`，已验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已补充取消数量。
- **AI 平台升级 Phase 4a 手动会议纪要 + 草稿确认已完成当前环境运行态验收**：已新增会议纪要、纪要更正、任务草稿 schema、会议接口、固定 JSON Schema 提取器和 Vite 会议纪要标签页；当前环境已打开 `FEATURE_MEETING_AI=true` 且 `PUBLIC_ACCESS_ENABLED=false`，确认草稿可写入既有 `execution_tasks`，不依赖 `FEATURE_EXECUTION=true`；内网 smoke 已验证自动提取并确认任务、人工补充后作废、revision 补充任务和 `/admin/execution` 访问。当前不启动语音转写、钉钉会议导入、经营驾驶舱或旧 HTML 迁移。
- **AI 平台升级 BIZ Track BIZ-3 已推进至 BIZ-3c-3 经营驾驶舱轻量 MVP 趋势图 + 风险规则精化**：BIZ-3a 项目/阶段/任务/2580 进度、EPC 流程模板、BIZ-3b 成果证据 MVP、无证据软提醒、缺证据汇总、证据策略显式字段落库与存量回填、A 级 `complete_required` 硬门禁与放行事件均已完成并通过当前环境业务验收；当前 head `20260601_0028`，当前库 `complete_required=8`。BIZ-3c-1 已新增 `FEATURE_DASHBOARD_BUSINESS_LITE` 和 `/api/v1/admin/dashboard/business-lite` 只读聚合接口；BIZ-3c-2 已在 `/admin/dashboard` 增加“经营总览”指标卡 + 风险列表；BIZ-3c-3 已补趋势/分布和硬门禁放行后缺证据精确风险，后端聚焦测试 `5 passed, 1 warning`，交叉依赖测试 `31 passed, 6 warnings`，`npm.cmd run build` 通过。下一步建议轻量 MVP 进入试运行观察，完整合同/回款/成本/毛利驾驶舱留到 BIZ-3d。
- **FE-UX-1 前端保守型体验重构一期已完成并通过人工验收**：已新增 `AI_Middle_Office/docs/fe-ux-1-admin-experience-refactor-planning.md` 和 `AI_Middle_Office/docs/fe-ux-1-trial-experience-acceptance.md`，定位为小范围试运行前的中后台体验补强；主参考 `vue-pure-admin` / `Vue3 Element Admin`，但不搬模板、不换 UI 库、不整站重写。FE-UX-1-1 至 FE-UX-1-5 均已通过人工验收，FE-UX-1-6 已形成试运行体验验收包。前端构建、旧页脚本解析和关键页面 9000 可达验证通过。
- **AI 平台升级 BIZ Track BIZ-1a 商务台账 v1 已完成当前环境验证**：已新增 `FEATURE_BUSINESS_LEDGER`、`client_inquiries.direction/stage/next_followup_at/cancelled_*`、`client_inquiry_events`、商务台账 service/schema/API、Vite `/admin/business-ledger` 内联页面和 `scripts/biz1a_business_ledger_smoke.ps1`；当前环境 smoke 已验证 admin/staff 权限、逾期筛选、作废、Phase 2 数据隔离、response-speed 口径不变和 XFF 审计。BIZ-1a v1 已落地字段为 `client_name` / `client_phone`；项目名 / 公司名暂由 `notes` 承载，后续是否拆字段待真实台账数据积累后评估。
- **AI 平台升级 BIZ Track BIZ-2a 成本数据库初始化已完成当前环境验证**：已新增 `FEATURE_COST_DB`、`cost_items`、`cost_item_history`、成本库 service/schema/API、Excel 导入预览/确认、Vite `/admin/cost-db` 页面和 `scripts/biz2a_cost_db_smoke.ps1`；当前环境 Alembic 已升级到 `20260520_0018 (head)`，smoke 已验证 admin/staff 权限、价格变更历史、`draft -> active -> archived` 状态机、归档原因校验、重复 activate/archive 幂等、旧 Excel 解析 191 条、人工/主材/辅材拆分价映射和重复 confirm 幂等。2026-05-21 已补充“撤回启用”和批量状态流转，支持单条/批量 `draft -> active`、`active -> draft`、原因审计和 `archived` 冻结保护。初始 Excel 路径为 `AI_Middle_Office/data/imports/cost_seed_readable.xlsx`。
- **AI 平台升级 BIZ Track BIZ-2b 报价时材料底价查询已完成代码层验证**：已新增报价结果成本参考匹配服务，接入同步 `/chat` 与异步 `quote_jobs` preview 结果；预审弹窗展示“成本库参考价 vs AI 生成价”、匹配类型、价差与偏离底价风险；全量测试通过，Vite build 通过。当前环境成本库 `active=190`，已具备真实运行态成本参考匹配数据基础。
- **AI 平台升级 BIZ Track BIZ-2c 成本库主库化 + active RAG 同步已完成当前环境验证**：已新增 active 成本条目 RAG 同步服务、`/api/v1/admin/cost-items/sync-rag` 管理员接口、`cost_rag_sync_runs` 同步记录表和 `/api/v1/admin/cost-items/sync-rag/runs` 查询接口，Vite `/admin/cost-db` 可手动同步 active 成本条目到 RAG 并查看同步记录；报价成本主库明确为 `cost_items.active`，`draft` / `archived` 不参与报价和有效 RAG 同步；旧 `materials` 作为报价/RAG 源已退役，70 条测试数据已备份后清空，旧 materials 写入/回滚、旧 `/admin/sync_milvus` 和旧知识候选 approve 均返回 410。当前环境 Alembic 已升级到 `20260520_0019 (head)`；全量测试 `145 passed`，Vite build 通过。
- **AI 平台升级 BIZ Track BIZ-2d 成本库参考价命中率优化已完成代码层验证**：已补强成本参考匹配服务，支持中文符号差异、连接词差异、词序变化和 `m`/`米`/`延米` 等单位族兼容，并通过动作词与单位兼容校验降低误命中；真实库 smoke 已确认“窗帘盒灯槽拆除”命中 active `cost_item_id=180`、参考价 `6.0`；已补充编号换行清单输入清洗，发送 N8N 前自动转成分号清单；不新增数据库结构，不启动漏项检测。
- **AI 平台升级 BIZ Track BIZ-2e 漏项检测已完成代码层验证**：已新增 `app/services/quote_omission_detection.py`，在同步 `/chat` 和异步 `quote_jobs` preview 的成本库 enrichment 后追加 `omission_summary` / `omission_suggestions`；首批规则覆盖木地板拆除→踢脚线拆除、防水→防水保护层、墙地砖/吊顶拆除→垃圾清运等保守提示，并内置“窗帘盒/灯槽拆除”不误报验收；只提示、不自动改总价、不新增数据库结构。
- **AI 平台升级 BIZ Track BIZ-2f 报价需求单 Excel 解析已完成代码层验证**：已新增 `app/services/quote_excel_parser.py`，支持 `.xlsx/.xlsm` 需求单按表头识别施工项目、数量、单位、规格/特征和备注；同步 `/chat` 与异步 `quote_jobs` 上传 Excel 时会先转成稳定文本清单，再进入现有 N8N/Dify 报价、成本库参考和漏项检测链路；旧 `.xls` 给出明确另存提示，不新增数据库结构。
- **AI 平台升级 BIZ Track BIZ-2g 成本库底价兜底填价已完成代码层验证**：已在 `quote_cost_matching` 增加保守兜底规则；仅当成本库已命中 active 底价、AI 单价为空/0 且报价行有有效数量时，用参考价回填单价和合计，并在预审弹窗提示“已用成本库底价兜底”；AI 正常正数单价不被覆盖，不新增数据库结构。
- **AI 平台升级 BIZ Track BIZ-2h 成本库价格前置给 AI 报价链路已完成代码层验证**：已新增 `app/services/quote_cost_context.py`，复用 active 成本匹配能力，在 FastAPI 调用 N8N/Dify 前追加命中的成本库底价强参考上下文；`FEATURE_COST_DB=false` 或无命中时保持原请求文本不变，不新增数据库结构，不改 N8N。
- **AI 平台升级 BIZ Track BIZ-2i/BIZ-2j 报价可解释性与证据链展示已完成代码层验证**：已新增报价成本证据审计记录和“查看依据”业务化展示；确认/打回后可追踪 AI 原始价、最终价、成本库参考价、行合计来源、整单合计来源、AI 报价来源和成本库详情链接。
- **BIZ-2 预审体验补强已完成**：支持同名不同规格成本条目在预审阶段切换，切换后同步成本库参考价重算本行报价；AI 报价来源可区分采纳前置成本库、偏离前置成本库、无成本库参考 AI 估算、成本库兜底和人工切换成本条目；报价运营详情可查看预审打回原因。
- **AI 平台升级 BIZ Track BIZ-2k 成本库数据质量体检 + 演示回归包已通过当前环境手工验收**：已新增 `app/services/cost_data_quality.py` 和 `scripts/biz2k_cost_quality_report.py`，只读分析 `cost_items.active` 并生成 Markdown/CSV/XLSX/演示回归包；BIZ-2k-1 已补强业务可读版报告和验收指引；不新增数据库结构、不写库、不触发 RAG 同步、不改变报价口径。
- **商务/市场部路线阶段性暂停**：BIZ-1a 台账属于信息沉淀工具，对核心效率提升有限；BIZ-1b 跟进提醒、BIZ-1c 跟进流水和传统 CRM 式延展暂不启动。后续只保留高价值待定方向：调用外部项目搜索引擎或外部项目收集平台，结合公司内部筛选项目标准，自动筛选合格项目并提供联系方式；待外部平台合作、API 权限和搜索可行性明确后再重新立项。
- **正式生产策略**：系统整体完善前不正式投入生产使用；不再为单一阶段单独做正式生产上线闭环。
- **维护策略**：进入问题驱动维护阶段，优先投入报价准确率、知识库质量、RAG 评测和真实用户体验问题。
- **Phase 4b/4c 暂缓**：语音转写（Phase 4b）和钉钉会议导入（Phase 4c）暂缓，等业务提效路线（BIZ Track）核心功能稳定后再重新评估必要性。
- **client_inquiries 双向模型已落地**：原被动接单（inbound）模型前提不成立——当今市场公司须主动开拓，客户主动联系的情况几乎不存在。`client_inquiries` 已通过 Alembic `20260520_0016` 增加 `direction`（`inbound`/`outbound`）字段；BIZ-1a 商务台账落在 `outbound` 方向；Phase 2 `FEATURE_CLIENT_INQUIRY` 和 `FEATURE_DASHBOARD_RESPONSE` 继续只统计 `inbound`，当前 smoke 已确认 response-speed 样本数不受 outbound 记录影响。

## 业务提效路线（BIZ Track，2026-05-19 新增）

### 背景

Phase 0–4a 解决了系统底座（权限、任务、看板、会议纪要）。在与**商务部**和**成本报价部**进行实地访谈后，识别出两个部门当前最大的日常痛点，均不在底座范围内：

- **商务部**：项目/客户跟进靠微信和人脑记忆，无台账，信息断层，跟进节点丢失，报价询价响应慢。
- **成本报价部**：市场单价靠人脑/旧 Excel，无数字化成本库；报价时无法快速查询材料底价；输入漏项靠人工逐项核对，效率低且易出错。

BIZ Track 在原有底座之上，新增面向这两个部门的业务效率工具，不替换底座，不干扰已完成的 Phase 0–4a。

### 整体路线概览

| 编号 | 名称 | 对应部门 | 优先级 | 依赖 |
|------|------|---------|--------|------|
| BIZ-1 | 商务/市场获客情报 | 商务部 / 市场部 | BIZ-1a 已完成，后续暂停 | Phase 0 RBAC；外部项目源能力待定 |
| BIZ-2 | 报价系统增强 | 成本报价部 | P1（与 BIZ-1 并行） | Phase 0 RBAC |
| BIZ-3 | 经营驾驶舱 | 管理层 | P2（BIZ-1 数据稳定后） | BIZ-1 + 合同/回款模型 |
| BIZ-4 | 知识库治理 | 成本报价部 | 并入 BIZ-2，不单独排期 | BIZ-2a |

### 已调整事项（相对原 Phase 规划）

| 调整项 | 原规划 | 调整后 | 原因 |
|--------|--------|--------|------|
| Phase 4b 语音转写 | Phase 4a 后启动 | 暂缓 | 业务部门未提出实际需求，BIZ Track 优先 |
| Phase 4c 钉钉会议导入 | Phase 4b 后启动 | 暂缓 | 同上 |
| Phase 5 知识库历史数据导入 | 独立 Phase | 并入 BIZ-2a | 成本报价部已有旧 Excel，直接复用 |
| Phase 6 经营驾驶舱数据底座 | 独立 Phase | 重命名为 BIZ-3 | 依赖 BIZ-1 商务数据，需等 BIZ-1 稳定 |
| client_inquiries 模型 | 被动接单（inbound） | 双向（direction 字段） | 市场实情：主动开拓为主，被动接单几乎不存在 |

---

### BIZ-1 商务获客情报

**阶段性结论**：BIZ-1a 已提供轻量"项目/客户跟进台账"，能解决部分信息沉淀问题，但对商务/市场部核心工作效率提升有限。后续不继续做传统 CRM 式提醒、流水和字段扩展，商务/市场部升级路线先告一段落。

**真正高价值方向（待定）**：系统应能调用外部项目搜索引擎或外部项目收集平台，结合公司内部筛选项目的要求，自动筛选符合公司标准的项目，并尽可能提供联系方式，帮助商务/市场部从"人工找项目"转向"系统预筛选项目"。

**待定原因**：
- 外部项目搜索引擎是否能稳定调用、覆盖目标行业并返回高质量项目线索，当前成功率不确定。
- 外部项目收集平台仍在与公司谈合作，尚未确定是否能开放 API、开放哪些字段、调用成本和频率限制。
- 公司内部项目筛选标准需要进一步结构化，需明确行业、地区、预算、项目类型、甲方/总包/分包角色、联系方式可信度等规则后才能自动化。

#### BIZ-1a 商务台账 v1（首期，锁定范围）

**当前状态**：已完成当前环境验证（2026-05-20）。已落地 Alembic `20260520_0016`、`FEATURE_BUSINESS_LEDGER`、5 个 `/api/v1/business-ledger` 接口、`/admin/business-ledger` Vite 页面和 `scripts/biz1a_business_ledger_smoke.ps1`。

**v1 字段范围（锁定，不扩展到 CRM）**

| 字段 | 说明 |
|------|------|
| 客户名 | 已落地为 `client_name`；项目名 / 公司名暂由 `notes` 承载，待真实台账数据积累后再评估是否拆字段 |
| 联系方式 | 电话/微信，手动录入 |
| 信息来源 | 下拉：自主开拓 / 介绍 / 招标平台 / 其他 |
| 当前阶段 | 下拉：初步接触 / 需求确认 / 报价中 / 跟进议价 / 成单 / 丢单 |
| 下次跟进时间 | 日期，超期高亮提醒 |
| 负责人 | 关联用户 |
| 备注 | 自由文本 |

**v1 明确不做（防止 CRM 蔓延）**

- 企业背调 / 外部平台数据接入
- 合同条款分析 / 财务风险评估
- 自动查找联系人 / 爬取公司信息
- 投标文件管理 / 客户评分体系

**技术实现**：复用 `client_inquiries` 表，新增 `direction='outbound'`（主动开拓）；原有 `inbound` 记录为被动接单（历史数据保留）。Alembic `20260520_0016` 已新增 `direction`、`stage`、`next_followup_at`、`cancelled_at`、`cancelled_by_id`、`cancel_reason` 字段，并将 `first_response_time` 调整为 nullable；新建 `client_inquiry_events` 审计表记录 create / stage_change / transfer / edit / cancel。BIZ-1a 时间字段沿用 Phase 2 `client_inquiries` 的 naive Asia/Shanghai 口径，后续若 BIZ-3 需要跨时区聚合再统一治理。

**验收标准**：
- [x] 商务人员可在 Vite 页面新增、编辑、查看项目跟进记录
- [x] 超期未跟进的记录在列表中高亮提醒
- [x] admin 可查看全员台账并按负责人过滤
- [x] 不影响原有 `FEATURE_CLIENT_INQUIRY` / `FEATURE_DASHBOARD_RESPONSE` 数据；当前 smoke 中 response-speed 样本数保持 `17 / 9`

#### BIZ-1b 跟进提醒（暂停）

- 暂不启动。该能力更多是台账提醒延展，对当前核心业务提效不高。
- 若未来商务团队真实使用 BIZ-1a 台账并明确需要系统提醒，再重新评估。

#### BIZ-1c 跟进记录流水（暂停）

- 暂不启动。当前不继续扩展为 CRM，不新增跟进流水表或复杂客户生命周期管理。
- 真实客户沟通仍可先记录在 BIZ-1a `notes`，避免过早做低收益结构化。

#### BIZ-1d 外部项目源自动筛选与联系方式获取（待定）

- 目标：调用外部项目搜索引擎或外部项目收集平台，按公司内部项目筛选标准自动筛选出可跟进项目，并输出项目摘要、匹配原因、风险点和可用联系方式。
- 输入：外部项目源数据、公司内部筛选规则、地区/行业/预算/项目类型等条件。
- 输出：候选项目列表、符合/不符合原因、联系人/联系方式、来源链接、更新时间和可信度标记。
- 当前状态：待定，不启动开发。
- 待定原因：外部项目搜索引擎调用可行性和效果未验证；外部项目收集平台仍在商务合作沟通中，API 是否可调用、字段开放范围、授权成本和频率限制均未确定。
- 启动条件：外部项目源 API 或稳定数据通道确认可用；公司内部筛选标准完成结构化；样本项目试跑证明自动筛选结果对商务/市场部有实际价值。

---

### BIZ-2 报价系统增强

**目标**：给成本报价部提供数字化"材料成本库"，让报价员在 AI 报价时能够快速查询和核对底价，并辅助发现漏项。

#### BIZ-2a 成本数据库初始化（最高优先级）

**当前状态**：已完成当前环境验证（2026-05-20）。已落地 Alembic `20260520_0017` / `20260520_0018`、`FEATURE_COST_DB`、8 个 `/api/v1/admin/cost-items*` 接口、`/admin/cost-db` Vite 页面和 `scripts/biz2a_cost_db_smoke.ps1`；旧 Excel `AI_Middle_Office/data/imports/cost_seed_readable.xlsx` 已可解析为 191 条导入预览，包含人工费、主材费、辅材费等拆分价，全部按 `draft` 口径落库；成本库筛选已支持类别/子类模糊匹配和名称/特征/类别/备注关键词搜索，后续由成本部核定为 `active`。

**前置确认（已确认）**

| 问题 | 已确认答案 |
|------|-----------|
| 数据维护负责人 | 成本部指定主负责人；采购部辅助维护材料采购价 |
| 更新触发条件 | ① 市场明显波动 ② 新项目开工前 ③ 供应商报价更新 ④ 季度定期复核 |
| 审核流程 | 系统导出 Excel → 成本部线下审核 → 反馈意见 → 系统更新（无需系统内审批流） |
| 历史数据处理 | 旧 Excel（2025广东旗胜标杆价初稿）作为结构参考和初始导入，状态标记为"待核定"，不作为正式底价 |
| 初始数据策略 | 近期真实报价作为种子数据，高频品类通过系统使用自动识别 |

**数据模型**（Alembic `20260520_0017` / `20260520_0018`，接在 `20260520_0016` 之后）

```
cost_items
  id, category（大类）, subcategory（子类）, item_name, spec（规格）
  unit（单位）, price（主参考价）
  client_tax_excluded_price（对甲税前综合单价）
  client_labor_price（对甲人工费）
  client_main_material_price（对甲主材费）
  client_auxiliary_material_price（对甲辅材费）
  client_direct_fee（对甲直接费小计）
  client_management_profit（对甲管理费利润）
  subcontract_composite_price（劳务发包综合单价）
  subcontract_labor_price（劳务人工费）
  subcontract_main_material_price（劳务主材费）
  subcontract_auxiliary_material_price（劳务辅材费）
  crew_benchmark_price（班组标底税前价）
  price_type（labor劳务/material材料/combined综合）
  status（draft待核定/active正式/archived已停用）
  source（manual手动/imported导入/ai_suggested AI建议）
  effective_date, notes
  created_by, created_at, updated_at

cost_item_history
  id, cost_item_id, old/new price + 三类价格 + 拆分价, old_status, new_status
  change_type, changed_by, change_reason, changed_at
```

**旧 Excel 导入策略**：
- 解析旧 Excel 结构，映射到 `cost_items` 字段
- 全部以 `status='draft'` 导入，不自动升为 `active`
- 导入后通过管理台审核界面逐条或批量核定

**验收标准**：
- [x] `cost_items` 和 `cost_item_history` 表通过 Alembic 迁移创建成功
- [x] 旧 Excel 可通过导入脚本解析并落库，状态为 `draft`
- [x] 管理台可查看、筛选、编辑、核定（draft→active）和停用（active→archived）成本条目
- [x] 价格变更自动写入 `cost_item_history`
- [x] 可撤回启用（active→draft），撤回原因写入 `cost_item_history`，archived 不可撤回
- [x] 可批量核定 active、批量恢复 draft，批量状态变更逐条写入 `cost_item_history`，archived 跳过且保持冻结

#### BIZ-2b 报价时材料底价查询（BIZ-2a 完成后）

**当前状态**：已完成代码层验证（2026-05-21）。已新增 `app/services/quote_cost_matching.py`，同步 `/chat` 与异步 `quote_jobs` preview 结果都会为 `project_details` 附加 `cost_reference`；旧 `index.html` 预审弹窗已展示成本库参考价、匹配类型、AI 单价价差和“偏离底价”风险。不新增数据库结构。当前环境成本库 `active=190`，已具备真实运行态成本参考匹配数据基础。2026-05-21 BIZ-2d 已对命中率做补强，覆盖符号、连接词、词序和单位族兼容。

- 报价员在输入工料单时，AI 报价流程自动匹配 `cost_items`（active 状态）中的参考底价
- 在预审弹窗中展示"成本库参考价 vs AI 生成价"对比
- 匹配逻辑：优先精确匹配 `item_name + spec`，其次模糊匹配 `item_name`，无匹配时显示"无底价参考"

**代码层验收标准**：
- [x] 同步 `/chat` 和异步 `quote_jobs` preview 结果均附加成本库参考信息
- [x] 仅匹配 `active` 成本条目，`draft` / `archived` 不参与报价参考
- [x] 支持 `item_name + spec` 精确匹配、`item_name` 模糊匹配和无匹配兜底
- [x] 预审弹窗展示参考价、匹配类型、价差和偏离底价风险
- [x] 不新增 Alembic revision

#### BIZ-2c 成本库主库化 + active RAG 同步（BIZ-2b 后）

**当前状态**：已完成当前环境验证（2026-05-21）。已新增 `app/services/cost_rag_sync.py`，将 `cost_items.active` 转为 RAG `/admin/reload` 兼容 payload；新增 `POST /api/v1/admin/cost-items/sync-rag` 管理员接口；新增 Alembic `20260520_0019` 和 `cost_rag_sync_runs` 记录每次同步的时间、状态、数量、耗时和结果；新增 `GET /api/v1/admin/cost-items/sync-rag/runs` 与 Vite `/admin/cost-db`“同步记录”窗口。当前环境成本库 `active=190 / archived=7`，RAG 检索已确认可命中 active 成本条目。

- `cost_items.active` 成为正式报价成本价格主库
- `draft` 不参与报价，不同步 RAG
- `archived` 作为作废冻结态，不参与报价，不同步 RAG
- RAG 同步只使用 active 成本条目，旧 `materials` 已退役并禁止继续写入/同步
- 同步仍复用 CentOS RAG `/admin/reload` 蓝绿切换能力，不改 RAG 服务协议
- 每次同步写入 `cost_rag_sync_runs`，用于后台窗口监控同步成功/失败、时间、数量和错误信息

**代码层验收标准**：
- [x] 管理员可从 `/admin/cost-db` 手动同步 active 成本条目到 RAG
- [x] 后端同步接口只允许 admin/system_admin 调用
- [x] 同步 payload 只包含 `cost_items.active`，并带入主参考价、对甲价、劳务价、班组价和拆分价 notes
- [x] active 为空时拒绝同步并给出明确错误
- [x] 同步记录窗口可查看同步状态、开始/结束时间、数量、耗时、操作人和错误信息
- [x] 新增数据库结构已走 Alembic revision，不启动漏项检测

#### BIZ-2d 成本库参考价命中率优化（BIZ-2b 补强）

**当前状态**：已完成代码层验证（2026-05-21）。本阶段聚焦预审弹窗“成本库参考价”无法命中真实 active 成本条目的问题，只增强匹配算法，不新增页面、不新增数据库结构、不启动漏项检测。当前真实库 smoke 已确认“窗帘盒灯槽拆除”命中 active `cost_item_id=180`、参考价 `6.0`；编号换行清单会在发送 N8N 前自动清洗成分号分隔，避免多行 `1. / 2. / 3.` 需求返回空 body。

- 统一中文标点、斜杠、括号、引号、空格、全半角和连接词归一化，提升“窗帘盒/灯槽拆除”与“窗帘盒灯槽拆除”等写法的命中率
- 增加词序无关 token 匹配，支持“拆除 窗帘盒灯槽”和“窗帘盒灯槽拆除”等顺序差异
- 增加单位族兼容，支持 `m`、`米`、`延米`、`延长米` 等等价单位互认，面积、体积、重量单位保持族内匹配
- 增加动作词和单位兼容保护，避免“安装”误命中“拆除”、`㎡` 单价误命中 `m` 单价
- 增加报价需求文本预清洗，检测到编号换行清单时自动改写为分号分隔的一句话
- 继续只匹配 `cost_items.active`，`draft` / `archived` 不参与报价参考

**代码层验收标准**：
- [x] “窗帘盒/灯槽拆除”类符号差异和单位差异可命中 active 成本条目
- [x] 同类条目词序变化可命中
- [x] 单位不兼容时不返回成本参考
- [x] 动作词不同且 archived 兜底不可用时不误命中
- [x] 编号换行清单在同步 `/chat` 和异步 `quote_jobs` 调用 N8N 前自动清洗
- [x] 不新增 Alembic revision，不启动漏项检测

#### BIZ-2e 漏项检测（BIZ-2c 后评估）

**当前状态**：已完成代码层验证（2026-05-21）。本阶段采用保守规则式漏项检测，不新增数据库结构；同步 `/chat` 和异步 `quote_jobs` preview 都会在成本库参考价匹配后追加 `omission_summary` / `omission_suggestions`，旧 `index.html` 预审弹窗只展示“疑似漏项”提示，不自动新增报价行、不改变系统合计。

- AI 报价生成后，基于当前报价行和 `cost_items.active` 检测常见配套漏项
- 首批规则覆盖木地板拆除→踢脚线拆除、防水→防水保护层、墙地砖/吊顶拆除→垃圾清运等低风险提示
- 如果配套项已在报价明细或备注中出现，则自动静默
- 如果成本库中找不到 active 的配套成本条目，则不提示
- “窗帘盒/灯槽拆除”等明确单项拆除不做泛化误报

**代码层验收标准**：
- [x] 仅使用 `cost_items.active` 作为漏项提示候选源
- [x] 预审弹窗展示疑似漏项、触发行、原因和成本库参考价
- [x] 已包含配套项时不重复提示
- [x] “窗帘盒/灯槽拆除”不误报漏项
- [x] 不自动新增报价行、不改变合计、不新增 Alembic revision

#### BIZ-2f 报价需求单 Excel 解析

**当前状态**：已完成代码层验证（2026-05-21）。本阶段只解决 `index` 业务工作台上传客户需求单 Excel 的解析问题，不新增数据库结构，不改 N8N/Dify，不替代 admin 成本库 Excel 导入。

- 支持 `.xlsx/.xlsm` 报价需求单上传
- 自动识别常见表头：施工项目/项目名称/工作内容、数量/工程量、单位/计量单位、规格/特征、备注/说明
- 跳过空行、合计/小计/汇总行和重复表头行
- 解析后输出稳定文本清单，进入现有报价流程
- 图片附件继续走 GLM-4V；PDF 继续拦截；旧 `.xls` 提示另存为 `.xlsx`

**代码层验收标准**：
- [x] 标准列名 Excel 可解析为报价清单文本
- [x] 常见别名列名 Excel 可解析为报价清单文本
- [x] 空表/缺少项目列时给出明确错误
- [x] 同步 `/chat` 和异步 `quote_jobs` 均支持 Excel 上传
- [x] Excel 上传不调用 GLM-4V
- [x] 不新增 Alembic revision，不改 N8N/Dify

#### BIZ-2g 成本库底价兜底填价

**当前状态**：已完成代码层验证（2026-05-21）。本阶段只处理报价预审中“AI 生成 0 元，但成本库已命中明确底价”的异常结果，不新增数据库结构，不改 N8N/Dify。

- 成本库命中 `active` 条目，且参考价大于 0
- AI 单价为空或小于等于 0
- 报价行能解析到有效数量
- 满足条件时回填 `unit_price` 和 `total_price`
- 在成本参考元数据中记录兜底前后的单价/合计，并在预审弹窗提示
- AI 已给出正数单价时不覆盖
- 无数量时不兜底，继续保留人工复核风险

**代码层验收标准**：
- [x] 0 元单价 + 成本库命中 + 有数量时回填底价和合计
- [x] 正常正数 AI 单价不被覆盖
- [x] 无数量时不自动回填
- [x] 预审弹窗展示“已用成本库底价兜底”
- [x] 不新增 Alembic revision，不改 N8N/Dify

#### BIZ-2h 成本库价格前置给 AI 报价链路

**当前状态**：已完成代码层验证（2026-05-22）。本阶段将成本库底价从“AI 返回后的校验/兜底”前移到“请求进入 N8N/Dify 前的强参考上下文”，不新增数据库结构，不改 N8N/Dify。

- 新增 `quote_cost_context`，复用 `quote_cost_matching` 的 `cost_items.active` 匹配能力
- 同步 `/chat` 与异步 `quote_jobs` 在调用 N8N 前追加 `[成本库底价强参考]`
- 上下文包含需求项、数量、单位、匹配类型、成本库项、`reference_unit_price`、参考合计和 `cost_item_id`
- Excel 需求单优先使用 BIZ-2f 解析出的结构化行；文本需求使用保守分段和数量单位识别
- `FEATURE_COST_DB=false`、无 active 成本项或无命中项时，请求文本保持不变
- BIZ-2g 后置兜底继续保留，用于 AI 仍返回空/0 单价的安全网

**代码层验收标准**：
- [x] 成本库命中项会在 N8N 前进入 `text.content`
- [x] 同步 `/chat` 与异步 `quote_jobs` 行为一致
- [x] 仅使用 `cost_items.active`，不使用 draft/archived
- [x] 无命中或关闭 `FEATURE_COST_DB` 时不改变原请求
- [x] 不新增 Alembic revision，不改 N8N/Dify

#### BIZ-2i 报价可解释性与审计记录

**当前状态**：已完成代码层验证（2026-05-22）。本阶段解决“业务员和管理员需要知道报价为什么是这个价、成本库证据来自哪里、最终合计按什么口径计算”的问题。

- 新增报价成本证据审计记录，确认/打回后保留每行报价的 AI 原始价、最终价、成本库参考价、价差、成本条目快照和证据链接
- 记录行合计来源与整单合计来源，区分 AI 报价计算、成本库兜底、人工确认价和混合来源
- 后台报价反馈/报价运营可查询成本证据，支持按报价任务和成本条目追溯
- 不新增 Alembic revision，复用已有报价反馈和成本证据模型；不改变报价计算规则

#### BIZ-2j 报价依据与成本库证据链展示优化

**当前状态**：已完成代码层验证（2026-05-22）。本阶段将 BIZ-2i 的审计数据整理为业务员可读的预审展示。

- 旧 `index.html` 预审“查看依据”弹窗重排为 AI 报价来源、AI 报价依据、成本库参考、合计对照等分区
- 成本库详情链接由文本 URL 改为按钮跳转，便于业务员直接打开 `/admin/cost-db?cost_item_id=...`
- 后台报价任务详情成本证据表补充 AI 来源、成本参考、合计来源和证据跳转
- 不新增数据库结构，不迁移旧 HTML，不改变报价计算规则

#### BIZ-2 预审体验与演示前补强

**当前状态**：已完成当前环境手动验收并已推送（2026-05-23）。本阶段为 BIZ-2i/BIZ-2j 后的真实业务使用补强。

- 品牌文案统一：旧“闪筑智价”已统一改为“旗胜智价”
- 同名不同规格成本条目可在预审阶段切换，切换后同步采用新成本条目参考价重算本行单价/合计
- AI 报价来源显式化，区分“采纳前置成本库 / 偏离前置成本库 / 无成本库参考 AI 估算 / 成本库兜底 / 人工切换成本条目”
- 兼容旧结果缺少来源字段的情况，可根据 AI 单价与成本库参考价自动推断来源
- 报价运营任务详情新增“预审打回”区块，展示打回人、打回时间和打回原因

#### BIZ-2k 成本库数据质量体检 + 演示回归包

**当前状态**：已通过当前环境手工验收（2026-05-28，BIZ-2k-1 报告可读性补强后复验）。本阶段只做只读体检和演示回归准备，不改报价逻辑、不改数据库结构、不新增页面/API、不写数据库、不触发 RAG 同步。

- 新增 `app/services/cost_data_quality.py`：分析 `cost_items.active` 中同名不同规格、完全重复、价格为空/0、三类参考价缺失、单位异常、同名单位混用、规格/备注缺失、相似条目和最近 RAG 同步数量差异。
- 新增 `scripts/biz2k_cost_quality_report.py`：从当前数据库只读读取 active 成本条目和最近同步记录，默认优先输出到 `outputs/biz2k/`，若本地目录不可写则回退到 `AI_Middle_Office/biz2k_reports/`。
- 报告产物：`cost_quality_*.md`、`cost_quality_issues_*.csv`、`cost_quality_*.xlsx`、`demo_regression_pack_*.md`。
- 报告中的问题均为“建议人工复核”，不代表系统自动判错；即使当前报价功能已完成业务员验收，也不据此自动修改报价规则、价格口径、无底价处理或成本库沉淀规则。
- 测试覆盖：新增 `tests/test_cost_data_quality_biz2k.py`，验证体检分类、active 范围、演示样例和 Markdown/CSV/XLSX 生成。

#### BIZ-2l 甲方需求单清洗与标准化预审

**当前状态**：BIZ-2l-0 已完成文档层确认，BIZ-2l-1 已完成代码层验证，BIZ-2l-2 已完成当前环境验收，BIZ-2l-3/BIZ-2l-4/BIZ-2l-5/BIZ-2l-6 已通过当前环境业务验收（2026-05-27）；字段合同详见 `docs/biz-2l-requirement-standardization-biz2l0.md`，验收记录与操作 SOP 详见 `docs/biz-2l-acceptance-and-sop.md`。成本报价部已完成当前报价功能验收，反馈为“功能目前没问题，主要等待更多材料数据导入，之后再优化相关功能”。下一类真实风险来自甲方需求单不固定：字段名、材料名称、规格描述、单位数量、合并单元格、多 Sheet 和备注写法都可能与 `cost_items` 固定字段不一致。由于真实甲方需求单数量有限且样式离散，本阶段不再以“大量样本收集 / 甲方模板库”为前提，而是改为“通用清洗解析 + 人工确认兜底”。

**定位判断**：不是单纯“加强 AI 识别”、单纯“要求人工先清洗 Excel”，也不是先建设甲方模板库，而是在报价前新增一层“需求单清洗与标准化预审”。系统负责把甲方原始需求单转换成统一的标准报价输入，AI/规则识别作为清洗能力的一部分；识别不准时由业务员快速指定列映射、修正行内容，确认后的标准清单再进入现有报价链路。

**目标**：

- 把不同甲方、不同字段名、不同表格结构的需求单，统一整理成系统可理解的标准报价行。
- 保留原始行、原始字段、标准字段、识别置信度、缺失提示和成本库候选，便于业务员核对。
- 降低因“名称不一致、规格写在备注里、单位数量拆不开、字段别名不同”导致的成本库误匹配或漏匹配。
- 让后续材料数据继续导入后，可以通过更稳定的标准输入提升命中率，而不是直接改报价规则。

**边界**：

- 不改报价逻辑、不改价格口径、不改无底价项目处理规则。
- 不自动新增报价行、不自动改总价、不自动沉淀成本库。
- 首版不新增数据库结构；如后续需要保存模板映射、客户画像或人工修正历史，必须单独立项并走 Alembic。
- 不迁移旧 `index.html` / `admin.html` / `app.html`，不生成新 HTML。
- 不改 N8N/Dify 工作流；清洗后的标准文本继续进入现有报价链路。
- `PUBLIC_ACCESS_ENABLED=false` 内网验证前提不变。

**标准输出草案**：

| 字段 | 用途 |
|------|------|
| `source_file` / `source_sheet` / `raw_row_index` | 定位原始需求单来源 |
| `raw_fields` / `raw_text` | 保留甲方原始字段和值，便于追溯 |
| `item_name` | 标准施工项目/材料名称 |
| `spec` | 规格、型号、做法、特征描述 |
| `unit` | 标准化单位 |
| `quantity` | 标准化数量 |
| `quantity_source` / `quantity_candidates` | 标准数量来源与原始工程量候选，避免多楼层/多工程量字段被误读 |
| `remark` | 不能安全结构化但需要保留的信息 |
| `location` / `work_area` | 房间、区域、楼层等位置字段，首版可选 |
| `field_mapping` | 原字段到标准字段的映射结果 |
| `confidence` | 行级识别置信度 |
| `warnings` | 缺数量、缺单位、规格不清、疑似合并行等提示 |
| `cost_candidates` | 基于 `cost_items.active` 的候选匹配，只展示，不自动定价 |

**执行清单**：

1. **BIZ-2l-0 标准字段与典型场景确认（已完成文档层确认）**：不等待大量真实样本，已确认标准字段、字段别名、单位数量规则、行类型、典型场景、置信度、警告码、人工确认口径和 BIZ-2l-1 输出合同；产物为 `docs/biz-2l-requirement-standardization-biz2l0.md`。
2. **BIZ-2l-1 只读通用清洗解析器（已完成代码层验证）**：新增 `app/services/requirement_standardizer.py`，输入 `.xlsx/.xlsm`，输出标准化预览 JSON/CSV/Markdown；识别候选表头、字段语义、数据行、说明行、合计行、空行和低置信度行；新增 `scripts/biz2l_requirement_standardization_preview.py` 供本地只读预览；不接报价、不写数据库。
3. **BIZ-2l-2 人工列映射与行确认（已完成当前环境验收）**：新增需求单标准化确认工作台，系统先猜测项目名称、规格描述、标准数量、单位、备注等列；业务员可按 Sheet 修正列映射、按 Sheet 确认/剔除行、查看原始行追溯、搜索筛选原始字段、切换标准数量来源，并通过本地历史解析记录和版本回滚继续未完成进度；不接报价、不写数据库。
4. **BIZ-2l-3 标准清单接入报价链路（已完成代码层验证并通过业务验收）**：只有人工确认后的标准清单进入现有报价、成本库匹配、漏项检测、底价兜底和证据链流程；报价规则和价格口径保持不变。前端通过“发起报价”复用现有 `/api/v1/quote/jobs`，发起前重新校验当前行，阻断行不进入报价；创建任务后自动跳转旧报价工作台接管进度，并复用原有预审弹窗人工验收；行确认可对当前筛选结果全选、取消选择、批量确认和批量撤回确认；若存在未通过校验行，自动展示问题面板并定位到对应 Sheet 原始行。
5. **BIZ-2l-4 预审对账与运营复核详情（已完成当前环境业务验收）**：持久化确认需求行，在预审弹窗和报价运营详情中展示确认清单与 AI 预审条目的数量差异、疑似未报价行、无底价参考和各项复核检查；新增数据库结构通过 Alembic `20260526_0022`，不改变报价规则和价格口径。
6. **BIZ-2l-5 确认清单逐行报价完整性保障（已完成当前环境业务验收）**：报价执行时将确认需求行以 `requirement_row_key` 逐行传给 AI，要求 `project_details` 与确认清单逐条对应；预审结果带 `requirement_integrity` 摘要，旧预审弹窗和报价运营详情提示完整性状态，预审不完整时前端禁用确认推送、后端 `/confirm_push` 返回 409 阻断。
7. **BIZ-2l-6 确认清单分批报价与缺失占位（已完成当前环境业务验收）**：大清单按批进入现有 N8N/Dify 报价链路，缺失行补报后仍未返回则生成需人工补价的占位预审行；占位行不触发成本库底价自动兜底，未补单价/合计前阻断下发；169 行确认清单已验证保留全部预审行、占位补价提示和推送阻断。
8. **BIZ-2l 验收记录与操作 SOP（已完成文档整理）**：新增 `docs/biz-2l-acceptance-and-sop.md`，覆盖环境基线、阶段验收记录、169 行大清单验收结果、业务员操作步骤、管理员复核步骤、阻断条件、异常处理和交接检查表。
9. **BIZ-2l-7 规则沉淀评估**：等真实使用样本自然积累后，再评估字段别名、客户偏好、人工修正历史或模板记忆；只要涉及持久化，必须单独设计 Alembic。
10. 字段别名基础规则：先内置通用别名，如“项目名称/施工项目/工作内容/材料名称/清单名称”，“规格/型号/项目特征/描述/备注”，“工程量/数量”，“单位/计量单位”。
11. 单位与数量规范：统一 `m/米/延米`、`㎡/m2/平方米`、`项/套/个` 等常见单位，识别数量和单位粘连、范围值、空数量、合计行。
12. 名称与规格拆分：把“拆除木地板20㎡”“窗帘盒/灯槽拆除 18m”“墙面乳胶漆 两遍”这类混合描述拆成名称、规格、数量、单位和备注。
13. 成本库候选提示：复用 active 成本库匹配能力，只给出候选和置信度，不自动选价；低置信度必须提示人工确认。

**验收标准**：

- 使用少量典型 Excel 场景样例和可获得的真实/脱敏需求单，系统能输出标准化预审结果，并保留原始行追溯；不要求先收集大量甲方模板。
- 对核心字段 `item_name`、`quantity`、`unit`、`spec/remark` 的识别结果可被业务员快速核对；缺失或低置信度必须显式提示。
- 已经格式规范的 Excel 需求单，标准化前后进入报价链路的报价规则和价格口径保持不变。
- 成本库候选只作为参考展示，不自动修改单价、不自动新增成本条目。
- 对合计行、说明行、空行、疑似标题行不生成报价项，或至少标记为需人工确认。
- 列映射猜错时，业务员可以人工指定正确列；人工确认前不允许低置信度内容直接进入报价。
- 行确认已按 Sheet 分组展示；业务员可以快速定位原始行内容、过滤低置信度/有警告/多数量候选/缺数量行。
- 历史解析记录和版本回滚只保存在浏览器本地 IndexedDB，不写数据库；刷新或重新进入页面后可继续上次确认进度。
- 人工确认清单可一键创建现有异步报价任务；创建前必须重新校验，阻断行、剔除行、说明/汇总/空白行不进入报价。
- 全量后端测试通过；如涉及前端展示，`ai-web` build 通过。
- 未新增数据库结构；如实际开发中必须新增持久化字段，则暂停本阶段并先补 Alembic 设计。

**依赖与风险**：

- 不依赖大量真实需求单样本；真实样本只用于后续回归增强和规则校准。
- 甲方字段和材料名称天然不统一，首版目标应是“降低整理成本和误识别风险”，不是承诺 100% 自动识别或自动套用模板。
- 如果过早自动记忆人工修正，可能把错误映射沉淀为规则；因此首版不做自动学习和自动入库。
- 成本库材料数据不足时，即使需求单清洗正确，也只能提升输入质量，不能凭空提高底价覆盖率。

**知识库深度治理后续**：BIZ-2c 已覆盖 active 成本条目同步进 RAG 的主路径；后续如需同时保留工艺知识、历史资料和成本主库，再设计多源 RAG 合并策略。原 Phase 5（知识库历史数据治理导入）的目标通过 BIZ-2a 导入和 BIZ-2c 联动覆盖，不单独排期。

#### BIZ-2m 无底价项目规则开发落地

**当前状态**：已通过当前环境手工验收（2026-05-28），计划见 `docs/biz-2m-no-cost-draft-implementation-plan.md`，演示与验收记录见 `docs/biz-2m-demo-and-acceptance.md`。本阶段承接 `docs/biz-2-no-cost-reference-rule-draft.md`，已在确认报价并下发成功后，把无 `cost_items.active` 参考价且已人工确认价格的预审行沉淀为成本库 `draft` 待审核条目，来源为 `ai_suggested`。

**目标**：

- 无底价行在预审中明确提示“无成本库参考价，AI 估价，仅供参考，请人工确认价格依据”。
- `/confirm_push` 成功后，把无底价且有正数单价/合计的行写入 `cost_items.draft(source=ai_suggested)`。
- BIZ-2l-6 的占位行如果未补价，继续阻断下发；如果已人工补价且下发成功，可以作为无底价 draft 候选。
- 自动生成的 draft 不参与后续报价成本参考、不触发成本库兜底、不进入 active RAG 同步。
- 成本部人工复核并启用为 `active` 后，后续同类报价才允许命中成本库参考价。

**阶段边界**：

- 不让 AI 估价自动成为 `active`。
- 不改变报价规则、价格口径、N8N/Dify 工作流或成本库匹配口径。
- 不自动覆盖已有 active 成本条目。
- 首版优先复用 `cost_items.status/source/notes/created_by` 与 `cost_item_history`，不新增 Alembic；若需要结构化来源字段，再暂停并单独设计迁移。
- 不迁移旧 `index.html` / `admin.html` / `app.html`，不生成新 HTML。
- 不启动 Phase 4b/4c/6，不启动 BIZ-1b/BIZ-1c/BIZ-1d。

**验收重点**：

- 推送成功才生成 draft；推送失败不生成。
- 有 active 成本参考的行不生成无底价 draft。
- 未补价占位行 `/confirm_push` 仍返回 409。
- 已补价占位行可生成 `draft(source=ai_suggested)`。
- 重复 draft 不重复堆积。
- draft 启用前不参与报价，启用后可命中。
- 开发完成后必须跑自动化测试、写演示规划并进行模拟演示，记录不清晰或不流畅的问题。

**本次验证**：

- BIZ-2m 新增专项测试和确认推送测试通过：`10 passed, 1 warning`。
- 旧确认推送、报价历史、报价反馈、占位阻断和成本匹配回归通过：`25 passed, 1 warning`。
- BIZ-2l/BIZ-2m 组合报价任务测试通过：`35 passed, 1 warning`。
- `ai-web` 的 `npm.cmd run build` 通过。
- 旧 `index.html` 脚本语法检查通过。
- 重启后运行态确认：9000 新 PID `32364`，`/health/ready=ready`，database ok，Celery broker/worker ok，worker_count=1，Alembic `20260526_0023 (head)`，`FEATURE_NO_COST_DRAFT_CAPTURE=True`。

#### BIZ-2n 预审人工改价字段与合计联动

**当前状态**：已通过当前环境手工验收（2026-05-28），详见 `docs/biz-2n-manual-price-preview.md`。本阶段修复 BIZ-2m 人工验收中暴露的“只改系统合计、单价仍为 0”操作风险。

**已完成**：

- 旧 `index.html` 预审表格新增可编辑“工程量/单位”和“人工改价(元)”列，其中“人工改价”位于“成本库参考”和“系统合计(元)”之间。
- “人工改价”默认取成本库 `reference_price`，无成本库参考时默认 0。
- 修改“工程量”或“人工改价”后按工程量联动“系统合计(元)”。
- AI 返回工程量 0 但源 Excel 有有效工程量时，后端用源 Excel 工程量回填后再进行成本库参考、兜底和预审展示。
- 确认下发前将 `manual_unit_price` 归一写回现有 `unit_price`，将系统合计写回 `total_price`。
- 未补有效人工改价或系统合计前阻断确认推送。
- 保留 AI 原始建议价展示，避免把 AI 价与最终人工确认价混用。

**边界**：

- 不新增数据库结构，不新增 Alembic。
- 不改 N8N/Dify。
- 不改成本库 active/draft 规则。
- 不迁移旧 HTML。

**验证**：

- 旧 `index.html` 脚本语法检查通过。
- 相关确认推送、报价历史、报价反馈、占位阻断和 BIZ-2m draft 沉淀回归通过：`22 passed, 1 warning`。
- `ai-web` 的 `npm.cmd run build` 通过。

#### BIZ-2o 成本库状态与流向台账

**当前状态**：已通过当前环境手工验收（2026-05-28），详见 `docs/biz-2o-cost-lineage.md`。本阶段用于让成本部集中追踪成本条目的来源、状态和后续引用。

**已完成**：

- `/admin/cost-db` 新增“状态与流向”入口。
- 支持总览、新增 draft、active 记录和归档记录四类视图。
- 展示条目来源、当前状态、当前去向、生命周期和报价引用。
- AI 建议 draft 可追溯来源报价任务、报价历史、行号、Sheet 和确认价格。
- active 条目可查看后续报价引用次数和最近引用记录。
- 首版不新增数据库结构，RAG 去向按 active 范围和最近成功同步批次推断，不记录单条同步明细。

**验证**：

- 成本库/同步/无底价沉淀/确认推送/成本匹配回归 `48 passed, 3 warnings`。
- 清理后关键回归 `17 passed, 1 warning`。
- `ai-web` 的 `npm.cmd run build` 通过。

---

#### BIZ-2p 预审人工改价来源判定与 AI 建议采纳

**当前状态**：已通过当前环境手工验收（2026-05-28），详见 `docs/biz-2p-preview-price-source.md`。本阶段用于让成本库 draft 来源区分“人工改价”和“AI 建议价”。

**已完成**：

- 旧预审弹窗“人工改价(元)”列新增“采用AI建议”按钮。
- 点击“采用AI建议”后自动采用 AI 建议单价，并按工程量重算系统合计。
- 手动改价下发后，无底价 draft 来源写 `manual`，前端显示“人工”。
- 采纳 AI 建议下发后，无底价 draft 来源写 `ai_suggested`，并在 notes/状态与流向详情记录“人工确认采纳AI建议”。
- 成本库状态与流向详情新增“价格动作”字段。
- 首版复用 `cost_items.source` 和 `cost_items.notes`，不新增数据库结构。

**验证**：

- 无底价确认推送专项 `7 passed, 1 warning`。
- 无底价 draft 捕获专项 `7 passed, 1 warning`。
- 成本库状态与流向专项 `1 passed, 1 warning`。
- 旧 `index.html` 脚本语法检查通过。
- `ai-web` 的 `npm.cmd run build` 通过。

---

#### BIZ-2q 报价预审草稿保存与恢复

**当前状态**：已通过当前环境手工验收（2026-05-28），详见 `docs/biz-2q-preview-draft-save.md`。本阶段用于解决“无底价预审单填价过程中离开页面后丢失人工价格”的问题。

**已完成**：

- 新增 `quote_preview_drafts` 表和 Alembic `20260527_0024`，按报价任务保存一份预审编辑草稿。
- 新增预审草稿查询、保存、放弃和标记已下发接口，权限沿用报价任务权限。
- 旧 `index.html` 预审弹窗新增“保存草稿”和保存状态提示。
- 旧 `index.html` 预审弹窗新增“关闭”按钮，关闭前保存草稿，不触发打回或确认下发。
- “我的报价历史”会展示 `editing` 草稿，推送列显示“草稿”，操作列显示“编辑”。
- “我的报价历史”不再将草稿固定置顶，统一按时间倒序排列。
- “我的报价历史”新增时间、报价内容、项目数、总价和状态筛选。
- “我的报价历史”新增草稿多选与批量删除；批量删除仅删除 `editing` 草稿快照，不删除报价任务或已推送历史。
- 人工改价、系统合计、工程量、单位、施工项目、备注、采用 AI 建议、切换成本库条目后会自动保存。
- 再次打开同一报价任务时自动恢复 `editing` 草稿。
- 打回重填后草稿标记为 `discarded`；确认下发成功后标记为 `pushed`，防止继续覆盖。

**验证**：

- 预审草稿专项 `2 passed, 1 warning`；预审草稿 + 历史入口专项 `3 passed, 1 warning`；预审草稿/确认下发/占位阻断组合回归 `11 passed, 1 warning`。
- 历史筛选与草稿批量删除补充回归：`tests\test_quote_preview_drafts_biz2q.py tests\test_quote_history.py tests\test_quote_confirm_push_biz2m.py` 为 `11 passed, 1 warning`。
- 旧 `index.html` 脚本语法检查通过。
- in-app browser 加载 `http://127.0.0.1:9000/index.html` 无 console error。
- `ai-web` 的 `npm.cmd run build` 通过。
- BIZ-2q 当时数据库已升级到 `20260527_0024 (head)`。

---

### BIZ-3 企业内部工程项目进度中台 / 经营驾驶舱后续

**当前优先目标**：先把企业内部工程项目进度透明化，围绕项目、阶段、任务、2580 进度、成果证据、缺证据提醒和关键节点门禁建立可审计的项目进度中台。

**当前状态（2026-06-02）**：项目进度中台已推进至 BIZ-3b-3-2 并通过当前环境业务验收；经营驾驶舱轻量 MVP 已推进至 BIZ-3c-3，后端只读聚合接口、前端经营总览标签页、趋势/分布和硬门禁放行后缺证据精确风险均已完成代码层验证。项目进度底座、单人试运行模板、旗胜 EPC 流程模板、任务成果证据 MVP、无证据软提醒、缺证据汇总、证据策略显式字段落库与存量回填、A 级 `complete_required` 硬门禁与放行事件均已完成；当前库已激活 8 条 A 级硬门禁任务。BIZ-3c 规划与验收记录详见 `AI_Middle_Office/docs/biz-3c-business-dashboard-lite-mvp-planning.md`。

**当前经营驾驶舱轻量 MVP 目标**：先复用现有报价、成本库、项目进度和系统健康数据，在管理台提供只读经营总览，用于内网试运行和管理层汇报；不新增经营数据表，不计算合同额、回款率、毛利和逾期应收。

**后续完整经营驾驶舱目标**：待项目进度、合同、回款、成本数据稳定后，再在管理层页面汇总合同签约、回款、项目成本、毛利等经营指标，辅助决策。

**前置依赖**：
- BIZ-1 商务数据稳定（项目/合同关联已建立）
- 合同、回款、项目成本数据模型已设计并有真实数据录入

**后续计划内容（项目进度与 BIZ-1 稳定后再详细规划）**：
- 合同模型：`contracts`（draft/signed/archived/cancelled）
- 回款模型：`payments`（pending/paid/cancelled）
- 项目成本模型：`project_costs`（active/cancelled）
- 驾驶舱视图：合同金额、已回款、待回款、成本、毛利的月度/季度趋势

**注意**：STATE-MACHINES.md 中已预定义 `contracts`、`payments`、`project_costs`、`contract_adjustments` 的状态机规则，BIZ-3 实现时必须严格遵守，不得绕过状态机直接改状态字段。

---

### BIZ-4 知识库治理

已并入 BIZ-2，不单独排期。覆盖：
- 旧 Excel 导入（BIZ-2a）
- 成本库联动 RAG（BIZ-2c）
- 知识候选从真实反馈生成（已在 P3 落地）

---

### 推进序列

**第一阶段（当前，BIZ-1a / BIZ-2a 已完成且商务/市场部路线暂停）**

| 子项 | 内容 | 状态 |
|------|------|------|
| Phase 4a 运行态验收 | 打开 `FEATURE_MEETING_AI`，走读会议纪要页面，确认功能正常 | 已完成（不阻塞） |
| BIZ-2a 数据模型 | 设计 `cost_items` + `cost_item_history`，新增 Alembic revision | 已完成当前环境验证（2026-05-20） |
| BIZ-2a 旧 Excel 导入 | 解析旧 Excel 结构，编写导入脚本，落库为 `draft` | 已完成当前环境验证（2026-05-20） |
| BIZ-2a 管理台界面 | 成本条目查看、筛选、核定、停用 | 已完成当前环境验证（2026-05-20） |
| BIZ-1a 台账 | `client_inquiries` 新增 `direction`/`stage`/`next_followup_at`/`cancelled_*` 字段（含将 `first_response_time` 改为 nullable），新建 `client_inquiry_events` 审计表，Vite 台账页面 | 已完成当前环境验证（2026-05-20） |
| BIZ-1 后续提醒/流水/CRM 延展 | BIZ-1b / BIZ-1c 等低收益延展 | 暂停，不进入下一批开发 |

**第二阶段（BIZ-2a 完成后）**

| 子项 | 内容 | 状态 |
|------|------|------|
| BIZ-2b | 报价时材料底价查询 + 预审价格对比 | 已完成代码层验证（2026-05-21）；当前 active=190 可参与运行态匹配 |
| BIZ-2c | 成本库主库化 + active RAG 同步 | 已完成当前环境验证（2026-05-21）；同步记录窗口已落地 |
| BIZ-2d | 成本库参考价命中率优化 | 已完成代码层验证（2026-05-21）；不新增 Alembic |
| BIZ-2e | 漏项检测 | 已完成代码层验证（2026-05-21）；保守规则提示，不新增 Alembic |
| BIZ-2f | 报价需求单 Excel 解析 | 已完成代码层验证（2026-05-21）；支持 `.xlsx/.xlsm` 需求单 |
| BIZ-2g | 成本库底价兜底填价 | 已完成代码层验证（2026-05-21）；0 元报价命中底价时保守回填 |
| BIZ-2h | 成本库价格前置给 AI 报价链路 | 已完成代码层验证（2026-05-22）；N8N/Dify 前追加 active 底价强参考 |
| BIZ-2i | 报价可解释性与审计记录 | 已完成代码层验证（2026-05-22）；记录成本证据、合计来源和证据链接 |
| BIZ-2j | 报价依据与成本库证据链展示优化 | 已完成代码层验证（2026-05-22）；预审查看依据和后台证据表已优化 |
| BIZ-2 体验补强 | 品牌统一、条目切换、AI 来源、打回原因展示 | 已完成当前环境手动验收并推送（2026-05-23） |
| BIZ-2k | 成本库数据质量体检 + 演示回归包 | 已通过当前环境手工验收（2026-05-28，BIZ-2k-1 报告可读性补强后复验）；只读报告，不改报价逻辑/数据库结构 |
| BIZ-2l | 甲方需求单清洗与标准化预审 | BIZ-2l-0/BIZ-2l-1/BIZ-2l-2 已完成；BIZ-2l-3/BIZ-2l-4/BIZ-2l-5/BIZ-2l-6 已完成当前环境业务验收；验收记录与操作 SOP 已整理到 `docs/biz-2l-acceptance-and-sop.md`；当前可由人工确认清单发起现有异步报价任务，并在预审/报价运营详情中对账确认行与预审行；大清单会分批报价，缺失行生成需人工补价占位行，未补价前阻断确认推送，不改价格口径 |
| BIZ-2m | 无底价项目规则开发落地 | 已通过当前环境手工验收（2026-05-28）；见 `docs/biz-2m-demo-and-acceptance.md` |
| BIZ-2n | 预审人工改价字段与合计联动 | 已通过当前环境手工验收（2026-05-28）；旧预审弹窗新增可编辑工程量/单位和人工改价列，源 Excel 有效工程量可覆盖 AI 返回的 0 工程量，修改工程量或改价后联动系统合计并在下发前写回 `unit_price/total_price`；见 `docs/biz-2n-manual-price-preview.md` |
| BIZ-2o | 成本库状态与流向台账 | 已通过当前环境手工验收（2026-05-28）；成本数据库新增“状态与流向”入口，可查看 draft/active/archived、来源、生命周期和报价引用；见 `docs/biz-2o-cost-lineage.md` |
| BIZ-2p | 预审人工改价来源判定与 AI 建议采纳 | 已通过当前环境手工验收（2026-05-28）；手动改价沉淀为人工来源，采用 AI 建议沉淀为 AI 建议来源并记录人工采纳动作；见 `docs/biz-2p-preview-price-source.md` |
| BIZ-2q | 报价预审草稿保存与恢复 | 已通过当前环境手工验收（2026-05-28）；同一报价任务可保存和恢复预审人工改价草稿，确认下发后锁定；历史列表支持筛选、按时间排序和批量删除无用草稿；见 `docs/biz-2q-preview-draft-save.md` |
| BIZ-2r | 成本库重复 active 防护与报价多候选提示 | 已通过当前环境手动验收；启用 active 前阻断重复或高风险相似条目，无底价沉淀前去重，报价命中多个 active 候选时要求人工确认成本依据；见 `docs/biz-2r-cost-duplicate-active-guard.md` |
| BIZ-2s | 成本价权限落地首版 | 已通过当前环境手动验收；普通 staff 不再浏览完整成本库，成本专项角色和报价预审受限候选接口已落地；见 `docs/biz-2s-cost-price-permissions-implementation.md` |
| BIZ-2t | 成本库数据治理执行包 | 已生成当前环境只读治理基线；报告位于 `reports/biz2t/20260528_current/`，高风险 5 条，试运行建议 `cleanup_before_trial`；见 `docs/biz-2t-cost-data-governance-execution-pack.md` |
| BIZ-2t-1 | 高风险整改交接清单 | 已完成文档层准备；将 5 条高风险试运行阻断项整理为成本部逐条核价表，见 `docs/biz-2t-high-risk-cost-handoff.md` |
| BIZ-2t-2 | 高风险整改结果复核包 | 已完成只读复核；当前 5 条均为 `accepted_risk`，`trial_blocker_count=0`，建议 `ready_with_known_risks`，见 `docs/biz-2t-2-high-risk-handoff-review.md` |
| BIZ-2u | 小范围内网试运行准备包 | 已完成文档层准备；明确准入门槛、人员角色、样例清单、反馈表、验收口径和暂停条件；正式试运行未启动，见 `docs/biz-2u-internal-trial-preparation.md` |
| BIZ-2u-1 | 小范围内网试运行执行模板包 | 已完成文档层准备；新增样例登记、问题反馈、每日检查和验收记录模板，正式试运行仍未启动，见 `docs/biz-2u-1-internal-trial-execution-templates.md` |
| BIZ-2u-2 | 小范围内网试运行启动前登记与检查包 | 已完成文档层准备；5 条 `accepted_risk` 已登记为已知风险，启动前检查材料已形成，正式试运行仍未启动，见 `docs/biz-2u-2-internal-trial-readiness-check.md` |
| BIZ-2v-1 | 报价下发与成本价权限安全加固 | 已通过当前环境手工验收（2026-05-28）；`confirm_push` 校验任务归属，普通 staff 候选查询受限，active 启用需核定原因，见 `docs/biz-2v-1-quote-push-permission-hardening.md` |
| BIZ-2v-2 | RAG 同步一致性与滞后提示 | 已通过当前环境手工验收（2026-05-28）；修正失败同步数量口径，新增 RAG 同步状态摘要和成本库页面提示，见 `docs/biz-2v-2-rag-sync-consistency.md` |
| BIZ-2v-3 | 成本库敏感操作审计与导出控制 | 已通过当前环境手工验收（2026-05-28）；新增成本库审计表、导出权限控制和审计记录查询，见 `docs/biz-2v-3-cost-audit-export-control.md` |
| BIZ-2w-1 | 系统完善审查与账号入口安全加固 | 已通过当前环境手工验收（2026-05-28）；默认关闭自注册，新增 `system_admin` 新建用户入口，见 `docs/biz-2w-1-system-risk-hardening.md` |
| BIZ-2w-2 | RAG 同步状态时间口径误报修复 | 已通过当前环境手工验收（2026-05-28）；当前真实环境状态已恢复为 `synced / 已同步`，验收需求单闭环通过，见 `docs/biz-2w-2-rag-sync-status-timezone-fix.md` |
| BIZ-2w-3 | 成本参考优先与 AI 改写防护 | 已完成代码层验证，待当前环境手工验收（2026-05-28）；原始需求命中成本项优先于 AI 返回项目名，AI 改写成本依据时提示并阻断下发，见 `docs/biz-2w-3-cost-reference-priority-ai-rewrite-guard.md` |
| BIZ-2w-4 | AI 备注与成本依据一致性校验 | 已通过当前环境手工验收（2026-05-29）；成本依据已命中但 AI 原始备注声称无数据/无法报价时，预审可见备注替换为系统建议备注并要求人工确认，见 `docs/biz-2w-4-ai-note-cost-basis-consistency.md` |
| BIZ-2w-5 | 自由文本同名不同规格拆行与工程量识别修复 | 已完成代码层验证，待当前环境手工验收（2026-05-29）；手输同名不同规格需求可按顿号拆行并正确识别 `mm` 规格与 `㎡` 工程量，见 `docs/biz-2w-5-text-multi-spec-quantity-guard.md` |
| BIZ-2w-6 | 报价来源口径与预审列名调整 | 已通过当前环境手工验收（2026-05-29）；预审展示明确成本库依据优先、AI 仅无底价估价、人工确认下发，并补充 AI 占位项目名回填原始需求行保护，见 `docs/biz-2w-6-quote-source-wording.md`、`docs/biz-2w-6-placeholder-project-name-hotfix.md` |
| BIZ-2 成本价权限后续 | 导出控制、二次验证和高风险审计增强 | 草案已完成；见 `docs/biz-2-cost-price-permissions-draft.md`，待业务确认后继续 |

**第三阶段（第二阶段稳定后）**

| 子项 | 内容 | 状态 |
|------|------|------|
| BIZ-3a/BIZ-3b | 企业内部工程项目进度中台 | 已推进至 BIZ-3b-3-2（2026-06-02）；A 级硬门禁与放行事件完成并通过当前环境业务验收，当前库 `complete_required=8`，B 级节点暂不自动纳入 |
| BIZ-3c | 经营驾驶舱轻量 MVP | BIZ-3c-1 后端只读聚合接口、BIZ-3c-2 `/admin/dashboard` 经营总览指标卡 + 风险列表、BIZ-3c-3 趋势/分布和硬门禁放行后缺证据精确风险均已完成代码层验证；下一步进入小范围试运行观察 |
| BIZ-3d | 完整经营驾驶舱 | 合同/回款/成本/毛利经营指标汇总暂缓；待数据口径稳定后再启动 |

**前端体验专项（小范围试运行前补强）**

| 子项 | 内容 | 状态 |
|------|------|------|
| FE-UX-1-0 | 前端体验重构一期规划 | 已完成；见 `AI_Middle_Office/docs/fe-ux-1-admin-experience-refactor-planning.md` |
| FE-UX-1-1 | 应用外壳与视觉基础 | 已通过人工验收；已统一 Vite 管理台视觉 token、侧边栏分组、内容区、指标卡、状态面板和通用 toolbar/filter/action/tag/empty 类 |
| FE-UX-1-2 | 报价工作台流程优化 | 已通过人工验收；旧报价工作台已新增顶部流程面板、底部固定操作区和任务恢复下一步提示卡；脚本解析、9000 `/index.html`、`npm.cmd run build` 通过 |
| FE-UX-1-3 | AI 预审弹窗重构 | 已通过人工验收；已新增顶部指挥区、摘要卡、阻断与复核面板、逐行表格导读和底部状态操作区；不改 `/confirm_push` 阻断、价格口径、成本依据、草稿或推送规则 |
| FE-UX-1-4 | 成本库高级表格优化 | 已通过人工验收；已新增成本库维护工作台摘要、当前筛选/状态/RAG/选择概览和独立批量操作条；不改成本库接口、权限、active 规则、价格口径、RAG 同步或审计 |
| FE-UX-1-5 | 经营总览与项目进度体验补强 | 已通过人工验收；已新增经营总览试运行读数条、项目列表概览、我的任务概览和项目详情焦点条；保持运营工作台风格，不做炫酷大屏 |
| FE-UX-1-6 | 试运行体验验收包 | 已完成；见 `AI_Middle_Office/docs/fe-ux-1-trial-experience-acceptance.md`，覆盖页面优先级、角色清单、核心链路、窄屏检查和问题登记模板 |
| FE-UX-2-1 | Apple-like 正式视觉增强第一版 | 已完成并通过人工视觉验收；见 `AI_Middle_Office/docs/fe-ux-2-apple-like-visual-upgrade.md`，已统一 Vite 管理台与旧报价工作台浅色正式视觉底座，不套模板、不换 UI 库、不改业务逻辑 |

**暂缓评估（触发条件明确后再启动）**

| 子项 | 触发条件 |
|------|---------|
| BIZ-1d 外部项目源自动筛选与联系方式获取 | 外部项目搜索引擎或项目收集平台 API/数据通道确认可用；公司内部项目筛选标准完成结构化；样本试跑证明能稳定筛出符合公司标准且有联系方式的项目 |
| Phase 4b 语音转写 | 会议纪要 Phase 4a 已在真实业务中稳定使用，且有明确转写需求 |
| Phase 4c 钉钉会议导入 | 同上，且钉钉 API 授权已申请 |
| P5 LangGraph | 自适应多轮检索、主动澄清或多模型状态机有真实触发需求时 |

## 业务优化路线（2026-05-07）

### 状态总览

| 状态 | 事项 | 后续处理 |
|------|------|----------|
| 已完成 | 后端基础设施 P0-P3、补充一致性优化、运维告警收敛 | 冻结，后续只按真实问题增量维护。 |
| 已完成 | 前端优化 P0-P3 | 冻结，后续只按真实体验问题调整。 |
| 已完成 | 业务优化 P0-P4 | 已完成到代码层，真实运行验收随下一轮报价继续确认。 |
| 未完成 | P2 候选 prompt 自动重跑 | 等样本量、Dify 调用路径和成本策略明确后再做。 |
| 已完成 | P3 Admin 知识候选审核面板 | 已接入 admin.html 并完成手工验收（2026-05-08）。 |
| 已完成 | P4 真实用户体验运行态手工验收 | 2026-05-08 手工验收通过，22/22 项全部通过，详见 `AI_Middle_Office/P4_ACCEPTANCE.md`。 |
| 暂不启动 | P5 LangGraph | 达到触发条件后再评估。 |
| 已完成 | AI 平台升级 Phase 0：Vite 壳 + 登录鉴权统一 + RBAC | 2026-05-18 开发与当前环境验证完成；旧页面保留；正式生产准备推迟到系统整体完善后统一处理。 |
| 已完成当前环境运行态验收 | AI 平台升级 Phase 1：报价速度看板 | 已新增报价速度聚合接口、功能开关和 Vite 看板视图；已修复 AI 生成耗时口径并回填历史成功任务；页面、看板数据和新增真实报价统计均正常；正式生产准备推迟到系统整体完善后统一处理。 |
| 已完成当前环境运行态验收 | AI 平台升级 Phase 2：响应速度追踪 | 已新增咨询记录模型、报价任务关联、咨询查询/修正接口、响应速度聚合和 Vite 响应速度视图；当前环境 Alembic 已升级到 `20260514_0012 (head)`，内网 smoke 样本已进入响应看板，平均首次响应 15 分钟，Celery worker 元数据加载问题已修复；正式生产准备推迟到系统整体完善后统一处理。 |
| 已完成当前环境验证 | AI 平台升级 Phase 2.5：管理员报价运营闭环 | 已在 Vite 管理台新增“报价运营”标签页，串联报价任务、客户咨询、确认报价和钉钉推送状态；后端报价任务列表补充客户/历史摘要和筛选能力；提交人独立列、筛选和任务详情已验证；不新增数据库结构，不启动 Phase 3。 |
| 已完成当前环境验证 | AI 平台升级 Phase 3：执行速度追踪 | 已新增执行任务 schema、任务接口、事件记录、执行速度聚合和 Vite 执行任务页面；当前环境已打开 `FEATURE_EXECUTION` / `FEATURE_DASHBOARD_EXECUTION` 并完成任务创建、开始、完成、取消、详情事件和执行速度看板验证，执行趋势已显示取消数量。 |
| 已完成当前环境运行态验收 | AI 平台升级 Phase 4a：手动会议纪要 + 草稿确认 | 已新增 `meeting_notes` / `task_drafts` / `meeting_note_revisions`、会议接口和 Vite 会议纪要标签页；当前环境已打开 `FEATURE_MEETING_AI=true` 且 `PUBLIC_ACCESS_ENABLED=false`，smoke 已验证草稿确认写入 `execution_tasks`、AI 未提取时人工补充后作废和 revision 补充任务；当前不启动 Phase 4b/4c/6，不迁移旧 HTML。 |
| 已完成当前环境验证 | AI 平台升级 BIZ Track BIZ-1a：商务台账 v1 | 已新增 Alembic `20260520_0016`、`FEATURE_BUSINESS_LEDGER`、商务台账 API/service/schema、`client_inquiry_events` 审计表、Vite `/admin/business-ledger` 页面和 smoke 脚本；当前环境 smoke 已验证 2 条 outbound 创建、1 条作废、5 条事件、XFF=`1.2.3.4`、Phase 2 列表隔离和 response-speed 样本 `17 / 9` 保持不变。 |
| 已完成当前环境验证 | AI 平台升级 BIZ Track BIZ-2a：成本数据库初始化 | 已新增 Alembic `20260520_0017` / `20260520_0018`、`FEATURE_COST_DB`、`cost_items` / `cost_item_history`、成本库 API/service/schema、Excel 导入预览/确认、Vite `/admin/cost-db` 页面和 smoke 脚本；当前环境 smoke 已验证权限、价格历史、状态机、旧 Excel 解析 191 条、拆分价映射和重复导入幂等。 |

- [x] **P0 报价反馈闭环 + 版本追踪**：新增 `quote_feedback`、`quote_corrections`、`quote_rag_traces`，在异步报价预审完成时记录 AI 初稿，在 `/confirm_push` 成功后记录最终人工确认稿和字段差异，在预审打回时记录 rejection；新增 `DIFY_APP_VERSION`、`DIFY_WORKFLOW_VERSION`、`DIFY_PROMPT_VERSION`、`DIFY_RELEASE_ID`、`RAG_COLLECTION_ALIAS` 配置。
- [x] **P0 前端上下文传递**：`index.html` 在预审弹窗保存 `quote_job_id` / `trace_id`，确认推送时带回后端，打回重填时调用 `/api/v1/quote/feedback/reject` 做轻量记录。
- [x] **P0 测试覆盖**：新增 `tests/test_quote_feedback.py`，覆盖确认推送后的反馈、修正、RAG trace 记录，以及人工打回记录。
- [x] **P0 验证结果**：`python -m compileall app` 通过，`python -m pytest` 为 `59 passed`；仍有已知 `.pytest_cache` 权限 warning，不影响测试结果。
- [x] **P1 Admin 反馈分析页面**：新增 admin summary/list/detail 接口和 `admin.html` 反馈分析模块，展示最近报价数量、确认/打回、平均总价偏差、字段修正、RAG trace、prompt 版本表现和高频召回知识条目。
- [x] **P1 验证结果**：`python -m compileall app` 通过，`python -m pytest` 为 `60 passed`；`node --check static/js/admin/feedback.js` 通过。
- [x] **P2 验证结果**：`python -m compileall app` 通过，`python -m alembic heads` 显示 `20260507_0005 (head)`，`python -m pytest` 为 `62 passed`。
- [x] **P2 Prompt 回归评测**：新增 `prompt_regression_cases` / `prompt_regression_runs`，可从真实反馈固化黄金案例，并生成按 prompt 版本统计的总价偏差、明细遗漏率、格式错误率、打回率、人工修改率和综合分。
- [x] **P3 知识库质量治理**：新增 `knowledge_candidates`，可从真实反馈、字段级修正和打回原因生成候选；旧 admin approve 写入 `materials` 已退役并返回 410，后续有效候选需转入成本数据库 draft；新增 RAG trace 聚合洞察接口。
- [x] **P3 验证结果**：`python -m compileall app` 通过，`python -m alembic heads` 显示 `20260507_0006 (head)`，`python -m pytest` 为 `64 passed`。
- [x] **P4 真实用户体验优化**：失败可操作原因、预审高风险标记、人工修改一键沉淀知识入口。
- [x] **P4 验收清单**：新增 `AI_Middle_Office/P4_ACCEPTANCE.md`，包含前置确认、金路径、打回路径、失败提示、trace_id 一致性 5 个章节共 22 项检查点。
- [x] **P4 运行态手工验收**：2026-05-08 完成，22/22 项全部通过，见 `AI_Middle_Office/P4_ACCEPTANCE.md` 验收记录。
- [x] **AI 平台升级 Phase 0 开发与当前环境验证**：2026-05-18 完成，Alembic `20260514_0011 (head)`，当前环境 `FEATURE_VITE_FRONTEND=true`，`PUBLIC_ACCESS_ENABLED=false`；`/login`、`/admin/permissions`、`/admin/dashboard`、旧 HTML 入口均冒烟通过。正式生产上线尚未发生。
- [x] **AI 平台升级 Phase 1 报价速度看板当前环境运行态验收完成**：2026-05-18 完成，新增 `FEATURE_DASHBOARD_QUOTE`、`GET /api/v1/admin/dashboard/quote-speed`、`/admin/dashboard` 报价速度视图和自动化测试；`quote_job_runner` 已改为记录单次运行实测耗时，历史 121 条成功任务的 `duration_ms=0` 已在 `C:\AI_Backups\20260517_205242\db\ai_quotation.sql` 备份后完成回填；`python -m compileall app scripts`、`python -m pytest`（85 passed）和 `npm.cmd run build` 均通过；用户手工确认 `/admin/dashboard` 页面无 404 / 403 / 控制台接口错误，三类耗时显示正常，新增真实报价任务已进入统计且 AI 生成耗时大于 0；旧 `index.html` / `admin.html` 业务功能未迁移，正式生产准备推迟到系统整体完善后统一处理。
- [x] **AI 平台升级 Phase 2 响应速度追踪当前环境运行态验收完成**：2026-05-18 完成，新增 Alembic `20260514_0012`、`client_inquiries`、`quote_jobs.client_inquiry_id`、`FEATURE_CLIENT_INQUIRY`、`FEATURE_DASHBOARD_RESPONSE`、`GET/PATCH /api/v1/client-inquiries`、`GET /api/v1/admin/dashboard/response-speed` 和 Vite 驾驶舱“响应速度”标签页；默认时间样本仅计数量不计平均响应耗时；当前环境 Alembic 已升级到 `20260514_0012 (head)`，功能开关已打开且 `PUBLIC_ACCESS_ENABLED=false`；内网 smoke 已创建带 `inquiry_time` 的报价任务并在看板展示 1 条可信样本，平均首次响应 15 分钟、SLA 达标率 100%；已修复 Celery worker 单独运行时未加载 `client_inquiries` 元数据导致任务卡在 `queued` 的问题，并新增 `scripts/phase2_response_smoke.ps1` 固化中国时区造数方式；`python -m alembic heads` 显示 `20260514_0012 (head)`，`python -m compileall app scripts`、`python -m pytest`（86 passed）和 `npm.cmd run build` 均通过。
- [x] **AI 平台升级 Phase 2.5 管理员报价运营闭环当前环境验证完成**：2026-05-18 完成，复用现有 `quote_jobs`、`client_inquiries`、`quote_history`，在 `/admin/dashboard` 新增“报价运营”标签页；当前环境已验证提交人列、筛选、任务详情和页面显示正常；该阶段已封板，不新增数据库结构，不启动 Phase 3。
- [x] **AI 平台升级 Phase 3 执行速度追踪当前环境验证完成**：2026-05-19 完成，新增 Alembic `20260514_0013`、`execution_tasks`、`execution_task_events`、`FEATURE_EXECUTION`、`FEATURE_DASHBOARD_EXECUTION`、执行任务 CRUD/取消接口、执行速度聚合接口、`/admin/execution` 和 `/admin/dashboard` 执行速度标签页；当前环境已打开开关并验证任务创建、开始、完成、取消、详情事件和执行速度看板，执行趋势已显示取消数量；`python -m compileall app`、`python -m pytest`（92 passed）、`python -m pytest tests\test_execution_tasks_phase3.py` 和 `npm.cmd run build` 均通过。
- [x] **AI 平台升级 Phase 4a 手动会议纪要 + 草稿确认当前环境运行态验收完成**：2026-05-19 完成，新增 Alembic `20260514_0014` / `20260514_0015`、`meeting_notes`、`task_drafts`、`meeting_note_revisions`、`FEATURE_MEETING_AI`、会议纪要创建/查询/更正/取消/草稿补充/草稿确认接口和 Vite `/admin/execution`“会议纪要”标签页；当前环境已打开 `FEATURE_MEETING_AI=true` 且 `PUBLIC_ACCESS_ENABLED=false`，`scripts/phase4a_meeting_smoke.ps1` 已验证自动提取并确认任务、人工补充后作废、revision 补充任务和 `/admin/execution` 200；`FEATURE_AUDIO_TRANSCRIPTION` 仅预留开关，Phase 4b/4c/6 未启动，旧 `index.html` / `admin.html` 未迁移；`python -m alembic current` 显示 `20260514_0015 (head)`，`python -m compileall app scripts`、`python -m pytest`（96 passed）和 `npm.cmd run build` 均通过。
- [x] **AI 平台升级 BIZ Track BIZ-1a 商务台账 v1 当前环境验证完成**：2026-05-20 完成，新增 Alembic `20260520_0016`、`FEATURE_BUSINESS_LEDGER`、`client_inquiries.direction/stage/next_followup_at/cancelled_*`、`client_inquiry_events`、5 个 `/api/v1/business-ledger` 接口、Vite `/admin/business-ledger` 页面和 `scripts/biz1a_business_ledger_smoke.ps1`；`python -m pytest` 为 `124 passed`，`npm.cmd run build` 通过，当前 9000 环境 smoke 输出 `created=2 cancelled=1 feature_flag=true`、`event_count=5`、XFF 审计 `ip_address=1.2.3.4`，并确认 outbound 不污染 Phase 2 `client-inquiries` 与 response-speed 样本。
- [x] **AI 平台升级 BIZ Track BIZ-2a 成本数据库初始化当前环境验证完成**：2026-05-20 完成，新增 Alembic `20260520_0017` / `20260520_0018`、`FEATURE_COST_DB`、`cost_items`、`cost_item_history`、8 个 `/api/v1/admin/cost-items*` 接口、Vite `/admin/cost-db` 页面和 `scripts/biz2a_cost_db_smoke.ps1`；当前环境 `python -m alembic current` 显示 `20260520_0018 (head)`，`python -m compileall app scripts` 通过，`python -m pytest` 为 `131 passed`，`cmd /c npm.cmd run build` 通过；runtime smoke 覆盖 admin/staff 权限、价格历史、状态机、人工/主材/辅材拆分价映射和重复 confirm 幂等。
- [ ] **P5 LangGraph 触发项**：仅在需要自适应多轮检索、主动澄清、多模型状态机或替换 N8N 核心编排时再评估。

## 高优先级

### ~~1. N8N conversationId 硬编码~~（✅ 2026-04-21 已完成）
- **位置**：`AI_Middle_Office/app/api/v1/chat.py`
- **修复**：改为每次请求生成 `str(uuid.uuid4())`，彻底隔离多用户上下文

### ~~2. N8N Webhook 鉴权不统一~~（✅ 2026-04-26 已兼容升级）
- **修复**：FastAPI 保留 `X-Webhook-Secret` 以兼容现有 N8N 校验，同时新增 `X-Webhook-Signature`
- **签名算法**：`HMAC-SHA256(WEBHOOK_SECRET, canonical_json_body)`，其中 `canonical_json_body` 使用 key 排序和紧凑 JSON
- **N8N 侧**：现有 workflow 可继续校验 `X-Webhook-Secret`；后续可平滑升级为校验 `X-Webhook-Signature`
- **密钥存储**：`AI_Middle_Office/.env` 的 `WEBHOOK_SECRET` 字段

### ~~3. 用户配额无管理界面~~（✅ 2026-04-21 已完成）
- **后端**：`chat.py` 新增 `GET /admin/users` 和 `PATCH /admin/users/{id}/quota` 两个接口
- **前端**：`admin.html` 顶部新增员工账号 & 额度管理面板，支持直接在表格内输入新额度并一键确认

### ~~10. 第一轮稳定性升级~~（✅ 2026-04-25 已完成）
- **配置治理**：新增集中配置读取，N8N/RAG/JWT/数据库/代理/CORS/物料库路径统一走 `.env`
- **可观测性**：新增 JSON 结构化日志、请求级 `trace_id`、`X-Trace-Id` 响应头
- **健康检查**：新增 `/health/live` 与 `/health/ready`
- **登录态**：`app.html` 刷新时通过 `/api/v1/auth/me` 校验 Token，退出时清理完整本地状态
- **错误提示**：`index.html` 报价失败展示后端错误详情与追踪 ID，便于排查

### ~~11. 钉钉 Excel 文件名与表头乱码~~（✅ 2026-04-26 已完成并验证）
- **后端修复**：`confirm_push` 推送前生成 ASCII 安全文件名，例如 `quote_20260426_142030_admin.xlsx`
- **兼容字段**：同时下发 `excel_filename`、`download_filename`、`filename`、`fileName`、`file_name`、`attachment_name`
- **N8N 修复**：`Convert to File` 使用 Webhook 中的安全文件名，最后发送文件消息节点修复 `msgParam` JSON Body
- **Excel 表头**：`Code in JavaScript1` 已将乱码表头修复为 `施工项目`、`AI核准单价(元)`、`项目合计(元)`、`工艺备注`
- **验证结果**：钉钉 markdown 报价单、Excel 附件、文件名、表头均已验证通过
- **备份文件**：`n8n_budget_push_fixed.json`

### ~~12. 后端自动化测试与 CI 基线~~（✅ 2026-04-26 已落地）
- **测试框架**：新增 `pytest.ini`、`requirements-dev.txt` 与 `AI_Middle_Office/tests/`
- **覆盖范围**：健康检查、登录/鉴权、报价文件名兼容字段、Webhook HMAC 签名
- **隔离策略**：测试强制使用本地 SQLite 测试库，不访问真实 MySQL、N8N、RAG 或外部模型
- **CI**：新增 `.github/workflows/backend-ci.yml`，在 push/PR 时执行依赖安装、`compileall` 和 `pytest`

### ~~13. 报价任务异步化基线~~（✅ 2026-04-26 已落地）
- **任务模型**：新增 `QuoteJob` 表，持久化 job_id、状态、阶段、trace_id、事件流、结果和错误信息
- **接口**：新增 `POST /api/v1/quote/jobs`、`GET /api/v1/quote/jobs/{job_id}`、`GET /api/v1/quote/jobs/{job_id}/events`
- **调度层**：新增 `TASK_QUEUE_MODE`，支持 `local` 本地后台线程、`celery` Celery+Redis、`disabled` 测试隔离模式
- **Worker 入口**：新增 `app/tasks/celery_app.py` 与 `app/tasks/quote_tasks.py`，生产可通过 Celery worker 消费报价任务
- **前端切换**：`index.html` 已改为先创建异步任务，再通过任务事件流订阅进度和预审结果
- **兼容策略**：保留原 `/api/v1/chat` SSE 接口，作为回退兼容入口
- **测试覆盖**：新增异步任务创建、状态查询、鉴权测试；本地验证 `9 passed`

### ~~14. Redis/Celery 正式生产化部署配置~~（✅ 2026-04-27 已落地）
- **Redis 编排**：`rag_docker/docker-compose.yml` 新增 `quote-redis` 服务，宿主机暴露 `6380`，开启 AOF 持久化、内存上限和日志轮转
- **Worker 启动**：新增 `start_celery_worker.ps1`，使用 `--pool=solo --concurrency=1` 适配 Windows
- **开机自启**：新增 `install_celery_worker_service.ps1`，注册 `AI_MiddleOffice_CeleryWorker` 任务计划程序
- **健康检查**：`/health/ready` 新增 `task_queue` 状态，Celery 模式检查 Redis broker 与 worker ping
- **部署手册**：新增 `AI_Middle_Office/DEPLOY_CELERY.md`
- **保守切换**：`.env.example` 保持 `TASK_QUEUE_MODE=local` 默认值，生产部署时手动改为 `celery`

### ~~15. 报价任务管理增强~~（✅ 2026-04-27 已落地）
- **任务列表**：新增 `GET /api/v1/quote/jobs`，普通用户只看本人任务，admin 可按 `username` / `status` 查看全队列
- **任务取消**：新增 `POST /api/v1/quote/jobs/{job_id}/cancel`，支持取消 queued/running 任务，并尝试 revoke Celery task
- **失败重试**：新增 `POST /api/v1/quote/jobs/{job_id}/retry`，失败、取消、超时任务可重新创建并派发
- **超时标记**：新增 `POST /api/v1/admin/quote/jobs/mark_timeouts`，管理员可批量将长时间 queued/running 任务标记为 `timed_out`
- **管理界面**：`admin.html` 新增报价任务队列面板，支持筛选、刷新、取消、重试和超时标记
- **执行器保护**：Worker 执行过程中会检查终态，避免已取消/超时任务继续落预审结果
- **测试覆盖**：新增列表、取消、重试、管理员超时标记测试

### ~~16. 模型调用统一网关~~（✅ 2026-04-27 已落地）
- **统一入口**：新增 `app/services/model_gateway.py`，GLM-4V 图像识别与 n8n/Dify/DeepSeek 工作流调用统一经过网关
- **调用日志**：新增 `ModelCallLog` 表，记录 provider、model、endpoint、状态、HTTP 状态、耗时、输入/输出字符、估算费用和 trace_id
- **熔断保护**：按 `provider:endpoint` 做内存级失败计数，超过阈值后短时间熔断，避免连续打爆外部模型/工作流
- **统计接口**：新增 `GET /api/v1/admin/model_gateway/stats` 与 `/admin/model_gateway/circuits`
- **管理界面**：`admin.html` 新增模型网关观测面板，展示调用次数、平均耗时、估算费用和熔断状态
- **测试覆盖**：新增模型网关统计和管理员权限测试

### ~~17. 知识库变更快照与回滚基础版~~（✅ 2026-04-27 已落地）
- **自动快照**：`POST /api/v1/admin/materials` 保存覆盖材料库前，自动将旧版 `rag_materials.json` 写入 `materials_audit/`
- **审计列表**：新增 `GET /api/v1/admin/materials/audit`，管理员可查看快照时间、操作者、动作和条目数
- **一键回滚**：新增 `POST /api/v1/admin/materials/rollback/{snapshot_id}`，回滚前会再次自动备份当前版本
- **管理界面**：`admin.html` 左侧新增“知识库变更快照”面板，支持刷新快照和回滚
- **测试覆盖**：新增 `tests/test_material_audit.py`，覆盖保存生成快照、列表读取和回滚恢复

### ~~18. MinIO 文件存储与临时下载链接~~（✅ 2026-04-27 已落地）
- **独立对象存储**：`rag_docker/docker-compose.yml` 新增 `quote-minio`，宿主机端口 `9002/9003`，避免与 Milvus/RAGFlow MinIO 冲突
- **后端存储服务**：新增 `app/services/file_storage.py`，封装 bucket 初始化、对象上传、临时下载链接和健康检查
- **文件元数据**：新增 `FileObject` 表，记录 file_id、上传用户、用途、bucket、object_name、原始文件名、类型、大小和上传时间
- **文件接口**：新增 `POST /api/v1/files`、`GET /api/v1/files`、`GET /api/v1/files/{file_id}/download_url`
- **健康检查**：新增 `GET /api/v1/admin/files/storage/health`
- **管理界面**：`admin.html` 新增 MinIO 文件存储面板，支持检测、上传、刷新列表和生成临时下载链接
- **部署手册**：新增 `AI_Middle_Office/DEPLOY_MINIO.md`
- **测试覆盖**：新增 `tests/test_file_storage.py`，覆盖上传元数据落库、列表查询和临时链接生成

### ~~19. 报价任务附件接入 MinIO~~（✅ 2026-04-27 已落地）
- **任务轻量化**：`POST /api/v1/quote/jobs` 在 `MINIO_ENABLED=true` 时会先将上传附件写入 MinIO，只在 `QuoteJob.file_object_id` 保存引用
- **兼容回退**：MinIO 未启用时继续使用原 `file_base64` 存储逻辑，保证本地和测试环境不受影响
- **执行器读取**：Worker 执行报价任务时优先根据 `file_object_id` 从 MinIO 拉取附件 bytes，再交给 GLM-4V 识别
- **失败保护**：附件从 MinIO 读取失败时，任务标记为 `failed`，阶段为 `file_load`，事件流返回明确错误
- **启动迁移**：`main.py` 增加 `quote_jobs.file_object_id` 保守迁移，兼容已有 MySQL 表
- **测试覆盖**：`tests/test_file_storage.py` 新增 MinIO 开启时创建报价任务的附件引用测试

---

### ~~20. 一键启动与自愈编排~~（✅ 2026-04-28 已落地）
- **CentOS 网络自愈**：新增 `rag_docker/enable_centos_autostart.sh`，将 `ens33` 配置为 DHCP + 开机自动连接，并启用 Docker/n8n/Compose 服务自恢复
- **远程安装脚本**：新增 `AI_Middle_Office/install_centos_autostart.ps1`，从 Windows 一键上传并执行 CentOS 自愈脚本，支持 SSH passphrase 交互
- **Windows 一键启动**：新增 `AI_Middle_Office/start_all.ps1`，启动前等待 MySQL、Redis、RAG、n8n、MinIO 端口就绪，再拉起 Celery Worker 和 FastAPI
- **开机任务升级**：`install_service.ps1` 改为注册 `start_watchdog.ps1`，开机后每 3 分钟重试一次启动编排，最多持续 60 分钟
- **数据库启动保护**：FastAPI 启动时会等待 MySQL 可用后再建表和执行启动迁移，降低冷启动 MySQL 尚未 ready 导致崩溃的风险
- **部署文档**：新增 `AI_Middle_Office/DEPLOY_STARTUP.md`，记录 CentOS 和 Windows 冷启动/自启动验证流程

### ~~21. 运维监控与告警~~（✅ 2026-04-28 已落地）
- **统一监控接口**：新增 `GET /api/v1/admin/ops/dashboard`，聚合基础服务探活、卡住任务、异常日志和告警列表
- **基础服务探活**：新增 MySQL、Redis、Celery、RAG、MinIO、n8n 状态检查，支持独立接口 `/admin/ops/services`
- **异常日志聚合**：新增 `/admin/ops/logs`，扫描 `AI_Middle_Office/logs/*.log` 最近日志，聚合 ERROR、Traceback、数据库断连和任务崩溃线索
- **卡住任务提醒**：新增 `/admin/ops/jobs`，按阈值统计 queued/running 长时间未更新任务，并在 dashboard 中生成提醒
- **管理员面板**：`admin.html` 顶部新增“运维监控与告警”面板，展示服务状态、卡住任务、异常日志，并每 60 秒自动刷新提醒
- **部署文档**：新增 `AI_Middle_Office/DEPLOY_OPS.md`，记录探活范围、接口和可配置项
- **测试覆盖**：新增 `tests/test_ops_monitor.py`，覆盖管理员权限、dashboard 结构和卡住任务提醒

### ~~22. 数据库迁移治理 Alembic 化~~（✅ 2026-04-28 已落地）
- **迁移框架**：新增 `AI_Middle_Office/alembic.ini`、`alembic/env.py`、`alembic/script.py.mako` 和 `alembic/versions/`
- **基线迁移**：新增 `20260428_0001_initial_schema`，覆盖 `users`、`quote_history`、`quote_jobs`、`model_call_logs`、`file_objects`，并兼容已有 MySQL 表只补缺失字段
- **启动编排接入**：`start_all.ps1` 在启动 FastAPI 前自动执行 `alembic upgrade head`，并提供 `-SkipMigrations` 临时跳过参数
- **兼容开关**：新增 `AUTO_CREATE_TABLES`、`STARTUP_COMPAT_MIGRATIONS`、`AUTO_RUN_DB_MIGRATIONS`，生产稳定后可关闭启动时建表和保守补列
- **迁移脚本**：新增 `upgrade_database.ps1`，支持手动执行数据库升级
- **部署文档**：新增 `AI_Middle_Office/DEPLOY_DB_MIGRATIONS.md`，记录安装依赖、执行迁移、版本查看和新增 revision 流程

### ~~后端重构 P0：chat.py 拆分 + lifespan 启动治理 + 统一鉴权依赖~~（✅ 2026-05-06 已完成）
- **chat.py 拆分**：643 行 God File 拆为 `quote.py`（201行）、`materials.py`（410行）、`history.py`（47行）、`users.py`（41行）；辅助逻辑提取至 `services/excel_service.py` 和 `services/quote_helpers.py`；chat.py 保留兼容层（72行），所有 API 路径不变
- **lifespan 启动治理**：`_wait_for_database_ready`、`_run_schema_compat_migrations`、`_mark_default_admin_password` 合并为 `_run_startup_database_tasks()`，通过 `asyncio.to_thread()` 在 `lifespan` 内执行，消除模块级副作用
- **统一鉴权依赖**：新增 `app/dependencies.py`，`get_current_user` / `require_admin` 统一维护，所有路由模块从此导入

### ~~后端重构 P1：confirm_push schema + 物料库入库~~（✅ 2026-05-06 已完成）
- **Pydantic schema**：新增 `app/schemas/quote.py`，`confirm_push` 请求体字段类型与必填约束明确，消除裸 `dict` 传递
- **物料库入库**：新增 `app/models/material.py`，知识库条目持久化至 MySQL `materials` 表
- **旧 JSON 兼容**：`LEGACY_MATERIALS_FILE` / `MATERIALS_FILE` 不再自动导入旧 `rag_materials.json`，RAG 评测报告目录独立为 `RAG_EVAL_REPORT_DIR`

### ~~后端重构 P2：requests → httpx~~（✅ 2026-05-06 已完成）
- 所有对外 HTTP 调用（`quote.py`、`materials.py`、`rag_eval.py` 等）改为 `httpx.AsyncClient`，消除异步路由中的同步阻塞

### ~~后端重构 P3：统一响应格式 + 前端收口~~（✅ 2026-05-06 已完成）
- **统一响应**：新增 `app/core/responses.py`，`api_ok` / `api_page` 统一所有 REST 接口返回 `{"code": 200, "message": "ok", "data": ...}`
- **前端收口**：`index.html`、`admin.html`、`app.html` 统一通过 `res.data` 读取响应，与后端格式一一对应

### ~~后端重构收尾：配置语义清理 + 验收闭环~~（✅ 2026-05-06 已完成）
- **配置清理**：`MATERIALS_FILE` 保留为兼容 alias，新增 `LEGACY_MATERIALS_FILE` 明确旧 JSON 导入语义，新增 `RAG_EVAL_REPORT_DIR` 独立控制评测报告目录
- **运行态验收**：FastAPI `/health/ready`、Celery worker、RAG 服务均已确认 ready；旧 `rag_materials.json` 曾导入数据库 70 条，后续在 BIZ-2c 收敛中已退役并清空。
- **测试闭环**：`python -m compileall app` 通过，`python -m pytest` 为 `55 passed`
- **Git 状态**：最新后端重构收尾提交 `6e54c09 clarify legacy materials config` 已推送到 GitHub `main`

### 后端补充优化路线（进行中，2026-05-06）
> 本轮处理 P0-P3 后遗留的一致性与低阻塞风险；每完成一项后在本清单打勾，并补充验证结果。

- [x] **B1 模型网关日志异步化**：为 `record_model_call` 增加 async 包装，`call_glm_vision_extract` / `post_json_via_gateway` 中的 DB 写入改为 `asyncio.to_thread`，避免 async 调用链被 MySQL commit 阻塞。验证：`pytest tests\test_model_gateway.py` 为 `5 passed`。
- [x] **B2 运维监控阻塞治理 + httpx 统一**：`ops.py` 的 async 路由通过 `asyncio.to_thread` 调用同步探活/日志聚合；`ops_monitor.py` 中 HTTP 探活和钉钉告警从 `urllib` 统一到 `httpx`。验证：`pytest tests\test_ops_monitor.py` 为 `4 passed`。
- [x] **B3 响应冗余字段收口**：优先清理 `quote_jobs.py` 的 `api_ok(data, **data)` 为 `api_ok(data)`；`auth.py` 顶层 token / 用户字段因 PowerShell 运维脚本兼容需求暂保留。验证：`pytest tests\test_quote_jobs.py tests\test_file_storage.py tests\test_api_response_format.py` 为 `13 passed`。
- [x] **B4 兼容别名说明**：保留 `DATA_FILE = LEGACY_DATA_FILE` 作为 `chat.py` 旧 import 兼容 alias，并补充代码注释，避免后续误删。
- [x] **B5 低风险项观察**：`/health/ready` 保持同步 `def` 路由；SSE 每秒短 session 轮询暂不调整，等并发压力或连接池指标证明有必要再改。

---

## 中优先级

### ~~4. 报价历史记录~~（✅ 2026-04-21 已完成）
- **后端**：新增 `QuoteHistory` 表（id/username/created_at/total_amount/item_count/payload_json），confirm_push 成功后自动写入
- **接口**：`GET /api/v1/history`，普通用户查自己，admin 可按 username 筛选全员
- **前端**：`index.html` 输入区新增"历史记录"按钮，点击弹出右侧抽屉展示历史列表，支持翻页和查看明细

### ~~5. RAG 检索效果评估~~（✅ 2026-04-21 已完成）
- **脚本**：`eval_rag.py`，30 条人工标注测试集（4 个难度级别：直接命名/同义口语/多意图/描述性）
- **指标**：Hit@K（命中率）+ MRR（平均倒数排名），按难度级别分解输出
- **用法**：`python eval_rag.py [--url http://192.168.88.128:8001] [--top_k 5]`
- **输出**：控制台报告 + 自动保存 `rag_eval_时间戳.json`

### ~~6. sync_milvus 每次重载 embedding 模型~~（✅ 2026-04-21 已完成）
- **RAG 服务**：`rag_api_service.py` 新增 `POST /admin/reload`，接收物料数据后复用常驻内存的 `_GLOBAL_MODEL` 完成向量化和蓝绿切换，同时热更新 BM25 索引
- **chat.py**：`sync_milvus` 改为直接 POST 到 RAG 服务，不再在 Windows 端加载大模型
- **鉴权**：`RELOAD_SECRET` 环境变量，`.env` 和 `docker-compose.yml` 均已配置

---

## 低优先级

### ~~7. 前端无重试机制~~（✅ 2026-04-22 已完成）
- **方案**：N8N 超时/GLM-4V 失败/网络异常时，在错误消息气泡下方显示"🔄 重试"按钮
- **逻辑**：保存最后一次发送的文本和文件，重试时还原输入并重新调用 `sendMessage()`

### ~~8. Docker 日志无集中收集~~（✅ 2026-04-22 已完成）
- **方案**：docker-compose.yml 所有容器加 `json-file` 日志驱动，限制单文件 20-50MB、最多 3-5 个滚动文件
- **工具**：`/opt/rag_service/show_logs.sh` 一键聚合查看所有容器日志，支持按容器过滤

### ~~9. admin 初始密码过于简单~~（✅ 2026-04-22 已完成）
- **方案**：启动时检测 admin 密码是否仍为 `123`，若是则标记 `must_change_password=True`
- **后端**：`auth.py` 新增 `POST /api/v1/auth/change_password`，登录响应含 `must_change_password` 字段
- **前端**：`app.html` 登录后若 `must_change_password=true` 弹出强制修改弹窗，新密码不少于6位

---

## 已完成

- [x] RAG 微服务迁移 CentOS（Docker 容器化）
- [x] Milvus 蓝绿零停机切换
- [x] GLM-4V 静默假数据修复（改为返回 None）
- [x] N8N SQL 注入修复 + ZHIPU_API_KEY 环境变量化
- [x] BM25 升级真混合检索（jieba 分词 + RRF 融合 + 低分过滤）
- [x] 知识库数据扩充至 40 条（北京 2024 年市场行情）
- [x] 前端统一入口 + FastAPI 托管三个 HTML 页面
- [x] 真实 JWT 鉴权替换 Mock 登录
- [x] Windows 任务计划程序自启服务
