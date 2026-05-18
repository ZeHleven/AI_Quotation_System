from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import SessionLocal
from app.models.quote_job import QuoteJob


SHIFT_HOURS = 8


def _ms(delta) -> int:
    return int(round(delta.total_seconds() * 1000))


def _summary(values: Iterable[int]) -> dict:
    values = list(values)
    if not values:
        return {"count": 0, "min_ms": None, "avg_ms": None, "max_ms": None}
    return {
        "count": len(values),
        "min_ms": min(values),
        "avg_ms": int(round(mean(values))),
        "max_ms": max(values),
    }


def _candidate_delta_ms(job: QuoteJob, max_safe_ms: int) -> int | None:
    if job.status != "succeeded":
        return None
    if job.duration_ms not in (None, 0):
        return None
    if not job.created_at or not job.finished_at:
        return None
    shifted_delta_ms = _ms((job.finished_at + timedelta(hours=SHIFT_HOURS)) - job.created_at)
    if 0 < shifted_delta_ms <= max_safe_ms:
        return shifted_delta_ms
    return None


def diagnose(max_safe_hours: float, sample_limit: int) -> dict:
    db = SessionLocal()
    try:
        jobs = db.query(QuoteJob).order_by(QuoteJob.created_at.asc(), QuoteJob.id.asc()).all()
    finally:
        db.close()

    by_status: dict[str, dict] = {}
    raw_positive: list[int] = []
    raw_negative: list[int] = []
    shifted_positive: list[int] = []
    repairable_examples: list[dict] = []
    missing_timestamps = 0
    zero_or_missing_succeeded = 0
    nonzero_succeeded = 0
    max_safe_ms = int(max_safe_hours * 60 * 60 * 1000)

    for job in jobs:
        status_bucket = by_status.setdefault(
            job.status or "unknown",
            {"count": 0, "duration_null": 0, "duration_zero": 0, "duration_positive": 0},
        )
        status_bucket["count"] += 1
        if job.duration_ms is None:
            status_bucket["duration_null"] += 1
        elif job.duration_ms == 0:
            status_bucket["duration_zero"] += 1
        elif job.duration_ms > 0:
            status_bucket["duration_positive"] += 1

        if job.status == "succeeded":
            if job.duration_ms is None or job.duration_ms == 0:
                zero_or_missing_succeeded += 1
            elif job.duration_ms > 0:
                nonzero_succeeded += 1

        if not job.created_at or not job.finished_at:
            missing_timestamps += 1
            continue

        raw_delta_ms = _ms(job.finished_at - job.created_at)
        if raw_delta_ms >= 0:
            raw_positive.append(raw_delta_ms)
            continue

        raw_negative.append(raw_delta_ms)
        shifted_delta_ms = _ms((job.finished_at + timedelta(hours=SHIFT_HOURS)) - job.created_at)
        if shifted_delta_ms > 0:
            shifted_positive.append(shifted_delta_ms)
        if (
            job.status == "succeeded"
            and (job.duration_ms is None or job.duration_ms == 0)
            and 0 < shifted_delta_ms <= max_safe_ms
        ):
            if len(repairable_examples) < sample_limit:
                repairable_examples.append(
                    {
                        "job_id": job.job_id,
                        "created_at": str(job.created_at),
                        "finished_at": str(job.finished_at),
                        "raw_delta_ms": raw_delta_ms,
                        "shifted_plus_8h_delta_ms": shifted_delta_ms,
                    }
                )

    repairable_count = sum(1 for job in jobs if _candidate_delta_ms(job, max_safe_ms) is not None)

    return {
        "mode": "read_only",
        "total_jobs": len(jobs),
        "by_status": by_status,
        "succeeded_duration": {
            "zero_or_missing": zero_or_missing_succeeded,
            "positive": nonzero_succeeded,
        },
        "timestamp_deltas": {
            "missing_timestamps": missing_timestamps,
            "raw_positive": _summary(raw_positive),
            "raw_negative": _summary(raw_negative),
            "shifted_plus_8h_positive": _summary(shifted_positive),
        },
        "repair_candidates": {
            "rule": f"succeeded with duration_ms null/0 and 0 < finished_at+{SHIFT_HOURS}h-created_at <= {max_safe_hours}h",
            "count": repairable_count,
            "examples": repairable_examples,
        },
    }


def backfill(max_safe_hours: float, sample_limit: int, *, apply: bool) -> dict:
    max_safe_ms = int(max_safe_hours * 60 * 60 * 1000)
    db = SessionLocal()
    changed_examples: list[dict] = []
    changed_count = 0
    try:
        jobs = db.query(QuoteJob).order_by(QuoteJob.created_at.asc(), QuoteJob.id.asc()).all()
        for job in jobs:
            new_duration_ms = _candidate_delta_ms(job, max_safe_ms)
            if new_duration_ms is None:
                continue
            changed_count += 1
            if len(changed_examples) < sample_limit:
                changed_examples.append(
                    {
                        "job_id": job.job_id,
                        "old_duration_ms": job.duration_ms,
                        "new_duration_ms": new_duration_ms,
                        "created_at": str(job.created_at),
                        "finished_at": str(job.finished_at),
                    }
                )
            if apply:
                job.duration_ms = new_duration_ms
        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "mode": "apply" if apply else "dry_run",
        "rule": f"succeeded with duration_ms null/0 and 0 < finished_at+{SHIFT_HOURS}h-created_at <= {max_safe_hours}h",
        "matched_count": changed_count,
        "updated_count": changed_count if apply else 0,
        "examples": changed_examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose or backfill quote_jobs.duration_ms history.")
    parser.add_argument(
        "--mode",
        choices=("diagnose", "backfill"),
        default="diagnose",
        help="diagnose is read-only; backfill is dry-run unless --apply is set.",
    )
    parser.add_argument("--max-safe-hours", type=float, default=6.0)
    parser.add_argument("--sample-limit", type=int, default=10)
    parser.add_argument("--apply", action="store_true", help="Actually update matched rows in backfill mode.")
    args = parser.parse_args()
    if args.mode == "diagnose":
        result = diagnose(args.max_safe_hours, args.sample_limit)
    else:
        result = backfill(args.max_safe_hours, args.sample_limit, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
