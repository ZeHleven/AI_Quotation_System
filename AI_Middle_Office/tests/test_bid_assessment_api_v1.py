from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker

import app.models.registry  # noqa: F401 - register the complete FK graph
from app.api.v1 import bid_assessments as assessments_api
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
from app.models.bid_assessment_runtime import BidAnalysisRun
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
    canonical_hash,
    project_outbox_event_to_public,
)
from app.services.bid_assessment_idempotency import begin_idempotent_request
from app.services.bid_upload_batch_cleanup import cleanup_due_abandoned_upload_batches
from app.services.bid_upload_file_storage import (
    BidUploadObjectCandidate,
    StoredBidUploadObject,
)
from app.services.bid_upload_files import cleanup_orphaned_bid_upload_objects


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
        object.__setattr__(settings, "feature_bid_assessment_v1_runtime", old_flag)
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
        self.fail_put = False
        self.fail_delete = False

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
