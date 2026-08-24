"""add governed enterprise evidence import authority

Revision ID: 20260817_0107
Revises: 20260817_0106
Create Date: 2026-08-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260817_0107"
down_revision: Union[str, None] = "20260817_0106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_enterprise_evidence_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="frozen", nullable=False),
        sa.Column("evidence_class", sa.String(length=40), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("source_label", sa.String(length=300), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("item_hash", sa.String(length=64), nullable=False),
        sa.Column("object_ref", sa.String(length=512), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status = 'frozen'", name="ck_bid_ent_evidence_items_status"),
        sa.CheckConstraint(
            "evidence_class IN ('official_document', 'internal_system', 'audited_record')",
            name="ck_bid_ent_evidence_items_class",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_bid_ent_evidence_items_size"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_bid_ent_evidence_items_validity",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name="fk_bid_ent_evidence_items_uploader",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_enterprise_evidence_items"),
        sa.UniqueConstraint(
            "source_record_id",
            "source_version",
            "content_sha256",
            name="uq_bid_ent_evidence_items_source",
        ),
        sa.UniqueConstraint("item_hash", name="uq_bid_ent_evidence_items_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_ent_evidence_items_source",
        "bid_enterprise_evidence_items",
        ["source_record_id", "source_version"],
    )
    op.create_index(
        "ix_bid_ent_evidence_items_uploaded",
        "bid_enterprise_evidence_items",
        ["uploaded_at", "id"],
    )

    op.create_table(
        "bid_enterprise_evidence_packages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="frozen", nullable=False),
        sa.Column("package_label", sa.String(length=300), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
        sa.Column("frozen_by", sa.Integer(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status = 'frozen'", name="ck_bid_ent_evidence_packages_status"),
        sa.ForeignKeyConstraint(
            ["frozen_by"],
            ["users.id"],
            name="fk_bid_ent_evidence_packages_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_enterprise_evidence_packages"),
        sa.UniqueConstraint("version", name="uq_bid_ent_evidence_packages_version"),
        sa.UniqueConstraint(
            "candidate_hash",
            name="uq_bid_ent_evidence_packages_candidate",
        ),
        sa.UniqueConstraint("package_hash", name="uq_bid_ent_evidence_packages_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_ent_evidence_packages_frozen",
        "bid_enterprise_evidence_packages",
        ["frozen_at", "id"],
    )

    op.create_table(
        "bid_enterprise_evidence_package_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_item_id", sa.String(length=36), nullable=False),
        sa.Column("slot_code", sa.String(length=3), nullable=False),
        sa.Column("mapping_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "slot_code IN ('I01','I02','I03','I04','I05','I06','I07','I08','I09','I10','I11')",
            name="ck_bid_ent_evidence_pkg_items_slot",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["bid_enterprise_evidence_packages.id"],
            name="fk_bid_ent_evidence_pkg_items_pkg",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_item_id"],
            ["bid_enterprise_evidence_items.id"],
            name="fk_bid_ent_evidence_pkg_items_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_enterprise_evidence_package_items"),
        sa.UniqueConstraint(
            "package_id",
            "evidence_item_id",
            "slot_code",
            name="uq_bid_ent_evidence_pkg_items_map",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_ent_evidence_pkg_items_slot",
        "bid_enterprise_evidence_package_items",
        ["package_id", "slot_code"],
    )

    with op.batch_alter_table("bid_enterprise_business_baselines") as batch_op:
        batch_op.add_column(sa.Column("evidence_package_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("evidence_package_hash", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_bid_ent_business_baseline_package",
            "bid_enterprise_evidence_packages",
            ["evidence_package_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0107 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable enterprise-evidence checks"
        )
    bind = op.get_bind()
    for table_name in (
        "bid_enterprise_evidence_package_items",
        "bid_enterprise_evidence_packages",
        "bid_enterprise_evidence_items",
    ):
        count = int(
            bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
        )
        if count:
            raise RuntimeError(
                "0107 downgrade would erase immutable enterprise-evidence lineage"
            )
    linked = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_enterprise_business_baselines "
                "WHERE evidence_package_id IS NOT NULL OR evidence_package_hash IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    if linked:
        raise RuntimeError(
            "0107 downgrade would erase business-baseline evidence-package lineage"
        )

    with op.batch_alter_table("bid_enterprise_business_baselines") as batch_op:
        batch_op.drop_constraint(
            "fk_bid_ent_business_baseline_package",
            type_="foreignkey",
        )
        batch_op.drop_column("evidence_package_hash")
        batch_op.drop_column("evidence_package_id")
    op.drop_table("bid_enterprise_evidence_package_items")
    op.drop_table("bid_enterprise_evidence_packages")
    op.drop_table("bid_enterprise_evidence_items")
