---
title: Transformer 推理基础：注意力 Q/K/V 与 KV Cache
category: LLM 与 Prompt
tags:
  - Transformer
  - Self-Attention
  - QKV
  - KV Cache
  - Prefill
  - Decode
  - Inference
  - LLM Engineering
source:
  - title: 大模型为什么绕不开 Transformer
    url: https://www.douyin.com/video/7659794838195849395
  - title: 注意力机制 Q、K、V
    url: https://www.douyin.com/video/7659393273282929510
  - title: KV Cache 与显存占用
    url: https://www.douyin.com/video/7659019290100423066
reviewed_at: 2026-07-28
status: 持续更新
---

# Transformer 推理基础：注意力 Q/K/V 与 KV Cache

## 核心结论

这三个概念是一条连续链路：

```text
Transformer
用自注意力在 Token 之间传递信息
        ↓
Q 和 K 决定当前 Token 应关注谁
V 提供实际聚合的内容
        ↓
自回归生成时，历史 Token 的 K/V 会被后续 Token 反复使用
        ↓
KV Cache 用显存保存这些 K/V，避免重复计算历史前缀
```

Transformer 适合大模型，不只是因为“能看全局”，还因为训练阶段可以并行处理序列，并能通过增加数据、参数和算力持续扩展。KV Cache 则解决推理阶段的另一类问题：生成仍然必须逐 Token 进行，怎样避免每一步都重新计算整个历史。

> 资料说明：本笔记依据视频公开简介和章节摘要整理，并结合当前 AI 智能报价中台的模型调用与可观测性代码扩展，不是逐字字幕。

---

## 1. Transformer 为什么成为主流架构

### RNN 的限制

RNN 按时间步递归计算：

```text
x1 → h1 → h2 → h3 → ... → hn
```

后一个位置依赖前一个位置，因此：

- 训练难以在序列维度充分并行；
- 长序列的依赖需要经过很多状态传递；
- 远距离信息容易衰减；
- 序列越长，训练吞吐越受限制。

LSTM、GRU 缓解了长依赖问题，但没有消除递归计算的串行结构。

### CNN 的限制

CNN 擅长局部模式：

```text
相邻像素
局部词组
固定感受野
```

通过堆叠层或扩大卷积核可以覆盖更远位置，但跨越很远的动态关系不是它最自然的表达方式。

### Transformer 的优势

标准自注意力允许一个位置直接计算与其他位置的关系：

```text
Token i ──attention──> Token 1...n
```

主要优势：

1. **全局关系**：一个 Token 可以直接引用远处 Token。
2. **训练并行**：已知整个训练序列时，各位置可同时计算。
3. **统一表示**：文本、图像块、音频片段等都能表示为 Token 序列。
4. **可扩展性**：易于扩大层数、隐藏维度、数据和集群规模。
5. **硬件友好**：核心计算可转化为大规模矩阵乘法。

但要避免一个误解：

```text
训练阶段可以并行处理序列
≠
自回归生成可以一次并行生成所有未知 Token
```

生成第 `t+1` 个 Token 前，必须先知道前 `t` 个 Token，因此 Decode 仍具有串行依赖。

---

## 2. 一个 Transformer 层里有什么

以现代 Decoder-only LLM 为例，单层通常包含：

```text
输入隐藏状态
  → 归一化
  → 因果自注意力
  → 残差连接
  → 归一化
  → 前馈网络 MLP
  → 残差连接
```

重复多层后：

```text
最终隐藏状态
→ 词表投影
→ 每个候选 Token 的 logits
→ 采样或选择下一个 Token
```

Transformer 不等于只有 Attention。模型参数和计算还大量存在于：

- Token Embedding；
- Q/K/V/O 投影；
- MLP；
- LayerNorm 或 RMSNorm；
- 输出词表投影；
- 位置编码相关计算。

### 三种常见架构

| 架构 | 典型用途 |
|---|---|
| Encoder-only | 表示、分类和理解 |
| Decoder-only | 自回归生成，大多数通用 LLM |
| Encoder-Decoder | 输入到输出的条件生成 |

原始 Transformer 是 Encoder-Decoder；不能把 Transformer 与 Decoder-only 完全画等号。

---

## 3. Q、K、V 到底在做什么

设一层输入隐藏状态为 `X`，模型通过三组可学习矩阵生成：

```text
Q = XWq
K = XWk
V = XWv
```

对当前 Token 来说：

- `Query`：我现在需要什么信息；
- `Key`：我能以什么特征被其他 Token 匹配；
- `Value`：如果关注我，实际取走什么信息。

可以简化为：

```text
Q × K：找谁
权重 × V：拿什么
```

这个类比有助理解，但 Q/K/V 本质上都是训练得到的向量空间，不是人工编写的关键词、数据库键和值。

### 注意力公式

```text
Attention(Q, K, V)
= softmax((QKᵀ / √dk) + Mask)V
```

分步看：

1. `QKᵀ`：计算 Query 与各 Key 的匹配分数；
2. `/ √dk`：控制点积随维度增大造成的数值幅度；
3. `+ Mask`：屏蔽不能访问的位置；
4. `softmax`：把分数转成权重；
5. `权重 × V`：按权重聚合 Value。

### 一个直观例子

句子：

```text
小王把图纸交给预算员，因为他需要重新报价。
```

处理“他”时，模型会用“他”的 Query 与前文各 Token 的 Key 比较，再聚合相关 Token 的 Value。不同注意力头可能分别关注：

- 人物指代；
- 动作关系；
- 时间与因果；
- 报价业务语义。

模型不是查到一条固定规则，而是在当前层、当前头和当前上下文中动态计算关系。

---

## 4. 为什么要拆成 Q、K、V

如果同一个向量同时承担匹配和内容传递，表达能力会受限。

拆分后，一个 Token 可以：

- 用一种特征表达“我在找什么”；
- 用另一种特征表达“别人怎样找到我”；
- 用第三种特征表达“我真正提供什么内容”。

同一个词在不同句子、不同层和不同注意力头中，可以产生不同 Q/K/V。

### Multi-Head Attention

模型不会只做一次注意力，而是分为多个头：

```text
Head 1：局部语法
Head 2：远距离指代
Head 3：数量与单位
Head 4：业务对象关系
...
```

各头结果拼接后再经过输出投影。上面的“每个头负责什么”只是理解方式，实际能力由训练形成，不一定能一一人工命名。

### 位置为什么重要

纯 Attention 只看到 Token 集合，不能天然区分顺序。模型还需要位置编码，例如：

- 绝对位置编码；
- 相对位置偏置；
- RoPE 等旋转位置编码。

因此：

```text
Token 内容
+ 位置关系
+ 注意力交互
= 上下文化表示
```

---

## 5. 因果注意力：为什么生成不能偷看未来

生成模型处理位置 `t` 时，只能看到当前位置及之前的 Token。

因果 Mask 类似：

```text
位置 1：看 1
位置 2：看 1,2
位置 3：看 1,2,3
位置 4：看 1,2,3,4
```

训练时，完整答案虽然已经存在，但 Mask 会阻止模型读取未来 Token。这样训练目标才能与实际生成一致。

### 上下文越长的代价

标准全注意力需要考虑 Token 两两关系，训练或 Prefill 的注意力计算与注意力矩阵通常随序列长度呈二次增长：

```text
O(n²)
```

现代模型会使用高效 Attention Kernel、稀疏/滑动窗口策略或其他架构优化，但“更长上下文会增加计算与内存压力”这一工程结论仍成立。

---

## 6. 推理的两个阶段：Prefill 与 Decode

### Prefill

模型先处理已有输入：

```text
System Prompt
+ 用户问题
+ RAG 证据
+ Tool 历史
+ 会话上下文
```

这些 Token 已全部已知，可以成批计算每层的隐藏状态，并生成每个位置的 K/V。

Prefill 主要影响：

- Time to First Token；
- 长上下文处理成本；
- Prompt 和 RAG 证据的读取延迟；
- 初始 KV Cache 大小。

### Decode

得到第一个输出 Token 后，模型逐步生成：

```text
输出 Token 1
→ 输出 Token 2
→ 输出 Token 3
→ ...
```

每一步：

1. 读取上一步生成的 Token；
2. 计算当前 Token 在每层的 Q/K/V；
3. 当前 Q 与历史 K 计算注意力；
4. 聚合历史 V；
5. 生成下一个 Token；
6. 把当前 K/V 追加到缓存。

Decode 主要影响：

- Token 间延迟；
- 输出 Token/s；
- 长回答总耗时；
- 并发请求可容纳数量。

---

## 7. KV Cache 为什么有效

假设已有：

```text
今天需要生成一份工程报价
```

模型生成下一个 Token 后，历史前缀不会变化。历史 Token 在每层生成的 K/V 会被后续每一步重复使用。

不使用 Cache：

```text
每生成一个 Token
→ 重新计算整个历史前缀
```

使用 KV Cache：

```text
Prefill 时保存历史 K/V
→ Decode 只计算新 Token 的 Q/K/V
→ 当前 Q 读取缓存中的历史 K/V
→ 追加新 K/V
```

### 为什么不缓存历史 Query

Query 用于“当前这个位置主动查询过去”。某个历史位置的 Query 已经在生成该位置输出时使用完毕；未来 Token 不会再次拿历史 Query 去查询。

未来需要的是：

```text
新 Token 的 Query
×
全部历史 Token 的 Key
→
聚合全部历史 Token 的 Value
```

因此缓存 K/V，而不是历史 Q。

### KV Cache 没有消除什么

KV Cache 避免重复计算历史前缀，但当前 Query 仍需要与历史 Key 做注意力，序列越长，每个新 Token 要读取的缓存越多。

```text
KV Cache ≠ 每个新 Token 都是 O(1)
KV Cache ≠ 长上下文没有代价
KV Cache ≠ 可以无限生成
```

它是典型的：

```text
用内存换计算
```

---

## 8. KV Cache 占多少显存

简化估算：

```text
KV Cache bytes
≈ batch_size
× num_layers
× sequence_length
× 2
× num_kv_heads
× head_dim
× bytes_per_element
```

其中 `2` 表示 Key 和 Value 两份张量。

### 示例

假设：

```text
32 层
序列长度 8192
8 个 KV Head
Head Dimension 128
FP16，每个元素 2 字节
Batch Size 1
```

则约为：

```text
32 × 8192 × 2 × 8 × 128 × 2
= 1,073,741,824 bytes
≈ 1 GiB
```

如果同时服务 20 个同等长度请求，仅这一项就可能接近 20 GiB。实际还要考虑：

- 模型权重；
- 激活与临时工作区；
- 内存碎片；
- 推理框架元数据；
- 多模态输入；
- 不同请求长度造成的利用率。

因此生产容量不能只看“模型权重能否放进显存”。

---

## 9. MHA、GQA、MQA 与 KV Cache

不同注意力结构的 KV Head 数不同。

| 结构 | Query Head | KV Head | 特点 |
|---|---:|---:|---|
| MHA | 多 | 与 Query Head 数相同 | 表达能力强，KV Cache 较大 |
| GQA | 多 | 多个 Query Head 共享一组 KV Head | 质量和缓存占用折中 |
| MQA | 多 | 通常共享单组 K/V | KV Cache 更小，吞吐友好 |

KV Cache 公式中使用的是 `num_kv_heads`，不一定等于 Query Head 数。

这也是为什么只知道：

```text
层数、隐藏维度、上下文长度
```

仍不能精确估算 KV Cache；还要知道模型的 Attention 结构、数据类型和并发策略。

---

## 10. 常见显存与推理优化

### PagedAttention

把每个请求的 KV Cache 划分成可管理的块，类似虚拟内存分页：

- 降低连续大块分配需求；
- 减少内存碎片；
- 便于不同长度请求动态增长；
- 支持连续批处理和更高并发。

它主要解决 KV Cache 的内存管理问题。

### KV Cache Quantization

用更低精度存储 K/V：

```text
FP16 / BF16
→ FP8 / INT8 / 更低精度方案
```

优点是减少显存与带宽，代价可能是额外量化计算和精度影响，需要评测。

### GQA / MQA

减少 KV Head 数，从模型结构上缩小缓存。

### Prefix Cache

多个请求共享完全相同的前缀时，可以复用前缀对应的 KV：

```text
相同 System Prompt
+ 相同工具定义
+ 相同固定业务规则
```

但必须绑定：

- 模型与模型版本；
- Tokenizer；
- 精确 Token 序列；
- 推理参数；
- 权限和租户；
- Prompt、工具和知识版本；
- 位置编码与缓存实现。

这不是“意思相近就复用”的语义缓存。

### Sliding Window / Context Pruning

限制每层只关注一定范围，或在应用层删除、摘要和压缩旧上下文。能降低成本，但可能损失远距离信息。

### FlashAttention

FlashAttention 通过 IO-aware 的分块计算减少显存读写和中间注意力矩阵开销，提升 Attention Kernel 效率。

需要区分：

```text
PagedAttention：管理 KV Cache 块
KV 量化：压缩缓存
GQA/MQA：减少 KV Head
FlashAttention：优化 Attention 计算与显存 IO
```

它们解决的问题有关联，但不是同一种技术。

---

## 11. 从模型原理映射到系统指标

| 模型阶段 | 主要受什么影响 | 应观察的指标 |
|---|---|---|
| 排队 | 并发、配额、显存槽位 | queue time |
| Prefill | 输入 Token、Attention、前缀缓存 | TTFT、input tokens |
| Decode | 输出长度、KV 带宽、采样 | inter-token latency、tokens/s |
| 完整请求 | 工具、网络、重试、生成 | total latency |

### 为什么只看总耗时不够

同样是 10 秒：

```text
情况 A：
9 秒才出第一个 Token，1 秒生成完成

情况 B：
1 秒出第一个 Token，持续 9 秒生成长答案
```

优化方向完全不同：

- A 重点检查排队、长输入、Prefill 和冷启动；
- B 重点检查输出长度、Decode 吞吐和流式体验。

### 上下文长度怎样影响 Agent

Agent 上下文通常包含：

```text
System Prompt
工具 Schema
状态视图
用户输入
RAG 证据
工具结果
历史消息
输出预算
```

这些都会增加 Prefill 和 KV Cache 压力。Context Engineering 的目标不仅是防噪声，还包括控制：

- 输入 Token；
- TTFT；
- 单请求缓存占用；
- 并发容量；
- 供应商费用；
- 重要证据是否被长上下文稀释。

---

## 12. 当前 AI 智能报价中台的实现映射

以下结论来自 2026-07-28 对项目代码的检查。

### 当前架构边界

项目主要通过外部 API 调用：

- DeepSeek；
- GLM Vision；
- Qwen / DashScope；
- OpenAI；
- n8n / Dify 后的模型链路。

当前没有自建 vLLM、TGI 或 Ollama 等 GPU 推理服务。因此：

```text
模型内部 Attention、KV Cache 和显存调度
由供应商负责

应用侧负责
上下文、请求预算、并发、超时、重试、路由和可观测性
```

现阶段不应为了学习 KV Cache 就在项目中自行实现推理引擎。

### 已有基础

1. **模型网关日志**
   - `model_call_logs` 已记录供应商、模型、端点、状态、HTTP 状态、总延迟、输入/输出字符、估算成本和错误；
   - 网关支持超时、失败计数和熔断。

2. **多供应商路由**
   - 图纸链路支持 GLM、Qwen/DashScope 和 OpenAI；
   - 资料研判 Agent 支持主模型与回退模型，并记录实际路由。

3. **调用规模限制**
   - 图纸识别有最大页面、最大视图、批大小和并发参数；
   - 成本测算和报价任务已有 Semaphore、批处理及 Celery 并发限制；
   - 这能间接控制外部模型的上下文、吞吐和供应商配额压力。

4. **Agent 运行预算**
   - 资料研判 Agent 已限制最大推理循环、工具总数、单轮工具数和重复参数；
   - 但这些是步骤预算，还不是 Token 预算。

5. **Usage 元数据**
   - OpenAI-compatible 资料研判适配器会把供应商返回的 `usage` 放入消息元数据；
   - 当前执行 Trace 主要读取模型路由，尚未看到 usage 被统一持久化和聚合。

### 当前缺口

| 缺口 | 工程影响 |
|---|---|
| 成本按输入/输出字符估算，而不是真实 Token | 不同模型、语言和多模态请求的成本误差较大 |
| 只有 HTTP 总延迟 | 无法区分排队、Prefill、TTFT 和 Decode |
| 非流式调用为主 | 无法测量首 Token 和 Token 间延迟 |
| Agent 有步骤预算但缺少输入/输出 Token 上限 | 长 Tool 结果和历史消息可能持续膨胀 |
| 没有统一记录 Context Window 使用率 | 无法提前发现截断、超限和长上下文退化 |
| 无供应商 Prefix Cache 命中记录 | 不能判断稳定前缀是否真正降低成本或 TTFT |
| 多供应商 usage 字段没有统一标准 | 路由和成本比较缺少可靠数据 |

### 推荐升级顺序

#### P0：统一 Token Usage

模型调用记录新增或统一：

```text
input_tokens
output_tokens
total_tokens
cached_input_tokens
reasoning_tokens
provider_usage_raw
```

不同供应商没有某字段时保留空值，不要用字符数伪装成精确 Token。

#### P0：建立上下文预算

按端点配置：

```text
max_input_tokens
max_output_tokens
reserved_output_tokens
max_tool_result_tokens
max_history_tokens
```

超过预算时优先压缩低价值历史和重复工具结果，不能静默删除权限、业务规则和关键证据。

#### P1：拆分延迟

在支持流式模型后记录：

```text
queue_ms
request_build_ms
provider_connect_ms
ttft_ms
decode_ms
output_tokens_per_second
total_ms
```

当前非流式链路先记录能够准确拆分的 queue、context build、retrieval、provider 和 tool。

#### P1：稳定前缀与缓存安全

把稳定的 System Prompt、工具 Schema 和政策版本放在前部，有助于供应商 Prefix Cache；但缓存必须绑定租户、权限、模型和版本，不能让成本优化破坏隔离。

#### P2：只有自建推理时再治理 KV Cache

如果未来部署本地模型，再补：

```text
KV Cache bytes
cache utilization
eviction rate
prefix cache hit rate
prefill/decode batch
GPU memory waterline
request admission
PagedAttention block utilization
```

当前阶段这些属于供应商内部指标，不应伪造。

---

## 13. 常见误区

### 误区一：Transformer 训练和推理都能完全并行

正确理解：

```text
训练/Prefill：已知序列可并行处理
自回归 Decode：输出 Token 之间仍然串行
```

### 误区二：Q、K、V 就是数据库查询、主键和值

它们是通过可学习矩阵得到的向量。数据库类比只帮助理解“匹配和取值”，不能据此推断模型内部存在显式键值表。

### 误区三：KV Cache 是 Agent Memory

KV Cache：

- 是模型层内部张量；
- 通常只服务当前推理会话；
- 保存历史 Token 的 K/V；
- 不能独立检索业务事实。

Agent Memory：

- 是应用层持久化信息；
- 具有用户、任务、来源和生命周期；
- 需要召回、更新和遗忘策略。

### 误区四：KV Cache 等同语义缓存

KV Cache 复用精确 Token 前缀的模型中间状态；语义缓存按问题相似度复用应用结果。二者的命中条件、安全风险和失效规则完全不同。

### 误区五：FlashAttention 就是压缩 KV Cache

FlashAttention 主要优化 Attention 计算和显存 IO；KV 量化、GQA/MQA 和 PagedAttention 才更直接影响 KV Cache 的大小或管理方式。

### 误区六：上下文窗口越大越好

更大窗口提供容量，但也可能增加：

- Prefill 延迟；
- 请求费用；
- KV Cache；
- 并发压力；
- 噪声和注意力稀释；
- 截断策略复杂度。

生产系统应追求“足够且高质量的上下文”，不是无限堆积。

---

## 14. 面试表达

### 问：为什么大模型大多使用 Transformer？

可以回答：

> 大模型训练需要同时满足长距离关系、大规模并行和可扩展性。RNN 的递归结构限制序列并行，CNN 更偏局部模式；Transformer 用自注意力让位置直接交互，训练可转化为大规模矩阵计算，适合扩展数据、参数和集群规模。但标准注意力对长序列有二次复杂度，而且自回归 Decode 仍是逐 Token 的，所以推理还需要 KV Cache、批处理和高效 Attention Kernel。

### 问：Q、K、V 分别是什么？

可以回答：

> 输入隐藏状态通过三组可学习投影得到 Q、K、V。Q 表示当前位置要查询什么，K 表示各位置可被怎样匹配，QK 点积经过缩放、Mask 和 softmax 得到注意力权重，再对 V 加权求和。简化说 Q/K 决定“关注谁”，V 决定“取什么”。拆分三种表示让同一 Token 在不同上下文和注意力头中承担不同角色。

### 问：为什么 KV Cache 只缓存 K/V？

可以回答：

> 自回归生成中，历史 Token 的 K/V 会被每个未来 Token 反复读取，而历史 Query 在生成其对应位置时已经完成使命。Prefill 先计算并缓存整个输入的 K/V；Decode 每步只计算新 Token 的 Q/K/V，用新 Q 读取历史 K/V，再追加新 K/V。它用显存换计算，避免重复计算前缀，但当前 Query 仍要扫描历史缓存，所以长序列和高并发仍会增加显存与带宽压力。

### 项目表达

当前报价中台可以这样讲：

```text
项目不自建 GPU 推理，KV Cache 由模型供应商负责。

应用层已做：
多供应商路由、调用日志、总延迟、字符量、熔断、
视图/批次/并发限制和 Agent 步骤预算。

现有缺口：
缺少统一真实 Token、Context 使用率、TTFT、TPS 和缓存命中。

下一步：
先统一 Usage 和上下文预算，再按流式能力拆分 Prefill/Decode 指标；
只有未来自建推理服务时，才直接治理 PagedAttention 和 KV Cache 水位。
```

---

## 15. 检查清单

### 原理

- [ ] 能区分训练并行与自回归生成串行
- [ ] 知道 Transformer 不只有 Attention
- [ ] 能解释 Q/K 匹配和 V 聚合
- [ ] 知道因果 Mask 防止读取未来 Token
- [ ] 能区分 MHA、GQA、MQA 对 KV Head 的影响
- [ ] 能解释 Prefill 与 Decode
- [ ] 知道为什么缓存 K/V 而不缓存历史 Q

### 推理工程

- [ ] 能估算 KV Cache 随层数、序列、KV Head、精度和 Batch 的变化
- [ ] 不把 KV Cache、Prefix Cache、语义缓存和 Agent Memory 混为一谈
- [ ] 能区分 PagedAttention、FlashAttention、KV 量化和 GQA/MQA
- [ ] 同时观察 TTFT、Token 间延迟、TPS 和总耗时
- [ ] 输入与输出均有 Token 预算
- [ ] 并发准入考虑长上下文和显存占用，而不只看 QPS
- [ ] Prefix Cache 绑定模型、Token 序列、权限和版本

## 关联笔记

- [模型输入输出控制：Token、上下文、Prompt、结构化输出与流式生成](./模型输入输出控制-Token上下文Prompt结构化输出与流式生成.md)
- [模型路由与协同：大小模型、多模态与质量成本权衡](./模型路由与协同-大小模型多模态与质量成本权衡.md)
- [模型推理服务：量化、vLLM、吞吐、延迟与部署治理](./模型推理服务-量化vLLM吞吐延迟与部署治理.md)
- [模型能力优化：Scaling、解码策略与微调](./模型能力优化-Scaling解码策略与微调.md)
- [Agent 可靠性工程：上下文、护栏、状态、评测与追踪](../03-生产级开发基础/Agent可靠性工程-上下文护栏状态评测与追踪.md)
- [LLM 平台工程：统一网关、资源限流、延迟拆解与安全缓存](../10-LLMOps与可观测性/LLM平台工程-统一网关资源限流延迟拆解与安全缓存.md)
- [Agent 记忆系统架构：短期、工作、压缩与长期记忆](../07-Memory/Agent记忆系统架构-短期工作压缩与长期记忆.md)
- [企业语义与时间上下文：业务词典、标准化与时态治理](../03-生产级开发基础/企业语义与时间上下文-业务词典标准化与时态治理.md)
