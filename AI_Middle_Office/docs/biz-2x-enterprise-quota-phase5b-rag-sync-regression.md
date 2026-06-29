# BIZ-2X 企业定额 Phase 5B RAG 同步与回归验证

日期：2026-06-26

## 目标

将 RAG 同步源从旧 `cost_items.active` 扩展为当前 active 企业定额版本，并完成：

- dry-run 预览
- Milvus `/admin/reload` 写入
- `cost_rag_sync_runs` 同步记录
- RAG 检索样例回归
- 报价链路成本参考对比验证

## 同步源规则

1. 若存在 `enterprise_quota_versions.status='active'` 且 `is_active=true`：
   - 同步源为 `enterprise_quota.active`
   - 写入企业定额主项、组成明细摘要和资源价格
2. 若不存在 active 企业定额版本：
   - 兼容回退旧 `cost_items.active`
3. 同步记录仍写入 `cost_rag_sync_runs`，通过 `source` 区分来源。

## RAG 文档构造

企业定额主项生成 `enterprise_quota_item_{id}`：

- `item_name`: `quota_code + item_name`
- `unit_price`: 主项 `unit_price`
- `unit`: 主项 `unit`
- `notes`: 企业定额版本、分部、编码、工作内容、人工/主材/辅材/机械费、组成明细摘要

资源价格生成 `enterprise_quota_resource_{id}`：

- `item_name`: `resource_code + resource_name`
- `unit_price`: 优先 `computed_price`，否则 `price`
- `unit`: 资源单位
- `notes`: 资源编码、资源名称、资源类型、税率、价格块等

## 当前环境验证

active 企业定额版本：

- `id`: `3`
- `version_code`: `qs-enterprise-quota-20260626-v1`
- `version_name`: `广东旗胜企业定额 1.0（20260626）`

dry-run：

- source: `enterprise_quota.active`
- quota item count: `474`
- resource count: `1197`
- RAG payload count: `1671`
- sample: `enterprise_quota_item_475 / QS201001 石材地面（正铺）`

Milvus 写入：

- 首次使用旧 300 秒超时执行，run `id=7`，结果为 timeout，`requested_count=1671`，`synced_count=0`
- 已将默认 `RAG_RELOAD_TIMEOUT_SECONDS` 从 `300` 调整为 `900`
- 重试成功，run `id=8`
- source: `enterprise_quota.active`
- `requested_count=1671`
- `synced_count=1671`
- `http_status=200`
- duration: `463390 ms`
- RAG 返回：`零停机热更新完成，共同步 1671 条（quotation_green -> enterprise_quotation_rag）`

RAG 检索回归：

| 查询 | 报价链路命中 | RAG 状态 | 代表返回 |
| --- | --- | --- | --- |
| 石材地面 | `enterprise_quota.active / QS201007 / 101.13` | 200 | `QS201007 石材地面（拼花）` |
| 瓷砖地面 | `enterprise_quota.active / QS201027 / 71.13` | 200 | `QS201027 瓷砖地面（造型拼花 砖）` |
| 石材过门石 | `enterprise_quota.active / QS201004 / 111.13` | 200 | `QS201004 石材过门石` |
| 楼地面找平层 | `enterprise_quota.active / QS202022 / 2.0` | 200 | `QS202001 楼地面1 :3水泥砂浆找平层15mm厚` |
| 地面防水 | `enterprise_quota.active / QS202027 / 16.29` | 200 | `QS202027 地面丙纶防水（1遍）` |
| 瓷砖美缝 | `enterprise_quota.active / QS201034 / 11.38` | 200 | `QS201034 石材、瓷砖表面美缝` |

## 报告文件

- dry-run 报告：`outputs/biz2x_enterprise_quota_rag_phase5b/phase5b_enterprise_quota_rag_20260626_143501.json`
- 成功同步报告：`outputs/biz2x_enterprise_quota_rag_phase5b/phase5b_enterprise_quota_rag_20260626_145340.json`
- RAG 回归报告：`outputs/biz2x_enterprise_quota_rag_phase5b/phase5b_enterprise_quota_rag_20260626_145403.json`
- 对应 Markdown 报告同名 `.md`

## 验证命令

```powershell
C:\Users\12521\miniconda3\python.exe -m py_compile AI_Middle_Office\app\core\config.py AI_Middle_Office\app\services\cost_rag_sync.py AI_Middle_Office\app\api\v1\cost_items.py AI_Middle_Office\scripts\biz2x_enterprise_quota_rag_phase5b.py
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_enterprise_quota_rag_sync_phase5b_biz2x.py AI_Middle_Office\tests\test_cost_rag_sync_biz2c.py -q
C:\Users\12521\miniconda3\python.exe -m pytest tests\test_config_validation.py -q
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_enterprise_quota_quote_source_phase4c_biz2x.py AI_Middle_Office\tests\test_quote_cost_matching_biz2b.py -q
C:\Users\12521\miniconda3\python.exe scripts\biz2x_enterprise_quota_rag_phase5b.py --sync --sample-limit 1 --reload-timeout-seconds 900
C:\Users\12521\miniconda3\python.exe scripts\biz2x_enterprise_quota_rag_phase5b.py --regression --require-rag --sample-limit 1
```

结果：

- Phase 5B 专项 + 旧 RAG 同步回归：`15 passed`
- 配置校验：`6 passed`
- 报价切源 + 成本匹配回归：`27 passed`
- 当前 RAG 同步状态：`synced`

备注：pytest 在当前工作区无法写入 `.pytest_cache`，仅产生缓存警告，不影响测试结论。
