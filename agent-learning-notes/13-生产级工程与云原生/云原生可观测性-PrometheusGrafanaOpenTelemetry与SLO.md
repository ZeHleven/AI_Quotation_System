---
title: 云原生可观测性：Prometheus、Grafana、OpenTelemetry 与 SLO
category: 生产级工程与云原生
tags:
  - Prometheus
  - Grafana
  - OpenTelemetry
  - Logs
  - Metrics
  - Traces
  - SLO
sources:
  - https://prometheus.io/docs/concepts/metric_types/
  - https://grafana.com/docs/grafana/latest/visualizations/dashboards/
  - https://opentelemetry.io/docs/concepts/signals/
  - https://opentelemetry.io/docs/collector/
reviewed_at: 2026-07-30
status: 已整理
---

# 云原生可观测性：Prometheus、Grafana、OpenTelemetry 与 SLO

## 核心结论

监控回答“已知问题是否发生”，可观测性帮助从系统输出推断内部状态。

```text
Metrics：哪里异常、影响多大
Logs：具体发生了什么
Traces：一次请求经过哪些环节、慢在哪里
```

Prometheus 负责采集和查询时序指标，Grafana 负责可视化与告警呈现，OpenTelemetry 负责统一生成、传递和导出遥测数据。三者不是相互替代关系。

---

## 1. 三大信号

### 1.1 Logs

结构化日志建议包含：

```json
{
  "timestamp": "...",
  "level": "ERROR",
  "service": "quote-api",
  "environment": "staging",
  "trace_id": "...",
  "span_id": "...",
  "job_id": "...",
  "event": "rag_search_failed",
  "error_type": "timeout",
  "duration_ms": 20124
}
```

要求：

- 字段稳定，可过滤和聚合；
- `trace_id`、任务 ID 等关联字段统一；
- 记录错误类型而不只记录错误文本；
- 敏感数据脱敏；
- 设置采集、轮转、保留和访问权限。

日志不适合直接承担高频全量统计，统计类问题优先用指标。

### 1.2 Metrics

指标适合：

- 速率；
- 比例；
- 分位延迟；
- 当前资源量；
- 队列和连接池状态；
- SLO 计算与告警。

### 1.3 Traces

Trace 表示一次请求或任务的端到端路径，Span 表示其中一个步骤。

报价链路可拆为：

```text
HTTP 接入
→ 鉴权
→ 文件解析
→ 成本库匹配
→ N8N / Dify
→ 模型调用
→ RAG 检索
→ 预审结果持久化
```

每个 Span 记录开始时间、耗时、状态、属性和父子关系，才能看出延迟究竟来自哪一段。

---

## 2. Prometheus

### 2.1 基本模型

Prometheus 通常定时抓取应用或 Exporter 暴露的指标端点，保存为带时间戳的时序数据，并用 PromQL 查询。

```text
应用 /metrics      Node Exporter
       \             /
        Prometheus
            ↓
       PromQL / Rules
            ↓
   Grafana / Alertmanager
```

并非所有场景都必须 pull。短生命周期批任务可通过适当机制暴露结果，但不应把 Pushgateway 当通用事件总线。

### 2.2 四种常见指标类型

| 类型 | 语义 | 示例 |
|---|---|---|
| Counter | 只增不减的累计值，进程重启可归零 | 请求总数、错误总数 |
| Gauge | 可增可减的瞬时值 | 队列长度、活跃任务、内存 |
| Histogram | 把观测值计入桶，可聚合分位数 | 请求延迟、响应大小 |
| Summary | 客户端计算分位数 | 特定单实例场景 |

延迟通常优先 Histogram，因为服务端可跨实例聚合；Summary 的客户端分位数通常不适合直接跨实例求和。

### 2.3 标签与基数

指标由名称和标签组合成时间序列。标签必须是有限枚举：

```text
适合：method、route、status_class、model_provider
危险：user_id、trace_id、完整 URL、错误堆栈、原始问题
```

高基数会造成内存、磁盘和查询成本急剧增长。`trace_id` 应进入日志或 Trace，不应作为 Prometheus 标签。

### 2.4 RED 与 USE

服务使用 RED：

- Rate：请求或任务速率；
- Errors：错误率；
- Duration：耗时分布。

资源使用 USE：

- Utilization：利用率；
- Saturation：饱和程度；
- Errors：资源错误。

AI 应用还需业务和质量指标：

- 报价任务成功率；
- 排队时间与端到端耗时；
- 预审完整率；
- RAG 无答案率、引用有效率；
- 模型 Token、费用、限流和重试；
- 人工打回率。

### 2.5 告警原则

好的告警应：

- 指向用户影响或即将耗尽的错误预算；
- 有明确负责人和 Runbook；
- 区分严重程度；
- 避免对每个瞬时尖峰报警；
- 告警恢复也可追踪。

页面告警优先关注“需要立即行动”，趋势和容量问题可进入工单或日报。

---

## 3. Grafana

Grafana Dashboard 是一个或多个 Panel 的集合，通过数据源查询展示系统状态。

### 3.1 看板分层

推荐从上到下：

1. 用户体验：成功率、P95/P99、可用性；
2. 业务流程：排队、处理、预审、下发；
3. 依赖：MySQL、Redis、队列、RAG、模型供应商；
4. 资源：CPU、内存、磁盘、网络；
5. 版本与事件：发布、配置变更、故障时间点。

### 3.2 报价中台看板

首页不应堆满曲线，核心可放：

- 当前 SLO 状态与错误预算；
- 报价成功率、P95 总耗时；
- 队列积压和最老等待时间；
- 模型、RAG、数据库错误率；
- 当前版本、最近发布时间；
- 正在发生的告警。

下钻页再展示：

- 分阶段延迟；
- 按模型、路由、状态码拆分；
- 资源与连接池；
- Trace 或日志跳转链接。

### 3.3 常见误区

- 只看平均值，掩盖长尾；
- 不标注发布事件；
- 颜色过多且无统一阈值；
- 图表没有单位、范围和责任人；
- Dashboard 很漂亮，但没有任何可执行告警；
- Grafana 展示的数据被误认为由 Grafana 自己采集。

---

## 4. OpenTelemetry

### 4.1 核心组件

```text
应用代码 / 自动插桩
        ↓
OpenTelemetry API + SDK
        ↓ OTLP
OpenTelemetry Collector
  receiver → processor → exporter
        ↓
Trace / Metrics / Logs 后端
```

概念：

- API：业务库调用的接口；
- SDK：采样、处理和导出实现；
- Instrumentation：为框架、HTTP、数据库等生成遥测；
- Resource：服务名、版本、环境等资源属性；
- Context Propagation：跨进程传递 Trace 上下文；
- Semantic Conventions：统一属性命名；
- Collector：接收、处理、批量和导出遥测。

### 4.2 为什么需要 Collector

Collector 可统一：

- 批量和重试；
- 属性补充和脱敏；
- 采样；
- 多后端导出；
- 减少应用与具体观测平台的耦合。

生产中通常让应用通过 OTLP 发送给 Collector，而不是每个应用直接维护多种后端 SDK。

### 4.3 上下文传播

HTTP 可传播 W3C Trace Context；异步消息需把上下文写入消息头或任务元数据。

报价中台需要跨越：

```text
浏览器
→ FastAPI
→ Celery
→ N8N / Dify
→ RAG API
→ Milvus / 模型供应商
```

若某一跳未传递上下文，Trace 会断裂。无法控制的外部平台，可把供应商请求 ID、任务 ID和本地 Span 属性关联起来。

### 4.4 采样

- Head Sampling：请求开始时决定是否采样，简单但可能漏掉后续错误；
- Tail Sampling：收集后按错误、延迟等条件决定，能力强但资源成本更高。

常见策略：

- 错误与高延迟 Trace 尽量保留；
- 普通成功请求按比例采样；
- 高价值业务或灰度版本提高采样；
- 敏感属性先脱敏再导出。

---

## 5. SLI、SLO、SLA 与错误预算

| 概念 | 含义 |
|---|---|
| SLI | 实际测量指标，如成功请求比例 |
| SLO | 内部可靠性目标，如 30 天成功率 ≥ 99.9% |
| SLA | 对外承诺及未达标责任 |
| Error Budget | `1 - SLO` 允许的失败空间 |

例：30 天 SLO 为 99.9%，理论错误预算约为 43.2 分钟不可用时间，但实际还要按业务口径定义合格事件和统计窗口。

### 5.1 如何选择 SLI

指标要贴近用户体验：

```text
报价可用性
= 成功完成的有效报价任务
÷ 符合统计条件的报价任务
```

还可定义：

- 延迟 SLI：在目标时间内完成的任务比例；
- 正确性 SLI：确认行完整、结构可解析、证据满足要求；
- 新鲜度 SLI：成本库和知识库在规定时间内完成同步。

不要只用 HTTP 200。异步任务返回“已受理”后最终失败，用户仍然没有成功。

### 5.2 错误预算与发布

错误预算把可靠性和迭代速度连接起来：

- 预算充足：允许正常发布和实验；
- 消耗过快：收紧灰度、暂停高风险变更；
- 预算耗尽：优先稳定性、修复和容量治理。

Burn Rate 告警关注预算消耗速度，比“过去一分钟错误率高”更贴近 SLO。

---

## 6. 从告警到故障处理

```text
告警触发
→ 确认用户影响与范围
→ 查最近发布和配置变更
→ 指标定位异常服务/阶段
→ Trace 定位慢点或错误跳
→ 日志确认错误上下文
→ 限流、降级、切流或回滚
→ 验证 SLI 恢复
→ 根因分析与复盘
```

告警、Dashboard 和 Trace 都必须指向操作手册。没有恢复动作和负责人，信号再多也不等于可运维。

---

## 7. 报价中台的真实映射

### 已具备

- FastAPI 中间件接收或生成 `X-Trace-Id`；
- `trace_id` 写入结构化日志和多个业务记录；
- `/health/ready` 检查数据库与异步任务相关依赖；
- 已有报价耗时、响应速度、执行速度等业务看板数据；
- Docker Compose 配置日志轮转；
- RAG 异常可进行 BM25 降级；
- 已形成 Agent 评测、故障定位和证据链笔记。

### 尚未落地

- 没有 Prometheus 指标采集与规则；
- 没有 Grafana 统一运维看板；
- 没有 OpenTelemetry SDK、Collector 和完整分布式 Trace；
- 目前的 `trace_id` 主要是相关 ID，不等于标准 Span 树；
- 没有正式的 SLO 统计、错误预算和 Burn Rate 告警；
- 未形成基于观测指标的自动灰度门。

### 最小落地顺序

```text
1. 定义报价任务与 API 的 SLI/SLO
2. FastAPI 暴露低基数 Prometheus 指标
3. 采集主机、容器、MySQL、Redis、Celery 指标
4. Grafana 建立总览和下钻看板
5. Alertmanager 接入负责人和 Runbook
6. FastAPI/Celery/RAG 接入 OTel
7. 部署 Collector，打通跨服务上下文
8. 用 SLO 和质量指标驱动灰度
```

---

## 8. 面试回答模板

### 日志、指标、链路追踪有什么区别？

> 指标适合发现异常趋势和告警，日志记录离散事件细节，Trace 还原一次请求跨服务的路径。实际排障通常先从 SLO 或 RED 指标判断范围，再用 Trace 定位阶段，最后用同一 trace_id 的结构化日志确认原因。

### Prometheus 标签为什么不能放 user_id？

> 每组标签值都会形成新的时间序列。user_id、trace_id 这类高基数字段会让存储和查询成本爆炸。它们应放在日志或 Trace 中，指标只保留 route、status class、provider 等有限维度。

### OpenTelemetry 与 Prometheus 是什么关系？

> OpenTelemetry 是生成、传播、处理和导出遥测的标准与工具体系；Prometheus 是指标监控和查询系统。可以用 OTel 采集或转换指标，再导出给 Prometheus 生态，也可以让应用直接暴露 Prometheus 指标，两者不是互斥关系。

### 如何给异步报价任务定义 SLO？

> 不能只看创建任务接口返回 200。我会以最终成功完成且结果满足基本质量门的任务为合格事件，同时定义端到端时延目标，排除明确由用户取消等不应计入的事件，并按 30 天滚动窗口计算成功率和错误预算。

---

## 9. 复习清单

- [ ] 能区分日志、指标和 Trace
- [ ] 能解释 Counter、Gauge、Histogram 和 Summary
- [ ] 能说明高基数标签风险
- [ ] 能用 RED、USE 设计指标
- [ ] 能解释 Grafana 只是展示和分析层
- [ ] 能说明 OTel API、SDK、插桩、传播和 Collector
- [ ] 能区分 SLI、SLO、SLA 和错误预算
- [ ] 能说明当前项目的 `trace_id` 不等于完整分布式追踪
- [ ] 能给出报价中台观测体系的最小落地顺序

## 延伸阅读

- [Agent 线上故障定位与可靠性治理](../10-LLMOps与可观测性/Agent线上故障定位与可靠性治理.md)
- [Agent 评测体系](../10-LLMOps与可观测性/Agent评测体系-事实过程工具效率安全与版本治理.md)
- [高并发与性能优化](../09-后端基础设施/高并发与性能优化-指标压测容量与瓶颈定位.md)
- [Git、测试工程与 CI/CD](./Git测试工程与CI-CD-分支质量门制品发布与回滚.md)
