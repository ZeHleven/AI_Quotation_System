"""add phase4a task drafts and meeting revisions

Revision ID: 20260514_0015
Revises: 20260514_0014
Create Date: 2026-05-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0015"
down_revision: Union[str, None] = "20260514_0014"
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
    if "meeting_note_revisions" not in _tables():
        op.create_table(
            "meeting_note_revisions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("meeting_note_id", sa.Integer(), sa.ForeignKey("meeting_notes.id"), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("previous_content_sha256", sa.String(length=64), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("task_ids_json", sa.Text(), nullable=True),
        )
    _create_index_if_missing("ix_meeting_note_revisions_id", "meeting_note_revisions", ["id"])
    _create_index_if_missing("ix_meeting_note_revisions_meeting_note_id", "meeting_note_revisions", ["meeting_note_id"])
    _create_index_if_missing("ix_meeting_note_revisions_created_by", "meeting_note_revisions", ["created_by"])
    _create_index_if_missing("ix_meeting_note_revisions_trace_id", "meeting_note_revisions", ["trace_id"])

    if "task_drafts" not in _tables():
        op.create_table(
            "task_drafts",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
            sa.Column("meeting_note_id", sa.Integer(), sa.ForeignKey("meeting_notes.id"), nullable=False),
            sa.Column("revision_id", sa.Integer(), sa.ForeignKey("meeting_note_revisions.id"), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("normalized_title", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_review"),
            sa.Column("source_sentence", sa.Text(), nullable=False),
            sa.Column("suggested_assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("suggested_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("confirmed_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_task_id", sa.Integer(), sa.ForeignKey("execution_tasks.id"), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("prompt_version", sa.String(length=64), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
        )
    _create_index_if_missing("ix_task_drafts_id", "task_drafts", ["id"])
    _create_index_if_missing("ix_task_drafts_meeting_note_id", "task_drafts", ["meeting_note_id"])
    _create_index_if_missing("ix_task_drafts_revision_id", "task_drafts", ["revision_id"])
    _create_index_if_missing("ix_task_drafts_normalized_title", "task_drafts", ["normalized_title"])
    _create_index_if_missing("ix_task_drafts_status", "task_drafts", ["status"])
    _create_index_if_missing("ix_task_drafts_suggested_assignee_id", "task_drafts", ["suggested_assignee_id"])
    _create_index_if_missing("ix_task_drafts_confirmed_assignee_id", "task_drafts", ["confirmed_assignee_id"])
    _create_index_if_missing("ix_task_drafts_accepted_task_id", "task_drafts", ["accepted_task_id"])
    _create_index_if_missing("ix_task_drafts_trace_id", "task_drafts", ["trace_id"])


def downgrade() -> None:
    if "task_drafts" in _tables():
        op.drop_table("task_drafts")
    if "meeting_note_revisions" in _tables():
        op.drop_table("meeting_note_revisions")
