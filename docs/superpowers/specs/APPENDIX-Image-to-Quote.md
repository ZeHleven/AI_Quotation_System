# APPENDIX-Image-to-Quote｜样板图驱动报价
> 创建日期：2026-05-14
> 状态：规划中
> 关联主文档：[2026-05-14-ai-platform-upgrade-design.md](2026-05-14-ai-platform-upgrade-design.md)

## 功能概述

样板图驱动报价是在现有文字 / 清单图报价入口之外新增的入口。用户上传装修效果图，系统识别主要材料和工艺，生成可编辑需求表，再进入现有预审和报价流程。

核心原则：识别只是辅助填表，不替代人工确认。识别失败时降级到手动填写需求表。

## 整体流程

```text
上传效果图
  -> 视觉模型识别材料 / 工艺
  -> 生成需求表草稿
  -> 用户补充面积 / 门窗洞口
  -> 系统计算数量
  -> 人工确认需求表
  -> 转成现有文字报价输入
  -> 复用 N8N / RAG / 预审 / confirm_push
```

`QuoteJob.source_type` 区分 `text` / `image`，用于后续统计两种入口的准确率和人工修改率。

## 模型策略

首选 GLM-4V，因为现有系统已接入智谱 AI。若同一 `model_provider + model_name` 最近连续 3 次 golden set 评测召回率均低于 80%，且每次评测样本不少于 10 张，则判定为“持续低于 80%”，触发告警并进入模型切换评审。通过 `model_gateway` 切换至 GPT-4o Vision 或其他视觉模型，业务代码不直接依赖具体模型。

达标标准：

- 召回率 >= 80%
- 精确率 >= 70%
- 基于 10-20 张 golden set 样板图评测

## 数据模型

```
quote_image_analyses:
- id, created_at
- file_object_id: FK -> file_objects.file_id
- model_provider: zhipu / openai / other
- model_name
- raw_response
- materials_json
- confidence_avg: Numeric(3,2), range 0.00-1.00
- status: pending / done / failed / timed_out / canceled
```

`model_provider` 必须与 [ADR-AI-Governance.md](ADR-AI-Governance.md) 中 `ai_invocations.provider` 的供应商枚举保持一致。`FEATURE_IMAGE_QUOTE_GLM=true` 时默认写 `zhipu`；通过 `model_gateway` 切换 GPT-4o Vision 时写 `openai`；其他供应商暂写 `other`，并在 `raw_response` 或调用日志中保留实际 provider 名称。

`quote_jobs` 扩展：

```
source_type: text / image
image_analysis_id: FK -> quote_image_analyses.id, nullable
```

多图场景不在阶段 A 支持。阶段 D 如需支持多图，新增 `quote_job_image_analyses` 关联表，避免双向循环 FK。

状态同步规则：

- 父 `QuoteJob` 因超时进入 `timed_out` 时，对应 `quote_image_analyses.status` 同步为 `timed_out`
- 父 `QuoteJob` 被取消时，对应 `quote_image_analyses.status` 同步为 `canceled`
- 视觉识别失败但报价任务仍允许手动降级时，`quote_image_analyses.status='failed'`，`QuoteJob` 可继续走手动填写需求表流程
- 不允许父任务已终态而 `quote_image_analyses` 仍停留在 `pending`
- 图片路径的 QuoteJob 重试必须先校验原 `file_object_id` 仍可访问。文件存在时，创建新的 `quote_image_analyses` 记录并关联新 QuoteJob，不复用原 failed / timed_out / canceled 记录；文件不存在或已清理时，重试返回 409，要求用户重新上传图片。
- 如果原 `quote_image_analyses.status='done'` 且 `materials_json` 可用，重试可复用该识别结果创建新的需求表草稿，但仍必须创建新的 QuoteJob；不得修改原分析记录状态。

## materials_json Schema

```json
{
  "version": "1.0",
  "materials": [
    {
      "area": "客厅地面",
      "material": "复合木地板",
      "work_type": "铺贴",
      "confidence": "high",
      "notes": ""
    }
  ]
}
```

字段名不得随意扩展。需要升级时通过 `version` 字段演进。

`confidence` 合法值固定为：

- `high`
- `medium`
- `low`

计算 `quote_image_analyses.confidence_avg` 时统一使用以下映射：

```text
high = 0.9
medium = 0.6
low = 0.3
confidence_avg = avg(mapped_confidence)
```

模型返回未知 confidence 时，后端必须降级为 `low`，并在 `notes` 或结构化 warning 中记录原因。前端低置信度高亮以 `confidence in (medium, low)` 或 `confidence_avg < 0.7` 为准。

`confidence_avg` 入库前按上述映射求平均后四舍五入到两位小数，使用 `Numeric(3,2)` 存储，避免浮点误差影响 `< 0.7` 判断。

## 面积与数量

面积录入分两步：

- 阶段 B1：用户填写总面积，系统按识别区域分配
- 阶段 B2：支持户型图 / 平面图提取面积

数量计算规则：

| 材料类型 | 计算方式 |
|---------|----------|
| 地面铺贴 | 区域面积 * 1.05 |
| 墙面涂刷 / 贴砖 | max(区域面积 * 层高系数 - 门洞数 * 2.0 - 窗户数 * 1.5, 0) * 1.05 |
| 顶面 | 区域面积 |
| 线性材料 | `sqrt(区域面积) * 4`，标注估算值 |

墙面 `层高系数` 默认取 `2.8`，含义为按地面区域面积估算墙面展开面积的默认系数。前端允许用户覆盖，后端接受 `wall_height_factor`，合法范围固定为 `2.0-4.0`；超出范围时返回 `422 VALIDATION_ERROR`，不得静默 clamp。未填写时使用默认值并在需求表标注“层高系数默认 2.8，请预审核实”。

门窗洞口默认标准尺寸：门洞 2.0 平方米 / 个，窗户 1.5 平方米 / 个。未填写门窗数量时不扣减，需求表标注“未扣除门窗洞口，预审时请核实”。扣减后面积不得小于 0。

## 适配现有报价流程

后端新增 `image_quote_adapter.py`，将需求表 JSON 转成现有自然语言文字清单：

```text
客厅地面 复合木地板铺贴 42.00㎡
主卧墙面 乳胶漆涂刷 68.04㎡
```

转换后的文字作为 `user_input` 写入 `QuoteJob`，后续 N8N / RAG / Dify / DeepSeek / 预审 / confirm_push 全部复用现有流程。

adapter 转换规则：

- 去重 key 为 `area + material + work_type`。
- 同一 key 出现多条时合并为一条，数量 / 面积相加，`confidence` 取最低等级，`notes` 去重后用 `；` 拼接。
- 合并过程必须保留所有非空 `notes`，并在输出文字中追加到该行末尾，例如：`客厅地面 复合木地板铺贴 42.00㎡（备注：需确认规格；特殊工艺）`。
- 如果 `notes` 中包含会影响价格的工艺、规格、品牌或风险提示，adapter 不得丢弃；后续报价流程应把备注作为 `user_input` 的一部分进入 RAG / 预审。

## 功能开关

```env
FEATURE_IMAGE_QUOTE=false
FEATURE_IMAGE_QUOTE_GLM=true
IMAGE_QUOTE_MODEL_PROVIDER=zhipu
IMAGE_QUOTE_MODEL_ENDPOINT=
IMAGE_QUOTE_MODEL_NAME=glm-4v
```

开关语义：

- `FEATURE_IMAGE_QUOTE=false`：关闭样板图报价入口和相关接口。
- `FEATURE_IMAGE_QUOTE=true` 且 `FEATURE_IMAGE_QUOTE_GLM=true`：使用现有 GLM-4V / 智谱图像识别路径，保持当前默认实现。
- `FEATURE_IMAGE_QUOTE=true` 且 `FEATURE_IMAGE_QUOTE_GLM=false`：不直连 GLM-4V，改走 `model_gateway` 的通用图像识别路径。必须同时配置 `IMAGE_QUOTE_MODEL_PROVIDER`、`IMAGE_QUOTE_MODEL_ENDPOINT` 和 `IMAGE_QUOTE_MODEL_NAME`；缺失时启动检查返回 degraded，接口返回 `503 AI_PROVIDER_UNAVAILABLE`。

该开关用于 A/B 或供应商切换，不得在业务代码中散落 provider 分支。供应商差异只能封装在 `model_gateway` / `image_quote_adapter.py` 之后。

## 测试要求

- `image_quote_adapter.py` 单元测试覆盖至少 10 种材料组合
- 面积和数量计算覆盖损耗、线性材料、门窗扣减
- mock 视觉模型调用，验证异步任务和 SSE 事件
- 图片路径 confirm_push 后可通过 `QuoteHistory.quote_job_id -> QuoteJob.source_type` 判断来源为 image
- 现有文字报价路径无回归

## 验收标准

- 上传效果图后 30 秒内返回识别结果或明确失败原因
- golden set 召回率 >= 80%，精确率 >= 70%
- 识别失败出现“跳过识别，手动填写”降级入口
- 需求表所有字段可编辑
- 低置信度行有高亮提示
- 切换视觉模型后业务代码零改动

30 秒 SLA 口径：从前端提交上传请求开始，到页面收到 `done / failed / timed_out` 或可解释的 `queued / running` 状态为止。30 秒是用户可见反馈 SLA，不等同于后台 `timeout_at`。后台硬超时仍使用 `IMAGE_ANALYSIS_TIMEOUT_MINUTES=30`；若任务排队超过 30 秒仍未开始，前端必须显示“识别排队中，可稍后查看”，并继续通过 SSE / 轮询更新。
