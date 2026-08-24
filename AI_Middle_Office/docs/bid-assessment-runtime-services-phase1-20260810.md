# 报价资料研判 Agent Phase 1 运行服务

日期：2026-08-10；更新：2026-08-12
状态：Phase 1/2/3A—3E 已完成代码与本地隔离专项验证；未连接、未迁移、未启用 ECS

## 实现范围

- API-01：`POST /api/v1/bid-assessments` 已接入冻结的严格请求 Schema，在一个事务中
  完成 Assessment、幂等响应快照、`bid.assessment.created.v1` Outbox 和创建审计；返回
  HTTP 201、Location、ETag、`X-Resource-Version`，同 Key 同请求重放原始 201，不创建
  Manifest、Scope 或 Run。
- API-03：`GET /api/v1/bid-assessments/{assessment_id}` 已成为首次加载、状态冲突恢复和
  SSE 重连后的权威读取入口；所有者和管理员可见，越权与不存在统一返回冻结的 404
  错误信封。HTTP 200 返回完整 `AssessmentSnapshot`、强 ETag、`X-Resource-Version` 和
  `Cache-Control: private, no-store`；`If-None-Match` 支持当前强/弱标签和标签列表，命中
  返回无响应体的 304。API-03 与 SSE 控制事件复用同一个持久化快照构建器，不写审计、
  Outbox 或幂等记录。
- API-10：`POST /api/v1/bid-assessments/{assessment_id}/upload-batches` 使用 API-03
  返回的 Assessment 强 ETag 作为 `If-Match`，在同一事务中创建开放批次、固化幂等 201
  响应、写入 `bid.upload_batch.created.v1` Outbox 和创建审计。`initial` 只接受无当前
  Manifest 的 Assessment，`change` 必须绑定当前 Manifest；同一 Assessment 只允许一个
  未结束批次，不同 Key 重复创建返回现有批次恢复地址。创建批次不推进 Assessment
  `row_version`，响应使用批次自身 ETag，且不上传文件、不创建 Manifest/Scope/Run。
- API-11：`GET /api/v1/bid-upload-batches/{batch_id}` 已把 API-10 返回的 Location 建成
  页面关闭恢复、并行上传进度对账和提交前获取最新批次 ETag 的权威读取入口。所有者和
  管理员可见，越权与不存在返回同形 404；HTTP 200 返回完整 `UploadBatchSnapshot`，
  `If-None-Match` 命中返回空体 304。读取不写审计、Outbox 或幂等记录。批次 ETag 同时
  覆盖数据库 `row_version` 和当前上传限制指纹：文件状态变化会换 ETag，服务配置中的
  文件数、字节上限或扩展名白名单变化也会换 ETag，避免动态限制变化后误返回 304；每个
  文件条目同时返回自己的 `row_version/etag`，是页面恢复后执行 API-13 的权威文件版本。
- API-12：`POST /api/v1/bid-upload-batches/{batch_id}/files` 已实现单文件有界流式接收、
  SHA-256、扩展名/MIME/magic bytes/Office ZIP/文本基础检查、文件级幂等和内容去重。
  MinIO 对象使用纯服务端键，写对象发生在数据库事务之外；最终事务原子提交 FileObject、
  BatchFile、批次新 `row_version`、幂等响应、`bid.upload_file.received.v1` Outbox 和审计。
  成功返回文件与批次双 ETag；同一客户端文件精确重传不重复推进版本，内容或元数据冲突
  返回冻结 409。数据库失败只补偿删除本请求的完整对象键，删除失败由引用感知孤儿任务
  收敛，禁止按前缀 TTL 盲删。
- API-13：`DELETE /api/v1/bid-upload-batches/{batch_id}/files/{file_id}` 使用 API-11/API-12
  返回的文件强 ETag 作为 `If-Match`，在批次行锁事务中移除草稿 BatchFile、重算批次状态、
  精确推进批次 `row_version`，并原子写入内部 204 删除回执、
  `bid.upload_file.removed.v1` Outbox 和审计。相同请求重放原 204 与原批次版本，不重复推进。
  其他 BatchFile、临时引用或 DocumentVersion 仍引用内容时保留 FileObject 和物理对象；仅当
  全部引用归零且对象位于受管前缀时删除 FileObject 元数据。MinIO 精确删除只在数据库提交
  成功后发生，失败保持 204 并由引用感知孤儿任务收敛。
- API-14：`POST /api/v1/bid-upload-batches/{batch_id}/deactivations` 使用 API-11 批次强
  ETag，在批次行锁事务中原子校验 `document_ids[]` 是否属于当前 `base_manifest_id`，登记
  下一 Manifest 的逻辑停用、重算批次状态、真实变化时精确推进一次 `row_version`，并写入
  `bid.upload_batch.deactivation_added.v1` Outbox、审计和幂等 201。相同原因重复为无操作，
  不推进版本或写事件；不同原因重复整单冲突。只有停用、没有新文件的 change 批次可为
  `ready/can_commit=true`。该路径不修改历史 Document/Version/Manifest、FileObject、证据、
  报告或 MinIO 对象。
- API-15：`POST /api/v1/bid-upload-batches/{batch_id}/commit` 使用批次强 ETag、ready 文件数、
  停用数和显式确认共同封闭并发窗口；在一个事务中按冻结算法合并 add/replace/deactivate，
  复用 available FileObject、登记新 DocumentVersion、创建不可变 Manifest、切换 Assessment
  当前指针、使基于旧当前 Manifest 的活跃/可重试 Run 进入 stale，并提交因果 Outbox、审计和
  幂等 202。旧 Manifest/Version 与物理对象不变；空结果 Manifest 合法并使 Assessment 回到
  `awaiting_files`。API-15 只发出新版本解析请求，不在尚无解析结果、Scope 和 Run 时伪造规划
  事件。
- API-16：`POST /api/v1/bid-upload-batches/{batch_id}/abandon` 强制显式 reason、批次强 ETag
  和幂等键；只允许未过期的 `draft/uploading/ready`。同一事务终态化批次、释放开放槽、推进
  一次版本并写 `bid.upload_batch.abandoned.v1`、审计和幂等 200；committing/committed 不得
  撤销 Manifest。HTTP 请求不调用对象存储。`cleanup_after` 到期后，Celery 后台事务才解除
  受管引用并重新统计 BatchFile/DocumentVersion/临时引用；只有总引用为零才先提交元数据
  清理、再精确物理删除，失败交给既有孤儿清理器收敛；功能开关关闭时定时任务空操作。
- API-20：`GET /api/v1/bid-assessments/{assessment_id}/documents` 已成为 Manifest 范围内的权威文件列表。默认选择当前 Manifest，显式参数可读同一 Assessment 的历史 Manifest；同时返回所选不可变版本和当前 Manifest 版本，版本链严格限制在本 Assessment 可见关系内。过滤先于稳定分页，支持私有强制重验证 ETag/304；Phase 1 无解析运行表时只投影 `not_requested`，且不暴露 FileObject ID、对象 key、存储 ETag/状态、完整哈希、parser hint 或 source metadata。该接口纯读取，不访问对象存储、不写 Outbox/审计/幂等，也不新增迁移。
- 上传限制：批次 Snapshot 从 `BID_UPLOAD_*` 配置返回最大文件数、单文件/整批字节上限、
  扩展名白名单和批次有效期；默认值与 v1 合同示例一致。
- 状态服务：行锁、`row_version` 乐观并发、Attempt fencing 校验、业务状态变更、
  Outbox 与审计同事务写入；服务只 `flush`，事务提交由调用方负责。
- Transactional Outbox Dispatcher：短租约抢占、过期租约回收、指数退避、最大尝试
  次数、死信、租约所有者与 `row_version` 双重 CAS。
- 消费者事务去重：业务处理和 `(consumer_name, event_id)` 标记同事务提交；重复投递
  返回已保存结果，不重复执行数据库副作用。
- Public Event Projector：只把白名单字段投影到 `bid_public_events`，在 Assessment 行锁内
  分配单调序号；不向 SSE 暴露内部 Outbox payload。
- SSE：`GET /api/v1/bid-assessments/{assessment_id}/events`，按所有者/管理员隔离，支持
  `Last-Event-ID`、7 天保留、持久化 `stream.reset`、持久化 Snapshot、15 秒 keepalive
  和代理禁缓存响应头。
- API 幂等处理器：作用域为 actor、HTTP method、规范化 route template 与
  `Idempotency-Key`；业务结果和响应快照同事务固化，完成请求可重放原状态码与响应。
- 统一审计写入器：追加 before/after/metadata SHA-256 与整条记录哈希，不提交事务，
  由业务事务统一原子提交。
- 上传孤儿清理：Celery 任务 `bid.cleanup_upload_orphans` 只扫描宽限期外候选，并同时排除
  FileObject 与 BatchFile 的数据库引用；只删除确定无引用的精确对象键。

## 关键事务边界

1. API/状态命令在一个 MySQL 事务中写业务状态、Outbox、审计和幂等完成响应。
2. 事务提交后，Dispatcher 才能看到并抢占 Outbox；消息发布不持有数据库事务。
3. 发布完成后以租约所有者和抢占时 `row_version` 更新为 `published`。进程若在发布后、
   状态确认前崩溃，租约到期会再次发布，因此交付语义是至少一次。
4. 消费者把业务投影和 processed marker 放在同一事务；异常整体回滚，Celery 重试；
   已处理事件直接确认重复，不再次执行业务写入。
5. SSE 只读取持久化 Public Event。Redis/Celery 消息不是公共进度真相源。
6. API-11 的条件读取依赖批次聚合版本：API-12 创建 BatchFile、API-13 移除 BatchFile、
   API-14 改变停用集合时，均在最终事务中同步递增 `bid_upload_batches.row_version`；精确重放
   和同原因停用无操作不递增。后续文件校验、失败和重试也必须遵守同一约束。
7. API-15 在相同事务中把 Batch 与 Assessment 各推进一个版本，并按
   `document.version_registered* -> manifest.committed -> assessment.input_stale? ->
   document.parse_requested*` 固化单调时间和因果链。规划请求延后到解析完成且 Scope 就绪的
   消费事务。
8. API-16 请求事务只终态化批次，不解除文件引用或调用 MinIO。到期清理按批次独立事务
   解除受管引用、写系统审计和推进 Batch 版本；物理删除严格发生在数据库提交后，共享引用
   未归零时不得删除。

## 运行门禁

- `FEATURE_BID_ASSESSMENT_V1_RUNTIME=false` 为默认值；关闭时 API-01、API-03、API-10、
  API-11、API-12、API-13、API-14、API-15、API-16、API-20 和 SSE 均 fail-closed 为 404，Dispatcher 循环不启动。
- 启用前必须把目标数据库从实际 `20260808_0082` 备份并迁移至 `20260811_0091`，且
  API 与 Celery Worker 必须发布同一版本。
- 本阶段没有修改或复用旧 `bid_intake_*` 运行链，也没有连接本机 CentOS、旧数据卷或
  ECS 数据库。
- 当前 Dispatcher 首个具体消费者是 `bid.project_public_event`；后续工作流消费者必须
  继续使用相同事务去重协议，不能绕过 `bid_processed_events`。

## 本地验证覆盖

- API-01 首次创建、机器响应 Schema、完整 Header、完成重放、处理中冲突、同 Key 异请求
  冲突、`external_ref` 冲突、未知字段/缺 Key、开关关闭和审计失败整单回滚；
- API-03 完整机器响应 Schema、所有者/管理员可见、越权与不存在同形 404、强/弱/列表
  `If-None-Match` 的空体 304、旧 ETag 恢复数据库最新版本、开关关闭和只读无副作用；
- API-10 完整机器响应 Schema、原 201 重放、Assessment/ETag 纳入幂等请求哈希、缺失或
  弱/旧 `If-Match`、开放批次唯一性与恢复地址、`initial/change` 基线、所有者/管理员、
  Outbox 到 Public Event 投影、开关关闭和审计失败整单回滚；
- API-11 完整机器响应 Schema、API-10 Location 恢复、所有者/管理员可见、越权与不存在
  同形 404、强/弱/列表/通配 `If-None-Match` 空体 304、旧 ETag 对账最新文件进度、动态
  上传限制变化时 ETag 失效并返回新限制、开关关闭和只读无副作用；
- API-12 完整机器请求/响应/Header 合同、服务端对象键、成功事务闭环、API-11 进度对账、
  幂等键重放、客户端文件精确重传、客户端文件冲突、哈希/MIME/magic/扩展名/相对路径、
  单文件/文件数/整批字节限制、存储失败同请求重试、审计失败整体回滚与精确删除、并发
  内容去重、`replace` 当前 Manifest 门禁、所有者/管理员、开关关闭、Public Event 投影和
  引用感知孤儿清理；
- API-13 文件强 ETag 与错误边界、空体 204 及精确重放、批次版本推进、共享 BatchFile 和
  DocumentVersion 引用保护、最后引用的提交后精确物理删除、删除失败后的孤儿收敛、审计
  失败整单回滚、已提交批次/所有权/管理员/开关门禁、Outbox 与 Public Event 投影；
- API-14 严格 `document_ids[]`/原因规范化、集合顺序无关幂等、批次强 ETag、基线 Manifest
  关系成员校验、同原因重复无操作、混合新增、异原因整单冲突、只有停用的 ready/提交校验、
  批次版本只推进一次、审计失败整单回滚、所有者/管理员/开关/初始批次/已提交批次门禁、
  Outbox/Public Event 投影，以及不调用对象存储和不改变历史 Document/Version/Manifest；
- API-15 严格请求/Header/幂等合同、initial 提交、change 的 add/replace/deactivate 合并、
  原 Manifest 不变、空 Manifest、重复替换冲突、显式计数和陈旧基线、Batch/Assessment
  版本与指针、旧 Run stale、事件单调因果链、Manifest
  Snapshot 投影、不提前规划、审计失败整体回滚、所有权/管理员/开关和对象存储零调用；
- API-16 必填 reason 的先 trim 后限长、批次强 ETag、精确幂等重放/异请求冲突、开放与终态
  门禁、放弃事务的对象存储零调用、Outbox/Public Event/审计原子闭环、宽限期前无操作、
  清理审计失败时引用解除整单回滚、引用解除版本推进、数据库提交后物理删除、删除失败转孤儿重试，以及跨批次共享 FileObject
  直到最后引用才删除；
- API-20 无当前 Manifest 空页、默认/显式历史选择、稳定过滤分页、所选/当前版本双投影、替换链、跨 Assessment 版本隔离、查询参数错误、私有缓存 ETag/304 和存储内部字段防泄漏；
- 状态、Outbox、审计原子提交与强制回滚；
- stale `row_version` 冲突；
- 消费者重复投递和处理异常回滚；
- Public Event 投影去重及 Assessment 内序号；
- SSE 首次 Snapshot、过期游标 reset、顺序和所有权；
- 幂等首次执行、处理中冲突、完成重放、Key 异请求冲突与整体回滚；
- Dispatcher 发布、重试、死信和 stale lease fencing。

最新验证结果：

- 机器合同全量：`59 passed, 1 warning`；API-20 运行时专项：`5 passed, 1 warning`；
- 合同 + Assessment API 首轮组合回归：`124 passed, 2 warnings`，仅两处新增 API-20 造数夹具因 FileObject/DocumentVersion 未显式 flush 顺序失败；修正夹具后 API-20 精确集全部通过；
- API-01/API-03/API-10/API-11/API-12/API-13/API-14/API-15/API-16 专项：`63 passed, 1 warning`；
- API-01/API-03/API-10/API-11/API-12/API-13/API-14/API-15/API-16 + 运行服务专项：`73 passed, 1 warning`；
- 机器合同 + `0083`—`0091` 迁移合同 + 运行服务 + API-01/API-03/API-10/API-11/API-12/API-13/API-14/API-15/API-16：
  `176 passed, 2 warnings`；
- 上述范围 + 配置、健康、鉴权和 RBAC 选定相邻回归：`208 passed, 24 warnings`；
- `alembic heads`：唯一 `20260811_0091 (head)`；
- MySQL 方言离线升级 SQL：`0090 -> 0091` 成功生成；`0091` 降级必须在线读取真实放弃血缘，
  `--sql` 离线降级会按设计拒绝，避免绕过数据保护门禁。正式降级演练只能在一次性 MySQL 8
  实例执行。

测试仅使用临时 SQLite 数据库、假对象存储和假消息发布器；没有连接 MinIO、Redis、
CentOS 或 ECS。上传对象命名与补偿协议详见
`docs/bid-assessment-api12-upload-protocol-20260810.md`；文件移除、共享引用与提交后物理删除
协议详见 `docs/bid-assessment-api13-draft-file-removal-protocol-20260811.md`；基线文档停用、
重复语义和历史保护详见 `docs/bid-assessment-api14-document-deactivation-protocol-20260811.md`；
批次提交、不可变 Manifest、旧 Run stale 和事件顺序详见
`docs/bid-assessment-api15-upload-batch-commit-protocol-20260811.md`。
API-16 reason、终态门禁与共享对象延迟清理详见
`docs/bid-assessment-api16-upload-batch-abandon-protocol-20260811.md`。
API-20 Manifest 选择、版本投影、分页缓存和存储防泄漏详见
`docs/bid-assessment-api20-document-list-protocol-20260811.md`。

## 2026-08-11 Phase 3A 交接

Phase 2 已完成 API-30—API-32 与 `20260811_0092/0093` 权威数据域收口。Phase 3A 进一步冻结并
实现了 `bid.plan.requested.v1 -> BidAnalysisRun -> bid.run.created.v1` 的 Run Bootstrap、冻结企业
快照/六类 active 配置选择、数据库 evaluation time、input fingerprint/hash、输入未就绪不写
processed marker 的恢复协议，以及 API-40/API-41。Phase 3A 不执行 Planner、模型、Tool 或 OCR，
复用 `0084`—`0086` 既有表且不新增 revision，代码唯一 head 保持 `20260811_0093`。

本增量获用户明确许可后完成合同、API-40/API-41、事务/ACL/幂等/ETag、Outbox 恢复和相邻回归
专项，共 `82 passed`：合同 64、Phase 3A 核心 3、API-03/API-31/API-32 相邻 8、事务/Outbox/SSE
运行服务 7。测试仅使用临时 SQLite、本地假消息/存储边界，未运行真实样例、OCR/视觉解析或模型
调用。下一开工门为 Phase 3B Planner：必须先冻结标准 Task 注册表、PlanProposal、
确定性 DAG 校验、PlanRevision/Task/Dependency 原子提交及 `bid.plan.committed.v1` / 首批
`bid.task.ready.v1` 边界。详见
`docs/bid-assessment-runtime-brain-phase3a-protocol-20260811.md`。

## 2026-08-11 Phase 3B 交接

Phase 3B 已实现 49 项标准任务运行时注册表、无模型初始 PlanProposal、九项确定性 DAG 校验、可复现 Plan envelope、`bid.run.created.v1 -> PlanRevision/Task/Dependency -> bid.plan.committed.v1/bid.task.ready.v1` 原子消费和无 processed marker 维护扫描。初始 DAG 为 8 个任务、7 条依赖、最大动态深度 3，只有 `bind_assessment_snapshot` 首先 ready；不重新解析文件或推断标段，不创建 Attempt，不调用 OCR/视觉/模型。独立开关 `FEATURE_BID_ASSESSMENT_PHASE3_PLANNER=false` 默认关闭。

本增量复用 `0085/0086`，不新增 Alembic revision，代码唯一 head 保持 `20260811_0093`；未连接或改动 ECS/CentOS/真实 MinIO/Redis。获得用户明确许可后完成合同 65、Planner/DAG 5、Plan Commit 与 API-40/API-41 相邻 4、迁移拓扑 47、事务回滚/维护恢复 2、Outbox/processed marker/SSE 运行服务 10，共 `133 passed`。完整协议见 `docs/bid-assessment-runtime-brain-phase3b-planner-protocol-20260811.md`。

Phase 3C 已进一步冻结并实现 Task Runtime Control Plane：从 committed Plan 与 frozen Run 输入重构 TaskContract，使用 `BidTaskAttempt` 的 180 秒 Lease、Heartbeat 和递增 fencing token 控制唯一写入权，以 `BidCheckpoint` 持久化不可变连续恢复点，并在完成事务内释放满足全部父依赖的下游 Task；全 DAG 完成后只转入 validating 并写 validation request，不直接完成 Run 或发布报告。失败、过期租约和 terminal Run 使用新 Attempt/旧 token 硬 fence 收敛。独立开关 `FEATURE_BID_ASSESSMENT_PHASE3_TASK_RUNTIME=false` 默认关闭，周期维护只恢复租约而不主动执行 ready Task。

Phase 3C 继续复用 `0085/0086`，不新增 Alembic revision，代码唯一 head 保持 `20260811_0093`；本阶段不执行模型、OCR、视觉、Tool 或真实存储。Python 编译、JSON 解析和 JSON Schema 静态检查已通过；经用户明确授权，Phase 3C 合同、状态机、API-41/SSE 相邻回归与迁移拓扑共 `123 passed`。完整协议见 `docs/bid-assessment-runtime-brain-phase3c-task-runtime-protocol-20260811.md`。

## 2026-08-11 Phase 3D 交接

Phase 3D 已冻结并实现 API-42/API-43 与 Run 生命周期收口。API-42 在强 Run ETag、ACL 和幂等事务下持久化 `cancel_requested_at`，由 30 秒维护任务原子取消非终态 Task、活跃 Attempt/AsyncOperation，并收敛 Run/Assessment；API-43 只恢复当前 failed/retryable 且 Scope/Manifest/active Run 未 stale 的原 Run，围栏旧执行后创建 attempt_no/fencing 单调递增的 `created` Attempt，下一次 Lease 复用该 Attempt 并携带最近不可变 Checkpoint。API-41 与 Outbox/SSE 同步补齐 actor-visible 取消时间、动态操作和取消/重试事件投影。

本增量使用独立默认关闭开关 `FEATURE_BID_ASSESSMENT_PHASE3_RUN_LIFECYCLE=false`，复用 `0085/0086` 已有结构，并新增 `20260811_0094` 仅扩展 `bid.run.retry_requested.v1` 的数据库 Outbox CHECK，代码唯一 head 为 `20260811_0094`；不执行模型、OCR、视觉、Tool 或真实存储，未连接或改动 ECS/CentOS/真实 MinIO/Redis。静态门已通过；经用户明确授权，机器合同与迁移拓扑 115、API-42/API-43 及 Phase 3C/API-41 相邻链 10、事务/幂等/Outbox/SSE/周期维护运行服务 12，共 `137 passed`。完整协议见 `docs/bid-assessment-runtime-brain-phase3d-run-lifecycle-protocol-20260811.md`。

## 2026-08-12 Phase 3E 交接

Phase 3E 已冻结并实现 Tool/Context Control Plane：确定性 Context Manifest 绑定当前 Attempt/Fence、Run frozen versions、TaskContract 和受控 Evidence/依赖/历史 ToolResult；Tool Gateway 执行严格参数 Schema、profile/allowlist、ToolRegistry、预算、幂等和 HMAC scope token；同步与异步结果写入不可变 Result Store，异步等待通过 Checkpoint、新 Attempt/Fence 恢复，旧 Attempt 与晚到回执硬围栏。Phase 3D 取消/重试同步取消未完成 Invocation。

本增量使用独立默认关闭开关 `FEATURE_BID_ASSESSMENT_PHASE3_TOOL_CONTEXT=false`，新增线性 revision `20260812_0095`、三张权威表及 Checkpoint Context 外键，代码唯一 head 为 `20260812_0095`；授权范围内合同与迁移拓扑 117、API/Phase 3C/3D 相邻链 16、Outbox/SSE/维护恢复 13，共 `146 passed`。不开放新外部 API 或执行器，未执行模型、OCR、视觉、Tool、真实样例或真实存储，未连接或改动 ECS/CentOS/真实 MinIO/Redis。完整协议见 `docs/bid-assessment-runtime-brain-phase3e-tool-context-protocol-20260812.md`。

## 2026-08-12 Phase 3F 交接

Phase 3F 已冻结并实现受控 Tool Adapter/Executor 调度：Gateway 在 Adapter I/O 前原子保存异步 continuation 和唯一 Dispatch；Executor 以数据库 Lease、单调 DispatchAttempt/Fence、稳定 provider request id 和持久 sending 边界执行；安全幂等调用可恢复重放，不可安全重放的发送后租约丢失进入 `uncertain`。取消、显式重试和操作超时同步围栏 Dispatch。首个 Adapter 仅为本地只读 `documents.outline`，只读取当前 ParseHead/结构化 ParseUnit，不读取原文件或触发解析。

新增独立默认关闭开关 `FEATURE_BID_ASSESSMENT_PHASE3_TOOL_EXECUTOR=false` 和线性 revision `20260812_0096`；代码唯一 head 为 `20260812_0096`。Phase 3F 合同、迁移、配置/周期任务、Dispatch/Adapter、事务/幂等/Lease/Fencing、Checkpoint/超时/取消恢复和 Phase 3C—3E/API-41/SSE 相邻回归已完成本地隔离专项验证，共 `149 passed`。不开放新外部 API，不调用模型、OCR/视觉、公网、真实外部工具或真实对象存储，未连接或改动外部环境。完整协议见 `docs/bid-assessment-runtime-brain-phase3f-tool-executor-protocol-20260812.md`。

## 2026-08-12 Phase 3G 交接

Phase 3G 已冻结并实现 Run Validation/Convergence：唯一 Validation、ValidationAttempt Lease/Fence、确定性 frozen input/Plan/Task/Dependency/Attempt/Checkpoint/AsyncOperation/Tool 血缘校验、不可变结果哈希，以及 Run/Assessment/Outbox/Audit 原子 `succeeded|failed|stale` 收敛。旧 active pointer 的 stale Run 不得覆盖新 Run 对应的 Assessment 状态。

本地隔离专项验证已完成：Phase 3G 合同、0097 迁移、Validation/Convergence 核心链与 Phase 3C—3F/API-41/SSE 相邻回归共 `158 passed / 0 failed`；未运行真实样例、OCR/视觉解析、模型、真实外部工具或真实对象存储。

新增默认关闭开关 `FEATURE_BID_ASSESSMENT_PHASE3_RUN_VALIDATION=false` 和线性 revision `20260812_0097`；代码唯一 head 为 `20260812_0097`。专项与相邻回归已获授权并完成，结果计入上述 `158 passed / 0 failed`；不调用模型、OCR/视觉、公网、真实工具或对象存储，未连接外部环境。完整协议见 `docs/bid-assessment-runtime-brain-phase3g-validation-convergence-protocol-20260812.md`。

## 2026-08-12 Phase 3 总收口交接

v0.1-r25 已冻结 `phase3-runtime-profile.json`、完整 A—G 链和跨阶段终态不变量；新增默认关闭总开关 `FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME=false`，只有 V1 Runtime、Phase 3A—3G 七个阶段开关和 Tool scope signing key 全部就绪才能加载完整运行配置。Run Validator 升级为 `bid-run-integrity-validator-v2`，materialization input 与终态检查进一步绑定全部 Task Attempt/Checkpoint、Context Manifest、Invocation、AsyncOperation、Dispatch/DispatchAttempt 和 ToolResult 的稳定身份、Hash、Fence 与代际连续性。

总链专项测试已完成：API-40 创建并提交 Plan 后，首个 Task 经过 Context、唯一允许的本地只读 `documents.outline` Adapter、AsyncOperation、DispatchAttempt、新 Task Attempt/Fence 和最终 Checkpoint，再完成其余 DAG、Run Validation、API-41 与可终止 SSE 投影。合同/Planner/配置门禁 `79`、Phase 3A—3G/API-40/API-41/完整端到端 `31`、事务/Outbox/SSE/维护恢复 `14`、`0083`—`0097` 迁移拓扑 `51`，最终为 `175 passed / 0 failed`。本增量不新增 Alembic revision，唯一 head 保持 `20260812_0097`；不调用真实模型、OCR/视觉、公网、外部工具或真实对象存储，未连接外部环境。完整协议见 `docs/bid-assessment-runtime-brain-phase3-closeout-protocol-20260812.md`。
