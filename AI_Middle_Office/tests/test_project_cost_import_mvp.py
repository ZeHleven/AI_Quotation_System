import uuid
from io import BytesIO

from openpyxl import Workbook

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.enterprise_quota import (
    IMPORT_BATCH_STATUS_ACTIVATED,
    QUOTA_VERSION_STATUS_ACTIVE,
    QUOTA_VERSION_STATUS_ARCHIVED,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.models.user import User, UserRole
from app.services.project_cost_import import parse_project_purchase_files


PASSWORD = "secret123"


def _set_flag(name: str, value):
    old = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old


def _headers(client) -> dict:
    db = SessionLocal()
    try:
        user = User(
            username=f"project_cost_import_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(PASSWORD),
            role="admin",
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role="admin", created_by=None, note="project cost import test"))
        db.commit()
        username = user.username
    finally:
        db.close()
    response = client.post("/api/v1/auth/login", data={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _purchase_workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "订购单"
    sheet.append(["供应商：东莞高泰建材有限公司"])
    sheet.append(["订单日期", "2026-07-08", "采购单号", "PO-20260708-01"])
    sheet.append(["价格含13%专票并含运费"])
    sheet.append(["序号", "材料名称", "品牌", "规格型号", "单位", "数量", "单价", "金额", "备注"])
    sheet.append([1, "水泥", "华润", "P.O 42.5", "吨", 1, 450, 450, "首批"])
    sheet.append([2, "水泥", "华润", "P.O 42.5", "吨", 2, 450, 900, "补单"])
    sheet.append([3, "运费", None, None, "项", 1, 100, 100, None])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _active_quota_fixture() -> tuple[int, int, int, list[int]]:
    db = SessionLocal()
    try:
        for row in db.query(EnterpriseQuotaVersion).filter(EnterpriseQuotaVersion.version_code.like("active-project-import-%")).all():
            row.is_active = False
            row.status = QUOTA_VERSION_STATUS_ARCHIVED
        db.flush()
        previous_active_ids = [row.id for row in db.query(EnterpriseQuotaVersion).filter(EnterpriseQuotaVersion.is_active.is_(True)).all()]
        for row in db.query(EnterpriseQuotaVersion).filter(EnterpriseQuotaVersion.is_active.is_(True)).all():
            row.is_active = False
            row.status = QUOTA_VERSION_STATUS_ARCHIVED
        token = uuid.uuid4().hex[:8]
        import_batch = CostImportBatch(
            batch_uuid=str(uuid.uuid4()),
            source_filename=f"active-{token}.xlsx",
            source_file_sha256=(token * 8)[:64],
            parser_version="test",
            status=IMPORT_BATCH_STATUS_ACTIVATED,
            error_count=0,
            warning_count=0,
        )
        db.add(import_batch)
        db.flush()
        version = EnterpriseQuotaVersion(
            version_code=f"active-project-import-{token}",
            version_name="项目采购入库测试 active",
            import_batch_id=import_batch.id,
            status=QUOTA_VERSION_STATUS_ACTIVE,
            is_active=True,
            summary_json='{"error_count": 0, "warning_count": 0}',
        )
        db.add(version)
        db.flush()
        section = EnterpriseQuotaSection(version_id=version.id, section_code=f"S-{token}", section_name="测试分部", sort_order=1)
        db.add(section)
        db.flush()
        item = EnterpriseQuotaItem(
            version_id=version.id,
            section_id=section.id,
            quota_code=f"Q-{token}",
            item_name="水泥砂浆找平层",
            unit="m2",
            unit_price=800,
            main_material_fee=800,
            sort_order=1,
        )
        resource = EnterpriseCostResource(
            version_id=version.id,
            resource_code=f"R-{token}",
            resource_name="水泥",
            resource_type="main_material",
            unit="吨",
            price=400,
            computed_price=400,
            sort_order=1,
        )
        db.add_all([item, resource])
        db.flush()
        component = EnterpriseQuotaComponent(
            version_id=version.id,
            quota_item_id=item.id,
            resource_id=resource.id,
            parent_quota_code=item.quota_code,
            component_type="主材",
            resource_code=resource.resource_code,
            resource_name=resource.resource_name,
            unit="吨",
            quantity=2,
            unit_price=400,
            amount=800,
            fee_bucket="main_material",
            sort_order=1,
        )
        db.add(component)
        db.commit()
        return version.id, item.id, resource.id, previous_active_ids
    finally:
        db.close()


def _restore_active_quota_fixture(version_id: int, previous_active_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        version = db.get(EnterpriseQuotaVersion, version_id)
        if version:
            version.is_active = False
            version.status = QUOTA_VERSION_STATUS_ARCHIVED
        if previous_active_ids:
            for row in db.query(EnterpriseQuotaVersion).filter(EnterpriseQuotaVersion.id.in_(previous_active_ids)).all():
                row.is_active = True
                row.status = QUOTA_VERSION_STATUS_ACTIVE
        db.commit()
    finally:
        db.close()


def test_purchase_parser_keeps_evidence_and_excludes_service_fee():
    result = parse_project_purchase_files([("订购单.xlsx", _purchase_workbook_bytes())])

    assert result["summary"]["parsed_file_count"] == 1
    assert result["summary"]["observation_count"] == 3
    cement = [row for row in result["observations"] if row["raw_item_name"] == "水泥"]
    assert len(cement) == 2
    assert cement[0]["supplier_name"] == "东莞高泰建材有限公司"
    assert cement[0]["tax_included"] is True
    assert cement[0]["tax_rate"] == 0.13
    assert cement[0]["freight_included"] is True
    assert cement[0]["candidate_key"]
    freight = next(row for row in result["observations"] if row["raw_item_name"] == "运费")
    assert freight["excluded_reason"] == "service_fee"
    assert freight["candidate_key"] is None


def test_project_purchase_import_review_and_draft_version(client):
    active_version_id, active_item_id, active_resource_id, previous_active_ids = _active_quota_fixture()
    headers = _headers(client)
    old_cost_flag = _set_flag("feature_cost_db", True)
    old_import_flag = _set_flag("feature_project_cost_import", True)
    try:
        imported = client.post(
            "/api/v1/admin/project-cost-imports",
            headers=headers,
            data={"project_name": "东莞香港中心项目", "source_name": "采购资料测试"},
            files=[("files", ("订购单.xlsx", _purchase_workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        assert imported.status_code == 200, imported.text
        batch = imported.json()["data"]
        assert batch["status"] == "reviewing"
        assert batch["observation_count"] == 3
        assert batch["candidate_count"] == 1

        candidates_response = client.get(
            f"/api/v1/admin/project-cost-imports/{batch['id']}/candidates",
            headers=headers,
        )
        assert candidates_response.status_code == 200, candidates_response.text
        candidate = candidates_response.json()["data"][0]
        assert candidate["matched_resource_id"] == active_resource_id
        assert candidate["recommended_price"] == 450

        reviewed = client.post(
            f"/api/v1/admin/project-cost-imports/{batch['id']}/review",
            headers=headers,
            json={"candidate_ids": [candidate["id"]], "action": "approve", "note": "采购合同与发票口径已核对"},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["data"]["batch"]["approved_count"] == 1

        drafted = client.post(
            f"/api/v1/admin/project-cost-imports/{batch['id']}/draft-version",
            headers=headers,
            json={"version_name": "东莞香港中心采购价草稿"},
        )
        assert drafted.status_code == 200, drafted.text
        draft = drafted.json()["data"]["draft_version"]
        assert draft["status"] == "draft"
        assert draft["is_active"] is False

        db = SessionLocal()
        try:
            active_version = db.get(EnterpriseQuotaVersion, active_version_id)
            active_item = db.get(EnterpriseQuotaItem, active_item_id)
            active_resource = db.get(EnterpriseCostResource, active_resource_id)
            assert active_version.is_active is True
            assert active_resource.price == 400
            assert active_item.unit_price == 800

            draft_resource = db.query(EnterpriseCostResource).filter(
                EnterpriseCostResource.version_id == draft["id"],
                EnterpriseCostResource.resource_code == active_resource.resource_code,
            ).one()
            assert draft_resource.price == 450
            draft_component = db.query(EnterpriseQuotaComponent).filter(
                EnterpriseQuotaComponent.version_id == draft["id"],
                EnterpriseQuotaComponent.resource_id == draft_resource.id,
            ).one()
            assert draft_component.unit_price == 450
            assert draft_component.amount == 900
            draft_item = db.get(EnterpriseQuotaItem, draft_component.quota_item_id)
            assert draft_item.main_material_fee == 900
            assert draft_item.unit_price == 900
        finally:
            db.close()
    finally:
        _set_flag("feature_cost_db", old_cost_flag)
        _set_flag("feature_project_cost_import", old_import_flag)
        _restore_active_quota_fixture(active_version_id, previous_active_ids)
