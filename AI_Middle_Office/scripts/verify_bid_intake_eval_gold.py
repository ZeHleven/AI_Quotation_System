from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from sqlalchemy import func  # noqa: E402

from app.agents.bid_intake.retrieval_evaluation import (  # noqa: E402
    build_dataset_quality_report,
    dataset_fingerprint,
    load_eval_cases,
)
from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject  # noqa: E402
from app.models.tender_evidence import (  # noqa: E402
    BidEvidenceBlock,
    BidEvidenceDocument,
    BidEvidenceManifest,
)
from app.models.tender_evidence_index import (  # noqa: E402
    BidEvidenceIndexJob,
)
from app.services.tender_evidence_body_storage import (  # noqa: E402
    TenderEvidenceBodyReader,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that every private retrieval Gold evidence ID belongs "
            "to the active scoped manifest and contains its annotated "
            "required fragments."
        )
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--case-id",
        help=(
            "Optionally verify only one project inside a multi-project "
            "Development pool."
        ),
    )
    parser.add_argument(
        "--require-index-completed",
        action="store_true",
    )
    return parser.parse_args()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    dataset_path = Path(args.dataset).resolve()
    cases = load_eval_cases(dataset_path)
    if args.case_id:
        cases = [
            item for item in cases if item.case_id == args.case_id
        ]
        if not cases:
            raise RuntimeError(
                f"dataset does not contain case_id: {args.case_id}"
            )
    case_ids = {item.case_id for item in cases}
    if len(case_ids) != 1:
        raise RuntimeError(
            "Gold verification requires exactly one scoped case_id"
        )
    case_id = next(iter(case_ids))
    expected_by_id = {
        evidence.evidence_id: tuple(
            evidence.required_text_fragments
        )
        for case in cases
        for evidence in case.gold_evidence
    }

    db = SessionLocal()
    reader = TenderEvidenceBodyReader()
    try:
        project = (
            db.query(BidProject)
            .filter(BidProject.project_uuid == case_id)
            .one_or_none()
        )
        if project is None:
            raise RuntimeError(
                f"dataset project does not exist: {case_id}"
            )
        active_documents = (
            db.query(BidEvidenceDocument)
            .filter(
                BidEvidenceDocument.project_id == project.id,
                BidEvidenceDocument.active.is_(True),
                BidEvidenceDocument.parse_status != "failed",
            )
            .order_by(BidEvidenceDocument.document_key.asc())
            .all()
        )
        active_document_ids = {item.id for item in active_documents}
        rows = (
            db.query(BidEvidenceBlock, BidEvidenceDocument)
            .join(
                BidEvidenceDocument,
                BidEvidenceDocument.id == BidEvidenceBlock.document_id,
            )
            .filter(
                BidEvidenceBlock.project_id == project.id,
                BidEvidenceBlock.evidence_id.in_(
                    list(expected_by_id)
                ),
            )
            .all()
        )
        by_evidence_id = {
            block.evidence_id: (block, document)
            for block, document in rows
        }
        missing_evidence_ids = sorted(
            set(expected_by_id) - set(by_evidence_id)
        )
        inactive_evidence_ids = sorted(
            evidence_id
            for evidence_id, (_, document) in by_evidence_id.items()
            if document.id not in active_document_ids
        )
        fragment_errors = []
        for evidence_id, fragments in expected_by_id.items():
            stored = by_evidence_id.get(evidence_id)
            if stored is None:
                continue
            block, document = stored
            compact_content = _compact(
                reader.read(document=document, block=block)
            )
            for fragment in fragments:
                if _compact(fragment) not in compact_content:
                    fragment_errors.append(
                        {
                            "evidence_id": evidence_id,
                            "required_fragment": fragment,
                            "document_key": document.document_key,
                            "block_order": block.block_order,
                        }
                    )

        manifest = (
            db.query(BidEvidenceManifest)
            .filter(
                BidEvidenceManifest.project_id == project.id,
                BidEvidenceManifest.active.is_(True),
            )
            .order_by(BidEvidenceManifest.version_no.desc())
            .first()
        )
        if manifest is None:
            raise RuntimeError(
                "dataset project has no active evidence manifest"
            )
        index_job = (
            db.query(BidEvidenceIndexJob)
            .filter(
                BidEvidenceIndexJob.project_id == project.id,
                BidEvidenceIndexJob.manifest_id == manifest.id,
            )
            .order_by(BidEvidenceIndexJob.id.desc())
            .first()
        )
        active_block_count = int(
            db.query(func.count(BidEvidenceBlock.id))
            .join(
                BidEvidenceDocument,
                BidEvidenceDocument.id
                == BidEvidenceBlock.document_id,
            )
            .filter(
                BidEvidenceBlock.project_id == project.id,
                BidEvidenceDocument.active.is_(True),
                BidEvidenceDocument.parse_status != "failed",
            )
            .scalar()
            or 0
        )
        document_payload = [
            {
                "document_key": item.document_key,
                "document_type": item.document_type,
                "document_version": item.version_no,
                "sha256": item.sha256,
                "parser_version": item.parser_version,
            }
            for item in active_documents
        ]
    finally:
        db.close()

    quality = build_dataset_quality_report(cases)
    index_completed = bool(
        index_job
        and index_job.status == "completed"
        and index_job.indexed_block_count
        == index_job.requested_block_count
        == active_block_count
    )
    failures = {
        "missing_evidence_ids": missing_evidence_ids,
        "inactive_evidence_ids": inactive_evidence_ids,
        "fragment_errors": fragment_errors,
    }
    evidence_ok = not any(failures.values())
    index_ok = (
        index_completed
        if args.require_index_completed
        else index_job is not None
    )
    payload = {
        "schema_version": "bid_intake_eval_gold_verification_v1",
        "ok": bool(
            quality.get("runnable")
            and evidence_ok
            and index_ok
        ),
        "dataset_path": str(dataset_path),
        "dataset_file_sha256": hashlib.sha256(
            dataset_path.read_bytes()
        ).hexdigest(),
        "dataset_fingerprint": dataset_fingerprint(cases),
        "dataset_quality": quality,
        "case_id": case_id,
        "case_count": len(cases),
        "gold_evidence_reference_count": sum(
            len(item.gold_evidence) for item in cases
        ),
        "unique_gold_evidence_count": len(expected_by_id),
        "project": {
            "project_id": project.id,
            "project_uuid": project.project_uuid,
            "project_name": project.project_name,
            "status": project.status,
        },
        "active_manifest": {
            "version": manifest.version_no,
            "hash": manifest.manifest_hash,
            "active_document_count": len(active_documents),
            "active_block_count": active_block_count,
            "document_type_counts": dict(
                sorted(
                    Counter(
                        item.document_type
                        for item in active_documents
                    ).items()
                )
            ),
            "documents": document_payload,
        },
        "index": (
            {
                "job_uuid": index_job.job_uuid,
                "status": index_job.status,
                "stage": index_job.stage,
                "requested_block_count": (
                    index_job.requested_block_count
                ),
                "indexed_block_count": (
                    index_job.indexed_block_count
                ),
                "error_code": index_job.error_code,
                "completed_and_current": index_completed,
            }
            if index_job is not None
            else None
        ),
        "evidence_verification": {
            "ok": evidence_ok,
            **failures,
        },
    }
    print(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
