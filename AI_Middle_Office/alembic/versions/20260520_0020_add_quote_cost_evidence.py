"""add quote cost evidence

Revision ID: 20260520_0020
Revises: 20260520_0019
Create Date: 2026-05-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260520_0020"
down_revision: Union[str, None] = "20260520_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT, "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    if "quote_cost_evidence" not in _tables():
        op.create_table(
            "quote_cost_evidence",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("feedback_id", sa.Integer(), nullable=False),
            sa.Column("quote_id", sa.String(length=36), nullable=False),
            sa.Column("quote_job_id", sa.String(length=36), nullable=True),
            sa.Column("quote_history_id", sa.Integer(), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=32), server_default="preview", nullable=False),
            sa.Column("status", sa.String(length=32), server_default="pending_review", nullable=False),
            sa.Column("item_index", sa.Integer(), nullable=False),
            sa.Column("project_name", sa.String(length=255), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("ai_unit_price", sa.Float(), nullable=True),
            sa.Column("ai_total_price", sa.Float(), nullable=True),
            sa.Column("final_unit_price", sa.Float(), nullable=True),
            sa.Column("final_total_price", sa.Float(), nullable=True),
            sa.Column("manual_modified", sa.Boolean(), server_default=sa.text("0"), nullable=False),
            sa.Column("adopted_cost_reference", sa.Boolean(), nullable=True),
            sa.Column("cost_item_id", sa.Integer(), nullable=True),
            sa.Column("cost_item_name_snapshot", sa.String(length=255), nullable=True),
            sa.Column("cost_item_category_snapshot", sa.String(length=128), nullable=True),
            sa.Column("cost_item_subcategory_snapshot", sa.String(length=128), nullable=True),
            sa.Column("cost_item_unit_snapshot", sa.String(length=64), nullable=True),
            sa.Column("cost_item_status_snapshot", sa.String(length=24), nullable=True),
            sa.Column("reference_price", sa.Float(), nullable=True),
            sa.Column("reference_total", sa.Float(), nullable=True),
            sa.Column("reference_price_source", sa.String(length=64), nullable=True),
            sa.Column("reference_price_source_label", sa.String(length=64), nullable=True),
            sa.Column("match_type", sa.String(length=64), nullable=True),
            sa.Column("match_type_label", sa.String(length=64), nullable=True),
            sa.Column("match_reason", sa.Text(), nullable=True),
            sa.Column("price_delta", sa.Float(), nullable=True),
            sa.Column("price_delta_rate", sa.Float(), nullable=True),
            sa.Column("fallback_applied", sa.Boolean(), server_default=sa.text("0"), nullable=False),
            sa.Column("ai_basis", sa.Text(), nullable=True),
            sa.Column("cost_context_basis", sa.Text(), nullable=True),
            sa.Column("comparison", sa.Text(), nullable=True),
            sa.Column("cost_item_url", sa.String(length=255), nullable=True),
            sa.Column("cost_reference_json", _long_text(), nullable=True),
            sa.Column("quote_explanation_json", _long_text(), nullable=True),
            sa.Column("cost_item_snapshot_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["feedback_id"], ["quote_feedback.id"], name="fk_quote_cost_evidence_feedback_id"),
        )

    _create_index_if_missing("ix_quote_cost_evidence_id", "quote_cost_evidence", ["id"])
    _create_index_if_missing("ix_quote_cost_evidence_feedback_id", "quote_cost_evidence", ["feedback_id"])
    _create_index_if_missing("ix_quote_cost_evidence_quote_id", "quote_cost_evidence", ["quote_id"])
    _create_index_if_missing("ix_quote_cost_evidence_quote_job_id", "quote_cost_evidence", ["quote_job_id"])
    _create_index_if_missing("ix_quote_cost_evidence_quote_history_id", "quote_cost_evidence", ["quote_history_id"])
    _create_index_if_missing("ix_quote_cost_evidence_trace_id", "quote_cost_evidence", ["trace_id"])
    _create_index_if_missing("ix_quote_cost_evidence_username", "quote_cost_evidence", ["username"])
    _create_index_if_missing("ix_quote_cost_evidence_source", "quote_cost_evidence", ["source"])
    _create_index_if_missing("ix_quote_cost_evidence_status", "quote_cost_evidence", ["status"])
    _create_index_if_missing("ix_quote_cost_evidence_cost_item_id", "quote_cost_evidence", ["cost_item_id"])
    _create_index_if_missing(
        "ix_quote_cost_evidence_feedback_item",
        "quote_cost_evidence",
        ["feedback_id", "item_index"],
    )
    _create_index_if_missing(
        "ix_quote_cost_evidence_job_status",
        "quote_cost_evidence",
        ["quote_job_id", "status"],
    )


def downgrade() -> None:
    if "quote_cost_evidence" in _tables():
        op.drop_table("quote_cost_evidence")
