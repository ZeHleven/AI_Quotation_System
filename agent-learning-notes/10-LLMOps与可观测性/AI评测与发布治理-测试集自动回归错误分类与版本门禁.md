---
title: AI 评测与发布治理：测试集、自动回归、错误分类与版本门禁
category: LLMOps 与可观测性
tags:
  - Evaluation
  - Golden Dataset
  - Regression Testing
  - Prompt Versioning
  - Error Taxonomy
  - Release Gate
  - LLMOps
sources:
  - https://www.nist.gov/itl/ai-risk-management-framework
  - https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
reviewed_at: 2026-07-30
status: 已整理
---

# AI 评测与发布治理：测试集、自动回归、错误分类与版本门禁

## 核心结论

AI 评测不是给回答打一个总分，而是证明某个完整运行版本在目标数据和风险边界上可以发布。

```text
模型 + Prompt + 参数 + Tool + Workflow
+ RAG/数据 + Policy + 代码
→ 固定测试集
→ 分层评测
→ 与基线对比
→ 发布门
→ 灰度
→ 线上反馈回流
```

高风险越权、泄露和关键业务错误属于硬门槛，不能被平均分掩盖。

---

## 1. 评测对象

每次 Eval Run 应绑定：

- 代码 commit；
- 模型供应商、模型 ID 和版本；
- Prompt 模板、变量与哈希；
- Temperature、Top-p、Token 上限；
- 工具 Schema 与权限策略；
- Workflow/Agent 图版本；
- RAG 集合、Embedding、Rerank 和数据快照；
- 业务规则和 Policy 版本；
- 评测数据集版本；
- 评测器版本。

没有版本清单，结果无法复现，也无法判断退化来自哪里。

---

## 2. 测试集分层

| 集合 | 用途 |
|---|---|
| Development | 日常开发和调参，可反复查看 |
| Holdout | 最终盲测，避免针对测试集过拟合 |
| Regression | 历史线上缺陷，保证不复发 |
| Adversarial | 注入、越权、泄露和边界攻击 |
| Shadow/Canary | 真实流量下比较候选与基线 |

### 样本必须覆盖

- 正常高频；
- 长尾和边界；
- 无答案；
- 冲突证据；
- 模糊或缺失字段；
- 工具故障；
- 超时和重试；
- 越权和 Prompt Injection；
- 敏感信息；
- 高金额或不可逆动作；
- 多语言、多格式、多模态。

测试集应按项目或客户隔离切分，避免同一份资料的相似片段同时进入开发集和 Holdout。

---

## 3. 案例结构

```json
{
  "case_id": "quote_missing_line_001",
  "dataset_version": "quote_eval_v12",
  "input_ref": "encrypted://...",
  "task_type": "quote_preview",
  "risk_level": "high",
  "expected": {
    "required_items": ["A", "B"],
    "must_abstain": false
  },
  "forbidden": {
    "tool_actions": ["push_quote"],
    "sensitive_patterns": ["..."]
  },
  "metadata": {
    "source": "production_failure",
    "project_group": "project_17"
  }
}
```

原始业务文本可放受控存储，评测清单使用引用、哈希和脱敏元数据。

---

## 4. 分层指标

### 模型输出

- Schema 通过率；
- 字段准确率；
- 事实与引用正确率；
- 无答案判断；
- 幻觉率；
- 敏感信息泄露率。

### Agent 任务

任务成功率应定义为：

```text
最终达到业务目标
+ 过程未违反安全规则
+ 副作用正确
+ 在预算和时间内完成
```

不能把“流程没有抛异常”当成任务成功。

### 工具

- 工具选择准确率；
- 参数正确率；
- 调用成功率；
- 越权拦截率；
- 重复副作用数；
- 人工审批绕过数。

### RAG

- Recall@K；
- MRR/NDCG；
- 证据覆盖；
- 引用正确率；
- 无答案率；
- 权限过滤泄漏数。

### 效率

- P50/P95/P99；
- TTFT；
- 输入/输出 Token；
- 每任务和每成功任务成本；
- 重试和 Fallback 次数；
- 人工处理时间。

### 业务

- 报价确认行完整率；
- 金额偏差；
- 人工修改率；
- 打回率；
- 漏项率；
- 用户完成率。

---

## 5. 评测器选择

### 确定性评测优先

适合：

- JSON Schema；
- 数值和金额；
- 必填行；
- 权限；
- 工具参数；
- 引用 ID；
- 延迟和成本；
- 敏感模式。

### 专家评测

适合商业合理性、风险判断和专业表述。需要评分规范、盲评和分歧处理。

### LLM-as-a-Judge

适合规模化评估模糊质量，但必须：

- 固定 Judge 模型和 Prompt；
- 隐藏候选顺序；
- 防长度和风格偏差；
- 用专家样本校准；
- 不让 Judge 接触不该看到的标签；
- 不用它单独裁决越权或金额。

评测器自身也要版本化和回归。

---

## 6. Prompt 版本管理

Prompt 不只是一个字符串，还包括：

```text
模板
+ 变量 Schema
+ Few-shot 示例
+ 输出 Schema
+ 工具描述
+ 模型与参数
+ 上下文组装策略
```

建议状态：

```text
draft → candidate → canary → active → retired
```

每个版本记录：

- 唯一 ID 和内容哈希；
- 创建者、原因、关联 Issue；
- 兼容模型；
- 数据与评测版本；
- 基线和候选结果；
- 审批、发布时间；
- 回滚目标。

生产调用保存版本 ID 和必要的脱敏摘要，不必把全部敏感 Prompt 原文散落到日志。

---

## 7. 自动化回归

触发条件：

- Prompt 变化；
- 模型或参数变化；
- Tool Schema 变化；
- RAG 数据或检索策略变化；
- Workflow/Policy 变化；
- 关键依赖升级；
- 线上新增高风险失败样本。

推荐流水线：

```text
变更
→ 快速确定性测试
→ 离线模型/RAG/Agent 回归
→ 与当前 active 基线比较
→ 生成分层差异报告
→ 硬门槛判定
→ 人工审批
→ 灰度与线上监控
```

为控制成本，可分层：

- PR：小型关键集 + Mock；
- 合并：完整离线集；
- 定时：真实模型大集；
- 发布候选：Holdout + 安全红队；
- 灰度：真实业务指标。

---

## 8. 错误分类

统一错误分类要同时回答三个维度。

### 发生在哪一层

```text
INPUT
AUTH
CONTEXT
RETRIEVAL
MODEL
PARSING
TOOL
WORKFLOW
DATA
DEPENDENCY
POLICY
HUMAN
```

### 能否重试

- retryable：网络抖动、限流、短暂依赖异常；
- non_retryable：权限不足、Schema 永久不兼容、业务规则拒绝；
- manual_required：证据冲突、金额高风险、模型持续失败。

### 影响等级

- info；
- warning；
- error；
- critical。

示例：

```text
MODEL_OUTPUT_SCHEMA_INVALID
RAG_NO_AUTHORIZED_EVIDENCE
TOOL_SCOPE_DENIED
QUOTE_REQUIREMENT_ROW_MISSING
DEPENDENCY_TIMEOUT
HUMAN_REVIEW_EXPIRED
SENSITIVE_DATA_BLOCKED
```

错误码用于：

- 决定是否重试、降级或人工处理；
- 聚合线上问题；
- 自动加入对应回归集；
- 关联 Runbook；
- 衡量版本退化。

错误文本可以变化，错误码和语义应保持稳定。

---

## 9. 发布门

### 硬门槛

任一发生即阻断：

- 越权工具真实执行；
- 敏感信息泄露；
- 高风险审批绕过；
- 关键需求行缺失；
- 金额越界；
- Schema 无法解析；
- Holdout 明显退化。

### 软门槛

允许在预算内权衡：

- 平均表达质量；
- 少量延迟变化；
- Token 成本；
- 低风险人工接管率。

示例：

```text
任务成功率不低于基线
关键完整率 = 100%
越权执行数 = 0
敏感泄露数 = 0
P95 增幅 ≤ 阈值
每成功任务成本增幅 ≤ 阈值
```

阈值应来自业务目标和历史数据，不机械照搬示例数字。

---

## 10. 线上闭环

```text
线上 Trace / 反馈 / 人工修改
→ 错误分类
→ 去重、脱敏和授权
→ 形成 Regression Case
→ 修复与候选版本
→ 自动回归
→ 发布门
→ 灰度
```

注意：

- 用户点赞不等于事实正确；
- 人工修改不一定是正确金标；
- 线上样本可能含隐私和攻击载荷；
- 只有复核、版本化的案例才能进入正式数据集。

---

## 11. 报价中台的真实映射

### 已具备

- `prompt_regression_cases` 保存来源、Prompt/Workflow/RAG 版本、AI 结果、人工结果和金额偏差；
- `prompt_regression_runs` 保存候选与基线版本、案例数、格式错误、漏项、金额和综合指标；
- RAG 评测报告与独立报告目录；
- 报价资料研判 Agent 有 Retrieval Gold、Development/Holdout 思路、检索评测脚本和成对延迟评测；
- Policy 校准存在不安全报价等硬门；
- 大量 pytest 回归覆盖权限、状态、工具、完整性和人工节点；
- 线上报价反馈、修正和 Trace 可形成失败样本；
- 错误码已广泛用于 API、异步任务和 Agent 运行。

### 仍需补强

- 候选 Prompt 自动真实重跑尚未形成完整闭环；
- Prompt、模型、RAG、Tool、Workflow、Policy 尚未统一为单一 Eval Run 清单；
- 不同业务模块的错误码尚未归入统一层级、重试性和严重度；
- 内容安全、隐私泄露和多模态注入测试集仍需单独建立；
- 线上质量、Token 和人工指标尚未全部进入统一发布门；
- 正式生产的自动灰度、自动停止和回滚门尚未落地。

---

## 12. 面试回答模板

### 如何评测 Agent？

> 我把任务成功定义为结果正确、过程合规、副作用正确并在预算内完成。指标分为输出、过程、工具、RAG、安全、效率和业务七层；使用确定性规则、专家和经校准的 LLM Judge；Development 用于迭代，Holdout 做发布判定，高风险越权和泄露作为零容忍硬门。

### Prompt 如何版本管理？

> 版本不仅包含模板，还包含变量、示例、输出 Schema、工具描述、模型参数和上下文组装策略。每个版本有哈希、评测数据、基线差异、审批和回滚目标，按 draft、candidate、canary、active、retired 流转。

### 如何做自动化回归？

> Prompt、模型、RAG、Tool 或 Workflow 变化都触发同一评测清单。CI 先跑确定性测试，再按成本分层运行真实模型集，与 active 基线比较；硬门通过后人工审批和灰度，线上失败按错误码回流回归集。

### 为什么需要错误分类？

> 统一错误码能决定重试、降级还是转人工，并支持线上聚合、告警和自动回归。分类至少包含发生层、可重试性和严重度，不能只保存一段异常文本。

---

## 13. 复习清单

- [ ] 能列出完整运行版本清单
- [ ] 能区分 Development、Holdout、Regression 和 Adversarial
- [ ] 能定义真正的 Agent 任务成功率
- [ ] 能比较确定性评测、专家和 LLM Judge
- [ ] 能设计 Prompt 生命周期和版本字段
- [ ] 能设计分层自动回归流水线
- [ ] 能从层级、重试性和严重度设计错误分类
- [ ] 能区分硬门槛和软指标
- [ ] 能诚实说明项目尚无完整候选 Prompt 自动重跑和生产灰度门

## 延伸阅读

- [Agent 评测体系](./Agent评测体系-事实过程工具效率安全与版本治理.md)
- [LLMOps 全生命周期管理](./LLMOps全生命周期管理.md)
- [Agent 线上故障定位](./Agent线上故障定位与可靠性治理.md)
- [RAG 生产工程闭环](../08-RAG与Embedding/生产级RAG工程闭环-数据治理检索生成评测与反馈.md)
