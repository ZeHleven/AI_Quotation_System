"""add Phase 3F durable Tool Adapter/Executor dispatch authority

Revision ID: 20260812_0096
Revises: 20260812_0095
Create Date: 2026-08-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260812_0096"
down_revision: Union[str, None] = "20260812_0095"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_bid_async_operations_task_attempt_id",
        "bid_async_operations",
        ["task_id", "task_attempt_id", "id"],
    )
    op.create_table(
        "bid_tool_dispatches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invocation_id", sa.String(length=36), nullable=False),
        sa.Column("async_operation_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("adapter_name", sa.String(length=128), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("adapter_mode", sa.String(length=24), nullable=False),
        sa.Column("replay_policy", sa.String(length=32), nullable=False),
        sa.Column("dispatch_key", sa.String(length=128), nullable=False),
        sa.Column("envelope_json", sa.JSON(), nullable=False),
        sa.Column("envelope_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_request_id", sa.String(length=191), nullable=False),
        sa.Column("provider_receipt_id", sa.String(length=191), nullable=True),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "reserved_cost_microunits", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "actual_cost_microunits", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'sending', 'awaiting_receipt', "
            "'retry_wait', 'succeeded', 'failed', 'cancelled', 'uncertain', "
            "'dead_letter')",
            name="ck_bid_tool_dispatches_status",
        ),
        sa.CheckConstraint(
            "adapter_mode IN ('local_readonly', 'external_async')",
            name="ck_bid_tool_dispatches_adapter_mode",
        ),
        sa.CheckConstraint(
            "replay_policy IN ('safe_idempotent', 'reconcile_required', 'no_replay')",
            name="ck_bid_tool_dispatches_replay_policy",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_bid_tool_dispatches_attempts",
        ),
        sa.CheckConstraint("fencing_token >= 0", name="ck_bid_tool_dispatches_fencing"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_tool_dispatches_row_version"),
        sa.CheckConstraint(
            "reserved_cost_microunits >= 0 AND actual_cost_microunits >= 0",
            name="ck_bid_tool_dispatches_cost",
        ),
        sa.CheckConstraint(
            "((status IN ('leased', 'sending') AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('queued', 'retry_wait', 'awaiting_receipt') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled', 'uncertain', 'dead_letter') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND completed_at IS NOT NULL))",
            name="ck_bid_tool_dispatches_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_tool_dispatches_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_attempt_id", "invocation_id"],
            ["bid_tool_invocations.task_attempt_id", "bid_tool_invocations.id"],
            name="fk_bid_tool_dispatches_invocation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "task_attempt_id", "async_operation_id"],
            [
                "bid_async_operations.task_id",
                "bid_async_operations.task_attempt_id",
                "bid_async_operations.id",
            ],
            name="fk_bid_tool_dispatches_operation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_tool_dispatches"),
        sa.UniqueConstraint("invocation_id", name="uq_bid_tool_dispatches_invocation"),
        sa.UniqueConstraint("async_operation_id", name="uq_bid_tool_dispatches_operation"),
        sa.UniqueConstraint("dispatch_key", name="uq_bid_tool_dispatches_key"),
        sa.UniqueConstraint(
            "provider_request_id", name="uq_bid_tool_dispatches_provider_request"
        ),
        sa.UniqueConstraint(
            "provider_receipt_id", name="uq_bid_tool_dispatches_provider_receipt"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_tool_dispatches_ready",
        "bid_tool_dispatches",
        ["status", "available_at", "lease_until"],
    )
    op.create_index(
        "ix_bid_tool_dispatches_task",
        "bid_tool_dispatches",
        ["task_id", "created_at"],
    )

    op.create_table(
        "bid_tool_dispatch_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dispatch_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("execution_key", sa.String(length=128), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('leased', 'sending', 'acknowledged', 'succeeded', 'failed', "
            "'lease_expired', 'cancelled', 'uncertain')",
            name="ck_bid_tool_dispatch_attempts_status",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_bid_tool_dispatch_attempts_number"),
        sa.CheckConstraint(
            "fencing_token >= 1", name="ck_bid_tool_dispatch_attempts_fencing"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bid_tool_dispatch_attempts_time_order",
        ),
        sa.CheckConstraint(
            "((status IN ('leased', 'sending') AND finished_at IS NULL) OR "
            "(status IN ('acknowledged', 'succeeded', 'failed', 'lease_expired', "
            "'cancelled', 'uncertain') AND finished_at IS NOT NULL))",
            name="ck_bid_tool_dispatch_attempts_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_id"],
            ["bid_tool_dispatches.id"],
            name="fk_bid_tool_dispatch_attempts_dispatch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_tool_dispatch_attempts"),
        sa.UniqueConstraint(
            "dispatch_id", "attempt_no", name="uq_bid_tool_dispatch_attempts_number"
        ),
        sa.UniqueConstraint(
            "dispatch_id", "fencing_token", name="uq_bid_tool_dispatch_attempts_fencing"
        ),
        sa.UniqueConstraint(
            "execution_key", name="uq_bid_tool_dispatch_attempts_execution_key"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_tool_dispatch_attempts_lease",
        "bid_tool_dispatch_attempts",
        ["status", "lease_until"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0096 guarded downgrade requires an online database connection; "
            "offline SQL would bypass Tool dispatch lineage checks"
        )
    bind = op.get_bind()
    counts = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in ("bid_tool_dispatch_attempts", "bid_tool_dispatches")
    }
    if any(counts.values()):
        raise RuntimeError(
            "0096 downgrade would erase immutable Tool dispatch lineage; "
            "export and explicitly remove Phase 3F rows first"
        )
    op.drop_table("bid_tool_dispatch_attempts")
    op.drop_table("bid_tool_dispatches")
    op.drop_constraint(
        "uq_bid_async_operations_task_attempt_id",
        "bid_async_operations",
        type_="unique",
    )
