#!/usr/bin/env bash
set -Eeuo pipefail

readonly TRACE_SCRIPT="/home/aiadmin/ecs-capture-private-forward-trace.sh"
readonly FIREWALL_SCRIPT="/home/aiadmin/ecs-private-forward-firewall.sh"
readonly FIREWALL_UNIT="/home/aiadmin/ecs-private-forward-firewall.service"
readonly INSTALLER="/home/aiadmin/ecs-install-private-forward-persistence.sh"
readonly LAST_BACKUP_FILE="/home/aiadmin/ai-hybrid-backups/last-private-forward-persistence-backup"
readonly TRACE_SHA256="9d8460048f56ed76632fcb5de43d944174fb4ac87ab392d3e158ff3b3a07e575"
readonly FIREWALL_SHA256="b6403473503d6d993797209f5520e876797de2745655ae12345ad7e426c2925c"
readonly UNIT_SHA256="618302c677a87e354a0668c62af75313f27c5737500d895757cdbb1194e52003"
readonly INSTALLER_SHA256="8d1448a50ed20b6b2512ba9d42e7f63aacad69e36aa9aca268c0f9c5f3c85cee"

LAST_TRACE_REPORT=""

verify_file() {
  local path="$1" expected="$2" actual
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "Hash mismatch: $path" >&2
    exit 1
  }
}

run_gate() {
  local label="$1" output rc
  set +e
  output="$(bash "$TRACE_SCRIPT" 2>&1)"
  rc=$?
  set -e
  printf '%s\n' "$output"
  LAST_TRACE_REPORT="$(printf '%s\n' "$output" | awk -F= '/^REPORT=/{print $2}' | tail -n 1)"
  if [[ $rc -ne 0 || -z "$LAST_TRACE_REPORT" || \
        ! -f "$LAST_TRACE_REPORT/connectivity-gate.txt" ]]
  then
    echo "$label connectivity gate failed." >&2
    return 1
  fi
  if ! grep -Fxq 'private_connectivity_gate=passed' "$LAST_TRACE_REPORT/connectivity-gate.txt"
  then
    echo "$label connectivity gate failed." >&2
    return 1
  fi
  echo "$label connectivity gate passed: $LAST_TRACE_REPORT"
}

[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
verify_file "$TRACE_SCRIPT" "$TRACE_SHA256"
verify_file "$FIREWALL_SCRIPT" "$FIREWALL_SHA256"
verify_file "$FIREWALL_UNIT" "$UNIT_SHA256"
verify_file "$INSTALLER" "$INSTALLER_SHA256"

run_gate pre_persistence || exit 1

bash "$INSTALLER" apply
[[ -f "$LAST_BACKUP_FILE" ]]
backup_dir="$(<"$LAST_BACKUP_FILE")"

if ! run_gate post_persistence
then
  echo "Post-persistence gate failed; rolling back ECS persistence." >&2
  bash "$INSTALLER" rollback "$backup_dir"
  exit 1
fi

echo "ECS_BACKUP=$backup_dir"
echo "PRE_PERSISTENCE_TRACE=passed"
echo "POST_PERSISTENCE_TRACE=$LAST_TRACE_REPORT"
echo "ecs_private_forward_persistence=committed"
