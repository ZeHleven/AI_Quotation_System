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


DEFAULT_PROJECT_CODE = "RET-GRAPH-EXPAND-001-DEV-B"
DEFAULT_PROJECT_NAME = "陵水福朋喜来登酒店项目样板间精装修工程"
SOURCE_DEFINITIONS = (
    {
        "document_key": "dev-b-lingshui-tender-document",
        "relative_path": (
            "source/"
            "1、陵水福朋喜来登酒店项目样板间精装修工程"
            "——招标文件.docx"
        ),
        "original_filename": (
            "1、陵水福朋喜来登酒店项目样板间精装修工程"
            "——招标文件.docx"
        ),
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "source_format": "docx",
        "converted_from": None,
    },
    {
        "document_key": "dev-b-lingshui-bill-of-quantities",
        "relative_path": (
            "source/"
            "2、陵水福朋喜来登酒店项目样板间精装修工程"
            "——招标清单.xlsx"
        ),
        "original_filename": (
            "2、陵水福朋喜来登酒店项目样板间精装修工程"
            "——招标清单.xlsx"
        ),
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        "source_format": "xlsx",
        "converted_from": None,
    },
    {
        "document_key": "dev-b-lingshui-construction-contract",
        "relative_path": (
            "converted/"
            "陵水福朋喜来登酒店项目样板间精装修工程施工合同.docx"
        ),
        "original_filename": (
            "陵水福朋喜来登酒店项目样板间精装修工程施工合同.docx"
        ),
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "source_format": "docx",
        "converted_from": (
            "source/"
            "陵水福朋喜来登酒店项目样板间精装修工程施工合同.doc"
        ),
    },
    {
        "document_key": "dev-b-lingshui-clarifications",
        "relative_path": "converted/投标疑问(1).xlsx",
        "original_filename": "投标疑问(1).xlsx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        "source_format": "xlsx",
        "converted_from": "source/投标疑问(1).xls",
    },
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently register the approved Lingshui Sheraton "
            "Development B project and ingest its four source roles."
        )
    )
    parser.add_argument(
        "--staging-root",
        required=True,
        help=(
            "Workspace staging directory containing source/ and "
            "converted/."
        ),
    )
    parser.add_argument("--project-code", default=DEFAULT_PROJECT_CODE)
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--owner-username", default="admin")
    parser.add_argument(
        "--only-document-key",
        choices=[
            item["document_key"]
            for item in SOURCE_DEFINITIONS
        ],
        help="Reparse only one registered source.",
    )
    return parser.parse_args()


def _resolve_sources(
    staging_root: Path,
) -> list[tuple[dict[str, str | None], Path]]:
    if not staging_root.is_dir():
        raise RuntimeError(
            f"staging root does not exist: {staging_root}"
        )
    resolved: list[tuple[dict[str, str | None], Path]] = []
    for definition in SOURCE_DEFINITIONS:
        source_path = (
            staging_root / str(definition["relative_path"])
        ).resolve()
        if not source_path.is_file():
            raise RuntimeError(
                "required Development B source does not exist: "
                f"{source_path}"
            )
        resolved.append((definition, source_path))
    return resolved


def _find_project(
    db,
    *,
    project_code: str,
) -> BidProject | None:
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
        tenderer_name="待资料确认",
        project_location="海南省陵水黎族自治县",
        project_type="酒店样板间精装修工程",
        status="draft",
        owner_user_id=owner.id,
        created_by=owner.id,
        summary_json=dumps_json(
            {
                "biz_stage": "BIZ-4a",
                "mvp": True,
                "dataset_case_code": project_code,
                "dataset_role": "development_b",
                "dataset_split_locked": False,
                "business_review_status": "approved",
                "business_reviewed_at": "2026-07-30",
                "source": "manager_provided",
                "experiment_id": "RET-GRAPH-EXPAND-001",
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
        content = source_path.read_bytes()
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
                content=content,
                original_filename=str(
                    definition["original_filename"]
                ),
                content_type=str(definition["content_type"]),
                file_type="auto",
                document_key=str(definition["document_key"]),
                current_user=current_owner,
            )
        finally:
            db.close()
        result = run_tender_parse_job(creation.job_uuid)
        job_results.append(
            {
                "document_key": definition["document_key"],
                "source_path": str(source_path),
                "source_format": definition["source_format"],
                "converted_from": definition["converted_from"],
                "job_uuid": result.job_uuid,
                "status": result.status,
                "stage": result.stage,
                "evidence_document_uuid": (
                    result.evidence_document_uuid
                ),
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
                    "bid_intake_development_b_registration_v1"
                ),
                "project_code": args.project_code,
                "project_uuid": project_uuid,
                "project_created": project_created,
                "parser_version": BIDDING_PARSER_VERSION,
                "completed": completed,
                "dataset_split_locked": False,
                "jobs": job_results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
