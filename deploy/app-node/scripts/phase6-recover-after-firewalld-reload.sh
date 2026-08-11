#!/usr/bin/env bash
set -Eeuo pipefail

readonly CURRENT_IPSEC_SOURCE="14.218.34.192/32"
readonly TRANSITIONAL_IPSEC_SOURCE="120.229.193.76/32"
readonly PRIVATE_SSH_SOURCE="192.168.88.128/32"
readonly PRIVATE_SSH_DESTINATION="10.240.10.1/32"
readonly FORWARD_SERVICE="ai-middle-office-private-forward-firewall.service"
readonly BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
readonly STAMP="$(date -u +%Y%m%d_%H%M%S)"
readonly BACKUP_DIR="${BACKUP_ROOT}/pre-docker-reconcile-${STAMP}"
readonly REPORT="/home/aiadmin/ai-phase6-docker-reconcile-${STAMP}.txt"

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
    ! printf '%s\n' "${rich_rules}" | grep -Fq 'source address="48.47.99.19/32"'
    ! printf '%s\n' "${rich_rules}" | grep -Fq "source address=\"${CURRENT_IPSEC_SOURCE}\" port port=\"22\""
    ! printf '%s\n' "${rich_rules}" | grep -Fq "source address=\"${TRANSITIONAL_IPSEC_SOURCE}\" port port=\"22\""
}

if [[ ${EUID} -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

echo "=== PRE-RECOVERY GATE ==="
printf 'timestamp=%s\n' "$(date --iso-8601=seconds)"
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"

failed_reload_report="$(find /home/aiadmin -maxdepth 1 -type f -name 'ai-phase6-controlled-firewalld-reload-*.txt' -printf '%T@ %p\n' | sort -n | tail -n 1 | cut -d' ' -f2-)"
if [[ -z "${failed_reload_report}" || ! -f "${failed_reload_report}" ]]; then
    echo "FAIL|missing_failed_reload_report"
    exit 1
fi
grep -Fxq 'active_total=0' "${failed_reload_report}"
grep -Fxq 'RESULT=FAIL' "${failed_reload_report}"
reload_journal="$(journalctl -u "${FORWARD_SERVICE}" --since '2026-08-05 23:18:00' --no-pager)"
grep -Fq 'DOCKER-USER' <<<"${reload_journal}"
grep -Fq 'is incompatible' <<<"${reload_journal}"
echo "PASS|failed_reload_evidence_confirmed"

[[ "$(systemctl is-active firewalld)" == "active" ]]
[[ "$(systemctl is-active docker)" == "active" ]]
[[ "$(systemctl is-active ipsec)" == "active" ]]
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]]
validate_firewalld_scope runtime
validate_firewalld_scope permanent
[[ "$(firewall-cmd --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)" -eq 1 ]]
[[ "$(firewall-cmd --permanent --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)" -eq 1 ]]
echo "PASS|host_and_firewalld_boundary_before_recovery"

api_id_before="$(docker ps -q \
    --filter label=com.docker.compose.project=ai-middle-office-app \
    --filter label=com.docker.compose.service=api | head -n 1)"
worker_id_before="$(docker ps -q \
    --filter label=com.docker.compose.project=ai-middle-office-app \
    --filter label=com.docker.compose.service=worker | head -n 1)"
if [[ -z "${api_id_before}" || -z "${worker_id_before}" ]]; then
    echo "FAIL|expected_containers_missing_before_recovery"
    exit 1
fi
docker exec "${api_id_before}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'
echo "PASS|public_access_enabled_false_before_recovery"

echo "=== FAILED-STATE BACKUP ==="
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
iptables-save >"${BACKUP_DIR}/iptables-save.failed-state" 2>"${BACKUP_DIR}/iptables-save.failed-state.stderr" || true
nft -a list ruleset >"${BACKUP_DIR}/nft-ruleset.failed-state"
firewall-cmd --zone=public --list-all >"${BACKUP_DIR}/firewalld-public-runtime.failed-state"
firewall-cmd --permanent --zone=public --list-all >"${BACKUP_DIR}/firewalld-public-permanent.failed-state"
firewall-cmd --direct --get-all-rules >"${BACKUP_DIR}/firewalld-direct-runtime.failed-state"
firewall-cmd --permanent --direct --get-all-rules >"${BACKUP_DIR}/firewalld-direct-permanent.failed-state"
cp -a "${failed_reload_report}" "${BACKUP_DIR}/"
{
    systemctl status "${FORWARD_SERVICE}" --no-pager -l || true
    journalctl -u "${FORWARD_SERVICE}" --since '30 minutes ago' --no-pager || true
    docker inspect --format 'api|name={{.Name}}|id={{.Id}}|state={{.State.Status}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}|started={{.State.StartedAt}}' "${api_id_before}"
    docker inspect --format 'worker|name={{.Name}}|id={{.Id}}|state={{.State.Status}}|started={{.State.StartedAt}}' "${worker_id_before}"
    ipsec trafficstatus
} >"${BACKUP_DIR}/safe-state.failed.txt" 2>&1
sha256sum "${BACKUP_DIR}"/* >"${BACKUP_DIR}/SHA256SUMS"
echo "PASS|failed_state_backed_up"

echo "=== DOCKER FIREWALL RECONCILIATION ==="
systemctl restart docker.service
for attempt in $(seq 1 24)
do
    if [[ "$(systemctl is-active docker.service 2>/dev/null || true)" == "active" ]]; then
        echo "docker_active_attempt=${attempt}"
        break
    fi
    sleep 5
done
[[ "$(systemctl is-active docker.service)" == "active" ]]
echo "PASS|docker_daemon_restarted"

systemctl restart "${FORWARD_SERVICE}"
[[ "$(systemctl is-active "${FORWARD_SERVICE}")" == "active" ]]
[[ "$(systemctl show -p ExecMainStatus --value "${FORWARD_SERVICE}")" == "0" ]]
echo "PASS|private_forward_service_restarted"

echo "=== POST-RECOVERY CONTAINERS ==="
api_id=""
worker_id=""
for attempt in $(seq 1 36)
do
    api_id="$(docker ps -q \
        --filter label=com.docker.compose.project=ai-middle-office-app \
        --filter label=com.docker.compose.service=api | head -n 1)"
    worker_id="$(docker ps -q \
        --filter label=com.docker.compose.project=ai-middle-office-app \
        --filter label=com.docker.compose.service=worker | head -n 1)"
    if [[ -n "${api_id}" && -n "${worker_id}" ]]; then
        echo "containers_running_attempt=${attempt}"
        break
    fi
    sleep 5
done
[[ -n "${api_id}" && -n "${worker_id}" ]]
docker ps --filter label=com.docker.compose.project=ai-middle-office-app \
    --format '{{.Names}}|{{.Status}}|{{.Ports}}'
echo "PASS|application_containers_running"

echo "=== POST-RECOVERY FIREWALL GATE ==="
validate_firewalld_scope runtime
validate_firewalld_scope permanent
[[ "$(firewall-cmd --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)" -eq 1 ]]
[[ "$(firewall-cmd --permanent --direct --get-all-rules | grep -Fxc -- "${DIRECT_RULE}" || true)" -eq 1 ]]
raw_owned="$(iptables -w 5 -t raw -S PREROUTING | grep -c -- '--comment ai-hybrid-' || true)"
docker_owned="$(iptables -w 5 -S DOCKER-USER | grep -c -- '--comment ai-hybrid-' || true)"
printf 'raw_owned_rule_count=%s\ndocker_user_owned_rule_count=%s\n' "${raw_owned}" "${docker_owned}"
[[ "${raw_owned}" -eq 3 && "${docker_owned}" -eq 2 ]]
echo "PASS|firewall_and_private_forward_recovered"

echo "=== POST-RECOVERY PRIVATE PORT GATE ==="
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

echo "=== POST-RECOVERY APPLICATION GATE ==="
worker_hostname="$(docker inspect --format '{{.Config.Hostname}}' "${worker_id}")"
celery_node="quote-worker@${worker_hostname}"
celery_output=""
health_json=""
application_ready=0
for attempt in $(seq 1 24)
do
    celery_output="$(docker exec "${api_id}" python -m celery \
        -A app.tasks.celery_app.celery_app inspect ping \
        --destination "${celery_node}" --timeout=5 2>&1 || true)"
    health_json="$(curl -fsS --max-time 10 http://127.0.0.1:9000/health/ready 2>/dev/null || true)"
    if printf '%s\n' "${celery_output}" | grep -Fq 'pong' && \
       [[ -n "${health_json}" ]] && \
       [[ "$(printf '%s' "${health_json}" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("status", ""))')" == "ready" ]]
    then
        application_ready=1
        echo "application_ready_attempt=${attempt}"
        break
    fi
    sleep 5
done
printf '%s\n' "${celery_output}"
[[ ${application_ready} -eq 1 ]]
printf '%s' "${health_json}" | python3 -c 'import json, sys; d=json.load(sys.stdin); q=d.get("task_queue", {}); print("health_status=" + str(d.get("status"))); print("database=" + str(d.get("database"))); print("external_dependencies=" + str(d.get("external_dependencies", {}).get("overall_status"))); print("broker=" + str(q.get("broker"))); print("worker=" + str(q.get("worker"))); print("worker_count=" + str(q.get("worker_count")))'
echo "PASS|application_ready"

docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]]
! ss -H -lnt 'sport = :443' | grep -q .
ss -H -lnt 'sport = :9000' | awk '{print $4}' | grep -Eq '^(127\.0\.0\.1|\[::1\]):9000$'
echo "PASS|public_boundary_unchanged"

ipsec trafficstatus
echo "PASS|phase6_docker_firewall_reconciliation"
