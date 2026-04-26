import uuid


def test_register_login_and_me(client):
    username = f"test_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"

    register_response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
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


def test_login_rejects_wrong_password(client):
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "missing-user", "password": "wrong-password"},
    )

    assert response.status_code == 401
