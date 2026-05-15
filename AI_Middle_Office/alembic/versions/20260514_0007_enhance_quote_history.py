"""enhance quote history readability

Revision ID: 20260514_0007
Revises: 20260507_0006
Create Date: 2026-05-14
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260514_0007"
down_revision: Union[str, None] = "20260507_0006"
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


def _strip_large_fields(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    if "excel_base64" in clean:
        clean["excel_base64"] = f"<base64:{len(clean.get('excel_base64') or '')}>"
    return clean


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


def _project_summary(details: list[dict[str, Any]]) -> str:
    names = _project_names(details, limit=3)
    if not details:
        return ""
    if not names:
        return f"{len(details)} items"
    suffix = f"; +{len(details) - len(names)} more" if len(details) > len(names) else ""
    return f"{', '.join(names)}; total_items={len(details)}{suffix}"


def _display_title(payload: dict[str, Any], details: list[dict[str, Any]]) -> str:
    explicit = _text_or_none(payload.get("display_title"), 255)
    if explicit:
        return explicit
    names = _project_names(details, limit=1)
    if names:
        return f"{names[0]} + {len(details) - 1} items" if len(details) > 1 else names[0]
    return "Quote history"


def _item_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _backfill_quote_history() -> None:
    bind = op.get_bind()
    if "quote_history" not in _tables() or "quote_history_items" not in _tables():
        return

    rows = bind.execute(
        sa.text(
            "SELECT id, username, total_amount, item_count, payload_json "
            "FROM quote_history ORDER BY id ASC"
        )
    ).mappings()
    item_table = sa.table(
        "quote_history_items",
        sa.column("quote_history_id", sa.Integer()),
        sa.column("line_no", sa.Integer()),
        sa.column("project_name", sa.String()),
        sa.column("space", sa.String()),
        sa.column("unit", sa.String()),
        sa.column("quantity", sa.Float()),
        sa.column("unit_price", sa.Float()),
        sa.column("total_price", sa.Float()),
        sa.column("material", sa.String()),
        sa.column("craft", sa.String()),
        sa.column("spec", sa.String()),
        sa.column("notes", sa.Text()),
        sa.column("raw_json", sa.Text()),
    )
    for row in rows:
        payload = _json_loads(row["payload_json"])
        payload = _strip_large_fields(payload) if isinstance(payload, dict) else payload
        details = _project_details(payload)
        names = _project_names(details)
        quote_id = None
        quote_job_id = None
        trace_id = None
        request_text = None
        source_file_name = None
        if isinstance(payload, dict):
            quote_job_id = _text_or_none(payload.get("quote_job_id") or payload.get("job_id"), 36)
            trace_id = _text_or_none(payload.get("trace_id"), 64)
            quote_id = _text_or_none(payload.get("quote_id") or quote_job_id or trace_id, 64)
            request_text = _text_or_none(payload.get("request_text") or payload.get("message"))
            source_file_name = _text_or_none(payload.get("source_file_name"), 255)
        quote_id = quote_id or str(uuid.uuid4())
        bind.execute(
            sa.text(
                "UPDATE quote_history SET "
                "quote_id = COALESCE(quote_id, :quote_id), "
                "quote_job_id = COALESCE(quote_job_id, :quote_job_id), "
                "trace_id = COALESCE(trace_id, :trace_id), "
                "request_text = COALESCE(request_text, :request_text), "
                "source_file_name = COALESCE(source_file_name, :source_file_name), "
                "display_title = COALESCE(display_title, :display_title), "
                "project_summary = COALESCE(project_summary, :project_summary), "
                "first_project_names = COALESCE(first_project_names, :first_project_names), "
                "confirmed_by = COALESCE(confirmed_by, :confirmed_by), "
                "pushed_to_dingtalk = COALESCE(pushed_to_dingtalk, :pushed_to_dingtalk), "
                "total_amount = :total_amount, "
                "item_count = :item_count, "
                "payload_json = :payload_json "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "quote_id": quote_id,
                "quote_job_id": quote_job_id,
                "trace_id": trace_id,
                "request_text": request_text,
                "source_file_name": source_file_name,
                "display_title": _display_title(payload if isinstance(payload, dict) else {}, details),
                "project_summary": _project_summary(details),
                "first_project_names": ", ".join(names),
                "confirmed_by": row["username"],
                "pushed_to_dingtalk": True,
                "total_amount": _total_amount(payload) if details else row["total_amount"],
                "item_count": len(details) if details else row["item_count"],
                "payload_json": _json_dumps(payload) if payload is not None else row["payload_json"],
            },
        )
        existing_count = bind.execute(
            sa.text("SELECT COUNT(1) FROM quote_history_items WHERE quote_history_id = :id"),
            {"id": row["id"]},
        ).scalar()
        if existing_count:
            continue
        item_rows = []
        for index, item in enumerate(details, start=1):
            item_rows.append(
                {
                    "quote_history_id": row["id"],
                    "line_no": index,
                    "project_name": _text_or_none(_item_value(item, "project_name", "name", "item_name"), 255),
                    "space": _text_or_none(_item_value(item, "space", "room", "area"), 128),
                    "unit": _text_or_none(_item_value(item, "unit"), 64),
                    "quantity": _parse_amount(_item_value(item, "quantity", "qty", "count")),
                    "unit_price": _parse_amount(_item_value(item, "unit_price", "price")),
                    "total_price": _parse_amount(_item_value(item, "total_price", "amount", "subtotal")),
                    "material": _text_or_none(_item_value(item, "material", "material_name"), 255),
                    "craft": _text_or_none(_item_value(item, "craft", "process", "workmanship"), 255),
                    "spec": _text_or_none(_item_value(item, "spec", "specification"), 255),
                    "notes": _text_or_none(_item_value(item, "notes", "remark", "description")),
                    "raw_json": _json_dumps(item),
                }
            )
        if item_rows:
            bind.execute(item_table.insert(), item_rows)


def upgrade() -> None:
    existing_tables = _tables()
    if "quote_history" not in existing_tables:
        return

    _add_column_if_missing("quote_history", sa.Column("quote_id", sa.String(length=64), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("quote_job_id", sa.String(length=36), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("trace_id", sa.String(length=64), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("request_text", _long_text(), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("source_file_name", sa.String(length=255), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("display_title", sa.String(length=255), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("project_summary", sa.String(length=512), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("first_project_names", sa.Text(), nullable=True))
    _add_column_if_missing("quote_history", sa.Column("confirmed_by", sa.String(length=64), nullable=True))
    _add_column_if_missing(
        "quote_history",
        sa.Column("pushed_to_dingtalk", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )
    _create_index_if_missing("ix_quote_history_quote_id", "quote_history", ["quote_id"])
    _create_index_if_missing("ix_quote_history_quote_job_id", "quote_history", ["quote_job_id"])
    _create_index_if_missing("ix_quote_history_trace_id", "quote_history", ["trace_id"])
    _create_index_if_missing("ix_quote_history_confirmed_by", "quote_history", ["confirmed_by"])

    if "quote_history_items" not in existing_tables:
        op.create_table(
            "quote_history_items",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("quote_history_id", sa.Integer(), sa.ForeignKey("quote_history.id"), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False),
            sa.Column("project_name", sa.String(length=255), nullable=True),
            sa.Column("space", sa.String(length=128), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("unit_price", sa.Float(), nullable=True),
            sa.Column("total_price", sa.Float(), nullable=True),
            sa.Column("material", sa.String(length=255), nullable=True),
            sa.Column("craft", sa.String(length=255), nullable=True),
            sa.Column("spec", sa.String(length=255), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("raw_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_quote_history_items_id", "quote_history_items", ["id"])
    _create_index_if_missing("ix_quote_history_items_quote_history_id", "quote_history_items", ["quote_history_id"])
    _create_index_if_missing("ix_quote_history_items_project_name", "quote_history_items", ["project_name"])
    _backfill_quote_history()


def downgrade() -> None:
    if "quote_history_items" in _tables():
        op.drop_table("quote_history_items")

    existing_columns = _columns("quote_history")
    for column in [
        "pushed_to_dingtalk",
        "confirmed_by",
        "first_project_names",
        "project_summary",
        "display_title",
        "source_file_name",
        "request_text",
        "trace_id",
        "quote_job_id",
        "quote_id",
    ]:
        if column in existing_columns:
            op.drop_column("quote_history", column)
