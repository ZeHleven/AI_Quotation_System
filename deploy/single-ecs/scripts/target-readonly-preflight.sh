#!/usr/bin/env bash
set -euo pipefail

readonly DATA_MOUNT="${AI_DATA_MOUNT:-/data}"
readonly DATA_ROOT="${AI_DATA_ROOT:-/data/ai-middle-office}"
readonly APP_NETWORK="${AI_APP_NETWORK:-ai-middle-office-app-net}"

status=0

pass() { printf 'PASS|%s|%s\n' "$1" "$2"; }
fail() { printf 'FAIL|%s|%s\n' "$1" "$2"; status=1; }

cpu_count="$(nproc)"
if (( cpu_count >= 4 )); then
  pass cpu "cores=${cpu_count}"
else
  fail cpu "cores=${cpu_count}; required>=4"
fi

available_kib="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
if (( available_kib >= 8 * 1024 * 1024 )); then
  pass memory "available_kib=${available_kib}"
else
  fail memory "available_kib=${available_kib}; required>=8388608 before migration"
fi

if findmnt -rn -T "${DATA_MOUNT}" >/dev/null 2>&1; then
  data_source="$(findmnt -rn -o SOURCE -T "${DATA_MOUNT}")"
  data_available_kib="$(df -Pk "${DATA_MOUNT}" | awk 'NR==2 {print $4}')"
  if (( data_available_kib >= 60 * 1024 * 1024 )); then
    pass data_disk "source=${data_source}; available_kib=${data_available_kib}"
  else
    fail data_disk "source=${data_source}; available_kib=${data_available_kib}; required>=62914560"
  fi
else
  fail data_disk "${DATA_MOUNT} is not mounted"
fi

root_available_kib="$(df -Pk / | awk 'NR==2 {print $4}')"
if (( root_available_kib >= 30 * 1024 * 1024 )); then
  pass root_disk "available_kib=${root_available_kib}"
else
  fail root_disk "available_kib=${root_available_kib}; required>=31457280"
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  pass docker "daemon=reachable"
else
  fail docker "daemon is not reachable as current user; run this script with sudo"
fi

if docker network inspect "${APP_NETWORK}" >/dev/null 2>&1; then
  network_subnet="$(docker network inspect "${APP_NETWORK}" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}')"
  pass app_network "name=${APP_NETWORK}; subnet=${network_subnet}"
else
  fail app_network "missing=${APP_NETWORK}"
fi

for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  if docker inspect "${container}" >/dev/null 2>&1; then
    state="$(docker inspect "${container}" --format '{{.State.Status}}')"
    image_id="$(docker inspect "${container}" --format '{{.Image}}')"
    pass app_container "name=${container}; state=${state}; image=${image_id}"
  else
    fail app_container "missing=${container}"
  fi
done

if [[ -e "${DATA_ROOT}" ]]; then
  pass data_root "exists=${DATA_ROOT}"
else
  pass data_root "not_created_yet=${DATA_ROOT}"
fi

if (( status == 0 )); then
  printf 'RESULT|target_readonly_preflight=passed\n'
else
  printf 'RESULT|target_readonly_preflight=failed\n'
fi

exit "${status}"
