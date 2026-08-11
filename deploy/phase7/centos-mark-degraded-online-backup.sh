#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEGRADED_DIR="/opt/rag_service/backups/20260806_103547"
readonly PREVIOUS_BASELINE="/opt/rag_service/backups/20260803_210859"
readonly RUN_LOG="/opt/rag_service/backups/phase7-online-backup-20260806_103547.run.log"
readonly VALIDATION_FILE="${DEGRADED_DIR}/PHASE7_VALIDATION.txt"
readonly STAMP="$(date +%Y%m%d_%H%M%S)"
readonly EVIDENCE_DIR="/opt/rag_service/backups/pre-degraded-mark-${STAMP}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi

umask 077

for required_path in \
    "${DEGRADED_DIR}" \
    "${PREVIOUS_BASELINE}" \
    "${RUN_LOG}" \
    "${VALIDATION_FILE}"; do
    if [[ ! -e "${required_path}" ]]; then
        echo "ERROR|required_path_missing|${required_path}" >&2
        exit 1
    fi
done

if ! grep -Fq \
    '[WARN] Failed to back up directory: /opt/rag_service/volumes/milvus' \
    "${RUN_LOG}"; then
    echo "ERROR|expected_milvus_archive_warning_missing" >&2
    exit 1
fi

if ! (
    cd "${PREVIOUS_BASELINE}"
    sha256sum -c --quiet SHA256SUMS
); then
    echo "ERROR|previous_baseline_manifest_failed" >&2
    exit 1
fi

install -d -o root -g root -m 0700 "${EVIDENCE_DIR}"
cp -a "${VALIDATION_FILE}" "${EVIDENCE_DIR}/PHASE7_VALIDATION.before.txt"
readlink -f /opt/rag_service/backups/latest \
    > "${EVIDENCE_DIR}/latest.before.txt"
chmod 0600 "${EVIDENCE_DIR}"/*.txt

{
    echo "status=degraded_online_backup"
    echo "validated_at=$(date --iso-8601=seconds)"
    echo "runtime_env_included=false"
    echo "mysql_dump_completion_marker=true"
    echo "milvus_snapshot=online_failed_due_to_file_changes"
    echo "do_not_use_as_complete_restore_baseline=true"
    echo "cold_snapshot_required=true"
} > "${VALIDATION_FILE}"
chmod 0600 "${VALIDATION_FILE}"

{
    echo "status=degraded"
    echo "reason=milvus_data_changed_during_online_archive"
    echo "marked_at=$(date --iso-8601=seconds)"
    echo "do_not_use_as_complete_restore_baseline=true"
} > "${DEGRADED_DIR}/DEGRADED"
chmod 0600 "${DEGRADED_DIR}/DEGRADED"

manifest_tmp="$(mktemp "${DEGRADED_DIR}/.SHA256SUMS.XXXXXX")"
(
    cd "${DEGRADED_DIR}"
    find . -maxdepth 1 -type f \
        ! -name SHA256SUMS \
        ! -name '.SHA256SUMS.*' \
        -print0 \
        | sort -z \
        | xargs -0 -r sha256sum > "${manifest_tmp}"
)
chmod 0600 "${manifest_tmp}"
mv "${manifest_tmp}" "${DEGRADED_DIR}/SHA256SUMS"

if ! (
    cd "${DEGRADED_DIR}"
    sha256sum -c --quiet SHA256SUMS
); then
    echo "ERROR|degraded_manifest_refresh_failed" >&2
    exit 1
fi

ln -sfn "${PREVIOUS_BASELINE}" /opt/rag_service/backups/latest
if [[ "$(readlink -f /opt/rag_service/backups/latest)" \
    != "${PREVIOUS_BASELINE}" ]]; then
    echo "ERROR|latest_baseline_restore_failed" >&2
    exit 1
fi

sha256sum "${EVIDENCE_DIR}"/*.txt > "${EVIDENCE_DIR}/SHA256SUMS"
chmod 0600 "${EVIDENCE_DIR}/SHA256SUMS"

echo "PASS|degraded_online_backup_marked"
echo "PASS|previous_verified_baseline_restored_as_latest"
echo "DEGRADED_DIR=${DEGRADED_DIR}"
echo "LATEST=$(readlink -f /opt/rag_service/backups/latest)"
echo "EVIDENCE_DIR=${EVIDENCE_DIR}"
echo "NEXT|cold_milvus_backup_during_maintenance_window"
