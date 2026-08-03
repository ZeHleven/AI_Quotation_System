from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.pricing_agent import (
    ARCHIVE_STATUS_DISABLED,
    ARCHIVE_STATUS_READY,
    PricingArchiveFile,
    PricingArchiveLine,
)
from app.models.user import User
from app.services.account_tenancy import resolve_current_account
from app.services.pricing_archive_parser import (
    PARSER_VERSION,
    ParsedArchiveWorkbook,
    PricingArchiveParseError,
    parse_priced_workbook,
)
from app.services.pricing_archive_storage import store_archive_bytes


class PricingArchiveError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _quota_bytes() -> int:
    return max(int(settings.pricing_agent_archive_account_quota_gb), 1) * 1024 * 1024 * 1024


def _max_upload_bytes() -> int:
    return max(int(settings.pricing_agent_archive_max_upload_mb), 1) * 1024 * 1024


def archive_storage_usage(db: Session, *, account_id: int) -> int:
    return int(
        db.query(func.coalesce(func.sum(PricingArchiveFile.size_bytes), 0))
        .filter(PricingArchiveFile.account_id == account_id)
        .scalar()
        or 0
    )


def _validate_upload(filename: str, content: bytes) -> None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise PricingArchiveError(
            "PRICING_ARCHIVE_FILE_TYPE_UNSUPPORTED",
            status_code=422,
            context={"supported_extensions": [".xlsx", ".xlsm"]},
        )
    if not content:
        raise PricingArchiveError("PRICING_ARCHIVE_FILE_EMPTY", status_code=422)
    if len(content) > _max_upload_bytes():
        raise PricingArchiveError(
            "PRICING_ARCHIVE_FILE_TOO_LARGE",
            status_code=413,
            context={"max_upload_mb": settings.pricing_agent_archive_max_upload_mb},
        )


def import_pricing_archive(
    db: Session,
    *,
    current_user: User,
    filename: str,
    content: bytes,
    content_type: str | None,
) -> tuple[PricingArchiveFile, bool]:
    account = resolve_current_account(db, current_user, for_update=True)
    _validate_upload(filename, content)
    file_sha256 = hashlib.sha256(content).hexdigest()
    existing = (
        db.query(PricingArchiveFile)
        .filter(
            PricingArchiveFile.account_id == account.id,
            PricingArchiveFile.file_sha256 == file_sha256,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing, True

    used_bytes = archive_storage_usage(db, account_id=int(account.id))
    if used_bytes + len(content) > _quota_bytes():
        raise PricingArchiveError(
            "PRICING_ARCHIVE_ACCOUNT_STORAGE_QUOTA_EXCEEDED",
            status_code=413,
            context={
                "used_bytes": used_bytes,
                "incoming_bytes": len(content),
                "quota_bytes": _quota_bytes(),
            },
        )
    try:
        parsed = parse_priced_workbook(content, filename)
    except PricingArchiveParseError as exc:
        raise PricingArchiveError(exc.code, status_code=422, context=exc.context) from exc
    if len(parsed.lines) > int(settings.pricing_agent_archive_max_indexed_rows):
        raise PricingArchiveError(
            "PRICING_ARCHIVE_INDEXED_ROW_LIMIT_EXCEEDED",
            status_code=422,
            context={"max_indexed_rows": settings.pricing_agent_archive_max_indexed_rows},
        )

    stored = store_archive_bytes(
        content=content,
        account_uuid=account.account_uuid,
        file_sha256=file_sha256,
        suffix=Path(filename).suffix,
        content_type=content_type,
    )
    archive = PricingArchiveFile(
        archive_uuid=str(uuid4()),
        account_id=account.id,
        original_filename=(filename or "archive.xlsx")[:255],
        content_type=(content_type or "application/octet-stream")[:128],
        file_sha256=file_sha256,
        size_bytes=len(content),
        storage_backend=stored["storage_backend"],
        storage_bucket=stored["storage_bucket"],
        storage_object_name=stored["storage_object_name"],
        parser_version=PARSER_VERSION,
        status=ARCHIVE_STATUS_READY,
        indexed_row_count=len(parsed.lines),
        rejected_row_count=int(parsed.summary.get("rejected_row_count") or 0),
        summary_json=_json_dump(parsed.summary),
        issues_json=_json_dump(list(parsed.issues)),
        created_by=current_user.id,
    )
    db.add(archive)
    db.flush()
    _persist_lines(db, archive=archive, account_id=int(account.id), parsed=parsed)
    db.flush()
    return archive, False


def _persist_lines(
    db: Session,
    *,
    archive: PricingArchiveFile,
    account_id: int,
    parsed: ParsedArchiveWorkbook,
) -> None:
    for item in parsed.lines:
        db.add(
            PricingArchiveLine(
                line_uuid=item.line_uuid,
                account_id=account_id,
                archive_file_id=archive.id,
                source_sheet=item.source_sheet,
                source_row_index=item.source_row_index,
                sort_order=item.sort_order,
                item_code=item.item_code,
                item_name=item.item_name,
                specification=item.specification,
                unit=item.unit,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item.total_price,
                normalized_code=item.normalized_code,
                normalized_name=item.normalized_name,
                normalized_spec=item.normalized_spec,
                normalized_unit=item.normalized_unit,
                searchable=True,
                price_derivation=item.price_derivation,
                fingerprint=item.fingerprint,
                raw_text=item.raw_text,
                raw_row_json=item.raw_row_json,
            )
        )


def list_pricing_archives(
    db: Session,
    *,
    current_user: User,
    include_disabled: bool = False,
) -> tuple[list[PricingArchiveFile], dict[str, int]]:
    account = resolve_current_account(db, current_user)
    query = db.query(PricingArchiveFile).filter(PricingArchiveFile.account_id == account.id)
    if not include_disabled:
        query = query.filter(PricingArchiveFile.status != ARCHIVE_STATUS_DISABLED)
    rows = query.order_by(PricingArchiveFile.created_at.desc(), PricingArchiveFile.id.desc()).all()
    used_bytes = archive_storage_usage(db, account_id=int(account.id))
    return rows, {"used_bytes": used_bytes, "quota_bytes": _quota_bytes()}


def get_pricing_archive(db: Session, *, current_user: User, archive_uuid: str) -> PricingArchiveFile:
    account = resolve_current_account(db, current_user)
    archive = (
        db.query(PricingArchiveFile)
        .filter(
            PricingArchiveFile.account_id == account.id,
            PricingArchiveFile.archive_uuid == archive_uuid,
        )
        .one_or_none()
    )
    if archive is None:
        raise PricingArchiveError("PRICING_ARCHIVE_NOT_FOUND", status_code=404)
    return archive


def disable_pricing_archive(db: Session, *, current_user: User, archive_uuid: str) -> PricingArchiveFile:
    archive = get_pricing_archive(db, current_user=current_user, archive_uuid=archive_uuid)
    archive.status = ARCHIVE_STATUS_DISABLED
    (
        db.query(PricingArchiveLine)
        .filter(PricingArchiveLine.archive_file_id == archive.id)
        .update({PricingArchiveLine.searchable: False}, synchronize_session=False)
    )
    db.flush()
    return archive


def serialize_pricing_archive(archive: PricingArchiveFile) -> dict[str, Any]:
    return {
        "archive_uuid": archive.archive_uuid,
        "original_filename": archive.original_filename,
        "content_type": archive.content_type,
        "file_sha256": archive.file_sha256,
        "size_bytes": int(archive.size_bytes or 0),
        "storage_backend": archive.storage_backend,
        "parser_version": archive.parser_version,
        "status": archive.status,
        "indexed_row_count": int(archive.indexed_row_count or 0),
        "rejected_row_count": int(archive.rejected_row_count or 0),
        "summary": _json_load(archive.summary_json, {}),
        "issues": _json_load(archive.issues_json, []),
        "created_at": archive.created_at.isoformat() if archive.created_at else None,
        "updated_at": archive.updated_at.isoformat() if archive.updated_at else None,
    }
