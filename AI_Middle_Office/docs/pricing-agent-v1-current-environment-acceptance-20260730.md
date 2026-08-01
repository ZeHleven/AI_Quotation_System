# 组价 Agent V1 当前环境验收记录（2026-07-30）

## 结论

组价 Agent V1 已在当前内网开发环境完成数据库迁移、准确模式启用和真实 API 冒烟验收。

本次启用保持旁路边界：

- 不修改现有 `/chat`、`/api/v1/quote/jobs`、预算报价草稿及确认下发链路。
- 存档文件和检索数据使用独立的 `pricing_agent` 表及 API 命名空间。
- 当前只开放“准确”匹配。
- “准确+近似”和“行业数据”继续由后端功能开关禁用，不能只靠前端隐藏。
- 第一版结果只作为组价建议，不自动写回现有报价、成本库或企业定额库。

## 当前环境配置

数据库版本：

```text
20260730_0075 (head)
```

新增表：

```text
pricing_archive_files
pricing_archive_lines
pricing_agent_runs
pricing_agent_run_lines
```

本机存档根目录：

```text
D:\QishengPricingAgent\archives
```

启用配置：

```dotenv
FEATURE_PRICING_AGENT=true
FEATURE_PRICING_AGENT_EXPANDED_MATCH=false
FEATURE_PRICING_AGENT_INDUSTRY_ESTIMATE=false
PRICING_AGENT_ARCHIVE_STORAGE_BACKEND=local
PRICING_AGENT_ARCHIVE_LOCAL_ROOT=D:/QishengPricingAgent/archives
PRICING_AGENT_ARCHIVE_MAX_UPLOAD_MB=30
PRICING_AGENT_ARCHIVE_ACCOUNT_QUOTA_GB=20
PRICING_AGENT_ARCHIVE_MAX_INDEXED_ROWS=100000
```

迁移前数据库备份：

```text
AI_Middle_Office/backups/pricing_agent_v1_pre_0075_20260730_170046.sql
```

备份不包含 MySQL events/routines。当前应用账号没有相应导出权限；应用业务表和数据已成功导出。

## 真实 API 冒烟结果

验收脚本：

```text
AI_Middle_Office/scripts/pricing_agent_v1_smoke.py
```

脚本通过真实 FastAPI、MySQL 和本地文件目录完成以下操作：

1. 生成并上传一份带报价的 `.xlsx` 存档清单。
2. 自动识别固定系统字段并索引一条报价行。
3. 上传一份待组价需求清单。
4. 选择“存档数据”和“准确”模式执行组价。
5. 校验命中单价、合价、证据来源和磁盘原文件。
6. 校验“准确+近似”请求在功能开关关闭时返回 `403`。
7. 对比现有报价核心表验收前后的记录数量。

关键结果：

```text
status=passed
mode=exact
archive_uuid=63b7ae3d-c535-474d-81ba-03285c324fe6
run_uuid=ca0ea439-3e92-45ad-afb2-aab07f56af4b
storage_backend=local
storage_file_exists=true
indexed_rows=1
selected_source=archive
unit_price=128.500000
total_price=257.000000
expanded_guard=passed
```

既有流程数据隔离校验：

| 表 | 验收前 | 验收后 |
| --- | ---: | ---: |
| `quote_jobs` | 343 | 343 |
| `quote_history` | 199 | 199 |
| `budget_project_pricing_runs` | 6 | 6 |

新模块验收数据：

| 表 | 记录数 |
| --- | ---: |
| `pricing_archive_files` | 1 |
| `pricing_agent_runs` | 1 |

## 页面与鉴权

临时验收服务运行在 `127.0.0.1:9001`。访问 `/admin/pricing-agent` 时，未登录用户会被正确重定向到 `/login`；登录页标题、品牌内容和表单均正常渲染，浏览器控制台无错误。

临时端口与日常 `9000` 端口属于不同浏览器域，因此本次没有复制令牌、绕过登录或修改用户浏览器认证状态。

## 当前剩余操作

日常 `9000` 服务仍由一个高权限旧进程占用，普通权限无法停止。代码、迁移和 `.env` 已准备完成，但要让日常入口加载新功能，需要在管理员权限终端执行一次现有服务重启。

重启后检查：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health/ready
```

然后使用现有账号登录并访问：

```text
http://127.0.0.1:9000/admin/pricing-agent
```

验收顺序建议：

1. 上传 1 至 3 份真实的小型历史报价文件。
2. 检查字段自动识别结果和索引行数。
3. 用同一项目名、规格和单位的需求清单验证准确命中。
4. 检查每行报价依据是否能追溯到存档文件、Sheet 和原始行。
5. 确认旧报价工作台的创建、预审和下发功能无变化。

“准确+近似”与“行业数据”暂不进入本轮业务验收。

## 真实清单验收后修正

2026-07-30 使用“02、信达公司职工食堂装修改造工程报价.xlsx”完成真实清单验收后，修正了以下问题：

- “准确”模式在需求提供规格时，必须同时满足规格完全一致；单位已提供时也必须完全兼容，不再只凭唯一同名项目自动套价。
- 同名、同规格、同单位存在多个不同价格时不自动选价，明确标记为“需复核”并保留可人工采用的候选。
- 同名但规格不同的候选不再造成误报复核。
- 需求解析自动忽略汇总、序号和章节行。
- 新运行的开始时间与完成时间统一使用应用本地时间。
- 前端证据抽屉支持“采用此价格”，选择只作用于当前旁路结果，不写入存档、企业定额或原报价流程。

修正后对同一真实文件进行只读复算：

| 指标 | 修正前 | 修正后 |
| --- | ---: | ---: |
| 解析总行数 | 138 | 127 |
| 自动忽略非项目行 | 0 | 11 |
| 自动计价 | 125 | 125 |
| 未计价 | 13 | 2 |
| 需复核 | 45 | 2 |
| 误报复核 | 45 | 0 |

剩余两条均为“插座安装”，其名称、规格和单位相同，但存档中存在 `25.561253` 与 `26.107410` 两个不同单价，应由用户在候选证据中人工选择。

专项与交叉回归测试为 `41 passed`，Vite 生产构建通过。日常 `9000` 仍需管理员权限重启一次才能载入本次后端修正。
