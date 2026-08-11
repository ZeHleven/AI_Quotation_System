#!/usr/bin/env bash
set -u

echo "CONTAINERS_BEGIN"
docker ps --format '{{.Names}}|{{.Status}}' \
    | grep -E 'ai-middle-office|api|worker' || true
echo "CONTAINERS_END"

while IFS= read -r container; do
    case "${container}" in
        *api*)
            printf 'TRUSTED_HOSTS_AUDIT|%s|' "${container}"
            docker exec "${container}" python -c '
import os

hosts = [item.strip() for item in os.getenv("TRUSTED_HOSTS", "").split(",") if item.strip()]
print(
    "count=%d|domain=%s|localhost=%s|loopback=%s|wildcard=%s"
    % (
        len(hosts),
        str("www.qskingship.com" in hosts).lower(),
        str("localhost" in hosts).lower(),
        str("127.0.0.1" in hosts).lower(),
        str("*" in hosts).lower(),
    )
)
' 2>/dev/null || echo unavailable
            ;;
    esac
done < <(docker ps --format '{{.Names}}')

printf 'NGINX_ACTIVE|'
systemctl is-active nginx 2>/dev/null || true
printf 'LISTENER_443|'
ss -lntH 'sport = :443' 2>/dev/null | wc -l
