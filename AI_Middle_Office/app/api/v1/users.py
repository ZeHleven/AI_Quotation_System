from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.responses import api_ok
from app.dependencies import require_admin, require_system_admin
from app.models.user import User, UserRoleEvent
from app.services.rbac import grant_role, revoke_role, serialize_user_for_rbac


router = APIRouter()


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
