from __future__ import annotations

import hashlib
import json
import uuid

from app.core.database import SessionLocal
from app.models.bidding import BidProject, BidProjectFile
from app.models.tender_evidence import BidEvidenceBlock, BidEvidenceDocument
from app.models.tender_evidence_index import BidEvidenceIndexJob
from app.models.user import User
from app.services.tender_evidence_indexing import (
    run_tender_evidence_index_job,
)
from app.services.tender_evidence_ingestion import ingest_bid_project_file
from mcp_servers.tender_evidence.hybrid_client import (
    HybridReindexResult,
    HybridSearchHit,
    TenderHybridSearchUnavailable,
    _configured_index_secret,
)
from mcp_servers.tender_evidence.sqlalchemy_repository import (
    SqlAlchemyTenderEvidenceRepository,
)


class FakeHybridClient:
    service_url = "http://hybrid.test"

    def __init__(self) -> None:
        self.reindex_calls: list[dict] = []
        self.search_hits: list[HybridSearchHit] = []
        self.search_calls = 0
        self.search_modes: list[str] = []
        self.fail_reindex = False
        self.fail_search = False

    def reindex(
        self,
        *,
        case_id,
        manifest_version,
        manifest_hash,
        index_schema_version,
        blocks,
    ) -> HybridReindexResult:
        if self.fail_reindex:
            raise TenderHybridSearchUnavailable("test outage")
        self.reindex_calls.append(
            {
                "case_id": case_id,
                "manifest_version": manifest_version,
                "manifest_hash": manifest_hash,
                "index_schema_version": index_schema_version,
                "blocks": list(blocks),
            }
        )
        return HybridReindexResult(
            case_id=case_id,
            manifest_version=manifest_version,
            manifest_hash=manifest_hash,
            indexed_block_count=len(blocks),
            idempotent=False,
        )

    def search(
        self,
        *,
        case_id,
        manifest_version,
        manifest_hash,
        query,
        top_k,
        search_mode="hybrid",
    ) -> list[HybridSearchHit]:
        del case_id, manifest_version, manifest_hash, query
        self.search_calls += 1
        self.search_modes.append(search_mode)
        if self.fail_search:
            raise TenderHybridSearchUnavailable("test outage")
        return self.search_hits[:top_k]


def test_hybrid_secret_can_be_loaded_from_a_file(monkeypatch, tmp_path):
    secret_file = tmp_path / "tender-index.secret"
    secret_file.write_text("file-secret-value\n", encoding="utf-8")
    monkeypatch.delenv("TENDER_EVIDENCE_INDEX_SECRET", raising=False)
    monkeypatch.setenv(
        "TENDER_EVIDENCE_INDEX_SECRET_FILE",
        str(secret_file),
    )
    assert _configured_index_secret() == "file-secret-value"


def test_hybrid_direct_secret_takes_precedence(monkeypatch, tmp_path):
    secret_file = tmp_path / "tender-index.secret"
    secret_file.write_text("file-secret-value", encoding="utf-8")
    monkeypatch.setenv("TENDER_EVIDENCE_INDEX_SECRET", "direct-secret")
    monkeypatch.setenv(
        "TENDER_EVIDENCE_INDEX_SECRET_FILE",
        str(secret_file),
    )
    assert _configured_index_secret() == "direct-secret"


def _create_user(db, prefix: str = "phase3b") -> User:
    user = User(
        username=f"{prefix}-{uuid.uuid4().hex[:12]}",
        hashed_password="phase3b-test",
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


def _create_source(
    db,
    *,
    project: BidProject,
    user: User,
    filename: str,
    texts: list[str],
    parser_version: str = "phase3b-parser-v1",
) -> BidProjectFile:
    raw = "\n".join(texts)
    source = BidProjectFile(
        file_uuid=str(uuid.uuid4()),
        project_id=project.id,
        file_type="tender_document",
        original_filename=filename,
        content_type="text/plain",
        size_bytes=len(raw.encode("utf-8")),
        sha256=hashlib.sha256(
            f"{project.project_uuid}:{filename}:{raw}:{parser_version}".encode(
                "utf-8"
            )
        ).hexdigest(),
        parser_status="parsed",
        parser_version=parser_version,
        extracted_text=raw,
        segments_json=json.dumps(
            [
                {
                    "text": text,
                    "page": index,
                    "source_location": f"第{index}页",
                    "keywords": ["保证金"] if "保证金" in text else [],
                }
                for index, text in enumerate(texts, start=1)
            ],
            ensure_ascii=False,
        ),
        page_count=len(texts),
        section_count=len(texts),
        uploaded_by=user.id,
    )
    db.add(source)
    db.flush()
    return source


def _seed_evidence(
    db,
    *,
    project_name: str,
    texts: list[str],
    document_key: str = "tender-notice",
):
    user = _create_user(db)
    project = _create_project(db, user, project_name)
    source = _create_source(
        db,
        project=project,
        user=user,
        filename=f"{project_name}.txt",
        texts=texts,
    )
    result = ingest_bid_project_file(
        db,
        project_uuid=project.project_uuid,
        file_uuid=source.file_uuid,
        document_key=document_key,
    )
    db.commit()
    return user, project, source, result


def test_ingestion_creates_idempotent_index_outbox_and_runner_snapshot(client):
    del client
    db = SessionLocal()
    fake = FakeHybridClient()
    try:
        _, project, source, evidence = _seed_evidence(
            db,
            project_name="Phase 3b 索引项目",
            texts=[
                "投标截止时间为2026年8月15日14:00。",
                "投标保证金为20万元。",
            ],
        )
        job = (
            db.query(BidEvidenceIndexJob)
            .filter(BidEvidenceIndexJob.job_uuid == evidence.index_job_uuid)
            .one()
        )
        assert job.status == "queued"
        assert job.requested_block_count == 2

        repeated = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=source.file_uuid,
            document_key="tender-notice",
        )
        db.commit()
        assert repeated.index_job_uuid == evidence.index_job_uuid
        assert (
            db.query(BidEvidenceIndexJob)
            .filter(BidEvidenceIndexJob.project_id == project.id)
            .count()
            == 1
        )
        project_uuid = project.project_uuid
    finally:
        db.close()

    indexed = run_tender_evidence_index_job(
        evidence.index_job_uuid,
        session_factory=SessionLocal,
        client=fake,
    )
    assert indexed.status == "completed"
    assert indexed.indexed_block_count == 2
    assert len(fake.reindex_calls) == 1
    call = fake.reindex_calls[0]
    assert call["case_id"] == project_uuid
    assert call["manifest_version"] == evidence.manifest_version
    assert len(call["blocks"]) == 2
    assert call["blocks"][0].locator["locator_type"] == "page"

    repeated_run = run_tender_evidence_index_job(
        evidence.index_job_uuid,
        session_factory=SessionLocal,
        client=fake,
    )
    assert repeated_run.status == "completed"
    assert len(fake.reindex_calls) == 1


def test_superseded_manifest_job_is_cancelled_without_remote_write(client):
    del client
    db = SessionLocal()
    fake = FakeHybridClient()
    try:
        user, project, _, first = _seed_evidence(
            db,
            project_name="Phase 3b 版本项目",
            texts=["原公告截止时间为2026年8月12日。"],
        )
        new_source = _create_source(
            db,
            project=project,
            user=user,
            filename="澄清公告.txt",
            texts=["澄清后截止时间为2026年8月15日。"],
            parser_version="phase3b-parser-v2",
        )
        second = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=new_source.file_uuid,
            document_key="tender-notice",
        )
        db.commit()
    finally:
        db.close()

    old_result = run_tender_evidence_index_job(
        first.index_job_uuid,
        session_factory=SessionLocal,
        client=fake,
    )
    assert old_result.status == "cancelled"
    assert old_result.error_code == "MANIFEST_SUPERSEDED"
    assert fake.reindex_calls == []

    current_result = run_tender_evidence_index_job(
        second.index_job_uuid,
        session_factory=SessionLocal,
        client=fake,
    )
    assert current_result.status == "completed"
    assert len(fake.reindex_calls) == 1
    assert fake.reindex_calls[0]["manifest_version"] == 2


def test_index_service_failure_is_retryable(client):
    del client
    db = SessionLocal()
    fake = FakeHybridClient()
    fake.fail_reindex = True
    try:
        _, _, _, evidence = _seed_evidence(
            db,
            project_name="Phase 3b 索引失败项目",
            texts=["资格要求：建筑装修装饰工程专业承包一级。"],
        )
    finally:
        db.close()

    result = run_tender_evidence_index_job(
        evidence.index_job_uuid,
        session_factory=SessionLocal,
        client=fake,
    )
    assert result.status == "retryable"
    assert result.error_code == "HYBRID_SERVICE_UNAVAILABLE"
    assert result.attempt_count == 1


def test_repository_hydrates_hybrid_hits_and_drops_cross_project_ids(client):
    del client
    db = SessionLocal()
    try:
        _, project_a, _, _ = _seed_evidence(
            db,
            project_name="Phase 3b 检索项目A",
            texts=[
                "项目A投标保证金为20万元。",
                "项目A工期要求为120日历天。",
            ],
        )
        _, project_b, _, _ = _seed_evidence(
            db,
            project_name="Phase 3b 隔离项目B",
            texts=["项目B内部控制价为500万元。"],
            document_key="private-budget",
        )
        blocks_a = (
            db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.project_id == project_a.id)
            .order_by(BidEvidenceBlock.block_order.asc())
            .all()
        )
        block_b = (
            db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.project_id == project_b.id)
            .one()
        )
        block_a_first_evidence_id = blocks_a[0].evidence_id
        block_a_second_evidence_id = blocks_a[1].evidence_id
        block_a_second_block_id = blocks_a[1].block_id
        block_b_evidence_id = block_b.evidence_id
        block_b_block_id = block_b.block_id
        index_job_a = (
            db.query(BidEvidenceIndexJob)
            .filter(BidEvidenceIndexJob.project_id == project_a.id)
            .one()
        )
        index_job_a.status = "completed"
        index_job_a.stage = "completed"
        index_job_a.indexed_block_count = index_job_a.requested_block_count
        db.commit()
        project_a_uuid = project_a.project_uuid
    finally:
        db.close()

    fake = FakeHybridClient()
    fake.search_hits = [
        HybridSearchHit(
            evidence_id=block_b_evidence_id,
            block_id=block_b_block_id,
            rrf_score=0.04,
        ),
        HybridSearchHit(
            evidence_id=block_a_second_evidence_id,
            block_id=block_a_second_block_id,
            rrf_score=0.03,
        ),
        HybridSearchHit(
            evidence_id=block_a_first_evidence_id,
            block_id="wrong-block-id",
            rrf_score=0.02,
        ),
    ]
    repository = SqlAlchemyTenderEvidenceRepository(
        SessionLocal,
        hybrid_search_client=fake,
    )
    matches = repository.search(
        case_id=project_a_uuid,
        query="工期",
        top_k=5,
        search_mode="semantic",
    )
    assert [item.evidence_id for item in matches] == [
        block_a_second_evidence_id
    ]
    assert fake.search_calls == 1
    assert fake.search_modes == ["semantic"]
    assert "120日历天" in matches[0].content


def test_repository_falls_back_to_database_lexical_search(client):
    del client
    db = SessionLocal()
    try:
        _, project, _, _ = _seed_evidence(
            db,
            project_name="Phase 3b 降级项目",
            texts=["开标地点为北京市西城区金融大街9号。"],
        )
        index_job = (
            db.query(BidEvidenceIndexJob)
            .filter(BidEvidenceIndexJob.project_id == project.id)
            .one()
        )
        index_job.status = "completed"
        index_job.stage = "completed"
        index_job.indexed_block_count = index_job.requested_block_count
        db.commit()
        project_uuid = project.project_uuid
    finally:
        db.close()

    fake = FakeHybridClient()
    fake.fail_search = True
    repository = SqlAlchemyTenderEvidenceRepository(
        SessionLocal,
        hybrid_search_client=fake,
    )
    matches = repository.search(
        case_id=project_uuid,
        query="金融大街9号",
        top_k=5,
    )
    assert len(matches) == 1
    assert fake.search_calls == 1
    assert "金融大街9号" in matches[0].content


def test_repository_does_not_query_incomplete_hybrid_snapshot(client):
    del client
    db = SessionLocal()
    try:
        _, project, _, _ = _seed_evidence(
            db,
            project_name="Phase 3b 未完成索引项目",
            texts=["投标文件递交截止时间为2026年10月8日。"],
        )
        project_uuid = project.project_uuid
    finally:
        db.close()

    fake = FakeHybridClient()
    repository = SqlAlchemyTenderEvidenceRepository(
        SessionLocal,
        hybrid_search_client=fake,
    )
    matches = repository.search(
        case_id=project_uuid,
        query="2026年10月8日",
        top_k=5,
    )
    assert len(matches) == 1
    assert fake.search_calls == 0
