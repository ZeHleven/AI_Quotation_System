"""Validate staged MySQL accounts over CA-verified TLS without printing secrets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from app.core.config import settings


EXPECTED_PRIVILEGES = {
    "runtime": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "migrator": {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "ALTER",
        "DROP",
        "INDEX",
    },
}


def _secure_url(username: str, password: str) -> URL:
    base = make_url(settings.database_url)
    ca_path = (
        PROJECT_ROOT / "config" / "security" / "mysql-ca-192.168.88.128.pem"
    ).resolve()
    if not ca_path.is_file():
        raise RuntimeError("CA certificate is missing")
    return base.set(
        username=username,
        password=password,
        query={
            "ssl_ca": ca_path.as_posix(),
            "ssl_check_hostname": "false",
        },
    )


def _extract_schema_privileges(grants: list[str]) -> set[str]:
    for grant in grants:
        match = re.match(
            r"GRANT (.+) ON `ai_quotation`\.\* TO ", grant, flags=re.IGNORECASE
        )
        if match:
            return {item.strip().upper() for item in match.group(1).split(",")}
    return set()


def _validate_account(kind: str, username: str, password: str) -> None:
    engine = create_engine(_secure_url(username, password), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            current_user = connection.execute(text("SELECT CURRENT_USER()")).scalar_one()
            database_name = connection.execute(text("SELECT DATABASE()")).scalar_one()
            ssl_cipher = connection.execute(
                text("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            ).one()[1]
            grants = [row[0] for row in connection.execute(text("SHOW GRANTS"))]
    finally:
        engine.dispose()

    if current_user != f"{username}@192.168.88.1":
        raise RuntimeError(f"unexpected authenticated account for {kind}")
    if database_name != "ai_quotation":
        raise RuntimeError(f"unexpected database for {kind}")
    if not ssl_cipher:
        raise RuntimeError(f"TLS is not active for {kind}")
    if any("GRANT OPTION" in grant.upper() for grant in grants):
        raise RuntimeError(f"GRANT OPTION must not be present for {kind}")
    if any("ALL PRIVILEGES" in grant.upper() for grant in grants):
        raise RuntimeError(f"ALL PRIVILEGES must not be present for {kind}")

    actual_privileges = _extract_schema_privileges(grants)
    if actual_privileges != EXPECTED_PRIVILEGES[kind]:
        raise RuntimeError(
            f"unexpected {kind} privileges: {sorted(actual_privileges)}"
        )
    print(
        f"MYSQL_STAGED_ACCOUNT_OK kind={kind} account={current_user} "
        f"cipher={ssl_cipher} privileges={','.join(sorted(actual_privileges))}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("credentials_path", type=Path)
    args = parser.parse_args()

    credentials = json.loads(args.credentials_path.read_text(encoding="utf-8"))
    for kind in ("runtime", "migrator"):
        item = credentials[kind]
        _validate_account(kind, item["username"], item["password"])
    print("MYSQL_STAGED_ACCOUNTS_VALIDATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
