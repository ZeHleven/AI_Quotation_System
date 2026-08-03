from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PHASE = "BIZ-2x-9-mvp-floor-ceiling-fixture-quantity"

MVP_CATEGORY_LABELS = {
    "floor_area": "地面面积",
    "ceiling_area": "吊顶面积",
    "fixture_count": "灯具/洁具数量",
}

FLOOR_TERMS = ("地面", "楼地面", "地砖", "瓷砖", "铺装", "铺贴", "地台")
CEILING_TERMS = ("吊顶", "天棚", "天花", "顶面")
LIGHTING_TERMS = ("灯具", "灯带", "射灯", "筒灯", "吸顶灯", "照明", "软膜灯", "换气扇")
SANITARY_TERMS = ("洁具", "地漏", "洗脸盆", "大便器", "坐便器", "小便器", "水龙头", "淋浴", "花洒", "拖布池")


def build_low_risk_quantity_mvp_report(quantity_suggestion_report: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []

    for suggestion in quantity_suggestion_report.get("suggestions") or []:
        category = classify_mvp_quantity_category(suggestion)
        if category:
            rows.append(_mvp_row(suggestion, category))
        else:
            excluded_rows.append(_excluded_row(suggestion))

    category_counts = Counter(row["mvp_category"] for row in rows)
    status_counts = Counter(row["suggestion_status"] for row in rows)
    ready_count = sum(1 for row in rows if row["ready_for_manual_review"])
    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "requires_manual_review": True,
        "summary": {
            "mvp_candidate_count": len(rows),
            "mvp_ready_for_manual_review_count": ready_count,
            "mvp_blocked_count": len(rows) - ready_count,
            "excluded_suggestion_count": len(excluded_rows),
            "mvp_category_counts": dict(category_counts.most_common()),
            "mvp_status_counts": dict(status_counts.most_common()),
            "floor_area_candidate_count": category_counts.get("floor_area", 0),
            "ceiling_area_candidate_count": category_counts.get("ceiling_area", 0),
            "fixture_count_candidate_count": category_counts.get("fixture_count", 0),
            "acceptance_scope": [
                "地面面积：只接收地面/楼地面/地砖/铺装语义明确的面积候选",
                "吊顶面积：只接收吊顶/天棚/天花/顶面语义明确的面积候选",
                "灯具/洁具数量：只接收灯具、洁具、地漏等语义明确的块数量候选",
            ],
            "blocked_from_final_reason": "首批 MVP 只生成可复核建议量，人工确认前不写入最终四字段清单。",
        },
        "mvp_rows": rows,
        "excluded_rows": excluded_rows,
    }


def classify_mvp_quantity_category(suggestion: dict[str, Any]) -> str:
    quantity_kind = str(suggestion.get("quantity_kind") or "")
    text = _suggestion_text(suggestion)
    if quantity_kind == "area":
        if _contains_any(text, FLOOR_TERMS) and not _contains_any(text, CEILING_TERMS):
            return "floor_area"
        if _contains_any(text, CEILING_TERMS):
            return "ceiling_area"
    if quantity_kind == "count" and (_contains_any(text, LIGHTING_TERMS) or _contains_any(text, SANITARY_TERMS)):
        return "fixture_count"
    return ""


def build_low_risk_quantity_mvp_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x-9 首批低风险算量 MVP",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- MVP 候选数：{summary.get('mvp_candidate_count', 0)}",
        f"- 可进入人工复核：{summary.get('mvp_ready_for_manual_review_count', 0)}",
        f"- 暂不进入本轮 MVP：{summary.get('excluded_suggestion_count', 0)}",
        f"- 是否可直接写最终工程量：{'是' if report.get('safe_for_final_quantity_list') else '否'}",
        "",
        "## 范围",
        "",
    ]
    for item in summary.get("acceptance_scope") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## 统计", ""])
    for category, count in (summary.get("mvp_category_counts") or {}).items():
        lines.append(f"- {MVP_CATEGORY_LABELS.get(category, category)}：{count}")
    lines.extend(["", "## 候选", ""])
    if not report.get("mvp_rows"):
        lines.append("- 暂无首批 MVP 候选。")
    for row in report.get("mvp_rows") or []:
        lines.append(
            f"- {row['mvp_category_label']} | {row['source_file']} | 图层 `{row['layer']}`"
            f"{' / 块 `' + row['block_name'] + '`' if row['block_name'] else ''}"
            f" | 建议量 {row['suggested_quantity']} {row['suggested_unit']}"
            f" | {row['review_status_label']}"
        )
    lines.extend(
        [
            "",
            "## 验收口径",
            "",
            "- 本报告是“系统建议量”，不是最终工程量。",
            "- 每条候选必须保留来源图层/块名、公式和 trace，业务员确认后才能进入最终四字段清单。",
            "- 不属于地面面积、吊顶面积、灯具/洁具数量的建议量，本轮不作为验收对象。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_low_risk_quantity_mvp_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report.get("mvp_rows") or []:
        rows.append(
            {
                "MVP类别": row.get("mvp_category_label", ""),
                "建议编号": row.get("suggestion_key", ""),
                "状态": row.get("review_status_label", ""),
                "文件名": row.get("source_file", ""),
                "图层": row.get("layer", ""),
                "块名": row.get("block_name", ""),
                "建议量": row.get("suggested_quantity", ""),
                "单位": row.get("suggested_unit", ""),
                "公式": row.get("formula", ""),
                "候选数": row.get("used_candidate_count", ""),
                "风险提示": "；".join(row.get("risk_flags") or []),
                "追溯": json.dumps(row.get("calculation_trace") or {}, ensure_ascii=False),
            }
        )
    return rows


def write_low_risk_quantity_mvp_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x9_MVP_低风险算量_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}_候选.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_low_risk_quantity_mvp_markdown(report), encoding="utf-8")
    _write_csv(csv_path, build_low_risk_quantity_mvp_csv_rows(report))
    return {"json": str(json_path), "markdown": str(markdown_path), "csv": str(csv_path)}


def _mvp_row(suggestion: dict[str, Any], category: str) -> dict[str, Any]:
    ready = suggestion.get("suggestion_status") == "suggestion_ready_for_manual_review"
    return {
        "mvp_category": category,
        "mvp_category_label": MVP_CATEGORY_LABELS.get(category, category),
        "suggestion_key": suggestion.get("suggestion_key", ""),
        "source_file": suggestion.get("source_file", ""),
        "layer": suggestion.get("layer", ""),
        "block_name": suggestion.get("block_name", ""),
        "business_hint": suggestion.get("business_hint", ""),
        "quantity_kind": suggestion.get("quantity_kind", ""),
        "suggestion_status": suggestion.get("suggestion_status", ""),
        "review_status_label": "可进入人工复核" if ready else "几何证据不足，暂不可复核",
        "ready_for_manual_review": ready,
        "suggested_quantity": suggestion.get("suggested_quantity", ""),
        "suggested_unit": suggestion.get("suggested_unit", ""),
        "formula": suggestion.get("formula", ""),
        "used_candidate_count": suggestion.get("used_candidate_count", ""),
        "skipped_candidate_count": suggestion.get("skipped_candidate_count", ""),
        "risk_flags": list(suggestion.get("risk_flags") or []),
        "calculation_trace": suggestion.get("calculation_trace") or {},
    }


def _excluded_row(suggestion: dict[str, Any]) -> dict[str, Any]:
    return {
        "suggestion_key": suggestion.get("suggestion_key", ""),
        "source_file": suggestion.get("source_file", ""),
        "layer": suggestion.get("layer", ""),
        "block_name": suggestion.get("block_name", ""),
        "business_hint": suggestion.get("business_hint", ""),
        "quantity_kind": suggestion.get("quantity_kind", ""),
        "suggestion_status": suggestion.get("suggestion_status", ""),
        "exclude_reason": "not_in_first_mvp_scope",
    }


def _suggestion_text(suggestion: dict[str, Any]) -> str:
    parts = [
        suggestion.get("layer", ""),
        suggestion.get("block_name", ""),
        suggestion.get("business_hint", ""),
        suggestion.get("matched_reason", ""),
    ]
    trace = suggestion.get("calculation_trace") or {}
    if isinstance(trace, dict):
        parts.append(trace.get("mapping_business_hint", ""))
        parts.append(trace.get("matched_reason", ""))
    return " ".join(str(part or "") for part in parts)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
