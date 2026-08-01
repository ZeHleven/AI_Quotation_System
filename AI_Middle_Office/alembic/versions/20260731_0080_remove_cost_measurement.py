"""remove retired cost measurement closed-loop

Revision ID: 20260731_0080
Revises: 20260731_0079
Create Date: 2026-07-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260731_0080"
down_revision: Union[str, None] = "20260731_0079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _large_text():
    return mysql.LONGTEXT() if op.get_bind().dialect.name == "mysql" else sa.Text()


def _restore_tables() -> None:
    if "cost_measurements" not in _tables():
        op.create_table(
            "cost_measurements",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("measurement_uuid", sa.String(length=36), nullable=False),
            sa.Column("measurement_code", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("project_name", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
            sa.Column("source_filename", sa.String(length=255), nullable=True),
            sa.Column("source_file_sha256", sa.String(length=64), nullable=True),
            sa.Column("quota_version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id"), nullable=True),
            sa.Column("management_rate", sa.Double(), server_default="0.03", nullable=False),
            sa.Column("profit_rate", sa.Double(), server_default="0.05", nullable=False),
            sa.Column("tax_rate", sa.Double(), server_default="0.09", nullable=False),
            sa.Column("line_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("review_line_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("matched_quota_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("direct_cost", sa.Double(), server_default="0", nullable=False),
            sa.Column("management_fee", sa.Double(), server_default="0", nullable=False),
            sa.Column("profit_fee", sa.Double(), server_default="0", nullable=False),
            sa.Column("pretax_total", sa.Double(), server_default="0", nullable=False),
            sa.Column("tax_total", sa.Double(), server_default="0", nullable=False),
            sa.Column("grand_total", sa.Double(), server_default="0", nullable=False),
            sa.Column("source_summary_json", _large_text(), nullable=True),
            sa.Column("notes", _large_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("locked_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("measurement_uuid", name="uq_cost_measurements_uuid"),
            sa.UniqueConstraint("measurement_code", name="uq_cost_measurements_code"),
        )
        for name, columns, unique in (
            ("ix_cost_measurements_id", ["id"], False),
            ("ix_cost_measurements_measurement_uuid", ["measurement_uuid"], True),
            ("ix_cost_measurements_measurement_code", ["measurement_code"], True),
            ("ix_cost_measurements_name", ["name"], False),
            ("ix_cost_measurements_project_name", ["project_name"], False),
            ("ix_cost_measurements_status", ["status"], False),
            ("ix_cost_measurements_source_file_sha256", ["source_file_sha256"], False),
            ("ix_cost_measurements_quota_version_id", ["quota_version_id"], False),
            ("ix_cost_measurements_created_by", ["created_by"], False),
            ("ix_cost_measurements_updated_by", ["updated_by"], False),
            ("ix_cost_measurements_locked_by", ["locked_by"], False),
            ("ix_cost_measurements_status_updated", ["status", "updated_at"], False),
        ):
            op.create_index(name, "cost_measurements", columns, unique=unique)

    if "cost_measurement_lines" not in _tables():
        op.create_table(
            "cost_measurement_lines",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("measurement_id", sa.Integer(), sa.ForeignKey("cost_measurements.id"), nullable=False),
            sa.Column("line_key", sa.String(length=128), nullable=False),
            sa.Column("quota_item_id", sa.Integer(), sa.ForeignKey("enterprise_quota_items.id"), nullable=True),
            sa.Column("source_sheet", sa.String(length=128), nullable=True),
            sa.Column("source_row_index", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
            sa.Column("sequence_no", sa.String(length=64), nullable=True),
            sa.Column("section_name", sa.String(length=255), nullable=True),
            sa.Column("item_name", sa.String(length=255), nullable=False),
            sa.Column("feature", _large_text(), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Double(), server_default="0", nullable=False),
            sa.Column("line_type", sa.String(length=24), server_default="item", nullable=False),
            sa.Column("pricing_mode", sa.String(length=24), server_default="breakdown", nullable=False),
            sa.Column("price_source", sa.String(length=32), server_default="historical_excel", nullable=False),
            sa.Column("source_unit_price", sa.Double(), nullable=True),
            sa.Column("source_total_price", sa.Double(), nullable=True),
            sa.Column("labor_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("main_material_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("material_loss_rate", sa.Double(), server_default="0", nullable=False),
            sa.Column("auxiliary_machinery_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("subcontract_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("direct_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("management_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("profit_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("calculated_unit_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("calculated_total_price", sa.Double(), server_default="0", nullable=False),
            sa.Column("source_variance", sa.Double(), server_default="0", nullable=False),
            sa.Column("review_status", sa.String(length=24), server_default="pending", nullable=False),
            sa.Column("warnings_json", _large_text(), nullable=True),
            sa.Column("source_row_json", _large_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("measurement_id", "line_key", name="uq_cost_measurement_lines_measurement_key"),
        )
        for name, columns in (
            ("ix_cost_measurement_lines_id", ["id"]),
            ("ix_cost_measurement_lines_measurement_id", ["measurement_id"]),
            ("ix_cost_measurement_lines_line_key", ["line_key"]),
            ("ix_cost_measurement_lines_quota_item_id", ["quota_item_id"]),
            ("ix_cost_measurement_lines_source_sheet", ["source_sheet"]),
            ("ix_cost_measurement_lines_source_row_index", ["source_row_index"]),
            ("ix_cost_measurement_lines_section_name", ["section_name"]),
            ("ix_cost_measurement_lines_item_name", ["item_name"]),
            ("ix_cost_measurement_lines_unit", ["unit"]),
            ("ix_cost_measurement_lines_review_status", ["review_status"]),
            ("ix_cost_measurement_lines_measurement_order", ["measurement_id", "sort_order"]),
            ("ix_cost_measurement_lines_review", ["measurement_id", "review_status"]),
        ):
            op.create_index(name, "cost_measurement_lines", columns)

    if "cost_measurement_events" not in _tables():
        op.create_table(
            "cost_measurement_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("measurement_id", sa.Integer(), sa.ForeignKey("cost_measurements.id"), nullable=False),
            sa.Column("line_id", sa.Integer(), sa.ForeignKey("cost_measurement_lines.id"), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("message", sa.String(length=2000), nullable=True),
            sa.Column("payload_json", _large_text(), nullable=True),
            sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        for name, columns in (
            ("ix_cost_measurement_events_id", ["id"]),
            ("ix_cost_measurement_events_measurement_id", ["measurement_id"]),
            ("ix_cost_measurement_events_line_id", ["line_id"]),
            ("ix_cost_measurement_events_event_type", ["event_type"]),
            ("ix_cost_measurement_events_actor_user_id", ["actor_user_id"]),
            ("ix_cost_measurement_events_measurement_created", ["measurement_id", "created_at"]),
        ):
            op.create_index(name, "cost_measurement_events", columns)


def _drop_tables() -> None:
    for table_name in (
        "cost_measurement_events",
        "cost_measurement_lines",
        "cost_measurements",
    ):
        if table_name in _tables():
            op.drop_table(table_name)


def upgrade() -> None:
    """Drop only the three retired cost-measurement tables."""

    _drop_tables()


def downgrade() -> None:
    """Restore the empty schema; deleted measurement rows cannot be recovered."""

    _restore_tables()
