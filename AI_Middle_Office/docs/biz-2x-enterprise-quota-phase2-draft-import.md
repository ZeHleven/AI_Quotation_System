# BIZ-2x 企业定额主库 Phase 2：保存待激活版本

## 目标

把 Phase 0 解析出的企业定额结构化结果写入 Phase 1 新增的数据模型，形成一个 `draft` 状态的企业定额版本。

本阶段只做“保存待激活版本”，不启用、不替换报价链路、不写 `cost_items.active`，也不触发 RAG 同步。

## 写入范围

- `cost_import_batches`：记录本次导入批次、源文件、解析器版本、错误/警告统计。
- `enterprise_quota_versions`：创建 `draft` 版本，`is_active=false`。
- `enterprise_quota_sections`：写入企业定额 Sheet 的分部行。
- `enterprise_quota_items`：写入定额主项，并关联分部。
- `enterprise_quota_components`：写入组成明细，并尽量关联父级定额主项和资源。
- `enterprise_cost_resources`：写入组成明细资源、劳务指导价候选、材料价格库价格块。

## 服务入口

核心服务位于：

`AI_Middle_Office/app/services/enterprise_quota_import.py`

主要函数：

- `save_enterprise_quota_draft_from_file(db, file_path, ...)`
- `save_enterprise_quota_draft_from_preview(db, preview_result, ...)`

服务函数只 `flush`，不 `commit`。调用方负责提交或回滚事务。

## 命令行演练

默认是 dry-run，会解析并写入当前事务，然后立即回滚：

```powershell
python .\scripts\biz2x_enterprise_quota_phase2_import_draft.py "C:\path\广东旗胜-企业定额1.0（20260626）.xls"
```

真正保存 draft 版本：

```powershell
python .\scripts\biz2x_enterprise_quota_phase2_import_draft.py "C:\path\广东旗胜-企业定额1.0（20260626）.xls" --commit
```

可选指定版本信息：

```powershell
python .\scripts\biz2x_enterprise_quota_phase2_import_draft.py "C:\path\广东旗胜-企业定额1.0（20260626）.xls" --version-code qs-enterprise-quota-20260626-v1 --version-name "广东旗胜企业定额 1.0" --commit
```

## 业务边界

- `draft` 版本不会参与报价、底价兜底、漏项检测或 RAG 检索。
- 同一个 `version_code` 不允许重复导入。
- Phase 0 有错误时拒绝保存；警告会进入导入批次的 `issues_json`。
- 材料价格库横向多价格块仍保留为候选资源，后续 Phase 3 再做人工映射、激活和价格口径确认。

## 下一阶段建议

Phase 3 建议实现 draft 版本详情/差异检查与激活流程：

- 查看 draft 版本的分部、主项、组成和资源统计。
- 支持按版本激活，并保证同一时间只有一个 active 版本。
- 激活前生成数据质量检查报告。
- 再决定是否把 active 企业定额同步到报价检索或 RAG。
