#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly CUTOVER_SCRIPT="/root/target-phase1-production-cutover.sh"
readonly CUTOVER_OLD_SHA256="5d4f72babbaab0c71c03024d8faf634383b28b0856eff603c80acf869e4dbbe0"
readonly CUTOVER_NEW_SHA256="df4369ad97d180182af033eac6b93c0c9677bba0c81e67df7a43eb191d7cb928"
readonly CUTOVER_NEW_SIZE="37247"
readonly API_CONTAINER="ai-middle-office-app-api-1"
readonly WORKER_CONTAINER="ai-middle-office-app-worker-1"
readonly N8N_CONTAINER="n8n"
readonly OLD_IMAGE="ai-middle-office-app:20260805_161737"
readonly N8N_ENV="/etc/ai-middle-office/n8n.env"
readonly N8N_DATA="/data/ai-middle-office/n8n"
readonly CANDIDATE_WORKFLOW_ID="QpP1Cand20260808"
readonly PRODUCTION_WORKFLOW_ID="UPGK6O16kr0xtO9z"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly FILE_PREFIX=".phase1-fixed409-${STAMP}"
readonly SOURCE_HOST="${N8N_DATA}/${FILE_PREFIX}-source.json"
readonly PATCHED_HOST="${N8N_DATA}/${FILE_PREFIX}-patched.json"
readonly VERIFY_CANDIDATE_HOST="${N8N_DATA}/${FILE_PREFIX}-verify-candidate.json"
readonly VERIFY_PRODUCTION_HOST="${N8N_DATA}/${FILE_PREFIX}-verify-production.json"
readonly SOURCE_CONTAINER="/home/node/.n8n/${FILE_PREFIX}-source.json"
readonly PATCHED_CONTAINER="/home/node/.n8n/${FILE_PREFIX}-patched.json"
readonly VERIFY_CANDIDATE_CONTAINER="/home/node/.n8n/${FILE_PREFIX}-verify-candidate.json"
readonly VERIFY_PRODUCTION_CONTAINER="/home/node/.n8n/${FILE_PREFIX}-verify-production.json"
readonly BACKUP_DIR="/data/ai-middle-office/backups/quote-consistency-phase1/n8n-fixed409-${STAMP}"
readonly N8N_BACKUP="${BACKUP_DIR}/n8n-before-fixed409.tar.gz"

n8n_image_id=""
n8n_uid=""
n8n_gid=""
repair_mutated=false
repair_success=false
nginx_was_active=false

log() {
  printf '%s\n' "$*"
}

container_running() {
  [[ "$(docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null || true)" == "true" ]]
}

wait_n8n() {
  local state health _attempt
  for _attempt in $(seq 1 60); do
    state="$(docker inspect "${N8N_CONTAINER}" --format '{{.State.Status}}' 2>/dev/null || true)"
    health="$(docker inspect "${N8N_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
    if [[ "${state}" == "running" && "${health}" == "healthy" ]]; then
      return 0
    fi
    if [[ "${state}" == "exited" || "${state}" == "dead" ]]; then
      return 1
    fi
    sleep 3
  done
  return 1
}

run_n8n_cli_cold() {
  docker run --rm \
    --volumes-from "${N8N_CONTAINER}" \
    --env-file "${N8N_ENV}" \
    --entrypoint n8n \
    "${n8n_image_id}" "$@"
}

restore_source_candidate() {
  if [[ "${repair_mutated}" != true || ! -f "${SOURCE_HOST}" ]]; then
    return 0
  fi
  if container_running "${N8N_CONTAINER}"; then
    docker stop --time 90 "${N8N_CONTAINER}" >/dev/null 2>&1 || true
  fi
  run_n8n_cli_cold import:workflow --input="${SOURCE_CONTAINER}" >/dev/null 2>&1 || true
  docker start "${N8N_CONTAINER}" >/dev/null 2>&1 || true
  wait_n8n >/dev/null 2>&1 || true
}

on_exit() {
  local rc=$?
  trap - EXIT INT TERM HUP
  set +e
  if [[ "${repair_success}" == true ]]; then
    exit "${rc}"
  fi
  log "ROLLBACK|n8n_fixed409_repair=begin|exit=${rc}"
  restore_source_candidate
  if ! container_running "${N8N_CONTAINER}"; then
    docker start "${N8N_CONTAINER}" >/dev/null 2>&1 || true
  fi
  wait_n8n >/dev/null 2>&1 || true
  if [[ "${nginx_was_active}" == true ]] && container_running "${N8N_CONTAINER}" && nginx -t >/dev/null 2>&1; then
    systemctl start nginx >/dev/null 2>&1 || true
  fi
  log "ROLLBACK|n8n_fixed409_repair=attempted|backup=${N8N_BACKUP}"
  exit "${rc}"
}
trap on_exit EXIT

on_signal() {
  log "ERROR|signal_received|signal=$1"
  exit 130
}
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

[[ "${EUID}" -eq 0 ]] || {
  log "ERROR|root_required"
  exit 1
}
for command in docker python3 sha256sum stat tar gzip systemctl nginx curl seq awk grep mktemp flock; do
  command -v "${command}" >/dev/null || {
    log "ERROR|required_command_missing|${command}"
    exit 1
  }
done
docker compose version >/dev/null
exec 8>/root/.quote-consistency-phase1-fixed409.lock
flock -n 8 || {
  log "ERROR|another_fixed409_repair_is_running"
  exit 1
}
exec 9>/root/.quote-consistency-phase1-cutover.lock
flock -n 9 || {
  log "ERROR|another_phase1_cutover_is_running"
  exit 1
}

[[ -f "${CUTOVER_SCRIPT}" && -f "${N8N_ENV}" ]] || {
  log "ERROR|required_file_missing"
  exit 1
}
for container in "${API_CONTAINER}" "${WORKER_CONTAINER}" "${N8N_CONTAINER}"; do
  container_running "${container}" || {
    log "ERROR|required_container_not_running|${container}"
    exit 1
  }
done
[[ "$(docker inspect "${API_CONTAINER}" --format '{{.Config.Image}}')" == "${OLD_IMAGE}" ]] || {
  log "ERROR|unexpected_api_image_before_repair"
  exit 1
}
[[ "$(docker inspect "${WORKER_CONTAINER}" --format '{{.Config.Image}}')" == "${OLD_IMAGE}" ]] || {
  log "ERROR|unexpected_worker_image_before_repair"
  exit 1
}
wait_n8n || {
  log "ERROR|n8n_not_healthy_before_repair"
  exit 1
}
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]] || {
  log "ERROR|nginx_not_active_before_repair"
  exit 1
}
nginx_was_active=true

current_cutover_sha256="$(sha256sum "${CUTOVER_SCRIPT}" | awk '{print $1}')"
log "RESULT|current_cutover_script_sha256=${current_cutover_sha256}"
if [[ "${current_cutover_sha256}" == "${CUTOVER_OLD_SHA256}" ]]; then
  python3 - "${CUTOVER_SCRIPT}" "${CUTOVER_OLD_SHA256}" "${CUTOVER_NEW_SHA256}" <<'PY'
import hashlib
import os
from pathlib import Path
import sys
import tempfile


path = Path(sys.argv[1])
expected_old = sys.argv[2]
expected_new = sys.argv[3]
source = path.read_bytes()
if hashlib.sha256(source).hexdigest() != expected_old:
    raise SystemExit("ERROR|cutover_patch_input_sha256_changed")

replacements = (
    (
        b'''    existing_response = by_name["Phase1 Respond Existing State"].get("parameters", {})
    if "409" not in json.dumps(existing_response, ensure_ascii=False):
        raise SystemExit("ERROR|candidate_duplicate_response_not_conflict")
''',
        b'''    existing_response = by_name["Phase1 Respond Existing State"].get("parameters", {})
    response_options = existing_response.get("options") or {}
    if response_options.get("responseCode") != 409:
        raise SystemExit("ERROR|candidate_duplicate_response_not_fixed_conflict")
''',
    ),
    (
        b'''    if http_status not in {200, 409}:
        raise SystemExit(f"ERROR|n8n_duplicate_gate_status_unexpected|http={http_status}")
    try:
        response_payload = json.loads(response_body)
        if isinstance(response_payload, str):
            response_payload = json.loads(response_payload)
    except (TypeError, ValueError) as error:
        raise SystemExit("ERROR|n8n_duplicate_gate_response_not_json") from error
    if not isinstance(response_payload, dict) or response_payload.get("action") != "in_progress":
        raise SystemExit("ERROR|n8n_duplicate_gate_action_mismatch")
    if response_payload.get("attempt_status") != PUSH_N8N_CLAIMED:
        raise SystemExit("ERROR|n8n_duplicate_gate_attempt_status_mismatch")
''',
        b'''    if http_status != 409:
        raise SystemExit(f"ERROR|n8n_duplicate_gate_status_unexpected|http={http_status}")
''',
    ),
    (
        b'''        f"duplicate_gate=blocked_before_delivery|http={http_status}|action=in_progress|"
        f"synthetic_row=removed",
''',
        b'''        f"duplicate_gate=fixed_409_before_delivery|http={http_status}|"
        f"db_status={PUSH_N8N_CLAIMED}|response_bytes={len(response_body)}|"
        f"synthetic_row=removed",
''',
    ),
)
patched = source
for old, new in replacements:
    if patched.count(old) != 1:
        raise SystemExit("ERROR|cutover_patch_anchor_count_mismatch")
    patched = patched.replace(old, new, 1)
if hashlib.sha256(patched).hexdigest() != expected_new:
    raise SystemExit("ERROR|cutover_patch_sha256_mismatch_before_write")

backup = path.with_name(path.name + ".before-fixed409-gate")
if backup.exists():
    if hashlib.sha256(backup.read_bytes()).hexdigest() != expected_old:
        raise SystemExit("ERROR|cutover_patch_backup_sha256_mismatch")
else:
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(source)
        handle.flush()
        os.fsync(handle.fileno())

descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(patched)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), path.stat().st_mode & 0o777)
    os.replace(temporary_name, path)
except BaseException:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise
print("PASS|fixed409_cutover_gate=patched_atomically", flush=True)
PY
elif [[ "${current_cutover_sha256}" == "${CUTOVER_NEW_SHA256}" ]]; then
  log "PASS|fixed409_cutover_gate=already_patched"
else
  log "ERROR|unexpected_cutover_script_sha256|actual=${current_cutover_sha256}"
  exit 1
fi

patched_cutover_sha256="$(sha256sum "${CUTOVER_SCRIPT}" | awk '{print $1}')"
patched_cutover_size="$(stat -c '%s' "${CUTOVER_SCRIPT}")"
[[ "${patched_cutover_sha256}" == "${CUTOVER_NEW_SHA256}" && "${patched_cutover_size}" == "${CUTOVER_NEW_SIZE}" ]] || {
  log "ERROR|fixed409_cutover_script_gate_failed|sha256=${patched_cutover_sha256}|size=${patched_cutover_size}"
  exit 1
}
bash -n "${CUTOVER_SCRIPT}"
log "PASS|fixed409_cutover_script_gate|sha256=${patched_cutover_sha256}|size=${patched_cutover_size}"

n8n_image_id="$(docker inspect "${N8N_CONTAINER}" --format '{{.Image}}')"
n8n_uid="$(docker exec "${N8N_CONTAINER}" id -u)"
n8n_gid="$(docker exec "${N8N_CONTAINER}" id -g)"
[[ "${n8n_image_id}" =~ ^sha256:[0-9a-f]{64}$ && "${n8n_uid}" =~ ^[0-9]+$ && "${n8n_gid}" =~ ^[0-9]+$ ]] || {
  log "ERROR|n8n_runtime_identity_invalid"
  exit 1
}

docker exec "${N8N_CONTAINER}" n8n export:workflow \
  --id="${CANDIDATE_WORKFLOW_ID}" --output="${SOURCE_CONTAINER}" >/dev/null
docker exec "${N8N_CONTAINER}" n8n export:workflow \
  --id="${PRODUCTION_WORKFLOW_ID}" --output="${VERIFY_PRODUCTION_CONTAINER}" >/dev/null

python3 - "${SOURCE_HOST}" "${PATCHED_HOST}" "${VERIFY_PRODUCTION_HOST}" <<'PY'
import json
import os
from pathlib import Path
import sys


source_path, patched_path, production_path = map(Path, sys.argv[1:])


def one_workflow(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("data", [raw])
    if len(items) != 1:
        raise SystemExit("ERROR|workflow_export_count_mismatch")
    return items[0]


candidate = one_workflow(source_path)
production = one_workflow(production_path)
if candidate.get("id") != "QpP1Cand20260808" or bool(candidate.get("active")):
    raise SystemExit("ERROR|candidate_identity_or_state_invalid")
if production.get("id") != "UPGK6O16kr0xtO9z" or not bool(production.get("active")):
    raise SystemExit("ERROR|production_identity_or_state_invalid")
nodes = candidate.get("nodes") or []
if len(nodes) != 21:
    raise SystemExit("ERROR|candidate_node_count_invalid")
matches = [node for node in nodes if node.get("name") == "Phase1 Respond Existing State"]
if len(matches) != 1:
    raise SystemExit("ERROR|candidate_response_node_invalid")
parameters = matches[0].setdefault("parameters", {})
options = parameters.setdefault("options", {})
old_code = options.get("responseCode")
if old_code != "={{ $json.action === 'delivered' ? 200 : 409 }}" and old_code != 409:
    raise SystemExit("ERROR|candidate_response_code_unexpected")
options["responseCode"] = 409
rendered = json.dumps([candidate], ensure_ascii=False, indent=2) + "\n"
temporary = patched_path.with_name(patched_path.name + ".tmp")
temporary.write_text(rendered, encoding="utf-8", newline="\n")
os.chmod(temporary, 0o600)
os.replace(temporary, patched_path)
print(f"RESULT|candidate_response_code_before={old_code!r}", flush=True)
print("PASS|candidate_fixed409_definition=prepared", flush=True)
PY
chown "${n8n_uid}:${n8n_gid}" "${SOURCE_HOST}" "${PATCHED_HOST}" "${VERIFY_PRODUCTION_HOST}"
chmod 0600 "${SOURCE_HOST}" "${PATCHED_HOST}" "${VERIFY_PRODUCTION_HOST}"

systemctl stop nginx
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]] || {
  log "ERROR|nginx_failed_to_stop_for_n8n_repair"
  exit 1
}
log "PASS|public_ingress=frozen_for_n8n_repair"

docker stop --time 90 "${N8N_CONTAINER}" >/dev/null
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
tar --numeric-owner -C /data/ai-middle-office -czf "${N8N_BACKUP}" n8n
gzip -t "${N8N_BACKUP}"
n8n_backup_sha256="$(sha256sum "${N8N_BACKUP}" | awk '{print $1}')"
log "RESULT|n8n_fixed409_backup=${N8N_BACKUP}|sha256=${n8n_backup_sha256}|size=$(stat -c '%s' "${N8N_BACKUP}")"
log "PASS|n8n_fixed409_backup=cold_and_verified"

repair_mutated=true
run_n8n_cli_cold import:workflow --input="${PATCHED_CONTAINER}"
docker start "${N8N_CONTAINER}" >/dev/null
wait_n8n || {
  docker logs --tail 160 "${N8N_CONTAINER}" >&2 || true
  log "ERROR|n8n_health_timeout_after_fixed409_import"
  exit 1
}

docker exec "${N8N_CONTAINER}" n8n export:workflow \
  --id="${CANDIDATE_WORKFLOW_ID}" --output="${VERIFY_CANDIDATE_CONTAINER}" >/dev/null
docker exec "${N8N_CONTAINER}" n8n export:workflow \
  --id="${PRODUCTION_WORKFLOW_ID}" --output="${VERIFY_PRODUCTION_CONTAINER}" >/dev/null

python3 - "${VERIFY_CANDIDATE_HOST}" "${VERIFY_PRODUCTION_HOST}" <<'PY'
import json
from pathlib import Path
import sys


def one_workflow(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("data", [raw])
    if len(items) != 1:
        raise SystemExit("ERROR|workflow_verify_count_mismatch")
    return items[0]


candidate = one_workflow(sys.argv[1])
production = one_workflow(sys.argv[2])
if candidate.get("id") != "QpP1Cand20260808" or bool(candidate.get("active")):
    raise SystemExit("ERROR|candidate_verify_identity_or_state_invalid")
if production.get("id") != "UPGK6O16kr0xtO9z" or not bool(production.get("active")):
    raise SystemExit("ERROR|production_verify_identity_or_state_invalid")
matches = [node for node in candidate.get("nodes", []) if node.get("name") == "Phase1 Respond Existing State"]
if len(matches) != 1:
    raise SystemExit("ERROR|candidate_verify_response_node_invalid")
response_code = ((matches[0].get("parameters") or {}).get("options") or {}).get("responseCode")
if type(response_code) is not int or response_code != 409:
    raise SystemExit("ERROR|candidate_verify_fixed409_missing")
print("PASS|candidate_fixed409=verified_inactive", flush=True)
print("PASS|production_workflow=verified_active_unchanged", flush=True)
PY

nginx -t
systemctl start nginx
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]] || {
  log "ERROR|nginx_failed_to_restart_after_n8n_repair"
  exit 1
}
public_code="$(curl -ksS --max-time 20 --resolve 'www.qskingship.com:443:127.0.0.1' \
  -o /dev/null -w '%{http_code}' https://www.qskingship.com/login 2>/dev/null || true)"
[[ "${public_code}" == "200" ]] || {
  log "ERROR|public_login_failed_after_n8n_repair|http=${public_code}"
  exit 1
}

rm -f -- "${SOURCE_HOST}" "${PATCHED_HOST}" "${VERIFY_CANDIDATE_HOST}" "${VERIFY_PRODUCTION_HOST}"
repair_success=true
repair_mutated=false
log "RESULT|n8n_candidate_fixed409_repair=passed"
log "RESULT|public_login=http_200|n8n=healthy"
log "INFO|phase1_cutover=starting_after_verified_repair"

trap - EXIT INT TERM HUP
flock -u 9
exec 9>&-
flock -u 8
exec 8>&-
exec /usr/bin/timeout --signal=TERM --kill-after=600s 3000s /usr/bin/bash "${CUTOVER_SCRIPT}"
