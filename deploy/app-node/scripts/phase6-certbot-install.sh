#!/usr/bin/env bash
set -euo pipefail

CERTBOT_VERSION="5.7.0"
INSTALL_DIR="/opt/certbot"
CERTBOT_LINK="/usr/local/bin/certbot"
BACKUP_ROOT="/home/aiadmin/ai-phase6-backups"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
EVIDENCE_DIR="${BACKUP_ROOT}/pre-certbot-install-${STAMP}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

if [[ -e "${INSTALL_DIR}" || -e "${CERTBOT_LINK}" ]]; then
    echo "ERROR|certbot_target_already_exists" >&2
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

if [[ -e /etc/letsencrypt/live/www.qskingship.com/fullchain.pem ]]; then
    echo "ERROR|certificate_already_exists" >&2
    exit 1
fi

if [[ ! -r /etc/ai-middle-office/app.env ]] \
    || ! grep -Eq '^PUBLIC_ACCESS_ENABLED=false([[:space:]]*)$' \
        /etc/ai-middle-office/app.env; then
    echo "ERROR|public_access_boundary_not_confirmed" >&2
    exit 1
fi

install -d -o root -g root -m 0700 "${EVIDENCE_DIR}"
{
    echo "timestamp_utc=${STAMP}"
    echo "certbot_version_target=${CERTBOT_VERSION}"
    echo "nginx_state=$(systemctl is-active nginx 2>/dev/null || true)"
    echo "nginx_enabled=$(systemctl is-enabled nginx 2>/dev/null || true)"
    echo "listener_443_count=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
    echo "certbot_install_dir_present=false"
    echo "certbot_link_present=false"
    echo "public_access_enabled=false"
} > "${EVIDENCE_DIR}/preflight-state.txt"
chmod 0600 "${EVIDENCE_DIR}/preflight-state.txt"

python3 -m venv "${INSTALL_DIR}"
"${INSTALL_DIR}/bin/pip" install --upgrade pip
"${INSTALL_DIR}/bin/pip" install "certbot==${CERTBOT_VERSION}"
ln -s "${INSTALL_DIR}/bin/certbot" "${CERTBOT_LINK}"

"${INSTALL_DIR}/bin/pip" freeze \
    > "${EVIDENCE_DIR}/certbot-installed-packages.txt"
chmod 0600 "${EVIDENCE_DIR}/certbot-installed-packages.txt"
sha256sum "${EVIDENCE_DIR}"/*.txt > "${EVIDENCE_DIR}/SHA256SUMS"
chmod 0600 "${EVIDENCE_DIR}/SHA256SUMS"

ACTUAL_VERSION="$("${INSTALL_DIR}/bin/certbot" --version 2>&1)"
if [[ "${ACTUAL_VERSION}" != "certbot ${CERTBOT_VERSION}" ]]; then
    echo "ERROR|unexpected_certbot_version|${ACTUAL_VERSION}" >&2
    exit 1
fi

echo "PASS|certbot_isolated_install"
echo "CERTBOT_VERSION=${ACTUAL_VERSION}"
echo "EVIDENCE_DIR=${EVIDENCE_DIR}"
echo "NGINX_ACTIVE=$(systemctl is-active nginx 2>/dev/null || true)"
echo "LISTENER_443_COUNT=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
echo "NEXT|manual_dns01_certificate_request"
