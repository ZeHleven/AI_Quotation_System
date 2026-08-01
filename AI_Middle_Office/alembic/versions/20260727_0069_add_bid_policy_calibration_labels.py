"""add bid policy calibration gold labels

Revision ID: 20260727_0069
Revises: 20260727_0068
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260727_0069"
down_revision: Union[str, None] = "20260727_0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    table_name = "bid_intake_policy_calibration_labels"
    if table_name in _tables():
        return
    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label_uuid", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("label_version", sa.Integer(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("supersedes_label_id", sa.Integer(), nullable=True),
        sa.Column("dataset_split", sa.String(length=24), nullable=False),
        sa.Column("label_basis", sa.String(length=40), nullable=False),
        sa.Column("expected_decision", sa.String(length=40), nullable=False),
        sa.Column(
            "hard_stop_expected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("rationale", _long_text(), nullable=False),
        sa.Column("actual_outcome_json", _long_text(), nullable=True),
        sa.Column("case_snapshot_json", _long_text(), nullable=False),
        sa.Column("source_report_version", sa.Integer(), nullable=False),
        sa.Column("source_manifest_version", sa.Integer(), nullable=False),
        sa.Column(
            "source_manifest_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "source_policy_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "superseded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_intake_assessments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["bid_projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_label_id"],
            [f"{table_name}.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assessment_id",
            "label_version",
            name="uq_bid_intake_policy_calibration_labels_version",
        ),
        sa.UniqueConstraint(
            "label_uuid",
            name="uq_bid_intake_policy_calibration_labels_uuid",
        ),
    )
    for index_name, columns in (
        (
            "ix_bid_intake_policy_calibration_labels_id",
            ["id"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_label_uuid",
            ["label_uuid"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_assessment_id",
            ["assessment_id"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_project_id",
            ["project_id"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_active",
            ["active"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_supersedes_label_id",
            ["supersedes_label_id"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_dataset_split",
            ["dataset_split"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_label_basis",
            ["label_basis"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_expected_decision",
            ["expected_decision"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_hard_stop_expected",
            ["hard_stop_expected"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_source_manifest_hash",
            ["source_manifest_hash"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_source_policy_version",
            ["source_policy_version"],
        ),
        (
            "ix_bid_intake_policy_calibration_labels_created_by",
            ["created_by"],
        ),
        (
            "ix_bid_intake_policy_labels_project_active_created",
            ["project_id", "active", "created_at"],
        ),
        (
            "ix_bid_intake_policy_labels_decision_active",
            ["expected_decision", "active"],
        ),
    ):
        op.create_index(
            index_name,
            table_name,
            columns,
            unique=False,
        )


def downgrade() -> None:
    table_name = "bid_intake_policy_calibration_labels"
    if table_name in _tables():
        op.drop_table(table_name)
