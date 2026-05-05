# P4 知识库发布流程增强 — 设计文档

> 生成时间：2026-05-02
> 项目：AI 智能报价中台（Clear_test / AI_Middle_Office）

---

## 一、目标

知识库热更新至 Milvus 成功后，自动触发 RAG 检索效果评测。评测结果在 `admin.html` 知识库面板内展示，指标低于阈值时给管理员警告（不强制阻断发布）。历史评测记录持久化，支持未来趋势追溯。

---

## 二、触发时机

**在 `POST /admin/sync_milvus` 成功返回后**自动触发，不在 `POST /admin/materials`（仅保存本地 JSON）后触发。原因：保存阶段 RAG 向量引擎中仍是旧数据，此时评测无意义；热更新完成后 RAG 才真正承载新知识库。

---

## 三、数据模型

### 新增表：`rag_eval_reports`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | Integer | PK, autoincrement | 自增主键 |
| triggered_by | String(64) | NOT NULL | 触发热更新的管理员用户名 |
| status | String(16) | NOT NULL, default `running` | `running` / `completed` / `failed` |
| started_at | DateTime | NOT NULL | 评测开始时间（UTC）|
| finished_at | DateTime | nullable | 评测结束时间（UTC）|
| top_k | Integer | NOT NULL, default 5 | 检索返回条数 |
| case_count | Integer | nullable | 实际运行的测试用例数 |
| hit_rate | Float | nullable | 整体 Hit@K（0.0～1.0）|
| mrr | Float | nullable | 整体 MRR（0.0～1.0）|
| by_level_json | Text | nullable | 各难度级别指标，JSON 字符串 |
| error | Text | nullable | 失败原因 |
| report_path | String(256) | nullable | 完整报告 JSON 文件路径 |

### Alembic Migration

文件：`alembic/versions/20260502_0002_add_rag_eval_reports.py`

---

## 四、配置项

在 `app/core/config.py` 新增，对应 `.env` / `.env.example`：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `RAG_EVAL_ENABLED` | bool | `true` | 热更新后是否自动触发评测 |
| `RAG_EVAL_TOP_K` | int | `5` | 检索返回条数 |
| `RAG_EVAL_WARN_HIT_RATE` | float | `0.70` | Hit@K 低于此值显示警告 |
| `RAG_EVAL_WARN_MRR` | float | `0.50` | MRR 低于此值显示警告 |

---

## 五、后端架构

### 5.1 新增 `app/services/rag_evaluator.py`

职责：
- 内嵌评测核心逻辑（`TEST_CASES`、`call_rag`、`hit_at_k`、`reciprocal_rank`、`run_eval`），从 `eval_rag.py` 提取，避免跨目录 import
- `trigger_eval_background(triggered_by, db_session_factory)` — 向 DB 写入 `running` 记录后立即返回 `report_id`，后台线程执行评测
- 内存锁（`threading.Lock` + running 标志位）防止并发重复触发；重复请求直接返回当前 running 的 `report_id`
- 评测完成后更新 DB 记录状态为 `completed` 或 `failed`，同时将完整报告写入 `rag_eval_reports/` 目录

### 5.2 新增 `app/api/v1/rag_eval.py`

仅 admin 可访问：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/admin/rag_eval/latest` | 返回最新一条评测记录（含状态、指标和 `quality_ok` 字段）|
| GET | `/api/v1/admin/rag_eval/history?limit=10` | 返回最近 N 条历史记录 |

### 5.3 修改 `app/api/v1/chat.py` — `sync_to_milvus`

热更新成功后追加：
```python
eval_report_id = None
if settings.rag_eval_enabled:
    eval_report_id = trigger_eval_background(current_user.username, SessionLocal)
return {"code": 200, "message": "...", "eval_triggered": settings.rag_eval_enabled, "eval_report_id": eval_report_id}
```

### 5.4 修改 `app/main.py`

注册 `rag_eval` router。

---

## 六、前端设计（admin.html）

### 位置

"一键热更新至 Milvus" 按钮下方，嵌入现有知识库管理区域，不新增独立面板。

### 状态机

```
热更新成功 → eval_triggered=true → 开始每 3 秒轮询 /admin/rag_eval/latest
    → status=running  : 显示旋转图标 + "RAG 评测进行中…"
    → status=completed: 停止轮询，展示指标
    → status=failed   : 停止轮询，展示错误原因
```

### 展示规则

前端根据响应体中的 `quality_ok`（由后端按配置阈值计算）决定展示颜色，不在 JS 中硬编码阈值。

| 场景 | 显示 |
|------|------|
| `running` | `<el-tag type="info">评测中…</el-tag>` + 旋转图标 |
| `completed` + `quality_ok=true` | 绿色 `<el-tag type="success">✓ 质量达标</el-tag>`，Hit@K、MRR 数值，各难度级别折叠展示 |
| `completed` + `quality_ok=false` | 橙色 `<el-alert type="warning">⚠ RAG 质量下滑，建议检查知识库</el-alert>`，同时展示具体指标 |
| `failed` | 红色 `<el-alert type="error">评测失败：{error}</el-alert>` |

### 页面加载

`onMounted` 时调用一次 `GET /admin/rag_eval/latest`，展示上次评测时间和结果，供管理员对比参考。

---

## 七、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `alembic/versions/20260502_0002_add_rag_eval_reports.py` | 新增 | 数据库迁移 |
| `app/models/rag_eval_report.py` | 新增 | ORM 模型 |
| `app/services/rag_evaluator.py` | 新增 | 评测服务（含内嵌评测逻辑）|
| `app/api/v1/rag_eval.py` | 新增 | API 路由 |
| `app/core/config.py` | 修改 | 新增 4 个配置项 |
| `app/api/v1/chat.py` | 修改 | `sync_to_milvus` 触发评测 |
| `app/main.py` | 修改 | 注册 router |
| `AI_Middle_Office/.env.example` | 修改 | 补充配置示例 |
| `admin.html` | 修改 | 新增评测结果展示区域 |
| `SPRINT.md` | 修改 | P4 标记为已完成 |
| `AI_Middle_Office/CLAUDE.md` | 修改 | 补充 P4 完成记录 |

---

## 八、不在本次范围内

- 评测用例的动态管理（增删改）— 当前沿用 `eval_rag.py` 内置 30 条 TEST_CASES
- 评测历史趋势图 — 数据已落库，可在后续迭代中添加
- 阻断发布 — 本次只警告，不强制阻断
