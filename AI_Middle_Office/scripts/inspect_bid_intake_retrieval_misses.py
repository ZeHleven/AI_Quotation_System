from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
load_dotenv(PROJECT_DIR / ".env")

from app.agents.bid_intake.retrieval_evaluation import (  # noqa: E402
    load_eval_cases,
)
from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.tender_evidence import (  # noqa: E402
    BidEvidenceBlock,
    BidEvidenceDocument,
)
from mcp_servers.tender_evidence.hybrid_client import (  # noqa: E402
    configured_hybrid_client,
    hybrid_search_enabled,
)
from mcp_servers.tender_evidence.query_planner import (  # noqa: E402
    plan_tender_query,
)
from mcp_servers.tender_evidence.retrieval_router import (  # noqa: E402
    route_tender_query,
)
from mcp_servers.tender_evidence.sqlalchemy_repository import (  # noqa: E402
    SqlAlchemyTenderEvidenceRepository,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Map retrieval misses to active document roles and source "
            "locations for evidence-backed experiment analysis."
        )
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--candidate-top-k", type=int, default=20)
    return parser.parse_args()


def _counts(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    cases = {
        item.eval_case_id: item
        for item in load_eval_cases(Path(args.dataset).resolve())
    }
    report = json.loads(
        Path(args.report).resolve().read_text(encoding="utf-8")
    )
    case_results = {
        str(item["eval_case_id"]): item
        for item in report.get("case_results") or []
    }
    if set(cases) != set(case_results):
        raise RuntimeError("dataset/report case IDs do not match")

    evidence_ids: set[str] = set()
    for case in cases.values():
        evidence_ids.update(
            item.evidence_id for item in case.gold_evidence
        )
    for result in case_results.values():
        evidence_ids.update(result.get("returned_evidence_ids") or [])

    db = SessionLocal()
    try:
        rows = (
            db.query(BidEvidenceBlock, BidEvidenceDocument)
            .join(
                BidEvidenceDocument,
                BidEvidenceDocument.id == BidEvidenceBlock.document_id,
            )
            .filter(BidEvidenceBlock.evidence_id.in_(evidence_ids))
            .all()
        )
    finally:
        db.close()
    evidence = {
        block.evidence_id: {
            "evidence_id": block.evidence_id,
            "document_key": document.document_key,
            "document_type": document.document_type,
            "document_version": document.version_no,
            "document_active": bool(document.active),
            "block_order": block.block_order,
            "section": block.section,
            "sheet": block.sheet,
            "cell_range": block.cell_range,
            "source_location": str(
                (
                    json.loads(block.locator_json or "{}")
                    if block.locator_json
                    else {}
                ).get("source_location")
                or ""
            ).strip()
            or None,
        }
        for block, document in rows
    }
    missing_metadata = sorted(evidence_ids - set(evidence))
    if missing_metadata:
        raise RuntimeError(
            "evidence metadata is missing: "
            + ", ".join(missing_metadata)
        )

    repository = SqlAlchemyTenderEvidenceRepository(
        SessionLocal,
        hybrid_search_client=(
            configured_hybrid_client()
            if hybrid_search_enabled()
            else None
        ),
    )
    case_ids = sorted({item.case_id for item in cases.values()})
    for case_id in case_ids:
        contexts = repository.get_structural_context(
            case_id=case_id,
            evidence_ids=sorted(evidence_ids),
            max_heading_lookback=12,
        )
        for evidence_id, parents in contexts.items():
            evidence[evidence_id]["structural_parents"] = [
                {
                    "relation": item.relation,
                    "evidence_id": item.evidence_ref.evidence_id,
                }
                for item in parents
            ]
    for item in evidence.values():
        item.setdefault("structural_parents", [])
    output_cases = []
    for eval_case_id, case in cases.items():
        result = case_results[eval_case_id]
        gold_ids = [
            item.evidence_id for item in case.gold_evidence
        ]
        returned_ids = list(
            result.get("returned_evidence_ids") or []
        )
        returned_gold_ids = [
            item for item in returned_ids if item in gold_ids
        ]
        missing_gold_ids = [
            item for item in gold_ids if item not in returned_ids
        ]
        candidate_sources: dict[str, list[dict[str, object]]] = {}
        plan = plan_tender_query(case.question)
        for query_index, planned_query in enumerate(plan.queries, start=1):
            route = route_tender_query(planned_query)
            blocks = repository.search(
                case_id=case.case_id,
                query=planned_query,
                top_k=max(1, min(args.candidate_top_k, 20)),
                search_mode=route.mode,
            )
            executed_mode = route.mode
            if not blocks and route.fallback_mode is not None:
                executed_mode = route.fallback_mode
                blocks = repository.search(
                    case_id=case.case_id,
                    query=planned_query,
                    top_k=max(1, min(args.candidate_top_k, 20)),
                    search_mode=route.fallback_mode,
                )
            for rank, block in enumerate(blocks, start=1):
                candidate_sources.setdefault(
                    block.evidence_id,
                    [],
                ).append(
                    {
                        "query_index": query_index,
                        "rank": rank,
                        "mode": executed_mode,
                    }
                )
        gold_in_candidate_pool = [
            {
                **evidence[item],
                "candidate_sources": candidate_sources.get(item, []),
            }
            for item in gold_ids
            if item in candidate_sources
        ]
        output_cases.append(
            {
                "eval_case_id": eval_case_id,
                "graph_trigger_expected": (
                    "graph_trigger_expected" in case.tags
                ),
                "recall_at_k": result.get("recall_at_k"),
                "gold_document_type_counts": _counts(
                    [
                        evidence[item]["document_type"]
                        for item in gold_ids
                    ]
                ),
                "returned_document_type_counts": _counts(
                    [
                        evidence[item]["document_type"]
                        for item in returned_ids
                    ]
                ),
                "returned_gold_document_type_counts": _counts(
                    [
                        evidence[item]["document_type"]
                        for item in returned_gold_ids
                    ]
                ),
                "returned": [
                    {
                        **evidence[item],
                        "is_gold": item in gold_ids,
                    }
                    for item in returned_ids
                ],
                "gold_in_candidate_pool_count": len(
                    gold_in_candidate_pool
                ),
                "gold_in_candidate_pool": gold_in_candidate_pool,
                "missing_gold": [
                    evidence[item] for item in missing_gold_ids
                ],
            }
        )

    print(
        json.dumps(
            {
                "schema_version": (
                    "bid_intake_retrieval_miss_inspection_v1"
                ),
                "dataset_fingerprint": report.get(
                    "dataset_fingerprint"
                ),
                "report_experiment": (
                    report.get("experiment") or {}
                ).get("name"),
                "cases": output_cases,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
