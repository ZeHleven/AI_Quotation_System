---
title: Agent 工具与 Skill 路由：分层召回、重排、执行校验与 Workflow 边界
category: 工具调用与 MCP
tags:
  - Agent
  - Tool Routing
  - Skill Routing
  - Tool Calling
  - MCP
  - Rerank
  - Workflow
  - FDE
source:
  - title: Agent 技能太多怎么选对
    url: https://www.douyin.com/video/7661443844164825946
  - title: Agent 工具太多怎么防选错
    url: https://www.douyin.com/video/7660693500581885642
  - title: Agent Skill 很多时怎么保证命中
    url: https://www.douyin.com/video/7659018850272845193
  - title: Agent 和 Workflow 到底有什么区别
    url: https://www.douyin.com/video/7659794205560134537
reviewed_at: 2026-07-28
status: 持续更新
---

# Agent 工具与 Skill 路由：分层召回、重排、执行校验与 Workflow 边界

## 核心结论

当 Agent 只有少数工具时，可以把全部工具 Schema 交给模型选择；当工具或 Skill 达到几十、上百个时，继续“把描述写得更长、全部塞进 Prompt”会导致：

- 上下文膨胀；
- 相似能力互相干扰；
- 误选和漏选增加；
- 权限暴露范围扩大；
- Token、延迟和调试成本上升。

此时，工具选择应被设计成受控的检索与执行系统：

```text
任务理解
→ 业务域与权限过滤
→ 候选召回
→ Schema、前置条件、风险与质量重排
→ 选择或澄清
→ 调用前确定性校验
→ 执行与结果验证
→ Trace、反馈和路由评测
```

同时先判断控制权：

```text
路径应由代码决定 → Workflow
下一步必须根据环境反馈动态决定 → 受控 Agent
```

> 资料说明：本笔记依据视频的公开简介、摘要和章节结构整理，并结合当前 AI 智能报价中台代码扩展，不是逐字字幕。

---

## 1. Tool、Skill、Workflow 与 Agent

### Tool

Tool 是可执行接口，例如：

- 检索招标证据；
- 查询成本库；
- 读取文件上下文；
- 创建任务；
- 发送审批；
- 导出 Excel。

它应有明确的输入、输出、权限、副作用和错误合同。

### Skill

Skill 是可复用能力包，可能包含：

- 适用场景和操作说明；
- Prompt 或领域规则；
- 一个或多个工具；
- 脚本、模板和参考资料；
- 验证与异常处理流程。

因此：

```text
Tool = 可调用动作
Skill = 完成一类任务的方法与资源包
```

一个 Skill 可能不直接执行外部动作，也可能编排多个 Tool。

### Workflow

Workflow 的步骤和分支主要由程序预先定义：

```text
上传文件
→ 解析
→ 规则校验
→ AI 处理
→ 人工预审
→ 确认下发
```

即使其中调用了模型和工具，它仍然可以是 Workflow。

### Agent

Agent 根据目标、当前状态和工具返回结果，动态决定下一步：

```text
Goal
→ Observe
→ Select action
→ Execute
→ Observe again
→ Stop or continue
```

是否调用工具不是 Agent 与 Workflow 的分界。真正的分界是：

> 下一步控制权主要在预设代码，还是在受控范围内交给模型动态决策。

---

## 2. 为什么只优化 Prompt 不够

### 工具描述相似

例如：

```text
search_project
search_project_document
search_project_history
search_tender_evidence
```

模型容易只根据名字或局部词汇误选。

### 全量加载造成干扰

如果每轮把 100 个工具 Schema 全部发送给模型：

- Prompt Token 增加；
- 模型注意力被无关工具分散；
- 相似工具冲突；
- 每次工具变更都影响全局；
- 未授权工具可能被提前暴露。

### 描述没有负边界

只写“这个工具能做什么”，没有写：

- 什么时候不能用；
- 需要哪些前置数据；
- 与相似工具怎样区分；
- 是否有副作用；
- 哪些结果必须人工确认。

模型就只能靠猜。

### 选择和执行混在一起

模型认为某工具“看起来适合”，不代表：

- 用户有权限；
- 参数完整；
- 资源属于当前租户；
- 业务状态允许；
- 风险可接受；
- 操作可以回滚。

路由是概率判断，执行前还需要确定性授权。

---

## 3. 建立结构化工具目录

不要只维护 `name + description`。建议至少记录：

```yaml
tool_id: tender.search_evidence
version: v3
domain: bidding
capabilities:
  - evidence_search
positive_triggers:
  - 查询当前招标项目中的事实证据
negative_triggers:
  - 不用于读取完整上下文
  - 不用于跨项目检索
required_inputs:
  - query
preconditions:
  - active_manifest_exists
permissions:
  - tender:evidence.search
risk_level: read_only
side_effect: none
idempotent: true
latency_class: low
cost_class: low
output_schema: ToolResultV1
owner: bidding-platform
```

### 目录的关键字段

| 字段 | 用途 |
|---|---|
| domain/capabilities | 做一级过滤和候选召回 |
| positive triggers | 描述典型命中场景 |
| negative triggers | 构造混淆负样本，降低误触发 |
| required inputs | 判断参数是否齐全 |
| preconditions | 判断业务状态是否允许 |
| permissions/scope | 执行前强制授权 |
| side effect/risk | 决定审批、幂等和回滚要求 |
| cost/latency | 在多个可用工具之间优化 |
| version/schema | 支持发布、回滚和复现 |

### 描述具体触发场景

不推荐：

```text
处理招标资料。
```

推荐：

```text
当需要在当前招标项目中查找付款、保证金、工期或资格等事实证据时使用。
只返回候选片段；形成高风险结论前还必须调用 read_evidence_context。
```

描述要同时说明用途、边界和后续动作。

---

## 4. 分层加载与两级路由

### 第一层：确定性过滤

先由代码排除不可能的候选：

- 当前租户不可见；
- 用户无权限；
- 业务域不匹配；
- 风险等级不允许；
- 前置状态不满足；
- 数据类型不兼容；
- 当前环境未启用；
- 已熔断或不可用。

这一步不能交给模型。

### 第二层：业务域路由

先选能力域：

```text
报价
招投标
成本库
项目进度
文件处理
会议与任务
```

模型只需要在相关域中选择。

### 第三层：候选召回

召回目标是避免漏掉正确工具，可以组合：

- 精确标签；
- 关键词或 BM25；
- 规则；
- Embedding 相似度；
- 历史成功调用；
- 当前 Workflow 节点限定。

```text
全部工具 200 个
→ 权限与业务域过滤 35 个
→ 召回 Top 10
```

### 第四层：重排

重排目标是把最合适的候选放到前面：

- 任务语义匹配；
- 输入 Schema 兼容；
- 必需参数是否存在；
- 前置条件；
- 风险和副作用；
- 成本与延迟；
- 历史成功率；
- 当前工具健康状态。

可使用规则、小模型、交叉编码器或 LLM，但安全条件必须是硬过滤项。

```text
候选 Top 10
→ 重排 Top 3
→ 模型选择一个，或要求澄清
```

### 渐进加载

模型开始时只看到：

- 能力域摘要；
- 当前允许的候选摘要；
- 必要的选择边界。

选中能力后，再加载完整 Tool/Skill 说明和 Schema。这样能减少 Token 和干扰。

---

## 5. 选择时需要解释，但不保存私有思维链

建议记录简短、结构化的选择摘要：

```json
{
  "selected_tool": "read_evidence_context",
  "reason_code": "HIGH_RISK_CLAIM_REQUIRES_CONTEXT",
  "matched_requirements": ["evidence_id_available"],
  "rejected_candidates": [
    {
      "tool": "search_tender_evidence",
      "reason_code": "CANDIDATE_ONLY_NOT_AUTHORITATIVE_CONTEXT"
    }
  ]
}
```

它用于：

- 调试误选；
- 构建训练和评测数据；
- 解释为什么没有执行；
- 分析路由版本退化。

不需要保存模型完整内部推理。

### 什么时候先澄清

出现以下情况时，不应勉强选一个工具：

- 多个候选得分接近；
- 缺少必填参数；
- 用户目标存在歧义；
- 资源或项目不明确；
- 高风险动作缺少审批；
- 没有工具真正匹配。

```text
低风险 + 高置信度 → 执行
低风险 + 中等置信度 → 澄清
高风险 → 硬校验 + 人工确认
无匹配 → 安全失败
```

---

## 6. 调用前校验比“选对”更重要

模型完成候选选择后，执行器还应检查：

1. 工具名是否在服务端白名单；
2. 工具版本是否允许；
3. 参数是否符合 Schema；
4. 参数值是否满足业务约束；
5. 当前用户、租户和资源范围；
6. 业务状态和前置条件；
7. 副作用与风险级别；
8. 是否需要审批；
9. 幂等键、版本号和参数哈希；
10. 调用预算、并发和费用；
11. 工具是否健康；
12. 是否存在重复调用。

选择层可以出错，但执行层必须 fail closed。

### 参数校验的三个层次

```text
类型校验
例如 top_k 必须为整数

范围校验
例如 1 <= top_k <= 20

业务语义校验
例如 document_key 必须属于当前 manifest
```

JSON Schema 只能覆盖前两类的一部分，不能替代业务授权。

### 写工具需要额外保护

```text
read
→ create/update
→ publish/approve
→ delete
```

风险逐级升高。写工具至少需要：

- 参数预览；
- Human-in-the-loop；
- 绑定批准参数哈希；
- 幂等键；
- 乐观锁或业务版本；
- 执行回执；
- 补偿或回滚方案；
- 审计日志。

---

## 7. 执行结果也需要验证

标准结果信封：

```json
{
  "status": "ok",
  "data": {},
  "retryable": false,
  "trace_id": "trace_xxx",
  "error_code": null,
  "message": null
}
```

Agent 不应只看 HTTP 200，还要判断：

- 是否真正有结果；
- 是否部分成功；
- 数据是否来自正确资源；
- 是否符合输出 Schema；
- 是否过期；
- 是否需要重试；
- 副作用是否已生效；
- 是否还要读取权威上下文；
- 是否满足停止条件。

Tool Result 是 Observation，不是天然可信事实。

---

## 8. 失败反馈闭环

### 路由失败分类

| 类型 | 示例 |
|---|---|
| 未召回 | 正确工具没有进入候选集 |
| 重排错误 | 正确工具被相似工具压到后面 |
| 误触发 | 本来无需工具却执行了 |
| Schema 错误 | 工具选对但参数格式错误 |
| 权限拒绝 | 候选未在路由前完成权限过滤 |
| 前置条件错误 | 业务状态不允许执行 |
| 重复调用 | 同一参数反复查询 |
| 执行失败 | 工具内部、网络或依赖异常 |
| Observation 误读 | 工具返回正确但 Agent 解释错误 |
| Workflow 边界错误 | 本应固定执行却交给模型选择 |

### 错误怎样回流

```text
Trace
→ 人工标记首个错误阶段
→ 形成正例、负例和混淆对
→ 更新描述、过滤规则或重排器
→ 固定评测集回放
→ 灰度发布新路由版本
```

不要只修改 Prompt 后直接上线。

---

## 9. 工具路由评测

### 离线指标

| 阶段 | 指标 |
|---|---|
| 权限过滤 | 未授权候选暴露率 |
| 召回 | Tool Recall@K |
| 重排 | Top-1 Accuracy、MRR、NDCG |
| 选择 | 正确工具率、无需调用识别率 |
| 参数 | Schema 有效率、参数准确率 |
| 安全 | 越权率、审批绕过率、危险调用率 |
| 过程 | 重复调用率、无效调用率、澄清率 |
| 结果 | 工具成功率、任务完成率 |
| 系统 | Token、P95、费用 |

### 评测集必须包含

- 正常单工具任务；
- 多个相似工具；
- 不需要调用工具；
- 缺少参数，需要澄清；
- 无权限工具；
- 高风险写操作；
- 工具不可用或超时；
- 恶意文档诱导调用；
- 正确工具不在候选集；
- 多工具依赖任务。

### 分开评测召回与重排

如果正确工具未进入 Top-K，是召回问题；如果进入候选但最终选错，是重排或决策问题。

两者混成“Agent 答错”会找不到根因。

### 路由也要版本化

每次运行记录：

```text
tool_catalog_version
permission_policy_version
recall_version
reranker_version
prompt_version
tool_schema_version
candidate_tools
selected_tool
authorization_result
execution_result
```

---

## 10. Agent 与 Workflow 的工程边界

### 适合 Workflow

- 步骤固定；
- 业务规则明确；
- 高审计要求；
- 不允许跳步；
- 错误可通过代码判断；
- 写操作和审批主链。

### 适合 Agent

- 需要根据新 Observation 决定下一步；
- 信息来源和顺序不固定；
- 工具组合无法完全预先枚举；
- 目标明确但路径存在不确定性；
- 可以用预算、权限、验证器和人工边界控制。

### 推荐的混合架构

```text
Workflow 控制主链路
├── 身份与权限
├── 数据版本
├── 确定性规则
├── 审批与写操作
└── 局部 Agent 节点
    ├── 动态检索
    ├── 工具选择
    ├── 证据补充
    └── 不确定性判断
```

Agent 负责处理不确定性，Workflow 负责控制确定性和责任边界。

---

## 11. 当前 AI 智能报价中台的项目映射

### 11.1 当前工具规模

报价资料研判 Agent 向模型暴露 4 个 ReAct 工具：

```text
search_tender_evidence
read_evidence_context
compare_document_versions
get_bid_policy_rule
```

前三个通过 Tender Evidence MCP 访问证据，最后一个读取当前绑定的本地政策。

工具总量很小，直接绑定全部 4 个工具是合理设计。此时引入向量召回和复杂 Rerank，收益可能低于复杂度。

### 11.2 已落地的路由与执行保护

| 环节 | 当前实现 |
|---|---|
| 工具描述 | 每个 Tool 有名称、说明和参数 Schema |
| 使用边界 | System Prompt 规定何时检索、读上下文、比较版本和读取政策 |
| 工具白名单 | `authorize_tools` 检查 `ALLOWED_TOOLS` |
| 总预算 | 限制推理轮数和总工具调用数 |
| 单轮预算 | 默认每轮最多 3 个工具 |
| 重复保护 | 相同工具与参数默认最多 2 次 |
| 参数范围 | Tool Schema、函数边界和 MCP 服务多层限制 |
| 资源作用域 | 短期 token 绑定 case、assessment 和 agent run |
| 能力作用域 | MCP 服务检查 `allowed_tools` |
| 防篡改 | case_id 不作为模型可修改的工具参数 |
| 只读边界 | 当前 ReAct 工具没有业务写入能力 |
| 结果合同 | `ToolResult` 包含 status、retryable、trace_id 和 error |
| 执行 Trace | 记录工具名、脱敏参数、状态、结果数量和证据 ID |
| 证据验证 | 高风险结论必须读过上下文并通过确定性证据门 |
| 人工边界 | 最终建议进入 Human-in-the-loop |

一个很好的设计细节是：

> `validate_evidence_refs` 是确定性证据门工具，明确不绑定给 ReAct 模型，由系统节点调用。

模型不能选择是否绕过最终证据校验。

### 11.3 当前还不是动态 Skill Router

项目尚未建立：

- 一等公民的 Skill 注册表；
- 按业务域、权限和能力生成候选；
- Embedding/BM25 召回；
- 工具 Rerank；
- 显式 `selection_reason`；
- Tool Recall@K 和 Top-1 路由指标；
- 误选、未召回和混淆对数据集；
- 路由版本的 baseline/candidate 回放。

这不是当前缺陷，因为工具只有 4 个。它是未来工具规模扩大后的演进条件。

### 11.4 需要留意的 Schema 漂移

当前模型侧 `TOOL_SCHEMAS` 与执行侧 `StructuredTool` 分别定义。

这意味着工具名称、描述、参数范围和默认值存在双份维护风险。

建议：

```text
单一工具注册定义
→ 生成模型 Tool Schema
→ 生成执行器参数模型
→ 生成文档和版本指纹
```

`tool_schema_version` 已经存在，可继续作为发布和 Trace 的版本入口。

### 11.5 调用前校验边界

当前 `authorize_tools` 节点主要检查：

- 工具白名单；
- 总调用预算；
- 相同参数重复预算。

类型和范围由 Tool Schema、StructuredTool 和 MCP 服务继续校验；项目资源范围由 scoped token 和 Repository 强制限制。

由于当前工具只读，这已经形成合理的防御纵深。未来增加写工具后，还需在执行前增加：

- 动作级权限；
- 业务状态；
- 风险审批；
- 参数哈希；
- 幂等与版本；
- 回滚或补偿。

### 11.6 Agent 与 Workflow 的项目分工

当前报价中台的大部分功能属于确定性 Workflow：

```text
FastAPI
→ 解析与规则
→ n8n / Dify
→ 成本依据与完整性校验
→ 人工预审
→ 确认下发
```

报价资料研判采用混合模式：

```text
LangGraph 固定主状态机
→ ReAct 动态选择 4 个只读工具
→ 严格 Schema
→ 确定性政策评估
→ 确定性证据门
→ Human-in-the-loop
```

这比把整个报价中台改造成“全自主 Agent”更符合生产要求。

---

## 12. 推荐的渐进改造顺序

### P0：统一工具注册定义

先消除模型 Schema、执行 Schema 和文档的双份维护。

注册表应包含：

- 版本；
- 业务域；
- 正负触发场景；
- 参数和结果 Schema；
- 权限；
- 风险和副作用；
- 成本、延迟和健康状态；
- owner。

### P0：增加路由事件和数据集

即使仍是 4 个工具，也开始记录：

```text
task_intent
available_tools
requested_tool
authorization_result
tool_args_valid
execution_result
human_error_label
```

先积累真实误选和重复调用，再决定是否需要检索路由。

### P1：工具规模扩大后引入分层路由

建议触发条件不是固定数字，而是出现：

- Prompt 明显膨胀；
- 相似工具误选增加；
- 工具权限差异扩大；
- 路由 P95 上升；
- 新增工具影响无关场景；
- Tool Top-1 准确率下降。

再引入：

```text
权限/风险硬过滤
→ 业务域路由
→ Recall@K
→ Rerank
→ 选择或澄清
```

### P1：为写工具建立独立治理层

写工具不要与只读工具共享同一默认策略。优先按：

```text
read
create
update
approve/publish
delete
```

建立独立权限、审批、审计和回滚。

### P2：不急于增加 Multi-Agent

工具路由问题优先通过目录、检索、重排和验证解决。把不同工具拆成多个 Agent 会增加通信、状态和责任边界，并不会自动提高命中率。

---

## 13. 面试表达

### 工具太多时怎样保证选对？

> 我不会把所有工具直接塞给模型，而是把选择设计成检索路由：先按租户权限、业务域、风险和前置条件硬过滤，再用关键词、规则或 Embedding 召回，按 Schema 兼容、任务相关性、成本和历史成功率重排。模型只能在候选集中选择或请求澄清，执行前再做确定性授权和参数校验。

### Skill 描述应该怎样写？

> Skill 描述要包含具体触发场景、输入输出、前置条件、禁用条件和与相似 Skill 的区别。正例提高召回，负例和边界减少误触发；规模扩大后还要分层加载，避免把全部 Skill 注入上下文。

### Agent 和 Workflow 有什么区别？

> 分界线是控制权，不是是否调用模型或工具。Workflow 的路径主要由代码预设，Agent 根据环境反馈在受控范围内动态决定下一步。生产系统通常用 Workflow 控制主链和写操作，局部不确定节点使用 Agent。

### 怎样评价当前项目的工具路由？

> 当前资料研判 Agent 只有 4 个只读工具，所以静态绑定更合适。项目已有白名单、Schema、scoped token、调用预算、重复保护、Trace、证据门和人工确认。下一步应先统一双份 Tool Schema、积累路由失败样本；只有工具规模和误选率真正上升后，再引入分层召回和 Rerank。

---

## 14. 检查清单

### Catalog

- [ ] Tool/Skill 有唯一 ID、版本和 owner
- [ ] 同时记录正触发与负触发场景
- [ ] 参数、结果、权限、风险和副作用明确
- [ ] 模型 Schema 与执行 Schema 来自同一来源

### Routing

- [ ] 权限、租户和风险在召回前过滤
- [ ] 分开评测 Recall 与 Rerank
- [ ] 低置信度时澄清或安全失败
- [ ] 候选、选择理由和路由版本可追踪

### Execution

- [ ] 服务端白名单和参数 Schema 生效
- [ ] 业务状态与资源作用域由代码校验
- [ ] 有调用、循环、重复和成本预算
- [ ] 高风险写操作绑定审批与参数哈希
- [ ] Tool Result 使用统一结构并验证

### Evaluation

- [ ] 有相似工具和无需调用负样本
- [ ] 统计 Recall@K、Top-1、参数准确率和误触发率
- [ ] 统计越权、重复、无效调用和人工接管
- [ ] 路由新版本经过固定数据集回放和灰度

### Architecture

- [ ] 固定主链优先使用 Workflow
- [ ] Agent 只处理真正不确定的节点
- [ ] Verifier 和 Human-in-the-loop 不由模型自行绕过
- [ ] 没有为解决工具路由问题盲目增加 Multi-Agent

---

## 关联笔记

- [MCP 运行机制：架构、协议、传输、安全与 Tool Calling 边界](./MCP运行机制-架构协议传输安全与ToolCalling边界.md)
- [Agent 工具安全：权限作用域、执行沙箱、注入防护与执行校验](./Agent工具安全-权限作用域注入防护与执行校验.md)
- [Agent 决策循环与执行架构](../06-Agent规划与工作流/Agent决策循环与执行架构.md)
- [生产级 Agent 核心机制与工程实践](../03-生产级开发基础/生产级Agent核心机制与工程实践.md)
- [Agent 评测体系：事实、过程、工具、效率、安全与版本治理](../10-LLMOps与可观测性/Agent评测体系-事实过程工具效率安全与版本治理.md)
- [LLM 平台工程：统一网关、资源限流、延迟拆解与安全缓存](../10-LLMOps与可观测性/LLM平台工程-统一网关资源限流延迟拆解与安全缓存.md)
