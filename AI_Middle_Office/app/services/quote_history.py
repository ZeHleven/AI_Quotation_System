import json
import re
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.quote_history import QuoteHistory, QuoteHistoryItem
from app.models.quote_job import QuoteJob


DETAIL_LIST_KEYS = ("project_details", "items", "details")


def json_loads(raw_value: Optional[str]) -> Any:
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except Exception:
        return None


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_amount(value: Any) -> Optional[float]:
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


def text_or_none(value: Any, max_length: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_length is not None:
        return text[:max_length]
    return text


def strip_large_fields(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    if "excel_base64" in clean:
        clean["excel_base64"] = f"<base64:{len(clean.get('excel_base64') or '')}>"
    return clean


def extract_project_payload(value: Any, depth: int = 0) -> Any:
    if value is None or depth > 6:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return extract_project_payload(json.loads(stripped), depth + 1)
        except Exception:
            object_start = stripped.find("{")
            object_end = stripped.rfind("}")
            if object_start >= 0 and object_end > object_start:
                try:
                    return extract_project_payload(json.loads(stripped[object_start : object_end + 1]), depth + 1)
                except Exception:
                    return value
            return value
    if isinstance(value, dict):
        for key in DETAIL_LIST_KEYS:
            if isinstance(value.get(key), list):
                return value
        for key in ("data", "result", "payload", "output", "answer", "message"):
            nested = value.get(key)
            found = extract_project_payload(nested, depth + 1)
            if isinstance(found, dict) and any(isinstance(found.get(item), list) for item in DETAIL_LIST_KEYS):
                return found
    return value


def project_details(payload: Any) -> list[dict[str, Any]]:
    candidate = extract_project_payload(payload)
    if isinstance(candidate, dict):
        for key in DETAIL_LIST_KEYS:
            if isinstance(candidate.get(key), list):
                return [item for item in candidate[key] if isinstance(item, dict)]
    if isinstance(candidate, list):
        return [item for item in candidate if isinstance(item, dict)]
    return []


def total_amount(payload: Any) -> float:
    details = project_details(payload)
    if details:
        return round(sum(parse_amount(item.get("total_price")) or 0.0 for item in details), 2)
    candidate = extract_project_payload(payload)
    if isinstance(candidate, dict):
        for key in ("total_amount", "total", "amount"):
            amount = parse_amount(candidate.get(key))
            if amount is not None:
                return round(amount, 2)
    return 0.0


def project_names(details: list[dict[str, Any]], limit: int = 5) -> list[str]:
    names: list[str] = []
    for item in details:
        name = text_or_none(item.get("project_name") or item.get("name") or item.get("item_name"), 255)
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def build_project_summary(details: list[dict[str, Any]]) -> str:
    names = project_names(details, limit=3)
    if not details:
        return ""
    if not names:
        return f"{len(details)} items"
    suffix = f"; +{len(details) - len(names)} more" if len(details) > len(names) else ""
    return f"{', '.join(names)}; total_items={len(details)}{suffix}"


def build_display_title(payload: dict[str, Any], details: list[dict[str, Any]], source_file_name: Optional[str]) -> str:
    explicit = text_or_none(payload.get("display_title"), 255)
    if explicit:
        return explicit
    names = project_names(details, limit=1)
    if names:
        if len(details) > 1:
            return f"{names[0]} + {len(details) - 1} items"
        return names[0]
    if source_file_name:
        return source_file_name
    return "Quote history"


def item_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def create_history_item(record: QuoteHistory, line_no: int, item: dict[str, Any]) -> QuoteHistoryItem:
    return QuoteHistoryItem(
        quote_history_id=record.id,
        line_no=line_no,
        project_name=text_or_none(item_value(item, "project_name", "name", "item_name"), 255),
        space=text_or_none(item_value(item, "space", "room", "area"), 128),
        unit=text_or_none(item_value(item, "unit"), 64),
        quantity=parse_amount(item_value(item, "quantity", "qty", "count")),
        unit_price=parse_amount(item_value(item, "unit_price", "price")),
        total_price=parse_amount(item_value(item, "total_price", "amount", "subtotal")),
        material=text_or_none(item_value(item, "material", "material_name"), 255),
        craft=text_or_none(item_value(item, "craft", "process", "workmanship"), 255),
        spec=text_or_none(item_value(item, "spec", "specification"), 255),
        notes=text_or_none(item_value(item, "notes", "remark", "description")),
        raw_json=json_dumps(item),
    )


def serialize_history_item(item: QuoteHistoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "line_no": item.line_no,
        "project_name": item.project_name,
        "space": item.space,
        "unit": item.unit,
        "quantity": item.quantity,
        "unit_price": item.unit_price,
        "total_price": item.total_price,
        "material": item.material,
        "craft": item.craft,
        "spec": item.spec,
        "notes": item.notes,
        "raw": json_loads(item.raw_json),
    }


def create_quote_history_record(
    db: Session,
    *,
    username: str,
    payload: dict[str, Any],
    confirmed_by: Optional[str] = None,
) -> QuoteHistory:
    clean_payload = strip_large_fields(payload)
    details = project_details(clean_payload)
    quote_job_id = text_or_none(payload.get("quote_job_id") or payload.get("job_id"), 36)
    job = db.query(QuoteJob).filter(QuoteJob.job_id == quote_job_id).first() if quote_job_id else None
    trace_id = text_or_none(payload.get("trace_id") or (job.trace_id if job else None), 64)
    quote_id = text_or_none(payload.get("quote_id") or quote_job_id or trace_id or str(uuid.uuid4()), 64)
    source_file_name = text_or_none((job.file_name if job else None) or payload.get("source_file_name"), 255)
    first_names = project_names(details)

    record = QuoteHistory(
        username=username,
        quote_id=quote_id,
        quote_job_id=quote_job_id,
        trace_id=trace_id,
        request_text=text_or_none((job.message if job else None) or payload.get("request_text") or payload.get("message")),
        source_file_name=source_file_name,
        display_title=build_display_title(clean_payload, details, source_file_name),
        project_summary=build_project_summary(details),
        first_project_names=", ".join(first_names),
        confirmed_by=confirmed_by or username,
        pushed_to_dingtalk=True,
        total_amount=total_amount(clean_payload),
        item_count=len(details),
        payload_json=json_dumps(clean_payload),
    )
    db.add(record)
    db.flush()
    for index, item in enumerate(details, start=1):
        db.add(create_history_item(record, index, item))
    return record
