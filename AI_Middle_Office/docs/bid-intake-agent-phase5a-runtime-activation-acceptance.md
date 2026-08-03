# 报价资料研判 Agent Phase 5a：运行态启用与首个闭环验收

## 本阶段结论

Phase 5a 已在当前内网开发环境启用真实运行态，并完成一条可追溯的端到端闭环：

```text
已解析招标资料
  -> 不可变证据文档 / block / manifest
  -> Tender Evidence MCP
  -> 独立 LangGraph Worker
  -> DeepSeek OpenAI-compatible Tool Calling
  -> AssessmentDraft JSON Schema
  -> PolicyEngine
  -> 确定性证据门
  -> Human-in-the-loop
  -> SQL Checkpoint 恢复
  -> waiting_supplement
```

本阶段没有修改报价价格口径、成本库规则、N8N、Dify 或正式报价链路。

## 新增运行编排

- 独立 Agent Python 环境：`.venv-agent`
- 独立启动器：`start_bid_intake_agent.ps1`
- `start_all.ps1` 在 `BID_INTAKE_AGENT_RUNTIME_ENABLED=true` 时自动启动：
  - 数据库模式 Tender Evidence MCP；
  - 独立 LangGraph Worker。
- `-SkipBidIntakeAgent` 可显式跳过 Agent。
- `-Restart` 会重启 MCP 与 Worker 的精确 PID。
- MCP JWT secret 只在启动进程内随机生成并传给两个子进程，不写 `.env`、日志或业务表。
- PID 和运行日志写入 `logs/`。

Agent 依赖与主 FastAPI 环境隔离：

```powershell
python -m venv .venv-agent --system-site-packages
.\.venv-agent\Scripts\python.exe -m pip install -r requirements-agent.in
```

## 模型配置

显式 `BID_INTAKE_MODEL_API_URL / API_KEY / ID` 仍具有最高优先级。

三项全部留空时，Worker 会复用现有：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_CHAT_URL`
- `BIDDING_LLM_MODEL`，其次 `DEEPSEEK_MODEL`

这样不需要在 `.env` 重复保存同一个密钥。Worker 心跳只记录安全摘要：

- `model_configured`
- `model_config_source`
- `model_id`

不记录 API key。

## 真实运行中修复的问题

### 1. 自定义研判目标丢失

持久 Executor 原先没有把 `assessment.analysis_goal` 传入初始 LangGraph State。现在已在控制面到图状态间完整透传。

### 2. Tool 调用预算不可见

模型现在能看到剩余循环与 Tool 预算。Prompt 约束每轮最多三个 Tool，并要求相同 evidence ID 只读取一次。

最终验收运行：

- ReAct 循环：5
- Tool 调用：10
- MCP 上下文读取审计：1

### 3. 模型输出契约不稳定

模型现在获得完整 `AssessmentDraft` JSON Schema，并受以下边界控制：

- 只返回 JSON；
- EvidenceRef 必须完整复制；
- 一次结构化输出修复；
- `unknown` Policy Factor 的来源确定性归一为 `unknown`；
- 其他 Pydantic 契约继续严格拒绝；
- 模型不能自行设置 Runtime 的异常终止原因。

Agent 审计版本更新为：

- graph：`bid_intake_graph_v3`
- state：`bid_intake_state_v3`
- prompt：`bid_intake_prompt_v3`
- runtime：`bid_intake_runtime_phase5a`

### 4. Windows 运维脚本

- `.env` 显式按 UTF-8 读取；
- Worker 与预检输出强制 UTF-8；
- `--config-only` 以数据库 Readiness 和在线 Worker 能力为准；
- PowerShell 启动器支持重复执行和已运行检测。

## 当前环境验收记录

受控项目：

- project UUID：`c96cdbc2-67d8-4a0a-9307-c534c99d6ea8`
- 项目名：`BIZ-4a smoke tender d2c269f9`
- manifest：v1
- active 文档：1
- evidence block：1
- 检索：`database_lexical`

最终保留记录：

- assessment UUID：`61b98bd2-fbbe-474a-8514-fa08e6b5d25c`
- run UUID：`79886686-7459-413f-88b3-0483bfe5f8f9`
- Agent 建议：`need_supplement`
- PolicyEngine：`need_supplement`
- 证据门：`supplement_required`
- 研判维度：10/10
- 政策因素：11/11
- 置信度：0.55
- 人工动作：`supplement_requested`
- 人工命令：`applied`
- 最终 run：`completed`
- 最终 phase：`waiting_supplement`

核心业务结论包括：

- 固定总价包干且漏项不补；
- 无预付款并在竣工审计后付款；
- 45 天工期及每日千分之一违约金；
- 3 日签证索赔窗口；
- 缺工程量清单、图纸、客户、保证金和评标办法等关键资料。

开发期间产生的四条诊断运行及其 Checkpoint、读取审计已经清理，只保留最终验收记录。

## 验证

```text
Agent / Runtime / MCP / Evidence 专项：50 passed
Agent 核心结构化输出专项：9 passed
PowerShell Parser：start_all.ps1 / start_bid_intake_agent.ps1 通过
Preflight --config-only：ok=true
```

已知非功能 warning：

- 项目目录 `.pytest_cache` 无写权限；
- 继承主环境时 `requests` 会报告现有依赖版本 warning。

两者均未影响运行态和测试结果。

## 人工验收步骤

当前 9000 端口 FastAPI 是启用开关前启动的旧进程，需要由原启动终端执行一次：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
.\start_all.ps1 -Restart
```

然后：

1. 打开左侧“报价资料研判 Agent”。
2. 选择 `BIZ-4a smoke tender d2c269f9`。
3. 确认 Runtime、Worker、MCP、模型和政策均为就绪。
4. 查看保留的 assessment。
5. 确认状态为“等待补资料”，并能看到 10 个维度、11 个政策因素、风险、缺失资料、证据门与运行事件。

## 当前边界与下一步

- 混合索引任务仍为 `queued`，当前使用数据库词法检索兜底；
- MCP runtime secret 是进程级，外部新终端不能直接复用它做带 token 预检；
- 当前模型生成完整结构化报告约 1–3 分钟；
- 尚未对真实业务项目做总经办人工校准。

下一阶段建议：

1. 先完成前端人工验收；
2. 再将 CentOS 招标证据混合检索部署启用；
3. 使用 5–10 个历史项目做质量、耗时、Tool 次数和政策一致性校准。
