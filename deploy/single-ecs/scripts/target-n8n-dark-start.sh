#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly STACK_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly COMPOSE_FILE="${STACK_DIR}/compose.backend.yaml"
readonly BACKEND_ENV="${BACKEND_ENV:-/etc/ai-middle-office/backend.env}"

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}
[[ -f "${COMPOSE_FILE}" ]] || {
  echo "ERROR|missing_compose|${COMPOSE_FILE}" >&2
  exit 1
}
[[ -f "${BACKEND_ENV}" ]] || {
  echo "ERROR|missing_backend_env|${BACKEND_ENV}" >&2
  exit 1
}

for dependency in rag-api-service dify-nginx-1; do
  if ! docker inspect "${dependency}" >/dev/null 2>&1 || \
    [[ "$(docker inspect "${dependency}" --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|required_dependency_not_running|${dependency}" >&2
    exit 1
  fi
done

compose=(docker compose --env-file "${BACKEND_ENV}" -f "${COMPOSE_FILE}")
"${compose[@]}" up -d --no-deps n8n

health=starting
for _ in $(seq 1 60); do
  health="$(docker inspect n8n --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
  if [[ "${health}" == healthy ]]; then
    break
  fi
  if [[ "$(docker inspect n8n --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|target_n8n_stopped_during_start|health=${health}" >&2
    docker logs --tail 80 n8n >&2 || true
    exit 1
  fi
  sleep 3
done

if [[ "${health}" != healthy ]]; then
  echo "ERROR|target_n8n_health_timeout|health=${health}" >&2
  docker logs --tail 80 n8n >&2 || true
  exit 1
fi

if [[ -n "$(docker port n8n)" ]]; then
  echo "ERROR|target_n8n_has_published_host_port" >&2
  exit 1
fi

docker exec -i n8n node - <<'NODE'
const probes = [
  ["n8n_health", "http://127.0.0.1:5678/healthz", (status) => status >= 200 && status < 300],
  ["rag_openapi", "http://rag-service:8001/openapi.json", (status) => status >= 200 && status < 300],
  ["dify_internal", "http://dify-nginx/", (status) => status >= 200 && status < 500],
];

(async () => {
  for (const [name, url, acceptable] of probes) {
    const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!acceptable(response.status)) {
      throw new Error(`${name} returned HTTP ${response.status}`);
    }
    console.log(`PASS|internal_probe|${name}|http_status=${response.status}`);
  }
})().catch((error) => {
  console.error(`ERROR|internal_probe|${error.message}`);
  process.exit(1);
});
NODE

network_count="$(docker inspect n8n --format '{{range $name, $_ := .NetworkSettings.Networks}}{{if eq $name "ai-middle-office-app-net"}}1{{end}}{{end}}')"
if [[ "${network_count}" != 1 ]]; then
  echo "ERROR|target_n8n_missing_internal_network" >&2
  exit 1
fi

docker inspect n8n --format 'PASS|container_ready|n8n|status={{.State.Status}}|health={{.State.Health.Status}}'
docker stats --no-stream n8n
echo "RESULT|target_n8n_dark_start=passed"
echo "INFO|no_host_port_published"
echo "INFO|source_n8n_not_touched"
