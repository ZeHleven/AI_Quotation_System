# 小范围内网试运行交付材料总入口

更新时间：2026-06-03

## 1. 当前结论

当前系统已进入“小范围内网试运行准备”状态：

```text
阶段 1：Windows 内网启动与访问收口 - 有条件通过
阶段 2：账号权限、安全口径与备份恢复收口 - 通过，Milvus 冷备待真实试运行前择机补做
阶段 3：试运行启动当天检查与交付文档收口 - 通过
阶段 4：核心前端体验与演示/沙盒包 - 通过，老板/业务员脚本已彩排通过
阶段 5：规则模板与正式上线清单 - completed_as_templates
```

当前系统可作为“小范围内网试运行候选版本”。当前仍不是正式生产环境。Windows 电脑仍是临时内网服务器。

## 2. 启动当天只看这几份

建议启动当天优先按以下顺序查看：

| 顺序 | 文档 | 用途 |
|---|---|---|
| 1 | `reports/trial_readiness/20260603_stage3/startup_day_checklist.md` | 启动当天逐项勾选 |
| 2 | `AI_Middle_Office/docs/windows-lan-startup-runbook.md` | Windows 内网启动、访问地址、防火墙 |
| 3 | `AI_Middle_Office/docs/trial-account-permission-runbook.md` | 账号、角色、临时密码、安全口径 |
| 4 | `AI_Middle_Office/docs/trial-backup-restore-runbook.md` | 备份与恢复口径 |
| 5 | `AI_Middle_Office/docs/trial-user-quick-start.md` | 业务员/老板怎么用 |
| 6 | `AI_Middle_Office/docs/trial-admin-operations-handbook.md` | 管理员日常怎么维护 |
| 7 | `AI_Middle_Office/docs/trial-troubleshooting-handbook.md` | 出问题怎么排查 |
| 8 | `AI_Middle_Office/docs/trial-risk-boundary-statement.md` | 对老板/参与者说明边界 |
| 9 | `AI_Middle_Office/docs/trial-stage4-frontend-demo-sandbox-runbook.md` | 阶段 4 演示/沙盒总口径 |
| 10 | `reports/trial_readiness/20260603_stage4/frontend_core_path_checklist.md` | 核心前端路径彩排检查 |
| 11 | `AI_Middle_Office/docs/trial-stage5-rule-template-and-production-readiness-runbook.md` | 规则模板与正式上线清单总口径 |
| 12 | `reports/trial_readiness/20260603_stage5/trial_to_production_gap_statement.md` | 对老板说明试运行与正式生产差距 |

## 3. 试运行启动前最低通过线

启动真实小范围试运行前，至少满足：

- Windows 主机已开机并联网。
- CentOS 依赖可达。
- `start_all.ps1 -Lan` 启动成功。
- 当前 `LAN URL` 已记录。
- 本机 `/health/ready` 返回 ready。
- Celery worker 正常。
- MySQL 业务数据库备份已通过。
- 试运行账号已准备。
- 参与人员知道当前不是正式生产。
- 风险边界已说明。
- 如做演示，已说明沙盒数据不代表真实 ROI。
- 如做演示，已选定样例文件并记录 sample_id。
- 如进入真实试运行，已使用阶段 5 模板说明哪些规则尚待业务拍板。

## 4. 当前推荐访问地址

本机：

```text
http://127.0.0.1:9000/login
```

当前已验证的 Windows 内网 IP：

```text
http://192.168.110.138:9000/login
```

如 Windows 换网或重启后 IP 变化，以 `logs/current_access_urls.txt` 最新内容为准。

## 5. 已知未完成但不阻断阶段 4

- 还没有第二台同局域网设备完成跨设备访问补验。
- Milvus 在线 volume 备份仍有文件变化 warning，真实试运行前建议低峰期补一次冷备。
- 管理员日常重置普通用户密码的正式 UI/API 仍待后续补强。
- 管理员日常停用账号的正式 UI/API 仍待后续补强。
- 当前演示样例均为 sandbox，只能验证系统流程和页面体验。
- 阶段 5 模板均为待业务拍板模板，不是最终公司制度。
- 正式上线前仍需固定服务器、固定地址、HTTPS、自动备份、监控告警和运维负责人。
- 当前系统不承诺正式生产 SLA。

## 6. 文件索引

阶段 1：

- `AI_Middle_Office/docs/windows-lan-startup-runbook.md`
- `AI_Middle_Office/docs/windows-lan-startup-acceptance-20260603.md`

阶段 2：

- `AI_Middle_Office/docs/trial-account-permission-runbook.md`
- `AI_Middle_Office/docs/trial-backup-restore-runbook.md`
- `AI_Middle_Office/docs/trial-stage2-account-backup-acceptance-20260603.md`
- `reports/trial_readiness/20260603_stage2/account_initialization_template.csv`
- `reports/trial_readiness/20260603_stage2/account_change_log.csv`
- `reports/trial_readiness/20260603_stage2/backup_acceptance_record.md`

阶段 3：

- `AI_Middle_Office/docs/internal-trial-delivery-index.md`
- `AI_Middle_Office/docs/trial-user-quick-start.md`
- `AI_Middle_Office/docs/trial-admin-operations-handbook.md`
- `AI_Middle_Office/docs/trial-troubleshooting-handbook.md`
- `AI_Middle_Office/docs/trial-risk-boundary-statement.md`
- `AI_Middle_Office/docs/trial-stage3-delivery-acceptance-20260603.md`
- `reports/trial_readiness/20260603_stage3/startup_day_checklist.md`
- `reports/trial_readiness/20260603_stage3/trial_issue_log.csv`
- `reports/trial_readiness/20260603_stage3/trial_daily_operation_log.md`

阶段 4：

- `AI_Middle_Office/docs/trial-stage4-frontend-demo-sandbox-runbook.md`
- `AI_Middle_Office/docs/trial-demo-script-boss-5min.md`
- `AI_Middle_Office/docs/trial-demo-script-operator-15min.md`
- `AI_Middle_Office/docs/trial-frontend-core-path-acceptance.md`
- `AI_Middle_Office/docs/trial-stage4-frontend-demo-acceptance-20260603.md`
- `reports/trial_readiness/20260603_stage4/demo_sample_inventory.csv`
- `reports/trial_readiness/20260603_stage4/frontend_core_path_checklist.md`
- `reports/trial_readiness/20260603_stage4/demo_run_record.md`
- `reports/trial_readiness/20260603_stage4/frontend_demo_issue_log.csv`

阶段 5：

- `AI_Middle_Office/docs/trial-stage5-rule-template-and-production-readiness-runbook.md`
- `AI_Middle_Office/docs/trial-stage5-acceptance-20260603.md`
- `reports/trial_readiness/20260603_stage5/effective_requirement_rule_template.md`
- `reports/trial_readiness/20260603_stage5/quote_rejection_reason_template.csv`
- `reports/trial_readiness/20260603_stage5/no_cost_reference_decision_template.md`
- `reports/trial_readiness/20260603_stage5/project_node_evidence_template.csv`
- `reports/trial_readiness/20260603_stage5/exception_escalation_template.csv`
- `reports/trial_readiness/20260603_stage5/weekly_business_metrics_template.md`
- `reports/trial_readiness/20260603_stage5/production_readiness_checklist.md`
- `reports/trial_readiness/20260603_stage5/trial_to_production_gap_statement.md`
