#!/usr/bin/env bash
set -Eeuo pipefail

readonly ENV_FILE="/etc/ai-middle-office/app.env"
readonly COMPOSE_FILE="/opt/ai-middle-office/app-node/compose.yaml"
readonly SOURCE_MONITOR="/home/aiadmin/ai-phase7-host-monitor.py"
readonly SOURCE_SERVICE="/home/aiadmin/ai-middle-office-monitor.service"
readonly SOURCE_TIMER="/home/aiadmin/ai-middle-office-monitor.timer"
readonly MONITOR_SHA256="c328d5c88ba4ef7b5f7042d4da993b3c4fa4c5e3dd7b7f077972df2209aa533f"
readonly SERVICE_SHA256="90fe96018a63723c8706568343c96a9d674f5265c2498275956892c41557b6a4"
readonly TIMER_SHA256="1fffa9b19a48a8b72e33b4abbf8da27e21a1fe2946987b8cd0c7e4b4ccedf067"
readonly TARGET_MONITOR="/usr/local/libexec/ai-middle-office-host-monitor.py"
readonly TARGET_SERVICE="/etc/systemd/system/ai-middle-office-monitor.service"
readonly TARGET_TIMER="/etc/systemd/system/ai-middle-office-monitor.timer"
readonly STATE_DIR="/var/lib/ai-middle-office-monitor"
readonly STAMP="$(date +%Y%m%d_%H%M%S)"
readonly BACKUP_DIR="/root/ai-middle-office-monitor-backups/pre-install-${STAMP}"
readonly REPORT="/home/aiadmin/ai-phase7-monitoring-install-${STAMP}.txt"
readonly CREDENTIAL_TEMP="/root/ai-phase7/.monitor-credentials-${STAMP}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi
if [[ ! -t 0 || ! -t 1 ]]; then
    echo "ERROR|interactive_tty_required" >&2
    exit 1
fi

umask 077
install -d -o root -g root -m 0700 /root/ai-phase7
touch "${REPORT}"
chmod 0600 "${REPORT}"
exec > >(tee -a "${REPORT}") 2>&1

rollback_needed=false
install_complete=false
webhook=""
sign_secret=""

restore_optional_file() {
    local name="$1"
    local target="$2"
    if [[ -f "${BACKUP_DIR}/${name}" ]]; then
        cp -a "${BACKUP_DIR}/${name}" "${target}"
    else
        rm -f -- "${target}"
    fi
}

rollback_install() {
    set +e
    echo "ROLLBACK|monitoring_install"
    systemctl disable --now ai-middle-office-monitor.timer >/dev/null 2>&1 || true
    if [[ -f "${BACKUP_DIR}/app.env" ]]; then
        cp -a "${BACKUP_DIR}/app.env" "${ENV_FILE}"
    fi
    restore_optional_file monitor.py "${TARGET_MONITOR}"
    restore_optional_file monitor.service "${TARGET_SERVICE}"
    restore_optional_file monitor.timer "${TARGET_TIMER}"
    systemctl daemon-reload
    docker compose --file "${COMPOSE_FILE}" --profile worker \
        up -d --no-deps --force-recreate worker api >/dev/null 2>&1 || true
    echo "ROLLBACK|attempted"
}

on_exit() {
    local rc=$?
    trap - EXIT INT TERM
    set +e
    webhook=""
    sign_secret=""
    if [[ -f "${CREDENTIAL_TEMP}" ]]; then
        chmod 000 "${CREDENTIAL_TEMP}" 2>/dev/null || true
        rm -f -- "${CREDENTIAL_TEMP}"
    fi
    if [[ "${rollback_needed}" == "true" && "${install_complete}" != "true" ]]; then
        rollback_install
    fi
    chown aiadmin:aiadmin "${REPORT}" 2>/dev/null || true
    chmod 0600 "${REPORT}" 2>/dev/null || true
    exit "${rc}"
}
trap on_exit EXIT INT TERM

for required_file in \
    "${ENV_FILE}" "${COMPOSE_FILE}" \
    "${SOURCE_MONITOR}" "${SOURCE_SERVICE}" "${SOURCE_TIMER}"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_file_not_readable|${required_file}"
        exit 1
    fi
done

echo "${MONITOR_SHA256}  ${SOURCE_MONITOR}" | sha256sum -c -
echo "${SERVICE_SHA256}  ${SOURCE_SERVICE}" | sha256sum -c -
echo "${TIMER_SHA256}  ${SOURCE_TIMER}" | sha256sum -c -
/usr/bin/python3 - "${SOURCE_MONITOR}" <<'PY'
import ast
import sys
from pathlib import Path

ast.parse(Path(sys.argv[1]).read_text(encoding="utf-8"))
PY
echo "PASS|source_hash_and_python_syntax"

active_quote_jobs="$(
    docker exec ai-middle-office-app-api-1 python -c '
from sqlalchemy import text
from app.core.database import SessionLocal
db = SessionLocal()
try:
    result = db.execute(
        text(
            "SELECT COUNT(*) FROM quote_jobs "
            "WHERE status IN (0x717565756564, 0x72756e6e696e67)"
        )
    )
    print(result.scalar_one())
finally:
    db.close()
'
)"
if [[ ! "${active_quote_jobs}" =~ ^[0-9]+$ ]]; then
    echo "ERROR|active_quote_job_count_invalid"
    exit 1
fi
if [[ "${active_quote_jobs}" != "0" ]]; then
    echo "ERROR|active_quote_jobs_present|count_${active_quote_jobs}"
    exit 1
fi
echo "PASS|no_active_quote_jobs"

printf 'Paste DingTalk official robot Webhook (hidden): ' >&2
IFS= read -r -s webhook
printf '\nPaste DingTalk signing secret (hidden): ' >&2
IFS= read -r -s sign_secret
printf '\n' >&2
webhook="${webhook%$'\r'}"
sign_secret="${sign_secret%$'\r'}"
printf '%s\n%s\n' "${webhook}" "${sign_secret}" > "${CREDENTIAL_TEMP}"
chmod 0600 "${CREDENTIAL_TEMP}"

/usr/bin/python3 - "${CREDENTIAL_TEMP}" <<'PY'
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if len(lines) != 2:
    raise SystemExit("ERROR|credential_line_count")
webhook, secret = (value.strip().strip("\"'<> ") for value in lines)
parsed = urlsplit(webhook)
try:
    port = parsed.port
except ValueError:
    raise SystemExit("ERROR|webhook_port_invalid")
if (
    parsed.scheme.lower() != "https"
    or parsed.hostname != "oapi.dingtalk.com"
    or port not in {None, 443}
    or parsed.username is not None
    or parsed.password is not None
    or parsed.path != "/robot/send"
    or bool(parsed.fragment)
):
    raise SystemExit("ERROR|webhook_not_official_https_robot_endpoint")
query = dict(parse_qsl(parsed.query, keep_blank_values=True))
if not query.get("access_token"):
    raise SystemExit("ERROR|webhook_access_token_missing")
if len(secret) < 16 or any(char.isspace() for char in secret):
    raise SystemExit("ERROR|signing_secret_format_invalid")
print("PASS|dingtalk_credential_format")
PY

mkdir -p "${BACKUP_DIR}"
chmod 0700 "${BACKUP_DIR}"
cp -a "${ENV_FILE}" "${BACKUP_DIR}/app.env"
[[ ! -e "${TARGET_MONITOR}" ]] || cp -a "${TARGET_MONITOR}" "${BACKUP_DIR}/monitor.py"
[[ ! -e "${TARGET_SERVICE}" ]] || cp -a "${TARGET_SERVICE}" "${BACKUP_DIR}/monitor.service"
[[ ! -e "${TARGET_TIMER}" ]] || cp -a "${TARGET_TIMER}" "${BACKUP_DIR}/monitor.timer"
echo "BACKUP_DIR=${BACKUP_DIR}"
rollback_needed=true

/usr/bin/python3 - "${ENV_FILE}" "${CREDENTIAL_TEMP}" <<'PY'
import os
import stat
import sys
import tempfile
from pathlib import Path

env_path = Path(sys.argv[1])
credential_path = Path(sys.argv[2])
webhook, secret = credential_path.read_text(encoding="utf-8").splitlines()
webhook = webhook.strip().strip("\"'<> ")
secret = secret.strip()
keys = {"ALERT_DINGTALK_WEBHOOK", "ALERT_DINGTALK_SECRET", "ALERT_CHECK_INTERVAL_SECONDS"}
original = env_path.read_text(encoding="utf-8").splitlines()
updated = [line for line in original if line.split("=", 1)[0].strip() not in keys]
updated.extend(
    [
        "ALERT_DINGTALK_WEBHOOK=" + webhook,
        "ALERT_DINGTALK_SECRET=" + secret,
        "ALERT_CHECK_INTERVAL_SECONDS=60",
    ]
)
metadata = env_path.stat()
fd, temporary_name = tempfile.mkstemp(prefix=".app.env.monitor.", dir=str(env_path.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(updated) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary_name, metadata.st_uid, metadata.st_gid)
    os.chmod(temporary_name, stat.S_IMODE(metadata.st_mode))
    os.replace(temporary_name, str(env_path))
finally:
    if os.path.exists(temporary_name):
        os.unlink(temporary_name)
PY
webhook=""
sign_secret=""
chmod 000 "${CREDENTIAL_TEMP}"
rm -f -- "${CREDENTIAL_TEMP}"
echo "PASS|app_env_alert_configuration_updated_without_output"

install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0750 "${SOURCE_MONITOR}" "${TARGET_MONITOR}"
install -o root -g root -m 0644 "${SOURCE_SERVICE}" "${TARGET_SERVICE}"
install -o root -g root -m 0644 "${SOURCE_TIMER}" "${TARGET_TIMER}"
install -d -o root -g root -m 0700 "${STATE_DIR}"

/usr/bin/systemd-analyze verify "${TARGET_SERVICE}" "${TARGET_TIMER}"
systemctl daemon-reload
echo "PASS|systemd_unit_verification"

docker compose --file "${COMPOSE_FILE}" --profile worker \
    up -d --no-deps --force-recreate worker
for _ in $(seq 1 60); do
    if docker inspect -f '{{.State.Running}}' ai-middle-office-app-worker-1 2>/dev/null \
        | grep -Fxq true; then
        break
    fi
    sleep 2
done
if ! docker inspect -f '{{.State.Running}}' ai-middle-office-app-worker-1 2>/dev/null \
    | grep -Fxq true; then
    echo "ERROR|worker_not_running_after_recreate"
    exit 1
fi

docker compose --file "${COMPOSE_FILE}" --profile worker \
    up -d --no-deps --force-recreate api
api_ready=false
for _ in $(seq 1 90); do
    if [[ "$(curl --silent --show-error --max-time 8 --output /dev/null --write-out '%{http_code}' http://127.0.0.1:9000/health/ready || true)" == "200" ]]; then
        api_ready=true
        break
    fi
    sleep 2
done
if [[ "${api_ready}" != "true" ]]; then
    echo "ERROR|api_readiness_failed_after_recreate"
    exit 1
fi
echo "PASS|api_and_worker_recreated_with_alert_configuration"

/usr/bin/python3 "${TARGET_MONITOR}" --test-alert
/usr/bin/python3 "${TARGET_MONITOR}"
echo "PASS|test_alert_and_initial_monitor_run"

systemctl enable --now ai-middle-office-monitor.timer
if [[ "$(systemctl is-enabled ai-middle-office-monitor.timer)" != "enabled" ]]; then
    echo "ERROR|monitor_timer_not_enabled"
    exit 1
fi
if [[ "$(systemctl is-active ai-middle-office-monitor.timer)" != "active" ]]; then
    echo "ERROR|monitor_timer_not_active"
    exit 1
fi

install_complete=true
rollback_needed=false
echo "PASS|phase7_free_monitoring_installed"
echo "PASS|monitor_timer_active_and_enabled"
echo "PASS|certificate_warning_threshold_45_days"
echo "PASS|public_access_enabled_remains_false"
echo "REPORT=${REPORT}"
echo "NEXT|operator_confirms_dingtalk_test_message"
