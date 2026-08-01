---
title: 多模态 Agent 架构：流水线、资产一致性与局部重做
category: Agent 核心架构
tags:
  - Multimodal Agent
  - Workflow
  - Asset Lineage
  - Evaluator
  - Local Repair
  - State Management
  - Human-in-the-loop
source:
  - title: 如何设计全自动 AI 短剧 Agent 架构
    url: https://www.douyin.com/video/7659794551392996827
reviewed_at: 2026-07-28
status: 持续更新
---

# 多模态 Agent 架构：流水线、资产一致性与局部重做

## 核心结论

多模态 Agent 不是把文本、图片、音频和视频模型串在一起，而是把不稳定的生成过程改造成：

```text
可规划
→ 可执行
→ 可检查
→ 可局部重做
→ 可追踪
→ 可回滚
```

AI 短剧只是一个案例。这套方法同样适用于：

- 图纸识别与工程量清单生成；
- PDF 合同或招标文件分析；
- 商品内容生产；
- 报告、幻灯片和视频自动生成；
- 包含 OCR、视觉模型和 LLM 的企业流程。

真正困难的不是“调用哪个模型”，而是：

1. 怎样定义每个阶段的输入输出；
2. 怎样保证跨阶段资产一致；
3. 怎样判断哪一步出了问题；
4. 怎样只重做受影响部分；
5. 怎样恢复任务并保留审计证据。

> 资料说明：本笔记依据视频公开简介和章节摘要整理，并结合当前 AI 智能报价中台的图纸/PDF 与招标资料 Agent 实现扩展，不是逐字字幕。

---

## 1. 总体架构

一个生产级多模态 Agent 可以拆成控制面和执行面。

### 控制面

负责：

- 需求解析；
- 任务规划；
- 依赖管理；
- 状态机；
- 预算和重试；
- 权限与人工审批；
- 质量门禁；
- 版本和审计。

### 执行面

负责：

- 文本生成；
- OCR；
- 图片理解与生成；
- 音频合成；
- 视频生成；
- 数据库查询；
- 文件转换；
- 结果组装。

```text
用户需求
   ↓
Requirement Spec
   ↓
Planner 生成任务依赖图
   ↓
┌────────────── 执行面 ──────────────┐
│ 文本 │ OCR │ 视觉 │ 图片 │ 音频 │ 视频 │
└────────────────────────────────────┘
   ↓
Artifact Registry
   ↓
Evaluator
   ├── 通过 → 组装与交付
   ├── 局部问题 → Repair Plan → 重跑局部节点
   └── 高风险 → Human Review
```

多 Agent 只是角色划分方式之一。角色可以由多个模型承担，也可以由同一个模型、普通代码和工作流节点共同承担。

---

## 2. 第一步：把自然语言变成需求规格

视频中的短剧需求包括题材、平台、时长和受众。工程系统也需要先把自然语言转成结构化契约。

示例：

```json
{
  "project_id": "project-001",
  "goal": "从施工图生成预算清单候选",
  "input_files": [
    {
      "file_id": "file-001",
      "sha256": "...",
      "type": "drawing_pdf"
    }
  ],
  "required_outputs": [
    "evidence_manifest",
    "quantity_list",
    "review_report"
  ],
  "quality_policy": "manual_review_on_low_confidence",
  "deadline": "2026-07-30T18:00:00+08:00"
}
```

### 需求规格至少应包含

- 目标；
- 输入资产；
- 输出类型；
- 质量标准；
- 成本或 Token 预算；
- 时限；
- 权限与可见范围；
- 人工确认点；
- 失败时的降级规则。

如果这些内容只存在于 Prompt 中，后续节点无法可靠判断是否完成任务。

---

## 3. 第二步：把生产过程拆成有契约的阶段

AI 短剧可以拆成：

```text
需求 → 大纲 → 角色卡 → 场景卡 → 分镜 → 图片 → 视频 → 音频 → 成片
```

图纸报价可以拆成：

```text
PDF
→ 页面渲染
→ 视图与区域规划
→ OCR / 视觉识别
→ 证据合并
→ 清单候选
→ 标准库匹配
→ 质量复核
→ Excel
```

### 每个阶段都要定义

| 字段 | 作用 |
|---|---|
| `stage_id` | 稳定的阶段标识 |
| `input_schema` | 能接收什么 |
| `output_schema` | 必须产出什么 |
| `preconditions` | 开始前必须满足什么 |
| `postconditions` | 怎样算完成 |
| `timeout` | 最长执行时间 |
| `retry_policy` | 哪些错误可重试 |
| `idempotency_key` | 防止重复副作用 |
| `quality_gate` | 通过标准 |
| `fallback` | 失败后的降级路线 |

没有阶段契约，流水线只能知道函数是否报错，无法知道业务结果是否可用。

---

## 4. 第三步：建立资产注册表

多模态任务的中间结果不是临时变量，而是需要管理的资产。

短剧资产包括：

- 角色卡；
- 场景卡；
- 分镜；
- 图片；
- 音频；
- 视频片段。

工程项目资产包括：

- 原始 PDF；
- 渲染页；
- Tile 和局部 Crop；
- OCR 结果；
- 视觉证据；
- 清单候选；
- 标准库匹配结果；
- Excel 和审核报告。

### 推荐资产模型

```json
{
  "asset_id": "asset-uuid",
  "asset_type": "drawing_evidence",
  "version": 3,
  "content_hash": "sha256:...",
  "status": "ready",
  "producer_stage": "vision_extract",
  "source_asset_ids": ["crop-17", "ocr-17"],
  "model_version": "provider/model",
  "prompt_version": "vision-v4",
  "quality": {
    "score": 0.86,
    "review_required": false
  },
  "created_at": "...",
  "supersedes": "asset-version-2"
}
```

### 为什么不能只保存文件路径

文件路径不能可靠回答：

- 文件内容是否被覆盖；
- 它由哪些输入生成；
- 使用了哪个模型和 Prompt；
- 哪些下游结果依赖它；
- 修改后哪些结果已经过期；
- 是否经过质量检查。

至少需要稳定 ID、内容哈希、版本和来源关系。

---

## 5. 资产一致性

### 引用稳定 ID

下游资产应引用 `asset_id + version`，而不是“最新版角色图.png”或“最终版2.xlsx”。

### 不覆盖旧版本

推荐：

```text
asset v1 → 已交付
asset v2 → 修改后候选
asset v3 → 当前生效
```

旧版本保留用于：

- 回滚；
- 审计；
- 对比；
- 重放。

### 显式记录依赖

```text
角色卡 v3
├── 分镜 7 v2
│   ├── 图片 7 v4
│   └── 视频片段 7 v1
└── 分镜 12 v1
    └── 图片 12 v2
```

如果角色外观发生变化，系统才能判断哪些分镜、图片和视频需要失效。

### 用结构化约束保持一致

一致性不应只靠 Prompt 提醒。可以使用：

- Schema；
- 枚举；
- 业务对象 ID；
- 参考图或 Embedding；
- 哈希；
- 规则校验；
- 版本锁；
- 受控词典。

---

## 6. Planner、Worker、Evaluator 和 Repair Controller

### Planner

负责：

- 将目标拆成阶段；
- 生成依赖图；
- 分配模型和工具；
- 设置预算；
- 决定可并行节点。

Planner 不直接修改业务资产。

### Worker

负责执行具体任务，例如：

- OCR；
- 视觉证据抽取；
- 分镜生成；
- 图片生成；
- 清单归纳；
- 标准库匹配。

Worker 只应输出符合契约的资产或结构化错误。

### Evaluator

负责检查：

- Schema；
- 完整性；
- 一致性；
- 事实与证据；
- 安全；
- 业务规则；
- 视觉或文本质量。

### Repair Controller

负责把质量问题转换为最小重做计划：

```json
{
  "issue_id": "issue-009",
  "failed_asset_id": "image-shot-12-v2",
  "rule": "character_identity_consistency",
  "severity": "high",
  "repair_scope": ["image-shot-12"],
  "invalidate_downstream": ["video-shot-12"],
  "max_attempts": 2,
  "fallback": "human_review"
}
```

Repair Controller 应由受控逻辑决定重做范围，不能让生成模型无限自我循环。

---

## 7. Evaluator 怎样设计

### 确定性检查优先

先用代码检查：

- JSON 是否符合 Schema；
- 必填字段是否齐全；
- 文件是否存在；
- 数量和合计是否一致；
- 引用的资产版本是否存在；
- 输出尺寸和格式是否正确；
- 所有需求行是否被覆盖。

### 模型评估用于模糊质量

模型更适合检查：

- 角色形象是否一致；
- 分镜是否符合剧情；
- 图纸证据是否支持清单项；
- 摘要是否遗漏关键语义；
- 表达是否符合目标受众。

### 生成者不能是唯一裁判

同一模型可能重复自己的偏差。高风险任务可组合：

```text
确定性规则
+ 独立模型评估
+ 业务证据
+ 人工确认
```

### Evaluator 必须输出可执行问题

差的评估：

```text
整体质量一般，需要优化。
```

好的评估：

```json
{
  "asset_id": "bill-item-27-v1",
  "rule": "evidence_required",
  "severity": "blocking",
  "message": "清单项没有 source_view_id",
  "repair_action": "rerun_evidence_linking"
}
```

---

## 8. 局部重做

### 为什么不能整条流水线重跑

整条重跑会导致：

- 已通过资产被重新生成；
- 新旧结果出现更多差异；
- 成本和延迟失控；
- 人工已确认内容丢失；
- 问题难以复现。

### 局部重做算法

```text
发现失败资产
→ 判断根因所在节点
→ 找到受影响的下游依赖
→ 将这些资产标记为 stale
→ 保留无关且已通过的资产
→ 重跑最小节点集合
→ 重新执行相关质量门禁
```

### 图纸项目示例

假设某个局部图框 OCR 质量低：

```text
原始 PDF                  保留
页面渲染                  保留
其他 Tile/Crop            保留
问题 Crop                 提高清晰度后重做
该 Crop 的 OCR/视觉证据    重做
受影响的证据合并           重做
相关清单候选               重做
相关标准库匹配             重做
最终审核报告               更新
```

不应重新识别全部 PDF。

### 防止无限循环

需要：

- 节点级最大尝试次数；
- 项目级 Token/费用预算；
- 相同错误指纹检测；
- 修复后必须重新评估；
- 超预算转人工；
- 保留所有尝试和失败原因。

---

## 9. 状态管理

### 推荐状态

```text
created
→ queued
→ running
→ waiting_review
→ completed

running
├── retryable_failed → queued
├── completed_with_review
├── failed
└── cancelled
```

### 任务记录至少包含

- `run_id`；
- 当前阶段；
- 当前状态；
- 进度；
- 尝试次数与上限；
- 输入资产版本；
- 输出资产版本；
- 依赖关系；
- 模型、Prompt 和工具版本；
- 错误码与错误信息；
- 租约或 Worker 所有权；
- 开始、更新时间和结束时间；
- 人工决定；
- 成本与 Token。

### 事件应追加写入

不要只覆盖“当前状态”。还要保留：

```text
run_created
stage_started
asset_created
quality_failed
repair_scheduled
stage_retried
human_review_requested
run_completed
```

事件流可以回答“任务为什么变成现在这样”。

---

## 10. 并行执行与依赖控制

依赖图允许安全并行：

```text
          ┌→ 角色设计 ─┐
需求解析 ─┼→ 场景设计 ─┼→ 分镜
          └→ 风格设计 ─┘
```

但只有满足以下条件才能并行：

- 不修改同一可变资产；
- 输入版本已经冻结；
- 合并规则明确；
- 失败能独立处理；
- 并发和供应商限流允许。

并行不是越多越好。图片、视频和视觉模型通常昂贵，应设置：

- 队列优先级；
- 租户配额；
- Provider 并发上限；
- 单项目预算；
- 背压；
- 超时和取消传播。

---

## 11. 当前 AI 智能报价中台的实现映射

### 已经具备的多模态流水线

当前 PDF Agent 主链路已经覆盖：

```text
PDF 收集
→ 基础解析与页面渲染
→ Tile/CAD 视图和区域规划
→ OCR
→ 视觉证据抽取
→ 证据合并
→ 清单候选归纳
→ 可列项性分类
→ 国标匹配
→ 质量复核
→ 业务 Excel 与 Debug 产物
```

主要实现在：

- `drawing_pdf_agent_itemizer.py`
- `drawing_pdf_evidence_pipeline.py`
- `drawing_agent_runtime.py`
- `drawing_ocr_quality_scorer.py`
- `drawing_regression_evaluator.py`

### 已有资产和一致性基础

1. 原始招标资料通过 `file_objects` 和不可变 `source_object` 保存。
2. 源文件记录 `sha256`，解析任务绑定 `parser_version`。
3. 图纸链路区分页面、Crop、OCR、视觉、Context、Items 和 Reports 目录。
4. 证据记录 `view_id/source_view_id`，弱证据和低置信度结果进入人工复核。
5. 清单归纳与国标匹配分开，模型不直接完成全部业务动作。
6. 输出同时保留业务结果和 Debug 证据。

### 已有状态与恢复基础

`DrawingAgentRunTracker` 已记录：

- `run_id`；
- `status/stage/progress`；
- 产物目录；
- Warning、Error 和 Issue；
- `completed_with_review`；
- `failed_with_report`；
- JSONL 事件。

招标资料解析任务还具备：

- `attempt_count/max_attempts`；
- `parser_version`；
- 阶段和状态；
- 错误码；
- 追加式任务事件。

报价资料研判 Agent 已进一步具备 SQL Checkpoint、Manifest/Policy 版本检查、事件 Trace 和 Human-in-the-loop 恢复。这些机制可以作为未来图纸 Agent 状态持久化的参考。

### 当前缺口

#### 缺口一：没有统一资产依赖图

现有目录和报告能追踪很多产物，但还没有统一表达：

```text
哪个资产版本
由哪些输入生成
哪些下游结果依赖它
```

因此上游变更后，难以自动计算最小失效范围。

#### 缺口二：局部重做控制器尚不完整

当前代码已有高分辨率 Crop、OCR 质量评分、部分成功状态和人工复核，但主 Itemizer 中没有看到完整的：

```text
Evaluator Issue
→ Repair Plan
→ 单 Crop/单 View 重试
→ 下游局部失效
→ 局部重新组装
```

`zooming_unclear_regions` 已出现在运行阶段定义中，但不能仅凭阶段名视为上述闭环已经实现。

#### 缺口三：图纸运行状态主要保存为本地产物

当前 Run Tracker 通过工作目录中的 JSON/JSONL 保存状态和事件，适合单机调试与审计，但若进入多 Worker、跨机器生产环境，还需要数据库或可靠对象存储、租约和并发控制。

#### 缺口四：阶段失败粒度仍可细化

证据抽取或清单归纳失败可以生成 Warning 和失败报告，但还缺少统一的节点级 Checkpoint，使已成功的局部证据可以跨重试直接复用。

---

## 12. 推荐升级顺序

### P0：统一 Artifact Manifest

先为现有产物补齐：

- 资产 ID；
- 类型；
- 版本；
- 内容哈希；
- 来源资产；
- 生产阶段；
- 模型/Prompt 版本；
- 质量状态。

不必立即拆成多个微服务。

### P1：把阶段结果变成可恢复 Checkpoint

每个阶段完成后持久化：

```text
input_hash
output_asset_ids
stage_version
status
metrics
```

输入和阶段版本未变时可以复用。

### P2：标准化 Evaluator Issue

将 OCR、视觉、完整性和人工复核问题统一为：

```text
issue_id
asset_id
rule
severity
repair_action
repair_scope
```

### P3：实现最小局部重做

先选一个高价值场景：

> 对低质量 Crop 重新高分辨率渲染和 OCR，只更新相关证据及清单候选。

验证稳定后再扩展到更多节点。

### P4：生产化状态和调度

复用招标研判 Agent 已有经验：

- 数据库状态；
- Worker 租约；
- 最大尝试；
- Checkpoint；
- 版本门禁；
- Human-in-the-loop；
- 事件 Trace。

### P5：增加多模态质量与成本指标

- 每页、每 Crop 成功率；
- OCR 高/中/低质量分布；
- 视觉调用失败率；
- 局部重做率；
- 人工复核率；
- 每份 PDF Token/费用；
- 首次通过率；
- 从失败恢复的时间；
- 资产复用率。

---

## 13. 常见失败模式

### 需求漂移

用户修改上游需求后，下游仍引用旧版本。

### 资产身份漂移

角色、场景或业务对象没有稳定 ID，只靠自然语言名称关联。

### 全量重跑

一个局部错误导致全部资产重新生成，成本高且差异不可控。

### 脏下游

上游资产已修改，下游仍被标记为可交付。

### 无限自修复

模型反复生成和评价，没有预算、尝试上限或人工出口。

### 部分成功被覆盖

某批次只有一个子任务失败，却丢失全部成功产物。

### 副作用重复

重试时重复上传、推送、扣费或发布。

### 只有最终文件，没有过程证据

无法回答某个结果来自哪张图、哪个模型和哪次尝试。

---

## 14. 面试表达

### 问：怎样设计一个全自动多模态 Agent？

> 我会先把自然语言需求转成结构化规格，再由 Planner 生成任务依赖图。文本、视觉、图片和音视频能力作为受控 Worker，每一步产出带版本、哈希和来源关系的资产。Evaluator 先用确定性规则检查 Schema、完整性和引用，再用模型检查模糊质量。失败后由 Repair Controller 计算最小重做范围，只使受影响的下游资产失效。所有阶段通过状态机、Checkpoint、事件 Trace、预算和 Human-in-the-loop 管理。

### 问：怎样保证角色或业务对象一致？

> 不依赖 Prompt 反复描述，而是给核心资产稳定 ID 和版本，下游显式引用资产版本；同时使用结构化 Schema、参考图或证据、哈希和规则校验。上游版本变化时，根据依赖图把受影响下游标为 stale。

### 问：怎样实现局部重做？

> Evaluator 输出结构化 Issue，定位失败资产和修复动作；系统沿资产依赖图计算受影响的下游闭包，只重跑最小节点集合。重跑必须有幂等键、最大尝试、预算和人工兜底，并保留旧版本用于回滚。

### 项目表达

> 我的报价中台已经有真实的多模态图纸链路：PDF 渲染、视图规划、OCR、视觉证据、证据合并、清单归纳、国标匹配、质量复核和 Excel 输出。运行时记录阶段、产物目录、事件、人工复核和失败报告。下一步不是再加一个视觉模型，而是补统一 Artifact Manifest、节点 Checkpoint 和 Evaluator 驱动的局部重做，让单个低质量 Crop 失败时只重跑相关证据和清单项。

---

## 15. 检查清单

### 流水线

- [ ] 用户需求已经结构化
- [ ] 每个阶段有输入输出 Schema
- [ ] 阶段有超时、重试、幂等和降级策略
- [ ] 可并行节点有明确的合并规则
- [ ] 外部副作用与生成阶段分离

### 资产

- [ ] 每个资产有稳定 ID、版本和内容哈希
- [ ] 记录生产阶段、模型和 Prompt 版本
- [ ] 显式保存来源资产和下游依赖
- [ ] 新版本不覆盖旧版本
- [ ] 上游变化会使相关下游失效

### 质量与修复

- [ ] 确定性规则优先于模型评估
- [ ] Evaluator 输出结构化 Issue
- [ ] 局部重做范围可计算
- [ ] 修复后重新经过质量门禁
- [ ] 有最大尝试、预算和人工出口

### 运行时

- [ ] 状态和事件可以持久化
- [ ] 节点结果可以从 Checkpoint 恢复
- [ ] 部分成功不会因单点失败丢失
- [ ] 多 Worker 场景有租约和并发控制
- [ ] 能追踪每次运行的质量、成本和延迟

## 关联笔记

- [Agent 技术栈全景图与数据流](./Agent技术栈全景图与数据流.md)
- [生产级 Agent 系统架构：从 Gateway 到部署运维](./生产级Agent系统架构.md)
- [Agent 任务可靠性：租约、幂等、调度与状态恢复](../03-生产级开发基础/Agent任务可靠性-租约幂等调度与状态恢复.md)
- [Agent 可靠性工程：上下文、护栏、状态、评测与追踪](../03-生产级开发基础/Agent可靠性工程-上下文护栏状态评测与追踪.md)
- [Agent 评测体系：事实、过程、工具、效率、安全与版本治理](../10-LLMOps与可观测性/Agent评测体系-事实过程工具效率安全与版本治理.md)
- [RAG 引用与证据链：从片段溯源到结论级对齐](../08-RAG与Embedding/RAG引用与证据链-从片段溯源到结论级对齐.md)
