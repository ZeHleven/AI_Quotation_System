#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly CUTOVER_SCRIPT="/root/target-phase1-production-cutover.sh"
readonly EXPECTED_OLD_SHA256="df4369ad97d180182af033eac6b93c0c9677bba0c81e67df7a43eb191d7cb928"
readonly EXPECTED_NEW_SHA256="abd01f40708f238d54d7282c2544e97aea120f0c38312c4f5bc187ab371bf720"
readonly EXPECTED_NEW_SIZE="38268"
readonly UNIT_NAME="quote-consistency-phase1-backend-gate-final"
readonly LOG_FILE="/root/quote-consistency-phase1-backend-gate-final.log"

log() {
  printf '%s\n' "$*"
}

[[ "${EUID}" -eq 0 ]] || {
  log "ERROR|root_required"
  exit 1
}
for command in awk bash python3 rm sha256sum stat systemctl systemd-run timeout; do
  command -v "${command}" >/dev/null || {
    log "ERROR|required_command_missing|${command}"
    exit 1
  }
done
[[ -f "${CUTOVER_SCRIPT}" ]] || {
  log "ERROR|cutover_script_missing|path=${CUTOVER_SCRIPT}"
  exit 1
}

current_sha256="$(sha256sum "${CUTOVER_SCRIPT}" | awk '{print $1}')"
log "RESULT|current_cutover_script_sha256=${current_sha256}"
if [[ "${current_sha256}" == "${EXPECTED_OLD_SHA256}" ]]; then
  python3 - "${CUTOVER_SCRIPT}" "${EXPECTED_OLD_SHA256}" "${EXPECTED_NEW_SHA256}" <<'PY'
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
    raise SystemExit("ERROR|backend_gate_patch_input_sha256_changed")

replacements = (
    (
        b'''    if claim_outputs[1][0].get("node") != "Phase1 Respond Existing State":
        raise SystemExit("ERROR|candidate_duplicate_branch_not_isolated")
    existing_response = by_name["Phase1 Respond Existing State"].get("parameters", {})
''',
        b'''    if claim_outputs[1][0].get("node") != "Phase1 Respond Existing State":
        raise SystemExit("ERROR|candidate_duplicate_branch_not_isolated")
    delivered_outputs = connections.get("Phase1 Mark Delivered", {}).get("main", [])
    if (
        len(delivered_outputs) != 1
        or len(delivered_outputs[0]) != 1
        or delivered_outputs[0][0].get("node") != "Phase1 Respond Delivered"
    ):
        raise SystemExit("ERROR|candidate_success_response_not_after_delivery_callback")
    existing_response = by_name["Phase1 Respond Existing State"].get("parameters", {})
''',
    ),
    (
        b'''    PUSH_N8N_CLAIMED,
    PUSH_SENDING,
    mark_quote_push_failed_before_dispatch,
''',
        b'''    PUSH_N8N_CLAIMED,
    PUSH_SENDING,
    QuoteConsistencyError,
    mark_quote_push_external_delivered,
    mark_quote_push_failed_before_dispatch,
''',
    ),
    (
        b'''    if http_status != 409:
        raise SystemExit(f"ERROR|n8n_duplicate_gate_status_unexpected|http={http_status}")

    mark_quote_push_failed_before_dispatch(
''',
        b'''    if http_status not in {200, 409}:
        raise SystemExit(f"ERROR|n8n_duplicate_gate_status_unexpected|http={http_status}")

    try:
        mark_quote_push_external_delivered(
            db,
            attempt_id=attempt.id,
            status_code=http_status,
            response_text="PHASE1_DUPLICATE_GATE_PROBE",
        )
    except QuoteConsistencyError as error:
        if str(error) != f"QUOTE_PUSH_NOT_SENDING_{PUSH_N8N_CLAIMED}":
            raise SystemExit(f"ERROR|backend_delivery_gate_unexpected|detail={error}") from error
        db.rollback()
    else:
        raise SystemExit("ERROR|backend_delivery_gate_accepted_unconfirmed_delivery")

    mark_quote_push_failed_before_dispatch(
''',
    ),
    (
        b'''        f"duplicate_gate=fixed_409_before_delivery|http={http_status}|"
        f"db_status={PUSH_N8N_CLAIMED}|response_bytes={len(response_body)}|"
        f"synthetic_row=removed",
''',
        b'''        f"duplicate_gate=blocked_before_delivery|http={http_status}|"
        f"db_status={PUSH_N8N_CLAIMED}|response_bytes={len(response_body)}|"
        f"backend_delivery_gate=rejected_unconfirmed_state|"
        f"synthetic_row=removed",
''',
    ),
)

patched = source
for old, new in replacements:
    if patched.count(old) != 1:
        raise SystemExit("ERROR|backend_gate_patch_anchor_count_mismatch")
    patched = patched.replace(old, new, 1)
if hashlib.sha256(patched).hexdigest() != expected_new:
    raise SystemExit("ERROR|backend_gate_patch_sha256_mismatch_before_write")

backup = path.with_name(path.name + ".before-backend-delivery-gate")
if backup.exists():
    if hashlib.sha256(backup.read_bytes()).hexdigest() != expected_old:
        raise SystemExit("ERROR|backend_gate_patch_backup_sha256_mismatch")
else:
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise

descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(patched)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), path.stat().st_mode & 0o777)
    os.replace(temporary_name, path)
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
except BaseException:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise

print("PASS|backend_delivery_gate=patched_atomically", flush=True)
PY
elif [[ "${current_sha256}" == "${EXPECTED_NEW_SHA256}" ]]; then
  log "PASS|backend_delivery_gate=already_patched"
else
  log "ERROR|unexpected_cutover_script_sha256|actual=${current_sha256}"
  exit 1
fi

patched_sha256="$(sha256sum "${CUTOVER_SCRIPT}" | awk '{print $1}')"
patched_size="$(stat -c '%s' "${CUTOVER_SCRIPT}")"
[[ "${patched_sha256}" == "${EXPECTED_NEW_SHA256}" && "${patched_size}" == "${EXPECTED_NEW_SIZE}" ]] || {
  log "ERROR|backend_gate_script_gate_failed|sha256=${patched_sha256}|size=${patched_size}"
  exit 1
}
bash -n "${CUTOVER_SCRIPT}"
python3 - "${CUTOVER_SCRIPT}" <<'PY'
import re
from pathlib import Path
import sys


source = Path(sys.argv[1]).read_text(encoding="utf-8")
blocks = re.findall(r"(?ms)<<'PY'\n(.*?)^PY\r?$", source)
if len(blocks) != 10:
    raise SystemExit(f"ERROR|python_heredoc_count_unexpected|count={len(blocks)}")
for index, block in enumerate(blocks, 1):
    compile(block, f"cutover-heredoc-{index}", "exec")
print(f"PASS|cutover_python_heredocs_compiled={len(blocks)}", flush=True)
PY
log "PASS|backend_gate_cutover_script_gate|sha256=${patched_sha256}|size=${patched_size}"

if [[ "$(systemctl is-active "${UNIT_NAME}.service" 2>/dev/null || true)" == "active" ]]; then
  log "ERROR|background_unit_already_active|unit=${UNIT_NAME}.service"
  exit 1
fi
systemctl reset-failed "${UNIT_NAME}.service" >/dev/null 2>&1 || true
rm -f -- "${LOG_FILE}"
systemd-run \
  --unit="${UNIT_NAME}" \
  --description="Quote consistency phase1 cutover with authoritative backend delivery gate" \
  --property="TimeoutStopSec=600" \
  --property="StandardOutput=append:${LOG_FILE}" \
  --property="StandardError=append:${LOG_FILE}" \
  /usr/bin/timeout --signal=TERM --kill-after=600s 3000s \
  /usr/bin/bash "${CUTOVER_SCRIPT}"

systemctl show "${UNIT_NAME}.service" \
  --property=Result \
  --property=ExecMainStatus \
  --property=ActiveState \
  --property=SubState \
  --no-pager
log "RESULT|background_unit=${UNIT_NAME}.service"
log "RESULT|background_log=${LOG_FILE}"
log "INFO|backend_gate_cutover=started"
