from __future__ import annotations

from pathlib import Path

import pytest

from scripts.evaluate_bid_pure_agent_v6052 import (
    OCR_EVIDENCE_ROLE,
    OcrProviderUnavailable,
    SCHEMA_VERSION,
    _render_case,
    assess_ocr_observation,
    detect_table_grid,
    guarded_ocr_result,
    load_dataset,
    select_extraction_route,
)


def test_v6052_dataset_is_versioned_synthetic_and_externally_closed() -> None:
    dataset = load_dataset()

    assert dataset["schema_version"] == SCHEMA_VERSION
    assert dataset["dataset_kind"] == "synthetic_only"
    assert all(value is False for value in dataset["execution_contract"].values())
    assert len(dataset["image_cases"]) == 4
    assert len(dataset["routing_cases"]) == 3
    assert {case["kind"] for case in dataset["image_cases"]} == {
        "text_page",
        "table_page",
        "blank_page",
    }


@pytest.mark.parametrize(
    ("native_text", "ocr_available", "expected"),
    (
        ("已有可用原生文本", True, "native"),
        ("  ", True, "ocr"),
        (None, False, "degraded"),
    ),
)
def test_v6052_native_first_route_is_small_and_deterministic(
    native_text: str | None,
    ocr_available: bool,
    expected: str,
) -> None:
    assert (
        select_extraction_route(
            native_text=native_text,
            ocr_available=ocr_available,
        )
        == expected
    )


def test_v6052_provider_unavailable_discloses_degradation_without_content() -> None:
    result = guarded_ocr_result(
        lambda: (_ for _ in ()).throw(OcrProviderUnavailable("offline"))
    )

    assert result["status"] == "degraded"
    assert result["error_code"] == "ocr_provider_unavailable"
    assert result["texts"] == []
    assert result["scores"] == []
    assert result["boxes"] == []
    assert result["evidence_role"] == OCR_EVIDENCE_ROLE
    assert result["citable"] is False
    assert result["review_required"] is True


def test_v6052_contract_error_is_not_silently_downgraded() -> None:
    with pytest.raises(ValueError, match="invalid OCR contract"):
        guarded_ocr_result(
            lambda: (_ for _ in ()).throw(ValueError("invalid OCR contract"))
        )


def test_v6052_ocr_observation_is_non_citable_and_low_quality_requests_review() -> None:
    result = assess_ocr_observation(
        texts=("可能识别出的文字",),
        scores=(0.99,),
        quality={
            "contrast_std_normalized": 0.01,
            "laplacian_variance": 3.0,
        },
    )

    assert result["review_required"] is True
    assert result["review_reasons"] == [
        "low_image_contrast",
        "low_image_sharpness",
    ]
    assert result["evidence_role"] == OCR_EVIDENCE_ROLE
    assert result["citable"] is False
    assert result["requires_evidence_read"] is True


def test_v6052_blank_observation_is_reviewed_even_with_neutral_quality() -> None:
    result = assess_ocr_observation(
        texts=(),
        scores=(),
        quality={
            "contrast_std_normalized": 0.1,
            "laplacian_variance": 100.0,
        },
    )

    assert result["review_required"] is True
    assert result["review_reasons"] == ["no_text_detected"]


def test_v6052_opencv_grid_detector_recovers_frozen_synthetic_shape(
    tmp_path: Path,
) -> None:
    dataset = load_dataset()
    table_case = next(
        case for case in dataset["image_cases"] if case["kind"] == "table_page"
    )
    image_path = tmp_path / "table.png"
    _render_case(
        table_case,
        image_path,
        font_path=Path("C:/Windows/Fonts/msyh.ttc"),
    )

    grid = detect_table_grid(image_path)

    assert grid["algorithm"] == "opencv_morphological_grid"
    assert grid["rows"] == table_case["expected_grid"]["rows"]
    assert grid["columns"] == table_case["expected_grid"]["columns"]
    assert len(grid["cells"]) == table_case["expected_grid"]["cells"]
    assert {cell["id"] for cell in grid["cells"]} == {
        f"r{row}c{column}" for row in range(3) for column in range(3)
    }


def test_v6052_thresholds_require_quality_safety_and_bounded_latency() -> None:
    thresholds = load_dataset()["thresholds"]

    assert thresholds["clear_fragment_recall_min"] == 1.0
    assert thresholds["table_fragment_recall_min"] >= 0.875
    assert thresholds["clear_mean_confidence_min"] >= 0.9
    assert thresholds["box_integrity_min"] == 1.0
    assert thresholds["table_grid_accuracy_min"] == 1.0
    assert thresholds["table_cell_fragment_recall_min"] >= 0.75
    assert thresholds["blank_false_positive_count_max"] == 0
    assert 0 < thresholds["ocr_case_seconds_max"] <= 30
