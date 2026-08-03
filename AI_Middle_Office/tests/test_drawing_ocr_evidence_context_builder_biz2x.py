from __future__ import annotations

import json
from pathlib import Path

from app.services.drawing_ocr_evidence_context_builder import build_ocr_context_packages


def test_context_builder_keeps_all_evidence_and_attaches_nearby_texts(tmp_path: Path) -> None:
    raw_evidences = [
        _evidence(
            "T001",
            "墙刷白色无机涂料三度",
            bbox=[0.10, 0.20, 0.20, 0.22],
            tile_id="p001_r010_c010",
            row_index=1,
        ),
        _evidence(
            "T002",
            "+50mm黑色拉丝不锈钢踢脚线",
            bbox=[0.205, 0.201, 0.33, 0.221],
            tile_id="p001_r010_c010",
            row_index=2,
        ),
        _evidence(
            "T003",
            "材料名称",
            bbox=[0.10, 0.175, 0.16, 0.193],
            tile_id="p001_r009_c010",
            row_index=3,
        ),
        _evidence(
            "T004",
            "宏发建设有限公司",
            bbox=[0.85, 0.85, 0.95, 0.87],
            tile_id="p001_r040_c040",
            row_index=4,
        ),
    ]

    report = build_ocr_context_packages(
        raw_evidences=raw_evidences,
        output_dir=tmp_path / "context",
        max_nearby=3,
        max_page_distance=0.08,
    )

    assert report["summary"]["input_evidence_count"] == 4
    assert report["summary"]["context_package_count"] == 4
    packages = {row["text_id"]: row for row in report["packages"]}
    assert packages["T001"]["current_text"] == "墙刷白色无机涂料三度"
    assert "T002" in packages["T001"]["nearby_text_ids"]
    assert "T003" in packages["T001"]["nearby_text_ids"]
    assert "T004" not in packages["T001"]["nearby_text_ids"]
    assert "当前文字：墙刷白色无机涂料三度" in packages["T001"]["llm_context_text"]
    assert "周边文字：" in packages["T001"]["llm_context_text"]
    assert packages["T001"]["nearby_evidences"][0]["relation"] == "same_tile"
    enriched = {row["text_id"]: row for row in report["enriched_evidences"]}
    assert enriched["T001"]["nearby_text_ids"] == packages["T001"]["nearby_text_ids"]

    context_jsonl = Path(report["outputs"]["ocr_context_packages_jsonl"])
    context_csv = Path(report["outputs"]["ocr_context_packages_csv"])
    enriched_jsonl = Path(report["outputs"]["ocr_raw_evidence_with_context_jsonl"])
    summary_json = Path(report["outputs"]["ocr_context_summary_json"])
    assert context_jsonl.exists()
    assert context_csv.exists()
    assert enriched_jsonl.exists()
    assert summary_json.exists()
    assert len(context_jsonl.read_text(encoding="utf-8").splitlines()) == 4
    assert json.loads(summary_json.read_text(encoding="utf-8"))["packages_with_nearby_count"] >= 3


def _evidence(
    text_id: str,
    text: str,
    *,
    bbox: list[float],
    tile_id: str,
    row_index: int,
) -> dict[str, object]:
    return {
        "schema_version": "drawing_ocr_raw_evidence_v1",
        "source_row_index": row_index,
        "text_id": text_id,
        "source_file": "drawing.pdf",
        "page": 1,
        "text": text,
        "confidence": 0.99,
        "bbox_ratio": bbox,
        "bbox_page_pt": [value * 1000 for value in bbox],
        "bbox_width_ratio": bbox[2] - bbox[0],
        "bbox_height_ratio": bbox[3] - bbox[1],
        "bbox_center_x": (bbox[0] + bbox[2]) / 2,
        "bbox_center_y": (bbox[1] + bbox[3]) / 2,
        "tile_id": tile_id,
        "snippet_id": f"{text_id}_snippet",
        "image_path": f"C:/tmp/{text_id}.png",
        "text_length": len(text),
        "is_single_char": len(text) == 1,
        "has_chinese": True,
        "has_number": any(char.isdigit() for char in text),
        "has_dimension_pattern": "50mm" in text,
        "page_zone": "top_left",
        "nearby_text_ids": [],
    }
