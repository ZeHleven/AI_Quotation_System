from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.services.drawing_highres_region_renderer import build_highres_region_render_report


def test_highres_region_renderer_skips_empty_region_plan(tmp_path: Path) -> None:
    report = build_highres_region_render_report(
        parse_report={"file_rows": [], "page_rows": []},
        layout_plan_report={"regions": []},
        output_dir=tmp_path / "highres",
    )

    assert report["status"] == "skipped"
    assert report["summary"]["requested_region_count"] == 0
    assert report["summary"]["highres_crop_count"] == 0
    assert Path(report["outputs"]["highres_region_summary_json"]).exists()
    assert Path(report["outputs"]["highres_region_manifest_json"]).exists()


def test_highres_region_renderer_uses_pdf_clip_not_lowres_upscale(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")

    pdf_path = tmp_path / "sample.pdf"
    _write_minimal_pdf(pdf_path)

    parse_report = {
        "file_rows": [{"file_name": "sample.pdf", "path": str(pdf_path), "page_count": 1}],
        "page_rows": [{"source_file": "sample.pdf", "page": 1, "width_pt": 200, "height_pt": 100}],
    }
    layout_plan_report = {
        "regions": [
            {
                "region_id": "p001_view001_right_material_grid",
                "source_file": "sample.pdf",
                "page": 1,
                "region_type": "material_table",
                "bbox_ratio": [0.70, 0.35, 0.98, 0.70],
                "priority": 0.95,
                "confidence": 0.9,
                "crop_strategy": {"padding_ratio": 0.0},
                "recommended_tools": ["ocr"],
                "expected_information": ["material_codes"],
            }
        ]
    }

    report = build_highres_region_render_report(
        parse_report=parse_report,
        layout_plan_report=layout_plan_report,
        output_dir=tmp_path / "highres",
        default_scale=8,
        max_scale=16,
        max_pixels=10_000_000,
        min_width_px=300,
        min_height_px=120,
    )

    assert report["status"] == "completed"
    assert report["summary"]["highres_crop_count"] == 1
    crop = report["crop_manifest"][0]
    assert crop["render_method"] == "pdfium_clip_crop"
    assert crop["is_upscaled_from_lowres"] is False
    assert crop["source_quality"] == "rerendered_from_source_pdf"
    assert crop["source_pdf_path"].endswith("sample.pdf")

    image = Image.open(crop["image_path"])
    assert image.width >= 300
    assert image.height >= 120


def test_highres_region_renderer_reports_missing_source_pdf(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    parse_report = {
        "file_rows": [{"file_name": "missing.pdf", "path": str(tmp_path / "missing.pdf"), "page_count": 1}],
        "page_rows": [{"source_file": "missing.pdf", "page": 1, "width_pt": 200, "height_pt": 100}],
    }
    layout_plan_report = {
        "regions": [
            {
                "region_id": "r001",
                "source_file": "missing.pdf",
                "page": 1,
                "region_type": "material_table",
                "bbox_ratio": [0.1, 0.1, 0.2, 0.2],
                "priority": 1,
                "confidence": 1,
            }
        ]
    }

    report = build_highres_region_render_report(
        parse_report=parse_report,
        layout_plan_report=layout_plan_report,
        output_dir=tmp_path / "highres",
    )

    assert report["status"] == "skipped"
    assert report["summary"]["valid_region_count"] == 0
    assert any(item["code"] == "HIGHRES_REGION_SOURCE_PDF_MISSING" for item in report["warnings"])


def test_highres_region_renderer_raises_scale_for_short_text_regions(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")

    pdf_path = tmp_path / "sample.pdf"
    _write_minimal_pdf(pdf_path)

    parse_report = {
        "file_rows": [{"file_name": "sample.pdf", "path": str(pdf_path), "page_count": 1}],
        "page_rows": [{"source_file": "sample.pdf", "page": 1, "width_pt": 200, "height_pt": 100}],
    }
    layout_plan_report = {
        "regions": [
            {
                "region_id": "short_text_line",
                "source_file": "sample.pdf",
                "page": 1,
                "region_type": "text_region_candidate",
                "bbox_ratio": [0.10, 0.45, 0.90, 0.47],
                "priority": 0.9,
                "confidence": 0.85,
                "crop_strategy": {"padding_ratio": 0.0},
            }
        ]
    }

    report = build_highres_region_render_report(
        parse_report=parse_report,
        layout_plan_report=layout_plan_report,
        output_dir=tmp_path / "highres",
        default_scale=1,
        max_scale=50,
        max_pixels=10_000_000,
        min_width_px=100,
        min_height_px=80,
    )

    assert report["status"] == "completed"
    crop = report["crop_manifest"][0]
    assert crop["requested_scale"] >= 40
    assert crop["render_scale"] >= 40

    image = Image.open(crop["image_path"])
    assert image.width >= 100
    assert image.height >= 80


def test_highres_region_renderer_preserves_source_region_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")

    pdf_path = tmp_path / "sample.pdf"
    _write_minimal_pdf(pdf_path)

    parse_report = {
        "file_rows": [{"file_name": "sample.pdf", "path": str(pdf_path), "page_count": 1}],
        "page_rows": [{"source_file": "sample.pdf", "page": 1, "width_pt": 200, "height_pt": 100}],
    }
    layout_plan_report = {
        "regions": [
            {
                "region_id": "tr_001",
                "source_file": "sample.pdf",
                "page": 1,
                "region_type": "text_region_candidate",
                "region_subtype": "colored_text_or_callout",
                "bbox_ratio": [0.1, 0.2, 0.5, 0.4],
                "bbox_pixel": [10, 20, 50, 40],
                "priority": 0.8,
                "confidence": 0.7,
                "features": {"text_density": 0.08, "page_zone": "right_notes"},
                "quality_flags": ["candidate_from_medium_cv"],
                "planner_source": "medium_cv_text_region_detector",
                "selected": True,
            }
        ]
    }

    report = build_highres_region_render_report(
        parse_report=parse_report,
        layout_plan_report=layout_plan_report,
        output_dir=tmp_path / "highres",
        default_scale=8,
        max_scale=16,
        max_pixels=10_000_000,
        min_width_px=120,
        min_height_px=80,
    )

    crop = report["crop_manifest"][0]
    assert crop["source_region_features"]["text_density"] == 0.08
    assert crop["source_region_features"]["page_zone"] == "right_notes"
    assert crop["source_region_quality_flags"] == ["candidate_from_medium_cv"]
    assert crop["source_region_planner_source"] == "medium_cv_text_region_detector"
    assert crop["source_region_selected"] is True
    assert crop["source_region_bbox_pixel"] == [10, 20, 50, 40]


def _write_minimal_pdf(path: Path) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 50 >>\nstream\nBT /F1 4 Tf 150 50 Td (CT-01 750x1500) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(content))
