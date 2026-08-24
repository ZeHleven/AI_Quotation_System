"""add Phase 3E context and tool execution authority

Revision ID: 20260812_0095
Revises: 20260811_0094
Create Date: 2026-08-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260812_0095"
down_revision: Union[str, None] = "20260811_0094"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _require_empty_legacy_context_refs() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0095 requires an online local/development database so legacy "
            "checkpoint context placeholders can be proven empty"
        )
    count = int(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_checkpoints "
                "WHERE context_manifest_id IS NOT NULL"
            )
        )
        .scalar()
        or 0
    )
    if count:
        raise RuntimeError(
            "0095 refuses to reinterpret legacy checkpoint context_manifest_id "
            "placeholders; export and explicitly reconcile those rows first"
        )


def upgrade() -> None:
    _require_empty_legacy_context_refs()

    op.create_table(
        "bid_context_manifests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_seq", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("context_profile", sa.String(length=64), nullable=False),
        sa.Column("assembler_version", sa.String(length=80), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("compression_level", sa.Integer(), nullable=False),
        sa.Column("bound_versions_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('planner', 'local_research', 'synthesizer', "
            "'evidence_validator', 'report_writer')",
            name="ck_bid_context_manifests_role",
        ),
        sa.CheckConstraint("manifest_seq >= 1", name="ck_bid_context_manifests_seq"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_context_manifests_fencing"),
        sa.CheckConstraint(
            "token_estimate BETWEEN 0 AND 32000",
            name="ck_bid_context_manifests_tokens",
        ),
        sa.CheckConstraint(
            "compression_level BETWEEN 0 AND 3",
            name="ck_bid_context_manifests_compression",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_context_manifests_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_context_manifests_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_context_manifests_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_context_manifests"),
        sa.UniqueConstraint(
            "task_attempt_id", "manifest_seq", name="uq_bid_context_manifests_seq"
        ),
        sa.UniqueConstraint(
            "task_attempt_id", "manifest_hash", name="uq_bid_context_manifests_hash"
        ),
        sa.UniqueConstraint(
            "task_attempt_id", "id", name="uq_bid_context_manifests_attempt_id"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_context_manifests_run",
        "bid_context_manifests",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_bid_context_manifests_task",
        "bid_context_manifests",
        ["task_id", "created_at"],
    )

    op.create_table(
        "bid_tool_invocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_call_id", sa.String(length=80), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("context_manifest_id", sa.String(length=36), nullable=False),
        sa.Column("tool_registry_version_id", sa.String(length=36), nullable=False),
        sa.Column("async_operation_id", sa.String(length=36), nullable=True),
        sa.Column("checkpoint_id", sa.String(length=36), nullable=True),
        sa.Column("invocation_seq", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_profile", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="accepted", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("budget_before_json", sa.JSON(), nullable=False),
        sa.Column("budget_after_json", sa.JSON(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
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
        sa.CheckConstraint(
            "status IN ('accepted', 'pending', 'succeeded', 'failed', "
            "'rejected', 'cancelled')",
            name="ck_bid_tool_invocations_status",
        ),
        sa.CheckConstraint("invocation_seq >= 1", name="ck_bid_tool_invocations_seq"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_tool_invocations_fencing"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_tool_invocations_row_version"),
        sa.CheckConstraint(
            "((status = 'accepted' AND completed_at IS NULL "
            "AND async_operation_id IS NULL AND checkpoint_id IS NULL) OR "
            "(status = 'pending' AND completed_at IS NULL "
            "AND async_operation_id IS NOT NULL AND checkpoint_id IS NOT NULL) OR "
            "(status IN ('succeeded', 'failed', 'rejected', 'cancelled') "
            "AND completed_at IS NOT NULL))",
            name="ck_bid_tool_invocations_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_tool_invocations_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_tool_invocations_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_tool_invocations_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_attempt_id", "context_manifest_id"],
            ["bid_context_manifests.task_attempt_id", "bid_context_manifests.id"],
            name="fk_bid_tool_invocations_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_registry_version_id"],
            ["bid_tool_registry_versions.id"],
            name="fk_bid_tool_invocations_registry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["async_operation_id"],
            ["bid_async_operations.id"],
            name="fk_bid_tool_invocations_async",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["bid_checkpoints.id"],
            name="fk_bid_tool_invocations_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_tool_invocations"),
        sa.UniqueConstraint("tool_call_id", name="uq_bid_tool_invocations_call_id"),
        sa.UniqueConstraint(
            "task_attempt_id", "invocation_seq", name="uq_bid_tool_invocations_seq"
        ),
        sa.UniqueConstraint(
            "task_attempt_id",
            "idempotency_key",
            name="uq_bid_tool_invocations_idempotency",
        ),
        sa.UniqueConstraint(
            "task_attempt_id", "id", name="uq_bid_tool_invocations_attempt_id"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_tool_invocations_run",
        "bid_tool_invocations",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_bid_tool_invocations_status",
        "bid_tool_invocations",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_bid_tool_invocations_async",
        "bid_tool_invocations",
        ["async_operation_id"],
    )

    op.create_table(
        "bid_tool_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invocation_id", sa.String(length=36), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("async_operation_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("storage_kind", sa.String(length=16), nullable=False),
        sa.Column("inline_data_json", sa.JSON(), nullable=True),
        sa.Column("object_ref", sa.String(length=512), nullable=True),
        sa.Column("data_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("returned_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'no_result', 'partial', 'failed', 'unauthorized', "
            "'invalid_arguments', 'missing_inputs', 'stale', 'budget_exhausted')",
            name="ck_bid_tool_results_status",
        ),
        sa.CheckConstraint(
            "storage_kind IN ('inline', 'external')",
            name="ck_bid_tool_results_storage",
        ),
        sa.CheckConstraint("byte_count >= 0", name="ck_bid_tool_results_bytes"),
        sa.CheckConstraint("returned_items >= 0", name="ck_bid_tool_results_items"),
        sa.CheckConstraint(
            "((storage_kind = 'inline' AND inline_data_json IS NOT NULL "
            "AND object_ref IS NULL) OR "
            "(storage_kind = 'external' AND object_ref IS NOT NULL))",
            name="ck_bid_tool_results_payload",
        ),
        sa.ForeignKeyConstraint(
            ["task_attempt_id", "invocation_id"],
            ["bid_tool_invocations.task_attempt_id", "bid_tool_invocations.id"],
            name="fk_bid_tool_results_invocation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["async_operation_id"],
            ["bid_async_operations.id"],
            name="fk_bid_tool_results_async",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_tool_results"),
        sa.UniqueConstraint("invocation_id", name="uq_bid_tool_results_invocation"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_tool_results_attempt",
        "bid_tool_results",
        ["task_attempt_id", "created_at"],
    )
    op.create_index(
        "ix_bid_tool_results_expiry",
        "bid_tool_results",
        ["expires_at"],
    )

    op.create_foreign_key(
        "fk_bid_checkpoints_context_manifest",
        "bid_checkpoints",
        "bid_context_manifests",
        ["context_manifest_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_bid_checkpoints_context_manifest",
        "bid_checkpoints",
        ["context_manifest_id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0095 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable context/tool lineage checks"
        )
    bind = op.get_bind()
    tables = (
        "bid_tool_results",
        "bid_tool_invocations",
        "bid_context_manifests",
    )
    nonempty = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in tables
    }
    checkpoint_refs = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_checkpoints "
                "WHERE context_manifest_id IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    if any(nonempty.values()) or checkpoint_refs:
        raise RuntimeError(
            "0095 downgrade would erase immutable context/tool execution lineage; "
            "export and explicitly remove Phase 3E rows first"
        )

    op.drop_index("ix_bid_checkpoints_context_manifest", table_name="bid_checkpoints")
    op.drop_constraint(
        "fk_bid_checkpoints_context_manifest",
        "bid_checkpoints",
        type_="foreignkey",
    )
    op.drop_table("bid_tool_results")
    op.drop_table("bid_tool_invocations")
    op.drop_table("bid_context_manifests")
