#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
    cat >&2 <<'EOF'
Usage:
  sudo bash phase8-free-performance-build.sh STAGING_DIR IMAGE_TAG [BASE_IMAGE_TAG]

STAGING_DIR must contain performance-overlay.Dockerfile, dist.SHA256SUMS,
and ai-web/dist. The build uses --network none and never pulls an image.
EOF
    exit 2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
    usage
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_with_sudo" >&2
    exit 1
fi

readonly STAGING_DIR="$1"
readonly IMAGE_TAG="$2"
readonly BASE_IMAGE_TAG="${3:-20260805_161737}"
readonly BASE_IMAGE="ai-middle-office-app:${BASE_IMAGE_TAG}"
readonly IMAGE_REF="ai-middle-office-app:${IMAGE_TAG}"
readonly DOCKERFILE="${STAGING_DIR}/performance-overlay.Dockerfile"
readonly DIST_MANIFEST="${STAGING_DIR}/dist.SHA256SUMS"
readonly OUTPUT_TAR="${STAGING_DIR}/${IMAGE_TAG}.tar"
readonly OUTPUT_SHA="${OUTPUT_TAR}.sha256"
readonly SCAN_REPORT="${STAGING_DIR}/${IMAGE_TAG}.trivy.json"
readonly TRIVY_SCANNER="public.ecr.aws/aquasecurity/trivy:0.72.0"
readonly TRIVY_SCANNER_ID="sha256:b81e075c68ad4b1a567e7b4d511e3b0493b0087ae708ff4dd607c497cde1daf6"
readonly TRIVY_SCANNER_DIGEST="public.ecr.aws/aquasecurity/trivy@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
readonly TRIVY_CACHE_DIR="/home/aiadmin/ai-phase5-image-evidence-20260805_161737/trivy-cache-20260805_161737"
readonly TRIVY_DB="${TRIVY_CACHE_DIR}/db/trivy.db"
readonly TRIVY_DB_METADATA="${TRIVY_CACHE_DIR}/db/metadata.json"
readonly TRIVY_DB_SHA256="098160a36a49825989724844beabcb6e1f5b37884cce8369dbac5d7d41fa51b9"
readonly TRIVY_DB_METADATA_SHA256="e90a901b39dd64d3e50ed68bd0475f764f428cbe905c7d085611b7feb60ca2ec"

if [[ ! "${IMAGE_TAG}" =~ ^[0-9]{8}_[0-9]{6}(_perf[0-9]+)?$ ]] \
    || [[ ! "${BASE_IMAGE_TAG}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "ERROR|invalid_image_tag" >&2
    exit 1
fi

for required_file in \
    "${DOCKERFILE}" \
    "${DIST_MANIFEST}" \
    "${STAGING_DIR}/ai-web/dist/index.html"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_file_not_readable|${required_file}" >&2
        exit 1
    fi
done

if ! docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
    echo "ERROR|local_base_image_missing|${BASE_IMAGE}" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR|python3_required_for_scan_validation" >&2
    exit 1
fi

if ! docker image inspect "${TRIVY_SCANNER}" >/dev/null 2>&1; then
    echo "ERROR|local_trivy_scanner_missing|${TRIVY_SCANNER}" >&2
    exit 1
fi
if [[ "$(docker image inspect --format '{{.Id}}' "${TRIVY_SCANNER}")" != "${TRIVY_SCANNER_ID}" ]]; then
    echo "ERROR|trivy_scanner_image_id_mismatch" >&2
    exit 1
fi
if ! docker image inspect --format '{{json .RepoDigests}}' "${TRIVY_SCANNER}" \
    | grep -Fq "${TRIVY_SCANNER_DIGEST}"; then
    echo "ERROR|trivy_scanner_digest_mismatch" >&2
    exit 1
fi

if [[ ! -d "${TRIVY_CACHE_DIR}" || -L "${TRIVY_CACHE_DIR}" ]] \
    || [[ "$(readlink -f -- "${TRIVY_CACHE_DIR}")" != "${TRIVY_CACHE_DIR}" ]]; then
    echo "ERROR|trivy_cache_invalid" >&2
    exit 1
fi
for database_file in "${TRIVY_DB}" "${TRIVY_DB_METADATA}"; do
    if [[ ! -f "${database_file}" || -L "${database_file}" ]]; then
        echo "ERROR|trivy_database_file_invalid|${database_file}" >&2
        exit 1
    fi
done
if [[ "$(sha256sum "${TRIVY_DB}" | awk '{print $1}')" != "${TRIVY_DB_SHA256}" ]]; then
    echo "ERROR|trivy_database_hash_mismatch" >&2
    exit 1
fi
if [[ "$(sha256sum "${TRIVY_DB_METADATA}" | awk '{print $1}')" != "${TRIVY_DB_METADATA_SHA256}" ]]; then
    echo "ERROR|trivy_database_metadata_hash_mismatch" >&2
    exit 1
fi

(
    cd "${STAGING_DIR}"
    sha256sum --check "${DIST_MANIFEST}"
)

docker build \
    --network none \
    --pull=false \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "APP_VERSION=${IMAGE_TAG}" \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_REF}" \
    "${STAGING_DIR}"

if [[ "$(docker image inspect --format '{{.Config.User}}' "${IMAGE_REF}")" != "10001:10001" ]]; then
    echo "ERROR|overlay_image_user_changed" >&2
    exit 1
fi

docker save --output "${OUTPUT_TAR}" "${IMAGE_REF}"
chmod 0600 "${OUTPUT_TAR}"
chown aiadmin:aiadmin "${OUTPUT_TAR}"

scanner_uid="$(id -u aiadmin)"
scanner_gid="$(id -g aiadmin)"
if [[ ! "${scanner_uid}" =~ ^[0-9]+$ || ! "${scanner_gid}" =~ ^[0-9]+$ ]]; then
    echo "ERROR|aiadmin_identity_invalid" >&2
    exit 1
fi

db_hash_before="$(sha256sum "${TRIVY_DB}" | awk '{print $1}')"
metadata_hash_before="$(sha256sum "${TRIVY_DB_METADATA}" | awk '{print $1}')"

docker run --rm \
    --name "ai-phase8-trivy-${IMAGE_TAG//_/-}" \
    --user "${scanner_uid}:${scanner_gid}" \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=2147483648 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 256 \
    --memory 4g \
    --cpus 2 \
    --mount type=bind,src="${TRIVY_CACHE_DIR}",dst=/cache \
    --mount type=bind,src="${STAGING_DIR}",dst=/input,readonly \
    --mount type=bind,src="${STAGING_DIR}",dst=/output \
    "${TRIVY_SCANNER}" image \
    --disable-telemetry \
    --cache-dir /cache \
    --skip-db-update \
    --skip-java-db-update \
    --skip-check-update \
    --offline-scan \
    --scanners vuln,secret \
    --exit-code 1 \
    --format json \
    --output "/output/$(basename "${SCAN_REPORT}")" \
    --input "/input/$(basename "${OUTPUT_TAR}")"

db_hash_after="$(sha256sum "${TRIVY_DB}" | awk '{print $1}')"
metadata_hash_after="$(sha256sum "${TRIVY_DB_METADATA}" | awk '{print $1}')"
if [[ "${db_hash_before}" != "${db_hash_after}" ]] \
    || [[ "${metadata_hash_before}" != "${metadata_hash_after}" ]]; then
    echo "ERROR|trivy_database_changed_during_offline_scan" >&2
    exit 1
fi

scan_summary="$(python3 - "${SCAN_REPORT}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    report = json.load(handle)

results = report.get("Results") or []
vulnerabilities = sum(len(item.get("Vulnerabilities") or []) for item in results)
secrets = sum(len(item.get("Secrets") or []) for item in results)
targets = len(results)
print(f"targets={targets}|vulnerabilities={vulnerabilities}|secrets={secrets}")
if targets < 1 or vulnerabilities or secrets:
    raise SystemExit(1)
PY
)" || {
    echo "ERROR|trivy_report_validation_failed" >&2
    exit 1
}

sha256sum "${OUTPUT_TAR}" > "${OUTPUT_SHA}"
chmod 0600 "${OUTPUT_TAR}" "${OUTPUT_SHA}" "${SCAN_REPORT}"
chown aiadmin:aiadmin "${OUTPUT_TAR}" "${OUTPUT_SHA}" "${SCAN_REPORT}"

echo "PASS|offline_overlay_built|${IMAGE_REF}"
echo "PASS|trivy_offline_scan|${scan_summary}"
echo "PASS|trivy_database_snapshot_unchanged"
echo "IMAGE_ARCHIVE=${OUTPUT_TAR}"
echo "IMAGE_SHA256=$(awk '{print $1}' "${OUTPUT_SHA}")"
echo "RESULT=PASS"
