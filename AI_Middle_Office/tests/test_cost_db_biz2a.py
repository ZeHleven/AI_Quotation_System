import uuid
from io import BytesIO

from openpyxl import Workbook

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.cost_item import CHANGE_TYPE_PRICE, CHANGE_TYPE_STATUS, COST_STATUS_ACTIVE, COST_STATUS_ARCHIVED, COST_STATUS_DRAFT, CostItem, CostItemHistory
from app.models.user import User, UserRole


PASSWORD = "secret123"


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "staff") -> User:
    username = f"biz2a_{role}_{uuid.uuid4().hex[:10]}"
    legacy_role = "admin" if role in {"admin", "system_admin"} else role if role != "staff" else "user"
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(PASSWORD),
            role=legacy_role,
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz2a test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _headers(client, role: str = "staff") -> tuple[User, dict]:
    user = _create_user(role=role)
    return user, _login(client, user)


def _sample_payload(**overrides):
    payload = {
        "category": "第二章、楼地面工程",
        "subcategory": "楼地面",
        "item_name": f"楼地面水泥砂浆找平-{uuid.uuid4().hex[:6]}",
        "spec": "厚度:30mm内",
        "unit": "㎡",
        "price_type": "combined",
        "client_tax_excluded_price": 31,
        "client_labor_price": 15.147,
        "client_main_material_price": None,
        "client_auxiliary_material_price": 10.5989,
        "client_direct_fee": 25.7459,
        "client_management_profit": 5.149,
        "subcontract_composite_price": 20.76284,
        "subcontract_labor_price": 12.215,
        "subcontract_main_material_price": 0,
        "subcontract_auxiliary_material_price": 8.547,
        "crew_benchmark_price": 25,
        "notes": "test cost item",
    }
    payload.update(overrides)
    return payload


def _create_item(client, headers: dict, **overrides):
    response = client.post("/api/v1/admin/cost-items", headers=headers, json=_sample_payload(**overrides))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _seed_workbook(item_name: str = "楼地面水泥砂浆找平") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "2025旗胜标杆价连接"
    ws.append(["2025年广东旗胜标杆价（广东省内）"])
    ws.append([None, None, None, None, None, None, "建议投标价（批量）", None, None, None, None, None, None, "劳务发包价B（对于普通住宅批量项目）"])
    ws.append(["编号", "项  目  名  称 ", "项目特征", "计算规则", "工作内容", "计量单位", None, None, None, None, "管理费、利润\n(元)", "2025年税前综合单价（对甲）", "备     注 ", None, None, None, None, "现行发包\n班组价-标底\n（税前）", "备     注 "])
    ws.append([None, None, None, None, None, None, "人工费\n（元）", "主材费\n（元）", "辅材费\n(元)", "直接费小计\n(元)", None, None, None, "人工费\n（元）", "主材费\n（元）", "辅材费\n(元)", "综合单价\n(元)", None, None])
    ws.append([])
    ws.append(["第二章、楼地面工程 "])
    ws.append(["2-1", item_name, "厚度:30mm内", "按相应面层计算规则计算", "清理基层、备料、调制砂浆", "㎡", 15.147, None, 10.5989, 25.7459, 5.149, 31, "对甲备注", 12.215, 0, 8.547, 20.76284, 25, "劳务备注"])
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def test_feature_flag_disabled_returns_feature_disabled(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", False)
    try:
        response = client.get("/api/v1/admin/cost-items", headers=headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_admin_create_defaults_draft_and_derives_main_price(client):
    admin, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        data = _create_item(client, headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert data["status"] == COST_STATUS_DRAFT
    assert data["created_by"] == admin.id
    assert data["price"] == data["subcontract_composite_price"]
    assert data["client_tax_excluded_price"] == 31
    assert data["client_labor_price"] == 15.147
    assert data["client_auxiliary_material_price"] == 10.5989
    assert data["client_direct_fee"] == 25.7459
    assert data["client_management_profit"] == 5.149
    assert data["subcontract_labor_price"] == 12.215
    assert data["subcontract_main_material_price"] == 0
    assert data["subcontract_auxiliary_material_price"] == 8.547
    assert data["crew_benchmark_price"] == 25


def test_staff_can_read_but_not_write(client):
    _, admin_headers = _headers(client, "admin")
    _, staff_headers = _headers(client, "staff")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _create_item(client, admin_headers, item_name=f"staff-read-{uuid.uuid4().hex[:6]}")
        read_response = client.get("/api/v1/admin/cost-items", headers=staff_headers)
        write_response = client.post("/api/v1/admin/cost-items", headers=staff_headers, json=_sample_payload())
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert read_response.status_code == 200
    assert write_response.status_code == 403
    assert write_response.json()["detail"] == "PERMISSION_DENIED"


def test_list_filters_use_fuzzy_category_and_broader_keyword(client):
    _, headers = _headers(client, "admin")
    item_name = f"filter-target-{uuid.uuid4().hex[:6]}"
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, headers, item_name=item_name, notes="成本库筛选备注-成品保护")
        category_response = client.get("/api/v1/admin/cost-items?category=楼地面", headers=headers)
        keyword_category_response = client.get("/api/v1/admin/cost-items?keyword=第二章", headers=headers)
        keyword_notes_response = client.get("/api/v1/admin/cost-items?keyword=成品保护", headers=headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert category_response.status_code == 200
    assert keyword_category_response.status_code == 200
    assert keyword_notes_response.status_code == 200
    category_ids = {row["id"] for row in category_response.json()["data"]}
    keyword_category_ids = {row["id"] for row in keyword_category_response.json()["data"]}
    keyword_notes_ids = {row["id"] for row in keyword_notes_response.json()["data"]}
    assert item["id"] in category_ids
    assert item["id"] in keyword_category_ids
    assert item["id"] in keyword_notes_ids


def test_patch_price_writes_history(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, headers)
        response = client.patch(
            f"/api/v1/admin/cost-items/{item['id']}",
            headers=headers,
            json={"subcontract_composite_price": 22.5, "subcontract_labor_price": 13.5, "change_reason": "供应商报价更新"},
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["price"] == 22.5
    assert data["history"][-1]["change_type"] == CHANGE_TYPE_PRICE
    assert data["history"][-1]["old_price"] == item["price"]
    assert data["history"][-1]["new_price"] == 22.5
    assert data["history"][-1]["old_subcontract_labor_price"] == item["subcontract_labor_price"]
    assert data["history"][-1]["new_subcontract_labor_price"] == 13.5
    assert set(data["history"][-1]["changed_fields"]) == {"price", "subcontract_composite_price", "subcontract_labor_price"}
    assert data["history"][-1]["change_reason"] == "供应商报价更新"


def test_patch_same_prices_does_not_write_noop_history(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, headers)
        response = client.patch(
            f"/api/v1/admin/cost-items/{item['id']}",
            headers=headers,
            json={
                "subcontract_composite_price": item["subcontract_composite_price"],
                "subcontract_labor_price": item["subcontract_labor_price"],
                "change_reason": "无价格变化",
            },
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert response.status_code == 200
    assert response.json()["data"]["history"] == []


def test_activate_and_archive_follow_state_machine(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, headers)
        activated = client.post(f"/api/v1/admin/cost-items/{item['id']}/activate", headers=headers)
        activated_again = client.post(f"/api/v1/admin/cost-items/{item['id']}/activate", headers=headers)
        missing_reason = client.post(f"/api/v1/admin/cost-items/{item['id']}/archive", headers=headers, json={})
        archived = client.post(f"/api/v1/admin/cost-items/{item['id']}/archive", headers=headers, json={"reason": "季度复核停用"})
        reactivate = client.post(f"/api/v1/admin/cost-items/{item['id']}/activate", headers=headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert activated.status_code == 200
    assert activated.json()["data"]["status"] == COST_STATUS_ACTIVE
    assert activated.json()["data"]["history"][-1]["change_type"] == CHANGE_TYPE_STATUS
    assert activated_again.status_code == 200
    assert missing_reason.status_code == 422
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == COST_STATUS_ARCHIVED
    assert reactivate.status_code == 409
    assert reactivate.json()["detail"] == "STATE_CONFLICT"


def test_withdraw_activation_returns_active_item_to_draft_and_writes_history(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, headers)
        activated = client.post(f"/api/v1/admin/cost-items/{item['id']}/activate", headers=headers)
        missing_reason = client.post(f"/api/v1/admin/cost-items/{item['id']}/withdraw", headers=headers, json={})
        withdrawn = client.post(
            f"/api/v1/admin/cost-items/{item['id']}/withdraw",
            headers=headers,
            json={"reason": "价格误启用，退回待核定"},
        )
        withdrawn_again = client.post(
            f"/api/v1/admin/cost-items/{item['id']}/withdraw",
            headers=headers,
            json={"reason": "重复撤回幂等"},
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert activated.status_code == 200
    assert missing_reason.status_code == 422
    assert withdrawn.status_code == 200
    data = withdrawn.json()["data"]
    assert data["status"] == COST_STATUS_DRAFT
    assert data["history"][-1]["change_type"] == CHANGE_TYPE_STATUS
    assert data["history"][-1]["old_status"] == COST_STATUS_ACTIVE
    assert data["history"][-1]["new_status"] == COST_STATUS_DRAFT
    assert data["history"][-1]["change_reason"] == "价格误启用，退回待核定"
    assert withdrawn_again.status_code == 200
    assert withdrawn_again.json()["data"]["status"] == COST_STATUS_DRAFT


def test_withdraw_activation_rejects_staff_and_archived_items(client):
    _, admin_headers = _headers(client, "admin")
    _, staff_headers = _headers(client, "staff")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, admin_headers)
        active = client.post(f"/api/v1/admin/cost-items/{item['id']}/activate", headers=admin_headers)
        staff_withdraw = client.post(
            f"/api/v1/admin/cost-items/{item['id']}/withdraw",
            headers=staff_headers,
            json={"reason": "staff should not withdraw"},
        )
        archived_item = _create_item(client, admin_headers)
        archived = client.post(f"/api/v1/admin/cost-items/{archived_item['id']}/archive", headers=admin_headers, json={})
        archived_withdraw = client.post(
            f"/api/v1/admin/cost-items/{archived_item['id']}/withdraw",
            headers=admin_headers,
            json={"reason": "归档后不可撤回"},
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert active.status_code == 200
    assert staff_withdraw.status_code == 403
    assert staff_withdraw.json()["detail"] == "PERMISSION_DENIED"
    assert archived.status_code == 200
    assert archived_withdraw.status_code == 409
    assert archived_withdraw.json()["detail"] == "STATE_CONFLICT"


def test_bulk_status_activate_and_restore_draft_write_history(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        draft_one = _create_item(client, headers, item_name=f"bulk-draft-1-{uuid.uuid4().hex[:6]}")
        draft_two = _create_item(client, headers, item_name=f"bulk-draft-2-{uuid.uuid4().hex[:6]}")
        active_item = _create_item(client, headers, item_name=f"bulk-active-{uuid.uuid4().hex[:6]}")
        archived_item = _create_item(client, headers, item_name=f"bulk-archived-{uuid.uuid4().hex[:6]}")
        assert client.post(f"/api/v1/admin/cost-items/{active_item['id']}/activate", headers=headers).status_code == 200
        assert client.post(f"/api/v1/admin/cost-items/{archived_item['id']}/archive", headers=headers, json={}).status_code == 200

        activate_response = client.post(
            "/api/v1/admin/cost-items/bulk-status",
            headers=headers,
            json={
                "item_ids": [draft_one["id"], draft_two["id"], active_item["id"], archived_item["id"], 999999999, draft_one["id"]],
                "target_status": COST_STATUS_ACTIVE,
                "reason": "批量核定",
            },
        )
        missing_reason = client.post(
            "/api/v1/admin/cost-items/bulk-status",
            headers=headers,
            json={"item_ids": [active_item["id"]], "target_status": COST_STATUS_DRAFT},
        )
        restore_response = client.post(
            "/api/v1/admin/cost-items/bulk-status",
            headers=headers,
            json={
                "item_ids": [draft_one["id"], draft_two["id"], active_item["id"], archived_item["id"]],
                "target_status": COST_STATUS_DRAFT,
                "reason": "批量恢复待核定",
            },
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert activate_response.status_code == 200
    activate_data = activate_response.json()["data"]
    assert activate_data["requested_count"] == 5
    assert activate_data["changed_count"] == 2
    assert activate_data["skipped_count"] == 1
    assert activate_data["conflict_count"] == 1
    assert activate_data["not_found_count"] == 1
    assert set(activate_data["changed_ids"]) == {draft_one["id"], draft_two["id"]}
    assert activate_data["conflicts"][0]["id"] == archived_item["id"]
    assert missing_reason.status_code == 422
    assert missing_reason.json()["detail"] == "REASON_REQUIRED"
    assert restore_response.status_code == 200
    restore_data = restore_response.json()["data"]
    assert restore_data["changed_count"] == 3
    assert restore_data["conflict_count"] == 1
    assert set(restore_data["changed_ids"]) == {draft_one["id"], draft_two["id"], active_item["id"]}

    db = SessionLocal()
    try:
        rows = db.query(CostItem).filter(CostItem.id.in_([draft_one["id"], draft_two["id"], active_item["id"], archived_item["id"]])).all()
        status_by_id = {row.id: row.status for row in rows}
        assert status_by_id[draft_one["id"]] == COST_STATUS_DRAFT
        assert status_by_id[draft_two["id"]] == COST_STATUS_DRAFT
        assert status_by_id[active_item["id"]] == COST_STATUS_DRAFT
        assert status_by_id[archived_item["id"]] == COST_STATUS_ARCHIVED
        active_histories = (
            db.query(CostItemHistory)
            .filter(
                CostItemHistory.cost_item_id.in_([draft_one["id"], draft_two["id"]]),
                CostItemHistory.new_status == COST_STATUS_ACTIVE,
                CostItemHistory.change_reason == "批量核定",
            )
            .count()
        )
        draft_histories = (
            db.query(CostItemHistory)
            .filter(
                CostItemHistory.cost_item_id.in_([draft_one["id"], draft_two["id"], active_item["id"]]),
                CostItemHistory.new_status == COST_STATUS_DRAFT,
                CostItemHistory.change_reason == "批量恢复待核定",
            )
            .count()
        )
    finally:
        db.close()

    assert active_histories == 2
    assert draft_histories == 3


def test_bulk_status_rejects_staff(client):
    _, admin_headers = _headers(client, "admin")
    _, staff_headers = _headers(client, "staff")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, admin_headers, item_name=f"bulk-staff-{uuid.uuid4().hex[:6]}")
        response = client.post(
            "/api/v1/admin/cost-items/bulk-status",
            headers=staff_headers,
            json={"item_ids": [item["id"]], "target_status": COST_STATUS_ACTIVE},
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "PERMISSION_DENIED"


def test_import_preview_and_confirm_are_idempotent(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        content = _seed_workbook(item_name=f"导入条目-{uuid.uuid4().hex[:6]}")
        preview = client.post(
            "/api/v1/admin/cost-items/import/preview",
            headers=headers,
            files={"file": ("seed.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview.status_code == 200, preview.text
        preview_data = preview.json()["data"]
        first_confirm = client.post(
            "/api/v1/admin/cost-items/import/confirm",
            headers=headers,
            json={"batch_id": preview_data["batch_id"]},
        )
        second_confirm = client.post(
            "/api/v1/admin/cost-items/import/confirm",
            headers=headers,
            json={"batch_id": preview_data["batch_id"]},
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert preview_data["item_count"] == 1
    item = preview_data["items"][0]
    assert item["category"].startswith("第二章")
    assert item["price"] == item["subcontract_composite_price"]
    assert item["client_tax_excluded_price"] == 31
    assert item["client_labor_price"] == 15.147
    assert item["client_auxiliary_material_price"] == 10.5989
    assert item["client_direct_fee"] == 25.7459
    assert item["client_management_profit"] == 5.149
    assert item["subcontract_labor_price"] == 12.215
    assert item["subcontract_main_material_price"] == 0
    assert item["subcontract_auxiliary_material_price"] == 8.547
    assert first_confirm.status_code == 200
    assert first_confirm.json()["data"]["created_count"] == 1
    assert second_confirm.status_code == 200
    assert second_confirm.json()["data"] == first_confirm.json()["data"]


def test_import_skips_existing_active_duplicate(client):
    _, headers = _headers(client, "admin")
    item_name = f"重复导入-{uuid.uuid4().hex[:6]}"
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, headers, item_name=item_name, subcategory=None)
        activate = client.post(f"/api/v1/admin/cost-items/{item['id']}/activate", headers=headers)
        assert activate.status_code == 200
        preview = client.post(
            "/api/v1/admin/cost-items/import/preview",
            headers=headers,
            files={"file": ("seed.xlsx", _seed_workbook(item_name=item_name), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        confirm = client.post(
            "/api/v1/admin/cost-items/import/confirm",
            headers=headers,
            json={"batch_id": preview.json()["data"]["batch_id"]},
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert any(warning["type"] == "existing_active" for warning in preview.json()["data"]["duplicate_warnings"])
    assert confirm.json()["data"]["created_count"] == 0
    assert confirm.json()["data"]["skipped_count"] == 1
    db = SessionLocal()
    try:
        active_count = db.query(CostItem).filter(CostItem.item_name == item_name, CostItem.status == COST_STATUS_ACTIVE).count()
    finally:
        db.close()
    assert active_count == 1
