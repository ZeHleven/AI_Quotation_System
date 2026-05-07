"""add quote feedback tables

Revision ID: 20260507_0004
Revises: 20260505_0003
Create Date: 2026-05-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260507_0004"
down_revision: Union[str, None] = "20260505_0003"
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

    if "quote_feedback" not in existing_tables:
        op.create_table(
            "quote_feedback",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("quote_id", sa.String(length=36), nullable=False),
            sa.Column("quote_job_id", sa.String(length=36), nullable=True),
            sa.Column("quote_history_id", sa.Integer(), nullable=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="async_job"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_review"),
            sa.Column("ai_total_amount", sa.Float(), nullable=True),
            sa.Column("final_total_amount", sa.Float(), nullable=True),
            sa.Column("amount_delta", sa.Float(), nullable=True),
            sa.Column("amount_delta_ratio", sa.Float(), nullable=True),
            sa.Column("ai_item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("final_item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("was_modified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("pushed_to_dingtalk", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("correction_summary_json", _long_text(), nullable=True),
            sa.Column("dify_app_version", sa.String(length=128), nullable=True),
            sa.Column("dify_workflow_version", sa.String(length=128), nullable=True),
            sa.Column("dify_prompt_version", sa.String(length=128), nullable=True),
            sa.Column("dify_release_id", sa.String(length=128), nullable=True),
            sa.Column("quote_model_name", sa.String(length=128), nullable=True),
            sa.Column("vision_model_name", sa.String(length=128), nullable=True),
            sa.Column("rag_collection_alias", sa.String(length=128), nullable=True),
            sa.Column("material_snapshot_id", sa.String(length=32), nullable=True),
            sa.Column("ai_payload_json", _long_text(), nullable=True),
            sa.Column("final_payload_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        )
    _create_index_if_missing("ix_quote_feedback_id", "quote_feedback", ["id"])
    _create_index_if_missing("ix_quote_feedback_quote_id", "quote_feedback", ["quote_id"], unique=True)
    _create_index_if_missing("ix_quote_feedback_quote_job_id", "quote_feedback", ["quote_job_id"], unique=True)
    _create_index_if_missing("ix_quote_feedback_quote_history_id", "quote_feedback", ["quote_history_id"])
    _create_index_if_missing("ix_quote_feedback_username", "quote_feedback", ["username"])
    _create_index_if_missing("ix_quote_feedback_trace_id", "quote_feedback", ["trace_id"])
    _create_index_if_missing("ix_quote_feedback_source", "quote_feedback", ["source"])
    _create_index_if_missing("ix_quote_feedback_status", "quote_feedback", ["status"])

    if "quote_corrections" not in existing_tables:
        op.create_table(
            "quote_corrections",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("feedback_id", sa.Integer(), sa.ForeignKey("quote_feedback.id"), nullable=False),
            sa.Column("quote_id", sa.String(length=36), nullable=False),
            sa.Column("quote_job_id", sa.String(length=36), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("item_index", sa.Integer(), nullable=True),
            sa.Column("project_name", sa.String(length=255), nullable=True),
            sa.Column("field_path", sa.String(length=255), nullable=False),
            sa.Column("before_value", _long_text(), nullable=True),
            sa.Column("after_value", _long_text(), nullable=True),
            sa.Column("delta_amount", sa.Float(), nullable=True),
            sa.Column("reason_category", sa.String(length=64), nullable=True),
            sa.Column("reason_text", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_quote_corrections_id", "quote_corrections", ["id"])
    _create_index_if_missing("ix_quote_corrections_feedback_id", "quote_corrections", ["feedback_id"])
    _create_index_if_missing("ix_quote_corrections_quote_id", "quote_corrections", ["quote_id"])
    _create_index_if_missing("ix_quote_corrections_quote_job_id", "quote_corrections", ["quote_job_id"])
    _create_index_if_missing("ix_quote_corrections_trace_id", "quote_corrections", ["trace_id"])

    if "quote_rag_traces" not in existing_tables:
        op.create_table(
            "quote_rag_traces",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("feedback_id", sa.Integer(), sa.ForeignKey("quote_feedback.id"), nullable=False),
            sa.Column("quote_id", sa.String(length=36), nullable=False),
            sa.Column("quote_job_id", sa.String(length=36), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("query_text", _long_text(), nullable=True),
            sa.Column("material_id", sa.String(length=64), nullable=True),
            sa.Column("item_name", sa.String(length=255), nullable=True),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("collection_alias", sa.String(length=128), nullable=True),
            sa.Column("material_snapshot_id", sa.String(length=32), nullable=True),
            sa.Column("sent_to_prompt", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("cited_by_model", sa.Boolean(), nullable=True),
            sa.Column("adopted_by_user", sa.Boolean(), nullable=True),
            sa.Column("raw_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_quote_rag_traces_id", "quote_rag_traces", ["id"])
    _create_index_if_missing("ix_quote_rag_traces_feedback_id", "quote_rag_traces", ["feedback_id"])
    _create_index_if_missing("ix_quote_rag_traces_quote_id", "quote_rag_traces", ["quote_id"])
    _create_index_if_missing("ix_quote_rag_traces_quote_job_id", "quote_rag_traces", ["quote_job_id"])
    _create_index_if_missing("ix_quote_rag_traces_trace_id", "quote_rag_traces", ["trace_id"])
    _create_index_if_missing("ix_quote_rag_traces_material_id", "quote_rag_traces", ["material_id"])


def downgrade() -> None:
    op.drop_table("quote_rag_traces")
    op.drop_table("quote_corrections")
    op.drop_table("quote_feedback")
