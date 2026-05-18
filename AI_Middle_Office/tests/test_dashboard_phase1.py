import uuid
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_feedback import QuoteFeedback
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.models.user import User, UserRole


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


def _login(client, username: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _set_quote_dashboard_flag(value: bool) -> bool:
    old_value = settings.feature_dashboard_quote
    object.__setattr__(settings, "feature_dashboard_quote", value)
    return old_value


def _seed_quote_dashboard_rows(username: str, now: datetime | None = None) -> dict:
    now = (now or datetime.now()).replace(microsecond=0)
    created_at = now - timedelta(days=1, minutes=12)
    finished_at = created_at + timedelta(minutes=2)
    confirmed_at = finished_at + timedelta(minutes=10)
    succeeded_job_id = f"dash-success-{uuid.uuid4().hex[:8]}"
    failed_job_id = f"dash-failed-{uuid.uuid4().hex[:8]}"

    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=succeeded_job_id,
                username=username,
                status="succeeded",
                stage="completed",
                message="dashboard quote",
                duration_ms=120_000,
                result_total_amount=1000,
                result_item_count=2,
                created_at=created_at,
                updated_at=finished_at,
                finished_at=finished_at,
            )
        )
        db.add(
            QuoteJob(
                job_id=failed_job_id,
                username=username,
                status="failed",
                stage="n8n",
                message="failed dashboard quote",
                created_at=now - timedelta(days=1, minutes=5),
                updated_at=now - timedelta(days=1, minutes=4),
                error_message="workflow failed",
            )
        )
        db.flush()
        db.add(
            QuoteHistory(
                username=username,
                quote_id=f"quote-{succeeded_job_id}",
                quote_job_id=succeeded_job_id,
                created_at=confirmed_at,
                total_amount=1030,
                item_count=2,
                payload_json="{}",
            )
        )
        db.add(
            QuoteFeedback(
                quote_id=f"feedback-{succeeded_job_id}",
                quote_job_id=succeeded_job_id,
                username=username,
                status="confirmed",
                was_modified=True,
                ai_total_amount=1000,
                final_total_amount=1030,
                amount_delta=30,
                created_at=confirmed_at,
                confirmed_at=confirmed_at,
            )
        )
        db.commit()
        return {
            "succeeded_job_id": succeeded_job_id,
            "failed_job_id": failed_job_id,
            "created_date": created_at.date().isoformat(),
        }
    finally:
        db.close()


def test_quote_speed_dashboard_requires_feature_flag(client):
    password = "secret123"
    username = f"dash_admin_disabled_{uuid.uuid4().hex[:8]}"
    _create_user(username, password, legacy_role="admin", roles=["admin"])
    headers = _login(client, username, password)

    old_value = _set_quote_dashboard_flag(False)
    try:
        response = client.get("/api/v1/admin/dashboard/quote-speed", headers=headers)
    finally:
        _set_quote_dashboard_flag(old_value)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_quote_speed_dashboard_aggregates_quote_metrics(client, monkeypatch):
    from app.services import quote_dashboard

    frozen_now = datetime(2035, 1, 15, 12, 0, 0)
    monkeypatch.setattr(quote_dashboard, "_now", lambda: frozen_now.replace(tzinfo=quote_dashboard.CN_TZ))
    password = "secret123"
    username = f"dash_admin_{uuid.uuid4().hex[:8]}"
    _create_user(username, password, legacy_role="admin", roles=["admin"])
    headers = _login(client, username, password)
    seeded = _seed_quote_dashboard_rows(username, now=frozen_now)

    old_value = _set_quote_dashboard_flag(True)
    try:
        response = client.get("/api/v1/admin/dashboard/quote-speed?range=last_30_days", headers=headers)
    finally:
        _set_quote_dashboard_flag(old_value)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["timezone"] == "Asia/Shanghai"
    assert data["range"] == "last_30_days"
    assert data["sample_count"] == 2
    assert data["completed_count"] == 1
    assert data["confirmed_count"] == 1
    assert data["feedback_sample_count"] == 1
    assert data["modified_count"] == 1
    assert data["ai_duration_avg_ms"] == 120_000
    assert data["manual_confirm_duration_avg_ms"] == 600_000
    assert data["total_delivery_duration_avg_ms"] == 720_000
    assert data["modified_rate"] == 1.0
    assert data["low_sample_warning"] is True
    assert {"status": "failed", "count": 1} in data["status_distribution"]
    assert {"status": "succeeded", "count": 1} in data["status_distribution"]
    trend = next(item for item in data["daily_trends"] if item["date"] == seeded["created_date"])
    assert trend["sample_count"] == 2
    assert trend["confirmed_count"] == 1


def test_quote_speed_dashboard_allows_viewer_but_not_staff(client):
    password = "secret123"
    viewer_name = f"dash_viewer_{uuid.uuid4().hex[:8]}"
    staff_name = f"dash_staff_{uuid.uuid4().hex[:8]}"
    _create_user(viewer_name, password, roles=["viewer"])
    _create_user(staff_name, password, roles=["staff"])
    viewer_headers = _login(client, viewer_name, password)
    staff_headers = _login(client, staff_name, password)

    old_value = _set_quote_dashboard_flag(True)
    try:
        viewer_response = client.get("/api/v1/admin/dashboard/quote-speed", headers=viewer_headers)
        staff_response = client.get("/api/v1/admin/dashboard/quote-speed", headers=staff_headers)
    finally:
        _set_quote_dashboard_flag(old_value)

    assert viewer_response.status_code == 200
    assert staff_response.status_code == 403
    assert staff_response.json()["detail"] == "PERMISSION_DENIED"


def test_quote_speed_dashboard_corrects_legacy_finished_at_offset(client, monkeypatch):
    from app.services import quote_dashboard

    frozen_now = datetime(2036, 2, 20, 12, 0, 0)
    monkeypatch.setattr(quote_dashboard, "_now", lambda: frozen_now.replace(tzinfo=quote_dashboard.CN_TZ))
    password = "secret123"
    username = f"dash_legacy_time_{uuid.uuid4().hex[:8]}"
    _create_user(username, password, legacy_role="admin", roles=["admin"])
    headers = _login(client, username, password)

    created_at = frozen_now - timedelta(days=1)
    real_finished_at = created_at + timedelta(seconds=30)
    legacy_finished_at = real_finished_at - timedelta(hours=8)
    confirmed_at = created_at + timedelta(seconds=90)
    job_id = f"dash-legacy-time-{uuid.uuid4().hex[:8]}"

    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status="succeeded",
                stage="completed",
                message="legacy timezone quote",
                duration_ms=30_000,
                created_at=created_at,
                updated_at=legacy_finished_at,
                finished_at=legacy_finished_at,
            )
        )
        db.add(
            QuoteHistory(
                username=username,
                quote_id=f"quote-{job_id}",
                quote_job_id=job_id,
                created_at=confirmed_at,
                total_amount=100,
                item_count=1,
                payload_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    old_value = _set_quote_dashboard_flag(True)
    try:
        response = client.get("/api/v1/admin/dashboard/quote-speed?range=last_30_days", headers=headers)
    finally:
        _set_quote_dashboard_flag(old_value)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sample_count"] == 1
    assert data["ai_duration_avg_ms"] == 30_000
    assert data["manual_confirm_duration_avg_ms"] == 60_000
    assert data["total_delivery_duration_avg_ms"] == 90_000
