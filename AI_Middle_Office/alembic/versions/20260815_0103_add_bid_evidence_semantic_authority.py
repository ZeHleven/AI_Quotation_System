"""add bid evidence semantic-index authority

Revision ID: 20260815_0103
Revises: 20260814_0102
Create Date: 2026-08-15
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260815_0103"
down_revision: Union[str, None] = "20260814_0102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_evidence_semantic_indexes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_index_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_profile_version", sa.String(length=100), nullable=False),
        sa.Column("semantic_profile_version", sa.String(length=100), nullable=False),
        sa.Column("provider_id", sa.String(length=80), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=200), nullable=False),
        sa.Column("embedding_model_revision", sa.String(length=100), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(length=24), nullable=False),
        sa.Column("normalized_embeddings", sa.Integer(), server_default="1", nullable=False),
        sa.Column("vector_namespace", sa.String(length=100), nullable=False),
        sa.Column("provider_request_id", sa.String(length=100), nullable=False),
        sa.Column("source_result_hash", sa.String(length=64), nullable=False),
        sa.Column("source_entry_count", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("entry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'building', 'ready', 'failed', 'stale')",
            name="ck_bid_semantic_indexes_status",
        ),
        sa.CheckConstraint(
            "source_entry_count >= 0 AND entry_count >= 0",
            name="ck_bid_semantic_indexes_counts",
        ),
        sa.CheckConstraint("embedding_dimension > 0", name="ck_bid_semantic_indexes_dimension"),
        sa.CheckConstraint(
            "normalized_embeddings IN (0, 1)",
            name="ck_bid_semantic_indexes_normalized",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts "
            "AND fencing_token >= 0",
            name="ck_bid_semantic_indexes_attempts",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_semantic_indexes_row_version"),
        sa.CheckConstraint(
            "((status = 'building' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) "
            "OR (status <> 'building'))",
            name="ck_bid_semantic_indexes_lease",
        ),
        sa.CheckConstraint(
            "((status = 'ready' AND result_hash IS NOT NULL AND finished_at IS NOT NULL "
            "AND error_code IS NULL AND entry_count = source_entry_count) "
            "OR (status <> 'ready'))",
            name="ck_bid_semantic_indexes_ready",
        ),
        sa.CheckConstraint(
            "((status = 'failed' AND error_code IS NOT NULL AND finished_at IS NOT NULL) "
            "OR (status <> 'failed'))",
            name="ck_bid_semantic_indexes_failed",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_index_id"],
            ["bid_evidence_retrieval_indexes.id"],
            name="fk_bid_semantic_indexes_retrieval",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_evidence_semantic_indexes"),
        sa.UniqueConstraint(
            "retrieval_index_id",
            "semantic_profile_version",
            name="uq_bid_semantic_indexes_input",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "semantic_profile_version",
            "id",
            "retrieval_index_id",
            name="uq_bid_semantic_indexes_head_target",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_semantic_indexes_queue",
        "bid_evidence_semantic_indexes",
        ["status", "requested_at"],
    )
    op.create_index(
        "ix_bid_semantic_indexes_source",
        "bid_evidence_semantic_indexes",
        ["retrieval_index_id", "semantic_profile_version"],
    )

    op.create_table(
        "bid_evidence_semantic_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("semantic_index_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_index_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_entry_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_child_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_child_key", sa.String(length=100), nullable=False),
        sa.Column("source_entry_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_text_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_record_id", sa.String(length=64), nullable=False),
        sa.Column("vector_hash", sa.String(length=64), nullable=False),
        sa.Column("vector_dimension", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_bid_semantic_entries_ordinal"),
        sa.CheckConstraint("vector_dimension > 0", name="ck_bid_semantic_entries_dimension"),
        sa.ForeignKeyConstraint(
            ["semantic_index_id"],
            ["bid_evidence_semantic_indexes.id"],
            name="fk_bid_semantic_entries_index",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_entry_id"],
            ["bid_evidence_retrieval_entries.id"],
            name="fk_bid_semantic_entries_retrieval_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_child_id"],
            ["bid_evidence_fragments.id"],
            name="fk_bid_semantic_entries_child",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_evidence_semantic_entries"),
        sa.UniqueConstraint(
            "semantic_index_id",
            "retrieval_entry_id",
            name="uq_bid_semantic_entries_retrieval",
        ),
        sa.UniqueConstraint(
            "semantic_index_id",
            "provider_record_id",
            name="uq_bid_semantic_entries_provider_record",
        ),
        sa.UniqueConstraint(
            "semantic_index_id",
            "entry_hash",
            name="uq_bid_semantic_entries_hash",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_semantic_entries_index_order",
        "bid_evidence_semantic_entries",
        ["semantic_index_id", "ordinal"],
    )
    op.create_index(
        "ix_bid_semantic_entries_child",
        "bid_evidence_semantic_entries",
        ["retrieval_child_id"],
    )

    op.create_table(
        "bid_evidence_semantic_heads",
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("semantic_profile_version", sa.String(length=100), nullable=False),
        sa.Column("current_semantic_index_id", sa.String(length=36), nullable=False),
        sa.Column("current_retrieval_index_id", sa.String(length=36), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_semantic_heads_row_version"),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint(
            "document_version_id",
            "semantic_profile_version",
            name="pk_bid_evidence_semantic_heads",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_semantic_heads_current",
        "bid_evidence_semantic_heads",
        ["current_semantic_index_id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0103 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable semantic-index lineage checks"
        )
    bind = op.get_bind()
    counts = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in (
            "bid_evidence_semantic_heads",
            "bid_evidence_semantic_entries",
            "bid_evidence_semantic_indexes",
        )
    }
    if any(counts.values()):
        raise RuntimeError(
            "0103 downgrade would erase semantic-index lineage; export and "
            "explicitly remove RQ2-A derived rows and provider namespaces first"
        )
    op.drop_table("bid_evidence_semantic_heads")
    op.drop_table("bid_evidence_semantic_entries")
    op.drop_table("bid_evidence_semantic_indexes")
