"""add knowledge candidates

Revision ID: 20260507_0006
Revises: 20260507_0005
Create Date: 2026-05-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260507_0006"
down_revision: Union[str, None] = "20260507_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


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
    existing_tables = _tables()

    if "knowledge_candidates" not in existing_tables:
        op.create_table(
            "knowledge_candidates",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("candidate_key", sa.String(length=160), nullable=False),
            sa.Column("source_type", sa.String(length=32), nullable=False),
            sa.Column("candidate_kind", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("source_feedback_id", sa.Integer(), sa.ForeignKey("quote_feedback.id"), nullable=True),
            sa.Column("source_correction_id", sa.Integer(), sa.ForeignKey("quote_corrections.id"), nullable=True),
            sa.Column("source_rag_trace_id", sa.Integer(), sa.ForeignKey("quote_rag_traces.id"), nullable=True),
            sa.Column("quote_id", sa.String(length=36), nullable=True),
            sa.Column("quote_job_id", sa.String(length=36), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("item_name", sa.String(length=255), nullable=True),
            sa.Column("unit_price", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("notes", _long_text(), nullable=True),
            sa.Column("existing_material_id", sa.String(length=64), nullable=True),
            sa.Column("suggested_material_id", sa.String(length=64), nullable=True),
            sa.Column("material_id", sa.String(length=64), nullable=True),
            sa.Column("confidence_score", sa.Float(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("evidence_json", _long_text(), nullable=True),
            sa.Column("created_by", sa.String(length=64), nullable=False, server_default="system"),
            sa.Column("reviewed_by", sa.String(length=64), nullable=True),
            sa.Column("review_note", sa.Text(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("is_draft_material", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        )
    _create_index_if_missing("ix_knowledge_candidates_id", "knowledge_candidates", ["id"])
    _create_index_if_missing(
        "ix_knowledge_candidates_candidate_key",
        "knowledge_candidates",
        ["candidate_key"],
        unique=True,
    )
    _create_index_if_missing("ix_knowledge_candidates_source_type", "knowledge_candidates", ["source_type"])
    _create_index_if_missing("ix_knowledge_candidates_candidate_kind", "knowledge_candidates", ["candidate_kind"])
    _create_index_if_missing("ix_knowledge_candidates_status", "knowledge_candidates", ["status"])
    _create_index_if_missing(
        "ix_knowledge_candidates_source_feedback_id",
        "knowledge_candidates",
        ["source_feedback_id"],
    )
    _create_index_if_missing(
        "ix_knowledge_candidates_source_correction_id",
        "knowledge_candidates",
        ["source_correction_id"],
    )
    _create_index_if_missing(
        "ix_knowledge_candidates_source_rag_trace_id",
        "knowledge_candidates",
        ["source_rag_trace_id"],
    )
    _create_index_if_missing("ix_knowledge_candidates_quote_id", "knowledge_candidates", ["quote_id"])
    _create_index_if_missing("ix_knowledge_candidates_quote_job_id", "knowledge_candidates", ["quote_job_id"])
    _create_index_if_missing("ix_knowledge_candidates_trace_id", "knowledge_candidates", ["trace_id"])
    _create_index_if_missing("ix_knowledge_candidates_username", "knowledge_candidates", ["username"])
    _create_index_if_missing("ix_knowledge_candidates_item_name", "knowledge_candidates", ["item_name"])
    _create_index_if_missing(
        "ix_knowledge_candidates_existing_material_id",
        "knowledge_candidates",
        ["existing_material_id"],
    )
    _create_index_if_missing("ix_knowledge_candidates_material_id", "knowledge_candidates", ["material_id"])


def downgrade() -> None:
    op.drop_table("knowledge_candidates")
