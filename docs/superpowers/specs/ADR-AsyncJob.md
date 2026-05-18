# ADR-AsyncJob｜后台异步任务边界
> 创建日期：2026-05-14
> 状态：规划中
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 决策摘要

`ExecutionTask` 表示人要完成的业务任务，`AsyncJob` 表示系统后台异步任务。两者必须分清，避免把 Celery 后台任务、报价任务、会议待办混在同一个概念里。

当前阶段允许语音转写临时复用 `quote_jobs + job_type`，因为它与报价任务状态机完全一致。若非报价类异步任务超过 2 类，或需要统一任务中心展示所有后台任务，再新增 `async_jobs` / `async_job_events`。

阶段 4 的会议任务确认依赖阶段 3 的 `execution_tasks` schema，但不依赖 `FEATURE_EXECUTION=true`。`FEATURE_EXECUTION` 控制独立任务管理 UI，`FEATURE_MEETING_AI` 控制会议纪要到任务的写入流程。

## 当前策略

`quote_jobs.job_type`：

| job_type | 含义 |
|----------|------|
| quote | 现有报价异步任务 |
| transcription | 语音转写任务 |

状态机复用现有状态：

```text
queued -> running -> succeeded / failed / timed_out / canceled
```

转写查询接口使用 `quote_jobs.job_id` UUID，不暴露内部整数 id。

## 超时策略

新增 `quote_jobs.timeout_at`，创建异步任务时写入任务自己的超时时间，不在扫描时临时用统一阈值计算。

建议配置：

```env
QUOTE_JOB_TIMEOUT_MINUTES=30
IMAGE_ANALYSIS_TIMEOUT_MINUTES=30
RAG_RELOAD_TIMEOUT_MINUTES=60
RAG_EVAL_TIMEOUT_MINUTES=30
TRANSCRIPTION_DEFAULT_TIMEOUT_MINUTES=240
TRANSCRIPTION_MIN_TIMEOUT_MINUTES=60
TRANSCRIPTION_MAX_TIMEOUT_MINUTES=480
```

转写任务如能读取音频时长：

```text
timeout_minutes = clamp(audio_duration_minutes * 3 + 30, min=60, max=480)
timeout_at = created_at + timeout_minutes
```

如无法读取音频时长，使用 `TRANSCRIPTION_DEFAULT_TIMEOUT_MINUTES=240`。

Alembic 要求：

- `ALTER TABLE quote_jobs ADD timeout_at`
- 建议索引：`(job_type, status, timeout_at)`
- 旧的 `queued` / `running` 数据按 migration 执行时刻回填：`timeout_at = migration_time + default_timeout_by_job_type`
- 已处于终态的数据可为空或按历史完成时间回填

回填基准选择说明：不使用 `created_at + timeout_minutes`，避免迁移时把已经运行较久但仍可能恢复的历史任务立即误杀；也不无限期放过旧任务，而是从 migration 执行时刻开始给一个完整默认窗口，后续由 `mark_async_job_timeouts` 接管。

## 超时扫描任务

主流程使用 Celery beat 定时执行 `mark_async_job_timeouts`：

```text
status IN ('queued', 'running') AND timeout_at < now()
```

该任务必须按 `job_type` 写入对应错误信息，并保持幂等。已处于终态的任务不得再次修改。

`rag_reload` 必须与 quote / transcription / image_analysis 一样写入独立 `timeout_at`。默认 `RAG_RELOAD_TIMEOUT_MINUTES=60`；超时后状态置为 `timed_out`，并在知识库页面展示“重新加载超时”。如超时时存在或后续产生 `pending_reload=true`，`process_pending_rag_reload` 负责重新排队，不由超时任务直接重试。

旧接口 `POST /api/v1/admin/quote/jobs/mark_timeouts` 保留为 `admin` 手动兜底，不再作为主流程。它必须复用 `mark_async_job_timeouts` 的同一服务函数，不得绕过 `timeout_at` 和状态机判断。稳定后可标记 deprecated，但 v20 不直接删除。

## 未来 async_jobs 结构

```
async_jobs:
- job_id: UUID 字符串
- job_type: quote / transcription / image_analysis / rag_reload / rag_eval / import / prompt_regression
- owner_user_id
- status: queued / running / succeeded / failed / timed_out / canceled
- stage
- progress
- source_ref_type, source_ref_id
- result_json
- error_message
- celery_task_id
- timeout_at
- created_at, started_at, finished_at

async_job_events:
- job_id
- event_type
- message
- payload_json
- created_at
```

触发拆表条件：

- 非报价类异步任务超过 2 类
- 管理员需要统一任务中心
- 不同 job_type 状态机出现明显分叉
- 需要跨模块统一取消、重试、优先级调度

## Celery beat 健康检查

阶段 3 的逾期提醒、`mark_async_job_timeouts`、`cleanup_ai_raw_logs` 都依赖 Celery beat。beat 必须是独立进程，运维三件套同步落地：

- `start_all.ps1` 启动 beat
- Windows 任务计划程序注册 `AI_MiddleOffice_CeleryBeat`
- `/health/ready` 检查 Redis heartbeat key

heartbeat 方案：

```text
beat 每 60 秒写入 celery_beat_alive
TTL = 90 秒
/health/ready 检查 key 是否存在
```

选择 Redis heartbeat 而非 PID 文件，是因为 PID 文件只能证明进程曾经启动，不能证明调度循环仍在工作。

## Beat 任务调度表

| 任务名 | 频率 | 作用 | 幂等要求 | 依赖服务 |
|--------|------|------|----------|----------|
| `celery_beat_heartbeat` | 每 60 秒 | 写入 Redis heartbeat key，供 `/health/ready` 检查 | 覆盖写入同一个 key | Redis |
| `mark_async_job_timeouts` | 每 5 分钟 | 标记 `queued` / `running` 且 `timeout_at < now()` 的 quote / transcription / image_analysis / rag_reload / rag_eval 任务为 `timed_out` | 已终态任务不得修改；重复扫描无副作用 | MySQL |
| `send_execution_overdue_reminders` | 每 1 小时 | 扫描逾期未完成 `execution_tasks` 并发送钉钉提醒 | 同一任务同一提醒窗口只发送一次 | MySQL / Redis / 钉钉 Webhook |
| `cleanup_ai_raw_logs` | 每天 00:30 | 清理超过保留期的 AI 原始输入输出对象 | 已清理对象跳过；保留摘要和 hash | MySQL / 对象存储 |
| `process_pending_rag_reload` | 每 5 分钟 | 如无 reload 正在运行且存在 `pending_reload`，启动 RAG reload | 同一时间只允许一个 reload | Redis / RAG 服务 / Milvus |

补充规则：

- `rag_reload` 正在运行时收到新的 reload 请求，应设置 `pending_reload=true`。
- `process_pending_rag_reload` 启动 reload 前必须获取 Redis 分布式锁 `rag_reload_lock`，并再次检查数据库中不存在 `queued` / `running` reload。
- Redis 锁 TTL 必须大于 `RAG_RELOAD_TIMEOUT_MINUTES`；锁异常丢失时，以数据库状态作为最终并发保护。
- 当前 reload 完成后必须立即检查 `pending_reload` 并启动下一次 reload，不等待下一轮 beat；`process_pending_rag_reload` 只是兜底。
- 所有 beat 任务必须写入最近执行时间、成功 / 失败状态和错误信息，供运维看板和 `/health/ready` 展示。

## 验收要求

- 报价任务和转写任务均可按 `job_type` 查询
- 转写状态值与报价任务状态值一致
- 不同 `job_type` 使用不同 `timeout_at`，2 小时录音不会被 30 分钟报价阈值误杀
- `mark_async_job_timeouts` 自动标记超时，旧 `mark_timeouts` 接口仅作为手动兜底
- Beat 任务调度表中的任务均可在运维看板查看最近执行状态
- beat 重启后能自动恢复
- `/health/ready` 能发现 beat 不工作
- 未来新增 `async_jobs` 时，旧 `quote_jobs` 仍保持兼容
