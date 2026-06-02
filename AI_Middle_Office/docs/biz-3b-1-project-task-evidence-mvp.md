# BIZ-3b-1 项目任务成果证据最小可用版

## 背景

BIZ-3b-0 已明确项目任务成果证据设计。本阶段进入最小可用实现，让 EPC 项目任务可以登记文字、外部链接和文件类成果证据，使任务推进具备可追溯依据。

本阶段仍不做硬门禁，不改变 2580 进度规则，不接钉钉/企微自动审批。

## 数据库变更

新增 Alembic：

`20260601_0027_add_project_task_evidences.py`

新增表：

`project_task_evidences`

核心字段：

| 字段 | 说明 |
| --- | --- |
| `project_id` | 所属项目 |
| `stage_id` | 所属阶段 |
| `task_id` | 所属任务 |
| `evidence_type` | `text` / `link` / `file` |
| `title` | 成果标题 |
| `description` | 成果说明或文字证据正文 |
| `file_object_id` | 文件证据关联 `file_objects.file_id` |
| `external_url` | 外部链接 |
| `external_provider` | `dingtalk` / `wecom` / `other` |
| `requirement_snapshot` | 登记证据时的成果要求快照 |
| `status` | `active` / `removed` |
| `created_by` | 登记人 |
| `removed_by` / `removed_at` / `remove_reason` | 软删除审计 |

## 后端接口

### 查询任务证据

`GET /api/v1/admin/project-tasks/{task_id}/evidences`

返回任务成果要求、证据数量、类型统计和证据列表。

### 新增任务证据

`POST /api/v1/admin/project-tasks/{task_id}/evidences`

支持三种类型：

```json
{
  "evidence_type": "text",
  "title": "客户口头确认记录",
  "description": "客户已电话确认，纸质签字后补。"
}
```

```json
{
  "evidence_type": "link",
  "title": "钉钉文档链接",
  "external_url": "https://example.com/doc",
  "external_provider": "dingtalk"
}
```

```json
{
  "evidence_type": "file",
  "title": "现场勘察文件",
  "file_object_id": "file-object-id"
}
```

文件上传仍走已有接口：

`POST /api/v1/files`，`purpose=project_task_evidence`

### 删除任务证据

`DELETE /api/v1/admin/project-task-evidences/{evidence_id}`

请求体：

```json
{
  "reason": "上传错项目，已重新上传"
}
```

删除为软删除，写入 `task_evidence_removed` 事件。

### 生成文件下载链接

`GET /api/v1/admin/project-task-evidences/{evidence_id}/download_url`

该接口按项目访问权限生成短时下载链接，避免直接依赖 `/files/{file_id}/download_url` 的上传人权限。

## 前端变化

Vite 后台项目进度页：

- `我的项目任务` 表新增 `证据 N` 按钮。
- `项目详情` 的任务表新增 `证据 N` 按钮。
- 点击后打开成果证据抽屉。
- 抽屉顶部显示成果要求，优先使用 EPC 节点的成果文件字段。
- 支持新增：
  - 文字说明。
  - 外部链接，来源可选钉钉、企微、其他。
  - 上传文件，先调用 `/files`，再关联到任务证据。
- 支持打开链接/下载文件。
- 支持删除证据，删除时必须填写原因。

## 权限规则

| 操作 | 规则 |
| --- | --- |
| 查看证据 | 能访问该项目任务即可查看 |
| 新增证据 | 项目经理或任务负责人 |
| 删除证据 | 项目经理可删全部，登记人可删自己登记的证据 |
| 文件下载 | 能访问该任务即可通过证据下载接口获取短时链接 |

## 事件审计

新增事件：

| 事件 | 说明 |
| --- | --- |
| `task_evidence_added` | 新增成果证据 |
| `task_evidence_removed` | 删除成果证据 |

项目动态会显示新增/删除成果证据动作。

## 当前边界

- 不强制提交或完成前必须有证据。
- 文件上传依赖 `MINIO_ENABLED=true`；若当前环境未启用 MinIO，仍可使用文字和链接证据。
- 外部链接只做格式校验和留痕，不校验链接长期有效性。
- 不自动同步钉钉/企微文件，只支持手工粘贴链接。

## 验收口径

1. EPC 项目任务显示 `证据 0`。
2. 可以新增文字证据，证据数量变为 1。
3. 可以新增外部链接证据，并可打开链接。
4. MinIO 可用时，可以上传文件并关联为文件证据。
5. 删除证据需要填写原因，删除后证据数量减少。
6. 项目动态能看到新增和删除证据事件。
7. 原任务开始、推进、提交、完成、阻塞、回退逻辑不受影响。

## 运行要求

本阶段新增数据库迁移。当前环境需要执行：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -m alembic upgrade head
```

然后重启后端。

## 后续

BIZ-3b-2 建议继续做“提交/完成提醒与软门禁”：

- 提交 80% 时提示缺成果证据。
- 完成 100% 时关键节点要求二次确认。
- 项目详情汇总缺证据节点。
