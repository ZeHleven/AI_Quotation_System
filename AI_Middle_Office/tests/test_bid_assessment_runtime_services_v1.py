from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models.registry  # noqa: F401 - register the complete FK graph
from app.api.v1 import bid_assessment_events as events_api
from app.core.config import settings
from app.core.database import Base, get_db
from app.dependencies import get_current_user
from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_eventing import (
    BidAuditLog,
    BidIdempotencyRecord,
    BidOutboxEvent,
    BidProcessedEvent,
    BidPublicEvent,
)
from app.models.user import User
from app.services.bid_assessment_eventing import (
    append_outbox_event,
    append_stream_control_events,
    canonical_hash,
    format_public_event_sse,
    list_public_events_after,
    process_outbox_event_once,
    project_outbox_event_to_public,
    resolve_sse_start_sequence,
    utc_now,
)
from app.services.bid_assessment_idempotency import (
    BidIdempotencyInProgress,
    BidIdempotencyKeyReused,
    IdempotentCommandResult,
    begin_idempotent_request,
    execute_idempotent_request,
)
from app.services.bid_assessment_outbox import (
    BidOutboxLeaseLost,
    claim_outbox_events,
    dispatch_outbox_batch,
    mark_outbox_published,
)
from app.services.bid_assessment_state import (
    BidActor,
    BidVersionConflict,
    transition_bid_state,
)
from app.tasks.celery_app import celery_app


@pytest.fixture()
def runtime_session_factory(tmp_path):
    database_path = tmp_path / "runtime.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    yield factory
    engine.dispose()


def _new_user(session_factory, *, role: str = "user") -> User:
    db = session_factory()
    try:
        with db.begin():
            user = User(
                username=f"bid-runtime-{uuid.uuid4().hex}",
                hashed_password="not-used",
                role=role,
                role_version=1,
                quota=10,
                quota_reserved=0,
                is_active=True,
                must_change_password=False,
            )
            db.add(user)
            db.flush()
            list(user.role_assignments)
        return user
    finally:
        db.close()


def _new_assessment(
    session_factory,
    *,
    owner_id: int,
    business_status: str = "draft",
) -> BidAssessment:
    db = session_factory()
    try:
        with db.begin():
            assessment = BidAssessment(
                id=str(uuid.uuid4()),
                title="运行服务事务测试",
                client_name="测试客户",
                lifecycle_status="active",
                business_status=business_status,
                created_by=owner_id,
                row_version=1,
            )
            db.add(assessment)
            db.flush()
        return assessment
    finally:
        db.close()


def _append_assessment_created_event(session_factory, assessment_id: str) -> str:
    db = session_factory()
    try:
        with db.begin():
            assessment = db.query(BidAssessment).filter_by(id=assessment_id).one()
            event_row = append_outbox_event(
                db,
                event_type="bid.assessment.created.v1",
                producer="runtime-test",
                aggregate_type="assessment",
                aggregate_id=assessment.id,
                aggregate_version=int(assessment.row_version),
                assessment_id=assessment.id,
                request_id=f"req_{uuid.uuid4().hex}",
                payload_schema="bid.assessment.created.v1.payload",
                payload={
                    "snapshot": {
                        "assessment_id": assessment.id,
                        "business_status": assessment.business_status,
                        "row_version": int(assessment.row_version),
                    }
                },
                dedupe_key=f"created:{assessment.id}",
            )
        return event_row.event_id
    finally:
        db.close()


def test_state_outbox_and_audit_commit_or_rollback_together(runtime_session_factory) -> None:
    owner = _new_user(runtime_session_factory)
    assessment = _new_assessment(
        runtime_session_factory,
        owner_id=owner.id,
        business_status="preparing",
    )
    db = runtime_session_factory()
    try:
        with db.begin():
            result = transition_bid_state(
                db,
                entity_type="assessment",
                entity_id=assessment.id,
                to_state="stale_input",
                expected_row_version=1,
                event_type="bid.assessment.input_stale.v1",
                actor=BidActor.user(owner.id, owner.username),
                request_id=f"req_{uuid.uuid4().hex}",
            )
        persisted = db.query(BidAssessment).filter_by(id=assessment.id).one()
        outbox = db.query(BidOutboxEvent).filter_by(event_id=result.outbox_event_id).one()
        audit = db.query(BidAuditLog).filter_by(id=result.audit_id).one()
        assert persisted.business_status == "stale_input"
        assert persisted.row_version == 2
        assert outbox.status == "pending"
        assert outbox.aggregate_version == 2
        assert outbox.payload_json["from"] == "preparing"
        assert outbox.payload_json["to"] == "stale_input"
        assert audit.before_hash == canonical_hash({"state": "preparing", "row_version": 1})
        assert audit.after_hash == canonical_hash({"state": "stale_input", "row_version": 2})
        assert audit.record_hash == canonical_hash(
            {
                "id": audit.id,
                "assessment_id": audit.assessment_id,
                "actor_type": audit.actor_type,
                "actor_id": audit.actor_id,
                "actor_ref": audit.actor_ref,
                "action": audit.action,
                "entity_type": audit.entity_type,
                "entity_id": audit.entity_id,
                "outcome": audit.outcome,
                "before_hash": audit.before_hash,
                "after_hash": audit.after_hash,
                "request_id": audit.request_id,
                "correlation_id": audit.correlation_id,
                "metadata": audit.metadata_json,
                "occurred_at": audit.occurred_at,
            }
        )
    finally:
        db.close()

    rolled_back = _new_assessment(
        runtime_session_factory,
        owner_id=owner.id,
        business_status="preparing",
    )
    db = runtime_session_factory()
    rolled_back_event_id = None
    try:
        with pytest.raises(RuntimeError, match="force rollback"):
            with db.begin():
                transition = transition_bid_state(
                    db,
                    entity_type="assessment",
                    entity_id=rolled_back.id,
                    to_state="stale_input",
                    expected_row_version=1,
                    event_type="bid.assessment.input_stale.v1",
                    actor=BidActor.user(owner.id, owner.username),
                    request_id=f"req_{uuid.uuid4().hex}",
                )
                rolled_back_event_id = transition.outbox_event_id
                raise RuntimeError("force rollback")
        db.expire_all()
        assert db.query(BidAssessment).filter_by(id=rolled_back.id).one().business_status == "preparing"
        assert db.query(BidOutboxEvent).filter_by(event_id=rolled_back_event_id).count() == 0
        assert db.query(BidAuditLog).filter_by(entity_id=rolled_back.id).count() == 0
    finally:
        db.close()


def test_state_transition_rejects_stale_row_version(runtime_session_factory) -> None:
    owner = _new_user(runtime_session_factory)
    assessment = _new_assessment(
        runtime_session_factory,
        owner_id=owner.id,
        business_status="preparing",
    )
    db = runtime_session_factory()
    try:
        with pytest.raises(BidVersionConflict):
            with db.begin():
                transition_bid_state(
                    db,
                    entity_type="assessment",
                    entity_id=assessment.id,
                    to_state="stale_input",
                    expected_row_version=7,
                    event_type="bid.assessment.input_stale.v1",
                    actor=BidActor.user(owner.id, owner.username),
                    request_id=f"req_{uuid.uuid4().hex}",
                )
        assert db.query(BidOutboxEvent).filter_by(assessment_id=assessment.id).count() == 0
    finally:
        db.close()


def test_consumer_business_change_and_processed_marker_are_transactional(
    runtime_session_factory,
) -> None:
    owner = _new_user(runtime_session_factory)
    assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    event_id = _append_assessment_created_event(runtime_session_factory, assessment.id)
    calls = {"count": 0}

    def _handler(db, _event):
        calls["count"] += 1
        row = db.query(BidAssessment).filter_by(id=assessment.id).with_for_update().one()
        row.internal_note = "consumer-applied"
        return {"result_ref": f"assessment:{assessment.id}", "changed": True}

    db = runtime_session_factory()
    try:
        with db.begin():
            first = process_outbox_event_once(
                db,
                consumer_name="runtime-test-consumer",
                event_id=event_id,
                handler=_handler,
            )
            duplicate = process_outbox_event_once(
                db,
                consumer_name="runtime-test-consumer",
                event_id=event_id,
                handler=_handler,
            )
        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert calls["count"] == 1
        assert db.query(BidAssessment).filter_by(id=assessment.id).one().internal_note == "consumer-applied"
        assert (
            db.query(BidProcessedEvent)
            .filter_by(consumer_name="runtime-test-consumer", event_id=event_id)
            .count()
            == 1
        )
    finally:
        db.close()

    rollback_assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    rollback_event = _append_assessment_created_event(
        runtime_session_factory,
        rollback_assessment.id,
    )

    def _failing_handler(db, _event):
        row = db.query(BidAssessment).filter_by(id=rollback_assessment.id).one()
        row.internal_note = "must-not-persist"
        raise RuntimeError("consumer failure")

    db = runtime_session_factory()
    try:
        with pytest.raises(RuntimeError, match="consumer failure"):
            with db.begin():
                process_outbox_event_once(
                    db,
                    consumer_name="runtime-test-consumer",
                    event_id=rollback_event,
                    handler=_failing_handler,
                )
        db.expire_all()
        assert db.query(BidAssessment).filter_by(id=rollback_assessment.id).one().internal_note is None
        assert (
            db.query(BidProcessedEvent)
            .filter_by(consumer_name="runtime-test-consumer", event_id=rollback_event)
            .count()
            == 0
        )
    finally:
        db.close()


def test_public_projector_is_idempotent_and_allocates_assessment_sequence(
    runtime_session_factory,
) -> None:
    owner = _new_user(runtime_session_factory)
    assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    created_event_id = _append_assessment_created_event(runtime_session_factory, assessment.id)
    db = runtime_session_factory()
    try:
        with db.begin():
            first = project_outbox_event_to_public(db, event_id=created_event_id)
        with db.begin():
            duplicate = project_outbox_event_to_public(db, event_id=created_event_id)
        assert first.duplicate is False
        assert duplicate.duplicate is True

        with db.begin():
            assessment_row = db.query(BidAssessment).filter_by(id=assessment.id).one()
            stale_event = append_outbox_event(
                db,
                event_type="bid.assessment.input_stale.v1",
                producer="runtime-test",
                aggregate_type="assessment",
                aggregate_id=assessment.id,
                aggregate_version=int(assessment_row.row_version),
                assessment_id=assessment.id,
                request_id=f"req_{uuid.uuid4().hex}",
                payload_schema="bid.assessment.input_stale.v1.payload",
                payload={
                    "from": "preparing",
                    "to": "stale_input",
                    "recommended_view": "assessment",
                    "allowed_actions": [],
                },
                dedupe_key=f"stale:{assessment.id}",
            )
        with db.begin():
            project_outbox_event_to_public(db, event_id=stale_event.event_id)
        public_rows = (
            db.query(BidPublicEvent)
            .filter_by(assessment_id=assessment.id)
            .order_by(BidPublicEvent.sequence_no.asc())
            .all()
        )
        assert [row.sequence_no for row in public_rows] == [1, 2]
        assert [row.event_type for row in public_rows] == [
            "assessment.snapshot",
            "assessment.status.changed",
        ]
        assert all(row.origin_type == "outbox" for row in public_rows)
    finally:
        db.close()


def test_sse_cursor_reset_snapshot_and_format_are_persisted(runtime_session_factory) -> None:
    owner = _new_user(runtime_session_factory)
    assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    now = utc_now()
    db = runtime_session_factory()
    try:
        with db.begin():
            start = resolve_sse_start_sequence(
                db,
                assessment_id=assessment.id,
                last_event_id=None,
                request_id=f"req_{uuid.uuid4().hex}",
                now=now,
            )
        assert start == 0
        initial = list_public_events_after(
            db,
            assessment_id=assessment.id,
            sequence_no=start,
            now=now,
        )
        assert [row.event_type for row in initial] == ["assessment.snapshot"]
        assert initial[0].origin_type == "stream_control"
        db.rollback()

        future = now + timedelta(days=8)
        with db.begin():
            reset_start = resolve_sse_start_sequence(
                db,
                assessment_id=assessment.id,
                last_event_id=initial[0].event_id,
                request_id=f"req_{uuid.uuid4().hex}",
                now=future,
            )
        assert reset_start == 1
        reset_rows = list_public_events_after(
            db,
            assessment_id=assessment.id,
            sequence_no=reset_start,
            now=future,
        )
        assert [row.event_type for row in reset_rows] == ["stream.reset", "assessment.snapshot"]
        assert [row.sequence_no for row in reset_rows] == [2, 3]
        rendered = format_public_event_sse(reset_rows[0])
        assert f"id: {reset_rows[0].event_id}\n" in rendered
        assert "event: stream.reset\n" in rendered
        assert "retry: 5000\n" in rendered
        assert '"assessment_id"' in rendered
    finally:
        db.close()


def test_idempotency_executes_once_replays_and_rolls_back_with_business_change(
    runtime_session_factory,
) -> None:
    owner = _new_user(runtime_session_factory)
    assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    key = f"idem-{uuid.uuid4()}"
    payload = {"reason": "same intent", "amount": "10.00"}
    calls = {"count": 0}

    def _command(db):
        calls["count"] += 1
        row = db.query(BidAssessment).filter_by(id=assessment.id).with_for_update().one()
        row.internal_note = "idempotent-command"
        return IdempotentCommandResult(
            status_code=202,
            body={"assessment_id": assessment.id, "accepted": True},
            resource_type="assessment",
            resource_id=assessment.id,
        )

    db = runtime_session_factory()
    try:
        with db.begin():
            first = execute_idempotent_request(
                db,
                actor_id=owner.id,
                http_method="POST",
                route_template="/api/v1/bid-assessments/{id}/actions",
                idempotency_key=key,
                request_payload=payload,
                request_id=f"req_{uuid.uuid4().hex}",
                handler=_command,
            )
        with db.begin():
            replay = execute_idempotent_request(
                db,
                actor_id=owner.id,
                http_method="POST",
                route_template="/api/v1/bid-assessments/{id}/actions",
                idempotency_key=key,
                request_payload=payload,
                request_id=f"req_{uuid.uuid4().hex}",
                handler=_command,
            )
        assert first.replayed is False
        assert replay.replayed is True
        assert first.status_code == replay.status_code == 202
        assert first.body == replay.body
        assert calls["count"] == 1
        record = db.query(BidIdempotencyRecord).filter_by(idempotency_key=key).one()
        assert record.status == "completed"
        assert record.response_hash == canonical_hash(
            {"status_code": 202, "body": first.body}
        )
        db.rollback()

        with pytest.raises(BidIdempotencyKeyReused):
            with db.begin():
                execute_idempotent_request(
                    db,
                    actor_id=owner.id,
                    http_method="POST",
                    route_template="/api/v1/bid-assessments/{id}/actions",
                    idempotency_key=key,
                    request_payload={"reason": "different intent"},
                    request_id=f"req_{uuid.uuid4().hex}",
                    handler=_command,
                )
    finally:
        db.close()

    rollback_assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    rollback_key = f"idem-{uuid.uuid4()}"
    db = runtime_session_factory()
    try:
        with pytest.raises(RuntimeError, match="rollback idempotency"):
            with db.begin():
                execute_idempotent_request(
                    db,
                    actor_id=owner.id,
                    http_method="PATCH",
                    route_template="/api/v1/bid-assessments/{id}",
                    idempotency_key=rollback_key,
                    request_payload={"title": "new"},
                    request_id=f"req_{uuid.uuid4().hex}",
                    handler=lambda command_db: IdempotentCommandResult(
                        status_code=200,
                        body={
                            "assessment_id": command_db.query(BidAssessment)
                            .filter_by(id=rollback_assessment.id)
                            .one()
                            .id
                        },
                    ),
                )
                raise RuntimeError("rollback idempotency")
        assert db.query(BidIdempotencyRecord).filter_by(idempotency_key=rollback_key).count() == 0
    finally:
        db.close()


def test_in_progress_idempotency_does_not_execute_second_command(runtime_session_factory) -> None:
    owner = _new_user(runtime_session_factory)
    key = f"idem-{uuid.uuid4()}"
    db = runtime_session_factory()
    try:
        with db.begin():
            begin_idempotent_request(
                db,
                actor_id=owner.id,
                http_method="DELETE",
                route_template="/api/v1/bid-assessments/{id}",
                idempotency_key=key,
                request_payload={"reason": "cancel"},
                request_id=f"req_{uuid.uuid4().hex}",
            )
            with pytest.raises(BidIdempotencyInProgress):
                begin_idempotent_request(
                    db,
                    actor_id=owner.id,
                    http_method="DELETE",
                    route_template="/api/v1/bid-assessments/{id}",
                    idempotency_key=key,
                    request_payload={"reason": "cancel"},
                    request_id=f"req_{uuid.uuid4().hex}",
                )
    finally:
        db.close()


def test_outbox_dispatcher_publishes_retries_dead_letters_and_fences(
    runtime_session_factory,
) -> None:
    owner = _new_user(runtime_session_factory)
    published_assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    published_event = _append_assessment_created_event(
        runtime_session_factory,
        published_assessment.id,
    )
    delivered: list[str] = []
    result = dispatch_outbox_batch(
        worker_id="dispatcher-success",
        session_factory=runtime_session_factory,
        publisher=lambda envelope: delivered.append(envelope.event_id),
    )
    assert result.claimed == 1
    assert result.published == 1
    assert delivered == [published_event]
    db = runtime_session_factory()
    try:
        row = db.query(BidOutboxEvent).filter_by(event_id=published_event).one()
        assert row.status == "published"
        assert row.attempts == 1
        assert row.lease_owner is None
        assert row.published_at is not None
    finally:
        db.close()

    failed_assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    failed_event = _append_assessment_created_event(runtime_session_factory, failed_assessment.id)
    first_time = utc_now()

    def _unavailable(_envelope):
        raise RuntimeError("broker unavailable")

    first_failure = dispatch_outbox_batch(
        worker_id="dispatcher-failure",
        session_factory=runtime_session_factory,
        publisher=_unavailable,
        max_attempts=2,
        now=first_time,
    )
    assert first_failure.retry_wait == 1
    second_failure = dispatch_outbox_batch(
        worker_id="dispatcher-failure",
        session_factory=runtime_session_factory,
        publisher=_unavailable,
        max_attempts=2,
        now=first_time + timedelta(seconds=10),
    )
    assert second_failure.dead_lettered == 1
    db = runtime_session_factory()
    try:
        failed_row = db.query(BidOutboxEvent).filter_by(event_id=failed_event).one()
        assert failed_row.status == "dead_letter"
        assert failed_row.attempts == 2
        assert failed_row.last_error_code == "BID_OUTBOX_PUBLISH_FAILED"
    finally:
        db.close()

    fenced_assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    _append_assessment_created_event(runtime_session_factory, fenced_assessment.id)
    db = runtime_session_factory()
    try:
        with db.begin():
            claim_batch = claim_outbox_events(db, worker_id="dispatcher-fenced")
            claim = claim_batch.claims[0]
        with pytest.raises(BidOutboxLeaseLost):
            with db.begin():
                mark_outbox_published(
                    db,
                    claim=replace(claim, row_version=claim.row_version - 1),
                )
        assert (
            db.query(BidOutboxEvent)
            .filter_by(event_id=claim.envelope.event_id)
            .one()
            .status
            == "dispatching"
        )
    finally:
        db.close()

    reclaimed: list[str] = []
    reclaim_result = dispatch_outbox_batch(
        worker_id="dispatcher-reclaim",
        session_factory=runtime_session_factory,
        publisher=lambda envelope: reclaimed.append(envelope.event_id),
        now=claim.lease_until + timedelta(seconds=1),
    )
    assert reclaim_result.claimed == 1
    assert reclaim_result.published == 1
    assert reclaimed == [claim.envelope.event_id]


def test_sse_route_is_owner_scoped_resumable_and_fail_closed(
    runtime_session_factory,
) -> None:
    owner = _new_user(runtime_session_factory)
    other = _new_user(runtime_session_factory)
    assessment = _new_assessment(runtime_session_factory, owner_id=owner.id)
    now = utc_now()
    db = runtime_session_factory()
    try:
        with db.begin():
            _, rows = append_stream_control_events(
                db,
                assessment_id=assessment.id,
                request_id=f"req_{uuid.uuid4().hex}",
                now=now,
            )
            snapshot = rows[0]
            closed_payload = {"reason": "terminal_test", "terminal": True}
            closed = BidPublicEvent(
                id=str(uuid.uuid4()),
                assessment_id=assessment.id,
                sequence_no=2,
                event_id=f"aevt_{uuid.uuid4().hex}",
                origin_type="stream_control",
                source_event_id=None,
                projection_key=f"stream:test:closed:{assessment.id}",
                event_type="stream.closed",
                resource_type="assessment",
                resource_id=assessment.id,
                resource_version=1,
                request_id=f"req_{uuid.uuid4().hex}",
                payload_json=closed_payload,
                payload_hash=canonical_hash(closed_payload),
                occurred_at=now,
                expires_at=now + timedelta(days=7),
            )
            db.add(closed)
    finally:
        db.close()

    test_app = FastAPI()
    test_app.include_router(events_api.router, prefix="/api/v1")
    active_user = {"value": owner}

    def _override_user():
        return active_user["value"]

    def _override_db():
        session = runtime_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_current_user] = _override_user
    test_app.dependency_overrides[get_db] = _override_db
    old_flag = settings.feature_bid_assessment_v1_runtime
    old_factory = events_api.SessionLocal
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)
    events_api.SessionLocal = runtime_session_factory
    try:
        with TestClient(test_app) as client:
            response = client.get(
                f"/api/v1/bid-assessments/{assessment.id}/events",
                headers={"Accept": "text/event-stream"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache, no-transform"
            assert response.headers["x-accel-buffering"] == "no"
            assert f"id: {snapshot.event_id}" in response.text
            assert "event: assessment.snapshot" in response.text
            assert "event: stream.closed" in response.text

            active_user["value"] = other
            hidden = client.get(
                f"/api/v1/bid-assessments/{assessment.id}/events",
                headers={"Last-Event-ID": snapshot.event_id},
            )
            assert hidden.status_code == 404

            object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
            disabled = client.get(f"/api/v1/bid-assessments/{assessment.id}/events")
            assert disabled.status_code == 404
    finally:
        events_api.SessionLocal = old_factory
        object.__setattr__(settings, "feature_bid_assessment_v1_runtime", old_flag)


def test_api16_abandoned_cleanup_task_is_periodic_and_worker_loaded() -> None:
    if celery_app is None:
        pytest.skip("Celery optional dependency is unavailable")
    assert "app.tasks.bid_assessment_tasks" in set(celery_app.conf.include)
    schedule = celery_app.conf.beat_schedule[
        "bid-cleanup-abandoned-upload-batches"
    ]
    assert schedule == {
        "task": "bid.cleanup_abandoned_upload_batches",
        "schedule": 300.0,
        "options": {"expires": 240},
    }
    from app.tasks.bid_assessment_tasks import (
        cleanup_abandoned_bid_upload_batches_task,
    )

    old_flag = settings.feature_bid_assessment_v1_runtime
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    try:
        assert cleanup_abandoned_bid_upload_batches_task.run(limit=1) == {
            "scanned_batches": 0,
            "released_batches": 0,
            "detached_files": 0,
            "removed_file_objects": 0,
            "preserved_references": 0,
            "deleted_objects": 0,
            "delete_failed": 0,
        }
    finally:
        object.__setattr__(settings, "feature_bid_assessment_v1_runtime", old_flag)
