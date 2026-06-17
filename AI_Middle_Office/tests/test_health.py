from types import SimpleNamespace

from app import main as main_module
from app.services import ops_monitor


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
    assert response.json()["task_queue"]["mode"] == "disabled"
    assert response.json()["task_queue"]["worker"] == "disabled"
    assert response.json()["external_dependencies"]["enabled"] is False
    assert response.headers.get("x-trace-id")


def test_health_ready_can_probe_external_dependencies(monkeypatch, client):
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(
            rag_service_url="http://rag.test",
            task_queue_mode="disabled",
            ready_check_external_services=True,
        ),
    )
    monkeypatch.setattr(
        main_module,
        "check_task_queue",
        lambda: {"ok": True, "mode": "disabled", "worker": "disabled"},
    )
    monkeypatch.setattr(
        ops_monitor,
        "collect_external_dependency_statuses",
        lambda: [
            {"key": "rag", "name": "RAG Service", "ok": False, "status": "error", "detail": "timeout"},
            {"key": "n8n", "name": "n8n", "ok": True, "status": "ok", "latency_ms": 12.3},
        ],
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["external_dependencies"]["enabled"] is True
    assert body["external_dependencies"]["overall_status"] == "degraded"
    assert {item["key"] for item in body["external_dependencies"]["services"]} == {"rag", "n8n"}
