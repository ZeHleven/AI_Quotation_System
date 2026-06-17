from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


TEXT_ENTITY_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
DEFAULT_TEXT_RECORD_LIMIT = 20000

ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("drawing_catalog", ("施工图目录", "图纸目录", "目录表", "目录")),
    ("design_note", ("设计说明", "工程概况", "说明")),
    ("material_table", ("材料表", "材料说明", "主材", "材料做法")),
    ("construction_method", ("构造做法", "做法表", "通用节点", "节点图集")),
    ("plan", ("平面", "天花", "地面铺装", "布置图")),
    ("elevation", ("立面",)),
    ("detail", ("大样", "节点", "详图")),
    ("project_identity", ("项目名称", "职工餐厅", "食堂", "装修改造")),
)


class DxfTextExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class DxfTextRecord:
    source_file: str
    entity_type: str
    text: str
    layer: str
    layout: str
    block_name: str
    x: float | None
    y: float | None
    z: float | None
    height: float | None
    rotation: float | None
    line_number: int
    role_tags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "entity_type": self.entity_type,
            "text": self.text,
            "layer": self.layer,
            "layout": self.layout,
            "block_name": self.block_name,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "height": self.height,
            "rotation": self.rotation,
            "line_number": self.line_number,
            "role_tags": list(self.role_tags),
        }


@dataclass(frozen=True)
class ParsedDxfFile:
    path: str
    file_name: str
    size_bytes: int
    detected_encoding: str
    declared_codepage: str
    acad_version: str
    layers: tuple[str, ...]
    layouts: tuple[str, ...]
    block_records: tuple[str, ...]
    entity_counts: dict[str, int]
    layer_entity_counts: dict[str, int]
    text_entity_count: int
    stored_text_record_count: int
    text_record_limit_reached: bool
    role_counts: dict[str, int]
    text_records: tuple[DxfTextRecord, ...]

    def as_summary_dict(self, *, sample_limit: int = 50, important_limit: int = 200) -> dict[str, Any]:
        important = [
            record.as_dict()
            for record in self.text_records
            if record.role_tags
        ][:important_limit]
        return {
            "path": self.path,
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "detected_encoding": self.detected_encoding,
            "declared_codepage": self.declared_codepage,
            "acad_version": self.acad_version,
            "layer_count": len(self.layers),
            "layout_count": len(self.layouts),
            "block_record_count": len(self.block_records),
            "entity_counts": self.entity_counts,
            "top_layers_by_entity_count": _counter_top(self.layer_entity_counts, 20),
            "text_entity_count": self.text_entity_count,
            "stored_text_record_count": self.stored_text_record_count,
            "text_record_limit_reached": self.text_record_limit_reached,
            "role_counts": self.role_counts,
            "layers_sample": list(self.layers[:sample_limit]),
            "layouts": list(self.layouts),
            "text_samples": [record.as_dict() for record in self.text_records[:sample_limit]],
            "important_texts": important,
        }


def collect_dxf_files(dxf_dir: str | Path | None = None, dxf_files: Iterable[str | Path] | None = None) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    if dxf_dir:
        directory = Path(dxf_dir)
        if not directory.exists():
            raise DxfTextExtractionError(f"DXF directory not found: {directory}")
        if not directory.is_dir():
            raise DxfTextExtractionError(f"DXF directory is not a directory: {directory}")
        for path in [*sorted(directory.glob("*.dxf")), *sorted(directory.glob("*.DXF"))]:
            _append_unique_path(paths, seen, path)
    for raw_path in dxf_files or []:
        _append_unique_path(paths, seen, Path(raw_path))
    return paths


def parse_dxf_file(path: str | Path, *, text_record_limit: int = DEFAULT_TEXT_RECORD_LIMIT) -> ParsedDxfFile:
    dxf_path = Path(path)
    if not dxf_path.exists():
        raise DxfTextExtractionError(f"DXF file not found: {dxf_path}")
    if not dxf_path.is_file():
        raise DxfTextExtractionError(f"DXF path is not a file: {dxf_path}")

    encoding = detect_dxf_encoding(dxf_path)
    layers: set[str] = set()
    layouts: set[str] = set()
    block_records: set[str] = set()
    entity_counts: Counter[str] = Counter()
    layer_entity_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    text_records: list[DxfTextRecord] = []
    text_entity_count = 0
    text_record_limit_reached = False
    header: dict[str, str] = {}

    current_section = ""
    pending_section_name = False
    current_header_var = ""
    current_record_type = ""
    current_record_section = ""
    current_record_line = 0
    current_record_pairs: list[tuple[str, str]] = []

    def finish_record() -> None:
        nonlocal text_entity_count, text_record_limit_reached
        if not current_record_type:
            return
        entity_counts[current_record_type] += 1
        groups = _groups(current_record_pairs)
        layer = _first(groups, "8")
        if layer:
            layer_entity_counts[layer] += 1
        if current_record_type == "LAYER":
            name = _first(groups, "2")
            if name:
                layers.add(name)
        elif current_record_type == "LAYOUT":
            name = _first(groups, "1") or _first(groups, "2")
            if name:
                layouts.add(name)
        elif current_record_type == "BLOCK_RECORD":
            name = _first(groups, "2")
            if name:
                block_records.add(name)
        elif current_record_type in TEXT_ENTITY_TYPES:
            text_entity_count += 1
            record = _build_text_record(
                dxf_path.name,
                current_record_type,
                current_record_section,
                current_record_line,
                current_record_pairs,
            )
            if record and len(text_records) < text_record_limit:
                text_records.append(record)
                for tag in record.role_tags:
                    role_counts[tag] += 1
            elif record:
                text_record_limit_reached = True

    for code, value, line_number in _iter_dxf_pairs(dxf_path, encoding):
        clean_code = code.strip()
        clean_value = value.strip()

        if current_section == "HEADER":
            if clean_code == "9":
                current_header_var = clean_value
            elif current_header_var:
                header[current_header_var] = clean_value
                current_header_var = ""

        if pending_section_name and clean_code == "2":
            current_section = clean_value
            pending_section_name = False
            continue

        if clean_code == "0":
            finish_record()
            current_record_type = ""
            current_record_pairs = []
            current_record_line = 0
            current_record_section = ""

            if clean_value == "SECTION":
                pending_section_name = True
                continue
            if clean_value == "ENDSEC":
                current_section = ""
                current_header_var = ""
                continue
            if clean_value in {"EOF", "ENDTAB", "SEQEND"}:
                continue

            current_record_type = clean_value
            current_record_section = current_section
            current_record_line = line_number
            continue

        if current_record_type:
            current_record_pairs.append((clean_code, value.rstrip("\r\n")))

    finish_record()

    return ParsedDxfFile(
        path=str(dxf_path.resolve()),
        file_name=dxf_path.name,
        size_bytes=dxf_path.stat().st_size,
        detected_encoding=encoding,
        declared_codepage=header.get("$DWGCODEPAGE", ""),
        acad_version=header.get("$ACADVER", ""),
        layers=tuple(sorted(layers)),
        layouts=tuple(sorted(layouts)),
        block_records=tuple(sorted(block_records)),
        entity_counts=dict(sorted(entity_counts.items())),
        layer_entity_counts=dict(layer_entity_counts.most_common()),
        text_entity_count=text_entity_count,
        stored_text_record_count=len(text_records),
        text_record_limit_reached=text_record_limit_reached,
        role_counts=dict(sorted(role_counts.items())),
        text_records=tuple(text_records),
    )


def build_dxf_extraction_report(parsed_files: list[ParsedDxfFile]) -> dict[str, Any]:
    total_text = sum(item.text_entity_count for item in parsed_files)
    total_stored = sum(item.stored_text_record_count for item in parsed_files)
    entity_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    for parsed in parsed_files:
        entity_counts.update(parsed.entity_counts)
        role_counts.update(parsed.role_counts)
    return {
        "ok": True,
        "phase": "BIZ-2x-3",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "file_count": len(parsed_files),
            "total_text_entity_count": total_text,
            "stored_text_record_count": total_stored,
            "text_record_limit_reached": any(item.text_record_limit_reached for item in parsed_files),
            "entity_counts": dict(entity_counts.most_common()),
            "role_counts": dict(role_counts.most_common()),
        },
        "files": [item.as_summary_dict() for item in parsed_files],
    }


def build_dxf_text_csv_rows(parsed_files: list[ParsedDxfFile]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for parsed in parsed_files:
        for index, record in enumerate(parsed.text_records, start=1):
            rows.append(
                {
                    "文件名": parsed.file_name,
                    "序号": index,
                    "文字": record.text,
                    "业务标签": " / ".join(record.role_tags),
                    "实体类型": record.entity_type,
                    "图层": record.layer,
                    "布局": record.layout,
                    "块名": record.block_name,
                    "X": record.x,
                    "Y": record.y,
                    "Z": record.z,
                    "字高": record.height,
                    "旋转角": record.rotation,
                    "源行号": record.line_number,
                }
            )
    return rows


def build_dxf_extraction_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-3 DXF 图纸文本与图层提取报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- DXF 文件数：{summary['file_count']}",
        f"- 文字实体数：{summary['total_text_entity_count']}",
        f"- 已保存文字记录数：{summary['stored_text_record_count']}",
        f"- 是否达到保存上限：{'是' if summary['text_record_limit_reached'] else '否'}",
        "",
        "## 业务标签统计",
        "",
    ]
    if summary["role_counts"]:
        for role, count in summary["role_counts"].items():
            lines.append(f"- `{role}`：{count}")
    else:
        lines.append("- 暂未识别到业务标签")

    lines.extend(["", "## 文件摘要", ""])
    for item in report["files"]:
        lines.extend(
            [
                f"### {item['file_name']}",
                "",
                f"- 编码：{item['detected_encoding']}（DXF 声明：{item['declared_codepage'] or '-'}）",
                f"- ACAD 版本：{item['acad_version'] or '-'}",
                f"- 图层数：{item['layer_count']}",
                f"- Layout 数：{item['layout_count']}",
                f"- 文字实体数：{item['text_entity_count']}",
                f"- 业务标签：{json.dumps(item['role_counts'], ensure_ascii=False)}",
                "",
            ]
        )
        important = item.get("important_texts", [])[:20]
        if important:
            lines.append("重要文字样例：")
            for record in important:
                text = str(record["text"]).replace("\n", " / ")
                tags = " / ".join(record["role_tags"])
                lines.append(f"- [{tags}] {text}")
            lines.append("")
    return "\n".join(lines)


def write_dxf_extraction_outputs(
    parsed_files: list[ParsedDxfFile],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x3_DXF图纸文本图层提取_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report = build_dxf_extraction_report(parsed_files)

    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_dxf_extraction_markdown(report), encoding="utf-8")

    rows = build_dxf_text_csv_rows(parsed_files)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "csv": str(csv_path),
    }


def detect_dxf_encoding(path: Path) -> str:
    sample = path.read_bytes()[:256 * 1024]
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8"


def _iter_dxf_pairs(path: Path, encoding: str):
    with path.open("r", encoding=encoding, errors="replace") as handle:
        line_number = 0
        while True:
            code_line = handle.readline()
            if not code_line:
                return
            line_number += 1
            value_line = handle.readline()
            if not value_line:
                return
            line_number += 1
            yield code_line.rstrip("\r\n"), value_line.rstrip("\r\n"), line_number - 1


def _build_text_record(
    source_file: str,
    entity_type: str,
    section: str,
    line_number: int,
    pairs: list[tuple[str, str]],
) -> DxfTextRecord | None:
    groups = _groups(pairs)
    raw_text_parts: list[str] = []
    for code, value in pairs:
        if code in {"1", "3"}:
            raw_text_parts.append(value)
    text = clean_dxf_text("".join(raw_text_parts))
    if not text:
        return None
    layout = _first(groups, "410")
    if not layout and section == "BLOCKS":
        layout = "BLOCKS"
    return DxfTextRecord(
        source_file=source_file,
        entity_type=entity_type,
        text=text,
        layer=_first(groups, "8"),
        layout=layout,
        block_name=_first(groups, "2"),
        x=_float_or_none(_first(groups, "10")),
        y=_float_or_none(_first(groups, "20")),
        z=_float_or_none(_first(groups, "30")),
        height=_float_or_none(_first(groups, "40")),
        rotation=_float_or_none(_first(groups, "50")),
        line_number=line_number,
        role_tags=tuple(classify_text_roles(text)),
    )


def clean_dxf_text(raw: str) -> str:
    text = re.sub(r"\\p[^;]*;", "", raw)
    text = text.replace("\\P", "\n").replace("\\p", "\n")
    text = text.replace("\\~", " ")
    text = re.sub(r"%%[cC]", "Φ", text)
    text = re.sub(r"%%[dD]", "°", text)
    text = re.sub(r"%%[pP]", "±", text)
    text = re.sub(r"\\[AaCcFfHhQqTtWw][^;]*;", "", text)
    text = re.sub(r"\\S([^;]+);", r"\1", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\\\", "\\")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify_text_roles(text: str) -> list[str]:
    tags: list[str] = []
    compact = text.replace(" ", "")
    for role, keywords in ROLE_KEYWORDS:
        if any(keyword in compact for keyword in keywords):
            tags.append(role)
    return tags


def _groups(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for code, value in pairs:
        groups.setdefault(code, []).append(value.strip())
    return groups


def _first(groups: dict[str, list[str]], code: str) -> str:
    values = groups.get(code) or []
    return values[0] if values else ""


def _float_or_none(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _counter_top(counter_or_dict: Counter[str] | dict[str, int], limit: int) -> list[dict[str, Any]]:
    counter = counter_or_dict if isinstance(counter_or_dict, Counter) else Counter(counter_or_dict)
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _append_unique_path(paths: list[Path], seen: set[str], path: Path) -> None:
    key = str(path.resolve()).lower() if path.exists() else str(path).lower()
    if key not in seen:
        seen.add(key)
        paths.append(path)
