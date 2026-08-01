---
title: 生产级 Agent 核心机制与工程实践
category: 生产级开发基础
tags:
  - Agent Runtime
  - State
  - Tool Calling
  - ReAct
  - 幂等性
  - 重试与降级
  - Human-in-the-loop
status: 持续更新
---

# 生产级 Agent 核心机制与工程实践

## 核心认知

Agent 开发的重点，不只是让模型表现得更聪明，而是确保：

> 即使模型会犯错，系统仍然能够稳定、可控、可恢复、可追踪地完成业务。

实现这一目标，需要真正理解：

- Agent 与 Workflow 的分工
- Agent Runtime
- 状态持久化
- Tool Calling 契约
- 结构化输出
- Agent Loop
- 幂等性
- 重试、回退与降级
- Human-in-the-loop

---

## 1. Agent 与普通 Workflow 的区别

### 普通 Workflow

普通工作流的执行路径由开发者预先定义：

```text
上传文件
  ↓
OCR
  ↓
提取字段
  ↓
查询数据库
  ↓
生成结果
```

优点：

- 稳定
- 可控
- 容易测试
- 成本和耗时较容易预估

局限：

- 对未知情况的适应能力较弱
- 新的业务分支通常需要修改代码
- 遇到非预期输入时难以动态选择处理方式

### Agent

Agent 会根据目标、当前状态和工具结果动态决定下一步：

```text
用户提交任务
      │
      ▼
判断任务类型
      │
      ▼
是否需要知识？
      ├── 是 → 调用检索工具
      └── 否
      │
      ▼
是否需要计算？
      ├── 是 → 调用计算工具
      └── 否
      │
      ▼
结果是否满足要求？
      ├── 否 → 补充检索 / 重试 / 转人工
      └── 是 → 输出结果
```

### 生产环境的正确组合

大多数生产系统不是在 Agent 和 Workflow 之间二选一，而是：

```text
Workflow 控制主流程和硬规则
             +
Agent 处理局部不确定性
```

适合 Workflow 的部分：

- 文件上传和任务入库
- 权限检查
- 状态迁移
- 人工审核
- 确认下发
- 审计记录

适合 Agent 的部分：

- 内容理解
- 查询意图判断
- 材料候选匹配
- 异常解释
- 在受控工具集合中选择下一步

关键业务规则应由代码、状态机或数据库约束保证，不能只靠 Prompt。

---

## 2. Agent Runtime：Agent 的运行控制中心

Agent Runtime 可以类比为 Agent 的“运行时操作系统”。它连接模型、状态、工具和工作流。

主要职责：

- 构建上下文
- 调用模型
- 解析模型输出
- 调用工具
- 保存任务状态
- 控制循环
- 处理失败
- 判断终止
- 记录事件
- 恢复中断任务

### 最小执行循环

```python
while not task_finished:
    context = build_context()
    decision = llm.invoke(context)

    if decision.type == "tool_call":
        result = execute_tool(decision.tool)
        save_tool_result(result)

    elif decision.type == "final_answer":
        task_finished = True
```

这段代码只能表达基本思想。生产 Runtime 还必须回答：

- 模型超时后是否重试？
- 工具失败后应该重试、回退还是转人工？
- 如何识别重复调用和死循环？
- 服务重启后如何恢复？
- 输出不符合 Schema 怎么办？
- 上下文或 Token 超限怎么裁剪？
- 哪些工具调用必须审批？
- 多个 Worker 如何避免同时执行同一任务？

### 常见实现方式

- LangGraph
- OpenAI Agents SDK
- Semantic Kernel
- AutoGen
- 自研 FastAPI + Queue + Database Runtime

学习框架时，最值得掌握的不是 API，而是以下通用思想：

- 显式状态
- 状态图
- 节点与边
- 检查点
- 中断与恢复
- 有界循环

---

## 3. State：为什么状态必须持久化

假设报价任务已执行到：

```text
1. 图纸已上传
2. OCR 已完成
3. VLM 识别已完成
4. 材料检索进行中
5. 报价尚未生成
```

如果状态只保存在进程内存中，服务器重启后任务就可能丢失。

生产系统需要保存可恢复状态，例如：

```json
{
  "task_id": "quote_20260727_001",
  "status": "retrieving_materials",
  "completed_steps": [
    "file_uploaded",
    "ocr_finished",
    "vlm_finished"
  ],
  "current_step": "material_retrieval",
  "retry_count": 1,
  "version": 7
}
```

### 三类状态

| 状态类型 | 回答的问题 | 示例 |
|---|---|---|
| 业务状态 | 业务任务进行到什么阶段 | 待处理、识别中、待审核、已完成 |
| Agent 状态 | Agent 已采取哪些行动 | 工具调用、观察结果、下一步计划 |
| 系统状态 | 基础设施如何执行任务 | 已入队、Worker 已领取、超时、重试次数 |

三类状态应分开建模，避免用一个 `status` 字段表达所有含义。

### 状态设计建议

- 使用明确的枚举值，而不是自由文本
- 为每次迁移记录事件
- 保存状态版本，防止并发覆盖
- 大型模型输入输出与业务字段分开保存
- 明确可重试、不可重试和终态
- 为恢复任务保存最后一个安全检查点

可以将“当前状态”和“事件历史”结合：

```text
任务表：快速读取当前状态
事件表：追溯状态如何变化
```

---

## 4. Tool Calling 的完整链路

工具调用不是“模型输出一个函数名后直接执行”，而是完整的受控链路：

```text
1. Runtime 向模型描述工具
2. 模型选择工具
3. 模型生成参数
4. 程序进行 Schema 校验
5. 程序进行权限与策略校验
6. Runtime 执行工具
7. 工具返回标准化结果
8. 保存审计与任务事件
9. 模型读取结果
10. 模型决定下一步
```

例如价格查询工具：

```python
def query_material_price(
    material_name: str,
    region: str,
    date: str
) -> dict:
    ...
```

模型生成：

```json
{
  "tool_name": "query_material_price",
  "arguments": {
    "material_name": "Low-E中空玻璃",
    "region": "东莞",
    "date": "2026-07"
  }
}
```

执行前必须检查：

- 材料名称是否为空
- 地区是否在允许范围内
- 日期格式是否合法
- 当前用户是否有查询权限
- 当前 Agent 是否允许调用该工具
- 这次调用是否需要审批
- 是否命中了频率或成本限制

正确链路：

```text
LLM 决策
   ↓
Schema 校验
   ↓
权限与策略校验
   ↓
幂等检查
   ↓
执行工具
   ↓
结果标准化
   ↓
保存事件
   ↓
返回 Agent
```

---

## 5. 工具设计：小而明确，但不要碎片化

不推荐：

```text
工具：process_project
功能：处理整个项目
```

这种工具边界过大，模型难以判断何时调用，也不利于权限、测试和错误恢复。

更清晰的业务能力可以拆为：

```text
extract_drawing_text
identify_material_items
retrieve_material_price
calculate_project_cost
generate_quote_excel
submit_for_review
```

### 三项基本原则

#### 输入明确

避免：

```json
{
  "data": "任意内容"
}
```

推荐：

```json
{
  "project_id": 123,
  "material_name": "铝型材",
  "quantity": 850.5,
  "unit": "kg"
}
```

#### 输出稳定

统一工具信封：

```json
{
  "success": true,
  "data": {},
  "error_code": null,
  "message": "查询成功",
  "retryable": false
}
```

#### 错误可理解

避免：

```text
Error
```

推荐：

```json
{
  "success": false,
  "data": null,
  "error_code": "MATERIAL_NOT_FOUND",
  "message": "未找到东莞地区该材料的有效价格",
  "retryable": false
}
```

Agent 才能据此判断应该：

- 调整查询词
- 补充输入
- 重试
- 使用回退方案
- 转人工

### 粒度判断

工具不是越小越好。过度拆分会增加调用次数、Token 成本和失败点。

合理工具通常满足：

- 完成一个清晰的业务能力
- 输入输出可以稳定定义
- 权限边界一致
- 能独立测试
- 失败后可以单独重试或补偿

---

## 6. 结构化输出与验证

自然语言适合人与人交流，但不适合直接驱动业务系统。

不稳定输出：

```text
该项目可能需要大约 500 平方米玻璃，
单价大概是 420 元。
```

结构化输出：

```json
{
  "material_name": "Low-E中空玻璃",
  "quantity": 500,
  "unit": "m²",
  "unit_price": 420,
  "confidence": 0.86
}
```

结构化输出可以用于：

- 写入数据库
- 调用工具
- 前端展示
- 自动计算
- 规则校验
- 生成报表
- 离线评测

### 使用 Pydantic 验证

```python
from pydantic import BaseModel, Field


class MaterialItem(BaseModel):
    material_name: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1)
    unit_price: float | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)
```

验证失败后，不应直接让异常传播到业务层。Runtime 可以：

1. 记录原始输出与验证错误
2. 在有限次数内要求模型修正
3. 仍失败则进入降级或人工处理

### 业务校验仍然必要

通过 Schema 只代表“格式正确”，不代表“业务正确”。

例如：

- `quantity=999999` 可能格式合法但明显异常
- `unit="kg"` 可能不符合玻璃计价单位
- `confidence=0.99` 不代表真的可靠

还要执行范围、单位、价格和跨字段一致性校验。

---

## 7. Agent Loop：有界的思考—行动循环

Agent 常以循环方式工作：

```text
理解当前状态
      ↓
选择行动
      ↓
调用工具
      ↓
观察结果
      ↓
更新状态
      ↓
决定继续或结束
```

这与 ReAct 的 `Reasoning + Acting` 思想相关。

### 示例

```text
目标：查询玻璃幕墙材料价格

行动 1：检索材料候选
观察：找到 3 种可能材料

行动 2：读取图纸中的玻璃规格
观察：规格为 6+12A+6 Low-E

行动 3：查询地区价格
观察：找到东莞地区有效参考价

结束：返回价格、来源和风险提示
```

在系统日志中，应记录可审计的行动、工具参数、观察结果和状态变化，不必保存或展示模型的隐藏推理过程。

### 必须设置上限

```python
MAX_STEPS = 10
MAX_TOOL_CALLS = 8
MAX_RETRIES = 3
```

### 循环检测

应检查：

- 是否连续调用同一工具
- 参数是否完全相同
- 返回结果是否没有变化
- 任务状态是否长期没有推进
- Token 或执行时间是否接近预算

触发循环保护后，可以：

- 更换策略
- 请求补充信息
- 转人工
- 以可解释的失败状态结束

---

## 8. 幂等性：重复执行不产生重复副作用

幂等性的目标是：

> 同一个逻辑操作执行一次或多次，最终业务结果保持一致。

可能导致重复执行的情况：

- 用户重复点击
- HTTP 客户端重试
- 消息队列重复投递
- Worker 超时后任务重新领取
- 服务重启后的恢复执行

高风险操作包括：

- 创建测算任务
- 上传或解析文件
- 触发 OCR
- 生成报价单
- 推送通知
- 提交审核
- 写入价格结果

### 幂等键

示例：

```text
用户 ID + 项目 ID + 操作类型 + 输入版本
```

```text
user_12:project_35:generate_quote:v3
```

### 不够安全的实现

```python
if not key_exists(key):
    execute()
    save_key(key)
```

并发请求可能同时通过检查，仍然重复执行。

### 更可靠的实现

- 数据库唯一约束
- 原子 `insert ... on conflict`
- Redis `SET NX`
- 事务锁
- Outbox/Inbox 模式

示意：

```python
record = create_idempotency_record_atomically(key)

if record.already_completed:
    return record.existing_result

if record.is_owned_by_another_worker:
    return task_in_progress()

result = execute_operation()
mark_completed(key, result)
return result
```

对于外部消息推送，还需要将外部平台的请求 ID 或返回 ID 保存下来，避免本地重试产生重复通知。

---

## 9. Retry、Fallback 与 Degradation

### Retry：重试

对同一个动作再次执行。

适合临时性故障：

- 网络抖动
- API 限流
- 服务暂时不可用
- 数据库连接瞬时中断

推荐策略：

```text
有限次数
+ 指数退避
+ 随机抖动
+ 明确可重试错误
+ 幂等保护
```

不要对参数错误、权限拒绝、资源不存在等永久性错误盲目重试。

### Fallback：回退

主方案失败后使用另一种方案。

示例：

```text
主模型失败 → 备用模型
向量检索失败 → BM25
实时价格不可用 → 最近有效价格并标记日期
```

回退结果可能与主结果质量不同，必须在输出和审计中标记实际采用的方案。

### Degradation：降级

降低能力或自动化程度，保证核心业务仍可继续。

示例：

```text
完整图纸识别失败
→ 用户手工填写关键字段

自动报价失败
→ 输出材料清单，价格转人工补充
```

### 三者对比

| 机制 | 动作 | 目标 |
|---|---|---|
| Retry | 同一方案再试一次 | 克服临时故障 |
| Fallback | 换另一套方案 | 尽量完成原目标 |
| Degradation | 降低能力或自动化程度 | 保住核心业务可用性 |

---

## 10. Human-in-the-loop：人工参与是控制机制

高风险业务不应追求无条件全自动化。

合理分工：

```text
AI：
识别、整理、检索、推荐、计算、提示风险

人工：
确认、修正、批准、处理例外、承担最终责任
```

### 置信度分流

可以设计类似规则：

```text
高置信度 → 通过基础自动校验
中置信度 → 普通人工复核
低置信度 → 强制高级人工处理
```

但模型自报的 `confidence` 通常没有经过校准，不能直接当作真实概率。

更可靠的风险分流应综合：

- 规则校验结果
- 数据完整性
- 检索依据质量
- 与历史价格的偏差
- 金额大小
- 是否为新材料或新规格
- 模型或工具是否发生回退
- 经评测校准后的置信指标

### 审核台应展示

- AI 原始结果
- 最终建议结果
- 引用依据
- 风险项
- 关键置信指标
- 修改前后差异
- 实际采用的模型、工具和回退路径
- 审核人
- 审核时间
- 审核意见

人工修正记录不仅用于审计，也可以形成后续评测和优化数据。

---

## 11. 实战建议：成本测算审核 Agent

不必另做一个与现有项目无关的 Demo。可以在 AI 智能报价中台内增加一个受控的“成本测算审核 Agent”。

### 目标

```text
读取报价结果
      ↓
检查字段完整性
      ↓
检查数量和单位异常
      ↓
查询历史与成本参考
      ↓
计算价格偏差
      ↓
生成风险清单
      ↓
决定审核等级
      ↓
提交人工审核
```

### 第一版工具

```text
get_quote_items
get_historical_prices
calculate_price_deviation
create_review_report
```

可以额外提供一个只读的依据工具：

```text
get_cost_evidence
```

### 第一版暂不包含

- Multi-Agent
- 自动长期记忆
- 无边界自主规划
- Agent 自主修改成本库
- 自动批准高风险报价

### 建议的状态机

```text
pending
   ↓
loading_quote
   ↓
validating_items
   ↓
retrieving_references
   ↓
calculating_risk
   ↓
generating_report
   ↓
waiting_human_review
   ├── approved
   ├── rejected
   └── revision_required
```

失败状态应单独表示：

```text
failed_retryable
failed_terminal
cancelled
timed_out
```

### 审核结果 Schema

```json
{
  "quote_job_id": "quote_123",
  "risk_level": "high",
  "requires_human_review": true,
  "summary": {
    "total_items": 18,
    "flagged_items": 3
  },
  "issues": [
    {
      "item_id": "item_7",
      "code": "PRICE_DEVIATION_HIGH",
      "severity": "high",
      "message": "当前报价高于有效成本参考 32%",
      "evidence_ids": ["cost_item_95"]
    }
  ]
}
```

### 第一版验收标准

- 能稳定调用受控工具
- 所有输出通过 Schema 和业务规则校验
- 每一步产生任务事件和 Trace
- 临时故障可有限重试
- 重复任务不会产生重复审核报告
- 高风险和不确定结果可以转人工
- 已建立基础回归数据集

### 基础评测指标

- 工具选择正确率
- 工具参数合法率
- 审核风险召回率
- 无风险项目误报率
- 报告 Schema 通过率
- 人工修改率
- 任务完成率
- 平均耗时与成本

---

## 上线前检查清单

### Runtime

- [ ] 循环有最大步数、最大工具次数和时间预算
- [ ] 支持检查点和中断恢复
- [ ] 能识别重复调用与无进展循环

### State

- [ ] 业务、Agent、系统状态分开建模
- [ ] 所有关键状态迁移可审计
- [ ] 并发更新有版本或锁保护

### Tools

- [ ] 工具职责明确
- [ ] 输入输出均有 Schema
- [ ] 权限和审批策略已实现
- [ ] 错误包含稳定错误码和 `retryable`

### Reliability

- [ ] 副作用操作具备原子幂等保护
- [ ] Retry、Fallback、Degradation 路径明确
- [ ] 重试采用有限次数和退避策略

### Human-in-the-loop

- [ ] 高风险动作不能绕过人工审核
- [ ] 审核台展示依据和修改差异
- [ ] 人工决定和意见可追踪

### Evaluation

- [ ] 有固定回归样本
- [ ] 有任务、工具、风险判断和成本指标
- [ ] 模型或 Prompt 变更后自动回归

## 关联笔记

- [Agent 工具与 Skill 路由：分层召回、重排、执行校验与 Workflow 边界](../05-工具调用与MCP/Agent工具与Skill路由-分层召回重排执行校验与Workflow边界.md)
- [生产级 Agent 系统架构](../02-Agent核心架构/生产级Agent系统架构.md)
- [Agent 技术栈全景图与数据流](../02-Agent核心架构/Agent技术栈全景图与数据流.md)
- [AI 落地工程师学习路线](../01-学习路线/AI落地工程师-Agent知识体系与学习路线.md)
