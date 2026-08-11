#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly API_CONTAINER="ai-middle-office-app-api-1"
readonly WORKER_CONTAINER="ai-middle-office-app-worker-1"
readonly MYSQL_CONTAINER="ai-middle-office-mysql"
readonly N8N_CONTAINER="n8n"
readonly MYSQL_IMAGE="mysql:8.0.39"
readonly CANDIDATE_IMAGE="ai-middle-office-app:20260808_consistency1_candidate"
readonly EXPECTED_CANDIDATE_ID="sha256:9056820a2d3e7215b4d8d9f53c8e6fd165e178e59d2ceafaa3668368710d9099"
readonly BACKUP_FILE="/data/ai-middle-office/backups/quote-consistency-phase1/pre-0082-20260808T143433Z/ai_quotation-pre-0082.sql.gz"
readonly EXPECTED_BACKUP_SHA256="6d80f27bac273e045eead419eb58ba99c6fd8be8a6baa70cac0ae554ad28d795"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly DRILL_CONTAINER="quote-phase1-mysql-drill-${STAMP}"
readonly DRILL_NETWORK="quote-phase1-drill-${STAMP}"
readonly DRILL_VOLUME="quote-phase1-drill-${STAMP}"

temp_dir=""
root_password_file=""
root_cnf=""
migration_env=""
account_sql=""
container_created=false
network_created=false
volume_created=false
success=false

log() {
  printf '%s\n' "$*"
}

container_running() {
  [[ "$(docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null || true)" == "true" ]]
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  set +e
  if [[ "${container_created}" == true ]]; then
    if [[ "${success}" != true ]] && docker inspect "${DRILL_CONTAINER}" >/dev/null 2>&1; then
      log "DIAGNOSTIC|drill_mysql_logs=begin"
      docker logs --tail 200 "${DRILL_CONTAINER}" 2>&1 || true
      log "DIAGNOSTIC|drill_mysql_logs=end"
      docker inspect "${DRILL_CONTAINER}" \
        --format 'DIAGNOSTIC|drill_mysql_state=status={{.State.Status}}|exit={{.State.ExitCode}}|oom_killed={{.State.OOMKilled}}|error={{.State.Error}}' \
        2>/dev/null || true
    fi
    docker rm --force "${DRILL_CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ "${volume_created}" == true ]]; then
    docker volume rm "${DRILL_VOLUME}" >/dev/null 2>&1 || true
  fi
  if [[ "${network_created}" == true ]]; then
    docker network rm "${DRILL_NETWORK}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${temp_dir}" && -d "${temp_dir}" ]]; then
    rm -f -- "${root_password_file}" "${root_cnf}" "${migration_env}" "${account_sql}"
    rmdir -- "${temp_dir}" 2>/dev/null || true
  fi
  if [[ "${success}" == true ]]; then
    log "RESULT|migration_restore_drill=passed"
  else
    log "ROLLBACK|migration_restore_drill=temporary_resources_cleanup_attempted|exit=${rc}"
  fi
  exit "${rc}"
}
trap cleanup EXIT

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

for required_command in docker python3 sha256sum gzip awk grep seq; do
  command -v "${required_command}" >/dev/null || {
    log "ERROR|required_command_missing|${required_command}"
    exit 1
  }
done

[[ -f "${BACKUP_FILE}" ]] || {
  log "ERROR|backup_missing|${BACKUP_FILE}"
  exit 1
}

for production_container in "${API_CONTAINER}" "${WORKER_CONTAINER}" "${MYSQL_CONTAINER}" "${N8N_CONTAINER}"; do
  container_running "${production_container}" || {
    log "ERROR|production_container_not_running|${production_container}"
    exit 1
  }
done

[[ "$(docker inspect "${API_CONTAINER}" --format '{{.Config.Image}}')" == "ai-middle-office-app:20260805_161737" ]] || {
  log "ERROR|production_api_image_changed"
  exit 1
}
[[ "$(docker inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')" == "${EXPECTED_CANDIDATE_ID}" ]] || {
  log "ERROR|candidate_image_id_mismatch"
  exit 1
}

actual_backup_sha256="$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"
[[ "${actual_backup_sha256}" == "${EXPECTED_BACKUP_SHA256}" ]] || {
  log "ERROR|backup_sha256_mismatch|actual=${actual_backup_sha256}"
  exit 1
}
gzip -t "${BACKUP_FILE}"
log "PASS|drill_backup_gate|sha256=${actual_backup_sha256}"

available_kib="$(df -Pk /data | awk 'NR==2 {print $4}')"
mem_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
[[ "${available_kib}" -ge 5242880 ]] || {
  log "ERROR|drill_disk_below_5gib|available_kib=${available_kib}"
  exit 1
}
[[ "${mem_available_kib}" -ge 4194304 ]] || {
  log "ERROR|drill_memory_below_4gib|available_kib=${mem_available_kib}"
  exit 1
}
log "PASS|drill_capacity_gate|disk_available_kib=${available_kib}|memory_available_kib=${mem_available_kib}"

temp_dir="$(mktemp -d /root/quote-phase1-migration-drill.XXXXXXXX)"
chmod 0700 "${temp_dir}"
root_password_file="${temp_dir}/mysql-root-password"
root_cnf="${temp_dir}/root.cnf"
migration_env="${temp_dir}/migration.env"
account_sql="${temp_dir}/create-drill-migrator.sql"

python3 - "${root_password_file}" "${root_cnf}" "${migration_env}" "${account_sql}" <<'PY'
import os
import secrets
import sys
from urllib.parse import quote


root_password_file, root_cnf, migration_env, account_sql = sys.argv[1:]
root_password = secrets.token_urlsafe(32)
migrator_password = secrets.token_urlsafe(32)


def option(value):
    value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def literal(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


url = (
    "mysql+pymysql://drill_migrator:"
    + quote(migrator_password, safe="")
    + "@drill-mysql:3306/ai_quotation?charset=utf8mb4"
)

outputs = {
    root_password_file: root_password + "\n",
    root_cnf: "\n".join(
        [
            "[client]",
            'user="root"',
            f"password={option(root_password)}",
            "protocol=socket",
            "",
            "[mysql]",
            "max-allowed-packet=1G",
            "",
        ]
    ),
    migration_env: f"MIGRATION_DATABASE_URL={url}\n",
    account_sql: "\n".join(
        [
            (
                "CREATE USER 'drill_migrator'@'%' "
                "IDENTIFIED WITH caching_sha2_password BY "
                + literal(migrator_password)
                + " PASSWORD EXPIRE NEVER;"
            ),
            (
                "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES "
                "ON `ai_quotation`.* TO 'drill_migrator'@'%';"
            ),
            "",
        ]
    ),
}

for path, content in outputs.items():
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    os.chmod(path, 0o600)
PY

docker network create --internal "${DRILL_NETWORK}" >/dev/null
network_created=true
docker volume create "${DRILL_VOLUME}" >/dev/null
volume_created=true
container_created=true

docker run --detach \
  --name "${DRILL_CONTAINER}" \
  --network "${DRILL_NETWORK}" \
  --network-alias drill-mysql \
  --memory 3g \
  --memory-swap 4g \
  --cpus 1.5 \
  --pids-limit 512 \
  --env MYSQL_ROOT_PASSWORD_FILE=/run/secrets/mysql-root-password \
  --volume "${root_password_file}:/run/secrets/mysql-root-password:ro" \
  --volume "${DRILL_VOLUME}:/var/lib/mysql" \
  "${MYSQL_IMAGE}" \
  --max-allowed-packet=1G \
  --net-read-timeout=600 \
  --net-write-timeout=600 \
  --innodb-buffer-pool-size=512M >/dev/null

docker cp "${root_cnf}" "${DRILL_CONTAINER}:/tmp/root.cnf" >/dev/null
docker exec --user root "${DRILL_CONTAINER}" chmod 0600 /tmp/root.cnf

drill_ready=false
for _attempt in $(seq 1 120); do
  if ! container_running "${DRILL_CONTAINER}"; then
    docker logs --tail 160 "${DRILL_CONTAINER}" >&2 || true
    log "ERROR|drill_mysql_stopped"
    exit 1
  fi
  if docker exec "${DRILL_CONTAINER}" \
    mysqladmin --defaults-extra-file=/tmp/root.cnf ping --silent >/dev/null 2>&1; then
    drill_ready=true
    break
  fi
  sleep 2
done
[[ "${drill_ready}" == true ]] || {
  docker exec "${DRILL_CONTAINER}" \
    mysqladmin --defaults-extra-file=/tmp/root.cnf ping --silent 2>&1 || true
  docker logs --tail 160 "${DRILL_CONTAINER}" >&2 || true
  log "ERROR|drill_mysql_ready_timeout"
  exit 1
}
log "PASS|drill_mysql=ready"

gzip -cd "${BACKUP_FILE}" | docker exec -i "${DRILL_CONTAINER}" \
  mysql --defaults-extra-file=/tmp/root.cnf
log "PASS|drill_restore=completed"

restored_head="$(docker exec "${DRILL_CONTAINER}" mysql \
  --defaults-extra-file=/tmp/root.cnf --batch --skip-column-names \
  --database=ai_quotation --execute='SELECT version_num FROM alembic_version')"
[[ "${restored_head}" == "20260801_0081" ]] || {
  log "ERROR|drill_restore_head_mismatch|head=${restored_head}"
  exit 1
}
log "PASS|drill_restore_head=${restored_head}"

docker exec -i "${DRILL_CONTAINER}" mysql \
  --defaults-extra-file=/tmp/root.cnf < "${account_sql}"
log "PASS|drill_migrator=least_privilege_ready"

docker run --rm \
  --network "${DRILL_NETWORK}" \
  --env-file "${migration_env}" \
  "${CANDIDATE_IMAGE}" \
  python -m alembic upgrade head
log "PASS|drill_alembic_upgrade=completed"

read -r migrated_head new_tables new_columns foreign_keys named_indexes invalid_users quota_rows push_rows existing_job_values < <(
  docker exec "${DRILL_CONTAINER}" mysql \
    --defaults-extra-file=/tmp/root.cnf \
    --database=ai_quotation \
    --batch \
    --skip-column-names \
    --execute="
      SELECT
        (SELECT version_num FROM alembic_version),
        (
          SELECT COUNT(*) FROM information_schema.TABLES
          WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME IN ('quote_push_attempts', 'quote_quota_reservations')
        ),
        (
          SELECT COUNT(*) FROM information_schema.COLUMNS
          WHERE TABLE_SCHEMA = DATABASE()
            AND (
              (TABLE_NAME = 'users' AND COLUMN_NAME = 'quota_reserved')
              OR
              (TABLE_NAME = 'quote_jobs' AND COLUMN_NAME IN
                ('source_job_id', 'attempt_id', 'started_at'))
            )
        ),
        (
          SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
          WHERE CONSTRAINT_SCHEMA = DATABASE()
            AND REFERENCED_TABLE_NAME IS NOT NULL
            AND (
              (
                TABLE_NAME = 'quote_jobs'
                AND COLUMN_NAME = 'source_job_id'
                AND REFERENCED_TABLE_NAME = 'quote_jobs'
                AND REFERENCED_COLUMN_NAME = 'job_id'
              )
              OR
              (
                TABLE_NAME = 'quote_quota_reservations'
                AND (
                  (COLUMN_NAME = 'quote_job_id' AND REFERENCED_TABLE_NAME = 'quote_jobs' AND REFERENCED_COLUMN_NAME = 'job_id')
                  OR
                  (COLUMN_NAME = 'user_id' AND REFERENCED_TABLE_NAME = 'users' AND REFERENCED_COLUMN_NAME = 'id')
                )
              )
              OR
              (
                TABLE_NAME = 'quote_push_attempts'
                AND (
                  (COLUMN_NAME = 'quote_job_id' AND REFERENCED_TABLE_NAME = 'quote_jobs' AND REFERENCED_COLUMN_NAME = 'job_id')
                  OR
                  (COLUMN_NAME = 'quote_history_id' AND REFERENCED_TABLE_NAME = 'quote_history' AND REFERENCED_COLUMN_NAME = 'id')
                )
              )
            )
        ),
        (
          SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS
          WHERE TABLE_SCHEMA = DATABASE()
            AND INDEX_NAME IN (
              'ix_quote_jobs_source_job_id',
              'ix_quote_jobs_attempt_id',
              'ix_quote_quota_reservations_quote_job_id',
              'ix_quote_quota_reservations_user_id',
              'ix_quote_quota_reservations_status',
              'ix_quote_push_attempts_quote_job_id',
              'ix_quote_push_attempts_username',
              'ix_quote_push_attempts_payload_sha256',
              'ix_quote_push_attempts_status',
              'ix_quote_push_attempts_quote_history_id'
            )
        ),
        (SELECT COUNT(*) FROM users WHERE quota_reserved IS NULL OR quota_reserved <> 0),
        (SELECT COUNT(*) FROM quote_quota_reservations),
        (SELECT COUNT(*) FROM quote_push_attempts),
        (
          SELECT COUNT(*) FROM quote_jobs
          WHERE source_job_id IS NOT NULL OR attempt_id IS NOT NULL OR started_at IS NOT NULL
        );
    "
)

log "RESULT|drill_schema=head=${migrated_head}|new_tables=${new_tables}|new_columns=${new_columns}|foreign_keys=${foreign_keys}|named_indexes=${named_indexes}"
log "RESULT|drill_data_defaults=invalid_users=${invalid_users}|quota_rows=${quota_rows}|push_rows=${push_rows}|existing_job_values=${existing_job_values}"

[[ "${migrated_head}" == "20260808_0082" ]]
[[ "${new_tables}" == "2" ]]
[[ "${new_columns}" == "4" ]]
[[ "${foreign_keys}" == "5" ]]
[[ "${named_indexes}" == "10" ]]
[[ "${invalid_users}" == "0" ]]
[[ "${quota_rows}" == "0" ]]
[[ "${push_rows}" == "0" ]]
[[ "${existing_job_values}" == "0" ]]

production_head="$(docker exec -i "${API_CONTAINER}" python - <<'PY'
from sqlalchemy import text
from app.core.database import engine

with engine.connect() as connection:
    print(connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one())
PY
)"
[[ "${production_head}" == "20260801_0081" ]] || {
  log "ERROR|production_head_changed_during_drill|head=${production_head}"
  exit 1
}

for production_container in "${API_CONTAINER}" "${WORKER_CONTAINER}" "${MYSQL_CONTAINER}" "${N8N_CONTAINER}"; do
  container_running "${production_container}" || {
    log "ERROR|production_container_changed_during_drill|${production_container}"
    exit 1
  }
done

log "RESULT|production_unchanged=head=${production_head}|containers=running|n8n_not_modified=true"
success=true
exit 0
