import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.security import ALGORITHM, SECRET_KEY
from app.models.user import User
from app.services.rbac import get_effective_roles, has_admin_role, has_any_role, has_system_admin_role


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_authenticated_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="认证失效",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        token_role_version = payload.get("role_version")
        if username is None:
            raise credentials_exception
    except Exception as exc:
        raise credentials_exception from exc

    user = (
        db.query(User)
        .options(selectinload(User.role_assignments))
        .filter(User.username == username)
        .first()
    )
    if user is None or not user.is_active:
        raise credentials_exception
    try:
        role_version_matches = token_role_version is not None and int(token_role_version) == int(user.role_version or 1)
    except (TypeError, ValueError):
        role_version_matches = False
    if not role_version_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ROLE_VERSION_EXPIRED",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user.effective_roles = get_effective_roles(user)
    user.token_password_change_required = bool(payload.get("password_change_required", False))
    return user


def get_current_user(current_user: User = Depends(get_authenticated_user)) -> User:
    if bool(current_user.must_change_password) or bool(
        getattr(current_user, "token_password_change_required", False)
    ):
        raise HTTPException(status_code=403, detail="PASSWORD_CHANGE_REQUIRED")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not has_admin_role(current_user):
        raise HTTPException(status_code=403, detail="权限不足，仅管理员可操作")
    return current_user


def require_account_quota_user(current_user: User = Depends(get_current_user)) -> User:
    if not has_any_role(current_user, {"system_admin", "admin", "staff"}):
        raise HTTPException(status_code=403, detail="PERMISSION_DENIED")
    return current_user


def require_system_admin(current_user: User = Depends(get_current_user)) -> User:
    if not has_system_admin_role(current_user):
        raise HTTPException(status_code=403, detail="权限不足，仅 system_admin 可操作")
    return current_user


def require_dashboard_viewer(current_user: User = Depends(get_current_user)) -> User:
    if not has_any_role(current_user, {"admin", "system_admin", "viewer"}):
        raise HTTPException(status_code=403, detail="PERMISSION_DENIED")
    return current_user
