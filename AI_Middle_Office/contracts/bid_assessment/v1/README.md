# 投标机会研判 Agent v1 机器合同

本目录是 `docs/bid-intake-agent-redesign-master-spec-v0.1.md` 的 Phase 0 机器可读派生产物。它只冻结外部合同和状态语义，不注册 FastAPI 路由、不创建数据库表，也不替换现有 `app/agents/bid_intake` 运行时。

## 产物

- `manifest.json`：合同版本、来源和文件入口；
- `state-transitions.json`：Assessment、Run、Upload Batch 等状态机；
- `error-codes.json`：稳定业务错误码与 HTTP 映射；
- `event-catalog.json`：Public SSE 与内部 Outbox 事件目录；
- `decision-compatibility.json`：最终决策和投入等级兼容矩阵；
- `task-catalog.json`：第 7.6 节冻结的 49 个标准任务类型；
- `../../../schemas/bid_assessment/v1/contracts.schema.json`：JSON Schema 2020-12 合同包；
- `../../../schemas/bid_assessment/v1/tools.schema.json`：25 个模型可见工具的参数 Schema；
- `../../../schemas/bid_assessment/v1/task.schema.json`：TaskDefinition 与单任务契约；
- `../../../schemas/bid_assessment/v1/planner.schema.json`：Planner 输入与 PlanProposal；
- `../../../schemas/bid_assessment/v1/fact.schema.json`：Assertion、Slot Coverage 与 Resolved Fact；
- `../../../schemas/bid_assessment/v1/dimension.schema.json`：七维统一输出；
- `../../../schemas/bid_assessment/v1/decision.schema.json`：确定性决策输出与兼容矩阵；
- `../../../schemas/bid_assessment/v1/report.schema.json`：不可变报告、Claim、Citation 与 Delta；
- `../../../schemas/bid_assessment/v1/context.schema.json`：可复现 Context Manifest；
- `../../../schemas/bid_assessment/v1/model-roles.schema.json`：Local Research、Synthesizer、Evidence Validator 边界；
- `../../../openapi/bid-assessment-v1.openapi.json`：OpenAPI 3.1 外部接口合同。

## 约束

1. 新合同版本只能向后兼容地增加可选响应字段；删除字段、改变语义或改变资源身份必须发布新版本。
2. 金额、数量和高精度比例在 JSON 边界使用十进制字符串，禁止使用 JSON number 作为正式计算输入。
3. 所有请求对象默认 `additionalProperties: false`。
4. 现有 `/api/v1/admin/bidding/projects/.../bid-intake/...` 继续属于旧运行时；本合同的资源前缀为 `/api/v1/bid-assessments/...`。
5. 本阶段没有 Alembic migration；数据库实现必须在确认候选 `20260808_0082` 和目标环境实际 head 后另行开始。

## 验证

```powershell
& .\.venv-agent\Scripts\python.exe -m pytest tests\test_bid_assessment_contracts_v1.py -q
```
