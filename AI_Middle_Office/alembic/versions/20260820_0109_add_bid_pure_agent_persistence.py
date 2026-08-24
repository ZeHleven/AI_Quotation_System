"""add isolated Pure Agent persistence

Revision ID: 20260820_0109
Revises: 20260818_0108
Create Date: 2026-08-20

This development-only revision must not be applied to ECS or any production
database before the Pure Agent receives explicit release authorization.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260820_0109"
down_revision: Union[str, None] = "20260818_0108"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _timestamps(*, updated: bool = False) -> tuple[sa.Column, ...]:
    columns: list[sa.Column] = [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)
    ]
    if updated:
        columns.append(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    return tuple(columns)


def upgrade() -> None:
    op.create_table(
        "bid_pa_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("tenant_ref", sa.String(length=160), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("next_message_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(updated=True),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_bid_pa_conversations_status"),
        sa.CheckConstraint("next_message_sequence >= 1", name="ck_bid_pa_conversations_sequence"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_bid_pa_conversations_owner", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_id"], ["bid_assessments.id"], name="fk_bid_pa_conversations_assessment", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_conversations"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_conversations_owner", "bid_pa_conversations", ["owner_id", "updated_at"])
    op.create_index("ix_bid_pa_conversations_assessment", "bid_pa_conversations", ["assessment_id", "updated_at"])

    op.create_table(
        "bid_pa_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("reply_to_message_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_by_ref", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence_no >= 1", name="ck_bid_pa_messages_sequence"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="ck_bid_pa_messages_role"),
        sa.ForeignKeyConstraint(["conversation_id"], ["bid_pa_conversations.id"], name="fk_bid_pa_messages_conversation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reply_to_message_id"], ["bid_pa_messages.id"], name="fk_bid_pa_messages_reply", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_messages"),
        sa.UniqueConstraint("conversation_id", "sequence_no", name="uq_bid_pa_messages_sequence"),
        sa.UniqueConstraint("conversation_id", "idempotency_key", name="uq_bid_pa_messages_idempotency"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_messages_conversation", "bid_pa_messages", ["conversation_id", "created_at"])

    op.create_table(
        "bid_pa_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("trigger_message_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("execution_mode", sa.String(length=16), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("goal_ref", sa.String(length=160), nullable=False),
        sa.Column("plan_ref", sa.String(length=160), nullable=True),
        sa.Column("active_slot_id", sa.String(length=36), nullable=True),
        sa.Column("active_checkpoint_id", sa.String(length=36), nullable=True),
        sa.Column("pending_phase", sa.String(length=32), nullable=True),
        sa.Column("validation_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("in_flight_action_id", sa.String(length=36), nullable=True),
        sa.Column("observation_refs_json", sa.JSON(), nullable=False),
        sa.Column("last_error_ref", sa.String(length=160), nullable=True),
        sa.Column("cancellation_fence_id", sa.String(length=36), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(updated=True),
        sa.CheckConstraint("status IN ('running', 'pending', 'completed', 'failed', 'cancelled')", name="ck_bid_pa_tasks_status"),
        sa.CheckConstraint("execution_mode IN ('direct', 'planned')", name="ck_bid_pa_tasks_mode"),
        sa.CheckConstraint("state_version >= 1", name="ck_bid_pa_tasks_state_version"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_pa_tasks_row_version"),
        sa.CheckConstraint("((status = 'pending' AND active_slot_id IS NOT NULL AND active_checkpoint_id IS NOT NULL AND pending_phase IS NOT NULL) OR (status <> 'pending' AND active_slot_id IS NULL AND active_checkpoint_id IS NULL AND pending_phase IS NULL AND validation_attempt_id IS NULL))", name="ck_bid_pa_tasks_pending_context"),
        sa.CheckConstraint("pending_phase IS NULL OR pending_phase IN ('waiting_input', 'validating_format', 'validating_business')", name="ck_bid_pa_tasks_pending_phase"),
        sa.CheckConstraint("((status IN ('completed', 'failed', 'cancelled') AND terminal_at IS NOT NULL) OR (status IN ('running', 'pending') AND terminal_at IS NULL))", name="ck_bid_pa_tasks_terminal_at"),
        sa.CheckConstraint("status <> 'failed' OR last_error_ref IS NOT NULL", name="ck_bid_pa_tasks_failed_error"),
        sa.CheckConstraint("status <> 'cancelled' OR cancellation_fence_id IS NOT NULL", name="ck_bid_pa_tasks_cancel_fence"),
        sa.ForeignKeyConstraint(["conversation_id"], ["bid_pa_conversations.id"], name="fk_bid_pa_tasks_conversation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["bid_pa_messages.id"], name="fk_bid_pa_tasks_trigger_message", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_bid_pa_tasks_owner", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_tasks"),
        sa.UniqueConstraint("conversation_id", "trigger_message_id", name="uq_bid_pa_tasks_trigger"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_tasks_conversation", "bid_pa_tasks", ["conversation_id", "created_at"])
    op.create_index("ix_bid_pa_tasks_status", "bid_pa_tasks", ["status", "updated_at"])

    op.create_table(
        "bid_pa_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("execution_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("effect_idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("result_ref", sa.String(length=160), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("sequence_no >= 1", name="ck_bid_pa_actions_sequence"),
        sa.CheckConstraint("status IN ('accepted', 'running', 'succeeded', 'failed', 'cancelled', 'ignored_late')", name="ck_bid_pa_actions_status"),
        sa.CheckConstraint("execution_kind IN ('direct', 'durable')", name="ck_bid_pa_actions_execution_kind"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_actions_task", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_actions"),
        sa.UniqueConstraint("task_id", "sequence_no", name="uq_bid_pa_actions_sequence"),
        sa.UniqueConstraint("task_id", "effect_idempotency_key", name="uq_bid_pa_actions_effect_key"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_actions_task_status", "bid_pa_actions", ["task_id", "status"])

    op.create_table(
        "bid_pa_plans",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("body_json", sa.JSON(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_plan_id", sa.String(length=160), nullable=True),
        sa.Column("context_snapshot_ref", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("plan_version >= 1", name="ck_bid_pa_plans_version"),
        sa.CheckConstraint("status IN ('active', 'superseded', 'completed', 'invalidated')", name="ck_bid_pa_plans_status"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_plans_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_plan_id"], ["bid_pa_plans.id"], name="fk_bid_pa_plans_supersedes", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_plans"),
        sa.UniqueConstraint("task_id", "plan_version", name="uq_bid_pa_plans_version"),
        sa.UniqueConstraint("task_id", "plan_hash", name="uq_bid_pa_plans_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_plans_task_status", "bid_pa_plans", ["task_id", "status"])

    op.create_table(
        "bid_pa_slots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("request_message", sa.Text(), nullable=False),
        sa.Column("input_model_ref", sa.String(length=160), nullable=False),
        sa.Column("business_validator_refs_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("candidate_input_ref", sa.String(length=160), nullable=True),
        sa.Column("resolved_value_ref", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('unresolved', 'resolved')", name="ck_bid_pa_slots_status"),
        sa.CheckConstraint("((status = 'resolved' AND resolved_value_ref IS NOT NULL) OR (status = 'unresolved' AND resolved_value_ref IS NULL))", name="ck_bid_pa_slots_resolution"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_slots_task", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_slots"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_slots_task_status", "bid_pa_slots", ["task_id", "status"])

    op.create_table(
        "bid_pa_effect_fences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("effect_key", sa.String(length=160), nullable=False),
        sa.Column("effect_type", sa.String(length=64), nullable=False),
        sa.Column("replay_policy", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_ref", sa.String(length=160), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_pa_effect_fences_token"),
        sa.CheckConstraint("status IN ('reserved', 'running', 'succeeded', 'failed', 'uncertain', 'cancelled', 'ignored_late')", name="ck_bid_pa_effect_fences_status"),
        sa.CheckConstraint("replay_policy IN ('safe_idempotent', 'reconcile_required', 'no_replay')", name="ck_bid_pa_effect_fences_replay"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_effect_fences_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["action_id"], ["bid_pa_actions.id"], name="fk_bid_pa_effect_fences_action", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_effect_fences"),
        sa.UniqueConstraint("task_id", "effect_key", name="uq_bid_pa_effect_fences_key"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_effect_fences_task_status", "bid_pa_effect_fences", ["task_id", "status"])

    op.create_table(
        "bid_pa_cancel_fences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("requested_by_ref", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state_version >= 1", name="ck_bid_pa_cancel_fences_version"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_cancel_fences_task", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_cancel_fences"),
        sa.UniqueConstraint("task_id", name="uq_bid_pa_cancel_fences_task"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "bid_pa_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("slot_id", sa.String(length=36), nullable=False),
        sa.Column("suspended_state_version", sa.Integer(), nullable=False),
        sa.Column("execution_mode", sa.String(length=16), nullable=False),
        sa.Column("context_snapshot_ref", sa.String(length=160), nullable=False),
        sa.Column("suspended_action_id", sa.String(length=36), nullable=False),
        sa.Column("effect_fence_id", sa.String(length=36), nullable=False),
        sa.Column("resume_token_hash", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recovery_lease_owner", sa.String(length=128), nullable=True),
        sa.Column("recovery_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'consumed', 'invalidated')", name="ck_bid_pa_checkpoints_status"),
        sa.CheckConstraint("execution_mode IN ('direct', 'planned')", name="ck_bid_pa_checkpoints_mode"),
        sa.CheckConstraint("suspended_state_version >= 1", name="ck_bid_pa_checkpoints_version"),
        sa.CheckConstraint("((status = 'consumed' AND consumed_at IS NOT NULL) OR (status <> 'consumed' AND consumed_at IS NULL))", name="ck_bid_pa_checkpoints_consumed"),
        sa.CheckConstraint("recovery_fencing_token >= 0", name="ck_bid_pa_checkpoints_recovery_token"),
        sa.CheckConstraint("((recovery_lease_owner IS NULL AND recovery_lease_until IS NULL) OR (recovery_lease_owner IS NOT NULL AND recovery_lease_until IS NOT NULL))", name="ck_bid_pa_checkpoints_recovery_lease"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_checkpoints_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["slot_id"], ["bid_pa_slots.id"], name="fk_bid_pa_checkpoints_slot", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["suspended_action_id"], ["bid_pa_actions.id"], name="fk_bid_pa_checkpoints_action", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["effect_fence_id"], ["bid_pa_effect_fences.id"], name="fk_bid_pa_checkpoints_effect", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_checkpoints"),
        sa.UniqueConstraint("resume_token_hash", name="uq_bid_pa_checkpoints_resume_token"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_checkpoints_task_status", "bid_pa_checkpoints", ["task_id", "status"])

    op.create_table(
        "bid_pa_slot_validations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("slot_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_message_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("candidate_json", sa.JSON(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("issues_json", sa.JSON(), nullable=True),
        sa.Column("issues_hash", sa.String(length=64), nullable=True),
        sa.Column("resolved_value_json", sa.JSON(), nullable=True),
        sa.Column("resolved_value_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("stage IN ('format_validation', 'business_validation')", name="ck_bid_pa_slot_validations_stage"),
        sa.CheckConstraint("status IN ('running', 'passed', 'failed')", name="ck_bid_pa_slot_validations_status"),
        sa.CheckConstraint("((status = 'running' AND completed_at IS NULL AND issues_json IS NULL AND resolved_value_json IS NULL) OR (status = 'passed' AND completed_at IS NOT NULL AND issues_json IS NULL AND resolved_value_json IS NOT NULL) OR (status = 'failed' AND completed_at IS NOT NULL AND issues_json IS NOT NULL AND resolved_value_json IS NULL))", name="ck_bid_pa_slot_validations_result"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_slot_validations_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["slot_id"], ["bid_pa_slots.id"], name="fk_bid_pa_slot_validations_slot", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_message_id"], ["bid_pa_messages.id"], name="fk_bid_pa_slot_validations_message", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_slot_validations"),
        sa.UniqueConstraint("slot_id", "idempotency_key", name="uq_bid_pa_slot_validations_idempotency"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_slot_validations_slot", "bid_pa_slot_validations", ["slot_id", "created_at"])

    op.create_table(
        "bid_pa_context_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("consumer", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("included_refs_json", sa.JSON(), nullable=False),
        sa.Column("excluded_refs_json", sa.JSON(), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state_version >= 1", name="ck_bid_pa_context_state_version"),
        sa.CheckConstraint("consumer IN ('intent', 'planner', 'main_agent')", name="ck_bid_pa_context_consumer"),
        sa.CheckConstraint("status IN ('ready', 'ready_with_limits', 'needs_narrowing', 'blocked_on_user', 'failed')", name="ck_bid_pa_context_status"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_context_task", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_context_snapshots"),
        sa.UniqueConstraint("task_id", "snapshot_hash", name="uq_bid_pa_context_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_context_task", "bid_pa_context_snapshots", ["task_id", "created_at"])

    op.create_table(
        "bid_pa_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("context_snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("call_key", sa.String(length=160), nullable=False),
        sa.Column("call_ref", sa.String(length=160), nullable=False),
        sa.Column("provider_tool_call_id", sa.String(length=160), nullable=False),
        sa.Column("model_turn_ref", sa.String(length=160), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("call_kind", sa.String(length=16), nullable=False),
        sa.Column("provider_binding_ref", sa.String(length=160), nullable=False),
        sa.Column("operation_name", sa.String(length=160), nullable=False),
        sa.Column("registry_snapshot_ref", sa.String(length=160), nullable=False),
        sa.Column("registry_snapshot_hash", sa.String(length=71), nullable=False),
        sa.Column("visible_tools_hash", sa.String(length=71), nullable=False),
        sa.Column("authorization_snapshot_ref", sa.String(length=160), nullable=False),
        sa.Column("guard_decisions_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_ref", sa.String(length=160), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("provider_receipt_ref", sa.String(length=256), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("call_kind IN ('model', 'tool')", name="ck_bid_pa_calls_kind"),
        sa.CheckConstraint("status IN ('reserved', 'running', 'succeeded', 'failed', 'uncertain', 'cancelled', 'ignored_late')", name="ck_bid_pa_calls_status"),
        sa.CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_bid_pa_calls_input_tokens"),
        sa.CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_bid_pa_calls_output_tokens"),
        sa.CheckConstraint("cost_micro_usd IS NULL OR cost_micro_usd >= 0", name="ck_bid_pa_calls_cost"),
        sa.CheckConstraint("state_version >= 1", name="ck_bid_pa_calls_state_version"),
        sa.CheckConstraint("sequence_no >= 1 AND sequence_no <= 64", name="ck_bid_pa_calls_sequence"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_calls_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["action_id"], ["bid_pa_actions.id"], name="fk_bid_pa_calls_action", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["context_snapshot_id"], ["bid_pa_context_snapshots.id"], name="fk_bid_pa_calls_context", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_calls"),
        sa.UniqueConstraint("task_id", "call_key", name="uq_bid_pa_calls_key"),
        sa.UniqueConstraint("task_id", "call_ref", name="uq_bid_pa_calls_ref"),
        sa.UniqueConstraint("task_id", "model_turn_ref", "provider_tool_call_id", name="uq_bid_pa_calls_provider_tool_call"),
        sa.UniqueConstraint("task_id", "model_turn_ref", "sequence_no", name="uq_bid_pa_calls_turn_sequence"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_calls_task_status", "bid_pa_calls", ["task_id", "status"])

    op.create_table(
        "bid_pa_budget_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("limit_amount", sa.BigInteger(), nullable=False),
        sa.Column("reserved_amount", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("actual_amount", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(updated=True),
        sa.CheckConstraint("limit_amount >= 0", name="ck_bid_pa_budget_limit"),
        sa.CheckConstraint("reserved_amount >= 0", name="ck_bid_pa_budget_reserved"),
        sa.CheckConstraint("actual_amount >= 0", name="ck_bid_pa_budget_actual"),
        sa.CheckConstraint("reserved_amount + actual_amount <= limit_amount", name="ck_bid_pa_budget_total"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_pa_budget_version"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_budget_task", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_budget_accounts"),
        sa.UniqueConstraint("task_id", "resource_type", name="uq_bid_pa_budget_resource"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "bid_pa_budget_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=True),
        sa.Column("entry_kind", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("reservation_ref", sa.String(length=160), nullable=True),
        sa.Column("reserved_after", sa.BigInteger(), nullable=False),
        sa.Column("actual_after", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("entry_kind IN ('reserve', 'settle', 'release', 'charge')", name="ck_bid_pa_budget_entries_kind"),
        sa.CheckConstraint("amount >= 0", name="ck_bid_pa_budget_entries_amount"),
        sa.CheckConstraint("reserved_after >= 0 AND actual_after >= 0", name="ck_bid_pa_budget_entries_balances"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_budget_entries_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["bid_pa_budget_accounts.id"], name="fk_bid_pa_budget_entries_account", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["action_id"], ["bid_pa_actions.id"], name="fk_bid_pa_budget_entries_action", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_budget_entries"),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_bid_pa_budget_entries_idempotency"),
        sa.UniqueConstraint("account_id", "reservation_ref", name="uq_bid_pa_budget_entries_reservation"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_budget_entries_task", "bid_pa_budget_entries", ["task_id", "created_at"])

    op.create_table(
        "bid_pa_responses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("context_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("draft_json", sa.JSON(), nullable=False),
        sa.Column("draft_hash", sa.String(length=64), nullable=False),
        sa.Column("rendered_message_id", sa.String(length=36), nullable=True),
        sa.Column("supersedes_response_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'committed', 'rejected', 'stale', 'superseded')", name="ck_bid_pa_responses_status"),
        sa.CheckConstraint("((status = 'committed' AND rendered_message_id IS NOT NULL AND committed_at IS NOT NULL) OR status <> 'committed')", name="ck_bid_pa_responses_committed"),
        sa.ForeignKeyConstraint(["conversation_id"], ["bid_pa_conversations.id"], name="fk_bid_pa_responses_conversation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_responses_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["context_snapshot_id"], ["bid_pa_context_snapshots.id"], name="fk_bid_pa_responses_context", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rendered_message_id"], ["bid_pa_messages.id"], name="fk_bid_pa_responses_message", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_response_id"], ["bid_pa_responses.id"], name="fk_bid_pa_responses_supersedes", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_responses"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_responses_task", "bid_pa_responses", ["task_id", "created_at"])

    op.create_table(
        "bid_pa_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("state_version_before", sa.Integer(), nullable=False),
        sa.Column("state_version_after", sa.Integer(), nullable=False),
        sa.Column("status_before", sa.String(length=16), nullable=False),
        sa.Column("status_after", sa.String(length=16), nullable=False),
        sa.Column("effect_idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("state_after_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state_version_before >= 1 AND state_version_after = state_version_before + 1", name="ck_bid_pa_events_state_version"),
        sa.CheckConstraint("status_before IN ('running', 'pending', 'completed', 'failed', 'cancelled')", name="ck_bid_pa_events_status_before"),
        sa.CheckConstraint("status_after IN ('running', 'pending', 'completed', 'failed', 'cancelled')", name="ck_bid_pa_events_status_after"),
        sa.ForeignKeyConstraint(["task_id"], ["bid_pa_tasks.id"], name="fk_bid_pa_events_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["action_id"], ["bid_pa_actions.id"], name="fk_bid_pa_events_action", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_pa_events"),
        sa.UniqueConstraint("event_id", name="uq_bid_pa_events_event_id"),
        sa.UniqueConstraint("task_id", "state_version_after", name="uq_bid_pa_events_state_version"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_pa_events_task_created", "bid_pa_events", ["task_id", "created_at"])


TABLES_IN_DROP_ORDER = (
    "bid_pa_events",
    "bid_pa_responses",
    "bid_pa_budget_entries",
    "bid_pa_budget_accounts",
    "bid_pa_calls",
    "bid_pa_context_snapshots",
    "bid_pa_slot_validations",
    "bid_pa_checkpoints",
    "bid_pa_cancel_fences",
    "bid_pa_effect_fences",
    "bid_pa_slots",
    "bid_pa_plans",
    "bid_pa_actions",
    "bid_pa_tasks",
    "bid_pa_messages",
    "bid_pa_conversations",
)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0109 guarded downgrade requires an online local development database"
        )
    bind = op.get_bind()
    for table_name in TABLES_IN_DROP_ORDER:
        count = int(
            bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
        )
        if count:
            raise RuntimeError(
                "0109 downgrade would erase Pure Agent development persistence"
            )
    for table_name in TABLES_IN_DROP_ORDER:
        op.drop_table(table_name)
