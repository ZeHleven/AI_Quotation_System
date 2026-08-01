"""add reviewed bid policy calibration datasets

Revision ID: 20260727_0071
Revises: 20260727_0070
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260727_0071"
down_revision: Union[str, None] = "20260727_0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


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


def upgrade() -> None:
    review_table = "bid_intake_policy_calibration_reviews"
    if review_table not in _tables():
        op.create_table(
            review_table,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(
                "review_uuid",
                sa.String(length=36),
                nullable=False,
            ),
            sa.Column("label_id", sa.Integer(), nullable=False),
            sa.Column(
                "action",
                sa.String(length=24),
                nullable=False,
            ),
            sa.Column("note", _long_text(), nullable=False),
            sa.Column("reviewed_by", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["label_id"],
                ["bid_intake_policy_calibration_labels.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["reviewed_by"],
                ["users.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "review_uuid",
                name="uq_bid_intake_policy_calibration_reviews_uuid",
            ),
            sa.UniqueConstraint(
                "label_id",
                name="uq_bid_intake_policy_calibration_reviews_label",
            ),
        )
        for index_name, columns in (
            (
                "ix_bid_intake_policy_calibration_reviews_id",
                ["id"],
            ),
            (
                "ix_bid_intake_policy_calibration_reviews_review_uuid",
                ["review_uuid"],
            ),
            (
                "ix_bid_intake_policy_calibration_reviews_label_id",
                ["label_id"],
            ),
            (
                "ix_bid_intake_policy_calibration_reviews_action",
                ["action"],
            ),
            (
                "ix_bid_intake_policy_calibration_reviews_reviewed_by",
                ["reviewed_by"],
            ),
            (
                "ix_bid_intake_policy_reviews_action_created",
                ["action", "created_at"],
            ),
        ):
            op.create_index(
                index_name,
                review_table,
                columns,
                unique=False,
            )

    dataset_table = "bid_intake_policy_calibration_datasets"
    if dataset_table not in _tables():
        op.create_table(
            dataset_table,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(
                "dataset_uuid",
                sa.String(length=36),
                nullable=False,
            ),
            sa.Column(
                "dataset_version",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(length=24),
                nullable=False,
                server_default="frozen",
            ),
            sa.Column(
                "dataset_fingerprint",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column("snapshot_json", _long_text(), nullable=False),
            sa.Column(
                "quality_report_json",
                _long_text(),
                nullable=False,
            ),
            sa.Column("freeze_note", _long_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["created_by"],
                ["users.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "dataset_uuid",
                name="uq_bid_intake_policy_calibration_datasets_uuid",
            ),
            sa.UniqueConstraint(
                "dataset_version",
                name="uq_bid_intake_policy_calibration_datasets_version",
            ),
            sa.UniqueConstraint(
                "dataset_fingerprint",
                name=(
                    "uq_bid_intake_policy_calibration_datasets_fingerprint"
                ),
            ),
        )
        for index_name, columns in (
            (
                "ix_bid_intake_policy_calibration_datasets_id",
                ["id"],
            ),
            (
                "ix_bid_intake_policy_calibration_datasets_dataset_uuid",
                ["dataset_uuid"],
            ),
            (
                "ix_bid_intake_policy_calibration_datasets_dataset_version",
                ["dataset_version"],
            ),
            (
                "ix_bid_intake_policy_calibration_datasets_status",
                ["status"],
            ),
            (
                "ix_bid_intake_policy_calibration_datasets_fingerprint",
                ["dataset_fingerprint"],
            ),
            (
                "ix_bid_intake_policy_calibration_datasets_created_by",
                ["created_by"],
            ),
            (
                "ix_bid_intake_policy_datasets_status_created",
                ["status", "created_at"],
            ),
        ):
            op.create_index(
                index_name,
                dataset_table,
                columns,
                unique=False,
            )

    candidate_table = "bid_intake_policy_candidates"
    column_name = "calibration_dataset_id"
    if column_name not in _columns(candidate_table):
        with op.batch_alter_table(
            candidate_table,
            reflect_kwargs={"resolve_fks": False},
        ) as batch_op:
            batch_op.add_column(
                sa.Column(column_name, sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_bid_policy_candidate_calibration_dataset",
                dataset_table,
                [column_name],
                ["id"],
                ondelete="RESTRICT",
            )
    index_name = (
        "ix_bid_intake_policy_candidates_calibration_dataset_id"
    )
    if index_name not in _indexes(candidate_table):
        op.create_index(
            index_name,
            candidate_table,
            [column_name],
            unique=False,
        )


def downgrade() -> None:
    candidate_table = "bid_intake_policy_candidates"
    column_name = "calibration_dataset_id"
    index_name = (
        "ix_bid_intake_policy_candidates_calibration_dataset_id"
    )
    if column_name in _columns(candidate_table):
        if index_name in _indexes(candidate_table):
            op.drop_index(index_name, table_name=candidate_table)
        with op.batch_alter_table(
            candidate_table,
            reflect_kwargs={"resolve_fks": False},
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_bid_policy_candidate_calibration_dataset",
                type_="foreignkey",
            )
            batch_op.drop_column(column_name)
    for table_name in (
        "bid_intake_policy_calibration_datasets",
        "bid_intake_policy_calibration_reviews",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
