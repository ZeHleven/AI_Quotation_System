---
title: 模型推理服务：量化、vLLM、吞吐、延迟与部署治理
category: LLM 与 Prompt
tags:
  - Model Serving
  - Quantization
  - vLLM
  - PagedAttention
  - Continuous Batching
  - GPU
  - Inference
sources:
  - https://huggingface.co/docs/transformers/main/en/quantization/overview
  - https://docs.vllm.ai/en/latest/features/quantization/
  - https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/
  - https://arxiv.org/abs/2309.06180
reviewed_at: 2026-07-30
status: 已整理
---

# 模型推理服务：量化、vLLM、吞吐、延迟与部署治理

## 核心结论

自部署模型不是“把模型放到 GPU 上并开放一个端口”，而是一套在线服务：

```text
API 与鉴权
→ 请求队列和调度
→ Tokenization / Chat Template
→ Prefill
→ Decode 与 KV Cache
→ Streaming / Structured Output
→ 用量、指标、日志和 Trace
```

量化解决模型表示和资源占用问题，vLLM 解决高效推理与服务问题。二者相关，但不是同一个概念。

---

## 1. 外部 API 与自部署如何选

| 维度 | 外部模型 API | 自部署推理 |
|---|---|---|
| 启动成本 | 低 | GPU、平台和运维成本高 |
| 弹性 | 供应商负责 | 自己规划容量 |
| 模型选择 | 受供应商限制 | 可使用合适的开放权重模型 |
| 数据边界 | 需评估外发合规 | 可放在受控环境 |
| 单位成本 | 小流量通常合适 | 稳定大流量可能更可控 |
| 版本控制 | 供应商可能升级 | 自己固定和发布 |
| 可靠性 | 依赖供应商 | 依赖自身集群和团队 |

决策不能只比较 Token 单价，还要计算：

```text
GPU 利用率
+ 空闲资源
+ 工程人力
+ 监控与故障
+ 模型升级
+ 安全与合规
```

没有稳定流量、GPU 运维能力或数据合规要求时，自部署不一定更便宜。

---

## 2. 推理服务的主要内存

GPU 内存大致被以下部分占用：

```text
模型权重
+ KV Cache
+ 中间激活与工作区
+ CUDA Graph / Kernel 等运行开销
```

### 模型权重粗略估算

```text
权重内存 ≈ 参数量 × 每参数字节数
```

例如仅作粗略理解：

- FP32：约 4 字节；
- FP16/BF16：约 2 字节；
- INT8：约 1 字节；
- INT4：约 0.5 字节。

实际还要考虑量化分组、scale、元数据、未量化层和运行开销，不能直接把理论值当部署结果。

### KV Cache

KV Cache 随以下因素增长：

- 层数；
- KV Head 数；
- Head Dimension；
- 上下文长度；
- 并发序列数；
- Cache 数据类型。

因此模型权重能装进 GPU，不代表长上下文高并发也能运行。

---

## 3. 量化基础

量化是用更低精度表示权重、激活或 KV Cache，以减少内存、带宽和计算成本。

### 常见对象

| 类型 | 量化对象 |
|---|---|
| Weight-only | 只量化权重 |
| Weight + Activation | 权重和激活都量化 |
| KV Cache Quantization | 量化推理时的 K/V 缓存 |

### PTQ 与 QAT

- PTQ：训练后量化，成本较低，适合快速部署评估；
- QAT：量化感知训练，让模型在训练中适应低精度，成本更高。

### 常见精度

- FP16 / BF16：常见推理基线；
- FP8：依赖硬件和 Kernel 支持；
- INT8：质量与压缩较平衡；
- INT4：压缩更强，但质量和算子兼容风险更高。

算法还可能采用不同分组、校准和权重格式。不能只说“都是 4 bit”就认为效果相同。

### 量化不保证更快

收益取决于：

- GPU 是否支持对应低精度；
- Kernel 是否优化；
- 任务是计算受限还是内存带宽受限；
- 量化/反量化开销；
- Batch 和序列长度；
- 并行策略；
- 模型架构。

可能出现：

```text
显存明显下降
但延迟没有下降
甚至因 Kernel 不匹配而变慢
```

### 量化评测

量化前后必须在真实任务上比较：

- 结构化输出成功率；
- 抽取和计算准确率；
- 长文本和多语言质量；
- Tool Calling；
- 幻觉与安全；
- TTFT、TPOT、吞吐；
- 最大稳定并发；
- GPU 内存；
- 每成功任务成本。

不能只用困惑度或通用榜单决定生产发布。

---

## 4. vLLM 解决什么问题

vLLM 是面向 LLM 推理和服务的引擎。它可提供：

- OpenAI 兼容 HTTP 服务；
- 请求调度与持续批处理；
- PagedAttention 式 KV Cache 管理；
- Streaming；
- 多 GPU 并行；
- Prefix Caching；
- 多种量化格式；
- 结构化输出等服务能力。

具体功能和兼容范围会随版本、模型和硬件变化，部署前必须核对当前版本文档。

### PagedAttention

传统连续 KV Cache 容易因为预留和碎片造成浪费。PagedAttention 把每个请求的 KV Cache 划分为块，通过类似分页的方式管理逻辑与物理存储。

目的：

- 减少内存碎片；
- 提高 KV Cache 利用率；
- 支持更多并发序列；
- 为共享前缀等优化提供基础。

它不是减少模型权重，也不是 Agent 的长期记忆。

### Continuous Batching

静态批处理往往要等整批请求全部完成。持续批处理允许已完成请求退出、新请求进入调度，提高 GPU 利用率和吞吐。

代价是：

- 调度更复杂；
- 高并发可能增加排队和单请求延迟；
- 需要设置公平性、优先级和资源上限；
- 吞吐优化不能破坏交互请求 SLO。

### OpenAI 兼容接口

兼容接口可以降低客户端迁移成本，但“API 兼容”不代表：

- 所有参数行为完全相同；
- Tool Calling 质量相同；
- Chat Template 自动正确；
- JSON Schema 全部特性一致；
- Token 计费和 usage 语义相同；
- 模型能力可以互换。

服务启动时要固定模型版本、Chat Template、Generation Config 和推理参数，不能依赖不可见默认值。

---

## 5. 推理性能指标

| 指标 | 含义 |
|---|---|
| TTFT | 请求到首 Token 的时间 |
| TPOT / ITL | 后续 Token 的平均生成间隔 |
| E2E Latency | 完整请求总耗时 |
| Input Throughput | 每秒处理输入 Token |
| Output Throughput | 每秒生成输出 Token |
| Request Throughput | 每秒完成请求数 |
| Goodput | 满足延迟和质量 SLO 的有效吞吐 |

只报“每秒多少 Token”没有意义，还要说明：

- 输入和输出长度；
- 并发；
- 模型和量化；
- GPU 型号与数量；
- TTFT/TPOT 目标；
- 是否 Streaming；
- 成功率和质量。

### 交互与批处理的取舍

- 交互式请求重视 TTFT 和 P95；
- 离线任务重视总吞吐和成本；
- 超长上下文会占用大量 KV Cache；
- 大 Batch 提高吞吐，却可能增加排队时间。

生产环境常为交互、异步、超长文本建立不同队列和资源池。

---

## 6. 并行与扩展

### Tensor Parallel

把一个模型层的计算拆到多张 GPU，适合单卡放不下或需要提高单模型计算能力。

风险：

- GPU 间通信开销；
- 拓扑不合适会拖慢；
- 故障域扩大。

### Pipeline Parallel

把不同层放在不同设备，形成流水线。需要处理流水线气泡和负载平衡。

### Data Parallel

复制多份模型实例处理不同请求，提高总体吞吐和可用性，但每份实例都需要完整或相应分片资源。

选择取决于：

```text
模型是否能单卡容纳
GPU 互联
请求长度与并发
延迟目标
容错与成本
```

---

## 7. 生产部署检查

### 模型与制品

- 固定模型 revision 和哈希；
- 固定 Tokenizer、Chat Template；
- 记录量化格式和校准信息；
- 明确许可证和使用边界；
- 离线下载并扫描模型文件；
- 保留上一版本和回滚步骤。

### 服务

- API Key / JWT 与租户鉴权；
- 请求体、上下文、输出长度限制；
- 并发、Token 和费用配额；
- 超时、取消和背压；
- Liveness、Readiness、Startup；
- 优雅停机和请求排空；
- 低权限容器和网络隔离。

### 观测

- 队列时间；
- TTFT、TPOT、E2E；
- 输入/输出 Token；
- 活跃和等待序列；
- KV Cache 利用率；
- GPU 利用率、显存、温度和错误；
- OOM、重启、请求失败；
- 按模型和版本的质量指标。

### 容量与压测

使用真实长度分布和到达模式，不只做单请求测速。

```text
逐步增加并发
→ 找到 SLO 开始恶化点
→ 记录最大稳定 Goodput
→ 保留故障和流量突增余量
```

---

## 8. 常见故障

| 现象 | 常见原因 | 定位方向 |
|---|---|---|
| OOM | 权重、KV Cache、长上下文、并发过高 | 显存分解、请求长度、批调度 |
| TTFT 高 | 排队、长 Prefill、冷启动 | queue、prefill、模型加载 |
| TPOT 高 | 算力不足、Batch、Kernel | decode 指标、GPU 利用率 |
| 吞吐高但体验差 | 批过大、排队长 | P95 TTFT 与 Goodput |
| 输出乱码/质量骤降 | Tokenizer 或 Chat Template 错误 | 模型制品与模板版本 |
| 量化后工具调用失败 | 精度损失或格式不兼容 | 量化前后专项评测 |
| Streaming 中断 | 代理超时、客户端断开、服务重启 | 链路日志、取消和重连 |

---

## 9. 报价中台的真实映射

### 当前事实

- 报价中台主要调用外部模型 API；
- `model_gateway.py` 负责多个供应商的调用、超时、重试、日志和熔断；
- RAG 服务使用本地 Embedding 模型，但它不是通过 vLLM 提供生成式推理；
- 当前项目没有部署 vLLM；
- 没有自建生成模型 GPU 集群；
- 因此不能把 PagedAttention、持续批处理或模型量化说成已落地经验。

### 何时评估自部署

- 内部数据不能外发；
- 调用量长期稳定且足以提高 GPU 利用率；
- 开放模型在业务评测上达标；
- 外部 API 成本或限流成为明确瓶颈；
- 团队具备 GPU、容器、Kubernetes 和观测能力；
- 能承担模型安全、升级、容量和故障责任。

### 最小验证路径

```text
1. 用真实报价评测集选择开放模型
2. 单机 vLLM 建立 FP16/BF16 基线
3. 测量质量、TTFT、TPOT、Goodput 和显存
4. 对 INT8/INT4/FP8 候选做同集对比
5. 接入现有模型网关，不让业务直连
6. 增加鉴权、配额、监控和回滚
7. 小流量灰度，与外部 API 对照
```

---

## 10. 面试回答模板

### 量化是什么？

> 量化用更低精度表示权重、激活或 KV Cache，以降低显存、带宽和计算成本。它不保证加速，收益取决于硬件和 Kernel；也可能损害结构化输出、工具调用和长文本质量，所以必须在真实业务集上比较质量、延迟、吞吐和成功任务成本。

### vLLM 为什么吞吐高？

> 核心思路包括更高效的 KV Cache 块管理和持续批处理。PagedAttention 减少 KV Cache 预留和碎片，持续批处理让完成的请求及时退出、新请求进入。但高吞吐不等于低 P95，还要用真实长度与并发测 TTFT、TPOT 和 Goodput。

### OpenAI 兼容接口是否能无缝替换？

> 只能降低协议迁移成本，不能保证模型能力、参数默认值、Chat Template、Tool Calling 和结构化输出语义完全一致。仍要做契约测试、业务评测和灰度。

### 什么时候选择自部署？

> 当数据边界、稳定规模、成本或供应商限流形成明确收益，并且开放模型质量达标、团队具备 GPU 运维能力时才考虑。否则外部 API 往往更快、更省平台成本。

---

## 11. 复习清单

- [ ] 能区分模型权重、激活和 KV Cache
- [ ] 能解释 PTQ、QAT、权重量化和 KV Cache 量化
- [ ] 能说明量化为什么不一定加速
- [ ] 能解释 PagedAttention 与持续批处理
- [ ] 能区分 TTFT、TPOT、总延迟、吞吐和 Goodput
- [ ] 能说明 Tensor、Pipeline、Data Parallel 的用途
- [ ] 能列出推理服务的安全、观测和发布要求
- [ ] 能诚实说明报价中台当前没有 vLLM 和生成模型自部署

## 延伸阅读

- [Transformer 推理基础](./Transformer推理基础-注意力QKV与KVCache.md)
- [模型输入输出控制](./模型输入输出控制-Token上下文Prompt结构化输出与流式生成.md)
- [模型路由与协同](./模型路由与协同-大小模型多模态与质量成本权衡.md)
- [云原生可观测性](../13-生产级工程与云原生/云原生可观测性-PrometheusGrafanaOpenTelemetry与SLO.md)
