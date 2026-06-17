from __future__ import annotations

import csv
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


CLOSED_REGION_HEADERS = [
    "闭合区编号",
    "识别项目编号",
    "项目名称",
    "单位",
    "材料编号",
    "材料文字",
    "来源文件",
    "材料文字X",
    "材料文字Y",
    "面积㎡",
    "建议工程量",
    "bbox",
    "水平边覆盖率",
    "竖向边覆盖率",
    "综合覆盖率",
    "材料文字到区域距离",
    "材料文字位置",
    "候选状态",
    "风险标记",
    "证据说明",
]

PROJECT_REGION_HEADERS = [
    "识别项目编号",
    "项目名称",
    "单位",
    "材料编号",
    "材料文字",
    "来源文件",
    "材料文字X",
    "材料文字Y",
    "命中线段数",
    "水平线段数",
    "竖向线段数",
    "闭合候选数量",
    "最佳闭合区编号",
    "建议工程量",
    "最佳闭合面积㎡",
    "最佳闭合bbox",
    "候选状态",
    "风险标记",
    "证据说明",
]

MIN_CLOSED_REGION_AREA_SQM = 2.0
MIN_SIDE_COVERAGE_RATIO = 0.75
DEFAULT_SNAP_TOLERANCE = 2.0
MAX_CANDIDATES_PER_PROJECT = 8


def build_floor_region_reconstruction_report(
    *,
    floor_layer_rescan_report: dict[str, Any],
    room_boundary_report: dict[str, Any] | None = None,
    area_to_square_meter_factor: float | None = None,
    snap_tolerance: float = DEFAULT_SNAP_TOLERANCE,
) -> dict[str, Any]:
    """Reconstruct conservative closed floor-region candidates from R3-3b line evidence."""
    conversion = _extract_area_factor(floor_layer_rescan_report, area_to_square_meter_factor)
    segment_rows = list(floor_layer_rescan_report.get("floor_segment_rows") or [])
    package_rows = list(floor_layer_rescan_report.get("floor_package_rows") or [])
    closed_rows: list[dict[str, Any]] = []
    project_rows: list[dict[str, Any]] = []
    sequence = 1
    for package in package_rows:
        project_result = _reconstruct_for_project(
            package,
            segment_rows,
            area_to_square_meter_factor=conversion,
            snap_tolerance=snap_tolerance,
            start_sequence=sequence,
        )
        sequence += len(project_result["closed_region_rows"])
        closed_rows.extend(project_result["closed_region_rows"])
        project_rows.append(project_result["project_region_row"])

    status_counts = Counter(row["候选状态"] for row in project_rows)
    risk_counts = Counter()
    for row in [*closed_rows, *project_rows]:
        for risk in _split_risks(row.get("风险标记")):
            risk_counts[risk] += 1
    ready_project_count = sum(1 for row in project_rows if row["候选状态"] == "已重构地面闭合区域候选，待人工确认/R4规则计算")
    ready_region_count = sum(1 for row in closed_rows if row["候选状态"] == "已重构地面闭合区域候选，待人工确认/R4规则计算")
    return {
        "ok": True,
        "phase": "BIZ-2x-R3-3c-floor-closed-region-reconstruction",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "floor_project_count": len(package_rows),
            "source_floor_segment_count": len(segment_rows),
            "closed_region_candidate_count": len(closed_rows),
            "ready_closed_region_candidate_count": ready_region_count,
            "small_closed_region_candidate_count": sum(
                1 for row in closed_rows if "closed_floor_region_area_too_small" in _split_risks(row.get("风险标记"))
            ),
            "project_ready_closed_region_count": ready_project_count,
            "project_blocked_count": len(project_rows) - ready_project_count,
            "project_blocked_no_closed_region_count": sum(
                1 for row in project_rows if row["候选状态"] == "地面线段未形成闭合区域候选，继续阻断"
            ),
            "project_blocked_small_region_count": sum(
                1 for row in project_rows if row["候选状态"] == "闭合区域面积过小，疑似图例/局部排版，继续阻断"
            ),
            "status_counts": dict(status_counts.most_common()),
            "risk_counts": dict(risk_counts.most_common()),
            "snap_tolerance": snap_tolerance,
            "min_closed_region_area_sqm": MIN_CLOSED_REGION_AREA_SQM,
            "min_side_coverage_ratio": MIN_SIDE_COVERAGE_RATIO,
            "room_boundary_available": bool((room_boundary_report or {}).get("room_rows")),
            "final_generation_status": (
                "ready_for_manual_review_not_final_quantity" if ready_project_count else "blocked_until_closed_floor_region_reconstruction"
            ),
            "next_step": "manual_review_ready_floor_regions_then_bind_gbt_floor_area_rule" if ready_project_count else "expand_floor_boundary_reconstruction_or_use_room_boundary",
        },
        "closed_region_rows": closed_rows,
        "project_region_rows": project_rows,
        "notes": [
            "R3-3c 只把线段闭合关系转换为可复核候选，不直接生成最终工程量。",
            "闭合区必须同时具备水平边和竖向边覆盖证据，且面积达到最小有效阈值。",
            "材料文字在闭合区外时会保留风险标记，后续需要人工确认材料归属。",
        ],
    }


def write_floor_region_reconstruction_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_R3_地面线段闭合区域重构_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    closed_csv_path = target_dir / f"{file_stem}_闭合区域候选.csv"
    project_csv_path = target_dir / f"{file_stem}_项目闭合区域绑定.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_floor_region_reconstruction_markdown(report), encoding="utf-8")
    _write_csv(closed_csv_path, report.get("closed_region_rows") or [], CLOSED_REGION_HEADERS)
    _write_csv(project_csv_path, report.get("project_region_rows") or [], PROJECT_REGION_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "closed_region_csv": str(closed_csv_path),
        "project_region_csv": str(project_csv_path),
    }


def build_floor_region_reconstruction_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x R3-3c 地面线段闭合区域重构报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 地面项目数：{summary.get('floor_project_count', 0)}",
        f"- 来源地面线段数：{summary.get('source_floor_segment_count', 0)}",
        f"- 闭合区域候选数：{summary.get('closed_region_candidate_count', 0)}",
        f"- 可复核闭合区域数：{summary.get('ready_closed_region_candidate_count', 0)}",
        f"- 可复核项目数：{summary.get('project_ready_closed_region_count', 0)}",
        f"- 阻断项目数：{summary.get('project_blocked_count', 0)}",
        f"- 状态分布：{summary.get('status_counts', {})}",
        f"- 风险分布：{summary.get('risk_counts', {})}",
        "",
        "## 项目闭合区域绑定",
        "",
        "| 项目编号 | 项目 | 材料编号 | 材料文字 | 线段数 | 闭合候选 | 最佳面积㎡ | 状态 | 风险 | 说明 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in (report.get("project_region_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("识别项目编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("材料编号")),
                    _md(row.get("材料文字")),
                    _md(row.get("命中线段数")),
                    _md(row.get("闭合候选数量")),
                    _md(row.get("最佳闭合面积㎡")),
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
            "- `建议工程量` 仅在闭合区域候选达到可复核条件时给出，仍需进入人工确认和 R4 标准规则绑定。",
        ]
    )
    return "\n".join(lines) + "\n"


def _reconstruct_for_project(
    package: dict[str, Any],
    segment_rows: list[dict[str, Any]],
    *,
    area_to_square_meter_factor: float,
    snap_tolerance: float,
    start_sequence: int,
) -> dict[str, Any]:
    x = _float_or_none(package.get("材料文字X"))
    y = _float_or_none(package.get("材料文字Y"))
    source_file = str(package.get("材料文字来源文件") or "")
    search_radius = _float_or_none(package.get("搜索半径")) or 800.0
    matched = _select_nearby_segments(segment_rows, source_file, x, y, search_radius)
    horizontal, vertical = _split_orthogonal_segments(matched, snap_tolerance=snap_tolerance)
    candidates = _build_rectangular_candidates(
        horizontal,
        vertical,
        material_x=x,
        material_y=y,
        area_to_square_meter_factor=area_to_square_meter_factor,
        snap_tolerance=snap_tolerance,
    )
    candidates = candidates[:MAX_CANDIDATES_PER_PROJECT]
    closed_rows = [
        _closed_region_row(start_sequence + index, package, candidate)
        for index, candidate in enumerate(candidates)
    ]
    best = candidates[0] if candidates else None
    best_region_id = closed_rows[0]["闭合区编号"] if closed_rows else ""
    project_row = _project_region_row(package, matched, horizontal, vertical, candidates, best, best_region_id)
    return {"closed_region_rows": closed_rows, "project_region_row": project_row}


def _select_nearby_segments(
    segment_rows: list[dict[str, Any]],
    source_file: str,
    x: float | None,
    y: float | None,
    search_radius: float,
) -> list[dict[str, Any]]:
    if x is None or y is None or not source_file:
        return []
    matched: list[dict[str, Any]] = []
    for row in segment_rows:
        if row.get("来源文件") != source_file:
            continue
        x1 = _float_or_none(row.get("X1"))
        y1 = _float_or_none(row.get("Y1"))
        x2 = _float_or_none(row.get("X2"))
        y2 = _float_or_none(row.get("Y2"))
        if None in {x1, y1, x2, y2}:
            continue
        midpoint = ((x1 + x2) / 2, (y1 + y2) / 2)
        if math.dist((x, y), midpoint) <= search_radius:
            matched.append(row)
    return matched


def _split_orthogonal_segments(rows: list[dict[str, Any]], *, snap_tolerance: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    horizontal: list[dict[str, Any]] = []
    vertical: list[dict[str, Any]] = []
    for row in rows:
        x1 = _float_or_none(row.get("X1"))
        y1 = _float_or_none(row.get("Y1"))
        x2 = _float_or_none(row.get("X2"))
        y2 = _float_or_none(row.get("Y2"))
        if None in {x1, y1, x2, y2}:
            continue
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx <= 0 and dy <= 0:
            continue
        if dy <= snap_tolerance and dx > 0:
            horizontal.append({"y": (y1 + y2) / 2, "start": min(x1, x2), "end": max(x1, x2), "row": row})
        elif dx <= snap_tolerance and dy > 0:
            vertical.append({"x": (x1 + x2) / 2, "start": min(y1, y2), "end": max(y1, y2), "row": row})
    return horizontal, vertical


def _build_rectangular_candidates(
    horizontal: list[dict[str, Any]],
    vertical: list[dict[str, Any]],
    *,
    material_x: float | None,
    material_y: float | None,
    area_to_square_meter_factor: float,
    snap_tolerance: float,
) -> list[dict[str, Any]]:
    if not horizontal or not vertical:
        return []
    xs = _snap_values([item["x"] for item in vertical], snap_tolerance)
    ys = _snap_values([item["y"] for item in horizontal], snap_tolerance)
    candidates: list[dict[str, Any]] = []
    for left_index, left in enumerate(xs):
        for right in xs[left_index + 1 :]:
            width = right - left
            if width <= snap_tolerance:
                continue
            for bottom_index, bottom in enumerate(ys):
                for top in ys[bottom_index + 1 :]:
                    height = top - bottom
                    if height <= snap_tolerance:
                        continue
                    top_coverage = _covered_ratio(horizontal, top, left, right, snap_tolerance)
                    bottom_coverage = _covered_ratio(horizontal, bottom, left, right, snap_tolerance)
                    left_coverage = _covered_ratio(vertical, left, bottom, top, snap_tolerance)
                    right_coverage = _covered_ratio(vertical, right, bottom, top, snap_tolerance)
                    coverage = min(top_coverage, bottom_coverage, left_coverage, right_coverage)
                    if coverage < MIN_SIDE_COVERAGE_RATIO:
                        continue
                    bbox = {"min_x": round(left, 4), "min_y": round(bottom, 4), "max_x": round(right, 4), "max_y": round(top, 4)}
                    area_sqm = (width * height) * area_to_square_meter_factor
                    material_distance = _distance_to_bbox(material_x, material_y, bbox)
                    risks: list[str] = []
                    if area_sqm < MIN_CLOSED_REGION_AREA_SQM:
                        risks.append("closed_floor_region_area_too_small")
                    if material_distance > 0:
                        risks.append("material_text_outside_closed_region")
                    status = "已重构地面闭合区域候选，待人工确认/R4规则计算"
                    if area_sqm < MIN_CLOSED_REGION_AREA_SQM:
                        status = "闭合区域面积过小，疑似图例/局部排版，继续阻断"
                    candidates.append(
                        {
                            "bbox": bbox,
                            "area_sqm": round(area_sqm, 4),
                            "top_coverage": round(top_coverage, 3),
                            "bottom_coverage": round(bottom_coverage, 3),
                            "left_coverage": round(left_coverage, 3),
                            "right_coverage": round(right_coverage, 3),
                            "horizontal_coverage": round(min(top_coverage, bottom_coverage), 3),
                            "vertical_coverage": round(min(left_coverage, right_coverage), 3),
                            "coverage": round(coverage, 3),
                            "material_distance": round(material_distance, 2),
                            "material_position": "区域内" if material_distance == 0 else "区域外",
                            "status": status,
                            "risks": risks,
                        }
                    )
    return sorted(candidates, key=lambda item: (item["status"] != "已重构地面闭合区域候选，待人工确认/R4规则计算", item["material_distance"], -item["area_sqm"], -item["coverage"]))


def _covered_ratio(items: list[dict[str, Any]], axis_value: float, start: float, end: float, tolerance: float) -> float:
    intervals: list[tuple[float, float]] = []
    target_length = max(0.0, end - start)
    if target_length <= 0:
        return 0.0
    for item in items:
        coord = item.get("y") if "y" in item else item.get("x")
        if coord is None or abs(float(coord) - axis_value) > tolerance:
            continue
        overlap_start = max(start, float(item["start"]))
        overlap_end = min(end, float(item["end"]))
        if overlap_end > overlap_start:
            intervals.append((overlap_start, overlap_end))
    if not intervals:
        return 0.0
    intervals.sort()
    merged: list[list[float]] = []
    for interval_start, interval_end in intervals:
        if not merged or interval_start > merged[-1][1] + tolerance:
            merged.append([interval_start, interval_end])
        else:
            merged[-1][1] = max(merged[-1][1], interval_end)
    covered = sum(interval_end - interval_start for interval_start, interval_end in merged)
    return min(1.0, covered / target_length)


def _snap_values(values: list[float], tolerance: float) -> list[float]:
    if not values:
        return []
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or abs(value - (sum(groups[-1]) / len(groups[-1]))) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def _closed_region_row(sequence: int, package: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    region_id = f"BIZ2xF-R{sequence:05d}"
    quantity = candidate["area_sqm"] if candidate["status"] == "已重构地面闭合区域候选，待人工确认/R4规则计算" else ""
    return {
        "闭合区编号": region_id,
        "识别项目编号": package.get("识别项目编号", ""),
        "项目名称": package.get("项目名称", ""),
        "单位": package.get("单位", ""),
        "材料编号": package.get("材料编号", ""),
        "材料文字": package.get("材料文字", ""),
        "来源文件": package.get("材料文字来源文件", ""),
        "材料文字X": package.get("材料文字X", ""),
        "材料文字Y": package.get("材料文字Y", ""),
        "面积㎡": candidate["area_sqm"],
        "建议工程量": quantity,
        "bbox": json.dumps(candidate["bbox"], ensure_ascii=False),
        "水平边覆盖率": candidate["horizontal_coverage"],
        "竖向边覆盖率": candidate["vertical_coverage"],
        "综合覆盖率": candidate["coverage"],
        "材料文字到区域距离": candidate["material_distance"],
        "材料文字位置": candidate["material_position"],
        "候选状态": candidate["status"],
        "风险标记": "；".join(candidate["risks"]),
        "证据说明": f"水平/竖向边覆盖率 {candidate['coverage']:.3f}，由地面线段端点连通关系重构。",
    }


def _project_region_row(
    package: dict[str, Any],
    matched: list[dict[str, Any]],
    horizontal: list[dict[str, Any]],
    vertical: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    best: dict[str, Any] | None,
    best_region_id: str,
) -> dict[str, Any]:
    base = {
        "识别项目编号": package.get("识别项目编号", ""),
        "项目名称": package.get("项目名称", ""),
        "单位": package.get("单位", ""),
        "材料编号": package.get("材料编号", ""),
        "材料文字": package.get("材料文字", ""),
        "来源文件": package.get("材料文字来源文件", ""),
        "材料文字X": package.get("材料文字X", ""),
        "材料文字Y": package.get("材料文字Y", ""),
        "命中线段数": len(matched),
        "水平线段数": len(horizontal),
        "竖向线段数": len(vertical),
        "闭合候选数量": len(candidates),
        "最佳闭合区编号": "",
        "建议工程量": "",
        "最佳闭合面积㎡": "",
        "最佳闭合bbox": "",
    }
    if best is None:
        status = "地面线段未形成闭合区域候选，继续阻断"
        risks = ["floor_segments_not_closed"]
        if not matched:
            risks.append("no_nearby_floor_segments")
        elif not horizontal or not vertical:
            risks.append("floor_segments_not_orthogonal")
        evidence = "材料文字附近地面线段未能形成水平/竖向边均覆盖的闭合区域。"
    else:
        status = best["status"]
        risks = list(best["risks"])
        base["最佳闭合区编号"] = best_region_id
        base["最佳闭合面积㎡"] = best["area_sqm"]
        base["最佳闭合bbox"] = json.dumps(best["bbox"], ensure_ascii=False)
        if status == "已重构地面闭合区域候选，待人工确认/R4规则计算":
            base["建议工程量"] = best["area_sqm"]
        evidence = f"已形成 {len(candidates)} 个闭合区域候选，最佳候选面积 {best['area_sqm']}㎡。"
    return {
        **base,
        "候选状态": status,
        "风险标记": "；".join(risks),
        "证据说明": evidence,
    }


def _extract_area_factor(report: dict[str, Any], explicit: float | None) -> float:
    if explicit:
        return float(explicit)
    inputs = report.get("inputs") or {}
    conversion = inputs.get("unit_conversion") or {}
    factor = conversion.get("area_to_square_meter_factor")
    if factor:
        return float(factor)
    return 0.000001


def _distance_to_bbox(x: float | None, y: float | None, bbox: dict[str, Any]) -> float:
    if x is None or y is None:
        return 0.0
    min_x = float(bbox["min_x"])
    min_y = float(bbox["min_y"])
    max_x = float(bbox["max_x"])
    max_y = float(bbox["max_y"])
    dx = max(min_x - x, 0, x - max_x)
    dy = max(min_y - y, 0, y - max_y)
    return math.hypot(dx, dy)


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _split_risks(value: Any) -> list[str]:
    return [part for part in str(value or "").split("；") if part]


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{header: row.get(header, "") for header in headers} for row in rows])
