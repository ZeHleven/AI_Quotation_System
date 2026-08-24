"""add bid enterprise business baseline authority

Revision ID: 20260817_0106
Revises: 20260817_0105
Create Date: 2026-08-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260817_0106"
down_revision: Union[str, None] = "20260817_0105"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_enterprise_business_baselines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="frozen", nullable=False),
        sa.Column("verification_outcome", sa.String(length=40), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("slot_reviews_json", sa.JSON(), nullable=False),
        sa.Column("source_hashes_json", sa.JSON(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("baseline_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status = 'frozen'",
            name="ck_bid_enterprise_business_baselines_status",
        ),
        sa.CheckConstraint(
            "verification_outcome IN ('verified', 'verified_with_follow_up')",
            name="ck_bid_enterprise_business_baselines_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["bid_enterprise_snapshots.id"],
            name="fk_bid_enterprise_business_baselines_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name="fk_bid_enterprise_business_baselines_reviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_enterprise_business_baselines"),
        sa.UniqueConstraint(
            "snapshot_id",
            name="uq_bid_enterprise_business_baselines_snapshot",
        ),
        sa.UniqueConstraint(
            "version",
            name="uq_bid_enterprise_business_baselines_version",
        ),
        sa.UniqueConstraint(
            "candidate_hash",
            name="uq_bid_enterprise_business_baselines_candidate_hash",
        ),
        sa.UniqueConstraint(
            "baseline_hash",
            name="uq_bid_enterprise_business_baselines_hash",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_enterprise_business_baselines_reviewed",
        "bid_enterprise_business_baselines",
        ["reviewed_at", "snapshot_id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0106 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable business-baseline checks"
        )
    bind = op.get_bind()
    count = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM bid_enterprise_business_baselines")
        ).scalar()
        or 0
    )
    if count:
        raise RuntimeError(
            "0106 downgrade would erase immutable enterprise business-baseline lineage"
        )
    op.drop_table("bid_enterprise_business_baselines")
