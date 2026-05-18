# API-CONTRACTS｜升级路线新增接口契约骨架
> 创建日期：2026-05-15
> 状态：Phase 0 接口已完成当前环境验证；Phase 1 报价速度看板代码层已完成，正式生产上线待单独 Runbook
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 目标

本文件固定新增接口的命名、权限、功能开关、错误码和重复调用边界。实施时每个接口必须补齐请求体、响应体、分页、排序和测试用例。

## 通用响应

继续沿用现有 `api_ok` / `api_page` 响应风格。

错误响应必须包含稳定错误码：

```json
{
  "ok": false,
  "error_code": "PERMISSION_DENIED",
  "message": "无权限访问",
  "trace_id": "..."
}
```

时间字段统一返回 ISO 8601 字符串，并带时区信息。业务看板所有日期分组按 `Asia/Shanghai` 计算；响应中如包含 `timezone` 字段，固定为 `Asia/Shanghai`。

## HTTP 状态码与错误码

| HTTP | error_code | 场景 | 前端动作 |
|------|------------|------|----------|
| 401 | AUTH_REQUIRED | 未登录或登录过期 | 清会话并跳转登录 |
| 401 | ROLE_VERSION_EXPIRED | 角色变更、账号禁用或旧 token 缺少 `role_version` | 清会话，提示重新登录 |
| 403 | DINGTALK_VERIFY_REQUIRED | 需要钉钉登录二次验证 | 弹钉钉验证流程，不清登录态 |
| 403 | PERMISSION_DENIED | 权限不足 | 显示无权限 |
| 403 | FILE_ACCESS_DENIED | 文件无权访问 | 显示文件无权访问 |
| 403 | FEATURE_DISABLED | 功能开关未开启 | 显示“功能未开启” |
| 404 | RESOURCE_NOT_FOUND | 资源不存在 | 显示不存在或返回列表 |
| 409 | IMPORT_DUPLICATE_DETECTED | 导入重复数据 | 显示重复导入提示 |
| 409 | STATE_CONFLICT | 状态机不允许该动作 | 显示当前状态不可操作 |
| 422 | VALIDATION_ERROR | 请求参数非法 | 显示字段错误 |
| 502 | AI_PARSE_FAILED | AI 输出解析失败 | 显示 AI 结果异常 |
| 502 | TRANSCRIPTION_FAILED | 语音转写失败 | 显示转写失败 |
| 503 | AI_PROVIDER_UNAVAILABLE | AI / RAG / 转写服务不可用 | 显示服务暂不可用 |

## 阶段 0：权限与登录（✅ 2026-05-18 已完成当前环境验证）

实现状态：

- 已实现并完成当前环境验证 `/api/v1/auth/login`、`/api/v1/auth/me`、`/api/v1/admin/users`、角色授予 / 撤销、权限历史和钉钉验证占位接口。
- 已验证旧 token 缺少 `role_version` 时返回 401。
- 已验证授权 / 撤销写入 `user_role_events`，并递增 `users.role_version`。
- `PUBLIC_ACCESS_ENABLED=false` 时钉钉二次验证不阻塞内网 Phase 0；公网入口仍未开启。

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/auth/login` | POST | 公开 | 无 | 登录；公网 admin/system_admin 需钉钉二验 |
| `/api/v1/auth/me` | GET | 登录 | 无 | 返回 `roles`、兼容 `role`、`role_version` |
| `/api/v1/admin/users` | GET | system_admin | 无 | 用户列表，供权限管理页面展示 |
| `/api/v1/admin/users/{id}/roles` | POST | system_admin | 无 | 授予角色 |
| `/api/v1/admin/users/{id}/roles/{role}/revoke` | POST | system_admin | 无 | 撤销角色，必须携带撤权备注 |
| `/api/v1/admin/users/{id}/role-events` | GET | system_admin | 无 | 查看授权历史 |
| `/api/v1/auth/dingtalk/verify` | POST | admin/system_admin | PUBLIC_ACCESS_ENABLED | 钉钉登录二次验证 |

Phase 0 已补齐并上线的字段级 Schema：

`GET /api/v1/auth/me` 最低响应字段：

- `id`
- `username`
- `role`
- `roles`
- `role_version`
- `dingtalk_bound`
- `dingtalk_verified_until`
- `available_modules`

`POST /api/v1/admin/users/{id}/roles` 最低请求字段：

- `role`
- `note`

`POST /api/v1/admin/users/{id}/roles/{role}/revoke` 最低请求字段：

- `note`
- `trace_id`

`GET /api/v1/admin/users` 最低响应字段：

- `id`
- `username`
- `role`
- `roles`
- `role_version`
- `is_active`
- `dingtalk_bound`
- `available_modules`
- `last_login_at`
- `created_at`

`GET /api/v1/admin/users/{id}/role-events` 最低响应字段：

- `id`
- `target_user_id`
- `role`
- `action`
- `operator_id`
- `created_at`
- `ip_address`
- `user_agent`
- `trace_id`
- `note`

## 阶段 1：报价速度看板（✅ 代码层已完成，自动化验证通过）

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/admin/dashboard/quote-speed` | GET | admin / system_admin / viewer | FEATURE_DASHBOARD_QUOTE | 报价速度聚合 |

查询参数：

- `range: today / week / month / last_30_days`，默认 `last_30_days`

Phase 1 已补齐 `quote-speed` 响应 Schema：

- `timezone: Asia/Shanghai`
- `range: today / week / month / last_30_days`
- `range_start`
- `range_end`
- `sample_count`
- `completed_count`
- `confirmed_count`
- `feedback_sample_count`
- `modified_count`
- `ai_duration_avg_ms`
- `manual_confirm_duration_avg_ms`
- `total_delivery_duration_avg_ms`
- `modified_rate`
- `daily_trends`
- `status_distribution`
- `empty_state`
- `low_sample_warning`

## 阶段 2：响应速度

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/admin/dashboard/response-speed` | GET | admin / system_admin / viewer | FEATURE_DASHBOARD_RESPONSE | 响应速度聚合 |
| `/api/v1/quote/jobs` | POST | staff / admin / system_admin | 既有报价开关 | 创建报价时可附带咨询来源字段 |
| `/api/v1/client-inquiries` | GET | staff / admin / system_admin | FEATURE_CLIENT_INQUIRY | 查询咨询记录；staff 仅自己的 |
| `/api/v1/client-inquiries/{id}` | PATCH | staff / admin / system_admin | FEATURE_CLIENT_INQUIRY | 修正来源、客户信息、首次响应时间；staff 仅自己的 |

`ClientInquiry` 表示一次业务咨询，`QuoteJob` 表示一次技术报价尝试。报价任务基础参数和文件基础校验通过后创建或复用 `ClientInquiry`；请求不合法、用户未授权或附件完全不可读时不创建。AI / RAG / N8N 后续失败不删除咨询记录。

`QuoteJob` 重试时继承原 `client_inquiry_id`、`inquiry_time`、`time_source` 和来源字段，不创建新的 `ClientInquiry`。

Phase 2 开工前必须补齐字段级 Schema：

`POST /api/v1/quote/jobs` 可选咨询字段：

- `client_inquiry_id`
- `source`
- `client_name`
- `client_phone`
- `inquiry_time`
- `time_source: manual / default / integration`
- `notes`

`GET /api/v1/client-inquiries` 查询参数：

- `date_from`
- `date_to`
- `source`
- `responder_id`
- `time_source`
- `has_quote_job`
- `page`
- `page_size`

`PATCH /api/v1/client-inquiries/{id}` 可修改字段：

- `source`
- `client_name`
- `client_phone`
- `inquiry_time`
- `first_response_time`
- `time_source`
- `notes`

`GET /api/v1/admin/dashboard/response-speed` 最低响应字段：

- `timezone: Asia/Shanghai`
- `sample_count_total`
- `sample_count_in_avg`
- `sample_count_excluded_default_time`
- `avg_first_response_minutes`
- `sla_pass_rate`
- `by_source`
- `by_responder`
- `overdue_count`
- `empty_state`
- `low_sample_warning`

## 阶段 3：执行任务

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/execution-tasks` | POST | admin / system_admin | FEATURE_EXECUTION | 创建任务 |
| `/api/v1/execution-tasks` | GET | admin / system_admin / staff / manager | FEATURE_EXECUTION | 列表查询，staff / manager 仅分配给自己的 |
| `/api/v1/execution-tasks/{id}` | PATCH | admin / system_admin / staff / manager | FEATURE_EXECUTION | 更新任务；staff / manager 仅自己的进度和备注 |
| `/api/v1/execution-tasks/{id}/cancel` | POST | admin / system_admin | FEATURE_EXECUTION | 取消任务，`reason` 必填 |
| `/api/v1/admin/dashboard/execution-speed` | GET | admin / system_admin / viewer | FEATURE_DASHBOARD_EXECUTION | 执行速度聚合 |

`staff` / `manager` 更新自己任务时，仅允许修改进度白名单字段：`status`、`completed_at`、`notes`。其中 `status` 只允许 `pending -> in_progress`、`in_progress -> done`、`pending -> done`；`completed_at` 只允许在进入 `done` 时写入，推荐由后端自动填充。修改负责人、截止时间、来源字段必须由 `admin` / `system_admin` 完成。

`POST /api/v1/execution-tasks/{id}/cancel` 必须写入 `execution_task_events`，并记录 `reason`、`operator_id`、`trace_id`。

## 阶段 4：会议与转写

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/meetings` | POST | staff / manager / admin / system_admin | FEATURE_MEETING_AI | 保存纪要并触发 AI 提取 |
| `/api/v1/meetings/{id}` | PATCH | 创建人 / admin / system_admin | FEATURE_MEETING_AI | 草稿阶段更正纪要并重新提取 |
| `/api/v1/meetings/{id}/revisions` | POST | admin / system_admin | FEATURE_MEETING_AI | 已确认后创建 revision，不覆盖原纪要 |
| `/api/v1/meetings/{id}/cancel` | POST | 创建人 / admin / system_admin | FEATURE_MEETING_AI | 作废 draft 纪要，关联草稿置为 rejected |
| `/api/v1/meetings/{id}/confirm-tasks` | POST | staff / manager / admin / system_admin | FEATURE_MEETING_AI | 确认草稿写入任务 |
| `/api/v1/meetings/transcribe` | POST | staff / manager / admin / system_admin | FEATURE_AUDIO_TRANSCRIPTION | 上传音频转写 |
| `/api/v1/meetings/transcribe/{job_id}` | GET | 登录且有权限 | FEATURE_AUDIO_TRANSCRIPTION | 查询转写任务 |
| `/api/v1/meetings/import-dingtalk` | POST | admin / system_admin | FEATURE_MEETING_AI | 钉钉会议导入 |

`confirm-tasks` 依赖 `execution_tasks` 表已存在，但不受 `FEATURE_EXECUTION=false` 阻断；该开关只控制独立任务管理 UI 和任务 CRUD 入口。

## 阶段 5：知识导入

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/admin/knowledge/import/preview` | POST | admin / system_admin | 无 | 知识导入预览 |
| `/api/v1/admin/knowledge/import/confirm` | POST | admin / system_admin | 无 | 确认导入到 candidates；成功后触发 RAG reload |
| `/api/v1/admin/knowledge/import/{batch_id}/rollback` | POST | admin / system_admin | 无 | 批次回滚 |
| `/api/v1/admin/knowledge/status` | GET | admin / system_admin | 无 | 返回 rag_reload / rag_eval 最新状态和待处理标记 |

批次 confirm 后如有数据进入 `materials` 或 RAG 文档，自动 enqueue `rag_reload`。`rag_reload` 成功后自动 enqueue `rag_eval`，结果写入评测报告。reload / eval 失败不回滚导入，但必须告警并在知识库页面显示待处理状态。

`GET /api/v1/admin/knowledge/status` 最低响应字段：

- `reload_status: queued / running / succeeded / failed / timed_out / null`
- `eval_status: queued / running / succeeded / failed / timed_out / null`
- `pending_reload: boolean`
- `last_reload_error`
- `last_eval_error`
- `last_successful_reload_at`
- `last_successful_eval_at`
- `latest_eval_report_id`
- `trace_id`

## 阶段 6：经营数据

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/admin/dashboard/business` | GET | admin / system_admin / viewer | FEATURE_DASHBOARD_BUSINESS | 经营汇总，viewer 脱敏 |
| `/api/v1/projects` | POST/GET/PATCH | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 项目管理 |
| `/api/v1/projects/{id}/archive` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 归档项目，写审计 |
| `/api/v1/projects/{id}/unarchive` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 取消归档，写审计 |
| `/api/v1/contracts` | POST/GET/PATCH | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 合同管理 |
| `/api/v1/contracts/{id}/sign` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 合同签约，进入有效合同金额 |
| `/api/v1/contracts/{id}/archive` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 合同归档，仍计入有效合同金额 |
| `/api/v1/contracts/{id}/cancel` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 合同作废，`reason` 必填 |
| `/api/v1/contract-adjustments` | POST/GET/PATCH | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 增减项 / 优惠 / 签证 |
| `/api/v1/contract-adjustments/{id}/confirm` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 确认调整项，金额锁定 |
| `/api/v1/contract-adjustments/{id}/cancel` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 作废调整项，`reason` 必填 |
| `/api/v1/payments` | POST/GET/PATCH | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 回款管理 |
| `/api/v1/payments/{id}/mark-paid` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 确认回款，写入 `paid_at` |
| `/api/v1/payments/{id}/cancel` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 作废回款，`reason` 必填 |
| `/api/v1/project-costs` | POST/GET/PATCH | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 成本管理 |
| `/api/v1/project-costs/{id}/cancel` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 作废成本记录，`reason` 必填 |
| `/api/v1/business/import/preview` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 经营数据导入预览 |
| `/api/v1/business/import/confirm` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 导入确认 |
| `/api/v1/business/import/{batch_id}/rollback` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 批次回滚 |
| `/api/v1/business/export` | POST | admin / system_admin | FEATURE_DASHBOARD_BUSINESS | 导出 Excel，必须水印和审计 |

所有经营 `cancel` 动作请求体最低字段：

- `reason`
- `trace_id`

取消原因不重复写入各业务实体的 `cancel_reason` 字段；当前阶段统一写入 `business_events.notes`，并在 `before_json` / `after_json` 中保留状态变化快照。执行任务取消原因写入 `execution_task_events.reason`。

## AI 治理管理接口

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/admin/ai-invocations/{id}/raw-input-url` | GET | system_admin | 无 | 查看 AI 原始输入短时 URL，公网需钉钉验证 |
| `/api/v1/admin/ai-invocations/{id}/raw-output-url` | GET | system_admin | 无 | 查看 AI 原始输出短时 URL，公网需钉钉验证 |

AI 原文查看属于高风险操作，必须写审计事件；若 RBAC 配置要求单次挑战，则接口调用前必须存在 verified 的 `admin_action_challenges`。

AI 原文短时 URL 响应字段：

- `url`
- `expires_in_seconds`（默认 300）
- `object_id`
- `raw_log_status`
- `trace_id`

短时 URL 仅用于一次受控预览或下载，不得复用普通 `/files/{id}/download_url`，也不得长期缓存到前端本地存储。

## 附录 A：样板图报价

| 接口 | 方法 | 权限 | 功能开关 | 说明 |
|------|------|------|----------|------|
| `/api/v1/quote/analyze-image` | POST | staff / admin / system_admin | FEATURE_IMAGE_QUOTE | 上传效果图识别 |
| `/api/v1/quote/analyze-image/{id}` | GET | 登录且有权限 | FEATURE_IMAGE_QUOTE | 查询识别结果 |
| `/api/v1/quote/analyze-image/{id}/events` | GET | 登录且有权限 | FEATURE_IMAGE_QUOTE | SSE 进度 |
| `/api/v1/quote/build-materials-table` | POST | staff / admin / system_admin | FEATURE_IMAGE_QUOTE | 面积 -> 需求表 |

## 导出水印

经营数据和合同明细导出的 Excel 必须包含水印字段：

- 导出人
- 导出时间
- 导出用途
- 系统名称
- trace_id

导出操作必须写审计事件，公网访问时必须存在当天有效的钉钉登录验证。

## 重复调用语义

| 动作 | 重复调用 | 冲突场景 |
|------|----------|----------|
| `contract-adjustments/{id}/confirm` | 已 confirmed 返回 `200 + 当前对象` | cancelled 后 confirm 返回 409 |
| `contract-adjustments/{id}/cancel` | 已 cancelled 返回 `200 + 当前对象` | 缺少 `reason` 返回 422 |
| `admin/users/{id}/roles` | 已拥有该角色返回 `200 + 当前角色列表` | 缺少 `note` 返回 422 |
| `admin/users/{id}/roles/{role}/revoke` | 已无该角色返回 `200 + 当前角色列表` | 撤销最后一个 `system_admin` 或缺少 `note` 返回 409 / 422 |
| `projects/{id}/archive` | 已归档返回 `200 + 当前对象` | 无 |
| `projects/{id}/unarchive` | 未归档返回 `200 + 当前对象` | 无 |
| `contracts/{id}/sign` | 已 signed 返回 `200 + 当前对象` | cancelled 后 sign 返回 409 |
| `contracts/{id}/archive` | 已 archived 返回 `200 + 当前对象` | draft / cancelled 状态 archive 返回 409 |
| `contracts/{id}/cancel` | 已 cancelled 返回 `200 + 当前对象` | 缺少 `reason` 返回 422 |
| `payments/{id}/mark-paid` | 已 paid 返回 `200 + 当前对象` | cancelled 后 mark-paid 返回 409 |
| `payments/{id}/cancel` | 已 cancelled 返回 `200 + 当前对象` | 缺少 `reason` 返回 422 |
| `project-costs/{id}/cancel` | 已 cancelled 返回 `200 + 当前对象` | 缺少 `reason` 返回 422 |
| `business/import/confirm` | 已 confirmed 返回 `200 + 已确认结果` | rolled_back 后 confirm 返回 409 |
| `business/import/{batch_id}/rollback` | 已 rolled_back 返回 `200 + 当前结果`；rollback_failed 后再次调用表示重试 | preview 状态 rollback 返回 409 |
| `meetings/{id}/confirm-tasks` | 已生成任务返回 `200 + 已生成任务列表` | rejected 草稿重新确认返回 409 |
| `meetings/{id}/cancel` | 已 cancelled 返回 `200 + 当前对象` | confirmed 后 cancel 返回 409 |
| `execution-tasks/{id}/cancel` | 已 cancelled 返回 `200 + 当前对象` | 已 done 后 cancel 返回 409 |
| `quote/jobs/{id}/retry` | 服务端应防重复创建；同一源 job 同一时间只允许一个 active retry | 源 job 不存在或无权访问返回 404 / 403 |

状态机详见 [STATE-MACHINES.md](STATE-MACHINES.md)，功能开关依赖详见 [FEATURE-FLAGS.md](FEATURE-FLAGS.md)。
