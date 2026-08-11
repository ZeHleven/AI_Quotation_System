#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR|run_as_root" >&2
  exit 1
fi

readonly DIFY_ROOT="${AI_DIFY_ROOT:-/data/ai-middle-office/dify/docker}"
readonly BASE_COMPOSE="${DIFY_ROOT}/docker-compose.yaml"
readonly SOURCE_ENV="${DIFY_ROOT}/.env"
readonly OVERRIDE_COMPOSE="${AI_DIFY_OVERRIDE:-/opt/ai-middle-office/single-ecs/compose.dify.override.yaml}"
readonly RUNTIME_DIR="/run/ai-middle-office"
readonly SAFE_BASE_COMPOSE="${RUNTIME_DIR}/dify-compose.loopback.yaml"

for required in "${BASE_COMPOSE}" "${SOURCE_ENV}" "${OVERRIDE_COMPOSE}"; do
  if [[ ! -f "${required}" ]]; then
    echo "ERROR|missing_file|${required}" >&2
    exit 1
  fi
done

env_mode="$(stat -c '%a' "${SOURCE_ENV}")"
if (( (8#${env_mode} & 8#077) != 0 )); then
  echo "ERROR|dify_env_permissions_must_be_root_only|mode=${env_mode}" >&2
  exit 1
fi

# The upstream file reuses these variables both in host port mappings and as
# numeric application settings. Keep them numeric, and create a root-only
# runtime copy whose three host mappings explicitly bind to loopback. This
# avoids expanding or persisting the secret-bearing Compose configuration.
install -d -m 0700 "${RUNTIME_DIR}"
sed \
  -e 's|"${EXPOSE_NGINX_PORT:-80}:${NGINX_PORT:-80}"|"127.0.0.1:${EXPOSE_NGINX_PORT:-80}:${NGINX_PORT:-80}"|' \
  -e 's|"${EXPOSE_NGINX_SSL_PORT:-443}:${NGINX_SSL_PORT:-443}"|"127.0.0.1:${EXPOSE_NGINX_SSL_PORT:-443}:${NGINX_SSL_PORT:-443}"|' \
  -e 's|"${EXPOSE_PLUGIN_DEBUGGING_PORT:-5003}:${PLUGIN_DEBUGGING_PORT:-5003}"|"127.0.0.1:${EXPOSE_PLUGIN_DEBUGGING_PORT:-5003}:${PLUGIN_DEBUGGING_PORT:-5003}"|' \
  "${BASE_COMPOSE}" > "${SAFE_BASE_COMPOSE}"
chmod 0600 "${SAFE_BASE_COMPOSE}"

for expected in \
  '127.0.0.1:${EXPOSE_NGINX_PORT:-80}:${NGINX_PORT:-80}' \
  '127.0.0.1:${EXPOSE_NGINX_SSL_PORT:-443}:${NGINX_SSL_PORT:-443}' \
  '127.0.0.1:${EXPOSE_PLUGIN_DEBUGGING_PORT:-5003}:${PLUGIN_DEBUGGING_PORT:-5003}'; do
  if [[ "$(grep -Fxc -- "      - \"${expected}\"" "${SAFE_BASE_COMPOSE}")" -ne 1 ]]; then
    echo "ERROR|unexpected_dify_compose_port_shape|${expected}" >&2
    exit 1
  fi
done

export EXPOSE_NGINX_PORT="18080"
export EXPOSE_NGINX_SSL_PORT="18443"
export EXPOSE_PLUGIN_DEBUGGING_PORT="5003"
export NGINX_HTTPS_ENABLED="false"
export MIGRATION_ENABLED="false"

exec docker compose \
  --project-name dify \
  --project-directory "${DIFY_ROOT}" \
  --env-file "${SOURCE_ENV}" \
  -f "${SAFE_BASE_COMPOSE}" \
  -f "${OVERRIDE_COMPOSE}" \
  --profile postgresql \
  --profile weaviate \
  "$@"
