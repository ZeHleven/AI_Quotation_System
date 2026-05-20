import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.client_inquiry import (
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    STAGE_INITIAL_CONTACT,
    STAGE_LOST,
    STAGE_QUOTING,
    STAGE_REQUIREMENT_CONFIRMATION,
    STAGE_WON,
    ClientInquiry,
)
from app.models.client_inquiry_event import (
    EVENT_TYPE_CANCEL,
    EVENT_TYPE_CREATE,
    EVENT_TYPE_STAGE_CHANGE,
    EVENT_TYPE_TRANSFER,
    ClientInquiryEvent,
)
from app.models.quote_job import QuoteJob
from app.models.user import User, UserRole


PASSWORD = "secret123"
CN_TZ = ZoneInfo("Asia/Shanghai")


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "staff", *, quota: int = 20) -> User:
    username = f"biz1a_{role}_{uuid.uuid4().hex[:10]}"
    legacy_role = "admin" if role in {"admin", "system_admin"} else role if role != "staff" else "user"
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(PASSWORD),
            role=legacy_role,
            role_version=1,
            quota=quota,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz1a test seed"))
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


def _create_ledger(client, headers: dict, **overrides) -> dict:
    payload = {
        "source": f"source-{uuid.uuid4().hex[:8]}",
        "client_name": f"client-{uuid.uuid4().hex[:8]}",
        "client_phone": f"139{uuid.uuid4().hex[:8]}",
        "notes": "biz1a test note",
    }
    payload.update(overrides)
    response = client.post("/api/v1/business-ledger", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _events(inquiry_id: str) -> list[ClientInquiryEvent]:
    db = SessionLocal()
    try:
        return (
            db.query(ClientInquiryEvent)
            .filter(ClientInquiryEvent.inquiry_id == inquiry_id)
            .order_by(ClientInquiryEvent.id.asc())
            .all()
        )
    finally:
        db.close()


def _inquiry(inquiry_id: str) -> ClientInquiry:
    db = SessionLocal()
    try:
        return db.query(ClientInquiry).filter(ClientInquiry.inquiry_id == inquiry_id).one()
    finally:
        db.close()


def test_feature_flag_disabled_hides_all_business_ledger_routes(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", False)
    try:
        responses = [
            client.post("/api/v1/business-ledger", headers=headers, json={}),
            client.get("/api/v1/business-ledger", headers=headers),
            client.get("/api/v1/business-ledger/missing-id", headers=headers),
            client.patch("/api/v1/business-ledger/missing-id", headers=headers, json={}),
            client.post("/api/v1/business-ledger/missing-id/cancel", headers=headers, json={"reason": "x"}),
        ]
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert [response.status_code for response in responses] == [404, 404, 404, 404, 404]
    assert all(response.json()["detail"] == "NOT_FOUND" for response in responses)


def test_feature_flag_enabled_makes_business_ledger_list_reachable(client):
    _, headers = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        response = client.get("/api/v1/business-ledger", headers=headers)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 200
    assert response.json()["code"] == 200


def test_staff_create_outbound_sets_defaults_and_create_event(client):
    staff, headers = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    before = datetime.now(CN_TZ).replace(tzinfo=None, microsecond=0) - timedelta(seconds=1)
    try:
        data = _create_ledger(client, headers, source="staff-create")
    finally:
        _set_flag("feature_business_ledger", old_flag)
    after = datetime.now(CN_TZ).replace(tzinfo=None, microsecond=0) + timedelta(seconds=1)

    inquiry = _inquiry(data["inquiry_id"])
    assert inquiry.direction == DIRECTION_OUTBOUND
    assert inquiry.responder_id == staff.id
    assert before <= inquiry.inquiry_time <= after
    assert inquiry.first_response_time is None
    events = _events(inquiry.inquiry_id)
    assert [event.event_type for event in events] == [EVENT_TYPE_CREATE]
    assert events[0].new_value == STAGE_INITIAL_CONTACT
    assert events[0].operator_id == staff.id
    assert events[0].after_json["direction"] == DIRECTION_OUTBOUND


def test_admin_create_can_assign_responder(client):
    _, admin_headers = _headers(client, "admin")
    responder, _ = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        data = _create_ledger(client, admin_headers, responder_id=responder.id)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert data["responder_id"] == responder.id


def test_staff_create_cannot_assign_other_responder(client):
    _, staff_headers = _headers(client, "staff")
    other, _ = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        response = client.post(
            "/api/v1/business-ledger",
            headers=staff_headers,
            json={"client_name": "forbidden", "responder_id": other.id},
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "PERMISSION_DENIED"


def test_post_rejects_unknown_fields_but_allows_empty_body(client):
    staff, headers = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        company = client.post("/api/v1/business-ledger", headers=headers, json={"company_name": "Acme"})
        project = client.post("/api/v1/business-ledger", headers=headers, json={"project_name": "P-001"})
        empty = client.post("/api/v1/business-ledger", headers=headers, json={})
        patch_empty = client.patch(
            f"/api/v1/business-ledger/{empty.json()['data']['inquiry_id']}",
            headers=headers,
            json={},
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert company.status_code == 422
    assert project.status_code == 422
    assert empty.status_code == 200
    data = empty.json()["data"]
    assert data["direction"] == DIRECTION_OUTBOUND
    assert data["stage"] == STAGE_INITIAL_CONTACT
    assert data["responder_id"] == staff.id
    assert data["client_name"] is None
    assert patch_empty.status_code == 200


def test_list_scope_staff_and_admin_system_admin(client):
    staff_a, headers_a = _headers(client, "staff")
    staff_b, headers_b = _headers(client, "staff")
    _, admin_headers = _headers(client, "admin")
    _, system_headers = _headers(client, "system_admin")
    source = f"scope-{uuid.uuid4().hex[:8]}"
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        a_item = _create_ledger(client, headers_a, source=source, client_name="scope-a")
        b_item = _create_ledger(client, headers_b, source=source, client_name="scope-b")
        list_a = client.get(f"/api/v1/business-ledger?source={source}", headers=headers_a)
        list_admin = client.get(f"/api/v1/business-ledger?source={source}", headers=admin_headers)
        list_system = client.get(f"/api/v1/business-ledger?source={source}", headers=system_headers)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert {item["inquiry_id"] for item in list_a.json()["data"]} == {a_item["inquiry_id"]}
    assert all(item["responder_id"] == staff_a.id for item in list_a.json()["data"])
    assert {item["inquiry_id"] for item in list_admin.json()["data"]} >= {a_item["inquiry_id"], b_item["inquiry_id"]}
    assert {item["inquiry_id"] for item in list_system.json()["data"]} >= {a_item["inquiry_id"], b_item["inquiry_id"]}
    assert staff_b.id != staff_a.id


def test_list_overdue_only_filters_active_past_followups(client):
    _, headers = _headers(client, "admin")
    source = f"overdue-{uuid.uuid4().hex[:8]}"
    past = (datetime.now() - timedelta(days=1)).isoformat()
    future = (datetime.now() + timedelta(days=1)).isoformat()
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        overdue = _create_ledger(client, headers, source=source, next_followup_at=past)
        _create_ledger(client, headers, source=source, next_followup_at=future)
        _create_ledger(client, headers, source=source, next_followup_at=past, stage=STAGE_WON)
        response = client.get(f"/api/v1/business-ledger?source={source}&overdue_only=true", headers=headers)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 200
    ids = {item["inquiry_id"] for item in response.json()["data"]}
    assert ids == {overdue["inquiry_id"]}


def test_list_filters_by_stage_source_keyword_and_date_range(client):
    _, headers = _headers(client, "admin")
    source = f"filter-{uuid.uuid4().hex[:8]}"
    phone = f"138{uuid.uuid4().hex[:8]}"
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        target = _create_ledger(
            client,
            headers,
            source=source,
            client_name=f"target-{uuid.uuid4().hex[:8]}",
            client_phone=phone,
            stage=STAGE_REQUIREMENT_CONFIRMATION,
        )
        _create_ledger(client, headers, source=f"other-{uuid.uuid4().hex[:8]}", stage=STAGE_REQUIREMENT_CONFIRMATION)
        stage_response = client.get(
            f"/api/v1/business-ledger?source={source}&stage={STAGE_REQUIREMENT_CONFIRMATION}",
            headers=headers,
        )
        source_response = client.get(f"/api/v1/business-ledger?source={source}", headers=headers)
        keyword_response = client.get(f"/api/v1/business-ledger?keyword={phone[-6:]}", headers=headers)
        date_response = client.get(
            f"/api/v1/business-ledger?source={source}&date_from=2000-01-01T00:00:00&date_to=2099-01-01T00:00:00",
            headers=headers,
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    for response in [stage_response, source_response, keyword_response, date_response]:
        assert response.status_code == 200
        assert target["inquiry_id"] in {item["inquiry_id"] for item in response.json()["data"]}


def test_list_pagination_returns_total_and_page_size(client):
    _, headers = _headers(client, "admin")
    source = f"page-{uuid.uuid4().hex[:8]}"
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        created = [_create_ledger(client, headers, source=source) for _ in range(3)]
        page1 = client.get(f"/api/v1/business-ledger?source={source}&page=1&page_size=2", headers=headers)
        page2 = client.get(f"/api/v1/business-ledger?source={source}&page=2&page_size=2", headers=headers)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert page1.json()["total"] == 3
    assert len(page1.json()["data"]) == 2
    assert page2.json()["total"] == 3
    assert len(page2.json()["data"]) == 1
    assert {item["inquiry_id"] for item in page1.json()["data"] + page2.json()["data"]} == {
        item["inquiry_id"] for item in created
    }


def test_detail_staff_other_record_hidden_and_feature_disabled_not_found(client):
    _, owner_headers = _headers(client, "staff")
    _, other_headers = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, owner_headers)
        hidden = client.get(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=other_headers)
        _set_flag("feature_business_ledger", False)
        disabled = client.get(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=other_headers)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "BUSINESS_LEDGER_NOT_FOUND"
    assert disabled.status_code == 404
    assert disabled.json()["detail"] == "NOT_FOUND"


def test_admin_can_read_any_business_ledger_record(client):
    _, owner_headers = _headers(client, "staff")
    _, admin_headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, owner_headers)
        response = client.get(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=admin_headers)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 200
    assert response.json()["data"]["inquiry_id"] == item["inquiry_id"]


def test_staff_patch_allows_only_progress_fields(client):
    _, headers = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, headers)
        ok = client.patch(
            f"/api/v1/business-ledger/{item['inquiry_id']}",
            headers=headers,
            json={
                "stage": STAGE_REQUIREMENT_CONFIRMATION,
                "next_followup_at": (datetime.now() + timedelta(days=2)).isoformat(),
                "client_phone": "13900000001",
                "notes": "staff update",
            },
        )
        client_name = client.patch(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=headers, json={"client_name": "x"})
        source = client.patch(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=headers, json={"source": "x"})
        responder = client.patch(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=headers, json={"responder_id": 1})
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert ok.status_code == 200
    assert ok.json()["data"]["stage"] == STAGE_REQUIREMENT_CONFIRMATION
    assert ok.json()["data"]["client_phone"] == "13900000001"
    assert [client_name.status_code, source.status_code, responder.status_code] == [403, 403, 403]


def test_admin_patch_can_update_admin_fields_and_transfer(client):
    _, admin_headers = _headers(client, "admin")
    first, _ = _headers(client, "staff")
    second, _ = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, admin_headers, responder_id=first.id)
        response = client.patch(
            f"/api/v1/business-ledger/{item['inquiry_id']}",
            headers=admin_headers,
            json={
                "client_name": "admin-updated",
                "source": "referral",
                "responder_id": second.id,
                "client_phone": "13900000002",
                "notes": "admin update",
            },
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["client_name"] == "admin-updated"
    assert data["source"] == "referral"
    assert data["responder_id"] == second.id


def test_patch_forbidden_fields_are_rejected_by_schema(client):
    _, headers = _headers(client, "admin")
    forbidden_fields = [
        "direction",
        "inquiry_time",
        "first_response_time",
        "cancelled_at",
        "cancelled_by_id",
        "cancel_reason",
        "company_name",
        "created_at",
        "updated_at",
    ]
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, headers)
        responses = [
            client.patch(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=headers, json={field: "x"})
            for field in forbidden_fields
        ]
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert all(response.status_code == 422 for response in responses)


def test_stage_machine_allows_strict_forward_edge_and_rejects_skip(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        forward = _create_ledger(client, headers)
        forward_response = client.patch(
            f"/api/v1/business-ledger/{forward['inquiry_id']}",
            headers=headers,
            json={"stage": STAGE_REQUIREMENT_CONFIRMATION},
        )
        skip = _create_ledger(client, headers)
        skip_response = client.patch(
            f"/api/v1/business-ledger/{skip['inquiry_id']}",
            headers=headers,
            json={"stage": STAGE_QUOTING},
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert forward_response.status_code == 200
    assert skip_response.status_code == 409
    assert skip_response.json()["detail"] == "STATE_CONFLICT"


def test_stage_machine_allows_jump_to_terminal_states(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        won = _create_ledger(client, headers)
        lost = _create_ledger(client, headers)
        won_response = client.patch(f"/api/v1/business-ledger/{won['inquiry_id']}", headers=headers, json={"stage": STAGE_WON})
        lost_response = client.patch(f"/api/v1/business-ledger/{lost['inquiry_id']}", headers=headers, json={"stage": STAGE_LOST})
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert won_response.status_code == 200
    assert lost_response.status_code == 200
    assert won_response.json()["data"]["stage"] == STAGE_WON
    assert lost_response.json()["data"]["stage"] == STAGE_LOST


def test_terminal_stage_rejects_later_patch(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        won = _create_ledger(client, headers)
        lost = _create_ledger(client, headers)
        client.patch(f"/api/v1/business-ledger/{won['inquiry_id']}", headers=headers, json={"stage": STAGE_WON})
        client.patch(f"/api/v1/business-ledger/{lost['inquiry_id']}", headers=headers, json={"stage": STAGE_LOST})
        won_again = client.patch(f"/api/v1/business-ledger/{won['inquiry_id']}", headers=headers, json={"notes": "late"})
        lost_again = client.patch(f"/api/v1/business-ledger/{lost['inquiry_id']}", headers=headers, json={"notes": "late"})
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert won_again.status_code == 409
    assert lost_again.status_code == 409
    assert won_again.json()["detail"] == "STATE_CONFLICT"
    assert lost_again.json()["detail"] == "STATE_CONFLICT"


def test_admin_cancel_writes_cancel_fields_and_event_snapshot(client):
    admin, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, headers)
        response = client.post(
            f"/api/v1/business-ledger/{item['inquiry_id']}/cancel",
            headers=headers,
            json={"reason": "duplicate lead"},
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["cancelled_at"]
    assert data["cancelled_by_id"] == admin.id
    assert data["cancel_reason"] == "duplicate lead"
    events = _events(item["inquiry_id"])
    assert events[-1].event_type == EVENT_TYPE_CANCEL
    assert events[-1].new_value == "duplicate lead"
    assert events[-1].before_json["cancelled_at"] is None
    assert events[-1].after_json["cancel_reason"] == "duplicate lead"


def test_staff_cancel_forbidden_and_blank_reason_rejected(client):
    _, staff_headers = _headers(client, "staff")
    _, admin_headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, admin_headers)
        staff_cancel = client.post(
            f"/api/v1/business-ledger/{item['inquiry_id']}/cancel",
            headers=staff_headers,
            json={"reason": "not allowed"},
        )
        blank_cancel = client.post(
            f"/api/v1/business-ledger/{item['inquiry_id']}/cancel",
            headers=admin_headers,
            json={"reason": ""},
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert staff_cancel.status_code == 403
    assert staff_cancel.json()["detail"] == "PERMISSION_DENIED"
    assert blank_cancel.status_code == 422


def test_cancelled_record_rejects_recancel_and_patch(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, headers)
        first_cancel = client.post(
            f"/api/v1/business-ledger/{item['inquiry_id']}/cancel",
            headers=headers,
            json={"reason": "cancel once"},
        )
        second_cancel = client.post(
            f"/api/v1/business-ledger/{item['inquiry_id']}/cancel",
            headers=headers,
            json={"reason": "cancel twice"},
        )
        patch = client.patch(f"/api/v1/business-ledger/{item['inquiry_id']}", headers=headers, json={"notes": "late"})
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert first_cancel.status_code == 200
    assert second_cancel.status_code == 409
    assert patch.status_code == 409
    assert second_cancel.json()["detail"] == "STATE_CONFLICT"
    assert patch.json()["detail"] == "STATE_CONFLICT"


def test_stage_change_event_records_before_after_and_operator(client):
    admin, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, headers)
        response = client.patch(
            f"/api/v1/business-ledger/{item['inquiry_id']}",
            headers=headers,
            json={"stage": STAGE_REQUIREMENT_CONFIRMATION},
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 200
    event = [event for event in _events(item["inquiry_id"]) if event.event_type == EVENT_TYPE_STAGE_CHANGE][-1]
    assert event.operator_id == admin.id
    assert event.old_value == STAGE_INITIAL_CONTACT
    assert event.new_value == STAGE_REQUIREMENT_CONFIRMATION
    assert event.before_json["stage"] == STAGE_INITIAL_CONTACT
    assert event.after_json["stage"] == STAGE_REQUIREMENT_CONFIRMATION


def test_transfer_event_records_before_after_responder(client):
    admin, headers = _headers(client, "admin")
    first, _ = _headers(client, "staff")
    second, _ = _headers(client, "staff")
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, headers, responder_id=first.id)
        response = client.patch(
            f"/api/v1/business-ledger/{item['inquiry_id']}",
            headers=headers,
            json={"responder_id": second.id},
        )
    finally:
        _set_flag("feature_business_ledger", old_flag)

    assert response.status_code == 200
    event = [event for event in _events(item["inquiry_id"]) if event.event_type == EVENT_TYPE_TRANSFER][-1]
    assert event.operator_id == admin.id
    assert event.old_value == str(first.id)
    assert event.new_value == str(second.id)
    assert event.before_json["responder_id"] == first.id
    assert event.after_json["responder_id"] == second.id


def test_event_context_prefers_x_forwarded_for(client):
    _, headers = _headers(client, "admin")
    headers = {
        **headers,
        "X-Forwarded-For": "1.2.3.4, 5.6.7.8",
        "User-Agent": "biz1a-test-agent",
        "X-Trace-Id": "biz1a-trace-001",
    }
    old_flag = _set_flag("feature_business_ledger", True)
    try:
        item = _create_ledger(client, headers)
    finally:
        _set_flag("feature_business_ledger", old_flag)

    event = _events(item["inquiry_id"])[0]
    assert event.ip_address == "1.2.3.4"
    assert event.user_agent == "biz1a-test-agent"
    assert event.trace_id == "biz1a-trace-001"


def test_outbound_does_not_appear_in_phase2_client_inquiries_list_or_patch(client):
    _, headers = _headers(client, "staff")
    old_business = _set_flag("feature_business_ledger", True)
    old_phase2 = _set_flag("feature_client_inquiry", True)
    try:
        item = _create_ledger(client, headers)
        phase2_list = client.get("/api/v1/client-inquiries", headers=headers)
        phase2_patch = client.patch(
            f"/api/v1/client-inquiries/{item['inquiry_id']}",
            headers=headers,
            json={"notes": "should be hidden"},
        )
    finally:
        _set_flag("feature_business_ledger", old_business)
        _set_flag("feature_client_inquiry", old_phase2)

    assert phase2_list.status_code == 200
    assert item["inquiry_id"] not in {row["inquiry_id"] for row in phase2_list.json()["data"]}
    assert phase2_patch.status_code == 404
    assert phase2_patch.json()["detail"] == "CLIENT_INQUIRY_NOT_FOUND"


def test_quote_jobs_default_list_keeps_jobs_without_client_inquiry(client):
    admin, headers = _headers(client, "admin")
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=admin.username,
                status="queued",
                stage="queued",
                message="no client inquiry attached",
                client_inquiry_id=None,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/quote/jobs", headers=headers)

    assert response.status_code == 200
    assert job_id in {row["job_id"] for row in response.json()["data"]}


def test_outbound_does_not_change_response_speed_dashboard_metrics(client):
    _, headers = _headers(client, "admin")
    old_business = _set_flag("feature_business_ledger", True)
    old_dashboard = _set_flag("feature_dashboard_response", True)
    try:
        before = client.get("/api/v1/admin/dashboard/response-speed?range=last_30_days", headers=headers)
        item = _create_ledger(client, headers, source=f"dashboard-{uuid.uuid4().hex[:8]}")
        after = client.get("/api/v1/admin/dashboard/response-speed?range=last_30_days", headers=headers)
    finally:
        _set_flag("feature_business_ledger", old_business)
        _set_flag("feature_dashboard_response", old_dashboard)

    assert _inquiry(item["inquiry_id"]).direction == DIRECTION_OUTBOUND
    assert before.status_code == 200
    assert after.status_code == 200
    before_data = before.json()["data"]
    after_data = after.json()["data"]
    assert after_data["sample_count_total"] == before_data["sample_count_total"]
    assert after_data["avg_first_response_minutes"] == before_data["avg_first_response_minutes"]


def test_inbound_control_record_is_still_visible_in_phase2_list(client):
    staff, headers = _headers(client, "staff")
    old_phase2 = _set_flag("feature_client_inquiry", True)
    inquiry_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            ClientInquiry(
                inquiry_id=inquiry_id,
                direction=DIRECTION_INBOUND,
                source="inbound-control",
                client_name="inbound visible",
                inquiry_time=datetime.now() - timedelta(minutes=5),
                first_response_time=datetime.now(),
                time_source="manual",
                responder_id=staff.id,
            )
        )
        db.commit()
        response = client.get("/api/v1/client-inquiries?source=inbound-control", headers=headers)
    finally:
        db.close()
        _set_flag("feature_client_inquiry", old_phase2)

    assert response.status_code == 200
    assert inquiry_id in {row["inquiry_id"] for row in response.json()["data"]}
