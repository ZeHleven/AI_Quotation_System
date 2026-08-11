"""add bid assessment runtime, lease, checkpoint, and question skeleton

Revision ID: 20260810_0085
Revises: 20260810_0084
Create Date: 2026-08-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0085"
down_revision: Union[str, None] = "20260810_0084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

RUN_STATES = (
    "created", "planning", "queued", "running", "waiting_input",
    "waiting_operation", "validating", "succeeded", "failed", "stale", "cancelled",
)
PLAN_STATES = ("proposed", "validating", "committed", "rejected", "superseded")
TASK_STATES = (
    "blocked", "ready", "leased", "running", "waiting_operation", "waiting_input",
    "validating", "succeeded", "failed", "skipped", "stale", "cancelled",
)
ATTEMPT_STATES = (
    "created", "leased", "running", "waiting_operation", "waiting_input",
    "validating", "succeeded", "failed", "stale", "cancelled", "lease_expired",
)
ASYNC_STATES = ("created", "submitted", "running", "succeeded", "failed", "cancelled", "timed_out")
QUESTION_STATES = ("candidate", "published", "answered", "expired", "withdrawn", "discarded", "superseded")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_bid_assessment_scopes_owner_id",
        "bid_assessment_scopes",
        ["assessment_id", "id"],
    )

    op.create_table(
        "bid_analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_snapshot_id", sa.String(length=36), sa.ForeignKey("bid_enterprise_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rule_set_id", sa.String(length=36), sa.ForeignKey("bid_rule_sets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fact_catalog_version_id", sa.String(length=36), sa.ForeignKey("bid_fact_catalog_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("prompt_bundle_id", sa.String(length=36), sa.ForeignKey("bid_prompt_bundles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tool_registry_version_id", sa.String(length=36), sa.ForeignKey("bid_tool_registry_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("model_profile_version_id", sa.String(length=36), sa.ForeignKey("bid_model_profile_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("formula_catalog_version_id", sa.String(length=36), sa.ForeignKey("bid_formula_catalog_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("restart_of_run_id", sa.String(length=36), nullable=True),
        sa.Column("run_sequence", sa.Integer(), nullable=False),
        sa.Column("run_kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="created", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("evaluation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_stage", sa.String(length=80), nullable=True),
        sa.Column("waiting_reason", sa.String(length=500), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checkpoint_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({_in_values(RUN_STATES)})", name="ck_bid_analysis_runs_status"),
        sa.CheckConstraint("run_kind IN ('preliminary', 'deep', 'reanalysis')", name="ck_bid_analysis_runs_kind"),
        sa.CheckConstraint("run_sequence >= 1", name="ck_bid_analysis_runs_sequence"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_analysis_runs_row_version"),
        sa.ForeignKeyConstraint(["assessment_id"], ["bid_assessments.id"], name="fk_bid_analysis_runs_assessment", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_id", "scope_id"], ["bid_assessment_scopes.assessment_id", "bid_assessment_scopes.id"], name="fk_bid_analysis_runs_scope", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_id", "manifest_id"], ["bid_document_manifests.assessment_id", "bid_document_manifests.id"], name="fk_bid_analysis_runs_manifest", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_id", "restart_of_run_id"], ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"], name="fk_bid_analysis_runs_restart", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_analysis_runs"),
        sa.UniqueConstraint("assessment_id", "id", name="uq_bid_analysis_runs_owner_id"),
        sa.UniqueConstraint("assessment_id", "run_sequence", name="uq_bid_analysis_runs_sequence"),
        sa.UniqueConstraint("assessment_id", "input_hash", "run_kind", name="uq_bid_analysis_runs_input"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_analysis_runs_assessment_status", "bid_analysis_runs", ["assessment_id", "status"])
    op.create_index("ix_bid_analysis_runs_fingerprint", "bid_analysis_runs", ["assessment_id", "input_fingerprint", "run_kind", "status"])
    op.create_index("ix_bid_analysis_runs_manifest", "bid_analysis_runs", ["manifest_id"])

    op.create_foreign_key(
        "fk_bid_assessments_active_run",
        "bid_assessments",
        "bid_analysis_runs",
        ["id", "active_run_id"],
        ["assessment_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "bid_plan_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("bid_analysis_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("proposal_json", sa.JSON(), nullable=False),
        sa.Column("validated_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="proposed", nullable=False),
        sa.Column("committed_slot_key", sa.String(length=32), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({_in_values(PLAN_STATES)})", name="ck_bid_plan_revisions_status"),
        sa.CheckConstraint("revision_no >= 1", name="ck_bid_plan_revisions_number"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_plan_revisions_row_version"),
        sa.CheckConstraint(
            "status NOT IN ('committed', 'rejected', 'superseded') OR validated_hash IS NOT NULL",
            name="ck_bid_plan_revisions_validated_hash",
        ),
        sa.CheckConstraint("((status = 'committed' AND committed_slot_key = 'committed' AND validated_hash IS NOT NULL AND committed_at IS NOT NULL) OR (status = 'superseded' AND committed_slot_key IS NULL AND validated_hash IS NOT NULL AND committed_at IS NOT NULL AND superseded_at IS NOT NULL) OR (status IN ('proposed', 'validating', 'rejected') AND committed_slot_key IS NULL))", name="ck_bid_plan_revisions_committed_slot"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_plan_revisions"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_plan_revisions_run_id"),
        sa.UniqueConstraint("run_id", "revision_no", name="uq_bid_plan_revisions_number"),
        sa.UniqueConstraint("run_id", "committed_slot_key", name="uq_bid_plan_revisions_committed"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_plan_revisions_run_status", "bid_plan_revisions", ["run_id", "status"])

    op.create_table(
        "bid_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("plan_revision_id", sa.String(length=36), nullable=False),
        sa.Column("task_key", sa.String(length=160), nullable=False),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("objective", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="blocked", nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("tool_profile", sa.String(length=64), nullable=False),
        sa.Column("context_profile", sa.String(length=64), nullable=False),
        sa.Column("budget_profile", sa.String(length=64), nullable=False),
        sa.Column("completion_contract", sa.String(length=128), nullable=False),
        sa.Column("current_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({_in_values(TASK_STATES)})", name="ck_bid_tasks_status"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_tasks_row_version"),
        sa.ForeignKeyConstraint(["run_id"], ["bid_analysis_runs.id"], name="fk_bid_tasks_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id", "plan_revision_id"], ["bid_plan_revisions.run_id", "bid_plan_revisions.id"], name="fk_bid_tasks_plan", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_tasks"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_tasks_run_id"),
        sa.UniqueConstraint("run_id", "task_key", "input_hash", name="uq_bid_tasks_logical_input"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_tasks_run_status", "bid_tasks", ["run_id", "status"])
    op.create_index("ix_bid_tasks_type_status", "bid_tasks", ["task_type", "status"])
    op.create_index("ix_bid_tasks_current_attempt", "bid_tasks", ["current_attempt_id"])

    op.create_table(
        "bid_task_dependencies",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("depends_on_task_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_bid_task_dependencies_not_self"),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["bid_tasks.run_id", "bid_tasks.id"], name="fk_bid_task_dependencies_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id", "depends_on_task_id"], ["bid_tasks.run_id", "bid_tasks.id"], name="fk_bid_task_dependencies_parent", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("task_id", "depends_on_task_id", name="pk_bid_task_dependencies"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_task_dependencies_parent", "bid_task_dependencies", ["depends_on_task_id"])

    op.create_table(
        "bid_task_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("bid_tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="created", nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail_ref", sa.String(length=512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_reclaimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({_in_values(ATTEMPT_STATES)})", name="ck_bid_task_attempts_status"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_bid_task_attempts_number"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_task_attempts_fencing"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_task_attempts_row_version"),
        sa.CheckConstraint("status NOT IN ('leased', 'running', 'validating') OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL AND heartbeat_at IS NOT NULL)", name="ck_bid_task_attempts_active_lease"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_task_attempts"),
        sa.UniqueConstraint("task_id", "id", name="uq_bid_task_attempts_task_id"),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_bid_task_attempts_number"),
        sa.UniqueConstraint("task_id", "fencing_token", name="uq_bid_task_attempts_fencing"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_task_attempts_task_status", "bid_task_attempts", ["task_id", "status"])
    op.create_index("ix_bid_task_attempts_lease", "bid_task_attempts", ["status", "lease_until", "heartbeat_at"])

    op.create_foreign_key(
        "fk_bid_tasks_current_attempt",
        "bid_tasks",
        "bid_task_attempts",
        ["id", "current_attempt_id"],
        ["task_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "bid_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=36), sa.ForeignKey("bid_task_attempts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("action_seq", sa.Integer(), nullable=False),
        sa.Column("context_manifest_id", sa.String(length=36), nullable=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("tool_refs_json", sa.JSON(), nullable=True),
        sa.Column("budget_usage_json", sa.JSON(), nullable=True),
        sa.Column("candidate_output_ref", sa.String(length=512), nullable=True),
        sa.Column("next_state", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("action_seq >= 0", name="ck_bid_checkpoints_action_seq"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_checkpoints_fencing"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_checkpoints"),
        sa.UniqueConstraint("task_attempt_id", "action_seq", name="uq_bid_checkpoints_action"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_checkpoints_attempt_created", "bid_checkpoints", ["task_attempt_id", "created_at"])

    op.create_table(
        "bid_async_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("operation_type", sa.String(length=64), nullable=False),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="created", nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_ref", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({_in_values(ASYNC_STATES)})", name="ck_bid_async_operations_status"),
        sa.CheckConstraint("retry_count >= 0", name="ck_bid_async_operations_retry"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_async_operations_row_version"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_tasks.id"], name="fk_bid_async_operations_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id", "task_attempt_id"], ["bid_task_attempts.task_id", "bid_task_attempts.id"], name="fk_bid_async_operations_attempt", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_async_operations"),
        sa.UniqueConstraint("task_id", "operation_type", "input_hash", name="uq_bid_async_operations_task_input"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_async_operations_status_timeout", "bid_async_operations", ["status", "timeout_at"])
    op.create_index("ix_bid_async_operations_attempt", "bid_async_operations", ["task_attempt_id"])

    op.create_table(
        "bid_question_rounds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="candidate", nullable=False),
        sa.Column("open_slot_key", sa.String(length=32), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({_in_values(QUESTION_STATES)})", name="ck_bid_question_rounds_status"),
        sa.CheckConstraint("round_no >= 1", name="ck_bid_question_rounds_number"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_question_rounds_row_version"),
        sa.CheckConstraint("((status = 'published' AND open_slot_key = 'published' AND published_at IS NOT NULL) OR (status IN ('answered', 'expired', 'withdrawn', 'superseded') AND open_slot_key IS NULL AND published_at IS NOT NULL) OR (status IN ('candidate', 'discarded') AND open_slot_key IS NULL))", name="ck_bid_question_rounds_open_slot"),
        sa.CheckConstraint("status <> 'answered' OR answered_at IS NOT NULL", name="ck_bid_question_rounds_answered_at"),
        sa.CheckConstraint("status <> 'withdrawn' OR withdrawn_at IS NOT NULL", name="ck_bid_question_rounds_withdrawn_at"),
        sa.ForeignKeyConstraint(["assessment_id"], ["bid_assessments.id"], name="fk_bid_question_rounds_assessment", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_id", "run_id"], ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"], name="fk_bid_question_rounds_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_id", "manifest_id"], ["bid_document_manifests.assessment_id", "bid_document_manifests.id"], name="fk_bid_question_rounds_manifest", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_question_rounds"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_question_rounds_run_id"),
        sa.UniqueConstraint("id", "manifest_id", name="uq_bid_question_rounds_manifest_id"),
        sa.UniqueConstraint("run_id", "round_no", name="uq_bid_question_rounds_number"),
        sa.UniqueConstraint("run_id", "open_slot_key", name="uq_bid_question_rounds_open_slot"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_question_rounds_assessment_status", "bid_question_rounds", ["assessment_id", "status"])
    op.create_index("ix_bid_question_rounds_run_created", "bid_question_rounds", ["run_id", "created_at"])

    op.create_table(
        "bid_questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question_round_id", sa.String(length=36), sa.ForeignKey("bid_question_rounds.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("question_order", sa.Integer(), nullable=False),
        sa.Column("fact_slot", sa.String(length=160), nullable=False),
        sa.Column("question_type", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=24), nullable=False),
        sa.Column("question_text", sa.String(length=1000), nullable=False),
        sa.Column("why_needed", sa.String(length=1000), nullable=False),
        sa.Column("impact", sa.String(length=32), nullable=False),
        sa.Column("impact_json", sa.JSON(), nullable=True),
        sa.Column("answer_schema_json", sa.JSON(), nullable=False),
        sa.Column("allow_unknown", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="candidate", nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("question_order BETWEEN 1 AND 3", name="ck_bid_questions_order"),
        sa.CheckConstraint(f"status IN ({_in_values(QUESTION_STATES)})", name="ck_bid_questions_status"),
        sa.CheckConstraint("question_type IN ('single_choice', 'boolean_unknown', 'number_with_unit', 'money', 'date', 'short_text', 'document_upload')", name="ck_bid_questions_type"),
        sa.CheckConstraint("priority IN ('critical', 'important', 'contextual')", name="ck_bid_questions_priority"),
        sa.CheckConstraint("impact IN ('decision_critical', 'decision_sensitive', 'contextual')", name="ck_bid_questions_impact"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_questions_row_version"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_questions"),
        sa.UniqueConstraint("question_round_id", "id", name="uq_bid_questions_round_id"),
        sa.UniqueConstraint("question_round_id", "question_order", name="uq_bid_questions_order"),
        sa.UniqueConstraint("question_round_id", "fact_slot", name="uq_bid_questions_fact_slot"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_questions_round_status", "bid_questions", ["question_round_id", "status"])

    op.create_table(
        "bid_answer_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question_round_id", sa.String(length=36), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("answer_status", sa.String(length=24), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("draft_hash", sa.String(length=64), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("answer_status IN ('answered', 'unknown')", name="ck_bid_answer_drafts_status"),
        sa.CheckConstraint("((answer_status = 'answered' AND value_json IS NOT NULL) OR (answer_status = 'unknown' AND value_json IS NULL))", name="ck_bid_answer_drafts_value"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_answer_drafts_row_version"),
        sa.ForeignKeyConstraint(["question_round_id", "question_id"], ["bid_questions.question_round_id", "bid_questions.id"], name="fk_bid_answer_drafts_question", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_answer_drafts"),
        sa.UniqueConstraint("question_round_id", "question_id", "actor_id", name="uq_bid_answer_drafts_actor"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_answer_drafts_actor_updated", "bid_answer_drafts", ["actor_id", "updated_at"])

    op.create_table(
        "bid_answer_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question_round_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("answered_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answer_set_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["bid_analysis_runs.id"], name="fk_bid_answer_sets_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id", "question_round_id"], ["bid_question_rounds.run_id", "bid_question_rounds.id"], name="fk_bid_answer_sets_round_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_round_id", "manifest_id"], ["bid_question_rounds.id", "bid_question_rounds.manifest_id"], name="fk_bid_answer_sets_round_manifest", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_answer_sets"),
        sa.UniqueConstraint("question_round_id", "id", name="uq_bid_answer_sets_round_id"),
        sa.UniqueConstraint("question_round_id", "answer_set_hash", name="uq_bid_answer_sets_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_answer_sets_run_submitted", "bid_answer_sets", ["run_id", "submitted_at"])

    op.create_table(
        "bid_answers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question_round_id", sa.String(length=36), nullable=False),
        sa.Column("answer_set_id", sa.String(length=36), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("answer_status", sa.String(length=24), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("answer_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_bid_answers_version"),
        sa.CheckConstraint("answer_status IN ('answered', 'unknown')", name="ck_bid_answers_status"),
        sa.CheckConstraint("((answer_status = 'answered' AND value_json IS NOT NULL) OR (answer_status = 'unknown' AND value_json IS NULL))", name="ck_bid_answers_value"),
        sa.ForeignKeyConstraint(["question_round_id", "answer_set_id"], ["bid_answer_sets.question_round_id", "bid_answer_sets.id"], name="fk_bid_answers_answer_set", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_round_id", "question_id"], ["bid_questions.question_round_id", "bid_questions.id"], name="fk_bid_answers_question", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_answers"),
        sa.UniqueConstraint("question_id", "version", name="uq_bid_answers_question_version"),
        sa.UniqueConstraint("answer_set_id", "question_id", name="uq_bid_answers_set_question"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_answers_round", "bid_answers", ["question_round_id"])


def downgrade() -> None:
    op.drop_table("bid_answers")
    op.drop_table("bid_answer_sets")
    op.drop_table("bid_answer_drafts")
    op.drop_table("bid_questions")
    op.drop_table("bid_question_rounds")
    op.drop_table("bid_async_operations")
    op.drop_table("bid_checkpoints")
    op.drop_constraint("fk_bid_tasks_current_attempt", "bid_tasks", type_="foreignkey")
    op.drop_table("bid_task_attempts")
    op.drop_table("bid_task_dependencies")
    op.drop_table("bid_tasks")
    op.drop_table("bid_plan_revisions")
    op.drop_constraint("fk_bid_assessments_active_run", "bid_assessments", type_="foreignkey")
    op.drop_table("bid_analysis_runs")
    op.drop_constraint("uq_bid_assessment_scopes_owner_id", "bid_assessment_scopes", type_="unique")
