#!/usr/bin/env bash
set -Eeuo pipefail

readonly BACKUP_ROOT="/opt/rag_service/backups"
readonly CANDIDATE_FIREWALL="/root/security_phase1_firewall.sh"
readonly CANDIDATE_PROBE="/root/centos-ipsec-nat-keepalive-probe.sh"
readonly CANDIDATE_KEEPALIVE_UNIT="/root/centos-ipsec-nat-keepalive.service"
readonly TARGET_FIREWALL="/usr/local/sbin/ai-middle-office-firewall.sh"
readonly TARGET_PROBE="/usr/local/sbin/centos-ipsec-nat-keepalive-probe.sh"
readonly TARGET_KEEPALIVE_UNIT="/etc/systemd/system/ai-ipsec-nat-keepalive.service"
readonly FIREWALL_SERVICE="ai-middle-office-firewall.service"
readonly KEEPALIVE_SERVICE="ai-ipsec-nat-keepalive.service"
readonly TRANSIENT_KEEPALIVE="ai-ipsec-keepalive-live.service"
readonly BACKEND_IP="192.168.88.128/32"

add_runtime_dnat_fallback() {
  local source
  for source in 10.240.10.10/32 10.240.10.11/32
  do
    iptables -w 5 -C AI_MO_DOCKER \
      -s "$source" -p tcp -m tcp --dport 6379 \
      -m conntrack --ctdir ORIGINAL --ctorigdst "$BACKEND_IP" --ctorigdstport 6380 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-redis-dnat-rollback \
      -j RETURN >/dev/null 2>&1 || \
    iptables -w 5 -I AI_MO_DOCKER 5 \
      -s "$source" -p tcp -m tcp --dport 6379 \
      -m conntrack --ctdir ORIGINAL --ctorigdst "$BACKEND_IP" --ctorigdstport 6380 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-redis-dnat-rollback \
      -j RETURN

    iptables -w 5 -C AI_MO_DOCKER \
      -s "$source" -p tcp -m tcp --dport 9000 \
      -m conntrack --ctdir ORIGINAL --ctorigdst "$BACKEND_IP" --ctorigdstport 9002 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-minio-dnat-rollback \
      -j RETURN >/dev/null 2>&1 || \
    iptables -w 5 -I AI_MO_DOCKER 5 \
      -s "$source" -p tcp -m tcp --dport 9000 \
      -m conntrack --ctdir ORIGINAL --ctorigdst "$BACKEND_IP" --ctorigdstport 9002 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-minio-dnat-rollback \
      -j RETURN
  done
}

validate_backup_dir() {
  local backup_dir="$1"
  [[ "$backup_dir" == "${BACKUP_ROOT}/pre-hybrid-persistence-"* ]]
  [[ -f "$backup_dir/ai-middle-office-firewall.sh.before" ]]
}

start_transient_keepalive() {
  systemctl is-active --quiet "$TRANSIENT_KEEPALIVE" && return 0
  systemd-run \
    --unit="${TRANSIENT_KEEPALIVE%.service}" \
    --property=Restart=always \
    --property=RestartSec=5s \
    /root/centos-ipsec-nat-keepalive-probe.sh >/dev/null
}

rollback_install() {
  local backup_dir="$1"
  validate_backup_dir "$backup_dir" || { echo "Invalid backup directory." >&2; exit 1; }

  systemctl disable --now "$KEEPALIVE_SERVICE" >/dev/null 2>&1 || true
  rm -f "$TARGET_KEEPALIVE_UNIT" "$TARGET_PROBE"
  install -o root -g root -m 0750 \
    "$backup_dir/ai-middle-office-firewall.sh.before" "$TARGET_FIREWALL"
  systemctl daemon-reload
  systemctl restart "$FIREWALL_SERVICE"
  add_runtime_dnat_fallback
  start_transient_keepalive
  echo "Rolled back CentOS hybrid persistence using: $backup_dir"
}

apply_install() {
  local stamp backup_dir
  stamp="$(date +%Y%m%d_%H%M%S)"
  backup_dir="${BACKUP_ROOT}/pre-hybrid-persistence-${stamp}"

  [[ -f "$CANDIDATE_FIREWALL" && -f "$CANDIDATE_PROBE" && -f "$CANDIDATE_KEEPALIVE_UNIT" ]]
  [[ -f "$TARGET_FIREWALL" ]]
  [[ ! -e "$TARGET_PROBE" && ! -e "$TARGET_KEEPALIVE_UNIT" ]] || {
    echo "Refusing to overwrite an existing persistent keepalive installation." >&2
    exit 1
  }
  systemctl is-active --quiet "$FIREWALL_SERVICE"
  systemctl is-active --quiet ipsec.service

  mkdir -p "$backup_dir"
  chmod 0700 "$backup_dir"
  install -o root -g root -m 0600 "$TARGET_FIREWALL" \
    "$backup_dir/ai-middle-office-firewall.sh.before"
  cp -a /etc/systemd/system/ai-middle-office-firewall.service \
    "$backup_dir/ai-middle-office-firewall.service.before"
  iptables-save >"$backup_dir/iptables-save.before"
  systemctl cat "$FIREWALL_SERVICE" >"$backup_dir/firewall-service.before"
  sha256sum "$backup_dir"/* >"$backup_dir/SHA256SUMS"

  rollback_on_error() {
    local rc=$?
    trap - ERR
    rollback_install "$backup_dir" || true
    exit "$rc"
  }
  trap rollback_on_error ERR

  install -o root -g root -m 0750 "$CANDIDATE_FIREWALL" "$TARGET_FIREWALL"
  install -o root -g root -m 0750 "$CANDIDATE_PROBE" "$TARGET_PROBE"
  install -o root -g root -m 0644 "$CANDIDATE_KEEPALIVE_UNIT" "$TARGET_KEEPALIVE_UNIT"
  systemctl daemon-reload
  systemctl restart "$FIREWALL_SERVICE"
  systemctl enable --now "$KEEPALIVE_SERVICE"

  systemctl is-enabled "$FIREWALL_SERVICE"
  systemctl is-active "$FIREWALL_SERVICE"
  systemctl is-enabled "$KEEPALIVE_SERVICE"
  systemctl is-active "$KEEPALIVE_SERVICE"
  systemctl is-active ipsec.service

  for source in 10.240.10.10/32 10.240.10.11/32
  do
    iptables -w 5 -C AI_MO_DOCKER \
      -s "$source" -p tcp -m tcp --dport 6379 \
      -m conntrack --ctdir ORIGINAL --ctorigdst "$BACKEND_IP" --ctorigdstport 6380 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-redis-dnat -j RETURN
    iptables -w 5 -C AI_MO_DOCKER \
      -s "$source" -p tcp -m tcp --dport 9000 \
      -m conntrack --ctdir ORIGINAL --ctorigdst "$BACKEND_IP" --ctorigdstport 9002 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-minio-dnat -j RETURN
  done

  trap - ERR
  systemctl stop "$TRANSIENT_KEEPALIVE" >/dev/null 2>&1 || true
  printf '%s\n' "$backup_dir" >"${BACKUP_ROOT}/last-hybrid-persistence-backup"
  chmod 0644 "${BACKUP_ROOT}/last-hybrid-persistence-backup"
  echo "Backup: $backup_dir"
  echo "CentOS hybrid firewall and NAT keepalive persistence installed."
  iptables -w 5 -t filter -nvL AI_MO_DOCKER --line-numbers
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
