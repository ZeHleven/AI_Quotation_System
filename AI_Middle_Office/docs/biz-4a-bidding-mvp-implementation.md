# BIZ-4a 招标文件解析与风险识别 MVP 实现说明

> 日期：2026-06-29
> 状态：第一版 MVP 已完成代码层验证，BIZ-4a-4 业务对象层已完成代码层验证。
> 范围：投标项目建档、招标资料上传与文本抽取、规则式招标要求/合同风险/废标风险识别、人工复核状态维护。

## 第一版原则

BIZ-4a 的第一版实现采用“规则优先、LLM 预留”的路线：

- 规则引擎负责稳定识别付款、结算、总价包干、清单漏项、工期违约、签证索赔、暗标、废标等高频风险。
- 每条要求和风险保留原文、来源文件、来源位置、风险解释和建议动作。
- LLM 暂不直接参与正式结果，后续可作为高风险条款二次复核能力接入模型网关。
- 人工复核仍是最终判断入口，风险可标记为确认、忽略、转答疑或转报价预留。

## 新增后端对象

- `bid_projects`：投标项目。
- `bid_project_files`：投标项目资料，保存文件元信息、抽取文本和段落证据。
- `bid_parse_runs`：每次解析版本。
- `tender_requirements`：招标要求清单。
- `tender_risks`：合同风险、废标风险和暗标风险。
- `tender_business_objects`：结构化投标业务对象，把要求和风险进一步归并为投标规则、资格审查、合同条款、报价约束和文件清单。

数据库迁移：

- `20260629_0037_add_bidding_mvp_tables.py`
- `20260630_0038_add_tender_business_objects.py`

功能开关：

- `FEATURE_BIDDING_MVP=false`

## 支持文件类型

第一版支持：

- `.txt`
- `.md`
- `.csv`
- `.pdf`
- `.docx`
- `.xlsx`
- `.xlsm`

暂不支持：

- 旧 `.xls`
- 纯扫描版 PDF 的 OCR
- 压缩包批量导入

## API

基础路径：

- `/api/v1/admin/bidding/projects`

核心接口：

- `POST /admin/bidding/projects`：创建投标项目。
- `GET /admin/bidding/projects`：查询投标项目。
- `GET /admin/bidding/projects/{project_uuid}`：查询项目详情。
- `PATCH /admin/bidding/projects/{project_uuid}`：更新项目基础信息。
- `POST /admin/bidding/projects/{project_uuid}/files`：上传并抽取招标资料文本。
- `GET /admin/bidding/projects/{project_uuid}/files`：查询项目资料。
- `POST /admin/bidding/projects/{project_uuid}/parse`：生成招标解析版本。
- `GET /admin/bidding/projects/{project_uuid}/parse-runs`：查询解析版本。
- `GET /admin/bidding/projects/{project_uuid}/requirements`：查询招标要求。
- `GET /admin/bidding/projects/{project_uuid}/risks`：查询合同/废标风险。
- `PATCH /admin/bidding/risks/{risk_uuid}/review`：人工复核风险。
- `GET /admin/bidding/projects/{project_uuid}/business-objects`：查询结构化投标业务对象。
- `PATCH /admin/bidding/business-objects/{object_uuid}/review`：人工复核业务对象。

## BIZ-4a-4 业务对象层

BIZ-4a-4 的目标是把“段落级要求/风险”升级成“投标业务对象”，作为后续响应矩阵、报价预留、答疑清单和投标文件生成的共同底座。

首版采用规则式抽取，不直接调用 LLM：

- `bid_rule`：投标截止、开标时间、保证金、递交方式、签章密封、评标办法等。
- `qualification`：企业资质、安全许可证、项目经理、建造师、业绩、联合体、分包限制等。
- `contract_clause`：付款、结算、变更签证、质保、违约金、工期责任、验收、范围界面等。
- `pricing_constraint`：固定总价、价格不调整、漏项责任、措施费包干、暂定量、甲供/甲限品牌、税率和报价口径等。
- `document_checklist`：商务标、技术标、报价清单、授权委托书、承诺函、资格文件、证书附件、偏离表/响应表等。

每个业务对象保留：

- 归一化标题和值。
- 原文证据和来源位置。
- 关联的要求 ID / 风险 ID。
- 责任角色、是否必须响应、复核状态。
- 置信度和抽取方法。

前端 `/admin/bidding` 已新增“业务对象”标签页，支持查看分布、来源证据，并可人工复核为确认、转答疑、报价预留或忽略。

## 当前风险类型

第一版规则覆盖：

- `fixed_total_price`：总价包干/固定总价。
- `omission_liability`：清单漏项责任转嫁。
- `no_price_adjustment`：材料或人工价格波动不调价。
- `advance_funding`：垫资或无预付款。
- `delayed_payment`：付款节点偏后、审计后付款或质保金压力。
- `liquidated_damages`：工期违约金。
- `claim_time_limit`：签证索赔时限。
- `site_condition`：现场条件自行承担。
- `design_or_drawing_unclear`：图纸、范围或暂定口径不明确。
- `material_brand_constraint`：指定品牌、甲供或甲指乙供。
- `bid_rejection`：废标/否决投标条款。
- `anonymous_bid`：暗标匿名风险。

## 验证

## BIZ-4a-4-1 业务对象质量增强

BIZ-4a-4-1 针对真实招标文件复盘中发现的对象误归类和对象过碎问题做了增强，不新增数据库结构：

- 合同条款分类改为强业务信号优先：解除合同、违约金、工期、质保金先于付款/支付，减少“违约金被归到付款条款”的误判。
- 付款条款拆分为预付款/垫资、进度款、结算款、质保金/保留金、付款前置条件。
- 投标规则分类优化：澄清/答疑先于投标截止，投标保函/保证金先于投标有效期，减少相对期限误判。
- 低价值泛化对象过滤：无明确值或无风险关联的 `contract_general`、`pricing_general`、`bid_rule_general`、`document_general` 不再从普通要求中生成大对象。
- 聚合键优化：付款、工期、结算、质保等对象不再按每个百分比/天数拆成多个对象，多个抽取值保存在 `normalized_json.extracted_values`。
- 增加业务用途 `business_action`：投标合规检查、资格响应、文件编制、报价预留、转答疑、法务复核、履约策划、信息参考。
- 前端业务对象表新增“用途”列，摘要新增报价预留数量。

基于当前真实文件“东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程招标文件”的最新解析结果临时重算：

- 原业务对象：69 个。
- 增强后业务对象：41 个。
- 需响应/处理：25 个。
- 报价预留：7 个。
- 法务复核：10 个。
- 对象分布：投标规则 10、合同条款 15、文件清单 5、报价约束 7、资格审查 4。

注意：该优化只影响重新解析后的结果；旧解析 run 中已落库的业务对象不会自动回填。

## BIZ-4a-4-2 大对象拆分与风险动作联动

BIZ-4a-4-2 在 BIZ-4a-4-1 的 41 个对象基础上继续增强，目标不是继续压缩数量，而是把高 `source_count` 的大对象拆成更接近人工复核和响应矩阵的可行动对象。

本阶段仍不新增数据库结构：

- 复用风险卡 `primary_action` / `secondary_action`，把风险动作联动到业务对象：
  - `to_quote_allowance` -> `quote_allowance`
  - `to_clarify` -> `clarification`
  - `manual_blocking_review` -> `bid_compliance`
  - `bid_decision_review` -> `legal_review`
- 业务对象 `normalized_json` 记录风险卡上下文：`risk_cards`、`risk_primary_actions`、`risk_secondary_actions`、`risk_grades`、`review_roles`。
- 对违约金、工期、范围、签证索赔和不调价对象做二级拆分：
  - 违约金：工期逾期、质量/验收、材料质量、人员更换/到岗、解除/停工。
  - 工期：总工期/节点、开工条件、顺延条件、进度计划报送、赶工责任。
  - 范围/现场：现场条件、范围调整/界面迁移、图纸会审/深化设计、报批报建/手续。
  - 签证索赔：签证时限、签证资料要求、反索赔/扣款、变更签证流程。
  - 不调价：综合单价、措施费/开办费、人工材料价格波动、合同价款。
- 代表证据选择从“第一条”改为“风险优先、高等级优先、命中对象关键词优先”，减少页面首条证据偏题。

基于当前真实文件最新解析 run 临时重算：

- BIZ-4a-4-1：41 个业务对象。
- BIZ-4a-4-2：56 个业务对象。
- 需响应/处理：33 个。
- 报价预留：12 个。
- 转答疑：3 个。
- 法务复核：9 个。
- 履约策划：13 个。
- 对象分布：投标规则 10、合同条款 28、文件清单 5、报价约束 9、资格审查 4。

这一步的取舍：对象数量上升，但“违约金/工期/签证/不调价”等大类被拆成更细动作，后续生成响应矩阵时更适合一行一事。仍有部分对象 `source_count >= 20`，例如材料质量、图纸会审、变更签证流程等，后续可通过 LLM 二次判别或更细的领域词典继续拆分。

已完成聚焦测试：

```powershell
C:\Users\12521\miniconda3\python.exe -m pytest tests\test_bidding_mvp_biz4a.py
```

结果：

- `8 passed`

前端构建：

```powershell
cd ai-web
cmd /c npm.cmd run build
```

结果：

- 通过

语法检查：

```powershell
C:\Users\12521\miniconda3\python.exe -m compileall app tests\test_bidding_mvp_biz4a.py
```

结果：

- 通过

## BIZ-4a-4-3 代表证据与大对象降噪

BIZ-4a-4-3 针对 BIZ-4a-4-2 真实文件复盘中暴露的“代表证据偏题”和“高 source_count 对象不利于人工复核”继续增强，不新增数据库结构：

- 代表证据从“风险优先”升级为“相关风险优先”：先计算对象关键词命中、风险子类映射和负向关键词，再决定是否让风险证据胜出。
- 为 `project_basic`、`brand_constraint`、`owner_supplied_material`、`response_table`、`scope_boundary` 等易偏题对象补充专用证据关键词和反向词，降低密封/付款/废标条款误抢代表证据的概率。
- `response_table` 识别排除废标、否决投标、无效投标、重大偏差等语境，避免把“未响应即废标”误当成响应表清单。
- 对 `source_count >= 20` 的大对象压缩证据样本，仅保留最相关的 8 条左右证据，同时在 `normalized_json` 中记录 `large_object`、`needs_secondary_split`、`evidence_sample_count` 和 `omitted_evidence_count`。
- 每个业务对象新增证据质量信号：`representative_evidence_relevance`、`representative_evidence_quality`、`representative_matched_keywords`、`representative_negative_keywords`、`low_confidence_representative`。
- 前端业务对象表展示大对象标记、低置信代表证据、相关度、样本压缩数量和次级风险动作，便于人工复核时决定确认、转答疑、报价预留或继续拆分。

本阶段仍是规则式质量增强，适合处理高频、可解释的证据偏题问题；后续 LLM 更适合用于低置信对象的二次判断、跨段落合并和真正的语义拆分。

## BIZ-4a-4-4 大对象二次拆分与章节证据降权

BIZ-4a-4-4 的目标是避免继续按单个真实文件堆规则，而是建立更通用的对象质量框架：大对象只有在 `source_count >= 20` 时触发二次拆分，小对象保持原识别路径。

本阶段不新增数据库结构，仍写入 `tender_business_objects.normalized_json`：

- 章节证据降权：目录、封面、结构噪声、协议书首页、纯页码目录等证据即使命中关键词，也会被降低代表证据排序分；正文合同条款、投标须知、技术要求、清单说明等保持优先。
- 大对象二次拆分：对材料质量、图纸深化、变更签证、付款、结算、保修、范围界面等高 `source_count` 父对象按通用语义桶拆分。
  - 材料：品牌/样板标准、进场验收、不合格材料拆除重采、扣款/违约金。
  - 图纸：深化设计、图纸错漏、图纸会审/交底、竣工图/记录图纸。
  - 变更签证：审批流程、书面确认、资料要求、时限、反索赔扣款。
  - 付款结算：付款资料/审核、发票条件、中期付款限制、基本要求费用付款条件、结算审计、结算资料、结算调整边界。
- 主动作与辅动作：业务对象保留 `primary_business_action` 和 `secondary_business_actions`，避免把材料、图纸、结算等复合事项压成单一动作。
- 不确定性显式化：无法继续拆清的大对象标记 `needs_secondary_split`、`needs_llm_review`、`split_confidence`，样本少或上下文较弱的拆分桶标记 `weak_split`，供后续 LLM 或人工复核。
- 前端业务对象页展示已拆分、弱拆分、仍需二拆、需 LLM/人工复核、上下文质量和次级动作。

这一步仍是规则优先的泛化框架，验收重点不是简单压低对象数量，而是减少目录/封面抢证据、让高来源对象更接近“一对象一动作一证据链”。

## BIZ-4a-4-5 DeepSeek 选择性复核不确定业务对象

BIZ-4a-4-5 开始引入 LLM，但边界非常窄：只处理规则层已经显式标记为不确定的业务对象，不重跑招标文件解析，也不全量扫描所有对象。

第一版默认模型：

- `BIDDING_LLM_PROVIDER=deepseek`
- `BIDDING_LLM_MODEL=deepseek-v4-pro`
- `BIDDING_LLM_TIMEOUT_SECONDS=0`（0 表示系统侧不设置 HTTP 超时）
- `BIDDING_LLM_MAX_OBJECTS=25`
- 独立开关：`FEATURE_BIDDING_LLM_REVIEW=true`

候选硬过滤条件：

- `normalized_json.weak_split = true`
- 或 `normalized_json.needs_llm_review = true`
- 或 `normalized_json.needs_secondary_split = true`
- 默认只处理 `review_status=pending` 的对象；人工已确认/忽略的对象不会再次送模型，除非后续显式 force。

LLM 只返回只读建议，并写回 `tender_business_objects.normalized_json`：

- `llm_review_status=pending_manual_confirm`
- `llm_provider=deepseek`
- `llm_model=deepseek-v4-pro`
- `llm_prompt_version=biz4a_business_object_llm_review_v1`
- `llm_review.decision`：`keep` / `rename` / `split` / `ignore` / `manual_review`
- `llm_review.selected_evidence_ids`：只能引用系统传入的 `E1/E2/...` 证据。
- `llm_review.suggested_splits`：仅作为拆分建议，不自动创建对象。

新增接口：

- `POST /admin/bidding/projects/{project_uuid}/business-objects/llm-review`

前端 `/admin/bidding` 的“业务对象”页新增 `DeepSeek复核` 按钮，点击后只提交不确定对象；表格展示 `DeepSeek已建议` / `LLM异常` 标签和模型建议摘要。

2026-06-30 补充体验优化：前端不再一次性提交全部对象，而是按 `object_uuid` 逐条调用 `limit=1`，页面展示 DeepSeek 复核进度条、当前处理对象、完成数、异常数和跳过数；每条完成后刷新结果区，方便观察长耗时模型调用进度。

当前明确不做：

- 不让 LLM 直接修改 `object_type` / `object_subtype`。
- 不让 LLM 自动新建或删除业务对象。
- 不让 LLM 自动改变人工复核状态。
- 不把完整招标文件原文发给 LLM，只发送单个对象的压缩证据包。

## BIZ-4a-4-6 DeepSeek 建议采纳/驳回闭环

BIZ-4a-4-6 把 BIZ-4a-4-5 的“只读建议”推进到可审计的人工闭环。第一版只提供三个业务动作：

- `采纳`：接受 DeepSeek 原建议，写入 `normalized_json.llm_review_effective`，状态改为 `llm_review_status=accepted`。
- `驳回`：要求填写驳回原因，保留原始 `llm_review` 作为历史建议，但 `llm_review_effective=null`，状态改为 `llm_review_status=rejected`。
- `修改`：复核人可调整建议类型、建议标题、建议子类、业务动作和说明，系统复用 LLM 建议清洗规则生成新的 `llm_review_effective`，状态改为 `llm_review_status=modified`。

新增接口：

- `PATCH /admin/bidding/business-objects/{object_uuid}/llm-review`

写回字段仍放在 `tender_business_objects.normalized_json`，不新增 Alembic：

- `llm_review_status`：`accepted` / `rejected` / `modified`
- `llm_review_decision_action`：`accept` / `reject` / `modify`
- `llm_review_decision_note`
- `llm_review_decided_by` / `llm_review_decided_by_username` / `llm_review_decided_at`
- `llm_review_effective`
- `llm_review_manual_edit`（仅修改时写入）

重要边界：

- 采纳/驳回/修改不会自动改 `object_type`、`object_subtype`、`title`、`review_status`。
- 业务对象的“确认/忽略/转答疑/报价预留”仍走原人工复核动作。
- 后续响应矩阵应读取 `llm_review_effective`，而不是直接读取原始 `llm_review`。
- 已采纳/驳回/修改的对象不会再次进入默认 DeepSeek 待处理队列，除非后续显式 force。

## BIZ-4a-5-1 响应矩阵数据表与初稿生成

BIZ-4a-5-1 把“解析/复核结果”推进到“投标响应任务表”。这一版不生成投标书正文，只生成可复核、可编辑、可追踪的响应矩阵。

新增表：

- `tender_response_items`

核心字段：

- `response_item_uuid`
- `project_id` / `parse_run_id`
- `business_object_id` / `requirement_id` / `risk_id`
- `source_key`：同一解析版本内的幂等键，例如 `bo:{object_uuid}`、`risk:{risk_uuid}`、`req:{requirement_uuid}`
- `response_category`
- `response_action`
- `response_title`
- `source_text`
- `evidence_json`
- `owner_role`
- `risk_level`
- `status`
- `response_note` / `reviewer_note`
- `created_from`
- `normalized_json`

新增接口：

- `POST /admin/bidding/projects/{project_uuid}/response-matrix/generate`
- `GET /admin/bidding/projects/{project_uuid}/response-matrix`
- `PATCH /admin/bidding/response-items/{response_item_uuid}`

生成规则：

- 主来源是 `TenderBusinessObject`，只有 `response_required=true` 且未人工忽略的对象进入矩阵。
- `llm_review_status=accepted/modified` 且存在 `llm_review_effective` 时，响应标题和动作优先采用人工确认后的 LLM 有效建议。
- `llm_review_status=rejected` 时，不读取被驳回的 DeepSeek 建议，仍按原业务对象动作生成。
- 未被业务对象覆盖的高风险/阻断风险会补充生成风险响应项。
- 未被业务对象覆盖的关键资格、技术、商务、工期、递交、清单、品牌要求会补充生成要求响应项。
- 重复点击生成不会新增重复项，也不会覆盖已有人工作业结果。

前端 `/admin/bidding` 项目详情新增“响应矩阵”Tab：

- 展示响应项、来源对象、原文证据、响应动作、风险等级、责任角色、状态和备注。
- 支持生成初稿。
- 支持轻编辑响应动作、状态、责任角色和响应说明。

当前明确不做：

- 不自动生成完整投标书正文。
- 不自动创建答疑单、报价预留单或法务任务，只在响应矩阵中标记动作和状态。
- 不在重复生成时覆盖人工修改；后续若需要“重新同步解析结果”，应单独设计带差异预览的刷新流程。

## BIZ-4a-5-2 响应矩阵工作台增强

BIZ-4a-5-2 的目标是把“系统生成的表”推进成“投标负责人可复核、可分工的工作台”。这一步不新增数据库表，复用 `tender_response_items.normalized_json` 承载增强信息。

新增生成能力：

- 技术要求标题生成/聚类：未被业务对象覆盖的 `technical` 要求不再逐条生成泛化标题，而是按施工组织、质量验收、工作面移交、材料环保、成品保护、批量施工、进度计划、安全文明等主题聚合成响应项。
- 旧泛化技术项替代：旧版自动生成的“识别到技术质量要求...”行在重新生成时会标记 `superseded_by=technical_requirement_cluster`，默认列表隐藏；人工复核过或已改状态的行不自动替代。
- 合同风险多动作联动：保留一个主 `response_action`，同时在 `workflow_actions` 中给出协同动作，例如报价预留、法务复核、转答疑、文件编制，并标注建议责任角色和原因。
- 覆盖解释：每条响应项写入 `coverage` 和 `coverage_explanation`，说明由哪个来源生成、覆盖几条要求/风险、保留几条代表证据。

前端增强：

- 响应矩阵行展示协同动作标签。
- 响应项展示覆盖要求/风险数量。
- 说明列展示覆盖解释，帮助负责人判断为什么这行存在、有没有漏项。
- 摘要区展示覆盖要求数、覆盖风险数和技术聚类数。

仍保持的边界：

- 不自动拆成真实任务流，不写入答疑单、预算任务或法务任务表。
- 不自动删除历史响应项，只默认隐藏被聚类替代的旧自动项。
- 不调用 LLM 生成技术标题，当前是保守规则聚类；后续可只对低置信技术簇调用 DeepSeek 生成更自然的标题和响应提纲。

## BIZ-4a-5-3 响应矩阵去重/拆分质量增强

BIZ-4a-5-3 的目标是把响应矩阵从“系统生成的表”继续推进为“投标负责人可复核、可分工的工作台”。这一步仍不新增数据库结构，复用 `tender_response_items.normalized_json` 承载质量规则结果。

新增后端能力：

- 精确重复合并：对未人工复核、未完成的同标题/同类型/同动作响应项，保留最早主项，其他项标记 `superseded_by` 并默认隐藏；不物理删除历史行。
- 过载项拆分：对同时覆盖过多风险、要求或协同动作的自动响应项，拆成预算、法务、经营等可单独复核的子项，例如“漏项责任报价预留”“漏项责任法务复核”“清单/表格填报响应要求”。
- 角色复核视图：每条响应项根据责任角色、主动作、协同动作和技术分类生成 `review_roles`，支持经营、预算、技术、法务视图过滤。
- 质量解释：响应项写入 `quality_flags`、`quality_explanation`、`split_parent_*`、`split_reason`，说明为什么被合并或拆分。

新增接口能力：

- `GET /admin/bidding/projects/{project_uuid}/response-matrix?review_role=business|budget|technical|legal`
- 响应项返回 `review_roles`、`quality_flags`、`quality_explanation`、`split_parent_uuid`、`split_parent_title`。
- 摘要返回 `by_review_role`、`quality_flag_count`、`by_quality_flag`、`split_item_count`。

前端增强：

- 响应矩阵 Tab 新增“全部/经营/预算/技术/法务”复核视图切换。
- 行内展示复核角色、拆分项/合并项质量标签和质量解释。
- 摘要区展示质量标记数、拆分项数和各角色复核数量。

仍保持的边界：

- 不自动创建经营任务、预算任务、技术任务或法务任务表。
- 不覆盖已人工复核、已完成或已有人工备注的响应项。
- 不使用 LLM 判断是否合并/拆分；第一版采用可解释规则，后续可只对低置信拆分项调用 LLM 给出建议。

## BIZ-4a-5-4 主责角色与可派工颗粒度增强

BIZ-4a-5-4 的目标是把响应矩阵从“按角色能筛选”推进到“每一行都能被明确派工、复核、关闭”。这一步仍不新增数据库结构，继续复用 `tender_response_items.normalized_json`。

新增后端能力：

- 主责/协同分离：
  - `primary_review_role`：经营、预算、技术、法务，只保留一个主责。
  - `supporting_roles`：协同角色，可多个。
  - `review_roles` 仍保留完整参与角色，但默认筛选按主责走。
- 复核动作建议：
  - `review_action` / `review_action_label`：确认响应、准备资格材料、写入商务标、写入技术标、转预算测算、转法务判断、形成答疑问题。
  - `done_criteria`：每行明确完成口径，避免“完成”按钮缺少业务定义。
- 颗粒度与覆盖口径：
  - `granularity_level`：`atomic`、`bundle`、`role_action`、`risk_family`、`theme_cluster`。
  - `coverage_classification`：首版区分 `must_respond` 和 `evidence_reference`。
  - `quality_score`：给负责人提示该行颗粒度/覆盖质量是否稳定。
- 二次拆分：
  - 对 BIZ-4a-5-3 拆出的仍然过载子项继续按风险族拆分。
  - 漏项类细分为清单漏项、表格漏填、重大偏差/责任转嫁等。
  - 违约/扣款类细分为工期、质量验收、人员材料、解除停工等。
  - 价格不调整类细分为人工材料波动、综合单价/总价包干、措施费/开办费等。

接口增强：

- `GET /admin/bidding/projects/{project_uuid}/response-matrix?review_role=business|budget|technical|legal` 默认按 `primary_review_role` 过滤。
- 响应项返回 `primary_review_role`、`supporting_roles`、`review_action_label`、`done_criteria`、`coverage_classification`、`granularity_level`、`quality_score`。
- 摘要返回 `by_primary_review_role`、`by_review_action`、`by_coverage_classification`。

前端增强：

- 响应矩阵角色视图按主责过滤，协同角色只作为标签展示。
- 响应项展示“主责/协同”“复核动作”“完成标准”。
- 角色统计改为主责口径，避免经营视图被技术标、法务、预算协同项撑大。

仍保持的边界：

- 不新增任务表，不自动创建派工任务。
- 不调用 LLM 做语义拆分；本阶段继续使用可解释规则。
- 不尝试把 841 条原始要求全部逐条转成响应矩阵行，而是先区分必须响应、证据参考和低价值噪声的口径。

## BIZ-4a-5-5 响应矩阵层级降噪与复核波次

BIZ-4a-5-5 的目标是把响应矩阵从“每行可派工”继续推进到“投标负责人能按层级和优先级复核”。这一步不新增数据库结构，继续复用 `tender_response_items.normalized_json`。

新增后端元数据：

- 父子层级：
  - `task_display_type`：`single_task`、`theme_task`、`summary_task`、`group_task`。
  - `task_display_label`：单项任务、主题任务、汇总任务、分组任务。
  - `task_group_key`、`task_group_parent_title`、`task_group_index`、`task_group_child_count`、`has_group_children`。
  - 同一风险族存在分组项时，前端按 `task_group_key` 把分组任务挂到汇总任务下，不再平铺造成语义重复。
- 行级完成标准：
  - `done_checklist`：按具体主题生成完成清单。
  - `done_criteria`：由完成清单压缩成一行口径。
  - 已覆盖漏项责任、固定总价/价格不调、人员/材料违约、工期违约、质量验收违约、解除/停工、付款/垫资、技术标、资格材料、答疑等常见类型。
- 复核优先级和波次：
  - `review_priority`：`P0`、`P1`、`P2`、`P3`。
  - `review_priority_label`：第1波-阻断/决策、第1波-高责任/高金额、第2波-必须响应、第3波-常规补齐。
  - `review_wave` / `review_wave_label`：`wave_1`、`wave_2`、`wave_3`。
  - `priority_reason`：解释为什么排在该波次。

接口增强：

- 响应项返回任务类型、分组层级、完成清单、复核优先级和波次字段。
- 摘要返回 `by_task_display_type`、`by_review_priority`、`by_review_wave`。

前端增强：

- 响应矩阵表格改为树形展示：汇总任务为父行，分组任务作为子行展开。
- 统计仍按过滤后的全部任务计算，避免树形展示后低估工作量。
- 响应项展示任务类型、复核波次、优先级原因和行级完成清单。

仍保持的边界：

- 不新增任务派发表和多人协同状态机。
- 不在后端物理删除历史过拆数据；通过元数据和 superseded 机制降噪，保留追溯。
- 复核波次仍是规则式首版，后续可引入 LLM 或企业规则库调优。

## BIZ-4b-0 / BIZ-4b-1 投标书目录骨架生成

BIZ-4b 的第一步不是直接生成完整投标书正文，而是先建立“投标书草稿的目录控制面”。从第一性原理看，投标书正文生成前必须先回答：

- 招标文件要求哪些章节和响应动作。
- 每个章节由经营、预算、技术、法务谁负责复核。
- 哪些章节可以带占位生成草稿，哪些被 P0/高风险/缺材料阻断。
- 每个章节关联了哪些响应矩阵项、要求、风险和原文证据。

BIZ-4b-0 的产品边界：

- 目录骨架从最新解析 run 和响应矩阵生成。
- 第一版不新增数据库表，不保存正文，不覆盖人工复核结果。
- 目录骨架是后续正文生成、附件绑定、导出 Word 前的工作台，不是最终投标书。

BIZ-4b-1 新增后端能力：

- 新增 `app/services/bidding_draft_outline.py`，规则式生成投标书目录骨架。
- 一级目录固定为：商务标响应、资格审查资料、技术标方案、报价文件与报价说明、合同偏离与风险决策、答疑问题清单、附件与签章清单。
- 二级章节按响应矩阵标题/分组标题聚合，保留：
  - `section_type`、`section_title`、`owner_role`。
  - `draft_status`：`ready`、`needs_input`、`blocked`。
  - 关联响应项 UUID、要求 ID、风险 ID、证据数量。
  - 缺口说明、风险提示、行级完成标准。
- P0 未完成、法务/答疑高风险项会阻断正文生成；其他待补充项允许后续带占位生成草稿。

新增接口：

- `GET /admin/bidding/projects/{project_uuid}/bid-draft/outline`：预览目录骨架。
- `POST /admin/bidding/projects/{project_uuid}/bid-draft/outline/generate`：生成目录骨架。

前端增强：

- `/admin/bidding` 项目详情新增“投标书草稿”Tab。
- 展示章节数量、可起草数量、阻断数量和来源响应项数量。
- 表格展示一级章节和二级章节任务、主责角色、状态、关联数量、缺口、风险提示和完成标准。

当前明确不做：

- 不生成完整 Word 投标书。
- 不生成正文段落和技术方案长文。
- 不自动绑定企业资质附件、报价文件或成本测算结果。
- 不调用 LLM；后续正文草稿、技术方案扩写、法务偏离表文字可作为 LLM 接入点。

## BIZ-4b-1.1 目录骨架质量修正

BIZ-4b-1.1 针对目录骨架首版复盘中发现的“章节误归类”和“可起草状态不清楚”做小范围质量修正，不进入正文生成，不新增数据库结构。

后端修正：

- 章节归类优先尊重响应矩阵业务分类：
  - `bid_rule` 默认进入商务标响应，不再因为原文出现“技术标”而误入技术标方案。
  - `qualification` 默认进入资格审查资料。
  - `pricing_constraint` 默认进入报价文件与报价说明。
  - `contract_clause` 默认进入合同偏离与风险决策，若主责/动作明确为预算则进入报价章节。
  - `document_checklist` 再按资格、报价、技术、商务和附件关键词兜底判断。
- 章节行新增正文生成模式：
  - `draft_mode=formal`：正式可成稿。
  - `draft_mode=placeholder`：可带占位起草。
  - `draft_mode=blocked`：暂不建议生成正文。
- 保留 `can_generate_draft` 兼容字段，同时新增 `can_generate_placeholder_draft` 和 `can_generate_formal_draft`。
- `summary` 新增 `placeholder_draft_count`、`formal_draft_ready_count` 和 `by_draft_mode`。
- 已完成复核的章节不再因为通用“需补充材料”提示永远停留在 `needs_input`。

前端修正：

- “投标书草稿”摘要区将可起草拆成“正式/占位”。
- 章节缺口列优先展示 `draft_mode_label`，让负责人区分“可带占位起草”和“正式可成稿”。

## BIZ-4b-2 单章节正文草稿 MVP

BIZ-4b-2 第一版只实现“按单个章节生成正文草稿”，用于验证目录骨架、响应矩阵和证据链是否能支撑真实投标书制作。当前不做整书批量生成，不导出 Word，不调用 LLM。

新增数据库表：

- `bid_draft_sections`：保存单章节草稿、占位符、来源响应项、要求、风险、证据、生成器信息和人工复核状态。

新增后端能力：

- `GET /admin/bidding/projects/{project_uuid}/bid-draft/sections`：查询已生成的章节草稿。
- `POST /admin/bidding/projects/{project_uuid}/bid-draft/sections/generate`：按 `section_key` 生成单章节草稿。
- `PATCH /admin/bidding/bid-draft/sections/{draft_uuid}/review`：人工标记章节草稿为 `reviewed`、`needs_revision` 或 `accepted`。
- 草稿生成器 `rule_section_draft_v1` 输出 Markdown，包含响应立场、具体措施、风险/偏离处理、待补充项和来源依据。
- `draft_mode=blocked` 的章节不生成可提交正文，只生成阻断说明和待处理动作。
- `draft_mode=placeholder` 的章节会显式写入 `【待补充：...】` 占位符，避免把缺资料伪装成已完成。

前端增强：

- “投标书草稿”目录表新增“草稿”操作列。
- 二级章节支持生成/重新生成章节草稿。
- 草稿预览抽屉展示 Markdown、占位符、风险提示、来源证据和复核按钮。

首版验收口径：

- 商务规则章节可生成正式草稿。
- 技术方案章节可生成带占位草稿。
- 报价/法务风险章节若阻断，只生成阻断说明。

## BIZ-4b-2.1 单章节生成小修正

BIZ-4b-2.1 目标是把“能生成章节”修正为“投标负责人更容易理解、复核和沉淀版本”。仍不做整书批量生成，不导出 Word。

修正内容：

- 前端文案将 `生成说明` 改为 `生成复核说明`，避免用户误以为它是可提交正文。
- 商务规则新增生成判定：
  - `direct_response`：可直接响应，可生成正式草稿，允许 LLM 润色。
  - `needs_input`：需补资料，只生成带占位草稿，不进入 LLM。
  - `risk_decision`：需风险决策，生成复核说明，不直接生成正文。
- `bid_draft_sections` 增加 `content_version` 和 `generation_decision_json`。
- 新增 `bid_draft_section_versions`，记录规则生成、DeepSeek 生成和人工编辑版本。
- 新增 `PATCH /admin/bidding/bid-draft/sections/{draft_uuid}/content`，支持人工编辑正文并保存版本。
- 技术方案草稿接入企业能力/施工经验模板，提供装饰工程常用的施工组织、质量、安全、进度、材料样板和成品保护表达骨架，同时继续保留人员、业绩等待补充占位。
- 单章节 LLM 只允许处理 `generation_decision.llm_eligible=true` 的正式草稿；`blocked`、`review_note`、`placeholder` 均拒绝 LLM 正文生成。

验收重点：

- 商务低风险或已复核章节显示“可直接响应”，可生成草稿。
- 商务高风险但不属于法务阻断的章节显示“需风险决策”，按钮为“生成复核说明”。
- 技术方案章节草稿包含企业能力/施工经验参考模板。
- 人工编辑正文后版本号递增，版本记录可追溯。
- 对阻断章节请求 LLM 生成正文返回 `BID_DRAFT_SECTION_LLM_NOT_ALLOWED`。

## BIZ-4b-2.2 旧草稿升级提示 + 商务 P0 二次细分

BIZ-4b-2.2 目标是修正 BIZ-4b-2.1 真实使用中的两个观感问题：旧草稿和新规则混在一起时用户不知道该不该重生成；商务 P0 被过度解释为风险决策。仍不做整书批量生成，不导出 Word，不自动覆盖用户已编辑内容。

修正内容：

- 目录骨架版本提升为 `biz4b_bid_draft_outline_v1.2`，单章节规则生成器提升为 `rule_section_draft_v1.2`。
- 商务 P0 新增二次细分：
  - `compliance_reminder`：硬性合规提醒，例如投标截止、递交、密封、开标、评标办法、投标有效期、答疑/澄清等；可生成正式草稿，允许 LLM 润色。
  - `needs_input`：需补资料，例如签章、授权、附件、响应表/偏离表、资质、证照、业绩、人员等；生成带占位草稿，不进入 LLM。
  - `risk_decision`：需风险决策，例如投标保证金、保函、废标、重大偏差、不响应、违约、赔偿、责任转嫁等；只生成复核说明，不进入 LLM。
- `serialize_bid_draft_section` 新增 `upgrade_hint` 和 `needs_upgrade`：
  - 旧规则模板不是最新 `rule_section_draft_v1.2`。
  - 缺少 `generation_decision_json`。
  - 缺少版本记录。
  - 技术方案草稿缺少“企业能力/施工经验参考模板”。
- 前端目录行展示“需升级”标签；旧草稿操作按钮显示“升级草稿”；草稿预览抽屉展示旧草稿升级原因。

验收重点：

- “投标截止时间”等商务 P0 显示“硬性合规提醒”，不是“需风险决策”。
- “投标保证金”等高责任事项仍显示“需风险决策”，只生成复核说明。
- 旧 `rule_section_draft_v1` / `rule_section_draft_v1.1` 草稿会提示升级，但不会被系统自动覆盖。
- 技术方案旧草稿若未包含企业能力/施工经验模板，会提示重新生成。

## BIZ-4b-2.3 综合类章节二次拆分

BIZ-4b-2.3 目标是把“其他投标规则”“综合响应事项”“其他商务要求”等兜底标题，从投标书目录里的大杂项拆成负责人可以复核的小章节。第一版不改响应矩阵原始数据，不新增数据库结构，不让 LLM 直接生成正文。

修正内容：

- 目录骨架版本提升为 `biz4b_bid_draft_outline_v1.3`。
- 在 `_group_title_for_item` 阶段识别泛化标题，并按关键词族二次拆分：
  - `bid_guarantee`：投标保证金与保函。
  - `rejection_deviation`：废标/重大偏差响应边界。
  - `submission_deadline`：投标截止与递交时间。
  - `submission_seal`：递交方式与密封要求。
  - `validity_evaluation`：投标有效期与评标办法。
  - `clarification`：答疑澄清事项。
  - `response_table`：响应表/偏离表一致性。
  - `document_package`：投标文件组成与签章附件。
  - `business_liability`：商务承诺与责任边界。
- 子章节新增追溯字段：
  - `split_from_generic_title`
  - `original_group_title`
  - `split_family`
  - `split_confidence`
  - `split_reason`
  - `needs_secondary_split`
- 未能可靠拆分的综合项保留为“原标题（待二次拆分）”，并标记 `needs_secondary_split=true`，后续可只对这类对象调用 LLM 给拆分建议。
- 前端目录行展示“综合项拆分/需二次拆分”标签，并展示拆分理由。
- 单章节草稿正文头部写入“拆分来源”和“拆分理由”，保证后续人工复核可追溯。

验收重点：

- “其他投标规则”中关于密封、递交的事项不再留在“其他”，应拆到“递交方式与密封要求”。
- “综合响应事项”中关于保证金/保函的事项应拆到“投标保证金与保函”，并保持 `risk_decision`。
- 无法稳定归类的“其他商务要求”应保留但标记为 `needs_secondary_split=true`。
- 摘要区返回 `generic_split_section_count`、`secondary_split_needed_count` 和 `by_split_family`。

## 后续建议

BIZ-4a 下一步可以继续补：

- 扫描版 PDF OCR 接入。
- 投标书正文草稿生成、附件绑定和 Word 导出链路。
- 响应矩阵一键导出 Excel，支持经营/预算/技术/法务分工复核。
- 解析结果导出 Markdown/Excel。
- Vite `/admin/bidding` 轻量页面。
- 真实招标文件样例回归集。
