#!/usr/bin/env bash
set -euo pipefail

# Configure CentOS network and Docker services for unattended startup.
# Usage: sudo bash enable_centos_autostart.sh [iface]

IFACE="${1:-ens33}"
APP_DIR="${APP_DIR:-/opt/rag_service}"
IFCFG="/etc/sysconfig/network-scripts/ifcfg-${IFACE}"

if [ "$(id -u)" -ne 0 ]; then
  echo "[ERROR] Please run as root, for example: sudo bash $0 ${IFACE}"
  exit 1
fi

echo "[INFO] Configuring interface: ${IFACE}"

mkdir -p /etc/sysconfig/network-scripts
if [ -f "${IFCFG}" ]; then
  cp "${IFCFG}" "${IFCFG}.bak_$(date +%Y%m%d_%H%M%S)"
else
  touch "${IFCFG}"
fi

set_ifcfg_key() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "${IFCFG}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${IFCFG}"
  else
    echo "${key}=${value}" >> "${IFCFG}"
  fi
}

set_ifcfg_key TYPE Ethernet
set_ifcfg_key BOOTPROTO dhcp
set_ifcfg_key DEFROUTE yes
set_ifcfg_key IPV4_FAILURE_FATAL no
set_ifcfg_key IPV6INIT no
set_ifcfg_key NAME "${IFACE}"
set_ifcfg_key DEVICE "${IFACE}"
set_ifcfg_key ONBOOT yes

if command -v nmcli >/dev/null 2>&1; then
  echo "[INFO] Enabling NetworkManager autoconnect for ${IFACE}"
  systemctl enable NetworkManager >/dev/null 2>&1 || true
  systemctl start NetworkManager >/dev/null 2>&1 || true

  CONN_NAME="$(nmcli -t -f NAME,DEVICE connection show | awk -F: -v dev="${IFACE}" '$2 == dev {print $1; exit}')"
  if [ -z "${CONN_NAME}" ]; then
    CONN_NAME="${IFACE}"
    nmcli connection add type ethernet ifname "${IFACE}" con-name "${CONN_NAME}" ipv4.method auto connection.autoconnect yes >/dev/null 2>&1 || true
  fi
  nmcli connection modify "${CONN_NAME}" connection.autoconnect yes ipv4.method auto ipv6.method ignore >/dev/null 2>&1 || true
  nmcli connection up "${CONN_NAME}" >/dev/null 2>&1 || true
fi

systemctl enable network >/dev/null 2>&1 || true
ip link set "${IFACE}" up >/dev/null 2>&1 || true
ifup "${IFACE}" >/dev/null 2>&1 || true
dhclient -r "${IFACE}" >/dev/null 2>&1 || true
dhclient -v "${IFACE}" >/dev/null 2>&1 || true

echo "[INFO] Enabling Docker startup"
systemctl enable docker >/dev/null 2>&1 || true
systemctl start docker >/dev/null 2>&1 || true

if docker inspect n8n >/dev/null 2>&1; then
  echo "[INFO] Enabling n8n restart policy"
  docker update --restart unless-stopped n8n >/dev/null 2>&1 || true
  docker start n8n >/dev/null 2>&1 || true
fi

if [ -d "${APP_DIR}" ]; then
  echo "[INFO] Starting compose services in ${APP_DIR}"
  cd "${APP_DIR}"
  docker compose up -d
fi

echo "[OK] Interface address:"
ip -4 addr show "${IFACE}" | sed 's/^/  /'

if [ -d "${APP_DIR}" ]; then
  echo "[OK] Compose status:"
  cd "${APP_DIR}"
  docker compose ps
fi
