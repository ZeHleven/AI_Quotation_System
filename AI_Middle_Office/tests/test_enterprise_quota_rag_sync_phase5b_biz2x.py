import asyncio
from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.cost_item import CostItem, CostRagSyncRun
from app.models.enterprise_quota import (
    QUOTA_VERSION_STATUS_ACTIVE,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.models.user import User
from app.services import cost_rag_sync
from app.services.cost_rag_sync import (
    ENTERPRISE_QUOTA_SYNC_SOURCE,
    active_enterprise_quota_rag_payload,
    cost_rag_sync_status_summary,
    preview_active_cost_items_rag_sync,
    sync_active_cost_items_to_rag,
)
from app.services.enterprise_quota_units import CUBIC_METER_UNIT, SQUARE_METER_UNIT


def test_enterprise_quota_rag_payload_includes_items_components_and_resources(db_session):
    seed = _seed_enterprise_quota_bundle(db_session)

    payload = active_enterprise_quota_rag_payload(db_session)

    item_rows = [row for row in payload if row["id"] == f"enterprise_quota_item_{seed['item'].id}"]
    resource_rows = [row for row in payload if row["id"] == f"enterprise_quota_resource_{seed['resource'].id}"]
    assert len(item_rows) == 1
    assert len(resource_rows) == 1
    item_row = item_rows[0]
    assert item_row["item_name"].startswith(seed["item"].quota_code)
    assert item_row["unit_price"] == seed["item"].unit_price
    assert item_row["unit"] == SQUARE_METER_UNIT
    assert "source: enterprise_quota.active" in item_row["notes"]
    assert f"version_code: {seed['version'].version_code}" in item_row["notes"]
    assert seed["component"].resource_name in item_row["notes"]
    resource_row = resource_rows[0]
    assert resource_row["unit_price"] == seed["resource"].computed_price
    assert resource_row["unit"] == CUBIC_METER_UNIT
    assert "source_type: enterprise_cost_resource" in resource_row["notes"]


def test_enterprise_quota_rag_dry_run_reports_active_version(db_session):
    seed = _seed_enterprise_quota_bundle(db_session)

    result = preview_active_cost_items_rag_sync(db_session, sample_limit=10)

    assert result["dry_run"] is True
    assert result["source"] == ENTERPRISE_QUOTA_SYNC_SOURCE
    assert result["requested_count"] == 2
    assert result["source_detail"]["active_version"]["version_code"] == seed["version"].version_code
    assert result["source_detail"]["quota_item_count"] == 1
    assert result["source_detail"]["resource_count"] == 1
    assert len(result["sample_materials"]) == 2
    assert db_session.query(CostRagSyncRun).count() == 0


def test_enterprise_quota_rag_sync_records_source_and_posts_reload_payload(db_session, monkeypatch):
    seed = _seed_enterprise_quota_bundle(db_session)
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"message": "enterprise quota synced"}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr(cost_rag_sync.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(sync_active_cost_items_to_rag(db_session, "phase5b-admin", user_id=None))
    run = db_session.query(CostRagSyncRun).filter(CostRagSyncRun.id == result["run"]["id"]).one()

    assert result["success"] is True
    assert result["source"] == ENTERPRISE_QUOTA_SYNC_SOURCE
    assert result["synced_count"] == 2
    assert run.source == ENTERPRISE_QUOTA_SYNC_SOURCE
    assert run.requested_count == 2
    assert run.synced_count == 2
    assert calls[0]["url"].endswith("/admin/reload")
    sent_ids = {row["id"] for row in calls[0]["json"]["materials"]}
    assert sent_ids == {f"enterprise_quota_item_{seed['item'].id}", f"enterprise_quota_resource_{seed['resource'].id}"}
    assert calls[0]["json"]["secret"] is not None


def test_enterprise_quota_rag_status_summary_uses_enterprise_source(db_session):
    _seed_enterprise_quota_bundle(db_session)
    now = datetime.now(timezone.utc)
    db_session.add(
        CostRagSyncRun(
            source=ENTERPRISE_QUOTA_SYNC_SOURCE,
            status="success",
            requested_count=2,
            synced_count=2,
            message="synced",
            rag_service_url="http://127.0.0.1:8001",
            http_status=200,
            started_at=now,
            finished_at=now,
        )
    )
    db_session.commit()

    summary = cost_rag_sync_status_summary(db_session)

    assert summary["source"] == ENTERPRISE_QUOTA_SYNC_SOURCE
    assert summary["active_count"] == 2
    assert summary["status"] == "synced"
    assert summary["needs_sync"] is False
    assert summary["source_detail"]["quota_item_count"] == 1
    assert summary["source_detail"]["resource_count"] == 1


def _seed_enterprise_quota_bundle(db) -> dict:
    suffix = uuid.uuid4().hex[:8]
    version = EnterpriseQuotaVersion(
        version_code=f"phase5b-{suffix}",
        version_name="Phase 5B active enterprise quota",
        status=QUOTA_VERSION_STATUS_ACTIVE,
        is_active=True,
        activated_at=datetime.now(timezone.utc),
    )
    section = EnterpriseQuotaSection(
        section_code="QS201",
        section_name="Phase 5B section",
        sort_order=1,
    )
    item = EnterpriseQuotaItem(
        section=section,
        quota_code=f"QS5B{suffix[:4]}",
        item_name=f"Phase 5B stone floor {suffix}",
        work_content="1. base clean 2. stone floor install",
        worker_or_subtype="mason",
        unit="m2",
        quantity=1,
        unit_price=71.13,
        labor_fee=40.0,
        main_material_fee=25.0,
        auxiliary_material_fee=6.13,
        sort_order=1,
    )
    resource = EnterpriseCostResource(
        version=version,
        resource_code=f"RS5B{suffix[:4]}",
        resource_name=f"Phase 5B stone material {suffix}",
        resource_type="main_material",
        unit="m3",
        price=22.0,
        computed_price=24.2,
        tax_rate=0.09,
        sort_order=1,
    )
    component = EnterpriseQuotaComponent(
        version=version,
        quota_item=item,
        resource=resource,
        parent_quota_code=item.quota_code,
        component_type="main_material",
        resource_code=resource.resource_code,
        resource_name=resource.resource_name,
        unit="m3",
        quantity=1,
        unit_price=24.2,
        amount=24.2,
        fee_bucket="main_material",
        sort_order=1,
    )
    version.sections.append(section)
    version.items.append(item)
    version.resources.append(resource)
    version.components.append(component)
    db.add(version)
    db.commit()
    db.refresh(version)
    db.refresh(item)
    db.refresh(resource)
    db.refresh(component)
    return {"version": version, "item": item, "resource": resource, "component": component}


def db_tables():
    return [
        User.__table__,
        CostItem.__table__,
        CostRagSyncRun.__table__,
        CostImportBatch.__table__,
        EnterpriseQuotaVersion.__table__,
        EnterpriseQuotaSection.__table__,
        EnterpriseQuotaItem.__table__,
        EnterpriseCostResource.__table__,
        EnterpriseQuotaComponent.__table__,
    ]


def _create_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    tables = db_tables()
    Base.metadata.create_all(bind=engine, tables=tables)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, Session, tables


@pytest.fixture()
def db_session():
    engine, Session, tables = _create_session()
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
