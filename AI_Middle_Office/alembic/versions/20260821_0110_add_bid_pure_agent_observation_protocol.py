"""add Pure Agent observation artifacts and Tool protocol arguments

Revision ID: 20260821_0110
Revises: 20260820_0109
Create Date: 2026-08-21

This development-only revision must not be applied to ECS or any production
database before the Pure Agent receives explicit release authorization.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260821_0110"
down_revision: Union[str, None] = "20260820_0109"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.add_column(
        "bid_pa_calls",
        sa.Column("input_json", sa.JSON(), nullable=True),
    )
    op.create_table(
        "bid_pa_observation_artifacts",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("context_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("action_sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("observation_json", sa.JSON(), nullable=False),
        sa.Column("observation_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_ref", sa.String(length=160), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('control_decision', 'plan_revision', 'tool_result', "
            "'slot_request', 'answer_draft', 'runtime_limit', 'error')",
            name="ck_bid_pa_observation_artifacts_kind",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'no_result', 'degraded', 'rejected', 'failed')",
            name="ck_bid_pa_observation_artifacts_status",
        ),
        sa.CheckConstraint(
            "state_version >= 1",
            name="ck_bid_pa_observation_artifacts_state_version",
        ),
        sa.CheckConstraint(
            "action_sequence >= 1",
            name="ck_bid_pa_observation_artifacts_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_observation_artifacts_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["bid_pa_actions.id"],
            name="fk_bid_pa_observation_artifacts_action",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["bid_pa_context_snapshots.id"],
            name="fk_bid_pa_observation_artifacts_context",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_bid_pa_observation_artifacts",
        ),
        sa.UniqueConstraint(
            "action_id",
            name="uq_bid_pa_observation_artifacts_action",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_pa_observation_artifacts_task_sequence",
        "bid_pa_observation_artifacts",
        ["task_id", "action_sequence"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0110 guarded downgrade requires an online local development database"
        )
    bind = op.get_bind()
    observation_count = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM bid_pa_observation_artifacts")
        ).scalar()
        or 0
    )
    argument_count = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM bid_pa_calls WHERE input_json IS NOT NULL")
        ).scalar()
        or 0
    )
    if observation_count or argument_count:
        raise RuntimeError(
            "0110 downgrade would erase Pure Agent observation or Tool protocol data"
        )
    op.drop_table("bid_pa_observation_artifacts")
    with op.batch_alter_table("bid_pa_calls") as batch_op:
        batch_op.drop_column("input_json")
