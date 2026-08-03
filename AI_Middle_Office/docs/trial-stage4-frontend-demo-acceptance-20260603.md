# 阶段 4 验收记录：核心前端体验与演示/沙盒包

日期：2026-06-03

## 1. 阶段结论

当前阶段 4 已完成文档层收口，状态为：

```text
passed_for_internal_rehearsal
```

含义：

- 已形成老板 5 分钟演示脚本。
- 已形成业务员 15 分钟演示脚本。
- 已形成核心前端路径验收说明和执行清单。
- 已形成沙盒样例清单和演示记录模板。
- 老板 5 分钟脚本已人工彩排通过。
- 业务员 15 分钟脚本已人工彩排通过。
- 本阶段没有新增业务模块。

## 2. 本阶段新增材料

| 材料 | 用途 |
|---|---|
| `AI_Middle_Office/docs/trial-stage4-frontend-demo-sandbox-runbook.md` | 阶段 4 总口径 |
| `AI_Middle_Office/docs/trial-demo-script-boss-5min.md` | 老板演示脚本 |
| `AI_Middle_Office/docs/trial-demo-script-operator-15min.md` | 业务员演示脚本 |
| `AI_Middle_Office/docs/trial-frontend-core-path-acceptance.md` | 核心前端路径验收说明 |
| `reports/trial_readiness/20260603_stage4/demo_sample_inventory.csv` | 沙盒样例清单 |
| `reports/trial_readiness/20260603_stage4/frontend_core_path_checklist.md` | 前端路径执行清单 |
| `reports/trial_readiness/20260603_stage4/demo_run_record.md` | 演示记录模板 |
| `reports/trial_readiness/20260603_stage4/frontend_demo_issue_log.csv` | 阶段 4 专项问题登记表 |

## 3. 不变边界

本阶段没有改变：

- 报价价格口径。
- 成本库 active 规则。
- 无底价 draft 沉淀规则。
- 项目进度硬门禁规则。
- RBAC 权限模型。
- 数据库结构。
- CentOS 服务部署。

## 4. 本机只读检查

2026-06-03 已完成本机只读检查：

| 检查项 | 结果 | 记录 |
|---|---|---|
| `http://127.0.0.1:9000/health/ready` | 通过 | `status=ready`，`database=ok`，Celery `ok=True`，`worker_count=1` |
| `http://127.0.0.1:9000/login` | 通过 | HTTP 200 |

本检查只确认服务和登录页可达，未代替完整角色登录、页面点击和演示彩排。

## 5. 已知未完成

| 项 | 状态 | 说明 |
|---|---|---|
| 真实业务员试用 | 未开始 | 当前部门暂时没有真实试运行窗口 |
| 真实老板演示 | 未开始 | 内部老板脚本彩排已通过，真实演示需等合适窗口 |
| 另一台同局域网设备访问 | 待补验 | 当前没有第二台同 Wi-Fi 电脑 |
| 真实跨部门闭环样本 | 暂无 | 不能证明真实 ROI |
| 管理员密码重置/停用正式 UI/API | 待后续 | 阶段 2 已明确为非阻断改进项 |

## 6. 下一步建议

内部彩排已完成。建议下一步进入阶段 5：

1. 准备规则模板包。
2. 准备正式上线前检查清单。
3. 把试运行和正式生产之间的差距写清楚。
4. 继续冻结业务边界，不新增跨部门业务模块。
5. 如真实试运行窗口出现，再按启动当天检查表执行。

## 7. 阶段 4 通过条件

阶段 4 进入“小范围试运行候选”前，建议满足：

- 老板演示脚本完成一次彩排。
- 业务员演示脚本完成一次彩排。
- 核心前端路径清单中 P0 项无阻断。
- 演示数据边界已在演示开头说明。
- 发现的问题已进入登记表。
