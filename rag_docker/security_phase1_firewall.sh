#!/usr/bin/env bash
set -euo pipefail

ALLOWED_SOURCE="${AI_MIDDLE_OFFICE_ALLOWED_SOURCE:-192.168.88.1/32}"
VPN_API_SOURCE="${AI_MIDDLE_OFFICE_VPN_API_SOURCE:-10.240.10.10/32}"
VPN_WORKER_SOURCE="${AI_MIDDLE_OFFICE_VPN_WORKER_SOURCE:-10.240.10.11/32}"
VPN_BIND_ADDRESS="${AI_MIDDLE_OFFICE_VPN_BIND_ADDRESS:-192.168.88.128/32}"
IPT="${IPTABLES_BIN:-iptables}"
IP6T="${IP6TABLES_BIN:-ip6tables}"
INPUT_CHAIN=AI_MO_INPUT
DOCKER_CHAIN=AI_MO_DOCKER

PROTECTED_PORTS_A="80,443,1200,5003,5455,5678,6379,6380,8001,8443,9000,9001,9002,9003"
PROTECTED_PORTS_B="9091,9380:9384,18080,19530"
HOST_PORTS="22,111,3306"
VPN_BACKEND_PORTS="3306,5678,6380,8001,9002"

remove_jump() {
  local tool="$1" base_chain="$2" custom_chain="$3"
  while "$tool" -w 5 -C "$base_chain" -j "$custom_chain" >/dev/null 2>&1
  do
    "$tool" -w 5 -D "$base_chain" -j "$custom_chain"
  done
}

remove_chain() {
  local tool="$1" base_chain="$2" custom_chain="$3"
  remove_jump "$tool" "$base_chain" "$custom_chain"
  if "$tool" -w 5 -nL "$custom_chain" >/dev/null 2>&1; then
    "$tool" -w 5 -F "$custom_chain"
    "$tool" -w 5 -X "$custom_chain"
  fi
}

append_common_allows() {
  local tool="$1" chain="$2"
  "$tool" -w 5 -A "$chain" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  "$tool" -w 5 -A "$chain" -i lo -j RETURN
  "$tool" -w 5 -A "$chain" -i docker0 -j RETURN 2>/dev/null || true
  "$tool" -w 5 -A "$chain" -i 'br+' -j RETURN
}

append_protected_drops() {
  local tool="$1" chain="$2"
  "$tool" -w 5 -A "$chain" -p tcp -m multiport --dports "$PROTECTED_PORTS_A" -j DROP
  "$tool" -w 5 -A "$chain" -p tcp -m multiport --dports "$PROTECTED_PORTS_B" -j DROP
}

append_ipv4_vpn_allows() {
  local chain="$1" source
  for source in "$VPN_API_SOURCE" "$VPN_WORKER_SOURCE"
  do
    "$IPT" -w 5 -A "$chain" \
      -s "$source" \
      -p tcp -m multiport --dports "$VPN_BACKEND_PORTS" \
      -m policy --dir in --pol ipsec --mode tunnel \
      -j RETURN
  done
}

append_ipv4_vpn_dnat_allows() {
  local chain="$1" source
  for source in "$VPN_API_SOURCE" "$VPN_WORKER_SOURCE"
  do
    "$IPT" -w 5 -A "$chain" \
      -s "$source" \
      -p tcp -m tcp --dport 6379 \
      -m conntrack --ctdir ORIGINAL \
      --ctorigdst "$VPN_BIND_ADDRESS" --ctorigdstport 6380 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-redis-dnat \
      -j RETURN
    "$IPT" -w 5 -A "$chain" \
      -s "$source" \
      -p tcp -m tcp --dport 9000 \
      -m conntrack --ctdir ORIGINAL \
      --ctorigdst "$VPN_BIND_ADDRESS" --ctorigdstport 9002 \
      -m policy --dir in --pol ipsec --mode tunnel \
      -m comment --comment ai-hybrid-minio-dnat \
      -j RETURN
  done
}

apply_ipv4() {
  "$IPT" -w 5 -nL "$INPUT_CHAIN" >/dev/null 2>&1 || "$IPT" -w 5 -N "$INPUT_CHAIN"
  "$IPT" -w 5 -F "$INPUT_CHAIN"
  append_common_allows "$IPT" "$INPUT_CHAIN"
  append_ipv4_vpn_allows "$INPUT_CHAIN"
  "$IPT" -w 5 -A "$INPUT_CHAIN" -s "$ALLOWED_SOURCE" -j RETURN
  append_protected_drops "$IPT" "$INPUT_CHAIN"
  "$IPT" -w 5 -A "$INPUT_CHAIN" -p tcp -m multiport --dports "$HOST_PORTS" -j DROP
  "$IPT" -w 5 -A "$INPUT_CHAIN" -p udp --dport 111 -j DROP
  "$IPT" -w 5 -A "$INPUT_CHAIN" -j RETURN
  remove_jump "$IPT" INPUT "$INPUT_CHAIN"
  "$IPT" -w 5 -I INPUT 1 -j "$INPUT_CHAIN"

  if "$IPT" -w 5 -nL DOCKER-USER >/dev/null 2>&1; then
    "$IPT" -w 5 -nL "$DOCKER_CHAIN" >/dev/null 2>&1 || "$IPT" -w 5 -N "$DOCKER_CHAIN"
    "$IPT" -w 5 -F "$DOCKER_CHAIN"
    append_common_allows "$IPT" "$DOCKER_CHAIN"
    append_ipv4_vpn_allows "$DOCKER_CHAIN"
    append_ipv4_vpn_dnat_allows "$DOCKER_CHAIN"
    "$IPT" -w 5 -A "$DOCKER_CHAIN" -s "$ALLOWED_SOURCE" -j RETURN
    append_protected_drops "$IPT" "$DOCKER_CHAIN"
    "$IPT" -w 5 -A "$DOCKER_CHAIN" -j RETURN
    remove_jump "$IPT" DOCKER-USER "$DOCKER_CHAIN"
    "$IPT" -w 5 -I DOCKER-USER 1 -j "$DOCKER_CHAIN"
  fi
}

apply_ipv6() {
  command -v "$IP6T" >/dev/null 2>&1 || return 0
  "$IP6T" -w 5 -nL "$INPUT_CHAIN" >/dev/null 2>&1 || "$IP6T" -w 5 -N "$INPUT_CHAIN"
  "$IP6T" -w 5 -F "$INPUT_CHAIN"
  append_common_allows "$IP6T" "$INPUT_CHAIN"
  append_protected_drops "$IP6T" "$INPUT_CHAIN"
  "$IP6T" -w 5 -A "$INPUT_CHAIN" -p tcp -m multiport --dports "$HOST_PORTS" -j DROP
  "$IP6T" -w 5 -A "$INPUT_CHAIN" -p udp --dport 111 -j DROP
  "$IP6T" -w 5 -A "$INPUT_CHAIN" -j RETURN
  remove_jump "$IP6T" INPUT "$INPUT_CHAIN"
  "$IP6T" -w 5 -I INPUT 1 -j "$INPUT_CHAIN"
}

remove_rules() {
  remove_chain "$IPT" DOCKER-USER "$DOCKER_CHAIN"
  remove_chain "$IPT" INPUT "$INPUT_CHAIN"
  if command -v "$IP6T" >/dev/null 2>&1; then
    remove_chain "$IP6T" INPUT "$INPUT_CHAIN"
  fi
}

show_status() {
  "$IPT" -w 5 -S INPUT
  "$IPT" -w 5 -S DOCKER-USER 2>/dev/null || true
  "$IPT" -w 5 -S "$INPUT_CHAIN" 2>/dev/null || true
  "$IPT" -w 5 -S "$DOCKER_CHAIN" 2>/dev/null || true
  if command -v "$IP6T" >/dev/null 2>&1; then
    "$IP6T" -w 5 -S INPUT
    "$IP6T" -w 5 -S "$INPUT_CHAIN" 2>/dev/null || true
  fi
}

case "${1:-apply}" in
  apply)
    apply_ipv4
    apply_ipv6
    ;;
  remove)
    remove_rules
    ;;
  status)
    show_status
    ;;
  *)
    printf 'usage: %s {apply|remove|status}\n' "$0" >&2
    exit 2
    ;;
esac
