from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from app.core.database import SessionLocal
from app.models.bidding import BidProject, BidProjectFile
from app.models.tender_evidence import (
    BidEvidenceBlock,
    BidEvidenceDocument,
    BidEvidenceManifest,
    BidEvidenceReadAudit,
)
from app.models.user import User
from app.services.tender_evidence_ingestion import (
    TenderEvidenceIngestConflict,
    ingest_bid_project_file,
)
from mcp_servers.tender_evidence.contracts import EvidenceRefInput
from mcp_servers.tender_evidence.local_repository import EvidenceNotFoundError
from mcp_servers.tender_evidence.sqlalchemy_repository import (
    SqlAlchemyTenderEvidenceRepository,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create_user(db, prefix: str) -> User:
    user = User(
        username=f"{prefix}-{uuid.uuid4().hex[:12]}",
        hashed_password="phase2-test",
        role="admin",
        quota=100,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _create_project(db, user: User, name: str) -> BidProject:
    project = BidProject(
        project_uuid=str(uuid.uuid4()),
        project_name=name,
        status="parsed",
        owner_user_id=user.id,
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    return project


def _create_source_file(
    db,
    *,
    project: BidProject,
    user: User,
    filename: str,
    texts: list[str],
    sha_seed: str,
) -> BidProjectFile:
    segments = [
        {
            "text": text,
            "source_location": f"第{index}段",
            "page": index,
            "keywords": ["投标截止时间"] if "截止时间" in text else [],
        }
        for index, text in enumerate(texts, start=1)
    ]
    source = BidProjectFile(
        file_uuid=str(uuid.uuid4()),
        project_id=project.id,
        file_type="tender_document",
        original_filename=filename,
        content_type="application/pdf",
        size_bytes=sum(len(item.encode("utf-8")) for item in texts),
        sha256=_sha(sha_seed),
        parser_status="parsed",
        parser_version="phase2-test-parser-v1",
        extracted_text="\n\n".join(texts),
        segments_json=json.dumps(segments, ensure_ascii=False),
        page_count=len(texts),
        section_count=len(texts),
        uploaded_by=user.id,
    )
    db.add(source)
    db.flush()
    return source


def _seed_two_versions(db) -> tuple[BidProject, BidProjectFile, BidProjectFile]:
    user = _create_user(db, "tender-evidence")
    project = _create_project(db, user, "Phase 2 证据版本测试项目")
    old_source = _create_source_file(
        db,
        project=project,
        user=user,
        filename="招标公告.pdf",
        texts=[
            "原招标公告：投标截止时间为2026年8月12日09:00。",
            "投标保证金为人民币20万元。",
        ],
        sha_seed="tender-notice-version-1",
    )
    new_source = _create_source_file(
        db,
        project=project,
        user=user,
        filename="招标公告澄清01.pdf",
        texts=[
            "澄清公告：投标截止时间调整为2026年8月15日14:00。",
            "投标保证金要求保持不变。",
        ],
        sha_seed="tender-notice-version-2",
    )
    return project, old_source, new_source


def test_ingest_is_idempotent_and_creates_append_only_versions(client) -> None:
    del client
    db = SessionLocal()
    try:
        project, old_source, new_source = _seed_two_versions(db)
        first = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=old_source.file_uuid,
            document_key="tender-notice",
        )
        db.commit()
        assert first.document_version == 1
        assert first.manifest_version == 1
        assert first.block_count == 2
        assert first.idempotent is False

        repeated = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=old_source.file_uuid,
            document_key="tender-notice",
        )
        db.commit()
        assert repeated.idempotent is True
        assert repeated.evidence_document_uuid == first.evidence_document_uuid
        assert repeated.manifest_version == 1

        second = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=new_source.file_uuid,
            document_key="tender-notice",
        )
        db.commit()
        assert second.document_version == 2
        assert second.manifest_version == 2
        assert second.idempotent is False

        documents = (
            db.query(BidEvidenceDocument)
            .filter(BidEvidenceDocument.project_id == project.id)
            .order_by(BidEvidenceDocument.version_no.asc())
            .all()
        )
        manifests = (
            db.query(BidEvidenceManifest)
            .filter(BidEvidenceManifest.project_id == project.id)
            .order_by(BidEvidenceManifest.version_no.asc())
            .all()
        )
        assert [(item.version_no, item.active) for item in documents] == [
            (1, False),
            (2, True),
        ]
        assert [(item.version_no, item.active) for item in manifests] == [
            (1, False),
            (2, True),
        ]
        assert (
            db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.project_id == project.id)
            .count()
            == 4
        )
        snapshot = json.loads(manifests[-1].snapshot_json)
        assert snapshot["case_id"] == project.project_uuid
        assert snapshot["manifest_version"] == 2
        assert [item["active"] for item in snapshot["documents"]] == [
            False,
            True,
        ]
    finally:
        db.rollback()
        db.close()


def test_ingest_rejects_rebinding_same_source_identity(client) -> None:
    del client
    db = SessionLocal()
    try:
        project, source, _ = _seed_two_versions(db)
        ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=source.file_uuid,
            document_key="tender-notice",
        )
        db.commit()
        with pytest.raises(TenderEvidenceIngestConflict):
            ingest_bid_project_file(
                db,
                project_uuid=project.project_uuid,
                file_uuid=source.file_uuid,
                document_key="payment-terms",
            )
    finally:
        db.rollback()
        db.close()


def test_sql_repository_scopes_search_validation_and_read_audit(client) -> None:
    del client
    db = SessionLocal()
    try:
        project, old_source, new_source = _seed_two_versions(db)
        first = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=old_source.file_uuid,
            document_key="tender-notice",
        )
        second = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=new_source.file_uuid,
            document_key="tender-notice",
        )
        other_user = _create_user(db, "tender-evidence-other")
        other_project = _create_project(db, other_user, "隔离项目")
        other_source = _create_source_file(
            db,
            project=other_project,
            user=other_user,
            filename="内部控制价.pdf",
            texts=["隔离项目的内部控制价为500万元。"],
            sha_seed="isolated-project-secret",
        )
        ingest_bid_project_file(
            db,
            project_uuid=other_project.project_uuid,
            file_uuid=other_source.file_uuid,
            document_key="private-budget",
        )
        project_uuid = project.project_uuid
        project_id = project.id
        other_project_uuid = other_project.project_uuid
        db.commit()
    finally:
        db.close()

    repository = SqlAlchemyTenderEvidenceRepository(SessionLocal)
    manifest = repository.get_manifest(case_id=project_uuid)
    assert manifest.manifest_version == second.manifest_version
    assert len(manifest.documents) == 2

    active_hits = repository.search(
        case_id=project_uuid,
        query="2026年8月15日",
        top_k=5,
    )
    assert len(active_hits) == 1
    active = active_hits[0]
    assert active.document_version == 2
    assert "8月15日" in active.content
    assert repository.search(
        case_id=project_uuid,
        query="500万元",
        top_k=5,
    ) == []

    versions, conflicts = repository.get_document_versions(
        case_id=project_uuid,
        document_key="tender-notice",
    )
    assert [item["document_version"] for item in versions] == [1, 2]
    assert [item["active"] for item in versions] == [False, True]
    assert conflicts == []

    active_ref = EvidenceRefInput(
        evidence_id=active.evidence_id,
        block_id=active.block_id,
        document_id=active.document_id,
        document_version=active.document_version,
        content_hash=active.content_hash,
    )
    validation = repository.validate_refs(
        case_id=project_uuid,
        refs=[active_ref],
        manifest_version=manifest.manifest_version,
    )
    assert validation == [
        {
            "evidence_id": active.evidence_id,
            "valid": True,
            "reasons": [],
        }
    ]

    assert repository.get_context_read_ids(
        case_id=project_uuid,
        assessment_id="ASSESSMENT-PHASE2",
        agent_run_id="RUN-PHASE2",
        evidence_ids=[active.evidence_id],
    ) == set()
    repository.record_context_read(
        case_id=project_uuid,
        assessment_id="ASSESSMENT-PHASE2",
        agent_run_id="RUN-PHASE2",
        subject="phase2-test-agent",
        evidence_id=active.evidence_id,
        trace_id="phase2-read-trace",
    )
    assert repository.get_context_read_ids(
        case_id=project_uuid,
        assessment_id="ASSESSMENT-PHASE2",
        agent_run_id="RUN-PHASE2",
        evidence_ids=[active.evidence_id],
    ) == {active.evidence_id}

    check_db = SessionLocal()
    try:
        audit = (
            check_db.query(BidEvidenceReadAudit)
            .filter(BidEvidenceReadAudit.trace_id == "phase2-read-trace")
            .one()
        )
        assert audit.project_id == project_id
        assert audit.assessment_id == "ASSESSMENT-PHASE2"
        old_document = (
            check_db.query(BidEvidenceDocument)
            .filter(
                BidEvidenceDocument.evidence_document_uuid
                == first.evidence_document_uuid
            )
            .one()
        )
        old_block = (
            check_db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.document_id == old_document.id)
            .order_by(BidEvidenceBlock.block_order.asc())
            .first()
        )
    finally:
        check_db.close()

    old_ref = EvidenceRefInput(
        evidence_id=old_block.evidence_id,
        block_id=old_block.block_id,
        document_id=old_document.evidence_document_uuid,
        document_version=old_document.version_no,
        content_hash=old_block.content_hash,
    )
    old_validation = repository.validate_refs(
        case_id=project_uuid,
        refs=[old_ref],
        manifest_version=manifest.manifest_version,
    )
    assert old_validation[0]["reasons"] == ["document_version_not_active"]

    with pytest.raises(EvidenceNotFoundError):
        repository.get_context(
            case_id=other_project_uuid,
            evidence_id=active.evidence_id,
            before_blocks=0,
            after_blocks=0,
        )
