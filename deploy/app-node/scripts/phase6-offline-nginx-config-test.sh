#!/usr/bin/env bash
set -euo pipefail

DOMAIN="www.qskingship.com"
SOURCE_CONFIG="/home/aiadmin/ai-middle-office-phase6/ai-middle-office-https.conf"
PENDING_CONFIG="/etc/nginx/conf.d/ai-middle-office.conf.pending"
ACTIVE_CONFIG="/etc/nginx/conf.d/ai-middle-office.conf"
CERT_FILE="/etc/letsencrypt/live/${DOMAIN}/cert.pem"
KEY_FILE="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/pre-offline-nginx-config-${STAMP}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

for required_file in \
    "${SOURCE_CONFIG}" \
    "${PENDING_CONFIG}" \
    "${CERT_FILE}" \
    "${KEY_FILE}"; do
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

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]]; then
    echo "ERROR|nginx_must_remain_inactive" >&2
    exit 1
fi

if ss -lntH 'sport = :443' 2>/dev/null | grep -q .; then
    echo "ERROR|port_443_already_listening" >&2
    exit 1
fi

if [[ -e "${ACTIVE_CONFIG}" || -L "${ACTIVE_CONFIG}" ]]; then
    echo "ERROR|active_config_already_exists" >&2
    exit 1
fi

SOURCE_HASH="$(sha256sum "${SOURCE_CONFIG}" | awk '{print $1}')"
PENDING_HASH="$(sha256sum "${PENDING_CONFIG}" | awk '{print $1}')"
if [[ "${SOURCE_HASH}" != "${PENDING_HASH}" ]]; then
    echo "ERROR|pending_config_hash_mismatch" >&2
    exit 1
fi

if grep -Eq 'listen[[:space:]]+80([[:space:];]|$)' "${PENDING_CONFIG}"; then
    echo "ERROR|http_listener_forbidden" >&2
    exit 1
fi

if ! grep -Fq "server_name ${DOMAIN};" "${PENDING_CONFIG}"; then
    echo "ERROR|expected_domain_missing" >&2
    exit 1
fi

CERT_PUBLIC_KEY_HASH="$(
    openssl x509 -in "${CERT_FILE}" -pubkey -noout \
        | openssl pkey -pubin -outform DER 2>/dev/null \
        | sha256sum \
        | awk '{print $1}'
)"
KEY_PUBLIC_KEY_HASH="$(
    openssl pkey -in "${KEY_FILE}" -pubout -outform DER 2>/dev/null \
        | sha256sum \
        | awk '{print $1}'
)"
if [[ -z "${CERT_PUBLIC_KEY_HASH}" \
    || "${CERT_PUBLIC_KEY_HASH}" != "${KEY_PUBLIC_KEY_HASH}" ]]; then
    echo "ERROR|certificate_private_key_mismatch" >&2
    exit 1
fi

install -d -o root -g root -m 0700 "${BACKUP_DIR}"
cp -a /etc/nginx/nginx.conf "${BACKUP_DIR}/nginx.conf"
tar -C /etc/nginx -cpf "${BACKUP_DIR}/nginx-conf.d.tar" conf.d
{
    echo "timestamp_utc=${STAMP}"
    echo "domain=${DOMAIN}"
    echo "source_config_sha256=${SOURCE_HASH}"
    echo "nginx_state=$(systemctl is-active nginx 2>/dev/null || true)"
    echo "nginx_enabled=$(systemctl is-enabled nginx 2>/dev/null || true)"
    echo "listener_443_count=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
    echo "public_access_enabled=false"
} > "${BACKUP_DIR}/preflight-state.txt"
chmod 0600 "${BACKUP_DIR}/preflight-state.txt"

install -o root -g root -m 0644 "${PENDING_CONFIG}" "${ACTIVE_CONFIG}"

if ! nginx -t > "${BACKUP_DIR}/nginx-test.txt" 2>&1; then
    mv "${ACTIVE_CONFIG}" "${BACKUP_DIR}/ai-middle-office.conf.failed"
    chmod 0600 "${BACKUP_DIR}/ai-middle-office.conf.failed"
    cat "${BACKUP_DIR}/nginx-test.txt" >&2
    echo "ERROR|offline_nginx_config_test_failed" >&2
    echo "FAILED_CONFIG=${BACKUP_DIR}/ai-middle-office.conf.failed" >&2
    exit 1
fi
chmod 0600 "${BACKUP_DIR}/nginx-test.txt"

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]]; then
    echo "ERROR|nginx_started_unexpectedly" >&2
    exit 1
fi

if ss -lntH 'sport = :443' 2>/dev/null | grep -q .; then
    echo "ERROR|port_443_started_listening_unexpectedly" >&2
    exit 1
fi

find "${BACKUP_DIR}" -maxdepth 1 -type f -print0 \
    | sort -z \
    | xargs -0 sha256sum > "${BACKUP_DIR}/SHA256SUMS"
chmod 0600 "${BACKUP_DIR}/SHA256SUMS"

cat "${BACKUP_DIR}/nginx-test.txt"
echo "PASS|offline_nginx_config_test"
echo "ACTIVE_CONFIG_SHA256=$(sha256sum "${ACTIVE_CONFIG}" | awk '{print $1}')"
echo "BACKUP_DIR=${BACKUP_DIR}"
echo "NGINX_ACTIVE=$(systemctl is-active nginx 2>/dev/null || true)"
echo "LISTENER_443_COUNT=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
echo "NEXT|controlled_nginx_start_and_external_https_gate"
