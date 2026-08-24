"""add role-aware bid evidence retrieval authority

Revision ID: 20260814_0102
Revises: 20260813_0101
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260814_0102"
down_revision: Union[str, None] = "20260813_0101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_evidence_retrieval_indexes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("parse_run_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_profile_version", sa.String(length=100), nullable=False),
        sa.Column("role_contract_version", sa.String(length=80), nullable=False),
        sa.Column("source_result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("parent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("child_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("atom_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("entry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'building', 'ready', 'failed', 'stale')",
            name="ck_bid_evidence_retrieval_indexes_status",
        ),
        sa.CheckConstraint(
            "parent_count >= 0 AND child_count >= 0 AND atom_count >= 0 AND entry_count >= 0",
            name="ck_bid_evidence_retrieval_indexes_counts",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_evidence_retrieval_indexes_row_version"),
        sa.CheckConstraint(
            "((status = 'ready' AND result_hash IS NOT NULL AND finished_at IS NOT NULL "
            "AND error_code IS NULL) OR (status <> 'ready'))",
            name="ck_bid_evidence_retrieval_indexes_ready",
        ),
        sa.CheckConstraint(
            "((status = 'failed' AND error_code IS NOT NULL AND finished_at IS NOT NULL) "
            "OR (status <> 'failed'))",
            name="ck_bid_evidence_retrieval_indexes_failed",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "parse_run_id"],
            ["bid_document_parse_runs.document_version_id", "bid_document_parse_runs.id"],
            name="fk_bid_evidence_retrieval_indexes_parse_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_evidence_retrieval_indexes"),
        sa.UniqueConstraint(
            "document_version_id",
            "parse_run_id",
            "retrieval_profile_version",
            name="uq_bid_evidence_retrieval_indexes_input",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "retrieval_profile_version",
            "id",
            "parse_run_id",
            name="uq_bid_evidence_retrieval_indexes_head_target",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_evidence_retrieval_indexes_queue",
        "bid_evidence_retrieval_indexes",
        ["status", "requested_at"],
    )
    op.create_index(
        "ix_bid_evidence_retrieval_indexes_parse_profile",
        "bid_evidence_retrieval_indexes",
        ["parse_run_id", "retrieval_profile_version"],
    )

    op.create_table(
        "bid_evidence_retrieval_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("index_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("parse_run_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_child_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_child_key", sa.String(length=100), nullable=False),
        sa.Column("section_parent_id", sa.String(length=36), nullable=False),
        sa.Column("section_parent_key", sa.String(length=100), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("retrieval_text", sa.Text(), nullable=False),
        sa.Column("retrieval_hash", sa.String(length=64), nullable=False),
        sa.Column("child_text_hash", sa.String(length=64), nullable=False),
        sa.Column("source_atom_ids_json", sa.JSON(), nullable=False),
        sa.Column("source_atom_keys_json", sa.JSON(), nullable=False),
        sa.Column("source_atom_count", sa.Integer(), nullable=False),
        sa.Column("source_atoms_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_bid_evidence_retrieval_entries_ordinal"),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_bid_evidence_retrieval_entries_pages",
        ),
        sa.CheckConstraint(
            "source_atom_count >= 1",
            name="ck_bid_evidence_retrieval_entries_atom_count",
        ),
        sa.ForeignKeyConstraint(
            ["index_id"],
            ["bid_evidence_retrieval_indexes.id"],
            name="fk_bid_evidence_retrieval_entries_index",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parse_run_id", "retrieval_child_id"],
            ["bid_evidence_fragments.parse_run_id", "bid_evidence_fragments.id"],
            name="fk_bid_evidence_retrieval_entries_child",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parse_run_id", "section_parent_id"],
            ["bid_evidence_fragments.parse_run_id", "bid_evidence_fragments.id"],
            name="fk_bid_evidence_retrieval_entries_parent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_evidence_retrieval_entries"),
        sa.UniqueConstraint("index_id", "retrieval_child_id", name="uq_bid_evidence_retrieval_entries_child"),
        sa.UniqueConstraint("index_id", "retrieval_child_key", name="uq_bid_evidence_retrieval_entries_child_key"),
        sa.UniqueConstraint("index_id", "entry_hash", name="uq_bid_evidence_retrieval_entries_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_evidence_retrieval_entries_index_order",
        "bid_evidence_retrieval_entries",
        ["index_id", "ordinal"],
    )
    op.create_index(
        "ix_bid_evidence_retrieval_entries_parent",
        "bid_evidence_retrieval_entries",
        ["index_id", "section_parent_id", "ordinal"],
    )
    op.create_index(
        "ix_bid_evidence_retrieval_entries_document",
        "bid_evidence_retrieval_entries",
        ["document_version_id", "parse_run_id"],
    )

    op.create_table(
        "bid_evidence_retrieval_heads",
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_profile_version", sa.String(length=100), nullable=False),
        sa.Column("current_index_id", sa.String(length=36), nullable=False),
        sa.Column("current_parse_run_id", sa.String(length=36), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_evidence_retrieval_heads_row_version"),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint(
            "document_version_id",
            "retrieval_profile_version",
            name="pk_bid_evidence_retrieval_heads",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_evidence_retrieval_heads_current_index",
        "bid_evidence_retrieval_heads",
        ["current_index_id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0102 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable retrieval-index lineage checks"
        )
    bind = op.get_bind()
    counts = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in (
            "bid_evidence_retrieval_heads",
            "bid_evidence_retrieval_entries",
            "bid_evidence_retrieval_indexes",
        )
    }
    if any(counts.values()):
        raise RuntimeError(
            "0102 downgrade would erase role-aware retrieval-index lineage; "
            "export and explicitly remove PDF-C3 derived rows first"
        )
    op.drop_table("bid_evidence_retrieval_heads")
    op.drop_table("bid_evidence_retrieval_entries")
    op.drop_table("bid_evidence_retrieval_indexes")
