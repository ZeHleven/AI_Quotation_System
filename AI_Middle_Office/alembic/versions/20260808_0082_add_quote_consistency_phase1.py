"""add quote consistency phase one primitives

Revision ID: 20260808_0082
Revises: 20260801_0081
Create Date: 2026-08-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260808_0082"
down_revision: Union[str, None] = "20260801_0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _large_text():
    return mysql.LONGTEXT() if op.get_bind().dialect.name == "mysql" else sa.Text()


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("quota_reserved", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("quote_jobs", sa.Column("source_job_id", sa.String(length=36), nullable=True))
    op.add_column("quote_jobs", sa.Column("attempt_id", sa.String(length=36), nullable=True))
    op.add_column("quote_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_quote_jobs_source_job_id",
        "quote_jobs",
        "quote_jobs",
        ["source_job_id"],
        ["job_id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_quote_jobs_source_job_id", "quote_jobs", ["source_job_id"])
    op.create_index("ix_quote_jobs_attempt_id", "quote_jobs", ["attempt_id"])

    op.create_table(
        "quote_quota_reservations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("reservation_id", sa.String(length=36), nullable=False),
        sa.Column(
            "quote_job_id",
            sa.String(length=36),
            sa.ForeignKey("quote_jobs.job_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="reserved", nullable=False),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("reservation_id", name="uq_quote_quota_reservations_id"),
        sa.UniqueConstraint("quote_job_id", name="uq_quote_quota_reservations_job"),
    )
    op.create_index("ix_quote_quota_reservations_quote_job_id", "quote_quota_reservations", ["quote_job_id"])
    op.create_index("ix_quote_quota_reservations_user_id", "quote_quota_reservations", ["user_id"])
    op.create_index("ix_quote_quota_reservations_status", "quote_quota_reservations", ["status"])

    op.create_table(
        "quote_push_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column(
            "quote_job_id",
            sa.String(length=36),
            sa.ForeignKey("quote_jobs.job_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_json", _large_text(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="sending", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("external_status_code", sa.Integer(), nullable=True),
        sa.Column("external_response", _large_text(), nullable=True),
        sa.Column("error_message", _large_text(), nullable=True),
        sa.Column(
            "quote_history_id",
            sa.Integer(),
            sa.ForeignKey("quote_history.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result_json", _large_text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("external_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_quote_push_attempts_idempotency"),
    )
    op.create_index("ix_quote_push_attempts_quote_job_id", "quote_push_attempts", ["quote_job_id"])
    op.create_index("ix_quote_push_attempts_username", "quote_push_attempts", ["username"])
    op.create_index("ix_quote_push_attempts_payload_sha256", "quote_push_attempts", ["payload_sha256"])
    op.create_index("ix_quote_push_attempts_status", "quote_push_attempts", ["status"])
    op.create_index("ix_quote_push_attempts_quote_history_id", "quote_push_attempts", ["quote_history_id"])


def downgrade() -> None:
    op.drop_table("quote_push_attempts")
    op.drop_table("quote_quota_reservations")
    op.drop_index("ix_quote_jobs_attempt_id", table_name="quote_jobs")
    op.drop_index("ix_quote_jobs_source_job_id", table_name="quote_jobs")
    op.drop_constraint("fk_quote_jobs_source_job_id", "quote_jobs", type_="foreignkey")
    op.drop_column("quote_jobs", "started_at")
    op.drop_column("quote_jobs", "attempt_id")
    op.drop_column("quote_jobs", "source_job_id")
    op.drop_column("users", "quota_reserved")
