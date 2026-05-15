import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.services.quote_history import json_loads, project_details, project_names, text_or_none, total_amount


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def first_project_names_text(payload: Any, limit: int = 5) -> str:
    return ", ".join(project_names(project_details(payload), limit=limit))


def build_request_summary(message: Optional[str], file_name: Optional[str]) -> str:
    text = text_or_none(message, 180)
    if text:
        return text
    if file_name:
        return f"Attachment: {file_name}"
    return "Quote request"


def apply_job_request_summary(job: QuoteJob) -> None:
    job.request_summary = build_request_summary(job.message, job.file_name)
    job.source_file_name = job.file_name


def apply_job_result_summary(job: QuoteJob, result_payload: Any) -> None:
    details = project_details(result_payload)
    job.result_total_amount = total_amount(result_payload)
    job.result_item_count = len(details)
    job.preview_project_names = first_project_names_text(result_payload)


def apply_job_failure(job: QuoteJob, stage: Optional[str] = None) -> None:
    job.failure_stage = stage or job.stage


def apply_job_duration(job: QuoteJob) -> None:
    if not job.created_at or not job.finished_at:
        return
    started = job.created_at
    finished = job.finished_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    job.duration_ms = max(0, int((finished - started).total_seconds() * 1000))


def serialize_event_row(event: QuoteJobEvent) -> dict[str, Any]:
    payload = json_loads(event.payload_json)
    data = {
        "id": event.id,
        "event_index": event.event_index,
        "status": event.event_type,
        "event_type": event.event_type,
        "stage": event.stage,
        "message": event.message,
        "trace_id": event.trace_id,
        "payload": payload,
        "created_at": event.created_at.strftime("%Y-%m-%d %H:%M:%S") if event.created_at else None,
    }
    if isinstance(payload, dict):
        data.update(payload)
    return data


def event_rows_from_json(raw_events: Optional[str]) -> list[dict[str, Any]]:
    events = json_loads(raw_events)
    if not isinstance(events, list):
        return []
    rows = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue
        payload = {key: value for key, value in event.items() if key not in {"status", "stage", "message", "trace_id"}}
        row = {
            "id": None,
            "event_index": index,
            "status": event.get("status"),
            "event_type": event.get("status"),
            "stage": event.get("stage"),
            "message": event.get("message"),
            "trace_id": event.get("trace_id"),
            "payload": payload,
            "created_at": event.get("created_at"),
        }
        row.update(payload)
        rows.append(row)
    return rows


def create_job_event_from_payload(job: QuoteJob, event_index: int, payload: dict[str, Any]) -> QuoteJobEvent:
    stage = payload.get("stage")
    extra_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"status", "message", "trace_id", "stage", "created_at"}
    }
    return QuoteJobEvent(
        quote_job_id=job.job_id,
        event_index=event_index,
        event_type=str(payload.get("status") or "event")[:32],
        stage=str(stage)[:64] if stage is not None else None,
        message=str(payload.get("message") or ""),
        trace_id=str(payload.get("trace_id") or job.trace_id or "")[:64] or None,
        payload_json=json_dumps(extra_payload) if extra_payload else None,
        created_at=_parse_event_datetime(payload.get("created_at")),
    )


def _parse_event_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00")
        return datetime.fromisoformat(value)
    except ValueError:
        return None
