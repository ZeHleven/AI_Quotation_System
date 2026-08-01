from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.bidding_parser import (  # noqa: E402
    BIDDING_PARSER_VERSION,
    dumps_json,
    loads_json,
)
from app.services.tender_parse_pipeline import (  # noqa: E402
    create_tender_parse_job,
    run_tender_parse_job,
)


DEFAULT_PROJECT_CODE = "RET-GRAPH-HOLDOUT-001"
DEFAULT_PROJECT_NAME = "惠州未来花园项目三期19及21号楼室内精装修工程"
SOURCE_DEFINITIONS = (
    {
        "document_key": "holdout-future-garden-tender-document",
        "relative_path": "tender.docx",
        "original_filename": "惠州未来花园项目三期19及21号楼室内精装修工程招标文件.docx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "file_type": "tender_document",
        "source_format": "docx",
    },
    {
        "document_key": "holdout-future-garden-contract",
        "relative_path": "contract.docx",
        "original_filename": (
            "第三版-附件1-合同条款及格式-建设工程施工两方分包合同（2022版).docx"
        ),
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "file_type": "contract",
        "source_format": "docx",
    },
    {
        "document_key": "holdout-future-garden-boq-19",
        "relative_path": "boq_19.xlsm",
        "original_filename": "惠州未来花园【立新路】三期19栋精装清单0606.xlsm",
        "content_type": "application/vnd.ms-excel.sheet.macroEnabled.12",
        "file_type": "bill_of_quantities",
        "source_format": "xlsm",
    },
    {
        "document_key": "holdout-future-garden-boq-21",
        "relative_path": "boq_21.xlsm",
        "original_filename": "惠州未来花园【立新路】三期21栋精装清单0606.xlsm",
        "content_type": "application/vnd.ms-excel.sheet.macroEnabled.12",
        "file_type": "bill_of_quantities",
        "source_format": "xlsm",
    },
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently register the Future Garden Holdout candidate "
            "without freezing or running retrieval evaluation."
        )
    )
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--project-code", default=DEFAULT_PROJECT_CODE)
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--owner-username", default="admin")
    parser.add_argument(
        "--only-document-key",
        choices=[item["document_key"] for item in SOURCE_DEFINITIONS],
    )
    return parser.parse_args()


def _resolve_sources(
    staging_root: Path,
) -> list[tuple[dict[str, str], Path]]:
    if not staging_root.is_dir():
        raise RuntimeError(f"staging root does not exist: {staging_root}")
    resolved: list[tuple[dict[str, str], Path]] = []
    for definition in SOURCE_DEFINITIONS:
        source_path = (
            staging_root / definition["relative_path"]
        ).resolve()
        if not source_path.is_file():
            raise RuntimeError(
                f"required Holdout source does not exist: {source_path}"
            )
        resolved.append((definition, source_path))
    return resolved


def _find_project(db, *, project_code: str) -> BidProject | None:
    for project in db.query(BidProject).order_by(BidProject.id.asc()):
        summary = loads_json(project.summary_json, {})
        if summary.get("dataset_case_code") == project_code:
            return project
    return None


def _get_or_create_project(
    db,
    *,
    project_code: str,
    project_name: str,
    owner: User,
) -> tuple[BidProject, bool]:
    project = _find_project(db, project_code=project_code)
    if project is not None:
        return project, False
    project = BidProject(
        project_uuid=str(uuid4()),
        project_name=project_name,
        tenderer_name="惠州市昭乐房地产有限公司",
        project_location="广东省惠州市惠城区马安镇",
        project_type="住宅室内精装修工程",
        status="draft",
        owner_user_id=owner.id,
        created_by=owner.id,
        summary_json=dumps_json(
            {
                "biz_stage": "BIZ-4a",
                "mvp": True,
                "dataset_case_code": project_code,
                "dataset_role": "holdout_candidate",
                "dataset_split_locked": False,
                "business_review_status": "awaiting_user_review",
                "source": "manager_provided",
                "experiment_id": "RET-GRAPH-EXPAND-003-HOLDOUT",
                "formal_agent_run_count": 0,
            }
        ),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project, True


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    staging_root = Path(args.staging_root).resolve()
    sources = _resolve_sources(staging_root)
    if args.only_document_key:
        sources = [
            item
            for item in sources
            if item[0]["document_key"] == args.only_document_key
        ]

    db = SessionLocal()
    try:
        owner = (
            db.query(User)
            .filter(
                User.username == args.owner_username,
                User.is_active.is_(True),
            )
            .one_or_none()
        )
        if owner is None:
            raise RuntimeError(
                f"active owner not found: {args.owner_username}"
            )
        project, project_created = _get_or_create_project(
            db,
            project_code=args.project_code.strip(),
            project_name=args.project_name.strip(),
            owner=owner,
        )
        owner_id = owner.id
        project_uuid = project.project_uuid
    finally:
        db.close()

    job_results = []
    for definition, source_path in sources:
        db = SessionLocal()
        try:
            current_owner = (
                db.query(User)
                .filter(User.id == owner_id)
                .one()
            )
            creation = create_tender_parse_job(
                db,
                project_uuid=project_uuid,
                content=source_path.read_bytes(),
                original_filename=definition["original_filename"],
                content_type=definition["content_type"],
                file_type=definition["file_type"],
                document_key=definition["document_key"],
                current_user=current_owner,
            )
        finally:
            db.close()
        result = run_tender_parse_job(creation.job_uuid)
        job_results.append(
            {
                "document_key": definition["document_key"],
                "document_role": definition["file_type"],
                "source_path": str(source_path),
                "source_format": definition["source_format"],
                "job_uuid": result.job_uuid,
                "status": result.status,
                "stage": result.stage,
                "evidence_document_uuid": result.evidence_document_uuid,
                "error_code": result.error_code,
                "idempotent": creation.idempotent,
            }
        )

    completed = all(
        item["status"] == "completed" for item in job_results
    )
    db = SessionLocal()
    try:
        project = (
            db.query(BidProject)
            .filter(BidProject.project_uuid == project_uuid)
            .one()
        )
        if completed:
            project.status = "parsed"
            db.commit()
    finally:
        db.close()

    print(
        json.dumps(
            {
                "schema_version": (
                    "bid_intake_holdout_candidate_registration_v1"
                ),
                "project_code": args.project_code,
                "project_uuid": project_uuid,
                "project_created": project_created,
                "parser_version": BIDDING_PARSER_VERSION,
                "completed": completed,
                "dataset_split_locked": False,
                "formal_agent_run_count": 0,
                "jobs": job_results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
