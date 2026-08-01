# 报价资料研判 Agent Phase 4d：历史回放与总经办标准校准

## 1. 阶段目标

本阶段解决“总经办标准由谁校准、如何证明新标准更好”的问题，不直接修改 `qs_bid_decision_policy_2026_01`。

形成以下闭环：

1. 对已完成研判建立独立的总经办金标；
2. 冻结当时的资料清单、Agent经营因素和人工判断；
3. 将样本分为 development 与 holdout；
4. 用相同样本离线比较active与候选政策；
5. 对危险报价、硬红线召回和一致率设置发布门；
6. 即使发布门通过，也不自动切换active版本。

## 2. 为什么不能直接使用Agent结论

Agent原建议是被评测对象，不能同时作为标准答案。

金标必须来自以下依据之一：

- `pre_bid_expert_review`：投标前总经办独立复核；
- `actual_project_outcome`：项目实际投标、中标、利润、回款或履约结果；
- `combined`：专家复核与实际结果综合判断。

每个金标必须填写结论、理由、是否应触发硬红线和数据分层。

## 3. 不可变金标快照

新增表：

`bid_intake_policy_calibration_labels`

关键字段：

- `assessment_id / project_id`；
- `label_version / active / supersedes_label_id`；
- `dataset_split`；
- `expected_decision / hard_stop_expected`；
- `label_basis / rationale / actual_outcome_json`；
- `case_snapshot_json`；
- 来源报告、资料清单和政策版本。

首次标注会冻结：

- `DocumentManifest`；
- `AssessmentDraft`；
- 总经办金标；
- 报告版本、资料版本与政策版本。

修订金标时只允许形成新版本，不覆盖旧行，也不重新读取已经变化的Assessment。development与holdout分层一经确定不可互相移动。

Alembic版本为 `20260727_0069`。

## 4. 影子评测

核心实现：

- `app/agents/bid_intake/calibration.py`；
- `app/services/bid_policy_calibration.py`；
- `skills/bid-decision-policy/scripts/calibrate_policy.py`。

评测同时加载：

- 当前 `active_version.txt` 指向的基线政策；
- 指定候选政策版本；
- 数据库中的active金标快照或离线JSON数据集。

所有评分均调用同一个确定性 `YamlBidPolicy`，不调用模型、不调用MCP、不写报价业务数据。

## 5. 发布门

候选标准进入总经办发布评审前必须满足：

- 总样本不少于30个；
- holdout不少于10个；
- 不报价样本不少于5个；
- 硬红线样本不少于3个；
- 回放错误为0；
- 不增加危险报价；
- holdout硬红线召回率100%；
- holdout金标一致率不低于active版本；
- holdout金标一致率不低于80%。

“危险报价”定义为：金标要求不报价或标记硬红线，但候选政策给出“建议报价”或“有条件报价”。

发布门只生成 `passed/failed` 建议。本阶段API固定返回 `activation_allowed=false`，不存在自动切换active版本的接口。

## 6. API与前端

新增API：

- `GET .../assessments/{assessment_uuid}/calibration-label`；
- `POST .../assessments/{assessment_uuid}/calibration-label`；
- `GET /api/v1/admin/bidding/bid-intake/calibration/report`。

维护和查看完整金标需要管理员、系统管理员或经理权限；其他投标用户只能看到金标已封存和聚合校准指标，不能读取逐个holdout答案。校准报告只返回聚合指标。

“立项研判”工作台新增：

- 候选政策选择；
- 金标样本数与holdout数量；
- 金标一致率；
- 危险报价数；
- 硬红线召回率；
- 发布门状态；
- 历史研判金标录入和版本修订。

## 7. Skill资源

`bid-decision-policy` Skill新增：

- `references/calibration-protocol.md`；
- `scripts/calibrate_policy.py`；
- `evals/calibration_case_template.json`。

离线用法：

```powershell
python skills/bid-decision-policy/scripts/calibrate_policy.py `
  --dataset <dataset.json> `
  --candidate-policy-version <candidate_version> `
  --aggregate-only
```

加上 `--fail-on-gate` 后，发布门未通过时退出码为2，可接入后续CI。

## 8. 验证结果

- Agent、Policy、MCP和持久化相关回归：36项通过；
- FastAPI运行时与校准API专项：3项通过；
- Vite生产构建通过，共转换1631个模块；
- Skill结构校验通过；
- 校准模板实际回放通过；
- Python编译检查通过；
- SQLite `0068 -> 0069 -> 0068 -> 0069` 往返通过；
- MySQL模型DDL编译通过。

测试环境仅存在 `.pytest_cache` 无写权限警告和既有前端大包提示，不影响本阶段结果。

当前环境未启用研判Runtime且没有本阶段可复用的登录后测试会话，因此未执行浏览器视觉验收；正式启用前仍需按金标录入、候选切换和窄屏布局完成一次人工验收。

## 9. 当前边界

本阶段没有：

- 迁移真实MySQL；
- 录入真实历史项目金标；
- 新建候选政策版本；
- 修改75/60分阈值或11项权重；
- 启动Agent Worker；
- 调用真实模型或MCP；
- 切换 `active_version.txt`。

下一阶段应先选择一批已结束的真实项目，由总经办进行独立标注；样本达到最低结构要求后，再生成第一个候选政策版本。
