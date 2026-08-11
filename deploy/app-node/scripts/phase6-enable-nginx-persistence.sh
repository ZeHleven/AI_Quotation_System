#!/usr/bin/env bash
set -Eeuo pipefail

readonly DOMAIN="www.qskingship.com"
readonly EXPECTED_CONFIG_SHA256="472e7fc79078b9b26fd94bfe33cb0110e5beb3e29a73f3bef4f5003af8302f67"
readonly ACTIVE_CONFIG="/etc/nginx/conf.d/ai-middle-office.conf"
readonly BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
readonly STAMP="$(date -u +%Y%m%d_%H%M%S)"
readonly BACKUP_DIR="${BACKUP_ROOT}/pre-nginx-persistence-${STAMP}"
readonly REPORT="/home/aiadmin/ai-phase6-nginx-persistence-${STAMP}.txt"

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

rollback_persistence() {
    local reason="$1"
    set +e
    echo "ERROR|${reason}" >&2
    systemctl disable nginx >/dev/null 2>&1 || true
    if nginx -t >/dev/null 2>&1; then
        systemctl start nginx >/dev/null 2>&1 || true
    fi
    echo "ROLLBACK|nginx_disabled|active_$(systemctl is-active nginx 2>/dev/null || true)" >&2
    exit 1
}

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

if [[ ! -r "${ACTIVE_CONFIG}" ]]; then
    echo "ERROR|active_config_not_readable" >&2
    exit 1
fi
if [[ "$(sha256sum "${ACTIVE_CONFIG}" | awk '{print $1}')" \
    != "${EXPECTED_CONFIG_SHA256}" ]]; then
    echo "ERROR|active_config_hash_mismatch" >&2
    exit 1
fi
if [[ ! -r /etc/ai-middle-office/app.env ]] \
    || ! grep -Eq '^PUBLIC_ACCESS_ENABLED=false([[:space:]]*)$' \
        /etc/ai-middle-office/app.env; then
    echo "ERROR|public_access_boundary_not_confirmed" >&2
    exit 1
fi
if [[ "$(systemctl is-active nginx 2>/dev/null || true)" != "active" ]]; then
    echo "ERROR|nginx_not_active_before_persistence" >&2
    exit 1
fi
if [[ "$(systemctl is-enabled nginx 2>/dev/null || true)" != "disabled" ]]; then
    echo "ERROR|nginx_enable_state_not_disabled" >&2
    exit 1
fi
if [[ "$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)" -lt 1 ]]; then
    echo "ERROR|port_443_not_listening" >&2
    exit 1
fi
if ! firewall-cmd --zone=public --query-service=https >/dev/null \
    || ! firewall-cmd --permanent --zone=public --query-service=https >/dev/null; then
    echo "ERROR|https_firewall_rule_missing" >&2
    exit 1
fi
if ! nginx -t; then
    echo "ERROR|nginx_config_test_failed" >&2
    exit 1
fi

install -d -o root -g root -m 0700 "${BACKUP_DIR}"
cp -a "${ACTIVE_CONFIG}" "${BACKUP_DIR}/ai-middle-office.conf"
{
    echo "timestamp_utc=${STAMP}"
    echo "domain=${DOMAIN}"
    echo "active_config_sha256=${EXPECTED_CONFIG_SHA256}"
    echo "nginx_state=$(systemctl is-active nginx 2>/dev/null || true)"
    echo "nginx_enabled=$(systemctl is-enabled nginx 2>/dev/null || true)"
    echo "listener_443_count=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
    echo "https_runtime=$(firewall-cmd --zone=public --query-service=https 2>/dev/null || true)"
    echo "https_permanent=$(firewall-cmd --permanent --zone=public --query-service=https 2>/dev/null || true)"
    echo "public_access_enabled=false"
} > "${BACKUP_DIR}/safe-state.before.txt"
systemctl cat nginx > "${BACKUP_DIR}/nginx-unit.txt"
chmod 0600 "${BACKUP_DIR}"/*.txt
sha256sum "${BACKUP_DIR}"/* > "${BACKUP_DIR}/SHA256SUMS"
chmod 0600 "${BACKUP_DIR}/SHA256SUMS"
echo "PASS|pre_persistence_backup"

if ! systemctl enable nginx; then
    rollback_persistence "nginx_enable_failed"
fi
if [[ "$(systemctl is-enabled nginx 2>/dev/null || true)" != "enabled" ]]; then
    rollback_persistence "nginx_not_enabled_after_enable"
fi

if ! systemctl restart nginx; then
    rollback_persistence "nginx_controlled_restart_failed"
fi
if [[ "$(systemctl is-active nginx 2>/dev/null || true)" != "active" ]]; then
    rollback_persistence "nginx_not_active_after_restart"
fi
if [[ "$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)" -lt 1 ]]; then
    rollback_persistence "port_443_not_listening_after_restart"
fi

root_code="$(
    curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
        -o /dev/null -w '%{http_code}' --max-time 20 \
        "https://${DOMAIN}/" || true
)"
case "${root_code}" in
    200|301|302|303|307|308) ;;
    *) rollback_persistence "local_https_root_failed_after_restart|http_${root_code}" ;;
esac

docs_code="$(
    curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
        -o /dev/null -w '%{http_code}' --max-time 15 \
        "https://${DOMAIN}/docs" || true
)"
if [[ "${docs_code}" != "404" ]]; then
    rollback_persistence "docs_block_failed_after_restart|http_${docs_code}"
fi

ready_code="$(
    curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
        -o /dev/null -w '%{http_code}' --max-time 15 \
        "https://${DOMAIN}/health/ready" || true
)"
if [[ "${ready_code}" != "200" ]]; then
    rollback_persistence "localhost_readiness_failed_after_restart|http_${ready_code}"
fi

echo "PASS|nginx_enabled"
echo "PASS|nginx_controlled_restart"
echo "PASS|local_https_after_restart|http_${root_code}"
echo "PASS|docs_block_after_restart|http_${docs_code}"
echo "PASS|localhost_readiness_after_restart|http_${ready_code}"
echo "BACKUP_DIR=${BACKUP_DIR}"
echo "NGINX_ACTIVE=$(systemctl is-active nginx 2>/dev/null || true)"
echo "NGINX_ENABLED=$(systemctl is-enabled nginx 2>/dev/null || true)"
echo "LISTENER_443_COUNT=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
echo "NEXT|external_https_gate_after_persistence"
