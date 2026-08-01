# 报价回归与“报价侧 RAG 关闭状态”验收记录

日期：2026-07-25

## 1. 验收口径

本次“RAG 关闭状态”不是停止或删除 RAG/Milvus 服务，而是：

- 同步报价 `/api/v1/chat` 不调用 RAG/Milvus；
- 异步报价 `/api/v1/quote/jobs` 不调用 RAG/Milvus；
- 报价继续使用数据库企业定额/成本依据、Dify、人工预审、完整性阻断、确认推送和审计链路；
- RAG 服务、Milvus、同步、评测和健康检查继续保留，供后续企业问答 Agent 使用。

## 2. 验收结论

结论：**通过（PASS）**。

| 验收项 | 结果 |
| --- | --- |
| FastAPI 配置指向 no-RAG Webhook | 通过 |
| N8N no-RAG 工作流已发布 | 通过 |
| 同步真实报价 | 通过 |
| 异步真实报价 | 通过 |
| 报价工作流无 RAG/Milvus 节点 | 通过 |
| 报价核心源码无直接 RAG/Milvus 依赖 | 通过 |
| 报价相关自动化回归 | 通过 |
| RAG 服务保留可用 | 通过 |
| Milvus 端口保留可用 | 通过 |
| 原报价工作流可回滚 | 通过 |

## 3. 环境基线

当前配置：

```text
N8N_WEBHOOK_URL_CALC=http://192.168.88.128:5678/webhook/budget-calc-no-rag
```

健康检查：

- FastAPI：`ready`
- database：`ok`
- Celery broker：`ok`
- Celery worker：`ok`
- worker_count：`1`

no-RAG 工作流：

- workflow id：`jiXOrZ7NZgl2Megd`
- 名称：`【新】1-智能预审流【no-RAG候选】`
- N8N 页面状态：`Published`
- Webhook：`budget-calc-no-rag`

原回滚工作流：

- workflow id：`ryHRy69WhkvelvRQ`
- Webhook：`budget-calc`
- 保留在线，未删除。

## 4. 工作流结构验收

no-RAG 工作流执行图只有 5 个节点：

```text
Webhook
  -> HTTP Request（Dify）
  -> LLMOps_成本监控
  -> Code in JavaScript
  -> Respond to Webhook
```

结构扫描结果：

- node_count：`5`
- `/api/v1/retrieve`：不存在
- `milvus`：不存在
- Dify `/v1/workflows/run`：存在
- Respond to Webhook：存在

N8N 最新运行记录：

| execution id | 状态 | 耗时 | 执行图 |
| --- | --- | ---: | --- |
| 786 | Succeeded | 471 ms | 5 节点，无 RAG |
| 787 | Succeeded | 397 ms | 5 节点，无 RAG |

## 5. 同步报价验收

请求：

`地面找平，工程量20平方米`

结果：

- HTTP：200
- SSE 事件：`processing -> processing -> processing -> preview`
- workflow id：`jiXOrZ7NZgl2Megd`
- project detail：1 行
- total_price：40.00
- warning：`cost_context_fallback_applied`

该需求命中当前 active 企业定额：

- 定额编号：`QS202022`
- 项目：`楼地面找平层压光增加 人工费`
- 单价：2.00/㎡
- 工程量：20㎡
- 参考合计：40.00

链路执行成功。该模糊命中是否符合最终业务口径仍需要人工预审；这是既有成本匹配质量问题，不是移除 RAG 引入的回归。

## 6. 异步报价验收

请求：

`石材地面（拼花），工程量10平方米`

验收任务：

- job number：`BJ-20260725-000326`
- job id：`63405c21-d43a-4311-a8c4-2c927afd1eee`
- 最终状态：`succeeded`
- 最终阶段：`completed`
- workflow id：`jiXOrZ7NZgl2Megd`
- project detail：1 行
- total_price：1011.30
- error：无

异步 Celery 报价已确认使用 no-RAG 工作流。

## 7. 自动化回归

本次回归覆盖：

- 同步 SSE 报价；
- 异步报价任务、状态和事件；
- 企业定额报价来源；
- 成本上下文、成本匹配和漏项检测；
- Excel 需求解析和需求标准化；
- 预审草稿、历史、反馈和 Agent Review；
- confirm_push schema、无底价 draft 和确认推送规则；
- 文件存储和 API 响应契约；
- no-RAG 工作流转换；
- 报价/RAG 依赖边界；
- 保留的 RAG 同步与评测能力。

结果：

`202 passed, 1 warning`

唯一警告是当前工作区不能写入 `.pytest_cache`，不影响测试结论。

## 8. 报价侧 RAG 关闭证据

报价核心文件扫描：

- `app/api/v1/quote.py`
- `app/services/quote_job_runner.py`
- `app/services/quote_cost_context.py`
- `app/services/quote_cost_matching.py`
- `app/services/enterprise_quota_cost_reference.py`

以下依赖均未发现：

- `settings.rag_service_url`
- `/api/v1/retrieve`
- `/admin/reload`
- `pymilvus`
- 旧“RAG & Agent / 穿透企业知识库”报价来源文案

运行态返回的 workflow id 为 `jiXOrZ7NZgl2Megd`，N8N 对应执行图无 RAG 节点。因此报价调用不经过 RAG/Milvus。

## 9. RAG/Milvus 保留验收

独立能力复验：

- `POST http://192.168.88.128:8001/api/v1/retrieve`：HTTP 200
- 返回检索结果：2 条
- 本次独立检索耗时：588.5 ms
- Milvus `192.168.88.128:19530`：TCP 端口可连接

这证明 RAG/Milvus 只是退出报价链路，没有被删除或停用。

## 10. 已知观察项

1. 本次新旧链路对照和最终验收中，Dify 返回空或无效输出，N8N 使用现有数据库成本依据生成预审草稿，标记 `cost_context_fallback_applied`。旧链路和 no-RAG 链路表现一致，不属于本次解耦回归；但建议后续单独检查 Dify 输出质量。
2. “地面找平”样例模糊命中人工费定额项，技术链路正确，但最终价格业务合理性仍需人工预审；建议另列成本匹配质量优化，不与 RAG 解耦混在一起。
3. 报价进度源码已改为“成本库 & Agent”，当前同步 FastAPI 进程会在下一次管理员重启后加载新文案。该展示项不影响 no-RAG 路由和报价结果。

## 11. 回滚

如 no-RAG 链路出现真实异常：

1. 把 `.env` 中 `N8N_WEBHOOK_URL_CALC` 改回 `http://192.168.88.128:5678/webhook/budget-calc`；
2. 在管理员 PowerShell 中运行 `restart_local_services.ps1`；
3. 验证 `/health/ready`；
4. 执行固定同步和异步报价样例。

回滚不涉及数据库恢复、RAG 数据删除或 Milvus 重建。
