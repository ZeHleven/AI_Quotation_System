#!/usr/bin/env bash
set -euo pipefail

DOMAIN="www.qskingship.com"
EXPECTED_CONFIG_SHA256="472e7fc79078b9b26fd94bfe33cb0110e5beb3e29a73f3bef4f5003af8302f67"
ACTIVE_CONFIG="/etc/nginx/conf.d/ai-middle-office.conf"
CERT_FILE="/etc/letsencrypt/live/${DOMAIN}/cert.pem"
KEY_FILE="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/pre-runtime-nginx-start-${STAMP}"
REPORT="/home/aiadmin/ai-phase6-runtime-nginx-start-${STAMP}.txt"

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

fail_and_stop() {
    local reason="$1"
    systemctl stop nginx >/dev/null 2>&1 || true
    echo "ERROR|${reason}" >&2
    echo "ROLLBACK|nginx_stopped" >&2
    exit 1
}

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1
trap finish EXIT

for required_file in "${ACTIVE_CONFIG}" "${CERT_FILE}" "${KEY_FILE}"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_file_not_readable|${required_file}" >&2
        exit 1
    fi
done

if [[ ! -r /etc/ai-middle-office/app.env ]] \
    || ! grep -Eq '^PUBLIC_ACCESS_ENABLED=false([[:space:]]*)$' \
        /etc/ai-middle-office/app.env; then
    echo "ERROR|public_access_boundary_not_confirmed" >&2
    exit 1
fi

if [[ "$(sha256sum "${ACTIVE_CONFIG}" | awk '{print $1}')" \
    != "${EXPECTED_CONFIG_SHA256}" ]]; then
    echo "ERROR|active_config_hash_mismatch" >&2
    exit 1
fi

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]]; then
    echo "ERROR|nginx_already_active" >&2
    exit 1
fi

if ss -lntH 'sport = :443' 2>/dev/null | grep -q .; then
    echo "ERROR|port_443_already_listening" >&2
    exit 1
fi

if ! firewall-cmd --zone=public --query-service=https >/dev/null; then
    echo "ERROR|https_runtime_firewall_rule_missing" >&2
    exit 1
fi

if ! firewall-cmd --permanent --zone=public --query-service=https >/dev/null; then
    echo "ERROR|https_permanent_firewall_rule_missing" >&2
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
} > "${BACKUP_DIR}/preflight-state.txt"
chmod 0600 "${BACKUP_DIR}/preflight-state.txt"
openssl x509 -in "${CERT_FILE}" -noout -subject -issuer -dates -fingerprint -sha256 \
    > "${BACKUP_DIR}/certificate-metadata.txt"
chmod 0600 "${BACKUP_DIR}/certificate-metadata.txt"

READY_BODY="${BACKUP_DIR}/ready-before-start.json"
READY_CODE="$(
    curl -sS --noproxy '*' -o "${READY_BODY}" -w '%{http_code}' --max-time 15 \
        http://127.0.0.1:9000/health/ready || true
)"
chmod 0600 "${READY_BODY}"
if [[ "${READY_CODE}" != "200" ]] \
    || ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"' "${READY_BODY}"; then
    echo "ERROR|application_not_ready_before_nginx_start|http_${READY_CODE}" >&2
    exit 1
fi

systemctl start nginx

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" != "active" ]]; then
    fail_and_stop "nginx_failed_to_start"
fi

if [[ "$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)" -lt 1 ]]; then
    fail_and_stop "port_443_not_listening"
fi

ROOT_HEADERS="${BACKUP_DIR}/local-root-headers.txt"
ROOT_CODE="$(
    curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
        -D "${ROOT_HEADERS}" -o /dev/null -w '%{http_code}' \
        --max-time 20 "https://${DOMAIN}/" || true
)"
chmod 0600 "${ROOT_HEADERS}"
case "${ROOT_CODE}" in
    200|301|302|303|307|308) ;;
    *) fail_and_stop "local_https_root_failed|http_${ROOT_CODE}" ;;
esac

for required_header in \
    strict-transport-security \
    x-content-type-options \
    x-frame-options \
    referrer-policy \
    permissions-policy \
    content-security-policy; do
    if ! grep -Eiq "^${required_header}:" "${ROOT_HEADERS}"; then
        fail_and_stop "security_header_missing|${required_header}"
    fi
done

for blocked_path in \
    /docs \
    /redoc \
    /openapi.json \
    /api/v1/admin/codex-worker/ \
    /api/v1/admin/dwg-quantity-trial/; do
    STATUS_CODE="$(
        curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
            -o /dev/null -w '%{http_code}' --max-time 15 \
            "https://${DOMAIN}${blocked_path}" || true
    )"
    if [[ "${STATUS_CODE}" != "404" ]]; then
        fail_and_stop "blocked_path_gate_failed|${blocked_path}|http_${STATUS_CODE}"
    fi
done

LOCAL_READY_CODE="$(
    curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
        -o /dev/null -w '%{http_code}' --max-time 15 \
        "https://${DOMAIN}/health/ready" || true
)"
if [[ "${LOCAL_READY_CODE}" != "200" ]]; then
    fail_and_stop "localhost_readiness_proxy_failed|http_${LOCAL_READY_CODE}"
fi

find "${BACKUP_DIR}" -maxdepth 1 -type f -print0 \
    | sort -z \
    | xargs -0 sha256sum > "${BACKUP_DIR}/SHA256SUMS"
chmod 0600 "${BACKUP_DIR}/SHA256SUMS"

echo "PASS|runtime_nginx_started"
echo "PASS|local_tls_hostname_and_chain"
echo "PASS|local_security_headers"
echo "PASS|public_sensitive_routes_blocked"
echo "PASS|localhost_readiness_allowed"
echo "BACKUP_DIR=${BACKUP_DIR}"
echo "NGINX_ACTIVE=$(systemctl is-active nginx 2>/dev/null || true)"
echo "NGINX_ENABLED=$(systemctl is-enabled nginx 2>/dev/null || true)"
echo "LISTENER_443_COUNT=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
echo "NEXT|external_https_gate_before_enable"
