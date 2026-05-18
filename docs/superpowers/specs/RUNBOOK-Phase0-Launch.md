# RUNBOOK-Phase0-Launch｜阶段 0 上线操作手册
> 创建日期：2026-05-14
> 状态：当前环境验证已完成（2026-05-18）；正式生产上线尚未发生，未来需单独 Runbook
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 目标

阶段 0 上线内容包括 Vite 壳、登录鉴权统一、多角色 RBAC、`role_version`、权限管理页面、SPA fallback、公网安全门槛配置骨架。旧 `index.html` / `admin.html` 保留，不在阶段 0 强制迁移报价工作台和知识库管理页面。

## 当前环境验证记录（非正式生产上线）

- 验证状态：已完成。
- 验证时间：2026-05-18 10:47 左右（Asia/Shanghai；本机日志对应 2026-05-17 19:47 PDT）。
- 当前环境备份路径：`C:\AI_Backups\20260517_191536`。
- 数据库备份文件：`C:\AI_Backups\20260517_191536\db\ai_quotation.sql`。
- 数据库 revision：`20260514_0011 (head)`。
- 当前环境 FastAPI 进程：`pid=38924`。
- 当前开关：`FEATURE_VITE_FRONTEND=true`，`PUBLIC_ACCESS_ENABLED=false`。
- 当前环境路由冒烟：`/health/ready`、`/login`、`/admin/permissions`、`/admin/dashboard`、`/index.html`、`/admin.html`、`/app.html` 均返回 200。
- RBAC 冒烟：旧 token 401、`/api/v1/auth/me` 返回 `roles` / `role_version`、staff 访问管理接口 403、viewer 授权/撤销写入 `user_role_events`、撤销后旧 token 401。
- 当前环境启动修复：`.env` BOM 已清理；`start_all.ps1` 已导入 `.env` 到启动进程，避免系统环境变量覆盖配置。
- 遗留说明：冒烟测试账号 `phase0_smoke_final` 保留在当前环境数据库中，仅有 `staff` 角色；`viewer` 已撤销。Phase 1 另行启动，不纳入本 Runbook。
- 正式生产上线：尚未发生；未来上线需单独 Runbook，重新执行备份、迁移、重启、冒烟、通知和验收签字。

## 上线前检查（当前环境已验证；正式生产需重做）

- [x] 确认当前 git 分支和提交范围。
- [x] 确认当前数据库 Alembic head，升级前为 `20260514_0010`。
- [x] 确认 `.env` 已配置 `SYSTEM_ADMIN_USERNAME`，当前为 `admin`。
- [x] 确认 `SYSTEM_ADMIN_USERNAME` 对应用户存在。
- [x] 确认 `PUBLIC_ACCESS_ENABLED=false`。
- [x] 上线前保持 `FEATURE_VITE_FRONTEND=false`，冒烟通过后已打开为 `true`。
- [x] 确认钉钉企业应用方案作为后续公网二验依赖；内网 Phase 0 不阻塞。
- [x] 确认备份覆盖数据库、旧前端静态文件和 `.env`；当前环境备份路径见验证记录。

## 备份步骤（当前环境已验证；正式生产需重做）

1. [x] 执行数据库备份。
2. [x] 备份当前前端静态文件：`app.html`、`index.html`、`admin.html`、`static/`、`ai-web/dist/`。
3. [x] 备份 `.env`，未提交 git。
4. [x] 记录备份文件路径：`C:\AI_Backups\20260517_191536`。

## 迁移步骤（当前环境已验证；正式生产需重做）

1. [x] 执行 Alembic 升级，当前为 `20260514_0011 (head)`。
2. [x] 检查新增字段和表：
   - [x] `users.role_version`
   - [x] `users.dingtalk_user_id`
   - [x] `users.dingtalk_bound_at`
   - [x] `user_roles`
   - [x] `user_role_events`
3. [x] 执行角色迁移：
   - [x] `users.role='user'` -> `user_roles.role='staff'`
   - [x] `users.role='admin'` -> `user_roles.role='admin'`
   - [x] `SYSTEM_ADMIN_USERNAME=admin` -> `system_admin + admin`
4. [x] 已确认 `SYSTEM_ADMIN_USERNAME` 存在。
5. [x] 已验证旧 JWT 因不包含 `role_version` 返回 401；存量登录会话会强制失效，这是预期行为，不是故障。

## 部署步骤（当前环境已验证；正式生产需重做）

1. [x] 构建 Vite 前端。
2. [x] 将 Vite 编译产物交由 FastAPI 托管。
3. [x] 配置 SPA fallback：
   - [x] `/admin/dashboard` 刷新返回 Vite `index.html`
   - [x] `/index.html`、`/admin.html`、`/app.html` 继续返回旧页面
4. [x] 重启 FastAPI，当前新进程为 `pid=38924`。
5. [x] Celery worker 检查通过，`/health/ready` 返回 `task_queue.ok=true`。

## 冒烟测试（当前环境已验证；正式生产需重做）

- [x] 访问 `/login`，页面返回 200。
- [x] 登录后 `/api/v1/auth/me` 返回角色数组，且包含兼容 `role` 和 `role_version`。
- [x] `system_admin` 可进入权限管理页面。
- [x] 授予测试用户 `viewer`，写入 `user_role_events`。
- [x] 撤销该角色后 `users.role_version` 递增，旧 token 请求返回 401。
- [x] 访问 `/admin/dashboard`，刷新不出现 FastAPI 404。
- [x] 访问 `/index.html`，旧报价工作台仍可打开。
- [x] 访问 `/admin.html`，旧知识库管理仍可打开。
- [x] 访问 `/app.html`，旧登录门户仍按兼容策略可打开。
- [x] 旧报价工作台 `/index.html` 使用迁移后的 `users.role` 兼容字段仍可完成鉴权。
- [x] 旧知识库管理 `/admin.html` 使用迁移后的 admin 兼容字段仍可完成鉴权。
- [x] 只分配 `viewer` / `manager` 且未保留其他旧角色的测试用户访问旧 `/index.html` / `/admin.html` 时，旧鉴权逻辑因兼容 `users.role` 不含有效旧角色值，可能返回 401 / 403。这是预期行为；如需这类用户继续访问旧报价工作台，应在权限管理页同时分配 `staff`。
- [x] 旧 token 访问 `/api/v1/auth/me` 返回 401；重新登录后返回 `roles` / `role_version`。
- [x] `PUBLIC_ACCESS_ENABLED=false`，公网入口不开启。
- [x] `FEATURE_VITE_FRONTEND=true` 打开后旧入口仍返回 200；`FEATURE_VITE_FRONTEND=false` 保留为回滚路径。

## 上线后角色分配

Phase 0 只自动迁移已有 `user` / `admin`。`manager` / `viewer` 是新增角色，不会自动分配给存量用户。

上线后 `system_admin` 需要通过权限管理页面为相关人员手动分配：

- `viewer`：用于后续查看效率和经营驾驶舱
- `manager`：用于后续执行任务上线后查看和更新分配给自己的任务

权限管理页面必须提示：

- `manager` 角色在 `FEATURE_EXECUTION` / `FEATURE_MEETING_AI` 上线后才有实际业务入口
- `viewer` 在看板功能开关开启前可能没有可见页面
- Phase 0 旧页面仍主要依赖兼容 `users.role`，只分配 `viewer` / `manager` 不代表可访问旧报价工作台或旧知识库管理；如需继续使用旧报价页，应同时分配 `staff`
- 当前可用模块和待上线模块应在角色说明中展示

## 打开功能开关（当前环境已验证；正式生产需重做）

冒烟测试通过后：

```env
FEATURE_VITE_FRONTEND=true
```

重启服务后再次执行冒烟测试。

公网访问保持：

```env
PUBLIC_ACCESS_ENABLED=false
```

公网入口必须等 HTTPS、Cookie、CSRF、防爆破、CORS 白名单、文件签名 URL、钉钉登录二验全部完成后再打开。

当前环境状态：

```env
FEATURE_VITE_FRONTEND=true
PUBLIC_ACCESS_ENABLED=false
```

## 回滚方案

优先使用功能开关回滚：

```env
FEATURE_VITE_FRONTEND=false
```

重启服务后旧页面继续使用。

如数据库迁移出现问题：

1. 停止服务。
2. 按 Alembic revision 回滚到阶段 0 前。
3. 如回滚失败，使用上线前数据库备份恢复。
4. 恢复旧前端静态文件。
5. 记录失败原因和处理结果。

不得使用 `git reset --hard` 或手工删表作为常规回滚方式。

## 验收签字（当前环境记录；正式生产需另行记录）

正式生产上线完成需记录：

- [x] 当前环境验证时间：2026-05-18 10:47 左右（Asia/Shanghai）。
- [x] 操作人：Codex 按用户确认执行；旧 FastAPI 进程由用户手动终止。
- [x] 数据库 revision：`20260514_0011 (head)`。
- [x] 备份路径：`C:\AI_Backups\20260517_191536`。
- [x] 当前环境冒烟测试结果：通过，详见“当前环境验证记录”。
- [ ] 存量 session 失效通知是否已发送：未由本次操作发送；技术验证旧 token 401 已通过。
- [ ] manager / viewer 首批角色分配是否完成：未做业务首批分配；仅做冒烟授权/撤销。
- [x] 是否打开 `FEATURE_VITE_FRONTEND`：已打开。
- [x] `PUBLIC_ACCESS_ENABLED` 是否仍为 false：是。
- [x] 遗留问题：保留冒烟测试账号 `phase0_smoke_final`，仅有 `staff` 角色；Phase 1 另行启动，不纳入本 Runbook。
