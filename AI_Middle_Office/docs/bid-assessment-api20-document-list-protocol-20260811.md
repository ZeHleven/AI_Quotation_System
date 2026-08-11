# API-20 Assessment 文件列表协议

日期：2026-08-11
状态：已冻结并完成代码与本地隔离验证
接口：`GET /api/v1/bid-assessments/{assessment_id}/documents`

## 1. 实现边界

API-20 是 Manifest 范围内的权威只读入口，用于资料页恢复、历史资料版本审阅和 API-21/API-22 导航。它只读取 `bid_assessments -> bid_document_manifests -> bid_manifest_documents -> bid_document_versions -> bid_documents/bid_file_objects` 的授权关系，不写业务表、Outbox、审计或幂等记录，不访问 MinIO，也不创建解析、Scope 或 Run 数据。

本接口不需要新 Alembic revision；代码迁移 head 保持 `20260811_0091`。目标 ECS 实际 head 仍是只读确认的 `20260808_0082`，本次未连接或升级 ECS。

## 2. Manifest 选择

- 省略 `manifest_id` 时选择 Assessment 的 `current_manifest_id`，响应 `manifest_selection=current`。
- Assessment 尚无当前 Manifest 时返回 HTTP 200 空页，`manifest=null`、`current_manifest_id=null`；不能把合法的“尚未提交资料”误报为 404。
- 显式 `manifest_id` 可选择该 Assessment 的当前或历史不可变 Manifest，响应 `manifest_selection=explicit`。
- 显式 Manifest 不存在、不属于路径中的 Assessment、Assessment 不可见或不存在，统一返回 `BID_RESOURCE_NOT_FOUND`/404，不暴露跨 Assessment 资源是否存在。
- `manifest.document_count` 是所选 Manifest 未筛选的成员总数；顶层 `total` 是过滤后的总数。

## 3. 文档与版本投影

列表一行对应所选 Manifest 中一个逻辑 `BidDocument` 成员：

- `selected_version`：所选 Manifest 精确绑定的不可变 DocumentVersion；历史查询不会偷偷替换成最新版。
- `current_version`：当前 Manifest 对同一逻辑 Document 的绑定；如果该逻辑文档已在当前 Manifest 停用，则为 `null`。
- `is_in_current_manifest`：仅当 `selected_version` 这个精确版本仍是当前 Manifest 成员时为 `true`。同一逻辑文档已有新版本时，历史版本必须为 `false`。
- `replacement_chain`：只基于当前 actor 通过本 Assessment 的所有 Manifest 可见的版本计算，按 `version_no` 稳定排序，返回前一版、后一版、最新可见版和可见版本数。
- `include_versions=false` 时 `versions=null`；为 `true` 时返回上述 Assessment 可见版本列表。其他 Assessment 独占的同一企业级逻辑 Document 版本不得混入。
- 默认排序是所选 Manifest 的 `order_no ASC`，再以 Document/Version ID 打破平局。过滤在分页与 `total` 计算之前执行。

版本摘要只公开版本 ID/号、原始文件名、字节数、MIME、12 位小写 SHA-256 前缀、创建时间及 API-21/API-22 地址。

## 4. 解析状态冻结

允许的 `parse_status` 查询和值域是：

`not_requested | queued | running | succeeded | partial | failed`

Phase 1 尚无 `bid_document_parse_runs` 数据域，因此所有现有版本确定性投影为：

- `parse_status=not_requested`
- `parse_quality=null`
- `warnings=[]`

过滤其他合法的未来状态返回空页。禁止从 `parser_hint`、FileObject 存储状态、上传事件或 `source_metadata_json` 猜测解析是否成功。Phase 2 引入解析运行表后，只替换投影数据源，不改变本次冻结的响应形状。

## 5. 筛选与分页

- `document_type`：单值精确匹配，格式 `^[a-z0-9][a-z0-9_.-]{0,63}$`。
- `parse_status`：单值精确匹配，取值见上一节。
- `include_versions`：只接受规范的 `true` 或 `false`，默认 `false`。
- `page`：大于等于 1 的十进制整数，默认 1。
- `page_size`：1～100，默认 20。
- 页码超过末页时返回 HTTP 200 空 `data`，保留真实 `total`，不返回 404。
- 非法参数统一返回 `BID_REQUEST_VALIDATION_FAILED`/422，并给出逐字段 `field_errors`。

## 6. 缓存与条件读取

- HTTP 200 返回强 ETag、`X-Resource-Version`、`Cache-Control: private, no-cache, max-age=0, must-revalidate` 和 `Vary: Authorization`。
- ETag 是 Assessment ID/row_version 与完整公开页面投影的规范 JSON 哈希，不包含对象 key 或原始存储 ETag。
- 支持 `If-None-Match` 的 `*`、弱标签及标签列表；命中返回空响应体 304，并保留 ETag、资源版本和私有重验证头。
- 不允许 CDN、共享代理或跨用户缓存；`no-cache` 表示可在私有客户端保存但每次必须向服务端重验证，不表示共享缓存可复用。
- 当前实现仍会读取数据库后再判断 304，以保证未来解析状态进入投影后不会返回陈旧结果；304 优化响应体和网络传输，不把 Redis 变成权威真相源。

## 7. 存储信息防泄漏

响应禁止包含以下字段或派生值：

- `BidFileObject.object_key`、`storage_etag`、`storage_status`、`file_object_id`
- `BidDocument.logical_identity_key`、`created_by`
- `BidDocumentVersion.parser_hint`、`source_metadata_hash`、`source_metadata_json`、`created_by`
- MinIO endpoint、bucket、临时对象 key、预签名存储 URL、完整 SHA-256

下载必须经后续 API-22 的授权入口完成；API-20 只返回应用 API 地址。任何显式 Manifest 和版本展开都必须从可见 Assessment 的 Manifest 成员关系出发，不能从 Document ID、FileObject ID 或对象 key 反推 ACL。

## 8. 本地验证

本次新增验证覆盖：

- 无当前 Manifest 的合法空页和机器 Schema；
- 私有缓存头、强/弱 `If-None-Match` 与空体 304；
- Manifest 顺序、过滤前分页和越界语义；
- 历史 `selected_version`、当前 `current_version` 与替换链；
- 同一逻辑 Document 的跨 Assessment 版本隔离；
- 显式外部 Manifest 同形 404；
- 全部查询参数错误；
- 响应内对象存储和内部元数据禁泄漏字段。

最终机器合同全量为 `59 passed, 1 warning`，API-20 运行时专项为 `5 passed, 1 warning`。合同 + Assessment API 首轮组合回归中既有及新增用例 `124 passed`，两处新增测试造数因未显式 flush FileObject 而失败；修正夹具插入顺序后 API-20 专项全部通过。测试只使用本地临时 SQLite，没有连接 MinIO、Redis、CentOS 或 ECS。

## 9. 下一步

下一项是 API-21“查看 DocumentVersion 元数据”。开工前应冻结：版本可见性必须继续沿 Assessment Manifest 关系验证、解析运行摘要与页/Sheet/OCR 质量来源、上传来源的脱敏形状、版本级 ETag，以及 API-21 到 API-22 的允许操作与下载授权边界。
