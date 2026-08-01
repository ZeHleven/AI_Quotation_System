import re
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional

from app.core.config import settings


class StorageError(RuntimeError):
    pass


class StorageDisabledError(StorageError):
    pass


def _load_minio_client_class():
    try:
        from minio import Minio
        from minio.error import S3Error
    except ImportError as exc:
        raise StorageError("MinIO SDK is not installed. Run: pip install minio") from exc
    return Minio, S3Error


def _normalize_segment(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-")
    return value[:120] or fallback


def sanitize_filename(filename: str) -> str:
    return _normalize_segment(filename, "file")


def sanitize_purpose(purpose: str) -> str:
    return _normalize_segment(purpose, "general")


def get_storage_client():
    if not settings.minio_enabled:
        raise StorageDisabledError("MinIO storage is disabled")
    Minio, _ = _load_minio_client_class()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def ensure_bucket(client=None) -> str:
    client = client or get_storage_client()
    bucket = settings.minio_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    return bucket


def build_object_name(username: str, purpose: str, filename: str) -> str:
    safe_user = _normalize_segment(username, "user")
    safe_purpose = sanitize_purpose(purpose)
    safe_filename = sanitize_filename(filename)
    date_prefix = datetime.now().strftime("%Y/%m/%d")
    return f"{safe_purpose}/{safe_user}/{date_prefix}/{uuid.uuid4().hex}_{safe_filename}"


def store_file_bytes(
    *,
    content: bytes,
    original_filename: str,
    content_type: Optional[str],
    username: str,
    purpose: str = "general",
) -> dict:
    client = get_storage_client()
    bucket = ensure_bucket(client)
    object_name = build_object_name(username, purpose, original_filename)
    client.put_object(
        bucket,
        object_name,
        data=BytesIO(content),
        length=len(content),
        content_type=content_type or "application/octet-stream",
    )
    return {
        "bucket": bucket,
        "object_name": object_name,
        "size_bytes": len(content),
        "content_type": content_type or "application/octet-stream",
    }


def put_object_bytes(
    *,
    content: bytes,
    object_name: str,
    content_type: Optional[str] = None,
    bucket: Optional[str] = None,
) -> dict:
    """Store bytes at one exact, caller-owned immutable object key."""

    normalized_object_name = (object_name or "").strip().lstrip("/")
    if not normalized_object_name or ".." in normalized_object_name.split("/"):
        raise StorageError("object_name is invalid")
    client = get_storage_client()
    resolved_bucket = bucket or ensure_bucket(client)
    client.put_object(
        resolved_bucket,
        normalized_object_name,
        data=BytesIO(content),
        length=len(content),
        content_type=content_type or "application/octet-stream",
    )
    return {
        "bucket": resolved_bucket,
        "object_name": normalized_object_name,
        "size_bytes": len(content),
        "content_type": content_type or "application/octet-stream",
    }


def get_object_bytes(object_name: str, bucket: Optional[str] = None) -> bytes:
    client = get_storage_client()
    response = client.get_object(bucket or settings.minio_bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_object(object_name: str, bucket: Optional[str] = None) -> None:
    """Delete one exact object, used only to compensate a failed DB write."""

    client = get_storage_client()
    client.remove_object(bucket or settings.minio_bucket, object_name)


def generate_presigned_get_url(object_name: str, expires_seconds: Optional[int] = None, bucket: Optional[str] = None) -> str:
    client = get_storage_client()
    expires_seconds = expires_seconds or settings.minio_presigned_expire_seconds
    expires_seconds = max(60, min(int(expires_seconds), 60 * 60 * 24 * 7))
    return client.presigned_get_object(
        bucket or settings.minio_bucket,
        object_name,
        expires=timedelta(seconds=expires_seconds),
    )


def check_storage_health() -> dict:
    if not settings.minio_enabled:
        return {"ok": False, "enabled": False, "status": "disabled"}
    try:
        client = get_storage_client()
        bucket = ensure_bucket(client)
        return {
            "ok": True,
            "enabled": True,
            "status": "ready",
            "endpoint": settings.minio_endpoint,
            "bucket": bucket,
            "secure": settings.minio_secure,
        }
    except StorageDisabledError as exc:
        return {"ok": False, "enabled": False, "status": "disabled", "detail": str(exc)}
    except Exception as exc:
        return {
            "ok": False,
            "enabled": True,
            "status": "error",
            "endpoint": settings.minio_endpoint,
            "bucket": settings.minio_bucket,
            "detail": str(exc),
        }
