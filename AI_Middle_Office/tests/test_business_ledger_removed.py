import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import settings
from app.core.database import Base
from app.services.rbac import get_available_modules


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RETIRED_COLUMNS = {
    "direction",
    "stage",
    "next_followup_at",
    "cancelled_at",
    "cancelled_by_id",
    "cancel_reason",
}
RETIRED_ROUTES = {
    "/api/v1/business-ledger",
    "/api/v1/business-ledger/{inquiry_id}",
    "/api/v1/business-ledger/{inquiry_id}/cancel",
    "/admin/business-ledger",
}


def _admin_user():
    return SimpleNamespace(
        id=1,
        username="retired_business_ledger_admin",
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


def test_business_ledger_routes_and_spa_entry_are_not_registered(client):
    registered_paths = {
        route.path for route in client.app.routes if hasattr(route, "path")
    }

    assert RETIRED_ROUTES.isdisjoint(registered_paths)
    assert client.get("/api/v1/business-ledger").status_code == 404
    assert client.get("/admin/business-ledger").status_code == 404


def test_business_ledger_model_flag_and_rbac_module_are_removed():
    assert "client_inquiry_events" not in Base.metadata.tables
    model_columns = {
        column.name
        for column in Base.metadata.tables["client_inquiries"].columns
    }
    assert RETIRED_COLUMNS.isdisjoint(model_columns)
    assert not hasattr(settings, "feature_business_ledger")

    module_keys = {module["key"] for module in get_available_modules(_admin_user())}
    assert "business_ledger" not in module_keys


def test_frontend_has_no_business_ledger_entry_or_api_calls():
    app_source = (PROJECT_ROOT.parent / "ai-web" / "src" / "App.vue").read_text(encoding="utf-8")

    for fragment in (
        "/admin/business-ledger",
        "/business-ledger",
        "商务台账",
        "businessLedger",
    ):
        assert fragment not in app_source


def test_0079_migration_removes_only_outbound_ledger_data_and_can_restore_schema():
    migration_path = (
        PROJECT_ROOT
        / "alembic"
        / "versions"
        / "20260731_0079_remove_business_ledger.py"
    )
    spec = importlib.util.spec_from_file_location("remove_business_ledger_0079", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        users = sa.Table(
            "users",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
        )
        client_inquiries = sa.Table(
            "client_inquiries",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("inquiry_id", sa.String(36), nullable=False, unique=True),
            sa.Column("source", sa.String(64), nullable=True),
            sa.Column("client_name", sa.String(128), nullable=True),
            sa.Column("client_phone", sa.String(64), nullable=True),
            sa.Column("inquiry_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("first_response_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("time_source", sa.String(24), nullable=False, server_default="default"),
            sa.Column("responder_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("first_quote_job_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False, server_default="inbound"),
            sa.Column("stage", sa.String(32), nullable=True),
            sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "cancelled_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id", name="fk_client_inquiries_cancelled_by_id"),
                nullable=True,
            ),
            sa.Column("cancel_reason", sa.Text(), nullable=True),
        )
        sa.Index(
            "ix_client_inquiries_stage_next_followup_at",
            client_inquiries.c.stage,
            client_inquiries.c.next_followup_at,
        )
        quote_jobs = sa.Table(
            "quote_jobs",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("job_id", sa.String(36), nullable=False, unique=True),
            sa.Column(
                "client_inquiry_id",
                sa.String(36),
                sa.ForeignKey("client_inquiries.inquiry_id"),
                nullable=True,
            ),
        )
        client_inquiry_events = sa.Table(
            "client_inquiry_events",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "inquiry_id",
                sa.String(36),
                sa.ForeignKey("client_inquiries.inquiry_id"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(32), nullable=False),
        )
        metadata.create_all(connection)

        now = datetime(2026, 7, 31, 12, 0, 0)
        connection.execute(users.insert(), [{"id": 1}])
        connection.execute(
            client_inquiries.insert(),
            [
                {
                    "id": 1,
                    "inquiry_id": "inbound-1",
                    "inquiry_time": now,
                    "first_response_time": now,
                    "time_source": "manual",
                    "responder_id": 1,
                    "created_at": now,
                    "updated_at": now,
                    "direction": "inbound",
                },
                {
                    "id": 2,
                    "inquiry_id": "outbound-1",
                    "inquiry_time": now,
                    "first_response_time": None,
                    "time_source": "manual",
                    "responder_id": 1,
                    "created_at": now,
                    "updated_at": now,
                    "direction": "outbound",
                    "stage": "初步接触",
                },
            ],
        )
        connection.execute(
            quote_jobs.insert(),
            [
                {"id": 1, "job_id": "job-inbound", "client_inquiry_id": "inbound-1"},
                {"id": 2, "job_id": "job-outbound", "client_inquiry_id": "outbound-1"},
            ],
        )
        connection.execute(
            client_inquiry_events.insert(),
            [{"id": 1, "inquiry_id": "outbound-1", "event_type": "create"}],
        )

        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            migration.upgrade()

            inspector = sa.inspect(connection)
            assert "client_inquiry_events" not in inspector.get_table_names()
            inquiry_columns = {column["name"] for column in inspector.get_columns("client_inquiries")}
            assert RETIRED_COLUMNS.isdisjoint(inquiry_columns)
            assert next(
                column
                for column in inspector.get_columns("client_inquiries")
                if column["name"] == "first_response_time"
            )["nullable"] is False

            inquiry_ids = connection.execute(
                sa.text("SELECT inquiry_id FROM client_inquiries ORDER BY id")
            ).scalars().all()
            assert inquiry_ids == ["inbound-1"]
            job_links = dict(
                connection.execute(
                    sa.text("SELECT job_id, client_inquiry_id FROM quote_jobs ORDER BY id")
                ).all()
            )
            assert job_links == {
                "job-inbound": "inbound-1",
                "job-outbound": None,
            }

            migration.downgrade()
            inspector = sa.inspect(connection)
            restored_columns = {column["name"] for column in inspector.get_columns("client_inquiries")}
            assert RETIRED_COLUMNS <= restored_columns
            assert "client_inquiry_events" in inspector.get_table_names()
            assert connection.execute(
                sa.text("SELECT COUNT(*) FROM client_inquiries WHERE inquiry_id='outbound-1'")
            ).scalar_one() == 0
        finally:
            migration.op = original_op
