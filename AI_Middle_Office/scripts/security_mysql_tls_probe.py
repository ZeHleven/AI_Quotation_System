"""Probe the configured MySQL account over CA-verified TLS without exposing secrets."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.core.config import settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--migration",
        action="store_true",
        help="Probe MIGRATION_DATABASE_URL instead of DATABASE_URL.",
    )
    args = parser.parse_args()

    ca_path = (
        PROJECT_ROOT / "config" / "security" / "mysql-ca-192.168.88.128.pem"
    ).resolve()
    if not ca_path.is_file():
        raise SystemExit("MYSQL_TLS_PROBE_FAILED: CA certificate is missing")

    raw_url = settings.alembic_database_url if args.migration else settings.database_url
    url = make_url(raw_url)
    query = dict(url.query)
    query.update(
        {
            "ssl_ca": ca_path.as_posix(),
            # PyMySQL requires a CA when hostname verification is disabled;
            # that combination still enforces CERT_REQUIRED against this CA.
            # The MySQL auto-generated server certificate has no IP SAN, so
            # identity verification cannot be enabled for 192.168.88.128.
            "ssl_check_hostname": "false",
        }
    )
    secure_url = url.set(query=query)
    engine = create_engine(secure_url, poolclass=NullPool)

    try:
        with engine.connect() as connection:
            ssl_cipher = connection.execute(
                text("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            ).one()[1]
            current_user = connection.execute(text("SELECT CURRENT_USER()")).scalar_one()
            database_name = connection.execute(text("SELECT DATABASE()")).scalar_one()
    finally:
        engine.dispose()

    if not ssl_cipher:
        raise SystemExit("MYSQL_TLS_PROBE_FAILED: session is not encrypted")
    print(
        "MYSQL_TLS_CA_VERIFIED "
        f"account={current_user} database={database_name} cipher={ssl_cipher}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
