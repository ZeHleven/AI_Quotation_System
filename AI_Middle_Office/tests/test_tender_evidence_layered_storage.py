from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from app.core.database import SessionLocal
from app.models.bidding import BidProject, BidProjectFile
from app.models.tender_evidence import BidEvidenceBlock, BidEvidenceDocument
from app.models.user import User
from app.services.tender_evidence_body_storage import (
    BODY_SCHEMA_VERSION,
    BODY_STORAGE_BACKEND_MINIO,
    StoredEvidenceBody,
    TenderEvidenceBodyIntegrityError,
    TenderEvidenceBodyReader,
)
from app.services.tender_evidence_ingestion import ingest_bid_project_file
from mcp_servers.tender_evidence.sqlalchemy_repository import (
    SqlAlchemyTenderEvidenceRepository,
)


class MemoryEvidenceBodyStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls = 0
        self.get_calls = 0

    def put(
        self,
        *,
        case_id: str,
        document_id: str,
        content: bytes,
        sha256: str,
    ) -> StoredEvidenceBody:
        self.put_calls += 1
        object_name = (
            f"bid_tender_evidence_body/{case_id}/{document_id}/{sha256}.json"
        )
        self.objects[("test-tender-bodies", object_name)] = content
        return StoredEvidenceBody(
            backend=BODY_STORAGE_BACKEND_MINIO,
            bucket="test-tender-bodies",
            object_name=object_name,
            sha256=sha256,
            size_bytes=len(content),
            schema_version=BODY_SCHEMA_VERSION,
        )

    def get(self, *, bucket: str, object_name: str) -> bytes:
        self.get_calls += 1
        return self.objects[(bucket, object_name)]


def _seed_source(db):
    user = User(
        username=f"layered-{uuid.uuid4().hex[:12]}",
        hashed_password="test",
        role="admin",
        quota=100,
        is_active=True,
    )
    db.add(user)
    db.flush()
    project = BidProject(
        project_uuid=str(uuid.uuid4()),
        project_name="分层存储测试项目",
        status="parsed",
        owner_user_id=user.id,
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    texts = [
        "投标截止时间为2026年8月15日14时。",
        "投标保证金为人民币20万元。",
    ]
    source_text = "\n".join(texts)
    source = BidProjectFile(
        file_uuid=str(uuid.uuid4()),
        project_id=project.id,
        file_type="tender_document",
        original_filename="招标文件.txt",
        content_type="text/plain",
        size_bytes=len(source_text.encode("utf-8")),
        sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        parser_status="parsed",
        parser_version="layered-test-v1",
        extracted_text=source_text,
        segments_json=json.dumps(
            [
                {
                    "text": text,
                    "page": index,
                    "source_location": f"第{index}页",
                }
                for index, text in enumerate(texts, start=1)
            ],
            ensure_ascii=False,
        ),
        page_count=2,
        section_count=2,
        uploaded_by=user.id,
    )
    db.add(source)
    db.flush()
    return project, source


def test_new_evidence_externalizes_body_and_repository_hydrates_it(client):
    del client
    storage = MemoryEvidenceBodyStorage()
    db = SessionLocal()
    try:
        project, source = _seed_source(db)
        result = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=source.file_uuid,
            document_key="tender-notice",
            body_storage=storage,
            externalize_body=True,
        )
        db.commit()
        document = (
            db.query(BidEvidenceDocument)
            .filter(
                BidEvidenceDocument.evidence_document_uuid
                == result.evidence_document_uuid
            )
            .one()
        )
        blocks = (
            db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.document_id == document.id)
            .order_by(BidEvidenceBlock.block_order.asc())
            .all()
        )
        db.refresh(source)
        assert storage.put_calls == 1
        assert document.body_storage_backend == BODY_STORAGE_BACKEND_MINIO
        assert document.body_schema_version == BODY_SCHEMA_VERSION
        assert document.body_size_bytes > 0
        assert source.extracted_text is None
        assert source.segments_json is None
        assert source.parsed_artifact_object_name == document.body_object_name
        assert all(item.content is None for item in blocks)
        assert [item.content_length for item in blocks] == [21, 14]
        project_uuid = project.project_uuid
    finally:
        db.close()

    reader = TenderEvidenceBodyReader(storage)
    repository = SqlAlchemyTenderEvidenceRepository(
        SessionLocal,
        body_reader=reader,
    )
    matches = repository.search(
        case_id=project_uuid,
        query="保证金",
        top_k=5,
    )
    assert len(matches) == 1
    assert matches[0].content == "投标保证金为人民币20万元。"
    assert storage.get_calls == 1

    context = repository.get_context(
        case_id=project_uuid,
        evidence_id=matches[0].evidence_id,
        before_blocks=1,
        after_blocks=0,
    )
    assert [item.content for item in context] == [
        "投标截止时间为2026年8月15日14时。",
        "投标保证金为人民币20万元。",
    ]
    assert storage.get_calls == 1


def test_body_reader_rejects_tampered_minio_package(client):
    del client
    storage = MemoryEvidenceBodyStorage()
    db = SessionLocal()
    try:
        project, source = _seed_source(db)
        result = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=source.file_uuid,
            body_storage=storage,
            externalize_body=True,
        )
        db.commit()
        document = (
            db.query(BidEvidenceDocument)
            .filter(
                BidEvidenceDocument.evidence_document_uuid
                == result.evidence_document_uuid
            )
            .one()
        )
        block = (
            db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.document_id == document.id)
            .first()
        )
        object_key = (document.body_bucket, document.body_object_name)
        storage.objects[object_key] += b" "
        with pytest.raises(
            TenderEvidenceBodyIntegrityError,
            match="SHA-256",
        ):
            TenderEvidenceBodyReader(storage).read(
                document=document,
                block=block,
            )
    finally:
        db.close()
