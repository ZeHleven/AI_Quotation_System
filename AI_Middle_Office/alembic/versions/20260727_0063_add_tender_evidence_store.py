"""add project-scoped tender evidence store

Revision ID: 20260727_0063
Revises: 20260723_0062
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260727_0063"
down_revision: Union[str, None] = "20260723_0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    tables = _tables()
    if "bid_evidence_documents" not in tables:
        op.create_table(
            "bid_evidence_documents",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("evidence_document_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_file_id",
                sa.Integer(),
                sa.ForeignKey("bid_project_files.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("document_key", sa.String(length=160), nullable=False),
            sa.Column("document_type", sa.String(length=64), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("parser_version", sa.String(length=64), nullable=False),
            sa.Column(
                "parse_status",
                sa.String(length=16),
                nullable=False,
                server_default="ready",
            ),
            sa.Column(
                "active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "activated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "evidence_document_uuid",
                name="uq_bid_evidence_documents_uuid",
            ),
            sa.UniqueConstraint(
                "project_id",
                "source_file_id",
                name="uq_bid_evidence_documents_project_source",
            ),
            sa.UniqueConstraint(
                "project_id",
                "document_key",
                "version_no",
                name="uq_bid_evidence_documents_project_key_version",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_evidence_documents_id", ["id"], False),
            (
                "ix_bid_evidence_documents_evidence_document_uuid",
                ["evidence_document_uuid"],
                True,
            ),
            ("ix_bid_evidence_documents_project_id", ["project_id"], False),
            (
                "ix_bid_evidence_documents_source_file_id",
                ["source_file_id"],
                False,
            ),
            ("ix_bid_evidence_documents_document_key", ["document_key"], False),
            ("ix_bid_evidence_documents_document_type", ["document_type"], False),
            ("ix_bid_evidence_documents_sha256", ["sha256"], False),
            ("ix_bid_evidence_documents_parse_status", ["parse_status"], False),
            ("ix_bid_evidence_documents_active", ["active"], False),
            ("ix_bid_evidence_documents_created_by", ["created_by"], False),
            (
                "ix_bid_evidence_documents_project_active",
                ["project_id", "active"],
                False,
            ),
            (
                "ix_bid_evidence_documents_project_key_version",
                ["project_id", "document_key", "version_no"],
                False,
            ),
        ):
            op.create_index(name, "bid_evidence_documents", columns, unique=unique)

    tables = _tables()
    if "bid_evidence_blocks" not in tables:
        op.create_table(
            "bid_evidence_blocks",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("evidence_id", sa.String(length=80), nullable=False),
            sa.Column("block_id", sa.String(length=80), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "document_id",
                sa.Integer(),
                sa.ForeignKey("bid_evidence_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("block_order", sa.Integer(), nullable=False),
            sa.Column("page", sa.Integer(), nullable=True),
            sa.Column("sheet", sa.String(length=160), nullable=True),
            sa.Column("cell_range", sa.String(length=80), nullable=True),
            sa.Column("section", sa.String(length=500), nullable=True),
            sa.Column("locator_json", _long_text(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("content", _long_text(), nullable=False),
            sa.Column("keywords_json", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "evidence_id",
                name="uq_bid_evidence_blocks_evidence_id",
            ),
            sa.UniqueConstraint(
                "block_id",
                name="uq_bid_evidence_blocks_block_id",
            ),
            sa.UniqueConstraint(
                "document_id",
                "block_order",
                name="uq_bid_evidence_blocks_document_order",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_evidence_blocks_id", ["id"], False),
            ("ix_bid_evidence_blocks_evidence_id", ["evidence_id"], True),
            ("ix_bid_evidence_blocks_block_id", ["block_id"], True),
            ("ix_bid_evidence_blocks_project_id", ["project_id"], False),
            ("ix_bid_evidence_blocks_document_id", ["document_id"], False),
            ("ix_bid_evidence_blocks_content_hash", ["content_hash"], False),
            (
                "ix_bid_evidence_blocks_project_document_order",
                ["project_id", "document_id", "block_order"],
                False,
            ),
            (
                "ix_bid_evidence_blocks_project_content_hash",
                ["project_id", "content_hash"],
                False,
            ),
        ):
            op.create_index(name, "bid_evidence_blocks", columns, unique=unique)

    tables = _tables()
    if "bid_evidence_manifests" not in tables:
        op.create_table(
            "bid_evidence_manifests",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("manifest_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("snapshot_json", _long_text(), nullable=False),
            sa.Column(
                "active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "manifest_uuid",
                name="uq_bid_evidence_manifests_uuid",
            ),
            sa.UniqueConstraint(
                "project_id",
                "version_no",
                name="uq_bid_evidence_manifests_project_version",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_evidence_manifests_id", ["id"], False),
            ("ix_bid_evidence_manifests_manifest_uuid", ["manifest_uuid"], True),
            ("ix_bid_evidence_manifests_project_id", ["project_id"], False),
            ("ix_bid_evidence_manifests_manifest_hash", ["manifest_hash"], False),
            ("ix_bid_evidence_manifests_active", ["active"], False),
            ("ix_bid_evidence_manifests_created_by", ["created_by"], False),
            (
                "ix_bid_evidence_manifests_project_active",
                ["project_id", "active"],
                False,
            ),
        ):
            op.create_index(name, "bid_evidence_manifests", columns, unique=unique)

    tables = _tables()
    if "bid_evidence_read_audits" not in tables:
        op.create_table(
            "bid_evidence_read_audits",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("audit_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "evidence_document_id",
                sa.Integer(),
                sa.ForeignKey("bid_evidence_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "evidence_block_id",
                sa.Integer(),
                sa.ForeignKey("bid_evidence_blocks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("assessment_id", sa.String(length=160), nullable=False),
            sa.Column("agent_run_id", sa.String(length=160), nullable=False),
            sa.Column("subject", sa.String(length=200), nullable=False),
            sa.Column(
                "capability",
                sa.String(length=64),
                nullable=False,
                server_default="read_evidence_context",
            ),
            sa.Column("trace_id", sa.String(length=160), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "audit_uuid",
                name="uq_bid_evidence_read_audits_uuid",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_evidence_read_audits_id", ["id"], False),
            ("ix_bid_evidence_read_audits_audit_uuid", ["audit_uuid"], True),
            ("ix_bid_evidence_read_audits_project_id", ["project_id"], False),
            (
                "ix_bid_evidence_read_audits_evidence_document_id",
                ["evidence_document_id"],
                False,
            ),
            (
                "ix_bid_evidence_read_audits_evidence_block_id",
                ["evidence_block_id"],
                False,
            ),
            ("ix_bid_evidence_read_audits_assessment_id", ["assessment_id"], False),
            ("ix_bid_evidence_read_audits_agent_run_id", ["agent_run_id"], False),
            ("ix_bid_evidence_read_audits_subject", ["subject"], False),
            ("ix_bid_evidence_read_audits_capability", ["capability"], False),
            ("ix_bid_evidence_read_audits_trace_id", ["trace_id"], False),
            (
                "ix_bid_evidence_read_audits_project_run_created",
                ["project_id", "agent_run_id", "created_at"],
                False,
            ),
            (
                "ix_bid_evidence_read_audits_project_evidence_run",
                ["project_id", "evidence_block_id", "agent_run_id"],
                False,
            ),
        ):
            op.create_index(name, "bid_evidence_read_audits", columns, unique=unique)


def downgrade() -> None:
    for table_name, indexes in (
        (
            "bid_evidence_read_audits",
            (
                "ix_bid_evidence_read_audits_project_evidence_run",
                "ix_bid_evidence_read_audits_project_run_created",
                "ix_bid_evidence_read_audits_trace_id",
                "ix_bid_evidence_read_audits_capability",
                "ix_bid_evidence_read_audits_subject",
                "ix_bid_evidence_read_audits_agent_run_id",
                "ix_bid_evidence_read_audits_assessment_id",
                "ix_bid_evidence_read_audits_evidence_block_id",
                "ix_bid_evidence_read_audits_evidence_document_id",
                "ix_bid_evidence_read_audits_project_id",
                "ix_bid_evidence_read_audits_audit_uuid",
                "ix_bid_evidence_read_audits_id",
            ),
        ),
        (
            "bid_evidence_manifests",
            (
                "ix_bid_evidence_manifests_project_active",
                "ix_bid_evidence_manifests_created_by",
                "ix_bid_evidence_manifests_active",
                "ix_bid_evidence_manifests_manifest_hash",
                "ix_bid_evidence_manifests_project_id",
                "ix_bid_evidence_manifests_manifest_uuid",
                "ix_bid_evidence_manifests_id",
            ),
        ),
        (
            "bid_evidence_blocks",
            (
                "ix_bid_evidence_blocks_project_content_hash",
                "ix_bid_evidence_blocks_project_document_order",
                "ix_bid_evidence_blocks_content_hash",
                "ix_bid_evidence_blocks_document_id",
                "ix_bid_evidence_blocks_project_id",
                "ix_bid_evidence_blocks_block_id",
                "ix_bid_evidence_blocks_evidence_id",
                "ix_bid_evidence_blocks_id",
            ),
        ),
        (
            "bid_evidence_documents",
            (
                "ix_bid_evidence_documents_project_key_version",
                "ix_bid_evidence_documents_project_active",
                "ix_bid_evidence_documents_created_by",
                "ix_bid_evidence_documents_active",
                "ix_bid_evidence_documents_parse_status",
                "ix_bid_evidence_documents_sha256",
                "ix_bid_evidence_documents_document_type",
                "ix_bid_evidence_documents_document_key",
                "ix_bid_evidence_documents_source_file_id",
                "ix_bid_evidence_documents_project_id",
                "ix_bid_evidence_documents_evidence_document_uuid",
                "ix_bid_evidence_documents_id",
            ),
        ),
    ):
        if table_name not in _tables():
            continue
        for index_name in indexes:
            _drop_index_if_exists(index_name, table_name)
        op.drop_table(table_name)
