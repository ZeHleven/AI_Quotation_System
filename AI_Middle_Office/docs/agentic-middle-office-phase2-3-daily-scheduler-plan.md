# Agentic Middle Office Phase 2-3: 每日自动复核调度器 MVP 规划

## 当前边界更新（2026-06-09）

本调度器当前只服务“报价后审计 Agent”：每日定时扫描当日已确认下发报价并记录审计结果，不再输出今日待办，不再触发建议采纳、草案执行或二次人工确认。调度失败时只展示结果状态，由管理员检查运行记录，不提供人工补扫作为默认流程。

后审计结果记录原预审风险、最终确认下发状态、修改前后解释。预审风险拆分为工程量改动和单价改动，两类阈值均为 25%。本 Agent 暂不使用企业内部 RAG 和 Memory；`market_price_web_search_v1` 只在审计时通过 Bing Web Search 查询东莞、深圳公开网页结果，并由 DeepSeek 归纳解释，不沉淀为成本库市场价表。

## 背景

当前已完成：

- 人工主动复核闭环：人工输入报价任务 ID，Agent 输出风险、建议、预计节省金额，人工采纳/拒绝，Agent 只生成草案，最终仍由人工确认。
- 每日复核手动扫描入口：AI助手中心可扫描业务日内已确认下发的 `quote_history`，并创建 `trigger_source=scheduled_daily` 的 Agent 复核记录。
- 自动复核去重：以 `quote_history.id` 作为自动复核触发引用，避免重复扫描同一张已下发报价历史。
- 业务日口径修正：默认由后端 `AGENT_DAILY_REVIEW_TIMEZONE=Asia/Shanghai` 决定当天，前端不再用浏览器本地日期猜测。

Phase 2-3 的目标是把“人工点击扫描”升级为“每天固定时间自动扫描”，但仍保持只读复核和人工确认边界。

## 目标

每天在业务时间点自动完成：

1. 找到当天确认下发的报价单。
2. 跳过已经自动复核过的报价历史。
3. 对未复核的报价历史创建报价复核 Agent run。
4. 记录调度运行结果，包括候选数、创建数、跳过数、失败数。
5. 在 AI助手中心能看到调度是否执行、是否失败、以及待处理建议。

## 非目标

本阶段不做：

- 不自动修改报价单。
- 不自动下发钉钉。
- 不自动执行 Agent 建议。
- 不接 LLM / RAG / MCP / LangGraph 重构。
- 不引入完整多 Agent 调度平台。
- 不把调度迁移到 Celery Beat，除非后续试运行证明应用内调度不够稳定。
- 不做跨机器分布式调度集群。

## 首版方案选择

采用 **FastAPI lifespan 内轻量调度循环**。

原因：

- 当前系统已经在 `app/main.py` 的 lifespan 中运行后台告警循环，可沿用同类模式。
- 当前阶段是内网试运行，部署复杂度要低。
- 每日复核本身已经幂等，重复触发也不会重复复核同一 `quote_history.id`。
- 不需要额外部署 Celery Beat 或 Windows Task Scheduler。

后续迁移条件：

- 如果 FastAPI 多进程部署导致多实例同时触发，且数据库锁仍不足以管控。
- 如果需要复杂重试、失败告警、任务队列隔离。
- 如果后续多个 Agent 都需要统一调度。

## 配置项

已有：

```env
FEATURE_AGENT_ASSISTANTS=true
FEATURE_AGENT_DAILY_REVIEW=true
AGENT_DAILY_REVIEW_TIMEZONE=Asia/Shanghai
AGENT_DAILY_REVIEW_RUN_TIME=18:30
AGENT_DAILY_REVIEW_MAX_JOBS=100
```

建议新增：

```env
AGENT_DAILY_REVIEW_POLL_SECONDS=60
AGENT_DAILY_REVIEW_CATCHUP_MINUTES=120
```

说明：

- `RUN_TIME`：业务日每日触发时间，默认 18:30。
- `TIMEZONE`：业务日期和触发时间所属时区，默认 Asia/Shanghai。
- `POLL_SECONDS`：后台循环检查频率，默认 60 秒。
- `CATCHUP_MINUTES`：服务重启后允许补跑的窗口。例如 18:30 没运行，19:20 服务恢复，仍可补跑当天。
- `MAX_JOBS`：单次最多扫描多少张已下发报价单，防止异常数据量冲击系统。

## 调度算法

后台循环每 `POLL_SECONDS` 检查一次：

1. 如果 `FEATURE_AGENT_ASSISTANTS=false` 或 `FEATURE_AGENT_DAILY_REVIEW=false`，不运行。
2. 读取 `AGENT_DAILY_REVIEW_TIMEZONE`，得到业务当前日期和时间。
3. 计算当天计划触发时间 `review_date + RUN_TIME`。
4. 如果当前时间早于计划时间，不运行。
5. 如果当前时间晚于计划时间超过 `CATCHUP_MINUTES`，不自动补跑，只在运行记录中标记 missed 或由人工补扫。
6. 查询当天是否已有调度运行记录。
7. 如果已有成功或运行中记录，不重复运行。
8. 获取数据库锁，避免并发调度。
9. 调用现有 `run_daily_quote_review(db, review_date=today, actor="system_scheduler")`。
10. 写入调度运行结果。

## 并发保护

首版采用数据库运行记录 + 单机进程锁的组合：

- 进程内 `asyncio.Lock`：防止同一 FastAPI 进程内重复触发。
- 数据库唯一约束：防止重启或多进程下同一业务日重复创建调度批次。

建议新增表：`agent_scheduler_runs`

核心字段：

- `id`
- `scheduler_key`：例如 `quote_review_daily`
- `run_date`：业务日期，例如 `2026-06-08`
- `status`：`running` / `success` / `failed` / `skipped`
- `scheduled_at`：计划触发时间
- `started_at`
- `finished_at`
- `triggered_by`：`system_scheduler` / `manual_retry`
- `candidate_count`
- `created_run_count`
- `skipped_duplicate_count`
- `skipped_invalid_count`
- `failed_count`
- `result_json`
- `error_message`
- `created_at`

唯一约束：

```text
scheduler_key + run_date + triggered_by=system_scheduler
```

如果数据库唯一约束实现复杂，首版也可以用：

```text
scheduler_key + run_date
```

人工补扫仍可复用现有 daily-runs API，不必写成新的 scheduler run。

## API 规划

保留现有：

- `POST /api/v1/admin/agents/quote-review/daily-runs`
- `GET /api/v1/admin/agents/quote-review/daily-summary`
- `GET /api/v1/admin/agents/suggestions/pending`

建议新增：

### 查询调度运行记录

```http
GET /api/v1/admin/agents/quote-review/scheduler-runs?date=2026-06-08
```

返回：

```json
{
  "scheduler_key": "quote_review_daily",
  "run_date": "2026-06-08",
  "status": "success",
  "scheduled_at": "2026-06-08 18:30:00",
  "started_at": "2026-06-08 18:30:12",
  "finished_at": "2026-06-08 18:30:45",
  "candidate_count": 12,
  "created_run_count": 10,
  "skipped_duplicate_count": 2,
  "failed_count": 0
}
```

### 手动重试调度批次

首版暂不新增，继续使用现有 daily-runs API 补扫。

如果要新增，建议：

```http
POST /api/v1/admin/agents/quote-review/scheduler-runs/retry
```

仅 `admin` / `quote_operator` 可用。

## 前端规划

AI助手中心每日自动复核区域增加一行“调度状态”：

- 今日计划时间：18:30
- 当前状态：
  - 未到时间
  - 已自动执行
  - 执行中
  - 执行失败
  - 超过补跑窗口未执行
- 最近执行时间
- 候选单数 / 新增复核 / 跳过重复 / 失败数

交互：

- “刷新概览”：刷新 summary、pending suggestions、scheduler run。
- “扫描当天已下发报价”：保留为人工补扫入口。
- 如果自动调度失败，显示醒目的错误提示，但不阻断人工补扫。

## 审计要求

必须能回答：

- 当天自动调度是否启动。
- 是哪个调度批次触发。
- 计划触发时间是多少。
- 实际开始和结束时间是多少。
- 扫描到多少已下发报价单。
- 创建了多少 Agent 复核 run。
- 跳过了多少重复记录。
- 失败了多少单，失败原因是什么。
- 后续人工是否采纳、执行、确认建议。

Agent 建议本身继续使用：

- `agent_suggestions`
- `agent_suggestion_events`

调度批次只记录“调度执行结果”，不替代建议审计。

## 失败策略

首版失败处理：

- 单张报价复核失败：记录到 `failed_count` 和 `result_json.failures`，继续处理后续报价。
- 整个调度异常：调度批次标记为 `failed`，记录 `error_message`。
- 调度失败不自动重试无限次。
- 管理员可通过页面人工点击“扫描当天已下发报价”补扫。
- 因为每张报价按 `quote_history.id` 去重，补扫不会重复生成已成功的复核结果。

## 验收标准

### 后端

- 关闭 `FEATURE_AGENT_DAILY_REVIEW` 时，后台调度不启动或不执行。
- 到达 `AGENT_DAILY_REVIEW_RUN_TIME` 后，调度自动调用 `run_daily_quote_review`。
- 同一个业务日不会重复自动执行调度批次。
- 同一个 `quote_history.id` 不会重复创建自动复核 run。
- 单条失败不影响后续报价继续复核。
- 调度运行结果可通过 API 查询。
- 服务重启后，在 `CATCHUP_MINUTES` 内可补跑当天未执行批次。

### 前端

- AI助手中心能看到今日计划时间和调度状态。
- 自动执行成功后，概览的候选数、复核数、待处理建议数更新。
- 自动执行失败时，页面能看到失败状态和错误摘要。
- 人工补扫入口仍可使用。
- 表格和按钮不出现文字重叠或溢出。

### 安全

- 调度只创建复核结果和建议。
- 调度不执行建议。
- 调度不改价。
- 调度不下发。
- 调度不启用成本库 active。

## 实施切片

### 2-3-1 调度运行记录

- 新增 `AgentSchedulerRun` 模型。
- 新增 Alembic 表 `agent_scheduler_runs`。
- 新增序列化函数和查询服务。
- 补后端测试。

### 2-3-2 调度循环服务

- 新增 `app/services/agent_daily_scheduler.py`。
- 实现时间判断、补跑窗口、进程锁、数据库批次记录。
- 在 FastAPI lifespan 中启动和关闭调度任务。
- 保持所有实际复核逻辑复用 `run_daily_quote_review`。

### 2-3-3 API 与前端状态

- 新增 scheduler run 查询 API。
- AI助手中心展示调度状态。
- 自动调度失败时显示可见提醒。
- 保留人工补扫按钮。

### 2-3-4 验证与试运行

- 后端专项测试。
- 前端构建。
- 当前环境设置 `AGENT_DAILY_REVIEW_RUN_TIME` 为接下来几分钟做一次真实触发验收。
- 验收后恢复为 `18:30`。
- 记录试运行结果。

## 建议执行顺序

先做 `2-3-1 + 2-3-2`，跑通后台自动触发和调度批次记录。

然后做 `2-3-3`，让 AI助手中心显示调度状态。

最后做 `2-3-4`，用临时时间做当前环境手工验收。
