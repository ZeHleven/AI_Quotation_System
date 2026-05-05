# Step 22 - Database Migration Governance

本系统从第 22 步开始使用 Alembic 管理数据库结构变更。

## 1. 安装依赖

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
pip install -r requirements.txt
```

## 2. 执行迁移

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\upgrade_database.ps1
```

等价命令：

```powershell
C:\Users\12521\miniconda3\python.exe -c "from alembic.config import main; main()" -c alembic.ini upgrade head
```

首次迁移 `20260428_0001_initial_schema` 是“可重复执行的基线迁移”：

- 新数据库：创建 `users`、`quote_history`、`quote_jobs`、`model_call_logs`、`file_objects`
- 已有数据库：只补缺失字段，例如 `users.must_change_password`、`quote_jobs.file_object_id`
- Alembic 会写入 `alembic_version`，以后新增表/字段都通过新 revision 管理

## 3. 一键启动中的自动迁移

`start_all.ps1` 会在启动 FastAPI 前执行：

```powershell
python -c "from alembic.config import main; main()" -c alembic.ini upgrade head
```

可通过 `.env` 控制：

```env
AUTO_RUN_DB_MIGRATIONS=true
```

如果需要临时跳过：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -SkipMigrations
```

## 4. 启动兼容开关

FastAPI 仍保留两个兼容开关，但默认值已经调整为关闭：

```env
AUTO_CREATE_TABLES=false
STARTUP_COMPAT_MIGRATIONS=false
```

含义：

- `AUTO_CREATE_TABLES=true`：启动时仍执行 SQLAlchemy `create_all`，便于测试库和小型本地库自动建表
- `STARTUP_COMPAT_MIGRATIONS=true`：保留旧库缺字段时的保守补列逻辑

生产环境必须显式设置：

```env
AUTO_CREATE_TABLES=false
STARTUP_COMPAT_MIGRATIONS=false
AUTO_RUN_DB_MIGRATIONS=true
```

前提条件：生产数据库初始化或结构变更必须先执行 `alembic upgrade head`，或保持 `AUTO_RUN_DB_MIGRATIONS=true` 由 `start_all.ps1` 在启动 FastAPI 前自动执行。新环境如果关闭自动迁移且没有手动执行 Alembic，FastAPI 不再通过 `create_all` 自动补建表结构。

仅在临时本地 SQLite 沙箱或旧部署救急时，才建议短期开启：

```env
AUTO_CREATE_TABLES=true
STARTUP_COMPAT_MIGRATIONS=true
```

## 5. 新增迁移

后续新增表或字段时：

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
C:\Users\12521\miniconda3\python.exe -c "from alembic.config import main; main()" -c alembic.ini revision -m "describe_change"
```

编辑生成的 `alembic/versions/*.py` 后执行：

```powershell
C:\Users\12521\miniconda3\python.exe -c "from alembic.config import main; main()" -c alembic.ini upgrade head
```

## 6. 查看当前版本

```powershell
C:\Users\12521\miniconda3\python.exe -c "from alembic.config import main; main()" -c alembic.ini current
```
