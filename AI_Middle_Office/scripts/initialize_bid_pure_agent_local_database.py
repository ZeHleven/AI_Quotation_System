"""Create the isolated C08 SQLite schema snapshot at development head 0110.

Historical production migrations include dialect-specific constraint changes
that SQLite cannot replay.  The C08 local boundary therefore builds the current
ORM metadata in a same-directory temporary database, creates one inactive
migration owner with a random discarded credential, validates the required
Pure Agent tables, stamps the isolated head, and atomically publishes the file.
It never upgrades or rewrites an existing database.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import uuid

import bcrypt
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
EXPECTED_HEAD = "20260821_0110"
_LOCAL_DIRECTORY = re.compile(
    r"^\.local-pure-agent-daily(?:-[a-z0-9][a-z0-9-]{0,31})?$"
)
_REQUIRED_PURE_AGENT_TABLES = frozenset(
    {
        "bid_pa_conversations",
        "bid_pa_tasks",
        "bid_pa_events",
        "bid_pa_observation_artifacts",
        "bid_pa_calls",
        "bid_pa_responses",
    }
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    return parser.parse_args()


def _validated_database_path(raw_path: Path) -> Path:
    path = raw_path.resolve(strict=False)
    if path.name != "runtime.db":
        raise RuntimeError("local Pure Agent database target is invalid")
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError:
        raise RuntimeError("local Pure Agent database is outside the project") from None
    if not _LOCAL_DIRECTORY.fullmatch(path.parent.name):
        raise RuntimeError("local Pure Agent database directory is not allowlisted")
    if not path.parent.is_dir():
        raise RuntimeError("local Pure Agent database directory does not exist")
    return path


def _existing_head(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def initialize_local_database(database_path: Path) -> bool:
    target = _validated_database_path(database_path)
    if target.exists():
        if _existing_head(target) == EXPECTED_HEAD:
            return False
        raise RuntimeError("existing local database is not at the expected head")

    temporary = target.parent / f".runtime-{uuid.uuid4().hex}.tmp.db"
    engine = create_engine(
        f"sqlite:///{temporary.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    completed = False
    try:
        import app.agents.bid_assessment_pure.persistence_models  # noqa: F401
        import app.models.registry  # noqa: F401
        from app.core.database import Base
        from app.models.user import User, UserRole

        Base.metadata.create_all(bind=engine)
        discarded_password = secrets.token_bytes(48)
        disabled_hash = bcrypt.hashpw(discarded_password, bcrypt.gensalt()).decode(
            "ascii"
        )
        with Session(engine) as session:
            owner = User(
                username="admin",
                hashed_password=disabled_hash,
                role="admin",
                role_version=1,
                quota=0,
                quota_reserved=0,
                is_active=False,
                must_change_password=True,
            )
            session.add(owner)
            session.flush()
            session.add_all(
                (
                    UserRole(
                        user_id=owner.id,
                        role="admin",
                        created_by=None,
                        note="c08_local_schema_bootstrap",
                    ),
                    UserRole(
                        user_id=owner.id,
                        role="system_admin",
                        created_by=None,
                        note="c08_local_schema_bootstrap",
                    ),
                )
            )
            session.commit()

        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:head)"),
                {"head": EXPECTED_HEAD},
            )

        tables = set(inspect(engine).get_table_names())
        missing = _REQUIRED_PURE_AGENT_TABLES - tables
        if missing:
            raise RuntimeError("local Pure Agent schema snapshot is incomplete")
        completed = True
    finally:
        engine.dispose()
        if not completed and temporary.is_file():
            temporary.unlink()

    temporary.replace(target)
    return True


def main() -> int:
    created = initialize_local_database(_arguments().database)
    print("local schema snapshot created" if created else "local schema already ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
