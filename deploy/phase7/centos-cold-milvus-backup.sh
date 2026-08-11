#!/usr/bin/env bash
set -Eeuo pipefail

readonly RAG_DIR="/opt/rag_service"
readonly RUNTIME_ENV="${RAG_DIR}/.env"
readonly BACKUP_SCRIPT="${RAG_DIR}/backup_production.sh"
readonly EXPECTED_BACKUP_SCRIPT_SHA256="bec0ce47db8971c0077aa65f772bbefaf592c6f1c255037e8089d9e7fc39e2fd"
readonly BACKUP_ROOT="${RAG_DIR}/backups"
readonly COMPOSE_FILE="${RAG_DIR}/docker-compose.yml"
readonly RUN_ID="${PHASE7_RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
readonly RUN_LOG="${BACKUP_ROOT}/phase7-cold-backup-${RUN_ID}.run.log"
readonly REPORT="${BACKUP_ROOT}/phase7-cold-backup-${RUN_ID}.report"
readonly STATUS_FILE="${BACKUP_ROOT}/phase7-cold-backup-${RUN_ID}.status"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi
if [[ ! "${RUN_ID}" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
    echo "ERROR|invalid_run_id" >&2
    exit 1
fi

umask 077
mkdir -p "${BACKUP_ROOT}"
touch "${REPORT}"
chmod 0600 "${REPORT}"
exec > >(tee -a "${REPORT}") 2>&1

write_status() {
    printf '%s\n' "$1" > "${STATUS_FILE}"
    chmod 0600 "${STATUS_FILE}"
}

compose_service_ready() {
    local service="$1"
    local cid running health
    cid="$(docker compose -f "${COMPOSE_FILE}" ps -q "${service}" 2>/dev/null || true)"
    [[ -n "${cid}" ]] || return 1
    running="$(docker inspect -f '{{.State.Running}}' "${cid}" 2>/dev/null || true)"
    [[ "${running}" == "true" ]] || return 1
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}" 2>/dev/null || true)"
    [[ "${health}" == "none" || "${health}" == "healthy" ]]
}

ensure_milvus_stack_running() {
    local need_start=false
    local service
    for service in etcd minio standalone; do
        if ! compose_service_ready "${service}"; then
            need_start=true
        fi
    done

    if [[ "${need_start}" == "true" ]]; then
        echo "INFO|milvus_recovery_start_requested"
        docker compose -f "${COMPOSE_FILE}" up -d etcd minio standalone
    fi

    for _ in $(seq 1 60); do
        if compose_service_ready etcd \
            && compose_service_ready minio \
            && compose_service_ready standalone; then
            echo "PASS|milvus_stack_running_after_backup"
            return 0
        fi
        sleep 2
    done
    echo "ERROR|milvus_stack_not_ready_after_120_seconds" >&2
    return 1
}

mysql_root_password=""
final_result="failed"
previous_latest=""
destination=""

cleanup_and_recover() {
    local original_rc=$?
    local recovery_rc=0
    trap - EXIT INT TERM
    set +e
    unset MYSQL_PASSWORD
    mysql_root_password=""
    ensure_milvus_stack_running
    recovery_rc=$?
    if [[ "${final_result}" != "passed" ]]; then
        if [[ "${destination}" == "${BACKUP_ROOT}"/20* && -d "${destination}" \
            && ! -f "${destination}/INCOMPLETE" \
            && ! -f "${destination}/DEGRADED" ]]; then
            {
                echo "status=incomplete"
                echo "reason=cold_backup_validation_failed"
                echo "marked_at=$(date --iso-8601=seconds)"
                echo "preserved=true"
            } > "${destination}/INCOMPLETE"
            chmod 0600 "${destination}/INCOMPLETE"
        fi
        if [[ "${previous_latest}" == "${BACKUP_ROOT}"/20* \
            && -d "${previous_latest}" ]]; then
            ln -sfn "${previous_latest}" "${BACKUP_ROOT}/latest"
            echo "PASS|previous_verified_baseline_restored_as_latest"
        fi
    fi
    if [[ ${recovery_rc} -ne 0 ]]; then
        write_status "ERROR|milvus_recovery_failed"
        exit 1
    fi
    if [[ "${final_result}" == "passed" && ${original_rc} -eq 0 ]]; then
        write_status "PASS|cold_backup_complete"
        exit 0
    fi
    write_status "ERROR|cold_backup_failed_services_recovered"
    if [[ ${original_rc} -eq 0 ]]; then
        exit 1
    fi
    exit "${original_rc}"
}
trap cleanup_and_recover EXIT INT TERM

write_status "RUNNING|preflight"
echo "START|phase7_cold_milvus_backup|${RUN_ID}"

for required_file in "${RUNTIME_ENV}" "${BACKUP_SCRIPT}" "${COMPOSE_FILE}"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_file_not_readable|${required_file}" >&2
        exit 1
    fi
done

actual_backup_script_sha256="$(sha256sum "${BACKUP_SCRIPT}" | awk '{print $1}')"
if [[ "${actual_backup_script_sha256}" != "${EXPECTED_BACKUP_SCRIPT_SHA256}" ]]; then
    echo "ERROR|unexpected_backup_script_sha256" >&2
    exit 1
fi
echo "PASS|backup_script_sha256_gate"

previous_latest="$(readlink -f "${BACKUP_ROOT}/latest" 2>/dev/null || true)"
if [[ "${previous_latest}" != "${BACKUP_ROOT}"/20* || ! -d "${previous_latest}" ]]; then
    echo "ERROR|previous_latest_not_a_timestamped_backup" >&2
    exit 1
fi

# Source runtime settings in this shell only. The backup child is launched with a
# clean environment so unrelated secrets are not inherited.
# shellcheck disable=SC1090
. "${RUNTIME_ENV}"

mysql_container="${MYSQL_CONTAINER:-}"
mysql_database="${MYSQL_DATABASE:-}"
n8n_container="${N8N_CONTAINER:-n8n}"
if [[ -z "${mysql_container}" ]] \
    || ! docker ps --format '{{.Names}}' | grep -Fxq "${mysql_container}"; then
    echo "ERROR|configured_mysql_container_not_running" >&2
    exit 1
fi

if ! docker exec "${mysql_container}" sh -c \
    'test -n "$(printenv MYSQL_ROOT_PASSWORD)"'; then
    echo "ERROR|mysql_container_root_secret_unavailable" >&2
    exit 1
fi
if ! docker exec "${mysql_container}" sh -c \
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqladmin -uroot ping --silent' \
    >/dev/null; then
    echo "ERROR|mysql_container_root_ping_failed" >&2
    exit 1
fi

mysql_root_password="$(docker exec "${mysql_container}" printenv MYSQL_ROOT_PASSWORD)"
if [[ -z "${mysql_root_password}" ]]; then
    echo "ERROR|mysql_root_password_capture_failed" >&2
    exit 1
fi

if [[ -z "${mysql_database}" ]]; then
    mysql_database="$(
        docker exec "${mysql_container}" sh -c \
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B -e "SELECT TABLE_SCHEMA FROM information_schema.tables WHERE table_name=0x71756f74655f6a6f6273 AND TABLE_SCHEMA NOT IN (0x6d7973716c,0x696e666f726d6174696f6e5f736368656d61,0x706572666f726d616e63655f736368656d61,0x737973) ORDER BY TABLE_SCHEMA LIMIT 1"'
    )"
fi
if [[ -z "${mysql_database}" ]]; then
    echo "ERROR|quote_jobs_database_not_resolved" >&2
    exit 1
fi

active_quote_jobs="$(
    docker exec "${mysql_container}" sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B "$1" -e "SELECT COUNT(*) FROM quote_jobs WHERE status IN (0x717565756564,0x72756e6e696e67)"' \
        sh "${mysql_database}"
)"
if [[ ! "${active_quote_jobs}" =~ ^[0-9]+$ ]]; then
    echo "ERROR|active_quote_job_count_invalid" >&2
    exit 1
fi
if [[ "${active_quote_jobs}" != "0" ]]; then
    echo "ERROR|active_quote_jobs_present|count_${active_quote_jobs}" >&2
    exit 1
fi
echo "PASS|no_active_quote_jobs"

for service in etcd minio standalone; do
    if ! compose_service_ready "${service}"; then
        echo "ERROR|milvus_service_not_ready_before_backup|${service}" >&2
        exit 1
    fi
done
echo "PASS|milvus_stack_ready_before_backup"

available_kib="$(df -Pk "${BACKUP_ROOT}" | awk 'NR==2 {print $4}')"
if [[ ! "${available_kib}" =~ ^[0-9]+$ || ${available_kib} -lt 10485760 ]]; then
    echo "ERROR|less_than_10gib_backup_space_available" >&2
    exit 1
fi
echo "PASS|backup_space_gate"

write_status "RUNNING|cold_snapshot"
set +e
env -i \
    HOME=/root \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    RAG_DIR="${RAG_DIR}" \
    BACKUP_ROOT="${BACKUP_ROOT}" \
    COMPOSE_FILE="${COMPOSE_FILE}" \
    ENV_FILE=/dev/null \
    BACKUP_INCLUDE_ENV=false \
    STOP_MILVUS_FOR_BACKUP=true \
    MYSQL_CONTAINER="${mysql_container}" \
    MYSQL_DATABASE="${mysql_database}" \
    MYSQL_USER=root \
    MYSQL_PASSWORD="${mysql_root_password}" \
    MYSQLDUMP_NO_TABLESPACES=true \
    N8N_CONTAINER="${n8n_container}" \
    bash "${BACKUP_SCRIPT}" 2>&1 | tee "${RUN_LOG}"
backup_rc=${PIPESTATUS[0]}
set -e
mysql_root_password=""

destination="$(sed -n 's/^\[INFO\] Backup destination: //p' "${RUN_LOG}" | head -n 1)"
if [[ "${destination}" != "${BACKUP_ROOT}"/20* || ! -d "${destination}" ]]; then
    echo "ERROR|backup_destination_not_resolved" >&2
    exit 1
fi

if [[ ${backup_rc} -ne 0 ]]; then
    {
        echo "status=incomplete"
        echo "reason=backup_script_exit_${backup_rc}"
        echo "marked_at=$(date --iso-8601=seconds)"
        echo "preserved=true"
    } > "${destination}/INCOMPLETE"
    chmod 0600 "${destination}/INCOMPLETE"
    ln -sfn "${previous_latest}" "${BACKUP_ROOT}/latest"
    echo "ERROR|backup_script_failed|rc_${backup_rc}" >&2
    exit 1
fi

if grep -Fq '[WARN] Failed to back up directory:' "${RUN_LOG}" \
    || grep -Fq '[WARN] n8n CLI export failed' "${RUN_LOG}" \
    || grep -Fq '[WARN] Skip MySQL backup' "${RUN_LOG}" \
    || grep -Fq 'file changed as we read it' "${RUN_LOG}"; then
    {
        echo "status=degraded"
        echo "reason=backup_component_warning"
        echo "marked_at=$(date --iso-8601=seconds)"
        echo "do_not_use_as_complete_restore_baseline=true"
    } > "${destination}/DEGRADED"
    chmod 0600 "${destination}/DEGRADED"
    ln -sfn "${previous_latest}" "${BACKUP_ROOT}/latest"
    echo "ERROR|backup_component_warning_detected" >&2
    exit 1
fi

if ! grep -Fq '[WARN] Stopping Milvus stack for a consistent volume backup' "${RUN_LOG}" \
    || ! grep -Fq '[INFO] Milvus stack restarted' "${RUN_LOG}"; then
    echo "ERROR|cold_snapshot_stop_restart_evidence_missing" >&2
    exit 1
fi

ensure_milvus_stack_running

required_artifacts=(
    docker-compose.yml
    mysql.sql
    n8n_workflows.json
    milvus_etcd.tgz
    milvus_minio.tgz
    milvus_data.tgz
    quote_minio.tgz
    SHA256SUMS
)
for artifact in "${required_artifacts[@]}"; do
    if [[ ! -s "${destination}/${artifact}" ]]; then
        echo "ERROR|required_artifact_missing_or_empty|${artifact}" >&2
        exit 1
    fi
done

if ! tail -n 50 "${destination}/mysql.sql" | grep -Fq 'Dump completed on'; then
    echo "ERROR|mysql_dump_completion_marker_missing" >&2
    exit 1
fi
if find "${destination}" -maxdepth 1 -type f \
    \( -name '*.env' -o -name 'rag_service.env' \) | grep -q .; then
    echo "ERROR|runtime_env_unexpectedly_in_backup" >&2
    exit 1
fi
if ! (cd "${destination}" && sha256sum -c --quiet SHA256SUMS); then
    echo "ERROR|initial_sha256_manifest_validation_failed" >&2
    exit 1
fi

tar_count=0
while IFS= read -r -d '' tarball; do
    tar -tzf "${tarball}" >/dev/null
    tar_count=$((tar_count + 1))
done < <(find "${destination}" -maxdepth 1 -type f -name '*.tgz' -print0)
if [[ ${tar_count} -lt 4 ]]; then
    echo "ERROR|compressed_artifact_count_below_four" >&2
    exit 1
fi

artifact_count="$(find "${destination}" -maxdepth 1 -type f ! -name SHA256SUMS | wc -l)"
backup_bytes="$(du -sb "${destination}" | awk '{print $1}')"
{
    echo "status=validated_cold_backup"
    echo "validated_at=$(date --iso-8601=seconds)"
    echo "runtime_env_included=false"
    echo "mysql_dump_completion_marker=true"
    echo "compressed_tar_count=${tar_count}"
    echo "artifact_count_before_validation_record=${artifact_count}"
    echo "backup_bytes_before_manifest_refresh=${backup_bytes}"
    echo "milvus_snapshot=cold"
    echo "active_quote_jobs_before_snapshot=0"
    echo "services_recovered=true"
} > "${destination}/PHASE7_VALIDATION.txt"
chmod 0600 "${destination}/PHASE7_VALIDATION.txt"

manifest_tmp="$(mktemp "${destination}/.SHA256SUMS.XXXXXX")"
(
    cd "${destination}"
    find . -maxdepth 1 -type f \
        ! -name SHA256SUMS \
        ! -name '.SHA256SUMS.*' \
        -print0 \
        | sort -z \
        | xargs -0 -r sha256sum > "${manifest_tmp}"
)
chmod 0600 "${manifest_tmp}"
mv "${manifest_tmp}" "${destination}/SHA256SUMS"
if ! (cd "${destination}" && sha256sum -c --quiet SHA256SUMS); then
    echo "ERROR|final_sha256_manifest_validation_failed" >&2
    exit 1
fi

latest_target="$(readlink -f "${BACKUP_ROOT}/latest")"
if [[ "${latest_target}" != "${destination}" ]]; then
    echo "ERROR|latest_link_does_not_match_validated_cold_backup" >&2
    exit 1
fi

final_result="passed"
echo "PASS|fresh_validated_cold_backup"
echo "PASS|mysql_dump_completion_marker"
echo "PASS|compressed_volume_archives|count_${tar_count}"
echo "PASS|sha256_manifest"
echo "PASS|runtime_env_excluded"
echo "BACKUP_DIR=${destination}"
echo "BACKUP_SIZE=$(du -sh "${destination}" | awk '{print $1}')"
echo "RUN_LOG=${RUN_LOG}"
echo "REPORT=${REPORT}"
echo "NEXT|source_side_encryption_and_offsite_copy"
