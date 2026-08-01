---
title: LLM 平台工程：统一网关、资源限流、延迟拆解与安全缓存
category: LLMOps 与可观测性
tags:
  - LLM Gateway
  - Rate Limiting
  - Token Quota
  - TTFT
  - Semantic Cache
  - Model Routing
  - FDE
source:
  - title: 企业接入大模型，为何先建 LLM 网关
    url: https://www.douyin.com/video/7662407760881005888
  - title: 大模型限流为何不能只看请求数
    url: https://www.douyin.com/video/7662761120793070287
  - title: 大模型响应速度为何不只看总耗时
    url: https://www.douyin.com/video/7663514634599677114
  - title: 大模型语义缓存为什么不能只存问答
    url: https://www.douyin.com/video/7662407432291960805
reviewed_at: 2026-07-28
status: 持续更新
---

# LLM 平台工程：统一网关、资源限流、延迟拆解与安全缓存

## 核心结论

企业接入多个模型后，稳定性问题不能由每个业务系统各自解决。生产级 LLM 平台需要形成一条统一受控链路：

```text
业务请求
→ 身份、租户和权限校验
→ Token、成本和并发准入
→ 安全缓存判断
→ 模型路由
→ 调用、重试、降级和熔断
→ 流式返回
→ 用量结算
→ Trace、指标和审计
```

四个核心组件分别回答：

| 组件 | 核心问题 |
|---|---|
| LLM Gateway | 谁在调用什么模型，为什么这样路由，失败后怎样处理？ |
| 资源限流 | 这次请求是否有足够的 Token、成本和并发预算？ |
| 延迟拆解 | 慢在排队、检索、首 Token、生成还是工具链？ |
| 安全缓存 | 哪些结果可以复用，怎样避免越权、过期和答非所问？ |

> 资料说明：本笔记依据视频的公开简介、摘要和章节结构整理，并结合当前 AI 智能报价中台代码扩展，不是逐字字幕。

---

## 1. LLM Gateway 不只是“统一转发”

普通反向代理主要处理域名、连接和 HTTP 转发。LLM Gateway 还要理解模型调用的治理语义：

- 调用者、租户、应用和业务场景；
- 模型、能力、上下文长度和风险等级；
- 输入与输出 Token；
- 流式响应；
- 模型限额、成本和服务状态；
- Prompt、知识库、工具与策略版本；
- 结果质量、安全和审计要求。

因此：

```text
API Gateway
负责系统入口、JWT、路由和通用安全

LLM Gateway
负责模型调用的统一治理
```

二者可以部署在一起，但职责不能混淆。

### 业务直连模型的问题

当每个系统分别直连模型供应商时，通常会出现：

1. API Key 散落在不同服务；
2. 鉴权、重试和超时规则不一致；
3. 业务代码绑定某个供应商协议；
4. 无法统一统计 Token 和费用；
5. 故障时无法快速切换模型；
6. 不知道某次结果由哪个模型和参数产生；
7. 不同团队重复实现相同治理逻辑；
8. 高风险数据可能被错误发送到不允许的模型。

### 网关的最小职责

```text
身份与租户
+ 统一模型协议
+ 策略路由
+ 限流与配额
+ 超时、重试、降级和熔断
+ 用量与成本
+ Trace 与审计
```

网关不应承担：

- 具体业务工作流；
- 报价、审批等领域规则；
- Agent 的全部状态；
- 为了“成功率”而隐藏所有错误；
- 未经评测的任意模型互换。

---

## 2. 建立统一调用契约

只有统一 URL，不代表已经完成模型治理。网关还需要统一请求上下文。

### 建议的请求信封

```json
{
  "request_id": "req_xxx",
  "trace_id": "trace_xxx",
  "tenant_id": "account_xxx",
  "user_id": "user_xxx",
  "app_id": "quotation",
  "use_case": "bid_risk_review",
  "risk_level": "high",
  "data_classification": "internal",
  "model_policy": "accurate_json_tool_calling",
  "prompt_version": "bid_review_v7",
  "knowledge_version": "tender_20260728",
  "stream": true,
  "max_output_tokens": 2000,
  "idempotency_key": "..."
}
```

模型消息可以放在同一请求中，也可以由业务服务先完成 Prompt 组装。关键是网关必须知道怎样治理这次调用。

### 建议的响应信封

```json
{
  "provider": "provider_a",
  "model": "model_x",
  "route_policy": "accurate_json_tool_calling",
  "fallback_used": false,
  "usage": {
    "input_tokens": 3200,
    "output_tokens": 640,
    "cached_tokens": 0
  },
  "timing": {
    "queue_ms": 12,
    "ttft_ms": 780,
    "generation_ms": 2300,
    "total_ms": 3150
  },
  "cache": {
    "status": "miss"
  }
}
```

### 模型路由不是随机切换

路由可以考虑：

- 是否支持视觉、Tool Calling 或结构化输出；
- 任务风险与质量门槛；
- 上下文长度；
- 延迟 SLO；
- 预算；
- 数据合规区域；
- 当前限额、错误率和熔断状态。

```text
任务要求 + 风险 + 数据边界 + 实时健康度
→ 候选模型集合
→ 质量、延迟和成本策略
→ 实际路由
```

### Fallback 必须保持语义安全

备用模型不能只看“接口是否能通”。还要验证：

- 输出 Schema 是否一致；
- 工具调用能力是否兼容；
- 上下文长度是否足够；
- 数据是否允许发送给该供应商；
- 质量是否满足当前风险等级；
- Prompt 是否适配；
- 切换后是否需要人工复核。

高风险任务宁可安全失败，也不能无条件降级到不满足门槛的模型。

---

## 3. 大模型限流不能只看 QPS

下面两个请求都只算一次请求，但资源消耗完全不同：

```text
请求 A：输入 100 Token，输出 50 Token
请求 B：输入 100,000 Token，输出 8,000 Token
```

因此只按请求数限流，无法保护 GPU、供应商额度和企业预算。

### 四层资源指标

| 指标 | 解决的问题 |
|---|---|
| Requests Per Minute | 防止接口被高频请求打爆 |
| Tokens Per Minute | 控制真实模型吞吐和供应商配额 |
| 并发槽位 | 控制同时占用的连接、显存或上游并发 |
| 金额预算 | 控制用户、应用和租户的周期性成本 |

还可以增加：

- 最大上下文长度；
- 单次最大输出 Token；
- 每日、每月 Token 配额；
- 不同模型的加权槽位；
- 长任务专用队列；
- 高低优先级队列。

### 输入与输出 Token 分开治理

输入在请求发送前大致可计算，输出只能预估。

推荐流程：

```text
计算 input_tokens
→ 按 max_output_tokens 预留输出额度
→ 检查租户、应用、用户和模型配额
→ 获取并发槽位
→ 执行调用
→ 按实际 usage 结算
→ 释放未使用额度和并发槽位
```

不能等请求结束后才检查额度，否则系统已经承担了成本。

### 分层配额

生产系统通常同时检查：

```text
企业总配额
└── 账号或租户配额
    └── 应用配额
        └── 用户配额
            └── 模型或场景配额
```

一次请求必须通过所有层级。

### 过载后的处理

不是所有请求都直接返回 429：

| 场景 | 建议 |
|---|---|
| 交互式低延迟请求 | 快速失败或切换可接受模型 |
| 可等待的异步任务 | 排队并返回任务 ID |
| 高优先级业务 | 使用保留配额和专用槽位 |
| 超大输入 | 拒绝、切片、摘要或转离线任务 |
| 上游暂时 429 | 尊重 Retry-After，带抖动退避 |

重试本身也会消耗额度。配额系统要区分：

- 预留额度；
- 实际供应商用量；
- 失败但已产生的用量；
- 因网关拒绝而未发生的用量。

---

## 4. 延迟必须按阶段拆解

只记录总耗时，只能知道“慢”，不能知道怎样优化。

### 端到端延迟模型

```text
T_total
= T_gateway
+ T_queue
+ T_context
+ T_retrieval
+ T_provider_queue
+ T_TTFT
+ T_generation
+ T_tools
+ T_postprocess
```

实际流程可能交错执行，但每个阶段都应有 Span。

### 三个模型体验指标

#### TTFT：Time to First Token

```text
TTFT = 收到第一个模型 Token 的时间 - 发出模型请求的时间
```

它决定用户何时感觉“系统开始回答”。

#### TPS：Tokens Per Second

```text
TPS = 输出 Token 数 / 持续生成时间
```

它决定流式内容是否顺畅。

#### Total Latency

```text
总耗时 = 最终结果完成时间 - 业务请求进入时间
```

它决定任务最终需要等待多久。

### SSE 不一定是模型流式输出

系统可以用 SSE 每隔一段时间发送“仍在处理中”，但模型可能仍是非流式调用。

```text
状态流
发送 queued / processing / heartbeat / completed

Token 流
真正转发模型 delta Token
```

只有后者才能准确测量模型 TTFT 和生成速度。

### 指标与瓶颈的对应关系

| 异常指标 | 常见原因 |
|---|---|
| 排队时间高 | 并发槽位不足、队列积压、优先级不合理 |
| 上下文构建慢 | 文件解析、数据库、序列化或 Prompt 过大 |
| 检索慢 | 向量库、重排、网络或 Top-K 过大 |
| TTFT 高 | 上游排队、长输入、冷启动或模型规格过大 |
| TPS 低 | 模型负载高、模型过大、网络转发慢 |
| 工具阶段慢 | 串行调用、外部 API 慢或重试过多 |
| 总耗时高但 TTFT 正常 | 输出太长、工具链过长或人工等待 |

### 聚合方式

不要只计算平均值，应至少统计：

```text
P50 + P95 + P99
```

并按以下维度分组：

- provider 和 model；
- use_case；
- 输入 Token 桶；
- 输出 Token 桶；
- cache hit/miss；
- primary/fallback；
- 成功、超时、限流和其他错误；
- 租户和优先级。

---

## 5. 语义缓存的核心是“能否安全复用”

语义相似不等于答案可复用。

例如：

```text
“我这个项目保证金是多少？”
```

对两个不同项目非常相似，但答案不能共用。

### 三类缓存

| 类型 | 示例 | 风险 |
|---|---|---|
| 精确缓存 | 完全相同版本和输入的结果 | 最低 |
| 中间结果缓存 | 文档解析、Embedding、检索、Rerank | 较低 |
| 最终语义答案缓存 | 相似问题直接复用答案 | 最高 |

生产系统通常先做精确缓存和中间结果缓存，再评估是否需要最终答案语义缓存。

### 安全缓存键

缓存至少需要考虑：

```text
tenant/account
+ user role / permission scope
+ app / use_case
+ locale / region
+ business time
+ source document version
+ knowledge index / alias / version
+ prompt / policy / tool schema version
+ model and output schema
+ data classification
+ risk class
```

语义向量只负责查找相似候选，以上边界负责判断候选是否有资格复用。

推荐顺序：

```text
先按租户、权限、版本、时间和风险过滤
→ 再做向量相似度检索
→ 再执行可复用性校验
```

不能先全局相似检索，再把结果交给业务“自己判断”。

### 适合缓存最终答案的场景

- 公开、低风险、只读知识；
- 答案在 TTL 内基本不变；
- 不包含个性化权限数据；
- 来源和知识版本稳定；
- 错误命中的后果较低；
- 已有语义缓存误命中评测。

### 不适合直接复用最终答案

- 报价金额和成本依据；
- 投标决策、资格和高风险条款；
- 当前库存、价格、状态或时间敏感信息；
- 用户或项目私有内容；
- 会触发写操作的 Agent 决策；
- 必须基于最新证据的法律、财务和审批结论。

这些场景可以缓存解析、检索或模型中间结果，但最终结论仍要重新校验。

### 缓存失效

常见策略：

- TTL；
- 文档或知识版本变化主动失效；
- Prompt、政策或模型版本变化使用新命名空间；
- 权限变化后失效；
- 高风险数据只允许短 TTL；
- 发现错误命中后加入禁用规则并回流评测集。

### 缓存指标

- exact hit rate；
- semantic hit rate；
- false-hit rate；
- stale-hit rate；
- permission reject count；
- cache bypass count；
- 节省的 Token、费用和延迟；
- 命中与未命中的质量差异。

命中率高不代表缓存好。错误复用率通常比命中率更重要。

---

## 6. 四个组件怎样形成闭环

```text
1. Gateway 识别调用者、场景、风险和版本
2. Rate Limiter 预留 Token、金额和并发资源
3. Cache 判断是否存在安全可复用结果
4. Router 选择满足质量与合规要求的模型
5. Executor 调用、流式转发、重试或降级
6. Metering 按实际用量结算
7. Trace 记录各阶段延迟、路由、缓存和错误
8. Metrics 聚合 SLO、成本与容量
```

四者不能割裂：

- 没有统一网关，限流和审计无法全面覆盖；
- 没有资源限流，缓存未命中可能形成调用洪峰；
- 没有阶段延迟，无法判断缓存或路由是否真正优化体验；
- 没有版本与权限，语义缓存可能造成越权和过期。

---

## 7. 当前 AI 智能报价中台的项目映射

### 7.1 已有模型网关骨架

`app/services/model_gateway.py` 已经承担多种模型与 n8n 调用的公共能力：

- 按 provider、model、endpoint type 记录调用；
- 保存状态、HTTP 状态、总延迟、输入/输出字符数；
- 按字符数估算费用；
- 维护超时和失败阈值；
- 按 provider + endpoint type 进行熔断；
- 支持 OpenAI、DashScope、智谱和 n8n 等调用适配。

`app/api/v1/model_gateway.py` 提供管理员查询：

- 调用次数；
- 平均延迟；
- 估算成本；
- 输入/输出字符；
- 熔断状态；
- 人工重置熔断。

资料研判 Agent 还具备 primary/fallback 模型适配，429、超时和部分上游错误可触发备用模型，并把实际路由写入 Trace。

这说明当前项目不是“没有网关”，而是已经具备服务级 LLM Gateway 骨架。

### 7.2 网关尚未完全统一

当前边界包括：

1. 它主要是内部调用封装，不是完整的 OpenAI-compatible 统一入口；
2. provider 和 model 多由调用方配置，尚未形成集中式策略路由；
3. 资料研判 Agent 的模型适配器仍直接调用 OpenAI-compatible 端点；
4. 部分调用虽会单独记日志，但没有全部经过同一执行入口；
5. 异步报价调用网关时，Trace ID 传递仍不完全一致；
6. 熔断状态保存在进程内存，不同 Web/Worker 进程之间不会自动共享；
7. 费用按字符数估算，不是供应商返回的精确 Token 和真实价格。

因此更准确的定位是：

```text
已有统一日志、熔断和调用封装
≠
已完成全平台模型治理
```

### 7.3 当前限流能力

已经具备：

- 登录接口按 IP 和请求次数限流；
- Celery Worker 并发配置；
- PDF 视觉任务并发上限；
- 报价任务总超时和 n8n 超时；
- 队列、Heartbeat 和卡死任务治理；
- 部分 429 可触发 Agent 模型 fallback。

尚未具备：

- 每个账号、应用和模型的 Token Bucket；
- 输入与输出 Token 分开预留和结算；
- 成本预算准入；
- 按模型权重计算并发槽位；
- Provider 限额的分布式协调；
- 网关统一返回 Retry-After；
- 对所有 LLM 端点的统一限流。

需要特别注意：

> 项目里的“账户定额”是工程造价定额库，不是 LLM Token 配额。

### 7.4 当前延迟观测

已经具备：

- `model_call_logs.latency_ms` 记录单次 HTTP 调用总延迟；
- 模型网关管理接口聚合平均延迟；
- 报价任务保存 `duration_ms`；
- 报价速度看板展示 AI、人工确认和总交付平均耗时；
- Agent Trace 保存节点 `duration_ms`；
- SSE 持续发送处理进度和 Heartbeat。

尚未具备：

- 模型 Token 级流式代理；
- TTFT；
- TPS；
- provider queue 与 generation 分离；
- 网关、排队、上下文、检索、模型和工具的统一阶段耗时；
- 模型网关和报价速度的 P95/P99；
- 按输入 Token、fallback 和 cache hit 分组的延迟分析。

当前 SSE 主要传递业务状态，不等于模型 Token 流。

### 7.5 当前缓存能力

项目中已经存在两类安全程度较高的确定性复用：

1. 招标分析的 LLM 结果保存在同一个 Parse Run 中，复用时检查 Prompt 版本和模型；
2. 证据正文读取使用以 bucket、object name 和 SHA-256 为键的进程内 LRU，并在读取后校验内容哈希。

这些设计值得保留，但它们不属于“相似问题跨请求复用答案”的语义缓存。

当前没有发现：

- 全局或租户级语义答案缓存；
- 基于 Embedding 的相似问答复用；
- 语义缓存阈值评测；
- 跨请求的权限、时间和知识版本缓存策略。

对报价中台而言，这是合理的保守状态。报价、成本依据和投标决策都不适合直接启用跨项目最终答案缓存。

---

## 8. 推荐的渐进改造顺序

### P0：先统一调用事实

让所有模型调用至少进入统一观测契约：

```text
request_id / trace_id
tenant / user / app / use_case
provider / model / route
prompt / policy / knowledge version
input_tokens / output_tokens
queue / total latency
status / retry / fallback
actual cost
```

优先补齐：

- 资料研判 Agent；
- 报价 n8n/Dify 链；
- 图纸视觉模型；
- 招标分析；
- AI 解释与会议纪要。

### P0：增加分布式资源准入

使用 Redis 等共享存储实现：

- RPM；
- 输入和输出 TPM；
- 并发槽位；
- 每账号、应用和模型的金额预算；
- 预留、实际结算和释放；
- 429 与 Retry-After；
- 指标和告警。

先保护高成本视觉模型、长上下文招标分析和批量报价。

### P1：把熔断与路由移到共享策略层

- 熔断状态跨进程共享；
- 按模型能力和风险定义路由；
- 建立 primary/fallback 兼容矩阵；
- 记录路由原因；
- 高风险降级必须通过发布门；
- 防止每个 Worker 独立判断造成流量震荡。

### P1：补阶段延迟

```text
ingress
→ queue_wait
→ context_build
→ retrieval
→ provider_request
→ first_token
→ generation_end
→ postprocess
→ completed
```

在当前模型仍为非流式时，先补 queue/context/retrieval/provider/tool/total；引入 Token 流后再增加 TTFT 和 TPS。

### P1：先做精确和中间结果缓存

适合优先复用：

- 相同文件哈希的解析结果；
- 相同知识版本的 Embedding；
- 同账号、同权限、同查询和同索引版本的检索结果；
- 同一 Parse Run、Prompt 和模型版本的招标分析；
- 低风险只读解释的精确结果。

不应跨项目复用：

- 最终报价；
- 成本库匹配结论；
- 投标决策；
- 资格、保证金和截止时间；
- 人工审批结果；
- 任何写操作计划。

### P2：评测后再启用语义答案缓存

先建立：

- 可缓存场景白名单；
- 误命中和过期金标；
- 权限隔离测试；
- 版本失效测试；
- 质量、成本和延迟对照；
- 一键全局旁路与回滚。

只有误命中风险可接受时，才对低风险只读场景小范围灰度。

---

## 9. 最小监控面板

### Gateway

- 请求量、成功率、错误率；
- provider/model/use_case 分布；
- primary/fallback 比例；
- 熔断次数和持续时间；
- 429、超时和重试；
- 输入、输出和缓存 Token；
- 实际费用。

### Rate Limit

- 配额拒绝次数；
- Token 预留和实际用量；
- 并发槽位占用；
- 排队深度和等待时间；
- 各租户和应用的预算消耗；
- 供应商限额利用率。

### Latency

- queue、context、retrieval、TTFT、generation、tool、total；
- P50、P95、P99；
- 输入/输出 Token 分桶；
- primary 与 fallback 对比；
- cache hit 与 miss 对比。

### Cache

- exact/semantic hit rate；
- false-hit 和 stale-hit；
- 权限或版本不兼容拒绝；
- 节省的 Token、费用和延迟；
- 命中结果质量；
- 被旁路和主动失效次数。

---

## 10. 面试表达

### 为什么企业要先建 LLM Gateway？

> 多个业务系统直连模型会让密钥、权限、成本、路由、故障处理和审计分散。LLM Gateway 把模型调用变成受控基础设施，统一调用契约、策略路由、资源准入、降级熔断、用量和全链路观测。

### 为什么限流不能只看请求数？

> 大模型的一次请求可能消耗几百或十几万 Token，占用时间也不同。生产限流要同时控制请求频率、输入输出 Token、并发槽位和金额预算，请求前预留、结束后按实际用量结算。

### 为什么响应速度不能只看总耗时？

> 总耗时不能区分排队、检索、首 Token、生成和工具链瓶颈。我会拆分 queue、context、retrieval、TTFT、TPS、tool 和 total，并查看 P50/P95/P99。SSE 状态 Heartbeat 也不能当成模型 Token 流。

### 语义缓存为什么危险？

> 语义相似只说明问题像，不说明答案在同一租户、权限、时间、知识和业务版本下可复用。生产缓存要先过滤权限与版本，再做相似检索；高风险业务优先缓存解析和检索中间结果，不直接缓存最终决策。

### 怎样评价当前报价中台？

> 项目已有服务级模型网关骨架，能统一记录部分模型调用、总延迟、字符量、估算成本和熔断状态，也具备 Agent 模型 fallback。但调用覆盖、精确 Token、分布式资源限流、阶段延迟和语义缓存治理仍不完整。下一步应先统一事实和资源准入，再谨慎扩展缓存。

---

## 11. 检查清单

### Gateway

- [ ] 所有模型调用经过统一治理入口或统一观测契约
- [ ] 模型路由有明确原因和版本
- [ ] Fallback 通过能力、质量和合规验证
- [ ] 熔断状态跨进程一致
- [ ] Trace ID 能贯穿业务、模型、工具和异步任务

### Limit

- [ ] 同时限制请求、Token、并发和成本
- [ ] 输入与输出 Token 分开治理
- [ ] 请求前预留，结束后结算
- [ ] 支持租户、应用、用户和模型分层配额
- [ ] 429 返回明确原因和 Retry-After

### Latency

- [ ] 区分状态流与 Token 流
- [ ] 记录 queue、context、retrieval、model、tool 和 total
- [ ] 流式模型记录 TTFT 与 TPS
- [ ] 查看 P50、P95、P99
- [ ] 能按模型、Token、路由和缓存状态分组

### Cache

- [ ] 缓存键包含租户、权限、时间和全部运行版本
- [ ] 先过滤边界，再做语义相似检索
- [ ] 高风险最终答案默认不缓存
- [ ] 有 TTL、主动失效、旁路和回滚
- [ ] 持续评测误命中与过期命中

---

## 关联笔记

- [模型输入输出控制：Token、上下文、Prompt、结构化输出与流式生成](../04-LLM与Prompt/模型输入输出控制-Token上下文Prompt结构化输出与流式生成.md)
- [模型路由与协同：大小模型、多模态与质量成本权衡](../04-LLM与Prompt/模型路由与协同-大小模型多模态与质量成本权衡.md)
- [模型推理服务：量化、vLLM、吞吐、延迟与部署治理](../04-LLM与Prompt/模型推理服务-量化vLLM吞吐延迟与部署治理.md)
- [Transformer 推理基础：注意力、QKV 与 KV Cache](../04-LLM与Prompt/Transformer推理基础-注意力QKV与KVCache.md)
- [模型能力优化：Scaling、解码策略与微调](../04-LLM与Prompt/模型能力优化-Scaling解码策略与微调.md)
- [LLMOps：大模型与 Agent 的持续评测、发布和运营](./LLMOps全生命周期管理.md)
- [Agent 评测体系：事实、过程、工具、效率、安全与版本治理](./Agent评测体系-事实过程工具效率安全与版本治理.md)
- [Agent 线上故障定位与可靠性治理](./Agent线上故障定位与可靠性治理.md)
- [Agent 技术栈全景图与数据流](../02-Agent核心架构/Agent技术栈全景图与数据流.md)
- [Agent 工具安全：权限作用域、注入防护与执行校验](../05-工具调用与MCP/Agent工具安全-权限作用域注入防护与执行校验.md)
