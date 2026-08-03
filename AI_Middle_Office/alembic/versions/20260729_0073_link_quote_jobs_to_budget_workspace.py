"""link chat quote jobs to budget project pricing drafts

Revision ID: 20260729_0073
Revises: 20260728_0072
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260729_0073"
down_revision: Union[str, None] = "20260728_0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def _foreign_keys(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
        if item.get("name")
    }


def upgrade() -> None:
    if "quote_jobs" not in _tables():
        return
    columns = _columns("quote_jobs")
    additions = (
        (
            "budget_project_id",
            sa.Column("budget_project_id", sa.Integer(), nullable=True),
        ),
        (
            "budget_pricing_draft_id",
            sa.Column("budget_pricing_draft_id", sa.Integer(), nullable=True),
        ),
        (
            "budget_workspace_source_sha256",
            sa.Column("budget_workspace_source_sha256", sa.String(length=64), nullable=True),
        ),
        (
            "budget_workspace_synced_at",
            sa.Column("budget_workspace_synced_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("quote_jobs", column)

    indexes = _indexes("quote_jobs")
    if "ix_quote_jobs_budget_project_id" not in indexes:
        op.create_index(
            "ix_quote_jobs_budget_project_id",
            "quote_jobs",
            ["budget_project_id"],
            unique=False,
        )
    if "ix_quote_jobs_budget_pricing_draft_id" not in indexes:
        op.create_index(
            "ix_quote_jobs_budget_pricing_draft_id",
            "quote_jobs",
            ["budget_pricing_draft_id"],
            unique=False,
        )

    # SQLite's batch mode is required when migrations are exercised in local
    # smoke databases; MySQL can add the same named constraints in-place.
    foreign_keys = _foreign_keys("quote_jobs")
    with op.batch_alter_table("quote_jobs") as batch_op:
        if (
            "projects" in _tables()
            and "fk_quote_jobs_budget_project_id_projects" not in foreign_keys
        ):
            batch_op.create_foreign_key(
                "fk_quote_jobs_budget_project_id_projects",
                "projects",
                ["budget_project_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if (
            "budget_project_pricing_drafts" in _tables()
            and "fk_quote_jobs_budget_pricing_draft_id_drafts" not in foreign_keys
        ):
            batch_op.create_foreign_key(
                "fk_quote_jobs_budget_pricing_draft_id_drafts",
                "budget_project_pricing_drafts",
                ["budget_pricing_draft_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    if "quote_jobs" not in _tables():
        return
    foreign_keys = _foreign_keys("quote_jobs")
    with op.batch_alter_table("quote_jobs") as batch_op:
        if "fk_quote_jobs_budget_pricing_draft_id_drafts" in foreign_keys:
            batch_op.drop_constraint(
                "fk_quote_jobs_budget_pricing_draft_id_drafts",
                type_="foreignkey",
            )
        if "fk_quote_jobs_budget_project_id_projects" in foreign_keys:
            batch_op.drop_constraint(
                "fk_quote_jobs_budget_project_id_projects",
                type_="foreignkey",
            )
    indexes = _indexes("quote_jobs")
    if "ix_quote_jobs_budget_pricing_draft_id" in indexes:
        op.drop_index("ix_quote_jobs_budget_pricing_draft_id", table_name="quote_jobs")
    if "ix_quote_jobs_budget_project_id" in indexes:
        op.drop_index("ix_quote_jobs_budget_project_id", table_name="quote_jobs")
    columns = _columns("quote_jobs")
    for name in (
        "budget_workspace_synced_at",
        "budget_workspace_source_sha256",
        "budget_pricing_draft_id",
        "budget_project_id",
    ):
        if name in columns:
            op.drop_column("quote_jobs", name)
