# GitHub 分支保护与测试流程

## 目标

本仓库以 `main` 作为稳定主分支。所有业务改动应先进入功能分支，通过 Pull Request、自动化检查和人工复核后再合并。

## 推荐分支规则

- `main` 禁止直接推送。
- 合并前必须创建 Pull Request。
- 合并前必须通过以下状态检查：
  - `Backend CI / Backend tests`
  - `Frontend CI / Frontend build`
- 合并前要求分支与 `main` 保持最新。
- 禁止 force push。
- 禁止删除 `main`。
- 管理员也遵守保护规则，除非紧急修复时临时解除。

## 本地提交前检查

后端：

```powershell
cd AI_Middle_Office
python -m compileall app tests
python -m pytest -q
```

前端：

```powershell
cd ai-web
npm ci
npm run build
```

Git 检查：

```powershell
git status --short
git diff --check
```

## Pull Request 流程

1. 从 `main` 拉出功能分支，分支名建议使用 `codex/<topic>`。
2. 提交前运行本地检查，保证没有秘密文件和无关输出。
3. 推送分支并创建 Pull Request。
4. 按 PR 模板填写变更摘要、测试计划和风险清单。
5. 等待 GitHub Actions 通过。
6. 人工复核关键业务流程。
7. 合并到 `main`。

## 生产风险事项

- 新增数据库字段或表必须新增 Alembic revision。
- 不提交 `.env`、密钥、客户资料、真实报价附件或导出的数据库文件。
- Redis/Celery、MySQL、Milvus、MinIO 等基础服务异常时，不应继续验证报价生成结果。
- 大清单报价、Excel 解析、AI 估价等长流程必须保留取消、重试或恢复方案。
