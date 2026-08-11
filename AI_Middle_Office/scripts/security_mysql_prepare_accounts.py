"""Stage strong MySQL account credentials and an administrator SQL script.

The generated files contain secrets and must stay in a protected temporary
directory. This script deliberately prints no credentials.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets


RUNTIME_USER = "ai_runtime"
MIGRATOR_USER = "ai_migrator"
SOURCE_HOST = "192.168.88.1"
DATABASE_NAME = "ai_quotation"


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    credentials_path = output_dir / "mysql-cutover-credentials.json"
    sql_path = output_dir / "mysql-create-accounts.sql"
    if credentials_path.exists() or sql_path.exists():
        raise SystemExit("MYSQL_ACCOUNT_STAGE_FAILED: output files already exist")

    credentials = {
        "runtime": {
            "username": RUNTIME_USER,
            "password": secrets.token_urlsafe(32),
        },
        "migrator": {
            "username": MIGRATOR_USER,
            "password": secrets.token_urlsafe(32),
        },
    }
    credentials_path.write_text(
        json.dumps(credentials, ensure_ascii=True), encoding="utf-8"
    )
    os.chmod(credentials_path, 0o600)

    runtime_password = _sql_literal(credentials["runtime"]["password"])
    migrator_password = _sql_literal(credentials["migrator"]["password"])
    sql = f"""\
CREATE USER '{RUNTIME_USER}'@'{SOURCE_HOST}'
  IDENTIFIED WITH caching_sha2_password BY {runtime_password}
  REQUIRE SSL PASSWORD EXPIRE NEVER;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON `{DATABASE_NAME}`.* TO '{RUNTIME_USER}'@'{SOURCE_HOST}';

CREATE USER '{MIGRATOR_USER}'@'{SOURCE_HOST}'
  IDENTIFIED WITH caching_sha2_password BY {migrator_password}
  REQUIRE SSL PASSWORD EXPIRE NEVER;
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
  ON `{DATABASE_NAME}`.* TO '{MIGRATOR_USER}'@'{SOURCE_HOST}';
"""
    sql_path.write_text(sql, encoding="utf-8", newline="\n")
    os.chmod(sql_path, 0o600)

    print("MYSQL_ACCOUNT_SECRETS_STAGED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
