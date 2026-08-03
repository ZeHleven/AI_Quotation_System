from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.drawing_agent_ocr import build_pdf_agent_ocr_report
from app.services.drawing_layout_planner import build_pdf_layout_plan_report
from app.services.drawing_region_cropper import build_region_crop_report


def test_layout_planner_normalizes_regions_and_region_cropper_dedupes(tmp_path):
    page_path = _make_page_image(tmp_path / "page.png")
    render_report = _render_report(page_path)

    def fake_layout_planner(page_manifest):
        assert page_manifest[0]["view_id"] == "layout_p001_001"
        assert Path(page_manifest[0]["image_path"]).exists()
        return {
            "schema_version": "drawing_layout_plan_v1",
            "page_type": "floor_plan",
            "regions": [
                {
                    "region_id": "r001",
                    "view_id": page_manifest[0]["view_id"],
                    "region_type": "material_table",
                    "bbox_ratio": [0.68, 0.10, 0.96, 0.36],
                    "priority": 0.95,
                    "confidence": 0.9,
                    "recommended_tools": ["ocr_and_vlm"],
                    "expected_information": ["material_codes", "specifications"],
                    "crop_strategy": {"scale": 2.0, "padding_ratio": 0.02},
                },
                {
                    "region_id": "duplicate_lower_priority",
                    "view_id": page_manifest[0]["view_id"],
                    "region_type": "legend",
                    "bbox_ratio": [0.69, 0.11, 0.95, 0.35],
                    "priority": 0.6,
                    "confidence": 0.7,
                    "recommended_tools": ["ocr"],
                },
                {
                    "region_id": "r002",
                    "view_id": page_manifest[0]["view_id"],
                    "region_type": "title_block",
                    "bbox_ratio": [0.60, 0.74, 0.97, 0.96],
                    "priority": 0.88,
                    "confidence": 0.82,
                    "recommended_tools": ["ocr"],
                },
            ],
        }

    layout_report = build_pdf_layout_plan_report(
        render_report=render_report,
        planner_dir=tmp_path / "layout",
        layout_planner=fake_layout_planner,
        max_pages=1,
    )
    crop_report = build_region_crop_report(
        render_report=render_report,
        layout_plan_report=layout_report,
        crop_dir=tmp_path / "regions",
        max_regions=8,
    )

    assert layout_report["status"] == "completed"
    assert layout_report["summary"]["layout_plan_region_count"] == 3
    assert layout_report["regions"][0]["recommended_tools"] == ["ocr", "vlm_read"]
    assert crop_report["summary"]["valid_region_count"] == 3
    assert crop_report["summary"]["deduped_region_count"] == 2
    assert crop_report["summary"]["region_crop_count"] == 2
    assert {row["region_id"] for row in crop_report["crop_manifest"]} == {"r001", "r002"}
    for row in crop_report["crop_manifest"]:
        assert Path(row["image_path"]).exists()


def test_region_crops_feed_ocr_and_material_candidates(tmp_path):
    page_path = _make_page_image(tmp_path / "page.png")
    render_report = _render_report(page_path)
    layout_report = {
        "status": "completed",
        "regions": [
            {
                "region_id": "r001",
                "source_file": "drawing.pdf",
                "page": 1,
                "region_type": "material_table",
                "bbox_ratio": [0.68, 0.10, 0.96, 0.36],
                "priority": 0.95,
                "confidence": 0.9,
                "recommended_tools": ["ocr"],
                "expected_information": ["material_codes"],
                "crop_strategy": {"scale": 2.0, "padding_ratio": 0.02},
            }
        ],
    }
    crop_report = build_region_crop_report(
        render_report=render_report,
        layout_plan_report=layout_report,
        crop_dir=tmp_path / "regions",
        max_regions=8,
    )

    def fake_ocr_runner(image_path: Path):
        if "material_table" in image_path.name:
            return [
                {"text": "CT-01 600x600 floor tile", "confidence": 0.94, "bbox": [[1, 1], [120, 1], [120, 20], [1, 20]]}
            ]
        return []

    ocr_report = build_pdf_agent_ocr_report(
        render_report=render_report,
        crop_dir=tmp_path / "default_crops",
        ocr_dir=tmp_path / "ocr",
        context_dir=tmp_path / "context",
        ocr_runner=fake_ocr_runner,
        extra_crop_manifest=crop_report["crop_manifest"],
    )

    assert ocr_report["summary"]["region_crop_count"] == 1
    assert ocr_report["summary"]["region_ocr_text_line_count"] == 1
    assert ocr_report["summary"]["region_material_legend_candidate_count"] == 1
    material = ocr_report["region_material_legend_candidates"][0]
    assert material["code"] == "CT-01"
    assert material["source_region_ids"] == ["r001"]
    assert Path(ocr_report["outputs"]["agent_region_ocr_rows_json"]).exists()
    assert Path(ocr_report["outputs"]["agent_region_material_legend_candidates_json"]).exists()


def _make_page_image(path: Path) -> Path:
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((820, 120, 1140, 320), outline="black", width=3)
    draw.text((850, 150), "Material Table CT-01 600x600", fill="black")
    draw.rectangle((720, 690, 1160, 850), outline="black", width=3)
    draw.text((760, 730), "Title Block", fill="black")
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
