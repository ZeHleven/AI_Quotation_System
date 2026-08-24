"""Phase 3G run-level validation and convergence authority."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
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
RUN_VALIDATION_STATES = (
    "requested",
    "leased",
    "running",
    "passed",
    "failed",
    "stale",
    "cancelled",
)
RUN_VALIDATION_ATTEMPT_STATES = (
    "leased",
    "running",
    "passed",
    "failed",
    "stale",
    "lease_expired",
    "cancelled",
)
RUN_VALIDATION_OUTCOMES = ("passed", "failed", "stale")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidRunValidation(Base):
    __tablename__ = "bid_run_validations"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(RUN_VALIDATION_STATES)})",
            name="ck_bid_run_validations_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_bid_run_validations_attempts"),
        CheckConstraint("fencing_token >= 0", name="ck_bid_run_validations_fencing"),
        CheckConstraint("row_version >= 1", name="ck_bid_run_validations_row_version"),
        CheckConstraint(
            "((status IN ('leased', 'running') AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND finished_at IS NULL) OR "
            "(status = 'requested' AND lease_owner IS NULL AND lease_until IS NULL "
            "AND finished_at IS NULL) OR "
            "(status IN ('passed', 'failed', 'stale', 'cancelled') "
            "AND lease_owner IS NULL AND lease_until IS NULL AND finished_at IS NOT NULL))",
            name="ck_bid_run_validations_lifecycle",
        ),
        CheckConstraint(
            "((status IN ('passed', 'failed', 'stale') AND outcome = status "
            "AND result_hash IS NOT NULL AND result_json IS NOT NULL) OR "
            "(status IN ('requested', 'leased', 'running', 'cancelled') "
            "AND outcome IS NULL))",
            name="ck_bid_run_validations_outcome",
        ),
        CheckConstraint(
            "((status = 'passed' AND retryable = 0 AND failure_code IS NULL) OR "
            "(status = 'failed' AND failure_code IS NOT NULL) OR "
            "(status = 'stale' AND retryable = 0 AND failure_code IS NOT NULL) OR "
            "status IN ('requested', 'leased', 'running', 'cancelled'))",
            name="ck_bid_run_validations_failure",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_run_validations_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "plan_revision_id"],
            ["bid_plan_revisions.run_id", "bid_plan_revisions.id"],
            name="fk_bid_run_validations_plan",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", name="uq_bid_run_validations_run"),
        UniqueConstraint("source_event_id", name="uq_bid_run_validations_source_event"),
        UniqueConstraint("validation_key", name="uq_bid_run_validations_key"),
        UniqueConstraint("run_id", "id", name="uq_bid_run_validations_run_id"),
        Index("ix_bid_run_validations_ready", "status", "lease_until", "requested_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    plan_revision_id = Column(String(36), nullable=True)
    source_event_id = Column(String(80), nullable=False)
    validation_key = Column(String(128), nullable=False)
    validator_version = Column(String(80), nullable=False)
    input_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="requested", server_default="requested")
    outcome = Column(String(24), nullable=True)
    retryable = Column(Boolean, nullable=False, default=False, server_default="0")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    fencing_token = Column(BigInteger, nullable=False, default=0, server_default="0")
    lease_owner = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    result_json = Column(JSON, nullable=True)
    result_hash = Column(String(64), nullable=True)
    failure_code = Column(String(100), nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidRunValidationAttempt(Base):
    __tablename__ = "bid_run_validation_attempts"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(RUN_VALIDATION_ATTEMPT_STATES)})",
            name="ck_bid_run_validation_attempts_status",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_bid_run_validation_attempts_number"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_run_validation_attempts_fencing"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bid_run_validation_attempts_time_order",
        ),
        CheckConstraint(
            "((status IN ('leased', 'running') AND finished_at IS NULL) OR "
            "(status IN ('passed', 'failed', 'stale', 'lease_expired', 'cancelled') "
            "AND finished_at IS NOT NULL))",
            name="ck_bid_run_validation_attempts_lifecycle",
        ),
        ForeignKeyConstraint(
            ["run_id", "validation_id"],
            ["bid_run_validations.run_id", "bid_run_validations.id"],
            name="fk_bid_run_validation_attempts_validation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "validation_id", "attempt_no", name="uq_bid_run_validation_attempts_number"
        ),
        UniqueConstraint(
            "validation_id", "fencing_token", name="uq_bid_run_validation_attempts_fencing"
        ),
        UniqueConstraint("execution_key", name="uq_bid_run_validation_attempts_execution_key"),
        Index("ix_bid_run_validation_attempts_lease", "status", "lease_until"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    validation_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    fencing_token = Column(BigInteger, nullable=False)
    worker_id = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False)
    execution_key = Column(String(128), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    heartbeat_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    result_hash = Column(String(64), nullable=True)
    error_code = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
