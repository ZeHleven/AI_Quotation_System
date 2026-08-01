from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")


class RetryableError(RuntimeError):
    """A transient dependency failure that may be retried."""


class PermanentError(RuntimeError):
    """A caller or business error that retrying cannot fix."""


class AttemptTimeout(TimeoutError):
    """The per-attempt timeout was reached."""


class CircuitOpenError(RuntimeError):
    """The dependency circuit is open, so no downstream call is made."""


class ServiceUnavailableError(RuntimeError):
    """No safe degraded result is available."""


class BulkheadRejected(RuntimeError):
    """The isolated resource pool and its bounded queue are full."""


@dataclass
class VirtualClock:
    now: float = 0.0

    def advance(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@dataclass(frozen=True)
class DownstreamEvent(Generic[T]):
    kind: str
    latency_seconds: float
    value: T | None = None
    message: str = ""


class ScriptedDownstream(Generic[T]):
    """Deterministic dependency used to reproduce failures without network I/O."""

    def __init__(self, clock: VirtualClock, events: list[DownstreamEvent[T]]) -> None:
        self.clock = clock
        self.events = deque(events)
        self.call_count = 0
        self.timeouts: list[float] = []

    def call(self, timeout_seconds: float) -> T:
        if not self.events:
            raise AssertionError("No scripted downstream event remains")

        event = self.events.popleft()
        self.call_count += 1
        self.timeouts.append(round(timeout_seconds, 6))

        waited = min(event.latency_seconds, timeout_seconds)
        self.clock.advance(waited)
        if event.latency_seconds > timeout_seconds:
            raise AttemptTimeout(
                f"attempt exceeded {timeout_seconds:.3f}s timeout"
            )
        if event.kind == "retryable_error":
            raise RetryableError(event.message or "temporary dependency failure")
        if event.kind == "permanent_error":
            raise PermanentError(event.message or "invalid request")
        if event.kind != "success":
            raise AssertionError(f"Unsupported downstream event: {event.kind}")
        return event.value  # type: ignore[return-value]


@dataclass
class CallOutcome(Generic[T]):
    status: str
    attempts: int
    elapsed_seconds: float
    value: T | None = None
    error_type: str | None = None
    retry_delays: tuple[float, ...] = ()


def call_with_deadline(
    downstream: ScriptedDownstream[T],
    clock: VirtualClock,
    *,
    total_budget_seconds: float,
    per_attempt_timeout_seconds: float,
    max_attempts: int,
    base_backoff_seconds: float,
) -> CallOutcome[T]:
    """Retry transient failures while respecting one end-to-end deadline."""

    started_at = clock.now
    deadline = started_at + total_budget_seconds
    retry_delays: list[float] = []
    attempts = 0

    while attempts < max_attempts:
        remaining = deadline - clock.now
        if remaining <= 0:
            return CallOutcome(
                status="deadline_exhausted",
                attempts=attempts,
                elapsed_seconds=clock.now - started_at,
                error_type="DeadlineExceeded",
                retry_delays=tuple(retry_delays),
            )

        attempts += 1
        attempt_timeout = min(per_attempt_timeout_seconds, remaining)
        try:
            value = downstream.call(attempt_timeout)
            return CallOutcome(
                status="success",
                attempts=attempts,
                elapsed_seconds=clock.now - started_at,
                value=value,
                retry_delays=tuple(retry_delays),
            )
        except PermanentError as exc:
            return CallOutcome(
                status="non_retryable_failure",
                attempts=attempts,
                elapsed_seconds=clock.now - started_at,
                error_type=type(exc).__name__,
                retry_delays=tuple(retry_delays),
            )
        except (RetryableError, AttemptTimeout) as exc:
            remaining = deadline - clock.now
            if remaining <= 0:
                return CallOutcome(
                    status="deadline_exhausted",
                    attempts=attempts,
                    elapsed_seconds=clock.now - started_at,
                    error_type=type(exc).__name__,
                    retry_delays=tuple(retry_delays),
                )
            if attempts >= max_attempts:
                return CallOutcome(
                    status="retry_exhausted",
                    attempts=attempts,
                    elapsed_seconds=clock.now - started_at,
                    error_type=type(exc).__name__,
                    retry_delays=tuple(retry_delays),
                )

            exponential_delay = base_backoff_seconds * (2 ** (attempts - 1))
            delay = min(exponential_delay, remaining)
            retry_delays.append(round(delay, 6))
            clock.advance(delay)

    raise AssertionError("retry loop exited unexpectedly")


class CircuitBreaker:
    def __init__(
        self,
        clock: VirtualClock,
        *,
        failure_threshold: int,
        recovery_timeout_seconds: float,
    ) -> None:
        self.clock = clock
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.state = "closed"
        self.failure_count = 0
        self.opened_at: float | None = None
        self.half_open_probe_in_flight = False

    def before_call(self) -> None:
        if self.state == "open":
            assert self.opened_at is not None
            if self.clock.now - self.opened_at < self.recovery_timeout_seconds:
                raise CircuitOpenError("dependency circuit is open")
            self.state = "half_open"
            self.half_open_probe_in_flight = True
            return
        if self.state == "half_open":
            if self.half_open_probe_in_flight:
                raise CircuitOpenError("half-open probe is already in flight")
            self.half_open_probe_in_flight = True

    def record_success(self) -> None:
        self.state = "closed"
        self.failure_count = 0
        self.opened_at = None
        self.half_open_probe_in_flight = False

    def record_failure(self) -> None:
        if self.state == "half_open":
            self.state = "open"
            self.opened_at = self.clock.now
            self.failure_count = self.failure_threshold
            self.half_open_probe_in_flight = False
            return

        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            self.opened_at = self.clock.now


def guarded_call(
    downstream: ScriptedDownstream[T],
    breaker: CircuitBreaker,
    *,
    timeout_seconds: float,
) -> T:
    breaker.before_call()
    try:
        value = downstream.call(timeout_seconds)
    except (RetryableError, AttemptTimeout):
        breaker.record_failure()
        raise
    except PermanentError:
        # A bad caller request is not evidence that the dependency is unhealthy.
        raise
    else:
        breaker.record_success()
        return value


@dataclass(frozen=True)
class CacheEntry(Generic[T]):
    value: T
    cached_at: float


def call_with_safe_fallback(
    downstream: ScriptedDownstream[T],
    breaker: CircuitBreaker,
    *,
    timeout_seconds: float,
    cache_entry: CacheEntry[T] | None,
) -> dict:
    try:
        value = guarded_call(
            downstream,
            breaker,
            timeout_seconds=timeout_seconds,
        )
        return {
            "value": value,
            "source": "downstream",
            "degraded": False,
            "warning": None,
        }
    except (RetryableError, AttemptTimeout, CircuitOpenError) as exc:
        if cache_entry is None:
            raise ServiceUnavailableError(
                "dependency unavailable and no safe fallback exists"
            ) from exc
        return {
            "value": cache_entry.value,
            "source": "stale_cache",
            "degraded": True,
            "warning": type(exc).__name__,
            "cached_at": cache_entry.cached_at,
        }


def liveness() -> dict:
    """Liveness deliberately does not probe dependencies."""

    return {"http_status": 200, "status": "alive"}


def readiness(
    *,
    critical_dependencies: dict[str, bool],
    optional_dependencies: dict[str, bool],
) -> dict:
    critical_ok = all(critical_dependencies.values())
    optional_ok = all(optional_dependencies.values())
    if not critical_ok:
        status = "not_ready"
        http_status = 503
    elif not optional_ok:
        status = "degraded"
        http_status = 200
    else:
        status = "ready"
        http_status = 200
    return {
        "http_status": http_status,
        "status": status,
        "critical": critical_dependencies,
        "optional": optional_dependencies,
    }


class Bulkhead:
    """Semaphore isolation plus a bounded wait queue."""

    def __init__(self, *, concurrency: int, queue_limit: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._state_lock = asyncio.Lock()
        self.queue_limit = queue_limit
        self.active = 0
        self.waiting = 0
        self.max_active = 0
        self.rejected = 0

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        queued = False
        async with self._state_lock:
            if self._semaphore.locked():
                if self.waiting >= self.queue_limit:
                    self.rejected += 1
                    raise BulkheadRejected("bulkhead queue is full")
                self.waiting += 1
                queued = True

        await self._semaphore.acquire()
        async with self._state_lock:
            if queued:
                self.waiting -= 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)

        try:
            return await operation()
        finally:
            async with self._state_lock:
                self.active -= 1
            self._semaphore.release()


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class CheckRecorder:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def check(self, condition: bool, name: str, detail: str) -> None:
        self.checks.append(Check(name=name, passed=bool(condition), detail=detail))
        if not condition:
            raise AssertionError(f"{name}: {detail}")


async def exercise_bulkhead(recorder: CheckRecorder) -> dict:
    model_bulkhead = Bulkhead(concurrency=2, queue_limit=1)
    database_bulkhead = Bulkhead(concurrency=1, queue_limit=0)
    release_model_calls = asyncio.Event()
    started: list[str] = []

    async def model_call(name: str) -> str:
        started.append(name)
        await release_model_calls.wait()
        return name

    first = asyncio.create_task(
        model_bulkhead.run(lambda: model_call("model-1"))
    )
    second = asyncio.create_task(
        model_bulkhead.run(lambda: model_call("model-2"))
    )
    await asyncio.sleep(0)
    recorder.check(
        model_bulkhead.active == 2,
        "bulkhead_concurrency_cap",
        f"active={model_bulkhead.active}",
    )

    third = asyncio.create_task(
        model_bulkhead.run(lambda: model_call("model-3"))
    )
    await asyncio.sleep(0)
    recorder.check(
        model_bulkhead.waiting == 1,
        "bulkhead_bounded_queue_accepts_one",
        f"waiting={model_bulkhead.waiting}",
    )

    rejected = False
    try:
        await model_bulkhead.run(lambda: model_call("model-4"))
    except BulkheadRejected:
        rejected = True
    recorder.check(
        rejected and model_bulkhead.rejected == 1,
        "bulkhead_overflow_fast_rejected",
        f"rejected={model_bulkhead.rejected}",
    )

    async def database_call() -> str:
        return "db-ok"

    database_result = await database_bulkhead.run(database_call)
    recorder.check(
        database_result == "db-ok",
        "separate_bulkhead_preserves_database",
        f"database_result={database_result}",
    )

    release_model_calls.set()
    model_results = await asyncio.gather(first, second, third)
    recorder.check(
        sorted(model_results) == ["model-1", "model-2", "model-3"],
        "accepted_bulkhead_work_completed",
        f"results={model_results}",
    )
    recorder.check(
        model_bulkhead.max_active == 2,
        "bulkhead_never_exceeds_limit",
        f"max_active={model_bulkhead.max_active}",
    )
    return {
        "model_results": model_results,
        "model_max_active": model_bulkhead.max_active,
        "model_rejected": model_bulkhead.rejected,
        "database_result_while_model_saturated": database_result,
        "started_order": started,
    }


async def run_experiment() -> dict:
    recorder = CheckRecorder()

    retry_clock = VirtualClock()
    retry_downstream = ScriptedDownstream(
        retry_clock,
        [
            DownstreamEvent(
                kind="retryable_error",
                latency_seconds=0.02,
                message="HTTP 503",
            ),
            DownstreamEvent(
                kind="success",
                latency_seconds=0.04,
                value={"quote": 1280},
            ),
        ],
    )
    retry_outcome = call_with_deadline(
        retry_downstream,
        retry_clock,
        total_budget_seconds=0.50,
        per_attempt_timeout_seconds=0.20,
        max_attempts=3,
        base_backoff_seconds=0.05,
    )
    recorder.check(
        retry_outcome.status == "success",
        "transient_failure_recovered",
        f"status={retry_outcome.status}",
    )
    recorder.check(
        retry_outcome.attempts == 2,
        "transient_failure_retried_once",
        f"attempts={retry_outcome.attempts}",
    )
    recorder.check(
        retry_outcome.elapsed_seconds <= 0.50,
        "retry_respects_total_deadline",
        f"elapsed={retry_outcome.elapsed_seconds:.3f}s",
    )
    recorder.check(
        retry_outcome.retry_delays == (0.05,),
        "retry_uses_backoff",
        f"retry_delays={retry_outcome.retry_delays}",
    )

    permanent_clock = VirtualClock()
    permanent_downstream = ScriptedDownstream(
        permanent_clock,
        [
            DownstreamEvent(
                kind="permanent_error",
                latency_seconds=0.01,
                message="HTTP 400",
            ),
            DownstreamEvent(
                kind="success",
                latency_seconds=0.01,
                value="must-not-be-called",
            ),
        ],
    )
    permanent_outcome = call_with_deadline(
        permanent_downstream,
        permanent_clock,
        total_budget_seconds=0.50,
        per_attempt_timeout_seconds=0.20,
        max_attempts=3,
        base_backoff_seconds=0.05,
    )
    recorder.check(
        permanent_outcome.status == "non_retryable_failure",
        "permanent_error_not_retried",
        f"status={permanent_outcome.status}",
    )
    recorder.check(
        permanent_downstream.call_count == 1,
        "permanent_error_single_downstream_call",
        f"calls={permanent_downstream.call_count}",
    )

    budget_clock = VirtualClock()
    budget_downstream = ScriptedDownstream(
        budget_clock,
        [
            DownstreamEvent(
                kind="retryable_error",
                latency_seconds=0.15,
                message="HTTP 503",
            ),
            DownstreamEvent(
                kind="success",
                latency_seconds=0.10,
                value="too-late",
            ),
        ],
    )
    budget_outcome = call_with_deadline(
        budget_downstream,
        budget_clock,
        total_budget_seconds=0.25,
        per_attempt_timeout_seconds=0.15,
        max_attempts=3,
        base_backoff_seconds=0.08,
    )
    recorder.check(
        budget_outcome.status == "deadline_exhausted",
        "deadline_stops_late_retry",
        f"status={budget_outcome.status}",
    )
    recorder.check(
        budget_outcome.attempts == 2,
        "deadline_limits_attempt_count",
        f"attempts={budget_outcome.attempts}",
    )
    recorder.check(
        budget_outcome.elapsed_seconds <= 0.25 + 1e-9,
        "deadline_caps_total_elapsed",
        f"elapsed={budget_outcome.elapsed_seconds:.3f}s",
    )
    recorder.check(
        budget_downstream.timeouts[-1] == 0.02,
        "remaining_budget_propagated_to_attempt",
        f"second_timeout={budget_downstream.timeouts[-1]:.3f}s",
    )

    circuit_clock = VirtualClock()
    circuit_downstream = ScriptedDownstream(
        circuit_clock,
        [
            DownstreamEvent(
                kind="retryable_error",
                latency_seconds=0.01,
                message="HTTP 503",
            ),
            DownstreamEvent(
                kind="retryable_error",
                latency_seconds=0.01,
                message="HTTP 503",
            ),
            DownstreamEvent(
                kind="success",
                latency_seconds=0.01,
                value={"model": "recovered"},
            ),
        ],
    )
    breaker = CircuitBreaker(
        circuit_clock,
        failure_threshold=2,
        recovery_timeout_seconds=30.0,
    )
    for _ in range(2):
        try:
            guarded_call(circuit_downstream, breaker, timeout_seconds=0.20)
        except RetryableError:
            pass
    recorder.check(
        breaker.state == "open",
        "circuit_opens_after_threshold",
        f"state={breaker.state}, failures={breaker.failure_count}",
    )

    calls_before_fast_failure = circuit_downstream.call_count
    fast_failed = False
    try:
        guarded_call(circuit_downstream, breaker, timeout_seconds=0.20)
    except CircuitOpenError:
        fast_failed = True
    recorder.check(
        fast_failed,
        "open_circuit_fast_fails",
        f"state={breaker.state}",
    )
    recorder.check(
        circuit_downstream.call_count == calls_before_fast_failure,
        "open_circuit_skips_downstream",
        f"calls={circuit_downstream.call_count}",
    )

    cache = CacheEntry(value={"quote": 1260}, cached_at=123.0)
    fallback_calls_before = circuit_downstream.call_count
    fallback_result = call_with_safe_fallback(
        circuit_downstream,
        breaker,
        timeout_seconds=0.20,
        cache_entry=cache,
    )
    recorder.check(
        fallback_result["degraded"] is True,
        "fallback_is_explicitly_degraded",
        f"degraded={fallback_result['degraded']}",
    )
    recorder.check(
        fallback_result["source"] == "stale_cache",
        "fallback_exposes_data_source",
        f"source={fallback_result['source']}",
    )
    recorder.check(
        circuit_downstream.call_count == fallback_calls_before,
        "fallback_does_not_bypass_open_circuit",
        f"calls={circuit_downstream.call_count}",
    )

    no_fallback_error = False
    try:
        call_with_safe_fallback(
            circuit_downstream,
            breaker,
            timeout_seconds=0.20,
            cache_entry=None,
        )
    except ServiceUnavailableError:
        no_fallback_error = True
    recorder.check(
        no_fallback_error,
        "unsafe_fallback_returns_controlled_error",
        f"raised={no_fallback_error}",
    )

    circuit_clock.advance(30.0)
    recovered_value = guarded_call(
        circuit_downstream,
        breaker,
        timeout_seconds=0.20,
    )
    recorder.check(
        recovered_value == {"model": "recovered"},
        "half_open_probe_reaches_dependency",
        f"value={recovered_value}",
    )
    recorder.check(
        breaker.state == "closed" and breaker.failure_count == 0,
        "successful_probe_closes_circuit",
        f"state={breaker.state}, failures={breaker.failure_count}",
    )

    live_when_dependencies_fail = liveness()
    recorder.check(
        live_when_dependencies_fail["http_status"] == 200,
        "liveness_ignores_dependency_failure",
        f"response={live_when_dependencies_fail}",
    )

    all_ready = readiness(
        critical_dependencies={"database": True, "task_queue": True},
        optional_dependencies={"model_gateway": True, "rag": True},
    )
    recorder.check(
        all_ready["http_status"] == 200 and all_ready["status"] == "ready",
        "readiness_all_dependencies_healthy",
        f"response={all_ready}",
    )

    critical_down = readiness(
        critical_dependencies={"database": False, "task_queue": True},
        optional_dependencies={"model_gateway": True, "rag": True},
    )
    recorder.check(
        critical_down["http_status"] == 503
        and critical_down["status"] == "not_ready",
        "critical_failure_removes_readiness",
        f"response={critical_down}",
    )

    optional_down = readiness(
        critical_dependencies={"database": True, "task_queue": True},
        optional_dependencies={"model_gateway": False, "rag": True},
    )
    recorder.check(
        optional_down["http_status"] == 200
        and optional_down["status"] == "degraded",
        "optional_failure_keeps_degraded_readiness",
        f"response={optional_down}",
    )

    bulkhead_result = await exercise_bulkhead(recorder)

    passed = sum(check.passed for check in recorder.checks)
    result = {
        "environment": {
            "network_ports_opened": False,
            "external_services_called": False,
            "persistent_files_created": False,
            "clock": "virtual",
        },
        "timeout_and_retry": {
            "transient_recovery": asdict(retry_outcome),
            "permanent_error": asdict(permanent_outcome),
            "deadline_exhaustion": asdict(budget_outcome),
        },
        "circuit_and_fallback": {
            "state_after_recovery": breaker.state,
            "downstream_calls": circuit_downstream.call_count,
            "fallback": fallback_result,
        },
        "health": {
            "liveness": live_when_dependencies_fail,
            "ready": all_ready,
            "critical_down": critical_down,
            "optional_down": optional_down,
        },
        "bulkhead": bulkhead_result,
        "checks": {
            "passed": passed,
            "total": len(recorder.checks),
            "all_assertions_passed": passed == len(recorder.checks),
            "items": [asdict(check) for check in recorder.checks],
        },
    }
    return result


def main() -> None:
    result = asyncio.run(run_experiment())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
