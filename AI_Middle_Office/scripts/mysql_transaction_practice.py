"""Isolated MySQL transaction, constraint, optimistic-lock, and deadlock practice.

Safety properties:
- Never reads from or writes to an existing business table.
- Uses two fixed tables whose names start with ``codex_practice_``.
- Aborts if either table already exists.
- Inserts synthetic data only.
- Drops only tables created by this process, in dependency order, in ``finally``.

Run from ``AI_Middle_Office``:

    C:/Users/12521/miniconda3/python.exe -m scripts.mysql_transaction_practice
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import settings


JOBS_TABLE = "codex_practice_tx_jobs_20260730"
EVENTS_TABLE = "codex_practice_tx_events_20260730"


def _assert_safe_names() -> None:
    for table_name in (JOBS_TABLE, EVENTS_TABLE):
        if not table_name.startswith("codex_practice_"):
            raise RuntimeError("Practice table must use the codex_practice_ prefix")
        if not table_name.replace("_", "").isalnum():
            raise RuntimeError("Practice table name contains unsafe characters")


def _table_exists(conn: Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).scalar()
    )


def _mysql_error_code(exc: Exception) -> int | None:
    original = getattr(exc, "orig", None)
    args = getattr(original, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _transaction_rollback_experiment(engine) -> dict[str, Any]:
    job_id = "practice-rollback-job"
    error_code: int | None = None

    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {JOBS_TABLE}
                        (job_id, status, version, created_at)
                    VALUES
                        (:job_id, 'queued', 0, :created_at)
                    """
                ),
                {"job_id": job_id, "created_at": datetime(2026, 7, 30, 13, 0, 0)},
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {EVENTS_TABLE}
                        (job_id, event_index, event_type, created_at)
                    VALUES
                        (:job_id, 1, 'job_created', :created_at)
                    """
                ),
                {"job_id": job_id, "created_at": datetime(2026, 7, 30, 13, 0, 1)},
            )
            # Deliberately violates UNIQUE(job_id, event_index).
            conn.execute(
                text(
                    f"""
                    INSERT INTO {EVENTS_TABLE}
                        (job_id, event_index, event_type, created_at)
                    VALUES
                        (:job_id, 1, 'duplicate_event', :created_at)
                    """
                ),
                {"job_id": job_id, "created_at": datetime(2026, 7, 30, 13, 0, 2)},
            )
            transaction.commit()
        except IntegrityError as exc:
            error_code = _mysql_error_code(exc)
            transaction.rollback()

    with engine.connect() as conn:
        job_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {JOBS_TABLE} WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).scalar()
        event_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {EVENTS_TABLE} WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).scalar()

    return {
        "deliberate_error_code": error_code,
        "expected_duplicate_key_code": 1062,
        "job_rows_after_rollback": job_count,
        "event_rows_after_rollback": event_count,
        "atomicity_verified": job_count == 0 and event_count == 0,
    }


def _unique_constraint_experiment(engine) -> dict[str, Any]:
    job_id = "practice-unique-job"
    barrier = threading.Barrier(2)
    result_lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def insert(worker_id: str) -> None:
        barrier.wait()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {JOBS_TABLE}
                            (job_id, status, version, worker_id, created_at)
                        VALUES
                            (:job_id, 'queued', 0, :worker_id, NOW(6))
                        """
                    ),
                    {"job_id": job_id, "worker_id": worker_id},
                )
            outcome = {"worker_id": worker_id, "result": "inserted", "error_code": None}
        except IntegrityError as exc:
            outcome = {
                "worker_id": worker_id,
                "result": "duplicate_rejected",
                "error_code": _mysql_error_code(exc),
            }
        with result_lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=insert, args=("request-A",), daemon=False),
        threading.Thread(target=insert, args=("request-B",), daemon=False),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT job_id, status, worker_id
                FROM {JOBS_TABLE}
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        ).mappings().all()

    return {
        "requests": sorted(results, key=lambda item: item["worker_id"]),
        "stored_rows": [dict(row) for row in rows],
        "single_row_verified": len(rows) == 1,
    }


def _optimistic_lock_experiment(engine) -> dict[str, Any]:
    job_id = "practice-optimistic-job"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {JOBS_TABLE}
                    (job_id, status, version, created_at)
                VALUES
                    (:job_id, 'queued', 0, NOW(6))
                """
            ),
            {"job_id": job_id},
        )

    barrier = threading.Barrier(2)
    result_lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def update(worker_id: str) -> None:
        barrier.wait()
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    f"""
                    UPDATE {JOBS_TABLE}
                    SET status = 'running',
                        worker_id = :worker_id,
                        version = version + 1
                    WHERE job_id = :job_id
                      AND status = 'queued'
                      AND version = 0
                    """
                ),
                {"job_id": job_id, "worker_id": worker_id},
            )
        with result_lock:
            results.append(
                {"worker_id": worker_id, "affected_rows": result.rowcount}
            )

    threads = [
        threading.Thread(target=update, args=("worker-A",), daemon=False),
        threading.Thread(target=update, args=("worker-B",), daemon=False),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with engine.connect() as conn:
        final_row = conn.execute(
            text(
                f"""
                SELECT job_id, status, version, worker_id
                FROM {JOBS_TABLE}
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        ).mappings().one()

    return {
        "workers": sorted(results, key=lambda item: item["worker_id"]),
        "successful_updates": sum(item["affected_rows"] for item in results),
        "final_row": dict(final_row),
    }


def _deadlock_experiment(engine) -> dict[str, Any]:
    row_ids = ("practice-deadlock-A", "practice-deadlock-B")
    with engine.begin() as conn:
        for job_id in row_ids:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {JOBS_TABLE}
                        (job_id, status, version, counter_value, created_at)
                    VALUES
                        (:job_id, 'queued', 0, 0, NOW(6))
                    """
                ),
                {"job_id": job_id},
            )

    barrier = threading.Barrier(2)
    result_lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def opposite_order_transaction(
        worker_id: str,
        first_job_id: str,
        second_job_id: str,
    ) -> None:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET SESSION innodb_lock_wait_timeout = 5")
            conn.commit()
            transaction = conn.begin()
            try:
                conn.execute(
                    text(
                        f"""
                        SELECT id
                        FROM {JOBS_TABLE}
                        WHERE job_id = :job_id
                        FOR UPDATE
                        """
                    ),
                    {"job_id": first_job_id},
                ).one()
                conn.execute(
                    text(
                        f"""
                        UPDATE {JOBS_TABLE}
                        SET counter_value = counter_value + 1
                        WHERE job_id = :job_id
                        """
                    ),
                    {"job_id": first_job_id},
                )

                barrier.wait()

                conn.execute(
                    text(
                        f"""
                        SELECT id
                        FROM {JOBS_TABLE}
                        WHERE job_id = :job_id
                        FOR UPDATE
                        """
                    ),
                    {"job_id": second_job_id},
                ).one()
                conn.execute(
                    text(
                        f"""
                        UPDATE {JOBS_TABLE}
                        SET counter_value = counter_value + 1
                        WHERE job_id = :job_id
                        """
                    ),
                    {"job_id": second_job_id},
                )
                transaction.commit()
                outcome = {
                    "worker_id": worker_id,
                    "result": "committed",
                    "error_code": None,
                }
            except OperationalError as exc:
                transaction.rollback()
                outcome = {
                    "worker_id": worker_id,
                    "result": "deadlock"
                    if _mysql_error_code(exc) == 1213
                    else "operational_error",
                    "error_code": _mysql_error_code(exc),
                }
            except Exception:
                transaction.rollback()
                raise

        with result_lock:
            results.append(outcome)

    threads = [
        threading.Thread(
            target=opposite_order_transaction,
            args=("worker-A", row_ids[0], row_ids[1]),
            daemon=False,
        ),
        threading.Thread(
            target=opposite_order_transaction,
            args=("worker-B", row_ids[1], row_ids[0]),
            daemon=False,
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    deadlocked_workers = [
        item["worker_id"] for item in results if item["result"] == "deadlock"
    ]
    retry_success = False
    if len(deadlocked_workers) == 1:
        # Retry the entire failed unit of work with a consistent lock order.
        with engine.begin() as conn:
            for job_id in sorted(row_ids):
                conn.execute(
                    text(
                        f"""
                        SELECT id
                        FROM {JOBS_TABLE}
                        WHERE job_id = :job_id
                        FOR UPDATE
                        """
                    ),
                    {"job_id": job_id},
                ).one()
                conn.execute(
                    text(
                        f"""
                        UPDATE {JOBS_TABLE}
                        SET counter_value = counter_value + 1
                        WHERE job_id = :job_id
                        """
                    ),
                    {"job_id": job_id},
                )
        retry_success = True

    with engine.connect() as conn:
        final_rows = conn.execute(
            text(
                f"""
                SELECT job_id, counter_value
                FROM {JOBS_TABLE}
                WHERE job_id IN (:first_id, :second_id)
                ORDER BY job_id
                """
            ),
            {"first_id": row_ids[0], "second_id": row_ids[1]},
        ).mappings().all()

    return {
        "first_attempts": sorted(results, key=lambda item: item["worker_id"]),
        "deadlocked_workers": deadlocked_workers,
        "retry_policy": "retry the whole transaction once, using A -> B lock order",
        "retry_success": retry_success,
        "final_rows": [dict(row) for row in final_rows],
    }


def main() -> None:
    _assert_safe_names()
    if not settings.database_url.startswith("mysql"):
        raise RuntimeError("This practice requires MySQL")

    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=6,
        max_overflow=2,
    )
    jobs_created = False
    events_created = False
    report: dict[str, Any] = {
        "tables": [JOBS_TABLE, EVENTS_TABLE],
    }

    try:
        with engine.connect() as conn:
            report["mysql_version"] = conn.execute(text("SELECT VERSION()")).scalar()
            report["database"] = conn.execute(text("SELECT DATABASE()")).scalar()
            existing = [
                table_name
                for table_name in (JOBS_TABLE, EVENTS_TABLE)
                if _table_exists(conn, table_name)
            ]
            if existing:
                raise RuntimeError(
                    f"Safety stop: practice tables already exist and will not be touched: {existing}"
                )

        # DDL implicitly commits in MySQL, so record each successfully created table.
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f"""
                CREATE TABLE {JOBS_TABLE} (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    job_id VARCHAR(64) NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    version INT NOT NULL DEFAULT 0,
                    worker_id VARCHAR(64) NULL,
                    counter_value INT NOT NULL DEFAULT 0,
                    created_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_practice_tx_job_id (job_id)
                ) ENGINE=InnoDB
                """
            )
            jobs_created = True
            conn.exec_driver_sql(
                f"""
                CREATE TABLE {EVENTS_TABLE} (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    job_id VARCHAR(64) NOT NULL,
                    event_index INT NOT NULL,
                    event_type VARCHAR(64) NOT NULL,
                    created_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_practice_tx_event_order (job_id, event_index),
                    CONSTRAINT fk_practice_tx_event_job
                        FOREIGN KEY (job_id)
                        REFERENCES {JOBS_TABLE} (job_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB
                """
            )
            events_created = True

        report["transaction_rollback"] = _transaction_rollback_experiment(engine)
        report["unique_constraint"] = _unique_constraint_experiment(engine)
        report["optimistic_lock"] = _optimistic_lock_experiment(engine)
        report["deadlock"] = _deadlock_experiment(engine)
    finally:
        cleanup: dict[str, Any] = {}
        with engine.begin() as conn:
            if events_created and _table_exists(conn, EVENTS_TABLE):
                conn.exec_driver_sql(f"DROP TABLE {EVENTS_TABLE}")
            cleanup["events_table_absent"] = not _table_exists(conn, EVENTS_TABLE)

            if jobs_created and _table_exists(conn, JOBS_TABLE):
                conn.exec_driver_sql(f"DROP TABLE {JOBS_TABLE}")
            cleanup["jobs_table_absent"] = not _table_exists(conn, JOBS_TABLE)
        report["cleanup"] = cleanup
        engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
