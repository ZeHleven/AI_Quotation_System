from __future__ import annotations

from collections import Counter

from app.services.drawing_ocr_budget_scheduler import build_budgeted_ocr_execution_plan


def test_budgeted_ocr_scheduler_reserves_fallback_slots_and_keeps_diversity() -> None:
    selected = [
        _region(f"sel_pos_{index}", priority=1.0 - index * 0.01, budget_bucket="ocr_positive_feedback")
        for index in range(8)
    ]
    selected.extend(
        [
            _region("sel_colored_a", priority=0.42, budget_bucket="colored_annotation", subtype="colored_text_or_callout"),
            _region("sel_colored_b", priority=0.40, budget_bucket="colored_annotation", subtype="colored_text_or_callout"),
        ]
    )
    rejected = [
        _region("rej_rec_a", priority=0.36, rejected_layer="recoverable_text_like"),
        _region("rej_rec_b", priority=0.34, rejected_layer="recoverable_text_like"),
        _region("rej_noise", priority=0.8, rejected_layer="hard_noise", subtype="line_or_marker_noise"),
    ]
    overflow = [
        _region("ovf_colored", priority=0.8, budget_bucket="colored_annotation", overflow_reason="page_region_cap"),
        _region("ovf_positive", priority=0.78, budget_bucket="ocr_positive_feedback", overflow_reason="page_region_cap"),
        _region("ovf_split", priority=0.76, budget_bucket="large_region_split", overflow_reason="large_region_split_rejected_cap"),
    ]

    plan = build_budgeted_ocr_execution_plan(
        selected_regions=selected,
        rejected_regions=rejected,
        overflow_regions=overflow,
        total_budget=10,
        overflow_reserve=2,
        recoverable_rejected_reserve=2,
    )

    rows = plan["regions"]
    buckets = Counter(row["ocr_execution_bucket"] for row in rows)
    source_buckets = Counter(row.get("budget_bucket") for row in rows)
    assert len(rows) == 10
    assert buckets["primary_selected"] == 6
    assert buckets["fallback_overflow_budget_cut"] == 2
    assert buckets["fallback_recoverable_rejected"] == 2
    assert source_buckets["colored_annotation"] >= 2
    assert all(row["recommended_tools"] == ["ocr"] for row in rows)
    assert all(row.get("ocr_execution_bucket_cn") for row in rows)
    assert all(row.get("ocr_execution_budget_decision_cn") for row in rows)
    assert all(row.get("candidate_decision_cn") for row in rows)
    assert all(row.get("candidate_reason_cn") for row in rows)
    assert all(row.get("candidate_signal_cn") for row in rows)
    assert all(row.get("candidate_risk_cn") for row in rows)
    assert all(row.get("next_action_cn") for row in rows)


def test_budgeted_ocr_scheduler_fills_unused_fallback_budget_with_primary() -> None:
    selected = [_region(f"sel_{index}", priority=0.9 - index * 0.01, budget_bucket="main_drawing") for index in range(6)]

    plan = build_budgeted_ocr_execution_plan(
        selected_regions=selected,
        rejected_regions=[],
        overflow_regions=[],
        total_budget=6,
        overflow_reserve=2,
        recoverable_rejected_reserve=2,
    )

    rows = plan["regions"]
    assert len(rows) == 6
    assert {row["ocr_execution_bucket"] for row in rows} == {"primary_selected"}
    assert plan["summary"]["fallback_region_count"] == 0


def _region(
    region_id: str,
    *,
    priority: float,
    budget_bucket: str = "main_drawing",
    subtype: str = "text_block",
    rejected_layer: str = "",
    overflow_reason: str = "",
) -> dict[str, object]:
    row: dict[str, object] = {
        "region_id": region_id,
        "source_file": "drawing.pdf",
        "page": 1,
        "bbox_ratio": [0.1, 0.1, 0.2, 0.14],
        "priority": priority,
        "confidence": priority,
        "budget_bucket": budget_bucket,
        "budget_bucket_cn": budget_bucket,
        "region_subtype": subtype,
        "features": {"page_zone": "main_drawing"},
        "quality_flags": [],
    }
    if rejected_layer:
        row["rejected_layer"] = rejected_layer
    if overflow_reason:
        row["overflow_reason"] = overflow_reason
    return row
