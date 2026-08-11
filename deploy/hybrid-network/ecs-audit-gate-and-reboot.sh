#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPORT="/home/aiadmin/ai-ecs-pre-reboot-audit.txt"
readonly TRACE_SCRIPT="/home/aiadmin/ecs-capture-private-forward-trace.sh"
readonly TARGET_SCRIPT="/usr/local/sbin/ai-middle-office-private-forward-firewall.sh"
readonly TARGET_UNIT="/etc/systemd/system/ai-middle-office-private-forward-firewall.service"
readonly SERVICE="ai-middle-office-private-forward-firewall.service"
readonly BACKUP_DIR="/home/aiadmin/ai-hybrid-backups/pre-private-forward-persistence-20260805_101131"
readonly CURRENT_SOURCE="14.218.34.192/32"
readonly STALE_SOURCE="120.229.193.76/32"
readonly TRACE_SHA256="9d8460048f56ed76632fcb5de43d944174fb4ac87ab392d3e158ff3b3a07e575"
readonly FIREWALL_SHA256="b6403473503d6d993797209f5520e876797de2745655ae12345ad7e426c2925c"
readonly UNIT_SHA256="618302c677a87e354a0668c62af75313f27c5737500d895757cdbb1194e52003"

current_ssh="rule family=\"ipv4\" source address=\"${CURRENT_SOURCE}\" port port=\"22\" protocol=\"tcp\" accept"
current_ike="rule family=\"ipv4\" source address=\"${CURRENT_SOURCE}\" port port=\"500\" protocol=\"udp\" accept"
current_natt="rule family=\"ipv4\" source address=\"${CURRENT_SOURCE}\" port port=\"4500\" protocol=\"udp\" accept"
stale_ssh="rule family=\"ipv4\" source address=\"${STALE_SOURCE}\" port port=\"22\" protocol=\"tcp\" accept"
stale_ike="rule family=\"ipv4\" source address=\"${STALE_SOURCE}\" port port=\"500\" protocol=\"udp\" accept"
stale_natt="rule family=\"ipv4\" source address=\"${STALE_SOURCE}\" port port=\"4500\" protocol=\"udp\" accept"

query_rule() {
  local scope="$1" rule="$2" args=()
  [[ "$scope" == permanent ]] && args+=(--permanent)
  firewall-cmd "${args[@]}" --zone=public --query-rich-rule="$rule" >/dev/null
}

verify_hash() {
  local path="$1" expected="$2" actual
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]]
}

[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
umask 022
exec > >(tee "$REPORT") 2>&1

echo "timestamp=$(date --iso-8601=seconds)"
sha256sum -c "$BACKUP_DIR/SHA256SUMS"
verify_hash "$TRACE_SCRIPT" "$TRACE_SHA256"
verify_hash "$TARGET_SCRIPT" "$FIREWALL_SHA256"
verify_hash "$TARGET_UNIT" "$UNIT_SHA256"

systemctl is-enabled "$SERVICE" firewalld docker ipsec
systemctl is-active "$SERVICE" firewalld docker ipsec

for scope in runtime permanent
do
  query_rule "$scope" "$current_ssh"
  query_rule "$scope" "$current_ike"
  query_rule "$scope" "$current_natt"
  if query_rule "$scope" "$stale_ssh" || \
     query_rule "$scope" "$stale_ike" || \
     query_rule "$scope" "$stale_natt"
  then
    echo "Stale source rule remains in $scope firewalld state." >&2
    exit 1
  fi
done

raw_count="$(iptables -w -t raw -S PREROUTING | grep -c -- '--comment ai-hybrid-')"
docker_count="$(iptables -w -S DOCKER-USER | grep -c -- '--comment ai-hybrid-')"
[[ "$raw_count" -eq 3 ]]
[[ "$docker_count" -eq 2 ]]

gate_output="$(bash "$TRACE_SCRIPT")"
printf '%s\n' "$gate_output"
trace_report="$(printf '%s\n' "$gate_output" | awk -F= '/^REPORT=/{print $2}' | tail -n 1)"
[[ -n "$trace_report" && -f "$trace_report/connectivity-gate.txt" ]]
grep -Fxq 'private_connectivity_gate=passed' "$trace_report/connectivity-gate.txt"

echo "pre_reboot_audit=passed"
echo "pre_reboot_trace=$trace_report"
chmod 0644 "$REPORT"
sync
echo "Rebooting ECS now for persistence verification."
systemctl reboot
