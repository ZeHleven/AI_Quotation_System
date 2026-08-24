# Phase 4C-1：企业能力快照与七项硬门业务闭环

状态：代码、机器合同和完全隔离本地专项验证已完成。本文不授权连接 ECS、生产 MySQL/Redis/MinIO/Milvus，也不授权运行真实 PDF、OCR/视觉、Embedding/Reranker、生成模型或外部 MCP。

## 1. 目标

Phase 4B-3 已证明资料解析、检索、Agent DAG、模型/工具网关、Checkpoint 和报告能够在隔离环境跑通，但当时企业快照仍是合成占位，HG02—HG07 大多只能保持 unknown。Phase 4C-1 将“招标要求”和“本企业是否具备能力”放入同一条可审计事实链：

`Runtime Lab 企业画像 → 不可变 Snapshot/Record → Run 固定版本 → P1 企业 FactAssertion → ResolvedFact → HG01—HG07 → Decision/Report`

## 2. I01—​I11 企业能力槽

| 槽位 | Fact Slot | 内容 |
|---|---|---|
| I01 | `enterprise.identity.legal_name` | 法定主体 |
| I02 | `enterprise.qualifications.active_records` | 有效资质 |
| I03 | `enterprise.safety_license.active_record` | 安全许可证 |
| I04 | `enterprise.performance.records` | 相似业绩 |
| I05 | `enterprise.personnel.available_records` | 可用人员/证书 |
| I06 | `enterprise.financial.capacity` | 可用资金 |
| I07 | `enterprise.guarantee.capacity` | 保证金/保函能力 |
| I08 | `enterprise.bid_preparation.capacity` | 投标准备人天 |
| I09 | `enterprise.prohibited_risk.rules` | 禁投规则触发记录 |
| I10 | `enterprise.compliance.current_records` | 当前合规状态 |
| I11 | `enterprise.client_risk.current_records` | 客户风险记录 |

每个槽只有 `supported / partial / unknown` 三种覆盖状态。`unknown` 不生成事实断言；`partial` 生成中等置信企业事实，但 ResolvedFact 保持 `partial`，不能被硬门当作已支持事实。

## 3. 不可变与血缘

- 复用 0084 已建立的 `bid_enterprise_snapshots` 与 `bid_enterprise_snapshot_records`，不复制业务正文到 Gate 或 Trace。
- 每个 I 槽正文以规范 JSON 写入本地内容寻址对象目录；`payload_hash` 与 `object_ref` 进入 SnapshotRecord。
- Snapshot Hash 覆盖 catalog、as-of、11条 Record 元数据、payload hash 和 object ref；冻结后不更新、不删除，只能创建新版本。
- Run Bootstrap 只接受恰好包含 I01—​I11 且重算 Hash 一致的 frozen Snapshot。
- 0104 仅新增 `bid_fact_enterprise_links`，把 Enterprise FactAssertion 精确连接到 SnapshotRecord；有历史企业事实时禁止 downgrade。
- 页面/Trace 不返回企业对象正文。只有本地 admin 的企业快照 GET 可以读取值；普通 Run 报告只展示门禁状态、事实槽和比较计数。

## 4. Planner 与执行边界

启用 `FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_CAPABILITY=true` 后：

- P1 新增 `mvp.p1.06.build_enterprise_snapshot`；
- 该 Task 使用 `bid-enterprise-capability@1.0.0` 确定性 Skill，不允许模型或 Tool；
- 原冲突消解移动到 `mvp.p1.07.resolve_fact_conflicts`，必须等待全部文档事实和企业事实；
- P2 七个 Gate 全部依赖 P1.07；
- 关闭开关时仍使用历史 P1/P2 模板和 `catalog-1.0.0.json`，避免改变既有 Run 合同。

## 5. HG01—​HG07

| Gate | 主要输入 | 确定性判断 |
|---|---|---|
| HG01 | 截止时间 | 与 Run 冻结 evaluation time 比较 |
| HG02 | 资格要求 + I02/I03 | 受控 requirement type、精确 code/name 集合与许可证状态 |
| HG03 | 业绩/人员要求 + I04/I05 | 受控类型、精确集合匹配 |
| HG04 | I10 | 当前合规状态枚举 |
| HG05 | 保证金要求 + I06/I07 | CNY 金额与保证形式比较 |
| HG06 | 投标准备人天要求 + I08 | required/available person-days 比较 |
| HG07 | 招标人/客户 + I09/I11 | 全局触发或规范化后的精确交易对手匹配 |

规则边界：不从文件名、MIME、parser_hint 或自由文本关键词推导 pass/fail；缺少结构、partial、冲突、过期或无法机器比较一律 `unknown`。历史六个企业布尔 Fact 只作为旧 Run 兼容分支。Gate 保存输入 Fact ID/slot、比较模式与 matched/mismatched/indeterminate 计数，不保存企业原文。

## 6. Runtime Lab

- `GET /api/v1/bid-assessment-runtime-lab/enterprise-snapshot`：仅本地 admin，允许 view-only 读取历史快照。
- `POST /api/v1/bid-assessment-runtime-lab/enterprise-snapshots`：仅本地 execute admin，必须 `Idempotency-Key`；view-only 中间件在路由前硬阻断。
- Execute Preflight 升级为 `bid.runtime.execute-preflight.v2`，新增 `ENTERPRISE_CAPABILITY_SNAPSHOT`；没有合法快照时禁止上传/启动新研判，但不禁止配置快照。
- 前端的“配置企业能力”权限只依赖服务端 execute/write/admin 能力，不依赖 `current_process_ready`，避免形成“快照缺失阻断运行、运行未就绪又不能配快照”的死锁。
- 新 Phase 4C-1 execute 环境使用新的 local-lab Profile 版本和独立目录；启动器默认在旧目录名后追加 `-phase4c1`（例如 RQ2-B 使用 `.local-mvp1-real-rq2b-phase4c1`），并使用独立 PID/日志；历史 Phase 4B-3 库仍可 view-only 读取，不会就地升级为可执行库。

## 7. 迁移与发布门禁

- 唯一开发 head：`20260817_0104`，线性下接 `20260815_0103`。
- 所有新开关默认 false；不得把 0104 或 Phase 4C-1 配置应用到 ECS。
- 本阶段验证只允许使用 localhost、独立 SQLite/对象目录、确定性 Provider 与合成 TXT；不得把本地验证环境或 0104 应用到 ECS。
- 验证通过也只表示完全隔离本地 MVP 可用，不等于正式企业数据接入或生产发布。

## 8. 验证结果

- 最终合同/Schema、0104迁移、Phase 4C-1核心和相邻自动矩阵：`31 passed / 0 failed`。0104已覆盖隔离 upgrade/downgrade、非空企业事实血缘的 downgrade 保护，以及0083—0104单线性拓扑。
- 第一版11项 `supported` 快照完整跑通合成TXT全链：27/27 Task、Run/Report/Run Validation succeeded，生成11条企业 FactAssertion 和11条 Enterprise Link。
- 第二版以 I02=`partial`、I05=`valid_to` 已过期验证保守边界：仍为27/27 Task且成功收敛，只生成10条企业 FactAssertion/Link；I02 ResolvedFact保持`partial`，I05不生成断言并保持`unknown`。七项门结果为2 pass + 5 unknown，没有把不完整数据升级为通过。
- 专项测试发现并修正两项真实缺口：Windows长对象路径的临时文件名重复目标名导致上传503；HG03在部分人员事实缺失时可能只看业绩分支并误判pass。修复后分别使用短UUID临时名和“已存在相关要求但企业事实非supported则unknown”的统一比较门。
- Execute模式浏览器确认快照11/11、Preflight企业快照检查ready、配置入口可用；切换同库view-only后，Capability为write/model/worker全false，快照POST返回403 `BID_MVP1_VIEW_ONLY`，上传与“创建新版本”按钮禁用，浏览器控制台0 error/warn。
- view-only启停和被拒写请求前后数据库SHA-256均为`27049C0B39B98A441F5D1EE880E448F138B05603944A04CBCC8FA0FDD49DF0EE`。9004最终保持完全隔离的view-only/deterministic/legacy模式。
- 验证未使用真实PDF、OCR、视觉、Embedding、Reranker、生成模型、外部MCP或外部环境。
