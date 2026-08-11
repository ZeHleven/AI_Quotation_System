#!/usr/bin/env bash
set -Eeuo pipefail

readonly BACKUP_ROOT="/opt/rag_service/backups"
readonly EXPECTED_SOURCE="${BACKUP_ROOT}/20260806_105418"
readonly OFFSITE_DIR="${BACKUP_ROOT}/offsite"
readonly OUTPUT_NAME="ai-middle-office-centos-20260806_105418.tar.gpg"
readonly OUTPUT_PATH="${OFFSITE_DIR}/${OUTPUT_NAME}"
readonly PARTIAL_PATH="${OUTPUT_PATH}.partial"
readonly SHA_PATH="${OUTPUT_PATH}.sha256"
readonly REPORT="${OFFSITE_DIR}/phase7-encryption-20260806_105418.report"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi
if [[ ! -t 0 || ! -t 1 ]]; then
    echo "ERROR|interactive_tty_required" >&2
    exit 1
fi

umask 077
mkdir -p "${OFFSITE_DIR}"
touch "${REPORT}"
chmod 0600 "${REPORT}"

safe_log() {
    printf '%s\n' "$1" | tee -a "${REPORT}"
}

if [[ "$(readlink -f "${EXPECTED_SOURCE}")" != "${EXPECTED_SOURCE}" \
    || ! -d "${EXPECTED_SOURCE}" ]]; then
    safe_log "ERROR|validated_cold_backup_source_missing"
    exit 1
fi
if [[ -e "${OUTPUT_PATH}" || -e "${PARTIAL_PATH}" ]]; then
    safe_log "ERROR|encrypted_output_or_partial_already_exists"
    exit 1
fi
if [[ ! -s "${EXPECTED_SOURCE}/PHASE7_VALIDATION.txt" ]] \
    || ! grep -Fxq 'status=validated_cold_backup' \
        "${EXPECTED_SOURCE}/PHASE7_VALIDATION.txt" \
    || ! grep -Fxq 'milvus_snapshot=cold' \
        "${EXPECTED_SOURCE}/PHASE7_VALIDATION.txt"; then
    safe_log "ERROR|cold_backup_validation_record_missing"
    exit 1
fi
if find "${EXPECTED_SOURCE}" -maxdepth 1 -type f \
    \( -name '*.env' -o -name 'rag_service.env' \) | grep -q .; then
    safe_log "ERROR|runtime_env_unexpectedly_in_backup"
    exit 1
fi
if ! (cd "${EXPECTED_SOURCE}" && sha256sum -c --quiet SHA256SUMS); then
    safe_log "ERROR|source_sha256_manifest_failed"
    exit 1
fi
safe_log "PASS|validated_cold_backup_source"
safe_log "PASS|runtime_env_excluded"

passphrase=""
confirmation=""
printf 'Enter a new offsite-backup passphrase (hidden): ' >&2
IFS= read -r -s passphrase
printf '\nConfirm the passphrase (hidden): ' >&2
IFS= read -r -s confirmation
printf '\n' >&2

if [[ ${#passphrase} -lt 20 ]]; then
    safe_log "ERROR|passphrase_shorter_than_20_characters"
    unset passphrase confirmation
    exit 1
fi
if [[ "${passphrase}" != "${confirmation}" ]]; then
    safe_log "ERROR|passphrase_confirmation_mismatch"
    unset passphrase confirmation
    exit 1
fi
confirmation=""

on_exit() {
    local rc=$?
    trap - EXIT INT TERM
    set +e
    unset passphrase confirmation
    if [[ ${rc} -ne 0 && -f "${PARTIAL_PATH}" ]]; then
        mv "${PARTIAL_PATH}" \
            "${OUTPUT_PATH}.incomplete-$(date +%Y%m%d_%H%M%S)"
        safe_log "PRESERVED|encrypted_partial_marked_incomplete"
    fi
    exit "${rc}"
}
trap on_exit EXIT INT TERM

safe_log "START|aes256_source_side_encryption"
exec 3<<<"${passphrase}"
tar -C "${BACKUP_ROOT}" -cf - "$(basename "${EXPECTED_SOURCE}")" \
    | gpg --batch --yes \
        --passphrase-fd 3 \
        --symmetric \
        --cipher-algo AES256 \
        --s2k-mode 3 \
        --s2k-digest-algo SHA512 \
        --s2k-count 65011712 \
        --compress-algo none \
        --output "${PARTIAL_PATH}"
exec 3<&-
chmod 0600 "${PARTIAL_PATH}"
safe_log "PASS|aes256_encrypted_artifact_created"

exec 3<<<"${passphrase}"
gpg --batch --yes --passphrase-fd 3 --decrypt "${PARTIAL_PATH}" 2>/dev/null \
    | tar -tf - >/dev/null
exec 3<&-
unset passphrase confirmation
safe_log "PASS|decrypt_and_tar_integrity_check"

mv "${PARTIAL_PATH}" "${OUTPUT_PATH}"
(
    cd "${OFFSITE_DIR}"
    sha256sum "${OUTPUT_NAME}" > "$(basename "${SHA_PATH}")"
)
chmod 0600 "${OUTPUT_PATH}" "${SHA_PATH}"

safe_log "PASS|encrypted_offsite_artifact_ready"
safe_log "ARTIFACT=${OUTPUT_PATH}"
safe_log "SHA256_FILE=${SHA_PATH}"
safe_log "ARTIFACT_SIZE=$(du -sh "${OUTPUT_PATH}" | awk '{print $1}')"
safe_log "NEXT|copy_artifact_and_sha256_to_offsite_host"
