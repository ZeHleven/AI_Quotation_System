# BIZ-4a-analysis-2 招标文件分析成果表生成

## 目标

将招标文件解析后的内部数据，从“业务对象列表”收敛为项目经理可复核的三张成果表：

1. 结构化信息摘要表
2. 评分细则表
3. 风险条款清单

业务对象继续保留为后台证据层，但前台默认隐藏，不再作为经理验收主页面。

## 输入

复用当前解析版本中的数据：

- `tender_requirements`
- `tender_risks`
- `tender_business_objects`
- `bid_parse_runs.summary_json.document_structure`
- `bid_projects` 项目基础字段

本阶段不新增数据库表，不新增 Alembic。

## 输出接口

`GET /api/v1/admin/bidding/projects/{project_uuid}/tender-analysis/preview`

参数：

- `run_uuid=latest`，默认取最新完成解析版本

返回结构：

- `business_object_policy.hidden_by_default=true`
- `tables.summary.items`
- `tables.scoring.items`
- `tables.risk_clause.items`
- `quality_summary`
- `review_queue`

## 前端展示

Vite 智能投标详情页新增“招标分析”主标签：

- 顶部展示摘要、评分、风险、待复核数量
- 三张成果表以子标签展示
- 待复核队列单独展示缺失、低置信度、高风险等原因
- 原“业务对象”移入“内部业务对象”折叠面板，默认收起

## 验收口径

- 上传并解析招标文件后，进入项目详情应优先看到“招标分析”成果表
- 业务对象不应作为默认主视图出现
- 摘要表至少覆盖项目概况、截标时间、评标标准、保证金、付款方式等标准项
- 评分细则表应能归属商务标、技术标、报价/商务标等包
- 风险条款清单应展示条款原文、风险等级、建议应对、是否影响报价、是否需答疑
- 待复核队列应提示缺失、低置信度和高风险项
