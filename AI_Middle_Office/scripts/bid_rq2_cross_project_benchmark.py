from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.services.bid_retrieval_benchmark import (  # noqa: E402
    BASELINE_FUSION_PROFILE_VERSION,
    BENCHMARK_DEVELOPMENT_SNAPSHOT_SCHEMA_VERSION,
    BENCHMARK_FREEZE_SCHEMA_VERSION,
    BENCHMARK_LEDGER_SCHEMA_VERSION,
    BENCHMARK_PROFILE_VERSION,
    CANDIDATE_RERANK_PROFILE_VERSION,
    DEFAULT_ACCEPTANCE_THRESHOLDS,
    aggregate_project_reports,
    canonical_hash,
    file_sha256,
    materialize_project_silver_cases,
    validate_dataset,
    validate_development_snapshot,
    validate_development_holdout_isolation,
    validate_freeze,
    with_freeze_hash,
    with_development_snapshot_hash,
)


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_exclusive(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_atomic(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    temporary = target.with_name(f".{target.name}.pending")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _dataset_ref(path: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": summary["dataset_id"],
        "dataset_hash": summary["dataset_hash"],
        "file_sha256": file_sha256(path),
        "project_count": summary["project_count"],
        "case_count": summary["case_count"],
        "restricted_path": str(path),
    }


def _artifact_hashes(
    snapshot: Mapping[str, Any],
    *,
    base: Path,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for source_name, output_name in (
        ("code", "code_sha256"),
        ("contracts", "contract_sha256"),
        ("dependencies", "dependency_sha256"),
    ):
        values = snapshot.get(source_name)
        if not isinstance(values, list) or not values:
            raise ValueError(f"artifact snapshot requires non-empty {source_name}")
        hashes: dict[str, str] = {}
        for raw in values:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"invalid artifact path in {source_name}")
            path = _resolve(base, raw)
            hashes[raw] = file_sha256(path)
        result[output_name] = hashes
    return result


def _load_project_reports(manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _load(manifest_path)
    base = manifest_path.parent
    rows: list[dict[str, Any]] = []
    for item in manifest.get("projects") or []:
        if not isinstance(item, dict):
            raise ValueError("project report entry must be an object")
        rows.append(
            {
                "project_id": item["project_id"],
                "dataset_hash": item["dataset_hash"],
                "document_sha256": item["document_sha256"],
                "baseline_summary": _load(_resolve(base, item["baseline_summary_path"])),
                "candidate_summary": _load(_resolve(base, item["candidate_summary_path"])),
                "ab_summary": _load(_resolve(base, item["ab_summary_path"])),
            }
        )
    return manifest, rows


def _cmd_validate_dataset(args: argparse.Namespace) -> int:
    summary = validate_dataset(
        _load(args.dataset),
        expected_split=args.split,
        enforce_portfolio_minimums=not args.allow_incomplete,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_validate_isolation(args: argparse.Namespace) -> int:
    summary = validate_development_holdout_isolation(
        _load(args.development),
        _load(args.holdout),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_materialize_project(args: argparse.Namespace) -> int:
    payload = materialize_project_silver_cases(
        _load(args.dataset),
        project_id=args.project_id,
    )
    _write(args.output, payload)
    print(
        json.dumps(
            {
                "dataset_id": payload["dataset_id"],
                "document_sha256": payload["document_sha256"],
                "case_count": len(payload["cases"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_create_freeze(args: argparse.Namespace) -> int:
    development_path = Path(args.development).resolve()
    holdout_path = Path(args.holdout).resolve()
    development = _load(development_path)
    holdout = _load(holdout_path)
    development_summary = validate_dataset(development, expected_split="development")
    holdout_summary = validate_dataset(holdout, expected_split="holdout")
    validate_development_holdout_isolation(development, holdout)
    development_report_path = Path(args.development_report).resolve()
    development_report = _load(development_report_path)
    if development_report.get("gate", {}).get("status") != "passed":
        raise ValueError("development report gate must be passed before holdout freeze")
    if development_report.get("split") != "development" or development_report.get(
        "dataset_hash"
    ) != development_summary["dataset_hash"]:
        raise ValueError("development report does not bind the approved development dataset")
    development_snapshot = _load(args.development_snapshot)
    snapshot_summary = validate_development_snapshot(
        development_snapshot,
        development=development,
    )
    report_binding = development_report.get("evaluation_binding") or {}
    if report_binding.get("kind") != "development_snapshot" or report_binding.get(
        "hash"
    ) != snapshot_summary["binding_hash"]:
        raise ValueError("development report does not bind the frozen development snapshot")
    freeze = with_freeze_hash(
        {
            "schema_version": BENCHMARK_FREEZE_SCHEMA_VERSION,
            "profile_version": BENCHMARK_PROFILE_VERSION,
            "freeze_id": args.freeze_id,
            "status": "frozen_preholdout",
            "datasets": {
                "development": _dataset_ref(development_path, development_summary),
                "holdout": _dataset_ref(holdout_path, holdout_summary),
            },
            "retrieval_profiles": {
                "baseline": BASELINE_FUSION_PROFILE_VERSION,
                "candidate": CANDIDATE_RERANK_PROFILE_VERSION,
            },
            "acceptance_thresholds": dict(DEFAULT_ACCEPTANCE_THRESHOLDS),
            "development_acceptance": {
                "status": "passed",
                "report_sha256": file_sha256(development_report_path),
                "snapshot_hash": snapshot_summary["binding_hash"],
            },
            "holdout_execution": {
                "maximum_formal_execution_count": 1,
                "baseline_before_candidate": True,
                "rerun_after_result_forbidden": True,
                "result_may_not_change_thresholds": True,
            },
            "anti_leakage": {
                "project_level_split": True,
                "project_family_overlap_forbidden": True,
                "source_document_overlap_forbidden": True,
                "project_specific_query_hints_forbidden": True,
                "holdout_not_used_for_tuning": True,
                "holdout_failures_may_not_define_new_rules": True,
                "threshold_change_after_freeze_forbidden": True,
            },
            "frozen_artifacts": snapshot_summary["frozen_artifacts"],
        }
    )
    validate_freeze(
        freeze,
        development=development,
        holdout=holdout,
        development_snapshot=development_snapshot,
    )
    _write(args.output, freeze)
    print(json.dumps({"freeze_id": freeze["freeze_id"], "freeze_hash": freeze["freeze_hash"]}, indent=2))
    return 0


def _cmd_create_development_snapshot(args: argparse.Namespace) -> int:
    development_path = Path(args.development).resolve()
    development = _load(development_path)
    development_summary = validate_dataset(
        development,
        expected_split="development",
    )
    artifact_snapshot_path = Path(args.artifact_snapshot).resolve()
    artifact_snapshot = _load(artifact_snapshot_path)
    snapshot = with_development_snapshot_hash(
        {
            "schema_version": BENCHMARK_DEVELOPMENT_SNAPSHOT_SCHEMA_VERSION,
            "profile_version": BENCHMARK_PROFILE_VERSION,
            "snapshot_id": args.snapshot_id,
            "status": "frozen_predevelopment",
            "dataset": _dataset_ref(development_path, development_summary),
            "retrieval_profiles": {
                "baseline": BASELINE_FUSION_PROFILE_VERSION,
                "candidate": CANDIDATE_RERANK_PROFILE_VERSION,
            },
            "acceptance_thresholds": dict(DEFAULT_ACCEPTANCE_THRESHOLDS),
            "anti_leakage": {
                "project_specific_query_hints_forbidden": True,
                "threshold_change_after_snapshot_forbidden": True,
                "code_or_profile_change_requires_new_snapshot": True,
            },
            "frozen_artifacts": _artifact_hashes(
                artifact_snapshot,
                base=artifact_snapshot_path.parent,
            ),
        }
    )
    validate_development_snapshot(snapshot, development=development)
    _write(args.output, snapshot)
    print(
        json.dumps(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "snapshot_hash": snapshot["snapshot_hash"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_validate_freeze(args: argparse.Namespace) -> int:
    summary = validate_freeze(
        _load(args.freeze),
        development=_load(args.development) if args.development else None,
        holdout=_load(args.holdout) if args.holdout else None,
        development_snapshot=(
            _load(args.development_snapshot)
            if args.development_snapshot
            else None
        ),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_begin_holdout(args: argparse.Namespace) -> int:
    freeze = _load(args.freeze)
    development = _load(args.development)
    holdout = _load(args.holdout)
    freeze_summary = validate_freeze(
        freeze,
        development=development,
        holdout=holdout,
        development_snapshot=_load(args.development_snapshot),
    )
    holdout_summary = validate_dataset(holdout, expected_split="holdout")
    ledger = {
        "schema_version": BENCHMARK_LEDGER_SCHEMA_VERSION,
        "status": "started",
        "formal_execution_id": args.execution_id,
        "formal_execution_count": 1,
        "freeze_id": freeze_summary["freeze_id"],
        "freeze_hash": freeze_summary["freeze_hash"],
        "dataset_id": holdout_summary["dataset_id"],
        "dataset_hash": holdout_summary["dataset_hash"],
        "authorization_record": args.authorization_record,
        "baseline_before_candidate": True,
        "rerun_after_result_forbidden": True,
        "report_hash": None,
    }
    ledger["ledger_hash"] = canonical_hash(ledger)
    _write_exclusive(args.ledger, ledger)
    print(
        json.dumps(
            {
                "status": "started",
                "formal_execution_id": args.execution_id,
                "ledger_hash": ledger["ledger_hash"],
                "warning": "Do not start a second holdout execution for this freeze.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    dataset = _load(args.dataset)
    manifest_path = Path(args.report_manifest).resolve()
    manifest, project_reports = _load_project_reports(manifest_path)
    dataset_summary = validate_dataset(dataset)
    split = dataset_summary["split"]
    if split == "development":
        if not args.development_snapshot or args.freeze:
            raise ValueError("development aggregation requires only --development-snapshot")
        development_snapshot = _load(args.development_snapshot)
        binding_summary = validate_development_snapshot(
            development_snapshot,
            development=dataset,
        )
        freeze = None
        if manifest.get("formal_execution_count") != 0:
            raise ValueError("development report manifest execution_count must be 0")
    else:
        if not args.freeze or args.development_snapshot:
            raise ValueError("holdout aggregation requires only --freeze")
        freeze = _load(args.freeze)
        binding_summary = validate_freeze(freeze, holdout=dataset)
        development_snapshot = None
    if manifest.get("evaluation_binding_kind") != binding_summary["binding_kind"]:
        raise ValueError("report manifest evaluation binding kind mismatch")
    if manifest.get("evaluation_binding_hash") != binding_summary["binding_hash"]:
        raise ValueError("report manifest evaluation binding hash mismatch")
    if manifest.get("dataset_hash") != dataset_summary["dataset_hash"]:
        raise ValueError("report manifest dataset_hash mismatch")
    if manifest.get("split") != dataset_summary["split"]:
        raise ValueError("report manifest split mismatch")
    if split == "holdout":
        if manifest.get("formal_execution_count") != 1:
            raise ValueError("formal holdout aggregation requires execution_count=1")
        if not args.execution_ledger:
            raise ValueError("formal holdout aggregation requires --execution-ledger")
        ledger = _load(args.execution_ledger)
        expected_ledger_hash = ledger.get("ledger_hash")
        unhashed_ledger = dict(ledger)
        unhashed_ledger.pop("ledger_hash", None)
        if expected_ledger_hash != canonical_hash(unhashed_ledger):
            raise ValueError("holdout execution ledger hash mismatch")
        if ledger.get("schema_version") != BENCHMARK_LEDGER_SCHEMA_VERSION:
            raise ValueError("holdout execution ledger schema mismatch")
        if ledger.get("status") != "started":
            raise ValueError("holdout execution ledger is not in started state")
        if ledger.get("formal_execution_count") != 1:
            raise ValueError("holdout execution ledger count must remain 1")
        if ledger.get("formal_execution_id") != manifest.get("formal_execution_id"):
            raise ValueError("holdout execution_id mismatch")
        if ledger.get("freeze_hash") != freeze.get("freeze_hash"):
            raise ValueError("holdout ledger freeze_hash mismatch")
        if ledger.get("dataset_hash") != dataset_summary["dataset_hash"]:
            raise ValueError("holdout ledger dataset_hash mismatch")
    else:
        ledger = None
    report = aggregate_project_reports(
        dataset=dataset,
        project_reports=project_reports,
        freeze=freeze,
        development_snapshot=development_snapshot,
    )
    report["formal_execution_id"] = manifest.get("formal_execution_id")
    report["formal_execution_count"] = manifest.get("formal_execution_count")
    unhashed = dict(report)
    unhashed.pop("report_hash", None)
    report["report_hash"] = canonical_hash(unhashed)
    _write(args.output, report)
    if ledger is not None:
        completed_ledger = dict(ledger)
        completed_ledger["status"] = "completed"
        completed_ledger["report_hash"] = report["report_hash"]
        completed_ledger.pop("ledger_hash", None)
        completed_ledger["ledger_hash"] = canonical_hash(completed_ledger)
        _write_atomic(args.execution_ledger, completed_ledger)
    print(
        json.dumps(
            {
                "split": report["split"],
                "gate": report["gate"]["status"],
                "failed_checks": report["gate"]["failed_checks"],
                "report_hash": report["report_hash"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["gate"]["status"] == "passed" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RQ2 cross-project Gold/Holdout contract and aggregation runner",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate_dataset_parser = commands.add_parser("validate-dataset")
    validate_dataset_parser.add_argument("--dataset", required=True)
    validate_dataset_parser.add_argument("--split", choices=("development", "holdout"))
    validate_dataset_parser.add_argument("--allow-incomplete", action="store_true")
    validate_dataset_parser.set_defaults(handler=_cmd_validate_dataset)

    isolation_parser = commands.add_parser("validate-isolation")
    isolation_parser.add_argument("--development", required=True)
    isolation_parser.add_argument("--holdout", required=True)
    isolation_parser.set_defaults(handler=_cmd_validate_isolation)

    materialize_parser = commands.add_parser("materialize-project")
    materialize_parser.add_argument("--dataset", required=True)
    materialize_parser.add_argument("--project-id", required=True)
    materialize_parser.add_argument("--output", required=True)
    materialize_parser.set_defaults(handler=_cmd_materialize_project)

    create_freeze_parser = commands.add_parser("create-freeze")
    create_freeze_parser.add_argument("--freeze-id", required=True)
    create_freeze_parser.add_argument("--development", required=True)
    create_freeze_parser.add_argument("--holdout", required=True)
    create_freeze_parser.add_argument("--development-report", required=True)
    create_freeze_parser.add_argument("--development-snapshot", required=True)
    create_freeze_parser.add_argument("--output", required=True)
    create_freeze_parser.set_defaults(handler=_cmd_create_freeze)

    development_snapshot_parser = commands.add_parser("create-development-snapshot")
    development_snapshot_parser.add_argument("--snapshot-id", required=True)
    development_snapshot_parser.add_argument("--development", required=True)
    development_snapshot_parser.add_argument("--artifact-snapshot", required=True)
    development_snapshot_parser.add_argument("--output", required=True)
    development_snapshot_parser.set_defaults(handler=_cmd_create_development_snapshot)

    validate_freeze_parser = commands.add_parser("validate-freeze")
    validate_freeze_parser.add_argument("--freeze", required=True)
    validate_freeze_parser.add_argument("--development")
    validate_freeze_parser.add_argument("--holdout")
    validate_freeze_parser.add_argument("--development-snapshot")
    validate_freeze_parser.set_defaults(handler=_cmd_validate_freeze)

    begin_holdout_parser = commands.add_parser("begin-holdout")
    begin_holdout_parser.add_argument("--freeze", required=True)
    begin_holdout_parser.add_argument("--development", required=True)
    begin_holdout_parser.add_argument("--holdout", required=True)
    begin_holdout_parser.add_argument("--development-snapshot", required=True)
    begin_holdout_parser.add_argument("--execution-id", required=True)
    begin_holdout_parser.add_argument("--authorization-record", required=True)
    begin_holdout_parser.add_argument("--ledger", required=True)
    begin_holdout_parser.set_defaults(handler=_cmd_begin_holdout)

    aggregate_parser = commands.add_parser("aggregate")
    aggregate_parser.add_argument("--freeze")
    aggregate_parser.add_argument("--development-snapshot")
    aggregate_parser.add_argument("--dataset", required=True)
    aggregate_parser.add_argument("--report-manifest", required=True)
    aggregate_parser.add_argument("--execution-ledger")
    aggregate_parser.add_argument("--output", required=True)
    aggregate_parser.set_defaults(handler=_cmd_aggregate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
