# 部署路径: AI_Middle_Office/app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session, selectinload
from pydantic import BaseModel

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.config import settings
from app.core.responses import api_ok
from app.dependencies import get_current_user, require_admin
from app.models.user import User, UserRole
from app.core.security import get_password_hash, verify_password, create_access_token
from app.services.rbac import get_effective_roles, serialize_user_for_rbac

router = APIRouter()


class UserCreate(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class DingTalkVerifyRequest(BaseModel):
    code: str | None = None


@router.post("/register", summary="注册新员工账号")
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    if not settings.allow_self_registration:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="SELF_REGISTRATION_DISABLED")

    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="该用户名已存在")

    hashed_pwd = get_password_hash(user.password)
    # 给新注册的账号默认分配 5 次 AI 调用体验额度
    new_user = User(username=user.username, hashed_password=hashed_pwd, quota=5, role="user", role_version=1)
    db.add(new_user)
    db.flush()
    db.add(UserRole(user_id=new_user.id, role="staff", created_by=None, note="register_default_staff"))
    db.commit()
    db.refresh(new_user)
    message = f"账号 {new_user.username} 注册成功，获得 {new_user.quota} 次查询额度！"
    return api_ok({"username": new_user.username, "quota": new_user.quota}, message=message)


@router.post("/login", summary="账号登录获取 Token")
@limiter.limit(settings.login_rate_limit)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .options(selectinload(User.role_assignments))
        .filter(User.username == form_data.username)
        .first()
    )
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 签发包含用户身份的 JWT
    roles = get_effective_roles(user)
    role_version = int(user.role_version or 1)
    access_token = create_access_token(
        data={
            "sub": user.username,
            "role": user.role,
            "roles": roles,
            "role_version": role_version,
        }
    )
    data = {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "roles": roles,
        "role_version": role_version,
        "username": user.username,
        "must_change_password": bool(user.must_change_password),
        "dingtalk_verification_required": False,
    }
    return api_ok(data, **data)


@router.get("/me", summary="获取当前登录用户")
def read_current_user(current_user: User = Depends(get_current_user)):
    data = serialize_user_for_rbac(current_user)
    return api_ok(data, **data)


@router.post("/change_password", summary="修改密码")
def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user or not verify_password(req.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码不能少于6位")

    user.hashed_password = get_password_hash(req.new_password)
    user.must_change_password = False
    db.commit()
    return api_ok(message="密码修改成功")


@router.post("/dingtalk/verify", summary="钉钉登录二次验证")
def verify_dingtalk_login(
    req: DingTalkVerifyRequest,
    current_user: User = Depends(require_admin),
):
    if not settings.public_access_enabled:
        return api_ok(
            {
                "verified": False,
                "required": False,
                "dingtalk_verified_until": None,
            },
            message="PUBLIC_ACCESS_ENABLED=false，内网阶段暂不强制钉钉二次验证",
        )
    raise HTTPException(status_code=403, detail="DINGTALK_VERIFY_REQUIRED")
