"""Read-only MySQL credential and transport inventory without secret output."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.core.database import engine
from app.core.config import settings


def _scalar(connection, sql: str, default=None):
    try:
        return connection.execute(text(sql)).scalar()
    except Exception:
        return default


def main() -> int:
    url = engine.url
    username = (url.username or "").lower()
    password_length = len(url.password or "")
    tls_requested = any(key.lower().startswith("ssl") for key in url.query)
    print(
        "APP_DATABASE_CONFIG "
        f"driver={url.drivername} host={url.host} port={url.port} database={url.database} "
        f"password_length={password_length} configured_user_root_like="
        f"{username in {'root', 'admin', 'administrator'}} tls_requested={tls_requested}"
    )
    migration_url = settings.migration_database_url
    if not migration_url:
        print("MIGRATION_DATABASE_CONFIG configured=false distinct_from_runtime=false")
    else:
        parsed_migration = make_url(migration_url)
        print(
            "MIGRATION_DATABASE_CONFIG "
            f"configured=true distinct_from_runtime={migration_url != settings.database_url} "
            f"host_matches_runtime={parsed_migration.host == url.host} "
            f"database_matches_runtime={parsed_migration.database == url.database} "
            f"password_length={len(parsed_migration.password or '')}"
        )

    with engine.connect() as connection:
        current_user = str(_scalar(connection, "SELECT CURRENT_USER()", ""))
        current_name, _, current_host = current_user.partition("@")
        version = str(_scalar(connection, "SELECT VERSION()", "unknown"))
        grants = []
        try:
            grants = [str(row[0]) for row in connection.execute(text("SHOW GRANTS FOR CURRENT_USER"))]
        except Exception:
            pass
        try:
            ssl_row = connection.execute(text("SHOW SESSION STATUS LIKE 'Ssl_cipher'")).one_or_none()
            ssl_cipher = str(ssl_row[1]) if ssl_row else ""
        except Exception:
            ssl_cipher = ""
        require_transport = _scalar(connection, "SELECT @@require_secure_transport", "unknown")
        active_schema_connections = _scalar(
            connection,
            "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE DB = DATABASE()",
            "unknown",
        )
        print(
            "CURRENT_DATABASE_ACCOUNT "
            f"connected=true root_like={current_name.lower() == 'root'} "
            f"host_wildcard={current_host == '%'} "
            f"all_privileges={any('ALL PRIVILEGES' in grant.upper() for grant in grants)} "
            f"grant_option={any('GRANT OPTION' in grant.upper() for grant in grants)}"
        )
        normalized_grants = "\n".join(grant.upper() for grant in grants)
        privilege_flags = {
            privilege: privilege in normalized_grants
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "CREATE",
                "ALTER",
                "DROP",
                "INDEX",
                "EXECUTE",
                "TRIGGER",
                "EVENT",
            )
        }
        has_effective_global_grant = any(
            " ON *.* " in grant.upper() and not grant.upper().startswith("GRANT USAGE ")
            for grant in grants
        )
        print(
            "CURRENT_DATABASE_PRIVILEGES "
            + " ".join(f"{name.lower()}={value}" for name, value in privilege_flags.items())
            + f" global_scope={has_effective_global_grant}"
        )
        print(
            "DATABASE_TRANSPORT "
            f"server_family={version.split('-')[0]} require_secure_transport={require_transport} "
            f"tls_active={bool(ssl_cipher)} active_schema_connections={active_schema_connections}"
        )

        try:
            accounts = list(
                connection.execute(
                    text(
                        "SELECT User, Host, plugin FROM mysql.user"
                    )
                )
            )
        except Exception:
            print("DATABASE_ACCOUNT_CATALOG readable=false")
        else:
            legacy_plugins = {"mysql_old_password", "mysql_native_password"}
            print(
                "DATABASE_ACCOUNT_CATALOG "
                f"readable=true total={len(accounts)} "
                f"anonymous={sum(not str(row[0]) for row in accounts)} "
                f"root_named={sum(str(row[0]).lower() == 'root' for row in accounts)} "
                f"wildcard_host={sum(str(row[1]) == '%' for row in accounts)} "
                f"legacy_auth_plugin={sum(str(row[2]).lower() in legacy_plugins for row in accounts)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
