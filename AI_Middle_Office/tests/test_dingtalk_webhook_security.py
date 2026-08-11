import base64
import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.dingtalk_webhook import (
    build_signed_dingtalk_webhook,
    describe_dingtalk_webhook_input,
    normalize_dingtalk_webhook_input,
    validate_dingtalk_custom_robot_config,
)
from scripts.security_configure_dingtalk_alert import update_env_file


def test_build_signed_webhook_uses_official_hmac_contract():
    timestamp = 1700000000123
    secret = "SEC-test-signing-secret"
    result = build_signed_dingtalk_webhook(
        "https://oapi.dingtalk.com/robot/send?access_token=test-access-token",
        secret,
        timestamp_ms=timestamp,
    )

    parsed = urlsplit(result)
    query = parse_qs(parsed.query)
    expected = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}\n{secret}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")

    assert parsed.scheme == "https"
    assert parsed.hostname == "oapi.dingtalk.com"
    assert parsed.path == "/robot/send"
    assert query["access_token"] == ["test-access-token"]
    assert query["timestamp"] == [str(timestamp)]
    assert query["sign"] == [expected]


@pytest.mark.parametrize(
    "webhook,secret",
    [
        ("http://oapi.dingtalk.com/robot/send?access_token=value", "SEC-test-signing-secret"),
        ("https://example.com/robot/send?access_token=value", "SEC-test-signing-secret"),
        ("https://oapi.dingtalk.com:444/robot/send?access_token=value", "SEC-test-signing-secret"),
        ("https://oapi.dingtalk.com/other?access_token=value", "SEC-test-signing-secret"),
        ("https://oapi.dingtalk.com/robot/send", "SEC-test-signing-secret"),
        ("https://oapi.dingtalk.com/robot/send?access_token=value#fragment", "SEC-test-signing-secret"),
        ("https://oapi.dingtalk.com/robot/send?access_token=value", "short"),
    ],
)
def test_invalid_or_unsigned_robot_configuration_is_rejected(webhook, secret):
    with pytest.raises(ValueError):
        validate_dingtalk_custom_robot_config(webhook, secret)


def test_secure_config_writer_replaces_and_deduplicates_credentials(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KEEP=value\n"
        "ALERT_DINGTALK_WEBHOOK=old-one\n"
        "ALERT_DINGTALK_SECRET=old-secret\n"
        "ALERT_DINGTALK_WEBHOOK=old-two\n",
        encoding="utf-8",
    )

    update_env_file(
        env_file,
        {
            "ALERT_DINGTALK_WEBHOOK": "new-webhook",
            "ALERT_DINGTALK_SECRET": "new-secret",
        },
    )

    updated = env_file.read_text(encoding="utf-8")
    assert "KEEP=value" in updated
    assert "old-one" not in updated
    assert "old-two" not in updated
    assert "old-secret" not in updated
    assert updated.count("ALERT_DINGTALK_WEBHOOK=") == 1
    assert updated.count("ALERT_DINGTALK_SECRET=") == 1


def test_clipboard_wrappers_are_removed_but_labelled_text_is_not_accepted():
    wrapped = "<https://oapi.dingtalk.com/robot/send?access_token=test-access-token>"
    labelled = "Webhook: https://oapi.dingtalk.com/robot/send?access_token=test-access-token"

    assert normalize_dingtalk_webhook_input(wrapped).startswith("https://")
    validate_dingtalk_custom_robot_config(wrapped, "SEC-test-signing-secret")
    labelled_diagnostic = describe_dingtalk_webhook_input(labelled)
    assert labelled_diagnostic["https"] is False
    assert labelled_diagnostic["host"] == "未识别"
