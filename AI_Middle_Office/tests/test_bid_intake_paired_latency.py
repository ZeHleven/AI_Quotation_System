from __future__ import annotations

from app.agents.bid_intake.paired_latency import (
    paired_order,
    rotated_case_ids,
    summarize_paired_latency,
)


def _sample(
    *,
    case_id: str,
    repetition: int,
    order: str,
    baseline_ms: int,
    candidate_ms: int,
    trigger_expected: bool,
    trigger_actual: bool,
    path_count: int = 0,
) -> dict[str, object]:
    return {
        "eval_case_id": case_id,
        "repetition": repetition,
        "order": order,
        "graph_trigger_expected": trigger_expected,
        "baseline_latency_ms": baseline_ms,
        "candidate_latency_ms": candidate_ms,
        "baseline_error_code": None,
        "candidate_error_code": None,
        "candidate_graph_triggered": trigger_actual,
        "trigger_mismatch": trigger_expected != trigger_actual,
        "candidate_path_count": path_count,
    }


def test_pair_order_is_balanced_for_six_repetitions() -> None:
    for case_index in range(10):
        values = [
            paired_order(
                repetition_index=repetition,
                case_index=case_index,
            )
            for repetition in range(6)
        ]
        assert values.count("baseline_first") == 3
        assert values.count("candidate_first") == 3


def test_case_rotation_changes_round_start_without_loss() -> None:
    values = ["Q1", "Q2", "Q3"]
    assert rotated_case_ids(values, repetition_index=0) == values
    assert rotated_case_ids(values, repetition_index=1) == [
        "Q2",
        "Q3",
        "Q1",
    ]
    assert sorted(
        rotated_case_ids(values, repetition_index=7)
    ) == values


def test_paired_latency_passes_balanced_error_free_samples() -> None:
    samples = []
    for case_index, case_id in enumerate(("Q1", "Q2")):
        for repetition in range(2):
            order = paired_order(
                repetition_index=repetition,
                case_index=case_index,
            )
            samples.append(
                _sample(
                    case_id=case_id,
                    repetition=repetition + 1,
                    order=order,
                    baseline_ms=100,
                    candidate_ms=130 + repetition,
                    trigger_expected=case_id == "Q1",
                    trigger_actual=case_id == "Q1",
                    path_count=1 if case_id == "Q1" else 0,
                )
            )

    result = summarize_paired_latency(
        samples,
        expected_pair_count=4,
        measured_pairs_per_case=2,
        paired_delta_p95_ms_max=500,
    )

    assert result["acceptance"]["passed"] is True
    assert result["overall"]["paired_delta_p95_ms"] == 31
    assert result["order_balance"]["mismatch_count"] == 0
    assert result["groups"]["candidate_graph_path_present"][
        "sample_count"
    ] == 2


def test_paired_latency_rejects_trigger_mismatch_and_p95_overrun() -> None:
    samples = [
        _sample(
            case_id="Q1",
            repetition=1,
            order="baseline_first",
            baseline_ms=100,
            candidate_ms=700,
            trigger_expected=True,
            trigger_actual=False,
        ),
        _sample(
            case_id="Q1",
            repetition=2,
            order="candidate_first",
            baseline_ms=100,
            candidate_ms=650,
            trigger_expected=True,
            trigger_actual=True,
        ),
    ]

    result = summarize_paired_latency(
        samples,
        expected_pair_count=2,
        measured_pairs_per_case=2,
        paired_delta_p95_ms_max=500,
    )

    assert result["acceptance"]["passed"] is False
    assert result["trigger_mismatch_count"] == 1
    assert set(result["acceptance"]["failed_checks"]) == {
        "trigger_mismatch_count",
        "paired_delta_p95_ms",
    }
