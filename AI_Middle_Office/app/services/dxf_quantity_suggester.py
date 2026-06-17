from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


class DxfQuantitySuggestionError(ValueError):
    pass


MIN_REVIEWABLE_AREA_SQM = 0.01
MIN_REVIEWABLE_LENGTH_M = 0.05


def load_json_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path)
    if not report_path.exists():
        raise DxfQuantitySuggestionError(f"Report not found: {report_path}")
    return json.loads(report_path.read_text(encoding="utf-8"))


def build_low_risk_quantity_suggestion_report(
    *,
    geometry_report: dict[str, Any],
    mapping_report: dict[str, Any],
) -> dict[str, Any]:
    if not mapping_report.get("ready_for_geometry_quantity_probe"):
        raise DxfQuantitySuggestionError("Layer/block mapping is not ready for quantity suggestion probe.")

    unit_conversion = mapping_report.get("unit_conversion", {})
    unit_to_meter_factor = float(unit_conversion.get("unit_to_meter_factor") or 0)
    area_to_square_meter_factor = float(unit_conversion.get("area_to_square_meter_factor") or 0)
    if unit_to_meter_factor <= 0:
        raise DxfQuantitySuggestionError("Missing valid unit_to_meter_factor.")
    if area_to_square_meter_factor <= 0:
        raise DxfQuantitySuggestionError("Missing valid area_to_square_meter_factor.")

    candidates_by_key = _index_geometry_candidates(geometry_report)
    suggestions: list[dict[str, Any]] = []
    for mapping in mapping_report.get("mapping_rows", []):
        if not mapping.get("allow_quantity_candidate_probe"):
            continue
        source_key = mapping.get("source_key", "")
        candidates = candidates_by_key.get(source_key, [])
        suggestion = _build_group_suggestion(
            mapping=mapping,
            candidates=candidates,
            unit_to_meter_factor=unit_to_meter_factor,
            area_to_square_meter_factor=area_to_square_meter_factor,
        )
        if suggestion:
            suggestions.append(suggestion)

    kind_counts = Counter(item["quantity_kind"] for item in suggestions)
    blocked_count = sum(1 for item in suggestions if item["suggestion_status"] != "suggestion_ready_for_manual_review")
    return {
        "ok": True,
        "phase": "BIZ-2x-9cde-low-risk-geometry-quantity-suggestions",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_auto_quantity": False,
        "standard_quantity_rule_applied": False,
        "summary": {
            "suggestion_count": len(suggestions),
            "ready_for_manual_review_count": len(suggestions) - blocked_count,
            "blocked_suggestion_count": blocked_count,
            "quantity_kind_counts": dict(kind_counts.most_common()),
            "unit_conversion": unit_conversion,
            "risk_flags": [
                "geometry_suggestions_not_final_quantity",
                "standard_quantity_rule_not_applied_yet",
                "manual_review_required_before_final_list",
                "deduction_rules_not_yet_applied",
            ],
            "next_step": "BIZ-2x-9f-9g-bind_standard_items_and_generate_standard_rule_trace",
        },
        "suggestions": suggestions,
    }


def build_quantity_suggestion_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-9c/9d/9e 低风险几何建议量报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 建议量条数：{summary['suggestion_count']}",
        f"- 可进入人工复核条数：{summary['ready_for_manual_review_count']}",
        f"- 阻断条数：{summary['blocked_suggestion_count']}",
        f"- 是否已套用 GB/T 标准工程量规则：{'是' if report['standard_quantity_rule_applied'] else '否'}",
        f"- 是否可直接生成最终工程量：{'是' if report['safe_for_auto_quantity'] else '否'}",
        "",
        "## 类型统计",
        "",
    ]
    for name, count in summary["quantity_kind_counts"].items():
        lines.append(f"- `{name}`：{count}")
    lines.extend(["", "## 建议量样例", ""])
    for item in report["suggestions"][:40]:
        lines.append(
            f"- {item['quantity_kind']} | {item['source_file']} | 图层 `{item['layer']}`"
            f"{' / 块 `' + item['block_name'] + '`' if item['block_name'] else ''}"
            f" | 建议量 {item['suggested_quantity']} {item['suggested_unit']} | {item['business_hint']}"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- 本报告只生成 CAD 几何建议量，不是最终工程量清单。",
            "- 所有建议量均需人工复核，并在后续绑定 active GB/T 标准项目后，按标准库 `quantity_rule` 生成标准计算追溯。",
            "- 扣减规则、材料/做法关联、标准项目绑定完成前，不得写入最终四字段 Excel。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_quantity_suggestion_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report.get("suggestions", []):
        rows.append(
            {
                "建议编号": item.get("suggestion_key", ""),
                "状态": item.get("suggestion_status", ""),
                "建议量类型": item.get("quantity_kind", ""),
                "文件名": item.get("source_file", ""),
                "图层": item.get("layer", ""),
                "块名": item.get("block_name", ""),
                "业务提示": item.get("business_hint", ""),
                "建议量": item.get("suggested_quantity", ""),
                "单位": item.get("suggested_unit", ""),
                "原始CAD合计": item.get("raw_total", ""),
                "换算系数": item.get("conversion_factor", ""),
                "使用候选数": item.get("used_candidate_count", ""),
                "跳过候选数": item.get("skipped_candidate_count", ""),
                "公式": item.get("formula", ""),
                "标准规则状态": item.get("standard_rule_status", ""),
                "是否最终工程量": "是" if item.get("is_final_quantity") else "否",
                "是否需要人工复核": "是" if item.get("requires_manual_review") else "否",
                "风险提示": "；".join(item.get("risk_flags", [])),
                "追溯": json.dumps(item.get("calculation_trace", {}), ensure_ascii=False),
            }
        )
    return rows


def write_quantity_suggestion_outputs(report: dict[str, Any], output_dir: str | Path, *, stem: str | None = None) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x9cde_低风险几何建议量_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    md_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}_建议量清单.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_quantity_suggestion_markdown(report), encoding="utf-8")
    _write_csv(csv_path, build_quantity_suggestion_csv_rows(report))
    return {"json": str(json_path), "markdown": str(md_path), "suggestion_csv": str(csv_path)}


def _index_geometry_candidates(geometry_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
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
                source_key = "|".join([source_file, candidate_type, layer, block_name])
                indexed.setdefault(source_key, []).append(candidate)
    return indexed


def _build_group_suggestion(
    *,
    mapping: dict[str, Any],
    candidates: list[dict[str, Any]],
    unit_to_meter_factor: float,
    area_to_square_meter_factor: float,
) -> dict[str, Any] | None:
    quantity_kind = mapping.get("quantity_kind", "")
    if quantity_kind == "area":
        return _area_suggestion(mapping, candidates, area_to_square_meter_factor)
    if quantity_kind == "length":
        return _length_suggestion(mapping, candidates, unit_to_meter_factor)
    if quantity_kind == "count":
        return _count_suggestion(mapping, candidates)
    return None


def _area_suggestion(mapping: dict[str, Any], candidates: list[dict[str, Any]], factor: float) -> dict[str, Any]:
    raw_values = [_float_or_none(candidate.get("area")) for candidate in candidates]
    positive_values = [value for value in raw_values if value is not None and value > 0]
    used_values = [value for value in positive_values if value * factor >= MIN_REVIEWABLE_AREA_SQM]
    tiny_candidate_count = len(positive_values) - len(used_values)
    raw_total = sum(used_values)
    suggested = raw_total * factor
    return _base_suggestion(
        mapping,
        candidates,
        quantity_kind="area",
        suggested_unit="㎡",
        raw_total=raw_total,
        suggested_quantity=suggested,
        conversion_factor=factor,
        formula="sum(CAD_area_mm2) * area_to_square_meter_factor",
        used_candidate_count=len(used_values),
        skipped_candidate_count=len(candidates) - len(used_values),
        extra_risks=[
            "deduction_rules_not_applied",
            "hatch_area_skipped_if_boundary_not_calculated",
            "tiny_area_geometry_filtered",
        ],
        minimum_reviewable_quantity=MIN_REVIEWABLE_AREA_SQM,
        tiny_candidate_count=tiny_candidate_count,
    )


def _length_suggestion(mapping: dict[str, Any], candidates: list[dict[str, Any]], factor: float) -> dict[str, Any]:
    raw_values = [_float_or_none(candidate.get("length")) for candidate in candidates]
    positive_values = [value for value in raw_values if value is not None and value > 0]
    used_values = [value for value in positive_values if value * factor >= MIN_REVIEWABLE_LENGTH_M]
    tiny_candidate_count = len(positive_values) - len(used_values)
    raw_total = sum(used_values)
    suggested = raw_total * factor
    return _base_suggestion(
        mapping,
        candidates,
        quantity_kind="length",
        suggested_unit="m",
        raw_total=raw_total,
        suggested_quantity=suggested,
        conversion_factor=factor,
        formula="sum(CAD_length_mm) * unit_to_meter_factor",
        used_candidate_count=len(used_values),
        skipped_candidate_count=len(candidates) - len(used_values),
        extra_risks=[
            "deduction_rules_not_applied",
            "line_overlap_or_duplicate_not_resolved",
            "tiny_length_geometry_filtered",
        ],
        minimum_reviewable_quantity=MIN_REVIEWABLE_LENGTH_M,
        tiny_candidate_count=tiny_candidate_count,
    )


def _count_suggestion(mapping: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    raw_values = [_int_or_one(candidate.get("count")) for candidate in candidates]
    raw_total = sum(raw_values)
    return _base_suggestion(
        mapping,
        candidates,
        quantity_kind="count",
        suggested_unit="个",
        raw_total=raw_total,
        suggested_quantity=float(raw_total),
        conversion_factor=1.0,
        formula="sum(INSERT_count)",
        used_candidate_count=len(candidates),
        skipped_candidate_count=0,
        extra_risks=["block_symbol_mapping_requires_manual_review"],
        minimum_reviewable_quantity=1.0,
        tiny_candidate_count=0,
    )


def _base_suggestion(
    mapping: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    quantity_kind: str,
    suggested_unit: str,
    raw_total: float,
    suggested_quantity: float,
    conversion_factor: float,
    formula: str,
    used_candidate_count: int,
    skipped_candidate_count: int,
    extra_risks: list[str],
    minimum_reviewable_quantity: float,
    tiny_candidate_count: int,
) -> dict[str, Any]:
    status = (
        "suggestion_ready_for_manual_review"
        if used_candidate_count > 0 and suggested_quantity >= minimum_reviewable_quantity
        else "blocked_no_usable_geometry_value"
    )
    risk_flags = [
        "not_final_quantity",
        "manual_review_required",
        "pending_standard_quantity_rule_binding",
        *extra_risks,
    ]
    if skipped_candidate_count:
        risk_flags.append("some_candidates_skipped_due_to_missing_numeric_value")
    if tiny_candidate_count:
        risk_flags.append("tiny_geometry_filtered_below_review_threshold")
    return {
        "suggestion_key": "BIZ2x9cde-" + hashlib.sha1(str(mapping.get("source_key", "")).encode("utf-8")).hexdigest()[:10],
        "source_key": mapping.get("source_key", ""),
        "source_file": mapping.get("source_file", ""),
        "candidate_type": mapping.get("candidate_type", ""),
        "quantity_kind": quantity_kind,
        "layer": mapping.get("layer", ""),
        "block_name": mapping.get("block_name", ""),
        "business_hint": mapping.get("business_hint", ""),
        "suggestion_status": status,
        "suggested_quantity": _round_quantity(suggested_quantity),
        "suggested_unit": suggested_unit,
        "raw_total": _round_quantity(raw_total),
        "conversion_factor": conversion_factor,
        "formula": formula,
        "used_candidate_count": used_candidate_count,
        "skipped_candidate_count": skipped_candidate_count,
        "raw_candidate_count": len(candidates),
        "tiny_candidate_count": tiny_candidate_count,
        "minimum_reviewable_quantity": minimum_reviewable_quantity,
        "standard_rule_status": "pending_standard_item_rule_binding",
        "standard_quantity_rule_applied": False,
        "is_final_quantity": False,
        "requires_manual_review": True,
        "risk_flags": risk_flags,
        "calculation_trace": {
            "source_key": mapping.get("source_key", ""),
            "mapping_business_hint": mapping.get("business_hint", ""),
            "matched_reason": mapping.get("matched_reason", ""),
            "input_candidate_count": len(candidates),
            "used_candidate_count": used_candidate_count,
            "skipped_candidate_count": skipped_candidate_count,
            "tiny_candidate_count": tiny_candidate_count,
            "minimum_reviewable_quantity": minimum_reviewable_quantity,
            "raw_total": _round_quantity(raw_total),
            "conversion_factor": conversion_factor,
            "formula": formula,
            "result": _round_quantity(suggested_quantity),
            "result_unit": suggested_unit,
            "sample_line_numbers": [candidate.get("line_number", "") for candidate in candidates[:10]],
            "sample_entity_types": dict(Counter(str(candidate.get("entity_type") or "") for candidate in candidates).most_common()),
        },
    }


def _float_or_none(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_one(value: Any) -> int:
    try:
        parsed = int(float(value))
        return parsed if parsed > 0 else 1
    except (TypeError, ValueError):
        return 1


def _round_quantity(value: float) -> float:
    return round(value, 4)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
