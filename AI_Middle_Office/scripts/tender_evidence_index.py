from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from sqlalchemy import func  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.bidding import BidProject  # noqa: E402
from app.models.tender_evidence import (  # noqa: E402
    BidEvidenceBlock,
    BidEvidenceDocument,
    BidEvidenceManifest,
)
from app.models.tender_evidence_index import BidEvidenceIndexJob  # noqa: E402
from app.services.tender_evidence_indexing import (  # noqa: E402
    ensure_evidence_index_job,
    run_tender_evidence_index_job,
    serialize_evidence_index_job,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Queue or execute one tender evidence hybrid index job."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--project-uuid")
    selection.add_argument("--job-uuid")
    selection.add_argument(
        "--all-active",
        action="store_true",
        help="Select the active evidence manifest of every bid project.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute the selected job immediately using configured HTTP RAG.",
    )
    args = parser.parse_args()
    db = SessionLocal()
    try:
        selected_jobs: list[BidEvidenceIndexJob] = []
        if args.job_uuid:
            job = (
                db.query(BidEvidenceIndexJob)
                .filter(BidEvidenceIndexJob.job_uuid == args.job_uuid)
                .one_or_none()
            )
            if job is None:
                raise SystemExit("evidence index job does not exist")
            selected_jobs.append(job)
        elif args.all_active:
            active_rows = (
                db.query(BidProject, BidEvidenceManifest)
                .join(
                    BidEvidenceManifest,
                    BidEvidenceManifest.project_id == BidProject.id,
                )
                .filter(BidEvidenceManifest.active.is_(True))
                .order_by(BidProject.id.asc())
                .all()
            )
            for project, manifest in active_rows:
                block_count = _active_block_count(db, project.id)
                selected_jobs.append(
                    ensure_evidence_index_job(
                        db,
                        project_id=project.id,
                        manifest=manifest,
                        requested_block_count=block_count,
                        created_by=manifest.created_by,
                    )
                )
            db.commit()
        else:
            project = (
                db.query(BidProject)
                .filter(BidProject.project_uuid == args.project_uuid)
                .one_or_none()
            )
            if project is None:
                raise SystemExit("bid project does not exist")
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
                raise SystemExit("project has no active evidence manifest")
            block_count = _active_block_count(db, project.id)
            job = ensure_evidence_index_job(
                db,
                project_id=project.id,
                manifest=manifest,
                requested_block_count=int(block_count),
                created_by=manifest.created_by,
            )
            db.commit()
            selected_jobs.append(job)
        payloads = [
            serialize_evidence_index_job(item) for item in selected_jobs
        ]
        print(json.dumps(payloads, ensure_ascii=False))
        job_uuids = [item.job_uuid for item in selected_jobs]
    finally:
        db.close()

    if args.run:
        results = [
            run_tender_evidence_index_job(job_uuid)
            for job_uuid in job_uuids
        ]
        print(
            json.dumps(
                [
                    {
                        "job_uuid": item.job_uuid,
                        "status": item.status,
                        "stage": item.stage,
                        "attempt_count": item.attempt_count,
                        "indexed_block_count": item.indexed_block_count,
                        "error_code": item.error_code,
                    }
                    for item in results
                ],
                ensure_ascii=False,
            )
        )
        return 0 if all(
            item.status == "completed" for item in results
        ) else 1
    return 0


def _active_block_count(db, project_id: int) -> int:
    return int(
        db.query(func.count(BidEvidenceBlock.id))
        .join(
            BidEvidenceDocument,
            BidEvidenceDocument.id == BidEvidenceBlock.document_id,
        )
        .filter(
            BidEvidenceBlock.project_id == project_id,
            BidEvidenceDocument.project_id == project_id,
            BidEvidenceDocument.active.is_(True),
            BidEvidenceDocument.parse_status != "failed",
        )
        .scalar()
        or 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
