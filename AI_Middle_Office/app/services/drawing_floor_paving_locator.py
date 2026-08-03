from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


FLOOR_PROJECT_HEADERS = [
    "识别项目编号",
    "项目名称",
    "单位",
    "材料编号",
    "材料名称",
    "规格",
    "候选状态",
    "候选CAD编号",
    "候选来源文件",
    "候选图层",
    "材料文字",
    "材料文字来源文件",
    "材料文字源行号",
    "材料文字X",
    "材料文字Y",
    "CAD面积",
    "CAD周长",
    "建议工程量",
    "建议单位",
    "距离",
    "风险标记",
    "证据说明",
]

FLOOR_GEOMETRY_HEADERS = [
    "候选CAD编号",
    "来源文件",
    "图层",
    "实体类型",
    "源行号",
    "CAD面积",
    "CAD周长",
    "bbox",
    "候选状态",
    "风险标记",
    "证据说明",
]

FLOOR_TEXT_HEADERS = [
    "材料编号",
    "材料名称",
    "规格",
    "材料文字",
    "来源文件",
    "源行号",
    "图层",
    "布局",
    "X",
    "Y",
    "匹配得分",
    "证据说明",
]

FLOOR_LAYER_TERMS = ("F-地面", "地面材料", "地面填充", "地面分界", "铺装", "FC-", "P-地台")
FLOOR_TEXT_TERMS = ("地砖", "玻化砖", "地面", "铺装", "地板", "美缝")
LEGEND_TEXT_TERMS = ("图例", "材料表", "材料说明", "序号", "页码", "ENTER NUMBER", "Ref ID", "轴号")
MIN_EFFECTIVE_FLOOR_AREA_SQM = 2.0


def build_floor_paving_locator_report(
    *,
    project_material_binding_report: dict[str, Any],
    field_report: dict[str, Any],
    geometry_report: dict[str, Any],
    region_label_report: dict[str, Any] | None = None,
    unit_conversion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversion = unit_conversion or {}
    unit_to_meter_factor = float(conversion.get("unit_to_meter_factor") or 0.001)
    area_to_square_meter_factor = float(conversion.get("area_to_square_meter_factor") or unit_to_meter_factor * unit_to_meter_factor)

    material_projects = _collect_floor_material_projects(project_material_binding_report)
    material_text_rows = _collect_material_text_rows(material_projects, field_report)
    floor_geometry_rows = _collect_floor_geometry_rows(geometry_report, unit_to_meter_factor, area_to_square_meter_factor)
    project_rows = _build_floor_project_rows(material_projects, material_text_rows, floor_geometry_rows)
    status_counts = Counter(row["候选状态"] for row in project_rows)
    risk_counts = Counter()
    for row in [*project_rows, *floor_geometry_rows]:
        for risk in _split_risks(row.get("风险标记")):
            risk_counts[risk] += 1

    return {
        "ok": True,
        "phase": "BIZ-2x-R3-3-floor-paving-effective-area-locator",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "floor_material_project_count": len(material_projects),
            "floor_material_text_evidence_count": len(material_text_rows),
            "floor_layer_area_candidate_count": len(floor_geometry_rows),
            "effective_floor_area_candidate_count": sum(1 for row in floor_geometry_rows if row["候选状态"] == "有效地面铺装面积候选，待材料文字绑定"),
            "floor_project_bound_candidate_count": sum(1 for row in project_rows if row["候选状态"] == "已定位地面铺装有效区域候选，待人工确认/R4规则计算"),
            "floor_project_sample_missing_count": sum(1 for row in project_rows if "地面图层面积候选未进入现有几何样本" in row["候选状态"]),
            "floor_project_no_material_text_count": sum(1 for row in project_rows if row["候选状态"] == "未找到材料文字坐标证据，待 R3-3 继续补文字定位"),
            "legend_or_detail_text_count": sum(1 for row in material_text_rows if "legend_or_detail_text" in _split_risks(row.get("风险标记"))),
            "status_counts": dict(status_counts.most_common()),
            "risk_counts": dict(risk_counts.most_common()),
            "final_generation_status": "blocked_until_floor_layer_rescan_or_manual_area_review",
            "next_step": "targeted_rescan_floor_layers_then_bind_CT_material_text_to_real_floor_regions",
        },
        "floor_project_rows": project_rows,
        "floor_geometry_rows": floor_geometry_rows,
        "floor_material_text_rows": material_text_rows,
        "notes": [
            "R3-3 只定位地面铺装有效区域候选，不生成最终工程量。",
            "材料文字来自图纸文字标注坐标；CAD 面积候选来自地面相关图层闭合几何。",
            "如果现有几何样本缺少地面图层面积候选，需要只针对地面图层定向重扫 DXF，而不是扩大通用 CAD 几何优化范围。",
            "疑似图例、材料表、节点小块的文字证据只保留为风险证据，不提升为项目工程量。",
        ],
    }


def write_floor_paving_locator_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_R3_地面铺装有效区域定位_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    project_csv_path = target_dir / f"{file_stem}_项目地面铺装候选.csv"
    geometry_csv_path = target_dir / f"{file_stem}_地面图层面积候选.csv"
    text_csv_path = target_dir / f"{file_stem}_地面材料文字坐标.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_floor_paving_locator_markdown(report), encoding="utf-8")
    _write_csv(project_csv_path, report.get("floor_project_rows") or [], FLOOR_PROJECT_HEADERS)
    _write_csv(geometry_csv_path, report.get("floor_geometry_rows") or [], FLOOR_GEOMETRY_HEADERS)
    _write_csv(text_csv_path, report.get("floor_material_text_rows") or [], FLOOR_TEXT_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "project_csv": str(project_csv_path),
        "geometry_csv": str(geometry_csv_path),
        "text_csv": str(text_csv_path),
    }


def build_floor_paving_locator_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x R3-3 地面铺装有效区域定位报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 地面材料项目数：{summary.get('floor_material_project_count', 0)}",
        f"- 地面材料文字坐标证据：{summary.get('floor_material_text_evidence_count', 0)}",
        f"- 地面图层面积候选：{summary.get('floor_layer_area_candidate_count', 0)}",
        f"- 有效地面面积候选：{summary.get('effective_floor_area_candidate_count', 0)}",
        f"- 已定位项目候选：{summary.get('floor_project_bound_candidate_count', 0)}",
        f"- 样本缺地面图层项目：{summary.get('floor_project_sample_missing_count', 0)}",
        f"- 状态分布：{summary.get('status_counts', {})}",
        f"- 风险分布：{summary.get('risk_counts', {})}",
        "",
        "## 项目候选",
        "",
        "| 项目编号 | 项目 | 材料编号 | 材料名称 | 状态 | CAD候选 | 图层 | 材料文字 | 建议量 | 风险 | 证据 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in (report.get("floor_project_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("识别项目编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("材料编号")),
                    _md(row.get("材料名称")),
                    _md(row.get("候选状态")),
                    _md(row.get("候选CAD编号")),
                    _md(row.get("候选图层")),
                    _md(row.get("材料文字")),
                    _md(f"{row.get('建议工程量')}{row.get('建议单位')}".strip()),
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
            "- 只有“已定位地面铺装有效区域候选”的行，才可进入 R4 标准工程量规则计算前的人工复核。",
            "- 若地面图层面积候选为 0，应下一步定向重扫地面图层 DXF 图元。",
        ]
    )
    return "\n".join(lines) + "\n"


def _collect_floor_material_projects(project_material_binding_report: dict[str, Any]) -> list[dict[str, Any]]:
    material_table_rows = project_material_binding_report.get("material_table_rows") or []
    table_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in material_table_rows:
        table_by_code.setdefault(str(row.get("材料编号") or ""), []).append(row)
    result: list[dict[str, Any]] = []
    for row in project_material_binding_report.get("project_binding_rows") or []:
        codes = _split_material_codes(row.get("材料编号"))
        if not codes:
            continue
        project_text = _normalize(" ".join(str(row.get(key) or "") for key in ("项目名称", "材料表证据", "绑定说明")))
        if not ("地面" in project_text or "楼地面" in project_text or "地砖" in project_text or any(code.startswith("CT-") for code in codes)):
            continue
        for code in codes:
            if not code.startswith("CT-"):
                continue
            entries = table_by_code.get(code) or [{"材料编号": code, "材料名称": "", "规格": ""}]
            best_entry = _best_material_entry(entries)
            result.append(
                {
                    "识别项目编号": row.get("识别项目编号", ""),
                    "项目名称": row.get("项目名称", ""),
                    "单位": row.get("单位", ""),
                    "材料编号": code,
                    "材料名称": best_entry.get("材料名称", ""),
                    "规格": best_entry.get("规格", ""),
                    "来源文件": row.get("来源文件", ""),
                }
            )
    return result


def _collect_material_text_rows(material_projects: list[dict[str, Any]], field_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    annotations = [
        row for row in field_report.get("drawing_annotation_rows") or []
        if _float_or_none(row.get("x")) is not None and _float_or_none(row.get("y")) is not None
    ]
    for project in material_projects:
        for annotation in annotations:
            score, reasons = _score_material_text(project, annotation)
            if score < 3.5:
                continue
            risks = _text_risks(annotation)
            rows.append(
                {
                    "材料编号": project["材料编号"],
                    "材料名称": project["材料名称"],
                    "规格": project["规格"],
                    "材料文字": annotation.get("material_or_method_name") or annotation.get("raw_row_text") or "",
                    "来源文件": annotation.get("source_file", ""),
                    "源行号": annotation.get("source_row_number", ""),
                    "图层": annotation.get("layer", ""),
                    "布局": annotation.get("layout", ""),
                    "X": annotation.get("x", ""),
                    "Y": annotation.get("y", ""),
                    "匹配得分": round(score, 2),
                    "证据说明": "；".join(reasons),
                    "风险标记": "；".join(risks),
                }
            )
    return _dedupe_text_rows(rows)


def _collect_floor_geometry_rows(
    geometry_report: dict[str, Any],
    unit_to_meter_factor: float,
    area_to_square_meter_factor: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sequence = 1
    for file_item in geometry_report.get("files") or []:
        source_file = str(file_item.get("file_name") or "")
        for candidate in file_item.get("area_candidates") or []:
            layer = str(candidate.get("layer") or "")
            if not _is_floor_layer(layer):
                continue
            raw_area = _float_or_none(candidate.get("area"))
            bbox = candidate.get("bbox") or {}
            if raw_area is None or not _valid_bbox(bbox):
                continue
            area = round(raw_area * area_to_square_meter_factor, 4)
            perimeter = round((_float_or_none(candidate.get("length")) or 0.0) * unit_to_meter_factor, 4)
            risks = []
            if area < MIN_EFFECTIVE_FLOOR_AREA_SQM:
                risks.append("area_too_small_for_floor_paving")
            if _looks_like_legend_or_detail_source(source_file, layer):
                risks.append("legend_or_detail_geometry")
            status = "有效地面铺装面积候选，待材料文字绑定" if not risks else "疑似图例/节点地面候选，需排除"
            rows.append(
                {
                    "候选CAD编号": f"BIZ2xF-G{sequence:05d}",
                    "来源文件": source_file,
                    "图层": layer,
                    "实体类型": candidate.get("entity_type", ""),
                    "源行号": candidate.get("line_number", ""),
                    "CAD面积": area,
                    "CAD周长": perimeter,
                    "bbox": json.dumps(bbox, ensure_ascii=False),
                    "候选状态": status,
                    "风险标记": "；".join(risks),
                    "证据说明": f"来源图层 `{layer}`，面积 {area}㎡",
                    "_bbox": bbox,
                }
            )
            sequence += 1
    return rows


def _build_floor_project_rows(
    material_projects: list[dict[str, Any]],
    material_text_rows: list[dict[str, Any]],
    floor_geometry_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    effective_geometries = [row for row in floor_geometry_rows if row["候选状态"] == "有效地面铺装面积候选，待材料文字绑定"]
    for project in material_projects:
        text_candidates = [
            row for row in material_text_rows
            if row["材料编号"] == project["材料编号"] and "legend_or_detail_text" not in _split_risks(row.get("风险标记"))
        ]
        if not text_candidates:
            rows.append(_empty_project_row(project, "未找到材料文字坐标证据，待 R3-3 继续补文字定位"))
            continue
        if not effective_geometries:
            best_text = _best_text_candidate(text_candidates)
            rows.append(
                _empty_project_row(
                    project,
                    "地面图层面积候选未进入现有几何样本，需定向重扫地面图层",
                    material_text=best_text,
                    risks=["floor_layer_sample_missing"],
                    evidence="已找到材料文字坐标，但现有几何样本中没有可用地面图层闭合面积候选",
                )
            )
            continue
        best = _nearest_geometry_for_text(text_candidates, effective_geometries)
        if not best:
            best_text = _best_text_candidate(text_candidates)
            rows.append(
                _empty_project_row(
                    project,
                    "材料文字未落入/靠近有效地面区域，待人工复核",
                    material_text=best_text,
                    risks=["material_text_not_near_floor_geometry"],
                )
            )
            continue
        text_row, geometry_row, distance = best
        rows.append(
            {
                "识别项目编号": project.get("识别项目编号", ""),
                "项目名称": project.get("项目名称", ""),
                "单位": project.get("单位", ""),
                "材料编号": project.get("材料编号", ""),
                "材料名称": project.get("材料名称", ""),
                "规格": project.get("规格", ""),
                "候选状态": "已定位地面铺装有效区域候选，待人工确认/R4规则计算",
                "候选CAD编号": geometry_row.get("候选CAD编号", ""),
                "候选来源文件": geometry_row.get("来源文件", ""),
                "候选图层": geometry_row.get("图层", ""),
                "材料文字": text_row.get("材料文字", ""),
                "材料文字来源文件": text_row.get("来源文件", ""),
                "材料文字源行号": text_row.get("源行号", ""),
                "材料文字X": text_row.get("X", ""),
                "材料文字Y": text_row.get("Y", ""),
                "CAD面积": geometry_row.get("CAD面积", ""),
                "CAD周长": geometry_row.get("CAD周长", ""),
                "建议工程量": geometry_row.get("CAD面积", ""),
                "建议单位": "㎡",
                "距离": distance,
                "风险标记": "",
                "证据说明": f"材料文字坐标落入/靠近地面图层面积候选；{text_row.get('证据说明', '')}",
            }
        )
    return rows


def _empty_project_row(
    project: dict[str, Any],
    status: str,
    *,
    material_text: dict[str, Any] | None = None,
    risks: list[str] | None = None,
    evidence: str = "",
) -> dict[str, Any]:
    text = material_text or {}
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "材料编号": project.get("材料编号", ""),
        "材料名称": project.get("材料名称", ""),
        "规格": project.get("规格", ""),
        "候选状态": status,
        "候选CAD编号": "",
        "候选来源文件": "",
        "候选图层": "",
        "材料文字": text.get("材料文字", ""),
        "材料文字来源文件": text.get("来源文件", ""),
        "材料文字源行号": text.get("源行号", ""),
        "材料文字X": text.get("X", ""),
        "材料文字Y": text.get("Y", ""),
        "CAD面积": "",
        "CAD周长": "",
        "建议工程量": "",
        "建议单位": "",
        "距离": "",
        "风险标记": "；".join(risks or []),
        "证据说明": evidence or status,
    }


def _nearest_geometry_for_text(
    text_rows: list[dict[str, Any]],
    geometry_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], float] | None:
    matches: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    for text in text_rows:
        x = _float_or_none(text.get("X"))
        y = _float_or_none(text.get("Y"))
        if x is None or y is None:
            continue
        for geometry in geometry_rows:
            if text.get("来源文件") != geometry.get("来源文件"):
                continue
            bbox = geometry.get("_bbox") or {}
            if not _valid_bbox(bbox):
                continue
            distance = _point_bbox_distance(x, y, bbox)
            threshold = _near_threshold(bbox)
            if distance <= threshold:
                matches.append((text, geometry, round(distance, 2)))
    if not matches:
        return None
    return sorted(matches, key=lambda item: (item[2], -float(item[0].get("匹配得分") or 0), -float(item[1].get("CAD面积") or 0)))[0]


def _best_text_candidate(text_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(text_rows, key=lambda item: (-float(item.get("匹配得分") or 0), str(item.get("来源文件") or ""), str(item.get("源行号") or "")))[0]


def _score_material_text(project: dict[str, Any], annotation: dict[str, Any]) -> tuple[float, list[str]]:
    text = _normalize(" ".join(str(annotation.get(key) or "") for key in ("material_or_method_name", "spec_or_method", "raw_row_text")))
    name = _normalize(str(project.get("材料名称") or ""))
    spec = _normalize(str(project.get("规格") or ""))
    score = 0.0
    reasons: list[str] = []
    if name and name in text:
        score += 5.0
        reasons.append("命中完整材料名称")
    if spec and spec in text:
        score += 3.0
        reasons.append("命中材料规格")
    family_hits = [term for term in _floor_family_terms(str(project.get("材料名称") or "")) if term in text]
    if family_hits:
        score += min(2.0, len(family_hits) * 1.0)
        reasons.append("命中地面材料类别：" + "、".join(family_hits[:4]))
    if _normalize(str(project.get("材料编号") or "").split("-")[0]) in text:
        score += 1.0
        reasons.append("命中材料类别前缀")
    color_hits = [term for term in ("灰色", "白色", "黑色") if term in text and term in str(project.get("材料名称") or "")]
    if color_hits:
        score += 0.5
        reasons.append("命中材料颜色：" + "、".join(color_hits))
    return score, reasons


def _text_risks(annotation: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(annotation.get(key) or "")
        for key in ("source_file", "material_or_method_name", "spec_or_method", "raw_row_text", "layer", "layout")
    )
    risks: list[str] = []
    if any(term in text for term in LEGEND_TEXT_TERMS):
        risks.append("legend_or_detail_text")
    if any(term in text for term in ("前言", "通用节点", "材料表", "图例")):
        risks.append("legend_or_detail_text")
    if "BLOCKS" in text and not any(term in text for term in ("地面文字", "FC-TEXT")):
        risks.append("block_symbol_text")
    return _dedupe(risks)


def _floor_family_terms(name: str) -> list[str]:
    normalized = _normalize(name)
    terms = []
    if any(term in normalized for term in ("地砖", "玻化砖", "瓷砖")):
        terms.extend(["地砖", "玻化砖", "瓷砖"])
    if "地板" in normalized:
        terms.append("地板")
    return _dedupe(terms)


def _best_material_entry(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(entries, key=lambda item: (-len(str(item.get("材料名称") or "")), str(item.get("来源文件") or "")))[0]


def _split_material_codes(value: Any) -> list[str]:
    return [part.strip().upper() for part in re.split(r"[、,，;\s]+", str(value or "")) if part.strip()]


def _is_floor_layer(layer: str) -> bool:
    return any(term.lower() in layer.lower() for term in FLOOR_LAYER_TERMS)


def _looks_like_legend_or_detail_source(source_file: str, layer: str) -> bool:
    text = f"{source_file} {layer}"
    return any(term in text for term in ("图例", "节点", "通用", "目录", "前言", "说明"))


def _valid_bbox(bbox: dict[str, Any]) -> bool:
    required = {"min_x", "min_y", "max_x", "max_y"}
    if not required.issubset(bbox):
        return False
    values = [_float_or_none(bbox.get(key)) for key in required]
    if any(value is None for value in values):
        return False
    return float(bbox["max_x"]) > float(bbox["min_x"]) and float(bbox["max_y"]) > float(bbox["min_y"])


def _point_bbox_distance(x: float, y: float, bbox: dict[str, Any]) -> float:
    min_x = float(bbox["min_x"])
    min_y = float(bbox["min_y"])
    max_x = float(bbox["max_x"])
    max_y = float(bbox["max_y"])
    dx = max(min_x - x, 0.0, x - max_x)
    dy = max(min_y - y, 0.0, y - max_y)
    return math.hypot(dx, dy)


def _near_threshold(bbox: dict[str, Any]) -> float:
    width = float(bbox["max_x"]) - float(bbox["min_x"])
    height = float(bbox["max_y"]) - float(bbox["min_y"])
    return max(200.0, min(max(width, height) * 0.18, 2500.0))


def _dedupe_text_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in sorted(rows, key=lambda item: (-float(item.get("匹配得分") or 0), str(item.get("来源文件") or ""), str(item.get("源行号") or ""))):
        key = (str(row.get("材料编号")), str(row.get("来源文件")), str(row.get("源行号")), str(row.get("材料文字")))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _split_risks(value: Any) -> list[str]:
    return [part for part in str(value or "").split("；") if part]


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
