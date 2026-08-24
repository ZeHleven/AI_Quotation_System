# 报价资料研判 Agent Phase 4A-2：受控 Model Gateway 与单 Task 有界 LangGraph Executor 协议

版本：`v0.1-r30`  
日期：`2026-08-13`  
状态：代码、静态检查与本地隔离专项验证完成；功能开关默认关闭，禁止应用到 ECS

## 1. 目标与非目标

Phase 4A-2 在 Phase 3 唯一运行控制面和 Phase 4A-1 SkillBinding 之内，建立模型调用的可审计权威域，并让 LangGraph 只负责一个 Task 的一次有界状态迁移。

本阶段实现：

- 模型调用、模型发送尝试和不可变模型结果的数据库权威；
- 冻结 ModelProfile、PromptBundle、ContextManifest、TaskContract 和 Fence 后才允许调用；
- 模型调用的幂等、预算、Lease、Heartbeat、Fencing、超时和发送后未知结果处理；
- 单 Task、单动作、无内存 Checkpointer 的 LangGraph 状态图；
- 每次推进最多产生一个持久化动作，并写入现有 `bid_checkpoints`；
- `request_tool` 只能交给 Phase 3E Tool Gateway，不得由 LangGraph 直接执行工具；
- 模型结果只产生候选或动作，不直接写 Fact、Claim、Decision 或 Report 权威。

本阶段不实现：

- 真实模型供应商、真实 MCP、OCR、视觉或公网调用；
- 项目级循环、跨 Task 调度或由 LangGraph 接管 Run/DAG；
- Fact/Claim 权威写入、HG01—HG07、Decision、Report；
- 旧 `bid_intake_*` 状态、Checkpoint、Prompt、Repository 或 ToolNode 的复用；
- ECS、正式 MySQL、Redis、MinIO 或外部环境变更。

## 2. 唯一控制边界

外层状态仍只有 Phase 3 可以改变：

```text
Run -> PlanRevision -> Task -> TaskAttempt/Fence -> Checkpoint
                                  |
                                  +-> ContextManifest
                                  +-> ModelCall -> ModelCallAttempt -> ModelResult
                                  +-> ToolInvocation -> AsyncOperation -> ToolDispatch
```

LangGraph 是无持久状态的纯转换器。它不持有数据库连接，不提交事务，不调用 Provider，不写 Tool/Facts，也不使用自身 Checkpointer。服务层先从权威表构造状态，执行一次图转换，再把唯一动作交给相应控制面持久化。

## 3. 本地状态与有界动作

状态协议为 `bid.local_agent.state.v1`，至少包含：

- `run_id/task_id/task_attempt_id/fencing_token`；
- `task_contract_hash/skill_binding_hash`；
- `phase/action_seq`；
- `observed_model_result_refs/observed_tool_result_refs`；
- `candidate_refs/missing_slots`；
- `outstanding_operation_ref/stop_reason`。

一次执行只允许以下一个动作：

1. `request_model`
2. `request_tool`
3. `submit_fact_candidates`
4. `submit_claim_candidates`
5. `request_task_input`
6. `finish`

图结构冻结为：

```text
hydrate_state
  -> propose_one_action
  -> validate_action
  -> route_one_action
  -> yield
```

无模型结果时，`propose_one_action` 只能输出 `request_model`。已有模型结果时，模型动作必须通过封闭 Schema、Skill allowed-tools、TaskContract、预算和 Fence 校验后才能输出。图运行完成后必须立即 yield，禁止在同次调用中继续第二个模型或工具动作。

## 4. 模型权威域

Alembic `20260813_0099` 新增：

- `bid_model_calls`：一个逻辑模型动作；绑定 Assessment/Run/Task/Attempt/Checkpoint/Context、ModelProfile、PromptBundle、Action sequence、Fence、输入 Hash、预算和重放策略；
- `bid_model_call_attempts`：每次 Provider 发送；独立 attempt number、Lease、Fence、execution key、provider request id 和发送阶段；
- `bid_model_results`：每个逻辑调用最多一个不可变结果；保存严格校验后的动作、usage、response/result Hash，不保存思维链。

合法调用主状态：

```text
accepted -> leased -> sending -> succeeded
                    |          -> retry_wait -> leased
                    |          -> uncertain/dead_letter
                    -> failed/cancelled
```

约束：

- 模型调用必须先提交数据库，再发生任何 Provider I/O；
- ModelCall 到 Checkpoint 使用 `task_attempt_id + checkpoint_id` 复合外键，Context 和 AsyncOperation 也必须与同一 Attempt 构成数据库级复合血缘；
- 同一 `task_id + action_seq` 只能有一个逻辑调用；
- 同一调用的 Provider 尝试单调递增，每次使用独立 Fence 和稳定 request id；
- 旧 Fence 的响应只能被记录为迟到，不得推进 Task；
- `sending` 后无法确认结果时，Attempt 进入 `uncertain`；只有冻结策略明确允许时才能新建 Provider Attempt；
- 模型结果只保存受约束动作和 usage，不保存原始密钥、Scope token、原始文档或隐藏推理。

## 5. 异步继续与 Checkpoint

调用模型前必须：

1. 重建并校验 TaskContract/SkillBinding；
2. 组装当前 Attempt 的 ContextManifest；
3. 写 `bid_checkpoints`，状态为 `await_model`；
4. 在同一事务创建 ModelCall 和 `BidAsyncOperation(model:*)`；
5. 将当前 Task/Attempt 转入既有 `waiting_operation` 协议并释放 Task Lease。

模型结果提交后，在同一事务中：

1. 写不可变 ModelResult；
2. 终结 ModelCall/ModelCallAttempt/AsyncOperation；
3. 围栏旧 TaskAttempt；
4. 把 Task 恢复为 `ready`，由下一次 Lease 创建新 Attempt/Fence；
5. 新 Attempt 从上一 Checkpoint 和 ModelResult 恢复，再执行唯一动作。

Checkpoint 仍只有 `bid_checkpoints` 一处权威；不新增 LangGraph Checkpointer。

## 6. Tool、候选和完成边界

- `request_tool`：服务层重新组装当前 Attempt Context，调用 Phase 3E Tool Gateway 完成 Schema、allowed-tools、预算、幂等和 scope 校验；LangGraph 不获得 scope token，也不直接派发。
- `submit_fact_candidates` / `submit_claim_candidates`：Phase 4A-2 只把已校验模型结果引用写入 Checkpoint，等待后续事实权威切片消费。
- `request_task_input`：只形成输入请求候选；正式 QuestionRound 发布由后续协议负责。
- `finish`：只形成 `completion_ready` Checkpoint；在 Fact/Claim/Validation 门尚未实现前不得直接把 Task 标记为 succeeded。

## 7. 预算与失败恢复

预算至少检查：

- TaskContract `max_iterations`；
- ModelProfile 路由的 `max_attempts/timeout_seconds`；
- 输入、输出 token 上限；
- 每个 Provider Attempt 都计入模型尝试预算；
- 冻结路由的调用成本上限，以 `microunits` 整数预留并按 Provider 已知回执结算；
- 失败和恢复不得重置 Task 的累计动作序号。

维护扫描只允许回收过期 ModelCall Lease 或已越过总调用时限的未领取调用：

- `leased` 且尚未发送：可回到 `retry_wait`；
- `sending`：当前 Attempt 记为 `uncertain`，按冻结 replay policy 决定重试或终结；
- `accepted/retry_wait` 且总时限已过：终结 ModelCall/AsyncOperation 并把 Task 恢复到确定性恢复入口；
- Run cancel/retry/stale/terminal：必须同时围栏未终结 ModelCall 和 ModelCallAttempt；
- 任何迟到回执不得解除 Run/Task 的终态 Fence。

## 8. 功能开关与部署门禁

- `FEATURE_BID_ASSESSMENT_PHASE4_LOCAL_AGENT=false`
- `FEATURE_BID_ASSESSMENT_PHASE4_MODEL_EXECUTOR=false`
- `FEATURE_BID_ASSESSMENT_PHASE4_MVP=false`

两个子开关必须依赖 Phase 3 complete runtime 和 Phase 4A-1 Plan Continuation。仓库不注册真实 Provider；只有调用方显式注入 Adapter 后才可执行 Provider I/O。

`0099` 只能应用到独立本地/开发数据库。Phase 4 未经用户确认全部完成并允许上线前，不得进入正式发布候选或 ECS 数据库。

## 9. 验收范围

本次已在用户单独许可下完成：

- Phase 4A-2 合同与 `0099` 迁移拓扑；
- ModelCall/Attempt/Result 事务、ACL 血缘、幂等、预算、Lease、Heartbeat、Fencing；
- 发送前失败、发送后未知、重试、取消、超时、迟到响应恢复；
- 单 Task LangGraph 一次单动作和 Checkpoint 连续性；
- Tool Gateway 交接、Phase 4A-1 历史 TaskContract、Run Validator v4；
- Phase 3C—3G、API-41/SSE 相邻回归。

验证结果：合同、`0099` 迁移拓扑及有界执行核心组 `133 passed`；Phase 4A-1、Phase 3C—3G、API-41 和运行维护相邻组 `48 passed`；SSE/Outbox/事务/幂等组 `8 passed`，合计 `189 passed / 0 failed`。测试仅使用本地 SQLite 与显式注入的内存测试 Provider，未调用真实模型、MCP、OCR/视觉、外部 Tool、真实样例或真实存储。
