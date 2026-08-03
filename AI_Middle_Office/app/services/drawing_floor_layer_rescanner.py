from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app.services.dxf_geometry_probe import (
    detect_dxf_encoding,
    _bbox,
    _first,
    _float_or_none,
    _groups,
    _iter_dxf_pairs,
    _lwpolyline_candidate,
    _polyline_candidate,
)


FLOOR_SEGMENT_HEADERS = [
    "线段编号",
    "来源文件",
    "图层",
    "实体类型",
    "源行号",
    "X1",
    "Y1",
    "X2",
    "Y2",
    "长度",
    "长度m",
    "bbox",
    "风险标记",
]

FLOOR_PACKAGE_HEADERS = [
    "识别项目编号",
    "项目名称",
    "单位",
    "材料编号",
    "材料名称",
    "规格",
    "材料文字",
    "材料文字来源文件",
    "材料文字源行号",
    "材料文字X",
    "材料文字Y",
    "搜索半径",
    "命中线段数",
    "最近线段距离",
    "线段总长m",
    "包络面积",
    "包络bbox",
    "候选状态",
    "风险标记",
    "证据说明",
]

FLOOR_LAYER_TERMS = ("F-地面", "地面材料", "地面分界", "地面填充", "铺装")
EXCLUDE_LAYER_TERMS = ("灯具", "天花", "吊顶", "门", "窗", "电气", "插座")
MIN_PACKAGE_AREA_SQM = 2.0
DEFAULT_SEARCH_RADIUS = 800.0


def build_floor_layer_rescan_report(
    *,
    dxf_files: Iterable[str | Path],
    floor_paving_locator_report: dict[str, Any],
    unit_conversion: dict[str, Any] | None = None,
    search_radius: float = DEFAULT_SEARCH_RADIUS,
) -> dict[str, Any]:
    conversion = unit_conversion or {}
    unit_to_meter_factor = float(conversion.get("unit_to_meter_factor") or 0.001)
    area_to_square_meter_factor = float(conversion.get("area_to_square_meter_factor") or unit_to_meter_factor * unit_to_meter_factor)
    dxf_paths = [Path(path) for path in dxf_files]
    segment_rows = _collect_floor_segments(dxf_paths, unit_to_meter_factor)
    package_rows = _build_floor_packages(
        floor_paving_locator_report.get("floor_project_rows") or [],
        segment_rows,
        search_radius=search_radius,
        area_to_square_meter_factor=area_to_square_meter_factor,
    )
    status_counts = Counter(row["候选状态"] for row in package_rows)
    risk_counts = Counter()
    for row in [*segment_rows, *package_rows]:
        for risk in _split_risks(row.get("风险标记")):
            risk_counts[risk] += 1
    return {
        "ok": True,
        "phase": "BIZ-2x-R3-3b-floor-layer-targeted-rescan",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "dxf_file_count": len(dxf_paths),
            "floor_segment_count": len(segment_rows),
            "floor_package_count": len(package_rows),
            "ready_floor_package_count": sum(1 for row in package_rows if row["候选状态"] == "已生成地面线段包络候选，待人工确认/R4规则计算"),
            "small_or_layout_floor_package_count": sum(1 for row in package_rows if row["候选状态"] == "线段包络面积过小，疑似图例/局部排版，继续阻断"),
            "unbound_floor_package_count": sum(1 for row in package_rows if row["候选状态"] == "材料文字附近未命中地面线段，继续阻断"),
            "status_counts": dict(status_counts.most_common()),
            "risk_counts": dict(risk_counts.most_common()),
            "search_radius": search_radius,
            "final_generation_status": "blocked_until_floor_line_packages_are_reviewed_or_closed_regions_reconstructed",
            "next_step": "review_floor_line_packages_then_reconstruct_closed_floor_regions_or_expand_floor_boundary_parser",
        },
        "floor_segment_rows": segment_rows,
        "floor_package_rows": package_rows,
        "notes": [
            "R3-3b 只定向重扫地面图层线段/闭合线，不修改通用 CAD 几何探测上限。",
            "线段包络候选用于判断材料文字附近是否存在真实地面边界，不直接生成最终工程量。",
            "包络面积过小或线段过少时视为图例/局部排版风险，继续阻断自动算量。",
        ],
    }


def write_floor_layer_rescan_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_R3_地面图层定向重扫_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    segment_csv_path = target_dir / f"{file_stem}_地面线段.csv"
    package_csv_path = target_dir / f"{file_stem}_材料文字线段包络.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_floor_layer_rescan_markdown(report), encoding="utf-8")
    _write_csv(segment_csv_path, report.get("floor_segment_rows") or [], FLOOR_SEGMENT_HEADERS)
    _write_csv(package_csv_path, report.get("floor_package_rows") or [], FLOOR_PACKAGE_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "segment_csv": str(segment_csv_path),
        "package_csv": str(package_csv_path),
    }


def build_floor_layer_rescan_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x R3-3b 地面图层定向重扫报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- DXF 文件数：{summary.get('dxf_file_count', 0)}",
        f"- 地面线段数：{summary.get('floor_segment_count', 0)}",
        f"- 材料文字线段包络数：{summary.get('floor_package_count', 0)}",
        f"- 可复核包络数：{summary.get('ready_floor_package_count', 0)}",
        f"- 小面积/排版风险包络数：{summary.get('small_or_layout_floor_package_count', 0)}",
        f"- 未命中线段包络数：{summary.get('unbound_floor_package_count', 0)}",
        f"- 状态分布：{summary.get('status_counts', {})}",
        f"- 风险分布：{summary.get('risk_counts', {})}",
        "",
        "## 材料文字线段包络",
        "",
        "| 项目编号 | 项目 | 材料编号 | 材料文字 | 文件 | 线段数 | 最近距离 | 包络面积 | 状态 | 风险 | 证据 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in (report.get("floor_package_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("识别项目编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("材料编号")),
                    _md(row.get("材料文字")),
                    _md(row.get("材料文字来源文件")),
                    _md(row.get("命中线段数")),
                    _md(row.get("最近线段距离")),
                    _md(row.get("包络面积")),
                    _md(row.get("候选状态")),
                    _md(row.get("风险标记")),
                    _md(row.get("证据说明")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告不是最终工程量清单。",
            "- `包络面积` 是材料文字附近线段的外接矩形面积，只用于复核和后续闭合区域重构，不能直接作为 GB/T 工程量。",
        ]
    )
    return "\n".join(lines) + "\n"


def _collect_floor_segments(dxf_paths: list[Path], unit_to_meter_factor: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sequence = 1
    for path in dxf_paths:
        if not path.exists() or not path.is_file():
            continue
        encoding = detect_dxf_encoding(path)
        current_type = ""
        current_line = 0
        current_pairs: list[tuple[str, str]] = []
        active_polyline: dict[str, Any] | None = None

        def finish_record() -> None:
            nonlocal sequence, active_polyline
            if not current_type:
                return
            groups = _groups(current_pairs)
            layer = _first(groups, "8")
            if current_type == "LINE":
                if not _is_floor_boundary_layer(layer):
                    return
                x1 = _float_or_none(_first(groups, "10"))
                y1 = _float_or_none(_first(groups, "20"))
                x2 = _float_or_none(_first(groups, "11"))
                y2 = _float_or_none(_first(groups, "21"))
                if None in {x1, y1, x2, y2}:
                    return
                rows.append(_segment_row(sequence, path.name, layer, "LINE", current_line, x1, y1, x2, y2, unit_to_meter_factor))
                sequence += 1
            elif current_type == "LWPOLYLINE" and _is_floor_boundary_layer(layer):
                candidate = _lwpolyline_candidate(path.name, current_line, groups)
                new_rows = _polyline_segment_rows(sequence, path.name, layer, candidate, unit_to_meter_factor)
                rows.extend(new_rows)
                sequence += len(new_rows)
            elif current_type == "POLYLINE":
                active_polyline = {
                    "source_file": path.name,
                    "entity_type": "POLYLINE",
                    "line_number": current_line,
                    "layer": layer,
                    "flags": _int_or_zero(_first(groups, "70")),
                    "vertices": [],
                }
            elif current_type == "VERTEX" and active_polyline is not None:
                x = _float_or_none(_first(groups, "10"))
                y = _float_or_none(_first(groups, "20"))
                if x is not None and y is not None:
                    active_polyline["vertices"].append((x, y))
            elif current_type == "SEQEND" and active_polyline is not None:
                layer = str(active_polyline.get("layer") or "")
                if _is_floor_boundary_layer(layer):
                    candidate = _polyline_candidate(active_polyline)
                    new_rows = _polyline_segment_rows(sequence, path.name, layer, candidate, unit_to_meter_factor)
                    rows.extend(new_rows)
                    sequence += len(new_rows)
                active_polyline = None

        for code, value, line_number in _iter_dxf_pairs(path, encoding):
            clean_code = code.strip()
            clean_value = value.strip()
            if clean_code == "0":
                finish_record()
                current_type = ""
                current_pairs = []
                current_line = 0
                if clean_value in {"SECTION", "ENDSEC", "EOF", "ENDTAB"}:
                    continue
                current_type = clean_value
                current_line = line_number
                continue
            if current_type:
                current_pairs.append((clean_code, value.rstrip("\r\n")))
        finish_record()
    return rows


def _segment_row(
    sequence: int,
    source_file: str,
    layer: str,
    entity_type: str,
    line_number: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    unit_to_meter_factor: float,
) -> dict[str, Any]:
    length = math.dist((x1, y1), (x2, y2))
    bbox = _bbox([(x1, y1), (x2, y2)]) or {}
    risks: list[str] = []
    if length <= 0:
        risks.append("zero_length_floor_segment")
    return {
        "线段编号": f"BIZ2xF-L{sequence:05d}",
        "来源文件": source_file,
        "图层": layer,
        "实体类型": entity_type,
        "源行号": line_number,
        "X1": round(x1, 4),
        "Y1": round(y1, 4),
        "X2": round(x2, 4),
        "Y2": round(y2, 4),
        "长度": round(length, 4),
        "长度m": round(length * unit_to_meter_factor, 4),
        "bbox": json.dumps(bbox, ensure_ascii=False),
        "风险标记": "；".join(risks),
        "_mx": (x1 + x2) / 2,
        "_my": (y1 + y2) / 2,
        "_points": [(x1, y1), (x2, y2)],
    }


def _polyline_segment_rows(
    sequence: int,
    source_file: str,
    layer: str,
    candidate: dict[str, Any],
    unit_to_meter_factor: float,
) -> list[dict[str, Any]]:
    # Existing probe candidates do not expose vertices, so keep LWPOLYLINE/POLYLINE as bbox diagonals.
    bbox_text = candidate.get("bbox") or {}
    if not _valid_bbox(bbox_text):
        return []
    x1 = float(bbox_text["min_x"])
    y1 = float(bbox_text["min_y"])
    x2 = float(bbox_text["max_x"])
    y2 = float(bbox_text["max_y"])
    return [_segment_row(sequence, source_file, layer, candidate.get("entity_type", "POLYLINE"), candidate.get("line_number", 0), x1, y1, x2, y2, unit_to_meter_factor)]


def _build_floor_packages(
    floor_project_rows: list[dict[str, Any]],
    segment_rows: list[dict[str, Any]],
    *,
    search_radius: float,
    area_to_square_meter_factor: float,
) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for project in floor_project_rows:
        x = _float_or_none(project.get("材料文字X"))
        y = _float_or_none(project.get("材料文字Y"))
        source_file = str(project.get("材料文字来源文件") or "")
        if x is None or y is None or not source_file:
            packages.append(_empty_package(project, search_radius, "缺少材料文字坐标，继续阻断", ["missing_material_text_point"]))
            continue
        matched = [
            row
            for row in segment_rows
            if row.get("来源文件") == source_file
            and _distance_to_segment_midpoint(x, y, row) <= search_radius
            and "zero_length_floor_segment" not in _split_risks(row.get("风险标记"))
        ]
        if not matched:
            packages.append(_empty_package(project, search_radius, "材料文字附近未命中地面线段，继续阻断", ["no_nearby_floor_segments"]))
            continue
        points = [point for row in matched for point in row.get("_points", [])]
        bbox = _bbox(points) or {}
        bbox_area = _bbox_area(bbox) * area_to_square_meter_factor if bbox else 0.0
        total_length = sum(float(row.get("长度m") or 0) for row in matched)
        nearest = min(_distance_to_segment_midpoint(x, y, row) for row in matched)
        risks: list[str] = []
        if bbox_area < MIN_PACKAGE_AREA_SQM:
            risks.append("floor_line_package_area_too_small")
        if len(matched) < 8:
            risks.append("floor_line_package_segment_count_low")
        status = "已生成地面线段包络候选，待人工确认/R4规则计算"
        if risks:
            status = "线段包络面积过小，疑似图例/局部排版，继续阻断"
        packages.append(
            {
                "识别项目编号": project.get("识别项目编号", ""),
                "项目名称": project.get("项目名称", ""),
                "单位": project.get("单位", ""),
                "材料编号": project.get("材料编号", ""),
                "材料名称": project.get("材料名称", ""),
                "规格": project.get("规格", ""),
                "材料文字": project.get("材料文字", ""),
                "材料文字来源文件": source_file,
                "材料文字源行号": project.get("材料文字源行号", ""),
                "材料文字X": project.get("材料文字X", ""),
                "材料文字Y": project.get("材料文字Y", ""),
                "搜索半径": search_radius,
                "命中线段数": len(matched),
                "最近线段距离": round(nearest, 2),
                "线段总长m": round(total_length, 4),
                "包络面积": round(bbox_area, 4),
                "包络bbox": json.dumps(bbox, ensure_ascii=False),
                "候选状态": status,
                "风险标记": "；".join(risks),
                "证据说明": f"材料文字 {project.get('材料文字', '')} 附近 {search_radius:g} CAD 单位内命中 {len(matched)} 条地面线段",
            }
        )
    return packages


def _empty_package(project: dict[str, Any], search_radius: float, status: str, risks: list[str]) -> dict[str, Any]:
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "材料编号": project.get("材料编号", ""),
        "材料名称": project.get("材料名称", ""),
        "规格": project.get("规格", ""),
        "材料文字": project.get("材料文字", ""),
        "材料文字来源文件": project.get("材料文字来源文件", ""),
        "材料文字源行号": project.get("材料文字源行号", ""),
        "材料文字X": project.get("材料文字X", ""),
        "材料文字Y": project.get("材料文字Y", ""),
        "搜索半径": search_radius,
        "命中线段数": 0,
        "最近线段距离": "",
        "线段总长m": "",
        "包络面积": "",
        "包络bbox": "",
        "候选状态": status,
        "风险标记": "；".join(risks),
        "证据说明": status,
    }


def _is_floor_boundary_layer(layer: str) -> bool:
    if not layer:
        return False
    normalized = layer.lower()
    if any(term.lower() in normalized for term in EXCLUDE_LAYER_TERMS):
        return False
    return any(term.lower() in normalized for term in FLOOR_LAYER_TERMS)


def _distance_to_segment_midpoint(x: float, y: float, row: dict[str, Any]) -> float:
    return math.dist((x, y), (float(row.get("_mx") or 0), float(row.get("_my") or 0)))


def _bbox_area(bbox: dict[str, Any]) -> float:
    if not _valid_bbox(bbox):
        return 0.0
    return (float(bbox["max_x"]) - float(bbox["min_x"])) * (float(bbox["max_y"]) - float(bbox["min_y"]))


def _valid_bbox(bbox: dict[str, Any]) -> bool:
    required = {"min_x", "min_y", "max_x", "max_y"}
    if not required.issubset(bbox):
        return False
    values = [_float_or_none(str(bbox.get(key))) for key in required]
    if any(value is None for value in values):
        return False
    return float(bbox["max_x"]) >= float(bbox["min_x"]) and float(bbox["max_y"]) >= float(bbox["min_y"])


def _int_or_zero(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _split_risks(value: Any) -> list[str]:
    return [part for part in str(value or "").split("；") if part]


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{header: row.get(header, "") for header in headers} for row in rows])
