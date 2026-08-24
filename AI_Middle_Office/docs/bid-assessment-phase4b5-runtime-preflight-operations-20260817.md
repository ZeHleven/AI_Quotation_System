# 旗胜投标机会研判 Agent Phase 4B-5：Execute Preflight 与运行操作面

> 版本：v0.1-r50  
> 日期：2026-08-17  
> 状态：代码、合同、自动专项、动态隔离验证与本地浏览器验收完成  
> 边界：仅限隔离本地 Runtime Lab，不得应用到 ECS

## 1. 目标

Phase 4B-5 把 Phase 4B-4 的双模式安全边界变成可观察、可操作但不能越权的本地工作台：

- 在上传或运行前显示非泄密 Execute Preflight；
- 在每次写提交前重新读取服务端权限，防止页面在进程模式变化后继续提交旧表单；
- 复用 API-41/42/43，提供 Run 安全取消和从最近 Checkpoint 重试；
- 保持 view-only 无 Worker、无写入、无模型凭据，且不能从浏览器提升为 execute。

## 2. Execute Preflight 合同

新增只读接口：

```text
GET /api/v1/bid-assessment-runtime-lab/execute-preflight
```

响应 Schema 为 `bid.runtime.execute-preflight.v1`，检查：

1. localhost/SQLite/本地对象目录/进程内队列隔离边界；
2. active Model Profile 与启动模式一致；
3. 本地对象目录可写；
4. DeepSeek execute 进程已加载凭据，view-only 仅标记为切换时校验；
5. `legacy/rq2b/rq2c` 检索白名单；
6. RQ2-B/C 的固定 BCE Embedding Snapshot 与冻结 Worker 依赖；
7. RQ2-C 的固定 BCE Reranker Snapshot；
8. Worker 生命周期与服务端写权限。
9. view-only 真实模型凭据隔离，覆盖父进程继承与项目 `.env` 回退。

接口只返回 `ready/blocked/deferred/inactive`、稳定阻断码和 Authority Fingerprint。禁止返回模型密钥值、绝对路径、模型文件内容或业务资料正文。

`launch_ready` 表示本地离线依赖无硬阻断；`current_process_ready` 只有在当前进程本身为 execute、Worker/写权限/模型权限都已生效时才为 true。view-only 即使依赖齐全，也必须停止进程并用启动器显式选择 execute。

## 3. 前端防误提交

工作台新增 Preflight 面板。创建 Assessment、上传资料、选择标段、取消 Run、重试 Run 前，前端都会重新读取 Capability 与 Preflight，并比较 Authority Fingerprint：

- 权限或依赖发生变化：清除未提交文件、关闭创建弹窗并终止请求；
- 当前进程不是 execute：按钮禁用；
- 绕过前端直接发写请求：仍由 Phase 4B-4 FastAPI 中间件返回 `403/BID_MVP1_VIEW_ONLY`。

view-only 启动器会用固定 `local-view-only-disabled` 哨兵覆盖进程中的 `BID_ASSESSMENT_MODEL_API_KEY` 与 `DEEPSEEK_API_KEY`。不能只删除环境变量，因为配置层还会回退读取项目 `.env`；非空禁用哨兵会同时截断父进程继承和 `.env` 回退，确保只读进程不持有真实模型凭据。Preflight 以 `VIEW_ONLY_SECRET_FENCE` 对这一不变量 fail closed。

## 4. Run 生命周期操作

操作面没有新增生命周期语义，只组合既有权威 API：

```text
API-41 GET  Run Snapshot -> 获取最新强 ETag
API-42 POST Run Cancel   -> Idempotency-Key + If-Match
API-43 POST Run Retry    -> Idempotency-Key + If-Match + from_latest_checkpoint
```

- 取消只持久化取消意图，由 Worker 围栏活跃 Attempt/Model/Tool 并收敛终态；
- 重试只允许 `failed + retryable` 的原 Run，创建更高 fencing token 的 Attempt；
- 页面不读取或编辑 Checkpoint payload，只使用服务端的最近不可变 Checkpoint；
- 任何 ETag 冲突都要求重新刷新后再次人工确认，不进行盲重试。

## 5. 数据与迁移

本增量复用既有 Run、Attempt、Checkpoint、Idempotency 和 Outbox 权威表，不新增数据库字段或枚举，不需要 Alembic revision。唯一 head 保持 `20260815_0103`；旧 `bid_intake_*` 不修改。

## 6. 验证结果

自动专项不重复矩阵：`22 passed / 0 failed`，覆盖：

- Phase 4B-5 合同/Schema、Preflight ready/blocked/deferred/inactive 与非泄密；
- Phase 4B-4 双模式与前端权威门禁；
- API-41/42/43 强 ETag、ACL、幂等、事务回滚；
- Tool/Model/Validation 活跃执行的取消 Fencing；
- Checkpoint 恢复、取消维护收敛和 SSE cursor/ACL/fail-closed 相邻链。

动态隔离专项：

- fresh `.local-mvp1-p4b5-check` deterministic/legacy execute：Preflight 11 项、0 blocker、`current_process_ready=true`，Assessment POST `201`；停止后数据库为 `1 Assessment / 0 Run / 0 ModelCall / 0 ToolInvocation`；
- 同库 view-only：GET Assessment `200`，POST/PUT/PATCH/DELETE 均为 `403/BID_MVP1_VIEW_ONLY`；父进程注入的测试 Key 不在 Preflight 出现，`VIEW_ONLY_SECRET_FENCE=ready`，绝对数据库路径不出现；启停前后 SQLite SHA-256 均为 `70E6B967...FB77`；
- Phase 4B-3 历史 RQ2-B 库升级到新版 view-only 后，页面、Capability、Preflight、5个历史 Run 均可读；`launch_ready=true`、`current_process_ready=false`、0 blocker、密钥围栏 ready，历史库 SHA-256 保持 `1EFC35CB...53942`；
- 本地浏览器只读验收：Readiness 11 项正常，创建/取消/Checkpoint重试按钮均禁用；成功历史 Run 的26 Task、92 Checkpoint与初筛报告可读，控制台无 error/warn。

静态检查：

- Python `py_compile`：通过；
- PowerShell AST：通过；
- JSON 合同解析：通过；
- Vite 生产构建：`2235 modules`，通过。

验证过程中发现并修复“删除进程 Key 后仍可能回退读取项目 `.env`”的密钥隔离缺口，最终方案改为固定禁用哨兵并由 Preflight fail closed。全程未读取真实 PDF，未调用 OCR、视觉、Embedding、Reranker、生成模型或外部 MCP，未连接任何外部环境。
