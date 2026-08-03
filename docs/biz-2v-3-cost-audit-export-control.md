# BIZ-2v-3 成本库敏感操作审计与导出控制

> 日期：2026-05-28  
> 状态：已完成代码层验证  
> Alembic：`20260528_0025_add_cost_access_audit_logs`  
> 边界：不改报价规则、不改价格口径、不改 N8N/Dify、不自动改动成本库数据

## 阶段目标

BIZ-2v-3 面向内网试运行前的成本价安全追溯，补齐两件事：

- 成本库导出必须受 `cost_exporter` 或管理员权限控制。
- 成本库完整查看、详情查看、导出、状态变更、导入确认和 RAG 同步等敏感动作必须留审计记录。

## 本阶段不做

- 不做复杂审批流。
- 不做导出水印或文件加密。
- 不把完整成本价明细复制进审计日志。
- 不改报价规则、价格口径、无底价 draft 沉淀、active 启用规则。
- 不自动触发 RAG 同步。
- 不改 N8N/Dify 工作流。
- 不启动正式试运行或生产上线。

## 数据库变更

新增 Alembic revision：

- `20260528_0025_add_cost_access_audit_logs`

新增表：

- `cost_access_audit_logs`

核心字段：

- `action`：敏感动作，例如 `cost_item.export`、`cost_item.detail`。
- `resource_type` / `resource_id`：对象类型和对象 ID。
- `user_id` / `username` / `roles_snapshot`：操作者和角色快照。
- `request_path` / `request_method` / `client_ip` / `user_agent` / `trace_id`：请求上下文。
- `filters_json` / `result_count`：筛选条件和影响数量。
- `status` / `message`：动作结果。
- `created_at`：记录时间。

审计日志不保存完整成本价格明细，避免敏感数据二次扩散。

## 后端能力

新增：

- `GET /api/v1/admin/cost-items/export`
  - 按当前筛选条件导出成本库 CSV。
  - 仅 `cost_exporter`、`admin`、`system_admin` 可访问。
  - 成功导出后写入 `cost_item.export` 审计。

- `GET /api/v1/admin/cost-items/audit-logs`
  - 查询成本库审计记录。
  - 仅 `admin`、`system_admin`、`cost_approver` 可访问。
  - 支持按 `action`、`username`、`resource_id`、`status` 筛选。

新增服务：

- `app/services/cost_audit.py`
  - `record_cost_audit`
  - `list_cost_audit_logs`
  - `serialize_cost_audit_log`

## 已接入审计的动作

- `cost_item.list`：完整成本库列表查看。
- `cost_item.detail`：成本条目详情查看。
- `cost_item.export`：成本库导出。
- `cost_item.create`：新建成本条目。
- `cost_item.update`：编辑成本条目。
- `cost_item.activate`：启用 active。
- `cost_item.withdraw`：撤回启用。
- `cost_item.archive`：归档。
- `cost_item.bulk_status`：批量状态变更。
- `cost_item.import_preview`：导入预览。
- `cost_item.import_confirm`：导入确认。
- `cost_item.lineage_summary` / `cost_item.lineage_list` / `cost_item.lineage_detail`：状态与流向查看。
- `cost_rag.sync`：同步 active 到 RAG。
- `cost_rag.runs`：同步记录查看。
- `cost_rag.status`：RAG 同步状态查看。

## 前端能力

Vite `/admin/cost-db` 新增：

- “导出”按钮：仅 `cost_exporter` / 管理员可见。
- “审计记录”入口：仅管理员 / `cost_approver` 可见。
- 审计记录弹窗：展示时间、动作、用户、对象、结果、数量、IP 和说明。

## 验收结果

- `python -m pytest tests/test_cost_audit_biz2v3.py -q`：`4 passed, 1 warning`
- `python -m pytest tests/test_cost_db_biz2a.py tests/test_cost_rag_sync_biz2c.py tests/test_cost_audit_biz2v3.py -q`：`35 passed, 5 warnings`
- `python -m pytest -q`：`257 passed, 5 warnings`
- `python -m compileall app tests`：通过
- `python -m alembic heads`：`20260528_0025 (head)`
- `cmd /c npm.cmd run build`：通过，仅保留 Vite chunk size 警告

## 回滚方式

- 代码层回滚 `cost_audit` 服务、导出接口、审计查询接口和前端入口。
- 数据库层执行 Alembic downgrade 至 `20260527_0024`，删除 `cost_access_audit_logs`。
- 本阶段不改成本条目、不改报价结果、不改 RAG 数据，因此回滚不会影响报价主链路。
