"""Phase 2 document-parse authority for the isolated bid-assessment domain.

The models in this module deliberately do not reuse the legacy bidding
``bid_parse_runs`` table or the legacy tender-evidence persistence domain.
Immutable document versions own logical parse runs; mutable heads select the
currently authoritative run without mutating the document version itself.
"""
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
    Numeric,
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

PARSE_RUN_STATES = ("queued", "running", "succeeded", "partial", "failed")
PARSE_ATTEMPT_STATES = (
    "leased",
    "running",
    "succeeded",
    "failed",
    "expired",
    "cancelled",
)
PARSE_UNIT_TYPES = ("document", "page", "sheet", "image")
PARSE_UNIT_STATES = ("succeeded", "partial", "failed", "skipped")
CONTENT_SOURCES = ("native", "ocr", "mixed", "none")
OCR_STATES = (
    "not_applicable",
    "not_requested",
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
)
QUALITY_GRADES = ("high", "medium", "low")
EVIDENCE_LOCATOR_TYPES = (
    "document",
    "page_bbox",
    "sheet_range",
    "image_bbox",
    "section",
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidDocumentParseRun(Base):
    __tablename__ = "bid_document_parse_runs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(PARSE_RUN_STATES)})",
            name="ck_bid_document_parse_runs_status",
        ),
        CheckConstraint(
            f"quality_grade IS NULL OR quality_grade IN ({_in_values(QUALITY_GRADES)})",
            name="ck_bid_document_parse_runs_quality_grade",
        ),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
            name="ck_bid_document_parse_runs_quality_score",
        ),
        CheckConstraint(
            f"ocr_status IN ({_in_values(OCR_STATES)})",
            name="ck_bid_document_parse_runs_ocr_status",
        ),
        CheckConstraint(
            "page_count >= 0 AND sheet_count >= 0 AND warning_count >= 0",
            name="ck_bid_document_parse_runs_counts",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_document_parse_runs_row_version"),
        CheckConstraint(
            "((status = 'queued' AND started_at IS NULL AND finished_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'partial') AND started_at IS NOT NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'failed' AND finished_at IS NOT NULL))",
            name="ck_bid_document_parse_runs_timestamps",
        ),
        CheckConstraint(
            "((status IN ('succeeded', 'partial') AND result_hash IS NOT NULL "
            "AND quality_grade IS NOT NULL AND quality_score IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'partial')))",
            name="ck_bid_document_parse_runs_result",
        ),
        CheckConstraint(
            "((status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL))",
            name="ck_bid_document_parse_runs_error",
        ),
        ForeignKeyConstraint(
            ["document_version_id"],
            ["bid_document_versions.id"],
            name="fk_bid_document_parse_runs_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "document_version_id",
            "parser_profile_version",
            "input_hash",
            name="uq_bid_document_parse_runs_input",
        ),
        UniqueConstraint(
            "document_version_id",
            "id",
            name="uq_bid_document_parse_runs_version_id",
        ),
        Index(
            "ix_bid_document_parse_runs_version_status",
            "document_version_id",
            "status",
        ),
        Index("ix_bid_document_parse_runs_status_requested", "status", "requested_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    document_version_id = Column(String(36), nullable=False)
    parser_profile_version = Column(String(80), nullable=False)
    input_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="queued", server_default="queued")
    retryable = Column(Boolean, nullable=False, default=True, server_default="1")
    requested_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    result_ref = Column(String(1024), nullable=True)
    result_hash = Column(String(64), nullable=True)
    quality_grade = Column(String(16), nullable=True)
    quality_score = Column(Integer, nullable=True)
    page_count = Column(Integer, nullable=False, default=0, server_default="0")
    sheet_count = Column(Integer, nullable=False, default=0, server_default="0")
    ocr_status = Column(
        String(24),
        nullable=False,
        default="not_requested",
        server_default="not_requested",
    )
    warning_count = Column(Integer, nullable=False, default=0, server_default="0")
    warnings_json = Column(JSON, nullable=True)
    error_code = Column(String(100), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidDocumentParseHead(Base):
    __tablename__ = "bid_document_parse_heads"
    __table_args__ = (
        CheckConstraint("row_version >= 1", name="ck_bid_document_parse_heads_row_version"),
        ForeignKeyConstraint(
            ["document_version_id", "current_run_id"],
            [
                "bid_document_parse_runs.document_version_id",
                "bid_document_parse_runs.id",
            ],
            name="fk_bid_document_parse_heads_current_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("current_run_id", name="uq_bid_document_parse_heads_current_run"),
        Index("ix_bid_document_parse_heads_current_run", "current_run_id"),
        TABLE_OPTIONS,
    )

    document_version_id = Column(String(36), primary_key=True)
    current_run_id = Column(String(36), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidDocumentParseAttempt(Base):
    __tablename__ = "bid_document_parse_attempts"
    __table_args__ = (
        CheckConstraint("attempt_no >= 1", name="ck_bid_document_parse_attempts_number"),
        CheckConstraint(
            f"status IN ({_in_values(PARSE_ATTEMPT_STATES)})",
            name="ck_bid_document_parse_attempts_status",
        ),
        CheckConstraint("fencing_token >= 1", name="ck_bid_document_parse_attempts_fencing"),
        CheckConstraint(
            "lease_owner IS NOT NULL AND lease_until IS NOT NULL",
            name="ck_bid_document_parse_attempts_lease",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_bid_document_parse_attempts_time_order",
        ),
        CheckConstraint(
            "((status IN ('succeeded', 'failed', 'expired', 'cancelled') "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('leased', 'running') AND finished_at IS NULL))",
            name="ck_bid_document_parse_attempts_terminal",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_document_parse_attempts_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "attempt_no", name="uq_bid_document_parse_attempts_number"),
        UniqueConstraint("run_id", "id", name="uq_bid_document_parse_attempts_run_id"),
        Index("ix_bid_document_parse_attempts_lease", "status", "lease_until"),
        Index("ix_bid_document_parse_attempts_run_status", "run_id", "status"),
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


class BidDocumentParseEvent(Base):
    __tablename__ = "bid_document_parse_events"
    __table_args__ = (
        CheckConstraint("sequence_no >= 1", name="ck_bid_document_parse_events_sequence"),
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_document_parse_events_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "attempt_id"],
            ["bid_document_parse_attempts.run_id", "bid_document_parse_attempts.id"],
            name="fk_bid_document_parse_events_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "sequence_no", name="uq_bid_document_parse_events_sequence"),
        Index("ix_bid_document_parse_events_run_created", "run_id", "created_at"),
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


class BidDocumentParseUnit(Base):
    __tablename__ = "bid_document_parse_units"
    __table_args__ = (
        CheckConstraint(
            f"unit_type IN ({_in_values(PARSE_UNIT_TYPES)})",
            name="ck_bid_document_parse_units_type",
        ),
        CheckConstraint(
            f"status IN ({_in_values(PARSE_UNIT_STATES)})",
            name="ck_bid_document_parse_units_status",
        ),
        CheckConstraint(
            f"content_source IN ({_in_values(CONTENT_SOURCES)})",
            name="ck_bid_document_parse_units_content_source",
        ),
        CheckConstraint(
            f"ocr_status IN ({_in_values(OCR_STATES)})",
            name="ck_bid_document_parse_units_ocr_status",
        ),
        CheckConstraint("ordinal >= 0", name="ck_bid_document_parse_units_ordinal"),
        CheckConstraint(
            "text_length IS NULL OR text_length >= 0",
            name="ck_bid_document_parse_units_text_length",
        ),
        CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_bid_document_parse_units_ocr_confidence",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "((content_source IN ('ocr', 'mixed') AND "
            "ocr_status IN ('queued', 'running', 'succeeded', 'partial', 'failed')) OR "
            "(content_source NOT IN ('ocr', 'mixed')))",
            name="ck_bid_document_parse_units_ocr_source",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_document_parse_units_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "unit_type", "unit_key", name="uq_bid_document_parse_units_key"),
        UniqueConstraint("run_id", "id", name="uq_bid_document_parse_units_run_id"),
        Index("ix_bid_document_parse_units_run_ordinal", "run_id", "ordinal"),
        Index("ix_bid_document_parse_units_run_status", "run_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    unit_type = Column(String(16), nullable=False)
    unit_key = Column(String(191), nullable=False)
    ordinal = Column(Integer, nullable=False)
    page_no = Column(Integer, nullable=True)
    sheet_index = Column(Integer, nullable=True)
    sheet_name = Column(String(191), nullable=True)
    cell_range = Column(String(128), nullable=True)
    image_index = Column(Integer, nullable=True)
    section_path_json = Column(JSON, nullable=True)
    content_source = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False)
    text_hash = Column(String(64), nullable=True)
    text_length = Column(Integer, nullable=True)
    result_ref = Column(String(1024), nullable=True)
    ocr_status = Column(String(24), nullable=False)
    ocr_engine_version = Column(String(128), nullable=True)
    ocr_confidence = Column(Numeric(10, 6), nullable=True)
    warnings_json = Column(JSON, nullable=True)
    metrics_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEvidenceFragment(Base):
    __tablename__ = "bid_evidence_fragments"
    __table_args__ = (
        CheckConstraint(
            f"locator_type IN ({_in_values(EVIDENCE_LOCATOR_TYPES)})",
            name="ck_bid_evidence_fragments_locator_type",
        ),
        CheckConstraint("ordinal >= 0", name="ck_bid_evidence_fragments_ordinal"),
        ForeignKeyConstraint(
            ["parse_run_id"],
            ["bid_document_parse_runs.id"],
            name="fk_bid_evidence_fragments_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_version_id"],
            ["bid_document_versions.id"],
            name="fk_bid_evidence_fragments_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parse_unit_id"],
            ["bid_document_parse_units.id"],
            name="fk_bid_evidence_fragments_unit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "parse_run_id"],
            [
                "bid_document_parse_runs.document_version_id",
                "bid_document_parse_runs.id",
            ],
            name="fk_bid_evidence_fragments_version_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parse_run_id", "parse_unit_id"],
            ["bid_document_parse_units.run_id", "bid_document_parse_units.id"],
            name="fk_bid_evidence_fragments_run_unit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parse_run_id", "parent_id"],
            ["bid_evidence_fragments.parse_run_id", "bid_evidence_fragments.id"],
            name="fk_bid_evidence_fragments_parent",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("parse_run_id", "id", name="uq_bid_evidence_fragments_run_id"),
        UniqueConstraint(
            "id",
            "document_version_id",
            name="uq_bid_evidence_fragments_id_version",
        ),
        UniqueConstraint(
            "document_version_id",
            "parse_run_id",
            "locator_hash",
            "text_hash",
            name="uq_bid_evidence_fragments_locator_text",
        ),
        Index("ix_bid_evidence_fragments_run_unit", "parse_run_id", "parse_unit_id"),
        Index("ix_bid_evidence_fragments_version", "document_version_id"),
        Index("ix_bid_evidence_fragments_text_hash", "text_hash"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    parse_run_id = Column(String(36), nullable=False)
    document_version_id = Column(String(36), nullable=False)
    parse_unit_id = Column(String(36), nullable=False)
    locator_type = Column(String(24), nullable=False)
    locator_json = Column(JSON, nullable=False)
    locator_hash = Column(String(64), nullable=False)
    normalized_text = Column(Text, nullable=False)
    text_hash = Column(String(64), nullable=False)
    parent_id = Column(String(36), nullable=True)
    ordinal = Column(Integer, nullable=False, default=0, server_default="0")
    object_ref = Column(String(1024), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
