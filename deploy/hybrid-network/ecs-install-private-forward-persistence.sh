#!/usr/bin/env bash
set -Eeuo pipefail

readonly BACKUP_ROOT="/home/aiadmin/ai-hybrid-backups"
readonly CANDIDATE_SCRIPT="/home/aiadmin/ecs-private-forward-firewall.sh"
readonly CANDIDATE_UNIT="/home/aiadmin/ecs-private-forward-firewall.service"
readonly TARGET_SCRIPT="/usr/local/sbin/ai-middle-office-private-forward-firewall.sh"
readonly TARGET_UNIT="/etc/systemd/system/ai-middle-office-private-forward-firewall.service"
readonly SERVICE="ai-middle-office-private-forward-firewall.service"
readonly CURRENT_SOURCE="14.218.34.192/32"
readonly STALE_SOURCE="120.229.193.76/32"

declare -A RICH_RULES=(
  [current_ssh]="rule family=\"ipv4\" source address=\"${CURRENT_SOURCE}\" port port=\"22\" protocol=\"tcp\" accept"
  [current_ike]="rule family=\"ipv4\" source address=\"${CURRENT_SOURCE}\" port port=\"500\" protocol=\"udp\" accept"
  [current_natt]="rule family=\"ipv4\" source address=\"${CURRENT_SOURCE}\" port port=\"4500\" protocol=\"udp\" accept"
  [stale_ssh]="rule family=\"ipv4\" source address=\"${STALE_SOURCE}\" port port=\"22\" protocol=\"tcp\" accept"
  [stale_ike]="rule family=\"ipv4\" source address=\"${STALE_SOURCE}\" port port=\"500\" protocol=\"udp\" accept"
  [stale_natt]="rule family=\"ipv4\" source address=\"${STALE_SOURCE}\" port port=\"4500\" protocol=\"udp\" accept"
)

fw_query() {
  local scope="$1" label="$2" args=()
  [[ "$scope" == permanent ]] && args+=(--permanent)
  firewall-cmd "${args[@]}" --zone=public --query-rich-rule="${RICH_RULES[$label]}" >/dev/null
}

fw_add() {
  local scope="$1" label="$2" args=()
  [[ "$scope" == permanent ]] && args+=(--permanent)
  fw_query "$scope" "$label" || \
    firewall-cmd "${args[@]}" --zone=public --add-rich-rule="${RICH_RULES[$label]}" >/dev/null
}

fw_remove() {
  local scope="$1" label="$2" args=()
  [[ "$scope" == permanent ]] && args+=(--permanent)
  fw_query "$scope" "$label" && \
    firewall-cmd "${args[@]}" --zone=public --remove-rich-rule="${RICH_RULES[$label]}" >/dev/null || true
}

capture_targeted_firewalld_state() {
  local file="$1" scope label present
  : >"$file"
  for scope in runtime permanent
  do
    for label in current_ssh current_ike current_natt stale_ssh stale_ike stale_natt
    do
      present=0
      fw_query "$scope" "$label" && present=1
      printf '%s\t%s\t%s\n' "$scope" "$label" "$present" >>"$file"
    done
  done
}

restore_targeted_firewalld_state() {
  local file="$1" scope label present
  while IFS=$'\t' read -r scope label present
  do
    if [[ "$present" == 1 ]]; then
      fw_add "$scope" "$label"
    else
      fw_remove "$scope" "$label"
    fi
  done <"$file"
}

remove_rule_all() {
  local table="$1" chain="$2"
  shift 2
  while iptables -w 5 -t "$table" -C "$chain" "$@" >/dev/null 2>&1
  do
    iptables -w 5 -t "$table" -D "$chain" "$@"
  done
}

restore_validated_runtime_rules() {
  local ports="3306,5678,6380,8001,9002" backend="192.168.88.128"
  local cidr="10.240.10.10/31" api="10.240.10.10" worker="10.240.10.11"
  local in_notrack=(
    -i eth0 -s "${backend}/32" -d "$cidr" -p tcp -m multiport --sports "$ports"
    -m policy --dir in --pol ipsec --mode tunnel -j CT --notrack
  )
  local in_accept=(
    -i eth0 -s "${backend}/32" -d "$cidr" -p tcp -m multiport --sports "$ports"
    -m policy --dir in --pol ipsec --mode tunnel -j ACCEPT
  )
  local out_notrack=(
    -i br-ai-app -s "$cidr" -d "${backend}/32" -p tcp -m multiport --dports "$ports"
    -j CT --notrack
  )
  local api_return=(
    -s "${backend}/32" -d "${api}/32" -p tcp -m multiport --sports "$ports"
    -m policy --dir in --pol ipsec --mode tunnel -j ACCEPT
  )
  local worker_return=(
    -s "${backend}/32" -d "${worker}/32" -p tcp -m multiport --sports "$ports"
    -m policy --dir in --pol ipsec --mode tunnel -j ACCEPT
  )

  remove_rule_all raw PREROUTING "${in_notrack[@]}"
  remove_rule_all raw PREROUTING "${in_accept[@]}"
  remove_rule_all raw PREROUTING "${out_notrack[@]}"
  iptables -w 5 -t raw -I PREROUTING 1 "${out_notrack[@]}"
  iptables -w 5 -t raw -I PREROUTING 1 "${in_accept[@]}"
  iptables -w 5 -t raw -I PREROUTING 1 "${in_notrack[@]}"

  if iptables -w 5 -nL DOCKER-USER >/dev/null 2>&1; then
    remove_rule_all filter DOCKER-USER "${api_return[@]}"
    remove_rule_all filter DOCKER-USER "${worker_return[@]}"
    iptables -w 5 -I DOCKER-USER 1 "${api_return[@]}"
    iptables -w 5 -I DOCKER-USER 1 "${worker_return[@]}"
  fi
}

validate_backup_dir() {
  local backup_dir="$1"
  [[ "$backup_dir" == "${BACKUP_ROOT}/pre-private-forward-persistence-"* ]]
  [[ -f "$backup_dir/firewalld-target-state.tsv" ]]
}

rollback_install() {
  local backup_dir="$1"
  validate_backup_dir "$backup_dir" || { echo "Invalid backup directory." >&2; exit 1; }

  systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
  rm -f "$TARGET_UNIT" "$TARGET_SCRIPT"
  systemctl daemon-reload
  restore_targeted_firewalld_state "$backup_dir/firewalld-target-state.tsv"
  restore_validated_runtime_rules
  echo "Rolled back ECS hybrid persistence using: $backup_dir"
}

apply_install() {
  local stamp backup_dir state_file
  stamp="$(date +%Y%m%d_%H%M%S)"
  backup_dir="${BACKUP_ROOT}/pre-private-forward-persistence-${stamp}"
  state_file="$backup_dir/firewalld-target-state.tsv"

  [[ -f "$CANDIDATE_SCRIPT" && -f "$CANDIDATE_UNIT" ]]
  [[ ! -e "$TARGET_SCRIPT" && ! -e "$TARGET_UNIT" ]] || {
    echo "Refusing to overwrite an existing persistence installation." >&2
    exit 1
  }
  firewall-cmd --state >/dev/null
  iptables -w 5 -nL DOCKER-USER >/dev/null

  mkdir -p "$backup_dir"
  chmod 0700 "$backup_dir"
  iptables-save >"$backup_dir/iptables-save.before"
  nft -a list ruleset >"$backup_dir/nft-ruleset.before"
  firewall-cmd --zone=public --list-all >"$backup_dir/firewalld-public-runtime.before"
  firewall-cmd --permanent --zone=public --list-all >"$backup_dir/firewalld-public-permanent.before"
  capture_targeted_firewalld_state "$state_file"
  sha256sum "$backup_dir"/*.before "$state_file" >"$backup_dir/SHA256SUMS"

  rollback_on_error() {
    local rc=$?
    trap - ERR
    rollback_install "$backup_dir" || true
    exit "$rc"
  }
  trap rollback_on_error ERR

  for scope in runtime permanent
  do
    fw_add "$scope" current_ssh
    fw_add "$scope" current_ike
    fw_add "$scope" current_natt
    fw_remove "$scope" stale_ssh
    fw_remove "$scope" stale_ike
    fw_remove "$scope" stale_natt
  done

  install -o root -g root -m 0750 "$CANDIDATE_SCRIPT" "$TARGET_SCRIPT"
  install -o root -g root -m 0644 "$CANDIDATE_UNIT" "$TARGET_UNIT"
  systemctl daemon-reload
  systemctl enable --now "$SERVICE"

  systemctl is-enabled "$SERVICE"
  systemctl is-active "$SERVICE"
  for scope in runtime permanent
  do
    fw_query "$scope" current_ssh
    fw_query "$scope" current_ike
    fw_query "$scope" current_natt
    if fw_query "$scope" stale_ssh || \
       fw_query "$scope" stale_ike || \
       fw_query "$scope" stale_natt
    then
      echo "A stale source rule is still present in $scope firewalld state." >&2
      return 1
    fi
  done

  trap - ERR
  printf '%s\n' "$backup_dir" >"${BACKUP_ROOT}/last-private-forward-persistence-backup"
  chmod 0644 "${BACKUP_ROOT}/last-private-forward-persistence-backup"
  echo "Backup: $backup_dir"
  echo "ECS hybrid forwarding persistence installed without reloading firewalld."
  "$TARGET_SCRIPT" status
}

[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

case "${1:-apply}" in
  apply)
    apply_install
    ;;
  rollback)
    [[ $# -eq 2 ]] || { echo "rollback requires a backup directory." >&2; exit 2; }
    rollback_install "$2"
    ;;
  *)
    printf 'usage: %s {apply|rollback BACKUP_DIR}\n' "$0" >&2
    exit 2
    ;;
esac
