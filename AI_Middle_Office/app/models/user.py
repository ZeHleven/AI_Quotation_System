# 部署路径: AI_Middle_Office/app/models/user.py
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True)
    hashed_password = Column(String(256))

    # 兼容旧页面：admin (管理员) / user (普通员工)；真实权限以 user_roles 为准。
    role = Column(String(16), default="user")
    role_version = Column(Integer, nullable=False, default=1, server_default="1")
    dingtalk_user_id = Column(String(128), unique=True, nullable=True)
    dingtalk_bound_at = Column(DateTime(timezone=True), nullable=True)

    # 额度管控：限制员工调用 AI 的次数，默认 10 次
    quota = Column(Integer, default=10)

    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)

    role_assignments = relationship(
        "UserRole",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserRole.user_id",
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role", name="uq_user_roles_user_id_role"),)

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)

    user = relationship("User", foreign_keys=[user_id], back_populates="role_assignments")


class UserRoleEvent(Base):
    __tablename__ = "user_role_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False, index=True)
    action = Column(String(24), nullable=False, index=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    note = Column(Text, nullable=True)
