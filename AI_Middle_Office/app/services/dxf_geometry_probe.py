from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app.services.dxf_text_extractor import detect_dxf_encoding


GEOMETRY_ENTITY_TYPES = {
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "VERTEX",
    "SEQEND",
    "HATCH",
    "INSERT",
    "DIMENSION",
    "CIRCLE",
    "ARC",
    "ELLIPSE",
    "SPLINE",
}

AREA_LAYER_KEYWORDS = ("地面", "铺装", "天花", "吊顶", "防水", "填充", "HATCH", "面层", "楼地面")
LENGTH_LAYER_KEYWORDS = ("踢脚", "窗帘", "线条", "收边", "压条", "门套", "墙身", "幕墙")
COUNT_LAYER_KEYWORDS = ("门", "窗", "灯", "洁具", "家具", "设备", "插座", "开关", "图块")

AREA_CANDIDATE_LIMIT = 300
LENGTH_CANDIDATE_LIMIT = 300
COUNT_CANDIDATE_LIMIT = 300
DIMENSION_CANDIDATE_LIMIT = 300


class DxfGeometryProbeError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedDxfGeometry:
    path: str
    file_name: str
    detected_encoding: str
    entity_counts: dict[str, int]
    layer_entity_counts: dict[str, int]
    geometry_entity_count: int
    area_candidates: tuple[dict[str, Any], ...]
    length_candidates: tuple[dict[str, Any], ...]
    count_candidates: tuple[dict[str, Any], ...]
    dimension_candidates: tuple[dict[str, Any], ...]
    risk_flags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_name": self.file_name,
            "detected_encoding": self.detected_encoding,
            "geometry_entity_count": self.geometry_entity_count,
            "entity_counts": self.entity_counts,
            "top_layers_by_geometry_count": _counter_top(self.layer_entity_counts, 30),
            "area_candidate_count": len(self.area_candidates),
            "length_candidate_count": len(self.length_candidates),
            "count_candidate_count": len(self.count_candidates),
            "dimension_candidate_count": len(self.dimension_candidates),
            "risk_flags": list(self.risk_flags),
            "area_candidates": list(self.area_candidates),
            "length_candidates": list(self.length_candidates),
            "count_candidates": list(self.count_candidates),
            "dimension_candidates": list(self.dimension_candidates),
        }


def collect_dxf_geometry_files(dxf_dir: str | Path | None = None, dxf_files: Iterable[str | Path] | None = None) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    if dxf_dir:
        directory = Path(dxf_dir)
        if not directory.exists():
            raise DxfGeometryProbeError(f"DXF directory not found: {directory}")
        if not directory.is_dir():
            raise DxfGeometryProbeError(f"DXF path is not a directory: {directory}")
        for path in [*sorted(directory.glob("*.dxf")), *sorted(directory.glob("*.DXF"))]:
            _append_unique(paths, seen, path)
    for raw_path in dxf_files or []:
        _append_unique(paths, seen, Path(raw_path))
    return paths


def parse_dxf_geometry_file(path: str | Path) -> ParsedDxfGeometry:
    dxf_path = Path(path)
    if not dxf_path.exists():
        raise DxfGeometryProbeError(f"DXF file not found: {dxf_path}")
    if not dxf_path.is_file():
        raise DxfGeometryProbeError(f"DXF path is not a file: {dxf_path}")

    encoding = detect_dxf_encoding(dxf_path)
    entity_counts: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    area_candidates: list[dict[str, Any]] = []
    length_candidates: list[dict[str, Any]] = []
    count_candidates: list[dict[str, Any]] = []
    dimension_candidates: list[dict[str, Any]] = []
    risk_flags: set[str] = set()

    current_record_type = ""
    current_record_line = 0
    current_pairs: list[tuple[str, str]] = []

    active_polyline: dict[str, Any] | None = None

    def finish_record() -> None:
        nonlocal active_polyline
        if not current_record_type:
            return
        entity_counts[current_record_type] += 1
        groups = _groups(current_pairs)
        layer = _first(groups, "8")
        if current_record_type in GEOMETRY_ENTITY_TYPES:
            layer_counts[layer or "(无图层)"] += 1

        if current_record_type == "LWPOLYLINE":
            candidate = _lwpolyline_candidate(dxf_path.name, current_record_line, groups)
            if candidate["is_closed"] and candidate["vertex_count"] >= 3 and candidate["area"] > 0:
                _append_limited(area_candidates, candidate, AREA_CANDIDATE_LIMIT)
            if candidate["length"] > 0:
                _append_limited(length_candidates, candidate, LENGTH_CANDIDATE_LIMIT)
        elif current_record_type == "LINE":
            candidate = _line_candidate(dxf_path.name, current_record_line, groups)
            if candidate and candidate["length"] > 0:
                _append_limited(length_candidates, candidate, LENGTH_CANDIDATE_LIMIT)
        elif current_record_type == "CIRCLE":
            candidate = _circle_candidate(dxf_path.name, current_record_line, groups)
            if candidate:
                _append_limited(area_candidates, candidate, AREA_CANDIDATE_LIMIT)
                _append_limited(length_candidates, candidate, LENGTH_CANDIDATE_LIMIT)
        elif current_record_type == "HATCH":
            _append_limited(area_candidates, _hatch_candidate(dxf_path.name, current_record_line, groups), AREA_CANDIDATE_LIMIT)
            risk_flags.add("hatch_boundary_area_needs_detailed_parser")
        elif current_record_type == "INSERT":
            _append_limited(count_candidates, _insert_candidate(dxf_path.name, current_record_line, groups), COUNT_CANDIDATE_LIMIT)
        elif current_record_type == "DIMENSION":
            _append_limited(dimension_candidates, _dimension_candidate(dxf_path.name, current_record_line, groups), DIMENSION_CANDIDATE_LIMIT)
        elif current_record_type == "POLYLINE":
            active_polyline = {
                "source_file": dxf_path.name,
                "entity_type": "POLYLINE",
                "line_number": current_record_line,
                "layer": layer,
                "flags": _int_or_zero(_first(groups, "70")),
                "vertices": [],
            }
        elif current_record_type == "VERTEX" and active_polyline is not None:
            x = _float_or_none(_first(groups, "10"))
            y = _float_or_none(_first(groups, "20"))
            if x is not None and y is not None:
                active_polyline["vertices"].append((x, y))
        elif current_record_type == "SEQEND" and active_polyline is not None:
            candidate = _polyline_candidate(active_polyline)
            if candidate["is_closed"] and candidate["vertex_count"] >= 3 and candidate["area"] > 0:
                _append_limited(area_candidates, candidate, AREA_CANDIDATE_LIMIT)
            if candidate["length"] > 0:
                _append_limited(length_candidates, candidate, LENGTH_CANDIDATE_LIMIT)
            active_polyline = None

    for code, value, line_number in _iter_dxf_pairs(dxf_path, encoding):
        clean_code = code.strip()
        clean_value = value.strip()
        if clean_code == "0":
            finish_record()
            current_record_type = ""
            current_pairs = []
            current_record_line = 0
            if clean_value in {"SECTION", "ENDSEC", "EOF", "ENDTAB"}:
                continue
            current_record_type = clean_value
            current_record_line = line_number
            continue
        if current_record_type:
            current_pairs.append((clean_code, value.rstrip("\r\n")))
    finish_record()

    if not area_candidates:
        risk_flags.add("no_closed_area_geometry_candidate_detected")
    if not dimension_candidates:
        risk_flags.add("no_dimension_entity_candidate_detected")
    if count_candidates and _too_many_anonymous_blocks(count_candidates):
        risk_flags.add("many_anonymous_or_generic_blocks_need_symbol_mapping")
    if len(area_candidates) >= AREA_CANDIDATE_LIMIT:
        risk_flags.add("area_candidate_sample_limit_reached")
    if len(length_candidates) >= LENGTH_CANDIDATE_LIMIT:
        risk_flags.add("length_candidate_sample_limit_reached")
    if len(count_candidates) >= COUNT_CANDIDATE_LIMIT:
        risk_flags.add("count_candidate_sample_limit_reached")
    if len(dimension_candidates) >= DIMENSION_CANDIDATE_LIMIT:
        risk_flags.add("dimension_candidate_sample_limit_reached")

    return ParsedDxfGeometry(
        path=str(dxf_path.resolve()),
        file_name=dxf_path.name,
        detected_encoding=encoding,
        entity_counts=dict(entity_counts.most_common()),
        layer_entity_counts=dict(layer_counts.most_common()),
        geometry_entity_count=sum(entity_counts.get(item, 0) for item in GEOMETRY_ENTITY_TYPES),
        area_candidates=tuple(area_candidates),
        length_candidates=tuple(length_candidates),
        count_candidates=tuple(count_candidates),
        dimension_candidates=tuple(dimension_candidates),
        risk_flags=tuple(sorted(risk_flags)),
    )


def build_geometry_probe_report(parsed_files: list[ParsedDxfGeometry]) -> dict[str, Any]:
    entity_counts: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    area_total = 0
    length_total = 0
    count_total = 0
    dimension_total = 0
    for parsed in parsed_files:
        entity_counts.update(parsed.entity_counts)
        layer_counts.update(parsed.layer_entity_counts)
        risk_counts.update(parsed.risk_flags)
        area_total += len(parsed.area_candidates)
        length_total += len(parsed.length_candidates)
        count_total += len(parsed.count_candidates)
        dimension_total += len(parsed.dimension_candidates)
    return {
        "ok": True,
        "phase": "BIZ-2x-9a-cad-geometry-probe",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_auto_quantity": False,
        "summary": {
            "file_count": len(parsed_files),
            "geometry_entity_count": sum(item.geometry_entity_count for item in parsed_files),
            "area_candidate_count": area_total,
            "length_candidate_count": length_total,
            "count_candidate_count": count_total,
            "dimension_candidate_count": dimension_total,
            "candidate_sample_limits_per_file": {
                "area": AREA_CANDIDATE_LIMIT,
                "length": LENGTH_CANDIDATE_LIMIT,
                "count": COUNT_CANDIDATE_LIMIT,
                "dimension": DIMENSION_CANDIDATE_LIMIT,
            },
            "entity_counts": dict(entity_counts.most_common()),
            "top_layers_by_geometry_count": _counter_top(layer_counts, 40),
            "risk_counts": dict(risk_counts.most_common()),
            "next_step": "manual_review_geometry_layers_and_select_low_risk_quantity_types",
        },
        "files": [item.as_dict() for item in parsed_files],
    }


def build_geometry_probe_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-9a CAD 几何图元探测报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- DXF 文件数：{summary['file_count']}",
        f"- 几何图元数：{summary['geometry_entity_count']}",
        f"- 面积候选数：{summary['area_candidate_count']}",
        f"- 长度候选数：{summary['length_candidate_count']}",
        f"- 数量候选数：{summary['count_candidate_count']}",
        f"- 标注候选数：{summary['dimension_candidate_count']}",
        "- 候选数说明：当前为每文件抽样上限内的候选，不代表最终工程量条数",
        f"- 是否可直接自动算量：{'是' if report.get('safe_for_auto_quantity') else '否，当前仅为探测报告'}",
        "",
        "## 图元类型统计",
        "",
    ]
    for name, count in summary["entity_counts"].items():
        if name in GEOMETRY_ENTITY_TYPES:
            lines.append(f"- `{name}`：{count}")
    lines.extend(["", "## 风险提示", ""])
    if summary["risk_counts"]:
        for name, count in summary["risk_counts"].items():
            lines.append(f"- `{name}`：{count}")
    else:
        lines.append("- 暂无")
    lines.extend(["", "## 文件摘要", ""])
    for item in report["files"]:
        lines.extend(
            [
                f"### {item['file_name']}",
                "",
                f"- 几何图元数：{item['geometry_entity_count']}",
                f"- 面积候选：{item['area_candidate_count']}，长度候选：{item['length_candidate_count']}，数量候选：{item['count_candidate_count']}，标注候选：{item['dimension_candidate_count']}",
                f"- 风险：{', '.join(item['risk_flags']) or '-'}",
                "",
            ]
        )
        top_area = item.get("area_candidates", [])[:5]
        if top_area:
            lines.append("面积候选样例：")
            for candidate in top_area:
                lines.append(
                    f"- {candidate['entity_type']} | 图层 `{candidate['layer']}` | 面积 {candidate.get('area', '')} | 顶点 {candidate.get('vertex_count', '-')}"
                )
            lines.append("")
    lines.extend(
        [
            "## 结论",
            "",
            "- 本报告只说明 CAD 图元中存在可进一步量取的几何候选，不生成工程量。",
            "- 进入自动建议工程量前，必须继续完成比例/单位校验、图框识别、材料/做法关联、扣减规则和 `calculation_trace`。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_geometry_candidate_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file_item in report.get("files", []):
        for candidate_type, key in [
            ("面积候选", "area_candidates"),
            ("长度候选", "length_candidates"),
            ("数量候选", "count_candidates"),
            ("标注候选", "dimension_candidates"),
        ]:
            for candidate in file_item.get(key, []):
                rows.append(
                    {
                        "文件名": file_item["file_name"],
                        "候选类型": candidate_type,
                        "实体类型": candidate.get("entity_type", ""),
                        "图层": candidate.get("layer", ""),
                        "块名": candidate.get("block_name", ""),
                        "源行号": candidate.get("line_number", ""),
                        "面积候选": candidate.get("area", ""),
                        "长度候选": candidate.get("length", ""),
                        "数量候选": candidate.get("count", ""),
                        "标注值": candidate.get("measurement", ""),
                        "顶点数": candidate.get("vertex_count", ""),
                        "是否闭合": candidate.get("is_closed", ""),
                        "分类建议": candidate.get("quantity_hint", ""),
                        "风险说明": "；".join(candidate.get("risk_flags", [])),
                    }
                )
    return rows


def write_geometry_probe_outputs(
    parsed_files: list[ParsedDxfGeometry],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x9a_CAD几何图元探测_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report = build_geometry_probe_report(parsed_files)
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    csv_path = directory / f"{file_stem}_几何候选.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_geometry_probe_markdown(report), encoding="utf-8")
    _write_csv(csv_path, build_geometry_candidate_csv_rows(report))
    return {"json": str(json_path), "markdown": str(md_path), "geometry_candidate_csv": str(csv_path)}


def _lwpolyline_candidate(source_file: str, line_number: int, groups: dict[str, list[str]]) -> dict[str, Any]:
    vertices = _vertices_from_groups(groups)
    flags = _int_or_zero(_first(groups, "70"))
    is_closed = bool(flags & 1)
    length = _polyline_length(vertices, is_closed=is_closed)
    area = abs(_shoelace_area(vertices)) if is_closed and len(vertices) >= 3 else 0.0
    layer = _first(groups, "8")
    return {
        "source_file": source_file,
        "entity_type": "LWPOLYLINE",
        "line_number": line_number,
        "layer": layer,
        "vertex_count": len(vertices),
        "is_closed": is_closed,
        "area": _round(area),
        "length": _round(length),
        "bbox": _bbox(vertices),
        "quantity_hint": _quantity_hint(layer),
        "risk_flags": _geometry_risks(area=area, length=length, layer=layer, is_closed=is_closed),
    }


def _polyline_candidate(active_polyline: dict[str, Any]) -> dict[str, Any]:
    vertices = list(active_polyline.get("vertices") or [])
    flags = int(active_polyline.get("flags") or 0)
    is_closed = bool(flags & 1)
    length = _polyline_length(vertices, is_closed=is_closed)
    area = abs(_shoelace_area(vertices)) if is_closed and len(vertices) >= 3 else 0.0
    layer = str(active_polyline.get("layer") or "")
    return {
        "source_file": active_polyline.get("source_file", ""),
        "entity_type": "POLYLINE",
        "line_number": active_polyline.get("line_number", 0),
        "layer": layer,
        "vertex_count": len(vertices),
        "is_closed": is_closed,
        "area": _round(area),
        "length": _round(length),
        "bbox": _bbox(vertices),
        "quantity_hint": _quantity_hint(layer),
        "risk_flags": _geometry_risks(area=area, length=length, layer=layer, is_closed=is_closed),
    }


def _line_candidate(source_file: str, line_number: int, groups: dict[str, list[str]]) -> dict[str, Any] | None:
    x1 = _float_or_none(_first(groups, "10"))
    y1 = _float_or_none(_first(groups, "20"))
    x2 = _float_or_none(_first(groups, "11"))
    y2 = _float_or_none(_first(groups, "21"))
    if None in {x1, y1, x2, y2}:
        return None
    layer = _first(groups, "8")
    length = math.dist((x1, y1), (x2, y2))
    return {
        "source_file": source_file,
        "entity_type": "LINE",
        "line_number": line_number,
        "layer": layer,
        "length": _round(length),
        "bbox": _bbox([(x1, y1), (x2, y2)]),
        "quantity_hint": _quantity_hint(layer),
        "risk_flags": _geometry_risks(area=0, length=length, layer=layer, is_closed=False),
    }


def _circle_candidate(source_file: str, line_number: int, groups: dict[str, list[str]]) -> dict[str, Any] | None:
    radius = _float_or_none(_first(groups, "40"))
    if radius is None or radius <= 0:
        return None
    layer = _first(groups, "8")
    return {
        "source_file": source_file,
        "entity_type": "CIRCLE",
        "line_number": line_number,
        "layer": layer,
        "area": _round(math.pi * radius * radius),
        "length": _round(2 * math.pi * radius),
        "radius": _round(radius),
        "quantity_hint": _quantity_hint(layer),
        "risk_flags": ["circle_geometry_needs_business_mapping"],
    }


def _hatch_candidate(source_file: str, line_number: int, groups: dict[str, list[str]]) -> dict[str, Any]:
    layer = _first(groups, "8")
    return {
        "source_file": source_file,
        "entity_type": "HATCH",
        "line_number": line_number,
        "layer": layer,
        "area": "",
        "length": "",
        "quantity_hint": _quantity_hint(layer) or "possible_area",
        "risk_flags": ["hatch_boundary_not_yet_calculated"],
    }


def _insert_candidate(source_file: str, line_number: int, groups: dict[str, list[str]]) -> dict[str, Any]:
    layer = _first(groups, "8")
    block_name = _first(groups, "2")
    return {
        "source_file": source_file,
        "entity_type": "INSERT",
        "line_number": line_number,
        "layer": layer,
        "block_name": block_name,
        "count": 1,
        "x": _float_or_none(_first(groups, "10")),
        "y": _float_or_none(_first(groups, "20")),
        "quantity_hint": _quantity_hint(" ".join([layer, block_name])) or "possible_count",
        "risk_flags": _insert_risks(block_name),
    }


def _dimension_candidate(source_file: str, line_number: int, groups: dict[str, list[str]]) -> dict[str, Any]:
    layer = _first(groups, "8")
    measurement = _first(groups, "42") or _first(groups, "1")
    return {
        "source_file": source_file,
        "entity_type": "DIMENSION",
        "line_number": line_number,
        "layer": layer,
        "measurement": measurement,
        "quantity_hint": "possible_dimension_reference",
        "risk_flags": ["dimension_needs_association_to_geometry"],
    }


def _vertices_from_groups(groups: dict[str, list[str]]) -> list[tuple[float, float]]:
    xs = [_float_or_none(value) for value in groups.get("10", [])]
    ys = [_float_or_none(value) for value in groups.get("20", [])]
    return [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]


def _polyline_length(vertices: list[tuple[float, float]], *, is_closed: bool) -> float:
    if len(vertices) < 2:
        return 0.0
    pairs = list(zip(vertices, vertices[1:]))
    if is_closed and len(vertices) > 2:
        pairs.append((vertices[-1], vertices[0]))
    return sum(math.dist(start, end) for start, end in pairs)


def _shoelace_area(vertices: list[tuple[float, float]]) -> float:
    if len(vertices) < 3:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(vertices, [*vertices[1:], vertices[0]]):
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _bbox(vertices: list[tuple[float, float]]) -> dict[str, float] | None:
    if not vertices:
        return None
    xs = [point[0] for point in vertices]
    ys = [point[1] for point in vertices]
    return {
        "min_x": _round(min(xs)),
        "min_y": _round(min(ys)),
        "max_x": _round(max(xs)),
        "max_y": _round(max(ys)),
    }


def _quantity_hint(text: str) -> str:
    if any(keyword in text for keyword in AREA_LAYER_KEYWORDS):
        return "possible_area"
    if any(keyword in text for keyword in LENGTH_LAYER_KEYWORDS):
        return "possible_length"
    if any(keyword in text for keyword in COUNT_LAYER_KEYWORDS):
        return "possible_count"
    return ""


def _geometry_risks(*, area: float, length: float, layer: str, is_closed: bool) -> list[str]:
    risks: list[str] = []
    if not layer:
        risks.append("missing_layer")
    if is_closed and area <= 0:
        risks.append("closed_geometry_zero_area")
    if length <= 0:
        risks.append("zero_length_geometry")
    if not _quantity_hint(layer):
        risks.append("layer_needs_business_mapping")
    return risks


def _insert_risks(block_name: str) -> list[str]:
    risks: list[str] = []
    if not block_name:
        risks.append("missing_block_name")
    if block_name.startswith("*"):
        risks.append("anonymous_block_needs_mapping")
    return risks


def _too_many_anonymous_blocks(candidates: list[dict[str, Any]]) -> bool:
    if not candidates:
        return False
    anonymous = sum(1 for item in candidates if str(item.get("block_name", "")).startswith("*"))
    return anonymous / len(candidates) > 0.5


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


def _int_or_zero(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _round(value: float) -> float:
    return round(value, 4)


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


def _append_limited(rows: list[dict[str, Any]], row: dict[str, Any] | None, limit: int) -> None:
    if row is not None and len(rows) < limit:
        rows.append(row)


def _counter_top(counter_or_dict: Counter[str] | dict[str, int], limit: int) -> list[dict[str, Any]]:
    counter = counter_or_dict if isinstance(counter_or_dict, Counter) else Counter(counter_or_dict)
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _append_unique(paths: list[Path], seen: set[str], path: Path) -> None:
    key = str(path.resolve()).lower() if path.exists() else str(path).lower()
    if key not in seen:
        seen.add(key)
        paths.append(path)
