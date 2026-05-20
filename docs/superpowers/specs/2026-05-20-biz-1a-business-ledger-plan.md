# BIZ-1a 商务台账阶段计划
> 创建日期：2026-05-20
> 状态：计划确认中，尚未编码

## 阶段定调

- 本阶段只做 BIZ-1a 商务台账 v1。
- ROADMAP 中的 "BIZ-1a + BIZ-2a 并行" 服从当前执行约束 "每次只做一个阶段"。
- Phase 4a 已在 2026-05-19 完成当前环境运行态验收；本阶段不重复启动 Phase 4a，只在必要时复跑 smoke。
- 不启动 Phase 4b / Phase 4c / Phase 6。
- 不迁移旧 `index.html` / `admin.html`，不生成 HTML。
- 新增数据库结构必须走 Alembic。
- 系统完善前不正式生产使用，`PUBLIC_ACCESS_ENABLED=false` 保持不变。

## 前置检查

- Alembic 代码 head：`20260514_0015 (head)`。
- 当前数据库版本：`20260514_0015 (head)`。
- `API-CONTRACTS.md` 已预定义 BIZ-1a 接口契约。
- `STATE-MACHINES.md` 已预定义 `client_inquiries` 的 BIZ-1a 状态机。
- `FEATURE-FLAGS.md` 已声明 `FEATURE_BUSINESS_LEDGER`。
- 当前 `client_inquiries` 为 Phase 2 inbound 模型，需要通过新 Alembic revision 扩展为 inbound/outbound 双向模型。

## 本阶段范围

### 后端

- 新增 `FEATURE_BUSINESS_LEDGER` 配置项，默认关闭。
- 通过新 Alembic revision 扩展 `client_inquiries`：
  - `direction`
  - `stage`
  - `next_followup_at`
  - `cancelled_at`
  - `cancelled_by_id`
  - `cancel_reason`
  - 将 `first_response_time` 调整为 nullable。
- 新建 `client_inquiry_events` 审计表。
- 保留历史记录为 `direction='inbound'`，不影响 Phase 2 响应速度看板。
- 新增 `/api/v1/business-ledger` 接口：
  - `POST /api/v1/business-ledger`
  - `GET /api/v1/business-ledger`
  - `GET /api/v1/business-ledger/{id}`
  - `PATCH /api/v1/business-ledger/{id}`
  - `POST /api/v1/business-ledger/{id}/cancel`
- 状态机严格遵守 `STATE-MACHINES.md`。

### PATCH 授权规则

- `admin` / `system_admin` 可 PATCH 任意未作废、未进入终态的 outbound 台账记录。
- `admin` / `system_admin` 可修改负责人字段 `responder_id`，用于转交记录。
- `admin` / `system_admin` 可修改 `source`。
- staff 只能 PATCH `responder_id == 当前用户 id` 的 outbound 台账记录。
- staff 可修改字段仅限：`stage`、`next_followup_at`、`client_phone`、`notes`。
- staff 不得修改 `responder_id`，不得把记录转交给他人。
- staff 不得修改 `source`。
- `direction` 创建后不可修改，所有角色均不可 PATCH。
- 已作废记录 PATCH 返回 `409 STATE_CONFLICT`。
- `stage` 已进入终态（`成单` / `丢单`）后 PATCH 返回 `409 STATE_CONFLICT`。
- 创建记录时，staff 的负责人必须是自己；如请求体携带其他 `responder_id`，返回权限错误。`admin` / `system_admin` 可为任意有效用户创建记录。

### 前端

- 新增 Vite 路由 `/admin/business-ledger`。
- 沿用当前 Vite SPA 壳惯例，页面逻辑内联到 `ai-web/src/App.vue`，样式写入 `ai-web/src/style.css`。
- 本阶段不新增 `ai-web/src/views/BusinessLedger.vue`，不引入 router/views 目录结构，避免借 BIZ-1a 做前端结构重构。
- 新增商务台账页面：
  - 列表
  - 筛选
  - 新建
  - 编辑
  - 作废
  - 逾期高亮
- 导航仅在用户角色和功能开关允许时开放；功能关闭时显示统一未开启状态。

### 不做

- 不做 BIZ-1b 跟进提醒。
- 不做 BIZ-1c 跟进流水。
- 不做 BIZ-1d 招标平台接入。
- 不做 BIZ-2a 成本数据库。
- 不改报价主流程，不改 RAG，不改旧 HTML。

## 预计涉及文件

- `AI_Middle_Office/app/core/config.py`
- `AI_Middle_Office/.env.example`
- `AI_Middle_Office/alembic/versions/20260520_0016_add_business_ledger.py`
- `AI_Middle_Office/app/models/client_inquiry.py`
- `AI_Middle_Office/app/services/client_inquiries.py`
- `AI_Middle_Office/app/services/business_ledger.py`
- `AI_Middle_Office/app/api/v1/business_ledger.py`
- `AI_Middle_Office/app/main.py`
- `AI_Middle_Office/tests/test_business_ledger_biz1a.py`
- `AI_Middle_Office/scripts/biz1a_business_ledger_smoke.ps1`
- `ai-web/src/App.vue`
- `ai-web/src/style.css`
- 不新增 `ai-web/src/views/BusinessLedger.vue`
- `AGENTS.md`
- `AI_Middle_Office/AGENTS.md`
- `ROADMAP.md`
- `docs/superpowers/specs/API-CONTRACTS.md`
- `docs/superpowers/specs/STATE-MACHINES.md`
- `docs/superpowers/specs/FEATURE-FLAGS.md`

## 验收标准

- `FEATURE_BUSINESS_LEDGER=false` 时，BIZ-1a 接口返回 `404 NOT_FOUND`，前端入口不可用或显示功能未开启。
- `FEATURE_BUSINESS_LEDGER=true` 时：
  - staff 可创建、查看、编辑自己负责的 `outbound` 台账记录。
  - admin / system_admin 可查看全员台账，并按负责人筛选。
  - `direction` 创建后不可修改。
  - `stage` 支持文档定义的流转；进入 `成单` / `丢单` 后再次 PATCH 返回 `409`。
  - 作废必须提交 `reason`，并写入 `cancelled_at` / `cancelled_by_id` / `cancel_reason`。
  - 创建、阶段变更、作废写入 `client_inquiry_events`。
  - 逾期未跟进记录在列表中高亮，但不自动改变 `stage`。
  - 历史 inbound 记录仍可用于 `FEATURE_CLIENT_INQUIRY` / `FEATURE_DASHBOARD_RESPONSE`。
- 后端测试通过。
- 前端 build 通过。
- 必要 runtime smoke 通过。
- 文档状态更新为 BIZ-1a 完成对应验证状态。

## 执行细则

### 硬约束

1. 不动 `index.html` / `admin.html` / `app.html`，不生成 HTML。
2. 不启动 Phase 4b / 4c / 6，不重启 Phase 4a；Phase 4a 当前按 2026-05-19 运行态验收完成处理。
3. `PUBLIC_ACCESS_ENABLED=false` 保持不变。
4. 新增表结构只走 Alembic，不退回依赖 `AUTO_CREATE_TABLES` 或启动兼容迁移。
5. 不做 BIZ-1b / BIZ-1c / BIZ-1d / BIZ-2a。
6. PATCH 授权规则严格按本文件 "PATCH 授权规则" 执行。
7. 前端不新增 `ai-web/src/views/BusinessLedger.vue`，不引入 router/views 目录结构。
8. 每完成一步先跑该步通过条件，再进入下一步。

### 文档常量基准

实现前必须重新打开 `STATE-MACHINES.md` 的 BIZ-1a 段落并逐字抄入代码常量：

- `direction` 合法值：`inbound` / `outbound`。
- `stage` 合法值：`初步接触` / `需求确认` / `报价中` / `跟进议价` / `成单` / `丢单`。
- `stage` 终态集合：`成单` / `丢单`。
- `cancelled_at` 存在表示软删除，不是 `stage` 值；取消后记录不得参与默认列表。

### 步骤 0：基线确认

- 读本计划、`API-CONTRACTS.md`、`STATE-MACHINES.md`、`FEATURE-FLAGS.md` 中 BIZ-1a 相关段。
- 跑 `python -m alembic current`，应为 `20260514_0015 (head)`。
- 跑 `python -m pytest -q`，应保持当前基线通过。
- 跑 `npm run build`（`ai-web/`），应通过。

通过后进入步骤 1。

### 步骤 1：配置项

- 改 `AI_Middle_Office/app/core/config.py`：新增 `feature_business_ledger`，从环境变量 `FEATURE_BUSINESS_LEDGER` 读取，默认 `False`。
- 改 `AI_Middle_Office/.env.example`：新增 `FEATURE_BUSINESS_LEDGER=false`。
- 不改真实 `.env`。

通过条件：能导入 `settings.feature_business_ledger`，默认值为 `False`。

### 步骤 2：Alembic 迁移

- 新建 `AI_Middle_Office/alembic/versions/20260520_0016_add_business_ledger.py`。
- `down_revision = "20260514_0015"`。
- 扩展 `client_inquiries`：
  - `direction VARCHAR`，非空，默认 `inbound`，历史行回填 `inbound`。
  - `stage VARCHAR`，可空。
  - `next_followup_at DATETIME`，可空。
  - `cancelled_at DATETIME`，可空。
  - `cancelled_by_id INT`，可空，外键到 `users.id`。
  - `cancel_reason TEXT`，可空。
  - `first_response_time` 调整为 nullable。
- 新建 `client_inquiry_events`：
  - `id`
  - `inquiry_id`
  - `event_type`
  - `old_value`
  - `new_value`
  - `operator_id`
  - `operated_at`
  - `ip_address`
  - `user_agent`
  - `trace_id`
  - `before_json`
  - `after_json`
- `downgrade()` 删除事件表和新增字段，并尽力还原 `first_response_time` 非空约束。

通过条件：`alembic upgrade head` 成功；`alembic downgrade -1` 成功；再 `alembic upgrade head` 成功。

### 步骤 3：ORM 模型

- 改 `AI_Middle_Office/app/models/client_inquiry.py`：补 BIZ-1a 字段。
- 新建 `AI_Middle_Office/app/models/client_inquiry_event.py`：定义 `ClientInquiryEvent`。
- 如有模型汇总文件需要导入，则补充导入，保证启动时 SQLAlchemy metadata 可见。

通过条件：导入模型后 `ClientInquiry.__table__.columns.keys()` 包含 BIZ-1a 字段。

### 步骤 4：Service 层

- 新建 `AI_Middle_Office/app/services/business_ledger.py`。
- 提供：
  - `create_outbound`
  - `list_ledger`
  - `get_ledger`
  - `update_ledger`
  - `cancel_ledger`
  - `_log_event`
- 权限检查放在 `business_ledger.py` 内部，使用现有角色判断；不新增权限模型，不改 `rbac.py`。
- `app/services/client_inquiries.py` 保持 Phase 2 inbound 语义，必要查询明确排除 `outbound`。
- `app/services/response_dashboard.py` 响应速度聚合查询必须加 `direction='inbound'`，避免 BIZ-1a outbound 记录污染 Phase 2 看板。

通过条件：`python -m compileall app/services` 通过。

### 步骤 5：API 路由

- 新建 `AI_Middle_Office/app/api/v1/business_ledger.py`。
- 实现 `API-CONTRACTS.md` 的 5 个端点：
  - `POST /api/v1/business-ledger`
  - `GET /api/v1/business-ledger`
  - `GET /api/v1/business-ledger/{id}`
  - `PATCH /api/v1/business-ledger/{id}`
  - `POST /api/v1/business-ledger/{id}/cancel`
- 查询参数使用契约中的 `stage`、`source`、`responder_id`、`overdue_only`、`date_from`、`date_to`、`keyword`、`page`、`page_size`。
- 每个端点先检查 `settings.feature_business_ledger`，关闭时返回 `404 NOT_FOUND`。
- 改 `AI_Middle_Office/app/main.py` 注册新路由。

通过条件：`python -m compileall app` 通过，FastAPI 导入不报错。

### 步骤 6：后端测试

- 新建 `AI_Middle_Office/tests/test_business_ledger_biz1a.py`。
- 至少覆盖：
  - feature flag 关闭时 5 个端点均返回 `404 NOT_FOUND`。
  - staff 创建时负责人必须是自己。
  - staff 只能 PATCH 自己的 `stage`、`next_followup_at`、`client_phone`、`notes`。
  - staff 不得 PATCH 别人的记录。
  - admin / system_admin 可转交 `responder_id`，可改 `source`。
  - `direction` 不允许 PATCH。
  - cancel 必填 `reason`；成功后写 `cancelled_*` 字段。
  - cancel 后 PATCH 返回 `409 STATE_CONFLICT`。
  - `stage` 进入 `成单` / `丢单` 后 PATCH 返回 `409 STATE_CONFLICT`。
  - 历史 `inbound` 记录不出现在商务台账默认列表里。
  - 创建、stage 变更、cancel 写入 `client_inquiry_events`。

通过条件：`python -m pytest tests/test_business_ledger_biz1a.py -v` 通过；全量测试通过。

### 步骤 7：前端

- 改 `ai-web/src/App.vue`：
  - 新增 `/admin/business-ledger` 路由。
  - 页面、表格、筛选、新建、编辑、作废弹窗全部内联。
  - 列表展示项目 / 客户、阶段、负责人、下次跟进时间、信息来源。
  - `next_followup_at < now()` 且 `stage` 非终态时高亮逾期。
  - 作废弹窗强制填写 `reason`。
  - 功能关闭时显示统一 "功能未开启" 状态。
- 改 `ai-web/src/style.css`：新增必要样式。

通过条件：`npm run build` 通过；本地页面自查列表、筛选、新建、编辑、作废、逾期高亮、功能关闭状态。

### 步骤 8：Smoke 脚本

- 新建 `AI_Middle_Office/scripts/biz1a_business_ledger_smoke.ps1`。
- 覆盖：
  - admin 登录。
  - 创建 outbound 台账。
  - GET 列表。
  - PATCH 到 `报价中`。
  - PATCH 到 `成单`。
  - 终态后 PATCH 期望 409。
  - 新建另一条后 cancel。
  - 作废后 PATCH 期望 409。
  - 使用本地 Python 直连当前 DB 查询 `client_inquiry_events` 数量，不新增事件查询 API。

通过条件：`FEATURE_BUSINESS_LEDGER=true` 的当前环境端到端 smoke 通过。

### 步骤 9：文档收尾

- 更新 `ROADMAP.md`、`AGENTS.md`、`AI_Middle_Office/AGENTS.md`。
- 更新 `API-CONTRACTS.md`、`STATE-MACHINES.md`、`FEATURE-FLAGS.md` 中 BIZ-1a 状态。
- 在本计划末尾追加 "验收结果"：Alembic head、pytest 计数、build 结果、smoke 结果、日期。
- 用 `git diff --stat` 与本文件 "预计涉及文件" 清单核对；多余文件需解释，缺漏文件需补齐或调整计划。

通过条件：文档状态与实际验证结果一致。

### 步骤 10：最终验收

- `python -m alembic current`：`20260520_0016 (head)`。
- `python -m compileall app scripts` 通过。
- `python -m pytest` 全量通过。
- `npm run build` 通过。
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\biz1a_business_ledger_smoke.ps1` 通过。
- `/admin/business-ledger` 五个核心场景自查通过。
