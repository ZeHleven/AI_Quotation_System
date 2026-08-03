from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.drawing_standard_rule_executor import execute_standard_quantity_rule


TRACE_ROW_HEADERS = [
    "专项算量编号",
    "识别项目编号",
    "标准项目编码",
    "项目名称",
    "图纸项目名称",
    "专项类型",
    "建议工程量",
    "建议单位",
    "trace状态",
    "是否可复核",
    "标准工程量计算规则",
    "标准规则模板",
    "标准规则执行状态",
    "计算公式",
    "计算输入",
    "区域编号",
    "房间编号",
    "房间/空间名称",
    "阻断原因",
    "未解决事项",
]


AREA_TYPES = {
    "ceiling_area": "吊顶/天棚水平投影面积",
    "floor_area": "地面闭合区域面积",
    "ceiling_paint_area": "天棚喷刷涂料面积",
}


def build_special_quantity_calculation_report(
    *,
    project_report: dict[str, Any],
    project_region_binding_report: dict[str, Any],
    room_boundary_report: dict[str, Any],
    standard_match_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    standard_index = _standard_index(standard_match_report or {})
    region_bindings = {
        str(row.get("识别项目编号") or ""): row
        for row in project_region_binding_report.get("binding_rows", [])
        if row.get("识别项目编号")
    }
    rooms_by_region = {
        str(row.get("绑定区域编号") or ""): row
        for row in room_boundary_report.get("room_rows", [])
        if row.get("绑定区域编号")
    }

    traces: list[dict[str, Any]] = []
    for index, project in enumerate(project_report.get("project_rows") or [], start=1):
        binding = region_bindings.get(str(project.get("识别项目编号") or ""))
        room = rooms_by_region.get(str((binding or {}).get("推荐区域编号") or ""))
        standard = standard_index.get(str(project.get("标准项目编码") or "")) or {}
        trace = _build_project_trace(index, project, binding, room, standard)
        traces.append(trace)

    status_counts = Counter(row["trace状态"] for row in traces)
    type_counts = Counter(row["专项类型"] for row in traces)
    rule_status_counts = Counter(row.get("标准规则执行状态") or "missing_standard_rule_execution" for row in traces)
    template_counts = Counter(row.get("标准规则模板") or "missing_standard_rule_template" for row in traces)
    ready_count = sum(1 for row in traces if row["是否可复核"] == "是")
    return {
        "ok": True,
        "phase": "BIZ-2x-special-quantity-calculation-trace",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "recognized_project_count": len(project_report.get("project_rows") or []),
            "special_quantity_trace_count": len(traces),
            "ready_for_manual_review_count": ready_count,
            "blocked_trace_count": len(traces) - ready_count,
            "final_ready_count": 0,
            "trace_status_counts": dict(status_counts.most_common()),
            "special_type_counts": dict(type_counts.most_common()),
            "standard_rule_execution_status_counts": dict(rule_status_counts.most_common()),
            "standard_rule_template_counts": dict(template_counts.most_common()),
            "final_generation_status": "blocked_until_special_trace_review_and_final_export",
            "next_step": "review_special_quantity_traces_then_generate_four_field_excel",
        },
        "special_quantity_trace_rows": traces,
        "notes": [
            "本报告把项目-区域绑定和房间边界证据转换为专项算量 trace，不直接生成最终工程量。",
            "所有可复核建议量仍需确认项目特征值、扣减/并入规则和标准项目适用性。",
            "缺少区域、净周长、防水高度或墙面展开面积证据的项目会被阻断，不写入最终四字段清单。",
        ],
    }


def write_special_quantity_calculation_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_专项算量trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    trace_csv_path = target_dir / f"{file_stem}_专项算量trace.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_special_quantity_calculation_markdown(report), encoding="utf-8")
    _write_csv(trace_csv_path, report.get("special_quantity_trace_rows") or [], TRACE_ROW_HEADERS)
    return {"json": str(json_path), "markdown": str(markdown_path), "trace_csv": str(trace_csv_path)}


def build_special_quantity_calculation_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x 专项算量 trace 报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 项目数：{summary.get('recognized_project_count', 0)}",
        f"- 专项 trace：{summary.get('special_quantity_trace_count', 0)}",
        f"- 可复核 trace：{summary.get('ready_for_manual_review_count', 0)}",
        f"- 阻断 trace：{summary.get('blocked_trace_count', 0)}",
        "",
        "## 专项算量 trace",
        "",
        "| 编号 | 项目 | 类型 | 状态 | 建议量 | 公式 | 阻断原因 |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in (report.get("special_quantity_trace_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("专项算量编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("专项类型")),
                    _md(row.get("trace状态")),
                    _md(f"{row.get('建议工程量')}{row.get('建议单位')}"),
                    _md(row.get("计算公式")),
                    _md(row.get("阻断原因")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告不直接生成最终工程量清单。",
            "- `是否可复核=是` 代表证据链具备人工复核条件，不代表可以免审下发报价。",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_project_trace(
    index: int,
    project: dict[str, Any],
    binding: dict[str, Any] | None,
    room: dict[str, Any] | None,
    standard: dict[str, Any],
) -> dict[str, Any]:
    special_type = _special_type(project)
    base = _base_trace(index, project, binding, room, standard, special_type)
    if not binding or binding.get("区域绑定状态") != "建议绑定区域，需复核":
        return _blocked(base, "blocked_missing_unique_region_binding", "项目尚未唯一绑定到可复核 CAD 区域")
    if special_type in AREA_TYPES:
        return _area_trace(base, binding, standard, AREA_TYPES[special_type], special_type)
    if special_type == "wall_waterproof":
        return _waterproof_trace(base, project, room, standard)
    if special_type == "baseboard":
        return _baseboard_trace(base, room, standard)
    if special_type == "wall_coating_expanded_area":
        return _blocked(base, "blocked_wall_expanded_area_not_supported_yet", "墙面涂料需要墙面展开面积和洞口扣减证据，首批暂不自动计算")
    return _blocked(base, "blocked_unsupported_special_quantity_type", "首批专项算量器暂不支持该项目类型")


def _base_trace(
    index: int,
    project: dict[str, Any],
    binding: dict[str, Any] | None,
    room: dict[str, Any] | None,
    standard: dict[str, Any],
    special_type: str,
) -> dict[str, Any]:
    return {
        "专项算量编号": f"BIZ2xSQ-{index:05d}",
        "识别项目编号": project.get("识别项目编号", ""),
        "标准项目编码": project.get("标准项目编码", ""),
        "项目名称": project.get("项目名称", ""),
        "图纸项目名称": project.get("图纸项目名称", ""),
        "专项类型": special_type,
        "建议工程量": "",
        "建议单位": project.get("单位", ""),
        "trace状态": "",
        "是否可复核": "否",
        "标准工程量计算规则": standard.get("quantity_rule_text", ""),
        "标准规则模板": "",
        "标准规则执行状态": "",
        "计算公式": "",
        "计算输入": "",
        "区域编号": (binding or {}).get("推荐区域编号", ""),
        "房间编号": (room or {}).get("房间编号", ""),
        "房间/空间名称": (room or {}).get("房间/空间名称", ""),
        "阻断原因": "",
        "未解决事项": "",
        "calculation_trace": {
            "standard_item_code": project.get("标准项目编码", ""),
            "standard_item_name": project.get("项目名称", ""),
            "standard_quantity_rule_text": standard.get("quantity_rule_text", ""),
            "standard_formula_type": standard.get("quantity_formula_type", ""),
            "project_feature_text": project.get("项目特征", ""),
            "project_source_evidence": project.get("识别证据", ""),
            "region_binding": binding or {},
            "room_boundary": room or {},
            "is_final_quantity": False,
        },
    }


def _area_trace(
    base: dict[str, Any],
    binding: dict[str, Any],
    standard: dict[str, Any],
    label: str,
    context: str,
) -> dict[str, Any]:
    area = _float_or_none(binding.get("区域面积"))
    if area is None or area <= 0:
        return _blocked(base, "blocked_missing_region_area", "绑定区域缺少可用面积证据")
    base["专项类型"] = label
    geometry_inputs = {"region_area_sqm": area}
    if context in {"ceiling_area", "ceiling_paint_area"}:
        geometry_inputs["horizontal_projection_area_sqm"] = area
    if context == "floor_area":
        geometry_inputs["floor_area_sqm"] = area
    execution = execute_standard_quantity_rule(
        rule_text=standard.get("quantity_rule_text", ""),
        formula_type=standard.get("quantity_formula_type", ""),
        template_context=context,
        geometry_inputs=geometry_inputs,
        item_code=base.get("标准项目编码", ""),
        item_name=base.get("项目名称", ""),
        result_unit="㎡",
    )
    _attach_standard_rule_execution(base, execution)
    if not execution.get("ok"):
        return _blocked(base, execution["standard_rule_execution_status"], execution["block_reason"])
    base["建议工程量"] = execution["result_quantity"]
    base["建议单位"] = execution["result_unit"]
    base["trace状态"] = "special_quantity_trace_ready_for_manual_review"
    base["是否可复核"] = "是"
    base["计算公式"] = execution["formula"]
    base["计算输入"] = f"区域面积={area}㎡"
    base["未解决事项"] = "standard_item_manual_confirmation；feature_values_need_review；deduction_and_merge_rules_need_review"
    base["calculation_trace"].update(
        {
            "calculator": "area_from_bound_region",
            "geometry_formula": "region_area_sqm",
            "geometry_input_area_sqm": area,
            "result_quantity": execution["result_quantity"],
            "result_unit": "㎡",
            "standard_rule_application": "use_bound_region_area_as_manual_review_candidate",
        }
    )
    return base


def _waterproof_trace(base: dict[str, Any], project: dict[str, Any], room: dict[str, Any] | None, standard: dict[str, Any]) -> dict[str, Any]:
    if not room:
        return _blocked(base, "blocked_missing_room_boundary", "墙面防水需要房间边界和净周长证据")
    perimeter = _float_or_none(room.get("净周长候选"))
    if perimeter is None or perimeter <= 0:
        return _blocked(base, "blocked_missing_net_perimeter", f"净周长不可用：{room.get('净周长状态', '')}")
    height = _extract_height_m(_project_text(project))
    if height is None or height <= 0:
        return _blocked(base, "blocked_missing_waterproof_height", "墙面防水缺少防水高度证据")
    execution = execute_standard_quantity_rule(
        rule_text=standard.get("quantity_rule_text", ""),
        formula_type=standard.get("quantity_formula_type", ""),
        template_context="wall_waterproof",
        geometry_inputs={"net_perimeter_m": perimeter, "height_m": height},
        item_code=base.get("标准项目编码", ""),
        item_name=base.get("项目名称", ""),
        result_unit="㎡",
    )
    _attach_standard_rule_execution(base, execution)
    if not execution.get("ok"):
        return _blocked(base, execution["standard_rule_execution_status"], execution["block_reason"])
    base["专项类型"] = "墙面防水面积"
    base["建议工程量"] = execution["result_quantity"]
    base["建议单位"] = execution["result_unit"]
    base["trace状态"] = "special_quantity_trace_ready_for_manual_review"
    base["是否可复核"] = "是"
    base["计算公式"] = execution["formula"]
    base["计算输入"] = f"净周长={perimeter}m；防水高度={height}m；净周长状态={room.get('净周长状态', '')}"
    unresolved = ["standard_item_manual_confirmation", "feature_values_need_review", "door_opening_deduction_need_review"]
    if room.get("净周长状态") == "未识别门洞，暂按区域周长候选":
        unresolved.append("door_opening_absence_needs_review")
    base["未解决事项"] = "；".join(unresolved)
    base["calculation_trace"].update(
        {
            "calculator": "wall_waterproof_by_net_perimeter_height",
            "geometry_formula": "net_perimeter_m * waterproof_height_m",
            "net_perimeter_m": perimeter,
            "waterproof_height_m": height,
            "result_quantity": execution["result_quantity"],
            "result_unit": "㎡",
            "standard_rule_application": "use_room_net_perimeter_and_height_as_manual_review_candidate",
        }
    )
    return base


def _baseboard_trace(base: dict[str, Any], room: dict[str, Any] | None, standard: dict[str, Any]) -> dict[str, Any]:
    if not room:
        return _blocked(base, "blocked_missing_room_boundary", "踢脚线需要房间边界和净周长证据")
    perimeter = _float_or_none(room.get("净周长候选"))
    if perimeter is None or perimeter <= 0:
        return _blocked(base, "blocked_missing_net_perimeter", f"净周长不可用：{room.get('净周长状态', '')}")
    execution = execute_standard_quantity_rule(
        rule_text=standard.get("quantity_rule_text", ""),
        formula_type=standard.get("quantity_formula_type", ""),
        template_context="baseboard",
        geometry_inputs={"net_perimeter_m": perimeter},
        item_code=base.get("标准项目编码", ""),
        item_name=base.get("项目名称", ""),
        result_unit="m",
    )
    _attach_standard_rule_execution(base, execution)
    if not execution.get("ok"):
        return _blocked(base, execution["standard_rule_execution_status"], execution["block_reason"])
    base["专项类型"] = "踢脚线净长度"
    base["建议工程量"] = execution["result_quantity"]
    base["建议单位"] = execution["result_unit"]
    base["trace状态"] = "special_quantity_trace_ready_for_manual_review"
    base["是否可复核"] = "是"
    base["计算公式"] = execution["formula"]
    base["计算输入"] = f"净周长={perimeter}m；净周长状态={room.get('净周长状态', '')}"
    unresolved = ["standard_item_manual_confirmation", "feature_values_need_review", "door_opening_deduction_need_review"]
    if room.get("净周长状态") == "未识别门洞，暂按区域周长候选":
        unresolved.append("door_opening_absence_needs_review")
    base["未解决事项"] = "；".join(unresolved)
    base["calculation_trace"].update(
        {
            "calculator": "baseboard_by_room_net_perimeter",
            "geometry_formula": "net_perimeter_m",
            "net_perimeter_m": perimeter,
            "result_quantity": execution["result_quantity"],
            "result_unit": "m",
            "standard_rule_application": "use_room_net_perimeter_as_manual_review_candidate",
        }
    )
    return base


def _blocked(base: dict[str, Any], reason: str, note: str) -> dict[str, Any]:
    base["trace状态"] = reason
    base["是否可复核"] = "否"
    base["阻断原因"] = note
    base["未解决事项"] = base.get("未解决事项") or "补充算量证据后重跑专项算量器"
    base["calculation_trace"].update({"standard_rule_application": "blocked", "block_reason": note})
    return base


def _attach_standard_rule_execution(base: dict[str, Any], execution: dict[str, Any]) -> None:
    base["标准规则模板"] = execution.get("template_id", "")
    base["标准规则执行状态"] = execution.get("standard_rule_execution_status", "")
    if not execution.get("ok"):
        unresolved = "；".join(execution.get("unresolved_requirements") or [])
        if unresolved:
            base["未解决事项"] = unresolved
    base["calculation_trace"]["standard_rule_execution"] = execution


def _special_type(project: dict[str, Any]) -> str:
    text = _project_text(project)
    if "防水" in text:
        return "wall_waterproof"
    if "踢脚" in text:
        return "baseboard"
    if any(term in text for term in ("涂料", "乳胶漆", "腻子")):
        if any(term in text for term in ("天棚", "天花", "吊顶", "顶面")):
            return "ceiling_paint_area"
        return "wall_coating_expanded_area"
    if any(term in text for term in ("吊顶", "天棚", "天花")):
        return "ceiling_area"
    if any(term in text for term in ("地面", "楼地面", "地砖", "地板")):
        return "floor_area"
    return "unsupported"


def _standard_index(standard_match_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for candidate in standard_match_report.get("standard_item_candidates") or []:
        code = str(candidate.get("standard_item_code") or "")
        if not code or code in result:
            continue
        result[code] = {
            "quantity_rule_text": candidate.get("quantity_rule_text", ""),
            "quantity_formula_type": candidate.get("quantity_formula_type", ""),
            "feature_fields": candidate.get("feature_fields", []),
        }
    return result


def _extract_height_m(text: str) -> float | None:
    patterns = (
        r"(?:高度|高|h)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mm|毫米|m|米)?",
        r"(\d+(?:\.\d+)?)\s*(mm|毫米|m|米)\s*(?:高|高度)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _float_or_none(match.group(1))
        unit = (match.group(2) if len(match.groups()) >= 2 else "") or ""
        if value is None:
            continue
        if unit in {"m", "米"}:
            return value
        if value > 20:
            return round(value / 1000, 4)
        return value
    return None


def _project_text(project: dict[str, Any]) -> str:
    return _normalize(
        " ".join(
            [
                str(project.get("图纸项目名称") or ""),
                str(project.get("项目名称") or ""),
                str(project.get("项目特征") or ""),
                str(project.get("识别证据") or ""),
            ]
        )
    )


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
