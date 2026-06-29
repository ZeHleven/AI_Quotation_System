from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.drawing_agent_ocr import build_pdf_agent_ocr_report
from app.services.drawing_material_region_planner import (
    build_material_evidence_report,
    build_material_ocr_subcrop_report,
    build_material_region_plan_report,
    normalize_material_region_plan,
)


def test_material_region_planner_finds_table_and_inline_callout(tmp_path: Path) -> None:
    page_path = _make_material_page(tmp_path / "page.png")
    report = build_material_region_plan_report(
        render_report=_render_report(page_path),
        planner_dir=tmp_path / "material_regions",
        max_pages=1,
        max_cad_views=0,
        max_regions=12,
    )

    region_types = {row["region_type"] for row in report["regions"]}
    assert report["summary"]["material_region_count"] >= 2
    assert region_types & {"material_table", "legend_table", "finish_schedule"}
    assert region_types & {"material_callout", "finish_code_label"}
    assert Path(report["outputs"]["material_region_plan_json"]).exists()
    assert Path(report["outputs"]["material_region_annotations_json"]).exists()


def test_vlm_material_regions_outrank_local_fallback(tmp_path: Path) -> None:
    page_path = _make_material_page(tmp_path / "page.png")

    def fake_vlm_planner(view_manifest):
        return {
            "regions": [
                {
                    "region_id": "vlm_callout",
                    "view_id": view_manifest[0]["view_id"],
                    "region_type": "material_callout",
                    "bbox_ratio": [0.68, 0.12, 0.92, 0.30],
                    "priority": 0.9,
                    "confidence": 0.8,
                    "recommended_tools": ["ocr_and_vlm"],
                }
            ]
        }

    report = build_material_region_plan_report(
        render_report=_render_report(page_path),
        planner_dir=tmp_path / "material_regions",
        material_region_planner=fake_vlm_planner,
        max_pages=1,
        max_cad_views=0,
        max_regions=1,
    )

    assert report["summary"]["vlm_material_region_count"] == 1
    assert report["regions"][0]["planner_source"] == "vlm_material_region"
    assert report["regions"][0]["region_type"] == "material_callout"


def test_vlm_material_region_plan_converts_cad_view_bbox_to_page_ratio() -> None:
    view_manifest = [
        {
            "view_id": "cad_view_001",
            "source_file": "drawing.pdf",
            "page": 1,
            "tile_type": "material_cad_view",
            "parent_bbox_pixel": [100, 200, 500, 600],
            "parent_bbox_ratio": [0.1, 0.2, 0.5, 0.6],
            "page_image_width_px": 1000,
            "page_image_height_px": 1000,
        }
    ]
    regions = normalize_material_region_plan(
        {
            "regions": [
                {
                    "region_id": "m001",
                    "view_id": "cad_view_001",
                    "region_type": "material_callout",
                    "bbox_ratio": [0.25, 0.25, 0.75, 0.5],
                    "priority": 0.9,
                    "confidence": 0.8,
                    "recommended_tools": ["ocr_and_vlm"],
                }
            ]
        },
        view_manifest=view_manifest,
        warnings=[],
    )

    assert len(regions) == 1
    assert regions[0]["bbox_ratio"] == [0.2, 0.3, 0.4, 0.4]
    assert regions[0]["recommended_tools"] == ["ocr", "vlm_read"]


def test_highres_material_subcrops_feed_material_evidence(tmp_path: Path) -> None:
    highres_image = _make_highres_material_crop(tmp_path / "highres.png")
    material_region_report = {
        "regions": [
            {
                "region_id": "m001",
                "region_type": "material_callout",
                "planner_source": "local_material_visual_scan",
            }
        ]
    }
    highres_report = {
        "crop_manifest": [
            {
                "crop_id": "hr001",
                "region_id": "m001",
                "region_type": "material_callout",
                "source_file": "drawing.pdf",
                "page": 1,
                "image_path": str(highres_image),
                "render_method": "pdfium_clip_crop",
                "source_quality": "rerendered_from_source_pdf",
                "is_upscaled_from_lowres": False,
            }
        ]
    }
    subcrop_report = build_material_ocr_subcrop_report(
        highres_report=highres_report,
        output_dir=tmp_path / "material_ocr_subcrops",
        max_subcrops=4,
    )

    def fake_ocr_runner(image_path: Path):
        return [
            {
                "text": "CT 04 600X1200 white wall tile",
                "confidence": 0.93,
                "bbox": [[1, 1], [200, 1], [200, 30], [1, 30]],
            },
            {
                "text": "MT 01 black stainless steel",
                "confidence": 0.91,
                "bbox": [[1, 40], [200, 40], [200, 70], [1, 70]],
            },
        ]

    ocr_report = build_pdf_agent_ocr_report(
        render_report={"render_rows": []},
        crop_dir=tmp_path / "default_crops",
        ocr_dir=tmp_path / "ocr",
        context_dir=tmp_path / "context",
        ocr_runner=fake_ocr_runner,
        max_page_crops=0,
        extra_crop_manifest=subcrop_report["crop_manifest"],
    )
    evidence_report = build_material_evidence_report(
        material_region_report=material_region_report,
        highres_report=highres_report,
        ocr_report=ocr_report,
        output_dir=tmp_path / "material_evidence",
    )

    codes = {row["code"] for row in evidence_report["material_mentions"]}
    assert "CT-04" in codes
    assert "MT-01" in codes
    assert evidence_report["summary"]["material_evidence_region_count"] == 1
    assert Path(evidence_report["outputs"]["material_evidence_json"]).exists()


def _make_material_page(path: Path) -> Path:
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    table = (70, 520, 730, 850)
    draw.rectangle(table, outline=(255, 0, 0), width=3)
    for x in (170, 340):
        draw.line((x, table[1], x, table[3]), fill=(255, 0, 0), width=2)
    for y in (600, 685, 770):
        draw.line((table[0], y, table[2], y), fill=(255, 0, 0), width=2)
    draw.text((105, 545), "NO", fill=(235, 235, 0))
    draw.text((220, 545), "LEGEND", fill=(235, 235, 0))
    draw.text((430, 545), "MATERIAL NAME", fill=(235, 235, 0))
    draw.text((210, 625), "W1", fill=(235, 235, 0))
    draw.text((430, 625), "white wall tile 400x800", fill=(235, 235, 0))
    draw.text((210, 710), "W2", fill=(235, 235, 0))
    draw.text((430, 710), "white paint + black skirting", fill=(235, 235, 0))

    draw.rectangle((820, 120, 920, 170), outline=(255, 0, 0), width=3)
    draw.text((840, 130), "CT 04", fill=(235, 235, 0))
    draw.text((820, 185), "600X1200 white wall tile", fill=(255, 0, 0))
    draw.line((870, 170, 770, 300), fill=(255, 0, 0), width=3)
    draw.rectangle((960, 120, 1060, 170), outline=(255, 0, 0), width=3)
    draw.text((980, 130), "MT 01", fill=(235, 235, 0))
    draw.text((960, 185), "black stainless steel", fill=(255, 0, 0))
    draw.line((1010, 170, 1050, 300), fill=(255, 0, 0), width=3)
    image.save(path)
    return path


def _make_highres_material_crop(path: Path) -> Path:
    image = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 60, 220, 130), outline=(255, 0, 0), width=4)
    draw.text((105, 82), "CT 04", fill=(240, 240, 0))
    draw.text((80, 150), "600X1200 white wall tile", fill=(255, 0, 0))
    draw.rectangle((480, 60, 620, 130), outline=(255, 0, 0), width=4)
    draw.text((505, 82), "MT 01", fill=(240, 240, 0))
    draw.text((480, 150), "black stainless steel", fill=(255, 0, 0))
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
