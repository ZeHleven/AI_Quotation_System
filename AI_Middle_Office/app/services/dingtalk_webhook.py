"""Secure URL validation and signing for DingTalk custom robots."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DINGTALK_CUSTOM_ROBOT_HOSTS = frozenset({"oapi.dingtalk.com"})
MIN_DINGTALK_SIGN_SECRET_LENGTH = 16


def normalize_dingtalk_webhook_input(webhook: str) -> str:
    """Remove harmless clipboard wrappers without accepting labelled text."""

    return (webhook or "").strip().strip("\"'<> ")


def describe_dingtalk_webhook_input(webhook: str) -> dict[str, str | bool]:
    """Return non-secret endpoint metadata suitable for operator diagnostics."""

    parsed = urlsplit(normalize_dingtalk_webhook_input(webhook))
    hostname = parsed.hostname or "未识别"
    return {
        "https": parsed.scheme.lower() == "https",
        "host": hostname,
        "official_host": parsed.hostname in DINGTALK_CUSTOM_ROBOT_HOSTS,
        "standard_path": parsed.path == "/robot/send",
        "has_access_token": bool(dict(parse_qsl(parsed.query, keep_blank_values=True)).get("access_token")),
    }


def validate_dingtalk_custom_robot_config(webhook: str, sign_secret: str) -> None:
    """Reject unsafe or incomplete custom-robot configuration."""

    parsed = urlsplit(normalize_dingtalk_webhook_input(webhook))
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("DingTalk webhook contains an invalid port") from None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname not in DINGTALK_CUSTOM_ROBOT_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/robot/send"
        or bool(parsed.fragment)
    ):
        raise ValueError("DingTalk webhook must use the official HTTPS custom-robot endpoint")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if not query.get("access_token"):
        raise ValueError("DingTalk webhook is missing access_token")
    if len((sign_secret or "").strip()) < MIN_DINGTALK_SIGN_SECRET_LENGTH:
        raise ValueError("DingTalk signing secret is missing or too short")


def build_signed_dingtalk_webhook(
    webhook: str,
    sign_secret: str,
    *,
    timestamp_ms: int | None = None,
) -> str:
    """Append the official timestamp/sign query parameters to a webhook URL."""

    webhook = normalize_dingtalk_webhook_input(webhook)
    sign_secret = (sign_secret or "").strip()
    validate_dingtalk_custom_robot_config(webhook, sign_secret)

    timestamp = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    string_to_sign = f"{timestamp}\n{sign_secret}".encode("utf-8")
    digest = hmac.new(sign_secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("ascii")

    parsed = urlsplit(webhook)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"timestamp", "sign"}
    ]
    query.extend((("timestamp", str(timestamp)), ("sign", signature)))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
