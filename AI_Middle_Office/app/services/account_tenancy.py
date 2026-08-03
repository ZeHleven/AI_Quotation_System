"""Fail-closed account resolution for budget-project tenancy."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.account import (
    ACCOUNT_MEMBERSHIP_STATUS_ACTIVE,
    ACCOUNT_STATUS_ACTIVE,
    Account,
    AccountBudgetProject,
    AccountMembership,
)
from app.models.user import User


class AccountTenancyError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 403, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def resolve_current_account(
    db: Session,
    current_user: User,
    *,
    for_update: bool = False,
) -> Account:
    """Resolve the authenticated user's default active account.

    There is deliberately no fallback to the first/global account.  A missing
    or ambiguous membership fails closed so a caller cannot cross tenants by
    omitting account metadata.
    """

    query = (
        db.query(AccountMembership, Account)
        .join(Account, Account.id == AccountMembership.account_id)
        .filter(
            AccountMembership.user_id == current_user.id,
            AccountMembership.status == ACCOUNT_MEMBERSHIP_STATUS_ACTIVE,
            Account.status == ACCOUNT_STATUS_ACTIVE,
        )
        .order_by(AccountMembership.is_default.desc(), AccountMembership.id.asc())
    )
    if for_update:
        query = query.with_for_update()
    rows = query.all()
    if not rows:
        raise AccountTenancyError("ACCOUNT_MEMBERSHIP_REQUIRED")
    defaults = [row for row in rows if bool(row[0].is_default)]
    if len(defaults) == 1:
        return defaults[0][1]
    if len(defaults) > 1:
        raise AccountTenancyError(
            "ACCOUNT_DEFAULT_MEMBERSHIP_AMBIGUOUS",
            status_code=409,
            context={"membership_ids": [int(row[0].id) for row in defaults]},
        )
    if len(rows) == 1:
        return rows[0][1]
    raise AccountTenancyError(
        "ACCOUNT_DEFAULT_MEMBERSHIP_REQUIRED",
        status_code=409,
        context={"membership_ids": [int(row[0].id) for row in rows]},
    )


def assign_user_to_account(
    db: Session,
    *,
    account: Account,
    target_user: User,
    actor: User | None,
    member_role: str = "member",
    make_default: bool = True,
) -> AccountMembership:
    """Internal provisioning helper; never expose ``account_id`` in an API payload."""

    membership = (
        db.query(AccountMembership)
        .filter(
            AccountMembership.account_id == account.id,
            AccountMembership.user_id == target_user.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if make_default:
        (
            db.query(AccountMembership)
            .filter(
                AccountMembership.user_id == target_user.id,
                AccountMembership.is_default.is_(True),
            )
            .update({AccountMembership.is_default: False}, synchronize_session=False)
        )
    if membership is None:
        membership = AccountMembership(
            account_id=account.id,
            user_id=target_user.id,
            member_role=(member_role or "member")[:32],
            status=ACCOUNT_MEMBERSHIP_STATUS_ACTIVE,
            is_default=make_default,
            created_by=actor.id if actor else None,
        )
        db.add(membership)
    else:
        membership.member_role = (member_role or membership.member_role or "member")[:32]
        membership.status = ACCOUNT_MEMBERSHIP_STATUS_ACTIVE
        membership.is_default = make_default or bool(membership.is_default)
    db.flush()
    return membership


def assign_user_to_operator_default_account(
    db: Session,
    *,
    target_user: User,
    operator: User,
) -> AccountMembership:
    account = resolve_current_account(db, operator, for_update=True)
    return assign_user_to_account(
        db,
        account=account,
        target_user=target_user,
        actor=operator,
        member_role="member",
        make_default=True,
    )


def bind_budget_project_to_current_account(
    db: Session,
    *,
    project_id: int,
    current_user: User,
) -> AccountBudgetProject:
    account = resolve_current_account(db, current_user, for_update=True)
    existing = (
        db.query(AccountBudgetProject)
        .filter(AccountBudgetProject.project_id == project_id)
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        if int(existing.account_id) != int(account.id):
            raise AccountTenancyError("BUDGET_PROJECT_ACCOUNT_CONFLICT", status_code=409)
        return existing
    binding = AccountBudgetProject(
        account_id=account.id,
        project_id=project_id,
        created_by=current_user.id,
    )
    db.add(binding)
    db.flush()
    return binding


def require_budget_project_account(
    db: Session,
    *,
    project_id: int,
    current_user: User,
    for_update: bool = False,
) -> tuple[Account, AccountBudgetProject]:
    account = resolve_current_account(db, current_user, for_update=for_update)
    query = db.query(AccountBudgetProject).filter(
        AccountBudgetProject.project_id == project_id,
        AccountBudgetProject.account_id == account.id,
    )
    if for_update:
        query = query.with_for_update()
    binding = query.one_or_none()
    if binding is None:
        # A 404 avoids revealing whether another account owns the project.
        raise AccountTenancyError("BUDGET_PROJECT_NOT_FOUND", status_code=404)
    return account, binding
