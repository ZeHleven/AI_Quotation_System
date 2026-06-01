import uuid

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole, UserRoleEvent


def _create_user(username: str, password: str, legacy_role: str = "user", roles: list[str] | None = None) -> User:
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role=legacy_role,
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.flush()
        for role in roles or []:
            db.add(UserRole(user_id=user.id, role=role, created_by=None, note="test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_auth_me_returns_roles_and_role_version(client):
    username = f"rbac_staff_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(username, password, roles=["staff"])

    token = _login(client, username, password)
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["roles"] == ["staff"]
    assert payload["role_version"] == 1
    assert payload["data"]["dingtalk_bound"] is False


def test_system_admin_can_grant_role_and_write_event(client):
    admin_name = f"rbac_sys_{uuid.uuid4().hex[:8]}"
    target_name = f"rbac_target_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(admin_name, password, legacy_role="admin", roles=["system_admin", "admin"])
    target = _create_user(target_name, password, roles=["staff"])

    token = _login(client, admin_name, password)
    response = client.post(
        f"/api/v1/admin/users/{target.id}/roles",
        headers={"Authorization": f"Bearer {token}", "X-Trace-Id": "test-grant-role"},
        json={"role": "viewer", "note": "phase0 test grant"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["roles"] == ["staff", "viewer"]
    assert data["role_version"] == 2

    db = SessionLocal()
    try:
        event = (
            db.query(UserRoleEvent)
            .filter(UserRoleEvent.target_user_id == target.id, UserRoleEvent.role == "viewer")
            .first()
        )
        assert event is not None
        assert event.action == "granted"
        assert event.trace_id == "test-grant-role"
    finally:
        db.close()


def test_system_admin_can_create_user_with_initial_roles(client):
    admin_name = f"rbac_create_sys_{uuid.uuid4().hex[:8]}"
    new_username = f"rbac_created_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(admin_name, password, legacy_role="admin", roles=["system_admin", "admin"])

    token = _login(client, admin_name, password)
    response = client.post(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}", "X-Trace-Id": "test-create-user"},
        json={
            "username": new_username,
            "password": password,
            "quota": 7,
            "roles": ["staff", "cost_viewer"],
            "note": "phase0 create user test",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["username"] == new_username
    assert data["quota"] == 7
    assert data["roles"] == ["cost_viewer", "staff"]
    assert data["must_change_password"] is True

    created_token = _login(client, new_username, password)
    me_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {created_token}"})
    assert me_response.status_code == 200
    assert me_response.json()["roles"] == ["cost_viewer", "staff"]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == new_username).first()
        assert user is not None
        events = (
            db.query(UserRoleEvent)
            .filter(UserRoleEvent.target_user_id == user.id)
            .order_by(UserRoleEvent.id.asc())
            .all()
        )
        assert [event.role for event in events] == ["staff", "cost_viewer"]
        assert all(event.trace_id == "test-create-user" for event in events)
    finally:
        db.close()


def test_admin_without_system_admin_cannot_create_user(client):
    admin_name = f"rbac_create_admin_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(admin_name, password, legacy_role="admin", roles=["admin"])

    token = _login(client, admin_name, password)
    response = client.post(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username": f"rbac_denied_create_{uuid.uuid4().hex[:8]}",
            "password": password,
            "quota": 5,
            "roles": ["staff"],
            "note": "should be denied",
        },
    )

    assert response.status_code == 403


def test_system_admin_can_grant_cost_specific_role(client):
    admin_name = f"rbac_cost_sys_{uuid.uuid4().hex[:8]}"
    target_name = f"rbac_cost_target_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(admin_name, password, legacy_role="admin", roles=["system_admin", "admin"])
    target = _create_user(target_name, password, roles=["staff"])

    token = _login(client, admin_name, password)
    response = client.post(
        f"/api/v1/admin/users/{target.id}/roles",
        headers={"Authorization": f"Bearer {token}", "X-Trace-Id": "test-grant-cost-role"},
        json={"role": "cost_viewer", "note": "biz2s test grant"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["roles"] == ["cost_viewer", "staff"]
    assert data["role_version"] == 2

    target_token = _login(client, target_name, password)
    me_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {target_token}"})
    assert me_response.status_code == 200
    available_keys = {item["key"] for item in me_response.json()["available_modules"]}
    assert "cost_db" in available_keys

    db = SessionLocal()
    try:
        event = (
            db.query(UserRoleEvent)
            .filter(UserRoleEvent.target_user_id == target.id, UserRoleEvent.role == "cost_viewer")
            .first()
        )
        assert event is not None
        assert event.action == "granted"
        assert event.trace_id == "test-grant-cost-role"
    finally:
        db.close()


def test_revoke_role_invalidates_existing_token(client):
    admin_name = f"rbac_revoke_admin_{uuid.uuid4().hex[:8]}"
    target_name = f"rbac_revoke_target_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(admin_name, password, legacy_role="admin", roles=["system_admin", "admin"])
    target = _create_user(target_name, password, roles=["staff"])

    target_token = _login(client, target_name, password)
    admin_token = _login(client, admin_name, password)

    revoke_response = client.post(
        f"/api/v1/admin/users/{target.id}/roles/staff/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"note": "phase0 revoke test", "trace_id": "revoke-test"},
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["data"]["roles"] == []

    old_token_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert old_token_response.status_code == 401
    assert old_token_response.json()["detail"] == "ROLE_VERSION_EXPIRED"


def test_staff_cannot_read_admin_users(client):
    username = f"rbac_denied_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(username, password, roles=["staff"])
    token = _login(client, username, password)

    response = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
