from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject  # noqa: E402
from app.services.bidding_parser import dumps_json, loads_json  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record one frozen bid-intake Holdout execution."
    )
    parser.add_argument("--project-uuid", required=True)
    parser.add_argument("--freeze-contract", required=True)
    parser.add_argument("--execution-record", required=True)
    return parser.parse_args()


def _project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    freeze_path = _project_path(args.freeze_contract)
    execution_path = _project_path(args.execution_record)
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    execution = json.loads(execution_path.read_text(encoding="utf-8"))

    if freeze.get("freeze_id") != execution.get("freeze_id"):
        raise RuntimeError("execution freeze_id does not match freeze contract")
    if (freeze.get("project_snapshot") or {}).get("project_uuid") != args.project_uuid:
        raise RuntimeError("freeze contract project_uuid mismatch")
    counts = execution.get("formal_execution_counts") or {}
    if counts.get("quality_comparison") != 1:
        raise RuntimeError("formal quality execution count must be exactly one")
    if counts.get("paired_latency") != 1:
        raise RuntimeError("formal paired latency execution count must be exactly one")
    if counts.get("rerun_performed") is not False:
        raise RuntimeError("execution record indicates a forbidden rerun")

    for artifact in (execution.get("artifacts") or {}).values():
        artifact_path = _project_path(artifact["path"])
        if not artifact_path.is_file():
            raise RuntimeError(f"execution artifact is missing: {artifact_path}")
        if _sha256(artifact_path) != artifact["sha256"]:
            raise RuntimeError(f"execution artifact SHA256 mismatch: {artifact_path}")

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
        if summary.get("dataset_freeze_id") != freeze["freeze_id"]:
            raise RuntimeError("project is not locked by this freeze contract")
        existing_execution_id = summary.get("holdout_execution_id")
        if existing_execution_id and existing_execution_id != execution["execution_id"]:
            raise RuntimeError("project already references a different Holdout execution")

        summary.update(
            {
                "holdout_execution_id": execution["execution_id"],
                "holdout_execution_status": execution["status"],
                "holdout_execution_record": str(
                    execution_path.relative_to(PROJECT_DIR)
                ).replace("\\", "/"),
                "holdout_execution_record_sha256": _sha256(execution_path),
                "holdout_completed_at": execution["executed_at"],
                "formal_quality_execution_count": 1,
                "formal_paired_latency_execution_count": 1,
                "formal_agent_run_count": counts.get("agent_run", 0),
                "holdout_quality_passed": (
                    execution["acceptance_evaluation"]["absolute_quality_passed"]
                ),
                "holdout_graph_mechanism_passed": (
                    execution["acceptance_evaluation"]["graph_mechanism_passed"]
                ),
                "holdout_paired_latency_passed": (
                    execution["acceptance_evaluation"]["paired_latency_passed"]
                ),
                "holdout_full_release_passed": (
                    execution["acceptance_evaluation"]["full_release_passed"]
                ),
                "production_enabled_from_holdout": False,
            }
        )
        project.summary_json = dumps_json(summary)
        db.commit()
    finally:
        db.close()

    print(
        json.dumps(
            {
                "schema_version": "bid_intake_holdout_execution_record_v1",
                "project_uuid": args.project_uuid,
                "freeze_id": freeze["freeze_id"],
                "execution_id": execution["execution_id"],
                "status": execution["status"],
                "formal_quality_execution_count": 1,
                "formal_paired_latency_execution_count": 1,
                "production_enabled_from_holdout": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
