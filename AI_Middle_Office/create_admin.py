"""
Emergency system_admin rescue script.

Use this only when the normal /admin/permissions user-management UI is not
usable and a system_admin account must be created or recovered.

Examples:
  python create_admin.py --confirm-rescue --username admin --password "change-me-now"
  set RESCUE_ADMIN_PASSWORD=change-me-now
  python create_admin.py --confirm-rescue
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover a system_admin account.")
    parser.add_argument("--confirm-rescue", action="store_true", help="Required safety confirmation.")
    parser.add_argument(
        "--username",
        default=os.environ.get("RESCUE_ADMIN_USERNAME", "admin"),
        help="Account username to create or recover. Defaults to admin.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("RESCUE_ADMIN_PASSWORD", ""),
        help="New temporary password. Prefer RESCUE_ADMIN_PASSWORD to avoid shell history.",
    )
    parser.add_argument("--quota", type=int, default=9999, help="AI quota to assign.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm_rescue:
        print("[ABORT] Refusing to modify accounts without --confirm-rescue.")
        print("Use the /admin/permissions UI for normal user maintenance.")
        return 2

    username = (args.username or "").strip()
    if not username:
        print("[ABORT] username is required.")
        return 2

    password = args.password or getpass.getpass("Temporary password: ")
    if len(password) < 8:
        print("[ABORT] temporary password must be at least 8 characters.")
        return 2

    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .options(selectinload(User.role_assignments))
            .filter(User.username == username)
            .first()
        )
        created = False
        if user is None:
            user = User(
                username=username,
                hashed_password=get_password_hash(password),
                role="admin",
                role_version=1,
                quota=args.quota,
                is_active=True,
                must_change_password=True,
            )
            db.add(user)
            db.flush()
            created = True
        else:
            user.hashed_password = get_password_hash(password)
            user.role = "admin"
            user.role_version = int(user.role_version or 1) + 1
            user.quota = args.quota
            user.is_active = True
            user.must_change_password = True
            db.flush()

        assigned_roles = {assignment.role for assignment in (user.role_assignments or [])}
        if "system_admin" not in assigned_roles:
            db.add(UserRole(user_id=user.id, role="system_admin", created_by=None, note="rescue_script"))
            if not created:
                user.role_version = int(user.role_version or 1) + 1

        db.commit()
        action = "created" if created else "recovered"
        print(f"[OK] {action} system_admin account '{username}'.")
        print("[INFO] The user must change the temporary password on next login.")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"[FAIL] account rescue failed: {exc}")
        print("[HINT] Ensure database migrations have been applied: alembic upgrade head")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
