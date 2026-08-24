# Phase 4D-2：真实企业能力资料导入协议

> 版本：v0.1-r60  
> 状态：代码、机器合同、0107 迁移、隔离专项与 Runtime Lab 双模式验收完成；I01—I05 真实企业资料已导入并冻结 v3 资料包  
> 边界：仅隔离本地开发；默认关闭；不得应用到 ECS

## 1. 目标

Phase 4D-1 已能由业务负责人核验 I01—I11 并冻结 Business Baseline，但仍依赖人工填写逻辑来源和 SHA-256。Phase 4D-2 把原始企业资料纳入新数据域权威，使“文件字节 → Evidence Item → I01—I11 Evidence Package → Business Baseline → Run → MVP RC”形成完整、可重放、可失效的血缘链。

本阶段不把上传成功解释为企业能力成立。文件名、扩展名、MIME、目录、OCR、视觉结果或模型输出均不得自动决定 I01—I11；槽位映射只能来自显式人工命令。

## 2. 三层权威

### 2.1 Evidence Item

每个原始文件形成一条不可变 `bid.enterprise.evidence-item.v1`：

- 服务端复用有界文件检查，校验扩展名、MIME、Magic、Office 容器、大小和客户端声明的 SHA-256；
- 对象键仅由服务端按内容 SHA-256 生成，API 不返回对象键、绝对路径或文件正文；
- 同时固化 `source_record_id`、`source_version`、来源类别、来源名称、有效期、文件名、MIME、大小和 `item_hash`；
- 允许的文件来源类别只有 `official_document`、`internal_system`、`audited_record`；管理层确认和无来源不是文件 Item。

### 2.2 Evidence Package

多个 Item 通过 `bid.enterprise.evidence-package.v1` 冻结为一个资料包：

- I01—I11 必须各出现一次；每个槽可显式选择 0—20 个 Item；
- 没有资料的槽必须写明 unknown 原因；整个资料包至少包含一个真实 Item；
- 同一 Item 可映射多个槽，但不会因为文件标题或 MIME 自动映射；
- Validate 只计算 Candidate Hash，不写资料包；Freeze 由服务端重算并校验 Idempotency-Key 与 `X-Enterprise-Evidence-Candidate-Hash`；
- Candidate/Package Hash 只提交稳定的 Item ID、Item Hash、内容 Hash、来源版本、有效期和人工映射，不纳入上传时刻等环境相关字段；
- Item 缺失、尚未生效、已过期或 Hash 漂移会阻断冻结。

### 2.3 Business Baseline 绑定

Phase 4D-2 开关启用时，新的 Business Baseline 必须绑定一个 Evidence Package。对 `supported/partial` 槽使用文件来源类别时：

- `evidence_item_id` 必须属于该 Package 且映射到同一个 I 槽；
- `evidence_class`、逻辑 `evidence_ref`、文件 SHA-256 和有效期必须与 Item 一致；
- unknown 槽不能携带 Evidence Item；
- Baseline Hash 同时绑定 Snapshot Hash 与 Package Hash。

Run Bootstrap 在 evaluation time 重新检查 Package/Item 状态和有效期，并将 Package Version/Hash 直接写入输入指纹；RC Source Hash 和决策复验 Manifest 继续绑定该 Hash。资料过期或 Package 漂移只会阻断新 Run，不会静默改写历史 Run。

## 3. 数据与迁移

线性 revision `20260817_0107` 下接 `20260817_0106`：

- `bid_enterprise_evidence_items`：原始资料内容寻址权威；
- `bid_enterprise_evidence_packages`：不可变资料包 Manifest；
- `bid_enterprise_evidence_package_items`：Item 与 I01—I11 的显式映射；
- `bid_enterprise_business_baselines` 增加可空的历史兼容 `evidence_package_id/evidence_package_hash`；只有 Phase 4D-2 开启时才强制要求。

0107 downgrade 在任一资料、资料包、映射或 Baseline 绑定存在时拒绝执行；offline downgrade 同样拒绝。新开关 `FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_EVIDENCE_IMPORT=false` 默认关闭，并要求 Phase 4D-1、Phase 4C-3 和企业能力开关依赖闭包。

## 4. Runtime Lab

新增本地 admin-only 能力：

- GET/POST Evidence Item 列表和单文件上传；
- GET 最新 Evidence Package；
- POST Package 零持久化 Validate；
- POST Package Candidate Hash Freeze；
- Business Baseline 页面从 Package 的槽位映射中选择权威 Item并自动带入来源类别、逻辑引用和 SHA-256；
- Execute Preflight 新增 `ENTERPRISE_EVIDENCE_PACKAGE` 门禁。

启动器仅在 execute 模式显式使用 `-EnableEnterpriseEvidenceImport`，或使用隔离的 `phase4d2` Lab 名称时启用。view-only 的所有 POST 继续由服务端 403 硬阻断。

## 5. 失败与恢复

- Item 上传以文件 SHA-256 和来源元数据参与幂等请求 Hash；同 Key 不同内容返回冲突；
- 对象存储先于数据库提交时只可能留下内容寻址孤儿，不会产生可见权威；后续维护可按 DB 引用安全清理；
- Package Candidate 漂移返回稳定 409，不覆盖既有 Package；
- 已冻结 Item、Package 和 Business Baseline 不更新，只能创建新版本；
- 新资料包不会自动使旧 Baseline“升级”，必须以新 Snapshot/Package 重新核验并创建新 Run。

## 6. 授权验证结果

本轮在完全隔离的 localhost、SQLite、本地对象目录和进程内测试边界内完成：

- Phase 4D-2 核心、合同/Schema/配置、0107 upgrade/downgrade、0083—0107 拓扑、Phase 4C/4D-1/Preflight/API-41/SSE 相邻回归共 `250 passed / 0 failed`；
- 覆盖文件格式/Magic/Office 容器/大小/SHA-256、对象存储失败恢复、API 无对象键或绝对路径泄露、幂等、事务、ACL、Candidate Hash 和 Package 漂移；
- 覆盖 I01—I11 显式人工映射、空包拒绝、unknown 说明、有效期、Business Baseline 同包同槽、Run/RC Package Hash 血缘及 stale 阻断；
- 修正 Execute Preflight v2 新增 `ENTERPRISE_EVIDENCE_PACKAGE` 后检查项上限仍为 13 的合同缺口，现冻结为 14；
- Vite 生产构建通过（2235 modules）；隔离 9013 execute 浏览器链以合成 TXT 完成 Item 上传、I01 显式映射、I02—I11 unknown、Candidate 校验与 Package 冻结；切换同库 view-only 后 Package 仍可读、写控件禁用，直接 POST 返回 `403 / BID_MVP1_VIEW_ONLY`，Worker 和模型调用关闭。

上述授权测试轮没有读取真实企业资料，也没有调用 PDF/OCR、视觉、Embedding、Reranker、生成模型或外部 MCP；9003 与外部环境未改动。

## 7. 首次真实企业资料导入

2026-08-18经用户提供文件并明确槽位，在独立9014 execute环境完成首次真实资料导入：

- 营业执照1份映射I01企业法定主体；
- 建筑装修装饰工程专业承包一级资质和建筑幕墙/机电安装/电子智能化二级资质共2份映射I02有效资质；
- 安全生产许可证1份映射I03安全生产许可，其证载有效期至2026-09-12，资料包明确记录临期续证跟进；
- I04—I11没有自动推断，均保留为带补充说明的unknown。

四个Evidence Item均以原文件SHA-256内容寻址并冻结。Package版本为`enterprise-evidence-20260818021830-6713c1f23e01`，Package Hash为`b0619006377bfaffba58830bee707c800952d530ab85c183252931ad71755549`。同库切换view-only后Package仍可读，Worker和模型关闭，写请求返回`403 / BID_MVP1_VIEW_ONLY`；9014保留view-only，9003/9013未改。

本次只做文件校验、来源元数据固化和用户明确映射，没有运行OCR、视觉解析、Embedding、Reranker、生成模型、研判任务或外部MCP。证书图片的真实性、二维码状态、企业当前登记和续证状态仍需业务负责人通过官方渠道复核。

## 8. 历史项目合同导入

2026-08-18经用户明确指定“近五年合同”，在同一独立9014 execute环境追加导入14份PDF原件：

- 14份文件均完成存在性、`%PDF-` Magic及复制前后SHA-256一致性校验，并分别冻结为不可变Evidence Item；
- 14个Item全部由用户明确映射到I04类似项目业绩/历史合同，I01—​I03沿用首次真实资料包的营业执照、两份资质证书和安全生产许可证；
- I05—I11继续保持显式unknown，没有从文件名、项目名称、年月、扩展名或MIME自动推断企业能力；
- 最新Package版本为`enterprise-evidence-20260818022828-f9e8e4ed24c0`，Package Hash为`f4649378616e79ce9f76efc48db9263cce0ffc6c2b22e4a8090f22c67a01d0c1`，映射槽为I01—​I04，I04包含14个Item；
- 同库恢复view-only后最新Package仍可读，Worker关闭，写探针返回`403 / BID_MVP1_VIEW_ONLY`，9014继续保留view-only。

“历史合同已入库”不等于“近五年业绩已通过”。以2026-08-18为当前日期时，2021-08处在滚动五年的边界月，2021-07及更早文件不能仅凭文件名自动计入；所有文件仍需按招标截止日和合同正文复核准确签订/履约时间、合同主体、金额、工程范围、完工验收及与目标项目的相似性。此次追加导入没有解析PDF正文，也没有运行OCR、视觉、Embedding、Reranker、生成模型、研判任务或外部MCP。

## 9. 人员与资格证书汇总导入

2026-08-18经用户提供“人员与资格证书”汇总图，在同一独立9014环境完成资料包v3：

- 原图通过PNG Magic及复制前后SHA-256一致性校验，作为1个不可变Evidence Item导入；
- 该图是企业提供的汇总表而不是逐人官方证书原件，因此Evidence Class冻结为`internal_system`，并由用户语境明确映射I05关键人员与人员证书；
- I01—​I04沿用v2资料，I06—I11继续保持显式unknown；
- 最新Package版本为`enterprise-evidence-20260818023417-986239c8dbd2`，Package Hash为`c00f746bf0691ddc83e443e798cc4596bbcdb5ebd2070ea9c2a0032e93d81a7b`，映射槽为I01—​I05；
- 同库恢复view-only后Package仍可读，Worker关闭，写探针返回`403 / BID_MVP1_VIEW_ONLY`。

该汇总表只能证明“企业提供了人员证书清单线索”，不能单独证明各证书真实、有效或适用于目标项目。进入Business Baseline前仍需补充或复核逐人证书原件、证书编号与有效状态、人员与企业的劳动/社保关系、项目任命资格，以及招标文件要求的人数、专业和等级。本次没有运行OCR、视觉解析、模型或研判任务。

## 10. 资料包 v3 真实 Business Baseline 与香港中心复验

2026-08-18经用户明确授权，在独立的9015本地环境复制Phase 4C-3“香港中心”权威数据库并线性升级到`20260817_0107`，没有修改原RC数据库、9014资料包环境或任何外部环境。随后把资料包v3的19个Evidence Item按原内容Hash和Item ID复制到隔离对象目录，重新冻结出Candidate Hash相同的本地Package：

- Package Candidate Hash：`986239c8dbd281beaa34cd51bd452273f1d86a817e7490f22b9988025994eee3`；
- 本地Package版本：`enterprise-evidence-20260818025955-986239c8dbd2`；
- 本地Package Hash：`a53757a05e909875f5cb517259d6f2b2861c765e89a18722f0247faa7caae5ad`；Package Hash因版本和冻结时刻参与Manifest而与9014源包不同，内容身份与槽位Candidate保持一致。

真实Enterprise Snapshot `enterprise-20260818030110-f4cd77ccbe31`将I01—I05保持`partial`、I06—I11保持`unknown`。业务负责人核验后冻结Business Baseline：

- Business Baseline版本：`enterprise-business-20260818030437-cb6c17be2ff6`；
- Baseline Hash：`66d001bb36574bc4eb2c7c2a1cc65a692450f93426a6646e1e527a9661ebc10c`；
- 验证结果：`verified_with_follow_up`；
- 主体、资质、安全许可、合同和人员汇总均绑定原始Evidence Item；临期安全许可、官方当前状态、合同日期/金额/验收/相似性、逐人证书和劳动关系不作自动推断。

以同一份307页“东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程”创建`reanalysis` Run `run_b73f0dffdd974bf19498dbba474920db`。Bootstrap审计固化上述Business Baseline和Package版本/Hash。RQ2-B、本地固定BCE与DeepSeek V4 Flash完整链结果为：

- Run、Report和Run Validation全部成功，27/27 Task、82 Attempt、33 Model、22 Tool、93 Checkpoint；Run Validation `51/51`检查通过；
- 生成Report v2、10 Claim和28条由权威证据渲染的引用；模型账本`234130/7915` Token、`18733` micro-USD；
- 七项硬门均为`unknown`，没有明确`fail`；最终Decision为`insufficient / hold`；
- 相比历史合成企业数据的`no_bid`（1 fail、4 unknown、HG04/HG07误呈pass），真实基线消除了合成数据造成的过度确定结论：HG02由`fail`改为`unknown`，HG04与HG07由`pass`改为`unknown`，其余保持或收敛为unknown。

该变化不是“真实企业一定可以投标”，而是更准确地表达当前证据边界：已有主体、资质、安全许可、历史合同和人员清单，但尚不足以形成七项硬门的权威可比结论。9015已恢复为同库view-only，Worker和模型调用关闭，写探针返回403；演示地址为`http://127.0.0.1:9015/admin/bid-assessment-runtime-lab`。本次未调用OCR、视觉或外部MCP，未连接ECS、生产Milvus或其他外部环境。
