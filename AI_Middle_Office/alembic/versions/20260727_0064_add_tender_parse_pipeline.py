"""add reliable tender source and parse job pipeline

Revision ID: 20260727_0064
Revises: 20260727_0063
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0064"
down_revision: Union[str, None] = "20260727_0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "bid_tender_source_objects" not in tables:
        op.create_table(
            "bid_tender_source_objects",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("source_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "file_object_id",
                sa.String(length=36),
                sa.ForeignKey("file_objects.file_id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("document_key", sa.String(length=160), nullable=False),
            sa.Column(
                "file_type",
                sa.String(length=64),
                nullable=False,
                server_default="tender_document",
            ),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="stored",
            ),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
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
                "source_uuid",
                name="uq_bid_tender_source_objects_uuid",
            ),
            sa.UniqueConstraint(
                "project_id",
                "document_key",
                "sha256",
                name="uq_bid_tender_source_objects_project_key_sha",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_tender_source_objects_id", ["id"], False),
            ("ix_bid_tender_source_objects_source_uuid", ["source_uuid"], True),
            ("ix_bid_tender_source_objects_project_id", ["project_id"], False),
            (
                "ix_bid_tender_source_objects_file_object_id",
                ["file_object_id"],
                False,
            ),
            (
                "ix_bid_tender_source_objects_document_key",
                ["document_key"],
                False,
            ),
            ("ix_bid_tender_source_objects_file_type", ["file_type"], False),
            ("ix_bid_tender_source_objects_sha256", ["sha256"], False),
            ("ix_bid_tender_source_objects_status", ["status"], False),
            ("ix_bid_tender_source_objects_created_by", ["created_by"], False),
            (
                "ix_bid_tender_source_objects_project_created",
                ["project_id", "created_at"],
                False,
            ),
        ):
            op.create_index(
                name,
                "bid_tender_source_objects",
                columns,
                unique=unique,
            )

    tables = _tables()
    if "bid_tender_parse_jobs" not in tables:
        op.create_table(
            "bid_tender_parse_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("job_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_object_id",
                sa.Integer(),
                sa.ForeignKey(
                    "bid_tender_source_objects.id",
                    ondelete="CASCADE",
                ),
                nullable=False,
            ),
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
            sa.Column("parser_version", sa.String(length=64), nullable=False),
            sa.Column(
                "bid_project_file_id",
                sa.Integer(),
                sa.ForeignKey("bid_project_files.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column(
                "evidence_document_uuid",
                sa.String(length=36),
                nullable=True,
            ),
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
                name="uq_bid_tender_parse_jobs_uuid",
            ),
            sa.UniqueConstraint(
                "source_object_id",
                "parser_version",
                name="uq_bid_tender_parse_jobs_source_parser",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_tender_parse_jobs_id", ["id"], False),
            ("ix_bid_tender_parse_jobs_job_uuid", ["job_uuid"], True),
            ("ix_bid_tender_parse_jobs_project_id", ["project_id"], False),
            (
                "ix_bid_tender_parse_jobs_source_object_id",
                ["source_object_id"],
                False,
            ),
            ("ix_bid_tender_parse_jobs_status", ["status"], False),
            ("ix_bid_tender_parse_jobs_stage", ["stage"], False),
            ("ix_bid_tender_parse_jobs_parser_version", ["parser_version"], False),
            (
                "ix_bid_tender_parse_jobs_bid_project_file_id",
                ["bid_project_file_id"],
                False,
            ),
            (
                "ix_bid_tender_parse_jobs_evidence_document_uuid",
                ["evidence_document_uuid"],
                False,
            ),
            ("ix_bid_tender_parse_jobs_celery_task_id", ["celery_task_id"], False),
            ("ix_bid_tender_parse_jobs_error_code", ["error_code"], False),
            ("ix_bid_tender_parse_jobs_created_by", ["created_by"], False),
            (
                "ix_bid_tender_parse_jobs_project_status_created",
                ["project_id", "status", "created_at"],
                False,
            ),
        ):
            op.create_index(
                name,
                "bid_tender_parse_jobs",
                columns,
                unique=unique,
            )

    tables = _tables()
    if "bid_tender_parse_job_events" not in tables:
        op.create_table(
            "bid_tender_parse_job_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("event_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "parse_job_id",
                sa.Integer(),
                sa.ForeignKey("bid_tender_parse_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("stage", sa.String(length=32), nullable=False),
            sa.Column(
                "attempt_no",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "event_uuid",
                name="uq_bid_tender_parse_job_events_uuid",
            ),
        )
        for name, columns, unique in (
            ("ix_bid_tender_parse_job_events_id", ["id"], False),
            (
                "ix_bid_tender_parse_job_events_event_uuid",
                ["event_uuid"],
                True,
            ),
            (
                "ix_bid_tender_parse_job_events_parse_job_id",
                ["parse_job_id"],
                False,
            ),
            (
                "ix_bid_tender_parse_job_events_event_type",
                ["event_type"],
                False,
            ),
            ("ix_bid_tender_parse_job_events_status", ["status"], False),
            ("ix_bid_tender_parse_job_events_stage", ["stage"], False),
            (
                "ix_bid_tender_parse_job_events_job_created",
                ["parse_job_id", "created_at"],
                False,
            ),
        ):
            op.create_index(
                name,
                "bid_tender_parse_job_events",
                columns,
                unique=unique,
            )


def downgrade() -> None:
    for table_name in (
        "bid_tender_parse_job_events",
        "bid_tender_parse_jobs",
        "bid_tender_source_objects",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
