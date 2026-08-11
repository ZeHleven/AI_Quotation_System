#!/usr/bin/env bash
set -Eeuo pipefail

readonly CURRENT_IPSEC_SOURCE="120.229.193.76/32"
readonly PRIVATE_SSH_SOURCE="192.168.88.128/32"
readonly PRIVATE_SSH_DESTINATION="10.240.10.1/32"
readonly REPORT="/home/aiadmin/ai-phase6-preflight-readonly-audit-$(date -u +%Y%m%d_%H%M%S).txt"

failures=0

pass() {
    printf 'PASS|%s\n' "$1"
}

fail() {
    printf 'FAIL|%s\n' "$1"
    failures=$((failures + 1))
}

section() {
    printf '\n=== %s ===\n' "$1"
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

finish() {
    local rc=$?
    chmod 0644 "${REPORT}" 2>/dev/null || true
    chown aiadmin:aiadmin "${REPORT}" 2>/dev/null || true
    printf '\nREPORT=%s\n' "${REPORT}"
    if [[ ${rc} -ne 0 ]]; then
        printf 'RESULT=ERROR\n'
        return "${rc}"
    fi
    if [[ ${failures} -eq 0 ]]; then
        printf 'RESULT=PASS\n'
    else
        printf 'RESULT=FAIL\n'
    fi
}

if [[ ${EUID} -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

section "IDENTITY AND BOOT"
printf 'timestamp=%s\n' "$(date --iso-8601=seconds)"
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
uptime

section "SERVICE STATE"
for service in \
    ai-middle-office-private-forward-firewall.service \
    firewalld.service \
    docker.service \
    ipsec.service
do
    enabled="$(systemctl is-enabled "${service}" 2>/dev/null || true)"
    active="$(systemctl is-active "${service}" 2>/dev/null || true)"
    printf '%s|enabled=%s|active=%s\n' "${service}" "${enabled}" "${active}"
    if [[ "${enabled}" == "enabled" && "${active}" == "active" ]]; then
        pass "service_${service}"
    else
        fail "service_${service}"
    fi
done

nginx_enabled="$(systemctl is-enabled nginx.service 2>/dev/null || true)"
nginx_active="$(systemctl is-active nginx.service 2>/dev/null || true)"
printf 'nginx.service|enabled=%s|active=%s\n' "${nginx_enabled}" "${nginx_active}"
if [[ "${nginx_active}" == "inactive" ]]; then
    pass "nginx_still_inactive"
else
    fail "nginx_still_inactive"
fi

systemctl show ai-middle-office-private-forward-firewall.service \
    -p ActiveState -p SubState -p UnitFileState -p FragmentPath \
    -p ExecMainStatus -p NeedDaemonReload

section "INSTALLED FORWARDING ARTIFACTS"
sha256sum \
    /usr/local/sbin/ai-middle-office-private-forward-firewall.sh \
    /etc/systemd/system/ai-middle-office-private-forward-firewall.service

section "FIREWALLD PUBLIC RUNTIME"
firewall-cmd --state
firewall-cmd --get-active-zones
firewall-cmd --zone=public --list-all

section "FIREWALLD PUBLIC PERMANENT"
firewall-cmd --permanent --zone=public --list-all

readonly IKE_RULE="rule family=\"ipv4\" source address=\"${CURRENT_IPSEC_SOURCE}\" port port=\"500\" protocol=\"udp\" accept"
readonly NATT_RULE="rule family=\"ipv4\" source address=\"${CURRENT_IPSEC_SOURCE}\" port port=\"4500\" protocol=\"udp\" accept"
readonly PRIVATE_SSH_RULE="rule family=\"ipv4\" source address=\"${PRIVATE_SSH_SOURCE}\" destination address=\"${PRIVATE_SSH_DESTINATION}\" port port=\"22\" protocol=\"tcp\" accept"

section "TARGETED FIREWALLD GATE"
for scope in runtime permanent
do
    for item in \
        "ike|${IKE_RULE}" \
        "natt|${NATT_RULE}" \
        "private_ssh|${PRIVATE_SSH_RULE}"
    do
        label="${item%%|*}"
        rule="${item#*|}"
        if query_rich_rule "${scope}" "${rule}"; then
            pass "firewalld_${scope}_${label}"
        else
            fail "firewalld_${scope}_${label}"
        fi
    done

    if query_service "${scope}" https; then
        pass "firewalld_${scope}_https"
    else
        fail "firewalld_${scope}_https"
    fi

    if query_service "${scope}" ssh; then
        fail "firewalld_${scope}_public_ssh_service_absent"
    else
        pass "firewalld_${scope}_public_ssh_service_absent"
    fi

    scope_args=()
    if [[ "${scope}" == "permanent" ]]; then
        scope_args+=(--permanent)
    fi
    rich_rules="$(firewall-cmd "${scope_args[@]}" --zone=public --list-rich-rules)"
    tcp22_count="$(printf '%s\n' "${rich_rules}" | grep -c 'port="22" protocol="tcp"' || true)"
    printf '%s_tcp22_rich_rule_count=%s\n' "${scope}" "${tcp22_count}"
    if [[ "${tcp22_count}" -eq 1 ]]; then
        pass "firewalld_${scope}_only_one_tcp22_rich_rule"
    else
        fail "firewalld_${scope}_only_one_tcp22_rich_rule"
    fi
    if printf '%s\n' "${rich_rules}" | grep -Eq 'source address="(14\.218\.34\.192|48\.47\.99\.19)/32"'; then
        fail "firewalld_${scope}_old_public_sources_absent"
    else
        pass "firewalld_${scope}_old_public_sources_absent"
    fi
    if printf '%s\n' "${rich_rules}" | grep -Fq "source address=\"${CURRENT_IPSEC_SOURCE}\" port port=\"22\""; then
        fail "firewalld_${scope}_current_public_ssh_absent"
    else
        pass "firewalld_${scope}_current_public_ssh_absent"
    fi
done

section "FIREWALLD DIRECT RULES"
echo "runtime_direct_begin"
firewall-cmd --direct --get-all-rules || true
echo "runtime_direct_end"
echo "permanent_direct_begin"
firewall-cmd --permanent --direct --get-all-rules || true
echo "permanent_direct_end"

section "IPTABLES PRIVATE FORWARDING"
iptables -w 5 -t raw -nvL PREROUTING --line-numbers
iptables -w 5 -nvL DOCKER-USER --line-numbers
raw_owned="$(iptables -w 5 -t raw -S PREROUTING | grep -c -- '--comment ai-hybrid-' || true)"
docker_owned="$(iptables -w 5 -S DOCKER-USER | grep -c -- '--comment ai-hybrid-' || true)"
printf 'raw_owned_rule_count=%s\n' "${raw_owned}"
printf 'docker_user_owned_rule_count=%s\n' "${docker_owned}"
if [[ "${raw_owned}" -eq 3 ]]; then
    pass "raw_owned_rule_count"
else
    fail "raw_owned_rule_count"
fi
if [[ "${docker_owned}" -eq 2 ]]; then
    pass "docker_user_owned_rule_count"
else
    fail "docker_user_owned_rule_count"
fi

section "NFTABLES RELEVANT CHAINS"
for chain in \
    filter_INPUT \
    filter_IN_public_allow \
    filter_FORWARD \
    filter_FWD_public \
    filter_FWD_public_allow
do
    echo "chain=${chain}"
    nft -a list chain inet firewalld "${chain}" 2>&1 || true
done

section "IPSEC"
ipsec trafficstatus
ip -s xfrm policy

section "ROUTES"
ip -4 route

section "DOCKER RUNTIME"
docker network ls --format '{{.Name}}|{{.Driver}}|{{.Scope}}'
docker ps -a --filter label=com.docker.compose.project=ai-middle-office-app \
    --format '{{.Names}}|{{.Status}}|{{.Ports}}'

api_id="$(docker ps -q --filter label=com.docker.compose.project=ai-middle-office-app --filter label=com.docker.compose.service=api | head -n 1)"
worker_id="$(docker ps -q --filter label=com.docker.compose.project=ai-middle-office-app --filter label=com.docker.compose.service=worker | head -n 1)"

if [[ -n "${api_id}" ]]; then
    pass "api_container_running"
    docker inspect --format 'api|user={{.Config.User}}|state={{.State.Status}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}|restart={{.HostConfig.RestartPolicy.Name}}|network_mode={{.HostConfig.NetworkMode}}' "${api_id}"
    docker inspect --format '{{range $name, $network := .NetworkSettings.Networks}}api_network={{$name}}|ip={{$network.IPAddress}}{{println}}{{end}}' "${api_id}"
    if docker exec "${api_id}" sh -lc 'test "${PUBLIC_ACCESS_ENABLED:-}" = false'; then
        pass "public_access_enabled_false"
    else
        fail "public_access_enabled_false"
    fi
else
    fail "api_container_running"
fi

if [[ -n "${worker_id}" ]]; then
    pass "worker_container_running"
    docker inspect --format 'worker|user={{.Config.User}}|state={{.State.Status}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}|restart={{.HostConfig.RestartPolicy.Name}}|network_mode={{.HostConfig.NetworkMode}}' "${worker_id}"
    docker inspect --format '{{range $name, $network := .NetworkSettings.Networks}}worker_network={{$name}}|ip={{$network.IPAddress}}{{println}}{{end}}' "${worker_id}"
else
    fail "worker_container_running"
fi

section "LOCAL LISTENERS"
ss -lntup
if ss -H -lnt 'sport = :443' | grep -q .; then
    fail "https_listener_still_absent"
else
    pass "https_listener_still_absent"
fi
if ss -H -lnt 'sport = :9000' | awk '{print $4}' | grep -Eq '^(127\.0\.0\.1|\[::1\]):9000$'; then
    pass "api_loopback_listener"
else
    fail "api_loopback_listener"
fi

section "APPLICATION HEALTH"
health_json="$(curl -fsS --max-time 10 http://127.0.0.1:9000/health/ready || true)"
if [[ -n "${health_json}" ]]; then
    printf '%s' "${health_json}" | python3 -c 'import json, sys; d=json.load(sys.stdin); q=d.get("task_queue", {}); print("health_status=" + str(d.get("status"))); print("database=" + str(d.get("database"))); print("external_dependencies=" + str(d.get("external_dependencies", {}).get("overall_status"))); print("broker=" + str(q.get("broker"))); print("worker=" + str(q.get("worker"))); print("worker_count=" + str(q.get("worker_count")))'
    health_status="$(printf '%s' "${health_json}" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("status", ""))')"
    if [[ "${health_status}" == "ready" ]]; then
        pass "application_ready"
    else
        fail "application_ready"
    fi
else
    fail "application_ready"
fi

if [[ -n "${api_id}" ]]; then
    celery_output="$(docker exec "${api_id}" python -m celery -A app.tasks.celery_app.celery_app inspect ping --timeout=5 2>&1 || true)"
    printf '%s\n' "${celery_output}"
    if printf '%s\n' "${celery_output}" | grep -Fq 'pong'; then
        pass "celery_worker_ping"
    else
        fail "celery_worker_ping"
    fi
fi

section "SELINUX"
getenforce
if [[ "$(getenforce)" == "Permissive" ]]; then
    printf 'KNOWN_RISK|selinux_permissive\n'
fi

section "SUMMARY"
printf 'failure_count=%s\n' "${failures}"
if [[ ${failures} -eq 0 ]]; then
    pass "phase6_preflight_readonly_audit"
else
    fail "phase6_preflight_readonly_audit"
fi
