from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker

import app.models.registry  # noqa: F401 - register the complete FK graph
from app.api.v1 import bid_assessments as assessments_api
from app.api.v1 import bid_assessment_events as assessment_events_api
from app.api.v1 import bid_assessment_reports as assessment_reports_api
from app.core.config import settings
from app.core.database import Base, get_db
from app.dependencies import get_current_user
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidFileObject,
    BidLotCandidate,
    BidManifestDocument,
    BidUploadBatch,
    BidUploadBatchDeactivation,
    BidUploadBatchFile,
)
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.bid_assessment_eventing import (
    BidAuditLog,
    BidIdempotencyRecord,
    BidOutboxEvent,
    BidProcessedEvent,
    BidPublicEvent,
)
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidAsyncOperation,
    BidCheckpoint,
    BidPlanRevision,
    BidTask,
    BidTaskAttempt,
    BidTaskDependency,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
    BidDocumentParseUnit,
    BidEvidenceFragment,
)
from app.models.bid_assessment_lots import (
    BidLotCandidateEvidence,
    BidLotDetectionHead,
    BidLotDetectionRun,
)
from app.models.bid_assessment_tooling import (
    BidContextManifest,
    BidToolInvocation,
    BidToolResult,
)
from app.models.bid_tool_execution import BidToolDispatch, BidToolDispatchAttempt
from app.models.bid_run_validation import BidRunValidation, BidRunValidationAttempt
from app.models.bid_model_execution import (
    BidModelCall,
    BidModelCallAttempt,
    BidModelResult,
)
from app.models.bid_assessment_results import (
    BidClaimCitation,
    BidFactAssertion,
    BidHardGateResult,
    BidPreliminaryDecision,
    BidPreliminaryReport,
    BidReportClaim,
    BidReportValidation,
    BidResolvedFact,
)
from app.models.user import User
from app.services import bid_assessments as assessment_commands
from app.services import bid_upload_batch_abandonments as upload_abandon_commands
from app.services import bid_upload_batch_cleanup as upload_cleanup_commands
from app.services import bid_upload_batch_commits as upload_commit_commands
from app.services import bid_upload_batch_deactivations as upload_deactivation_commands
from app.services import bid_upload_batches as upload_commands
from app.services import bid_upload_file_removals as upload_file_removal_commands
from app.services import bid_upload_files as upload_file_commands
from app.services.bid_assessment_eventing import (
    append_outbox_event,
    append_stream_control_events,
    canonical_hash,
    project_outbox_event_to_public,
)
from app.services.bid_assessment_idempotency import begin_idempotent_request
from app.services.bid_upload_batch_cleanup import cleanup_due_abandoned_upload_batches
from app.services.bid_upload_file_storage import (
    BidUploadObjectCandidate,
    BidUploadStorageError,
    LocalBidUploadObjectStorage,
    StoredBidUploadObject,
)
from app.services.bid_upload_files import cleanup_orphaned_bid_upload_objects
from app.services.bid_lot_detection_runs import build_manifest_parse_set
from app.services.bid_lot_detector import (
    LotDetectionEvidenceInput,
    detect_lot_candidates,
)
from app.services.bid_run_bootstrap import (
    BidRunInputNotReady,
    consume_plan_requested_event,
)
from app.services.bid_plan_commit import consume_run_created_event
from app.services.bid_plan_continuation import (
    PLAN_CONTINUATION_CONSUMER,
    consume_plan_continuation_requested_event,
    process_pending_plan_continuations,
)
from app.services import bid_plan_continuation as plan_continuation_service
from app.services import bid_plan_commit as plan_commit_service
from app.services import bid_task_runtime as task_runtime_service
from app.services import bid_run_lifecycle as run_lifecycle_service
from app.services import bid_tool_context as tool_context_service
from app.services import bid_tool_execution as tool_execution_service
from app.services import bid_run_validation as run_validation_service
from app.services import bid_model_execution as model_execution_service
from app.services.bid_local_agent_executor import advance_local_agent_one_action
from app.services.bid_model_execution import (
    BidModelBudgetExhausted,
    BidModelCallConflict,
    BidModelFenceLost,
    ModelProviderResult,
    claim_model_call,
    execute_model_call_claim,
    fail_model_call_attempt,
    heartbeat_model_call,
    mark_model_call_sending,
    recover_expired_model_calls,
    schedule_model_call,
    settle_model_call,
)
from app.services.bid_mvp1_executor import process_mvp1_model_queue, process_mvp1_task_queue
from app.services.bid_mvp1_local_provider import DeterministicMvp1LocalProvider
from mcp_servers.bid_assessment_evidence.service import (
    BidEvidenceMcpError,
    BidEvidenceMcpScope,
    BidEvidenceMcpService,
)
from app.services.bid_plan_commit import (
    PLAN_COMMIT_CONSUMER,
    process_pending_plan_commits,
)
from app.services.bid_task_runtime import (
    BidCheckpointConflict,
    BidTaskFenceLost,
    TaskCompletionReceipt,
    complete_task_attempt,
    fail_task_attempt,
    heartbeat_task_attempt,
    lease_next_ready_task,
    maintain_task_runtime,
    start_task_attempt,
    write_task_checkpoint,
    build_task_contract,
)
from app.services.bid_run_lifecycle import (
    finalize_cancel_requested_run,
    maintain_run_lifecycle,
)
from app.services.bid_tool_context import (
    BidToolArgumentsInvalid,
    BidToolBudgetExhausted,
    BidToolInvocationConflict,
    BidToolUnauthorized,
    assemble_context_manifest,
    authorize_tool_invocation,
    complete_tool_invocation,
    defer_tool_invocation,
    read_tool_result_slice,
    settle_async_tool_operation,
    time_out_async_tool_operation,
    validate_tool_arguments,
    verify_tool_scope_token,
)
from app.services.bid_tool_execution import (
    BidToolDispatchFenceLost,
    ToolAdapterResult,
    claim_next_tool_dispatch,
    enqueue_tool_dispatch,
    execute_tool_dispatch_claim,
    mark_tool_dispatch_sending,
    process_tool_dispatch_queue,
    recover_expired_tool_dispatch,
    settle_tool_dispatch,
)
from app.services.bid_run_validation import (
    BidRunValidationFenceLost,
    claim_next_run_validation,
    consume_run_validation_requested_event,
    execute_run_validation_claim,
    heartbeat_run_validation,
    maintain_run_validations,
    process_run_validation_queue,
    recover_expired_run_validation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_BUNDLE = json.loads(
    (PROJECT_ROOT / "schemas" / "bid_assessment" / "v1" / "contracts.schema.json").read_text(
        encoding="utf-8"
    )
)


def _validate_contract(definition: str, value) -> None:
    Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/$defs/{definition}",
            "$defs": CONTRACT_BUNDLE["$defs"],
        }
    ).validate(value)


@pytest.fixture()
def api_runtime(tmp_path):
    database_path = tmp_path / "assessment-api.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    db = session_factory()
    try:
        with db.begin():
            user = User(
                username=f"assessment-api-{uuid.uuid4().hex}",
                hashed_password="not-used",
                role="user",
                role_version=1,
                quota=10,
                quota_reserved=0,
                is_active=True,
                must_change_password=False,
            )
            db.add(user)
            db.flush()
            list(user.role_assignments)
    finally:
        db.close()

    app = FastAPI()
    app.include_router(assessments_api.router, prefix="/api/v1")
    app.include_router(assessment_events_api.router, prefix="/api/v1")
    app.include_router(assessment_reports_api.router, prefix="/api/v1")
    app.state.active_user = {"value": user}

    def _override_user():
        return app.state.active_user["value"]

    def _override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    old_flag = settings.feature_bid_assessment_v1_runtime
    old_events_factory = assessment_events_api.SessionLocal
    old_phase3_flag = settings.feature_bid_assessment_phase3_run_bootstrap
    old_phase3_lifecycle_flag = settings.feature_bid_assessment_phase3_run_lifecycle
    old_upload_settings = {
        "bid_upload_batch_ttl_days": settings.bid_upload_batch_ttl_days,
        "bid_upload_max_files": settings.bid_upload_max_files,
        "bid_upload_max_file_bytes": settings.bid_upload_max_file_bytes,
        "bid_upload_max_batch_bytes": settings.bid_upload_max_batch_bytes,
        "bid_upload_accepted_extensions": settings.bid_upload_accepted_extensions,
        "bid_upload_read_chunk_bytes": settings.bid_upload_read_chunk_bytes,
        "bid_upload_minio_part_size_bytes": settings.bid_upload_minio_part_size_bytes,
        "bid_upload_processing_timeout_seconds": settings.bid_upload_processing_timeout_seconds,
        "bid_upload_object_prefix": settings.bid_upload_object_prefix,
        "bid_upload_orphan_grace_seconds": settings.bid_upload_orphan_grace_seconds,
    }
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)
    assessment_events_api.SessionLocal = session_factory
    object.__setattr__(settings, "feature_bid_assessment_phase3_run_bootstrap", True)
    object.__setattr__(settings, "feature_bid_assessment_phase3_run_lifecycle", True)
    object.__setattr__(settings, "bid_upload_batch_ttl_days", 7)
    object.__setattr__(settings, "bid_upload_max_files", 100)
    object.__setattr__(settings, "bid_upload_max_file_bytes", 209715200)
    object.__setattr__(settings, "bid_upload_max_batch_bytes", 1073741824)
    object.__setattr__(
        settings,
        "bid_upload_accepted_extensions",
        ["pdf", "docx", "xlsx", "xlsm", "png", "jpg", "jpeg", "txt", "md"],
    )
    object.__setattr__(settings, "bid_upload_read_chunk_bytes", 65536)
    object.__setattr__(settings, "bid_upload_minio_part_size_bytes", 5242880)
    object.__setattr__(settings, "bid_upload_processing_timeout_seconds", 3600)
    object.__setattr__(settings, "bid_upload_object_prefix", "bid-assessment/uploading/v1")
    object.__setattr__(settings, "bid_upload_orphan_grace_seconds", 86400)
    try:
        with TestClient(app) as client:
            yield client, session_factory, user
    finally:
        assessment_events_api.SessionLocal = old_events_factory
        object.__setattr__(settings, "feature_bid_assessment_v1_runtime", old_flag)
        object.__setattr__(
            settings,
            "feature_bid_assessment_phase3_run_bootstrap",
            old_phase3_flag,
        )
        object.__setattr__(
            settings,
            "feature_bid_assessment_phase3_run_lifecycle",
            old_phase3_lifecycle_flag,
        )
        for name, value in old_upload_settings.items():
            object.__setattr__(settings, name, value)
        engine.dispose()


def _payload(**overrides):
    payload = {
        "title": "某办公楼装饰项目投标研判",
        "client_name": "某甲方",
        "internal_note": "内部跟进",
        "external_ref": f"crm-{uuid.uuid4().hex}",
    }
    payload.update(overrides)
    return payload


def _key() -> str:
    return f"idem-{uuid.uuid4()}"


class _FakeBidUploadStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.modified: dict[str, datetime] = {}
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.open_read_calls: list[str] = []
        self.opened_streams: list[BytesIO] = []
        self.fail_put = False
        self.fail_delete = False
        self.fail_open_read = False

    def put(
        self,
        *,
        stream,
        object_key: str,
        size_bytes: int,
        mime_type: str,
    ) -> StoredBidUploadObject:
        self.put_calls.append(object_key)
        if self.fail_put:
            raise RuntimeError("forced object put failure")
        content = bytearray()
        while True:
            chunk = stream.read(32768)
            if not chunk:
                break
            content.extend(chunk)
        assert len(content) == size_bytes
        self.objects[object_key] = bytes(content)
        self.modified[object_key] = datetime.now(timezone.utc)
        return StoredBidUploadObject(
            object_key=object_key,
            size_bytes=size_bytes,
            mime_type=mime_type,
            storage_etag=f"etag-{uuid.uuid4().hex}",
        )

    def delete(self, *, object_key: str) -> None:
        self.delete_calls.append(object_key)
        if self.fail_delete:
            raise RuntimeError("forced object delete failure")
        self.objects.pop(object_key, None)
        self.modified.pop(object_key, None)

    def open_read(self, *, object_key: str):
        self.open_read_calls.append(object_key)
        if self.fail_open_read or object_key not in self.objects:
            raise RuntimeError("forced object read failure")
        stream = BytesIO(self.objects[object_key])
        self.opened_streams.append(stream)
        return stream

    def list_candidates(
        self,
        *,
        prefix: str,
        limit: int,
    ) -> list[BidUploadObjectCandidate]:
        return [
            BidUploadObjectCandidate(
                object_key=object_key,
                last_modified=self.modified[object_key],
            )
            for object_key in sorted(self.objects)
            if object_key.startswith(prefix.rstrip("/") + "/")
        ][:limit]


def _pdf_bytes(label: str = "api12") -> bytes:
    return (
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        + label.encode("utf-8")
        + b"\n%%EOF\n"
    )


def _upload_file(
    client: TestClient,
    *,
    batch_location: str,
    storage: _FakeBidUploadStorage,
    monkeypatch,
    content: bytes | None = None,
    filename: str = "招标文件.pdf",
    content_type: str = "application/pdf",
    client_file_id: str | None = None,
    idempotency_key: str | None = None,
    operation: str = "add",
    replace_document_id: str | None = None,
    relative_path: str | None = "招标资料/招标文件.pdf",
    expected_sha256: str | None = None,
):
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: storage,
    )
    data = {
        "client_file_id": client_file_id or f"client-{uuid.uuid4().hex}",
        "operation": operation,
    }
    if replace_document_id is not None:
        data["replace_document_id"] = replace_document_id
    if relative_path is not None:
        data["relative_path"] = relative_path
    headers = {"Idempotency-Key": idempotency_key or _key()}
    if expected_sha256 is not None:
        headers["X-Content-SHA256"] = expected_sha256
    return client.post(
        f"{batch_location}/files",
        headers=headers,
        data=data,
        files={
            "file": (
                filename,
                content if content is not None else _pdf_bytes(),
                content_type,
            )
        },
    )


def _delete_upload_file(
    client: TestClient,
    *,
    batch_location: str,
    file_id: str,
    file_etag: str,
    storage: _FakeBidUploadStorage,
    monkeypatch,
    idempotency_key: str | None = None,
):
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: storage,
    )
    return client.delete(
        f"{batch_location}/files/{file_id}",
        headers={
            "Idempotency-Key": idempotency_key or _key(),
            "If-Match": file_etag,
        },
    )


def _create_user(session_factory, *, role: str = "user") -> User:
    db = session_factory()
    try:
        with db.begin():
            user = User(
                username=f"assessment-api-{role}-{uuid.uuid4().hex}",
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


def _counts(session_factory) -> dict[str, int]:
    db = session_factory()
    try:
        return {
            "assessment": db.query(BidAssessment).count(),
            "manifest": db.query(BidDocumentManifest).count(),
            "run": db.query(BidAnalysisRun).count(),
            "outbox": db.query(BidOutboxEvent).count(),
            "audit": db.query(BidAuditLog).count(),
            "idempotency": db.query(BidIdempotencyRecord).count(),
        }
    finally:
        db.close()


def _upload_counts(session_factory) -> dict[str, int]:
    db = session_factory()
    try:
        return {
            "batch": db.query(BidUploadBatch).count(),
            "outbox": db.query(BidOutboxEvent).count(),
            "audit": db.query(BidAuditLog).count(),
            "idempotency": db.query(BidIdempotencyRecord).count(),
            "public_event": db.query(BidPublicEvent).count(),
            "processed_event": db.query(BidProcessedEvent).count(),
        }
    finally:
        db.close()


def _upload_file_counts(session_factory) -> dict[str, int]:
    db = session_factory()
    try:
        return {
            "batch": db.query(BidUploadBatch).count(),
            "batch_file": db.query(BidUploadBatchFile).count(),
            "file_object": db.query(BidFileObject).count(),
            "outbox": db.query(BidOutboxEvent).count(),
            "audit": db.query(BidAuditLog).count(),
            "idempotency": db.query(BidIdempotencyRecord).count(),
            "public_event": db.query(BidPublicEvent).count(),
            "processed_event": db.query(BidProcessedEvent).count(),
        }
    finally:
        db.close()


def _deactivation_counts(session_factory) -> dict[str, int]:
    db = session_factory()
    try:
        return {
            "deactivation": db.query(BidUploadBatchDeactivation).count(),
            "document": db.query(BidDocument).count(),
            "document_version": db.query(BidDocumentVersion).count(),
            "manifest": db.query(BidDocumentManifest).count(),
            "manifest_document": db.query(BidManifestDocument).count(),
            "file_object": db.query(BidFileObject).count(),
            "outbox": db.query(BidOutboxEvent).count(),
            "audit": db.query(BidAuditLog).count(),
            "idempotency": db.query(BidIdempotencyRecord).count(),
        }
    finally:
        db.close()


def _create_assessment(client: TestClient):
    response = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(),
    )
    assert response.status_code == 201
    return response


def _create_initial_upload_batch(
    client: TestClient,
    *,
    assessment_id: str,
):
    assessment = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert assessment.status_code == 200
    response = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": assessment.headers["etag"],
        },
        json={"purpose": "initial", "base_manifest_id": None},
    )
    assert response.status_code == 201
    return response


def _attach_current_manifest(
    session_factory,
    *,
    assessment_id: str,
    actor_id: int,
) -> str:
    manifest_id = str(uuid.uuid4())
    db = session_factory()
    try:
        with db.begin():
            assessment = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == assessment_id)
                .one()
            )
            current_version = (
                db.query(func.max(BidDocumentManifest.version))
                .filter(BidDocumentManifest.assessment_id == assessment_id)
                .scalar()
            )
            db.add(
                BidDocumentManifest(
                    id=manifest_id,
                    assessment_id=assessment_id,
                    version=(
                        int(current_version) + 1
                        if current_version is not None
                        else 1
                    ),
                    manifest_hash=manifest_id.replace("-", "") * 2,
                    committed_by=actor_id,
                )
            )
            db.flush()
            assessment.current_manifest_id = manifest_id
            assessment.business_status = "preliminary_ready"
            assessment.row_version = int(assessment.row_version) + 1
        return manifest_id
    finally:
        db.close()


def _attach_manifest_document(
    session_factory,
    *,
    manifest_id: str,
    actor_id: int,
) -> str:
    document_id = str(uuid.uuid4())
    file_object_id = str(uuid.uuid4())
    document_version_id = str(uuid.uuid4())
    db = session_factory()
    try:
        with db.begin():
            current_order = (
                db.query(func.max(BidManifestDocument.order_no))
                .filter(BidManifestDocument.manifest_id == manifest_id)
                .scalar()
            )
            order_no = int(current_order) + 1 if current_order is not None else 0
            db.add(
                BidFileObject(
                    id=file_object_id,
                    sha256=file_object_id.replace("-", "") * 2,
                    object_key=f"bid-assessment/content/v1/{file_object_id}",
                    size_bytes=16,
                    mime_type="application/pdf",
                    storage_status="available",
                    storage_etag="existing-etag",
                    created_by=actor_id,
                    row_version=1,
                )
            )
            db.add(
                BidDocument(
                    id=document_id,
                    logical_identity_key=f"document-{document_id}",
                    logical_name="原招标文件",
                    document_type="tender_document",
                    created_by=actor_id,
                )
            )
            db.flush()
            db.add(
                BidDocumentVersion(
                    id=document_version_id,
                    document_id=document_id,
                    file_object_id=file_object_id,
                    version_no=1,
                    original_filename="原招标文件.pdf",
                    parser_hint="pdf",
                    source_metadata_hash="d" * 64,
                    source_metadata_json={},
                    created_by=actor_id,
                )
            )
            db.flush()
            db.add(
                BidManifestDocument(
                    manifest_id=manifest_id,
                    document_version_id=document_version_id,
                    role="tender_document",
                    order_no=order_no,
                )
            )
        return document_id
    finally:
        db.close()


def _attach_active_run(
    session_factory,
    *,
    assessment_id: str,
    manifest_id: str,
    actor_id: int,
) -> str:
    run_id = str(uuid.uuid4())
    marker = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            scope = BidAssessmentScope(
                id=str(uuid.uuid4()),
                assessment_id=assessment_id,
                version=1,
                scope_type="lot",
                source_lot_candidate_id=None,
                selected_lot_snapshot_json={
                    "lot_id": f"lot-{marker}",
                    "lot_name": "测试标段",
                },
                scope_hash=canonical_hash({"marker": marker, "type": "scope"}),
                created_by=actor_id,
            )
            enterprise = BidEnterpriseSnapshot(
                id=str(uuid.uuid4()),
                version=f"enterprise-{marker}",
                as_of=now,
                snapshot_hash=None,
                source_catalog_version="test-v1",
                status="building",
                error_code=None,
                created_by=actor_id,
                frozen_by=None,
                frozen_at=None,
                row_version=1,
            )
            rule_set = BidRuleSet(
                id=str(uuid.uuid4()),
                version=f"rules-{marker}",
                status="draft",
                active_slot_key=None,
                artifact_ref=f"memory://rules/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "rules"}),
                authored_by=actor_id,
                test_cases_ref=f"memory://rules/{marker}/tests",
                row_version=1,
            )
            fact_catalog = BidFactCatalogVersion(
                id=str(uuid.uuid4()),
                version=f"facts-{marker}",
                status="draft",
                active_slot_key=None,
                artifact_ref=f"memory://facts/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "facts"}),
                schema_version="v1",
                authored_by=actor_id,
                row_version=1,
            )
            prompt_bundle = BidPromptBundle(
                id=str(uuid.uuid4()),
                version=f"prompts-{marker}",
                status="draft",
                active_slot_key=None,
                artifact_ref=f"memory://prompts/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "prompts"}),
                bundle_schema_version="v1",
                authored_by=actor_id,
                row_version=1,
            )
            tool_registry = BidToolRegistryVersion(
                id=str(uuid.uuid4()),
                version=f"tools-{marker}",
                status="draft",
                active_slot_key=None,
                artifact_ref=f"memory://tools/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "tools"}),
                registry_schema_version="v1",
                authored_by=actor_id,
                row_version=1,
            )
            model_profile = BidModelProfileVersion(
                id=str(uuid.uuid4()),
                version=f"models-{marker}",
                status="draft",
                active_slot_key=None,
                artifact_ref=f"memory://models/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "models"}),
                role_routing_json={},
                provider_identifiers_json={},
                model_identifiers_json={},
                authored_by=actor_id,
                row_version=1,
            )
            formula_catalog = BidFormulaCatalogVersion(
                id=str(uuid.uuid4()),
                version=f"formulas-{marker}",
                status="draft",
                active_slot_key=None,
                artifact_ref=f"memory://formulas/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "formulas"}),
                rounding_policy_json={},
                authored_by=actor_id,
                row_version=1,
            )
            db.add_all(
                [
                    scope,
                    enterprise,
                    rule_set,
                    fact_catalog,
                    prompt_bundle,
                    tool_registry,
                    model_profile,
                    formula_catalog,
                ]
            )
            db.flush()
            run = BidAnalysisRun(
                id=run_id,
                assessment_id=assessment_id,
                scope_id=scope.id,
                manifest_id=manifest_id,
                enterprise_snapshot_id=enterprise.id,
                rule_set_id=rule_set.id,
                fact_catalog_version_id=fact_catalog.id,
                prompt_bundle_id=prompt_bundle.id,
                tool_registry_version_id=tool_registry.id,
                model_profile_version_id=model_profile.id,
                formula_catalog_version_id=formula_catalog.id,
                restart_of_run_id=None,
                run_sequence=1,
                run_kind="preliminary",
                status="running",
                retryable=False,
                input_fingerprint=canonical_hash({"marker": marker, "type": "fingerprint"}),
                input_hash=canonical_hash({"marker": marker, "type": "input"}),
                evaluation_time=now,
                current_stage="extract",
                waiting_reason=None,
                row_version=1,
            )
            db.add(run)
            db.flush()
            assessment = db.query(BidAssessment).filter(BidAssessment.id == assessment_id).one()
            assessment.active_run_id = run.id
            assessment.business_status = "preliminary_analyzing"
            assessment.row_version = int(assessment.row_version) + 1
        return run_id
    finally:
        db.close()


def _create_change_upload_batch(
    client: TestClient,
    *,
    assessment_id: str,
    manifest_id: str,
):
    assessment = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert assessment.status_code == 200
    response = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": assessment.headers["etag"],
        },
        json={"purpose": "change", "base_manifest_id": manifest_id},
    )
    assert response.status_code == 201
    return response


def _add_deactivations(
    client: TestClient,
    *,
    batch_location: str,
    batch_etag: str,
    document_ids: list[str],
    reason: str = "补遗已明确附件不再适用",
    idempotency_key: str | None = None,
):
    return client.post(
        f"{batch_location}/deactivations",
        headers={
            "Idempotency-Key": idempotency_key or _key(),
            "If-Match": batch_etag,
        },
        json={"document_ids": document_ids, "reason": reason},
    )


def _commit_upload_batch(
    client: TestClient,
    *,
    batch_location: str,
    batch_etag: str,
    expected_file_count: int,
    expected_deactivation_count: int = 0,
    change_note: str | None = "提交资料",
    idempotency_key: str | None = None,
):
    return client.post(
        f"{batch_location}/commit",
        headers={
            "Idempotency-Key": idempotency_key or _key(),
            "If-Match": batch_etag,
        },
        json={
            "expected_file_count": expected_file_count,
            "expected_deactivation_count": expected_deactivation_count,
            "change_note": change_note,
            "confirm_start_analysis": True,
        },
    )


def _abandon_upload_batch(
    client: TestClient,
    *,
    batch_location: str,
    batch_etag: str,
    reason: str = "用户重新整理资料",
    idempotency_key: str | None = None,
):
    return client.post(
        f"{batch_location}/abandon",
        headers={
            "Idempotency-Key": idempotency_key or _key(),
            "If-Match": batch_etag,
        },
        json={"reason": reason},
    )


def _change_batch_with_documents(
    client: TestClient,
    session_factory,
    *,
    user: User,
    document_count: int = 2,
):
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=user.id,
    )
    document_ids = [
        _attach_manifest_document(
            session_factory,
            manifest_id=manifest_id,
            actor_id=user.id,
        )
        for _ in range(document_count)
    ]
    batch = _create_change_upload_batch(
        client,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
    )
    return assessment_id, manifest_id, document_ids, batch


def test_api01_creates_complete_atomic_request_closure(api_runtime) -> None:
    client, session_factory, user = api_runtime
    payload = _payload()
    response = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=payload,
    )

    assert response.status_code == 201
    _validate_contract("AssessmentResponse", response.json())
    assert response.json()["code"] == 200
    assert response.json()["message"] == "ok"
    assert response.json()["error"] is None
    assert response.json()["request_id"].startswith("req_")
    snapshot = response.json()["data"]
    assert snapshot["title"] == payload["title"]
    assert snapshot["client_name"] == payload["client_name"]
    assert snapshot["internal_note"] == payload["internal_note"]
    assert snapshot["lifecycle_status"] == "active"
    assert snapshot["business_status"] == "awaiting_files"
    assert snapshot["row_version"] == 1
    assert snapshot["scope"] is None
    assert snapshot["current_manifest"] is None
    assert snapshot["active_run"] is None
    assert snapshot["latest_reports"] == {"preliminary": None, "deep": None}
    assert snapshot["recommended_view"] == "documents"
    assert snapshot["primary_action"] == "upload_batch.create"
    assert {item["code"] for item in snapshot["allowed_actions"]} == {
        "upload_batch.create",
        "assessment.edit_metadata",
        "assessment.abandon_draft",
    }
    assert response.headers["location"] == (
        f"/api/v1/bid-assessments/{snapshot['assessment_id']}"
    )
    assert response.headers["etag"] == (
        f'"bid-assessment:{snapshot["assessment_id"]}:1"'
    )
    assert response.headers["x-resource-version"] == "1"
    assert response.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in response.headers

    assert _counts(session_factory) == {
        "assessment": 1,
        "manifest": 0,
        "run": 0,
        "outbox": 1,
        "audit": 1,
        "idempotency": 1,
    }
    db = session_factory()
    try:
        assessment = db.query(BidAssessment).one()
        outbox = db.query(BidOutboxEvent).one()
        audit = db.query(BidAuditLog).one()
        idempotency = db.query(BidIdempotencyRecord).one()
        assert assessment.id == snapshot["assessment_id"]
        assert assessment.external_ref == payload["external_ref"]
        assert assessment.created_by == assessment.updated_by == user.id
        assert outbox.event_type == "bid.assessment.created.v1"
        assert outbox.status == "pending"
        assert outbox.payload_json == {"snapshot": snapshot}
        assert outbox.aggregate_id == assessment.id
        assert outbox.aggregate_version == 1
        assert audit.action == "assessment.create"
        assert audit.entity_id == assessment.id
        assert audit.correlation_id == outbox.event_id
        assert audit.after_hash == canonical_hash(snapshot)
        assert audit.actor_id == user.id
        assert idempotency.status == "completed"
        assert idempotency.response_status_code == 201
        assert idempotency.response_snapshot_json == response.json()
        assert idempotency.resource_type == "assessment"
        assert idempotency.resource_id == assessment.id
        assert idempotency.response_ref == response.headers["location"]
    finally:
        db.close()


def test_api01_same_key_same_request_replays_original_201_without_new_writes(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    payload = _payload()
    key = _key()
    first = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    replay = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": key},
        json=payload,
    )

    assert first.status_code == replay.status_code == 201
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == first.json()
    assert replay.headers["location"] == first.headers["location"]
    assert replay.headers["etag"] == first.headers["etag"]
    assert _counts(session_factory) == {
        "assessment": 1,
        "manifest": 0,
        "run": 0,
        "outbox": 1,
        "audit": 1,
        "idempotency": 1,
    }


def test_api01_same_key_different_request_and_processing_conflicts(api_runtime) -> None:
    client, session_factory, user = api_runtime
    payload = _payload()
    key = _key()
    created = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert created.status_code == 201

    reused = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": key},
        json={**payload, "title": "另一业务意图"},
    )
    assert reused.status_code == 409
    _validate_contract("ErrorEnvelope", reused.json())
    assert reused.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    assert reused.json()["error"]["retryable"] is False

    processing_payload = _payload()
    processing_key = _key()
    normalized = {
        "title": processing_payload["title"],
        "client_name": processing_payload["client_name"],
        "internal_note": processing_payload["internal_note"],
        "external_ref": processing_payload["external_ref"],
    }
    db = session_factory()
    try:
        with db.begin():
            begin_idempotent_request(
                db,
                actor_id=user.id,
                http_method="POST",
                route_template="/api/v1/bid-assessments",
                idempotency_key=processing_key,
                request_payload=normalized,
                request_id=f"req_{uuid.uuid4().hex}",
            )
    finally:
        db.close()

    in_progress = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": processing_key},
        json=processing_payload,
    )
    assert in_progress.status_code == 409
    _validate_contract("ErrorEnvelope", in_progress.json())
    assert in_progress.headers["retry-after"] == "2"
    assert in_progress.json()["error"]["error_code"] == "BID_IDEMPOTENCY_IN_PROGRESS"
    assert in_progress.json()["error"]["retryable"] is True
    assert _counts(session_factory)["assessment"] == 1


def test_api01_external_ref_conflict_does_not_leave_partial_idempotency(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    external_ref = f"crm-{uuid.uuid4().hex}"
    first = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(external_ref=external_ref),
    )
    conflict = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(external_ref=external_ref),
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["error_code"] == "BID_ASSESSMENT_STATE_CONFLICT"
    assert _counts(session_factory) == {
        "assessment": 1,
        "manifest": 0,
        "run": 0,
        "outbox": 1,
        "audit": 1,
        "idempotency": 1,
    }


def test_api01_audit_failure_rolls_back_assessment_outbox_and_idempotency(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime

    def _fail_audit(*_args, **_kwargs):
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(assessment_commands, "append_audit_log", _fail_audit)
    failed = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(),
    )

    assert failed.status_code == 503
    assert failed.json()["error"]["error_code"] == "BID_STORAGE_UNAVAILABLE"
    assert _counts(session_factory) == {
        "assessment": 0,
        "manifest": 0,
        "run": 0,
        "outbox": 0,
        "audit": 0,
        "idempotency": 0,
    }


def test_api01_validation_and_feature_gate_fail_before_writes(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    unknown = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json={**_payload(), "known_lot": {"lot_code": "01"}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["error_code"] == "BID_REQUEST_VALIDATION_FAILED"
    assert any(row["field"] == "known_lot" for row in unknown.json()["error"]["field_errors"])

    missing_key = client.post("/api/v1/bid-assessments", json=_payload())
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["error_code"] == "BID_REQUEST_VALIDATION_FAILED"

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(),
    )
    assert disabled.status_code == 404
    assert _counts(session_factory) == {
        "assessment": 0,
        "manifest": 0,
        "run": 0,
        "outbox": 0,
        "audit": 0,
        "idempotency": 0,
    }


def test_api03_returns_authoritative_snapshot_and_supports_conditional_get(
    api_runtime,
) -> None:
    client, session_factory, _user = api_runtime
    created = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(),
    )
    assert created.status_code == 201
    assessment_id = created.json()["data"]["assessment_id"]
    before = _counts(session_factory)

    fetched = client.get(f"/api/v1/bid-assessments/{assessment_id}")

    assert fetched.status_code == 200
    _validate_contract("AssessmentResponse", fetched.json())
    assert fetched.json()["code"] == 200
    assert fetched.json()["message"] == "ok"
    assert fetched.json()["error"] is None
    assert fetched.json()["data"] == created.json()["data"]
    assert fetched.headers["etag"] == created.headers["etag"]
    assert fetched.headers["x-resource-version"] == "1"
    assert fetched.headers["cache-control"] == "private, no-store"

    current_etag = fetched.headers["etag"]
    unchanged = client.get(
        f"/api/v1/bid-assessments/{assessment_id}",
        headers={"If-None-Match": current_etag},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == current_etag
    assert unchanged.headers["x-resource-version"] == "1"
    assert unchanged.headers["cache-control"] == "private, no-store"

    weak_or_listed = client.get(
        f"/api/v1/bid-assessments/{assessment_id}",
        headers={"If-None-Match": f'"stale", W/{current_etag}'},
    )
    assert weak_or_listed.status_code == 304
    assert weak_or_listed.content == b""
    assert _counts(session_factory) == before


def test_api03_stale_etag_recovers_latest_persisted_version(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    created = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(),
    )
    assessment_id = created.json()["data"]["assessment_id"]
    old_etag = created.headers["etag"]

    db = session_factory()
    try:
        with db.begin():
            assessment = db.query(BidAssessment).filter(BidAssessment.id == assessment_id).one()
            assessment.title = "冲突恢复后的权威标题"
            assessment.internal_note = "来自数据库的最新快照"
            assessment.row_version = 2
    finally:
        db.close()

    recovered = client.get(
        f"/api/v1/bid-assessments/{assessment_id}",
        headers={"If-None-Match": old_etag},
    )

    assert recovered.status_code == 200
    _validate_contract("AssessmentResponse", recovered.json())
    assert recovered.json()["data"]["title"] == "冲突恢复后的权威标题"
    assert recovered.json()["data"]["internal_note"] == "来自数据库的最新快照"
    assert recovered.json()["data"]["row_version"] == 2
    assert recovered.headers["etag"] == f'"bid-assessment:{assessment_id}:2"'
    assert recovered.headers["x-resource-version"] == "2"

    now_current = client.get(
        f"/api/v1/bid-assessments/{assessment_id}",
        headers={"If-None-Match": recovered.headers["etag"]},
    )
    assert now_current.status_code == 304
    assert now_current.content == b""


def test_api03_hides_unauthorized_resources_but_allows_admin(api_runtime) -> None:
    client, session_factory, _owner = api_runtime
    created = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(),
    )
    assessment_id = created.json()["data"]["assessment_id"]

    missing = client.get(f"/api/v1/bid-assessments/{uuid.uuid4()}")
    assert missing.status_code == 404
    _validate_contract("ErrorEnvelope", missing.json())
    assert missing.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"

    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert hidden.status_code == 404
    _validate_contract("ErrorEnvelope", hidden.json())
    assert hidden.json()["message"] == missing.json()["message"]
    assert hidden.json()["error"] == missing.json()["error"]

    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    visible = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert visible.status_code == 200
    assert visible.json()["data"]["assessment_id"] == assessment_id


def test_api03_and_api10_feature_gate_fail_closed_with_frozen_error_envelope(
    api_runtime,
) -> None:
    client, session_factory, _user = api_runtime
    created = client.post(
        "/api/v1/bid-assessments",
        headers={"Idempotency-Key": _key()},
        json=_payload(),
    )
    assessment_id = created.json()["data"]["assessment_id"]
    before = _counts(session_factory)

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = client.get(f"/api/v1/bid-assessments/{assessment_id}")

    assert disabled.status_code == 404
    _validate_contract("ErrorEnvelope", disabled.json())
    assert disabled.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    disabled_batch = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": created.headers["etag"],
        },
        json={"purpose": "initial", "base_manifest_id": None},
    )
    assert disabled_batch.status_code == 404
    _validate_contract("ErrorEnvelope", disabled_batch.json())
    assert disabled_batch.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    assert _counts(session_factory) == before


def test_api10_creates_atomic_initial_upload_batch_closure(api_runtime) -> None:
    client, session_factory, user = api_runtime
    assessment_response = _create_assessment(client)
    assessment_id = assessment_response.json()["data"]["assessment_id"]
    authoritative = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assessment_etag = authoritative.headers["etag"]

    response = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": assessment_etag,
        },
        json={"purpose": "initial", "base_manifest_id": None},
    )

    assert response.status_code == 201
    _validate_contract("UploadBatchResponse", response.json())
    assert response.json()["code"] == 200
    assert response.json()["message"] == "ok"
    assert response.json()["error"] is None
    snapshot = response.json()["data"]
    assert snapshot["assessment_id"] == assessment_id
    assert snapshot["purpose"] == "initial"
    assert snapshot["status"] == "draft"
    assert snapshot["base_manifest_id"] is None
    assert snapshot["row_version"] == 1
    assert snapshot["files"] == []
    assert snapshot["deactivations"] == []
    assert snapshot["validation"] == {
        "can_commit": False,
        "blocking_errors": ["尚未上传文件"],
        "warnings": [],
    }
    assert snapshot["limits"] == {
        "max_files": 100,
        "max_file_bytes": 209715200,
        "max_batch_bytes": 1073741824,
        "accepted_extensions": [
            "pdf",
            "docx",
            "xlsx",
            "xlsm",
            "png",
            "jpg",
            "jpeg",
            "txt",
            "md",
        ],
    }
    assert snapshot["expires_at"].endswith("Z")
    assert response.headers["location"] == (
        f"/api/v1/bid-upload-batches/{snapshot['batch_id']}"
    )
    assert response.headers["etag"].startswith(
        f'"bid-upload-batch:{snapshot["batch_id"]}:1:'
    )
    assert response.headers["etag"].endswith('"')
    assert response.headers["x-resource-version"] == "1"
    assert response.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in response.headers

    assert _upload_counts(session_factory) == {
        "batch": 1,
        "outbox": 2,
        "audit": 2,
        "idempotency": 2,
        "public_event": 0,
        "processed_event": 0,
    }
    db = session_factory()
    try:
        assessment = db.query(BidAssessment).filter(BidAssessment.id == assessment_id).one()
        batch = db.query(BidUploadBatch).one()
        outbox = (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.event_type == "bid.upload_batch.created.v1")
            .one()
        )
        audit = (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_batch.create")
            .one()
        )
        idempotency = (
            db.query(BidIdempotencyRecord)
            .filter(BidIdempotencyRecord.resource_type == "upload_batch")
            .one()
        )
        assert assessment.row_version == 1
        assert assessment.current_manifest_id is None
        assert batch.id == snapshot["batch_id"]
        assert batch.open_slot_key == "initial"
        assert batch.created_by == batch.updated_by == user.id
        assert outbox.aggregate_type == "upload_batch"
        assert outbox.aggregate_id == batch.id
        assert outbox.aggregate_version == 1
        assert outbox.assessment_id == assessment_id
        assert outbox.payload_json == {
            "batch_id": batch.id,
            "status": "draft",
            "ready_count": 0,
            "failed_count": 0,
            "resource_version": 1,
        }
        assert audit.entity_type == "upload_batch"
        assert audit.entity_id == batch.id
        assert audit.assessment_id == assessment_id
        assert audit.correlation_id == outbox.event_id
        assert audit.after_hash == canonical_hash(snapshot)
        assert idempotency.status == "completed"
        assert idempotency.response_status_code == 201
        assert idempotency.response_snapshot_json == response.json()
        assert idempotency.resource_id == batch.id
        assert idempotency.response_ref == response.headers["location"]
    finally:
        db.close()


def test_api10_same_key_replays_original_201_without_duplicate_writes(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    etag = client.get(f"/api/v1/bid-assessments/{assessment_id}").headers["etag"]
    key = _key()
    headers = {"Idempotency-Key": key, "If-Match": etag}
    body = {"purpose": "initial", "base_manifest_id": None}

    first = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers=headers,
        json=body,
    )
    object.__setattr__(settings, "bid_upload_max_files", 12)
    replay = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers=headers,
        json=body,
    )

    assert first.status_code == replay.status_code == 201
    assert first.json()["data"]["limits"]["max_files"] == 100
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.headers["location"] == first.headers["location"]
    assert replay.headers["etag"] == first.headers["etag"]
    assert _upload_counts(session_factory) == {
        "batch": 1,
        "outbox": 2,
        "audit": 2,
        "idempotency": 2,
        "public_event": 0,
        "processed_event": 0,
    }


def test_api10_idempotency_scope_includes_assessment_and_precondition(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    first_assessment = _create_assessment(client)
    second_assessment = _create_assessment(client)
    first_id = first_assessment.json()["data"]["assessment_id"]
    second_id = second_assessment.json()["data"]["assessment_id"]
    first_etag = client.get(f"/api/v1/bid-assessments/{first_id}").headers["etag"]
    second_etag = client.get(f"/api/v1/bid-assessments/{second_id}").headers["etag"]
    key = _key()

    created = client.post(
        f"/api/v1/bid-assessments/{first_id}/upload-batches",
        headers={"Idempotency-Key": key, "If-Match": first_etag},
        json={"purpose": "initial", "base_manifest_id": None},
    )
    reused = client.post(
        f"/api/v1/bid-assessments/{second_id}/upload-batches",
        headers={"Idempotency-Key": key, "If-Match": second_etag},
        json={"purpose": "initial", "base_manifest_id": None},
    )

    assert created.status_code == 201
    assert reused.status_code == 409
    _validate_contract("ErrorEnvelope", reused.json())
    assert reused.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    db = session_factory()
    try:
        assert db.query(BidUploadBatch).count() == 1
        assert db.query(BidUploadBatch).one().assessment_id == first_id
    finally:
        db.close()


def test_api10_requires_current_single_strong_assessment_etag(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    current_etag = client.get(
        f"/api/v1/bid-assessments/{assessment_id}"
    ).headers["etag"]
    body = {"purpose": "initial", "base_manifest_id": None}

    missing = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key()},
        json=body,
    )
    assert missing.status_code == 428
    _validate_contract("ErrorEnvelope", missing.json())
    assert missing.json()["error"]["error_code"] == "BID_PRECONDITION_REQUIRED"

    weak = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": f"W/{current_etag}"},
        json=body,
    )
    assert weak.status_code == 400
    assert weak.json()["error"]["error_code"] == "BID_REQUEST_MALFORMED"

    stale_etag = f'"bid-assessment:{assessment_id}:99"'
    stale = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": stale_etag},
        json=body,
    )
    assert stale.status_code == 412
    _validate_contract("ErrorEnvelope", stale.json())
    assert stale.json()["error"]["error_code"] == "BID_RESOURCE_VERSION_MISMATCH"
    assert stale.json()["error"]["details"] == {
        "provided_etag": stale_etag,
        "current_etag": current_etag,
        "current_resource_url": f"/api/v1/bid-assessments/{assessment_id}",
    }
    assert stale.headers["etag"] == current_etag
    assert stale.headers["x-resource-version"] == "1"
    assert _upload_counts(session_factory) == {
        "batch": 0,
        "outbox": 1,
        "audit": 1,
        "idempotency": 1,
        "public_event": 0,
        "processed_event": 0,
    }


def test_api10_rejects_second_open_batch_and_returns_recovery_target(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    etag = client.get(f"/api/v1/bid-assessments/{assessment_id}").headers["etag"]
    body = {"purpose": "initial", "base_manifest_id": None}

    first = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json=body,
    )
    conflict = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json=body,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    _validate_contract("ErrorEnvelope", conflict.json())
    assert conflict.json()["error"]["error_code"] == "BID_UPLOAD_BATCH_ALREADY_OPEN"
    expected_url = first.headers["location"]
    assert conflict.json()["error"]["details"]["resource_url"] == expected_url
    assert conflict.json()["error"]["recovery"] == {
        "action": "resume_upload_batch",
        "resource_url": expected_url,
    }
    assert _upload_counts(session_factory)["batch"] == 1
    assert _upload_counts(session_factory)["idempotency"] == 2


def test_api10_enforces_initial_and_change_manifest_baselines(api_runtime) -> None:
    client, session_factory, user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    first_etag = client.get(f"/api/v1/bid-assessments/{assessment_id}").headers["etag"]

    invalid_initial = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": first_etag},
        json={"purpose": "initial", "base_manifest_id": str(uuid.uuid4())},
    )
    invalid_change = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": first_etag},
        json={"purpose": "change", "base_manifest_id": None},
    )
    unknown_field = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": first_etag},
        json={
            "purpose": "initial",
            "base_manifest_id": None,
            "change_note": "not in the frozen request contract",
        },
    )
    assert invalid_initial.status_code == 422
    assert invalid_change.status_code == 422
    assert unknown_field.status_code == 422

    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=user.id,
    )
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert current.status_code == 200
    assert current.json()["data"]["current_manifest"]["manifest_id"] == manifest_id
    current_etag = current.headers["etag"]

    wrong_purpose = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": current_etag},
        json={"purpose": "initial", "base_manifest_id": None},
    )
    stale_base = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": current_etag},
        json={"purpose": "change", "base_manifest_id": str(uuid.uuid4())},
    )
    assert wrong_purpose.status_code == 409
    assert wrong_purpose.json()["error"]["error_code"] == "BID_BASE_MANIFEST_STALE"
    assert stale_base.status_code == 409
    assert stale_base.json()["error"]["error_code"] == "BID_BASE_MANIFEST_STALE"
    assert stale_base.json()["error"]["details"]["current_manifest_id"] == manifest_id

    created = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": current_etag},
        json={"purpose": "change", "base_manifest_id": manifest_id},
    )
    assert created.status_code == 201
    _validate_contract("UploadBatchResponse", created.json())
    assert created.json()["data"]["purpose"] == "change"
    assert created.json()["data"]["base_manifest_id"] == manifest_id
    assert _upload_counts(session_factory)["batch"] == 1
    assert _upload_counts(session_factory)["idempotency"] == 2


def test_api10_hides_unauthorized_assessment_but_allows_admin(api_runtime) -> None:
    client, session_factory, _owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    etag = client.get(f"/api/v1/bid-assessments/{assessment_id}").headers["etag"]
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider

    hidden = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json={"purpose": "initial", "base_manifest_id": None},
    )
    assert hidden.status_code == 404
    _validate_contract("ErrorEnvelope", hidden.json())
    assert hidden.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"

    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    created = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json={"purpose": "initial", "base_manifest_id": None},
    )
    assert created.status_code == 201
    db = session_factory()
    try:
        assert db.query(BidUploadBatch).one().created_by == admin.id
    finally:
        db.close()


def test_api10_audit_failure_rolls_back_batch_outbox_and_idempotency(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    etag = client.get(f"/api/v1/bid-assessments/{assessment_id}").headers["etag"]

    def _fail_audit(*_args, **_kwargs):
        raise RuntimeError("forced upload audit failure")

    monkeypatch.setattr(upload_commands, "append_audit_log", _fail_audit)
    failed = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json={"purpose": "initial", "base_manifest_id": None},
    )

    assert failed.status_code == 503
    assert failed.json()["error"]["error_code"] == "BID_STORAGE_UNAVAILABLE"
    assert _upload_counts(session_factory) == {
        "batch": 0,
        "outbox": 1,
        "audit": 1,
        "idempotency": 1,
        "public_event": 0,
        "processed_event": 0,
    }


def test_api10_created_outbox_projects_public_upload_batch_event(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    etag = client.get(f"/api/v1/bid-assessments/{assessment_id}").headers["etag"]
    created = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json={"purpose": "initial", "base_manifest_id": None},
    )
    assert created.status_code == 201

    db = session_factory()
    try:
        event_id = (
            db.query(BidOutboxEvent.event_id)
            .filter(BidOutboxEvent.event_type == "bid.upload_batch.created.v1")
            .scalar()
        )
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            result = project_outbox_event_to_public(db, event_id=event_id)
            assert result.duplicate is False
    finally:
        db.close()

    db = session_factory()
    try:
        public_event = (
            db.query(BidPublicEvent)
            .filter(BidPublicEvent.event_type == "upload_batch.changed")
            .one()
        )
        assert public_event.assessment_id == assessment_id
        assert public_event.resource_type == "upload_batch"
        assert public_event.resource_id == created.json()["data"]["batch_id"]
        assert public_event.resource_version == 1
        assert public_event.payload_json == {
            "batch_id": created.json()["data"]["batch_id"],
            "status": "draft",
            "ready_count": 0,
            "failed_count": 0,
        }
    finally:
        db.close()


def test_api11_returns_authoritative_snapshot_and_supports_conditional_get(
    api_runtime,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    created = _create_initial_upload_batch(
        client,
        assessment_id=assessment_id,
    )
    location = created.headers["location"]
    before = _upload_counts(session_factory)

    recovered = client.get(location)

    assert recovered.status_code == 200
    _validate_contract("UploadBatchResponse", recovered.json())
    assert recovered.json()["data"] == created.json()["data"]
    assert recovered.headers["etag"] == created.headers["etag"]
    assert recovered.headers["x-resource-version"] == "1"
    assert recovered.headers["cache-control"] == "private, no-store"
    assert "location" not in recovered.headers

    exact = client.get(location, headers={"If-None-Match": recovered.headers["etag"]})
    weak_list = client.get(
        location,
        headers={
            "If-None-Match": (
                f'"unrelated", W/{recovered.headers["etag"]}'
            )
        },
    )
    wildcard = client.get(location, headers={"If-None-Match": "*"})

    for not_modified in (exact, weak_list, wildcard):
        assert not_modified.status_code == 304
        assert not_modified.content == b""
        assert not_modified.headers["etag"] == recovered.headers["etag"]
        assert not_modified.headers["x-resource-version"] == "1"
        assert not_modified.headers["cache-control"] == "private, no-store"
    assert _upload_counts(session_factory) == before


def test_api11_stale_etag_recovers_parallel_file_progress(api_runtime) -> None:
    client, session_factory, user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    created = _create_initial_upload_batch(
        client,
        assessment_id=assessment_id,
    )
    location = created.headers["location"]
    batch_id = created.json()["data"]["batch_id"]
    stale_etag = created.headers["etag"]

    db = session_factory()
    try:
        with db.begin():
            batch = db.query(BidUploadBatch).filter(BidUploadBatch.id == batch_id).one()
            db.add(
                BidUploadBatchFile(
                    id=str(uuid.uuid4()),
                    batch_id=batch_id,
                    file_object_id=None,
                    replace_document_id=None,
                    client_file_id=f"client-{uuid.uuid4().hex}",
                    operation="add",
                    filename="招标文件.pdf",
                    relative_path=None,
                    size_bytes=1024,
                    mime_type="application/pdf",
                    sha256="b" * 64,
                    temporary_object_ref="tmp/api11-progress",
                    status="ready",
                    error_code=None,
                    row_version=1,
                )
            )
            batch.status = "ready"
            batch.row_version = 2
            batch.updated_by = user.id
    finally:
        db.close()

    reconciled = client.get(location, headers={"If-None-Match": stale_etag})

    assert reconciled.status_code == 200
    _validate_contract("UploadBatchResponse", reconciled.json())
    snapshot = reconciled.json()["data"]
    assert snapshot["batch_id"] == batch_id
    assert snapshot["status"] == "ready"
    assert snapshot["row_version"] == 2
    assert snapshot["files"] == [
        {
            "batch_file_id": snapshot["files"][0]["batch_file_id"],
            "client_file_id": snapshot["files"][0]["client_file_id"],
            "filename": "招标文件.pdf",
            "relative_path": None,
            "operation": "add",
            "replace_document_id": None,
            "size_bytes": 1024,
            "sha256": "b" * 64,
            "mime_type": "application/pdf",
                "status": "ready",
                "error_code": None,
                "row_version": 1,
                "etag": (
                    f'"bid-upload-file:'
                    f'{snapshot["files"][0]["batch_file_id"]}:1"'
                ),
            }
        ]
    assert snapshot["validation"] == {
        "can_commit": True,
        "blocking_errors": [],
        "warnings": [],
    }
    current_etag = reconciled.headers["etag"]
    assert current_etag != stale_etag
    assert reconciled.headers["x-resource-version"] == "2"

    current = client.get(location, headers={"If-None-Match": current_etag})
    assert current.status_code == 304
    assert current.content == b""


def test_api11_dynamic_limits_change_invalidates_etag(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    created = _create_initial_upload_batch(
        client,
        assessment_id=assessment_id,
    )
    before = _upload_counts(session_factory)
    old_etag = created.headers["etag"]

    object.__setattr__(settings, "bid_upload_max_files", 12)
    refreshed = client.get(
        created.headers["location"],
        headers={"If-None-Match": old_etag},
    )

    assert refreshed.status_code == 200
    _validate_contract("UploadBatchResponse", refreshed.json())
    assert refreshed.json()["data"]["limits"]["max_files"] == 12
    assert refreshed.json()["data"]["row_version"] == 1
    assert refreshed.headers["etag"] != old_etag
    assert refreshed.headers["x-resource-version"] == "1"
    assert _upload_counts(session_factory) == before


def test_api11_hides_unauthorized_batch_but_allows_admin(api_runtime) -> None:
    client, session_factory, _owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    created = _create_initial_upload_batch(
        client,
        assessment_id=assessment_id,
    )
    location = created.headers["location"]

    missing = client.get(f"/api/v1/bid-upload-batches/{uuid.uuid4()}")
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = client.get(location)

    assert missing.status_code == hidden.status_code == 404
    _validate_contract("ErrorEnvelope", missing.json())
    _validate_contract("ErrorEnvelope", hidden.json())
    assert hidden.json()["error"] == missing.json()["error"]

    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    visible = client.get(location)
    assert visible.status_code == 200
    _validate_contract("UploadBatchResponse", visible.json())
    assert visible.json()["data"]["batch_id"] == created.json()["data"]["batch_id"]


def test_api11_feature_gate_fails_closed_without_writes(api_runtime) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    created = _create_initial_upload_batch(
        client,
        assessment_id=assessment_id,
    )
    before = _upload_counts(session_factory)

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = client.get(created.headers["location"])

    assert disabled.status_code == 404
    _validate_contract("ErrorEnvelope", disabled.json())
    assert disabled.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    assert _upload_counts(session_factory) == before


def test_api12_streams_and_registers_one_file_as_an_atomic_batch_change(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    batch = _create_initial_upload_batch(client, assessment_id=assessment_id)
    batch_id = batch.json()["data"]["batch_id"]
    original_batch_etag = batch.headers["etag"]
    storage = _FakeBidUploadStorage()

    response = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )

    assert response.status_code == 201
    _validate_contract("UploadFileResponse", response.json())
    body = response.json()
    file_snapshot = body["data"]["file"]
    batch_summary = body["data"]["batch"]
    assert body["message"] == "文件已接收"
    assert file_snapshot["filename"] == "招标文件.pdf"
    assert file_snapshot["status"] == "ready"
    assert file_snapshot["row_version"] == 1
    assert file_snapshot["duplicate_of"] is None
    assert batch_summary == {
        "batch_id": batch_id,
        "row_version": 2,
        "can_commit": True,
    }
    assert response.headers["location"] == (
        f"/api/v1/bid-upload-batches/{batch_id}/files/"
        f"{file_snapshot['batch_file_id']}"
    )
    assert response.headers["etag"] == (
        f'"bid-upload-file:{file_snapshot["batch_file_id"]}:1"'
    )
    assert response.headers["x-resource-version"] == "1"
    assert response.headers["x-batch-resource-version"] == "2"
    assert response.headers["x-batch-etag"] != original_batch_etag
    assert response.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in response.headers

    assert len(storage.put_calls) == 1
    object_key = storage.put_calls[0]
    assert object_key.startswith("bid-assessment/uploading/v1/")
    assert f"/{batch_id}/" in object_key
    assert "招标文件" not in object_key
    assert "client-" not in object_key
    assert storage.objects[object_key] == _pdf_bytes()
    assert _upload_file_counts(session_factory) == {
        "batch": 1,
        "batch_file": 1,
        "file_object": 1,
        "outbox": 3,
        "audit": 3,
        "idempotency": 3,
        "public_event": 0,
        "processed_event": 0,
    }

    db = session_factory()
    try:
        persisted_batch = db.query(BidUploadBatch).filter(BidUploadBatch.id == batch_id).one()
        persisted_file = db.query(BidUploadBatchFile).one()
        file_object = db.query(BidFileObject).one()
        event = (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.event_type == "bid.upload_file.received.v1")
            .one()
        )
        audit = (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_file.receive")
            .one()
        )
        idempotency = (
            db.query(BidIdempotencyRecord)
            .filter(BidIdempotencyRecord.resource_type == "upload_file")
            .one()
        )
        assert persisted_batch.status == "ready"
        assert persisted_batch.row_version == 2
        assert persisted_batch.updated_by == user.id
        assert persisted_file.file_object_id == file_object.id
        assert persisted_file.temporary_object_ref == object_key
        assert file_object.object_key == object_key
        assert file_object.storage_status == "available"
        assert event.aggregate_id == batch_id
        assert event.aggregate_version == 2
        assert event.payload_json == {
            "batch_id": batch_id,
            "batch_file_id": persisted_file.id,
            "status": "ready",
            "ready_count": 1,
            "failed_count": 0,
            "resource_version": 2,
        }
        assert audit.entity_id == persisted_file.id
        assert audit.assessment_id == assessment_id
        assert audit.correlation_id == event.event_id
        assert idempotency.response_status_code == 201
        assert idempotency.response_snapshot_json == body
    finally:
        db.close()

    reconciled = client.get(
        batch.headers["location"],
        headers={"If-None-Match": original_batch_etag},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["data"]["row_version"] == 2
    assert reconciled.json()["data"]["files"][0]["batch_file_id"] == (
        file_snapshot["batch_file_id"]
    )


def test_api12_replays_same_key_and_same_client_content_without_duplicate_writes(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    key = _key()
    client_file_id = f"client-{uuid.uuid4().hex}"
    first = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
        client_file_id=client_file_id,
    )
    before = _upload_file_counts(session_factory)

    same_key = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
        client_file_id=client_file_id,
    )
    other_key = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=_key(),
        client_file_id=client_file_id,
    )

    assert first.status_code == same_key.status_code == other_key.status_code == 201
    assert same_key.json() == first.json()
    assert same_key.headers["idempotent-replay"] == "true"
    assert other_key.headers["idempotent-replay"] == "true"
    assert other_key.json()["data"]["file"]["batch_file_id"] == (
        first.json()["data"]["file"]["batch_file_id"]
    )
    assert len(storage.put_calls) == 1
    assert _upload_file_counts(session_factory) == {
        **before,
        "idempotency": before["idempotency"] + 1,
    }


def test_api12_rejects_key_reuse_and_compensates_client_file_conflict(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    key = _key()
    client_file_id = f"client-{uuid.uuid4().hex}"
    first = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
        client_file_id=client_file_id,
        content=_pdf_bytes("first"),
    )
    assert first.status_code == 201
    first_object_key = storage.put_calls[0]

    reused_key = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
        client_file_id=client_file_id,
        content=_pdf_bytes("different"),
    )
    assert reused_key.status_code == 409
    assert reused_key.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    assert len(storage.put_calls) == 1

    conflict_key = _key()
    client_conflict = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=conflict_key,
        client_file_id=client_file_id,
        content=_pdf_bytes("different"),
    )
    assert client_conflict.status_code == 409
    _validate_contract("ErrorEnvelope", client_conflict.json())
    assert client_conflict.json()["error"]["error_code"] == (
        "BID_UPLOAD_CLIENT_FILE_CONFLICT"
    )
    assert len(storage.put_calls) == 2
    assert storage.delete_calls == [storage.put_calls[1]]
    assert set(storage.objects) == {first_object_key}
    counts = _upload_file_counts(session_factory)
    assert counts["batch_file"] == 1
    assert counts["file_object"] == 1
    assert counts["outbox"] == 3
    assert counts["audit"] == 3
    assert counts["idempotency"] == 4

    replayed_conflict = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=conflict_key,
        client_file_id=client_file_id,
        content=_pdf_bytes("different"),
    )
    assert replayed_conflict.status_code == 409
    assert replayed_conflict.headers["idempotent-replay"] == "true"
    assert len(storage.put_calls) == 2


def test_api12_validates_hash_magic_extension_and_relative_path_before_storage(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    before = _upload_file_counts(session_factory)

    hash_mismatch = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        expected_sha256="0" * 64,
    )
    disguised = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=b"not a pdf",
    )
    unsupported = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        filename="payload.exe",
        content=b"MZ",
        content_type="application/octet-stream",
    )
    unsafe_path = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        relative_path="../招标文件.pdf",
    )

    assert hash_mismatch.status_code == 422
    assert hash_mismatch.json()["error"]["error_code"] == "BID_FILE_CONTENT_INVALID"
    assert hash_mismatch.json()["error"]["details"]["reason"] == "sha256_mismatch"
    assert disguised.status_code == 422
    assert disguised.json()["error"]["details"]["reason"] == "pdf_magic_mismatch"
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["error_code"] == "BID_FILE_TYPE_UNSUPPORTED"
    assert unsafe_path.status_code == 422
    assert unsafe_path.json()["error"]["error_code"] == "BID_REQUEST_VALIDATION_FAILED"
    assert storage.put_calls == []
    assert _upload_file_counts(session_factory) == before


def test_api12_enforces_stream_and_batch_limits_without_unbounded_storage_write(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()

    object.__setattr__(settings, "bid_upload_max_file_bytes", 32)
    too_large = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("larger-than-32-bytes"),
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["error_code"] == "BID_FILE_TOO_LARGE"
    assert storage.put_calls == []

    object.__setattr__(settings, "bid_upload_max_file_bytes", 209715200)
    object.__setattr__(settings, "bid_upload_max_files", 1)
    first = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("first"),
    )
    assert first.status_code == 201
    second = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("second"),
    )
    assert second.status_code == 413
    assert second.json()["error"]["error_code"] == "BID_BATCH_TOO_LARGE"
    assert second.json()["error"]["details"]["reason"] == "max_files"
    assert len(storage.put_calls) == 1
    db = session_factory()
    try:
        persisted = db.query(BidUploadBatch).one()
        assert persisted.row_version == 2
        assert db.query(BidUploadBatchFile).count() == 1
    finally:
        db.close()

    object.__setattr__(settings, "bid_upload_max_files", 100)
    first_content = _pdf_bytes("batch-byte-first")
    object.__setattr__(settings, "bid_upload_max_batch_bytes", len(first_content))
    second_assessment = _create_assessment(client)
    second_batch = _create_initial_upload_batch(
        client,
        assessment_id=second_assessment.json()["data"]["assessment_id"],
    )
    accepted = _upload_file(
        client,
        batch_location=second_batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=first_content,
    )
    batch_bytes = _upload_file(
        client,
        batch_location=second_batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("batch-byte-second"),
    )
    assert accepted.status_code == 201
    assert batch_bytes.status_code == 413
    assert batch_bytes.json()["error"]["error_code"] == "BID_BATCH_TOO_LARGE"
    assert batch_bytes.json()["error"]["details"]["reason"] == "max_batch_bytes"


def test_api12_storage_failure_is_retryable_with_the_same_idempotency_key(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    storage.fail_put = True
    key = _key()
    client_file_id = f"client-{uuid.uuid4().hex}"

    failed = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
        client_file_id=client_file_id,
    )
    assert failed.status_code == 503
    assert failed.json()["error"]["error_code"] == "BID_STORAGE_UNAVAILABLE"
    db = session_factory()
    try:
        record = (
            db.query(BidIdempotencyRecord)
            .filter(BidIdempotencyRecord.idempotency_key == key)
            .one()
        )
        assert record.status == "failed"
        assert record.retryable is True
        assert db.query(BidUploadBatchFile).count() == 0
    finally:
        db.close()

    storage.fail_put = False
    retried = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
        client_file_id=client_file_id,
    )
    assert retried.status_code == 201
    assert len(storage.put_calls) == 2
    assert len(storage.objects) == 1


def test_api12_database_failure_rolls_back_and_deletes_only_its_exact_object(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()

    def _fail_audit(*_args, **_kwargs):
        raise RuntimeError("forced API-12 audit failure")

    monkeypatch.setattr(upload_file_commands, "append_audit_log", _fail_audit)
    failed = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )

    assert failed.status_code == 503
    assert failed.json()["error"]["error_code"] == "BID_STORAGE_UNAVAILABLE"
    assert len(storage.put_calls) == 1
    assert storage.delete_calls == storage.put_calls
    assert storage.objects == {}
    counts = _upload_file_counts(session_factory)
    assert counts["batch_file"] == 0
    assert counts["file_object"] == 0
    assert counts["outbox"] == 2
    assert counts["audit"] == 2
    assert counts["idempotency"] == 3
    db = session_factory()
    try:
        batch_row = db.query(BidUploadBatch).one()
        upload_record = (
            db.query(BidIdempotencyRecord)
            .filter(BidIdempotencyRecord.route_template == assessments_api._UPLOAD_FILE_ROUTE_TEMPLATE)
            .one()
        )
        assert batch_row.row_version == 1
        assert batch_row.status == "draft"
        assert upload_record.status == "failed"
        assert upload_record.retryable is True
    finally:
        db.close()


def test_api12_projects_upload_progress_and_orphan_cleanup_preserves_references(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    batch = _create_initial_upload_batch(client, assessment_id=assessment_id)
    storage = _FakeBidUploadStorage()
    created = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert created.status_code == 201

    db = session_factory()
    try:
        event_id = (
            db.query(BidOutboxEvent.event_id)
            .filter(BidOutboxEvent.event_type == "bid.upload_file.received.v1")
            .scalar()
        )
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            result = project_outbox_event_to_public(db, event_id=event_id)
            assert result.duplicate is False
    finally:
        db.close()
    db = session_factory()
    try:
        public_event = (
            db.query(BidPublicEvent)
            .filter(BidPublicEvent.event_type == "upload_batch.changed")
            .one()
        )
        assert public_event.assessment_id == assessment_id
        assert public_event.resource_version == 2
        assert public_event.payload_json == {
            "batch_id": batch.json()["data"]["batch_id"],
            "status": "ready",
            "ready_count": 1,
            "failed_count": 0,
        }
    finally:
        db.close()

    referenced_key = next(iter(storage.objects))
    orphan_key = (
        "bid-assessment/uploading/v1/2026/08/01/"
        f"{batch.json()['data']['batch_id']}/{uuid.uuid4()}"
    )
    storage.objects[orphan_key] = b"orphan"
    old_time = datetime.now(timezone.utc) - timedelta(days=2)
    storage.modified[referenced_key] = old_time
    storage.modified[orphan_key] = old_time
    db = session_factory()
    try:
        cleanup = cleanup_orphaned_bid_upload_objects(
            db,
            storage=storage,
            now=datetime.now(timezone.utc),
        )
    finally:
        db.close()
    assert cleanup.scanned == 2
    assert cleanup.referenced == 1
    assert cleanup.deleted == 1
    assert cleanup.delete_failed == 0
    assert referenced_key in storage.objects
    assert orphan_key not in storage.objects


def test_api12_replace_requires_a_document_in_the_current_manifest(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=user.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=user.id,
    )
    assessment_snapshot = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    batch = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/upload-batches",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": assessment_snapshot.headers["etag"],
        },
        json={"purpose": "change", "base_manifest_id": manifest_id},
    )
    assert batch.status_code == 201
    storage = _FakeBidUploadStorage()

    invalid = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        operation="replace",
        replace_document_id=str(uuid.uuid4()),
        content=_pdf_bytes("invalid replacement"),
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["error_code"] == "BID_REPLACEMENT_TARGET_INVALID"
    assert storage.delete_calls == storage.put_calls

    valid = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        operation="replace",
        replace_document_id=document_id,
        content=_pdf_bytes("valid replacement"),
    )
    assert valid.status_code == 201
    db = session_factory()
    try:
        uploaded = db.query(BidUploadBatchFile).one()
        assert uploaded.operation == "replace"
        assert uploaded.replace_document_id == document_id
        assert db.query(BidUploadBatch).filter(BidUploadBatch.id == batch.json()["data"]["batch_id"]).one().row_version == 2
    finally:
        db.close()


def test_api12_hides_unauthorized_batch_and_feature_gate_without_object_write(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _owner = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider

    hidden = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    assert storage.put_calls == []

    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    visible = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert visible.status_code == 201

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("disabled"),
    )
    assert disabled.status_code == 404
    assert len(storage.put_calls) == 1


def test_api13_removes_last_draft_reference_and_advances_authoritative_batch(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    batch = _create_initial_upload_batch(client, assessment_id=assessment_id)
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    file_id = uploaded.json()["data"]["file"]["batch_file_id"]
    authoritative = client.get(batch.headers["location"])
    file_snapshot = authoritative.json()["data"]["files"][0]
    assert file_snapshot["row_version"] == 1
    assert file_snapshot["etag"] == uploaded.headers["etag"]

    removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=file_id,
        file_etag=file_snapshot["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )

    assert removed.status_code == 204
    assert removed.content == b""
    assert removed.headers["x-batch-resource-version"] == "3"
    assert removed.headers["x-batch-etag"].startswith(
        f'"bid-upload-batch:{batch.json()["data"]["batch_id"]}:3:'
    )
    assert removed.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in removed.headers
    assert storage.delete_calls == storage.put_calls
    assert storage.objects == {}

    db = session_factory()
    try:
        persisted_batch = db.query(BidUploadBatch).one()
        removed_event = (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.event_type == "bid.upload_file.removed.v1")
            .one()
        )
        audit = (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_file.remove_draft")
            .one()
        )
        idempotency = (
            db.query(BidIdempotencyRecord)
            .filter(
                BidIdempotencyRecord.route_template
                == assessments_api._UPLOAD_FILE_ITEM_ROUTE_TEMPLATE
            )
            .one()
        )
        assert persisted_batch.status == "draft"
        assert persisted_batch.row_version == 3
        assert db.query(BidUploadBatchFile).count() == 0
        assert db.query(BidFileObject).count() == 0
        assert removed_event.aggregate_version == 3
        assert removed_event.payload_json == {
            "batch_id": str(persisted_batch.id),
            "batch_file_id": file_id,
            "status": "draft",
            "ready_count": 0,
            "failed_count": 0,
            "resource_version": 3,
        }
        assert audit.entity_id == file_id
        assert audit.correlation_id == removed_event.event_id
        assert audit.metadata_json["file_etag"] == file_snapshot["etag"]
        assert idempotency.status == "completed"
        assert idempotency.response_status_code == 204
        assert idempotency.resource_type == "upload_batch"
        assert idempotency.resource_id == str(persisted_batch.id)
        assert idempotency.response_snapshot_json["batch_row_version"] == 3
        removed_event_id = removed_event.event_id
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            projection = project_outbox_event_to_public(
                db,
                event_id=removed_event_id,
            )
            assert projection.duplicate is False
    finally:
        db.close()
    db = session_factory()
    try:
        public_event = db.query(BidPublicEvent).one()
        assert public_event.event_type == "upload_batch.changed"
        assert public_event.resource_version == 3
        assert public_event.payload_json == {
            "batch_id": batch.json()["data"]["batch_id"],
            "status": "draft",
            "ready_count": 0,
            "failed_count": 0,
        }
    finally:
        db.close()

    reconciled = client.get(
        batch.headers["location"],
        headers={"If-None-Match": authoritative.headers["etag"]},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["data"]["row_version"] == 3
    assert reconciled.json()["data"]["files"] == []
    assert reconciled.json()["data"]["validation"]["can_commit"] is False


def test_api13_replays_empty_204_and_rejects_key_reuse_without_second_mutation(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    first = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("first-to-remove"),
    )
    second = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("second-to-keep"),
    )
    key = _key()
    first_file = first.json()["data"]["file"]
    second_file = second.json()["data"]["file"]
    removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=first_file["batch_file_id"],
        file_etag=first.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
    )
    counts = _upload_file_counts(session_factory)
    replay = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=first_file["batch_file_id"],
        file_etag=first.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
    )
    reused = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=second_file["batch_file_id"],
        file_etag=second.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
        idempotency_key=key,
    )

    assert removed.status_code == replay.status_code == 204
    assert removed.content == replay.content == b""
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.headers["x-batch-etag"] == removed.headers["x-batch-etag"]
    assert reused.status_code == 409
    assert reused.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    assert _upload_file_counts(session_factory) == counts
    assert len(storage.delete_calls) == 1
    db = session_factory()
    try:
        batch_row = db.query(BidUploadBatch).one()
        assert batch_row.row_version == 4
        assert db.query(BidUploadBatchFile).count() == 1
        assert db.query(BidUploadBatchFile).one().id == second_file["batch_file_id"]
    finally:
        db.close()


def test_api13_requires_one_current_strong_file_etag_without_side_effects(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    file_id = uploaded.json()["data"]["file"]["batch_file_id"]
    url = f"{batch.headers['location']}/files/{file_id}"
    before = _upload_file_counts(session_factory)
    prior_delete_calls = list(storage.delete_calls)

    missing = client.delete(url, headers={"Idempotency-Key": _key()})
    malformed = [
        client.delete(
            url,
            headers={"Idempotency-Key": _key(), "If-Match": value},
        )
        for value in ("*", f"W/{uploaded.headers['etag']}", '"one", "two"')
    ]
    stale_etag = f'"bid-upload-file:{file_id}:99"'
    stale = client.delete(
        url,
        headers={"Idempotency-Key": _key(), "If-Match": stale_etag},
    )

    assert missing.status_code == 428
    assert missing.json()["error"]["error_code"] == "BID_PRECONDITION_REQUIRED"
    assert all(response.status_code == 400 for response in malformed)
    assert all(
        response.json()["error"]["error_code"] == "BID_REQUEST_MALFORMED"
        for response in malformed
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["error_code"] == "BID_RESOURCE_VERSION_MISMATCH"
    assert stale.headers["etag"] == uploaded.headers["etag"]
    assert stale.headers["x-resource-version"] == "1"
    assert _upload_file_counts(session_factory) == before
    assert storage.delete_calls == prior_delete_calls


def test_api13_preserves_shared_file_object_until_the_last_batch_reference(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    content = _pdf_bytes("shared-content")
    first = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=content,
    )
    second = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=content,
    )
    assert len(storage.put_calls) == 1
    object_key = storage.put_calls[0]

    first_removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=first.json()["data"]["file"]["batch_file_id"],
        file_etag=first.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert first_removed.status_code == 204
    assert storage.delete_calls == []
    assert object_key in storage.objects
    db = session_factory()
    try:
        assert db.query(BidUploadBatchFile).count() == 1
        assert db.query(BidFileObject).count() == 1
        assert db.query(BidUploadBatch).one().status == "ready"
    finally:
        db.close()

    second_removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=second.json()["data"]["file"]["batch_file_id"],
        file_etag=second.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert second_removed.status_code == 204
    assert storage.delete_calls == [object_key]
    assert storage.objects == {}
    db = session_factory()
    try:
        assert db.query(BidUploadBatchFile).count() == 0
        assert db.query(BidFileObject).count() == 0
        assert db.query(BidUploadBatch).one().row_version == 5
    finally:
        db.close()


def test_api13_never_deletes_an_object_referenced_by_document_version(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    object_key = storage.put_calls[0]
    db = session_factory()
    try:
        with db.begin():
            file_object = db.query(BidFileObject).one()
            document = BidDocument(
                id=str(uuid.uuid4()),
                logical_identity_key=f"document-{uuid.uuid4().hex}",
                logical_name="共享权威文档",
                document_type="tender_document",
                created_by=user.id,
            )
            db.add(document)
            db.flush()
            db.add(
                BidDocumentVersion(
                    id=str(uuid.uuid4()),
                    document_id=document.id,
                    file_object_id=file_object.id,
                    version_no=1,
                    original_filename="共享权威文档.pdf",
                    parser_hint="pdf",
                    source_metadata_hash="e" * 64,
                    source_metadata_json={},
                    created_by=user.id,
                )
            )
    finally:
        db.close()

    removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=uploaded.json()["data"]["file"]["batch_file_id"],
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert removed.status_code == 204
    assert storage.delete_calls == []
    assert object_key in storage.objects
    db = session_factory()
    try:
        assert db.query(BidUploadBatchFile).count() == 0
        assert db.query(BidFileObject).count() == 1
        assert db.query(BidDocumentVersion).count() == 1
    finally:
        db.close()


def test_api13_audit_failure_rolls_back_before_any_physical_delete(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )

    def _fail_audit(*_args, **_kwargs):
        raise RuntimeError("forced API-13 audit failure")

    monkeypatch.setattr(
        upload_file_removal_commands,
        "append_audit_log",
        _fail_audit,
    )
    before = _upload_file_counts(session_factory)
    removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=uploaded.json()["data"]["file"]["batch_file_id"],
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )

    assert removed.status_code == 503
    assert removed.json()["error"]["error_code"] == "BID_STORAGE_UNAVAILABLE"
    assert _upload_file_counts(session_factory) == before
    assert storage.delete_calls == []
    assert len(storage.objects) == 1
    db = session_factory()
    try:
        assert db.query(BidUploadBatchFile).count() == 1
        assert db.query(BidFileObject).count() == 1
        assert db.query(BidUploadBatch).one().row_version == 2
    finally:
        db.close()


def test_api13_post_commit_delete_failure_is_204_and_orphan_cleanup_recovers(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    object_key = storage.put_calls[0]
    storage.fail_delete = True
    removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=uploaded.json()["data"]["file"]["batch_file_id"],
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )

    assert removed.status_code == 204
    assert object_key in storage.objects
    db = session_factory()
    try:
        assert db.query(BidUploadBatchFile).count() == 0
        assert db.query(BidFileObject).count() == 0
    finally:
        db.close()

    storage.fail_delete = False
    storage.modified[object_key] = datetime.now(timezone.utc) - timedelta(days=2)
    db = session_factory()
    try:
        cleanup = cleanup_orphaned_bid_upload_objects(
            db,
            storage=storage,
            now=datetime.now(timezone.utc),
        )
    finally:
        db.close()
    assert cleanup.deleted == 1
    assert cleanup.referenced == 0
    assert object_key not in storage.objects
    assert storage.delete_calls == [object_key, object_key]


def test_api13_enforces_committed_visibility_admin_and_feature_gates(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _owner = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    file_id = uploaded.json()["data"]["file"]["batch_file_id"]
    db = session_factory()
    try:
        with db.begin():
            batch_row = db.query(BidUploadBatch).one()
            # The API-13 gate treats the in-flight commit state exactly like the
            # terminal state.  Use it here so the fixture cannot bypass API-15's
            # committed_manifest_id/committed_at database invariant.
            batch_row.status = "committing"
            batch_row.open_slot_key = None
    finally:
        db.close()

    committed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=file_id,
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert committed.status_code == 409
    assert committed.json()["error"]["error_code"] == (
        "BID_UPLOAD_BATCH_ALREADY_COMMITTED"
    )
    assert storage.delete_calls == []

    db = session_factory()
    try:
        with db.begin():
            batch_row = db.query(BidUploadBatch).one()
            batch_row.status = "ready"
            batch_row.open_slot_key = batch_row.purpose
    finally:
        db.close()
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=file_id,
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert hidden.status_code == 404
    assert storage.delete_calls == []

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=file_id,
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert disabled.status_code == 404
    assert storage.delete_calls == []

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)
    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    admin_removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=file_id,
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert admin_removed.status_code == 204
    assert storage.delete_calls == storage.put_calls


def test_api14_atomically_deactivates_baseline_documents_without_touching_history(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    assessment_id, manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=2,
    )
    before = _deactivation_counts(session_factory)
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: (_ for _ in ()).throw(AssertionError("API-14 must not use storage")),
    )

    response = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=list(reversed(document_ids)),
        reason="  补遗已明确附件不再适用  ",
    )

    assert response.status_code == 201
    _validate_contract("UploadBatchResponse", response.json())
    snapshot = response.json()["data"]
    assert snapshot["assessment_id"] == assessment_id
    assert snapshot["base_manifest_id"] == manifest_id
    assert snapshot["purpose"] == "change"
    assert snapshot["status"] == "ready"
    assert snapshot["row_version"] == 2
    assert snapshot["files"] == []
    assert snapshot["deactivations"] == sorted(document_ids)
    assert snapshot["validation"] == {
        "can_commit": True,
        "blocking_errors": [],
        "warnings": [],
    }
    assert response.headers["location"] == batch.headers["location"]
    assert response.headers["etag"] != batch.headers["etag"]
    assert response.headers["x-resource-version"] == "2"
    assert response.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in response.headers

    after = _deactivation_counts(session_factory)
    assert after == {
        **before,
        "deactivation": 2,
        "outbox": before["outbox"] + 1,
        "audit": before["audit"] + 1,
        "idempotency": before["idempotency"] + 1,
    }
    db = session_factory()
    try:
        rows = db.query(BidUploadBatchDeactivation).all()
        assert {row.document_id for row in rows} == set(document_ids)
        assert {row.reason for row in rows} == {"补遗已明确附件不再适用"}
        outbox = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.event_type
                == "bid.upload_batch.deactivation_added.v1"
            )
            .one()
        )
        assert outbox.aggregate_version == 2
        assert outbox.payload_json == {
            "batch_id": snapshot["batch_id"],
            "document_ids": sorted(document_ids),
            "status": "ready",
            "ready_count": 0,
            "failed_count": 0,
            "deactivation_count": 2,
            "resource_version": 2,
        }
        audit = (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_batch.add_deactivations")
            .one()
        )
        assert audit.correlation_id == outbox.event_id
        assert audit.metadata_json["operation_noop"] is False
        event_id = outbox.event_id
        with db.begin_nested():
            projected = project_outbox_event_to_public(db, event_id=event_id)
            assert projected.duplicate is False
        db.commit()
    finally:
        db.close()

    db = session_factory()
    try:
        public_event = (
            db.query(BidPublicEvent)
            .filter(BidPublicEvent.event_type == "upload_batch.changed")
            .one()
        )
        assert public_event.resource_version == 2
        assert public_event.payload_json == {
            "batch_id": snapshot["batch_id"],
            "status": "ready",
            "ready_count": 0,
            "failed_count": 0,
        }
    finally:
        db.close()

    authoritative = client.get(batch.headers["location"])
    assert authoritative.status_code == 200
    assert authoritative.headers["etag"] == response.headers["etag"]
    assert authoritative.json()["data"] == snapshot


def test_api14_requires_current_strong_batch_etag_and_strict_atomic_body(
    api_runtime,
) -> None:
    client, session_factory, user = api_runtime
    _assessment_id, _manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=1,
    )
    body = {"document_ids": document_ids, "reason": "停用原因"}
    location = f"{batch.headers['location']}/deactivations"
    baseline = _deactivation_counts(session_factory)

    missing = client.post(
        location,
        headers={"Idempotency-Key": _key()},
        json=body,
    )
    weak = client.post(
        location,
        headers={"Idempotency-Key": _key(), "If-Match": f"W/{batch.headers['etag']}"},
        json=body,
    )
    wildcard = client.post(
        location,
        headers={"Idempotency-Key": _key(), "If-Match": "*"},
        json=body,
    )
    listed = client.post(
        location,
        headers={
            "Idempotency-Key": _key(),
            "If-Match": f"{batch.headers['etag']}, \"other\"",
        },
        json=body,
    )
    stale = client.post(
        location,
        headers={
            "Idempotency-Key": _key(),
            "If-Match": f'"bid-upload-batch:{batch.json()["data"]["batch_id"]}:99:deadbeef0000"',
        },
        json=body,
    )
    duplicate_ids = client.post(
        location,
        headers={"Idempotency-Key": _key(), "If-Match": batch.headers["etag"]},
        json={"document_ids": document_ids * 2, "reason": "停用原因"},
    )
    old_shape = client.post(
        location,
        headers={"Idempotency-Key": _key(), "If-Match": batch.headers["etag"]},
        json={"document_id": document_ids[0], "reason": "停用原因"},
    )
    blank_reason = client.post(
        location,
        headers={"Idempotency-Key": _key(), "If-Match": batch.headers["etag"]},
        json={"document_ids": document_ids, "reason": "   "},
    )

    assert missing.status_code == 428
    assert missing.json()["error"]["error_code"] == "BID_PRECONDITION_REQUIRED"
    for response in (weak, wildcard, listed):
        assert response.status_code == 400
        assert response.json()["error"]["error_code"] == "BID_REQUEST_MALFORMED"
    assert stale.status_code == 412
    assert stale.json()["error"]["error_code"] == "BID_RESOURCE_VERSION_MISMATCH"
    assert stale.headers["etag"] == batch.headers["etag"]
    assert stale.headers["x-resource-version"] == "1"
    for response in (duplicate_ids, old_shape, blank_reason):
        assert response.status_code == 422
        assert response.json()["error"]["error_code"] == (
            "BID_REQUEST_VALIDATION_FAILED"
        )
    assert _deactivation_counts(session_factory) == baseline


def test_api14_validates_every_target_against_the_frozen_base_manifest(
    api_runtime,
) -> None:
    client, session_factory, user = api_runtime
    assessment_id, _manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=1,
    )
    invalid_document_id = str(uuid.uuid4())
    invalid = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=[document_ids[0], invalid_document_id],
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["error_code"] == (
        "BID_UPLOAD_DEACTIVATION_TARGET_INVALID"
    )
    assert invalid.json()["error"]["details"] == {
        "invalid_document_ids": [invalid_document_id]
    }
    assert _deactivation_counts(session_factory)["deactivation"] == 0

    new_manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=user.id,
    )
    stale = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["error_code"] == "BID_BASE_MANIFEST_STALE"
    assert stale.json()["error"]["details"]["current_manifest_id"] == new_manifest_id
    assert _deactivation_counts(session_factory)["deactivation"] == 0


def test_api14_duplicate_semantics_are_noop_or_atomic_conflict(api_runtime) -> None:
    client, session_factory, user = api_runtime
    _assessment_id, _manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=3,
    )
    key = _key()
    first = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=[document_ids[1], document_ids[0]],
        reason=" 同一原因 ",
        idempotency_key=key,
    )
    replay = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids[:2],
        reason="同一原因",
        idempotency_key=key,
    )
    assert first.status_code == replay.status_code == 201
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == first.json()
    assert replay.headers["etag"] == first.headers["etag"]

    after_first = _deactivation_counts(session_factory)
    noop = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=first.headers["etag"],
        document_ids=list(reversed(document_ids[:2])),
        reason="同一原因",
    )
    assert noop.status_code == 201
    assert noop.json()["data"]["row_version"] == 2
    assert noop.headers["etag"] == first.headers["etag"]
    after_noop = _deactivation_counts(session_factory)
    assert after_noop["deactivation"] == after_first["deactivation"] == 2
    assert after_noop["outbox"] == after_first["outbox"]
    assert after_noop["audit"] == after_first["audit"] + 1
    assert after_noop["idempotency"] == after_first["idempotency"] + 1

    mixed = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=noop.headers["etag"],
        document_ids=list(reversed(document_ids)),
        reason="同一原因",
    )
    assert mixed.status_code == 201
    assert mixed.json()["data"]["row_version"] == 3
    assert mixed.json()["data"]["deactivations"] == sorted(document_ids)
    after_mixed = _deactivation_counts(session_factory)
    assert after_mixed["deactivation"] == 3
    assert after_mixed["outbox"] == after_noop["outbox"] + 1

    conflict = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=mixed.headers["etag"],
        document_ids=[document_ids[0]],
        reason="不同原因",
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["error_code"] == (
        "BID_UPLOAD_DEACTIVATION_CONFLICT"
    )
    assert _deactivation_counts(session_factory) == after_mixed

    reused = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=[document_ids[1]],
        reason="同一原因",
        idempotency_key=key,
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    assert _deactivation_counts(session_factory) == after_mixed


def test_api14_rolls_back_when_audit_fails_and_never_calls_object_storage(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    _assessment_id, _manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=1,
    )
    baseline = _deactivation_counts(session_factory)
    monkeypatch.setattr(
        upload_deactivation_commands,
        "append_audit_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit down")),
    )
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: (_ for _ in ()).throw(AssertionError("API-14 must not use storage")),
    )

    failed = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    assert failed.status_code == 503
    assert _deactivation_counts(session_factory) == baseline
    db = session_factory()
    try:
        batch_row = db.query(BidUploadBatch).one()
        assert batch_row.status == "draft"
        assert batch_row.row_version == 1
    finally:
        db.close()


def test_api14_visibility_feature_purpose_and_committed_gates(api_runtime) -> None:
    client, session_factory, owner = api_runtime
    initial_assessment = _create_assessment(client)
    initial_batch = _create_initial_upload_batch(
        client,
        assessment_id=initial_assessment.json()["data"]["assessment_id"],
    )
    initial = _add_deactivations(
        client,
        batch_location=initial_batch.headers["location"],
        batch_etag=initial_batch.headers["etag"],
        document_ids=[str(uuid.uuid4())],
    )
    assert initial.status_code == 409
    assert initial.json()["error"]["error_code"] == (
        "BID_UPLOAD_DEACTIVATION_NOT_ALLOWED"
    )

    db = session_factory()
    try:
        with db.begin():
            initial_row = (
                db.query(BidUploadBatch)
                .filter(BidUploadBatch.id == initial_batch.json()["data"]["batch_id"])
                .one()
            )
            initial_row.status = "expired"
            initial_row.open_slot_key = None
    finally:
        db.close()

    _assessment_id, _manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=owner,
        document_count=1,
    )
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    assert hidden.status_code == 404

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    assert disabled.status_code == 404
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)

    db = session_factory()
    try:
        with db.begin():
            batch_row = (
                db.query(BidUploadBatch)
                .filter(BidUploadBatch.id == batch.json()["data"]["batch_id"])
                .one()
            )
            # Do not manufacture a terminal committed row without API-15's
            # immutable Manifest lineage; committing exercises the same gate.
            batch_row.status = "committing"
            batch_row.open_slot_key = None
    finally:
        db.close()
    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    committed = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    assert committed.status_code == 409
    assert committed.json()["error"]["error_code"] == (
        "BID_UPLOAD_BATCH_ALREADY_COMMITTED"
    )

    db = session_factory()
    try:
        with db.begin():
            batch_row = (
                db.query(BidUploadBatch)
                .filter(BidUploadBatch.id == batch.json()["data"]["batch_id"])
                .one()
            )
            batch_row.status = "draft"
            batch_row.open_slot_key = batch_row.purpose
    finally:
        db.close()
    admin_created = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    assert admin_created.status_code == 201


def test_api13_keeps_change_batch_ready_when_deactivation_remains(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    _assessment_id, _manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=1,
    )
    deactivated = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    removed = _delete_upload_file(
        client,
        batch_location=batch.headers["location"],
        file_id=uploaded.json()["data"]["file"]["batch_file_id"],
        file_etag=uploaded.headers["etag"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    assert deactivated.json()["data"]["row_version"] == 2
    assert uploaded.json()["data"]["batch"]["row_version"] == 3
    assert removed.status_code == 204
    assert removed.headers["x-batch-resource-version"] == "4"
    authoritative = client.get(batch.headers["location"])
    assert authoritative.json()["data"]["status"] == "ready"
    assert authoritative.json()["data"]["deactivations"] == document_ids
    assert authoritative.json()["data"]["validation"]["can_commit"] is True


def test_api15_commits_initial_batch_manifest_and_atomic_events(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    batch = _create_initial_upload_batch(client, assessment_id=assessment_id)
    storage = _FakeBidUploadStorage()
    first = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("first"),
        filename="01-招标文件.pdf",
    )
    second = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("second"),
        filename="02-工程量清单.pdf",
    )

    response = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=second.headers["x-batch-etag"],
        expected_file_count=2,
        change_note="  首次提交招标资料  ",
    )

    assert response.status_code == 202
    _validate_contract("CommitUploadBatchResponse", response.json())
    data = response.json()["data"]
    assert data["run"] is None
    assert data["manifest"]["version"] == 1
    assert data["manifest"]["document_count"] == 2
    assert data["assessment"]["assessment_id"] == assessment_id
    assert data["assessment"]["business_status"] == "preparing"
    assert data["assessment"]["current_manifest"]["manifest_id"] == data["manifest"]["manifest_id"]
    assert data["batch"]["status"] == "committed"
    assert response.headers["location"] == f"/api/v1/bid-assessments/{assessment_id}"
    assert response.headers["etag"] == (
        f'"bid-assessment:{assessment_id}:{data["assessment"]["row_version"]}"'
    )
    assert response.headers["x-batch-etag"] == data["batch"]["etag"]
    assert storage.delete_calls == []

    db = session_factory()
    try:
        manifest = db.query(BidDocumentManifest).one()
        batch_row = db.query(BidUploadBatch).one()
        assessment_row = db.query(BidAssessment).one()
        versions = db.query(BidDocumentVersion).order_by(BidDocumentVersion.original_filename).all()
        members = db.query(BidManifestDocument).order_by(BidManifestDocument.order_no).all()
        events = db.query(BidOutboxEvent).order_by(BidOutboxEvent.occurred_at).all()
        assert manifest.id == data["manifest"]["manifest_id"]
        assert manifest.change_note == "首次提交招标资料"
        assert batch_row.committed_manifest_id == manifest.id
        assert batch_row.committed_at is not None
        assert batch_row.open_slot_key is None
        assert assessment_row.current_manifest_id == manifest.id
        assert assessment_row.active_run_id is None
        assert len(versions) == len(members) == 2
        assert [row.order_no for row in members] == [0, 1]
        commit_events = [row for row in events if row.request_id == response.json()["request_id"]]
        assert [row.event_type for row in commit_events] == [
            "bid.document.version_registered.v1",
            "bid.document.version_registered.v1",
            "bid.manifest.committed.v1",
            "bid.document.parse_requested.v1",
            "bid.document.parse_requested.v1",
        ]
        assert commit_events[0].causation_event_id is None
        for previous, current in zip(commit_events, commit_events[1:]):
            assert current.causation_event_id == previous.event_id
        assert not any(row.event_type == "bid.plan.requested.v1" for row in commit_events)
        audit = (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_batch.commit")
            .one()
        )
        assert audit.metadata_json["planning_event"] == (
            "deferred_until_parse_and_scope_ready"
        )
        assert audit.correlation_id == commit_events[2].event_id
        manifest_event_id = commit_events[2].event_id
        db.commit()
        with db.begin():
            projected = project_outbox_event_to_public(
                db,
                event_id=manifest_event_id,
            )
            assert projected.duplicate is False
        public_event = db.query(BidPublicEvent).one()
        assert public_event.event_type == "assessment.snapshot"
        assert public_event.payload_json["snapshot"]["current_manifest"][
            "manifest_id"
        ] == manifest.id
    finally:
        db.close()


def test_api15_merges_add_replace_deactivate_and_stales_old_run(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    assessment_id, base_manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=3,
    )
    run_id = _attach_active_run(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=base_manifest_id,
        actor_id=user.id,
    )
    # Refresh after the helper advances the Assessment version; the already-open
    # batch remains valid because its baseline still equals the current Manifest.
    storage = _FakeBidUploadStorage()
    replacement = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("replacement"),
        filename="替换后的招标文件.pdf",
        operation="replace",
        replace_document_id=document_ids[0],
    )
    addition = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("addition"),
        filename="补遗文件.pdf",
    )
    deactivated = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=addition.headers["x-batch-etag"],
        document_ids=[document_ids[1]],
    )
    response = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=deactivated.headers["etag"],
        expected_file_count=2,
        expected_deactivation_count=1,
        change_note="补遗资料变更",
    )

    assert replacement.status_code == addition.status_code == 201
    assert deactivated.status_code == 201
    assert response.status_code == 202
    _validate_contract("CommitUploadBatchResponse", response.json())
    data = response.json()["data"]
    assert data["manifest"]["version"] == 2
    assert data["manifest"]["document_count"] == 3
    assert data["assessment"]["active_run"] is None
    assert data["assessment"]["business_status"] == "preparing"

    db = session_factory()
    try:
        base_members = (
            db.query(BidManifestDocument)
            .filter(BidManifestDocument.manifest_id == base_manifest_id)
            .order_by(BidManifestDocument.order_no)
            .all()
        )
        new_members = (
            db.query(BidManifestDocument, BidDocumentVersion)
            .join(
                BidDocumentVersion,
                BidDocumentVersion.id == BidManifestDocument.document_version_id,
            )
            .filter(BidManifestDocument.manifest_id == data["manifest"]["manifest_id"])
            .order_by(BidManifestDocument.order_no)
            .all()
        )
        assert len(base_members) == 3
        assert len(new_members) == 3
        new_by_document = {
            str(version.document_id): (member, version)
            for member, version in new_members
        }
        assert document_ids[0] in new_by_document
        assert new_by_document[document_ids[0]][1].version_no == 2
        assert document_ids[1] not in new_by_document
        assert document_ids[2] in new_by_document
        assert new_by_document[document_ids[2]][0].order_no == 2
        assert len(db.query(BidDocument).all()) == 4
        assert len(db.query(BidDocumentVersion).all()) == 5

        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.status == "stale"
        assert run.waiting_reason == "input_manifest_superseded"
        assert run.finished_at is not None
        assert run.row_version == 2
        assessment_row = db.query(BidAssessment).filter(BidAssessment.id == assessment_id).one()
        assert assessment_row.active_run_id is None

        commit_events = (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.request_id == response.json()["request_id"])
            .order_by(BidOutboxEvent.occurred_at)
            .all()
        )
        assert [row.event_type for row in commit_events] == [
            "bid.document.version_registered.v1",
            "bid.document.version_registered.v1",
            "bid.manifest.committed.v1",
            "bid.assessment.input_stale.v1",
            "bid.document.parse_requested.v1",
            "bid.document.parse_requested.v1",
            "bid.document.parse_requested.v1",
        ]
        assert commit_events[3].payload_json["stale_run_ids"] == [run_id]
        run_audit = (
            db.query(BidAuditLog)
            .filter(
                BidAuditLog.action == "analysis_run.input_stale",
                BidAuditLog.entity_id == run_id,
            )
            .one()
        )
        assert run_audit.correlation_id == commit_events[3].event_id
    finally:
        db.close()


def test_api15_allows_deactivation_only_change_and_empty_manifest(api_runtime) -> None:
    client, session_factory, user = api_runtime
    assessment_id, base_manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=1,
    )
    deactivated = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=document_ids,
    )
    assert deactivated.json()["data"]["validation"]["can_commit"] is True

    response = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=deactivated.headers["etag"],
        expected_file_count=0,
        expected_deactivation_count=1,
        change_note="停用唯一旧附件",
    )

    assert response.status_code == 202
    _validate_contract("CommitUploadBatchResponse", response.json())
    assert response.json()["data"]["manifest"]["document_count"] == 0
    assert response.json()["data"]["assessment"]["business_status"] == (
        "awaiting_files"
    )
    db = session_factory()
    try:
        assert db.query(BidDocumentManifest).count() == 2
        assert db.query(BidManifestDocument).count() == 1
        commit_events = (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.request_id == response.json()["request_id"])
            .order_by(BidOutboxEvent.occurred_at)
            .all()
        )
        assert [row.event_type for row in commit_events] == [
            "bid.manifest.committed.v1"
        ]
        base_manifest = (
            db.query(BidDocumentManifest)
            .filter(BidDocumentManifest.id == base_manifest_id)
            .one()
        )
        assert base_manifest.version == 1
    finally:
        db.close()


def test_api15_enforces_etag_body_and_operation_counts(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    batch = _create_initial_upload_batch(client, assessment_id=assessment_id)
    location = batch.headers["location"]

    missing = client.post(
        f"{location}/commit",
        headers={"Idempotency-Key": _key()},
        json={
            "expected_file_count": 0,
            "expected_deactivation_count": 0,
            "change_note": None,
            "confirm_start_analysis": True,
        },
    )
    assert missing.status_code == 428
    malformed = client.post(
        f"{location}/commit",
        headers={"Idempotency-Key": _key(), "If-Match": "W/\"weak\""},
        json={
            "expected_file_count": 0,
            "expected_deactivation_count": 0,
            "change_note": None,
            "confirm_start_analysis": True,
        },
    )
    assert malformed.status_code == 400
    unconfirmed = client.post(
        f"{location}/commit",
        headers={"Idempotency-Key": _key(), "If-Match": batch.headers["etag"]},
        json={
            "expected_file_count": 0,
            "expected_deactivation_count": 0,
            "change_note": None,
            "confirm_start_analysis": False,
        },
    )
    assert unconfirmed.status_code == 422

    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=location,
        storage=storage,
        monkeypatch=monkeypatch,
    )
    stale = _commit_upload_batch(
        client,
        batch_location=location,
        batch_etag=batch.headers["etag"],
        expected_file_count=1,
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["error_code"] == "BID_RESOURCE_VERSION_MISMATCH"
    mismatch = _commit_upload_batch(
        client,
        batch_location=location,
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=0,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["error_code"] == "BID_EXPECTED_FILE_COUNT_MISMATCH"
    assert mismatch.json()["error"]["details"] == {
        "expected_file_count": 0,
        "actual_file_count": 1,
    }
    db = session_factory()
    try:
        assert db.query(BidDocumentManifest).count() == 0
        assert db.query(BidDocumentVersion).count() == 0
        assert db.query(BidUploadBatch).one().status == "ready"
    finally:
        db.close()


def test_api15_idempotent_replay_and_already_committed_gate(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=_FakeBidUploadStorage(),
        monkeypatch=monkeypatch,
    )
    key = _key()
    first = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=1,
        idempotency_key=key,
    )
    replay = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=1,
        idempotency_key=key,
    )
    reused = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=0,
        idempotency_key=key,
    )
    committed = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=first.headers["x-batch-etag"],
        expected_file_count=1,
    )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replay"] == "true"
    assert reused.status_code == 409
    assert reused.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    assert committed.status_code == 409
    assert committed.json()["error"]["error_code"] == "BID_UPLOAD_BATCH_ALREADY_COMMITTED"
    db = session_factory()
    try:
        assert db.query(BidDocumentManifest).count() == 1
        assert db.query(BidDocumentVersion).count() == 1
        assert (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_batch.commit")
            .count()
            == 1
        )
    finally:
        db.close()


def test_api15_rolls_back_everything_when_audit_fails(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    before = _deactivation_counts(session_factory)
    monkeypatch.setattr(
        upload_commit_commands,
        "append_audit_log",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced audit failure")),
    )

    response = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=1,
    )

    assert response.status_code == 503
    assert _deactivation_counts(session_factory) == before
    assert len(storage.objects) == 1
    assert storage.delete_calls == []
    db = session_factory()
    try:
        batch_row = db.query(BidUploadBatch).one()
        assessment_row = db.query(BidAssessment).one()
        assert batch_row.status == "ready"
        assert batch_row.committed_manifest_id is None
        assert assessment_row.current_manifest_id is None
    finally:
        db.close()


def test_api15_rejects_deactivation_count_stale_baseline_and_merge_conflicts(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, user = api_runtime
    assessment_id, base_manifest_id, document_ids, batch = _change_batch_with_documents(
        client,
        session_factory,
        user=user,
        document_count=2,
    )
    deactivated = _add_deactivations(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
        document_ids=[document_ids[0]],
    )
    count_mismatch = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=deactivated.headers["etag"],
        expected_file_count=0,
        expected_deactivation_count=0,
    )
    assert count_mismatch.status_code == 409
    assert count_mismatch.json()["error"]["error_code"] == (
        "BID_EXPECTED_DEACTIVATION_COUNT_MISMATCH"
    )

    newer_manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=user.id,
    )
    baseline_stale = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=deactivated.headers["etag"],
        expected_file_count=0,
        expected_deactivation_count=1,
    )
    assert baseline_stale.status_code == 409
    assert baseline_stale.json()["error"]["error_code"] == "BID_BASE_MANIFEST_STALE"
    assert baseline_stale.json()["error"]["details"] == {
        "base_manifest_id": base_manifest_id,
        "current_manifest_id": newer_manifest_id,
    }

    # Use a second Assessment to isolate a duplicate-replacement merge conflict.
    second_assessment_id, _second_manifest, second_documents, second_batch = (
        _change_batch_with_documents(
            client,
            session_factory,
            user=user,
            document_count=1,
        )
    )
    assert second_assessment_id != assessment_id
    storage = _FakeBidUploadStorage()
    first_replace = _upload_file(
        client,
        batch_location=second_batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("replace-one"),
        filename="替换一.pdf",
        operation="replace",
        replace_document_id=second_documents[0],
    )
    second_replace = _upload_file(
        client,
        batch_location=second_batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
        content=_pdf_bytes("replace-two"),
        filename="替换二.pdf",
        operation="replace",
        replace_document_id=second_documents[0],
    )
    conflict = _commit_upload_batch(
        client,
        batch_location=second_batch.headers["location"],
        batch_etag=second_replace.headers["x-batch-etag"],
        expected_file_count=2,
    )
    assert first_replace.status_code == second_replace.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["error_code"] == "BID_UPLOAD_BATCH_MERGE_CONFLICT"
    assert "同一逻辑文档不能在一个批次中被替换多次" in conflict.json()["error"]["details"]["reasons"]


def test_api15_hides_cross_user_and_disabled_resources_but_allows_admin(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=_FakeBidUploadStorage(),
        monkeypatch=monkeypatch,
    )
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=1,
    )
    assert hidden.status_code == 404

    client.app.state.active_user["value"] = owner
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=1,
    )
    assert disabled.status_code == 404
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)

    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    committed = _commit_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        expected_file_count=1,
    )
    assert committed.status_code == 202


def test_api16_atomically_abandons_without_inline_storage_delete_and_projects_sse(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    delete_calls_before = list(storage.delete_calls)

    response = _abandon_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
        reason="  用户重新整理资料  ",
    )

    assert response.status_code == 200
    assert response.headers["location"] == batch.headers["location"]
    assert response.headers["x-resource-version"] == "3"
    assert response.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in response.headers
    _validate_contract("UploadBatchResponse", response.json())
    snapshot = response.json()["data"]
    assert snapshot["status"] == "abandoned"
    assert snapshot["abandon_reason"] == "用户重新整理资料"
    assert snapshot["abandoned_at"] is not None
    assert snapshot["cleanup_after"] is not None
    assert snapshot["cleanup_completed_at"] is None
    assert snapshot["validation"]["can_commit"] is False
    abandoned_at = datetime.fromisoformat(
        snapshot["abandoned_at"].replace("Z", "+00:00")
    )
    cleanup_after = datetime.fromisoformat(
        snapshot["cleanup_after"].replace("Z", "+00:00")
    )
    assert cleanup_after - abandoned_at == timedelta(days=1)
    assert storage.delete_calls == delete_calls_before
    assert len(storage.objects) == 1

    db = session_factory()
    try:
        batch_row = db.query(BidUploadBatch).one()
        batch_file = db.query(BidUploadBatchFile).one()
        event_row = (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.event_type == "bid.upload_batch.abandoned.v1")
            .one()
        )
        assert batch_row.open_slot_key is None
        assert batch_file.file_object_id is not None
        assert batch_file.temporary_object_ref is not None
        assert db.query(BidFileObject).count() == 1
        assert (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_batch.abandon")
            .count()
            == 1
        )
        event_id = event_row.event_id
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            projection = project_outbox_event_to_public(db, event_id=event_id)
        assert projection.duplicate is False
    finally:
        db.close()
    db = session_factory()
    try:
        public = (
            db.query(BidPublicEvent)
            .filter(BidPublicEvent.source_event_id == event_id)
            .one()
        )
        assert public.event_type == "upload_batch.changed"
        assert public.payload_json["status"] == "abandoned"
        assert public.resource_version == 3
    finally:
        db.close()


def test_api16_reason_etag_and_idempotency_are_frozen(api_runtime) -> None:
    client, _session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    location = batch.headers["location"]
    key = _key()

    missing_reason = client.post(
        f"{location}/abandon",
        headers={"Idempotency-Key": _key(), "If-Match": batch.headers["etag"]},
        json={},
    )
    blank_reason = client.post(
        f"{location}/abandon",
        headers={"Idempotency-Key": _key(), "If-Match": batch.headers["etag"]},
        json={"reason": "   "},
    )
    trimmed_max_reason = " " + ("x" * 500) + " "
    stale = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag='"bid-upload-batch:stale:99:000000000000"',
    )
    first = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=batch.headers["etag"],
        reason=trimmed_max_reason,
        idempotency_key=key,
    )
    replay = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=batch.headers["etag"],
        reason="x" * 500,
        idempotency_key=key,
    )
    reused = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=batch.headers["etag"],
        reason="另一个原因",
        idempotency_key=key,
    )
    already_abandoned = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=first.headers["etag"],
    )

    assert missing_reason.status_code == blank_reason.status_code == 422
    assert stale.status_code == 412
    assert stale.json()["error"]["error_code"] == "BID_RESOURCE_VERSION_MISMATCH"
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replay"] == "true"
    assert reused.status_code == 409
    assert reused.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    assert already_abandoned.status_code == 409
    assert already_abandoned.json()["error"]["error_code"] == (
        "BID_UPLOAD_BATCH_ALREADY_ABANDONED"
    )


def test_api16_terminal_visibility_and_feature_gates(api_runtime) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    location = batch.headers["location"]
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=batch.headers["etag"],
    )
    assert hidden.status_code == 404

    client.app.state.active_user["value"] = owner
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    disabled = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=batch.headers["etag"],
    )
    assert disabled.status_code == 404
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)

    db = session_factory()
    try:
        with db.begin():
            row = db.query(BidUploadBatch).one()
            row.status = "committing"
            row.row_version = int(row.row_version) + 1
    finally:
        db.close()
    latest = client.get(location)
    committing = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=latest.headers["etag"],
    )
    assert committing.status_code == 409
    assert committing.json()["error"]["error_code"] == (
        "BID_UPLOAD_BATCH_ALREADY_COMMITTED"
    )

    db = session_factory()
    try:
        with db.begin():
            row = db.query(BidUploadBatch).one()
            row.status = "expired"
            row.open_slot_key = None
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            row.row_version = int(row.row_version) + 1
    finally:
        db.close()
    latest = client.get(location)
    expired = _abandon_upload_batch(
        client,
        batch_location=location,
        batch_etag=latest.headers["etag"],
    )
    assert expired.status_code == 409
    assert expired.json()["error"]["error_code"] == "BID_UPLOAD_BATCH_NOT_READY"
    assert expired.json()["error"]["details"] == {
        "batch_status": "expired",
        "expired": True,
    }


def test_api16_cleanup_waits_for_grace_commits_before_delete_and_retries_orphan(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    storage = _FakeBidUploadStorage()
    uploaded = _upload_file(
        client,
        batch_location=batch.headers["location"],
        storage=storage,
        monkeypatch=monkeypatch,
    )
    abandoned = _abandon_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=uploaded.headers["x-batch-etag"],
    )
    cleanup_after = datetime.fromisoformat(
        abandoned.json()["data"]["cleanup_after"].replace("Z", "+00:00")
    )

    early = cleanup_due_abandoned_upload_batches(
        session_factory=session_factory,
        storage=storage,
        now=cleanup_after - timedelta(microseconds=1),
    )
    assert early.scanned_batches == early.released_batches == 0
    assert len(storage.objects) == 1

    original_cleanup_audit = upload_cleanup_commands.append_audit_log
    monkeypatch.setattr(
        upload_cleanup_commands,
        "append_audit_log",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("forced cleanup audit failure")
        ),
    )
    with pytest.raises(RuntimeError, match="forced cleanup audit failure"):
        cleanup_due_abandoned_upload_batches(
            session_factory=session_factory,
            storage=storage,
            now=cleanup_after,
        )
    db = session_factory()
    try:
        assert db.query(BidFileObject).count() == 1
        assert db.query(BidUploadBatch).one().cleanup_completed_at is None
        assert db.query(BidUploadBatchFile).one().file_object_id is not None
    finally:
        db.close()
    assert len(storage.objects) == 1
    monkeypatch.setattr(
        upload_cleanup_commands,
        "append_audit_log",
        original_cleanup_audit,
    )

    observed_after_commit: list[tuple[int, bool]] = []
    original_delete = storage.delete

    def _failing_delete_after_observation(*, object_key: str) -> None:
        db = session_factory()
        try:
            observed_after_commit.append(
                (
                    db.query(BidFileObject).count(),
                    db.query(BidUploadBatch).one().cleanup_completed_at is not None,
                )
            )
        finally:
            db.close()
        raise RuntimeError(f"forced delete failure: {object_key}")

    storage.delete = _failing_delete_after_observation
    due = cleanup_due_abandoned_upload_batches(
        session_factory=session_factory,
        storage=storage,
        now=cleanup_after,
    )
    assert due.released_batches == 1
    assert due.detached_files == 1
    assert due.removed_file_objects == 1
    assert due.deleted_objects == 0
    assert due.delete_failed == 1
    assert observed_after_commit == [(0, True)]
    assert len(storage.objects) == 1

    db = session_factory()
    try:
        batch_row = db.query(BidUploadBatch).one()
        file_row = db.query(BidUploadBatchFile).one()
        assert batch_row.row_version == 4
        assert batch_row.cleanup_completed_at is not None
        assert file_row.row_version == 2
        assert file_row.file_object_id is None
        assert file_row.temporary_object_ref is None
        assert (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "upload_batch.cleanup_references")
            .count()
            == 1
        )
    finally:
        db.close()

    storage.delete = original_delete
    db = session_factory()
    try:
        retried = cleanup_orphaned_bid_upload_objects(
            db,
            storage=storage,
            now=cleanup_after + timedelta(days=2),
        )
    finally:
        db.close()
    assert retried.deleted == 1
    assert storage.objects == {}


def test_api16_cleanup_preserves_shared_object_until_last_batch_releases(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    storage = _FakeBidUploadStorage()
    content = _pdf_bytes("shared-api16")
    batches = []
    for _ in range(2):
        assessment = _create_assessment(client)
        batch = _create_initial_upload_batch(
            client,
            assessment_id=assessment.json()["data"]["assessment_id"],
        )
        uploaded = _upload_file(
            client,
            batch_location=batch.headers["location"],
            storage=storage,
            monkeypatch=monkeypatch,
            content=content,
        )
        abandoned = _abandon_upload_batch(
            client,
            batch_location=batch.headers["location"],
            batch_etag=uploaded.headers["x-batch-etag"],
        )
        batches.append(abandoned)

    first_id = batches[0].json()["data"]["batch_id"]
    second_id = batches[1].json()["data"]["batch_id"]
    first_due = datetime.fromisoformat(
        batches[0].json()["data"]["cleanup_after"].replace("Z", "+00:00")
    )
    second_due = first_due + timedelta(days=1)
    db = session_factory()
    try:
        with db.begin():
            second = db.query(BidUploadBatch).filter(BidUploadBatch.id == second_id).one()
            second.cleanup_after = second_due
    finally:
        db.close()
    storage.delete_calls.clear()
    assert len(storage.objects) == 1

    first_cleanup = cleanup_due_abandoned_upload_batches(
        session_factory=session_factory,
        storage=storage,
        now=first_due,
    )
    assert first_cleanup.released_batches == 1
    assert first_cleanup.removed_file_objects == 0
    assert first_cleanup.deleted_objects == 0
    assert first_cleanup.preserved_references >= 1
    assert storage.delete_calls == []
    assert len(storage.objects) == 1
    db = session_factory()
    try:
        first_file = (
            db.query(BidUploadBatchFile)
            .filter(BidUploadBatchFile.batch_id == first_id)
            .one()
        )
        second_file = (
            db.query(BidUploadBatchFile)
            .filter(BidUploadBatchFile.batch_id == second_id)
            .one()
        )
        assert first_file.file_object_id is None
        assert second_file.file_object_id is not None
        assert db.query(BidFileObject).count() == 1
    finally:
        db.close()

    second_cleanup = cleanup_due_abandoned_upload_batches(
        session_factory=session_factory,
        storage=storage,
        now=second_due,
    )
    assert second_cleanup.released_batches == 1
    assert second_cleanup.removed_file_objects == 1
    assert second_cleanup.deleted_objects == 1
    assert len(storage.delete_calls) == 1
    assert storage.objects == {}
    db = session_factory()
    try:
        assert db.query(BidFileObject).count() == 0
        assert db.query(BidUploadBatchFile).count() == 2
    finally:
        db.close()


def test_api16_rolls_back_business_outbox_audit_and_idempotency_together(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    batch = _create_initial_upload_batch(
        client,
        assessment_id=assessment.json()["data"]["assessment_id"],
    )
    before = _upload_counts(session_factory)
    monkeypatch.setattr(
        upload_abandon_commands,
        "append_audit_log",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("forced audit failure")
        ),
    )

    response = _abandon_upload_batch(
        client,
        batch_location=batch.headers["location"],
        batch_etag=batch.headers["etag"],
    )

    assert response.status_code == 503
    assert _upload_counts(session_factory) == before
    db = session_factory()
    try:
        row = db.query(BidUploadBatch).one()
        assert row.status == "draft"
        assert row.open_slot_key == "initial"
        assert row.abandon_reason is None
        assert row.abandoned_at is None
        assert row.cleanup_after is None
        assert (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.event_type == "bid.upload_batch.abandoned.v1")
            .count()
            == 0
        )
    finally:
        db.close()


def _attach_replacement_manifest(
    session_factory,
    *,
    assessment_id: str,
    document_id: str,
    actor_id: int,
    filename: str = "招标文件-修订版.pdf",
) -> tuple[str, str]:
    file_object_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    manifest_id = str(uuid.uuid4())
    db = session_factory()
    try:
        with db.begin():
            db.add(
                BidFileObject(
                    id=file_object_id,
                    sha256="b" * 64,
                    object_key=f"private/object/{file_object_id}",
                    size_bytes=32,
                    mime_type="application/pdf",
                    storage_status="available",
                    storage_etag="private-storage-etag",
                    created_by=actor_id,
                    row_version=1,
                )
            )
            db.flush()
            db.add(
                BidDocumentVersion(
                    id=version_id,
                    document_id=document_id,
                    file_object_id=file_object_id,
                    version_no=2,
                    original_filename=filename,
                    parser_hint="private-parser-hint",
                    source_metadata_hash="c" * 64,
                    source_metadata_json={"private_source": "must-not-leak"},
                    created_by=actor_id,
                )
            )
            db.add(
                BidDocumentManifest(
                    id=manifest_id,
                    assessment_id=assessment_id,
                    version=2,
                    manifest_hash="e" * 64,
                    committed_by=actor_id,
                )
            )
            db.flush()
            db.add(
                BidManifestDocument(
                    manifest_id=manifest_id,
                    document_version_id=version_id,
                    role="tender_document",
                    order_no=0,
                )
            )
            assessment = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == assessment_id)
                .one()
            )
            assessment.current_manifest_id = manifest_id
            assessment.row_version = int(assessment.row_version) + 1
        return manifest_id, version_id
    finally:
        db.close()


def test_api20_empty_current_manifest_contract_and_private_conditional_cache(
    api_runtime,
) -> None:
    client, _session_factory, _user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]

    response = client.get(f"/api/v1/bid-assessments/{assessment_id}/documents")

    assert response.status_code == 200
    _validate_contract("DocumentPageResponse", response.json())
    assert response.json()["data"] == []
    assert response.json()["total"] == 0
    assert response.json()["manifest"] is None
    assert response.json()["current_manifest_id"] is None
    assert response.json()["manifest_selection"] == "current"
    assert response.headers["cache-control"] == (
        "private, no-cache, max-age=0, must-revalidate"
    )
    assert response.headers["vary"] == "Authorization"
    assert response.headers["x-resource-version"] == "1"
    assert response.headers["etag"].startswith(
        f'"bid-document-page:{assessment_id}:'
    )

    unchanged = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        headers={"If-None-Match": f'W/{response.headers["etag"]}'},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == response.headers["etag"]
    assert unchanged.headers["cache-control"] == response.headers["cache-control"]


def test_api20_stable_filter_before_pagination_and_storage_safe_projection(
    api_runtime,
) -> None:
    client, session_factory, user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=user.id,
    )
    document_ids = [
        _attach_manifest_document(
            session_factory,
            manifest_id=manifest_id,
            actor_id=user.id,
        )
        for _ in range(3)
    ]
    before_read = _deactivation_counts(session_factory)

    first = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        params={"page": "1", "page_size": "2", "include_versions": "false"},
    )
    second = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        params={"page": "2", "page_size": "2", "include_versions": "false"},
    )

    assert first.status_code == second.status_code == 200
    _validate_contract("DocumentPageResponse", first.json())
    _validate_contract("DocumentPageResponse", second.json())
    assert first.json()["total"] == second.json()["total"] == 3
    assert [item["order_no"] for item in first.json()["data"]] == [0, 1]
    assert [item["order_no"] for item in second.json()["data"]] == [2]
    returned_ids = [
        item["document_id"]
        for item in first.json()["data"] + second.json()["data"]
    ]
    assert returned_ids == document_ids
    for item in first.json()["data"] + second.json()["data"]:
        assert item["selected_version"] == item["current_version"]
        assert item["is_in_current_manifest"] is True
        assert item["parse_status"] == "not_requested"
        assert item["parse_quality"] is None
        assert item["warnings"] == []
        assert item["versions"] is None
        assert len(item["selected_version"]["sha256_prefix"]) == 12

    serialized = json.dumps(first.json(), ensure_ascii=False)
    for forbidden in {
        "object_key",
        "storage_etag",
        "file_object_id",
        "source_metadata_hash",
        "source_metadata_json",
        "parser_hint",
        "logical_identity_key",
        "created_by",
        "existing-etag",
        "bid-assessment/content/v1",
    }:
        assert forbidden not in serialized

    matching = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        params={
            "document_type": "tender_document",
            "parse_status": "not_requested",
        },
    )
    future_parse_state = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        params={"parse_status": "succeeded"},
    )
    wrong_type = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        params={"document_type": "drawing"},
    )
    assert matching.json()["total"] == 3
    assert future_parse_state.json()["total"] == 0
    assert wrong_type.json()["total"] == 0
    assert _deactivation_counts(session_factory) == before_read


def test_api20_historical_projection_and_version_chain_are_assessment_scoped(
    api_runtime,
) -> None:
    client, session_factory, user = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    base_manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=user.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=base_manifest_id,
        actor_id=user.id,
    )
    current_manifest_id, current_version_id = _attach_replacement_manifest(
        session_factory,
        assessment_id=assessment_id,
        document_id=document_id,
        actor_id=user.id,
    )

    historical = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        params={"manifest_id": base_manifest_id, "include_versions": "true"},
    )
    current = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/documents",
        params={"include_versions": "true"},
    )

    assert historical.status_code == current.status_code == 200
    _validate_contract("DocumentPageResponse", historical.json())
    old_item = historical.json()["data"][0]
    current_item = current.json()["data"][0]
    old_version_id = old_item["selected_version"]["version_id"]
    assert historical.json()["manifest_selection"] == "explicit"
    assert historical.json()["manifest"]["is_current"] is False
    assert historical.json()["current_manifest_id"] == current_manifest_id
    assert old_item["selected_version"]["version_no"] == 1
    assert old_item["current_version"]["version_id"] == current_version_id
    assert old_item["is_in_current_manifest"] is False
    assert old_item["replacement_chain"] == {
        "previous_version_id": None,
        "next_version_id": current_version_id,
        "latest_version_id": current_version_id,
        "visible_version_count": 2,
    }
    assert [version["version_id"] for version in old_item["versions"]] == [
        old_version_id,
        current_version_id,
    ]
    assert current_item["selected_version"]["version_id"] == current_version_id
    assert current_item["current_version"]["version_id"] == current_version_id
    assert current_item["is_in_current_manifest"] is True
    assert current_item["replacement_chain"]["previous_version_id"] == (
        old_version_id
    )
    assert "must-not-leak" not in json.dumps(historical.json())
    assert "private-storage-etag" not in json.dumps(current.json())


def test_api20_hides_foreign_manifest_versions_and_invalid_queries(
    api_runtime,
) -> None:
    client, session_factory, user = api_runtime
    first = _create_assessment(client)
    first_id = first.json()["data"]["assessment_id"]
    first_manifest = _attach_current_manifest(
        session_factory,
        assessment_id=first_id,
        actor_id=user.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=first_manifest,
        actor_id=user.id,
    )
    second = _create_assessment(client)
    second_id = second.json()["data"]["assessment_id"]
    second_manifest = _attach_current_manifest(
        session_factory,
        assessment_id=second_id,
        actor_id=user.id,
    )

    # A later version of the same enterprise-level logical Document is visible
    # only through the second Assessment and must not enter the first chain.
    foreign_file_id = str(uuid.uuid4())
    foreign_version_id = str(uuid.uuid4())
    db = session_factory()
    try:
        with db.begin():
            db.add(
                BidFileObject(
                    id=foreign_file_id,
                    sha256="f" * 64,
                    object_key=f"foreign/private/{foreign_file_id}",
                    size_bytes=64,
                    mime_type="application/pdf",
                    storage_status="available",
                    storage_etag="foreign-etag",
                    created_by=user.id,
                    row_version=1,
                )
            )
            db.flush()
            db.add(
                BidDocumentVersion(
                    id=foreign_version_id,
                    document_id=document_id,
                    file_object_id=foreign_file_id,
                    version_no=2,
                    original_filename="其他 Assessment 版本.pdf",
                    source_metadata_hash="f" * 64,
                    source_metadata_json={},
                    created_by=user.id,
                )
            )
            db.flush()
            db.add(
                BidManifestDocument(
                    manifest_id=second_manifest,
                    document_version_id=foreign_version_id,
                    role="tender_document",
                    order_no=0,
                )
            )
    finally:
        db.close()

    scoped = client.get(
        f"/api/v1/bid-assessments/{first_id}/documents",
        params={"include_versions": "true"},
    )
    foreign_manifest = client.get(
        f"/api/v1/bid-assessments/{first_id}/documents",
        params={"manifest_id": second_manifest},
    )
    assert scoped.status_code == 200
    assert len(scoped.json()["data"][0]["versions"]) == 1
    assert foreign_version_id not in json.dumps(scoped.json())
    assert foreign_manifest.status_code == 404
    assert foreign_manifest.json()["error"]["error_code"] == (
        "BID_RESOURCE_NOT_FOUND"
    )

    invalid = client.get(
        f"/api/v1/bid-assessments/{first_id}/documents",
        params={
            "manifest_id": "bad id",
            "document_type": "Tender Document",
            "parse_status": "ready",
            "include_versions": "1",
            "page": "0",
            "page_size": "101",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["error_code"] == (
        "BID_REQUEST_VALIDATION_FAILED"
    )
    assert {row["field"] for row in invalid.json()["error"]["field_errors"]} == {
        "manifest_id",
        "document_type",
        "parse_status",
        "include_versions",
        "page",
        "page_size",
    }


def test_api20_hides_cross_user_and_disabled_resources_but_allows_admin(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )

    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = client.get(f"/api/v1/bid-assessments/{assessment_id}/documents")
    assert hidden.status_code == 404
    assert hidden.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"

    client.app.state.active_user["value"] = owner
    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    try:
        disabled = client.get(
            f"/api/v1/bid-assessments/{assessment_id}/documents"
        )
        assert disabled.status_code == 404
    finally:
        object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)

    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    visible = client.get(f"/api/v1/bid-assessments/{assessment_id}/documents")
    assert visible.status_code == 200
    assert visible.json()["total"] == 1


def _document_version_fixture(
    session_factory,
    *,
    document_id: str,
) -> dict[str, str | int]:
    db = session_factory()
    try:
        version, file_object = (
            db.query(BidDocumentVersion, BidFileObject)
            .join(
                BidFileObject,
                BidFileObject.id == BidDocumentVersion.file_object_id,
            )
            .filter(BidDocumentVersion.document_id == document_id)
            .order_by(BidDocumentVersion.version_no.asc())
            .first()
        )
        return {
            "version_id": str(version.id),
            "file_object_id": str(file_object.id),
            "object_key": str(file_object.object_key),
            "size_bytes": int(file_object.size_bytes),
        }
    finally:
        db.close()


def test_api21_manifest_authorization_source_redaction_parse_placeholder_and_etag(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    fixture = _document_version_fixture(
        session_factory,
        document_id=document_id,
    )
    version_id = str(fixture["version_id"])

    db = session_factory()
    try:
        with db.begin():
            version = (
                db.query(BidDocumentVersion)
                .filter(BidDocumentVersion.id == version_id)
                .one()
            )
            version.source_metadata_json = {
                "source": "bid_upload_batch",
                "operation": "add",
                "relative_path": "招标资料/原招标文件.pdf",
                "batch_id": "secret-batch",
                "batch_file_id": "secret-file",
                "client_file_id": "secret-client",
                "replace_document_id": "secret-document",
                "sha256": "secret-hash-copy",
            }
    finally:
        db.close()

    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: (_ for _ in ()).throw(AssertionError("API-21 touched storage")),
    )
    response = client.get(f"/api/v1/bid-document-versions/{version_id}")

    assert response.status_code == 200
    _validate_contract("DocumentVersionResponse", response.json())
    detail = response.json()["data"]
    assert detail["version_id"] == version_id
    assert detail["document"] == {
        "document_id": document_id,
        "logical_name": "原招标文件",
        "document_type": "tender_document",
    }
    assert detail["sha256"] == str(fixture["file_object_id"]).replace("-", "") * 2
    assert detail["upload_source"] == {
        "source_type": "upload_batch",
        "operation": "add",
        "relative_path": "招标资料/原招标文件.pdf",
    }
    assert detail["parse_summary"] == {
        "status": "not_requested",
        "latest_run_id": None,
        "requested_at": None,
        "started_at": None,
        "finished_at": None,
        "quality": None,
        "warnings": [],
    }
    assert detail["manifest_references"] == [
        {
            "assessment_id": assessment_id,
            "assessment_url": f"/api/v1/bid-assessments/{assessment_id}",
            "manifest_id": manifest_id,
            "manifest_version": 1,
            "is_current_manifest": True,
            "role": "tender_document",
            "order_no": 0,
        }
    ]
    assert detail["allowed_actions"] == {
        "download": True,
        "download_url": f"/api/v1/bid-document-versions/{version_id}/download",
    }
    assert response.headers["cache-control"] == (
        "private, no-cache, max-age=0, must-revalidate"
    )
    assert response.headers["vary"] == "Authorization"
    assert response.headers["x-resource-version"] == "1"
    assert response.headers["etag"].startswith(
        f'"bid-document-version:{version_id}:'
    )

    unchanged = client.get(
        f"/api/v1/bid-document-versions/{version_id}",
        headers={"If-None-Match": f'W/{response.headers["etag"]}'},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == response.headers["etag"]

    serialized = json.dumps(response.json(), ensure_ascii=False)
    for forbidden in {
        "object_key",
        "storage_etag",
        "storage_status",
        "file_object_id",
        "source_metadata_hash",
        "source_metadata_json",
        "parser_hint",
        "logical_identity_key",
        "created_by",
        "secret-batch",
        "secret-file",
        "secret-client",
        "secret-document",
        "secret-hash-copy",
        str(fixture["object_key"]),
    }:
        assert forbidden not in serialized


def test_api21_filters_manifest_references_and_uses_uniform_not_found(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    owner_assessment = _create_assessment(client)
    owner_assessment_id = owner_assessment.json()["data"]["assessment_id"]
    owner_manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=owner_assessment_id,
        actor_id=owner.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=owner_manifest_id,
        actor_id=owner.id,
    )
    version_id = str(
        _document_version_fixture(
            session_factory,
            document_id=document_id,
        )["version_id"]
    )

    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    outsider_assessment = _create_assessment(client)
    outsider_assessment_id = outsider_assessment.json()["data"]["assessment_id"]
    outsider_manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=outsider_assessment_id,
        actor_id=outsider.id,
    )
    db = session_factory()
    try:
        with db.begin():
            db.add(
                BidManifestDocument(
                    manifest_id=outsider_manifest_id,
                    document_version_id=version_id,
                    role="reference_document",
                    order_no=0,
                )
            )
    finally:
        db.close()

    outsider_view = client.get(f"/api/v1/bid-document-versions/{version_id}")
    assert outsider_view.status_code == 200
    assert {
        reference["assessment_id"]
        for reference in outsider_view.json()["data"]["manifest_references"]
    } == {outsider_assessment_id}

    unrelated = _create_user(session_factory)
    client.app.state.active_user["value"] = unrelated
    hidden = client.get(f"/api/v1/bid-document-versions/{version_id}")
    invalid = client.get("/api/v1/bid-document-versions/bad%20id")
    missing = client.get(f"/api/v1/bid-document-versions/{uuid.uuid4()}")
    assert hidden.status_code == invalid.status_code == missing.status_code == 404
    assert hidden.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    assert invalid.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    assert missing.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"

    admin = _create_user(session_factory, role="admin")
    client.app.state.active_user["value"] = admin
    admin_view = client.get(f"/api/v1/bid-document-versions/{version_id}")
    assert admin_view.status_code == 200
    assert {
        reference["assessment_id"]
        for reference in admin_view.json()["data"]["manifest_references"]
    } == {owner_assessment_id, outsider_assessment_id}


def test_api22_reauthorizes_streams_full_file_and_sanitizes_headers(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    fixture = _document_version_fixture(
        session_factory,
        document_id=document_id,
    )
    version_id = str(fixture["version_id"])
    content = b"0123456789abcdef"

    db = session_factory()
    try:
        with db.begin():
            version = (
                db.query(BidDocumentVersion)
                .filter(BidDocumentVersion.id == version_id)
                .one()
            )
            version.original_filename = "../危险\r\nX-Injected: yes.pdf"
            file_object = (
                db.query(BidFileObject)
                .filter(BidFileObject.id == fixture["file_object_id"])
                .one()
            )
            file_object.size_bytes = len(content)
            file_object.mime_type = "application/pdf"
    finally:
        db.close()

    storage = _FakeBidUploadStorage()
    storage.objects[str(fixture["object_key"])] = content
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: storage,
    )

    response = client.get(
        f"/api/v1/bid-document-versions/{version_id}/download",
        headers={"Range": "bytes=2-5"},
    )

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Authorization"
    assert response.headers["accept-ranges"] == "none"
    assert response.headers["content-security-policy"] == "sandbox"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert "\r" not in disposition and "\n" not in disposition
    assert "X-Injected:" not in disposition
    assert storage.open_read_calls == [str(fixture["object_key"])]
    assert len(storage.opened_streams) == 1
    assert storage.opened_streams[0].closed is True
    assert "minio" not in json.dumps(dict(response.headers)).lower()
    assert str(fixture["object_key"]) not in json.dumps(dict(response.headers))


def test_api22_blocks_cross_user_before_storage_and_maps_storage_failure(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    fixture = _document_version_fixture(
        session_factory,
        document_id=document_id,
    )
    version_id = str(fixture["version_id"])
    storage = _FakeBidUploadStorage()
    storage.fail_open_read = True
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: storage,
    )

    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = client.get(
        f"/api/v1/bid-document-versions/{version_id}/download"
    )
    assert hidden.status_code == 404
    assert storage.open_read_calls == []

    client.app.state.active_user["value"] = owner
    failed = client.get(
        f"/api/v1/bid-document-versions/{version_id}/download"
    )
    assert failed.status_code == 503
    assert failed.json()["error"]["error_code"] == "BID_STORAGE_UNAVAILABLE"
    assert str(fixture["object_key"]) not in json.dumps(failed.json())
    assert storage.open_read_calls == [str(fixture["object_key"])]

    db = session_factory()
    try:
        with db.begin():
            file_object = (
                db.query(BidFileObject)
                .filter(BidFileObject.id == fixture["file_object_id"])
                .one()
            )
            file_object.storage_status = "missing"
    finally:
        db.close()
    storage.open_read_calls.clear()
    unavailable = client.get(
        f"/api/v1/bid-document-versions/{version_id}/download"
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["error_code"] == (
        "BID_STORAGE_UNAVAILABLE"
    )
    assert storage.open_read_calls == []

    detail = client.get(f"/api/v1/bid-document-versions/{version_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["allowed_actions"] == {
        "download": False,
        "download_url": None,
    }


def test_api21_api22_feature_gate_and_download_mime_fallback(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    fixture = _document_version_fixture(
        session_factory,
        document_id=document_id,
    )
    version_id = str(fixture["version_id"])
    db = session_factory()
    try:
        with db.begin():
            file_object = (
                db.query(BidFileObject)
                .filter(BidFileObject.id == fixture["file_object_id"])
                .one()
            )
            file_object.mime_type = "text/html; charset=utf-8\r\nX-Test: injected"
    finally:
        db.close()

    storage = _FakeBidUploadStorage()
    storage.objects[str(fixture["object_key"])] = b"0123456789abcdef"
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: storage,
    )
    fallback = client.get(
        f"/api/v1/bid-document-versions/{version_id}/download"
    )
    assert fallback.status_code == 200
    assert fallback.headers["content-type"] == "application/octet-stream"
    assert "X-Test" not in json.dumps(dict(fallback.headers))

    object.__setattr__(settings, "feature_bid_assessment_v1_runtime", False)
    try:
        hidden_detail = client.get(f"/api/v1/bid-document-versions/{version_id}")
        hidden_download = client.get(
            f"/api/v1/bid-document-versions/{version_id}/download"
        )
    finally:
        object.__setattr__(settings, "feature_bid_assessment_v1_runtime", True)
    assert hidden_detail.status_code == hidden_download.status_code == 404
    assert hidden_detail.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    assert hidden_download.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"


def _attach_phase2_lot_candidate(
    session_factory,
    *,
    assessment_id: str,
    manifest_id: str,
    document_id: str,
) -> tuple[str, str, str]:
    fixture = _document_version_fixture(
        session_factory,
        document_id=document_id,
    )
    version_id = str(fixture["version_id"])
    parse_run_id = f"dpr_{uuid.uuid4().hex}"
    parse_unit_id = f"dpu_{uuid.uuid4().hex}"
    evidence_id = f"bef_{uuid.uuid4().hex}"
    detection_run_id = f"ldr_{uuid.uuid4().hex}"
    lot_id = f"lot_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            assessment = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == assessment_id)
                .one()
            )
            assessment.business_status = "awaiting_lot_selection"
            assessment.row_version = int(assessment.row_version) + 1
            db.add(
                BidDocumentParseRun(
                    id=parse_run_id,
                    document_version_id=version_id,
                    parser_profile_version="bid-document-parser-profile-v1",
                    input_hash="1" * 64,
                    status="succeeded",
                    retryable=False,
                    requested_at=now - timedelta(seconds=2),
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    result_hash="2" * 64,
                    quality_grade="high",
                    quality_score=95,
                    page_count=1,
                    sheet_count=0,
                    ocr_status="not_applicable",
                    warning_count=0,
                    warnings_json=[],
                    row_version=2,
                )
            )
            db.flush()
            db.add(
                BidDocumentParseHead(
                    document_version_id=version_id,
                    current_run_id=parse_run_id,
                    row_version=1,
                )
            )
            db.add(
                BidDocumentParseUnit(
                    id=parse_unit_id,
                    run_id=parse_run_id,
                    unit_type="page",
                    unit_key="page:1",
                    ordinal=0,
                    page_no=1,
                    content_source="native",
                    status="succeeded",
                    text_hash="3" * 64,
                    text_length=18,
                    ocr_status="not_applicable",
                )
            )
            db.flush()
            db.add(
                BidEvidenceFragment(
                    id=evidence_id,
                    parse_run_id=parse_run_id,
                    document_version_id=version_id,
                    parse_unit_id=parse_unit_id,
                    locator_type="section",
                    locator_json={"page_no": 1, "section_index": 0},
                    locator_hash="4" * 64,
                    normalized_text="第一标段：室内装饰工程",
                    text_hash="5" * 64,
                    ordinal=0,
                )
            )
            db.flush()
            parse_set = build_manifest_parse_set(db, manifest_id=manifest_id)
            assert parse_set.status == "ready"
            db.add(
                BidLotDetectionRun(
                    id=detection_run_id,
                    manifest_id=manifest_id,
                    parse_set_hash=parse_set.parse_set_hash,
                    detector_version="bid-lot-detector-rules-v1",
                    rule_set_version="bid-lot-rules-v1",
                    normalizer_version="bid-lot-normalizer-v1",
                    input_hash="6" * 64,
                    status="succeeded",
                    retryable=False,
                    requested_at=now - timedelta(seconds=2),
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    result_hash="7" * 64,
                    candidate_count=1,
                    warnings_json=[],
                    row_version=2,
                )
            )
            db.flush()
            db.add(
                BidLotDetectionHead(
                    manifest_id=manifest_id,
                    current_run_id=detection_run_id,
                    row_version=1,
                )
            )
            db.add(
                BidLotCandidate(
                    id=lot_id,
                    manifest_id=manifest_id,
                    detection_run_id=detection_run_id,
                    lot_code="1",
                    lot_name="室内装饰工程",
                    scope_summary="正文明确列示第一标段",
                    normalized_lot_key="标段:1",
                    source_status="detected",
                    confidence=Decimal("0.900000"),
                    confidence_level="high",
                    candidate_hash="8" * 64,
                    warnings_json=[],
                )
            )
            db.flush()
            db.add(
                BidLotCandidateEvidence(
                    lot_candidate_id=lot_id,
                    evidence_id=evidence_id,
                    manifest_id=manifest_id,
                    document_version_id=version_id,
                    support_role="identity",
                    display_order=0,
                    display_label="第1页",
                )
            )
        return lot_id, detection_run_id, evidence_id
    finally:
        db.close()


PHASE3E_SCOPE_SIGNING_KEY = "phase3e-test-scope-signing-key-32-bytes-minimum"


def test_phase3e_tool_argument_schema_is_strict_and_scope_free() -> None:
    assert validate_tool_arguments(
        "facts.query",
        {"fact_slots": ["tender.deadline"]},
    ) == {"fact_slots": ["tender.deadline"]}
    with pytest.raises(BidToolArgumentsInvalid):
        validate_tool_arguments(
            "facts.query",
            {"fact_slots": ["tender.deadline"], "assessment_id": "injected"},
        )
    with pytest.raises(BidToolArgumentsInvalid):
        validate_tool_arguments("unknown.tool", {})


def test_phase3e_context_tool_sync_fencing_budget_and_idempotency(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3e-sync", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now)
            first_context = assemble_context_manifest(
                db,
                claim,
                working_state={"step": "start"},
                now=now,
            )
            replay_context = assemble_context_manifest(
                db,
                claim,
                working_state={"step": "start"},
                now=now,
            )
            assert replay_context.duplicate is True
            assert replay_context.context_manifest_id == first_context.context_manifest_id
            decision = authorize_tool_invocation(
                db,
                claim,
                context_manifest_id=first_context.context_manifest_id,
                tool_name="facts.query",
                arguments={"fact_slots": ["tender.deadline"]},
                idempotency_key="phase3e-sync-key-0001",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            assert verify_tool_scope_token(
                db,
                invocation_id=decision.invocation_id,
                scope_token=decision.call_envelope["scope_token"],
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
            )
            replay = authorize_tool_invocation(
                db,
                claim,
                context_manifest_id=first_context.context_manifest_id,
                tool_name="facts.query",
                arguments={"fact_slots": ["tender.deadline"]},
                idempotency_key="phase3e-sync-key-0001",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            assert replay.duplicate is True
            with pytest.raises(BidToolInvocationConflict):
                authorize_tool_invocation(
                    db,
                    claim,
                    context_manifest_id=first_context.context_manifest_id,
                    tool_name="facts.query",
                    arguments={"fact_slots": ["tender.amount"]},
                    idempotency_key="phase3e-sync-key-0001",
                    scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                    now=now,
                )
            receipt = complete_tool_invocation(
                db,
                claim,
                invocation_id=decision.invocation_id,
                status="ok",
                summary="No resolved facts yet",
                data=[],
                returned_items=0,
                now=now,
            )
            result_replay = complete_tool_invocation(
                db,
                claim,
                invocation_id=decision.invocation_id,
                status="ok",
                summary="No resolved facts yet",
                data=[],
                returned_items=0,
                now=now,
            )
            assert result_replay.duplicate is True
            page = read_tool_result_slice(
                db,
                claim,
                result_ref_id=receipt.result_id,
                now=now,
            )
            assert page["items"] == []
            with pytest.raises(BidToolUnauthorized):
                authorize_tool_invocation(
                    db,
                    claim,
                    context_manifest_id=first_context.context_manifest_id,
                    tool_name="enterprise.profile.query",
                    arguments={"fields": ["legal_name"]},
                    idempotency_key="phase3e-sync-key-deny",
                    scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                    now=now,
                )
    finally:
        db.close()

    db = session_factory()
    try:
        assert db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one().status == "running"
        assert db.query(BidContextManifest).count() == 1
        assert db.query(BidToolInvocation).count() == 1
        assert db.query(BidToolResult).count() == 1
    finally:
        db.close()


def test_phase3e_async_operation_resumes_on_new_attempt_and_never_revives_old_fence(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, _run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3e-async", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now)
            context = assemble_context_manifest(db, claim, now=now)
            invocation = authorize_tool_invocation(
                db,
                claim,
                context_manifest_id=context.context_manifest_id,
                tool_name="facts.query",
                arguments={"fact_slots": ["tender.deadline"]},
                idempotency_key="phase3e-async-key-001",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            checkpoint = write_task_checkpoint(
                db,
                claim,
                action_seq=0,
                state={"pending_invocation_id": invocation.invocation_id},
                context_manifest_id=context.context_manifest_id,
                now=now,
            )
            pending = defer_tool_invocation(
                db,
                claim,
                invocation_id=invocation.invocation_id,
                checkpoint_id=checkpoint.checkpoint_id,
                now=now,
            )
            operation_id = pending["operation_id"]
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            settled = settle_async_tool_operation(
                db,
                operation_id=operation_id,
                status="ok",
                summary="Async tool observation completed",
                data=[{"slot": "tender.deadline", "status": "missing"}],
                returned_items=1,
                now=now,
            )
            assert settled.duplicate is False
            async_result_id = settled.result_id
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            resumed = lease_next_ready_task(db, worker_id="phase3e-resumed", now=now)
            assert resumed is not None
            assert resumed.attempt_no == claim.attempt_no + 1
            assert resumed.fencing_token == claim.fencing_token + 1
            assert resumed.resume_checkpoint["checkpoint_id"] == checkpoint.checkpoint_id
            start_task_attempt(db, resumed, now=now)
            page = read_tool_result_slice(
                db,
                resumed,
                result_ref_id=async_result_id,
                now=now,
            )
            assert page["items"] == [{"slot": "tender.deadline", "status": "missing"}]
            old_attempt = (
                db.query(BidTaskAttempt)
                .filter(BidTaskAttempt.id == claim.attempt_id)
                .one()
            )
            assert old_attempt.status == "cancelled"
            assert (
                db.query(BidAsyncOperation)
                .filter(BidAsyncOperation.id == operation_id)
                .one()
                .status
                == "succeeded"
            )
    finally:
        db.close()


def test_phase3e_audit_failure_rolls_back_context_and_tool_rows(
    api_runtime,
    monkeypatch,
) -> None:
    _client, session_factory, _owner = api_runtime
    _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3e-rollback", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now)
    finally:
        db.close()

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("synthetic-phase3e-audit-failure")

    monkeypatch.setattr(tool_context_service, "append_audit_log", fail_audit)
    db = session_factory()
    try:
        with pytest.raises(RuntimeError, match="synthetic-phase3e-audit-failure"):
            with db.begin():
                assemble_context_manifest(db, claim, now=now)
    finally:
        db.close()
    db = session_factory()
    try:
        assert db.query(BidContextManifest).count() == 0
        assert db.query(BidToolInvocation).count() == 0
        assert db.query(BidToolResult).count() == 0
    finally:
        db.close()


def test_phase3e_timeout_persists_failed_result_and_resumes_on_new_fence(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3e-timeout", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now)
            context = assemble_context_manifest(db, claim, now=now)
            invocation = authorize_tool_invocation(
                db,
                claim,
                context_manifest_id=context.context_manifest_id,
                tool_name="facts.query",
                arguments={"fact_slots": ["tender.deadline"]},
                idempotency_key="phase3e-timeout-key-001",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            checkpoint = write_task_checkpoint(
                db,
                claim,
                action_seq=0,
                state={"pending_invocation_id": invocation.invocation_id},
                context_manifest_id=context.context_manifest_id,
                now=now,
            )
            pending = defer_tool_invocation(
                db,
                claim,
                invocation_id=invocation.invocation_id,
                checkpoint_id=checkpoint.checkpoint_id,
                timeout_seconds=30,
                now=now,
            )
            operation_id = pending["operation_id"]
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            changed, receipt = time_out_async_tool_operation(
                db,
                operation_id=operation_id,
                now=now + timedelta(seconds=31),
            )
            assert changed is True
            assert receipt is not None
        operation = (
            db.query(BidAsyncOperation)
            .filter(BidAsyncOperation.id == operation_id)
            .one()
        )
        assert operation.status == "timed_out"
        assert operation.error_code == "BID_TOOL_OPERATION_TIMED_OUT"
        invocation_row = (
            db.query(BidToolInvocation)
            .filter(BidToolInvocation.id == invocation.invocation_id)
            .one()
        )
        assert invocation_row.status == "failed"
        assert invocation_row.error_code == "BID_TOOL_OPERATION_TIMED_OUT"
    finally:
        db.close()


def test_phase3e_budget_and_old_fence_fail_closed(api_runtime) -> None:
    _client, session_factory, _owner = api_runtime
    _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3e-budget", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now)
            context = assemble_context_manifest(db, claim, now=now)
            max_calls = int(claim.task_contract["budget"]["max_tool_calls"])
            for index in range(max_calls):
                decision = authorize_tool_invocation(
                    db,
                    claim,
                    context_manifest_id=context.context_manifest_id,
                    tool_name="facts.query",
                    arguments={"fact_slots": [f"tender.slot_{index}"]},
                    idempotency_key=f"phase3e-budget-key-{index:04d}",
                    scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                    now=now,
                )
                complete_tool_invocation(
                    db,
                    claim,
                    invocation_id=decision.invocation_id,
                    status="no_result",
                    summary="No governed result",
                    data=[],
                    returned_items=0,
                    now=now,
                )
            with pytest.raises(BidToolBudgetExhausted):
                authorize_tool_invocation(
                    db,
                    claim,
                    context_manifest_id=context.context_manifest_id,
                    tool_name="facts.query",
                    arguments={"fact_slots": ["tender.over_budget"]},
                    idempotency_key="phase3e-budget-key-overflow",
                    scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                    now=now,
                )
            claim_attempt_id = claim.attempt_id
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            attempt = (
                db.query(BidTaskAttempt)
                .filter(BidTaskAttempt.id == claim_attempt_id)
                .one()
            )
            attempt.fencing_token = int(attempt.fencing_token) + 1
            attempt.row_version = int(attempt.row_version) + 1
        with pytest.raises(BidTaskFenceLost):
            with db.begin():
                assemble_context_manifest(db, claim, now=now)
    finally:
        db.close()


def _prepare_phase3f_documents_outline(api_runtime, *, now: datetime):
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id = _create_phase3c_committed_run(
        api_runtime,
        attach_document=True,
    )
    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        version_id = str(
            db.query(BidManifestDocument.document_version_id)
            .filter(BidManifestDocument.manifest_id == run.manifest_id)
            .scalar()
        )
    finally:
        db.close()
    parse_run_id = str(uuid.uuid4())
    db = session_factory()
    try:
        with db.begin():
            db.add(
                BidDocumentParseRun(
                    id=parse_run_id,
                    document_version_id=version_id,
                    parser_profile_version="phase3f-local-outline-v1",
                    input_hash="6" * 64,
                    status="succeeded",
                    retryable=False,
                    requested_at=now - timedelta(seconds=2),
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    result_ref="local://phase3f/outline",
                    result_hash="7" * 64,
                    quality_grade="high",
                    quality_score=100,
                    page_count=1,
                    sheet_count=0,
                    ocr_status="not_applicable",
                    warning_count=0,
                    warnings_json=[],
                    row_version=1,
                )
            )
            db.flush()
            db.add(
                BidDocumentParseHead(
                    document_version_id=version_id,
                    current_run_id=parse_run_id,
                    row_version=1,
                )
            )
            db.add(
                BidDocumentParseUnit(
                    id=str(uuid.uuid4()),
                    run_id=parse_run_id,
                    unit_type="page",
                    unit_key="page:1",
                    ordinal=0,
                    page_no=1,
                    section_path_json=["投标须知", "重要时间"],
                    content_source="native",
                    status="succeeded",
                    text_hash="8" * 64,
                    text_length=24,
                    ocr_status="not_applicable",
                )
            )
    finally:
        db.close()
    return session_factory, run_id, version_id


def _enqueue_phase3f_outline_dispatch(
    api_runtime,
    *,
    now: datetime,
    idempotency_key: str,
):
    session_factory, run_id, version_id = _prepare_phase3f_documents_outline(
        api_runtime,
        now=now,
    )
    db = session_factory()
    try:
        with db.begin():
            task_claim = lease_next_ready_task(
                db,
                worker_id=f"phase3f-setup-{idempotency_key}"[:128],
                now=now,
            )
            assert task_claim is not None
            start_task_attempt(db, task_claim, now=now)
            context = assemble_context_manifest(db, task_claim, now=now)
            invocation = authorize_tool_invocation(
                db,
                task_claim,
                context_manifest_id=context.context_manifest_id,
                tool_name="documents.outline",
                arguments={"document_version_id": version_id},
                idempotency_key=idempotency_key,
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            checkpoint = write_task_checkpoint(
                db,
                task_claim,
                action_seq=0,
                state={"pending_invocation_id": invocation.invocation_id},
                context_manifest_id=context.context_manifest_id,
                now=now,
            )
            dispatch = enqueue_tool_dispatch(
                db,
                task_claim,
                invocation_id=invocation.invocation_id,
                checkpoint_id=checkpoint.checkpoint_id,
                scope_token=invocation.call_envelope["scope_token"],
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                timeout_seconds=60,
                now=now,
            )
            return session_factory, run_id, dispatch
    finally:
        db.close()


def test_phase3f_local_outline_dispatch_end_to_end_and_new_fence_resume(
    api_runtime,
) -> None:
    now = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)
    session_factory, _run_id, version_id = _prepare_phase3f_documents_outline(
        api_runtime,
        now=now,
    )
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3f-enqueue", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now)
            context = assemble_context_manifest(db, claim, now=now)
            invocation = authorize_tool_invocation(
                db,
                claim,
                context_manifest_id=context.context_manifest_id,
                tool_name="documents.outline",
                arguments={"document_version_id": version_id, "max_depth": 4},
                idempotency_key="phase3f-outline-key-0001",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            checkpoint = write_task_checkpoint(
                db,
                claim,
                action_seq=0,
                state={"pending_invocation_id": invocation.invocation_id},
                context_manifest_id=context.context_manifest_id,
                now=now,
            )
            dispatch = enqueue_tool_dispatch(
                db,
                claim,
                invocation_id=invocation.invocation_id,
                checkpoint_id=checkpoint.checkpoint_id,
                scope_token=invocation.call_envelope["scope_token"],
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            assert dispatch.status == "queued"
            duplicate_dispatch = enqueue_tool_dispatch(
                db,
                claim,
                invocation_id=invocation.invocation_id,
                checkpoint_id=checkpoint.checkpoint_id,
                scope_token=invocation.call_envelope["scope_token"],
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            assert duplicate_dispatch.dispatch_id == dispatch.dispatch_id
            assert duplicate_dispatch.duplicate is True
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            dispatch_claim = claim_next_tool_dispatch(
                db,
                worker_id="phase3f-dispatcher",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            assert dispatch_claim is not None
            assert dispatch_claim.fencing_token == 1
            assert dispatch_claim.adapter_mode == "local_readonly"
    finally:
        db.close()
    assert (
        execute_tool_dispatch_claim(
            session_factory=session_factory,
            claim=dispatch_claim,
            now=now,
        )
        == "succeeded"
    )

    db = session_factory()
    try:
        stored_dispatch = db.query(BidToolDispatch).one()
        result = db.query(BidToolResult).one()
        assert stored_dispatch.status == "succeeded"
        assert stored_dispatch.provider_receipt_id.startswith("local:bid-tool:")
        assert result.inline_data_json["items"][0]["section_path"] == [
            "投标须知",
            "重要时间",
        ]
        db.rollback()
        with db.begin():
            resumed = lease_next_ready_task(
                db,
                worker_id="phase3f-resumed",
                now=now + timedelta(seconds=1),
            )
            assert resumed is not None
            assert resumed.fencing_token == claim.fencing_token + 1
            assert resumed.resume_checkpoint["checkpoint_id"] == checkpoint.checkpoint_id
    finally:
        db.close()


def test_phase3f_dispatch_enqueue_rolls_back_and_old_fence_cannot_settle(
    api_runtime,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)
    session_factory, _run_id, version_id = _prepare_phase3f_documents_outline(
        api_runtime,
        now=now,
    )
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3f-rollback", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now)
            context = assemble_context_manifest(db, claim, now=now)
            invocation = authorize_tool_invocation(
                db,
                claim,
                context_manifest_id=context.context_manifest_id,
                tool_name="documents.outline",
                arguments={"document_version_id": version_id},
                idempotency_key="phase3f-outline-key-rollback",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
            checkpoint = write_task_checkpoint(
                db,
                claim,
                action_seq=0,
                state={"pending_invocation_id": invocation.invocation_id},
                context_manifest_id=context.context_manifest_id,
                now=now,
            )
    finally:
        db.close()

    original_audit = tool_execution_service.append_audit_log
    monkeypatch.setattr(
        tool_execution_service,
        "append_audit_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("phase3f-audit")),
    )
    db = session_factory()
    try:
        with pytest.raises(RuntimeError, match="phase3f-audit"):
            with db.begin():
                enqueue_tool_dispatch(
                    db,
                    claim,
                    invocation_id=invocation.invocation_id,
                    checkpoint_id=checkpoint.checkpoint_id,
                    scope_token=invocation.call_envelope["scope_token"],
                    scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                    now=now,
                )
    finally:
        db.close()
    db = session_factory()
    try:
        assert db.query(BidToolDispatch).count() == 0
        assert db.query(BidAsyncOperation).count() == 0
        assert db.query(BidToolInvocation).one().status == "accepted"
    finally:
        db.close()

    monkeypatch.setattr(tool_execution_service, "append_audit_log", original_audit)
    db = session_factory()
    try:
        with db.begin():
            enqueue_tool_dispatch(
                db,
                claim,
                invocation_id=invocation.invocation_id,
                checkpoint_id=checkpoint.checkpoint_id,
                scope_token=invocation.call_envelope["scope_token"],
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now,
            )
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            first = claim_next_tool_dispatch(
                db,
                worker_id="phase3f-fence-one",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                lease_seconds=15,
                now=now,
            )
            assert first is not None
            mark_tool_dispatch_sending(db, first, now=now)
        with db.begin():
            assert (
                recover_expired_tool_dispatch(
                    db,
                    dispatch_id=first.dispatch_id,
                    now=now + timedelta(seconds=16),
                )
                == "recovered"
            )
        with db.begin():
            second = claim_next_tool_dispatch(
                db,
                worker_id="phase3f-fence-two",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now + timedelta(seconds=17),
            )
            assert second is not None
            assert second.fencing_token == first.fencing_token + 1
            mark_tool_dispatch_sending(db, second, now=now + timedelta(seconds=17))
        with pytest.raises(BidToolDispatchFenceLost):
            with db.begin():
                settle_tool_dispatch(
                    db,
                    first,
                    ToolAdapterResult(
                        status="ok",
                        summary="late old-fence result",
                        data={},
                    ),
                    now=now + timedelta(seconds=18),
                )
        with db.begin():
            current_dispatch = (
                db.query(BidToolDispatch)
                .filter(BidToolDispatch.id == second.dispatch_id)
                .one()
            )
            current_dispatch.replay_policy = "reconcile_required"
            assert (
                recover_expired_tool_dispatch(
                    db,
                    dispatch_id=second.dispatch_id,
                    now=now + timedelta(seconds=90),
                )
                == "uncertain"
            )
            assert current_dispatch.status == "uncertain"
            assert current_dispatch.last_error_code == (
                "BID_TOOL_DISPATCH_OUTCOME_UNCERTAIN"
            )
    finally:
        db.close()


def test_phase3f_timeout_fences_queued_dispatch(api_runtime) -> None:
    now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
    session_factory, _timeout_run_id, timeout_dispatch = (
        _enqueue_phase3f_outline_dispatch(
            api_runtime,
            now=now,
            idempotency_key="phase3f-timeout-key-0001",
        )
    )
    db = session_factory()
    try:
        with db.begin():
            changed, _receipt = time_out_async_tool_operation(
                db,
                operation_id=timeout_dispatch.operation_id,
                now=now + timedelta(seconds=61),
            )
            assert changed is True
        timed_out = (
            db.query(BidToolDispatch)
            .filter(BidToolDispatch.id == timeout_dispatch.dispatch_id)
            .one()
        )
        assert timed_out.status == "failed"
        assert timed_out.last_error_code == "BID_TOOL_OPERATION_TIMED_OUT"
    finally:
        db.close()


def test_phase3f_run_cancel_fences_sending_dispatch_and_attempt(api_runtime) -> None:
    cancel_now = datetime(2026, 8, 12, 8, 5, tzinfo=timezone.utc)
    session_factory, cancel_run_id, cancel_dispatch = _enqueue_phase3f_outline_dispatch(
        api_runtime,
        now=cancel_now,
        idempotency_key="phase3f-cancel-key-0001",
    )
    db = session_factory()
    try:
        with db.begin():
            dispatch_claim = claim_next_tool_dispatch(
                db,
                worker_id="phase3f-cancel-sending",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=cancel_now,
            )
            assert dispatch_claim is not None
            mark_tool_dispatch_sending(db, dispatch_claim, now=cancel_now)
        with db.begin():
            run = (
                db.query(BidAnalysisRun)
                .filter(BidAnalysisRun.id == cancel_run_id)
                .with_for_update()
                .one()
            )
            run.cancel_requested_at = cancel_now + timedelta(seconds=1)
            run.row_version = int(run.row_version) + 1
            changed, _tasks, _attempts, _operations = finalize_cancel_requested_run(
                db,
                run_id=cancel_run_id,
                now=cancel_now + timedelta(seconds=1),
            )
            assert changed is True
        cancelled = (
            db.query(BidToolDispatch)
            .filter(BidToolDispatch.id == cancel_dispatch.dispatch_id)
            .one()
        )
        cancelled_attempt = (
            db.query(BidToolDispatchAttempt)
            .filter(BidToolDispatchAttempt.dispatch_id == cancelled.id)
            .one()
        )
        assert cancelled.status == "cancelled"
        assert cancelled_attempt.status == "cancelled"
        assert execute_tool_dispatch_claim(
            session_factory=session_factory,
            claim=dispatch_claim,
            now=cancel_now + timedelta(seconds=2),
        ) == "cancelled"
    finally:
        db.close()


def _create_phase3c_committed_run(
    api_runtime,
    *,
    attach_document: bool = False,
    phase4_plan_continuation: bool = False,
    phase4_model_gateway: bool = False,
) -> tuple[str, str]:
    client, session_factory, owner = api_runtime
    created = _create_assessment(client)
    assessment_id = created.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    if attach_document:
        _attach_manifest_document(
            session_factory,
            manifest_id=manifest_id,
            actor_id=owner.id,
        )
    _attach_phase3_scope(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    _activate_phase3_frozen_versions(
        session_factory,
        actor_id=owner.id,
        phase4_model_gateway=phase4_model_gateway,
    )
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    started = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={"Idempotency-Key": _key(), "If-Match": current.headers["etag"]},
        json={
            "manifest_id": manifest_id,
            "reason": "manual_restart",
            "note": "Phase 3C task runtime",
        },
    )
    assert started.status_code == 202
    run_id = started.json()["data"]["run_id"]
    db = session_factory()
    try:
        event_id = str(
            db.query(BidOutboxEvent.event_id)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.created.v1",
            )
            .scalar()
        )
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            result = consume_run_created_event(
                db,
                event_id=event_id,
                phase4_plan_continuation=phase4_plan_continuation,
            )
        assert result.value["committed"] is True
    finally:
        db.close()
    return assessment_id, run_id


def test_phase3c_task_contract_lease_checkpoint_completion_and_dependency_release(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(
                db,
                worker_id="phase3c-worker-1",
                now=now,
            )
            assert claim is not None
            assert claim.attempt_no == claim.fencing_token == 1
            assert claim.task_contract["task_type"] == "bind_assessment_snapshot"
            assert claim.task_contract["budget"]["max_iterations"] == 3
            assert claim.task_contract_hash == canonical_hash(claim.task_contract)
            start_task_attempt(db, claim, now=now + timedelta(seconds=1))
            lease_until = heartbeat_task_attempt(
                db,
                claim,
                now=now + timedelta(seconds=30),
            )
            assert lease_until == now + timedelta(seconds=210)
            checkpoint = write_task_checkpoint(
                db,
                claim,
                action_seq=0,
                state={"phase": "bound", "input_hash": claim.task_contract_hash},
                tool_refs=[],
                budget_usage={"iterations": 1, "tool_calls": 0},
                candidate_output_ref="task-output:test:1",
                next_state="succeeded",
                now=now + timedelta(seconds=31),
            )
            replay = write_task_checkpoint(
                db,
                claim,
                action_seq=0,
                state={"phase": "bound", "input_hash": claim.task_contract_hash},
                tool_refs=[],
                budget_usage={"iterations": 1, "tool_calls": 0},
                candidate_output_ref="task-output:test:1",
                next_state="succeeded",
                now=now + timedelta(seconds=32),
            )
            assert replay.duplicate is True
            with pytest.raises(BidCheckpointConflict):
                write_task_checkpoint(
                    db,
                    claim,
                    action_seq=2,
                    state={"phase": "invalid-gap"},
                    now=now + timedelta(seconds=32),
                )
            completed = complete_task_attempt(
                db,
                claim,
                completion=TaskCompletionReceipt(
                    checkpoint_id=checkpoint.checkpoint_id,
                    state_hash=checkpoint.state_hash,
                    output_hash="a" * 64,
                    completion_contract=claim.task_contract["completion_contract"],
                    validator_version="bid-task-output-validator-v1",
                    output_ref="task-output:test:1",
                ),
                now=now + timedelta(seconds=33),
            )
            assert completed.status == "succeeded"
            assert len(completed.released_task_ids) == 1
            assert completed.validation_requested is False
    finally:
        db.close()

    db = session_factory()
    try:
        tasks = db.query(BidTask).filter(BidTask.run_id == run_id).all()
        assert [task.status for task in tasks].count("succeeded") == 1
        assert [task.status for task in tasks].count("ready") == 1
        assert db.query(BidTaskAttempt).filter_by(id=claim.attempt_id).one().status == (
            "succeeded"
        )
        assert db.query(BidCheckpoint).filter_by(id=checkpoint.checkpoint_id).count() == 1
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.task.succeeded.v1",
            )
            .count()
            == 1
        )
    finally:
        db.close()


def test_phase3c_retry_then_fail_and_transaction_rollback_boundaries(
    api_runtime,
    monkeypatch,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)

    def _raise_audit_failure(*_args, **_kwargs):
        raise RuntimeError("synthetic-task-runtime-audit-failure")

    monkeypatch.setattr(
        task_runtime_service,
        "append_audit_log",
        _raise_audit_failure,
    )
    db = session_factory()
    try:
        with pytest.raises(RuntimeError, match="synthetic-task-runtime-audit-failure"):
            with db.begin():
                lease_next_ready_task(
                    db,
                    worker_id="phase3c-rollback-worker",
                    now=now,
                )
    finally:
        db.close()
    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.status == "queued"
        assert db.query(BidTaskAttempt).count() == 0
        assert db.query(BidTask).filter(BidTask.status == "ready").count() == 1
        assert (
            db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.event_type == "bid.task.leased.v1")
            .count()
            == 0
        )
    finally:
        db.close()

    monkeypatch.undo()
    db = session_factory()
    try:
        with db.begin():
            first = lease_next_ready_task(
                db,
                worker_id="phase3c-retry-worker",
                now=now + timedelta(seconds=1),
            )
            assert first is not None
            start_task_attempt(db, first, now=now + timedelta(seconds=2))
            failed = fail_task_attempt(
                db,
                first,
                error_code="BID_QUEUE_UNAVAILABLE",
                retryable=True,
                max_attempts=2,
                now=now + timedelta(seconds=3),
            )
            assert failed.retry_scheduled is True
            assert failed.task_status == "ready"
            assert failed.run_status == "running"
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            second = lease_next_ready_task(
                db,
                worker_id="phase3c-retry-worker",
                now=now + timedelta(seconds=4),
            )
            assert second is not None
            start_task_attempt(db, second, now=now + timedelta(seconds=5))
            exhausted = fail_task_attempt(
                db,
                second,
                error_code="BID_QUEUE_UNAVAILABLE",
                retryable=True,
                max_attempts=2,
                now=now + timedelta(seconds=6),
            )
            assert exhausted.retry_scheduled is False
            assert exhausted.task_status == "failed"
            assert exhausted.run_status == "failed"
    finally:
        db.close()
    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.status == "failed"
        assert run.retryable is True
        assert (
            db.query(BidTaskAttempt)
            .filter(BidTaskAttempt.task_id == second.task_id)
            .count()
            == 2
        )
    finally:
        db.close()


def test_phase3c_expired_lease_creates_new_fence_and_rejects_old_worker(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, _run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            first = lease_next_ready_task(
                db,
                worker_id="phase3c-worker-old",
                lease_seconds=30,
                now=now,
            )
            assert first is not None
            start_task_attempt(db, first, now=now + timedelta(seconds=1))
    finally:
        db.close()

    maintenance = maintain_task_runtime(
        session_factory=session_factory,
        now=now + timedelta(seconds=31),
    )
    assert maintenance.scanned == maintenance.recovered == 1
    assert maintenance.retry_scheduled == 1
    db = session_factory()
    try:
        with db.begin():
            second = lease_next_ready_task(
                db,
                worker_id="phase3c-worker-new",
                now=now + timedelta(seconds=32),
            )
            assert second is not None
            assert second.task_id == first.task_id
            assert second.attempt_no == 2
            assert second.fencing_token == 2
            with pytest.raises(BidTaskFenceLost):
                heartbeat_task_attempt(
                    db,
                    first,
                    now=now + timedelta(seconds=33),
                )
    finally:
        db.close()


def test_phase3c_dag_completion_requests_validation_without_running_a_model(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
    completed_task_ids: list[str] = []
    for index in range(8):
        db = session_factory()
        try:
            with db.begin():
                claim = lease_next_ready_task(
                    db,
                    worker_id="phase3c-deterministic-worker",
                    now=now + timedelta(seconds=index * 10),
                )
                assert claim is not None
                start_task_attempt(
                    db,
                    claim,
                    now=now + timedelta(seconds=index * 10 + 1),
                )
                checkpoint = write_task_checkpoint(
                    db,
                    claim,
                    action_seq=0,
                    state={"deterministic_test": True, "task_id": claim.task_id},
                    budget_usage={"iterations": 1, "tool_calls": 0},
                    next_state="succeeded",
                    now=now + timedelta(seconds=index * 10 + 2),
                )
                result = complete_task_attempt(
                    db,
                    claim,
                    completion=TaskCompletionReceipt(
                        checkpoint_id=checkpoint.checkpoint_id,
                        state_hash=checkpoint.state_hash,
                        output_hash=canonical_hash({"task_id": claim.task_id}),
                        completion_contract=claim.task_contract["completion_contract"],
                        validator_version="bid-task-output-validator-v1",
                    ),
                    now=now + timedelta(seconds=index * 10 + 3),
                )
                completed_task_ids.append(result.task_id)
        finally:
            db.close()
    assert len(set(completed_task_ids)) == 8
    assert result.validation_requested is True
    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.status == "validating"
        assert run.current_stage == "validation"
        assert db.query(BidTask).filter(BidTask.run_id == run_id, BidTask.status == "succeeded").count() == 8
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.validation_requested.v1",
            )
            .count()
            == 1
        )
    finally:
        db.close()


def test_phase4a1_plan_continuation_freezes_skills_and_validates_only_after_p4(
    api_runtime,
) -> None:
    client, session_factory, _owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(
        api_runtime,
        phase4_plan_continuation=True,
    )
    now = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
    stage_counts = {"P0": 8, "P1": 6, "P2": 7, "P3": 4, "P4": 1}
    completed_count = 0
    first_task_id: str | None = None

    for stage_index, (stage_code, stage_task_count) in enumerate(stage_counts.items()):
        db = session_factory()
        try:
            current_plan = (
                db.query(BidPlanRevision)
                .filter(
                    BidPlanRevision.run_id == run_id,
                    BidPlanRevision.status == "committed",
                    BidPlanRevision.committed_slot_key == "committed",
                )
                .one()
            )
            assert current_plan.revision_no == stage_index + 1
            assert current_plan.proposal_json["schema"] == "bid.plan.commit.envelope.v2"
            assert current_plan.proposal_json["stage"] == stage_code
            assert current_plan.proposal_json["final_stage"] is (stage_code == "P4")
            stage_tasks = (
                db.query(BidTask)
                .filter(BidTask.plan_revision_id == current_plan.id)
                .all()
            )
            assert len(stage_tasks) == stage_task_count
            for task in stage_tasks:
                contract = build_task_contract(db, task)
                assert contract["skill_binding"]["skill_hash"]
                assert contract["allowed_tools"]
            if first_task_id is None:
                first_task_id = str(stage_tasks[0].id)
        finally:
            db.close()

        last_completion = None
        for _ in range(stage_task_count):
            task_time = now + timedelta(seconds=completed_count * 10)
            db = session_factory()
            try:
                with db.begin():
                    claim = lease_next_ready_task(
                        db,
                        worker_id="phase4a1-deterministic-worker",
                        now=task_time,
                    )
                    assert claim is not None
                    assert claim.task_contract["skill_binding"]["skill_hash"]
                    start_task_attempt(db, claim, now=task_time + timedelta(seconds=1))
                    checkpoint = write_task_checkpoint(
                        db,
                        claim,
                        action_seq=0,
                        state={
                            "phase4a1_static_fixture": True,
                            "stage": stage_code,
                            "task_id": claim.task_id,
                        },
                        budget_usage={"iterations": 1, "tool_calls": 0},
                        next_state="succeeded",
                        now=task_time + timedelta(seconds=2),
                    )
                    last_completion = complete_task_attempt(
                        db,
                        claim,
                        completion=TaskCompletionReceipt(
                            checkpoint_id=checkpoint.checkpoint_id,
                            state_hash=checkpoint.state_hash,
                            output_hash=canonical_hash(
                                {"stage": stage_code, "task_id": claim.task_id}
                            ),
                            completion_contract=claim.task_contract[
                                "completion_contract"
                            ],
                            validator_version="bid-task-output-validator-v1",
                        ),
                        plan_continuation_enabled=True,
                        now=task_time + timedelta(seconds=3),
                    )
                completed_count += 1
            finally:
                db.close()

        assert last_completion is not None
        if stage_code == "P4":
            assert last_completion.validation_requested is True
            continue
        assert last_completion.validation_requested is False

        db = session_factory()
        try:
            continuation_events = (
                db.query(BidOutboxEvent)
                .filter(
                    BidOutboxEvent.run_id == run_id,
                    BidOutboxEvent.event_type
                    == "bid.plan.continuation_requested.v1",
                )
                .all()
            )
            continuation_event = next(
                row
                for row in continuation_events
                if row.payload_json["completed_stage"] == stage_code
            )
            continuation_event_id = str(continuation_event.event_id)
        finally:
            db.close()

        db = session_factory()
        try:
            with db.begin():
                projected = project_outbox_event_to_public(
                    db,
                    event_id=continuation_event_id,
                )
            assert projected.duplicate is False
        finally:
            db.close()

        db = session_factory()
        try:
            with db.begin():
                continued = consume_plan_continuation_requested_event(
                    db,
                    event_id=continuation_event_id,
                    committed_at=now
                    + timedelta(seconds=(completed_count - 1) * 10 + 4),
                )
            assert continued.duplicate is False
            assert continued.value["committed"] is True
            with db.begin():
                replay = consume_plan_continuation_requested_event(
                    db,
                    event_id=continuation_event_id,
                    committed_at=now
                    + timedelta(seconds=(completed_count - 1) * 10 + 5),
                )
            assert replay.duplicate is True
        finally:
            db.close()

        db = session_factory()
        try:
            revisions = (
                db.query(BidPlanRevision)
                .filter(BidPlanRevision.run_id == run_id)
                .order_by(BidPlanRevision.revision_no.asc())
                .all()
            )
            assert [row.status for row in revisions[:-1]] == [
                "superseded"
            ] * (len(revisions) - 1)
            assert revisions[-1].status == "committed"
            assert first_task_id is not None
            assert build_task_contract(
                db,
                db.query(BidTask).filter(BidTask.id == first_task_id).one(),
            )["skill_binding"]["skill_id"]
        finally:
            db.close()

    assert completed_count == 26
    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.status == "validating"
        assert run.current_stage == "validation"
        assert db.query(BidPlanRevision).filter_by(run_id=run_id).count() == 5
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.plan.continuation_requested.v1",
            )
            .count()
            == 4
        )
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.validation_requested.v1",
            )
            .count()
            == 1
        )
        validation_event_id = str(
            db.query(BidOutboxEvent.event_id)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.validation_requested.v1",
            )
            .scalar()
        )
        public_stage_events = (
            db.query(BidPublicEvent)
            .filter(
                BidPublicEvent.assessment_id == assessment_id,
                BidPublicEvent.event_type == "run.stage.changed",
            )
            .count()
        )
        assert public_stage_events >= 4
    finally:
        db.close()

    validation_time = now + timedelta(seconds=260)
    db = session_factory()
    try:
        with db.begin():
            materialized = consume_run_validation_requested_event(
                db,
                event_id=validation_event_id,
                now=validation_time,
            )
            assert materialized.duplicate is False
            claim = claim_next_run_validation(
                db,
                worker_id="phase4a1-run-validator",
                now=validation_time + timedelta(seconds=1),
            )
            assert claim is not None
            validation_result = execute_run_validation_claim(
                db,
                claim,
                now=validation_time + timedelta(seconds=2),
            )
            assert validation_result.outcome == "passed"
            assert validation_result.run_status == "succeeded"
    finally:
        db.close()

    db = session_factory()
    try:
        validation = db.query(BidRunValidation).filter_by(run_id=run_id).one()
        assert validation.validator_version == "bid-run-integrity-validator-v4"
        assert validation.result_json["outcome"] == "passed"
        assert db.query(BidAnalysisRun).filter_by(id=run_id).one().status == "succeeded"
    finally:
        db.close()

    progress = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert progress.status_code == 200
    assert progress.json()["data"]["status"] == "succeeded"


def test_phase4a1_plan_continuation_rolls_back_and_maintenance_recovers(
    api_runtime,
    monkeypatch,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id = _create_phase3c_committed_run(
        api_runtime,
        phase4_plan_continuation=True,
    )
    now = datetime(2026, 8, 12, 19, 0, tzinfo=timezone.utc)

    for index in range(8):
        task_time = now + timedelta(seconds=index * 10)
        db = session_factory()
        try:
            with db.begin():
                claim = lease_next_ready_task(
                    db,
                    worker_id="phase4a1-recovery-worker",
                    now=task_time,
                )
                assert claim is not None
                start_task_attempt(db, claim, now=task_time + timedelta(seconds=1))
                checkpoint = write_task_checkpoint(
                    db,
                    claim,
                    action_seq=0,
                    state={"phase4a1_recovery": True, "task_id": claim.task_id},
                    budget_usage={"iterations": 1, "tool_calls": 0},
                    next_state="succeeded",
                    now=task_time + timedelta(seconds=2),
                )
                complete_task_attempt(
                    db,
                    claim,
                    completion=TaskCompletionReceipt(
                        checkpoint_id=checkpoint.checkpoint_id,
                        state_hash=checkpoint.state_hash,
                        output_hash=canonical_hash({"task_id": claim.task_id}),
                        completion_contract=claim.task_contract["completion_contract"],
                        validator_version="bid-task-output-validator-v1",
                    ),
                    plan_continuation_enabled=True,
                    now=task_time + timedelta(seconds=3),
                )
        finally:
            db.close()

    db = session_factory()
    try:
        continuation_event = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.plan.continuation_requested.v1",
            )
            .one()
        )
        continuation_event_id = str(continuation_event.event_id)
    finally:
        db.close()

    original_append_audit_log = plan_continuation_service.append_audit_log

    def _fail_audit(*_args, **_kwargs):
        raise RuntimeError("phase4a1 forced audit failure")

    monkeypatch.setattr(plan_continuation_service, "append_audit_log", _fail_audit)
    db = session_factory()
    try:
        with pytest.raises(RuntimeError, match="forced audit failure"):
            with db.begin():
                consume_plan_continuation_requested_event(
                    db,
                    event_id=continuation_event_id,
                    committed_at=now + timedelta(seconds=85),
                )
    finally:
        db.close()

    db = session_factory()
    try:
        plans = db.query(BidPlanRevision).filter_by(run_id=run_id).all()
        assert len(plans) == 1
        assert plans[0].status == "committed"
        assert plans[0].proposal_json["stage"] == "P0"
        assert (
            db.query(BidProcessedEvent)
            .filter_by(
                consumer_name=PLAN_CONTINUATION_CONSUMER,
                event_id=continuation_event_id,
            )
            .count()
            == 0
        )
    finally:
        db.close()

    monkeypatch.setattr(
        plan_continuation_service,
        "append_audit_log",
        original_append_audit_log,
    )
    recovered = process_pending_plan_continuations(
        session_factory=session_factory,
        limit=20,
    )
    assert recovered.scanned == 1
    assert recovered.committed == 1
    assert recovered.duplicate == 0
    assert recovered.ignored == 0
    assert recovered.failed == 0

    db = session_factory()
    try:
        plans = (
            db.query(BidPlanRevision)
            .filter_by(run_id=run_id)
            .order_by(BidPlanRevision.revision_no.asc())
            .all()
        )
        assert [(row.proposal_json["stage"], row.status) for row in plans] == [
            ("P0", "superseded"),
            ("P1", "committed"),
        ]
        historical_task = (
            db.query(BidTask)
            .filter(BidTask.plan_revision_id == plans[0].id)
            .order_by(BidTask.task_key.asc())
            .first()
        )
        assert historical_task is not None
        assert build_task_contract(db, historical_task)["skill_binding"]["skill_hash"]
    finally:
        db.close()

    replay = process_pending_plan_continuations(
        session_factory=session_factory,
        limit=20,
    )
    assert replay.scanned == 0


def _prepare_phase4a2_pending_model_call(api_runtime, *, now: datetime):
    _client, session_factory, _owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(
        api_runtime,
        phase4_plan_continuation=True,
        phase4_model_gateway=True,
    )
    for index in range(3):
        task_time = now + timedelta(seconds=index * 10)
        db = session_factory()
        try:
            with db.begin():
                claim = lease_next_ready_task(
                    db,
                    worker_id="phase4a2-prerequisite-worker",
                    now=task_time,
                )
                assert claim is not None
                assert claim.task_contract["skill_binding"]["executor_kind"] == "deterministic"
                start_task_attempt(db, claim, now=task_time + timedelta(seconds=1))
                checkpoint = write_task_checkpoint(
                    db,
                    claim,
                    action_seq=0,
                    state={"phase4a2_prerequisite": True, "task_id": claim.task_id},
                    budget_usage={"iterations": 1, "tool_calls": 0},
                    next_state="succeeded",
                    now=task_time + timedelta(seconds=2),
                )
                complete_task_attempt(
                    db,
                    claim,
                    completion=TaskCompletionReceipt(
                        checkpoint_id=checkpoint.checkpoint_id,
                        state_hash=checkpoint.state_hash,
                        output_hash=canonical_hash({"task_id": claim.task_id}),
                        completion_contract=claim.task_contract["completion_contract"],
                        validator_version="bid-task-output-validator-v1",
                    ),
                    plan_continuation_enabled=True,
                    now=task_time + timedelta(seconds=3),
                )
        finally:
            db.close()

    first_step_time = now + timedelta(seconds=35)
    db = session_factory()
    try:
        with db.begin():
            task_claim = lease_next_ready_task(
                db,
                worker_id="phase4a2-langgraph-worker",
                now=first_step_time,
            )
            assert task_claim is not None
            assert task_claim.task_contract["skill_binding"]["executor_kind"] == "langgraph"
            start_task_attempt(db, task_claim, now=first_step_time + timedelta(seconds=1))
            first_step = advance_local_agent_one_action(
                db,
                task_claim,
                tool_scope_signing_key="phase4a2-local-test-signing-key-32chars",
                now=first_step_time + timedelta(seconds=2),
            )
            assert first_step.operation_type == "request_model"
            assert first_step.operation_ref is not None
    finally:
        db.close()
    return assessment_id, run_id, task_claim, first_step_time, first_step


def test_phase4a2_model_budget_failure_rolls_back_claim_and_settlement(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, _run_id, task_claim, step_time, first_step = (
        _prepare_phase4a2_pending_model_call(
            api_runtime,
            now=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    db = session_factory()
    try:
        with pytest.raises(BidModelBudgetExhausted):
            with db.begin():
                call = db.query(BidModelCall).filter_by(task_id=task_claim.task_id).one()
                replay = schedule_model_call(
                    db,
                    task_claim,
                    context_manifest_id=str(call.context_manifest_id),
                    checkpoint_id=str(first_step.checkpoint_id),
                    action_seq=int(call.action_seq),
                    idempotency_key=str(call.idempotency_key),
                    now=step_time + timedelta(seconds=3),
                )
                assert replay.duplicate is True
                model_claim = claim_model_call(
                    db,
                    worker_id="phase4a2-budget-worker",
                    now=step_time + timedelta(seconds=4),
                )
                assert model_claim is not None
                mark_model_call_sending(
                    db,
                    model_claim,
                    now=step_time + timedelta(seconds=5),
                )
                settle_model_call(
                    db,
                    model_claim,
                    provider_result=ModelProviderResult(
                        action={
                            "action_type": "finish",
                            "completion_summary": "must roll back",
                            "output_candidate": None,
                            "reason_codes": ["TASK_ACTION_READY"],
                        },
                        usage={
                            "input_tokens": int(call.reserved_input_tokens) + 1,
                            "output_tokens": 1,
                        },
                        finish_reason="stop",
                    ),
                    now=step_time + timedelta(seconds=6),
                )
    finally:
        db.close()

    db = session_factory()
    try:
        call = db.query(BidModelCall).filter_by(task_id=task_claim.task_id).one()
        assert call.status == "accepted"
        assert call.attempt_count == 0
        assert db.query(BidModelCallAttempt).filter_by(model_call_id=call.id).count() == 0
        assert db.query(BidModelResult).filter_by(model_call_id=call.id).count() == 0
    finally:
        db.close()


def test_phase4a2_model_heartbeat_fencing_and_send_unknown_recovery(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, _run_id, task_claim, step_time, _first_step = (
        _prepare_phase4a2_pending_model_call(
            api_runtime,
            now=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    db = session_factory()
    try:
        with db.begin():
            first_claim = claim_model_call(
                db,
                worker_id="phase4a2-fence-worker",
                lease_seconds=15,
                now=step_time + timedelta(seconds=4),
            )
            assert first_claim is not None
            extended = heartbeat_model_call(
                db,
                first_claim,
                lease_seconds=30,
                now=step_time + timedelta(seconds=10),
            )
            assert extended == step_time + timedelta(seconds=40)
            with pytest.raises(BidModelFenceLost):
                heartbeat_model_call(
                    db,
                    replace(first_claim, fencing_token=first_claim.fencing_token + 1),
                    now=step_time + timedelta(seconds=11),
                )
            mark_model_call_sending(
                db,
                first_claim,
                now=step_time + timedelta(seconds=12),
            )
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            recovered = recover_expired_model_calls(
                db,
                now=step_time + timedelta(seconds=41),
            )
            assert recovered.scanned == 1
            assert recovered.recovered == 1
            assert recovered.uncertain == 1
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            second_claim = claim_model_call(
                db,
                worker_id="phase4a2-fence-worker",
                now=step_time + timedelta(seconds=42),
            )
            assert second_claim is not None
            assert second_claim.fencing_token == first_claim.fencing_token + 1
            assert second_claim.provider_request_id != first_claim.provider_request_id
            with pytest.raises(BidModelFenceLost):
                settle_model_call(
                    db,
                    first_claim,
                    provider_result=ModelProviderResult(
                        action={
                            "action_type": "finish",
                            "completion_summary": "stale result",
                            "output_candidate": None,
                            "reason_codes": ["STALE_ATTEMPT"],
                        },
                        usage={"input_tokens": 1, "output_tokens": 1},
                        finish_reason="stop",
                    ),
                    now=step_time + timedelta(seconds=43),
                )
            mark_model_call_sending(
                db,
                second_claim,
                now=step_time + timedelta(seconds=44),
            )
            settled = settle_model_call(
                db,
                second_claim,
                provider_result=ModelProviderResult(
                    action={
                        "action_type": "finish",
                        "completion_summary": "retry settled",
                        "output_candidate": {"status": "candidate_only"},
                        "reason_codes": ["TASK_ACTION_READY"],
                    },
                    usage={"input_tokens": 10, "output_tokens": 5},
                    finish_reason="stop",
                    provider_receipt_id="phase4a2-retry-receipt",
                    actual_cost_microunits=10,
                ),
                now=step_time + timedelta(seconds=45),
            )
            assert settled.duplicate is False
    finally:
        db.close()

    db = session_factory()
    try:
        attempts = (
            db.query(BidModelCallAttempt)
            .join(BidModelCall, BidModelCall.id == BidModelCallAttempt.model_call_id)
            .filter(BidModelCall.task_id == task_claim.task_id)
            .order_by(BidModelCallAttempt.attempt_no.asc())
            .all()
        )
        assert [row.status for row in attempts] == ["uncertain", "succeeded"]
    finally:
        db.close()


def test_phase4a2_unclaimed_model_timeout_resumes_task_for_recovery(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id, task_claim, step_time, _first_step = (
        _prepare_phase4a2_pending_model_call(
            api_runtime,
            now=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    db = session_factory()
    try:
        with db.begin():
            result = recover_expired_model_calls(
                db,
                now=step_time + timedelta(seconds=123),
            )
            assert result.scanned == 1
            assert result.failed == 1
    finally:
        db.close()
    db = session_factory()
    try:
        call = db.query(BidModelCall).filter_by(task_id=task_claim.task_id).one()
        task = db.query(BidTask).filter_by(id=task_claim.task_id).one()
        attempt = db.query(BidTaskAttempt).filter_by(id=task_claim.attempt_id).one()
        operation = db.query(BidAsyncOperation).filter_by(id=call.async_operation_id).one()
        run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
        assert (call.status, call.last_error_code) == (
            "failed",
            "BID_MODEL_OPERATION_TIMEOUT",
        )
        assert operation.status == "failed"
        assert attempt.status == "cancelled"
        assert task.status == "ready"
        assert run.status == "queued"
    finally:
        db.close()


def test_phase4a2_injected_provider_io_runs_after_sending_commit(api_runtime) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, _run_id, task_claim, step_time, _first_step = (
        _prepare_phase4a2_pending_model_call(
            api_runtime,
            now=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    db = session_factory()
    try:
        with db.begin():
            model_claim = claim_model_call(
                db,
                worker_id="phase4a2-provider-boundary-worker",
                now=step_time + timedelta(seconds=4),
            )
            assert model_claim is not None
    finally:
        db.close()

    class InspectingProvider:
        def execute(self, *, request_envelope, provider_request_id):
            provider_db = session_factory()
            try:
                call = (
                    provider_db.query(BidModelCall)
                    .filter_by(id=request_envelope["model_call_id"])
                    .one()
                )
                attempt = (
                    provider_db.query(BidModelCallAttempt)
                    .filter_by(provider_request_id=provider_request_id)
                    .one()
                )
                operation = (
                    provider_db.query(BidAsyncOperation)
                    .filter_by(id=call.async_operation_id)
                    .one()
                )
                assert call.status == "sending"
                assert attempt.status == "sending"
                assert operation.status == "running"
                assert provider_db.query(BidModelResult).filter_by(model_call_id=call.id).count() == 0
            finally:
                provider_db.close()
            return ModelProviderResult(
                action={
                    "action_type": "finish",
                    "completion_summary": "provider transaction boundary verified",
                    "output_candidate": {"status": "candidate_only"},
                    "reason_codes": ["TASK_ACTION_READY"],
                },
                usage={"input_tokens": 20, "output_tokens": 5},
                finish_reason="stop",
                provider_receipt_id="phase4a2-boundary-receipt",
                actual_cost_microunits=20,
            )

    receipt = execute_model_call_claim(
        session_factory,
        claim=model_claim,
        provider=InspectingProvider(),
    )
    assert receipt.duplicate is False
    db = session_factory()
    try:
        call = db.query(BidModelCall).filter_by(task_id=task_claim.task_id).one()
        assert call.status == "succeeded"
        assert db.query(BidModelResult).filter_by(model_call_id=call.id).count() == 1
    finally:
        db.close()


def test_phase4b2_rejected_provider_response_is_accounted_before_retry(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, _run_id, task_claim, step_time, _first_step = (
        _prepare_phase4a2_pending_model_call(
            api_runtime,
            now=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    db = session_factory()
    try:
        with db.begin():
            first_claim = claim_model_call(
                db,
                worker_id="phase4b2-accounting-worker",
                now=step_time + timedelta(seconds=4),
            )
            assert first_claim is not None
            mark_model_call_sending(
                db,
                first_claim,
                now=step_time + timedelta(seconds=5),
            )
            status = fail_model_call_attempt(
                db,
                first_claim,
                error_code="BID_MODEL_ACTION_INVALID",
                retryable=True,
                send_started=False,
                retry_delay_seconds=1,
                provider_result=ModelProviderResult(
                    action={"action_type": "finish", "summary": "invalid"},
                    usage={"input_tokens": 20, "output_tokens": 5},
                    finish_reason="stop",
                    provider_receipt_id="phase4b2-rejected-receipt",
                    actual_cost_microunits=7,
                ),
                validation_issues=[
                    {
                        "loc": ["finish", "completion_summary"],
                        "type": "missing",
                        "message": "Field required",
                    }
                ],
                now=step_time + timedelta(seconds=6),
            )
            assert status == "retry_wait"
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            second_claim = claim_model_call(
                db,
                worker_id="phase4b2-accounting-worker",
                now=step_time + timedelta(seconds=8),
            )
            assert second_claim is not None
            assert second_claim.model_call_id == first_claim.model_call_id
            mark_model_call_sending(
                db,
                second_claim,
                now=step_time + timedelta(seconds=9),
            )
            receipt = settle_model_call(
                db,
                second_claim,
                provider_result=ModelProviderResult(
                    action={
                        "action_type": "finish",
                        "completion_summary": "retry settled",
                        "output_candidate": None,
                        "reason_codes": ["TASK_ACTION_READY"],
                    },
                    usage={"input_tokens": 10, "output_tokens": 4},
                    finish_reason="stop",
                    provider_receipt_id="phase4b2-success-receipt",
                    actual_cost_microunits=3,
                ),
                now=step_time + timedelta(seconds=10),
            )
            assert receipt.duplicate is False
    finally:
        db.close()

    db = session_factory()
    try:
        call = db.query(BidModelCall).filter_by(task_id=task_claim.task_id).one()
        attempts = (
            db.query(BidModelCallAttempt)
            .filter_by(model_call_id=call.id)
            .order_by(BidModelCallAttempt.attempt_no.asc())
            .all()
        )
        result = db.query(BidModelResult).filter_by(model_call_id=call.id).one()
        assert call.status == "succeeded"
        assert (
            call.actual_input_tokens,
            call.actual_output_tokens,
            call.actual_cost_microunits,
        ) == (30, 9, 10)
        assert (result.input_tokens, result.output_tokens, result.actual_cost_microunits) == (
            10,
            4,
            3,
        )
        assert [row.status for row in attempts] == ["failed", "succeeded"]
        assert attempts[0].detail_json["validation_issues"][0]["type"] == "missing"
        assert attempts[0].detail_json["usage"] == {
            "input_tokens": 20,
            "output_tokens": 5,
        }
        assert attempts[1].detail_json["usage"] == {
            "input_tokens": 10,
            "output_tokens": 4,
        }
    finally:
        db.close()


def test_phase4a2_provider_exception_without_stable_code_is_normalized(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, _run_id, task_claim, step_time, _first_step = (
        _prepare_phase4a2_pending_model_call(
            api_runtime,
            now=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    db = session_factory()
    try:
        with db.begin():
            model_claim = claim_model_call(
                db,
                worker_id="phase4a2-provider-error-worker",
                now=step_time + timedelta(seconds=4),
            )
            assert model_claim is not None
    finally:
        db.close()

    class ProviderFailureWithoutCode:
        def execute(self, *, request_envelope, provider_request_id):
            del request_envelope, provider_request_id
            error = RuntimeError("synthetic provider failure")
            error.code = None
            error.retryable = False
            raise error

    assert execute_model_call_claim(
        session_factory,
        claim=model_claim,
        provider=ProviderFailureWithoutCode(),
    ) == "uncertain"
    db = session_factory()
    try:
        call = db.query(BidModelCall).filter_by(task_id=task_claim.task_id).one()
        attempt = (
            db.query(BidModelCallAttempt)
            .filter_by(model_call_id=call.id)
            .one()
        )
        task = db.query(BidTask).filter_by(id=task_claim.task_id).one()
        assert call.last_error_code == "BID_MODEL_PROVIDER_ERROR"
        assert attempt.error_code == "BID_MODEL_PROVIDER_ERROR"
        assert task.status == "ready"
    finally:
        db.close()


def test_phase4a2_run_cancel_fences_sending_model_attempt(api_runtime) -> None:
    client, session_factory, _owner = api_runtime
    assessment_id, run_id, task_claim, step_time, _first_step = (
        _prepare_phase4a2_pending_model_call(
            api_runtime,
            now=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    db = session_factory()
    try:
        with db.begin():
            model_claim = claim_model_call(
                db,
                worker_id="phase4a2-cancel-worker",
                now=step_time + timedelta(seconds=4),
            )
            assert model_claim is not None
            mark_model_call_sending(
                db,
                model_claim,
                now=step_time + timedelta(seconds=5),
            )
    finally:
        db.close()

    progress = client.get(f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}")
    requested = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel",
        headers={"Idempotency-Key": _key(), "If-Match": progress.headers["etag"]},
        json={"reason": "phase4a2 governed model cancellation"},
    )
    assert requested.status_code == 202
    maintenance = maintain_run_lifecycle(
        session_factory=session_factory,
        now=step_time + timedelta(seconds=10),
    )
    assert maintenance.cancelled == 1

    db = session_factory()
    try:
        call = db.query(BidModelCall).filter_by(task_id=task_claim.task_id).one()
        attempt = (
            db.query(BidModelCallAttempt)
            .filter_by(model_call_id=call.id)
            .one()
        )
        operation = db.query(BidAsyncOperation).filter_by(id=call.async_operation_id).one()
        assert call.status == "cancelled"
        assert attempt.status == "cancelled"
        assert operation.status == "cancelled"
        with pytest.raises(BidModelFenceLost):
            settle_model_call(
                db,
                model_claim,
                provider_result=ModelProviderResult(
                    action={
                        "action_type": "finish",
                        "completion_summary": "late result",
                        "output_candidate": None,
                        "reason_codes": ["LATE_RESULT"],
                    },
                    usage={"input_tokens": 1, "output_tokens": 1},
                    finish_reason="stop",
                ),
                now=step_time + timedelta(seconds=11),
            )
    finally:
        db.close()


def test_phase4a2_model_gateway_resumes_one_bounded_langgraph_action(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    _assessment_id, run_id = _create_phase3c_committed_run(
        api_runtime,
        phase4_plan_continuation=True,
        phase4_model_gateway=True,
    )
    now = datetime.now(timezone.utc) + timedelta(minutes=1)

    # P0 begins with three deterministic control tasks.  Complete only those
    # prerequisites, then hand the first LangGraph-bound extraction Task to A-2.
    for index in range(3):
        task_time = now + timedelta(seconds=index * 10)
        db = session_factory()
        try:
            with db.begin():
                claim = lease_next_ready_task(
                    db,
                    worker_id="phase4a2-deterministic-worker",
                    now=task_time,
                )
                assert claim is not None
                assert claim.task_contract["skill_binding"]["executor_kind"] == "deterministic"
                start_task_attempt(db, claim, now=task_time + timedelta(seconds=1))
                checkpoint = write_task_checkpoint(
                    db,
                    claim,
                    action_seq=0,
                    state={"phase4a2_prerequisite": True, "task_id": claim.task_id},
                    budget_usage={"iterations": 1, "tool_calls": 0},
                    next_state="succeeded",
                    now=task_time + timedelta(seconds=2),
                )
                complete_task_attempt(
                    db,
                    claim,
                    completion=TaskCompletionReceipt(
                        checkpoint_id=checkpoint.checkpoint_id,
                        state_hash=checkpoint.state_hash,
                        output_hash=canonical_hash({"task_id": claim.task_id}),
                        completion_contract=claim.task_contract["completion_contract"],
                        validator_version="bid-task-output-validator-v1",
                    ),
                    plan_continuation_enabled=True,
                    now=task_time + timedelta(seconds=3),
                )
        finally:
            db.close()

    first_step_time = now + timedelta(seconds=35)
    db = session_factory()
    try:
        with db.begin():
            task_claim = lease_next_ready_task(
                db,
                worker_id="phase4a2-langgraph-worker",
                now=first_step_time,
            )
            assert task_claim is not None
            assert task_claim.task_contract["skill_binding"]["executor_kind"] == "langgraph"
            start_task_attempt(db, task_claim, now=first_step_time + timedelta(seconds=1))
            first_step = advance_local_agent_one_action(
                db,
                task_claim,
                tool_scope_signing_key="phase4a2-local-test-signing-key-32chars",
                now=first_step_time + timedelta(seconds=2),
            )
            assert first_step.operation_type == "request_model"
            assert first_step.action_seq == 1
            assert first_step.operation_ref is not None
    finally:
        db.close()

    provider_time = first_step_time + timedelta(seconds=4)
    db = session_factory()
    try:
        with db.begin():
            model_claim = claim_model_call(
                db,
                worker_id="phase4a2-model-worker",
                now=provider_time,
            )
            assert model_claim is not None
            assert model_claim.provider_ref == "local-test-provider"
            mark_model_call_sending(
                db,
                model_claim,
                now=provider_time + timedelta(seconds=1),
            )
            provider_result = ModelProviderResult(
                action={
                    "action_type": "finish",
                    "completion_summary": "Bounded extraction candidate is ready",
                    "output_candidate": {"status": "candidate_only"},
                    "reason_codes": ["TASK_ACTION_READY"],
                },
                usage={"input_tokens": 120, "output_tokens": 40},
                finish_reason="stop",
                provider_receipt_id="local-receipt-01",
                actual_cost_microunits=1200,
            )
            result = settle_model_call(
                db,
                model_claim,
                provider_result=provider_result,
                now=provider_time + timedelta(seconds=2),
            )
            replay = settle_model_call(
                db,
                model_claim,
                provider_result=provider_result,
                now=provider_time + timedelta(seconds=3),
            )
            assert result.duplicate is False
            assert replay.duplicate is True
            assert replay.result_hash == result.result_hash
    finally:
        db.close()

    resume_time = provider_time + timedelta(seconds=5)
    db = session_factory()
    try:
        with db.begin():
            resumed_claim = lease_next_ready_task(
                db,
                worker_id="phase4a2-langgraph-worker",
                allowed_task_types=[task_claim.task_contract["task_type"]],
                now=resume_time,
            )
            assert resumed_claim is not None
            assert resumed_claim.task_id == task_claim.task_id
            assert resumed_claim.attempt_no == task_claim.attempt_no + 1
            assert resumed_claim.fencing_token > task_claim.fencing_token
            start_task_attempt(db, resumed_claim, now=resume_time + timedelta(seconds=1))
            resumed = advance_local_agent_one_action(
                db,
                resumed_claim,
                tool_scope_signing_key="phase4a2-local-test-signing-key-32chars",
                now=resume_time + timedelta(seconds=2),
            )
            duplicate = advance_local_agent_one_action(
                db,
                resumed_claim,
                tool_scope_signing_key="phase4a2-local-test-signing-key-32chars",
                now=resume_time + timedelta(seconds=3),
            )
            assert resumed.operation_type == "finish"
            assert resumed.completion_ready is True
            assert duplicate == resumed
    finally:
        db.close()

    db = session_factory()
    try:
        assert db.query(BidModelCall).filter_by(run_id=run_id).count() == 1
        assert db.query(BidModelCallAttempt).count() == 1
        assert db.query(BidModelResult).count() == 1
        task = db.query(BidTask).filter_by(id=task_claim.task_id).one()
        assert task.status == "running"
    finally:
        db.close()


def _complete_phase3c_dag_for_phase3g(api_runtime, *, now: datetime):
    _assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    _client, session_factory, _owner = api_runtime
    for index in range(8):
        db = session_factory()
        try:
            with db.begin():
                claim = lease_next_ready_task(
                    db,
                    worker_id="phase3g-task-worker",
                    now=now + timedelta(seconds=index * 10),
                )
                assert claim is not None
                start_task_attempt(
                    db,
                    claim,
                    now=now + timedelta(seconds=index * 10 + 1),
                )
                checkpoint = write_task_checkpoint(
                    db,
                    claim,
                    action_seq=0,
                    state={"phase3g": True, "task_id": claim.task_id},
                    budget_usage={"iterations": 1, "tool_calls": 0},
                    next_state="succeeded",
                    now=now + timedelta(seconds=index * 10 + 2),
                )
                completion = complete_task_attempt(
                    db,
                    claim,
                    completion=TaskCompletionReceipt(
                        checkpoint_id=checkpoint.checkpoint_id,
                        state_hash=checkpoint.state_hash,
                        output_hash=canonical_hash({"task_id": claim.task_id}),
                        completion_contract=claim.task_contract["completion_contract"],
                        validator_version="bid-task-output-validator-v1",
                    ),
                    now=now + timedelta(seconds=index * 10 + 3),
                )
        finally:
            db.close()
    assert completion.validation_requested is True
    db = session_factory()
    try:
        event = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.validation_requested.v1",
            )
            .one()
        )
        return run_id, str(event.event_id)
    finally:
        db.close()


def test_phase3g_validation_materializes_idempotently_and_converges_success(
    api_runtime,
) -> None:
    client, session_factory, _owner = api_runtime
    now = datetime(2026, 8, 12, 16, 0, tzinfo=timezone.utc)
    run_id, event_id = _complete_phase3c_dag_for_phase3g(api_runtime, now=now)
    db = session_factory()
    try:
        with db.begin():
            first = consume_run_validation_requested_event(
                db,
                event_id=event_id,
                now=now + timedelta(seconds=90),
            )
            assert first.duplicate is False
        with db.begin():
            replay = consume_run_validation_requested_event(
                db,
                event_id=event_id,
                now=now + timedelta(seconds=91),
            )
            assert replay.duplicate is True
        with db.begin():
            claim = claim_next_run_validation(
                db,
                worker_id="phase3g-validator",
                now=now + timedelta(seconds=92),
            )
            assert claim is not None
            heartbeat_run_validation(
                db,
                claim,
                now=now + timedelta(seconds=93),
            )
            result = execute_run_validation_claim(
                db,
                claim,
                now=now + timedelta(seconds=94),
            )
            assert result.outcome == "passed"
            assert result.run_status == "succeeded"
    finally:
        db.close()
    db = session_factory()
    try:
        validation = db.query(BidRunValidation).filter_by(run_id=run_id).one()
        run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
        assessment = db.query(BidAssessment).filter_by(id=run.assessment_id).one()
        assert validation.status == "passed"
        assert validation.result_hash
        assert run.status == "succeeded"
        assert assessment.business_status == "deep_ready"
        succeeded_event = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.succeeded.v1",
            )
            .one()
        )
        succeeded_event_id = str(succeeded_event.event_id)
        assessment_id = str(run.assessment_id)
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            projected = project_outbox_event_to_public(
                db,
                event_id=succeeded_event_id,
            )
            assert projected.duplicate is False
    finally:
        db.close()
    progress = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert progress.status_code == 200
    assert progress.json()["data"]["status"] == "succeeded"
    assert progress.json()["data"]["latest_event"]["event_type"] == (
        "run.status.changed"
    )
    assert progress.json()["data"]["latest_event"]["resource_version"] == (
        progress.json()["data"]["row_version"]
    )


def test_phase3g_validation_integrity_failure_converges_failed(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    now = datetime(2026, 8, 12, 16, 30, tzinfo=timezone.utc)
    run_id, event_id = _complete_phase3c_dag_for_phase3g(api_runtime, now=now)
    db = session_factory()
    try:
        with db.begin():
            consume_run_validation_requested_event(
                db,
                event_id=event_id,
                now=now + timedelta(seconds=90),
            )
            claim = claim_next_run_validation(
                db,
                worker_id="phase3g-integrity-validator",
                now=now + timedelta(seconds=91),
            )
            assert claim is not None
            task = (
                db.query(BidTask)
                .filter(BidTask.run_id == run_id)
                .order_by(BidTask.task_key.asc())
                .first()
            )
            assert task is not None
            assert task.current_attempt_id is not None
            db.add(
                BidAsyncOperation(
                    id=str(uuid.uuid4()),
                    task_id=str(task.id),
                    task_attempt_id=str(task.current_attempt_id),
                    operation_type="phase3g_integrity_probe",
                    status="submitted",
                    input_hash=canonical_hash({"run_id": run_id, "task_id": task.id}),
                    retry_count=0,
                    submitted_at=now + timedelta(seconds=92),
                    row_version=1,
                )
            )
            result = execute_run_validation_claim(
                db,
                claim,
                now=now + timedelta(seconds=93),
            )
            assert result.outcome == "failed"
            assert result.run_status == "failed"
    finally:
        db.close()
    db = session_factory()
    try:
        validation = db.query(BidRunValidation).filter_by(run_id=run_id).one()
        run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
        assessment = db.query(BidAssessment).filter_by(id=run.assessment_id).one()
        assert validation.status == "failed"
        assert validation.failure_code == "BID_RUN_VALIDATION_INPUT_DRIFT"
        assert validation.result_json["summary"]["failed_codes"] == [
            "NO_ACTIVE_ASYNC_OPERATIONS",
            "VALIDATION_INPUT_IMMUTABLE",
        ]
        assert run.status == "failed"
        assert run.retryable is False
        assert assessment.business_status == "failed"
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.failed.v1",
            )
            .count()
            == 1
        )
    finally:
        db.close()


def test_phase3g_validation_stale_fails_closed_and_fences_old_worker(
    api_runtime,
) -> None:
    _client, session_factory, _owner = api_runtime
    now = datetime(2026, 8, 12, 17, 0, tzinfo=timezone.utc)
    run_id, event_id = _complete_phase3c_dag_for_phase3g(api_runtime, now=now)
    db = session_factory()
    try:
        with db.begin():
            consume_run_validation_requested_event(
                db,
                event_id=event_id,
                now=now + timedelta(seconds=90),
            )
            first = claim_next_run_validation(
                db,
                worker_id="phase3g-old-validator",
                lease_seconds=15,
                now=now + timedelta(seconds=91),
            )
            assert first is not None
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            assert recover_expired_run_validation(
                db,
                validation_id=first.validation_id,
                now=now + timedelta(seconds=107),
            ) == "recovered"
            second = claim_next_run_validation(
                db,
                worker_id="phase3g-new-validator",
                now=now + timedelta(seconds=108),
            )
            assert second is not None
            assert second.fencing_token == first.fencing_token + 1
            with pytest.raises(BidRunValidationFenceLost):
                heartbeat_run_validation(
                    db,
                    first,
                    now=now + timedelta(seconds=109),
                )
            run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
            assessment = db.query(BidAssessment).filter_by(id=run.assessment_id).one()
            assessment.current_manifest_id = None
            assessment.row_version = int(assessment.row_version) + 1
            result = execute_run_validation_claim(
                db,
                second,
                now=now + timedelta(seconds=110),
            )
            assert result.outcome == "stale"
            assert result.run_status == "stale"
    finally:
        db.close()


def test_phase3g_validation_transaction_rollback_and_maintenance_recovery(
    api_runtime,
    monkeypatch,
) -> None:
    _client, session_factory, _owner = api_runtime
    now = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
    run_id, event_id = _complete_phase3c_dag_for_phase3g(api_runtime, now=now)

    def _raise_audit(*_args, **_kwargs):
        raise RuntimeError("phase3g-audit-rollback")

    monkeypatch.setattr(run_validation_service, "append_audit_log", _raise_audit)
    db = session_factory()
    try:
        with pytest.raises(RuntimeError, match="phase3g-audit-rollback"):
            with db.begin():
                consume_run_validation_requested_event(
                    db,
                    event_id=event_id,
                    now=now + timedelta(seconds=90),
                )
    finally:
        db.close()
    db = session_factory()
    try:
        assert db.query(BidRunValidation).filter_by(run_id=run_id).count() == 0
        assert (
            db.query(BidProcessedEvent)
            .filter_by(
                consumer_name="bid-run-validation-v1",
                event_id=event_id,
            )
            .count()
            == 0
        )
    finally:
        db.close()
    monkeypatch.undo()
    maintenance = maintain_run_validations(
        session_factory=session_factory,
        now=now + timedelta(seconds=91),
    )
    assert maintenance.scanned_events == 1
    assert maintenance.materialized == 1


def test_phase3g_run_cancel_fences_active_validation_attempt(
    api_runtime,
) -> None:
    client, session_factory, _owner = api_runtime
    now = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)
    run_id, event_id = _complete_phase3c_dag_for_phase3g(api_runtime, now=now)
    db = session_factory()
    try:
        with db.begin():
            consume_run_validation_requested_event(
                db,
                event_id=event_id,
                now=now + timedelta(seconds=90),
            )
            claim = claim_next_run_validation(
                db,
                worker_id="phase3g-cancelled-validator",
                now=now + timedelta(seconds=91),
            )
            assert claim is not None
            heartbeat_run_validation(
                db,
                claim,
                now=now + timedelta(seconds=92),
            )
    finally:
        db.close()

    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
        assessment_id = str(run.assessment_id)
    finally:
        db.close()
    run_url = f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    progress = client.get(run_url)
    assert progress.status_code == 200
    requested = client.post(
        f"{run_url}/cancel",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": progress.headers["etag"],
        },
        json={"reason": "operator cancelled during run validation"},
    )
    assert requested.status_code == 202
    maintenance = maintain_run_lifecycle(
        session_factory=session_factory,
        now=now + timedelta(seconds=100),
    )
    assert maintenance.scanned == maintenance.cancelled == 1

    db = session_factory()
    try:
        validation = db.query(BidRunValidation).filter_by(run_id=run_id).one()
        attempt = (
            db.query(BidRunValidationAttempt)
            .filter_by(validation_id=validation.id, id=claim.attempt_id)
            .one()
        )
        run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
        assessment = db.query(BidAssessment).filter_by(id=assessment_id).one()
        assert validation.status == "cancelled"
        assert validation.failure_code == "BID_RUN_CANCELLED"
        assert validation.lease_owner is None
        assert validation.lease_until is None
        assert attempt.status == "cancelled"
        assert attempt.error_code == "BID_RUN_CANCELLED"
        assert run.status == "cancelled"
        assert assessment.business_status == "cancelled"
        with pytest.raises(BidRunValidationFenceLost):
            with db.begin_nested():
                heartbeat_run_validation(db, claim)
    finally:
        db.close()


def test_phase3_closeout_api40_tool_checkpoint_validation_api41_sse_chain(
    api_runtime,
) -> None:
    """Exercise the complete deterministic A-G control-plane path without external I/O."""

    client, session_factory, _owner = api_runtime
    now = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
    session_factory, run_id, version_id = _prepare_phase3f_documents_outline(
        api_runtime,
        now=now,
    )

    db = session_factory()
    try:
        with db.begin():
            first_claim = lease_next_ready_task(
                db,
                worker_id="phase3-closeout-tool-task",
                now=now,
            )
            assert first_claim is not None
            start_task_attempt(db, first_claim, now=now + timedelta(seconds=1))
            context = assemble_context_manifest(
                db,
                first_claim,
                working_state={"phase3_closeout": "tool_dispatch"},
                now=now + timedelta(seconds=1),
            )
            invocation = authorize_tool_invocation(
                db,
                first_claim,
                context_manifest_id=context.context_manifest_id,
                tool_name="documents.outline",
                arguments={"document_version_id": version_id, "max_depth": 4},
                idempotency_key="phase3-closeout-outline-0001",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now + timedelta(seconds=2),
            )
            pending_checkpoint = write_task_checkpoint(
                db,
                first_claim,
                action_seq=0,
                state={"pending_invocation_id": invocation.invocation_id},
                context_manifest_id=context.context_manifest_id,
                budget_usage={"iterations": 1, "tool_calls": 1},
                now=now + timedelta(seconds=2),
            )
            dispatch = enqueue_tool_dispatch(
                db,
                first_claim,
                invocation_id=invocation.invocation_id,
                checkpoint_id=pending_checkpoint.checkpoint_id,
                scope_token=invocation.call_envelope["scope_token"],
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                timeout_seconds=60,
                now=now + timedelta(seconds=2),
            )
            assert dispatch.status == "queued"
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            dispatch_claim = claim_next_tool_dispatch(
                db,
                worker_id="phase3-closeout-tool-executor",
                scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
                now=now + timedelta(seconds=3),
            )
            assert dispatch_claim is not None
    finally:
        db.close()
    assert execute_tool_dispatch_claim(
        session_factory=session_factory,
        claim=dispatch_claim,
        now=now + timedelta(seconds=4),
    ) == "succeeded"

    db = session_factory()
    try:
        result_row = db.query(BidToolResult).one()
        result_id = str(result_row.id)
        result_hash = str(result_row.result_hash)
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            resumed = lease_next_ready_task(
                db,
                worker_id="phase3-closeout-resumed-task",
                now=now + timedelta(seconds=5),
            )
            assert resumed is not None
            assert resumed.task_id == first_claim.task_id
            assert resumed.attempt_no == first_claim.attempt_no + 1
            assert resumed.fencing_token == first_claim.fencing_token + 1
            assert resumed.resume_checkpoint["checkpoint_id"] == (
                pending_checkpoint.checkpoint_id
            )
            start_task_attempt(db, resumed, now=now + timedelta(seconds=6))
            result_slice = read_tool_result_slice(
                db,
                resumed,
                result_ref_id=result_id,
                now=now + timedelta(seconds=6),
            )
            assert result_slice["result_hash"] == result_hash
            final_checkpoint = write_task_checkpoint(
                db,
                resumed,
                action_seq=0,
                state={
                    "phase3_closeout": "tool_result_consumed",
                    "result_hash": result_hash,
                },
                tool_refs=[{"result_id": result_id, "result_hash": result_hash}],
                budget_usage={"iterations": 2, "tool_calls": 1},
                next_state="succeeded",
                now=now + timedelta(seconds=7),
            )
            completion = complete_task_attempt(
                db,
                resumed,
                completion=TaskCompletionReceipt(
                    checkpoint_id=final_checkpoint.checkpoint_id,
                    state_hash=final_checkpoint.state_hash,
                    output_hash=canonical_hash(
                        {"task_id": resumed.task_id, "result_hash": result_hash}
                    ),
                    completion_contract=resumed.task_contract["completion_contract"],
                    validator_version="bid-task-output-validator-v1",
                ),
                now=now + timedelta(seconds=8),
            )
            assert completion.validation_requested is False
    finally:
        db.close()

    completed_task_ids = {first_claim.task_id}
    completion = None
    for index in range(7):
        task_now = now + timedelta(seconds=20 + index * 10)
        db = session_factory()
        try:
            with db.begin():
                claim = lease_next_ready_task(
                    db,
                    worker_id=f"phase3-closeout-task-{index}",
                    now=task_now,
                )
                assert claim is not None
                start_task_attempt(db, claim, now=task_now + timedelta(seconds=1))
                checkpoint = write_task_checkpoint(
                    db,
                    claim,
                    action_seq=0,
                    state={"phase3_closeout": True, "task_id": claim.task_id},
                    budget_usage={"iterations": 1, "tool_calls": 0},
                    next_state="succeeded",
                    now=task_now + timedelta(seconds=2),
                )
                completion = complete_task_attempt(
                    db,
                    claim,
                    completion=TaskCompletionReceipt(
                        checkpoint_id=checkpoint.checkpoint_id,
                        state_hash=checkpoint.state_hash,
                        output_hash=canonical_hash({"task_id": claim.task_id}),
                        completion_contract=claim.task_contract["completion_contract"],
                        validator_version="bid-task-output-validator-v1",
                    ),
                    now=task_now + timedelta(seconds=3),
                )
                completed_task_ids.add(claim.task_id)
        finally:
            db.close()
    assert len(completed_task_ids) == 8
    assert completion is not None and completion.validation_requested is True

    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
        assessment_id = str(run.assessment_id)
        validation_event = (
            db.query(BidOutboxEvent)
            .filter_by(
                run_id=run_id,
                event_type="bid.run.validation_requested.v1",
            )
            .one()
        )
        validation_event_id = str(validation_event.event_id)
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            materialized = consume_run_validation_requested_event(
                db,
                event_id=validation_event_id,
                now=now + timedelta(seconds=100),
            )
            assert materialized.duplicate is False
            claim = claim_next_run_validation(
                db,
                worker_id="phase3-closeout-validator",
                now=now + timedelta(seconds=101),
            )
            assert claim is not None
            validation_result = execute_run_validation_claim(
                db,
                claim,
                now=now + timedelta(seconds=102),
            )
            assert validation_result.outcome == "passed"
            assert validation_result.run_status == "succeeded"
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            _previous_sequence, snapshot_rows = append_stream_control_events(
                db,
                assessment_id=assessment_id,
                request_id="phase3-closeout-sse-snapshot",
                now=now + timedelta(seconds=103),
            )
            snapshot_event_id = str(snapshot_rows[0].event_id)
            terminal_event = (
                db.query(BidOutboxEvent)
                .filter_by(run_id=run_id, event_type="bid.run.succeeded.v1")
                .one()
            )
            projected = project_outbox_event_to_public(
                db,
                event_id=str(terminal_event.event_id),
                now=now + timedelta(seconds=104),
            )
            assert projected.duplicate is False
            sequence_no = int(
                db.query(func.max(BidPublicEvent.sequence_no))
                .filter(BidPublicEvent.assessment_id == assessment_id)
                .scalar()
                or 0
            ) + 1
            closed_payload = {"reason": "run_terminal", "terminal": True}
            db.add(
                BidPublicEvent(
                    id=str(uuid.uuid4()),
                    assessment_id=assessment_id,
                    sequence_no=sequence_no,
                    event_id=f"aevt_{uuid.uuid4().hex}",
                    origin_type="stream_control",
                    source_event_id=None,
                    projection_key=f"stream:phase3-closeout:{run_id}",
                    event_type="stream.closed",
                    resource_type="assessment",
                    resource_id=assessment_id,
                    resource_version=int(terminal_event.aggregate_version),
                    request_id="phase3-closeout-sse-closed",
                    payload_json=closed_payload,
                    payload_hash=canonical_hash(closed_payload),
                    occurred_at=now + timedelta(seconds=105),
                    expires_at=now + timedelta(days=7),
                )
            )
    finally:
        db.close()

    progress = client.get(f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}")
    assert progress.status_code == 200
    assert progress.json()["data"]["status"] == "succeeded"
    assert progress.json()["data"]["latest_event"]["event_type"] == (
        "run.status.changed"
    )
    stream = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/events",
        headers={"Accept": "text/event-stream", "Last-Event-ID": snapshot_event_id},
    )
    assert stream.status_code == 200
    assert "event: run.status.changed" in stream.text
    assert '"to":"succeeded"' in stream.text
    assert "event: stream.closed" in stream.text

    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter_by(id=run_id).one()
        validation = db.query(BidRunValidation).filter_by(run_id=run_id).one()
        assert run.status == "succeeded"
        assert validation.status == "passed"
        assert validation.validator_version == "bid-run-integrity-validator-v2"
        assert validation.result_json["summary"]["failed_codes"] == []
        checks_by_code = {
            row["code"]: row["passed"]
            for row in validation.result_json["checks"]
        }
        lineage_checks = {
            "TASK_ATTEMPT_CHAINS_MONOTONIC",
            "CONTEXT_MANIFEST_LINEAGE_VALID",
            "TOOL_INVOCATION_LINEAGE_VALID",
            "ASYNC_OPERATION_LINEAGE_VALID",
            "TOOL_DISPATCH_LINEAGE_VALID",
            "TOOL_DISPATCH_ATTEMPT_CHAINS_MONOTONIC",
            "TOOL_RESULTS_IMMUTABLE_AND_SCOPED",
            "CHECKPOINT_TOOL_REFS_VALID",
        }
        assert lineage_checks <= set(checks_by_code)
        assert all(checks_by_code[code] for code in lineage_checks)
        assert db.query(BidPlanRevision).filter_by(run_id=run_id, status="committed").count() == 1
        assert db.query(BidTask).filter_by(run_id=run_id, status="succeeded").count() == 8
        assert db.query(BidContextManifest).filter_by(run_id=run_id).count() == 1
        assert db.query(BidToolInvocation).filter_by(run_id=run_id, status="succeeded").count() == 1
        assert db.query(BidToolDispatch).filter_by(status="succeeded").count() == 1
        assert db.query(BidToolResult).count() == 1
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type.in_(
                    (
                        "bid.run.succeeded.v1",
                        "bid.run.failed.v1",
                        "bid.run.stale.v1",
                        "bid.run.cancelled.v1",
                    )
                ),
            )
            .count()
            == 1
        )
    finally:
        db.close()


def test_phase3d_api42_api43_enforce_command_headers_and_strict_bodies(
    api_runtime,
) -> None:
    client, _session_factory, _owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    run_url = f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    progress = client.get(run_url)
    assert progress.status_code == 200
    etag = progress.headers["etag"]

    missing_key = client.post(
        f"{run_url}/cancel",
        headers={"If-Match": etag},
        json={"reason": "负责人取消"},
    )
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["error_code"] == "BID_REQUEST_VALIDATION_FAILED"

    missing_etag = client.post(
        f"{run_url}/cancel",
        headers={"Idempotency-Key": _key()},
        json={"reason": "负责人取消"},
    )
    assert missing_etag.status_code == 428
    assert missing_etag.json()["error"]["error_code"] == "BID_PRECONDITION_REQUIRED"

    weak_etag = client.post(
        f"{run_url}/cancel",
        headers={"Idempotency-Key": _key(), "If-Match": f"W/{etag}"},
        json={"reason": "负责人取消"},
    )
    assert weak_etag.status_code == 400
    assert weak_etag.json()["error"]["error_code"] == "BID_REQUEST_MALFORMED"

    blank_reason = client.post(
        f"{run_url}/cancel",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json={"reason": "   "},
    )
    assert blank_reason.status_code == 422

    invalid_retry_mode = client.post(
        f"{run_url}/retry",
        headers={"Idempotency-Key": _key(), "If-Match": etag},
        json={"retry_mode": "restart_from_beginning", "note": None},
    )
    assert invalid_retry_mode.status_code == 422


def test_phase3d_api42_cancel_is_idempotent_fences_worker_and_settles_atomically(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(
                db,
                worker_id="phase3d-cancel-worker",
                now=now,
            )
            assert claim is not None
            start_task_attempt(db, claim, now=now + timedelta(seconds=1))
            db.add(
                BidAsyncOperation(
                    id=str(uuid.uuid4()),
                    task_id=claim.task_id,
                    task_attempt_id=claim.attempt_id,
                    operation_type="phase3d_test_operation",
                    status="submitted",
                    input_hash=canonical_hash({"attempt_id": claim.attempt_id}),
                    retry_count=0,
                    submitted_at=now + timedelta(seconds=2),
                    row_version=1,
                )
            )
    finally:
        db.close()

    progress = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert progress.status_code == 200
    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel",
        headers={"Idempotency-Key": _key(), "If-Match": progress.headers["etag"]},
        json={"reason": "不属于该用户"},
    )
    assert hidden.status_code == 404

    client.app.state.active_user["value"] = owner
    key = _key()
    requested = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel",
        headers={"Idempotency-Key": key, "If-Match": progress.headers["etag"]},
        json={"reason": "负责人决定暂不继续"},
    )
    assert requested.status_code == 202
    _validate_contract("RunResponse", requested.json())
    requested_snapshot = requested.json()["data"]
    assert requested_snapshot["status"] == "running"
    assert requested_snapshot["current_stage"] == "cancelling"
    assert requested_snapshot["stages"][0]["status"] == "running"
    assert requested_snapshot["cancel_requested_at"] is not None
    assert [row["code"] for row in requested_snapshot["allowed_actions"]] == [
        "run.view_progress"
    ]
    replay = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel",
        headers={"Idempotency-Key": key, "If-Match": progress.headers["etag"]},
        json={"reason": "负责人决定暂不继续"},
    )
    assert replay.status_code == 202
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == requested.json()
    key_reuse = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel",
        headers={"Idempotency-Key": key, "If-Match": progress.headers["etag"]},
        json={"reason": "同一键不得更换取消原因"},
    )
    assert key_reuse.status_code == 409
    assert key_reuse.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"

    db = session_factory()
    try:
        with pytest.raises(BidTaskFenceLost):
            with db.begin():
                heartbeat_task_attempt(
                    db,
                    claim,
                    now=now + timedelta(seconds=10),
                )
    finally:
        db.close()

    maintenance = maintain_run_lifecycle(
        session_factory=session_factory,
        now=now + timedelta(seconds=11),
    )
    assert maintenance.scanned == maintenance.cancelled == 1
    assert maintenance.tasks_cancelled == 8
    assert maintenance.attempts_cancelled == 1
    assert maintenance.operations_cancelled == 1
    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assessment = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == assessment_id)
            .one()
        )
        assert run.status == "cancelled"
        assert run.retryable is False
        assert run.finished_at is not None
        assert assessment.business_status == "cancelled"
        assert db.query(BidTask).filter(BidTask.run_id == run_id, BidTask.status == "cancelled").count() == 8
        assert db.query(BidTaskAttempt).filter_by(id=claim.attempt_id).one().status == "cancelled"
        assert (
            db.query(BidAsyncOperation)
            .filter(BidAsyncOperation.task_attempt_id == claim.attempt_id)
            .one()
            .status
            == "cancelled"
        )
        events = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type.in_(
                    ("bid.run.cancel_requested.v1", "bid.run.cancelled.v1")
                ),
            )
            .order_by(BidOutboxEvent.occurred_at.asc(), BidOutboxEvent.event_id.asc())
            .all()
        )
        event_ids_by_type = {str(row.event_type): str(row.event_id) for row in events}
        assert set(event_ids_by_type) == {
            "bid.run.cancel_requested.v1",
            "bid.run.cancelled.v1",
        }
    finally:
        db.close()
    for event_type in ("bid.run.cancel_requested.v1", "bid.run.cancelled.v1"):
        db = session_factory()
        try:
            with db.begin():
                project_outbox_event_to_public(
                    db,
                    event_id=event_ids_by_type[event_type],
                )
        finally:
            db.close()
    final_snapshot = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert final_snapshot.status_code == 200
    assert final_snapshot.json()["data"]["status"] == "cancelled"
    assert final_snapshot.json()["data"]["latest_event"]["event_type"] == (
        "run.status.changed"
    )


def test_phase3d_api43_creates_attempt_reuses_fence_and_exposes_resume_checkpoint(
    api_runtime,
) -> None:
    client, session_factory, _owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            first = lease_next_ready_task(
                db,
                worker_id="phase3d-retry-old",
                now=now,
            )
            assert first is not None
            start_task_attempt(db, first, now=now + timedelta(seconds=1))
            checkpoint = write_task_checkpoint(
                db,
                first,
                action_seq=0,
                state={"phase": "recoverable"},
                tool_refs=[],
                budget_usage={"iterations": 1, "tool_calls": 0},
                next_state="running",
                now=now + timedelta(seconds=2),
            )
            failed = fail_task_attempt(
                db,
                first,
                error_code="BID_QUEUE_UNAVAILABLE",
                retryable=True,
                max_attempts=1,
                now=now + timedelta(seconds=3),
            )
            assert failed.run_status == "failed"
            assert failed.retry_scheduled is False
    finally:
        db.close()

    failed_snapshot = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert failed_snapshot.status_code == 200
    action_codes = {
        row["code"] for row in failed_snapshot.json()["data"]["allowed_actions"]
    }
    assert {"run.cancel", "run.retry_from_checkpoint"}.issubset(action_codes)
    key = _key()
    retried = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry",
        headers={
            "Idempotency-Key": key,
            "If-Match": failed_snapshot.headers["etag"],
        },
        json={"retry_mode": "from_latest_checkpoint", "note": "恢复失败任务"},
    )
    assert retried.status_code == 202
    _validate_contract("RunResponse", retried.json())
    assert retried.json()["data"]["status"] == "queued"
    assert retried.json()["data"]["retryable"] is False
    replay = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry",
        headers={
            "Idempotency-Key": key,
            "If-Match": failed_snapshot.headers["etag"],
        },
        json={"retry_mode": "from_latest_checkpoint", "note": "恢复失败任务"},
    )
    assert replay.status_code == 202
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == retried.json()
    key_reuse = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry",
        headers={
            "Idempotency-Key": key,
            "If-Match": failed_snapshot.headers["etag"],
        },
        json={"retry_mode": "from_latest_checkpoint", "note": "改变备注"},
    )
    assert key_reuse.status_code == 409
    assert key_reuse.json()["error"]["error_code"] == "BID_IDEMPOTENCY_KEY_REUSED"
    stale_etag = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": failed_snapshot.headers["etag"],
        },
        json={"retry_mode": "from_latest_checkpoint", "note": None},
    )
    assert stale_etag.status_code == 412

    db = session_factory()
    try:
        attempts = (
            db.query(BidTaskAttempt)
            .filter(BidTaskAttempt.task_id == first.task_id)
            .order_by(BidTaskAttempt.attempt_no.asc())
            .all()
        )
        assert [row.attempt_no for row in attempts] == [1, 2]
        assert [row.fencing_token for row in attempts] == [1, 2]
        assert attempts[0].status == "failed"
        assert attempts[1].status == "created"
        created_attempt_id = str(attempts[1].id)
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.retry_requested.v1",
            )
            .count()
            == 1
        )
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            second = lease_next_ready_task(
                db,
                worker_id="phase3d-retry-new",
                now=now + timedelta(seconds=20),
            )
            assert second is not None
            assert second.attempt_id == created_attempt_id
            assert second.attempt_no == second.fencing_token == 2
            assert second.resume_checkpoint is not None
            assert second.resume_checkpoint["checkpoint_id"] == checkpoint.checkpoint_id
            assert second.resume_checkpoint["state_hash"] == checkpoint.state_hash
            with pytest.raises(BidTaskFenceLost):
                heartbeat_task_attempt(
                    db,
                    first,
                    now=now + timedelta(seconds=21),
                )
    finally:
        db.close()

    current = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    not_retryable = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry",
        headers={"Idempotency-Key": _key(), "If-Match": current.headers["etag"]},
        json={"retry_mode": "from_latest_checkpoint", "note": None},
    )
    assert not_retryable.status_code == 409
    assert not_retryable.json()["error"]["error_code"] == "BID_RUN_NOT_RETRYABLE"


def test_phase3d_api43_rejects_stale_input_without_creating_attempt(api_runtime) -> None:
    client, session_factory, _owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)
    db = session_factory()
    try:
        with db.begin():
            claim = lease_next_ready_task(db, worker_id="phase3d-stale", now=now)
            assert claim is not None
            start_task_attempt(db, claim, now=now + timedelta(seconds=1))
            fail_task_attempt(
                db,
                claim,
                error_code="BID_QUEUE_UNAVAILABLE",
                retryable=True,
                max_attempts=1,
                now=now + timedelta(seconds=2),
            )
    finally:
        db.close()
    failed_snapshot = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    db = session_factory()
    try:
        with db.begin():
            assessment = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == assessment_id)
                .one()
            )
            assessment.business_status = "stale_input"
            assessment.row_version = int(assessment.row_version) + 1
    finally:
        db.close()
    response = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": failed_snapshot.headers["etag"],
        },
        json={"retry_mode": "from_latest_checkpoint", "note": None},
    )
    assert response.status_code == 409
    assert response.json()["error"]["error_code"] == "BID_RUN_INPUT_STALE"
    db = session_factory()
    try:
        assert (
            db.query(BidTaskAttempt)
            .filter(BidTaskAttempt.task_id == claim.task_id)
            .count()
            == 1
        )
    finally:
        db.close()


def test_phase3d_cancel_audit_failure_rolls_back_run_outbox_and_idempotency(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, _owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    progress = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    key = _key()

    def _raise_audit_failure(*_args, **_kwargs):
        raise RuntimeError("synthetic-run-lifecycle-audit-failure")

    monkeypatch.setattr(
        run_lifecycle_service,
        "append_audit_log",
        _raise_audit_failure,
    )
    response = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel",
        headers={"Idempotency-Key": key, "If-Match": progress.headers["etag"]},
        json={"reason": "验证事务回滚"},
    )
    assert response.status_code == 503
    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.cancel_requested_at is None
        assert run.current_stage == "fact_baseline"
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.run.cancel_requested.v1",
            )
            .count()
            == 0
        )
        assert (
            db.query(BidIdempotencyRecord)
            .filter(BidIdempotencyRecord.idempotency_key == key)
            .count()
            == 0
        )
    finally:
        db.close()


def test_phase3b_plan_commit_is_atomic_deterministic_and_exactly_once(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    created = _create_assessment(client)
    assessment_id = created.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    _attach_phase3_scope(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    _activate_phase3_frozen_versions(session_factory, actor_id=owner.id)
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    started = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={"Idempotency-Key": _key(), "If-Match": current.headers["etag"]},
        json={
            "manifest_id": manifest_id,
            "reason": "manual_restart",
            "note": "Phase 3B deterministic plan commit",
        },
    )
    assert started.status_code == 202
    run_id = started.json()["data"]["run_id"]

    db = session_factory()
    try:
        created_event = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.event_type == "bid.run.created.v1",
                BidOutboxEvent.run_id == run_id,
            )
            .one()
        )
        created_event_id = str(created_event.event_id)
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            first = consume_run_created_event(db, event_id=created_event_id)
        assert first.duplicate is False
        assert first.value["committed"] is True
        assert first.value["task_count"] == 8
        assert first.value["ready_task_count"] == 1
    finally:
        db.close()

    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        plan = (
            db.query(BidPlanRevision)
            .filter(BidPlanRevision.run_id == run_id)
            .one()
        )
        tasks = (
            db.query(BidTask)
            .filter(BidTask.run_id == run_id)
            .order_by(BidTask.task_key.asc())
            .all()
        )
        assert run.status == "queued"
        assert run.current_stage == "fact_baseline"
        assert run.row_version == 4
        assert plan.status == "committed"
        assert plan.committed_slot_key == "committed"
        assert plan.row_version == 3
        assert plan.proposal_json["schema"] == "bid.plan.commit.envelope.v1"
        assert plan.proposal_json["validation"]["validated_hash"] == plan.validated_hash
        assert len(tasks) == 8
        assert [task.status for task in tasks].count("ready") == 1
        assert [task.status for task in tasks].count("blocked") == 7
        assert (
            db.query(BidTaskDependency)
            .filter(BidTaskDependency.run_id == run_id)
            .count()
            == 7
        )
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.plan.committed.v1",
            )
            .count()
            == 1
        )
        ready_event = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type == "bid.task.ready.v1",
            )
            .one()
        )
        assert ready_event.payload_json["resource_version"] == run.row_version
        ready_event_id = str(ready_event.event_id)
        assert (
            db.query(BidAuditLog)
            .filter(
                BidAuditLog.action == "plan.commit",
                BidAuditLog.entity_id == plan.id,
            )
            .count()
            == 1
        )
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            replay = consume_run_created_event(db, event_id=created_event_id)
        assert replay.duplicate is True
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            projected = project_outbox_event_to_public(db, event_id=ready_event_id)
        assert projected.duplicate is False
    finally:
        db.close()
    progress = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert progress.status_code == 200
    assert progress.json()["data"]["status"] == "queued"
    assert progress.json()["data"]["current_stage"] == "fact_baseline"
    assert progress.json()["data"]["latest_event"]["event_type"] == (
        "run.stage.changed"
    )


def test_phase3b_plan_commit_rolls_back_all_writes_on_audit_failure(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, owner = api_runtime
    created = _create_assessment(client)
    assessment_id = created.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    _attach_phase3_scope(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    _activate_phase3_frozen_versions(session_factory, actor_id=owner.id)
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    started = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={"Idempotency-Key": _key(), "If-Match": current.headers["etag"]},
        json={
            "manifest_id": manifest_id,
            "reason": "manual_restart",
            "note": "Phase 3B rollback boundary",
        },
    )
    assert started.status_code == 202
    run_id = started.json()["data"]["run_id"]

    db = session_factory()
    try:
        created_event_id = str(
            db.query(BidOutboxEvent.event_id)
            .filter(
                BidOutboxEvent.event_type == "bid.run.created.v1",
                BidOutboxEvent.run_id == run_id,
            )
            .scalar()
        )
    finally:
        db.close()

    def _raise_audit_failure(*_args, **_kwargs):
        raise RuntimeError("synthetic-plan-audit-failure")

    monkeypatch.setattr(
        plan_commit_service,
        "append_audit_log",
        _raise_audit_failure,
    )
    db = session_factory()
    try:
        with pytest.raises(RuntimeError, match="synthetic-plan-audit-failure"):
            with db.begin():
                consume_run_created_event(db, event_id=created_event_id)
    finally:
        db.close()

    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.status == "created"
        assert run.current_stage == "planning"
        assert run.row_version == 1
        assert db.query(BidPlanRevision).filter(BidPlanRevision.run_id == run_id).count() == 0
        assert db.query(BidTask).filter(BidTask.run_id == run_id).count() == 0
        assert (
            db.query(BidTaskDependency)
            .filter(BidTaskDependency.run_id == run_id)
            .count()
            == 0
        )
        assert (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.run_id == run_id,
                BidOutboxEvent.event_type.in_(
                    {"bid.plan.committed.v1", "bid.task.ready.v1"}
                ),
            )
            .count()
            == 0
        )
        assert (
            db.query(BidProcessedEvent)
            .filter(
                BidProcessedEvent.consumer_name == PLAN_COMMIT_CONSUMER,
                BidProcessedEvent.event_id == created_event_id,
            )
            .count()
            == 0
        )
    finally:
        db.close()


def test_phase3b_maintenance_scan_recovers_unprocessed_run_created_event(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    created = _create_assessment(client)
    assessment_id = created.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    _attach_phase3_scope(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    _activate_phase3_frozen_versions(session_factory, actor_id=owner.id)
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    started = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={"Idempotency-Key": _key(), "If-Match": current.headers["etag"]},
        json={
            "manifest_id": manifest_id,
            "reason": "manual_restart",
            "note": "Phase 3B maintenance recovery",
        },
    )
    assert started.status_code == 202
    run_id = started.json()["data"]["run_id"]

    first = process_pending_plan_commits(
        session_factory=session_factory,
        limit=20,
    )
    assert first.scanned == 1
    assert first.committed == 1
    assert first.duplicate == 0
    assert first.ignored == 0
    assert first.failed == 0

    replay = process_pending_plan_commits(
        session_factory=session_factory,
        limit=20,
    )
    assert replay.scanned == 0
    assert replay.committed == 0
    assert replay.failed == 0

    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assert run.status == "queued"
        assert (
            db.query(BidPlanRevision)
            .filter(
                BidPlanRevision.run_id == run_id,
                BidPlanRevision.status == "committed",
            )
            .count()
            == 1
        )
        assert (
            db.query(BidProcessedEvent)
            .filter(BidProcessedEvent.consumer_name == PLAN_COMMIT_CONSUMER)
            .count()
            == 1
        )
    finally:
        db.close()


def _attach_phase3_scope(
    session_factory,
    *,
    assessment_id: str,
    manifest_id: str,
    actor_id: int,
) -> dict[str, str]:
    marker = uuid.uuid4().hex
    scope_id = str(uuid.uuid4())
    lot_id = f"lot-{marker}"
    snapshot = {
        "schema_version": "bid-assessment-lot-scope-v1",
        "assessment_id": assessment_id,
        "manifest_id": manifest_id,
        "lot_id": lot_id,
        "lot_name": "Phase 3A 测试标段",
        "operation_id": f"op-{marker}",
    }
    db = session_factory()
    try:
        with db.begin():
            assessment = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == assessment_id)
                .one()
            )
            db.add(
                BidAssessmentScope(
                    id=scope_id,
                    assessment_id=assessment_id,
                    version=1,
                    scope_type="lot",
                    source_lot_candidate_id=None,
                    selected_lot_snapshot_json=snapshot,
                    scope_hash=canonical_hash(snapshot),
                    created_by=actor_id,
                )
            )
            assessment.business_status = "preliminary_ready"
            assessment.row_version = int(assessment.row_version) + 1
        return {"scope_id": scope_id, "lot_id": lot_id}
    finally:
        db.close()


def _activate_phase3_frozen_versions(
    session_factory,
    *,
    actor_id: int,
    phase4_model_gateway: bool = False,
) -> dict[str, str]:
    marker = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    governed_at = now - timedelta(minutes=5)
    versions = {
        "enterprise_snapshot_version": f"enterprise-{marker}",
        "rule_set_version": f"rules-{marker}",
        "fact_catalog_version": f"facts-{marker}",
        "prompt_bundle_version": f"prompts-{marker}",
        "tool_registry_version": f"tools-{marker}",
        "model_profile_version": f"models-{marker}",
        "formula_catalog_version": f"formulas-{marker}",
    }
    db = session_factory()
    try:
        with db.begin():
            enterprise = BidEnterpriseSnapshot(
                id=str(uuid.uuid4()),
                version=versions["enterprise_snapshot_version"],
                as_of=governed_at,
                snapshot_hash=canonical_hash(
                    {"marker": marker, "type": "enterprise"}
                ),
                source_catalog_version="test-catalog-v1",
                status="frozen",
                error_code=None,
                created_by=actor_id,
                frozen_by=actor_id,
                frozen_at=governed_at,
                row_version=1,
            )
            common = {
                "status": "active",
                "active_slot_key": "active",
                "authored_by": actor_id,
                "reviewed_by": actor_id,
                "reviewed_at": governed_at,
                "activated_at": governed_at,
                "row_version": 1,
            }
            rule_set = BidRuleSet(
                id=str(uuid.uuid4()),
                version=versions["rule_set_version"],
                artifact_ref=f"memory://rules/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "rules"}),
                effective_from=governed_at,
                effective_to=None,
                test_cases_ref=f"memory://rules/{marker}/tests",
                **common,
            )
            fact_catalog = BidFactCatalogVersion(
                id=str(uuid.uuid4()),
                version=versions["fact_catalog_version"],
                artifact_ref=f"memory://facts/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "facts"}),
                schema_version="v1",
                **common,
            )
            prompt_bundle = BidPromptBundle(
                id=str(uuid.uuid4()),
                version=versions["prompt_bundle_version"],
                artifact_ref=f"memory://prompts/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "prompts"}),
                bundle_schema_version="v1",
                **common,
            )
            tool_registry = BidToolRegistryVersion(
                id=str(uuid.uuid4()),
                version=versions["tool_registry_version"],
                artifact_ref=f"memory://tools/{marker}",
                artifact_hash=canonical_hash({"marker": marker, "type": "tools"}),
                registry_schema_version="v1",
                **common,
            )
            provider_identifiers = (
                {
                    "local-test-provider": {
                        "adapter_kind": "injected_test_provider",
                        "endpoint_class": "local_only",
                    }
                }
                if phase4_model_gateway
                else {}
            )
            model_identifiers = (
                {
                    "local-action-model": {
                        "provider_ref": "local-test-provider",
                        "capability": "closed_task_action",
                    }
                }
                if phase4_model_gateway
                else {}
            )
            role_routing = (
                {
                    role: {
                        "provider_ref": "local-test-provider",
                        "model_ref": "local-action-model",
                        "prompt_role": f"{role}.task-action.v1",
                        "action_schema": "bid.task.action.v1",
                        "replay_policy": "safe_idempotent",
                        "max_attempts": 2,
                        "timeout_seconds": 120,
                        "reserved_cost_microunits": 100000,
                    }
                    for role in (
                        "local_research",
                        "synthesizer",
                        "evidence_validator",
                        "report_writer",
                    )
                }
                if phase4_model_gateway
                else {}
            )
            model_profile = BidModelProfileVersion(
                id=str(uuid.uuid4()),
                version=versions["model_profile_version"],
                artifact_ref=f"memory://models/{marker}",
                artifact_hash=canonical_hash(
                    {
                        "role_routing": role_routing,
                        "provider_identifiers": provider_identifiers,
                        "model_identifiers": model_identifiers,
                    }
                ),
                role_routing_json=role_routing,
                provider_identifiers_json=provider_identifiers,
                model_identifiers_json=model_identifiers,
                **common,
            )
            formula_catalog = BidFormulaCatalogVersion(
                id=str(uuid.uuid4()),
                version=versions["formula_catalog_version"],
                artifact_ref=f"memory://formulas/{marker}",
                artifact_hash=canonical_hash(
                    {"marker": marker, "type": "formulas"}
                ),
                rounding_policy_json={},
                **common,
            )
            db.add_all(
                [
                    enterprise,
                    rule_set,
                    fact_catalog,
                    prompt_bundle,
                    tool_registry,
                    model_profile,
                    formula_catalog,
                ]
            )
        return versions
    finally:
        db.close()


def _phase2_lot_selection_fixture(
    client: TestClient,
    session_factory,
    *,
    owner: User,
) -> dict[str, str]:
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    lot_id, detection_run_id, evidence_id = _attach_phase2_lot_candidate(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        document_id=document_id,
    )
    snapshot = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert snapshot.status_code == 200
    candidates = client.get(f"/api/v1/bid-assessments/{assessment_id}/lots")
    assert candidates.status_code == 200
    assert candidates.json()["data"]["selection_required"] is True
    return {
        "assessment_id": assessment_id,
        "manifest_id": manifest_id,
        "lot_id": lot_id,
        "detection_run_id": detection_run_id,
        "evidence_id": evidence_id,
        "etag": snapshot.headers["etag"],
    }


def _attach_additional_phase2_lot_candidate(
    session_factory,
    *,
    manifest_id: str,
    detection_run_id: str,
    evidence_id: str,
) -> str:
    lot_id = f"lot_{uuid.uuid4().hex}"
    db = session_factory()
    try:
        with db.begin():
            evidence = (
                db.query(BidEvidenceFragment)
                .filter(BidEvidenceFragment.id == evidence_id)
                .one()
            )
            detection_run = (
                db.query(BidLotDetectionRun)
                .filter(BidLotDetectionRun.id == detection_run_id)
                .one()
            )
            candidate_payload = {
                "lot_code": "2",
                "lot_name": "机电安装工程",
                "normalized_lot_key": "标段:2",
            }
            db.add(
                BidLotCandidate(
                    id=lot_id,
                    manifest_id=manifest_id,
                    detection_run_id=detection_run_id,
                    lot_code="2",
                    lot_name="机电安装工程",
                    scope_summary="正文明确列示第二标段",
                    normalized_lot_key="标段:2",
                    source_status="detected",
                    confidence=Decimal("0.880000"),
                    confidence_level="high",
                    candidate_hash=canonical_hash(candidate_payload),
                    warnings_json=[],
                )
            )
            db.flush()
            db.add(
                BidLotCandidateEvidence(
                    lot_candidate_id=lot_id,
                    evidence_id=evidence_id,
                    manifest_id=manifest_id,
                    document_version_id=str(evidence.document_version_id),
                    support_role="identity",
                    display_order=0,
                    display_label="第1页—第二标段",
                )
            )
            detection_run.candidate_count = int(detection_run.candidate_count) + 1
            detection_run.result_hash = canonical_hash(
                {
                    "previous_result_hash": detection_run.result_hash,
                    "additional_lot_id": lot_id,
                }
            )
        return lot_id
    finally:
        db.close()


def test_api30_not_started_is_read_only_private_and_conditionally_cached(
    api_runtime,
) -> None:
    client, session_factory, _owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    before = _counts(session_factory)

    response = client.get(f"/api/v1/bid-assessments/{assessment_id}/lots")

    assert response.status_code == 200
    _validate_contract("LotCandidatePageResponse", response.json())
    assert response.json()["data"]["manifest"] is None
    assert response.json()["data"]["generation"]["status"] == "not_started"
    assert response.json()["data"]["candidates"] == []
    assert response.json()["data"]["blocking_reason"]["code"] == (
        "manifest_not_available"
    )
    assert response.headers["cache-control"] == (
        "private, no-cache, max-age=0, must-revalidate"
    )
    assert response.headers["vary"] == "Authorization"
    unchanged = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/lots",
        headers={"If-None-Match": f'W/{response.headers["etag"]}'},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert _counts(session_factory) == before


def test_api30_projects_evidence_selection_and_scope_without_storage_access(
    api_runtime,
    monkeypatch,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    document_id = _attach_manifest_document(
        session_factory,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    lot_id, detection_run_id, evidence_id = _attach_phase2_lot_candidate(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        document_id=document_id,
    )
    monkeypatch.setattr(
        assessments_api,
        "get_bid_upload_object_storage",
        lambda: (_ for _ in ()).throw(AssertionError("API-30 touched storage")),
    )

    response = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/lots",
        params={"manifest_id": manifest_id},
    )

    assert response.status_code == 200
    _validate_contract("LotCandidatePageResponse", response.json())
    page = response.json()["data"]
    assert page["generation"]["status"] == "succeeded"
    assert page["generation"]["detection_run_id"] == detection_run_id
    assert page["generation"]["candidate_count"] == 1
    assert page["selection_required"] is True
    assert page["blocking_reason"] is None
    assert [action["code"] for action in page["allowed_actions"]] == ["lot.select"]
    assert page["candidates"] == [
        {
            "lot_id": lot_id,
            "detection_run_id": detection_run_id,
            "lot_code": "1",
            "lot_name": "室内装饰工程",
            "scope_summary": "正文明确列示第一标段",
            "status": "candidate",
            "confidence": "high",
            "confidence_score": "0.9",
            "evidence_refs": [
                {
                    "evidence_id": evidence_id,
                    "display_label": "第1页",
                    "detail_url": f"/api/v1/bid-evidence/{evidence_id}",
                }
            ],
            "warnings": [],
        }
    ]

    db = session_factory()
    try:
        with db.begin():
            current = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == assessment_id)
                .one()
            )
            db.add(
                BidAssessmentScope(
                    id=f"scope_{uuid.uuid4().hex[:30]}",
                    assessment_id=assessment_id,
                    version=1,
                    scope_type="lot",
                    source_lot_candidate_id=lot_id,
                    selected_lot_snapshot_json={
                        "lot_id": lot_id,
                        "lot_code": "1",
                        "lot_name": "室内装饰工程",
                    },
                    scope_hash="9" * 64,
                    created_by=owner.id,
                )
            )
            current.business_status = "preliminary_analyzing"
            current.row_version = int(current.row_version) + 1
    finally:
        db.close()

    selected = client.get(f"/api/v1/bid-assessments/{assessment_id}/lots")
    assert selected.status_code == 200
    _validate_contract("LotCandidatePageResponse", selected.json())
    selected_page = selected.json()["data"]
    assert selected_page["selection_required"] is False
    assert selected_page["selected_lot_id"] == lot_id
    assert selected_page["candidates"][0]["status"] == "selected"
    assert [action["code"] for action in selected_page["allowed_actions"]] == [
        "assessment.create_for_other_lot"
    ]
    assert selected.headers["etag"] != response.headers["etag"]


def test_api31_atomically_binds_scope_projects_reads_events_and_domain_retries(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    fixture = _phase2_lot_selection_fixture(
        client,
        session_factory,
        owner=owner,
    )
    key = _key()
    body = {
        "manifest_id": fixture["manifest_id"],
        "lot_id": fixture["lot_id"],
        "selection_note": "  首次选择，以正文标段证据为准  ",
    }
    before = _counts(session_factory)

    selected = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/lot-selection",
        headers={"Idempotency-Key": key, "If-Match": fixture["etag"]},
        json=body,
    )

    assert selected.status_code == 202
    _validate_contract("LotSelectionResponse", selected.json())
    result = selected.json()["data"]
    assert result["scope"]["lot_id"] == fixture["lot_id"]
    assert result["scope"]["scope_version"] == 1
    assert result["accepted_operation"]["status"] == "accepted"
    assert result["run"] is None
    assert result["assessment"]["business_status"] == "preliminary_analyzing"
    assert result["assessment"]["active_run"] is None
    assert result["assessment"]["scope"] == result["scope"]
    assert selected.headers["location"] == (
        f"/api/v1/bid-assessments/{fixture['assessment_id']}"
    )
    assert selected.headers["etag"] != fixture["etag"]
    assert selected.headers["x-resource-version"] == str(
        result["assessment"]["row_version"]
    )
    assert selected.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in selected.headers

    after = _counts(session_factory)
    assert after == {
        **before,
        "run": before["run"],
        "outbox": before["outbox"] + 2,
        "audit": before["audit"] + 1,
        "idempotency": before["idempotency"] + 1,
    }
    db = session_factory()
    try:
        scope = db.query(BidAssessmentScope).one()
        assessment_row = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == fixture["assessment_id"])
            .one()
        )
        events = {
            row.event_type: row
            for row in (
                db.query(BidOutboxEvent)
                .filter(
                    BidOutboxEvent.event_type.in_(
                        ["bid.lot.selected.v1", "bid.plan.requested.v1"]
                    )
                )
                .all()
            )
        }
        audit = (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "lot.select")
            .one()
        )
        idempotency = (
            db.query(BidIdempotencyRecord)
            .filter(BidIdempotencyRecord.idempotency_key == key)
            .one()
        )
        snapshot = dict(scope.selected_lot_snapshot_json)
        assert scope.id == result["scope"]["scope_id"]
        assert scope.source_lot_candidate_id == fixture["lot_id"]
        assert scope.scope_hash == canonical_hash(snapshot)
        assert snapshot["manifest_id"] == fixture["manifest_id"]
        assert snapshot["detection_run_id"] == fixture["detection_run_id"]
        assert snapshot["evidence_ids"] == [fixture["evidence_id"]]
        assert snapshot["selection_note"] == "首次选择，以正文标段证据为准"
        assert snapshot["operation_id"] == result["accepted_operation"]["operation_id"]
        assert assessment_row.active_run_id is None
        assert db.query(BidAnalysisRun).count() == 0
        lot_event = events["bid.lot.selected.v1"]
        plan_event = events["bid.plan.requested.v1"]
        assert lot_event.aggregate_type == plan_event.aggregate_type == "scope"
        assert lot_event.aggregate_id == plan_event.aggregate_id == scope.id
        assert lot_event.payload_json["scope_id"] == scope.id
        assert lot_event.payload_json["lot_id"] == fixture["lot_id"]
        assert lot_event.payload_json["from"] == "awaiting_lot_selection"
        assert lot_event.payload_json["to"] == "preliminary_analyzing"
        assert plan_event.causation_event_id == lot_event.event_id
        assert plan_event.payload_json["requested_run_kind"] == "preliminary"
        assert plan_event.payload_json["operation_id"] == snapshot["operation_id"]
        assert audit.entity_id == scope.id
        assert audit.correlation_id == plan_event.event_id
        assert idempotency.status == "completed"
        assert idempotency.resource_type == "scope"
        assert idempotency.resource_id == scope.id
        lot_event_id = lot_event.event_id
    finally:
        db.close()

    api03 = client.get(f"/api/v1/bid-assessments/{fixture['assessment_id']}")
    assert api03.status_code == 200
    _validate_contract("AssessmentResponse", api03.json())
    assert api03.json()["data"]["scope"] == result["scope"]
    assert api03.json()["data"]["business_status"] == "preliminary_analyzing"
    api30 = client.get(f"/api/v1/bid-assessments/{fixture['assessment_id']}/lots")
    assert api30.status_code == 200
    _validate_contract("LotCandidatePageResponse", api30.json())
    assert api30.json()["data"]["selection_required"] is False
    assert api30.json()["data"]["selected_lot_id"] == fixture["lot_id"]
    assert api30.json()["data"]["candidates"][0]["status"] == "selected"
    assert [
        action["code"] for action in api30.json()["data"]["allowed_actions"]
    ] == ["assessment.create_for_other_lot"]

    db = session_factory()
    try:
        with db.begin():
            projection = project_outbox_event_to_public(db, event_id=lot_event_id)
        assert projection.duplicate is False
    finally:
        db.close()
    db = session_factory()
    try:
        public = (
            db.query(BidPublicEvent)
            .filter(BidPublicEvent.source_event_id == lot_event_id)
            .one()
        )
        assert public.event_type == "lot.selected"
        assert public.resource_type == "assessment"
        assert public.resource_id == fixture["assessment_id"]
        assert public.resource_version == result["assessment"]["row_version"]
        assert public.payload_json == {
            "scope_id": result["scope"]["scope_id"],
            "lot_id": fixture["lot_id"],
        }
    finally:
        db.close()

    replay = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/lot-selection",
        headers={"Idempotency-Key": key, "If-Match": fixture["etag"]},
        json=body,
    )
    assert replay.status_code == 202
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == selected.json()
    after_replay = _counts(session_factory)
    assert after_replay == after

    domain_retry = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/lot-selection",
        headers={"Idempotency-Key": _key(), "If-Match": selected.headers["etag"]},
        json={**body, "selection_note": "不会改写首次快照"},
    )
    assert domain_retry.status_code == 202
    _validate_contract("LotSelectionResponse", domain_retry.json())
    assert domain_retry.json()["data"]["scope"] == result["scope"]
    assert domain_retry.json()["data"]["accepted_operation"] == result[
        "accepted_operation"
    ]
    final_counts = _counts(session_factory)
    assert final_counts["outbox"] == after["outbox"]
    assert final_counts["audit"] == after["audit"]
    assert final_counts["run"] == 0
    assert final_counts["idempotency"] == after["idempotency"] + 1


def test_api31_rejects_stale_version_unready_candidates_and_other_scope(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    assessment = _create_assessment(client)
    assessment_id = assessment.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    db = session_factory()
    try:
        with db.begin():
            current = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == assessment_id)
                .one()
            )
            current.business_status = "awaiting_lot_selection"
            current.row_version = int(current.row_version) + 1
    finally:
        db.close()
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert current.status_code == 200
    request_body = {
        "manifest_id": manifest_id,
        "lot_id": "lot_not_ready",
        "selection_note": None,
    }

    stale = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/lot-selection",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": f'"bid-assessment:{assessment_id}:1"',
        },
        json=request_body,
    )
    assert stale.status_code == 412
    _validate_contract("ErrorEnvelope", stale.json())
    assert stale.json()["error"]["error_code"] == "BID_RESOURCE_VERSION_MISMATCH"
    assert stale.headers["etag"] == current.headers["etag"]

    not_ready = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/lot-selection",
        headers={"Idempotency-Key": _key(), "If-Match": current.headers["etag"]},
        json=request_body,
    )
    assert not_ready.status_code == 409
    _validate_contract("ErrorEnvelope", not_ready.json())
    assert not_ready.json()["error"]["error_code"] == (
        "BID_LOT_CANDIDATES_NOT_READY"
    )
    assert not_ready.json()["error"]["retryable"] is True

    fixture = _phase2_lot_selection_fixture(
        client,
        session_factory,
        owner=owner,
    )
    selected = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/lot-selection",
        headers={"Idempotency-Key": _key(), "If-Match": fixture["etag"]},
        json={
            "manifest_id": fixture["manifest_id"],
            "lot_id": fixture["lot_id"],
            "selection_note": None,
        },
    )
    assert selected.status_code == 202
    other_scope = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/lot-selection",
        headers={"Idempotency-Key": _key(), "If-Match": selected.headers["etag"]},
        json={
            "manifest_id": fixture["manifest_id"],
            "lot_id": "lot_other_candidate",
            "selection_note": None,
        },
    )
    assert other_scope.status_code == 409
    _validate_contract("ErrorEnvelope", other_scope.json())
    assert other_scope.json()["error"]["error_code"] == (
        "BID_LOT_SCOPE_ALREADY_BOUND"
    )
    assert other_scope.json()["error"]["recovery"]["action"] == (
        "assessment.create_for_other_lot"
    )


def test_api32_creates_independent_manifest_scope_acl_and_replays(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    fixture = _phase2_lot_selection_fixture(
        client,
        session_factory,
        owner=owner,
    )
    other_lot_id = _attach_additional_phase2_lot_candidate(
        session_factory,
        manifest_id=fixture["manifest_id"],
        detection_run_id=fixture["detection_run_id"],
        evidence_id=fixture["evidence_id"],
    )
    selected = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/lot-selection",
        headers={"Idempotency-Key": _key(), "If-Match": fixture["etag"]},
        json={
            "manifest_id": fixture["manifest_id"],
            "lot_id": fixture["lot_id"],
            "selection_note": None,
        },
    )
    assert selected.status_code == 202
    source_etag = selected.headers["etag"]
    body = {
        "source_manifest_id": fixture["manifest_id"],
        "lot_id": other_lot_id,
        "title": "  某办公楼—机电标段投标研判  ",
    }
    key = _key()
    db = session_factory()
    try:
        source_version_ids = {
            str(row[0])
            for row in (
                db.query(BidManifestDocument.document_version_id)
                .filter(BidManifestDocument.manifest_id == fixture["manifest_id"])
                .all()
            )
        }
        file_object_count = db.query(BidFileObject).count()
        document_version_count = db.query(BidDocumentVersion).count()
    finally:
        db.close()
    before = _counts(session_factory)

    cloned = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/clone-for-lot",
        headers={"Idempotency-Key": key, "If-Match": source_etag},
        json=body,
    )

    assert cloned.status_code == 201
    _validate_contract("AssessmentResponse", cloned.json())
    snapshot = cloned.json()["data"]
    cloned_assessment_id = snapshot["assessment_id"]
    cloned_manifest_id = snapshot["current_manifest"]["manifest_id"]
    assert cloned_assessment_id != fixture["assessment_id"]
    assert snapshot["title"] == "某办公楼—机电标段投标研判"
    assert snapshot["client_name"] == "某甲方"
    assert snapshot["internal_note"] == "内部跟进"
    assert snapshot["business_status"] == "preliminary_analyzing"
    assert snapshot["row_version"] == 1
    assert snapshot["active_run"] is None
    assert snapshot["scope"]["lot_id"] == other_lot_id
    assert snapshot["current_manifest"]["version"] == 1
    assert cloned.headers["location"] == (
        f"/api/v1/bid-assessments/{cloned_assessment_id}"
    )
    assert cloned.headers["etag"] == (
        f'"bid-assessment:{cloned_assessment_id}:1"'
    )
    assert cloned.headers["x-resource-version"] == "1"
    assert cloned.headers["cache-control"] == "private, no-store"
    assert "idempotent-replay" not in cloned.headers

    after = _counts(session_factory)
    assert after == {
        **before,
        "assessment": before["assessment"] + 1,
        "manifest": before["manifest"] + 1,
        "run": before["run"],
        "outbox": before["outbox"] + 2,
        "audit": before["audit"] + 1,
        "idempotency": before["idempotency"] + 1,
    }
    db = session_factory()
    try:
        source = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == fixture["assessment_id"])
            .one()
        )
        cloned_row = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == cloned_assessment_id)
            .one()
        )
        manifest = (
            db.query(BidDocumentManifest)
            .filter(BidDocumentManifest.id == cloned_manifest_id)
            .one()
        )
        cloned_version_ids = {
            str(row[0])
            for row in (
                db.query(BidManifestDocument.document_version_id)
                .filter(BidManifestDocument.manifest_id == cloned_manifest_id)
                .all()
            )
        }
        scope = (
            db.query(BidAssessmentScope)
            .filter(BidAssessmentScope.assessment_id == cloned_assessment_id)
            .one()
        )
        events = {
            event.event_type: event
            for event in (
                db.query(BidOutboxEvent)
                .filter(BidOutboxEvent.assessment_id == cloned_assessment_id)
                .all()
            )
        }
        audit = (
            db.query(BidAuditLog)
            .filter(BidAuditLog.action == "assessment.clone_for_lot")
            .one()
        )
        assert source.row_version == int(selected.json()["data"]["assessment"]["row_version"])
        assert source.current_manifest_id == fixture["manifest_id"]
        assert cloned_row.created_by == owner.id
        assert cloned_row.external_ref is None
        assert manifest.assessment_id == cloned_assessment_id
        assert manifest.manifest_hash != (
            db.query(BidDocumentManifest.manifest_hash)
            .filter(BidDocumentManifest.id == fixture["manifest_id"])
            .scalar()
        )
        assert cloned_version_ids == source_version_ids
        assert db.query(BidFileObject).count() == file_object_count
        assert db.query(BidDocumentVersion).count() == document_version_count
        assert scope.source_lot_candidate_id is None
        scope_snapshot = dict(scope.selected_lot_snapshot_json)
        assert scope.scope_hash == canonical_hash(scope_snapshot)
        assert scope_snapshot["source_assessment_id"] == fixture["assessment_id"]
        assert scope_snapshot["source_manifest_id"] == fixture["manifest_id"]
        assert scope_snapshot["source_detection_run_id"] == fixture["detection_run_id"]
        assert scope_snapshot["lot_id"] == other_lot_id
        assert scope_snapshot["evidence_ids"] == [fixture["evidence_id"]]
        assert set(events) == {
            "bid.assessment.created.v1",
            "bid.plan.requested.v1",
        }
        created_event = events["bid.assessment.created.v1"]
        plan_event = events["bid.plan.requested.v1"]
        assert plan_event.causation_event_id == created_event.event_id
        assert plan_event.payload_json["source_assessment_id"] == (
            fixture["assessment_id"]
        )
        assert audit.assessment_id == cloned_assessment_id
        assert audit.correlation_id == plan_event.event_id
        assert db.query(BidAnalysisRun).count() == 0
        document_version_id = next(iter(cloned_version_ids))
        created_event_id = created_event.event_id
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            projection = project_outbox_event_to_public(
                db,
                event_id=created_event_id,
            )
        assert projection.duplicate is False
    finally:
        db.close()
    db = session_factory()
    try:
        public = (
            db.query(BidPublicEvent)
            .filter(BidPublicEvent.source_event_id == created_event_id)
            .one()
        )
        assert public.assessment_id == cloned_assessment_id
        assert public.resource_id == cloned_assessment_id
        assert public.event_type == "assessment.snapshot"
        assert public.payload_json["snapshot"]["assessment_id"] == (
            cloned_assessment_id
        )
        assert public.payload_json["snapshot"]["scope"]["lot_id"] == (
            other_lot_id
        )
    finally:
        db.close()

    projected = client.get(
        f"/api/v1/bid-assessments/{cloned_assessment_id}/lots"
    )
    assert projected.status_code == 200
    _validate_contract("LotCandidatePageResponse", projected.json())
    page = projected.json()["data"]
    assert page["generation"]["status"] == "not_started"
    assert page["candidates"] == []
    assert page["selection_required"] is False
    assert page["selected_lot_id"] == other_lot_id
    assert page["blocking_reason"] is None
    assert page["allowed_actions"] == []

    replay = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/clone-for-lot",
        headers={"Idempotency-Key": key, "If-Match": source_etag},
        json=body,
    )
    assert replay.status_code == 201
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == cloned.json()
    assert _counts(session_factory) == after

    db = session_factory()
    try:
        with db.begin():
            source = (
                db.query(BidAssessment)
                .filter(BidAssessment.id == fixture["assessment_id"])
                .one()
            )
            source.lifecycle_status = "archived"
            source.archived_at = datetime.now(timezone.utc)
            source.row_version = int(source.row_version) + 1
    finally:
        db.close()
    assert client.get(
        f"/api/v1/bid-assessments/{cloned_assessment_id}"
    ).status_code == 200
    assert client.get(
        f"/api/v1/bid-document-versions/{document_version_id}"
    ).status_code == 200

    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = client.get(f"/api/v1/bid-assessments/{cloned_assessment_id}")
    hidden_document = client.get(
        f"/api/v1/bid-document-versions/{document_version_id}"
    )
    assert hidden.status_code == hidden_document.status_code == 404
    assert hidden.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"
    assert hidden_document.json()["error"]["error_code"] == (
        "BID_RESOURCE_NOT_FOUND"
    )


def test_api32_rejects_same_lot_missing_scope_and_stale_source_version(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    fixture = _phase2_lot_selection_fixture(
        client,
        session_factory,
        owner=owner,
    )
    body = {
        "source_manifest_id": fixture["manifest_id"],
        "lot_id": fixture["lot_id"],
        "title": "同标段研判",
    }
    no_scope = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/clone-for-lot",
        headers={"Idempotency-Key": _key(), "If-Match": fixture["etag"]},
        json=body,
    )
    assert no_scope.status_code == 409
    _validate_contract("ErrorEnvelope", no_scope.json())
    assert no_scope.json()["error"]["error_code"] == (
        "BID_ASSESSMENT_STATE_CONFLICT"
    )
    assert no_scope.json()["error"]["details"]["reason"] == (
        "source_scope_required"
    )

    selected = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/lot-selection",
        headers={"Idempotency-Key": _key(), "If-Match": fixture["etag"]},
        json={
            "manifest_id": fixture["manifest_id"],
            "lot_id": fixture["lot_id"],
            "selection_note": None,
        },
    )
    assert selected.status_code == 202
    stale = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/clone-for-lot",
        headers={"Idempotency-Key": _key(), "If-Match": fixture["etag"]},
        json=body,
    )
    assert stale.status_code == 412
    _validate_contract("ErrorEnvelope", stale.json())
    assert stale.json()["error"]["error_code"] == (
        "BID_RESOURCE_VERSION_MISMATCH"
    )
    same_lot = client.post(
        f"/api/v1/bid-assessments/{fixture['assessment_id']}/clone-for-lot",
        headers={"Idempotency-Key": _key(), "If-Match": selected.headers["etag"]},
        json=body,
    )
    assert same_lot.status_code == 409
    _validate_contract("ErrorEnvelope", same_lot.json())
    assert same_lot.json()["error"]["details"]["reason"] == (
        "same_lot_not_allowed"
    )
    assert _counts(session_factory)["assessment"] == 1


def test_phase2_lot_detector_accepts_only_explicit_content_evidence() -> None:
    unrelated = detect_lot_candidates(
        (
            LotDetectionEvidenceInput(
                evidence_id="bef_unrelated",
                document_version_id="version_unrelated",
                role="tender_document",
                text="本项目包含装饰工程、机电工程及配套服务。",
                locator={"page_no": 1},
            ),
        )
    )
    explicit = detect_lot_candidates(
        (
            LotDetectionEvidenceInput(
                evidence_id="bef_explicit",
                document_version_id="version_explicit",
                role="tender_document",
                text="第一标段：室内装饰工程",
                locator={"page_no": 2},
            ),
        )
    )

    assert unrelated == ()
    assert len(explicit) == 1
    assert explicit[0].lot_code == "一"
    assert explicit[0].lot_name == "室内装饰工程"
    assert [row.evidence_id for row in explicit[0].evidence] == ["bef_explicit"]


def test_api40_api41_bootstrap_frozen_run_idempotency_acl_and_etag(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    created = _create_assessment(client)
    assessment_id = created.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    scope = _attach_phase3_scope(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    frozen_versions = _activate_phase3_frozen_versions(
        session_factory,
        actor_id=owner.id,
    )
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    assert current.status_code == 200

    key = _key()
    body = {
        "manifest_id": manifest_id,
        "reason": "rule_reanalysis",
        "note": "使用当前已评审规则重新研判",
    }
    started = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={"Idempotency-Key": key, "If-Match": current.headers["etag"]},
        json=body,
    )
    assert started.status_code == 202
    _validate_contract("RunResponse", started.json())
    snapshot = started.json()["data"]
    run_id = snapshot["run_id"]
    assert started.headers["location"].endswith(f"/runs/{run_id}")
    assert started.headers["cache-control"] == "private, no-store"
    assert snapshot["assessment_id"] == assessment_id
    assert snapshot["status"] == "created"
    assert snapshot["run_kind"] == "reanalysis"
    assert snapshot["current_stage"] == "planning"
    assert snapshot["latest_event"] is None
    assert snapshot["input_versions"]["manifest_id"] == manifest_id
    assert snapshot["input_versions"]["scope_id"] == scope["scope_id"]
    for field, version in frozen_versions.items():
        assert snapshot["input_versions"][field] == version

    replay = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={"Idempotency-Key": key, "If-Match": current.headers["etag"]},
        json=body,
    )
    assert replay.status_code == 202
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == started.json()

    progress = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert progress.status_code == 200
    _validate_contract("RunResponse", progress.json())
    assert progress.headers["etag"] == started.headers["etag"]
    unchanged = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}",
        headers={"If-None-Match": progress.headers["etag"]},
    )
    assert unchanged.status_code == 304
    assert unchanged.headers["etag"] == progress.headers["etag"]

    db = session_factory()
    try:
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
        assessment = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == assessment_id)
            .one()
        )
        created_event = (
            db.query(BidOutboxEvent)
            .filter(
                BidOutboxEvent.event_type == "bid.run.created.v1",
                BidOutboxEvent.run_id == run_id,
            )
            .one()
        )
        assert assessment.active_run_id == run_id
        assert run.scope_id == scope["scope_id"]
        assert created_event.payload_json["input_hash"] == run.input_hash
        assert (
            db.query(BidAuditLog)
            .filter(
                BidAuditLog.action == "run.bootstrap.create",
                BidAuditLog.entity_id == run_id,
            )
            .count()
            == 1
        )
        created_event_id = str(created_event.event_id)
    finally:
        db.close()

    db = session_factory()
    try:
        with db.begin():
            project_outbox_event_to_public(db, event_id=created_event_id)
    finally:
        db.close()
    changed = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}",
        headers={"If-None-Match": progress.headers["etag"]},
    )
    assert changed.status_code == 200
    assert changed.headers["etag"] != progress.headers["etag"]
    assert changed.json()["data"]["latest_event"]["event_type"] == (
        "run.status.changed"
    )

    outsider = _create_user(session_factory)
    client.app.state.active_user["value"] = outsider
    hidden = client.get(
        f"/api/v1/bid-assessments/{assessment_id}/runs/{run_id}"
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["error_code"] == "BID_RESOURCE_NOT_FOUND"

    client.app.state.active_user["value"] = owner
    db = session_factory()
    try:
        with db.begin():
            run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
            run.status = "failed"
            run.retryable = True
            run.row_version = int(run.row_version) + 1
    finally:
        db.close()
    latest_assessment = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    blocked_by_retryable_run = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={
            "Idempotency-Key": _key(),
            "If-Match": latest_assessment.headers["etag"],
        },
        json=body,
    )
    assert blocked_by_retryable_run.status_code == 409
    assert blocked_by_retryable_run.json()["error"]["error_code"] == (
        "BID_ACTIVE_RUN_EXISTS"
    )


def test_api40_input_not_ready_does_not_create_placeholder_run(api_runtime) -> None:
    client, session_factory, owner = api_runtime
    created = _create_assessment(client)
    assessment_id = created.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    _attach_phase3_scope(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    current = client.get(f"/api/v1/bid-assessments/{assessment_id}")
    response = client.post(
        f"/api/v1/bid-assessments/{assessment_id}/runs",
        headers={"Idempotency-Key": _key(), "If-Match": current.headers["etag"]},
        json={
            "manifest_id": manifest_id,
            "reason": "manual_restart",
            "note": None,
        },
    )
    assert response.status_code == 409
    _validate_contract("ErrorEnvelope", response.json())
    assert response.json()["error"]["error_code"] == "BID_RUN_INPUT_NOT_READY"
    assert _counts(session_factory)["run"] == 0


def test_plan_requested_bootstrap_is_exactly_once_and_waits_for_frozen_inputs(
    api_runtime,
) -> None:
    client, session_factory, owner = api_runtime
    created = _create_assessment(client)
    assessment_id = created.json()["data"]["assessment_id"]
    manifest_id = _attach_current_manifest(
        session_factory,
        assessment_id=assessment_id,
        actor_id=owner.id,
    )
    scope = _attach_phase3_scope(
        session_factory,
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        actor_id=owner.id,
    )
    db = session_factory()
    try:
        with db.begin():
            plan_event = append_outbox_event(
                db,
                event_type="bid.plan.requested.v1",
                producer="bid-assessment-api-v1",
                aggregate_type="scope",
                aggregate_id=scope["scope_id"],
                aggregate_version=1,
                assessment_id=assessment_id,
                request_id=f"req-{uuid.uuid4().hex}",
                payload_schema="bid.plan.requested.v1.payload",
                payload={
                    "operation_id": f"op-{uuid.uuid4().hex}",
                    "assessment_id": assessment_id,
                    "scope_id": scope["scope_id"],
                    "manifest_id": manifest_id,
                    "lot_id": scope["lot_id"],
                    "requested_run_kind": "preliminary",
                    "resource_version": 1,
                },
                dedupe_key=f"plan-requested:{scope['scope_id']}",
            )
            event_id = str(plan_event.event_id)
    finally:
        db.close()

    db = session_factory()
    try:
        with pytest.raises(BidRunInputNotReady):
            with db.begin():
                consume_plan_requested_event(db, event_id=event_id)
    finally:
        db.close()
    db = session_factory()
    try:
        assert db.query(BidAnalysisRun).count() == 0
        assert (
            db.query(BidProcessedEvent)
            .filter(BidProcessedEvent.event_id == event_id)
            .count()
            == 0
        )
    finally:
        db.close()

    _activate_phase3_frozen_versions(session_factory, actor_id=owner.id)
    db = session_factory()
    try:
        with db.begin():
            first = consume_plan_requested_event(db, event_id=event_id)
        assert first.duplicate is False
        assert first.value["created"] is True
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            replay = consume_plan_requested_event(db, event_id=event_id)
        assert replay.duplicate is True
    finally:
        db.close()
    db = session_factory()
    try:
        assert db.query(BidAnalysisRun).count() == 1
        assert (
            db.query(BidProcessedEvent)
            .filter(BidProcessedEvent.event_id == event_id)
            .count()
            == 1
        )
    finally:
        db.close()


def test_mvp1_local_upload_storage_is_bounded_and_path_scoped(tmp_path) -> None:
    storage = LocalBidUploadObjectStorage(tmp_path / "objects")
    content = b"local-isolated-bid-document"
    stored = storage.put(
        stream=BytesIO(content),
        object_key="bid-assessment/uploading/v1/batch/file",
        size_bytes=len(content),
        mime_type="text/plain",
    )
    assert stored.size_bytes == len(content)
    assert len(str(stored.storage_etag)) == 64
    with storage.open_read(object_key=stored.object_key) as stream:
        assert stream.read() == content
    assert [row.object_key for row in storage.list_candidates(
        prefix="bid-assessment/uploading/v1",
        limit=10,
    )] == [stored.object_key]
    with pytest.raises(BidUploadStorageError, match="BID_UPLOAD_OBJECT_KEY_INVALID"):
        storage.open_read(object_key="../outside")
    with pytest.raises(BidUploadStorageError, match="BID_UPLOAD_OBJECT_SIZE_MISMATCH"):
        storage.put(
            stream=BytesIO(content + b"-extra"),
            object_key="bid-assessment/uploading/v1/batch/oversized",
            size_bytes=len(content),
            mime_type="text/plain",
        )
    storage.delete(object_key=stored.object_key)
    assert storage.list_candidates(prefix="bid-assessment/uploading/v1", limit=10) == []


def test_mvp1_evidence_mcp_is_manifest_and_current_parse_head_scoped(api_runtime) -> None:
    _client, session_factory, _owner = api_runtime
    now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    session_factory, run_id, version_id = _prepare_phase3f_documents_outline(
        api_runtime,
        now=now,
    )
    db = session_factory()
    try:
        with db.begin():
            run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
            parse_head = (
                db.query(BidDocumentParseHead)
                .filter(BidDocumentParseHead.document_version_id == version_id)
                .one()
            )
            parse_unit = (
                db.query(BidDocumentParseUnit)
                .filter(BidDocumentParseUnit.run_id == parse_head.current_run_id)
                .one()
            )
            evidence_id = str(uuid.uuid4())
            text = "本项目投标截止时间为2026年8月18日09时30分，逾期递交将被拒绝。"
            locator = {"page_no": 1, "section_path": ["投标须知", "重要时间"]}
            db.add(
                BidEvidenceFragment(
                    id=evidence_id,
                    parse_run_id=str(parse_head.current_run_id),
                    document_version_id=version_id,
                    parse_unit_id=str(parse_unit.id),
                    locator_type="page_bbox",
                    locator_json=locator,
                    locator_hash=canonical_hash(locator),
                    normalized_text=text,
                    text_hash=canonical_hash({"text": text}),
                    parent_id=None,
                    ordinal=0,
                    object_ref=None,
                )
            )
            manifest_id = str(run.manifest_id)
            assessment_id = str(run.assessment_id)
    finally:
        db.close()

    db = session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=assessment_id,
                run_id=run_id,
                manifest_id=manifest_id,
            ),
        )
        search = service.search({"query": "投标截止时间", "top_k": 5})
        assert search["status"] == "ok"
        assert search["retrieval_mode"] == "bm25_rrf"
        assert search["hits"][0]["evidence_id"] == evidence_id
        assert search["hits"][0]["context_read"] is False
        read = service.read(
            {"evidence_ids": [evidence_id], "expansion": "neighbors", "radius": 1}
        )
        assert read["status"] == "ok"
        assert read["items"][0]["evidence_id"] == evidence_id
        assert read["items"][0]["context_read"] is True
        assert service.search(
            {
                "query": "投标截止时间",
                "top_k": 5,
                "document_version_ids": [str(uuid.uuid4())],
            }
        )["status"] == "no_result"
        with pytest.raises(BidEvidenceMcpError, match="BID_EVIDENCE_REFERENCE_OUT_OF_SCOPE"):
            service.read({"evidence_ids": [str(uuid.uuid4())]})
        with pytest.raises(BidEvidenceMcpError, match="BID_EVIDENCE_SCOPE_INVALID"):
            BidEvidenceMcpService(
                db,
                scope=BidEvidenceMcpScope(
                    assessment_id=assessment_id,
                    run_id=run_id,
                    manifest_id=str(uuid.uuid4()),
                ),
            )
    finally:
        db.close()


def test_mvp1_deterministic_local_profile_converges_full_p0_p4_run(
    api_runtime, request
) -> None:
    _client, session_factory, _owner = api_runtime
    old_evidence_mcp_flag = settings.feature_bid_assessment_phase4_evidence_mcp
    object.__setattr__(settings, "feature_bid_assessment_phase4_evidence_mcp", True)
    request.addfinalizer(
        lambda: object.__setattr__(
            settings,
            "feature_bid_assessment_phase4_evidence_mcp",
            old_evidence_mcp_flag,
        )
    )
    assessment_id, run_id = _create_phase3c_committed_run(
        api_runtime,
        attach_document=True,
        phase4_plan_continuation=True,
        phase4_model_gateway=True,
    )
    now = datetime(2026, 8, 13, 11, 0, tzinfo=timezone.utc)
    document_text = (
        "项目概况与招标范围：办公楼装饰工程。投标截止时间为2026年8月18日09时30分，"
        "开标时间同日。资格要求包含建筑装修装饰资质。否决投标条款要求文件完整。"
        "投标保证金人民币壹万元。评标办法采用综合评分法。工程量以清单为准。"
        "中标后提交材料样品和竣工成果。合同付款按节点执行。工期60日，现场封闭施工。"
    )
    db = session_factory()
    try:
        with db.begin():
            run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
            version_id = str(
                db.query(BidManifestDocument.document_version_id)
                .filter(BidManifestDocument.manifest_id == run.manifest_id)
                .scalar()
            )
            parse_run_id = str(uuid.uuid4())
            parse_unit_id = str(uuid.uuid4())
            db.add(
                BidDocumentParseRun(
                    id=parse_run_id,
                    document_version_id=version_id,
                    parser_profile_version="mvp1-local-deterministic-v1",
                    input_hash=canonical_hash({"version_id": version_id, "text": document_text}),
                    status="succeeded",
                    retryable=False,
                    requested_at=now - timedelta(seconds=2),
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    result_ref="local://mvp1/deterministic",
                    result_hash=canonical_hash({"text": document_text}),
                    quality_grade="high",
                    quality_score=100,
                    page_count=1,
                    sheet_count=0,
                    ocr_status="not_applicable",
                    warning_count=0,
                    warnings_json=[],
                    row_version=1,
                )
            )
            db.flush()
            db.add(
                BidDocumentParseHead(
                    document_version_id=version_id,
                    current_run_id=parse_run_id,
                    row_version=1,
                )
            )
            db.add(
                BidDocumentParseUnit(
                    id=parse_unit_id,
                    run_id=parse_run_id,
                    unit_type="page",
                    unit_key="page:1",
                    ordinal=0,
                    page_no=1,
                    section_path_json=["本地隔离样例"],
                    content_source="native",
                    status="succeeded",
                    text_hash=canonical_hash({"text": document_text}),
                    text_length=len(document_text),
                    ocr_status="not_applicable",
                )
            )
            db.flush()
            locator = {"page_no": 1, "section_path": ["本地隔离样例"]}
            db.add(
                BidEvidenceFragment(
                    id=str(uuid.uuid4()),
                    parse_run_id=parse_run_id,
                    document_version_id=version_id,
                    parse_unit_id=parse_unit_id,
                    locator_type="page_bbox",
                    locator_json=locator,
                    locator_hash=canonical_hash(locator),
                    normalized_text=document_text,
                    text_hash=canonical_hash({"normalized_text": document_text}),
                    parent_id=None,
                    ordinal=0,
                    object_ref=None,
                )
            )
    finally:
        db.close()

    provider = DeterministicMvp1LocalProvider(session_factory=session_factory)
    totals = {"task_failed": 0, "model_failed": 0, "tool_failed": 0, "validation_errors": 0}
    task_error_codes: list[str] = []
    terminal_status = None
    for cycle in range(160):
        task_batch = process_mvp1_task_queue(
            session_factory=session_factory,
            worker_id=f"mvp1-local-task-{cycle}",
            tool_scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
            limit=100,
        )
        model_batch = process_mvp1_model_queue(
            session_factory=session_factory,
            worker_id=f"mvp1-local-model-{cycle}",
            provider=provider,
            limit=50,
        )
        tool_batch = process_tool_dispatch_queue(
            session_factory=session_factory,
            worker_id=f"mvp1-local-tool-{cycle}",
            scope_signing_key=PHASE3E_SCOPE_SIGNING_KEY,
            limit=100,
        )
        process_pending_plan_continuations(session_factory=session_factory, limit=20)
        maintain_run_validations(session_factory=session_factory, limit=20)
        validation_batch = process_run_validation_queue(
            session_factory=session_factory,
            worker_id=f"mvp1-local-validation-{cycle}",
            limit=20,
        )
        totals["task_failed"] += int(task_batch.failed)
        task_error_codes.extend(task_batch.error_codes)
        totals["model_failed"] += int(model_batch.failed)
        totals["tool_failed"] += int(tool_batch.failed)
        totals["validation_errors"] += int(validation_batch.errors)
        db = session_factory()
        try:
            terminal_status = str(
                db.query(BidAnalysisRun.status).filter(BidAnalysisRun.id == run_id).scalar()
            )
            report_count = db.query(BidPreliminaryReport).filter(
                BidPreliminaryReport.run_id == run_id,
                BidPreliminaryReport.status == "ready",
            ).count()
        finally:
            db.close()
        if terminal_status in {"succeeded", "failed", "stale", "cancelled"}:
            break
    db = session_factory()
    try:
        diagnostics = {
            "task_error_codes": task_error_codes,
            "tasks": [
                (str(task.task_type), str(task.status), str(task.current_attempt_id or ""))
                for task in db.query(BidTask)
                .filter(BidTask.run_id == run_id, BidTask.status != "succeeded")
                .order_by(BidTask.task_key.asc())
                .all()
            ],
            "attempts": [
                (str(attempt.task_id), str(attempt.status), attempt.error_code)
                for attempt in db.query(BidTaskAttempt)
                .join(BidTask, BidTask.id == BidTaskAttempt.task_id)
                .filter(BidTask.run_id == run_id, BidTaskAttempt.status == "failed")
                .order_by(BidTaskAttempt.created_at.asc())
                .all()
            ],
            "dispatches": [
                (
                    str(dispatch.adapter_name),
                    str(dispatch.status),
                    dispatch.last_error_code,
                )
                for dispatch in db.query(BidToolDispatch)
                .join(BidTask, BidTask.id == BidToolDispatch.task_id)
                .filter(BidTask.run_id == run_id, BidToolDispatch.status != "succeeded")
                .order_by(BidToolDispatch.created_at.asc())
                .all()
            ],
            "invocations": [
                (
                    str(invocation.tool_name),
                    str(invocation.status),
                    invocation.error_code,
                )
                for invocation in db.query(BidToolInvocation)
                .filter(
                    BidToolInvocation.run_id == run_id,
                    BidToolInvocation.status != "succeeded",
                )
                .order_by(BidToolInvocation.created_at.asc())
                .all()
            ],
            "model_calls": [
                (
                    str(call.task_id),
                    int(call.action_seq),
                    str(call.status),
                    call.last_error_code,
                )
                for call in db.query(BidModelCall)
                .join(BidTask, BidTask.id == BidModelCall.task_id)
                .filter(BidTask.run_id == run_id, BidModelCall.status != "succeeded")
                .order_by(BidModelCall.created_at.asc())
                .all()
            ],
            "model_attempts": [
                (
                    str(attempt.model_call_id),
                    int(attempt.attempt_no),
                    str(attempt.status),
                    attempt.error_code,
                    dict(attempt.detail_json or {}),
                )
                for attempt in db.query(BidModelCallAttempt)
                .join(BidModelCall, BidModelCall.id == BidModelCallAttempt.model_call_id)
                .join(BidTask, BidTask.id == BidModelCall.task_id)
                .filter(BidTask.run_id == run_id, BidModelCall.status != "succeeded")
                .order_by(BidModelCallAttempt.created_at.asc())
                .all()
            ],
        }
    finally:
        db.close()
    assert totals == {
        "task_failed": 0,
        "model_failed": 0,
        "tool_failed": 0,
        "validation_errors": 0,
    }, json.dumps(diagnostics, ensure_ascii=False, default=str)
    assert terminal_status == "succeeded", json.dumps(
        diagnostics, ensure_ascii=False, default=str
    )
    assert report_count == 1, json.dumps(diagnostics, ensure_ascii=False, default=str)
    db = session_factory()
    try:
        assert db.query(BidFactAssertion).filter(
            BidFactAssertion.run_id == run_id,
            BidFactAssertion.status == "accepted",
        ).count() >= 10
        assert db.query(BidResolvedFact).filter(BidResolvedFact.run_id == run_id).count() >= 10
        assert db.query(BidHardGateResult).filter(BidHardGateResult.run_id == run_id).count() == 7
        assert (
            db.query(BidClaimCitation)
            .join(BidReportClaim, BidReportClaim.id == BidClaimCitation.claim_id)
            .filter(BidReportClaim.run_id == run_id)
            .count()
            >= 1
        )
        assert db.query(BidTask).filter(BidTask.run_id == run_id, BidTask.status != "succeeded").count() == 0
        assert {
            str(row[0])
            for row in db.query(BidToolDispatch.adapter_mode)
            .join(BidTask, BidTask.id == BidToolDispatch.task_id)
            .filter(
                BidTask.run_id == run_id,
                BidToolDispatch.adapter_name.in_(
                    ("bid-evidence-mcp-search", "bid-evidence-mcp-read")
                ),
            )
            .all()
        } == {"local_readonly"}
        report = db.query(BidPreliminaryReport).filter(BidPreliminaryReport.run_id == run_id).one()
        assert report.assessment_id == assessment_id
        assert report.report_json["decision"]["code"] in {
            "bid",
            "no_bid",
            "conditional",
            "insufficient",
        }
    finally:
        db.close()


def test_api60_api61_report_acl_etag_status_and_feature_gates(api_runtime) -> None:
    client, session_factory, owner = api_runtime
    assessment_id, run_id = _create_phase3c_committed_run(api_runtime)
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    report_id = str(uuid.uuid4())
    report_json = {
        "schema": "bid.preliminary.report.mvp1.v1",
        "decision": {"code": "insufficient", "investment_level": "hold"},
        "hard_gates": [
            {"gate_code": "HG01", "status": "unknown"},
            {"gate_code": "HG02", "status": "pass"},
        ],
        "claims": [],
        "citations": [],
    }
    db = session_factory()
    try:
        with db.begin():
            run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one()
            task = (
                db.query(BidTask)
                .filter(BidTask.run_id == run_id)
                .order_by(BidTask.task_key.asc())
                .first()
            )
            decision = BidPreliminaryDecision(
                id=str(uuid.uuid4()),
                run_id=run_id,
                task_id=str(task.id),
                rule_set_id=str(run.rule_set_id),
                formula_catalog_version_id=str(run.formula_catalog_version_id),
                decision="insufficient",
                investment_level="hold",
                failed_gate_count=0,
                unknown_gate_count=1,
                unknown_fact_count=1,
                summary="资料不足，暂缓投入。",
                reason_codes_json=["CRITICAL_FACT_UNKNOWN"],
                input_hash=canonical_hash({"run_id": run_id, "kind": "decision-input"}),
                decision_hash=canonical_hash({"run_id": run_id, "kind": "decision"}),
            )
            validation = BidReportValidation(
                id=str(uuid.uuid4()),
                run_id=run_id,
                task_id=str(task.id),
                status="passed",
                validator_version="bid-claim-evidence-validator-mvp1-v1",
                checks_json=[{"code": "REPORT_SCHEMA", "status": "passed"}],
                input_hash=canonical_hash({"run_id": run_id, "kind": "validation-input"}),
                result_hash=canonical_hash({"run_id": run_id, "kind": "validation"}),
            )
            db.add_all([decision, validation])
            db.flush()
            db.add(
                BidPreliminaryReport(
                    id=report_id,
                    assessment_id=assessment_id,
                    run_id=run_id,
                    decision_id=str(decision.id),
                    validation_id=str(validation.id),
                    report_version=1,
                    status="ready",
                    title="投标机会初筛报告",
                    executive_summary="资料不足，暂缓投入。",
                    report_json=report_json,
                    report_hash=canonical_hash(report_json),
                    generated_at=now,
                )
            )
    finally:
        db.close()

    previous = {
        "feature_bid_assessment_phase4_mvp": settings.feature_bid_assessment_phase4_mvp,
        "feature_bid_assessment_phase4_preliminary_report": (
            settings.feature_bid_assessment_phase4_preliminary_report
        ),
    }
    object.__setattr__(settings, "feature_bid_assessment_phase4_mvp", True)
    object.__setattr__(settings, "feature_bid_assessment_phase4_preliminary_report", True)
    try:
        listed = client.get(f"/api/v1/bid-assessments/{assessment_id}/reports")
        assert listed.status_code == 200
        assert listed.headers["cache-control"] == "private, no-store"
        assert listed.json()["data"]["total"] == 1
        assert listed.json()["data"]["items"][0]["gate_summary"] == {
            "pass": 1,
            "fail": 0,
            "unknown": 1,
        }

        detail = client.get(f"/api/v1/bid-reports/{report_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["report"] == report_json
        etag = detail.headers["etag"]
        assert client.get(
            f"/api/v1/bid-reports/{report_id}",
            headers={"If-None-Match": etag},
        ).status_code == 304

        outsider = _create_user(session_factory)
        client.app.state.active_user["value"] = outsider
        assert client.get(f"/api/v1/bid-assessments/{assessment_id}/reports").status_code == 404
        assert client.get(f"/api/v1/bid-reports/{report_id}").status_code == 404
        admin = _create_user(session_factory, role="admin")
        client.app.state.active_user["value"] = admin
        assert client.get(f"/api/v1/bid-reports/{report_id}").status_code == 200

        db = session_factory()
        try:
            with db.begin():
                db.query(BidPreliminaryReport).filter(
                    BidPreliminaryReport.id == report_id
                ).update({BidPreliminaryReport.status: "invalid"})
        finally:
            db.close()
        client.app.state.active_user["value"] = owner
        assert client.get(f"/api/v1/bid-assessments/{assessment_id}/reports").json()["data"][
            "total"
        ] == 0
        assert client.get(f"/api/v1/bid-reports/{report_id}").status_code == 404

        object.__setattr__(settings, "feature_bid_assessment_phase4_mvp", False)
        assert client.get(f"/api/v1/bid-reports/{report_id}").status_code == 404
    finally:
        client.app.state.active_user["value"] = owner
        for name, value in previous.items():
            object.__setattr__(settings, name, value)
