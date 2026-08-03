# BIZ-2x R0-R9 标准库约束型动态列项实现说明

日期：2026-06-16

## 目标

本轮把原先“样例答案反推规则”的路线，调整为可长期扩展的工程链路：

```text
DXF/PDF 证据
  -> GB/T 标准库索引
  -> 图纸专业/算量场景路由
  -> 标准项目候选召回
  -> LLM-ready 动态列项 JSON 决策
  -> 程序硬校验
  -> 分类算量/人工补量
  -> 人工确认
  -> 四字段 Excel
  -> 反馈闭环
```

核心原则：

- 不再要求人工提前制定完整、周全的细规则库。
- LLM 只做受约束判断，不能编造国标编码、单位或工程量。
- 项目名称、项目特征、单位必须落在标准库与图纸证据约束内。
- 工程量必须来自 DXF/PDF 证据或人工补量；无可靠证据时阻断最终导出。

## 本轮新增

### R0/R3 标准库统一索引

新增服务：

- `AI_Middle_Office/app/services/quantity_standard_index.py`

能力：

- 加载 `AI_Middle_Office/data/standards/standard_library_index.json`
- 统一管理：
  - `GB/T 50854-2024` 房屋建筑与装饰工程项目库
  - `GB/T 50856-2024` 通用安装工程项目库
  - `GB/T 50500-2024` 计价规则库
- 支持跨标准库召回。
- 支持基础专业路由：
  - 装饰/建筑类优先 `50854`
  - 电气/给排水/洁具/灯具/开关等安装类优先 `50856`
- 支持少量标准归并提示，例如“地漏”归到 `031003014 给、排水附件`。

### R0-R9 动态列项服务

新增服务：

- `AI_Middle_Office/app/services/drawing_dynamic_itemization.py`

能力：

- 把 DXF/PDF 字段收敛结果或手工 evidence signal 统一为证据线索。
- 为每条线索生成：
  - R2 图纸专业/场景路由
  - R3 标准项目候选
  - R4 LLM 决策 JSON schema 与 prompt payload
  - R5 程序硬校验结果
  - R6 工程量证据状态
  - R7 人工确认包
  - R8 四字段 Excel 导出门槛
  - R9 反馈 hook
- 未配置外部 LLM 时，使用确定性 top candidate fallback 跑通链路。
- 配置 `AGENT_LLM_PROVIDER=deepseek` 且 `DEEPSEEK_API_KEY` 有效时，R4 会通过模型网关调用 DeepSeek，并按 `LLM_DECISION_SCHEMA` 回填决策，仍然经过硬校验。

### 命令行预览脚本

新增脚本：

- `AI_Middle_Office/scripts/biz2x_r0_r9_pipeline_preview.py`

示例：

```powershell
C:\Users\12521\miniconda3\python.exe AI_Middle_Office\scripts\biz2x_r0_r9_pipeline_preview.py `
  --signal "CT-01 750x1500灰色地砖地面" `
  --signal "配电箱 AL-01" `
  --signal "地漏供货及安装" `
  --output-dir outputs\biz2x_r0_r9
```

本轮样例输出：

- `outputs/biz2x_r0_r9/BIZ2x_R0_R9_DXF_PDF_国标_LLM动态列项_20260616_202240.json`
- `outputs/biz2x_r0_r9/BIZ2x_R0_R9_DXF_PDF_国标_LLM动态列项_20260616_202240.md`
- `outputs/biz2x_r0_r9/BIZ2x_R0_R9_DXF_PDF_国标_LLM动态列项_20260616_202240_itemization_decisions.csv`
- `outputs/biz2x_r0_r9/BIZ2x_R0_R9_DXF_PDF_国标_LLM动态列项_20260616_202240_manual_confirmation.xlsx`

样例结论：

- `CT-01 750x1500灰色地砖地面` -> `GB/T 50854-2024 011102003 块料楼地面`
- `配电箱 AL-01` -> `GB/T 50856-2024 030402011 成套配电箱`
- `地漏供货及安装` -> 显示细支为“地漏供货及安装”，标准上位项目为 `GB/T 50856-2024 031003014 给、排水附件`

三条样例均通过 R5 标准硬校验，但因缺少可靠工程量证据，R6/R8 正确阻断，进入 R7 人工确认。

### DWG 上传链路旁路接入

修改：

- `AI_Middle_Office/app/services/dwg_item_listing.py`
- `AI_Middle_Office/app/api/v1/dwg_quantity_trial.py`

接入方式：

- 在 DXF 字段收敛 `field_report` 生成后，旁路生成 R0-R9 动态列项报告。
- 不替换旧的 BIZ-2x 标准匹配、项目识别、专项算量、四字段草稿路径。
- `/admin/dwg-quantity-trial/list-items` 返回新增：
  - `dynamic_itemization_summary`
  - `dynamic_itemization_stage_results`
  - `dynamic_itemization_decision_rows`
  - `has_dynamic_itemization`
- 下载文件新增：
  - R0-R9 动态列项 JSON
  - R0-R9 动态列项 Markdown
  - R0-R9 动态列项 CSV
  - R0-R9 动态列项人工确认表

## 验证

新增测试：

- `AI_Middle_Office/tests/test_quantity_standard_index_biz2x_r0.py`
- `AI_Middle_Office/tests/test_drawing_dynamic_itemization_biz2x_r0_r9.py`

已执行：

```powershell
C:\Users\12521\miniconda3\python.exe -m pytest `
  AI_Middle_Office\tests\test_quantity_standard_library_biz2x1.py `
  AI_Middle_Office\tests\test_drawing_standard_matcher_biz2x4.py `
  AI_Middle_Office\tests\test_drawing_quantity_confirmation_biz2x6.py `
  AI_Middle_Office\tests\test_quantity_standard_index_biz2x_r0.py `
  AI_Middle_Office\tests\test_drawing_dynamic_itemization_biz2x_r0_r9.py -q
```

结果：

```text
20 passed, 1 warning
```

接入 DWG API 后补充执行：

```powershell
C:\Users\12521\miniconda3\python.exe -m pytest `
  AI_Middle_Office\tests\test_dwg_quantity_trial_biz2x.py `
  AI_Middle_Office\tests\test_quantity_standard_index_biz2x_r0.py `
  AI_Middle_Office\tests\test_drawing_dynamic_itemization_biz2x_r0_r9.py -q
```

结果：

```text
17 passed, 1 warning
```

warning 均为现有 `.pytest_cache` 权限问题，不影响功能判断。

## 2026-06-16 R4 LLM runtime 补充

- `drawing_dynamic_itemization.py` 新增 `build_dynamic_itemization_report_with_llm` 和 `build_dynamic_itemization_report_runtime`。
- 当 `AGENT_LLM_PROVIDER=deepseek` 且 `DEEPSEEK_API_KEY` 已配置时，R4 会通过 `model_gateway.post_json_via_gateway` 调用 DeepSeek，`endpoint_type` 为 `drawing_dynamic_itemization`。
- LLM 只允许返回 `LLM_DECISION_SCHEMA` 约束内的 JSON 决策；项目编码、单位、特征字段和工程量仍必须经过 R5 程序硬校验。
- 当 provider 不是 `deepseek`、缺少 key、无候选项或单条调用失败时，系统自动回退到确定性候选，不中断 R0-R9 报告生成。
- `biz2x_r0_r9_pipeline_preview.py` 新增 `--use-llm`，可单独验证 R4 真实 LLM 动态列项。
- `dwg_quantity_trial.py` 的 DWG 上传处理改为 `asyncio.to_thread(...)`，避免长任务阻塞事件循环，并允许同步 DWG 流程在后台线程中使用 runtime LLM。
- `/api/v1/admin/dwg-quantity-trial/validate-confirmation` 新增通用 R0-R9/BIZ-2x-6 人工确认表校验入口：业务员补齐确认表后上传，校验通过才导出最终四字段 Excel。
- 2026-06-16 实测样例输出位于 `outputs/biz2x_r0_r9_llm/`：3 条证据信号均由 DeepSeek 返回 JSON 决策，R5 硬校验全通过；因缺少可靠工程量证据，R6/R8 继续阻断最终四字段 Excel。

## 当前边界

- 已完成 R0-R9 的工程骨架、数据契约、标准库召回、LLM-ready JSON schema、硬校验、确认包和 DWG 上传旁路接入。
- 当前已支持可选 DeepSeek runtime；未配置或调用失败时会回退到确定性候选，并在 `llm_runtime` 中记录原因。
- 当前不会在缺少可靠数量证据时自动生成最终工程量。
- 当前不会把历史样例清单硬编码成全量规则库。
- R3-3c 地面小面积闭合候选仍不能作为地面工程量，动态列项链路会正确把这类结果留在人工确认/待算量状态。
