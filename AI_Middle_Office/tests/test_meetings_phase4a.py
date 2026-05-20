import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.execution_task import ExecutionTask
from app.models.meeting import MeetingNote, TaskDraft
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
    username = f"phase4a_{role}_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    user = _create_user(username, password, role=role)
    return user, _login(client, username, password)


def test_meetings_require_feature_flag(client):
    _, headers = _create_headers(client, role="user")
    old_flag = _set_flag("feature_meeting_ai", False)
    try:
        response = client.post("/api/v1/meetings", headers=headers, json={"content": "请张三明天负责复核现场尺寸"})
    finally:
        _set_flag("feature_meeting_ai", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_meeting_note_extracts_draft_and_confirm_writes_execution_task_when_execution_flag_closed(client):
    creator, creator_headers = _create_headers(client, role="manager")
    assignee, _ = _create_headers(client, role="user")
    old_meeting_flag = _set_flag("feature_meeting_ai", True)
    old_execution_flag = _set_flag("feature_execution", False)
    try:
        response = client.post(
            "/api/v1/meetings",
            headers=creator_headers,
            json={"content": f"会议决定：请 {assignee.username} 明天负责复核厨房尺寸；下周再讨论样板。"},
        )
        assert response.status_code == 200
        meeting = response.json()["data"]
        assert meeting["status"] == "draft"
        assert meeting["ai_status"] == "extracted"
        assert len(meeting["drafts"]) == 1
        draft = meeting["drafts"][0]
        assert draft["source_sentence"]
        assert draft["suggested_assignee_id"] == assignee.id

        confirm = client.post(
            f"/api/v1/meetings/{meeting['id']}/confirm-tasks",
            headers=creator_headers,
            json={
                "drafts": [
                    {
                        "draft_id": draft["id"],
                        "action": "accept",
                        "title": "复核厨房尺寸",
                        "assignee_id": assignee.id,
                        "notes": "确认后进入执行任务",
                    }
                ]
            },
        )
        assert confirm.status_code == 200
        data = confirm.json()["data"]
        assert data["meeting"]["status"] == "confirmed"
        assert data["tasks"][0]["source"] == "meeting"
        assert data["tasks"][0]["source_ref_id"] == str(meeting["id"])
        task_id = data["tasks"][0]["id"]

        db = SessionLocal()
        try:
            task = db.query(ExecutionTask).filter(ExecutionTask.id == task_id).first()
            assert task is not None
            assert task.status == "pending"
            assert task.assignee_id == assignee.id
            saved_note = db.query(MeetingNote).filter(MeetingNote.id == meeting["id"]).first()
            assert saved_note.status == "confirmed"
        finally:
            db.close()
    finally:
        _set_flag("feature_meeting_ai", old_meeting_flag)
        _set_flag("feature_execution", old_execution_flag)


def test_meeting_manual_draft_and_cancel_rejects_pending_drafts(client):
    _, headers = _create_headers(client, role="staff")
    assignee, _ = _create_headers(client, role="user")
    old_flag = _set_flag("feature_meeting_ai", True)
    try:
        response = client.post("/api/v1/meetings", headers=headers, json={"content": "今天只同步客户背景，没有形成明确待办。"})
        assert response.status_code == 200
        meeting = response.json()["data"]
        assert meeting["ai_status"] == "no_tasks"
        assert meeting["drafts"] == []

        manual = client.post(
            f"/api/v1/meetings/{meeting['id']}/drafts",
            headers=headers,
            json={
                "title": "人工补充客户回访",
                "source_sentence": "会议后人工补充",
                "assignee_id": assignee.id,
                "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                "notes": "AI 未提取时降级补充",
            },
        )
        assert manual.status_code == 200
        draft_id = manual.json()["data"]["id"]

        cancel = client.post(
            f"/api/v1/meetings/{meeting['id']}/cancel",
            headers=headers,
            json={"reason": "录入错误"},
        )
        assert cancel.status_code == 200
        assert cancel.json()["data"]["status"] == "cancelled"

        db = SessionLocal()
        try:
            draft = db.query(TaskDraft).filter(TaskDraft.id == draft_id).first()
            assert draft.status == "rejected"
            assert draft.rejection_reason == "meeting_cancelled"
        finally:
            db.close()
    finally:
        _set_flag("feature_meeting_ai", old_flag)


def test_confirmed_meeting_revision_creates_supplemental_drafts(client):
    admin, admin_headers = _create_headers(client, role="admin")
    assignee, _ = _create_headers(client, role="user")
    old_flag = _set_flag("feature_meeting_ai", True)
    try:
        created = client.post(
            "/api/v1/meetings",
            headers=admin_headers,
            json={"content": f"请 {assignee.username} 明天负责整理报价复盘。"},
        )
        meeting = created.json()["data"]
        draft = meeting["drafts"][0]
        confirmed = client.post(
            f"/api/v1/meetings/{meeting['id']}/confirm-tasks",
            headers=admin_headers,
            json={
                "drafts": [
                    {
                        "draft_id": draft["id"],
                        "assignee_id": assignee.id,
                        "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                    }
                ]
            },
        )
        assert confirmed.status_code == 200
        original_confirmed_at = confirmed.json()["data"]["meeting"]["confirmed_at"]

        revised = client.post(
            f"/api/v1/meetings/{meeting['id']}/revisions",
            headers=admin_headers,
            json={"content": f"补充：请 {assignee.username} 后天负责提交客户回访结果。", "reason": "补充遗漏任务"},
        )
        assert revised.status_code == 200
        revised_note = revised.json()["data"]
        assert revised_note["status"] == "revised"
        assert revised_note["revisions"][0]["previous_content_sha256"]
        assert any(draft["revision_id"] for draft in revised_note["drafts"])

        revision_draft = next(item for item in revised_note["drafts"] if item["status"] == "pending_review")
        confirm_revision = client.post(
            f"/api/v1/meetings/{meeting['id']}/confirm-tasks",
            headers=admin_headers,
            json={
                "drafts": [
                    {
                        "draft_id": revision_draft["id"],
                        "assignee_id": assignee.id,
                        "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                    }
                ]
            },
        )
        assert confirm_revision.status_code == 200
        assert confirm_revision.json()["data"]["meeting"]["status"] == "confirmed"
        assert confirm_revision.json()["data"]["meeting"]["confirmed_at"] == original_confirmed_at
        assert confirm_revision.json()["data"]["tasks"][0]["source"] == "meeting"

        db = SessionLocal()
        try:
            task_count = db.query(ExecutionTask).filter(ExecutionTask.source_ref_id == str(meeting["id"])).count()
            assert task_count >= 2
        finally:
            db.close()
    finally:
        _set_flag("feature_meeting_ai", old_flag)
