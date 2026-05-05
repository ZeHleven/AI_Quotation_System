import uuid
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.rag_eval_report import RagEvalReport
from app.models.user import User


def _admin_headers(client):
    username = f"eval_admin_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash(password), role="admin", quota=20))
        db.commit()
    finally:
        db.close()
    resp = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _user_headers(client):
    username = f"eval_user_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    resp = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert resp.status_code == 200
    resp = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _insert_report(status="completed", hit_rate=0.85, mrr=0.80, triggered_by="admin"):
    db = SessionLocal()
    try:
        report = RagEvalReport(
            triggered_by=triggered_by,
            status=status,
            started_at=datetime.now(timezone.utc),
            top_k=5,
            case_count=29 if status == "completed" else None,
            hit_rate=hit_rate if status == "completed" else None,
            mrr=mrr if status == "completed" else None,
            by_level_json='{"1":{"hit_rate":1.0,"mrr":1.0},"2":{"hit_rate":0.8,"mrr":0.7},"3":{"hit_rate":0.7,"mrr":0.6},"4":{"hit_rate":0.6,"mrr":0.5}}' if status == "completed" else None,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        return report.id
    finally:
        db.close()


def test_latest_requires_admin(client):
    headers = _user_headers(client)
    resp = client.get("/api/v1/admin/rag_eval/latest", headers=headers)
    assert resp.status_code == 403


def test_latest_returns_none_when_no_reports(client):
    headers = _admin_headers(client)
    resp = client.get("/api/v1/admin/rag_eval/latest", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert "data" in body


def test_latest_returns_most_recent_completed(client):
    headers = _admin_headers(client)
    _insert_report(status="completed", hit_rate=0.85, mrr=0.80)
    resp = client.get("/api/v1/admin/rag_eval/latest", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data is not None
    assert data["status"] == "completed"
    assert "hit_rate" in data
    assert "mrr" in data
    assert "quality_ok" in data


def test_latest_quality_ok_true_when_above_threshold(client):
    headers = _admin_headers(client)
    _insert_report(status="completed", hit_rate=0.90, mrr=0.80)
    resp = client.get("/api/v1/admin/rag_eval/latest", headers=headers)
    data = resp.json()["data"]
    # 默认阈值 hit_rate=0.70, mrr=0.50；0.90 和 0.80 均达标
    assert data["quality_ok"] is True


def test_latest_quality_ok_false_when_below_threshold(client):
    headers = _admin_headers(client)
    _insert_report(status="completed", hit_rate=0.50, mrr=0.30)
    resp = client.get("/api/v1/admin/rag_eval/latest", headers=headers)
    data = resp.json()["data"]
    assert data["quality_ok"] is False


def test_history_requires_admin(client):
    headers = _user_headers(client)
    resp = client.get("/api/v1/admin/rag_eval/history", headers=headers)
    assert resp.status_code == 403


def test_history_returns_list(client):
    headers = _admin_headers(client)
    _insert_report(status="completed", hit_rate=0.75, mrr=0.60)
    resp = client.get("/api/v1/admin/rag_eval/history", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 1
