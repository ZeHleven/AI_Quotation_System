# 报价资料研判与立项辅助 Agent Phase 0 原型

## 目标

Phase 0 只验证一条可运行、可暂停、可恢复、可测试的 LangGraph 纵向链路：

```text
资料清单
  -> 受控 ReAct
  -> 只读证据/规则 Tool
  -> AssessmentDraft
  -> 确定性证据门
  -> Human-in-the-loop
  -> 人工决定
```

本阶段不接生产数据库、真实 Milvus、MinIO、Celery、前端或正式模型，也不修改现有报价链路。

## 代码入口

- Agent包：`app/agents/bid_intake/`
- 业务规则说明：`skills/bid-decision-policy/`
- 演示脚本：`scripts/bid_agent_demo.py`
- 聚焦测试：`agent_tests/bid_intake_checks.py`
- 独立依赖：`requirements-agent.in`、`requirements-agent-dev.in`

## 已实现边界

- 使用`StateGraph`和`ToolNode`，没有使用高层预制Agent。
- Tool调用前执行名称白名单、总调用数、相同参数重复次数检查。
- 招标资料Tool不接受`case_id`，项目范围由运行时证据客户端绑定。
- Agent输出必须通过Pydantic结构校验。
- 高风险结论必须有有效证据，并且必须存在读取上下文的服务端轨迹。
- 证据门不使用LLM。
- 人工节点使用`interrupt()`暂停，恢复时校验报告版本和资料清单版本。
- 证据失效、未读取上下文或Agent提前终止时禁止直接批准。

## 本地运行

从`AI_Middle_Office`目录运行：

```powershell
python -m pip install -r requirements-agent-dev.in
python scripts/bid_agent_demo.py --decision approved
python -m pytest agent_tests/bid_intake_checks.py -q
```

演示脚本使用Fake Model和项目隔离的Fake Evidence Client，但Tool Call、LangGraph循环、证据门和HITL均运行真实代码。

## 后续接入顺序

1. 用FastMCP实现真实`Tender Evidence MCP Server`。
2. 用模型网关适配器替换Fake Model。
3. 接入`AsyncPostgresSaver`。
4. 通过Alembic增加业务表，再接FastAPI和Celery。
5. 最后增加Vite人工审批工作台。
