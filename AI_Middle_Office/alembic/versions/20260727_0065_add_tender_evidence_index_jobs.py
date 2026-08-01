"""add tender evidence hybrid index jobs

Revision ID: 20260727_0065
Revises: 20260727_0064
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0065"
down_revision: Union[str, None] = "20260727_0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "bid_evidence_index_jobs" in _tables():
        return
    op.create_table(
        "bid_evidence_index_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("job_uuid", sa.String(length=36), nullable=False),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "manifest_id",
            sa.Integer(),
            sa.ForeignKey("bid_evidence_manifests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("manifest_version", sa.Integer(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("index_schema_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "stage",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "requested_block_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "indexed_block_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("search_service_url", sa.String(length=500), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=160), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "job_uuid",
            name="uq_bid_evidence_index_jobs_uuid",
        ),
        sa.UniqueConstraint(
            "manifest_id",
            "index_schema_version",
            name="uq_bid_evidence_index_jobs_manifest_schema",
        ),
    )
    for name, columns, unique in (
        ("ix_bid_evidence_index_jobs_id", ["id"], False),
        ("ix_bid_evidence_index_jobs_job_uuid", ["job_uuid"], True),
        ("ix_bid_evidence_index_jobs_project_id", ["project_id"], False),
        ("ix_bid_evidence_index_jobs_manifest_id", ["manifest_id"], False),
        (
            "ix_bid_evidence_index_jobs_manifest_version",
            ["manifest_version"],
            False,
        ),
        ("ix_bid_evidence_index_jobs_manifest_hash", ["manifest_hash"], False),
        (
            "ix_bid_evidence_index_jobs_index_schema_version",
            ["index_schema_version"],
            False,
        ),
        ("ix_bid_evidence_index_jobs_status", ["status"], False),
        ("ix_bid_evidence_index_jobs_stage", ["stage"], False),
        (
            "ix_bid_evidence_index_jobs_celery_task_id",
            ["celery_task_id"],
            False,
        ),
        ("ix_bid_evidence_index_jobs_error_code", ["error_code"], False),
        ("ix_bid_evidence_index_jobs_created_by", ["created_by"], False),
        (
            "ix_bid_evidence_index_jobs_project_status_created",
            ["project_id", "status", "created_at"],
            False,
        ),
    ):
        op.create_index(
            name,
            "bid_evidence_index_jobs",
            columns,
            unique=unique,
        )


def downgrade() -> None:
    if "bid_evidence_index_jobs" in _tables():
        op.drop_table("bid_evidence_index_jobs")
