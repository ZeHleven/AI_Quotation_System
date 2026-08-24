"""allow Phase 4A-1 Plan Continuation event

Revision ID: 20260812_0098
Revises: 20260812_0097
Create Date: 2026-08-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260812_0098"
down_revision: Union[str, None] = "20260812_0097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

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
    "bid.run.succeeded.v1", "bid.run.failed.v1", "bid.run.stale.v1",
    "bid.facts.changed.v1", "bid.calculation.completed.v1",
    "bid.gates.evaluated.v1", "bid.question.published.v1",
    "bid.question.answered.v1", "bid.dimensions.completed.v1",
    "bid.decision.completed.v1", "bid.owner_override.recorded.v1",
    "bid.report.requested.v1", "bid.report.validated.v1",
    "bid.report.published.v1", "bid.report.failed.v1",
    "bid.report.superseded.v1",
)
OUTBOX_EVENT_TYPES = (
    *PREVIOUS_OUTBOX_EVENT_TYPES,
    "bid.plan.continuation_requested.v1",
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_constraint("ck_bid_outbox_events_type", "bid_outbox_events", type_="check")
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(OUTBOX_EVENT_TYPES)})",
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0098 guarded downgrade requires an online database connection; "
            "offline SQL would bypass Plan Continuation lineage checks"
        )
    bind = op.get_bind()
    event_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_outbox_events "
                "WHERE event_type = 'bid.plan.continuation_requested.v1'"
            )
        ).scalar()
        or 0
    )
    phase4_plan_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_plan_revisions "
                "WHERE CAST(proposal_json AS CHAR) "
                "LIKE '%bid.plan.commit.envelope.v2%'"
            )
        ).scalar()
        or 0
    )
    if event_count or phase4_plan_count:
        raise RuntimeError(
            "0098 downgrade would invalidate persisted Plan Continuation or SkillBinding lineage; "
            "export and explicitly remove Phase 4A-1 rows first"
        )
    op.drop_constraint("ck_bid_outbox_events_type", "bid_outbox_events", type_="check")
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(PREVIOUS_OUTBOX_EVENT_TYPES)})",
    )
