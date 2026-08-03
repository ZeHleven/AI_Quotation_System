from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models.cost_item import CostItem, CostItemHistory
from app.models.enterprise_quota import (
    QUOTA_VERSION_STATUS_ACTIVE,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.models.user import User, UserRole, UserRoleEvent
from app.services.cost_items import list_quote_cost_candidates, serialize_quote_cost_candidate
from app.services.enterprise_quota_cost_reference import (
    ENTERPRISE_QUOTA_PRICE_SOURCE,
    ENTERPRISE_QUOTA_REFERENCE_SOURCE,
)
from app.services.enterprise_quota_units import SQUARE_METER_UNIT
from app.services.quote_cost_context import build_quote_cost_context, cost_context_references_as_source_rows
from app.services.quote_cost_matching import enrich_quote_payload_with_cost_refs


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    tables = [
        User.__table__,
        UserRole.__table__,
        UserRoleEvent.__table__,
        CostItem.__table__,
        CostItemHistory.__table__,
        CostImportBatch.__table__,
        EnterpriseQuotaVersion.__table__,
        EnterpriseQuotaSection.__table__,
        EnterpriseQuotaItem.__table__,
        EnterpriseCostResource.__table__,
        EnterpriseQuotaComponent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)


def test_quote_enrichment_uses_active_enterprise_quota_when_legacy_cost_items_empty(db_session):
    item = _seed_active_enterprise_quota_item(
        db_session,
        item_name=f"BIZ4C gypsum ceiling {uuid.uuid4().hex[:8]}",
        work_content="standard install",
        unit="m2",
        unit_price=88.0,
    )
    old_flag = _set_flag("feature_cost_db", True)
    try:
        enriched = enrich_quote_payload_with_cost_refs(
            db_session,
            {
                "project_details": [
                    {
                        "project_name": item.item_name,
                        "spec": item.work_content,
                        "quantity": 3,
                        "unit": "m2",
                        "unit_price": 90,
                        "total_price": 270,
                    }
                ]
            },
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert db_session.query(CostItem).count() == 0
    assert reference["matched"] is True
    assert reference["reference_source"] == ENTERPRISE_QUOTA_REFERENCE_SOURCE
    assert reference["source_type"] == "enterprise_quota_item"
    assert reference["enterprise_quota_item_id"] == item.id
    assert reference["quota_code"] == item.quota_code
    assert reference["reference_price"] == 88.0
    assert reference["unit"] == SQUARE_METER_UNIT
    assert reference["reference_price_source"] == ENTERPRISE_QUOTA_PRICE_SOURCE
    assert reference["reference_price_source_label"] == "企业定额综合单价"
    assert reference["evidence_api_url"] == f"/api/v1/admin/cost-master/quota-items/{item.id}"
    assert reference["evidence_url"] == f"/admin/cost-db?enterprise_quota_item_id={item.id}"
    assert reference["source_cost_item"]["enterprise_quota_item_id"] == item.id
    assert enriched["cost_reference_summary"]["reference_sources"] == [ENTERPRISE_QUOTA_REFERENCE_SOURCE]


def test_quote_cost_context_locks_enterprise_quota_reference_source(db_session):
    item = _seed_active_enterprise_quota_item(
        db_session,
        item_name=f"BIZ4C floor leveling {uuid.uuid4().hex[:8]}",
        work_content="30mm",
        unit="m2",
        unit_price=31.5,
    )
    old_flag = _set_flag("feature_cost_db", True)
    try:
        context = build_quote_cost_context(
            db_session,
            "ignored because source rows are authoritative",
            source_rows=[
                {
                    "project_name": item.item_name,
                    "spec": item.work_content,
                    "quantity": 2,
                    "unit": "m2",
                }
            ],
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert context.matched_count == 1
    assert context.active_cost_item_count == 1
    assert "reference_source: enterprise_quota.active" in context.text
    source_rows = cost_context_references_as_source_rows(context)
    assert source_rows[0]["locked_cost_item_id"] == item.id
    assert source_rows[0]["locked_cost_reference_source"] == ENTERPRISE_QUOTA_REFERENCE_SOURCE
    assert source_rows[0]["locked_enterprise_quota_item_id"] == item.id
    assert source_rows[0]["locked_quota_code"] == item.quota_code


def test_quote_candidate_search_prefers_active_enterprise_quota(db_session):
    item = _seed_active_enterprise_quota_item(
        db_session,
        item_name=f"BIZ4C manual switch candidate {uuid.uuid4().hex[:8]}",
        work_content="manual switch spec",
        unit="m",
        unit_price=12.25,
    )
    user = User(username="phase4c_admin", role="admin", quota=1)

    rows, total = list_quote_cost_candidates(
        db_session,
        user,
        keyword="manual switch candidate",
        page=1,
        page_size=20,
    )

    assert total == 1
    assert rows[0].id == item.id
    assert rows[0].reference_source == ENTERPRISE_QUOTA_REFERENCE_SOURCE
    serialized = serialize_quote_cost_candidate(rows[0], include_full_cost=True)
    assert serialized["id"] == item.id
    assert serialized["reference_source"] == ENTERPRISE_QUOTA_REFERENCE_SOURCE
    assert serialized["enterprise_quota_item_id"] == item.id
    assert serialized["quota_code"] == item.quota_code
    assert serialized["price"] == 12.25
    assert serialized["reference_price_source"] == ENTERPRISE_QUOTA_PRICE_SOURCE


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _seed_active_enterprise_quota_item(
    db,
    *,
    item_name: str,
    work_content: str,
    unit: str,
    unit_price: float,
) -> EnterpriseQuotaItem:
    version = EnterpriseQuotaVersion(
        version_code=f"phase4c-{uuid.uuid4().hex[:8]}",
        version_name="Phase 4C active enterprise quota",
        status=QUOTA_VERSION_STATUS_ACTIVE,
        is_active=True,
        activated_at=datetime.now(timezone.utc),
    )
    section = EnterpriseQuotaSection(
        section_code="A",
        section_name="Phase 4C section",
        sort_order=1,
    )
    item = EnterpriseQuotaItem(
        section=section,
        quota_code=f"QS-{uuid.uuid4().hex[:6]}",
        item_name=item_name,
        work_content=work_content,
        unit=unit,
        quantity=1,
        unit_price=unit_price,
        labor_fee=round(unit_price * 0.6, 2),
        main_material_fee=round(unit_price * 0.25, 2),
        auxiliary_material_fee=round(unit_price * 0.15, 2),
        sort_order=1,
    )
    version.sections.append(section)
    version.items.append(item)
    db.add(version)
    db.commit()
    db.refresh(item)
    return item
