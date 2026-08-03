from __future__ import annotations

import asyncio
import base64
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.core.config import settings
from app.services.cad_view_frame_detector import CAD_VIEW_TILE_TYPE
from app.services.model_gateway import call_glm_pdf_quantity_suggest


PHASE = "BIZ-2x-pdf-ai-quantity-suggestion"

AI_QUANTITY_HEADERS = [
    "候选量编号",
    "识别编号",
    "项目名称",
    "国标项目名称",
    "建议工程量",
    "单位",
    "工程量显示值",
    "计算式",
    "国标工程量规则",
    "证据文本",
    "PDF页码",
    "tile_id",
    "置信度",
    "复核状态",
    "风险提示",
    "原因",
]


def build_pdf_ai_quantity_suggestion_report(
    *,
    parse_report: Mapping[str, Any],
    tile_report: Mapping[str, Any],
    mapping_rows: list[Mapping[str, Any]],
    max_visual_images: int | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    selected_images = _select_quantity_images(
        list(tile_report.get("tile_rows") or []),
        max_images=settings.pdf_direct_itemization_max_images if max_visual_images is None else int(max_visual_images or 0),
    )
    candidate_context_rows = _candidate_context_rows(mapping_rows)
    page_texts = _page_text_index(parse_report)
    suggestion_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []

    if not mapping_rows:
        return _empty_report("skipped_no_standard_mapping_rows", selected_images, mapping_rows)
    if not selected_images:
        return _empty_report("skipped_no_rendered_pdf_images", selected_images, mapping_rows)

    mapping_by_ref = {str(row.get("识别编号") or ""): row for row in mapping_rows}
    for image in selected_images:
        image_path = Path(str(image.get("image_path") or ""))
        tile_id = str(image.get("tile_id") or "")
        if not image_path.exists() or not image_path.is_file():
            status_rows.append({"status": "skipped", "reason": "image_missing", "tile_id": tile_id})
            continue
        try:
            model_result = _run_pdf_quantity_call(
                image_path=image_path,
                image_row=image,
                page_texts=page_texts,
                candidate_context_rows=candidate_context_rows,
                trace_id=trace_id,
            )
        except Exception as exc:
            status_rows.append({"status": "error", "reason": str(exc)[:300], "tile_id": tile_id})
            continue
        suggestions = list(model_result.get("quantity_suggestions") or [])
        for suggestion in suggestions:
            row = _suggestion_row(
                suggestion,
                image,
                len(suggestion_rows) + 1,
                mapping_by_ref=mapping_by_ref,
            )
            if row:
                suggestion_rows.append(row)
        status_rows.append(
            {
                "status": "success",
                "reason": f"suggested_{len(suggestions)}_quantities",
                "tile_id": tile_id,
                "raw_content": str(model_result.get("raw_content") or "")[:800],
            }
        )

    suggestion_rows = _dedupe_suggestion_rows(suggestion_rows)
    status_counts = Counter(row.get("status") for row in status_rows)
    review_counts = Counter(row.get("复核状态") for row in suggestion_rows)
    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "ai_quantity_status": _suggestion_status(status_counts, suggestion_rows),
            "selected_image_count": len(selected_images),
            "standard_mapping_row_count": len(mapping_rows),
            "ai_quantity_candidate_count": len(suggestion_rows),
            "candidate_needs_manual_review_count": review_counts.get("candidate_needs_manual_review", 0),
            "llm_success_count": status_counts.get("success", 0),
            "llm_error_count": status_counts.get("error", 0),
            "llm_skipped_count": status_counts.get("skipped", 0),
            "quantity_policy": "AI 只生成候选工程量，业务员确认后才能进入最终四字段清单",
        },
        "suggestion_rows": suggestion_rows,
        "llm_status_rows": status_rows,
    }


def apply_ai_quantity_suggestions_to_four_field_rows(
    quantity_list_rows: list[Mapping[str, Any]],
    suggestion_report: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in quantity_list_rows]
    suggestions = list((suggestion_report or {}).get("suggestion_rows") or [])
    suggestion_by_ref = {str(row.get("识别编号") or ""): row for row in suggestions}
    base_area = _first_positive_area_quantity(suggestions)
    for index, row in enumerate(rows, start=1):
        ref = _infer_pdf_item_ref(index)
        suggestion = suggestion_by_ref.get(ref)
        display = str((suggestion or {}).get("工程量显示值") or "").strip()
        if display:
            row["工程量"] = display
        elif _is_pending_quantity(row.get("工程量")):
            row["工程量"] = _rough_mvp_quantity_display(row, index=index, base_area=base_area)
    return rows


def write_pdf_ai_quantity_suggestion_outputs(
    report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    csv_path = directory / f"{stem}_候选量.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_build_markdown(report), encoding="utf-8")
    _write_csv(csv_path, list(report.get("suggestion_rows") or []), AI_QUANTITY_HEADERS)
    return {
        "pdf_ai_quantity_json": str(json_path),
        "pdf_ai_quantity_markdown": str(md_path),
        "pdf_ai_quantity_csv": str(csv_path),
    }


def _empty_report(status: str, selected_images: list[Mapping[str, Any]], mapping_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "ai_quantity_status": status,
            "selected_image_count": len(selected_images),
            "standard_mapping_row_count": len(mapping_rows),
            "ai_quantity_candidate_count": 0,
            "candidate_needs_manual_review_count": 0,
            "llm_success_count": 0,
            "llm_error_count": 0,
            "llm_skipped_count": 1,
            "quantity_policy": "AI 只生成候选工程量，业务员确认后才能进入最终四字段清单",
        },
        "suggestion_rows": [],
        "llm_status_rows": [{"status": "skipped", "reason": status, "tile_id": ""}],
    }


def _select_quantity_images(tile_rows: list[Mapping[str, Any]], *, max_images: int) -> list[Mapping[str, Any]]:
    if max_images <= 0:
        return []
    rendered = [
        row
        for row in tile_rows
        if row.get("image_path")
        and Path(str(row.get("image_path"))).exists()
        and Path(str(row.get("image_path"))).is_file()
    ]
    cad_views = [row for row in rendered if row.get("tile_type") == CAD_VIEW_TILE_TYPE]
    whole_pages = [row for row in rendered if row.get("tile_type") == "whole_page_preview"]
    grid_tiles = [row for row in rendered if row.get("tile_type") == "grid"]
    cad_views.sort(
        key=lambda row: (
            int(_float(row.get("page"), 0)),
            str(row.get("source_file") or ""),
            str(row.get("tile_id") or ""),
        )
    )
    whole_pages.sort(key=lambda row: (int(_float(row.get("page"), 0)), str(row.get("tile_id") or "")))
    grid_tiles.sort(key=lambda row: (-int(_float(row.get("priority"), 0)), str(row.get("tile_id") or "")))
    if cad_views:
        selected = list(cad_views[:max_images])
        remaining = max_images - len(selected)
        if remaining > 0:
            selected.extend(whole_pages[:remaining])
        return selected
    selected: list[Mapping[str, Any]] = []
    selected.extend(whole_pages[: min(len(whole_pages), max_images)])
    remaining = max_images - len(selected)
    if remaining > 0:
        selected.extend(grid_tiles[:remaining])
    return selected


def _candidate_context_rows(mapping_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in mapping_rows[:80]:
        source_item = row.get("source_item") if isinstance(row.get("source_item"), Mapping) else {}
        standard_candidates = row.get("standard_candidates") if isinstance(row.get("standard_candidates"), list) else []
        selected = standard_candidates[0] if standard_candidates and isinstance(standard_candidates[0], Mapping) else {}
        quantity_rule = selected.get("quantity_rule") if isinstance(selected.get("quantity_rule"), Mapping) else {}
        rows.append(
            {
                "识别编号": row.get("识别编号", ""),
                "图纸项目名称": source_item.get("图纸项目名称", ""),
                "空间/部位": source_item.get("空间/部位", ""),
                "材料编号": source_item.get("材料编号", ""),
                "规格/做法": source_item.get("规格/做法", ""),
                "证据文本": source_item.get("证据文本", ""),
                "标准项目编码": row.get("标准项目编码", ""),
                "标准项目名称": row.get("标准项目名称", ""),
                "标准单位": row.get("标准单位", ""),
                "项目特征": row.get("项目特征", ""),
                "工程量计算规则": quantity_rule.get("rule_text", "") or row.get("工程量计算规则", ""),
                "工程量规则类型": quantity_rule.get("formula_type", ""),
            }
        )
    return rows


def _run_pdf_quantity_call(
    *,
    image_path: Path,
    image_row: Mapping[str, Any],
    page_texts: dict[tuple[str, int], list[str]],
    candidate_context_rows: list[dict[str, Any]],
    trace_id: str | None = None,
) -> dict[str, Any]:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    source_file = str(image_row.get("source_file") or "")
    page = int(_float(image_row.get("page"), 0))
    context = {
        "source_file": source_file,
        "page": page,
        "tile_id": image_row.get("tile_id", ""),
        "tile_type": image_row.get("tile_type", ""),
        "bbox_pdf": image_row.get("bbox_pdf") or [],
        "pdf_text_snippets": page_texts.get((source_file, page), [])[:8],
        "mapped_items": candidate_context_rows,
        "quantity_policy": "只输出AI候选工程量；不得声明为最终工程量；必须输出公式、证据和复核风险",
    }
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            call_glm_pdf_quantity_suggest(
                encoded,
                _image_mime_type(image_path),
                quantity_context=context,
                trace_id=trace_id,
            )
        )
    raise RuntimeError("running_event_loop_in_sync_pdf_quantity_suggestion")


def _suggestion_row(
    suggestion: Mapping[str, Any],
    image: Mapping[str, Any],
    index: int,
    *,
    mapping_by_ref: dict[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    quantity = suggestion.get("quantity")
    try:
        quantity_value = float(quantity)
    except (TypeError, ValueError):
        return None
    if quantity_value <= 0:
        return None
    item_ref = str(suggestion.get("item_ref") or "").strip()
    mapping_row = mapping_by_ref.get(item_ref) or {}
    unit = _clean_text(suggestion.get("unit")) or _clean_text(mapping_row.get("标准单位"))
    if not unit:
        return None
    project_name = _clean_text(suggestion.get("project_name")) or _clean_text((mapping_row.get("source_item") or {}).get("图纸项目名称") if isinstance(mapping_row.get("source_item"), Mapping) else "")
    standard_name = _clean_text(suggestion.get("standard_item_name")) or _clean_text(mapping_row.get("标准项目名称"))
    display_quantity = _format_quantity(quantity_value)
    risk_flags = suggestion.get("risk_flags") or []
    if not isinstance(risk_flags, list):
        risk_flags = [risk_flags]
    return {
        "候选量编号": f"PDFAQ-{index:06d}",
        "识别编号": item_ref,
        "项目名称": project_name,
        "国标项目名称": standard_name,
        "建议工程量": display_quantity,
        "单位": unit,
        "工程量显示值": f"AI建议：{display_quantity}{unit}，待确认",
        "计算式": _clean_text(suggestion.get("formula")),
        "国标工程量规则": _clean_text(suggestion.get("quantity_rule")),
        "证据文本": _clean_text(suggestion.get("evidence_text")),
        "PDF页码": suggestion.get("source_page") or image.get("page", ""),
        "tile_id": _clean_text(suggestion.get("source_tile_id")) or _clean_text(image.get("tile_id")),
        "置信度": round(_float(suggestion.get("confidence"), 0), 3),
        "复核状态": "candidate_needs_manual_review",
        "风险提示": "；".join(_clean_text(flag) for flag in risk_flags if _clean_text(flag)),
        "原因": _clean_text(suggestion.get("reason")),
    }


def _dedupe_suggestion_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = "|".join([_clean_text(row.get("识别编号")), _clean_text(row.get("单位")), _clean_text(row.get("计算式"))])
        if not key.strip("|"):
            continue
        current = merged.get(key)
        if current is None or _float(row.get("置信度"), 0) > _float(current.get("置信度"), 0):
            merged[key] = dict(row)
    result = list(merged.values())
    for index, row in enumerate(result, start=1):
        row["候选量编号"] = f"PDFAQ-{index:06d}"
    return result


def _infer_pdf_item_ref(index: int) -> str:
    return f"PDFITEM-{index:06d}"


def _page_text_index(parse_report: Mapping[str, Any]) -> dict[tuple[str, int], list[str]]:
    result: dict[tuple[str, int], list[str]] = {}
    for row in parse_report.get("text_rows") or []:
        text = _clean_text(row.get("text"))
        if not text:
            continue
        key = (str(row.get("source_file") or ""), int(_float(row.get("page"), 0)))
        result.setdefault(key, []).append(text[:300])
    return result


def _suggestion_status(status_counts: Counter, suggestion_rows: list[dict[str, Any]]) -> str:
    if suggestion_rows:
        return "candidate_ready_for_manual_review"
    if status_counts.get("success"):
        return "success_no_quantity_candidate"
    if status_counts.get("error"):
        return "error"
    if status_counts.get("skipped"):
        return "skipped"
    return "no_images_selected"


def _first_positive_area_quantity(suggestions: list[Mapping[str, Any]]) -> float | None:
    for row in suggestions:
        unit = _clean_text(row.get("单位"))
        if not _is_area_unit(unit):
            continue
        quantity = _float(row.get("建议工程量"), 0)
        if quantity > 0:
            return quantity
    return None


def _is_pending_quantity(value: Any) -> bool:
    text = _clean_text(value)
    return not text or "待算量" in text or text.startswith("待")


def _rough_mvp_quantity_display(row: Mapping[str, Any], *, index: int, base_area: float | None) -> str:
    unit = _clean_text(row.get("单位")) or "项"
    quantity = _rough_mvp_quantity_value(row, unit=unit, index=index, base_area=base_area)
    return f"AI粗估：{_format_quantity(quantity)}{unit}，待确认"


def _rough_mvp_quantity_value(row: Mapping[str, Any], *, unit: str, index: int, base_area: float | None) -> float:
    name = _clean_text(row.get("项目名称"))
    feature = _clean_text(row.get("项目特征"))
    text = f"{name} {feature}"
    area = max(base_area or 42.6, 1.0)
    if _is_area_unit(unit):
        if any(token in text for token in ("防水", "找平", "门槛石", "窗台石")):
            return round(area * 0.45, 2)
        if any(token in text for token in ("墙", "抹灰", "乳胶漆", "刷漆")):
            return round(area * 1.2, 2)
        return round(area, 2)
    if _is_length_unit(unit):
        base_length = max(area**0.5 * 4, 8.0)
        if any(token in text for token in ("管", "线", "电缆", "桥架", "配管", "配线")):
            return round(base_length * 1.5, 2)
        return round(base_length, 2)
    if unit in {"个", "只", "盏", "套", "台", "樘"}:
        return float(_rough_mvp_count(text, unit=unit, index=index))
    return 1.0


def _rough_mvp_count(text: str, *, unit: str, index: int) -> int:
    if any(token in text for token in ("灯", "筒灯", "射灯", "格栅灯")):
        return 12
    if "插座" in text:
        return 10
    if "开关" in text:
        return 6
    if "地漏" in text:
        return 2
    if any(token in text for token in ("阀门", "阀")):
        return 4
    if any(token in text for token in ("门", "窗", "隔断", "配电箱", "水表", "台盆", "马桶", "洁具")):
        return 1
    if unit in {"套", "台", "樘"}:
        return 1
    return max(1, min(12, index))


def _is_area_unit(unit: str) -> bool:
    normalized = unit.lower().replace(" ", "")
    return normalized in {"㎡", "m²", "m2", "平方米", "平米"} or "²" in unit


def _is_length_unit(unit: str) -> bool:
    normalized = unit.lower().replace(" ", "")
    return normalized in {"m", "米"}


def _format_quantity(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _write_csv(path: Path, rows: list[Mapping[str, Any]], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _build_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# PDF AI 候选工程量报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 状态：{summary.get('ai_quantity_status', '-')}",
        f"- 映射项目数：{summary.get('standard_mapping_row_count', 0)}",
        f"- 候选工程量数：{summary.get('ai_quantity_candidate_count', 0)}",
        "",
        "## 边界",
        "",
        "- 本报告只提供 AI 候选工程量，不是最终工程量。",
        "- 业务员必须复核公式、证据和风险后，才能回填正式四字段清单。",
        "",
        "## 候选",
        "",
        "| 候选量 | 识别编号 | 项目 | 建议量 | 公式 | 证据 | 置信度 |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for row in list(report.get("suggestion_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("候选量编号")),
                    _md(row.get("识别编号")),
                    _md(row.get("项目名称")),
                    _md(f"{row.get('建议工程量', '')}{row.get('单位', '')}"),
                    _md(row.get("计算式")),
                    _md(row.get("证据文本")),
                    _md(row.get("置信度")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _md(value: Any) -> str:
    return _clean_text(value).replace("|", "\\|").replace("\n", "<br>")
