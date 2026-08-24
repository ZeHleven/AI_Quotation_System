from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User, UserRole, UserRoleEvent


VALID_ROLES = (
    "system_admin",
    "admin",
    "quote_operator",
    "quote_user",
    "cost_viewer",
    "cost_editor",
    "cost_approver",
    "cost_exporter",
    "enterprise_profile_viewer",
    "enterprise_profile_editor",
    "enterprise_profile_approver",
    "project_viewer",
    "project_member",
    "project_manager",
    "staff",
    "manager",
    "viewer",
)
ROLE_ORDER = {role: index for index, role in enumerate(VALID_ROLES)}
LEGACY_ROLE_MAP = {
    "admin": ["admin"],
    "user": ["staff"],
    "staff": ["staff"],
    "manager": ["manager"],
    "viewer": ["viewer"],
    "system_admin": ["system_admin", "admin"],
}
ROLE_DEFAULT_HOME_RULES = (
    ({"system_admin", "admin"}, "/admin/dashboard"),
    ({"quote_operator", "viewer"}, "/admin/dashboard"),
    ({"manager", "project_manager"}, "/admin/projects"),
    ({"project_member"}, "/admin/project-tasks/my"),
    ({"project_viewer"}, "/admin/projects"),
    ({"cost_viewer", "cost_editor", "cost_approver", "cost_exporter"}, "/admin/cost-db"),
    (
        {"enterprise_profile_viewer", "enterprise_profile_editor", "enterprise_profile_approver"},
        "/admin/enterprise-profile",
    ),
    ({"staff", "quote_user"}, "/quote/new"),
)

BUDGET_PRICING_VIEW_ROLES = {
    "system_admin",
    "admin",
    "cost_viewer",
    "cost_editor",
    "cost_approver",
    "cost_exporter",
}
BUDGET_PRICING_CREATE_ROLES = {
    "system_admin",
    "admin",
    "cost_editor",
    "cost_approver",
}
BUDGET_PROJECT_ACCESS_ROLES = {
    "system_admin",
    "admin",
    "staff",
    "manager",
    "viewer",
    "project_viewer",
    "project_member",
    "project_manager",
    "quote_user",
    "quote_operator",
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
        roles.update({"quote_operator", "quote_user"})
        roles.update({"cost_viewer", "cost_editor", "cost_approver", "cost_exporter"})
        roles.update({"enterprise_profile_viewer", "enterprise_profile_editor", "enterprise_profile_approver"})
    if "admin" in roles:
        roles.update({"quote_operator", "quote_user"})
        roles.update({"cost_viewer", "cost_editor", "cost_approver"})
        roles.update({"enterprise_profile_viewer", "enterprise_profile_editor", "enterprise_profile_approver"})
    if "cost_approver" in roles:
        roles.update({"cost_viewer", "cost_editor"})
    if "cost_editor" in roles:
        roles.add("cost_viewer")
    if "enterprise_profile_approver" in roles:
        roles.update({"enterprise_profile_viewer", "enterprise_profile_editor"})
    if "enterprise_profile_editor" in roles:
        roles.add("enterprise_profile_viewer")
    if "system_admin" in roles or "admin" in roles:
        roles.update({"project_viewer", "project_member", "project_manager"})
    if "manager" in roles:
        roles.update({"project_viewer", "project_member", "project_manager"})
    if "project_manager" in roles:
        roles.update({"project_viewer", "project_member"})
    if "project_member" in roles:
        roles.add("project_viewer")
    return roles


def has_any_role(user: User, roles: Iterable[str]) -> bool:
    allowed = set(roles)
    return bool(_roles_with_implications(user) & allowed)


def has_admin_role(user: User) -> bool:
    return has_any_role(user, {"admin", "system_admin"})


def has_system_admin_role(user: User) -> bool:
    return "system_admin" in get_effective_roles(user)


def can_view_budget_pricing(user: User) -> bool:
    return has_any_role(user, BUDGET_PRICING_VIEW_ROLES)


def can_create_budget_pricing(user: User) -> bool:
    return has_any_role(user, BUDGET_PRICING_CREATE_ROLES)


def require_budget_pricing_view(user: User) -> None:
    if not can_view_budget_pricing(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def require_budget_pricing_create(user: User) -> None:
    if not can_create_budget_pricing(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


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
    assigned_roles = set(get_effective_roles(user))
    staff_only = assigned_roles == {"staff"}
    roles = set(_roles_with_implications(user))
    modules: list[dict] = []

    if BUDGET_PROJECT_ACCESS_ROLES & roles:
        quick_source_available = bool({"system_admin", "admin", "staff", "quote_user"} & roles)
        budget_source_available = bool(settings.feature_budget_projects and BUDGET_PROJECT_ACCESS_ROLES & roles)
        modules.append(
            {
                "key": "unified_quotes",
                "name": "报价工作台",
                "path": "/quote/new",
                "status": "available"
                if settings.feature_unified_quotes and (quick_source_available or budget_source_available)
                else "pending",
                "stage": "trial",
            }
        )
    if {"system_admin", "admin", "staff", "quote_user"} & roles:
        modules.append(
            {
                "key": "legacy_quote",
                "name": "AI 报价",
                "path": "/index.html",
                "status": "available",
            }
        )
    if {"system_admin", "admin"} & roles:
        modules.append(
            {
                "key": "legacy_knowledge",
                "name": "兼容管理页",
                "path": "/admin.html",
                "status": "available",
                "stage": "compatibility",
            }
        )
    if {"system_admin", "admin"} & roles:
        modules.append(
            {
                "key": "permissions",
                "name": "账号与权限",
                "path": "/admin/permissions",
                "status": "available",
            }
        )
    if {"system_admin", "admin", "manager", "project_viewer", "project_member", "project_manager"} & roles:
        modules.append(
            {
                "key": "project_progress",
                "name": "项目进度",
                "path": "/admin/projects",
                "status": "available" if settings.feature_project_progress else "pending",
            }
        )
    # A staff-only account reaches budget projects through the unified
    # "project quotation" workspace. Do not expose a fourth standalone
    # module for the same workflow.
    if BUDGET_PROJECT_ACCESS_ROLES & roles and not staff_only:
        modules.append(
            {
                "key": "budget_projects",
                "name": "\u9884\u7b97\u9879\u76ee",
                "path": "/admin/budget-projects",
                "status": "available" if settings.feature_budget_projects else "pending",
            }
        )
    if {"system_admin", "admin", "quote_user", "quote_operator"} & roles:
        modules.append(
            {
                "key": "pricing_agent",
                "name": "智能组价实验室",
                "path": "/admin/pricing-agent",
                "status": "available" if settings.feature_pricing_agent else "pending",
                "stage": "trial",
            }
        )
    if (BUDGET_PRICING_VIEW_ROLES | BUDGET_PROJECT_ACCESS_ROLES) & roles and not staff_only:
        budget_pricing_enabled = settings.feature_budget_projects and settings.feature_budget_pricing
        can_reach_budget_project = bool(BUDGET_PROJECT_ACCESS_ROLES & roles)
        can_view_pricing = bool(BUDGET_PRICING_VIEW_ROLES & roles)
        modules.append(
            {
                "key": "budget_pricing",
                "name": "项目成本计价",
                "path": "/admin/budget-projects",
                "status": "available"
                if budget_pricing_enabled and can_reach_budget_project and can_view_pricing
                else "forbidden"
                if budget_pricing_enabled and can_reach_budget_project and not can_view_pricing
                else "pending",
                "stage": "trial",
            }
        )
    if {"system_admin", "admin", "staff"} & roles:
        modules.append(
            {
                "key": "account_quotas",
                "name": "账户定额库",
                "path": "/admin/account-quotas",
                "status": "available" if settings.feature_account_quotas else "pending",
                "stage": "trial",
            }
        )
    if {"system_admin", "admin", "staff", "cost_viewer", "cost_editor", "cost_approver", "cost_exporter"} & roles:
        modules.append(
            {
                "key": "cost_db",
                "name": "企业定额主库 / 成本主库",
                "path": "/admin/cost-db",
                "status": "available" if settings.feature_cost_db else "pending",
            }
        )
    if {"system_admin", "admin", "quote_user"} & roles:
        modules.append(
            {
                "key": "requirement_standardization",
                "name": "需求单标准化",
                "path": "/admin/requirement-standardization",
                "status": "available" if settings.feature_requirement_standardization else "pending",
            }
        )
    if {"system_admin", "admin", "quote_user"} & roles:
        modules.append(
            {
                "key": "dwg_trial",
                "name": "图纸识图",
                "path": "/admin/dwg-trial",
                "status": "available",
                "stage": "trial",
            }
        )
    if {"system_admin", "admin", "staff", "manager", "quote_user", "quote_operator"} & roles:
        modules.append(
            {
                "key": "bidding",
                "name": "智能投标",
                "path": "/admin/bidding",
                "status": "available" if settings.feature_bidding_mvp else "pending",
                "stage": "trial",
            }
        )
    if {"system_admin", "admin", "staff", "manager", "quote_user", "quote_operator"} & roles:
        modules.append(
            {
                "key": "bid_assessment_pure_agent",
                "name": "投标机会研判 Agent",
                "path": "/admin/bid-assessment-pure-agent",
                "status": "available"
                if settings.feature_bid_assessment_pure_agent
                else "pending",
                "stage": "local_development",
            }
        )
    if {"system_admin", "admin", "enterprise_profile_viewer", "enterprise_profile_editor", "enterprise_profile_approver"} & roles:
        modules.append(
            {
                "key": "enterprise_profile",
                "name": "企业资料库",
                "path": "/admin/enterprise-profile",
                "status": "available" if settings.feature_enterprise_profile else "pending",
            }
        )
    if {"system_admin", "admin", "quote_user", "quote_operator"} & roles:
        modules.append(
            {
                "key": "agent_center",
                "name": "AI 助手中心",
                "path": "/admin/agent-center",
                "status": "available" if settings.feature_agent_assistants else "pending",
                "stage": "trial",
            }
        )
    if {"system_admin", "admin", "viewer", "quote_operator"} & roles:
        dashboard_enabled = (
            settings.feature_dashboard_quote
            or settings.feature_dashboard_response
            or settings.feature_dashboard_project
            or settings.feature_dashboard_business_lite
            or "quote_operator" in roles
        )
        modules.append(
            {
                "key": "dashboards",
                "name": "经营总览",
                "path": "/admin/dashboard",
                "status": "available" if dashboard_enabled else "pending",
            }
        )
    return modules


def get_default_home_path(user: User) -> str:
    roles = set(get_effective_roles(user))
    modules = get_available_modules(user)
    available_paths = {item["path"] for item in modules if item.get("status") == "available"}

    def path_is_available(path: str) -> bool:
        if path == "/quote/new":
            return "/quote/new" in available_paths or "/index.html" in available_paths
        if path == "/admin/project-tasks/my":
            return "/admin/projects" in available_paths
        return path in available_paths

    for matching_roles, path in ROLE_DEFAULT_HOME_RULES:
        if roles & matching_roles and path_is_available(path):
            return path

    first_available = next(
        (item for item in modules if item.get("status") == "available"),
        None,
    )
    return first_available["path"] if first_available else "/no-access"


def serialize_user_for_rbac(user: User) -> dict:
    roles = get_effective_roles(user)
    quota_reserved = int(getattr(user, "quota_reserved", 0) or 0)
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "roles": roles,
        "role_version": int(user.role_version or 1),
        "quota": user.quota,
        "quota_reserved": quota_reserved,
        "quota_available": max(0, int(user.quota or 0) - quota_reserved),
        "is_active": bool(user.is_active),
        "must_change_password": bool(user.must_change_password),
        "dingtalk_bound": bool(user.dingtalk_user_id),
        "dingtalk_verified_until": None,
        "available_modules": get_available_modules(user),
        "default_home_path": get_default_home_path(user),
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
    commit: bool = True,
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
    if commit:
        db.commit()
        db.refresh(target_user)
    else:
        # Atomic callers (for example user + account provisioning) own the
        # transaction boundary.  Flush role/event state without exposing a
        # partially-created user if a later step fails.
        db.flush()
    return get_effective_roles(target_user)


def replace_roles(
    db: Session,
    *,
    target_user: User,
    roles: Iterable[str],
    operator: User,
    note: str | None,
    request: Request | None = None,
) -> list[str]:
    """Atomically replace a user's assigned role set and audit the real changes."""
    note = _require_note(note)
    desired_roles = _sort_roles(normalize_role(role) for role in roles)
    desired_set = set(desired_roles)
    current_roles = set(get_effective_roles(target_user))
    if desired_set == current_roles:
        return _sort_roles(current_roles)

    if (
        "system_admin" in current_roles
        and "system_admin" not in desired_set
        and _active_system_admin_count(db) <= 1
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能撤销最后一个 system_admin")

    assignments = {
        assignment.role: assignment
        for assignment in (getattr(target_user, "role_assignments", None) or [])
    }
    removed_roles = _sort_roles(current_roles - desired_set)
    added_roles = _sort_roles(desired_set - current_roles)

    for role, assignment in assignments.items():
        if role not in desired_set:
            db.delete(assignment)
    # A legacy-only role has no UserRole row. When the requested set changes,
    # materialize every retained role so sync_legacy_role cannot restore a
    # removed permission from User.role.
    for role in desired_roles:
        if role not in assignments:
            db.add(UserRole(user_id=target_user.id, role=role, created_by=operator.id, note=note))

    bump_role_version(target_user)
    db.flush()
    db.expire(target_user, ["role_assignments"])
    sync_legacy_role(target_user)

    for role in removed_roles:
        _write_role_event(
            db,
            target_user=target_user,
            role=role,
            action="revoked",
            operator=operator,
            note=note,
            request=request,
        )
    for role in added_roles:
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
