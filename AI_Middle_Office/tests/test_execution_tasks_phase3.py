import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.execution_task import ExecutionTask, ExecutionTaskEvent
from app.models.user import User


def _set_flag(name: str, value: bool) -> bool:
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(username: str, password: str, *, role: str = "user", quota: int = 20) -> User:
    db = SessionLocal()
    try:
        user = User(username=username, hashed_password=get_password_hash(password), role=role, quota=quota)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, username: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_headers(client, *, role: str = "admin") -> tuple[User, dict]:
    username = f"phase3_{role}_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    user = _create_user(username, password, role=role)
    return user, _login(client, username, password)


def test_execution_tasks_require_feature_flag(client):
    _, headers = _create_headers(client, role="admin")
    old_flag = _set_flag("feature_execution", False)
    try:
        response = client.get("/api/v1/execution-tasks", headers=headers)
    finally:
        _set_flag("feature_execution", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_admin_creates_lists_and_cancels_execution_task(client):
    admin, admin_headers = _create_headers(client, role="admin")
    assignee, _ = _create_headers(client, role="user")
    old_flag = _set_flag("feature_execution", True)
    try:
        create_response = client.post(
            "/api/v1/execution-tasks",
            headers=admin_headers,
            json={
                "title": "跟进厨房翻新报价",
                "source": "manual",
                "assignee_id": assignee.id,
                "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "notes": "确认客户是否接受方案",
            },
        )
        assert create_response.status_code == 200
        task = create_response.json()["data"]
        task_id = task["id"]
        assert task["status"] == "pending"
        assert task["assignee_id"] == assignee.id
        assert task["is_overdue"] is False

        list_response = client.get("/api/v1/execution-tasks?status=pending", headers=admin_headers)
        assert list_response.status_code == 200
        assert any(item["id"] == task_id for item in list_response.json()["data"])

        cancel_response = client.post(
            f"/api/v1/execution-tasks/{task_id}/cancel",
            headers=admin_headers,
            json={"reason": "测试取消"},
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["data"]["status"] == "cancelled"
    finally:
        _set_flag("feature_execution", old_flag)

    db = SessionLocal()
    try:
        events = db.query(ExecutionTaskEvent).filter(ExecutionTaskEvent.execution_task_id == task_id).all()
        assert [event.event_type for event in events] == ["created", "cancelled"]
        assert events[-1].reason == "测试取消"
        assert events[-1].operator_id == admin.id
    finally:
        db.close()


def test_assignee_updates_progress_but_not_assignment(client):
    _, admin_headers = _create_headers(client, role="admin")
    assignee, assignee_headers = _create_headers(client, role="manager")
    other_assignee, _ = _create_headers(client, role="user")
    old_flag = _set_flag("feature_execution", True)
    try:
        create_response = client.post(
            "/api/v1/execution-tasks",
            headers=admin_headers,
            json={
                "title": "确认报价材料",
                "source": "quote",
                "source_ref_id": "quote-001",
                "assignee_id": assignee.id,
                "due_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            },
        )
        task_id = create_response.json()["data"]["id"]

        forbidden = client.patch(
            f"/api/v1/execution-tasks/{task_id}",
            headers=assignee_headers,
            json={"assignee_id": other_assignee.id},
        )
        assert forbidden.status_code == 403

        progress = client.patch(
            f"/api/v1/execution-tasks/{task_id}",
            headers=assignee_headers,
            json={"status": "in_progress", "notes": "已开始处理"},
        )
        assert progress.status_code == 200
        assert progress.json()["data"]["status"] == "in_progress"

        done = client.patch(
            f"/api/v1/execution-tasks/{task_id}",
            headers=assignee_headers,
            json={"status": "done"},
        )
        assert done.status_code == 200
        data = done.json()["data"]
        assert data["status"] == "done"
        assert data["completed_at"]

        cancel_done = client.post(
            f"/api/v1/execution-tasks/{task_id}/cancel",
            headers=admin_headers,
            json={"reason": "完成后不能取消"},
        )
        assert cancel_done.status_code == 409
    finally:
        _set_flag("feature_execution", old_flag)


def test_execution_speed_dashboard_aggregates_metrics(client, monkeypatch):
    from app.services import execution_dashboard

    admin, headers = _create_headers(client, role="admin")
    old_execution_flag = _set_flag("feature_execution", True)
    old_dashboard_flag = _set_flag("feature_dashboard_execution", True)
    frozen_now = datetime(2026, 5, 19, 10, 0, 0, tzinfo=execution_dashboard.CN_TZ)
    monkeypatch.setattr(execution_dashboard, "_range_bounds", lambda range_name: (frozen_now - timedelta(days=29), frozen_now))
    try:
        db = SessionLocal()
        try:
            done_task = ExecutionTask(
                title="完成样板间复核",
                source="manual",
                assignee_id=admin.id,
                created_at=frozen_now.replace(tzinfo=None) - timedelta(hours=2),
                due_at=frozen_now.replace(tzinfo=None) + timedelta(days=1),
                status="done",
                completed_at=frozen_now.replace(tzinfo=None),
            )
            overdue_task = ExecutionTask(
                title="逾期客户回访",
                source="manual",
                assignee_id=admin.id,
                created_at=frozen_now.replace(tzinfo=None) - timedelta(hours=1),
                due_at=frozen_now.replace(tzinfo=None) - timedelta(days=1),
                status="pending",
            )
            cancelled_task = ExecutionTask(
                title="已取消现场复核",
                source="manual",
                assignee_id=admin.id,
                created_at=frozen_now.replace(tzinfo=None) - timedelta(minutes=30),
                due_at=frozen_now.replace(tzinfo=None) + timedelta(days=1),
                status="cancelled",
            )
            db.add_all([done_task, overdue_task, cancelled_task])
            db.commit()
        finally:
            db.close()

        response = client.get("/api/v1/admin/dashboard/execution-speed?range=last_30_days", headers=headers)
    finally:
        _set_flag("feature_execution", old_execution_flag)
        _set_flag("feature_dashboard_execution", old_dashboard_flag)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["task_count"] >= 2
    assert data["done_count"] >= 1
    assert data["cancelled_count"] >= 1
    assert data["overdue_count"] >= 1
    assert any(item["cancelled_count"] >= 1 for item in data["daily_trends"])
    assert data["by_assignee"][0]["username"] == admin.username
