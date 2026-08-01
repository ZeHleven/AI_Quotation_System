---
title: API 通信：RESTful 设计、RPC 与 gRPC
category: 后端基础设施
tags:
  - REST
  - HTTP
  - RPC
  - gRPC
  - FastAPI
  - API Design
reviewed_at: 2026-07-30
status: 持续更新
---

# API 通信：RESTful 设计、RPC 与 gRPC

> 配套实战：[练习 05：RESTful 异步任务接口、权限、幂等、状态码与分页实战](./练习记录/05-RESTful异步任务接口权限幂等状态码与分页实战.md)

## 核心结论

API 是跨团队、跨进程的长期契约，不只是“能调用的函数”。

一个生产级 API 必须明确：

```text
资源或动作
输入 Schema
身份和权限
成功结果
失败语义
幂等性
超时和取消
分页与版本
Trace 和审计
```

REST 常用于浏览器、开放平台和普通业务服务；gRPC 常用于内部强类型、低开销或流式通信。选型应看调用关系和治理能力，不看技术名气。

---

## 1. RESTful 资源设计

资源用名词，HTTP 方法表达动作：

```text
POST   /api/v1/quote/jobs
GET    /api/v1/quote/jobs/{job_id}
GET    /api/v1/quote/jobs/{job_id}/events
POST   /api/v1/quote/jobs/{job_id}/cancel
POST   /api/v1/quote/jobs/{job_id}/retry
```

严格 REST 会倾向把取消建模成状态更新，但业务动作接口也可以接受。关键是语义稳定、权限清晰和副作用明确。

### 方法语义

| 方法 | 语义 | 通常是否幂等 |
|---|---|---|
| GET | 获取资源 | 是 |
| POST | 创建或执行业务动作 | 默认否 |
| PUT | 完整替换指定资源 | 是 |
| PATCH | 部分更新 | 取决于操作 |
| DELETE | 删除指定资源 | 应设计为幂等 |

“幂等”指重复相同请求的最终业务效果相同，不代表响应内容和时间完全一样。

---

## 2. HTTP 状态码

| 状态码 | 使用场景 |
|---|---|
| 200 | 查询或同步操作成功 |
| 201 | 资源创建成功 |
| 202 | 已接受异步处理，尚未完成 |
| 204 | 成功但无响应体 |
| 400 | 请求格式或一般参数错误 |
| 401 | 未认证 |
| 403 | 已认证但无权限 |
| 404 | 资源不存在或按安全策略隐藏 |
| 409 | 状态冲突、版本冲突、幂等键冲突 |
| 422 | Schema/字段语义校验失败 |
| 429 | 超过限流 |
| 500 | 未预期服务端错误 |
| 502/503/504 | 网关或依赖不可用/超时 |

不要所有失败都返回 HTTP 200，再只看业务 `code`。统一响应体可以保留，但 HTTP 状态本身必须表达传输和处理结果。

---

## 3. 错误契约

推荐结构：

```json
{
  "code": "QUOTE_JOB_STATE_CONFLICT",
  "message": "当前任务状态不允许重试",
  "details": {
    "current_status": "running",
    "allowed_statuses": ["failed", "cancelled", "timed_out"]
  },
  "trace_id": "trace-id"
}
```

要求：

- `code` 稳定，供客户端处理；
- `message` 可读，但不作为程序判断依据；
- `details` 只返回安全的诊断信息；
- `trace_id` 支持排障；
- 不泄露密钥、SQL、内部路径和完整堆栈。

---

## 4. 请求与响应 Schema

### 输入校验

校验层次：

```text
类型和格式
→ 字段组合
→ 权限和资源归属
→ 当前业务状态
→ 外部副作用前置条件
```

Pydantic 只解决第一部分，不能替代业务规则。

### 分页

Offset 分页：

```text
GET /quote/jobs?page=2&page_size=20
```

简单但深分页成本高，数据变化时可能重复或漏项。

Cursor 分页：

```text
GET /quote/jobs?cursor=encoded-last-id&limit=20
```

更适合大数据和持续滚动，但客户端不能随机跳页。

响应中应返回：

```json
{
  "data": [],
  "next_cursor": "...",
  "has_more": true
}
```

### 异步 API

```http
POST /api/v1/quote/jobs
```

返回：

```json
{
  "job_id": "...",
  "status": "queued",
  "status_url": "/api/v1/quote/jobs/..."
}
```

HTTP 状态使用 `202 Accepted` 更能表达“已接收但未完成”。

---

## 5. 鉴权、授权与多租户

顺序：

```text
Authentication：你是谁
Authorization：你能做什么
Resource Ownership：你能否操作这个具体对象
Tenant Scope：数据属于哪个租户
```

JWT 中的角色不能无限期相信：

- 用户角色可能变化；
- Token 要有过期时间；
- 高风险操作可校验 `role_version`；
- 服务端查询资源时必须带租户/归属条件；
- 管理员接口使用明确权限依赖。

不要先按 ID 查询对象，再在响应阶段过滤租户；应在查询条件中限制作用域。

---

## 6. 幂等 API

创建、付款、推送、确认等副作用操作可使用：

```http
Idempotency-Key: client-generated-uuid
```

服务端保存：

```text
key
user/tenant
operation
request_hash
status
response
expires_at
```

处理规则：

- 新 Key：开始处理；
- 相同 Key + 相同请求：返回处理中或原结果；
- 相同 Key + 不同请求：返回 409；
- 失败是否允许相同 Key 重试，要按错误类型定义。

---

## 7. 超时、取消和 Trace

每个外部调用必须有超时。调用链需要分配总预算：

```text
客户端预算 30s
网关预留 2s
服务 A 预算 25s
服务 B 预算 20s
重试必须包含在总预算内
```

如果每层各自超时 30 秒并重试三次，最外层可能等待数分钟。

请求头建议：

```text
traceparent / X-Trace-ID
Idempotency-Key
X-Request-ID
```

取消不是“前端关掉页面”。服务端必须定义：

- 是否只停止等待；
- 是否撤销后台任务；
- 外部调用能否真正取消；
- 已完成副作用如何处理。

---

## 8. RPC 与 gRPC

RPC 以“调用远程方法”为核心：

```text
QuoteService.CreateJob(request)
QuoteService.GetJob(request)
```

gRPC 使用 Protocol Buffers 定义强类型契约：

```proto
service QuoteService {
  rpc CreateQuoteJob(CreateQuoteJobRequest)
      returns (CreateQuoteJobResponse);
  rpc WatchQuoteJob(WatchQuoteJobRequest)
      returns (stream QuoteJobEvent);
}
```

### gRPC 优势

- 强类型和代码生成；
- 二进制序列化开销较低；
- 支持客户端/服务端/双向流；
- 适合内部服务契约。

### gRPC 代价

- 浏览器直接使用更复杂；
- 调试不如普通 JSON 直观；
- Proto 版本治理要求高；
- 网关、负载均衡和可观测性需要配套；
- 不会自动解决权限、幂等、超时和服务治理。

### REST 与 gRPC 选型

| 场景 | 建议 |
|---|---|
| 浏览器、第三方开放接口 | REST |
| 普通内部 CRUD | REST 也足够 |
| 高频内部强类型调用 | 可考虑 gRPC |
| 流式事件和双向通信 | gRPC/WebSocket/SSE 按场景选择 |
| 团队无 gRPC 运维经验 | 优先简单方案 |

---

## 9. 版本兼容

REST：

- URL 版本：`/api/v1/`；
- 新增可选字段通常向后兼容；
- 删除、改名和改变语义需要新版本或迁移期；
- 服务端不能假设所有客户端同时升级。

Proto：

- 字段编号一旦发布不要复用；
- 删除字段应 `reserved`；
- 新字段应有安全默认行为；
- 不要把 enum 默认值定义成真实业务状态，使用 `UNSPECIFIED`。

---

## 10. 报价中台映射

### 已有能力

- FastAPI 在 `app/main.py` 统一挂载 `/api/v1` 路由；
- `get_current_user`、`require_admin` 等依赖负责认证和授权；
- `api_ok`、`api_page` 提供统一响应结构；
- 异步报价提供创建、详情、事件、取消和重试接口；
- 报价详情按用户、角色和资源归属控制访问；
- OpenAPI 可由 FastAPI 自动生成。

### 继续练习

- 统一业务错误码，而不是让客户端解析中文；
- 明确哪些异步创建接口返回 `202`；
- 为 `confirm_push` 设计端到端幂等键；
- 对大文件上传采用对象存储引用，避免 Base64 长期进入 API 和数据库；
- 为内部 RAG/模型调用统一 Deadline 和 Trace 传递。

---

## 11. 面试回答

### REST 和 gRPC 怎样选？

> 浏览器和开放接口优先 REST，因为兼容性和调试体验更好；内部高频、强类型或流式调用可以考虑 gRPC。gRPC 的价值是契约和传输效率，但会增加 Proto 版本、网关和可观测性治理成本。当前报价中台以前端和多种 HTTP AI 服务为主，REST 更符合现状，只有内部稳定高频链路出现明确瓶颈时才引入 gRPC。

### 怎样设计异步任务 API？

> 创建接口先校验权限和输入，在 MySQL 中写入 queued 任务并提交，然后分发后台执行，返回 202、任务 ID 和状态查询地址。客户端通过详情或事件接口获得进度；取消、重试是显式动作，并由状态机约束。服务端还记录 Trace、失败阶段和耗时，避免前端依赖一个长连接才能知道结果。

### 401 和 403 有什么区别？

> 401 表示没有有效身份，403 表示身份有效但没有权限。资源归属检查还要防止越权；某些场景会对无权访问的资源返回 404，避免泄露资源是否存在。

---

## 12. 动手练习

设计一个报价任务 API：

- `POST /quote/jobs`；
- `GET /quote/jobs/{id}`；
- `GET /quote/jobs/{id}/events`；
- `POST /quote/jobs/{id}/cancel`；
- `POST /quote/jobs/{id}/retry`。

验收：

- OpenAPI Schema 完整；
- 401/403/404/409/422/429 语义正确；
- 有资源归属校验；
- 创建返回 202；
- 重复取消幂等；
- Trace ID 贯穿日志；
- 错误响应不泄露内部堆栈。

---

## 13. 掌握检查

- [ ] 能用资源和方法设计 REST API；
- [ ] 能正确使用常见 HTTP 状态码；
- [ ] 能设计稳定错误码；
- [ ] 能区分 Schema 校验和业务校验；
- [ ] 能设计 Offset/Cursor 分页；
- [ ] 能实现鉴权、授权、归属和租户隔离；
- [ ] 能设计幂等 API；
- [ ] 能传播超时、取消和 Trace；
- [ ] 能比较 REST、RPC 和 gRPC；
- [ ] 能说明 API 版本兼容规则。

---

## 代码证据

- `AI_Middle_Office/app/main.py`
- `AI_Middle_Office/app/dependencies.py`
- `AI_Middle_Office/app/core/responses.py`
- `AI_Middle_Office/app/api/v1/quote_jobs.py`
- `AI_Middle_Office/app/api/v1/quote.py`
- `AI_Middle_Office/tests/test_api_response_format.py`
- `AI_Middle_Office/tests/test_quote_jobs.py`
