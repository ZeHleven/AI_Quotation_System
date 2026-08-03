"""add frozen bid policy candidate proposals

Revision ID: 20260727_0070
Revises: 20260727_0069
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260727_0070"
down_revision: Union[str, None] = "20260727_0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    table_name = "bid_intake_policy_candidates"
    if table_name in _tables():
        return
    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_uuid", sa.String(length=36), nullable=False),
        sa.Column(
            "candidate_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "base_policy_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "search_method",
            sa.String(length=48),
            nullable=False,
        ),
        sa.Column(
            "dataset_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("dataset_snapshot_json", _long_text(), nullable=False),
        sa.Column("policy_yaml", _long_text(), nullable=False),
        sa.Column("changed_fields_json", _long_text(), nullable=False),
        sa.Column(
            "development_report_json",
            _long_text(),
            nullable=False,
        ),
        sa.Column("blind_report_json", _long_text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("blind_evaluated_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "blind_evaluated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["blind_evaluated_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "proposal_uuid",
            name="uq_bid_intake_policy_candidates_uuid",
        ),
        sa.UniqueConstraint(
            "candidate_version",
            name="uq_bid_intake_policy_candidates_version",
        ),
        sa.UniqueConstraint(
            "base_policy_version",
            "dataset_fingerprint",
            name="uq_bid_intake_policy_candidates_dataset",
        ),
    )
    for index_name, columns in (
        ("ix_bid_intake_policy_candidates_id", ["id"]),
        (
            "ix_bid_intake_policy_candidates_proposal_uuid",
            ["proposal_uuid"],
        ),
        (
            "ix_bid_intake_policy_candidates_candidate_version",
            ["candidate_version"],
        ),
        (
            "ix_bid_intake_policy_candidates_base_policy_version",
            ["base_policy_version"],
        ),
        ("ix_bid_intake_policy_candidates_status", ["status"]),
        (
            "ix_bid_intake_policy_candidates_dataset_fingerprint",
            ["dataset_fingerprint"],
        ),
        (
            "ix_bid_intake_policy_candidates_created_by",
            ["created_by"],
        ),
        (
            "ix_bid_intake_policy_candidates_blind_evaluated_by",
            ["blind_evaluated_by"],
        ),
        (
            "ix_bid_intake_policy_candidates_status_created",
            ["status", "created_at"],
        ),
        (
            "ix_bid_intake_policy_candidates_base_created",
            ["base_policy_version", "created_at"],
        ),
    ):
        op.create_index(
            index_name,
            table_name,
            columns,
            unique=False,
        )


def downgrade() -> None:
    table_name = "bid_intake_policy_candidates"
    if table_name in _tables():
        op.drop_table(table_name)
