#!/usr/bin/env bash
set -Eeuo pipefail

readonly CURRENT_IPSEC_SOURCE="120.229.193.76/32"
readonly PRIVATE_SSH_SOURCE="192.168.88.128/32"
readonly PRIVATE_SSH_DESTINATION="10.240.10.1/32"
readonly FORWARD_SERVICE="ai-middle-office-private-forward-firewall.service"
readonly FORWARD_SCRIPT="/usr/local/sbin/ai-middle-office-private-forward-firewall.sh"
readonly BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
readonly STAMP="$(date -u +%Y%m%d_%H%M%S)"
readonly BACKUP_DIR="${BACKUP_ROOT}/pre-firewalld-reload-${STAMP}"
readonly REPORT="/home/aiadmin/ai-phase6-controlled-firewalld-reload-${STAMP}.txt"

readonly IKE_RULE="rule family=\"ipv4\" source address=\"${CURRENT_IPSEC_SOURCE}\" port port=\"500\" protocol=\"udp\" accept"
readonly NATT_RULE="rule family=\"ipv4\" source address=\"${CURRENT_IPSEC_SOURCE}\" port port=\"4500\" protocol=\"udp\" accept"
readonly PRIVATE_SSH_RULE="rule family=\"ipv4\" source address=\"${PRIVATE_SSH_SOURCE}\" destination address=\"${PRIVATE_SSH_DESTINATION}\" port port=\"22\" protocol=\"tcp\" accept"
readonly DIRECT_RULE="ipv4 nat POSTROUTING 0 -s 10.240.10.0/24 -d 192.168.88.128/32 -m policy --dir out --pol ipsec --mode tunnel -j ACCEPT"

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

query_rich_rule() {
    local scope="$1"
    local rule="$2"
    local args=()
    if [[ "${scope}" == "permanent" ]]; then
        args+=(--permanent)
    fi
    firewall-cmd "${args[@]}" --zone=public --query-rich-rule="${rule}" >/dev/null
}

query_service() {
    local scope="$1"
    local service="$2"
    local args=()
    if [[ "${scope}" == "permanent" ]]; then
        args+=(--permanent)
    fi
    firewall-cmd "${args[@]}" --zone=public --query-service="${service}" >/dev/null
}

validate_firewalld_scope() {
    local scope="$1"
    local args=()
    if [[ "${scope}" == "permanent" ]]; then
        args+=(--permanent)
    fi

    query_rich_rule "${scope}" "${IKE_RULE}"
    query_rich_rule "${scope}" "${NATT_RULE}"
    query_rich_rule "${scope}" "${PRIVATE_SSH_RULE}"
    query_service "${scope}" https
    ! query_service "${scope}" ssh

    local rich_rules tcp22_count
    rich_rules="$(firewall-cmd "${args[@]}" --zone=public --list-rich-rules)"
    tcp22_count="$(printf '%s\n' "${rich_rules}" | grep -c 'port="22" protocol="tcp"' || true)"
    [[ "${tcp22_count}" -eq 1 ]]
    ! printf '%s\n' "${rich_rules}" | grep -Eq 'source address="(14\.218\.34\.192|48\.47\.99\.19)/32"'
    ! printf '%s\n' "${rich_rules}" | grep -Fq "source address=\"${CURRENT_IPSEC_SOURCE}\" port port=\"22\""
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

echo "=== PRE-RELOAD SAFETY GATE ==="
printf 'timestamp=%s\n' "$(date --iso-8601=seconds)"
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"

active_job_output="$(docker exec "${api_id}" python -c 'from sqlalchemy import text; from app.core.database import engine; connection=engine.connect(); rows=connection.execute(text("SELECT status, COUNT(*) AS item_count FROM quote_jobs WHERE status IN (\"queued\", \"running\") GROUP BY status")).all(); counts={str(row[0]): int(row[1]) for row in rows}; print("queued=" + str(counts.get("queued", 0))); print("running=" + str(counts.get("running", 0))); print("active_total=" + str(sum(counts.values()))); connection.close()')"
printf '%s\n' "${active_job_output}"
active_total="$(printf '%s\n' "${active_job_output}" | awk -F= '/^active_total=/{print $2}')"
if [[ "${active_total}" != "0" ]]; then
    echo "FAIL|active_quote_jobs_present_no_reload"
    exit 1
fi
echo "PASS|no_active_quote_jobs"

for service in firewalld.service docker.service ipsec.service "${FORWARD_SERVICE}"
do
    [[ "$(systemctl is-enabled "${service}")" == "enabled" ]]
    [[ "$(systemctl is-active "${service}")" == "active" ]]
done
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]]
[[ -x "${FORWARD_SCRIPT}" ]]
validate_firewalld_scope runtime
validate_firewalld_scope permanent
[[ "$(firewall-cmd --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)" -eq 1 ]]
[[ "$(firewall-cmd --permanent --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)" -eq 1 ]]
[[ "$(iptables -w 5 -t raw -S PREROUTING | grep -c -- '--comment ai-hybrid-' || true)" -eq 3 ]]
[[ "$(iptables -w 5 -S DOCKER-USER | grep -c -- '--comment ai-hybrid-' || true)" -eq 2 ]]
docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'
echo "PASS|pre_reload_gate"

echo "=== BACKUP ==="
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
iptables-save >"${BACKUP_DIR}/iptables-save.before"
nft -a list ruleset >"${BACKUP_DIR}/nft-ruleset.before"
firewall-cmd --zone=public --list-all >"${BACKUP_DIR}/firewalld-public-runtime.before"
firewall-cmd --permanent --zone=public --list-all >"${BACKUP_DIR}/firewalld-public-permanent.before"
firewall-cmd --direct --get-all-rules >"${BACKUP_DIR}/firewalld-direct-runtime.before"
firewall-cmd --permanent --direct --get-all-rules >"${BACKUP_DIR}/firewalld-direct-permanent.before"
cp -a "${FORWARD_SCRIPT}" "${BACKUP_DIR}/"
cp -a "/etc/systemd/system/${FORWARD_SERVICE}" "${BACKUP_DIR}/"
{
    systemctl show "${FORWARD_SERVICE}" -p ActiveState -p SubState -p UnitFileState -p ExecMainStatus -p NeedDaemonReload
    docker inspect --format 'api|name={{.Name}}|id={{.Id}}|state={{.State.Status}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}|started={{.State.StartedAt}}' "${api_id}"
    docker inspect --format 'worker|name={{.Name}}|id={{.Id}}|state={{.State.Status}}|started={{.State.StartedAt}}' "${worker_id}"
    printf '%s\n' "${active_job_output}"
    ipsec trafficstatus
} >"${BACKUP_DIR}/safe-state.before.txt"
sha256sum "${BACKUP_DIR}"/* >"${BACKUP_DIR}/SHA256SUMS"
echo "PASS|pre_reload_backup"

api_started_before="$(docker inspect --format '{{.State.StartedAt}}' "${api_id}")"
worker_started_before="$(docker inspect --format '{{.State.StartedAt}}' "${worker_id}")"

echo "=== CONTROLLED FIREWALLD RELOAD ==="
firewall-cmd --reload
systemctl reload "${FORWARD_SERVICE}"
echo "PASS|firewalld_reloaded_and_private_forward_reapplied"

echo "=== POST-RELOAD FIREWALL GATE ==="
validate_firewalld_scope runtime
validate_firewalld_scope permanent
echo "PASS|firewalld_runtime_and_permanent_targets"

runtime_direct_count="$(firewall-cmd --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)"
permanent_direct_count="$(firewall-cmd --permanent --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)"
printf 'runtime_direct_count=%s\npermanent_direct_count=%s\n' "${runtime_direct_count}" "${permanent_direct_count}"
[[ "${runtime_direct_count}" -eq 1 && "${permanent_direct_count}" -eq 1 ]]
echo "PASS|firewalld_direct_rule_persisted"

raw_owned="$(iptables -w 5 -t raw -S PREROUTING | grep -c -- '--comment ai-hybrid-' || true)"
docker_owned="$(iptables -w 5 -S DOCKER-USER | grep -c -- '--comment ai-hybrid-' || true)"
printf 'raw_owned_rule_count=%s\ndocker_user_owned_rule_count=%s\n' "${raw_owned}" "${docker_owned}"
[[ "${raw_owned}" -eq 3 && "${docker_owned}" -eq 2 ]]
echo "PASS|private_forward_rule_counts"

echo "=== POST-RELOAD PRIVATE PORT GATE ==="
docker exec "${worker_id}" python -c 'import socket, sys; host="192.168.88.128"; allowed=(3306,5678,6380,8001,9002); blocked=(9001,19530); failures=[];
for port in allowed:
    try:
        connection=socket.create_connection((host, port), 5); connection.close(); print(f"PASS|allowed|{port}")
    except OSError:
        print(f"FAIL|allowed|{port}"); failures.append(port)
for port in blocked:
    try:
        connection=socket.create_connection((host, port), 3); connection.close(); print(f"FAIL|blocked|{port}"); failures.append(port)
    except OSError:
        print(f"PASS|blocked|{port}")
sys.exit(1 if failures else 0)'
echo "PASS|private_port_gate"

echo "=== POST-RELOAD APPLICATION GATE ==="
worker_hostname="$(docker inspect --format '{{.Config.Hostname}}' "${worker_id}")"
celery_node="quote-worker@${worker_hostname}"
celery_output="$(docker exec "${api_id}" python -m celery \
    -A app.tasks.celery_app.celery_app inspect ping \
    --destination "${celery_node}" --timeout=10 2>&1)"
printf '%s\n' "${celery_output}"
printf '%s\n' "${celery_output}" | grep -Fq 'pong'
echo "PASS|celery_worker_ping"

health_json="$(curl -fsS --max-time 10 http://127.0.0.1:9000/health/ready)"
printf '%s' "${health_json}" | python3 -c 'import json, sys; d=json.load(sys.stdin); q=d.get("task_queue", {}); print("health_status=" + str(d.get("status"))); print("database=" + str(d.get("database"))); print("external_dependencies=" + str(d.get("external_dependencies", {}).get("overall_status"))); print("broker=" + str(q.get("broker"))); print("worker=" + str(q.get("worker"))); print("worker_count=" + str(q.get("worker_count")))'
health_status="$(printf '%s' "${health_json}" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("status", ""))')"
worker_count="$(printf '%s' "${health_json}" | python3 -c 'import json, sys; print(int(json.load(sys.stdin).get("task_queue", {}).get("worker_count", 0)))')"
[[ "${health_status}" == "ready" && "${worker_count}" -ge 1 ]]
echo "PASS|application_ready"

docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]]
! ss -H -lnt 'sport = :443' | grep -q .
ss -H -lnt 'sport = :9000' | awk '{print $4}' | grep -Eq '^(127\.0\.0\.1|\[::1\]):9000$'
echo "PASS|public_boundary_unchanged"

api_started_after="$(docker inspect --format '{{.State.StartedAt}}' "${api_id}")"
worker_started_after="$(docker inspect --format '{{.State.StartedAt}}' "${worker_id}")"
[[ "${api_started_before}" == "${api_started_after}" ]]
[[ "${worker_started_before}" == "${worker_started_after}" ]]
echo "PASS|application_containers_not_restarted"

ipsec trafficstatus
echo "PASS|phase6_controlled_firewalld_reload"
