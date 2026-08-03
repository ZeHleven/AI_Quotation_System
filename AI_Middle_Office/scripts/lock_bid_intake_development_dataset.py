from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.retrieval_evaluation import (  # noqa: E402
    dataset_fingerprint,
    load_eval_cases,
)
from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject  # noqa: E402
from app.services.bidding_parser import dumps_json, loads_json  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Lock one reviewed bid-intake Development dataset against a "
            "pre-baseline freeze contract."
        )
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--freeze-contract", required=True)
    parser.add_argument("--project-uuid", required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def _verify_snapshot_file(
    *,
    relative_path: str,
    expected_sha256: str,
) -> None:
    path = _project_path(relative_path)
    if not path.is_file():
        raise RuntimeError(f"frozen snapshot file is missing: {path}")
    actual_sha256 = _sha256(path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            "frozen snapshot SHA256 mismatch: "
            f"{relative_path}; expected={expected_sha256}; "
            f"actual={actual_sha256}"
        )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    dataset_path = Path(args.dataset).resolve()
    freeze_path = Path(args.freeze_contract).resolve()
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))

    if freeze.get("status") != "frozen_prebaseline":
        raise RuntimeError("freeze contract is not frozen_prebaseline")
    project_snapshot = freeze.get("project_snapshot") or {}
    if project_snapshot.get("project_uuid") != args.project_uuid:
        raise RuntimeError("freeze contract project_uuid mismatch")

    dataset_snapshot = freeze.get("dataset") or {}
    cases = load_eval_cases(dataset_path)
    actual_file_sha256 = _sha256(dataset_path)
    actual_fingerprint = dataset_fingerprint(cases)
    if actual_file_sha256 != dataset_snapshot.get("file_sha256"):
        raise RuntimeError("dataset file SHA256 does not match freeze")
    if actual_fingerprint != dataset_snapshot.get("dataset_fingerprint"):
        raise RuntimeError("dataset fingerprint does not match freeze")
    if len(cases) != dataset_snapshot.get("case_count"):
        raise RuntimeError("dataset case count does not match freeze")
    if any(item.case_id != args.project_uuid for item in cases):
        raise RuntimeError("dataset contains a different project case_id")
    if any(item.annotation_status != "approved" for item in cases):
        raise RuntimeError("dataset contains an unapproved case")

    source_snapshot = freeze.get("source_snapshot") or {}
    _verify_snapshot_file(
        relative_path=source_snapshot["manifest_path"],
        expected_sha256=source_snapshot["manifest_file_sha256"],
    )
    _verify_snapshot_file(
        relative_path=source_snapshot["review_path"],
        expected_sha256=source_snapshot["review_file_sha256"],
    )
    for relative_path, expected_sha256 in (
        (freeze.get("code_snapshot") or {}).get("files") or {}
    ).items():
        _verify_snapshot_file(
            relative_path=relative_path,
            expected_sha256=expected_sha256,
        )

    db = SessionLocal()
    try:
        project = (
            db.query(BidProject)
            .filter(BidProject.project_uuid == args.project_uuid)
            .one_or_none()
        )
        if project is None:
            raise RuntimeError("bid project does not exist")
        summary = loads_json(project.summary_json, {})
        existing_freeze_id = summary.get("dataset_freeze_id")
        if (
            summary.get("dataset_split_locked")
            and existing_freeze_id
            and existing_freeze_id != freeze["freeze_id"]
        ):
            raise RuntimeError(
                "project is already locked by a different freeze contract"
            )
        summary.update(
            {
                "dataset_split_locked": True,
                "dataset_frozen_at": freeze["frozen_at"],
                "dataset_fingerprint": actual_fingerprint,
                "dataset_file_sha256": actual_file_sha256,
                "dataset_freeze_id": freeze["freeze_id"],
                "dataset_freeze_contract": str(
                    freeze_path.relative_to(PROJECT_DIR)
                ).replace("\\", "/"),
                "business_review_status": "approved",
            }
        )
        project.summary_json = dumps_json(summary)
        db.commit()
        db.refresh(project)
    finally:
        db.close()

    print(
        json.dumps(
            {
                "schema_version": (
                    "bid_intake_development_dataset_lock_v1"
                ),
                "project_uuid": args.project_uuid,
                "freeze_id": freeze["freeze_id"],
                "dataset_split_locked": True,
                "case_count": len(cases),
                "dataset_fingerprint": actual_fingerprint,
                "dataset_file_sha256": actual_file_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
