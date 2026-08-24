# Phase 4D-3：真实事实核验与硬门可比化

> 版本：v0.1-r62  
> 状态：协议、代码、机器合同、0108 迁移、真实业务闭环和 MVP RC 复验已完成本地隔离验收  
> 边界：仅隔离本地开发；所有新开关默认关闭；不得应用到 ECS

## 1. 目标

Phase 4D-2 已把真实企业文件冻结成 Evidence Item、Evidence Package 和 Business Baseline，但 `partial` 仍表示“资料存在、尚未形成可直接比较的权威事实”。Phase 4D-3 在不放宽证据门的前提下，把招标要求和企业能力转成同一套机器可比较事实，使 HG01—HG07 能区分 `supported`、`partial` 与 `unknown`，并阻止模型候选覆盖人工核验结果。

本阶段不解析新文件、不调用 OCR、视觉、Embedding、Reranker 或生成模型，也不从文件名、MIME、`parser_hint` 或模型自由文本推断事实。

## 2. 可比事实与证据规则

比较基线固定包含 16 个事实槽：

- 招标侧 5 项：项目概况、投标截止时间、资格要求、保证金要求、工期/现场约束；
- 企业侧 11 项：I01—​I11 对应的主体、资质、安全许可、业绩、人员、资金、担保、投标准备能力、禁投规则、合规记录和客户风险记录。

每个槽必须显式选择以下状态之一：

- `supported`：值已结构化且可供确定性比较；
- `partial`：有证据和值，但尚不足以作为硬门通过依据；
- `unknown`：没有足够权威事实，不允许携带值或伪证据。

招标侧非 unknown 事实必须引用当前 Manifest、当前 ParseHead 下可引用的 Evidence Atom；企业侧非 unknown 事实必须引用当前 Business Baseline 所绑定 Evidence Package 中、且明确映射到同一 I 槽的 Evidence Item。Child、Parent、文件名、模型结果和不在资料包内的文件不能作为冻结证据。

## 3. 不可变 Comparison Baseline

新增 `bid.hard-gate.comparison-baseline.v1`，按 Assessment 冻结：

- source Run、Manifest/Scope、Enterprise Snapshot、Business Baseline、Evidence Package；
- 16 个事实的状态、规范值、值 Hash、证据 ID 和 Fact Hash；
- Atom 的文本/定位 Hash、Evidence Item Hash，以及整体 Candidate/Baseline Hash；
- reviewer、核验时间、核验说明和待跟进槽位。

Validate 只生成候选和 Candidate Hash，不持久化；Freeze 必须同时通过 admin ACL、execute 模式、Idempotency-Key 和客户端回传的 Candidate Hash。已经冻结的基线不可修改，只能在来源或核验结论变化后创建新版本。

以下任一变化会让基线失效并阻断新 Run：当前 Manifest/Scope 变化、ParseHead 变化、Atom 文本或定位变化、最新 Business Baseline/Package 变化、Evidence Item Hash/状态/有效期变化，或任一事实/整体 Hash 不一致。

## 4. Run 与七项硬门

新开关启用时，Run Bootstrap 必须选择与当前 Manifest、Scope 和 Business Baseline 一致且仍有效的 Comparison Baseline，并把版本和 Hash 写入 Run 输入指纹及 Run 行。Preflight 同步增加 `HARD_GATE_COMPARISON_BASELINE` 门禁。

P1 的确定性事实物化任务将冻结事实写成 FactAssertion，并通过 `bid_fact_comparison_links` 记录 Assertion → Comparison Baseline → Fact Hash 血缘。招标事实继续写 Atom EvidenceLink；企业事实通过 Comparison EvidenceLink 回到真实 Evidence Item。Resolver 对这 16 个槽只接受当前 Run 绑定基线产生的 Assertion：

- 基线为 unknown 时，即使模型或旧任务产生候选，也保持 unknown；
- 基线为 partial 时，ResolvedFact 保持 partial，硬门不得据此 pass；
- 基线为 supported 时，才进入既有 HG01—HG07 确定性比较器；
- 其他未纳入比较基线的事实槽继续沿用历史解析和冲突消解逻辑。

Run Validator 校验 Run 绑定的 Baseline ID/Hash 和所有来源权威；漂移归类为 stale，不允许静默生成新结论。

## 5. 数据与迁移

线性 revision `20260818_0108` 下接 `20260817_0107`：

- `bid_hard_gate_comparison_baselines`：不可变比较基线；
- `bid_hard_gate_comparison_evidence_links`：事实到 Atom/Evidence Item 的证据血缘；
- `bid_fact_comparison_links`：运行事实到比较基线的物化血缘；
- `bid_analysis_runs` 新增成对可空的 Comparison Baseline ID/Hash，兼容历史 Run。

0108 downgrade 在存在任何比较基线、证据链接、事实链接或已绑定 Run 时拒绝执行；offline downgrade 同样拒绝。新开关 `FEATURE_BID_ASSESSMENT_PHASE4_FACT_VERIFICATION=false` 默认关闭，并要求 Phase 4D-2/4D-1/4C-3 能力依赖闭包。

## 6. Runtime Lab

Runtime Lab 新增：

- 基于一个 succeeded Run 和最新 Business Baseline 生成零写入核验草稿；
- 编辑 16 个事实的状态、类型、JSON 规范值、Atom/Item ID 和核验说明；
- Validate 展示 supported/partial/unknown 数量、待跟进槽位和 Candidate Hash；
- Freeze 后展示 Comparison Baseline 版本、Hash、来源绑定与 16 项核验结果；
- view-only 可读取但不能 Validate/Freeze，模式变化后原候选不能绕过服务端 Hash 重算。

启动器新增 `-EnableFactVerification`，且只允许 execute 模式；隔离 Lab 名 `phase4d3` 会补齐 4D-2/4D-1/4C-3 依赖，但不会自动启用模型、OCR、视觉或外部服务。

## 7. 验证结果

2026-08-18 经用户明确授权，在完全隔离的本地环境完成以下验证：

- Phase 4D-3 合同/Schema/配置、0108 实际 upgrade/downgrade、0083—0108 线性拓扑和核心动态矩阵：`197 passed / 0 failed`；0108 downgrade 对已绑定 Run、不可变 Baseline/Evidence/Fact 血缘均 fail-closed；
- Phase 4B-5、Phase 4C-1/2/3、Phase 4D-1/2 相邻回归：`45 passed / 0 failed`；
- API-40/41、SSE、Bootstrap 输入未就绪零占位相邻链：`3 passed / 0 failed`；合计 `245 passed / 0 failed`；
- Vite 生产构建通过，`2235 modules transformed`；
- 新建 9016 临时 SQLite Lab 验证 execute 能力、Worker 生命周期和四项业务前置阻断；同库切换 view-only 后 Worker、模型调用、SSE 和写权限均关闭，POST/PUT/PATCH/DELETE 全部返回 403，四个前端写按钮禁用，控制台 0 error；
- view-only 前后数据库 SHA-256 均为 `35BF3C4F59B28DC27CA0A3A5CE3DFC3DD8A2077E6E402944F8AF74340CF2A5BA`，证明只读模式没有持久化副作用；9016 已停止。

专项验证发现并修复一个真实边界：历史 Resolver 只遍历旧 Fact Catalog，导致 Comparison Baseline 新增而旧 Catalog 未登记的事实槽无法进入 ResolvedFact。现改为旧 Catalog 与 Comparison Fact 槽位并集；没有绑定 Comparison Baseline 时历史行为保持不变，相邻回归全通过。

本轮没有读取或运行真实 PDF/企业资料，没有调用 OCR、视觉、BCE Embedding、Reranker、生成模型或外部 MCP；9015、本地历史数据库和 ECS 均未改动。Phase 4D-3 已达到本地隔离工程验收状态，但仍不得视为生产发布或允许把 0108 应用到 ECS。

## 8. 真实业务闭环与验收

2026-08-18 经用户再次明确授权，在完全隔离的新副本
`.local-mvp1-real-rq2b-phase4d3-business-closure` 中使用资料包 v3、香港中心 307 页真实 PDF、固定本地 BCE 和 DeepSeek V4 Flash 完成首次 Phase 4D-3 真实业务闭环；9015、原始资料包和 ECS 全程未改动，未调用 OCR、视觉或外部 MCP。

### 8.1 Comparison Baseline

- 16 个可比槽按 `supported=1 / partial=6 / unknown=9` 冻结；唯一 supported 为招标侧装修一级资质要求，并绑定当前 ParseHead 下的可引用 Atom；
- 企业 I01—I05 保守保持 partial，I06—I11 保持 unknown；投标截止时间因原文为空白日期模板且值类型不满足 datetime 合同，明确降为 unknown；
- Comparison Baseline 为 `hard-gate-comparison-20260818084654-247feaf59517`，ID `a3403d88-176d-57d3-a5a6-233041975b90`，Hash `41a46387...65d4`，结果为 `verified_with_follow_up`；
- Run Bootstrap 和最终 Run Validation 均绑定同一 Baseline ID/Hash。

### 8.2 真实 Run 与恢复

真实 Run `run_e15b50e60d6b4ff1ab7b22ecb5b69a7e` 首次执行暴露一个历史兼容缺口：P1 Comparison 物化已经成功生成 7 条事实，但旧 Executor 仍固定从结果读取 `enterprise_snapshot_id` 来构造输出引用，而新协议返回 `comparison_baseline_id`，因此事务末尾触发 KeyError，三次任务尝试均安全回滚，依赖任务保持 blocked。

修复后 Executor 对历史 Enterprise Snapshot 与新 Comparison Baseline 分别生成稳定输出引用，并增加完整 Executor 路径回归；Phase 4D-3 专项由 `11 passed` 增至 `12 passed`。通过 API-43 从最近安全 Checkpoint 恢复同一 Run，没有覆盖失败记录或重新创建历史：

- `27/27` Task succeeded，历史失败链仍保留 3 个 failed Attempt，当前 Attempt 与依赖状态全部收敛；
- `34` ModelCall、`23` 本地 Tool、`95` Checkpoint，模型账本 `263105/9317` Token、`19546` micro-USD；
- 生成 16 条 FactAssertion，其中 7 条有 Comparison Fact Link、招标侧 2 条事实形成 6 条 Atom EvidenceLink；
- 形成 6 条有效 Claim、6 条 Atom 引用，Child-only 引用违规为 0；
- Run Validation `52/52` 通过，其中 `HARD_GATE_COMPARISON_BASELINE_CURRENT`、Checkpoint/Attempt/Model/Tool/Report/引用和终态唯一性均通过。

### 8.3 决策对比与业务验收

| 版本 | 企业数据权威 | 决策 | HG01—HG07 |
|---|---|---|---|
| 历史合成快照 | 合成演示数据 | `no_bid / hold` | unknown / fail / unknown / pass / unknown / unknown / pass |
| Phase 4D-2 资料包 v3 | 真实文件已导入但未冻结同构比较事实 | `insufficient / hold` | 7 项 unknown |
| Phase 4D-3 可比基线 | 16 槽显式核验并绑定 Atom/Item/Hash | `insufficient / hold` | 7 项 unknown |

Phase 4D-3 没有为了得到“更积极”的结论而把 partial 当作 pass。它确认 HG02 不应因旧合成数据 fail，HG04/HG07 也不应无证据 pass；在 0 个明确 fail、7 个 unknown 下保持 `insufficient/hold`，表示当前不能作投标承诺，需继续补齐官方资质状态、严格近五年相似履约、逐人证书与劳动关系、资金/担保、合规和禁投风险核验。

业务负责人零持久化预验收的 16 项系统检查全部通过，随后实际冻结新 MVP RC：

- RC `mvp-rc-20260818091440-c21d8d4b3bf9`；
- RC ID `5acf5ea4-ba1d-5d30-afaf-064a00b8df2a`；
- Release Hash `a6507f50...40dd`；
- 验收结果 `accepted_with_follow_up`，并显式绑定历史合成 RC，使 `no_bid -> insufficient` 和 HG02/HG04/HG07 变化可审计。

验收完成后 9017 已切回 view-only：Worker、模型调用和写权限全部关闭，写请求返回 403；切换前后数据库 SHA-256 均为 `75F3830D706B85A1F68D81B8E65DE3AC1D39FEFC016EFFCAB2B66D3460E5AC6E`。只读演示地址为 `http://127.0.0.1:9017/admin/bid-assessment-runtime-lab`。本结果仍是本地 MVP 业务验收，不构成生产上线授权，0108 仍不得应用到 ECS。
