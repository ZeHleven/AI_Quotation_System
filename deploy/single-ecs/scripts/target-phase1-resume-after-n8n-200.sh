#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly CUTOVER_SCRIPT="${CUTOVER_SCRIPT:-/root/target-phase1-production-cutover.sh}"
readonly EXPECTED_OLD_SHA256="30c35d0a43eaead7ec7cb9c809aac5789be87c468a728632a6d8e66af4d7deac"
readonly EXPECTED_NEW_SHA256="5d4f72babbaab0c71c03024d8faf634383b28b0856eff603c80acf869e4dbbe0"
readonly EXPECTED_NEW_SIZE="37755"

[[ "${EUID}" -eq 0 ]] || {
  printf '%s\n' "ERROR|root_required"
  exit 1
}
[[ -f "${CUTOVER_SCRIPT}" ]] || {
  printf '%s\n' "ERROR|cutover_script_missing|path=${CUTOVER_SCRIPT}"
  exit 1
}

current_sha256="$(sha256sum "${CUTOVER_SCRIPT}" | awk '{print $1}')"
printf '%s\n' "RESULT|current_script_sha256=${current_sha256}"

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
    raise SystemExit("ERROR|patch_input_sha256_changed")

replacements = (
    (
        b'''    http_status = None
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            http_status = response.status
    except urllib.error.HTTPError as error:
        http_status = error.code
''',
        b'''    http_status = None
    response_body = b""
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            http_status = response.status
            response_body = response.read()
    except urllib.error.HTTPError as error:
        http_status = error.code
        response_body = error.read()
''',
    ),
    (
        b'''    if http_status != 409:
        raise SystemExit(f"ERROR|n8n_duplicate_gate_status_unexpected|http={http_status}")
''',
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
    ),
    (
        b'''        f"duplicate_gate=blocked_before_delivery|http={http_status}|synthetic_row=removed",
''',
        b'''        f"duplicate_gate=blocked_before_delivery|http={http_status}|action=in_progress|"
        f"synthetic_row=removed",
''',
    ),
)

patched = source
for old, new in replacements:
    if patched.count(old) != 1:
        raise SystemExit("ERROR|patch_anchor_count_mismatch")
    patched = patched.replace(old, new, 1)

if hashlib.sha256(patched).hexdigest() != expected_new:
    raise SystemExit("ERROR|patched_script_sha256_mismatch_before_write")

backup = path.with_name(path.name + ".before-n8n-response-body-gate")
if backup.exists():
    if hashlib.sha256(backup.read_bytes()).hexdigest() != expected_old:
        raise SystemExit("ERROR|existing_patch_backup_sha256_mismatch")
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

print("PASS|n8n_response_body_gate=patched_atomically", flush=True)
PY
elif [[ "${current_sha256}" == "${EXPECTED_NEW_SHA256}" ]]; then
  printf '%s\n' "PASS|n8n_response_body_gate=already_patched"
else
  printf '%s\n' "ERROR|unexpected_cutover_script_sha256|actual=${current_sha256}"
  exit 1
fi

patched_sha256="$(sha256sum "${CUTOVER_SCRIPT}" | awk '{print $1}')"
patched_size="$(stat -c '%s' "${CUTOVER_SCRIPT}")"
[[ "${patched_sha256}" == "${EXPECTED_NEW_SHA256}" ]] || {
  printf '%s\n' "ERROR|final_script_sha256_mismatch|actual=${patched_sha256}"
  exit 1
}
[[ "${patched_size}" == "${EXPECTED_NEW_SIZE}" ]] || {
  printf '%s\n' "ERROR|final_script_size_mismatch|actual=${patched_size}"
  exit 1
}
bash -n "${CUTOVER_SCRIPT}"
printf '%s\n' "PASS|resume_cutover_script_gate|sha256=${patched_sha256}|size=${patched_size}"

exec timeout --signal=TERM --kill-after=600s 3000s bash "${CUTOVER_SCRIPT}"
