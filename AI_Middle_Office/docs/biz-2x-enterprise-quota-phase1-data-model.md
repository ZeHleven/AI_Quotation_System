# BIZ-2x 企业定额主库 Phase 1 数据模型

## 目标

Phase 1 新增企业定额主库数据模型，只完成表结构落地。

本阶段不导入真实定额、不清空旧成本库、不生成 `cost_items` 投影、不接报价链路、不触发 RAG 同步。

## 新增表

| 表 | 定位 |
|---|---|
| `cost_import_batches` | 成本/定额文件导入批次，记录文件 hash、解析版本、摘要和问题清单 |
| `enterprise_quota_versions` | 企业定额版本，一个版本对应一次可激活的定额主库快照 |
| `enterprise_quota_sections` | 定额分部，例如 `QS201 块料楼地面工程` |
| `enterprise_quota_items` | 定额主项，例如 `QS201001 石材地面（正铺）` |
| `enterprise_quota_components` | 定额组成明细，例如人工、主材、辅材、机械消耗行 |
| `enterprise_cost_resources` | 企业资源价格库，承接劳务指导价和材料价格库候选资源 |

## 关系

```text
cost_import_batches 1 -> N enterprise_quota_versions
enterprise_quota_versions 1 -> N enterprise_quota_sections
enterprise_quota_versions 1 -> N enterprise_quota_items
enterprise_quota_versions 1 -> N enterprise_quota_components
enterprise_quota_versions 1 -> N enterprise_cost_resources
enterprise_quota_sections 1 -> N enterprise_quota_items
enterprise_quota_items 1 -> N enterprise_quota_components
enterprise_cost_resources 1 -> N enterprise_quota_components
```

## 关键设计

- `enterprise_quota_versions.status` 使用 `draft / active / archived`。
- `enterprise_quota_versions.is_active` 用于快速查询当前激活版本；唯一 active 由后续 Phase 2 服务逻辑控制。
- `enterprise_quota_items` 允许 `quota_code / item_name / unit / unit_price` 为空，以便 draft 阶段完整保存原始文件中的问题行。
- `enterprise_quota_components.quota_item_id` 允许为空，以便保存暂时无法挂接父级定额的组成明细。
- 所有主数据表保留 `source_sheet`、`source_row_index` 和 `raw_row_json`，支持回溯原始 Excel 行。
- `cost_items` 本阶段不改；后续 Phase 2 由 active 定额版本生成兼容投影。

## Alembic

新增 revision：

```text
20260626_0035_add_enterprise_quota_tables.py
```

下游依赖：

```text
20260609_0034 -> 20260626_0035
```

## 后续 Phase 2

Phase 2 建议实现：

- 定额文件解析结果写入 draft 版本。
- 管理员确认后激活一个版本。
- 激活时清空/归档旧 `cost_items` 业务数据。
- 从 `enterprise_quota_items` 生成新的 `cost_items.active` 投影。
- 触发或提示 RAG 全量同步。
