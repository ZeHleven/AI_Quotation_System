# Single-ECS backend migration assets

These assets prepare the existing public application ECS to host the private
backend dependencies that currently run on the local CentOS VM.

They do not authorize a cutover by themselves. Do not stop the source CentOS,
restore production data, or start this stack until the backup, image, secret,
and maintenance-window gates in
`AI_Middle_Office/docs/security-single-ecs-data-migration-plan-20260806.md`
have passed.

## Security boundary

- Public ingress remains host Nginx on TCP 443 only.
- No service in `compose.backend.yaml` publishes a host port.
- Backend containers join the existing external Docker network
  `ai-middle-office-app-net`, so the existing API (`10.240.10.10`) and Worker
  (`10.240.10.11`) can reach them without changing the current MySQL
  source-host grants during cutover.
- N8N is fixed at `10.240.10.12`; this prevents Docker's dynamic allocator
  from taking the API address while the application containers are stopped.
- Persistent state is stored below `/data/ai-middle-office`.
- Real environment files belong under `/etc/ai-middle-office`, mode `0600`,
  and must never be copied into Git or printed with `docker compose config`.
- Source `/var/lib/docker` is not copied. Images are loaded or pulled
  separately and verified against `image-lock-20260806.txt`.

## Files

- `compose.backend.yaml`: MySQL, quote Redis/MinIO, Milvus, etcd, Milvus
  MinIO, RAG service, and N8N.
- `backend.env.example`: non-secret shape of the root-only backend runtime
  file.
- `image-lock-20260806.txt`: source image IDs captured before migration.
- `compose.dify.override.yaml`: Dify resource/log limits plus the internal
  `dify-nginx` alias used by N8N.
- `scripts/target-readonly-preflight.sh`: read-only capacity/network/runtime
  gate for the existing ECS.
- `scripts/source-cold-export.sh`: source validation by default; creates a
  root-only, checksummed cold backup only with `--create-backup`.
- `scripts/source-readonly-dependency-audit.sh`: emits only N8N workflow
  identity/private origins and RAGFlow container/network relationships; it
  does not print workflow JSON, URL paths, query strings, or credentials.
- `scripts/target-restore.sh`: target validation by default; restores data and
  root-only secrets only with `--apply`.
- `scripts/target-n8n-offline-rewrite.sh`: while target N8N is stopped, creates
  a cold safety backup, rewrites the audited Dify/RAG workflow endpoints to
  internal Docker aliases, unpublishes the legacy inventory schedule, and
  republishes the quote workflows without starting the N8N server.
- `scripts/target-n8n-dark-start.sh`: starts only target N8N after the offline
  rewrite, waits for container health, verifies N8N/RAG/Dify connectivity over
  the private Docker network, and rejects any published N8N host port.
- `scripts/target-pre-dify-worker-readonly-audit.sh`: keeps both Dify workers
  stopped, verifies that the restored Dify Redis has no pending list, delayed,
  or stream work, and proves both fixed-IP application containers can read the
  target MySQL at the expected Alembic head over TLS without changing their
  live environment.
- `scripts/target-dify-worker-dark-start.sh`: starts only the Dify Celery
  worker after the empty-queue gate, verifies it responds to Celery control and
  remains idle, and keeps Dify Beat stopped so no periodic work is scheduled.
- `scripts/target-quote-e2e-dark-smoke.sh`: sends one signed, non-push quote
  request from the existing API container to target N8N's current no-RAG
  webhook, validates a non-empty JSON response through target Dify, and keeps
  Dify Beat and the live application configuration unchanged.
- `scripts/dify-compose.sh`: wraps the original Dify Compose and safely limits
  its Nginx and plugin host mappings to `127.0.0.1`.

Dify remains a separate Compose project because its upstream stack has its own
PostgreSQL, Redis, Weaviate, Sandbox, Plugin Daemon, Web and proxy topology.
The original `/opt/dify/docker/docker-compose.yaml` is copied without secret
expansion. `scripts/dify-compose.sh` creates a root-only runtime copy under
`/run`, adds loopback addresses to the three upstream host port mappings, and
then merges `compose.dify.override.yaml`. Never save expanded `docker compose
config` output because it would expose secrets.

## Intended target paths

```text
/etc/ai-middle-office/backend.env
/etc/ai-middle-office/n8n.env
/etc/ai-middle-office/tender_evidence_index.secret
/data/ai-middle-office/mysql
/data/ai-middle-office/quote-redis
/data/ai-middle-office/quote-minio
/data/ai-middle-office/milvus
/data/ai-middle-office/milvus-etcd
/data/ai-middle-office/milvus-minio
/data/ai-middle-office/rag/model-cache
/data/ai-middle-office/rag/config
/data/ai-middle-office/n8n
```

Run only the read-only preflight at this stage:

```bash
sudo bash scripts/target-readonly-preflight.sh
```

The matching read-only source check is also safe before a maintenance window:

```bash
sudo bash scripts/source-cold-export.sh
sudo bash scripts/source-readonly-dependency-audit.sh
```

The following commands are intentionally gated and must not be run until the
maintenance window and rollback owner are confirmed:

```bash
# Source CentOS: briefly stops formal dependencies, archives consistent data,
# restarts the source stack, and only then exports immutable images online.
sudo bash scripts/source-cold-export.sh --create-backup --include-images

# Target ECS: verifies every checksum and refuses an existing/non-empty target.
sudo bash scripts/target-restore.sh --backup-dir=/path/to/transferred/backup
sudo bash scripts/target-restore.sh --apply --backup-dir=/path/to/transferred/backup
```

The restore command does not start containers or switch application endpoints.
Image verification, dark-start health checks, N8N/Dify URL audit, application
environment cutover, and the 48-hour rollback observation remain separate
gates.
