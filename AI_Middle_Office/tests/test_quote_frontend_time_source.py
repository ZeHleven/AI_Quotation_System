from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parents[2] / "index.html"


def test_quote_submission_preserves_default_and_manual_time_sources():
    html = FRONTEND_PATH.read_text(encoding="utf-8")

    assert "const clientTimeManual = ref(false);" in html
    assert '@change="markClientTimeManual"' in html
    assert "clientTimeManual.value = !!clientForm.value.inquiryTime;" in html
    assert (
        "formData.append('time_source', clientTimeManual.value ? 'manual' : 'default');"
        in html
    )
    assert "formData.append('time_source', 'manual');" not in html
