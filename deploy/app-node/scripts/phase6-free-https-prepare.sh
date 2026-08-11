#!/usr/bin/env bash
set -euo pipefail

DOMAIN="www.qskingship.com"
SOURCE_CONFIG="${1:-/home/aiadmin/ai-middle-office-phase6/ai-middle-office-https.conf}"
PENDING_CONFIG="/etc/nginx/conf.d/ai-middle-office.conf.pending"
BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/pre-free-https-${STAMP}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

if [[ ! -f "${SOURCE_CONFIG}" ]]; then
    echo "ERROR|missing_source_config" >&2
    exit 1
fi

if ! grep -Fq "server_name ${DOMAIN};" "${SOURCE_CONFIG}"; then
    echo "ERROR|unexpected_domain" >&2
    exit 1
fi

if ! grep -Fq "listen 443 ssl" "${SOURCE_CONFIG}"; then
    echo "ERROR|https_listener_missing" >&2
    exit 1
fi

if grep -Eq 'listen[[:space:]]+80([[:space:];]|$)' "${SOURCE_CONFIG}"; then
    echo "ERROR|http_listener_forbidden" >&2
    exit 1
fi

install -d -o root -g root -m 0700 "${BACKUP_DIR}"

cp -a /etc/nginx/nginx.conf "${BACKUP_DIR}/nginx.conf"
if [[ -d /etc/nginx/conf.d ]]; then
    tar -C /etc/nginx -cpf "${BACKUP_DIR}/nginx-conf.d.tar" conf.d
fi
if [[ -d /etc/firewalld ]]; then
    tar -C /etc -cpf "${BACKUP_DIR}/firewalld.tar" firewalld
fi
if [[ -f /etc/ai-middle-office/app.env ]]; then
    cp -a /etc/ai-middle-office/app.env "${BACKUP_DIR}/app.env"
fi

{
    echo "timestamp_utc=${STAMP}"
    echo "domain=${DOMAIN}"
    echo "nginx_state=$(systemctl is-active nginx 2>/dev/null || true)"
    echo "nginx_enabled=$(systemctl is-enabled nginx 2>/dev/null || true)"
    echo "firewalld_state=$(firewall-cmd --state 2>/dev/null || true)"
    echo "selinux_mode=$(getenforce 2>/dev/null || true)"
    echo "public_listeners_begin"
    ss -lntup 2>/dev/null || true
    echo "public_listeners_end"
    echo "firewalld_public_begin"
    firewall-cmd --zone=public --list-all 2>/dev/null || true
    echo "firewalld_public_end"
} > "${BACKUP_DIR}/preflight-state.txt"

chmod 0600 "${BACKUP_DIR}/preflight-state.txt"
install -o root -g root -m 0644 "${SOURCE_CONFIG}" "${PENDING_CONFIG}"

# The .pending suffix is deliberately outside nginx.conf's *.conf include.
# This validates the existing active configuration only; the HTTPS file is
# activated later, after its certificate and private key have been verified.
nginx -t

find "${BACKUP_DIR}" -maxdepth 1 -type f -print0 \
    | sort -z \
    | xargs -0 sha256sum > "${BACKUP_DIR}/SHA256SUMS"
chmod 0600 "${BACKUP_DIR}/SHA256SUMS"

echo "PASS|phase6_pending_config_prepared"
echo "BACKUP_DIR=${BACKUP_DIR}"
echo "PENDING_CONFIG=${PENDING_CONFIG}"
echo "NGINX_ACTIVE=$(systemctl is-active nginx 2>/dev/null || true)"
