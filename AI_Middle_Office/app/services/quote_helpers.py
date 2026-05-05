import hashlib
import hmac
import json
import re
from datetime import datetime

from app.core.config import settings


def sign_payload(body: dict) -> dict:
    """Return backward-compatible n8n auth headers plus an HMAC signature."""
    canonical_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(
        settings.webhook_secret.encode("utf-8"),
        canonical_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Webhook-Secret": settings.webhook_secret,
        "X-Webhook-Signature": signature,
    }


def build_quote_filename(username: str) -> str:
    safe_user = re.sub(r"[^A-Za-z0-9_-]+", "_", username or "user").strip("_") or "user"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"quote_{timestamp}_{safe_user}.xlsx"


def attach_quote_filename(payload: dict, username: str) -> dict:
    payload = dict(payload)
    filename = payload.get("excel_filename") or payload.get("filename") or build_quote_filename(username)
    display_title = payload.get("display_title") or f"AI报价单-{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    payload.update(
        {
            "excel_filename": filename,
            "download_filename": filename,
            "filename": filename,
            "fileName": filename,
            "file_name": filename,
            "attachment_name": filename,
            "display_title": display_title,
        }
    )
    return payload
