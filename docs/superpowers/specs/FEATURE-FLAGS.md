# FEATURE-FLAGS｜功能开关与阶段依赖矩阵
> 创建日期：2026-05-15
> 状态：Phase 0 开关已完成当前环境验证；Phase 1 报价速度看板已完成当前环境运行态验收；Phase 2 响应速度开关代码层已完成。系统完善前不正式投入生产使用。
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 目标

本文件固定功能开关、Schema 依赖、运行前置条件和 Vite 路由开放阶段。功能开关控制入口和行为，不得替代数据库迁移顺序。

当前环境状态（2026-05-18，非正式生产上线）：

- `FEATURE_VITE_FRONTEND=true` 已打开。
- `PUBLIC_ACCESS_ENABLED=false` 保持关闭。
- `FEATURE_DASHBOARD_QUOTE` 代码路径已完成，当前环境已完成运行态验收。
- `FEATURE_CLIENT_INQUIRY` / `FEATURE_DASHBOARD_RESPONSE` 代码路径已完成，默认仍保持关闭；当前环境 Alembic 已升级到 `20260514_0012 (head)`，开关打开和运行态验收待执行。
- Phase 3+ 相关执行、经营功能开关未启动。

## 功能开关矩阵

| 开关 | 阶段 | Schema / 服务依赖 | 开启前置条件 | 关闭时行为 |
|------|------|-------------------|--------------|------------|
| `FEATURE_VITE_FRONTEND` | 0（当前环境已验证） | Vite 构建产物、SPA fallback | `/login`、权限管理、旧路由冒烟通过 | 继续使用旧 `app.html` / `index.html` / `admin.html` |
| `FEATURE_DASHBOARD_QUOTE` | 1（代码层已完成） | `quote_jobs` / `quote_feedback` / `quote_history` | 报价速度接口验证通过，且有基线数据 | 看板显示功能未开启 |
| `FEATURE_CLIENT_INQUIRY` | 2（代码层已完成） | `client_inquiries`、`quote_jobs.client_inquiry_id` | 报价创建流程已接入咨询字段，当前环境需先执行 Alembic `20260514_0012` | 创建报价不写咨询记录，响应看板不可用 |
| `FEATURE_DASHBOARD_RESPONSE` | 2（代码层已完成） | `client_inquiries` | 有响应统计数据，且默认时间排除口径已验证 | 响应速度看板显示功能未开启 |
| `FEATURE_EXECUTION` | 3 | `execution_tasks` / `execution_task_events` | 任务 CRUD 和权限测试通过 | 隐藏任务管理 UI |
| `FEATURE_DASHBOARD_EXECUTION` | 3 | `execution_tasks` | 执行速度聚合接口可用 | 执行速度看板显示功能未开启 |
| `FEATURE_MEETING_AI` | 4a | `execution_tasks`、`meeting_notes`、`task_drafts` | AI JSON Schema、草稿确认流程可用 | 会议 AI 页面显示功能未开启 |
| `FEATURE_AUDIO_TRANSCRIPTION` | 4b | `FEATURE_MEETING_AI`、`quote_jobs.job_type`、`quote_jobs.timeout_at`、对象存储 | 转写服务账号、额度、回调/轮询可用 | 上传录音入口隐藏 |
| `FEATURE_DASHBOARD_BUSINESS` | 6 | 经营数据表、导入审计表 | 导入模板、权限脱敏、导出水印通过 | 经营驾驶舱显示功能未开启 |
| `FEATURE_IMAGE_QUOTE` | 附录 A | `file_objects`、`quote_image_analyses` | 图片识别 mock 和降级路径通过 | 样板图入口隐藏 |
| `FEATURE_IMAGE_QUOTE_GLM` | 附录 A | `FEATURE_IMAGE_QUOTE`、GLM-4V 配置或 `model_gateway` | true 时直连智谱 GLM-4V；false 时必须配置 `IMAGE_QUOTE_MODEL_PROVIDER` / `IMAGE_QUOTE_MODEL_ENDPOINT` / `IMAGE_QUOTE_MODEL_NAME` | 关闭直连 GLM-4V，改走 `model_gateway`；依赖缺失时健康检查 degraded |
| `PUBLIC_ACCESS_ENABLED` | 公网前 | HTTPS、Cookie、CSRF、CORS、文件签名 URL、钉钉二验 | 安全门槛全部通过 | 仅内网访问，不开放公网入口 |
| `AI_RAW_LOG_ENABLED` | 可选 | `ai_call_logs` 原文字段、清理任务 | 脱敏和 7 天游标清理通过 | 只记录脱敏摘要，不保存原文 |

## Phase 3 / Phase 4 依赖约定

Schema 层强依赖，Feature Flag 层解耦：

- `FEATURE_MEETING_AI` 依赖 `execution_tasks` 表已经存在。
- `FEATURE_MEETING_AI` 不依赖 `FEATURE_EXECUTION=true`。
- `FEATURE_EXECUTION=false` 时，`/meetings/{id}/confirm-tasks` 仍可在 `FEATURE_MEETING_AI=true` 下写入 `execution_tasks`。
- `FEATURE_EXECUTION` 只控制任务管理 UI 和独立任务 CRUD 入口是否开放。

这样可以先上线会议纪要闭环，再决定是否开放独立任务中心。

## 语音转写依赖约定

`FEATURE_AUDIO_TRANSCRIPTION=true` 前必须先开启 `FEATURE_MEETING_AI=true`。原因是转写结果需要进入 `meeting_notes -> task_drafts -> execution_tasks` 流程；没有会议 AI 主流程时，录音转写缺少业务落点。

启动检查规则：

- `FEATURE_AUDIO_TRANSCRIPTION=true` 且 `FEATURE_MEETING_AI=false` 时，服务启动应拒绝或 `/health/ready` 返回 degraded。
- 前端不得在 `FEATURE_MEETING_AI=false` 时展示上传录音入口。
- 后端转写接口在依赖未满足时返回 `403 FEATURE_DISABLED`。

## PUBLIC_ACCESS_ENABLED 前置检查

`PUBLIC_ACCESS_ENABLED=true` 前必须满足以下条件，任一缺失都不得开放公网入口：

- 全站 HTTPS，公网禁止明文 HTTP 登录
- 新 Vite 前端优先使用 HttpOnly Cookie 会话
- Cookie 设置 `HttpOnly` / `Secure` / `SameSite=Lax` 或更严格
- POST / PATCH / DELETE 启用 CSRF 校验
- 登录接口启用失败次数限制、短时锁定和审计日志
- CORS 使用白名单，禁止 `*`
- `system_admin` / `admin` 已绑定钉钉账号
- 公网 admin / system_admin 每次登录必须通过钉钉二次验证，当天有效
- 文件下载、音频播放、合同预览必须走后端鉴权和短时签名 URL

详细设计见 [ADR-RBAC.md](ADR-RBAC.md)。

## Vite 路由表

| 路由 | 阶段 | Feature Flag | 说明 |
|------|------|--------------|------|
| `/login` | Phase 0（当前环境已验证） | 无 | 新登录页 |
| `/admin/permissions` | Phase 0（当前环境已验证） | `FEATURE_VITE_FRONTEND` | 权限管理页；Phase 0 管理员默认落地页 |
| `/admin/users` | Phase 0（当前环境已验证） | `FEATURE_VITE_FRONTEND` | 用户列表和角色分配 |
| `/admin/dashboard` | Phase 1-2（Phase 2 代码层已完成） | `FEATURE_DASHBOARD_QUOTE` / `FEATURE_DASHBOARD_RESPONSE` / `FEATURE_DASHBOARD_EXECUTION` | 所有看板开关均关闭时显示“功能未开启”，不得作为 Phase 0 默认落地页 |
| `/admin/execution` | Phase 3 / 4 | `FEATURE_EXECUTION` / `FEATURE_MEETING_AI` | 任务管理和会议执行系统 |
| `/admin/business` | Phase 6 | `FEATURE_DASHBOARD_BUSINESS` | 经营驾驶舱 |
| `/quote` | 后续迁移 | 待定 | 旧 `/index.html` 保留到迁移完成 |
| `/admin/knowledge` | 后续迁移 | 待定 | 旧 `/admin.html` 保留到迁移完成 |
| `/admin/settings` | 未来规划 | 暂不注册主导航；后续如启用需新增 `FEATURE_SYSTEM_SETTINGS` | 系统配置页不进入 Phase 0-6 交付范围 |

`/admin/settings` 若后续承载功能开关、SLA 阈值、模型供应商、公司配置或公网开关，必须按高风险管理入口处理：仅 `system_admin` 可访问；修改动作需要当天有效钉钉验证；公网访问时按 [ADR-RBAC.md](ADR-RBAC.md) 的高风险操作审计规则执行。在正式设计前，不得把 `/admin/settings` 作为隐藏入口提前上线。

## 旧页面迁移准出条件

`/quote` 替代旧 `/index.html` 前必须同时满足：

- 文字报价、清单图上传、报价进度、失败恢复、确认推送、报价反馈闭环全部完成迁移
- 新页面与旧页面的核心手工验收用例全部通过
- `staff` / `admin` / `system_admin` 权限行为与旧页面一致
- 旧 `/index.html` 至少保留一个发布周期作为回退入口
- Runbook 中已补充从 `/quote` 回退到 `/index.html` 的步骤

`/admin/knowledge` 替代旧 `/admin.html` 前必须同时满足：

- materials CRUD、快照、CSV 导入 / 导出、sync_milvus、knowledge_candidates 审核全部完成迁移
- admin / system_admin 权限行为与旧页面一致
- RAG reload / eval 的状态展示和失败告警可用
- 旧 `/admin.html` 至少保留一个发布周期作为回退入口
- Runbook 中已补充从 `/admin/knowledge` 回退到 `/admin.html` 的步骤

未满足以上准出条件前，Vite 路由可预留但不得在导航中作为主入口替换旧页面。

登录后跳转顺序：

1. URL 带 `redirect` 且用户有权限时，优先回到 `redirect`。
2. `system_admin` / `admin` 默认进入 `/admin/permissions`。
3. `staff` 默认进入旧 `/index.html`，直到 `/quote` 完成迁移。
4. 其他角色进入第一个已开启且有权限的模块。
5. 没有任何可用模块时显示空状态页，提示联系管理员分配权限或开启功能。

Vite 路由守卫规则：

- 未登录跳 `/login`。
- 无权限显示 403 页面。
- 功能未开启显示“功能未开启”，不展示空白页。
- 旧路由 `/index.html`、`/admin.html`、`/app.html` 不进入 SPA fallback。

## 页面状态展示

所有 Vite 新页面统一使用以下状态，不允许直接空白：

| 状态 | 触发条件 | 展示要求 |
|------|----------|----------|
| 功能未开启 | API 返回 `FEATURE_DISABLED` 或对应 feature flag 为 false | 显示“功能未开启”，可提示联系管理员开启 |
| 无权限 | API 返回 `PERMISSION_DENIED` | 显示 403，不泄露资源是否存在 |
| 未登录 | API 返回 `AUTH_REQUIRED` / `ROLE_VERSION_EXPIRED` | 清会话并跳转 `/login` |
| 需要钉钉验证 | API 返回 `DINGTALK_VERIFY_REQUIRED` | 弹出钉钉验证流程，不清登录态 |
| 无数据 | 聚合接口 `sample_count=0` | 显示“暂无数据，数据从上线后开始统计” |
| 样本量过小 | 聚合接口返回 `low_sample_warning=true` | 显示“样本量较少，仅供参考” |
| 低可信数据 | 响应速度中存在 `time_source='default'` | 显示被排除样本量，默认时间不进入平均响应时长 |
