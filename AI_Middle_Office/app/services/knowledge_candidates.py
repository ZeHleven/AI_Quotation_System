import json
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.knowledge_candidate import KnowledgeCandidate
from app.models.material import Material, MaterialSnapshot
from app.models.quote_feedback import QuoteCorrection, QuoteFeedback, QuoteRagTrace
from app.models.quote_job import QuoteJob
from app.schemas.knowledge_candidate import KnowledgeCandidateApproveRequest, KnowledgeCandidateBuildRequest
from app.services.quote_feedback import _json_loads, _parse_amount, _project_details


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _format_dt(value) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _safe_key_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "_", text)
    return re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", text)[:80] or uuid.uuid4().hex[:8]


def _normalize_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _material_by_name(db: Session, item_name: str) -> Optional[Material]:
    normalized = _normalize_name(item_name)
    if not normalized:
        return None
    for material in db.query(Material).all():
        if _normalize_name(material.item_name) == normalized:
            return material
    return None


def _material_map(db: Session) -> dict[str, Material]:
    return {_normalize_name(item.item_name): item for item in db.query(Material).all()}


def _item_notes(item: dict[str, Any], feedback: QuoteFeedback) -> str:
    parts = []
    for key in ("notes", "spec", "material", "craft", "space"):
        value = item.get(key)
        if value:
            parts.append(f"{key}: {value}")
    parts.append(f"source_feedback_id: {feedback.id}")
    if feedback.dify_prompt_version:
        parts.append(f"prompt: {feedback.dify_prompt_version}")
    return "\n".join(parts)


def _feedback_evidence(feedback: QuoteFeedback, extra: Optional[dict] = None) -> str:
    payload = {
        "feedback_id": feedback.id,
        "quote_id": feedback.quote_id,
        "quote_job_id": feedback.quote_job_id,
        "trace_id": feedback.trace_id,
        "username": feedback.username,
        "status": feedback.status,
        "dify_prompt_version": feedback.dify_prompt_version,
        "rag_collection_alias": feedback.rag_collection_alias,
    }
    if extra:
        payload.update(extra)
    return _json_dumps(payload)


def _upsert_candidate(db: Session, payload: dict, *, overwrite: bool) -> tuple[str, KnowledgeCandidate]:
    existing = (
        db.query(KnowledgeCandidate)
        .filter(KnowledgeCandidate.candidate_key == payload["candidate_key"])
        .first()
    )
    if existing:
        if not overwrite or existing.status != "pending":
            return "skipped", existing
        for key, value in payload.items():
            setattr(existing, key, value)
        return "updated", existing

    candidate = KnowledgeCandidate(**payload)
    db.add(candidate)
    return "created", candidate


def _candidate_from_final_item(
    db: Session,
    *,
    feedback: QuoteFeedback,
    item: dict[str, Any],
    item_index: int,
    material_lookup: dict[str, Material],
    request: KnowledgeCandidateBuildRequest,
) -> tuple[str, KnowledgeCandidate] | None:
    item_name = str(item.get("project_name") or item.get("item_name") or item.get("name") or "").strip()
    if not item_name:
        return None
    existing = material_lookup.get(_normalize_name(item_name))
    if existing or not request.include_new_materials:
        return None

    unit_price = _parse_amount(item.get("unit_price"))
    if unit_price is None:
        unit_price = _parse_amount(item.get("total_price"))
    payload = {
        "candidate_key": f"final-item:{feedback.id}:{item_index}:{_safe_key_text(item_name)}",
        "source_type": "quote_feedback",
        "candidate_kind": "new_material",
        "status": "pending",
        "source_feedback_id": feedback.id,
        "quote_id": feedback.quote_id,
        "quote_job_id": feedback.quote_job_id,
        "trace_id": feedback.trace_id,
        "username": feedback.username,
        "item_name": item_name[:255],
        "unit_price": unit_price,
        "unit": str(item.get("unit") or "项")[:64],
        "notes": _item_notes(item, feedback),
        "suggested_material_id": f"kb_{uuid.uuid4().hex[:8]}",
        "confidence_score": 0.72,
        "reason": "confirmed quote item is not in materials",
        "evidence_json": _feedback_evidence(feedback, {"item": item, "item_index": item_index}),
        "is_draft_material": True,
    }
    return _upsert_candidate(db, payload, overwrite=request.overwrite)


def _candidate_from_correction(
    db: Session,
    *,
    feedback: QuoteFeedback,
    correction: QuoteCorrection,
    material_lookup: dict[str, Material],
    request: KnowledgeCandidateBuildRequest,
) -> tuple[str, KnowledgeCandidate] | None:
    if not request.include_price_updates:
        return None
    if not correction.field_path.endswith(".unit_price") and not correction.field_path.endswith(".total_price"):
        return None

    after_amount = _parse_amount(correction.after_value)
    before_amount = _parse_amount(correction.before_value)
    if after_amount is None:
        return None
    delta = after_amount - (before_amount or 0.0)
    base = abs(before_amount or 0.0)
    delta_ratio = abs(delta) / base if base else None
    if abs(delta) < request.min_abs_delta and (delta_ratio is None or delta_ratio < request.min_delta_ratio):
        return None

    item_name = str(correction.project_name or "").strip()
    if not item_name:
        return None
    existing = material_lookup.get(_normalize_name(item_name))
    candidate_kind = "price_update" if existing else "new_material"
    if candidate_kind == "new_material" and not request.include_new_materials:
        return None

    payload = {
        "candidate_key": f"correction:{correction.id}:{_safe_key_text(correction.field_path)}",
        "source_type": "quote_correction",
        "candidate_kind": candidate_kind,
        "status": "pending",
        "source_feedback_id": feedback.id,
        "source_correction_id": correction.id,
        "quote_id": feedback.quote_id,
        "quote_job_id": feedback.quote_job_id,
        "trace_id": feedback.trace_id,
        "username": feedback.username,
        "item_name": item_name[:255],
        "unit_price": after_amount,
        "unit": existing.unit if existing else "项",
        "notes": "\n".join(
            part
            for part in (
                f"manual correction: {correction.field_path}",
                f"before: {correction.before_value}",
                f"after: {correction.after_value}",
                correction.reason_text,
            )
            if part
        ),
        "existing_material_id": existing.material_id if existing else None,
        "suggested_material_id": existing.material_id if existing else f"kb_{uuid.uuid4().hex[:8]}",
        "confidence_score": 0.86 if existing else 0.76,
        "reason": correction.reason_category or "manual price correction",
        "evidence_json": _feedback_evidence(
            feedback,
            {
                "field_path": correction.field_path,
                "before_value": correction.before_value,
                "after_value": correction.after_value,
                "delta_amount": _round(delta, 2),
                "delta_ratio": _round(delta_ratio, 6),
            },
        ),
        "is_draft_material": True,
    }
    return _upsert_candidate(db, payload, overwrite=request.overwrite)


def _candidate_from_rejection(
    db: Session,
    *,
    feedback: QuoteFeedback,
    request: KnowledgeCandidateBuildRequest,
) -> tuple[str, KnowledgeCandidate] | None:
    if not request.include_rejected or not feedback.rejected:
        return None
    reason = (feedback.rejection_reason or "").strip()
    if not reason:
        return None
    ai_details = _project_details(_json_loads(feedback.ai_payload_json))
    first_name = ""
    if ai_details:
        first_name = str(ai_details[0].get("project_name") or ai_details[0].get("item_name") or "").strip()
    item_name = first_name or f"待补充知识: {reason[:40]}"
    payload = {
        "candidate_key": f"rejection:{feedback.id}:{_safe_key_text(reason)}",
        "source_type": "quote_feedback",
        "candidate_kind": "missing_knowledge",
        "status": "pending",
        "source_feedback_id": feedback.id,
        "quote_id": feedback.quote_id,
        "quote_job_id": feedback.quote_job_id,
        "trace_id": feedback.trace_id,
        "username": feedback.username,
        "item_name": item_name[:255],
        "unit_price": None,
        "unit": "项",
        "notes": f"Rejected quote reason: {reason}",
        "suggested_material_id": f"kb_{uuid.uuid4().hex[:8]}",
        "confidence_score": 0.58,
        "reason": "quote rejected by reviewer",
        "evidence_json": _feedback_evidence(feedback, {"rejection_reason": reason}),
        "is_draft_material": True,
    }
    return _upsert_candidate(db, payload, overwrite=request.overwrite)


def build_knowledge_candidates(db: Session, request: KnowledgeCandidateBuildRequest) -> dict:
    query = db.query(QuoteFeedback)
    statuses = ["confirmed"]
    if request.include_rejected:
        statuses.append("rejected")
    query = query.filter(QuoteFeedback.status.in_(statuses))
    if request.days:
        cutoff = _utcnow() - timedelta(days=request.days)
        query = query.filter(QuoteFeedback.created_at >= cutoff)

    feedback_rows = (
        query.order_by(QuoteFeedback.created_at.desc(), QuoteFeedback.id.desc())
        .limit(request.limit)
        .all()
    )
    feedback_ids = [item.id for item in feedback_rows]
    corrections_by_feedback: dict[int, list[QuoteCorrection]] = {item.id: [] for item in feedback_rows}
    if feedback_ids:
        corrections = (
            db.query(QuoteCorrection)
            .filter(QuoteCorrection.feedback_id.in_(feedback_ids))
            .order_by(QuoteCorrection.item_index.asc(), QuoteCorrection.id.asc())
            .all()
        )
        for correction in corrections:
            corrections_by_feedback.setdefault(correction.feedback_id, []).append(correction)

    material_lookup = _material_map(db)
    counts = Counter()
    candidate_ids: list[int] = []
    for feedback in feedback_rows:
        for index, item in enumerate(_project_details(_json_loads(feedback.final_payload_json))):
            result = _candidate_from_final_item(
                db,
                feedback=feedback,
                item=item,
                item_index=index,
                material_lookup=material_lookup,
                request=request,
            )
            if result:
                action, candidate = result
                counts[action] += 1
                db.flush()
                candidate_ids.append(candidate.id)

        for correction in corrections_by_feedback.get(feedback.id, []):
            result = _candidate_from_correction(
                db,
                feedback=feedback,
                correction=correction,
                material_lookup=material_lookup,
                request=request,
            )
            if result:
                action, candidate = result
                counts[action] += 1
                db.flush()
                candidate_ids.append(candidate.id)

        result = _candidate_from_rejection(db, feedback=feedback, request=request)
        if result:
            action, candidate = result
            counts[action] += 1
            db.flush()
            candidate_ids.append(candidate.id)

    db.commit()
    return {
        "created": counts["created"],
        "updated": counts["updated"],
        "skipped": counts["skipped"],
        "total_feedback": len(feedback_rows),
        "candidate_ids": candidate_ids,
    }


def _materials_snapshot_data(db: Session) -> list[dict]:
    return [
        {
            "id": item.material_id,
            "item_name": item.item_name,
            "unit_price": item.unit_price or 0.0,
            "unit": item.unit or "项",
            "notes": item.notes or "",
            "is_draft": bool(item.is_draft),
        }
        for item in db.query(Material).order_by(Material.id.asc()).all()
    ]


def _create_snapshot(db: Session, *, username: str, candidate: KnowledgeCandidate) -> MaterialSnapshot:
    data = _materials_snapshot_data(db)
    snapshot = MaterialSnapshot(
        snapshot_id=f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(),
        username=username,
        action="knowledge_candidate_approve",
        reason=f"Before approving knowledge candidate {candidate.id}",
        item_count=len(data),
        data_json=_json_dumps(data),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _unique_material_id(db: Session, preferred: Optional[str]) -> str:
    candidate = (preferred or "").strip()[:64]
    if not candidate:
        candidate = f"kb_{uuid.uuid4().hex[:8]}"
    while db.query(Material).filter(Material.material_id == candidate).first():
        candidate = f"kb_{uuid.uuid4().hex[:8]}"
    return candidate


def approve_candidate(
    db: Session,
    *,
    candidate: KnowledgeCandidate,
    username: str,
    request: KnowledgeCandidateApproveRequest,
) -> dict:
    if candidate.status != "pending":
        raise ValueError("candidate is not pending")

    item_name = (request.item_name or candidate.item_name or "").strip()
    if not item_name:
        raise ValueError("item_name is required to approve a candidate")

    unit_price = request.unit_price if request.unit_price is not None else candidate.unit_price
    unit = (request.unit or candidate.unit or "项").strip() or "项"
    notes = request.notes if request.notes is not None else (candidate.notes or "")

    material = None
    if request.material_id:
        material = db.query(Material).filter(Material.material_id == request.material_id).first()
    if material is None and candidate.existing_material_id:
        material = db.query(Material).filter(Material.material_id == candidate.existing_material_id).first()
    if material is None and request.update_existing:
        material = _material_by_name(db, item_name)

    snapshot = _create_snapshot(db, username=username, candidate=candidate)
    if material:
        material.item_name = item_name
        material.unit_price = float(unit_price or 0.0)
        material.unit = unit
        material.notes = notes
        material.is_draft = request.as_draft
    else:
        material = Material(
            material_id=_unique_material_id(db, request.material_id or candidate.suggested_material_id),
            item_name=item_name,
            unit_price=float(unit_price or 0.0),
            unit=unit,
            notes=notes,
            is_draft=request.as_draft,
        )
        db.add(material)
        db.flush()

    candidate.status = "approved"
    candidate.material_id = material.material_id
    candidate.reviewed_by = username
    candidate.review_note = request.review_note
    candidate.reviewed_at = _utcnow()
    candidate.is_draft_material = request.as_draft
    db.commit()
    db.refresh(candidate)
    db.refresh(material)
    return {
        "candidate": candidate_to_dict(candidate),
        "material": {
            "id": material.material_id,
            "item_name": material.item_name,
            "unit_price": material.unit_price or 0.0,
            "unit": material.unit or "项",
            "notes": material.notes or "",
            "is_draft": bool(material.is_draft),
        },
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "item_count": snapshot.item_count,
            "action": snapshot.action,
        },
    }


def reject_candidate(db: Session, *, candidate: KnowledgeCandidate, username: str, review_note: Optional[str]) -> dict:
    if candidate.status != "pending":
        raise ValueError("candidate is not pending")
    candidate.status = "rejected"
    candidate.reviewed_by = username
    candidate.review_note = review_note
    candidate.reviewed_at = _utcnow()
    db.commit()
    db.refresh(candidate)
    return candidate_to_dict(candidate)


def candidate_to_dict(candidate: KnowledgeCandidate) -> dict:
    return {
        "id": candidate.id,
        "candidate_key": candidate.candidate_key,
        "source_type": candidate.source_type,
        "candidate_kind": candidate.candidate_kind,
        "status": candidate.status,
        "source_feedback_id": candidate.source_feedback_id,
        "source_correction_id": candidate.source_correction_id,
        "source_rag_trace_id": candidate.source_rag_trace_id,
        "quote_id": candidate.quote_id,
        "quote_job_id": candidate.quote_job_id,
        "trace_id": candidate.trace_id,
        "username": candidate.username,
        "item_name": candidate.item_name,
        "unit_price": _round(candidate.unit_price, 2),
        "unit": candidate.unit,
        "notes": candidate.notes,
        "existing_material_id": candidate.existing_material_id,
        "suggested_material_id": candidate.suggested_material_id,
        "material_id": candidate.material_id,
        "confidence_score": _round(candidate.confidence_score, 4),
        "reason": candidate.reason,
        "evidence": _json_load(candidate.evidence_json),
        "created_by": candidate.created_by,
        "reviewed_by": candidate.reviewed_by,
        "review_note": candidate.review_note,
        "reviewed_at": _format_dt(candidate.reviewed_at),
        "created_at": _format_dt(candidate.created_at),
        "updated_at": _format_dt(candidate.updated_at),
        "is_draft_material": candidate.is_draft_material,
    }


def summarize_candidates(db: Session) -> dict:
    by_status = [
        {"status": status, "count": count}
        for status, count in (
            db.query(KnowledgeCandidate.status, func.count(KnowledgeCandidate.id))
            .group_by(KnowledgeCandidate.status)
            .all()
        )
    ]
    by_kind = [
        {"candidate_kind": kind, "count": count}
        for kind, count in (
            db.query(KnowledgeCandidate.candidate_kind, func.count(KnowledgeCandidate.id))
            .group_by(KnowledgeCandidate.candidate_kind)
            .all()
        )
    ]
    return {
        "total": db.query(KnowledgeCandidate).count(),
        "pending": db.query(KnowledgeCandidate).filter(KnowledgeCandidate.status == "pending").count(),
        "approved": db.query(KnowledgeCandidate).filter(KnowledgeCandidate.status == "approved").count(),
        "rejected": db.query(KnowledgeCandidate).filter(KnowledgeCandidate.status == "rejected").count(),
        "by_status": by_status,
        "by_kind": by_kind,
    }


def rag_trace_insights(db: Session, *, days: Optional[int], min_count: int, limit: int) -> list[dict]:
    query = db.query(QuoteRagTrace, QuoteFeedback).join(QuoteFeedback, QuoteFeedback.id == QuoteRagTrace.feedback_id)
    if days:
        cutoff = _utcnow() - timedelta(days=days)
        query = query.filter(QuoteRagTrace.created_at >= cutoff)
    rows = query.all()

    grouped: dict[tuple[str, str], dict] = {}
    for trace, feedback in rows:
        key = (trace.material_id or "", trace.item_name or "")
        bucket = grouped.setdefault(
            key,
            {
                "material_id": trace.material_id,
                "item_name": trace.item_name,
                "count": 0,
                "score_sum": 0.0,
                "score_count": 0,
                "rejected_count": 0,
                "modified_count": 0,
                "adopted_count": 0,
                "cited_count": 0,
                "unknown_adoption_count": 0,
                "collection_alias": trace.collection_alias,
            },
        )
        bucket["count"] += 1
        if trace.score is not None:
            bucket["score_sum"] += trace.score
            bucket["score_count"] += 1
        if feedback.rejected:
            bucket["rejected_count"] += 1
        if feedback.was_modified:
            bucket["modified_count"] += 1
        if trace.adopted_by_user is True:
            bucket["adopted_count"] += 1
        elif trace.adopted_by_user is None:
            bucket["unknown_adoption_count"] += 1
        if trace.cited_by_model is True:
            bucket["cited_count"] += 1

    result = []
    for item in grouped.values():
        if item["count"] < min_count:
            continue
        avg_score = item["score_sum"] / item["score_count"] if item["score_count"] else None
        review_reasons = []
        if item["rejected_count"]:
            review_reasons.append("retrieved in rejected quotes")
        if item["modified_count"]:
            review_reasons.append("retrieved in manually modified quotes")
        if avg_score is not None and avg_score < 0.65:
            review_reasons.append("low average retrieval score")
        if item["adopted_count"] == 0 and item["count"] >= 3:
            review_reasons.append("no explicit adoption signal")
        result.append(
            {
                "material_id": item["material_id"],
                "item_name": item["item_name"],
                "count": item["count"],
                "avg_score": _round(avg_score, 6),
                "rejected_count": item["rejected_count"],
                "modified_count": item["modified_count"],
                "adopted_count": item["adopted_count"],
                "cited_count": item["cited_count"],
                "unknown_adoption_count": item["unknown_adoption_count"],
                "collection_alias": item["collection_alias"],
                "needs_review": bool(review_reasons),
                "review_reasons": review_reasons,
            }
        )
    result.sort(key=lambda item: (item["needs_review"], item["count"]), reverse=True)
    return result[:limit]
