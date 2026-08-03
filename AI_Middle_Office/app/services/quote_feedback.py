import json
import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.material import MaterialSnapshot
from app.models.quote_feedback import QuoteCorrection, QuoteFeedback, QuoteRagTrace
from app.models.quote_job import QuoteJob
from app.services.quote_cost_evidence import (
    safe_record_confirmed_cost_evidence,
    safe_record_preview_cost_evidence,
    safe_record_rejected_cost_evidence,
)


logger = logging.getLogger(__name__)

DETAIL_FIELDS = {
    "project_name",
    "unit",
    "quantity",
    "unit_price",
    "total_price",
    "notes",
    "space",
    "spec",
    "material",
    "craft",
}
FIELD_LABELS = {
    "project_name": "项目名称",
    "unit": "单位",
    "quantity": "数量",
    "unit_price": "单价",
    "total_price": "小计",
    "notes": "备注",
    "space": "空间",
    "spec": "规格",
    "material": "材料",
    "craft": "工艺",
    "row": "整行",
}
TRACE_KEYS = {
    "rag_trace",
    "rag_traces",
    "ragTrace",
    "retrieved_materials",
    "retrievedMaterials",
    "retrieval_results",
    "retrievalResults",
    "retrieved_context",
    "retrievedContext",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(raw_value: Optional[str]) -> Any:
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except Exception:
        return None


def _strip_large_fields(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    if "excel_base64" in clean:
        clean["excel_base64"] = f"<base64:{len(clean.get('excel_base64') or '')}>"
    return clean


def _parse_amount(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _extract_project_payload(value: Any, depth: int = 0) -> Any:
    if value is None or depth > 6:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return _extract_project_payload(json.loads(stripped), depth + 1)
        except Exception:
            object_start = stripped.find("{")
            object_end = stripped.rfind("}")
            if object_start >= 0 and object_end > object_start:
                try:
                    return _extract_project_payload(json.loads(stripped[object_start : object_end + 1]), depth + 1)
                except Exception:
                    return value
            return value
    if isinstance(value, dict):
        if isinstance(value.get("project_details"), list):
            return value
        for key in ("data", "result", "payload", "output", "answer", "message"):
            nested = value.get(key)
            found = _extract_project_payload(nested, depth + 1)
            if isinstance(found, dict) and isinstance(found.get("project_details"), list):
                return found
    return value


def _project_details(payload: Any) -> list[dict[str, Any]]:
    candidate = _extract_project_payload(payload)
    if isinstance(candidate, dict) and isinstance(candidate.get("project_details"), list):
        return [item for item in candidate["project_details"] if isinstance(item, dict)]
    if isinstance(candidate, list):
        return [item for item in candidate if isinstance(item, dict)]
    return []


def _total_amount(payload: Any) -> float:
    details = _project_details(payload)
    if details:
        return round(sum(_parse_amount(item.get("total_price")) or 0.0 for item in details), 2)
    candidate = _extract_project_payload(payload)
    if isinstance(candidate, dict):
        for key in ("total_amount", "total", "amount"):
            amount = _parse_amount(candidate.get(key))
            if amount is not None:
                return round(amount, 2)
    return 0.0


def _value_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return _json_dumps(value)
    if value is None:
        return ""
    return str(value)


def _text_or_none(value: Any, max_length: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length] if max_length is not None else text


def _display_text(value: Any, max_length: int = 255) -> str:
    text = _value_text(value).strip()
    if len(text) > max_length:
        return f"{text[: max_length - 3]}..."
    return text


def _project_name_from_item(item: dict[str, Any]) -> Optional[str]:
    return _text_or_none(item.get("project_name") or item.get("name") or item.get("item_name"), 255)


def _project_names(payload: Any, limit: int = 5) -> list[str]:
    names: list[str] = []
    for item in _project_details(payload):
        name = _project_name_from_item(item)
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _project_summary(payload: Any) -> str:
    details = _project_details(payload)
    if not details:
        return ""
    names = _project_names(payload, limit=3)
    if not names:
        return f"{len(details)} items"
    suffix = f"; +{len(details) - len(names)} more" if len(details) > len(names) else ""
    return f"{', '.join(names)}; total_items={len(details)}{suffix}"


def _field_key(field_path: str) -> str:
    return (field_path or "").rsplit(".", 1)[-1] or field_path


def _field_label(field_key: str) -> str:
    return FIELD_LABELS.get(field_key, field_key)


def _change_type(before_row: dict[str, Any], after_row: dict[str, Any], key: str) -> str:
    if key == "row":
        if before_row and not after_row:
            return "removed"
        if after_row and not before_row:
            return "added"
    before_empty = before_row == {} or before_row.get(key) in (None, "")
    after_empty = after_row == {} or after_row.get(key) in (None, "")
    if before_empty and not after_empty:
        return "added"
    if after_empty and not before_empty:
        return "removed"
    return "updated"


def _build_change_summary(corrections: list[QuoteCorrection], amount_delta: Optional[float]) -> tuple[str, str]:
    if not corrections and not amount_delta:
        return "No manual changes", ""
    field_counts = Counter(item.field_label or _field_label(_field_key(item.field_path)) for item in corrections)
    top_labels = [label for label, _ in field_counts.most_common(5)]
    parts = [f"{len(corrections)} field changes"] if corrections else []
    if top_labels:
        parts.append("top fields: " + ", ".join(top_labels[:3]))
    if amount_delta:
        parts.append(f"amount delta: {round(amount_delta, 2)}")
    return "; ".join(parts), ", ".join(top_labels)


def _apply_feedback_context(
    feedback: QuoteFeedback,
    *,
    job: Optional[QuoteJob] = None,
    request_text: Optional[str] = None,
    source_file_name: Optional[str] = None,
    payload: Any = None,
) -> None:
    if request_text or (job and job.message):
        feedback.request_text = _text_or_none(request_text or job.message)
    if source_file_name or (job and job.file_name):
        feedback.source_file_name = _text_or_none(source_file_name or job.file_name, 255)
    summary = _project_summary(payload) if payload is not None else ""
    if summary:
        feedback.project_summary = summary


def _normalized_value(value: Any) -> Any:
    amount = _parse_amount(value)
    if amount is not None:
        return round(amount, 4)
    if isinstance(value, str):
        return value.strip()
    return value


def _latest_snapshot_id(db: Session) -> Optional[str]:
    try:
        snapshot = db.query(MaterialSnapshot).order_by(MaterialSnapshot.id.desc()).first()
        return snapshot.snapshot_id if snapshot else None
    except Exception:
        return None


def _apply_runtime_metadata(feedback: QuoteFeedback, db: Session, payload: Any = None) -> None:
    candidate = _extract_project_payload(payload) if payload is not None else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    feedback.dify_app_version = candidate.get("dify_app_version") or settings.dify_app_version or feedback.dify_app_version
    feedback.dify_workflow_version = (
        candidate.get("dify_workflow_version") or settings.dify_workflow_version or feedback.dify_workflow_version
    )
    feedback.dify_prompt_version = (
        candidate.get("dify_prompt_version") or settings.dify_prompt_version or feedback.dify_prompt_version
    )
    feedback.dify_release_id = candidate.get("dify_release_id") or settings.dify_release_id or feedback.dify_release_id
    feedback.quote_model_name = candidate.get("quote_model_name") or "dify-deepseek"
    feedback.vision_model_name = settings.glm_vision_model
    feedback.rag_collection_alias = (
        candidate.get("rag_collection_alias") or settings.rag_collection_alias or feedback.rag_collection_alias
    )
    feedback.material_snapshot_id = (
        candidate.get("material_snapshot_id") or candidate.get("snapshot_id") or feedback.material_snapshot_id
    )
    if not feedback.material_snapshot_id:
        feedback.material_snapshot_id = _latest_snapshot_id(db)


def _feedback_by_context(
    db: Session,
    *,
    quote_job_id: Optional[str] = None,
    quote_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Optional[QuoteFeedback]:
    if quote_job_id:
        feedback = db.query(QuoteFeedback).filter(QuoteFeedback.quote_job_id == quote_job_id).first()
        if feedback:
            return feedback
    if quote_id:
        feedback = db.query(QuoteFeedback).filter(QuoteFeedback.quote_id == quote_id).first()
        if feedback:
            return feedback
    if trace_id:
        return db.query(QuoteFeedback).filter(QuoteFeedback.trace_id == trace_id).order_by(QuoteFeedback.id.desc()).first()
    return None


def _extract_trace_lists(value: Any, depth: int = 0) -> Iterable[list[Any]]:
    if value is None or depth > 6:
        return
    if isinstance(value, str):
        try:
            yield from _extract_trace_lists(json.loads(value), depth + 1)
        except Exception:
            return
    elif isinstance(value, dict):
        for key, nested in value.items():
            if key in TRACE_KEYS and isinstance(nested, list):
                yield nested
            else:
                yield from _extract_trace_lists(nested, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _extract_trace_lists(item, depth + 1)


def _has_trace_shape(item: dict[str, Any]) -> bool:
    return any(key in item for key in ("material_id", "item_name", "score", "rank", "distance", "id", "name"))


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def _trace_item_index(raw_item: dict[str, Any], details: list[dict[str, Any]]) -> Optional[int]:
    for key in ("item_index", "project_index", "detail_index", "line_no"):
        value = raw_item.get(key)
        if value is None:
            continue
        try:
            index = int(value)
            return index - 1 if key == "line_no" and index > 0 else index
        except (TypeError, ValueError):
            continue

    project_name = raw_item.get("project_name") or raw_item.get("quote_project_name") or raw_item.get("matched_project_name")
    if project_name:
        normalized = str(project_name).strip().lower()
        for index, detail in enumerate(details):
            if (_project_name_from_item(detail) or "").lower() == normalized:
                return index

    needle_values = [
        raw_item.get("item_name"),
        raw_item.get("name"),
        raw_item.get("title"),
        raw_item.get("material_id"),
        raw_item.get("id"),
    ]
    needles = [str(value).strip().lower() for value in needle_values if value not in (None, "")]
    for index, detail in enumerate(details):
        haystack = " ".join(
            str(detail.get(key) or "")
            for key in ("project_name", "item_name", "material", "notes", "spec", "craft")
        ).lower()
        if haystack and any(needle and needle in haystack for needle in needles):
            return index

    if len(details) == 1:
        return 0
    return None


def _trace_context(raw_item: dict[str, Any], source_payload: Any) -> tuple[Optional[int], Optional[str], Optional[str]]:
    details = _project_details(source_payload)
    item_index = _trace_item_index(raw_item, details)
    raw_project_name = raw_item.get("project_name") or raw_item.get("quote_project_name") or raw_item.get("matched_project_name")
    if item_index is not None and 0 <= item_index < len(details):
        return item_index, _project_name_from_item(details[item_index]) or _text_or_none(raw_project_name, 255), "matched quote item"
    if raw_project_name:
        return None, _text_or_none(raw_project_name, 255), "provided by trace"
    return None, None, None


def _trace_used_in_payload(trace: QuoteRagTrace, final_payload: Any) -> bool:
    needles = [
        trace.project_name,
        trace.item_name,
        trace.material_id,
    ]
    normalized_needles = [str(value).strip().lower() for value in needles if value not in (None, "")]
    if not normalized_needles:
        return False
    if trace.item_index is not None:
        details = _project_details(final_payload)
        if 0 <= trace.item_index < len(details):
            haystack = _json_dumps(details[trace.item_index]).lower()
            return any(needle in haystack for needle in normalized_needles)
    final_text = _json_dumps(_project_details(final_payload)).lower()
    return any(needle in final_text for needle in normalized_needles)


def _mark_rag_trace_usage(db: Session, *, feedback: QuoteFeedback, final_payload: Any) -> None:
    traces = db.query(QuoteRagTrace).filter(QuoteRagTrace.feedback_id == feedback.id).all()
    for trace in traces:
        used = _trace_used_in_payload(trace, final_payload)
        trace.used_in_final_quote = used
        trace.adopted_by_user = used
        if used and not trace.match_reason:
            trace.match_reason = "appears in final quote"


def _replace_rag_traces(
    db: Session,
    *,
    feedback: QuoteFeedback,
    source_payload: Any,
    query_text: Optional[str],
) -> None:
    trace_rows: list[QuoteRagTrace] = []
    for trace_list in _extract_trace_lists(source_payload):
        for index, raw_item in enumerate(trace_list):
            if not isinstance(raw_item, dict) or not _has_trace_shape(raw_item):
                continue
            material_id = raw_item.get("material_id") or raw_item.get("id")
            item_name = raw_item.get("item_name") or raw_item.get("name") or raw_item.get("title")
            score = _parse_amount(raw_item.get("score"))
            if score is None:
                score = _parse_amount(raw_item.get("distance"))
            rank = raw_item.get("rank")
            try:
                rank_value = int(rank) if rank is not None else index + 1
            except (TypeError, ValueError):
                rank_value = index + 1
            item_index, project_name, match_reason = _trace_context(raw_item, source_payload)
            trace_rows.append(
                QuoteRagTrace(
                    feedback_id=feedback.id,
                    quote_id=feedback.quote_id,
                    quote_job_id=feedback.quote_job_id,
                    trace_id=feedback.trace_id,
                    query_text=query_text,
                    item_index=item_index,
                    project_name=project_name,
                    material_id=str(material_id)[:64] if material_id is not None else None,
                    item_name=str(item_name)[:255] if item_name is not None else None,
                    rank=rank_value,
                    score=score,
                    collection_alias=raw_item.get("collection_alias") or feedback.rag_collection_alias,
                    material_snapshot_id=raw_item.get("material_snapshot_id") or feedback.material_snapshot_id,
                    sent_to_prompt=_bool_or_none(raw_item.get("sent_to_prompt")) is not False,
                    cited_by_model=_bool_or_none(raw_item.get("cited_by_model")),
                    adopted_by_user=_bool_or_none(raw_item.get("adopted_by_user")),
                    used_in_final_quote=_bool_or_none(raw_item.get("used_in_final_quote")),
                    match_reason=raw_item.get("match_reason") or match_reason,
                    raw_json=_json_dumps(raw_item),
                )
            )

    if not trace_rows:
        return

    db.query(QuoteRagTrace).filter(QuoteRagTrace.feedback_id == feedback.id).delete(synchronize_session=False)
    for trace in trace_rows:
        db.add(trace)


def _build_corrections(
    *,
    feedback: QuoteFeedback,
    ai_payload: Any,
    final_payload: Any,
    reason_category: Optional[str],
    reason_text: Optional[str],
) -> list[QuoteCorrection]:
    corrections: list[QuoteCorrection] = []
    ai_rows = _project_details(ai_payload)
    final_rows = _project_details(final_payload)
    max_len = max(len(ai_rows), len(final_rows))
    for index in range(max_len):
        before = ai_rows[index] if index < len(ai_rows) else {}
        after = final_rows[index] if index < len(final_rows) else {}
        keys = sorted((set(before) | set(after) | DETAIL_FIELDS) & (set(before) | set(after)))
        if not keys and before != after:
            keys = ["row"]
        project_name = after.get("project_name") or before.get("project_name")
        for key in keys:
            before_value = before if key == "row" else before.get(key)
            after_value = after if key == "row" else after.get(key)
            if _normalized_value(before_value) == _normalized_value(after_value):
                continue
            delta_amount = None
            if key in {"unit_price", "total_price"}:
                before_amount = _parse_amount(before_value) or 0.0
                after_amount = _parse_amount(after_value) or 0.0
                delta_amount = round(after_amount - before_amount, 2)
            before_text = _value_text(before_value)
            after_text = _value_text(after_value)
            corrections.append(
                QuoteCorrection(
                    feedback_id=feedback.id,
                    quote_id=feedback.quote_id,
                    quote_job_id=feedback.quote_job_id,
                    trace_id=feedback.trace_id,
                    item_index=index,
                    project_name=str(project_name)[:255] if project_name else None,
                    field_path=f"project_details[{index}].{key}",
                    field_label=_field_label(key),
                    change_type=_change_type(before, after, key),
                    before_value=before_text,
                    after_value=after_text,
                    before_display=_display_text(before_value),
                    after_display=_display_text(after_value),
                    delta_amount=delta_amount,
                    reason_category=reason_category,
                    reason_text=reason_text,
                )
            )
    return corrections


def record_ai_preview(
    db: Session,
    *,
    username: str,
    ai_payload: Any,
    quote_job: Optional[QuoteJob] = None,
    quote_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    source: str = "async_job",
    query_text: Optional[str] = None,
) -> QuoteFeedback:
    quote_job_id = quote_job.job_id if quote_job else None
    trace_id = trace_id or (quote_job.trace_id if quote_job else None)
    quote_id = quote_id or quote_job_id or str(uuid.uuid4())
    feedback = _feedback_by_context(db, quote_job_id=quote_job_id, quote_id=quote_id, trace_id=trace_id)
    if not feedback:
        feedback = QuoteFeedback(
            quote_id=quote_id,
            quote_job_id=quote_job_id,
            username=username,
            trace_id=trace_id,
            source=source,
        )
        db.add(feedback)
        db.flush()

    details = _project_details(ai_payload)
    feedback.username = username
    feedback.source = source
    feedback.status = feedback.status if feedback.status in {"confirmed", "rejected"} else "pending_review"
    feedback.ai_total_amount = _total_amount(ai_payload)
    feedback.ai_item_count = len(details)
    feedback.ai_payload_json = _json_dumps(ai_payload)
    _apply_feedback_context(feedback, job=quote_job, request_text=query_text, payload=ai_payload)
    _apply_runtime_metadata(feedback, db, ai_payload)
    db.flush()
    _replace_rag_traces(db, feedback=feedback, source_payload=ai_payload, query_text=query_text)
    safe_record_preview_cost_evidence(db, feedback=feedback, payload=ai_payload)
    return feedback


def record_confirmed_quote(
    db: Session,
    *,
    username: str,
    final_payload: dict[str, Any],
    quote_history_id: Optional[int] = None,
    allow_cross_user: bool = False,
) -> QuoteFeedback:
    quote_job_id = final_payload.get("quote_job_id") or final_payload.get("job_id")
    trace_id = final_payload.get("trace_id")
    job = db.query(QuoteJob).filter(QuoteJob.job_id == quote_job_id).first() if quote_job_id else None
    if job and job.username != username and not allow_cross_user:
        logger.warning(
            "quote_feedback_cross_user_job_ignored",
            extra={"event": "quote_feedback_cross_user_job_ignored", "quote_job_id": quote_job_id},
        )
        job = None
        quote_job_id = None
    quote_id = final_payload.get("quote_id") or quote_job_id or trace_id or str(uuid.uuid4())
    feedback = _feedback_by_context(db, quote_job_id=quote_job_id, quote_id=quote_id, trace_id=trace_id)
    if not feedback and job and job.result_json:
        feedback = record_ai_preview(
            db,
            username=username,
            ai_payload=_json_loads(job.result_json),
            quote_job=job,
            quote_id=quote_id,
            trace_id=trace_id or job.trace_id,
            source="async_job",
            query_text=job.message,
        )
    if not feedback:
        feedback = QuoteFeedback(
            quote_id=quote_id,
            quote_job_id=quote_job_id,
            username=username,
            trace_id=trace_id or (job.trace_id if job else None),
            source="async_job" if quote_job_id else "confirm_push",
        )
        db.add(feedback)
        db.flush()

    ai_payload = _json_loads(feedback.ai_payload_json)
    if ai_payload is None and job and job.result_json:
        ai_payload = _json_loads(job.result_json)

    clean_final_payload = _strip_large_fields(final_payload)
    final_total = _total_amount(clean_final_payload)
    ai_total = feedback.ai_total_amount if feedback.ai_total_amount is not None else _total_amount(ai_payload)
    feedback.username = username
    feedback.quote_history_id = quote_history_id
    feedback.trace_id = feedback.trace_id or trace_id or (job.trace_id if job else None)
    feedback.status = "confirmed"
    feedback.final_total_amount = final_total
    feedback.ai_total_amount = ai_total
    feedback.amount_delta = round(final_total - (ai_total or 0.0), 2)
    feedback.amount_delta_ratio = round(feedback.amount_delta / ai_total, 6) if ai_total else None
    feedback.final_item_count = len(_project_details(clean_final_payload))
    feedback.ai_item_count = feedback.ai_item_count or len(_project_details(ai_payload))
    feedback.pushed_to_dingtalk = True
    feedback.rejected = False
    feedback.final_payload_json = _json_dumps(clean_final_payload)
    feedback.reviewed_by = username
    feedback.confirmed_at = _utcnow()
    _apply_feedback_context(feedback, job=job, payload=clean_final_payload)
    _apply_runtime_metadata(feedback, db, clean_final_payload)
    db.flush()

    reason_category = final_payload.get("feedback_reason_category")
    reason_text = final_payload.get("feedback_reason")
    db.query(QuoteCorrection).filter(QuoteCorrection.feedback_id == feedback.id).delete(synchronize_session=False)
    corrections = _build_corrections(
        feedback=feedback,
        ai_payload=ai_payload,
        final_payload=clean_final_payload,
        reason_category=reason_category,
        reason_text=reason_text,
    )
    for correction in corrections:
        db.add(correction)
    feedback.was_modified = bool(corrections or feedback.amount_delta)
    feedback.change_summary, feedback.top_changed_fields = _build_change_summary(corrections, feedback.amount_delta)
    feedback.correction_summary_json = _json_dumps(
        {
            "correction_count": len(corrections),
            "reason_category": reason_category,
            "reason": reason_text,
            "change_summary": feedback.change_summary,
            "top_changed_fields": feedback.top_changed_fields,
        }
    )
    _mark_rag_trace_usage(db, feedback=feedback, final_payload=clean_final_payload)
    safe_record_confirmed_cost_evidence(
        db,
        feedback=feedback,
        ai_payload=ai_payload,
        final_payload=clean_final_payload,
    )
    return feedback


def record_rejected_quote(
    db: Session,
    *,
    username: str,
    quote_job_id: Optional[str],
    trace_id: Optional[str],
    reason: Optional[str] = None,
    allow_cross_user: bool = False,
) -> QuoteFeedback:
    job = db.query(QuoteJob).filter(QuoteJob.job_id == quote_job_id).first() if quote_job_id else None
    if job and job.username != username and not allow_cross_user:
        logger.warning(
            "quote_feedback_cross_user_reject_ignored",
            extra={"event": "quote_feedback_cross_user_reject_ignored", "quote_job_id": quote_job_id},
        )
        job = None
        quote_job_id = None
    feedback = _feedback_by_context(db, quote_job_id=quote_job_id, trace_id=trace_id)
    if not feedback and job and job.result_json:
        feedback = record_ai_preview(
            db,
            username=username,
            ai_payload=_json_loads(job.result_json),
            quote_job=job,
            trace_id=trace_id or job.trace_id,
            source="async_job",
            query_text=job.message,
        )
    if not feedback:
        feedback = QuoteFeedback(
            quote_id=quote_job_id or trace_id or str(uuid.uuid4()),
            quote_job_id=quote_job_id,
            username=username,
            trace_id=trace_id or (job.trace_id if job else None),
            source="async_job" if quote_job_id else "manual_review",
        )
        db.add(feedback)
        db.flush()

    feedback.username = username
    feedback.status = "rejected"
    feedback.rejected = True
    feedback.pushed_to_dingtalk = False
    feedback.rejection_reason = reason
    feedback.reviewed_by = username
    feedback.change_summary = f"Rejected: {reason}" if reason else "Rejected by reviewer"
    _apply_feedback_context(feedback, job=job)
    feedback.rejected_at = _utcnow()
    _apply_runtime_metadata(feedback, db)
    ai_payload = _json_loads(feedback.ai_payload_json)
    if ai_payload is None and job and job.result_json:
        ai_payload = _json_loads(job.result_json)
    safe_record_rejected_cost_evidence(db, feedback=feedback, ai_payload=ai_payload)
    return feedback


def safe_record_ai_preview(db: Session, **kwargs: Any) -> None:
    try:
        record_ai_preview(db, **kwargs)
    except Exception:
        db.rollback()
        logger.exception("quote_feedback_preview_record_failed", extra={"event": "quote_feedback_preview_record_failed"})
