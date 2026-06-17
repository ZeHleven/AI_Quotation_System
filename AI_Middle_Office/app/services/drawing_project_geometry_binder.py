from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


MIN_AREA_SQM = 0.5
MIN_LENGTH_M = 0.2
HIGH_SCORE = 7.0
MEDIUM_SCORE = 4.0
MAX_CANDIDATES_PER_PROJECT = 8

BINDING_ROW_HEADERS = [
    "识别项目编号",
    "图纸项目名称",
    "项目名称",
    "单位",
    "期望算量类型",
    "绑定状态",
    "推荐CAD候选编号",
    "建议工程量",
    "建议单位",
    "绑定置信度",
    "绑定说明",
    "候选数量",
]

CANDIDATE_ROW_HEADERS = [
    "识别项目编号",
    "项目名称",
    "CAD候选编号",
    "建议工程量",
    "建议单位",
    "候选类型",
    "来源文件",
    "图层",
    "块名",
    "实体类型",
    "源行号",
    "绑定评分",
    "绑定置信度",
    "评分原因",
    "CAD原始值",
    "CAD边界",
    "CAD区域文字",
]


def build_project_geometry_binding_report(
    *,
    project_report: dict[str, Any],
    geometry_report: dict[str, Any],
    unit_conversion: dict[str, Any] | None = None,
    region_label_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversion = unit_conversion or {}
    unit_to_meter_factor = float(conversion.get("unit_to_meter_factor") or 0.001)
    area_to_square_meter_factor = float(conversion.get("area_to_square_meter_factor") or unit_to_meter_factor * unit_to_meter_factor)
    geometry_candidates = _collect_geometry_candidates(
        geometry_report,
        unit_to_meter_factor=unit_to_meter_factor,
        area_to_square_meter_factor=area_to_square_meter_factor,
        region_label_report=region_label_report or {},
    )

    binding_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for project in project_report.get("project_rows") or []:
        expected_kind = _expected_quantity_kind(project)
        compatible = [candidate for candidate in geometry_candidates if candidate["quantity_kind"] == expected_kind]
        scored = sorted(
            (_score_candidate(project, candidate) for candidate in compatible),
            key=lambda item: item["score"],
            reverse=True,
        )
        selected = [item for item in scored if item["score"] >= MEDIUM_SCORE][:MAX_CANDIDATES_PER_PROJECT]
        binding = _binding_row(project, expected_kind, selected)
        binding_rows.append(binding)
        for item in selected:
            candidate_rows.append(_candidate_row(project, item))

    status_counts = Counter(row["绑定状态"] for row in binding_rows)
    return {
        "ok": True,
        "phase": "BIZ-2x-project-geometry-binding",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "recognized_project_count": len(project_report.get("project_rows") or []),
            "geometry_candidate_count": len(geometry_candidates),
            "binding_ready_project_count": sum(1 for row in binding_rows if row["绑定状态"] == "建议绑定，需复核"),
            "ambiguous_project_count": sum(1 for row in binding_rows if row["绑定状态"] == "多个CAD候选需选择"),
            "unbound_project_count": sum(1 for row in binding_rows if row["绑定状态"] == "未找到可绑定CAD候选"),
            "candidate_option_count": len(candidate_rows),
            "region_labeled_candidate_count": sum(1 for candidate in geometry_candidates if candidate.get("region_text")),
            "binding_status_counts": dict(status_counts.most_common()),
            "unit_conversion": {
                "unit_to_meter_factor": unit_to_meter_factor,
                "area_to_square_meter_factor": area_to_square_meter_factor,
            },
            "final_generation_status": "blocked_until_project_geometry_binding_reviewed",
            "next_step": "review_binding_candidates_then_apply_standard_quantity_rule",
        },
        "binding_rows": binding_rows,
        "candidate_rows": candidate_rows,
        "notes": [
            "本报告以图纸项目为中心寻找兼容 CAD 面积/长度/数量候选，仍不是最终工程量。",
            "建议绑定的工程量必须继续经过标准工程量规则、扣减/并入规则和业务复核。",
            "未找到候选的项目需要继续识别区域边界、材料编号或人工补充算量证据。",
        ],
    }


def write_project_geometry_binding_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_项目几何绑定_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    binding_csv_path = target_dir / f"{file_stem}_项目绑定状态.csv"
    candidate_csv_path = target_dir / f"{file_stem}_项目CAD候选.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_project_geometry_binding_markdown(report), encoding="utf-8")
    _write_csv(binding_csv_path, report.get("binding_rows") or [], BINDING_ROW_HEADERS)
    _write_csv(candidate_csv_path, report.get("candidate_rows") or [], CANDIDATE_ROW_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "binding_csv": str(binding_csv_path),
        "candidate_csv": str(candidate_csv_path),
    }


def build_project_geometry_binding_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x 图纸项目与 CAD 几何绑定建议",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 图纸项目数：{summary.get('recognized_project_count', 0)}",
        f"- CAD 几何候选数：{summary.get('geometry_candidate_count', 0)}",
        f"- 建议绑定项目：{summary.get('binding_ready_project_count', 0)}",
        f"- 多候选需选择项目：{summary.get('ambiguous_project_count', 0)}",
        f"- 未绑定项目：{summary.get('unbound_project_count', 0)}",
        "",
        "## 项目绑定状态",
        "",
        "| 项目编号 | 项目 | 类型 | 状态 | 建议量 | 说明 |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in (report.get("binding_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("识别项目编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("期望算量类型")),
                    _md(row.get("绑定状态")),
                    _md(f"{row.get('建议工程量')}{row.get('建议单位')}"),
                    _md(row.get("绑定说明")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 绑定状态为“建议绑定，需复核”的行仍不是最终工程量。",
            "- 多候选和未绑定项目需要继续通过材料编号、房间区域、闭合边界或人工复核确认。",
        ]
    )
    return "\n".join(lines) + "\n"


def _collect_geometry_candidates(
    geometry_report: dict[str, Any],
    *,
    unit_to_meter_factor: float,
    area_to_square_meter_factor: float,
    region_label_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    region_index = _region_index(region_label_report or {})
    sequence = 1
    for file_item in geometry_report.get("files") or []:
        source_file = str(file_item.get("file_name") or "")
        for raw in file_item.get("area_candidates") or []:
            quantity = _float_or_none(raw.get("area"))
            if quantity is None:
                continue
            converted = quantity * area_to_square_meter_factor
            if converted < MIN_AREA_SQM:
                continue
            candidates.append(_geometry_candidate(sequence, source_file, raw, "area", "㎡", converted, quantity, region_index))
            sequence += 1
        for raw in file_item.get("length_candidates") or []:
            quantity = _float_or_none(raw.get("length"))
            if quantity is None:
                continue
            converted = quantity * unit_to_meter_factor
            if converted < MIN_LENGTH_M:
                continue
            candidates.append(_geometry_candidate(sequence, source_file, raw, "length", "m", converted, quantity, region_index))
            sequence += 1
        for raw in file_item.get("count_candidates") or []:
            quantity = _float_or_none(raw.get("count")) or 1.0
            if quantity <= 0:
                continue
            candidates.append(_geometry_candidate(sequence, source_file, raw, "count", "个", quantity, quantity, region_index))
            sequence += 1
    return candidates


def _geometry_candidate(
    sequence: int,
    source_file: str,
    raw: dict[str, Any],
    quantity_kind: str,
    unit: str,
    converted_quantity: float,
    raw_quantity: float,
    region_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    region = (region_index or {}).get(_geometry_key(source_file, raw)) or {}
    region_text = "；".join(
        item
        for item in [
            str(region.get("区域内文字") or ""),
            str(region.get("附近文字") or ""),
            str(region.get("房间/空间标签") or ""),
            str(region.get("项目标签") or ""),
            str(region.get("区域类型建议") or ""),
        ]
        if item
    )
    return {
        "candidate_id": f"BIZ2xG-{sequence:05d}",
        "source_file": source_file,
        "quantity_kind": quantity_kind,
        "suggested_quantity": round(converted_quantity, 4),
        "suggested_unit": unit,
        "raw_quantity": raw_quantity,
        "layer": str(raw.get("layer") or ""),
        "block_name": str(raw.get("block_name") or ""),
        "entity_type": str(raw.get("entity_type") or ""),
        "line_number": raw.get("line_number", ""),
        "bbox": raw.get("bbox") or {},
        "quantity_hint": str(raw.get("quantity_hint") or ""),
        "risk_flags": list(raw.get("risk_flags") or []),
        "region_id": str(region.get("区域编号") or ""),
        "region_text": region_text,
        "region_status": str(region.get("绑定状态") or ""),
    }


def _binding_row(project: dict[str, Any], expected_kind: str, selected: list[dict[str, Any]]) -> dict[str, Any]:
    high = [item for item in selected if item["score"] >= HIGH_SCORE]
    if len(high) == 1:
        best = high[0]
        status = "建议绑定，需复核"
        explanation = "；".join(best["reasons"][:4])
    elif len(high) > 1:
        best = high[0]
        status = "多个CAD候选需选择"
        explanation = f"找到 {len(high)} 个高分候选，需要确认哪个区域属于该项目"
    elif selected:
        best = selected[0]
        status = "多个CAD候选需选择"
        explanation = "候选存在但证据不足，需要结合材料编号/房间区域复核"
    else:
        best = None
        status = "未找到可绑定CAD候选"
        explanation = "未找到同类型且图层/文件/项目关键词足够匹配的 CAD 几何候选"
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "图纸项目名称": project.get("图纸项目名称", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "期望算量类型": expected_kind,
        "绑定状态": status,
        "推荐CAD候选编号": best["candidate"]["candidate_id"] if best else "",
        "建议工程量": best["candidate"]["suggested_quantity"] if best else "",
        "建议单位": best["candidate"]["suggested_unit"] if best else "",
        "绑定置信度": _confidence(best["score"]) if best else "",
        "绑定说明": explanation,
        "候选数量": len(selected),
    }


def _candidate_row(project: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    candidate = scored["candidate"]
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "CAD候选编号": candidate["candidate_id"],
        "建议工程量": candidate["suggested_quantity"],
        "建议单位": candidate["suggested_unit"],
        "候选类型": candidate["quantity_kind"],
        "来源文件": candidate["source_file"],
        "图层": candidate["layer"],
        "块名": candidate["block_name"],
        "实体类型": candidate["entity_type"],
        "源行号": candidate["line_number"],
        "绑定评分": scored["score"],
        "绑定置信度": _confidence(scored["score"]),
        "评分原因": "；".join(scored["reasons"]),
        "CAD原始值": candidate["raw_quantity"],
        "CAD边界": json.dumps(candidate.get("bbox") or {}, ensure_ascii=False),
        "CAD区域文字": candidate.get("region_text", ""),
    }


def _score_candidate(project: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    project_text = _normalize(
        " ".join(
            [
                str(project.get("图纸项目名称") or ""),
                str(project.get("项目名称") or ""),
                str(project.get("项目特征") or ""),
                str(project.get("识别证据") or ""),
            ]
        )
    )
    source_files = _normalize(str(project.get("来源文件") or ""))
    candidate_file = _normalize(candidate["source_file"])
    candidate_text = _normalize(
        " ".join([candidate["source_file"], candidate["layer"], candidate["block_name"], candidate["quantity_hint"], candidate.get("region_text", "")])
    )

    if candidate_file and candidate_file in source_files:
        score += 3.0
        reasons.append("来源文件一致")

    category_terms = _category_terms(project)
    matched_terms = [term for term in category_terms if term in candidate_text]
    if matched_terms:
        score += min(5.0, len(matched_terms) * 1.5 + 1.0)
        reasons.append("CAD图层/块名命中项目类型：" + "、".join(matched_terms[:4]))

    project_terms = [term for term in _PROJECT_TERMS if term in project_text and term in candidate_text]
    if project_terms:
        score += min(4.0, len(project_terms) * 1.2)
        reasons.append("项目关键词一致：" + "、".join(project_terms[:4]))
    if candidate.get("region_text"):
        score += 1.5
        reasons.append("CAD区域已绑定文字标签")

    if _is_detail_or_annotation(candidate_text):
        score -= 2.5
        reasons.append("候选可能来自节点/图例/标注图层")
    if candidate["suggested_quantity"] < (1 if candidate["quantity_kind"] == "area" else 0.5 if candidate["quantity_kind"] == "length" else 1):
        score -= 1.0
        reasons.append("候选量偏小，需防止局部碎片")
    if not reasons:
        reasons.append("仅算量类型兼容，缺少强证据")
    return {"candidate": candidate, "score": round(max(0.0, score), 2), "reasons": reasons}


def _expected_quantity_kind(project: dict[str, Any]) -> str:
    unit = str(project.get("单位") or "")
    text = _normalize(" ".join([str(project.get("项目名称") or ""), str(project.get("图纸项目名称") or "")]))
    if unit in {"㎡", "m²", "m2", "平方米"}:
        return "area"
    if unit in {"m", "米"}:
        return "length"
    if unit in {"个", "套", "樘", "项"}:
        return "count"
    if any(term in text for term in ("踢脚", "窗帘盒", "线条", "线脚")):
        return "length"
    if any(term in text for term in ("门", "窗")):
        return "count"
    return "area"


def _category_terms(project: dict[str, Any]) -> tuple[str, ...]:
    text = _normalize(" ".join([str(project.get("项目名称") or ""), str(project.get("图纸项目名称") or ""), str(project.get("项目特征") or "")]))
    if any(term in text for term in ("吊顶", "天棚", "天花")):
        return ("吊顶", "天棚", "天花", "顶面", "造型")
    if any(term in text for term in ("楼地面", "地面", "地板", "防水")):
        return ("地面", "楼地面", "地台", "铺装", "防水")
    if "踢脚" in text:
        return ("踢脚", "踢脚线")
    if "窗帘盒" in text:
        return ("窗帘盒", "窗帘")
    if "墙" in text or "腻子" in text:
        return ("墙面", "立面", "腻子", "涂料")
    if "门" in text:
        return ("门",)
    if "窗" in text:
        return ("窗",)
    return ()


_PROJECT_TERMS = (
    "地面",
    "楼地面",
    "铺装",
    "防水",
    "吊顶",
    "天棚",
    "天花",
    "顶面",
    "墙面",
    "立面",
    "踢脚",
    "窗帘盒",
    "窗台",
    "门",
    "窗",
)


def _is_detail_or_annotation(text: str) -> bool:
    return any(term in text for term in ("图例", "图框", "标注", "尺寸", "节点", "大样", "索引", "文字"))


def _confidence(score: float) -> str:
    if score >= HIGH_SCORE:
        return f"高({score})"
    if score >= MEDIUM_SCORE:
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


def _region_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("region_index_rows") or report.get("region_rows") or []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("_geometry_key") or row.get("几何键") or "")
        if key:
            result[key] = row
    return result


def _geometry_key(source_file: str, raw: dict[str, Any]) -> str:
    return "|".join([source_file, str(raw.get("entity_type") or ""), str(raw.get("line_number") or "")])


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{header: row.get(header, "") for header in headers} for row in rows])
