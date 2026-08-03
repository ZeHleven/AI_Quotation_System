---
name: bid-decision-policy
description: 为装饰工程招投标项目生成可解释的报价立项建议，并按总经办标准执行资料门槛、经营因素评分、红线识别和缺项补充。用于报价资料研判 Agent 形成是否报价建议、解释评分、识别特别审批事项或回放历史决策；不能替代总经办最终审批。
---

# 报价立项决策

读取 `active_version.txt` 确定新任务使用的规则版本。恢复历史任务时，必须继续读取任务绑定的原版本，禁止自动漂移到 active 版本。

使用规则时：

1. 先提取并核验证据，再填写规则要求的经营因素。
2. 没有可靠来源时将因素标记为 `unknown`，禁止猜测。
3. 将招标资料事实标记为 `tender_evidence`，将企业系统数据标记为 `internal_data`，将经办人补充标记为 `human_input`。
4. 只把结构化因素交给确定性 PolicyEngine；不得让语言模型自行计算总分或决定规则是否命中。
5. 将硬规则、缺失信息、分项得分和最终建议完整返回给人工审核。
6. 不执行批准、报价、投标或对外发送动作。

详细口径见 [references/decision-standard.md](references/decision-standard.md)。运行规则位于 `rules/`，只能新增版本，不得覆盖已被任务绑定的历史版本。

使用 `scripts/replay_policy.py --input <case.json>` 对历史研判进行只读回放。输入包含 `manifest` 和 `assessment`，输出确定性的政策评估结果；回放不得写业务数据库。

校准或比较规则版本时，先读取 [references/calibration-protocol.md](references/calibration-protocol.md)，再使用 `scripts/calibrate_policy.py --dataset <dataset.json> --candidate-policy-version <version>`。开发样本和holdout样本必须分离；不得根据holdout逐项答案反向调参，也不得因回放结果自动修改 `active_version.txt`。

需要从历史金标形成候选提案时，先按校准协议完成异人复核、项目级分层检查和不可变数据集冻结，再使用冻结版本。离线验证可运行 `scripts/propose_candidate_policy.py --dataset <dataset.json> --candidate-policy-version <version>`。脚本只能在 development 上搜索报价分数阈值，输出到标准输出；不得调整权重、覆盖率门槛、五档分值、关键未知项或硬红线，不得读取holdout逐项结果，也不得写入 `rules/` 或切换active版本。
