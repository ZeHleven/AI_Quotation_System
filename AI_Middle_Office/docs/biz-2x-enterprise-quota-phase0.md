# BIZ-2x 企业定额主库 Phase 0 解析确认

## 目标

Phase 0 只解决一件事：确认 `广东旗胜-企业定额1.0（20260626）.xls` 能被系统稳定读取，并形成后续主库重建所需的字段映射、数量统计和问题清单。

本阶段不写数据库、不新增 Alembic、不清空旧成本库、不改报价链路、不触发 RAG 同步。

## 输入文件结构

当前样本工作簿包含 3 个 Sheet：

| Sheet | Phase 0 定位 | 当前处理 |
|---|---|---|
| 企业定额 | 主数据来源 | 识别分部、定额主项、组成明细 |
| 劳务指导价 | 劳务资源价格候选 | 识别候选行和疑似指导价列 |
| 材料价格库 | 材料资源价格候选 | 识别候选资源和横向价格块，标记需人工确认 |

## 企业定额字段映射

| 原字段 | 标准字段 | 说明 |
|---|---|---|
| 定额编码 | quota_code | 分部编码或定额编码 |
| 类型 | row_type | `分部`、`定额`、`RG人工`、`CB辅材` 等 |
| 项目名称 | item_name | 定额主项名称；组成行中多为资源编码 |
| 项目特征及工作内容 | work_content | 定额特征；组成行中多为资源名称 |
| 类型 | worker_or_subtype | 工种或子类型 |
| 单位 | unit | 计量单位 |
| 含量 | quantity | 主项通常为 1；组成行表示消耗量 |
| 单价 | unit_price | 主项综合单价或资源单价 |
| 人工费 | labor_fee | 主项费用拆分；组成行作为金额候选 |
| 主材费 | main_material_fee | 主项费用拆分 |
| 辅材费 | auxiliary_material_fee | 主项费用拆分 |
| 机械费 | machinery_fee | 主项费用拆分 |

## 校验规则

Phase 0 当前内置以下只读校验：

- 必需 Sheet 是否存在。
- 企业定额表头是否可识别。
- 分部行是否缺少编码或名称。
- 定额主项是否缺少编码、名称、单位或单价。
- 定额主项编码是否重复。
- 定额单价是否约等于 `人工费 + 主材费 + 辅材费 + 机械费`。
- 组成明细是否缺少父级编码、资源名称、单位。
- 组成明细金额是否约等于 `含量 * 单价`。
- 劳务指导价候选行是否缺少单位或价格。
- 材料价格库是否为横向多价格块结构，并提示后续人工确认列含义。

## 命令

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
python .\scripts\biz2x_enterprise_quota_phase0_preview.py "C:\path\to\广东旗胜-企业定额1.0（20260626）.xls"
```

默认输出目录：

```text
Clear_test\outputs\biz2x_enterprise_quota_phase0\
```

输出文件：

- JSON：完整结构化解析结果。
- Markdown：业务可读摘要。
- CSV：错误和警告清单。

## 阶段 0 验收口径

阶段 0 通过条件：

- 能读取 `.xls/.xlsx/.xlsm`。
- 能识别 3 个目标 Sheet。
- 能统计企业定额分部、定额主项、组成明细、劳务候选行、材料候选资源。
- 能输出错误和警告清单。
- 不写数据库、不触发旧成本库清理、不触发 RAG。

## 下一阶段

Phase 1 再新增企业定额主库表结构：

- `cost_import_batches`
- `enterprise_quota_versions`
- `enterprise_quota_sections`
- `enterprise_quota_items`
- `enterprise_quota_components`
- `enterprise_cost_resources`

Phase 0 的解析结果将作为 Phase 1 建表字段和导入规则的依据。
