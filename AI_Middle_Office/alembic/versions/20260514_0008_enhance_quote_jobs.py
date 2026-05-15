"""enhance quote job readability

Revision ID: 20260514_0008
Revises: 20260514_0007
Create Date: 2026-05-14
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260514_0008"
down_revision: Union[str, None] = "20260514_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DETAIL_LIST_KEYS = ("project_details", "items", "details")


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def _json_loads(raw_value: Optional[str]) -> Any:
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except Exception:
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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


def _text_or_none(value: Any, max_length: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_length is not None:
        return text[:max_length]
    return text


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
        for key in DETAIL_LIST_KEYS:
            if isinstance(value.get(key), list):
                return value
        for key in ("data", "result", "payload", "output", "answer", "message"):
            found = _extract_project_payload(value.get(key), depth + 1)
            if isinstance(found, dict) and any(isinstance(found.get(item), list) for item in DETAIL_LIST_KEYS):
                return found
    return value


def _project_details(payload: Any) -> list[dict[str, Any]]:
    candidate = _extract_project_payload(payload)
    if isinstance(candidate, dict):
        for key in DETAIL_LIST_KEYS:
            if isinstance(candidate.get(key), list):
                return [item for item in candidate[key] if isinstance(item, dict)]
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


def _project_names(details: list[dict[str, Any]], limit: int = 5) -> list[str]:
    names: list[str] = []
    for item in details:
        name = _text_or_none(item.get("project_name") or item.get("name") or item.get("item_name"), 255)
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _as_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_ms(created_at: Any, finished_at: Any) -> Optional[int]:
    start = _as_utc(created_at)
    end = _as_utc(finished_at)
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_event_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00")
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _backfill_quote_jobs() -> None:
    bind = op.get_bind()
    if "quote_jobs" not in _tables():
        return
    rows = bind.execute(
        sa.text(
            "SELECT id, job_id, message, file_name, result_json, events_json, status, stage, "
            "created_at, finished_at FROM quote_jobs ORDER BY id ASC"
        )
    ).mappings()
    event_table = sa.table(
        "quote_job_events",
        sa.column("quote_job_id", sa.String()),
        sa.column("event_index", sa.Integer()),
        sa.column("event_type", sa.String()),
        sa.column("stage", sa.String()),
        sa.column("message", sa.Text()),
        sa.column("trace_id", sa.String()),
        sa.column("payload_json", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for row in rows:
        result_payload = _json_loads(row["result_json"])
        details = _project_details(result_payload)
        finished_at = row["finished_at"]
        stage = row["stage"]
        failure_stage = stage if row["status"] in {"failed", "canceled", "timed_out"} else None
        bind.execute(
            sa.text(
                "UPDATE quote_jobs SET "
                "request_summary = COALESCE(request_summary, :request_summary), "
                "source_file_name = COALESCE(source_file_name, :source_file_name), "
                "result_total_amount = COALESCE(result_total_amount, :result_total_amount), "
                "result_item_count = COALESCE(result_item_count, :result_item_count), "
                "preview_project_names = COALESCE(preview_project_names, :preview_project_names), "
                "duration_ms = COALESCE(duration_ms, :duration_ms), "
                "failure_stage = COALESCE(failure_stage, :failure_stage) "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "request_summary": _text_or_none(row["message"], 180)
                or (f"Attachment: {row['file_name']}" if row["file_name"] else "Quote request"),
                "source_file_name": row["file_name"],
                "result_total_amount": _total_amount(result_payload) if result_payload is not None else None,
                "result_item_count": len(details),
                "preview_project_names": ", ".join(_project_names(details)),
                "duration_ms": _duration_ms(row["created_at"], finished_at),
                "failure_stage": failure_stage,
            },
        )

        existing_count = bind.execute(
            sa.text("SELECT COUNT(1) FROM quote_job_events WHERE quote_job_id = :job_id"),
            {"job_id": row["job_id"]},
        ).scalar()
        if existing_count:
            continue
        events = _json_loads(row["events_json"])
        if not isinstance(events, list):
            continue
        event_rows = []
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                continue
            payload = {
                key: value
                for key, value in event.items()
                if key not in {"status", "message", "trace_id", "stage", "created_at"}
            }
            event_rows.append(
                {
                    "quote_job_id": row["job_id"],
                    "event_index": index,
                    "event_type": str(event.get("status") or "event")[:32],
                    "stage": _text_or_none(event.get("stage"), 64),
                    "message": _text_or_none(event.get("message")),
                    "trace_id": _text_or_none(event.get("trace_id"), 64),
                    "payload_json": _json_dumps(payload) if payload else None,
                    "created_at": _parse_event_datetime(event.get("created_at")) or datetime.now(timezone.utc),
                }
            )
        if event_rows:
            bind.execute(event_table.insert(), event_rows)


def upgrade() -> None:
    existing_tables = _tables()
    if "quote_jobs" not in existing_tables:
        return

    _add_column_if_missing("quote_jobs", sa.Column("request_summary", sa.String(length=255), nullable=True))
    _add_column_if_missing("quote_jobs", sa.Column("source_file_name", sa.String(length=255), nullable=True))
    _add_column_if_missing("quote_jobs", sa.Column("result_total_amount", sa.Float(), nullable=True))
    _add_column_if_missing(
        "quote_jobs",
        sa.Column("result_item_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing("quote_jobs", sa.Column("preview_project_names", sa.Text(), nullable=True))
    _add_column_if_missing("quote_jobs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    _add_column_if_missing("quote_jobs", sa.Column("failure_stage", sa.String(length=64), nullable=True))
    _create_index_if_missing("ix_quote_jobs_failure_stage", "quote_jobs", ["failure_stage"])

    if "quote_job_events" not in existing_tables:
        op.create_table(
            "quote_job_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("quote_job_id", sa.String(length=36), sa.ForeignKey("quote_jobs.job_id"), nullable=False),
            sa.Column("event_index", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("stage", sa.String(length=64), nullable=True),
            sa.Column("message", _long_text(), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("payload_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_quote_job_events_id", "quote_job_events", ["id"])
    _create_index_if_missing("ix_quote_job_events_quote_job_id", "quote_job_events", ["quote_job_id"])
    _create_index_if_missing("ix_quote_job_events_event_type", "quote_job_events", ["event_type"])
    _create_index_if_missing("ix_quote_job_events_stage", "quote_job_events", ["stage"])
    _create_index_if_missing("ix_quote_job_events_trace_id", "quote_job_events", ["trace_id"])
    _backfill_quote_jobs()


def downgrade() -> None:
    if "quote_job_events" in _tables():
        op.drop_table("quote_job_events")

    existing_columns = _columns("quote_jobs")
    for column in [
        "failure_stage",
        "duration_ms",
        "preview_project_names",
        "result_item_count",
        "result_total_amount",
        "source_file_name",
        "request_summary",
    ]:
        if column in existing_columns:
            op.drop_column("quote_jobs", column)
