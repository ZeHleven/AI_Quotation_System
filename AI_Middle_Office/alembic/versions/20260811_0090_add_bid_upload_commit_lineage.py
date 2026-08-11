"""add API-15 upload-batch to immutable-Manifest lineage

Revision ID: 20260811_0090
Revises: 20260811_0089
Create Date: 2026-08-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "20260811_0090"
down_revision: Union[str, None] = "20260811_0089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_document_manifests",
        sa.Column("change_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "bid_upload_batches",
        sa.Column("committed_manifest_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "bid_upload_batches",
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_bid_upload_batches_committed_manifest",
        "bid_upload_batches",
        "bid_document_manifests",
        ["assessment_id", "committed_manifest_id"],
        ["assessment_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_bid_upload_batches_committed_manifest",
        "bid_upload_batches",
        ["committed_manifest_id"],
    )
    op.create_check_constraint(
        "ck_bid_upload_batches_commit_result",
        "bid_upload_batches",
        "((status = 'committed' AND committed_manifest_id IS NOT NULL "
        "AND committed_at IS NOT NULL) OR "
        "(status <> 'committed' AND committed_manifest_id IS NULL "
        "AND committed_at IS NULL))",
    )


def downgrade() -> None:
    if context.is_offline_mode():
        # The lineage guard must inspect live rows.  Emitting unchecked DROP
        # COLUMN statements into an offline script would turn a safety gate
        # into silent data loss, so guarded downgrade is intentionally online.
        raise RuntimeError(
            "0090 guarded downgrade requires an online database connection; "
            "offline SQL would bypass the API-15 commit-lineage data check"
        )
    bind = op.get_bind()
    committed_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_upload_batches "
                "WHERE committed_manifest_id IS NOT NULL OR committed_at IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    noted_manifest_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_document_manifests "
                "WHERE change_note IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    if committed_count or noted_manifest_count:
        raise RuntimeError(
            "0090 downgrade would erase API-15 commit lineage; "
            "archive/remove committed batches and Manifest change notes first"
        )

    op.drop_constraint(
        "ck_bid_upload_batches_commit_result",
        "bid_upload_batches",
        type_="check",
    )
    op.drop_constraint(
        "uq_bid_upload_batches_committed_manifest",
        "bid_upload_batches",
        type_="unique",
    )
    op.drop_constraint(
        "fk_bid_upload_batches_committed_manifest",
        "bid_upload_batches",
        type_="foreignkey",
    )
    op.drop_column("bid_upload_batches", "committed_at")
    op.drop_column("bid_upload_batches", "committed_manifest_id")
    op.drop_column("bid_document_manifests", "change_note")
