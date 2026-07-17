"""Customer-account tenancy for budget-project data.

P2-2A scopes budget workspaces and mutable pricing drafts to an account.  The
account is resolved from the authenticated user's active membership; API
payloads never select an account id.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.database import Base


ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_ARCHIVED = "archived"
ACCOUNT_MEMBERSHIP_STATUS_ACTIVE = "active"
ACCOUNT_MEMBERSHIP_STATUS_DISABLED = "disabled"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("account_uuid", name="uq_accounts_uuid"),
        UniqueConstraint("account_code", name="uq_accounts_code"),
        Index("ix_accounts_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    account_uuid = Column(String(36), nullable=False)
    account_code = Column(String(64), nullable=False)
    account_name = Column(String(255), nullable=False)
    status = Column(
        String(24),
        nullable=False,
        default=ACCOUNT_STATUS_ACTIVE,
        server_default=ACCOUNT_STATUS_ACTIVE,
        index=True,
    )
    is_internal_default = Column(Boolean, nullable=False, default=False, server_default="0")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    memberships = relationship("AccountMembership", back_populates="account")
    budget_projects = relationship("AccountBudgetProject", back_populates="account")


class AccountMembership(Base):
    __tablename__ = "account_memberships"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_account_memberships_account_user"),
        Index("ix_account_memberships_user_status", "user_id", "status"),
        Index("ix_account_memberships_account_status", "account_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    member_role = Column(String(32), nullable=False, default="member", server_default="member")
    status = Column(
        String(24),
        nullable=False,
        default=ACCOUNT_MEMBERSHIP_STATUS_ACTIVE,
        server_default=ACCOUNT_MEMBERSHIP_STATUS_ACTIVE,
        index=True,
    )
    is_default = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    account = relationship("Account", back_populates="memberships")
    user = relationship("User", foreign_keys=[user_id])


class AccountBudgetProject(Base):
    """Unique tenant binding for a canonical project used as a budget workspace."""

    __tablename__ = "account_budget_projects"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_account_budget_projects_project"),
        UniqueConstraint("account_id", "project_id", name="uq_account_budget_projects_account_project"),
        Index("ix_account_budget_projects_account_created", "account_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    account = relationship("Account", back_populates="budget_projects")
    project = relationship("Project")
