#!/usr/bin/env bash
set -u

printf 'IDENTITY|'
id -un

printf 'OS|'
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    printf '%s %s\n' "${ID:-unknown}" "${VERSION_ID:-unknown}"
else
    echo unknown
fi

printf 'NGINX_ACTIVE|'
systemctl is-active nginx 2>/dev/null || true
printf 'NGINX_ENABLED|'
systemctl is-enabled nginx 2>/dev/null || true
printf 'NGINX_BINARY|'
command -v nginx || true
printf 'CERTBOT_BINARY|'
command -v certbot || true
printf 'ACME_SH_BINARY|'
command -v acme.sh || true

printf 'CERT_PATH|'
if [[ -e /etc/letsencrypt/live/www.qskingship.com/fullchain.pem ]]; then
    echo present
else
    echo absent
fi

printf 'PENDING_CONFIG|'
if [[ -e /etc/nginx/conf.d/ai-middle-office.conf.pending ]]; then
    echo present
else
    echo absent
fi

printf 'ACTIVE_CONFIG|'
if [[ -e /etc/nginx/conf.d/ai-middle-office.conf ]]; then
    echo present
else
    echo absent
fi

printf 'LISTENER_443|'
ss -lntH 'sport = :443' 2>/dev/null | wc -l
printf 'LOCAL_API|'
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 5 \
    http://127.0.0.1:9000/health 2>/dev/null || true
printf 'SUDO_NONINTERACTIVE|'
if sudo -n true >/dev/null 2>&1; then
    echo available
else
    echo unavailable
fi
