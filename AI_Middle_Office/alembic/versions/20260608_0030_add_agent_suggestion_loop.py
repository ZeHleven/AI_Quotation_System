"""add agent suggestion loop tables

Revision ID: 20260608_0030
Revises: 20260605_0029
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260608_0030"
down_revision: Union[str, None] = "20260605_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _long_text() -> sa.Text:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], *, unique: bool = False) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    tables = _tables()
    if "agent_suggestions" not in tables:
        op.create_table(
            "agent_suggestions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("suggestion_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("agent_type", sa.String(length=64), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=False),
            sa.Column("target_id", sa.String(length=128), nullable=False),
            sa.Column("suggestion_type", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_review"),
            sa.Column("priority", sa.String(length=24), nullable=False, server_default="medium"),
            sa.Column("target_ref", sa.String(length=255), nullable=True),
            sa.Column("target_line_no", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("rationale", _long_text(), nullable=True),
            sa.Column("risk_note", _long_text(), nullable=True),
            sa.Column("current_snapshot_json", _long_text(), nullable=True),
            sa.Column("proposed_snapshot_json", _long_text(), nullable=True),
            sa.Column("execution_result_json", _long_text(), nullable=True),
            sa.Column("final_result_json", _long_text(), nullable=True),
            sa.Column("estimated_saving_amount", sa.Float(), nullable=True),
            sa.Column("estimated_saving_rate", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.String(length=64), nullable=False),
            sa.Column("decided_by", sa.String(length=64), nullable=True),
            sa.Column("decision_note", _long_text(), nullable=True),
            sa.Column("executed_by", sa.String(length=64), nullable=True),
            sa.Column("final_confirmed_by", sa.String(length=64), nullable=True),
            sa.Column("final_note", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("final_confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("suggestion_id", name="uq_agent_suggestions_suggestion_id"),
        )

    if "agent_suggestion_events" not in tables:
        op.create_table(
            "agent_suggestion_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.String(length=36), nullable=False),
            sa.Column("suggestion_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("actor", sa.String(length=64), nullable=True),
            sa.Column("note", _long_text(), nullable=True),
            sa.Column("payload_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["suggestion_id"], ["agent_suggestions.suggestion_id"]),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", name="uq_agent_suggestion_events_event_id"),
        )

    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_suggestion_id", ["suggestion_id"], unique=True)
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_run_id", ["run_id"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_agent_type", ["agent_type"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_target_type", ["target_type"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_target_id", ["target_id"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_suggestion_type", ["suggestion_type"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_status", ["status"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_priority", ["priority"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_target_ref", ["target_ref"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_target_line_no", ["target_line_no"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_created_by", ["created_by"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_decided_by", ["decided_by"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_executed_by", ["executed_by"])
    _create_index_if_missing("agent_suggestions", "ix_agent_suggestions_final_confirmed_by", ["final_confirmed_by"])
    _create_index_if_missing("agent_suggestion_events", "ix_agent_suggestion_events_event_id", ["event_id"], unique=True)
    _create_index_if_missing("agent_suggestion_events", "ix_agent_suggestion_events_suggestion_id", ["suggestion_id"])
    _create_index_if_missing("agent_suggestion_events", "ix_agent_suggestion_events_run_id", ["run_id"])
    _create_index_if_missing("agent_suggestion_events", "ix_agent_suggestion_events_event_type", ["event_type"])
    _create_index_if_missing("agent_suggestion_events", "ix_agent_suggestion_events_actor", ["actor"])


def downgrade() -> None:
    for table_name, index_name in (
        ("agent_suggestion_events", "ix_agent_suggestion_events_actor"),
        ("agent_suggestion_events", "ix_agent_suggestion_events_event_type"),
        ("agent_suggestion_events", "ix_agent_suggestion_events_run_id"),
        ("agent_suggestion_events", "ix_agent_suggestion_events_suggestion_id"),
        ("agent_suggestion_events", "ix_agent_suggestion_events_event_id"),
        ("agent_suggestions", "ix_agent_suggestions_final_confirmed_by"),
        ("agent_suggestions", "ix_agent_suggestions_executed_by"),
        ("agent_suggestions", "ix_agent_suggestions_decided_by"),
        ("agent_suggestions", "ix_agent_suggestions_created_by"),
        ("agent_suggestions", "ix_agent_suggestions_target_line_no"),
        ("agent_suggestions", "ix_agent_suggestions_target_ref"),
        ("agent_suggestions", "ix_agent_suggestions_priority"),
        ("agent_suggestions", "ix_agent_suggestions_status"),
        ("agent_suggestions", "ix_agent_suggestions_suggestion_type"),
        ("agent_suggestions", "ix_agent_suggestions_target_id"),
        ("agent_suggestions", "ix_agent_suggestions_target_type"),
        ("agent_suggestions", "ix_agent_suggestions_agent_type"),
        ("agent_suggestions", "ix_agent_suggestions_run_id"),
        ("agent_suggestions", "ix_agent_suggestions_suggestion_id"),
    ):
        if table_name in _tables() and index_name in _indexes(table_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name in ("agent_suggestion_events", "agent_suggestions"):
        if table_name in _tables():
            op.drop_table(table_name)
