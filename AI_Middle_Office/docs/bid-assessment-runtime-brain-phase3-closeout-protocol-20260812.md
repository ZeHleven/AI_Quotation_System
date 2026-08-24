# 旗胜投标机会研判 Agent Phase 3 总收口协议

版本：v0.1-r25  
日期：2026-08-12  
状态：协议冻结、总收口增量已实现并完成本地隔离综合验证  
组成：Phase 3A Run Bootstrap 至 Phase 3G Run Validation/Convergence

## 1. 收口定义

Phase 3 总收口只表示运行大脑控制面完成闭环：Run 创建、确定性 Plan Commit、Task Attempt/Lease/Fence、Checkpoint、Context/Tool 调度、取消与恢复、运行级 Validation，以及 API-41/SSE 终态投影可以在同一权威数据域中连续工作。

本收口不表示整个投标机会研判 Agent 已完成或允许上线。当前首个 Adapter 仍只是在本地数据库读取当前 ParseHead 的 `documents.outline`；真实模型、OCR/视觉、公网检索、计算、企业外部系统和真实对象存储执行器继续关闭。DAG 内容质量、事实解析、维度结论和报告生成仍需后续阶段以真实受控执行器实现并评测。

## 2. 完整链

完整成功路径固定为：

`API-40|bid.plan.requested.v1 -> bid.run.created.v1 -> bid.plan.committed.v1 -> bid.task.ready/leased/succeeded.v1 -> bid.run.validation_requested.v1 -> bid.run.succeeded.v1 -> API-41/SSE`。

工具路径只能嵌入当前 Task Attempt：

`TaskContract -> ContextManifest -> ToolInvocation -> AsyncOperation -> ToolDispatch -> ToolDispatchAttempt -> ToolResult -> 新 Task Attempt/Fence -> final Checkpoint`。

取消、超时、重试、输入 stale 或验证失败不能绕开统一 Run 终态，也不能让旧 Task/Tool/Validation Fence 晚到写入。

## 3. 完整运行 Profile 与功能开关

新增机器可读 `contracts/bid_assessment/v1/phase3-runtime-profile.json` 和默认关闭总开关：

`FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME=false`

总开关只用于声明“按完整 Phase 3 链运行”。启用时必须同时启用 V1 Runtime 和 Phase 3A—3G 七个阶段开关，并满足 Phase 3F Tool scope signing key 门禁；任何缺项都在配置加载时 fail closed。总开关关闭时，各阶段开关仍可用于隔离开发和专项验证，既有行为不变。

## 4. 跨阶段终态不变量

1. 每个 Assessment 最多一个 active Run；每 Run 最多一个 committed Plan 和一个 Validation。
2. Task current Attempt、Tool DispatchAttempt、ValidationAttempt 的 fencing 单调递增；旧 Fence 永远不能提交新状态。
3. Checkpoint、ContextManifest、ToolResult 和 Validation Result 均不可变且可哈希复现。
4. DAG 只有全部成功或显式跳过后才能请求 Validation；Task Runtime 不直接把 Run 标记成功。
5. Run 成功、验证失败、输入 stale 和取消分别只有一个原子终态收敛；Assessment 只允许由当前 active Run 更新。
6. Outbox、Processed marker、Audit 和业务状态必须在同一事务边界提交或回滚。
7. API-41 只暴露业务进度投影，不暴露内部 DAG、Attempt、Context 或 Tool 调度细节；SSE 只投影允许的公共事件字段。

## 5. 迁移门禁

总收口不新增表、字段、受约束枚举或 Outbox 事件，故不新增 Alembic revision；代码唯一 head 保持 `20260812_0097`。`0083`—`0097` 必须继续构成从已确认 `20260808_0082` 出发的单线拓扑。

目标 ECS 最近一次只读确认仍为 `20260808_0082`。在用户确认整个 Agent 全部开发完成并允许上线前，Phase 3 总收口及 `0083`—`0097` 均不得进入正式发布候选或应用到 ECS。

## 6. 综合验证门禁

用户已明确授权并完成以下本地隔离验证：

- v0.1-r25 完整运行 Profile 和功能开关依赖闭包；
- API-40 -> Plan Commit -> Task/Context/本地只读 Tool Adapter -> 新 Attempt/Fence -> final Checkpoint -> Validation -> API-41/SSE 的确定性端到端链；
- `bid-run-integrity-validator-v2` 对 Task Attempt/Checkpoint、Context Manifest、Invocation、AsyncOperation、Dispatch/DispatchAttempt 和 ToolResult 的完整 Hash/Fence 血缘；
- Phase 3A—3G 合同、事务、幂等、Lease/Fence、取消、超时、恢复和终态唯一性综合回归；
- `0083`—`0097` 迁移拓扑和静态一致性。

最终干净验证矩阵为 `175 passed / 0 failed`：合同/Planner/配置门禁 `79`，Phase 3A—3G/API-40/API-41/完整端到端 `31`，事务/Outbox/SSE/维护恢复 `14`，`0083`—`0097` 迁移拓扑 `51`。另完成 Python 编译、JSON/Schema、Alembic head、模型注册和 `git diff --check` 静态检查。验证只使用本地 SQLite 和本地数据库只读 `documents.outline` Adapter；未运行真实样例、OCR/视觉、模型、公网、真实外部工具或真实对象存储，未连接任何外部环境。
