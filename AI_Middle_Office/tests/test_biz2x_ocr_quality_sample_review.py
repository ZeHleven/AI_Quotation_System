from __future__ import annotations

from scripts.biz2x_ocr_quality_sample_review import build_review_sample_regions


def test_quality_review_plan_samples_selected_recoverable_noise_and_overflow() -> None:
    selected = [
        _region("sel_a", priority=0.9, confidence=0.8),
        _region("sel_b", priority=0.7, confidence=0.6, flags=["split_from_too_large_region"]),
    ]
    rejected = [
        _region("rec_a", priority=0.4, confidence=0.5, subtype="split_noise", layer="recoverable_text_like", flags=["split_candidate_too_small"]),
        _region("neg_a", priority=0.3, subtype="line_or_marker_noise", flags=["line_dominant", "score_below_threshold"]),
    ]
    overflow = [
        _region("ovf_a", priority=0.35, confidence=0.4, overflow_reason="page_region_cap"),
    ]

    rows = build_review_sample_regions(
        selected_regions=selected,
        rejected_regions=rejected,
        overflow_regions=overflow,
        positive_samples=2,
        recoverable_rejected_samples=1,
        negative_samples=1,
        overflow_samples=1,
    )

    buckets = [row["review_sample_bucket"] for row in rows]
    assert buckets == [
        "expected_effective_text",
        "expected_effective_text",
        "recoverable_rejected_text_like",
        "expected_noise_or_no_text",
        "overflow_budget_cut",
    ]
    assert [row["region_id"][:3] for row in rows] == ["pos", "pos", "rec", "neg", "ovf"]
    assert all(row["recommended_tools"] == ["ocr"] for row in rows)


def _region(
    region_id: str,
    *,
    priority: float,
    confidence: float = 0.2,
    subtype: str = "text_line",
    layer: str = "",
    flags: list[str] | None = None,
    overflow_reason: str = "",
) -> dict[str, object]:
    row: dict[str, object] = {
        "region_id": region_id,
        "source_file": "drawing.pdf",
        "page": 1,
        "bbox_ratio": [0.1, 0.1, 0.2, 0.12],
        "priority": priority,
        "confidence": confidence,
        "region_subtype": subtype,
        "quality_flags": flags or [],
    }
    if layer:
        row["rejected_layer"] = layer
    if overflow_reason:
        row["overflow_reason"] = overflow_reason
    return row
