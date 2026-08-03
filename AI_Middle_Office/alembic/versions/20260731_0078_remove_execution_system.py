"""remove retired execution tasks and meeting notes

Revision ID: 20260731_0078
Revises: 20260731_0077
Create Date: 2026-07-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_0078"
down_revision: Union[str, None] = "20260731_0077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=False)


def upgrade() -> None:
    """Drop only the five tables owned exclusively by the retired feature."""

    for table_name in (
        "task_drafts",
        "meeting_note_revisions",
        "meeting_notes",
        "execution_task_events",
        "execution_tasks",
    ):
        if table_name in _tables():
            op.drop_table(table_name)


def downgrade() -> None:
    """Restore the retired schema for migration rollback; deleted rows are not restored."""

    if "execution_tasks" not in _tables():
        op.create_table(
            "execution_tasks",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("source", sa.String(length=24), nullable=False, server_default="manual"),
            sa.Column("source_ref_id", sa.String(length=64), nullable=True),
            sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("notes", sa.Text(), nullable=True),
        )
    for name, columns in (
        ("ix_execution_tasks_id", ["id"]),
        ("ix_execution_tasks_source", ["source"]),
        ("ix_execution_tasks_source_ref_id", ["source_ref_id"]),
        ("ix_execution_tasks_assignee_id", ["assignee_id"]),
        ("ix_execution_tasks_due_at", ["due_at"]),
        ("ix_execution_tasks_completed_at", ["completed_at"]),
        ("ix_execution_tasks_status", ["status"]),
    ):
        _create_index_if_missing(name, "execution_tasks", columns)

    if "execution_task_events" not in _tables():
        op.create_table(
            "execution_task_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("execution_task_id", sa.Integer(), sa.ForeignKey("execution_tasks.id"), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("from_status", sa.String(length=24), nullable=True),
            sa.Column("to_status", sa.String(length=24), nullable=True),
            sa.Column("operator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
        )
    for name, columns in (
        ("ix_execution_task_events_id", ["id"]),
        ("ix_execution_task_events_execution_task_id", ["execution_task_id"]),
        ("ix_execution_task_events_event_type", ["event_type"]),
        ("ix_execution_task_events_operator_id", ["operator_id"]),
        ("ix_execution_task_events_trace_id", ["trace_id"]),
    ):
        _create_index_if_missing(name, "execution_task_events", columns)

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
    for name, columns in (
        ("ix_meeting_notes_id", ["id"]),
        ("ix_meeting_notes_status", ["status"]),
        ("ix_meeting_notes_created_by", ["created_by"]),
        ("ix_meeting_notes_confirmed_by", ["confirmed_by"]),
        ("ix_meeting_notes_cancelled_by", ["cancelled_by"]),
        ("ix_meeting_notes_ai_status", ["ai_status"]),
        ("ix_meeting_notes_trace_id", ["trace_id"]),
    ):
        _create_index_if_missing(name, "meeting_notes", columns)

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
    for name, columns in (
        ("ix_meeting_note_revisions_id", ["id"]),
        ("ix_meeting_note_revisions_meeting_note_id", ["meeting_note_id"]),
        ("ix_meeting_note_revisions_created_by", ["created_by"]),
        ("ix_meeting_note_revisions_trace_id", ["trace_id"]),
    ):
        _create_index_if_missing(name, "meeting_note_revisions", columns)

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
    for name, columns in (
        ("ix_task_drafts_id", ["id"]),
        ("ix_task_drafts_meeting_note_id", ["meeting_note_id"]),
        ("ix_task_drafts_revision_id", ["revision_id"]),
        ("ix_task_drafts_normalized_title", ["normalized_title"]),
        ("ix_task_drafts_status", ["status"]),
        ("ix_task_drafts_suggested_assignee_id", ["suggested_assignee_id"]),
        ("ix_task_drafts_confirmed_assignee_id", ["confirmed_assignee_id"]),
        ("ix_task_drafts_accepted_task_id", ["accepted_task_id"]),
        ("ix_task_drafts_trace_id", ["trace_id"]),
    ):
        _create_index_if_missing(name, "task_drafts", columns)
