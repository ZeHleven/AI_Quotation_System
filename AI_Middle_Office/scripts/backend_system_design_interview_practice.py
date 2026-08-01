from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DesignConfig:
    api_returns_202: bool
    mysql_is_job_source_of_truth: bool
    redis_is_only_business_truth: bool
    task_and_outbox_same_transaction: bool
    consumer_atomic_claim: bool
    worker_checkpoint: bool
    external_effect_idempotency: bool
    retries_are_bounded: bool
    end_to_end_deadline: bool
    queue_is_bounded: bool
    dependency_circuit_breaker: bool
    degraded_result_is_marked: bool
    high_risk_human_gate: bool
    trace_and_stage_metrics: bool
    tenant_scope_enforced: bool
    immutable_audit_events: bool
    large_files_use_object_storage: bool
    result_quality_gate: bool


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    recommendation: str


def review_design(config: DesignConfig) -> list[Finding]:
    """Review one long-running, high-risk AI quotation architecture."""

    findings: list[Finding] = []

    def add(
        condition: bool,
        code: str,
        severity: str,
        message: str,
        recommendation: str,
    ) -> None:
        if condition:
            findings.append(
                Finding(
                    code=code,
                    severity=severity,
                    message=message,
                    recommendation=recommendation,
                )
            )

    add(
        not config.api_returns_202,
        "SYNC_LONG_RUNNING_API",
        "high",
        "Long AI work is held inside the request lifecycle.",
        "Persist a job, return 202, and expose status/events.",
    )
    add(
        not config.mysql_is_job_source_of_truth,
        "NO_DURABLE_JOB_TRUTH",
        "critical",
        "The job lifecycle has no durable business source of truth.",
        "Persist status, input version, result, and events in MySQL.",
    )
    add(
        config.redis_is_only_business_truth,
        "REDIS_AS_ONLY_BUSINESS_TRUTH",
        "critical",
        "Recoverable cache or broker data is being treated as business truth.",
        "Keep authoritative quotation and audit facts in MySQL.",
    )
    add(
        not config.task_and_outbox_same_transaction,
        "DB_BROKER_DUAL_WRITE_GAP",
        "high",
        "A crash can leave a committed job undispatched.",
        "Write job and Outbox in one transaction; publish idempotently.",
    )
    add(
        not config.consumer_atomic_claim,
        "DUPLICATE_WORKER_OWNERSHIP",
        "high",
        "Duplicate delivery can create multiple active Workers.",
        "Use conditional update or lease/fencing-token ownership.",
    )
    add(
        not config.worker_checkpoint,
        "FULL_RESTART_AFTER_CRASH",
        "medium",
        "Long tasks must restart from the beginning after a crash.",
        "Persist checkpoints at safe, versioned boundaries.",
    )
    add(
        not config.external_effect_idempotency,
        "DUPLICATE_EXTERNAL_EFFECT",
        "critical",
        "Redelivery can duplicate push, notification, or billing effects.",
        "Use a stable business idempotency key and unique ledger.",
    )
    add(
        not config.retries_are_bounded,
        "UNBOUNDED_RETRY",
        "high",
        "A poison task or failed dependency can retry forever.",
        "Classify errors and cap attempts, delay, and total budget.",
    )
    add(
        not config.end_to_end_deadline,
        "NO_END_TO_END_DEADLINE",
        "high",
        "Local timeouts can exceed the user's total latency budget.",
        "Propagate remaining deadline through every dependency call.",
    )
    add(
        not config.queue_is_bounded,
        "UNBOUNDED_QUEUE",
        "high",
        "Overload becomes growing wait time and memory pressure.",
        "Apply admission control, bounded queues, and backpressure.",
    )
    add(
        not config.dependency_circuit_breaker,
        "FAILURE_AMPLIFICATION",
        "medium",
        "A persistently failed dependency keeps receiving calls.",
        "Add dependency-scoped circuit breaking and half-open probes.",
    )
    add(
        not config.degraded_result_is_marked,
        "SILENT_DEGRADATION",
        "critical",
        "Users cannot distinguish normal output from weaker fallback data.",
        "Expose fallback source, age, version, and review requirement.",
    )
    add(
        not config.high_risk_human_gate,
        "NO_HUMAN_GATE",
        "critical",
        "AI-generated prices can directly create a business side effect.",
        "Require review before final quotation push.",
    )
    add(
        not config.trace_and_stage_metrics,
        "INSUFFICIENT_OBSERVABILITY",
        "high",
        "The slow or failed stage cannot be located reliably.",
        "Record trace ID, stage duration, retry, queue wait, and dependency.",
    )
    add(
        not config.tenant_scope_enforced,
        "TENANT_DATA_LEAK_RISK",
        "critical",
        "Resource access is not constrained by account or ownership.",
        "Enforce tenant scope in queries and authorization.",
    )
    add(
        not config.immutable_audit_events,
        "NO_AUDIT_TRAIL",
        "high",
        "State changes and manual decisions cannot be reconstructed.",
        "Append immutable lifecycle and decision events.",
    )
    add(
        not config.large_files_use_object_storage,
        "LARGE_FILE_IN_HOT_DATABASE",
        "medium",
        "Large files inflate hot rows, backups, and list queries.",
        "Store objects separately and persist hashes plus references.",
    )
    add(
        not config.result_quality_gate,
        "NO_RESULT_QUALITY_GATE",
        "critical",
        "Missing rows or unsupported evidence can pass as a valid quote.",
        "Block incomplete rows and unsupported high-risk conclusions.",
    )
    return findings


@dataclass(frozen=True)
class CapacityInput:
    peak_jobs_per_hour: float
    average_model_seconds: float
    retry_amplification: float
    burst_factor: float
    target_utilization: float


def estimate_model_capacity(inputs: CapacityInput) -> dict[str, Any]:
    base_arrival_rps = inputs.peak_jobs_per_hour / 3600
    base_concurrency = base_arrival_rps * inputs.average_model_seconds
    peak_demand = (
        base_concurrency
        * inputs.retry_amplification
        * inputs.burst_factor
    )
    required_concurrency = math.ceil(
        peak_demand / inputs.target_utilization
    )
    actual_target_utilization = peak_demand / required_concurrency
    previous_concurrency_utilization = (
        peak_demand / (required_concurrency - 1)
        if required_concurrency > 1
        else float("inf")
    )
    return {
        "base_arrival_rps": round(base_arrival_rps, 4),
        "base_average_concurrency": round(base_concurrency, 4),
        "retry_and_burst_adjusted_demand": round(peak_demand, 4),
        "target_utilization": inputs.target_utilization,
        "required_model_concurrency": required_concurrency,
        "utilization_at_required_concurrency": round(
            actual_target_utilization,
            4,
        ),
        "utilization_with_one_fewer_slot": round(
            previous_concurrency_utilization,
            4,
        ),
    }


@dataclass(frozen=True)
class Incident:
    incident_id: str
    title: str
    signals: dict[str, float | int | bool]


@dataclass(frozen=True)
class Diagnosis:
    code: str
    hypothesis: str
    evidence: tuple[str, ...]
    immediate_actions: tuple[str, ...]
    permanent_fixes: tuple[str, ...]


def diagnose_incident(incident: Incident) -> Diagnosis:
    signals = incident.signals

    if (
        signals.get("created_jobs_rate", 0) > 0
        and signals.get("oldest_undispatched_seconds", 0) > 60
        and signals.get("outbox_pending", 0) > 0
        and signals.get("broker_healthy") is True
        and signals.get("worker_idle_percent", 0) > 50
    ):
        return Diagnosis(
            code="DISPATCHER_STALLED",
            hypothesis="Jobs commit successfully, but the Outbox dispatcher is not publishing them.",
            evidence=(
                "Job creation continues",
                "Undispatched Outbox age is increasing",
                "Broker is healthy while Workers are idle",
            ),
            immediate_actions=(
                "Pause non-essential job admission",
                "Restart or fail over the dispatcher",
                "Republish pending Outbox rows with the same event IDs",
            ),
            permanent_fixes=(
                "Alert on oldest undispatched Outbox age",
                "Run more than one safe dispatcher",
                "Keep publish and consume paths idempotent",
            ),
        )

    if (
        signals.get("queue_growth_per_minute", 0) > 0
        and signals.get("worker_busy_percent", 0) > 90
        and signals.get("model_utilization_percent", 0) > 95
        and signals.get("database_cpu_percent", 100) < 50
        and signals.get("completed_rate_flat") is True
    ):
        return Diagnosis(
            code="MODEL_CAPACITY_SATURATION",
            hypothesis="The model dependency is saturated; extra input becomes queue wait.",
            evidence=(
                "Queue and oldest-job age are growing",
                "Workers and model slots are saturated",
                "Completed throughput is flat while database CPU is low",
            ),
            immediate_actions=(
                "Apply admission control and return retry guidance",
                "Pause low-priority batch work",
                "Use only an already-evaluated backup route if allowed",
            ),
            permanent_fixes=(
                "Capacity-test model concurrency and batch size",
                "Split interactive and batch queues",
                "Add model-level concurrency and cost budgets",
            ),
        )

    if (
        signals.get("broker_redeliveries", 0) > 0
        and signals.get("external_duplicate_effects", 0) > 0
        and signals.get("idempotency_ledger_hit_rate_percent", 100) < 1
    ):
        return Diagnosis(
            code="SIDE_EFFECT_IDEMPOTENCY_GAP",
            hypothesis="At-least-once delivery is repeating an unprotected external side effect.",
            evidence=(
                "Broker redelivery is present",
                "The same business object has multiple external receipts",
                "Idempotency ledger reuse is absent",
            ),
            immediate_actions=(
                "Freeze the affected push operation",
                "Preserve job, event, broker, and external receipt evidence",
                "Reconcile duplicates before resuming",
            ),
            permanent_fixes=(
                "Create a stable operation plus object plus version idempotency key",
                "Add a unique idempotency ledger",
                "Return the first receipt on replay",
            ),
        )

    if (
        signals.get("cache_hit_rate_drop_points", 0) > 40
        and signals.get("database_qps_multiplier", 0) > 3
        and signals.get("ttl_spread_seconds", 999) < 5
    ):
        return Diagnosis(
            code="CACHE_AVALANCHE",
            hypothesis="Many cache keys expire together and create a database fallback spike.",
            evidence=(
                "Cache hit rate drops abruptly",
                "Database QPS rises several times",
                "Key TTLs are concentrated in a narrow window",
            ),
            immediate_actions=(
                "Rate-limit expensive cache misses",
                "Serve explicitly marked stale values where safe",
                "Warm the highest-value keys with single-flight protection",
            ),
            permanent_fixes=(
                "Add randomized TTL jitter",
                "Use logical expiry and controlled rebuild for hot keys",
                "Monitor miss-source and rebuild concurrency",
            ),
        )

    if (
        signals.get("database_p95_ms", 0) > 500
        and signals.get("rows_examined_per_returned", 0) > 1000
        and signals.get("pool_wait_p95_ms", 0) > 100
    ):
        return Diagnosis(
            code="SQL_ACCESS_PATH_BOTTLENECK",
            hypothesis="Inefficient SQL access is occupying connections and causing pool wait.",
            evidence=(
                "Database P95 and connection-pool wait are both high",
                "Rows examined greatly exceed rows returned",
                "Application CPU is not the main saturation signal",
            ),
            immediate_actions=(
                "Stop or throttle the offending query path",
                "Capture the exact SQL, parameters, and execution plan",
                "Protect the database with a bounded application concurrency",
            ),
            permanent_fixes=(
                "Fix N+1 or add a query-shaped composite index",
                "Validate with EXPLAIN ANALYZE under representative data",
                "Regression-test result correctness and write overhead",
            ),
        )

    if (
        signals.get("rag_error_rate_percent", 0) > 20
        and signals.get("evidence_coverage_percent", 100) < 80
        and signals.get("answer_success_percent", 0) > 95
        and signals.get("degraded_marker_percent", 100) < 5
    ):
        return Diagnosis(
            code="UNSAFE_RAG_DEGRADATION",
            hypothesis="The system keeps returning normal-looking answers after evidence retrieval fails.",
            evidence=(
                "RAG errors and evidence gaps are high",
                "Answer success remains implausibly high",
                "Almost no output is marked degraded",
            ),
            immediate_actions=(
                "Stop automatic high-risk output or push",
                "Mark affected answers as evidence unavailable",
                "Route cases to human review",
            ),
            permanent_fixes=(
                "Enforce an evidence-coverage gate",
                "Track citation validity and unsupported-answer rate",
                "Test RAG failure and recovery explicitly",
            ),
        )

    return Diagnosis(
        code="INSUFFICIENT_EVIDENCE",
        hypothesis="The available signals do not justify a single root-cause claim.",
        evidence=(
            "No diagnostic rule has enough independent supporting signals",
        ),
        immediate_actions=(
            "Preserve traces and compare a known-good time window",
            "Collect stage latency, resource saturation, and recent-change evidence",
            "Avoid broad restarts that destroy evidence",
        ),
        permanent_fixes=(
            "Add missing RED, USE, queue-age, and dependency metrics",
        ),
    )


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
    recorder = CheckRecorder()

    production_candidate = DesignConfig(
        api_returns_202=True,
        mysql_is_job_source_of_truth=True,
        redis_is_only_business_truth=False,
        task_and_outbox_same_transaction=True,
        consumer_atomic_claim=True,
        worker_checkpoint=True,
        external_effect_idempotency=True,
        retries_are_bounded=True,
        end_to_end_deadline=True,
        queue_is_bounded=True,
        dependency_circuit_breaker=True,
        degraded_result_is_marked=True,
        high_risk_human_gate=True,
        trace_and_stage_metrics=True,
        tenant_scope_enforced=True,
        immutable_audit_events=True,
        large_files_use_object_storage=True,
        result_quality_gate=True,
    )
    candidate_findings = review_design(production_candidate)
    recorder.check(
        candidate_findings == [],
        "candidate_design_satisfies_exercise_invariants",
        f"findings={candidate_findings}",
    )

    fragile_design = DesignConfig(
        api_returns_202=False,
        mysql_is_job_source_of_truth=False,
        redis_is_only_business_truth=True,
        task_and_outbox_same_transaction=False,
        consumer_atomic_claim=False,
        worker_checkpoint=False,
        external_effect_idempotency=False,
        retries_are_bounded=False,
        end_to_end_deadline=False,
        queue_is_bounded=False,
        dependency_circuit_breaker=False,
        degraded_result_is_marked=False,
        high_risk_human_gate=False,
        trace_and_stage_metrics=False,
        tenant_scope_enforced=False,
        immutable_audit_events=False,
        large_files_use_object_storage=False,
        result_quality_gate=False,
    )
    fragile_findings = review_design(fragile_design)
    finding_codes = {finding.code for finding in fragile_findings}
    critical_codes = {
        finding.code
        for finding in fragile_findings
        if finding.severity == "critical"
    }
    high_codes = {
        finding.code
        for finding in fragile_findings
        if finding.severity == "high"
    }
    recorder.check(
        len(fragile_findings) == 18,
        "fragile_design_exposes_all_review_dimensions",
        f"finding_count={len(fragile_findings)}",
    )
    for code in (
        "NO_DURABLE_JOB_TRUTH",
        "DB_BROKER_DUAL_WRITE_GAP",
        "DUPLICATE_EXTERNAL_EFFECT",
        "UNBOUNDED_RETRY",
        "NO_END_TO_END_DEADLINE",
        "UNBOUNDED_QUEUE",
        "NO_HUMAN_GATE",
        "TENANT_DATA_LEAK_RISK",
        "NO_RESULT_QUALITY_GATE",
    ):
        recorder.check(
            code in finding_codes,
            f"review_detects_{code.lower()}",
            f"detected={code in finding_codes}",
        )
    recorder.check(
        len(critical_codes) >= 6 and len(high_codes) >= 6,
        "review_separates_critical_and_high_risks",
        f"critical={sorted(critical_codes)}, high={sorted(high_codes)}",
    )

    capacity_input = CapacityInput(
        peak_jobs_per_hour=720,
        average_model_seconds=45,
        retry_amplification=1.10,
        burst_factor=1.50,
        target_utilization=0.70,
    )
    capacity = estimate_model_capacity(capacity_input)
    recorder.check(
        capacity["base_arrival_rps"] == 0.2,
        "capacity_converts_hourly_peak_to_rps",
        f"base_arrival_rps={capacity['base_arrival_rps']}",
    )
    recorder.check(
        capacity["base_average_concurrency"] == 9.0,
        "little_law_estimates_base_concurrency",
        f"base_concurrency={capacity['base_average_concurrency']}",
    )
    recorder.check(
        capacity["retry_and_burst_adjusted_demand"] == 14.85,
        "capacity_includes_retry_and_burst",
        f"demand={capacity['retry_and_burst_adjusted_demand']}",
    )
    recorder.check(
        capacity["required_model_concurrency"] == 22,
        "capacity_rounds_required_slots_up",
        f"required={capacity['required_model_concurrency']}",
    )
    recorder.check(
        capacity["utilization_at_required_concurrency"] <= 0.70,
        "required_capacity_meets_target_utilization",
        f"utilization={capacity['utilization_at_required_concurrency']}",
    )
    recorder.check(
        capacity["utilization_with_one_fewer_slot"] > 0.70,
        "one_fewer_slot_breaks_target_utilization",
        (
            "utilization="
            f"{capacity['utilization_with_one_fewer_slot']}"
        ),
    )

    incidents = [
        Incident(
            incident_id="INC-01",
            title="创建成功但任务不开始",
            signals={
                "created_jobs_rate": 12,
                "oldest_undispatched_seconds": 480,
                "outbox_pending": 160,
                "broker_healthy": True,
                "worker_idle_percent": 90,
            },
        ),
        Incident(
            incident_id="INC-02",
            title="队列持续增长且吞吐不再提升",
            signals={
                "queue_growth_per_minute": 60,
                "worker_busy_percent": 99,
                "model_utilization_percent": 99,
                "database_cpu_percent": 25,
                "completed_rate_flat": True,
            },
        ),
        Incident(
            incident_id="INC-03",
            title="Worker 恢复后重复推送",
            signals={
                "broker_redeliveries": 8,
                "external_duplicate_effects": 3,
                "idempotency_ledger_hit_rate_percent": 0,
            },
        ),
        Incident(
            incident_id="INC-04",
            title="缓存命中率骤降且数据库流量暴涨",
            signals={
                "cache_hit_rate_drop_points": 65,
                "database_qps_multiplier": 5,
                "ttl_spread_seconds": 1,
            },
        ),
        Incident(
            incident_id="INC-05",
            title="数据库和连接池同时变慢",
            signals={
                "database_p95_ms": 1200,
                "rows_examined_per_returned": 8500,
                "pool_wait_p95_ms": 640,
            },
        ),
        Incident(
            incident_id="INC-06",
            title="RAG 故障但回答仍显示正常",
            signals={
                "rag_error_rate_percent": 48,
                "evidence_coverage_percent": 35,
                "answer_success_percent": 99,
                "degraded_marker_percent": 0,
            },
        ),
        Incident(
            incident_id="INC-07",
            title="只有用户反馈系统偶尔慢",
            signals={
                "user_reports": 3,
                "average_latency_ms": 180,
            },
        ),
    ]
    expected_codes = {
        "INC-01": "DISPATCHER_STALLED",
        "INC-02": "MODEL_CAPACITY_SATURATION",
        "INC-03": "SIDE_EFFECT_IDEMPOTENCY_GAP",
        "INC-04": "CACHE_AVALANCHE",
        "INC-05": "SQL_ACCESS_PATH_BOTTLENECK",
        "INC-06": "UNSAFE_RAG_DEGRADATION",
        "INC-07": "INSUFFICIENT_EVIDENCE",
    }
    diagnoses: dict[str, Diagnosis] = {}
    for incident in incidents:
        diagnosis = diagnose_incident(incident)
        diagnoses[incident.incident_id] = diagnosis
        recorder.check(
            diagnosis.code == expected_codes[incident.incident_id],
            f"diagnoses_{incident.incident_id.lower()}",
            (
                f"expected={expected_codes[incident.incident_id]}, "
                f"actual={diagnosis.code}"
            ),
        )
        recorder.check(
            len(diagnosis.evidence) >= 1
            and len(diagnosis.immediate_actions) >= 1
            and len(diagnosis.permanent_fixes) >= 1,
            f"diagnosis_{incident.incident_id.lower()}_is_actionable",
            (
                f"evidence={len(diagnosis.evidence)}, "
                f"immediate={len(diagnosis.immediate_actions)}, "
                f"permanent={len(diagnosis.permanent_fixes)}"
            ),
        )

    recorder.check(
        diagnoses["INC-03"].immediate_actions[0]
        == "Freeze the affected push operation",
        "duplicate_effect_incident_protects_business_first",
        (
            f"first_action="
            f"{diagnoses['INC-03'].immediate_actions[0]}"
        ),
    )
    recorder.check(
        diagnoses["INC-06"].code == "UNSAFE_RAG_DEGRADATION"
        and "human review"
        in " ".join(diagnoses["INC-06"].immediate_actions).lower(),
        "rag_incident_enforces_quality_gate",
        f"actions={diagnoses['INC-06'].immediate_actions}",
    )
    recorder.check(
        diagnoses["INC-07"].code == "INSUFFICIENT_EVIDENCE",
        "ambiguous_incident_does_not_guess_root_cause",
        f"diagnosis={diagnoses['INC-07'].code}",
    )

    passed = sum(check.passed for check in recorder.checks)
    return {
        "environment": {
            "network_ports_opened": False,
            "external_services_called": False,
            "persistent_files_created": False,
            "mode": "deterministic interview drill",
        },
        "architecture_review": {
            "scenario": "long-running high-risk AI quotation",
            "candidate_finding_count": len(candidate_findings),
            "fragile_finding_count": len(fragile_findings),
            "fragile_findings": [
                asdict(finding) for finding in fragile_findings
            ],
        },
        "capacity_estimate": {
            "inputs": asdict(capacity_input),
            "result": capacity,
            "note": "Estimate, not measured production capacity.",
        },
        "incident_drills": [
            {
                "incident_id": incident.incident_id,
                "title": incident.title,
                "signals": incident.signals,
                "diagnosis": asdict(diagnoses[incident.incident_id]),
            }
            for incident in incidents
        ],
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
