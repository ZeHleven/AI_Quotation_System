"""persist pricing-agent decisions and quote draft confirmation

Revision ID: 20260731_0077
Revises: 20260731_0076
Create Date: 2026-07-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260731_0077"
down_revision: Union[str, None] = "20260731_0076"
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


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    if "pricing_agent_runs" not in _tables() or "pricing_agent_run_lines" not in _tables():
        return

    run_columns = _columns("pricing_agent_runs")
    for name, column in (
        ("confirmed_quote_job_id", sa.Column("confirmed_quote_job_id", sa.String(length=36), nullable=True)),
        ("confirmed_preview_draft_id", sa.Column("confirmed_preview_draft_id", sa.Integer(), nullable=True)),
        ("confirmed_by", sa.Column("confirmed_by", sa.Integer(), nullable=True)),
        ("confirmed_at", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)),
        ("confirmation_hash", sa.Column("confirmation_hash", sa.String(length=64), nullable=True)),
        ("confirmation_json", sa.Column("confirmation_json", _long_text(), nullable=True)),
    ):
        if name not in run_columns:
            op.add_column("pricing_agent_runs", column)

    line_columns = _columns("pricing_agent_run_lines")
    for name, column in (
        (
            "selection_origin",
            sa.Column(
                "selection_origin",
                sa.String(length=24),
                nullable=False,
                server_default="automatic",
            ),
        ),
        ("selected_candidate_json", sa.Column("selected_candidate_json", _long_text(), nullable=True)),
        ("manual_selected_by", sa.Column("manual_selected_by", sa.Integer(), nullable=True)),
        ("manual_selected_at", sa.Column("manual_selected_at", sa.DateTime(timezone=True), nullable=True)),
        (
            "decision_revision",
            sa.Column("decision_revision", sa.Integer(), nullable=False, server_default="0"),
        ),
    ):
        if name not in line_columns:
            op.add_column("pricing_agent_run_lines", column)

    run_indexes = _indexes("pricing_agent_runs")
    for name, columns, unique in (
        ("ix_pricing_agent_runs_confirmed_quote_job_id", ["confirmed_quote_job_id"], True),
        ("ix_pricing_agent_runs_confirmed_preview_draft_id", ["confirmed_preview_draft_id"], True),
        ("ix_pricing_agent_runs_confirmed_by", ["confirmed_by"], False),
        ("ix_pricing_agent_runs_confirmation_hash", ["confirmation_hash"], False),
    ):
        if name not in run_indexes:
            op.create_index(name, "pricing_agent_runs", columns, unique=unique)

    line_indexes = _indexes("pricing_agent_run_lines")
    if "ix_pricing_agent_run_lines_manual_selected_by" not in line_indexes:
        op.create_index(
            "ix_pricing_agent_run_lines_manual_selected_by",
            "pricing_agent_run_lines",
            ["manual_selected_by"],
            unique=False,
        )

    run_foreign_keys = _foreign_keys("pricing_agent_runs")
    with op.batch_alter_table("pricing_agent_runs") as batch_op:
        if (
            "quote_jobs" in _tables()
            and "fk_pricing_agent_runs_confirmed_quote_job" not in run_foreign_keys
        ):
            batch_op.create_foreign_key(
                "fk_pricing_agent_runs_confirmed_quote_job",
                "quote_jobs",
                ["confirmed_quote_job_id"],
                ["job_id"],
                ondelete="SET NULL",
            )
        if (
            "quote_preview_drafts" in _tables()
            and "fk_pricing_agent_runs_confirmed_preview_draft" not in run_foreign_keys
        ):
            batch_op.create_foreign_key(
                "fk_pricing_agent_runs_confirmed_preview_draft",
                "quote_preview_drafts",
                ["confirmed_preview_draft_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "users" in _tables() and "fk_pricing_agent_runs_confirmed_by" not in run_foreign_keys:
            batch_op.create_foreign_key(
                "fk_pricing_agent_runs_confirmed_by",
                "users",
                ["confirmed_by"],
                ["id"],
                ondelete="SET NULL",
            )

    line_foreign_keys = _foreign_keys("pricing_agent_run_lines")
    with op.batch_alter_table("pricing_agent_run_lines") as batch_op:
        if (
            "users" in _tables()
            and "fk_pricing_agent_run_lines_manual_selected_by" not in line_foreign_keys
        ):
            batch_op.create_foreign_key(
                "fk_pricing_agent_run_lines_manual_selected_by",
                "users",
                ["manual_selected_by"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    if "pricing_agent_run_lines" in _tables():
        line_foreign_keys = _foreign_keys("pricing_agent_run_lines")
        with op.batch_alter_table("pricing_agent_run_lines") as batch_op:
            if "fk_pricing_agent_run_lines_manual_selected_by" in line_foreign_keys:
                batch_op.drop_constraint(
                    "fk_pricing_agent_run_lines_manual_selected_by",
                    type_="foreignkey",
                )
        line_indexes = _indexes("pricing_agent_run_lines")
        if "ix_pricing_agent_run_lines_manual_selected_by" in line_indexes:
            op.drop_index(
                "ix_pricing_agent_run_lines_manual_selected_by",
                table_name="pricing_agent_run_lines",
            )
        for name in (
            "decision_revision",
            "manual_selected_at",
            "manual_selected_by",
            "selected_candidate_json",
            "selection_origin",
        ):
            if name in _columns("pricing_agent_run_lines"):
                op.drop_column("pricing_agent_run_lines", name)

    if "pricing_agent_runs" in _tables():
        run_foreign_keys = _foreign_keys("pricing_agent_runs")
        with op.batch_alter_table("pricing_agent_runs") as batch_op:
            for name in (
                "fk_pricing_agent_runs_confirmed_by",
                "fk_pricing_agent_runs_confirmed_preview_draft",
                "fk_pricing_agent_runs_confirmed_quote_job",
            ):
                if name in run_foreign_keys:
                    batch_op.drop_constraint(name, type_="foreignkey")
        run_indexes = _indexes("pricing_agent_runs")
        for name in (
            "ix_pricing_agent_runs_confirmation_hash",
            "ix_pricing_agent_runs_confirmed_by",
            "ix_pricing_agent_runs_confirmed_preview_draft_id",
            "ix_pricing_agent_runs_confirmed_quote_job_id",
        ):
            if name in run_indexes:
                op.drop_index(name, table_name="pricing_agent_runs")
        for name in (
            "confirmation_json",
            "confirmation_hash",
            "confirmed_at",
            "confirmed_by",
            "confirmed_preview_draft_id",
            "confirmed_quote_job_id",
        ):
            if name in _columns("pricing_agent_runs"):
                op.drop_column("pricing_agent_runs", name)
