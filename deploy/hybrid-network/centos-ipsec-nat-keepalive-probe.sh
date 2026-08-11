#!/usr/bin/env bash
set -u

readonly SOURCE_IP="192.168.88.128"
readonly TUNNEL_TARGET="10.240.10.1"

while true; do
  ping -n -q -c 1 -W 1 -I "${SOURCE_IP}" "${TUNNEL_TARGET}" \
    >/dev/null 2>&1 || true
  sleep 10
done
