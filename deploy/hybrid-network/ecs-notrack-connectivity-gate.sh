#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPORT_PATH="/home/aiadmin/ai-notrack-test-result.txt"
readonly TEST_CONTAINER="ai-private-connectivity-test-notrack"
readonly TEST_IMAGE="ai-middle-office-app:20260804_162758"
readonly TEST_NETWORK="ai-middle-office-app-net"
readonly TEST_IP="10.240.10.11"
readonly BACKEND_IP="192.168.88.128"
readonly ALLOWED_PORTS="3306,5678,6380,8001,9002"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run this script with sudo" >&2
  exit 77
fi

install -o aiadmin -g aiadmin -m 0640 /dev/null "${REPORT_PATH}"
exec > >(tee "${REPORT_PATH}") 2>&1

cleanup() {
  docker rm -f "${TEST_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== preflight ==="
systemctl is-active --quiet ipsec
docker image inspect "${TEST_IMAGE}" >/dev/null
docker network inspect "${TEST_NETWORK}" >/dev/null

if docker ps -a --format '{{.Names}}' | grep -Fxq "${TEST_CONTAINER}"; then
  echo "ERROR: reserved test container name is already in use"
  exit 1
fi

if docker network inspect \
  --format '{{range .Containers}}{{.IPv4Address}}{{"\n"}}{{end}}' \
  "${TEST_NETWORK}" | grep -Eq "^${TEST_IP}/"; then
  echo "ERROR: reserved test IP ${TEST_IP} is already in use"
  exit 1
fi

inbound_rule=(
  -i eth0
  -s "${BACKEND_IP}/32"
  -d "${TEST_IP}/32"
  -p tcp
  -m multiport
  --sports "${ALLOWED_PORTS}"
  -m policy
  --dir in
  --pol ipsec
  --mode tunnel
  -j CT
  --notrack
)

outbound_rule=(
  -i br-ai-app
  -s "${TEST_IP}/32"
  -d "${BACKEND_IP}/32"
  -p tcp
  -m multiport
  --dports "${ALLOWED_PORTS}"
  -j CT
  --notrack
)

iptables -t raw -C PREROUTING "${inbound_rule[@]}" 2>/dev/null || \
  iptables -t raw -I PREROUTING 1 "${inbound_rule[@]}"

iptables -t raw -C PREROUTING "${outbound_rule[@]}" 2>/dev/null || \
  iptables -t raw -I PREROUTING 1 "${outbound_rule[@]}"

echo "=== temporary NOTRACK rules ==="
iptables -t raw -nvL PREROUTING --line-numbers

echo "=== restricted connectivity gate from ${TEST_IP} ==="
set +e
docker run --rm \
  --name "${TEST_CONTAINER}" \
  --network "${TEST_NETWORK}" \
  --ip "${TEST_IP}" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --pids-limit 64 \
  --memory 128m \
  --cpus 0.5 \
  --entrypoint python \
  "${TEST_IMAGE}" \
  -c '
import socket

host = "192.168.88.128"
allowed = [
    ("MySQL", 3306),
    ("N8N", 5678),
    ("Redis", 6380),
    ("RAG", 8001),
    ("MinIO API", 9002),
]
blocked = [
    ("MinIO Console", 9001),
    ("Milvus", 19530),
]
failures = []

for name, port in allowed:
    try:
        connection = socket.create_connection((host, port), timeout=5)
        connection.close()
        print(f"PASS allowed: {name} {host}:{port}")
    except OSError as exc:
        print(f"FAIL allowed: {name} {host}:{port} ({type(exc).__name__})")
        failures.append(name)

for name, port in blocked:
    try:
        connection = socket.create_connection((host, port), timeout=3)
        connection.close()
    except OSError:
        print(f"PASS blocked: {name} {host}:{port}")
    else:
        print(f"FAIL unexpectedly reachable: {name} {host}:{port}")
        failures.append(name)

if failures:
    raise SystemExit("connectivity gate failed: " + ", ".join(failures))

print("private_connectivity_gate=passed")
'
test_rc=$?
set -e

echo "=== NOTRACK counters ==="
iptables -t raw -nvL PREROUTING --line-numbers

echo "=== return allow counters ==="
iptables -nvL DOCKER-USER --line-numbers

echo "=== encrypted traffic ==="
ipsec status | grep 'Traffic:' || true

echo "connectivity_gate_rc=${test_rc}"
exit "${test_rc}"
