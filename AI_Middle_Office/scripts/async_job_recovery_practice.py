from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timed_out"}
ALLOWED_TRANSITIONS = {
    "queued": {"running", "cancel_requested", "timed_out"},
    "running": {
        "succeeded",
        "failed",
        "cancel_requested",
        "timed_out",
    },
    "cancel_requested": {"cancelled", "timed_out"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
    "timed_out": set(),
}


class StateConflict(RuntimeError):
    """The requested state transition is not valid."""


class LeaseLost(RuntimeError):
    """The Worker no longer owns the job."""


class CheckpointConflict(RuntimeError):
    """The saved checkpoint does not belong to the current immutable input."""


@dataclass
class VirtualClock:
    now: float = 0.0

    def advance(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@dataclass(frozen=True)
class Event:
    sequence: int
    event_type: str
    occurred_at: float
    from_status: str | None
    to_status: str
    detail: dict[str, Any]


@dataclass
class Checkpoint:
    input_version: str
    completed_steps: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    saved_at: float = 0.0


@dataclass
class Job:
    job_id: int
    input_version: str
    status: str
    stage: str
    created_at: float
    task_deadline_at: float
    max_attempts: int
    retry_of: int | None = None
    attempt_count: int = 0
    worker_id: str | None = None
    lease_token: str | None = None
    lease_expires_at: float | None = None
    lease_seconds: float = 0.0
    heartbeat_at: float | None = None
    cancel_requested_at: float | None = None
    finished_at: float | None = None
    checkpoint: Checkpoint | None = None
    result: dict[str, Any] | None = None
    events: list[Event] = field(default_factory=list)


@dataclass(frozen=True)
class Claim:
    job_id: int
    worker_id: str
    lease_token: str
    attempt_count: int
    recovered: bool
    checkpoint_steps: tuple[str, ...]


class JobRepository:
    """Thread-safe in-memory repository that models database conditional updates."""

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self._lock = threading.RLock()
        self._jobs: dict[int, Job] = {}
        self._next_id = 1

    def create_job(
        self,
        *,
        input_version: str,
        task_timeout_seconds: float,
        max_attempts: int = 3,
        retry_of: int | None = None,
    ) -> Job:
        with self._lock:
            job = Job(
                job_id=self._next_id,
                input_version=input_version,
                status="queued",
                stage="queued",
                created_at=self.clock.now,
                task_deadline_at=self.clock.now + task_timeout_seconds,
                max_attempts=max(1, max_attempts),
                retry_of=retry_of,
            )
            self._next_id += 1
            self._jobs[job.job_id] = job
            self._append_event(
                job,
                event_type="job_created",
                from_status=None,
                to_status="queued",
                detail={
                    "input_version": input_version,
                    "retry_of": retry_of,
                },
            )
            return job

    def get(self, job_id: int) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def claim(
        self,
        job_id: int,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> Claim | None:
        with self._lock:
            job = self._jobs[job_id]
            if self.clock.now >= job.task_deadline_at:
                if job.status not in TERMINAL_STATUSES:
                    self._transition(
                        job,
                        "timed_out",
                        event_type="task_deadline_exceeded",
                        detail={"worker_id": worker_id},
                    )
                    self._clear_lease(job)
                return None

            recovered = (
                job.status == "running"
                and job.lease_expires_at is not None
                and job.lease_expires_at <= self.clock.now
            )
            if job.status != "queued" and not recovered:
                return None

            if job.attempt_count >= job.max_attempts:
                if recovered:
                    self._transition(
                        job,
                        "failed",
                        event_type="recovery_attempts_exhausted",
                        detail={
                            "attempt_count": job.attempt_count,
                            "max_attempts": job.max_attempts,
                        },
                    )
                    self._clear_lease(job)
                return None

            old_status = job.status
            job.attempt_count += 1
            job.worker_id = worker_id
            job.lease_token = uuid.uuid4().hex
            job.lease_seconds = max(1.0, lease_seconds)
            job.lease_expires_at = self.clock.now + job.lease_seconds
            job.heartbeat_at = self.clock.now
            job.stage = "recovering" if recovered else "running"

            if recovered:
                self._append_event(
                    job,
                    event_type="lease_recovered",
                    from_status="running",
                    to_status="running",
                    detail={
                        "worker_id": worker_id,
                        "attempt_count": job.attempt_count,
                        "checkpoint_steps": list(
                            job.checkpoint.completed_steps
                            if job.checkpoint
                            else []
                        ),
                    },
                )
            else:
                self._transition(
                    job,
                    "running",
                    event_type="job_claimed",
                    detail={
                        "worker_id": worker_id,
                        "attempt_count": job.attempt_count,
                    },
                    expected_from=old_status,
                )

            return Claim(
                job_id=job.job_id,
                worker_id=worker_id,
                lease_token=job.lease_token,
                attempt_count=job.attempt_count,
                recovered=recovered,
                checkpoint_steps=tuple(
                    job.checkpoint.completed_steps if job.checkpoint else []
                ),
            )

    def heartbeat(self, job_id: int, *, lease_token: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            self._require_active_lease(job, lease_token)
            job.heartbeat_at = self.clock.now
            job.lease_expires_at = self.clock.now + job.lease_seconds
            self._append_event(
                job,
                event_type="heartbeat",
                from_status=job.status,
                to_status=job.status,
                detail={"stage": job.stage},
            )

    def save_checkpoint(
        self,
        job_id: int,
        *,
        lease_token: str,
        completed_step: str,
        payload: dict[str, Any],
        input_version: str,
    ) -> Checkpoint:
        with self._lock:
            job = self._jobs[job_id]
            self._require_active_lease(job, lease_token)
            if input_version != job.input_version:
                raise CheckpointConflict(
                    "checkpoint input version does not match immutable job input"
                )
            if job.checkpoint is None:
                job.checkpoint = Checkpoint(input_version=input_version)
            if job.checkpoint.input_version != input_version:
                raise CheckpointConflict(
                    "existing checkpoint belongs to another input version"
                )
            if completed_step not in job.checkpoint.completed_steps:
                job.checkpoint.completed_steps.append(completed_step)
            job.checkpoint.payload.update(payload)
            job.checkpoint.saved_at = self.clock.now
            job.stage = completed_step
            job.heartbeat_at = self.clock.now
            job.lease_expires_at = self.clock.now + job.lease_seconds
            self._append_event(
                job,
                event_type="checkpoint_saved",
                from_status=job.status,
                to_status=job.status,
                detail={
                    "completed_step": completed_step,
                    "completed_steps": list(job.checkpoint.completed_steps),
                },
            )
            return job.checkpoint

    def remaining_steps(
        self,
        job_id: int,
        *,
        ordered_steps: list[str],
        input_version: str,
    ) -> list[str]:
        with self._lock:
            job = self._jobs[job_id]
            if input_version != job.input_version:
                raise CheckpointConflict("resume input version mismatch")
            completed = (
                set(job.checkpoint.completed_steps) if job.checkpoint else set()
            )
            return [step for step in ordered_steps if step not in completed]

    def request_cancel(self, job_id: int) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job.status in {"cancel_requested", "cancelled"}:
                return {"status": job.status, "replayed": True}
            if job.status in TERMINAL_STATUSES:
                raise StateConflict(
                    f"terminal job {job.status} cannot be cancelled"
                )
            job.cancel_requested_at = self.clock.now
            self._transition(
                job,
                "cancel_requested",
                event_type="cancel_requested",
                detail={"requested_at": self.clock.now},
            )
            return {"status": job.status, "replayed": False}

    def acknowledge_cancel(self, job_id: int, *, lease_token: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status != "cancel_requested":
                raise StateConflict(
                    f"job status {job.status} cannot acknowledge cancellation"
                )
            if job.lease_token != lease_token:
                raise LeaseLost("cancellation acknowledgement lease mismatch")
            self._transition(
                job,
                "cancelled",
                event_type="job_cancelled_at_safe_point",
                detail={"worker_id": job.worker_id},
            )
            job.finished_at = self.clock.now
            self._clear_lease(job)

    def complete(
        self,
        job_id: int,
        *,
        lease_token: str,
        result: dict[str, Any],
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            self._require_active_lease(job, lease_token)
            job.result = dict(result)
            self._transition(
                job,
                "succeeded",
                event_type="job_succeeded",
                detail={"result_keys": sorted(result)},
            )
            job.finished_at = self.clock.now
            self._clear_lease(job)

    def reap_task_deadlines(self) -> list[int]:
        with self._lock:
            timed_out: list[int] = []
            for job in self._jobs.values():
                if (
                    job.status not in TERMINAL_STATUSES
                    and self.clock.now >= job.task_deadline_at
                ):
                    self._transition(
                        job,
                        "timed_out",
                        event_type="task_deadline_exceeded",
                        detail={
                            "last_heartbeat_at": job.heartbeat_at,
                            "worker_id": job.worker_id,
                        },
                    )
                    job.finished_at = self.clock.now
                    self._clear_lease(job)
                    timed_out.append(job.job_id)
            return timed_out

    def create_retry(
        self,
        source_job_id: int,
        *,
        task_timeout_seconds: float,
    ) -> Job:
        with self._lock:
            source = self._jobs[source_job_id]
            if source.status not in {"failed", "cancelled", "timed_out"}:
                raise StateConflict(
                    f"job status {source.status} is not retryable"
                )
            return self.create_job(
                input_version=source.input_version,
                task_timeout_seconds=task_timeout_seconds,
                max_attempts=source.max_attempts,
                retry_of=source.job_id,
            )

    def _require_active_lease(self, job: Job, lease_token: str) -> None:
        if job.status != "running":
            raise StateConflict(
                f"job status {job.status} does not accept Worker writes"
            )
        if job.lease_token != lease_token:
            raise LeaseLost("lease token mismatch")
        if (
            job.lease_expires_at is None
            or job.lease_expires_at <= self.clock.now
        ):
            raise LeaseLost("lease expired")

    def _transition(
        self,
        job: Job,
        new_status: str,
        *,
        event_type: str,
        detail: dict[str, Any],
        expected_from: str | None = None,
    ) -> None:
        old_status = job.status
        if expected_from is not None and old_status != expected_from:
            raise StateConflict(
                f"expected {expected_from}, current status is {old_status}"
            )
        if new_status not in ALLOWED_TRANSITIONS[old_status]:
            raise StateConflict(
                f"illegal transition: {old_status} -> {new_status}"
            )
        job.status = new_status
        job.stage = new_status
        self._append_event(
            job,
            event_type=event_type,
            from_status=old_status,
            to_status=new_status,
            detail=detail,
        )

    def _append_event(
        self,
        job: Job,
        *,
        event_type: str,
        from_status: str | None,
        to_status: str,
        detail: dict[str, Any],
    ) -> None:
        job.events.append(
            Event(
                sequence=len(job.events) + 1,
                event_type=event_type,
                occurred_at=self.clock.now,
                from_status=from_status,
                to_status=to_status,
                detail=detail,
            )
        )

    @staticmethod
    def _clear_lease(job: Job) -> None:
        job.worker_id = None
        job.lease_token = None
        job.lease_expires_at = None
        job.lease_seconds = 0.0


class EffectLedger:
    """Idempotency ledger for an external side effect."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, dict[str, Any]] = {}
        self.actual_effect_count = 0
        self.attempt_count = 0

    def apply(self, idempotency_key: str, payload: dict[str, Any]) -> dict:
        with self._lock:
            self.attempt_count += 1
            if idempotency_key in self._results:
                return {
                    "replayed": True,
                    "result": self._results[idempotency_key],
                }
            self.actual_effect_count += 1
            result = {
                "receipt": f"receipt-{self.actual_effect_count}",
                "payload": dict(payload),
            }
            self._results[idempotency_key] = result
            return {"replayed": False, "result": result}


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


class CheckRecorder:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def check(self, condition: bool, name: str, detail: str) -> None:
        self.checks.append(
            Check(name=name, passed=bool(condition), detail=detail)
        )
        if not condition:
            raise AssertionError(f"{name}: {detail}")


def run_experiment() -> dict[str, Any]:
    clock = VirtualClock()
    repository = JobRepository(clock)
    recorder = CheckRecorder()

    # Scenario 1: several Workers receive the same delivery, only one can claim.
    claim_job = repository.create_job(
        input_version="claim-input-v1",
        task_timeout_seconds=120,
    )
    start_barrier = threading.Barrier(8)

    def competing_claim(worker_number: int) -> Claim | None:
        start_barrier.wait()
        return repository.claim(
            claim_job.job_id,
            worker_id=f"claim-worker-{worker_number}",
            lease_seconds=30,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(competing_claim, range(8)))
    winning_claims = [claim for claim in claims if claim is not None]
    recorder.check(
        len(winning_claims) == 1,
        "duplicate_delivery_has_one_owner",
        f"winning_claims={len(winning_claims)}",
    )
    recorder.check(
        claim_job.status == "running",
        "claimed_job_enters_running",
        f"status={claim_job.status}",
    )
    recorder.check(
        claim_job.attempt_count == 1,
        "duplicate_delivery_does_not_increase_attempts",
        f"attempt_count={claim_job.attempt_count}",
    )
    recorder.check(
        sum(event.event_type == "job_claimed" for event in claim_job.events)
        == 1,
        "claim_event_written_once",
        f"events={[event.event_type for event in claim_job.events]}",
    )

    # Scenario 2: cancellation is requested, observed at a safe point, and
    # completion is fenced off after the request.
    cancel_job = repository.create_job(
        input_version="cancel-input-v1",
        task_timeout_seconds=120,
    )
    cancel_claim = repository.claim(
        cancel_job.job_id,
        worker_id="cancel-worker",
        lease_seconds=30,
    )
    assert cancel_claim is not None
    first_cancel = repository.request_cancel(cancel_job.job_id)
    repeated_cancel = repository.request_cancel(cancel_job.job_id)
    recorder.check(
        first_cancel == {
            "status": "cancel_requested",
            "replayed": False,
        },
        "cancel_request_changes_state",
        f"result={first_cancel}",
    )
    recorder.check(
        repeated_cancel["replayed"] is True,
        "cancel_request_is_idempotent",
        f"result={repeated_cancel}",
    )

    completion_blocked = False
    try:
        repository.complete(
            cancel_job.job_id,
            lease_token=cancel_claim.lease_token,
            result={"must_not": "win"},
        )
    except StateConflict:
        completion_blocked = True
    recorder.check(
        completion_blocked,
        "cancel_request_fences_completion",
        f"blocked={completion_blocked}",
    )
    repository.acknowledge_cancel(
        cancel_job.job_id,
        lease_token=cancel_claim.lease_token,
    )
    recorder.check(
        cancel_job.status == "cancelled",
        "worker_cancels_at_safe_point",
        f"status={cancel_job.status}",
    )
    recorder.check(
        cancel_job.lease_token is None,
        "cancel_releases_lease",
        f"lease_token={cancel_job.lease_token}",
    )
    terminal_cancel_replay = repository.request_cancel(cancel_job.job_id)
    recorder.check(
        terminal_cancel_replay["replayed"] is True,
        "terminal_cancel_replay_has_no_new_effect",
        f"result={terminal_cancel_replay}",
    )

    # Scenario 3: Worker A crashes after an external effect but before saving
    # that step. Worker B reclaims the expired lease and resumes from checkpoint.
    recovery_job = repository.create_job(
        input_version="recovery-input-v1",
        task_timeout_seconds=120,
        max_attempts=3,
    )
    worker_a = repository.claim(
        recovery_job.job_id,
        worker_id="worker-a",
        lease_seconds=10,
    )
    assert worker_a is not None
    repository.save_checkpoint(
        recovery_job.job_id,
        lease_token=worker_a.lease_token,
        completed_step="parse",
        payload={"parsed_rows": 20},
        input_version="recovery-input-v1",
    )
    recorder.check(
        recovery_job.checkpoint is not None
        and recovery_job.checkpoint.completed_steps == ["parse"],
        "checkpoint_persists_completed_step",
        f"steps={recovery_job.checkpoint.completed_steps if recovery_job.checkpoint else None}",
    )

    effects = EffectLedger()
    effect_key = f"push:{recovery_job.job_id}:budget-v1"
    first_effect = effects.apply(
        effect_key,
        {"job_id": recovery_job.job_id, "amount": 1280},
    )
    recorder.check(
        first_effect["replayed"] is False
        and effects.actual_effect_count == 1,
        "first_external_effect_applied",
        f"result={first_effect}",
    )
    # Simulated crash: no "push" checkpoint and no completion.
    clock.advance(11)
    worker_b = repository.claim(
        recovery_job.job_id,
        worker_id="worker-b",
        lease_seconds=10,
    )
    assert worker_b is not None
    recorder.check(
        worker_b.recovered is True and worker_b.attempt_count == 2,
        "expired_lease_recovered_by_new_worker",
        f"claim={asdict(worker_b)}",
    )

    remaining = repository.remaining_steps(
        recovery_job.job_id,
        ordered_steps=["parse", "push", "build_preview"],
        input_version="recovery-input-v1",
    )
    recorder.check(
        remaining == ["push", "build_preview"],
        "resume_skips_checkpointed_work",
        f"remaining={remaining}",
    )

    stale_worker_blocked = False
    try:
        repository.save_checkpoint(
            recovery_job.job_id,
            lease_token=worker_a.lease_token,
            completed_step="stale-worker-write",
            payload={},
            input_version="recovery-input-v1",
        )
    except LeaseLost:
        stale_worker_blocked = True
    recorder.check(
        stale_worker_blocked,
        "old_worker_cannot_write_after_recovery",
        f"blocked={stale_worker_blocked}",
    )

    replayed_effect = effects.apply(
        effect_key,
        {"job_id": recovery_job.job_id, "amount": 1280},
    )
    recorder.check(
        replayed_effect["replayed"] is True,
        "recovered_worker_reuses_idempotent_effect",
        f"result={replayed_effect}",
    )
    recorder.check(
        effects.attempt_count == 2 and effects.actual_effect_count == 1,
        "at_least_once_attempt_has_one_business_effect",
        (
            f"attempts={effects.attempt_count}, "
            f"actual_effects={effects.actual_effect_count}"
        ),
    )

    repository.save_checkpoint(
        recovery_job.job_id,
        lease_token=worker_b.lease_token,
        completed_step="push",
        payload={"push_receipt": replayed_effect["result"]["receipt"]},
        input_version="recovery-input-v1",
    )
    repository.save_checkpoint(
        recovery_job.job_id,
        lease_token=worker_b.lease_token,
        completed_step="build_preview",
        payload={"preview_rows": 20},
        input_version="recovery-input-v1",
    )
    repository.complete(
        recovery_job.job_id,
        lease_token=worker_b.lease_token,
        result={"quoted_rows": 20},
    )
    recorder.check(
        recovery_job.status == "succeeded",
        "recovered_job_completes",
        f"status={recovery_job.status}",
    )
    recorder.check(
        recovery_job.checkpoint is not None
        and recovery_job.checkpoint.completed_steps
        == ["parse", "push", "build_preview"],
        "checkpoint_contains_all_steps",
        f"steps={recovery_job.checkpoint.completed_steps if recovery_job.checkpoint else None}",
    )
    recorder.check(
        [event.sequence for event in recovery_job.events]
        == list(range(1, len(recovery_job.events) + 1)),
        "event_sequence_is_contiguous",
        f"sequences={[event.sequence for event in recovery_job.events]}",
    )
    recorder.check(
        any(
            event.event_type == "lease_recovered"
            for event in recovery_job.events
        )
        and recovery_job.events[-1].event_type == "job_succeeded",
        "recovery_and_terminal_events_are_auditable",
        f"events={[event.event_type for event in recovery_job.events]}",
    )

    terminal_mutation_blocked = False
    try:
        repository.request_cancel(recovery_job.job_id)
    except StateConflict:
        terminal_mutation_blocked = True
    recorder.check(
        terminal_mutation_blocked,
        "terminal_state_is_immutable",
        f"blocked={terminal_mutation_blocked}",
    )

    checkpoint_version_blocked = False
    try:
        repository.remaining_steps(
            recovery_job.job_id,
            ordered_steps=["parse", "push", "build_preview"],
            input_version="recovery-input-v2",
        )
    except CheckpointConflict:
        checkpoint_version_blocked = True
    recorder.check(
        checkpoint_version_blocked,
        "stale_input_cannot_reuse_checkpoint",
        f"blocked={checkpoint_version_blocked}",
    )

    # Scenario 4: the business deadline is terminal and a retry gets a new job.
    timeout_job = repository.create_job(
        input_version="timeout-input-v1",
        task_timeout_seconds=20,
    )
    timeout_claim = repository.claim(
        timeout_job.job_id,
        worker_id="slow-worker",
        lease_seconds=30,
    )
    assert timeout_claim is not None
    clock.advance(21)
    timed_out_ids = repository.reap_task_deadlines()
    recorder.check(
        timed_out_ids == [timeout_job.job_id],
        "deadline_reaper_finds_stuck_job",
        f"timed_out_ids={timed_out_ids}",
    )
    recorder.check(
        timeout_job.status == "timed_out"
        and timeout_job.lease_token is None,
        "deadline_timeout_is_terminal_and_releases_lease",
        (
            f"status={timeout_job.status}, "
            f"lease_token={timeout_job.lease_token}"
        ),
    )

    late_completion_blocked = False
    try:
        repository.complete(
            timeout_job.job_id,
            lease_token=timeout_claim.lease_token,
            result={"late": True},
        )
    except StateConflict:
        late_completion_blocked = True
    recorder.check(
        late_completion_blocked,
        "timed_out_worker_cannot_complete_late",
        f"blocked={late_completion_blocked}",
    )

    retry_job = repository.create_retry(
        timeout_job.job_id,
        task_timeout_seconds=30,
    )
    recorder.check(
        retry_job.job_id != timeout_job.job_id
        and retry_job.retry_of == timeout_job.job_id,
        "retry_creates_linked_new_job",
        (
            f"source={timeout_job.job_id}, retry={retry_job.job_id}, "
            f"retry_of={retry_job.retry_of}"
        ),
    )
    recorder.check(
        timeout_job.status == "timed_out"
        and retry_job.status == "queued",
        "retry_preserves_original_terminal_attempt",
        (
            f"source_status={timeout_job.status}, "
            f"retry_status={retry_job.status}"
        ),
    )

    # Scenario 5: expired leases cannot be reclaimed forever.
    exhausted_job = repository.create_job(
        input_version="exhausted-input-v1",
        task_timeout_seconds=100,
        max_attempts=2,
    )
    exhausted_a = repository.claim(
        exhausted_job.job_id,
        worker_id="exhausted-a",
        lease_seconds=5,
    )
    assert exhausted_a is not None
    clock.advance(6)
    exhausted_b = repository.claim(
        exhausted_job.job_id,
        worker_id="exhausted-b",
        lease_seconds=5,
    )
    assert exhausted_b is not None
    clock.advance(6)
    exhausted_c = repository.claim(
        exhausted_job.job_id,
        worker_id="exhausted-c",
        lease_seconds=5,
    )
    recorder.check(
        exhausted_c is None,
        "recovery_stops_after_max_attempts",
        f"third_claim={exhausted_c}",
    )
    recorder.check(
        exhausted_job.status == "failed",
        "exhausted_recovery_becomes_failed",
        f"status={exhausted_job.status}",
    )
    recorder.check(
        exhausted_job.events[-1].event_type
        == "recovery_attempts_exhausted",
        "recovery_exhaustion_is_audited",
        f"last_event={exhausted_job.events[-1].event_type}",
    )

    passed = sum(check.passed for check in recorder.checks)
    return {
        "environment": {
            "network_ports_opened": False,
            "external_services_called": False,
            "persistent_files_created": False,
            "database": "thread-safe in-memory repository",
            "clock": "virtual",
        },
        "atomic_claim": {
            "competing_workers": 8,
            "winning_claims": len(winning_claims),
            "attempt_count": claim_job.attempt_count,
        },
        "cancellation": {
            "final_status": cancel_job.status,
            "request_replayed": repeated_cancel["replayed"],
            "event_types": [
                event.event_type for event in cancel_job.events
            ],
        },
        "lease_checkpoint_recovery": {
            "final_status": recovery_job.status,
            "attempt_count": recovery_job.attempt_count,
            "remaining_steps_on_resume": remaining,
            "checkpoint_steps": (
                recovery_job.checkpoint.completed_steps
                if recovery_job.checkpoint
                else []
            ),
            "effect_attempts": effects.attempt_count,
            "actual_business_effects": effects.actual_effect_count,
            "event_types": [
                event.event_type for event in recovery_job.events
            ],
        },
        "timeout_and_retry": {
            "source_job_id": timeout_job.job_id,
            "source_status": timeout_job.status,
            "retry_job_id": retry_job.job_id,
            "retry_status": retry_job.status,
            "retry_of": retry_job.retry_of,
        },
        "bounded_recovery": {
            "attempt_count": exhausted_job.attempt_count,
            "max_attempts": exhausted_job.max_attempts,
            "final_status": exhausted_job.status,
        },
        "checks": {
            "passed": passed,
            "total": len(recorder.checks),
            "all_assertions_passed": passed == len(recorder.checks),
            "items": [asdict(check) for check in recorder.checks],
        },
    }


def main() -> None:
    result = run_experiment()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
