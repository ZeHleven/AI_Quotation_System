# 生产化加固规划：时区、双机链路、旧页迁移与前端验收

> 日期：2026-06-08
> 范围：面向小范围内网试运行到正式生产前的工程化加固。

## 背景判断

当前系统已具备报价主链路、成本库、需求单标准化、运营复核、项目进度和经营总览能力。下一阶段重点不应继续扩功能面，而是降低试运行和生产上线时的定位成本、回滚成本和长期维护成本。

本规划承接四个风险点：

- 时区口径混用：UTC aware、Asia/Shanghai naive、数据库 `NOW()` / `UTC_TIMESTAMP()` 补偿并存。
- 双机网络单点：Windows FastAPI 依赖 CentOS 侧 RAG、n8n、Redis、MinIO 等服务。
- 旧 HTML 与 Vite 并行：`index.html` / `admin.html` 与 Vite 管理台共存。
- 前端验收薄弱：当前主要依赖 `npm run build` 和人工验收，缺少自动化关键路径保护。

## P0：时区基线收口

目标：先明确口径，不立刻大迁移。

已开始：

- 新增 `app/core/time_utils.py`，统一声明应用展示时区为 `Asia/Shanghai`。
- 新增 helper：
  - `utc_now()`：新运行时和审计记录优先使用 aware UTC。
  - `app_now()`：展示层和业务日历聚合使用 aware Asia/Shanghai。
  - `app_local_naive()`：兼容历史 naive DB 字段。
  - `as_utc()`：把 naive 业务时间明确按 Asia/Shanghai 转为 UTC。
- 已将商务台账、客户咨询、执行任务、项目进度中的重复本地时间 helper 收口到统一模块。

后续建议：

- 新增数据库字段时一律使用 aware UTC 语义，并在 API 层返回 ISO 字符串。
- 对 `quote_jobs`、`client_inquiries`、`execution_tasks`、`project_tasks` 的按天/周/月聚合逐步改为统一 helper。
- 正式迁移前生成只读审计报告，列出各表时间字段、naive 样本比例、可安全转换规则和不可修复边界。

验收口径：

- 单测固定 naive 值默认按 Asia/Shanghai 解释。
- 看板聚合返回继续标明 `timezone=Asia/Shanghai`。
- 不因时区收口改变历史报价、成本库和项目进度数据。

## P1：双机链路韧性

目标：IP 漂移或 CentOS 服务异常时，系统能快速暴露故障点。

已开始：

- 新增 `READY_CHECK_EXTERNAL_SERVICES=false` 配置，默认保持本地开发 ready 检查轻量。
- 当该配置打开时，`/health/ready` 会额外探测 Redis、RAG、MinIO、n8n，并在任一异常时返回 `status=degraded`。
- ready 返回体只暴露服务 key/name/status/latency/detail 摘要，避免把完整连接串放到公开健康检查里。

后续建议：

- 生产 `.env` 打开 `READY_CHECK_EXTERNAL_SERVICES=true`。
- CentOS 固定静态 IP，或改为内网 DNS 名称并同步更新 `RAG_SERVICE_URL`、`N8N_WEBHOOK_URL_*`、Redis、MinIO 配置。
- `start_all.ps1` 和试运行每日检查表读取 `/health/ready` 与 `/api/v1/admin/ops/dashboard`，把链路异常作为暂停试运行条件。

验收口径：

- RAG/n8n 不可达时 ready 降级。
- 运维面板仍保留完整服务详情和告警聚合。
- 不让外部探活成为本地测试和开发启动的强依赖。

## P2：旧 HTML 与 Vite 迁移边界

目标：保留稳定主链路，停止无边界并行。

建议迁移顺序：

1. 管理侧低风险页面优先迁移到 Vite：用户权限、运维监控、成本库只读/审计、经营总览。
2. 报价辅助页后迁移：我的报价历史、草稿列表、运营复核详情。
3. 最后迁移旧 `index.html` 报价工作台主流程：上传、报价进度、AI 预审、人工改价、草稿恢复、确认下发。

边界要求：

- 旧工作台未迁移前，报价主链路 bug fix 仍在 `index.html` 落地。
- 新页面复用现有 API，不改变报价规则、价格口径、成本库 active 规则和确认下发阻断条件。
- 每迁移一个旧页入口，需要补一条关键路径验收记录和回退入口说明。

## P3：前端自动化验收

目标：先覆盖业务关键路径，不追求一次性高覆盖率。

建议第一批 E2E：

- 登录并进入 Vite 管理台。
- 需求单标准化上传、列映射、确认清单生成。
- 从确认清单发起报价任务并跳转旧工作台。
- 旧工作台恢复报价任务、展示预审、修改人工价、保存草稿。
- 确认下发成功后，在报价历史和运营详情可追溯。
- 成本库状态与流向、RAG 同步状态、审计记录可访问。

落地策略：

- 首选 Playwright，单独新增 `ai-web` 的 `test:e2e` 脚本和 `e2e/` 目录。
- 测试账号和样例清单走试运行模板数据，不依赖真实客户资料。
- 无法连接 CentOS 或外部 AI 链路时，E2E 允许只跑登录、路由、只读看板和草稿恢复等离线子集。

## 暂不做

- 不在本轮一次性修改所有数据库时间字段。
- 不迁移旧 `index.html` 报价主流程。
- 不改变 N8N/Dify/RAG 工作流。
- 不新增经营数据表或改变成本库 active 口径。
- 不把外部依赖探活默认打开，以免本地开发和单测被 CentOS 状态绑定。
