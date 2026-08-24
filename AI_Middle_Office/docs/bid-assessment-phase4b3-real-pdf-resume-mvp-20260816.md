# Phase 4B-3：真实 PDF 本地可演示 MVP 收口

日期：2026-08-16  
版本：v0.1-r48  
结论：通过本地演示门；未通过生产发布门。

## 1. 结果

在隔离的 localhost 控制平面中，三份 Development 招标 PDF 已完成以下闭环；数据库、对象存储、检索和 Worker 均为本地资源，模型生成按用户授权调用 DeepSeek API：

`PDF 原生布局解析 -> Parent/Child/Atom -> RQ2-B BM25F+BCE 语义融合 -> Evidence MCP Search/Atom Read -> P0-P4 有界执行 -> Fact/HG01-HG07/Claim/Report -> Run Validation`

三份资料共 383 页，3/3 Run succeeded、3/3 Report ready、78/78 Task succeeded、3/3 Run Validation passed；产生 33 条事实断言、21 条报告 Claim、83 条 Atom 引用、288 个 Checkpoint。DeepSeek V4 Flash 共 105 次调用，输入/输出 `765433/21158` Token，账本费用 `62962` micro-USD（约 `$0.062962`）。

| 资料 | 页数 | Parse | Child/语义条目 | Fact | Claim/引用 | 结论 |
|---|---:|---|---:|---:|---:|---|
| 香港中心 | 307 | review_required / 84 | 1244/1244 | 12 | 10/55 | insufficient |
| 丰隆深港科技园 | 35 | review_required / 78 | 91/91 | 12 | 3/4 | no_bid |
| 泰丰花园六期 | 41 | review_required / 84 | 162/162 | 9 | 8/24 | insufficient |

丰隆的 `no_bid` 由已过投标截止时间的确定性硬门触发。香港中心和泰丰的七项企业硬门主要为 unknown，是因为本地隔离环境使用合成企业快照，而不是系统臆造企业资质。

## 2. 本次为快速落地完成的工程收口

- 新增固定本地 BCE exact-COSINE Provider，并接入 RQ2-A/RQ2-B 索引与融合链。
- 本地 Lab 可处理真实 PDF 的解析、语义索引、标段确认、Run 执行与报告查询。
- Evidence Search/Read 输出分别限制为 24 KiB，并保持 Search Child 不可引用、Read Atom-only。
- Prompt 只允许引用 Tool Gateway 明确投影的 citable Atom ID；禁止用 Child、请求 ID 或模型自造 ID 作为证据。
- 对空事实/Claim 候选执行 fail-safe `finish(EVIDENCE_INSUFFICIENT)`，避免为完成任务而编造内容。
- 保留 Task/Model/Tool 幂等、Lease、Heartbeat、Fencing、Checkpoint、Result Store、发送后未知结果恢复和 Run Validator 血缘检查。
- 新增真实资料启动参数、断点续跑汇总器和三份逐 Run 结果文件。

香港中心运行过程中，为修复 Evidence Read 传输大小而重启过一次本地服务，留下 1 次失败的旧 Dispatch；新 Attempt 从 Checkpoint 恢复并最终通过。丰隆与泰丰的最终运行没有失败 Tool Dispatch。

## 3. 质量判断

已通过：

- 三份真实 PDF 均能从上传走到报告，不再只是合成样例。
- 所有 78 个 Task 和三次 Run 完整性验证均成功。
- 引用保持 Atom-only；没有把检索 Child 当作可引用事实。
- 关键字段抽查能找到项目范围、投标保证金、资格/废标条款和截止时间等信息。
- 费用可记录、执行可追踪、失败可恢复。

仍需人工复核：

- 未调用 OCR/视觉，三份 Parse 均为 partial/review_required；扫描页与复杂视觉表格可能遗漏。
- 当前 Claim 引用数量偏宽，引用精度与报告表达仍需小样本人工评审。
- 香港中心原文含未填写日期模板，Agent 保留原文并将硬门置 unknown，没有推断一个具体日期。
- 本轮实时链使用 RQ2-B；本机没有可用的冻结 RQ2-C Reranker snapshot，因此没有在实时链启用轻量重排。
- 企业快照是隔离合成数据，不能据此验证企业资质匹配或形成生产投标结论。
- RQ2 Development 的正式 Holdout 准出仍未通过/未运行；本轮是快速 MVP 真实资料烟测，不替代未见集评测。

因此当前结论是：可用于 localhost 产品演示、面试讲解和继续迭代；不可宣称已生产上线、无人复核准确率达标或正式 Holdout 通过。

## 4. 隔离与发布边界

- 服务：`http://127.0.0.1:9003/admin/bid-assessment-runtime-lab`
- 数据：独立 SQLite、本地对象目录、进程内 Outbox/Worker。
- 检索：固定本地 BCE Embedding exact-COSINE + RQ2-B 融合；不使用生产 Milvus。
- 模型：DeepSeek V4 Flash；按本次授权将三份 Development 资料的受控 Task Context 发送至模型 API，不连接其他外部环境。
- 未调用：OCR、视觉、外部 MCP、真实 MinIO/Redis/MySQL、ECS/CentOS。
- 功能开关在正式配置中仍默认关闭；没有新增迁移，Alembic head 保持 `20260815_0103`。
- 本增量不得应用到 ECS。

本轮代码收口后运行直接相关专项：Phase 2 Worker、Phase 4A-2 Model Executor、Phase 4B-1 DeepSeek Provider 共 `26 passed`；Evidence Search/Read 传输边界与 Tool Dispatch 共 `3 passed`，合计 `29 passed / 0 failed`。另完成修改文件 `py_compile` 与汇总 JSON 语法检查。没有运行后端全量测试。

## 5. 可用于简历的真实表述

- 设计并落地招标研判 Agent 的可恢复执行链：PDF 原生布局与 Parent/Child/Atom 证据建模、BM25F+BCE 语义融合、Evidence MCP、单 Task 有界 LangGraph、Fact/硬门/报告及可视化运行追踪。
- 在 3 份真实招标文件、383 页 Development 资料上完成端到端验证：3/3 Run 和报告成功、78/78 Task 完成、33 条事实、83 条证据引用，3/3 Run Validator 通过。
- 实现 Model/Tool Gateway、预算账本、幂等、Lease/Fencing、Checkpoint 与发送后未知结果恢复；真实 DeepSeek 调用总成本约 `$0.063`。

这些表述应保留“Development/本地隔离验证”限定，不写成生产上线或准确率承诺。

## 6. 产物

- 汇总：`outputs/bid_assessment_mvp1_real/development-e2e-summary.json`
- 单 Run：`香港中心-summary.json`、`丰隆深港科技园-summary.json`、`泰丰花园六期-summary.json`
- 本地启动：`scripts/start_bid_assessment_mvp1_local.ps1`
- 断点续跑与汇总：`scripts/summarize_bid_assessment_mvp1_local.py`
