# MinIO 文件存储部署说明

> 第 18 步：MinIO 文件存储与临时下载链接

## 1. CentOS 启动 quote-minio

`rag_docker/docker-compose.yml` 已新增独立服务 `quote-minio`，避免占用已有 RAGFlow/Milvus MinIO。

端口：

- API：`192.168.88.128:9002`
- Console：`http://192.168.88.128:9003`

默认账号：

- `quoteadmin`
- `change-this-password`

启动命令：

```bash
cd /opt/rag_service
docker compose up -d quote-minio
docker compose ps quote-minio
```

如需修改账号密码，可在 CentOS `/opt/rag_service/.env` 中设置：

```env
QUOTE_MINIO_ROOT_USER=quoteadmin
QUOTE_MINIO_ROOT_PASSWORD=change-this-password
```

## 2. Windows 后端配置

`AI_Middle_Office/.env` 增加：

```env
MINIO_ENABLED=true
MINIO_ENDPOINT=192.168.88.128:9002
MINIO_ACCESS_KEY=quoteadmin
MINIO_SECRET_KEY=change-this-password
MINIO_SECURE=false
MINIO_BUCKET=quote-files
MINIO_PRESIGNED_EXPIRE_SECONDS=3600
MINIO_MAX_UPLOAD_MB=50
```

安装依赖：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
pip install -r requirements.txt
```

重启 FastAPI 后，进入：

```text
http://localhost:9000/admin.html
```

在“MinIO 文件存储”面板中点击“检测”，状态应为 `ready`。

## 3. API

- `POST /api/v1/files`：上传文件，返回文件元数据和临时下载链接
- `GET /api/v1/files`：查询文件列表，普通用户只看本人，管理员可看全部
- `GET /api/v1/files/{file_id}/download_url`：生成临时下载链接
- `GET /api/v1/admin/files/storage/health`：管理员查看存储健康状态

临时下载链接默认 3600 秒有效，可通过 `MINIO_PRESIGNED_EXPIRE_SECONDS` 调整。

## 4. 报价任务附件

`MINIO_ENABLED=true` 后，`POST /api/v1/quote/jobs` 上传的图纸/清单图片会先写入 MinIO。

任务表只保存：

- `file_name`
- `file_mime_type`
- `file_object_id`

Worker 执行任务时会通过 `file_object_id` 从 MinIO 拉取附件内容，再交给 GLM-4V 识别。若 MinIO 未启用，系统会自动回退到旧的 `file_base64` 存储逻辑。
