# BIZ-3b-0 项目任务成果证据设计

## 背景

BIZ-3a 已完成工程项目进度底座，支持项目、阶段、任务、2580 进度、阻塞、解除阻塞、回退和 EPC 流程模板。当前 EPC 模板中的 `成果文件` 已能展示在任务表中，但还只是文字要求，无法证明某个节点为什么可以提交到 80% 或确认到 100%。

BIZ-3b 的目标是把“进度条”从单纯人工点击，升级为“有依据、有成果、有审计”的项目进度。BIZ-3b-0 只做设计，不编码、不新增数据库迁移、不改变当前运行功能。

## 设计目标

1. 每个项目任务可以登记成果证据，包括文件、外部链接和文字备注。
2. EPC 模板中的成果文件要求成为任务验收提示，不再只是说明文字。
3. 当前单人试运行不增加复杂审批负担，先做轻量提醒和可追溯。
4. 后续多人协同时，可以升级为成果门禁、岗位确认、钉钉/企微待办。
5. 文件存储复用现有 `file_objects` / MinIO 能力，不重复建设文件系统。

## 非目标

- 不做合同级、回款级、成本级档案管理。
- 不把项目任务成果自动写入知识库或 RAG。
- 不在 BIZ-3b-1 就强制所有节点上传文件。
- 不在本阶段接入钉钉/企微审批。
- 不改变 BIZ-3a 既有 2580 状态机。

## 核心口径

### 成果证据

成果证据是挂在 `project_tasks` 下的记录，用来证明任务推进的业务依据。它可以是：

| 类型 | 说明 | 示例 |
| --- | --- | --- |
| `file` | 系统文件对象 | 会议纪要、设计图纸、验收单、报价表 |
| `link` | 外部链接 | 钉钉文档、企微微盘、网盘链接、在线表格 |
| `text` | 纯文字说明 | “客户已电话确认，明天补签纸质单” |

### 成果要求

成果要求来自任务本身，优先级如下：

1. EPC 模板任务的 `epc_deliverable`。
2. 手工任务填写的 `evidence_requirement`。
3. 无要求时显示 `未设置成果要求`。

### 进度关系

成果证据不替代 2580 状态，而是作为状态推进依据：

| 任务状态 | 当前语义 | BIZ-3b 建议 |
| --- | --- | --- |
| `todo` 0% | 未开始 | 不要求成果 |
| `started` 25% | 已开始 | 可登记过程证据 |
| `progressing` 50% | 推进中 | 可登记过程证据 |
| `submitted` 80% | 已提交待确认 | 提醒补充成果证据 |
| `done` 100% | 项目经理确认完成 | 关键节点可要求至少 1 条证据 |

## 数据模型建议

### 新增表：`project_task_evidences`

用于保存任务成果证据。BIZ-3b-1 实施时通过 Alembic 新增。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | Integer PK | 是 | 主键 |
| `project_id` | Integer FK | 是 | 冗余项目 ID，便于查询 |
| `stage_id` | Integer FK | 是 | 冗余阶段 ID，便于阶段汇总 |
| `task_id` | Integer FK | 是 | 所属任务 |
| `evidence_type` | String(32) | 是 | `file` / `link` / `text` |
| `title` | String(255) | 是 | 成果标题 |
| `description` | Text | 否 | 成果说明 |
| `file_object_id` | String(36) | 否 | 关联 `file_objects.file_id` |
| `external_url` | String(1024) | 否 | 外部链接 |
| `external_provider` | String(64) | 否 | `dingtalk` / `wecom` / `other` |
| `requirement_snapshot` | Text | 否 | 创建时的成果要求快照 |
| `status` | String(32) | 是 | `active` / `removed` |
| `created_by` | Integer FK | 是 | 上传/登记人 |
| `created_at` | DateTime | 是 | 创建时间 |
| `updated_at` | DateTime | 是 | 更新时间 |
| `removed_by` | Integer FK | 否 | 删除人 |
| `removed_at` | DateTime | 否 | 删除时间 |
| `remove_reason` | Text | 否 | 删除原因 |

约束建议：

- `evidence_type='file'` 时必须有 `file_object_id`。
- `evidence_type='link'` 时必须有 `external_url`。
- `evidence_type='text'` 时必须有 `description`。
- 默认只做软删除，保留审计痕迹。

索引建议：

- `(task_id, status, created_at)`
- `(project_id, status, created_at)`
- `(stage_id, status)`
- `(file_object_id)`

### 可选新增字段：`project_tasks.evidence_policy`

如果 BIZ-3b-2 要做门禁，可在 `project_tasks` 增加轻量策略字段：

| 值 | 说明 |
| --- | --- |
| `optional` | 可上传，不强制 |
| `submit_remind` | 提交 80% 时提醒 |
| `complete_required` | 完成 100% 前必须有证据 |

首版建议不新增该字段，先用服务层规则：

- EPC 模板任务默认 `submit_remind`。
- 关键节点默认 `complete_required`，可由后续模板配置覆盖。
- 手工任务默认 `optional`。

若后续需要每个任务单独配置，再新增字段。

## 文件存储策略

系统已有统一文件能力：

- `file_objects`：保存文件元数据。
- `POST /api/v1/files`：上传文件到 MinIO。
- `GET /api/v1/files/{file_id}/download_url`：生成短时下载链接。

BIZ-3b 不直接保存文件 bytes，只保存 `file_object_id` 作为引用。

上传建议：

| 场景 | 处理 |
| --- | --- |
| MinIO 开启 | 通过 `/files` 上传，`purpose=project_task_evidence` |
| MinIO 未开启 | BIZ-3b-1 可以先只开放链接和文字证据 |
| 文件过大 | 沿用 `MINIO_MAX_UPLOAD_MB` |
| 文件下载 | 走后端鉴权和短时签名 URL |

## 权限设计

| 操作 | 项目经理 | 任务负责人 | 协作者 | 项目查看者 |
| --- | --- | --- | --- | --- |
| 查看证据 | 是 | 是 | 是 | 是 |
| 上传/登记证据 | 是 | 是 | 是 | 否 |
| 删除自己证据 | 是 | 是 | 是 | 否 |
| 删除他人证据 | 是 | 否 | 否 | 否 |
| 作为完成依据确认 | 是 | 否 | 否 | 否 |

单人试运行阶段，创建人通常同时是项目经理和任务负责人，因此操作路径保持简单。

## API 设计草案

### 查询任务证据

`GET /api/v1/admin/project-tasks/{task_id}/evidences`

返回当前任务所有 `active` 证据，同时返回成果要求和统计：

```json
{
  "requirement": "会议及纪要；工作确认单",
  "evidence_count": 2,
  "has_file": true,
  "has_link": false,
  "items": []
}
```

### 新增文字或链接证据

`POST /api/v1/admin/project-tasks/{task_id}/evidences`

```json
{
  "evidence_type": "link",
  "title": "设计方案确认链接",
  "description": "客户已在线批注确认",
  "external_url": "https://...",
  "external_provider": "dingtalk"
}
```

### 关联已上传文件

同一接口，传 `file_object_id`：

```json
{
  "evidence_type": "file",
  "title": "现场勘察照片汇总",
  "file_object_id": "uuid-file-id",
  "description": "现场照片和原始结构记录"
}
```

### 删除证据

`DELETE /api/v1/admin/project-task-evidences/{evidence_id}`

```json
{
  "reason": "上传错项目，已重新上传"
}
```

删除后写入任务事件，证据本身软删除。

### 任务提交/完成响应补充

任务推进接口保持 URL 不变。返回体补充：

```json
{
  "evidence_summary": {
    "requirement": "验收表甲方签字确认及备案",
    "evidence_count": 0,
    "policy": "submit_remind",
    "warning": "当前节点尚未登记成果证据"
  }
}
```

## 前端交互设计

### 任务表

在 `我的项目任务` 和 `项目详情` 中展示：

- 成果要求：来自 `epc_deliverable`。
- 证据数量：如 `证据 2`。
- 缺证据提示：关键节点显示橙色提示。

### 任务证据抽屉

点击任务的 `证据` 按钮打开抽屉：

1. 顶部显示成果要求。
2. 显示现有证据列表。
3. 支持新增三类证据：
   - 上传文件。
   - 填外部链接。
   - 填文字说明。
4. 每条证据显示登记人、时间、类型、下载/打开链接。
5. 项目经理可删除任意证据，普通负责人只能删除自己登记的证据。

### 提交与完成提示

首版建议采用“提醒优先，少阻断”：

| 动作 | 无证据时行为 |
| --- | --- |
| 推进到 50% | 不提示 |
| 提交到 80% | 弹窗提醒，可继续提交 |
| 完成到 100% | 关键节点二次确认，项目经理可继续完成 |

BIZ-3b-2 再把关键节点升级为硬门禁。

## 事件审计

新增证据相关事件类型，继续写入 `project_task_events`：

| 事件 | 说明 |
| --- | --- |
| `task_evidence_added` | 新增成果证据 |
| `task_evidence_removed` | 删除成果证据 |
| `task_evidence_downloaded` | 可选，下载审计，公网前建议启用 |
| `task_completion_without_evidence` | 无证据完成关键节点 |

事件 message 示例：

- `新增成果证据：现场勘察照片汇总`
- `删除成果证据：上传错项目，已重新上传`
- `关键节点无成果证据仍确认完成：项目经理确认纸质资料线下留存`

## 单人试运行策略

当前只有一个人使用时，BIZ-3b-1 建议保持轻量：

1. 默认不强制上传文件。
2. 允许文字证据代替文件。
3. 完成关键节点时只做醒目提醒，不阻断。
4. 所有证据默认当前账号登记。
5. 后续多人上线前，再把部分 EPC 关键节点切到 `complete_required`。

## 钉钉/企微预留

先不接外部系统，但字段预留：

| 字段 | 预留用途 |
| --- | --- |
| `external_provider` | `dingtalk` / `wecom` |
| `external_url` | 钉钉文档、企微微盘、审批单链接 |
| `external_object_id` | 后续如果需要，可扩展保存外部文件 ID |
| `sync_status` | 后续如果自动同步，可扩展保存同步状态 |

后续接入方式：

1. 先支持手动粘贴钉钉/企微链接。
2. 再支持把任务提醒推送到钉钉/企微。
3. 最后再考虑自动拉取外部审批或文件元数据。

## 分阶段实施建议

### BIZ-3b-1：成果证据最小可用版

范围：

- Alembic 新增 `project_task_evidences`。
- 新增证据模型、服务、API。
- 支持文字、链接、已上传文件引用。
- 前端任务证据抽屉。
- 任务表显示证据数量。
- 不做硬门禁。

验收：

- 能给 EPC 任务新增文字证据。
- 能上传文件并关联到任务。
- 能填写外部链接。
- 证据出现在任务详情和项目事件中。
- 删除证据为软删除并写审计。

### BIZ-3b-2：提交/完成提醒与软门禁

范围：

- 提交 80% 时提醒缺证据。
- 完成 100% 时对关键节点二次确认。
- 返回 `evidence_summary`。
- 项目详情显示缺证据节点。

验收：

- 无证据提交时能看到提示。
- 项目经理无证据完成关键节点时必须填写确认说明。
- 事件记录无证据完成原因。

### BIZ-3b-3：关键节点硬门禁

范围：

- 支持任务级 `evidence_policy`。
- EPC 关键节点默认至少 1 条证据才能完成。
- 项目经理可临时豁免，但必须填写原因。

验收：

- `complete_required` 且无证据时返回 409。
- 有证据后可完成。
- 豁免完成写入审计事件。

## 预计涉及文件

BIZ-3b-1 预计涉及：

| 文件 | 说明 |
| --- | --- |
| `app/models/project_progress.py` | 增加 `ProjectTaskEvidence` 模型 |
| `alembic/versions/*_add_project_task_evidences.py` | 新增迁移 |
| `app/services/project_progress.py` | 证据序列化、权限、统计 |
| `app/api/v1/project_progress.py` | 证据 CRUD 接口 |
| `ai-web/src/App.vue` | 任务证据抽屉和任务表证据数量 |
| `tests/test_project_progress_biz3a.py` 或新增测试 | 证据 API 和权限测试 |

## 风险与取舍

| 风险 | 取舍 |
| --- | --- |
| 一开始强制上传会增加单人试运行负担 | 先提醒，不硬阻断 |
| 文件权限处理不严会泄露项目资料 | 下载走后端鉴权，复用短时签名 URL |
| 外部链接长期失效 | 链接作为辅助证据，关键节点建议上传文件或文字说明 |
| 证据过多导致任务表拥挤 | 表格只显示数量，详情进抽屉 |
| MinIO 未开启影响验收 | 首版支持文字和链接，文件上传按环境能力启用 |

## BIZ-3b-0 验收标准

- 已明确成果证据的数据模型和权限边界。
- 已明确文件存储复用 `file_objects`，不重复建设。
- 已明确首版单人试运行不做强制门禁。
- 已拆分 BIZ-3b-1 / BIZ-3b-2 / BIZ-3b-3。
- 已明确后续钉钉/企微接入从手动链接开始，不直接做深度集成。

## 结论

BIZ-3b 推荐先从“任务成果证据最小可用版”开始，而不是直接做复杂审批。这样既能让项目进度有依据，又不会打断当前单人试运行节奏。等真实项目跑出常见成果类型和缺失场景后，再逐步升级关键节点门禁和外部协同。
