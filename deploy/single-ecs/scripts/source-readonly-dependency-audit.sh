#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR|run_as_root" >&2
  exit 1
fi

for required in docker; do
  if ! command -v "${required}" >/dev/null 2>&1; then
    echo "ERROR|missing_command|${required}" >&2
    exit 1
  fi
done

if ! docker inspect n8n >/dev/null 2>&1; then
  echo "ERROR|missing_container|n8n" >&2
  exit 1
fi
if [[ "$(docker inspect n8n --format '{{.State.Running}}')" != true ]]; then
  echo "ERROR|container_not_running|n8n" >&2
  exit 1
fi

readonly workflow_tmp="/tmp/single-ecs-workflow-audit-$$.json"
cleanup() {
  trap - EXIT
  docker exec n8n rm -f "${workflow_tmp}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The export exists only inside the running N8N container and is removed by
# the EXIT trap. The parser emits workflow identity plus private/local origins;
# it never emits credentials, query strings, URL paths, or workflow JSON.
docker exec n8n n8n export:workflow --all --output="${workflow_tmp}" >/dev/null
docker exec -i n8n node - "${workflow_tmp}" <<'NODE'
const fs = require("fs");
const source = process.argv[2];
const parsed = JSON.parse(fs.readFileSync(source, "utf8"));
const workflows = Array.isArray(parsed) ? parsed : [parsed];

function safe(value) {
  return String(value ?? "")
    .replace(/[|\r\n]/g, "_")
    .slice(0, 160);
}

function isPrivateHost(host) {
  const value = host.toLowerCase().replace(/^\[|\]$/g, "");
  if (value === "localhost" || value === "127.0.0.1" || value === "::1") return true;
  if (/^10\./.test(value) || /^192\.168\./.test(value)) return true;
  const match = value.match(/^172\.(\d+)\./);
  if (match && Number(match[1]) >= 16 && Number(match[1]) <= 31) return true;
  return value.endsWith(".local") || /(^|[-.])(dify|rag|milvus|minio|redis|n8n|mysql)([-.]|$)/.test(value);
}

function collect(value, hits) {
  if (typeof value === "string") {
    const urlPattern = /https?:\/\/[^\s"'<>]+/g;
    for (const raw of value.match(urlPattern) || []) {
      try {
        const url = new URL(raw);
        if (isPrivateHost(url.hostname)) {
          hits.add(`${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}`);
        }
      } catch (_) {}
    }
    for (const ip of value.match(/(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})/g) || []) {
      hits.add(`private-ip:${ip}`);
    }
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collect(item, hits);
    return;
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) collect(item, hits);
  }
}

let totalReferences = 0;
for (const workflow of workflows) {
  const hits = new Set();
  collect(workflow, hits);
  for (const reference of [...hits].sort()) {
    totalReferences += 1;
    process.stdout.write(
      `N8N_REFERENCE|id=${safe(workflow.id)}|active=${Boolean(workflow.active)}|name=${safe(workflow.name)}|origin=${safe(reference)}\n`
    );
  }
}
process.stdout.write(`N8N_AUDIT|workflows=${workflows.length}|private_or_local_references=${totalReferences}\n`);
NODE

echo "RAGFLOW_AUDIT|begin"
for container in ragflow-ragflow-cpu-1 ragflow-es01-1 ragflow-redis-1 ragflow-minio-1; do
  if ! docker inspect "${container}" >/dev/null 2>&1; then
    echo "RAGFLOW_CONTAINER|name=${container}|state=missing"
    continue
  fi
  state="$(docker inspect "${container}" --format '{{.State.Status}}')"
  project="$(docker inspect "${container}" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
  networks="$(docker inspect "${container}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}},{{end}}')"
  echo "RAGFLOW_CONTAINER|name=${container}|state=${state}|project=${project}|networks=${networks%,}"
done

for container in ragflow-redis-1 ragflow-minio-1; do
  if ! docker inspect "${container}" >/dev/null 2>&1; then
    continue
  fi
  while IFS= read -r network; do
    [[ -n "${network}" ]] || continue
    peers="$(docker network inspect "${network}" --format '{{range .Containers}}{{.Name}},{{end}}')"
    echo "RAGFLOW_NETWORK|container=${container}|network=${network}|peers=${peers%,}"
  done < <(docker inspect "${container}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}')
done

if [[ "$(docker inspect ragflow-redis-1 --format '{{.State.Running}}' 2>/dev/null || true)" == true ]]; then
  redis_clients="$(docker exec ragflow-redis-1 sh -c 'redis-cli CLIENT LIST 2>/dev/null | wc -l' || true)"
  echo "RAGFLOW_REDIS|client_count_including_this_audit=${redis_clients:-unknown}"
fi

echo "RESULT|source_readonly_dependency_audit=completed"
