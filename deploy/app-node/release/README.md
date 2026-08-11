# Two-command production release

日常上线只保留两步：本地运行 `.\release.cmd`，然后把它打印的
`sudo ai-release deploy <release-id>` 粘贴到阿里云 SSM。脚本会自动完成专项
测试、构建、HTTPS 上传、哈希校验、备份、切换、健康检查和失败回滚。

This workflow turns the normal no-migration application release into two
commands while keeping the existing backup and rollback controls.

It uses the application's authenticated HTTPS file API and existing MinIO.
It does not require public SSH, an OSS bucket, a paid registry, a temporary
Linux upload account, or manual Base64 reconstruction.

## Safety boundaries

- Only a clean, committed Git tree can be packaged by default.
- Changed backend paths select focused tests from `test-map.json`. An unmapped
  backend change blocks the release instead of silently running no tests or
  falling back to the full suite.
- Frontend changes run the production Vite build.
- Image/OCR and bid-assessment Agent changes stop until the user explicitly
  approves their tests. Agent code also needs separate release approval.
- Any Alembic-head change is detected before build. The reusable ECS manager
  currently rejects migration releases and directs them to a dedicated,
  approval-gated migration runbook.
- The ECS manager verifies every part, the complete archive, image identity,
  non-root runtime contract, current database head and idle queue.
- It creates a fresh, checksummed database backup before every cutover.
- API and Worker are switched together. A failed health or runtime gate
  automatically restores the previous image and `.env`.
- The new image, previous image, backup, release tar and generated rollback
  script are retained. Temporary MinIO objects and `file_objects` rows are
  removed automatically after a successful cutover.

## One-time ECS installation

Install `ecs-ai-release` as `/usr/local/sbin/ai-release`, owned by root with
mode `0700`. Perform this through Alibaba Cloud SSM/Cloud Assistant; do not
reopen public SSH just to install it.

```bash
sudo install -o root -g root -m 0700 /path/to/ecs-ai-release /usr/local/sbin/ai-release
sudo /usr/local/sbin/ai-release status
```

Generate the exact one-time, hash-verified SSM command locally:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\deploy\app-node\release\New-AiReleaseBootstrap.ps1
```

Copy the single line from the generated file into SSM. Its gzip + Base64
payload remains below the Cloud Assistant command-size boundary; no file
upload, public SSH or OSS is needed. This bootstrap is only required once.

## Normal future release

From a clean worktree containing the intended release commit:

```powershell
.\release.cmd
```

The script:

1. compares the commit with the locally recorded production baseline;
2. shows the exact changed scope and focused test plan;
3. runs only those backend tests plus the frontend build when applicable;
4. builds and inspects the immutable Docker image;
5. saves and hashes the image, splits it into 24 MiB parts;
6. prompts for the administrator password without storing it;
7. uploads the parts and manifest through HTTPS;
8. prints one short ECS command.

Run the printed command in SSM:

```bash
sudo /usr/local/sbin/ai-release deploy rel-YYYYMMDD-HHMMSS-abcdef0
```

After the page is manually accepted, ask Codex to run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\deploy\app-node\release\Confirm-AiRelease.ps1 `
  -ReleaseId rel-YYYYMMDD-HHMMSS-abcdef0
```

That records the new production commit in the repository's private Git
metadata. It is not committed and does not mix operational state into source
changes. A new workstation falls back to `production-baseline.json` until its
local marker is initialized.

## Useful commands

Preview the test and safety plan without building or uploading:

```powershell
.\release.cmd -PlanOnly
```

If automatic Python discovery does not select the local test environment,
pass it explicitly:

```powershell
.\release.cmd -PythonPath "C:\path\to\python.exe" -PlanOnly
```

Build a bundle without uploading it:

```powershell
.\release.cmd -NoUpload
```

Inspect production:

```bash
sudo /usr/local/sbin/ai-release status
```

Retry temporary transfer cleanup if cutover succeeded but cleanup failed:

```bash
sudo /usr/local/sbin/ai-release purge <release-id>
```

Roll back the application image to the version captured for that release:

```bash
sudo /usr/local/sbin/ai-release rollback <release-id>
```

Rollback does not reverse database migrations. Migration releases remain a
separate controlled workflow for that reason.

## Adding focused coverage

When a new backend area is changed for the first time, add a narrow path-to-
test rule to `test-map.json`, or pass one or more paths below
`AI_Middle_Office/tests`:

```powershell
.\release.cmd -AdditionalBackendTests tests/test_the_changed_area.py
```

Do not use `-AllowDirty` for a real release. It exists only to validate the
release tooling while it is being developed.
