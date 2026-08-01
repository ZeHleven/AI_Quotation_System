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
    "execution_tasks",
    "execution_task_events",
    "meeting_notes",
    "meeting_note_revisions",
    "task_drafts",
}
RETIRED_ROUTES = {
    "/api/v1/execution-tasks",
    "/api/v1/execution-tasks/{task_id}",
    "/api/v1/execution-tasks/{task_id}/cancel",
    "/api/v1/meetings",
    "/api/v1/meetings/{meeting_id}",
    "/api/v1/meetings/{meeting_id}/drafts",
    "/api/v1/meetings/{meeting_id}/cancel",
    "/api/v1/meetings/{meeting_id}/confirm-tasks",
    "/api/v1/admin/dashboard/execution-speed",
    "/admin/execution",
}


def _admin_user():
    return SimpleNamespace(
        id=1,
        username="retired_execution_admin",
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


def test_execution_routes_and_spa_entry_are_not_registered(client):
    registered_paths = {
        route.path for route in client.app.routes if hasattr(route, "path")
    }

    assert RETIRED_ROUTES.isdisjoint(registered_paths)
    assert client.get("/api/v1/execution-tasks").status_code == 404
    assert client.get("/api/v1/meetings").status_code == 404
    assert client.get("/api/v1/admin/dashboard/execution-speed").status_code == 404
    assert client.get("/admin/execution").status_code == 404


def test_execution_models_flags_and_rbac_module_are_removed():
    assert RETIRED_TABLES.isdisjoint(Base.metadata.tables)
    for setting_name in (
        "feature_execution",
        "feature_dashboard_execution",
        "feature_meeting_ai",
        "feature_audio_transcription",
    ):
        assert not hasattr(settings, setting_name)

    module_keys = {module["key"] for module in get_available_modules(_admin_user())}
    assert "execution" not in module_keys


def test_frontend_has_no_execution_system_entry_or_api_calls():
    app_source = (PROJECT_ROOT.parent / "ai-web" / "src" / "App.vue").read_text(encoding="utf-8")

    for fragment in (
        "/admin/execution",
        "/execution-tasks",
        "/meetings",
        "/admin/dashboard/execution-speed",
        "执行系统",
        "执行速度",
        "会议纪要",
    ):
        assert fragment not in app_source


def test_0078_migration_drops_and_can_restore_retired_tables():
    migration_path = (
        PROJECT_ROOT
        / "alembic"
        / "versions"
        / "20260731_0078_remove_execution_system.py"
    )
    spec = importlib.util.spec_from_file_location("remove_execution_system_0078", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
        metadata.create_all(connection)

        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            migration.downgrade()
            assert RETIRED_TABLES <= set(sa.inspect(connection).get_table_names())

            migration.upgrade()
            assert RETIRED_TABLES.isdisjoint(sa.inspect(connection).get_table_names())
        finally:
            migration.op = original_op
