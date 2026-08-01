import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import settings
from app.core.database import Base
from app.services.rbac import get_available_modules


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RETIRED_TABLES = {
    "cost_measurements",
    "cost_measurement_lines",
    "cost_measurement_events",
}
RETIRED_ROUTES = {
    "/api/v1/admin/cost-measurements/import-preview",
    "/api/v1/admin/cost-measurements/import",
    "/api/v1/admin/cost-measurements",
    "/api/v1/admin/cost-measurements/{measurement_id}",
    "/api/v1/admin/cost-measurements/{measurement_id}/recalculate",
    "/api/v1/admin/cost-measurements/{measurement_id}/lock",
    "/admin/cost-measurement",
}


def _admin_user():
    return SimpleNamespace(
        id=1,
        username="retired_cost_measurement_admin",
        role="admin",
        role_version=1,
        quota=5,
        is_active=True,
        must_change_password=False,
        dingtalk_user_id=None,
        role_assignments=[
            SimpleNamespace(role="system_admin"),
            SimpleNamespace(role="admin"),
        ],
    )


def _load_migration():
    migration_path = (
        PROJECT_ROOT
        / "alembic"
        / "versions"
        / "20260731_0080_remove_cost_measurement.py"
    )
    spec = importlib.util.spec_from_file_location("remove_cost_measurement_0080", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_cost_measurement_routes_and_spa_entry_are_not_registered(client):
    registered_paths = {
        route.path for route in client.app.routes if hasattr(route, "path")
    }

    assert RETIRED_ROUTES.isdisjoint(registered_paths)
    assert client.get("/api/v1/admin/cost-measurements").status_code == 404
    assert client.get("/admin/cost-measurement").status_code == 404


def test_cost_measurement_model_flag_and_rbac_module_are_removed():
    assert RETIRED_TABLES.isdisjoint(Base.metadata.tables)
    assert not hasattr(settings, "feature_cost_measurement")

    module_keys = {module["key"] for module in get_available_modules(_admin_user())}
    assert "cost_measurement" not in module_keys
    assert "cost_db" in module_keys
    assert "budget_pricing" in module_keys
    assert "pricing_agent" in module_keys


def test_frontend_has_no_cost_measurement_entry_or_api_calls():
    app_source = (PROJECT_ROOT.parent / "ai-web" / "src" / "App.vue").read_text(encoding="utf-8")

    for fragment in (
        "/admin/cost-measurement",
        "/admin/cost-measurements",
        "costMeasurement",
        "CostMeasurement",
        "成本测算",
    ):
        assert fragment not in app_source


def test_0080_migration_drops_only_three_tables_and_restores_empty_schema():
    migration = _load_migration()
    assert migration.revision == "20260731_0080"
    assert migration.down_revision == "20260731_0079"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        users = sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
        quota_versions = sa.Table(
            "enterprise_quota_versions",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
        )
        quota_items = sa.Table(
            "enterprise_quota_items",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
        )
        preserved = sa.Table(
            "preserved_business_data",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("label", sa.String(64), nullable=False),
        )
        metadata.create_all(connection)
        connection.execute(users.insert(), [{"id": 1}])
        connection.execute(quota_versions.insert(), [{"id": 1}])
        connection.execute(quota_items.insert(), [{"id": 1}])
        connection.execute(preserved.insert(), [{"id": 1, "label": "keep"}])

        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            migration.downgrade()
            inspector = sa.inspect(connection)
            assert RETIRED_TABLES <= set(inspector.get_table_names())

            connection.execute(
                sa.text(
                    "INSERT INTO cost_measurements "
                    "(id, measurement_uuid, measurement_code, name, created_by) "
                    "VALUES (1, 'measurement-1', 'CM-1', 'sample', 1)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO cost_measurement_lines "
                    "(id, measurement_id, line_key, item_name) "
                    "VALUES (1, 1, 'Sheet1:1', 'sample line')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO cost_measurement_events "
                    "(id, measurement_id, event_type, actor_user_id) "
                    "VALUES (1, 1, 'imported', 1)"
                )
            )

            migration.upgrade()
            inspector = sa.inspect(connection)
            assert RETIRED_TABLES.isdisjoint(inspector.get_table_names())
            assert {
                "users",
                "enterprise_quota_versions",
                "enterprise_quota_items",
                "preserved_business_data",
            } <= set(inspector.get_table_names())
            assert connection.execute(
                sa.text("SELECT label FROM preserved_business_data WHERE id = 1")
            ).scalar_one() == "keep"

            migration.downgrade()
            inspector = sa.inspect(connection)
            assert RETIRED_TABLES <= set(inspector.get_table_names())
            assert connection.execute(sa.text("SELECT COUNT(*) FROM cost_measurements")).scalar_one() == 0
            assert connection.execute(sa.text("SELECT COUNT(*) FROM cost_measurement_lines")).scalar_one() == 0
            assert connection.execute(sa.text("SELECT COUNT(*) FROM cost_measurement_events")).scalar_one() == 0
        finally:
            migration.op = original_op
