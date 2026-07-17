from __future__ import annotations

import asyncio
import json
import importlib.util
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy import Column, Integer, MetaData, Table, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

import app.main  # noqa: F401 - register complete metadata before create_all
from app.core.config import settings
from app.core.database import Base
from app.core.database import get_db
from app.dependencies import get_current_user
from app.api.v1 import budget_pricing as budget_pricing_api
from app.api.v1 import users as users_api
from app.models.account import Account, AccountBudgetProject, AccountMembership
from app.models.budget_project import (
    BudgetProjectImportBatch,
    BudgetProjectImportRevision,
    BudgetProjectProfile,
)
from app.models.budget_pricing import BudgetProjectPricingRun
from app.models.budget_pricing_draft import (
    PRICING_MODE_ACCOUNT_STRICT,
    PRICING_MODE_ENTERPRISE_AI,
    BudgetProjectPricingDraftLine,
    BudgetProjectPricingDraftQuoteJob,
    BudgetProjectPricingDraftQuoteJobLine,
)
from app.schemas.budget_pricing import BudgetPricingDraftQuoteJobCreate
from app.models.enterprise_quota import EnterpriseQuotaItem, EnterpriseQuotaVersion
from app.models.project_progress import Project
from app.models.user import User
from app.services.account_tenancy import AccountTenancyError, resolve_current_account
from app.services.budget_pricing import BudgetPricingError
from app.services.budget_pricing_drafts import (
    create_or_rebuild_budget_pricing_draft,
    patch_budget_pricing_draft_line,
)
from app.services.budget_pricing_ai_estimates import estimate_budget_pricing_draft_line
from app.services import budget_pricing_draft_quote_jobs as quote_job_service
from app.services.budget_pricing_draft_quote_jobs import (
    create_budget_pricing_draft_quote_job,
    run_budget_pricing_draft_quote_job,
)
from app.services.budget_projects import accessible_budget_profile_query, get_budget_profile


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260716_0052_add_account_pricing_drafts.py"
)
MIGRATION_0058_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260717_0058_add_budget_pricing_draft_quote_jobs.py"
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
        yield session, engine
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _formal_rows() -> list[dict]:
    rows = []
    for order, (name, unit, quantity) in enumerate(
        (("石材地面", "㎡", "2"), ("账户专属陌生项目", "项", "3")),
        start=1,
    ):
        rows.append(
            {
                "row_key": f"bill:{order}",
                "source_sheet": "清单",
                "sheet_role": "bill",
                "raw_row_index": order + 1,
                "sort_order": order,
                "mapping_revision": 0,
                "row_type": "item",
                "is_standard_item": True,
                "quantity_status": "valid",
                "standard_row": {
                    "item_name": name,
                    "spec": None,
                    "unit": unit,
                    "calculation_quantity": quantity,
                    "quantity_status": "valid",
                    "warnings": [],
                },
            }
        )
    return rows


def _seed_account_project(db, *, suffix: str):
    user = User(
        username=f"admin-{suffix}",
        hashed_password="x",
        role="admin",
        role_version=1,
        quota=10,
        is_active=True,
    )
    db.add(user)
    db.flush()
    account = Account(
        account_uuid=str(uuid4()),
        account_code=f"account-{suffix}",
        account_name=f"账号 {suffix}",
        status="active",
        created_by=user.id,
    )
    db.add(account)
    db.flush()
    db.add(
        AccountMembership(
            account_id=account.id,
            user_id=user.id,
            member_role="owner",
            status="active",
            is_default=True,
            created_by=user.id,
        )
    )
    project = Project(
        project_code=f"P-{suffix}",
        name=f"项目 {suffix}",
        status="planning",
        risk_level="normal",
        progress_percent=0,
        project_manager_id=user.id,
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    profile = BudgetProjectProfile(
        project_id=project.id,
        workspace_status="active",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(profile)
    db.add(
        AccountBudgetProject(
            account_id=account.id,
            project_id=project.id,
            created_by=user.id,
        )
    )
    db.flush()

    rows = _formal_rows()
    batch = BudgetProjectImportBatch(
        batch_uuid=str(uuid4()),
        project_id=project.id,
        source_filename=f"{suffix}.xlsx",
        source_file_sha256=(suffix[0] * 64),
        source_file_size=1,
        source_storage_mode="metadata_only",
        parser_version="test",
        status="active",
        remap_revision=0,
        sheet_count=1,
        total_output_row_count=2,
        standard_item_count=2,
        valid_quantity_count=2,
        invalid_quantity_count=0,
        original_preview_json="{}",
        current_preview_json="{}",
        issues_json="[]",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(batch)
    db.flush()
    revision = BudgetProjectImportRevision(
        revision_uuid=str(uuid4()),
        batch_id=batch.id,
        revision_number=1,
        revision_kind="initial",
        snapshot_sha256=(suffix[-1] * 64),
        preview_json="{}",
        sheet_mappings_json="[]",
        standard_rows_json=json.dumps(rows, ensure_ascii=False),
        summary_json=json.dumps({"standard_item_count": 2}),
        created_by=user.id,
    )
    db.add(revision)
    db.flush()
    batch.current_revision_id = revision.id
    batch.confirmed_revision_id = revision.id
    profile.active_import_batch_id = batch.id
    profile.active_import_revision_id = revision.id
    db.flush()
    return user, account, project, profile, batch, revision


def _seed_enterprise_quota(db, user: User):
    version = EnterpriseQuotaVersion(
        version_code="quota-active-v1",
        version_name="企业定额 active",
        status="active",
        is_active=True,
        source_file_sha256="q" * 64,
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    item = EnterpriseQuotaItem(
        version_id=version.id,
        quota_code="Q-001",
        item_name="石材地面",
        unit="㎡",
        unit_price=10,
        labor_fee=2,
        main_material_fee=6,
        auxiliary_material_fee=1,
        machinery_fee=1,
        sort_order=1,
    )
    db.add(item)
    db.flush()
    return version, item


def test_account_first_access_and_missing_membership_fail_closed(db):
    session, _ = db
    user_a, account_a, project_a, _, _, _ = _seed_account_project(session, suffix="a1")
    _, account_b, project_b, _, _, _ = _seed_account_project(session, suffix="b2")
    outsider = User(
        username="no-account",
        hashed_password="x",
        role="admin",
        role_version=1,
        is_active=True,
    )
    session.add(outsider)
    session.commit()

    old = settings.feature_budget_pricing_drafts
    object.__setattr__(settings, "feature_budget_pricing_drafts", True)
    try:
        visible = accessible_budget_profile_query(session, user_a).all()
        assert [row.project_id for row in visible] == [project_a.id]
        assert all(row.project_id != project_b.id for row in visible)
        assert resolve_current_account(session, user_a).id == account_a.id
        assert account_a.id != account_b.id
        with pytest.raises(HTTPException) as cross_account:
            get_budget_profile(session, project_b.id, user_a)
        assert cross_account.value.status_code == 404
        with pytest.raises(HTTPException) as missing:
            accessible_budget_profile_query(session, outsider).all()
        assert missing.value.status_code == 403
        assert missing.value.detail["code"] == "ACCOUNT_MEMBERSHIP_REQUIRED"
        with pytest.raises(AccountTenancyError):
            resolve_current_account(session, outsider)
    finally:
        object.__setattr__(settings, "feature_budget_pricing_drafts", old)


def test_admin_user_creation_commits_user_roles_and_membership_atomically(db, monkeypatch):
    session, engine = db
    operator, account, _, _, _, _ = _seed_account_project(session, suffix="atomic-ok")
    session.commit()
    monkeypatch.setattr(users_api, "get_password_hash", lambda value: f"hashed:{value}")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/users",
            "headers": [],
            "query_string": b"",
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    old = settings.feature_budget_pricing_drafts
    object.__setattr__(settings, "feature_budget_pricing_drafts", True)
    try:
        result = asyncio.run(
            users_api.create_user_by_admin(
                users_api.AdminUserCreate(
                    username="atomic-created",
                    password="secret1",
                    quota=5,
                    roles=["staff", "quote_user"],
                    note="原子创建",
                ),
                request,
                session,
                operator,
            )
        )
        assert result["data"]["username"] == "atomic-created"

        audit = sessionmaker(bind=engine)()
        try:
            created = audit.query(User).filter(User.username == "atomic-created").one()
            assert {item.role for item in created.role_assignments} == {"staff", "quote_user"}
            assert resolve_current_account(audit, created).id == account.id
        finally:
            audit.close()

        no_account_operator = User(
            username="operator-without-account",
            hashed_password="x",
            role="admin",
            role_version=1,
            is_active=True,
        )
        session.add(no_account_operator)
        session.commit()
        with pytest.raises(HTTPException) as failed:
            asyncio.run(
                users_api.create_user_by_admin(
                    users_api.AdminUserCreate(
                        username="must-rollback",
                        password="secret2",
                        quota=5,
                        roles=["staff"],
                        note="应回滚",
                    ),
                    request,
                    session,
                    no_account_operator,
                )
            )
        assert failed.value.status_code == 403
        audit = sessionmaker(bind=engine)()
        try:
            assert audit.query(User).filter(User.username == "must-rollback").first() is None
        finally:
            audit.close()
    finally:
        object.__setattr__(settings, "feature_budget_pricing_drafts", old)


def test_enterprise_ai_matches_and_keeps_unmatched_price_null(db):
    session, engine = db
    user, _, _, profile, batch, revision = _seed_account_project(session, suffix="c3")
    quota, _ = _seed_enterprise_quota(session, user)
    session.commit()
    run_count_before = session.query(BudgetProjectPricingRun).count()

    enterprise_draft = create_or_rebuild_budget_pricing_draft(
        session,
        profile,
        user,
        pricing_mode=PRICING_MODE_ENTERPRISE_AI,
        source_import_batch_id=batch.id,
        source_import_revision_id=revision.id,
        expected_active_quota_version_id=quota.id,
    )
    session.flush()
    enterprise_id = enterprise_draft.id
    enterprise_revision = enterprise_draft.revision
    enterprise_lines = (
        session.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == enterprise_id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order)
        .all()
    )
    assert len(enterprise_lines) == 2
    assert enterprise_lines[0].effective_unit_price == Decimal("10.000000")
    assert enterprise_lines[0].line_total == Decimal("20.000000")
    assert enterprise_lines[1].base_unit_price is None
    assert enterprise_lines[1].effective_unit_price is None
    assert enterprise_draft.summary_json.find('"llm_auto_estimation_connected":false') >= 0
    assert session.query(BudgetProjectPricingRun).count() == run_count_before


def test_pricing_draft_http_api_is_account_scoped_and_rejects_account_id(db):
    session, _ = db
    user_a, _, project_a, _, batch_a, revision_a = _seed_account_project(
        session,
        suffix="http-a",
    )
    _, _, project_b, _, _, _ = _seed_account_project(session, suffix="http-b")
    session.commit()

    test_app = FastAPI()
    test_app.include_router(budget_pricing_api.router, prefix="/api/v1")

    def override_user():
        return user_a

    def override_db():
        yield session

    test_app.dependency_overrides[get_current_user] = override_user
    test_app.dependency_overrides[get_db] = override_db
    previous = (
        settings.feature_budget_projects,
        settings.feature_budget_pricing,
        settings.feature_budget_pricing_drafts,
    )
    object.__setattr__(settings, "feature_budget_projects", True)
    object.__setattr__(settings, "feature_budget_pricing", True)
    object.__setattr__(settings, "feature_budget_pricing_drafts", True)
    try:
        with TestClient(test_app) as client:
            current = client.get(
                f"/api/v1/admin/budget-projects/{project_b.id}/pricing-draft/current"
            )
            lines = client.get(
                f"/api/v1/admin/budget-projects/{project_b.id}/pricing-draft/lines"
            )
            patch = client.patch(
                f"/api/v1/admin/budget-projects/{project_b.id}/pricing-draft/lines/1",
                json={
                    "expected_revision": 1,
                    "expected_line_revision": 1,
                    "manual_unit_price": "12.000000",
                },
            )
            for response in (current, lines, patch):
                assert response.status_code == 404, response.text
                assert response.json()["detail"] == "BUDGET_PROJECT_NOT_FOUND"

            injected = client.post(
                f"/api/v1/admin/budget-projects/{project_a.id}/pricing-draft",
                json={
                    "pricing_mode": "account_strict",
                    "source_import_batch_id": batch_a.id,
                    "source_import_revision_id": revision_a.id,
                    "account_id": 999999,
                },
            )
            assert injected.status_code == 422, injected.text
            assert any(
                item.get("loc", [])[-1:] == ["account_id"]
                and item.get("type") == "extra_forbidden"
                for item in injected.json()["detail"]
            )
    finally:
        (
            budget_projects_enabled,
            budget_pricing_enabled,
            pricing_drafts_enabled,
        ) = previous
        object.__setattr__(settings, "feature_budget_projects", budget_projects_enabled)
        object.__setattr__(settings, "feature_budget_pricing", budget_pricing_enabled)
        object.__setattr__(settings, "feature_budget_pricing_drafts", pricing_drafts_enabled)


def test_mode_switch_same_draft_manual_patch_clear_and_optimistic_locks(db):
    session, engine = db
    user, _, _, profile, batch, revision = _seed_account_project(
        session,
        suffix="switch-c3",
    )
    quota, _ = _seed_enterprise_quota(session, user)
    session.commit()
    run_count_before = session.query(BudgetProjectPricingRun).count()
    enterprise_draft = create_or_rebuild_budget_pricing_draft(
        session,
        profile,
        user,
        pricing_mode=PRICING_MODE_ENTERPRISE_AI,
        source_import_batch_id=batch.id,
        source_import_revision_id=revision.id,
        expected_active_quota_version_id=quota.id,
    )
    session.flush()
    enterprise_id = enterprise_draft.id
    enterprise_revision = enterprise_draft.revision

    statements: list[str] = []

    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        account_draft = create_or_rebuild_budget_pricing_draft(
            session,
            profile,
            user,
            pricing_mode=PRICING_MODE_ACCOUNT_STRICT,
            source_import_batch_id=batch.id,
            source_import_revision_id=revision.id,
            expected_revision=enterprise_revision,
        )
        session.flush()
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
    assert account_draft.id == enterprise_id
    assert account_draft.revision == enterprise_revision + 1
    assert not any("enterprise_quota_versions" in statement for statement in statements)
    assert not any("enterprise_quota_items" in statement for statement in statements)
    account_lines = (
        session.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == account_draft.id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order)
        .all()
    )
    assert len(account_lines) == 2
    assert all(line.base_unit_price is None for line in account_lines)
    assert all(line.effective_unit_price is None for line in account_lines)
    assert all(line.line_total is None for line in account_lines)
    assert session.query(BudgetProjectPricingRun).count() == run_count_before

    line = account_lines[0]
    price_draft_revision = account_draft.revision
    old_line_revision = line.line_revision
    account_draft, line = patch_budget_pricing_draft_line(
        session,
        profile,
        user,
        line_identifier=line.id,
        expected_revision=price_draft_revision,
        expected_line_revision=old_line_revision,
        manual_unit_price=Decimal("25.123456"),
        reason="人工核价",
    )
    assert account_draft.revision == price_draft_revision + 1
    assert line.line_revision == old_line_revision + 1
    assert line.effective_unit_price == Decimal("25.123456")
    assert line.line_total == Decimal("50.246912")
    assert line.price_source == "manual"

    with pytest.raises(BudgetPricingError) as stale_draft:
        patch_budget_pricing_draft_line(
            session,
            profile,
            user,
            line_identifier=line.id,
            expected_revision=price_draft_revision,
            expected_line_revision=line.line_revision,
            manual_unit_price=Decimal("30"),
        )
    assert stale_draft.value.code == "BUDGET_PRICING_DRAFT_REVISION_CONFLICT"

    with pytest.raises(BudgetPricingError) as stale_line:
        patch_budget_pricing_draft_line(
            session,
            profile,
            user,
            line_identifier=line.id,
            expected_revision=account_draft.revision,
            expected_line_revision=old_line_revision,
            manual_unit_price=Decimal("30"),
        )
    assert stale_line.value.code == "BUDGET_PRICING_DRAFT_LINE_REVISION_CONFLICT"

    account_draft, line = patch_budget_pricing_draft_line(
        session,
        profile,
        user,
        line_identifier=line.id,
        expected_revision=account_draft.revision,
        expected_line_revision=line.line_revision,
        manual_unit_price=None,
        reason="清空人工价",
    )
    assert line.manual_unit_price is None
    assert line.effective_unit_price is None
    assert line.line_total is None
    assert session.query(BudgetProjectPricingRun).count() == run_count_before


def test_p2_2a_draft_source_has_no_legacy_or_model_orchestration_dependencies():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "budget_pricing_drafts.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("cost_items", "model_gateway", "n8n", "create_budget_pricing_run("):
        assert forbidden not in source


def test_p2_2c1_manual_ai_estimate_enters_draft_and_manual_price_still_wins(db):
    session, _ = db
    old_provider = settings.budget_pricing_ai_provider
    object.__setattr__(settings, "budget_pricing_ai_provider", "rule")
    user, _, _, profile, batch, revision = _seed_account_project(session, suffix="ai-c1")
    try:
        _seed_enterprise_quota(session, user)
        session.commit()
        run_count_before = session.query(BudgetProjectPricingRun).count()

        draft = create_or_rebuild_budget_pricing_draft(
            session,
            profile,
            user,
            pricing_mode=PRICING_MODE_ACCOUNT_STRICT,
            source_import_batch_id=batch.id,
            source_import_revision_id=revision.id,
            expected_revision=None,
            reason="account mode missing price",
        )
        session.flush()
        line = (
            session.query(BudgetProjectPricingDraftLine)
            .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
            .order_by(BudgetProjectPricingDraftLine.source_sort_order)
            .first()
        )
        assert line.base_unit_price is None
        assert line.effective_unit_price is None

        draft, line = asyncio.run(
            estimate_budget_pricing_draft_line(
                session,
                profile,
                user,
                line_identifier=line.id,
                expected_revision=draft.revision,
                expected_line_revision=line.line_revision,
                reason="manual ai estimate",
            )
        )
        assert line.ai_estimated_unit_price is not None
        assert line.manual_unit_price is None
        assert line.effective_unit_price == line.ai_estimated_unit_price
        assert line.price_source == "ai_estimate"
        assert line.line_total is not None
        estimate_snapshot = json.loads(line.ai_estimate_snapshot_json)
        assert estimate_snapshot["estimate"]["provider"] == "rule"
        assert estimate_snapshot["input"]["pricing_mode"] == PRICING_MODE_ACCOUNT_STRICT
        assert json.loads(draft.summary_json)["ai_estimate_count"] == 1
        assert session.query(BudgetProjectPricingRun).count() == run_count_before

        draft, line = patch_budget_pricing_draft_line(
            session,
            profile,
            user,
            line_identifier=line.id,
            expected_revision=draft.revision,
            expected_line_revision=line.line_revision,
            manual_unit_price=Decimal("33.333333"),
            reason="manual overrides ai",
        )
        assert line.price_source == "manual"
        assert line.effective_unit_price == Decimal("33.333333")
        draft, line = patch_budget_pricing_draft_line(
            session,
            profile,
            user,
            line_identifier=line.id,
            expected_revision=draft.revision,
            expected_line_revision=line.line_revision,
            manual_unit_price=None,
            reason="clear manual restores ai",
        )
        assert line.manual_unit_price is None
        assert line.price_source == "ai_estimate"
        assert line.effective_unit_price == line.ai_estimated_unit_price
    finally:
        object.__setattr__(settings, "budget_pricing_ai_provider", old_provider)


def test_p2_2c1_ai_estimate_does_not_override_enterprise_base_price(db):
    session, _ = db
    user, _, _, profile, batch, revision = _seed_account_project(session, suffix="ai-base")
    quota, _ = _seed_enterprise_quota(session, user)
    session.commit()
    draft = create_or_rebuild_budget_pricing_draft(
        session,
        profile,
        user,
        pricing_mode=PRICING_MODE_ENTERPRISE_AI,
        source_import_batch_id=batch.id,
        source_import_revision_id=revision.id,
        expected_active_quota_version_id=quota.id,
        reason="enterprise base price",
    )
    line = (
        session.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order)
        .first()
    )
    assert line.base_unit_price == Decimal("10.000000")
    with pytest.raises(BudgetPricingError) as blocked:
        asyncio.run(
            estimate_budget_pricing_draft_line(
                session,
                profile,
                user,
                line_identifier=line.id,
                expected_revision=draft.revision,
                expected_line_revision=line.line_revision,
            )
        )
    assert blocked.value.code == "BUDGET_PRICING_AI_ESTIMATE_BASE_PRICE_EXISTS"


def test_p2_2c2_one_click_quote_job_prices_unmatched_lines_in_background(db, monkeypatch):
    session, engine = db
    user, _, _, profile, batch, revision = _seed_account_project(session, suffix="quote-job")
    quota, _ = _seed_enterprise_quota(session, user)
    session.commit()
    run_count_before = session.query(BudgetProjectPricingRun).count()
    monkeypatch.setattr(quote_job_service, "SessionLocal", sessionmaker(bind=engine, autoflush=False))

    batch_calls: list[int] = []

    async def fake_generate_batch(snapshots, *, current_user):
        batch_calls.append(len(snapshots))
        results = {}
        for snapshot in snapshots:
            assert snapshot["pricing_mode"] == PRICING_MODE_ENTERPRISE_AI
            assert snapshot["item_name"]
            results[snapshot["line_uuid"]] = {
                "unit_price": "22.500000",
                "confidence": 0.86,
                "basis": "mocked DeepSeek batch estimate for background job",
                "risks": ["manual review required"],
                "mode": "deepseek",
                "provider": "deepseek",
                "model": "deepseek-test",
                "prompt_version": "test",
                "engine_version": "test",
                "batch_mode": True,
            }
        return results

    monkeypatch.setattr(quote_job_service, "generate_budget_pricing_ai_estimate_batch", fake_generate_batch)

    job = create_budget_pricing_draft_quote_job(
        session,
        profile,
        user,
        BudgetPricingDraftQuoteJobCreate(
            pricing_mode=PRICING_MODE_ENTERPRISE_AI,
            source_import_batch_id=batch.id,
            source_import_revision_id=revision.id,
            expected_active_quota_version_id=quota.id,
            ai_concurrency=1,
            ai_batch_size=12,
            reason="one click quote job test",
        ),
    )
    assert job.total_line_count == 2
    assert job.enterprise_priced_count == 1
    assert job.ai_total_count == 1
    assert job.ai_completed_count == 0
    session.commit()

    asyncio.run(run_budget_pricing_draft_quote_job(job.id))
    session.expire_all()
    finished = session.query(BudgetProjectPricingDraftQuoteJob).filter(BudgetProjectPricingDraftQuoteJob.id == job.id).one()
    assert finished.status == "succeeded"
    assert finished.progress_percent == 100
    assert finished.enterprise_priced_count == 1
    assert finished.ai_total_count == 1
    assert finished.ai_completed_count == 1
    assert finished.ai_failed_count == 0
    assert batch_calls == [1]
    job_lines = (
        session.query(BudgetProjectPricingDraftQuoteJobLine)
        .filter(BudgetProjectPricingDraftQuoteJobLine.job_id == job.id)
        .order_by(BudgetProjectPricingDraftQuoteJobLine.source_sort_order)
        .all()
    )
    assert [line.status for line in job_lines] == ["enterprise_matched", "ai_succeeded"]

    draft_lines = (
        session.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == finished.draft_id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order)
        .all()
    )
    assert draft_lines[0].base_unit_price == Decimal("10.000000")
    assert draft_lines[0].price_source == "enterprise_quota"
    assert draft_lines[1].base_unit_price is None
    assert draft_lines[1].ai_estimated_unit_price == Decimal("22.500000")
    assert draft_lines[1].effective_unit_price == Decimal("22.500000")
    assert draft_lines[1].price_source == "ai_estimate"
    snapshot = json.loads(draft_lines[1].ai_estimate_snapshot_json)
    assert snapshot["estimate"]["provider"] == "deepseek"
    assert session.query(BudgetProjectPricingRun).count() == run_count_before


def test_0052_migration_backfills_memberships_and_budget_project_bindings():
    spec = importlib.util.spec_from_file_location("budget_pricing_0052", MIGRATION_PATH)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260716_0052"
    assert migration.down_revision == "20260716_0051"

    metadata = MetaData()
    users = Table("users", metadata, Column("id", Integer, primary_key=True))
    projects = Table("projects", metadata, Column("id", Integer, primary_key=True))
    profiles = Table(
        "budget_project_profiles",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, nullable=False),
        Column("created_by", Integer, nullable=False),
    )
    for name in (
        "budget_project_import_batches",
        "budget_project_import_revisions",
        "enterprise_quota_versions",
        "enterprise_quota_items",
    ):
        Table(name, metadata, Column("id", Integer, primary_key=True))

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(users.insert(), [{"id": 1}, {"id": 2}])
        connection.execute(projects.insert(), [{"id": 10}, {"id": 11}])
        connection.execute(
            profiles.insert(),
            [
                {"id": 100, "project_id": 10, "created_by": 1},
                {"id": 101, "project_id": 11, "created_by": 2},
            ],
        )
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            migration.upgrade()
            database = inspect(connection)
            assert {
                "accounts",
                "account_memberships",
                "account_budget_projects",
                "budget_project_pricing_drafts",
                "budget_project_pricing_draft_lines",
                "budget_project_pricing_draft_events",
            }.issubset(database.get_table_names())
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM accounts").scalar_one() == 1
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM account_memberships").scalar_one() == 2
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM account_budget_projects").scalar_one() == 2
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM account_memberships WHERE status='active' AND is_default=1"
            ).scalar_one() == 2
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM account_budget_projects WHERE project_id IN (10, 11)"
            ).scalar_one() == 2
            migration.downgrade()
            database = inspect(connection)
            assert not {
                "accounts",
                "account_memberships",
                "account_budget_projects",
                "budget_project_pricing_drafts",
                "budget_project_pricing_draft_lines",
                "budget_project_pricing_draft_events",
            }.intersection(database.get_table_names())
        finally:
            migration.op = original_op


def test_alembic_metadata_registers_account_and_pricing_draft_models():
    env_source = (
        Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")
    assert "    account," in env_source
    assert "    budget_pricing_draft," in env_source
    assert {
        "accounts",
        "account_memberships",
        "account_budget_projects",
        "budget_project_pricing_drafts",
        "budget_project_pricing_draft_lines",
        "budget_project_pricing_draft_events",
        "budget_project_pricing_draft_quote_jobs",
        "budget_project_pricing_draft_quote_job_lines",
    }.issubset(Base.metadata.tables)


def test_p2_2c1_migration_declares_ai_estimate_columns():
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260716_0057_add_budget_pricing_ai_estimate_fields.py"
    )
    text = migration.read_text(encoding="utf-8")
    assert 'revision: str = "20260716_0057"' in text
    assert 'down_revision: Union[str, None] = "20260716_0056"' in text
    assert '"ai_estimated_unit_price"' in text
    assert '"ai_estimate_snapshot_json"' in text
    assert "ai_estimated_unit_price" in BudgetProjectPricingDraftLine.__table__.c
    assert "ai_estimate_snapshot_json" in BudgetProjectPricingDraftLine.__table__.c


def test_p2_2c2_migration_declares_background_quote_job_tables():
    text = MIGRATION_0058_PATH.read_text(encoding="utf-8")
    assert 'revision: str = "20260717_0058"' in text
    assert 'down_revision: Union[str, None] = "20260716_0057"' in text
    assert "budget_project_pricing_draft_quote_jobs" in text
    assert "budget_project_pricing_draft_quote_job_lines" in text
    assert "budget_project_pricing_draft_quote_jobs" in Base.metadata.tables
    assert "budget_project_pricing_draft_quote_job_lines" in Base.metadata.tables
