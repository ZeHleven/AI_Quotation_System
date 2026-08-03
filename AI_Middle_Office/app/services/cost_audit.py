from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models.cost_audit import CostAccessAuditLog
from app.models.user import User
from app.services.rbac import get_effective_roles, has_any_role


logger = logging.getLogger(__name__)

COST_AUDIT_VIEW_ROLES = {"system_admin", "admin", "cost_approver"}


def can_view_cost_audit(user: User) -> bool:
    return has_any_role(user, COST_AUDIT_VIEW_ROLES)


def require_cost_audit_access(user: User) -> None:
    if not can_view_cost_audit(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _json_dumps(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _request_context(request: Request | None) -> dict[str, str | None]:
    if request is None:
        return {
            "request_path": None,
            "request_method": None,
            "client_ip": None,
            "user_agent": None,
            "trace_id": None,
        }
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else None
    if not client_ip and request.client:
        client_ip = request.client.host
    return {
        "request_path": str(request.url.path),
        "request_method": request.method,
        "client_ip": client_ip,
        "user_agent": request.headers.get("user-agent"),
        "trace_id": getattr(request.state, "trace_id", None) or request.headers.get("x-trace-id"),
    }


def record_cost_audit(
    db: Session,
    *,
    user: User | None,
    action: str,
    resource_type: str = "cost_item",
    resource_id: int | str | None = None,
    filters: dict[str, Any] | None = None,
    result_count: int | None = None,
    status_value: str = "success",
    message: str | None = None,
    request: Request | None = None,
) -> CostAccessAuditLog | None:
    try:
        request_context = _request_context(request)
        log = CostAccessAuditLog(
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            user_id=user.id if user else None,
            username=user.username if user else None,
            roles_snapshot=_json_dumps(get_effective_roles(user)) if user else None,
            filters_json=_json_dumps(filters),
            result_count=result_count,
            status=status_value,
            message=(message or None),
            **request_context,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
    except Exception:
        db.rollback()
        logger.exception("cost_audit_log_write_failed", extra={"action": action, "username": getattr(user, "username", None)})
        return None


def serialize_cost_audit_log(log: CostAccessAuditLog) -> dict[str, Any]:
    try:
        filters = json.loads(log.filters_json) if log.filters_json else None
    except Exception:
        filters = None
    try:
        roles = json.loads(log.roles_snapshot) if log.roles_snapshot else []
    except Exception:
        roles = []
    return {
        "id": log.id,
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "user_id": log.user_id,
        "username": log.username,
        "roles_snapshot": roles,
        "request_path": log.request_path,
        "request_method": log.request_method,
        "client_ip": log.client_ip,
        "user_agent": log.user_agent,
        "trace_id": log.trace_id,
        "filters": filters,
        "result_count": log.result_count,
        "status": log.status,
        "message": log.message,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def list_cost_audit_logs(
    db: Session,
    user: User,
    *,
    action: str | None = None,
    username: str | None = None,
    resource_id: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CostAccessAuditLog], int]:
    require_cost_audit_access(user)
    query = db.query(CostAccessAuditLog)
    if action:
        query = query.filter(CostAccessAuditLog.action == action)
    if username:
        query = query.filter(CostAccessAuditLog.username.like(f"%{username.strip()}%"))
    if resource_id:
        query = query.filter(CostAccessAuditLog.resource_id == str(resource_id).strip())
    if status_filter:
        query = query.filter(CostAccessAuditLog.status == status_filter)
    total = query.count()
    rows = (
        query.order_by(CostAccessAuditLog.created_at.desc(), CostAccessAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total
