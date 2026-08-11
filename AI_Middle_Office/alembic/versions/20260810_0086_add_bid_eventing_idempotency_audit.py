"""add bid eventing, idempotency, legacy mapping, and audit foundation

Revision ID: 20260810_0086
Revises: 20260810_0085
Create Date: 2026-08-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0086"
down_revision: Union[str, None] = "20260810_0085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

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
    "bid.document.version_registered.v1",
    "bid.manifest.committed.v1",
    "bid.document.parse_requested.v1",
    "bid.document.parsed.v1",
    "bid.document.parse_failed.v1",
    "bid.lots.detected.v1",
    "bid.lot.selected.v1",
    "bid.assessment.input_stale.v1",
    "bid.run.created.v1",
    "bid.plan.requested.v1",
    "bid.plan.committed.v1",
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
    "bid.run.succeeded.v1",
    "bid.run.failed.v1",
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


def upgrade() -> None:
    op.create_table(
        "bid_outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("producer", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=80), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("causation_event_id", sa.String(length=80), nullable=True),
        sa.Column("payload_schema", sa.String(length=160), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=191), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_ref", sa.String(length=512), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            f"event_type IN ({_in_values(OUTBOX_EVENT_TYPES)})",
            name="ck_bid_outbox_events_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_in_values(OUTBOX_STATES)})",
            name="ck_bid_outbox_events_status",
        ),
        sa.CheckConstraint("aggregate_version >= 1", name="ck_bid_outbox_events_aggregate_version"),
        sa.CheckConstraint("attempts >= 0", name="ck_bid_outbox_events_attempts"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_outbox_events_row_version"),
        sa.CheckConstraint("run_id IS NULL OR assessment_id IS NOT NULL", name="ck_bid_outbox_events_run_scope"),
        sa.CheckConstraint(
            "((status = 'dispatching' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) "
            "OR (status <> 'dispatching' AND lease_owner IS NULL AND lease_until IS NULL))",
            name="ck_bid_outbox_events_dispatch_lease",
        ),
        sa.CheckConstraint(
            "((status = 'published' AND published_at IS NOT NULL) "
            "OR (status <> 'published' AND published_at IS NULL))",
            name="ck_bid_outbox_events_published_at",
        ),
        sa.CheckConstraint(
            "status <> 'dead_letter' OR last_error_code IS NOT NULL",
            name="ck_bid_outbox_events_dead_letter_error",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_outbox_events_assessment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_outbox_events_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_outbox_events"),
        sa.UniqueConstraint("event_id", name="uq_bid_outbox_events_event_id"),
        sa.UniqueConstraint("producer", "dedupe_key", name="uq_bid_outbox_events_dedupe"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_outbox_events_dispatch",
        "bid_outbox_events",
        ["status", "available_at", "lease_until"],
    )
    op.create_index(
        "ix_bid_outbox_events_assessment_created",
        "bid_outbox_events",
        ["assessment_id", "created_at"],
    )
    op.create_index("ix_bid_outbox_events_type_created", "bid_outbox_events", ["event_type", "created_at"])

    op.create_table(
        "bid_processed_events",
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=80), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("result_ref", sa.String(length=512), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["bid_outbox_events.event_id"],
            name="fk_bid_processed_events_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("consumer_name", "event_id", name="pk_bid_processed_events"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_processed_events_processed",
        "bid_processed_events",
        ["processed_at"],
    )

    op.create_table(
        "bid_public_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(length=80), nullable=False),
        sa.Column("origin_type", sa.String(length=24), nullable=False),
        sa.Column("source_event_id", sa.String(length=80), nullable=True),
        sa.Column("projection_key", sa.String(length=191), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=80), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sequence_no >= 1", name="ck_bid_public_events_sequence"),
        sa.CheckConstraint("resource_version >= 1", name="ck_bid_public_events_resource_version"),
        sa.CheckConstraint(
            f"event_type IN ({_in_values(PUBLIC_EVENT_TYPES)})",
            name="ck_bid_public_events_type",
        ),
        sa.CheckConstraint(
            f"resource_type IN ({_in_values(PUBLIC_RESOURCE_TYPES)})",
            name="ck_bid_public_events_resource_type",
        ),
        sa.CheckConstraint(
            "((origin_type = 'outbox' AND source_event_id IS NOT NULL) "
            "OR (origin_type = 'stream_control' AND source_event_id IS NULL "
            "AND event_type IN ('assessment.snapshot', 'stream.reset', 'stream.closed')))",
            name="ck_bid_public_events_origin",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_bid_public_events_retention"),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_public_events_assessment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_event_id"],
            ["bid_outbox_events.event_id"],
            name="fk_bid_public_events_source_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_public_events"),
        sa.UniqueConstraint("assessment_id", "sequence_no", name="uq_bid_public_events_sequence"),
        sa.UniqueConstraint("event_id", name="uq_bid_public_events_event_id"),
        sa.UniqueConstraint(
            "source_event_id",
            "projection_key",
            name="uq_bid_public_events_projection",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_public_events_assessment_expiry",
        "bid_public_events",
        ["assessment_id", "expires_at"],
    )
    op.create_index(
        "ix_bid_public_events_assessment_created",
        "bid_public_events",
        ["assessment_id", "created_at"],
    )
    op.create_index("ix_bid_public_events_type", "bid_public_events", ["event_type"])

    op.create_table(
        "bid_idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("http_method", sa.String(length=8), nullable=False),
        sa.Column("route_template", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=300), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="processing", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=80), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("response_ref", sa.String(length=512), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("processing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("http_method IN ('POST', 'PUT', 'PATCH', 'DELETE')", name="ck_bid_idempotency_records_method"),
        sa.CheckConstraint(
            f"status IN ({_in_values(IDEMPOTENCY_STATES)})",
            name="ck_bid_idempotency_records_status",
        ),
        sa.CheckConstraint(
            "LENGTH(idempotency_key) BETWEEN 16 AND 128",
            name="ck_bid_idempotency_records_key_length",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_idempotency_records_row_version"),
        sa.CheckConstraint("expires_at > created_at", name="ck_bid_idempotency_records_retention"),
        sa.CheckConstraint(
            "processing_expires_at IS NULL OR processing_expires_at > created_at",
            name="ck_bid_idempotency_records_processing_window",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR retryable = 1",
            name="ck_bid_idempotency_records_failed_retryable",
        ),
        sa.CheckConstraint(
            "((resource_type IS NULL AND resource_id IS NULL) "
            "OR (resource_type IS NOT NULL AND resource_id IS NOT NULL))",
            name="ck_bid_idempotency_records_resource_pair",
        ),
        sa.CheckConstraint(
            "response_status_code IS NULL OR response_status_code BETWEEN 100 AND 599",
            name="ck_bid_idempotency_records_http_status",
        ),
        sa.CheckConstraint(
            "((status = 'processing' AND processing_expires_at IS NOT NULL "
            "AND completed_at IS NULL AND response_status_code IS NULL AND response_hash IS NULL) "
            "OR (status = 'completed' AND processing_expires_at IS NULL "
            "AND completed_at IS NOT NULL AND response_status_code IS NOT NULL "
            "AND response_hash IS NOT NULL "
            "AND (response_snapshot_json IS NOT NULL OR response_ref IS NOT NULL)) "
            "OR (status = 'failed' AND processing_expires_at IS NULL "
            "AND completed_at IS NOT NULL AND failure_code IS NOT NULL "
            "AND response_status_code IS NULL AND response_snapshot_json IS NULL "
            "AND response_ref IS NULL AND response_hash IS NULL))",
            name="ck_bid_idempotency_records_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_bid_idempotency_records_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_idempotency_records"),
        sa.UniqueConstraint(
            "actor_id",
            "scope",
            "idempotency_key",
            name="uq_bid_idempotency_records_actor_scope_key",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_idempotency_records_status_expiry",
        "bid_idempotency_records",
        ["status", "processing_expires_at", "expires_at"],
    )
    op.create_index(
        "ix_bid_idempotency_records_request",
        "bid_idempotency_records",
        ["request_id"],
    )

    op.create_table(
        "bid_legacy_resource_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("legacy_system", sa.String(length=64), nullable=False),
        sa.Column("legacy_resource_type", sa.String(length=64), nullable=False),
        sa.Column("legacy_resource_id", sa.String(length=128), nullable=False),
        sa.Column("new_resource_type", sa.String(length=64), nullable=False),
        sa.Column("new_resource_id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=True),
        sa.Column("manifest_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("import_metadata_json", sa.JSON(), nullable=True),
        sa.Column("imported_by", sa.Integer(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "new_resource_type IN ('assessment', 'manifest', 'run')",
            name="ck_bid_legacy_resource_links_type",
        ),
        sa.CheckConstraint(
            "((new_resource_type = 'assessment' AND assessment_id IS NOT NULL "
            "AND assessment_id = new_resource_id AND manifest_id IS NULL AND run_id IS NULL) "
            "OR (new_resource_type = 'manifest' AND manifest_id IS NOT NULL "
            "AND manifest_id = new_resource_id AND assessment_id IS NULL AND run_id IS NULL) "
            "OR (new_resource_type = 'run' AND run_id IS NOT NULL "
            "AND run_id = new_resource_id AND assessment_id IS NULL AND manifest_id IS NULL))",
            name="ck_bid_legacy_resource_links_target",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_legacy_resource_links_assessment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id"],
            ["bid_document_manifests.id"],
            name="fk_bid_legacy_resource_links_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["bid_analysis_runs.id"],
            name="fk_bid_legacy_resource_links_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["imported_by"],
            ["users.id"],
            name="fk_bid_legacy_resource_links_imported_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_legacy_resource_links"),
        sa.UniqueConstraint(
            "legacy_system",
            "legacy_resource_type",
            "legacy_resource_id",
            "new_resource_type",
            "new_resource_id",
            name="uq_bid_legacy_resource_links_mapping",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_legacy_resource_links_legacy",
        "bid_legacy_resource_links",
        ["legacy_system", "legacy_resource_type", "legacy_resource_id"],
    )
    op.create_index(
        "ix_bid_legacy_resource_links_new",
        "bid_legacy_resource_links",
        ["new_resource_type", "new_resource_id"],
    )

    op.create_table(
        "bid_audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=True),
        sa.Column("actor_type", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_ref", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("metadata_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('user', 'system', 'service')",
            name="ck_bid_audit_log_actor_type",
        ),
        sa.CheckConstraint(
            "((actor_type = 'user' AND actor_id IS NOT NULL) "
            "OR (actor_type IN ('system', 'service') AND actor_id IS NULL))",
            name="ck_bid_audit_log_actor",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')",
            name="ck_bid_audit_log_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_audit_log_assessment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_bid_audit_log_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_audit_log"),
        sa.UniqueConstraint("record_hash", name="uq_bid_audit_log_record_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_audit_log_assessment_occurred",
        "bid_audit_log",
        ["assessment_id", "occurred_at"],
    )
    op.create_index(
        "ix_bid_audit_log_entity_occurred",
        "bid_audit_log",
        ["entity_type", "entity_id", "occurred_at"],
    )
    op.create_index("ix_bid_audit_log_request", "bid_audit_log", ["request_id"])
    op.create_index(
        "ix_bid_audit_log_actor_occurred",
        "bid_audit_log",
        ["actor_type", "actor_ref", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("bid_audit_log")
    op.drop_table("bid_legacy_resource_links")
    op.drop_table("bid_idempotency_records")
    op.drop_table("bid_public_events")
    op.drop_table("bid_processed_events")
    op.drop_table("bid_outbox_events")
