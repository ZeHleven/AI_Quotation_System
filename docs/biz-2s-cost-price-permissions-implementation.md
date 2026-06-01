# BIZ-2s 成本价权限落地首版

> 状态：已通过当前环境手动验收（2026-05-28）  
> 前置：BIZ-2r 成本库重复 active 防护与报价多候选提示已通过当前环境手动验收  
> 边界：不新增数据库结构、不新增 Alembic、不改报价规则、不改价格口径、不改 N8N/Dify、不改变无底价 draft 和 active 启用原则

## 1. 目标

BIZ-2s 按 `docs/biz-2-cost-price-permissions-draft.md` 的最小落地方案，先把“普通业务员能看完整成本库”的风险收紧，同时保留报价预审必须用到的成本参考能力。

本阶段解决：

1. 普通 `staff` 不再进入完整成本数据库列表、详情、流向和同步记录。
2. 普通 `staff` 仍可在报价预审中按当前报价行查询必要的 active 成本候选，用于切换成本依据和多候选确认。
3. 新增成本专项角色，避免继续把成本部账号都授予 `admin`。
4. 成本库维护动作拆成“查看、编辑、审批启用/归档/同步”三类。
5. Vite 管理后台按角色显示成本库入口和操作按钮。

## 2. 角色边界

本阶段复用现有 `user_roles` 表，不新增表：

| 角色 | 能力 |
|------|------|
| `cost_viewer` | 查看完整成本库、详情、状态与流向、RAG 同步记录 |
| `cost_editor` | 继承 `cost_viewer`，可新建、编辑 draft、导入 Excel 到 draft |
| `cost_approver` | 继承 `cost_editor`，可启用 active、撤回 active、归档、批量状态流转、同步 active 到 RAG |
| `cost_exporter` | 预留导出角色；当前只开放完整成本库查看，不新增导出功能 |
| `admin` | 兼容现有管理员能力，并隐含 `cost_viewer/cost_editor/cost_approver` |
| `system_admin` | 兼容系统管理员能力，并隐含全部成本专项角色 |
| `staff` | 只保留报价工作台和报价预审必要成本候选，不开放完整成本库 |

## 3. 接口调整

完整成本库接口继续使用 `/api/v1/admin/cost-items*`，但访问角色收紧：

| 接口/能力 | 权限 |
|----------|------|
| 成本库列表、详情、状态与流向、同步记录 | `cost_viewer` 及以上，`admin/system_admin` |
| 新建、编辑 draft、Excel 导入预览/确认 | `cost_editor` 及以上，`admin/system_admin` |
| 编辑 active、启用、撤回、归档、批量状态流转、同步 active 到 RAG | `cost_approver`，`admin/system_admin` |

新增报价预审按需候选接口：

```text
GET /api/v1/cost-items/quote-candidates?keyword=...
```

该接口只返回 `cost_items.active` 中匹配当前关键字的候选，给普通报价预审切换成本依据使用。它不是完整成本库浏览接口，普通 `staff` 只能通过这个受限入口读取必要候选。

## 4. 前端调整

旧 `index.html`：

- 预审“切换成本条目”改为调用 `/api/v1/cost-items/quote-candidates`。
- 普通 `staff` 不再显示完整“成本库详情”跳转。
- `admin/system_admin/cost_*` 角色仍可打开完整成本库详情。

Vite `/admin/cost-db`：

- 成本库入口仅对 `admin/system_admin/cost_viewer/cost_editor/cost_approver/cost_exporter` 显示。
- 导入、新建、编辑只对 `cost_editor` 及以上显示。
- 启用、撤回、归档、批量状态流转、同步 RAG 只对 `cost_approver` 及以上显示。
- 同步记录、状态与流向对完整成本库可见角色开放。

## 5. 验证

已完成：

```text
C:\Users\12521\miniconda3\python.exe -m pytest tests/test_cost_db_biz2a.py tests/test_cost_rag_sync_biz2c.py tests/test_rbac_phase0.py
30 passed, 3 warnings

cmd /c npm.cmd run build
vite build passed

node -e "...index.html inline script syntax check..."
checked 1 inline scripts
```

补充说明：本次尝试使用 Codex in-app Browser 打开 `http://localhost:9000/`，但 Browser 插件在本地沙箱启动阶段连续退出，未完成浏览器层页面验证；后端端口探测返回 `200`，自动化测试与构建验证已通过。

## 6. 当前环境手动验收

2026-05-28 已由当前内网环境完成手动验收，验收结论为通过。

验收口径：

1. 普通 `staff` 不再浏览完整成本库。
2. 普通 `staff` 在报价预审中仍可查询必要 active 成本候选并完成成本依据确认。
3. 成本专项角色可按 `cost_viewer` / `cost_editor` / `cost_approver` 边界访问页面和操作按钮。
4. `admin/system_admin` 保持现有兼容能力。
5. 权限收紧不改变报价规则、价格口径、无底价 draft 规则、active 生效规则和 RAG 同步逻辑。

## 7. 未覆盖事项

本阶段没有做：

- 成本库导出功能、导出水印、导出审批。
- 高风险操作二次验证。
- 更细的老板/成本负责人审批流。
- RAG 同步失败重试、版本追踪、同步质量评估。
- 成本库数据自动治理或自动合并。
- 上云、公网访问、安全加固和生产 Runbook。

这些继续作为后续成本库数据治理、小范围内网试运行准备和正式生产前安全工作处理。
