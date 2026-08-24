"""Phase 3F durable Tool Adapter/Executor dispatch authority."""
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
    UniqueConstraint,
    func,
)

from app.core.database import Base


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}
TOOL_DISPATCH_STATES = (
    "queued",
    "leased",
    "sending",
    "awaiting_receipt",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
    "uncertain",
    "dead_letter",
)
TOOL_DISPATCH_ATTEMPT_STATES = (
    "leased",
    "sending",
    "acknowledged",
    "succeeded",
    "failed",
    "lease_expired",
    "cancelled",
    "uncertain",
)
TOOL_ADAPTER_MODES = ("local_readonly", "external_async")
TOOL_REPLAY_POLICIES = ("safe_idempotent", "reconcile_required", "no_replay")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidToolDispatch(Base):
    __tablename__ = "bid_tool_dispatches"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(TOOL_DISPATCH_STATES)})",
            name="ck_bid_tool_dispatches_status",
        ),
        CheckConstraint(
            f"adapter_mode IN ({_in_values(TOOL_ADAPTER_MODES)})",
            name="ck_bid_tool_dispatches_adapter_mode",
        ),
        CheckConstraint(
            f"replay_policy IN ({_in_values(TOOL_REPLAY_POLICIES)})",
            name="ck_bid_tool_dispatches_replay_policy",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_bid_tool_dispatches_attempts",
        ),
        CheckConstraint("fencing_token >= 0", name="ck_bid_tool_dispatches_fencing"),
        CheckConstraint("row_version >= 1", name="ck_bid_tool_dispatches_row_version"),
        CheckConstraint(
            "reserved_cost_microunits >= 0 AND actual_cost_microunits >= 0",
            name="ck_bid_tool_dispatches_cost",
        ),
        CheckConstraint(
            "((status IN ('leased', 'sending') AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('queued', 'retry_wait', 'awaiting_receipt') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled', 'uncertain', 'dead_letter') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND completed_at IS NOT NULL))",
            name="ck_bid_tool_dispatches_lifecycle",
        ),
        ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_tool_dispatches_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "invocation_id"],
            ["bid_tool_invocations.task_attempt_id", "bid_tool_invocations.id"],
            name="fk_bid_tool_dispatches_invocation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "task_attempt_id", "async_operation_id"],
            [
                "bid_async_operations.task_id",
                "bid_async_operations.task_attempt_id",
                "bid_async_operations.id",
            ],
            name="fk_bid_tool_dispatches_operation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("invocation_id", name="uq_bid_tool_dispatches_invocation"),
        UniqueConstraint("async_operation_id", name="uq_bid_tool_dispatches_operation"),
        UniqueConstraint("dispatch_key", name="uq_bid_tool_dispatches_key"),
        UniqueConstraint(
            "provider_request_id",
            name="uq_bid_tool_dispatches_provider_request",
        ),
        UniqueConstraint(
            "provider_receipt_id",
            name="uq_bid_tool_dispatches_provider_receipt",
        ),
        Index(
            "ix_bid_tool_dispatches_ready",
            "status",
            "available_at",
            "lease_until",
        ),
        Index("ix_bid_tool_dispatches_task", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    invocation_id = Column(String(36), nullable=False)
    async_operation_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    task_attempt_id = Column(String(36), nullable=False)
    adapter_name = Column(String(128), nullable=False)
    adapter_version = Column(String(80), nullable=False)
    adapter_mode = Column(String(24), nullable=False)
    replay_policy = Column(String(32), nullable=False)
    dispatch_key = Column(String(128), nullable=False)
    envelope_json = Column(JSON, nullable=False)
    envelope_hash = Column(String(64), nullable=False)
    scope_token_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="queued", server_default="queued")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=3, server_default="3")
    fencing_token = Column(BigInteger, nullable=False, default=0, server_default="0")
    available_at = Column(DateTime(timezone=True), nullable=False)
    lease_owner = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    provider_request_id = Column(String(191), nullable=False)
    provider_receipt_id = Column(String(191), nullable=True)
    provider_ref = Column(String(255), nullable=True)
    reserved_cost_microunits = Column(BigInteger, nullable=False, default=0, server_default="0")
    actual_cost_microunits = Column(BigInteger, nullable=False, default=0, server_default="0")
    last_error_code = Column(String(100), nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidToolDispatchAttempt(Base):
    __tablename__ = "bid_tool_dispatch_attempts"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(TOOL_DISPATCH_ATTEMPT_STATES)})",
            name="ck_bid_tool_dispatch_attempts_status",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_bid_tool_dispatch_attempts_number"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_tool_dispatch_attempts_fencing"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bid_tool_dispatch_attempts_time_order",
        ),
        CheckConstraint(
            "((status IN ('leased', 'sending') AND finished_at IS NULL) OR "
            "(status IN ('acknowledged', 'succeeded', 'failed', 'lease_expired', "
            "'cancelled', 'uncertain') AND finished_at IS NOT NULL))",
            name="ck_bid_tool_dispatch_attempts_lifecycle",
        ),
        ForeignKeyConstraint(
            ["dispatch_id"],
            ["bid_tool_dispatches.id"],
            name="fk_bid_tool_dispatch_attempts_dispatch",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "dispatch_id", "attempt_no", name="uq_bid_tool_dispatch_attempts_number"
        ),
        UniqueConstraint(
            "dispatch_id", "fencing_token", name="uq_bid_tool_dispatch_attempts_fencing"
        ),
        UniqueConstraint(
            "execution_key", name="uq_bid_tool_dispatch_attempts_execution_key"
        ),
        Index(
            "ix_bid_tool_dispatch_attempts_lease",
            "status",
            "lease_until",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    dispatch_id = Column(String(36), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    fencing_token = Column(BigInteger, nullable=False)
    worker_id = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False)
    execution_key = Column(String(128), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    send_started_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    outcome_hash = Column(String(64), nullable=True)
    error_code = Column(String(100), nullable=True)
    detail_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
