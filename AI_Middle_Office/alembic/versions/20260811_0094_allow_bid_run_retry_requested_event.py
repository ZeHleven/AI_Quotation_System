"""allow the Phase 3D run retry-requested outbox event

Revision ID: 20260811_0094
Revises: 20260811_0093
Create Date: 2026-08-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260811_0094"
down_revision: Union[str, None] = "20260811_0093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREVIOUS_OUTBOX_EVENT_TYPES = (
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
    "bid.manifest.parse_set_ready.v1",
    "bid.lot_detection.requested.v1",
    "bid.lots.detected.v1",
    "bid.lot_detection.failed.v1",
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
OUTBOX_EVENT_TYPES = (
    *PREVIOUS_OUTBOX_EVENT_TYPES,
    "bid.run.retry_requested.v1",
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
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
            "0094 guarded downgrade requires an online database connection; "
            "offline SQL would bypass retry-requested event checks"
        )

    event_count = int(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_outbox_events "
                "WHERE event_type = 'bid.run.retry_requested.v1'"
            )
        )
        .scalar()
        or 0
    )
    if event_count:
        raise RuntimeError(
            "0094 downgrade would invalidate persisted run retry-requested events; "
            "archive and explicitly remove those rows first"
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
