# ADR-RBAC｜多角色权限、公网安全与钉钉二次验证
> 创建日期：2026-05-14
> 状态：Phase 0 RBAC 已完成当前环境验证，正式生产上线待单独 Runbook；公网安全与钉钉强制二验仍按后续门槛推进
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 决策摘要

系统从单字段 `users.role` 迁移为多角色 RBAC。`users.role` 仅保留旧页面和旧接口兼容，真实权限以 `user_roles` 表为准。系统未来可能公网访问，公网开放前必须完成 HTTPS、Cookie、CSRF、防爆破、CORS 白名单、文件鉴权和钉钉二次验证。

公网访问启用后，`system_admin` / `admin` 每次登录都必须通过钉钉二次验证。验证通过后，当天有效；跨自然日或重新登录时必须重新验证。

## Phase 0 实施状态

- 已通过 Alembic `20260514_0011` 实现并完成当前环境验证 `users.role_version`、`users.dingtalk_user_id`、`users.dingtalk_bound_at`、`user_roles`、`user_role_events`。
- 已完成 `users.role` 到 `user_roles` 的兼容迁移，旧页面仍保留 `users.role` 兼容字段。
- `SYSTEM_ADMIN_USERNAME=admin` 已具备 `system_admin + admin`。
- JWT / 当前会话已写入 `roles` / `role_version`，数据库比对不一致返回 401。
- 角色授予 / 撤销已写入 `user_role_events`，并递增 `users.role_version`。
- `PUBLIC_ACCESS_ENABLED=false` 保持关闭；公网强制钉钉二次验证不在 Phase 0 内网版启用。

## 角色模型

```
users 扩展字段:
- role_version: Integer, NOT NULL, default=1
- dingtalk_user_id: String(128), nullable, UNIQUE where not null
- dingtalk_bound_at: nullable

user_roles:
- id, created_at
- user_id: FK -> users.id
- role: system_admin / admin / staff / manager / viewer
- created_by: FK -> users.id
- note: 授权备注
- UNIQUE (user_id, role)

user_role_events:
- id, created_at
- target_user_id: FK -> users.id
- role: system_admin / admin / staff / manager / viewer
- action: granted / revoked
- operator_id: FK -> users.id
- ip_address
- user_agent
- trace_id
- note: 备注
```

Phase 0 RBAC Alembic migration 负责补齐 `users.role_version`、`users.dingtalk_user_id` 和 `users.dingtalk_bound_at`。`users.role` 已存在，仅保留兼容，不新增同名字段。`dingtalk_user_id` 存钉钉企业用户标识，不存手机号；同一个钉钉用户不得绑定多个系统账号。

`users.role_version` 为整数，默认 1。每次授予、撤销角色或禁用账号时递增。JWT / Cookie 会话中写入 `role_version`，后端鉴权时与数据库比对，不一致则返回 401。

`user_roles` 必须对 `(user_id, role)` 建唯一约束。重复 grant 同一角色返回 `200 + 当前角色列表` 并写幂等审计事件；revoke 删除唯一角色记录。不得允许同一用户同一角色出现多行。

首次迁移由 `.env` 指定 `SYSTEM_ADMIN_USERNAME`，默认 `admin`。该账号自动获得 `system_admin + admin`。若账号不存在，迁移必须失败，不允许系统进入无人可管理状态。

允许多个 `system_admin`，但必须满足以下约束：

- 只有 `system_admin` 可以授予或撤销 `system_admin`
- 授予或撤销 `system_admin` 必须存在当天有效的钉钉登录验证
- 授权和撤权必须填写 `note`，并写入 `user_role_events`
- 禁止撤销最后一个 `system_admin`
- 禁止禁用最后一个 `system_admin`
- 唯一 `system_admin` 不能自我降权

## 权限矩阵

| 功能 | system_admin | admin | staff | manager | viewer |
|------|:------------:|:-----:|:-----:|:-------:|:------:|
| 管理用户角色 / 功能开关 / 运维状态 | 是 | 否 | 否 | 否 | 否 |
| 创建 / 取消报价任务 | 是 | 是 | 自己 | 否 | 否 |
| 查看报价任务 | 全部 | 全部 | 自己 | 否 | 否 |
| 创建 ClientInquiry | 是 | 是 | 是 | 否 | 否 |
| 查看 ClientInquiry | 全部 | 全部 | 自己 | 否 | 否 |
| 手动创建 ExecutionTask | 是 | 是 | 否 | 否 | 否 |
| 查看 ExecutionTask | 全部 | 全部 | 分配给自己的 | 分配给自己的 | 否 |
| 更新 ExecutionTask | 是 | 是 | 分配给自己的进度 / 备注 | 分配给自己的进度 / 备注 | 否 |
| 取消 ExecutionTask | 是 | 是 | 否 | 否 | 否 |
| 录入会议纪要 / 确认草稿任务 | 是 | 是 | 是 | 是 | 否 |
| 修订已确认会议纪要 | 是 | 是 | 否 | 否 | 否 |
| 查看效率驾驶舱 | 是 | 是 | 否 | 否 | 是 |
| 查看经营驾驶舱 | 是 | 是 | 否 | 否 | 是 |
| 查看 / 下载合同、录音、效果图附件 | 全部 | 全部 | 自己报价相关效果图 | 分配给自己的任务录音 | 否 |
| 管理知识库 | 是 | 是 | 否 | 否 | 否 |

权限取所有角色的并集。例如 `staff + manager` 既可创建报价，也可更新分配给自己的执行任务。后端不得只读取单个 `users.role` 判断权限。

普通员工和 manager 更新分配给自己的 `ExecutionTask` 时，只能更新进度类字段，例如 `status`、`completed_at`、`notes`。不得修改 `assignee_id`、`due_at`、`source`、`source_ref_id` 等分配与来源字段，避免员工绕过管理分派。

已确认会议纪要的修订必须写入 `meeting_note_revisions`，初期仅允许 `admin` / `system_admin` 操作。`staff` / `manager` 可以录入纪要和确认草稿任务，但不得改写已确认纪要的历史来源链路。

Phase 3 前尚无执行任务入口，`manager` 与 `staff` 在实际业务能力上差异很小。权限管理页面必须提示：“manager 角色将在执行任务功能上线后生效”。`viewer` 也可能在看板开关开启前没有可见页面，权限页面应展示每个角色当前可用模块和待上线模块。

## 钉钉登录二次验证

公网访问启用后，`system_admin` / `admin` 每次登录都必须通过钉钉二次验证。仅靠 Webhook 机器人不能证明操作者身份，因此二次验证必须基于钉钉企业应用 OAuth / 扫码 / 免登能力，或企业应用发送的可回调确认动作。

验证有效期：

- 登录时完成钉钉验证后，当前会话获得 `dingtalk_verified_until`
- 有效期到 `Asia/Shanghai` 自然日结束（23:59:59）
- 跨自然日后继续使用后台时，必须重新钉钉验证
- 用户主动退出登录后，本次会话失效；再次登录仍需钉钉验证
- 撤销角色、禁用账号或递增 `users.role_version` 后，钉钉验证状态同时失效

`dingtalk_verified_until` 的“当天有效”统一按 `Asia/Shanghai` 判断，不使用服务器本地时区或 UTC 自然日。FastAPI、MySQL、前端展示即使部署在不同时区，也必须把验证截止时间转换为中国时区当天 23:59:59 后再比较。

建议数据模型：

```
admin_login_verifications:
- id, created_at
- user_id: FK -> users.id
- dingtalk_user_id
- verified_at
- verified_until
- login_session_id: JWT `jti` 或服务端会话 ID 的 hash
- ip_address
- user_agent
- trace_id
- status: active / expired / revoked
```

`login_session_id` 用于把钉钉验证结果精确绑定到一次登录会话。JWT 模式下登录时必须生成 `jti` 并写入 JWT payload，数据库仅保存其 hash；Cookie / server-side session 模式下保存 session id 的 hash。后端校验钉钉验证时必须同时匹配 `user_id` 与 `login_session_id`，不得把一个会话的验证状态复用到另一个会话。

`expired` 不依赖额外 Celery beat。后端在读取或校验 `admin_login_verifications` 时，若 `status='active'` 且 `verified_until < now(Asia/Shanghai)`，应在同一事务中懒更新为 `expired`，再要求重新验证。运维报表也按相同规则动态展示过期状态。

状态触发条件：

- `active`：本次登录会话已完成钉钉验证，且未过期、未撤销。
- `expired`：自然时间到期触发，即 `verified_until < now(Asia/Shanghai)`。
- `revoked`：主动或安全事件触发，包括用户退出登录、撤销 admin/system_admin 角色、禁用账号、递增 `users.role_version`、管理员强制作废会话。

`expired` 和 `revoked` 不得混用。前者表示被动过期，后者表示主动撤销或安全失效。

公网访问前，所有 `system_admin` / `admin` 必须绑定 `users.dingtalk_user_id`。未绑定钉钉账号的管理员不得通过公网入口登录后台。

## 高风险操作

高风险操作包括：

- 授予或撤销角色，尤其是 `system_admin` / `admin`
- 修改功能开关
- 启用 `PUBLIC_ACCESS_ENABLED`
- 导出合同、回款、客户明细
- 下载合同文件、会议录音
- 将合同文件全文发送至外部 AI 服务
- 查看 AI 原始输入输出
- 批量确认、回滚或删除历史成交知识候选
- 批次导入确认、批次回滚
- 删除或物理清理文件对象

高风险操作默认复用当天有效的钉钉登录验证，不再每次操作单独验证，避免操作过重。但每次高风险操作必须写入审计事件，记录 `operator_id`、`dingtalk_user_id`、`verified_at`、`action_type`、目标对象、IP、user agent 和 `trace_id`。

`admin_login_verifications` 与 `admin_action_challenges` 的关系：

- 默认只要求当天有效的 `admin_login_verifications`，覆盖普通高风险操作。
- `admin_action_challenges` 是预留的加强机制，仅在某类操作被显式配置为“单次挑战”时启用。
- 若启用了单次挑战，该操作必须同时满足当天登录验证 active + 本次 action challenge verified。
- 未配置单次挑战的操作不得临时自行弹 challenge，避免不同模块安全体验不一致。

若后续某类操作风险升高，可再引入单次操作挑战。预留模型如下：

```
admin_action_challenges:
- id, created_at, expires_at
- challenge_id: UUID
- user_id: FK -> users.id
- action_type: grant_role / revoke_role / export_business_data / download_contract / rollback_import / enable_public_access / delete_file
- target_type: string，如 user / contract / import_batch / file_object / feature_flag
- target_id: string，统一把不同主键类型转为字符串
- status: pending / verified / expired / cancelled
- dingtalk_user_id
- verified_at
- metadata_json
- trace_id
```

单次操作挑战有效期建议 5 分钟。同一挑战必须校验 `challenge_id`、用户、动作类型和目标对象一致。挑战是单次使用：状态变为 `verified` 后，只能用于完成创建它的那一次操作，不提供跨操作复用窗口，因此不需要 `verified_until`。

`admin_action_challenges.status='expired'` 与 `admin_login_verifications` 一样使用懒更新，不新增高频 Celery beat。后端读取或校验挑战时，若 `status='pending'` 且 `expires_at < now()`，应在同一事务中更新为 `expired`，再返回挑战过期。运维报表也按相同规则动态展示过期状态。

## 钉钉不可用与 break-glass

安全策略选择：高安全优先。钉钉 OAuth / 企业应用不可用时，公网 admin / system_admin 的高风险操作默认阻断。系统不得在普通登录页、后台页面或 API 中提供固定超级密码、本地万能密钥或长期绕过钉钉二次验证的入口。

钉钉二次验证不可用时不降级到纯密码、邮件或 TOTP。唯一例外是下文离线 break-glass 程序，且只能用于恢复钉钉配置、关闭公网入口或修复角色配置，不得用于日常高风险业务操作。

为避免钉钉长期故障导致系统完全无法恢复，预留离线 break-glass 程序，仅用于极端运维恢复，不作为日常高风险操作绕过方式。

break-glass 规则：

- 只能在服务器本机或受控运维终端执行 CLI 命令启用，不提供网页按钮
- 必须先通过 `system_admin` 账号密码认证
- 必须输入一次性恢复码；恢复码只保存 hash，放在 `.env` 或离线安全介质中
- 启用后生成短时 `break_glass_session`，默认 15 分钟，最长 30 分钟
- 仅允许最小必要操作：修复钉钉配置、关闭 `PUBLIC_ACCESS_ENABLED`、撤销错误角色、创建临时 `system_admin`
- 禁止导出经营数据、下载合同 / 录音、批量删除、批次回滚、开启公网入口
- 所有 break-glass 操作必须写审计日志，标记 `break_glass=true`，并记录 `ip_address`、`user_agent`、`trace_id`
- 启用时尝试发送钉钉告警；若钉钉不可用，写本地安全日志，钉钉恢复后补发

break_glass_sessions:
- id, created_at, expires_at
- session_id: UUID
- session_token_hash
- user_id: FK -> users.id
- status: active / expired / revoked
- reason
- allowed_actions_json
- recovery_code_hash_version
- ip_address
- user_agent
- trace_id
- revoked_at: nullable
- revoked_by: FK -> users.id, nullable

`session_token` 仅在 CLI 启用时显示一次，数据库只保存 hash。`allowed_actions_json` 必须限制为本次恢复所需的最小动作集合，默认不包含经营数据导出、合同 / 录音下载、批次回滚和公网开启。

break-glass CLI 和恢复码不在 Phase 0 默认实现，但公网开放前必须完成方案评审和演练。

## 公网访问安全门槛

`PUBLIC_ACCESS_ENABLED=false` 为默认值。设置为 true 前必须满足：

- 全站 HTTPS，禁止公网明文 HTTP 登录
- Cookie 设置 `HttpOnly` / `Secure` / `SameSite=Lax` 或更严格
- Vite 新前端优先使用 HttpOnly Cookie 会话
- 旧页面兼容期允许 Bearer JWT + localStorage，但公网入口不得长期依赖 localStorage
- POST / PATCH / DELETE 启用 CSRF 校验
- 登录接口启用失败次数限制、短时锁定、审计日志
- CORS 使用白名单，禁止 `*`
- 文件下载、音频播放、合同预览必须走后端鉴权和短时签名 URL

公网 / 内网判定：

- 安全策略不按单个请求 IP 临时判断，而以部署配置 `PUBLIC_ACCESS_ENABLED` 为准。
- `PUBLIC_ACCESS_ENABLED=true` 时，所有 admin / system_admin 后台访问均按公网安全要求执行，即使请求来自内网 IP。
- `PUBLIC_ACCESS_ENABLED=false` 时，系统默认处于内网部署；仍可记录 `X-Forwarded-For`，但不得把可伪造请求头作为降低安全要求的依据。
- 如未来需要混合部署，必须由受信任反向代理写入经过校验的 `X-Forwarded-For`，并维护可信代理 IP 白名单；未列入白名单的来源头一律忽略。

## 对象存储演进

当前阶段可继续使用本地 MinIO，后续文件对象迁移到阿里云 OSS 或腾讯云 COS。业务代码不得直接依赖 MinIO SDK，应通过统一 `file_storage` 抽象访问对象存储。

要求：

- `file_objects` 存储逻辑文件 ID、bucket、object key、provider、content_type、size、checksum
- 下载统一走后端鉴权，再生成短时签名 URL
- provider 可取 `minio` / `aliyun_oss` / `tencent_cos`
- 迁移对象存储时不改变业务表中的 `file_object_id`
- 备份策略必须同时覆盖数据库元数据和对象文件

## 文件与离职访问

合同、录音、效果图默认永久保存。员工离职时撤销其所有 `user_roles` 或设置账号禁用，并递增 `users.role_version`，旧 JWT / Cookie 会话立即失效。

文件访问策略：

- 合同文件：仅 `admin` / `system_admin` 可查看明细和下载
- 录音文件：会议创建人、任务负责人、`admin` / `system_admin` 可访问
- 效果图：按报价任务归属和报价权限访问
- viewer 不可下载合同、录音或客户敏感附件

## 验收要求

- 撤销角色后旧 token 请求返回 401
- 纯 staff 访问 admin 接口返回 403
- staff / manager 更新他人任务返回 403
- staff / manager 修改自己任务的 `assignee_id` / `due_at` 返回 403
- viewer 访问明细列表和合同下载返回 403
- `PUBLIC_ACCESS_ENABLED=true` 时，缺 HTTPS / CSRF / CORS 白名单 / 钉钉绑定任一条件，启动失败或健康检查 degraded
- `system_admin` / `admin` 公网登录未通过钉钉二次验证时返回 403
- 钉钉登录验证跨自然日后自动失效
- 高风险操作缺少当天有效钉钉验证时返回 403
- 禁止撤销、禁用或自我降权导致系统没有任何 `system_admin`
- Phase 0 权限页面提示 manager / viewer 的当前可用模块和待上线模块
