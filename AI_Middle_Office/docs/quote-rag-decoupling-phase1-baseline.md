# 报价链路与 RAG/Milvus 解耦：第一阶段基线报告

日期：2026-07-25

## 1. 阶段目标

本阶段只做现状取证、回归基线和边界保护，不修改线上报价逻辑、N8N/Dify 工作流、价格口径、RAG/Milvus 数据或部署配置。

目标是回答三个问题：

1. 报价链路当前在哪些位置直接或间接使用 RAG/Milvus。
2. 在不影响报价主要功能的前提下，RAG/Milvus 能否退出报价运行链路。
3. 保留下来的 RAG/Milvus 是否可以直接用于后续企业问答 Agent。

## 2. 第一阶段结论

结论：**可以让 RAG/Milvus 退出报价运行链路，同时保留 RAG/Milvus 服务；但不能直接删除 N8N 的 RAG 节点，必须先保持 Dify 输入契约和 N8N 响应结构兼容。**

当前报价的权威成本依据已经在 FastAPI 内通过数据库进行确定性匹配：

- 优先读取唯一 active 企业定额版本 `enterprise_quota.active`。
- 当前 active 企业定额为 `qs-enterprise-quota-20260626-v1`，共 `474` 个主项。
- 当前 `cost_items.active` 为 `0`；只有不存在 active 企业定额时，代码才会回退读取 `cost_items.active`。
- 同步和异步报价都会在调用 N8N 前追加成本参考上下文，并在 N8N 返回后再次进行数据库成本证据匹配、兜底和审计。
- FastAPI 报价入口未直接访问 `RAG_SERVICE_URL`、`/api/v1/retrieve`、`/admin/reload` 或 `pymilvus`。

因此，RAG/Milvus 在报价中不是权威价格源，而是旧 N8N 工作流中的一层间接检索增强。它与 FastAPI 已经提供的数据库成本上下文存在功能重复，也可能给 Dify 提供第二套不完全一致的价格候选。

## 3. 当前报价调用链

```text
文本 / Excel / 图片
  -> FastAPI 输入解析与标准化
  -> 数据库成本匹配
       1. enterprise_quota.active（当前 474 项）
       2. 无 active 企业定额时才回退 cost_items.active
  -> 把确定性成本参考追加到 customer_requirement
  -> N8N budget-calc
       -> 旧工作流：RAG /api/v1/retrieve
       -> Dify（customer_requirement + strict_pricing_json）
  -> FastAPI 对 AI 返回结果再次匹配成本证据、处理底价兜底和漏项
  -> 人工预审
  -> confirm_push / budget-push
```

其中，FastAPI 到 N8N 的直接调用点为：

- 同步报价：`app/api/v1/quote.py`
- 异步报价：`app/services/quote_job_runner.py`

报价成本依据相关服务为：

- `app/services/quote_cost_context.py`
- `app/services/quote_cost_matching.py`
- `app/services/enterprise_quota_cost_reference.py`

这些报价文件当前均无直接 RAG/Milvus 调用。

## 4. RAG/Milvus 当前发挥作用的位置

### 4.1 报价运行链路中的间接作用

仓库中的 2026-04-21 N8N 备份显示，active 工作流 `【新】1-智能预审流`（`budget-calc`，版本 `c46b728a-a227-40d8-91cb-20b3c84d68a6`）采用以下节点顺序：

```text
Webhook
  -> HMAC Verify
  -> 调用_RAG_微服务
  -> HTTP Request（Dify）
  -> LLMOps_成本监控
  -> Code in JavaScript
  -> Respond to Webhook
```

旧工作流将 Webhook 文本以 `top_k=5` 调用 `/api/v1/retrieve`，随后给 Dify 传入：

- `customer_requirement`
- `strict_pricing_json`，值来自 RAG 节点输出的 `data`

这证明历史工作流的 RAG 位于 N8N 内部，而不在 FastAPI 报价代码中。

当前 N8N 服务 `/healthz` 返回 200，但浏览器没有 N8N 登录态，因此尚未直接导出并核对当前线上 active 版本。**2026-04-21 备份只能作为历史强证据，第二阶段实施前必须以当前线上导出为准。**

### 4.2 报价运行链路之外的保留作用

RAG/Milvus 目前还用于：

- 管理员把 active 企业定额同步到 RAG：`POST /admin/reload`
- RAG 检索回归评测：`POST /api/v1/retrieve`
- 系统健康和运维监控
- 报价反馈、Prompt 回归中的 RAG 版本和检索痕迹记录
- 后续企业问答 Agent 的基础设施预留

这些能力不需要随报价解耦而删除。

## 5. 运行态基线

### 5.1 服务状态

2026-07-25 只读检查结果：

- FastAPI `/health/ready`：`ready`
- 数据库：`ok`
- 任务队列：`celery`
- Redis broker：`ok`
- Celery worker：`ok`，`worker_count=1`
- N8N `/healthz`：HTTP 200
- RAG `/docs`、`/openapi.json`：HTTP 200
- RAG OpenAPI 当前只提供：
  - `POST /api/v1/retrieve`
  - `POST /admin/reload`

FastAPI 的 ready 配置当前没有探测外部依赖，返回 `external_dependencies.enabled=false`，所以 ready 状态本身不能证明 N8N/RAG 全链路可用；本报告另外做了独立探测。

### 5.2 报价任务历史

当前库中共有 `323` 个报价任务：

| 状态 | 数量 |
| --- | ---: |
| succeeded | 284 |
| failed | 33 |
| timed_out | 4 |
| canceled | 2 |

历史成功率约为 `87.9%`（284/323）。失败或超时阶段中：

| 阶段 | 数量 |
| --- | ---: |
| n8n | 19 |
| vision | 9 |
| timeout | 4 |
| validation | 3 |
| crashed | 1 |
| 未记录 | 1 |

这些历史数据只能作为切换前对比基线，不能据此断定 19 个 N8N 阶段失败由 RAG 引起。

### 5.3 RAG 保留能力

最近一次 RAG 同步记录：

- run id：`10`
- source：`enterprise_quota.active`
- status：`success`
- requested/synced：`1671/1671`
- HTTP：`200`
- duration：`509000 ms`
- 完成时间：2026-06-29

2026-07-25 对 `/api/v1/retrieve` 的只读查询返回 HTTP 200，证明当前 RAG 服务和 Milvus 检索通道可用。

## 6. 回归测试基线

本阶段执行结果：

1. 报价主链回归：
   - 同步 SSE 报价
   - 异步报价任务
   - 成本上下文
   - 成本匹配与底价兜底
   - 确认推送及阻断规则
   - 报价请求标准化
   - 结果：`87 passed`
2. 新增报价/RAG 直接依赖边界测试：
   - 结果：`5 passed`
3. RAG 评测、企业定额 RAG 同步和健康接口回归：
   - 结果：`16 passed`

合计：`108 passed`。

唯一警告为当前工作区无法写入 `.pytest_cache`，不影响测试结论。

新增保护测试：

- `tests/test_quote_rag_boundary_phase1.py`

该测试只禁止报价主链重新引入直接 RAG/Milvus 依赖，不限制独立 RAG 服务、同步管理、评测或未来企业问答 Agent 使用 RAG/Milvus。

## 7. 发现的冗余与风险

### 7.1 报价侧冗余

1. FastAPI 已把数据库成本参考追加到 `customer_requirement`，旧 N8N 又用相同需求检索报价 RAG，再把第二套候选作为 `strict_pricing_json` 交给 Dify。
2. FastAPI 在 AI 返回后还会再次进行数据库成本匹配和底价兜底。
3. 前端/事件消息仍显示“RAG & Agent”，但 FastAPI 实际执行的是数据库成本匹配；这会误导运维判断。
4. 报价对 RAG 的真正依赖隐藏在 N8N 内，FastAPI 的代码扫描和单元测试无法单独证明全链路已解耦。

### 7.2 直接删除 RAG 节点的风险

旧 Dify 请求仍包含 `strict_pricing_json`。如果只删除 N8N 的 RAG 节点：

- `$json.data` 的来源会改变；
- Dify 输入可能为空、缺失或结构不兼容；
- 后续 Code 节点和 Webhook 响应结构可能改变；
- 同步与异步报价都可能出现解析失败或空预审。

因此第二阶段必须通过兼容值、旁路节点或克隆工作流完成灰度切换，不能直接修改 active 工作流后立即上线。

### 7.3 RAG 用于企业问答前的结构性问题

当前 RAG 服务可以保留，但不能原样作为企业问答 Agent 的知识库层：

1. `hybrid_searcher.py` 将集合名硬编码为 `enterprise_quotation_rag`，没有真正使用可配置集合名。
2. 蓝绿集合名固定为 `quotation_blue` / `quotation_green`，与未来企业知识库命名和隔离要求冲突。
3. `/api/v1/retrieve` 的 `top_k` 只限制每个向量/BM25 子通道，最终结果固定最多取 15 条；实测 `top_k=3` 返回 5 条，API 语义不稳定。
4. 返回结构只有项目名、单价和单位，没有正文片段、相似度、来源文件、页码、版本或引用信息，无法支撑可追溯企业问答。
5. `/admin/reload` 热更新 Milvus 和内存 BM25，但 `rag_materials.json` 以只读方式挂载且没有持久化更新；服务重启后 BM25 可能恢复为旧文件数据，与 Milvus 不一致。
6. 当前文档模型固定为报价物料字段，不具备企业文档的 chunk、ACL、部门范围、租户、版本和失效控制。

后续企业问答 Agent 应复用 Milvus 基础设施，但使用独立集合、独立文档模型、独立检索 API 和独立权限边界，不应复用 `enterprise_quotation_rag` 作为企业文档集合。

## 8. 第二阶段准入条件

进入报价解耦实施前，应全部满足：

1. 登录当前 N8N，导出 active `budget-calc` 工作流并记录 workflow/version id 与文件哈希。
2. 核对当前 Dify 工作流的输入定义，确认 `strict_pricing_json` 是否必填、空数组是否兼容、Prompt 是否强依赖该字段。
3. 克隆或新增可回滚的 no-RAG 报价工作流/入口，不直接覆盖唯一 active 版本。
4. 保持 FastAPI -> N8N 请求结构和 N8N -> FastAPI 响应结构不变。
5. 保持 `enterprise_quota.active` 数据库成本上下文、后置成本证据、兜底、漏项、人工预审、完整性阻断和推送规则不变。
6. 验证报价期间 RAG `/api/v1/retrieve` 没有被调用，同时 `/admin/reload`、RAG 评测和独立检索仍可用。
7. 用固定样例对比切换前后：
   - 报价行数和需求行完整性
   - 成本参考来源及命中
   - AI 返回结构
   - 人工预审和确认推送
   - 总价及单价差异
   - 执行耗时和失败率
8. 保留一键回滚到旧 N8N workflow/version 的能力。

## 9. 第一阶段交付边界

本阶段已完成：

- 报价与 RAG/Milvus 直接、间接依赖盘点
- 运行态健康和历史任务基线
- 报价主链与 RAG 保留能力回归
- 报价主链直接依赖保护测试
- 第二阶段准入条件和风险清单

本阶段未执行：

- 未修改 N8N 或 Dify
- 未切换报价 Webhook
- 未停止或删除 RAG/Milvus
- 未触发 RAG 全量同步
- 未调用真实报价工作流或消耗模型额度
- 未修改报价价格、兜底、审计、推送或人工预审规则
