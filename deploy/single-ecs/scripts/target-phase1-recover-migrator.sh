#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly API_CONTAINER="ai-middle-office-app-api-1"
readonly WORKER_CONTAINER="ai-middle-office-app-worker-1"
readonly MYSQL_CONTAINER="ai-middle-office-mysql"
readonly N8N_CONTAINER="n8n"
readonly RECOVERY_CONTAINER="ai-middle-office-mysql-phase1-recovery"
readonly MYSQL_IMAGE="mysql:8.0.39"
readonly APP_NETWORK="ai-middle-office-app-net"
readonly MYSQL_CA="/etc/ai-middle-office/mysql-ca.pem"
readonly MIGRATION_ENV="/root/.quote-consistency-phase1-migration.env"
readonly EXPECTED_APP_IMAGE="ai-middle-office-app:20260805_161737"
readonly BACKUP_FILE="/data/ai-middle-office/backups/quote-consistency-phase1/pre-0082-20260808T143433Z/ai_quotation-pre-0082.sql.gz"
readonly EXPECTED_BACKUP_SHA256="6d80f27bac273e045eead419eb58ba99c6fd8be8a6baa70cac0ae554ad28d795"

temp_dir=""
pending_env=""
migrator_cnf=""
recovery_sql=""
probe_file=""
nginx_stopped=false
api_stopped=false
worker_stopped=false
mysql_stopped=false
rotation_applied=false
migration_env_created=false
recovery_container_created=false
success=false

log() {
  printf '%s\n' "$*"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null || true)" == "true" ]]
}

remove_recovery_container() {
  if [[ "${recovery_container_created}" == true ]] && container_exists "${RECOVERY_CONTAINER}"; then
    docker rm --force "${RECOVERY_CONTAINER}" >/dev/null 2>&1 || true
  fi
}

wait_mysql_healthy() {
  local health=""
  local state=""
  local _attempt
  for _attempt in $(seq 1 90); do
    state="$(docker inspect "${MYSQL_CONTAINER}" --format '{{.State.Status}}' 2>/dev/null || true)"
    health="$(docker inspect "${MYSQL_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
    if [[ "${state}" == "running" && "${health}" == "healthy" ]]; then
      return 0
    fi
    if [[ "${state}" == "exited" || "${state}" == "dead" ]]; then
      return 1
    fi
    sleep 2
  done
  return 1
}

wait_api_healthy() {
  local health=""
  local state=""
  local _attempt
  for _attempt in $(seq 1 90); do
    state="$(docker inspect "${API_CONTAINER}" --format '{{.State.Status}}' 2>/dev/null || true)"
    health="$(docker inspect "${API_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
    if [[ "${state}" == "running" && "${health}" == "healthy" ]]; then
      return 0
    fi
    if [[ "${state}" == "exited" || "${state}" == "dead" ]]; then
      return 1
    fi
    sleep 2
  done
  return 1
}

probe_application_idle() {
  timeout 60 docker exec -i "${API_CONTAINER}" python - <<'PY'
import os

import redis

from app.tasks.celery_app import celery_app


def count(mapping):
    return sum(len(items or []) for items in (mapping or {}).values())


inspector = celery_app.control.inspect(timeout=5)
ping = inspector.ping() or {}
active = count(inspector.active())
reserved = count(inspector.reserved())
scheduled = count(inspector.scheduled())
client = redis.Redis.from_url(
    os.environ["CELERY_BROKER_URL"],
    socket_connect_timeout=5,
    socket_timeout=5,
)
queue_name = celery_app.conf.task_default_queue or "celery"
queued = client.llen(queue_name)
unacked = client.hlen("unacked")
unacked_index = client.zcard("unacked_index")
print(
    "RESULT|application_idle="
    f"workers={len(ping)}|active={active}|reserved={reserved}|"
    f"scheduled={scheduled}|queue={queue_name}|queued={queued}|"
    f"unacked={unacked}|unacked_index={unacked_index}",
    flush=True,
)
if len(ping) != 1 or any((active, reserved, scheduled, queued, unacked, unacked_index)):
    raise SystemExit("ERROR|application_not_idle")
PY
}

probe_database_baseline() {
  timeout 45 docker exec -i "${API_CONTAINER}" python - <<'PY'
from sqlalchemy import text

from app.core.database import engine


with engine.connect() as connection:
    current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    tables = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN ('quote_push_attempts', 'quote_quota_reservations')
            """
        )
    ).scalar_one()
    columns = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND (
                (TABLE_NAME = 'users' AND COLUMN_NAME = 'quota_reserved')
                OR
                (TABLE_NAME = 'quote_jobs' AND COLUMN_NAME IN
                  ('source_job_id', 'attempt_id', 'started_at'))
              )
            """
        )
    ).scalar_one()
print(f"RESULT|database_baseline=head={current}|new_tables={int(tables)}|new_columns={int(columns)}")
if current != "20260801_0081" or int(tables) != 0 or int(columns) != 0:
    raise SystemExit("ERROR|database_baseline_changed")
PY
}

restore_old_application() {
  if [[ "${mysql_stopped}" == true ]]; then
    if ! container_running "${MYSQL_CONTAINER}"; then
      docker start "${MYSQL_CONTAINER}" >/dev/null 2>&1 || true
    fi
    wait_mysql_healthy >/dev/null 2>&1 || true
  fi

  if [[ "${api_stopped}" == true ]] && ! container_running "${API_CONTAINER}"; then
    docker start "${API_CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ "${worker_stopped}" == true ]] && ! container_running "${WORKER_CONTAINER}"; then
    docker start "${WORKER_CONTAINER}" >/dev/null 2>&1 || true
  fi
  wait_api_healthy >/dev/null 2>&1 || true

  if [[ "${nginx_stopped}" == true ]]; then
    nginx -t >/dev/null 2>&1 && systemctl start nginx >/dev/null 2>&1 || true
  fi
}

cleanup_temp_files() {
  if [[ -n "${temp_dir}" && -d "${temp_dir}" ]]; then
    rm -f -- "${migrator_cnf}" "${recovery_sql}" "${probe_file}" "${pending_env}"
    rmdir -- "${temp_dir}" 2>/dev/null || true
  fi
}

on_exit() {
  local rc=$?
  trap - EXIT INT TERM HUP
  set +e
  remove_recovery_container
  if [[ "${success}" != true ]]; then
    log "ROLLBACK|migrator_recovery=begin|exit=${rc}"
    restore_old_application
    if [[ "${migration_env_created}" == true && "${rotation_applied}" != true ]]; then
      rm -f -- "${MIGRATION_ENV}"
    fi
    log "ROLLBACK|migrator_recovery=application_restore_attempted"
  fi
  cleanup_temp_files
  exit "${rc}"
}
trap on_exit EXIT

on_signal() {
  log "ERROR|signal_received|signal=$1"
  exit 130
}
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

[[ "${EUID}" -eq 0 ]] || {
  log "ERROR|root_required"
  exit 1
}

for required_command in docker python3 sha256sum systemctl nginx timeout gzip; do
  command -v "${required_command}" >/dev/null || {
    log "ERROR|required_command_missing|${required_command}"
    exit 1
  }
done

for required_file in "${MYSQL_CA}" "${BACKUP_FILE}"; do
  [[ -f "${required_file}" ]] || {
    log "ERROR|required_file_missing|${required_file}"
    exit 1
  }
done

[[ ! -e "${MIGRATION_ENV}" ]] || {
  log "ERROR|migration_env_already_exists|${MIGRATION_ENV}"
  exit 1
}

if container_exists "${RECOVERY_CONTAINER}"; then
  log "ERROR|unexpected_recovery_container_exists|${RECOVERY_CONTAINER}"
  exit 1
fi

for required_container in "${API_CONTAINER}" "${WORKER_CONTAINER}" "${MYSQL_CONTAINER}" "${N8N_CONTAINER}"; do
  container_running "${required_container}" || {
    log "ERROR|required_container_not_running|${required_container}"
    exit 1
  }
done

[[ "$(docker inspect "${API_CONTAINER}" --format '{{.Config.Image}}')" == "${EXPECTED_APP_IMAGE}" ]] || {
  log "ERROR|unexpected_api_image"
  exit 1
}
[[ "$(docker inspect "${WORKER_CONTAINER}" --format '{{.Config.Image}}')" == "${EXPECTED_APP_IMAGE}" ]] || {
  log "ERROR|unexpected_worker_image"
  exit 1
}
[[ "$(docker inspect "${MYSQL_CONTAINER}" --format '{{.Config.Image}}')" == "${MYSQL_IMAGE}" ]] || {
  log "ERROR|unexpected_mysql_image"
  exit 1
}
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]] || {
  log "ERROR|nginx_not_active_before_recovery"
  exit 1
}

actual_backup_sha256="$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"
[[ "${actual_backup_sha256}" == "${EXPECTED_BACKUP_SHA256}" ]] || {
  log "ERROR|backup_sha256_mismatch|actual=${actual_backup_sha256}"
  exit 1
}
gzip -t "${BACKUP_FILE}"
log "PASS|backup_gate|sha256=${actual_backup_sha256}"

probe_database_baseline
probe_application_idle

temp_dir="$(mktemp -d /root/quote-phase1-migrator-recovery.XXXXXXXX)"
chmod 0700 "${temp_dir}"
pending_env="${temp_dir}/migration.env.pending"
migrator_cnf="${temp_dir}/migrator.cnf"
recovery_sql="${temp_dir}/recover-migrator.sql"
probe_file="${temp_dir}/migrator-probe.txt"

python3 - "${pending_env}" "${migrator_cnf}" "${recovery_sql}" <<'PY'
import os
import secrets
import sys
from urllib.parse import quote


pending_env, migrator_cnf, recovery_sql = sys.argv[1:]
password = secrets.token_urlsafe(32)
if len(password) != 43:
    raise SystemExit("ERROR|unexpected_generated_password_length")


def mysql_option(value):
    value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def sql_literal(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


migration_url = (
    "mysql+pymysql://ai_migrator:"
    + quote(password, safe="")
    + "@ai-mysql:3306/ai_quotation"
    + "?charset=utf8mb4"
    + "&ssl_ca=/run/secrets/mysql-ca.pem"
    + "&ssl_check_hostname=false"
)

client_config = "\n".join(
    [
        "[client]",
        'user="ai_migrator"',
        f"password={mysql_option(password)}",
        'host="ai-mysql"',
        "port=3306",
        "protocol=tcp",
        "default-character-set=utf8mb4",
        "ssl-mode=VERIFY_CA",
        'ssl-ca="/run/secrets/mysql-ca.pem"',
        "",
    ]
)

account_sql = "\n".join(
    [
        "FLUSH PRIVILEGES;",
        (
            "CREATE USER IF NOT EXISTS 'ai_migrator'@'10.240.10.10' "
            "IDENTIFIED WITH caching_sha2_password BY "
            + sql_literal(password)
            + " REQUIRE SSL PASSWORD EXPIRE NEVER;"
        ),
        (
            "ALTER USER 'ai_migrator'@'10.240.10.10' "
            "IDENTIFIED WITH caching_sha2_password BY "
            + sql_literal(password)
            + " REQUIRE SSL PASSWORD EXPIRE NEVER;"
        ),
        (
            "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES "
            "ON `ai_quotation`.* TO 'ai_migrator'@'10.240.10.10';"
        ),
        "",
    ]
)

outputs = {
    pending_env: f"MIGRATION_DATABASE_URL={migration_url}\n",
    migrator_cnf: client_config,
    recovery_sql: account_sql,
}
for path, content in outputs.items():
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    os.chmod(path, 0o600)
PY

nginx_stopped=true
systemctl stop nginx
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "inactive" ]] || {
  log "ERROR|nginx_failed_to_stop"
  exit 1
}
log "PASS|public_ingress=frozen"

probe_application_idle

worker_stopped=true
docker stop --time 90 "${WORKER_CONTAINER}" >/dev/null
api_stopped=true
docker stop --time 45 "${API_CONTAINER}" >/dev/null
log "PASS|application_containers=stopped"

mysql_stopped=true
docker stop --time 120 "${MYSQL_CONTAINER}" >/dev/null
log "PASS|mysql_primary=stopped"

recovery_container_created=true
docker run --detach \
  --name "${RECOVERY_CONTAINER}" \
  --network none \
  --volumes-from "${MYSQL_CONTAINER}" \
  "${MYSQL_IMAGE}" \
  --skip-grant-tables \
  --skip-networking \
  --socket=/var/run/mysqld/mysqld.sock >/dev/null

recovery_ready=false
for _attempt in $(seq 1 90); do
  if ! container_running "${RECOVERY_CONTAINER}"; then
    docker logs --tail 120 "${RECOVERY_CONTAINER}" >&2 || true
    log "ERROR|mysql_recovery_container_stopped"
    exit 1
  fi
  if docker exec "${RECOVERY_CONTAINER}" \
    mysqladmin --protocol=socket --user=root ping --silent >/dev/null 2>&1; then
    recovery_ready=true
    break
  fi
  sleep 2
done
[[ "${recovery_ready}" == true ]] || {
  docker logs --tail 120 "${RECOVERY_CONTAINER}" >&2 || true
  log "ERROR|mysql_recovery_ready_timeout"
  exit 1
}
log "PASS|mysql_recovery=isolated_and_ready"

# Preserve the generated credential before the first account-changing SQL.
# If MySQL accepts only part of the statement batch, the operator must still
# retain the candidate password for deterministic recovery and verification.
mv -- "${pending_env}" "${MIGRATION_ENV}"
chmod 0600 "${MIGRATION_ENV}"
migration_env_created=true
rotation_applied=true
log "PASS|migration_secret=stored_root_only"

docker exec -i "${RECOVERY_CONTAINER}" \
  mysql --protocol=socket --user=root < "${recovery_sql}"
log "PASS|migrator_account=rotated_with_references"

docker stop --time 120 "${RECOVERY_CONTAINER}" >/dev/null
docker rm "${RECOVERY_CONTAINER}" >/dev/null
recovery_container_created=false

docker start "${MYSQL_CONTAINER}" >/dev/null
wait_mysql_healthy || {
  docker logs --tail 160 "${MYSQL_CONTAINER}" >&2 || true
  log "ERROR|mysql_primary_health_timeout"
  exit 1
}
mysql_stopped=false
log "PASS|mysql_primary=healthy"

docker run --rm \
  --network "${APP_NETWORK}" \
  --ip 10.240.10.10 \
  --volume "${migrator_cnf}:/run/secrets/mysql-client.cnf:ro" \
  --volume "${MYSQL_CA}:/run/secrets/mysql-ca.pem:ro" \
  "${MYSQL_IMAGE}" \
  mysql \
    --defaults-extra-file=/run/secrets/mysql-client.cnf \
    --database=ai_quotation \
    --batch \
    --skip-column-names \
    --execute="
      SELECT CONCAT('RESULT|migrator_identity=', CURRENT_USER(), '|database=', DATABASE());
      SELECT CONCAT(
        'RESULT|migrator_ssl=',
        COALESCE(
          (SELECT VARIABLE_VALUE FROM performance_schema.session_status WHERE VARIABLE_NAME = 'Ssl_cipher'),
          'unknown'
        )
      );
      SELECT CONCAT(
        'RESULT|migrator_schema_privileges=',
        COALESCE(
          GROUP_CONCAT(DISTINCT PRIVILEGE_TYPE ORDER BY PRIVILEGE_TYPE SEPARATOR ','),
          'none'
        )
      )
      FROM information_schema.SCHEMA_PRIVILEGES
      WHERE TABLE_SCHEMA = DATABASE();
      SELECT CONCAT(
        'RESULT|migrator_references=',
        IF(SUM(PRIVILEGE_TYPE = 'REFERENCES') > 0, 'present', 'absent')
      )
      FROM information_schema.SCHEMA_PRIVILEGES
      WHERE TABLE_SCHEMA = DATABASE();
    " > "${probe_file}"

cat "${probe_file}"
grep -Fxq "RESULT|migrator_identity=ai_migrator@10.240.10.10|database=ai_quotation" "${probe_file}"
grep -Eq '^RESULT\|migrator_ssl=.+$' "${probe_file}"
! grep -Fxq 'RESULT|migrator_ssl=unknown' "${probe_file}"
grep -Fxq 'RESULT|migrator_references=present' "${probe_file}"

docker start "${API_CONTAINER}" "${WORKER_CONTAINER}" >/dev/null
wait_api_healthy || {
  docker logs --tail 160 "${API_CONTAINER}" >&2 || true
  log "ERROR|api_health_timeout_after_recovery"
  exit 1
}

timeout 75 docker exec -i "${API_CONTAINER}" python - <<'PY'
import json
import os
import time
import urllib.request

from app.tasks.celery_app import celery_app


with urllib.request.urlopen("http://127.0.0.1:9000/health/ready", timeout=20) as response:
    payload = json.loads(response.read())
if response.status != 200 or payload.get("status") != "ready":
    raise SystemExit("ERROR|application_ready_failed_after_recovery")

ping = {}
for _ in range(8):
    ping = celery_app.control.inspect(timeout=5).ping() or {}
    if ping:
        break
    time.sleep(2)
if len(ping) != 1:
    raise SystemExit(f"ERROR|unexpected_worker_count_after_recovery|count={len(ping)}")
print("RESULT|application_recovered=ready|worker_count=1", flush=True)
PY

api_stopped=false
worker_stopped=false

nginx -t
systemctl start nginx
[[ "$(systemctl is-active nginx 2>/dev/null || true)" == "active" ]] || {
  log "ERROR|nginx_failed_to_restart"
  exit 1
}
nginx_stopped=false

stat -c 'RESULT|migration_env=%n|owner=%U:%G|mode=%a|size=%s' "${MIGRATION_ENV}"
log "RESULT|migrator_recovery=passed"
log "INFO|database_schema_unchanged=20260801_0081"
log "INFO|application_image_unchanged=${EXPECTED_APP_IMAGE}"
log "INFO|n8n_not_modified=true"

success=true
exit 0
