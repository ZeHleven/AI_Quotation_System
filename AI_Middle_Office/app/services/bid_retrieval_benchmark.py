from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence


BENCHMARK_DATASET_SCHEMA_VERSION = "bid.evidence.retrieval-benchmark-dataset.v1"
BENCHMARK_DEVELOPMENT_SNAPSHOT_SCHEMA_VERSION = (
    "bid.evidence.retrieval-benchmark-development-snapshot.v1"
)
BENCHMARK_FREEZE_SCHEMA_VERSION = "bid.evidence.retrieval-benchmark-freeze.v1"
BENCHMARK_REPORT_SCHEMA_VERSION = "bid.evidence.retrieval-benchmark-report.v1"
BENCHMARK_LEDGER_SCHEMA_VERSION = "bid.evidence.retrieval-benchmark-execution-ledger.v1"
BENCHMARK_PROFILE_VERSION = (
    "bid-evidence-retrieval-benchmark-profile-v1-rq2-closeout"
)

BASELINE_FUSION_PROFILE_VERSION = (
    "bid-evidence-candidate-fusion-profile-v1-rq2b"
)
CANDIDATE_RERANK_PROFILE_VERSION = "bid-evidence-rerank-profile-v1-rq2c-bce"

MIN_PROJECTS = {"development": 3, "holdout": 2}
MIN_CASES = {"development": 60, "holdout": 40}
MIN_CASES_PER_PROJECT = 20
MIN_CATEGORY_COUNT = 5

METRIC_NAMES = (
    "hit_at_5",
    "target_recall_at_5",
    "mrr_at_5",
    "ndcg_at_5",
    "hit_at_8",
    "target_recall_at_8",
    "mrr_at_8",
    "ndcg_at_8",
    "read_target_recall_at_5",
    "citable_target_availability",
)

DEFAULT_ACCEPTANCE_THRESHOLDS: dict[str, float | int] = {
    "macro_hit_at_5_min": 0.85,
    "macro_target_recall_at_5_min": 0.78,
    "macro_mrr_at_5_min": 0.55,
    "macro_ndcg_at_5_min": 0.60,
    "macro_hit_at_8_min": 0.92,
    "macro_target_recall_at_8_min": 0.85,
    "macro_read_target_recall_at_5_min": 0.75,
    "macro_citable_target_availability_min": 0.98,
    "worst_project_hit_at_5_min": 0.75,
    "worst_project_target_recall_at_5_min": 0.65,
    "macro_hit_at_5_delta_min": 0.0,
    "macro_target_recall_at_5_delta_min": 0.0,
    "macro_hit_at_8_delta_min": 0.0,
    "macro_target_recall_at_8_delta_min": 0.0,
    "macro_read_target_recall_at_5_delta_min": 0.0,
    "per_case_quality_regression_count_max": 0,
    "atom_only_violation_count_max": 0,
    "candidate_search_p95_ms_max": 4500,
    "paired_search_delta_p95_ms_max": 2500,
}

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class BenchmarkContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def with_dataset_hash(dataset: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(dataset)
    result.pop("dataset_hash", None)
    result["dataset_hash"] = canonical_hash(result)
    return result


def with_freeze_hash(freeze: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(freeze)
    result.pop("freeze_hash", None)
    result["freeze_hash"] = canonical_hash(result)
    return result


def with_development_snapshot_hash(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    result.pop("snapshot_hash", None)
    result["snapshot_hash"] = canonical_hash(result)
    return result


def validate_dataset(
    dataset: Mapping[str, Any],
    *,
    expected_split: str | None = None,
    enforce_portfolio_minimums: bool = True,
) -> dict[str, Any]:
    _require(
        dataset.get("schema_version") == BENCHMARK_DATASET_SCHEMA_VERSION,
        "BENCHMARK_DATASET_SCHEMA_INVALID",
        "unexpected dataset schema_version",
    )
    split = str(dataset.get("split") or "")
    _require(
        split in MIN_PROJECTS,
        "BENCHMARK_SPLIT_INVALID",
        "split must be development or holdout",
    )
    if expected_split is not None:
        _require(
            split == expected_split,
            "BENCHMARK_SPLIT_MISMATCH",
            f"expected {expected_split}, got {split}",
        )
    required_status = "gold_approved" if split == "development" else "holdout_sealed"
    _require(
        dataset.get("status") == required_status,
        "BENCHMARK_ANNOTATION_NOT_APPROVED",
        f"{split} dataset must be {required_status}",
    )
    if split == "holdout":
        _require(
            dataset.get("sealed") is True,
            "BENCHMARK_HOLDOUT_NOT_SEALED",
            "holdout dataset must be sealed before a formal run",
        )
        _require(
            dataset.get("maximum_formal_execution_count") == 1,
            "BENCHMARK_HOLDOUT_EXECUTION_POLICY_INVALID",
            "holdout permits exactly one formal execution",
        )

    projects = _mapping_list(dataset.get("projects"), "projects")
    cases = _mapping_list(dataset.get("cases"), "cases")
    project_ids: set[str] = set()
    project_family_hashes: set[str] = set()
    document_hashes: set[str] = set()
    project_case_counts: Counter[str] = Counter()
    project_document_hashes: dict[str, list[str]] = {}

    for project in projects:
        project_id = _non_empty(project.get("project_id"), "project_id")
        _require(
            project_id not in project_ids,
            "BENCHMARK_PROJECT_DUPLICATE",
            project_id,
        )
        project_ids.add(project_id)
        family_hash = _sha256(project.get("project_family_hash"), "project_family_hash")
        _require(
            family_hash not in project_family_hashes,
            "BENCHMARK_PROJECT_FAMILY_DUPLICATE",
            project_id,
        )
        project_family_hashes.add(family_hash)
        exposure = project.get("prior_rq_exposure")
        _require(
            exposure in {"none", "development"},
            "BENCHMARK_PROJECT_EXPOSURE_INVALID",
            project_id,
        )
        if split == "holdout":
            _require(
                exposure == "none",
                "BENCHMARK_HOLDOUT_PROJECT_PREVIOUSLY_EXPOSED",
                project_id,
            )
        documents = _mapping_list(project.get("source_documents"), "source_documents")
        _require(
            bool(documents),
            "BENCHMARK_SOURCE_DOCUMENT_MISSING",
            project_id,
        )
        _require(
            len(documents) == 1,
            "BENCHMARK_V1_DOCUMENT_CARDINALITY_INVALID",
            f"{project_id} v1 benchmark accepts exactly one primary tender PDF",
        )
        primary_count = 0
        hashes: list[str] = []
        for document in documents:
            digest = _sha256(document.get("sha256"), "source document sha256")
            _require(
                digest not in document_hashes,
                "BENCHMARK_SOURCE_DOCUMENT_DUPLICATE",
                digest,
            )
            document_hashes.add(digest)
            hashes.append(digest)
            if document.get("role") == "primary_tender":
                primary_count += 1
        _require(
            primary_count == 1,
            "BENCHMARK_PRIMARY_TENDER_CARDINALITY_INVALID",
            f"{project_id} requires exactly one primary_tender document",
        )
        project_document_hashes[project_id] = hashes

    case_ids: set[str] = set()
    question_hashes: set[str] = set()
    categories: Counter[str] = Counter()
    answer_status_counts: Counter[str] = Counter()
    target_count = 0
    for case in cases:
        case_id = _non_empty(case.get("case_id"), "case_id")
        _require(
            case_id not in case_ids,
            "BENCHMARK_CASE_DUPLICATE",
            case_id,
        )
        case_ids.add(case_id)
        project_id = _non_empty(case.get("project_id"), "case project_id")
        _require(
            project_id in project_ids,
            "BENCHMARK_CASE_PROJECT_UNKNOWN",
            f"{case_id}: {project_id}",
        )
        project_case_counts[project_id] += 1
        question = _non_empty(case.get("question"), "question")
        question_hash = canonical_hash(" ".join(question.casefold().split()))
        _require(
            question_hash not in question_hashes,
            "BENCHMARK_QUESTION_DUPLICATE",
            case_id,
        )
        question_hashes.add(question_hash)
        category = _non_empty(case.get("category"), "category")
        categories[category] += 1
        answer_status = case.get("answer_status")
        _require(
            answer_status in {"answerable", "insufficient"},
            "BENCHMARK_ANSWER_STATUS_INVALID",
            case_id,
        )
        answer_status_counts[str(answer_status)] += 1
        targets = _mapping_list(case.get("targets"), "targets")
        _require(
            bool(targets),
            "BENCHMARK_POSITIVE_CASE_WITHOUT_TARGETS",
            case_id,
        )
        target_ids: set[str] = set()
        for target in targets:
            target_id = _non_empty(target.get("target_id"), "target_id")
            _require(
                target_id not in target_ids,
                "BENCHMARK_TARGET_DUPLICATE",
                f"{case_id}: {target_id}",
            )
            target_ids.add(target_id)
            _require(
                target.get("evidence_role") == "evidence_atom",
                "BENCHMARK_TARGET_ROLE_INVALID",
                f"{case_id}: targets must resolve to evidence_atom",
            )
            fragments = target.get("required_text_fragments")
            _require(
                isinstance(fragments, list)
                and len(fragments) == 1
                and all(isinstance(item, str) and item.strip() for item in fragments),
                "BENCHMARK_TARGET_ANCHOR_INVALID",
                f"{case_id}: {target_id} requires exactly one phrase anchor in v1",
            )
            _require(
                target.get("match_policy") == "all_fragments_same_atom",
                "BENCHMARK_TARGET_MATCH_POLICY_INVALID",
                f"{case_id}: {target_id}",
            )
            pages = target.get("page_numbers")
            _require(
                isinstance(pages, list)
                and bool(pages)
                and all(isinstance(item, int) and item >= 1 for item in pages),
                "BENCHMARK_TARGET_PAGE_INVALID",
                f"{case_id}: {target_id}",
            )
        target_count += len(targets)
        annotation = case.get("annotation")
        _require(
            isinstance(annotation, Mapping),
            "BENCHMARK_ANNOTATION_INVALID",
            case_id,
        )
        _require(
            annotation.get("status") in {"approved", "adjudicated"},
            "BENCHMARK_ANNOTATION_NOT_APPROVED",
            case_id,
        )
        annotator_hash = _sha256(annotation.get("annotator_hash"), "annotator_hash")
        reviewer_hash = _sha256(annotation.get("reviewer_hash"), "reviewer_hash")
        _require(
            annotator_hash != reviewer_hash,
            "BENCHMARK_REVIEW_NOT_INDEPENDENT",
            case_id,
        )

    if enforce_portfolio_minimums:
        _require(
            len(projects) >= MIN_PROJECTS[split],
            "BENCHMARK_PROJECT_COUNT_BELOW_MINIMUM",
            f"{split} requires at least {MIN_PROJECTS[split]} projects",
        )
        _require(
            len(cases) >= MIN_CASES[split],
            "BENCHMARK_CASE_COUNT_BELOW_MINIMUM",
            f"{split} requires at least {MIN_CASES[split]} cases",
        )
        for project_id in project_ids:
            _require(
                project_case_counts[project_id] >= MIN_CASES_PER_PROJECT,
                "BENCHMARK_PROJECT_CASE_COUNT_BELOW_MINIMUM",
                f"{project_id} requires at least {MIN_CASES_PER_PROJECT} cases",
            )
        _require(
            len(categories) >= MIN_CATEGORY_COUNT,
            "BENCHMARK_CATEGORY_COVERAGE_BELOW_MINIMUM",
            f"requires at least {MIN_CATEGORY_COUNT} categories",
        )

    expected_hash = _sha256(dataset.get("dataset_hash"), "dataset_hash")
    unhashed = dict(dataset)
    unhashed.pop("dataset_hash", None)
    _require(
        canonical_hash(unhashed) == expected_hash,
        "BENCHMARK_DATASET_HASH_MISMATCH",
        str(dataset.get("dataset_id") or "dataset"),
    )
    return {
        "dataset_id": _non_empty(dataset.get("dataset_id"), "dataset_id"),
        "dataset_version": _non_empty(dataset.get("dataset_version"), "dataset_version"),
        "dataset_hash": expected_hash,
        "split": split,
        "project_count": len(projects),
        "case_count": len(cases),
        "target_count": target_count,
        "category_count": len(categories),
        "categories": dict(sorted(categories.items())),
        "answer_status_counts": dict(sorted(answer_status_counts.items())),
        "project_ids": sorted(project_ids),
        "project_family_hashes": sorted(project_family_hashes),
        "document_hashes": sorted(document_hashes),
        "question_hashes": sorted(question_hashes),
        "project_case_counts": dict(sorted(project_case_counts.items())),
        "project_document_hashes": dict(sorted(project_document_hashes.items())),
    }


def materialize_project_silver_cases(
    dataset: Mapping[str, Any],
    *,
    project_id: str,
) -> dict[str, Any]:
    """Project the reviewed benchmark contract into the existing PDF evaluator.

    The output name keeps the historical `silver-cases` schema because the
    evaluator contract is frozen.  Its annotation_status and source dataset
    hash make clear that the authority is the reviewed Gold/Holdout dataset.
    """

    summary = validate_dataset(dataset)
    _require(
        project_id in summary["project_ids"],
        "BENCHMARK_PROJECT_UNKNOWN",
        project_id,
    )
    project = next(
        item for item in dataset["projects"] if item["project_id"] == project_id
    )
    primary = project["source_documents"][0]
    cases = []
    for case in dataset["cases"]:
        if case["project_id"] != project_id:
            continue
        cases.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "question": case["question"],
                "answer_status": case["answer_status"],
                "targets": [
                    {
                        "phrase": target["required_text_fragments"][0],
                        "pages": list(target["page_numbers"]),
                    }
                    for target in case["targets"]
                ],
            }
        )
    _require(bool(cases), "BENCHMARK_PROJECT_CASES_MISSING", project_id)
    return {
        "schema_version": "bid.pdf-c3.silver-cases.v1",
        "dataset_id": f"{summary['dataset_id']}::{project_id}",
        "annotation_status": (
            "approved_gold" if summary["split"] == "development" else "sealed_holdout"
        ),
        "source_benchmark_dataset_hash": summary["dataset_hash"],
        "document_sha256": primary["sha256"],
        "cases": cases,
    }


def validate_development_holdout_isolation(
    development: Mapping[str, Any],
    holdout: Mapping[str, Any],
) -> dict[str, int]:
    dev = validate_dataset(development, expected_split="development")
    test = validate_dataset(holdout, expected_split="holdout")
    overlaps = {
        "project_id_overlap": len(set(dev["project_ids"]) & set(test["project_ids"])),
        "project_family_overlap": len(
            set(dev["project_family_hashes"]) & set(test["project_family_hashes"])
        ),
        "document_hash_overlap": len(
            set(dev["document_hashes"]) & set(test["document_hashes"])
        ),
        "question_hash_overlap": len(
            set(dev["question_hashes"]) & set(test["question_hashes"])
        ),
    }
    _require(
        all(value == 0 for value in overlaps.values()),
        "BENCHMARK_DEVELOPMENT_HOLDOUT_OVERLAP",
        json.dumps(overlaps, sort_keys=True),
    )
    return overlaps


def validate_development_snapshot(
    snapshot: Mapping[str, Any],
    *,
    development: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _require(
        snapshot.get("schema_version")
        == BENCHMARK_DEVELOPMENT_SNAPSHOT_SCHEMA_VERSION,
        "BENCHMARK_DEVELOPMENT_SNAPSHOT_SCHEMA_INVALID",
        "unexpected development snapshot schema_version",
    )
    _require(
        snapshot.get("profile_version") == BENCHMARK_PROFILE_VERSION,
        "BENCHMARK_PROFILE_MISMATCH",
        "unexpected benchmark profile",
    )
    _require(
        snapshot.get("status") == "frozen_predevelopment",
        "BENCHMARK_DEVELOPMENT_SNAPSHOT_STATUS_INVALID",
        "snapshot must be frozen_predevelopment",
    )
    _validate_frozen_profiles(snapshot.get("retrieval_profiles"))
    thresholds = _validate_thresholds(snapshot.get("acceptance_thresholds"))
    guards = snapshot.get("anti_leakage")
    _require(
        isinstance(guards, Mapping)
        and guards.get("project_specific_query_hints_forbidden") is True
        and guards.get("threshold_change_after_snapshot_forbidden") is True
        and guards.get("code_or_profile_change_requires_new_snapshot") is True,
        "BENCHMARK_ANTI_LEAKAGE_GUARD_MISSING",
        "development snapshot guards are incomplete",
    )
    _validate_artifact_snapshot(snapshot.get("frozen_artifacts"))
    expected_hash = _sha256(snapshot.get("snapshot_hash"), "snapshot_hash")
    unhashed = dict(snapshot)
    unhashed.pop("snapshot_hash", None)
    _require(
        canonical_hash(unhashed) == expected_hash,
        "BENCHMARK_DEVELOPMENT_SNAPSHOT_HASH_MISMATCH",
        str(snapshot.get("snapshot_id") or "snapshot"),
    )
    if development is not None:
        summary = validate_dataset(development, expected_split="development")
        ref = snapshot.get("dataset")
        _require(
            isinstance(ref, Mapping),
            "BENCHMARK_DATASET_REFERENCE_MISSING",
            "development",
        )
        for name in ("dataset_id", "dataset_hash", "project_count", "case_count"):
            _require(
                ref.get(name) == summary[name],
                "BENCHMARK_DATASET_REFERENCE_MISMATCH",
                f"development.{name}",
            )
    return {
        "binding_kind": "development_snapshot",
        "binding_id": _non_empty(snapshot.get("snapshot_id"), "snapshot_id"),
        "binding_hash": expected_hash,
        "thresholds": thresholds,
        "frozen_artifacts": snapshot["frozen_artifacts"],
    }


def validate_freeze(
    freeze: Mapping[str, Any],
    *,
    development: Mapping[str, Any] | None = None,
    holdout: Mapping[str, Any] | None = None,
    development_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _require(
        freeze.get("schema_version") == BENCHMARK_FREEZE_SCHEMA_VERSION,
        "BENCHMARK_FREEZE_SCHEMA_INVALID",
        "unexpected freeze schema_version",
    )
    _require(
        freeze.get("profile_version") == BENCHMARK_PROFILE_VERSION,
        "BENCHMARK_PROFILE_MISMATCH",
        "unexpected benchmark profile",
    )
    _require(
        freeze.get("status") == "frozen_preholdout",
        "BENCHMARK_FREEZE_STATUS_INVALID",
        "freeze must be frozen_preholdout",
    )
    _validate_frozen_profiles(freeze.get("retrieval_profiles"))
    thresholds = _validate_thresholds(freeze.get("acceptance_thresholds"))
    development_acceptance = freeze.get("development_acceptance")
    _require(
        isinstance(development_acceptance, Mapping)
        and development_acceptance.get("status") == "passed",
        "BENCHMARK_DEVELOPMENT_NOT_ACCEPTED",
        "a passed frozen development report is required before holdout",
    )
    _sha256(
        development_acceptance.get("report_sha256"),
        "development_acceptance.report_sha256",
    )
    _sha256(
        development_acceptance.get("snapshot_hash"),
        "development_acceptance.snapshot_hash",
    )
    execution = freeze.get("holdout_execution")
    _require(
        isinstance(execution, Mapping)
        and execution.get("maximum_formal_execution_count") == 1
        and execution.get("rerun_after_result_forbidden") is True
        and execution.get("baseline_before_candidate") is True,
        "BENCHMARK_HOLDOUT_EXECUTION_POLICY_INVALID",
        "one formal baseline-then-candidate execution is required",
    )
    guards = freeze.get("anti_leakage")
    required_guards = {
        "project_level_split",
        "project_family_overlap_forbidden",
        "source_document_overlap_forbidden",
        "project_specific_query_hints_forbidden",
        "holdout_not_used_for_tuning",
        "holdout_failures_may_not_define_new_rules",
        "threshold_change_after_freeze_forbidden",
    }
    _require(
        isinstance(guards, Mapping)
        and required_guards.issubset(guards)
        and all(guards[name] is True for name in required_guards),
        "BENCHMARK_ANTI_LEAKAGE_GUARD_MISSING",
        "all frozen anti-leakage guards must be true",
    )
    _validate_artifact_snapshot(freeze.get("frozen_artifacts"))
    expected_hash = _sha256(freeze.get("freeze_hash"), "freeze_hash")
    unhashed = dict(freeze)
    unhashed.pop("freeze_hash", None)
    _require(
        canonical_hash(unhashed) == expected_hash,
        "BENCHMARK_FREEZE_HASH_MISMATCH",
        str(freeze.get("freeze_id") or "freeze"),
    )
    if development is not None and holdout is not None:
        isolation = validate_development_holdout_isolation(development, holdout)
    else:
        isolation = None
    if development is not None or holdout is not None:
        refs = freeze.get("datasets")
        _require(isinstance(refs, Mapping), "BENCHMARK_DATASET_REFERENCE_MISSING", "")
        supplied = (
            ("development", development),
            ("holdout", holdout),
        )
        for split, dataset in supplied:
            if dataset is None:
                continue
            summary = validate_dataset(dataset, expected_split=split)
            ref = refs.get(split)
            _require(isinstance(ref, Mapping), "BENCHMARK_DATASET_REFERENCE_MISSING", split)
            for name in ("dataset_id", "dataset_hash", "project_count", "case_count"):
                _require(
                    ref.get(name) == summary[name],
                    "BENCHMARK_DATASET_REFERENCE_MISMATCH",
                    f"{split}.{name}",
                )
    if development_snapshot is not None:
        snapshot_summary = validate_development_snapshot(
            development_snapshot,
            development=development,
        )
        _require(
            development_acceptance.get("snapshot_hash")
            == snapshot_summary["binding_hash"],
            "BENCHMARK_DEVELOPMENT_SNAPSHOT_REFERENCE_MISMATCH",
            "freeze does not bind the accepted development snapshot",
        )
        _require(
            freeze.get("frozen_artifacts") == snapshot_summary["frozen_artifacts"],
            "BENCHMARK_ARTIFACT_CHANGED_AFTER_DEVELOPMENT",
            "code, contracts or dependencies changed after development",
        )
    return {
        "binding_kind": "holdout_freeze",
        "binding_id": _non_empty(freeze.get("freeze_id"), "freeze_id"),
        "binding_hash": expected_hash,
        "freeze_id": _non_empty(freeze.get("freeze_id"), "freeze_id"),
        "freeze_hash": expected_hash,
        "thresholds": dict(thresholds),
        "isolation": isolation,
    }


def aggregate_project_reports(
    *,
    dataset: Mapping[str, Any],
    project_reports: Sequence[Mapping[str, Any]],
    freeze: Mapping[str, Any] | None = None,
    development_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dataset_summary = validate_dataset(dataset)
    split = dataset_summary["split"]
    if split == "development":
        _require(
            development_snapshot is not None and freeze is None,
            "BENCHMARK_EVALUATION_BINDING_INVALID",
            "development aggregation requires only a development snapshot",
        )
        binding_summary = validate_development_snapshot(
            development_snapshot,
            development=dataset,
        )
    else:
        _require(
            freeze is not None and development_snapshot is None,
            "BENCHMARK_EVALUATION_BINDING_INVALID",
            "holdout aggregation requires only a holdout freeze",
        )
        binding_summary = validate_freeze(freeze, holdout=dataset)
    reports_by_project: dict[str, Mapping[str, Any]] = {}
    for report in project_reports:
        project_id = _non_empty(report.get("project_id"), "project report project_id")
        _require(
            project_id not in reports_by_project,
            "BENCHMARK_PROJECT_REPORT_DUPLICATE",
            project_id,
        )
        reports_by_project[project_id] = report
    _require(
        set(reports_by_project) == set(dataset_summary["project_ids"]),
        "BENCHMARK_PROJECT_REPORT_SET_MISMATCH",
        "project reports must exactly match the frozen dataset",
    )

    project_rows: list[dict[str, Any]] = []
    baseline_case_rows: list[Mapping[str, Any]] = []
    candidate_case_rows: list[Mapping[str, Any]] = []
    paired_deltas: list[float] = []
    candidate_latencies: list[float] = []
    regression_count = 0
    squeeze_recovery_count = 0
    atom_only_violation_count = 0
    invariant_failures: list[str] = []

    for project_id in sorted(reports_by_project):
        wrapper = reports_by_project[project_id]
        baseline = _as_mapping(wrapper.get("baseline_summary"), "baseline_summary")
        candidate = _as_mapping(wrapper.get("candidate_summary"), "candidate_summary")
        ab = _as_mapping(wrapper.get("ab_summary"), "ab_summary")
        expected_project_payload = materialize_project_silver_cases(
            dataset,
            project_id=project_id,
        )
        _validate_project_report_lineage(
            project_id=project_id,
            wrapper=wrapper,
            dataset_summary=dataset_summary,
            baseline=baseline,
            candidate=candidate,
            ab=ab,
            expected_project_dataset_hash=canonical_hash(expected_project_payload),
        )
        baseline_metrics = _as_mapping(baseline.get("retrieval"), "baseline retrieval")
        candidate_metrics = _as_mapping(candidate.get("retrieval"), "candidate retrieval")
        baseline_cases = _case_metrics_by_id(baseline)
        candidate_cases = _case_metrics_by_id(candidate)
        baseline_case_rows.extend(baseline_cases.values())
        candidate_case_rows.extend(candidate_cases.values())
        _require(
            set(baseline_cases) == set(candidate_cases),
            "BENCHMARK_CASE_REPORT_SET_MISMATCH",
            project_id,
        )
        expected_case_ids = {
            str(item["case_id"])
            for item in expected_project_payload["cases"]
        }
        _require(
            set(candidate_cases) == expected_case_ids,
            "BENCHMARK_CASE_REPORT_SET_MISMATCH",
            f"{project_id}: report cases differ from frozen dataset",
        )
        _validate_summary_matches_cases(
            project_id=project_id,
            arm="baseline",
            summary_metrics=baseline_metrics,
            cases=list(baseline_cases.values()),
        )
        _validate_summary_matches_cases(
            project_id=project_id,
            arm="candidate",
            summary_metrics=candidate_metrics,
            cases=list(candidate_cases.values()),
        )
        local_regressions = 0
        local_recoveries = 0
        for case_id in sorted(baseline_cases):
            baseline_case = baseline_cases[case_id]
            candidate_case = candidate_cases[case_id]
            baseline_5 = _as_mapping(baseline_case.get("metrics_at_5"), "metrics_at_5")
            candidate_5 = _as_mapping(candidate_case.get("metrics_at_5"), "metrics_at_5")
            baseline_8 = _as_mapping(baseline_case.get("metrics_at_8"), "metrics_at_8")
            candidate_8 = _as_mapping(candidate_case.get("metrics_at_8"), "metrics_at_8")
            if any(
                _number(candidate_metric.get(name), name)
                < _number(baseline_metric.get(name), name) - 1e-9
                for baseline_metric, candidate_metric, name in (
                    (baseline_5, candidate_5, "target_recall"),
                    (baseline_8, candidate_8, "target_recall"),
                    (baseline_case, candidate_case, "read_target_recall_at_5"),
                )
            ):
                local_regressions += 1
            if _number(candidate_8.get("target_recall"), "target_recall") > (
                _number(baseline_8.get("target_recall"), "target_recall") + 1e-9
            ):
                local_recoveries += 1
            baseline_ms = _number(baseline_case.get("search_ms"), "search_ms")
            candidate_ms = _number(candidate_case.get("search_ms"), "search_ms")
            paired_deltas.append(candidate_ms - baseline_ms)
            candidate_latencies.append(candidate_ms)
        regression_count += local_regressions
        squeeze_recovery_count += local_recoveries
        violations = int(candidate_metrics.get("atom_only_violation_count") or 0)
        atom_only_violation_count += violations
        invariants = _as_mapping(ab.get("invariants"), "ab invariants")
        for name in (
            "same_database",
            "same_parse_head",
            "same_retrieval_index_head",
            "same_semantic_index_head",
            "frozen_fusion_candidate_pool_identical",
            "zero_promotion_identity",
            "atom_only_violation_unchanged",
            "deterministic_replay_match",
        ):
            if invariants.get(name) is not True:
                invariant_failures.append(f"{project_id}:{name}")
        project_rows.append(
            {
                "project_id": project_id,
                "case_count": int(candidate_metrics.get("case_count") or 0),
                "baseline": _metric_subset(baseline_metrics),
                "candidate": _metric_subset(candidate_metrics),
                "delta": {
                    name: round(
                        _number(candidate_metrics.get(name), name)
                        - _number(baseline_metrics.get(name), name),
                        6,
                    )
                    for name in METRIC_NAMES
                },
                "quality_regression_count": local_regressions,
                "squeeze_recovery_count": local_recoveries,
                "candidate_search_p95_ms": _percentile_nearest_rank(
                    [_number(item.get("search_ms"), "search_ms") for item in candidate_cases.values()],
                    0.95,
                ),
            }
        )

    macro_baseline = _macro_metrics(row["baseline"] for row in project_rows)
    macro_candidate = _macro_metrics(row["candidate"] for row in project_rows)
    macro_delta = {
        name: round(macro_candidate[name] - macro_baseline[name], 6)
        for name in METRIC_NAMES
    }
    micro_baseline = _weighted_project_metrics(project_rows, "baseline")
    micro_candidate = _weighted_project_metrics(project_rows, "candidate")
    aggregate = {
        "schema_version": BENCHMARK_REPORT_SCHEMA_VERSION,
        "profile_version": BENCHMARK_PROFILE_VERSION,
        "evaluation_binding": {
            "kind": binding_summary["binding_kind"],
            "id": binding_summary["binding_id"],
            "hash": binding_summary["binding_hash"],
        },
        "dataset_id": dataset_summary["dataset_id"],
        "dataset_hash": dataset_summary["dataset_hash"],
        "split": split,
        "project_count": len(project_rows),
        "case_count": sum(row["case_count"] for row in project_rows),
        "projects": project_rows,
        "macro_baseline": macro_baseline,
        "macro_candidate": macro_candidate,
        "macro_delta": macro_delta,
        "micro_baseline": micro_baseline,
        "micro_candidate": micro_candidate,
        "micro_delta": {
            name: round(micro_candidate[name] - micro_baseline[name], 6)
            for name in METRIC_NAMES
        },
        "macro_by_category_baseline": _category_metrics(baseline_case_rows),
        "macro_by_category_candidate": _category_metrics(candidate_case_rows),
        "worst_project_candidate": {
            name: min(row["candidate"][name] for row in project_rows)
            for name in ("hit_at_5", "target_recall_at_5")
        },
        "candidate_search_p95_ms": _percentile_nearest_rank(candidate_latencies, 0.95),
        "paired_search_delta_p95_ms": _percentile_nearest_rank(paired_deltas, 0.95),
        "quality_regression_count": regression_count,
        "squeeze_recovery_count": squeeze_recovery_count,
        "atom_only_violation_count": atom_only_violation_count,
        "invariant_failures": invariant_failures,
    }
    aggregate["gate"] = evaluate_acceptance_gate(
        aggregate,
        thresholds=binding_summary["thresholds"],
    )
    unhashed = dict(aggregate)
    aggregate["report_hash"] = canonical_hash(unhashed)
    return aggregate


def evaluate_acceptance_gate(
    aggregate: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any]:
    frozen = dict(thresholds or DEFAULT_ACCEPTANCE_THRESHOLDS)
    candidate = _as_mapping(aggregate.get("macro_candidate"), "macro_candidate")
    delta = _as_mapping(aggregate.get("macro_delta"), "macro_delta")
    worst = _as_mapping(
        aggregate.get("worst_project_candidate"),
        "worst_project_candidate",
    )
    checks: dict[str, bool] = {
        "macro_hit_at_5": _number(candidate.get("hit_at_5"), "hit_at_5")
        >= _number(frozen["macro_hit_at_5_min"], "macro_hit_at_5_min"),
        "macro_target_recall_at_5": _number(candidate.get("target_recall_at_5"), "target_recall_at_5")
        >= _number(frozen["macro_target_recall_at_5_min"], "macro_target_recall_at_5_min"),
        "macro_mrr_at_5": _number(candidate.get("mrr_at_5"), "mrr_at_5")
        >= _number(frozen["macro_mrr_at_5_min"], "macro_mrr_at_5_min"),
        "macro_ndcg_at_5": _number(candidate.get("ndcg_at_5"), "ndcg_at_5")
        >= _number(frozen["macro_ndcg_at_5_min"], "macro_ndcg_at_5_min"),
        "macro_hit_at_8": _number(candidate.get("hit_at_8"), "hit_at_8")
        >= _number(frozen["macro_hit_at_8_min"], "macro_hit_at_8_min"),
        "macro_target_recall_at_8": _number(candidate.get("target_recall_at_8"), "target_recall_at_8")
        >= _number(frozen["macro_target_recall_at_8_min"], "macro_target_recall_at_8_min"),
        "macro_read_target_recall_at_5": _number(candidate.get("read_target_recall_at_5"), "read_target_recall_at_5")
        >= _number(frozen["macro_read_target_recall_at_5_min"], "macro_read_target_recall_at_5_min"),
        "macro_citable_target_availability": _number(candidate.get("citable_target_availability"), "citable_target_availability")
        >= _number(frozen["macro_citable_target_availability_min"], "macro_citable_target_availability_min"),
        "worst_project_hit_at_5": _number(worst.get("hit_at_5"), "hit_at_5")
        >= _number(frozen["worst_project_hit_at_5_min"], "worst_project_hit_at_5_min"),
        "worst_project_target_recall_at_5": _number(worst.get("target_recall_at_5"), "target_recall_at_5")
        >= _number(frozen["worst_project_target_recall_at_5_min"], "worst_project_target_recall_at_5_min"),
        "macro_hit_at_5_delta": _number(delta.get("hit_at_5"), "hit_at_5")
        >= _number(frozen["macro_hit_at_5_delta_min"], "macro_hit_at_5_delta_min"),
        "macro_target_recall_at_5_delta": _number(delta.get("target_recall_at_5"), "target_recall_at_5")
        >= _number(frozen["macro_target_recall_at_5_delta_min"], "macro_target_recall_at_5_delta_min"),
        "macro_hit_at_8_delta": _number(delta.get("hit_at_8"), "hit_at_8")
        >= _number(frozen["macro_hit_at_8_delta_min"], "macro_hit_at_8_delta_min"),
        "macro_target_recall_at_8_delta": _number(delta.get("target_recall_at_8"), "target_recall_at_8")
        >= _number(frozen["macro_target_recall_at_8_delta_min"], "macro_target_recall_at_8_delta_min"),
        "macro_read_target_recall_at_5_delta": _number(delta.get("read_target_recall_at_5"), "read_target_recall_at_5")
        >= _number(frozen["macro_read_target_recall_at_5_delta_min"], "macro_read_target_recall_at_5_delta_min"),
        "per_case_quality_regression_count": int(aggregate.get("quality_regression_count") or 0)
        <= int(frozen["per_case_quality_regression_count_max"]),
        "atom_only_violation_count": int(aggregate.get("atom_only_violation_count") or 0)
        <= int(frozen["atom_only_violation_count_max"]),
        "candidate_search_p95_ms": _number(aggregate.get("candidate_search_p95_ms"), "candidate_search_p95_ms")
        <= _number(frozen["candidate_search_p95_ms_max"], "candidate_search_p95_ms_max"),
        "paired_search_delta_p95_ms": _number(aggregate.get("paired_search_delta_p95_ms"), "paired_search_delta_p95_ms")
        <= _number(frozen["paired_search_delta_p95_ms_max"], "paired_search_delta_p95_ms_max"),
        "lineage_and_determinism_invariants": not bool(aggregate.get("invariant_failures")),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "status": "passed" if not failed else "failed",
        "checks": checks,
        "failed_checks": failed,
        "mechanism_observation": (
            "recovered_frozen_candidate_squeeze"
            if int(aggregate.get("squeeze_recovery_count") or 0) > 0
            else "not_observed_in_this_split"
        ),
        "holdout_failure_policy": (
            "no_rerun_or_tuning; create a new development cohort and a new holdout version"
        ),
    }


def _validate_project_report_lineage(
    *,
    project_id: str,
    wrapper: Mapping[str, Any],
    dataset_summary: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    ab: Mapping[str, Any],
    expected_project_dataset_hash: str,
) -> None:
    _require(
        wrapper.get("dataset_hash") == dataset_summary["dataset_hash"],
        "BENCHMARK_PROJECT_DATASET_HASH_MISMATCH",
        project_id,
    )
    expected_documents = set(dataset_summary["project_document_hashes"][project_id])
    reported_documents = set(wrapper.get("document_sha256") or [])
    _require(
        reported_documents == expected_documents,
        "BENCHMARK_PROJECT_DOCUMENT_HASH_MISMATCH",
        project_id,
    )
    _require(
        baseline.get("profiles", {}).get("candidate_fusion_profile_version")
        == BASELINE_FUSION_PROFILE_VERSION,
        "BENCHMARK_BASELINE_PROFILE_MISMATCH",
        project_id,
    )
    _require(
        baseline.get("profiles", {}).get("rerank_profile_version")
        != CANDIDATE_RERANK_PROFILE_VERSION,
        "BENCHMARK_BASELINE_PROFILE_MISMATCH",
        project_id,
    )
    _require(
        candidate.get("profiles", {}).get("candidate_fusion_profile_version")
        == BASELINE_FUSION_PROFILE_VERSION
        and candidate.get("profiles", {}).get("rerank_profile_version")
        == CANDIDATE_RERANK_PROFILE_VERSION,
        "BENCHMARK_CANDIDATE_PROFILE_MISMATCH",
        project_id,
    )
    _require(
        ab.get("schema_version") == "bid.pdf-c3.lightweight-rerank-ab.v1",
        "BENCHMARK_AB_REPORT_SCHEMA_INVALID",
        project_id,
    )
    _require(
        ab.get("dataset_hash")
        == baseline.get("dataset_hash")
        == candidate.get("dataset_hash")
        == expected_project_dataset_hash,
        "BENCHMARK_PROJECT_CASESET_HASH_MISMATCH",
        project_id,
    )
    _require(
        ab.get("document_sha256") in expected_documents,
        "BENCHMARK_PROJECT_DOCUMENT_HASH_MISMATCH",
        project_id,
    )


def _validate_frozen_profiles(value: Any) -> None:
    profiles = _as_mapping(value, "retrieval_profiles")
    _require(
        profiles.get("baseline") == BASELINE_FUSION_PROFILE_VERSION,
        "BENCHMARK_BASELINE_PROFILE_MISMATCH",
        "RQ2-B must be the frozen baseline",
    )
    _require(
        profiles.get("candidate") == CANDIDATE_RERANK_PROFILE_VERSION,
        "BENCHMARK_CANDIDATE_PROFILE_MISMATCH",
        "RQ2-C must be the frozen candidate",
    )


def _validate_thresholds(value: Any) -> dict[str, float | int]:
    thresholds = _as_mapping(value, "acceptance_thresholds")
    _require(
        set(thresholds) == set(DEFAULT_ACCEPTANCE_THRESHOLDS),
        "BENCHMARK_THRESHOLD_SET_INVALID",
        "threshold names must match the frozen RQ2 closeout profile",
    )
    _require(
        dict(thresholds) == DEFAULT_ACCEPTANCE_THRESHOLDS,
        "BENCHMARK_THRESHOLD_VALUE_MISMATCH",
        "threshold values are immutable for the RQ2 closeout profile",
    )
    for name, item in thresholds.items():
        _number(item, name)
    return dict(thresholds)


def _validate_artifact_snapshot(value: Any) -> None:
    artifacts = _as_mapping(value, "frozen_artifacts")
    for group in ("code_sha256", "contract_sha256", "dependency_sha256"):
        values = artifacts.get(group)
        _require(
            isinstance(values, Mapping) and bool(values),
            "BENCHMARK_ARTIFACT_SNAPSHOT_MISSING",
            group,
        )
        for name, digest in values.items():
            _non_empty(name, group)
            _sha256(digest, f"{group}.{name}")


def _case_metrics_by_id(summary: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cases = _mapping_list(summary.get("case_metrics"), "case_metrics")
    result: dict[str, Mapping[str, Any]] = {}
    for item in cases:
        case_id = _non_empty(item.get("case_id"), "case metric case_id")
        _require(
            case_id not in result,
            "BENCHMARK_CASE_REPORT_DUPLICATE",
            case_id,
        )
        result[case_id] = item
    _require(bool(result), "BENCHMARK_CASE_REPORT_EMPTY", "case_metrics")
    return result


def _validate_summary_matches_cases(
    *,
    project_id: str,
    arm: str,
    summary_metrics: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
) -> None:
    total_targets = sum(int(case.get("target_count") or 0) for case in cases)
    citable_targets = sum(int(case.get("citable_target_count") or 0) for case in cases)
    _require(
        total_targets > 0 and 0 <= citable_targets <= total_targets,
        "BENCHMARK_CASE_TARGET_COUNTS_INVALID",
        f"{project_id}:{arm}",
    )
    recomputed = {
        "hit_at_5": fmean(1.0 if case["metrics_at_5"].get("hit") else 0.0 for case in cases),
        "target_recall_at_5": fmean(_number(case["metrics_at_5"].get("target_recall"), "target_recall") for case in cases),
        "mrr_at_5": fmean(_number(case["metrics_at_5"].get("mrr"), "mrr") for case in cases),
        "ndcg_at_5": fmean(_number(case["metrics_at_5"].get("ndcg"), "ndcg") for case in cases),
        "hit_at_8": fmean(1.0 if case["metrics_at_8"].get("hit") else 0.0 for case in cases),
        "target_recall_at_8": fmean(_number(case["metrics_at_8"].get("target_recall"), "target_recall") for case in cases),
        "mrr_at_8": fmean(_number(case["metrics_at_8"].get("mrr"), "mrr") for case in cases),
        "ndcg_at_8": fmean(_number(case["metrics_at_8"].get("ndcg"), "ndcg") for case in cases),
        "read_target_recall_at_5": fmean(_number(case.get("read_target_recall_at_5"), "read_target_recall_at_5") for case in cases),
        "citable_target_availability": citable_targets / total_targets,
    }
    _require(
        int(summary_metrics.get("case_count") or 0) == len(cases),
        "BENCHMARK_SUMMARY_CASE_COUNT_MISMATCH",
        f"{project_id}:{arm}",
    )
    for name, expected in recomputed.items():
        actual = _number(summary_metrics.get(name), name)
        _require(
            abs(actual - expected) <= 0.0000015,
            "BENCHMARK_SUMMARY_METRIC_MISMATCH",
            f"{project_id}:{arm}:{name}",
        )


def _metric_subset(metrics: Mapping[str, Any]) -> dict[str, float]:
    return {
        name: round(_number(metrics.get(name), name), 6)
        for name in METRIC_NAMES
    }


def _macro_metrics(rows: Iterable[Mapping[str, float]]) -> dict[str, float]:
    materialized = list(rows)
    _require(bool(materialized), "BENCHMARK_PROJECT_REPORT_EMPTY", "")
    return {
        name: round(fmean(row[name] for row in materialized), 6)
        for name in METRIC_NAMES
    }


def _weighted_project_metrics(
    project_rows: Sequence[Mapping[str, Any]],
    arm: str,
) -> dict[str, float]:
    total = sum(int(row["case_count"]) for row in project_rows)
    _require(total > 0, "BENCHMARK_CASE_REPORT_EMPTY", "")
    return {
        name: round(
            sum(
                _number(row[arm][name], name) * int(row["case_count"])
                for row in project_rows
            )
            / total,
            6,
        )
        for name in METRIC_NAMES
    }


def _category_metrics(cases: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for case in cases:
        category = _non_empty(case.get("category"), "case category")
        groups.setdefault(category, []).append(case)
    result: dict[str, dict[str, float | int]] = {}
    for category, rows in sorted(groups.items()):
        result[category] = {
            "case_count": len(rows),
            "hit_at_5": round(fmean(1.0 if row["metrics_at_5"].get("hit") else 0.0 for row in rows), 6),
            "target_recall_at_5": round(fmean(_number(row["metrics_at_5"].get("target_recall"), "target_recall") for row in rows), 6),
            "mrr_at_5": round(fmean(_number(row["metrics_at_5"].get("mrr"), "mrr") for row in rows), 6),
            "ndcg_at_5": round(fmean(_number(row["metrics_at_5"].get("ndcg"), "ndcg") for row in rows), 6),
            "hit_at_8": round(fmean(1.0 if row["metrics_at_8"].get("hit") else 0.0 for row in rows), 6),
            "target_recall_at_8": round(fmean(_number(row["metrics_at_8"].get("target_recall"), "target_recall") for row in rows), 6),
            "mrr_at_8": round(fmean(_number(row["metrics_at_8"].get("mrr"), "mrr") for row in rows), 6),
            "ndcg_at_8": round(fmean(_number(row["metrics_at_8"].get("ndcg"), "ndcg") for row in rows), 6),
            "read_target_recall_at_5": round(fmean(_number(row.get("read_target_recall_at_5"), "read_target_recall_at_5") for row in rows), 6),
        }
    return result


def _percentile_nearest_rank(values: Sequence[float], percentile: float) -> float:
    _require(bool(values), "BENCHMARK_LATENCY_SAMPLE_EMPTY", "")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return round(float(ordered[rank - 1]), 3)


def _mapping_list(value: Any, name: str) -> list[Mapping[str, Any]]:
    _require(
        isinstance(value, list) and all(isinstance(item, Mapping) for item in value),
        "BENCHMARK_FIELD_INVALID",
        f"{name} must be a list of objects",
    )
    return list(value)


def _as_mapping(value: Any, name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), "BENCHMARK_FIELD_INVALID", name)
    return value


def _non_empty(value: Any, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), "BENCHMARK_FIELD_INVALID", name)
    return value.strip()


def _sha256(value: Any, name: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        "BENCHMARK_SHA256_INVALID",
        name,
    )
    return value


def _number(value: Any, name: str) -> float:
    _require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        "BENCHMARK_METRIC_INVALID",
        name,
    )
    return float(value)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise BenchmarkContractError(code, message)
