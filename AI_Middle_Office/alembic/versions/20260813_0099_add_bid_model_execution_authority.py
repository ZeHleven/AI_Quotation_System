"""add Phase 4A-2 controlled model execution authority

Revision ID: 20260813_0099
Revises: 20260812_0098
Create Date: 2026-08-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260813_0099"
down_revision: Union[str, None] = "20260812_0098"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_bid_checkpoints_attempt_id",
        "bid_checkpoints",
        ["task_attempt_id", "id"],
    )
    op.create_table(
        "bid_model_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=36), nullable=False),
        sa.Column("context_manifest_id", sa.String(length=36), nullable=False),
        sa.Column("async_operation_id", sa.String(length=36), nullable=False),
        sa.Column("model_profile_version_id", sa.String(length=36), nullable=False),
        sa.Column("prompt_bundle_id", sa.String(length=36), nullable=False),
        sa.Column("action_seq", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("logical_role", sa.String(length=32), nullable=False),
        sa.Column("provider_ref", sa.String(length=128), nullable=False),
        sa.Column("model_ref", sa.String(length=128), nullable=False),
        sa.Column("prompt_role", sa.String(length=128), nullable=False),
        sa.Column("action_schema", sa.String(length=128), nullable=False),
        sa.Column("replay_policy", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_envelope_json", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="accepted", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("actual_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("actual_output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved_cost_microunits", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("actual_cost_microunits", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'leased', 'sending', 'retry_wait', 'succeeded', "
            "'failed', 'cancelled', 'uncertain', 'dead_letter')",
            name="ck_bid_model_calls_status",
        ),
        sa.CheckConstraint(
            "replay_policy IN ('safe_idempotent', 'reconcile_required', 'no_replay')",
            name="ck_bid_model_calls_replay_policy",
        ),
        sa.CheckConstraint("action_seq >= 1", name="ck_bid_model_calls_action_seq"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_model_calls_fencing"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_bid_model_calls_attempts",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_model_calls_row_version"),
        sa.CheckConstraint(
            "reserved_input_tokens >= 0 AND reserved_output_tokens >= 0 "
            "AND actual_input_tokens >= 0 AND actual_output_tokens >= 0 "
            "AND reserved_cost_microunits >= 0 AND actual_cost_microunits >= 0",
            name="ck_bid_model_calls_tokens",
        ),
        sa.CheckConstraint(
            "((status IN ('leased', 'sending') AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('accepted', 'retry_wait') AND lease_owner IS NULL "
            "AND lease_until IS NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled', 'uncertain', 'dead_letter') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND completed_at IS NOT NULL))",
            name="ck_bid_model_calls_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_model_calls_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_model_calls_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_model_calls_task_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_attempt_id", "context_manifest_id"],
            ["bid_context_manifests.task_attempt_id", "bid_context_manifests.id"],
            name="fk_bid_model_calls_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_attempt_id", "checkpoint_id"],
            ["bid_checkpoints.task_attempt_id", "bid_checkpoints.id"],
            name="fk_bid_model_calls_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "task_attempt_id", "async_operation_id"],
            [
                "bid_async_operations.task_id",
                "bid_async_operations.task_attempt_id",
                "bid_async_operations.id",
            ],
            name="fk_bid_model_calls_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_profile_version_id"],
            ["bid_model_profile_versions.id"],
            name="fk_bid_model_calls_profile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_bundle_id"],
            ["bid_prompt_bundles.id"],
            name="fk_bid_model_calls_prompt_bundle",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_model_calls"),
        sa.UniqueConstraint("task_id", "action_seq", name="uq_bid_model_calls_action"),
        sa.UniqueConstraint(
            "task_id", "idempotency_key", name="uq_bid_model_calls_idempotency"
        ),
        sa.UniqueConstraint("async_operation_id", name="uq_bid_model_calls_operation"),
        sa.UniqueConstraint("task_id", "id", name="uq_bid_model_calls_task_id"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_model_calls_ready",
        "bid_model_calls",
        ["status", "available_at", "lease_until"],
    )
    op.create_index(
        "ix_bid_model_calls_run", "bid_model_calls", ["run_id", "created_at"]
    )
    op.create_index(
        "ix_bid_model_calls_attempt",
        "bid_model_calls",
        ["task_attempt_id", "created_at"],
    )

    op.create_table(
        "bid_model_call_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("model_call_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("execution_key", sa.String(length=128), nullable=False),
        sa.Column("provider_request_id", sa.String(length=191), nullable=False),
        sa.Column("provider_receipt_id", sa.String(length=191), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('leased', 'sending', 'succeeded', 'failed', 'lease_expired', "
            "'cancelled', 'uncertain')",
            name="ck_bid_model_call_attempts_status",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_bid_model_call_attempts_number"),
        sa.CheckConstraint(
            "fencing_token >= 1", name="ck_bid_model_call_attempts_fencing"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bid_model_call_attempts_time_order",
        ),
        sa.CheckConstraint(
            "((status IN ('leased', 'sending') AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'lease_expired', 'cancelled', 'uncertain') "
            "AND finished_at IS NOT NULL))",
            name="ck_bid_model_call_attempts_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["bid_model_calls.id"],
            name="fk_bid_model_call_attempts_call",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_model_call_attempts"),
        sa.UniqueConstraint(
            "model_call_id", "attempt_no", name="uq_bid_model_call_attempts_number"
        ),
        sa.UniqueConstraint(
            "model_call_id", "fencing_token", name="uq_bid_model_call_attempts_fencing"
        ),
        sa.UniqueConstraint(
            "model_call_id", "id", name="uq_bid_model_call_attempts_call_id"
        ),
        sa.UniqueConstraint(
            "execution_key", name="uq_bid_model_call_attempts_execution_key"
        ),
        sa.UniqueConstraint(
            "provider_request_id", name="uq_bid_model_call_attempts_provider_request"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_model_call_attempts_lease",
        "bid_model_call_attempts",
        ["status", "lease_until"],
    )

    op.create_table(
        "bid_model_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("model_call_id", sa.String(length=36), nullable=False),
        sa.Column("model_call_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("source_task_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=48), nullable=False),
        sa.Column("storage_kind", sa.String(length=16), server_default="inline", nullable=False),
        sa.Column("action_json", sa.JSON(), nullable=True),
        sa.Column("object_ref", sa.String(length=512), nullable=True),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("actual_cost_microunits", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("finish_reason", sa.String(length=64), nullable=False),
        sa.Column("provider_receipt_id", sa.String(length=191), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "storage_kind IN ('inline', 'external')",
            name="ck_bid_model_results_storage_kind",
        ),
        sa.CheckConstraint(
            "((storage_kind = 'inline' AND action_json IS NOT NULL AND object_ref IS NULL) OR "
            "(storage_kind = 'external' AND action_json IS NULL AND object_ref IS NOT NULL))",
            name="ck_bid_model_results_payload",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND actual_cost_microunits >= 0",
            name="ck_bid_model_results_tokens",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "model_call_id"],
            ["bid_model_calls.task_id", "bid_model_calls.id"],
            name="fk_bid_model_results_call",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id", "model_call_attempt_id"],
            ["bid_model_call_attempts.model_call_id", "bid_model_call_attempts.id"],
            name="fk_bid_model_results_call_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "source_task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_model_results_source_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_model_results"),
        sa.UniqueConstraint("model_call_id", name="uq_bid_model_results_call"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_model_results_task", "bid_model_results", ["task_id", "created_at"]
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0099 guarded downgrade requires an online database connection; "
            "offline SQL would bypass model execution lineage checks"
        )
    bind = op.get_bind()
    counts = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in (
            "bid_model_results",
            "bid_model_call_attempts",
            "bid_model_calls",
        )
    }
    if any(counts.values()):
        raise RuntimeError(
            "0099 downgrade would erase immutable ModelCall/Attempt/Result lineage; "
            "export and explicitly remove Phase 4A-2 rows first"
        )
    op.drop_table("bid_model_results")
    op.drop_table("bid_model_call_attempts")
    op.drop_table("bid_model_calls")
    op.drop_constraint(
        "uq_bid_checkpoints_attempt_id",
        "bid_checkpoints",
        type_="unique",
    )
