from __future__ import annotations

import heapq
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class StageConfig:
    name: str
    concurrency: int
    service_times_seconds: tuple[float, ...]

    @property
    def average_service_seconds(self) -> float:
        return sum(self.service_times_seconds) / len(
            self.service_times_seconds
        )

    @property
    def theoretical_capacity_rps(self) -> float:
        return self.concurrency / self.average_service_seconds

    def service_time_for(self, request_id: int) -> float:
        return self.service_times_seconds[
            request_id % len(self.service_times_seconds)
        ]


@dataclass(frozen=True)
class StageTiming:
    ready_at: float
    started_at: float
    finished_at: float
    queue_wait_seconds: float
    service_seconds: float


@dataclass
class RequestRecord:
    request_id: int
    arrived_at: float
    ready_at: float
    stages: dict[str, StageTiming] = field(default_factory=dict)


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


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def round_ms(seconds: float) -> float:
    return round(seconds * 1000, 2)


def simulate_pipeline(
    *,
    offered_rps: int,
    load_duration_seconds: float,
    slo_seconds: float,
    stages: tuple[StageConfig, ...],
) -> dict[str, Any]:
    request_count = int(offered_rps * load_duration_seconds)
    records = [
        RequestRecord(
            request_id=request_id,
            arrived_at=request_id / offered_rps,
            ready_at=request_id / offered_rps,
        )
        for request_id in range(request_count)
    ]

    for stage in stages:
        available_servers = [0.0] * stage.concurrency
        heapq.heapify(available_servers)
        for record in sorted(
            records,
            key=lambda item: (item.ready_at, item.request_id),
        ):
            server_available_at = heapq.heappop(available_servers)
            started_at = max(record.ready_at, server_available_at)
            service_seconds = stage.service_time_for(record.request_id)
            finished_at = started_at + service_seconds
            timing = StageTiming(
                ready_at=record.ready_at,
                started_at=started_at,
                finished_at=finished_at,
                queue_wait_seconds=started_at - record.ready_at,
                service_seconds=service_seconds,
            )
            record.stages[stage.name] = timing
            record.ready_at = finished_at
            heapq.heappush(available_servers, finished_at)

    latencies = [record.ready_at - record.arrived_at for record in records]
    last_finished_at = max(record.ready_at for record in records)
    makespan_seconds = max(load_duration_seconds, last_finished_at)
    timeout_count = sum(latency > slo_seconds for latency in latencies)
    backlog_at_load_end = sum(
        record.ready_at > load_duration_seconds for record in records
    )

    stage_metrics: dict[str, dict[str, float | int]] = {}
    for stage in stages:
        timings = [record.stages[stage.name] for record in records]
        waits = [timing.queue_wait_seconds for timing in timings]
        busy_in_load_window = sum(
            max(
                0.0,
                min(timing.finished_at, load_duration_seconds)
                - max(timing.started_at, 0.0),
            )
            for timing in timings
        )
        utilization = busy_in_load_window / (
            stage.concurrency * load_duration_seconds
        )
        stage_metrics[stage.name] = {
            "concurrency": stage.concurrency,
            "average_service_ms": round_ms(
                stage.average_service_seconds
            ),
            "theoretical_capacity_rps": round(
                stage.theoretical_capacity_rps,
                2,
            ),
            "utilization_percent": round(
                min(1.0, utilization) * 100,
                2,
            ),
            "p95_queue_wait_ms": round_ms(percentile(waits, 0.95)),
            "max_queue_wait_ms": round_ms(max(waits)),
        }

    throughput_over_makespan = request_count / makespan_seconds
    average_latency = sum(latencies) / len(latencies)
    average_inflight = sum(latencies) / makespan_seconds
    little_law_estimate = throughput_over_makespan * average_latency

    concurrency_events: list[tuple[float, int]] = []
    for record in records:
        concurrency_events.append((record.arrived_at, 1))
        concurrency_events.append((record.ready_at, -1))
    # Finishes at the same instant are applied before new arrivals.
    concurrency_events.sort(key=lambda item: (item[0], item[1]))
    current_inflight = 0
    max_inflight = 0
    for _, delta in concurrency_events:
        current_inflight += delta
        max_inflight = max(max_inflight, current_inflight)

    return {
        "offered_rps": offered_rps,
        "load_duration_seconds": load_duration_seconds,
        "request_count": request_count,
        "slo_ms": round_ms(slo_seconds),
        "latency_ms": {
            "average": round_ms(average_latency),
            "p50": round_ms(percentile(latencies, 0.50)),
            "p95": round_ms(percentile(latencies, 0.95)),
            "p99": round_ms(percentile(latencies, 0.99)),
            "max": round_ms(max(latencies)),
        },
        "timeout_count": timeout_count,
        "timeout_rate_percent": round(
            timeout_count / request_count * 100,
            2,
        ),
        "backlog_at_load_end": backlog_at_load_end,
        "drained_throughput_rps": round(
            throughput_over_makespan,
            2,
        ),
        "successful_throughput_rps": round(
            (request_count - timeout_count) / load_duration_seconds,
            2,
        ),
        "makespan_seconds": round(makespan_seconds, 4),
        "average_inflight": round(average_inflight, 4),
        "max_inflight": max_inflight,
        "little_law": {
            "lambda_rps": round(throughput_over_makespan, 4),
            "average_w_seconds": round(average_latency, 6),
            "lambda_times_w": round(little_law_estimate, 4),
            "measured_average_inflight": round(average_inflight, 4),
        },
        "stages": stage_metrics,
    }


def baseline_stages(
    *,
    model_concurrency: int = 4,
    database_scale: float = 1.0,
) -> tuple[StageConfig, ...]:
    return (
        StageConfig(
            name="api",
            concurrency=16,
            service_times_seconds=(0.002, 0.003, 0.004),
        ),
        StageConfig(
            name="database",
            concurrency=8,
            service_times_seconds=tuple(
                value * database_scale
                for value in (0.012, 0.015, 0.018)
            ),
        ),
        StageConfig(
            name="model",
            concurrency=model_concurrency,
            service_times_seconds=(0.10, 0.12, 0.14, 0.16),
        ),
        StageConfig(
            name="postprocess",
            concurrency=8,
            service_times_seconds=(0.008, 0.010, 0.012),
        ),
    )


def n_plus_one_stages(*, batched: bool) -> tuple[StageConfig, ...]:
    database_seconds = 0.008 if batched else 11 * 0.005
    return (
        StageConfig(
            name="api",
            concurrency=16,
            service_times_seconds=(0.002,),
        ),
        StageConfig(
            name="database",
            concurrency=8,
            service_times_seconds=(database_seconds,),
        ),
        StageConfig(
            name="postprocess",
            concurrency=16,
            service_times_seconds=(0.002,),
        ),
    )


def compact_load_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "offered_rps": result["offered_rps"],
        "p50_ms": result["latency_ms"]["p50"],
        "p95_ms": result["latency_ms"]["p95"],
        "p99_ms": result["latency_ms"]["p99"],
        "timeout_rate_percent": result["timeout_rate_percent"],
        "drained_throughput_rps": result["drained_throughput_rps"],
        "backlog_at_load_end": result["backlog_at_load_end"],
        "model_utilization_percent": result["stages"]["model"][
            "utilization_percent"
        ],
        "model_p95_queue_ms": result["stages"]["model"][
            "p95_queue_wait_ms"
        ],
    }


def run_experiment() -> dict[str, Any]:
    recorder = CheckRecorder()
    slo_seconds = 0.50
    load_duration_seconds = 20.0
    offered_rates = (10, 20, 25, 30, 35, 40)
    stages = baseline_stages()

    results = [
        simulate_pipeline(
            offered_rps=rate,
            load_duration_seconds=load_duration_seconds,
            slo_seconds=slo_seconds,
            stages=stages,
        )
        for rate in offered_rates
    ]
    by_rate = {result["offered_rps"]: result for result in results}
    low_load = by_rate[10]
    high_load = by_rate[40]

    recorder.check(
        low_load["latency_ms"]["p50"]
        <= low_load["latency_ms"]["p95"]
        <= low_load["latency_ms"]["p99"],
        "latency_percentiles_are_ordered",
        f"latency={low_load['latency_ms']}",
    )
    recorder.check(
        low_load["timeout_rate_percent"] == 0,
        "low_load_meets_timeout_slo",
        f"timeout_rate={low_load['timeout_rate_percent']}%",
    )
    recorder.check(
        high_load["latency_ms"]["p95"]
        > low_load["latency_ms"]["p95"] * 5,
        "tail_latency_explodes_after_saturation",
        (
            f"low_p95={low_load['latency_ms']['p95']}ms, "
            f"high_p95={high_load['latency_ms']['p95']}ms"
        ),
    )
    recorder.check(
        high_load["timeout_rate_percent"]
        > low_load["timeout_rate_percent"],
        "timeout_rate_rises_after_saturation",
        (
            f"low={low_load['timeout_rate_percent']}%, "
            f"high={high_load['timeout_rate_percent']}%"
        ),
    )
    recorder.check(
        high_load["backlog_at_load_end"]
        > low_load["backlog_at_load_end"],
        "backlog_grows_after_capacity",
        (
            f"low={low_load['backlog_at_load_end']}, "
            f"high={high_load['backlog_at_load_end']}"
        ),
    )

    theoretical_capacities = {
        stage.name: stage.theoretical_capacity_rps for stage in stages
    }
    theoretical_bottleneck = min(
        theoretical_capacities,
        key=theoretical_capacities.get,  # type: ignore[arg-type]
    )
    observed_bottleneck = max(
        high_load["stages"],
        key=lambda name: high_load["stages"][name]["p95_queue_wait_ms"],
    )
    recorder.check(
        theoretical_bottleneck == "model",
        "service_demand_predicts_model_bottleneck",
        f"capacities={theoretical_capacities}",
    )
    recorder.check(
        observed_bottleneck == "model",
        "stage_queue_wait_locates_model_bottleneck",
        (
            f"p95_waits="
            f"{ {name: data['p95_queue_wait_ms'] for name, data in high_load['stages'].items()} }"
        ),
    )
    recorder.check(
        high_load["stages"]["model"]["utilization_percent"] >= 99,
        "model_is_saturated_at_high_load",
        (
            f"utilization="
            f"{high_load['stages']['model']['utilization_percent']}%"
        ),
    )

    passing_rates = [
        result["offered_rps"]
        for result in results
        if result["latency_ms"]["p95"] <= slo_seconds * 1000
        and result["timeout_rate_percent"] <= 1.0
    ]
    empirical_slo_capacity_rps = max(passing_rates)
    theoretical_capacity_rps = min(theoretical_capacities.values())
    safe_planned_capacity_rps = math.floor(theoretical_capacity_rps * 0.70)
    recorder.check(
        empirical_slo_capacity_rps <= theoretical_capacity_rps,
        "empirical_capacity_does_not_exceed_bottleneck_capacity",
        (
            f"empirical={empirical_slo_capacity_rps}, "
            f"theoretical={theoretical_capacity_rps:.2f}"
        ),
    )
    recorder.check(
        safe_planned_capacity_rps < empirical_slo_capacity_rps,
        "planned_capacity_keeps_headroom",
        (
            f"safe={safe_planned_capacity_rps}, "
            f"empirical={empirical_slo_capacity_rps}"
        ),
    )

    little = by_rate[25]["little_law"]
    recorder.check(
        abs(
            little["lambda_times_w"]
            - little["measured_average_inflight"]
        )
        <= 0.0001,
        "little_law_matches_measured_inflight",
        f"little_law={little}",
    )
    recorder.check(
        by_rate[25]["max_inflight"]
        >= by_rate[25]["average_inflight"],
        "max_inflight_is_above_average",
        (
            f"max={by_rate[25]['max_inflight']}, "
            f"avg={by_rate[25]['average_inflight']}"
        ),
    )

    # Single-variable comparison 1: speed up a non-bottleneck database.
    faster_database = simulate_pipeline(
        offered_rps=40,
        load_duration_seconds=load_duration_seconds,
        slo_seconds=slo_seconds,
        stages=baseline_stages(database_scale=0.5),
    )
    database_p95_change_percent = (
        (
            high_load["latency_ms"]["p95"]
            - faster_database["latency_ms"]["p95"]
        )
        / high_load["latency_ms"]["p95"]
        * 100
    )
    recorder.check(
        database_p95_change_percent < 10,
        "optimizing_non_bottleneck_has_small_gain",
        f"p95_improvement={database_p95_change_percent:.2f}%",
    )
    recorder.check(
        faster_database["stages"]["model"]["p95_queue_wait_ms"]
        > slo_seconds * 1000,
        "database_optimization_leaves_model_queue",
        (
            f"model_p95_queue="
            f"{faster_database['stages']['model']['p95_queue_wait_ms']}ms"
        ),
    )

    # Single-variable comparison 2: expand the actual bottleneck.
    larger_model_pool = simulate_pipeline(
        offered_rps=40,
        load_duration_seconds=load_duration_seconds,
        slo_seconds=slo_seconds,
        stages=baseline_stages(model_concurrency=6),
    )
    model_p95_improvement_percent = (
        (
            high_load["latency_ms"]["p95"]
            - larger_model_pool["latency_ms"]["p95"]
        )
        / high_load["latency_ms"]["p95"]
        * 100
    )
    recorder.check(
        model_p95_improvement_percent > 80,
        "expanding_bottleneck_removes_tail_queue",
        f"p95_improvement={model_p95_improvement_percent:.2f}%",
    )
    recorder.check(
        larger_model_pool["timeout_rate_percent"]
        < high_load["timeout_rate_percent"],
        "expanding_bottleneck_reduces_timeouts",
        (
            f"before={high_load['timeout_rate_percent']}%, "
            f"after={larger_model_pool['timeout_rate_percent']}%"
        ),
    )

    drained_difference_percent = (
        abs(
            by_rate[40]["drained_throughput_rps"]
            - by_rate[35]["drained_throughput_rps"]
        )
        / by_rate[35]["drained_throughput_rps"]
        * 100
    )
    recorder.check(
        drained_difference_percent < 10,
        "throughput_flattens_near_bottleneck_capacity",
        (
            f"rps35={by_rate[35]['drained_throughput_rps']}, "
            f"rps40={by_rate[40]['drained_throughput_rps']}"
        ),
    )

    # N+1 comparison at the same load.
    n_plus_one = simulate_pipeline(
        offered_rps=180,
        load_duration_seconds=10.0,
        slo_seconds=0.25,
        stages=n_plus_one_stages(batched=False),
    )
    batched = simulate_pipeline(
        offered_rps=180,
        load_duration_seconds=10.0,
        slo_seconds=0.25,
        stages=n_plus_one_stages(batched=True),
    )
    recorder.check(
        n_plus_one["stages"]["database"]["average_service_ms"] == 55.0
        and batched["stages"]["database"]["average_service_ms"] == 8.0,
        "n_plus_one_and_batch_service_demands_differ",
        (
            f"n_plus_one={n_plus_one['stages']['database']['average_service_ms']}ms, "
            f"batch={batched['stages']['database']['average_service_ms']}ms"
        ),
    )
    recorder.check(
        n_plus_one["latency_ms"]["p95"]
        > batched["latency_ms"]["p95"] * 10,
        "batch_query_removes_n_plus_one_tail_latency",
        (
            f"n_plus_one_p95={n_plus_one['latency_ms']['p95']}ms, "
            f"batch_p95={batched['latency_ms']['p95']}ms"
        ),
    )
    recorder.check(
        n_plus_one["timeout_rate_percent"]
        > batched["timeout_rate_percent"],
        "batch_query_reduces_timeout_rate",
        (
            f"n_plus_one={n_plus_one['timeout_rate_percent']}%, "
            f"batch={batched['timeout_rate_percent']}%"
        ),
    )
    recorder.check(
        n_plus_one["stages"]["database"]["utilization_percent"] >= 99
        and batched["stages"]["database"]["utilization_percent"] < 25,
        "use_metrics_confirm_database_saturation",
        (
            f"n_plus_one_util={n_plus_one['stages']['database']['utilization_percent']}%, "
            f"batch_util={batched['stages']['database']['utilization_percent']}%"
        ),
    )

    passed = sum(check.passed for check in recorder.checks)
    return {
        "environment": {
            "mode": "deterministic_discrete_event_simulation",
            "network_ports_opened": False,
            "external_services_called": False,
            "persistent_files_created": False,
            "real_provider_cost": 0,
        },
        "slo": {
            "p95_latency_ms": slo_seconds * 1000,
            "timeout_rate_percent": 1.0,
        },
        "stage_capacity_model": {
            name: round(capacity, 2)
            for name, capacity in theoretical_capacities.items()
        },
        "load_steps": [compact_load_row(result) for result in results],
        "capacity": {
            "theoretical_bottleneck": theoretical_bottleneck,
            "theoretical_capacity_rps": round(
                theoretical_capacity_rps,
                2,
            ),
            "empirical_slo_capacity_rps": empirical_slo_capacity_rps,
            "safe_planned_capacity_rps_at_70_percent": (
                safe_planned_capacity_rps
            ),
        },
        "little_law_at_25_rps": little,
        "single_variable_comparison_at_40_rps": {
            "baseline": {
                "p95_ms": high_load["latency_ms"]["p95"],
                "timeout_rate_percent": high_load[
                    "timeout_rate_percent"
                ],
                "model_p95_queue_ms": high_load["stages"]["model"][
                    "p95_queue_wait_ms"
                ],
            },
            "database_twice_as_fast": {
                "p95_ms": faster_database["latency_ms"]["p95"],
                "p95_improvement_percent": round(
                    database_p95_change_percent,
                    2,
                ),
                "model_p95_queue_ms": faster_database["stages"]["model"][
                    "p95_queue_wait_ms"
                ],
            },
            "model_concurrency_4_to_6": {
                "p95_ms": larger_model_pool["latency_ms"]["p95"],
                "p95_improvement_percent": round(
                    model_p95_improvement_percent,
                    2,
                ),
                "timeout_rate_percent": larger_model_pool[
                    "timeout_rate_percent"
                ],
            },
        },
        "n_plus_one_comparison_at_180_rps": {
            "eleven_queries": {
                "database_service_ms": n_plus_one["stages"]["database"][
                    "average_service_ms"
                ],
                "database_utilization_percent": n_plus_one["stages"][
                    "database"
                ]["utilization_percent"],
                "p95_ms": n_plus_one["latency_ms"]["p95"],
                "timeout_rate_percent": n_plus_one[
                    "timeout_rate_percent"
                ],
            },
            "one_batch_query": {
                "database_service_ms": batched["stages"]["database"][
                    "average_service_ms"
                ],
                "database_utilization_percent": batched["stages"][
                    "database"
                ]["utilization_percent"],
                "p95_ms": batched["latency_ms"]["p95"],
                "timeout_rate_percent": batched[
                    "timeout_rate_percent"
                ],
            },
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
