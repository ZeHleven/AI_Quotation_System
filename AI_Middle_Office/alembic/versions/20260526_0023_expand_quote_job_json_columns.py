"""expand quote job json columns

Revision ID: 20260526_0023
Revises: 20260526_0022
Create Date: 2026-05-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260526_0023"
down_revision: Union[str, None] = "20260526_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _alter_text_to_longtext(table_name: str, column_name: str, nullable: bool = True) -> None:
    if column_name not in _columns(table_name):
        return
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Text(),
            type_=mysql.LONGTEXT(),
            existing_nullable=nullable,
        )


def upgrade() -> None:
    for column_name in ("message", "file_base64", "result_json", "error_message", "events_json"):
        _alter_text_to_longtext("quote_jobs", column_name)

    for column_name in ("message", "payload_json"):
        _alter_text_to_longtext("quote_job_events", column_name)


def downgrade() -> None:
    # Keep the wider type on downgrade to avoid truncating existing quote audit data.
    pass
