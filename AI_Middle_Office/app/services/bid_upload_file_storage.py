"""Bounded MinIO streaming and exact-key compensation for API-12."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Protocol

from app.core.config import settings
from app.services.file_storage import ensure_bucket, get_storage_client


class BidUploadStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredBidUploadObject:
    object_key: str
    size_bytes: int
    mime_type: str
    storage_etag: str | None


@dataclass(frozen=True)
class BidUploadObjectCandidate:
    object_key: str
    last_modified: datetime


class BidUploadObjectStorage(Protocol):
    def put(
        self,
        *,
        stream: BinaryIO,
        object_key: str,
        size_bytes: int,
        mime_type: str,
    ) -> StoredBidUploadObject: ...

    def delete(self, *, object_key: str) -> None: ...

    def list_candidates(
        self,
        *,
        prefix: str,
        limit: int,
    ) -> list[BidUploadObjectCandidate]: ...


def normalized_upload_object_prefix() -> str:
    prefix = str(settings.bid_upload_object_prefix).strip().strip("/")
    segments = prefix.split("/") if prefix else []
    if (
        not segments
        or any(
            segment in {"", ".", ".."}
            or not segment.replace("-", "").replace("_", "").isalnum()
            for segment in segments
        )
    ):
        raise BidUploadStorageError("BID_UPLOAD_OBJECT_PREFIX_INVALID")
    return "/".join(segments)


def build_temporary_object_key(
    *,
    batch_id: str,
    batch_file_id: str,
    now: datetime | None = None,
) -> str:
    """Build a server-only key; no user-controlled filename enters this path."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return (
        f"{normalized_upload_object_prefix()}/"
        f"{current:%Y/%m/%d}/{batch_id}/{batch_file_id}"
    )


class MinioBidUploadObjectStorage:
    def put(
        self,
        *,
        stream: BinaryIO,
        object_key: str,
        size_bytes: int,
        mime_type: str,
    ) -> StoredBidUploadObject:
        if int(size_bytes) < 1:
            raise BidUploadStorageError("BID_UPLOAD_OBJECT_EMPTY")
        client = get_storage_client()
        bucket = ensure_bucket(client)
        try:
            result = client.put_object(
                bucket,
                object_key,
                data=stream,
                length=int(size_bytes),
                content_type=mime_type,
                part_size=max(
                    5 * 1024 * 1024,
                    min(
                        int(settings.bid_upload_minio_part_size_bytes),
                        64 * 1024 * 1024,
                    ),
                ),
            )
        except Exception as exc:
            raise BidUploadStorageError("BID_UPLOAD_OBJECT_PUT_FAILED") from exc
        etag = str(getattr(result, "etag", "") or "").strip().strip('"') or None
        return StoredBidUploadObject(
            object_key=object_key,
            size_bytes=int(size_bytes),
            mime_type=mime_type,
            storage_etag=etag,
        )

    def delete(self, *, object_key: str) -> None:
        client = get_storage_client()
        try:
            client.remove_object(settings.minio_bucket, object_key)
        except Exception as exc:
            raise BidUploadStorageError("BID_UPLOAD_OBJECT_DELETE_FAILED") from exc

    def list_candidates(
        self,
        *,
        prefix: str,
        limit: int,
    ) -> list[BidUploadObjectCandidate]:
        client = get_storage_client()
        rows: list[BidUploadObjectCandidate] = []
        try:
            for item in client.list_objects(
                settings.minio_bucket,
                prefix=prefix.rstrip("/") + "/",
                recursive=True,
            ):
                modified = item.last_modified
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=timezone.utc)
                rows.append(
                    BidUploadObjectCandidate(
                        object_key=str(item.object_name),
                        last_modified=modified.astimezone(timezone.utc),
                    )
                )
                if len(rows) >= max(1, int(limit)):
                    break
        except Exception as exc:
            raise BidUploadStorageError("BID_UPLOAD_OBJECT_LIST_FAILED") from exc
        return rows


def get_bid_upload_object_storage() -> BidUploadObjectStorage:
    return MinioBidUploadObjectStorage()
