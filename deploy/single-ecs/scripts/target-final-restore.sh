#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

backup_dir=""
for arg in "$@"; do
  case "${arg}" in
    --backup-dir=*) backup_dir="${arg#*=}" ;;
    *) echo "ERROR|unknown_argument|${arg}" >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}
[[ -n "${backup_dir}" && -d "${backup_dir}" ]] || {
  echo "ERROR|valid_backup_dir_required" >&2
  exit 1
}

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly STACK_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly BACKEND_COMPOSE="${STACK_DIR}/compose.backend.yaml"
readonly DIFY_COMPOSE="${SCRIPT_DIR}/dify-compose.sh"
readonly APP_COMPOSE="/opt/ai-middle-office/app-node/compose.yaml"
readonly BACKEND_ENV="/etc/ai-middle-office/backend.env"
readonly DATA_ROOT="/data/ai-middle-office"
readonly SECRET_ROOT="/etc/ai-middle-office"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly ROLLBACK_DATA="/data/ai-middle-office-dark-before-final-${STAMP}"
readonly FAILED_DATA="/data/ai-middle-office-failed-final-${STAMP}"
readonly ROLLBACK_SECRETS="/etc/ai-middle-office-before-final-${STAMP}"

for required in "${BACKEND_COMPOSE}" "${DIFY_COMPOSE}" "${APP_COMPOSE}" \
  "${BACKEND_ENV}" "${backup_dir}/SHA256SUMS"; do
  [[ -e "${required}" ]] || {
    echo "ERROR|missing_required_path|${required}" >&2
    exit 1
  }
done

for path in "${DATA_ROOT}" "${ROLLBACK_DATA}" "${FAILED_DATA}"; do
  resolved="$(realpath -m -- "${path}")"
  [[ "${resolved}" == /data/* && "${resolved}" != /data ]] || {
    echo "ERROR|unsafe_data_path|${resolved}" >&2
    exit 1
  }
done

for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  if docker inspect "${container}" >/dev/null 2>&1 && \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" == true ]]; then
    echo "ERROR|application_must_be_stopped|${container}" >&2
    exit 1
  fi
done

(
  cd "${backup_dir}"
  sha256sum -c SHA256SUMS
)

archives=(
  mysql dify-app-storage dify-plugin-daemon dify-redis
  dify-sandbox-conf dify-sandbox-dependencies dify-postgres dify-weaviate
  n8n rag-model-cache milvus-etcd milvus milvus-minio quote-minio quote-redis
)
for name in "${archives[@]}"; do
  [[ -s "${backup_dir}/payload/${name}.tar.gz" ]] || {
    echo "ERROR|missing_archive|${name}" >&2
    exit 1
  }
done

"${DIFY_COMPOSE}" stop
docker compose --env-file "${BACKEND_ENV}" -f "${BACKEND_COMPOSE}" stop

if docker ps --format '{{.Names}}' | grep -Eq \
  '^(ai-middle-office-mysql|quote-redis|quote-minio|milvus-etcd|milvus-minio|milvus-standalone|rag-api-service|n8n|dify-)'; then
  echo "ERROR|target_dependency_still_running" >&2
  exit 1
fi

[[ -d "${DATA_ROOT}" ]] || {
  echo "ERROR|target_data_root_missing|${DATA_ROOT}" >&2
  exit 1
}
[[ ! -e "${ROLLBACK_DATA}" && ! -e "${ROLLBACK_SECRETS}" ]] || {
  echo "ERROR|rollback_path_already_exists" >&2
  exit 1
}

cp -a -- "${SECRET_ROOT}" "${ROLLBACK_SECRETS}"
chmod -R go-rwx "${ROLLBACK_SECRETS}"
mv -- "${DATA_ROOT}" "${ROLLBACK_DATA}"
install -d -m 0700 "${DATA_ROOT}"

restore_started=true
rollback_partial_restore() {
  rc=$?
  trap - ERR
  set +e
  echo "ROLLBACK|target_final_restore|begin|exit=${rc}" >&2
  if [[ "${restore_started}" == true ]]; then
    if [[ -d "${DATA_ROOT}" && ! -e "${FAILED_DATA}" ]]; then
      mv -- "${DATA_ROOT}" "${FAILED_DATA}"
    fi
    if [[ -d "${ROLLBACK_DATA}" && ! -e "${DATA_ROOT}" ]]; then
      mv -- "${ROLLBACK_DATA}" "${DATA_ROOT}"
    fi
    if [[ -d "${ROLLBACK_SECRETS}" ]]; then
      cp -a -- "${ROLLBACK_SECRETS}/." "${SECRET_ROOT}/"
    fi
  fi
  echo "ROLLBACK|target_final_restore|complete|failed_data=${FAILED_DATA}" >&2
  exit "${rc}"
}
trap rollback_partial_restore ERR

install -d -m 0750 \
  "${DATA_ROOT}/mysql" \
  "${DATA_ROOT}/dify/docker" \
  "${DATA_ROOT}/dify/docker/volumes/app/storage" \
  "${DATA_ROOT}/dify/docker/volumes/plugin_daemon" \
  "${DATA_ROOT}/dify/docker/volumes/redis/data" \
  "${DATA_ROOT}/dify/docker/volumes/sandbox/conf" \
  "${DATA_ROOT}/dify/docker/volumes/sandbox/dependencies" \
  "${DATA_ROOT}/dify/docker/volumes/db/data" \
  "${DATA_ROOT}/dify/docker/volumes/weaviate" \
  "${DATA_ROOT}/n8n" \
  "${DATA_ROOT}/rag/model-cache" \
  "${DATA_ROOT}/rag/config" \
  "${DATA_ROOT}/milvus-etcd" \
  "${DATA_ROOT}/milvus" \
  "${DATA_ROOT}/milvus-minio" \
  "${DATA_ROOT}/quote-minio" \
  "${DATA_ROOT}/quote-redis"

tar --numeric-owner --acls --xattrs -C "${DATA_ROOT}/dify/docker" \
  -xzf "${backup_dir}/config/dify-config.tar.gz"
tar --numeric-owner --acls --xattrs -C "${DATA_ROOT}/rag/config" \
  -xzf "${backup_dir}/config/rag-config.tar.gz"

restore_archive() {
  local name="$1" target="$2"
  tar --numeric-owner --acls --xattrs -C "${target}" \
    -xzf "${backup_dir}/payload/${name}.tar.gz"
}

restore_archive mysql "${DATA_ROOT}/mysql"
restore_archive dify-app-storage "${DATA_ROOT}/dify/docker/volumes/app/storage"
restore_archive dify-plugin-daemon "${DATA_ROOT}/dify/docker/volumes/plugin_daemon"
restore_archive dify-redis "${DATA_ROOT}/dify/docker/volumes/redis/data"
restore_archive dify-sandbox-conf "${DATA_ROOT}/dify/docker/volumes/sandbox/conf"
restore_archive dify-sandbox-dependencies "${DATA_ROOT}/dify/docker/volumes/sandbox/dependencies"
restore_archive dify-postgres "${DATA_ROOT}/dify/docker/volumes/db/data"
restore_archive dify-weaviate "${DATA_ROOT}/dify/docker/volumes/weaviate"
restore_archive n8n "${DATA_ROOT}/n8n"
restore_archive rag-model-cache "${DATA_ROOT}/rag/model-cache"
restore_archive milvus-etcd "${DATA_ROOT}/milvus-etcd"
restore_archive milvus "${DATA_ROOT}/milvus"
restore_archive milvus-minio "${DATA_ROOT}/milvus-minio"
restore_archive quote-minio "${DATA_ROOT}/quote-minio"
restore_archive quote-redis "${DATA_ROOT}/quote-redis"

install -m 0600 "${backup_dir}/root-only-secrets/dify.env" \
  "${DATA_ROOT}/dify/docker/.env"
install -m 0600 "${backup_dir}/root-only-secrets/rag-service.env" \
  "${SECRET_ROOT}/backend.env"
install -m 0600 "${backup_dir}/root-only-secrets/n8n.env" \
  "${SECRET_ROOT}/n8n.env"
install -m 0600 "${backup_dir}/root-only-secrets/tender_evidence_index.secret" \
  "${SECRET_ROOT}/tender_evidence_index.secret"

restore_started=false
trap - ERR
echo "RESULT|target_final_restore=passed"
echo "INFO|no_containers_started"
echo "ROLLBACK_DATA|${ROLLBACK_DATA}"
echo "ROLLBACK_SECRETS|${ROLLBACK_SECRETS}"
echo "INFO|next_gate=n8n_offline_rewrite_and_target_start"
