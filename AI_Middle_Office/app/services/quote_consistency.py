from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.quote_job import QuoteJob, QuotePushAttempt, QuoteQuotaReservation
from app.models.user import User


QUOTA_RESERVED = "reserved"
QUOTA_CONSUMED = "consumed"
QUOTA_RELEASED = "released"

PUSH_SENDING = "sending"
PUSH_N8N_CLAIMED = "n8n_claimed"
PUSH_N8N_DISPATCHING = "n8n_dispatching"
PUSH_DELIVERY_UNKNOWN = "delivery_unknown"
PUSH_EXTERNAL_DELIVERED = "external_delivered"
PUSH_DELIVERED = "delivered"
PUSH_FAILED = "failed"


class QuoteQuotaUnavailable(RuntimeError):
    pass


class QuoteConsistencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PushAttemptStart:
    attempt: QuotePushAttempt
    action: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def claim_quote_job(db: Session, *, job_id: str, attempt_id: str) -> bool:
    updated = (
        db.query(QuoteJob)
        .filter(QuoteJob.job_id == job_id, QuoteJob.status == "queued")
        .update(
            {
                QuoteJob.status: "running",
                QuoteJob.stage: "started",
                QuoteJob.attempt_id: attempt_id,
                QuoteJob.started_at: utcnow(),
                QuoteJob.updated_at: utcnow(),
            },
            synchronize_session=False,
        )
    )
    return updated == 1


def quote_quota_reservation(db: Session, quote_job_id: str, *, lock: bool = False) -> QuoteQuotaReservation | None:
    query = db.query(QuoteQuotaReservation).filter(QuoteQuotaReservation.quote_job_id == quote_job_id)
    if lock:
        query = query.with_for_update()
    return query.first()


def reserve_quote_quota(
    db: Session,
    *,
    user_id: int,
    quote_job_id: str,
    amount: int = 1,
) -> QuoteQuotaReservation:
    existing = quote_quota_reservation(db, quote_job_id, lock=True)
    if existing:
        if existing.user_id != user_id or existing.amount != amount:
            raise QuoteConsistencyError("QUOTE_QUOTA_RESERVATION_MISMATCH")
        if existing.status == QUOTA_RESERVED:
            return existing
        raise QuoteConsistencyError(f"QUOTE_QUOTA_ALREADY_{existing.status.upper()}")

    updated = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.is_active.is_(True),
            (User.quota - User.quota_reserved) >= amount,
        )
        .update(
            {User.quota_reserved: User.quota_reserved + amount},
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise QuoteQuotaUnavailable("QUOTE_QUOTA_UNAVAILABLE")

    reservation = QuoteQuotaReservation(
        reservation_id=str(uuid.uuid4()),
        quote_job_id=quote_job_id,
        user_id=user_id,
        amount=amount,
        status=QUOTA_RESERVED,
    )
    db.add(reservation)
    db.flush()
    return reservation


def ensure_quote_quota_reservation(db: Session, *, user_id: int, quote_job_id: str) -> QuoteQuotaReservation:
    existing = quote_quota_reservation(db, quote_job_id, lock=True)
    if existing:
        if existing.user_id != user_id:
            raise QuoteConsistencyError("QUOTE_QUOTA_OWNER_MISMATCH")
        if existing.status == QUOTA_RESERVED:
            return existing
        raise QuoteConsistencyError(f"QUOTE_QUOTA_ALREADY_{existing.status.upper()}")
    return reserve_quote_quota(db, user_id=user_id, quote_job_id=quote_job_id)


def consume_quote_quota(db: Session, *, quote_job_id: str) -> QuoteQuotaReservation:
    reservation = quote_quota_reservation(db, quote_job_id, lock=True)
    if not reservation:
        raise QuoteConsistencyError("QUOTE_QUOTA_RESERVATION_MISSING")
    if reservation.status == QUOTA_CONSUMED:
        return reservation
    if reservation.status != QUOTA_RESERVED:
        raise QuoteConsistencyError(f"QUOTE_QUOTA_NOT_CONSUMABLE_{reservation.status.upper()}")

    updated = (
        db.query(User)
        .filter(
            User.id == reservation.user_id,
            User.quota >= reservation.amount,
            User.quota_reserved >= reservation.amount,
        )
        .update(
            {
                User.quota: User.quota - reservation.amount,
                User.quota_reserved: User.quota_reserved - reservation.amount,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise QuoteConsistencyError("QUOTE_QUOTA_COUNTER_MISMATCH")

    reservation.status = QUOTA_CONSUMED
    reservation.consumed_at = utcnow()
    reservation.release_reason = None
    db.flush()
    return reservation


def release_quote_quota(db: Session, *, quote_job_id: str, reason: str) -> QuoteQuotaReservation | None:
    reservation = quote_quota_reservation(db, quote_job_id, lock=True)
    if not reservation or reservation.status == QUOTA_RELEASED:
        return reservation
    if reservation.status == QUOTA_CONSUMED:
        return reservation

    updated = (
        db.query(User)
        .filter(
            User.id == reservation.user_id,
            User.quota_reserved >= reservation.amount,
        )
        .update(
            {User.quota_reserved: User.quota_reserved - reservation.amount},
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise QuoteConsistencyError("QUOTE_QUOTA_RELEASE_COUNTER_MISMATCH")

    reservation.status = QUOTA_RELEASED
    reservation.release_reason = (reason or "released")[:64]
    reservation.released_at = utcnow()
    db.flush()
    return reservation


def _push_payload_snapshot(payload: dict[str, Any]) -> tuple[str, str]:
    presentation_fields = {
        "excel_base64",
        "idempotency_key",
        "excel_filename",
        "download_filename",
        "filename",
        "fileName",
        "file_name",
        "attachment_name",
        "display_title",
    }
    clean_payload = {
        key: value
        for key, value in payload.items()
        if key not in presentation_fields
    }
    payload_json = json.dumps(
        clean_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest(), payload_json


def push_idempotency_key(*, username: str, payload: dict[str, Any]) -> tuple[str, str, str]:
    payload_sha256, payload_json = _push_payload_snapshot(payload)
    quote_job_id = str(payload.get("quote_job_id") or payload.get("job_id") or "")
    raw_key = f"{username}:{quote_job_id}:{payload_sha256}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest(), payload_sha256, payload_json


def _existing_push_start(existing: QuotePushAttempt) -> PushAttemptStart:
    if existing.status == PUSH_DELIVERED:
        return PushAttemptStart(existing, "delivered")
    if existing.status == PUSH_EXTERNAL_DELIVERED:
        return PushAttemptStart(existing, "finalize")
    if existing.status in {
        PUSH_SENDING,
        PUSH_N8N_CLAIMED,
        PUSH_N8N_DISPATCHING,
        PUSH_DELIVERY_UNKNOWN,
    }:
        return PushAttemptStart(existing, "in_progress")
    if existing.status == PUSH_FAILED:
        existing.status = PUSH_SENDING
        existing.retry_count = int(existing.retry_count or 0) + 1
        existing.error_message = None
        return PushAttemptStart(existing, "send")
    raise QuoteConsistencyError(f"QUOTE_PUSH_UNKNOWN_STATUS_{existing.status}")


def start_quote_push_attempt(
    db: Session,
    *,
    username: str,
    quote_job_id: str | None,
    payload: dict[str, Any],
) -> PushAttemptStart:
    idempotency_key, payload_sha256, payload_json = push_idempotency_key(username=username, payload=payload)
    existing = (
        db.query(QuotePushAttempt)
        .filter(QuotePushAttempt.idempotency_key == idempotency_key)
        .with_for_update()
        .first()
    )
    if existing:
        return _existing_push_start(existing)

    attempt = QuotePushAttempt(
        idempotency_key=idempotency_key,
        quote_job_id=quote_job_id,
        username=username,
        payload_sha256=payload_sha256,
        payload_json=payload_json,
        status=PUSH_SENDING,
    )
    db.add(attempt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(QuotePushAttempt)
            .filter(QuotePushAttempt.idempotency_key == idempotency_key)
            .with_for_update()
            .one()
        )
        return _existing_push_start(existing)
    return PushAttemptStart(attempt, "send")


def _quote_push_attempt_by_key(
    db: Session,
    *,
    idempotency_key: str,
    quote_job_id: str | None = None,
) -> QuotePushAttempt:
    attempt = (
        db.query(QuotePushAttempt)
        .filter(QuotePushAttempt.idempotency_key == idempotency_key)
        .with_for_update()
        .first()
    )
    if not attempt:
        raise QuoteConsistencyError("QUOTE_PUSH_ATTEMPT_NOT_FOUND")
    if quote_job_id and attempt.quote_job_id and attempt.quote_job_id != quote_job_id:
        raise QuoteConsistencyError("QUOTE_PUSH_JOB_MISMATCH")
    return attempt


def claim_quote_push_for_n8n(
    db: Session,
    *,
    idempotency_key: str,
    quote_job_id: str | None = None,
) -> PushAttemptStart:
    attempt = _quote_push_attempt_by_key(
        db,
        idempotency_key=idempotency_key,
        quote_job_id=quote_job_id,
    )
    if attempt.status == PUSH_SENDING:
        attempt.status = PUSH_N8N_CLAIMED
        attempt.error_message = None
        db.flush()
        return PushAttemptStart(attempt, "claimed")
    if attempt.status in {PUSH_EXTERNAL_DELIVERED, PUSH_DELIVERED}:
        return PushAttemptStart(attempt, "delivered")
    if attempt.status == PUSH_N8N_CLAIMED:
        return PushAttemptStart(attempt, "in_progress")
    if attempt.status in {PUSH_N8N_DISPATCHING, PUSH_DELIVERY_UNKNOWN}:
        return PushAttemptStart(attempt, "delivery_unknown")
    if attempt.status == PUSH_FAILED:
        return PushAttemptStart(attempt, "failed")
    raise QuoteConsistencyError(f"QUOTE_PUSH_UNKNOWN_STATUS_{attempt.status}")


def mark_quote_push_n8n_dispatching(
    db: Session,
    *,
    idempotency_key: str,
    quote_job_id: str | None = None,
) -> QuotePushAttempt:
    attempt = _quote_push_attempt_by_key(
        db,
        idempotency_key=idempotency_key,
        quote_job_id=quote_job_id,
    )
    if attempt.status in {PUSH_N8N_DISPATCHING, PUSH_EXTERNAL_DELIVERED, PUSH_DELIVERED}:
        return attempt
    if attempt.status != PUSH_N8N_CLAIMED:
        raise QuoteConsistencyError(f"QUOTE_PUSH_NOT_N8N_CLAIMED_{attempt.status}")
    attempt.status = PUSH_N8N_DISPATCHING
    attempt.error_message = None
    db.flush()
    return attempt


def mark_quote_push_failed_before_dispatch(
    db: Session,
    *,
    idempotency_key: str,
    error_message: str,
    quote_job_id: str | None = None,
) -> QuotePushAttempt:
    attempt = _quote_push_attempt_by_key(
        db,
        idempotency_key=idempotency_key,
        quote_job_id=quote_job_id,
    )
    if attempt.status in {PUSH_EXTERNAL_DELIVERED, PUSH_DELIVERED}:
        return attempt
    if attempt.status == PUSH_FAILED:
        return attempt
    if attempt.status != PUSH_N8N_CLAIMED:
        raise QuoteConsistencyError(f"QUOTE_PUSH_ALREADY_MAY_HAVE_DISPATCHED_{attempt.status}")
    attempt.status = PUSH_FAILED
    attempt.error_message = (error_message or "N8N_FAILED_BEFORE_DISPATCH")[:4000]
    db.flush()
    return attempt


def mark_quote_push_delivery_unknown(
    db: Session,
    *,
    idempotency_key: str,
    error_message: str,
    quote_job_id: str | None = None,
) -> QuotePushAttempt:
    attempt = _quote_push_attempt_by_key(
        db,
        idempotency_key=idempotency_key,
        quote_job_id=quote_job_id,
    )
    if attempt.status in {PUSH_DELIVERY_UNKNOWN, PUSH_EXTERNAL_DELIVERED, PUSH_DELIVERED}:
        return attempt
    if attempt.status != PUSH_N8N_DISPATCHING:
        raise QuoteConsistencyError(f"QUOTE_PUSH_NOT_DISPATCHING_{attempt.status}")
    attempt.status = PUSH_DELIVERY_UNKNOWN
    attempt.error_message = (error_message or "N8N_DELIVERY_UNKNOWN")[:4000]
    db.flush()
    return attempt


def mark_quote_push_external_delivered(
    db: Session,
    *,
    attempt_id: int,
    status_code: int,
    response_text: str | None,
) -> QuotePushAttempt:
    attempt = db.query(QuotePushAttempt).filter(QuotePushAttempt.id == attempt_id).with_for_update().one()
    if attempt.status == PUSH_DELIVERED:
        return attempt
    if attempt.status not in {PUSH_SENDING, PUSH_N8N_DISPATCHING, PUSH_EXTERNAL_DELIVERED}:
        raise QuoteConsistencyError(f"QUOTE_PUSH_NOT_SENDING_{attempt.status}")
    attempt.status = PUSH_EXTERNAL_DELIVERED
    attempt.external_status_code = status_code
    attempt.external_response = (response_text or "")[:4000]
    attempt.error_message = None
    attempt.external_delivered_at = attempt.external_delivered_at or utcnow()
    db.flush()
    return attempt


def lock_quote_push_for_finalize(db: Session, *, attempt_id: int) -> QuotePushAttempt:
    attempt = db.query(QuotePushAttempt).filter(QuotePushAttempt.id == attempt_id).with_for_update().one()
    if attempt.status not in {PUSH_EXTERNAL_DELIVERED, PUSH_DELIVERED}:
        raise QuoteConsistencyError(f"QUOTE_PUSH_NOT_FINALIZABLE_{attempt.status}")
    return attempt


def mark_quote_push_failed(db: Session, *, attempt_id: int, error_message: str) -> QuotePushAttempt:
    attempt = db.query(QuotePushAttempt).filter(QuotePushAttempt.id == attempt_id).with_for_update().one()
    if attempt.status in {
        PUSH_N8N_CLAIMED,
        PUSH_N8N_DISPATCHING,
        PUSH_DELIVERY_UNKNOWN,
        PUSH_EXTERNAL_DELIVERED,
        PUSH_DELIVERED,
    }:
        return attempt
    attempt.status = PUSH_FAILED
    attempt.error_message = (error_message or "QUOTE_PUSH_FAILED")[:4000]
    db.flush()
    return attempt


def quote_push_result(attempt: QuotePushAttempt) -> dict[str, Any]:
    if not attempt.result_json:
        return {}
    try:
        value = json.loads(attempt.result_json)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def mark_quote_push_delivered(
    db: Session,
    *,
    attempt_id: int,
    quote_history_id: int | None,
    result: dict[str, Any],
) -> QuotePushAttempt:
    attempt = db.query(QuotePushAttempt).filter(QuotePushAttempt.id == attempt_id).with_for_update().one()
    if attempt.status == PUSH_DELIVERED:
        return attempt
    if attempt.status != PUSH_EXTERNAL_DELIVERED:
        raise QuoteConsistencyError(f"QUOTE_PUSH_NOT_EXTERNALLY_DELIVERED_{attempt.status}")
    attempt.status = PUSH_DELIVERED
    attempt.quote_history_id = quote_history_id
    attempt.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    attempt.error_message = None
    attempt.delivered_at = utcnow()
    db.flush()
    return attempt
