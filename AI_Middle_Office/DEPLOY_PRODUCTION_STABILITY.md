# Production Stability Runbook

This runbook covers the first hardening stage: secret placement, reboot
acceptance, backups, RAG regression, and old VM cleanup.

## 1. Secrets

Keep real secrets only in runtime `.env` files:

- Windows backend: `AI_Middle_Office/.env`
- CentOS compose: `/opt/rag_service/.env`

Tracked files must keep placeholders only:

- `AI_Middle_Office/.env.example`
- `rag_docker/.env.example`
- `rag_docker/docker-compose.yml`
- deployment documents

CentOS setup:

```bash
cd /opt/rag_service
cp .env.example .env
vi .env
```

Required CentOS values:

```env
RELOAD_SECRET=replace-with-real-secret
MILVUS_MINIO_ACCESS_KEY=replace-with-real-user
MILVUS_MINIO_SECRET_KEY=replace-with-real-password
QUOTE_MINIO_ROOT_USER=replace-with-real-user
QUOTE_MINIO_ROOT_PASSWORD=replace-with-real-password
```

Windows `.env` must use the same `RELOAD_SECRET` and quote-MinIO values when
those features are enabled.

Before restarting the CentOS compose stack after changing credential variables,
set `MILVUS_MINIO_*` to the credentials currently used by the existing Milvus
internal MinIO volume. A mismatch can make Milvus fail to read existing vector
data.

## 2. Reboot Acceptance

After Windows and the CentOS VM reboot, start or wait for the system:

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

Then run the acceptance check:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_startup.ps1
```

Expected result:

```text
[PASS] Acceptance check passed
```

The script validates:

- MySQL `192.168.88.128:5455`
- Redis `192.168.88.128:6380`
- RAG service `192.168.88.128:8001`
- n8n `192.168.88.128:5678`
- MinIO `192.168.88.128:9002` when enabled
- RAG `/api/v1/retrieve`
- FastAPI `/health/ready`
- Celery worker health through `/health/ready`

## 3. Backups

Backup script:

```text
rag_docker/backup_production.sh
```

Windows launcher:

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1 -SshUser root
```

The backup includes:

- MySQL dump when `MYSQL_*` variables are configured in `/opt/rag_service/.env`
- quote-MinIO volume
- `rag_materials.json`
- Milvus etcd/minio/milvus volumes
- n8n workflow export when the n8n container is reachable
- `SHA256SUMS` manifest

Backups can contain database data, uploaded files, and the copied CentOS
`.env`; protect `/opt/rag_service/backups` as sensitive production data.

Default backup directory:

```text
/opt/rag_service/backups/YYYYMMDD_HHMMSS
```

For a cold Milvus volume snapshot:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_centos_backup.ps1 -SshUser root -ColdMilvusSnapshot
```

Cold snapshot temporarily stops `standalone`, `etcd`, and `minio`, then restarts
them. Use it during a maintenance window.

## 4. RAG Regression

Run after every knowledge-base change:

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test
C:\Users\12521\miniconda3\python.exe .\eval_rag.py --url http://192.168.88.128:8001 --top_k 5 --cases .\rag_regression_cases.json --min_hit_rate 0.85 --min_mrr 0.60
```

Reports are written to:

```text
rag_eval_reports/
```

Do not publish knowledge-base changes if the command exits with code `2`.

## 5. Old VM Cleanup

Do not delete the old C drive VM directory without a final path confirmation.

Candidate old directory:

```text
C:\Users\12521\Desktop\Linux\vmwareData\CentOS 7 64 位_C_OLD_DO_NOT_DELETE
```

Current active VM:

```text
D:\Desktop_Archive\Linux\vmwareData\CentOS 7 64 位\CentOS 7 64 位.vmx
```

Before cleanup, confirm the C drive path and verify the active D drive VM boots
successfully.
