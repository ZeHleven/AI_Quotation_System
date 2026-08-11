"""add API-16 upload-batch abandonment and cleanup schedule

Revision ID: 20260811_0091
Revises: 20260811_0090
Create Date: 2026-08-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "20260811_0091"
down_revision: Union[str, None] = "20260811_0090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_EVENT_TYPES = (
    "bid.assessment.created.v1",
    "bid.upload_batch.created.v1",
    "bid.upload_file.received.v1",
    "bid.upload_file.removed.v1",
    "bid.upload_batch.deactivation_added.v1",
    "bid.upload_batch.abandoned.v1",
    "bid.document.version_registered.v1",
    "bid.manifest.committed.v1",
    "bid.document.parse_requested.v1",
    "bid.document.parsed.v1",
    "bid.document.parse_failed.v1",
    "bid.lots.detected.v1",
    "bid.lot.selected.v1",
    "bid.assessment.input_stale.v1",
    "bid.run.created.v1",
    "bid.plan.requested.v1",
    "bid.plan.committed.v1",
    "bid.task.ready.v1",
    "bid.task.leased.v1",
    "bid.task.waiting_operation.v1",
    "bid.task.waiting_input.v1",
    "bid.task.succeeded.v1",
    "bid.task.failed.v1",
    "bid.task.stale.v1",
    "bid.run.validation_requested.v1",
    "bid.run.cancel_requested.v1",
    "bid.run.cancelled.v1",
    "bid.run.succeeded.v1",
    "bid.run.failed.v1",
    "bid.facts.changed.v1",
    "bid.calculation.completed.v1",
    "bid.gates.evaluated.v1",
    "bid.question.published.v1",
    "bid.question.answered.v1",
    "bid.dimensions.completed.v1",
    "bid.decision.completed.v1",
    "bid.owner_override.recorded.v1",
    "bid.report.requested.v1",
    "bid.report.validated.v1",
    "bid.report.published.v1",
    "bid.report.failed.v1",
    "bid.report.superseded.v1",
)
PREVIOUS_OUTBOX_EVENT_TYPES = tuple(
    value for value in OUTBOX_EVENT_TYPES if value != "bid.upload_batch.abandoned.v1"
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column(
        "bid_upload_batches",
        sa.Column("abandon_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "bid_upload_batches",
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bid_upload_batches",
        sa.Column("cleanup_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bid_upload_batches",
        sa.Column("cleanup_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # No API-16 existed before this revision, but retain compatibility with
    # manually terminalized rows while making their legacy origin explicit.
    op.execute(
        sa.text(
            "UPDATE bid_upload_batches "
            "SET abandon_reason = 'legacy_abandoned_before_api16', "
            "abandoned_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP), "
            "cleanup_after = DATE_ADD("
            "COALESCE(updated_at, created_at, CURRENT_TIMESTAMP), INTERVAL 1 DAY) "
            "WHERE status = 'abandoned'"
        )
    )
    op.create_check_constraint(
        "ck_bid_upload_batches_abandonment",
        "bid_upload_batches",
        "((status = 'abandoned' AND abandon_reason IS NOT NULL "
        "AND abandoned_at IS NOT NULL AND cleanup_after IS NOT NULL) OR "
        "(status <> 'abandoned' AND abandon_reason IS NULL "
        "AND abandoned_at IS NULL AND cleanup_after IS NULL "
        "AND cleanup_completed_at IS NULL))",
    )
    op.create_check_constraint(
        "ck_bid_upload_batches_cleanup_order",
        "bid_upload_batches",
        "(cleanup_completed_at IS NULL OR "
        "(abandoned_at IS NOT NULL AND cleanup_completed_at >= abandoned_at))",
    )
    op.create_index(
        "ix_bid_upload_batches_cleanup_due",
        "bid_upload_batches",
        ["status", "cleanup_completed_at", "cleanup_after"],
        unique=False,
    )
    op.drop_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(OUTBOX_EVENT_TYPES)})",
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0091 guarded downgrade requires an online database connection; "
            "offline SQL would bypass API-16 abandonment and event data checks"
        )
    bind = op.get_bind()
    abandoned_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_upload_batches "
                "WHERE abandon_reason IS NOT NULL OR abandoned_at IS NOT NULL "
                "OR cleanup_after IS NOT NULL OR cleanup_completed_at IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    event_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_outbox_events "
                "WHERE event_type = 'bid.upload_batch.abandoned.v1'"
            )
        ).scalar()
        or 0
    )
    if abandoned_count or event_count:
        raise RuntimeError(
            "0091 downgrade would erase API-16 abandonment lineage; "
            "archive/remove abandoned batches and events first"
        )

    op.drop_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(PREVIOUS_OUTBOX_EVENT_TYPES)})",
    )
    op.drop_constraint(
        "ck_bid_upload_batches_cleanup_order",
        "bid_upload_batches",
        type_="check",
    )
    op.drop_index(
        "ix_bid_upload_batches_cleanup_due",
        table_name="bid_upload_batches",
    )
    op.drop_constraint(
        "ck_bid_upload_batches_abandonment",
        "bid_upload_batches",
        type_="check",
    )
    op.drop_column("bid_upload_batches", "cleanup_completed_at")
    op.drop_column("bid_upload_batches", "cleanup_after")
    op.drop_column("bid_upload_batches", "abandoned_at")
    op.drop_column("bid_upload_batches", "abandon_reason")
