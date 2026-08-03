from __future__ import annotations

import csv
import json
from pathlib import Path

from app.services.drawing_ocr_raw_evidence import build_ocr_raw_evidence_repository


def test_raw_evidence_repository_preserves_rows_and_adds_derived_fields(tmp_path: Path) -> None:
    image_path = tmp_path / "snippet.png"
    image_path.write_bytes(b"fake image")
    input_csv = tmp_path / "all_text_evidence_64.csv"
    with input_csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "text_id",
                "source_file",
                "page",
                "text",
                "confidence",
                "bbox_ratio",
                "bbox_page_pt",
                "tile_id",
                "snippet_id",
                "image_path",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "text_id": "T001",
                "source_file": "drawing.pdf",
                "page": "1",
                "text": "600X1200白色墙面砖",
                "confidence": "0.99",
                "bbox_ratio": "[0.1, 0.2, 0.2, 0.22]",
                "bbox_page_pt": "[10, 20, 20, 22]",
                "tile_id": "p001_r001_c001",
                "snippet_id": "s001",
                "image_path": str(image_path),
            }
        )
        writer.writerow(
            {
                "text_id": "T002",
                "source_file": "drawing.pdf",
                "page": "1",
                "text": "A",
                "confidence": "1.0",
                "bbox_ratio": "[0.7, 0.8, 0.71, 0.81]",
                "bbox_page_pt": "[70, 80, 71, 81]",
                "tile_id": "p001_r002_c002",
                "snippet_id": "s002",
                "image_path": "",
            }
        )

    report = build_ocr_raw_evidence_repository(input_csv=input_csv, output_dir=tmp_path / "raw_evidence")

    assert report["summary"]["raw_row_count"] == 2
    assert report["summary"]["evidence_count"] == 2
    assert report["summary"]["image_path_exists_count"] == 1
    rows = {row["text_id"]: row for row in report["evidences"]}
    assert rows["T001"]["text"] == "600X1200白色墙面砖"
    assert rows["T001"]["has_chinese"] is True
    assert rows["T001"]["has_number"] is True
    assert rows["T001"]["has_dimension_pattern"] is True
    assert rows["T001"]["is_single_char"] is False
    assert rows["T001"]["page_zone"] == "top_left"
    assert rows["T001"]["nearby_text_ids"] == []
    assert rows["T002"]["is_single_char"] is True
    assert rows["T002"]["page_zone"] == "bottom_right"

    jsonl_path = Path(report["outputs"]["ocr_raw_evidence_jsonl"])
    csv_path = Path(report["outputs"]["ocr_raw_evidence_csv"])
    summary_path = Path(report["outputs"]["ocr_raw_evidence_summary_json"])
    assert jsonl_path.exists()
    assert csv_path.exists()
    assert summary_path.exists()
    assert len(jsonl_path.read_text(encoding="utf-8").splitlines()) == 2
    assert json.loads(summary_path.read_text(encoding="utf-8"))["schema_version"] == "drawing_ocr_raw_evidence_v1"
