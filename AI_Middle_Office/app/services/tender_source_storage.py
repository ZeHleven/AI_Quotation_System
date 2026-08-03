from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.services import file_storage


class TenderSourceStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredTenderObject:
    bucket: str
    object_name: str
    size_bytes: int
    content_type: str


class TenderSourceStorage(Protocol):
    def store(
        self,
        *,
        content: bytes,
        original_filename: str,
        content_type: str | None,
        username: str,
    ) -> StoredTenderObject: ...

    def get(self, *, bucket: str, object_name: str) -> bytes: ...

    def delete(self, *, bucket: str, object_name: str) -> None: ...


class MinioTenderSourceStorage:
    """Thin adapter around the platform's existing MinIO file storage."""

    purpose = "bid_tender_source"

    def store(
        self,
        *,
        content: bytes,
        original_filename: str,
        content_type: str | None,
        username: str,
    ) -> StoredTenderObject:
        try:
            stored = file_storage.store_file_bytes(
                content=content,
                original_filename=original_filename,
                content_type=content_type,
                username=username,
                purpose=self.purpose,
            )
        except Exception as exc:
            raise TenderSourceStorageError(
                "original tender file could not be stored"
            ) from exc
        return StoredTenderObject(
            bucket=str(stored["bucket"]),
            object_name=str(stored["object_name"]),
            size_bytes=int(stored["size_bytes"]),
            content_type=str(stored["content_type"]),
        )

    def get(self, *, bucket: str, object_name: str) -> bytes:
        try:
            return file_storage.get_object_bytes(
                object_name=object_name,
                bucket=bucket,
            )
        except Exception as exc:
            raise TenderSourceStorageError(
                "original tender file could not be read"
            ) from exc

    def delete(self, *, bucket: str, object_name: str) -> None:
        try:
            file_storage.delete_object(
                object_name=object_name,
                bucket=bucket,
            )
        except Exception as exc:
            raise TenderSourceStorageError(
                "original tender file could not be deleted"
            ) from exc
