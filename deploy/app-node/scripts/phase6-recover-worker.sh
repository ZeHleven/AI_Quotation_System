#!/usr/bin/env bash
set -Eeuo pipefail

readonly BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
readonly STAMP="$(date -u +%Y%m%d_%H%M%S)"
readonly BACKUP_DIR="${BACKUP_ROOT}/pre-worker-recovery-${STAMP}"
readonly REPORT="/home/aiadmin/ai-phase6-worker-recovery-${STAMP}.txt"

finish() {
    local rc=$?
    chmod 0644 "${REPORT}" 2>/dev/null || true
    chown aiadmin:aiadmin "${REPORT}" 2>/dev/null || true
    printf '\nREPORT=%s\n' "${REPORT}"
    printf 'BACKUP_DIR=%s\n' "${BACKUP_DIR}"
    if [[ ${rc} -eq 0 ]]; then
        printf 'RESULT=PASS\n'
    else
        printf 'RESULT=FAIL\n'
    fi
    return "${rc}"
}

if [[ ${EUID} -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

api_id="$(docker ps -q \
    --filter label=com.docker.compose.project=ai-middle-office-app \
    --filter label=com.docker.compose.service=api | head -n 1)"
worker_id="$(docker ps -q \
    --filter label=com.docker.compose.project=ai-middle-office-app \
    --filter label=com.docker.compose.service=worker | head -n 1)"

if [[ -z "${api_id}" || -z "${worker_id}" ]]; then
    echo "FAIL|expected_running_containers_missing"
    exit 1
fi

echo "=== PRE-RECOVERY SAFETY GATE ==="
printf 'timestamp=%s\n' "$(date --iso-8601=seconds)"
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"

active_job_output="$(docker exec "${api_id}" python -c 'from sqlalchemy import text; from app.core.database import engine; connection=engine.connect(); rows=connection.execute(text("SELECT status, COUNT(*) AS item_count FROM quote_jobs WHERE status IN (\"queued\", \"running\") GROUP BY status")).all(); counts={str(row[0]): int(row[1]) for row in rows}; print("queued=" + str(counts.get("queued", 0))); print("running=" + str(counts.get("running", 0))); print("active_total=" + str(sum(counts.values()))); connection.close()')"
printf '%s\n' "${active_job_output}"
active_total="$(printf '%s\n' "${active_job_output}" | awk -F= '/^active_total=/{print $2}')"
if [[ "${active_total}" != "0" ]]; then
    echo "FAIL|active_quote_jobs_present_no_restart"
    exit 1
fi
echo "PASS|no_active_quote_jobs"

if docker exec "${worker_id}" python -c 'import os, redis, sys; u=os.environ.get("CELERY_BROKER_URL", ""); sys.exit(0 if u and redis.Redis.from_url(u, socket_connect_timeout=5, socket_timeout=5).ping() else 1)'; then
    echo "PASS|worker_redis_ping_before_restart"
else
    echo "FAIL|worker_redis_ping_before_restart"
    exit 1
fi

if ! docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'; then
    echo "FAIL|public_access_enabled_false_before_restart"
    exit 1
fi
echo "PASS|public_access_enabled_false_before_restart"

echo "=== EVIDENCE BACKUP ==="
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
{
    docker inspect --format 'api|name={{.Name}}|id={{.Id}}|user={{.Config.User}}|state={{.State.Status}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}|pid={{.State.Pid}}|oom={{.State.OOMKilled}}|restart_count={{.RestartCount}}|started={{.State.StartedAt}}|error={{.State.Error}}' "${api_id}"
    docker inspect --format 'worker|name={{.Name}}|id={{.Id}}|hostname={{.Config.Hostname}}|user={{.Config.User}}|state={{.State.Status}}|pid={{.State.Pid}}|oom={{.State.OOMKilled}}|restart_count={{.RestartCount}}|started={{.State.StartedAt}}|error={{.State.Error}}' "${worker_id}"
    docker top "${worker_id}" -eo pid,ppid,user,stat,lstart,etime,pcpu,pmem,args
    printf '%s\n' "${active_job_output}"
    systemctl is-active firewalld docker ipsec ai-middle-office-private-forward-firewall.service nginx || true
    iptables -w 5 -t raw -S PREROUTING | grep -- '--comment ai-hybrid-' || true
    iptables -w 5 -S DOCKER-USER | grep -- '--comment ai-hybrid-' || true
} >"${BACKUP_DIR}/safe-state.before.txt"

latest_diagnostic="$(find /home/aiadmin -maxdepth 1 -type f -name 'ai-phase6-worker-readonly-diagnostic-*.txt' -printf '%T@ %p\n' | sort -n | tail -n 1 | cut -d' ' -f2-)"
if [[ -n "${latest_diagnostic}" && -f "${latest_diagnostic}" ]]; then
    cp -a "${latest_diagnostic}" "${BACKUP_DIR}/"
fi
sha256sum "${BACKUP_DIR}"/* >"${BACKUP_DIR}/SHA256SUMS"
echo "PASS|pre_restart_evidence_backed_up"

echo "=== WORKER-ONLY RESTART ==="
worker_hostname="$(docker inspect --format '{{.Config.Hostname}}' "${worker_id}")"
celery_node="quote-worker@${worker_hostname}"
api_started_before="$(docker inspect --format '{{.State.StartedAt}}' "${api_id}")"
worker_started_before="$(docker inspect --format '{{.State.StartedAt}}' "${worker_id}")"

docker restart --time 60 "${worker_id}" >/dev/null
echo "PASS|worker_container_restart_requested"

celery_output=""
celery_ready=0
for attempt in $(seq 1 18)
do
    if [[ "$(docker inspect --format '{{.State.Running}}' "${worker_id}" 2>/dev/null || true)" == "true" ]]; then
        celery_output="$(docker exec "${api_id}" python -m celery \
            -A app.tasks.celery_app.celery_app inspect ping \
            --destination "${celery_node}" --timeout=5 2>&1 || true)"
        if printf '%s\n' "${celery_output}" | grep -Fq 'pong'; then
            celery_ready=1
            echo "celery_ready_attempt=${attempt}"
            break
        fi
    fi
    sleep 5
done
printf '%s\n' "${celery_output}"
if [[ ${celery_ready} -ne 1 ]]; then
    echo "FAIL|celery_worker_did_not_recover"
    exit 1
fi
echo "PASS|celery_worker_ping_after_restart"

echo "=== POST-RECOVERY GATE ==="
api_started_after="$(docker inspect --format '{{.State.StartedAt}}' "${api_id}")"
worker_started_after="$(docker inspect --format '{{.State.StartedAt}}' "${worker_id}")"
printf 'api_started_before=%s\napi_started_after=%s\n' "${api_started_before}" "${api_started_after}"
printf 'worker_started_before=%s\nworker_started_after=%s\n' "${worker_started_before}" "${worker_started_after}"
if [[ "${api_started_before}" == "${api_started_after}" ]]; then
    echo "PASS|api_not_restarted"
else
    echo "FAIL|api_not_restarted"
    exit 1
fi
if [[ "${worker_started_before}" != "${worker_started_after}" ]]; then
    echo "PASS|worker_restart_confirmed"
else
    echo "FAIL|worker_restart_confirmed"
    exit 1
fi

if docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'; then
    echo "PASS|public_access_enabled_false_after_restart"
else
    echo "FAIL|public_access_enabled_false_after_restart"
    exit 1
fi

raw_owned="$(iptables -w 5 -t raw -S PREROUTING | grep -c -- '--comment ai-hybrid-' || true)"
docker_owned="$(iptables -w 5 -S DOCKER-USER | grep -c -- '--comment ai-hybrid-' || true)"
printf 'raw_owned_rule_count=%s\ndocker_user_owned_rule_count=%s\n' "${raw_owned}" "${docker_owned}"
if [[ "${raw_owned}" -ne 3 || "${docker_owned}" -ne 2 ]]; then
    echo "FAIL|private_forward_rule_counts_changed"
    exit 1
fi
echo "PASS|private_forward_rule_counts_unchanged"

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]]; then
    echo "PASS|nginx_still_inactive"
else
    echo "FAIL|nginx_still_inactive"
    exit 1
fi

health_json="$(curl -fsS --max-time 10 http://127.0.0.1:9000/health/ready)"
printf '%s' "${health_json}" | python3 -c 'import json, sys; d=json.load(sys.stdin); q=d.get("task_queue", {}); print("health_status=" + str(d.get("status"))); print("database=" + str(d.get("database"))); print("external_dependencies=" + str(d.get("external_dependencies", {}).get("overall_status"))); print("broker=" + str(q.get("broker"))); print("worker=" + str(q.get("worker"))); print("worker_count=" + str(q.get("worker_count")))'
health_status="$(printf '%s' "${health_json}" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("status", ""))')"
worker_count="$(printf '%s' "${health_json}" | python3 -c 'import json, sys; print(int(json.load(sys.stdin).get("task_queue", {}).get("worker_count", 0)))')"
if [[ "${health_status}" != "ready" || "${worker_count}" -lt 1 ]]; then
    echo "FAIL|application_ready_after_worker_restart"
    exit 1
fi
echo "PASS|application_ready_after_worker_restart"
echo "PASS|phase6_worker_recovery"
