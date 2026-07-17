# 成本预算对标 Phase 2 P2-2C-2：基础定额模式“一键生成报价”后台任务

日期：2026-07-17

## 目标

在“基础定额”模式下，把“企业定额匹配 → 未匹配项目 AI 估价 → 草稿报价单展示”做成后台任务，而不是依赖前端逐行点击 AI 估价按钮。

## 已实现

- 新增后台任务表：
  - `budget_project_pricing_draft_quote_jobs`
  - `budget_project_pricing_draft_quote_job_lines`
- 新增 Alembic：`20260717_0058_add_budget_pricing_draft_quote_jobs.py`
- 新增 API：
  - `POST /api/v1/admin/budget-projects/{project_id}/pricing-draft/quote-job`
  - `GET /api/v1/admin/budget-projects/{project_id}/pricing-draft/quote-job/current`
  - `GET /api/v1/admin/budget-projects/{project_id}/pricing-draft/quote-jobs/{job_identifier}`
- 前端新增“基础定额”模式下的“一键生成报价”按钮和进度条。
- 任务运行中隐藏半成品草稿行，终态后自动刷新报价草稿。
- 后台任务按行记录：
  - 企业定额命中：`enterprise_matched`
  - AI 待估价/估价中/成功/失败：`ai_pending` / `ai_running` / `ai_succeeded` / `ai_failed`
  - 人工价或状态变化跳过：`skipped`
- 单行 AI 估价写入逻辑改为可复用短事务写回：模型调用前不持有 `FOR UPDATE` 锁，写回时再短事务锁草稿和行。
- 运行态补丁：真实 `SessionLocal` 使用 `autoflush=False`，任务创建时必须先 `flush()` 任务行再统计进度；否则会误把 198 行任务统计为 0 行并标记完成。该问题已修复，并把专项测试改为 `autoflush=False` 复现保护。

## 业务边界

- 只作用于“计价草稿”，不生成新的不可变正式 run。
- 不写企业定额。
- 不写账户定额 active。
- 有企业定额基础价的行不调用 AI。
- 有人工价的行优先尊重人工价，后台任务标记为跳过。
- AI 成功写入 `ai_estimated_unit_price`，有效价优先级仍为：人工价 > AI 建议价 > 企业定额基础价。
- AI 失败不会让整单任务 500 消失；任务会进入 `partial_failed`，由前端提示人工补价。

## 验证

- `python -m compileall`：通过。
- `npm.cmd run build`：通过。
- `pytest AI_Middle_Office/tests/test_budget_pricing_drafts_p2_2a.py -q`：`13 passed`。
- `pytest AI_Middle_Office/tests/test_account_quotas_p2_2b1.py AI_Middle_Office/tests/test_account_quota_draft_sync_p2_2b2.py -q`：`11 passed`。
- `pytest AI_Middle_Office/tests/test_budget_pricing_frontend_contract.py -q`：`6 passed`。
- 当前数据库已升级到 `20260717_0058 (head)`。
- FastAPI `/health/ready` 可用；运行态补丁需要重启 9000 端口进程后生效。

## 后续建议

P2-2C-3 可继续做真实项目运行态验收：用联昇项目触发“一键生成报价”，观察 198 行任务的 DeepSeek 调用数量、失败行、耗时、以及前端进度体验。

## P2-2C-3 补充：DeepSeek 批量估价引擎

日期：2026-07-17

- 新增 `generate_budget_pricing_ai_estimate_batch`，DeepSeek 一次接收多行清单并返回 `{"items":[...]}` JSON 数组。
- 后台任务从“1 行 = 1 次 DeepSeek 请求”改为“每批多行 = 1 次 DeepSeek 请求”。
- 默认启动参数：
  - `ai_batch_size=12`
  - `ai_concurrency=2`
- 批量 JSON 要求每个输入 `row_id` 必须原样返回；系统按 `row_id` 写回对应草稿行。
- 批量请求失败时会自动拆小批重试；批量返回漏行时会对漏行单独重试。
- 熔断打开时会等待一次网关恢复窗口后重试，避免立即把剩余大批行全部标记失败。
- 已有 AI 估价的行会被识别为 `ai_succeeded`，重新点击一键生成报价时不会重复请求模型。
- 前端启动参数已调整为 `ai_concurrency=2`、`ai_batch_size=12`。
- 验证：
  - P2-2A/P2-2C 聚焦测试 `13 passed`
  - 账户定额相邻专项 `11 passed`
  - 前端契约 `6 passed`
  - `npm.cmd run build` 通过
  - 相关服务 `compileall` 通过

## P2-2C-4 补充：DeepSeek 批量估价稳定性调优

日期：2026-07-17

- 运行态观察：P2-2C-3 虽已进入批量模式，但 `ai_batch_size=12`、`BUDGET_PRICING_AI_TIMEOUT_SECONDS=20`、`deepseek-v4-pro` 组合在联昇 198 行项目中仍出现批量请求超时；连续超时触发模型网关熔断后，后续批次会被本地熔断拦截，前端表现为 AI 失败快速上涨。
- 新增预算估价专用模型配置：`BUDGET_PRICING_AI_MODEL=deepseek-v4-flash`。预算估价不再直接复用全局 `DEEPSEEK_MODEL`，避免影响投标、问答等其它 DeepSeek 链路。
- `.env` 当前预算估价配置调整为：
  - `BUDGET_PRICING_AI_MODEL=deepseek-v4-flash`
  - `BUDGET_PRICING_AI_TIMEOUT_SECONDS=45`
- 一键生成报价默认启动参数调整为：
  - `ai_batch_size=6`
  - `ai_concurrency=3`
- 失败延迟重试策略：
  - 普通批量异常先延迟，再拆小批重试；小批/单行仍失败时继续延迟补跑，达到上限后才标记失败。
  - 模型网关熔断时最多等待 3 个恢复窗口后重试，减少“熔断刚打开就把剩余大批行全部标失败”的体验问题。
  - 任务运行期间失败行不会立即大面积上涨，更多行会保持运行/等待重试状态，直到重试上限耗尽。
- 边界不变：仍只写可变计价草稿，不写正式 run、不写企业定额、不写账户定额 active。
- 聚焦验证：
  - `compileall` 通过
  - `pytest AI_Middle_Office/tests/test_budget_pricing_drafts_p2_2a.py -q`：`13 passed`
  - `pytest AI_Middle_Office/tests/test_budget_pricing_frontend_contract.py -q`：`6 passed`
  - `npm.cmd run build` 通过
