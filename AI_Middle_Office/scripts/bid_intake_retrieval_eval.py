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
from langchain_core.messages import ToolMessage


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
load_dotenv(PROJECT_DIR / ".env")

from app.agents.bid_intake.retrieval_evaluation import (  # noqa: E402
    RetrievalEvalCase,
    RetrievalEvidenceGroup,
    RetrievalPrediction,
    build_dataset_quality_report,
    evaluate_retrieval_predictions,
    load_eval_cases,
    load_predictions,
    render_markdown_report,
    sanitize_query_plan_for_evaluation,
    sanitize_fact_coverage_for_evaluation,
    write_predictions,
)
from app.agents.bid_intake.contracts import (  # noqa: E402
    FactCoverageMode,
)
from app.agents.bid_intake.fact_coverage import (  # noqa: E402
    build_fact_coverage_state,
)
from app.core.database import SessionLocal  # noqa: E402
from mcp_servers.tender_evidence.auth import (  # noqa: E402
    TenderScope,
)
from mcp_servers.tender_evidence.hybrid_client import (  # noqa: E402
    configured_hybrid_client,
    hybrid_search_enabled,
)
from mcp_servers.tender_evidence.local_repository import (  # noqa: E402
    LocalTenderEvidenceRepository,
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
    / "public_demo.jsonl"
)
DEFAULT_EVIDENCE_DATASET = (
    PROJECT_DIR
    / "mcp_servers"
    / "tender_evidence"
    / "fixtures"
    / "demo_cases.json"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_DIR / "outputs" / "bid_intake_retrieval_eval"
)


class _CandidatePoolCapture:
    """Capture evidence IDs returned by repository searches during one case."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.evidence_ids: list[str] = []
        self.search_call_count = 0
        self._seen: set[str] = set()

    def search(self, *args, **kwargs):
        self.search_call_count += 1
        blocks = self._delegate.search(*args, **kwargs)
        for block in blocks:
            evidence_id = str(
                getattr(block, "evidence_id", "") or ""
            ).strip()
            if not evidence_id or evidence_id in self._seen:
                continue
            self._seen.add(evidence_id)
            self.evidence_ids.append(evidence_id)
        return blocks

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate bid-intake query planning, retrieval routing and "
            "evidence recall without invoking the analysis LLM."
        )
    )
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="JSONL retrieval evaluation dataset.",
    )
    parser.add_argument(
        "--backend",
        choices=("local", "database", "predictions"),
        default="local",
        help=(
            "local uses the public fixture; database uses the current "
            "MySQL/MinIO/Milvus stack; predictions replays captured JSONL."
        ),
    )
    parser.add_argument(
        "--evidence-dataset",
        default=str(DEFAULT_EVIDENCE_DATASET),
        help="Local tender evidence fixture used by --backend local.",
    )
    parser.add_argument(
        "--predictions",
        help="Captured prediction JSONL used by --backend predictions.",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--candidate-top-k",
        type=int,
        default=None,
        help=(
            "Optional per-query candidate depth before RRF. "
            "The final result still uses --top-k."
        ),
    )
    parser.add_argument(
        "--semantic-fact-companion",
        action="store_true",
        help=(
            "Add one factual supporting query for a single-topic "
            "semantic risk question. Supporting queries are audited "
            "separately from primary query-count metrics."
        ),
    )
    parser.add_argument(
        "--atomic-fact-slots",
        action="store_true",
        help=(
            "Split compound questions into generic surface fact slots. "
            "Slot queries are audited separately from primary "
            "query-count metrics."
        ),
    )
    parser.add_argument(
        "--candidate-coverage-selection",
        action="store_true",
        help=(
            "Select final Top K by generic answer-bearing need coverage "
            "inside the existing primary-query candidate pool. "
            "This does not execute additional retrieval queries."
        ),
    )
    parser.add_argument(
        "--candidate-coverage-policy",
        choices=(
            "greedy",
            "anchor_preserving_direct_alignment",
            "predicate_aware_marginal_gain",
        ),
        default="greedy",
        help=(
            "Final Top-K coverage selection policy. Predicate-aware "
            "marginal gain preserves the shared baseline skeleton and "
            "only promotes candidates with positive relation coverage."
        ),
    )
    parser.add_argument(
        "--evidence-sufficiency-assessment",
        action="store_true",
        help=(
            "Assess direct subject-relation-answer coverage on the "
            "unchanged final evidence set. No additional retrieval or "
            "Top-K selection change is allowed."
        ),
    )
    parser.add_argument(
        "--adjacent-candidate-expansion",
        action="store_true",
        help=(
            "Expand coverage-selected evidence with same-document, "
            "same-section direct neighbors (before/after one block). "
            "This performs bounded context reads but no extra search query."
        ),
    )
    parser.add_argument(
        "--context-evidence-groups",
        action="store_true",
        help=(
            "Keep the final Top K anchors unchanged and attach at most one "
            "same-document, same-section direct neighbor to a coverage "
            "anchor as a context evidence group member."
        ),
    )
    parser.add_argument(
        "--structured-context-groups",
        action="store_true",
        help=(
            "Attach verified same-document section/table/sheet parents to "
            "existing candidates before coverage selection. This executes "
            "no additional retrieval query and does not consume Top K."
        ),
    )
    parser.add_argument(
        "--controlled-second-round",
        action="store_true",
        help=(
            "Execute at most two additional fact queries in one retry "
            "round, only when first-round fact coverage is partial."
        ),
    )
    parser.add_argument(
        "--selective-graph-expansion",
        action="store_true",
        help=(
            "For queries that explicitly name at least two document roles "
            "and a cross-source relation intent, attach at most four "
            "one-hop verified relation targets without reordering Top K."
        ),
    )
    parser.add_argument(
        "--experiment-name",
        default="baseline-v1",
    )
    parser.add_argument(
        "--change-note",
        default="建立报价资料研判Agent检索评测基线。",
    )
    parser.add_argument(
        "--baseline-report",
        help="Optional prior report JSON for an A/B comparison.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
    )
    parser.add_argument(
        "--include-excerpts",
        action="store_true",
        help=(
            "Persist retrieved source excerpts in prediction JSONL. "
            "Disabled by default to protect tender data."
        ),
    )
    args = parser.parse_args()

    cases = load_eval_cases(args.dataset)
    quality = build_dataset_quality_report(cases)
    if not quality["runnable"]:
        print(
            json.dumps(quality, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2

    if args.backend == "predictions":
        if not args.predictions:
            parser.error(
                "--predictions is required for --backend predictions"
            )
        predictions = load_predictions(args.predictions)
        backend_metadata = {
            "backend": "captured_predictions",
            "predictions_path": str(
                Path(args.predictions).resolve()
            ),
        }
    else:
        repository, backend_metadata = _repository(
            backend=args.backend,
            evidence_dataset=args.evidence_dataset,
        )
        predictions = [
            _run_case(
                case=case,
                repository=repository,
                top_k=args.top_k,
                candidate_top_k=args.candidate_top_k,
                enable_semantic_fact_companion=(
                    args.semantic_fact_companion
                ),
                enable_atomic_fact_slots=args.atomic_fact_slots,
                enable_candidate_coverage_selection=(
                    args.candidate_coverage_selection
                ),
                enable_evidence_sufficiency_assessment=(
                    args.evidence_sufficiency_assessment
                ),
                candidate_coverage_selection_policy=(
                    args.candidate_coverage_policy
                ),
                enable_adjacent_candidate_expansion=(
                    args.adjacent_candidate_expansion
                ),
                enable_context_evidence_groups=(
                    args.context_evidence_groups
                ),
                enable_structured_context_groups=(
                    args.structured_context_groups
                ),
                enable_controlled_second_round=(
                    args.controlled_second_round
                ),
                enable_selective_graph_expansion=(
                    args.selective_graph_expansion
                ),
                include_excerpts=args.include_excerpts,
            )
            for case in cases
        ]

    final_top_k = max(1, min(int(args.top_k), 20))
    configured_candidate_top_k = (
        final_top_k
        if args.candidate_top_k is None
        else int(args.candidate_top_k)
    )
    effective_candidate_top_k = min(
        max(configured_candidate_top_k, final_top_k, 5),
        20,
    )
    experiment = {
        "name": args.experiment_name,
        "change_note": args.change_note,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "backend": backend_metadata,
        "retrieval_config": {
            "top_k": final_top_k,
            "per_query_candidate_top_k": (
                effective_candidate_top_k
            ),
            "semantic_fact_companion_enabled": bool(
                args.semantic_fact_companion
            ),
            "atomic_fact_slots_enabled": bool(args.atomic_fact_slots),
            "candidate_coverage_selection_enabled": bool(
                args.candidate_coverage_selection
            ),
            "candidate_coverage_selection_policy": (
                args.candidate_coverage_policy
                if args.candidate_coverage_selection
                else "off"
            ),
            "evidence_sufficiency_assessment_enabled": bool(
                args.evidence_sufficiency_assessment
            ),
            "adjacent_candidate_expansion_enabled": bool(
                args.adjacent_candidate_expansion
            ),
            "context_evidence_groups_enabled": bool(
                args.context_evidence_groups
            ),
            "structured_context_groups_enabled": bool(
                args.structured_context_groups
            ),
            "controlled_second_round_enabled": bool(
                args.controlled_second_round
            ),
            "selective_graph_expansion_enabled": bool(
                args.selective_graph_expansion
            ),
            "retrieval_result_unit": (
                "evidence_group_v1"
            ),
            "query_planner": "tender-query-plan/v1",
            "retrieval_router": "adaptive-retrieval-route/v1",
            "embedding_model": os.environ.get(
                "EMBEDDING_MODEL_NAME",
                "maidalun1020/bce-embedding-base_v1",
            ),
            "embedding_dimension": int(
                os.environ.get(
                    "TENDER_EVIDENCE_VECTOR_DIMENSION",
                    "768",
                )
            ),
            "chunk_max_chars": 1200,
            "chunk_overlap_chars": 0,
            "fusion": "rrf",
            "reranker": "none",
            "prediction_excerpts_persisted": bool(
                args.include_excerpts
            ),
            "fact_coverage_shadow_enabled": True,
        },
    }
    baseline_report = (
        json.loads(
            Path(args.baseline_report).read_text(encoding="utf-8")
        )
        if args.baseline_report
        else None
    )
    report = evaluate_retrieval_predictions(
        cases=cases,
        predictions=predictions,
        top_k=args.top_k,
        experiment=experiment,
        baseline_report=baseline_report,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_filename(args.experiment_name)
    stem = f"{timestamp}_{safe_name}"
    predictions_path = output_dir / f"{stem}_predictions.jsonl"
    report_path = output_dir / f"{stem}_report.json"
    markdown_path = output_dir / f"{stem}_report.md"
    write_predictions(predictions_path, predictions)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_markdown_report(report),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "dataset_quality": quality,
                "overall": report["overall"],
                "predictions_path": str(predictions_path.resolve()),
                "report_path": str(report_path.resolve()),
                "markdown_path": str(markdown_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _repository(
    *,
    backend: str,
    evidence_dataset: str,
):
    if backend == "local":
        path = Path(evidence_dataset).resolve()
        return (
            LocalTenderEvidenceRepository(path),
            {
                "backend": "local_lexical_contract",
                "evidence_dataset": str(path),
                "note": (
                    "公开样例只验证评测框架、Query Planner与路由；"
                    "真实向量/BM25效果必须使用database backend。"
                ),
            },
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
    return (
        repository,
        {
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
    )


def _run_case(
    *,
    case: RetrievalEvalCase,
    repository,
    top_k: int,
    candidate_top_k: int | None = None,
    enable_semantic_fact_companion: bool = False,
    enable_atomic_fact_slots: bool = False,
    enable_candidate_coverage_selection: bool = False,
    enable_evidence_sufficiency_assessment: bool = False,
    candidate_coverage_selection_policy: str = "greedy",
    enable_adjacent_candidate_expansion: bool = False,
    enable_context_evidence_groups: bool = False,
    enable_structured_context_groups: bool = False,
    enable_controlled_second_round: bool = False,
    enable_selective_graph_expansion: bool = False,
    include_excerpts: bool = False,
) -> RetrievalPrediction:
    now = int(time.time())
    scope = TenderScope(
        case_id=case.case_id,
        assessment_id=f"eval:{case.eval_case_id}",
        agent_run_id=f"eval:{case.eval_case_id}",
        subject="bid-intake-retrieval-evaluator",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 3600,
        audience="local-evaluation",
        issuer="local-evaluation",
    )
    candidate_capture = _CandidatePoolCapture(repository)
    service = TenderEvidenceService(
        candidate_capture,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=candidate_top_k,
        enable_semantic_fact_companion=(
            enable_semantic_fact_companion
        ),
        enable_atomic_fact_slots=enable_atomic_fact_slots,
        enable_candidate_coverage_selection=(
            enable_candidate_coverage_selection
        ),
        enable_evidence_sufficiency_assessment=(
            enable_evidence_sufficiency_assessment
        ),
        candidate_coverage_selection_policy=(
            candidate_coverage_selection_policy
        ),
        enable_adjacent_candidate_expansion=(
            enable_adjacent_candidate_expansion
        ),
        enable_context_evidence_groups=(
            enable_context_evidence_groups
        ),
        enable_structured_context_groups=(
            enable_structured_context_groups
        ),
        enable_controlled_second_round=(
            enable_controlled_second_round
        ),
        enable_selective_graph_expansion=(
            enable_selective_graph_expansion
        ),
    )
    started = time.perf_counter()
    try:
        envelope = service.search_tender_evidence(
            query=case.question,
            top_k=top_k,
        )
        elapsed = round(
            (time.perf_counter() - started) * 1000
        )
        data = envelope.data if isinstance(envelope.data, dict) else {}
        fact_coverage = build_fact_coverage_state(
            [
                ToolMessage(
                    content=json.dumps(
                        envelope.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    tool_call_id=(
                        f"eval-search:{case.eval_case_id}"
                    ),
                    name="search_tender_evidence",
                )
            ],
            mode=FactCoverageMode.SHADOW,
        )
        matches = (
            data.get("matches")
            if isinstance(data.get("matches"), list)
            else []
        )
        evidence_ids = []
        evidence_groups: list[RetrievalEvidenceGroup] = []
        excerpts = {}
        for item in matches:
            if not isinstance(item, dict):
                continue
            evidence_ref = (
                item.get("evidence_ref")
                if isinstance(item.get("evidence_ref"), dict)
                else {}
            )
            evidence_id = str(
                evidence_ref.get("evidence_id") or ""
            ).strip()
            if not evidence_id:
                continue
            evidence_ids.append(evidence_id)
            raw_group = (
                item.get("context_evidence_group")
                if isinstance(
                    item.get("context_evidence_group"),
                    dict,
                )
                else {}
            )
            raw_members = (
                raw_group.get("members")
                if isinstance(raw_group.get("members"), list)
                else []
            )
            context_evidence_ids: list[str] = []
            for member in raw_members:
                if not isinstance(member, dict):
                    continue
                member_ref = (
                    member.get("evidence_ref")
                    if isinstance(member.get("evidence_ref"), dict)
                    else {}
                )
                member_id = str(
                    member_ref.get("evidence_id") or ""
                ).strip()
                if (
                    not member_id
                    or member_id == evidence_id
                    or member_id in context_evidence_ids
                ):
                    continue
                context_evidence_ids.append(member_id)
                if include_excerpts:
                    excerpts[member_id] = str(
                        member.get("excerpt") or ""
                    )[:500]
            evidence_groups.append(
                RetrievalEvidenceGroup(
                    anchor_evidence_id=evidence_id,
                    context_evidence_ids=context_evidence_ids,
                )
            )
            if include_excerpts:
                excerpts[evidence_id] = str(
                    item.get("excerpt") or ""
                )[:500]
        return RetrievalPrediction(
            eval_case_id=case.eval_case_id,
            returned_evidence_ids=evidence_ids,
            returned_evidence_groups=evidence_groups,
            candidate_pool_captured=True,
            candidate_pool_evidence_ids=(
                candidate_capture.evidence_ids
            ),
            candidate_pool_search_call_count=(
                candidate_capture.search_call_count
            ),
            returned_excerpts=excerpts,
            query_plan=sanitize_query_plan_for_evaluation(
                data.get("query_plan")
                if isinstance(data.get("query_plan"), dict)
                else {}
            ),
            fact_coverage=(
                sanitize_fact_coverage_for_evaluation(
                    fact_coverage
                )
            ),
            result_status=str(envelope.status.value),
            latency_ms=elapsed,
            error_code=envelope.error_code,
        )
    except Exception as exc:
        elapsed = round(
            (time.perf_counter() - started) * 1000
        )
        return RetrievalPrediction(
            eval_case_id=case.eval_case_id,
            candidate_pool_captured=True,
            candidate_pool_evidence_ids=(
                candidate_capture.evidence_ids
            ),
            candidate_pool_search_call_count=(
                candidate_capture.search_call_count
            ),
            result_status="failed",
            latency_ms=elapsed,
            error_code=type(exc).__name__,
        )


def _safe_filename(value: str) -> str:
    normalized = "".join(
        char
        if char.isalnum() or char in {"-", "_"}
        else "-"
        for char in str(value or "experiment")
    ).strip("-")
    return normalized[:80] or "experiment"


if __name__ == "__main__":
    raise SystemExit(main())
