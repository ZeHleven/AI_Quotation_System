#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly API_CONTAINER="ai-middle-office-app-api-1"
readonly WORKER_CONTAINER="ai-middle-office-app-worker-1"
readonly MYSQL_CONTAINER="ai-middle-office-mysql"
readonly N8N_CONTAINER="n8n"
readonly MYSQL_IMAGE="mysql:8.0.39"
readonly APP_ROOT="/opt/ai-middle-office/app-node"
readonly APP_COMPOSE="${APP_ROOT}/compose.yaml"
readonly APP_DOTENV="${APP_ROOT}/.env"
readonly APP_ENV="/etc/ai-middle-office/app.env"
readonly MYSQL_CA="/etc/ai-middle-office/mysql-ca.pem"
readonly MIGRATION_ENV="/root/.quote-consistency-phase1-migration.env"
readonly N8N_ENV="/etc/ai-middle-office/n8n.env"
readonly APP_NETWORK="ai-middle-office-app-net"
readonly OLD_IMAGE="ai-middle-office-app:20260805_161737"
readonly OLD_IMAGE_TAG="20260805_161737"
readonly CANDIDATE_IMAGE="ai-middle-office-app:20260808_consistency1_candidate"
readonly CANDIDATE_IMAGE_TAG="20260808_consistency1_candidate"
readonly EXPECTED_CANDIDATE_ID="sha256:9056820a2d3e7215b4d8d9f53c8e6fd165e178e59d2ceafaa3668368710d9099"
readonly CANDIDATE_PUSH_URL="http://n8n:5678/webhook/budget-push-phase1-candidate"
readonly PRODUCTION_WORKFLOW_ID="UPGK6O16kr0xtO9z"
readonly CANDIDATE_WORKFLOW_ID="QpP1Cand20260808"
readonly BACKUP_FILE="/data/ai-middle-office/backups/quote-consistency-phase1/pre-0082-20260808T143433Z/ai_quotation-pre-0082.sql.gz"
readonly EXPECTED_BACKUP_SHA256="6d80f27bac273e045eead419eb58ba99c6fd8be8a6baa70cac0ae554ad28d795"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly STATE_DIR="/root/quote-consistency-phase1-cutover/${STAMP}"
readonly N8N_BACKUP_DIR="/data/ai-middle-office/backups/quote-consistency-phase1/cutover-${STAMP}"
readonly N8N_BACKUP="${N8N_BACKUP_DIR}/n8n-before-production-cutover.tar.gz"
readonly FINAL_DB_BACKUP="${N8N_BACKUP_DIR}/ai_quotation-final-pre-cutover.sql.gz"

temp_dir=""
n8n_image_id=""
cutover_started=false
migration_started=false
migration_complete=false
n8n_mutated=false
app_env_changed=false
app_dotenv_changed=false
success=false

log() {
  printf '%s\n' "$*"
}

container_running() {
  [[ "$(docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null || true)" == "true" ]]
}

wait_container_health() {
  local container="$1"
  local attempts="$2"
  local state=""
  local health=""
  local _attempt
  for _attempt in $(seq 1 "${attempts}"); do
    state="$(docker inspect "${container}" --format '{{.State.Status}}' 2>/dev/null || true)"
    health="$(docker inspect "${container}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
    if [[ "${state}" == "running" && ("${health}" == "healthy" || "${health}" == "running") ]]; then
      return 0
    fi
    if [[ "${state}" == "exited" || "${state}" == "dead" ]]; then
      return 1
    fi
    sleep 3
  done
  return 1
}

compose_up_with_tag() {
  local image_tag="$1"
  AI_APP_IMAGE_TAG="${image_tag}" \
  AI_APP_ENV_FILE="${APP_ENV}" \
    docker compose \
      --project-directory "${APP_ROOT}" \
      -f "${APP_COMPOSE}" \
      --profile worker \
      up -d --no-build --force-recreate api worker
}

probe_application_idle() {
  timeout 60 docker exec -i "${API_CONTAINER}" python - <<'PY'
import os

import redis

from app.tasks.celery_app import celery_app


def count(mapping):
    return sum(len(items or []) for items in (mapping or {}).values())


inspector = celery_app.control.inspect(timeout=5)
ping = inspector.ping() or {}
active = count(inspector.active())
reserved = count(inspector.reserved())
scheduled = count(inspector.scheduled())
client = redis.Redis.from_url(
    os.environ["CELERY_BROKER_URL"],
    socket_connect_timeout=5,
    socket_timeout=5,
)
queue_name = celery_app.conf.task_default_queue or "celery"
queued = client.llen(queue_name)
unacked = client.hlen("unacked")
unacked_index = client.zcard("unacked_index")
print(
    "RESULT|application_idle="
    f"workers={len(ping)}|active={active}|reserved={reserved}|"
    f"scheduled={scheduled}|queue={queue_name}|queued={queued}|"
    f"unacked={unacked}|unacked_index={unacked_index}",
    flush=True,
)
if len(ping) != 1 or any((active, reserved, scheduled, queued, unacked, unacked_index)):
    raise SystemExit("ERROR|application_not_idle")
PY
}

probe_database_baseline() {
  timeout 45 docker exec -i "${API_CONTAINER}" python - <<'PY'
from sqlalchemy import text

from app.core.database import engine


with engine.connect() as connection:
    row = connection.execute(
        text(
            """
            SELECT
              (SELECT version_num FROM alembic_version),
              (
                SELECT COUNT(*) FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN ('quote_push_attempts', 'quote_quota_reservations')
              ),
              (
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND (
                    (TABLE_NAME = 'users' AND COLUMN_NAME = 'quota_reserved')
                    OR
                    (TABLE_NAME = 'quote_jobs' AND COLUMN_NAME IN
                      ('source_job_id', 'attempt_id', 'started_at'))
                  )
              )
            """
        )
    ).one()
current, tables, columns = row
if current == "20260801_0081" and int(tables) == 0 and int(columns) == 0:
    mode = "fresh_0081"
elif current == "20260808_0082" and int(tables) == 2 and int(columns) == 4:
    with engine.connect() as resume_connection:
        counts = resume_connection.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM users WHERE quota_reserved IS NULL OR quota_reserved <> 0),
                  (SELECT COUNT(*) FROM quote_quota_reservations),
                  (SELECT COUNT(*) FROM quote_push_attempts),
                  (
                    SELECT COUNT(*) FROM quote_jobs
                    WHERE source_job_id IS NOT NULL OR attempt_id IS NOT NULL OR started_at IS NOT NULL
                  )
                """
            )
        ).one()
    if any(int(value) != 0 for value in counts):
        raise SystemExit("ERROR|database_resume_0082_contains_phase1_runtime_data")
    mode = "safe_resume_0082"
else:
    raise SystemExit("ERROR|database_baseline_changed")
print(
    f"RESULT|database_baseline=head={current}|new_tables={int(tables)}|"
    f"new_columns={int(columns)}|mode={mode}",
    flush=True,
)
PY
}

export_and_assert_workflow() {
  local workflow_id="$1"
  local expected_active="$2"
  local expected_path="$3"
  local expected_nodes="$4"
  local kind="$5"
  local remote_file="/tmp/phase1-${kind}-${STAMP}.json"
  local local_file="${temp_dir}/${kind}.json"

  docker exec "${N8N_CONTAINER}" rm -f -- "${remote_file}" >/dev/null 2>&1 || true
  docker exec "${N8N_CONTAINER}" n8n export:workflow \
    --id="${workflow_id}" --output="${remote_file}" >/dev/null
  docker cp "${N8N_CONTAINER}:${remote_file}" "${local_file}" >/dev/null
  docker exec "${N8N_CONTAINER}" rm -f -- "${remote_file}" >/dev/null 2>&1 || true

  python3 - "${local_file}" "${workflow_id}" "${expected_active}" \
    "${expected_path}" "${expected_nodes}" "${kind}" <<'PY'
import json
import sys


path, expected_id, expected_active, expected_path, expected_nodes, kind = sys.argv[1:]
with open(path, "r", encoding="utf-8") as stream:
    raw = json.load(stream)
items = raw if isinstance(raw, list) else raw.get("data", [raw])
if len(items) != 1:
    raise SystemExit("ERROR|workflow_export_count_mismatch")
workflow = items[0]
if workflow.get("id") != expected_id:
    raise SystemExit("ERROR|workflow_id_mismatch")
active = bool(workflow.get("active"))
if active != (expected_active == "true"):
    raise SystemExit(f"ERROR|workflow_active_mismatch|kind={kind}|active={str(active).lower()}")
nodes = workflow.get("nodes") or []
if expected_nodes != "any" and len(nodes) != int(expected_nodes):
    raise SystemExit(f"ERROR|workflow_node_count_mismatch|kind={kind}|nodes={len(nodes)}")
webhooks = [node for node in nodes if node.get("type") == "n8n-nodes-base.webhook"]
if len(webhooks) != 1 or webhooks[0].get("parameters", {}).get("path") != expected_path:
    raise SystemExit(f"ERROR|workflow_webhook_mismatch|kind={kind}")

if kind == "candidate":
    by_name = {node.get("name"): node for node in nodes}
    connections = workflow.get("connections") or {}
    required_names = {
        "Phase1 Validate Idempotency Key",
        "Phase1 Claim Push",
        "Phase1 Is Newly Claimed",
        "Phase1 Respond Existing State",
        "Phase1 Mark Dispatching",
        "Phase1 Mark Delivered",
        "Phase1 Respond Delivered",
    }
    if not required_names.issubset(by_name):
        raise SystemExit("ERROR|candidate_required_nodes_missing")
    claim_outputs = connections.get("Phase1 Is Newly Claimed", {}).get("main", [])
    if len(claim_outputs) != 2 or len(claim_outputs[0]) != 1 or len(claim_outputs[1]) != 1:
        raise SystemExit("ERROR|candidate_claim_branch_shape_mismatch")
    if claim_outputs[1][0].get("node") != "Phase1 Respond Existing State":
        raise SystemExit("ERROR|candidate_duplicate_branch_not_isolated")
    delivered_outputs = connections.get("Phase1 Mark Delivered", {}).get("main", [])
    if (
        len(delivered_outputs) != 1
        or len(delivered_outputs[0]) != 1
        or delivered_outputs[0][0].get("node") != "Phase1 Respond Delivered"
    ):
        raise SystemExit("ERROR|candidate_success_response_not_after_delivery_callback")
    existing_response = by_name["Phase1 Respond Existing State"].get("parameters", {})
    response_options = existing_response.get("options") or {}
    if response_options.get("responseCode") != 409:
        raise SystemExit("ERROR|candidate_duplicate_response_not_fixed_conflict")
    serialized = json.dumps(workflow, ensure_ascii=False)
    for required in (
        "http://api:9000/api/v1/internal/n8n/quote-push/claim",
        "http://api:9000/api/v1/internal/n8n/quote-push/dispatch-start",
        "http://api:9000/api/v1/internal/n8n/quote-push/delivered",
    ):
        if required not in serialized:
            raise SystemExit("ERROR|candidate_callback_missing")
    for forbidden in ("192.168.88.128", "192.168.1.21"):
        if forbidden in serialized:
            raise SystemExit("ERROR|candidate_old_endpoint_present")

print(
    f"RESULT|workflow_gate={kind}|id={expected_id}|"
    f"active={str(active).lower()}|nodes={len(nodes)}|path={expected_path}",
    flush=True,
)
PY
}

validate_secret_and_runtime_files() {
  local migration_mode migration_uid migration_gid
  migration_mode="$(stat -c '%a' "${MIGRATION_ENV}")"
  migration_uid="$(stat -c '%u' "${MIGRATION_ENV}")"
  migration_gid="$(stat -c '%g' "${MIGRATION_ENV}")"
  [[ "${migration_mode}" == "600" && "${migration_uid}" == "0" && "${migration_gid}" == "0" ]] || {
    log "ERROR|migration_env_permissions_invalid|mode=${migration_mode}|uid=${migration_uid}|gid=${migration_gid}"
    return 1
  }

  python3 - "${MIGRATION_ENV}" "${APP_ENV}" <<'PY'
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


def values(path):
    result = {}
    counts = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        key = key.strip()
        result[key] = value.strip()
        counts[key] = counts.get(key, 0) + 1
    return result, counts


migration, migration_counts = values(sys.argv[1])
runtime, runtime_counts = values(sys.argv[2])
if set(migration) != {"MIGRATION_DATABASE_URL"} or migration_counts.get("MIGRATION_DATABASE_URL") != 1:
    raise SystemExit("ERROR|migration_env_shape_invalid")
parsed = urlsplit(migration["MIGRATION_DATABASE_URL"])
query = parse_qs(parsed.query)
if (
    parsed.scheme != "mysql+pymysql"
    or unquote(parsed.username or "") != "ai_migrator"
    or parsed.hostname != "ai-mysql"
    or parsed.port != 3306
    or parsed.path != "/ai_quotation"
    or query.get("ssl_ca") != ["/run/secrets/mysql-ca.pem"]
):
    raise SystemExit("ERROR|migration_database_url_contract_invalid")
if runtime.get("MIGRATION_DATABASE_URL", "").strip():
    raise SystemExit("ERROR|runtime_contains_migration_database_url")
if runtime_counts.get("N8N_WEBHOOK_URL_PUSH") != 1:
    raise SystemExit("ERROR|runtime_push_url_duplicate_or_missing")
if runtime.get("N8N_WEBHOOK_URL_PUSH") != "http://n8n:5678/webhook/budget-push":
    raise SystemExit("ERROR|runtime_push_url_not_old_baseline")
if runtime.get("AUTO_RUN_DB_MIGRATIONS", "").strip().lower() != "false":
    raise SystemExit("ERROR|runtime_auto_migrations_not_false")
print("PASS|migration_secret_contract=ai_migrator@ai-mysql:3306|tls_ca=present|runtime_separation=true")
PY
}

write_migrator_client_config() {
  local target="${temp_dir}/migrator.cnf"
  python3 - "${MIGRATION_ENV}" "${target}" <<'PY'
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


source, target = sys.argv[1:]
line = next(
    (
        raw_line.split("=", 1)[1].strip()
        for raw_line in Path(source).read_text(encoding="utf-8").splitlines()
        if raw_line.startswith("MIGRATION_DATABASE_URL=")
    ),
    "",
)
parsed = urlsplit(line)
password = unquote(parsed.password or "")
if not password:
    raise SystemExit("ERROR|migration_password_missing")


def option(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


content = "\n".join(
    (
        "[client]",
        'user="ai_migrator"',
        f"password={option(password)}",
        'host="ai-mysql"',
        "port=3306",
        "protocol=tcp",
        "default-character-set=utf8mb4",
        "ssl-mode=VERIFY_CA",
        'ssl-ca="/run/secrets/mysql-ca.pem"',
        "",
    )
)
with open(target, "x", encoding="utf-8", newline="\n") as stream:
    stream.write(content)
os.chmod(target, 0o600)
PY
}

create_final_database_backup() {
  docker run --rm \
    --user root \
    --network "${APP_NETWORK}" \
    --ip 10.240.10.10 \
    --volume "${temp_dir}/migrator.cnf:/run/secrets/mysql-client.cnf:ro" \
    --volume "${MYSQL_CA}:/run/secrets/mysql-ca.pem:ro" \
    "${MYSQL_IMAGE}" \
    mysqldump \
      --defaults-extra-file=/run/secrets/mysql-client.cnf \
      --single-transaction \
      --quick \
      --skip-lock-tables \
      --skip-triggers \
      --skip-routines \
      --skip-events \
      --no-tablespaces \
      --set-gtid-purged=OFF \
      --databases ai_quotation \
    | gzip -1 > "${FINAL_DB_BACKUP}"

  gzip -t "${FINAL_DB_BACKUP}"
  gzip -cd "${FINAL_DB_BACKUP}" | tail -n 8 | grep -Fq -- '-- Dump completed on'
  local backup_sha256
  backup_sha256="$(sha256sum "${FINAL_DB_BACKUP}" | awk '{print $1}')"
  log "RESULT|final_database_backup=${FINAL_DB_BACKUP}|sha256=${backup_sha256}|size=$(stat -c '%s' "${FINAL_DB_BACKUP}")"
}

verify_schema_with_migrator() {
  docker run --rm -i \
    --network "${APP_NETWORK}" \
    --ip 10.240.10.10 \
    --env-file "${MIGRATION_ENV}" \
    --volume "${MYSQL_CA}:/run/secrets/mysql-ca.pem:ro" \
    "${CANDIDATE_IMAGE}" \
    python - <<'PY'
import os

from sqlalchemy import create_engine, text


engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], pool_pre_ping=True)
with engine.connect() as connection:
    row = connection.execute(
        text(
            """
            SELECT
              (SELECT version_num FROM alembic_version),
              (
                SELECT COUNT(*) FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN ('quote_push_attempts', 'quote_quota_reservations')
              ),
              (
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND (
                    (TABLE_NAME = 'users' AND COLUMN_NAME = 'quota_reserved')
                    OR
                    (TABLE_NAME = 'quote_jobs' AND COLUMN_NAME IN
                      ('source_job_id', 'attempt_id', 'started_at'))
                  )
              ),
              (SELECT COUNT(*) FROM users WHERE quota_reserved IS NULL OR quota_reserved <> 0),
              (SELECT COUNT(*) FROM quote_quota_reservations),
              (SELECT COUNT(*) FROM quote_push_attempts),
              (
                SELECT COUNT(*) FROM quote_jobs
                WHERE source_job_id IS NOT NULL OR attempt_id IS NOT NULL OR started_at IS NOT NULL
              )
            """
        )
    ).one()
head, tables, columns, invalid_users, quota_rows, push_rows, job_values = row
print(
    f"RESULT|production_schema=head={head}|new_tables={tables}|new_columns={columns}|"
    f"invalid_users={invalid_users}|quota_rows={quota_rows}|push_rows={push_rows}|"
    f"existing_job_values={job_values}",
    flush=True,
)
if tuple(map(str, row)) != ("20260808_0082", "2", "4", "0", "0", "0", "0"):
    raise SystemExit("ERROR|production_schema_assertion_failed")
PY
}

stop_and_backup_n8n() {
  n8n_mutated=true
  docker stop --time 90 "${N8N_CONTAINER}" >/dev/null

  tar --numeric-owner -C /data/ai-middle-office -czf "${N8N_BACKUP}" n8n
  gzip -t "${N8N_BACKUP}"
  local n8n_backup_sha256
  n8n_backup_sha256="$(sha256sum "${N8N_BACKUP}" | awk '{print $1}')"
  log "RESULT|n8n_cutover_backup=${N8N_BACKUP}|sha256=${n8n_backup_sha256}|size=$(stat -c '%s' "${N8N_BACKUP}")"
  log "PASS|n8n_writer=frozen_and_backed_up"
}

activate_candidate_workflow_cold() {
  if container_running "${N8N_CONTAINER}"; then
    log "ERROR|n8n_must_be_stopped_before_candidate_activation"
    return 1
  fi
  docker run --rm \
    --volumes-from "${N8N_CONTAINER}" \
    --env-file "${N8N_ENV}" \
    --entrypoint n8n \
    "${n8n_image_id}" \
    update:workflow --id="${CANDIDATE_WORKFLOW_ID}" --active=true

  docker start "${N8N_CONTAINER}" >/dev/null
  wait_container_health "${N8N_CONTAINER}" 60 || {
    docker logs --tail 160 "${N8N_CONTAINER}" >&2 || true
    log "ERROR|n8n_health_timeout_after_candidate_activation"
    return 1
  }
  export_and_assert_workflow \
    "${CANDIDATE_WORKFLOW_ID}" true budget-push-phase1-candidate 21 candidate
  log "PASS|n8n_candidate=active|legacy_workflow_retained_for_rollback=true"
}

update_runtime_files() {
  local app_env_temp compose_env_temp
  app_env_temp="$(mktemp /etc/ai-middle-office/.app.env.phase1.XXXXXXXX)"
  compose_env_temp="$(mktemp "${APP_ROOT}/.env.phase1.XXXXXXXX")"

  python3 - "${APP_ENV}" "${app_env_temp}" "${CANDIDATE_PUSH_URL}" <<'PY'
import os
import sys


source, target, value = sys.argv[1:]
with open(source, "r", encoding="utf-8") as stream:
    lines = stream.read().splitlines()
key = "N8N_WEBHOOK_URL_PUSH"
count = sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() == key)
if count != 1:
    raise SystemExit("ERROR|runtime_push_url_duplicate_or_missing")
rendered = []
for line in lines:
    if line.strip() and not line.lstrip().startswith("#") and "=" in line and line.split("=", 1)[0].strip() == key:
        rendered.append(f"{key}={value}")
    else:
        rendered.append(line)
with open(target, "w", encoding="utf-8", newline="\n") as stream:
    stream.write("\n".join(rendered) + "\n")
os.chmod(target, 0o600)
PY
  chown --reference="${APP_ENV}" "${app_env_temp}"
  chmod 0600 "${app_env_temp}"
  app_env_changed=true
  mv -f -- "${app_env_temp}" "${APP_ENV}"

  python3 - "${APP_DOTENV}" "${compose_env_temp}" "${CANDIDATE_IMAGE_TAG}" <<'PY'
import os
import sys
from pathlib import Path


source, target, value = sys.argv[1:]
path = Path(source)
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
key = "AI_APP_IMAGE_TAG"
count = sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#") and "=" in line and line.split("=", 1)[0].strip() == key)
if count > 1:
    raise SystemExit("ERROR|compose_image_tag_duplicate")
rendered = []
updated = False
for line in lines:
    if line.strip() and not line.lstrip().startswith("#") and "=" in line and line.split("=", 1)[0].strip() == key:
        rendered.append(f"{key}={value}")
        updated = True
    else:
        rendered.append(line)
if not updated:
    rendered.append(f"{key}={value}")
with open(target, "w", encoding="utf-8", newline="\n") as stream:
    stream.write("\n".join(rendered) + "\n")
os.chmod(target, 0o600)
PY
  if [[ -f "${APP_DOTENV}" ]]; then
    chown --reference="${APP_DOTENV}" "${compose_env_temp}"
  else
    chown root:root "${compose_env_temp}"
  fi
  chmod 0600 "${compose_env_temp}"
  app_dotenv_changed=true
  mv -f -- "${compose_env_temp}" "${APP_DOTENV}"

  AI_APP_IMAGE_TAG="${CANDIDATE_IMAGE_TAG}" \
  AI_APP_ENV_FILE="${APP_ENV}" \
    docker compose \
      --project-directory "${APP_ROOT}" \
      -f "${APP_COMPOSE}" \
      --profile worker config --quiet
  log "PASS|runtime_configuration=candidate_image_and_candidate_webhook_persisted"
}

verify_candidate_application() {
  wait_container_health "${API_CONTAINER}" 90 || {
    docker logs --tail 200 "${API_CONTAINER}" >&2 || true
    log "ERROR|candidate_api_health_timeout"
    return 1
  }
  container_running "${WORKER_CONTAINER}" || {
    docker logs --tail 200 "${WORKER_CONTAINER}" >&2 || true
    log "ERROR|candidate_worker_not_running"
    return 1
  }

  for container in "${API_CONTAINER}" "${WORKER_CONTAINER}"; do
    [[ "$(docker inspect "${container}" --format '{{.Config.Image}}')" == "${CANDIDATE_IMAGE}" ]] || {
      log "ERROR|candidate_container_image_name_mismatch|${container}"
      return 1
    }
    [[ "$(docker inspect "${container}" --format '{{.Image}}')" == "${EXPECTED_CANDIDATE_ID}" ]] || {
      log "ERROR|candidate_container_image_id_mismatch|${container}"
      return 1
    }
  done

  timeout 120 docker exec -i "${API_CONTAINER}" python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.main import app
from app.tasks.celery_app import celery_app


with urllib.request.urlopen("http://127.0.0.1:9000/health/ready", timeout=20) as response:
    ready = json.loads(response.read())
if response.status != 200 or ready.get("status") != "ready":
    raise SystemExit("ERROR|candidate_ready_payload_failed")

ping = {}
for _ in range(10):
    ping = celery_app.control.inspect(timeout=5).ping() or {}
    if ping:
        break
    time.sleep(2)
if len(ping) != 1:
    raise SystemExit(f"ERROR|candidate_worker_count|count={len(ping)}")

if settings.n8n_webhook_url_push != "http://n8n:5678/webhook/budget-push-phase1-candidate":
    raise SystemExit("ERROR|candidate_runtime_push_url_mismatch")
paths = {route.path for route in app.routes}
required_paths = {
    "/api/v1/internal/n8n/quote-push/claim",
    "/api/v1/internal/n8n/quote-push/dispatch-start",
    "/api/v1/internal/n8n/quote-push/delivered",
}
if not required_paths.issubset(paths):
    raise SystemExit("ERROR|candidate_internal_routes_missing")

with engine.connect() as connection:
    head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
if head != "20260808_0082":
    raise SystemExit(f"ERROR|candidate_database_head|head={head}")

payload = json.dumps(
    {"idempotency_key": "0" * 64, "quote_job_id": None},
    separators=(",", ":"),
).encode()
unauthorized = urllib.request.Request(
    "http://127.0.0.1:9000/api/v1/internal/n8n/quote-push/claim",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    urllib.request.urlopen(unauthorized, timeout=10)
    raise SystemExit("ERROR|internal_callback_accepted_without_secret")
except urllib.error.HTTPError as error:
    if error.code != 401:
        raise SystemExit(f"ERROR|internal_callback_unauthorized_status|http={error.code}")

authorized = urllib.request.Request(
    "http://127.0.0.1:9000/api/v1/internal/n8n/quote-push/claim",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "X-Webhook-Secret": settings.webhook_secret,
    },
    method="POST",
)
try:
    urllib.request.urlopen(authorized, timeout=10)
    raise SystemExit("ERROR|internal_callback_unknown_attempt_accepted")
except urllib.error.HTTPError as error:
    if error.code != 409:
        raise SystemExit(f"ERROR|internal_callback_authorized_status|http={error.code}")

print(
    "RESULT|candidate_application=ready|worker_count=1|database_head=20260808_0082|"
    "internal_callback_auth=passed",
    flush=True,
)
PY
}

probe_candidate_n8n_without_delivery() {
  timeout 120 docker exec -i "${API_CONTAINER}" python - <<'PY'
import json
import urllib.error
import urllib.request
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app as _application  # Import all model modules before mapper configuration.
from app.models.quote_job import QuotePushAttempt
from app.services.quote_consistency import (
    PUSH_FAILED,
    PUSH_N8N_CLAIMED,
    PUSH_SENDING,
    QuoteConsistencyError,
    mark_quote_push_external_delivered,
    mark_quote_push_failed_before_dispatch,
    start_quote_push_attempt,
)


marker = str(uuid.uuid4())
payload = {
    "quote_job_id": None,
    "project_details": [],
    "phase1_no_delivery_probe": marker,
}
db = SessionLocal()
key = None
safe_cleanup_statuses = {PUSH_SENDING, PUSH_N8N_CLAIMED, PUSH_FAILED}
try:
    start = start_quote_push_attempt(
        db,
        username="__phase1_no_delivery_probe__",
        quote_job_id=None,
        payload=payload,
    )
    start.attempt.status = PUSH_N8N_CLAIMED
    key = start.attempt.idempotency_key
    db.commit()

    request_body = dict(payload)
    request_body["idempotency_key"] = key
    encoded = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        settings.n8n_webhook_url_push,
        data=encoded,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Secret": settings.webhook_secret,
        },
        method="POST",
    )
    http_status = None
    response_body = b""
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            http_status = response.status
            response_body = response.read()
    except urllib.error.HTTPError as error:
        http_status = error.code
        response_body = error.read()

    db.expire_all()
    attempt = db.query(QuotePushAttempt).filter(QuotePushAttempt.idempotency_key == key).one()
    if attempt.status != PUSH_N8N_CLAIMED:
        raise SystemExit(
            f"ERROR|n8n_claim_callback_not_observed|status={attempt.status}|http={http_status}"
        )
    if http_status not in {200, 409}:
        raise SystemExit(f"ERROR|n8n_duplicate_gate_status_unexpected|http={http_status}")

    try:
        mark_quote_push_external_delivered(
            db,
            attempt_id=attempt.id,
            status_code=http_status,
            response_text="PHASE1_DUPLICATE_GATE_PROBE",
        )
    except QuoteConsistencyError as error:
        if str(error) != f"QUOTE_PUSH_NOT_SENDING_{PUSH_N8N_CLAIMED}":
            raise SystemExit(f"ERROR|backend_delivery_gate_unexpected|detail={error}") from error
        db.rollback()
    else:
        raise SystemExit("ERROR|backend_delivery_gate_accepted_unconfirmed_delivery")

    mark_quote_push_failed_before_dispatch(
        db,
        idempotency_key=key,
        quote_job_id=None,
        error_message="PHASE1_NO_DELIVERY_PROBE_CLEANUP",
    )
    db.commit()
    db.expire_all()
    attempt = db.query(QuotePushAttempt).filter(QuotePushAttempt.idempotency_key == key).one()
    if attempt.status != PUSH_FAILED or attempt.external_delivered_at is not None:
        raise SystemExit("ERROR|n8n_no_delivery_probe_cleanup_state_invalid")
    db.delete(attempt)
    db.commit()
    key = None
    print(
        f"RESULT|n8n_candidate_no_delivery_probe=passed|claim_callback=observed|"
        f"duplicate_gate=blocked_before_delivery|http={http_status}|"
        f"db_status={PUSH_N8N_CLAIMED}|response_bytes={len(response_body)}|"
        f"backend_delivery_gate=rejected_unconfirmed_state|"
        f"synthetic_row=removed",
        flush=True,
    )
finally:
    if key:
        db.rollback()
        attempt = db.query(QuotePushAttempt).filter(QuotePushAttempt.idempotency_key == key).first()
        if attempt is not None and attempt.status in safe_cleanup_statuses and attempt.external_delivered_at is None:
            db.delete(attempt)
            db.commit()
    db.close()
PY
}

restore_runtime_files() {
  if [[ "${app_env_changed}" == true && -f "${STATE_DIR}/app.env.before" ]]; then
    cp -a -- "${STATE_DIR}/app.env.before" "${APP_ENV}"
  fi
  if [[ "${app_dotenv_changed}" == true ]]; then
    if [[ -f "${STATE_DIR}/app-dotenv.before" ]]; then
      cp -a -- "${STATE_DIR}/app-dotenv.before" "${APP_DOTENV}"
    elif [[ -f "${STATE_DIR}/app-dotenv.was-absent" ]]; then
      rm -f -- "${APP_DOTENV}"
    fi
  fi
}

deactivate_candidate_workflow_for_rollback() {
  if [[ "${n8n_mutated}" != true ]]; then
    return 0
  fi
  if container_running "${N8N_CONTAINER}"; then
    docker stop --time 90 "${N8N_CONTAINER}" >/dev/null 2>&1 || true
  fi
  docker run --rm \
    --volumes-from "${N8N_CONTAINER}" \
    --env-file "${N8N_ENV}" \
    --entrypoint n8n \
    "${n8n_image_id}" \
    update:workflow --id="${CANDIDATE_WORKFLOW_ID}" --active=false >/dev/null 2>&1 || true
  docker start "${N8N_CONTAINER}" >/dev/null 2>&1 || true
  wait_container_health "${N8N_CONTAINER}" 60 >/dev/null 2>&1 || true
}

cleanup_temp() {
  if [[ -n "${temp_dir}" && -d "${temp_dir}" ]]; then
    rm -f -- "${temp_dir}"/* >/dev/null 2>&1 || true
    rmdir -- "${temp_dir}" >/dev/null 2>&1 || true
  fi
}

on_exit() {
  local rc=$?
  trap - EXIT INT TERM HUP
  set +e

  if [[ "${success}" == true ]]; then
    cleanup_temp
    exit 0
  fi

  if [[ "${cutover_started}" != true ]]; then
    cleanup_temp
    log "ROLLBACK|production_cutover=not_started|exit=${rc}"
    exit "${rc}"
  fi

  log "ROLLBACK|production_cutover=begin|exit=${rc}"
  systemctl stop nginx >/dev/null 2>&1 || true
  restore_runtime_files
  deactivate_candidate_workflow_for_rollback

  compose_up_with_tag "${OLD_IMAGE_TAG}" >/dev/null 2>&1 || true
  local app_recovered=false
  if wait_container_health "${API_CONTAINER}" 90 >/dev/null 2>&1 && container_running "${WORKER_CONTAINER}"; then
    app_recovered=true
  fi
  if [[ "${app_recovered}" == true ]] && nginx -t >/dev/null 2>&1; then
    systemctl start nginx >/dev/null 2>&1 || true
  fi
  log "ROLLBACK|production_cutover=old_application_restore_attempted|app_ready=${app_recovered}"
  if [[ "${migration_started}" == true ]]; then
    log "INFO|rollback_database=additive_schema_not_downgraded|migration_complete=${migration_complete}"
  fi
  log "INFO|rollback_state_dir=${STATE_DIR}"
  log "INFO|rollback_n8n_backup=${N8N_BACKUP}"
  log "INFO|rollback_database_backup=${FINAL_DB_BACKUP}"
  cleanup_temp
  exit "${rc}"
}
trap on_exit EXIT

on_signal() {
  log "ERROR|signal_received|signal=$1"
  exit 130
}
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

[[ "${EUID}" -eq 0 ]] || {
  log "ERROR|root_required"
  exit 1
}

for command in docker python3 sha256sum gzip tar stat systemctl nginx timeout awk grep seq curl df tail mktemp flock; do
  command -v "${command}" >/dev/null || {
    log "ERROR|required_command_missing|${command}"
    exit 1
  }
done
docker compose version >/dev/null
exec 9>/root/.quote-consistency-phase1-cutover.lock
flock -n 9 || {
  log "ERROR|another_phase1_cutover_is_running"
  exit 1
}

for file in "${APP_COMPOSE}" "${APP_ENV}" "${MYSQL_CA}" "${MIGRATION_ENV}" "${N8N_ENV}" "${BACKUP_FILE}"; do
  [[ -f "${file}" ]] || {
    log "ERROR|required_file_missing|${file}"
    exit 1
  }
done

for container in "${API_CONTAINER}" "${WORKER_CONTAINER}" "${MYSQL_CONTAINER}" "${N8N_CONTAINER}"; do
  container_running "${container}" || {
    log "ERROR|required_container_not_running|${container}"
    exit 1
  }
done

[[ "$(docker inspect "${API_CONTAINER}" --format '{{.Config.Image}}')" == "${OLD_IMAGE}" ]] || {
  log "ERROR|unexpected_api_image_before_cutover"
  exit 1
}
[[ "$(docker inspect "${WORKER_CONTAINER}" --format '{{.Config.Image}}')" == "${OLD_IMAGE}" ]] || {
  log "ERROR|unexpected_worker_image_before_cutover"
  exit 1
}
[[ "$(docker inspect "${MYSQL_CONTAINER}" --format '{{.Config.Image}}')" == "${MYSQL_IMAGE}" ]] || {
  log "ERROR|unexpected_mysql_image_before_cutover"
  exit 1
}
[[ "$(docker inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')" == "${EXPECTED_CANDIDATE_ID}" ]] || {
  log "ERROR|candidate_image_id_mismatch"
  exit 1
}
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]] || {
  log "ERROR|nginx_not_active_before_cutover"
  exit 1
}
wait_container_health "${MYSQL_CONTAINER}" 1 || {
  log "ERROR|mysql_not_healthy_before_cutover"
  exit 1
}
wait_container_health "${N8N_CONTAINER}" 1 || {
  log "ERROR|n8n_not_healthy_before_cutover"
  exit 1
}

actual_backup_sha256="$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"
[[ "${actual_backup_sha256}" == "${EXPECTED_BACKUP_SHA256}" ]] || {
  log "ERROR|backup_sha256_mismatch|actual=${actual_backup_sha256}"
  exit 1
}
gzip -t "${BACKUP_FILE}"
log "PASS|production_backup_gate|sha256=${actual_backup_sha256}"

disk_available_kib="$(df -Pk /data | awk 'NR==2 {print $4}')"
memory_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
[[ "${disk_available_kib}" -ge 2097152 ]] || {
  log "ERROR|cutover_disk_below_2gib|available_kib=${disk_available_kib}"
  exit 1
}
[[ "${memory_available_kib}" -ge 2097152 ]] || {
  log "ERROR|cutover_memory_below_2gib|available_kib=${memory_available_kib}"
  exit 1
}
log "PASS|cutover_capacity_gate|disk_available_kib=${disk_available_kib}|memory_available_kib=${memory_available_kib}"

validate_secret_and_runtime_files
probe_database_baseline
probe_application_idle

n8n_image_id="$(docker inspect "${N8N_CONTAINER}" --format '{{.Image}}')"
[[ "${n8n_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  log "ERROR|n8n_image_id_invalid"
  exit 1
}
n8n_update_help="$(docker exec "${N8N_CONTAINER}" n8n update:workflow --help 2>&1)" || {
  log "ERROR|n8n_cli_update_help_failed"
  exit 1
}
if ! grep -Fq -- '--active' <<<"${n8n_update_help}"; then
  log "ERROR|n8n_cli_active_option_missing"
  exit 1
fi

temp_dir="$(mktemp -d /root/quote-phase1-cutover.XXXXXXXX)"
chmod 0700 "${temp_dir}"
write_migrator_client_config
export_and_assert_workflow "${PRODUCTION_WORKFLOW_ID}" true budget-push any production
export_and_assert_workflow "${CANDIDATE_WORKFLOW_ID}" false budget-push-phase1-candidate 21 candidate

install -d -o root -g root -m 0700 "${STATE_DIR}" "${N8N_BACKUP_DIR}"
cp -a -- "${APP_ENV}" "${STATE_DIR}/app.env.before"
cp -a -- "${APP_COMPOSE}" "${STATE_DIR}/compose.yaml.before"
if [[ -f "${APP_DOTENV}" ]]; then
  cp -a -- "${APP_DOTENV}" "${STATE_DIR}/app-dotenv.before"
else
  : > "${STATE_DIR}/app-dotenv.was-absent"
  chmod 0600 "${STATE_DIR}/app-dotenv.was-absent"
fi
{
  log "timestamp_utc=${STAMP}"
  log "old_image=${OLD_IMAGE}"
  log "candidate_image=${CANDIDATE_IMAGE}"
  log "candidate_image_id=${EXPECTED_CANDIDATE_ID}"
  log "database_backup=${BACKUP_FILE}"
  log "database_backup_sha256=${EXPECTED_BACKUP_SHA256}"
  log "production_workflow_id=${PRODUCTION_WORKFLOW_ID}"
  log "candidate_workflow_id=${CANDIDATE_WORKFLOW_ID}"
} > "${STATE_DIR}/preflight-state.txt"
chmod 0600 "${STATE_DIR}"/*
log "PASS|production_preflight=complete|state_dir=${STATE_DIR}"

cutover_started=true
systemctl stop nginx
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]] || {
  log "ERROR|nginx_failed_to_stop"
  exit 1
}
log "PASS|public_ingress=frozen"

probe_application_idle
docker stop --time 90 "${WORKER_CONTAINER}" >/dev/null
docker stop --time 45 "${API_CONTAINER}" >/dev/null
for container in "${API_CONTAINER}" "${WORKER_CONTAINER}"; do
  if container_running "${container}"; then
    log "ERROR|application_container_failed_to_stop|${container}"
    exit 1
  fi
done
log "PASS|application_containers=stopped"

stop_and_backup_n8n
create_final_database_backup
sha256sum "${FINAL_DB_BACKUP}" "${N8N_BACKUP}" > "${N8N_BACKUP_DIR}/SHA256SUMS"
chmod 0600 "${N8N_BACKUP_DIR}/SHA256SUMS"
sha256sum --check "${N8N_BACKUP_DIR}/SHA256SUMS"
log "PASS|cutover_backup_manifest=verified|path=${N8N_BACKUP_DIR}/SHA256SUMS"

migration_started=true
docker run --rm \
  --network "${APP_NETWORK}" \
  --ip 10.240.10.10 \
  --env-file "${MIGRATION_ENV}" \
  --volume "${MYSQL_CA}:/run/secrets/mysql-ca.pem:ro" \
  "${CANDIDATE_IMAGE}" \
  python -m alembic upgrade head
migration_complete=true
log "PASS|production_alembic_upgrade=completed"
verify_schema_with_migrator

activate_candidate_workflow_cold
update_runtime_files

compose_up_with_tag "${CANDIDATE_IMAGE_TAG}"
verify_candidate_application
probe_candidate_n8n_without_delivery

nginx -t
systemctl start nginx
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]] || {
  log "ERROR|nginx_failed_to_start_after_cutover"
  exit 1
}

public_code="$(curl -sS --noproxy '*' --resolve 'www.qskingship.com:443:127.0.0.1' \
  -o /dev/null -w '%{http_code}' --max-time 20 https://www.qskingship.com/login || true)"
[[ "${public_code}" == "200" ]] || {
  log "ERROR|public_login_gate_failed|http=${public_code}"
  exit 1
}

success=true
log "RESULT|phase1_production_cutover=passed"
log "RESULT|database_head=20260808_0082"
log "RESULT|application_image=${CANDIDATE_IMAGE}|image_id=${EXPECTED_CANDIDATE_ID}"
log "RESULT|application_push_webhook=budget-push-phase1-candidate"
log "RESULT|n8n_candidate_workflow=${CANDIDATE_WORKFLOW_ID}|active=true"
log "RESULT|n8n_legacy_workflow=${PRODUCTION_WORKFLOW_ID}|active=true|retained_for_rollback=true"
log "RESULT|public_login=http_200|nginx=active"
log "RESULT|cutover_state_dir=${STATE_DIR}"
log "RESULT|final_database_backup=${FINAL_DB_BACKUP}"
log "RESULT|n8n_cutover_backup=${N8N_BACKUP}"
exit 0
