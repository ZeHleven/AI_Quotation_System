# BIZ-2m 无底价项目规则开发落地演示与验收记录

> 状态：代码层验证与自动化模拟演示已完成（2026-05-27）
> 范围：无底价项目确认下发后生成成本库 `draft` 待审核
> 边界：未做真实业务下发验收；未改变报价价格口径；未新增 Alembic。

## 1. 演示目标

验证 BIZ-2m 是否把无底价项目从“预审人工判断”接成“成本库待审核治理”：

- 无底价行可以在人工确认后下发。
- 下发成功后，系统自动生成 `cost_items.draft`。BIZ-2p 后，手动改价来源为 `manual`，采用 AI 建议或沿用 AI 价来源为 `ai_suggested`。
- 未补价占位行继续阻断下发。
- 有 active 成本参考的行不生成无底价 draft。
- 推送失败不生成 draft。
- 重复 draft 不重复堆积。
- draft 启用前不参与后续报价；启用为 active 后才参与报价。

## 2. 演示准备

环境要求：

- 数据库迁移版本不低于 `20260526_0023`。
- `FEATURE_COST_DB=true`。
- `FEATURE_NO_COST_DRAFT_CAPTURE=true`。
- `PUBLIC_ACCESS_ENABLED=false`。

本次代码层模拟使用测试数据库与 fake N8N push，不触发真实钉钉推送。

## 3. 演示流程设计

### 3.1 业务员视角

1. 发起报价，预审单出现无底价行。
2. 无底价行显示：

```text
无成本库参考价，AI 估价，仅供参考，请人工确认价格依据
```

3. 业务员确认或修改单价/合计。
4. 点击确认推送。
5. 如果存在未补价占位行，系统阻断下发。
6. 如果无底价行已补正数单价/合计，推送成功后提示已生成成本库待审核草稿。

### 3.2 成本部视角

1. 打开成本库。
2. 筛选 `status=draft`。
3. 筛选 `source=AI 建议`。
4. 查看新生成条目的名称、规格、单位、价格和来源备注。
5. 复核价格依据后，决定启用为 `active` 或继续保留 draft。

### 3.3 回归视角

1. draft 未启用前，再次报价同类条目，不应命中成本库参考。
2. 人工启用为 active 后，再次报价同类条目，应可命中成本库参考。
3. active RAG 同步只同步 active，不同步 draft。

## 4. 自动化模拟样例

| 样例 | 结果 |
|------|------|
| 无底价 AI/人工确认价推送成功 | 生成 `cost_items.draft`，来源按 BIZ-2p 价格动作判定 |
| 有 active 成本参考 | 不生成无底价 draft |
| 推送失败 | 不生成 draft |
| 未补价占位行 | `/confirm_push` 返回 409，不生成 draft |
| 已补价占位行 | 可生成 draft |
| 已存在相同 draft | 不重复创建 |
| 已存在相同 active | 不创建重复 draft |
| 成本库 source 筛选 | 可按 `source=manual` 筛出人工来源，按 `source=ai_suggested` 筛出 AI 建议来源 |
| draft 不参与报价匹配 | 既有成本匹配测试保持通过，只取 active |

## 5. 本次执行结果

已执行：

```text
python -m pytest AI_Middle_Office/tests/test_no_cost_draft_capture_biz2m.py AI_Middle_Office/tests/test_quote_confirm_push_biz2m.py AI_Middle_Office/tests/test_cost_db_biz2a.py::test_list_filters_by_source
```

结果：

```text
10 passed, 1 warning
```

已执行：

```text
python -m pytest AI_Middle_Office/tests/test_confirm_push_schema.py AI_Middle_Office/tests/test_quote_history.py AI_Middle_Office/tests/test_quote_feedback.py AI_Middle_Office/tests/test_quote_jobs.py::test_confirm_push_rejects_unpriced_requirement_placeholders AI_Middle_Office/tests/test_quote_jobs.py::test_cost_fallback_does_not_price_requirement_placeholder AI_Middle_Office/tests/test_quote_cost_matching_biz2b.py
```

结果：

```text
25 passed, 1 warning
```

已执行：

```text
python -m pytest AI_Middle_Office/tests/test_quote_jobs.py AI_Middle_Office/tests/test_no_cost_draft_capture_biz2m.py AI_Middle_Office/tests/test_quote_confirm_push_biz2m.py
```

结果：

```text
35 passed, 1 warning
```

已执行：

```text
npm.cmd run build
```

结果：

```text
Vite build passed
```

已执行：

```text
node -e "...index.html script syntax check..."
```

结果：

```text
index.html script syntax ok
```

说明：测试中的 warning 是 pytest cache 写入 `.pytest_cache` 被 Windows 权限拒绝，不影响功能验证。

## 6. 重启后运行态确认

2026-05-27 已结束旧 9000 进程并重启后端，随后确认：

```text
9000 监听 PID: 32364
/health/ready: ready
database: ok
task_queue.mode: celery
task_queue.broker: ok
task_queue.worker: ok
task_queue.worker_count: 1
alembic current: 20260526_0023 (head)
FEATURE_COST_DB: True
FEATURE_NO_COST_DRAFT_CAPTURE: True
PUBLIC_ACCESS_ENABLED: False
```

说明：运行态已加载 BIZ-2m 功能开关，可进入当前环境人工验收。

## 7. 模拟演示观察

清晰点：

- 规则落点清楚：只在 `/confirm_push` 成功之后写入 draft。
- 成本库状态清楚：自动生成的条目永远是 `draft`，不会自动 `active`。
- 价格主库边界清楚：后续报价、兜底和 RAG 同步仍只读取 `cost_items.active`。
- 成本部入口更明确：Vite 成本库页面已支持按来源筛选，可配合 `draft + AI 建议` 找到待审核条目。

不流畅点：

- 当前首版把来源任务、确认行、AI 价和人工确认价写入 `notes`，足够人工查看，但不适合复杂统计；后续如要按来源任务筛选，应单独加 Alembic 做结构化字段。
- 旧预审弹窗可以展示固定无底价提示，但尚未增加“价格依据必填”字段；是否强制填写依据需业务再确认。
- 本次是自动化模拟和代码层验证，尚未用真实 N8N/钉钉链路做一次人工端到端演示。

## 8. 通过标准

BIZ-2m 当前达到代码层通过：

- 后端规则落地完成。
- 旧预审弹窗提示完成。
- 成本库 source 筛选完成。
- 自动化测试覆盖核心分支。
- Vite build 通过。
- 旧 `index.html` 脚本语法检查通过。

进入业务验收前建议：

- 重启后端和 Celery worker，使 `.env` 中的 `FEATURE_NO_COST_DRAFT_CAPTURE=true` 生效。
- 用一单包含“有底价、无底价、未补价占位、已补价占位”的小清单做人工验收。
- 验收后由成本部在 `/admin/cost-db` 筛选 `draft + AI 建议`，确认待审核条目是否容易理解。
