"""add phase2 client inquiries

Revision ID: 20260514_0012
Revises: 20260514_0011
Create Date: 2026-05-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0012"
down_revision: Union[str, None] = "20260514_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def _unique_constraints(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def _foreign_keys(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {constraint["name"] for constraint in inspector.get_foreign_keys(table_name) if constraint.get("name")}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    existing = _indexes(table_name) | _unique_constraints(table_name)
    if name not in existing:
        op.create_index(name, table_name, columns, unique=unique)


def _drop_index_if_exists(name: str, table_name: str) -> None:
    existing = _indexes(table_name) | _unique_constraints(table_name)
    if name in existing:
        op.drop_index(name, table_name=table_name)


def _create_fk_if_missing(
    name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
) -> None:
    if name not in _foreign_keys(source_table):
        op.create_foreign_key(name, source_table, referent_table, local_cols, remote_cols)


def _drop_fk_if_exists(name: str, table_name: str) -> None:
    if name in _foreign_keys(table_name):
        op.drop_constraint(name, table_name, type_="foreignkey")


def upgrade() -> None:
    if "client_inquiries" not in _tables():
        op.create_table(
            "client_inquiries",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("inquiry_id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("client_name", sa.String(length=128), nullable=True),
            sa.Column("client_phone", sa.String(length=64), nullable=True),
            sa.Column("inquiry_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("first_response_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("time_source", sa.String(length=24), nullable=False, server_default="default"),
            sa.Column("responder_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("first_quote_job_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("inquiry_id", name="uq_client_inquiries_inquiry_id"),
        )
    _create_index_if_missing("ix_client_inquiries_id", "client_inquiries", ["id"])
    _create_index_if_missing("ix_client_inquiries_inquiry_id", "client_inquiries", ["inquiry_id"], unique=True)
    _create_index_if_missing("ix_client_inquiries_source", "client_inquiries", ["source"])
    _create_index_if_missing("ix_client_inquiries_inquiry_time", "client_inquiries", ["inquiry_time"])
    _create_index_if_missing("ix_client_inquiries_first_response_time", "client_inquiries", ["first_response_time"])
    _create_index_if_missing("ix_client_inquiries_time_source", "client_inquiries", ["time_source"])
    _create_index_if_missing("ix_client_inquiries_responder_id", "client_inquiries", ["responder_id"])

    if "quote_jobs" in _tables():
        _add_column_if_missing("quote_jobs", sa.Column("client_inquiry_id", sa.String(length=36), nullable=True))
        _create_index_if_missing("ix_quote_jobs_client_inquiry_id", "quote_jobs", ["client_inquiry_id"])
        if "client_inquiries" in _tables():
            _create_fk_if_missing(
                "fk_quote_jobs_client_inquiry_id",
                "quote_jobs",
                "client_inquiries",
                ["client_inquiry_id"],
                ["inquiry_id"],
            )


def downgrade() -> None:
    if "quote_jobs" in _tables():
        _drop_fk_if_exists("fk_quote_jobs_client_inquiry_id", "quote_jobs")
        _drop_index_if_exists("ix_quote_jobs_client_inquiry_id", "quote_jobs")
        _drop_column_if_exists("quote_jobs", "client_inquiry_id")
    if "client_inquiries" in _tables():
        op.drop_table("client_inquiries")
