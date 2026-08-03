# 报价资料研判 Agent 自适应 Tool 预算

## 目标

解决模型在每轮 ReAct 中持续吃满“最多 3 个 Tool”静态上限的问题，减少相近检索、无效上下文读取和不必要的模型往返，同时保留证据核验能力。

## 调用节奏

| 阶段 | 本轮上限 | 主要动作 |
|---|---:|---|
| `initial_search` | 1 | 用一个覆盖核心维度的宽 Query 完成主检索 |
| `evidence_read` | 2 | 从检索候选中读取最多两条关键证据上下文 |
| `gap_check` | 1 | 只有存在明确证据缺口时执行一次定向补查 |
| `targeted_followup` | 1 | 版本、规则或修复阶段只执行一个必要动作 |

`BID_INTAKE_MAX_TOOL_CALLS_PER_TURN` 仍是绝对上限，自适应预算只能在该上限以内收紧，不能放宽总预算。总调用预算、相同参数重复预算和 Tool 白名单继续生效。

## 确定性约束

- 首轮如模型同时请求多个 Tool，优先保留 `search_tender_evidence`，只执行一个。
- 检索 Observation 返回后，如模型同时请求读取和新检索，优先保留最多两个 `read_evidence_context`。
- 读取 Observation 返回后，本轮无论模型请求多少 Tool，最多保留一个。
- Runtime 不替模型编造 Tool 调用，只对模型已请求的调用做阶段化选择和裁剪。

## 图谱审计

ReAct、行动计划和工具授权节点会记录：

- 当前动态预算阶段和中文名称；
- 本轮 Tool 上限及限制原因；
- 模型原始请求数、实际保留数、裁剪数；
- 当前阶段优先 Tool。

这些内容是 Runtime 的确定性决策摘要，不是模型私有思维链。

## 验证

- Agent、持久化、MCP、运行图谱和前端契约联合回归：`67 passed`。
- Query Planner / 自适应检索路由回归：`9 passed`。
- `compileall` 与 `git diff --check` 通过。

本功能不新增 Alembic，不改变 MCP Tool 对外契约、证据门、总经办立项标准或人工审核流程。
