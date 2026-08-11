#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPORT="/home/aiadmin/ai-phase6-worker-readonly-diagnostic-$(date -u +%Y%m%d_%H%M%S).txt"

finish() {
    local rc=$?
    chmod 0644 "${REPORT}" 2>/dev/null || true
    chown aiadmin:aiadmin "${REPORT}" 2>/dev/null || true
    printf '\nREPORT=%s\n' "${REPORT}"
    return "${rc}"
}

if [[ ${EUID} -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

echo "=== TIMESTAMP ==="
date --iso-8601=seconds

api_id="$(docker ps -q \
    --filter label=com.docker.compose.project=ai-middle-office-app \
    --filter label=com.docker.compose.service=api | head -n 1)"
worker_id="$(docker ps -q \
    --filter label=com.docker.compose.project=ai-middle-office-app \
    --filter label=com.docker.compose.service=worker | head -n 1)"

if [[ -z "${api_id}" || -z "${worker_id}" ]]; then
    echo "FAIL|expected_running_containers_missing"
    docker ps -a --filter label=com.docker.compose.project=ai-middle-office-app \
        --format '{{.Names}}|{{.Status}}|{{.Ports}}'
    exit 1
fi

echo "=== SAFE CONTAINER STATE ==="
docker inspect --format 'api|name={{.Name}}|id={{.Id}}|user={{.Config.User}}|state={{.State.Status}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}|pid={{.State.Pid}}|oom={{.State.OOMKilled}}|restart_count={{.RestartCount}}|started={{.State.StartedAt}}|error={{.State.Error}}' "${api_id}"
docker inspect --format 'worker|name={{.Name}}|id={{.Id}}|hostname={{.Config.Hostname}}|user={{.Config.User}}|state={{.State.Status}}|pid={{.State.Pid}}|oom={{.State.OOMKilled}}|restart_count={{.RestartCount}}|started={{.State.StartedAt}}|error={{.State.Error}}' "${worker_id}"
docker inspect --format '{{range $name, $network := .NetworkSettings.Networks}}api_network={{$name}}|ip={{$network.IPAddress}}{{println}}{{end}}' "${api_id}"
docker inspect --format '{{range $name, $network := .NetworkSettings.Networks}}worker_network={{$name}}|ip={{$network.IPAddress}}{{println}}{{end}}' "${worker_id}"

echo "=== WORKER PROCESS ==="
docker top "${worker_id}" -eo pid,ppid,user,stat,lstart,etime,pcpu,pmem,args
docker exec "${worker_id}" sh -lc "printf 'pid1_cmd='; tr '\000' ' ' </proc/1/cmdline; echo; grep -E '^(Name|State|Pid|PPid|Threads|VmRSS|VmSize):' /proc/1/status"

echo "=== CONTAINER RESOURCE SNAPSHOT ==="
docker stats --no-stream --format '{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|mem_percent={{.MemPerc}}|pids={{.PIDs}}' "${api_id}" "${worker_id}"

echo "=== BROKER CONNECTIVITY FROM WORKER ==="
if docker exec "${worker_id}" python -c 'import os, redis, sys; u=os.environ.get("CELERY_BROKER_URL", ""); sys.exit(0 if u and redis.Redis.from_url(u, socket_connect_timeout=5, socket_timeout=5).ping() else 1)'; then
    echo "PASS|worker_redis_ping"
else
    echo "FAIL|worker_redis_ping"
fi

echo "=== TARGETED CELERY PING ==="
worker_hostname="$(docker inspect --format '{{.Config.Hostname}}' "${worker_id}")"
celery_node="quote-worker@${worker_hostname}"
echo "destination=${celery_node}"
set +e
docker exec "${api_id}" python -m celery \
    -A app.tasks.celery_app.celery_app inspect ping \
    --destination "${celery_node}" --timeout=10
api_ping_rc=$?
docker exec "${worker_id}" python -m celery \
    -A app.tasks.celery_app.celery_app inspect ping \
    --destination "${celery_node}" --timeout=10
worker_ping_rc=$?
set -e
echo "api_targeted_ping_rc=${api_ping_rc}"
echo "worker_targeted_ping_rc=${worker_ping_rc}"

echo "=== FILTERED WORKER LIFECYCLE AND ERRORS ==="
docker logs --since 2h --tail 1000 "${worker_id}" 2>&1 \
    | grep -E '(^|[[:space:]])(ERROR|CRITICAL|Traceback|OperationalError|ConnectionError|WorkerLostError|consumer:|Connected to|mingle:|ready\.)' \
    | sed -E 's#(redis://)[^/@[:space:]]+@#\1[redacted]@#g' \
    | tail -n 200 || true

echo "=== HOST OOM EVIDENCE ==="
journalctl -k --since '2 hours ago' --no-pager 2>/dev/null \
    | grep -Ei 'out of memory|oom-kill|killed process' \
    | tail -n 50 || true

echo "=== CURRENT HEALTH SUMMARY ==="
health_json="$(curl -fsS --max-time 10 http://127.0.0.1:9000/health/ready || true)"
if [[ -n "${health_json}" ]]; then
    printf '%s' "${health_json}" | python3 -c 'import json, sys; d=json.load(sys.stdin); q=d.get("task_queue", {}); print("health_status=" + str(d.get("status"))); print("database=" + str(d.get("database"))); print("external_dependencies=" + str(d.get("external_dependencies", {}).get("overall_status"))); print("broker=" + str(q.get("broker"))); print("worker=" + str(q.get("worker"))); print("worker_count=" + str(q.get("worker_count")))'
else
    echo "FAIL|health_endpoint_unavailable"
fi

echo "PASS|phase6_worker_readonly_diagnostic_completed"
