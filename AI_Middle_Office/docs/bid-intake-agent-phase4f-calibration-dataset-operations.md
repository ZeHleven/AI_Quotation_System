# 报价资料研判 Agent Phase 4f：真实金标运营与数据集冻结

## 1. 阶段目标

Phase 4f 将 Phase 4d 的单条金标和 Phase 4e 的候选提案升级为可运营的数据治理闭环：

1. 集中查询当前有效金标；
2. 由不同人员独立复核；
3. 阻止同一项目跨Development/Holdout；
4. 只统计复核通过的样本；
5. 通过质量门后冻结不可变数据集；
6. 候选标准必须显式绑定冻结数据集。

本阶段不发布立项标准，也不修改active版本。

## 2. 双人复核

金标与复核结果分开保存。

新增表：

`bid_intake_policy_calibration_reviews`

约束：

- 一个金标版本最多存在一条复核记录；
- 创建人与复核人不能是同一账号；
- 复核动作为 `approved` 或 `rejected`；
- 复核意见必填；
- 相同请求可幂等返回；
- 已存在的不同复核结果不能覆盖。

金标被修订时，旧版本及旧复核记录保留；新金标版本重新进入 `pending`。复核退回后不直接修改原金标，应形成新版本重新复核。

## 3. 项目级分层防泄漏

原规则只冻结单个Assessment的分层。本阶段增加项目级保护：

- 同一 `project_id` 第一次形成金标时确定Development或Holdout；
- 该项目后续不同研判报告只能沿用相同分层；
- 跨分层写入返回 `CALIBRATION_PROJECT_SPLIT_FROZEN`。

这避免同一项目的不同报告版本同时出现在调参集和盲测集中。

## 4. 数据质量门

只有同时满足以下条件的数据集可以冻结：

- 复核通过总样本不少于30个；
- Development不少于20个；
- Holdout不少于10个；
- 不报价金标不少于5个；
- 硬红线金标不少于3个；
- Development不报价金标不少于3个；
- Development报价/有条件报价金标不少于3个；
- 无无效金标快照；
- 无重复case_id；
- 无项目跨分层泄漏。

待复核与复核退回样本单独显示，但不进入冻结数据集。

## 5. 不可变数据集

新增表：

`bid_intake_policy_calibration_datasets`

冻结内容包括：

- 数据集UUID和版本号；
- 全部复核通过的不可变case快照；
- 金标版本、复核UUID和复核人引用；
- Development/Holdout分层；
- 质量报告；
- SHA-256数据集指纹；
- 冻结人、冻结时间和备注。

相同样本内容再次冻结会幂等返回原数据集。冻结后，实时金标的新增、修订或复核不会改变已有版本。

## 6. 候选提案绑定

`bid_intake_policy_candidates` 新增可空外键：

`calibration_dataset_id`

可空是为了兼容Phase 4e可能存在的旧候选记录；Phase 4f新候选必须提供 `dataset_uuid`，并且只能读取状态为 `frozen` 的数据集。

候选搜索继续只使用冻结快照中的Development，Holdout直到一次性盲测时才进入聚合评估。

## 7. API

新增：

- `GET /api/v1/admin/bidding/bid-intake/calibration/samples`；
- `POST /api/v1/admin/bidding/bid-intake/calibration/labels/{label_uuid}/review`；
- `GET /api/v1/admin/bidding/bid-intake/calibration/quality`；
- `GET /api/v1/admin/bidding/bid-intake/calibration/datasets`；
- `POST /api/v1/admin/bidding/bid-intake/calibration/datasets`。

调整：

- `POST /api/v1/admin/bidding/bid-intake/calibration/candidates` 必须提交 `dataset_uuid`；
- 实时候选对比报告只统计当前有效且复核通过的金标。

样本明细、复核、质量门与冻结需要管理员、系统管理员或经理权限；冻结数据集和候选提案的聚合信息仍可供有投标模块访问权的用户查看。

## 8. 页面

“立项研判”工作台新增：

- 左侧“智能工具”分组下的“报价资料研判 Agent”独立入口；
- 独立路由 `/admin/bid-intake-agent`，进入后可检索并切换投标项目；
- 独立页面直接复用项目级Agent工作台，不再要求先打开“智能投标”项目抽屉；
- 复核通过、Development、Holdout、待复核和质量门指标；
- 未通过质量规则的中文说明；
- 金标样本筛选、分页和项目追溯；
- 异人复核通过/退回入口；
- 数据集冻结入口；
- 已冻结数据集版本展示；
- 生成候选标准时显式选择冻结数据集；
- 候选提案展示绑定的数据集版本。

## 9. Skill

`bid-decision-policy` Skill的校准协议新增：

- 异人复核；
- 修订后重新复核；
- 项目级分层防泄漏；
- 复核通过样本口径；
- 不可变数据集冻结；
- 候选提案必须绑定冻结版本。

## 10. 验证

- Agent、Policy、MCP、持久化、候选与数据集专项：45项通过；
- FastAPI运行时、金标复核、项目分层与质量门专项：4项通过；
- Vite生产构建通过，共转换1631个模块；
- Python编译检查通过；
- Skill结构校验通过；
- SQLite `0068 -> 0069 -> 0070 -> 0071 -> 0070 -> 0071` 往返通过；
- MySQL新增模型DDL编译通过。

SQLite迁移验证从0068隔离基线开始，只执行本Agent相关迁移，不触碰真实数据库。

### 10.1 当前环境运行态验收（2026-07-27）

- 迁移前已备份当前MySQL业务表、数据与触发器，备份大小 `514016668` bytes，SHA-256为 `9526780E2E6DB2E90FEAAC948514DB0B064C73F69283A6446B05AE028002B7DC`；应用账号没有事件调度器权限，本次备份不包含数据库EVENT与存储过程；
- 当前MySQL已从 `20260727_0070` 升级到 `20260727_0071 (head)`；
- 已确认新增复核表、不可变数据集表、候选数据集外键字段和 `fk_bid_policy_candidate_calibration_dataset` 外键；
- 使用30个带明确验收标记的临时样本走通异人复核，其中Development 20个、Holdout 10个、不报价11个、硬红线8个；
- 浏览器人工验收通过：待复核列表、复核意见必填、复核冻结、质量门、数据集冻结、绑定数据集生成候选、一次性Holdout盲测均符合设计；
- 冻结数据集版本为 `qs_calibration_dataset_2694108ebb75`，候选版本为 `qs_bid_decision_policy_2026_01_cand_2694108e`，阈值候选为报价 `75 -> 72.5`、有条件报价 `60 -> 60`，Development和Holdout均为100%，发布门通过；
- API幂等性检查通过：重复冻结、重复生成候选不会新增记录，重复盲测不会改写盲测时间；Holdout逐条结果未暴露，`activation_allowed=false`；
- 浏览器控制台无错误，验收期间使用当前代码启动的隔离端口 `9101`，`/health/ready=ready`、MySQL正常、Celery broker/worker正常；
- 验收结束后已删除全部临时样本、复核、数据集、候选和临时账号，Agent运行表计数回到验收前状态，`bid_projects`回到6条；
- 当前active政策仍为 `qs_bid_decision_policy_2026_01`，本次未执行政策发布。

当前主端口 `9000` 已由所属Windows会话完成重启，新FastAPI进程PID为 `13700`。复核确认 `/health/ready=ready`、MySQL正常、Celery worker正常、首页返回200，7个Phase 4f校准接口均已加载；未登录访问质量门接口返回401而非404，表明主服务已经切换到当前代码。

## 11. 当前边界

本阶段没有：

- 录入或复核真实历史项目；
- 冻结真实校准数据集；
- 生成真实候选政策；
- 执行真实Holdout盲测；
- 修改active政策；
- 实现候选政策审批、发布或回滚。

当前active政策仍为 `qs_bid_decision_policy_2026_01`。
