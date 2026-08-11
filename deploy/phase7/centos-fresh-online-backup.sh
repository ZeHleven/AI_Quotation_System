#!/usr/bin/env bash
set -Eeuo pipefail

readonly RAG_DIR="/opt/rag_service"
readonly RUNTIME_ENV="${RAG_DIR}/.env"
readonly BACKUP_SCRIPT="${RAG_DIR}/backup_production.sh"
readonly BACKUP_ROOT="${RAG_DIR}/backups"
readonly KNOWN_PARTIAL="${BACKUP_ROOT}/20260806_103144"
readonly STAMP="$(date +%Y%m%d_%H%M%S)"
readonly RUN_LOG="${BACKUP_ROOT}/phase7-online-backup-${STAMP}.run.log"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi

umask 077

for required_file in "${RUNTIME_ENV}" "${BACKUP_SCRIPT}"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_file_not_readable|${required_file}" >&2
        exit 1
    fi
done

if [[ -d "${KNOWN_PARTIAL}" \
    && ! -f "${KNOWN_PARTIAL}/SHA256SUMS" \
    && ! -f "${KNOWN_PARTIAL}/INCOMPLETE" ]]; then
    {
        echo "status=incomplete"
        echo "reason=mysql_backup_account_locked"
        echo "marked_at=$(date --iso-8601=seconds)"
        echo "preserved=true"
    } > "${KNOWN_PARTIAL}/INCOMPLETE"
    chmod 0600 "${KNOWN_PARTIAL}/INCOMPLETE"
    echo "PASS|known_partial_backup_marked_and_preserved"
fi

previous_latest="$(readlink -f "${BACKUP_ROOT}/latest" 2>/dev/null || true)"

set -a
# shellcheck disable=SC1090
. "${RUNTIME_ENV}"
set +a

mysql_container="${MYSQL_CONTAINER:-}"
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

export RAG_DIR
export BACKUP_ROOT
export COMPOSE_FILE="${RAG_DIR}/docker-compose.yml"
export ENV_FILE="/dev/null"
export BACKUP_INCLUDE_ENV="false"
export STOP_MILVUS_FOR_BACKUP="false"
export MYSQL_CONTAINER="${mysql_container}"
export MYSQL_USER="root"
export MYSQL_PASSWORD="${mysql_root_password}"
export MYSQLDUMP_NO_TABLESPACES="true"

set +e
bash "${BACKUP_SCRIPT}" 2>&1 | tee "${RUN_LOG}"
backup_rc=${PIPESTATUS[0]}
set -e
unset mysql_root_password MYSQL_PASSWORD

destination="$(
    sed -n 's/^\[INFO\] Backup destination: //p' "${RUN_LOG}" | head -n 1
)"
if [[ "${destination}" != "${BACKUP_ROOT}"/20* \
    || ! -d "${destination}" ]]; then
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
    echo "ERROR|backup_script_failed|rc_${backup_rc}" >&2
    echo "INCOMPLETE_DIR=${destination}" >&2
    exit 1
fi

archive_warning=false
if grep -Fq '[WARN] Failed to back up directory:' "${RUN_LOG}"; then
    archive_warning=true
fi

if [[ ! -s "${destination}/mysql.sql" ]]; then
    echo "ERROR|mysql_dump_missing_or_empty" >&2
    exit 1
fi
if ! tail -n 50 "${destination}/mysql.sql" \
    | grep -Fq 'Dump completed on'; then
    echo "ERROR|mysql_dump_completion_marker_missing" >&2
    exit 1
fi
if [[ ! -s "${destination}/SHA256SUMS" ]]; then
    echo "ERROR|backup_manifest_missing_or_empty" >&2
    exit 1
fi
if find "${destination}" -maxdepth 1 -type f \
    \( -name '*.env' -o -name 'rag_service.env' \) \
    | grep -q .; then
    echo "ERROR|runtime_env_unexpectedly_in_backup" >&2
    exit 1
fi

if ! (
    cd "${destination}"
    sha256sum -c --quiet SHA256SUMS
); then
    echo "ERROR|initial_sha256_manifest_validation_failed" >&2
    exit 1
fi

tar_count=0
while IFS= read -r -d '' tarball; do
    tar -tzf "${tarball}" >/dev/null
    tar_count=$((tar_count + 1))
done < <(find "${destination}" -maxdepth 1 -type f -name '*.tgz' -print0)
if [[ ${tar_count} -lt 1 ]]; then
    echo "ERROR|no_compressed_volume_artifacts" >&2
    exit 1
fi

artifact_count="$(
    find "${destination}" -maxdepth 1 -type f ! -name SHA256SUMS | wc -l
)"
backup_bytes="$(du -sb "${destination}" | awk '{print $1}')"
{
    if [[ "${archive_warning}" == "true" ]]; then
        echo "status=degraded_online_backup"
    else
        echo "status=validated_online_backup"
    fi
    echo "validated_at=$(date --iso-8601=seconds)"
    echo "runtime_env_included=false"
    echo "mysql_dump_completion_marker=true"
    echo "compressed_tar_count=${tar_count}"
    echo "artifact_count_before_validation_record=${artifact_count}"
    echo "backup_bytes_before_manifest_refresh=${backup_bytes}"
    echo "milvus_snapshot=online"
    echo "cold_snapshot_still_required=true"
} > "${destination}/PHASE7_VALIDATION.txt"
chmod 0600 "${destination}/PHASE7_VALIDATION.txt"

if [[ "${archive_warning}" == "true" ]]; then
    {
        echo "status=degraded"
        echo "reason=one_or_more_volume_archives_reported_failure"
        echo "marked_at=$(date --iso-8601=seconds)"
        echo "do_not_use_as_complete_restore_baseline=true"
    } > "${destination}/DEGRADED"
    chmod 0600 "${destination}/DEGRADED"
fi

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

if ! (
    cd "${destination}"
    sha256sum -c --quiet SHA256SUMS
); then
    echo "ERROR|final_sha256_manifest_validation_failed" >&2
    exit 1
fi

if [[ "${archive_warning}" == "true" ]]; then
    if [[ "${previous_latest}" == "${BACKUP_ROOT}"/20* \
        && -d "${previous_latest}" ]]; then
        ln -sfn "${previous_latest}" "${BACKUP_ROOT}/latest"
    fi
    echo "ERROR|online_backup_degraded_by_volume_archive_warning" >&2
    echo "DEGRADED_DIR=${destination}" >&2
    echo "LATEST_RESTORED_TO=$(readlink -f "${BACKUP_ROOT}/latest" 2>/dev/null || true)" >&2
    exit 2
else
    latest_target="$(readlink -f "${BACKUP_ROOT}/latest")"
    if [[ "${latest_target}" != "${destination}" ]]; then
        echo "ERROR|latest_link_does_not_match_validated_backup" >&2
        exit 1
    fi
fi

echo "PASS|fresh_online_backup"
echo "PASS|mysql_dump_completion_marker"
echo "PASS|compressed_volume_archives|count_${tar_count}"
echo "PASS|sha256_manifest"
echo "PASS|runtime_env_excluded"
echo "BACKUP_DIR=${destination}"
echo "BACKUP_SIZE=$(du -sh "${destination}" | awk '{print $1}')"
echo "RUN_LOG=${RUN_LOG}"
echo "NEXT|source_side_encryption_and_offsite_copy"
