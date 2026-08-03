from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any, Literal


PairOrder = Literal["baseline_first", "candidate_first"]


def paired_order(
    *,
    repetition_index: int,
    case_index: int,
) -> PairOrder:
    """Return a deterministic balanced AB/BA order."""

    return (
        "baseline_first"
        if (int(repetition_index) + int(case_index)) % 2 == 0
        else "candidate_first"
    )


def rotated_case_ids(
    case_ids: Sequence[str],
    *,
    repetition_index: int,
) -> list[str]:
    values = list(case_ids)
    if not values:
        return []
    offset = int(repetition_index) % len(values)
    return values[offset:] + values[:offset]


def summarize_paired_latency(
    samples: Sequence[dict[str, Any]],
    *,
    expected_pair_count: int,
    measured_pairs_per_case: int,
    paired_delta_p95_ms_max: int,
) -> dict[str, Any]:
    rows = [dict(item) for item in samples]
    valid_rows = [
        item
        for item in rows
        if item.get("baseline_latency_ms") is not None
        and item.get("candidate_latency_ms") is not None
        and not item.get("baseline_error_code")
        and not item.get("candidate_error_code")
    ]
    baseline_error_count = sum(
        bool(item.get("baseline_error_code")) for item in rows
    )
    candidate_error_count = sum(
        bool(item.get("candidate_error_code")) for item in rows
    )
    trigger_mismatch_count = sum(
        bool(item.get("trigger_mismatch")) for item in rows
    )
    order_balance = _order_balance(
        rows,
        measured_pairs_per_case=measured_pairs_per_case,
    )
    overall = _aggregate(valid_rows)
    paired_p95 = overall.get("paired_delta_p95_ms")
    checks = {
        "valid_pair_count": len(valid_rows) == expected_pair_count,
        "order_balance": order_balance["mismatch_count"] == 0,
        "baseline_error_count": baseline_error_count == 0,
        "candidate_error_count": candidate_error_count == 0,
        "trigger_mismatch_count": trigger_mismatch_count == 0,
        "paired_delta_p95_ms": (
            paired_p95 is not None
            and paired_p95 <= paired_delta_p95_ms_max
        ),
    }
    return {
        "schema_version": "bid_intake_paired_latency_summary_v1",
        "sample_count": len(rows),
        "valid_pair_count": len(valid_rows),
        "baseline_error_count": baseline_error_count,
        "candidate_error_count": candidate_error_count,
        "trigger_mismatch_count": trigger_mismatch_count,
        "order_balance": order_balance,
        "overall": overall,
        "groups": {
            "graph_trigger_expected": _aggregate(
                [
                    item
                    for item in valid_rows
                    if item.get("graph_trigger_expected")
                ]
            ),
            "graph_no_trigger_expected": _aggregate(
                [
                    item
                    for item in valid_rows
                    if not item.get("graph_trigger_expected")
                ]
            ),
            "candidate_graph_path_present": _aggregate(
                [
                    item
                    for item in valid_rows
                    if int(item.get("candidate_path_count") or 0) > 0
                ]
            ),
            "candidate_graph_path_absent": _aggregate(
                [
                    item
                    for item in valid_rows
                    if int(item.get("candidate_path_count") or 0) == 0
                ]
            ),
            "baseline_first": _aggregate(
                [
                    item
                    for item in valid_rows
                    if item.get("order") == "baseline_first"
                ]
            ),
            "candidate_first": _aggregate(
                [
                    item
                    for item in valid_rows
                    if item.get("order") == "candidate_first"
                ]
            ),
        },
        "acceptance": {
            "passed": all(checks.values()),
            "thresholds": {
                "expected_pair_count": expected_pair_count,
                "measured_pairs_per_case": measured_pairs_per_case,
                "paired_delta_p95_ms_max": (
                    paired_delta_p95_ms_max
                ),
                "baseline_error_count_max": 0,
                "candidate_error_count_max": 0,
                "trigger_mismatch_count_max": 0,
            },
            "checks": checks,
            "failed_checks": [
                key for key, passed in checks.items() if not passed
            ],
        },
    }


def render_paired_latency_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    overall = summary["overall"]
    acceptance = summary["acceptance"]
    groups = summary["groups"]
    lines = [
        "# 选择性图扩展配对延迟评测",
        "",
        "## 结论",
        "",
        (
            "**通过**"
            if acceptance["passed"]
            else "**未通过**"
        ),
        "",
        (
            f"- 有效配对：{summary['valid_pair_count']}/"
            f"{summary['sample_count']}"
        ),
        (
            "- 配对延迟差中位数："
            f"{overall.get('paired_delta_median_ms')}ms"
        ),
        (
            "- 配对延迟差P95："
            f"{overall.get('paired_delta_p95_ms')}ms"
        ),
        (
            "- 门槛：P95不超过"
            f"{acceptance['thresholds']['paired_delta_p95_ms_max']}ms"
        ),
        (
            "- Baseline/Candidate错误："
            f"{summary['baseline_error_count']}/"
            f"{summary['candidate_error_count']}"
        ),
        (
            "- 触发标签不一致："
            f"{summary['trigger_mismatch_count']}"
        ),
        "",
        "## 分组结果",
        "",
        "| 分组 | 样本 | 中位差(ms) | P90差(ms) | P95差(ms) |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in (
        "graph_trigger_expected",
        "graph_no_trigger_expected",
        "candidate_graph_path_present",
        "candidate_graph_path_absent",
        "baseline_first",
        "candidate_first",
    ):
        item = groups[label]
        lines.append(
            f"| {label} | {item['sample_count']} | "
            f"{item.get('paired_delta_median_ms')} | "
            f"{item.get('paired_delta_p90_ms')} | "
            f"{item.get('paired_delta_p95_ms')} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "本报告只判断同一时段配对条件下的延迟增量，"
            "不改变此前质量指标，也不授权生产启用。",
            "",
        ]
    )
    return "\n".join(lines)


def _order_balance(
    rows: Sequence[dict[str, Any]],
    *,
    measured_pairs_per_case: int,
) -> dict[str, Any]:
    expected_baseline_first = measured_pairs_per_case // 2
    expected_candidate_first = measured_pairs_per_case // 2
    by_case: dict[str, dict[str, int]] = {}
    for item in rows:
        case_id = str(item.get("eval_case_id") or "")
        order = str(item.get("order") or "")
        counts = by_case.setdefault(
            case_id,
            {"baseline_first": 0, "candidate_first": 0},
        )
        if order in counts:
            counts[order] += 1
    mismatches = {
        case_id: counts
        for case_id, counts in by_case.items()
        if counts["baseline_first"] != expected_baseline_first
        or counts["candidate_first"] != expected_candidate_first
    }
    return {
        "expected_per_case": {
            "baseline_first": expected_baseline_first,
            "candidate_first": expected_candidate_first,
        },
        "by_case": dict(sorted(by_case.items())),
        "mismatch_count": len(mismatches),
        "mismatches": dict(sorted(mismatches.items())),
    }


def _aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "sample_count": 0,
            "baseline_mean_ms": None,
            "baseline_p95_ms": None,
            "candidate_mean_ms": None,
            "candidate_p95_ms": None,
            "paired_delta_mean_ms": None,
            "paired_delta_median_ms": None,
            "paired_delta_p50_ms": None,
            "paired_delta_p75_ms": None,
            "paired_delta_p90_ms": None,
            "paired_delta_p95_ms": None,
            "paired_delta_min_ms": None,
            "paired_delta_max_ms": None,
            "candidate_slower_count": 0,
            "candidate_faster_count": 0,
            "equal_count": 0,
        }
    baseline = [int(item["baseline_latency_ms"]) for item in rows]
    candidate = [
        int(item["candidate_latency_ms"]) for item in rows
    ]
    deltas = [
        int(item["candidate_latency_ms"])
        - int(item["baseline_latency_ms"])
        for item in rows
    ]
    return {
        "sample_count": len(rows),
        "baseline_mean_ms": _mean(baseline),
        "baseline_p95_ms": _percentile(baseline, 0.95),
        "candidate_mean_ms": _mean(candidate),
        "candidate_p95_ms": _percentile(candidate, 0.95),
        "paired_delta_mean_ms": _mean(deltas),
        "paired_delta_median_ms": round(
            float(statistics.median(deltas)),
            3,
        ),
        "paired_delta_p50_ms": _percentile(deltas, 0.50),
        "paired_delta_p75_ms": _percentile(deltas, 0.75),
        "paired_delta_p90_ms": _percentile(deltas, 0.90),
        "paired_delta_p95_ms": _percentile(deltas, 0.95),
        "paired_delta_min_ms": min(deltas),
        "paired_delta_max_ms": max(deltas),
        "candidate_slower_count": sum(item > 0 for item in deltas),
        "candidate_faster_count": sum(item < 0 for item in deltas),
        "equal_count": sum(item == 0 for item in deltas),
    }


def _mean(values: Sequence[int]) -> float:
    return round(statistics.fmean(values), 3)


def _percentile(
    values: Sequence[int],
    quantile: float,
) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            math.ceil(len(ordered) * quantile) - 1,
        ),
    )
    return ordered[index]
