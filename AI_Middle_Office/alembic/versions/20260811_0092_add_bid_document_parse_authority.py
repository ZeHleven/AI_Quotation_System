"""add Phase 2 bid document parse authority

Revision ID: 20260811_0092
Revises: 20260811_0091
Create Date: 2026-08-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260811_0092"
down_revision: Union[str, None] = "20260811_0091"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

PREVIOUS_OUTBOX_EVENT_TYPES = (
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
OUTBOX_EVENT_TYPES = (
    *PREVIOUS_OUTBOX_EVENT_TYPES,
    "bid.manifest.parse_set_ready.v1",
    "bid.lot_detection.requested.v1",
    "bid.lot_detection.failed.v1",
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "bid_document_parse_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("parser_profile_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_ref", sa.String(length=1024), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("quality_grade", sa.String(length=16), nullable=True),
        sa.Column("quality_score", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sheet_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ocr_status", sa.String(length=24), server_default="not_requested", nullable=False),
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_bid_document_parse_runs_status",
        ),
        sa.CheckConstraint(
            "quality_grade IS NULL OR quality_grade IN ('high', 'medium', 'low')",
            name="ck_bid_document_parse_runs_quality_grade",
        ),
        sa.CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
            name="ck_bid_document_parse_runs_quality_score",
        ),
        sa.CheckConstraint(
            "ocr_status IN ('not_applicable', 'not_requested', 'queued', 'running', "
            "'succeeded', 'partial', 'failed')",
            name="ck_bid_document_parse_runs_ocr_status",
        ),
        sa.CheckConstraint(
            "page_count >= 0 AND sheet_count >= 0 AND warning_count >= 0",
            name="ck_bid_document_parse_runs_counts",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_document_parse_runs_row_version"),
        sa.CheckConstraint(
            "((status = 'queued' AND started_at IS NULL AND finished_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'partial') AND started_at IS NOT NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'failed' AND finished_at IS NOT NULL))",
            name="ck_bid_document_parse_runs_timestamps",
        ),
        sa.CheckConstraint(
            "((status IN ('succeeded', 'partial') AND result_hash IS NOT NULL "
            "AND quality_grade IS NOT NULL AND quality_score IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'partial')))",
            name="ck_bid_document_parse_runs_result",
        ),
        sa.CheckConstraint(
            "((status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL))",
            name="ck_bid_document_parse_runs_error",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["bid_document_versions.id"],
            name="fk_bid_document_parse_runs_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_document_parse_runs"),
        sa.UniqueConstraint(
            "document_version_id",
            "parser_profile_version",
            "input_hash",
            name="uq_bid_document_parse_runs_input",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "id",
            name="uq_bid_document_parse_runs_version_id",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_document_parse_runs_version_status",
        "bid_document_parse_runs",
        ["document_version_id", "status"],
    )
    op.create_index(
        "ix_bid_document_parse_runs_status_requested",
        "bid_document_parse_runs",
        ["status", "requested_at"],
    )

    op.create_table(
        "bid_document_parse_heads",
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("current_run_id", sa.String(length=36), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_document_parse_heads_row_version"),
        sa.ForeignKeyConstraint(
            ["document_version_id", "current_run_id"],
            [
                "bid_document_parse_runs.document_version_id",
                "bid_document_parse_runs.id",
            ],
            name="fk_bid_document_parse_heads_current_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("document_version_id", name="pk_bid_document_parse_heads"),
        sa.UniqueConstraint("current_run_id", name="uq_bid_document_parse_heads_current_run"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_document_parse_heads_current_run",
        "bid_document_parse_heads",
        ["current_run_id"],
    )

    op.create_table(
        "bid_document_parse_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("error_class", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_no >= 1", name="ck_bid_document_parse_attempts_number"),
        sa.CheckConstraint(
            "status IN ('leased', 'running', 'succeeded', 'failed', 'expired', 'cancelled')",
            name="ck_bid_document_parse_attempts_status",
        ),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_document_parse_attempts_fencing"),
        sa.CheckConstraint(
            "lease_owner IS NOT NULL AND lease_until IS NOT NULL",
            name="ck_bid_document_parse_attempts_lease",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_bid_document_parse_attempts_time_order",
        ),
        sa.CheckConstraint(
            "((status IN ('succeeded', 'failed', 'expired', 'cancelled') "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('leased', 'running') AND finished_at IS NULL))",
            name="ck_bid_document_parse_attempts_terminal",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_document_parse_attempts_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_document_parse_attempts"),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_bid_document_parse_attempts_number"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_document_parse_attempts_run_id"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_document_parse_attempts_lease",
        "bid_document_parse_attempts",
        ["status", "lease_until"],
    )
    op.create_index(
        "ix_bid_document_parse_attempts_run_status",
        "bid_document_parse_attempts",
        ["run_id", "status"],
    )

    op.create_table(
        "bid_document_parse_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sequence_no >= 1", name="ck_bid_document_parse_events_sequence"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_document_parse_events_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "attempt_id"],
            ["bid_document_parse_attempts.run_id", "bid_document_parse_attempts.id"],
            name="fk_bid_document_parse_events_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_document_parse_events"),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_bid_document_parse_events_sequence"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_document_parse_events_run_created",
        "bid_document_parse_events",
        ["run_id", "created_at"],
    )

    op.create_table(
        "bid_document_parse_units",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("unit_type", sa.String(length=16), nullable=False),
        sa.Column("unit_key", sa.String(length=191), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("sheet_index", sa.Integer(), nullable=True),
        sa.Column("sheet_name", sa.String(length=191), nullable=True),
        sa.Column("cell_range", sa.String(length=128), nullable=True),
        sa.Column("image_index", sa.Integer(), nullable=True),
        sa.Column("section_path_json", sa.JSON(), nullable=True),
        sa.Column("content_source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=True),
        sa.Column("text_length", sa.Integer(), nullable=True),
        sa.Column("result_ref", sa.String(length=1024), nullable=True),
        sa.Column("ocr_status", sa.String(length=24), nullable=False),
        sa.Column("ocr_engine_version", sa.String(length=128), nullable=True),
        sa.Column("ocr_confidence", sa.Numeric(10, 6), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "unit_type IN ('document', 'page', 'sheet', 'image')",
            name="ck_bid_document_parse_units_type",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'partial', 'failed', 'skipped')",
            name="ck_bid_document_parse_units_status",
        ),
        sa.CheckConstraint(
            "content_source IN ('native', 'ocr', 'mixed', 'none')",
            name="ck_bid_document_parse_units_content_source",
        ),
        sa.CheckConstraint(
            "ocr_status IN ('not_applicable', 'not_requested', 'queued', 'running', "
            "'succeeded', 'partial', 'failed')",
            name="ck_bid_document_parse_units_ocr_status",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_bid_document_parse_units_ordinal"),
        sa.CheckConstraint(
            "text_length IS NULL OR text_length >= 0",
            name="ck_bid_document_parse_units_text_length",
        ),
        sa.CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_bid_document_parse_units_ocr_confidence",
        ),
        sa.CheckConstraint(
            "((unit_type = 'document' AND page_no IS NULL AND sheet_index IS NULL "
            "AND sheet_name IS NULL AND image_index IS NULL AND cell_range IS NULL) OR "
            "(unit_type = 'page' AND page_no IS NOT NULL AND page_no >= 1 "
            "AND sheet_index IS NULL AND sheet_name IS NULL AND image_index IS NULL "
            "AND cell_range IS NULL) OR "
            "(unit_type = 'sheet' AND page_no IS NULL AND sheet_index IS NOT NULL "
            "AND sheet_index >= 0 AND sheet_name IS NOT NULL AND image_index IS NULL) OR "
            "(unit_type = 'image' AND page_no IS NULL AND sheet_index IS NULL "
            "AND sheet_name IS NULL AND image_index IS NOT NULL AND image_index >= 0 "
            "AND cell_range IS NULL))",
            name="ck_bid_document_parse_units_locator",
        ),
        sa.CheckConstraint(
            "((content_source IN ('ocr', 'mixed') AND "
            "ocr_status IN ('queued', 'running', 'succeeded', 'partial', 'failed')) OR "
            "(content_source NOT IN ('ocr', 'mixed')))",
            name="ck_bid_document_parse_units_ocr_source",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_document_parse_units_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_document_parse_units"),
        sa.UniqueConstraint("run_id", "unit_type", "unit_key", name="uq_bid_document_parse_units_key"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_document_parse_units_run_id"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_document_parse_units_run_ordinal",
        "bid_document_parse_units",
        ["run_id", "ordinal"],
    )
    op.create_index(
        "ix_bid_document_parse_units_run_status",
        "bid_document_parse_units",
        ["run_id", "status"],
    )

    op.create_table(
        "bid_evidence_fragments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("parse_run_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("parse_unit_id", sa.String(length=36), nullable=False),
        sa.Column("locator_type", sa.String(length=24), nullable=False),
        sa.Column("locator_json", sa.JSON(), nullable=False),
        sa.Column("locator_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("ordinal", sa.Integer(), server_default="0", nullable=False),
        sa.Column("object_ref", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "locator_type IN ('document', 'page_bbox', 'sheet_range', 'image_bbox', 'section')",
            name="ck_bid_evidence_fragments_locator_type",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_bid_evidence_fragments_ordinal"),
        sa.ForeignKeyConstraint(
            ["parse_run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_evidence_fragments_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["bid_document_versions.id"],
            name="fk_bid_evidence_fragments_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parse_unit_id"],
            ["bid_document_parse_units.id"],
            name="fk_bid_evidence_fragments_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "parse_run_id"],
            [
                "bid_document_parse_runs.document_version_id",
                "bid_document_parse_runs.id",
            ],
            name="fk_bid_evidence_fragments_version_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parse_run_id", "parse_unit_id"],
            ["bid_document_parse_units.run_id", "bid_document_parse_units.id"],
            name="fk_bid_evidence_fragments_run_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parse_run_id", "parent_id"],
            ["bid_evidence_fragments.parse_run_id", "bid_evidence_fragments.id"],
            name="fk_bid_evidence_fragments_parent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_evidence_fragments"),
        sa.UniqueConstraint("parse_run_id", "id", name="uq_bid_evidence_fragments_run_id"),
        sa.UniqueConstraint(
            "id",
            "document_version_id",
            name="uq_bid_evidence_fragments_id_version",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "parse_run_id",
            "locator_hash",
            "text_hash",
            name="uq_bid_evidence_fragments_locator_text",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_evidence_fragments_run_unit",
        "bid_evidence_fragments",
        ["parse_run_id", "parse_unit_id"],
    )
    op.create_index(
        "ix_bid_evidence_fragments_version",
        "bid_evidence_fragments",
        ["document_version_id"],
    )
    op.create_index(
        "ix_bid_evidence_fragments_text_hash",
        "bid_evidence_fragments",
        ["text_hash"],
    )

    op.drop_constraint("ck_bid_outbox_events_type", "bid_outbox_events", type_="check")
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(OUTBOX_EVENT_TYPES)})",
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0092 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable parse/evidence data checks"
        )

    bind = op.get_bind()
    tables = (
        "bid_document_parse_heads",
        "bid_document_parse_attempts",
        "bid_document_parse_events",
        "bid_document_parse_units",
        "bid_evidence_fragments",
        "bid_document_parse_runs",
    )
    nonempty = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in tables
    }
    event_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_outbox_events WHERE event_type IN "
                "('bid.manifest.parse_set_ready.v1', "
                "'bid.lot_detection.requested.v1', "
                "'bid.lot_detection.failed.v1')"
            )
        ).scalar()
        or 0
    )
    if any(nonempty.values()) or event_count:
        raise RuntimeError(
            "0092 downgrade would erase immutable document parse/evidence lineage; "
            "export and explicitly remove Phase 2 rows/events first"
        )

    op.drop_constraint("ck_bid_outbox_events_type", "bid_outbox_events", type_="check")
    op.create_check_constraint(
        "ck_bid_outbox_events_type",
        "bid_outbox_events",
        f"event_type IN ({_in_values(PREVIOUS_OUTBOX_EVENT_TYPES)})",
    )
    op.drop_table("bid_evidence_fragments")
    op.drop_table("bid_document_parse_units")
    op.drop_table("bid_document_parse_events")
    op.drop_table("bid_document_parse_attempts")
    op.drop_table("bid_document_parse_heads")
    op.drop_table("bid_document_parse_runs")
