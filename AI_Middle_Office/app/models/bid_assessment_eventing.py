"""Transactional eventing, API idempotency, legacy mapping, and audit models."""
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

OUTBOX_STATES = ("pending", "dispatching", "retry_wait", "published", "dead_letter")
IDEMPOTENCY_STATES = ("processing", "completed", "failed")
PUBLIC_RESOURCE_TYPES = (
    "assessment",
    "upload_batch",
    "document_version",
    "run",
    "question_round",
    "report",
    "delta",
)
PUBLIC_EVENT_TYPES = (
    "assessment.snapshot",
    "assessment.status.changed",
    "upload_batch.changed",
    "document.parse.changed",
    "lot.selection.required",
    "lot.selected",
    "run.status.changed",
    "run.stage.changed",
    "question.round.published",
    "question.round.answered",
    "report.published",
    "report.delta.published",
    "operation.failed",
    "stream.reset",
    "stream.closed",
)
OUTBOX_EVENT_TYPES = (
    "bid.assessment.created.v1",
    "bid.upload_batch.created.v1",
    "bid.upload_file.received.v1",
    "bid.upload_file.removed.v1",
    "bid.upload_batch.deactivation_added.v1",
    "bid.upload_batch.abandoned.v1",
    "bid.document.version_registered.v1",
    "bid.manifest.committed.v1",
    "bid.document.parse_requested.v1",
    "bid.document.parsed.v1",
    "bid.document.parse_failed.v1",
    "bid.manifest.parse_set_ready.v1",
    "bid.lot_detection.requested.v1",
    "bid.lots.detected.v1",
    "bid.lot_detection.failed.v1",
    "bid.lot.selected.v1",
    "bid.assessment.input_stale.v1",
    "bid.run.created.v1",
    "bid.plan.requested.v1",
    "bid.plan.committed.v1",
    "bid.plan.continuation_requested.v1",
    "bid.task.ready.v1",
    "bid.task.leased.v1",
    "bid.task.waiting_operation.v1",
    "bid.task.waiting_input.v1",
    "bid.task.succeeded.v1",
    "bid.task.failed.v1",
    "bid.task.stale.v1",
    "bid.run.validation_requested.v1",
    "bid.run.cancel_requested.v1",
    "bid.run.cancelled.v1",
    "bid.run.retry_requested.v1",
    "bid.run.succeeded.v1",
    "bid.run.failed.v1",
    "bid.run.stale.v1",
    "bid.facts.changed.v1",
    "bid.calculation.completed.v1",
    "bid.gates.evaluated.v1",
    "bid.question.published.v1",
    "bid.question.answered.v1",
    "bid.dimensions.completed.v1",
    "bid.decision.completed.v1",
    "bid.owner_override.recorded.v1",
    "bid.report.requested.v1",
    "bid.report.validated.v1",
    "bid.report.published.v1",
    "bid.report.failed.v1",
    "bid.report.superseded.v1",
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidOutboxEvent(Base):
    __tablename__ = "bid_outbox_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({_in_values(OUTBOX_EVENT_TYPES)})",
            name="ck_bid_outbox_events_type",
        ),
        CheckConstraint(
            f"status IN ({_in_values(OUTBOX_STATES)})",
            name="ck_bid_outbox_events_status",
        ),
        CheckConstraint(
            "aggregate_version >= 1",
            name="ck_bid_outbox_events_aggregate_version",
        ),
        CheckConstraint("attempts >= 0", name="ck_bid_outbox_events_attempts"),
        CheckConstraint("row_version >= 1", name="ck_bid_outbox_events_row_version"),
        CheckConstraint(
            "run_id IS NULL OR assessment_id IS NOT NULL",
            name="ck_bid_outbox_events_run_scope",
        ),
        CheckConstraint(
            "((status = 'dispatching' AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL) OR (status <> 'dispatching' "
            "AND lease_owner IS NULL AND lease_until IS NULL))",
            name="ck_bid_outbox_events_dispatch_lease",
        ),
        CheckConstraint(
            "((status = 'published' AND published_at IS NOT NULL) "
            "OR (status <> 'published' AND published_at IS NULL))",
            name="ck_bid_outbox_events_published_at",
        ),
        CheckConstraint(
            "status <> 'dead_letter' OR last_error_code IS NOT NULL",
            name="ck_bid_outbox_events_dead_letter_error",
        ),
        ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_outbox_events_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_outbox_events_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("event_id", name="uq_bid_outbox_events_event_id"),
        UniqueConstraint("producer", "dedupe_key", name="uq_bid_outbox_events_dedupe"),
        Index("ix_bid_outbox_events_dispatch", "status", "available_at", "lease_until"),
        Index("ix_bid_outbox_events_assessment_created", "assessment_id", "created_at"),
        Index("ix_bid_outbox_events_type_created", "event_type", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    event_id = Column(String(80), nullable=False)
    event_type = Column(String(128), nullable=False)
    producer = Column(String(128), nullable=False)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(80), nullable=False)
    aggregate_version = Column(Integer, nullable=False)
    assessment_id = Column(String(36), nullable=True)
    run_id = Column(String(36), nullable=True)
    request_id = Column(String(80), nullable=False)
    causation_event_id = Column(String(80), nullable=True)
    payload_schema = Column(String(160), nullable=False)
    payload_json = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    dedupe_key = Column(String(191), nullable=False)
    status = Column(String(24), nullable=False, default="pending", server_default="pending")
    available_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    lease_owner = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(100), nullable=True)
    last_error_ref = Column(String(512), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidProcessedEvent(Base):
    __tablename__ = "bid_processed_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id"],
            ["bid_outbox_events.event_id"],
            name="fk_bid_processed_events_event",
            ondelete="RESTRICT",
        ),
        Index("ix_bid_processed_events_processed", "processed_at"),
        TABLE_OPTIONS,
    )

    consumer_name = Column(String(128), primary_key=True)
    event_id = Column(String(80), primary_key=True)
    result_hash = Column(String(64), nullable=False)
    result_ref = Column(String(512), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidPublicEvent(Base):
    __tablename__ = "bid_public_events"
    __table_args__ = (
        CheckConstraint("sequence_no >= 1", name="ck_bid_public_events_sequence"),
        CheckConstraint(
            "resource_version >= 1",
            name="ck_bid_public_events_resource_version",
        ),
        CheckConstraint(
            f"event_type IN ({_in_values(PUBLIC_EVENT_TYPES)})",
            name="ck_bid_public_events_type",
        ),
        CheckConstraint(
            f"resource_type IN ({_in_values(PUBLIC_RESOURCE_TYPES)})",
            name="ck_bid_public_events_resource_type",
        ),
        CheckConstraint(
            "((origin_type = 'outbox' AND source_event_id IS NOT NULL) "
            "OR (origin_type = 'stream_control' AND source_event_id IS NULL "
            "AND event_type IN ('assessment.snapshot', 'stream.reset', 'stream.closed')))",
            name="ck_bid_public_events_origin",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_bid_public_events_retention",
        ),
        ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_public_events_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_event_id"],
            ["bid_outbox_events.event_id"],
            name="fk_bid_public_events_source_event",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "assessment_id",
            "sequence_no",
            name="uq_bid_public_events_sequence",
        ),
        UniqueConstraint("event_id", name="uq_bid_public_events_event_id"),
        UniqueConstraint(
            "source_event_id",
            "projection_key",
            name="uq_bid_public_events_projection",
        ),
        Index("ix_bid_public_events_assessment_expiry", "assessment_id", "expires_at"),
        Index("ix_bid_public_events_assessment_created", "assessment_id", "created_at"),
        Index("ix_bid_public_events_type", "event_type"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    sequence_no = Column(BigInteger, nullable=False)
    event_id = Column(String(80), nullable=False)
    origin_type = Column(String(24), nullable=False)
    source_event_id = Column(String(80), nullable=True)
    projection_key = Column(String(191), nullable=False)
    event_type = Column(String(128), nullable=False)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(String(80), nullable=False)
    resource_version = Column(Integer, nullable=False)
    request_id = Column(String(80), nullable=False)
    payload_json = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidIdempotencyRecord(Base):
    __tablename__ = "bid_idempotency_records"
    __table_args__ = (
        CheckConstraint(
            "http_method IN ('POST', 'PUT', 'PATCH', 'DELETE')",
            name="ck_bid_idempotency_records_method",
        ),
        CheckConstraint(
            f"status IN ({_in_values(IDEMPOTENCY_STATES)})",
            name="ck_bid_idempotency_records_status",
        ),
        CheckConstraint(
            "LENGTH(idempotency_key) BETWEEN 16 AND 128",
            name="ck_bid_idempotency_records_key_length",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="ck_bid_idempotency_records_row_version",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_bid_idempotency_records_retention",
        ),
        CheckConstraint(
            "processing_expires_at IS NULL OR processing_expires_at > created_at",
            name="ck_bid_idempotency_records_processing_window",
        ),
        CheckConstraint(
            "status <> 'failed' OR retryable = 1",
            name="ck_bid_idempotency_records_failed_retryable",
        ),
        CheckConstraint(
            "((resource_type IS NULL AND resource_id IS NULL) "
            "OR (resource_type IS NOT NULL AND resource_id IS NOT NULL))",
            name="ck_bid_idempotency_records_resource_pair",
        ),
        CheckConstraint(
            "response_status_code IS NULL OR response_status_code BETWEEN 100 AND 599",
            name="ck_bid_idempotency_records_http_status",
        ),
        CheckConstraint(
            "((status = 'processing' AND processing_expires_at IS NOT NULL "
            "AND completed_at IS NULL AND response_status_code IS NULL "
            "AND response_hash IS NULL) OR (status = 'completed' "
            "AND processing_expires_at IS NULL AND completed_at IS NOT NULL "
            "AND response_status_code IS NOT NULL AND response_hash IS NOT NULL "
            "AND (response_snapshot_json IS NOT NULL OR response_ref IS NOT NULL)) "
            "OR (status = 'failed' AND processing_expires_at IS NULL "
            "AND completed_at IS NOT NULL AND failure_code IS NOT NULL "
            "AND response_status_code IS NULL AND response_snapshot_json IS NULL "
            "AND response_ref IS NULL AND response_hash IS NULL))",
            name="ck_bid_idempotency_records_lifecycle",
        ),
        ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_bid_idempotency_records_actor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "actor_id",
            "scope",
            "idempotency_key",
            name="uq_bid_idempotency_records_actor_scope_key",
        ),
        Index(
            "ix_bid_idempotency_records_status_expiry",
            "status",
            "processing_expires_at",
            "expires_at",
        ),
        Index("ix_bid_idempotency_records_request", "request_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    actor_id = Column(Integer, nullable=False)
    http_method = Column(String(8), nullable=False)
    route_template = Column(String(255), nullable=False)
    scope = Column(String(300), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    request_id = Column(String(80), nullable=False)
    request_hash = Column(String(64), nullable=False)
    status = Column(
        String(24), nullable=False, default="processing", server_default="processing"
    )
    retryable = Column(Boolean, nullable=False, default=False, server_default="0")
    resource_type = Column(String(64), nullable=True)
    resource_id = Column(String(80), nullable=True)
    response_status_code = Column(Integer, nullable=True)
    response_snapshot_json = Column(JSON, nullable=True)
    response_ref = Column(String(512), nullable=True)
    response_hash = Column(String(64), nullable=True)
    failure_code = Column(String(100), nullable=True)
    processing_expires_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidLegacyResourceLink(Base):
    __tablename__ = "bid_legacy_resource_links"
    __table_args__ = (
        CheckConstraint(
            "new_resource_type IN ('assessment', 'manifest', 'run')",
            name="ck_bid_legacy_resource_links_type",
        ),
        CheckConstraint(
            "((new_resource_type = 'assessment' AND assessment_id IS NOT NULL "
            "AND assessment_id = new_resource_id AND manifest_id IS NULL "
            "AND run_id IS NULL) OR (new_resource_type = 'manifest' "
            "AND manifest_id IS NOT NULL AND manifest_id = new_resource_id "
            "AND assessment_id IS NULL AND run_id IS NULL) OR "
            "(new_resource_type = 'run' AND run_id IS NOT NULL "
            "AND run_id = new_resource_id AND assessment_id IS NULL "
            "AND manifest_id IS NULL))",
            name="ck_bid_legacy_resource_links_target",
        ),
        ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_legacy_resource_links_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["manifest_id"],
            ["bid_document_manifests.id"],
            name="fk_bid_legacy_resource_links_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_analysis_runs.id"],
            name="fk_bid_legacy_resource_links_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["imported_by"],
            ["users.id"],
            name="fk_bid_legacy_resource_links_imported_by",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "legacy_system",
            "legacy_resource_type",
            "legacy_resource_id",
            "new_resource_type",
            "new_resource_id",
            name="uq_bid_legacy_resource_links_mapping",
        ),
        Index(
            "ix_bid_legacy_resource_links_legacy",
            "legacy_system",
            "legacy_resource_type",
            "legacy_resource_id",
        ),
        Index(
            "ix_bid_legacy_resource_links_new",
            "new_resource_type",
            "new_resource_id",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    legacy_system = Column(String(64), nullable=False)
    legacy_resource_type = Column(String(64), nullable=False)
    legacy_resource_id = Column(String(128), nullable=False)
    new_resource_type = Column(String(64), nullable=False)
    new_resource_id = Column(String(36), nullable=False)
    assessment_id = Column(String(36), nullable=True)
    manifest_id = Column(String(36), nullable=True)
    run_id = Column(String(36), nullable=True)
    source_hash = Column(String(64), nullable=False)
    import_metadata_json = Column(JSON, nullable=True)
    imported_by = Column(Integer, nullable=True)
    imported_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidAuditLog(Base):
    __tablename__ = "bid_audit_log"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'system', 'service')",
            name="ck_bid_audit_log_actor_type",
        ),
        CheckConstraint(
            "((actor_type = 'user' AND actor_id IS NOT NULL) "
            "OR (actor_type IN ('system', 'service') AND actor_id IS NULL))",
            name="ck_bid_audit_log_actor",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')",
            name="ck_bid_audit_log_outcome",
        ),
        ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_audit_log_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_bid_audit_log_actor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("record_hash", name="uq_bid_audit_log_record_hash"),
        Index("ix_bid_audit_log_assessment_occurred", "assessment_id", "occurred_at"),
        Index("ix_bid_audit_log_entity_occurred", "entity_type", "entity_id", "occurred_at"),
        Index("ix_bid_audit_log_request", "request_id"),
        Index("ix_bid_audit_log_actor_occurred", "actor_type", "actor_ref", "occurred_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=True)
    actor_type = Column(String(24), nullable=False)
    actor_id = Column(Integer, nullable=True)
    actor_ref = Column(String(128), nullable=False)
    action = Column(String(128), nullable=False)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(String(80), nullable=False)
    outcome = Column(String(24), nullable=False)
    before_hash = Column(String(64), nullable=True)
    after_hash = Column(String(64), nullable=True)
    request_id = Column(String(80), nullable=False)
    correlation_id = Column(String(80), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    metadata_hash = Column(String(64), nullable=False)
    record_hash = Column(String(64), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
