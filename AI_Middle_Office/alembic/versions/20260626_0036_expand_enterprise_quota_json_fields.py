"""expand enterprise quota json fields

Revision ID: 20260626_0036
Revises: 20260626_0035
Create Date: 2026-06-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260626_0036"
down_revision: Union[str, None] = "20260626_0035"
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
    for column_name in ("summary_json", "issues_json"):
        _alter_text_to_longtext("cost_import_batches", column_name)

    for column_name in ("summary_json", "notes"):
        _alter_text_to_longtext("enterprise_quota_versions", column_name)

    _alter_text_to_longtext("enterprise_quota_sections", "raw_row_json")

    for column_name in ("work_content", "raw_row_json"):
        _alter_text_to_longtext("enterprise_quota_items", column_name)

    _alter_text_to_longtext("enterprise_cost_resources", "raw_row_json")
    _alter_text_to_longtext("enterprise_quota_components", "raw_row_json")


def downgrade() -> None:
    # Keep wider text fields on downgrade to avoid truncating import evidence.
    pass
