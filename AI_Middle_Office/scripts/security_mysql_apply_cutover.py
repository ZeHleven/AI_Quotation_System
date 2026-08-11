"""Atomically switch .env to dedicated MySQL runtime and migration accounts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CA_PATH = (
    PROJECT_ROOT / "config" / "security" / "mysql-ca-192.168.88.128.pem"
).resolve()


def _replace_env_value(content: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^(?:\ufeff)?{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(content):
        return pattern.sub(replacement, content, count=1)
    separator = "" if not content or content.endswith(("\n", "\r")) else "\n"
    return f"{content}{separator}{replacement}\n"


def _account_url(base_url: str, username: str, password: str) -> str:
    base = make_url(base_url)
    query = {
        key: value
        for key, value in base.query.items()
        if not key.startswith("ssl_")
    }
    query.update(
        {
            "ssl_ca": CA_PATH.as_posix(),
            "ssl_check_hostname": "false",
        }
    )
    return base.set(
        username=username,
        password=password,
        query=query,
    ).render_as_string(hide_password=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("credentials_path", type=Path)
    parser.add_argument("backup_path", type=Path)
    args = parser.parse_args()

    if not ENV_PATH.is_file():
        raise SystemExit("MYSQL_ENV_CUTOVER_FAILED: .env is missing")
    if not CA_PATH.is_file():
        raise SystemExit("MYSQL_ENV_CUTOVER_FAILED: CA certificate is missing")

    backup_path = args.backup_path.resolve()
    if backup_path.exists():
        raise SystemExit("MYSQL_ENV_CUTOVER_FAILED: backup already exists")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ENV_PATH, backup_path)
    os.chmod(backup_path, 0o600)

    credentials = json.loads(args.credentials_path.read_text(encoding="utf-8"))
    original = ENV_PATH.read_text(encoding="utf-8")
    match = re.search(r"^(?:\ufeff)?DATABASE_URL=(.+)$", original, re.MULTILINE)
    if not match:
        raise SystemExit("MYSQL_ENV_CUTOVER_FAILED: DATABASE_URL is missing")
    base_url = match.group(1).strip()

    runtime = credentials["runtime"]
    migrator = credentials["migrator"]
    runtime_url = _account_url(base_url, runtime["username"], runtime["password"])
    migration_url = _account_url(
        base_url, migrator["username"], migrator["password"]
    )
    updated = _replace_env_value(original, "DATABASE_URL", runtime_url)
    updated = _replace_env_value(updated, "MIGRATION_DATABASE_URL", migration_url)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".env.mysql-cutover-", dir=ENV_PATH.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, ENV_PATH)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    print("MYSQL_ENV_CUTOVER_APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
