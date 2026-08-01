---
title: 练习 05：RESTful 异步任务接口、权限、幂等、状态码与分页实战
category: 后端基础设施练习记录
tags:
  - RESTful API
  - FastAPI
  - Idempotency-Key
  - Authorization
  - HTTP Status
  - Cursor Pagination
  - OpenAPI
  - AI 报价中台
practiced_at: 2026-07-30
status: 已完成
---

# 练习 05：RESTful 异步任务接口、权限、幂等、状态码与分页实战

## 实验结论

本次使用 FastAPI 0.136、Pydantic 2.13、SQLAlchemy 2.0 和 HTTPX 0.28，在进程内完成一个异步报价任务 API，并通过 16 项契约检查：

- 未认证返回 401；
- Schema 不合法返回 422；
- 异步创建返回 202 和 `queued`；
- 相同幂等键、相同内容返回首次任务；
- 相同幂等键、不同内容返回 409；
- 资源拥有者和管理员可以读取；
- 其他用户得到 404，不泄露资源是否存在；
- 重复取消保持同一终态；
- 取消任务可以创建新的重试任务；
- 排队任务禁止重试并返回 409；
- 游标分页的前后两页没有重复；
- 第 4 个超限创建请求返回 429 和 `Retry-After`；
- 请求 Trace ID 原样出现在响应；
- OpenAPI 声明 200、202、409、422 和 429。

核心认识：

```text
URL 和方法定义资源操作
HTTP 状态码表达处理语义
Schema 约束输入
权限约束谁能操作
状态机约束何时能操作
Idempotency-Key 约束重复副作用
Trace ID 支撑排障
OpenAPI 固化接口契约
```

---

## 1. 安全边界

| 项目 | 本次做法 |
|---|---|
| 应用 | 独立 FastAPI 练习应用 |
| 调用 | `TestClient` 进程内请求 |
| 网络端口 | 未开放 |
| 数据库 | 内存 SQLite |
| 持久化文件 | 未创建 |
| 当前报价 API | 未调用、未修改 |
| 正式数据库 | 未连接 |
| Redis/Celery | 未连接 |

本次验证的是 API 契约，不是当前项目正式接口验收。

---

## 2. 接口设计

```text
POST /api/v1/practice/quote-jobs
GET  /api/v1/practice/quote-jobs
GET  /api/v1/practice/quote-jobs/{job_id}
POST /api/v1/practice/quote-jobs/{job_id}/cancel
POST /api/v1/practice/quote-jobs/{job_id}/retry
```

### 为什么创建返回 202

请求只完成：

```text
输入和权限校验
→ 创建 queued 任务
→ 接受后台处理
```

模型、RAG 和 N8N 尚未完成，因此：

```text
202 Accepted
```

比 `200 OK` 更准确地表达“已接收但尚未处理完成”。

---

## 3. 统一响应与错误结构

成功：

```json
{
  "code": 202,
  "message": "报价任务已接受",
  "data": {
    "job_id": "...",
    "status": "queued"
  },
  "meta": {
    "replayed": false
  }
}
```

失败：

```json
{
  "code": 409,
  "message": "相同幂等键不能对应不同请求内容",
  "data": null,
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "details": {
      "operation": "create_quote_job"
    }
  },
  "trace_id": "practice-trace-001"
}
```

要求：

- HTTP 状态与响应体一致；
- 程序使用稳定错误码，不解析中文；
- `details` 只包含安全诊断信息；
- Trace ID 支撑日志定位；
- 不返回堆栈、SQL、密钥和内部路径。

---

## 4. 状态码实验

| 场景 | HTTP | 稳定错误码 |
|---|---:|---|
| 缺少身份 | 401 | `AUTH_REQUIRED` |
| 输入长度不合法 | 422 | `VALIDATION_ERROR` |
| 异步创建成功 | 202 | 无 |
| 幂等重放 | 200 | 无 |
| 幂等键内容冲突 | 409 | `IDEMPOTENCY_CONFLICT` |
| 其他用户访问 | 404 | `QUOTE_JOB_NOT_FOUND` |
| 当前状态不允许重试 | 409 | `QUOTE_JOB_STATE_CONFLICT` |
| 超过创建频率 | 429 | `QUOTE_CREATE_RATE_LIMITED` |

### 401 与 403

```text
401：没有有效身份
403：身份有效，但没有权限执行该动作
```

本次对越权读取返回 404，是为了不向其他用户泄露任务是否存在。

---

## 5. Idempotency-Key

### 服务端记录

```text
owner
operation
idempotency_key
request_hash
job_id
created_at
```

唯一约束：

```text
owner + operation + idempotency_key
```

### 请求内容哈希

先对 JSON 做稳定规范化：

```text
字段排序
→ 紧凑序列化
→ SHA-256
```

避免字段顺序不同却被误判成不同内容。

### 三种处理

| 情况 | 处理 |
|---|---|
| 新 Key | 创建任务，返回 202 |
| 同 Key + 同 Hash | 返回原任务，返回 200 |
| 同 Key + 不同 Hash | 返回 409 |

### 真实结果

```text
首次状态 = 202
重放状态 = 200
首次与重放 job_id 相同 = true
冲突状态 = 409
```

重复请求没有创建第二个任务。

---

## 6. 身份、权限与资源归属

本次简化身份头：

```text
X-User
X-Role
```

真实系统应使用 JWT、Session 或可信网关身份。

资源读取结果：

| 调用者 | 状态 |
|---|---:|
| 任务拥有者 | 200 |
| 其他普通用户 | 404 |
| 管理员 | 200 |

查询时必须同时限定资源归属，而不是先查出数据再在响应阶段过滤。

正确层次：

```text
Authentication：你是谁
Authorization：你是否有该能力
Ownership：你是否能操作这个对象
Tenant Scope：对象属于哪个租户
```

---

## 7. 取消接口必须幂等

第一次取消：

```text
queued → cancelled
HTTP 200
```

第二次取消：

```text
cancelled → cancelled
HTTP 200
meta.replayed = true
```

重复取消没有再次产生业务效果。

注意：

- 修改数据库状态不代表外部任务一定已经停止；
- Worker 需要检查 `cancel_requested`；
- 已发生的外部副作用可能需要补偿；
- 取消操作本身应记录操作者和事件。

---

## 8. 重试创建新任务

原任务：

```text
status = cancelled
```

重试：

```text
创建新的 queued 任务
retry_of = 原 job_id
HTTP 202
```

相同重试幂等键再次提交：

```text
返回同一个 retry job
HTTP 200
```

对新的 `queued` 任务再次请求重试：

```text
HTTP 409
error.code = QUOTE_JOB_STATE_CONFLICT
```

规则：

```text
只有 failed / cancelled / timed_out 可以重试
```

保留原任务并创建新任务，便于审计每次尝试。

---

## 9. 游标分页

请求：

```text
GET /quote-jobs?limit=1
```

响应：

```json
{
  "items": [],
  "next_cursor": 2,
  "has_more": true
}
```

下一页：

```text
GET /quote-jobs?limit=1&cursor=2
```

真实结果：

```text
第一页 = 1 条
第二页 = 1 条
两页 job_id 不重复
```

游标查询近似：

```sql
WHERE id < :cursor
ORDER BY id DESC
LIMIT :limit
```

它避免深 Offset 扫描，但不支持任意跳页；排序字段必须稳定且能唯一定位。

---

## 10. 限流

本次练习限制：

```text
同一用户最多创建 3 个任务
```

四次请求结果：

```text
202, 202, 202, 429
```

429 响应包含：

```text
Retry-After: 30
```

本次使用进程内计数，仅用于接口语义验证。多实例正式限流必须使用：

- Redis；
- API Gateway；
- 专用限流服务；
- 用户/租户/模型额度。

---

## 11. Trace ID

请求：

```text
X-Trace-ID: practice-trace-001
```

响应：

```text
X-Trace-ID: practice-trace-001
```

错误体也包含同一 Trace。

真实系统中还应继续传递到：

```text
FastAPI
→ Celery task
→ RAG
→ 模型网关
→ N8N
→ 外部推送
```

---

## 12. OpenAPI

创建任务接口声明：

```text
200：相同幂等请求重放
202：新任务已接受
409：幂等或状态冲突
422：请求校验失败
429：超过速率
```

实验从 `/openapi.json` 读取并确认这些响应存在。

OpenAPI 的价值：

- 前后端共享契约；
- 自动生成客户端；
- 契约回归；
- 明确错误状态；
- 支撑 API 评审。

自动生成不代表文档自动正确；业务错误和示例仍需维护。

---

## 13. 数据验证

实验结束前内存数据库：

| 表 | 行数 |
|---|---:|
| 报价任务 | 5 |
| 幂等记录 | 5 |

这些数据来自：

- Alice 的首次任务和重试任务；
- 限流用户成功创建的 3 个任务。

幂等重放、冲突请求、校验失败和第 4 个限流请求都没有增加任务行。

---

## 14. 与报价中台映射

### 已有真实接口

```text
POST /api/v1/quote/jobs
GET  /api/v1/quote/jobs/{job_id}
GET  /api/v1/quote/jobs/{job_id}/events
POST /api/v1/quote/jobs/{job_id}/cancel
POST /api/v1/quote/jobs/{job_id}/retry
POST /api/v1/confirm_push
```

### 已有基础

- FastAPI `/api/v1` 版本前缀；
- Pydantic Schema；
- JWT/RBAC；
- 用户和管理员资源访问控制；
- 异步任务状态、事件、取消和重试；
- `api_ok`、`api_page` 统一成功响应；
- 自动 OpenAPI。

### 可继续核对

- 异步创建是否统一使用 202；
- 错误是否有稳定机器码；
- `confirm_push` 是否支持正式 `Idempotency-Key`；
- 相同 Key、不同内容是否返回 409；
- Trace 是否贯穿 Celery、RAG、模型和 N8N；
- 多实例限流是否统一使用 Redis；
- 大列表是否需要游标分页。

练习方案不是要求立刻修改现有 API，应先做兼容性和调用方影响分析。

---

## 15. 面试回答

### 怎样设计异步任务 API？

> 创建接口先校验身份、权限和输入，在数据库中保存 queued 任务，再返回 202、任务 ID 和状态查询地址。客户端通过详情或事件接口获取进度，取消和重试由状态机约束。重试保留原任务并创建新任务，便于审计。所有响应携带 Trace ID，错误使用稳定机器码。

### 怎样设计幂等 API？

> 客户端传 `Idempotency-Key`，服务端按用户、操作和 Key 建唯一约束，并保存规范化请求 Hash 和首次结果。相同 Key、相同内容返回原任务；相同 Key、不同内容返回 409。我在练习中验证首次创建返回 202，重放返回同一 job ID，冲突请求没有增加数据。

### 为什么越权读取返回 404？

> 对某些用户资源返回 404 可以避免泄露资源是否存在。服务端查询时直接带用户或租户作用域；管理员通过明确权限读取。401 表示未认证，403 表示身份有效但无动作权限，404 是资源不可见策略。

### 为什么使用游标分页？

> 深 Offset 会扫描和丢弃大量记录，数据变化时还可能重复或漏项。游标分页按稳定的唯一排序键继续查询，更适合大列表和持续滚动；代价是不方便随机跳页。

---

## 16. 练习脚本

文件：

```text
AI_Middle_Office/scripts/rest_api_contract_practice.py
```

执行：

```powershell
cd AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m scripts.rest_api_contract_practice
```

脚本使用内存数据库，进程退出后练习数据自动消失。

---

## 17. 本次没有证明什么

- 没有调用或修改正式报价 API；
- 没有使用真实 JWT、RBAC 和租户模型；
- 没有测试并发请求争抢同一幂等键；
- 没有把创建任务真正发送到 Celery；
- 没有验证 Redis 多实例限流；
- 没有做网络延迟和负载测试；
- 没有验证 SSE、WebSocket 或 gRPC；
- 没有形成现有 API 的迁移方案。

---

## 18. 下一步练习

RESTful API 的基础闭环已经覆盖：

```text
资源与方法
→ Schema
→ 状态码
→ 身份和归属
→ 幂等
→ 状态机
→ 游标分页
→ 限流
→ Trace
→ OpenAPI
```

下一步建议进行服务治理与故障保护：健康检查、超时预算、有限重试、熔断、降级和资源隔离。
