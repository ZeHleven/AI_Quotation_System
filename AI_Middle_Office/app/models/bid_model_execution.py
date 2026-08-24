"""Phase 4A-2 authoritative model-call, provider-attempt, and result rows."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)

from app.core.database import Base


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}
MODEL_CALL_STATES = (
    "accepted",
    "leased",
    "sending",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
    "uncertain",
    "dead_letter",
)
MODEL_CALL_ATTEMPT_STATES = (
    "leased",
    "sending",
    "succeeded",
    "failed",
    "lease_expired",
    "cancelled",
    "uncertain",
)
MODEL_REPLAY_POLICIES = ("safe_idempotent", "reconcile_required", "no_replay")
MODEL_RESULT_STORAGE_KINDS = ("inline", "external")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidModelCall(Base):
    __tablename__ = "bid_model_calls"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(MODEL_CALL_STATES)})",
            name="ck_bid_model_calls_status",
        ),
        CheckConstraint(
            f"replay_policy IN ({_in_values(MODEL_REPLAY_POLICIES)})",
            name="ck_bid_model_calls_replay_policy",
        ),
        CheckConstraint("action_seq >= 1", name="ck_bid_model_calls_action_seq"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_model_calls_fencing"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_bid_model_calls_attempts",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_model_calls_row_version"),
        CheckConstraint(
            "reserved_input_tokens >= 0 AND reserved_output_tokens >= 0 "
            "AND actual_input_tokens >= 0 AND actual_output_tokens >= 0 "
            "AND reserved_cost_microunits >= 0 AND actual_cost_microunits >= 0",
            name="ck_bid_model_calls_tokens",
        ),
        CheckConstraint(
            "((status IN ('leased', 'sending') AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('accepted', 'retry_wait') AND lease_owner IS NULL "
            "AND lease_until IS NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled', 'uncertain', 'dead_letter') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND completed_at IS NOT NULL))",
            name="ck_bid_model_calls_lifecycle",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_model_calls_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_model_calls_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_model_calls_task_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "context_manifest_id"],
            ["bid_context_manifests.task_attempt_id", "bid_context_manifests.id"],
            name="fk_bid_model_calls_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "checkpoint_id"],
            ["bid_checkpoints.task_attempt_id", "bid_checkpoints.id"],
            name="fk_bid_model_calls_checkpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "task_attempt_id", "async_operation_id"],
            [
                "bid_async_operations.task_id",
                "bid_async_operations.task_attempt_id",
                "bid_async_operations.id",
            ],
            name="fk_bid_model_calls_operation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "action_seq", name="uq_bid_model_calls_action"),
        UniqueConstraint("task_id", "idempotency_key", name="uq_bid_model_calls_idempotency"),
        UniqueConstraint("async_operation_id", name="uq_bid_model_calls_operation"),
        UniqueConstraint("task_id", "id", name="uq_bid_model_calls_task_id"),
        Index("ix_bid_model_calls_ready", "status", "available_at", "lease_until"),
        Index("ix_bid_model_calls_run", "run_id", "created_at"),
        Index("ix_bid_model_calls_attempt", "task_attempt_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    task_attempt_id = Column(String(36), nullable=False)
    checkpoint_id = Column(String(36), nullable=False)
    context_manifest_id = Column(String(36), nullable=False)
    async_operation_id = Column(String(36), nullable=False)
    model_profile_version_id = Column(
        String(36),
        ForeignKey("bid_model_profile_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    prompt_bundle_id = Column(
        String(36), ForeignKey("bid_prompt_bundles.id", ondelete="RESTRICT"), nullable=False
    )
    action_seq = Column(Integer, nullable=False)
    fencing_token = Column(BigInteger, nullable=False)
    logical_role = Column(String(32), nullable=False)
    provider_ref = Column(String(128), nullable=False)
    model_ref = Column(String(128), nullable=False)
    prompt_role = Column(String(128), nullable=False)
    action_schema = Column(String(128), nullable=False)
    replay_policy = Column(String(32), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    request_envelope_json = Column(JSON, nullable=False)
    request_hash = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="accepted", server_default="accepted")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=1, server_default="1")
    lease_owner = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    available_at = Column(DateTime(timezone=True), nullable=False)
    timeout_at = Column(DateTime(timezone=True), nullable=False)
    reserved_input_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    reserved_output_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    actual_input_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    actual_output_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    reserved_cost_microunits = Column(BigInteger, nullable=False, default=0, server_default="0")
    actual_cost_microunits = Column(BigInteger, nullable=False, default=0, server_default="0")
    last_error_code = Column(String(100), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidModelCallAttempt(Base):
    __tablename__ = "bid_model_call_attempts"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(MODEL_CALL_ATTEMPT_STATES)})",
            name="ck_bid_model_call_attempts_status",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_bid_model_call_attempts_number"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_model_call_attempts_fencing"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bid_model_call_attempts_time_order",
        ),
        CheckConstraint(
            "((status IN ('leased', 'sending') AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'lease_expired', 'cancelled', 'uncertain') "
            "AND finished_at IS NOT NULL))",
            name="ck_bid_model_call_attempts_lifecycle",
        ),
        UniqueConstraint(
            "model_call_id", "attempt_no", name="uq_bid_model_call_attempts_number"
        ),
        UniqueConstraint(
            "model_call_id", "fencing_token", name="uq_bid_model_call_attempts_fencing"
        ),
        UniqueConstraint(
            "model_call_id", "id", name="uq_bid_model_call_attempts_call_id"
        ),
        UniqueConstraint("execution_key", name="uq_bid_model_call_attempts_execution_key"),
        UniqueConstraint(
            "provider_request_id", name="uq_bid_model_call_attempts_provider_request"
        ),
        Index("ix_bid_model_call_attempts_lease", "status", "lease_until"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    model_call_id = Column(
        String(36), ForeignKey("bid_model_calls.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_no = Column(Integer, nullable=False)
    fencing_token = Column(BigInteger, nullable=False)
    worker_id = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False)
    execution_key = Column(String(128), nullable=False)
    provider_request_id = Column(String(191), nullable=False)
    provider_receipt_id = Column(String(191), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    heartbeat_at = Column(DateTime(timezone=True), nullable=False)
    send_started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    outcome_hash = Column(String(64), nullable=True)
    error_code = Column(String(100), nullable=True)
    detail_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidModelResult(Base):
    __tablename__ = "bid_model_results"
    __table_args__ = (
        CheckConstraint(
            f"storage_kind IN ({_in_values(MODEL_RESULT_STORAGE_KINDS)})",
            name="ck_bid_model_results_storage_kind",
        ),
        CheckConstraint(
            "((storage_kind = 'inline' AND action_json IS NOT NULL AND object_ref IS NULL) OR "
            "(storage_kind = 'external' AND action_json IS NULL AND object_ref IS NOT NULL))",
            name="ck_bid_model_results_payload",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND actual_cost_microunits >= 0",
            name="ck_bid_model_results_tokens",
        ),
        ForeignKeyConstraint(
            ["task_id", "model_call_id"],
            ["bid_model_calls.task_id", "bid_model_calls.id"],
            name="fk_bid_model_results_call",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_call_id", "model_call_attempt_id"],
            ["bid_model_call_attempts.model_call_id", "bid_model_call_attempts.id"],
            name="fk_bid_model_results_call_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "source_task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_model_results_source_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("model_call_id", name="uq_bid_model_results_call"),
        Index("ix_bid_model_results_task", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    model_call_id = Column(String(36), nullable=False)
    model_call_attempt_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    source_task_attempt_id = Column(String(36), nullable=False)
    action_type = Column(String(48), nullable=False)
    storage_kind = Column(String(16), nullable=False, default="inline", server_default="inline")
    action_json = Column(JSON, nullable=True)
    object_ref = Column(String(512), nullable=True)
    action_hash = Column(String(64), nullable=False)
    response_hash = Column(String(64), nullable=False)
    usage_json = Column(JSON, nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    output_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    actual_cost_microunits = Column(BigInteger, nullable=False, default=0, server_default="0")
    finish_reason = Column(String(64), nullable=False)
    provider_receipt_id = Column(String(191), nullable=True)
    result_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
