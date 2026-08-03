"""Isolated Redis Cache Aside and hot-key practice.

Safety properties:
- Uses Redis logical DB 15 instead of Celery broker/result DB 0/1.
- Uses a random ``codex:practice:cache:<uuid>`` key prefix.
- Never calls FLUSHDB/FLUSHALL and deletes only keys under that run prefix.
- Uses one fixed synthetic MySQL table with the ``codex_practice_`` prefix.
- Aborts if the MySQL practice table already exists.
- Drops only the table created by this process in ``finally``.

Run from ``AI_Middle_Office``:

    C:/Users/12521/miniconda3/python.exe -m scripts.redis_cache_practice
"""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from app.core.config import settings


MYSQL_TABLE = "codex_practice_cache_items_20260730"
REDIS_DB = 15
CACHE_TTL_SECONDS = 120
NEGATIVE_TTL_SECONDS = 30
HOT_CONCURRENCY = 24
NULL_SENTINEL = "__NULL__"


def _assert_safe_table_name() -> None:
    if not MYSQL_TABLE.startswith("codex_practice_"):
        raise RuntimeError("Practice table must use the codex_practice_ prefix")
    if not MYSQL_TABLE.replace("_", "").isalnum():
        raise RuntimeError("Practice table name contains unsafe characters")


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
            {"table_name": MYSQL_TABLE},
        ).scalar()
    )


def _serialize_item(row: dict[str, Any]) -> str:
    payload = dict(row)
    price = payload.get("unit_price")
    if isinstance(price, Decimal):
        payload["unit_price"] = str(price)
    updated_at = payload.get("updated_at")
    if isinstance(updated_at, datetime):
        payload["updated_at"] = updated_at.isoformat()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _deserialize_item(raw: str) -> dict[str, Any]:
    return json.loads(raw)


class PracticeContext:
    def __init__(self, engine, redis_client, prefix: str):
        self.engine = engine
        self.redis = redis_client
        self.prefix = prefix
        self.keys: set[str] = set()
        self.db_query_counts: Counter[str] = Counter()
        self.counter_lock = threading.Lock()

    def key(self, purpose: str) -> str:
        value = f"{self.prefix}:{purpose}"
        self.keys.add(value)
        return value

    def query_item(
        self,
        item_id: int,
        counter_name: str,
        simulated_latency_seconds: float = 0.0,
    ) -> dict[str, Any] | None:
        with self.counter_lock:
            self.db_query_counts[counter_name] += 1
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    f"""
                    SELECT item_id, item_name, unit_price, version, updated_at
                    FROM {MYSQL_TABLE}
                    WHERE item_id = :item_id
                    """
                ),
                {"item_id": item_id},
            ).mappings().one_or_none()
        if simulated_latency_seconds:
            time.sleep(simulated_latency_seconds)
        return dict(row) if row is not None else None

    def read_cache_aside(
        self,
        *,
        item_id: int,
        cache_key: str,
        counter_name: str,
        cache_null: bool,
    ) -> dict[str, Any]:
        cached = self.redis.get(cache_key)
        if cached is not None:
            if cached == NULL_SENTINEL:
                return {"source": "negative_cache", "item": None}
            return {"source": "cache", "item": _deserialize_item(cached)}

        item = self.query_item(item_id, counter_name)
        if item is None:
            if cache_null:
                self.redis.setex(cache_key, NEGATIVE_TTL_SECONDS, NULL_SENTINEL)
            return {"source": "database", "item": None}

        self.redis.setex(cache_key, CACHE_TTL_SECONDS, _serialize_item(item))
        return {"source": "database", "item": item}


def _cache_aside_experiment(ctx: PracticeContext) -> dict[str, Any]:
    cache_key = ctx.key("cost-item:1001")
    ctx.redis.delete(cache_key)
    first = ctx.read_cache_aside(
        item_id=1001,
        cache_key=cache_key,
        counter_name="cache_aside",
        cache_null=True,
    )
    second = ctx.read_cache_aside(
        item_id=1001,
        cache_key=cache_key,
        counter_name="cache_aside",
        cache_null=True,
    )
    return {
        "first_source": first["source"],
        "second_source": second["source"],
        "database_queries": ctx.db_query_counts["cache_aside"],
        "cached_ttl_seconds": ctx.redis.ttl(cache_key),
        "same_item": first["item"]["item_id"] == second["item"]["item_id"],
    }


def _consistency_experiment(ctx: PracticeContext) -> dict[str, Any]:
    cache_key = ctx.key("cost-item:1001")
    before = ctx.read_cache_aside(
        item_id=1001,
        cache_key=cache_key,
        counter_name="consistency",
        cache_null=True,
    )
    with ctx.engine.begin() as conn:
        conn.execute(
            text(
                f"""
                UPDATE {MYSQL_TABLE}
                SET unit_price = 120.00,
                    version = version + 1,
                    updated_at = NOW(6)
                WHERE item_id = 1001
                """
            )
        )
    deleted = ctx.redis.delete(cache_key)
    after_delete = ctx.read_cache_aside(
        item_id=1001,
        cache_key=cache_key,
        counter_name="consistency",
        cache_null=True,
    )
    next_hit = ctx.read_cache_aside(
        item_id=1001,
        cache_key=cache_key,
        counter_name="consistency",
        cache_null=True,
    )
    return {
        "cached_price_before_update": before["item"]["unit_price"],
        "cache_delete_count": deleted,
        "first_read_after_update_source": after_delete["source"],
        "first_read_after_update_price": str(after_delete["item"]["unit_price"]),
        "first_read_after_update_version": after_delete["item"]["version"],
        "second_read_after_update_source": next_hit["source"],
        "database_queries_during_experiment": ctx.db_query_counts["consistency"],
    }


def _penetration_experiment(ctx: PracticeContext) -> dict[str, Any]:
    naive_key = ctx.key("missing:naive:9999")
    negative_key = ctx.key("missing:negative:9999")
    ctx.redis.delete(naive_key, negative_key)

    for _ in range(20):
        ctx.read_cache_aside(
            item_id=9999,
            cache_key=naive_key,
            counter_name="penetration_naive",
            cache_null=False,
        )

    negative_sources: list[str] = []
    for _ in range(20):
        result = ctx.read_cache_aside(
            item_id=9999,
            cache_key=negative_key,
            counter_name="penetration_negative",
            cache_null=True,
        )
        negative_sources.append(result["source"])

    return {
        "requests_each": 20,
        "database_queries_without_negative_cache": ctx.db_query_counts[
            "penetration_naive"
        ],
        "database_queries_with_negative_cache": ctx.db_query_counts[
            "penetration_negative"
        ],
        "negative_cache_sources": dict(Counter(negative_sources)),
        "negative_cache_ttl_seconds": ctx.redis.ttl(negative_key),
    }


def _run_concurrently(worker) -> tuple[list[str], float]:
    barrier = threading.Barrier(HOT_CONCURRENCY)
    result_lock = threading.Lock()
    sources: list[str] = []

    def target() -> None:
        barrier.wait()
        source = worker()
        with result_lock:
            sources.append(source)

    threads = [threading.Thread(target=target, daemon=False) for _ in range(HOT_CONCURRENCY)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return sources, elapsed_ms


def _hot_key_experiment(ctx: PracticeContext) -> dict[str, Any]:
    naive_key = ctx.key("hot:naive:2001")
    protected_key = ctx.key("hot:protected:2001")
    lock_key = ctx.key("hot:protected:2001:lock")
    ctx.redis.delete(naive_key, protected_key, lock_key)

    def naive_worker() -> str:
        cached = ctx.redis.get(naive_key)
        if cached is not None:
            return "cache"
        item = ctx.query_item(
            2001,
            "hot_naive",
            simulated_latency_seconds=0.08,
        )
        ctx.redis.setex(naive_key, CACHE_TTL_SECONDS, _serialize_item(item))
        return "database"

    naive_sources, naive_elapsed_ms = _run_concurrently(naive_worker)

    release_lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
    """

    def protected_worker() -> str:
        cached = ctx.redis.get(protected_key)
        if cached is not None:
            return "cache"

        token = uuid.uuid4().hex
        acquired = ctx.redis.set(lock_key, token, nx=True, px=2_000)
        if acquired:
            try:
                # Double-check after acquiring the lock.
                cached_after_lock = ctx.redis.get(protected_key)
                if cached_after_lock is not None:
                    return "cache_after_lock"
                item = ctx.query_item(
                    2001,
                    "hot_protected",
                    simulated_latency_seconds=0.08,
                )
                ctx.redis.setex(
                    protected_key,
                    CACHE_TTL_SECONDS,
                    _serialize_item(item),
                )
                return "database_rebuilder"
            finally:
                ctx.redis.eval(release_lua, 1, lock_key, token)

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            cached_after_wait = ctx.redis.get(protected_key)
            if cached_after_wait is not None:
                return "cache_after_wait"
            time.sleep(0.01)

        # A bounded fallback prevents indefinite waiting if the lock holder dies.
        item = ctx.query_item(
            2001,
            "hot_protected_fallback",
            simulated_latency_seconds=0.08,
        )
        ctx.redis.setex(protected_key, CACHE_TTL_SECONDS, _serialize_item(item))
        return "database_fallback"

    protected_sources, protected_elapsed_ms = _run_concurrently(protected_worker)

    return {
        "concurrency": HOT_CONCURRENCY,
        "naive": {
            "database_queries": ctx.db_query_counts["hot_naive"],
            "sources": dict(Counter(naive_sources)),
            "elapsed_ms": round(naive_elapsed_ms, 3),
        },
        "protected": {
            "database_rebuild_queries": ctx.db_query_counts["hot_protected"],
            "fallback_database_queries": ctx.db_query_counts[
                "hot_protected_fallback"
            ],
            "sources": dict(Counter(protected_sources)),
            "elapsed_ms": round(protected_elapsed_ms, 3),
        },
    }


def _ttl_jitter_experiment(ctx: PracticeContext) -> dict[str, Any]:
    fixed_keys: list[str] = []
    jitter_keys: list[str] = []
    random_generator = random.Random(20260730)
    for index in range(20):
        fixed_key = ctx.key(f"ttl:fixed:{index}")
        jitter_key = ctx.key(f"ttl:jitter:{index}")
        fixed_keys.append(fixed_key)
        jitter_keys.append(jitter_key)
        ctx.redis.setex(fixed_key, 60, "1")
        ctx.redis.setex(jitter_key, 60 + random_generator.randint(0, 30), "1")

    fixed_ttls = [ctx.redis.ttl(key) for key in fixed_keys]
    jitter_ttls = [ctx.redis.ttl(key) for key in jitter_keys]
    return {
        "keys_each": 20,
        "fixed_unique_ttl_count": len(set(fixed_ttls)),
        "jitter_unique_ttl_count": len(set(jitter_ttls)),
        "fixed_ttl_range": [min(fixed_ttls), max(fixed_ttls)],
        "jitter_ttl_range": [min(jitter_ttls), max(jitter_ttls)],
    }


def _redis_failure_degradation(ctx: PracticeContext) -> dict[str, Any]:
    unavailable_client = redis.Redis(
        host="127.0.0.1",
        port=1,
        db=15,
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
        decode_responses=True,
    )
    cache_error: str | None = None
    item: dict[str, Any] | None = None
    try:
        unavailable_client.get("unreachable")
    except redis.RedisError as exc:
        cache_error = type(exc).__name__
        item = ctx.query_item(1001, "redis_unavailable")
    finally:
        unavailable_client.close()
    return {
        "cache_error": cache_error,
        "fallback_source": "database" if item else None,
        "returned_item_id": item["item_id"] if item else None,
        "database_queries": ctx.db_query_counts["redis_unavailable"],
    }


def main() -> None:
    _assert_safe_table_name()
    if not settings.database_url.startswith("mysql"):
        raise RuntimeError("This practice requires MySQL")

    redis_url = _redis_url_for_db(settings.celery_broker_url, REDIS_DB)
    redis_client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=1,
        socket_timeout=2,
        decode_responses=True,
    )
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=8,
        max_overflow=8,
    )
    prefix = f"codex:practice:cache:{uuid.uuid4().hex}"
    context = PracticeContext(engine, redis_client, prefix)
    mysql_table_created = False
    report: dict[str, Any] = {
        "redis_db": REDIS_DB,
        "key_prefix": prefix,
        "mysql_table": MYSQL_TABLE,
    }

    try:
        report["redis_ping"] = redis_client.ping()
        report["redis_version"] = redis_client.info("server").get("redis_version")
        report["redis_dbsize_before"] = redis_client.dbsize()

        with engine.begin() as conn:
            report["mysql_version"] = conn.execute(text("SELECT VERSION()")).scalar()
            if _table_exists(conn):
                raise RuntimeError(
                    f"Safety stop: {MYSQL_TABLE} already exists and will not be touched"
                )
            conn.exec_driver_sql(
                f"""
                CREATE TABLE {MYSQL_TABLE} (
                    item_id BIGINT NOT NULL,
                    item_name VARCHAR(128) NOT NULL,
                    unit_price DECIMAL(18, 2) NOT NULL,
                    version INT NOT NULL DEFAULT 1,
                    updated_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (item_id)
                ) ENGINE=InnoDB
                """
            )
            mysql_table_created = True
            conn.execute(
                text(
                    f"""
                    INSERT INTO {MYSQL_TABLE}
                        (item_id, item_name, unit_price, version, updated_at)
                    VALUES
                        (1001, '合成成本条目', 100.00, 1, NOW(6)),
                        (2001, '合成热点条目', 88.88, 1, NOW(6))
                    """
                )
            )

        report["cache_aside"] = _cache_aside_experiment(context)
        report["consistency"] = _consistency_experiment(context)
        report["penetration"] = _penetration_experiment(context)
        report["hot_key"] = _hot_key_experiment(context)
        report["ttl_jitter"] = _ttl_jitter_experiment(context)
        report["redis_failure_degradation"] = _redis_failure_degradation(context)
    finally:
        cleanup: dict[str, Any] = {}

        # Delete only exact keys registered under this random run prefix.
        if context.keys:
            redis_client.delete(*sorted(context.keys))
        remaining_before_guard_cleanup = list(
            redis_client.scan_iter(match=f"{prefix}:*", count=100)
        )
        cleanup["redis_untracked_keys_before_guard_cleanup"] = len(
            remaining_before_guard_cleanup
        )
        if remaining_before_guard_cleanup:
            # The prefix includes a per-run UUID, so these can only belong to this run.
            redis_client.delete(*remaining_before_guard_cleanup)
        cleanup["redis_remaining_keys"] = len(
            list(redis_client.scan_iter(match=f"{prefix}:*", count=100))
        )

        with engine.begin() as conn:
            if mysql_table_created and _table_exists(conn):
                conn.exec_driver_sql(f"DROP TABLE {MYSQL_TABLE}")
            cleanup["mysql_table_absent"] = not _table_exists(conn)

        report["cleanup"] = cleanup
        redis_client.close()
        engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
