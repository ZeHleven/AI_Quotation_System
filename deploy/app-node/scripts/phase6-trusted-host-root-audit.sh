#!/usr/bin/env bash
set -euo pipefail

PROJECT="ai-middle-office-app"
ENV_FILE="/etc/ai-middle-office/app.env"
REPORT="/home/aiadmin/ai-phase6-trusted-host-root-audit-$(date -u +%Y%m%d_%H%M%S).txt"

finish() {
    local rc=$?
    trap - EXIT
    chmod 0644 "${REPORT}" 2>/dev/null || true
    chown aiadmin:aiadmin "${REPORT}" 2>/dev/null || true
    printf 'REPORT=%s\n' "${REPORT}"
    exit "${rc}"
}

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

api_id="$(docker ps -q \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter label=com.docker.compose.service=api | head -n 1)"
worker_id="$(docker ps -q \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter label=com.docker.compose.service=worker | head -n 1)"

if [[ -z "${api_id}" || -z "${worker_id}" ]]; then
    echo "ERROR|expected_running_containers_missing" >&2
    exit 1
fi

if [[ ! -r "${ENV_FILE}" ]]; then
    echo "ERROR|runtime_env_not_readable" >&2
    exit 1
fi

echo "=== SAFE CONTAINER METADATA ==="
docker inspect --format 'api|name={{.Name}}|image={{.Config.Image}}|state={{.State.Status}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}|started={{.State.StartedAt}}|working_dir={{index .Config.Labels "com.docker.compose.project.working_dir"}}|config_files={{index .Config.Labels "com.docker.compose.project.config_files"}}' "${api_id}"
docker inspect --format 'worker|name={{.Name}}|image={{.Config.Image}}|state={{.State.Status}}|started={{.State.StartedAt}}|working_dir={{index .Config.Labels "com.docker.compose.project.working_dir"}}|config_files={{index .Config.Labels "com.docker.compose.project.config_files"}}' "${worker_id}"

echo "=== TRUSTED HOSTS BOOLEAN AUDIT ==="
ENV_FILE="${ENV_FILE}" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
lines = path.read_text(encoding="utf-8").splitlines()
matches = [line for line in lines if line.startswith("TRUSTED_HOSTS=")]
hosts = []
if len(matches) == 1:
    hosts = [item.strip() for item in matches[0].split("=", 1)[1].split(",") if item.strip()]
print(f"env_line_count={len(matches)}")
print(f"env_host_count={len(hosts)}")
print(f"env_domain={str('www.qskingship.com' in hosts).lower()}")
print(f"env_localhost={str('localhost' in hosts).lower()}")
print(f"env_loopback={str('127.0.0.1' in hosts).lower()}")
print(f"env_wildcard={str('*' in hosts).lower()}")
PY

docker exec "${api_id}" python -c '
import os

hosts = [item.strip() for item in os.getenv("TRUSTED_HOSTS", "").split(",") if item.strip()]
print(f"runtime_host_count={len(hosts)}")
print("runtime_domain=" + str("www.qskingship.com" in hosts).lower())
print("runtime_localhost=" + str("localhost" in hosts).lower())
print("runtime_loopback=" + str("127.0.0.1" in hosts).lower())
print("runtime_wildcard=" + str("*" in hosts).lower())
'

echo "=== RESTART SAFETY GATE ==="
docker exec "${api_id}" python -c '
from sqlalchemy import text
from app.core.database import engine

with engine.connect() as connection:
    rows = connection.execute(
        text("SELECT status, COUNT(*) FROM quote_jobs WHERE status IN (:queued, :running) GROUP BY status"),
        {"queued": "queued", "running": "running"},
    ).all()
counts = {str(status): int(count) for status, count in rows}
print("queued=" + str(counts.get("queued", 0)))
print("running=" + str(counts.get("running", 0)))
print(f"active_total={sum(counts.values())}")
'

if docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'; then
    echo "public_access_enabled=false"
else
    echo "public_access_enabled=unexpected"
fi

health_json="$(curl -fsS --noproxy '*' --max-time 15 http://127.0.0.1:9000/health/ready)"
printf '%s' "${health_json}" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
queue = data.get("task_queue", {})
print("health_status=" + str(data.get("status")))
print("worker_count=" + str(int(queue.get("worker_count", 0))))
'

printf 'domain_host_http_code='
curl -sS --noproxy '*' -o /dev/null -w '%{http_code}\n' --max-time 10 \
    -H 'Host: www.qskingship.com' http://127.0.0.1:9000/
printf 'nginx_active='
systemctl is-active nginx 2>/dev/null || true
printf 'listener_443_count='
ss -lntH 'sport = :443' 2>/dev/null | wc -l

echo "PASS|trusted_host_root_audit"
