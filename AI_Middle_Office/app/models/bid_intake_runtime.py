from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import LONGBLOB, LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


def _long_blob():
    return LargeBinary().with_variant(LONGBLOB, "mysql")


class BidIntakeAssessment(Base):
    """Business-facing assessment bound to one immutable evidence manifest."""

    __tablename__ = "bid_intake_assessments"
    __table_args__ = (
        UniqueConstraint(
            "assessment_uuid",
            name="uq_bid_intake_assessments_uuid",
        ),
        Index(
            "ix_bid_intake_assessments_project_status_created",
            "project_id",
            "status",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    assessment_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    manifest_id = Column(
        Integer,
        ForeignKey("bid_evidence_manifests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    manifest_version = Column(Integer, nullable=False, index=True)
    manifest_hash = Column(String(64), nullable=False, index=True)
    policy_version = Column(
        String(64),
        nullable=False,
        default="qs_bid_decision_policy_2026_01",
        server_default="qs_bid_decision_policy_2026_01",
        index=True,
    )
    analysis_goal = Column(_long_text(), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    report_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    latest_run_uuid = Column(String(36), nullable=True, index=True)
    recommendation = Column(String(40), nullable=True, index=True)
    gate_status = Column(String(40), nullable=True, index=True)
    assessment_json = Column(_long_text(), nullable=True)
    policy_evaluation_json = Column(_long_text(), nullable=True)
    gate_result_json = Column(_long_text(), nullable=True)
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidIntakePolicyCalibrationLabel(Base):
    """Immutable expert gold label for one assessment report snapshot."""

    __tablename__ = "bid_intake_policy_calibration_labels"
    __table_args__ = (
        UniqueConstraint(
            "label_uuid",
            name="uq_bid_intake_policy_calibration_labels_uuid",
        ),
        UniqueConstraint(
            "assessment_id",
            "label_version",
            name="uq_bid_intake_policy_calibration_labels_version",
        ),
        Index(
            "ix_bid_intake_policy_labels_project_active_created",
            "project_id",
            "active",
            "created_at",
        ),
        Index(
            "ix_bid_intake_policy_labels_decision_active",
            "expected_decision",
            "active",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    label_uuid = Column(String(36), nullable=False, unique=True, index=True)
    assessment_id = Column(
        Integer,
        ForeignKey("bid_intake_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label_version = Column(Integer, nullable=False)
    active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )
    supersedes_label_id = Column(
        Integer,
        ForeignKey(
            "bid_intake_policy_calibration_labels.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    dataset_split = Column(String(24), nullable=False, index=True)
    label_basis = Column(String(40), nullable=False, index=True)
    expected_decision = Column(String(40), nullable=False, index=True)
    hard_stop_expected = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )
    rationale = Column(_long_text(), nullable=False)
    actual_outcome_json = Column(_long_text(), nullable=True)
    case_snapshot_json = Column(_long_text(), nullable=False)
    source_report_version = Column(Integer, nullable=False)
    source_manifest_version = Column(Integer, nullable=False)
    source_manifest_hash = Column(String(64), nullable=False, index=True)
    source_policy_version = Column(String(64), nullable=False, index=True)
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    superseded_at = Column(DateTime(timezone=True), nullable=True)


class BidIntakePolicyCalibrationReview(Base):
    """Immutable second-person review for one immutable gold label."""

    __tablename__ = "bid_intake_policy_calibration_reviews"
    __table_args__ = (
        UniqueConstraint(
            "review_uuid",
            name="uq_bid_intake_policy_calibration_reviews_uuid",
        ),
        UniqueConstraint(
            "label_id",
            name="uq_bid_intake_policy_calibration_reviews_label",
        ),
        Index(
            "ix_bid_intake_policy_reviews_action_created",
            "action",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    review_uuid = Column(String(36), nullable=False, unique=True, index=True)
    label_id = Column(
        Integer,
        ForeignKey(
            "bid_intake_policy_calibration_labels.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )
    action = Column(String(24), nullable=False, index=True)
    note = Column(_long_text(), nullable=False)
    reviewed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class BidIntakePolicyCalibrationDataset(Base):
    """Immutable reviewed calibration dataset used by candidate search."""

    __tablename__ = "bid_intake_policy_calibration_datasets"
    __table_args__ = (
        UniqueConstraint(
            "dataset_uuid",
            name="uq_bid_intake_policy_calibration_datasets_uuid",
        ),
        UniqueConstraint(
            "dataset_version",
            name="uq_bid_intake_policy_calibration_datasets_version",
        ),
        UniqueConstraint(
            "dataset_fingerprint",
            name="uq_bid_intake_policy_calibration_datasets_fingerprint",
        ),
        Index(
            "ix_bid_intake_policy_datasets_status_created",
            "status",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    dataset_uuid = Column(String(36), nullable=False, unique=True, index=True)
    dataset_version = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    status = Column(
        String(24),
        nullable=False,
        default="frozen",
        server_default="frozen",
        index=True,
    )
    dataset_fingerprint = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    snapshot_json = Column(_long_text(), nullable=False)
    quality_report_json = Column(_long_text(), nullable=False)
    freeze_note = Column(_long_text(), nullable=True)
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class BidIntakePolicyCandidate(Base):
    """Frozen threshold proposal awaiting one-time holdout evaluation."""

    __tablename__ = "bid_intake_policy_candidates"
    __table_args__ = (
        UniqueConstraint(
            "proposal_uuid",
            name="uq_bid_intake_policy_candidates_uuid",
        ),
        UniqueConstraint(
            "candidate_version",
            name="uq_bid_intake_policy_candidates_version",
        ),
        UniqueConstraint(
            "base_policy_version",
            "dataset_fingerprint",
            name="uq_bid_intake_policy_candidates_dataset",
        ),
        Index(
            "ix_bid_intake_policy_candidates_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_bid_intake_policy_candidates_base_created",
            "base_policy_version",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    proposal_uuid = Column(String(36), nullable=False, unique=True, index=True)
    candidate_version = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    base_policy_version = Column(
        String(64),
        nullable=False,
        index=True,
    )
    status = Column(
        String(24),
        nullable=False,
        default="draft",
        server_default="draft",
        index=True,
    )
    search_method = Column(String(48), nullable=False)
    dataset_fingerprint = Column(
        String(64),
        nullable=False,
        index=True,
    )
    dataset_snapshot_json = Column(_long_text(), nullable=False)
    policy_yaml = Column(_long_text(), nullable=False)
    changed_fields_json = Column(_long_text(), nullable=False)
    development_report_json = Column(_long_text(), nullable=False)
    blind_report_json = Column(_long_text(), nullable=True)
    calibration_dataset_id = Column(
        Integer,
        ForeignKey(
            "bid_intake_policy_calibration_datasets.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    blind_evaluated_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    blind_evaluated_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
    calibration_dataset = relationship(
        "BidIntakePolicyCalibrationDataset",
        foreign_keys=[calibration_dataset_id],
    )


class BidIntakeAgentRun(Base):
    """Durable execution control plane for one LangGraph thread."""

    __tablename__ = "bid_intake_agent_runs"
    __table_args__ = (
        UniqueConstraint("run_uuid", name="uq_bid_intake_agent_runs_uuid"),
        UniqueConstraint("thread_id", name="uq_bid_intake_agent_runs_thread"),
        Index(
            "ix_bid_intake_agent_runs_assessment_created",
            "assessment_id",
            "created_at",
        ),
        Index(
            "ix_bid_intake_agent_runs_status_lease",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_bid_intake_agent_runs_project_status_created",
            "project_id",
            "status",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_uuid = Column(String(36), nullable=False, unique=True, index=True)
    assessment_id = Column(
        Integer,
        ForeignKey("bid_intake_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id = Column(String(160), nullable=False, unique=True, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    phase = Column(
        String(64),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    trigger_source = Column(
        String(32),
        nullable=False,
        default="manual",
        server_default="manual",
        index=True,
    )
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=3, server_default="3")
    checkpoint_id = Column(String(64), nullable=True, index=True)
    state_summary_json = Column(_long_text(), nullable=True)
    versions_json = Column(_long_text(), nullable=True)
    worker_id = Column(String(160), nullable=True, index=True)
    lease_token = Column(String(64), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    error_code = Column(String(64), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidIntakeHumanDecision(Base):
    """Idempotent human command queued for a paused Agent run."""

    __tablename__ = "bid_intake_human_decisions"
    __table_args__ = (
        UniqueConstraint(
            "decision_uuid",
            name="uq_bid_intake_human_decisions_uuid",
        ),
        Index(
            "ix_bid_intake_human_decisions_run_status_created",
            "run_id",
            "status",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    decision_uuid = Column(String(36), nullable=False, unique=True, index=True)
    assessment_id = Column(
        Integer,
        ForeignKey("bid_intake_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(
        Integer,
        ForeignKey("bid_intake_agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action = Column(String(40), nullable=False, index=True)
    report_version = Column(Integer, nullable=False)
    manifest_version = Column(Integer, nullable=False)
    decided_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    decided_by_name = Column(String(160), nullable=False)
    note = Column(Text, nullable=True)
    conditions_json = Column(_long_text(), nullable=True)
    status = Column(
        String(24),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    applied_at = Column(DateTime(timezone=True), nullable=True)


class BidIntakeRunEvent(Base):
    """Append-only execution audit event."""

    __tablename__ = "bid_intake_run_events"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_bid_intake_run_events_uuid"),
        Index(
            "ix_bid_intake_run_events_run_created",
            "run_id",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_uuid = Column(String(36), nullable=False, unique=True, index=True)
    assessment_id = Column(
        Integer,
        ForeignKey("bid_intake_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(
        Integer,
        ForeignKey("bid_intake_agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    phase = Column(String(64), nullable=True, index=True)
    message = Column(Text, nullable=True)
    payload_json = Column(_long_text(), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class BidIntakeWorkerHeartbeat(Base):
    """Last-known liveness and safe capability summary of one Agent worker."""

    __tablename__ = "bid_intake_worker_heartbeats"
    __table_args__ = (
        UniqueConstraint(
            "worker_id",
            name="uq_bid_intake_worker_heartbeats_worker",
        ),
        Index(
            "ix_bid_intake_worker_heartbeats_status_seen",
            "status",
            "last_seen_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(String(160), nullable=False, unique=True, index=True)
    hostname = Column(String(160), nullable=False)
    process_id = Column(Integer, nullable=False)
    runtime_version = Column(String(64), nullable=False, index=True)
    status = Column(
        String(24),
        nullable=False,
        default="online",
        server_default="online",
        index=True,
    )
    current_run_uuid = Column(String(36), nullable=True, index=True)
    capabilities_json = Column(_long_text(), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    stopped_at = Column(DateTime(timezone=True), nullable=True)


class BidIntakeCheckpoint(Base):
    """Serialized LangGraph checkpoint without business-report duplication."""

    __tablename__ = "bid_intake_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            name="uq_bid_intake_checkpoints_identity",
        ),
        Index(
            "ix_bid_intake_checkpoints_thread_ns_created",
            "thread_id",
            "checkpoint_ns",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    thread_id = Column(String(160), nullable=False, index=True)
    checkpoint_ns = Column(
        String(128),
        nullable=False,
        default="",
        server_default="",
    )
    checkpoint_id = Column(String(64), nullable=False)
    parent_checkpoint_id = Column(String(64), nullable=True)
    checkpoint_type = Column(String(64), nullable=False)
    checkpoint_blob = Column(_long_blob(), nullable=False)
    metadata_type = Column(String(64), nullable=False)
    metadata_blob = Column(_long_blob(), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class BidIntakeCheckpointBlob(Base):
    __tablename__ = "bid_intake_checkpoint_blobs"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
            name="uq_bid_intake_checkpoint_blobs_identity",
        ),
    )

    id = Column(Integer, primary_key=True)
    thread_id = Column(String(160), nullable=False, index=True)
    checkpoint_ns = Column(
        String(128),
        nullable=False,
        default="",
        server_default="",
    )
    channel = Column(String(128), nullable=False)
    version = Column(String(128), nullable=False)
    value_type = Column(String(64), nullable=False)
    value_blob = Column(_long_blob(), nullable=False)


class BidIntakeCheckpointWrite(Base):
    __tablename__ = "bid_intake_checkpoint_writes"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "write_index",
            name="uq_bid_intake_checkpoint_writes_identity",
        ),
        Index(
            "ix_bid_intake_checkpoint_writes_checkpoint",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
        ),
    )

    id = Column(Integer, primary_key=True)
    thread_id = Column(String(160), nullable=False, index=True)
    checkpoint_ns = Column(
        String(128),
        nullable=False,
        default="",
        server_default="",
    )
    checkpoint_id = Column(String(64), nullable=False)
    task_id = Column(String(160), nullable=False)
    task_path = Column(String(500), nullable=False, default="", server_default="")
    write_index = Column(Integer, nullable=False)
    channel = Column(String(128), nullable=False)
    value_type = Column(String(64), nullable=False)
    value_blob = Column(_long_blob(), nullable=False)
