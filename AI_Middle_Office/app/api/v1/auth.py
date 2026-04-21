# 部署路径: AI_Middle_Office/app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.core.security import get_password_hash, verify_password, create_access_token
from pydantic import BaseModel

router = APIRouter()


class UserCreate(BaseModel):
    username: str
    password: str


@router.post("/register", summary="注册新员工账号")
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="该用户名已存在")

    hashed_pwd = get_password_hash(user.password)
    # 给新注册的账号默认分配 5 次 AI 调用体验额度
    new_user = User(username=user.username, hashed_password=hashed_pwd, quota=5)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": f"账号 {new_user.username} 注册成功，获得 {new_user.quota} 次查询额度！"}


@router.post("/login", summary="账号登录获取 Token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 签发包含用户身份的 JWT
    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer", "role": user.role, "username": user.username}