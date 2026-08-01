from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject, BidProjectFile  # noqa: E402
from app.models.tender_evidence import (  # noqa: E402
    BidEvidenceBlock,
    BidEvidenceDocument,
)
from app.services.tender_evidence_body_storage import (  # noqa: E402
    BODY_STORAGE_BACKEND_MINIO,
    MinioTenderEvidenceBodyStorage,
    TenderEvidenceBodyReader,
    build_evidence_body_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy legacy MySQL tender evidence bodies into immutable MinIO "
            "packages. Dry-run is the default."
        )
    )
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read and hash-check every selected MinIO-backed evidence block.",
    )
    parser.add_argument("--purge-mysql-content", action="store_true")
    parser.add_argument("--project-uuid")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.purge_mysql_content and not args.run:
        parser.error("--purge-mysql-content requires --run")
    if args.verify and args.run:
        parser.error("--verify and --run are mutually exclusive")

    db = SessionLocal()
    try:
        if args.verify:
            return _verify_externalized_documents(
                db,
                project_uuid=args.project_uuid,
                limit=args.limit,
            )
        backend_filter = (
            BidEvidenceDocument.body_storage_backend
            == BODY_STORAGE_BACKEND_MINIO
            if args.purge_mysql_content
            else BidEvidenceDocument.body_storage_backend
            != BODY_STORAGE_BACKEND_MINIO
        )
        query = (
            db.query(BidEvidenceDocument, BidProject, BidProjectFile)
            .join(BidProject, BidProject.id == BidEvidenceDocument.project_id)
            .join(
                BidProjectFile,
                BidProjectFile.id == BidEvidenceDocument.source_file_id,
            )
            .filter(backend_filter)
            .order_by(BidEvidenceDocument.id.asc())
        )
        if args.project_uuid:
            query = query.filter(
                BidProject.project_uuid == args.project_uuid.strip()
            )
        rows = query.limit(max(1, min(int(args.limit), 10_000))).all()
        block_count = 0
        mysql_character_count = 0
        for document, _project, _source in rows:
            blocks = (
                db.query(BidEvidenceBlock)
                .filter(BidEvidenceBlock.document_id == document.id)
                .all()
            )
            block_count += len(blocks)
            mysql_character_count += sum(
                len(item.content or "") for item in blocks
            )
        summary = {
            "mode": "run" if args.run else "dry_run",
            "candidate_documents": len(rows),
            "candidate_blocks": block_count,
            "mysql_body_characters": mysql_character_count,
            "purge_mysql_content": bool(args.purge_mysql_content),
        }
        if not args.run:
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        storage = MinioTenderEvidenceBodyStorage()
        reader = TenderEvidenceBodyReader(storage)
        migrated = 0
        purged_blocks = 0
        for document, project, source in rows:
            blocks = (
                db.query(BidEvidenceBlock)
                .filter(BidEvidenceBlock.document_id == document.id)
                .order_by(BidEvidenceBlock.block_order.asc())
                .all()
            )
            if not blocks:
                raise RuntimeError(
                    "evidence document has no blocks: "
                    f"{document.evidence_document_uuid}"
                )
            if args.purge_mysql_content:
                for block in blocks:
                    reader.read(document=document, block=block)
                    if block.content is not None:
                        block.content_length = len(block.content)
                        block.content = None
                        purged_blocks += 1
                source.extracted_text = None
                source.segments_json = None
                db.commit()
                continue
            if any(item.content is None for item in blocks):
                raise RuntimeError(
                    "legacy document has incomplete MySQL content: "
                    f"{document.evidence_document_uuid}"
                )
            package_bytes, package_sha256 = build_evidence_body_package(
                case_id=project.project_uuid,
                document_id=document.evidence_document_uuid,
                document_key=document.document_key,
                document_version=document.version_no,
                source_file_uuid=source.file_uuid,
                source_sha256=document.sha256,
                parser_version=document.parser_version,
                extracted_text=source.extracted_text or "",
                source_segments=_load_json_list(source.segments_json),
                blocks=[
                    {
                        "evidence_id": item.evidence_id,
                        "block_id": item.block_id,
                        "block_order": item.block_order,
                        "content_hash": item.content_hash,
                        "content": item.content,
                        "locator": _load_json_dict(item.locator_json),
                        "keywords": _load_json_list(item.keywords_json),
                    }
                    for item in blocks
                ],
            )
            stored = storage.put(
                case_id=project.project_uuid,
                document_id=document.evidence_document_uuid,
                content=package_bytes,
                sha256=package_sha256,
            )
            document.body_storage_backend = stored.backend
            document.body_bucket = stored.bucket
            document.body_object_name = stored.object_name
            document.body_sha256 = stored.sha256
            document.body_size_bytes = stored.size_bytes
            document.body_schema_version = stored.schema_version
            source.parsed_artifact_bucket = stored.bucket
            source.parsed_artifact_object_name = stored.object_name
            source.parsed_artifact_sha256 = stored.sha256
            source.parsed_artifact_size_bytes = stored.size_bytes
            source.parsed_artifact_schema_version = stored.schema_version
            db.commit()
            migrated += 1

        summary.update(
            {
                "migrated_documents": migrated,
                "purged_mysql_blocks": purged_blocks,
            }
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _load_json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _load_json_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _verify_externalized_documents(
    db,
    *,
    project_uuid: str | None,
    limit: int,
) -> int:
    query = (
        db.query(BidEvidenceDocument, BidProject)
        .join(BidProject, BidProject.id == BidEvidenceDocument.project_id)
        .filter(
            BidEvidenceDocument.body_storage_backend
            == BODY_STORAGE_BACKEND_MINIO
        )
        .order_by(BidEvidenceDocument.id.asc())
    )
    if project_uuid:
        query = query.filter(BidProject.project_uuid == project_uuid.strip())
    documents = query.limit(max(1, min(int(limit), 10_000))).all()
    reader = TenderEvidenceBodyReader()
    verified_blocks = 0
    mysql_blocks_with_content = 0
    for document, _project in documents:
        blocks = (
            db.query(BidEvidenceBlock)
            .filter(BidEvidenceBlock.document_id == document.id)
            .order_by(BidEvidenceBlock.block_order.asc())
            .all()
        )
        for block in blocks:
            reader.read(document=document, block=block)
            verified_blocks += 1
            if block.content is not None:
                mysql_blocks_with_content += 1
    print(
        json.dumps(
            {
                "mode": "verify",
                "verified_documents": len(documents),
                "verified_blocks": verified_blocks,
                "mysql_blocks_with_content": mysql_blocks_with_content,
                "integrity": "ok",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
