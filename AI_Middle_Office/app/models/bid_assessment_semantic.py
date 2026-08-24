"""RQ2-A semantic-index authority for bid-assessment evidence.

The relational rows freeze which immutable PDF-C3 Retrieval Children were
embedded and which provider/model profile produced each vector.  Vector values
remain in the configured semantic provider; the database keeps only stable
keys and hashes so every provider hit can be validated before it becomes an
Evidence MCP candidate.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
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

SEMANTIC_INDEX_STATES = ("queued", "building", "ready", "failed", "stale")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidEvidenceSemanticIndex(Base):
    __tablename__ = "bid_evidence_semantic_indexes"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(SEMANTIC_INDEX_STATES)})",
            name="ck_bid_semantic_indexes_status",
        ),
        CheckConstraint(
            "source_entry_count >= 0 AND entry_count >= 0",
            name="ck_bid_semantic_indexes_counts",
        ),
        CheckConstraint(
            "embedding_dimension > 0",
            name="ck_bid_semantic_indexes_dimension",
        ),
        CheckConstraint(
            "normalized_embeddings IN (0, 1)",
            name="ck_bid_semantic_indexes_normalized",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 "
            "AND attempt_count <= max_attempts AND fencing_token >= 0",
            name="ck_bid_semantic_indexes_attempts",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="ck_bid_semantic_indexes_row_version",
        ),
        CheckConstraint(
            "((status = 'building' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) "
            "OR (status <> 'building'))",
            name="ck_bid_semantic_indexes_lease",
        ),
        CheckConstraint(
            "((status = 'ready' AND result_hash IS NOT NULL "
            "AND finished_at IS NOT NULL AND error_code IS NULL "
            "AND entry_count = source_entry_count) OR (status <> 'ready'))",
            name="ck_bid_semantic_indexes_ready",
        ),
        CheckConstraint(
            "((status = 'failed' AND error_code IS NOT NULL "
            "AND finished_at IS NOT NULL) OR (status <> 'failed'))",
            name="ck_bid_semantic_indexes_failed",
        ),
        ForeignKeyConstraint(
            ["retrieval_index_id"],
            ["bid_evidence_retrieval_indexes.id"],
            name="fk_bid_semantic_indexes_retrieval",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "retrieval_index_id",
            "semantic_profile_version",
            name="uq_bid_semantic_indexes_input",
        ),
        UniqueConstraint(
            "document_version_id",
            "semantic_profile_version",
            "id",
            "retrieval_index_id",
            name="uq_bid_semantic_indexes_head_target",
        ),
        Index(
            "ix_bid_semantic_indexes_queue",
            "status",
            "requested_at",
        ),
        Index(
            "ix_bid_semantic_indexes_source",
            "retrieval_index_id",
            "semantic_profile_version",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    document_version_id = Column(String(36), nullable=False)
    retrieval_index_id = Column(String(36), nullable=False)
    retrieval_profile_version = Column(String(100), nullable=False)
    semantic_profile_version = Column(String(100), nullable=False)
    provider_id = Column(String(80), nullable=False)
    embedding_model_id = Column(String(200), nullable=False)
    embedding_model_revision = Column(String(100), nullable=False)
    embedding_dimension = Column(Integer, nullable=False)
    distance_metric = Column(String(24), nullable=False)
    normalized_embeddings = Column(Integer, nullable=False, default=1, server_default="1")
    vector_namespace = Column(String(100), nullable=False)
    provider_request_id = Column(String(100), nullable=False)
    source_result_hash = Column(String(64), nullable=False)
    source_entry_count = Column(Integer, nullable=False)
    input_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="queued", server_default="queued")
    entry_count = Column(Integer, nullable=False, default=0, server_default="0")
    result_hash = Column(String(64), nullable=True)
    error_code = Column(String(100), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=5, server_default="5")
    fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    requested_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEvidenceSemanticEntry(Base):
    __tablename__ = "bid_evidence_semantic_entries"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_bid_semantic_entries_ordinal"),
        CheckConstraint(
            "vector_dimension > 0",
            name="ck_bid_semantic_entries_dimension",
        ),
        ForeignKeyConstraint(
            ["semantic_index_id"],
            ["bid_evidence_semantic_indexes.id"],
            name="fk_bid_semantic_entries_index",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["retrieval_entry_id"],
            ["bid_evidence_retrieval_entries.id"],
            name="fk_bid_semantic_entries_retrieval_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["retrieval_child_id"],
            ["bid_evidence_fragments.id"],
            name="fk_bid_semantic_entries_child",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "semantic_index_id",
            "retrieval_entry_id",
            name="uq_bid_semantic_entries_retrieval",
        ),
        UniqueConstraint(
            "semantic_index_id",
            "provider_record_id",
            name="uq_bid_semantic_entries_provider_record",
        ),
        UniqueConstraint(
            "semantic_index_id",
            "entry_hash",
            name="uq_bid_semantic_entries_hash",
        ),
        Index(
            "ix_bid_semantic_entries_index_order",
            "semantic_index_id",
            "ordinal",
        ),
        Index(
            "ix_bid_semantic_entries_child",
            "retrieval_child_id",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    semantic_index_id = Column(String(36), nullable=False)
    retrieval_index_id = Column(String(36), nullable=False)
    retrieval_entry_id = Column(String(36), nullable=False)
    retrieval_child_id = Column(String(36), nullable=False)
    retrieval_child_key = Column(String(100), nullable=False)
    source_entry_hash = Column(String(64), nullable=False)
    embedding_text_hash = Column(String(64), nullable=False)
    provider_record_id = Column(String(64), nullable=False)
    vector_hash = Column(String(64), nullable=False)
    vector_dimension = Column(Integer, nullable=False)
    ordinal = Column(Integer, nullable=False)
    entry_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEvidenceSemanticHead(Base):
    __tablename__ = "bid_evidence_semantic_heads"
    __table_args__ = (
        CheckConstraint(
            "row_version >= 1",
            name="ck_bid_semantic_heads_row_version",
        ),
        ForeignKeyConstraint(
            [
                "document_version_id",
                "semantic_profile_version",
                "current_semantic_index_id",
                "current_retrieval_index_id",
            ],
            [
                "bid_evidence_semantic_indexes.document_version_id",
                "bid_evidence_semantic_indexes.semantic_profile_version",
                "bid_evidence_semantic_indexes.id",
                "bid_evidence_semantic_indexes.retrieval_index_id",
            ],
            name="fk_bid_semantic_heads_current",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_bid_semantic_heads_current",
            "current_semantic_index_id",
        ),
        TABLE_OPTIONS,
    )

    document_version_id = Column(String(36), primary_key=True)
    semantic_profile_version = Column(String(100), primary_key=True)
    current_semantic_index_id = Column(String(36), nullable=False)
    current_retrieval_index_id = Column(String(36), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
