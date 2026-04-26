def test_health_live(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers.get("x-trace-id")


def test_health_ready(client):
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["database"] == "ok"
    assert response.headers.get("x-trace-id")
