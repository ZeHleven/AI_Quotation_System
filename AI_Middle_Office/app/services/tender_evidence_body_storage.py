from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from app.models.tender_evidence import BidEvidenceBlock, BidEvidenceDocument
from app.services import file_storage


BODY_SCHEMA_VERSION = "tender-evidence-body-v1"
BODY_STORAGE_BACKEND_MINIO = "minio"
BODY_STORAGE_BACKEND_MYSQL = "mysql_legacy"


class TenderEvidenceBodyError(RuntimeError):
    pass


class TenderEvidenceBodyIntegrityError(TenderEvidenceBodyError):
    pass


@dataclass(frozen=True)
class StoredEvidenceBody:
    backend: str
    bucket: str
    object_name: str
    sha256: str
    size_bytes: int
    schema_version: str


class TenderEvidenceBodyStorage(Protocol):
    def put(
        self,
        *,
        case_id: str,
        document_id: str,
        content: bytes,
        sha256: str,
    ) -> StoredEvidenceBody: ...

    def get(self, *, bucket: str, object_name: str) -> bytes: ...


class MinioTenderEvidenceBodyStorage:
    """Content-addressed storage for immutable parsed evidence packages."""

    purpose = "bid_tender_evidence_body"

    def put(
        self,
        *,
        case_id: str,
        document_id: str,
        content: bytes,
        sha256: str,
    ) -> StoredEvidenceBody:
        normalized_case_id = _safe_key_segment(case_id)
        normalized_document_id = _safe_key_segment(document_id)
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise TenderEvidenceBodyError("evidence body SHA-256 is invalid")
        object_name = (
            f"{self.purpose}/{normalized_case_id}/{normalized_document_id}/"
            f"{sha256}.json"
        )
        try:
            stored = file_storage.put_object_bytes(
                content=content,
                object_name=object_name,
                content_type="application/json; charset=utf-8",
            )
        except Exception as exc:
            raise TenderEvidenceBodyError(
                "parsed tender evidence body could not be stored"
            ) from exc
        return StoredEvidenceBody(
            backend=BODY_STORAGE_BACKEND_MINIO,
            bucket=str(stored["bucket"]),
            object_name=str(stored["object_name"]),
            sha256=sha256,
            size_bytes=int(stored["size_bytes"]),
            schema_version=BODY_SCHEMA_VERSION,
        )

    def get(self, *, bucket: str, object_name: str) -> bytes:
        try:
            return file_storage.get_object_bytes(
                object_name=object_name,
                bucket=bucket,
            )
        except Exception as exc:
            raise TenderEvidenceBodyError(
                "parsed tender evidence body could not be read"
            ) from exc


def build_evidence_body_package(
    *,
    case_id: str,
    document_id: str,
    document_key: str,
    document_version: int,
    source_file_uuid: str,
    source_sha256: str,
    parser_version: str,
    extracted_text: str,
    source_segments: Sequence[dict[str, Any]],
    blocks: Sequence[dict[str, Any]],
) -> tuple[bytes, str]:
    payload = {
        "schema_version": BODY_SCHEMA_VERSION,
        "case_id": case_id,
        "document_id": document_id,
        "document_key": document_key,
        "document_version": int(document_version),
        "source_file_uuid": source_file_uuid,
        "source_sha256": source_sha256,
        "parser_version": parser_version,
        "extracted_text": extracted_text,
        "source_segments": list(source_segments),
        "blocks": list(blocks),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded, hashlib.sha256(encoded).hexdigest()


class TenderEvidenceBodyReader:
    """Verified body loader with a small per-process document cache."""

    def __init__(
        self,
        storage: TenderEvidenceBodyStorage | None = None,
        *,
        max_cached_documents: int = 32,
    ):
        self._storage = storage or MinioTenderEvidenceBodyStorage()
        self._max_cached_documents = max(1, min(int(max_cached_documents), 256))
        self._cache: OrderedDict[
            tuple[str, str, str],
            dict[str, Any],
        ] = OrderedDict()
        self._lock = threading.RLock()

    def read(
        self,
        *,
        document: BidEvidenceDocument,
        block: BidEvidenceBlock,
    ) -> str:
        legacy_content = block.content
        if document.body_storage_backend != BODY_STORAGE_BACKEND_MINIO:
            if legacy_content is None:
                raise TenderEvidenceBodyIntegrityError(
                    "legacy evidence block has no MySQL content"
                )
            return self._verify_content(block, legacy_content)

        package = self._load_package(document)
        return self._read_from_package(
            document=document,
            block=block,
            package=package,
        )

    def read_many(
        self,
        rows: Sequence[tuple[BidEvidenceBlock, BidEvidenceDocument]],
    ) -> dict[str, str]:
        grouped: dict[
            int,
            tuple[
                BidEvidenceDocument,
                list[BidEvidenceBlock],
            ],
        ] = {}
        for block, document in rows:
            entry = grouped.setdefault(
                int(document.id),
                (document, []),
            )
            entry[1].append(block)

        resolved: dict[str, str] = {}
        for document, blocks in grouped.values():
            if document.body_storage_backend != BODY_STORAGE_BACKEND_MINIO:
                for block in blocks:
                    if block.content is None:
                        raise TenderEvidenceBodyIntegrityError(
                            "legacy evidence block has no MySQL content"
                        )
                    resolved[block.evidence_id] = self._verify_content(
                        block,
                        block.content,
                    )
                continue
            package = self._load_package(document)
            raw_blocks = package.get("blocks")
            if not isinstance(raw_blocks, list):
                raise TenderEvidenceBodyIntegrityError(
                    "evidence body package has no blocks list"
                )
            by_evidence_id = {
                str(item.get("evidence_id")): item
                for item in raw_blocks
                if isinstance(item, dict)
                and item.get("evidence_id")
            }
            for block in blocks:
                selected = by_evidence_id.get(block.evidence_id)
                resolved[block.evidence_id] = (
                    self._validate_package_block(
                        block=block,
                        selected=selected,
                    )
                )
        return resolved

    def _read_from_package(
        self,
        *,
        document: BidEvidenceDocument,
        block: BidEvidenceBlock,
        package: dict[str, Any],
    ) -> str:
        del document
        blocks = package.get("blocks")
        if not isinstance(blocks, list):
            raise TenderEvidenceBodyIntegrityError(
                "evidence body package has no blocks list"
            )
        selected = next(
            (
                item
                for item in blocks
                if isinstance(item, dict)
                and item.get("evidence_id") == block.evidence_id
            ),
            None,
        )
        return self._validate_package_block(
            block=block,
            selected=selected,
        )

    def _validate_package_block(
        self,
        *,
        block: BidEvidenceBlock,
        selected: Any,
    ) -> str:
        if not isinstance(selected, dict):
            raise TenderEvidenceBodyIntegrityError(
                "evidence block is absent from its body package"
            )
        if selected.get("block_id") != block.block_id:
            raise TenderEvidenceBodyIntegrityError(
                "evidence body block identity does not match MySQL metadata"
            )
        if (
            selected.get("block_order") != block.block_order
            or selected.get("content_hash") != block.content_hash
        ):
            raise TenderEvidenceBodyIntegrityError(
                "evidence body block metadata does not match MySQL"
            )
        content = selected.get("content")
        if not isinstance(content, str):
            raise TenderEvidenceBodyIntegrityError(
                "evidence body block content is invalid"
            )
        return self._verify_content(block, content)

    def _load_package(
        self,
        document: BidEvidenceDocument,
    ) -> dict[str, Any]:
        bucket = (document.body_bucket or "").strip()
        object_name = (document.body_object_name or "").strip()
        expected_sha = (document.body_sha256 or "").strip().lower()
        if (
            not bucket
            or not object_name
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
        ):
            raise TenderEvidenceBodyIntegrityError(
                "evidence document has incomplete body storage metadata"
            )
        cache_key = (bucket, object_name, expected_sha)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached

        raw = self._storage.get(bucket=bucket, object_name=object_name)
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != expected_sha:
            raise TenderEvidenceBodyIntegrityError(
                "evidence body package failed SHA-256 verification"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise TenderEvidenceBodyIntegrityError(
                "evidence body package is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise TenderEvidenceBodyIntegrityError(
                "evidence body package must be a JSON object"
            )
        if payload.get("schema_version") != BODY_SCHEMA_VERSION:
            raise TenderEvidenceBodyIntegrityError(
                "evidence body package schema is unsupported"
            )
        if document.body_schema_version != BODY_SCHEMA_VERSION:
            raise TenderEvidenceBodyIntegrityError(
                "evidence document body schema metadata is unsupported"
            )
        if payload.get("document_id") != document.evidence_document_uuid:
            raise TenderEvidenceBodyIntegrityError(
                "evidence body package document identity is invalid"
            )
        if (
            payload.get("document_key") != document.document_key
            or payload.get("document_version") != document.version_no
            or payload.get("source_sha256") != document.sha256
        ):
            raise TenderEvidenceBodyIntegrityError(
                "evidence body package metadata does not match MySQL"
            )
        with self._lock:
            self._cache[cache_key] = payload
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._max_cached_documents:
                self._cache.popitem(last=False)
        return payload

    @staticmethod
    def _verify_content(block: BidEvidenceBlock, content: str) -> str:
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_hash != block.content_hash:
            raise TenderEvidenceBodyIntegrityError(
                "evidence block content failed SHA-256 verification"
            )
        return content


def _safe_key_segment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-")
    if not normalized:
        raise TenderEvidenceBodyError("evidence body object key is invalid")
    return normalized[:160]
