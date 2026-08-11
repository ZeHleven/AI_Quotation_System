#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_COMPOSE="/opt/ai-middle-office/app-node/compose.yaml"
readonly APP_ENV="/etc/ai-middle-office/app.env"
readonly APP_NETWORK="ai-middle-office-app-net"

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}

for required in "${APP_COMPOSE}" "${APP_ENV}"; do
  [[ -f "${required}" ]] || {
    echo "ERROR|missing_required_file|${required}" >&2
    exit 1
  }
done

required_running=(
  ai-middle-office-app-api-1 ai-middle-office-app-worker-1
  ai-middle-office-mysql quote-redis quote-minio
  milvus-etcd milvus-minio milvus-standalone rag-api-service n8n
  dify-nginx-1 dify-api-1 dify-worker-1 dify-redis-1
  dify-db_postgres-1 dify-plugin_daemon-1
)
for container in "${required_running[@]}"; do
  if ! docker inspect "${container}" >/dev/null 2>&1 || \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|required_container_not_running|${container}" >&2
    exit 1
  fi
done

if docker inspect dify-worker_beat-1 >/dev/null 2>&1 && \
  [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|dify_worker_beat_must_remain_stopped" >&2
  exit 1
fi

api_health="$(docker inspect ai-middle-office-app-api-1 \
  --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
[[ "${api_health}" == healthy ]] || {
  echo "ERROR|application_api_not_healthy|health=${api_health}" >&2
  exit 1
}

for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  network_ip="$(docker inspect "${container}" \
    --format '{{with index .NetworkSettings.Networks "ai-middle-office-app-net"}}{{.IPAddress}}{{end}}')"
  [[ -n "${network_ip}" ]] || {
    echo "ERROR|application_missing_internal_network|${container}" >&2
    exit 1
  }
done

timeout 45 docker exec -i ai-middle-office-app-api-1 python - <<'PY'
import os
import sys
from urllib.parse import urlsplit

import redis

from app.tasks.celery_app import celery_app


def finish(code, message):
    stream = sys.stdout if code == 0 else sys.stderr
    print(message, file=stream, flush=True)
    os._exit(code)


def count_jobs(mapping):
    return sum(len(items or []) for items in (mapping or {}).values())


try:
    inspector = celery_app.control.inspect(timeout=5)
    ping = inspector.ping() or {}
    if not ping:
        finish(1, "ERROR|application_celery_workers=no_response")

    active = count_jobs(inspector.active())
    reserved = count_jobs(inspector.reserved())
    scheduled = count_jobs(inspector.scheduled())
    if active or reserved or scheduled:
        finish(
            1,
            "ERROR|application_celery_not_idle"
            f"|active={active}|reserved={reserved}|scheduled={scheduled}",
        )

    broker_url = os.environ["CELERY_BROKER_URL"]
    client = redis.Redis.from_url(
        broker_url,
        socket_connect_timeout=5,
        socket_timeout=5,
        decode_responses=False,
    )
    if not client.ping():
        finish(1, "ERROR|application_broker_ping_failed")

    queue_name = celery_app.conf.task_default_queue or "celery"
    queued = client.llen(queue_name)
    unacked = client.hlen("unacked")
    unacked_index = client.zcard("unacked_index")
    if queued or unacked or unacked_index:
        finish(
            1,
            "ERROR|application_broker_queue_not_empty"
            f"|queue={queue_name}|queued={queued}|unacked={unacked}"
            f"|unacked_index={unacked_index}",
        )

    db_host = urlsplit(os.environ["DATABASE_URL"]).hostname or "unknown"
    broker_host = urlsplit(broker_url).hostname or "unknown"
    print(
        "PASS|application_celery_idle"
        f"|workers={len(ping)}|active={active}|reserved={reserved}"
        f"|scheduled={scheduled}|queue={queue_name}|queued={queued}"
        f"|unacked={unacked}|unacked_index={unacked_index}",
        flush=True,
    )
    print("WORKER_NODES|" + ",".join(sorted(ping)), flush=True)
    finish(
        0,
        f"TOPOLOGY|database_host={db_host}|broker_host={broker_host}",
    )
except BaseException as error:
    finish(1, f"ERROR|application_celery_probe|error_type={type(error).__name__}")
PY

if ! docker exec ai-middle-office-app-api-1 \
  python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/health/ready', timeout=10).read()"; then
  echo "ERROR|application_ready_endpoint_failed" >&2
  exit 1
fi

nginx_state="$(systemctl is-active nginx 2>/dev/null || true)"
[[ "${nginx_state}" == active ]] || {
  echo "ERROR|public_nginx_not_active|state=${nginx_state}" >&2
  exit 1
}

root_available_kib="$(df -Pk / | awk 'NR==2 {print $4}')"
data_available_kib="$(df -Pk /data | awk 'NR==2 {print $4}')"
if (( root_available_kib < 20 * 1024 * 1024 )); then
  echo "ERROR|insufficient_root_disk|available_kib=${root_available_kib}" >&2
  exit 1
fi
if (( data_available_kib < 25 * 1024 * 1024 )); then
  echo "ERROR|insufficient_data_disk|available_kib=${data_available_kib}" >&2
  exit 1
fi

echo "PASS|nginx|state=${nginx_state}"
echo "PASS|disk|root_available_kib=${root_available_kib}|data_available_kib=${data_available_kib}"
echo "PASS|dify_worker_beat=stopped"
echo "RESULT|target_final_precutover_gate=passed"
echo "INFO|no_state_changed"
echo "INFO|next_gate=application_freeze"
