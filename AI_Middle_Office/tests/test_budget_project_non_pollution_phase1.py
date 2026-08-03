import uuid
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.budget_project import BudgetProjectProfile
from app.models.project_progress import Project, ProjectStage, ProjectTask, ProjectTaskEvent
from app.models.user import User, UserRole
from app.services import business_lite_dashboard


def _set_flag(name: str, value: bool) -> bool:
    previous = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return previous


def _create_project_manager() -> tuple[int, str, str]:
    token = uuid.uuid4().hex[:10]
    username = f"phase1_non_pollution_{token}"
    password = "secret123"
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role="user",
            role_version=1,
            quota=20,
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role="project_manager", created_by=None, note="phase1 test"))
        db.commit()
        return user.id, username, password
    finally:
        db.close()


def _login(client, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _project(*, user_id: int, token: str, suffix: str, now: datetime) -> Project:
    return Project(
        project_code=f"P1NP-{token}-{suffix}"[:64],
        name=f"{token}-{suffix}",
        status="active",
        risk_level="normal",
        progress_percent=0,
        project_manager_id=user_id,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )


def test_budget_workspace_is_hidden_from_project_progress_list_detail_and_dashboard(client):
    user_id, username, password = _create_project_manager()
    headers = _login(client, username, password)
    token = f"phase1-non-pollution-{uuid.uuid4().hex[:8]}"
    now = datetime.now().replace(microsecond=0)

    db = SessionLocal()
    try:
        regular = _project(user_id=user_id, token=token, suffix="regular", now=now)
        budget = _project(user_id=user_id, token=token, suffix="budget", now=now)
        db.add_all([regular, budget])
        db.flush()
        budget_id = budget.id
        db.add(BudgetProjectProfile(project_id=budget.id, created_by=user_id, updated_by=user_id))
        budget_stage = ProjectStage(
            project_id=budget.id,
            stage_key="leak-check",
            stage_name="泄漏检查",
            sort_order=1,
            weight_percent=100,
            status="todo",
            owner_user_id=user_id,
        )
        db.add(budget_stage)
        db.flush()
        db.add(
            ProjectTask(
                project_id=budget.id,
                stage_id=budget_stage.id,
                title=f"{token}-budget-task",
                owner_user_id=user_id,
                status="blocked",
                priority="normal",
                created_by=user_id,
            )
        )
        db.commit()
    finally:
        db.close()

    old_project_flag = _set_flag("feature_project_progress", True)
    old_dashboard_flag = _set_flag("feature_dashboard_project", True)
    try:
        listed = client.get(
            "/api/v1/admin/projects",
            params={"keyword": token, "page_size": 100},
            headers=headers,
        )
        detail = client.get(f"/api/v1/admin/projects/{budget_id}", headers=headers)
        my_tasks = client.get(
            "/api/v1/admin/project-tasks/my",
            params={"keyword": token, "page_size": 100},
            headers=headers,
        )
        dashboard = client.get("/api/v1/admin/dashboard/projects", headers=headers)
    finally:
        _set_flag("feature_project_progress", old_project_flag)
        _set_flag("feature_dashboard_project", old_dashboard_flag)

    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()["data"]}
    assert f"{token}-regular" in names
    assert f"{token}-budget" not in names
    assert detail.status_code == 404
    assert detail.json()["detail"] == "PROJECT_NOT_FOUND"
    assert my_tasks.status_code == 200
    assert my_tasks.json()["data"] == []
    assert dashboard.status_code == 200
    manager_row = next(
        item
        for item in dashboard.json()["data"]["by_project_manager"]
        if item["project_manager_id"] == user_id
    )
    assert manager_row["project_count"] == 1


def test_business_lite_project_aggregates_remove_budget_workspace_and_its_activity():
    user_id, _, _ = _create_project_manager()
    token = uuid.uuid4().hex[:8]
    now = datetime(2045, 5, 20, 12, 0, 0, tzinfo=business_lite_dashboard.CN_TZ)
    db_now = now.replace(tzinfo=None)

    db = SessionLocal()
    try:
        project = _project(user_id=user_id, token=token, suffix="aggregate", now=db_now)
        project.risk_level = "blocked"
        db.add(project)
        db.flush()
        stage = ProjectStage(
            project_id=project.id,
            stage_key="phase1",
            stage_name="第一阶段",
            sort_order=1,
            weight_percent=100,
            status="todo",
            owner_user_id=user_id,
        )
        db.add(stage)
        db.flush()
        task = ProjectTask(
            project_id=project.id,
            stage_id=stage.id,
            title="预算项目不应进入项目进度聚合",
            evidence_requirement="需成果证据",
            evidence_policy="complete_required",
            is_key_node=True,
            owner_user_id=user_id,
            status="blocked",
            priority="high",
            due_at=db_now - timedelta(days=1),
            created_by=user_id,
            created_at=db_now - timedelta(days=2),
            updated_at=db_now - timedelta(hours=1),
        )
        db.add(task)
        db.flush()
        db.add(
            ProjectTaskEvent(
                project_id=project.id,
                stage_id=stage.id,
                task_id=task.id,
                event_type="task_completed_bypass_gate",
                actor_user_id=user_id,
                created_at=db_now - timedelta(hours=1),
            )
        )
        db.flush()

        before = business_lite_dashboard._build_project_section(
            db,
            start=now - timedelta(days=3),
            end=now,
            now=now,
        )
        db.add(BudgetProjectProfile(project_id=project.id, created_by=user_id, updated_by=user_id))
        db.flush()
        after = business_lite_dashboard._build_project_section(
            db,
            start=now - timedelta(days=3),
            end=now,
            now=now,
        )

        expected_decrements = {
            "project_count": 1,
            "active_project_count": 1,
            "task_count": 1,
            "open_task_count": 1,
            "blocked_task_count": 1,
            "overdue_task_count": 1,
            "missing_evidence_task_count": 1,
            "complete_required_task_count": 1,
            "bypass_gate_event_count": 1,
            "hard_gate_bypassed_missing_evidence_count": 1,
        }
        for key, decrement in expected_decrements.items():
            assert before[key] - after[key] == decrement, key
        assert sum(item["bypass_gate_event_count"] for item in before["daily_trend"]) - sum(
            item["bypass_gate_event_count"] for item in after["daily_trend"]
        ) == 1
    finally:
        db.rollback()
        db.close()
