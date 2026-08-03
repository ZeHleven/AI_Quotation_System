# 统一报价 UQ-1 实施与验收记录

> 2026-07-28 最新阶段决策：调试期恢复“双入口并存”。`/quotes` 列表和 `/quotes/new` 新表单恢复，旧对话报价 `/index.html?entry=new-quote&mode=quick` 同时保留；流程全部调通并验收后，再决定隐藏其中一个入口。现行实现见 `docs/unified-quotation-chat-entry-correction-20260728.md`。

日期：2026-07-28
阶段：UQ-1（统一入口与兼容适配层）
状态：项目报价入口已恢复，与对话报价并行调试

## 1. 本阶段目标

UQ-1 不重写已有报价引擎，也不改变价格口径。本阶段先解决对外产品最明显的入口分裂问题：

- 导航中只展示一个“报价工作台”；
- `/quotes` 汇总异步报价任务、报价历史和预算项目；
- `/quotes/new` 提供唯一的新建表单；
- 用户不再选择“快速报价”或“预算项目”；
- 系统按输入类型、功能开关和当前账号权限自动选择兼容链路；
- 保留旧链路作为过渡适配和回滚通道。

## 2. 已实现的用户流程

### 2.1 报价工作台

统一列表聚合三类现有数据：

1. `/api/v1/quote/jobs`：排队、处理中、失败、待预审的异步报价任务；
2. `/api/v1/history`：已确认报价、预审草稿和被打回记录；
3. `/api/v1/admin/budget-projects`：正式预算项目及其清单阶段。

前端将各链路状态归一为：

- 草稿；
- 处理中；
- 待补充；
- 待确认；
- 已完成；
- 失败；
- 已取消；
- 已归档。

已确认的旧报价可以直接在统一工作台抽屉中查看明细；进行中、失败或待预审的任务会进入原报价工作台继续处理；预算项目进入现有项目详情继续处理。

### 2.2 唯一新建入口

用户只需要：

1. 填写报价需求，或上传一个需求文件；
2. 可选填写项目名称和客户名称；
3. 点击“开始生成报价”。

系统自动分流：

| 输入与权限 | 系统处理 |
|---|---|
| 文本、图片 | 创建现有异步 AI 报价任务 |
| Excel，且账号同时具备预算项目编辑和项目计价权限 | 自动创建预算项目并导入清单 |
| Excel，但账号不具备完整预算计价权限 | 进入现有异步 AI 报价任务，沿用 Excel 解析能力 |
| 旧 `.xls` | 明确提示另存为 `.xlsx`，不静默失败 |

预算项目已经创建但文件导入失败时，页面保留项目编号，并提供“进入项目继续”，避免用户重试后创建重复项目。

## 3. 价格与数据边界

UQ-1 只统一入口和展示，不合并两套价格来源。

- 快速报价继续使用现有 AI 报价、`cost_items.active`、RAG/N8N/Dify 及其证据链；
- 预算项目继续严格使用唯一 active `enterprise_quota_versions` 和账户定额规则；
- 统一列表只做读取和状态映射；
- 不新增数据库表或字段；
- 不把成本库价格写入预算项目；
- 不把企业定额写回快速报价成本库；
- 不改变确认、下发、审计或草稿规则。

## 4. 权限、开关与兼容

新增独立开关：

```env
FEATURE_UNIFIED_QUOTES=true
```

开关打开：

- 导航展示单一“报价工作台”；
- `/quotes` 和 `/quotes/new` 可访问；
- 旧 `/quote/new` 自动跳转 `/quotes/new`；
- `staff`、`quote_user` 默认首页改为 `/quotes`；
- 经营总览中的报价链接改到 `/quotes`。

开关关闭：

- `/quotes` 和 `/quotes/new` 返回 404；
- 前端恢复旧“新建报价”和“预算项目”两个导航入口；
- `/quote/new` 保持原行为；
- 默认首页会退回第一个可用旧模块。

旧 URL、旧 HTML、旧报价任务和预算项目详情路由均未删除。

## 5. 主要代码

- `ai-web/src/UnifiedQuotes.vue`
- `ai-web/src/unifiedQuoteApi.js`
- `ai-web/src/App.vue`
- `AI_Middle_Office/app/core/config.py`
- `AI_Middle_Office/app/main.py`
- `AI_Middle_Office/app/services/rbac.py`
- `AI_Middle_Office/app/services/business_lite_dashboard.py`
- `AI_Middle_Office/tests/test_unified_quotes_uq1_frontend.py`

## 6. 验证结果

### 前端

```text
npm.cmd run build
✓ built
```

构建仅保留项目原有的大包体积提示，没有新增构建错误。

### 后端与契约测试

第一组：

```text
45 passed
```

覆盖：

- SPA 路由与旧入口重定向；
- 角色默认首页；
- 统一报价前端契约；
- 登录入口兼容；
- 经营总览链接。

第二组：

```text
4 passed
```

覆盖：

- 预算项目开关与角色模块；
- 预算计价权限边界；
- 报价运营只读权限；
- 新增统一模块没有污染旧权限矩阵。

### 运行态

使用同一份代码和 `.env` 在 `127.0.0.1:9001` 启动验证：

- `/health/ready`：ready；
- `/quotes`：200；
- `/quote/new`：307 跳转 `/quotes/new`；
- 未登录访问：正确进入登录保护边界。

当前 `127.0.0.1:9000` 由高权限 Python 进程占用。常规及授权重启脚本均无法终止该进程，因此 9000 尚未重新加载 `FEATURE_UNIFIED_QUOTES=true`。代码、构建产物和 `.env` 已准备好；使用管理员 PowerShell 执行以下命令后即可切换：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restart_backend.ps1
```

## 7. UQ-1 边界与下一阶段

UQ-1 已实现“一个入口”和“自动选择兼容链路”，但预算项目内部仍保留清单确认、正式版本启用、计价和复核等原有页面。

建议下一阶段 UQ-2 聚焦：

1. 把预算清单解析、低风险字段映射和正式版本创建改成后台自动步骤；
2. 在统一页面展示预算计价进度，不再直接暴露完整预算项目详情；
3. 将两条链路统一为同一个“待确认报价”页面；
4. 仅在缺字段、低置信度、价格异常或权限不足时要求用户介入；
5. 保持快速报价成本库和预算企业定额价格来源隔离。
