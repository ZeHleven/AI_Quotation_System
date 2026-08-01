"""Storage adapter for pricing-agent archive source files.

Local development uses an explicit disk directory.  Production can switch to
MinIO without changing database records or the archive parser contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR, settings
from app.services.file_storage import get_object_bytes, put_object_bytes


class PricingArchiveStorageError(RuntimeError):
    pass


def selected_backend() -> str:
    configured = (settings.pricing_agent_archive_storage_backend or "auto").strip().lower()
    if configured == "auto":
        return "minio" if settings.minio_enabled else "local"
    if configured not in {"local", "minio"}:
        raise PricingArchiveStorageError("PRICING_ARCHIVE_STORAGE_BACKEND_INVALID")
    if configured == "minio" and not settings.minio_enabled:
        raise PricingArchiveStorageError("PRICING_ARCHIVE_MINIO_DISABLED")
    return configured


def local_root() -> Path:
    configured = Path(settings.pricing_agent_archive_local_root).expanduser()
    root = configured if configured.is_absolute() else BASE_DIR / configured
    return root.resolve()


def _relative_object_name(account_uuid: str, file_sha256: str, suffix: str) -> str:
    safe_account = "".join(char for char in account_uuid if char.isalnum() or char in {"-", "_"})[:64]
    safe_suffix = suffix.lower() if suffix.lower() in {".xlsx", ".xlsm"} else ".xlsx"
    return f"pricing-agent-archives/{safe_account}/{file_sha256[:2]}/{file_sha256}{safe_suffix}"


def _resolved_local_path(object_name: str) -> Path:
    root = local_root()
    target = (root / Path(object_name)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PricingArchiveStorageError("PRICING_ARCHIVE_STORAGE_PATH_INVALID") from exc
    return target


def store_archive_bytes(
    *,
    content: bytes,
    account_uuid: str,
    file_sha256: str,
    suffix: str,
    content_type: str | None,
) -> dict[str, Any]:
    backend = selected_backend()
    object_name = _relative_object_name(account_uuid, file_sha256, suffix)
    if backend == "minio":
        try:
            stored = put_object_bytes(
                content=content,
                object_name=object_name,
                content_type=content_type,
            )
        except Exception as exc:
            raise PricingArchiveStorageError("PRICING_ARCHIVE_MINIO_WRITE_FAILED") from exc
        return {
            "storage_backend": "minio",
            "storage_bucket": stored["bucket"],
            "storage_object_name": stored["object_name"],
        }

    target = _resolved_local_path(object_name)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
    except OSError as exc:
        raise PricingArchiveStorageError("PRICING_ARCHIVE_LOCAL_WRITE_FAILED") from exc
    return {
        "storage_backend": "local",
        "storage_bucket": None,
        "storage_object_name": object_name,
    }


def read_archive_bytes(*, storage_backend: str, storage_bucket: str | None, storage_object_name: str) -> bytes:
    if storage_backend == "minio":
        try:
            return get_object_bytes(storage_object_name, bucket=storage_bucket)
        except Exception as exc:
            raise PricingArchiveStorageError("PRICING_ARCHIVE_MINIO_READ_FAILED") from exc
    if storage_backend != "local":
        raise PricingArchiveStorageError("PRICING_ARCHIVE_STORAGE_BACKEND_INVALID")
    target = _resolved_local_path(storage_object_name)
    if not target.is_file():
        raise PricingArchiveStorageError("PRICING_ARCHIVE_FILE_NOT_FOUND")
    try:
        return target.read_bytes()
    except OSError as exc:
        raise PricingArchiveStorageError("PRICING_ARCHIVE_LOCAL_READ_FAILED") from exc
