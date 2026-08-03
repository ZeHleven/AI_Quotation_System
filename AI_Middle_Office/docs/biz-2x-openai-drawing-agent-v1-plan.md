# BIZ-2x OpenAI 识图 Agent V1 规划清单

生成日期：2026-06-22

## 1. 当前问题

当前 PDF 直接识图链路是：

`PDF -> 渲染 -> CAD 图框裁切 -> GLM 单图框列项 -> GB/T 国标匹配 -> Excel`

这条链路已经把“完全看不到图纸”推进到“能切出图框并输出候选项”，但最近同一份信达餐厅 PDF 的真实运行暴露了两个核心问题：

1. 模型仍然是按单张裁切图框局部识别。它倾向输出最清楚、最重复的标注，尤其是 CT 墙面瓷砖。
2. 多个图框逐张请求模型容易触发服务商限流。切出 33 个 CAD 图框后，GLM-5V-Turbo 连续调用触发 `HTTP 429`，随后模型网关熔断，最终输出 0 行清单。

因此下一步不应该继续只调 GLM 提示词，而是新增一条更接近 Codex 直接看图纸方式的 OpenAI 识图 Agent 通道：先收集整套图纸证据，再归纳清单。

## 2. V1 目标

建设第一版 OpenAI 识图 Agent 通道：

`PDF / CAD 图框 -> 全局图纸证据抽取 -> 清单项目归纳 -> GB/T 国标匹配 -> 四字段 Excel`

V1 输出仍然定位为“人工复核用预览清单”，不是正式结算工程量清单。

四字段为：

- `项目名称`
- `项目特征`
- `单位`
- `工程量`

V1 期望达到的业务质量：

- 看起来像一份可继续人工修改的清单草稿。
- 项目类型比当前 GLM 单图框链路更丰富。
- 每行尽量保留证据来源，方便人工判断为什么生成该项。
- 工程量允许粗估或标记为待复核。

## 3. 非目标

V1 暂不做：

- 不立即替换现有 GLM 链路。
- 不输出可直接结算的精确工程量。
- 不保证和人工 Excel 答案逐行一致。
- 不建设 Togal/Kreo 那类几何算量能力。
- 不自动进入报价、定价或下发流程。
- 不新增数据库持久化，除非后续单独确认。

## 4. 和当前 GLM 链路的核心区别

当前链路是：

`单张图框 -> 直接生成项目行`

OpenAI 识图 Agent V1 是：

`整套图纸证据 -> 全局归纳项目行`

也就是说，模型第一步不急着输出最终清单，而是先抽取材料编号、图名、标注、对象、做法、节点等证据。系统汇总证据后，再让模型基于整套证据生成预算员风格的清单。

## 5. V1 架构规划

### 5.1 复用现有模块

保留并复用：

| 层级 | 现有文件 | 用途 |
| --- | --- | --- |
| 上传 API | `app/api/v1/dwg_quantity_trial.py` | 复用 PDF 上传入口，可增加 provider 参数或新增测试入口 |
| PDF 渲染解析 | `app/services/drawing_pdf_evidence_pipeline.py` | 复用 PDF 渲染、基础解析能力 |
| CAD 图框裁切 | `app/services/cad_view_frame_detector.py` | 复用已完成的 CAD 图框检测与裁切 |
| 国标匹配 | `app/services/quantity_standard_index.py` | 复用 GB/T 清单项目搜索 |
| 国标库加载 | `app/services/quantity_standard_library.py` | 复用现有国标库 |
| Excel 输出 | `app/services/quantity_list_export.py` | 复用四字段 Excel/CSV 导出 |

### 5.2 新增模块

建议新增：

| 新文件 | 职责 |
| --- | --- |
| `app/services/openai_drawing_agent.py` | OpenAI 识图 Agent 主流程编排 |
| `app/services/openai_model_gateway.py` 或 `model_gateway.py` 内新增 provider 函数 | OpenAI 多模态调用封装 |
| `tests/test_openai_drawing_agent_biz2x.py` | 证据合并、清单归纳解析等单元测试 |

V1 可以先把 OpenAI provider 放在 `model_gateway.py` 内，减少文件数量。若后续 provider 逻辑变复杂，再拆成 `openai_model_gateway.py`。

## 6. 配置规划

新增配置：

```env
OPENAI_API_KEY=
OPENAI_VISION_MODEL=
PDF_ITEMIZATION_PROVIDER=glm
DASHSCOPE_API_KEY=
DASHSCOPE_VISION_MODEL=qwen3.7-plus
DASHSCOPE_EVIDENCE_MODEL=qwen3.7-plus
DASHSCOPE_BILL_SUMMARY_MODEL=qwen3.7-plus
OPENAI_DRAWING_AGENT_MAX_VIEWS=24
OPENAI_DRAWING_AGENT_BATCH_SIZE=8
OPENAI_DRAWING_AGENT_TIMEOUT_SECONDS=120
```

provider 规则：

- `PDF_ITEMIZATION_PROVIDER=glm`：沿用当前 GLM 直接识图链路。
- `PDF_ITEMIZATION_PROVIDER=dashscope_agent`：启用 DashScope/Qwen Agent 链路，可用于 `qwen3.7-plus` 服务商通道 smoke。
- `PDF_ITEMIZATION_PROVIDER=openai_agent`：启用新的 OpenAI 识图 Agent 链路。

V1 先通过 `.env` 手动切换；前端按钮切换可以后续再做。

## 7. V1 流程设计

### 步骤 1：准备图纸输入

输入：

- 上传的 PDF 文件。
- 现有 PDF 渲染结果。
- 现有 CAD 图框裁切结果。

输出：

- 整页预览图，如果可用。
- 被选中的 CAD 图框图片。
- 图框清单 manifest：

```json
{
  "view_id": "p001_view005",
  "page": 1,
  "bbox_pixel": [0, 0, 100, 100],
  "image_path": "...",
  "priority": 250
}
```

V1 图框选择规则：

- 优先使用 CAD 图框，而不是 3x3 宫格。
- 如果整页预览图不太大，可以额外放入一次，帮助模型理解整体布局。
- 使用 `OPENAI_DRAWING_AGENT_MAX_VIEWS` 控制最大图框数。
- 图框顺序固定：页码、view id。

### 步骤 2：图纸证据抽取

提示词目标：

让 OpenAI 先抽取图纸证据，不直接输出最终清单。

证据 schema：

```json
{
  "drawing_evidence": [
    {
      "view_id": "p001_view005",
      "view_title": "",
      "view_type": "plan/elevation/section/detail/material_schedule/legend/unknown",
      "visible_texts": [],
      "material_codes": [],
      "objects": [],
      "methods": [],
      "spaces": [],
      "evidence_notes": [],
      "confidence": 0.0,
      "needs_manual_review": true
    }
  ]
}
```

关键要求：

- 此步骤不生成最终清单行。
- 只抽取图纸中可见或可追溯的证据。
- 保留每条证据来自哪个 `view_id`。

### 步骤 3：证据合并

系统侧合并：

- 同一材料编号跨图框合并。
- 同一对象 + 同一做法跨图框合并。
- 保留所有来源 `view_id`。
- 清理模型复制的占位文字。
- 弱证据标记为需人工复核。

中间输出：

`outputs/biz2x_trial/debug/{timestamp}/openai_drawing_evidence.json`

这个文件非常重要，用于调试和建立人工信任。

### 步骤 4：清单项目归纳

提示词目标：

让 OpenAI 基于合并后的证据，生成预算员风格四字段候选清单。

清单归纳 schema：

```json
{
  "bill_items": [
    {
      "concrete_item_name": "墙面瓷砖湿贴CT-04",
      "feature": "材料编号CT-04；规格600*600；湿贴；来源p001_view003,p001_view005",
      "unit": "m²",
      "rough_quantity": "",
      "quantity_note": "待复核",
      "source_view_ids": ["p001_view003"],
      "source_evidence": ["CT-04 600*600 白色墙面砖 湿贴"],
      "confidence": 0.0,
      "needs_manual_review": true,
      "reason": ""
    }
  ]
}
```

归纳规则：

- 同一材料、同一做法通常生成一条项目。
- 立面图、节点图重复出现的标注作为证据，不重复生成项目。
- 如果根据图纸范围可合理推断，但证据较弱，可以低置信度列出并标记人工复核。
- 不编造精确工程量。

### 步骤 5：GB/T 国标匹配

复用现有国标搜索：

`concrete_item_name + feature + source_evidence -> search_standard_index`

最终显示名称：

`具体项目名称（国标项目名称）`

示例：

`墙面瓷砖湿贴CT-04（块料墙面）`

V1 需要补一个轻量上下文约束：

- 名称含 `墙面`，优先匹配墙面类标准项。
- 名称含 `地面`，优先匹配楼地面类标准项。
- 名称含 `吊顶`，优先匹配天棚/吊顶类标准项。
- 名称含 `门`，优先匹配门类标准项。

这不是重建国标库，只是减少“墙砖匹配到块料楼地面”这类明显错误。

### 步骤 6：Excel 输出

复用 `quantity_list_export.py`。

Excel 字段：

- `项目名称`
- `项目特征`
- `单位`
- `工程量`

V1 工程量规则：

- 如果模型给出可解释的粗估量，必须标记为待复核。
- 如果没有可靠依据，填 `待复核` 或 `待算量`。
- 避免把候选量伪装成最终工程量。

## 8. API 接入方案

### 方案 A：复用现有接口，加 provider 参数

现有接口：

`POST /api/v1/admin/dwg-quantity-trial/list-items-from-pdf`

新增请求字段：

```json
{
  "provider": "openai_agent"
}
```

如果不传，则读取 `.env` 默认值。

优点：

- 前端和接口改动较小。
- 同一个上传页面可以测试不同 provider。

缺点：

- 现有接口逻辑会变复杂。

### 方案 B：新增独立测试接口

新增接口：

`POST /api/v1/admin/dwg-quantity-trial/list-items-from-pdf-openai-agent`

优点：

- 与现有 GLM 链路隔离，更安全。
- 方便并排比较 GLM 和 OpenAI Agent 输出。

缺点：

- API 和前端会有少量重复。

V1 建议：

如果要最快接入现有上传页面，选方案 A。  
如果要保证现有 GLM 链路不受影响，选方案 B。

## 9. 输出文件规划

每次运行输出：

```text
outputs/biz2x_trial/debug/{timestamp}/openai_drawing_evidence.json
outputs/biz2x_trial/debug/{timestamp}/openai_drawing_bill_items.json
outputs/biz2x_trial/debug/{timestamp}/openai_drawing_agent_report.json
outputs/biz2x_trial/business/{timestamp}/BIZ2x_OpenAI识图四字段清单_{timestamp}.xlsx
outputs/biz2x_trial/business/{timestamp}/BIZ2x_OpenAI识图四字段清单_{timestamp}.csv
```

report 中记录：

- 选中图框数
- 证据条数
- 归纳清单条数
- 国标匹配条数
- provider
- 模型名
- 成功/失败次数
- token/usage，如果接口返回
- warning 列表

## 10. 验收标准

V1 用同一份信达餐厅 PDF 验收，达到以下标准即可进入下一轮优化：

1. Excel 非空。
2. 去重后至少 12 条候选清单。
3. 项目不再被墙面瓷砖大量主导。
4. 至少出现 5 类以上业务项目，例如：
   - 墙面
   - 地面
   - 吊顶
   - 门或洞口
   - 踢脚线 / 金属 / 石材 / 隔断 / 水电证据
5. CT 墙砖按材料编号和做法合并，不因多个立面/节点重复生成多行。
6. 每一行至少有一个证据来源或 source view id。
7. 国标匹配比当前“墙砖误匹配块料楼地面”更合理。
8. 工程量必须明确标记为粗估或待复核。
9. 模型调用部分失败时，不应抹掉已成功生成的证据。

## 11. 风险清单

| 风险 | 影响 | V1 应对 |
| --- | --- | --- |
| OpenAI API key 缺失 | 新链路无法运行 | 保留 GLM 默认链路，报清晰错误 |
| 成本高于 GLM | 试运行成本上升 | 限制最大图框数，记录 usage |
| 图片上下文过大 | 请求慢或失败 | 分批抽取证据 |
| 模型仍偏向 CT 标注 | 清单多样性不足 | 先抽证据，再全局归纳 |
| 国标匹配仍错误 | 标准后缀不准 | 加轻量上下文约束 |
| PDF 是超大 CAD 拼图 | 整页难读 | 复用 CAD 图框裁切 |
| 服务商限流 | 部分或整单失败 | 分批、重试、保留部分成功结果 |
| 模型幻觉项目 | 出现误列项 | 要求证据来源，弱证据标复核 |

## 12. 建议实施任务清单

### 任务 1：配置项

- 新增 `OPENAI_API_KEY`。
- 新增 `OPENAI_VISION_MODEL`。
- 新增 `PDF_ITEMIZATION_PROVIDER`。
- 新增 OpenAI Agent 最大图框数、批大小、超时时间。
- 缺少 key 时返回清晰错误。

### 任务 2：OpenAI Gateway

- 新增 OpenAI 多模态调用封装。
- 返回 parsed JSON 和 raw content。
- 捕获错误与 usage。
- 保持 GLM 函数不变。

### 任务 3：证据抽取

- 从现有 CAD view report 构建图框输入包。
- 调用 OpenAI 抽取结构化证据。
- 保存 evidence JSON。
- 增加 mock 模型响应单元测试。

### 任务 4：证据合并

- 合并重复材料编号和做法。
- 保留 source view ids。
- 清理占位和弱证据文字。
- 保证排序稳定。

### 任务 5：清单归纳

- 把合并证据交给 OpenAI 生成 `bill_items`。
- 解析、校验、清洗 `bill_items`。
- 增加去重和证据追溯测试。

### 任务 6：国标匹配与 Excel 导出

- 把 `bill_items` 转成标准映射行。
- 复用 `search_standard_index`。
- 复用四字段导出。
- 写入 business/debug 输出文件。

### 任务 7：接口接入

- 增加 provider switch 或新增独立 endpoint。
- 保持 GLM 路径可用。
- 返回能比较 provider 效果的 summary。

### 任务 8：真实 PDF smoke test

- 用同一份信达餐厅 PDF。
- 对比：
  - GLM 24 图框运行：25 行，墙砖主导。
  - GLM-5V 限流运行：0 行。
- 人工查看 Excel。
- 记录验收结果。

## 13. 需要用户审核确认的问题

实施前需要确认：

1. V1 是复用现有上传接口加 provider 参数，还是新增独立 OpenAI 测试接口？
2. OpenAI 路线第一版是优先输入 CAD 图框图片，还是尝试直接输入原始 PDF？
3. 每份 PDF 可接受的最高试运行成本是多少？
4. V1 是否允许输出低置信度推断项，还是只输出有明确图纸证据的项目？
5. Excel 是否只保留四个可见字段，证据来源只放 debug JSON？还是 Excel 也增加隐藏/调试列？

## 14. 推荐的 V1 决策

建议第一版这样做：

- 保持 GLM 链路不变。
- 新增独立 OpenAI Agent 测试通道。
- 第一版优先使用 CAD 图框图片，不先直接使用原始 PDF。
- 明确分成“证据抽取”和“清单归纳”两个模型步骤。
- Excel 只输出四个可见字段，证据来源保存在 debug JSON。
- 所有工程量都标记为粗估或待复核。

这样可以最安全地比较两条路线：当前 GLM 路线继续可用，新 OpenAI Agent 路线专门用于验证“是否更接近 Codex 直接看图纸生成清单”的效果。
