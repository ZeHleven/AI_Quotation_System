"""Phase 2 Manifest-scoped lot-detection runs and candidate evidence links."""
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

LOT_DETECTION_STATES = ("queued", "running", "succeeded", "failed", "stale")
LOT_DETECTION_ATTEMPT_STATES = (
    "leased",
    "running",
    "succeeded",
    "failed",
    "expired",
    "cancelled",
)
LOT_EVIDENCE_ROLES = ("identity", "code", "name", "scope", "overall_scope")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidLotDetectionRun(Base):
    __tablename__ = "bid_lot_detection_runs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(LOT_DETECTION_STATES)})",
            name="ck_bid_lot_detection_runs_status",
        ),
        CheckConstraint("candidate_count >= 0", name="ck_bid_lot_detection_runs_count"),
        CheckConstraint("row_version >= 1", name="ck_bid_lot_detection_runs_row_version"),
        CheckConstraint(
            "((status = 'queued' AND started_at IS NULL AND finished_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'stale') AND finished_at IS NOT NULL))",
            name="ck_bid_lot_detection_runs_timestamps",
        ),
        CheckConstraint(
            "((status = 'succeeded' AND result_hash IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'stale')))",
            name="ck_bid_lot_detection_runs_result",
        ),
        ForeignKeyConstraint(
            ["manifest_id"],
            ["bid_document_manifests.id"],
            name="fk_bid_lot_detection_runs_manifest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("manifest_id", "input_hash", name="uq_bid_lot_detection_runs_input"),
        UniqueConstraint("manifest_id", "id", name="uq_bid_lot_detection_runs_manifest_id"),
        Index("ix_bid_lot_detection_runs_manifest_status", "manifest_id", "status"),
        Index("ix_bid_lot_detection_runs_status_requested", "status", "requested_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    manifest_id = Column(String(36), nullable=False)
    parse_set_hash = Column(String(64), nullable=False)
    detector_version = Column(String(80), nullable=False)
    rule_set_version = Column(String(80), nullable=False)
    normalizer_version = Column(String(80), nullable=False)
    input_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="queued", server_default="queued")
    retryable = Column(Boolean, nullable=False, default=True, server_default="1")
    requested_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    result_hash = Column(String(64), nullable=True)
    candidate_count = Column(Integer, nullable=False, default=0, server_default="0")
    warnings_json = Column(JSON, nullable=True)
    error_code = Column(String(100), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidLotDetectionHead(Base):
    __tablename__ = "bid_lot_detection_heads"
    __table_args__ = (
        CheckConstraint("row_version >= 1", name="ck_bid_lot_detection_heads_row_version"),
        ForeignKeyConstraint(
            ["manifest_id", "current_run_id"],
            ["bid_lot_detection_runs.manifest_id", "bid_lot_detection_runs.id"],
            name="fk_bid_lot_detection_heads_current_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("current_run_id", name="uq_bid_lot_detection_heads_current_run"),
        Index("ix_bid_lot_detection_heads_current_run", "current_run_id"),
        TABLE_OPTIONS,
    )

    manifest_id = Column(String(36), primary_key=True)
    current_run_id = Column(String(36), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidLotDetectionAttempt(Base):
    __tablename__ = "bid_lot_detection_attempts"
    __table_args__ = (
        CheckConstraint("attempt_no >= 1", name="ck_bid_lot_detection_attempts_number"),
        CheckConstraint(
            f"status IN ({_in_values(LOT_DETECTION_ATTEMPT_STATES)})",
            name="ck_bid_lot_detection_attempts_status",
        ),
        CheckConstraint("fencing_token >= 1", name="ck_bid_lot_detection_attempts_fencing"),
        CheckConstraint(
            "lease_owner IS NOT NULL AND lease_until IS NOT NULL",
            name="ck_bid_lot_detection_attempts_lease",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_bid_lot_detection_attempts_time_order",
        ),
        CheckConstraint(
            "((status IN ('succeeded', 'failed', 'expired', 'cancelled') "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('leased', 'running') AND finished_at IS NULL))",
            name="ck_bid_lot_detection_attempts_terminal",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_lot_detection_runs.id"],
            name="fk_bid_lot_detection_attempts_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "attempt_no", name="uq_bid_lot_detection_attempts_number"),
        UniqueConstraint("run_id", "id", name="uq_bid_lot_detection_attempts_run_id"),
        Index("ix_bid_lot_detection_attempts_lease", "status", "lease_until"),
        Index("ix_bid_lot_detection_attempts_run_status", "run_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False)
    lease_owner = Column(String(128), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=False)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    fencing_token = Column(BigInteger, nullable=False)
    error_class = Column(String(100), nullable=True)
    error_code = Column(String(100), nullable=True)
    retryable = Column(Boolean, nullable=False, default=False, server_default="0")
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidLotDetectionEvent(Base):
    __tablename__ = "bid_lot_detection_events"
    __table_args__ = (
        CheckConstraint("sequence_no >= 1", name="ck_bid_lot_detection_events_sequence"),
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_lot_detection_runs.id"],
            name="fk_bid_lot_detection_events_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "attempt_id"],
            ["bid_lot_detection_attempts.run_id", "bid_lot_detection_attempts.id"],
            name="fk_bid_lot_detection_events_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "sequence_no", name="uq_bid_lot_detection_events_sequence"),
        Index("ix_bid_lot_detection_events_run_created", "run_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    attempt_id = Column(String(36), nullable=True)
    sequence_no = Column(Integer, nullable=False)
    event_type = Column(String(80), nullable=False)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    payload_json = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidLotCandidateEvidence(Base):
    __tablename__ = "bid_lot_candidate_evidence"
    __table_args__ = (
        CheckConstraint(
            f"support_role IN ({_in_values(LOT_EVIDENCE_ROLES)})",
            name="ck_bid_lot_candidate_evidence_role",
        ),
        CheckConstraint("display_order >= 0", name="ck_bid_lot_candidate_evidence_order"),
        ForeignKeyConstraint(
            ["lot_candidate_id", "manifest_id"],
            ["bid_lot_candidates.id", "bid_lot_candidates.manifest_id"],
            name="fk_bid_lot_candidate_evidence_candidate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "document_version_id"],
            ["bid_evidence_fragments.id", "bid_evidence_fragments.document_version_id"],
            name="fk_bid_lot_candidate_evidence_fragment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["manifest_id", "document_version_id"],
            ["bid_manifest_documents.manifest_id", "bid_manifest_documents.document_version_id"],
            name="fk_bid_lot_candidate_evidence_manifest_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "lot_candidate_id",
            "display_order",
            name="uq_bid_lot_candidate_evidence_order",
        ),
        Index("ix_bid_lot_candidate_evidence_fragment", "evidence_id"),
        Index("ix_bid_lot_candidate_evidence_manifest", "manifest_id"),
        TABLE_OPTIONS,
    )

    lot_candidate_id = Column(String(36), primary_key=True)
    evidence_id = Column(String(36), primary_key=True)
    manifest_id = Column(String(36), nullable=False)
    document_version_id = Column(String(36), nullable=False)
    support_role = Column(String(24), nullable=False)
    display_order = Column(Integer, nullable=False)
    display_label = Column(String(300), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

