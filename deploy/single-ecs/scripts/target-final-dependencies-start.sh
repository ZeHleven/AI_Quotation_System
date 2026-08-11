#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly STACK_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly BACKEND_COMPOSE="${STACK_DIR}/compose.backend.yaml"
readonly BACKEND_ENV="/etc/ai-middle-office/backend.env"
readonly DIFY_COMPOSE="${SCRIPT_DIR}/dify-compose.sh"

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}
for required in "${BACKEND_COMPOSE}" "${BACKEND_ENV}" "${DIFY_COMPOSE}"; do
  [[ -f "${required}" ]] || {
    echo "ERROR|missing_required_file|${required}" >&2
    exit 1
  }
done

for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  if docker inspect "${container}" >/dev/null 2>&1 && \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" == true ]]; then
    echo "ERROR|application_must_remain_stopped|${container}" >&2
    exit 1
  fi
done

if [[ "$(systemctl is-active nginx 2>/dev/null || true)" == active ]]; then
  echo "ERROR|public_nginx_must_remain_stopped" >&2
  exit 1
fi

backend=(docker compose --env-file "${BACKEND_ENV}" -f "${BACKEND_COMPOSE}")

# Final restored n8n data is a fresh source snapshot, so apply the audited
# internal endpoint rewrite again before starting any n8n server.
bash "${SCRIPT_DIR}/target-n8n-offline-rewrite.sh"

"${backend[@]}" up -d mysql quote-redis milvus-etcd milvus-minio quote-minio

wait_healthy() {
  local container="$1" attempts="${2:-80}" status
  for _ in $(seq 1 "${attempts}"); do
    status="$(docker inspect "${container}" \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      2>/dev/null || true)"
    if [[ "${status}" == healthy ]]; then
      echo "PASS|container_healthy|${container}"
      return 0
    fi
    if [[ "${status}" == exited || "${status}" == dead ]]; then
      echo "ERROR|container_stopped_during_start|${container}|status=${status}" >&2
      docker logs --tail 100 "${container}" >&2 || true
      return 1
    fi
    sleep 3
  done
  echo "ERROR|container_health_timeout|${container}|status=${status:-unknown}" >&2
  docker logs --tail 100 "${container}" >&2 || true
  return 1
}

for container in ai-middle-office-mysql quote-redis milvus-etcd milvus-minio quote-minio; do
  wait_healthy "${container}" 100
done

"${backend[@]}" up -d milvus rag-service
for _ in $(seq 1 100); do
  if docker run --rm --network ai-middle-office-app-net \
    --entrypoint wget busybox:latest \
    -q -T 5 -O /dev/null http://rag-service:8001/openapi.json 2>/dev/null; then
    echo "PASS|rag_service=ready"
    break
  fi
  if [[ "$(docker inspect rag-api-service --format '{{.State.Running}}' 2>/dev/null || true)" != true ]]; then
    echo "ERROR|rag_service_stopped_during_start" >&2
    docker logs --tail 100 rag-api-service >&2 || true
    exit 1
  fi
  sleep 3
done
if ! docker run --rm --network ai-middle-office-app-net \
  --entrypoint wget busybox:latest \
  -q -T 5 -O /dev/null http://rag-service:8001/openapi.json 2>/dev/null; then
  echo "ERROR|rag_service_readiness_timeout" >&2
  exit 1
fi

"${DIFY_COMPOSE}" up -d \
  nginx api plugin_daemon ssrf_proxy web redis sandbox db_postgres weaviate

for container in dify-redis-1 dify-db_postgres-1 dify-sandbox-1; do
  wait_healthy "${container}" 100
done

for _ in $(seq 1 100); do
  if docker run --rm --network ai-middle-office-app-net \
    --entrypoint wget busybox:latest \
    -q -T 5 -O /dev/null http://dify-nginx/ 2>/dev/null; then
    echo "PASS|dify_api=ready"
    break
  fi
  sleep 3
done
if ! docker run --rm --network ai-middle-office-app-net \
  --entrypoint wget busybox:latest \
  -q -T 5 -O /dev/null http://dify-nginx/ 2>/dev/null; then
  echo "ERROR|dify_readiness_timeout" >&2
  docker logs --tail 100 dify-api-1 >&2 || true
  exit 1
fi

redis_password="$(
  docker inspect dify-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' |
    sed -n 's/^REDIS_PASSWORD=//p' |
    head -n 1
)"
[[ -n "${redis_password}" ]] || {
  echo "ERROR|missing_dify_redis_password" >&2
  exit 1
}

read -r -d '' redis_audit_lua <<'LUA' || true
local cursor = "0"
local list_items = 0
local zset_items = 0
local stream_entries = 0
repeat
  local result = redis.call("SCAN", cursor, "COUNT", 1000)
  cursor = result[1]
  for _, key in ipairs(result[2]) do
    local type_result = redis.call("TYPE", key)
    local key_type = type_result["ok"] or type_result
    if key_type == "list" then list_items = list_items + redis.call("LLEN", key) end
    if key_type == "zset" then zset_items = zset_items + redis.call("ZCARD", key) end
    if key_type == "stream" then stream_entries = stream_entries + redis.call("XLEN", key) end
  end
until cursor == "0"
return "list_items=" .. list_items ..
  "|zset_items=" .. zset_items ..
  "|stream_entries=" .. stream_entries
LUA

dify_queue="$(docker exec -e REDISCLI_AUTH="${redis_password}" dify-redis-1 \
  redis-cli --raw EVAL "${redis_audit_lua}" 0)"
echo "DIFY_QUEUE_AGGREGATE|${dify_queue}"
for expected in list_items=0 zset_items=0 stream_entries=0; do
  [[ "|${dify_queue}|" == *"|${expected}|"* ]] || {
    echo "ERROR|restored_dify_queue_not_empty|expected=${expected}" >&2
    exit 1
  }
done

bash "${SCRIPT_DIR}/target-dify-worker-dark-start.sh"
bash "${SCRIPT_DIR}/target-n8n-dark-start.sh"

if docker inspect dify-worker_beat-1 >/dev/null 2>&1 && \
  [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|dify_worker_beat_started_unexpectedly" >&2
  exit 1
fi

echo "RESULT|target_final_dependencies_start=passed"
echo "INFO|application_and_public_nginx_still_stopped"
echo "INFO|dify_worker_beat_still_stopped"
echo "INFO|next_gate=application_environment_switch"
