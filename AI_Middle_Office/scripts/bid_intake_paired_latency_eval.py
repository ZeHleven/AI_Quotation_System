from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
load_dotenv(PROJECT_DIR / ".env")

from app.agents.bid_intake.paired_latency import (  # noqa: E402
    paired_order,
    render_paired_latency_markdown,
    rotated_case_ids,
    summarize_paired_latency,
)
from app.agents.bid_intake.retrieval_evaluation import (  # noqa: E402
    RetrievalEvalCase,
    build_dataset_quality_report,
    load_eval_cases,
)
from app.core.database import SessionLocal  # noqa: E402
from mcp_servers.tender_evidence.auth import TenderScope  # noqa: E402
from mcp_servers.tender_evidence.hybrid_client import (  # noqa: E402
    configured_hybrid_client,
    hybrid_search_enabled,
)
from mcp_servers.tender_evidence.service import (  # noqa: E402
    TenderEvidenceService,
)
from mcp_servers.tender_evidence.sqlalchemy_repository import (  # noqa: E402
    SqlAlchemyTenderEvidenceRepository,
)


DEFAULT_DATASET = (
    PROJECT_DIR
    / "evals"
    / "bid_intake"
    / "retrieval"
    / "v1"
    / "private_graph_development_b_lingshui_approved_v1.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_DIR / "outputs" / "bid_intake_paired_latency"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure paired AB/BA latency for the bid-intake selective "
            "graph expansion without invoking the analysis LLM."
        )
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument(
        "--dataset-split",
        choices=("development", "holdout"),
        default="development",
        help=(
            "Expected split for every approved case. The default keeps the "
            "original Development protocol; independent Holdout runs must "
            "opt in explicitly."
        ),
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-top-k", type=int, default=20)
    parser.add_argument("--warmup-pairs-per-case", type=int, default=1)
    parser.add_argument("--measured-pairs-per-case", type=int, default=6)
    parser.add_argument(
        "--paired-delta-p95-max-ms",
        type=int,
        default=500,
    )
    parser.add_argument("--expected-dataset-fingerprint", required=True)
    parser.add_argument(
        "--experiment-name",
        default="RET-GRAPH-LATENCY-001",
    )
    parser.add_argument(
        "--freeze-id",
        default=(
            "RET-GRAPH-LATENCY-001-PAIRED-FREEZE-20260730-V1"
        ),
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    if args.top_k != 5 or args.candidate_top_k != 20:
        raise ValueError("the frozen paired protocol requires Top5/20")
    if args.warmup_pairs_per_case != 1:
        raise ValueError("the frozen protocol requires one warmup pair")
    if args.measured_pairs_per_case != 6:
        raise ValueError("the frozen protocol requires six measured pairs")
    if args.paired_delta_p95_max_ms != 500:
        raise ValueError("the frozen paired P95 threshold is 500ms")

    cases = load_eval_cases(Path(args.dataset).resolve())
    _validate_cases(cases, expected_split=args.dataset_split)
    quality = build_dataset_quality_report(cases)
    fingerprint = str(quality["dataset_fingerprint"])
    if fingerprint != args.expected_dataset_fingerprint:
        raise RuntimeError(
            "dataset fingerprint differs from the frozen protocol"
        )

    hybrid_enabled = hybrid_search_enabled()
    repository = SqlAlchemyTenderEvidenceRepository(
        SessionLocal,
        hybrid_search_client=(
            configured_hybrid_client()
            if hybrid_enabled
            else None
        ),
    )
    by_id = {item.eval_case_id: item for item in cases}
    case_ids = [item.eval_case_id for item in cases]
    warmup_errors = _run_phase(
        phase="warmup",
        repetitions=args.warmup_pairs_per_case,
        case_ids=case_ids,
        by_id=by_id,
        repository=repository,
        top_k=args.top_k,
        candidate_top_k=args.candidate_top_k,
        persist_samples=False,
        progress_every=max(1, args.progress_every),
    )
    samples = _run_phase(
        phase="measured",
        repetitions=args.measured_pairs_per_case,
        case_ids=case_ids,
        by_id=by_id,
        repository=repository,
        top_k=args.top_k,
        candidate_top_k=args.candidate_top_k,
        persist_samples=True,
        progress_every=max(1, args.progress_every),
    )
    expected_pairs = len(cases) * args.measured_pairs_per_case
    summary = summarize_paired_latency(
        samples,
        expected_pair_count=expected_pairs,
        measured_pairs_per_case=args.measured_pairs_per_case,
        paired_delta_p95_ms_max=args.paired_delta_p95_max_ms,
    )
    report = {
        "schema_version": "bid_intake_paired_latency_report_v1",
        "experiment_name": args.experiment_name,
        "freeze_id": args.freeze_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "path": str(Path(args.dataset).resolve()),
            "dataset_fingerprint": fingerprint,
            "case_count": len(cases),
            "dataset_split": args.dataset_split,
        },
        "environment": {
            "backend": "database_layered_retrieval",
            "hybrid_search_enabled": hybrid_enabled,
            "search_url": os.environ.get(
                "TENDER_EVIDENCE_SEARCH_URL",
                os.environ.get(
                    "RAG_SERVICE_URL",
                    "http://192.168.88.128:8001",
                ),
            ),
        },
        "configuration": {
            "top_k": args.top_k,
            "per_query_candidate_top_k": args.candidate_top_k,
            "warmup_pairs_per_case": args.warmup_pairs_per_case,
            "measured_pairs_per_case": (
                args.measured_pairs_per_case
            ),
            "baseline_selective_graph_enabled": False,
            "candidate_selective_graph_enabled": True,
            "all_other_optional_enhancements_enabled": False,
            "additional_llm_tokens": 0,
            "question_or_evidence_content_persisted": False,
        },
        "warmup": {
            "pair_count": (
                len(cases) * args.warmup_pairs_per_case
            ),
            "error_count": warmup_errors,
            "excluded_from_statistics": True,
        },
        "summary": summary,
        "samples": samples,
    }

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(
        item
        if item.isalnum() or item in {"-", "_"}
        else "-"
        for item in args.experiment_name
    )
    report_path = output_dir / f"{timestamp}_{safe_name}_report.json"
    markdown_path = output_dir / f"{timestamp}_{safe_name}_report.md"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_paired_latency_markdown(report),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "acceptance": summary["acceptance"],
                "overall": summary["overall"],
                "report_path": str(report_path),
                "markdown_path": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


def _validate_cases(
    cases: list[RetrievalEvalCase],
    *,
    expected_split: str,
) -> None:
    if not cases:
        raise ValueError("the paired latency dataset is empty")
    invalid_split = [
        item.eval_case_id
        for item in cases
        if item.dataset_split != expected_split
    ]
    if invalid_split:
        raise ValueError(
            "paired latency dataset split mismatch; expected "
            f"{expected_split}: "
            + ", ".join(invalid_split)
        )
    unapproved = [
        item.eval_case_id
        for item in cases
        if item.annotation_status != "approved"
    ]
    if unapproved:
        raise ValueError(
            "all paired latency cases must be approved: "
            + ", ".join(unapproved)
        )


def _run_phase(
    *,
    phase: str,
    repetitions: int,
    case_ids: list[str],
    by_id: dict[str, RetrievalEvalCase],
    repository,
    top_k: int,
    candidate_top_k: int,
    persist_samples: bool,
    progress_every: int,
) -> list[dict[str, Any]] | int:
    samples: list[dict[str, Any]] = []
    error_count = 0
    original_index = {
        case_id: index for index, case_id in enumerate(case_ids)
    }
    completed = 0
    total = repetitions * len(case_ids)
    for repetition_index in range(repetitions):
        for case_id in rotated_case_ids(
            case_ids,
            repetition_index=repetition_index,
        ):
            case = by_id[case_id]
            order = paired_order(
                repetition_index=repetition_index,
                case_index=original_index[case_id],
            )
            if order == "baseline_first":
                baseline = _measure(
                    case=case,
                    repository=repository,
                    top_k=top_k,
                    candidate_top_k=candidate_top_k,
                    enable_selective_graph_expansion=False,
                    phase=phase,
                    repetition_index=repetition_index,
                )
                candidate = _measure(
                    case=case,
                    repository=repository,
                    top_k=top_k,
                    candidate_top_k=candidate_top_k,
                    enable_selective_graph_expansion=True,
                    phase=phase,
                    repetition_index=repetition_index,
                )
            else:
                candidate = _measure(
                    case=case,
                    repository=repository,
                    top_k=top_k,
                    candidate_top_k=candidate_top_k,
                    enable_selective_graph_expansion=True,
                    phase=phase,
                    repetition_index=repetition_index,
                )
                baseline = _measure(
                    case=case,
                    repository=repository,
                    top_k=top_k,
                    candidate_top_k=candidate_top_k,
                    enable_selective_graph_expansion=False,
                    phase=phase,
                    repetition_index=repetition_index,
                )
            error_count += bool(baseline["error_code"])
            error_count += bool(candidate["error_code"])
            if persist_samples:
                expected_trigger = (
                    "graph_trigger_expected" in case.tags
                )
                candidate_summary = candidate["graph_summary"]
                samples.append(
                    {
                        "eval_case_id": case.eval_case_id,
                        "repetition": repetition_index + 1,
                        "order": order,
                        "graph_trigger_expected": expected_trigger,
                        "baseline_latency_ms": baseline["latency_ms"],
                        "candidate_latency_ms": (
                            candidate["latency_ms"]
                        ),
                        "paired_delta_ms": (
                            candidate["latency_ms"]
                            - baseline["latency_ms"]
                        ),
                        "baseline_status": baseline["status"],
                        "candidate_status": candidate["status"],
                        "baseline_error_code": (
                            baseline["error_code"]
                        ),
                        "candidate_error_code": (
                            candidate["error_code"]
                        ),
                        "candidate_graph_triggered": bool(
                            candidate_summary.get("triggered")
                        ),
                        "trigger_mismatch": (
                            bool(
                                candidate_summary.get("triggered")
                            )
                            != expected_trigger
                        ),
                        "candidate_graph_call_count": int(
                            candidate_summary.get(
                                "graph_call_count"
                            )
                            or 0
                        ),
                        "candidate_path_count": int(
                            candidate_summary.get("path_count") or 0
                        ),
                        "candidate_context_read_count": int(
                            candidate_summary.get(
                                "context_read_count"
                            )
                            or 0
                        ),
                        "candidate_reference_lookup_count": int(
                            candidate_summary.get(
                                "reference_lookup_count"
                            )
                            or 0
                        ),
                    }
                )
            completed += 1
            if completed % progress_every == 0 or completed == total:
                print(
                    f"[{phase}] paired cases {completed}/{total}",
                    flush=True,
                )
    return samples if persist_samples else error_count


def _measure(
    *,
    case: RetrievalEvalCase,
    repository,
    top_k: int,
    candidate_top_k: int,
    enable_selective_graph_expansion: bool,
    phase: str,
    repetition_index: int,
) -> dict[str, Any]:
    now = int(time.time())
    mode = (
        "candidate"
        if enable_selective_graph_expansion
        else "baseline"
    )
    scope = TenderScope(
        case_id=case.case_id,
        assessment_id=(
            f"latency:{phase}:{case.eval_case_id}:"
            f"{repetition_index}:{mode}"
        ),
        agent_run_id=(
            f"latency:{phase}:{case.eval_case_id}:"
            f"{repetition_index}:{mode}"
        ),
        subject="bid-intake-paired-latency-evaluator",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 3600,
        audience="local-evaluation",
        issuer="local-evaluation",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=candidate_top_k,
        enable_selective_graph_expansion=(
            enable_selective_graph_expansion
        ),
    )
    started = time.perf_counter_ns()
    try:
        envelope = service.search_tender_evidence(
            query=case.question,
            top_k=top_k,
        )
        elapsed_ms = round(
            (time.perf_counter_ns() - started) / 1_000_000
        )
        data = envelope.data if isinstance(envelope.data, dict) else {}
        query_plan = (
            data.get("query_plan")
            if isinstance(data.get("query_plan"), dict)
            else {}
        )
        graph_summary = (
            query_plan.get("selective_graph_expansion_summary")
            if isinstance(
                query_plan.get(
                    "selective_graph_expansion_summary"
                ),
                dict,
            )
            else {}
        )
        return {
            "latency_ms": elapsed_ms,
            "status": envelope.status.value,
            "error_code": envelope.error_code,
            "graph_summary": graph_summary,
        }
    except Exception as exc:
        elapsed_ms = round(
            (time.perf_counter_ns() - started) / 1_000_000
        )
        return {
            "latency_ms": elapsed_ms,
            "status": "failed",
            "error_code": type(exc).__name__,
            "graph_summary": {},
        }


if __name__ == "__main__":
    raise SystemExit(main())
