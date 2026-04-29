import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.file_object import FileObject
from app.models.quote_job import QuoteJob


def _login_headers(client):
    username = f"file_user_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_file_upload_records_metadata_and_returns_presigned_url(client, monkeypatch):
    from app.api.v1 import files as files_api

    def fake_store_file_bytes(*, content, original_filename, content_type, username, purpose):
        return {
            "bucket": "quote-files",
            "object_name": f"{purpose}/{username}/fake/{original_filename}",
            "size_bytes": len(content),
            "content_type": content_type,
        }

    def fake_presigned_get_url(object_name, expires_seconds=None, bucket=None):
        return f"http://minio.local/{object_name}?expires={expires_seconds or 3600}"

    monkeypatch.setattr(files_api, "store_file_bytes", fake_store_file_bytes)
    monkeypatch.setattr(files_api, "generate_presigned_get_url", fake_presigned_get_url)

    headers = _login_headers(client)
    response = client.post(
        "/api/v1/files",
        headers=headers,
        data={"purpose": "quote_attachment"},
        files={"file": ("quote.xlsx", b"fake-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["file_id"]
    assert data["purpose"] == "quote_attachment"
    assert data["original_filename"] == "quote.xlsx"
    assert data["size_bytes"] == len(b"fake-excel")
    assert data["download_url"].startswith("http://minio.local/")

    list_response = client.get("/api/v1/files", headers=headers)
    assert list_response.status_code == 200
    assert any(item["file_id"] == data["file_id"] for item in list_response.json()["data"])

    url_response = client.get(f"/api/v1/files/{data['file_id']}/download_url", headers=headers)
    assert url_response.status_code == 200
    assert url_response.json()["data"]["download_url"].startswith("http://minio.local/")


def test_quote_job_upload_uses_minio_when_enabled(client, monkeypatch):
    from app.api.v1 import quote_jobs as quote_jobs_api

    old_enabled = settings.minio_enabled
    object.__setattr__(settings, "minio_enabled", True)

    def fake_store_file_bytes(*, content, original_filename, content_type, username, purpose):
        return {
            "bucket": "quote-files",
            "object_name": f"{purpose}/{username}/fake/{original_filename}",
            "size_bytes": len(content),
            "content_type": content_type,
        }

    monkeypatch.setattr(quote_jobs_api, "store_file_bytes", fake_store_file_bytes)
    headers = _login_headers(client)

    try:
        response = client.post(
            "/api/v1/quote/jobs",
            headers=headers,
            data={"message": "厨房墙砖 10 平米"},
            files={"file": ("drawing.png", b"fake-image", "image/png")},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["file_object_id"]

        db = SessionLocal()
        try:
            job = db.query(QuoteJob).filter(QuoteJob.job_id == body["job_id"]).first()
            file_obj = db.query(FileObject).filter(FileObject.file_id == body["file_object_id"]).first()
            assert job.file_object_id == body["file_object_id"]
            assert job.file_base64 is None
            assert file_obj.original_filename == "drawing.png"
            assert file_obj.purpose == "quote_job_attachment"
        finally:
            db.close()
    finally:
        object.__setattr__(settings, "minio_enabled", old_enabled)
