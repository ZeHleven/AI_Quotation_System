#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${EUID}" -eq 0 ]] || {
  echo "ERROR|root_required" >&2
  exit 1
}

for container in ai-middle-office-app-api-1 n8n dify-nginx-1 dify-api-1 dify-worker-1; do
  if ! docker inspect "${container}" >/dev/null 2>&1 || \
    [[ "$(docker inspect "${container}" --format '{{.State.Running}}')" != true ]]; then
    echo "ERROR|required_container_not_running|${container}" >&2
    exit 1
  fi
done

if docker inspect dify-worker_beat-1 >/dev/null 2>&1 && \
  [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|dify_worker_beat_must_remain_stopped" >&2
  exit 1
fi

timeout 210 docker exec -i ai-middle-office-app-api-1 python - <<'PY'
import time
import uuid

import httpx

from app.services.quote_helpers import sign_payload


payload = {
    "text": {
        "content": (
            "迁移暗验证：墙面乳胶漆涂刷1平方米。"
            "仅验证报价预审链路，不生成文件，不确认下发。"
        )
    },
    "conversationId": str(uuid.uuid4()),
}
started = time.monotonic()
try:
    with httpx.Client(timeout=180) as client:
        response = client.post(
            "http://n8n:5678/webhook/budget-calc-no-rag",
            json=payload,
            headers=sign_payload(payload),
        )
    elapsed = round(time.monotonic() - started, 2)
    if response.status_code != 200:
        print(
            "ERROR|target_quote_e2e_dark_smoke"
            f"|http_status={response.status_code}|elapsed_seconds={elapsed}",
            flush=True,
        )
        raise SystemExit(1)
    try:
        parsed = response.json()
    except Exception:
        print(
            "ERROR|target_quote_e2e_dark_smoke"
            f"|invalid_json=true|elapsed_seconds={elapsed}",
            flush=True,
        )
        raise SystemExit(1)
    if parsed in (None, "", [], {}):
        print(
            "ERROR|target_quote_e2e_dark_smoke"
            f"|empty_json=true|elapsed_seconds={elapsed}",
            flush=True,
        )
        raise SystemExit(1)

    response_kind = type(parsed).__name__
    item_count = len(parsed) if isinstance(parsed, (list, dict)) else 1
    print(
        "PASS|target_quote_e2e_dark_smoke"
        f"|http_status=200|elapsed_seconds={elapsed}"
        f"|response_kind={response_kind}|top_level_items={item_count}",
        flush=True,
    )
except httpx.HTTPError as error:
    print(
        "ERROR|target_quote_e2e_dark_smoke"
        f"|error_type={type(error).__name__}",
        flush=True,
    )
    raise SystemExit(1)
PY

if docker inspect dify-worker_beat-1 >/dev/null 2>&1 && \
  [[ "$(docker inspect dify-worker_beat-1 --format '{{.State.Running}}')" == true ]]; then
  echo "ERROR|dify_worker_beat_started_unexpectedly" >&2
  exit 1
fi

echo "RESULT|target_quote_e2e_dark_smoke=passed"
echo "INFO|no_push_workflow_called"
echo "INFO|source_app_config_unchanged"
echo "INFO|next_gate=final_cutover_window"
