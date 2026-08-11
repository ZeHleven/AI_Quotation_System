"""API idempotency reservation, conflict detection, and response replay."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bid_assessment_eventing import BidIdempotencyRecord
from app.services.bid_assessment_eventing import canonical_hash, canonical_json


class BidIdempotencyError(RuntimeError):
    code = "BID_IDEMPOTENCY_ERROR"


class BidIdempotencyInProgress(BidIdempotencyError):
    code = "BID_IDEMPOTENCY_IN_PROGRESS"


class BidIdempotencyKeyReused(BidIdempotencyError):
    code = "BID_IDEMPOTENCY_KEY_REUSED"


@dataclass(frozen=True)
class IdempotencyDecision:
    record_id: str
    replayed: bool
    response_status_code: int | None = None
    response_body: Any = None
    response_ref: str | None = None


@dataclass(frozen=True)
class IdempotentCommandResult:
    status_code: int
    body: Any
    resource_type: str | None = None
    resource_id: str | None = None
    response_ref: str | None = None


@dataclass(frozen=True)
class IdempotentExecution:
    status_code: int
    body: Any
    replayed: bool
    response_ref: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_idempotency_scope(http_method: str, route_template: str) -> tuple[str, str, str]:
    method = str(http_method).strip().upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        raise BidIdempotencyError("BID_IDEMPOTENCY_METHOD_INVALID")
    route = "/" + str(route_template).strip().lstrip("/")
    if len(route) > 255:
        raise BidIdempotencyError("BID_IDEMPOTENCY_ROUTE_TOO_LONG")
    return method, route, f"{method} {route}"


def validate_idempotency_key(idempotency_key: str) -> str:
    key = str(idempotency_key)
    try:
        encoded = key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BidIdempotencyError("BID_IDEMPOTENCY_KEY_INVALID") from exc
    if not 16 <= len(encoded) <= 128 or any(byte < 0x20 or byte > 0x7E for byte in encoded):
        raise BidIdempotencyError("BID_IDEMPOTENCY_KEY_INVALID")
    return key


def idempotency_request_hash(request_payload: Any) -> str:
    return canonical_hash(request_payload)


def _resume_or_replay_record(
    db: Session,
    *,
    record: BidIdempotencyRecord,
    request_hash: str,
    request_id: str,
    processing_timeout_seconds: int,
    retention_days: int,
    current_time: datetime,
) -> IdempotencyDecision:
    if record.request_hash != request_hash:
        raise BidIdempotencyKeyReused("BID_IDEMPOTENCY_KEY_REUSED")
    if record.status == "completed":
        return IdempotencyDecision(
            record_id=record.id,
            replayed=True,
            response_status_code=int(record.response_status_code),
            response_body=record.response_snapshot_json,
            response_ref=record.response_ref,
        )
    if record.status == "processing":
        processing_expires_at = record.processing_expires_at
        if processing_expires_at is not None and processing_expires_at.tzinfo is None:
            processing_expires_at = processing_expires_at.replace(tzinfo=timezone.utc)
        if processing_expires_at is None or processing_expires_at > current_time:
            raise BidIdempotencyInProgress("BID_IDEMPOTENCY_IN_PROGRESS")
    if record.status == "failed" and not record.retryable:
        raise BidIdempotencyInProgress("BID_IDEMPOTENCY_FAILED_NOT_RETRYABLE")

    record.status = "processing"
    record.retryable = False
    record.request_id = str(request_id)[:80]
    record.processing_expires_at = current_time + timedelta(
        seconds=max(5, int(processing_timeout_seconds))
    )
    record.completed_at = None
    record.failure_code = None
    record.response_status_code = None
    record.response_snapshot_json = None
    record.response_ref = None
    record.response_hash = None
    record.resource_type = None
    record.resource_id = None
    record.expires_at = current_time + timedelta(days=max(7, int(retention_days)))
    record.row_version = int(record.row_version) + 1
    db.flush()
    return IdempotencyDecision(record_id=record.id, replayed=False)


def begin_idempotent_request(
    db: Session,
    *,
    actor_id: int,
    http_method: str,
    route_template: str,
    idempotency_key: str,
    request_payload: Any,
    request_id: str,
    processing_timeout_seconds: int = 30,
    retention_days: int = 7,
    now: datetime | None = None,
) -> IdempotencyDecision:
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)
    method, route, scope = normalize_idempotency_scope(http_method, route_template)
    key = validate_idempotency_key(idempotency_key)
    request_hash = idempotency_request_hash(request_payload)
    record = (
        db.query(BidIdempotencyRecord)
        .filter(
            BidIdempotencyRecord.actor_id == int(actor_id),
            BidIdempotencyRecord.scope == scope,
            BidIdempotencyRecord.idempotency_key == key,
        )
        .with_for_update()
        .one_or_none()
    )
    if record is not None:
        return _resume_or_replay_record(
            db,
            record=record,
            request_hash=request_hash,
            request_id=request_id,
            processing_timeout_seconds=processing_timeout_seconds,
            retention_days=retention_days,
            current_time=current_time,
        )

    record = BidIdempotencyRecord(
        id=str(uuid.uuid4()),
        actor_id=int(actor_id),
        http_method=method,
        route_template=route,
        scope=scope,
        idempotency_key=key,
        request_id=str(request_id)[:80],
        request_hash=request_hash,
        status="processing",
        retryable=False,
        processing_expires_at=current_time
        + timedelta(seconds=max(5, int(processing_timeout_seconds))),
        expires_at=current_time + timedelta(days=max(7, int(retention_days))),
        row_version=1,
    )
    if db.get_bind().dialect.name == "sqlite":
        # Python's sqlite3 legacy transaction mode can release a first-write
        # SAVEPOINT outside the surrounding transaction. A direct flush keeps
        # local rollback semantics faithful to the production contract.
        db.add(record)
        db.flush()
        return IdempotencyDecision(record_id=record.id, replayed=False)

    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        # A concurrent request can win the unique actor/scope/key reservation
        # after the first SELECT. Re-read it with a lock and apply the same
        # replay/conflict rules instead of executing the command twice.
        concurrent = (
            db.query(BidIdempotencyRecord)
            .filter(
                BidIdempotencyRecord.actor_id == int(actor_id),
                BidIdempotencyRecord.scope == scope,
                BidIdempotencyRecord.idempotency_key == key,
            )
            .with_for_update()
            .one_or_none()
        )
        if concurrent is None:
            raise
        return _resume_or_replay_record(
            db,
            record=concurrent,
            request_hash=request_hash,
            request_id=request_id,
            processing_timeout_seconds=processing_timeout_seconds,
            retention_days=retention_days,
            current_time=current_time,
        )
    return IdempotencyDecision(record_id=record.id, replayed=False)


def complete_idempotent_request(
    db: Session,
    *,
    record_id: str,
    response_status_code: int,
    response_body: Any,
    resource_type: str | None = None,
    resource_id: str | None = None,
    response_ref: str | None = None,
    now: datetime | None = None,
) -> BidIdempotencyRecord:
    record = (
        db.query(BidIdempotencyRecord)
        .filter(BidIdempotencyRecord.id == record_id)
        .with_for_update()
        .one_or_none()
    )
    if record is None:
        raise BidIdempotencyError("BID_IDEMPOTENCY_RECORD_NOT_FOUND")
    if record.status != "processing":
        raise BidIdempotencyError("BID_IDEMPOTENCY_RECORD_NOT_PROCESSING")
    if not 100 <= int(response_status_code) <= 599:
        raise BidIdempotencyError("BID_IDEMPOTENCY_RESPONSE_STATUS_INVALID")
    if (resource_type is None) != (resource_id is None):
        raise BidIdempotencyError("BID_IDEMPOTENCY_RESOURCE_PAIR_INVALID")

    normalized_body = json.loads(canonical_json(response_body))
    record.status = "completed"
    record.retryable = False
    record.resource_type = str(resource_type)[:64] if resource_type else None
    record.resource_id = str(resource_id)[:80] if resource_id else None
    record.response_status_code = int(response_status_code)
    record.response_snapshot_json = normalized_body
    record.response_ref = str(response_ref)[:512] if response_ref else None
    record.response_hash = canonical_hash(
        {"status_code": int(response_status_code), "body": normalized_body}
    )
    record.processing_expires_at = None
    record.completed_at = now or _utc_now()
    record.failure_code = None
    record.row_version = int(record.row_version) + 1
    db.flush()
    return record


def fail_idempotent_request(
    db: Session,
    *,
    record_id: str,
    failure_code: str,
    now: datetime | None = None,
) -> BidIdempotencyRecord:
    record = (
        db.query(BidIdempotencyRecord)
        .filter(BidIdempotencyRecord.id == record_id)
        .with_for_update()
        .one_or_none()
    )
    if record is None:
        raise BidIdempotencyError("BID_IDEMPOTENCY_RECORD_NOT_FOUND")
    record.status = "failed"
    record.retryable = True
    record.failure_code = str(failure_code)[:100]
    record.processing_expires_at = None
    record.completed_at = now or _utc_now()
    record.response_status_code = None
    record.response_snapshot_json = None
    record.response_ref = None
    record.response_hash = None
    record.row_version = int(record.row_version) + 1
    db.flush()
    return record


def execute_idempotent_request(
    db: Session,
    *,
    actor_id: int,
    http_method: str,
    route_template: str,
    idempotency_key: str,
    request_payload: Any,
    request_id: str,
    handler: Callable[[Session], IdempotentCommandResult],
    processing_timeout_seconds: int = 30,
    retention_days: int = 7,
    now: datetime | None = None,
) -> IdempotentExecution:
    """Run one API command and persist its replay response in one transaction.

    The helper flushes but never commits. The caller owns the surrounding
    transaction, so business writes and the completed idempotency record are
    committed or rolled back together.
    """

    decision = begin_idempotent_request(
        db,
        actor_id=actor_id,
        http_method=http_method,
        route_template=route_template,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        request_id=request_id,
        processing_timeout_seconds=processing_timeout_seconds,
        retention_days=retention_days,
        now=now,
    )
    if decision.replayed:
        return IdempotentExecution(
            status_code=int(decision.response_status_code),
            body=decision.response_body,
            replayed=True,
            response_ref=decision.response_ref,
        )

    result = handler(db)
    complete_idempotent_request(
        db,
        record_id=decision.record_id,
        response_status_code=result.status_code,
        response_body=result.body,
        resource_type=result.resource_type,
        resource_id=result.resource_id,
        response_ref=result.response_ref,
        now=now,
    )
    return IdempotentExecution(
        status_code=int(result.status_code),
        body=json.loads(canonical_json(result.body)),
        replayed=False,
        response_ref=result.response_ref,
    )
