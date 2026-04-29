"""initial schema baseline

Revision ID: 20260428_0001
Revises:
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260428_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    existing_tables = _tables()

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("hashed_password", sa.String(length=256), nullable=True),
            sa.Column("role", sa.String(length=16), nullable=True),
            sa.Column("quota", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True),
            sa.Column("must_change_password", sa.Boolean(), nullable=True),
        )
    else:
        _add_column_if_missing("users", sa.Column("must_change_password", sa.Boolean(), nullable=True))
    _create_index_if_missing("ix_users_id", "users", ["id"])
    _create_index_if_missing("ix_users_username", "users", ["username"], unique=True)

    if "quote_history" not in existing_tables:
        op.create_table(
            "quote_history",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("total_amount", sa.Float(), nullable=True),
            sa.Column("item_count", sa.Integer(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
        )
    _create_index_if_missing("ix_quote_history_id", "quote_history", ["id"])
    _create_index_if_missing("ix_quote_history_username", "quote_history", ["username"])

    if "quote_jobs" not in existing_tables:
        op.create_table(
            "quote_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("job_id", sa.String(length=36), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("stage", sa.String(length=64), nullable=True),
            sa.Column("message", _long_text(), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=True),
            sa.Column("file_mime_type", sa.String(length=128), nullable=True),
            sa.Column("file_object_id", sa.String(length=36), nullable=True),
            sa.Column("file_base64", _long_text(), nullable=True),
            sa.Column("result_json", _long_text(), nullable=True),
            sa.Column("error_message", _long_text(), nullable=True),
            sa.Column("events_json", _long_text(), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("celery_task_id", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
    else:
        _add_column_if_missing("quote_jobs", sa.Column("file_object_id", sa.String(length=36), nullable=True))
    _create_index_if_missing("ix_quote_jobs_id", "quote_jobs", ["id"])
    _create_index_if_missing("ix_quote_jobs_job_id", "quote_jobs", ["job_id"], unique=True)
    _create_index_if_missing("ix_quote_jobs_username", "quote_jobs", ["username"])
    _create_index_if_missing("ix_quote_jobs_status", "quote_jobs", ["status"])
    _create_index_if_missing("ix_quote_jobs_trace_id", "quote_jobs", ["trace_id"])

    if "model_call_logs" not in existing_tables:
        op.create_table(
            "model_call_logs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column("endpoint_type", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("http_status", sa.Integer(), nullable=True),
            sa.Column("latency_ms", sa.Float(), nullable=True),
            sa.Column("input_chars", sa.Integer(), nullable=True),
            sa.Column("output_chars", sa.Integer(), nullable=True),
            sa.Column("estimated_cost", sa.Float(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
    _create_index_if_missing("ix_model_call_logs_id", "model_call_logs", ["id"])
    _create_index_if_missing("ix_model_call_logs_created_at", "model_call_logs", ["created_at"])
    _create_index_if_missing("ix_model_call_logs_trace_id", "model_call_logs", ["trace_id"])
    _create_index_if_missing("ix_model_call_logs_username", "model_call_logs", ["username"])
    _create_index_if_missing("ix_model_call_logs_provider", "model_call_logs", ["provider"])
    _create_index_if_missing("ix_model_call_logs_model", "model_call_logs", ["model"])
    _create_index_if_missing("ix_model_call_logs_endpoint_type", "model_call_logs", ["endpoint_type"])
    _create_index_if_missing("ix_model_call_logs_status", "model_call_logs", ["status"])

    if "file_objects" not in existing_tables:
        op.create_table(
            "file_objects",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("file_id", sa.String(length=36), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("purpose", sa.String(length=64), nullable=False),
            sa.Column("bucket", sa.String(length=128), nullable=False),
            sa.Column("object_name", sa.String(length=512), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        )
    _create_index_if_missing("ix_file_objects_id", "file_objects", ["id"])
    _create_index_if_missing("ix_file_objects_file_id", "file_objects", ["file_id"], unique=True)
    _create_index_if_missing("ix_file_objects_username", "file_objects", ["username"])
    _create_index_if_missing("ix_file_objects_purpose", "file_objects", ["purpose"])


def downgrade() -> None:
    op.drop_table("file_objects")
    op.drop_table("model_call_logs")
    op.drop_table("quote_jobs")
    op.drop_table("quote_history")
    op.drop_table("users")
