#!/usr/bin/env bash
set -Eeuo pipefail

readonly CANDIDATE="/root/centos-ai-middle-office-ipsec.passive.candidate"
readonly TARGET="/etc/ipsec.d/ai-middle-office-hybrid.conf"
readonly EXPECTED_SHA256="9298a903608719586524527505daf6eab664906d90bbe74b7576ad7b479832be"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: this installer must run as root" >&2
  exit 77
fi

actual_sha256="$(sha256sum "${CANDIDATE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
  echo "ERROR: candidate hash mismatch" >&2
  exit 1
fi

backup_dir="/opt/rag_service/backups/pre-ipsec-passive-liveness-$(date +%Y%m%d_%H%M%S)"
install -d -o root -g root -m 0700 "${backup_dir}"
cp -a "${TARGET}" "${backup_dir}/"

install -o root -g root -m 0600 "${CANDIDATE}" "${TARGET}"
restorecon "${TARGET}" 2>/dev/null || true

echo "BACKUP=${backup_dir}"
sha256sum "${TARGET}"

ipsec addconn --checkconfig
echo "checkconfig_rc=0"

systemctl restart ipsec
sleep 2

systemctl is-active ipsec
grep -nE '^[[:space:]]*(dpddelay|dpdtimeout|dpdaction|auto)=' "${TARGET}"
ipsec status | grep -E 'PARENT SA established|IPsec SA established|Traffic:' || true
