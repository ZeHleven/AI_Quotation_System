from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOM_ROW_HEADERS = [
    "房间编号",
    "房间/空间名称",
    "绑定区域编号",
    "来源文件",
    "CAD面积",
    "CAD周长",
    "净周长候选",
    "门洞/开口候选数量",
    "门洞/开口扣减长度候选",
    "净周长状态",
    "面积单位",
    "长度单位",
    "区域文字证据",
    "房间边界证据",
    "后续可用于项目",
    "风险提示",
]

OPENING_ROW_HEADERS = [
    "房间编号",
    "房间/空间名称",
    "区域编号",
    "开口候选编号",
    "候选类型",
    "来源文件",
    "图层",
    "块名",
    "实体类型",
    "源行号",
    "坐标或边界",
    "扣减长度候选",
    "匹配状态",
    "匹配置信度",
    "匹配说明",
]

ROOM_NOISE_TERMS = ("图例", "节点", "大样", "目录", "材料表", "构造做法", "门洞砌墙示意图", "家具", "furniture")
OPENING_KEYWORDS = ("门", "门洞", "开口", "洞口", "平面门", "门套")
OPENING_EXCLUDE_TERMS = ("门槛石", "门拉手", "门牌", "门头", "门口石材")
ROOM_PROJECT_USAGE = "地面面积；吊顶/天棚面积；墙面防水周长；踢脚线净周长；墙面/顶面涂料专项算量"


def build_room_boundary_analysis_report(
    *,
    region_label_report: dict[str, Any],
    geometry_report: dict[str, Any],
    unit_conversion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversion = unit_conversion or {}
    unit_to_meter_factor = float(conversion.get("unit_to_meter_factor") or 0.001)
    room_regions = _collect_room_regions(region_label_report)
    opening_candidates = _collect_opening_candidates(geometry_report, unit_to_meter_factor=unit_to_meter_factor)

    room_rows: list[dict[str, Any]] = []
    opening_rows: list[dict[str, Any]] = []
    for index, room in enumerate(room_regions, start=1):
        room_id = f"BIZ2xROOM-{index:05d}"
        matches = _match_openings_to_room(room, opening_candidates)
        room_rows.append(_room_row(room_id, room, matches))
        for match_index, matched in enumerate(matches, start=1):
            opening_rows.append(_opening_row(room_id, room, matched, match_index))

    status_counts = Counter(row["净周长状态"] for row in room_rows)
    return {
        "ok": True,
        "phase": "BIZ-2x-room-boundary-net-perimeter",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "room_boundary_count": len(room_rows),
            "opening_candidate_count": len(opening_rows),
            "room_with_opening_candidate_count": sum(1 for row in room_rows if int(row["门洞/开口候选数量"] or 0) > 0),
            "room_with_net_perimeter_candidate_count": sum(
                1 for row in room_rows if row["净周长状态"] in {"未识别门洞，暂按区域周长候选", "已按开口候选扣减，需复核"}
            ),
            "net_perimeter_blocked_count": sum(1 for row in room_rows if row["净周长状态"] == "存在开口但缺少宽度证据"),
            "status_counts": dict(status_counts.most_common()),
            "unit_conversion": {
                "unit_to_meter_factor": unit_to_meter_factor,
                "area_to_square_meter_factor": conversion.get("area_to_square_meter_factor"),
            },
            "final_generation_status": "blocked_until_special_quantity_calculator",
            "next_step": "apply_waterproof_baseboard_floor_ceiling_quantity_rules",
        },
        "room_rows": room_rows,
        "opening_candidate_rows": opening_rows,
        "notes": [
            "本报告把有房间/空间文字证据的闭合区域整理为房间边界候选，不直接生成最终工程量。",
            "净周长候选只有在没有识别到开口，或开口宽度已有证据时才给出；缺少宽度证据时必须继续确认。",
            "后续墙面防水、踢脚线等项目必须引用本报告的房间编号、区域编号和扣减证据，再按 GB/T 标准规则计算。",
        ],
    }


def write_room_boundary_analysis_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_房间边界净周长_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    room_csv_path = target_dir / f"{file_stem}_房间边界净周长.csv"
    opening_csv_path = target_dir / f"{file_stem}_门洞开口候选.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_room_boundary_analysis_markdown(report), encoding="utf-8")
    _write_csv(room_csv_path, report.get("room_rows") or [], ROOM_ROW_HEADERS)
    _write_csv(opening_csv_path, report.get("opening_candidate_rows") or [], OPENING_ROW_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "room_csv": str(room_csv_path),
        "opening_csv": str(opening_csv_path),
    }


def build_room_boundary_analysis_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x 房间边界与净周长报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 房间边界候选：{summary.get('room_boundary_count', 0)}",
        f"- 门洞/开口候选：{summary.get('opening_candidate_count', 0)}",
        f"- 可给净周长候选房间：{summary.get('room_with_net_perimeter_candidate_count', 0)}",
        f"- 净周长阻断房间：{summary.get('net_perimeter_blocked_count', 0)}",
        "",
        "## 房间边界",
        "",
        "| 房间编号 | 房间/空间 | 区域编号 | 面积 | 周长 | 净周长候选 | 开口数 | 状态 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in (report.get("room_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("房间编号")),
                    _md(row.get("房间/空间名称")),
                    _md(row.get("绑定区域编号")),
                    _md(row.get("CAD面积")),
                    _md(row.get("CAD周长")),
                    _md(row.get("净周长候选")),
                    _md(row.get("门洞/开口候选数量")),
                    _md(row.get("净周长状态")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告只提供房间级几何证据，不把净周长直接作为最终工程量。",
            "- 门洞/开口宽度缺失时，踢脚线和墙面防水不得自动扣减。",
        ]
    )
    return "\n".join(lines) + "\n"


def _collect_room_regions(region_label_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = region_label_report.get("region_rows") or region_label_report.get("region_index_rows") or []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        room_name = str(row.get("房间/空间标签") or "").strip()
        evidence_text = _region_evidence_text(row)
        if not room_name:
            continue
        if _is_noise_room(evidence_text):
            continue
        area = _float_or_none(row.get("CAD面积"))
        perimeter = _float_or_none(row.get("CAD周长"))
        bbox = _parse_bbox(row.get("区域边界"))
        if area is None or perimeter is None or not _valid_bbox(bbox):
            continue
        region_id = str(row.get("区域编号") or "")
        if region_id and region_id in seen:
            continue
        seen.add(region_id)
        result.append(
            {
                "region_id": region_id,
                "room_name": room_name,
                "source_file": str(row.get("来源文件") or ""),
                "area": round(area, 4),
                "perimeter": round(perimeter, 4),
                "bbox": bbox,
                "layer": str(row.get("图层") or ""),
                "entity_type": str(row.get("实体类型") or ""),
                "line_number": row.get("源行号", ""),
                "evidence_text": evidence_text,
            }
        )
    return result


def _collect_opening_candidates(geometry_report: dict[str, Any], *, unit_to_meter_factor: float) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    sequence = 1
    for file_item in geometry_report.get("files") or []:
        source_file = str(file_item.get("file_name") or "")
        for raw in file_item.get("count_candidates") or []:
            text = _normalize(" ".join([str(raw.get("layer") or ""), str(raw.get("block_name") or ""), str(raw.get("quantity_hint") or "")]))
            if not _looks_like_opening(text):
                continue
            x = _float_or_none(raw.get("x"))
            y = _float_or_none(raw.get("y"))
            if x is None or y is None:
                continue
            width = _parse_opening_width_m(text)
            candidates.append(
                {
                    "opening_id": f"BIZ2xOPEN-{sequence:05d}",
                    "candidate_type": "门洞/开口图块候选",
                    "source_file": source_file,
                    "layer": str(raw.get("layer") or ""),
                    "block_name": str(raw.get("block_name") or ""),
                    "entity_type": str(raw.get("entity_type") or "INSERT"),
                    "line_number": raw.get("line_number", ""),
                    "point": {"x": x, "y": y},
                    "bbox": None,
                    "deduction_length": width,
                    "raw": raw,
                }
            )
            sequence += 1
        for raw in file_item.get("length_candidates") or []:
            text = _normalize(" ".join([str(raw.get("layer") or ""), str(raw.get("block_name") or ""), str(raw.get("quantity_hint") or "")]))
            if not _looks_like_opening(text):
                continue
            bbox = raw.get("bbox") or {}
            length = _float_or_none(raw.get("length"))
            if length is None or not _valid_bbox(bbox):
                continue
            length_m = round(length * unit_to_meter_factor, 4)
            if not 0.4 <= length_m <= 2.5:
                continue
            candidates.append(
                {
                    "opening_id": f"BIZ2xOPEN-{sequence:05d}",
                    "candidate_type": "门洞/开口线段候选",
                    "source_file": source_file,
                    "layer": str(raw.get("layer") or ""),
                    "block_name": str(raw.get("block_name") or ""),
                    "entity_type": str(raw.get("entity_type") or ""),
                    "line_number": raw.get("line_number", ""),
                    "point": _bbox_center(bbox),
                    "bbox": bbox,
                    "deduction_length": length_m,
                    "raw": raw,
                }
            )
            sequence += 1
    return candidates


def _match_openings_to_room(room: dict[str, Any], opening_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    bbox = room["bbox"]
    for candidate in opening_candidates:
        if candidate["source_file"] != room["source_file"]:
            continue
        point = candidate.get("point") or {}
        x = _float_or_none(point.get("x"))
        y = _float_or_none(point.get("y"))
        if x is None or y is None:
            continue
        distance = _point_bbox_boundary_distance(x, y, bbox)
        if distance is None:
            continue
        threshold = _boundary_threshold(bbox)
        inside_or_near = _point_in_expanded_bbox(x, y, bbox, threshold)
        if distance <= threshold and inside_or_near:
            score = 8.0 if distance <= threshold / 2 else 6.0
            reasons = ["门洞/开口候选靠近房间边界"]
            if candidate.get("deduction_length"):
                score += 1.0
                reasons.append("已识别扣减长度候选")
            matches.append(
                {
                    "candidate": candidate,
                    "score": round(score, 2),
                    "distance": round(distance, 2),
                    "reasons": reasons,
                }
            )
    matches.sort(key=lambda item: (-item["score"], item["distance"], item["candidate"]["opening_id"]))
    return matches[:12]


def _room_row(room_id: str, room: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
    known_deductions = [float(item["candidate"]["deduction_length"]) for item in matches if item["candidate"].get("deduction_length")]
    unknown_openings = len(matches) - len(known_deductions)
    deduction_sum = round(sum(known_deductions), 4)
    if matches and unknown_openings:
        net_perimeter = ""
        status = "存在开口但缺少宽度证据"
        risk = "门洞/开口候选缺少宽度，暂不能自动扣减净周长"
    elif matches:
        net_perimeter = round(max(0.0, float(room["perimeter"]) - deduction_sum), 4)
        status = "已按开口候选扣减，需复核"
        risk = "已形成净周长候选，但门洞识别和扣减规则仍需复核"
    else:
        net_perimeter = room["perimeter"]
        status = "未识别门洞，暂按区域周长候选"
        risk = "未识别门洞/开口，不代表实际无门洞"
    return {
        "房间编号": room_id,
        "房间/空间名称": room["room_name"],
        "绑定区域编号": room["region_id"],
        "来源文件": room["source_file"],
        "CAD面积": room["area"],
        "CAD周长": room["perimeter"],
        "净周长候选": net_perimeter,
        "门洞/开口候选数量": len(matches),
        "门洞/开口扣减长度候选": deduction_sum if deduction_sum else "",
        "净周长状态": status,
        "面积单位": "㎡",
        "长度单位": "m",
        "区域文字证据": room["evidence_text"],
        "房间边界证据": f"区域 {room['region_id']}，图层 {room['layer']}，实体 {room['entity_type']}，源行号 {room['line_number']}",
        "后续可用于项目": ROOM_PROJECT_USAGE,
        "风险提示": risk,
    }


def _opening_row(room_id: str, room: dict[str, Any], matched: dict[str, Any], index: int) -> dict[str, Any]:
    candidate = matched["candidate"]
    coordinate = candidate.get("bbox") or candidate.get("point") or {}
    return {
        "房间编号": room_id,
        "房间/空间名称": room["room_name"],
        "区域编号": room["region_id"],
        "开口候选编号": candidate.get("opening_id") or f"{room_id}-OPEN-{index:02d}",
        "候选类型": candidate.get("candidate_type", ""),
        "来源文件": candidate.get("source_file", ""),
        "图层": candidate.get("layer", ""),
        "块名": candidate.get("block_name", ""),
        "实体类型": candidate.get("entity_type", ""),
        "源行号": candidate.get("line_number", ""),
        "坐标或边界": json.dumps(coordinate, ensure_ascii=False),
        "扣减长度候选": candidate.get("deduction_length") or "",
        "匹配状态": "建议作为门洞/开口扣减候选",
        "匹配置信度": _confidence(matched["score"]),
        "匹配说明": "；".join(matched.get("reasons") or []),
    }


def _region_evidence_text(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("区域内文字") or ""),
        str(row.get("附近文字") or ""),
        str(row.get("房间/空间标签") or ""),
        str(row.get("项目标签") or ""),
        str(row.get("区域类型建议") or ""),
    ]
    return "；".join(part for part in parts if part)


def _looks_like_opening(text: str) -> bool:
    return any(term in text for term in OPENING_KEYWORDS) and not any(term in text for term in OPENING_EXCLUDE_TERMS)


def _is_noise_room(text: str) -> bool:
    normalized = _normalize(text)
    return any(term in normalized for term in ROOM_NOISE_TERMS)


def _parse_opening_width_m(text: str) -> float | None:
    for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", text):
        value = _float_or_none(match.group(1))
        if value is not None and 500 <= value <= 1800:
            return round(value / 1000, 4)
    for match in re.finditer(r"(?<!\d)(\d(?:\.\d+)?)(?:m|米)(?!\d)", text):
        value = _float_or_none(match.group(1))
        if value is not None and 0.5 <= value <= 1.8:
            return round(value, 4)
    return None


def _point_bbox_boundary_distance(x: float, y: float, bbox: dict[str, float]) -> float | None:
    if not _valid_bbox(bbox):
        return None
    min_x = float(bbox["min_x"])
    min_y = float(bbox["min_y"])
    max_x = float(bbox["max_x"])
    max_y = float(bbox["max_y"])
    if min_x <= x <= max_x and min_y <= y <= max_y:
        return min(x - min_x, max_x - x, y - min_y, max_y - y)
    dx = max(min_x - x, 0.0, x - max_x)
    dy = max(min_y - y, 0.0, y - max_y)
    return math.hypot(dx, dy)


def _point_in_expanded_bbox(x: float, y: float, bbox: dict[str, float], threshold: float) -> bool:
    return (
        float(bbox["min_x"]) - threshold
        <= x
        <= float(bbox["max_x"]) + threshold
        and float(bbox["min_y"]) - threshold
        <= y
        <= float(bbox["max_y"]) + threshold
    )


def _boundary_threshold(bbox: dict[str, float]) -> float:
    width = abs(float(bbox["max_x"]) - float(bbox["min_x"]))
    height = abs(float(bbox["max_y"]) - float(bbox["min_y"]))
    return max(250.0, min(max(width, height) * 0.08, 1200.0))


def _bbox_center(bbox: dict[str, Any]) -> dict[str, float]:
    return {
        "x": (float(bbox["min_x"]) + float(bbox["max_x"])) / 2,
        "y": (float(bbox["min_y"]) + float(bbox["max_y"])) / 2,
    }


def _parse_bbox(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _valid_bbox(bbox: dict[str, Any]) -> bool:
    required = {"min_x", "min_y", "max_x", "max_y"}
    if not required.issubset(bbox):
        return False
    try:
        return float(bbox["max_x"]) > float(bbox["min_x"]) and float(bbox["max_y"]) > float(bbox["min_y"])
    except (TypeError, ValueError):
        return False


def _confidence(score: float) -> str:
    if score >= 8:
        return f"高({score})"
    if score >= 5:
        return f"中({score})"
    if score > 0:
        return f"低({score})"
    return "无"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{header: row.get(header, "") for header in headers} for row in rows])
