import hashlib
import hmac
import json
import re

from app.api.v1.chat import _attach_quote_filename, _build_quote_filename, _sign_payload
from app.core.config import settings
from app.services.quote_helpers import normalize_quote_request_text


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


def test_normalize_quote_request_text_flattens_numbered_lists():
    text = (
        "请生成报价明细，只包含以下三项：\n"
        "1. 拆除复合木地板，35平方米\n"
        "2. 拆除木脚线，42米\n"
        "3. 拆砖墙（120厚砖墙），8平方米"
    )

    normalized = normalize_quote_request_text(text)

    assert "\n" not in normalized
    assert "1." not in normalized
    assert "2." not in normalized
    assert "3." not in normalized
    assert "拆除复合木地板，35平方米；拆除木脚线，42米；拆砖墙（120厚砖墙），8平方米" in normalized


def test_normalize_quote_request_text_flattens_plain_multiline_quote_items():
    text = "拆除复合木地板 20㎡\n拆除复合木地板 20㎡，拆除木脚线 30m\n窗帘盒/灯槽拆除 18m"

    normalized = normalize_quote_request_text(text)

    assert normalized == "拆除复合木地板 20㎡；拆除复合木地板 20㎡，拆除木脚线 30m；窗帘盒/灯槽拆除 18m"
    assert "\n" not in normalized


def test_normalize_quote_request_text_keeps_plain_text_unchanged():
    text = "请生成报价明细：拆除复合木地板35平方米；拆除木脚线42米"

    assert normalize_quote_request_text(text) == text


def test_normalize_quote_request_text_keeps_non_quote_multiline_text_unchanged():
    text = "请按现场照片预估\n垃圾清运是否包含由现场确认"

    assert normalize_quote_request_text(text) == text
