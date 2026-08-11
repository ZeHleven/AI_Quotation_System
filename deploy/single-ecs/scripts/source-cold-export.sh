#!/usr/bin/env bash
set -Eeuo pipefail

readonly RAG_ROOT="${RAG_ROOT:-/opt/rag_service}"
readonly DIFY_ROOT="${DIFY_ROOT:-/opt/dify/docker}"
readonly RAGFLOW_ROOT="${RAGFLOW_ROOT:-/opt/ragflow/docker}"
readonly N8N_ROOT="${N8N_ROOT:-/root/.n8n}"
readonly MYSQL_ROOT="${MYSQL_ROOT:-/var/lib/docker/volumes/ragflow_mysql_data/_data}"
readonly BACKUP_ROOT="${BACKUP_ROOT:-/opt/rag_service/backups/single-ecs-migration}"

create_backup=false
include_images=false
leave_stopped=false

for arg in "$@"; do
  case "${arg}" in
    --create-backup) create_backup=true ;;
    --include-images) include_images=true ;;
    --leave-stopped) leave_stopped=true ;;
    *) echo "ERROR|unknown_argument|${arg}" >&2; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR|run_as_root" >&2
  exit 1
fi

required_paths=(
  "${RAG_ROOT}/docker-compose.yml"
  "${RAG_ROOT}/tender_evidence_index.secret"
  "${RAG_ROOT}/model_cache"
  "${RAG_ROOT}/volumes"
  "${DIFY_ROOT}/docker-compose.yaml"
  "${DIFY_ROOT}/.env"
  "${DIFY_ROOT}/volumes"
  "${N8N_ROOT}"
  "${MYSQL_ROOT}"
)

for path in "${required_paths[@]}"; do
  if [[ ! -e "${path}" ]]; then
    echo "ERROR|missing_source|${path}" >&2
    exit 1
  fi
done

required_containers=(
  rag-api-service milvus-standalone milvus-minio quote-redis quote-minio milvus-etcd
  ragflow-mysql-1 dify-nginx-1 dify-api-1 dify-worker-1 dify-worker_beat-1
  dify-plugin_daemon-1 dify-ssrf_proxy-1 dify-web-1 dify-redis-1
  dify-sandbox-1 dify-db_postgres-1 dify-weaviate-1 n8n
)

for container in "${required_containers[@]}"; do
  if ! docker inspect "${container}" >/dev/null 2>&1; then
    echo "ERROR|missing_container|${container}" >&2
    exit 1
  fi
done

dify_containers=(
  dify-nginx-1 dify-api-1 dify-worker-1 dify-worker_beat-1
  dify-plugin_daemon-1 dify-ssrf_proxy-1 dify-web-1 dify-redis-1
  dify-sandbox-1 dify-db_postgres-1 dify-weaviate-1
)
for container in "${dify_containers[@]}"; do
  compose_project="$(docker inspect "${container}" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
  if [[ "${compose_project}" != dify ]]; then
    echo "ERROR|unexpected_dify_compose_project|container=${container}; project=${compose_project}" >&2
    exit 1
  fi
done

unexpected_dify="$(docker ps -aq \
  --filter label=com.docker.compose.project=docker)"
if [[ -n "${unexpected_dify}" ]]; then
  echo "ERROR|unexpected_dify_project_containers_present|project=docker" >&2
  exit 1
fi

available_kib="$(df -Pk "${BACKUP_ROOT%/*}" | awk 'NR==2 {print $4}')"
if (( available_kib < 12 * 1024 * 1024 )); then
  echo "ERROR|insufficient_backup_space_kib|${available_kib}" >&2
  exit 1
fi

echo "PASS|source_preflight|backup_root=${BACKUP_ROOT}; available_kib=${available_kib}"
echo "INFO|estimated_business_state|approximately_6_gib"

if [[ "${create_backup}" != true ]]; then
  echo "RESULT|source_cold_export_preflight=passed"
  echo "INFO|no_services_stopped|rerun_with_--create-backup_in_approved_maintenance_window"
  exit 0
fi

stamp="$(date -u +%Y%m%d_%H%M%S)"
backup_dir="${BACKUP_ROOT}/${stamp}"
payload_dir="${backup_dir}/payload"
config_dir="${backup_dir}/config"
secret_dir="${backup_dir}/root-only-secrets"
mkdir -p "${payload_dir}" "${config_dir}" "${secret_dir}"
chmod 0700 "${backup_dir}" "${secret_dir}"

docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' > "${config_dir}/containers.txt"
docker inspect "${required_containers[@]}" \
  --format '{{.Name}}|{{.Config.Image}}|{{.Image}}|{{.HostConfig.RestartPolicy.Name}}' \
  > "${config_dir}/images-and-restart.txt"
docker inspect n8n \
  --format 'image={{.Config.Image}} image_id={{.Image}} cmd={{json .Config.Cmd}} entrypoint={{json .Config.Entrypoint}} restart={{.HostConfig.RestartPolicy.Name}} network={{.HostConfig.NetworkMode}} ports={{json .HostConfig.PortBindings}}' \
  > "${config_dir}/n8n-runtime.txt"

install -m 0600 "${DIFY_ROOT}/.env" "${secret_dir}/dify.env"
if [[ -f "${RAG_ROOT}/.env" ]]; then
  install -m 0600 "${RAG_ROOT}/.env" "${secret_dir}/rag-service.env"
else
  echo "ERROR|missing_secret_file|${RAG_ROOT}/.env" >&2
  exit 1
fi
if [[ -f "${RAGFLOW_ROOT}/.env" ]]; then
  install -m 0600 "${RAGFLOW_ROOT}/.env" "${secret_dir}/ragflow.env"
fi
install -m 0600 "${RAG_ROOT}/tender_evidence_index.secret" "${secret_dir}/tender_evidence_index.secret"
docker inspect n8n --format '{{range .Config.Env}}{{println .}}{{end}}' > "${secret_dir}/n8n.env"
chmod 0600 "${secret_dir}/n8n.env"

tar --numeric-owner --acls --xattrs \
  --exclude='./volumes' --exclude='./.env' \
  -C "${DIFY_ROOT}" -czf "${config_dir}/dify-config.tar.gz" .

rag_config_files=()
for name in docker-compose.yml Dockerfile Dockerfile.tender-overlay hybrid_searcher.py rag_materials.json reload_auth.py rag_api_service.py tender_evidence_search.py; do
  [[ -e "${RAG_ROOT}/${name}" ]] && rag_config_files+=("${name}")
done
tar --numeric-owner --acls --xattrs -C "${RAG_ROOT}" \
  -czf "${config_dir}/rag-config.tar.gz" "${rag_config_files[@]}"

source_stopped=false
restart_source() {
  rc=$?
  trap - EXIT
  if [[ "${source_stopped}" == true ]]; then
    echo "INFO|restarting_source_services"
    docker start ragflow-mysql-1 >/dev/null 2>&1 || true
    docker compose -f "${RAG_ROOT}/docker-compose.yml" up -d >/dev/null 2>&1 || true
    docker compose --project-name dify --project-directory "${DIFY_ROOT}" \
      --env-file "${DIFY_ROOT}/.env" -f "${DIFY_ROOT}/docker-compose.yaml" \
      --profile postgresql --profile weaviate up -d >/dev/null 2>&1 || true
    docker start n8n >/dev/null 2>&1 || true
  fi
  exit "${rc}"
}
trap restart_source EXIT

source_stopped=true
docker stop n8n >/dev/null
docker compose --project-name dify --project-directory "${DIFY_ROOT}" \
  --env-file "${DIFY_ROOT}/.env" -f "${DIFY_ROOT}/docker-compose.yaml" \
  --profile postgresql --profile weaviate stop >/dev/null
docker compose -f "${RAG_ROOT}/docker-compose.yml" stop >/dev/null
docker stop ragflow-mysql-1 >/dev/null

archive_dir() {
  local label="$1" source="$2"
  tar --numeric-owner --acls --xattrs --one-file-system \
    -C "${source}" -czf "${payload_dir}/${label}.tar.gz" .
}

archive_dir mysql "${MYSQL_ROOT}"
archive_dir dify-app-storage "${DIFY_ROOT}/volumes/app/storage"
archive_dir dify-plugin-daemon "${DIFY_ROOT}/volumes/plugin_daemon"
archive_dir dify-redis "${DIFY_ROOT}/volumes/redis/data"
archive_dir dify-sandbox-conf "${DIFY_ROOT}/volumes/sandbox/conf"
archive_dir dify-sandbox-dependencies "${DIFY_ROOT}/volumes/sandbox/dependencies"
archive_dir dify-postgres "${DIFY_ROOT}/volumes/db/data"
archive_dir dify-weaviate "${DIFY_ROOT}/volumes/weaviate"
archive_dir n8n "${N8N_ROOT}"
archive_dir rag-model-cache "${RAG_ROOT}/model_cache"
archive_dir milvus-etcd "${RAG_ROOT}/volumes/etcd"
archive_dir milvus "${RAG_ROOT}/volumes/milvus"
archive_dir milvus-minio "${RAG_ROOT}/volumes/minio"
archive_dir quote-minio "${RAG_ROOT}/volumes/quote-minio"
archive_dir quote-redis "${RAG_ROOT}/volumes/redis"

if [[ "${leave_stopped}" == true ]]; then
  source_stopped=false
  echo "INFO|source_services_left_stopped_for_final_cutover"
else
  docker start ragflow-mysql-1 >/dev/null
  docker compose -f "${RAG_ROOT}/docker-compose.yml" up -d >/dev/null
  docker compose --project-name dify --project-directory "${DIFY_ROOT}" \
    --env-file "${DIFY_ROOT}/.env" -f "${DIFY_ROOT}/docker-compose.yaml" \
    --profile postgresql --profile weaviate up -d >/dev/null
  docker start n8n >/dev/null
  source_stopped=false
  echo "INFO|source_services_restarted_before_image_export"
fi

# Images are immutable inputs, so export them only after all source services
# have been restarted. This keeps the maintenance window limited to the cold
# data archives instead of extending it through a potentially slow image save.
if [[ "${include_images}" == true ]]; then
  images=(
    rag_service-rag-service-tender:phase3c
    milvusdb/milvus:v2.3.1
    minio/minio:RELEASE.2023-03-20T20-16-18Z
    redis:7.2-alpine
    quay.io/coreos/etcd:v3.5.5
    mysql:8.0.39
    busybox:latest
    nginx:latest
    langgenius/dify-api:1.13.2
    langgenius/dify-plugin-daemon:0.5.4-local
    ubuntu/squid:latest
    langgenius/dify-web:1.13.2
    redis:6-alpine
    langgenius/dify-sandbox:0.2.12
    postgres:15-alpine
    semitechnologies/weaviate:1.27.0
    docker.n8n.io/n8nio/n8n
  )
  : > "${config_dir}/image-lock-runtime.txt"
  for image in "${images[@]}"; do
    image_id="$(docker image inspect "${image}" --format '{{.Id}}')"
    printf '%s|%s\n' "${image}" "${image_id}" >> "${config_dir}/image-lock-runtime.txt"
  done
  docker save "${images[@]}" | gzip -1 > "${payload_dir}/docker-images.tar.gz"
fi

(
  cd "${backup_dir}"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum
) > "${backup_dir}/SHA256SUMS"
chmod -R go-rwx "${backup_dir}"

trap - EXIT
echo "RESULT|source_cold_export=passed"
echo "BACKUP_DIR|${backup_dir}"
if [[ "${leave_stopped}" == true ]]; then
  echo "RESULT|source_final_freeze=passed"
  echo "INFO|source_write_services_remain_stopped"
fi
echo "WARNING|root-only-secrets_are_present; transfer only over approved SSH and never attach or commit"
