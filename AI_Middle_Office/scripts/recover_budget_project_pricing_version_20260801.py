"""Recover one quote draft damaged by the 2026-07-31 version activation bug.

The command is dry-run by default.  ``--apply --rollback`` executes every
mutation and validation inside one transaction, then rolls it back.  A real
recovery requires ``--apply`` without ``--rollback`` after a database backup.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import text

import app.models.registry  # noqa: F401 - register all SQLAlchemy relationships
from app.core.database import SessionLocal
from app.models.budget_pricing import BudgetProjectPricingRun
from app.models.budget_pricing_draft import (
    PRICING_MODE_ENTERPRISE_AI,
    BudgetProjectPricingDraft,
    BudgetProjectPricingDraftEvent,
    BudgetProjectPricingDraftLine,
)
from app.models.budget_project import BudgetProjectImportBatch, BudgetProjectProfile
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.budget_pricing_drafts import (
    capture_budget_pricing_run_draft_snapshot,
    patch_budget_pricing_draft_line,
    patch_budget_pricing_draft_line_construction_note,
)
from app.services.quote_budget_workspace import _quote_payload, _sync_pricing_draft


RECOVERY_REASON = "restore_version_switch_incident_20260801"


def _json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def _event_payload(event: BudgetProjectPricingDraftEvent) -> dict[str, Any]:
    return _json(event.event_json)


def _latest_events_by_legacy_line(
    events: list[BudgetProjectPricingDraftEvent],
    event_type: str,
) -> list[tuple[int, dict[str, Any]]]:
    latest: dict[int, dict[str, Any]] = {}
    for event in events:
        if event.event_type != event_type:
            continue
        payload = _event_payload(event)
        line_id = int(payload.get("line_id") or 0)
        if line_id <= 0:
            raise RuntimeError(f"{event_type} event {event.id} has no legacy line_id")
        latest[line_id] = payload
    return sorted(latest.items())


def _summary(draft: BudgetProjectPricingDraft) -> dict[str, Any]:
    return _json(draft.summary_json)


def _assert_decimal(actual: Any, expected: str, label: str) -> None:
    if Decimal(str(actual or 0)).quantize(Decimal("0.000001")) != Decimal(expected):
        raise RuntimeError(f"{label} mismatch: expected {expected}, got {actual}")


def _validate_summary(
    draft: BudgetProjectPricingDraft,
    *,
    expected_row_count: int,
    expected_subtotal: str,
) -> dict[str, Any]:
    summary = _summary(draft)
    expected = {
        "row_count": expected_row_count,
        "unit_priced_count": expected_row_count,
        "pending_count": 0,
        "manual_price_count": 6,
        "ai_estimate_count": 29,
        "enterprise_quota_matched_count": 98,
    }
    for key, expected_value in expected.items():
        actual = int(summary.get(key) or 0)
        if actual != expected_value:
            raise RuntimeError(
                f"summary {key} mismatch: expected {expected_value}, got {actual}"
            )
    _assert_decimal(summary.get("priced_subtotal"), expected_subtotal, "priced_subtotal")
    _assert_decimal(
        (summary.get("totals") or {}).get("quote_amount"),
        expected_subtotal,
        "quote_amount",
    )
    return summary


def _target_line(
    lines: list[BudgetProjectPricingDraftLine],
    *,
    legacy_line_id: int,
    legacy_line_id_base: int,
) -> BudgetProjectPricingDraftLine:
    index = legacy_line_id - legacy_line_id_base
    if index < 0 or index >= len(lines):
        raise RuntimeError(
            f"legacy line {legacy_line_id} is outside recovery range "
            f"{legacy_line_id_base}..{legacy_line_id_base + len(lines) - 1}"
        )
    return lines[index]


def _load_context(db, args):
    profile = (
        db.query(BudgetProjectProfile)
        .filter(BudgetProjectProfile.project_id == args.project_id)
        .one()
    )
    draft = (
        db.query(BudgetProjectPricingDraft)
        .filter(
            BudgetProjectPricingDraft.id == args.draft_id,
            BudgetProjectPricingDraft.project_id == args.project_id,
            BudgetProjectPricingDraft.pricing_mode == PRICING_MODE_ENTERPRISE_AI,
        )
        .one()
    )
    actor = db.query(User).filter(User.id == args.actor_user_id).one()
    job = db.query(QuoteJob).filter(QuoteJob.job_id == args.quote_job_id).one()
    batch = (
        db.query(BudgetProjectImportBatch)
        .filter(
            BudgetProjectImportBatch.id == draft.source_import_batch_id,
            BudgetProjectImportBatch.project_id == args.project_id,
        )
        .one()
    )
    runs = (
        db.query(BudgetProjectPricingRun)
        .filter(
            BudgetProjectPricingRun.project_id == args.project_id,
            BudgetProjectPricingRun.id.in_(args.run_ids),
        )
        .order_by(BudgetProjectPricingRun.run_number)
        .all()
    )
    if len(runs) != len(set(args.run_ids)):
        raise RuntimeError("one or more pricing run ids are missing")
    events = (
        db.query(BudgetProjectPricingDraftEvent)
        .filter(
            BudgetProjectPricingDraftEvent.draft_id == draft.id,
            BudgetProjectPricingDraftEvent.id <= args.event_cutoff_id,
        )
        .order_by(BudgetProjectPricingDraftEvent.id)
        .all()
    )
    payload, rows = _quote_payload(db, job)
    if len(rows) != args.expected_row_count:
        raise RuntimeError(
            f"quote job row count mismatch: expected {args.expected_row_count}, got {len(rows)}"
        )
    if int(job.budget_project_id or 0) != args.project_id:
        raise RuntimeError("quote job is not linked to the requested budget project")
    return profile, draft, actor, job, batch, runs, events, payload, rows


def _dry_run_report(db, args) -> dict[str, Any]:
    profile, draft, actor, job, batch, runs, events, payload, rows = _load_context(db, args)
    manual = _latest_events_by_legacy_line(events, "manual_price_updated")
    notes = _latest_events_by_legacy_line(events, "construction_note_updated")
    return {
        "mode": "dry_run",
        "project_id": profile.project_id,
        "draft_id": draft.id,
        "current_revision": draft.revision,
        "current_row_count": draft.row_count,
        "current_priced_count": draft.priced_count,
        "current_pending_count": draft.pending_count,
        "current_priced_subtotal": str(draft.priced_subtotal),
        "source_import_batch_id": batch.id,
        "source_import_revision_id": draft.source_import_revision_id,
        "quote_job_id": job.job_id,
        "quote_job_status": job.status,
        "quote_job_rows": len(rows),
        "quote_job_total": payload.get("total_price"),
        "manual_event_lines": [line_id for line_id, _ in manual],
        "construction_note_lines": [line_id for line_id, _ in notes],
        "actor_user_id": actor.id,
        "run_ids": [run.id for run in runs],
        "expected_subtotal": args.expected_subtotal,
    }


def _apply_recovery(db, args) -> dict[str, Any]:
    profile, draft, actor, job, batch, runs, events, _payload, rows = _load_context(db, args)
    if draft.revision != args.expected_current_revision:
        raise RuntimeError(
            f"current draft revision changed: expected {args.expected_current_revision}, "
            f"got {draft.revision}"
        )
    _assert_decimal(
        draft.priced_subtotal,
        args.expected_current_subtotal,
        "current priced_subtotal",
    )
    if db.query(BudgetProjectPricingRun).filter(
        BudgetProjectPricingRun.id.in_(args.run_ids),
        BudgetProjectPricingRun.draft_snapshot.has(),
    ).count():
        raise RuntimeError("one or more target runs already have an immutable draft snapshot")

    restored_draft, _ = _sync_pricing_draft(
        db,
        profile,
        batch,
        actor,
        job=job,
        rows=rows,
    )
    current_lines = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == restored_draft.id)
        .order_by(
            BudgetProjectPricingDraftLine.source_sort_order,
            BudgetProjectPricingDraftLine.id,
        )
        .all()
    )
    if len(current_lines) != args.expected_row_count:
        raise RuntimeError("restored draft line count mismatch")

    for legacy_line_id, payload in _latest_events_by_legacy_line(
        events,
        "construction_note_updated",
    ):
        target = _target_line(
            current_lines,
            legacy_line_id=legacy_line_id,
            legacy_line_id_base=args.legacy_line_id_base,
        )
        restored_draft, _ = patch_budget_pricing_draft_line_construction_note(
            db,
            profile,
            actor,
            pricing_mode=PRICING_MODE_ENTERPRISE_AI,
            line_identifier=target.id,
            expected_revision=restored_draft.revision,
            expected_line_revision=target.line_revision,
            remark=payload.get("remark"),
            reason=RECOVERY_REASON,
        )

    for legacy_line_id, payload in _latest_events_by_legacy_line(
        events,
        "manual_price_updated",
    ):
        target = _target_line(
            current_lines,
            legacy_line_id=legacy_line_id,
            legacy_line_id_base=args.legacy_line_id_base,
        )
        restored_draft, _ = patch_budget_pricing_draft_line(
            db,
            profile,
            actor,
            pricing_mode=PRICING_MODE_ENTERPRISE_AI,
            line_identifier=target.id,
            expected_revision=restored_draft.revision,
            expected_line_revision=target.line_revision,
            manual_unit_price=payload.get("manual_unit_price"),
            pricing_breakdown=payload.get("pricing_breakdown"),
            reason=RECOVERY_REASON,
        )

    db.flush()
    db.refresh(restored_draft)
    summary = _validate_summary(
        restored_draft,
        expected_row_count=args.expected_row_count,
        expected_subtotal=args.expected_subtotal,
    )
    snapshots = [
        capture_budget_pricing_run_draft_snapshot(
            db,
            profile,
            actor,
            run,
        )
        for run in runs
    ]
    db.flush()
    return {
        "mode": "rollback_validation" if args.rollback else "applied",
        "project_id": profile.project_id,
        "draft_id": restored_draft.id,
        "restored_revision": restored_draft.revision,
        "row_count": restored_draft.row_count,
        "priced_count": restored_draft.priced_count,
        "pending_count": restored_draft.pending_count,
        "manual_price_count": restored_draft.manual_price_count,
        "ai_estimate_count": int(summary.get("ai_estimate_count") or 0),
        "enterprise_quota_matched_count": int(
            summary.get("enterprise_quota_matched_count") or 0
        ),
        "priced_subtotal": str(restored_draft.priced_subtotal),
        "tax_included_total": (summary.get("totals") or {}).get("tax_included_total"),
        "snapshots": [
            {
                "run_id": snapshot.run_id,
                "row_count": snapshot.row_count,
                "snapshot_sha256": snapshot.snapshot_sha256,
            }
            for snapshot in snapshots
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--draft-id", type=int, required=True)
    parser.add_argument("--quote-job-id", required=True)
    parser.add_argument("--actor-user-id", type=int, required=True)
    parser.add_argument("--run-ids", type=int, nargs="+", required=True)
    parser.add_argument("--event-cutoff-id", type=int, required=True)
    parser.add_argument("--legacy-line-id-base", type=int, required=True)
    parser.add_argument("--expected-row-count", type=int, required=True)
    parser.add_argument("--expected-current-revision", type=int, required=True)
    parser.add_argument("--expected-current-subtotal", required=True)
    parser.add_argument("--expected-subtotal", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.rollback and not args.apply:
        raise SystemExit("--rollback requires --apply")
    db = SessionLocal()
    try:
        report = _apply_recovery(db, args) if args.apply else _dry_run_report(db, args)
        if args.apply and args.rollback:
            db.rollback()
        elif args.apply:
            db.commit()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
