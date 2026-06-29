import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.enterprise_quota import (
    QUOTA_VERSION_STATUS_DRAFT,
    RESOURCE_TYPE_AUXILIARY_MATERIAL,
    RESOURCE_TYPE_LABOR,
    RESOURCE_TYPE_MAIN_MATERIAL,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.models.user import User, UserRole, UserRoleEvent
from app.services.enterprise_quota_import import (
    EnterpriseQuotaImportError,
    save_enterprise_quota_draft_from_preview,
)
from app.services.enterprise_quota_units import CUBIC_METER_UNIT, SQUARE_METER_UNIT


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            UserRole.__table__,
            UserRoleEvent.__table__,
            CostImportBatch.__table__,
            EnterpriseQuotaVersion.__table__,
            EnterpriseQuotaSection.__table__,
            EnterpriseQuotaItem.__table__,
            EnterpriseCostResource.__table__,
            EnterpriseQuotaComponent.__table__,
        ],
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_save_enterprise_quota_draft_from_preview_writes_full_master_shape(db_session):
    result = save_enterprise_quota_draft_from_preview(
        db_session,
        _preview_fixture(),
        version_code="qs-enterprise-quota-test-v1",
        version_name="QS enterprise quota test v1",
        batch_uuid="11111111-1111-1111-1111-111111111111",
    )

    assert result["ok"] is True
    assert result["status"] == QUOTA_VERSION_STATUS_DRAFT
    assert result["is_active"] is False
    assert result["section_count"] == 1
    assert result["item_count"] == 1
    assert result["component_count"] == 2
    assert result["resource_count"] == 5

    version = db_session.query(EnterpriseQuotaVersion).one()
    assert version.status == QUOTA_VERSION_STATUS_DRAFT
    assert version.is_active is False
    assert version.import_batch.batch_uuid == "11111111-1111-1111-1111-111111111111"

    section = db_session.query(EnterpriseQuotaSection).one()
    item = db_session.query(EnterpriseQuotaItem).one()
    assert item.section_id == section.id
    assert item.quota_code == "QS001"
    assert item.unit == SQUARE_METER_UNIT

    components = db_session.query(EnterpriseQuotaComponent).order_by(EnterpriseQuotaComponent.sort_order).all()
    assert [component.parent_quota_code for component in components] == ["QS001", "QS001"]
    assert all(component.quota_item_id == item.id for component in components)
    assert all(component.resource_id is not None for component in components)
    assert components[0].fee_bucket == RESOURCE_TYPE_LABOR
    assert components[1].fee_bucket == RESOURCE_TYPE_AUXILIARY_MATERIAL
    assert components[1].unit == CUBIC_METER_UNIT

    resource_types = [
        row.resource_type
        for row in db_session.query(EnterpriseCostResource).order_by(EnterpriseCostResource.sort_order).all()
    ]
    assert resource_types.count(RESOURCE_TYPE_LABOR) == 2
    assert resource_types.count(RESOURCE_TYPE_AUXILIARY_MATERIAL) == 1
    assert resource_types.count(RESOURCE_TYPE_MAIN_MATERIAL) == 2
    normalized_units = {
        row.resource_name: row.unit
        for row in db_session.query(EnterpriseCostResource).order_by(EnterpriseCostResource.sort_order).all()
    }
    assert normalized_units["Floor labor guide"] == SQUARE_METER_UNIT
    assert normalized_units["Stone slab"] == SQUARE_METER_UNIT
    assert normalized_units["Sand"] == CUBIC_METER_UNIT


def test_save_enterprise_quota_draft_rejects_duplicate_version_code(db_session):
    save_enterprise_quota_draft_from_preview(
        db_session,
        _preview_fixture(),
        version_code="qs-enterprise-quota-duplicate",
        batch_uuid="22222222-2222-2222-2222-222222222222",
    )

    with pytest.raises(EnterpriseQuotaImportError, match="Version code already exists"):
        save_enterprise_quota_draft_from_preview(
            db_session,
            _preview_fixture(),
            version_code="qs-enterprise-quota-duplicate",
            batch_uuid="33333333-3333-3333-3333-333333333333",
        )


def test_save_enterprise_quota_draft_rejects_failed_phase0_preview(db_session):
    preview = _preview_fixture()
    preview["ok"] = False
    preview["summary"]["error_count"] = 1

    with pytest.raises(EnterpriseQuotaImportError, match="Phase 0 preview is not importable"):
        save_enterprise_quota_draft_from_preview(db_session, preview, version_code="will-not-save")


def test_save_enterprise_quota_draft_bounds_dirty_source_strings(db_session):
    preview = _preview_fixture()
    dirty_name = "Dirty\x00Resource" + ("X" * 400)
    preview["labor_guide"]["candidates"][0]["item_name"] = dirty_name
    preview["labor_guide"]["candidates"][0]["raw_row"]["item_name"] = dirty_name

    save_enterprise_quota_draft_from_preview(
        db_session,
        preview,
        version_code="qs-enterprise-quota-dirty-source",
        batch_uuid="44444444-4444-4444-4444-444444444444",
    )

    labor_resources = (
        db_session.query(EnterpriseCostResource)
        .filter(EnterpriseCostResource.resource_type == RESOURCE_TYPE_LABOR)
        .order_by(EnterpriseCostResource.sort_order)
        .all()
    )
    imported_labor = labor_resources[-1]
    assert "\x00" not in imported_labor.resource_name
    assert len(imported_labor.resource_name) == 255
    assert "Dirty\\u0000Resource" in imported_labor.raw_row_json


def _preview_fixture():
    return {
        "ok": True,
        "version": "biz2x-enterprise-quota-phase0-v0",
        "source": {
            "file_name": "qs-enterprise-quota-20260626.xls",
            "file_type": "xls",
            "file_size": 1024,
            "sha256": "a" * 64,
        },
        "summary": {
            "sheet_count": 3,
            "enterprise_quota_section_count": 1,
            "enterprise_quota_item_count": 1,
            "enterprise_quota_component_count": 2,
            "labor_guide_candidate_count": 1,
            "material_resource_candidate_count": 1,
            "error_count": 0,
            "warning_count": 0,
        },
        "enterprise_quota": {
            "sections": [
                {
                    "row_index": 3,
                    "section_code": "A",
                    "section_name": "Floor works",
                    "source_sheet": "enterprise quota",
                    "raw_row": {"quota_code": "A", "row_type": "section"},
                }
            ],
            "items": [
                {
                    "row_index": 4,
                    "quota_code": "QS001",
                    "section_code": "A",
                    "section_name": "Floor works",
                    "item_name": "Stone floor",
                    "work_content": "Dry-lay stone floor",
                    "worker_or_subtype": "",
                    "unit": "m2",
                    "quantity": 1,
                    "unit_price": 71.13,
                    "labor_fee": 60,
                    "main_material_fee": 0,
                    "auxiliary_material_fee": 11.13,
                    "machinery_fee": 0,
                    "source_sheet": "enterprise quota",
                    "raw_row": {"quota_code": "QS001", "row_type": "quota"},
                }
            ],
            "components": [
                {
                    "row_index": 5,
                    "parent_quota_code": "QS001",
                    "component_type": "RG labor",
                    "resource_code": "LAB001",
                    "resource_name": "Floor worker",
                    "worker_or_subtype": "skilled",
                    "unit": "workday",
                    "quantity": 0.5,
                    "unit_price": 120,
                    "amount": 60,
                    "source_sheet": "enterprise quota",
                    "raw_row": {"quota_code": "QS001", "row_type": "RG labor"},
                },
                {
                    "row_index": 6,
                    "parent_quota_code": "QS001",
                    "component_type": "CB auxiliary",
                    "resource_code": "MAT001",
                    "resource_name": "Sand",
                    "worker_or_subtype": "",
                    "unit": "m3",
                    "quantity": 0.04,
                    "unit_price": 85,
                    "amount": 3.4,
                    "source_sheet": "enterprise quota",
                    "raw_row": {"quota_code": "QS001", "row_type": "CB auxiliary"},
                },
            ],
        },
        "labor_guide": {
            "candidates": [
                {
                    "row_index": 2,
                    "quota_code": "QS-LAB-001",
                    "item_name": "Floor labor guide",
                    "work_content": "Stone floor labor",
                    "worker_type": "skilled",
                    "unit": "m2",
                    "quantity": 1,
                    "guide_price": 62,
                    "source_sheet": "labor guide",
                    "raw_row": {"quota_code": "QS-LAB-001"},
                }
            ]
        },
        "material_price_library": {
            "candidates": [
                {
                    "row_index": 7,
                    "resource_code": "M-001",
                    "resource_name": "Stone slab",
                    "price_blocks": [
                        {"block": "tax excluded", "unit": "m2", "price": 180, "tax_rate": 0, "computed_price": 180},
                        {"block": "tax included", "unit": "m2", "price": 196.2, "tax_rate": 0.09, "computed_price": 196.2},
                    ],
                    "source_sheet": "material library",
                    "raw_row": {"resource_code": "M-001"},
                }
            ]
        },
        "issues": [],
    }
