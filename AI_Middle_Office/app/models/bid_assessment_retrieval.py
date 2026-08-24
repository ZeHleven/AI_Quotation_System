"""Role-aware retrieval-index authority for bid-assessment evidence.

The index is a deterministic derivative of one immutable Phase 2 ParseRun.
Mutable heads select the ready derivative for one document/profile pair; MCP
queries must also prove that the selected index still matches ParseHead.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
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

RETRIEVAL_INDEX_STATES = ("queued", "building", "ready", "failed", "stale")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidEvidenceRetrievalIndex(Base):
    __tablename__ = "bid_evidence_retrieval_indexes"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(RETRIEVAL_INDEX_STATES)})",
            name="ck_bid_evidence_retrieval_indexes_status",
        ),
        CheckConstraint(
            "parent_count >= 0 AND child_count >= 0 AND atom_count >= 0 "
            "AND entry_count >= 0",
            name="ck_bid_evidence_retrieval_indexes_counts",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="ck_bid_evidence_retrieval_indexes_row_version",
        ),
        CheckConstraint(
            "((status = 'ready' AND result_hash IS NOT NULL AND finished_at IS NOT NULL "
            "AND error_code IS NULL) OR (status <> 'ready'))",
            name="ck_bid_evidence_retrieval_indexes_ready",
        ),
        CheckConstraint(
            "((status = 'failed' AND error_code IS NOT NULL AND finished_at IS NOT NULL) "
            "OR (status <> 'failed'))",
            name="ck_bid_evidence_retrieval_indexes_failed",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "parse_run_id"],
            [
                "bid_document_parse_runs.document_version_id",
                "bid_document_parse_runs.id",
            ],
            name="fk_bid_evidence_retrieval_indexes_parse_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "document_version_id",
            "parse_run_id",
            "retrieval_profile_version",
            name="uq_bid_evidence_retrieval_indexes_input",
        ),
        UniqueConstraint(
            "document_version_id",
            "retrieval_profile_version",
            "id",
            "parse_run_id",
            name="uq_bid_evidence_retrieval_indexes_head_target",
        ),
        Index(
            "ix_bid_evidence_retrieval_indexes_queue",
            "status",
            "requested_at",
        ),
        Index(
            "ix_bid_evidence_retrieval_indexes_parse_profile",
            "parse_run_id",
            "retrieval_profile_version",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    document_version_id = Column(String(36), nullable=False)
    parse_run_id = Column(String(36), nullable=False)
    retrieval_profile_version = Column(String(100), nullable=False)
    role_contract_version = Column(String(80), nullable=False)
    source_result_hash = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="queued", server_default="queued")
    parent_count = Column(Integer, nullable=False, default=0, server_default="0")
    child_count = Column(Integer, nullable=False, default=0, server_default="0")
    atom_count = Column(Integer, nullable=False, default=0, server_default="0")
    entry_count = Column(Integer, nullable=False, default=0, server_default="0")
    result_hash = Column(String(64), nullable=True)
    error_code = Column(String(100), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    requested_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEvidenceRetrievalEntry(Base):
    __tablename__ = "bid_evidence_retrieval_entries"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_bid_evidence_retrieval_entries_ordinal"),
        CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_bid_evidence_retrieval_entries_pages",
        ),
        CheckConstraint(
            "source_atom_count >= 1",
            name="ck_bid_evidence_retrieval_entries_atom_count",
        ),
        ForeignKeyConstraint(
            ["index_id"],
            ["bid_evidence_retrieval_indexes.id"],
            name="fk_bid_evidence_retrieval_entries_index",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parse_run_id", "retrieval_child_id"],
            ["bid_evidence_fragments.parse_run_id", "bid_evidence_fragments.id"],
            name="fk_bid_evidence_retrieval_entries_child",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parse_run_id", "section_parent_id"],
            ["bid_evidence_fragments.parse_run_id", "bid_evidence_fragments.id"],
            name="fk_bid_evidence_retrieval_entries_parent",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "index_id",
            "retrieval_child_id",
            name="uq_bid_evidence_retrieval_entries_child",
        ),
        UniqueConstraint(
            "index_id",
            "retrieval_child_key",
            name="uq_bid_evidence_retrieval_entries_child_key",
        ),
        UniqueConstraint(
            "index_id",
            "entry_hash",
            name="uq_bid_evidence_retrieval_entries_hash",
        ),
        Index(
            "ix_bid_evidence_retrieval_entries_index_order",
            "index_id",
            "ordinal",
        ),
        Index(
            "ix_bid_evidence_retrieval_entries_parent",
            "index_id",
            "section_parent_id",
            "ordinal",
        ),
        Index(
            "ix_bid_evidence_retrieval_entries_document",
            "document_version_id",
            "parse_run_id",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    index_id = Column(String(36), nullable=False)
    document_version_id = Column(String(36), nullable=False)
    parse_run_id = Column(String(36), nullable=False)
    retrieval_child_id = Column(String(36), nullable=False)
    retrieval_child_key = Column(String(100), nullable=False)
    section_parent_id = Column(String(36), nullable=False)
    section_parent_key = Column(String(100), nullable=False)
    ordinal = Column(Integer, nullable=False)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    retrieval_text = Column(Text, nullable=False)
    retrieval_hash = Column(String(64), nullable=False)
    child_text_hash = Column(String(64), nullable=False)
    source_atom_ids_json = Column(JSON, nullable=False)
    source_atom_keys_json = Column(JSON, nullable=False)
    source_atom_count = Column(Integer, nullable=False)
    source_atoms_hash = Column(String(64), nullable=False)
    entry_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEvidenceRetrievalHead(Base):
    __tablename__ = "bid_evidence_retrieval_heads"
    __table_args__ = (
        CheckConstraint(
            "row_version >= 1",
            name="ck_bid_evidence_retrieval_heads_row_version",
        ),
        ForeignKeyConstraint(
            [
                "document_version_id",
                "retrieval_profile_version",
                "current_index_id",
                "current_parse_run_id",
            ],
            [
                "bid_evidence_retrieval_indexes.document_version_id",
                "bid_evidence_retrieval_indexes.retrieval_profile_version",
                "bid_evidence_retrieval_indexes.id",
                "bid_evidence_retrieval_indexes.parse_run_id",
            ],
            name="fk_bid_evidence_retrieval_heads_current_index",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_bid_evidence_retrieval_heads_current_index",
            "current_index_id",
        ),
        TABLE_OPTIONS,
    )

    document_version_id = Column(String(36), primary_key=True)
    retrieval_profile_version = Column(String(100), primary_key=True)
    current_index_id = Column(String(36), nullable=False)
    current_parse_run_id = Column(String(36), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
