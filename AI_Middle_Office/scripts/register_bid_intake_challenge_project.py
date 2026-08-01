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


DEFAULT_PROJECT_CODE = "DATA-CHALLENGE-001"
DEFAULT_PROJECT_NAME = "余干蓝城·明月江南二期公区精装修工程"
SOURCE_DEFINITIONS = (
    {
        "document_key": "data-challenge-001-tender-document",
        "name_contains": "招标文件",
        "suffix": ".docx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    },
    {
        "document_key": "data-challenge-001-construction-contract",
        "name_contains": "施工合同",
        "suffix": ".docx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    },
    {
        "document_key": "data-challenge-001-bill-of-quantities",
        "name_contains": "招标清单",
        "suffix": ".xlsx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    },
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently register one manager-provided project as a "
            "bid-intake Challenge dataset project and ingest its sources."
        )
    )
    parser.add_argument("--source-dir", required=True)
    parser.add_argument(
        "--project-code",
        default=DEFAULT_PROJECT_CODE,
    )
    parser.add_argument(
        "--project-name",
        default=DEFAULT_PROJECT_NAME,
    )
    parser.add_argument("--owner-username", default="admin")
    parser.add_argument(
        "--only-document-key",
        choices=[
            item["document_key"]
            for item in SOURCE_DEFINITIONS
        ],
        help=(
            "Reparse only one registered source, for example after a "
            "classifier-only parser upgrade."
        ),
    )
    return parser.parse_args()


def _find_sources(source_dir: Path) -> list[tuple[dict[str, str], Path]]:
    if not source_dir.is_dir():
        raise RuntimeError(f"source directory does not exist: {source_dir}")
    discovered: list[tuple[dict[str, str], Path]] = []
    for definition in SOURCE_DEFINITIONS:
        matches = [
            item
            for item in source_dir.iterdir()
            if item.is_file()
            and item.suffix.lower() == definition["suffix"]
            and definition["name_contains"] in item.name
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "expected exactly one source matching "
                f"{definition['name_contains']}{definition['suffix']}, "
                f"found {len(matches)}"
            )
        discovered.append((definition, matches[0]))
    return discovered


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
        tenderer_name="余干蓝城",
        project_location="江西省上饶市余干县",
        project_type="公区精装修工程",
        status="draft",
        owner_user_id=owner.id,
        created_by=owner.id,
        summary_json=dumps_json(
            {
                "biz_stage": "BIZ-4a",
                "mvp": True,
                "dataset_case_code": project_code,
                "dataset_role": "challenge",
                "dataset_split_locked": True,
                "source": "manager_provided",
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
    source_dir = Path(args.source_dir).resolve()
    sources = _find_sources(source_dir)
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
                original_filename=source_path.name,
                content_type=definition["content_type"],
                file_type="auto",
                document_key=definition["document_key"],
                current_user=current_owner,
            )
        finally:
            db.close()
        result = run_tender_parse_job(creation.job_uuid)
        job_results.append(
            {
                "document_key": definition["document_key"],
                "filename": source_path.name,
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
                    "bid_intake_challenge_registration_v1"
                ),
                "project_code": args.project_code,
                "project_uuid": project_uuid,
                "project_created": project_created,
                "parser_version": BIDDING_PARSER_VERSION,
                "completed": completed,
                "jobs": job_results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
