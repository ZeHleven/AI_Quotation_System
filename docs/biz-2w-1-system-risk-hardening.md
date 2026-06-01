# BIZ-2w-1 系统完善审查与账号入口安全加固

> 日期：2026-05-28  
> 状态：已完成代码层验证，并已通过当前环境手工验收（2026-05-28）  
> 范围：账号入口、权限管理、旧报价工作台登录提示  
> 边界：不新增数据库结构，不新增 Alembic，不改报价规则、价格口径、RAG、N8N/Dify 或成本库 active 规则。

## 1. 审查结论

本轮按风险优先级先审查了报价下发、成本价权限、成本候选查询、预审草稿、报价历史和账号入口。

最高优先级问题是：旧注册接口 `/api/v1/auth/register` 默认开放，调用后会直接创建 `staff` 账号并给 5 次报价额度。虽然当前仍是内网验证阶段，但该入口与“仅管理员和一名成本部业务员使用”的实际边界不一致，会带来非授权人员注册、使用报价额度和访问报价工作台的风险。

## 2. 本次修复

1. 新增配置 `ALLOW_SELF_REGISTRATION=false`，默认关闭自注册。
2. `/api/v1/auth/register` 在未显式开启时返回 `403 SELF_REGISTRATION_DISABLED`。
3. 新增 `POST /api/v1/admin/users`，仅 `system_admin` 可创建用户、设置初始额度和初始角色，并强制新账号首次登录后改密。
4. Vite 权限管理页新增“新建用户”入口，仅 `system_admin` 可见。
5. 旧 `index.html` 登录区域不再展示“注册并领取额度”，改为提示账号由管理员统一开通。

## 3. 不做事项

- 不开放公网。
- 不启动正式试运行。
- 不创建真实业务账号。
- 不改报价预审、下发、总价、无底价、成本库匹配或 RAG 同步逻辑。
- 不新增数据库字段或表。

## 4. 验证结果

已完成：

- `C:\Users\12521\miniconda3\python.exe -m pytest`
  - 结果：`260 passed, 5 warnings`
- `C:\Users\12521\miniconda3\python.exe -m pytest tests/test_auth.py tests/test_rbac_phase0.py tests/test_cost_db_biz2a.py::test_staff_can_only_read_quote_cost_candidates`
  - 结果：`11 passed, 1 warning`（账号入口与 RBAC 专项回归）
- `C:\Users\12521\miniconda3\python.exe -m compileall app tests`
  - 结果：通过
- `npm.cmd run build`
  - 结果：通过
- 旧 `index.html` inline script 语法检查
  - 结果：`checked 1 inline scripts`

说明：pytest warning 仍是 Windows `.pytest_cache` 写入权限提示，不影响功能验证。

## 5. 验收建议

1. 未开启 `ALLOW_SELF_REGISTRATION` 时，直接调用 `/api/v1/auth/register` 应返回 `403`。
2. 使用 `system_admin` 登录 `/admin/permissions`，可以看到“新建用户”按钮。
3. 新建一个测试用户，选择 `staff` 或成本专项角色，保存后用户列表出现该账号。
4. 使用新账号登录，应能登录并提示需要改初始密码。
5. 使用普通 `admin` 或 `staff` 登录时，不应看到或不能调用新建用户能力。

## 6. 手工验收结论

用户已确认当前环境手工验收通过（2026-05-28）。本阶段不代表正式试运行已启动，系统仍保持内网验证阶段。

## 7. 回滚方式

如需临时恢复自注册，可在内网受控时间窗口设置：

```env
ALLOW_SELF_REGISTRATION=true
```

完成账号创建后应立即改回：

```env
ALLOW_SELF_REGISTRATION=false
```

推荐继续使用 `system_admin` 的新建用户入口，不再依赖开放注册。
