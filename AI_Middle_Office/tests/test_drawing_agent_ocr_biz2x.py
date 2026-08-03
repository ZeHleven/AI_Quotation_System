from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.drawing_agent_ocr import build_pdf_agent_ocr_report
from app.services.drawing_pdf_agent_itemizer import merge_agent_evidence


def test_pdf_agent_local_ocr_builds_crops_and_material_candidates(tmp_path):
    page_path = _make_page_image(tmp_path / "page.png")

    def fake_ocr_runner(image_path: Path):
        return [
            {"text": "CT-01 600x600 floor tile", "confidence": 0.93, "bbox": [[1, 1], [120, 1], [120, 24], [1, 24]]},
            {"text": "PT-02 washable paint", "confidence": 0.88, "bbox": [[1, 32], [150, 32], [150, 58], [1, 58]]},
        ]

    report = build_pdf_agent_ocr_report(
        render_report={"render_rows": [{"source_file": "drawing.pdf", "page": 1, "png_path": str(page_path)}]},
        crop_dir=tmp_path / "crops",
        ocr_dir=tmp_path / "ocr",
        context_dir=tmp_path / "context",
        ocr_runner=fake_ocr_runner,
    )

    assert report["ocr_status"] == "completed"
    assert report["summary"]["crop_count"] == 4
    assert report["summary"]["ocr_completed_crop_count"] == 4
    assert report["summary"]["ocr_text_line_count"] == 8
    assert {item["code"] for item in report["material_legend_candidates"]} >= {"CT-01", "PT-02"}
    assert report["summary"]["material_legend_candidate_count"] == 2
    for path in report["outputs"].values():
        assert Path(path).exists()


def test_pdf_agent_local_ocr_unavailable_still_writes_manifest(tmp_path):
    page_path = _make_page_image(tmp_path / "page.png")

    report = build_pdf_agent_ocr_report(
        render_report={"render_rows": [{"source_file": "drawing.pdf", "page": 1, "png_path": str(page_path)}]},
        crop_dir=tmp_path / "crops",
        ocr_dir=tmp_path / "ocr",
        context_dir=tmp_path / "context",
        ocr_engine="unsupported",
    )

    assert report["ocr_status"] == "unavailable"
    assert report["summary"]["crop_count"] == 4
    assert report["summary"]["ocr_text_line_count"] == 0
    assert report["warnings"][0]["code"] == "OCR_UNAVAILABLE"
    assert Path(report["outputs"]["agent_ocr_crop_manifest_json"]).exists()
    assert Path(report["outputs"]["agent_ocr_summary_json"]).exists()


def test_pdf_agent_ocr_context_is_merged_into_global_context():
    ocr_report = {
        "summary": {
            "ocr_status": "completed",
            "crop_count": 2,
            "ocr_text_line_count": 2,
            "material_legend_candidate_count": 1,
        },
        "ocr_rows": [
            {"crop_id": "ocr_p001_right_legend_001", "text": "CT-01 600x600 floor tile", "confidence": 0.92}
        ],
        "material_legend_candidates": [
            {
                "code": "CT-01",
                "name_or_hint": "600x600 floor tile",
                "spec_or_method": "600x600 floor tile",
                "source_crop_ids": ["ocr_p001_right_legend_001"],
                "source_texts": ["CT-01 600x600 floor tile"],
                "confidence": 0.92,
            }
        ],
    }

    merged = merge_agent_evidence(
        [],
        view_manifest=[{"view_id": "p001_whole", "tile_type": "whole_page_preview", "selection_role": "whole_page_context"}],
        ocr_report=ocr_report,
    )

    context = merged["global_context"]
    assert context["ocr_status"] == "completed"
    assert context["ocr_crop_count"] == 2
    assert context["ocr_text_line_count"] == 2
    assert context["ocr_material_legend_candidate_count"] == 1
    assert context["material_legend_candidates"][0]["code"] == "CT-01"
    assert merged["merged_materials"][0]["source_crop_ids"] == ["ocr_p001_right_legend_001"]


def _make_page_image(path: Path) -> Path:
    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((760, 80, 1160, 1500), outline="black", width=3)
    draw.text((790, 120), "Material legend CT-01 PT-02", fill="black")
    draw.rectangle((40, 1240, 1160, 1560), outline="black", width=3)
    draw.text((80, 1280), "Title block", fill="black")
    image.save(path)
    return path
