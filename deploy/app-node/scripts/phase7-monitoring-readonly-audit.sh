#!/usr/bin/env bash
set -Eeuo pipefail

readonly ENV_FILE="/etc/ai-middle-office/app.env"
readonly COMPOSE_FILE="/opt/ai-middle-office/app-node/compose.yaml"
readonly CERT_FILE="/etc/letsencrypt/live/www.qskingship.com/fullchain.pem"
readonly REPORT_ROOT="/home/aiadmin"
readonly STAMP="$(date +%Y%m%d_%H%M%S)"
readonly REPORT="${REPORT_ROOT}/ai-phase7-monitoring-audit-${STAMP}.txt"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR|run_as_root" >&2
    exit 1
fi

umask 077
exec > >(tee "${REPORT}") 2>&1

env_value_present() {
    local key="$1"
    awk -F= -v wanted="${key}" '
        $1 == wanted {
            value = substr($0, index($0, "=") + 1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            if (value != "" && value !~ /^REPLACE_/ && value !~ /^CHANGE_ME/) {
                found = 1
            }
        }
        END { exit(found ? 0 : 1) }
    ' "${ENV_FILE}"
}

env_value_is_false() {
    local key="$1"
    awk -F= -v wanted="${key}" '
        $1 == wanted {
            value = tolower(substr($0, index($0, "=") + 1))
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            if (value == "false" || value == "0" || value == "no" || value == "off") {
                found = 1
            }
        }
        END { exit(found ? 0 : 1) }
    ' "${ENV_FILE}"
}

echo "TIMESTAMP=$(date --iso-8601=seconds)"

for required_file in "${ENV_FILE}" "${COMPOSE_FILE}" "${CERT_FILE}"; do
    if [[ ! -r "${required_file}" ]]; then
        echo "ERROR|required_file_not_readable|${required_file}"
        exit 1
    fi
done
echo "PASS|required_files_readable"

if env_value_is_false PUBLIC_ACCESS_ENABLED; then
    echo "PASS|public_access_enabled_false"
else
    echo "FAIL|public_access_enabled_not_false"
fi

for key in ALERT_DINGTALK_WEBHOOK ALERT_DINGTALK_SECRET; do
    if env_value_present "${key}"; then
        echo "PASS|${key}_configured"
    else
        echo "FAIL|${key}_not_configured"
    fi
done
if env_value_present ALERT_CHECK_INTERVAL_SECONDS; then
    echo "PASS|alert_check_interval_configured"
else
    echo "INFO|alert_check_interval_uses_application_default"
fi

for unit in nginx firewalld docker; do
    echo "SERVICE|${unit}|active=$(systemctl is-active "${unit}" 2>/dev/null || true)|enabled=$(systemctl is-enabled "${unit}" 2>/dev/null || true)"
done

api_http="$(
    curl --silent --show-error --max-time 20 \
        --output /dev/null --write-out '%{http_code}' \
        http://127.0.0.1:9000/health/ready || true
)"
echo "API_READY_HTTP=${api_http}"

certificate_end="$(openssl x509 -in "${CERT_FILE}" -noout -enddate | cut -d= -f2-)"
certificate_end_epoch="$(date -d "${certificate_end}" +%s)"
now_epoch="$(date +%s)"
certificate_days="$(( (certificate_end_epoch - now_epoch) / 86400 ))"
echo "CERTIFICATE_DAYS_REMAINING=${certificate_days}"
if (( certificate_days >= 30 )); then
    echo "PASS|certificate_more_than_30_days_remaining"
else
    echo "FAIL|certificate_less_than_30_days_remaining"
fi

if systemctl list-unit-files --no-pager | awk '{print $1}' | grep -Fxq 'certbot.timer'; then
    echo "CERTBOT_TIMER|present|active=$(systemctl is-active certbot.timer 2>/dev/null || true)|enabled=$(systemctl is-enabled certbot.timer 2>/dev/null || true)"
else
    echo "CERTBOT_TIMER|not_present"
fi

echo "=== MATCHING_TIMERS ==="
systemctl list-timers --all --no-pager \
    | grep -Ei 'cert|monitor|health|backup|NEXT|UNIT' || true

echo "=== COMPOSE_STATUS ==="
docker compose --file "${COMPOSE_FILE}" ps --format '{{.Name}}|{{.State}}|{{.Status}}' 2>/dev/null \
    || docker compose --file "${COMPOSE_FILE}" ps

echo "SELINUX=$(getenforce 2>/dev/null || echo unknown)"
echo "FIREWALL_HTTPS_QUERY=$(firewall-cmd --query-service=https 2>/dev/null || true)"
echo "LISTENER_443_COUNT=$(ss -H -lnt 'sport = :443' | wc -l)"

chown aiadmin:aiadmin "${REPORT}"
chmod 0600 "${REPORT}"

echo "REPORT=${REPORT}"
echo "PASS|phase7_monitoring_readonly_audit"
