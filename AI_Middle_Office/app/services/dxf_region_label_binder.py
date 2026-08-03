from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


MIN_REGION_AREA_SQM = 0.5
MAX_TEXT_PER_REGION = 8

REGION_LABEL_HEADERS = [
    "区域编号",
    "来源文件",
    "CAD面积",
    "CAD周长",
    "面积单位",
    "长度单位",
    "图层",
    "实体类型",
    "源行号",
    "区域边界",
    "区域内文字",
    "附近文字",
    "房间/空间标签",
    "项目标签",
    "区域类型建议",
    "绑定置信度",
    "绑定状态",
    "证据说明",
]

ROOM_KEYWORDS = (
    "餐厅",
    "食堂",
    "洗手间",
    "卫生间",
    "厨房",
    "包间",
    "大厅",
    "前厅",
    "走道",
    "过道",
    "库房",
    "更衣",
    "操作间",
    "备餐",
    "办公室",
    "会议室",
)

PROJECT_KEYWORDS = (
    "吊顶",
    "天棚",
    "天花",
    "地面",
    "楼地面",
    "地砖",
    "地板",
    "墙面",
    "防水",
    "踢脚",
    "踢脚线",
    "乳胶漆",
    "涂料",
    "石膏板",
    "窗帘盒",
    "门",
    "窗",
)

NOISE_TEXT_TERMS = (
    "图号",
    "比例",
    "日期",
    "设计",
    "审核",
    "审定",
    "说明",
    "目录",
    "材料表",
    "构造做法",
    "节点",
    "大样",
)


def build_region_label_binding_report(
    *,
    geometry_report: dict[str, Any],
    parsed_text_files: Iterable[Any] | None = None,
    text_report: dict[str, Any] | None = None,
    unit_conversion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversion = unit_conversion or {}
    unit_to_meter_factor = float(conversion.get("unit_to_meter_factor") or 0.001)
    area_to_square_meter_factor = float(conversion.get("area_to_square_meter_factor") or unit_to_meter_factor * unit_to_meter_factor)
    text_by_file = _collect_text_records_by_file(parsed_text_files=parsed_text_files, text_report=text_report)

    region_rows: list[dict[str, Any]] = []
    sequence = 1
    for file_item in geometry_report.get("files") or []:
        source_file = str(file_item.get("file_name") or "")
        text_records = text_by_file.get(source_file, [])
        for raw in file_item.get("area_candidates") or []:
            raw_area = _float_or_none(raw.get("area"))
            bbox = raw.get("bbox") or {}
            if raw_area is None or not _valid_bbox(bbox):
                continue
            area_sqm = raw_area * area_to_square_meter_factor
            if area_sqm < MIN_REGION_AREA_SQM:
                continue
            raw_length = _float_or_none(raw.get("length")) or 0.0
            perimeter_m = raw_length * unit_to_meter_factor
            inside, nearby = _bind_text_to_bbox(text_records, bbox)
            room_labels = _labels_from_texts([*inside, *nearby], ROOM_KEYWORDS)
            project_labels = _labels_from_texts([*inside, *nearby], PROJECT_KEYWORDS)
            confidence, status, evidence = _region_confidence(inside, nearby, room_labels, project_labels, raw)
            region_rows.append(
                {
                    "区域编号": f"BIZ2xR-{sequence:05d}",
                    "来源文件": source_file,
                    "CAD面积": round(area_sqm, 4),
                    "CAD周长": round(perimeter_m, 4),
                    "面积单位": "㎡",
                    "长度单位": "m",
                    "图层": str(raw.get("layer") or ""),
                    "实体类型": str(raw.get("entity_type") or ""),
                    "源行号": raw.get("line_number", ""),
                    "区域边界": json.dumps(bbox, ensure_ascii=False),
                    "区域内文字": "；".join(item["text"] for item in inside[:MAX_TEXT_PER_REGION]),
                    "附近文字": "；".join(item["text"] for item in nearby[:MAX_TEXT_PER_REGION]),
                    "房间/空间标签": "；".join(room_labels),
                    "项目标签": "；".join(project_labels),
                    "区域类型建议": _region_type(project_labels, room_labels, raw),
                    "绑定置信度": confidence,
                    "绑定状态": status,
                    "证据说明": evidence,
                    "_geometry_key": _geometry_key(source_file, raw),
                    "_inside_texts": inside,
                    "_nearby_texts": nearby,
                }
            )
            sequence += 1

    status_counts = Counter(row["绑定状态"] for row in region_rows)
    return {
        "ok": True,
        "phase": "BIZ-2x-cad-region-label-binding",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "region_candidate_count": len(region_rows),
            "labeled_region_count": sum(1 for row in region_rows if row["绑定状态"] != "未绑定文字"),
            "inside_text_region_count": sum(1 for row in region_rows if row["区域内文字"]),
            "nearby_text_region_count": sum(1 for row in region_rows if row["附近文字"]),
            "room_labeled_region_count": sum(1 for row in region_rows if row["房间/空间标签"]),
            "project_labeled_region_count": sum(1 for row in region_rows if row["项目标签"]),
            "status_counts": dict(status_counts.most_common()),
            "unit_conversion": {
                "unit_to_meter_factor": unit_to_meter_factor,
                "area_to_square_meter_factor": area_to_square_meter_factor,
            },
            "next_step": "bind_recognized_project_to_labeled_cad_region",
        },
        "region_rows": [{key: value for key, value in row.items() if not key.startswith("_")} for row in region_rows],
        "region_index_rows": region_rows,
        "notes": [
            "本报告只建立 CAD 闭合区域与文字标签的关系，不直接生成最终工程量。",
            "区域标签可用于后续房间面积、吊顶面积、墙面防水、踢脚线长度等专项算量。",
            "未绑定文字的区域仍可能是有效几何区域，但不能直接用于最终清单。",
        ],
    }


def write_region_label_binding_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_CAD区域文字绑定_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}_区域文字绑定.csv"
    json_path.write_text(json.dumps(_serializable_report(report), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_region_label_binding_markdown(report), encoding="utf-8")
    _write_csv(csv_path, report.get("region_rows") or [], REGION_LABEL_HEADERS)
    return {"json": str(json_path), "markdown": str(markdown_path), "region_label_csv": str(csv_path)}


def build_region_label_binding_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x CAD 区域文字绑定报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 区域候选数：{summary.get('region_candidate_count', 0)}",
        f"- 已绑定文字区域：{summary.get('labeled_region_count', 0)}",
        f"- 房间/空间标签区域：{summary.get('room_labeled_region_count', 0)}",
        f"- 项目标签区域：{summary.get('project_labeled_region_count', 0)}",
        "",
        "## 区域样例",
        "",
        "| 区域编号 | 来源文件 | 面积 | 图层 | 房间/空间 | 项目标签 | 状态 | 文字证据 |",
        "| --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in (report.get("region_rows") or [])[:120]:
        text = row.get("区域内文字") or row.get("附近文字")
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("区域编号")),
                    _md(row.get("来源文件")),
                    _md(f"{row.get('CAD面积')}㎡"),
                    _md(row.get("图层")),
                    _md(row.get("房间/空间标签")),
                    _md(row.get("项目标签")),
                    _md(row.get("绑定状态")),
                    _md(text),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告不输出最终工程量。",
            "- 文字位于区域内时优先级高于附近文字。",
            "- 后续需要结合标准规则处理扣减、并入、展开面积和复用关系。",
        ]
    )
    return "\n".join(lines) + "\n"


def region_label_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("region_index_rows") or report.get("region_rows") or []
    return {str(row.get("_geometry_key") or row.get("几何键") or ""): row for row in rows if row.get("_geometry_key") or row.get("几何键")}


def _collect_text_records_by_file(
    *,
    parsed_text_files: Iterable[Any] | None,
    text_report: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for parsed in parsed_text_files or []:
        file_name = str(getattr(parsed, "file_name", "") or "")
        records = getattr(parsed, "text_records", ())
        for record in records:
            item = record.as_dict() if hasattr(record, "as_dict") else dict(record)
            _append_text_record(by_file, file_name or str(item.get("source_file") or ""), item)
    if parsed_text_files:
        return by_file
    for file_item in (text_report or {}).get("files") or []:
        file_name = str(file_item.get("file_name") or "")
        for item in [*(file_item.get("important_texts") or []), *(file_item.get("text_samples") or [])]:
            _append_text_record(by_file, file_name or str(item.get("source_file") or ""), item)
    return by_file


def _append_text_record(by_file: dict[str, list[dict[str, Any]]], file_name: str, item: dict[str, Any]) -> None:
    text = _clean_text(item.get("text"))
    if not file_name or not text or _is_noise_text(text):
        return
    x = _float_or_none(item.get("x"))
    y = _float_or_none(item.get("y"))
    if x is None or y is None:
        return
    by_file.setdefault(file_name, []).append(
        {
            "text": text,
            "x": x,
            "y": y,
            "layer": str(item.get("layer") or ""),
            "line_number": item.get("line_number", ""),
        }
    )


def _bind_text_to_bbox(text_records: list[dict[str, Any]], bbox: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inside: list[dict[str, Any]] = []
    nearby: list[dict[str, Any]] = []
    min_x = float(bbox["min_x"])
    min_y = float(bbox["min_y"])
    max_x = float(bbox["max_x"])
    max_y = float(bbox["max_y"])
    width = max_x - min_x
    height = max_y - min_y
    near_threshold = max(200.0, min(max(width, height) * 0.18, 2500.0))
    for item in text_records:
        x = float(item["x"])
        y = float(item["y"])
        if min_x <= x <= max_x and min_y <= y <= max_y:
            inside.append({**item, "distance": 0.0})
            continue
        distance = _point_bbox_distance(x, y, min_x, min_y, max_x, max_y)
        if distance <= near_threshold:
            nearby.append({**item, "distance": round(distance, 2)})
    inside = _rank_texts(inside)
    nearby = _rank_texts(nearby)
    return inside[:MAX_TEXT_PER_REGION], nearby[:MAX_TEXT_PER_REGION]


def _rank_texts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (float(item.get("distance") or 0), -_text_score(item["text"]), len(item["text"])))


def _labels_from_texts(texts: list[dict[str, Any]], keywords: tuple[str, ...]) -> list[str]:
    labels: list[str] = []
    for item in texts:
        text = str(item.get("text") or "")
        for keyword in keywords:
            if keyword in text and keyword not in labels:
                labels.append(keyword)
        if len(labels) >= 6:
            break
    return labels


def _region_confidence(
    inside: list[dict[str, Any]],
    nearby: list[dict[str, Any]],
    room_labels: list[str],
    project_labels: list[str],
    raw: dict[str, Any],
) -> tuple[str, str, str]:
    score = 0
    reasons: list[str] = []
    if inside:
        score += 4
        reasons.append("区域内存在文字")
    if nearby:
        score += 1
        reasons.append("区域附近存在文字")
    if room_labels:
        score += 2
        reasons.append("识别到房间/空间标签")
    if project_labels:
        score += 2
        reasons.append("识别到项目标签")
    layer = str(raw.get("layer") or "")
    if any(keyword in layer for keyword in PROJECT_KEYWORDS):
        score += 1
        reasons.append("图层包含项目关键词")
    if score >= 7:
        return f"高({score})", "已绑定区域文字", "；".join(reasons)
    if score >= 4:
        return f"中({score})", "疑似绑定区域文字", "；".join(reasons)
    if score > 0:
        return f"低({score})", "弱绑定区域文字", "；".join(reasons)
    return "", "未绑定文字", "区域内/附近未发现可用文字"


def _region_type(project_labels: list[str], room_labels: list[str], raw: dict[str, Any]) -> str:
    labels = set(project_labels)
    layer = str(raw.get("layer") or "")
    if {"吊顶", "天棚", "天花", "石膏板"} & labels or any(term in layer for term in ("天花", "吊顶", "造型")):
        return "吊顶/天棚区域候选"
    if {"地面", "楼地面", "地砖", "地板"} & labels:
        return "地面区域候选"
    if {"防水"} & labels or any(term in room_labels for term in ("洗手间", "卫生间")):
        return "防水/湿区区域候选"
    if {"墙面", "乳胶漆", "涂料"} & labels:
        return "墙面/涂料区域候选"
    if room_labels:
        return "房间/空间区域候选"
    return "未分类闭合区域"


def _geometry_key(source_file: str, raw: dict[str, Any]) -> str:
    return "|".join([source_file, str(raw.get("entity_type") or ""), str(raw.get("line_number") or "")])


def _serializable_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "region_index_rows"}


def _valid_bbox(bbox: dict[str, Any]) -> bool:
    required = {"min_x", "min_y", "max_x", "max_y"}
    if not required.issubset(bbox):
        return False
    values = [_float_or_none(bbox.get(key)) for key in required]
    if any(value is None for value in values):
        return False
    return float(bbox["max_x"]) > float(bbox["min_x"]) and float(bbox["max_y"]) > float(bbox["min_y"])


def _point_bbox_distance(x: float, y: float, min_x: float, min_y: float, max_x: float, max_y: float) -> float:
    dx = max(min_x - x, 0.0, x - max_x)
    dy = max(min_y - y, 0.0, y - max_y)
    return math.hypot(dx, dy)


def _text_score(text: str) -> int:
    score = 0
    if any(keyword in text for keyword in ROOM_KEYWORDS):
        score += 3
    if any(keyword in text for keyword in PROJECT_KEYWORDS):
        score += 3
    if len(text) <= 16:
        score += 1
    return score


def _is_noise_text(text: str) -> bool:
    if len(text) > 50:
        return True
    if re.fullmatch(r"[\d\s.,:：~\-+*/()（）]+", text):
        return True
    return any(term in text for term in NOISE_TEXT_TERMS)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{header: row.get(header, "") for header in headers} for row in rows])
