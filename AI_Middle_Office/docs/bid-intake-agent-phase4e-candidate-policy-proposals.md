# 报价资料研判 Agent Phase 4e：候选标准提案引擎

## 1. 阶段目标

本阶段把 Phase 4d 的历史金标转化为“可审计、不可自动生效”的候选标准提案。

闭环为：

1. 冻结当前active政策和全部active金标快照；
2. 只使用development样本搜索候选阈值；
3. 保存候选政策、数据集指纹和development聚合指标；
4. 由总经办单独触发一次Holdout盲测；
5. 保存盲测聚合结果并永久冻结候选状态；
6. 不写Skill规则文件，不切换active版本。

## 2. 受约束的搜索空间

首版只允许调整：

- `decision_thresholds.recommend_quote_min`；
- `decision_thresholds.conditional_quote_min`。

搜索范围为active阈值上下 `5 / 2.5 / 0` 分，并要求：

- 有条件报价阈值不得高于建议报价阈值；
- 两档阈值至少相差5分；
- 阈值保持在0到100之间。

以下内容完全冻结：

- 11项经营因素和权重；
- `favorable / acceptable / adverse / critical / unknown` 五档分值；
- 关键未知项定义；
- 报价和有条件报价的覆盖率门槛；
- 六条硬红线。

因此本阶段不能通过降低覆盖率或删除红线来换取更高的一致率。

## 3. Safety-first目标

候选按以下字典序选择：

1. 危险报价数更少；
2. 硬红线漏判数更少；
3. 回放错误数更少；
4. 金标完全一致数更多；
5. 应追踪项目被错误放弃的数量更少；
6. 与active阈值的距离更小。

Development至少需要：

- 20个样本；
- 3个“不报价”金标；
- 3个“报价/有条件报价”金标；
- 0个回放错误。

如果搜索结果没有在前五项上实质优于active版本，返回 `NO_BETTER_CANDIDATE_FOUND`，不生成无意义提案。

## 4. 数据集冻结与盲测隔离

生成候选时：

- 按 `case_id` 排序全部active金标快照；
- 生成规范JSON；
- 计算SHA-256数据集指纹；
- 将全部development和holdout快照固化到提案；
- 搜索函数内部只筛选并读取development。

候选形成后，即使外部金标继续修订，该提案仍绑定原始快照。

Holdout盲测：

- 只能由管理员、系统管理员或经理触发；
- 同一候选最多计算一次；
- 重复请求幂等返回已冻结结果；
- API不返回逐个Holdout样本结果；
- 只保存总体、development、holdout聚合指标和发布门结果；
- 盲测通过也不获得active切换权限。

## 5. 持久化状态机

新增表：

`bid_intake_policy_candidates`

关键字段：

- `proposal_uuid / candidate_version / base_policy_version`；
- `status`；
- `search_method`；
- `dataset_fingerprint / dataset_snapshot_json`；
- `policy_yaml / changed_fields_json`；
- `development_report_json / blind_report_json`；
- 创建人与盲测执行人；
- 创建时间与盲测冻结时间。

状态：

```text
draft -> blind_passed
      -> blind_failed
```

`blind_passed` 和 `blind_failed` 都是终态。本阶段没有 `approved`、`published` 或 `active` 状态。

Alembic版本为 `20260727_0070`。

## 6. API与页面

新增API：

- `GET /api/v1/admin/bidding/bid-intake/calibration/candidates`；
- `POST /api/v1/admin/bidding/bid-intake/calibration/candidates`；
- `POST /api/v1/admin/bidding/bid-intake/calibration/candidates/{proposal_uuid}/blind-evaluate`。

“立项研判”页面新增：

- 生成候选标准入口；
- 候选版本、基线版本和状态；
- 两档阈值前后对照；
- Development一致率；
- Holdout聚合盲测结论；
- 一次性盲测确认；
- “本阶段不能切换active”的固定边界提示。

## 7. Skill能力

`bid-decision-policy` Skill新增：

- 候选提案生成边界；
- Safety-first搜索顺序；
- 一次性Holdout协议；
- `scripts/propose_candidate_policy.py`。

离线用法：

```powershell
python skills/bid-decision-policy/scripts/propose_candidate_policy.py `
  --dataset <dataset.json> `
  --candidate-policy-version <candidate_version>
```

脚本只输出JSON，不写入 `rules/`，不修改 `active_version.txt`。

## 8. 验证结果

- Agent、Policy、MCP、持久化与候选提案回归：42项通过；
- 候选提案专项：6项通过；
- FastAPI运行时、金标与候选API专项：4项通过；
- Vite生产构建通过，共转换1631个模块；
- Python编译检查通过；
- Skill结构校验通过；
- Skill候选脚本使用代表性development数据实际执行通过；
- SQLite `0069 -> 0070 -> 0069 -> 0070` 往返通过；
- MySQL候选表DDL编译通过。

完整历史迁移链从空SQLite库执行时，会被既有Phase 0 RBAC迁移的“必须预置系统管理员”安全门阻断；本阶段改用标记为0069的隔离临时库验证0070往返，不影响0070结论。

本阶段未迁移当前MySQL，也未执行登录后的浏览器视觉验收；页面结论属于构建级验证。

## 9. 当前边界

本阶段没有：

- 迁移真实MySQL；
- 使用真实历史项目生成候选；
- 执行真实Holdout盲测；
- 修改当前 `75 / 60` 阈值；
- 修改Skill中的规则版本；
- 切换 `active_version.txt`；
- 调用真实模型、MCP或Agent Worker；
- 新增候选发布审批接口。

当前active政策仍为 `qs_bid_decision_policy_2026_01`。下一步应先积累满足结构要求的真实金标，再决定进入小范围校准试运行或独立的总经办发布审批阶段。
