# ADR-AI-Governance｜AI 调用治理、脱敏与可追溯
> 创建日期：2026-05-14
> 状态：规划中
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 决策摘要

系统会调用 DeepSeek、GLM、讯飞 / 阿里云、RAG 服务等 AI 或外部智能服务。会议纪要、录音转写、效果图、历史成交数据和报价上下文可能包含客户信息、价格、合同内容。必须在设计阶段明确数据外发、日志保留、脱敏和复盘策略。

默认策略：保存结构化摘要和可追溯元数据，不长期保存原始输入输出。原始输入输出只在调试期开启，当前默认保留 7 天，后续可按需要调整，但最长不超过 30 天，并仅允许 `system_admin` 查看。

## “原始输入输出”的定义

AI 调用原始输入输出指每次模型调用时系统发送给模型的完整内容，以及模型返回的完整内容。

例子：

- 会议纪要拆任务：输入 = 系统 Prompt + 会议纪要文本；输出 = AI 提取的任务 JSON
- 效果图识别：输入 = 图片引用 + 识别 Prompt；输出 = 识别出的材料清单
- 报价优化：输入 = 用户需求 + RAG 检索结果 + Prompt；输出 = AI 报价建议
- 语音转写：输入 = 音频文件；输出 = 转写文本

这些原文可能包含客户姓名、手机号、地址、合同金额、工程价格、录音文本，因此不能无边界长期保存。

## 数据外发规则

| 数据类型 | 可否发外部 AI | 要求 |
|---------|---------------|------|
| 普通报价需求 | 可以 | 去除非必要手机号、身份证、详细地址 |
| RAG 检索片段 | 可以 | 仅发送报价需要的片段，不发送整库 |
| 会议纪要 | 可以 | 尽量脱敏客户手机号、身份证、详细地址 |
| 会议录音 | 可以发转写服务 | 仅用于转写，不用于模型训练；优先选择企业服务条款明确的供应商 |
| 效果图 / 样板图 | 可以 | 不附带客户身份信息 |
| 合同文件全文 | 默认不发 | 需 system_admin 审批并记录审计 |
| 回款 / 成本明细 | 默认不发 | 只允许汇总或脱敏摘要进入 AI |
| 历史成交数据 | 可以治理导入 | 先进入 `knowledge_candidates`，不得直接污染材料库 |

历史成交数据治理导入规则：

- 批量导入至 `knowledge_candidates` 可由 `admin` / `system_admin` 执行
- 候选数据审核通过并写入 `materials` / RAG 文档 / 案例库时，必须由 `admin` / `system_admin` 确认
- 批量确认、批量回滚和整批删除属于高风险操作；公网访问时必须存在当天有效钉钉验证
- 每次确认、驳回、回滚必须写入知识治理审计事件；若当前没有专用审计表，先写入 `business_events` 或 `knowledge_candidate_events`，字段必须包含 `operator_id`、`source_batch_id`、`target_type`、`target_id`、`before_json`、`after_json`、`ip_address`、`user_agent`、`trace_id`
- AI 只参与结构化和分类建议，不能自动确认候选数据入库

## 调用日志模型

优先扩展现有 `model_call_logs`，如字段不足再新增 `ai_invocations`。统一记录：

```
ai_invocations / model_call_logs:
- id, created_at
- task_type: quote / meeting_extract / transcription / image_analysis / rag_reload / rag_eval / prompt_regression
- provider: deepseek / zhipu / iflytek / aliyun / rag_service / other
- model_name
- prompt_version
- workflow_version: nullable
- dify_app_version: nullable
- redaction_policy: nullable
- user_id: FK -> users.id, nullable
- source_ref_type, source_ref_id
- input_summary
- output_summary
- input_sha256
- output_sha256
- raw_input_object_id: FK -> file_objects.file_id, nullable
- raw_output_object_id: FK -> file_objects.file_id, nullable
- raw_log_status: not_saved / saved / cleaned / save_failed
- raw_logs_cleaned_at: nullable
- raw_log_error: nullable
- latency_ms
- status: succeeded / failed / timed_out / canceled
- error_message
- token_usage_json
- ip_address
- user_agent
- trace_id
```

`input_summary` / `output_summary` 用于长期保留。`raw_input_object_id` / `raw_output_object_id` 仅在调试期开启。`raw_log_status` 用于区分“从未保存原文”、“已保存”、“已清理”和“启用保存但保存失败”。`user_id` 与其他审计表保持一致；系统自动触发或无法归属到具体用户的调用允许为 NULL，但必须通过 `source_ref_type` / `source_ref_id` 和 `trace_id` 追踪来源。

报价优化需要同时记录 Dify 应用版本、工作流版本和 Prompt 版本，不拼接进单一字符串：

- `dify_app_version`：Dify 应用或发布版本
- `workflow_version`：N8N / Dify workflow 版本
- `prompt_version`：当前模型 Prompt 版本，遵循下文统一格式

质量评估按三元组 `(dify_app_version, workflow_version, prompt_version)` 聚合；缺失任一版本时，该调用不得进入正式回归对比样本。

## 原文保留策略

配置项：

```env
AI_RAW_LOG_ENABLED=false
AI_RAW_LOG_RETENTION_DAYS=7
AI_RAW_LOG_MAX_RETENTION_DAYS=30
AI_RAW_LOG_BUCKET=ai-raw-logs
```

规则：

- 默认不保存原始输入输出，只保存摘要、hash、模型信息、耗时、错误
- 调试期可开启 `AI_RAW_LOG_ENABLED=true`
- 原文存储到受权限保护的对象存储，不直接写入普通日志文件
- 原文默认 7 天自动清理，最长 30 天
- 原文查看仅限 `system_admin`，公网访问时需钉钉二次验证
- 清理任务只删除原文对象，保留摘要和 hash，便于后续证明调用发生过

原文对象存储规则：

- 原文对象使用独立 bucket：`AI_RAW_LOG_BUCKET=ai-raw-logs`，不得与合同、录音、效果图等长期业务文件混用生命周期策略
- `raw_input_object_id` / `raw_output_object_id` 指向 `file_objects.file_id`，对象 key 建议形如 `ai-raw-logs/{yyyy}/{mm}/{invocation_id}/input.json`
- `file_objects.provider` 继续使用统一对象存储抽象，可为 `minio` / `aliyun_oss` / `tencent_cos`
- 原文查看不得复用普通 `/files/{id}/download_url` 下载入口，必须走专用管理接口，例如 `GET /api/v1/admin/ai-invocations/{id}/raw-input-url` / `raw-output-url`
- 专用查看接口仅允许 `system_admin`，公网访问时必须存在当天有效钉钉验证，并写入审计事件
- 清理后删除对象并清空 `raw_input_object_id` / `raw_output_object_id`，同时保留 hash、summary、`raw_log_status='cleaned'` 和 `raw_logs_cleaned_at`
- 如果 `AI_RAW_LOG_ENABLED=true` 但原文对象上传失败，业务调用记录仍必须落库，`raw_log_status='save_failed'`，`raw_log_error` 记录失败摘要，`raw_input_object_id` / `raw_output_object_id` 保持 NULL。不得把上传失败伪装成 `not_saved`。

原文查看接口：

- `GET /api/v1/admin/ai-invocations/{id}/raw-input-url`
- `GET /api/v1/admin/ai-invocations/{id}/raw-output-url`

接口只返回短时签名 URL，不直接透传对象内容。响应至少包含 `url`、`expires_in_seconds`、`object_id`、`raw_log_status` 和 `trace_id`；默认有效期 300 秒。权限仅限 `system_admin`；公网访问时必须存在当天有效钉钉登录验证。查看 AI 原始输入输出属于高风险操作，若 RBAC 配置将其提升为单次挑战，则还必须通过 `admin_action_challenges`。每次生成 URL 必须写审计事件，记录 invocation id、对象类型、operator、IP、user agent 和 trace_id。

`AI_RAW_LOG_RETENTION_DAYS` 上限强制：

- 应用启动时配置校验：若 `AI_RAW_LOG_RETENTION_DAYS > AI_RAW_LOG_MAX_RETENTION_DAYS`，启动失败或 `/health/ready` 返回 degraded，不允许静默使用超限配置。
- 清理任务运行时再次防御性 clamp：`effective_retention_days = min(AI_RAW_LOG_RETENTION_DAYS, AI_RAW_LOG_MAX_RETENTION_DAYS)`，并记录 warning。

## 原文清理任务

新增 Celery beat 定时任务 `cleanup_ai_raw_logs`，每天 `Asia/Shanghai` 00:30 执行一次。调度时间与 [ADR-AsyncJob.md](ADR-AsyncJob.md) 的 Beat 任务调度表保持一致。

清理规则：

- 读取 `AI_RAW_LOG_RETENTION_DAYS`，当前默认 7 天
- 删除超过保留期的 `raw_input_object_id` / `raw_output_object_id` 对应对象
- 将记录中的 `raw_input_object_id` / `raw_output_object_id` 置空
- 将 `raw_log_status` 更新为 `cleaned`，并写入 `raw_logs_cleaned_at`
- 保留 `input_summary`、`output_summary`、`input_sha256`、`output_sha256`、模型信息、耗时、错误信息
- 每次清理写入审计日志：清理数量、失败数量、开始时间、结束时间
- 清理失败不得影响主业务，但 `/health/ready` 或运维看板应能看到最近一次清理状态

单条对象删除失败规则：

- 任一原文对象删除失败时，该条记录不得置为 `cleaned`，继续保持 `raw_log_status='saved'`
- `raw_log_error` 写入最近一次删除失败摘要和对象 ID
- 下一次 `cleanup_ai_raw_logs` 继续重试该条记录
- 单次清理存在失败时，运维看板显示 warning；连续 3 次清理均存在失败，或最早失败对象超过 24 小时未清理时，`/health/ready` 返回 degraded
- 删除部分成功、部分失败时，不清空失败对象对应字段；已删除对象字段可置空，但整条记录仍保持 `saved`，直到所有原文对象清理完成后再置为 `cleaned`

如果 `AI_RAW_LOG_ENABLED=false`，清理任务仍可运行，但只处理历史遗留原文对象。

状态区分：

- `not_saved`：本次调用从未保存原始输入输出
- `saved`：原文对象仍在对象存储中，可按权限查看
- `cleaned`：原文曾保存过，已按保留策略清理；可用 `raw_logs_cleaned_at` 证明清理时间
- `save_failed`：本次调用尝试保存原文但上传对象失败；可用 `raw_log_error` 追踪原因

## Prompt 版本与复盘

所有 AI 功能必须有 `prompt_version`。Prompt 修改后需能比较修改前后的指标。

`prompt_version` 统一格式：

```text
<task_type>@<semver>+<yyyymmdd>
```

示例：`quote@1.3.0+20260515`、`meeting_extract@1.0.0+20260515`。`task_type` 必须与调用日志中的 `task_type` 保持一致；`semver` 用于判断功能内版本顺序；日期用于快速定位上线批次。禁止使用不可排序的自由文本版本号，例如 `new-prompt`、`final2`。如需描述实验目的，写入单独的 `prompt_note` 或评测报告，不放进 `prompt_version`。

最低要求：

- 会议任务拆解记录 prompt_version 和任务提取成功率
- 效果图识别记录 prompt_version、召回率、精确率
- RAG 评测记录 Hit@K、MRR、评测集版本
- 报价优化记录 DIFY / workflow / prompt 版本

会议任务提取成功率公式：

```text
task_extract_success_rate = accepted_task_drafts_count / extracted_task_drafts_count
```

统计口径：

- 分母：指定时间范围内 AI 成功解析出的 `task_drafts` 数量，不含解析失败且未生成草稿的会议。
- 分子：其中最终进入 `accepted` 并创建 `execution_tasks` 的草稿数量。
- 辅助指标：`meeting_parse_success_rate = meetings_with_at_least_one_task_draft / submitted_meetings_count`，用于观察模型是否完全提取不到任务。
- 所有指标必须按 `prompt_version` 分组，低样本量时标记 `low_sample_warning`。

## 脱敏规则

脱敏必须在统一的 AI 调用入口执行，例如 `model_gateway` / `ai_client` 的预处理层。各业务调用点可以提供上下文策略，但不得绕过统一脱敏函数直接调用外部 AI。

进入 AI 前按以下规则脱敏：

- 手机号保留前三后四，中间替换为 `****`
- 身份证、银行卡、详细住址默认删除
- 合同文件和回款明细默认只传摘要
- 客户姓名默认替换为“客户”

客户姓名只有在满足以下任一条件时才视为“必要”，允许保留：生成对客正式文书、合同模板套打、用户明确要求输出客户抬头。报价计算、会议任务拆解、RAG 检索、Prompt 回归和内部经营分析均不需要客户姓名，应替换为“客户”。

脱敏不应破坏报价必需信息。例如面积、材料、工艺、地区差异、合同金额、成本金额在经营分析中可能必要，但发送给通用大模型时优先使用聚合摘要。每个 AI 调用日志应记录 `redaction_policy` 或等效字段，用于追溯采用的脱敏策略版本。

## 验收要求

- 每类 AI 调用都能追溯 provider、model_name、prompt_version、耗时、状态
- 默认关闭原文保存时，日志中无完整会议纪要、合同全文、手机号明文
- 开启原文保存后，原文对象 7 天后被清理
- `cleanup_ai_raw_logs` 每日运行并记录清理数量
- system_admin 以外角色无法查看原文
- 合同文件全文发外部 AI 前必须有审计事件
- Prompt 修改后可按版本对比质量指标
