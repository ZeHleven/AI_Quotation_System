# Retrieval Eval v1

本目录用于报价资料研判 Agent 的 Query 拆分、检索路由和证据召回评测。

- `public_demo.jsonl`：公开合成样本，只验证框架与路由；
- `historical_case_template.json`：私有历史样本的单条标注模板；
- `private_challenge_draft_v1.jsonl`：Holdout之后新增项目的Challenge标注草稿，不参与当前调参；
- `experiment_template.json`：单变量 A/B 实验记录模板。
- `experiment_ledger_v1.md`：脱敏实验台账，记录每次唯一改动、指标变化和保留/拒绝结论；
- `development_baseline_summary_v1.md`：首轮Development基线的脱敏摘要。
- `ret_exp_003b_holdout_freeze_v1.json`：RET-EXP-003B唯一Holdout盲测前的候选、代码哈希、配置和发布门槛冻结快照；
- `ret_exp_003b_holdout_blind_summary.md`：唯一Holdout盲测的脱敏结论、分层诊断、未通过原因和后续防过拟合边界。
- `ret_fact_gate_001_shadow_summary.md`：事实槽位工作状态与证据充分性门的shadow实现、评测契约、测试和新Development准备记录。
- `private_fact_coverage_development_approved_v1.jsonl`：经业务复核的8题Development A，只用于新的事实门/结构检索实验。
- `ret_fact_gate_001_development_a_freeze_v1.json`：Development A运行前的数据、代码、配置和门槛冻结快照。
- `ret_fact_gate_001_development_a_execution_v1.json`：唯一一次正式shadow评测指标、逐题分类与保留/拒绝结论。
- `ret_struct_context_001_development_a_freeze_v1.json` / `ret_struct_context_001_development_a_execution_v1.json`：只补父章节/表头的第一版结构实验冻结与失败结论。
- `ret_struct_context_002_development_a_freeze_v1.json` / `ret_struct_context_002_development_a_execution_v1.json`：同父兄弟证据组第二版的冻结、指标与Development A通过结论。
- `ret_struct_context_002_summary.md`：父子结构两次试验、指标解释、过拟合边界和下一实验方向。
- `ret_controlled_retry_001/002/003_development_a_*`：受控第二轮检索三次冻结与执行记录；前两次失败、003检索门通过。
- `ret_controlled_retry_003_summary.md`：第二轮检索的触发边界、失败整合方式、最终候选及残留事实门缺口。
- `ret_graph_expand_001_experiment_contract.md`：选择性跨文档图扩展的触发、关系、预算和新数据门槛；当前因缺少未污染跨文档Development而暂停。
- `private_graph_development_b_lingshui_source_manifest_v1.json`：全新陵水项目四类资料的哈希、旧格式转换、解析数量、质量限制和防污染边界。
- `private_graph_development_b_lingshui_review_v1.md`：10题Development B业务复核稿；复核冻结前不得运行或调参。
- `ret_graph_holdout_001_freeze_v1.json`：惠州未来花园独立Holdout的题目、Gold、资料、代码、配置与门槛冻结快照。
- `ret_graph_holdout_001_execution_v1.json`：独立Holdout唯一质量对照和唯一配对延迟的完整机器可读结果。
- `ret_graph_holdout_001_summary.md`：独立Holdout质量失败、延迟通过、根因判断与防过拟合下一步。
- `ret_candidate_coverage_002_development_c_contract.md`：候选覆盖选择跨项目复验的数据门槛、候选池诊断指标、单变量配置和预注册验收门槛。
- `private_fixed_development_pool_v2_approved.jsonl`：香港中心、深圳丰隆、总部基地和陵水共38题的固定可复用Development池。
- `ret_candidate_coverage_002_fixed_development_freeze_v1.json`：候选覆盖选择002首次固定Development对照前的数据、代码、配置、逐项目与候选池门槛快照。
- `ret_candidate_coverage_002_fixed_development_execution_v1.json`：固定38题Development池唯一一次Baseline/Candidate正式对照、逐项目结果、候选池诊断和拒绝结论。
- `ret_candidate_coverage_002_fixed_development_summary.md`：总体指标改善但跨项目稳定性失败的原因、典型退化案例、防过拟合判断和003方向。
- `ret_candidate_coverage_003_experiment_contract.md`：锚点保护型直接对齐选择器的单变量边界、固定数据、门槛和运行纪律。
- `ret_candidate_coverage_003_fixed_development_freeze_v1.json`：003唯一正式运行前的数据、代码、项目索引、配置和验收门槛快照。
- `ret_candidate_coverage_003_fixed_development_execution_v1.json`：003唯一正式A/B、逐项目指标、实现契约不一致诊断和拒绝结论。
- `ret_candidate_coverage_003_fixed_development_summary.md`：为什么003先因实现不符合契约而无效、哪些结果仍可使用，以及004的变形不变量方向。
- `ret_candidate_coverage_004_experiment_contract.md`：共享普通Baseline Top-5骨架、Candidate只修改其副本的独立实验契约与预注册门槛。
- `ret_candidate_coverage_004_fixed_development_freeze_v1.json`：004唯一正式A/B前的数据、代码、配置、索引、不变量测试和运行纪律冻结快照。
- `ret_candidate_coverage_004_fixed_development_execution_v1.json`：004一次Baseline、一次Candidate的机器可读指标、实现一致性审计、冻结门判断与拒绝结论。
- `ret_candidate_coverage_004_fixed_development_summary.md`：004工程修复有效但质量收益、Gold保留率和跨项目门未通过的脱敏总结。
- `ret_candidate_coverage_005_experiment_contract.md`：基于004已保存预测形成的选择损失诊断，以及谓词感知边际事实收益/替换损失政策契约。
- `ret_candidate_coverage_005_fixed_development_freeze_v1.json`：005唯一正式A/B前的数据、代码、配置、关系门、不变量测试和运行纪律冻结快照。
- `ret_candidate_coverage_005_fixed_development_execution_v1.json`：005一次Baseline、一次Candidate的机器可读指标、promotion审计、候选池顺序不一致和拒绝结论。
- `ret_candidate_coverage_005_fixed_development_summary.md`：005总体与跨项目质量向好，但候选池有序等价和Gold保留率冻结门失败的脱敏总结。
- `ret_evidence_sufficiency_gate_002_experiment_contract.md`：把谓词感知关系覆盖从005选择器中解耦，只读评估普通Baseline证据充分度、零/部分覆盖拒答及失败关闭的独立代码与评测契约。

真实客户资料不要写入 `public_demo.jsonl`。私有评测集应脱敏、受控保存，并由不同人员完成标注和复核。完整说明见 `docs/bid-intake-agent-retrieval-evaluation-v1.md`。

Holdout只允许在候选冻结后运行一次。盲测结果不能用于修改参数、追加关键词规则或改变Gold标签；后续优化必须从全新项目建立新的Development/Challenge样本。

Challenge项目不计入原Development/Holdout的30题门槛，不用于回调已经冻结的候选；完成独立业务复核后，单独报告其泛化结果。

项目级的“问题 → 证据 → 假设 → 方案比较 → 试错 → 指标 → 结论 → 下一步”学习记录见
`docs/bid-intake-agent-development-notes.md`。实验台账与开发笔记必须同步更新，不能只记录最终成功方案。
