"""Public, storage-safe projections of authoritative document parse heads."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
)


_WARNING_CODE = re.compile(r"^[A-Z0-9_]{1,80}$")


def _utc_rfc3339(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_warnings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    warnings: list[dict[str, Any]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "PARSE_WARNING").upper()[:80]
        if not _WARNING_CODE.fullmatch(code):
            code = "PARSE_WARNING"
        message = str(item.get("message") or "解析结果需要复核")[:500]
        details = item.get("details")
        warnings.append(
            {
                "code": code,
                "message": message,
                "details": dict(details) if isinstance(details, dict) else {},
            }
        )
    return warnings


def _low_quality_locations(warnings: list[dict[str, Any]]) -> list[str]:
    locations: list[str] = []
    for warning in warnings:
        details = warning["details"]
        if details.get("page_no") is not None:
            candidate = f"page:{details['page_no']}"
        elif details.get("sheet_name") is not None:
            candidate = f"sheet:{details['sheet_name']}"
        else:
            continue
        if candidate not in locations:
            locations.append(candidate[:200])
    return locations


def _not_requested() -> dict[str, Any]:
    return {
        "status": "not_requested",
        "latest_run_id": None,
        "requested_at": None,
        "started_at": None,
        "finished_at": None,
        "quality": None,
        "warnings": [],
    }


def project_document_parse_run(run: BidDocumentParseRun) -> dict[str, Any]:
    status = str(run.status)
    warnings = _safe_warnings(run.warnings_json)
    quality = None
    if status in {"succeeded", "partial"}:
        quality = {
            "grade": str(run.quality_grade),
            "score": int(run.quality_score) if run.quality_score is not None else None,
            "page_count": int(run.page_count),
            "sheet_count": int(run.sheet_count),
            "ocr_status": str(run.ocr_status),
            "low_quality_locations": _low_quality_locations(warnings),
        }
    return {
        "status": status,
        "latest_run_id": str(run.id),
        "requested_at": _utc_rfc3339(run.requested_at),
        "started_at": _utc_rfc3339(run.started_at),
        "finished_at": _utc_rfc3339(run.finished_at),
        "quality": quality,
        "warnings": warnings,
    }


def build_document_parse_summaries(
    db: Session,
    document_version_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    version_ids = list(dict.fromkeys(str(value) for value in document_version_ids))
    summaries = {version_id: _not_requested() for version_id in version_ids}
    if not version_ids:
        return summaries
    rows = (
        db.query(BidDocumentParseHead, BidDocumentParseRun)
        .join(
            BidDocumentParseRun,
            BidDocumentParseRun.id == BidDocumentParseHead.current_run_id,
        )
        .filter(BidDocumentParseHead.document_version_id.in_(version_ids))
        .all()
    )
    for head, run in rows:
        summaries[str(head.document_version_id)] = project_document_parse_run(run)
    return summaries


def build_document_parse_summary(
    db: Session,
    document_version_id: str,
) -> dict[str, Any]:
    return build_document_parse_summaries(db, [document_version_id])[
        str(document_version_id)
    ]

