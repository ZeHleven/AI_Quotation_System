# 报价资料研判 Agent Phase 4B-1：DeepSeek V4 Flash 受控 Provider 协议

> 版本：v0.1-r46
> 日期：2026-08-16
> 状态：本地专项与相邻回归通过；首次烟测缺口已修复，强化后的最小官方真实复烟通过
> 边界：默认关闭；仅允许完全隔离本地 MVP-1 或未来经单独上线审批的 Worker，当前不得应用到 ECS

## 1. 目标

Phase 4A-2 已建立 ModelCall、Provider Attempt、Result、预算、Lease/Heartbeat/Fencing、Checkpoint 和发送后未知结果恢复，但 MVP-1 本地实验台一直使用确定性 Provider。Phase 4B-1 把官方 `deepseek-v4-flash` 接到同一控制面，不允许模型绕过 Model Gateway、Context Manifest、TaskContract、Fact/Citation 权威或 Run Validator。

官方模型标识为 `deepseek-v4-flash`，OpenAI-compatible Base URL 保持 `https://api.deepseek.com`。本实现只接受 `https://api.deepseek.com/chat/completions` 或 `/v1/chat/completions`，拒绝代理 Host、HTTP、用户名/密码 URL、Query 和 Fragment。

## 2. 冻结模型边界

- Provider Ref：`deepseek`；
- Model Ref：`deepseek-v4-flash`；
- Thinking：`disabled`，避免思维链跨轮回传和持久化；
- Temperature：`0`；
- Response Format：`json_object`；
- 唯一模型输出：`bid.task.action.v1` 候选动作；
- Provider 返回的 `reasoning_content` 即使出现也不进入 Result、Checkpoint、Trace 或报告；
- API Key 只来自 `BID_ASSESSMENT_MODEL_API_KEY`，空值时兼容读取既有 `DEEPSEEK_API_KEY`，不写数据库、日志、Envelope 或前端。

## 3. 受控执行路径

```text
Task Lease
  -> Context Manifest
  -> durable ModelCall/Attempt + provider_request_id
  -> official DeepSeek V4 Flash HTTPS request
  -> JSON action parse
  -> TaskAction Schema + allowed-tools + token/cost budget validation
  -> immutable ModelResult
  -> Checkpoint continuation
  -> Fact/Claim/Citation deterministic authority
```

Provider I/O 继续发生在数据库事务之外。模型不能直接写 Fact、Claim、Decision 或 Report；文档事实必须先通过 `evidence.read` 取得 `context_read=true` 的 Atom，再提交 FactAssertionCandidate。超预算、模型不匹配、Host 不匹配、非法 JSON、非法动作和越权 Tool 均 fail closed。

## 4. 成本与用量

Profile 以 micro-USD 冻结 2026-08-16 的 V4 Flash 价格：每百万 Token 缓存命中输入 `2800`、未命中输入 `140000`、输出 `280000` microunits。优先使用 Provider 的 `prompt_cache_hit_tokens/prompt_cache_miss_tokens`；缺少拆分时全部输入按 cache miss 保守计费。每个逻辑角色预留 `100000` microunits，实际回执仍受 ModelCall 预算门限制。

## 5. 本地实验台

默认命令与既有数据库继续使用 `deterministic`，行为不变。显式传入：

```powershell
.\scripts\start_bid_assessment_mvp1_local.ps1 -ModelMode deepseek-v4-flash -ReplaceLocalPreview
```

才会启用真实 Provider，并使用独立 `.local-mvp1-deepseek-v4-flash` SQLite/对象目录，避免污染原确定性实验台。启动前必须在本机 `.env` 或进程环境配置 API Key；脚本不打印 Key。健康检查会明确返回 `model_provider=deepseek-v4-flash` 和 `external_network=deepseek_official_api_only`。

## 6. 配置与门禁

- `FEATURE_BID_ASSESSMENT_PHASE4_DEEPSEEK_ADAPTER=false`；
- `BID_ASSESSMENT_MODEL_PROVIDER_REF=deepseek`；
- `BID_ASSESSMENT_MODEL_ID=deepseek-v4-flash`；
- `BID_ASSESSMENT_MODEL_CHAT_URL=https://api.deepseek.com/chat/completions`；
- `BID_ASSESSMENT_MODEL_THINKING_MODE=disabled`；
- `BID_MVP1_LOCAL_MODEL_MODE=deterministic`。

DeepSeek 开关依赖 Phase 4 Model Executor；本地 DeepSeek 模式又依赖该开关。现有数据库若冻结了 deterministic ModelProfile，切换模式会拒绝启动并要求使用新的隔离数据库，不原地改写历史 Profile。

## 7. 验证结果

授权后的本地隔离矩阵共 `141 passed / 0 failed`：

- Phase 4B-1 Provider/配置/Host/Model/Thinking/API Key/预算/本地 Profile；
- 完整机器合同；
- Phase 4A-2 LangGraph/Model Gateway/事务/Lease/Fencing/Checkpoint/未知结果恢复；
- MVP-1、API-41 与 SSE 相邻链。

一次不含 PDF、客户资料或业务事实的官方真实调用已发生。HTTPS、API Key 鉴权、`deepseek-v4-flash` 路由和 JSON Object 返回均成功，但返回的 `finish` Action 同时携带 `tool_call_id/tool_name/arguments` 空占位，且缺少必填 `completion_summary`，因此未通过 `bid.task.action.v1`。烟测未连接数据库，未产生 Fact、Claim、Decision 或 Report；真实运行中的 Model Gateway 也会在持久化 ModelResult 前以 `BID_MODEL_ACTION_INVALID` fail closed。

针对该结果已完成以下强化：

- 将完整 `bid.task.action.v1` JSON Schema 注入每次受控请求；
- 明确 action 分支互斥，只允许所选分支字段，禁止 null/空对象填充其他分支；
- 增加合法 `finish` 精确示例和必填字段说明；
- 烟测脚本只输出安全的字段名、Token/费用和回执指纹，非法 Action 也不输出正文。

强化后的本地完整矩阵仍为 `141 passed / 0 failed`。经再次明确授权，唯一一次强化复烟返回：`provider_ref=deepseek`、`model_ref=deepseek-v4-flash`、`thinking_mode=disabled`、`action_type=finish`、`schema_valid=true`、`finish_reason=stop`；输入 `2240` Token、输出 `42` Token、实际成本 `326` micro-USD（约 `$0.000326`）。安全输出确认 `business_data_included=false`，只保留回执 SHA-256 短指纹，不输出 API Key、Prompt、Action 正文或 reasoning。

至此 Phase 4B-1 Provider 可进入完全隔离的本地 MVP-1 联调，但默认开关仍关闭；这不等于真实招标文件端到端质量通过，也不授权 ECS/生产上线。未调用 OCR、视觉、外部 MCP 或真实样例，无新 Alembic revision，代码 head 保持 `20260815_0103`。
