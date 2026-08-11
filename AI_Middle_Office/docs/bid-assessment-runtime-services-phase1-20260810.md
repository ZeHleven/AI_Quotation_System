# 报价资料研判 Agent Phase 1 运行服务

日期：2026-08-10；更新：2026-08-11
状态：代码实现与本地隔离验证；未连接、未迁移、未启用 ECS

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

## 下一开工门

下一项为 API-21 `GET /api/v1/bid-document-versions/{version_id}`。开工前须冻结版本可见性必须沿
Assessment Manifest 关系验证、解析运行摘要与页/Sheet/OCR 质量来源、上传来源脱敏形状、版本级
ETag，以及 API-21 到 API-22 的允许操作与下载授权边界。
