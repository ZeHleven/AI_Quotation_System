"""add agent assistant tables

Revision ID: 20260605_0029
Revises: 20260601_0028
Create Date: 2026-06-05
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260605_0029"
down_revision: Union[str, None] = "20260601_0028"
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
    if "agent_runs" not in tables:
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("agent_type", sa.String(length=64), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=False),
            sa.Column("target_id", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
            sa.Column("risk_level", sa.String(length=24), nullable=True),
            sa.Column("recommendation", sa.String(length=64), nullable=True),
            sa.Column("summary", _long_text(), nullable=True),
            sa.Column("output_json", _long_text(), nullable=True),
            sa.Column("created_by", sa.String(length=64), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error_message", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", name="uq_agent_runs_run_id"),
        )
    if "agent_tool_calls" not in tables:
        op.create_table(
            "agent_tool_calls",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("input_json", _long_text(), nullable=True),
            sa.Column("output_summary", _long_text(), nullable=True),
            sa.Column("output_json", _long_text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="success"),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if "agent_findings" not in tables:
        op.create_table(
            "agent_findings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("finding_type", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=24), nullable=False),
            sa.Column("target_ref", sa.String(length=255), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("evidence_json", _long_text(), nullable=True),
            sa.Column("suggestion", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    _create_index_if_missing("agent_runs", "ix_agent_runs_run_id", ["run_id"], unique=True)
    _create_index_if_missing("agent_runs", "ix_agent_runs_agent_type", ["agent_type"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_target_type", ["target_type"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_target_id", ["target_id"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_status", ["status"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_risk_level", ["risk_level"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_recommendation", ["recommendation"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_created_by", ["created_by"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_trace_id", ["trace_id"])
    _create_index_if_missing("agent_tool_calls", "ix_agent_tool_calls_run_id", ["run_id"])
    _create_index_if_missing("agent_tool_calls", "ix_agent_tool_calls_tool_name", ["tool_name"])
    _create_index_if_missing("agent_tool_calls", "ix_agent_tool_calls_status", ["status"])
    _create_index_if_missing("agent_findings", "ix_agent_findings_run_id", ["run_id"])
    _create_index_if_missing("agent_findings", "ix_agent_findings_finding_type", ["finding_type"])
    _create_index_if_missing("agent_findings", "ix_agent_findings_severity", ["severity"])
    _create_index_if_missing("agent_findings", "ix_agent_findings_target_ref", ["target_ref"])


def downgrade() -> None:
    for table_name, index_name in (
        ("agent_findings", "ix_agent_findings_target_ref"),
        ("agent_findings", "ix_agent_findings_severity"),
        ("agent_findings", "ix_agent_findings_finding_type"),
        ("agent_findings", "ix_agent_findings_run_id"),
        ("agent_tool_calls", "ix_agent_tool_calls_status"),
        ("agent_tool_calls", "ix_agent_tool_calls_tool_name"),
        ("agent_tool_calls", "ix_agent_tool_calls_run_id"),
        ("agent_runs", "ix_agent_runs_trace_id"),
        ("agent_runs", "ix_agent_runs_created_by"),
        ("agent_runs", "ix_agent_runs_recommendation"),
        ("agent_runs", "ix_agent_runs_risk_level"),
        ("agent_runs", "ix_agent_runs_status"),
        ("agent_runs", "ix_agent_runs_target_id"),
        ("agent_runs", "ix_agent_runs_target_type"),
        ("agent_runs", "ix_agent_runs_agent_type"),
        ("agent_runs", "ix_agent_runs_run_id"),
    ):
        if table_name in _tables() and index_name in _indexes(table_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name in ("agent_findings", "agent_tool_calls", "agent_runs"):
        if table_name in _tables():
            op.drop_table(table_name)
