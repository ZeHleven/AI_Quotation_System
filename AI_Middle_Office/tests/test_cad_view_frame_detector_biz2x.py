from pathlib import Path

from PIL import Image, ImageDraw

from app.services.cad_view_frame_detector import (
    CAD_VIEW_TILE_TYPE,
    build_cad_view_frame_report,
    detect_cad_view_frames,
)
from app.services.drawing_pdf_direct_itemizer import select_images_for_pdf_itemization
from app.services.drawing_pdf_direct_itemizer import dedupe_pdf_item_rows


def test_detect_cad_view_frames_from_cyan_sheet_borders():
    image = Image.new("RGB", (900, 700), "white")
    draw = ImageDraw.Draw(image)
    for box in [(20, 50, 300, 220), (330, 50, 610, 220), (20, 270, 300, 450)]:
        draw.rectangle(box, outline=(0, 255, 255), width=3)
        draw.line((box[0] + 30, box[1] + 60, box[2] - 30, box[3] - 60), fill=(0, 0, 0), width=4)
        draw.text((box[0] + 20, box[1] + 20), "PLAN", fill=(0, 0, 0))

    frames = detect_cad_view_frames(image, max_frames=10)

    assert len(frames) == 3
    expected = [(20, 50, 300, 220), (330, 50, 610, 220), (20, 270, 300, 450)]
    for frame, box in zip(frames, expected, strict=True):
        assert abs(frame.x1 - box[0]) <= 3
        assert abs(frame.y1 - box[1]) <= 3
        assert abs(frame.x2 - box[2]) <= 3
        assert abs(frame.y2 - box[3]) <= 3


def test_build_cad_view_frame_report_writes_view_tiles(tmp_path):
    image = Image.new("RGB", (700, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 60, 330, 260), outline=(0, 255, 255), width=3)
    draw.line((70, 140, 290, 140), fill=(0, 0, 0), width=4)
    page_png = tmp_path / "page.png"
    image.save(page_png)

    report = build_cad_view_frame_report(
        parse_report={
            "page_rows": [
                {
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "width_pt": 700,
                    "height_pt": 500,
                }
            ]
        },
        render_report={
            "render_rows": [
                {
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "png_path": str(page_png),
                }
            ]
        },
        view_dir=tmp_path / "views",
    )

    assert report["summary"]["cad_view_frame_count"] == 1
    row = report["view_rows"][0]
    assert row["tile_type"] == CAD_VIEW_TILE_TYPE
    assert Path(row["image_path"]).exists()
    assert row["tile_id"] == "p001_view001"


def test_pdf_itemization_selection_prefers_cad_view_tiles(tmp_path):
    image_paths = []
    for name in ["whole.png", "grid.png", "view1.png", "view2.png"]:
        path = tmp_path / name
        path.write_bytes(b"fake image bytes")
        image_paths.append(path)

    rows = [
        {"tile_id": "p001_whole", "tile_type": "whole_page_preview", "image_path": str(image_paths[0]), "page": 1},
        {"tile_id": "p001_g03_r01_c01", "tile_type": "grid", "image_path": str(image_paths[1]), "page": 1},
        {"tile_id": "p001_view001", "tile_type": CAD_VIEW_TILE_TYPE, "image_path": str(image_paths[2]), "page": 1},
        {"tile_id": "p001_view002", "tile_type": CAD_VIEW_TILE_TYPE, "image_path": str(image_paths[3]), "page": 1},
    ]

    selected = select_images_for_pdf_itemization(rows, max_images=2)

    assert [row["tile_id"] for row in selected] == ["p001_view001", "p001_view002"]


def test_pdf_item_dedupe_merges_same_ct_item_across_views():
    rows = [
        {
            "识别编号": "PDFITEM-000001",
            "tile_id": "p001_view003",
            "图纸项目名称": "墙面瓷砖湿贴CT-04",
            "空间/部位": "",
            "材料编号": "CT-04",
            "规格/做法": "600*600白色墙面砖，湿贴",
            "证据文本": "立面图CT-04",
            "建议单位": "m²",
            "置信度": 0.8,
            "需人工复核": False,
            "识别原因": "立面图可见",
        },
        {
            "识别编号": "PDFITEM-000002",
            "tile_id": "p001_view011",
            "图纸项目名称": "墙面瓷砖湿贴CT-04",
            "空间/部位": "",
            "材料编号": "CT-04",
            "规格/做法": "材料编号、材料名称、规格、做法、安装方式或构造说明",
            "证据文本": "图纸上可见的原文或可追溯依据",
            "建议单位": "m²",
            "置信度": 0.6,
            "需人工复核": True,
            "识别原因": "节点重复标注",
        },
        {
            "识别编号": "PDFITEM-000003",
            "tile_id": "p001_view016",
            "图纸项目名称": "墙面瓷砖干挂CT-04",
            "空间/部位": "",
            "材料编号": "CT-04",
            "规格/做法": "600*600白色墙面砖，干挂",
            "证据文本": "干挂节点CT-04",
            "建议单位": "m²",
            "置信度": 0.8,
            "需人工复核": False,
            "识别原因": "干挂做法不同",
        },
    ]

    result = dedupe_pdf_item_rows(rows)

    assert len(result) == 2
    wet = result[0]
    assert wet["图纸项目名称"] == "墙面瓷砖湿贴CT-04"
    assert wet["规格/做法"] == "600*600白色墙面砖，湿贴"
    assert wet["证据文本"] == "立面图CT-04"
    assert wet["tile_id"] == "p001_view003,p001_view011"
    assert result[1]["图纸项目名称"] == "墙面瓷砖干挂CT-04"
