from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.services import drawing_text_region_detector as detector
from app.services.drawing_text_region_detector import build_text_region_discovery_report


pytest.importorskip("cv2")


def test_text_region_detector_selects_text_and_writes_report(tmp_path: Path) -> None:
    page_path = _make_medium_page(tmp_path / "page.png")
    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions=20,
        min_score=0.34,
    )

    assert report["status"] in {"completed", "completed_with_warnings"}
    assert report["summary"]["selected_region_count"] >= 2
    assert Path(report["outputs"]["text_region_plan_json"]).exists()
    assert Path(report["outputs"]["text_region_candidates_csv"]).exists()
    assert Path(report["outputs"]["text_region_annotations_json"]).exists()
    assert all(row["region_type"] == "text_region_candidate" for row in report["regions"])
    assert all(row["recommended_tools"] == ["ocr"] for row in report["regions"])
    assert {row["crop_strategy"]["highres_scale"] for row in report["regions"]} <= {32.0, 48.0, 64.0}


def test_text_region_detector_keeps_colored_callout_and_rejects_line_noise(tmp_path: Path) -> None:
    page_path = _make_medium_page(tmp_path / "page.png")
    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions=20,
        min_score=0.34,
    )

    selected_subtypes = {row["region_subtype"] for row in report["regions"]}
    assert selected_subtypes & {"colored_text_or_callout", "right_side_notes_text", "text_line", "text_block"}
    assert any(row["features"]["page_zone"] == "right_notes" for row in report["regions"])
    rejected_flags = {flag for row in report["rejected_regions"] for flag in row.get("quality_flags", [])}
    assert rejected_flags & {"line_dominant", "too_sparse_after_line_removal", "aspect_ratio_unlikely_text", "score_below_threshold"}


def test_text_region_detector_adds_colored_annotation_cluster_candidates(tmp_path: Path) -> None:
    page_path = _make_colored_annotation_page(tmp_path / "page.png")
    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions=20,
        min_score=0.34,
    )

    colored_cluster_rows = [
        row
        for row in report["regions"]
        if (row.get("features") or {}).get("candidate_source") == "colored_annotation_cluster"
    ]
    assert colored_cluster_rows
    assert all(row["region_subtype"] == "colored_text_or_callout" for row in colored_cluster_rows)
    candidates_csv = Path(report["outputs"]["text_region_candidates_csv"]).read_text(encoding="utf-8-sig")
    assert "candidate_source" in candidates_csv
    assert "colored_annotation_cluster" in candidates_csv


def test_text_region_detector_rejects_single_component_colored_marker(tmp_path: Path) -> None:
    page_path = _make_medium_page(tmp_path / "page.png")
    image = Image.open(page_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.line((940, 500, 980, 512), fill=(255, 0, 180), width=1)
    image.save(page_path)

    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions=30,
        min_score=0.34,
    )

    rejected_noise = [
        row
        for row in report["rejected_regions"]
        if "colored_region_without_text_fragments" in row.get("quality_flags", [])
        or "single_component_stroke" in row.get("quality_flags", [])
    ]
    assert rejected_noise
    assert all(row["region_subtype"] == "line_or_marker_noise" for row in rejected_noise)


def test_text_region_detector_splits_too_large_cad_region_for_small_text(tmp_path: Path) -> None:
    page_path = _make_large_cad_text_page(tmp_path / "large_cad.png")
    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions=40,
        min_score=0.32,
        max_area_ratio=0.012,
    )

    split_regions = [
        row
        for row in report["regions"]
        if row.get("planner_source") == "medium_cv_text_region_detector.large_region_splitter"
    ]
    assert split_regions
    assert any("split_from_too_large_region" in row.get("quality_flags", []) for row in split_regions)
    assert report["summary"]["large_region_split_selected_count"] == len(split_regions)
    assert any("too_large_for_text_region" in row.get("quality_flags", []) for row in report["rejected_regions"])


def test_text_region_detector_regions_are_highres_renderer_compatible(tmp_path: Path) -> None:
    page_path = _make_medium_page(tmp_path / "page.png")
    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions=8,
        min_score=0.34,
    )

    region = report["regions"][0]
    assert region["source_file"] == "drawing.pdf"
    assert region["page"] == 1
    assert len(region["bbox_ratio"]) == 4
    assert 0 <= region["bbox_ratio"][0] < region["bbox_ratio"][2] <= 1
    assert 0 <= region["bbox_ratio"][1] < region["bbox_ratio"][3] <= 1
    assert "highres_scale" in region["crop_strategy"]
    assert "padding_ratio" in region["crop_strategy"]


def test_text_region_detector_applies_ocr_quality_feedback_to_candidate_priority(tmp_path: Path) -> None:
    page_path = _make_medium_page(tmp_path / "page.png")
    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions=20,
        min_score=0.34,
        ocr_quality_feedback_profile=_feedback_profile_by_page_zone(),
    )

    all_rows = [*report["regions"], *report["rejected_regions"]]
    assert report["summary"]["ocr_feedback_enabled"] is True
    assert report["summary"]["ocr_feedback_positive_match_count"] >= 1
    assert any(
        row.get("ocr_feedback_positive_shape_match") and row.get("ocr_feedback_score_delta", 0) > 0
        for row in report["regions"]
    )
    assert any(
        row.get("ocr_feedback_negative_shape_match") and row.get("ocr_feedback_score_delta", 0) < 0
        for row in all_rows
    )
    assert "ocr_feedback_score_delta" in Path(report["outputs"]["text_region_candidates_csv"]).read_text(encoding="utf-8-sig")


def test_text_region_detector_writes_overflow_and_rejected_layers(tmp_path: Path) -> None:
    page_path = _make_medium_page(tmp_path / "page.png")
    report = build_text_region_discovery_report(
        render_report=_render_report(page_path),
        output_dir=tmp_path / "text_regions",
        max_pages=1,
        max_regions_per_page=1,
        max_regions=1,
        min_score=0.34,
    )

    assert Path(report["outputs"]["text_region_overflow_json"]).exists()
    assert Path(report["outputs"]["text_region_overflow_csv"]).exists()
    assert report["summary"]["overflow_region_count"] >= 1
    assert report["summary"]["page_region_hit_cap"] is True
    assert report["overflow_regions"]
    assert all(row.get("overflow") is True for row in report["overflow_regions"])
    assert any(row.get("rejected_layer") for row in report["rejected_regions"])
    assert any(row.get("rejected_layer_cn") for row in report["rejected_regions"])
    for bucket in (report["regions"], report["overflow_regions"], report["rejected_regions"]):
        assert all(row.get("candidate_decision_cn") for row in bucket)
        assert all(row.get("candidate_reason_cn") for row in bucket)
        assert all(row.get("candidate_signal_cn") for row in bucket)
        assert all(row.get("candidate_risk_cn") for row in bucket)
        assert all(row.get("next_action_cn") for row in bucket)
    overflow_csv = Path(report["outputs"]["text_region_overflow_csv"]).read_text(encoding="utf-8-sig")
    candidates_csv = Path(report["outputs"]["text_region_candidates_csv"]).read_text(encoding="utf-8-sig")
    rejected_csv = Path(report["outputs"]["text_region_rejected_csv"]).read_text(encoding="utf-8-sig")
    assert "candidate_decision_cn" in candidates_csv
    assert "candidate_reason_cn" in rejected_csv
    assert "candidate_signal_cn" in overflow_csv
    assert "overflow_reason_cn" in overflow_csv
    assert "rejected_layer_cn" in overflow_csv


def test_budget_diversity_keeps_lower_score_material_legend_bucket() -> None:
    high_priority_fallback = [
        _budget_candidate(f"fallback_{index}", priority=0.95 - index * 0.01, page_zone="bottom_title")
        for index in range(6)
    ]
    colored_material_candidate = _budget_candidate(
        "material_legend",
        priority=0.42,
        page_zone="main_drawing",
        candidate_source="colored_annotation_cluster",
        region_subtype="colored_text_or_callout",
    )
    main_drawing_candidate = _budget_candidate("main_text", priority=0.5, page_zone="main_drawing")

    selected, overflow = detector._select_regions_with_budget_diversity(
        [*high_priority_fallback, colored_material_candidate, main_drawing_candidate],
        limit=3,
        scope="global",
    )

    selected_buckets = {row["budget_bucket"] for row in selected}
    assert "colored_annotation" in selected_buckets
    assert "main_drawing" in selected_buckets
    assert len(selected) == 3
    assert overflow
    assert all(row.get("budget_decision_cn") for row in selected)
    assert any(row.get("budget_bucket_cn") == "综合高优先级兜底" for row in overflow)


def test_overflow_rows_keep_budget_bucket_for_split_candidates() -> None:
    split_candidate = _budget_candidate("split_001", priority=0.39, page_zone="main_drawing")
    split_candidate["planner_source"] = "medium_cv_text_region_detector.large_region_splitter"
    split_candidate["quality_flags"] = ["split_from_too_large_region"]

    rows = detector._overflow_rows(
        [split_candidate],
        overflow_reason="large_region_split_rejected_cap",
        overflow_reason_cn="大块区域二次拆分命中过多，超出复核预算。",
        overflow_source_bucket="large_region_split",
    )

    assert rows[0]["overflow"] is True
    assert rows[0]["selected"] is False
    assert rows[0]["budget_bucket"] == "large_region_split"
    assert rows[0]["budget_bucket_cn"] == "大块 CAD 二次拆分小字"
    assert rows[0]["budget_selected"] is False
    assert "预算不足" in rows[0]["budget_decision_cn"]


def _make_medium_page(path: Path) -> Path:
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)

    # Large line/grid noise that should not dominate selected regions.
    for x in range(80, 720, 80):
        draw.line((x, 80, x, 760), fill=(20, 20, 20), width=1)
    for y in range(80, 760, 80):
        draw.line((80, y, 720, y), fill=(20, 20, 20), width=1)
    draw.rectangle((70, 70, 730, 770), outline=(0, 0, 0), width=2)

    # Ordinary text blocks.
    draw.text((120, 140), "ROOM 101 FLOOR FINISH", fill=(0, 0, 0))
    draw.text((120, 175), "CT-01 600X1200 GRAY TILE", fill=(0, 0, 0))
    draw.text((120, 215), "SKIRTING LINE 80 HIGH", fill=(0, 0, 0))

    # Right-side notes and colored callouts get a small priority lift.
    draw.rectangle((820, 110, 1100, 360), outline=(255, 0, 0), width=2)
    draw.text((845, 145), "MATERIAL LEGEND", fill=(255, 0, 0))
    draw.text((845, 185), "CT 02 750X1500 TILE", fill=(255, 0, 0))
    draw.text((845, 225), "PT 01 WHITE PAINT", fill=(255, 0, 0))
    draw.line((880, 270, 760, 420), fill=(255, 0, 0), width=2)

    # Dense hatch-like fill should be rejected or score low.
    for offset in range(0, 180, 8):
        draw.line((820 + offset, 560, 900 + offset, 720), fill=(0, 0, 0), width=1)

    image.save(path)
    return path


def _budget_candidate(
    region_id: str,
    *,
    priority: float,
    page_zone: str,
    candidate_source: str = "",
    region_subtype: str = "text_block",
) -> dict[str, object]:
    return {
        "region_id": region_id,
        "source_file": "drawing.pdf",
        "page": 1,
        "priority": priority,
        "confidence": priority,
        "selected": True,
        "region_subtype": region_subtype,
        "planner_source": "medium_cv_text_region_detector",
        "bbox_ratio": [0.1, 0.1, 0.2, 0.12],
        "features": {
            "page_zone": page_zone,
            "candidate_source": candidate_source,
        },
        "quality_flags": [],
    }


def _make_colored_annotation_page(path: Path) -> Path:
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 80, 700, 760), outline=(0, 0, 0), width=2)
    draw.text((140, 160), "MAIN PLAN", fill=(0, 0, 0))
    draw.text((140, 210), "ROOM 101", fill=(0, 0, 0))

    # Dense CAD title/legend block that should trigger colored annotation discovery.
    draw.rectangle((835, 115, 980, 215), outline=(0, 210, 0), width=2)
    for index, text in enumerate(
        [
            "CT-01 WHITE TILE",
            "PT-01 WHITE PAINT",
            "SCALE 1:75",
        ]
    ):
        y = 135 + index * 24
        draw.text((855, y), text, fill=(0, 180, 0))
        draw.line((850, y + 16, 965, y + 16), fill=(230, 180, 0), width=2)
    draw.line((835, 205, 980, 205), fill=(255, 0, 0), width=2)

    image.save(path)
    return path


def _make_large_cad_text_page(path: Path) -> Path:
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)

    # Dense CAD-like short strokes make the first pass see one large region.
    for y in range(120, 440, 16):
        for x in range(90, 760, 24):
            draw.line((x, y, x + 12, y + 4), fill=(30, 30, 30), width=1)
    for x in range(90, 780, 70):
        draw.rectangle((x, 110, x + 46, 430), outline=(30, 30, 30), width=1)
    for y in range(115, 430, 48):
        draw.rectangle((85, y, 790, y + 28), outline=(30, 30, 30), width=1)

    draw.text((130, 160), "ROOM 201 WALL FINISH", fill=(0, 0, 0))
    draw.text((130, 190), "PT-01 WHITE PAINT", fill=(0, 0, 0))
    draw.text((130, 220), "CT-02 GRAY TILE", fill=(0, 0, 0))

    image.save(path)
    return path


def _render_report(page_path: Path) -> dict:
    return {
        "render_rows": [
            {
                "source_file": "drawing.pdf",
                "page": 1,
                "png_path": str(page_path),
                "status": "rendered",
                "image_width_px": 1200,
                "image_height_px": 900,
            }
        ]
    }


def _feedback_profile_by_page_zone() -> dict:
    return {
        "schema_version": "drawing_ocr_quality_feedback_profile_v1",
        "positive_sample_count": 1,
        "negative_sample_count": 1,
        "positive_feature_profile": {
            "sample_count": 1,
            "numeric_ranges": {},
            "categorical_values": {
                "page_zone": [{"value": "right_notes", "count": 1, "ratio": 1.0}],
            },
        },
        "negative_feature_profile": {
            "sample_count": 1,
            "numeric_ranges": {},
            "categorical_values": {
                "page_zone": [{"value": "main_drawing", "count": 1, "ratio": 1.0}],
            },
        },
        "settings": {
            "positive_score_weight": 0.10,
            "negative_score_weight": 0.14,
            "shape_match_threshold": 0.55,
            "max_positive_delta": 0.12,
            "max_negative_delta": -0.18,
        },
    }
