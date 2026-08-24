# API-21/22 DocumentVersion 详情与原文件下载协议

日期：2026-08-11  
状态：已冻结并完成代码与本地隔离验证  
接口：

- `GET /api/v1/bid-document-versions/{version_id}`
- `GET /api/v1/bid-document-versions/{version_id}/download`

## 1. 共同授权边界

- DocumentVersion 没有独立 ACL。当前 actor 只有沿
  `BidDocumentVersion -> BidManifestDocument -> BidDocumentManifest -> BidAssessment`
  找到至少一个可见 Assessment 时，才能读取元数据或原文件。
- 普通用户只看自己创建的 Assessment；管理员沿用现有研判 v1 管理员可见性。共享
  `BidFileObject`、相同 SHA-256、Document 创建人、上传批次创建人和对象 key 都不能授予权限。
- API-21 只返回当前 actor 可见的 Manifest 引用。其他 Assessment 对同一版本的引用不得泄漏。
- 版本不存在、没有可见 Manifest 引用、ID 格式无效和跨用户猜测统一返回
  `BID_RESOURCE_NOT_FOUND`/404，不能用错误形状泄漏资源是否存在。
- API-21 与 API-22 必须复用同一查询服务，禁止出现“元数据不可见但文件可下载”或相反的分叉。

## 2. API-21 权威投影

响应 `DocumentVersionResponse.data` 冻结为：

- 不可变版本身份：`version_id/version_no/filename/size_bytes/mime_type/sha256/created_at`；
- 逻辑文档：`document_id/logical_name/document_type`；
- 脱敏上传来源：只公开 `source_type/operation/relative_path`；不得公开 batch ID、batch file ID、
  client file ID、替换目标 ID、源元数据哈希或源元数据原文；
- `parse_summary`：解析状态、最新运行摘要、质量和警告；
- `manifest_references`：仅当前 actor 可见的 Assessment/Manifest、角色和顺序；
- `allowed_actions`：是否可下载及应用内 API-22 地址。

严禁返回 FileObject ID、对象 key、存储 ETag/原始存储状态、parser hint、逻辑身份键、创建人、
MinIO endpoint/bucket 或预签名地址。完整 SHA-256 在已授权的单版本详情中允许返回，用于原文件
完整性核对；API-20 列表继续只返回 12 位前缀。

## 3. Phase 1 解析占位

当前没有 `bid_document_parse_runs` 数据域，API-21 必须确定性返回：

```json
{
  "status": "not_requested",
  "latest_run_id": null,
  "requested_at": null,
  "started_at": null,
  "finished_at": null,
  "quality": null,
  "warnings": []
}
```

不得从 `parser_hint`、MIME、文件后缀、对象状态或上传来源猜测页数、Sheet、OCR 或解析成功。
Phase 2 引入权威解析运行表后只替换数据源，保持上述外部形状。

## 4. API-21 缓存

- HTTP 200 返回强 ETag、`X-Resource-Version=version_no`、
  `Cache-Control: private, no-cache, max-age=0, must-revalidate` 和 `Vary: Authorization`。
- ETag 对完整公开投影做规范 JSON SHA-256；可见 Manifest 引用变化会改变 ETag，内部对象 key、
  storage ETag 和不可见引用不进入 ETag。
- `If-None-Match` 支持 `*`、弱标签和标签列表；命中返回空体 304，并保留上述私有重验证头。
- API-21 纯读取 MySQL，不访问对象存储，不写 Outbox、审计或幂等记录，不新增迁移。

## 5. API-22 受控下载

- 每次请求重新执行共同授权查询，然后检查 FileObject 当前允许读取，最后由应用打开精确对象 key。
- HTTP 200 使用 `StreamingResponse` 分块传输，不一次性读入应用内存；客户端断开、读取完成或异常时
  必须关闭并释放 MinIO 响应连接。
- 响应设置安全 `Content-Disposition`（ASCII fallback + RFC 5987 UTF-8 filename）、权威
  `Content-Type`、`Content-Length`、`X-Content-Type-Options: nosniff`、
  `Cache-Control: private, no-store`、`Vary: Authorization` 和
  `Content-Security-Policy: sandbox`。
- 文件名先剥离路径和控制字符，禁止 CR/LF 响应头注入；异常 MIME 降级为
  `application/octet-stream`。
- 对象存储打开失败、状态不可读或对象缺失统一映射为可重试
  `BID_STORAGE_UNAVAILABLE`/503，响应和日志不得把 object key 返回给用户。
- 禁止 302 跳转、永久/预签名 MinIO URL，也不把内部 bucket/endpoint 暴露给浏览器。

## 6. Range 冻结

首版 API-22 不声明字节 Range 能力，不返回 206/`Content-Range`，并设置
`Accept-Ranges: none`。客户端即使发送 `Range` 也必须先重新鉴权，并返回完整 200 文件流；不能把
Range 请求转成绕过授权的 MinIO 直连。未来若需要 PDF 大文件随机访问，必须先单独冻结单区间语法、
416 错误合同、`If-Range`、长度校验和 Nginx 转发行为，再向后兼容增加 206。

## 7. 本地验收门

- API-21：普通用户/管理员可见性、跨用户同形 404、可见引用过滤、脱敏上传来源、Phase 1 解析占位、
  强/弱 ETag 与 304、内部字段防泄漏。
- API-22：共同授权、真实字节与长度、中文/恶意文件名安全头、MIME 降级、Range 不绕权、流关闭、
  对象缺失/打开失败 503、响应无 MinIO 信息。
- 两接口继续受 `FEATURE_BID_ASSESSMENT_V1_RUNTIME` 控制；测试只使用临时 SQLite 和假对象存储，
  不连接 MinIO、Redis、CentOS 或 ECS。

## 8. 验证结果与下一步

- 机器合同：`61 passed, 1 warning`；
- API-21/22 运行时专项：`5 passed, 1 warning`；
- Assessment API 全量：`73 passed, 1 warning`；
- `0083`—`0091` 迁移合同与运行服务：`55 passed, 2 warnings`；
- 配置、健康、鉴权与 RBAC 相邻回归：`33 passed, 23 warnings`；
- `compileall` 通过，`git diff --check` 无格式错误。

以上测试只使用本地临时 SQLite 和内存假对象存储，没有运行 OCR/视觉解析、真实样例或模型调用，
也没有连接 MinIO、Redis、CentOS 或 ECS。代码 Alembic head 保持 `20260811_0091`。

下一项不是直接凭文件名生成标段，而是先冻结 Phase 2 的解析运行表、页/Sheet/OCR 质量权威来源、
Document Worker 状态/事件、失败恢复和 LotCandidate 生成输入，再评审迁移并实现 API-30。
