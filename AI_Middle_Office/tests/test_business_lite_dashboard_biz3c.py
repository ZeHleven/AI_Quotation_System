import uuid
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.cost_audit import CostAccessAuditLog
from app.models.cost_item import (
    COST_SOURCE_AI_SUGGESTED,
    COST_SOURCE_MANUAL,
    COST_STATUS_ACTIVE,
    COST_STATUS_ARCHIVED,
    COST_STATUS_DRAFT,
    CostItem,
    CostRagSyncRun,
)
from app.models.project_progress import Project, ProjectStage, ProjectTask, ProjectTaskEvent, ProjectTaskEvidence
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.models.quote_preview_draft import PREVIEW_DRAFT_STATUS_EDITING, QuotePreviewDraft
from app.models.user import User, UserRole
from app.services import business_lite_dashboard


def _set_flag(name: str, value: bool) -> bool:
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(username: str, password: str, roles: list[str] | None = None, legacy_role: str = "user") -> User:
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role=legacy_role,
            role_version=1,
            quota=20,
            is_active=True,
        )
        db.add(user)
        db.flush()
        for role in roles or []:
            db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz3c test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, username: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_business_lite_rows(user: User, now: datetime) -> dict:
    quote_success_id = f"biz3c-success-{uuid.uuid4().hex[:8]}"
    quote_failed_id = f"biz3c-failed-{uuid.uuid4().hex[:8]}"
    quote_created_at = now - timedelta(days=1, minutes=20)
    quote_finished_at = quote_created_at + timedelta(minutes=3)
    confirmed_at = quote_finished_at + timedelta(minutes=8)
    stale_draft_updated_at = now - timedelta(hours=business_lite_dashboard.STALE_PREVIEW_DRAFT_HOURS + 2)
    cost_now = datetime.now().replace(microsecond=0)

    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=quote_success_id,
                username=user.username,
                status="succeeded",
                stage="completed",
                message="biz3c quote success",
                duration_ms=180_000,
                result_total_amount=1000,
                result_item_count=2,
                created_at=quote_created_at,
                updated_at=quote_finished_at,
                finished_at=quote_finished_at,
            )
        )
        db.add(
            QuoteJob(
                job_id=quote_failed_id,
                username=user.username,
                status="failed",
                stage="n8n",
                message="biz3c quote failed",
                created_at=quote_created_at + timedelta(minutes=2),
                updated_at=quote_finished_at,
                error_message="workflow failed",
            )
        )
        db.add(
            QuoteHistory(
                username=user.username,
                quote_id=f"quote-{quote_success_id}",
                quote_job_id=quote_success_id,
                created_at=confirmed_at,
                total_amount=1234.56,
                item_count=2,
                payload_json="{}",
            )
        )
        db.add(
            QuotePreviewDraft(
                quote_job_id=quote_failed_id,
                username=user.username,
                status=PREVIEW_DRAFT_STATUS_EDITING,
                draft_json="{}",
                row_count=1,
                updated_at=stale_draft_updated_at,
                created_at=stale_draft_updated_at,
            )
        )
        db.add(
            CostItem(
                category="biz3c",
                item_name=f"active-{uuid.uuid4().hex[:6]}",
                unit="项",
                price=100,
                status=COST_STATUS_ACTIVE,
                source=COST_SOURCE_MANUAL,
                created_by=user.id,
                created_at=cost_now - timedelta(days=3),
                updated_at=cost_now - timedelta(days=3),
            )
        )
        db.add(
            CostItem(
                category="biz3c",
                item_name=f"draft-{uuid.uuid4().hex[:6]}",
                unit="项",
                price=88,
                status=COST_STATUS_DRAFT,
                source=COST_SOURCE_AI_SUGGESTED,
                created_by=user.id,
                created_at=cost_now - timedelta(days=2),
                updated_at=cost_now - timedelta(days=2),
            )
        )
        db.add(
            CostItem(
                category="biz3c",
                item_name=f"archived-{uuid.uuid4().hex[:6]}",
                unit="项",
                price=50,
                status=COST_STATUS_ARCHIVED,
                source=COST_SOURCE_MANUAL,
                created_by=user.id,
                created_at=cost_now - timedelta(days=2),
                updated_at=cost_now - timedelta(days=2),
            )
        )
        db.add(
            CostRagSyncRun(
                source="cost_items.active",
                status="success",
                requested_count=1,
                synced_count=1,
                message="biz3c sync",
                triggered_by=user.id,
                triggered_by_username=user.username,
                started_at=cost_now - timedelta(minutes=10),
                finished_at=cost_now - timedelta(minutes=9),
            )
        )
        db.add(
            CostAccessAuditLog(
                action="cost_item.list",
                resource_type="cost_item",
                user_id=user.id,
                username=user.username,
                status="success",
                created_at=now - timedelta(days=1),
            )
        )
        db.flush()

        project = Project(
            project_code=f"BIZ3C-{uuid.uuid4().hex[:8]}",
            name="BIZ-3c 驾驶舱测试项目",
            status="active",
            risk_level="blocked",
            progress_percent=50,
            project_manager_id=user.id,
            created_by=user.id,
            created_at=now - timedelta(days=5),
            updated_at=now - timedelta(days=1),
        )
        db.add(project)
        db.flush()
        stage = ProjectStage(
            project_id=project.id,
            stage_key="quote",
            stage_name="报价阶段",
            sort_order=1,
            weight_percent=20,
            status="todo",
            owner_user_id=user.id,
        )
        db.add(stage)
        db.flush()
        task = ProjectTask(
            project_id=project.id,
            stage_id=stage.id,
            title="A 级节点缺证据测试",
            evidence_requirement="需上传成果截图",
            evidence_policy="complete_required",
            is_key_node=True,
            owner_user_id=user.id,
            status="blocked",
            priority="high",
            due_at=now - timedelta(days=1),
            created_by=user.id,
            created_at=now - timedelta(days=4),
            updated_at=now - timedelta(days=1),
        )
        db.add(task)
        db.flush()
        db.add(
            ProjectTaskEvent(
                project_id=project.id,
                stage_id=stage.id,
                task_id=task.id,
                event_type="task_completed_bypass_gate",
                message="测试放行",
                actor_user_id=user.id,
                created_at=now - timedelta(hours=2),
            )
        )
        db.commit()
        return {"quote_success_id": quote_success_id, "project_id": project.id, "task_id": task.id}
    finally:
        db.close()


def test_business_lite_dashboard_requires_feature_flag(client):
    password = "secret123"
    username = f"biz3c_admin_disabled_{uuid.uuid4().hex[:8]}"
    _create_user(username, password, roles=["admin"], legacy_role="admin")
    headers = _login(client, username, password)

    old_flag = _set_flag("feature_dashboard_business_lite", False)
    try:
        response = client.get("/api/v1/admin/dashboard/business-lite", headers=headers)
    finally:
        _set_flag("feature_dashboard_business_lite", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_business_lite_dashboard_allows_viewer_and_aggregates(client, monkeypatch):
    frozen_now = datetime(2042, 3, 18, 15, 0, 0)
    monkeypatch.setattr(business_lite_dashboard, "_now", lambda: frozen_now.replace(tzinfo=business_lite_dashboard.CN_TZ))
    password = "secret123"
    username = f"biz3c_viewer_{uuid.uuid4().hex[:8]}"
    user = _create_user(username, password, roles=["viewer"])
    headers = _login(client, username, password)
    _seed_business_lite_rows(user, frozen_now)

    old_flag = _set_flag("feature_dashboard_business_lite", True)
    try:
        response = client.get("/api/v1/admin/dashboard/business-lite", headers=headers)
    finally:
        _set_flag("feature_dashboard_business_lite", old_flag)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["range"] == "last_30_days"
    assert data["timezone"] == "Asia/Shanghai"
    assert data["quote"]["task_count"] == 2
    assert data["quote"]["success_count"] == 1
    assert data["quote"]["failed_count"] == 1
    assert data["quote"]["pushed_count"] == 1
    assert data["quote"]["avg_duration_ms"] == 180_000
    assert data["quote"]["stale_draft_count"] >= 1
    assert data["cost"]["active_count"] >= 1
    assert data["cost"]["draft_count"] >= 1
    assert data["cost"]["archived_count"] >= 1
    assert data["cost"]["no_cost_draft_count"] >= 1
    assert data["cost"]["audit_event_count"] >= 1
    assert data["project_progress"]["project_count"] >= 1
    assert data["project_progress"]["blocked_task_count"] >= 1
    assert data["project_progress"]["missing_evidence_task_count"] >= 1
    assert data["project_progress"]["complete_required_task_count"] >= 1
    assert data["project_progress"]["bypass_gate_event_count"] >= 1
    assert data["project_progress"]["hard_gate_bypassed_missing_evidence_count"] >= 1
    assert data["quote"]["daily_trend"]
    assert any(item["task_count"] >= 2 for item in data["quote"]["daily_trend"])
    assert data["cost"]["status_distribution"]
    assert data["cost"]["source_distribution"]
    assert data["project_progress"]["daily_trend"]
    assert any(item["bypassed_missing_evidence_count"] >= 1 for item in data["project_progress"]["daily_trend"])
    risk_keys = {item["key"] for item in data["risks"]}
    assert "quote_failed_or_timeout" in risk_keys
    assert "quote_draft_stale" in risk_keys
    assert "cost_draft_pending" in risk_keys
    assert "project_blocked_tasks" in risk_keys
    assert "project_missing_evidence" in risk_keys
    assert "hard_gate_bypassed_missing_evidence" in risk_keys
    assert {item["key"] for item in data["links"]} >= {"cost_db", "project_progress", "quote_workspace"}
    quote_link = next(item for item in data["links"] if item["key"] == "quote_workspace")
    assert quote_link["label"] == ("报价工作台" if settings.feature_unified_quotes else "新建报价")
    assert quote_link["path"] == "/quote/new"


def test_business_lite_dashboard_hard_gate_missing_risk_resolves_after_evidence(client, monkeypatch):
    frozen_now = datetime(2043, 4, 19, 15, 0, 0)
    monkeypatch.setattr(business_lite_dashboard, "_now", lambda: frozen_now.replace(tzinfo=business_lite_dashboard.CN_TZ))
    password = "secret123"
    username = f"biz3c_evidence_resolved_{uuid.uuid4().hex[:8]}"
    user = _create_user(username, password, roles=["viewer"])
    headers = _login(client, username, password)
    seeded = _seed_business_lite_rows(user, frozen_now)

    db = SessionLocal()
    try:
        task = db.query(ProjectTask).filter(ProjectTask.id == seeded["task_id"]).first()
        assert task is not None
        db.add(
            ProjectTaskEvidence(
                project_id=task.project_id,
                stage_id=task.stage_id,
                task_id=task.id,
                evidence_type="text",
                title="补充验收记录",
                description="放行后已补齐成果证据",
                status="active",
                created_by=user.id,
                created_at=frozen_now - timedelta(minutes=30),
                updated_at=frozen_now - timedelta(minutes=30),
            )
        )
        db.commit()
    finally:
        db.close()

    old_flag = _set_flag("feature_dashboard_business_lite", True)
    try:
        response = client.get("/api/v1/admin/dashboard/business-lite", headers=headers)
    finally:
        _set_flag("feature_dashboard_business_lite", old_flag)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["project_progress"]["bypass_gate_event_count"] >= 1
    assert data["project_progress"]["hard_gate_bypassed_missing_evidence_count"] == 0
    assert "hard_gate_bypassed_missing_evidence" not in {item["key"] for item in data["risks"]}


def test_business_lite_dashboard_rejects_staff(client):
    password = "secret123"
    username = f"biz3c_staff_{uuid.uuid4().hex[:8]}"
    _create_user(username, password, roles=["staff"])
    headers = _login(client, username, password)

    old_flag = _set_flag("feature_dashboard_business_lite", True)
    try:
        response = client.get("/api/v1/admin/dashboard/business-lite", headers=headers)
    finally:
        _set_flag("feature_dashboard_business_lite", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "PERMISSION_DENIED"


def test_business_lite_dashboard_degrades_failed_section(monkeypatch):
    db = SessionLocal()
    try:
        monkeypatch.setattr(
            business_lite_dashboard,
            "cost_rag_sync_status_summary",
            lambda db: (_ for _ in ()).throw(RuntimeError("rag summary failed")),
        )
        data = business_lite_dashboard.build_business_lite_dashboard(db)
    finally:
        db.close()

    assert data["cost"]["available"] is False
    assert data["environment"]["overall_status"] == "degraded"
    assert {"section": "cost", "message": "rag summary failed"} in data["section_errors"]
    assert "section_unavailable_cost" in {item["key"] for item in data["risks"]}
