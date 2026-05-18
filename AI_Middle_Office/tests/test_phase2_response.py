import uuid
import subprocess
import sys
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.client_inquiry import ClientInquiry
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


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def test_quote_job_runner_imports_phase2_metadata_for_worker():
    code = (
        "import app.services.quote_job_runner; "
        "from app.core.database import Base; "
        "assert 'client_inquiries' in Base.metadata.tables"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_quote_job_creates_client_inquiry_when_enabled(client):
    username = f"phase2_staff_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(username, password, roles=["staff"])
    headers = _login(client, username, password)
    inquiry_time = (datetime.now() - timedelta(minutes=15)).replace(microsecond=0)

    old_flag = _set_flag("feature_client_inquiry", True)
    try:
        response = client.post(
            "/api/v1/quote/jobs",
            data={
                "message": "客厅地砖 20 平米",
                "source": "dingtalk",
                "client_name": "张三",
                "client_phone": "13800000000",
                "inquiry_time": inquiry_time.isoformat(sep=" "),
                "time_source": "manual",
                "notes": "客户主动咨询",
            },
            headers=headers,
        )
    finally:
        _set_flag("feature_client_inquiry", old_flag)

    assert response.status_code == 202
    job = response.json()["data"]
    assert job["client_inquiry_id"]

    db = SessionLocal()
    try:
        inquiry = db.query(ClientInquiry).filter(ClientInquiry.inquiry_id == job["client_inquiry_id"]).one()
        quote_job = db.query(QuoteJob).filter(QuoteJob.job_id == job["job_id"]).one()
        assert inquiry.source == "dingtalk"
        assert inquiry.client_name == "张三"
        assert inquiry.time_source == "manual"
        assert inquiry.first_quote_job_id == job["job_id"]
        assert quote_job.client_inquiry_id == inquiry.inquiry_id
    finally:
        db.close()


def test_quote_job_retry_inherits_client_inquiry(client):
    username = f"phase2_retry_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(username, password, roles=["staff"])
    headers = _login(client, username, password)
    inquiry_time = (datetime.now() - timedelta(minutes=12)).replace(microsecond=0)

    old_flag = _set_flag("feature_client_inquiry", True)
    try:
        create_response = client.post(
            "/api/v1/quote/jobs",
            data={
                "message": "卧室乳胶漆 30 平米",
                "source": "wechat",
                "inquiry_time": inquiry_time.isoformat(sep=" "),
                "time_source": "manual",
            },
            headers=headers,
        )
        job_id = create_response.json()["data"]["job_id"]
        inquiry_id = create_response.json()["data"]["client_inquiry_id"]
        cancel_response = client.post(f"/api/v1/quote/jobs/{job_id}/cancel", headers=headers)
        retry_response = client.post(f"/api/v1/quote/jobs/{job_id}/retry", headers=headers)
    finally:
        _set_flag("feature_client_inquiry", old_flag)

    assert create_response.status_code == 202
    assert cancel_response.status_code == 200
    assert retry_response.status_code == 202
    assert retry_response.json()["data"]["client_inquiry_id"] == inquiry_id


def test_client_inquiry_list_and_patch_scope(client):
    staff_a = f"phase2_a_{uuid.uuid4().hex[:8]}"
    staff_b = f"phase2_b_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    user_a = _create_user(staff_a, password, roles=["staff"])
    user_b = _create_user(staff_b, password, roles=["staff"])
    headers_a = _login(client, staff_a, password)
    headers_b = _login(client, staff_b, password)
    inquiry_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        db.add(
            ClientInquiry(
                inquiry_id=inquiry_id,
                source="phone",
                client_name="李四",
                inquiry_time=datetime.now() - timedelta(minutes=20),
                first_response_time=datetime.now() - timedelta(minutes=10),
                time_source="manual",
                responder_id=user_a.id,
            )
        )
        db.add(
            ClientInquiry(
                inquiry_id=str(uuid.uuid4()),
                source="wechat",
                client_name="王五",
                inquiry_time=datetime.now(),
                first_response_time=datetime.now(),
                time_source="default",
                responder_id=user_b.id,
            )
        )
        db.commit()
    finally:
        db.close()

    old_flag = _set_flag("feature_client_inquiry", True)
    try:
        list_a = client.get("/api/v1/client-inquiries", headers=headers_a)
        list_b = client.get("/api/v1/client-inquiries", headers=headers_b)
        forbidden_patch = client.patch(
            f"/api/v1/client-inquiries/{inquiry_id}",
            json={"source": "wechat"},
            headers=headers_b,
        )
        patch_a = client.patch(
            f"/api/v1/client-inquiries/{inquiry_id}",
            json={"source": "walk_in", "notes": "门店来访"},
            headers=headers_a,
        )
    finally:
        _set_flag("feature_client_inquiry", old_flag)

    assert list_a.status_code == 200
    assert list_a.json()["total"] == 1
    assert list_a.json()["data"][0]["inquiry_id"] == inquiry_id
    assert list_b.status_code == 200
    assert list_b.json()["total"] == 1
    assert forbidden_patch.status_code == 404
    assert patch_a.status_code == 200
    assert patch_a.json()["data"]["source"] == "walk_in"


def test_client_inquiry_list_filters_blank_client_info(client):
    username = f"phase2_info_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    user = _create_user(username, password, roles=["staff"])
    headers = _login(client, username, password)
    visible_id = str(uuid.uuid4())
    blank_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        db.add(
            ClientInquiry(
                inquiry_id=visible_id,
                source="微信",
                client_name="admin",
                client_phone="13800000000",
                inquiry_time=datetime.now() - timedelta(minutes=5),
                first_response_time=datetime.now(),
                time_source="manual",
                responder_id=user.id,
            )
        )
        db.add(
            ClientInquiry(
                inquiry_id=blank_id,
                inquiry_time=datetime.now() - timedelta(minutes=4),
                first_response_time=datetime.now(),
                time_source="default",
                responder_id=user.id,
            )
        )
        db.commit()
    finally:
        db.close()

    old_flag = _set_flag("feature_client_inquiry", True)
    try:
        visible_response = client.get("/api/v1/client-inquiries?has_client_info=true", headers=headers)
        blank_response = client.get("/api/v1/client-inquiries?has_client_info=false", headers=headers)
    finally:
        _set_flag("feature_client_inquiry", old_flag)

    assert visible_response.status_code == 200
    assert visible_response.json()["total"] == 1
    assert visible_response.json()["data"][0]["inquiry_id"] == visible_id
    assert blank_response.status_code == 200
    assert blank_response.json()["total"] == 1
    assert blank_response.json()["data"][0]["inquiry_id"] == blank_id


def test_response_speed_dashboard_excludes_default_time(client, monkeypatch):
    from app.services import response_dashboard

    frozen_now = datetime(2037, 3, 18, 12, 0, 0)
    monkeypatch.setattr(response_dashboard, "_now", lambda: frozen_now.replace(tzinfo=response_dashboard.CN_TZ))
    username = f"phase2_admin_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    admin = _create_user(username, password, legacy_role="admin", roles=["admin"])
    headers = _login(client, username, password)

    db = SessionLocal()
    try:
        db.add(
            ClientInquiry(
                inquiry_id=str(uuid.uuid4()),
                source="dingtalk",
                client_name="赵六",
                inquiry_time=frozen_now - timedelta(days=1, minutes=20),
                first_response_time=frozen_now - timedelta(days=1, minutes=10),
                time_source="manual",
                responder_id=admin.id,
            )
        )
        db.add(
            ClientInquiry(
                inquiry_id=str(uuid.uuid4()),
                source="dingtalk",
                client_name="默认时间样本",
                inquiry_time=frozen_now - timedelta(days=1, minutes=5),
                first_response_time=frozen_now - timedelta(days=1, minutes=5),
                time_source="default",
                responder_id=admin.id,
            )
        )
        db.commit()
    finally:
        db.close()

    old_flag = _set_flag("feature_dashboard_response", True)
    old_sla = _set_flag("response_sla_minutes", 30)
    try:
        response = client.get("/api/v1/admin/dashboard/response-speed?range=last_30_days", headers=headers)
    finally:
        _set_flag("feature_dashboard_response", old_flag)
        _set_flag("response_sla_minutes", old_sla)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["timezone"] == "Asia/Shanghai"
    assert data["sample_count_total"] == 2
    assert data["sample_count_in_avg"] == 1
    assert data["sample_count_excluded_default_time"] == 1
    assert data["avg_first_response_minutes"] == 10.0
    assert data["sla_pass_rate"] == 1.0
    assert data["overdue_count"] == 0
    assert data["by_source"][0]["source"] == "dingtalk"


def test_response_speed_dashboard_requires_feature_flag(client):
    username = f"phase2_viewer_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    _create_user(username, password, roles=["viewer"])
    headers = _login(client, username, password)

    old_flag = _set_flag("feature_dashboard_response", False)
    try:
        response = client.get("/api/v1/admin/dashboard/response-speed", headers=headers)
    finally:
        _set_flag("feature_dashboard_response", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"
