# 报价链路 RAG/Milvus 解耦：第二阶段实施记录

日期：2026-07-25

## 1. 阶段目标

在不改变报价主要功能、价格来源、人工预审、完整性阻断、确认推送和审计规则的前提下：

1. 从 N8N 报价运行链路中移除 RAG 检索节点；
2. 保持 FastAPI -> N8N、N8N -> FastAPI 以及 Dify 输入契约兼容；
3. 保留原 RAG 服务、Milvus、同步、评测和健康检查能力；
4. 建立独立候选入口和明确回滚通道，为后续企业问答 Agent 保留基础设施；
5. 先灰度切换异步报价，再完成 FastAPI 同步入口重载。

## 2. 当前线上工作流取证

2026-07-25 从当前 N8N 实例导出的正式工作流：

- 名称：`【新】1-智能预审流`
- workflow id：`ryHRy69WhkvelvRQ`
- Webhook：`budget-calc`
- 导出文件 SHA256：`056f0a895597c5625ded04303de88969d33b9a7e4ce0031f0a75cb19951e341e`
- 当前节点数：6
- 当前节点：
  - `Webhook`
  - `调用_RAG_微服务`
  - `HTTP Request`（Dify）
  - `LLMOps_成本监控`
  - `Code in JavaScript`
  - `Respond to Webhook`

当前线上版本与 2026-04-21 仓库备份存在差异：线上版本没有独立 `HMAC Verify` 节点，Webhook 节点自身配置了请求头认证。未签名测试请求返回 HTTP 403，说明认证仍有效。

原始导出和部署导出可能继承敏感连接配置，只保存在 git 忽略的 `AI_Middle_Office/output/n8n_quote_no_rag_phase2/`，不得提交或对外分享。

## 3. no-RAG 候选工作流

已新增确定性转换工具：

- `app/services/n8n_quote_workflow_transform.py`
- `scripts/build_no_rag_n8n_workflow.py`
- `tests/test_n8n_quote_no_rag_transform_phase2.py`

转换规则：

1. 要求源工作流恰好存在一个 `/api/v1/retrieve` 节点；
2. 要求恰好存在一个携带 `strict_pricing_json` 的 Dify `/v1/workflows/run` 节点；
3. 删除 RAG 节点并把其前驱直接连接到 Dify；
4. 保留 `strict_pricing_json` 字段，但使用 `JSON.stringify([])` 作为兼容值；
5. 保持 Dify、LLMOps、解析节点和 Webhook 响应结构不变；
6. 改用独立 Webhook path `budget-calc-no-rag`；
7. 去除服务端 id/version/shared/webhookId，默认生成 inactive 候选；
8. 校验所有连接目标、Dify 节点和 Respond to Webhook 节点仍完整存在。

生成候选 SHA256：

`40b307e71607c22de419d40400b9dad3cc01e6b7c0d8639c65ba3742a9cef7d3`

部署后从 N8N 再次导出的候选 SHA256：

`6284a4fd398d989cc1c1408b6b9b6a3489aab897e5da6fb066b342a4a434f32b`

N8N 候选信息：

- 名称：`【新】1-智能预审流【no-RAG候选】`
- workflow id：`jiXOrZ7NZgl2Megd`
- Webhook：`budget-calc-no-rag`
- 状态：已发布
- 节点数：5

实际拓扑：

```text
Webhook
  -> HTTP Request（Dify）
  -> LLMOps_成本监控
  -> Code in JavaScript
  -> Respond to Webhook
```

候选工作流中没有 RAG 节点、`/api/v1/retrieve` URL 或 Milvus 调用。

## 4. 真实请求对照结果

测试需求：

`石材地面（拼花），工程量10平方米`

FastAPI 数据库成本上下文命中数：`1`。

### 4.1 候选 Test URL

- HTTP：200
- project detail：1 行
- 单价：101.13
- 合计：1011.30
- workflow id：`jiXOrZ7NZgl2Megd`
- execution id：`781`

### 4.2 原正式链路

- HTTP：200
- project detail：1 行
- 单价：101.13
- 合计：1011.30
- workflow id：`ryHRy69WhkvelvRQ`
- execution id：`782`

### 4.3 候选 Production URL

- HTTP：200
- project detail：1 行
- 单价：101.13
- 合计：1011.30
- workflow id：`jiXOrZ7NZgl2Megd`

三次请求的业务结果一致。Dify 本轮都返回空或无效结果，N8N 使用现有的数据库前置成本参考生成预审草稿，`workflow_warning.type=cost_context_fallback_applied`。这不是 no-RAG 改造新增的差异，旧链路和候选链路表现一致。

### 4.4 FastAPI / Celery 最终运行态验收

管理员重启完成后，使用真实认证请求执行最终验收：

同步 `/api/v1/chat`：

- HTTP：200，`text/event-stream`
- 最终事件：`preview`
- workflow id：`jiXOrZ7NZgl2Megd`
- project detail：1 行
- 合计：1011.30

异步 `/api/v1/quote/jobs`：

- 验收任务：`BJ-20260725-000325`
- job id：`d8df5aa1-1dec-4812-b863-6df7a51aa65e`
- 最终状态：`succeeded / completed`
- workflow id：`jiXOrZ7NZgl2Megd`
- project detail：1 行
- 合计：1011.30

同步和异步报价均已确认命中无 RAG 节点的候选工作流。

## 5. 应用配置与运行状态

已把以下配置改为：

`N8N_WEBHOOK_URL_CALC=http://192.168.88.128:5678/webhook/budget-calc-no-rag`

修改位置：

- `.env`
- `.env.example`
- `app/core/config.py` 默认值

2026-07-25 当前运行状态：

| 范围 | 状态 |
| --- | --- |
| no-RAG N8N production endpoint | 已发布并通过签名请求 |
| Celery 异步报价 Worker | 已重启，加载 no-RAG 配置 |
| FastAPI 同步 `/chat` 运行进程 | 已重启，加载 no-RAG 配置 |
| 旧 `budget-calc` 工作流 | 仍在线，可回滚 |
| RAG/Milvus 服务 | 保留，未停止、未删除 |

FastAPI 由 Windows SYSTEM 级计划任务托管。本阶段第一次普通重启无法结束旧 PID 4088；随后由用户在管理员环境完成重启，新 FastAPI PID 为 33444，Celery worker 也完成重载。

报价运行进度文案已从“RAG & Agent / 企业知识库”改为“成本库 & Agent / 企业成本依据”，并增加防回归测试。该纯文案更新发生在最终功能验收之后；第二次 UAC 重启被取消，因此同步 `/chat` 进程会在下一次正常管理员重启后显示新文案，不影响已经生效的 no-RAG 调用路径和报价结果。

## 6. 自动化回归

本阶段专项和报价主链回归：

- no-RAG 转换及默认入口保护；
- 报价/RAG 直接依赖边界；
- 同步 SSE 报价；
- 异步报价任务；
- 成本上下文；
- 成本匹配和底价兜底；
- confirm_push schema 与无底价 draft；
- Webhook 签名与报价辅助函数。

最终执行结果为：

`101 passed, 1 warning`

警告仅为当前工作区不能写入 `.pytest_cache`，不影响测试结论。

## 7. 保留能力和边界

本阶段没有删除或停止：

- RAG 服务；
- Milvus；
- 企业定额/成本数据到 RAG 的管理员同步；
- RAG 评测；
- RAG 健康检查和运维监控；
- 历史 RAG trace / version 审计字段。

切换后复验结果：

- `GET http://192.168.88.128:8001/openapi.json`：HTTP 200；
- `POST http://192.168.88.128:8001/api/v1/retrieve`：HTTP 200，仍可返回检索结果；
- FastAPI `/health/ready`：HTTP 200，database / broker / worker 均为 `ok`，worker_count=1。

报价价格权威来源仍是 FastAPI 中的数据库成本参考与后置证据链。RAG/Milvus 将作为未来企业问答 Agent 的独立基础设施继续保留；企业问答 Agent 应使用独立集合、文档 chunk 模型、引用信息和 ACL，不应直接复用报价集合的数据契约。

## 8. 回滚方案

如果 no-RAG 链路出现真实业务异常：

1. 把 `.env` 中 `N8N_WEBHOOK_URL_CALC` 改回：

   `http://192.168.88.128:5678/webhook/budget-calc`

2. 在管理员 PowerShell 中运行 `restart_local_services.ps1`；
3. 验证 `/health/ready`；
4. 用固定样例执行同步和异步报价；
5. 保留 no-RAG 工作流用于问题排查，不删除任何 RAG/Milvus 数据。

回滚只切换报价 Webhook，不需要恢复数据库、不需要重建 Milvus，也不影响后续企业问答 Agent 的准备工作。

## 9. 第二阶段结论

第二阶段核心目标已完成：

- 当前线上工作流导出、哈希和实际拓扑核对；
- no-RAG 转换器、脚本和保护测试；
- 独立候选部署；
- Test URL、旧正式入口、候选正式入口三方真实请求对照；
- no-RAG 工作流发布；
- 应用配置切换；
- FastAPI 同步报价切换；
- Celery 异步报价切换；
- 同步与异步运行态 workflow id 验收；
- 误导性 RAG 报价进度文案源码清理与防回归保护；
- RAG/Milvus 保留。

当前仅剩纯展示层的新进度文案等待下一次管理员重启后加载，不影响报价功能、no-RAG 路由或本阶段架构结论。

后续已完成正式报价回归与“报价侧 RAG 关闭状态”验收，结论为通过，详见：

`docs/quote-rag-off-acceptance-20260725.md`
