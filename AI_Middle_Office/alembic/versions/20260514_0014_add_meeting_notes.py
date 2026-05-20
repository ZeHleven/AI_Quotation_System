"""add phase4a meeting notes

Revision ID: 20260514_0014
Revises: 20260514_0013
Create Date: 2026-05-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0014"
down_revision: Union[str, None] = "20260514_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    if "meeting_notes" not in _tables():
        op.create_table(
            "meeting_notes",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("cancel_reason", sa.Text(), nullable=True),
            sa.Column("ai_status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("prompt_version", sa.String(length=64), nullable=True),
            sa.Column("extraction_error", sa.Text(), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
        )
    _create_index_if_missing("ix_meeting_notes_id", "meeting_notes", ["id"])
    _create_index_if_missing("ix_meeting_notes_status", "meeting_notes", ["status"])
    _create_index_if_missing("ix_meeting_notes_created_by", "meeting_notes", ["created_by"])
    _create_index_if_missing("ix_meeting_notes_confirmed_by", "meeting_notes", ["confirmed_by"])
    _create_index_if_missing("ix_meeting_notes_cancelled_by", "meeting_notes", ["cancelled_by"])
    _create_index_if_missing("ix_meeting_notes_ai_status", "meeting_notes", ["ai_status"])
    _create_index_if_missing("ix_meeting_notes_trace_id", "meeting_notes", ["trace_id"])


def downgrade() -> None:
    if "meeting_notes" in _tables():
        op.drop_table("meeting_notes")
