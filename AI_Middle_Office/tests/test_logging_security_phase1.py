import json
import logging

from app.core.logging import JsonFormatter, redact_log_text


def test_log_redaction_removes_query_and_bearer_credentials():
    raw = (
        "POST https://example.invalid/hook?access_token=token-value&sign=signature-value "
        "Authorization: Bearer bearer-value"
    )

    sanitized = redact_log_text(raw)

    assert "token-value" not in sanitized
    assert "signature-value" not in sanitized
    assert "bearer-value" not in sanitized
    assert "[REDACTED]" in sanitized


def test_json_formatter_never_serializes_sensitive_httpx_url():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: POST https://example.invalid/send?access_token=must-not-log "200 OK"',
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert "must-not-log" not in payload["message"]
    assert "access_token=[REDACTED]" in payload["message"]


def test_log_redaction_removes_json_and_mapping_style_secrets():
    raw = (
        '{"password": "json-password", "client_secret": "json-client-secret"} '
        "{'jwt_secret_key': 'mapping-jwt-secret'}"
    )

    sanitized = redact_log_text(raw)

    assert "json-password" not in sanitized
    assert "json-client-secret" not in sanitized
    assert "mapping-jwt-secret" not in sanitized
    assert sanitized.count("[REDACTED]") == 3


def test_log_redaction_removes_dingtalk_signing_secret_assignment():
    sanitized = redact_log_text("ALERT_DINGTALK_SECRET=must-not-appear")

    assert "must-not-appear" not in sanitized
    assert sanitized == "ALERT_DINGTALK_SECRET=[REDACTED]"
