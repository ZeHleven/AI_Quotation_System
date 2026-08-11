"""Lease-based Transactional Outbox dispatcher for bid-assessment v1."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.bid_assessment_eventing import BidOutboxEvent
from app.services.bid_assessment_eventing import as_utc, utc_now


logger = logging.getLogger(__name__)


class BidOutboxError(RuntimeError):
    code = "BID_OUTBOX_ERROR"


class BidOutboxLeaseLost(BidOutboxError):
    code = "BID_OUTBOX_LEASE_LOST"


class BidOutboxPublisherUnavailable(BidOutboxError):
    code = "BID_QUEUE_UNAVAILABLE"


@dataclass(frozen=True)
class OutboxEnvelope:
    event_id: str
    event_type: str
    producer: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    assessment_id: str | None
    run_id: str | None
    request_id: str
    causation_event_id: str | None
    payload_schema: str
    payload: dict[str, Any]
    payload_hash: str
    occurred_at: datetime


@dataclass(frozen=True)
class ClaimedOutboxEvent:
    envelope: OutboxEnvelope
    lease_owner: str
    lease_until: datetime
    attempts: int
    row_version: int


@dataclass(frozen=True)
class OutboxClaimBatch:
    claims: tuple[ClaimedOutboxEvent, ...]
    dead_lettered: int


@dataclass(frozen=True)
class OutboxDispatchResult:
    claimed: int
    published: int
    retry_wait: int
    dead_lettered: int
    lease_lost: int


OutboxPublisher = Callable[[OutboxEnvelope], Any]


def _envelope(row: BidOutboxEvent) -> OutboxEnvelope:
    return OutboxEnvelope(
        event_id=row.event_id,
        event_type=row.event_type,
        producer=row.producer,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_version=int(row.aggregate_version),
        assessment_id=row.assessment_id,
        run_id=row.run_id,
        request_id=row.request_id,
        causation_event_id=row.causation_event_id,
        payload_schema=row.payload_schema,
        payload=dict(row.payload_json or {}),
        payload_hash=row.payload_hash,
        occurred_at=as_utc(row.occurred_at),
    )


def claim_outbox_events(
    db: Session,
    *,
    worker_id: str,
    batch_size: int = 20,
    lease_seconds: int = 60,
    max_attempts: int = 10,
    now: datetime | None = None,
) -> OutboxClaimBatch:
    """Claim available rows under a short database lease without committing."""

    current_time = as_utc(now or utc_now())
    lease_until = current_time + timedelta(seconds=max(5, int(lease_seconds)))
    size = max(1, min(int(batch_size), 500))
    candidates = (
        db.query(BidOutboxEvent)
        .filter(
            or_(
                and_(
                    BidOutboxEvent.status.in_(("pending", "retry_wait")),
                    BidOutboxEvent.available_at <= current_time,
                ),
                and_(
                    BidOutboxEvent.status == "dispatching",
                    BidOutboxEvent.lease_until <= current_time,
                ),
            )
        )
        .order_by(BidOutboxEvent.available_at.asc(), BidOutboxEvent.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(size)
        .all()
    )

    claimed: list[ClaimedOutboxEvent] = []
    dead_lettered = 0
    for row in candidates:
        if int(row.attempts) >= max(1, int(max_attempts)):
            row.status = "dead_letter"
            row.lease_owner = None
            row.lease_until = None
            row.last_error_code = "BID_OUTBOX_ATTEMPTS_EXHAUSTED"
            row.last_error_ref = "Dispatcher attempt limit reached before a confirmed publish."
            row.row_version = int(row.row_version) + 1
            dead_lettered += 1
            continue

        row.status = "dispatching"
        row.lease_owner = str(worker_id)[:128]
        row.lease_until = lease_until
        row.attempts = int(row.attempts) + 1
        row.last_error_code = None
        row.last_error_ref = None
        row.row_version = int(row.row_version) + 1
        db.flush()
        claimed.append(
            ClaimedOutboxEvent(
                envelope=_envelope(row),
                lease_owner=row.lease_owner,
                lease_until=lease_until,
                attempts=int(row.attempts),
                row_version=int(row.row_version),
            )
        )
    db.flush()
    return OutboxClaimBatch(claims=tuple(claimed), dead_lettered=dead_lettered)


def _locked_claim(
    db: Session,
    *,
    claim: ClaimedOutboxEvent,
    now: datetime,
) -> BidOutboxEvent:
    row = (
        db.query(BidOutboxEvent)
        .filter(BidOutboxEvent.event_id == claim.envelope.event_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        row is None
        or row.status != "dispatching"
        or row.lease_owner != claim.lease_owner
        or int(row.row_version) != int(claim.row_version)
        or row.lease_until is None
        or as_utc(row.lease_until) <= as_utc(now)
    ):
        raise BidOutboxLeaseLost(f"BID_OUTBOX_LEASE_LOST:{claim.envelope.event_id}")
    return row


def mark_outbox_published(
    db: Session,
    *,
    claim: ClaimedOutboxEvent,
    now: datetime | None = None,
) -> BidOutboxEvent:
    current_time = as_utc(now or utc_now())
    row = _locked_claim(db, claim=claim, now=current_time)
    row.status = "published"
    row.published_at = current_time
    row.lease_owner = None
    row.lease_until = None
    row.last_error_code = None
    row.last_error_ref = None
    row.row_version = int(row.row_version) + 1
    db.flush()
    return row


def mark_outbox_failed(
    db: Session,
    *,
    claim: ClaimedOutboxEvent,
    error_code: str,
    error_ref: str | None,
    max_attempts: int = 10,
    base_retry_seconds: int = 2,
    max_retry_seconds: int = 300,
    now: datetime | None = None,
) -> BidOutboxEvent:
    current_time = as_utc(now or utc_now())
    row = _locked_claim(db, claim=claim, now=current_time)
    exhausted = int(row.attempts) >= max(1, int(max_attempts))
    row.status = "dead_letter" if exhausted else "retry_wait"
    row.available_at = current_time + timedelta(
        seconds=min(
            max(1, int(max_retry_seconds)),
            max(1, int(base_retry_seconds)) * (2 ** max(0, int(row.attempts) - 1)),
        )
    )
    row.lease_owner = None
    row.lease_until = None
    row.last_error_code = str(error_code or "BID_OUTBOX_PUBLISH_FAILED")[:100]
    row.last_error_ref = str(error_ref)[:512] if error_ref else None
    row.row_version = int(row.row_version) + 1
    db.flush()
    return row


def publish_outbox_to_celery(envelope: OutboxEnvelope) -> str:
    """Publish a committed event to the first concrete transactional consumer."""

    from app.tasks.celery_app import celery_app

    if celery_app is None:
        raise BidOutboxPublisherUnavailable("BID_QUEUE_UNAVAILABLE")
    async_result = celery_app.send_task(
        "bid.project_public_event",
        args=[envelope.event_id],
        headers={
            "bid-event-id": envelope.event_id,
            "bid-event-type": envelope.event_type,
            "bid-request-id": envelope.request_id,
        },
    )
    return str(async_result.id)


def _publisher_failure(exc: Exception) -> tuple[str, str]:
    error_code = str(getattr(exc, "code", "BID_OUTBOX_PUBLISH_FAILED"))[:100]
    # Exception messages from brokers can contain credentials or internal
    # endpoints. Persist only a stable diagnostic type; detailed traces stay
    # in protected application logs.
    error_ref = f"{type(exc).__module__}.{type(exc).__name__}"[:512]
    return error_code, error_ref


def dispatch_outbox_batch(
    *,
    worker_id: str,
    publisher: OutboxPublisher = publish_outbox_to_celery,
    session_factory: Callable[[], Session] = SessionLocal,
    batch_size: int = 20,
    lease_seconds: int = 60,
    max_attempts: int = 10,
    base_retry_seconds: int = 2,
    max_retry_seconds: int = 300,
    now: datetime | None = None,
) -> OutboxDispatchResult:
    """Claim, publish, and finalize one batch using short transactions."""

    claim_db = session_factory()
    try:
        with claim_db.begin():
            claim_batch = claim_outbox_events(
                claim_db,
                worker_id=worker_id,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
                now=now,
            )
    finally:
        claim_db.close()

    claims = claim_batch.claims
    published = retry_wait = lease_lost = 0
    dead_lettered = claim_batch.dead_lettered
    for claim in claims:
        try:
            publisher(claim.envelope)
        except Exception as exc:
            error_code, error_ref = _publisher_failure(exc)
            finalize_db = session_factory()
            try:
                with finalize_db.begin():
                    row = mark_outbox_failed(
                        finalize_db,
                        claim=claim,
                        error_code=error_code,
                        error_ref=error_ref,
                        max_attempts=max_attempts,
                        base_retry_seconds=base_retry_seconds,
                        max_retry_seconds=max_retry_seconds,
                        now=now,
                    )
                    if row.status == "dead_letter":
                        dead_lettered += 1
                    else:
                        retry_wait += 1
            except BidOutboxLeaseLost:
                lease_lost += 1
            finally:
                finalize_db.close()
            continue

        finalize_db = session_factory()
        try:
            with finalize_db.begin():
                mark_outbox_published(finalize_db, claim=claim, now=now)
                published += 1
        except BidOutboxLeaseLost:
            lease_lost += 1
        finally:
            finalize_db.close()

    return OutboxDispatchResult(
        claimed=len(claims),
        published=published,
        retry_wait=retry_wait,
        dead_lettered=dead_lettered,
        lease_lost=lease_lost,
    )


async def bid_outbox_dispatcher_loop() -> None:
    """Continuously dispatch Outbox rows while the guarded runtime is enabled."""

    worker_id = (
        f"api-outbox:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    )[:128]
    while True:
        try:
            await asyncio.to_thread(
                dispatch_outbox_batch,
                worker_id=worker_id,
                batch_size=settings.bid_outbox_batch_size,
                lease_seconds=settings.bid_outbox_lease_seconds,
                max_attempts=settings.bid_outbox_max_attempts,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("bid_outbox_dispatch_failed")
        await asyncio.sleep(max(0.1, settings.bid_outbox_poll_seconds))
