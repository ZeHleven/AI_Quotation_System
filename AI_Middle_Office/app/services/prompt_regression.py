import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.prompt_regression import PromptRegressionCase, PromptRegressionRun
from app.models.quote_feedback import QuoteCorrection, QuoteFeedback
from app.models.quote_job import QuoteJob
from app.schemas.prompt_regression import PromptRegressionBuildRequest, PromptRegressionRunRequest
from app.services.quote_feedback import _json_loads, _project_details, _total_amount


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _format_dt(value) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _case_name(feedback: QuoteFeedback, ai_payload: Any, expected_payload: Any) -> str:
    for payload in (expected_payload, ai_payload):
        for item in _project_details(payload):
            name = item.get("project_name") or item.get("name") or item.get("title")
            if name:
                return str(name)[:255]
    return f"quote-{feedback.quote_id}"[:255]


def _correction_payloads(corrections: list[QuoteCorrection]) -> list[dict]:
    return [
        {
            "item_index": item.item_index,
            "project_name": item.project_name,
            "field_path": item.field_path,
            "before_value": item.before_value,
            "after_value": item.after_value,
            "delta_amount": _round(item.delta_amount, 2),
            "reason_category": item.reason_category,
            "reason_text": item.reason_text,
        }
        for item in corrections
    ]


def _apply_case_fields(
    case: PromptRegressionCase,
    feedback: QuoteFeedback,
    corrections: list[QuoteCorrection],
    job: Optional[QuoteJob],
    *,
    active: bool,
) -> None:
    ai_payload = _json_loads(feedback.ai_payload_json)
    expected_payload = _json_loads(feedback.final_payload_json)
    ai_details = _project_details(ai_payload)
    expected_details = _project_details(expected_payload)
    ai_total = feedback.ai_total_amount if feedback.ai_total_amount is not None else _total_amount(ai_payload)
    expected_total = (
        feedback.final_total_amount
        if feedback.final_total_amount is not None
        else (_total_amount(expected_payload) if expected_payload is not None else None)
    )
    amount_delta = None
    amount_delta_ratio = None
    if expected_total is not None and ai_total is not None:
        amount_delta = round(expected_total - ai_total, 2)
        amount_delta_ratio = round(amount_delta / ai_total, 6) if ai_total else None

    case.quote_id = feedback.quote_id
    case.quote_job_id = feedback.quote_job_id
    case.quote_history_id = feedback.quote_history_id
    case.username = feedback.username
    case.case_name = _case_name(feedback, ai_payload, expected_payload)
    case.request_text = job.message if job else None
    case.source_status = feedback.status
    case.source_prompt_version = feedback.dify_prompt_version
    case.source_workflow_version = feedback.dify_workflow_version
    case.source_release_id = feedback.dify_release_id
    case.rag_collection_alias = feedback.rag_collection_alias
    case.material_snapshot_id = feedback.material_snapshot_id
    case.ai_total_amount = _round(ai_total, 2)
    case.expected_total_amount = _round(expected_total, 2)
    case.amount_delta = amount_delta
    case.amount_delta_ratio = amount_delta_ratio
    case.ai_item_count = len(ai_details)
    case.expected_item_count = len(expected_details)
    case.correction_count = len(corrections)
    case.format_error_count = 1 if feedback.ai_payload_json and not ai_details else 0
    case.missing_item_count = max(0, len(expected_details) - len(ai_details))
    case.rejected = bool(feedback.rejected or feedback.status == "rejected")
    case.was_modified = bool(feedback.was_modified or corrections or amount_delta)
    case.active = active
    case.locked = True
    case.rejection_reason = feedback.rejection_reason
    case.ai_payload_json = feedback.ai_payload_json
    case.expected_payload_json = feedback.final_payload_json
    case.corrections_json = _json_dumps(_correction_payloads(corrections))
    case.metadata_json = _json_dumps(
        {
            "source_feedback_created_at": _format_dt(feedback.created_at),
            "confirmed_at": _format_dt(feedback.confirmed_at),
            "rejected_at": _format_dt(feedback.rejected_at),
            "trace_id": feedback.trace_id,
            "quote_model_name": feedback.quote_model_name,
            "vision_model_name": feedback.vision_model_name,
        }
    )


def build_golden_cases_from_feedback(db: Session, request: PromptRegressionBuildRequest) -> dict:
    statuses = ["confirmed"]
    if request.include_rejected:
        statuses.append("rejected")

    query = db.query(QuoteFeedback).filter(QuoteFeedback.status.in_(statuses))
    if request.days:
        cutoff = _utcnow() - timedelta(days=request.days)
        query = query.filter(QuoteFeedback.created_at >= cutoff)
    if request.prompt_version:
        query = query.filter(QuoteFeedback.dify_prompt_version == request.prompt_version)

    feedback_rows = (
        query.order_by(QuoteFeedback.created_at.desc(), QuoteFeedback.id.desc())
        .limit(request.limit)
        .all()
    )

    created = 0
    updated = 0
    skipped = 0
    case_ids: list[int] = []
    for feedback in feedback_rows:
        existing = (
            db.query(PromptRegressionCase)
            .filter(PromptRegressionCase.source_feedback_id == feedback.id)
            .first()
        )
        if existing and not request.overwrite:
            skipped += 1
            case_ids.append(existing.id)
            continue

        case = existing or PromptRegressionCase(source_feedback_id=feedback.id)
        if not existing:
            db.add(case)
            created += 1
        else:
            updated += 1

        corrections = (
            db.query(QuoteCorrection)
            .filter(QuoteCorrection.feedback_id == feedback.id)
            .order_by(QuoteCorrection.item_index.asc(), QuoteCorrection.id.asc())
            .all()
        )
        job = (
            db.query(QuoteJob)
            .filter(QuoteJob.job_id == feedback.quote_job_id)
            .first()
            if feedback.quote_job_id
            else None
        )
        _apply_case_fields(case, feedback, corrections, job, active=request.active)
        db.flush()
        case_ids.append(case.id)

    db.commit()
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_feedback": len(feedback_rows),
        "case_ids": case_ids,
    }


def _metric_summary(cases: list[PromptRegressionCase], amount_tolerance: float) -> dict:
    total = len(cases)
    if total == 0:
        return {
            "case_count": 0,
            "confirmed_count": 0,
            "rejected_count": 0,
            "modified_count": 0,
            "avg_abs_amount_delta": 0.0,
            "avg_abs_delta_ratio": 0.0,
            "exact_total_match_rate": 0.0,
            "format_error_rate": 0.0,
            "missing_item_rate": 0.0,
            "rejection_rate": 0.0,
            "score": 0.0,
        }

    confirmed = [case for case in cases if not case.rejected]
    deltas = [abs(case.amount_delta) for case in confirmed if case.amount_delta is not None]
    ratios = [abs(case.amount_delta_ratio) for case in confirmed if case.amount_delta_ratio is not None]
    exact_matches = sum(
        1
        for case in confirmed
        if case.amount_delta is not None and abs(case.amount_delta) <= amount_tolerance
    )
    modified_count = sum(1 for case in cases if case.was_modified)
    rejected_count = sum(1 for case in cases if case.rejected)
    format_error_count = sum(1 for case in cases if case.format_error_count > 0)
    missing_item_count = sum(1 for case in cases if case.missing_item_count > 0)

    avg_abs_delta_ratio = sum(ratios) / len(ratios) if ratios else 0.0
    modified_rate = modified_count / total
    rejection_rate = rejected_count / total
    format_error_rate = format_error_count / total
    missing_item_rate = missing_item_count / total
    score = 100.0
    score -= min(avg_abs_delta_ratio, 1.0) * 40
    score -= modified_rate * 15
    score -= rejection_rate * 20
    score -= format_error_rate * 20
    score -= missing_item_rate * 20

    return {
        "case_count": total,
        "confirmed_count": len(confirmed),
        "rejected_count": rejected_count,
        "modified_count": modified_count,
        "avg_abs_amount_delta": _round(sum(deltas) / len(deltas), 2) if deltas else 0.0,
        "avg_abs_delta_ratio": _round(avg_abs_delta_ratio, 6),
        "exact_total_match_rate": _round(exact_matches / len(confirmed), 6) if confirmed else 0.0,
        "format_error_rate": _round(format_error_rate, 6),
        "missing_item_rate": _round(missing_item_rate, 6),
        "rejection_rate": _round(rejection_rate, 6),
        "score": _round(max(0.0, min(100.0, score)), 2),
    }


def _metrics_by_prompt(cases: list[PromptRegressionCase], amount_tolerance: float) -> list[dict]:
    grouped: dict[str, list[PromptRegressionCase]] = {}
    for case in cases:
        key = case.source_prompt_version or "unknown"
        grouped.setdefault(key, []).append(case)
    return [
        {"prompt_version": key, **_metric_summary(rows, amount_tolerance)}
        for key, rows in sorted(grouped.items(), key=lambda item: item[0])
    ]


def create_prompt_regression_run(
    db: Session,
    *,
    triggered_by: str,
    request: PromptRegressionRunRequest,
) -> PromptRegressionRun:
    query = db.query(PromptRegressionCase)
    if request.active_only:
        query = query.filter(PromptRegressionCase.active.is_(True))
    if request.case_ids:
        query = query.filter(PromptRegressionCase.id.in_(request.case_ids))
    if request.prompt_version:
        query = query.filter(PromptRegressionCase.source_prompt_version == request.prompt_version)

    cases = query.order_by(PromptRegressionCase.id.asc()).all()
    if not cases:
        raise ValueError("no prompt regression cases matched the request")

    metrics = _metric_summary(cases, request.amount_tolerance)
    by_prompt_version = _metrics_by_prompt(cases, request.amount_tolerance)
    baseline_metrics = None
    comparison = None
    if request.baseline_prompt_version:
        baseline_query = db.query(PromptRegressionCase)
        if request.active_only:
            baseline_query = baseline_query.filter(PromptRegressionCase.active.is_(True))
        baseline_cases = (
            baseline_query
            .filter(PromptRegressionCase.source_prompt_version == request.baseline_prompt_version)
            .order_by(PromptRegressionCase.id.asc())
            .all()
        )
        baseline_metrics = _metric_summary(baseline_cases, request.amount_tolerance)
        comparison = {
            "score_delta": _round(metrics["score"] - baseline_metrics["score"], 2),
            "avg_abs_amount_delta_delta": _round(
                metrics["avg_abs_amount_delta"] - baseline_metrics["avg_abs_amount_delta"],
                2,
            ),
            "rejection_rate_delta": _round(metrics["rejection_rate"] - baseline_metrics["rejection_rate"], 6),
        }

    prompt_counts = Counter(case.source_prompt_version or "unknown" for case in cases)
    metrics_json = {
        "amount_tolerance": request.amount_tolerance,
        "case_ids": [case.id for case in cases],
        "prompt_counts": [{"prompt_version": key, "count": count} for key, count in prompt_counts.items()],
        "by_prompt_version": by_prompt_version,
        "baseline": baseline_metrics,
        "comparison": comparison,
    }

    now = _utcnow()
    run = PromptRegressionRun(
        triggered_by=triggered_by,
        name=request.name,
        status="completed",
        started_at=now,
        finished_at=now,
        prompt_version=request.prompt_version,
        baseline_prompt_version=request.baseline_prompt_version,
        case_count=metrics["case_count"],
        confirmed_count=metrics["confirmed_count"],
        rejected_count=metrics["rejected_count"],
        modified_count=metrics["modified_count"],
        avg_abs_amount_delta=metrics["avg_abs_amount_delta"],
        avg_abs_delta_ratio=metrics["avg_abs_delta_ratio"],
        exact_total_match_rate=metrics["exact_total_match_rate"],
        format_error_rate=metrics["format_error_rate"],
        missing_item_rate=metrics["missing_item_rate"],
        rejection_rate=metrics["rejection_rate"],
        score=metrics["score"],
        metrics_json=_json_dumps(metrics_json),
        notes=request.notes,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def case_to_dict(case: PromptRegressionCase) -> dict:
    return {
        "id": case.id,
        "source_feedback_id": case.source_feedback_id,
        "quote_id": case.quote_id,
        "quote_job_id": case.quote_job_id,
        "quote_history_id": case.quote_history_id,
        "username": case.username,
        "case_name": case.case_name,
        "source_status": case.source_status,
        "source_prompt_version": case.source_prompt_version,
        "source_workflow_version": case.source_workflow_version,
        "source_release_id": case.source_release_id,
        "rag_collection_alias": case.rag_collection_alias,
        "material_snapshot_id": case.material_snapshot_id,
        "ai_total_amount": _round(case.ai_total_amount, 2),
        "expected_total_amount": _round(case.expected_total_amount, 2),
        "amount_delta": _round(case.amount_delta, 2),
        "amount_delta_ratio": _round(case.amount_delta_ratio, 6),
        "ai_item_count": case.ai_item_count,
        "expected_item_count": case.expected_item_count,
        "correction_count": case.correction_count,
        "format_error_count": case.format_error_count,
        "missing_item_count": case.missing_item_count,
        "rejected": case.rejected,
        "was_modified": case.was_modified,
        "active": case.active,
        "locked": case.locked,
        "rejection_reason": case.rejection_reason,
        "created_at": _format_dt(case.created_at),
        "updated_at": _format_dt(case.updated_at),
    }


def run_to_dict(run: PromptRegressionRun) -> dict:
    metrics = None
    if run.metrics_json:
        try:
            metrics = json.loads(run.metrics_json)
        except Exception:
            metrics = None
    return {
        "id": run.id,
        "triggered_by": run.triggered_by,
        "name": run.name,
        "status": run.status,
        "started_at": _format_dt(run.started_at),
        "finished_at": _format_dt(run.finished_at),
        "prompt_version": run.prompt_version,
        "baseline_prompt_version": run.baseline_prompt_version,
        "case_count": run.case_count,
        "confirmed_count": run.confirmed_count,
        "rejected_count": run.rejected_count,
        "modified_count": run.modified_count,
        "avg_abs_amount_delta": _round(run.avg_abs_amount_delta, 2),
        "avg_abs_delta_ratio": _round(run.avg_abs_delta_ratio, 6),
        "exact_total_match_rate": _round(run.exact_total_match_rate, 6),
        "format_error_rate": _round(run.format_error_rate, 6),
        "missing_item_rate": _round(run.missing_item_rate, 6),
        "rejection_rate": _round(run.rejection_rate, 6),
        "score": _round(run.score, 2),
        "metrics": metrics,
        "error": run.error,
        "notes": run.notes,
    }
