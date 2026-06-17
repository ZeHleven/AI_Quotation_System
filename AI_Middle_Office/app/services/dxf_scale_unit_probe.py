from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


SCALE_VALUE_RE = re.compile(r"(?<![\dA-Za-z])1\s*[:：]\s*(\d{1,5})(?!\s*(?:水泥|砂浆|沙浆|混合|防水|找平|配合比))")
SCALE_LABEL_RE = re.compile(r"(比例|SCALE)", re.IGNORECASE)
UNIT_STATEMENT_RE = re.compile(
    r"(单位\s*[:：]?\s*(mm|毫米|m|米|cm|厘米)|尺寸.{0,12}(mm|毫米|m|米|cm|厘米)|均以.{0,6}(mm|毫米|m|米|cm|厘米)|以.{0,6}(mm|毫米|m|米|cm|厘米).{0,8}为单位)",
    re.IGNORECASE,
)
WEAK_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?\s*(mm|毫米|cm|厘米|m|米)|\b(mm|cm|m)\b)", re.IGNORECASE)

FRAME_KEYWORDS = ("图框", "标题栏", "Frame", "FRAME")
DIMENSION_KEYWORDS = ("尺寸", "标高", "DIM", "dimension", "Dimension")
MATERIAL_RATIO_KEYWORDS = ("水泥", "砂浆", "沙浆", "混合", "防水", "找平", "配合比")

EVIDENCE_LIMIT_PER_CATEGORY = 80


class DxfScaleUnitProbeError(ValueError):
    pass


def load_json_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path)
    if not report_path.exists():
        raise DxfScaleUnitProbeError(f"Report not found: {report_path}")
    return json.loads(report_path.read_text(encoding="utf-8"))


def load_text_records_csv(path: str | Path) -> list[dict[str, Any]]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise DxfScaleUnitProbeError(f"Text CSV not found: {csv_path}")
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                {
                    "source_file": row.get("文件名", ""),
                    "text": row.get("文字", ""),
                    "layer": row.get("图层", ""),
                    "layout": row.get("布局", ""),
                    "block_name": row.get("块名", ""),
                    "x": _float_or_none(row.get("X", "")),
                    "y": _float_or_none(row.get("Y", "")),
                    "line_number": _int_or_zero(row.get("源行号", "")),
                }
            )
    return rows


def build_scale_unit_probe_report(
    *,
    text_report: dict[str, Any],
    geometry_report: dict[str, Any],
    text_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = text_records if text_records is not None else _records_from_text_report(text_report)
    evidence: list[dict[str, Any]] = []
    evidence.extend(_detect_scale_evidence(records))
    evidence.extend(_detect_unit_evidence(records))
    evidence.extend(_detect_frame_evidence(text_report, geometry_report))
    evidence.extend(_detect_dimension_evidence(geometry_report))

    scale_values = sorted({item["value"] for item in evidence if item["category"] == "比例值" and item.get("value")})
    scale_label_count = sum(1 for item in evidence if item["category"] == "比例标签")
    unit_confirmed_count = sum(1 for item in evidence if item["category"] == "单位说明" and item["confidence"] == "high")
    weak_unit_count = sum(1 for item in evidence if item["category"] == "单位弱线索")
    frame_count = sum(1 for item in evidence if item["category"] == "图框图层")
    dimension_count = _dimension_count_from_geometry_report(geometry_report)

    scale_status = _scale_status(scale_values, scale_label_count)
    unit_status = _unit_status(unit_confirmed_count, weak_unit_count)
    frame_status = "frame_layer_detected" if frame_count else "frame_layer_not_detected"
    dimension_status = "dimension_entities_detected" if dimension_count else "dimension_entities_not_detected"

    risk_flags = _risk_flags(
        scale_status=scale_status,
        unit_status=unit_status,
        frame_status=frame_status,
        dimension_status=dimension_status,
        scale_label_count=scale_label_count,
    )
    ready_for_geometry_quantity_probe = (
        scale_status == "confirmed_single_scale"
        and unit_status == "confirmed_drawing_unit"
        and frame_status == "frame_layer_detected"
        and dimension_status == "dimension_entities_detected"
    )
    if ready_for_geometry_quantity_probe:
        risk_flags.append("geometry_quantity_still_requires_standard_rule_and_manual_review")

    return {
        "ok": True,
        "phase": "BIZ-2x-9b-scale-unit-frame-probe",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_auto_quantity": False,
        "ready_for_geometry_quantity_probe": ready_for_geometry_quantity_probe,
        "summary": {
            "text_record_count": len(records),
            "ready_for_geometry_quantity_probe": ready_for_geometry_quantity_probe,
            "safe_for_auto_quantity": False,
            "scale_status": scale_status,
            "scale_values": scale_values,
            "scale_label_count": scale_label_count,
            "unit_status": unit_status,
            "unit_confirmed_count": unit_confirmed_count,
            "weak_unit_count": weak_unit_count,
            "frame_status": frame_status,
            "frame_evidence_count": frame_count,
            "dimension_status": dimension_status,
            "dimension_entity_count": dimension_count,
            "evidence_count": len(evidence),
            "risk_flags": risk_flags,
            "next_step": "manual_confirm_scale_unit_then_select_low_risk_layers",
        },
        "evidence": evidence,
    }


def build_scale_unit_probe_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-9b 图框、比例、单位校验报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 文本记录数：{summary['text_record_count']}",
        f"- 比例状态：`{summary['scale_status']}`",
        f"- 比例值：{', '.join(summary['scale_values']) or '-'}",
        f"- 单位状态：`{summary['unit_status']}`",
        f"- 图框状态：`{summary['frame_status']}`",
        f"- 尺寸标注状态：`{summary['dimension_status']}`，尺寸标注实体数：{summary['dimension_entity_count']}",
        f"- 是否允许进入几何建议量探测：{'是' if report['ready_for_geometry_quantity_probe'] else '否'}",
        f"- 是否可直接自动生成最终工程量：{'是' if report['safe_for_auto_quantity'] else '否'}",
        "",
        "## 风险提示",
        "",
    ]
    for flag in summary["risk_flags"] or ["暂无"]:
        lines.append(f"- `{flag}`")

    lines.extend(["", "## 证据样例", ""])
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in report["evidence"]:
        by_category.setdefault(item["category"], []).append(item)
    for category, items in by_category.items():
        lines.extend([f"### {category}", ""])
        for item in items[:10]:
            if item.get("text"):
                lines.append(
                    f"- {item.get('source_file', '-')} | 图层 `{item.get('layer', '-')}` | 值 `{item.get('value', '')}` | {item.get('text', '')}"
                )
            else:
                lines.append(
                    f"- {item.get('source_file', '-') or '汇总'} | 图层 `{item.get('layer', item.get('name', '-'))}` | 数量 {item.get('count', '')} | {item.get('reason', '')}"
                )
        lines.append("")

    lines.extend(
        [
            "## 结论",
            "",
            "- 本报告只校验图框、比例、单位和尺寸标注证据，不计算工程量。",
            "- 只有比例、单位、图框和尺寸证据全部清楚后，才允许进入 BIZ-2x-9c/9d 的面积/长度建议量探测。",
            "- 即使进入后续几何建议量阶段，仍必须按 active GB/T 标准库 `quantity_rule` 生成 `calculation_trace`，并经过人工确认。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_scale_unit_evidence_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report.get("evidence", []):
        rows.append(
            {
                "证据类型": item.get("category", ""),
                "状态": item.get("status", ""),
                "置信度": item.get("confidence", ""),
                "文件名": item.get("source_file", ""),
                "图层": item.get("layer", item.get("name", "")),
                "布局": item.get("layout", ""),
                "值": item.get("value", ""),
                "数量": item.get("count", ""),
                "文字": item.get("text", ""),
                "源行号": item.get("line_number", ""),
                "说明": item.get("reason", ""),
            }
        )
    return rows


def write_scale_unit_probe_outputs(report: dict[str, Any], output_dir: str | Path, *, stem: str | None = None) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x9b_图框比例单位校验_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}_证据清单.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_scale_unit_probe_markdown(report), encoding="utf-8")
    _write_csv(csv_path, build_scale_unit_evidence_csv_rows(report))
    return {"json": str(json_path), "markdown": str(markdown_path), "evidence_csv": str(csv_path)}


def build_manual_scale_unit_confirmation_report(
    *,
    scale_unit_report: dict[str, Any],
    geometry_report: dict[str, Any],
    drawing_unit: str = "mm",
    model_space_scale: str = "1:1",
    title_block_scale_usage: str = "plot_scale_only_not_quantity_multiplier",
    title_block_scale_varies_by_drawing: bool = True,
    allow_geometry_quantity_for_all_files: bool = True,
    confirmation_note: str = "",
) -> dict[str, Any]:
    unit = drawing_unit.strip().lower()
    unit_to_meter_factor = _unit_to_meter_factor(unit)
    files = []
    for file_item in geometry_report.get("files", []):
        files.append(
            {
                "file_name": file_item.get("file_name", ""),
                "drawing_unit": unit,
                "unit_to_meter_factor": unit_to_meter_factor,
                "model_space_scale": model_space_scale,
                "title_block_scale_usage": title_block_scale_usage,
                "title_block_scale_varies_by_drawing": title_block_scale_varies_by_drawing,
                "allow_geometry_quantity_probe": allow_geometry_quantity_for_all_files,
                "geometry_entity_count": file_item.get("geometry_entity_count", 0),
                "dimension_candidate_count": file_item.get("dimension_candidate_count", 0),
                "confirmation_status": "confirmed" if allow_geometry_quantity_for_all_files else "partially_confirmed",
                "note": confirmation_note,
            }
        )
    ready = bool(unit_to_meter_factor and model_space_scale == "1:1" and allow_geometry_quantity_for_all_files)
    risk_flags = [
        "safe_for_auto_quantity_still_false_until_standard_rule_and_manual_review",
        "title_block_scale_must_not_be_used_as_quantity_multiplier",
        "layer_block_mapping_still_required_before_quantity_suggestion",
        "deduction_rules_not_yet_confirmed",
    ]
    if not ready:
        risk_flags.append("manual_scale_unit_confirmation_incomplete")
    return {
        "ok": True,
        "phase": "BIZ-2x-9b-1-manual-scale-unit-confirmation",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ready_for_geometry_quantity_probe": ready,
        "safe_for_auto_quantity": False,
        "manual_confirmation": {
            "drawing_unit": unit,
            "unit_to_meter_factor": unit_to_meter_factor,
            "model_space_scale": model_space_scale,
            "title_block_scale_usage": title_block_scale_usage,
            "title_block_scale_varies_by_drawing": title_block_scale_varies_by_drawing,
            "allow_geometry_quantity_for_all_files": allow_geometry_quantity_for_all_files,
            "confirmation_note": confirmation_note,
        },
        "previous_probe_summary": scale_unit_report.get("summary", {}),
        "summary": {
            "file_count": len(files),
            "confirmed_file_count": sum(1 for item in files if item["allow_geometry_quantity_probe"]),
            "ready_for_geometry_quantity_probe": ready,
            "safe_for_auto_quantity": False,
            "drawing_unit": unit,
            "model_space_scale": model_space_scale,
            "title_block_scale_usage": title_block_scale_usage,
            "risk_flags": risk_flags,
            "next_step": "BIZ-2x-9c-9d-low-risk-area-length-quantity-candidate_probe",
        },
        "files": files,
    }


def build_manual_scale_unit_confirmation_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    confirmation = report["manual_confirmation"]
    lines = [
        "# BIZ-2x-9b-1 比例/单位人工确认配置",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 确认绘图单位：`{confirmation['drawing_unit']}`",
        f"- 换算系数：1 CAD 单位 = {confirmation['unit_to_meter_factor']} m",
        f"- 模型空间比例：`{confirmation['model_space_scale']}`",
        f"- 标题栏比例用途：`{confirmation['title_block_scale_usage']}`",
        f"- 标题栏比例是否逐图不同：{'是' if confirmation['title_block_scale_varies_by_drawing'] else '否'}",
        f"- 允许进入几何建议量探测的文件数：{summary['confirmed_file_count']} / {summary['file_count']}",
        f"- 是否允许进入几何建议量探测：{'是' if report['ready_for_geometry_quantity_probe'] else '否'}",
        f"- 是否可直接自动生成最终工程量：{'是' if report['safe_for_auto_quantity'] else '否'}",
        "",
        "## 口径解释",
        "",
        "- 本批图纸按模型空间真实尺寸 `1:1` 绘制，CAD 坐标单位按 `mm` 解释。",
        "- 标题栏里的 `1:50`、`1:100` 等比例视为出图/打印比例，不参与模型空间几何算量换算。",
        "- 后续面积按 CAD 面积除以 1,000,000 换算为㎡，长度按 CAD 长度除以 1,000 换算为 m。",
        "- 这只解除比例/单位阻断，不代表可以自动生成最终工程量；仍需标准库规则、图层/块名映射、扣减规则和人工确认。",
        "",
        "## 风险提示",
        "",
    ]
    for flag in summary["risk_flags"]:
        lines.append(f"- `{flag}`")
    lines.extend(["", "## 文件确认", ""])
    for item in report["files"]:
        lines.append(
            f"- {item['file_name']}：单位 `{item['drawing_unit']}`，模型空间 `{item['model_space_scale']}`，允许进入几何建议量探测：{'是' if item['allow_geometry_quantity_probe'] else '否'}"
        )
    return "\n".join(lines) + "\n"


def build_manual_scale_unit_confirmation_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report.get("files", []):
        rows.append(
            {
                "文件名": item.get("file_name", ""),
                "确认绘图单位": item.get("drawing_unit", ""),
                "换算系数_1CAD单位等于多少米": item.get("unit_to_meter_factor", ""),
                "模型空间比例": item.get("model_space_scale", ""),
                "标题栏比例用途": item.get("title_block_scale_usage", ""),
                "标题栏比例是否逐图不同": "是" if item.get("title_block_scale_varies_by_drawing") else "否",
                "是否允许进入几何建议量探测": "是" if item.get("allow_geometry_quantity_probe") else "否",
                "几何实体数": item.get("geometry_entity_count", ""),
                "尺寸标注候选数": item.get("dimension_candidate_count", ""),
                "确认状态": item.get("confirmation_status", ""),
                "备注": item.get("note", ""),
            }
        )
    return rows


def write_manual_scale_unit_confirmation_outputs(report: dict[str, Any], output_dir: str | Path, *, stem: str | None = None) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x9b_比例单位人工确认配置_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}_图纸确认.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_manual_scale_unit_confirmation_markdown(report), encoding="utf-8")
    _write_csv(csv_path, build_manual_scale_unit_confirmation_csv_rows(report))
    return {"json": str(json_path), "markdown": str(markdown_path), "confirmation_csv": str(csv_path)}


def _detect_scale_evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    label_records: list[dict[str, Any]] = []
    value_records: list[dict[str, Any]] = []
    for record in records:
        text = str(record.get("text") or "")
        if not text:
            continue
        if SCALE_LABEL_RE.search(text):
            label_records.append(record)
            _append_limited(
                evidence,
                _evidence(record, category="比例标签", status="label_only", confidence="low", reason="识别到比例/SCALE 标签，但标签本身不能证明比例值"),
            )
        for match in SCALE_VALUE_RE.finditer(text):
            if _looks_like_material_ratio(text):
                continue
            value = f"1:{int(match.group(1))}"
            value_records.append({**record, "scale_value": value})
            confidence = "high" if SCALE_LABEL_RE.search(text) or _near_scale_label(record, label_records) else "medium"
            reason = "识别到比例值文本" if confidence == "medium" else "比例标签附近识别到比例值"
            _append_limited(evidence, _evidence(record, category="比例值", status="scale_value_detected", confidence=confidence, value=value, reason=reason))
    # Pair label-only records with nearby later value records when the value is parsed after the label.
    for value_record in value_records:
        if not any(item.get("category") == "比例值" and item.get("source_file") == value_record.get("source_file") and item.get("line_number") == value_record.get("line_number") for item in evidence):
            _append_limited(
                evidence,
                _evidence(value_record, category="比例值", status="scale_value_detected", confidence="high", value=value_record["scale_value"], reason="比例标签附近识别到比例值"),
            )
    return evidence


def _detect_unit_evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    weak_count = 0
    for record in records:
        text = str(record.get("text") or "")
        if not text:
            continue
        if UNIT_STATEMENT_RE.search(text):
            _append_limited(
                evidence,
                _evidence(record, category="单位说明", status="unit_statement_detected", confidence="high", value=_unit_value(text), reason="识别到图纸单位或尺寸单位说明"),
            )
        elif WEAK_UNIT_RE.search(text) and weak_count < EVIDENCE_LIMIT_PER_CATEGORY:
            weak_count += 1
            _append_limited(
                evidence,
                _evidence(record, category="单位弱线索", status="weak_unit_mention", confidence="low", value=_unit_value(text), reason="仅识别到材料厚度/尺寸文字中的单位，不能证明全图绘图单位"),
            )
    return evidence


def _detect_frame_evidence(text_report: dict[str, Any], geometry_report: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for source, report in [("text", text_report), ("geometry", geometry_report)]:
        for layer in _top_layers(report):
            name = str(layer.get("name") or "")
            if any(keyword in name for keyword in FRAME_KEYWORDS):
                evidence.append(
                    {
                        "category": "图框图层",
                        "status": "frame_layer_detected",
                        "confidence": "medium",
                        "source": source,
                        "name": name,
                        "layer": name,
                        "count": layer.get("count", 0),
                        "reason": "图层名称包含图框/Frame，可作为图框候选；仍需后续定位图框边界和标题栏字段",
                    }
                )
    return evidence[:EVIDENCE_LIMIT_PER_CATEGORY]


def _detect_dimension_evidence(geometry_report: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    dimension_count = _dimension_count_from_geometry_report(geometry_report)
    if dimension_count:
        evidence.append(
            {
                "category": "尺寸标注实体",
                "status": "dimension_entities_detected",
                "confidence": "medium",
                "count": dimension_count,
                "reason": "DXF 中存在 DIMENSION 标注实体；后续仍需关联到具体边界或线段",
            }
        )
    layer_counter: Counter[str] = Counter()
    for file_item in geometry_report.get("files", []):
        for candidate in file_item.get("dimension_candidates", []):
            layer = str(candidate.get("layer") or "")
            if layer:
                layer_counter[layer] += 1
    for layer, count in layer_counter.most_common(20):
        evidence.append(
            {
                "category": "尺寸标注图层",
                "status": "dimension_layer_detected",
                "confidence": "medium",
                "layer": layer,
                "count": count,
                "reason": "尺寸标注候选所在图层，可用于后续比例/尺寸一致性复核",
            }
        )
    return evidence[:EVIDENCE_LIMIT_PER_CATEGORY]


def _records_from_text_report(text_report: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for file_item in text_report.get("files", []):
        for key in ("text_samples", "important_texts"):
            for record in file_item.get(key, []):
                normalized = {
                    "source_file": record.get("source_file") or file_item.get("file_name", ""),
                    "text": record.get("text", ""),
                    "layer": record.get("layer", ""),
                    "layout": record.get("layout", ""),
                    "block_name": record.get("block_name", ""),
                    "x": record.get("x"),
                    "y": record.get("y"),
                    "line_number": record.get("line_number", 0),
                }
                identity = (str(normalized["source_file"]), int(normalized["line_number"] or 0), str(normalized["text"]))
                if identity not in seen:
                    seen.add(identity)
                    records.append(normalized)
    return records


def _top_layers(report: dict[str, Any]) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    summary = report.get("summary", {})
    layers.extend(summary.get("top_layers_by_geometry_count", []))
    for file_item in report.get("files", []):
        layers.extend(file_item.get("top_layers_by_geometry_count", []))
        layers.extend(file_item.get("top_layers_by_entity_count", []))
        for layer in file_item.get("layers_sample", []):
            layers.append({"name": layer, "count": ""})
    return layers


def _dimension_count_from_geometry_report(geometry_report: dict[str, Any]) -> int:
    summary = geometry_report.get("summary", {})
    if "dimension_candidate_count" in summary:
        return int(summary.get("dimension_candidate_count") or 0)
    return sum(int(file_item.get("dimension_candidate_count") or 0) for file_item in geometry_report.get("files", []))


def _scale_status(scale_values: list[str], scale_label_count: int) -> str:
    if len(scale_values) == 1:
        return "confirmed_single_scale"
    if len(scale_values) > 1:
        return "conflicting_multiple_scale_values"
    if scale_label_count:
        return "scale_label_only_needs_manual_value"
    return "missing_scale_evidence"


def _unit_status(unit_confirmed_count: int, weak_unit_count: int) -> str:
    if unit_confirmed_count:
        return "confirmed_drawing_unit"
    if weak_unit_count:
        return "weak_unit_mentions_only"
    return "missing_unit_evidence"


def _risk_flags(*, scale_status: str, unit_status: str, frame_status: str, dimension_status: str, scale_label_count: int) -> list[str]:
    flags: list[str] = []
    if scale_status == "missing_scale_evidence":
        flags.append("scale_value_not_detected")
    elif scale_status == "scale_label_only_needs_manual_value":
        flags.append("scale_label_without_value")
    elif scale_status == "conflicting_multiple_scale_values":
        flags.append("multiple_scale_values_need_layout_mapping")
    if unit_status == "missing_unit_evidence":
        flags.append("drawing_unit_not_detected")
    elif unit_status == "weak_unit_mentions_only":
        flags.append("drawing_unit_only_weakly_inferred")
    if frame_status != "frame_layer_detected":
        flags.append("frame_layer_not_detected")
    if dimension_status != "dimension_entities_detected":
        flags.append("dimension_entities_not_detected")
    if scale_label_count and scale_status != "confirmed_single_scale":
        flags.append("title_block_scale_field_needs_manual_fill")
    return flags


def _looks_like_material_ratio(text: str) -> bool:
    return any(keyword in text for keyword in MATERIAL_RATIO_KEYWORDS)


def _near_scale_label(record: dict[str, Any], label_records: list[dict[str, Any]]) -> bool:
    file_name = record.get("source_file")
    layout = record.get("layout")
    x = _float_or_none(record.get("x"))
    y = _float_or_none(record.get("y"))
    if x is None or y is None:
        return False
    for label in label_records:
        if label.get("source_file") != file_name or label.get("layout") != layout:
            continue
        label_x = _float_or_none(label.get("x"))
        label_y = _float_or_none(label.get("y"))
        if label_x is None or label_y is None:
            continue
        if 0 <= x - label_x <= 1500 and abs(y - label_y) <= 200:
            return True
    return False


def _unit_value(text: str) -> str:
    lowered = text.lower()
    if "毫米" in text or "mm" in lowered:
        return "mm"
    if "厘米" in text or "cm" in lowered:
        return "cm"
    if "米" in text or re.search(r"\bm\b", lowered):
        return "m"
    return ""


def _unit_to_meter_factor(unit: str) -> float:
    if unit in {"mm", "毫米"}:
        return 0.001
    if unit in {"cm", "厘米"}:
        return 0.01
    if unit in {"m", "米"}:
        return 1.0
    return 0.0


def _evidence(
    record: dict[str, Any],
    *,
    category: str,
    status: str,
    confidence: str,
    reason: str,
    value: str = "",
) -> dict[str, Any]:
    return {
        "category": category,
        "status": status,
        "confidence": confidence,
        "source_file": record.get("source_file", ""),
        "text": str(record.get("text") or "").replace("\n", " / "),
        "layer": record.get("layer", ""),
        "layout": record.get("layout", ""),
        "block_name": record.get("block_name", ""),
        "x": record.get("x"),
        "y": record.get("y"),
        "line_number": record.get("line_number", ""),
        "value": value,
        "reason": reason,
    }


def _append_limited(evidence: list[dict[str, Any]], item: dict[str, Any]) -> None:
    category_count = sum(1 for row in evidence if row["category"] == item["category"])
    if category_count < EVIDENCE_LIMIT_PER_CATEGORY:
        evidence.append(item)


def _float_or_none(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
