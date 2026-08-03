# BIZ-2X 企业定额 Phase 4C/5 报价成本参考切源

日期：2026-06-26

## 目标

将报价链路的成本参考来源，从旧 `cost_items.active` 切换为当前已激活的企业定额版本。

Phase 4B 已完成“清空旧成本库 + 激活 draft 企业定额版本”，因此 Phase 4C/5 的重点不是再次激活数据，而是让报价前置参考、预审成本匹配、漏项建议、证据链和后台成本候选检索统一读取 `enterprise_quota_versions.is_active=true` 且 `status=active` 的企业定额主项。

## 切换规则

1. 若存在 active 企业定额版本：
   - 报价成本参考来源为 `enterprise_quota.active`
   - 价格口径为企业定额主项 `unit_price`
   - 证据字段保留 `enterprise_quota_version_id`、`enterprise_quota_version_code`、`enterprise_quota_item_id`、`quota_code`、分部信息和工作内容
2. 若不存在 active 企业定额版本：
   - 继续回退读取旧 `cost_items.active`
   - 保留既有测试环境和历史开发环境兼容性
3. 本阶段不把企业定额主项复制回 `cost_items`
4. 本阶段不自动同步 RAG
5. 本阶段不改变 Phase 4B 的激活状态或备份文件

## 字段映射

| 报价成本参考字段 | 企业定额来源 |
| --- | --- |
| `item_name` | `enterprise_quota_items.item_name` |
| `spec` | `enterprise_quota_items.work_content` |
| `unit` | `enterprise_quota_items.unit` |
| `price` / `reference_price` | `enterprise_quota_items.unit_price` |
| `client_labor_price` | `enterprise_quota_items.labor_fee` |
| `client_main_material_price` | `enterprise_quota_items.main_material_fee` |
| `client_auxiliary_material_price` | `enterprise_quota_items.auxiliary_material_fee` |
| `category` | `enterprise_quota_sections.section_name` |
| `subcategory` | `enterprise_quota_items.worker_or_subtype` |
| `reference_source` | `enterprise_quota.active` |
| `reference_price_source` | `enterprise_quota_unit_price` |

## 覆盖链路

- 报价预审成本匹配：`quote_cost_matching.load_active_cost_items`
- 报价前置成本上下文：`quote_cost_context.build_quote_cost_context`
- 成本库兜底填价：沿用既有 enrichment 结果，新增企业定额来源元数据
- 漏项检测建议：输出企业定额版本、主项和编码
- 报价证据链：序列化企业定额来源字段
- 后台报价成本候选查询：存在 active 企业定额时优先搜索企业定额主项
- 旧报价页人工切换成本参考：展示企业定额综合单价和企业定额来源字段

## 当前环境只读 smoke

- active 企业定额版本：`id=3`
- 版本编码：`qs-enterprise-quota-20260626-v1`
- 旧 `cost_items` 数量：`0`
- 报价链路读取到的 active reference 数量：`474`
- 抽样 enrichment：
  - `reference_source=enterprise_quota.active`
  - `reference_price_source=enterprise_quota_unit_price`
  - `enterprise_quota_version_code=qs-enterprise-quota-20260626-v1`
  - `fallback_applied_count=1`
- 后台报价成本候选搜索：
  - 关键词：`石材`
  - 返回数量：`20`
  - 前三条均为 `enterprise_quota.active`
  - 详情链接指向 `/admin/enterprise-quota?version_id=3&quota_item_id=...`

## 验证命令

```powershell
C:\Users\12521\miniconda3\python.exe -m py_compile AI_Middle_Office\app\services\enterprise_quota_cost_reference.py AI_Middle_Office\app\services\quote_cost_matching.py AI_Middle_Office\app\services\quote_cost_context.py AI_Middle_Office\app\services\quote_cost_evidence.py AI_Middle_Office\app\services\quote_omission_detection.py AI_Middle_Office\app\services\cost_items.py AI_Middle_Office\tests\test_enterprise_quota_quote_source_phase4c_biz2x.py
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_enterprise_quota_quote_source_phase4c_biz2x.py -q
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_quote_cost_matching_biz2b.py -q
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_chat_sse.py -q
C:\Users\12521\miniconda3\python.exe -m pytest AI_Middle_Office\tests\test_cost_db_biz2a.py::test_staff_can_only_read_quote_cost_candidates -q
node -e "const fs=require('fs'); const html=fs.readFileSync('index.html','utf8'); const scripts=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).filter(s=>s.trim()); scripts.forEach((code,i)=>{try{new Function(code)}catch(e){console.error('script '+(i+1)+' syntax error: '+e.message); process.exit(1)}}); console.log('checked inline scripts:', scripts.length);"
```

验证结果：

- Phase 4C 专项：`3 passed`
- 报价成本匹配回归：`24 passed`
- SSE 报价入口回归：`6 passed`
- 后台候选读取权限回归：`1 passed`
- 旧报价页内联脚本：`checked inline scripts: 1`

备注：pytest 在当前工作区无法写入 `.pytest_cache`，仅产生缓存警告，不影响测试结论。
