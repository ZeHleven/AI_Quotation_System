from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.main  # noqa: F401 - register all SQLAlchemy models
from app.core.database import SessionLocal
from app.services.pricing_agent_retrieval import (
    RetrievalSourceResult,
    retrieve_archive,
    retrieve_enterprise,
)
from app.services.pricing_archive_parser import normalize_text


DEFAULT_CASES = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "pricing_agent"
    / "v1_1"
    / "regression_cases.json"
)


def _run_case(
    db,
    *,
    account_id: int,
    case: dict[str, Any],
) -> RetrievalSourceResult:
    query = {
        "row_key": case["case_id"],
        **case["query"],
    }
    context = {
        "city": "杭州市",
        "project_type": "内部回归",
        "decoration_level": "标准",
    }
    expanded = case["category"] != "exact"
    if case["source"] == "archive":
        return retrieve_archive(
            db,
            account_id=account_id,
            query=query,
            context=context,
            expanded=expanded,
        )
    return retrieve_enterprise(
        db,
        query=query,
        context=context,
        expanded=expanded,
    )


def run_evaluation(
    *,
    account_id: int,
    cases_path: Path,
) -> dict[str, Any]:
    contract = json.loads(cases_path.read_text(encoding="utf-8"))
    details: list[dict[str, Any]] = []
    exact_total = 0
    exact_passed = 0
    approximate_total = 0
    approximate_hit = 0
    approximate_auto_adoption_count = 0
    unrelated_total = 0
    unrelated_candidate_count = 0
    unrelated_auto_pricing_count = 0

    with SessionLocal() as db:
        for case in contract["cases"]:
            result = _run_case(
                db,
                account_id=account_id,
                case=case,
            )
            candidate_names = [
                str(item.get("item_name") or "")
                for item in result.candidates[:5]
            ]
            selected_name = (
                str(result.selected.get("item_name") or "")
                if result.selected
                else None
            )
            category = case["category"]
            passed = False
            if category == "exact":
                exact_total += 1
                passed = normalize_text(selected_name) == normalize_text(
                    case.get("expected_name")
                )
                exact_passed += int(passed)
            elif category == "approximate":
                approximate_total += 1
                expected = normalize_text(case.get("expected_name"))
                passed = any(
                    normalize_text(name) == expected
                    for name in candidate_names
                )
                approximate_hit += int(passed)
                approximate_auto_adoption_count += int(result.selected is not None)
            else:
                unrelated_total += 1
                forbidden = normalize_text(case.get("forbidden_name_contains"))
                forbidden_hit = bool(
                    forbidden
                    and any(
                        forbidden in normalize_text(name)
                        for name in candidate_names
                    )
                )
                passed = not candidate_names and not forbidden_hit
                unrelated_candidate_count += len(candidate_names)
                unrelated_auto_pricing_count += int(result.selected is not None)

            details.append(
                {
                    "case_id": case["case_id"],
                    "category": category,
                    "passed": passed,
                    "selected_name": selected_name,
                    "candidate_names": candidate_names,
                    "channel_status": result.channel_status,
                    "source_issue": result.source_issue,
                }
            )

    metrics = {
        "exact_accuracy": (
            exact_passed / exact_total
            if exact_total
            else 1.0
        ),
        "approximate_recall_at_5": (
            approximate_hit / approximate_total
            if approximate_total
            else 1.0
        ),
        "approximate_auto_adoption_count": approximate_auto_adoption_count,
        "unrelated_candidate_count": unrelated_candidate_count,
        "unrelated_auto_pricing_count": unrelated_auto_pricing_count,
    }
    targets = contract["targets"]
    gates = {
        "exact_accuracy": (
            metrics["exact_accuracy"]
            >= float(targets["exact_accuracy"])
        ),
        "approximate_recall_at_5": (
            metrics["approximate_recall_at_5"]
            >= float(targets["approximate_recall_at_5"])
        ),
        "approximate_auto_adoption_count": (
            metrics["approximate_auto_adoption_count"]
            <= int(targets["approximate_auto_adoption_count"])
        ),
        "unrelated_auto_pricing_count": (
            metrics["unrelated_auto_pricing_count"]
            <= int(targets["unrelated_auto_pricing_count"])
        ),
    }
    return {
        "version": contract["version"],
        "account_id": account_id,
        "metrics": metrics,
        "targets": targets,
        "gates": gates,
        "passed": all(gates.values()),
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Pricing Agent v1.1 exact/near/unrelated regression.",
    )
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_evaluation(
        account_id=args.account_id,
        cases_path=args.cases,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
