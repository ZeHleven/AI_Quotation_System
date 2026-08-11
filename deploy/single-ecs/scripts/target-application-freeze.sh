#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_ROOT="/opt/ai-middle-office/app-node"
readonly APP_COMPOSE="${APP_ROOT}/compose.yaml"
readonly APP_ENV="/etc/ai-middle-office/app.env"
readonly MYSQL_CA="/etc/ai-middle-office/mysql-ca.pem"
readonly APP_NETWORK="ai-middle-office-app-net"

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}
for required in "${APP_COMPOSE}" "${APP_ENV}" "${MYSQL_CA}"; do
  [[ -f "${required}" ]] || {
    echo "ERROR|missing_required_file|${required}" >&2
    exit 1
  }
done

for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  if ! docker inspect "${container}" >/dev/null 2>&1 || \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|application_not_running_before_freeze|${container}" >&2
    exit 1
  fi
done

app_image="$(docker inspect ai-middle-office-app-api-1 --format '{{.Config.Image}}')"
compose=(docker compose --project-directory "${APP_ROOT}" -f "${APP_COMPOSE}" --profile worker)
freeze_started=false

rollback_freeze() {
  rc=$?
  trap - ERR
  set +e
  if [[ "${freeze_started}" == true ]]; then
    echo "ROLLBACK|application_freeze|begin|exit=${rc}" >&2
    "${compose[@]}" up -d api worker >/dev/null 2>&1 || true
    systemctl start nginx >/dev/null 2>&1 || true
    echo "ROLLBACK|application_freeze|application_and_nginx_restart_attempted" >&2
  fi
  exit "${rc}"
}
trap rollback_freeze ERR

probe_idle() {
  local container="$1"
  timeout 45 docker exec -i "${container}" python - <<'PY'
import os
import sys

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
    active = count_jobs(inspector.active())
    reserved = count_jobs(inspector.reserved())
    scheduled = count_jobs(inspector.scheduled())
    client = redis.Redis.from_url(
        os.environ["CELERY_BROKER_URL"],
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    queue_name = celery_app.conf.task_default_queue or "celery"
    queued = client.llen(queue_name)
    unacked = client.hlen("unacked")
    unacked_index = client.zcard("unacked_index")
    if active or reserved or scheduled or queued or unacked or unacked_index:
        finish(
            1,
            "ERROR|application_not_idle"
            f"|workers={len(ping)}|active={active}|reserved={reserved}"
            f"|scheduled={scheduled}|queue={queue_name}|queued={queued}"
            f"|unacked={unacked}|unacked_index={unacked_index}",
        )
    finish(
        0,
        "PASS|application_idle"
        f"|workers={len(ping)}|active={active}|reserved={reserved}"
        f"|scheduled={scheduled}|queue={queue_name}|queued={queued}"
        f"|unacked={unacked}|unacked_index={unacked_index}",
    )
except BaseException as error:
    finish(1, f"ERROR|application_idle_probe|error_type={type(error).__name__}")
PY
}

probe_idle ai-middle-office-app-api-1

systemctl stop nginx
freeze_started=true
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == inactive ]] || {
  echo "ERROR|nginx_failed_to_stop" >&2
  false
}
echo "PASS|public_ingress=frozen"

# Recheck after ingress is closed so no request can race with the freeze.
probe_idle ai-middle-office-app-api-1

"${compose[@]}" stop worker
if [[ "$(docker inspect ai-middle-office-app-worker-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|ecs_application_worker_failed_to_stop" >&2
  false
fi

# Shut down any remaining legacy worker (for example the old Windows worker)
# through Celery control while the source broker is still reachable.
timeout 45 docker run --rm -i \
  --network "${APP_NETWORK}" \
  --env-file "${APP_ENV}" \
  -v "${MYSQL_CA}:/run/secrets/mysql-ca.pem:ro" \
  --entrypoint python \
  "${app_image}" - <<'PY'
import os
import sys
import time

from app.tasks.celery_app import celery_app


def finish(code, message):
    stream = sys.stdout if code == 0 else sys.stderr
    print(message, file=stream, flush=True)
    os._exit(code)


try:
    inspector = celery_app.control.inspect(timeout=5)
    ping = inspector.ping() or {}
    nodes = sorted(ping)
    if nodes:
        celery_app.control.broadcast("shutdown", destination=nodes, reply=False)
        print("INFO|legacy_worker_shutdown_requested|count=" + str(len(nodes)), flush=True)
    for _ in range(6):
        time.sleep(2)
        if not (celery_app.control.inspect(timeout=3).ping() or {}):
            finish(0, "PASS|all_application_workers=stopped")
    finish(1, "ERROR|application_worker_remains_online_after_shutdown")
except BaseException as error:
    finish(1, f"ERROR|worker_shutdown_probe|error_type={type(error).__name__}")
PY

"${compose[@]}" stop api
for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  if [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" == true ]]; then
    echo "ERROR|application_container_still_running|${container}" >&2
    false
  fi
done

freeze_started=false
trap - ERR
echo "RESULT|target_application_freeze=passed"
echo "INFO|public_nginx_stopped"
echo "INFO|application_api_and_workers_stopped"
echo "INFO|source_dependencies_not_touched"
echo "INFO|next_gate=source_final_cold_export"
