#!/usr/bin/env bash
set -Eeuo pipefail

apply=false
backup_dir=""

for arg in "$@"; do
  case "${arg}" in
    --apply) apply=true ;;
    --backup-dir=*) backup_dir="${arg#*=}" ;;
    *) echo "ERROR|unknown_argument|${arg}" >&2; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR|run_as_root" >&2
  exit 1
fi

if [[ -z "${backup_dir}" || ! -d "${backup_dir}" ]]; then
  echo "ERROR|valid_--backup-dir_is_required" >&2
  exit 1
fi

readonly DATA_ROOT="${AI_DATA_ROOT:-/data/ai-middle-office}"
readonly SECRET_ROOT="/etc/ai-middle-office"

if [[ ! -f "${backup_dir}/SHA256SUMS" ]]; then
  echo "ERROR|missing_sha256_manifest" >&2
  exit 1
fi

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
  if [[ ! -f "${backup_dir}/payload/${name}.tar.gz" ]]; then
    echo "ERROR|missing_archive|${name}" >&2
    exit 1
  fi
done

if docker ps -a --format '{{.Names}}' | grep -Eq \
  '^(ai-middle-office-mysql|quote-redis|quote-minio|milvus-etcd|milvus-minio|milvus-standalone|rag-api-service|n8n|dify-)'; then
  echo "ERROR|target_backend_containers_already_exist" >&2
  exit 1
fi

targets=(
  "${DATA_ROOT}/mysql"
  "${DATA_ROOT}/dify/docker"
  "${DATA_ROOT}/n8n"
  "${DATA_ROOT}/rag/model-cache"
  "${DATA_ROOT}/milvus-etcd"
  "${DATA_ROOT}/milvus"
  "${DATA_ROOT}/milvus-minio"
  "${DATA_ROOT}/quote-minio"
  "${DATA_ROOT}/quote-redis"
)
for target in "${targets[@]}"; do
  if [[ -d "${target}" && -n "$(find "${target}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "ERROR|target_not_empty|${target}" >&2
    exit 1
  fi
done

echo "RESULT|target_restore_preflight=passed"
echo "INFO|data_root=${DATA_ROOT}"
if [[ "${apply}" != true ]]; then
  echo "INFO|no_data_written|rerun_with_--apply_after_approval"
  exit 0
fi

install -d -m 0700 "${DATA_ROOT}" "${SECRET_ROOT}"
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

install -m 0600 "${backup_dir}/root-only-secrets/dify.env" "${DATA_ROOT}/dify/docker/.env"
install -m 0600 "${backup_dir}/root-only-secrets/rag-service.env" "${SECRET_ROOT}/backend.env"
install -m 0600 "${backup_dir}/root-only-secrets/n8n.env" "${SECRET_ROOT}/n8n.env"
install -m 0600 "${backup_dir}/root-only-secrets/tender_evidence_index.secret" \
  "${SECRET_ROOT}/tender_evidence_index.secret"

echo "RESULT|target_restore=passed"
echo "INFO|no_containers_started"
echo "INFO|next_gate=image_load_and_compose_config_quiet"
