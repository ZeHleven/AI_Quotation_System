"""add prompt regression tables

Revision ID: 20260507_0005
Revises: 20260507_0004
Create Date: 2026-05-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260507_0005"
down_revision: Union[str, None] = "20260507_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    existing_tables = _tables()

    if "prompt_regression_cases" not in existing_tables:
        op.create_table(
            "prompt_regression_cases",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("source_feedback_id", sa.Integer(), sa.ForeignKey("quote_feedback.id"), nullable=False),
            sa.Column("quote_id", sa.String(length=36), nullable=False),
            sa.Column("quote_job_id", sa.String(length=36), nullable=True),
            sa.Column("quote_history_id", sa.Integer(), nullable=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("case_name", sa.String(length=255), nullable=True),
            sa.Column("request_text", _long_text(), nullable=True),
            sa.Column("source_status", sa.String(length=32), nullable=False),
            sa.Column("source_prompt_version", sa.String(length=128), nullable=True),
            sa.Column("source_workflow_version", sa.String(length=128), nullable=True),
            sa.Column("source_release_id", sa.String(length=128), nullable=True),
            sa.Column("rag_collection_alias", sa.String(length=128), nullable=True),
            sa.Column("material_snapshot_id", sa.String(length=32), nullable=True),
            sa.Column("ai_total_amount", sa.Float(), nullable=True),
            sa.Column("expected_total_amount", sa.Float(), nullable=True),
            sa.Column("amount_delta", sa.Float(), nullable=True),
            sa.Column("amount_delta_ratio", sa.Float(), nullable=True),
            sa.Column("ai_item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expected_item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("correction_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("format_error_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("missing_item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("was_modified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("ai_payload_json", _long_text(), nullable=True),
            sa.Column("expected_payload_json", _long_text(), nullable=True),
            sa.Column("corrections_json", _long_text(), nullable=True),
            sa.Column("metadata_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_prompt_regression_cases_id", "prompt_regression_cases", ["id"])
    _create_index_if_missing(
        "ix_prompt_regression_cases_source_feedback_id",
        "prompt_regression_cases",
        ["source_feedback_id"],
        unique=True,
    )
    _create_index_if_missing("ix_prompt_regression_cases_quote_id", "prompt_regression_cases", ["quote_id"])
    _create_index_if_missing("ix_prompt_regression_cases_quote_job_id", "prompt_regression_cases", ["quote_job_id"])
    _create_index_if_missing(
        "ix_prompt_regression_cases_quote_history_id",
        "prompt_regression_cases",
        ["quote_history_id"],
    )
    _create_index_if_missing("ix_prompt_regression_cases_username", "prompt_regression_cases", ["username"])
    _create_index_if_missing("ix_prompt_regression_cases_source_status", "prompt_regression_cases", ["source_status"])
    _create_index_if_missing(
        "ix_prompt_regression_cases_source_prompt_version",
        "prompt_regression_cases",
        ["source_prompt_version"],
    )
    _create_index_if_missing("ix_prompt_regression_cases_active", "prompt_regression_cases", ["active"])

    if "prompt_regression_runs" not in existing_tables:
        op.create_table(
            "prompt_regression_runs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("triggered_by", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("prompt_version", sa.String(length=128), nullable=True),
            sa.Column("baseline_prompt_version", sa.String(length=128), nullable=True),
            sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("modified_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("avg_abs_amount_delta", sa.Float(), nullable=True),
            sa.Column("avg_abs_delta_ratio", sa.Float(), nullable=True),
            sa.Column("exact_total_match_rate", sa.Float(), nullable=True),
            sa.Column("format_error_rate", sa.Float(), nullable=True),
            sa.Column("missing_item_rate", sa.Float(), nullable=True),
            sa.Column("rejection_rate", sa.Float(), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("metrics_json", _long_text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
        )
    _create_index_if_missing("ix_prompt_regression_runs_id", "prompt_regression_runs", ["id"])
    _create_index_if_missing("ix_prompt_regression_runs_status", "prompt_regression_runs", ["status"])
    _create_index_if_missing("ix_prompt_regression_runs_prompt_version", "prompt_regression_runs", ["prompt_version"])


def downgrade() -> None:
    op.drop_table("prompt_regression_runs")
    op.drop_table("prompt_regression_cases")
