# 部署路径: AI_Middle_Office/app/models/user.py
from sqlalchemy import Column, Integer, String, Boolean
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)

    # 角色隔离：admin (管理员) / user (普通员工)
    role = Column(String, default="user")

    # 额度管控：限制员工调用 AI 的次数，默认 10 次
    quota = Column(Integer, default=10)

    is_active = Column(Boolean, default=True)