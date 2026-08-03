import json
import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.file_object import FileObject
from app.models.project_progress import Project, ProjectStage, ProjectTask, ProjectTaskEvent
from app.models.user import User, UserRole
from app.services.project_progress import (
    QISHENG_EPC_A_LEVEL_GATE_NODE_KEYS,
    activate_project_task_hard_gates,
    backfill_project_task_evidence_fields,
    qisheng_epc_template_tasks,
)


def _set_flag(name: str, value: bool) -> bool:
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(username: str, password: str, *, roles: list[str] | None = None, legacy_role: str = "user") -> User:
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


def _user_with_headers(client, role: str) -> tuple[User, dict]:
    password = "secret123"
    username = f"biz3a_{role}_{uuid.uuid4().hex[:8]}"
    roles = [role] if role != "legacy_manager" else []
    legacy_role = "manager" if role == "legacy_manager" else "user"
    user = _create_user(username, password, roles=roles, legacy_role=legacy_role)
    return user, _login(client, username, password)


def test_project_progress_requires_feature_flag(client):
    _, headers = _user_with_headers(client, "project_manager")
    old_flag = _set_flag("feature_project_progress", False)
    try:
        response = client.get("/api/v1/admin/projects", headers=headers)
    finally:
        _set_flag("feature_project_progress", old_flag)

    assert response.status_code == 404
    assert response.json()["detail"] == "NOT_FOUND"


def test_project_lifecycle_uses_2580_progress_and_manager_completion(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    owner, owner_headers = _user_with_headers(client, "project_member")
    old_project_flag = _set_flag("feature_project_progress", True)
    old_dashboard_flag = _set_flag("feature_dashboard_project", True)
    try:
        project_response = client.post(
            "/api/v1/admin/projects",
            headers=manager_headers,
            json={
                "name": "办公楼装饰进度测试",
                "client_name": "联昇集团",
                "project_manager_id": manager.id,
                "owner_department": "工程部",
                "planned_start_at": datetime.now(timezone.utc).isoformat(),
                "planned_finish_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            },
        )
        assert project_response.status_code == 200
        project = project_response.json()["data"]
        project_id = project["id"]
        assert project["progress_percent"] == 0
        assert len(project["stages"]) == 8
        assert sum(stage["weight_percent"] for stage in project["stages"]) == 100

        quote_stage = next(stage for stage in project["stages"] if stage["stage_key"] == "quote")
        task_response = client.post(
            f"/api/v1/admin/projects/{project_id}/tasks",
            headers=manager_headers,
            json={
                "stage_id": quote_stage["id"],
                "title": "完成 AI 报价复核",
                "owner_user_id": owner.id,
                "priority": "high",
                "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        assert task_response.status_code == 200
        task_data = task_response.json()["data"]
        task_id = task_data["id"]
        assert task_data["evidence_requirement"] is None
        assert task_data["evidence_policy"] == "none"
        assert task_data["is_key_node"] is False

        started = client.post(f"/api/v1/admin/project-tasks/{task_id}/start", headers=owner_headers)
        assert started.status_code == 200
        assert started.json()["data"]["status"] == "started"
        assert started.json()["data"]["progress_percent"] == 25

        progressing = client.post(f"/api/v1/admin/project-tasks/{task_id}/progress", headers=owner_headers)
        assert progressing.status_code == 200
        assert progressing.json()["data"]["progress_percent"] == 50

        submitted = client.post(f"/api/v1/admin/project-tasks/{task_id}/submit", headers=owner_headers)
        assert submitted.status_code == 200
        assert submitted.json()["data"]["progress_percent"] == 80

        owner_complete = client.post(f"/api/v1/admin/project-tasks/{task_id}/complete", headers=owner_headers)
        assert owner_complete.status_code == 403
        assert owner_complete.json()["detail"] == "PROJECT_MANAGER_CONFIRM_REQUIRED"

        completed = client.post(f"/api/v1/admin/project-tasks/{task_id}/complete", headers=manager_headers)
        assert completed.status_code == 200
        assert completed.json()["data"]["progress_percent"] == 100

        detail = client.get(f"/api/v1/admin/projects/{project_id}", headers=manager_headers)
        assert detail.status_code == 200
        project_detail = detail.json()["data"]
        assert project_detail["progress_percent"] == 15
        assert project_detail["stages"][1]["progress_percent"] == 100

        dashboard = client.get("/api/v1/admin/dashboard/projects", headers=manager_headers)
        assert dashboard.status_code == 200
        assert dashboard.json()["data"]["project_count"] >= 1
    finally:
        _set_flag("feature_project_progress", old_project_flag)
        _set_flag("feature_dashboard_project", old_dashboard_flag)

    db = SessionLocal()
    try:
        events = db.query(ProjectTaskEvent).filter(ProjectTaskEvent.project_id == project_id).all()
        event_types = [event.event_type for event in events]
        assert "project_created" in event_types
        assert "task_created" in event_types
        assert "task_completed" in event_types
    finally:
        db.close()


def test_single_user_trial_template_creates_self_owned_project_tasks(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        response = client.post(
            "/api/v1/admin/projects/trial-template",
            headers=manager_headers,
            json={
                "name": "单人试运行模板项目",
                "client_name": "旗胜内测客户",
                "owner_department": "工程部",
                "planned_start_at": datetime.now(timezone.utc).isoformat(),
                "planned_finish_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            },
        )
        assert response.status_code == 200
        project = response.json()["data"]
        assert project["project_manager_id"] == manager.id
        assert project["template"]["template_key"] == "single_user_fitout_v1"
        assert len(project["stages"]) == 8
        assert len(project["tasks"]) == 18
        assert {task["owner_user_id"] for task in project["tasks"]} == {manager.id}
        assert {task["source_id"] for task in project["tasks"]} == {"single_user_fitout_v1"}
        assert {task["evidence_policy"] for task in project["tasks"]} == {"none"}
        assert all(task["evidence_requirement"] is None for task in project["tasks"])
        assert all(task["is_key_node"] is False for task in project["tasks"])
        assert all(stage["owner_user_id"] == manager.id for stage in project["stages"])
        assert project["progress_percent"] == 0

        my_tasks = client.get("/api/v1/admin/project-tasks/my?page_size=100", headers=manager_headers)
        assert my_tasks.status_code == 200
        assert sum(1 for task in my_tasks.json()["data"] if task["project_name"] == "单人试运行模板项目") == 18
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_qisheng_epc_template_creates_stage_weighted_process_tasks(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        compact_response = client.post(
            "/api/v1/admin/projects/epc-template",
            headers=manager_headers,
            json={
                "name": "旗胜 EPC 精简流程项目",
                "client_name": "旗胜内测客户",
                "owner_department": "项目管理部",
                "planned_start_at": datetime.now(timezone.utc).isoformat(),
                "planned_finish_at": (datetime.now(timezone.utc) + timedelta(days=180)).isoformat(),
                "mode": "compact",
            },
        )
        assert compact_response.status_code == 200
        compact_project = compact_response.json()["data"]
        compact_count = len(qisheng_epc_template_tasks("compact"))
        assert compact_project["template"]["template_key"] == "qisheng_epc_compact_v1"
        assert compact_project["template"]["task_count"] == compact_count
        assert compact_project["project_manager_id"] == manager.id
        assert len(compact_project["stages"]) == 5
        assert sum(stage["weight_percent"] for stage in compact_project["stages"]) == 100
        assert [stage["stage_key"] for stage in compact_project["stages"]] == [
            "market_development",
            "design_solution",
            "tender_procurement",
            "production_delivery",
            "after_sales",
        ]
        assert len(compact_project["tasks"]) == compact_count
        assert {task["owner_user_id"] for task in compact_project["tasks"]} == {manager.id}
        assert {task["source_id"] for task in compact_project["tasks"]} == {"qisheng_epc_compact_v1"}
        gated_titles = {task["title"] for task in compact_project["tasks"] if task["evidence_policy"] == "complete_required"}
        assert gated_titles == {title for _, title in QISHENG_EPC_A_LEVEL_GATE_NODE_KEYS}
        assert sum(1 for task in compact_project["tasks"] if task["evidence_policy"] == "complete_required") == 4
        assert sum(1 for task in compact_project["tasks"] if task["evidence_policy"] == "soft_reminder") == compact_count - 4
        assert all(task["evidence_requirement"] for task in compact_project["tasks"])
        assert all(task["is_key_node"] is True for task in compact_project["tasks"])
        first_task = compact_project["tasks"][0]
        assert first_task["epc_meta"]["standard"]
        assert first_task["epc_standard"] == first_task["epc_meta"]["standard"]
        assert first_task["epc_deliverable"]
        assert first_task["evidence_requirement"] == first_task["epc_deliverable"]
        assert first_task["owner_role"]

        my_tasks = client.get("/api/v1/admin/project-tasks/my?page_size=100", headers=manager_headers)
        assert my_tasks.status_code == 200
        assert sum(1 for task in my_tasks.json()["data"] if task["project_name"] == "旗胜 EPC 精简流程项目") == compact_count

        full_response = client.post(
            "/api/v1/admin/projects/epc-template",
            headers=manager_headers,
            json={"name": "旗胜 EPC 完整流程项目", "mode": "full"},
        )
        assert full_response.status_code == 200
        full_project = full_response.json()["data"]
        assert full_project["template"]["template_key"] == "qisheng_epc_full_v1"
        assert full_project["template"]["task_count"] == len(qisheng_epc_template_tasks("full")) == 82
        assert len(full_project["tasks"]) == 82
        assert sum(1 for task in full_project["tasks"] if task["is_key_node"]) == compact_count
        assert any(task["is_key_node"] is False for task in full_project["tasks"])
        assert sum(1 for task in full_project["tasks"] if task["evidence_policy"] == "complete_required") == 4
        assert sum(1 for task in full_project["tasks"] if task["evidence_policy"] == "soft_reminder") == 78

        invalid_response = client.post(
            "/api/v1/admin/projects/epc-template",
            headers=manager_headers,
            json={"name": "错误模式", "mode": "unknown"},
        )
        assert invalid_response.status_code == 422
        assert invalid_response.json()["detail"] == "INVALID_EPC_TEMPLATE_MODE"
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_task_evidence_fields_backfill_keeps_soft_policy(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        response = client.post(
            "/api/v1/admin/projects/epc-template",
            headers=manager_headers,
            json={"name": "BIZ-3b-3-1 回填验收项目", "mode": "full"},
        )
        assert response.status_code == 200
        project = response.json()["data"]
        compact_count = len(qisheng_epc_template_tasks("compact"))

        db = SessionLocal()
        try:
            tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project["id"]).all()
            for task in tasks:
                task.evidence_requirement = None
                task.evidence_policy = "none"
                task.is_key_node = False
            db.commit()

            dry_run = backfill_project_task_evidence_fields(db, apply=False, project_id=project["id"])
            assert dry_run["updated_task_count"] == 82
            assert dry_run["complete_required_count"] == 0
            assert dry_run["key_node_count"] == compact_count
            assert dry_run["soft_reminder_count"] == 82

            summary = backfill_project_task_evidence_fields(db, apply=True, project_id=project["id"])
            assert summary["updated_task_count"] == 82
            assert summary["complete_required_count"] == 0
            assert summary["requirement_parse_missing_count"] == 0
            db.expire_all()
            refreshed = db.query(ProjectTask).filter(ProjectTask.project_id == project["id"]).all()
            assert sum(1 for task in refreshed if task.is_key_node) == compact_count
            assert {task.evidence_policy for task in refreshed} == {"soft_reminder"}
            assert all(task.evidence_requirement for task in refreshed)
        finally:
            db.close()
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_task_hard_gate_activation_upgrades_a_level_nodes(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        response = client.post(
            "/api/v1/admin/projects/epc-template",
            headers=manager_headers,
            json={"name": "BIZ-3b-3-2 门禁激活项目", "mode": "full"},
        )
        assert response.status_code == 200
        project = response.json()["data"]

        db = SessionLocal()
        try:
            tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project["id"]).all()
            for task in tasks:
                task.evidence_policy = "soft_reminder" if task.evidence_requirement else "none"
            db.commit()

            dry_run = activate_project_task_hard_gates(db, apply=False, project_id=project["id"])
            assert dry_run["a_level_candidate_count"] == 4
            assert dry_run["updated_task_count"] == 4
            assert dry_run["complete_required_count"] == 4

            summary = activate_project_task_hard_gates(db, apply=True, project_id=project["id"])
            assert summary["updated_task_count"] == 4
            db.expire_all()
            refreshed = db.query(ProjectTask).filter(ProjectTask.project_id == project["id"]).all()
            gated = {task.title for task in refreshed if task.evidence_policy == "complete_required"}
            assert gated == {title for _, title in QISHENG_EPC_A_LEVEL_GATE_NODE_KEYS}
        finally:
            db.close()
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_risk_blocked_and_delayed_are_calculated(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    owner, owner_headers = _user_with_headers(client, "project_member")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        project_response = client.post(
            "/api/v1/admin/projects",
            headers=manager_headers,
            json={
                "name": "风险状态测试项目",
                "project_manager_id": manager.id,
            },
        )
        project = project_response.json()["data"]
        project_id = project["id"]
        stage_id = project["stages"][0]["id"]

        blocked_task = client.post(
            f"/api/v1/admin/projects/{project_id}/tasks",
            headers=manager_headers,
            json={
                "stage_id": stage_id,
                "title": "等待客户确认图纸",
                "owner_user_id": owner.id,
                "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        ).json()["data"]
        block_response = client.post(
            f"/api/v1/admin/project-tasks/{blocked_task['id']}/block",
            headers=owner_headers,
            json={"reason": "客户暂未确认", "next_action": "明天上午继续跟进"},
        )
        assert block_response.status_code == 200
        blocked_detail = client.get(f"/api/v1/admin/projects/{project_id}", headers=manager_headers).json()["data"]
        assert blocked_detail["risk_level"] == "blocked"

        unblock_response = client.post(
            f"/api/v1/admin/project-tasks/{blocked_task['id']}/unblock",
            headers=owner_headers,
            json={"resolution": "客户已确认图纸，继续推进"},
        )
        assert unblock_response.status_code == 200

        overdue_task = client.post(
            f"/api/v1/admin/projects/{project_id}/tasks",
            headers=manager_headers,
            json={
                "stage_id": stage_id,
                "title": "逾期现场确认",
                "owner_user_id": owner.id,
                "due_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            },
        )
        assert overdue_task.status_code == 200
        delayed_detail = client.get(f"/api/v1/admin/projects/{project_id}", headers=manager_headers).json()["data"]
        assert delayed_detail["risk_level"] == "delayed"
        assert delayed_detail["overdue_task_count"] >= 1
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_task_rollback_and_block_resolution_are_audited(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    owner, owner_headers = _user_with_headers(client, "project_member")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        project = client.post(
            "/api/v1/admin/projects",
            headers=manager_headers,
            json={"name": "回退与解除阻塞验收项目", "project_manager_id": manager.id},
        ).json()["data"]
        task = client.post(
            f"/api/v1/admin/projects/{project['id']}/tasks",
            headers=manager_headers,
            json={
                "stage_id": project["stages"][0]["id"],
                "title": "确认现场条件",
                "owner_user_id": owner.id,
            },
        ).json()["data"]

        task_id = task["id"]
        assert client.post(f"/api/v1/admin/project-tasks/{task_id}/start", headers=owner_headers).status_code == 200
        progressed = client.post(f"/api/v1/admin/project-tasks/{task_id}/progress", headers=owner_headers)
        assert progressed.status_code == 200
        assert progressed.json()["data"]["progress_percent"] == 50

        rollback = client.post(
            f"/api/v1/admin/project-tasks/{task_id}/rollback",
            headers=owner_headers,
            json={"target_status": "started", "reason": "现场条件重新核对"},
        )
        assert rollback.status_code == 200
        assert rollback.json()["data"]["status"] == "started"
        assert rollback.json()["data"]["progress_percent"] == 25

        block = client.post(
            f"/api/v1/admin/project-tasks/{task_id}/block",
            headers=owner_headers,
            json={"reason": "客户暂未开放现场", "next_action": "等客户明天确认进场时间"},
        )
        assert block.status_code == 200
        assert block.json()["data"]["status"] == "blocked"
        assert block.json()["data"]["blocked_reason"] == "客户暂未开放现场"

        unblock = client.post(
            f"/api/v1/admin/project-tasks/{task_id}/unblock",
            headers=owner_headers,
            json={"resolution": "客户已开放现场，明早进场复核", "next_action": "明早进场复核"},
        )
        assert unblock.status_code == 200
        assert unblock.json()["data"]["status"] == "started"
        assert unblock.json()["data"]["blocked_reason"] is None

        events = client.get(f"/api/v1/admin/projects/{project['id']}/events", headers=manager_headers)
        assert events.status_code == 200
        event_rows = events.json()["data"]
        rollback_event = next(event for event in event_rows if event["event_type"] == "task_rolled_back")
        unblock_event = next(event for event in event_rows if event["event_type"] == "task_unblocked")
        assert rollback_event["actor_username"] == owner.username
        assert "现场条件重新核对" in rollback_event["message"]
        assert unblock_event["actor_username"] == owner.username
        assert "客户已开放现场" in unblock_event["message"]
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_task_evidences_support_text_link_file_and_soft_delete(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        project = client.post(
            "/api/v1/admin/projects/epc-template",
            headers=manager_headers,
            json={"name": "成果证据验收项目", "mode": "compact"},
        ).json()["data"]
        task = project["tasks"][0]
        task_id = task["id"]
        assert task["evidence_count"] == 0
        assert task["evidence_requirement"]
        original_requirement = task["evidence_requirement"]

        db = SessionLocal()
        try:
            db_task = db.query(ProjectTask).filter(ProjectTask.id == task_id).first()
            db_task.description = "description edited after template creation"
            db.commit()
        finally:
            db.close()
        broken_description_detail = client.get(f"/api/v1/admin/projects/{project['id']}", headers=manager_headers).json()["data"]
        broken_description_task = next(item for item in broken_description_detail["tasks"] if item["id"] == task_id)
        assert broken_description_task["evidence_requirement"] == original_requirement

        text_response = client.post(
            f"/api/v1/admin/project-tasks/{task_id}/evidences",
            headers=manager_headers,
            json={
                "evidence_type": "text",
                "title": "客户口头确认记录",
                "description": "客户已电话确认本节点资料，纸质签字后补。",
            },
        )
        assert text_response.status_code == 200
        text_evidence = text_response.json()["data"]
        assert text_evidence["evidence_type"] == "text"
        assert text_evidence["requirement_snapshot"] == task["evidence_requirement"]

        invalid_link = client.post(
            f"/api/v1/admin/project-tasks/{task_id}/evidences",
            headers=manager_headers,
            json={"evidence_type": "link", "title": "错误链接", "external_url": "not-a-url"},
        )
        assert invalid_link.status_code == 422
        assert invalid_link.json()["detail"] == "INVALID_PROJECT_TASK_EVIDENCE_LINK"

        link_response = client.post(
            f"/api/v1/admin/project-tasks/{task_id}/evidences",
            headers=manager_headers,
            json={
                "evidence_type": "link",
                "title": "钉钉文档链接",
                "external_url": "https://example.com/project-evidence",
                "external_provider": "dingtalk",
            },
        )
        assert link_response.status_code == 200
        assert link_response.json()["data"]["external_provider"] == "dingtalk"

        db = SessionLocal()
        try:
            file_obj = FileObject(
                file_id=f"file-{uuid.uuid4().hex[:8]}",
                username=manager.username,
                purpose="project_task_evidence",
                bucket="test-quote-files",
                object_name="project/task/evidence.pdf",
                original_filename="现场勘察.pdf",
                content_type="application/pdf",
                size_bytes=128,
            )
            db.add(file_obj)
            db.commit()
            file_id = file_obj.file_id
        finally:
            db.close()

        file_response = client.post(
            f"/api/v1/admin/project-tasks/{task_id}/evidences",
            headers=manager_headers,
            json={"evidence_type": "file", "title": "现场勘察文件", "file_object_id": file_id},
        )
        assert file_response.status_code == 200
        assert file_response.json()["data"]["file_original_filename"] == "现场勘察.pdf"

        list_response = client.get(f"/api/v1/admin/project-tasks/{task_id}/evidences", headers=manager_headers)
        assert list_response.status_code == 200
        evidence_list = list_response.json()["data"]
        assert evidence_list["evidence_count"] == 3
        assert evidence_list["has_file"] is True
        assert evidence_list["has_link"] is True
        assert evidence_list["has_text"] is True

        detail = client.get(f"/api/v1/admin/projects/{project['id']}", headers=manager_headers).json()["data"]
        refreshed_task = next(item for item in detail["tasks"] if item["id"] == task_id)
        assert refreshed_task["evidence_count"] == 3
        assert refreshed_task["has_evidence"] is True
        assert detail["evidence_summary"]["required_task_count"] == len(project["tasks"])
        assert detail["evidence_summary"]["evidenced_task_count"] == 1
        assert detail["evidence_summary"]["missing_evidence_task_count"] == len(project["tasks"]) - 1

        remove_response = client.request(
            "DELETE",
            f"/api/v1/admin/project-task-evidences/{text_evidence['id']}",
            headers=manager_headers,
            json={"reason": "文字说明已由正式文件替代"},
        )
        assert remove_response.status_code == 200
        assert remove_response.json()["data"]["status"] == "removed"

        after_remove = client.get(f"/api/v1/admin/project-tasks/{task_id}/evidences", headers=manager_headers).json()["data"]
        assert after_remove["evidence_count"] == 2
        assert all(item["id"] != text_evidence["id"] for item in after_remove["items"])
        detail_after_remove = client.get(f"/api/v1/admin/projects/{project['id']}", headers=manager_headers).json()["data"]
        assert detail_after_remove["evidence_summary"]["evidenced_task_count"] == 1

        events = client.get(f"/api/v1/admin/projects/{project['id']}/events", headers=manager_headers)
        event_types = [event["event_type"] for event in events.json()["data"]]
        assert "task_evidence_added" in event_types
        assert "task_evidence_removed" in event_types
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_task_missing_evidence_submit_and_complete_are_soft_audited(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        project = client.post(
            "/api/v1/admin/projects/epc-template",
            headers=manager_headers,
            json={"name": "无证据软提醒验收项目", "mode": "compact"},
        ).json()["data"]
        task = project["tasks"][0]
        assert task["evidence_requirement"]
        assert task["evidence_count"] == 0

        submitted = client.post(f"/api/v1/admin/project-tasks/{task['id']}/submit", headers=manager_headers)
        assert submitted.status_code == 200
        assert submitted.json()["data"]["status"] == "submitted"

        complete_without_reason = client.post(f"/api/v1/admin/project-tasks/{task['id']}/complete", headers=manager_headers)
        assert complete_without_reason.status_code == 422
        assert complete_without_reason.json()["detail"] == "EVIDENCE_CONFIRM_REASON_REQUIRED"

        completed = client.post(
            f"/api/v1/admin/project-tasks/{task['id']}/complete",
            headers=manager_headers,
            json={"confirm_without_evidence_reason": "纸质资料已线下留存，稍后补传"},
        )
        assert completed.status_code == 200
        assert completed.json()["data"]["status"] == "done"
        detail = client.get(f"/api/v1/admin/projects/{project['id']}", headers=manager_headers).json()["data"]
        assert detail["evidence_summary"]["done_without_evidence_task_count"] == 1

        events = client.get(f"/api/v1/admin/projects/{project['id']}/events", headers=manager_headers)
        event_rows = events.json()["data"]
        submit_event = next(event for event in event_rows if event["event_type"] == "task_submitted_without_evidence")
        complete_event = next(event for event in event_rows if event["event_type"] == "task_completed_without_evidence")
        assert "无成果证据提交确认" in submit_event["message"]
        assert "纸质资料已线下留存" in complete_event["message"]
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_task_complete_required_gate_blocks_member_and_audits_manager_bypass(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    owner, owner_headers = _user_with_headers(client, "project_member")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        project = client.post(
            "/api/v1/admin/projects",
            headers=manager_headers,
            json={"name": "硬门禁放行验收项目", "project_manager_id": manager.id},
        ).json()["data"]
        stage_id = project["stages"][0]["id"]
        task = client.post(
            f"/api/v1/admin/projects/{project['id']}/tasks",
            headers=manager_headers,
            json={
                "stage_id": stage_id,
                "title": "竣工精装验收",
                "owner_user_id": owner.id,
                "priority": "high",
            },
        ).json()["data"]

        db = SessionLocal()
        try:
            task_row = db.query(ProjectTask).filter(ProjectTask.id == task["id"]).one()
            task_row.evidence_requirement = "精装验收表甲方签字确认及备案"
            task_row.evidence_policy = "complete_required"
            task_row.is_key_node = True
            db.commit()
        finally:
            db.close()

        submitted = client.post(f"/api/v1/admin/project-tasks/{task['id']}/submit", headers=owner_headers)
        assert submitted.status_code == 200
        assert submitted.json()["data"]["status"] == "submitted"

        member_complete = client.post(
            f"/api/v1/admin/project-tasks/{task['id']}/complete",
            headers=owner_headers,
            json={"confirm_without_evidence_reason": "我确认线下已有纸质资料"},
        )
        assert member_complete.status_code == 409
        assert member_complete.json()["detail"] == "EVIDENCE_HARD_GATE_BLOCKED"

        manager_without_reason = client.post(f"/api/v1/admin/project-tasks/{task['id']}/complete", headers=manager_headers)
        assert manager_without_reason.status_code == 422
        assert manager_without_reason.json()["detail"] == "EVIDENCE_BYPASS_REASON_REQUIRED"

        manager_with_soft_reason = client.post(
            f"/api/v1/admin/project-tasks/{task['id']}/complete",
            headers=manager_headers,
            json={"confirm_without_evidence_reason": "纸质资料已线下留存，稍后补传"},
        )
        assert manager_with_soft_reason.status_code == 422
        assert manager_with_soft_reason.json()["detail"] == "EVIDENCE_BYPASS_REASON_REQUIRED"

        completed = client.post(
            f"/api/v1/admin/project-tasks/{task['id']}/complete",
            headers=manager_headers,
            json={"bypass_reason": "甲方签字件已线下确认，扫描件次日补传"},
        )
        assert completed.status_code == 200
        assert completed.json()["data"]["status"] == "done"

        events = client.get(f"/api/v1/admin/projects/{project['id']}/events", headers=manager_headers).json()["data"]
        event_types = [event["event_type"] for event in events]
        assert "task_completed_bypass_gate" in event_types
        assert "task_completed_without_evidence" not in event_types
        bypass_event = next(event for event in events if event["event_type"] == "task_completed_bypass_gate")
        payload = json.loads(bypass_event["payload_json"])
        assert payload["evidence_policy"] == "complete_required"
        assert payload["evidence_requirement"] == "精装验收表甲方签字确认及备案"
        assert payload["evidence_count_at_decision"] == 0
        assert payload["bypass_reason"] == "甲方签字件已线下确认，扫描件次日补传"
        assert payload["decided_by_user_id"] == manager.id
        assert payload["task_status_before"] == "submitted"
        assert payload["task_status_after"] == "done"
    finally:
        _set_flag("feature_project_progress", old_flag)


def test_project_member_only_sees_related_project_tasks(client):
    manager, manager_headers = _user_with_headers(client, "project_manager")
    owner, owner_headers = _user_with_headers(client, "project_member")
    stranger, stranger_headers = _user_with_headers(client, "project_member")
    old_flag = _set_flag("feature_project_progress", True)
    try:
        project = client.post(
            "/api/v1/admin/projects",
            headers=manager_headers,
            json={"name": "权限过滤测试项目", "project_manager_id": manager.id},
        ).json()["data"]
        stage_id = project["stages"][0]["id"]
        client.post(
            f"/api/v1/admin/projects/{project['id']}/tasks",
            headers=manager_headers,
            json={"stage_id": stage_id, "title": "只分配给 owner", "owner_user_id": owner.id},
        )

        owner_projects = client.get("/api/v1/admin/projects", headers=owner_headers)
        assert owner_projects.status_code == 200
        assert any(item["id"] == project["id"] for item in owner_projects.json()["data"])

        stranger_projects = client.get("/api/v1/admin/projects", headers=stranger_headers)
        assert stranger_projects.status_code == 200
        assert all(item["id"] != project["id"] for item in stranger_projects.json()["data"])

        my_tasks = client.get("/api/v1/admin/project-tasks/my", headers=owner_headers)
        assert my_tasks.status_code == 200
        assert any(item["project_name"] == "权限过滤测试项目" for item in my_tasks.json()["data"])
        assert stranger.id != owner.id
    finally:
        _set_flag("feature_project_progress", old_flag)
