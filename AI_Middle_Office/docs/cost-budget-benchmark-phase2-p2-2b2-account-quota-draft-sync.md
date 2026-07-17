# P2-2B-2 报价草稿同步到账户定额

状态：已完成当前环境验收（2026-07-16）

## 目标与边界

本阶段将“报价草稿中用户人工认可的价格”沉淀为当前账号的账户定额草稿，形成可追溯的账户价格资产。

- 仅处理当前账号、当前预算项目的可变计价草稿。
- 仅允许正数 `manual_unit_price` 行进入预览；空值、零值、基础匹配价和 AI 价格均不会自动沉淀。
- 新建或更新后的账户定额一律为 `draft`，不自动 `active`，不参与当前草稿重算。
- 不读取或写入企业定额主库，不创建/修改正式计价 run，不调用 LLM，不触碰“项目测算/采购结果”。

## 操作闭环

1. 在“预算项目 / 双模式计价草稿”点击“同步到账户定额”。
2. 服务端预览当前人工改价行，按账户内 `名称 + 特征 + 规格 + 单位` 指纹检测已有条目。
3. 用户逐行选择“新建账户定额草稿、更新已有条目或跳过”，填写同步依据后确认。
4. 服务器使用草稿与条目 revision 做并发校验，在同一事务内写入账户定额和同步审计。
5. 在“账户定额库”查看草稿；后续仍需人工编辑、启用，才可在下一阶段参与账户定额模式匹配。

已有条目处理规则：默认跳过；明确选择更新时，只更新单价、状态和 revision。若原条目为 `active`，会撤回到 `draft`；`archived` 条目不可更新。

## 数据与接口

- 开关：`FEATURE_ACCOUNT_QUOTA_DRAFT_SYNC`，依赖预算项目、计价草稿和账户定额库开关。
- 迁移：`20260716_0054_add_account_quota_draft_sync.py`。
- 审计表：`account_quota_sync_runs` 记录一次确认批次，`account_quota_sync_lines` 记录每个来源草稿行、选择动作、目标条目、来源快照、前后快照和结果。
- API：
  - `POST /api/v1/admin/budget-projects/{project_id}/pricing-draft/account-quota-sync/preview`
  - `POST /api/v1/admin/budget-projects/{project_id}/pricing-draft/account-quota-sync/confirm`

API 从登录用户的服务端账号关系解析范围；请求体不接受 `account_id`。草稿 revision、行 revision 和更新目标 revision 不一致时返回冲突，不会写入半成品。

## 当前环境验收

- 0054 前完整数据库备份：`output/pre_budget_0054_20260716_170148_p2_2b2/ai_quotation_before_0054.sql`，`172471901` bytes，SHA256 `356E4ABCDFD9068461C1C1A62C09F13E6BA237EC7AF4E776923AC4B52E4900D3`。
- 当前项目 15 的既有人工改价“拆除铝合金玻璃门”，单价 `50.000000 元/㎡`，在 Chrome 预览后同步为账户定额 `pricing_draft_sync / draft / R1`；页面显示精确价 `50.000000`。
- 企业定额、正式计价 run 与来源计价草稿在同步动作中不被写入。

## 后续阶段

P2-2B-3 才会建设“账户定额模式匹配”；其匹配源只能是当前账号人工启用的账户定额 `active`。P2-2C 的 LLM 估价不属于本阶段。
