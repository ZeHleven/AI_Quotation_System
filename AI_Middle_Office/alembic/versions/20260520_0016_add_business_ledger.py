"""add biz1a business ledger

Revision ID: 20260520_0016
Revises: 20260514_0015
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_0016"
down_revision: Union[str, None] = "20260514_0015"
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
        return

    if "direction" not in _columns("client_inquiries"):
        op.add_column("client_inquiries", sa.Column("direction", sa.String(length=16), nullable=True))
    op.execute("UPDATE client_inquiries SET direction='inbound' WHERE direction IS NULL")
    op.alter_column(
        "client_inquiries",
        "direction",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="inbound",
    )

    _add_column_if_missing("client_inquiries", sa.Column("stage", sa.String(length=32), nullable=True))
    _add_column_if_missing("client_inquiries", sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("client_inquiries", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("client_inquiries", sa.Column("cancelled_by_id", sa.Integer(), nullable=True))
    _add_column_if_missing("client_inquiries", sa.Column("cancel_reason", sa.Text(), nullable=True))
    if "cancelled_by_id" in _columns("client_inquiries"):
        _create_fk_if_missing(
            "fk_client_inquiries_cancelled_by_id",
            "client_inquiries",
            "users",
            ["cancelled_by_id"],
            ["id"],
        )

    if "first_response_time" in _columns("client_inquiries"):
        op.alter_column(
            "client_inquiries",
            "first_response_time",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )

    _create_index_if_missing("ix_client_inquiries_responder_id", "client_inquiries", ["responder_id"])
    _create_index_if_missing(
        "ix_client_inquiries_stage_next_followup_at",
        "client_inquiries",
        ["stage", "next_followup_at"],
    )

    if "client_inquiry_events" not in _tables():
        op.create_table(
            "client_inquiry_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("inquiry_id", sa.String(length=36), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("old_value", sa.String(length=64), nullable=True),
            sa.Column("new_value", sa.String(length=64), nullable=True),
            sa.Column("operator_id", sa.Integer(), nullable=True),
            sa.Column("operated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("before_json", sa.JSON(), nullable=True),
            sa.Column("after_json", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["inquiry_id"], ["client_inquiries.inquiry_id"], name="fk_client_inquiry_events_inquiry_id"),
            sa.ForeignKeyConstraint(["operator_id"], ["users.id"], name="fk_client_inquiry_events_operator_id"),
        )
    _create_index_if_missing("ix_client_inquiry_events_inquiry_id", "client_inquiry_events", ["inquiry_id"])
    _create_index_if_missing(
        "ix_client_inquiry_events_operator_id_operated_at",
        "client_inquiry_events",
        ["operator_id", "operated_at"],
    )


def downgrade() -> None:
    if "client_inquiry_events" in _tables():
        op.drop_table("client_inquiry_events")

    if "client_inquiries" not in _tables():
        return

    _drop_index_if_exists("ix_client_inquiries_stage_next_followup_at", "client_inquiries")
    _drop_fk_if_exists("fk_client_inquiries_cancelled_by_id", "client_inquiries")
    _drop_column_if_exists("client_inquiries", "cancel_reason")
    _drop_column_if_exists("client_inquiries", "cancelled_by_id")
    _drop_column_if_exists("client_inquiries", "cancelled_at")
    _drop_column_if_exists("client_inquiries", "next_followup_at")
    _drop_column_if_exists("client_inquiries", "stage")
    _drop_column_if_exists("client_inquiries", "direction")
    # BIZ-1a allows outbound rows without first response time. Re-applying NOT NULL here
    # would fail once outbound data exists, so downgrade leaves this column nullable.
