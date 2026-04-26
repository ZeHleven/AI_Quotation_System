import hashlib
import hmac
import json
import re

from app.api.v1.chat import _attach_quote_filename, _build_quote_filename, _sign_payload
from app.core.config import settings


def test_build_quote_filename_is_ascii_and_stable():
    filename = _build_quote_filename("张 三/admin")

    assert filename.endswith("_admin.xlsx")
    assert re.match(r"^quote_\d{8}_\d{6}_admin\.xlsx$", filename)
    filename.encode("ascii")


def test_attach_quote_filename_sets_common_aliases():
    payload = _attach_quote_filename({"project_details": []}, "admin")

    filename = payload["excel_filename"]
    assert payload["download_filename"] == filename
    assert payload["filename"] == filename
    assert payload["fileName"] == filename
    assert payload["file_name"] == filename
    assert payload["attachment_name"] == filename
    assert payload["display_title"]


def test_sign_payload_keeps_secret_header_and_adds_hmac_signature():
    body = {"b": 2, "a": "中文"}
    headers = _sign_payload(body)
    canonical_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    expected_signature = hmac.new(
        settings.webhook_secret.encode("utf-8"),
        canonical_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert headers["X-Webhook-Secret"] == settings.webhook_secret
    assert headers["X-Webhook-Signature"] == expected_signature
