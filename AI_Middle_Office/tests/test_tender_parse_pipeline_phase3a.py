from __future__ import annotations

import hashlib
import json
import uuid
from io import BytesIO

from openpyxl import Workbook

from app.core.config import settings
from app.core.database import SessionLocal
from app.dependencies import get_current_user
from app.main import app
from app.models.bidding import BidProject, BidProjectFile
from app.models.file_object import FileObject
from app.models.tender_evidence import BidEvidenceBlock, BidEvidenceDocument
from app.models.tender_parse_pipeline import (
    BidTenderParseJob,
    BidTenderParseJobEvent,
    BidTenderSourceObject,
)
from app.models.user import User
from app.services.tender_parse_pipeline import (
    create_tender_parse_job,
    run_tender_parse_job,
)
from app.services.tender_source_storage import (
    StoredTenderObject,
    TenderSourceStorageError,
)


class MemoryTenderStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.store_calls = 0
        self.delete_calls = 0
        self.get_failures_remaining = 0

    def store(
        self,
        *,
        content: bytes,
        original_filename: str,
        content_type: str | None,
        username: str,
    ) -> StoredTenderObject:
        self.store_calls += 1
        object_name = (
            f"bid_tender_source/{username}/{self.store_calls}-{original_filename}"
        )
        self.objects[("test-bucket", object_name)] = content
        return StoredTenderObject(
            bucket="test-bucket",
            object_name=object_name,
            size_bytes=len(content),
            content_type=content_type or "application/octet-stream",
        )

    def get(self, *, bucket: str, object_name: str) -> bytes:
        if self.get_failures_remaining:
            self.get_failures_remaining -= 1
            raise TenderSourceStorageError("temporary test storage outage")
        return self.objects[(bucket, object_name)]

    def delete(self, *, bucket: str, object_name: str) -> None:
        self.delete_calls += 1
        self.objects.pop((bucket, object_name), None)


def _create_user(db, role: str = "staff") -> User:
    user = User(
        username=f"phase3a-{uuid.uuid4().hex[:12]}",
        hashed_password="phase3a-test",
        role=role,
        quota=100,
        is_active=True,
    )
    db.add(user)
    db.flush()
    user.effective_roles = {role}
    return user


def _create_project(db, user: User, name: str = "Phase 3a 解析项目") -> BidProject:
    project = BidProject(
        project_uuid=str(uuid.uuid4()),
        project_name=name,
        status="draft",
        owner_user_id=user.id,
        created_by=user.id,
    )
    db.add(project)
    db.commit()
    return project


def _create_job(
    db,
    *,
    project: BidProject,
    user: User,
    storage: MemoryTenderStorage,
    content: bytes | None = None,
    max_attempts: int = 3,
):
    return create_tender_parse_job(
        db,
        project_uuid=project.project_uuid,
        content=content
        or "投标截止时间为2026年8月15日14:00。\n投标保证金20万元。".encode(
            "utf-8"
        ),
        original_filename="招标文件.txt",
        content_type="text/plain",
        file_type="tender_document",
        document_key="tender-notice",
        current_user=user,
        storage=storage,
        max_attempts=max_attempts,
    )


def test_pipeline_stores_parses_and_ingests_evidence(client) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(db, user)
        project_uuid = project.project_uuid
        project_id = project.id
        created = _create_job(
            db,
            project=project,
            user=user,
            storage=storage,
        )
        assert created.status == "queued"
        assert created.idempotent is False
        assert storage.store_calls == 1
    finally:
        db.close()

    result = run_tender_parse_job(
        created.job_uuid,
        session_factory=SessionLocal,
        storage=storage,
    )
    assert result.status == "completed"
    assert result.evidence_document_uuid

    check_db = SessionLocal()
    try:
        job = (
            check_db.query(BidTenderParseJob)
            .filter(BidTenderParseJob.job_uuid == created.job_uuid)
            .one()
        )
        source = (
            check_db.query(BidTenderSourceObject)
            .filter(BidTenderSourceObject.id == job.source_object_id)
            .one()
        )
        evidence = (
            check_db.query(BidEvidenceDocument)
            .filter(
                BidEvidenceDocument.evidence_document_uuid
                == result.evidence_document_uuid
            )
            .one()
        )
        block = (
            check_db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.document_id == evidence.id)
            .order_by(BidEvidenceBlock.block_order.asc())
            .first()
        )
        events = (
            check_db.query(BidTenderParseJobEvent)
            .filter(BidTenderParseJobEvent.parse_job_id == job.id)
            .order_by(BidTenderParseJobEvent.id.asc())
            .all()
        )
        assert source.status == "ingested"
        assert job.bid_project_file_id is not None
        assert (
            check_db.query(BidProjectFile)
            .filter(BidProjectFile.id == job.bid_project_file_id)
            .count()
            == 1
        )
        assert (
            check_db.query(FileObject)
            .filter(FileObject.file_id == source.file_object_id)
            .count()
            == 1
        )
        assert block.project_id == project_id
        assert json.loads(block.locator_json)["locator_type"] == "section"
        assert [item.event_type for item in events] == [
            "job_created",
            "attempt_started",
            "source_verified",
            "evidence_ingested",
        ]
        assert evidence.project_id == project_id
        assert evidence.document_key == "tender-notice"
        assert evidence.original_filename == "招标文件.txt"
        assert project_uuid
    finally:
        check_db.close()


def test_pipeline_auto_classifies_file_before_evidence_ingestion(client) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(db, user, "Phase 5c 自动分类项目")
        created = create_tender_parse_job(
            db,
            project_uuid=project.project_uuid,
            content=(
                "分部分项工程和单价措施项目清单\n"
                "项目编码 项目名称 项目特征 单位 工程量 综合单价 合价"
            ).encode("utf-8"),
            original_filename="附件2.txt",
            content_type="text/plain",
            file_type="auto",
            document_key="auto-附件2.txt",
            current_user=user,
            storage=storage,
        )
    finally:
        db.close()

    result = run_tender_parse_job(
        created.job_uuid,
        session_factory=SessionLocal,
        storage=storage,
    )
    assert result.status == "completed"

    check_db = SessionLocal()
    try:
        job = (
            check_db.query(BidTenderParseJob)
            .filter(BidTenderParseJob.job_uuid == created.job_uuid)
            .one()
        )
        source = (
            check_db.query(BidTenderSourceObject)
            .filter(BidTenderSourceObject.id == job.source_object_id)
            .one()
        )
        parsed_file = (
            check_db.query(BidProjectFile)
            .filter(BidProjectFile.id == job.bid_project_file_id)
            .one()
        )
        evidence = (
            check_db.query(BidEvidenceDocument)
            .filter(
                BidEvidenceDocument.evidence_document_uuid
                == job.evidence_document_uuid
            )
            .one()
        )
        events = (
            check_db.query(BidTenderParseJobEvent)
            .filter(BidTenderParseJobEvent.parse_job_id == job.id)
            .order_by(BidTenderParseJobEvent.id.asc())
            .all()
        )
        assert source.file_type == "bill_of_quantities"
        assert parsed_file.file_type == "bill_of_quantities"
        assert evidence.document_type == "bill_of_quantities"
        assert "file_type_classified" in {
            item.event_type for item in events
        }
    finally:
        check_db.close()


def test_pipeline_records_safe_workbook_scan_diagnostics(client) -> None:
    del client
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "二期工程量清单"
    sheet.append(
        [
            "项目编码",
            "项目名称",
            "项目特征",
            "单位",
            "工程量",
            "综合单价",
        ]
    )
    sheet.append(
        [
            "011102003001",
            "块料楼地面",
            "800×800地砖",
            "m2",
            100,
            0,
        ]
    )
    workbook_buffer = BytesIO()
    workbook.save(workbook_buffer)

    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(
            db,
            user,
            "Phase 3a Excel安全扫描项目",
        )
        created = create_tender_parse_job(
            db,
            project_uuid=project.project_uuid,
            content=workbook_buffer.getvalue(),
            original_filename="二期工程量清单.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            file_type="auto",
            document_key="auto-二期工程量清单.xlsx",
            current_user=user,
            storage=storage,
        )
    finally:
        db.close()

    result = run_tender_parse_job(
        created.job_uuid,
        session_factory=SessionLocal,
        storage=storage,
    )
    assert result.status == "completed"

    check_db = SessionLocal()
    try:
        job = (
            check_db.query(BidTenderParseJob)
            .filter(BidTenderParseJob.job_uuid == created.job_uuid)
            .one()
        )
        events = (
            check_db.query(BidTenderParseJobEvent)
            .filter(BidTenderParseJobEvent.parse_job_id == job.id)
            .order_by(BidTenderParseJobEvent.id.asc())
            .all()
        )
        diagnostic_event = next(
            item
            for item in events
            if item.event_type == "workbook_scan_completed"
        )
        diagnostic = json.loads(diagnostic_event.message)
        assert diagnostic["sheet_count"] == 1
        assert diagnostic["parsed_sheet_count"] == 1
        assert diagnostic["quarantined_sheet_count"] == 0
        assert diagnostic["extracted_segment_count"] == 2
    finally:
        check_db.close()


def test_create_is_idempotent_before_object_storage(client) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(db, user)
        first = _create_job(
            db,
            project=project,
            user=user,
            storage=storage,
        )
        repeated = _create_job(
            db,
            project=project,
            user=user,
            storage=storage,
        )
        assert repeated.idempotent is True
        assert repeated.job_uuid == first.job_uuid
        assert repeated.source_uuid == first.source_uuid
        assert storage.store_calls == 1
        assert (
            db.query(BidTenderSourceObject)
            .filter(BidTenderSourceObject.project_id == project.id)
            .count()
            == 1
        )
    finally:
        db.close()


def test_new_parser_version_reuses_original_object(client, monkeypatch) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(db, user)
        first = _create_job(
            db,
            project=project,
            user=user,
            storage=storage,
        )
        first_result = run_tender_parse_job(
            first.job_uuid,
            session_factory=SessionLocal,
            storage=storage,
        )
        assert first_result.status == "completed"
        monkeypatch.setattr(
            "app.services.tender_parse_pipeline.BIDDING_PARSER_VERSION",
            "phase3a-test-parser-v2",
        )
        monkeypatch.setattr(
            "app.services.tender_parse_pipeline.extract_tender_text",
            lambda content, filename, content_type: {
                "filename": filename,
                "parser_version": "phase3a-test-parser-v2",
                "text": content.decode("utf-8"),
                "segments": [
                    {
                        "text": content.decode("utf-8"),
                        "page": 1,
                        "source_location": "第1页",
                    }
                ],
                "page_count": 1,
                "section_count": 1,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        )
        reparsed = _create_job(
            db,
            project=project,
            user=user,
            storage=storage,
        )
        assert reparsed.idempotent is False
        assert reparsed.job_uuid != first.job_uuid
        assert reparsed.source_uuid == first.source_uuid
        assert storage.store_calls == 1
        reparsed_result = run_tender_parse_job(
            reparsed.job_uuid,
            session_factory=SessionLocal,
            storage=storage,
        )
        assert reparsed_result.status == "completed"
        assert (
            db.query(BidTenderParseJob)
            .join(
                BidTenderSourceObject,
                BidTenderSourceObject.id
                == BidTenderParseJob.source_object_id,
            )
            .filter(BidTenderSourceObject.project_id == project.id)
            .count()
            == 2
        )
        documents = (
            db.query(BidEvidenceDocument)
            .filter(BidEvidenceDocument.project_id == project.id)
            .order_by(BidEvidenceDocument.version_no.asc())
            .all()
        )
        assert [item.version_no for item in documents] == [1, 2]
        assert documents[0].parser_version != documents[1].parser_version
        assert documents[1].parser_version == "phase3a-test-parser-v2"
    finally:
        db.close()


def test_auto_reparse_reuses_source_after_classification(
    client,
    monkeypatch,
) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(
            db,
            user,
            "Phase 5c 自动分类重解析项目",
        )
        first = create_tender_parse_job(
            db,
            project_uuid=project.project_uuid,
            content=(
                "分部分项工程和单价措施项目清单\n"
                "项目编码 项目名称 工程量 综合单价 合价"
            ).encode("utf-8"),
            original_filename="附件2.txt",
            content_type="text/plain",
            file_type="auto",
            document_key="auto-reparse-source",
            current_user=user,
            storage=storage,
        )
        first_result = run_tender_parse_job(
            first.job_uuid,
            session_factory=SessionLocal,
            storage=storage,
        )
        assert first_result.status == "completed"
        monkeypatch.setattr(
            "app.services.tender_parse_pipeline.BIDDING_PARSER_VERSION",
            "phase5c-auto-reparse-v2",
        )
        repeated = create_tender_parse_job(
            db,
            project_uuid=project.project_uuid,
            content=(
                "分部分项工程和单价措施项目清单\n"
                "项目编码 项目名称 工程量 综合单价 合价"
            ).encode("utf-8"),
            original_filename="附件2.txt",
            content_type="text/plain",
            file_type="auto",
            document_key="auto-reparse-source",
            current_user=user,
            storage=storage,
        )
        assert repeated.idempotent is False
        assert repeated.source_uuid == first.source_uuid
        assert storage.store_calls == 1
        source = (
            db.query(BidTenderSourceObject)
            .filter(
                BidTenderSourceObject.source_uuid
                == repeated.source_uuid
            )
            .one()
        )
        assert source.file_type == "auto"
    finally:
        db.close()


def test_transient_storage_failure_retries_then_becomes_terminal(client) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(db, user)
        created = _create_job(
            db,
            project=project,
            user=user,
            storage=storage,
            max_attempts=2,
        )
    finally:
        db.close()

    storage.get_failures_remaining = 2
    first = run_tender_parse_job(
        created.job_uuid,
        session_factory=SessionLocal,
        storage=storage,
    )
    assert first.status == "retryable"
    assert first.attempt_count == 1
    assert first.error_code == "SOURCE_STORAGE_UNAVAILABLE"

    second = run_tender_parse_job(
        created.job_uuid,
        session_factory=SessionLocal,
        storage=storage,
    )
    assert second.status == "failed"
    assert second.attempt_count == 2
    assert second.error_code == "SOURCE_STORAGE_UNAVAILABLE"


def test_source_hash_mismatch_is_not_retryable(client) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(db, user)
        created = _create_job(
            db,
            project=project,
            user=user,
            storage=storage,
        )
        job = (
            db.query(BidTenderParseJob)
            .filter(BidTenderParseJob.job_uuid == created.job_uuid)
            .one()
        )
        source = (
            db.query(BidTenderSourceObject)
            .filter(BidTenderSourceObject.id == job.source_object_id)
            .one()
        )
        file_object = (
            db.query(FileObject)
            .filter(FileObject.file_id == source.file_object_id)
            .one()
        )
        storage.objects[(file_object.bucket, file_object.object_name)] = (
            b"tampered"
        )
    finally:
        db.close()

    result = run_tender_parse_job(
        created.job_uuid,
        session_factory=SessionLocal,
        storage=storage,
    )
    assert result.status == "failed"
    assert result.attempt_count == 1
    assert result.error_code == "SOURCE_HASH_MISMATCH"


def test_parse_job_api_creates_queued_job(client, monkeypatch) -> None:
    storage = MemoryTenderStorage()
    db = SessionLocal()
    old_flag = settings.feature_bidding_mvp
    try:
        owner = _create_user(db)
        project = _create_project(db, owner, "API tender upload")
        owner_id = owner.id
        owner_username = owner.username
        project_uuid = project.project_uuid
    finally:
        db.close()

    def owner_dependency() -> User:
        user = User(
            id=owner_id,
            username=owner_username,
            role="staff",
            is_active=True,
        )
        user.effective_roles = {"staff"}
        return user

    monkeypatch.setattr(
        "app.api.v1.tender_evidence_pipeline._storage",
        lambda: storage,
    )
    object.__setattr__(settings, "feature_bidding_mvp", True)
    app.dependency_overrides[get_current_user] = owner_dependency
    try:
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project_uuid}"
            "/evidence/parse-jobs",
            data={
                "document_key": "api-tender",
            },
            files={
                "file": (
                    "招标文件.txt",
                    "招标截止时间为2026年9月1日。".encode("utf-8"),
                    "text/plain",
                )
            },
        )
        assert response.status_code == 201
        payload = response.json()["data"]
        assert payload["status"] == "queued"
        assert payload["file_type"] == "auto"
        assert payload["document_key"] == "api-tender"
        assert payload["idempotent"] is False
        assert storage.store_calls == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        object.__setattr__(settings, "feature_bidding_mvp", old_flag)


def test_db_commit_failure_deletes_only_new_object(client, monkeypatch) -> None:
    del client
    storage = MemoryTenderStorage()
    db = SessionLocal()
    try:
        user = _create_user(db)
        project = _create_project(db, user)

        def fail_commit() -> None:
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        try:
            _create_job(
                db,
                project=project,
                user=user,
                storage=storage,
            )
            raise AssertionError("commit failure should propagate")
        except RuntimeError as exc:
            assert str(exc) == "simulated commit failure"
        assert storage.store_calls == 1
        assert storage.delete_calls == 1
        assert storage.objects == {}
    finally:
        db.rollback()
        db.close()


def test_parse_job_api_hides_other_users_project(client) -> None:
    db = SessionLocal()
    old_flag = settings.feature_bidding_mvp
    try:
        owner = _create_user(db)
        project = _create_project(db, owner, "Owner-only tender")
        outsider = _create_user(db)
        db.commit()
        outsider_id = outsider.id
        outsider_username = outsider.username
        project_uuid = project.project_uuid
    finally:
        db.close()

    def outsider_dependency() -> User:
        user = User(
            id=outsider_id,
            username=outsider_username,
            role="staff",
            is_active=True,
        )
        user.effective_roles = {"staff"}
        return user

    object.__setattr__(settings, "feature_bidding_mvp", True)
    app.dependency_overrides[get_current_user] = outsider_dependency
    try:
        response = client.get(
            f"/api/v1/admin/bidding/projects/{project_uuid}"
            "/evidence/parse-jobs"
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "BID_PROJECT_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        object.__setattr__(settings, "feature_bidding_mvp", old_flag)
