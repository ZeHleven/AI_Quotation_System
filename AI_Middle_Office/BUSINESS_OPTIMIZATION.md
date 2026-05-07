# 业务优化路线

> 当前阶段：P0 报价反馈闭环已落地到代码层。目标数据库需要升级到 Alembic `20260507_0004`。

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

- [ ] 最近 7 天报价数量、通过率、驳回率。
- [ ] 平均人工修改率、平均总价偏差。
- [ ] 高频错误类型、偏差最大的报价。
- [ ] 不同 Dify/prompt 版本表现对比。
- [ ] 高频召回知识条目和低价值召回条目。

## P2 Prompt 回归评测

- [ ] 固定黄金报价案例集。
- [ ] 每次 Dify prompt 改版后记录版本并跑回归。
- [ ] 指标包括总价偏差、明细遗漏率、格式错误率、人工修改率。

## P3 知识库质量治理

- [ ] 从成功推送报价中沉淀黄金案例。
- [ ] 按工艺、地区、面积区间、材料档次、施工条件细化知识条目。
- [ ] 根据 RAG trace 清理低价值条目，补充缺失条目。

## P4 真实用户体验优化

- [ ] 报价失败时显示可操作原因。
- [ ] 预审弹窗标记高风险项。
- [ ] 用户修改报价后可标记“沉淀为知识”。

## P5 LangGraph 触发项

当前不引入。仅当需要自适应检索、主动澄清、多模型状态机，或需要把 N8N 核心编排迁回 Python 后再评估。
