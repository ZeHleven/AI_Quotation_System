#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly APP_ROOT="/opt/ai-middle-office/app-node"
readonly APP_COMPOSE="${APP_ROOT}/compose.yaml"
readonly APP_ENV="/etc/ai-middle-office/app.env"
readonly DIFY_COMPOSE="${SCRIPT_DIR}/dify-compose.sh"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly ENV_BACKUP="/etc/ai-middle-office/app.env.before-single-ecs-${STAMP}"

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}
for required in "${APP_COMPOSE}" "${APP_ENV}" "${DIFY_COMPOSE}"; do
  [[ -f "${required}" ]] || {
    echo "ERROR|missing_required_file|${required}" >&2
    exit 1
  }
done
command -v python3 >/dev/null || {
  echo "ERROR|python3_required" >&2
  exit 1
}

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == active ]]; then
  echo "ERROR|public_nginx_must_remain_stopped_before_activation" >&2
  exit 1
fi
for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  if docker inspect "${container}" >/dev/null 2>&1 && \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" == true ]]; then
    echo "ERROR|application_must_remain_stopped_before_activation|${container}" >&2
    exit 1
  fi
done

required_dependencies=(
  ai-middle-office-mysql quote-redis quote-minio
  milvus-etcd milvus-minio milvus-standalone rag-api-service n8n
  dify-nginx-1 dify-api-1 dify-worker-1 dify-redis-1 dify-db_postgres-1
)
for container in "${required_dependencies[@]}"; do
  if ! docker inspect "${container}" >/dev/null 2>&1 || \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|required_dependency_not_running|${container}" >&2
    exit 1
  fi
done
if docker inspect dify-worker_beat-1 >/dev/null 2>&1 && \
  [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|dify_worker_beat_must_remain_stopped_before_activation" >&2
  exit 1
fi

cp -a -- "${APP_ENV}" "${ENV_BACKUP}"
chmod 0600 "${ENV_BACKUP}"

tmp_env="$(mktemp /etc/ai-middle-office/.app.env.single-ecs.XXXXXX)"
cleanup() {
  rm -f -- "${tmp_env}"
}
trap cleanup EXIT

python3 - "${APP_ENV}" "${tmp_env}" <<'PY'
import os
import re
import sys
from urllib.parse import urlsplit

source, target = sys.argv[1:]
with open(source, "r", encoding="utf-8") as stream:
    lines = stream.read().splitlines()

values = {}
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value

migration_url = values.get("MIGRATION_DATABASE_URL", "").strip()
if migration_url:
    raise SystemExit("ERROR|migration_database_url_must_be_empty_on_runtime")

database_url = values.get("DATABASE_URL", "").strip()
if not database_url:
    raise SystemExit("ERROR|database_url_missing")

match = re.match(
    r"^([^:]+://(?:[^/@]+@)?)(?:\[[^\]]+\]|[^/:?#]+)(?::\d+)?(/.*)$",
    database_url,
)
if not match:
    raise SystemExit("ERROR|database_url_shape_unexpected")
database_url = match.group(1) + "ai-mysql:3306" + match.group(2)

internal_hosts = [
    "localhost",
    "127.0.0.1",
    "ai-mysql",
    "mysql",
    "quote-redis",
    "quote-minio",
    "rag-service",
    "n8n",
    "dify-nginx",
]
existing_no_proxy = []
for key in ("NO_PROXY", "no_proxy"):
    existing_no_proxy.extend(
        item.strip() for item in values.get(key, "").split(",") if item.strip()
    )
no_proxy = ",".join(dict.fromkeys(existing_no_proxy + internal_hosts))

updates = {
    "DATABASE_URL": database_url,
    "CELERY_BROKER_URL": "redis://quote-redis:6379/0",
    "CELERY_RESULT_BACKEND": "redis://quote-redis:6379/1",
    "RAG_SERVICE_URL": "http://rag-service:8001",
    "N8N_WEBHOOK_URL_CALC": "http://n8n:5678/webhook/budget-calc-no-rag",
    "N8N_WEBHOOK_URL_PUSH": "http://n8n:5678/webhook/budget-push",
    "MINIO_ENDPOINT": "quote-minio:9000",
    "NO_PROXY": no_proxy,
    "no_proxy": no_proxy,
}

seen = set()
rendered = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        rendered.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        if key not in seen:
            rendered.append(f"{key}={updates[key]}")
            seen.add(key)
        continue
    rendered.append(line)
for key, value in updates.items():
    if key not in seen:
        rendered.append(f"{key}={value}")

selected = {**values, **updates}
for key in (
    "DATABASE_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "RAG_SERVICE_URL",
    "N8N_WEBHOOK_URL_CALC",
    "N8N_WEBHOOK_URL_PUSH",
    "MINIO_ENDPOINT",
):
    value = selected[key]
    if "192.168.88.128" in value:
        raise SystemExit(f"ERROR|source_endpoint_remains|key={key}")

expected_hosts = {
    "DATABASE_URL": "ai-mysql",
    "CELERY_BROKER_URL": "quote-redis",
    "CELERY_RESULT_BACKEND": "quote-redis",
    "RAG_SERVICE_URL": "rag-service",
    "N8N_WEBHOOK_URL_CALC": "n8n",
    "N8N_WEBHOOK_URL_PUSH": "n8n",
}
for key, host in expected_hosts.items():
    if (urlsplit(selected[key]).hostname or "") != host:
        raise SystemExit(f"ERROR|unexpected_internal_host|key={key}")

with open(target, "w", encoding="utf-8", newline="\n") as stream:
    stream.write("\n".join(rendered) + "\n")
os.chmod(target, 0o600)
PY

chown --reference="${APP_ENV}" "${tmp_env}"
chmod 0600 "${tmp_env}"
mv -f -- "${tmp_env}" "${APP_ENV}"
echo "PASS|application_environment=switched_to_internal_aliases"
echo "ENV_BACKUP|${ENV_BACKUP}"

AI_APP_ENV_FILE="${APP_ENV}" docker compose \
  --project-directory "${APP_ROOT}" -f "${APP_COMPOSE}" \
  --profile worker config --quiet

AI_APP_ENV_FILE="${APP_ENV}" docker compose \
  --project-directory "${APP_ROOT}" -f "${APP_COMPOSE}" \
  --profile worker up -d --force-recreate api worker

api_health=starting
for _ in $(seq 1 60); do
  api_health="$(docker inspect ai-middle-office-app-api-1 \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    2>/dev/null || true)"
  if [[ "${api_health}" == healthy ]]; then
    break
  fi
  if [[ "${api_health}" == exited || "${api_health}" == dead ]]; then
    echo "ERROR|application_api_stopped_during_start|health=${api_health}" >&2
    docker logs --tail 120 ai-middle-office-app-api-1 >&2 || true
    exit 1
  fi
  sleep 3
done
[[ "${api_health}" == healthy ]] || {
  echo "ERROR|application_api_health_timeout|health=${api_health}" >&2
  docker logs --tail 120 ai-middle-office-app-api-1 >&2 || true
  exit 1
}

timeout 60 docker exec -i ai-middle-office-app-api-1 python - <<'PY'
import json
import os
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from app.tasks.celery_app import celery_app


def finish(code, message):
    stream = sys.stdout if code == 0 else sys.stderr
    print(message, file=stream, flush=True)
    os._exit(code)


try:
    with urllib.request.urlopen("http://127.0.0.1:9000/health/ready", timeout=20) as response:
        payload = json.loads(response.read())
    if response.status != 200 or payload.get("status") != "ready":
        finish(1, "ERROR|application_ready_payload_failed")
    services = payload.get("external_dependencies", {}).get("services", [])
    if services and not all(service.get("ok") for service in services):
        finish(1, "ERROR|application_external_dependency_failed")

    ping = {}
    for _ in range(8):
        ping = celery_app.control.inspect(timeout=5).ping() or {}
        if ping:
            break
        time.sleep(2)
    if len(ping) != 1:
        finish(1, f"ERROR|unexpected_application_worker_count|count={len(ping)}")

    topology = {
        "database": urlsplit(os.environ["DATABASE_URL"]).hostname,
        "broker": urlsplit(os.environ["CELERY_BROKER_URL"]).hostname,
        "rag": urlsplit(os.environ["RAG_SERVICE_URL"]).hostname,
        "n8n_calc": urlsplit(os.environ["N8N_WEBHOOK_URL_CALC"]).hostname,
        "n8n_push": urlsplit(os.environ["N8N_WEBHOOK_URL_PUSH"]).hostname,
        "minio": os.environ["MINIO_ENDPOINT"].split(":", 1)[0],
    }
    expected = {
        "database": "ai-mysql",
        "broker": "quote-redis",
        "rag": "rag-service",
        "n8n_calc": "n8n",
        "n8n_push": "n8n",
        "minio": "quote-minio",
    }
    if topology != expected:
        finish(1, "ERROR|application_runtime_topology_mismatch")
    print(
        "PASS|application_runtime_topology|"
        + "|".join(f"{key}={value}" for key, value in topology.items()),
        flush=True,
    )
    finish(0, "PASS|application_ready|worker_count=1|external_dependencies=ok")
except BaseException as error:
    finish(1, f"ERROR|application_activation_probe|error_type={type(error).__name__}")
PY

"${DIFY_COMPOSE}" up -d --no-deps worker_beat
if [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" != true ]]; then
  echo "ERROR|dify_worker_beat_failed_to_start" >&2
  docker logs --tail 100 dify-worker_beat-1 >&2 || true
  exit 1
fi
echo "PASS|dify_worker_beat=running"

nginx -t
systemctl start nginx
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == active ]] || {
  echo "ERROR|public_nginx_failed_to_start" >&2
  exit 1
}

trap - EXIT
echo "PASS|public_nginx=active"
echo "RESULT|target_final_application_activate=passed"
echo "INFO|single_ecs_cutover=active"
echo "INFO|source_centos_must_remain_stopped_for_48_hour_rollback_window"
