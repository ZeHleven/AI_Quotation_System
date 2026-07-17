from __future__ import annotations

from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, Numeric, Table, create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 - register the complete model metadata
from app.api.v1 import account_quotas as account_quotas_api
from app.core.config import settings
from app.core.database import Base, get_db
from app.dependencies import get_current_user
from app.models.account import Account, AccountMembership
from app.models.account_quota import (
    ACCOUNT_QUOTA_EVENT_CREATED,
    ACCOUNT_QUOTA_EVENT_STATUS_CHANGED,
    ACCOUNT_QUOTA_EVENT_UPDATED,
    AccountQuotaItem,
    AccountQuotaItemHistory,
)
from app.models.budget_pricing import BudgetProjectPricingRun
from app.models.enterprise_quota import EnterpriseQuotaItem, EnterpriseQuotaVersion
from app.models.user import User
from app.services import account_quotas as account_quotas_service
from app.services import model_gateway


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260716_0053_add_account_quota_catalog.py"
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_user_account(db, *, suffix: str, role: str = "admin"):
    user = User(
        username=f"account-quota-{suffix}",
        hashed_password="x",
        role=role,
        role_version=1,
        quota=10,
        is_active=True,
    )
    db.add(user)
    db.flush()
    account = Account(
        account_uuid=str(uuid4()),
        account_code=f"account-quota-{suffix}",
        account_name=f"账户定额测试 {suffix}",
        status="active",
        created_by=user.id,
    )
    db.add(account)
    db.flush()
    db.add(
        AccountMembership(
            account_id=account.id,
            user_id=user.id,
            member_role="owner" if role == "admin" else "member",
            status="active",
            is_default=True,
            created_by=user.id,
        )
    )
    db.commit()
    return user, account


def _seed_active_enterprise_quota(db, *, user_id: int):
    version = EnterpriseQuotaVersion(
        version_code=f"account-quota-guard-{uuid4().hex[:10]}",
        version_name="企业定额 active（账户定额防污染基线）",
        status="active",
        is_active=True,
        source_file_sha256="e" * 64,
        created_by=user_id,
    )
    db.add(version)
    db.flush()
    db.add(
        EnterpriseQuotaItem(
            version_id=version.id,
            quota_code="ENT-001",
            item_name="企业正式石材地面",
            unit="㎡",
            unit_price=88.5,
            sort_order=1,
        )
    )
    db.commit()
    return version


@contextmanager
def _api_client(db, current_user: User):
    state = SimpleNamespace(user=current_user)
    test_app = FastAPI()
    test_app.include_router(account_quotas_api.router, prefix="/api/v1")

    def override_user():
        return state.user

    def override_db():
        yield db

    test_app.dependency_overrides[get_current_user] = override_user
    test_app.dependency_overrides[get_db] = override_db
    with TestClient(test_app) as client:
        yield client, state


@contextmanager
def _account_quota_feature(enabled: bool):
    previous = settings.feature_account_quotas
    object.__setattr__(settings, "feature_account_quotas", enabled)
    try:
        yield
    finally:
        object.__setattr__(settings, "feature_account_quotas", previous)


def _payload(**overrides):
    data = {
        "quota_code": "USR-001",
        "item_name": "石材地面铺装",
        "item_features": "20mm 厚花岗岩，水泥砂浆结合层",
        "spec": "600×600×20mm",
        "unit": "㎡",
        "unit_price": "12.345678",
        "source": "manual",
        "notes": "账户人工核定",
    }
    data.update(overrides)
    return data


def _response_data(response):
    assert response.status_code in {200, 201}, response.text
    return response.json()["data"]


def _error_code(response):
    detail = response.json().get("detail")
    if isinstance(detail, dict):
        return detail.get("code") or detail.get("detail")
    return detail


def _block_model_calls(monkeypatch):
    def forbidden_model_call(*_args, **_kwargs):
        pytest.fail("P2-2B-1 账户定额 CRUD 不得调用任何 LLM/视觉模型")

    for name in dir(model_gateway):
        if name.startswith("call_") or name in {"record_model_call", "record_model_call_async"}:
            candidate = getattr(model_gateway, name)
            if callable(candidate):
                monkeypatch.setattr(model_gateway, name, forbidden_model_call)


def test_feature_gate_non_admin_positive_decimal_and_account_id_injection(db):
    admin, _ = _seed_user_account(db, suffix="guard-admin", role="admin")
    staff, _ = _seed_user_account(db, suffix="guard-staff", role="user")

    with _api_client(db, admin) as (client, state):
        with _account_quota_feature(False):
            disabled = client.get("/api/v1/admin/account-quotas")
            assert disabled.status_code == 403
            assert _error_code(disabled) == "FEATURE_DISABLED"

        with _account_quota_feature(True):
            state.user = staff
            denied_read = client.get("/api/v1/admin/account-quotas")
            denied_write = client.post(
                "/api/v1/admin/account-quotas",
                json=_payload(quota_code="STAFF-DENIED"),
            )
            assert denied_read.status_code == 403
            assert denied_write.status_code == 403

            state.user = admin
            injected = client.post(
                "/api/v1/admin/account-quotas",
                json=_payload(account_id=999999),
            )
            assert injected.status_code == 422, injected.text
            assert any(
                issue.get("loc", [])[-1:] == ["account_id"]
                and issue.get("type") == "extra_forbidden"
                for issue in injected.json()["detail"]
            )

            for invalid_price in ("0", "-0.000001"):
                invalid = client.post(
                    "/api/v1/admin/account-quotas",
                    json=_payload(
                        quota_code=f"INVALID-{invalid_price}",
                        item_name=f"无效单价 {invalid_price}",
                        unit_price=invalid_price,
                    ),
                )
                assert invalid.status_code == 422, invalid.text

            forged_create_source = client.post(
                "/api/v1/admin/account-quotas",
                json=_payload(
                    quota_code="FORGED-AI-SOURCE",
                    item_name="伪造 AI 来源",
                    source="ai_estimate",
                ),
            )
            assert forged_create_source.status_code == 422, forged_create_source.text
            assert any(
                issue.get("loc", [])[-1:] == ["source"]
                for issue in forged_create_source.json()["detail"]
            )

            manual_item = _response_data(
                client.post(
                    "/api/v1/admin/account-quotas",
                    json=_payload(quota_code="MANUAL-SOURCE-GUARD"),
                )
            )
            forged_update_source = client.patch(
                f"/api/v1/admin/account-quotas/{manual_item['item_uuid']}",
                json={
                    "expected_revision": 1,
                    "source": "ai_estimate",
                    "reason": "普通 CRUD 不得伪造来源",
                },
            )
            assert forged_update_source.status_code == 422, forged_update_source.text
            assert any(
                issue.get("loc", [])[-1:] == ["source"]
                and issue.get("type") == "extra_forbidden"
                for issue in forged_update_source.json()["detail"]
            )


def test_account_quota_crud_lifecycle_history_and_non_pollution(db, monkeypatch):
    admin, _ = _seed_user_account(db, suffix="lifecycle", role="admin")
    _seed_active_enterprise_quota(db, user_id=admin.id)
    _block_model_calls(monkeypatch)
    baseline = {
        "enterprise_versions": db.query(EnterpriseQuotaVersion).count(),
        "enterprise_active_versions": db.query(EnterpriseQuotaVersion)
        .filter(
            EnterpriseQuotaVersion.status == "active",
            EnterpriseQuotaVersion.is_active.is_(True),
        )
        .count(),
        "enterprise_items": db.query(EnterpriseQuotaItem).count(),
        "formal_pricing_runs": db.query(BudgetProjectPricingRun).count(),
    }

    with _account_quota_feature(True), _api_client(db, admin) as (client, _state):
        created = _response_data(
            client.post("/api/v1/admin/account-quotas", json=_payload())
        )
        item_id = created["id"]
        item_uuid = created["item_uuid"]
        assert created["status"] == "draft"
        assert created["revision"] == 1
        assert created["unit_price"] == "12.345678"
        assert created["fingerprint"]
        assert "account_id" not in created

        by_uuid = _response_data(
            client.get(f"/api/v1/admin/account-quotas/{item_uuid}")
        )
        by_id = _response_data(
            client.get(f"/api/v1/admin/account-quotas/{item_id}")
        )
        assert by_uuid["id"] == item_id
        assert by_id["item_uuid"] == item_uuid

        listed = _response_data(client.get("/api/v1/admin/account-quotas"))
        assert any(row["item_uuid"] == item_uuid for row in listed)

        updated = _response_data(
            client.patch(
                f"/api/v1/admin/account-quotas/{item_uuid}",
                json={
                    "expected_revision": 1,
                    "quota_code": "USR-001-A",
                    "item_features": "20mm 厚花岗岩，干硬性砂浆结合层",
                    "unit_price": "123.000001",
                    "reason": "人工复核市场价",
                },
            )
        )
        assert updated["revision"] == 2
        assert updated["unit_price"] == "123.000001"
        assert updated["fingerprint"] != created["fingerprint"]

        stale = client.patch(
            f"/api/v1/admin/account-quotas/{item_uuid}",
            json={
                "expected_revision": 1,
                "unit_price": "130.000000",
                "reason": "过期页面提交",
            },
        )
        assert stale.status_code == 409, stale.text
        assert _error_code(stale) == "ACCOUNT_QUOTA_REVISION_CONFLICT"

        duplicate = client.post(
            "/api/v1/admin/account-quotas",
            json=_payload(
                quota_code="USR-DUPLICATE",
                item_features=updated["item_features"],
                unit_price="999.000000",
            ),
        )
        assert duplicate.status_code == 409, duplicate.text
        assert _error_code(duplicate) == "ACCOUNT_QUOTA_DUPLICATE_FINGERPRINT"

        active = _response_data(
            client.post(
                f"/api/v1/admin/account-quotas/{item_uuid}/status",
                json={
                    "target_status": "active",
                    "expected_revision": 2,
                    "reason": "审核启用",
                },
            )
        )
        assert active["status"] == "active"
        assert active["revision"] == 3

        draft_again = _response_data(
            client.post(
                f"/api/v1/admin/account-quotas/{item_uuid}/status",
                json={
                    "target_status": "draft",
                    "expected_revision": 3,
                    "reason": "撤回修订",
                },
            )
        )
        assert draft_again["status"] == "draft"
        assert draft_again["revision"] == 4

        archived = _response_data(
            client.post(
                f"/api/v1/admin/account-quotas/{item_uuid}/status",
                json={
                    "target_status": "archived",
                    "expected_revision": 4,
                    "reason": "不再使用",
                },
            )
        )
        assert archived["status"] == "archived"
        assert archived["revision"] == 5

        frozen_update = client.patch(
            f"/api/v1/admin/account-quotas/{item_uuid}",
            json={
                "expected_revision": 5,
                "unit_price": "200.000000",
                "reason": "归档后不应允许编辑",
            },
        )
        frozen_status = client.post(
            f"/api/v1/admin/account-quotas/{item_uuid}/status",
            json={
                "target_status": "draft",
                "expected_revision": 5,
                "reason": "归档后不应允许恢复",
            },
        )
        for response in (frozen_update, frozen_status):
            assert response.status_code == 409, response.text
            assert _error_code(response) == "ACCOUNT_QUOTA_ARCHIVED"

        history = _response_data(
            client.get(f"/api/v1/admin/account-quotas/{item_uuid}/history")
        )
        assert [row["revision"] for row in history] == [5, 4, 3, 2, 1]
        assert {row["event_type"] for row in history} == {
            ACCOUNT_QUOTA_EVENT_CREATED,
            ACCOUNT_QUOTA_EVENT_UPDATED,
            ACCOUNT_QUOTA_EVENT_STATUS_CHANGED,
        }
        assert all(row["actor_id"] == admin.id for row in history)
        assert all(row["actor_username"] == admin.username for row in history)
        assert history[0]["to_status"] == "archived"
        assert history[-1]["after_snapshot"]["unit_price"] == "12.345678"

    assert db.query(EnterpriseQuotaVersion).count() == baseline["enterprise_versions"]
    assert (
        db.query(EnterpriseQuotaVersion)
        .filter(
            EnterpriseQuotaVersion.status == "active",
            EnterpriseQuotaVersion.is_active.is_(True),
        )
        .count()
        == baseline["enterprise_active_versions"]
    )
    assert db.query(EnterpriseQuotaItem).count() == baseline["enterprise_items"]
    assert db.query(BudgetProjectPricingRun).count() == baseline["formal_pricing_runs"]


def test_account_quota_cross_account_is_404_and_fingerprint_is_tenant_scoped(db):
    admin_a, _ = _seed_user_account(db, suffix="tenant-a", role="admin")
    admin_b, _ = _seed_user_account(db, suffix="tenant-b", role="admin")

    with _account_quota_feature(True), _api_client(db, admin_a) as (client, state):
        item_a = _response_data(
            client.post("/api/v1/admin/account-quotas", json=_payload())
        )

        state.user = admin_b
        foreign_responses = (
            client.get(f"/api/v1/admin/account-quotas/{item_a['item_uuid']}"),
            client.get(f"/api/v1/admin/account-quotas/{item_a['item_uuid']}/history"),
            client.patch(
                f"/api/v1/admin/account-quotas/{item_a['item_uuid']}",
                json={
                    "expected_revision": 1,
                    "unit_price": "88.000000",
                    "reason": "越权编辑",
                },
            ),
            client.post(
                f"/api/v1/admin/account-quotas/{item_a['item_uuid']}/status",
                json={
                    "target_status": "active",
                    "expected_revision": 1,
                    "reason": "越权启用",
                },
            ),
        )
        for response in foreign_responses:
            assert response.status_code == 404, response.text
            assert _error_code(response) == "ACCOUNT_QUOTA_NOT_FOUND"

        list_b = _response_data(client.get("/api/v1/admin/account-quotas"))
        assert all(row["item_uuid"] != item_a["item_uuid"] for row in list_b)

        # 相同指纹在不同账户中合法，且价格可以独立维护。
        item_b = _response_data(
            client.post(
                "/api/v1/admin/account-quotas",
                json=_payload(quota_code="USR-B-001", unit_price="66.000000"),
            )
        )
        assert item_b["fingerprint"] == item_a["fingerprint"]
        assert item_b["unit_price"] == "66.000000"

        state.user = admin_a
        list_a = _response_data(client.get("/api/v1/admin/account-quotas"))
        ids_a = {row["item_uuid"] for row in list_a}
        assert item_a["item_uuid"] in ids_a
        assert item_b["item_uuid"] not in ids_a


def test_account_quota_modules_have_no_llm_or_external_pricing_dependencies():
    for module in (account_quotas_api, account_quotas_service):
        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for forbidden in (
            "app.services.model_gateway",
            "call_openai",
            "call_dashscope",
            "call_glm",
            "n8n",
            "dify",
        ):
            assert forbidden not in source


def test_account_quota_model_metadata_and_alembic_env_are_registered():
    assert {
        "account_quota_items",
        "account_quota_item_history",
    }.issubset(Base.metadata.tables)

    price_type = AccountQuotaItem.__table__.c.unit_price.type
    assert isinstance(price_type, Numeric)
    assert (price_type.precision, price_type.scale) == (18, 6)
    item_constraint_names = {
        constraint.name
        for constraint in AccountQuotaItem.__table__.constraints
        if constraint.name
    }
    assert "uq_account_quota_items_uuid" in item_constraint_names
    assert "uq_account_quota_items_account_fingerprint" in item_constraint_names
    history_constraint_names = {
        constraint.name
        for constraint in AccountQuotaItemHistory.__table__.constraints
        if constraint.name
    }
    assert "uq_account_quota_history_item_revision" in history_constraint_names

    env_source = (
        Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")
    assert "account_quota" in env_source


def test_0053_migration_up_down_numeric_and_unique_contracts():
    spec = importlib.util.spec_from_file_location("account_quota_0053", MIGRATION_PATH)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260716_0053"
    assert migration.down_revision == "20260716_0052"

    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    Table("accounts", metadata, Column("id", Integer, primary_key=True))
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        metadata.create_all(connection)
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            migration.upgrade()
            database = inspect(connection)
            assert {
                "account_quota_items",
                "account_quota_item_history",
            }.issubset(database.get_table_names())

            unit_price = next(
                column
                for column in database.get_columns("account_quota_items")
                if column["name"] == "unit_price"
            )
            assert isinstance(unit_price["type"], Numeric)
            assert (unit_price["type"].precision, unit_price["type"].scale) == (18, 6)

            item_uniques = {
                constraint["name"]
                for constraint in database.get_unique_constraints("account_quota_items")
            }
            assert {
                "uq_account_quota_items_uuid",
                "uq_account_quota_items_account_fingerprint",
            }.issubset(item_uniques)
            history_uniques = {
                constraint["name"]
                for constraint in database.get_unique_constraints("account_quota_item_history")
            }
            assert "uq_account_quota_history_item_revision" in history_uniques

            migration.downgrade()
            assert not {
                "account_quota_items",
                "account_quota_item_history",
            }.intersection(inspect(connection).get_table_names())
        finally:
            migration.op = original_op
    engine.dispose()
