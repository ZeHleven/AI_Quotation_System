#!/usr/bin/env bash
set -Eeuo pipefail

readonly DOMAIN="www.qskingship.com"
readonly ACTIVE_CONFIG="/etc/nginx/conf.d/ai-middle-office.conf"
readonly COMPOSE_FILE="/opt/ai-middle-office/app-node/compose.yaml"
readonly APP_ENV_FILE="/etc/ai-middle-office/app.env"
readonly BACKUP_ROOT="/root/ai-middle-office-performance-backups"

usage() {
    cat >&2 <<'EOF'
Usage:
  sudo bash phase8-free-performance-activate.sh \
    CONFIG CONFIG_SHA256 IMAGE_TAR IMAGE_SHA256 IMAGE_TAG

The script is offline-only: it never builds, pulls, or downloads an image.
EOF
    exit 2
}

if [[ $# -ne 5 ]]; then
    usage
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

readonly SOURCE_CONFIG="$1"
readonly EXPECTED_CONFIG_SHA256="$2"
readonly IMAGE_ARCHIVE="$3"
readonly EXPECTED_IMAGE_SHA256="$4"
readonly IMAGE_TAG="$5"
readonly IMAGE_REF="ai-middle-office-app:${IMAGE_TAG}"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly BACKUP_DIR="${BACKUP_ROOT}/${STAMP}"
readonly REPORT="${BACKUP_DIR}/activation-report.txt"

if [[ ! "${EXPECTED_CONFIG_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
    || [[ ! "${EXPECTED_IMAGE_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
    || [[ ! "${IMAGE_TAG}" =~ ^[0-9]{8}_[0-9]{6}(_perf[0-9]+)?$ ]]; then
    echo "ERROR|invalid_integrity_or_tag_argument" >&2
    exit 1
fi

umask 077
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
exec > >(tee "${REPORT}") 2>&1

for required_file in \
    "${SOURCE_CONFIG}" \
    "${IMAGE_ARCHIVE}" \
    "${ACTIVE_CONFIG}" \
    "${COMPOSE_FILE}" \
    "${APP_ENV_FILE}"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_file_not_readable|${required_file}" >&2
        exit 1
    fi
done

if ! grep -Eq '^PUBLIC_ACCESS_ENABLED=false([[:space:]]*)$' "${APP_ENV_FILE}"; then
    echo "ERROR|public_access_boundary_not_confirmed" >&2
    exit 1
fi

if [[ "$(sha256sum "${SOURCE_CONFIG}" | awk '{print $1}')" != "${EXPECTED_CONFIG_SHA256}" ]]; then
    echo "ERROR|nginx_source_hash_mismatch" >&2
    exit 1
fi

if [[ "$(sha256sum "${IMAGE_ARCHIVE}" | awk '{print $1}')" != "${EXPECTED_IMAGE_SHA256}" ]]; then
    echo "ERROR|image_archive_hash_mismatch" >&2
    exit 1
fi

for required_marker in \
    'listen 443 ssl default_server' \
    'return 444' \
    'gzip on' \
    'immutable' \
    'add_header Strict-Transport-Security' \
    'add_header Content-Security-Policy' \
    'location = /docs' \
    'location = /openapi.json' \
    'location = /api/v1/auth/login' \
    'limit_req zone=ai_login' \
    'limit_req zone=ai_general'; do
    if ! grep -Fq "${required_marker}" "${SOURCE_CONFIG}"; then
        echo "ERROR|required_nginx_contract_missing|${required_marker}" >&2
        exit 1
    fi
done

if grep -Eiq '^[[:space:]]*brotli' "${SOURCE_CONFIG}"; then
    echo "ERROR|brotli_not_available_on_this_host" >&2
    exit 1
fi

NGINX_BUILD="$(timeout 5s nginx -V 2>&1 || true)"
if grep -Fq -- '--without-http_gzip_module' <<<"${NGINX_BUILD}"; then
    echo "ERROR|nginx_gzip_module_missing" >&2
    exit 1
fi

API_CONTAINER_ID="$(docker compose -f "${COMPOSE_FILE}" ps -q api)"
WORKER_CONTAINER_ID="$(docker compose --profile worker -f "${COMPOSE_FILE}" ps -q worker)"
if [[ -z "${API_CONTAINER_ID}" || -z "${WORKER_CONTAINER_ID}" ]]; then
    echo "ERROR|api_or_worker_container_missing" >&2
    exit 1
fi

OLD_API_IMAGE="$(docker inspect --format '{{.Config.Image}}' "${API_CONTAINER_ID}")"
OLD_WORKER_IMAGE="$(docker inspect --format '{{.Config.Image}}' "${WORKER_CONTAINER_ID}")"
if [[ "${OLD_API_IMAGE}" != "${OLD_WORKER_IMAGE}" ]] \
    || [[ ! "${OLD_API_IMAGE}" =~ ^ai-middle-office-app:[A-Za-z0-9_.-]+$ ]]; then
    echo "ERROR|current_runtime_image_mismatch" >&2
    exit 1
fi
readonly OLD_IMAGE_TAG="${OLD_API_IMAGE#ai-middle-office-app:}"

cp -a "${ACTIVE_CONFIG}" "${BACKUP_DIR}/ai-middle-office.conf"
cp -a "${COMPOSE_FILE}" "${BACKUP_DIR}/compose.yaml"
{
    echo "timestamp_utc=${STAMP}"
    echo "old_image=${OLD_API_IMAGE}"
    echo "new_image=${IMAGE_REF}"
    echo "nginx_state=$(systemctl is-active nginx 2>/dev/null || true)"
    echo "nginx_enabled=$(systemctl is-enabled nginx 2>/dev/null || true)"
    echo "public_access_enabled=false"
    echo "source_config_sha256=${EXPECTED_CONFIG_SHA256}"
    echo "image_archive_sha256=${EXPECTED_IMAGE_SHA256}"
} > "${BACKUP_DIR}/preflight-state.txt"
chmod 0600 "${BACKUP_DIR}/preflight-state.txt"

CONFIG_CHANGED=0
RUNTIME_CHANGED=0

rollback() {
    local rc=$?
    trap - ERR
    set +e
    echo "ROLLBACK|begin|exit_${rc}" >&2
    if [[ "${RUNTIME_CHANGED}" -eq 1 ]]; then
        AI_APP_IMAGE_TAG="${OLD_IMAGE_TAG}" \
            docker compose --profile worker -f "${COMPOSE_FILE}" \
            up -d --no-build api worker
        for _rollback_attempt in $(seq 1 36); do
            if curl -fsS --noproxy '*' --max-time 10 \
                http://127.0.0.1:9000/health/ready 2>/dev/null \
                | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"'; then
                break
            fi
            sleep 5
        done
    fi
    if [[ "${CONFIG_CHANGED}" -eq 1 ]]; then
        install -o root -g root -m 0644 \
            "${BACKUP_DIR}/ai-middle-office.conf" "${ACTIVE_CONFIG}"
        if nginx -t; then
            systemctl reload nginx
        else
            echo "ROLLBACK|restored_config_failed_nginx_test" >&2
        fi
    fi
    echo "ROLLBACK|complete|backup_${BACKUP_DIR}" >&2
    exit "${rc}"
}
trap rollback ERR

docker load --input "${IMAGE_ARCHIVE}"
if ! docker image inspect "${IMAGE_REF}" >/dev/null 2>&1; then
    echo "ERROR|loaded_image_tag_missing|${IMAGE_REF}" >&2
    false
fi

install -o root -g root -m 0644 "${SOURCE_CONFIG}" "${ACTIVE_CONFIG}"
CONFIG_CHANGED=1
nginx -t
systemctl reload nginx

# Validate the candidate edge policy against the still-running old application
# before recreating API/Worker. A cache/header failure therefore rolls back only
# Nginx and does not cause an unnecessary application restart.
PREFLIGHT_LOGIN_HEADERS="${BACKUP_DIR}/preflight-login-headers.txt"
PREFLIGHT_LOGIN_CODE=""
PREFLIGHT_READY=0
for _nginx_preflight_attempt in $(seq 1 20); do
    PREFLIGHT_LOGIN_CODE="$(curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
        -D "${PREFLIGHT_LOGIN_HEADERS}" -o /dev/null -w '%{http_code}' \
        --max-time 20 "https://${DOMAIN}/login" || true)"
    if [[ "${PREFLIGHT_LOGIN_CODE}" == "200" ]] \
        && grep -Eiq '^cache-control:[[:space:]]*no-store' "${PREFLIGHT_LOGIN_HEADERS}"; then
        PREFLIGHT_READY=1
        break
    fi
    sleep 1
done
if [[ "${PREFLIGHT_READY}" -ne 1 ]]; then
    echo "ERROR|nginx_preflight_login_no_store_failed|http_${PREFLIGHT_LOGIN_CODE}" >&2
    false
fi
for required_header in \
    strict-transport-security \
    x-content-type-options \
    x-frame-options \
    referrer-policy \
    permissions-policy \
    content-security-policy; do
    if ! grep -Eiq "^${required_header}:" "${PREFLIGHT_LOGIN_HEADERS}"; then
        echo "ERROR|nginx_preflight_security_header_missing|${required_header}" >&2
        false
    fi
done

RUNTIME_CHANGED=1
AI_APP_IMAGE_TAG="${IMAGE_TAG}" \
    docker compose --profile worker -f "${COMPOSE_FILE}" \
    up -d --no-build api worker

READY=0
for _attempt in $(seq 1 60); do
    READY_BODY="$(curl -fsS --noproxy '*' --max-time 10 \
        http://127.0.0.1:9000/health/ready 2>/dev/null || true)"
    if grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"' <<<"${READY_BODY}"; then
        READY=1
        break
    fi
    sleep 5
done
if [[ "${READY}" -ne 1 ]]; then
    echo "ERROR|new_application_readiness_timeout" >&2
    false
fi

NEW_API_CONTAINER_ID="$(docker compose -f "${COMPOSE_FILE}" ps -q api)"
NEW_WORKER_CONTAINER_ID="$(docker compose --profile worker -f "${COMPOSE_FILE}" ps -q worker)"
for container_id in "${NEW_API_CONTAINER_ID}" "${NEW_WORKER_CONTAINER_ID}"; do
    if [[ -z "${container_id}" ]] \
        || [[ "$(docker inspect --format '{{.State.Running}}' "${container_id}")" != "true" ]] \
        || [[ "$(docker inspect --format '{{.Config.Image}}' "${container_id}")" != "${IMAGE_REF}" ]]; then
        echo "ERROR|new_runtime_container_gate_failed" >&2
        false
    fi
done

# The candidate configuration was already reloaded and positively identified
# by the preflight before the runtime switch; do not start another graceful
# worker transition immediately before the final response-header gates.
nginx -t

LOGIN_HEADERS="${BACKUP_DIR}/login-headers.txt"
LOGIN_CODE="$(curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
    -D "${LOGIN_HEADERS}" -o "${BACKUP_DIR}/login.html" -w '%{http_code}' \
    --max-time 20 "https://${DOMAIN}/login" || true)"
if [[ "${LOGIN_CODE}" != "200" ]] \
    || ! grep -Eiq '^cache-control:[[:space:]]*no-store' "${LOGIN_HEADERS}"; then
    echo "ERROR|login_no_store_gate_failed|http_${LOGIN_CODE}" >&2
    false
fi

for required_header in \
    strict-transport-security \
    x-content-type-options \
    x-frame-options \
    referrer-policy \
    permissions-policy \
    content-security-policy; do
    if ! grep -Eiq "^${required_header}:" "${LOGIN_HEADERS}"; then
        echo "ERROR|security_header_missing|${required_header}" >&2
        false
    fi
done

ENTRY_ASSET="$(grep -Eo '/assets/index-[A-Za-z0-9_-]+\.js' \
    "${BACKUP_DIR}/login.html" | head -n 1)"
if [[ -z "${ENTRY_ASSET}" ]]; then
    echo "ERROR|hashed_entry_asset_not_found" >&2
    false
fi

ASSET_HEADERS="${BACKUP_DIR}/asset-headers.txt"
ASSET_CODE="$(curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
    -H 'Accept-Encoding: gzip' -D "${ASSET_HEADERS}" -o /dev/null \
    -w '%{http_code}' --max-time 20 "https://${DOMAIN}${ENTRY_ASSET}" || true)"
if [[ "${ASSET_CODE}" != "200" ]] \
    || ! grep -Eiq '^content-encoding:[[:space:]]*gzip' "${ASSET_HEADERS}" \
    || ! grep -Eiq '^cache-control:.*max-age=31536000.*immutable' "${ASSET_HEADERS}"; then
    echo "ERROR|asset_gzip_or_immutable_gate_failed|http_${ASSET_CODE}" >&2
    false
fi

for blocked_path in \
    /docs \
    /redoc \
    /openapi.json \
    /api/v1/admin/codex-worker/ \
    /api/v1/admin/dwg-quantity-trial/; do
    STATUS_CODE="$(curl -sS --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" \
        -o /dev/null -w '%{http_code}' --max-time 15 \
        "https://${DOMAIN}${blocked_path}" || true)"
    if [[ "${STATUS_CODE}" != "404" ]]; then
        echo "ERROR|blocked_path_gate_failed|${blocked_path}|http_${STATUS_CODE}" >&2
        false
    fi
done

find "${BACKUP_DIR}" -maxdepth 1 -type f \
    ! -name SHA256SUMS \
    ! -name activation-report.txt \
    -print0 \
    | sort -z \
    | xargs -0 sha256sum > "${BACKUP_DIR}/SHA256SUMS"
chmod 0600 "${BACKUP_DIR}"/*
trap - ERR

echo "PASS|offline_image_loaded|${IMAGE_REF}"
echo "PASS|nginx_cache_security_preflight|attempt_${_nginx_preflight_attempt}"
echo "PASS|application_ready"
echo "PASS|nginx_reloaded"
echo "PASS|login_no_store_and_security_headers"
echo "PASS|hashed_asset_gzip_and_immutable"
echo "PASS|sensitive_routes_hidden"
echo "BACKUP_DIR=${BACKUP_DIR}"
echo "RESULT=PASS"
