from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.drawing_agent_ocr import build_pdf_agent_ocr_report
from app.services.drawing_cad_view_detail_planner import build_cad_view_detail_plan_report
from app.services.drawing_region_cropper import build_region_crop_report


def test_cad_view_detail_planner_builds_right_and_bottom_bar_regions(tmp_path):
    page_path, view_path = _make_page_with_cad_view(tmp_path)
    render_report = _render_report(page_path)
    cad_view_report = _cad_view_report(view_path)

    detail_report = build_cad_view_detail_plan_report(
        render_report=render_report,
        cad_view_report=cad_view_report,
        planner_dir=tmp_path / "detail_plan",
        max_views=1,
    )
    crop_report = build_region_crop_report(
        render_report=render_report,
        layout_plan_report=detail_report,
        crop_dir=tmp_path / "detail_crops",
        max_regions=8,
        min_area_ratio=0.00002,
        iou_threshold=0.92,
    )

    assert detail_report["summary"]["selected_cad_view_count"] == 1
    assert detail_report["summary"]["cad_view_detail_region_count"] == 3
    assert detail_report["summary"]["right_bar_region_count"] == 2
    assert detail_report["summary"]["bottom_note_region_count"] == 1
    assert {row["region_subtype"] for row in detail_report["regions"]} == {
        "right_title_bar",
        "right_material_grid",
        "bottom_note_bar",
    }
    assert crop_report["summary"]["region_crop_count"] == 3
    for row in crop_report["crop_manifest"]:
        assert Path(row["image_path"]).exists()


def test_cad_view_detail_crops_feed_ocr_material_candidates(tmp_path):
    page_path, view_path = _make_page_with_cad_view(tmp_path)
    render_report = _render_report(page_path)
    cad_view_report = _cad_view_report(view_path)
    detail_report = build_cad_view_detail_plan_report(
        render_report=render_report,
        cad_view_report=cad_view_report,
        planner_dir=tmp_path / "detail_plan",
        max_views=1,
    )
    crop_report = build_region_crop_report(
        render_report=render_report,
        layout_plan_report=detail_report,
        crop_dir=tmp_path / "detail_crops",
        max_regions=8,
        min_area_ratio=0.00002,
        iou_threshold=0.92,
    )

    def fake_ocr_runner(image_path: Path):
        if "right_material_grid" in image_path.name:
            return [
                {"text": "CT-01 750x1500 floor tile", "confidence": 0.93, "bbox": [[1, 1], [120, 1], [120, 20], [1, 20]]}
            ]
        return []

    ocr_report = build_pdf_agent_ocr_report(
        render_report=render_report,
        crop_dir=tmp_path / "default_crops",
        ocr_dir=tmp_path / "ocr",
        context_dir=tmp_path / "context",
        ocr_runner=fake_ocr_runner,
        max_page_crops=0,
        extra_crop_manifest=crop_report["crop_manifest"],
    )

    assert ocr_report["summary"]["region_crop_count"] == 3
    assert ocr_report["summary"]["region_material_legend_candidate_count"] == 1
    candidate = ocr_report["region_material_legend_candidates"][0]
    assert candidate["code"] == "CT-01"
    assert candidate["source_region_ids"]
    assert "material_table" in candidate["source_region_types"]


def _make_page_with_cad_view(tmp_path: Path) -> tuple[Path, Path]:
    page = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(page)
    view_box = (100, 100, 620, 430)
    draw.rectangle(view_box, outline=(0, 255, 255), width=3)
    draw.line((140, 230, 460, 230), fill=(0, 0, 0), width=4)
    draw.rectangle((540, 110, 610, 420), outline=(0, 210, 0), width=3)
    draw.text((550, 190), "CT-01", fill=(0, 180, 0))
    draw.rectangle((110, 390, 610, 420), outline=(230, 220, 0), width=2)
    draw.text((430, 398), "A-01", fill=(220, 190, 0))
    page_path = tmp_path / "page.png"
    page.save(page_path)
    view = page.crop(view_box)
    view_path = tmp_path / "view.png"
    view.save(view_path)
    return page_path, view_path


def _render_report(page_path: Path) -> dict:
    return {
        "render_rows": [
            {
                "source_file": "drawing.pdf",
                "page": 1,
                "png_path": str(page_path),
                "status": "rendered",
                "image_width_px": 1000,
                "image_height_px": 700,
            }
        ]
    }


def _cad_view_report(view_path: Path) -> dict:
    return {
        "view_rows": [
            {
                "tile_id": "p001_view001",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "cad_view",
                "bbox_pixel": [100, 100, 620, 430],
                "image_path": str(view_path),
                "priority": 250,
                "view_frame_ink_ratio": 0.05,
                "view_frame_border_coverage": 0.9,
            }
        ]
    }
