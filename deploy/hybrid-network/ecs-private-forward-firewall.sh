#!/usr/bin/env bash
set -Eeuo pipefail

readonly IPT="${IPTABLES_BIN:-iptables}"
readonly BACKEND_IP="192.168.88.128"
readonly APP_CIDR="10.240.10.10/31"
readonly API_IP="10.240.10.10"
readonly WORKER_IP="10.240.10.11"
readonly EXTERNAL_INTERFACE="eth0"
readonly APP_BRIDGE="br-ai-app"
readonly ALLOWED_PORTS="3306,5678,6380,8001,9002"

raw_in_notrack=(
  -i "${EXTERNAL_INTERFACE}" -s "${BACKEND_IP}/32" -d "${APP_CIDR}"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -m comment --comment ai-hybrid-in-notrack
  -j CT --notrack
)
raw_in_accept=(
  -i "${EXTERNAL_INTERFACE}" -s "${BACKEND_IP}/32" -d "${APP_CIDR}"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -m comment --comment ai-hybrid-in-raw-accept
  -j ACCEPT
)
raw_out_notrack=(
  -i "${APP_BRIDGE}" -s "${APP_CIDR}" -d "${BACKEND_IP}/32"
  -p tcp -m multiport --dports "${ALLOWED_PORTS}"
  -m comment --comment ai-hybrid-out-notrack
  -j CT --notrack
)

legacy_raw_in_notrack=(
  -i "${EXTERNAL_INTERFACE}" -s "${BACKEND_IP}/32" -d "${APP_CIDR}"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -j CT --notrack
)
legacy_raw_in_accept=(
  -i "${EXTERNAL_INTERFACE}" -s "${BACKEND_IP}/32" -d "${APP_CIDR}"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -j ACCEPT
)
legacy_raw_out_notrack=(
  -i "${APP_BRIDGE}" -s "${APP_CIDR}" -d "${BACKEND_IP}/32"
  -p tcp -m multiport --dports "${ALLOWED_PORTS}"
  -j CT --notrack
)

docker_api_return=(
  -i "${EXTERNAL_INTERFACE}" -o "${APP_BRIDGE}"
  -s "${BACKEND_IP}/32" -d "${API_IP}/32"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -m comment --comment ai-hybrid-api-return
  -j ACCEPT
)
docker_worker_return=(
  -i "${EXTERNAL_INTERFACE}" -o "${APP_BRIDGE}"
  -s "${BACKEND_IP}/32" -d "${WORKER_IP}/32"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -m comment --comment ai-hybrid-worker-return
  -j ACCEPT
)

legacy_docker_api_return=(
  -s "${BACKEND_IP}/32" -d "${API_IP}/32"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -j ACCEPT
)
legacy_docker_worker_return=(
  -s "${BACKEND_IP}/32" -d "${WORKER_IP}/32"
  -p tcp -m multiport --sports "${ALLOWED_PORTS}"
  -m policy --dir in --pol ipsec --mode tunnel
  -j ACCEPT
)

delete_all() {
  local table="$1" chain="$2"
  shift 2
  while "${IPT}" -w 5 -t "${table}" -C "${chain}" "$@" >/dev/null 2>&1
  do
    "${IPT}" -w 5 -t "${table}" -D "${chain}" "$@"
  done
}

remove_owned_rules() {
  delete_all raw PREROUTING "${raw_in_notrack[@]}"
  delete_all raw PREROUTING "${raw_in_accept[@]}"
  delete_all raw PREROUTING "${raw_out_notrack[@]}"
  if "${IPT}" -w 5 -nL DOCKER-USER >/dev/null 2>&1; then
    delete_all filter DOCKER-USER "${docker_api_return[@]}"
    delete_all filter DOCKER-USER "${docker_worker_return[@]}"
  fi
}

remove_legacy_rules() {
  delete_all raw PREROUTING "${legacy_raw_in_notrack[@]}"
  delete_all raw PREROUTING "${legacy_raw_in_accept[@]}"
  delete_all raw PREROUTING "${legacy_raw_out_notrack[@]}"
  if "${IPT}" -w 5 -nL DOCKER-USER >/dev/null 2>&1; then
    delete_all filter DOCKER-USER "${legacy_docker_api_return[@]}"
    delete_all filter DOCKER-USER "${legacy_docker_worker_return[@]}"
  fi
}

apply_rules() {
  "${IPT}" -w 5 -t raw -nL PREROUTING >/dev/null
  "${IPT}" -w 5 -nL DOCKER-USER >/dev/null

  remove_owned_rules
  remove_legacy_rules

  "${IPT}" -w 5 -t raw -I PREROUTING 1 "${raw_out_notrack[@]}"
  "${IPT}" -w 5 -t raw -I PREROUTING 1 "${raw_in_accept[@]}"
  "${IPT}" -w 5 -t raw -I PREROUTING 1 "${raw_in_notrack[@]}"

  "${IPT}" -w 5 -I DOCKER-USER 1 "${docker_api_return[@]}"
  "${IPT}" -w 5 -I DOCKER-USER 1 "${docker_worker_return[@]}"
}

show_status() {
  "${IPT}" -w 5 -t raw -nvL PREROUTING --line-numbers
  "${IPT}" -w 5 -nvL DOCKER-USER --line-numbers
}

[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

case "${1:-apply}" in
  apply)
    apply_rules
    show_status
    ;;
  remove)
    remove_owned_rules
    ;;
  status)
    show_status
    ;;
  *)
    printf 'usage: %s {apply|remove|status}\n' "$0" >&2
    exit 2
    ;;
esac
