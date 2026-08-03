"""add layered storage metadata for tender evidence bodies

Revision ID: 20260728_0072
Revises: 20260727_0071
Create Date: 2026-07-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260728_0072"
down_revision: Union[str, None] = "20260727_0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    if "bid_evidence_documents" in _tables():
        columns = _columns("bid_evidence_documents")
        additions = (
            (
                "body_storage_backend",
                sa.Column(
                    "body_storage_backend",
                    sa.String(length=32),
                    nullable=False,
                    server_default="mysql_legacy",
                ),
            ),
            (
                "body_bucket",
                sa.Column("body_bucket", sa.String(length=128), nullable=True),
            ),
            (
                "body_object_name",
                sa.Column(
                    "body_object_name",
                    sa.String(length=512),
                    nullable=True,
                ),
            ),
            (
                "body_sha256",
                sa.Column("body_sha256", sa.String(length=64), nullable=True),
            ),
            (
                "body_size_bytes",
                sa.Column(
                    "body_size_bytes",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            ),
            (
                "body_schema_version",
                sa.Column(
                    "body_schema_version",
                    sa.String(length=64),
                    nullable=True,
                ),
            ),
        )
        for name, column in additions:
            if name not in columns:
                op.add_column("bid_evidence_documents", column)
        indexes = _indexes("bid_evidence_documents")
        if "ix_bid_evidence_documents_body_storage_backend" not in indexes:
            op.create_index(
                "ix_bid_evidence_documents_body_storage_backend",
                "bid_evidence_documents",
                ["body_storage_backend"],
                unique=False,
            )
        if "ix_bid_evidence_documents_body_sha256" not in indexes:
            op.create_index(
                "ix_bid_evidence_documents_body_sha256",
                "bid_evidence_documents",
                ["body_sha256"],
                unique=False,
            )

    if "bid_evidence_blocks" in _tables():
        if "content_length" not in _columns("bid_evidence_blocks"):
            op.add_column(
                "bid_evidence_blocks",
                sa.Column(
                    "content_length",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            )
        dialect = op.get_bind().dialect.name
        length_function = "CHAR_LENGTH" if dialect == "mysql" else "LENGTH"
        op.execute(
            sa.text(
                "UPDATE bid_evidence_blocks "
                f"SET content_length = {length_function}(content) "
                "WHERE content IS NOT NULL AND content_length = 0"
            )
        )
        op.alter_column(
            "bid_evidence_blocks",
            "content",
            existing_type=_long_text(),
            nullable=True,
        )

    if "bid_project_files" in _tables():
        columns = _columns("bid_project_files")
        additions = (
            (
                "parsed_artifact_bucket",
                sa.Column(
                    "parsed_artifact_bucket",
                    sa.String(length=128),
                    nullable=True,
                ),
            ),
            (
                "parsed_artifact_object_name",
                sa.Column(
                    "parsed_artifact_object_name",
                    sa.String(length=512),
                    nullable=True,
                ),
            ),
            (
                "parsed_artifact_sha256",
                sa.Column(
                    "parsed_artifact_sha256",
                    sa.String(length=64),
                    nullable=True,
                ),
            ),
            (
                "parsed_artifact_size_bytes",
                sa.Column(
                    "parsed_artifact_size_bytes",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            ),
            (
                "parsed_artifact_schema_version",
                sa.Column(
                    "parsed_artifact_schema_version",
                    sa.String(length=64),
                    nullable=True,
                ),
            ),
        )
        for name, column in additions:
            if name not in columns:
                op.add_column("bid_project_files", column)
        if (
            "ix_bid_project_files_parsed_artifact_sha256"
            not in _indexes("bid_project_files")
        ):
            op.create_index(
                "ix_bid_project_files_parsed_artifact_sha256",
                "bid_project_files",
                ["parsed_artifact_sha256"],
                unique=False,
            )


def downgrade() -> None:
    if "bid_project_files" in _tables():
        if (
            "ix_bid_project_files_parsed_artifact_sha256"
            in _indexes("bid_project_files")
        ):
            op.drop_index(
                "ix_bid_project_files_parsed_artifact_sha256",
                table_name="bid_project_files",
            )
        for name in (
            "parsed_artifact_schema_version",
            "parsed_artifact_size_bytes",
            "parsed_artifact_sha256",
            "parsed_artifact_object_name",
            "parsed_artifact_bucket",
        ):
            if name in _columns("bid_project_files"):
                op.drop_column("bid_project_files", name)

    if "bid_evidence_blocks" in _tables():
        externalized_count = int(
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT COUNT(*) FROM bid_evidence_blocks "
                    "WHERE content IS NULL"
                )
            )
            .scalar()
            or 0
        )
        if externalized_count:
            raise RuntimeError(
                "cannot downgrade layered tender storage while "
                f"{externalized_count} evidence blocks are externalized; "
                "restore their verified MinIO bodies to MySQL first"
            )
        op.alter_column(
            "bid_evidence_blocks",
            "content",
            existing_type=_long_text(),
            nullable=False,
        )
        if "content_length" in _columns("bid_evidence_blocks"):
            op.drop_column("bid_evidence_blocks", "content_length")

    if "bid_evidence_documents" in _tables():
        for index_name in (
            "ix_bid_evidence_documents_body_sha256",
            "ix_bid_evidence_documents_body_storage_backend",
        ):
            if index_name in _indexes("bid_evidence_documents"):
                op.drop_index(
                    index_name,
                    table_name="bid_evidence_documents",
                )
        for name in (
            "body_schema_version",
            "body_size_bytes",
            "body_sha256",
            "body_object_name",
            "body_bucket",
            "body_storage_backend",
        ):
            if name in _columns("bid_evidence_documents"):
                op.drop_column("bid_evidence_documents", name)
