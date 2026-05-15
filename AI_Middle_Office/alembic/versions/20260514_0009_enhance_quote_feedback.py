"""enhance quote feedback readability

Revision ID: 20260514_0009
Revises: 20260514_0008
Create Date: 2026-05-14
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260514_0009"
down_revision: Union[str, None] = "20260514_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def _text(value: Any, max_length: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length] if max_length else text


def _display(value: Any, max_length: int = 255) -> str:
    if isinstance(value, (dict, list)):
        text = _json_dumps(value)
    elif value is None:
        text = ""
    else:
        text = str(value)
    return f"{text[: max_length - 3]}..." if len(text) > max_length else text


def _extract_payload(value: Any, depth: int = 0) -> Any:
    if value is None or depth > 6:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return _extract_payload(json.loads(stripped), depth + 1)
        except Exception:
            return value
    if isinstance(value, dict):
        if isinstance(value.get("project_details"), list):
            return value
        for key in ("data", "result", "payload", "output", "answer", "message"):
            found = _extract_payload(value.get(key), depth + 1)
            if isinstance(found, dict) and isinstance(found.get("project_details"), list):
                return found
    return value


def _details(payload: Any) -> list[dict[str, Any]]:
    candidate = _extract_payload(payload)
    if isinstance(candidate, dict) and isinstance(candidate.get("project_details"), list):
        return [item for item in candidate["project_details"] if isinstance(item, dict)]
    if isinstance(candidate, list):
        return [item for item in candidate if isinstance(item, dict)]
    return []


def _project_name(item: dict[str, Any]) -> Optional[str]:
    return _text(item.get("project_name") or item.get("name") or item.get("item_name"), 255)


def _project_names(payload: Any, limit: int = 5) -> list[str]:
    names: list[str] = []
    for item in _details(payload):
        name = _project_name(item)
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _project_summary(payload: Any) -> str:
    rows = _details(payload)
    if not rows:
        return ""
    names = _project_names(payload, 3)
    if not names:
        return f"{len(rows)} items"
    suffix = f"; +{len(rows) - len(names)} more" if len(rows) > len(names) else ""
    return f"{', '.join(names)}; total_items={len(rows)}{suffix}"


def _field_key(field_path: str) -> str:
    return (field_path or "").rsplit(".", 1)[-1] or field_path


def _field_label(field_path: str) -> str:
    key = _field_key(field_path)
    return FIELD_LABELS.get(key, key)


def _change_type(before_value: Any, after_value: Any) -> str:
    before_empty = before_value in (None, "")
    after_empty = after_value in (None, "")
    if before_empty and not after_empty:
        return "added"
    if after_empty and not before_empty:
        return "removed"
    return "updated"


def _change_summary(corrections: list[dict[str, Any]], amount_delta: Optional[float]) -> tuple[str, str]:
    if not corrections and not amount_delta:
        return "No manual changes", ""
    labels = Counter(item["field_label"] for item in corrections if item.get("field_label"))
    top = [label for label, _ in labels.most_common(5)]
    parts = [f"{len(corrections)} field changes"] if corrections else []
    if top:
        parts.append("top fields: " + ", ".join(top[:3]))
    if amount_delta:
        parts.append(f"amount delta: {round(float(amount_delta), 2)}")
    return "; ".join(parts), ", ".join(top)


def _trace_used(trace: dict[str, Any], final_payload: Any) -> bool:
    needles = [
        trace.get("project_name"),
        trace.get("item_name"),
        trace.get("material_id"),
    ]
    normalized = [str(value).strip().lower() for value in needles if value not in (None, "")]
    if not normalized:
        return False
    final_text = _json_dumps(_details(final_payload)).lower()
    return any(item in final_text for item in normalized)


def _trace_context(trace: dict[str, Any], source_payload: Any) -> tuple[Optional[int], Optional[str], Optional[str]]:
    rows = _details(source_payload)
    raw = _json_loads(trace.get("raw_json")) if isinstance(trace.get("raw_json"), str) else None
    raw = raw if isinstance(raw, dict) else {}
    for key in ("item_index", "project_index", "detail_index"):
        value = raw.get(key)
        if value is not None:
            try:
                index = int(value)
                name = _project_name(rows[index]) if 0 <= index < len(rows) else None
                return index, name, "provided by trace"
            except (TypeError, ValueError, IndexError):
                pass
    needle = str(trace.get("item_name") or trace.get("material_id") or "").lower()
    if needle:
        for index, row in enumerate(rows):
            haystack = _json_dumps(row).lower()
            if needle in haystack:
                return index, _project_name(row), "matched quote item"
    if len(rows) == 1:
        return 0, _project_name(rows[0]), "single quote item"
    return None, None, None


def _backfill() -> None:
    bind = op.get_bind()
    if "quote_feedback" not in _tables():
        return

    feedback_rows = bind.execute(
        sa.text(
            "SELECT id, quote_job_id, ai_payload_json, final_payload_json, amount_delta, status, username, "
            "rejection_reason FROM quote_feedback ORDER BY id ASC"
        )
    ).mappings().all()

    for feedback in feedback_rows:
        job = None
        if feedback["quote_job_id"]:
            job = bind.execute(
                sa.text("SELECT message, file_name FROM quote_jobs WHERE job_id = :job_id"),
                {"job_id": feedback["quote_job_id"]},
            ).mappings().first()
        ai_payload = _json_loads(feedback["ai_payload_json"])
        final_payload = _json_loads(feedback["final_payload_json"])
        display_payload = final_payload if final_payload is not None else ai_payload

        corrections = bind.execute(
            sa.text(
                "SELECT id, field_path, before_value, after_value FROM quote_corrections "
                "WHERE feedback_id = :feedback_id ORDER BY id ASC"
            ),
            {"feedback_id": feedback["id"]},
        ).mappings().all()
        correction_payloads = []
        for correction in corrections:
            label = _field_label(correction["field_path"])
            change_type = _change_type(correction["before_value"], correction["after_value"])
            correction_payloads.append({"field_label": label, "change_type": change_type})
            bind.execute(
                sa.text(
                    "UPDATE quote_corrections SET "
                    "field_label = COALESCE(field_label, :field_label), "
                    "change_type = COALESCE(change_type, :change_type), "
                    "before_display = COALESCE(before_display, :before_display), "
                    "after_display = COALESCE(after_display, :after_display) "
                    "WHERE id = :id"
                ),
                {
                    "id": correction["id"],
                    "field_label": label,
                    "change_type": change_type,
                    "before_display": _display(correction["before_value"]),
                    "after_display": _display(correction["after_value"]),
                },
            )

        change_summary, top_changed_fields = _change_summary(correction_payloads, feedback["amount_delta"])
        if feedback["status"] == "rejected":
            change_summary = f"Rejected: {feedback['rejection_reason']}" if feedback["rejection_reason"] else "Rejected by reviewer"

        bind.execute(
            sa.text(
                "UPDATE quote_feedback SET "
                "request_text = COALESCE(request_text, :request_text), "
                "source_file_name = COALESCE(source_file_name, :source_file_name), "
                "project_summary = COALESCE(project_summary, :project_summary), "
                "change_summary = COALESCE(change_summary, :change_summary), "
                "top_changed_fields = COALESCE(top_changed_fields, :top_changed_fields), "
                "reviewed_by = COALESCE(reviewed_by, :reviewed_by) "
                "WHERE id = :id"
            ),
            {
                "id": feedback["id"],
                "request_text": job["message"] if job else None,
                "source_file_name": job["file_name"] if job else None,
                "project_summary": _project_summary(display_payload),
                "change_summary": change_summary,
                "top_changed_fields": top_changed_fields,
                "reviewed_by": feedback["username"] if feedback["status"] in {"confirmed", "rejected"} else None,
            },
        )

        trace_rows = bind.execute(
            sa.text(
                "SELECT id, item_name, material_id, raw_json, project_name FROM quote_rag_traces "
                "WHERE feedback_id = :feedback_id ORDER BY id ASC"
            ),
            {"feedback_id": feedback["id"]},
        ).mappings().all()
        for trace in trace_rows:
            item_index, project_name, match_reason = _trace_context(dict(trace), ai_payload)
            trace_dict = dict(trace)
            trace_dict["project_name"] = project_name or trace_dict.get("project_name")
            used = _trace_used(trace_dict, final_payload) if final_payload is not None else None
            bind.execute(
                sa.text(
                    "UPDATE quote_rag_traces SET "
                    "item_index = COALESCE(item_index, :item_index), "
                    "project_name = COALESCE(project_name, :project_name), "
                    "used_in_final_quote = COALESCE(used_in_final_quote, :used_in_final_quote), "
                    "adopted_by_user = COALESCE(adopted_by_user, :used_in_final_quote), "
                    "match_reason = COALESCE(match_reason, :match_reason) "
                    "WHERE id = :id"
                ),
                {
                    "id": trace["id"],
                    "item_index": item_index,
                    "project_name": project_name,
                    "used_in_final_quote": used,
                    "match_reason": match_reason or ("appears in final quote" if used else None),
                },
            )


def upgrade() -> None:
    if "quote_feedback" not in _tables():
        return

    _add_column_if_missing("quote_feedback", sa.Column("request_text", _long_text(), nullable=True))
    _add_column_if_missing("quote_feedback", sa.Column("source_file_name", sa.String(length=255), nullable=True))
    _add_column_if_missing("quote_feedback", sa.Column("project_summary", sa.String(length=512), nullable=True))
    _add_column_if_missing("quote_feedback", sa.Column("change_summary", sa.String(length=512), nullable=True))
    _add_column_if_missing("quote_feedback", sa.Column("top_changed_fields", sa.Text(), nullable=True))
    _add_column_if_missing("quote_feedback", sa.Column("reviewed_by", sa.String(length=64), nullable=True))
    _create_index_if_missing("ix_quote_feedback_reviewed_by", "quote_feedback", ["reviewed_by"])

    _add_column_if_missing("quote_corrections", sa.Column("field_label", sa.String(length=64), nullable=True))
    _add_column_if_missing("quote_corrections", sa.Column("change_type", sa.String(length=32), nullable=True))
    _add_column_if_missing("quote_corrections", sa.Column("before_display", sa.String(length=255), nullable=True))
    _add_column_if_missing("quote_corrections", sa.Column("after_display", sa.String(length=255), nullable=True))
    _create_index_if_missing("ix_quote_corrections_change_type", "quote_corrections", ["change_type"])

    _add_column_if_missing("quote_rag_traces", sa.Column("item_index", sa.Integer(), nullable=True))
    _add_column_if_missing("quote_rag_traces", sa.Column("project_name", sa.String(length=255), nullable=True))
    _add_column_if_missing("quote_rag_traces", sa.Column("used_in_final_quote", sa.Boolean(), nullable=True))
    _add_column_if_missing("quote_rag_traces", sa.Column("match_reason", sa.String(length=255), nullable=True))
    _backfill()


def downgrade() -> None:
    for table_name, columns in [
        (
            "quote_rag_traces",
            ["match_reason", "used_in_final_quote", "project_name", "item_index"],
        ),
        (
            "quote_corrections",
            ["after_display", "before_display", "change_type", "field_label"],
        ),
        (
            "quote_feedback",
            ["reviewed_by", "top_changed_fields", "change_summary", "project_summary", "source_file_name", "request_text"],
        ),
    ]:
        existing_columns = _columns(table_name)
        for column in columns:
            if column in existing_columns:
                op.drop_column(table_name, column)
