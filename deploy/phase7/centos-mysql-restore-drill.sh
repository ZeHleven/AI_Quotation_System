#!/usr/bin/env bash
set -Eeuo pipefail

readonly BACKUP_DIR="/opt/rag_service/backups/20260806_105418"
readonly MYSQL_DUMP="${BACKUP_DIR}/mysql.sql"
readonly MYSQL_IMAGE="mysql:8.0.39"
readonly EXPECTED_DATABASE="ai_quotation"
readonly EXPECTED_ALEMBIC_VERSION="20260801_0081"
readonly PRODUCTION_MYSQL_CONTAINER="ragflow-mysql-1"
readonly RUN_ID="${PHASE7_RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
readonly DRILL_CONTAINER="ai-phase7-mysql-restore-${RUN_ID}"
readonly TEMP_ENV="/root/ai-phase7/.mysql-restore-${RUN_ID}.env"
readonly REPORT="/opt/rag_service/backups/phase7-mysql-restore-${RUN_ID}.report"
readonly STATUS_FILE="/opt/rag_service/backups/phase7-mysql-restore-${RUN_ID}.status"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi
if [[ ! "${RUN_ID}" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
    echo "ERROR|invalid_run_id" >&2
    exit 1
fi

umask 077
touch "${REPORT}"
chmod 0600 "${REPORT}"
exec > >(tee -a "${REPORT}") 2>&1

write_status() {
    printf '%s\n' "$1" > "${STATUS_FILE}"
    chmod 0600 "${STATUS_FILE}"
}

cleanup_complete=false

cleanup_drill_resources() {
    local cleanup_rc=0
    set +e
    if docker ps -a --format '{{.Names}}' | grep -Fxq "${DRILL_CONTAINER}"; then
        docker rm -f -v "${DRILL_CONTAINER}" >/dev/null
        if [[ $? -ne 0 ]]; then
            cleanup_rc=1
        fi
    fi
    if [[ -f "${TEMP_ENV}" ]]; then
        chmod 0600 "${TEMP_ENV}" 2>/dev/null
        rm -f -- "${TEMP_ENV}"
        if [[ $? -ne 0 ]]; then
            cleanup_rc=1
        fi
    fi
    if docker ps -a --format '{{.Names}}' | grep -Fxq "${DRILL_CONTAINER}"; then
        cleanup_rc=1
    fi
    if [[ ${cleanup_rc} -eq 0 ]]; then
        cleanup_complete=true
        echo "PASS|temporary_restore_container_and_volume_removed"
    else
        echo "ERROR|temporary_restore_resource_cleanup_failed" >&2
    fi
    set -e
    return "${cleanup_rc}"
}

on_exit() {
    local original_rc=$?
    local cleanup_rc=0
    trap - EXIT INT TERM
    if [[ "${cleanup_complete}" != "true" ]]; then
        cleanup_drill_resources || cleanup_rc=$?
    fi
    if [[ ${cleanup_rc} -ne 0 ]]; then
        write_status "ERROR|restore_drill_cleanup_failed"
        exit 1
    fi
    if [[ ${original_rc} -eq 0 ]]; then
        write_status "PASS|mysql_restore_drill_complete"
    else
        write_status "ERROR|mysql_restore_drill_failed_resources_cleaned"
    fi
    exit "${original_rc}"
}
trap on_exit EXIT INT TERM

write_status "RUNNING|preflight"
echo "START|phase7_isolated_mysql_restore_drill|${RUN_ID}"

if [[ ! -s "${MYSQL_DUMP}" || ! -s "${BACKUP_DIR}/SHA256SUMS" ]]; then
    echo "ERROR|backup_dump_or_manifest_missing" >&2
    exit 1
fi
if ! tail -n 50 "${MYSQL_DUMP}" | grep -Fq 'Dump completed on'; then
    echo "ERROR|mysql_dump_completion_marker_missing" >&2
    exit 1
fi
if ! (cd "${BACKUP_DIR}" && sha256sum -c --quiet SHA256SUMS); then
    echo "ERROR|backup_sha256_manifest_failed" >&2
    exit 1
fi
echo "PASS|backup_source_integrity"

if ! docker image inspect "${MYSQL_IMAGE}" >/dev/null 2>&1; then
    echo "ERROR|required_mysql_image_not_local" >&2
    exit 1
fi
if docker ps -a --format '{{.Names}}' | grep -Fxq "${DRILL_CONTAINER}"; then
    echo "ERROR|restore_drill_container_name_already_exists" >&2
    exit 1
fi
if ! docker ps --format '{{.Names}}' | grep -Fxq "${PRODUCTION_MYSQL_CONTAINER}"; then
    echo "ERROR|production_mysql_not_running" >&2
    exit 1
fi

active_quote_jobs="$(
    docker exec "${PRODUCTION_MYSQL_CONTAINER}" sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B ai_quotation -e "SELECT COUNT(*) FROM quote_jobs WHERE status IN (0x717565756564,0x72756e6e696e67)"'
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

available_kib="$(df -Pk "${BACKUP_DIR}" | awk 'NR==2 {print $4}')"
if [[ ! "${available_kib}" =~ ^[0-9]+$ || ${available_kib} -lt 15728640 ]]; then
    echo "ERROR|less_than_15gib_restore_space_available" >&2
    exit 1
fi
echo "PASS|restore_space_gate"

restore_password="$(openssl rand -hex 32)"
if [[ ${#restore_password} -ne 64 ]]; then
    echo "ERROR|temporary_password_generation_failed" >&2
    exit 1
fi
printf 'MYSQL_ROOT_PASSWORD=%s\n' "${restore_password}" > "${TEMP_ENV}"
chmod 0600 "${TEMP_ENV}"
restore_password=""

write_status "RUNNING|container_start"
docker run -d \
    --name "${DRILL_CONTAINER}" \
    --network none \
    --pull never \
    --cpus 1.0 \
    --memory 2g \
    --pids-limit 512 \
    --env-file "${TEMP_ENV}" \
    "${MYSQL_IMAGE}" >/dev/null
rm -f -- "${TEMP_ENV}"
echo "PASS|isolated_mysql_container_started_without_network"

ready=false
for _ in $(seq 1 90); do
    if docker exec "${DRILL_CONTAINER}" sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqladmin -uroot ping --silent' \
        >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 2
done
if [[ "${ready}" != "true" ]]; then
    echo "ERROR|isolated_mysql_not_ready_after_180_seconds" >&2
    exit 1
fi
echo "PASS|isolated_mysql_ready"

write_status "RUNNING|mysql_import"
echo "START|mysql_dump_import"
docker exec -i "${DRILL_CONTAINER}" sh -c \
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --binary-mode=1' \
    < "${MYSQL_DUMP}"
echo "PASS|mysql_dump_import"

write_status "RUNNING|structural_validation"
database_exists="$(
    docker exec "${DRILL_CONTAINER}" sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B -e "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=0x61695f71756f746174696f6e"'
)"
table_count="$(
    docker exec "${DRILL_CONTAINER}" sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=0x61695f71756f746174696f6e AND table_type=0x42415345205441424c45"'
)"
required_table_count="$(
    docker exec "${DRILL_CONTAINER}" sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=0x61695f71756f746174696f6e AND table_name IN (0x71756f74655f6a6f6273,0x636f73745f6974656d73,0x6275646765745f70726f6a6563745f70726963696e675f72756e5f64726166745f736e617073686f7473)"'
)"
alembic_version="$(
    docker exec "${DRILL_CONTAINER}" sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B ai_quotation -e "SELECT version_num FROM alembic_version LIMIT 1"'
)"

if [[ "${database_exists}" != "1" ]]; then
    echo "ERROR|expected_database_missing_after_restore" >&2
    exit 1
fi
if [[ ! "${table_count}" =~ ^[0-9]+$ || ${table_count} -lt 10 ]]; then
    echo "ERROR|restored_table_count_below_minimum" >&2
    exit 1
fi
if [[ "${required_table_count}" != "3" ]]; then
    echo "ERROR|required_tables_missing_after_restore" >&2
    exit 1
fi
if [[ "${alembic_version}" != "${EXPECTED_ALEMBIC_VERSION}" ]]; then
    echo "ERROR|unexpected_alembic_version_after_restore" >&2
    exit 1
fi

echo "PASS|expected_database_restored"
echo "PASS|required_tables_restored|count_3"
echo "PASS|alembic_version|${alembic_version}"
echo "RESTORED_BASE_TABLE_COUNT=${table_count}"

cleanup_drill_resources

echo "PASS|isolated_mysql_restore_drill"
echo "PASS|no_network_exposure"
echo "PASS|production_database_untouched"
echo "REPORT=${REPORT}"
echo "NEXT|monitoring_certificate_and_restart_gates"
