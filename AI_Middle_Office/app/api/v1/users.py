from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok
from app.core.security import get_password_hash
from app.dependencies import require_admin, require_system_admin
from app.models.user import User, UserRoleEvent
from app.services.rbac import grant_role, normalize_role, revoke_role, serialize_user_for_rbac
from app.services.account_tenancy import (
    AccountTenancyError,
    assign_user_to_operator_default_account,
)


router = APIRouter()


class AdminUserCreate(BaseModel):
    username: str
    password: str
    quota: int = 5
    roles: list[str] = Field(default_factory=lambda: ["staff"])
    note: str


class QuotaUpdate(BaseModel):
    quota: int


class RoleGrantRequest(BaseModel):
    role: str
    note: str


class RoleRevokeRequest(BaseModel):
    note: str
    trace_id: str | None = None


@router.get("/admin/users", summary="获取所有用户列表")
async def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    users = db.query(User).options(selectinload(User.role_assignments)).order_by(User.id).all()
    return api_ok([serialize_user_for_rbac(user) for user in users])


@router.post("/admin/users", summary="系统管理员创建用户")
async def create_user_by_admin(
    body: AdminUserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    username = (body.username or "").strip()
    password = body.password or ""
    note = (body.note or "").strip()
    if not username:
        raise HTTPException(status_code=422, detail="USERNAME_REQUIRED")
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="PASSWORD_TOO_SHORT")
    if body.quota < 0:
        raise HTTPException(status_code=400, detail="额度不能为负数")
    if not note:
        raise HTTPException(status_code=422, detail="备注不能为空")
    roles = []
    for role in body.roles or ["staff"]:
        normalized = normalize_role(role)
        if normalized not in roles:
            roles.append(normalized)
    if not roles:
        roles = ["staff"]
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail="该用户名已存在")

    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            quota=body.quota,
            role="user",
            role_version=1,
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        for role in roles:
            grant_role(
                db,
                target_user=user,
                role=role,
                operator=current_user,
                note=note,
                request=request,
                commit=False,
            )
        if settings.feature_budget_pricing_drafts:
            assign_user_to_operator_default_account(
                db,
                target_user=user,
                operator=current_user,
            )
        user_id = user.id
        db.commit()
        db.expire_all()
        user = (
            db.query(User)
            .options(selectinload(User.role_assignments))
            .filter(User.id == user_id)
            .one()
        )
    except AccountTenancyError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_user_for_rbac(user))


@router.patch("/admin/users/{user_id}/quota", summary="设置指定用户的 AI 调用额度")
async def set_user_quota(
    user_id: int,
    body: QuotaUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if body.quota < 0:
        raise HTTPException(status_code=400, detail="额度不能为负数")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.quota = body.quota
    db.commit()
    return api_ok(message=f"已将 {user.username} 的额度设置为 {body.quota} 次")


@router.post("/admin/users/{user_id}/roles", summary="授予用户角色")
async def grant_user_role(
    user_id: int,
    body: RoleGrantRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    user = (
        db.query(User)
        .options(selectinload(User.role_assignments))
        .filter(User.id == user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    roles = grant_role(
        db,
        target_user=user,
        role=body.role,
        operator=current_user,
        note=body.note,
        request=request,
    )
    return api_ok({"id": user.id, "username": user.username, "roles": roles, "role_version": user.role_version})


@router.post("/admin/users/{user_id}/roles/{role}/revoke", summary="撤销用户角色")
async def revoke_user_role(
    user_id: int,
    role: str,
    body: RoleRevokeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    user = (
        db.query(User)
        .options(selectinload(User.role_assignments))
        .filter(User.id == user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    roles = revoke_role(
        db,
        target_user=user,
        role=role,
        operator=current_user,
        note=body.note,
        request=request,
    )
    return api_ok({"id": user.id, "username": user.username, "roles": roles, "role_version": user.role_version})


@router.get("/admin/users/{user_id}/role-events", summary="查看用户角色授权历史")
async def list_user_role_events(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    events = (
        db.query(UserRoleEvent)
        .filter(UserRoleEvent.target_user_id == user_id)
        .order_by(UserRoleEvent.id.desc())
        .all()
    )
    return api_ok(
        [
            {
                "id": event.id,
                "target_user_id": event.target_user_id,
                "role": event.role,
                "action": event.action,
                "operator_id": event.operator_id,
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "ip_address": event.ip_address,
                "user_agent": event.user_agent,
                "trace_id": event.trace_id,
                "note": event.note,
            }
            for event in events
        ]
    )
