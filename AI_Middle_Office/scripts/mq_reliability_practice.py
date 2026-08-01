"""Isolated Celery/Redis message reliability practice.

The exercise covers:
- Outbox-style event creation.
- Duplicate publish and idempotent consumption.
- Bounded retry for a transient error.
- Application-level dead-letter recording for a poison message.

Safety properties:
- Uses Redis logical DB 15, never Celery production DB 0/1.
- Requires DB 15 to be empty before starting.
- Uses a random queue name and task IDs.
- Starts one hidden Celery ``solo`` worker and stops that exact subprocess.
- Never calls FLUSHDB/FLUSHALL; it deletes the exact final-minus-initial Key set.
- Uses fixed synthetic MySQL tables with the ``codex_practice_`` prefix.
- Aborts if any practice table already exists.
- Drops only tables created by this process, in dependency-safe order.

Run from ``AI_Middle_Office``:

    C:/Users/12521/miniconda3/python.exe -m scripts.mq_reliability_practice
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import redis
from celery import Celery
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.core.config import settings


REDIS_DB = 15
RUN_ID = os.environ.get("CODEX_MQ_PRACTICE_RUN_ID") or uuid.uuid4().hex
QUEUE_NAME = os.environ.get("CODEX_MQ_PRACTICE_QUEUE") or f"codex_mq_practice_{RUN_ID}"

OUTBOX_TABLE = "codex_practice_mq_outbox_20260730"
CONSUMED_TABLE = "codex_practice_mq_consumed_20260730"
EFFECTS_TABLE = "codex_practice_mq_effects_20260730"
ATTEMPTS_TABLE = "codex_practice_mq_attempts_20260730"
DEAD_TABLE = "codex_practice_mq_dead_20260730"
PRACTICE_TABLES = (
    OUTBOX_TABLE,
    CONSUMED_TABLE,
    EFFECTS_TABLE,
    ATTEMPTS_TABLE,
    DEAD_TABLE,
)


def _redis_url_for_db(source_url: str, database: int) -> str:
    parsed = urlsplit(source_url)
    path_prefix = parsed.path.rsplit("/", 1)[0]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"{path_prefix}/{database}",
            parsed.query,
            parsed.fragment,
        )
    )


PRACTICE_REDIS_URL = _redis_url_for_db(settings.celery_broker_url, REDIS_DB)

# Celery's environment loader has higher precedence than constructor arguments.
# Override it inside this isolated process before creating/finalizing the app.
os.environ["CELERY_BROKER_URL"] = PRACTICE_REDIS_URL
os.environ["CELERY_RESULT_BACKEND"] = PRACTICE_REDIS_URL

celery_app = Celery(
    "codex_mq_reliability_practice",
    broker=PRACTICE_REDIS_URL,
    backend=PRACTICE_REDIS_URL,
)
celery_app.conf.update(
    broker_url=PRACTICE_REDIS_URL,
    result_backend=PRACTICE_REDIS_URL,
    task_default_queue=QUEUE_NAME,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=120,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 10},
)


_worker_engine = None


def _get_worker_engine():
    global _worker_engine
    if _worker_engine is None:
        _worker_engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
        )
    return _worker_engine


def _mysql_error_code(exc: Exception) -> int | None:
    original = getattr(exc, "orig", None)
    args = getattr(original, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _record_attempt(
    *,
    task_kind: str,
    event_id: str,
    attempt_no: int,
    result: str,
) -> None:
    engine = _get_worker_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {ATTEMPTS_TABLE}
                    (task_kind, event_id, attempt_no, result, created_at)
                VALUES
                    (:task_kind, :event_id, :attempt_no, :result, NOW(6))
                """
            ),
            {
                "task_kind": task_kind,
                "event_id": event_id,
                "attempt_no": attempt_no,
                "result": result,
            },
        )


@celery_app.task(name="codex.practice.ping")
def practice_ping() -> dict[str, Any]:
    return {"pong": True, "queue": QUEUE_NAME}


@celery_app.task(name="codex.practice.consume_event")
def consume_event(event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    engine = _get_worker_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {CONSUMED_TABLE}
                        (consumer_name, event_id, consumed_at)
                    VALUES
                        ('quote-preview-consumer', :event_id, NOW(6))
                    """
                ),
                {"event_id": event_id},
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {EFFECTS_TABLE}
                        (event_id, effect_type, payload_json, applied_at)
                    VALUES
                        (:event_id, 'preview_created', :payload_json, NOW(6))
                    """
                ),
                {
                    "event_id": event_id,
                    "payload_json": json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )
        return {"result": "applied", "event_id": event_id}
    except IntegrityError as exc:
        if _mysql_error_code(exc) == 1062:
            return {"result": "duplicate_ignored", "event_id": event_id}
        raise


@celery_app.task(
    bind=True,
    name="codex.practice.transient_then_success",
    max_retries=2,
)
def transient_then_success(self, event_id: str) -> dict[str, Any]:
    attempt_no = int(self.request.retries) + 1
    if self.request.retries < 2:
        _record_attempt(
            task_kind="transient",
            event_id=event_id,
            attempt_no=attempt_no,
            result="retry_scheduled",
        )
        raise self.retry(
            exc=RuntimeError("simulated transient dependency error"),
            countdown=0.2,
        )

    _record_attempt(
        task_kind="transient",
        event_id=event_id,
        attempt_no=attempt_no,
        result="succeeded",
    )
    engine = _get_worker_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {EFFECTS_TABLE}
                    (event_id, effect_type, payload_json, applied_at)
                VALUES
                    (:event_id, 'transient_task_completed', '{{}}', NOW(6))
                """
            ),
            {"event_id": event_id},
        )
    return {
        "result": "succeeded",
        "event_id": event_id,
        "attempt_no": attempt_no,
    }


@celery_app.task(
    bind=True,
    name="codex.practice.poison_to_dead_letter",
    max_retries=2,
)
def poison_to_dead_letter(self, event_id: str) -> dict[str, Any]:
    attempt_no = int(self.request.retries) + 1
    if self.request.retries < 2:
        _record_attempt(
            task_kind="poison",
            event_id=event_id,
            attempt_no=attempt_no,
            result="retry_scheduled",
        )
        raise self.retry(
            exc=RuntimeError("simulated deterministic poison message"),
            countdown=0.2,
        )

    _record_attempt(
        task_kind="poison",
        event_id=event_id,
        attempt_no=attempt_no,
        result="dead_lettered",
    )
    engine = _get_worker_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {DEAD_TABLE}
                    (event_id, reason, attempts, created_at)
                VALUES
                    (
                        :event_id,
                        'simulated deterministic poison message',
                        :attempts,
                        NOW(6)
                    )
                """
            ),
            {"event_id": event_id, "attempts": attempt_no},
        )
    return {
        "result": "dead_lettered",
        "event_id": event_id,
        "attempt_no": attempt_no,
    }


def _assert_safe_table_names() -> None:
    for table_name in PRACTICE_TABLES:
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


def _create_tables(engine) -> list[str]:
    created: list[str] = []
    definitions = {
        OUTBOX_TABLE: f"""
            CREATE TABLE {OUTBOX_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT,
                event_id VARCHAR(64) NOT NULL,
                event_type VARCHAR(64) NOT NULL,
                payload_json TEXT NOT NULL,
                status VARCHAR(24) NOT NULL,
                publish_attempts INT NOT NULL DEFAULT 0,
                created_at DATETIME(6) NOT NULL,
                published_at DATETIME(6) NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uq_practice_mq_outbox_event (event_id)
            ) ENGINE=InnoDB
        """,
        CONSUMED_TABLE: f"""
            CREATE TABLE {CONSUMED_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT,
                consumer_name VARCHAR(64) NOT NULL,
                event_id VARCHAR(64) NOT NULL,
                consumed_at DATETIME(6) NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uq_practice_mq_consumed (consumer_name, event_id)
            ) ENGINE=InnoDB
        """,
        EFFECTS_TABLE: f"""
            CREATE TABLE {EFFECTS_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT,
                event_id VARCHAR(64) NOT NULL,
                effect_type VARCHAR(64) NOT NULL,
                payload_json TEXT NOT NULL,
                applied_at DATETIME(6) NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uq_practice_mq_effect_event (event_id)
            ) ENGINE=InnoDB
        """,
        ATTEMPTS_TABLE: f"""
            CREATE TABLE {ATTEMPTS_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT,
                task_kind VARCHAR(32) NOT NULL,
                event_id VARCHAR(64) NOT NULL,
                attempt_no INT NOT NULL,
                result VARCHAR(32) NOT NULL,
                created_at DATETIME(6) NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uq_practice_mq_attempt (
                    task_kind,
                    event_id,
                    attempt_no
                )
            ) ENGINE=InnoDB
        """,
        DEAD_TABLE: f"""
            CREATE TABLE {DEAD_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT,
                event_id VARCHAR(64) NOT NULL,
                reason VARCHAR(255) NOT NULL,
                attempts INT NOT NULL,
                created_at DATETIME(6) NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uq_practice_mq_dead_event (event_id)
            ) ENGINE=InnoDB
        """,
    }
    for table_name in PRACTICE_TABLES:
        with engine.begin() as conn:
            conn.exec_driver_sql(definitions[table_name])
        created.append(table_name)
    return created


def _start_worker() -> subprocess.Popen:
    environment = os.environ.copy()
    environment["CODEX_MQ_PRACTICE_RUN_ID"] = RUN_ID
    environment["CODEX_MQ_PRACTICE_QUEUE"] = QUEUE_NAME
    environment["CELERY_BROKER_URL"] = PRACTICE_REDIS_URL
    environment["CELERY_RESULT_BACKEND"] = PRACTICE_REDIS_URL
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "scripts.mq_reliability_practice:celery_app",
        "worker",
        "--loglevel=ERROR",
        "--pool=solo",
        "--concurrency=1",
        "--queues",
        QUEUE_NAME,
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
    ]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        command,
        cwd=os.getcwd(),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )


def _stop_worker(process: subprocess.Popen | None) -> dict[str, Any]:
    if process is None:
        return {"started": False, "return_code": None, "log_tail": []}
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    output = ""
    if process.stdout is not None:
        output = process.stdout.read()
        process.stdout.close()
    nonempty_lines = [line for line in output.splitlines() if line.strip()]
    return {
        "started": True,
        "return_code": process.returncode,
        "log_tail": nonempty_lines[-8:],
    }


def _wait_for_worker() -> dict[str, Any]:
    ping_result = practice_ping.apply_async(queue=QUEUE_NAME)
    return ping_result.get(timeout=25)


def _duplicate_publish_experiment(engine) -> dict[str, Any]:
    event_id = f"duplicate-{RUN_ID}"
    payload = {
        "quote_job_id": "synthetic-quote-job",
        "action": "build_preview",
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {OUTBOX_TABLE}
                    (
                        event_id,
                        event_type,
                        payload_json,
                        status,
                        publish_attempts,
                        created_at
                    )
                VALUES
                    (
                        :event_id,
                        'quote.preview.requested.v1',
                        :payload_json,
                        'pending',
                        0,
                        NOW(6)
                    )
                """
            ),
            {
                "event_id": event_id,
                "payload_json": json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        )

    # Simulates: publish succeeded, publisher crashed before marking the Outbox row,
    # and the same logical event was published again during recovery.
    first = consume_event.apply_async(
        args=[event_id, payload],
        queue=QUEUE_NAME,
    )
    second = consume_event.apply_async(
        args=[event_id, payload],
        queue=QUEUE_NAME,
    )
    results = [first.get(timeout=25), second.get(timeout=25)]

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                UPDATE {OUTBOX_TABLE}
                SET status = 'published',
                    publish_attempts = 2,
                    published_at = NOW(6)
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        )

    with engine.connect() as conn:
        outbox = conn.execute(
            text(
                f"""
                SELECT status, publish_attempts
                FROM {OUTBOX_TABLE}
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        ).mappings().one()
        consumed_count = conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM {CONSUMED_TABLE}
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        ).scalar()
        effect_count = conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM {EFFECTS_TABLE}
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        ).scalar()

    return {
        "logical_event_id": event_id,
        "publish_count": 2,
        "task_results": results,
        "outbox": dict(outbox),
        "consumed_rows": consumed_count,
        "business_effect_rows": effect_count,
        "single_effect_verified": effect_count == 1,
    }


def _retry_experiment(engine) -> dict[str, Any]:
    event_id = f"transient-{RUN_ID}"
    result = transient_then_success.apply_async(
        args=[event_id],
        queue=QUEUE_NAME,
    ).get(timeout=30)
    with engine.connect() as conn:
        attempts = conn.execute(
            text(
                f"""
                SELECT attempt_no, result
                FROM {ATTEMPTS_TABLE}
                WHERE task_kind = 'transient'
                  AND event_id = :event_id
                ORDER BY attempt_no
                """
            ),
            {"event_id": event_id},
        ).mappings().all()
        effect_count = conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM {EFFECTS_TABLE}
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        ).scalar()
    return {
        "task_result": result,
        "attempts": [dict(row) for row in attempts],
        "business_effect_rows": effect_count,
    }


def _dead_letter_experiment(engine) -> dict[str, Any]:
    event_id = f"poison-{RUN_ID}"
    result = poison_to_dead_letter.apply_async(
        args=[event_id],
        queue=QUEUE_NAME,
    ).get(timeout=30)
    with engine.connect() as conn:
        attempts = conn.execute(
            text(
                f"""
                SELECT attempt_no, result
                FROM {ATTEMPTS_TABLE}
                WHERE task_kind = 'poison'
                  AND event_id = :event_id
                ORDER BY attempt_no
                """
            ),
            {"event_id": event_id},
        ).mappings().all()
        dead_row = conn.execute(
            text(
                f"""
                SELECT event_id, reason, attempts
                FROM {DEAD_TABLE}
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        ).mappings().one()
        effect_count = conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM {EFFECTS_TABLE}
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        ).scalar()
    return {
        "task_result": result,
        "attempts": [dict(row) for row in attempts],
        "dead_letter": dict(dead_row),
        "business_effect_rows": effect_count,
    }


def main() -> None:
    _assert_safe_table_names()
    if not settings.database_url.startswith("mysql"):
        raise RuntimeError("This practice requires MySQL")

    redis_client = redis.Redis.from_url(
        PRACTICE_REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=2,
        decode_responses=True,
    )
    initial_redis_keys = set(redis_client.scan_iter(match="*", count=100))
    if initial_redis_keys:
        redis_client.close()
        raise RuntimeError(
            "Safety stop: Redis DB 15 is not empty; no keys will be changed"
        )

    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=4,
    )
    created_tables: list[str] = []
    worker_process: subprocess.Popen | None = None
    report: dict[str, Any] = {
        "run_id": RUN_ID,
        "queue_name": QUEUE_NAME,
        "redis_db": REDIS_DB,
        "initial_redis_key_count": len(initial_redis_keys),
        "tables": list(PRACTICE_TABLES),
        "celery_config": {
            "task_acks_late": True,
            "worker_prefetch_multiplier": 1,
            "visibility_timeout_seconds": 10,
            "worker_pool": "solo",
        },
    }

    try:
        with engine.connect() as conn:
            report["mysql_version"] = conn.execute(text("SELECT VERSION()")).scalar()
            existing = [
                table_name
                for table_name in PRACTICE_TABLES
                if _table_exists(conn, table_name)
            ]
            if existing:
                raise RuntimeError(
                    f"Safety stop: practice tables already exist and will not be touched: {existing}"
                )

        created_tables = _create_tables(engine)
        worker_process = _start_worker()
        report["worker_ping"] = _wait_for_worker()
        report["duplicate_publish"] = _duplicate_publish_experiment(engine)
        report["bounded_retry"] = _retry_experiment(engine)
        report["dead_letter"] = _dead_letter_experiment(engine)
    finally:
        cleanup: dict[str, Any] = {}
        cleanup["worker"] = _stop_worker(worker_process)
        time.sleep(0.5)

        final_redis_keys = set(redis_client.scan_iter(match="*", count=100))
        keys_created_by_run = final_redis_keys - initial_redis_keys
        cleanup["redis_keys_created"] = len(keys_created_by_run)
        if keys_created_by_run:
            redis_client.delete(*sorted(keys_created_by_run))
        cleanup["redis_remaining_keys"] = len(
            set(redis_client.scan_iter(match="*", count=100)) - initial_redis_keys
        )

        with engine.begin() as conn:
            for table_name in reversed(created_tables):
                if _table_exists(conn, table_name):
                    conn.exec_driver_sql(f"DROP TABLE {table_name}")
            cleanup["mysql_remaining_tables"] = [
                table_name
                for table_name in PRACTICE_TABLES
                if _table_exists(conn, table_name)
            ]

        report["cleanup"] = cleanup
        redis_client.close()
        engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
