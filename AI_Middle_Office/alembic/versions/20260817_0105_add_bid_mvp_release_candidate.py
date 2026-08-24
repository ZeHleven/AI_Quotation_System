"""add bid MVP release candidate authority

Revision ID: 20260817_0105
Revises: 20260817_0104
Create Date: 2026-08-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260817_0105"
down_revision: Union[str, None] = "20260817_0104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_mvp_release_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("run_validation_id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="frozen", nullable=False),
        sa.Column("acceptance_outcome", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("review_json", sa.JSON(), nullable=False),
        sa.Column("source_hashes_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("release_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status = 'frozen'",
            name="ck_bid_mvp_release_candidates_status",
        ),
        sa.CheckConstraint(
            "acceptance_outcome IN ('accepted', 'accepted_with_follow_up')",
            name="ck_bid_mvp_release_candidates_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_mvp_release_candidates_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["bid_preliminary_reports.id"],
            name="fk_bid_mvp_release_candidates_report",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "run_validation_id"],
            ["bid_run_validations.run_id", "bid_run_validations.id"],
            name="fk_bid_mvp_release_candidates_validation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_snapshot_id"],
            ["bid_enterprise_snapshots.id"],
            name="fk_bid_mvp_release_candidates_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name="fk_bid_mvp_release_candidates_reviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_mvp_release_candidates"),
        sa.UniqueConstraint("run_id", name="uq_bid_mvp_release_candidates_run"),
        sa.UniqueConstraint("version", name="uq_bid_mvp_release_candidates_version"),
        sa.UniqueConstraint(
            "candidate_hash",
            name="uq_bid_mvp_release_candidates_candidate_hash",
        ),
        sa.UniqueConstraint(
            "release_hash",
            name="uq_bid_mvp_release_candidates_hash",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_mvp_release_candidates_assessment",
        "bid_mvp_release_candidates",
        ["assessment_id", "reviewed_at"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0105 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable business acceptance checks"
        )
    bind = op.get_bind()
    count = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM bid_mvp_release_candidates")
        ).scalar()
        or 0
    )
    if count:
        raise RuntimeError(
            "0105 downgrade would erase immutable MVP release acceptance lineage"
        )
    op.drop_table("bid_mvp_release_candidates")
