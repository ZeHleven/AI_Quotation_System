from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.quote_job import QuoteJob
from app.models.quote_preview_draft import (
    PREVIEW_DRAFT_STATUS_DISCARDED,
    PREVIEW_DRAFT_STATUS_EDITING,
    PREVIEW_DRAFT_STATUS_PUSHED,
    QuotePreviewDraft,
)
from app.models.user import User
from app.services.quote_history import parse_amount, project_details
from app.services.rbac import has_admin_role


CN_TZ = ZoneInfo("Asia/Shanghai")


def _now_local_naive() -> datetime:
    return datetime.now(CN_TZ).replace(tzinfo=None)


def _display_local_time(value: datetime | None, *, baseline: datetime | None = None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is not None:
        return value.astimezone(CN_TZ).replace(tzinfo=None)
    if baseline and baseline.tzinfo is None and value < baseline:
        delta = baseline - value
        if timedelta(hours=6) <= delta <= timedelta(hours=10):
            return value + timedelta(hours=8)
    return value


def _format_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat(timespec="seconds")


def _json_loads(raw_value: str | None, fallback: Any) -> Any:
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except Exception:
        return fallback


def _positive_amount(value: Any) -> float | None:
    amount = parse_amount(value)
    if amount is None or amount <= 0:
        return None
    return float(amount)


def _draft_counts(draft: dict[str, Any]) -> tuple[int, int, int]:
    rows = project_details(draft)
    row_count = len(rows)
    priced_count = 0
    for row in rows:
        unit_price = _positive_amount(
            row.get("manual_unit_price")
            or row.get("confirmed_unit_price")
            or row.get("final_unit_price")
            or row.get("unit_price")
            or row.get("price")
        )
        total_price = _positive_amount(
            row.get("confirmed_total_price")
            or row.get("final_total_price")
            or row.get("total_price")
            or row.get("amount")
            or row.get("subtotal")
        )
        if unit_price is not None and total_price is not None:
            priced_count += 1
    return row_count, priced_count, max(0, row_count - priced_count)


def _serialize_preview_draft(row: QuotePreviewDraft | None) -> dict[str, Any]:
    if not row:
        return {
            "exists": False,
            "status": None,
            "draft": None,
        }
    return {
        "exists": True,
        "id": row.id,
        "quote_job_id": row.quote_job_id,
        "quote_id": row.quote_id,
        "trace_id": row.trace_id,
        "username": row.username,
        "status": row.status,
        "draft": _json_loads(row.draft_json, {}),
        "row_count": row.row_count,
        "priced_row_count": row.priced_row_count,
        "unpriced_row_count": row.unpriced_row_count,
        "version": row.version,
        "created_at": _format_dt(_display_local_time(row.created_at)),
        "updated_at": _format_dt(_display_local_time(row.updated_at, baseline=row.created_at)),
        "pushed_at": _format_dt(_display_local_time(row.pushed_at, baseline=row.created_at)),
        "discarded_at": _format_dt(_display_local_time(row.discarded_at, baseline=row.created_at)),
    }


def get_preview_draft(db: Session, quote_job_id: str) -> dict[str, Any]:
    row = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.quote_job_id == quote_job_id).first()
    return _serialize_preview_draft(row)


def save_preview_draft(
    db: Session,
    *,
    job: QuoteJob,
    user: User,
    draft: dict[str, Any],
    quote_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="DRAFT_REQUIRED")
    row_count, priced_count, unpriced_count = _draft_counts(draft)
    raw_json = json.dumps(draft, ensure_ascii=False)
    existing = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.quote_job_id == job.job_id).first()
    now = _now_local_naive()
    if existing and existing.status == PREVIEW_DRAFT_STATUS_PUSHED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PREVIEW_DRAFT_PUSHED")
    if existing:
        existing.quote_id = quote_id or existing.quote_id
        existing.trace_id = trace_id or existing.trace_id or job.trace_id
        existing.user_id = user.id
        existing.username = user.username
        existing.status = PREVIEW_DRAFT_STATUS_EDITING
        existing.draft_json = raw_json
        existing.row_count = row_count
        existing.priced_row_count = priced_count
        existing.unpriced_row_count = unpriced_count
        existing.version = int(existing.version or 0) + 1
        existing.discarded_at = None
        existing.updated_at = now
        row = existing
    else:
        row = QuotePreviewDraft(
            quote_job_id=job.job_id,
            quote_id=quote_id,
            trace_id=trace_id or job.trace_id,
            user_id=user.id,
            username=user.username,
            status=PREVIEW_DRAFT_STATUS_EDITING,
            draft_json=raw_json,
            row_count=row_count,
            priced_row_count=priced_count,
            unpriced_row_count=unpriced_count,
            version=1,
        )
        db.add(row)
    db.flush()
    return _serialize_preview_draft(row)


def discard_preview_draft(db: Session, *, quote_job_id: str) -> dict[str, Any]:
    row = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.quote_job_id == quote_job_id).first()
    if not row:
        return _serialize_preview_draft(None)
    row.status = PREVIEW_DRAFT_STATUS_DISCARDED
    row.discarded_at = _now_local_naive()
    row.version = int(row.version or 0) + 1
    db.flush()
    return _serialize_preview_draft(row)


def mark_preview_draft_pushed(db: Session, *, quote_job_id: str | None) -> dict[str, Any] | None:
    if not quote_job_id:
        return None
    row = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.quote_job_id == quote_job_id).first()
    if not row:
        return None
    row.status = PREVIEW_DRAFT_STATUS_PUSHED
    row.pushed_at = _now_local_naive()
    row.version = int(row.version or 0) + 1
    db.flush()
    return _serialize_preview_draft(row)


def delete_preview_drafts(
    db: Session,
    *,
    draft_ids: list[int],
    user: User,
) -> dict[str, Any]:
    unique_ids: list[int] = []
    for draft_id in draft_ids:
        if draft_id and draft_id > 0 and draft_id not in unique_ids:
            unique_ids.append(draft_id)
    if not unique_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="DRAFT_IDS_REQUIRED")

    rows = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.id.in_(unique_ids)).all()
    rows_by_id = {row.id: row for row in rows}
    is_admin = has_admin_role(user)
    deleted_ids: list[int] = []
    skipped: list[dict[str, Any]] = []

    for draft_id in unique_ids:
        row = rows_by_id.get(draft_id)
        if not row:
            skipped.append({"draft_id": draft_id, "reason": "not_found"})
            continue
        if not is_admin and row.username != user.username:
            skipped.append({"draft_id": draft_id, "reason": "permission_denied"})
            continue
        if row.status != PREVIEW_DRAFT_STATUS_EDITING:
            skipped.append({"draft_id": draft_id, "reason": "not_editing"})
            continue
        db.delete(row)
        deleted_ids.append(draft_id)

    db.flush()
    return {
        "requested_count": len(unique_ids),
        "deleted_count": len(deleted_ids),
        "skipped_count": len(skipped),
        "deleted_ids": deleted_ids,
        "skipped": skipped,
    }
