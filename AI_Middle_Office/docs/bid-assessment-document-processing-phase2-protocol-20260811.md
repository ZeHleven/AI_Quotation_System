# 报价资料研判 Agent Phase 2 文档解析与标段候选协议

> 状态：冻结，作为 v0.1-r16、Document Worker、API-30、API-31 与 API-32 的实现基线  
> 日期：2026-08-11  
> 适用数据域：新的 `bid_assessment` 数据域  
> 不适用：旧 `bid_intake_*`、旧 `app.models.bidding.BidParseRun`、旧 Evidence Store 的持久化真相
> 实现记录：代码已线性新增 `20260811_0092`、`20260811_0093` 并实现 Worker、投影、API-30、API-31 与 API-32。API-31 原子固化不可变 Scope，写入 `lot.selected -> plan.requested` 因果事件链；API-32 创建新 Assessment、自有 Manifest ACL 和不依赖源候选外键的 Scope 快照，写入 `assessment.created -> plan.requested`，均不在 Phase 3 版本集合就绪前伪造 Run。迁移复审确认 API-31/API-32 无需新增 revision，代码 head 保持 `20260811_0093`。此前经用户授权的合同、迁移、API-15/20/21/22/30、标段规则与 Worker 纯数据库状态机专项合计 `134 passed`；API-31 合同、API-03/API-30/SSE 相邻回归、迁移拓扑和状态机收口专项为 `123 passed, 1 warning`。本次经用户授权的 API-32 合同、事务/ACL/幂等、API-03/API-20/API-21/API-30、公共事件/SSE 与迁移拓扑专项为 `37 passed`；唯一告警为既有 `pytest-asyncio` 默认 loop scope 弃用提示。全部测试使用合成结构化数据，未运行真实样例、OCR/视觉解析或模型调用。

## 1. 目的和边界

本协议冻结 Phase 2 的以下边界：

- 不可变 DocumentVersion 的解析运行、重试和权威结果；
- 页、Sheet、图片/OCR 的稳定定位与证据来源；
- Document Worker 的消费、租约、fencing、事件和失败恢复；
- Manifest 变化、解析器升级和解析结果变化后的失效规则；
- LotCandidate 的生成输入、证据约束和检测代际；
- API-30 的只读投影、状态、缓存与错误边界；
- 创建新 Alembic revision 前必须满足的门禁。

本阶段不得恢复或写入旧 `bid_intake_*`，不得复用旧 `bid_parse_runs` 作为新数据域真相。可以通过纯适配器复用现有文件读取和解析算法，但适配器输出只能写入本协议定义的新表。

## 2. 不变量

1. 原始文件内容由不可变 `BidDocumentVersion -> BidFileObject` 标识。
2. 文件名、扩展名、客户端 MIME、服务端 MIME 和 `parser_hint` 只能参与解析器路由或安全校验，禁止直接生成标段、标段证据或置信度。
3. 页、Sheet、单元格、OCR 文本和 bbox 只能来自已完成的权威 ParseRun。
4. ParseRun 属于 DocumentVersion；LotDetectionRun 属于精确 Manifest 和精确 ParseSet。
5. 已完成 ParseRun、ParseUnit、EvidenceFragment、LotDetectionRun 和 LotCandidate 均不可原地改写。
6. 可变“当前结果”只能通过 Head 表切换；历史结果必须保留。
7. 一个 Assessment 只能绑定一个标段 Scope；其他标段必须通过 API-32 建立独立 Assessment。
8. GET API-30 是纯读取接口，禁止隐式创建任务、重新解析或生成候选。
9. 只有内容证据可以支持 LotCandidate；没有直接内容证据的模型输出不得持久化为候选。
10. 分析 Run 只能在解析集合就绪且标段已经唯一绑定后创建。

## 3. 解析运行数据域

### 3.1 `bid_document_parse_runs`

一行表示对一个不可变 DocumentVersion、确定 Parser Profile 和确定输入执行的一次逻辑解析。

关键字段：

| 字段 | 约束 |
|---|---|
| `id` | 应用生成 ID，主键 |
| `document_version_id` | FK `bid_document_versions.id` |
| `parser_profile_version` | 已冻结解析配置版本，禁止仅记录库版本 |
| `input_hash` | DocumentVersion 内容哈希、Parser Profile 和解析策略的确定性哈希 |
| `status` | `queued/running/succeeded/partial/failed` |
| `retryable` | 最终失败是否允许人工或协调器重新调度 |
| `requested_at/started_at/finished_at` | UTC；按状态约束可空性 |
| `result_ref/result_hash` | 大结果受控引用及完整性哈希；不得经 API 暴露对象键 |
| `quality_grade/quality_score` | `high/medium/low` 与可空 0–100 整数 |
| `page_count/sheet_count` | 已持久化权威单元数 |
| `ocr_status` | 本协议第 4.3 节枚举 |
| `warning_count/warnings_json/error_code` | 脱敏汇总；内部异常写 Attempt/Event |
| `row_version` | 乐观锁，仅用于非终态调度状态 |

唯一约束：

`(document_version_id, parser_profile_version, input_hash)`。

同一唯一键的重复调度必须返回已有 Run；不得并行创建第二个逻辑 Run。

### 3.2 `bid_document_parse_heads`

一行表示 DocumentVersion 当前对外权威解析结果：

- PK/FK `document_version_id`；
- FK `current_run_id`；
- `row_version`、`updated_at`；
- `current_run_id` 必须属于同一 DocumentVersion。

没有 Head 时，API-20/21 投影 `not_requested`。有 Head 时，API 状态完全来自所指 ParseRun。DocumentVersion 不增加可变解析字段。

### 3.3 `bid_document_parse_attempts`

每次 Worker 尝试一行，关键字段：

- `run_id`、`attempt_no`，唯一 `(run_id, attempt_no)`；
- `status=leased/running/succeeded/failed/expired/cancelled`；
- `lease_owner`、`lease_until`、`heartbeat_at`；
- 单调递增 `fencing_token`；
- `error_class`、稳定 `error_code`、`retryable`；
- `started_at`、`finished_at`。

所有运行状态、结果和终态写入必须校验当前 fencing token。过期 Attempt 只能记录自身终态，不能发布权威结果。

### 3.4 `bid_document_parse_events`

这是解析域内部的追加式状态与进度账本，不是 Public Event：

- PK `id`；FK `run_id`、可空 `attempt_id`；
- `sequence_no`，唯一 `(run_id, sequence_no)`；
- `event_type`、`from_status`、`to_status`；
- 脱敏 `payload_json`、`payload_hash`；
- `created_at`。

心跳、临时失败、重租约和单元进度只写本表，不广播给前端。

## 4. 页、Sheet 与 OCR 权威来源

### 4.1 `bid_document_parse_units`

一个成功或部分成功 ParseRun 包含零个或多个不可变 ParseUnit。

关键字段：

- `run_id`；
- `unit_type=document/page/sheet/image`；
- `unit_key` 和 `ordinal`，唯一 `(run_id, unit_type, unit_key)`；
- PDF/DOCX：`page_no`、可空 `section_path_json`；
- Excel：`sheet_index`、`sheet_name`、可空 `cell_range`；
- 图片/OCR：可空 `image_index`；
- `content_source=native/ocr/mixed/none`；
- `status=succeeded/partial/failed/skipped`；
- `text_hash`、`text_length`、`result_ref`；
- `ocr_status`、`ocr_engine_version`、可空 `ocr_confidence`；
- `warnings_json`、`metrics_json`。

数据库 Check 约束必须阻止：

- page 单元缺失正整数 `page_no`；
- sheet 单元缺失 `sheet_index/sheet_name`；
- `content_source=ocr/mixed` 却没有可解释 OCR 状态；
- 同一 Run 中重复页或重复 Sheet 身份。

### 4.2 `bid_evidence_fragments`

EvidenceFragment 是 Phase 2 内容证据的最小权威单位：

- FK `parse_run_id`、`document_version_id`、`parse_unit_id`；
- `locator_type=document/page_bbox/sheet_range/image_bbox/section`；
- `locator_json` 和确定性 `locator_hash`；
- `normalized_text`、`text_hash`；
- 可空 `parent_id`、`ordinal`、`object_ref`；
- 唯一 `(document_version_id, parse_run_id, locator_hash, text_hash)`。

必须通过 FK 或 Service 校验 Fragment、Unit、Run 均属于同一 DocumentVersion。搜索命中可以引用 Fragment，但搜索命中本身不是新的直接证据。

### 4.3 OCR 状态

OCR 状态统一为：

`not_applicable | not_requested | queued | running | succeeded | partial | failed`

- 原生文本已满足质量门槛时为 `not_applicable`；
- 尚未触发 OCR 时为 `not_requested`；
- 扫描页不得因为 PDF MIME 被当作已有正文；
- 混合文档必须逐页记录 `native/ocr/mixed/none`，Run 汇总不得掩盖低质量页；
- OCR 结果只有在写入 ParseUnit 和 EvidenceFragment 后才成为权威来源。

质量等级由冻结的 Parser Profile 使用文本覆盖率、失败/隔离单元数量、OCR 置信度和结构完整度确定；禁止由文件名或模型自由文本决定。

## 5. 调度、消费和失败恢复

### 5.1 创建与复用

API-15 提交新 Manifest 时，对 Manifest 中每个没有当前 Parser Profile 可复用结果的 DocumentVersion（包括 Phase 2 启用前已存在的沿用版本），在同一事务中：

1. 创建或复用唯一 ParseRun；
2. 创建/切换 ParseHead 指向该 Run；
3. 写 `bid.document.parse_requested.v1` Outbox；
4. 事件中携带 `parse_run_id`，不得只携带 DocumentVersion ID。

已有同 Parser Profile、同 input hash 的 `succeeded/partial` Run 时，复用结果并由 Manifest 协调器重新计算 ParseSet，不重复解析。已有 `queued/running` Run 时只复用，不重复发起并行逻辑 Run。

### 5.2 消费者边界

Outbox 派发必须支持独立消费者：

- `bid-public-event-projector`：只生成脱敏 Public Event；
- `bid-document-worker`：只消费解析调度事件；
- `bid-manifest-parse-coordinator`：根据解析终态重算 Manifest ParseSet；
- `bid-lot-detection-worker`：消费标段检测请求。

每个消费者使用独立 `consumer_name + event_id` processed marker。公开事件投影不能承担工作流推进。

### 5.3 终态事务

成功、部分成功或最终失败必须在一个数据库事务内完成：

- 校验 fencing token；
- 写 ParseUnit、EvidenceFragment 和结果引用；
- 推进 ParseRun 终态；
- 追加 ParseEvent；
- 写对应 Outbox；
- 写消费者 processed marker。

事务回滚后不得留下对外可见的半套解析结果。对象存储临时结果由引用感知孤儿清理处理；禁止按前缀或未解析环境变量批量删除。

## 6. Manifest ParseSet 与失效

Manifest ParseSet 由以下有序输入计算确定性 `parse_set_hash`：

- Manifest ID、Manifest hash；
- 每个 ManifestDocument 的角色和顺序；
- DocumentVersion ID；
- 当前权威 ParseRun ID、status、result hash；
- Parser Profile version。

规则：

- DocumentVersion 解析结果可以被多个可见 Manifest 复用；
- LotDetectionRun 和 LotCandidate 不能跨 Manifest 复用；
- 新 Manifest 必须拥有独立 LotDetectionHead；
- 同一 Manifest 的 ParseHead 或 Parser Profile 变化导致 `parse_set_hash` 变化，旧检测 Head 投影为 `stale`，候选保留历史但不得选择；
- 只有所有必需文件进入 `succeeded/partial`，且部分成功文件通过最低可读性门槛时才发送 `bid.manifest.parse_set_ready.v1`；
- 不可恢复解析失败使检测进入失败/阻塞状态，不允许用文件名补齐标段。

## 7. 标段检测数据域

### 7.1 `bid_lot_detection_runs` 与 Head

LotDetectionRun 的关键输入和字段：

- FK `manifest_id`；
- `parse_set_hash`；
- `detector_version`、`rule_set_version`、`normalizer_version`；
- `input_hash`，唯一 `(manifest_id, input_hash)`；
- `status=queued/running/succeeded/failed/stale`；
- `retryable`、时间、结果 hash、候选数、脱敏错误码、row version。

`bid_lot_detection_heads` 以 `manifest_id` 为主键，指向当前检测 Run。Head 指向的 Run 输入与当前 ParseSet 不一致时，对外状态必须是 `stale`。

Attempt、Event 表沿用 ParseRun 的租约、fencing 和追加式历史规则，分别使用：

- `bid_lot_detection_attempts`；
- `bid_lot_detection_events`。

### 7.2 LotCandidate 与证据

`bid_lot_candidates` 增加不可空 `detection_run_id`。唯一约束调整为：

- `(detection_run_id, normalized_lot_key)`；
- `(detection_run_id, candidate_hash)`。

候选保留 `manifest_id` 以便约束和查询，但必须与 DetectionRun 的 Manifest 一致。候选内容和置信度写入后不可变。

`bid_lot_candidate_evidence` 至少包含：

- PK `(lot_candidate_id, evidence_id)`；
- `support_role=identity/code/name/scope/overall_scope`；
- `display_order`、`display_label`；
- EvidenceFragment 必须来自该候选 Manifest 中的 DocumentVersion。

每个可见候选至少需要一条直接内容证据。确定性校验器负责：

- 同 Manifest 校验；
- 归一化、去重和稳定 candidate hash；
- 防止跨标段内容拼接；
- 将数值 score 映射为固定 `high/medium/low`；
- 拒绝只有文件名、MIME、parser hint 或搜索摘要的候选。

“未划分标段（整体）”只能在内容证据证明资料描述整体项目/单一标段，且关键章节覆盖达到规则门槛时生成；其 `source_status=system_scope` 并携带可见 warning。

## 8. 事件契约

### 8.1 内部 Outbox

| 事件 | 必需 Payload |
|---|---|
| `bid.document.parse_requested.v1` | `parse_run_id`, `document_version_id`, `input_hash`, `parser_profile_version` |
| `bid.document.parsed.v1` | `parse_run_id`, `document_version_id`, `status`, `quality`, `warnings`, `unit_counts`, `result_hash` |
| `bid.document.parse_failed.v1` | `parse_run_id`, `document_version_id`, `status=failed`, `quality=null`, `warnings`, `error_code`, `retryable`, `attempt_count` |
| `bid.manifest.parse_set_ready.v1` | `manifest_id`, `parse_set_hash`, `document_count`, `partial_count` |
| `bid.lot_detection.requested.v1` | `detection_run_id`, `manifest_id`, `parse_set_hash`, `input_hash` |
| `bid.lots.detected.v1` | `detection_run_id`, `manifest_id`, `parse_set_hash`, `candidate_count`, `selection_required`, `lots_url` |
| `bid.lot_detection.failed.v1` | `detection_run_id`, `manifest_id`, `error_code`, `retryable`, `attempt_count` |

所有 Payload 只保存路由与投影所需的小数据，不包含正文、bbox 全量列表、对象键、内部异常栈或模型原始输出。

### 8.2 Public Event

- `document.parse.changed`：解析排队、运行、成功、部分成功或最终失败的脱敏投影；
- `lot.selection.required`：检测成功且需要人工选择时发出；
- `operation.failed`：不可恢复解析/检测失败；
- 成功零候选不伪造 `lot.selection.required`，由 API-30 返回阻塞原因。

## 9. API-30 冻结合同

### 9.1 请求和授权

`GET /api/v1/bid-assessments/{assessment_id}/lots?manifest_id={optional}`

- 省略 `manifest_id` 时读取 Assessment 当前 Manifest；
- 显式 Manifest 必须属于同一 Assessment；允许读取历史候选，但 `is_current_manifest=false` 且所有选择动作关闭；
- 没有当前 Manifest 时返回 200 空结果；
- 功能关闭、Assessment/Manifest 不存在或当前 actor 不可见时统一 404；
- GET 不创建 DetectionRun，不发 Outbox，不写审计或幂等记录。

### 9.2 Generation 状态

统一枚举：

`not_started | queued | running | succeeded | failed | stale`

- `not_started`：没有 DetectionHead；
- `queued/running`：当前检测 Run 非终态；
- `succeeded`：检测完成，包括零候选；
- `failed`：最终失败，返回稳定脱敏错误与 retryable；
- `stale`：Head 输入不匹配当前 ParseSet，历史候选不可选择。

### 9.3 响应

HTTP 200 数据至少包含：

```json
{
  "assessment_id": "asmt_...",
  "manifest": {
    "manifest_id": "mft_...",
    "version": 2,
    "manifest_hash": "sha256:...",
    "is_current_manifest": true
  },
  "generation": {
    "status": "succeeded",
    "detection_run_id": "ldr_...",
    "parse_set_hash": "sha256:...",
    "candidate_count": 2,
    "retryable": false,
    "error_code": null,
    "requested_at": "2026-08-11T08:00:00Z",
    "started_at": "2026-08-11T08:00:01Z",
    "finished_at": "2026-08-11T08:00:03Z"
  },
  "candidates": [],
  "selection_required": false,
  "selected_lot_id": null,
  "blocking_reason": null,
  "allowed_actions": []
}
```

零候选且检测成功时：

- `generation.status=succeeded`；
- `candidate_count=0`；
- `blocking_reason.code=no_supported_lot`；
- 不返回 404。

候选状态是读取时投影：当前 Scope 引用的候选为 `selected`；Scope 已绑定后的其他同代候选为 `rejected`；其余为 `candidate`。`selection_required` 只有在当前 Manifest 检测成功、尚无 Scope、候选无法按冻结规则唯一自动绑定且允许 `lot.select` 时为 true。

### 9.4 缓存与泄漏边界

- 200 返回私有强 ETag、`Cache-Control: private, no-cache, max-age=0, must-revalidate`、`Vary: Authorization`；
- `If-None-Match` 命中返回 304、空响应体；
- ETag 覆盖 actor 可见的完整 API-30 投影；
- 禁止返回对象存储引用、内部解析器/模型输出、异常栈、租约信息或不可见 EvidenceFragment；
- Evidence 只返回稳定 `evidence_id`、`display_label` 和后续受控读取 URL。

## 10. API-31 Scope 绑定冻结合同

### 10.1 命令与前置条件

`POST /api/v1/bid-assessments/{assessment_id}/lot-selection`

请求必须同时携带：

- `Idempotency-Key`：16–128 个可打印 ASCII 字符；
- `If-Match`：API-03 返回的单个 Assessment 强 ETag；
- JSON：`manifest_id`、`lot_id`、可空且 trim 后最长 1000 字的 `selection_note`，拒绝未知字段。

首次绑定必须同时满足：

1. Assessment 对 actor 可见、`lifecycle_status=active`、`business_status=awaiting_lot_selection`；
2. 请求 Manifest 属于 Assessment 且仍是 `current_manifest_id`；
3. Manifest ParseSet 为 `ready`；
4. LotDetectionHead 指向 `status=succeeded` 且 `parse_set_hash` 等于当前 ParseSet 的 Run；
5. LotCandidate 属于该 Run/Manifest；
6. Candidate 至少有一条同 Manifest 的 `BidLotCandidateEvidence` 正文证据关联；
7. Assessment 尚未绑定 Scope。

不得从客户端提交或重新计算 lot 名称、范围、置信度、证据或检测版本；这些字段只能从当前权威 Candidate、DetectionRun 和 Manifest 快照。

### 10.2 不可变 Scope 快照

`selected_lot_snapshot_json` 固定包含：

- `schema_version=bid-assessment-lot-scope-v1`；
- Assessment、Manifest ID/版本/hash；
- DetectionRun ID、`parse_set_hash`、Candidate ID/hash；
- lot code/name、scope summary、normalized key、source status、confidence level/score、warnings；
- 按稳定顺序排列的 `evidence_ids`；
- `selection_note`、`selected_by` 和稳定 `operation_id`。

`scope_hash` 对上述业务快照做规范 JSON SHA-256；`created_at` 不进入 hash。Scope 创建后不可修改，Assessment 的当前 Scope 继续由最高 `version` 投影，本增量首次绑定通常为 version 1。

### 10.3 原子状态与事件

一个数据库事务内依次完成：

1. 创建 Scope；
2. Assessment `awaiting_lot_selection -> preliminary_analyzing`，递增 `row_version`；
3. 写 `bid.lot.selected.v1`，aggregate 为 Scope，Payload 至少包含 `scope_id/lot_id/manifest_id/detection_run_id/from/to/resource_version`；
4. 以 lot-selected event 为 causation 写 `bid.plan.requested.v1`，Payload 至少包含 `operation_id/assessment_id/scope_id/manifest_id/lot_id/requested_run_kind/resource_version`；
5. 写用户审计和完成幂等响应。

`bid.plan.requested.v1` 只表示规划请求已持久化。Phase 3 的企业快照与版本配置选择器尚未完成前，本增量不创建不完整 Run；响应 `run=null`。Lot selected 由现有公共投影生成 `lot.selected`，前端据此刷新 API-03/API-30。

### 10.4 幂等、并发与错误

- 相同幂等键、ETag 和 body 返回原 202 响应，并带 `Idempotent-Replay: true`；
- 使用最新 ETag、不同幂等键再次选择同一 Manifest/lot，返回原 Scope，不重复状态、Outbox 或审计；
- 已绑定其他标段返回 409 `BID_LOT_SCOPE_ALREADY_BOUND`，恢复 URL 指向 API-32；
- 缺少 If-Match 返回 428，旧 ETag 返回 412 并返回当前 ETag；
- 当前状态不允许返回 409 `BID_ASSESSMENT_STATE_CONFLICT`；
- 候选检测未开始、排队、运行、失败或 stale 返回 409 `BID_LOT_CANDIDATES_NOT_READY`；
- 非当前 Manifest 或 Candidate 不属于该 Manifest 返回 422 `BID_LOT_NOT_IN_MANIFEST`；
- 不可见 Assessment/Candidate 统一按资源不可见边界返回 404/422，不泄漏其他 Assessment 数据。

成功 202 返回类型化 `LotSelectionResponse`：`scope`、`accepted_operation`、`run=null`、最新 `assessment`；响应包含最新 Assessment ETag、`X-Resource-Version`、`Location`、`Cache-Control: private, no-store`。

### 10.5 迁移结论

API-31 所需字段、FK、唯一约束、事件枚举、幂等和审计表均已存在于 `0083`–`0093`。本增量不新增 Alembic revision，代码唯一 head 保持 `20260811_0093`。

## 11. API-32 独立研判克隆冻结合同

### 11.1 命令、锁与权威来源

`POST /api/v1/bid-assessments/{assessment_id}/clone-for-lot`

请求必须携带 16–128 字符 `Idempotency-Key`、源 Assessment 的单个强 `If-Match`，以及拒绝未知字段的 `source_manifest_id/lot_id/title`。事务先锁源 Assessment，再按稳定顺序锁当前 Manifest、源 Scope、DetectionHead、Manifest ParseHead、DetectionRun、Candidate/Evidence 和 Manifest 成员。

创建前必须满足：

1. 源 Assessment 对 actor 可见且 `lifecycle_status=active`；
2. 源 Assessment 已有不可变 Scope；API-31 尚未完成时应先选择源标段；
3. `source_manifest_id` 属于源 Assessment 且仍为当前 Manifest；
4. 当前 ParseSet 为 ready，DetectionHead 指向同 ParseSet 的 succeeded Run；
5. 目标 Candidate 属于该 Run/Manifest，且至少有一条同 Manifest 正文 Evidence 关联；
6. 目标 Candidate ID 与 `normalized_lot_key` 均不得等于源 Scope 已选标段。

### 11.2 新聚合与 ACL

一个事务创建：

- 新 Assessment：owner 为当前 actor，title 来自请求，复制源 `client_name/internal_note`，`external_ref=null`，`lifecycle_status=active`、`business_status=preliminary_analyzing`、`row_version=1`、`active_run_id=null`；
- 新 Manifest：version=1，用新 Assessment ID 和稳定成员序重新计算 hash；
- 新 `BidManifestDocument`：只复用同一批不可变 DocumentVersion 引用，不创建 FileObject/DocumentVersion，不读写或复制 MinIO 对象；
- 新 Scope：version=1，快照保存新 Assessment/Manifest 和源 Assessment/Manifest/DetectionRun/Candidate/Evidence 谱系。

新 Scope 的 `source_lot_candidate_id=null`，Candidate ID/hash、normalized key、证据和来源版本全部固化在不可变快照；因此新聚合授权和生命周期不依赖源 Candidate/Assessment 外键链。DocumentVersion 可见性只沿“新 Assessment -> 新 Manifest -> 新 ManifestDocument”判断。源 Assessment 后续归档不影响新 Assessment 读取；无新 Assessment 权限的 actor 仍统一得到 404。

### 11.3 事件、幂等与响应

事务依次写：

1. `bid.assessment.created.v1`，Payload 为新 Assessment 完整快照；
2. 以 created event 为 causation 的 `bid.plan.requested.v1`，aggregate 为新 Scope，并携带新 Assessment/Scope/Manifest/lot、源 Assessment/Manifest 和 `resource_version=1`；
3. `assessment.clone_for_lot` 用户审计和完成的幂等响应。

API-32 不写 `bid.lot.selected.v1`，因为目标 Candidate 来自源 Manifest，不能伪装成新 Manifest 的检测选择事件。Phase 3 版本集合未冻结前不创建 Run。相同幂等键、源 ETag 和 body 重放原 201 响应；成功返回新 `AssessmentSnapshot`、新 Assessment `Location/ETag/X-Resource-Version`、`Cache-Control: private, no-store`。

旧源 ETag 返回 412；非当前 Manifest/不属于 Manifest 的 Candidate 返回 422 `BID_LOT_NOT_IN_MANIFEST`；检测未就绪或 stale 返回 409 `BID_LOT_CANDIDATES_NOT_READY`；源未选标段、源已归档或同标段返回 409 `BID_ASSESSMENT_STATE_CONFLICT`；不可见源 Assessment 返回 404。

### 11.4 API-30 相邻投影

新 Assessment 已有权威 Scope，但其自有 Manifest 不复制源 DetectionRun。因此 API-30 对新 Assessment 返回 `selected_lot_id`、空 `candidates` 和 `generation.status=not_started`，同时 `selection_required=false`、`blocking_reason=null`、无标段操作；不得把“未复制检测运行”误报为用户阻塞，也不得触发解析或检测副作用。

### 11.5 迁移结论

API-32 复用 `bid_assessments`、`bid_document_manifests`、`bid_manifest_documents`、`bid_assessment_scopes`、幂等、Outbox 和审计数据域。克隆谱系已进入不可变 Scope 快照与审计，无需新列、表、事件枚举或 Alembic revision；代码唯一 head 保持 `20260811_0093`。

## 12. Alembic 门禁

在创建 revision 前必须同时满足：

1. master spec 至少为 v0.1-r14，当前已升级到 v0.1-r16；
2. API-30 OpenAPI 与 contracts 已具有类型化响应；
3. 事件目录已包含解析/检测七个内部事件，以及 API-31 的 `bid.lot.selected.v1`、`bid.plan.requested.v1` 与必需 Payload；
4. 状态枚举、表名、唯一约束和失效规则没有待决项；
5. 创建 `0092/0093` 前已静态确认代码迁移唯一 head 为 `20260811_0091`；API-31 迁移复审时唯一 head 为 `20260811_0093`，全程不得连接 ECS 查询；
6. revision 只接续唯一代码 head，不修改旧 `bid_intake_*` 或旧 `bid_parse_runs`。

建议分两条 revision：

- 第一条：ParseRun、Head、Attempt、Event、ParseUnit、EvidenceFragment；
- 第二条：LotDetectionRun、Head、Attempt、Event、LotCandidate 代际和候选证据。

两条 revision 在用户确认整个 Agent 开发完成并允许上线前，只能用于独立本地/开发数据库，不得进入正式发布候选或应用到 ECS。任何 Agent、OCR/视觉解析、真实样例、评测或模型调用仍需用户另行明确许可。
