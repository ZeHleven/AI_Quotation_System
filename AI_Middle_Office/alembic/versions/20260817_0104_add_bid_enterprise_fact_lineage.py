"""add bid enterprise fact lineage

Revision ID: 20260817_0104
Revises: 20260815_0103
Create Date: 2026-08-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260817_0104"
down_revision: Union[str, None] = "20260815_0103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_fact_enterprise_links",
        sa.Column("assertion_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_record_id", sa.String(length=36), nullable=False),
        sa.Column("record_type", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("link_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assertion_id"],
            ["bid_fact_assertions.id"],
            name="fk_bid_fact_enterprise_links_assertion",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_record_id"],
            ["bid_enterprise_snapshot_records.id"],
            name="fk_bid_fact_enterprise_links_record",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "assertion_id",
            "snapshot_record_id",
            name="pk_bid_fact_enterprise_links",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_fact_enterprise_links_record",
        "bid_fact_enterprise_links",
        ["snapshot_record_id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0104 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable enterprise fact lineage checks"
        )
    bind = op.get_bind()
    count = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM bid_fact_enterprise_links")).scalar()
        or 0
    )
    if count:
        raise RuntimeError(
            "0104 downgrade would erase enterprise snapshot to FactAssertion lineage; "
            "retain the revision while governed facts or historical Runs exist"
        )
    op.drop_table("bid_fact_enterprise_links")
