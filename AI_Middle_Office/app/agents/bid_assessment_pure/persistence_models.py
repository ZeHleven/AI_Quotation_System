"""Isolated SQLAlchemy persistence models for the Pure Agent runtime.

The module is intentionally not imported by ``app.models.registry`` in B02.
Import it only from the Pure Agent repository or isolated migration tooling.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

TASK_STATUSES = ("running", "pending", "completed", "failed", "cancelled")
EXECUTION_MODES = ("direct", "planned")
PENDING_PHASES = ("waiting_input", "validating_format", "validating_business")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidPureAgentConversation(Base):
    __tablename__ = "bid_pa_conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_bid_pa_conversations_status",
        ),
        CheckConstraint(
            "next_message_sequence >= 1",
            name="ck_bid_pa_conversations_sequence",
        ),
        ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_bid_pa_conversations_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_pa_conversations_assessment",
            ondelete="RESTRICT",
        ),
        Index("ix_bid_pa_conversations_owner", "owner_id", "updated_at"),
        Index("ix_bid_pa_conversations_assessment", "assessment_id", "updated_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    owner_id = Column(Integer, nullable=False)
    tenant_ref = Column(String(160), nullable=False)
    assessment_id = Column(String(36), nullable=True)
    title = Column(String(255), nullable=True)
    status = Column(String(16), nullable=False, default="active", server_default="active")
    next_message_sequence = Column(Integer, nullable=False, default=1, server_default="1")
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidPureAgentMessage(Base):
    __tablename__ = "bid_pa_messages"
    __table_args__ = (
        CheckConstraint("sequence_no >= 1", name="ck_bid_pa_messages_sequence"),
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_bid_pa_messages_role",
        ),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["bid_pa_conversations.id"],
            name="fk_bid_pa_messages_conversation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reply_to_message_id"],
            ["bid_pa_messages.id"],
            name="fk_bid_pa_messages_reply",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_bid_pa_messages_sequence",
        ),
        UniqueConstraint(
            "conversation_id",
            "idempotency_key",
            name="uq_bid_pa_messages_idempotency",
        ),
        Index("ix_bid_pa_messages_conversation", "conversation_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    conversation_id = Column(String(36), nullable=False)
    sequence_no = Column(Integer, nullable=False)
    role = Column(String(16), nullable=False)
    message_type = Column(String(64), nullable=False)
    content_json = Column(JSON, nullable=False)
    content_hash = Column(String(64), nullable=False)
    reply_to_message_id = Column(String(36), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    created_by_ref = Column(String(160), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentTask(Base):
    __tablename__ = "bid_pa_tasks"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(TASK_STATUSES)})",
            name="ck_bid_pa_tasks_status",
        ),
        CheckConstraint(
            f"execution_mode IN ({_in_values(EXECUTION_MODES)})",
            name="ck_bid_pa_tasks_mode",
        ),
        CheckConstraint("state_version >= 1", name="ck_bid_pa_tasks_state_version"),
        CheckConstraint("row_version >= 1", name="ck_bid_pa_tasks_row_version"),
        CheckConstraint(
            "((status = 'pending' AND active_slot_id IS NOT NULL "
            "AND active_checkpoint_id IS NOT NULL AND pending_phase IS NOT NULL) "
            "OR (status <> 'pending' AND active_slot_id IS NULL "
            "AND active_checkpoint_id IS NULL AND pending_phase IS NULL "
            "AND validation_attempt_id IS NULL))",
            name="ck_bid_pa_tasks_pending_context",
        ),
        CheckConstraint(
            "pending_phase IS NULL OR pending_phase IN "
            "('waiting_input', 'validating_format', 'validating_business')",
            name="ck_bid_pa_tasks_pending_phase",
        ),
        CheckConstraint(
            "((status IN ('completed', 'failed', 'cancelled') AND terminal_at IS NOT NULL) "
            "OR (status IN ('running', 'pending') AND terminal_at IS NULL))",
            name="ck_bid_pa_tasks_terminal_at",
        ),
        CheckConstraint(
            "status <> 'failed' OR last_error_ref IS NOT NULL",
            name="ck_bid_pa_tasks_failed_error",
        ),
        CheckConstraint(
            "status <> 'cancelled' OR cancellation_fence_id IS NOT NULL",
            name="ck_bid_pa_tasks_cancel_fence",
        ),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["bid_pa_conversations.id"],
            name="fk_bid_pa_tasks_conversation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trigger_message_id"],
            ["bid_pa_messages.id"],
            name="fk_bid_pa_tasks_trigger_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_bid_pa_tasks_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "conversation_id",
            "trigger_message_id",
            name="uq_bid_pa_tasks_trigger",
        ),
        Index("ix_bid_pa_tasks_conversation", "conversation_id", "created_at"),
        Index("ix_bid_pa_tasks_status", "status", "updated_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    conversation_id = Column(String(36), nullable=False)
    trigger_message_id = Column(String(36), nullable=False)
    owner_id = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)
    execution_mode = Column(String(16), nullable=False)
    state_version = Column(Integer, nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    goal_ref = Column(String(160), nullable=False)
    plan_ref = Column(String(160), nullable=True)
    active_slot_id = Column(String(36), nullable=True)
    active_checkpoint_id = Column(String(36), nullable=True)
    pending_phase = Column(String(32), nullable=True)
    validation_attempt_id = Column(String(36), nullable=True)
    in_flight_action_id = Column(String(36), nullable=True)
    observation_refs_json = Column(JSON, nullable=False, default=list)
    last_error_ref = Column(String(160), nullable=True)
    cancellation_fence_id = Column(String(36), nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentAction(Base):
    __tablename__ = "bid_pa_actions"
    __table_args__ = (
        CheckConstraint("sequence_no >= 1", name="ck_bid_pa_actions_sequence"),
        CheckConstraint(
            "status IN ('accepted', 'running', 'succeeded', 'failed', "
            "'cancelled', 'ignored_late')",
            name="ck_bid_pa_actions_status",
        ),
        CheckConstraint(
            "execution_kind IN ('direct', 'durable')",
            name="ck_bid_pa_actions_execution_kind",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_actions_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "sequence_no", name="uq_bid_pa_actions_sequence"),
        UniqueConstraint(
            "task_id",
            "effect_idempotency_key",
            name="uq_bid_pa_actions_effect_key",
        ),
        Index("ix_bid_pa_actions_task_status", "task_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    sequence_no = Column(Integer, nullable=False)
    action_type = Column(String(80), nullable=False)
    execution_kind = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False)
    arguments_json = Column(JSON, nullable=False)
    arguments_hash = Column(String(64), nullable=False)
    effect_idempotency_key = Column(String(160), nullable=True)
    result_ref = Column(String(160), nullable=True)
    result_hash = Column(String(64), nullable=True)
    error_code = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class BidPureAgentPlan(Base):
    __tablename__ = "bid_pa_plans"
    __table_args__ = (
        CheckConstraint("plan_version >= 1", name="ck_bid_pa_plans_version"),
        CheckConstraint(
            "status IN ('active', 'superseded', 'completed', 'invalidated')",
            name="ck_bid_pa_plans_status",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_plans_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_plan_id"],
            ["bid_pa_plans.id"],
            name="fk_bid_pa_plans_supersedes",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "plan_version", name="uq_bid_pa_plans_version"),
        UniqueConstraint("task_id", "plan_hash", name="uq_bid_pa_plans_hash"),
        Index("ix_bid_pa_plans_task_status", "task_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(160), primary_key=True)
    task_id = Column(String(36), nullable=False)
    plan_version = Column(Integer, nullable=False)
    schema_version = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False)
    body_json = Column(JSON, nullable=False)
    plan_hash = Column(String(64), nullable=False)
    supersedes_plan_id = Column(String(160), nullable=True)
    context_snapshot_ref = Column(String(160), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentSlot(Base):
    __tablename__ = "bid_pa_slots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('unresolved', 'resolved')",
            name="ck_bid_pa_slots_status",
        ),
        CheckConstraint(
            "((status = 'resolved' AND resolved_value_ref IS NOT NULL) "
            "OR (status = 'unresolved' AND resolved_value_ref IS NULL))",
            name="ck_bid_pa_slots_resolution",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_slots_task",
            ondelete="RESTRICT",
        ),
        Index("ix_bid_pa_slots_task_status", "task_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    name = Column(String(128), nullable=False)
    request_message = Column(Text, nullable=False)
    input_model_ref = Column(String(160), nullable=False)
    business_validator_refs_json = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False)
    candidate_input_ref = Column(String(160), nullable=True)
    resolved_value_ref = Column(String(160), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class BidPureAgentEffectFence(Base):
    __tablename__ = "bid_pa_effect_fences"
    __table_args__ = (
        CheckConstraint("fencing_token >= 1", name="ck_bid_pa_effect_fences_token"),
        CheckConstraint(
            "status IN ('reserved', 'running', 'succeeded', 'failed', "
            "'uncertain', 'cancelled', 'ignored_late')",
            name="ck_bid_pa_effect_fences_status",
        ),
        CheckConstraint(
            "replay_policy IN ('safe_idempotent', 'reconcile_required', 'no_replay')",
            name="ck_bid_pa_effect_fences_replay",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_effect_fences_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["action_id"],
            ["bid_pa_actions.id"],
            name="fk_bid_pa_effect_fences_action",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "effect_key", name="uq_bid_pa_effect_fences_key"),
        Index("ix_bid_pa_effect_fences_task_status", "task_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    action_id = Column(String(36), nullable=False)
    effect_key = Column(String(160), nullable=False)
    effect_type = Column(String(64), nullable=False)
    replay_policy = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False)
    fencing_token = Column(BigInteger, nullable=False)
    request_hash = Column(String(64), nullable=False)
    result_ref = Column(String(160), nullable=True)
    result_hash = Column(String(64), nullable=True)
    error_code = Column(String(100), nullable=True)
    reserved_at = Column(DateTime(timezone=True), nullable=False)
    settled_at = Column(DateTime(timezone=True), nullable=True)


class BidPureAgentCancellationFence(Base):
    __tablename__ = "bid_pa_cancel_fences"
    __table_args__ = (
        CheckConstraint("state_version >= 1", name="ck_bid_pa_cancel_fences_version"),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_cancel_fences_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", name="uq_bid_pa_cancel_fences_task"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    state_version = Column(Integer, nullable=False)
    requested_by_ref = Column(String(160), nullable=False)
    reason = Column(String(1000), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentCheckpoint(Base):
    __tablename__ = "bid_pa_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'consumed', 'invalidated')",
            name="ck_bid_pa_checkpoints_status",
        ),
        CheckConstraint(
            f"execution_mode IN ({_in_values(EXECUTION_MODES)})",
            name="ck_bid_pa_checkpoints_mode",
        ),
        CheckConstraint(
            "suspended_state_version >= 1",
            name="ck_bid_pa_checkpoints_version",
        ),
        CheckConstraint(
            "((status = 'consumed' AND consumed_at IS NOT NULL) "
            "OR (status <> 'consumed' AND consumed_at IS NULL))",
            name="ck_bid_pa_checkpoints_consumed",
        ),
        CheckConstraint(
            "recovery_fencing_token >= 0",
            name="ck_bid_pa_checkpoints_recovery_token",
        ),
        CheckConstraint(
            "((recovery_lease_owner IS NULL AND recovery_lease_until IS NULL) "
            "OR (recovery_lease_owner IS NOT NULL AND recovery_lease_until IS NOT NULL))",
            name="ck_bid_pa_checkpoints_recovery_lease",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_checkpoints_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["slot_id"],
            ["bid_pa_slots.id"],
            name="fk_bid_pa_checkpoints_slot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["suspended_action_id"],
            ["bid_pa_actions.id"],
            name="fk_bid_pa_checkpoints_action",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["effect_fence_id"],
            ["bid_pa_effect_fences.id"],
            name="fk_bid_pa_checkpoints_effect",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("resume_token_hash", name="uq_bid_pa_checkpoints_resume_token"),
        Index("ix_bid_pa_checkpoints_task_status", "task_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    slot_id = Column(String(36), nullable=False)
    suspended_state_version = Column(Integer, nullable=False)
    execution_mode = Column(String(16), nullable=False)
    context_snapshot_ref = Column(String(160), nullable=False)
    suspended_action_id = Column(String(36), nullable=False)
    effect_fence_id = Column(String(36), nullable=False)
    resume_token_hash = Column(String(71), nullable=False)
    status = Column(String(16), nullable=False)
    recovery_lease_owner = Column(String(128), nullable=True)
    recovery_lease_until = Column(DateTime(timezone=True), nullable=True)
    recovery_fencing_token = Column(BigInteger, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)


class BidPureAgentSlotValidation(Base):
    __tablename__ = "bid_pa_slot_validations"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('format_validation', 'business_validation')",
            name="ck_bid_pa_slot_validations_stage",
        ),
        CheckConstraint(
            "status IN ('running', 'passed', 'failed')",
            name="ck_bid_pa_slot_validations_status",
        ),
        CheckConstraint(
            "((status = 'running' AND completed_at IS NULL AND issues_json IS NULL "
            "AND resolved_value_json IS NULL) OR (status = 'passed' "
            "AND completed_at IS NOT NULL AND issues_json IS NULL "
            "AND resolved_value_json IS NOT NULL) OR (status = 'failed' "
            "AND completed_at IS NOT NULL AND issues_json IS NOT NULL "
            "AND resolved_value_json IS NULL))",
            name="ck_bid_pa_slot_validations_result",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_slot_validations_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["slot_id"],
            ["bid_pa_slots.id"],
            name="fk_bid_pa_slot_validations_slot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_message_id"],
            ["bid_pa_messages.id"],
            name="fk_bid_pa_slot_validations_message",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "slot_id",
            "idempotency_key",
            name="uq_bid_pa_slot_validations_idempotency",
        ),
        Index("ix_bid_pa_slot_validations_slot", "slot_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    slot_id = Column(String(36), nullable=False)
    candidate_message_id = Column(String(36), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    stage = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False)
    candidate_json = Column(JSON, nullable=False)
    candidate_hash = Column(String(64), nullable=False)
    # SQL NULL is part of the validation-state constraint.  The generic JSON
    # type otherwise persists Python None as the JSON literal ``null``.
    issues_json = Column(JSON(none_as_null=True), nullable=True)
    issues_hash = Column(String(64), nullable=True)
    resolved_value_json = Column(JSON(none_as_null=True), nullable=True)
    resolved_value_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class BidPureAgentContextSnapshot(Base):
    __tablename__ = "bid_pa_context_snapshots"
    __table_args__ = (
        CheckConstraint("state_version >= 1", name="ck_bid_pa_context_state_version"),
        CheckConstraint(
            "consumer IN ('intent', 'planner', 'main_agent')",
            name="ck_bid_pa_context_consumer",
        ),
        CheckConstraint(
            "status IN ('ready', 'ready_with_limits', 'needs_narrowing', "
            "'blocked_on_user', 'failed')",
            name="ck_bid_pa_context_status",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_context_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "snapshot_hash", name="uq_bid_pa_context_hash"),
        Index("ix_bid_pa_context_task", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    state_version = Column(Integer, nullable=False)
    consumer = Column(String(24), nullable=False)
    status = Column(String(32), nullable=False)
    snapshot_json = Column(JSON, nullable=False)
    included_refs_json = Column(JSON, nullable=False)
    excluded_refs_json = Column(JSON, nullable=False)
    estimated_input_tokens = Column(Integer, nullable=True)
    snapshot_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentObservationArtifact(Base):
    __tablename__ = "bid_pa_observation_artifacts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('control_decision', 'plan_revision', 'tool_result', "
            "'slot_request', 'answer_draft', 'runtime_limit', 'error')",
            name="ck_bid_pa_observation_artifacts_kind",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'no_result', 'degraded', 'rejected', 'failed')",
            name="ck_bid_pa_observation_artifacts_status",
        ),
        CheckConstraint(
            "state_version >= 1",
            name="ck_bid_pa_observation_artifacts_state_version",
        ),
        CheckConstraint(
            "action_sequence >= 1",
            name="ck_bid_pa_observation_artifacts_sequence",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_observation_artifacts_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["action_id"],
            ["bid_pa_actions.id"],
            name="fk_bid_pa_observation_artifacts_action",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["bid_pa_context_snapshots.id"],
            name="fk_bid_pa_observation_artifacts_context",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "action_id",
            name="uq_bid_pa_observation_artifacts_action",
        ),
        Index(
            "ix_bid_pa_observation_artifacts_task_sequence",
            "task_id",
            "action_sequence",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(160), primary_key=True)
    task_id = Column(String(36), nullable=False)
    action_id = Column(String(36), nullable=False)
    context_snapshot_id = Column(String(36), nullable=False)
    state_version = Column(Integer, nullable=False)
    action_sequence = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False)
    observation_json = Column(JSON, nullable=False)
    observation_hash = Column(String(64), nullable=False)
    artifact_ref = Column(String(160), nullable=False)
    artifact_hash = Column(String(64), nullable=False)
    artifact_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentCall(Base):
    __tablename__ = "bid_pa_calls"
    __table_args__ = (
        CheckConstraint(
            "call_kind IN ('model', 'tool')",
            name="ck_bid_pa_calls_kind",
        ),
        CheckConstraint(
            "status IN ('reserved', 'running', 'succeeded', 'failed', "
            "'uncertain', 'cancelled', 'ignored_late')",
            name="ck_bid_pa_calls_status",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_bid_pa_calls_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_bid_pa_calls_output_tokens",
        ),
        CheckConstraint(
            "cost_micro_usd IS NULL OR cost_micro_usd >= 0",
            name="ck_bid_pa_calls_cost",
        ),
        CheckConstraint(
            "state_version >= 1",
            name="ck_bid_pa_calls_state_version",
        ),
        CheckConstraint(
            "sequence_no >= 1 AND sequence_no <= 64",
            name="ck_bid_pa_calls_sequence",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_calls_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["action_id"],
            ["bid_pa_actions.id"],
            name="fk_bid_pa_calls_action",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["bid_pa_context_snapshots.id"],
            name="fk_bid_pa_calls_context",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "call_key", name="uq_bid_pa_calls_key"),
        UniqueConstraint("task_id", "call_ref", name="uq_bid_pa_calls_ref"),
        UniqueConstraint(
            "task_id",
            "model_turn_ref",
            "provider_tool_call_id",
            name="uq_bid_pa_calls_provider_tool_call",
        ),
        UniqueConstraint(
            "task_id",
            "model_turn_ref",
            "sequence_no",
            name="uq_bid_pa_calls_turn_sequence",
        ),
        Index("ix_bid_pa_calls_task_status", "task_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    action_id = Column(String(36), nullable=False)
    context_snapshot_id = Column(String(36), nullable=True)
    call_key = Column(String(160), nullable=False)
    call_ref = Column(String(160), nullable=False)
    provider_tool_call_id = Column(String(160), nullable=False)
    model_turn_ref = Column(String(160), nullable=False)
    sequence_no = Column(Integer, nullable=False)
    state_version = Column(Integer, nullable=False)
    call_kind = Column(String(16), nullable=False)
    provider_binding_ref = Column(String(160), nullable=False)
    operation_name = Column(String(160), nullable=False)
    registry_snapshot_ref = Column(String(160), nullable=False)
    registry_snapshot_hash = Column(String(71), nullable=False)
    visible_tools_hash = Column(String(71), nullable=False)
    authorization_snapshot_ref = Column(String(160), nullable=False)
    guard_decisions_json = Column(JSON, nullable=False)
    status = Column(String(24), nullable=False)
    input_hash = Column(String(64), nullable=False)
    input_json = Column(JSON(none_as_null=True), nullable=True)
    output_ref = Column(String(160), nullable=True)
    output_hash = Column(String(64), nullable=True)
    output_json = Column(JSON, nullable=True)
    provider_receipt_ref = Column(String(256), nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cost_micro_usd = Column(BigInteger, nullable=True)
    error_code = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class BidPureAgentBudgetAccount(Base):
    __tablename__ = "bid_pa_budget_accounts"
    __table_args__ = (
        CheckConstraint("limit_amount >= 0", name="ck_bid_pa_budget_limit"),
        CheckConstraint("reserved_amount >= 0", name="ck_bid_pa_budget_reserved"),
        CheckConstraint("actual_amount >= 0", name="ck_bid_pa_budget_actual"),
        CheckConstraint(
            "reserved_amount + actual_amount <= limit_amount",
            name="ck_bid_pa_budget_total",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_pa_budget_version"),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_budget_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "resource_type", name="uq_bid_pa_budget_resource"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    resource_type = Column(String(64), nullable=False)
    unit = Column(String(32), nullable=False)
    limit_amount = Column(BigInteger, nullable=False)
    reserved_amount = Column(BigInteger, nullable=False, default=0, server_default="0")
    actual_amount = Column(BigInteger, nullable=False, default=0, server_default="0")
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentBudgetEntry(Base):
    __tablename__ = "bid_pa_budget_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_kind IN ('reserve', 'settle', 'release', 'charge')",
            name="ck_bid_pa_budget_entries_kind",
        ),
        CheckConstraint("amount >= 0", name="ck_bid_pa_budget_entries_amount"),
        CheckConstraint(
            "reserved_after >= 0 AND actual_after >= 0",
            name="ck_bid_pa_budget_entries_balances",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_budget_entries_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["account_id"],
            ["bid_pa_budget_accounts.id"],
            name="fk_bid_pa_budget_entries_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["action_id"],
            ["bid_pa_actions.id"],
            name="fk_bid_pa_budget_entries_action",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "account_id",
            "idempotency_key",
            name="uq_bid_pa_budget_entries_idempotency",
        ),
        UniqueConstraint(
            "account_id",
            "reservation_ref",
            name="uq_bid_pa_budget_entries_reservation",
        ),
        Index("ix_bid_pa_budget_entries_task", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    account_id = Column(String(36), nullable=False)
    action_id = Column(String(36), nullable=True)
    entry_kind = Column(String(16), nullable=False)
    amount = Column(BigInteger, nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    reservation_ref = Column(String(160), nullable=True)
    reserved_after = Column(BigInteger, nullable=False)
    actual_after = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class BidPureAgentResponse(Base):
    __tablename__ = "bid_pa_responses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'committed', 'rejected', 'stale', 'superseded')",
            name="ck_bid_pa_responses_status",
        ),
        CheckConstraint(
            "((status = 'committed' AND rendered_message_id IS NOT NULL "
            "AND committed_at IS NOT NULL) OR status <> 'committed')",
            name="ck_bid_pa_responses_committed",
        ),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["bid_pa_conversations.id"],
            name="fk_bid_pa_responses_conversation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_responses_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["bid_pa_context_snapshots.id"],
            name="fk_bid_pa_responses_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["rendered_message_id"],
            ["bid_pa_messages.id"],
            name="fk_bid_pa_responses_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_response_id"],
            ["bid_pa_responses.id"],
            name="fk_bid_pa_responses_supersedes",
            ondelete="RESTRICT",
        ),
        Index("ix_bid_pa_responses_task", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    conversation_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    context_snapshot_id = Column(String(36), nullable=False)
    status = Column(String(16), nullable=False)
    draft_json = Column(JSON, nullable=False)
    draft_hash = Column(String(64), nullable=False)
    rendered_message_id = Column(String(36), nullable=True)
    supersedes_response_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    committed_at = Column(DateTime(timezone=True), nullable=True)


class BidPureAgentEvent(Base):
    __tablename__ = "bid_pa_events"
    __table_args__ = (
        CheckConstraint(
            "state_version_before >= 1 AND state_version_after = state_version_before + 1",
            name="ck_bid_pa_events_state_version",
        ),
        CheckConstraint(
            f"status_before IN ({_in_values(TASK_STATUSES)})",
            name="ck_bid_pa_events_status_before",
        ),
        CheckConstraint(
            f"status_after IN ({_in_values(TASK_STATUSES)})",
            name="ck_bid_pa_events_status_after",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_pa_tasks.id"],
            name="fk_bid_pa_events_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["action_id"],
            ["bid_pa_actions.id"],
            name="fk_bid_pa_events_action",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("event_id", name="uq_bid_pa_events_event_id"),
        UniqueConstraint(
            "task_id",
            "state_version_after",
            name="uq_bid_pa_events_state_version",
        ),
        Index("ix_bid_pa_events_task_created", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    event_id = Column(String(160), nullable=False)
    task_id = Column(String(36), nullable=False)
    action_id = Column(String(36), nullable=True)
    event_type = Column(String(100), nullable=False)
    state_version_before = Column(Integer, nullable=False)
    state_version_after = Column(Integer, nullable=False)
    status_before = Column(String(16), nullable=False)
    status_after = Column(String(16), nullable=False)
    effect_idempotency_key = Column(String(160), nullable=True)
    payload_json = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    state_after_json = Column(JSON, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
