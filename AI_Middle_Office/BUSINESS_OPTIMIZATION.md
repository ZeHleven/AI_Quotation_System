# 业务优化路线

> 2026-05-07 更新：P4 真实用户体验优化已进入代码落地阶段，报价失败提示、预审风险标记和人工修改沉淀知识入口已接入 `index.html`；本阶段不新增数据库迁移。

> 当前阶段：P0 报价反馈闭环已落地并完成手工报价验收；P1 Admin 反馈分析页面已落地到代码层。

## P0 报价反馈闭环 + 版本追踪

- [x] 新增 `quote_feedback`：记录每次报价的 AI 初稿金额、最终确认金额、差额、是否修改、是否推送、是否打回。
- [x] 新增 `quote_corrections`：记录人工预审对明细字段的 before / after / delta。
- [x] 新增 `quote_rag_traces`：记录报价结果中可解析的 RAG 召回条目、rank、score、collection alias 和 snapshot id。
- [x] 新增 Dify / prompt / RAG 版本配置：`DIFY_APP_VERSION`、`DIFY_WORKFLOW_VERSION`、`DIFY_PROMPT_VERSION`、`DIFY_RELEASE_ID`、`RAG_COLLECTION_ALIAS`。
- [x] 异步报价成功生成预审结果时，记录 AI 初稿。
- [x] `/confirm_push` 成功推送后，记录最终人工确认稿和字段级修正。
- [x] `index.html` 打回重填时调用 `/api/v1/quote/feedback/reject` 记录 rejection。
- [x] 新增自动化测试 `tests/test_quote_feedback.py`。

## P1 Admin 反馈分析页面

- [x] 新增 `GET /api/v1/admin/quote_feedback/summary`：汇总最近报价数量、确认数、打回数、平均总价偏差、字段修正数和 RAG trace 数。
- [x] 新增 `GET /api/v1/admin/quote_feedback`：分页查看报价反馈记录，支持按天数、用户和状态筛选。
- [x] 新增 `GET /api/v1/admin/quote_feedback/{feedback_id}`：查看 AI 初稿、最终稿、字段级修正和 RAG trace 明细。
- [x] `admin.html` 新增“报价反馈分析”模块，展示核心指标、反馈列表、高频修正字段、高频召回知识条目和 prompt 版本分布。
- [x] 新增测试覆盖 admin summary / list / detail。
- [ ] 低价值召回条目需要后续有“模型引用/人工采纳”信号后再判断，放入 P3 知识库质量治理。

## P2 Prompt 回归评测

- [x] 固定黄金报价案例集：`prompt_regression_cases` 从 `quote_feedback` 固化 AI 初稿、人工最终稿、修正项、请求文本和 prompt/RAG 版本。
- [x] Prompt 回归报告：`prompt_regression_runs` 保存按 prompt 版本计算的总价偏差、格式错误率、明细遗漏率、打回率、人工修改率和综合分。
- [x] 管理接口：新增 build/list/run/latest/history API，先做离线基线，不自动触发 Dify/N8N 外部模型调用。
- [ ] 候选 prompt 自动重跑：需等固定样本量足够、Dify 调用路径和成本策略明确后再启动。

## P3 知识库质量治理

- [x] 从成功推送报价中沉淀知识候选：`knowledge_candidates` 从已确认报价、字段级修正和打回原因生成候选，不自动改物料库。
- [x] 人工确认入库：新增 approve/reject 接口，approve 前自动创建 `material_snapshots` 快照，再新增或更新 `materials` 条目。
- [x] RAG trace 质量洞察：新增 RAG 召回聚合接口，识别高频召回、低分召回、出现在打回/人工修改报价中的条目。
- [ ] Admin 前端候选审核面板后续接入；当前先通过 API 完成候选生成与确认。

## P4 真实用户体验优化

- [x] 报价失败时显示可操作原因：按认证、额度、超时、队列、RAG、N8N/Dify、附件等常见原因给出下一步处理建议。
- [x] 预审弹窗标记高风险项：缺少项目、单价异常、合计异常、缺少备注和人工改动较大时在明细行展示风险标签。
- [x] 用户修改报价后可标记“沉淀为知识”：确认推送时携带 `feedback_reason_category=user_marked_knowledge_candidate` 和人工说明，供后续知识候选生成使用。

## P5 LangGraph 触发项

当前不引入。仅当需要自适应检索、主动澄清、多模型状态机，或需要把 N8N 核心编排迁回 Python 后再评估。
