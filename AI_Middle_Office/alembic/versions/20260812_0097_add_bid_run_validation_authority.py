"""add Phase 3G run validation and convergence authority

Revision ID: 20260812_0097
Revises: 20260812_0096
Create Date: 2026-08-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260812_0097"
down_revision: Union[str, None] = "20260812_0096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}
PREVIOUS_OUTBOX_EVENT_TYPES = (
    "bid.assessment.created.v1", "bid.upload_batch.created.v1",
    "bid.upload_file.received.v1", "bid.upload_file.removed.v1",
    "bid.upload_batch.deactivation_added.v1", "bid.upload_batch.abandoned.v1",
    "bid.document.version_registered.v1", "bid.manifest.committed.v1",
    "bid.document.parse_requested.v1", "bid.document.parsed.v1",
    "bid.document.parse_failed.v1", "bid.manifest.parse_set_ready.v1",
    "bid.lot_detection.requested.v1", "bid.lots.detected.v1",
    "bid.lot_detection.failed.v1", "bid.lot.selected.v1",
    "bid.assessment.input_stale.v1", "bid.run.created.v1",
    "bid.plan.requested.v1", "bid.plan.committed.v1", "bid.task.ready.v1",
    "bid.task.leased.v1", "bid.task.waiting_operation.v1",
    "bid.task.waiting_input.v1", "bid.task.succeeded.v1",
    "bid.task.failed.v1", "bid.task.stale.v1",
    "bid.run.validation_requested.v1", "bid.run.cancel_requested.v1",
    "bid.run.cancelled.v1", "bid.run.retry_requested.v1",
    "bid.run.succeeded.v1", "bid.run.failed.v1", "bid.facts.changed.v1",
    "bid.calculation.completed.v1", "bid.gates.evaluated.v1",
    "bid.question.published.v1", "bid.question.answered.v1",
    "bid.dimensions.completed.v1", "bid.decision.completed.v1",
    "bid.owner_override.recorded.v1", "bid.report.requested.v1",
    "bid.report.validated.v1", "bid.report.published.v1",
    "bid.report.failed.v1", "bid.report.superseded.v1",
)
OUTBOX_EVENT_TYPES = (*PREVIOUS_OUTBOX_EVENT_TYPES, "bid.run.stale.v1")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_constraint("ck_bid_outbox_events_type", "bid_outbox_events", type_="check")
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(OUTBOX_EVENT_TYPES)})",
    )
    op.create_table(
        "bid_run_validations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("plan_revision_id", sa.String(length=36), nullable=True),
        sa.Column("source_event_id", sa.String(length=80), nullable=False),
        sa.Column("validation_key", sa.String(length=128), nullable=False),
        sa.Column("validator_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="requested", nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('requested', 'leased', 'running', 'passed', 'failed', "
            "'stale', 'cancelled')",
            name="ck_bid_run_validations_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_bid_run_validations_attempts"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_bid_run_validations_fencing"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_run_validations_row_version"),
        sa.CheckConstraint(
            "((status IN ('leased', 'running') AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND finished_at IS NULL) OR "
            "(status = 'requested' AND lease_owner IS NULL AND lease_until IS NULL "
            "AND finished_at IS NULL) OR "
            "(status IN ('passed', 'failed', 'stale', 'cancelled') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND finished_at IS NOT NULL))",
            name="ck_bid_run_validations_lifecycle",
        ),
        sa.CheckConstraint(
            "((status IN ('passed', 'failed', 'stale') AND outcome = status "
            "AND result_hash IS NOT NULL AND result_json IS NOT NULL) OR "
            "(status IN ('requested', 'leased', 'running', 'cancelled') "
            "AND outcome IS NULL))",
            name="ck_bid_run_validations_outcome",
        ),
        sa.CheckConstraint(
            "((status = 'passed' AND retryable = 0 AND failure_code IS NULL) OR "
            "(status = 'failed' AND failure_code IS NOT NULL) OR "
            "(status = 'stale' AND retryable = 0 AND failure_code IS NOT NULL) OR "
            "status IN ('requested', 'leased', 'running', 'cancelled'))",
            name="ck_bid_run_validations_failure",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_run_validations_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "plan_revision_id"],
            ["bid_plan_revisions.run_id", "bid_plan_revisions.id"],
            name="fk_bid_run_validations_plan",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_run_validations"),
        sa.UniqueConstraint("run_id", name="uq_bid_run_validations_run"),
        sa.UniqueConstraint("source_event_id", name="uq_bid_run_validations_source_event"),
        sa.UniqueConstraint("validation_key", name="uq_bid_run_validations_key"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_run_validations_run_id"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_run_validations_ready",
        "bid_run_validations",
        ["status", "lease_until", "requested_at"],
    )
    op.create_table(
        "bid_run_validation_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("validation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("execution_key", sa.String(length=128), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('leased', 'running', 'passed', 'failed', 'stale', "
            "'lease_expired', 'cancelled')",
            name="ck_bid_run_validation_attempts_status",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_bid_run_validation_attempts_number"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_run_validation_attempts_fencing"),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bid_run_validation_attempts_time_order",
        ),
        sa.CheckConstraint(
            "((status IN ('leased', 'running') AND finished_at IS NULL) OR "
            "(status IN ('passed', 'failed', 'stale', 'lease_expired', 'cancelled') "
            "AND finished_at IS NOT NULL))",
            name="ck_bid_run_validation_attempts_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "validation_id"],
            ["bid_run_validations.run_id", "bid_run_validations.id"],
            name="fk_bid_run_validation_attempts_validation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_run_validation_attempts"),
        sa.UniqueConstraint("validation_id", "attempt_no", name="uq_bid_run_validation_attempts_number"),
        sa.UniqueConstraint("validation_id", "fencing_token", name="uq_bid_run_validation_attempts_fencing"),
        sa.UniqueConstraint("execution_key", name="uq_bid_run_validation_attempts_execution_key"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_run_validation_attempts_lease",
        "bid_run_validation_attempts",
        ["status", "lease_until"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0097 guarded downgrade requires an online database connection; "
            "offline SQL would bypass Run validation lineage checks"
        )
    bind = op.get_bind()
    counts = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in ("bid_run_validation_attempts", "bid_run_validations")
    }
    if any(counts.values()):
        raise RuntimeError(
            "0097 downgrade would erase immutable Run validation/convergence lineage; "
            "export and explicitly remove Phase 3G rows first"
        )
    stale_event_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_outbox_events "
                "WHERE event_type = 'bid.run.stale.v1'"
            )
        ).scalar()
        or 0
    )
    if stale_event_count:
        raise RuntimeError(
            "0097 downgrade would invalidate persisted run-stale events; "
            "archive and explicitly remove those rows first"
        )
    op.drop_table("bid_run_validation_attempts")
    op.drop_table("bid_run_validations")
    op.drop_constraint("ck_bid_outbox_events_type", "bid_outbox_events", type_="check")
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(PREVIOUS_OUTBOX_EVENT_TYPES)})",
    )
