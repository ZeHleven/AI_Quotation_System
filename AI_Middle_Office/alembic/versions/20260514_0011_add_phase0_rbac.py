"""add phase0 rbac roles and role version

Revision ID: 20260514_0011
Revises: 20260514_0010
Create Date: 2026-05-14
"""
from __future__ import annotations

import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0011"
down_revision: Union[str, None] = "20260514_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VALID_ROLES = {"system_admin", "admin", "staff", "manager", "viewer"}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _unique_constraints(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    existing = _indexes(table_name) | _unique_constraints(table_name)
    if name not in existing:
        op.create_index(name, table_name, columns, unique=unique)


def _drop_index_if_exists(name: str, table_name: str) -> None:
    existing = _indexes(table_name) | _unique_constraints(table_name)
    if name in existing:
        op.drop_index(name, table_name=table_name)


def _role_exists(conn, user_id: int, role: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM user_roles WHERE user_id = :user_id AND role = :role"),
            {"user_id": user_id, "role": role},
        ).first()
    )


def _grant_migrated_role(conn, user_id: int, role: str, note: str) -> None:
    if role not in VALID_ROLES or _role_exists(conn, user_id, role):
        return
    conn.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role, created_by, note) "
            "VALUES (:user_id, :role, NULL, :note)"
        ),
        {"user_id": user_id, "role": role, "note": note},
    )
    conn.execute(
        sa.text(
            "INSERT INTO user_role_events "
            "(target_user_id, role, action, operator_id, ip_address, user_agent, trace_id, note) "
            "VALUES (:user_id, :role, 'granted', NULL, '0.0.0.0', "
            "'system:phase0_rbac_migration', 'system-phase0-rbac-migration', :note)"
        ),
        {"user_id": user_id, "role": role, "note": note},
    )


def _legacy_roles(legacy_role: str | None) -> list[str]:
    if legacy_role == "admin":
        return ["admin"]
    if legacy_role == "user":
        return ["staff"]
    if legacy_role in VALID_ROLES:
        return [legacy_role]
    return []


def _migrate_existing_roles() -> None:
    conn = op.get_bind()
    if "users" not in _tables():
        return

    system_admin_username = os.environ.get("SYSTEM_ADMIN_USERNAME", "admin").strip() or "admin"
    system_admin = conn.execute(
        sa.text("SELECT id FROM users WHERE username = :username"),
        {"username": system_admin_username},
    ).first()
    if system_admin is None:
        raise RuntimeError(
            f"SYSTEM_ADMIN_USERNAME '{system_admin_username}' does not exist; "
            "Phase 0 RBAC migration aborted to avoid an unmanaged system."
        )

    users = conn.execute(sa.text("SELECT id, username, role FROM users")).mappings().all()
    for row in users:
        for role in _legacy_roles(row["role"]):
            _grant_migrated_role(conn, row["id"], role, "phase0 migration from users.role")
        if row["username"] == system_admin_username:
            _grant_migrated_role(conn, row["id"], "admin", "phase0 system admin bootstrap")
            _grant_migrated_role(conn, row["id"], "system_admin", "phase0 system admin bootstrap")


def upgrade() -> None:
    if "users" in _tables():
        _add_column_if_missing(
            "users",
            sa.Column("role_version", sa.Integer(), nullable=False, server_default="1"),
        )
        _add_column_if_missing("users", sa.Column("dingtalk_user_id", sa.String(length=128), nullable=True))
        _add_column_if_missing("users", sa.Column("dingtalk_bound_at", sa.DateTime(timezone=True), nullable=True))
        _create_index_if_missing("uq_users_dingtalk_user_id", "users", ["dingtalk_user_id"], unique=True)
        op.execute(sa.text("UPDATE users SET role_version = 1 WHERE role_version IS NULL OR role_version < 1"))

    if "user_roles" not in _tables():
        op.create_table(
            "user_roles",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.UniqueConstraint("user_id", "role", name="uq_user_roles_user_id_role"),
        )
    _create_index_if_missing("ix_user_roles_id", "user_roles", ["id"])
    _create_index_if_missing("ix_user_roles_user_id", "user_roles", ["user_id"])
    _create_index_if_missing("ix_user_roles_role", "user_roles", ["role"])
    _create_index_if_missing("uq_user_roles_user_id_role", "user_roles", ["user_id", "role"], unique=True)

    if "user_role_events" not in _tables():
        op.create_table(
            "user_role_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("action", sa.String(length=24), nullable=False),
            sa.Column("operator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
        )
    _create_index_if_missing("ix_user_role_events_id", "user_role_events", ["id"])
    _create_index_if_missing("ix_user_role_events_target_user_id", "user_role_events", ["target_user_id"])
    _create_index_if_missing("ix_user_role_events_role", "user_role_events", ["role"])
    _create_index_if_missing("ix_user_role_events_action", "user_role_events", ["action"])
    _create_index_if_missing("ix_user_role_events_operator_id", "user_role_events", ["operator_id"])
    _create_index_if_missing("ix_user_role_events_trace_id", "user_role_events", ["trace_id"])

    _migrate_existing_roles()


def downgrade() -> None:
    if "user_role_events" in _tables():
        op.drop_table("user_role_events")
    if "user_roles" in _tables():
        op.drop_table("user_roles")
    if "users" in _tables():
        _drop_index_if_exists("uq_users_dingtalk_user_id", "users")
        _drop_column_if_exists("users", "dingtalk_bound_at")
        _drop_column_if_exists("users", "dingtalk_user_id")
        _drop_column_if_exists("users", "role_version")
