"""Phase 3E context-manifest, tool-invocation, and result-store models."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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
MODEL_ROLES = (
    "planner",
    "local_research",
    "synthesizer",
    "evidence_validator",
    "report_writer",
)
TOOL_INVOCATION_STATES = (
    "accepted",
    "pending",
    "succeeded",
    "failed",
    "rejected",
    "cancelled",
)
TOOL_RESULT_STATES = (
    "ok",
    "no_result",
    "partial",
    "failed",
    "unauthorized",
    "invalid_arguments",
    "missing_inputs",
    "stale",
    "budget_exhausted",
)
TOOL_RESULT_STORAGE_KINDS = ("inline", "external")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidContextManifest(Base):
    __tablename__ = "bid_context_manifests"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({_in_values(MODEL_ROLES)})",
            name="ck_bid_context_manifests_role",
        ),
        CheckConstraint("manifest_seq >= 1", name="ck_bid_context_manifests_seq"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_context_manifests_fencing"),
        CheckConstraint(
            "token_estimate BETWEEN 0 AND 32000",
            name="ck_bid_context_manifests_tokens",
        ),
        CheckConstraint(
            "compression_level BETWEEN 0 AND 3",
            name="ck_bid_context_manifests_compression",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_context_manifests_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_context_manifests_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_context_manifests_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "task_attempt_id",
            "manifest_seq",
            name="uq_bid_context_manifests_seq",
        ),
        UniqueConstraint(
            "task_attempt_id",
            "manifest_hash",
            name="uq_bid_context_manifests_hash",
        ),
        UniqueConstraint(
            "task_attempt_id",
            "id",
            name="uq_bid_context_manifests_attempt_id",
        ),
        Index("ix_bid_context_manifests_run", "run_id", "created_at"),
        Index("ix_bid_context_manifests_task", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    task_attempt_id = Column(String(36), nullable=False)
    manifest_seq = Column(Integer, nullable=False)
    fencing_token = Column(BigInteger, nullable=False)
    role = Column(String(32), nullable=False)
    context_profile = Column(String(64), nullable=False)
    assembler_version = Column(String(80), nullable=False)
    token_estimate = Column(Integer, nullable=False)
    compression_level = Column(Integer, nullable=False)
    bound_versions_json = Column(JSON, nullable=False)
    manifest_json = Column(JSON, nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidToolInvocation(Base):
    __tablename__ = "bid_tool_invocations"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(TOOL_INVOCATION_STATES)})",
            name="ck_bid_tool_invocations_status",
        ),
        CheckConstraint("invocation_seq >= 1", name="ck_bid_tool_invocations_seq"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_tool_invocations_fencing"),
        CheckConstraint("row_version >= 1", name="ck_bid_tool_invocations_row_version"),
        CheckConstraint(
            "((status = 'accepted' AND completed_at IS NULL "
            "AND async_operation_id IS NULL AND checkpoint_id IS NULL) OR "
            "(status = 'pending' AND completed_at IS NULL "
            "AND async_operation_id IS NOT NULL AND checkpoint_id IS NOT NULL) OR "
            "(status IN ('succeeded', 'failed', 'rejected', 'cancelled') "
            "AND completed_at IS NOT NULL))",
            name="ck_bid_tool_invocations_lifecycle",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_tool_invocations_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_tool_invocations_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_tool_invocations_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "context_manifest_id"],
            ["bid_context_manifests.task_attempt_id", "bid_context_manifests.id"],
            name="fk_bid_tool_invocations_context",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tool_call_id", name="uq_bid_tool_invocations_call_id"),
        UniqueConstraint(
            "task_attempt_id",
            "invocation_seq",
            name="uq_bid_tool_invocations_seq",
        ),
        UniqueConstraint(
            "task_attempt_id",
            "idempotency_key",
            name="uq_bid_tool_invocations_idempotency",
        ),
        UniqueConstraint(
            "task_attempt_id",
            "id",
            name="uq_bid_tool_invocations_attempt_id",
        ),
        Index("ix_bid_tool_invocations_run", "run_id", "created_at"),
        Index("ix_bid_tool_invocations_status", "status", "created_at"),
        Index("ix_bid_tool_invocations_async", "async_operation_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    tool_call_id = Column(String(80), nullable=False)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    task_attempt_id = Column(String(36), nullable=False)
    context_manifest_id = Column(String(36), nullable=False)
    tool_registry_version_id = Column(
        String(36),
        ForeignKey("bid_tool_registry_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    async_operation_id = Column(
        String(36),
        ForeignKey("bid_async_operations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    checkpoint_id = Column(
        String(36),
        ForeignKey("bid_checkpoints.id", ondelete="RESTRICT"),
        nullable=True,
    )
    invocation_seq = Column(Integer, nullable=False)
    fencing_token = Column(BigInteger, nullable=False)
    tool_name = Column(String(128), nullable=False)
    tool_profile = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    arguments_json = Column(JSON, nullable=False)
    arguments_hash = Column(String(64), nullable=False)
    request_hash = Column(String(64), nullable=False)
    scope_token_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="accepted", server_default="accepted")
    error_code = Column(String(100), nullable=True)
    budget_before_json = Column(JSON, nullable=False)
    budget_after_json = Column(JSON, nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidToolResult(Base):
    __tablename__ = "bid_tool_results"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(TOOL_RESULT_STATES)})",
            name="ck_bid_tool_results_status",
        ),
        CheckConstraint(
            f"storage_kind IN ({_in_values(TOOL_RESULT_STORAGE_KINDS)})",
            name="ck_bid_tool_results_storage",
        ),
        CheckConstraint("byte_count >= 0", name="ck_bid_tool_results_bytes"),
        CheckConstraint("returned_items >= 0", name="ck_bid_tool_results_items"),
        CheckConstraint(
            "((storage_kind = 'inline' AND inline_data_json IS NOT NULL "
            "AND object_ref IS NULL) OR "
            "(storage_kind = 'external' AND object_ref IS NOT NULL))",
            name="ck_bid_tool_results_payload",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "invocation_id"],
            ["bid_tool_invocations.task_attempt_id", "bid_tool_invocations.id"],
            name="fk_bid_tool_results_invocation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("invocation_id", name="uq_bid_tool_results_invocation"),
        Index("ix_bid_tool_results_attempt", "task_attempt_id", "created_at"),
        Index("ix_bid_tool_results_expiry", "expires_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    invocation_id = Column(String(36), nullable=False)
    task_attempt_id = Column(String(36), nullable=False)
    async_operation_id = Column(
        String(36),
        ForeignKey("bid_async_operations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status = Column(String(24), nullable=False)
    summary = Column(Text, nullable=False)
    storage_kind = Column(String(16), nullable=False)
    inline_data_json = Column(JSON, nullable=True)
    object_ref = Column(String(512), nullable=True)
    data_hash = Column(String(64), nullable=False)
    evidence_refs_json = Column(JSON, nullable=False)
    warnings_json = Column(JSON, nullable=False)
    metrics_json = Column(JSON, nullable=False)
    truncated = Column(Boolean, nullable=False, default=False, server_default="0")
    byte_count = Column(Integer, nullable=False)
    returned_items = Column(Integer, nullable=False, default=0, server_default="0")
    result_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
