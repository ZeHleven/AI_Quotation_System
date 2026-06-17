from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.quantity_standard_library import (
    ACTIVE_STATUS,
    QuantityStandardItem,
    QuantityStandardLibrary,
    quantity_standard_summary,
)


class DxfStandardRuleBindingError(ValueError):
    pass


AREA_COMPATIBLE_FORMULAS = {"area"}
LENGTH_COMPATIBLE_FORMULAS = {"length"}
COUNT_COMPATIBLE_FORMULAS = {"count"}
EXPANDED_AREA_FORMULAS = {"expanded_area"}
OUT_OF_SCOPE_TERMS = (
    "插座",
    "开关",
    "洁具",
    "地漏",
    "水龙头",
    "座便器",
    "坐便器",
    "洗脸盆",
    "淋浴",
    "花洒",
    "毛巾",
    "手纸",
    "水槽",
    "燃气灶",
    "冰箱",
    "接驳点",
)


def load_json_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path)
    if not report_path.exists():
        raise DxfStandardRuleBindingError(f"Report not found: {report_path}")
    return json.loads(report_path.read_text(encoding="utf-8"))


def build_standard_rule_binding_report(
    *,
    quantity_suggestion_report: dict[str, Any],
    standard_match_report: dict[str, Any],
    library: QuantityStandardLibrary,
) -> dict[str, Any]:
    if not quantity_suggestion_report.get("ok"):
        raise DxfStandardRuleBindingError("Quantity suggestion report is not ok.")
    if not standard_match_report.get("ok"):
        raise DxfStandardRuleBindingError("Standard match report is not ok.")

    active_items = [item for item in library.items if item.status == ACTIVE_STATUS]
    items_by_code = {item.item_code: item for item in active_items}
    match_codes_by_topic = _build_match_codes_by_topic(standard_match_report, items_by_code)

    bindings: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    for suggestion in quantity_suggestion_report.get("suggestions", []):
        binding = _bind_one_suggestion(suggestion, items_by_code, match_codes_by_topic)
        bindings.append(binding)
        trace_rows.extend(binding["standard_rule_traces"])

    binding_status_counts = Counter(row["binding_status"] for row in bindings)
    trace_status_counts = Counter(row["trace_status"] for row in trace_rows)
    compatible_trace_count = sum(1 for row in trace_rows if row["trace_status"] == "standard_rule_trace_ready_for_manual_review")
    bound_suggestion_count = sum(1 for row in bindings if row["standard_candidate_count"] > 0)
    final_ready_count = sum(1 for row in trace_rows if row.get("is_final_quantity"))

    return {
        "ok": True,
        "phase": "BIZ-2x-9f-9g-standard-item-binding-and-rule-trace",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "standard_quantity_rule_trace_generated": compatible_trace_count > 0,
        "standard_library_summary": quantity_standard_summary(library),
        "source_summary": {
            "quantity_suggestion_summary": quantity_suggestion_report.get("summary", {}),
            "standard_match_summary": standard_match_report.get("summary", {}),
        },
        "summary": {
            "suggestion_count": len(bindings),
            "bound_suggestion_count": bound_suggestion_count,
            "unbound_suggestion_count": len(bindings) - bound_suggestion_count,
            "standard_rule_trace_count": len(trace_rows),
            "compatible_standard_rule_trace_count": compatible_trace_count,
            "final_ready_count": final_ready_count,
            "binding_status_counts": dict(binding_status_counts.most_common()),
            "trace_status_counts": dict(trace_status_counts.most_common()),
            "risk_flags": [
                "standard_rule_trace_not_final_quantity",
                "manual_review_required_before_final_list",
                "feature_values_and_material_method_binding_pending",
                "deduction_rules_need_manual_review",
            ],
            "next_step": "BIZ-2x-9h-feed_rule_trace_into_manual_confirmation_and_sample_regression",
        },
        "bindings": bindings,
        "standard_rule_traces": trace_rows,
    }


def build_standard_rule_binding_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-9f/9g 标准项目绑定与标准规则 trace 报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 几何建议量条数：{summary['suggestion_count']}",
        f"- 已找到标准候选的建议量：{summary['bound_suggestion_count']}",
        f"- 未绑定标准候选的建议量：{summary['unbound_suggestion_count']}",
        f"- 标准规则 trace 行数：{summary['standard_rule_trace_count']}",
        f"- 可进入人工复核的标准规则 trace：{summary['compatible_standard_rule_trace_count']}",
        f"- 可直接进入最终清单条数：{summary['final_ready_count']}",
        f"- 是否可直接生成最终工程量：{'是' if report['safe_for_final_quantity_list'] else '否'}",
        "",
        "## 绑定状态统计",
        "",
    ]
    for status, count in summary["binding_status_counts"].items():
        lines.append(f"- `{status}`：{count}")
    lines.extend(["", "## 标准规则 trace 状态统计", ""])
    for status, count in summary["trace_status_counts"].items():
        lines.append(f"- `{status}`：{count}")
    lines.extend(
        [
            "",
            "## 绑定样例",
            "",
            "| 建议编号 | 来源 | 建议量 | 绑定状态 | 标准候选 | 说明 |",
            "| --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in report["bindings"][:80]:
        candidates = "；".join(
            f"{item['item_code']} {item['item_name']}({item['trace_status']})" for item in row["standard_rule_traces"][:3]
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["suggestion_key"]),
                    _md(f"{row['source_file']} / {row['layer']} / {row['block_name']}"),
                    _md(f"{row['suggested_quantity']} {row['suggested_unit']}"),
                    _md(row["binding_status"]),
                    _md(candidates or "-"),
                    _md("；".join(row["binding_notes"][:3])),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- 本报告已经把部分 CAD 几何建议量绑定到 active GB/T 标准项目，并引用标准库 `quantity_rule` 生成 trace。",
            "- 这些 trace 仍是人工复核输入，不是最终工程量；项目特征值、材料/做法关联、扣减规则和标准项目选择未完成前，不得导出最终四字段 Excel。",
            "- 插座、开关、洁具、普通灯具等样例多属于机电/安装或家具设备范围，当前不强行套用 GB/T 50854 房屋建筑与装饰标准。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_binding_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report.get("bindings", []):
        rows.append(
            {
                "建议编号": row.get("suggestion_key", ""),
                "绑定状态": row.get("binding_status", ""),
                "文件名": row.get("source_file", ""),
                "图层": row.get("layer", ""),
                "块名": row.get("block_name", ""),
                "业务提示": row.get("business_hint", ""),
                "建议量类型": row.get("quantity_kind", ""),
                "建议量": row.get("suggested_quantity", ""),
                "单位": row.get("suggested_unit", ""),
                "标准候选数": row.get("standard_candidate_count", ""),
                "可复核trace数": row.get("compatible_trace_count", ""),
                "是否最终工程量": "是" if row.get("is_final_quantity") else "否",
                "说明": "；".join(row.get("binding_notes", [])),
                "风险提示": "；".join(row.get("risk_flags", [])),
            }
        )
    return rows


def build_trace_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report.get("standard_rule_traces", []):
        rows.append(
            {
                "建议编号": row.get("suggestion_key", ""),
                "trace状态": row.get("trace_status", ""),
                "标准项目编码": row.get("item_code", ""),
                "标准项目名称": row.get("item_name", ""),
                "标准单位": "、".join(row.get("unit_options", [])),
                "标准规则类型": row.get("quantity_formula_type", ""),
                "标准工程量计算规则": row.get("quantity_rule_text", ""),
                "几何建议量": row.get("geometry_quantity", ""),
                "几何单位": row.get("geometry_unit", ""),
                "标准规则计算建议量": row.get("standard_rule_suggested_quantity", ""),
                "建议单位": row.get("suggested_unit", ""),
                "是否可进入人工复核": "是" if row.get("ready_for_manual_review") else "否",
                "是否最终工程量": "是" if row.get("is_final_quantity") else "否",
                "阻断原因": row.get("block_reason", ""),
                "未解决事项": "；".join(row.get("unresolved_requirements", [])),
                "追溯": json.dumps(row.get("calculation_trace", {}), ensure_ascii=False),
            }
        )
    return rows


def write_standard_rule_binding_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x9fg_标准规则绑定trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    md_path = target_dir / f"{file_stem}.md"
    binding_csv_path = target_dir / f"{file_stem}_绑定清单.csv"
    trace_csv_path = target_dir / f"{file_stem}_标准规则trace.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_standard_rule_binding_markdown(report), encoding="utf-8")
    _write_csv(binding_csv_path, build_binding_csv_rows(report))
    _write_csv(trace_csv_path, build_trace_csv_rows(report))
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "binding_csv": str(binding_csv_path),
        "trace_csv": str(trace_csv_path),
    }


def _bind_one_suggestion(
    suggestion: dict[str, Any],
    items_by_code: dict[str, QuantityStandardItem],
    match_codes_by_topic: dict[str, list[str]],
) -> dict[str, Any]:
    text = _suggestion_search_text(suggestion)
    notes: list[str] = []
    risk_flags = [
        "not_final_quantity",
        "manual_review_required",
        "pending_feature_values_and_material_method_binding",
        "pending_deduction_rule_review",
    ]

    if suggestion.get("suggestion_status") != "suggestion_ready_for_manual_review":
        notes.append("几何建议量本身未就绪，不能绑定标准规则。")
        return _base_binding(suggestion, "blocked_source_suggestion_not_ready", notes, risk_flags, [])

    if _is_out_of_scope(text):
        notes.append("该图层/块名更接近机电、洁具、家具或设备，不强行套用 GB/T 50854 建筑装饰标准。")
        risk_flags.append("out_of_gbt50854_building_decoration_scope")
        return _base_binding(suggestion, "blocked_out_of_scope_or_no_active_standard_candidate", notes, risk_flags, [])

    target_codes, source_note = _target_standard_codes(suggestion, text, match_codes_by_topic, items_by_code)
    notes.append(source_note)
    standard_items = [items_by_code[code] for code in target_codes if code in items_by_code]
    if not standard_items:
        notes.append("active 标准库中未找到可信标准项目候选。")
        risk_flags.append("no_active_standard_candidate")
        return _base_binding(suggestion, "blocked_out_of_scope_or_no_active_standard_candidate", notes, risk_flags, [])

    traces = [_build_trace_row(suggestion, item) for item in standard_items]
    compatible_count = sum(1 for item in traces if item["trace_status"] == "standard_rule_trace_ready_for_manual_review")
    if compatible_count == 0:
        status = "blocked_standard_rule_incompatible_with_geometry_kind"
        risk_flags.append("standard_quantity_rule_incompatible_with_geometry_kind")
        notes.append("找到标准项目候选，但标准库工程量规则与当前几何建议量类型不一致。")
    elif compatible_count == 1 and len(traces) == 1:
        status = "standard_rule_trace_ready_for_manual_review"
        notes.append("标准项目与几何建议量类型匹配，可进入人工复核。")
    else:
        status = "blocked_multiple_standard_candidates_need_selection"
        risk_flags.append("multiple_standard_candidates_need_manual_selection")
        notes.append("存在多个标准项目候选，需人工选择标准项目和做法后才能进入最终清单。")
    return _base_binding(suggestion, status, notes, risk_flags, traces)


def _base_binding(
    suggestion: dict[str, Any],
    status: str,
    notes: list[str],
    risk_flags: list[str],
    traces: list[dict[str, Any]],
) -> dict[str, Any]:
    compatible_count = sum(1 for item in traces if item["trace_status"] == "standard_rule_trace_ready_for_manual_review")
    return {
        "suggestion_key": suggestion.get("suggestion_key", ""),
        "source_key": suggestion.get("source_key", ""),
        "source_file": suggestion.get("source_file", ""),
        "layer": suggestion.get("layer", ""),
        "block_name": suggestion.get("block_name", ""),
        "business_hint": suggestion.get("business_hint", ""),
        "quantity_kind": suggestion.get("quantity_kind", ""),
        "suggested_quantity": suggestion.get("suggested_quantity", ""),
        "suggested_unit": suggestion.get("suggested_unit", ""),
        "binding_status": status,
        "standard_candidate_count": len(traces),
        "compatible_trace_count": compatible_count,
        "standard_rule_traces": traces,
        "is_final_quantity": False,
        "requires_manual_review": True,
        "binding_notes": notes,
        "risk_flags": risk_flags,
    }


def _build_trace_row(suggestion: dict[str, Any], item: QuantityStandardItem) -> dict[str, Any]:
    quantity_kind = str(suggestion.get("quantity_kind") or "")
    formula_type = str(item.quantity_rule.get("formula_type") or "")
    quantity = _float_or_none(suggestion.get("suggested_quantity"))
    unit = str(suggestion.get("suggested_unit") or "")
    trace_status, block_reason = _trace_status(quantity_kind, formula_type)
    ready = trace_status == "standard_rule_trace_ready_for_manual_review"
    result_quantity = quantity if ready else None
    unresolved = [
        "standard_item_manual_confirmation",
        "feature_values_need_standard_field_review",
        "material_method_region_association_pending",
        "deduction_and_merge_rules_need_review",
    ]
    if formula_type in EXPANDED_AREA_FORMULAS:
        unresolved.append("expanded_area_requires_more_geometry_than_horizontal_area")
    return {
        "suggestion_key": suggestion.get("suggestion_key", ""),
        "item_code": item.item_code,
        "item_name": item.item_name,
        "chapter_name": item.chapter_name,
        "unit_options": list(item.unit_options),
        "feature_fields": item.feature_names,
        "quantity_formula_type": formula_type,
        "quantity_rule_text": _clean_text(item.quantity_rule.get("rule_text")),
        "quantity_required_evidence": list(item.quantity_rule.get("required_evidence") or []),
        "trace_status": trace_status,
        "ready_for_manual_review": ready,
        "geometry_quantity": quantity,
        "geometry_unit": unit,
        "standard_rule_suggested_quantity": result_quantity,
        "suggested_unit": unit if ready else "",
        "block_reason": block_reason,
        "unresolved_requirements": unresolved,
        "standard_quantity_rule_referenced": True,
        "is_final_quantity": False,
        "requires_manual_review": True,
        "calculation_trace": {
            "standard_item_code": item.item_code,
            "standard_item_name": item.item_name,
            "standard_quantity_rule_text": _clean_text(item.quantity_rule.get("rule_text")),
            "standard_formula_type": formula_type,
            "geometry_source_key": suggestion.get("source_key", ""),
            "geometry_formula": suggestion.get("formula", ""),
            "geometry_input_quantity": quantity,
            "geometry_input_unit": unit,
            "standard_rule_application": "use_geometry_input_as_manual_review_candidate" if ready else "blocked",
            "result_quantity": result_quantity,
            "result_unit": unit if ready else "",
            "source_calculation_trace": suggestion.get("calculation_trace", {}),
        },
    }


def _trace_status(quantity_kind: str, formula_type: str) -> tuple[str, str]:
    if quantity_kind == "area" and formula_type in AREA_COMPATIBLE_FORMULAS:
        return "standard_rule_trace_ready_for_manual_review", ""
    if quantity_kind == "length" and formula_type in LENGTH_COMPATIBLE_FORMULAS:
        return "standard_rule_trace_ready_for_manual_review", ""
    if quantity_kind == "count" and formula_type in COUNT_COMPATIBLE_FORMULAS:
        return "standard_rule_trace_ready_for_manual_review", ""
    if quantity_kind == "area" and formula_type in EXPANDED_AREA_FORMULAS:
        return "blocked_standard_rule_requires_expanded_area", "标准规则要求展开面积，当前 CAD 建议量仅为平面/水平面积候选。"
    if not formula_type:
        return "blocked_missing_standard_quantity_rule", "标准库缺少工程量规则类型。"
    return (
        "blocked_standard_rule_incompatible_with_geometry_kind",
        f"标准规则类型为 {formula_type}，与几何建议量类型 {quantity_kind} 不一致。",
    )


def _target_standard_codes(
    suggestion: dict[str, Any],
    text: str,
    match_codes_by_topic: dict[str, list[str]],
    items_by_code: dict[str, QuantityStandardItem],
) -> tuple[list[str], str]:
    quantity_kind = str(suggestion.get("quantity_kind") or "")
    codes: list[str] = []
    if any(term in text for term in ("天棚", "吊顶", "顶面")):
        codes.extend(match_codes_by_topic.get("ceiling", []))
        codes.extend(["011302001", "011302003", "011404002"])
        return _unique_existing(codes, items_by_code), "按天棚/吊顶图层与 BIZ-2x-4 材料做法候选匹配。"
    if any(term in text for term in ("地面", "楼地面", "地台")):
        codes.extend(match_codes_by_topic.get("floor", []))
        codes.extend(["011102003", "010904002"])
        return _unique_existing(codes, items_by_code), "按地面/楼地面图层与 BIZ-2x-4 材料做法候选匹配。"
    if "窗帘盒" in text:
        return _unique_existing(["010810002"], items_by_code), "按窗帘盒关键词匹配标准项目。"
    if "踢脚" in text:
        codes.extend(match_codes_by_topic.get("baseboard", []))
        codes.extend(["011105006", "011105003", "011105002", "011105005"])
        return _unique_existing(codes, items_by_code), "按踢脚线关键词匹配标准项目。"
    if any(term in text for term in ("线脚", "线条", "装饰线")):
        return _unique_existing(["011502001", "011404004", "011401003", "011403002"], items_by_code), "按装饰线条/线脚关键词匹配标准项目。"
    if "玻璃门" in text:
        return _unique_existing(["010802001", "010801001", "010802004", "010801004"], items_by_code), "按玻璃门/门块名匹配门类标准项目，但需校验面积规则。"
    if "门" in text and quantity_kind == "count":
        return _unique_existing(["010801001", "010802001", "010801004", "010802004"], items_by_code), "按门块名匹配门类标准项目，但当前 CAD 只有数量，标准多按洞口面积计算。"
    if "窗" in text:
        return _unique_existing(["010806001", "010807001", "010807002", "010807003"], items_by_code), "按窗关键词匹配窗类标准项目。"
    return [], "未命中可保守绑定的标准项目关键词。"


def _build_match_codes_by_topic(
    standard_match_report: dict[str, Any],
    items_by_code: dict[str, QuantityStandardItem],
) -> dict[str, list[str]]:
    topics: dict[str, list[str]] = {"ceiling": [], "floor": [], "baseboard": [], "curtain_box": []}
    for row in standard_match_report.get("standard_item_candidates", []):
        code = str(row.get("standard_item_code") or "")
        item = items_by_code.get(code)
        if not item:
            continue
        name = _clean_text(item.item_name)
        if any(term in name for term in ("天棚", "吊顶")):
            topics["ceiling"].append(code)
        if any(term in name for term in ("地面", "楼地面", "楼(地)面", "防水")):
            topics["floor"].append(code)
        if "踢脚" in name:
            topics["baseboard"].append(code)
        if "窗帘盒" in name:
            topics["curtain_box"].append(code)
    return {key: _unique(value) for key, value in topics.items()}


def _is_out_of_scope(text: str) -> bool:
    if "灯具" in text and "灯箱" not in text:
        return True
    return any(term in text for term in OUT_OF_SCOPE_TERMS)


def _suggestion_search_text(suggestion: dict[str, Any]) -> str:
    return _clean_text(
        " ".join(
            [
                str(suggestion.get("source_file") or ""),
                str(suggestion.get("layer") or ""),
                str(suggestion.get("block_name") or ""),
                str(suggestion.get("business_hint") or ""),
            ]
        )
    )


def _unique_existing(codes: list[str], items_by_code: dict[str, QuantityStandardItem]) -> list[str]:
    return [code for code in _unique(codes) if code in items_by_code]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _md(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
