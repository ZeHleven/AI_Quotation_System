#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DIFY_COMPOSE="${SCRIPT_DIR}/dify-compose.sh"

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}
[[ -x "${DIFY_COMPOSE}" ]] || {
  echo "ERROR|missing_dify_compose_wrapper|${DIFY_COMPOSE}" >&2
  exit 1
}

for dependency in dify-api-1 dify-redis-1 dify-db_postgres-1 dify-plugin_daemon-1; do
  if ! docker inspect "${dependency}" >/dev/null 2>&1 || \
    [[ "$(docker inspect "${dependency}" --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|required_dependency_not_running|${dependency}" >&2
    exit 1
  fi
done

if docker inspect dify-worker_beat-1 >/dev/null 2>&1 && \
  [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|dify_worker_beat_must_remain_stopped" >&2
  exit 1
fi

"${DIFY_COMPOSE}" up -d --no-deps worker

worker_ready=false
for _ in $(seq 1 3); do
  if docker inspect dify-worker-1 >/dev/null 2>&1 && \
    [[ "$(docker inspect dify-worker-1 --format '{{.State.Running}}')" == true ]]; then
    ping_output="$(
      timeout 20 docker exec dify-worker-1 \
        /app/api/.venv/bin/celery -A celery_entrypoint.celery \
        inspect ping --timeout=5 2>&1 || true
    )"
    if grep -q 'pong' <<<"${ping_output}"; then
      worker_ready=true
      break
    fi
  fi
  sleep 3
done

if ! docker inspect dify-worker-1 >/dev/null 2>&1 || \
  [[ "$(docker inspect dify-worker-1 --format '{{.State.Running}}')" != true ]]; then
  echo "ERROR|dify_worker_not_running" >&2
  docker logs --tail 100 dify-worker-1 >&2 || true
  exit 1
fi

if [[ "${worker_ready}" != true ]]; then
  echo "ERROR|dify_worker_ping_timeout" >&2
  docker logs --tail 100 dify-worker-1 >&2 || true
  exit 1
fi

timeout 60 docker exec -i dify-worker-1 /app/api/.venv/bin/python - <<'PY'
import os
import sys

from celery_entrypoint import celery


def count_jobs(mapping):
    if not mapping:
        return 0
    return sum(len(items or []) for items in mapping.values())


def finish(code, message):
    stream = sys.stdout if code == 0 else sys.stderr
    print(message, file=stream, flush=True)
    os._exit(code)


try:
    inspector = celery.control.inspect(timeout=5)
    ping = inspector.ping() or {}
    if not ping:
        finish(1, "ERROR|target_dify_worker_ping=no_response")

    active = count_jobs(inspector.active())
    reserved = count_jobs(inspector.reserved())
    scheduled = count_jobs(inspector.scheduled())
    if active or reserved or scheduled:
        finish(
            1,
            "ERROR|target_dify_worker_not_idle"
            f"|active={active}|reserved={reserved}|scheduled={scheduled}",
        )

    print(f"PASS|target_dify_worker_ping|worker_count={len(ping)}", flush=True)
    finish(
        0,
        "PASS|target_dify_worker_idle"
        f"|active={active}|reserved={reserved}|scheduled={scheduled}",
    )
except BaseException as error:
    finish(1, f"ERROR|target_dify_worker_probe|error_type={type(error).__name__}")
PY

if [[ -n "$(docker port dify-worker-1)" ]]; then
  echo "ERROR|dify_worker_has_published_host_port" >&2
  exit 1
fi

restart_count="$(docker inspect dify-worker-1 --format '{{.RestartCount}}')"
if [[ "${restart_count}" != 0 ]]; then
  echo "ERROR|dify_worker_restarted|count=${restart_count}" >&2
  docker logs --tail 100 dify-worker-1 >&2 || true
  exit 1
fi

if docker inspect dify-worker_beat-1 >/dev/null 2>&1 && \
  [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|dify_worker_beat_started_unexpectedly" >&2
  exit 1
fi

docker inspect dify-worker-1 \
  --format 'PASS|container_ready|dify-worker-1|status={{.State.Status}}|restart_count={{.RestartCount}}'
docker stats --no-stream dify-worker-1
echo "RESULT|target_dify_worker_dark_start=passed"
echo "INFO|dify_worker_beat_still_stopped"
echo "INFO|no_pending_tasks"
