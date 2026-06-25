from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services.drawing_ocr_quality_scorer import (
    build_highres_ocr_quality_report,
    build_ocr_quality_feedback_profile,
    build_ocr_quality_reranked_plan,
    score_ocr_crop,
)


def test_ocr_quality_score_prefers_material_dimensions_and_chinese_text() -> None:
    material_score = score_ocr_crop(
        crop={"crop_id": "c1", "region_id": "r1", "priority": 0.6},
        ocr_rows=[
            {"text": "CT-01 600x600 地砖", "confidence": 0.92},
            {"text": "墙面 PT-02 乳胶漆", "confidence": 0.88},
        ],
    )
    noise_score = score_ocr_crop(
        crop={"crop_id": "c2", "region_id": "r2", "priority": 0.9},
        ocr_rows=[
            {"text": "--", "confidence": 0.42},
            {"text": "A", "confidence": 0.51},
        ],
    )

    assert material_score["ocr_quality_score"] > noise_score["ocr_quality_score"]
    assert material_score["ocr_material_code_count"] == 2
    assert material_score["ocr_dimension_count"] >= 1
    assert material_score["ocr_chinese_char_count"] >= 4
    assert noise_score["ocr_quality_label"] == "low"


def test_highres_ocr_quality_report_writes_scores_with_fake_runner(tmp_path: Path) -> None:
    crop_a = _make_image(tmp_path / "material.png")
    crop_b = _make_image(tmp_path / "noise.png")

    def fake_runner(image_path: Path):
        if image_path.name == "material.png":
            return [
                {"text": "WD-01 木饰面 1200x2400", "confidence": 0.94},
                {"text": "天花 PT-01 白色涂料", "confidence": 0.91},
            ]
        return [{"text": "丨", "confidence": 0.39}]

    report = build_highres_ocr_quality_report(
        crop_manifest=[
            {"crop_id": "c1", "region_id": "r1", "image_path": str(crop_a), "priority": 0.4},
            {"crop_id": "c2", "region_id": "r2", "image_path": str(crop_b), "priority": 0.9},
        ],
        output_dir=tmp_path / "quality",
        ocr_runner=fake_runner,
        max_crops=5,
    )

    assert report["status"] == "completed"
    assert report["summary"]["scored_crop_count"] == 2
    scores = {row["region_id"]: row["ocr_quality_score"] for row in report["crop_scores"]}
    assert scores["r1"] > scores["r2"]
    assert Path(report["outputs"]["ocr_quality_scores_json"]).exists()
    assert Path(report["outputs"]["ocr_quality_scores_csv"]).exists()
    assert Path(report["outputs"]["ocr_quality_feedback_profile_json"]).exists()


def test_ocr_quality_reranked_plan_uses_sampled_quality_score(tmp_path: Path) -> None:
    plan = {
        "regions": [
            {"region_id": "r_noise", "source_file": "drawing.pdf", "page": 1, "priority": 0.95},
            {"region_id": "r_material", "source_file": "drawing.pdf", "page": 1, "priority": 0.45},
            {"region_id": "r_unsampled", "source_file": "drawing.pdf", "page": 1, "priority": 0.8},
        ]
    }
    quality_report = {
        "crop_scores": [
            {"region_id": "r_noise", "ocr_quality_score": 0.08, "ocr_quality_label": "low", "ocr_text_line_count": 1},
            {
                "region_id": "r_material",
                "ocr_quality_score": 0.92,
                "ocr_quality_label": "high",
                "ocr_text_line_count": 8,
                "ocr_chinese_char_count": 20,
                "ocr_material_code_count": 2,
                "ocr_dimension_count": 3,
            },
        ]
    }

    report = build_ocr_quality_reranked_plan(
        text_region_plan=plan,
        quality_report=quality_report,
        output_dir=tmp_path / "reranked",
    )

    assert report["regions"][0]["region_id"] == "r_material"
    assert report["regions"][0]["ocr_feedback_rank"] == 1
    assert report["regions"][0]["ocr_quality_sampled"] is True
    positions = {row["region_id"]: row["ocr_feedback_rank"] for row in report["regions"]}
    assert positions["r_material"] < positions["r_noise"]
    assert Path(report["outputs"]["ocr_reranked_text_region_plan_json"]).exists()


def test_ocr_quality_feedback_profile_keeps_good_and_bad_shape_features() -> None:
    profile = build_ocr_quality_feedback_profile(
        crop_scores=[
            {
                "crop_id": "c_good",
                "region_id": "r_good",
                "ocr_quality_score": 0.91,
                "ocr_quality_label": "high",
                "ocr_text_line_count": 6,
                "source_region_features": {
                    "width_px": 220,
                    "height_px": 24,
                    "aspect_ratio": 9.1,
                    "text_density": 0.08,
                    "component_count": 12,
                    "page_zone": "right_notes",
                },
                "source_region_planner_source": "medium_cv_text_region_detector.large_region_splitter",
            },
            {
                "crop_id": "c_bad",
                "region_id": "r_bad",
                "ocr_quality_score": 0.04,
                "ocr_quality_label": "no_text",
                "ocr_text_line_count": 0,
                "source_region_features": {
                    "width_px": 260,
                    "height_px": 9,
                    "aspect_ratio": 28.8,
                    "text_density": 0.01,
                    "component_count": 1,
                    "page_zone": "main_drawing",
                },
            },
        ]
    )

    assert profile["schema_version"] == "drawing_ocr_quality_feedback_profile_v1"
    assert profile["positive_sample_count"] == 1
    assert profile["negative_sample_count"] == 1
    assert "height_px" in profile["positive_feature_profile"]["numeric_ranges"]
    assert profile["positive_feature_profile"]["categorical_values"]["page_zone"][0]["value"] == "right_notes"
    assert profile["negative_feature_profile"]["categorical_values"]["page_zone"][0]["value"] == "main_drawing"


def _make_image(path: Path) -> Path:
    image = Image.new("RGB", (120, 80), "white")
    image.save(path)
    return path
