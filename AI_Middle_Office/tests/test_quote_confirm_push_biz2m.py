import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.cost_item import COST_SOURCE_AI_SUGGESTED, COST_SOURCE_MANUAL, COST_STATUS_DRAFT, CostItem
from app.models.user import User


PASSWORD = "secret123"


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user_headers(client):
    username = f"biz2m_push_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.add(
            User(
                username=username,
                hashed_password=get_password_hash(PASSWORD),
                role="user",
                quota=20,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/login", data={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class _FakePushSuccess:
    status_code = 200

    def json(self):
        return {"ok": True}


class _FakePushFailed:
    status_code = 500

    def json(self):
        return {"ok": False}


async def _fake_push_success(**kwargs):
    return _FakePushSuccess()


async def _fake_push_failed(**kwargs):
    return _FakePushFailed()


def _enable_capture():
    old_cost_db = _set_flag("feature_cost_db", True)
    old_capture = _set_flag("feature_no_cost_draft_capture", True)
    return old_cost_db, old_capture


def _restore_capture(flags):
    old_cost_db, old_capture = flags
    _set_flag("feature_cost_db", old_cost_db)
    _set_flag("feature_no_cost_draft_capture", old_capture)


def test_confirm_push_success_creates_no_cost_draft(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"确认下发无底价-{uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "spec": "特殊做法",
                        "quantity": 10,
                        "unit": "m",
                        "unit_price": 45,
                        "total_price": 450,
                        "notes": "人工确认供应商报价",
                        "cost_reference": {"matched": False},
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 200, response.text
    summary = response.json()["data"]["no_cost_draft_summary"]
    assert summary["created_count"] == 1
    assert "成本库待审核草稿" in response.json()["message"]

    db = SessionLocal()
    try:
        item = db.query(CostItem).filter(CostItem.item_name == item_name).one()
        assert item.status == COST_STATUS_DRAFT
        assert item.source == COST_SOURCE_AI_SUGGESTED
        assert item.price == 45
    finally:
        db.close()


def test_confirm_push_captures_total_only_no_cost_draft(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"仅填合计下发无底价-{uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 0,
                        "unit": "㎡",
                        "unit_price": 0,
                        "total_price": 100,
                        "notes": "人工只填写了系统合计",
                        "cost_reference": {"matched": False},
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 200, response.text
    summary = response.json()["data"]["no_cost_draft_summary"]
    assert summary["created_count"] == 1
    assert summary["created_items"][0]["price"] == 100

    db = SessionLocal()
    try:
        item = db.query(CostItem).filter(CostItem.item_name == item_name).one()
        assert item.status == COST_STATUS_DRAFT
        assert item.source == COST_SOURCE_AI_SUGGESTED
        assert item.price == 100
        assert "draft_price_source: confirmed_total_price_fallback" in item.notes
    finally:
        db.close()


def test_confirm_push_manual_override_creates_manual_no_cost_draft(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"人工改价无底价-{uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 10,
                        "unit": "m",
                        "ai_suggested_unit_price": 45,
                        "unit_price": 55,
                        "manual_unit_price": 55,
                        "total_price": 550,
                        "manual_price_action": "manual_override",
                        "manual_price_source": "manual_input",
                        "final_price_source": "manual",
                        "price_confirmation_label": "人工确认价",
                        "cost_reference": {"matched": False},
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 200, response.text
    summary = response.json()["data"]["no_cost_draft_summary"]
    assert summary["created_count"] == 1
    assert summary["created_items"][0]["source"] == COST_SOURCE_MANUAL

    db = SessionLocal()
    try:
        item = db.query(CostItem).filter(CostItem.item_name == item_name).one()
        assert item.status == COST_STATUS_DRAFT
        assert item.source == COST_SOURCE_MANUAL
        assert item.price == 55
        assert "manual_price_action: manual_override" in item.notes
        assert "final_price_source: manual" in item.notes
    finally:
        db.close()


def test_confirm_push_accept_ai_suggestion_keeps_ai_suggested_no_cost_draft(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"采纳AI建议无底价-{uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 6,
                        "unit": "个",
                        "ai_suggested_unit_price": 45.71,
                        "unit_price": 45.71,
                        "manual_unit_price": 45.71,
                        "total_price": 274.26,
                        "manual_price_action": "accepted_ai_suggestion",
                        "manual_price_source": "accepted_ai_suggestion",
                        "final_price_source": "ai_suggested",
                        "price_confirmation_label": "人工确认采纳AI建议",
                        "cost_reference": {"matched": False},
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 200, response.text
    summary = response.json()["data"]["no_cost_draft_summary"]
    assert summary["created_count"] == 1
    assert summary["created_items"][0]["source"] == COST_SOURCE_AI_SUGGESTED
    assert summary["created_items"][0]["manual_price_action"] == "accepted_ai_suggestion"

    db = SessionLocal()
    try:
        item = db.query(CostItem).filter(CostItem.item_name == item_name).one()
        assert item.status == COST_STATUS_DRAFT
        assert item.source == COST_SOURCE_AI_SUGGESTED
        assert item.price == 45.71
        assert "manual_price_action: accepted_ai_suggestion" in item.notes
        assert "price_confirmation_label: 人工确认采纳AI建议" in item.notes
    finally:
        db.close()


def test_confirm_push_does_not_capture_when_push_fails(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_failed)
    flags = _enable_capture()
    item_name = f"推送失败无底价-{uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 1,
                        "unit": "项",
                        "unit_price": 300,
                        "total_price": 300,
                        "cost_reference": {"matched": False},
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 500
    db = SessionLocal()
    try:
        assert db.query(CostItem).filter(CostItem.item_name == item_name).count() == 0
    finally:
        db.close()


def test_confirm_push_skips_matched_cost_reference(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"已有成本参考-{uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 5,
                        "unit": "m",
                        "unit_price": 6,
                        "total_price": 30,
                        "cost_reference": {"matched": True, "cost_item_id": 180},
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 200, response.text
    summary = response.json()["data"]["no_cost_draft_summary"]
    assert summary["created_count"] == 0
    assert summary["candidate_count"] == 0
    db = SessionLocal()
    try:
        assert db.query(CostItem).filter(CostItem.item_name == item_name).count() == 0
    finally:
        db.close()


def test_confirm_push_rejects_unpriced_placeholder_before_capture(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"未补价占位-{uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "unit": "m",
                        "unit_price": 0,
                        "total_price": 0,
                        "requirement_placeholder": True,
                        "quote_source": "requirement_placeholder",
                        "cost_reference": {"matched": False},
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 409
    db = SessionLocal()
    try:
        assert db.query(CostItem).filter(CostItem.item_name == item_name).count() == 0
    finally:
        db.close()


def test_confirm_push_rejects_unconfirmed_cost_candidate(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"BIZ2r ambiguous push {uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 5,
                        "unit": "m",
                        "unit_price": 100,
                        "total_price": 500,
                        "cost_reference": {
                            "matched": True,
                            "cost_item_id": 1,
                            "requires_manual_cost_candidate_confirmation": True,
                            "candidate_count": 2,
                        },
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 409
    assert "多条 active 成本候选" in response.json()["detail"]
    db = SessionLocal()
    try:
        assert db.query(CostItem).filter(CostItem.item_name == item_name).count() == 0
    finally:
        db.close()


def test_confirm_push_rejects_unconfirmed_ai_rewrite_cost_reference(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"BIZ2w3 ai rewrite push {uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 5,
                        "unit": "m",
                        "unit_price": 100,
                        "total_price": 500,
                        "cost_reference": {
                            "matched": True,
                            "cost_item_id": 1,
                            "requires_manual_ai_rewrite_confirmation": True,
                            "source_requirement_project_name": "original cost item",
                            "ai_returned_project_name": item_name,
                        },
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 409
    assert "AI" in response.json()["detail"]
    db = SessionLocal()
    try:
        assert db.query(CostItem).filter(CostItem.item_name == item_name).count() == 0
    finally:
        db.close()


def test_confirm_push_rejects_unconfirmed_ai_note_cost_basis_conflict(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"BIZ2w4 ai note push {uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 5,
                        "unit": "m",
                        "unit_price": 100,
                        "total_price": 500,
                        "notes": "已命中成本库参考，请以人工确认价为准。",
                        "ai_original_notes": "当前底层数据集中未包含相关条目，无法提供报价。建议补充对应施工项。",
                        "cost_reference": {
                            "matched": True,
                            "cost_item_id": 1,
                            "ai_note_cost_basis_conflict": True,
                            "requires_manual_ai_note_confirmation": True,
                            "manual_ai_note_confirmed": False,
                        },
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 409
    assert "AI 备注与成本库依据不一致" in response.json()["detail"]
    db = SessionLocal()
    try:
        assert db.query(CostItem).filter(CostItem.item_name == item_name).count() == 0
    finally:
        db.close()


def test_confirm_push_allows_confirmed_ai_note_cost_basis_conflict(client, monkeypatch):
    headers = _create_user_headers(client)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)
    flags = _enable_capture()
    item_name = f"BIZ2w4 ai note confirmed {uuid.uuid4().hex[:6]}"
    try:
        response = client.post(
            "/api/v1/confirm_push",
            headers=headers,
            json={
                "project_details": [
                    {
                        "project_name": item_name,
                        "quantity": 5,
                        "unit": "m",
                        "unit_price": 100,
                        "total_price": 500,
                        "notes": "已人工确认：按成本库参考和现场复核价下发。",
                        "ai_original_notes": "当前底层数据集中未包含相关条目，无法提供报价。建议补充对应施工项。",
                        "cost_reference": {
                            "matched": True,
                            "cost_item_id": 1,
                            "ai_note_cost_basis_conflict": True,
                            "requires_manual_ai_note_confirmation": True,
                            "manual_ai_note_confirmed": True,
                        },
                    }
                ]
            },
        )
    finally:
        _restore_capture(flags)

    assert response.status_code == 200, response.text
    summary = response.json()["data"]["no_cost_draft_summary"]
    assert summary["created_count"] == 0
    db = SessionLocal()
    try:
        assert db.query(CostItem).filter(CostItem.item_name == item_name).count() == 0
    finally:
        db.close()
