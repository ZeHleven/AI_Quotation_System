#!/usr/bin/env bash
set -euo pipefail

DOMAIN="www.qskingship.com"
LIVE_DIR="/etc/letsencrypt/live/${DOMAIN}"
CERT_FILE="${LIVE_DIR}/cert.pem"
CHAIN_FILE="${LIVE_DIR}/chain.pem"
FULLCHAIN_FILE="${LIVE_DIR}/fullchain.pem"
KEY_FILE="${LIVE_DIR}/privkey.pem"
STAGING_DIR="/home/aiadmin/ai-middle-office-phase6"
PREPARE_SCRIPT="${STAGING_DIR}/phase6-free-https-prepare.sh"
SOURCE_CONFIG="${STAGING_DIR}/ai-middle-office-https.conf"
PENDING_CONFIG="/etc/nginx/conf.d/ai-middle-office.conf.pending"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

for required_file in \
    "${CERT_FILE}" \
    "${CHAIN_FILE}" \
    "${FULLCHAIN_FILE}" \
    "${KEY_FILE}" \
    "${PREPARE_SCRIPT}" \
    "${SOURCE_CONFIG}"; do
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

if ! firewall-cmd --zone=public --query-service=https >/dev/null; then
    echo "ERROR|https_runtime_firewall_rule_missing" >&2
    exit 1
fi

if ! firewall-cmd --permanent --zone=public --query-service=https >/dev/null; then
    echo "ERROR|https_permanent_firewall_rule_missing" >&2
    exit 1
fi

if ! openssl x509 -in "${CERT_FILE}" -noout -checkend 2592000; then
    echo "ERROR|certificate_expires_within_30_days" >&2
    exit 1
fi

if ! openssl x509 -in "${CERT_FILE}" -noout -ext subjectAltName \
    | tr ',' '\n' \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
    | grep -Fxq "DNS:${DOMAIN}"; then
    echo "ERROR|certificate_san_mismatch" >&2
    exit 1
fi

if ! openssl verify -untrusted "${CHAIN_FILE}" "${CERT_FILE}" \
    | grep -Fq ': OK'; then
    echo "ERROR|certificate_chain_verification_failed" >&2
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

echo "PASS|certificate_files_and_private_key_match"
openssl x509 -in "${CERT_FILE}" -noout -subject -issuer -dates -fingerprint -sha256
echo "PASS|https_firewall_runtime_and_permanent"
echo "PASS|public_access_false"

"${PREPARE_SCRIPT}" "${SOURCE_CONFIG}"

if [[ ! -f "${PENDING_CONFIG}" ]]; then
    echo "ERROR|pending_config_not_installed" >&2
    exit 1
fi

if [[ "$(sha256sum "${SOURCE_CONFIG}" | awk '{print $1}')" \
    != "$(sha256sum "${PENDING_CONFIG}" | awk '{print $1}')" ]]; then
    echo "ERROR|pending_config_hash_mismatch" >&2
    exit 1
fi

echo "PASS|certificate_and_pending_https_config"
echo "NGINX_ACTIVE=$(systemctl is-active nginx 2>/dev/null || true)"
echo "LISTENER_443_COUNT=$(ss -lntH 'sport = :443' 2>/dev/null | wc -l)"
echo "NEXT|offline_nginx_config_activation_and_test"
