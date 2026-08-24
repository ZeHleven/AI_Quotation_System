from __future__ import annotations

import hashlib
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models.registry  # noqa: F401 - register the complete FK graph
import app.services.bid_lot_detection_worker as lot_worker
from app.core.config import settings
from app.core.database import Base
from app.models.bid_assessment import (
    BidAssessment,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidFileObject,
    BidLotCandidate,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseAttempt,
    BidDocumentParseEvent,
    BidDocumentParseHead,
    BidDocumentParseRun,
    BidDocumentParseUnit,
    BidEvidenceFragment,
)
from app.models.bid_assessment_eventing import (
    BidOutboxEvent,
    BidProcessedEvent,
    BidPublicEvent,
)
from app.models.bid_assessment_lots import (
    BidLotCandidateEvidence,
    BidLotDetectionAttempt,
    BidLotDetectionEvent,
    BidLotDetectionRun,
)
from app.models.user import User
from app.services.bid_assessment_eventing import (
    PUBLIC_PROJECTOR_CONSUMER,
    project_outbox_event_to_public,
    utc_now,
)
from app.services.bid_document_parse_runs import ensure_document_parse_run
from app.services.bid_document_parse_worker import (
    BidDocumentParseFencingRejected,
    BidDocumentParseResultInvalid,
    DocumentParseResult,
    EvidenceFragmentResult,
    ParseUnitResult,
    claim_document_parse_run,
    complete_document_parse_run,
    fail_document_parse_run,
    heartbeat_document_parse_run,
)
from app.services.bid_parse_quality_gate import (
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    evaluate_pdf_parse_quality,
)
from app.services.bid_lot_detection_runs import (
    build_manifest_parse_set,
    ensure_lot_detection_run,
)
from app.services.bid_lot_detection_worker import (
    consume_lot_detection_requested_event,
    execute_lot_detection_request,
)


@pytest.fixture()
def phase2_session_factory(tmp_path):
    database_path = tmp_path / "phase2-workers.db"
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


@dataclass(frozen=True)
class _Phase2Seed:
    owner_id: int
    assessment_id: str
    manifest_id: str
    document_version_id: str


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_current_manifest(session_factory) -> _Phase2Seed:
    token = uuid.uuid4().hex
    assessment_id = str(uuid.uuid4())
    file_object_id = f"obj_{uuid.uuid4().hex}"
    document_id = f"doc_{uuid.uuid4().hex}"
    document_version_id = f"ver_{uuid.uuid4().hex}"
    manifest_id = f"man_{uuid.uuid4().hex}"
    db = session_factory()
    try:
        with db.begin():
            owner = User(
                username=f"phase2-worker-{token}",
                hashed_password="not-used",
                role="user",
                role_version=1,
                quota=10,
                quota_reserved=0,
                is_active=True,
                must_change_password=False,
            )
            db.add(owner)
            db.flush()
            assessment = BidAssessment(
                id=assessment_id,
                title="Phase 2 worker state-machine test",
                client_name="Local synthetic fixture",
                lifecycle_status="active",
                business_status="preparing",
                created_by=int(owner.id),
                updated_by=int(owner.id),
                row_version=1,
            )
            db.add(assessment)
            db.flush()
            db.add(
                BidFileObject(
                    id=file_object_id,
                    sha256=_digest(f"content:{token}"),
                    object_key=f"bid-assessment/test/{token}.pdf",
                    size_bytes=16,
                    mime_type="application/pdf",
                    storage_status="available",
                    created_by=int(owner.id),
                    row_version=1,
                )
            )
            db.add(
                BidDocument(
                    id=document_id,
                    logical_identity_key=f"test-document:{token}",
                    logical_name="synthetic-tender.pdf",
                    document_type="tender_document",
                    created_by=int(owner.id),
                )
            )
            db.flush()
            db.add(
                BidDocumentVersion(
                    id=document_version_id,
                    document_id=document_id,
                    file_object_id=file_object_id,
                    version_no=1,
                    original_filename="synthetic-tender.pdf",
                    parser_hint=None,
                    source_metadata_hash=_digest(f"metadata:{token}"),
                    source_metadata_json={"source": "synthetic-test"},
                    created_by=int(owner.id),
                )
            )
            db.add(
                BidDocumentManifest(
                    id=manifest_id,
                    assessment_id=assessment_id,
                    version=1,
                    manifest_hash=_digest(f"manifest:{token}"),
                    change_note="synthetic test fixture",
                    committed_by=int(owner.id),
                )
            )
            db.flush()
            db.add(
                BidManifestDocument(
                    manifest_id=manifest_id,
                    document_version_id=document_version_id,
                    role="tender_document",
                    order_no=0,
                )
            )
            assessment.current_manifest_id = manifest_id
            assessment.row_version = 2
            db.flush()
        return _Phase2Seed(
            owner_id=int(owner.id),
            assessment_id=assessment_id,
            manifest_id=manifest_id,
            document_version_id=document_version_id,
        )
    finally:
        db.close()


def _schedule_parse(session_factory, seed: _Phase2Seed):
    db = session_factory()
    try:
        with db.begin():
            schedule = ensure_document_parse_run(
                db,
                document_version_id=seed.document_version_id,
                parser_profile_version="bid-document-parser-profile-v1",
                requested_at=utc_now(),
            )
        return schedule
    finally:
        db.close()


def _schedule_parse_with_profile(session_factory, seed: _Phase2Seed, profile: str):
    db = session_factory()
    try:
        with db.begin():
            schedule = ensure_document_parse_run(
                db,
                document_version_id=seed.document_version_id,
                parser_profile_version=profile,
                requested_at=utc_now(),
            )
        return schedule
    finally:
        db.close()


def _synthetic_parse_result(
    text: str = "第一标段：室内装饰工程",
) -> DocumentParseResult:
    return DocumentParseResult(
        status="succeeded",
        quality_grade="high",
        quality_score=95,
        ocr_status="not_applicable",
        units=(
            ParseUnitResult(
                unit_key="page:1",
                unit_type="page",
                ordinal=0,
                page_no=1,
                content_source="native",
                status="succeeded",
                ocr_status="not_applicable",
                text_hash=_digest(text),
                text_length=len(text),
            ),
        ),
        evidence=(
            EvidenceFragmentResult(
                evidence_key="lot-heading:1",
                unit_key="page:1",
                locator_type="section",
                locator={"page_no": 1, "section_index": 0},
                normalized_text=text,
                ordinal=0,
            ),
        ),
    )


def _complete_synthetic_parse(session_factory, seed: _Phase2Seed) -> str:
    schedule = _schedule_parse(session_factory, seed)
    now = utc_now()
    db = session_factory()
    try:
        with db.begin():
            claim = claim_document_parse_run(
                db,
                run_id=str(schedule.run.id),
                worker_id="document-worker:test",
                lease_seconds=60,
                max_attempts=3,
                request_id="req-parse-success",
                causation_event_id=None,
                now=now,
            )
            assert claim is not None
        with db.begin():
            completion = complete_document_parse_run(
                db,
                claim=claim,
                result=_synthetic_parse_result(),
                request_id="req-parse-success",
                causation_event_id=None,
                now=now + timedelta(seconds=1),
            )
            assert completion.status == "succeeded"
        return str(schedule.run.id)
    finally:
        db.close()


def _schedule_lot_detection(session_factory, seed: _Phase2Seed):
    db = session_factory()
    try:
        with db.begin():
            parse_set = build_manifest_parse_set(db, manifest_id=seed.manifest_id)
            assert parse_set.status == "ready"
            schedule = ensure_lot_detection_run(
                db,
                parse_set=parse_set,
                assessment_id=seed.assessment_id,
                request_id="req-lot-detection",
                causation_event_id=None,
                requested_at=utc_now(),
            )
            assert schedule.request_event_id is not None
        return schedule
    finally:
        db.close()


def test_document_worker_completes_immutable_authority_and_emits_event(
    phase2_session_factory,
) -> None:
    seed = _seed_current_manifest(phase2_session_factory)
    schedule = _schedule_parse(phase2_session_factory, seed)
    started_at = utc_now()
    db = phase2_session_factory()
    try:
        with db.begin():
            claim = claim_document_parse_run(
                db,
                run_id=str(schedule.run.id),
                worker_id="document-worker:success",
                lease_seconds=30,
                max_attempts=3,
                request_id="req-document-success",
                causation_event_id=None,
                now=started_at,
            )
            assert claim is not None
        with db.begin():
            renewed_until = heartbeat_document_parse_run(
                db,
                claim=claim,
                lease_seconds=60,
                now=started_at + timedelta(seconds=1),
            )
            assert renewed_until > claim.lease_until
        with db.begin():
            completion = complete_document_parse_run(
                db,
                claim=claim,
                result=_synthetic_parse_result(),
                request_id="req-document-success",
                causation_event_id=None,
                now=started_at + timedelta(seconds=2),
            )
            assert completion.status == "succeeded"
            assert completion.result_hash
            assert len(completion.emitted_event_ids) == 1

        run = db.query(BidDocumentParseRun).filter_by(id=schedule.run.id).one()
        head = (
            db.query(BidDocumentParseHead)
            .filter_by(document_version_id=seed.document_version_id)
            .one()
        )
        attempt = (
            db.query(BidDocumentParseAttempt)
            .filter_by(run_id=schedule.run.id)
            .one()
        )
        assert run.status == "succeeded"
        assert run.ocr_status == "not_applicable"
        assert run.page_count == 1
        assert head.current_run_id == run.id
        assert attempt.status == "succeeded"
        assert db.query(BidDocumentParseUnit).filter_by(run_id=run.id).count() == 1
        assert db.query(BidEvidenceFragment).filter_by(parse_run_id=run.id).count() == 1
        assert [
            row.event_type
            for row in (
                db.query(BidDocumentParseEvent)
                .filter_by(run_id=run.id)
                .order_by(BidDocumentParseEvent.sequence_no)
                .all()
            )
        ] == ["parse.requested", "parse.attempt_started", "parse.completed"]
        outbox = db.query(BidOutboxEvent).filter_by(
            event_type="bid.document.parsed.v1"
        ).one()
        assert outbox.assessment_id == seed.assessment_id
        assert outbox.payload_json["quality"]["ocr_status"] == "not_applicable"
    finally:
        db.close()


def test_document_worker_validates_rq1b_report_before_authority_write(
    phase2_session_factory,
) -> None:
    seed = _seed_current_manifest(phase2_session_factory)
    schedule = _schedule_parse_with_profile(
        phase2_session_factory,
        seed,
        PDF_RQ1B_PARSER_PROFILE_VERSION,
    )
    now = utc_now()
    db = phase2_session_factory()
    try:
        with db.begin():
            claim = claim_document_parse_run(
                db,
                run_id=str(schedule.run.id),
                worker_id="document-worker:rq1b",
                lease_seconds=60,
                max_attempts=3,
                request_id="req-rq1b-authority",
                causation_event_id=None,
                now=now,
            )
            assert claim is not None
        evaluation = evaluate_pdf_parse_quality(
            layout=SimpleNamespace(
                pages=(
                    SimpleNamespace(
                        status="succeeded",
                        content_source="native",
                        ocr_status="not_applicable",
                    ),
                ),
                warnings=(),
            ),
            chunks=SimpleNamespace(
                metrics={
                    "retrieval_child_count": 1,
                    "evidence_atom_count": 2,
                    "heading_block_count": 1,
                    "citable_heading_atom_count": 1,
                },
                warnings=(),
            ),
        )
        base = _synthetic_parse_result()
        valid = DocumentParseResult(
            status=base.status,
            quality_grade=evaluation.grade,
            quality_score=evaluation.score,
            ocr_status=base.ocr_status,
            units=base.units,
            evidence=base.evidence,
            warnings=(evaluation.to_warning(),),
        )
        drifted_warning = deepcopy(evaluation.to_warning())
        drifted_warning["details"]["result_hash"] = "0" * 64
        drifted = DocumentParseResult(
            status=valid.status,
            quality_grade=valid.quality_grade,
            quality_score=valid.quality_score,
            ocr_status=valid.ocr_status,
            units=valid.units,
            evidence=valid.evidence,
            warnings=(drifted_warning,),
        )
        with pytest.raises(BidDocumentParseResultInvalid):
            with db.begin():
                complete_document_parse_run(
                    db,
                    claim=claim,
                    result=drifted,
                    request_id="req-rq1b-authority",
                    causation_event_id=None,
                    now=now + timedelta(seconds=1),
                )
        assert db.query(BidDocumentParseUnit).count() == 0
        db.rollback()
        with db.begin():
            completion = complete_document_parse_run(
                db,
                claim=claim,
                result=valid,
                request_id="req-rq1b-authority",
                causation_event_id=None,
                now=now + timedelta(seconds=2),
            )
        assert completion.status == "succeeded"
        run = db.query(BidDocumentParseRun).filter_by(id=str(schedule.run.id)).one()
        assert run.quality_score == 100
        assert run.quality_grade == "high"
        assert run.warnings_json[0]["details"]["result_hash"] == evaluation.result_hash
    finally:
        db.close()


def test_document_worker_expires_old_lease_rejects_stale_fence_and_fails_finally(
    phase2_session_factory,
) -> None:
    seed = _seed_current_manifest(phase2_session_factory)
    schedule = _schedule_parse(phase2_session_factory, seed)
    started_at = utc_now()
    db = phase2_session_factory()
    try:
        with db.begin():
            first_claim = claim_document_parse_run(
                db,
                run_id=str(schedule.run.id),
                worker_id="document-worker:first",
                lease_seconds=5,
                max_attempts=2,
                request_id="req-document-recovery",
                causation_event_id=None,
                now=started_at,
            )
            assert first_claim is not None
        with db.begin():
            second_claim = claim_document_parse_run(
                db,
                run_id=str(schedule.run.id),
                worker_id="document-worker:second",
                lease_seconds=30,
                max_attempts=2,
                request_id="req-document-recovery",
                causation_event_id=None,
                now=started_at + timedelta(seconds=6),
            )
            assert second_claim is not None
            assert second_claim.attempt_no == 2
            assert second_claim.fencing_token > first_claim.fencing_token

        with pytest.raises(BidDocumentParseFencingRejected):
            with db.begin():
                heartbeat_document_parse_run(
                    db,
                    claim=first_claim,
                    lease_seconds=30,
                    now=started_at + timedelta(seconds=7),
                )

        with db.begin():
            completion = fail_document_parse_run(
                db,
                claim=second_claim,
                error_code="BID_DOCUMENT_PARSE_SYNTHETIC_FAILURE",
                retryable=True,
                max_attempts=2,
                request_id="req-document-recovery",
                causation_event_id=None,
                now=started_at + timedelta(seconds=8),
            )
            assert completion.status == "failed"
            assert completion.requeued is False

        run = db.query(BidDocumentParseRun).filter_by(id=schedule.run.id).one()
        attempts = (
            db.query(BidDocumentParseAttempt)
            .filter_by(run_id=run.id)
            .order_by(BidDocumentParseAttempt.attempt_no)
            .all()
        )
        assert run.status == "failed"
        assert run.retryable is False
        assert run.ocr_status == "not_requested"
        assert [row.status for row in attempts] == ["expired", "failed"]
        assert [row.fencing_token for row in attempts] == [1, 2]
        failure = db.query(BidOutboxEvent).filter_by(
            event_type="bid.document.parse_failed.v1"
        ).one()
        assert failure.payload_json["attempt_count"] == 2
        assert failure.payload_json["retryable"] is False
    finally:
        db.close()


def test_lot_worker_consumes_once_completes_candidates_and_projects_public_event(
    phase2_session_factory,
) -> None:
    seed = _seed_current_manifest(phase2_session_factory)
    _complete_synthetic_parse(phase2_session_factory, seed)
    schedule = _schedule_lot_detection(phase2_session_factory, seed)
    request_event_id = str(schedule.request_event_id)

    db = phase2_session_factory()
    try:
        with db.begin():
            first = consume_lot_detection_requested_event(
                db,
                event_id=request_event_id,
            )
            assert first.duplicate is False
            assert first.value["detection_run_id"] == schedule.run.id
        with db.begin():
            duplicate = consume_lot_detection_requested_event(
                db,
                event_id=request_event_id,
            )
            assert duplicate.duplicate is True
    finally:
        db.close()

    result = execute_lot_detection_request(
        event_id=request_event_id,
        session_factory=phase2_session_factory,
    )
    assert result.status == "succeeded"
    assert result.candidate_count == 1

    db = phase2_session_factory()
    try:
        run = db.query(BidLotDetectionRun).filter_by(id=schedule.run.id).one()
        assessment = db.query(BidAssessment).filter_by(id=seed.assessment_id).one()
        candidate = db.query(BidLotCandidate).filter_by(
            detection_run_id=run.id
        ).one()
        evidence_link = db.query(BidLotCandidateEvidence).filter_by(
            lot_candidate_id=candidate.id
        ).one()
        attempt = db.query(BidLotDetectionAttempt).filter_by(run_id=run.id).one()
        detected_event = db.query(BidOutboxEvent).filter_by(
            event_type="bid.lots.detected.v1"
        ).one()
        assert run.status == "succeeded"
        assert run.candidate_count == 1
        assert attempt.status == "succeeded"
        assert assessment.business_status == "awaiting_lot_selection"
        assert candidate.lot_code == "一"
        assert candidate.lot_name == "室内装饰工程"
        assert evidence_link.support_role == "identity"
        assert detected_event.payload_json["selection_required"] is True

    finally:
        db.close()

    projection_db = phase2_session_factory()
    try:
        with projection_db.begin():
            projection = project_outbox_event_to_public(
                projection_db,
                event_id=str(detected_event.event_id),
            )
            assert projection.duplicate is False
        public = projection_db.query(BidPublicEvent).filter_by(
            source_event_id=detected_event.event_id
        ).one()
        assert public.event_type == "lot.selection.required"
        assert public.resource_id == seed.assessment_id
    finally:
        projection_db.close()


def test_lot_worker_requeues_then_emits_sanitized_terminal_failure(
    phase2_session_factory,
    monkeypatch,
) -> None:
    seed = _seed_current_manifest(phase2_session_factory)
    _complete_synthetic_parse(phase2_session_factory, seed)
    schedule = _schedule_lot_detection(phase2_session_factory, seed)
    request_event_id = str(schedule.request_event_id)

    def _raise_synthetic_failure(_evidence):
        raise RuntimeError("synthetic detector failure details must not leak")

    monkeypatch.setattr(lot_worker, "detect_lot_candidates", _raise_synthetic_failure)
    previous_max_attempts = settings.bid_lot_detection_max_attempts
    object.__setattr__(settings, "bid_lot_detection_max_attempts", 2)
    try:
        first = execute_lot_detection_request(
            event_id=request_event_id,
            session_factory=phase2_session_factory,
        )
        second = execute_lot_detection_request(
            event_id=request_event_id,
            session_factory=phase2_session_factory,
        )
    finally:
        object.__setattr__(
            settings,
            "bid_lot_detection_max_attempts",
            previous_max_attempts,
        )

    assert first.status == "queued"
    assert second.status == "failed"
    db = phase2_session_factory()
    try:
        run = db.query(BidLotDetectionRun).filter_by(id=schedule.run.id).one()
        attempts = (
            db.query(BidLotDetectionAttempt)
            .filter_by(run_id=run.id)
            .order_by(BidLotDetectionAttempt.attempt_no)
            .all()
        )
        assert run.status == "failed"
        assert run.retryable is False
        assert run.error_code == "BID_LOT_DETECTION_FAILED"
        assert [row.status for row in attempts] == ["failed", "failed"]
        assert [row.retryable for row in attempts] == [True, False]
        failure_events = (
            db.query(BidLotDetectionEvent)
            .filter_by(run_id=run.id)
            .order_by(BidLotDetectionEvent.sequence_no)
            .all()
        )
        assert [row.event_type for row in failure_events][-4:] == [
            "lot_detection.attempt_started",
            "lot_detection.attempt_failed",
            "lot_detection.attempt_started",
            "lot_detection.failed",
        ]
        failure = db.query(BidOutboxEvent).filter_by(
            event_type="bid.lot_detection.failed.v1"
        ).one()
        assert failure.payload_json["error_code"] == "BID_LOT_DETECTION_FAILED"
        assert failure.payload_json["retryable"] is False
        assert failure.payload_json["attempt_count"] == 2
        assert "synthetic detector failure details" not in str(failure.payload_json)
        failure_event_id = str(failure.event_id)
    finally:
        db.close()

    projection_db = phase2_session_factory()
    try:
        with projection_db.begin():
            projection = project_outbox_event_to_public(
                projection_db,
                event_id=failure_event_id,
            )
            assert projection.duplicate is False
        public = projection_db.query(BidPublicEvent).filter_by(
            source_event_id=failure_event_id
        ).one()
        assert public.event_type == "operation.failed"
        assert public.payload_json["error_code"] == "BID_LOT_DETECTION_FAILED"
        assert public.payload_json["retryable"] is False
        assert projection_db.query(BidProcessedEvent).filter_by(
            consumer_name=PUBLIC_PROJECTOR_CONSUMER,
            event_id=failure_event_id,
        ).count() == 1
    finally:
        projection_db.close()
