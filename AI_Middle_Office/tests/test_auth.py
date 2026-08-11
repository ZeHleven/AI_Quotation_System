import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def test_register_login_and_me(client):
    username = f"test_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"

    old_flag = _set_flag("allow_self_registration", True)
    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": password},
        )
    finally:
        _set_flag("allow_self_registration", old_flag)
    assert register_response.status_code == 200

    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["username"] == username
    assert me_response.json()["role"] == "user"
    assert me_response.json()["quota"] == 5


def test_register_disabled_by_default(client):
    old_flag = _set_flag("allow_self_registration", False)
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={"username": f"disabled_{uuid.uuid4().hex[:10]}", "password": "secret123"},
        )
    finally:
        _set_flag("allow_self_registration", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "SELF_REGISTRATION_DISABLED"


def test_login_rejects_wrong_password(client):
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "missing-user", "password": "wrong-password"},
    )

    assert response.status_code == 401


def test_forced_password_change_blocks_business_apis_and_revokes_rotation_token(client):
    username = f"forced_rotation_{uuid.uuid4().hex[:10]}"
    old_password = "temporary123"
    new_password = "changed456"
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(old_password),
            role="user",
            role_version=1,
            quota=5,
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role="staff", created_by=None, note="forced rotation test"))
        db.commit()
    finally:
        db.close()

    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": old_password},
    )
    assert login_response.status_code == 200
    assert login_response.json()["must_change_password"] is True
    rotation_token = login_response.json()["access_token"]
    rotation_headers = {"Authorization": f"Bearer {rotation_token}"}

    me_response = client.get("/api/v1/auth/me", headers=rotation_headers)
    assert me_response.status_code == 200
    assert me_response.json()["must_change_password"] is True

    blocked_response = client.get("/api/v1/quote/jobs", headers=rotation_headers)
    assert blocked_response.status_code == 403
    assert blocked_response.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        user.must_change_password = False
        db.commit()
    finally:
        db.close()
    claim_blocked_response = client.get("/api/v1/quote/jobs", headers=rotation_headers)
    assert claim_blocked_response.status_code == 403
    assert claim_blocked_response.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        user.must_change_password = True
        db.commit()
    finally:
        db.close()

    change_response = client.post(
        "/api/v1/auth/change_password",
        headers=rotation_headers,
        json={"old_password": old_password, "new_password": new_password},
    )
    assert change_response.status_code == 200
    assert change_response.json()["must_change_password"] is False
    rotated_headers = {
        "Authorization": f"Bearer {change_response.json()['access_token']}"
    }
    assert client.get("/api/v1/quote/jobs", headers=rotated_headers).status_code == 200

    expired_response = client.get("/api/v1/auth/me", headers=rotation_headers)
    assert expired_response.status_code == 401
    assert expired_response.json()["detail"] == "ROLE_VERSION_EXPIRED"

    assert client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": old_password},
    ).status_code == 401

    relogin_response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": new_password},
    )
    assert relogin_response.status_code == 200
    assert relogin_response.json()["must_change_password"] is False
    normal_headers = {"Authorization": f"Bearer {relogin_response.json()['access_token']}"}
    assert client.get("/api/v1/quote/jobs", headers=normal_headers).status_code == 200
