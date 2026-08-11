#!/usr/bin/env bash
# Production backup helper for the CentOS side of AI Middle Office.
# Run on CentOS from /opt/rag_service. It does not delete old backups.

set -euo pipefail
umask 077

RAG_DIR="${RAG_DIR:-/opt/rag_service}"
ENV_FILE="${ENV_FILE:-$RAG_DIR/.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

BACKUP_ROOT="${BACKUP_ROOT:-$RAG_DIR/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
DEST="$BACKUP_ROOT/$STAMP"
COMPOSE_FILE="${COMPOSE_FILE:-$RAG_DIR/docker-compose.yml}"

mkdir -p "$DEST"

log() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

have_container() {
  docker ps --format '{{.Names}}' | grep -Fxq "$1"
}

backup_file() {
  local src="$1"
  local name="$2"
  if [ -f "$src" ]; then
    cp -a "$src" "$DEST/$name"
    log "Backed up $src"
  else
    warn "Skip missing file: $src"
  fi
}

backup_dir_tar() {
  local src="$1"
  local name="$2"
  if [ -d "$src" ]; then
    if tar -czf "$DEST/$name.tgz" -C "$(dirname "$src")" "$(basename "$src")"; then
      log "Backed up directory $src"
    else
      warn "Failed to back up directory: $src"
    fi
  else
    warn "Skip missing directory: $src"
  fi
}

backup_mysql() {
  local mysql_user="${MYSQL_USER:-}"
  local mysql_password="${MYSQL_PASSWORD:-}"
  local mysql_database="${MYSQL_DATABASE:-}"
  local mysql_container="${MYSQL_CONTAINER:-}"
  local mysql_host="${MYSQL_HOST:-127.0.0.1}"
  local mysql_port="${MYSQL_PORT:-3306}"
  local db_args="--all-databases"
  local tablespace_args=""

  if [ "${MYSQLDUMP_NO_TABLESPACES:-true}" != "false" ]; then
    tablespace_args="--no-tablespaces"
  fi

  if [ -n "$mysql_database" ]; then
    db_args="--databases $mysql_database"
  fi

  if [ -n "$mysql_container" ] && have_container "$mysql_container"; then
    if [ -z "$mysql_user" ]; then
      warn "MYSQL_CONTAINER is set but MYSQL_USER is empty; skip MySQL backup"
      return
    fi
    log "Backing up MySQL through container $mysql_container"
    docker exec -e MYSQL_PWD="$mysql_password" "$mysql_container" sh -c \
      "mysqldump -u\"$mysql_user\" --single-transaction --routines --triggers --events $tablespace_args $db_args" \
      > "$DEST/mysql.sql"
    return
  fi

  if command -v mysqldump >/dev/null 2>&1 && [ -n "$mysql_user" ]; then
    log "Backing up MySQL through host $mysql_host:$mysql_port"
    MYSQL_PWD="$mysql_password" mysqldump \
      -h "$mysql_host" \
      -P "$mysql_port" \
      -u "$mysql_user" \
      --single-transaction \
      --routines \
      --triggers \
      --events \
      $tablespace_args \
      $db_args > "$DEST/mysql.sql"
    return
  fi

  warn "Skip MySQL backup. Set MYSQL_CONTAINER or install mysqldump and set MYSQL_USER."
}

backup_n8n() {
  local n8n_container="${N8N_CONTAINER:-n8n}"
  if have_container "$n8n_container"; then
    log "Exporting n8n workflows from $n8n_container"
    if docker exec "$n8n_container" n8n export:workflow --all --output=/tmp/n8n_workflows.json >/dev/null 2>&1; then
      docker cp "$n8n_container:/tmp/n8n_workflows.json" "$DEST/n8n_workflows.json"
      return
    fi
    warn "n8n CLI export failed in container $n8n_container"
  else
    warn "n8n container not found: $n8n_container"
  fi

  backup_file "$RAG_DIR/n8n_workflows_backup.json" "n8n_workflows_backup.json"
}

backup_milvus_volumes() {
  if [ "${STOP_MILVUS_FOR_BACKUP:-false}" = "true" ]; then
    warn "Stopping Milvus stack for a consistent volume backup"
    docker compose -f "$COMPOSE_FILE" stop standalone etcd minio
  else
    warn "Milvus volumes are backed up online. Set STOP_MILVUS_FOR_BACKUP=true for a cold volume snapshot."
  fi

  backup_dir_tar "$RAG_DIR/volumes/etcd" "milvus_etcd"
  backup_dir_tar "$RAG_DIR/volumes/minio" "milvus_minio"
  backup_dir_tar "$RAG_DIR/volumes/milvus" "milvus_data"

  if [ "${STOP_MILVUS_FOR_BACKUP:-false}" = "true" ]; then
    docker compose -f "$COMPOSE_FILE" up -d etcd minio standalone
    log "Milvus stack restarted"
  fi
}

log "Backup destination: $DEST"

backup_file "$RAG_DIR/rag_materials.json" "rag_materials.json"
backup_file "$RAG_DIR/docker-compose.yml" "docker-compose.yml"
if [ "${BACKUP_INCLUDE_ENV:-false}" = "true" ]; then
  warn "BACKUP_INCLUDE_ENV=true: the backup will contain live secrets and must be encrypted before leaving this host"
  backup_file "$ENV_FILE" "rag_service.env"
else
  warn "Runtime .env excluded from backup; restore secrets from the approved secret store"
fi
backup_mysql
backup_n8n
backup_milvus_volumes
backup_dir_tar "$RAG_DIR/volumes/quote-minio" "quote_minio"

(
  cd "$DEST"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 -r sha256sum > SHA256SUMS
)

ln -sfn "$DEST" "$BACKUP_ROOT/latest"
log "Backup completed: $DEST"
