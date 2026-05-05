from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import api_ok
from app.dependencies import require_admin
from app.models.user import User


router = APIRouter()


class QuotaUpdate(BaseModel):
    quota: int


@router.get("/admin/users", summary="获取所有用户列表")
async def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    users = db.query(User).order_by(User.id).all()
    return api_ok([
        {"id": u.id, "username": u.username, "role": u.role, "quota": u.quota, "is_active": u.is_active}
        for u in users
    ])


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
