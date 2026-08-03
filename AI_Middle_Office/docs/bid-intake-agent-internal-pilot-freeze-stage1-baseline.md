# 报价资料研判 Agent 内部试运行冻结包：阶段1基线

> 快照ID：`BID-INTAKE-INTERNAL-PILOT-STAGE1-BASELINE-20260731-V1`
>
> 采集时间：2026-07-31 15:13:52 +08:00
>
> 状态：`stage1_baseline_captured_pending_stage2_database_decision`

> 后续状态：数据库已推进并核验到`20260801_0081 (head)`；当前结论见
> `docs/bid-intake-agent-internal-pilot-freeze-stage2-database-closeout.md`。

## 1. 本阶段结论

阶段1只完成依赖盘点、运行基线采集、版本边界划分和风险登记。

当前可以冻结 Agent 的架构方向，并停止继续优化候选召回；但当前工作区还
不能直接形成正式发布版本，主要原因是：

1. Agent 主体、MCP、迁移、测试和策略目录大多尚未纳入 Git；
2. `app/main.py`、`app/models/bidding.py`、`start_all.ps1`、
   `ai-web/src/App.vue`等共享文件同时包含其他业务改动，不能整体作为
   Agent 专属改动提交；
3. 当前数据库为`20260731_0077`，代码迁移头为
   `20260731_0078`，而0078包含删除旧执行系统的操作；
4. 新证据充分度评估仍关闭，事实门仍为`shadow`，固定Development的
   三道无答案题在普通检索下仍为`0/3`；
5. 本阶段只执行了配置预检，真实MCP和模型探针均明确跳过。

因此，本快照不是最终发布冻结，也不授权重启、数据库迁移、门禁启用或
正式试运行。

## 2. 运行架构基线

```text
Vite 管理工作台
  -> FastAPI 项目/资料/研判控制面
  -> MinIO 原件与解析正文 + MySQL Evidence/Manifest
  -> Celery 解析与索引任务
  -> Milvus向量 + BM25 + RRF混合检索
  -> 项目作用域JWT保护的Tender Evidence MCP
  -> 专用Bid Intake Worker
  -> LangGraph ReAct
       prepare
       -> react_model
       -> authorize_tools
       -> tool_executor
       -> update_fact_coverage
       -> finalize/repair
       -> PolicyEngine
       -> evidence_gate
       -> Human-in-the-loop
  -> SQLAlchemy Checkpoint、租约、运行事件和人工决定
```

当前Agent运行版本为`bid_intake_runtime_phase5a`，模型协议为
`openai_compatible`，模型为`deepseek-v4-flash`，活动政策为
`qs_bid_decision_policy_2026_01`。

## 3. 发布依赖范围

### 3.1 Agent专属主体

| 分组 | 范围 | 文件数 | 当前树摘要SHA-256 |
|---|---|---:|---|
| 核心Runtime | `app/agents/bid_intake/` | 22 | `78002e672cf4d15e2c6a1f42260cfa977d7ecc2a2ae06d569ac736b4ee5a03a7` |
| MCP与检索 | `mcp_servers/tender_evidence/` | 14 | `1dd0c2b0fb5195b55f71eb45ce91f4d838f59dc611e3880d3f24299caa9a61e8` |
| 后端管线 | Agent API、Evidence API、模型、存储、解析、索引、Runtime服务 | 20 | `a012d0a26fff19724b1f113b8abb1af7d06767c31685502b3014d827d940006c` |
| 决策政策Skill | `skills/bid-decision-policy/` | 11 | `805fb392607f5281ab6003167108ea0cd05210b28120692c5ed675c8fbc83e9f` |
| 运维入口 | Agent/MCP脚本、启动器和Agent依赖清单 | 16 | `651825780e174550f3d0597c80122a3d0e4836217bfe1d486e7eb5ba640c8209` |
| 前端 | Agent API、工作台、详情、运行图和共享入口 | 5 | `506e27e67dc9b58c07f7d9d2f298c4e1be7be6ddf395c04f09812ff385c9f649` |
| Agent迁移 | `0063`至`0072` | 10 | `044aab60d92347aa54e14bd4b60c73a9f4cd291f61040199017b32e49a50cfc7` |
| 聚焦测试 | `agent_tests`及Agent/Evidence pytest | 19 | `2cbb0e9384e203b12f7070e6b28da7457268f63e7df6f0812761d5f2b03937dd` |
| 写入阶段1产物前的治理与评测记录 | Agent开发文档与`evals/bid_intake/retrieval/v1` | 112 | `7984a52d5066a95c7228f4cdf5a1cd36202ed28510d784646c9d548af6178bdb` |

摘要算法为：按相对路径排序，对每个文件计算SHA-256，再对
`相对路径<TAB>文件SHA-256`组成的UTF-8清单计算SHA-256。摘要只描述
本次快照，不表示这些文件已经进入Git。

治理与评测摘要特意记录阶段1文档写入前的状态，避免让快照文件对自身
形成循环哈希；本阶段新增文档和台账追加内容不包含在该摘要中。

### 3.2 共享集成文件

以下文件不能在后续阶段直接整体归入Agent提交，必须先做差异拆分和
依赖闭包验证：

- `app/main.py`：包含Agent/Evidence路由，同时包含执行系统移除、
  企业定额、Pricing Agent等其他改动；
- `app/models/bidding.py`：Agent需要`BidProjectFile`解析正文存储字段，
  但同文件还包含商务报价导入等其他模型；
- `app/models/registry.py`：共享模型注册入口；
- `app/core/config.py`：包含Agent配置，同时包含其他功能配置；
- `app/tasks/celery_app.py`：包含Evidence任务，同时为全局Celery入口；
- `start_all.ps1`：包含Agent启动，同时负责数据库自动升级和其他服务；
- `.env.example`与`requirements.txt`：共享部署配置及依赖；
- `ai-web/src/App.vue`、`package.json`、`package-lock.json`：包含Agent
  页面入口和D3依赖，同时混有大量其他前端变更。

### 3.3 平台前置依赖

Agent运行还依赖以下已经存在的共享平台能力，后续发布清单必须绑定其
明确版本，不能只提交Agent目录：

- `BidProject`、`BidProjectFile`及用户/RBAC；
- FastAPI数据库、统一响应和鉴权依赖；
- MinIO `FileObject`与招标原件存储；
- Celery/Redis任务队列；
- `bid_projects`、`bid_project_files`、`file_objects`、`users`等前置表；
- 招投标MVP的`0037`及其后续迁移链；
- Vite公共壳、认证和项目选择能力。

## 4. Git与可复现性基线

采集时仓库状态：

| 项目 | 值 |
|---|---|
| 分支 | `codex/github-ci-and-protection` |
| HEAD | `ac8f0b6ca57664640a0f50816df70d315321d07a` |
| HEAD时间 | `2026-07-22T10:52:22+08:00` |
| 工作树总变化 | 267 |
| 已暂存 | 0 |
| 修改 | 56 |
| 未跟踪 | 201 |
| 删除 | 10 |

Agent核心目录、MCP、0063—0072迁移、政策Skill和大部分测试均为未跟踪；
`start_all.ps1`和`App.vue`为已修改共享文件。后续阶段必须先构造精确
文件清单和共享文件补丁，不能使用“全部暂存”或提交整个工作树。

## 5. 配置基线

只记录非密钥配置，未读取或写入任何密钥值：

```text
TASK_QUEUE_MODE=celery
PUBLIC_ACCESS_ENABLED=false
FEATURE_BIDDING_MVP=true
BID_INTAKE_AGENT_RUNTIME_ENABLED=true
BID_INTAKE_FACT_COVERAGE_MODE=shadow
TENDER_EVIDENCE_CANDIDATE_COVERAGE_SELECTION=false
TENDER_EVIDENCE_SUFFICIENCY_ASSESSMENT=false
```

`.env`中`PUBLIC_ACCESS_ENABLED=false`出现两次，当前值一致，不影响
行为，但后续生成脱敏配置基线时应消除重复定义。

活动政策：

| 项目 | 值 |
|---|---|
| 版本 | `qs_bid_decision_policy_2026_01` |
| 规则SHA-256 | `acc9a8579630b16b558d133e151cb13c9ca0a701bb774b4c392ef79898c0d3b5` |
| `active_version.txt` SHA-256 | `417bdaaa83b51dbfb161705027b0b6dbe5e12ec7bd0c705583349f2c441a7fa7` |
| 政策因子 | 11 |

## 6. 运行状态基线

### 6.1 主服务

- `/health/ready`：`ready`
- 数据库：`ok`
- 任务队列：`celery`
- Redis broker：`ok`
- Celery Worker：`ok`
- Celery Worker数量：1
- 通用外部依赖探针：`not_probed`

### 6.2 Agent专属预检

受控项目：

- UUID：`c96cdbc2-67d8-4a0a-9307-c534c99d6ea8`
- 名称：`BIZ-4a smoke tender d2c269f9`
- `ready_to_start=true`
- blockers：空
- 在线Agent Worker：1
- Worker错误数：0
- Worker：`小智:12256`
- Runtime：`bid_intake_runtime_phase5a`
- Checkpoint：`sqlalchemy`
- MCP session：`persistent`
- 模型：`deepseek-v4-flash`
- 备用模型：未配置
- Policy：`qs_bid_decision_policy_2026_01`
- Evidence manifest：v1
- Manifest SHA-256：
  `7a004bd2cf58f4cf1cad27ba715563bc48a16828c055ee31213e17b8ef94f9ad`
- 可用文档：1
- 检索：`hybrid_rrf`
- 索引：`completed`

配置预检进程自身没有注入MCP运行密钥，因此报告
`configuration.mcp_configured=false`；在线Worker能力报告为
`mcp_configured=true`。本阶段未主动连接MCP，也未发送模型请求，
`mcp_probe`和`model_probe`均为`skipped`。

## 7. 数据库基线与重启风险

```text
alembic current: 20260731_0077
alembic heads:   20260731_0078
```

`20260731_0078_remove_execution_system.py`会删除旧执行系统。当前
`start_all.ps1`包含自动执行`alembic upgrade head`的逻辑，因此：

- 阶段2作出数据库决定之前，不应使用`start_all.ps1`重启整套服务；
- 不应直接执行`alembic upgrade head`；
- 必须先检查0078涉及的表、记录数、代码引用、备份与恢复路径；
- 阶段2需由用户单独批准。

## 8. 依赖版本基线

Agent虚拟环境：

```text
Python 3.12.4
httpx 0.28.1
langchain-core 1.5.1
langgraph 1.2.9
mcp 1.28.1
pydantic 2.13.2
PyJWT 2.13.0
PyMySQL 1.1.2
python-dotenv 1.2.2
PyYAML 6.0.2
SQLAlchemy 2.0.49
pytest 9.0.3
```

本地仍有既有`RequestsDependencyWarning`，后续发布依赖锁定阶段需
单独处理，不能把警告误写为本次测试失败。

## 9. 质量与测试基线

固定4项目38题普通Baseline的保存结果：

- 正向35题、负向3题；
- Hit@5：88.57%；
- Recall@5：70.67%；
- Precision@5：28.00%；
- MRR：79.29%；
- nDCG@5：69.04%；
- 普通检索负样本准确率：0/3。

候选003、004、005均按各自冻结契约拒绝，候选覆盖功能继续关闭。

拒答门002工程通用性审计保存结果为`112 passed`，确认代码不是具体
项目补丁，但没有授权生产启用。

本次阶段1重新执行以下本地聚焦回归：

```text
tests/test_bid_intake_fact_coverage.py
tests/test_bid_intake_runtime_phase4a.py
tests/test_bid_intake_runtime_config.py
tests/test_bid_intake_retrieval_evaluation.py
tests/test_tender_query_planner.py
```

结果：`89 passed, 1 warning in 5.22s`。没有运行固定38题、Agent、
Holdout、Challenge或正式A/B。

## 10. 已知风险

| 风险 | 当前判断 | 后续控制 |
|---|---|---|
| Agent主体未进入Git | 阻止可复现发布 | 阶段3建立精确发布提交 |
| 共享文件混有其他业务改动 | 可能误带入或漏依赖 | 逐文件拆分Agent补丁并验证依赖闭包 |
| DB 0077落后于0078 | 重启可能触发破坏性迁移 | 阶段2先审计、备份、决定版本 |
| 拒答门关闭、事实门shadow | 负例安全能力未实际生效 | 阶段4经批准后受控启用 |
| 真实MCP/模型探针未执行 | 配置就绪不等于链路实测 | 阶段4执行一次运维canary |
| 单Agent Worker、无备用模型 | 无高可用、吞吐有限 | 首批仅低频低并发试运行 |
| `.env`重复公共访问开关 | 配置卫生问题 | 生成脱敏基线时去重 |
| Requests依赖警告 | 非阻断，但依赖组合未完全收敛 | 发布锁定时统一依赖版本 |

## 11. 阶段2入口

阶段1完成后，下一步只处理数据库迁移风险：

1. 只读检查0078删除目标、现存记录数和代码引用；
2. 形成备份、恢复和二选一版本方案；
3. 向用户报告并申请批准；
4. 未批准前不迁移、不重启、不启用门禁。

阶段2不需要新项目、不运行候选召回A/B，也不解锁泰丰Holdout、
蓝城Challenge或惠州Holdout。
