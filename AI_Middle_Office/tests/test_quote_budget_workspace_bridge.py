from __future__ import annotations

import json
from uuid import uuid4

import app.main  # noqa: F401 - register complete SQLAlchemy metadata
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.account import Account, AccountMembership
from app.models.budget_pricing_draft import BudgetProjectPricingDraft, BudgetProjectPricingDraftLine
from app.models.enterprise_quota import EnterpriseQuotaItem, EnterpriseQuotaVersion
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.quote_budget_workspace import materialize_quote_budget_workspace


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


def _seed_user_account_and_enterprise_quota(db):
    user = User(
        username=f"bridge-{uuid4().hex[:8]}",
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
        account_code=f"account-{uuid4().hex[:8]}",
        account_name="桥接测试账户",
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
    version = EnterpriseQuotaVersion(
        version_code=f"bridge-{uuid4().hex[:8]}",
        version_name="桥接测试企业定额",
        status="active",
        is_active=True,
        summary_json="{}",
        created_by=user.id,
        activated_by=user.id,
    )
    db.add(version)
    db.flush()
    enterprise_item = EnterpriseQuotaItem(
        version_id=version.id,
        quota_code="Q-001",
        item_name="墙面乳胶漆",
        work_content="两遍腻子两遍乳胶漆",
        unit="m²",
        unit_price=100,
        source_sheet="企业定额",
        source_row_index=2,
        sort_order=1,
        raw_row_json="{}",
    )
    db.add(enterprise_item)
    db.flush()
    return user, enterprise_item


def test_materialize_chat_quote_creates_one_linked_project_and_reuses_same_draft(db):
    user, enterprise_item = _seed_user_account_and_enterprise_quota(db)
    result = {
        "project_details": [
            {
                "requirement_row_key": "chat:1",
                "item_name": "墙面乳胶漆",
                "spec": "两遍腻子两遍乳胶漆",
                "quantity": 2,
                "unit": "m²",
                "unit_price": 100,
                "total_price": 200,
                "pricing_tier": "enterprise_quota",
                "manual_price_action": "untouched",
                "notes": "报价来源：企业定额；市场行情，含人工及机械；基层含水率达标后施工。",
                "quote_explanation": {
                    "ai_basis": "市场行情，含人工及机械",
                },
                "cost_reference": {
                    "matched": True,
                    "enterprise_quota_item_id": enterprise_item.id,
                    "source_cost_item": {"id": enterprise_item.id, "unit_price": 100},
                },
            },
            {
                "requirement_row_key": "chat:2",
                "item_name": "定制造型",
                "spec": "现场复核尺寸",
                "quantity": 3,
                "unit": "项",
                "unit_price": 33,
                "total_price": 99,
                "pricing_tier": "ai_estimate",
                "manual_price_action": "untouched",
                "notes": "深化确认后下单，避免返工。",
                "ai_estimate": {"unit_price": 33, "basis": "AI测试估价"},
            },
        ],
        "total_price": 299,
    }
    job = QuoteJob(
        job_id=str(uuid4()),
        username=user.username,
        status="succeeded",
        stage="completed",
        message="墙面乳胶漆2m²；定制造型3项",
        request_summary="桥接报价测试",
        result_json=json.dumps(result, ensure_ascii=False),
        result_item_count=2,
        result_total_amount=299,
        trace_id=uuid4().hex,
    )
    db.add(job)
    db.commit()

    previous_projects = settings.feature_budget_projects
    previous_drafts = settings.feature_budget_pricing_drafts
    object.__setattr__(settings, "feature_budget_projects", True)
    object.__setattr__(settings, "feature_budget_pricing_drafts", True)
    try:
        first = materialize_quote_budget_workspace(
            db,
            job=job,
            current_user=user,
            file_content=None,
        )
        db.commit()
        db.refresh(job)

        assert first["synced"] is True
        assert first["budget_project_id"] == job.budget_project_id
        assert first["budget_pricing_draft_id"] == job.budget_pricing_draft_id
        assert first["quote_amount"] == "299.000000"
        assert json.loads(job.result_json)["budget_project_id"] == job.budget_project_id

        draft = db.query(BudgetProjectPricingDraft).filter(
            BudgetProjectPricingDraft.id == job.budget_pricing_draft_id
        ).one()
        lines = (
            db.query(BudgetProjectPricingDraftLine)
            .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
            .order_by(BudgetProjectPricingDraftLine.source_sort_order)
            .all()
        )
        assert [line.price_source for line in lines] == ["enterprise_quota", "ai_estimate"]
        assert [float(line.line_total) for line in lines] == [200.0, 99.0]
        assert float(draft.priced_subtotal) == 299.0
        assert json.loads(lines[0].pricing_breakdown_json)["remark"] == "基层含水率达标后施工。"

        revision = draft.revision
        second = materialize_quote_budget_workspace(
            db,
            job=job,
            current_user=user,
            file_content=None,
        )
        assert second["synced"] is False
        assert second["budget_project_id"] == first["budget_project_id"]
        assert second["budget_pricing_draft_id"] == first["budget_pricing_draft_id"]
        assert second["row_count"] == 2
        assert second["quote_amount"] == "299.000000"
        db.refresh(draft)
        assert draft.revision == revision
    finally:
        object.__setattr__(settings, "feature_budget_projects", previous_projects)
        object.__setattr__(settings, "feature_budget_pricing_drafts", previous_drafts)
