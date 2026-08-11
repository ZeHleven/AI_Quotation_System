#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly STACK_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly COMPOSE_FILE="${STACK_DIR}/compose.backend.yaml"
readonly BACKEND_ENV="${BACKEND_ENV:-/etc/ai-middle-office/backend.env}"
readonly DATA_ROOT="${AI_DATA_ROOT:-/data/ai-middle-office}"
readonly N8N_ROOT="${DATA_ROOT}/n8n"
readonly BACKUP_ROOT="${N8N_BACKUP_ROOT:-/data/ai-middle-office-migration/target-safety}"
readonly N8N_IMAGE="docker.n8n.io/n8nio/n8n"
readonly REWRITE_JS="${SCRIPT_DIR}/target-n8n-rewrite-workflows.js"
readonly INVENTORY_WORKFLOW_ID="kHbeaP65zPcFmvZs"
readonly PUBLISH_IDS=(
  sc27NkNq3dgOH5L8
  ryHRy69WhkvelvRQ
  jiXOrZ7NZgl2Megd
)

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}
[[ -f "${COMPOSE_FILE}" ]] || {
  echo "ERROR|missing_compose|${COMPOSE_FILE}" >&2
  exit 1
}
[[ -f "${BACKEND_ENV}" ]] || {
  echo "ERROR|missing_backend_env|${BACKEND_ENV}" >&2
  exit 1
}
[[ -f "${REWRITE_JS}" ]] || {
  echo "ERROR|missing_rewrite_script|${REWRITE_JS}" >&2
  exit 1
}
[[ -s "${N8N_ROOT}/database.sqlite" ]] || {
  echo "ERROR|missing_n8n_database|${N8N_ROOT}/database.sqlite" >&2
  exit 1
}

if docker inspect n8n >/dev/null 2>&1 && [[ "$(docker inspect n8n --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|target_n8n_must_be_stopped" >&2
  exit 1
fi

exec 9>"/run/ai-middle-office-n8n-rewrite.lock"
flock -n 9 || {
  echo "ERROR|n8n_rewrite_already_running" >&2
  exit 1
}

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${BACKUP_ROOT}"
chmod 0700 "${BACKUP_ROOT}"
backup_file="${BACKUP_ROOT}/n8n-before-endpoint-rewrite-${timestamp}.tar.gz"
export_file="${N8N_ROOT}/.migration-export-${timestamp}.json"
patched_file="${N8N_ROOT}/.migration-patched-${timestamp}.json"

cleanup() {
  rm -f -- "${export_file}" "${patched_file}"
}
trap cleanup EXIT

tar -C "${DATA_ROOT}" -czf "${backup_file}" n8n
chmod 0600 "${backup_file}"
backup_sha256="$(sha256sum "${backup_file}" | awk '{print $1}')"
echo "RESULT|n8n_cold_backup=created|file=${backup_file}|sha256=${backup_sha256}"

compose=(docker compose --env-file "${BACKEND_ENV}" -f "${COMPOSE_FILE}")
"${compose[@]}" run --rm --no-deps --entrypoint n8n n8n \
  export:workflow --all --pretty --output="/home/node/.n8n/$(basename "${export_file}")" >/dev/null

docker run --rm --network none \
  --user 1000:1000 \
  -v "${N8N_ROOT}:/home/node/.n8n" \
  -v "${REWRITE_JS}:/work/rewrite.js:ro" \
  --entrypoint node \
  "${N8N_IMAGE}" \
  /work/rewrite.js \
  "/home/node/.n8n/$(basename "${export_file}")" \
  "/home/node/.n8n/$(basename "${patched_file}")"

"${compose[@]}" run --rm --no-deps --entrypoint n8n n8n \
  import:workflow --input="/home/node/.n8n/$(basename "${patched_file}")" >/dev/null

"${compose[@]}" run --rm --no-deps --entrypoint n8n n8n \
  unpublish:workflow --id="${INVENTORY_WORKFLOW_ID}" >/dev/null

for workflow_id in "${PUBLISH_IDS[@]}"; do
  "${compose[@]}" run --rm --no-deps --entrypoint n8n n8n \
    publish:workflow --id="${workflow_id}" >/dev/null
done

verify_file="${N8N_ROOT}/.migration-verify-${timestamp}.json"
trap 'rm -f -- "${export_file}" "${patched_file}" "${verify_file}"' EXIT
"${compose[@]}" run --rm --no-deps --entrypoint n8n n8n \
  export:workflow --all --pretty --output="/home/node/.n8n/$(basename "${verify_file}")" >/dev/null

docker run --rm --network none \
  --user 1000:1000 \
  -v "${N8N_ROOT}:/home/node/.n8n:ro" \
  --entrypoint node \
  "${N8N_IMAGE}" - "/home/node/.n8n/$(basename "${verify_file}")" <<'NODE'
const fs = require("fs");
const workflows = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const byId = new Map(workflows.map((workflow) => [workflow.id, workflow]));
const required = [
  "kHbeaP65zPcFmvZs",
  "sc27NkNq3dgOH5L8",
  "ryHRy69WhkvelvRQ",
  "jiXOrZ7NZgl2Megd",
  "UPGK6O16kr0xtO9z",
];
for (const id of required) {
  if (!byId.has(id)) throw new Error(`missing workflow after import: ${id}`);
}
const inventory = byId.get("kHbeaP65zPcFmvZs");
if (inventory.active) throw new Error("inventory schedule remains active");
for (const id of ["sc27NkNq3dgOH5L8", "ryHRy69WhkvelvRQ", "jiXOrZ7NZgl2Megd", "UPGK6O16kr0xtO9z"]) {
  if (!byId.get(id).active) throw new Error(`required workflow is inactive: ${id}`);
}
const serialized = JSON.stringify(workflows);
for (const forbidden of [
  "http://192.168.88.128:8001",
  "http://192.168.1.21:8001",
]) {
  if (serialized.includes(forbidden)) throw new Error(`old RAG endpoint remains: ${forbidden}`);
}
for (const expected of ["http://rag-service:8001", "http://dify-nginx"]) {
  if (!serialized.includes(expected)) throw new Error(`new internal endpoint missing: ${expected}`);
}
console.log("RESULT|n8n_inventory_schedule=unpublished");
console.log("RESULT|n8n_quote_workflows=published");
console.log("RESULT|n8n_internal_endpoints=verified");
NODE

echo "RESULT|target_n8n_offline_rewrite=passed"
echo "INFO|no_n8n_server_started"
echo "INFO|next_gate=target_n8n_dark_start"
