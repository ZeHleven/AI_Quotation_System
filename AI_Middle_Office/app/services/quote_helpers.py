import hashlib
import hmac
import json
import re
from datetime import datetime

from app.core.config import settings


NUMBERED_LIST_ITEM = re.compile(r"^\s*(?:[\(（]?\d+[\)）\.．、]|[一二三四五六七八九十]+[、\.．])\s*(?P<item>.+?)\s*$")
QUOTE_ITEM_LINE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:㎡|m2|m²|平方米|平米|平方|m|米|延米|延长米|项|个|套|处|立方米|m3|m³|方|kg|公斤|吨|t)",
    re.IGNORECASE,
)


def normalize_quote_request_text(text: str | None) -> str:
    """Flatten quote item lists before sending them to n8n/Dify."""
    if not text:
        return ""

    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n")]
    non_empty_lines = [line for line in lines if line]
    if not any(NUMBERED_LIST_ITEM.match(line) for line in lines if line):
        if len(non_empty_lines) >= 2 and sum(1 for line in non_empty_lines if QUOTE_ITEM_LINE.search(line)) >= 2:
            return "；".join(line.strip("；;") for line in non_empty_lines).strip()
        return normalized.strip()

    prefix_parts: list[str] = []
    list_items: list[str] = []
    suffix_parts: list[str] = []
    in_list = False
    for line in lines:
        if not line:
            continue
        match = NUMBERED_LIST_ITEM.match(line)
        if match:
            in_list = True
            item = match.group("item").strip().strip("；;")
            if item:
                list_items.append(item)
            continue
        if in_list:
            suffix_parts.append(line)
        else:
            prefix_parts.append(line)

    parts: list[str] = []
    if prefix_parts:
        parts.append(" ".join(prefix_parts))
    if list_items:
        parts.append("；".join(list_items))
    if suffix_parts:
        parts.append(" ".join(suffix_parts))
    return " ".join(parts).strip()


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
