"""Isolated MySQL practice for SQL, EXPLAIN, indexing, and atomic claims.

Safety properties:
- Never reads from or writes to an existing business table.
- Uses one fixed table whose name starts with ``codex_practice_``.
- Aborts if that table already exists.
- Drops only the table created by this process in ``finally``.
- Inserts synthetic data only.

Run from ``AI_Middle_Office``:

    C:/Users/12521/miniconda3/python.exe -m scripts.mysql_backend_foundation_practice
"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from app.core.config import settings


TABLE_NAME = "codex_practice_quote_jobs_20260730"
ROW_COUNT = 60_000
BENCHMARK_ROUNDS = 80
TARGET_USER = "user_018"
TARGET_STATUS = "succeeded"
CONCURRENT_JOB_ID = "practice-concurrent-claim"


def _assert_safe_table_name() -> None:
    if not TABLE_NAME.startswith("codex_practice_"):
        raise RuntimeError("Practice table must use the codex_practice_ prefix")
    if not TABLE_NAME.replace("_", "").isalnum():
        raise RuntimeError("Practice table name contains unsafe characters")


def _table_exists(conn: Connection) -> bool:
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
            {"table_name": TABLE_NAME},
        ).scalar()
    )


def _create_table(conn: Connection) -> None:
    conn.exec_driver_sql(
        f"""
        CREATE TABLE {TABLE_NAME} (
            id BIGINT NOT NULL AUTO_INCREMENT,
            job_id CHAR(36) NOT NULL,
            username VARCHAR(64) NOT NULL,
            status VARCHAR(24) NOT NULL,
            failure_stage VARCHAR(64) NULL,
            duration_ms INT NULL,
            worker_id VARCHAR(64) NULL,
            created_at DATETIME(6) NOT NULL,
            started_at DATETIME(6) NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_practice_job_id (job_id)
        ) ENGINE=InnoDB
        """
    )


def _synthetic_rows() -> list[tuple[Any, ...]]:
    statuses = ("queued", "running", "succeeded", "failed", "cancelled", "timed_out")
    failure_stages = (None, "file_parse", "rag", "model", "n8n")
    start = datetime(2026, 1, 1)
    rows: list[tuple[Any, ...]] = []
    for index in range(ROW_COUNT):
        status = statuses[index % len(statuses)]
        rows.append(
            (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"practice-job-{index}")),
                f"user_{index % 50:03d}",
                status,
                failure_stages[index % len(failure_stages)] if status == "failed" else None,
                1_000 + (index * 37) % 180_000 if status == "succeeded" else None,
                start + timedelta(seconds=index * 17),
            )
        )
    return rows


def _insert_rows(conn: Connection) -> None:
    insert_sql = f"""
        INSERT INTO {TABLE_NAME}
            (job_id, username, status, failure_stage, duration_ms, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    rows = _synthetic_rows()
    batch_size = 2_000
    for start in range(0, len(rows), batch_size):
        conn.exec_driver_sql(insert_sql, rows[start : start + batch_size])
    conn.exec_driver_sql(
        f"""
        INSERT INTO {TABLE_NAME}
            (job_id, username, status, failure_stage, duration_ms, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            CONCURRENT_JOB_ID,
            "concurrency_user",
            "queued",
            None,
            None,
            datetime(2026, 7, 30, 12, 0, 0),
        ),
    )


def _query_sql() -> str:
    return f"""
        SELECT job_id, status, duration_ms, created_at
        FROM {TABLE_NAME}
        WHERE username = :username
          AND status = :status
        ORDER BY created_at DESC
        LIMIT 20
    """


def _explain(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(f"EXPLAIN {_query_sql()}"),
        {"username": TARGET_USER, "status": TARGET_STATUS},
    ).mappings()
    return [{key: value for key, value in row.items()} for row in rows]


def _benchmark(conn: Connection) -> dict[str, float]:
    statement = text(_query_sql())
    params = {"username": TARGET_USER, "status": TARGET_STATUS}
    result_rows = conn.execute(statement, params).all()
    timings: list[float] = []
    for _ in range(BENCHMARK_ROUNDS):
        started = time.perf_counter()
        conn.execute(statement, params).all()
        timings.append((time.perf_counter() - started) * 1000)
    ordered = sorted(timings)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "rounds": float(BENCHMARK_ROUNDS),
        "result_rows": float(len(result_rows)),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
    }


def _run_atomic_claim(engine) -> dict[str, Any]:
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []
    result_lock = threading.Lock()

    def claim(worker_id: str) -> None:
        barrier.wait()
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    f"""
                    UPDATE {TABLE_NAME}
                    SET status = 'running',
                        worker_id = :worker_id,
                        started_at = NOW(6)
                    WHERE job_id = :job_id
                      AND status = 'queued'
                    """
                ),
                {"worker_id": worker_id, "job_id": CONCURRENT_JOB_ID},
            )
            affected = result.rowcount
        with result_lock:
            results.append({"worker_id": worker_id, "affected_rows": affected})

    threads = [
        threading.Thread(target=claim, args=("worker-A",), daemon=False),
        threading.Thread(target=claim, args=("worker-B",), daemon=False),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with engine.connect() as conn:
        final_row = conn.execute(
            text(
                f"""
                SELECT job_id, status, worker_id
                FROM {TABLE_NAME}
                WHERE job_id = :job_id
                """
            ),
            {"job_id": CONCURRENT_JOB_ID},
        ).mappings().one()

    return {
        "workers": sorted(results, key=lambda item: item["worker_id"]),
        "successful_claims": sum(item["affected_rows"] for item in results),
        "final_row": dict(final_row),
    }


def main() -> None:
    _assert_safe_table_name()
    if not settings.database_url.startswith("mysql"):
        raise RuntimeError("This practice requires an isolated table on MySQL")

    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=2,
    )
    created_by_this_process = False
    report: dict[str, Any] = {
        "table_name": TABLE_NAME,
        "synthetic_rows": ROW_COUNT + 1,
    }

    try:
        with engine.begin() as conn:
            report["mysql_version"] = conn.execute(text("SELECT VERSION()")).scalar()
            report["database"] = conn.execute(text("SELECT DATABASE()")).scalar()
            if _table_exists(conn):
                raise RuntimeError(
                    f"Safety stop: {TABLE_NAME} already exists; it will not be touched"
                )
            _create_table(conn)
            created_by_this_process = True
            _insert_rows(conn)

        with engine.connect() as conn:
            conn.exec_driver_sql(f"ANALYZE TABLE {TABLE_NAME}").all()
            report["before_index"] = {
                "explain": _explain(conn),
                "benchmark": _benchmark(conn),
            }

        with engine.begin() as conn:
            conn.exec_driver_sql(
                f"""
                CREATE INDEX ix_practice_user_status_created
                ON {TABLE_NAME} (username, status, created_at)
                """
            )

        with engine.connect() as conn:
            conn.exec_driver_sql(f"ANALYZE TABLE {TABLE_NAME}").all()
            report["after_index"] = {
                "explain": _explain(conn),
                "benchmark": _benchmark(conn),
            }

        report["atomic_claim"] = _run_atomic_claim(engine)
    finally:
        if created_by_this_process:
            with engine.begin() as conn:
                if _table_exists(conn):
                    conn.exec_driver_sql(f"DROP TABLE {TABLE_NAME}")
                report["cleanup_verified"] = not _table_exists(conn)
        engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
