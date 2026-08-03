"""add bid intake worker heartbeats

Revision ID: 20260727_0067
Revises: 20260727_0066
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260727_0067"
down_revision: Union[str, None] = "20260727_0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "bid_intake_worker_heartbeats" in _tables():
        return
    op.create_table(
        "bid_intake_worker_heartbeats",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("hostname", sa.String(length=160), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("runtime_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="online",
        ),
        sa.Column("current_run_uuid", sa.String(length=36), nullable=True),
        sa.Column(
            "capabilities_json",
            sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "worker_id",
            name="uq_bid_intake_worker_heartbeats_worker",
        ),
    )
    for name, columns, unique in (
        ("ix_bid_intake_worker_heartbeats_id", ["id"], False),
        (
            "ix_bid_intake_worker_heartbeats_worker_id",
            ["worker_id"],
            True,
        ),
        (
            "ix_bid_intake_worker_heartbeats_runtime_version",
            ["runtime_version"],
            False,
        ),
        ("ix_bid_intake_worker_heartbeats_status", ["status"], False),
        (
            "ix_bid_intake_worker_heartbeats_current_run_uuid",
            ["current_run_uuid"],
            False,
        ),
        (
            "ix_bid_intake_worker_heartbeats_last_seen_at",
            ["last_seen_at"],
            False,
        ),
        (
            "ix_bid_intake_worker_heartbeats_status_seen",
            ["status", "last_seen_at"],
            False,
        ),
    ):
        op.create_index(
            name,
            "bid_intake_worker_heartbeats",
            columns,
            unique=unique,
        )


def downgrade() -> None:
    if "bid_intake_worker_heartbeats" in _tables():
        op.drop_table("bid_intake_worker_heartbeats")
