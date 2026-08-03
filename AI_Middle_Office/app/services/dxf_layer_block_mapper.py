from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


LOW_RISK_AREA_KEYWORDS = ("地面", "楼地面", "顶面造型", "天花", "吊顶", "防水", "地台")
LOW_RISK_LENGTH_KEYWORDS = ("踢脚", "线脚", "窗帘", "窗帘盒", "门套", "压条", "收边", "里面线条", "立面线条")
LOW_RISK_COUNT_KEYWORDS = ("平面门", "建筑窗", "天花灯具", "顶面灯具", "洁具", "插座", "开关", "地漏")
LIGHTING_KEYWORDS = ("灯具", "灯带", "射灯", "筒灯", "吸顶灯", "柔光灯", "换气扇")

EXCLUDE_LAYER_KEYWORDS = (
    "尺寸",
    "标高",
    "标注",
    "图框",
    "目录",
    "图例",
    "索引",
    "文字",
    "轴号",
    "辅助",
    "引线",
    "0-TL",
    "DIM",
)
EXCLUDE_BLOCK_KEYWORDS = ("_ArchTick", "_ARCHTICK", "_Oblique", "轴号", "ZHL_XS", "A2 总平面轴号")
GENERIC_LAYER_NAMES = {"", "0", "WAll", "Wall", "墙体", "FURN"}

MAPPING_LIMIT = 500


class DxfLayerBlockMappingError(ValueError):
    pass


def load_json_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path)
    if not report_path.exists():
        raise DxfLayerBlockMappingError(f"Report not found: {report_path}")
    return json.loads(report_path.read_text(encoding="utf-8"))


def build_layer_block_mapping_report(
    *,
    geometry_report: dict[str, Any],
    confirmation_report: dict[str, Any],
) -> dict[str, Any]:
    if not confirmation_report.get("ready_for_geometry_quantity_probe"):
        raise DxfLayerBlockMappingError("Scale/unit confirmation is not ready for geometry quantity probe.")

    grouped = _group_geometry_candidates(geometry_report)
    mapping_rows = [_classify_group(group) for group in grouped]
    mapping_rows.sort(key=lambda item: (item["status_sort"], -item["candidate_count"], item["source_key"]))

    allowed_rows = [item for item in mapping_rows if item["allow_quantity_candidate_probe"]]
    status_counts = Counter(item["mapping_status"] for item in mapping_rows)
    kind_counts = Counter(item["quantity_kind"] for item in allowed_rows)
    risk_counts = Counter(item["risk_level"] for item in mapping_rows)

    return {
        "ok": True,
        "phase": "BIZ-2x-9c0-layer-block-low-risk-mapping",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ready_for_geometry_quantity_probe": bool(allowed_rows),
        "safe_for_auto_quantity": False,
        "unit_conversion": {
            "drawing_unit": confirmation_report.get("manual_confirmation", {}).get("drawing_unit", ""),
            "unit_to_meter_factor": confirmation_report.get("manual_confirmation", {}).get("unit_to_meter_factor", 0),
            "area_to_square_meter_factor": _area_factor(confirmation_report),
            "title_block_scale_usage": confirmation_report.get("manual_confirmation", {}).get("title_block_scale_usage", ""),
        },
        "summary": {
            "group_count": len(mapping_rows),
            "allowed_group_count": len(allowed_rows),
            "blocked_group_count": len(mapping_rows) - len(allowed_rows),
            "status_counts": dict(status_counts.most_common()),
            "allowed_quantity_kind_counts": dict(kind_counts.most_common()),
            "risk_counts": dict(risk_counts.most_common()),
            "next_step": "BIZ-2x-9c-9d-generate_area_length_count_suggestion_candidates",
        },
        "mapping_rows": mapping_rows[:MAPPING_LIMIT],
    }


def build_layer_block_mapping_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    unit = report["unit_conversion"]
    lines = [
        "# BIZ-2x-9c0 低风险图层/块名映射报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 分组数：{summary['group_count']}",
        f"- 允许进入建议量探测分组数：{summary['allowed_group_count']}",
        f"- 阻断/待人工映射分组数：{summary['blocked_group_count']}",
        f"- 绘图单位：`{unit.get('drawing_unit', '-')}`；长度换算系数：{unit.get('unit_to_meter_factor', '-')}",
        f"- 面积换算系数：{unit.get('area_to_square_meter_factor', '-')}",
        f"- 是否可直接自动生成最终工程量：{'是' if report['safe_for_auto_quantity'] else '否'}",
        "",
        "## 状态统计",
        "",
    ]
    for name, count in summary["status_counts"].items():
        lines.append(f"- `{name}`：{count}")
    lines.extend(["", "## 允许进入建议量探测的图层/块名", ""])
    allowed = [row for row in report["mapping_rows"] if row["allow_quantity_candidate_probe"]]
    if not allowed:
        lines.append("- 暂无")
    for row in allowed[:40]:
        block = f" / 块 `{row['block_name']}`" if row["block_name"] else ""
        lines.append(
            f"- {row['quantity_kind']} | {row['candidate_type']} | 图层 `{row['layer']}`{block} | 数量 {row['candidate_count']} | 风险 `{row['risk_level']}` | {row['business_hint']}"
        )
    lines.extend(["", "## 结论", ""])
    lines.append("- 本报告只做图层/块名映射，不计算工程量。")
    lines.append("- 后续 BIZ-2x-9c/9d/9e 可以只对低风险分组生成建议量，并且每条建议量仍需 `calculation_trace` 与人工确认。")
    lines.append("- 被排除或待人工映射的图层/块名不得进入建议量计算。")
    return "\n".join(lines) + "\n"


def build_layer_block_mapping_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report.get("mapping_rows", []):
        rows.append(
            {
                "状态": row.get("mapping_status", ""),
                "是否允许进入建议量探测": "是" if row.get("allow_quantity_candidate_probe") else "否",
                "风险等级": row.get("risk_level", ""),
                "建议量类型": row.get("quantity_kind", ""),
                "候选类型": row.get("candidate_type", ""),
                "文件名": row.get("source_file", ""),
                "图层": row.get("layer", ""),
                "块名": row.get("block_name", ""),
                "候选数量": row.get("candidate_count", ""),
                "业务提示": row.get("business_hint", ""),
                "匹配原因": row.get("matched_reason", ""),
                "阻断原因": "；".join(row.get("block_reasons", [])),
                "后续阶段": row.get("next_phase", ""),
            }
        )
    return rows


def write_layer_block_mapping_outputs(report: dict[str, Any], output_dir: str | Path, *, stem: str | None = None) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x9c0_低风险图层块名映射_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    md_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}_映射清单.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_layer_block_mapping_markdown(report), encoding="utf-8")
    _write_csv(csv_path, build_layer_block_mapping_csv_rows(report))
    return {"json": str(json_path), "markdown": str(md_path), "mapping_csv": str(csv_path)}


def _group_geometry_candidates(geometry_report: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for file_item in geometry_report.get("files", []):
        source_file = file_item.get("file_name", "")
        for candidate_type, key in [
            ("面积候选", "area_candidates"),
            ("长度候选", "length_candidates"),
            ("数量候选", "count_candidates"),
        ]:
            for candidate in file_item.get(key, []):
                layer = str(candidate.get("layer") or "")
                block_name = str(candidate.get("block_name") or "")
                group_key = (source_file, candidate_type, layer, block_name)
                group = groups.setdefault(
                    group_key,
                    {
                        "source_file": source_file,
                        "candidate_type": candidate_type,
                        "layer": layer,
                        "block_name": block_name,
                        "candidate_count": 0,
                        "sample_entities": Counter(),
                        "sample_line_numbers": [],
                    },
                )
                group["candidate_count"] += int(candidate.get("count") or 1)
                group["sample_entities"].update([str(candidate.get("entity_type") or "")])
                if len(group["sample_line_numbers"]) < 5:
                    group["sample_line_numbers"].append(candidate.get("line_number", ""))
    for group in groups.values():
        group["sample_entities"] = dict(group["sample_entities"].most_common())
    return list(groups.values())


def _classify_group(group: dict[str, Any]) -> dict[str, Any]:
    layer = group["layer"]
    block_name = group["block_name"]
    text = f"{layer} {block_name}"
    candidate_type = group["candidate_type"]
    block_reasons = _block_reasons(layer, block_name)
    quantity_kind = ""
    business_hint = ""
    matched_reason = ""
    risk_level = "high"
    next_phase = ""
    mapping_status = "blocked_excluded_geometry"

    if candidate_type == "面积候选" and not block_reasons:
        quantity_kind, business_hint, matched_reason, next_phase = _area_mapping(text)
    elif candidate_type == "长度候选" and not block_reasons:
        quantity_kind, business_hint, matched_reason, next_phase = _length_mapping(text)
    elif candidate_type == "数量候选" and not block_reasons:
        quantity_kind, business_hint, matched_reason, next_phase = _count_mapping(text, block_name)

    if quantity_kind:
        mapping_status = "allowed_low_risk_mapping"
        risk_level = "low"
    elif not block_reasons:
        mapping_status = "needs_manual_layer_block_mapping"
        risk_level = "medium"
        block_reasons = ["layer_or_block_not_in_low_risk_dictionary"]

    return {
        **group,
        "source_key": "|".join([group["source_file"], candidate_type, layer, block_name]),
        "quantity_kind": quantity_kind,
        "business_hint": business_hint,
        "matched_reason": matched_reason,
        "block_reasons": block_reasons,
        "mapping_status": mapping_status,
        "allow_quantity_candidate_probe": mapping_status == "allowed_low_risk_mapping",
        "risk_level": risk_level,
        "next_phase": next_phase,
        "status_sort": {"allowed_low_risk_mapping": 0, "needs_manual_layer_block_mapping": 1, "blocked_excluded_geometry": 2}[mapping_status],
    }


def _area_mapping(text: str) -> tuple[str, str, str, str]:
    if any(keyword in text for keyword in LIGHTING_KEYWORDS):
        return "", "", "", ""
    if any(keyword in text for keyword in ("地面", "楼地面", "地台")):
        return "area", "地面/楼地面面积候选", "图层/块名包含地面或地台关键词", "BIZ-2x-9c"
    if any(keyword in text for keyword in ("顶面", "天花", "吊顶")):
        return "area", "天棚/吊顶面积候选", "图层/块名包含顶面/天花/吊顶关键词", "BIZ-2x-9c"
    if "防水" in text:
        return "area", "防水面积候选", "图层/块名包含防水关键词", "BIZ-2x-9c"
    return "", "", "", ""


def _length_mapping(text: str) -> tuple[str, str, str, str]:
    if "踢脚" in text:
        return "length", "踢脚线长度候选", "图层/块名包含踢脚关键词", "BIZ-2x-9d"
    if any(keyword in text for keyword in ("线脚", "线条", "里面线条", "立面线条")):
        return "length", "装饰线条长度候选", "图层/块名包含线脚/线条关键词", "BIZ-2x-9d"
    if "窗帘" in text:
        return "length", "窗帘盒/窗帘长度候选", "图层/块名包含窗帘关键词", "BIZ-2x-9d"
    if "门套" in text:
        return "length", "门套长度候选", "图层/块名包含门套关键词", "BIZ-2x-9d"
    return "", "", "", ""


def _count_mapping(text: str, block_name: str) -> tuple[str, str, str, str]:
    if not block_name:
        return "", "", "", ""
    if "窗帘" in text:
        return "", "", "", ""
    if any(keyword in text for keyword in ("平面门", "门")) and not any(keyword in text for keyword in ("门拉手", "轴号")):
        return "count", "门数量候选", "图层/块名包含门关键词", "BIZ-2x-9e"
    if "窗" in text:
        return "count", "窗数量候选", "图层/块名包含窗关键词", "BIZ-2x-9e"
    if any(keyword in text for keyword in ("灯具", "射灯", "筒灯", "吸顶灯", "600x600")):
        return "count", "灯具数量候选", "图层/块名包含灯具关键词", "BIZ-2x-9e"
    if any(keyword in text for keyword in ("洁具", "地漏")):
        return "count", "洁具/地漏数量候选", "图层/块名包含洁具/地漏关键词", "BIZ-2x-9e"
    if "插座" in text:
        return "count", "插座数量候选", "图层/块名包含插座关键词", "BIZ-2x-9e"
    if "开关" in text:
        return "count", "开关数量候选", "图层/块名包含开关关键词", "BIZ-2x-9e"
    return "", "", "", ""


def _block_reasons(layer: str, block_name: str) -> list[str]:
    reasons: list[str] = []
    text = f"{layer} {block_name}"
    if layer in GENERIC_LAYER_NAMES:
        reasons.append("generic_or_zero_layer")
    if any(keyword in text for keyword in EXCLUDE_LAYER_KEYWORDS):
        reasons.append("annotation_frame_index_or_text_layer")
    if any(keyword in text for keyword in EXCLUDE_BLOCK_KEYWORDS):
        reasons.append("dimension_tick_axis_or_annotation_block")
    if block_name.startswith("*"):
        reasons.append("anonymous_block_needs_mapping")
    if block_name.startswith("A$C"):
        reasons.append("generated_block_name_needs_mapping")
    return reasons


def _area_factor(confirmation_report: dict[str, Any]) -> float:
    factor = float(confirmation_report.get("manual_confirmation", {}).get("unit_to_meter_factor", 0) or 0)
    return round(factor * factor, 8)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
