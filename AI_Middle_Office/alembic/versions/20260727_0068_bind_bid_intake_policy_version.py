"""bind bid intake assessments to a policy version

Revision ID: 20260727_0068
Revises: 20260727_0067
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0068"
down_revision: Union[str, None] = "20260727_0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {
        str(item["name"])
        for item in inspector.get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {
        str(item["name"])
        for item in inspector.get_indexes(table_name)
    }


def upgrade() -> None:
    table_name = "bid_intake_assessments"
    if "policy_version" not in _columns(table_name):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "policy_version",
                    sa.String(length=64),
                    nullable=False,
                    server_default="qs_bid_decision_policy_2026_01",
                )
            )
    index_name = "ix_bid_intake_assessments_policy_version"
    if index_name not in _indexes(table_name):
        op.create_index(
            index_name,
            table_name,
            ["policy_version"],
            unique=False,
        )


def downgrade() -> None:
    table_name = "bid_intake_assessments"
    if "policy_version" not in _columns(table_name):
        return
    index_name = "ix_bid_intake_assessments_policy_version"
    if index_name in _indexes(table_name):
        op.drop_index(index_name, table_name=table_name)
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_column("policy_version")
