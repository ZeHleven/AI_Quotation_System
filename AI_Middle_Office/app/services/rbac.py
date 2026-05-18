from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User, UserRole, UserRoleEvent


VALID_ROLES = ("system_admin", "admin", "staff", "manager", "viewer")
ROLE_ORDER = {role: index for index, role in enumerate(VALID_ROLES)}
LEGACY_ROLE_MAP = {
    "admin": ["admin"],
    "user": ["staff"],
    "staff": ["staff"],
    "manager": ["manager"],
    "viewer": ["viewer"],
    "system_admin": ["system_admin", "admin"],
}


def normalize_role(role: str) -> str:
    normalized = (role or "").strip()
    if normalized not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的角色")
    return normalized


def _sort_roles(roles: Iterable[str]) -> list[str]:
    return sorted({role for role in roles if role in VALID_ROLES}, key=lambda item: ROLE_ORDER[item])


def get_assigned_roles(user: User) -> list[str]:
    assignments = getattr(user, "role_assignments", None) or []
    return _sort_roles(assignment.role for assignment in assignments)


def get_effective_roles(user: User) -> list[str]:
    assigned_roles = get_assigned_roles(user)
    if assigned_roles:
        return assigned_roles
    return _sort_roles(LEGACY_ROLE_MAP.get(user.role or "", []))


def _roles_with_implications(user: User) -> set[str]:
    roles = set(get_effective_roles(user))
    if "system_admin" in roles:
        roles.add("admin")
    return roles


def has_any_role(user: User, roles: Iterable[str]) -> bool:
    allowed = set(roles)
    return bool(_roles_with_implications(user) & allowed)


def has_admin_role(user: User) -> bool:
    return has_any_role(user, {"admin", "system_admin"})


def has_system_admin_role(user: User) -> bool:
    return "system_admin" in get_effective_roles(user)


def sync_legacy_role(user: User) -> None:
    roles = set(get_assigned_roles(user))
    if {"system_admin", "admin"} & roles:
        user.role = "admin"
    elif "staff" in roles:
        user.role = "user"
    elif "manager" in roles:
        user.role = "manager"
    elif "viewer" in roles:
        user.role = "viewer"
    else:
        user.role = "none"


def bump_role_version(user: User) -> None:
    user.role_version = int(user.role_version or 1) + 1


def get_available_modules(user: User) -> list[dict]:
    roles = set(_roles_with_implications(user))
    modules: list[dict] = []

    if {"system_admin", "admin", "staff"} & roles:
        modules.append(
            {
                "key": "legacy_quote",
                "name": "旧报价工作台",
                "path": "/index.html",
                "status": "available",
            }
        )
    if {"system_admin", "admin"} & roles:
        modules.append(
            {
                "key": "legacy_knowledge",
                "name": "旧知识库管理",
                "path": "/admin.html",
                "status": "available",
            }
        )
    if "system_admin" in roles:
        modules.append(
            {
                "key": "permissions",
                "name": "权限管理",
                "path": "/admin/permissions",
                "status": "available",
            }
        )
    if "manager" in roles:
        modules.append(
            {
                "key": "execution",
                "name": "执行任务",
                "path": "/admin/execution",
                "status": "pending",
            }
        )
    if {"system_admin", "admin", "viewer"} & roles:
        modules.append(
            {
                "key": "dashboards",
                "name": "效率与经营驾驶舱",
                "path": "/admin/dashboard",
                "status": "available" if settings.feature_dashboard_quote else "pending",
            }
        )
    return modules


def serialize_user_for_rbac(user: User) -> dict:
    roles = get_effective_roles(user)
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "roles": roles,
        "role_version": int(user.role_version or 1),
        "quota": user.quota,
        "is_active": bool(user.is_active),
        "must_change_password": bool(user.must_change_password),
        "dingtalk_bound": bool(user.dingtalk_user_id),
        "dingtalk_verified_until": None,
        "available_modules": get_available_modules(user),
        "last_login_at": None,
        "created_at": None,
    }


def _request_context(request: Request | None) -> tuple[str | None, str | None, str | None]:
    if request is None:
        return None, None, None
    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip_address = forwarded_for.split(",")[0].strip() if forwarded_for else None
    if not ip_address and request.client:
        ip_address = request.client.host
    user_agent = request.headers.get("user-agent")
    trace_id = getattr(request.state, "trace_id", None) or request.headers.get("x-trace-id")
    return ip_address, user_agent, trace_id


def _write_role_event(
    db: Session,
    *,
    target_user: User,
    role: str,
    action: str,
    operator: User | None,
    note: str,
    request: Request | None,
) -> None:
    ip_address, user_agent, trace_id = _request_context(request)
    db.add(
        UserRoleEvent(
            target_user_id=target_user.id,
            role=role,
            action=action,
            operator_id=operator.id if operator else None,
            ip_address=ip_address,
            user_agent=user_agent,
            trace_id=trace_id,
            note=note,
        )
    )


def _require_note(note: str | None) -> str:
    cleaned = (note or "").strip()
    if not cleaned:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="备注不能为空")
    return cleaned


def grant_role(
    db: Session,
    *,
    target_user: User,
    role: str,
    operator: User,
    note: str | None,
    request: Request | None = None,
) -> list[str]:
    role = normalize_role(role)
    note = _require_note(note)

    existing = (
        db.query(UserRole)
        .filter(UserRole.user_id == target_user.id, UserRole.role == role)
        .first()
    )
    if existing is None:
        db.add(UserRole(user_id=target_user.id, role=role, created_by=operator.id, note=note))
        bump_role_version(target_user)
        db.flush()
        db.expire(target_user, ["role_assignments"])
        sync_legacy_role(target_user)

    _write_role_event(
        db,
        target_user=target_user,
        role=role,
        action="granted",
        operator=operator,
        note=note,
        request=request,
    )
    db.commit()
    db.refresh(target_user)
    return get_effective_roles(target_user)


def _active_system_admin_count(db: Session) -> int:
    return (
        db.query(UserRole)
        .join(User, User.id == UserRole.user_id)
        .filter(UserRole.role == "system_admin", User.is_active.is_(True))
        .count()
    )


def revoke_role(
    db: Session,
    *,
    target_user: User,
    role: str,
    operator: User,
    note: str | None,
    request: Request | None = None,
) -> list[str]:
    role = normalize_role(role)
    note = _require_note(note)

    existing = (
        db.query(UserRole)
        .filter(UserRole.user_id == target_user.id, UserRole.role == role)
        .first()
    )
    if existing is None:
        return get_effective_roles(target_user)

    if role == "system_admin" and _active_system_admin_count(db) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能撤销最后一个 system_admin")

    db.delete(existing)
    bump_role_version(target_user)
    db.flush()
    db.expire(target_user, ["role_assignments"])
    sync_legacy_role(target_user)
    _write_role_event(
        db,
        target_user=target_user,
        role=role,
        action="revoked",
        operator=operator,
        note=note,
        request=request,
    )
    db.commit()
    db.refresh(target_user)
    return get_effective_roles(target_user)
