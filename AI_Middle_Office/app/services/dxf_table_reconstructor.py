from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.dxf_text_extractor import (
    DxfTextRecord,
    ParsedDxfFile,
    build_dxf_extraction_report,
)


TABLE_TYPES = {
    "drawing_catalog": "图纸目录",
    "material_table": "材料表",
    "construction_method": "构造做法/通用节点",
}

SHEET_TYPE_LABELS = {
    "drawing_catalog": "图纸目录",
    "material_table": "材料表",
    "design_note": "设计说明",
    "construction_method": "构造做法/通用节点",
    "plan": "平面图",
    "elevation": "立面图",
    "detail": "大样/节点图",
    "project_identity": "项目身份",
}

GENERIC_TITLE_TEXTS = {
    "图号",
    "图名",
    "图号 Drawing No.",
    "图纸名称",
    "Drawing title",
    "项目名称",
    "Project Name",
    "材料名称",
    "说明",
    "说明:",
    "备注",
}


@dataclass(frozen=True)
class SpatialTextCell:
    text: str
    x: float
    y: float
    layer: str
    layout: str
    source_file: str
    line_number: int
    role_tags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "layer": self.layer,
            "layout": self.layout,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "role_tags": list(self.role_tags),
        }


@dataclass(frozen=True)
class SpatialTextRow:
    source_file: str
    y: float
    cells: tuple[SpatialTextCell, ...]

    @property
    def row_text(self) -> str:
        return " | ".join(cell.text for cell in self.cells if cell.text)

    @property
    def min_x(self) -> float:
        return min((cell.x for cell in self.cells), default=0.0)

    @property
    def max_x(self) -> float:
        return max((cell.x for cell in self.cells), default=0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "y": self.y,
            "row_text": self.row_text,
            "cells": [cell.as_dict() for cell in self.cells],
        }


@dataclass(frozen=True)
class TableCandidate:
    source_file: str
    table_type: str
    table_type_label: str
    anchor_text: str
    anchor_x: float
    anchor_y: float
    confidence: float
    row_count: int
    column_count: int
    rows: tuple[SpatialTextRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "table_type": self.table_type,
            "table_type_label": self.table_type_label,
            "anchor_text": self.anchor_text,
            "anchor_x": self.anchor_x,
            "anchor_y": self.anchor_y,
            "confidence": self.confidence,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "rows": [row.as_dict() for row in self.rows],
        }


@dataclass(frozen=True)
class DrawingIndexEntry:
    source_file: str
    sheet_title: str
    drawing_type: str
    drawing_type_label: str
    sheet_no: str
    x: float
    y: float
    layer: str
    occurrence_count: int
    confidence: float
    evidence_texts: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "sheet_title": self.sheet_title,
            "drawing_type": self.drawing_type,
            "drawing_type_label": self.drawing_type_label,
            "sheet_no": self.sheet_no,
            "x": self.x,
            "y": self.y,
            "layer": self.layer,
            "occurrence_count": self.occurrence_count,
            "confidence": self.confidence,
            "evidence_texts": list(self.evidence_texts),
        }


def reconstruct_dxf_tables(parsed_files: list[ParsedDxfFile], *, row_limit_per_table: int = 30) -> dict[str, Any]:
    table_candidates: list[TableCandidate] = []
    drawing_index_entries: list[DrawingIndexEntry] = []
    file_summaries: list[dict[str, Any]] = []

    for parsed in parsed_files:
        records = _records_with_coordinates(parsed.text_records)
        rows = build_spatial_rows(records)
        file_tables = _detect_table_candidates(parsed.file_name, rows, row_limit_per_table=row_limit_per_table)
        file_index = _build_drawing_index(parsed.file_name, records, rows)
        table_candidates.extend(file_tables)
        drawing_index_entries.extend(file_index)
        file_summaries.append(
            {
                "file_name": parsed.file_name,
                "spatial_row_count": len(rows),
                "table_candidate_count": len(file_tables),
                "drawing_index_entry_count": len(file_index),
            }
        )

    table_counts = Counter(candidate.table_type for candidate in table_candidates)
    drawing_counts = Counter(entry.drawing_type for entry in drawing_index_entries)
    return {
        "ok": True,
        "phase": "BIZ-2x-3",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "file_count": len(parsed_files),
            "table_candidate_count": len(table_candidates),
            "table_type_counts": dict(table_counts.most_common()),
            "drawing_index_entry_count": len(drawing_index_entries),
            "drawing_type_counts": dict(drawing_counts.most_common()),
            "source_extraction_summary": build_dxf_extraction_report(parsed_files)["summary"],
        },
        "files": file_summaries,
        "table_candidates": [candidate.as_dict() for candidate in table_candidates],
        "drawing_index_entries": [entry.as_dict() for entry in drawing_index_entries],
    }


def build_spatial_rows(records: list[DxfTextRecord]) -> list[SpatialTextRow]:
    cells = [
        SpatialTextCell(
            text=record.text.strip(),
            x=float(record.x),
            y=float(record.y),
            layer=record.layer,
            layout=record.layout,
            source_file=record.source_file,
            line_number=record.line_number,
            role_tags=record.role_tags,
        )
        for record in records
        if record.x is not None and record.y is not None and record.text.strip()
    ]
    if not cells:
        return []

    tolerance = _row_y_tolerance(records)
    rows: list[list[SpatialTextCell]] = []
    row_ys: list[float] = []
    for cell in sorted(cells, key=lambda item: (-item.y, item.x)):
        matched_index = -1
        for index, row_y in enumerate(row_ys):
            if abs(cell.y - row_y) <= tolerance:
                matched_index = index
                break
        if matched_index == -1:
            rows.append([cell])
            row_ys.append(cell.y)
        else:
            rows[matched_index].append(cell)
            row_ys[matched_index] = sum(item.y for item in rows[matched_index]) / len(rows[matched_index])

    spatial_rows: list[SpatialTextRow] = []
    for row, row_y in zip(rows, row_ys):
        sorted_cells = tuple(sorted(row, key=lambda item: item.x))
        source_file = sorted_cells[0].source_file if sorted_cells else ""
        spatial_rows.append(SpatialTextRow(source_file=source_file, y=row_y, cells=sorted_cells))
    return spatial_rows


def build_table_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-3 图纸表格重建与图纸索引归档报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- DXF 文件数：{summary['file_count']}",
        f"- 表格候选数：{summary['table_candidate_count']}",
        f"- 图纸索引候选数：{summary['drawing_index_entry_count']}",
        f"- 表格类型统计：{json.dumps(summary['table_type_counts'], ensure_ascii=False)}",
        f"- 图纸类型统计：{json.dumps(summary['drawing_type_counts'], ensure_ascii=False)}",
        "",
        "## 图纸索引候选",
        "",
        "| 文件 | 图号候选 | 图名 | 类型 | 置信度 | 出现次数 |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for entry in report["drawing_index_entries"][:120]:
        lines.append(
            f"| {entry['source_file']} | {entry['sheet_no'] or '-'} | {entry['sheet_title']} | "
            f"{entry['drawing_type_label']} | {entry['confidence']:.2f} | {entry['occurrence_count']} |"
        )

    lines.extend(["", "## 表格候选", ""])
    for candidate in report["table_candidates"][:30]:
        lines.extend(
            [
                f"### {candidate['source_file']} - {candidate['table_type_label']}",
                "",
                f"- 锚点：{candidate['anchor_text']} ({candidate['anchor_x']:.2f}, {candidate['anchor_y']:.2f})",
                f"- 行数：{candidate['row_count']}，列数估计：{candidate['column_count']}，置信度：{candidate['confidence']:.2f}",
                "",
            ]
        )
        for row in candidate["rows"][:12]:
            lines.append(f"- {row['row_text']}")
        lines.append("")
    return "\n".join(lines)


def build_table_rows_csv(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table_index, candidate in enumerate(report["table_candidates"], start=1):
        for row_index, row in enumerate(candidate["rows"], start=1):
            rows.append(
                {
                    "表格序号": table_index,
                    "文件名": candidate["source_file"],
                    "表格类型": candidate["table_type_label"],
                    "锚点文字": candidate["anchor_text"],
                    "行号": row_index,
                    "Y": row["y"],
                    "行文本": row["row_text"],
                    "列文本": " / ".join(cell["text"] for cell in row["cells"]),
                }
            )
    return rows


def build_drawing_index_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "文件名": entry["source_file"],
            "图号候选": entry["sheet_no"],
            "图名": entry["sheet_title"],
            "图纸类型": entry["drawing_type_label"],
            "X": entry["x"],
            "Y": entry["y"],
            "图层": entry["layer"],
            "出现次数": entry["occurrence_count"],
            "置信度": entry["confidence"],
            "证据文本": " / ".join(entry["evidence_texts"]),
        }
        for entry in report["drawing_index_entries"]
    ]


def write_table_reconstruction_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x3_DXF表格重建与图纸索引_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    table_csv_path = target_dir / f"{file_stem}_表格候选.csv"
    drawing_csv_path = target_dir / f"{file_stem}_图纸索引.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_table_markdown(report), encoding="utf-8")
    _write_csv(table_csv_path, build_table_rows_csv(report))
    _write_csv(drawing_csv_path, build_drawing_index_csv_rows(report))
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "table_csv": str(table_csv_path),
        "drawing_index_csv": str(drawing_csv_path),
    }


def _detect_table_candidates(
    source_file: str,
    rows: list[SpatialTextRow],
    *,
    row_limit_per_table: int,
) -> list[TableCandidate]:
    candidates: list[TableCandidate] = []
    seen: set[tuple[str, int, int]] = set()
    for row in rows:
        table_type = _infer_table_type(row.row_text, _row_tags(row))
        if not table_type:
            continue
        anchor = _anchor_cell(row, table_type)
        nearby = _nearby_rows(rows, row, anchor, limit=row_limit_per_table)
        if not nearby:
            continue
        table_type = _refine_table_type_from_neighborhood(table_type, nearby)
        key = (table_type, round(anchor.x / 500), round(max(item.y for item in nearby) / 250))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            TableCandidate(
                source_file=source_file,
                table_type=table_type,
                table_type_label=TABLE_TYPES[table_type],
                anchor_text=anchor.text,
                anchor_x=anchor.x,
                anchor_y=anchor.y,
                confidence=_table_confidence(table_type, row.row_text),
                row_count=len(nearby),
                column_count=max((len(item.cells) for item in nearby), default=0),
                rows=tuple(nearby),
            )
        )
    candidates.sort(key=lambda item: (item.source_file, item.table_type, -item.anchor_y, item.anchor_x))
    return candidates


def _build_drawing_index(
    source_file: str,
    records: list[DxfTextRecord],
    rows: list[SpatialTextRow],
) -> list[DrawingIndexEntry]:
    candidates: list[dict[str, Any]] = []
    for record in records:
        if record.x is None or record.y is None:
            continue
        title = _clean_title(record.text)
        if not _looks_like_sheet_title(title, record.role_tags):
            continue
        drawing_type = _infer_drawing_type(title, record.role_tags)
        if not drawing_type:
            continue
        sheet_no = _find_nearby_sheet_no(record, records, rows)
        evidence = [title]
        if sheet_no:
            evidence.append(sheet_no)
        candidates.append(
            {
                "source_file": source_file,
                "sheet_title": title,
                "drawing_type": drawing_type,
                "drawing_type_label": SHEET_TYPE_LABELS.get(drawing_type, drawing_type),
                "sheet_no": sheet_no,
                "x": float(record.x),
                "y": float(record.y),
                "layer": record.layer,
                "confidence": 0.86 if sheet_no else 0.68,
                "evidence_texts": evidence,
            }
        )

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            candidate["source_file"],
            _normalize_title(candidate["sheet_title"]),
            candidate["drawing_type"],
            candidate["sheet_no"],
        )
        grouped.setdefault(key, []).append(candidate)

    entries: list[DrawingIndexEntry] = []
    for items in grouped.values():
        best = max(items, key=lambda item: (item["confidence"], len(item["sheet_title"])))
        evidence = []
        for item in items[:5]:
            for text in item["evidence_texts"]:
                if text and text not in evidence:
                    evidence.append(text)
        entries.append(
            DrawingIndexEntry(
                source_file=best["source_file"],
                sheet_title=best["sheet_title"],
                drawing_type=best["drawing_type"],
                drawing_type_label=best["drawing_type_label"],
                sheet_no=best["sheet_no"],
                x=best["x"],
                y=best["y"],
                layer=best["layer"],
                occurrence_count=len(items),
                confidence=best["confidence"],
                evidence_texts=tuple(evidence),
            )
        )
    entries.sort(key=lambda item: (item.source_file, item.drawing_type, -item.confidence, item.sheet_title))
    return entries


def _records_with_coordinates(records: tuple[DxfTextRecord, ...]) -> list[DxfTextRecord]:
    return [record for record in records if record.x is not None and record.y is not None and record.text.strip()]


def _row_y_tolerance(records: list[DxfTextRecord]) -> float:
    heights = sorted(
        float(record.height)
        for record in records
        if record.height is not None and math.isfinite(float(record.height)) and 0 < float(record.height) <= 500
    )
    if not heights:
        return 5.0
    median = heights[len(heights) // 2]
    return max(2.0, min(25.0, median * 1.5))


def _infer_table_type(row_text: str, tags: set[str]) -> str:
    compact = row_text.replace(" ", "")
    if "drawing_catalog" in tags or "施工图目录" in compact or "图纸目录" in compact or ("图号" in compact and "图名" in compact):
        return "drawing_catalog"
    if "material_table" in tags or "材料表" in compact or "材料名称" in compact or "图例与材料" in compact:
        return "material_table"
    if (
        "construction_method" in tags
        or "通用节点" in compact
        or "构造做法" in compact
        or "做法详图" in compact
        or "工程做法" in compact
    ):
        return "construction_method"
    return ""


def _row_tags(row: SpatialTextRow) -> set[str]:
    return {tag for cell in row.cells for tag in cell.role_tags}


def _anchor_cell(row: SpatialTextRow, table_type: str) -> SpatialTextCell:
    for cell in row.cells:
        if table_type in cell.role_tags:
            return cell
    for cell in row.cells:
        if _infer_table_type(cell.text, set(cell.role_tags)) == table_type:
            return cell
    return row.cells[0]


def _nearby_rows(
    rows: list[SpatialTextRow],
    anchor_row: SpatialTextRow,
    anchor: SpatialTextCell,
    *,
    limit: int,
) -> list[SpatialTextRow]:
    x_margin_left = 800.0
    x_margin_right = 6500.0
    y_above = 450.0
    y_below = 2600.0
    selected: list[SpatialTextRow] = []
    for row in rows:
        if row.y > anchor_row.y + y_above or row.y < anchor_row.y - y_below:
            continue
        if row.max_x < anchor.x - x_margin_left or row.min_x > anchor.x + x_margin_right:
            continue
        selected.append(row)
    selected.sort(key=lambda item: (-item.y, item.min_x))
    return selected[:limit]


def _table_confidence(table_type: str, row_text: str) -> float:
    compact = row_text.replace(" ", "")
    if table_type == "drawing_catalog" and ("施工图目录" in compact or "图纸目录" in compact):
        return 0.9
    if table_type == "material_table" and ("材料表" in compact or "材料名称" in compact):
        return 0.86
    if table_type == "construction_method" and ("通用节点" in compact or "做法" in compact):
        return 0.82
    return 0.65


def _looks_like_sheet_title(text: str, tags: tuple[str, ...]) -> bool:
    if not text or text in GENERIC_TITLE_TEXTS:
        return False
    if len(text) < 3:
        return False
    if len(text) > 48:
        return False
    if not set(tags) & set(SHEET_TYPE_LABELS):
        return False
    if text.replace(".", "").isdigit():
        return False
    if "project_identity" in tags and not set(tags) & {"plan", "elevation", "drawing_catalog", "material_table", "construction_method", "detail"}:
        return False
    if re.search(r"[，。；;：:]", text) and len(text) > 24:
        return False
    compact = text.replace(" ", "")
    title_patterns = (
        "施工图目录",
        "图纸目录",
        "图例与材料表",
        "材料表",
        "材料说明",
        "施工图设计说明",
        "电气设计说明",
        "给排水设计说明",
        "通用节点",
        "做法详图",
        "工程做法",
        "平面图",
        "布置图",
        "现状图",
        "铺装图",
        "天花",
        "立面图",
        "剖面图",
        "索引图",
        "大样图",
        "详图",
    )
    return any(pattern in compact for pattern in title_patterns)


def _infer_drawing_type(text: str, tags: tuple[str, ...]) -> str:
    compact = text.replace(" ", "")
    if "drawing_catalog" in tags or "目录" in compact:
        return "drawing_catalog"
    if "material_table" in tags or "材料表" in compact:
        return "material_table"
    if "construction_method" in tags or "通用节点" in compact or "做法" in compact:
        return "construction_method"
    if "elevation" in tags or "立面" in compact:
        return "elevation"
    if "detail" in tags or "节点" in compact or "详图" in compact or "大样" in compact:
        return "detail"
    if "plan" in tags or "平面" in compact or "天花" in compact or "地面铺装" in compact:
        return "plan"
    if "design_note" in tags or "说明" in compact or "工程概况" in compact:
        return "design_note"
    if "project_identity" in tags and any(pattern in compact for pattern in ("平面图", "立面图", "剖面图", "索引图", "现状图")):
        return "project_identity"
    return ""


def _find_nearby_sheet_no(record: DxfTextRecord, records: list[DxfTextRecord], rows: list[SpatialTextRow]) -> str:
    if record.x is None or record.y is None:
        return ""
    same_row_codes: list[tuple[float, str]] = []
    for row in rows:
        if abs(row.y - float(record.y)) > 8:
            continue
        for cell in row.cells:
            if _looks_like_sheet_no(cell.text):
                same_row_codes.append((abs(cell.x - float(record.x)), cell.text))
    if same_row_codes:
        same_row_codes.sort(key=lambda item: item[0])
        return same_row_codes[0][1]

    nearby_codes: list[tuple[float, str]] = []
    for other in records:
        if other is record or other.x is None or other.y is None:
            continue
        if not _looks_like_sheet_no(other.text):
            continue
        dx = abs(float(other.x) - float(record.x))
        dy = abs(float(other.y) - float(record.y))
        if dx <= 250 and dy <= 280:
            nearby_codes.append((dx + dy, other.text))
    if nearby_codes:
        nearby_codes.sort(key=lambda item: item[0])
        return nearby_codes[0][1]
    return ""


def _looks_like_sheet_no(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 24:
        return False
    if stripped in GENERIC_TITLE_TEXTS:
        return False
    return bool(re.match(r"^[A-Za-z]{1,5}[A-Za-z0-9]*[-－][A-Za-z0-9一-龥（）()]+$", stripped))


def _refine_table_type_from_neighborhood(table_type: str, rows: list[SpatialTextRow]) -> str:
    text = " ".join(row.row_text for row in rows).replace(" ", "")
    if ("图纸名称" in text and "图纸编号" in text) or ("施工图目录" in text and "序号" in text):
        return "drawing_catalog"
    if "材料名称" in text and ("材料编号" in text or "规格" in text or "图例" in text):
        return "material_table"
    return table_type


def _clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("-", "－")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
