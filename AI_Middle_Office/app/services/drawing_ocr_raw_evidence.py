from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "drawing_ocr_raw_evidence_v1"

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
NUMBER_RE = re.compile(r"\d")
DIMENSION_RE = re.compile(
    r"("
    r"\d+(?:\.\d+)?\s*(?:x|X|\u00d7|\*)\s*\d+(?:\.\d+)?"
    r"|(?:\u5bbd\u5ea6|\u9ad8\u5ea6|\u957f\u5ea6|\u539a\u5ea6|\u58c1\u539a|\u539a)\s*\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*(?:mm|MM|\u6beb\u7c73|cm|CM|\u5398\u7c73|\u33a1|m2|M2|\u5e73\u65b9\u7c73)"
    r")"
)


def build_ocr_raw_evidence_repository(
    *,
    input_csv: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    source_path = Path(input_csv)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    rows = read_ocr_evidence_csv(source_path)
    evidences = [_normalize_evidence(row, index=index) for index, row in enumerate(rows, start=1)]
    summary = _build_summary(evidences, input_csv=source_path)
    outputs = _write_outputs(directory, evidences=evidences, summary=summary)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "evidences": evidences,
        "outputs": outputs,
    }


def read_ocr_evidence_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def _normalize_evidence(row: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    text = _clean_text(row.get("text"))
    bbox_ratio = _parse_number_list(row.get("bbox_ratio"))
    bbox_page_pt = _parse_number_list(row.get("bbox_page_pt"))
    image_path = _clean_text(row.get("image_path"))
    page = _int(row.get("page"))
    x1, y1, x2, y2 = _bbox4(bbox_ratio)
    width = max(0.0, x2 - x1) if bbox_ratio else 0.0
    height = max(0.0, y2 - y1) if bbox_ratio else 0.0
    center_x = (x1 + x2) / 2 if bbox_ratio else None
    center_y = (y1 + y2) / 2 if bbox_ratio else None
    return {
        "schema_version": SCHEMA_VERSION,
        "source_row_index": index,
        "text_id": _clean_text(row.get("text_id")) or f"OCR{index:06d}",
        "source_file": _clean_text(row.get("source_file")),
        "page": page,
        "text": text,
        "confidence": _float(row.get("confidence")),
        "bbox_ratio": bbox_ratio,
        "bbox_page_pt": bbox_page_pt,
        "tile_id": _clean_text(row.get("tile_id")),
        "snippet_id": _clean_text(row.get("snippet_id")),
        "image_path": image_path,
        "image_path_exists": bool(image_path and Path(image_path).is_file()),
        "text_length": len(text),
        "is_single_char": len(text) == 1,
        "has_chinese": bool(CHINESE_RE.search(text)),
        "has_number": bool(NUMBER_RE.search(text)),
        "has_dimension_pattern": bool(DIMENSION_RE.search(text)),
        "bbox_width_ratio": width,
        "bbox_height_ratio": height,
        "bbox_center_x": center_x,
        "bbox_center_y": center_y,
        "page_zone": _page_zone(center_x, center_y),
        "nearby_text_ids": [],
    }


def _build_summary(evidences: Sequence[Mapping[str, Any]], *, input_csv: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "input_csv": str(input_csv.resolve()),
        "raw_row_count": len(evidences),
        "evidence_count": len(evidences),
        "text_present_count": sum(1 for row in evidences if _clean_text(row.get("text"))),
        "empty_text_count": sum(1 for row in evidences if not _clean_text(row.get("text"))),
        "bbox_present_count": sum(1 for row in evidences if row.get("bbox_ratio")),
        "image_path_present_count": sum(1 for row in evidences if _clean_text(row.get("image_path"))),
        "image_path_exists_count": sum(1 for row in evidences if row.get("image_path_exists")),
        "single_char_count": sum(1 for row in evidences if row.get("is_single_char")),
        "has_chinese_count": sum(1 for row in evidences if row.get("has_chinese")),
        "has_number_count": sum(1 for row in evidences if row.get("has_number")),
        "has_dimension_pattern_count": sum(1 for row in evidences if row.get("has_dimension_pattern")),
        "page_counts": dict(Counter(str(row.get("page") or "") for row in evidences)),
        "page_zone_counts": dict(Counter(_clean_text(row.get("page_zone")) for row in evidences)),
        "nearby_text_ids_populated_count": sum(1 for row in evidences if row.get("nearby_text_ids")),
    }


def _write_outputs(directory: Path, *, evidences: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> dict[str, str]:
    jsonl_path = directory / "ocr_raw_evidence.jsonl"
    csv_path = directory / "ocr_raw_evidence.csv"
    summary_path = directory / "ocr_raw_evidence_summary.json"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in evidences:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    _write_csv(csv_path, evidences)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ocr_raw_evidence_jsonl": str(jsonl_path.resolve()),
        "ocr_raw_evidence_csv": str(csv_path.resolve()),
        "ocr_raw_evidence_summary_json": str(summary_path.resolve()),
    }


def _write_csv(path: Path, evidences: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "schema_version",
        "source_row_index",
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
        "image_path_exists",
        "text_length",
        "is_single_char",
        "has_chinese",
        "has_number",
        "has_dimension_pattern",
        "bbox_width_ratio",
        "bbox_height_ratio",
        "bbox_center_x",
        "bbox_center_y",
        "page_zone",
        "nearby_text_ids",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in evidences:
            writer.writerow({key: _csv_value(row.get(key)) for key in headers})


def _parse_number_list(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [_float(item) for item in value]
    text = _clean_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [_float(item) for item in parsed]


def _bbox4(value: Sequence[float]) -> tuple[float, float, float, float]:
    if len(value) != 4:
        return 0.0, 0.0, 0.0, 0.0
    return float(value[0]), float(value[1]), float(value[2]), float(value[3])


def _page_zone(center_x: float | None, center_y: float | None) -> str:
    if center_x is None or center_y is None:
        return "unknown"
    horizontal = "left" if center_x < 1 / 3 else "right" if center_x >= 2 / 3 else "center"
    vertical = "top" if center_y < 1 / 3 else "bottom" if center_y >= 2 / 3 else "middle"
    return f"{vertical}_{horizontal}"


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else value


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
