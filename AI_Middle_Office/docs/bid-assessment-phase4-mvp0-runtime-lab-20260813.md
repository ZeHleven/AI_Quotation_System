# Phase 4 工程可视化 MVP-0：Runtime Lab

版本：`v0.1-r31`  
日期：`2026-08-13`

## 目标

MVP-0 不是业务初筛报告，也不增加第二套 Agent Runtime。它是 Phase 2—Phase 4A-2
新数据域上的只读工程观察面，用一张图解释并观察以下链路：

`Run Bootstrap → Planner/SkillBinding → Task DAG → Lease/Fencing → Context Manifest → 单 Task LangGraph → Model/Tool Gateway → Result Store → Checkpoint → Validation/Convergence`

本地入口：`/admin/bid-assessment-runtime-lab`

## 读取协议

- `GET /api/v1/bid-assessment-runtime-lab/capabilities`
- `GET /api/v1/bid-assessment-runtime-lab/runs`
- `GET /api/v1/bid-assessment-runtime-lab/runs/{run_id}/trace`
- 实时提示继续复用 `GET /api/v1/bid-assessments/{assessment_id}/events`；浏览器以带 JWT 的 `fetch` 流读取 SSE，事件到达后重新拉取权威快照。

Runtime Trace schema 固定为 `bid.runtime.trace.v1`。节点来自既有权威表，边表示
contains、depends_on、attempt、context、checkpoint、model_call、tool_call、dispatch、result
和 validation 等血缘关系。Trace Hash 不包含 `generated_at`，相同数据库状态必须形成相同
ETag。机器合同见 `schemas/bid_assessment/v1/runtime-trace.schema.json`。

## 可见性与脱敏

- 列表和 Trace 均为 owner/admin ACL；越权访问统一按 404 处理。
- API 只返回控制平面元数据：状态、版本、时间、预算计数、Lease、Fencing、Hash 和依赖。
- API 不返回 Prompt 正文、Context 正文、模型 Action 正文、Tool 参数、Tool 结果正文、对象存储正文或模型思维过程。
- API-41 继续保持“不暴露内部 Task DAG”的正式进度投影边界；Runtime Lab 使用独立只读路由。
- 不读取或修改旧 `bid_intake_*` 权威数据。

## 预览模式

页面在开关关闭或无可见 Run 时展示明确标记的“协议预览”。预览节点全部使用
`preview` 标识和 `preview-not-authoritative` Hash，只用于学习架构，不代表模型、MCP、Tool、
OCR 或真实样例曾经执行。

## 功能开关与迁移

新增 `FEATURE_BID_ASSESSMENT_PHASE4_MVP0_TRACE=false`，默认关闭。仅开启该开关
即可读取静态 Runtime Trace；不要求开启 V1/Phase 3/Phase 4 执行器开关，因此仅查看页面
不会启动 Outbox 或执行器后台循环。需要实时 SSE 时可另外开启
`FEATURE_BID_ASSESSMENT_V1_RUNTIME=true`。读取现有 `0099` 权威表即可，本增量不新增
Alembic revision，代码 head 保持 `20260813_0099`。

MVP-0 仅限独立本地/开发环境，不得部署或应用到 ECS。
