#!/usr/bin/env bash
set -Eeuo pipefail

readonly DOMAIN="www.qskingship.com"
readonly PROJECT="ai-middle-office-app"
readonly COMPOSE_DIR="/opt/ai-middle-office/app-node"
readonly COMPOSE_FILE="${COMPOSE_DIR}/compose.yaml"
readonly ENV_FILE="/etc/ai-middle-office/app.env"
readonly ORIGINAL_BACKUP_DIR="/home/aiadmin/ai-phase6-backups/pre-trusted-host-repair-20260806_015732"
readonly ORIGINAL_ENV="${ORIGINAL_BACKUP_DIR}/app.env"
readonly FAILED_REPORT="/home/aiadmin/ai-phase6-trusted-host-repair-20260806_015732.txt"
readonly BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
readonly STAMP="$(date -u +%Y%m%d_%H%M%S)"
readonly RECOVERY_DIR="${BACKUP_ROOT}/pre-trusted-host-partial-recovery-${STAMP}"
readonly REPORT="/home/aiadmin/ai-phase6-trusted-host-partial-recovery-${STAMP}.txt"

finish() {
    local rc=$?
    trap - EXIT
    chmod 0644 "${REPORT}" 2>/dev/null || true
    chown aiadmin:aiadmin "${REPORT}" 2>/dev/null || true
    printf 'REPORT=%s\n' "${REPORT}"
    if [[ ${rc} -eq 0 ]]; then
        echo "RESULT=PASS"
    else
        echo "RESULT=FAIL"
    fi
    exit "${rc}"
}

compose_recreate() {
    docker compose \
        --project-name "${PROJECT}" \
        --file "${COMPOSE_FILE}" \
        --profile worker \
        up -d \
        --no-deps \
        --force-recreate \
        --no-build \
        --pull never \
        api worker
}

wait_for_application() {
    local attempt
    local health_json
    local ready
    for attempt in $(seq 1 36); do
        health_json="$(
            curl -fsS --noproxy '*' --max-time 15 \
                http://127.0.0.1:9000/health/ready 2>/dev/null || true
        )"
        ready="$(
            printf '%s' "${health_json}" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    print("false")
    raise SystemExit
queue = data.get("task_queue", {})
print(str(data.get("status") == "ready" and int(queue.get("worker_count", 0)) >= 1).lower())
' 2>/dev/null || true
        )"
        if [[ "${ready}" == "true" ]]; then
            echo "application_ready_attempt=${attempt}"
            return 0
        fi
        sleep 5
    done
    return 1
}

restore_original_and_exit() {
    local reason="$1"
    set +e
    echo "ERROR|${reason}" >&2
    systemctl stop nginx >/dev/null 2>&1 || true
    cp -a "${ORIGINAL_ENV}" "${ENV_FILE}"
    compose_recreate
    local compose_rc=$?
    wait_for_application
    local health_rc=$?
    if [[ ${compose_rc} -eq 0 && ${health_rc} -eq 0 ]]; then
        echo "ROLLBACK|original_015732_env_restored_and_containers_recreated" >&2
    else
        echo "ROLLBACK_ERROR|manual_recovery_required|${ORIGINAL_BACKUP_DIR}" >&2
    fi
    exit 1
}

validate_host_file() {
    local target_file="$1"
    local expect_domain="$2"
    TARGET_FILE="${target_file}" EXPECT_DOMAIN="${expect_domain}" DOMAIN_VALUE="${DOMAIN}" python3 -c '
import os
from pathlib import Path

lines = Path(os.environ["TARGET_FILE"]).read_text(encoding="utf-8").splitlines()
matches = [line for line in lines if line.startswith("TRUSTED_HOSTS=")]
if len(matches) != 1:
    raise SystemExit(1)
hosts = [item.strip() for item in matches[0].split("=", 1)[1].split(",") if item.strip()]
has_domain = os.environ["DOMAIN_VALUE"] in hosts
valid = (
    has_domain == (os.environ["EXPECT_DOMAIN"] == "true")
    and "localhost" in hosts
    and "127.0.0.1" in hosts
    and "*" not in hosts
    and len(hosts) == len(set(hosts))
)
raise SystemExit(0 if valid else 1)
'
}

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

for required_file in \
    "${COMPOSE_FILE}" \
    "${ENV_FILE}" \
    "${ORIGINAL_ENV}" \
    "${FAILED_REPORT}"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_recovery_file_not_readable|${required_file}" >&2
        exit 1
    fi
done

if ! grep -Fq 'line 222: ENV_FILE: readonly variable' "${FAILED_REPORT}" \
    || ! grep -Fq 'RESULT=FAIL' "${FAILED_REPORT}"; then
    echo "ERROR|failed_report_does_not_match_expected_partial_state" >&2
    exit 1
fi

if ! validate_host_file "${ORIGINAL_ENV}" false; then
    echo "ERROR|original_backup_validation_failed" >&2
    exit 1
fi
if ! validate_host_file "${ENV_FILE}" true; then
    echo "ERROR|current_updated_env_validation_failed" >&2
    exit 1
fi

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

if docker exec "${api_id}" python -c '
import os
hosts = [item.strip() for item in os.getenv("TRUSTED_HOSTS", "").split(",") if item.strip()]
raise SystemExit(0 if "www.qskingship.com" in hosts else 1)
'; then
    echo "ERROR|runtime_already_contains_domain_unexpectedly" >&2
    exit 1
fi

active_job_output="$(docker exec "${api_id}" python -c '
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
print("active_total=" + str(sum(counts.values())))
')"
printf '%s\n' "${active_job_output}"
active_total="$(printf '%s\n' "${active_job_output}" | awk -F= '/^active_total=/{print $2}')"
if [[ "${active_total}" != "0" ]]; then
    echo "ERROR|active_quote_jobs_present_no_recreate" >&2
    exit 1
fi

if ! docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'; then
    echo "ERROR|public_access_boundary_not_confirmed" >&2
    exit 1
fi
if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]]; then
    echo "ERROR|nginx_must_remain_inactive" >&2
    exit 1
fi
if ss -lntH 'sport = :443' 2>/dev/null | grep -q .; then
    echo "ERROR|port_443_must_not_be_listening" >&2
    exit 1
fi

install -d -o root -g root -m 0700 "${RECOVERY_DIR}"
cp -a "${ENV_FILE}" "${RECOVERY_DIR}/updated-app.env"
{
    echo "timestamp_utc=${STAMP}"
    echo "failed_report=${FAILED_REPORT}"
    echo "original_backup_dir=${ORIGINAL_BACKUP_DIR}"
    printf '%s\n' "${active_job_output}"
    echo "public_access_enabled=false"
    echo "nginx_state=inactive"
    echo "listener_443_count=0"
} > "${RECOVERY_DIR}/safe-state.before.txt"
chmod 0600 "${RECOVERY_DIR}/safe-state.before.txt"
sha256sum "${RECOVERY_DIR}/updated-app.env" \
    "${RECOVERY_DIR}/safe-state.before.txt" > "${RECOVERY_DIR}/SHA256SUMS"
chmod 0600 "${RECOVERY_DIR}/SHA256SUMS"
echo "PASS|expected_partial_state_confirmed_and_backed_up"

if ! compose_recreate; then
    restore_original_and_exit "api_worker_recreate_failed"
fi
echo "PASS|api_worker_recreated_from_updated_env"

if ! wait_for_application; then
    restore_original_and_exit "application_readiness_failed_after_recreate"
fi

api_id="$(docker ps -q \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter label=com.docker.compose.service=api | head -n 1)"
worker_id="$(docker ps -q \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter label=com.docker.compose.service=worker | head -n 1)"
if [[ -z "${api_id}" || -z "${worker_id}" ]]; then
    restore_original_and_exit "containers_missing_after_recreate"
fi

if ! docker exec "${api_id}" python -c '
import os
hosts = [item.strip() for item in os.getenv("TRUSTED_HOSTS", "").split(",") if item.strip()]
valid = (
    "www.qskingship.com" in hosts
    and "localhost" in hosts
    and "127.0.0.1" in hosts
    and "*" not in hosts
    and len(hosts) == len(set(hosts))
)
raise SystemExit(0 if valid else 1)
'; then
    restore_original_and_exit "runtime_trusted_hosts_validation_failed"
fi

if ! docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'; then
    restore_original_and_exit "public_access_boundary_changed"
fi

api_ip="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${api_id}")"
worker_ip="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${worker_id}")"
if [[ "${api_ip}" != "10.240.10.10" || "${worker_ip}" != "10.240.10.11" ]]; then
    restore_original_and_exit "fixed_container_ip_changed"
fi

domain_code="$(
    curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 15 \
        -H "Host: ${DOMAIN}" http://127.0.0.1:9000/ || true
)"
case "${domain_code}" in
    200|301|302|303|307|308) ;;
    *) restore_original_and_exit "domain_host_still_rejected|http_${domain_code}" ;;
esac

raw_owned="$(iptables -w 5 -t raw -S PREROUTING | grep -c -- '--comment ai-hybrid-' || true)"
docker_owned="$(iptables -w 5 -S DOCKER-USER | grep -c -- '--comment ai-hybrid-' || true)"
if [[ "${raw_owned}" -ne 3 || "${docker_owned}" -ne 2 ]]; then
    restore_original_and_exit "private_forward_rule_counts_changed"
fi

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]] \
    || ss -lntH 'sport = :443' 2>/dev/null | grep -q .; then
    restore_original_and_exit "nginx_or_443_changed_unexpectedly"
fi

echo "PASS|partial_trusted_host_recovery"
echo "PASS|runtime_trusted_hosts_contains_domain"
echo "PASS|public_access_false"
echo "PASS|application_ready_with_worker"
echo "PASS|fixed_container_ips"
echo "PASS|domain_host_accepted|http_${domain_code}"
echo "PASS|private_forward_rule_counts|raw_${raw_owned}|docker_user_${docker_owned}"
echo "PASS|nginx_still_inactive"
echo "ORIGINAL_BACKUP_DIR=${ORIGINAL_BACKUP_DIR}"
echo "RECOVERY_DIR=${RECOVERY_DIR}"
echo "NEXT|retry_runtime_nginx_start"
