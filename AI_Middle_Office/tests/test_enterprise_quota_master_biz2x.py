from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.cost_item import CostRagSyncRun
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
from app.services.enterprise_quota_cost_reference import ENTERPRISE_QUOTA_REFERENCE_SOURCE
from app.services.enterprise_quota_master import (
    enterprise_quota_master_summary,
    get_enterprise_quota_master_item_detail,
    list_enterprise_quota_master_components,
    list_enterprise_quota_master_items,
    list_enterprise_quota_master_resources,
)
from app.services.enterprise_quota_units import CUBIC_METER_UNIT, SQUARE_METER_UNIT


def test_enterprise_quota_master_summary_reports_active_version_and_latest_rag_sync(db_session):
    seed = _seed_enterprise_quota_master(db_session)
    now = datetime.now(timezone.utc)
    db_session.add(
        CostRagSyncRun(
            source=ENTERPRISE_QUOTA_REFERENCE_SOURCE,
            status="success",
            requested_count=2,
            synced_count=2,
            message="synced enterprise quota",
            rag_service_url="http://127.0.0.1:8001",
            http_status=200,
            started_at=now,
            finished_at=now,
        )
    )
    db_session.commit()

    summary = enterprise_quota_master_summary(db_session)

    assert summary["source"] == ENTERPRISE_QUOTA_REFERENCE_SOURCE
    assert summary["active_version"]["version_code"] == seed["version"].version_code
    assert summary["section_count"] == 1
    assert summary["quota_item_count"] == 1
    assert summary["component_count"] == 1
    assert summary["resource_count"] == 1
    assert summary["latest_successful_rag_sync"]["synced_count"] == 2


def test_enterprise_quota_master_lists_items_components_and_resources(db_session):
    seed = _seed_enterprise_quota_master(db_session)

    items, item_total = list_enterprise_quota_master_items(db_session, keyword="墙面", page=1, page_size=10)
    components, component_total = list_enterprise_quota_master_components(
        db_session,
        keyword="瓷砖",
        fee_bucket="main_material",
        page=1,
        page_size=10,
    )
    resources, resource_total = list_enterprise_quota_master_resources(
        db_session,
        keyword="瓷砖",
        resource_type="main_material",
        page=1,
        page_size=10,
    )

    assert item_total == 1
    assert items[0]["id"] == seed["item"].id
    assert items[0]["section_name"] == seed["section"].section_name
    assert items[0]["unit"] == SQUARE_METER_UNIT
    assert items[0]["component_count"] == 1
    assert component_total == 1
    assert components[0]["quota_item_name"] == seed["item"].item_name
    assert components[0]["unit"] == CUBIC_METER_UNIT
    assert components[0]["fee_bucket"] == "main_material"
    assert resource_total == 1
    assert resources[0]["resource_name"] == seed["resource"].resource_name
    assert resources[0]["unit"] == CUBIC_METER_UNIT


def test_enterprise_quota_master_item_detail_includes_components(db_session):
    seed = _seed_enterprise_quota_master(db_session)

    detail = get_enterprise_quota_master_item_detail(db_session, seed["item"].id)

    assert detail is not None
    assert detail["id"] == seed["item"].id
    assert detail["source"] == ENTERPRISE_QUOTA_REFERENCE_SOURCE
    assert detail["active_version"]["id"] == seed["version"].id
    assert detail["section_name"] == seed["section"].section_name
    assert detail["unit"] == SQUARE_METER_UNIT
    assert detail["component_count"] == 1
    assert detail["components"][0]["id"] == seed["component"].id
    assert detail["components"][0]["unit"] == CUBIC_METER_UNIT


def test_enterprise_quota_master_returns_empty_without_active_version(db_session):
    summary = enterprise_quota_master_summary(db_session)
    items, item_total = list_enterprise_quota_master_items(db_session, page=1, page_size=10)
    components, component_total = list_enterprise_quota_master_components(db_session, page=1, page_size=10)
    resources, resource_total = list_enterprise_quota_master_resources(db_session, page=1, page_size=10)

    assert summary["active_version"] is None
    assert summary["quota_item_count"] == 0
    assert items == []
    assert item_total == 0
    assert components == []
    assert component_total == 0
    assert resources == []
    assert resource_total == 0


def _seed_enterprise_quota_master(db) -> dict:
    suffix = uuid.uuid4().hex[:8]
    version = EnterpriseQuotaVersion(
        version_code=f"master-{suffix}",
        version_name="企业定额主库测试版本",
        status=QUOTA_VERSION_STATUS_ACTIVE,
        is_active=True,
        activated_at=datetime.now(timezone.utc),
    )
    section = EnterpriseQuotaSection(
        section_code="QS-A",
        section_name="墙面工程",
        sort_order=1,
    )
    item = EnterpriseQuotaItem(
        section=section,
        quota_code=f"QS{suffix[:4]}",
        item_name="墙面瓷砖铺贴",
        work_content="基层清理、瓷砖铺贴、勾缝",
        worker_or_subtype="泥工",
        unit="m2",
        quantity=1,
        unit_price=128.5,
        labor_fee=68.0,
        main_material_fee=52.0,
        auxiliary_material_fee=8.5,
        machinery_fee=0,
        sort_order=1,
    )
    resource = EnterpriseCostResource(
        version=version,
        resource_code=f"R{suffix[:4]}",
        resource_name="瓷砖主材",
        resource_type="main_material",
        unit="m3",
        price=48.0,
        computed_price=52.32,
        tax_rate=0.09,
        price_block_label="材料价格库",
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
        unit_price=52.32,
        amount=52.32,
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
    db.refresh(section)
    db.refresh(item)
    db.refresh(resource)
    db.refresh(component)
    return {
        "version": version,
        "section": section,
        "item": item,
        "resource": resource,
        "component": component,
    }


def _db_tables():
    return [
        User.__table__,
        CostImportBatch.__table__,
        CostRagSyncRun.__table__,
        EnterpriseQuotaVersion.__table__,
        EnterpriseQuotaSection.__table__,
        EnterpriseQuotaItem.__table__,
        EnterpriseCostResource.__table__,
        EnterpriseQuotaComponent.__table__,
    ]


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    tables = _db_tables()
    Base.metadata.create_all(bind=engine, tables=tables)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
