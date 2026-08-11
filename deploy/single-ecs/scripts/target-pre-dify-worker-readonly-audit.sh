#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}

for container in dify-api-1 dify-redis-1 ai-middle-office-mysql \
  ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  if ! docker inspect "${container}" >/dev/null 2>&1 || \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|required_container_not_running|${container}" >&2
    exit 1
  fi
done

for worker in dify-worker-1 dify-worker_beat-1; do
  if docker inspect "${worker}" >/dev/null 2>&1 && \
    [[ "$(docker inspect "${worker}" --format '{{.State.Running}}')" == true ]]; then
    echo "ERROR|dify_worker_must_remain_stopped|${worker}" >&2
    exit 1
  fi
done

redis_password="$(
  docker inspect dify-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' |
    sed -n 's/^REDIS_PASSWORD=//p' |
    head -n 1
)"
[[ -n "${redis_password}" ]] || {
  echo "ERROR|missing_dify_redis_password" >&2
  exit 1
}

if ! docker exec -e REDISCLI_AUTH="${redis_password}" dify-redis-1 redis-cli ping >/dev/null; then
  echo "ERROR|target_dify_redis_auth_failed" >&2
  exit 1
fi

read -r -d '' redis_audit_lua <<'LUA' || true
local cursor = "0"
local keys = 0
local type_counts = {string=0,list=0,set=0,zset=0,hash=0,stream=0,none=0}
local list_items = 0
local zset_items = 0
local hash_fields = 0
local stream_entries = 0
repeat
  local result = redis.call("SCAN", cursor, "COUNT", 1000)
  cursor = result[1]
  for _, key in ipairs(result[2]) do
    keys = keys + 1
    local type_result = redis.call("TYPE", key)
    local key_type = type_result["ok"] or type_result
    type_counts[key_type] = (type_counts[key_type] or 0) + 1
    if key_type == "list" then list_items = list_items + redis.call("LLEN", key) end
    if key_type == "zset" then zset_items = zset_items + redis.call("ZCARD", key) end
    if key_type == "hash" then hash_fields = hash_fields + redis.call("HLEN", key) end
    if key_type == "stream" then stream_entries = stream_entries + redis.call("XLEN", key) end
  end
until cursor == "0"
return "keys=" .. keys ..
  "|string_keys=" .. type_counts.string ..
  "|list_keys=" .. type_counts.list ..
  "|list_items=" .. list_items ..
  "|set_keys=" .. type_counts.set ..
  "|zset_keys=" .. type_counts.zset ..
  "|zset_items=" .. zset_items ..
  "|hash_keys=" .. type_counts.hash ..
  "|hash_fields=" .. hash_fields ..
  "|stream_keys=" .. type_counts.stream ..
  "|stream_entries=" .. stream_entries
LUA

redis_aggregate="$(
  docker exec -e REDISCLI_AUTH="${redis_password}" dify-redis-1 \
    redis-cli --raw EVAL "${redis_audit_lua}" 0
)"
echo "DIFY_REDIS_AGGREGATE|${redis_aggregate}"

for required_empty in 'list_items=0' 'zset_items=0' 'stream_entries=0'; do
  if [[ "|${redis_aggregate}|" != *"|${required_empty}|"* ]]; then
    echo "ERROR|target_dify_queue_not_empty|expected=${required_empty}" >&2
    exit 1
  fi
done
echo "PASS|target_dify_queue=empty"

declare -A expected_ips=(
  [ai-middle-office-app-api-1]=10.240.10.10
  [ai-middle-office-app-worker-1]=10.240.10.11
)

for container in ai-middle-office-app-api-1 ai-middle-office-app-worker-1; do
  actual_ip="$(
    docker inspect "${container}" \
      --format '{{with index .NetworkSettings.Networks "ai-middle-office-app-net"}}{{.IPAddress}}{{end}}'
  )"
  if [[ "${actual_ip}" != "${expected_ips[${container}]}" ]]; then
    echo "ERROR|unexpected_app_container_ip|container=${container}|ip=${actual_ip}" >&2
    exit 1
  fi

  docker exec -i "${container}" python - "${container}" <<'PY'
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

container = sys.argv[1]
try:
    source_url = make_url(os.environ["DATABASE_URL"])
    target_url = source_url.set(host="ai-mysql", port=3306)
    engine = create_engine(target_url, pool_pre_ping=True, pool_recycle=60)
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                raise RuntimeError("SELECT 1 mismatch")
            alembic = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            ssl_row = connection.execute(text("SHOW STATUS LIKE 'Ssl_cipher'"))
            ssl_cipher = ssl_row.fetchone()
            if not ssl_cipher or not ssl_cipher[1]:
                raise RuntimeError("TLS cipher is empty")
            if alembic != "20260801_0081":
                raise RuntimeError("unexpected Alembic version")
        print(
            f"PASS|target_mysql_readonly|container={container}"
            f"|alembic={alembic}|tls=enabled"
        )
    finally:
        engine.dispose()
except Exception as error:
    print(
        f"ERROR|target_mysql_readonly|container={container}"
        f"|error_type={type(error).__name__}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
done

echo "RESULT|target_pre_dify_worker_readonly_audit=passed"
echo "INFO|no_database_writes"
echo "INFO|dify_workers_still_stopped"
