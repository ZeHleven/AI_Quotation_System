"""add enterprise quota v2 workbook and formula workbench

Revision ID: 20260729_0074
Revises: 20260729_0073
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260729_0074"
down_revision: Union[str, None] = "20260729_0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {str(item["name"]) for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {str(item["name"]) for item in sa.inspect(op.get_bind()).get_indexes(table_name)}


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


def _add_columns(table_name: str, additions: tuple[tuple[str, sa.Column], ...]) -> None:
    if table_name not in _tables():
        return
    columns = _columns(table_name)
    for name, column in additions:
        if name not in columns:
            op.add_column(table_name, column)


def _add_index(table_name: str, index_name: str, columns: list[str]) -> None:
    if table_name in _tables() and index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=False)


def upgrade() -> None:
    _add_columns(
        "enterprise_quota_versions",
        (
            ("schema_version", sa.Column("schema_version", sa.String(length=32), nullable=True)),
            ("workbook_title", sa.Column("workbook_title", sa.String(length=255), nullable=True)),
            ("workbook_metadata_json", sa.Column("workbook_metadata_json", _long_text(), nullable=True)),
            ("quality_status", sa.Column("quality_status", sa.String(length=24), nullable=True)),
            ("quality_summary_json", sa.Column("quality_summary_json", _long_text(), nullable=True)),
            ("formula_count", sa.Column("formula_count", sa.Integer(), nullable=False, server_default="0")),
            ("revision", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")),
            ("last_recalculated_at", sa.Column("last_recalculated_at", sa.DateTime(timezone=True), nullable=True)),
        ),
    )
    _add_index("enterprise_quota_versions", "ix_enterprise_quota_versions_schema_version", ["schema_version"])
    _add_index("enterprise_quota_versions", "ix_enterprise_quota_versions_quality_status", ["quality_status"])

    _add_columns(
        "enterprise_quota_sections",
        (
            ("parent_section_id", sa.Column("parent_section_id", sa.Integer(), nullable=True)),
            ("level", sa.Column("level", sa.Integer(), nullable=False, server_default="1")),
            ("outline_level", sa.Column("outline_level", sa.Integer(), nullable=False, server_default="0")),
        ),
    )
    _add_index("enterprise_quota_sections", "ix_enterprise_quota_sections_parent_section_id", ["parent_section_id"])
    _add_index("enterprise_quota_sections", "ix_enterprise_quota_sections_level", ["level"])
    if "enterprise_quota_sections" in _tables():
        foreign_keys = _foreign_keys("enterprise_quota_sections")
        if "fk_enterprise_quota_sections_parent" not in foreign_keys:
            with op.batch_alter_table("enterprise_quota_sections") as batch_op:
                batch_op.create_foreign_key(
                    "fk_enterprise_quota_sections_parent",
                    "enterprise_quota_sections",
                    ["parent_section_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    _add_columns(
        "enterprise_quota_items",
        (
            ("row_type", sa.Column("row_type", sa.String(length=32), nullable=True)),
            ("specification", sa.Column("specification", sa.String(length=255), nullable=True)),
            ("brand", sa.Column("brand", sa.String(length=255), nullable=True)),
            ("outline_level", sa.Column("outline_level", sa.Integer(), nullable=False, server_default="0")),
            ("formulas_json", sa.Column("formulas_json", _long_text(), nullable=True)),
        ),
    )
    _add_index("enterprise_quota_items", "ix_enterprise_quota_items_row_type", ["row_type"])

    _add_columns(
        "enterprise_cost_resources",
        (
            ("library_kind", sa.Column("library_kind", sa.String(length=24), nullable=True)),
            ("category", sa.Column("category", sa.String(length=128), nullable=True)),
            ("specification", sa.Column("specification", sa.String(length=255), nullable=True)),
            ("brand", sa.Column("brand", sa.String(length=255), nullable=True)),
            ("work_content", sa.Column("work_content", _long_text(), nullable=True)),
            ("calculation_rule", sa.Column("calculation_rule", _long_text(), nullable=True)),
            ("default_quantity", sa.Column("default_quantity", sa.Float(), nullable=True)),
            ("formulas_json", sa.Column("formulas_json", _long_text(), nullable=True)),
        ),
    )
    _add_index("enterprise_cost_resources", "ix_enterprise_cost_resources_library_kind", ["library_kind"])
    _add_index("enterprise_cost_resources", "ix_enterprise_cost_resources_category", ["category"])

    _add_columns(
        "enterprise_quota_components",
        (
            ("work_content", sa.Column("work_content", _long_text(), nullable=True)),
            ("specification", sa.Column("specification", sa.String(length=255), nullable=True)),
            ("brand", sa.Column("brand", sa.String(length=255), nullable=True)),
            ("outline_level", sa.Column("outline_level", sa.Integer(), nullable=False, server_default="1")),
            ("formulas_json", sa.Column("formulas_json", _long_text(), nullable=True)),
            ("formula_library_kind", sa.Column("formula_library_kind", sa.String(length=24), nullable=True)),
            ("formula_link_status", sa.Column("formula_link_status", sa.String(length=24), nullable=True)),
        ),
    )
    _add_index(
        "enterprise_quota_components",
        "ix_enterprise_quota_components_formula_library_kind",
        ["formula_library_kind"],
    )
    _add_index(
        "enterprise_quota_components",
        "ix_enterprise_quota_components_formula_link_status",
        ["formula_link_status"],
    )

    if "enterprise_quota_sheet_rows" not in _tables():
        op.create_table(
            "enterprise_quota_sheet_rows",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column(
                "version_id",
                sa.Integer(),
                sa.ForeignKey("enterprise_quota_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sheet_name", sa.String(length=128), nullable=False),
            sa.Column("sheet_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("row_kind", sa.String(length=32), nullable=False, server_default="data"),
            sa.Column("outline_level", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("parent_row_number", sa.Integer(), nullable=True),
            sa.Column("entity_type", sa.String(length=32), nullable=True),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("values_json", _long_text(), nullable=False),
            sa.Column("formulas_json", _long_text(), nullable=True),
            sa.Column("styles_json", _long_text(), nullable=True),
            sa.Column("merge_ranges_json", _long_text(), nullable=True),
            sa.Column("row_height", sa.Float(), nullable=True),
            sa.Column("hidden", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("collapsed", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "version_id",
                "sheet_name",
                "row_number",
                name="uq_enterprise_quota_sheet_rows_version_sheet_row",
            ),
        )
        op.create_index("ix_enterprise_quota_sheet_rows_id", "enterprise_quota_sheet_rows", ["id"])
        op.create_index(
            "ix_enterprise_quota_sheet_rows_version_id",
            "enterprise_quota_sheet_rows",
            ["version_id"],
        )
        op.create_index(
            "ix_enterprise_quota_sheet_rows_sheet_name",
            "enterprise_quota_sheet_rows",
            ["sheet_name"],
        )
        op.create_index(
            "ix_enterprise_quota_sheet_rows_row_kind",
            "enterprise_quota_sheet_rows",
            ["row_kind"],
        )
        op.create_index(
            "ix_enterprise_quota_sheet_rows_entity_type",
            "enterprise_quota_sheet_rows",
            ["entity_type"],
        )
        op.create_index(
            "ix_enterprise_quota_sheet_rows_entity_id",
            "enterprise_quota_sheet_rows",
            ["entity_id"],
        )
        op.create_index(
            "ix_enterprise_quota_sheet_rows_version_sheet_order",
            "enterprise_quota_sheet_rows",
            ["version_id", "sheet_order", "row_number"],
        )
        op.create_index(
            "ix_enterprise_quota_sheet_rows_entity",
            "enterprise_quota_sheet_rows",
            ["entity_type", "entity_id"],
        )

    if "enterprise_quota_version_events" not in _tables():
        op.create_table(
            "enterprise_quota_version_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column(
                "version_id",
                sa.Integer(),
                sa.ForeignKey("enterprise_quota_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(length=48), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reason", _long_text(), nullable=True),
            sa.Column("details_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_enterprise_quota_version_events_id",
            "enterprise_quota_version_events",
            ["id"],
        )
        op.create_index(
            "ix_enterprise_quota_version_events_version_id",
            "enterprise_quota_version_events",
            ["version_id"],
        )
        op.create_index(
            "ix_enterprise_quota_version_events_event_type",
            "enterprise_quota_version_events",
            ["event_type"],
        )
        op.create_index(
            "ix_enterprise_quota_version_events_actor_id",
            "enterprise_quota_version_events",
            ["actor_id"],
        )
        op.create_index(
            "ix_enterprise_quota_version_events_version_created",
            "enterprise_quota_version_events",
            ["version_id", "created_at"],
        )
        op.create_index(
            "ix_enterprise_quota_version_events_type_created",
            "enterprise_quota_version_events",
            ["event_type", "created_at"],
        )


def downgrade() -> None:
    if "enterprise_quota_version_events" in _tables():
        op.drop_table("enterprise_quota_version_events")
    if "enterprise_quota_sheet_rows" in _tables():
        op.drop_table("enterprise_quota_sheet_rows")

    component_columns = _columns("enterprise_quota_components")
    for index_name in (
        "ix_enterprise_quota_components_formula_link_status",
        "ix_enterprise_quota_components_formula_library_kind",
    ):
        if index_name in _indexes("enterprise_quota_components"):
            op.drop_index(index_name, table_name="enterprise_quota_components")
    for name in (
        "formula_link_status",
        "formula_library_kind",
        "formulas_json",
        "outline_level",
        "brand",
        "specification",
        "work_content",
    ):
        if name in component_columns:
            op.drop_column("enterprise_quota_components", name)

    resource_columns = _columns("enterprise_cost_resources")
    for index_name in (
        "ix_enterprise_cost_resources_category",
        "ix_enterprise_cost_resources_library_kind",
    ):
        if index_name in _indexes("enterprise_cost_resources"):
            op.drop_index(index_name, table_name="enterprise_cost_resources")
    for name in (
        "formulas_json",
        "default_quantity",
        "calculation_rule",
        "work_content",
        "brand",
        "specification",
        "category",
        "library_kind",
    ):
        if name in resource_columns:
            op.drop_column("enterprise_cost_resources", name)

    item_columns = _columns("enterprise_quota_items")
    if "ix_enterprise_quota_items_row_type" in _indexes("enterprise_quota_items"):
        op.drop_index("ix_enterprise_quota_items_row_type", table_name="enterprise_quota_items")
    for name in ("formulas_json", "outline_level", "brand", "specification", "row_type"):
        if name in item_columns:
            op.drop_column("enterprise_quota_items", name)

    if "enterprise_quota_sections" in _tables():
        foreign_keys = _foreign_keys("enterprise_quota_sections")
        if "fk_enterprise_quota_sections_parent" in foreign_keys:
            with op.batch_alter_table("enterprise_quota_sections") as batch_op:
                batch_op.drop_constraint("fk_enterprise_quota_sections_parent", type_="foreignkey")
        for index_name in (
            "ix_enterprise_quota_sections_level",
            "ix_enterprise_quota_sections_parent_section_id",
        ):
            if index_name in _indexes("enterprise_quota_sections"):
                op.drop_index(index_name, table_name="enterprise_quota_sections")
        section_columns = _columns("enterprise_quota_sections")
        for name in ("outline_level", "level", "parent_section_id"):
            if name in section_columns:
                op.drop_column("enterprise_quota_sections", name)

    if "enterprise_quota_versions" in _tables():
        for index_name in (
            "ix_enterprise_quota_versions_quality_status",
            "ix_enterprise_quota_versions_schema_version",
        ):
            if index_name in _indexes("enterprise_quota_versions"):
                op.drop_index(index_name, table_name="enterprise_quota_versions")
        version_columns = _columns("enterprise_quota_versions")
        for name in (
            "last_recalculated_at",
            "revision",
            "formula_count",
            "quality_summary_json",
            "quality_status",
            "workbook_metadata_json",
            "workbook_title",
            "schema_version",
        ):
            if name in version_columns:
                op.drop_column("enterprise_quota_versions", name)
