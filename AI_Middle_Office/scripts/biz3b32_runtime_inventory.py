from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.project_progress import ProjectTask, Project, ProjectTaskEvidence  # noqa: E402
from app.services.rbac import get_effective_roles  # noqa: E402
from app.services.project_progress import can_manage_project, can_access_project_progress  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        out = {}
        tasks = (
            db.query(ProjectTask)
            .filter(ProjectTask.evidence_policy == "complete_required")
            .order_by(ProjectTask.project_id, ProjectTask.id)
            .all()
        )
        task_rows = []
        for t in tasks:
            ev_count = (
                db.query(ProjectTaskEvidence)
                .filter(
                    ProjectTaskEvidence.task_id == t.id,
                    ProjectTaskEvidence.status == "active",
                )
                .count()
            )
            task_rows.append({
                "id": t.id,
                "project_id": t.project_id,
                "title": t.title,
                "status": t.status,
                "progress_percent": t.progress_percent,
                "owner_user_id": t.owner_user_id,
                "evidence_policy": t.evidence_policy,
                "evidence_requirement": t.evidence_requirement,
                "is_key_node": t.is_key_node,
                "active_evidence_count": ev_count,
            })
        out["complete_required_tasks"] = task_rows

        # Projects for these tasks
        proj_ids = sorted({t.project_id for t in tasks})
        proj_rows = []
        for pid in proj_ids:
            p = db.query(Project).filter(Project.id == pid).first()
            if p:
                proj_rows.append({
                    "id": p.id,
                    "name": p.name,
                    "project_manager_id": getattr(p, "project_manager_id", None),
                    "created_by": getattr(p, "created_by", None),
                })
        out["projects"] = proj_rows

        # Candidate accounts
        users = db.query(User).order_by(User.id).all()
        user_rows = []
        for u in users:
            eff = sorted(get_effective_roles(u))
            user_rows.append({
                "id": u.id,
                "username": u.username,
                "base_role": u.role,
                "role_version": u.role_version,
                "is_active": u.is_active,
                "effective_roles": eff,
                "can_access_pp": can_access_project_progress(u),
            })
        out["users"] = user_rows

        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
