# 报价资料研判 Agent Phase 5d：实时运行图谱

## 目标

把原有“研判中/等待人工审核”的静态进度，升级为会随 LangGraph 执行逐步展开的 Agent Runtime Graph。界面参考 MiroFish 演示图谱的力导向布局与缩放交互，但展示的是报价研判 Agent 的真实执行结构：

`发起研判 → 准备上下文 → 组装 LLM 输入 → LLM 研判 → 行动计划/工具选择 → Tool 授权门 → Tool → Observation 回传 → 循环判断 → 下一轮 ReAct 或形成草稿 → 总经办标准 → 证据门 → Human-in-the-loop`

## 后端事件合同

LangGraph 继续作为唯一执行状态机。`PersistentBidIntakeExecutor` 使用 `stream_mode="tasks"` 读取节点开始、完成、失败和中断事件，再通过 `BidIntakeExecutionTrace` 投影为 append-only 运行事件：

- `trace_step_started`
- `trace_step_completed`
- `trace_step_failed`
- `trace_step_waiting`

事件 payload 使用 `bid-intake-agent-trace/v1`，核心字段包括：

- `sequence`
- `step_id`
- `parent_step_ids`
- `node_name`
- `kind`
- `title`
- `state`
- `summary`
- `iteration`
- `duration_ms`
- `details`

父节点显式记录，因此工具分支、Observation 回流 ReAct、证据门和人工节点都能还原为图，而不是被压平为时间轴。复用现有 `bid_intake_run_events` 表，不新增 Alembic。

## 可见性与安全边界

前端展示：

- 当前节点在做什么；
- 每轮实际发送给 LLM 的安全输入结构，包括研判目标、资料版本、历史消息/Observation 数量、模型、政策版本、可用工具与运行约束；
- ReAct 回合编号；
- LLM 根据当前信息缺口形成的行动计划、工具选择及循环继续/停止决策；
- Tool 名称与脱敏后的输入摘要；
- Observation 状态、命中数量、证据编号等结果摘要，以及结果已回写 LLM 上下文；
- 策略结论、证据门问题与 Human-in-the-loop 状态；
- 节点耗时和最近活动。

前端不展示或持久化模型私有思维链。密钥、token、password、authorization 等字段会被脱敏；工具 Observation 不保存招标文件的长原文，只保留可审计摘要和证据引用。

## 前端交互

`BidIntakeRunGraph.vue` 使用 Vue 3 + D3 force simulation：

- 新事件出现时自动增加节点和连线；
- 当前运行节点脉冲、活动连线流动；
- 节点核心半径由 26px 收敛为 18px，自动适配只缩小不放大，支持更高流程密度；
- 连线显示“发送 LLM、规划动作、安全校验、调用、结果返回、继续/停止循环”等语义；
- 支持缩放、拖拽、适应画布和自动跟随；
- 点击节点查看输入、结果、耗时和状态；
- 旧研判记录没有 trace 事件时，降级显示运行生命周期回放；
- 研判详情轮询间隔为 1.2 秒。

## 验证

- 后端/持久化/前端契约聚焦回归：`19 passed`
- Vite production build：通过
- Chrome 人工视觉验收：旧运行记录兼容图可见，节点、连线、自动居中、详情面板和最近活动正常
- 当前运行中的旧后端不会产生新版 trace；必须重启 FastAPI 与 Agent Worker，并新发起一次研判，才能验收完整 ReAct/Tool/Observation 动态图

## 人工验收步骤

1. 在 `AI_Middle_Office` 目录重启整套服务。
2. 打开 `/admin/bid-intake-agent` 并选择资料已解析的项目。
3. 点击“发起研判”。
4. 确认图谱节点按执行进度逐步增加，并能看到 LLM 输入、ReAct、行动计划、Tool、Observation、循环判断、策略/证据门和人工节点。
5. 点击任意节点，确认右侧显示可审计摘要，但不出现模型私有思维链或敏感密钥。
6. 提交人工决定，确认图谱从 Checkpoint 恢复并继续增加节点。
