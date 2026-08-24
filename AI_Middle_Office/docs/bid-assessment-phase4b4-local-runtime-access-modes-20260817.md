# 旗胜投标机会研判 Agent Phase 4B-4：本地 Runtime 双模式门禁

> 版本：v0.1-r49  
> 日期：2026-08-17  
> 状态：代码与授权专项验证完成；仅限隔离本地 Runtime Lab，不得应用到 ECS

## 1. 目标

把本地 Runtime Lab 从“依靠启动习惯避免误执行”收口为服务端权威的两种互斥模式：

- `view-only`：只允许读取已有 Run、Trace、Report 和 Capability；不初始化 Schema、不启动 Worker、不接受写请求、不加载密钥、不调用模型或外部网络。
- `execute`：显式允许本地初始化、写请求和进程内 Worker；DeepSeek 模式必须在替换既有服务前通过非占位密钥门禁。

默认模式为 `view-only`。模式由进程环境和服务端 Capability 决定，前端状态不能扩大权限。

## 2. 权威边界

| 边界 | view-only | execute |
|---|---|---|
| HTTP | 仅 `GET/HEAD/OPTIONS` | 按 API/ACL/ETag/幂等合同执行 |
| 数据库 | 只读核验已有本地用户和冻结 Model Profile | 允许创建隔离 Schema、种子和业务数据 |
| Worker | 不创建 lifespan Worker | 创建进程内 Worker loop |
| 模型 | `model_calls_enabled=false` | 由冻结 Model Profile 和 Gateway 控制 |
| DeepSeek 密钥 | 不读取 `SecretEnvFile`，不要求 Key | 必须为非空、非占位、长度合格的 Key |
| SSE | 关闭自动实时更新 | V1 Runtime 开启时允许 |
| 外部网络 | `disabled_by_design` | 仅受控 Provider 合同允许的官方端点 |

view-only 的非安全方法统一在 FastAPI 中间件、路由和数据库依赖之前返回：

```json
{
  "code": 403,
  "error": {
    "code": "BID_MVP1_VIEW_ONLY",
    "retryable": false
  }
}
```

## 3. Capability 与前端门禁

Capability 新增并由服务端生成：

- `access_mode`
- `execution_enabled`
- `write_enabled`
- `worker_enabled`
- `worker_running`
- `model_calls_enabled`
- `model_provider`
- `retrieval_mode`

前端只有在 `access_mode=execute` 且 `execution_enabled/write_enabled/worker_running/assessment_intake_enabled` 全为 `true` 时才开放上传和启动 Run。view-only 下表单、文件选择、标段确认和提交按钮均禁用；即使绕过前端，服务端仍返回 `BID_MVP1_VIEW_ONLY`。

## 4. 启停协议

启动器新增：

- `-AccessMode view-only|execute`，默认 `view-only`；
- `-LabDirectoryName`，只接受当前工作目录下 `.local-mvp1[-name]`，用于隔离专项实例；
- 启动后必须通过 health 与 Capability 的 mode/write/worker 自校验；
- view-only 必须指向已存在且 Model Profile 匹配的数据库；
- execute/DeepSeek 在创建目录、替换监听进程之前验证密钥。

停止器优先使用 PID 文件；PID 文件缺失时，只能在显式传入 `-Port` 后按 localhost listener 定位，并以进程命令行、对应启动日志或本服务 health identity 至少一种方式确认是 `app.mvp1_local:app`，否则拒绝停止。

## 5. 授权验证结果

自动专项：

- 双模式合同、Capability 投影、前端权威门禁：`5 passed`；
- DeepSeek/local mode 配置相邻回归：`1 passed`；
- SSE cursor/持久化/ACL/fail-closed 相邻回归：`2 passed`；
- 合并矩阵：`8 passed / 0 failed`；
- Python compile 与 PowerShell AST：通过；
- Vite：`2235 modules` 生产构建通过。

动态隔离专项：

- 缺失 Key、占位 Key 均在创建测试目录和监听端口前被拒绝；
- fresh deterministic execute：health/capability 均为 execute，Worker running，Assessment POST `201`；数据库为 `1 Assessment / 0 Run / 0 ModelCall`；
- 同一库切换 view-only：GET Run/Assessment `200`，POST/PUT/PATCH/DELETE 均为 `403/BID_MVP1_VIEW_ONLY`；停止后 SQLite SHA-256 与启动前一致；
- view-only Model Profile 不匹配：启动失败、无监听、数据库 SHA-256 不变；
- Phase 4B-3 RQ2-B 历史库：view-only GET 正常，停启前后数据库 SHA-256 均为 `1EFC35CB...53942`；
- 启停专项暴露 PID 文件不可见窗口后，停止器增加显式 Port + 服务身份回退核验并复测通过。

## 6. 当前运行状态与限制

`http://127.0.0.1:9003/admin/bid-assessment-runtime-lab` 当前为：

- `access_mode=view-only`
- `write_enabled=false`
- `worker_running=false`
- `model_calls_enabled=false`
- `retrieval_mode=rq2b`
- `external_network=disabled_by_design`

本增量未读取 PDF，未调用 OCR、视觉、Embedding、Reranker、生成模型或外部 MCP；未连接外部环境；没有新增数据库结构或 Alembic revision，唯一 head 仍为 `20260815_0103`。

