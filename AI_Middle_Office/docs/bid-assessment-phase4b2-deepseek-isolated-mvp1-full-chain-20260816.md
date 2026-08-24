# 报价资料研判 Agent Phase 4B-2：DeepSeek 隔离 MVP-1 合成全链

> 版本：v0.1-r47  
> 日期：2026-08-16  
> 状态：官方 DeepSeek 多轮模型 + 本地 Evidence MCP 合成全链已成功收敛  
> 边界：localhost、独立 SQLite/对象目录、进程内队列、合成 TXT；不得应用到 ECS

## 1. 目标与结果

Phase 4B-2 首次把 Phase 4B-1 的真实 `deepseek-v4-flash` Provider 放入完整 MVP-1 控制平面，而不是只做单次烟测。最终授权运行 `run_96981075f4c0497d9ccf02fb8bddd4ae` 已完成：

- 26/26 Task `succeeded`；
- 31/31 ModelCall 形成不可变 ModelResult；
- 20 次本地 Evidence MCP 调用全部成功，严格为 10 次 Search + 10 次 Read；
- 12 条 FactAssertion 均绑定 Evidence，形成 12 条 EvidenceLink，并解析为 18 条 ResolvedFact；
- 88 个 Checkpoint 保留单 Task 有界 LangGraph 的跨 Attempt 延续；
- Run Validator v5 的 51 项检查全部通过；
- Run=`succeeded`，PreliminaryReport=`ready`，合成资料因缺少企业侧权威输入得出 `insufficient`，符合“不知道即不足、不得编造”的规则。

## 2. 完整执行路径

```text
Assessment / synthetic TXT upload / immutable Manifest
  -> local parse + lot selection
  -> Run Bootstrap + P0—P4 Plan Continuation
  -> Task Lease / Attempt / Context Manifest
  -> durable ModelCall / Provider Attempt
  -> DeepSeek closed TaskAction
  -> local Evidence MCP Search (Child, not citable)
  -> local Evidence MCP Read (Atom-only, context_read)
  -> Fact / Claim candidate
  -> deterministic Fact, HG01—HG07, Decision, Citation and Report authority
  -> Run Validation / terminal convergence
```

模型仍不直接写 Fact、Gate、Decision 或 Report。`evidence.search` 返回的 Child 不能引用；只有 `evidence.read` 返回且 `context_read=true` 的 Atom 可以进入文档事实。

## 3. 联调中发现并收口的问题

### 3.1 时间与 typed value

SQLite 会返回无时区 datetime，原 Provider 直接 `isoformat()` 后与 `asserted_at` 的 RFC3339 `Z` 合同不一致。现统一按 UTC 规范化并输出 `Z`。Prompt 同时显式给出各 Task 允许的 Fact slot/value type，以及 money、datetime、date、list 的精确值形状。

DeepSeek 对 money 偶尔仍输出 `10000` 而非 `10000.0000`。Adapter 只对可无损解析、非负、最多四位小数且 currency=`CNY` 的数值补齐四位；中文金额、不确定金额和需要舍入的值保持 fail closed，不做语义猜测。

### 3.2 Tool 参数与控制字段

- `tool_call_id` 改由 Gateway 根据 ModelCall、Action sequence、Tool 和 arguments 生成稳定 ASCII ID；模型仍决定 Tool 与参数，但不能控制幂等身份。
- Query `field_aliases/primary_query/query_language_policy/no_result_policy` 只是构造 query 的上下文提示，进入 Tool Gateway 前会被剥离；`query/top_k` 保留，其他未知参数继续拒绝。
- 当前 Task 的 model-visible Tool 名称和参数 Schema 动态投影进 Action JSON Schema；Scope 始终由服务端注入。

### 3.3 无效响应账本与恢复

Provider 已收到但 Action 合同失败的响应不再丢失 Token/费用。Attempt 只记录以下安全元数据：Action type/字段名/Hash、usage、费用、finish reason、回执 Hash，以及 Pydantic 错误路径/类型/内部错误码；不保存原始无效 Action、Prompt、reasoning 或 API Key。

最终成功 Run 中，`extract_guarantees_and_fees` 的第一次 Fact Action 被 `BID_MODEL_FACT_VALUE_TYPE_MISMATCH` 拒绝，随后安全重试成功。这一被拒绝响应消耗 `6399/392` Token、`479` micro-USD，已计入 ModelCall 总账：

- 逻辑调用总账：输入 `170165`、输出 `5730` Token，`11430` micro-USD（约 `$0.011430`）；
- 不可变成功 Result：输入 `163766`、输出 `5338` Token，`10951` micro-USD；
- 差额与失败 Attempt 完全一致，Run Validator 同时校验 Attempt 合计与 Call 总账。

DeepSeek 本地实验 Profile 升级为不可变 `mvp1-local-deepseek-v4-flash-1.0.1`：每个逻辑调用最多 3 次安全重放、180 秒恢复窗口。旧 1.0.0 Profile 和历史数据库不原地修改。新隔离目录缩短为 `.local-mvp1-ds-b2`，避免 Windows 临时对象路径超过传统长度限制。

## 4. 验证

本轮直接相关合同、Provider、配置、Model/LangGraph、账本恢复与 MVP-1 相邻专项合并为 `110 passed / 0 failed`：

- Phase 4B-1/4B-2 Provider、Host/Model/Key、动态 Tool Schema、UTC、Gateway Tool ID、Query hint 剥离、CNY 表示规范化；
- Phase 4A-2 Model/LangGraph 合同；
- 配置依赖；
- Model rejected-response 账本、重试结算；
- 确定性 MVP-1 P0—P4 全链。

此外，官方 DeepSeek 合成 HTTP 全链单独通过，结果数据见上文。服务保留在 `http://127.0.0.1:9002/admin/bid-assessment-runtime-lab` 供本地查看。

## 5. 边界与下一步

本轮只使用合成 TXT 和本地 Evidence MCP；没有使用真实 PDF、OCR、视觉、外部 MCP、生产 Milvus、ECS、真实 MinIO/Redis 或正式数据库。所有功能开关默认仍为 false，无新 Alembic revision，代码 head 保持 `20260815_0103`，旧 `bid_intake_*` 未修改。

Phase 4B-2 证明“真实模型能在受控运行时完成 MVP-1”，但不等于真实招标资料的生成质量已验收。下一步应进入 Phase 4B-3：在完全隔离环境用已批准的真实 Development PDF 做生成质量评测，冻结事实准确率、引用正确率、漏抽/错抽、Gate/Decision/Report 一致性和成本/延迟门；执行前仍需用户单独授权真实资料和模型调用。
