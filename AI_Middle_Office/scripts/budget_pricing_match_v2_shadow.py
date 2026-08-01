from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

import app.main  # noqa: F401,E402  # Register the complete SQLAlchemy model graph.
from app.core.database import SessionLocal  # noqa: E402
from app.models.budget_project import BudgetProjectImportRevision, BudgetProjectProfile  # noqa: E402
from app.models.project_progress import Project  # noqa: E402
from app.models.quote_job import QuoteJob  # noqa: E402
from app.services.budget_pricing import (  # noqa: E402
    _load_quota_catalog,
    _source_values,
    strict_active_quota_version,
)
from app.services.budget_pricing_match_v2_shadow import (  # noqa: E402
    SHADOW_DECISION_AUTO,
    SHADOW_DECISION_NONE,
    SHADOW_DECISION_REVIEW,
    SHADOW_MATCHING_ENGINE_VERSION,
    serialize_shadow_candidate,
    shadow_match_source,
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _parse_json(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _actual_row_key(row: dict[str, Any]) -> tuple[str, int]:
    return str(row.get("source_sheet") or ""), int(row.get("raw_row_index") or 0)


def _enterprise_attempt(row: dict[str, Any] | None) -> dict[str, Any]:
    for attempt in (row or {}).get("pricing_match_attempts") or []:
        if attempt.get("tier") == "enterprise_quota":
            return dict(attempt)
    return {"status": "unmatched", "rule": "missing_actual_attempt", "candidate_ids": []}


def _candidate_label(candidate: dict[str, Any] | None) -> str | None:
    if not candidate:
        return None
    return " / ".join(
        part
        for part in (
            str(candidate.get("quota_code") or "").strip(),
            str(candidate.get("quota_name") or "").strip(),
            str(candidate.get("quota_unit") or "").strip(),
        )
        if part
    )


def _change_label(old_status: str, decision: str) -> str:
    if old_status == "auto_matched" and decision == SHADOW_DECISION_AUTO:
        return "保持自动命中"
    if old_status != "auto_matched" and decision == SHADOW_DECISION_AUTO:
        return "新增自动建议"
    if old_status != "auto_matched" and decision == SHADOW_DECISION_REVIEW:
        return "新增人工复核候选"
    if old_status == "auto_matched" and decision != SHADOW_DECISION_AUTO:
        return "原命中降级复核"
    return "仍未形成建议"


def build_shadow_report(*, project_id: int, quote_job_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        profile = (
            db.query(BudgetProjectProfile)
            .filter(BudgetProjectProfile.project_id == project_id)
            .one_or_none()
        )
        if project is None or profile is None or not profile.active_import_revision_id:
            raise RuntimeError("BUDGET_PROJECT_ACTIVE_REVISION_REQUIRED")
        revision = db.get(BudgetProjectImportRevision, int(profile.active_import_revision_id))
        if revision is None:
            raise RuntimeError("BUDGET_PROJECT_ACTIVE_REVISION_NOT_FOUND")
        quota_version = strict_active_quota_version(db)
        catalog, catalog_stats = _load_quota_catalog(db, quota_version)
        catalog_by_id = {int(entry.item_id): entry for entry in catalog}
        quote_job = db.get(QuoteJob, quote_job_id)
        if quote_job is None:
            raise RuntimeError("QUOTE_JOB_NOT_FOUND")

        stored_rows = _parse_json(revision.standard_rows_json, [])
        formal_rows = [
            row
            for row in stored_rows
            if isinstance(row, dict)
            and row.get("sheet_role") == "bill"
            and row.get("is_standard_item") is True
        ]
        quote_result = _parse_json(quote_job.result_json, {})
        actual_rows = {
            _actual_row_key(row): row
            for row in quote_result.get("project_details") or []
            if isinstance(row, dict)
        }

        detail_rows: list[dict[str, Any]] = []
        old_status_counts: Counter[str] = Counter()
        decision_counts: Counter[str] = Counter()
        change_counts: Counter[str] = Counter()
        sheet_counts: Counter[str] = Counter()
        parser_issue_counts: Counter[str] = Counter()

        for stored_row in formal_rows:
            source = _source_values(stored_row)
            actual = actual_rows.get((source["source_sheet"], source["raw_row_index"]))
            attempt = _enterprise_attempt(actual)
            old_status = str(attempt.get("status") or "unmatched")
            old_candidate_ids = [
                int(value)
                for value in attempt.get("candidate_ids") or []
                if str(value).isdigit()
            ]
            old_candidates = [
                {
                    "quota_item_id": item_id,
                    "quota_code": catalog_by_id[item_id].quota_code,
                    "quota_name": catalog_by_id[item_id].item_name,
                    "quota_unit": catalog_by_id[item_id].unit,
                }
                for item_id in old_candidate_ids
                if item_id in catalog_by_id
            ]

            shadow = shadow_match_source(source, catalog)
            recommended = serialize_shadow_candidate(shadow.get("recommended"))
            candidates = [
                serialize_shadow_candidate(candidate)
                for candidate in shadow.get("candidates") or []
            ]
            decision = str(shadow["decision"])
            change = _change_label(old_status, decision)
            actual_unit = str((actual or {}).get("unit") or "")
            formal_unit = str(source.get("unit") or "")
            parser_issue = None
            if formal_unit and not actual_unit:
                parser_issue = "报价入口单位丢失"
            elif formal_unit != actual_unit:
                parser_issue = "报价入口单位不一致"

            old_status_counts[old_status] += 1
            decision_counts[decision] += 1
            change_counts[change] += 1
            sheet_counts[source["source_sheet"]] += 1
            if parser_issue:
                parser_issue_counts[parser_issue] += 1

            detail_rows.append(
                {
                    "sequence": len(detail_rows) + 1,
                    "source_sheet": source["source_sheet"],
                    "source_row": source["raw_row_index"],
                    "item_name": source["item_name"],
                    "spec": source["spec"],
                    "formal_unit": formal_unit,
                    "quote_unit": actual_unit,
                    "quantity": str(source.get("quantity") or "0"),
                    "parser_issue": parser_issue,
                    "old_status": old_status,
                    "old_rule": attempt.get("rule"),
                    "old_candidate_count": len(old_candidates),
                    "old_candidates": old_candidates,
                    "old_candidate_labels": "；".join(
                        label
                        for candidate in old_candidates
                        if (label := _candidate_label(candidate))
                    ),
                    "v2_decision": decision,
                    "v2_rule": shadow.get("rule"),
                    "v2_top_score": str(shadow.get("top_score") or "0"),
                    "v2_runner_up_score": str(shadow.get("runner_up_score") or "0"),
                    "v2_margin": str(shadow.get("margin") or "0"),
                    "v2_recommended": recommended,
                    "v2_recommended_label": _candidate_label(recommended),
                    "v2_candidates": candidates,
                    "v2_candidate_labels": "；".join(
                        f'{candidate.get("quota_code") or ""} / '
                        f'{candidate.get("quota_name") or ""} / '
                        f'{candidate.get("quota_unit") or ""} / '
                        f'{candidate.get("score") or "0"}'
                        for candidate in candidates
                        if candidate
                    ),
                    "v2_reason": recommended.get("reason") if recommended else None,
                    "v2_risk_flags": "；".join(recommended.get("risk_flags") or []) if recommended else None,
                    "change": change,
                    "business_confirmation": None,
                    "confirmed_quota_code": None,
                    "business_note": None,
                }
            )

        row_count = len(detail_rows)
        old_matched = int(old_status_counts.get("auto_matched", 0))
        v2_auto = int(decision_counts.get(SHADOW_DECISION_AUTO, 0))
        v2_review = int(decision_counts.get(SHADOW_DECISION_REVIEW, 0))
        v2_covered = v2_auto + v2_review
        return {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "engine_version": SHADOW_MATCHING_ENGINE_VERSION,
                "mode": "read_only_shadow",
                "project_id": int(project.id),
                "project_code": project.project_code,
                "project_name": project.name,
                "quote_job_id": quote_job.job_id,
                "source_filename": quote_job.file_name,
                "source_import_revision_id": int(revision.id),
                "enterprise_quota_version_id": int(quota_version.id),
                "enterprise_quota_version_code": quota_version.version_code,
                "enterprise_quota_version_name": quota_version.version_name,
            },
            "summary": {
                "row_count": row_count,
                "sheet_counts": dict(sheet_counts),
                "old_status_counts": dict(old_status_counts),
                "v2_decision_counts": dict(decision_counts),
                "change_counts": dict(change_counts),
                "parser_issue_counts": dict(parser_issue_counts),
                "old_auto_match_count": old_matched,
                "old_auto_match_rate": old_matched / row_count if row_count else 0,
                "v2_auto_recommendation_count": v2_auto,
                "v2_auto_recommendation_rate": v2_auto / row_count if row_count else 0,
                "v2_review_recommendation_count": v2_review,
                "v2_review_recommendation_rate": v2_review / row_count if row_count else 0,
                "v2_candidate_coverage_count": v2_covered,
                "v2_candidate_coverage_rate": v2_covered / row_count if row_count else 0,
                "target_50_percent_count": (row_count + 1) // 2,
                "target_candidate_coverage_reached": v2_covered >= (row_count + 1) // 2,
                "business_accuracy_pending": True,
                "catalog": catalog_stats,
            },
            "rows": detail_rows,
        }
    finally:
        db.rollback()
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the enterprise quota V2 matcher in read-only shadow mode.")
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--quote-job-id", type=int, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()

    report = build_shadow_report(project_id=args.project_id, quote_job_id=args.quote_job_id)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, default=_json_default))
    print(str(args.output_json.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
