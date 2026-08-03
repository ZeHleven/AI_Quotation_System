"""remove retired business ledger

Revision ID: 20260731_0079
Revises: 20260731_0078
Create Date: 2026-07-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_0079"
down_revision: Union[str, None] = "20260731_0078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEDGER_COLUMNS = (
    "direction",
    "stage",
    "next_followup_at",
    "cancelled_at",
    "cancelled_by_id",
    "cancel_reason",
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return set()
    return {
        str(index["name"])
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }


def _foreign_keys(table_name: str) -> set[str]:
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return set()
    return {
        str(foreign_key["name"])
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key.get("name")
    }


def _create_index_if_missing(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=False)


def _purge_outbound_rows() -> None:
    columns = _columns("client_inquiries")
    if "direction" not in columns:
        return

    bind = op.get_bind()
    if "quote_jobs" in _tables() and "client_inquiry_id" in _columns("quote_jobs"):
        bind.execute(
            sa.text(
                """
                UPDATE quote_jobs
                SET client_inquiry_id = NULL
                WHERE client_inquiry_id IN (
                    SELECT inquiry_id
                    FROM client_inquiries
                    WHERE direction = :direction
                )
                """
            ),
            {"direction": "outbound"},
        )
    bind.execute(
        sa.text("DELETE FROM client_inquiries WHERE direction = :direction"),
        {"direction": "outbound"},
    )


def _restore_missing_response_times() -> None:
    columns = _columns("client_inquiries")
    if "first_response_time" not in columns:
        return
    fallback_parts = [
        column_name
        for column_name in ("inquiry_time", "created_at")
        if column_name in columns
    ]
    fallback_parts.append("CURRENT_TIMESTAMP")
    op.execute(
        sa.text(
            "UPDATE client_inquiries "
            f"SET first_response_time = COALESCE({', '.join(fallback_parts)}) "
            "WHERE first_response_time IS NULL"
        )
    )


def _drop_ledger_columns() -> None:
    existing = _columns("client_inquiries")
    dialect_name = op.get_bind().dialect.name
    if "ix_client_inquiries_stage_next_followup_at" in _indexes("client_inquiries"):
        op.drop_index(
            "ix_client_inquiries_stage_next_followup_at",
            table_name="client_inquiries",
        )
    if dialect_name == "sqlite":
        with op.batch_alter_table("client_inquiries", recreate="always") as batch_op:
            if "first_response_time" in existing:
                batch_op.alter_column(
                    "first_response_time",
                    existing_type=sa.DateTime(timezone=True),
                    nullable=False,
                )
            for column_name in _LEDGER_COLUMNS:
                if column_name in existing:
                    batch_op.drop_column(column_name)
        return

    if "fk_client_inquiries_cancelled_by_id" in _foreign_keys("client_inquiries"):
        op.drop_constraint(
            "fk_client_inquiries_cancelled_by_id",
            "client_inquiries",
            type_="foreignkey",
        )
    if "first_response_time" in existing:
        op.alter_column(
            "client_inquiries",
            "first_response_time",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    for column_name in reversed(_LEDGER_COLUMNS):
        if column_name in _columns("client_inquiries"):
            op.drop_column("client_inquiries", column_name)


def upgrade() -> None:
    """Delete only outbound ledger data and restore the inbound inquiry schema."""

    if "client_inquiry_events" in _tables():
        op.drop_table("client_inquiry_events")
    if "client_inquiries" not in _tables():
        return

    _purge_outbound_rows()
    _restore_missing_response_times()
    _drop_ledger_columns()


def _restore_ledger_columns() -> None:
    existing = _columns("client_inquiries")
    dialect_name = op.get_bind().dialect.name

    if dialect_name == "sqlite":
        with op.batch_alter_table("client_inquiries", recreate="always") as batch_op:
            if "first_response_time" in existing:
                batch_op.alter_column(
                    "first_response_time",
                    existing_type=sa.DateTime(timezone=True),
                    nullable=True,
                )
            if "direction" not in existing:
                batch_op.add_column(
                    sa.Column(
                        "direction",
                        sa.String(length=16),
                        nullable=False,
                        server_default="inbound",
                    )
                )
            if "stage" not in existing:
                batch_op.add_column(sa.Column("stage", sa.String(length=32), nullable=True))
            if "next_followup_at" not in existing:
                batch_op.add_column(sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True))
            if "cancelled_at" not in existing:
                batch_op.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
            if "cancelled_by_id" not in existing:
                batch_op.add_column(sa.Column("cancelled_by_id", sa.Integer(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_client_inquiries_cancelled_by_id",
                    "users",
                    ["cancelled_by_id"],
                    ["id"],
                )
            if "cancel_reason" not in existing:
                batch_op.add_column(sa.Column("cancel_reason", sa.Text(), nullable=True))
    else:
        if "first_response_time" in existing:
            op.alter_column(
                "client_inquiries",
                "first_response_time",
                existing_type=sa.DateTime(timezone=True),
                nullable=True,
            )
        if "direction" not in existing:
            op.add_column(
                "client_inquiries",
                sa.Column(
                    "direction",
                    sa.String(length=16),
                    nullable=False,
                    server_default="inbound",
                ),
            )
        if "stage" not in existing:
            op.add_column("client_inquiries", sa.Column("stage", sa.String(length=32), nullable=True))
        if "next_followup_at" not in existing:
            op.add_column(
                "client_inquiries",
                sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "cancelled_at" not in existing:
            op.add_column(
                "client_inquiries",
                sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "cancelled_by_id" not in existing:
            op.add_column(
                "client_inquiries",
                sa.Column("cancelled_by_id", sa.Integer(), nullable=True),
            )
        if "cancel_reason" not in existing:
            op.add_column("client_inquiries", sa.Column("cancel_reason", sa.Text(), nullable=True))
        if "fk_client_inquiries_cancelled_by_id" not in _foreign_keys("client_inquiries"):
            op.create_foreign_key(
                "fk_client_inquiries_cancelled_by_id",
                "client_inquiries",
                "users",
                ["cancelled_by_id"],
                ["id"],
            )

    _create_index_if_missing(
        "ix_client_inquiries_stage_next_followup_at",
        "client_inquiries",
        ["stage", "next_followup_at"],
    )


def downgrade() -> None:
    """Restore the retired schema only; deleted outbound rows are not restored."""

    if "client_inquiries" not in _tables():
        return

    _restore_ledger_columns()
    if "client_inquiry_events" not in _tables():
        op.create_table(
            "client_inquiry_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("inquiry_id", sa.String(length=36), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("old_value", sa.String(length=64), nullable=True),
            sa.Column("new_value", sa.String(length=64), nullable=True),
            sa.Column("operator_id", sa.Integer(), nullable=True),
            sa.Column(
                "operated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("before_json", sa.JSON(), nullable=True),
            sa.Column("after_json", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(
                ["inquiry_id"],
                ["client_inquiries.inquiry_id"],
                name="fk_client_inquiry_events_inquiry_id",
            ),
            sa.ForeignKeyConstraint(
                ["operator_id"],
                ["users.id"],
                name="fk_client_inquiry_events_operator_id",
            ),
        )
    _create_index_if_missing(
        "ix_client_inquiry_events_inquiry_id",
        "client_inquiry_events",
        ["inquiry_id"],
    )
    _create_index_if_missing(
        "ix_client_inquiry_events_operator_id_operated_at",
        "client_inquiry_events",
        ["operator_id", "operated_at"],
    )
