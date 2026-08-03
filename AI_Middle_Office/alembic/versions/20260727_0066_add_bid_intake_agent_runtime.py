"""add persistent bid intake agent runtime

Revision ID: 20260727_0066
Revises: 20260727_0065
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260727_0066"
down_revision: Union[str, None] = "20260727_0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _long_blob():
    return sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql")


def upgrade() -> None:
    tables = _tables()
    if "bid_intake_assessments" not in tables:
        op.create_table(
            "bid_intake_assessments",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("assessment_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "manifest_id",
                sa.Integer(),
                sa.ForeignKey("bid_evidence_manifests.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("manifest_version", sa.Integer(), nullable=False),
            sa.Column("manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("analysis_goal", _long_text(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="queued",
            ),
            sa.Column(
                "report_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("latest_run_uuid", sa.String(length=36), nullable=True),
            sa.Column("recommendation", sa.String(length=40), nullable=True),
            sa.Column("gate_status", sa.String(length=40), nullable=True),
            sa.Column("assessment_json", _long_text(), nullable=True),
            sa.Column("policy_evaluation_json", _long_text(), nullable=True),
            sa.Column("gate_result_json", _long_text(), nullable=True),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "assessment_uuid",
                name="uq_bid_intake_assessments_uuid",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_intake_assessments_id", ["id"], False),
            (
                "ix_bid_intake_assessments_assessment_uuid",
                ["assessment_uuid"],
                True,
            ),
            ("ix_bid_intake_assessments_project_id", ["project_id"], False),
            ("ix_bid_intake_assessments_manifest_id", ["manifest_id"], False),
            (
                "ix_bid_intake_assessments_manifest_version",
                ["manifest_version"],
                False,
            ),
            (
                "ix_bid_intake_assessments_manifest_hash",
                ["manifest_hash"],
                False,
            ),
            ("ix_bid_intake_assessments_status", ["status"], False),
            (
                "ix_bid_intake_assessments_latest_run_uuid",
                ["latest_run_uuid"],
                False,
            ),
            (
                "ix_bid_intake_assessments_recommendation",
                ["recommendation"],
                False,
            ),
            (
                "ix_bid_intake_assessments_gate_status",
                ["gate_status"],
                False,
            ),
            ("ix_bid_intake_assessments_created_by", ["created_by"], False),
            (
                "ix_bid_intake_assessments_project_status_created",
                ["project_id", "status", "created_at"],
                False,
            ),
        ):
            op.create_index(
                name,
                "bid_intake_assessments",
                columns,
                unique=unique,
            )

    tables = _tables()
    if "bid_intake_agent_runs" not in tables:
        op.create_table(
            "bid_intake_agent_runs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("run_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "assessment_id",
                sa.Integer(),
                sa.ForeignKey("bid_intake_assessments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("thread_id", sa.String(length=160), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="queued",
            ),
            sa.Column(
                "phase",
                sa.String(length=64),
                nullable=False,
                server_default="queued",
            ),
            sa.Column(
                "trigger_source",
                sa.String(length=32),
                nullable=False,
                server_default="manual",
            ),
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "max_attempts",
                sa.Integer(),
                nullable=False,
                server_default="3",
            ),
            sa.Column("checkpoint_id", sa.String(length=64), nullable=True),
            sa.Column("state_summary_json", _long_text(), nullable=True),
            sa.Column("versions_json", _long_text(), nullable=True),
            sa.Column("worker_id", sa.String(length=160), nullable=True),
            sa.Column("lease_token", sa.String(length=64), nullable=True),
            sa.Column(
                "lease_expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "run_uuid",
                name="uq_bid_intake_agent_runs_uuid",
            ),
            sa.UniqueConstraint(
                "thread_id",
                name="uq_bid_intake_agent_runs_thread",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_intake_agent_runs_id", ["id"], False),
            ("ix_bid_intake_agent_runs_run_uuid", ["run_uuid"], True),
            (
                "ix_bid_intake_agent_runs_assessment_id",
                ["assessment_id"],
                False,
            ),
            ("ix_bid_intake_agent_runs_project_id", ["project_id"], False),
            ("ix_bid_intake_agent_runs_thread_id", ["thread_id"], True),
            ("ix_bid_intake_agent_runs_status", ["status"], False),
            ("ix_bid_intake_agent_runs_phase", ["phase"], False),
            (
                "ix_bid_intake_agent_runs_trigger_source",
                ["trigger_source"],
                False,
            ),
            (
                "ix_bid_intake_agent_runs_checkpoint_id",
                ["checkpoint_id"],
                False,
            ),
            ("ix_bid_intake_agent_runs_worker_id", ["worker_id"], False),
            ("ix_bid_intake_agent_runs_lease_token", ["lease_token"], False),
            (
                "ix_bid_intake_agent_runs_lease_expires_at",
                ["lease_expires_at"],
                False,
            ),
            ("ix_bid_intake_agent_runs_error_code", ["error_code"], False),
            ("ix_bid_intake_agent_runs_created_by", ["created_by"], False),
            (
                "ix_bid_intake_agent_runs_assessment_created",
                ["assessment_id", "created_at"],
                False,
            ),
            (
                "ix_bid_intake_agent_runs_status_lease",
                ["status", "lease_expires_at"],
                False,
            ),
            (
                "ix_bid_intake_agent_runs_project_status_created",
                ["project_id", "status", "created_at"],
                False,
            ),
        ):
            op.create_index(name, "bid_intake_agent_runs", columns, unique=unique)

    tables = _tables()
    if "bid_intake_human_decisions" not in tables:
        op.create_table(
            "bid_intake_human_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("decision_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "assessment_id",
                sa.Integer(),
                sa.ForeignKey("bid_intake_assessments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("bid_intake_agent_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("action", sa.String(length=40), nullable=False),
            sa.Column("report_version", sa.Integer(), nullable=False),
            sa.Column("manifest_version", sa.Integer(), nullable=False),
            sa.Column(
                "decided_by",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("decided_by_name", sa.String(length=160), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("conditions_json", _long_text(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=24),
                nullable=False,
                server_default="queued",
            ),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "decision_uuid",
                name="uq_bid_intake_human_decisions_uuid",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_intake_human_decisions_id", ["id"], False),
            (
                "ix_bid_intake_human_decisions_decision_uuid",
                ["decision_uuid"],
                True,
            ),
            (
                "ix_bid_intake_human_decisions_assessment_id",
                ["assessment_id"],
                False,
            ),
            ("ix_bid_intake_human_decisions_run_id", ["run_id"], False),
            ("ix_bid_intake_human_decisions_action", ["action"], False),
            (
                "ix_bid_intake_human_decisions_decided_by",
                ["decided_by"],
                False,
            ),
            ("ix_bid_intake_human_decisions_status", ["status"], False),
            (
                "ix_bid_intake_human_decisions_run_status_created",
                ["run_id", "status", "created_at"],
                False,
            ),
        ):
            op.create_index(
                name,
                "bid_intake_human_decisions",
                columns,
                unique=unique,
            )

    tables = _tables()
    if "bid_intake_run_events" not in tables:
        op.create_table(
            "bid_intake_run_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("event_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "assessment_id",
                sa.Integer(),
                sa.ForeignKey("bid_intake_assessments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("bid_intake_agent_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("phase", sa.String(length=64), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("payload_json", _long_text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "event_uuid",
                name="uq_bid_intake_run_events_uuid",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_intake_run_events_id", ["id"], False),
            ("ix_bid_intake_run_events_event_uuid", ["event_uuid"], True),
            ("ix_bid_intake_run_events_assessment_id", ["assessment_id"], False),
            ("ix_bid_intake_run_events_run_id", ["run_id"], False),
            ("ix_bid_intake_run_events_event_type", ["event_type"], False),
            ("ix_bid_intake_run_events_status", ["status"], False),
            ("ix_bid_intake_run_events_phase", ["phase"], False),
            (
                "ix_bid_intake_run_events_run_created",
                ["run_id", "created_at"],
                False,
            ),
        ):
            op.create_index(name, "bid_intake_run_events", columns, unique=unique)

    tables = _tables()
    if "bid_intake_checkpoints" not in tables:
        op.create_table(
            "bid_intake_checkpoints",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("thread_id", sa.String(length=160), nullable=False),
            sa.Column(
                "checkpoint_ns",
                sa.String(length=128),
                nullable=False,
                server_default="",
            ),
            sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
            sa.Column("parent_checkpoint_id", sa.String(length=64), nullable=True),
            sa.Column("checkpoint_type", sa.String(length=64), nullable=False),
            sa.Column("checkpoint_blob", _long_blob(), nullable=False),
            sa.Column("metadata_type", sa.String(length=64), nullable=False),
            sa.Column("metadata_blob", _long_blob(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "thread_id",
                "checkpoint_ns",
                "checkpoint_id",
                name="uq_bid_intake_checkpoints_identity",
            ),
        )
        op.create_index(
            "ix_bid_intake_checkpoints_thread_id",
            "bid_intake_checkpoints",
            ["thread_id"],
        )
        op.create_index(
            "ix_bid_intake_checkpoints_thread_ns_created",
            "bid_intake_checkpoints",
            ["thread_id", "checkpoint_ns", "created_at"],
        )

    tables = _tables()
    if "bid_intake_checkpoint_blobs" not in tables:
        op.create_table(
            "bid_intake_checkpoint_blobs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("thread_id", sa.String(length=160), nullable=False),
            sa.Column(
                "checkpoint_ns",
                sa.String(length=128),
                nullable=False,
                server_default="",
            ),
            sa.Column("channel", sa.String(length=128), nullable=False),
            sa.Column("version", sa.String(length=128), nullable=False),
            sa.Column("value_type", sa.String(length=64), nullable=False),
            sa.Column("value_blob", _long_blob(), nullable=False),
            sa.UniqueConstraint(
                "thread_id",
                "checkpoint_ns",
                "channel",
                "version",
                name="uq_bid_intake_checkpoint_blobs_identity",
            ),
        )
        op.create_index(
            "ix_bid_intake_checkpoint_blobs_thread_id",
            "bid_intake_checkpoint_blobs",
            ["thread_id"],
        )

    tables = _tables()
    if "bid_intake_checkpoint_writes" not in tables:
        op.create_table(
            "bid_intake_checkpoint_writes",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("thread_id", sa.String(length=160), nullable=False),
            sa.Column(
                "checkpoint_ns",
                sa.String(length=128),
                nullable=False,
                server_default="",
            ),
            sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
            sa.Column("task_id", sa.String(length=160), nullable=False),
            sa.Column(
                "task_path",
                sa.String(length=500),
                nullable=False,
                server_default="",
            ),
            sa.Column("write_index", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=128), nullable=False),
            sa.Column("value_type", sa.String(length=64), nullable=False),
            sa.Column("value_blob", _long_blob(), nullable=False),
            sa.UniqueConstraint(
                "thread_id",
                "checkpoint_ns",
                "checkpoint_id",
                "task_id",
                "write_index",
                name="uq_bid_intake_checkpoint_writes_identity",
            ),
        )
        op.create_index(
            "ix_bid_intake_checkpoint_writes_thread_id",
            "bid_intake_checkpoint_writes",
            ["thread_id"],
        )
        op.create_index(
            "ix_bid_intake_checkpoint_writes_checkpoint",
            "bid_intake_checkpoint_writes",
            ["thread_id", "checkpoint_ns", "checkpoint_id"],
        )


def downgrade() -> None:
    tables = _tables()
    for table in (
        "bid_intake_checkpoint_writes",
        "bid_intake_checkpoint_blobs",
        "bid_intake_checkpoints",
        "bid_intake_run_events",
        "bid_intake_human_decisions",
        "bid_intake_agent_runs",
        "bid_intake_assessments",
    ):
        if table in tables:
            op.drop_table(table)
