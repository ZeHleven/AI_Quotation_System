import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user, require_admin
from app.models.quote_cost_evidence import QuoteCostEvidence
from app.models.quote_feedback import QuoteCorrection, QuoteFeedback, QuoteRagTrace
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.schemas.quote_feedback import QuoteFeedbackRejectRequest
from app.services.quote_cost_evidence import serialize_cost_evidence
from app.services.quote_feedback import record_rejected_quote
from app.services.rbac import has_admin_role


router = APIRouter()


def _format_dt(value) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _load_json(raw_value: Optional[str]) -> Any:
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except Exception:
        return raw_value


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _status_filter(query, status_filter: Optional[str]):
    if not status_filter:
        return query
    statuses = [item.strip() for item in status_filter.split(",") if item.strip()]
    if not statuses:
        return query
    return query.filter(QuoteFeedback.status.in_(statuses))


def _feedback_query(
    db: Session,
    *,
    days: Optional[int] = None,
    username: Optional[str] = None,
    status_filter: Optional[str] = None,
):
    query = db.query(QuoteFeedback)
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(QuoteFeedback.created_at >= cutoff)
    if username:
        query = query.filter(QuoteFeedback.username == username)
    return _status_filter(query, status_filter)


def _count_by_feedback_id(db: Session, model, feedback_ids: list[int]) -> dict[int, int]:
    if not feedback_ids:
        return {}
    return {
        feedback_id: count
        for feedback_id, count in (
            db.query(model.feedback_id, func.count(model.id))
            .filter(model.feedback_id.in_(feedback_ids))
            .group_by(model.feedback_id)
            .all()
        )
    }


def _feedback_row(
    feedback: QuoteFeedback,
    correction_counts: dict[int, int],
    rag_counts: dict[int, int],
    cost_evidence_counts: Optional[dict[int, int]] = None,
) -> dict:
    cost_evidence_counts = cost_evidence_counts or {}
    return {
        "id": feedback.id,
        "quote_id": feedback.quote_id,
        "quote_job_id": feedback.quote_job_id,
        "quote_history_id": feedback.quote_history_id,
        "username": feedback.username,
        "trace_id": feedback.trace_id,
        "source": feedback.source,
        "status": feedback.status,
        "request_text": feedback.request_text,
        "source_file_name": feedback.source_file_name,
        "project_summary": feedback.project_summary,
        "change_summary": feedback.change_summary,
        "top_changed_fields": [
            item.strip() for item in (feedback.top_changed_fields or "").split(",") if item.strip()
        ],
        "reviewed_by": feedback.reviewed_by,
        "ai_total_amount": _round(feedback.ai_total_amount),
        "final_total_amount": _round(feedback.final_total_amount),
        "amount_delta": _round(feedback.amount_delta),
        "amount_delta_ratio": _round(feedback.amount_delta_ratio, 6),
        "ai_item_count": feedback.ai_item_count,
        "final_item_count": feedback.final_item_count,
        "was_modified": feedback.was_modified,
        "pushed_to_dingtalk": feedback.pushed_to_dingtalk,
        "rejected": feedback.rejected,
        "rejection_reason": feedback.rejection_reason,
        "dify_prompt_version": feedback.dify_prompt_version,
        "dify_workflow_version": feedback.dify_workflow_version,
        "rag_collection_alias": feedback.rag_collection_alias,
        "material_snapshot_id": feedback.material_snapshot_id,
        "correction_count": correction_counts.get(feedback.id, 0),
        "rag_trace_count": rag_counts.get(feedback.id, 0),
        "cost_evidence_count": cost_evidence_counts.get(feedback.id, 0),
        "created_at": _format_dt(feedback.created_at),
        "confirmed_at": _format_dt(feedback.confirmed_at),
        "rejected_at": _format_dt(feedback.rejected_at),
    }


def _feedback_detail(db: Session, feedback: QuoteFeedback) -> dict:
    corrections = (
        db.query(QuoteCorrection)
        .filter(QuoteCorrection.feedback_id == feedback.id)
        .order_by(QuoteCorrection.item_index.asc(), QuoteCorrection.id.asc())
        .all()
    )
    rag_traces = (
        db.query(QuoteRagTrace)
        .filter(QuoteRagTrace.feedback_id == feedback.id)
        .order_by(QuoteRagTrace.rank.asc(), QuoteRagTrace.id.asc())
        .all()
    )
    cost_evidence = (
        db.query(QuoteCostEvidence)
        .filter(QuoteCostEvidence.feedback_id == feedback.id)
        .order_by(QuoteCostEvidence.item_index.asc(), QuoteCostEvidence.id.asc())
        .all()
    )
    data = _feedback_row(
        feedback,
        {feedback.id: len(corrections)},
        {feedback.id: len(rag_traces)},
        {feedback.id: len(cost_evidence)},
    )
    data.update(
        {
            "correction_summary": _load_json(feedback.correction_summary_json),
            "ai_payload": _load_json(feedback.ai_payload_json),
            "final_payload": _load_json(feedback.final_payload_json),
            "corrections": [
                {
                    "id": item.id,
                    "item_index": item.item_index,
                    "project_name": item.project_name,
                    "field_path": item.field_path,
                    "field_label": item.field_label,
                    "change_type": item.change_type,
                    "before_value": item.before_value,
                    "after_value": item.after_value,
                    "before_display": item.before_display,
                    "after_display": item.after_display,
                    "delta_amount": _round(item.delta_amount),
                    "reason_category": item.reason_category,
                    "reason_text": item.reason_text,
                    "created_at": _format_dt(item.created_at),
                }
                for item in corrections
            ],
            "rag_traces": [
                {
                    "id": item.id,
                    "query_text": item.query_text,
                    "item_index": item.item_index,
                    "project_name": item.project_name,
                    "material_id": item.material_id,
                    "item_name": item.item_name,
                    "rank": item.rank,
                    "score": _round(item.score, 6),
                    "collection_alias": item.collection_alias,
                    "material_snapshot_id": item.material_snapshot_id,
                    "sent_to_prompt": item.sent_to_prompt,
                    "cited_by_model": item.cited_by_model,
                    "adopted_by_user": item.adopted_by_user,
                    "used_in_final_quote": item.used_in_final_quote,
                    "match_reason": item.match_reason,
                    "raw": _load_json(item.raw_json),
                    "created_at": _format_dt(item.created_at),
                }
                for item in rag_traces
            ],
            "cost_evidence": [serialize_cost_evidence(item) for item in cost_evidence],
        }
    )
    return data


def _top_rows(db: Session, model, feedback_ids: list[int], columns: list, limit: int = 8) -> list[dict]:
    if not feedback_ids:
        return []
    count_expr = func.count(model.id).label("count")
    rows = (
        db.query(*columns, count_expr)
        .filter(model.feedback_id.in_(feedback_ids))
        .group_by(*columns)
        .order_by(count_expr.desc())
        .limit(limit)
        .all()
    )
    result = []
    for row in rows:
        values = row[:-1]
        item = {"count": row[-1]}
        for index, column in enumerate(columns):
            item[column.key] = values[index]
        result.append(item)
    return result


@router.post("/quote/feedback/reject", summary="记录人工预审打回")
async def reject_quote_feedback(
    payload: QuoteFeedbackRejectRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.quote_job_id and not payload.trace_id:
        raise HTTPException(status_code=400, detail="quote_job_id or trace_id is required")
    if payload.quote_job_id:
        job = db.query(QuoteJob).filter(QuoteJob.job_id == payload.quote_job_id).first()
        if not job or (not has_admin_role(current_user) and job.username != current_user.username):
            raise HTTPException(status_code=404, detail="quote job not found")
    feedback = record_rejected_quote(
        db,
        username=current_user.username,
        quote_job_id=payload.quote_job_id,
        trace_id=payload.trace_id,
        reason=payload.reason,
        allow_cross_user=has_admin_role(current_user),
    )
    db.commit()
    return api_ok(
        {
            "feedback_id": feedback.id,
            "quote_id": feedback.quote_id,
            "quote_job_id": feedback.quote_job_id,
            "status": feedback.status,
        }
    )


@router.get("/admin/quote_feedback/summary", summary="报价反馈分析汇总")
async def get_quote_feedback_summary(
    days: int = Query(7, ge=1, le=365),
    username: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    feedback_rows = _feedback_query(db, days=days, username=username, status_filter=status).all()
    feedback_ids = [item.id for item in feedback_rows]

    status_counts = Counter(item.status for item in feedback_rows)
    prompt_counts = Counter((item.dify_prompt_version or "unknown") for item in feedback_rows)
    deltas = [item.amount_delta for item in feedback_rows if item.amount_delta is not None]
    abs_deltas = [abs(item.amount_delta) for item in feedback_rows if item.amount_delta is not None]
    ratios = [item.amount_delta_ratio for item in feedback_rows if item.amount_delta_ratio is not None]

    correction_count = 0
    rag_trace_count = 0
    cost_evidence_count = 0
    if feedback_ids:
        correction_count = (
            db.query(func.count(QuoteCorrection.id))
            .filter(QuoteCorrection.feedback_id.in_(feedback_ids))
            .scalar()
            or 0
        )
        cost_evidence_count = (
            db.query(func.count(QuoteCostEvidence.id))
            .filter(QuoteCostEvidence.feedback_id.in_(feedback_ids))
            .scalar()
            or 0
        )
        rag_trace_count = (
            db.query(func.count(QuoteRagTrace.id))
            .filter(QuoteRagTrace.feedback_id.in_(feedback_ids))
            .scalar()
            or 0
        )

    top_correction_fields = _top_rows(db, QuoteCorrection, feedback_ids, [QuoteCorrection.field_path])
    top_correction_field_labels = _top_rows(db, QuoteCorrection, feedback_ids, [QuoteCorrection.field_label])
    top_reason_categories = _top_rows(db, QuoteCorrection, feedback_ids, [QuoteCorrection.reason_category])
    top_rag_materials = _top_rows(
        db,
        QuoteRagTrace,
        feedback_ids,
        [QuoteRagTrace.material_id, QuoteRagTrace.item_name],
    )
    top_cost_items = _top_rows(
        db,
        QuoteCostEvidence,
        feedback_ids,
        [QuoteCostEvidence.cost_item_id, QuoteCostEvidence.cost_item_name_snapshot],
    )

    data = {
        "days": days,
        "username": username,
        "total_count": len(feedback_rows),
        "confirmed_count": status_counts.get("confirmed", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "pending_count": status_counts.get("pending_review", 0),
        "pushed_count": sum(1 for item in feedback_rows if item.pushed_to_dingtalk),
        "modified_count": sum(1 for item in feedback_rows if item.was_modified),
        "avg_amount_delta": _round(sum(deltas) / len(deltas)) if deltas else 0.0,
        "avg_abs_amount_delta": _round(sum(abs_deltas) / len(abs_deltas)) if abs_deltas else 0.0,
        "avg_delta_ratio": _round(sum(ratios) / len(ratios), 6) if ratios else 0.0,
        "correction_count": correction_count,
        "rag_trace_count": rag_trace_count,
        "cost_evidence_count": cost_evidence_count,
        "by_status": [{"status": key, "count": count} for key, count in status_counts.items()],
        "by_prompt_version": [
            {"prompt_version": key, "count": count}
            for key, count in prompt_counts.most_common(8)
        ],
        "top_correction_fields": top_correction_fields,
        "top_correction_field_labels": [
            item for item in top_correction_field_labels if item.get("field_label")
        ],
        "top_reason_categories": [
            item for item in top_reason_categories if item.get("reason_category")
        ],
        "top_rag_materials": top_rag_materials,
        "top_cost_items": [item for item in top_cost_items if item.get("cost_item_id")],
    }
    return api_ok(data)


@router.get("/admin/quote-cost-evidence", summary="报价成本证据审计列表")
async def list_quote_cost_evidence(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    feedback_id: Optional[int] = None,
    quote_id: Optional[str] = None,
    quote_job_id: Optional[str] = None,
    quote_history_id: Optional[int] = None,
    cost_item_id: Optional[int] = None,
    username: Optional[str] = None,
    status: Optional[str] = None,
    min_abs_delta_rate: Optional[float] = Query(None, ge=0),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(QuoteCostEvidence)
    if feedback_id is not None:
        query = query.filter(QuoteCostEvidence.feedback_id == feedback_id)
    if quote_id:
        query = query.filter(QuoteCostEvidence.quote_id == quote_id)
    if quote_job_id:
        query = query.filter(QuoteCostEvidence.quote_job_id == quote_job_id)
    if quote_history_id is not None:
        query = query.filter(QuoteCostEvidence.quote_history_id == quote_history_id)
    if cost_item_id is not None:
        query = query.filter(QuoteCostEvidence.cost_item_id == cost_item_id)
    if username:
        query = query.filter(QuoteCostEvidence.username == username)
    if status:
        statuses = [item.strip() for item in status.split(",") if item.strip()]
        if statuses:
            query = query.filter(QuoteCostEvidence.status.in_(statuses))
    if min_abs_delta_rate is not None:
        query = query.filter(
            or_(
                QuoteCostEvidence.price_delta_rate >= min_abs_delta_rate,
                QuoteCostEvidence.price_delta_rate <= -min_abs_delta_rate,
            )
        )

    total = query.count()
    rows = (
        query.order_by(QuoteCostEvidence.created_at.desc(), QuoteCostEvidence.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_cost_evidence(item) for item in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/quote_feedback", summary="报价反馈记录列表")
async def list_quote_feedback(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    days: Optional[int] = Query(None, ge=1, le=365),
    username: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = _feedback_query(db, days=days, username=username, status_filter=status)
    total = query.count()
    rows = (
        query.order_by(QuoteFeedback.created_at.desc(), QuoteFeedback.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    feedback_ids = [item.id for item in rows]
    correction_counts = _count_by_feedback_id(db, QuoteCorrection, feedback_ids)
    rag_counts = _count_by_feedback_id(db, QuoteRagTrace, feedback_ids)
    cost_evidence_counts = _count_by_feedback_id(db, QuoteCostEvidence, feedback_ids)
    return api_page(
        [_feedback_row(item, correction_counts, rag_counts, cost_evidence_counts) for item in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/quote_feedback/{feedback_id}", summary="报价反馈记录详情")
async def get_quote_feedback_detail(
    feedback_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    feedback = db.query(QuoteFeedback).filter(QuoteFeedback.id == feedback_id).first()
    if not feedback:
        raise HTTPException(status_code=404, detail="quote feedback not found")
    return api_ok(_feedback_detail(db, feedback))
