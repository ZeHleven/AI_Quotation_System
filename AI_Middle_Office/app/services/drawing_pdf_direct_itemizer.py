from __future__ import annotations

import asyncio
import base64
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.core.config import settings
from app.services.cad_view_frame_detector import (
    CAD_VIEW_TILE_TYPE,
    augment_tile_report_with_cad_views,
    build_cad_view_frame_report,
)
from app.services.drawing_pdf_evidence_pipeline import (
    build_pdf_basic_parse_report,
    build_pdf_render_report,
    build_pdf_tile_report,
    collect_pdf_files,
)
from app.services.drawing_pdf_ai_quantity_suggester import (
    apply_ai_quantity_suggestions_to_four_field_rows,
    build_pdf_ai_quantity_suggestion_report,
    write_pdf_ai_quantity_suggestion_outputs,
)
from app.services.model_gateway import call_glm_pdf_drawing_itemize
from app.services.quantity_list_export import write_quantity_list_outputs
from app.services.quantity_standard_index import search_standard_index


PHASE = "BIZ-2x-pdf-direct-itemization"

PDF_ITEM_HEADERS = [
    "识别编号",
    "PDF文件",
    "页码",
    "tile_id",
    "图纸项目名称",
    "空间/部位",
    "材料编号",
    "规格/做法",
    "证据文本",
    "建议单位",
    "置信度",
    "需人工复核",
    "识别原因",
]

STANDARD_MAPPING_HEADERS = [
    "识别编号",
    "映射状态",
    "标准号",
    "标准项目编码",
    "标准项目名称",
    "标准章节",
    "标准单位",
    "匹配分数",
    "匹配原因",
    "候选数量",
    "项目特征",
    "工程量计算规则",
    "工程量",
]


class PdfDirectItemizationError(ValueError):
    pass


def run_pdf_direct_itemization(
    *,
    pdf_dir: str | Path,
    output_dir: str | Path,
    timestamp: str | None = None,
    render_dpi: int = 350,
    tile_grid_size: int = 3,
    max_visual_images: int | None = None,
    style_prompt_text: str = "",
) -> dict[str, Any]:
    run_timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    source_dir = Path(pdf_dir)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    debug_dir = target_dir / "debug" / run_timestamp
    business_dir = target_dir / "business" / run_timestamp
    debug_dir.mkdir(parents=True, exist_ok=True)
    business_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = collect_pdf_files(source_dir)
    if not pdf_files:
        raise PdfDirectItemizationError("没有找到可识别的 .pdf 文件")

    parse_report = build_pdf_basic_parse_report(pdf_files)
    render_report = build_pdf_render_report(
        parse_report,
        debug_dir / f"pages_{run_timestamp}",
        render_dpi=render_dpi,
    )
    tile_report = build_pdf_tile_report(
        parse_report=parse_report,
        render_report=render_report,
        tile_dir=debug_dir / f"tiles_{run_timestamp}",
        grid_size=tile_grid_size,
    )
    cad_view_report = build_cad_view_frame_report(
        parse_report=parse_report,
        render_report=render_report,
        view_dir=debug_dir / f"cad_views_{run_timestamp}",
    )
    tile_report = augment_tile_report_with_cad_views(tile_report, cad_view_report)
    itemization_report = build_pdf_llm_itemization_report(
        parse_report=parse_report,
        tile_report=tile_report,
        max_visual_images=max_visual_images,
        style_prompt_text=style_prompt_text,
        trace_id=f"pdf-direct-itemize-{run_timestamp}",
    )
    item_rows = dedupe_pdf_item_rows(itemization_report.get("item_rows") or [])
    mapping_rows = build_standard_mapping_rows(item_rows)
    base_quantity_list_rows = build_four_field_rows(mapping_rows)
    ai_quantity_report = build_optional_pdf_ai_quantity_suggestion_report(
        parse_report=parse_report,
        tile_report=tile_report,
        mapping_rows=mapping_rows,
        trace_id=f"pdf-ai-quantity-{run_timestamp}",
    )
    quantity_list_rows = apply_ai_quantity_suggestions_to_four_field_rows(base_quantity_list_rows, ai_quantity_report)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = build_pdf_direct_itemization_summary(
        parse_report=parse_report,
        render_report=render_report,
        tile_report=tile_report,
        itemization_report=itemization_report,
        item_rows=item_rows,
        mapping_rows=mapping_rows,
        quantity_list_rows=quantity_list_rows,
        ai_quantity_report=ai_quantity_report,
    )
    issues = build_pdf_direct_itemization_issues(render_report, itemization_report, mapping_rows)
    issues.extend(list((ai_quantity_report or {}).get("issues") or []))
    outputs = write_pdf_direct_itemization_outputs(
        {
            "ok": True,
            "phase": PHASE,
            "generated_at": generated_at,
            "summary": summary,
            "parse_report": parse_report,
            "render_report": render_report,
            "cad_view_report": cad_view_report,
            "tile_report": tile_report,
            "itemization_report": itemization_report,
            "item_rows": item_rows,
            "standard_mapping_rows": mapping_rows,
            "base_quantity_list_rows": base_quantity_list_rows,
            "quantity_list_rows": quantity_list_rows,
            "ai_quantity_report": ai_quantity_report,
            "issues": issues,
        },
        business_dir=business_dir,
        debug_dir=debug_dir,
        run_timestamp=run_timestamp,
    )
    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": generated_at,
        "summary": summary,
        "quantity_list_rows": quantity_list_rows,
        "base_quantity_list_rows": base_quantity_list_rows,
        "pdf_direct_item_rows": item_rows,
        "standard_mapping_rows": mapping_rows,
        "pdf_ai_quantity_summary": (ai_quantity_report or {}).get("summary", {}),
        "pdf_ai_quantity_rows": (ai_quantity_report or {}).get("suggestion_rows", []),
        "pdf_page_rows": list(parse_report.get("page_rows") or []),
        "pdf_render_rows": list(render_report.get("render_rows") or []),
        "pdf_cad_view_rows": list(cad_view_report.get("view_rows") or []),
        "pdf_tile_rows": list(tile_report.get("tile_rows") or []),
        "outputs": outputs,
        "issues": issues,
    }


def build_pdf_llm_itemization_report(
    *,
    parse_report: Mapping[str, Any],
    tile_report: Mapping[str, Any],
    max_visual_images: int | None = None,
    style_prompt_text: str = "",
    trace_id: str | None = None,
) -> dict[str, Any]:
    max_images = settings.pdf_direct_itemization_max_images if max_visual_images is None else int(max_visual_images or 0)
    selected_images = select_images_for_pdf_itemization(list(tile_report.get("tile_rows") or []), max_images=max_images)
    page_texts = _page_text_index(parse_report)
    item_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    if not selected_images:
        return {
            "ok": True,
            "phase": "PDF-Direct-2-llm-itemization",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "llm_itemization_status": "skipped",
                "selected_image_count": 0,
                "llm_success_count": 0,
                "llm_error_count": 0,
                "llm_skipped_count": 1,
                "raw_item_count": 0,
            },
            "item_rows": [],
            "llm_status_rows": [{"status": "skipped", "reason": "no_rendered_pdf_images", "tile_id": ""}],
        }

    for image in selected_images:
        raw_image_path = str(image.get("image_path") or "").strip()
        image_path = Path(raw_image_path) if raw_image_path else None
        tile_id = str(image.get("tile_id") or "")
        if not image_path or not image_path.exists() or not image_path.is_file():
            status_rows.append({"status": "skipped", "reason": "image_missing", "tile_id": tile_id})
            continue
        try:
            model_result = _run_pdf_itemization_call(
                image_path=image_path,
                image_row=image,
                page_texts=page_texts,
                style_prompt_text=style_prompt_text,
                trace_id=trace_id,
            )
        except Exception as exc:
            status_rows.append({"status": "error", "reason": str(exc)[:300], "tile_id": tile_id})
            continue
        drawing_items = model_result.get("drawing_items") or []
        for item in drawing_items:
            item_rows.append(_pdf_item_row(item, image, len(item_rows) + 1))
        status_rows.append(
            {
                "status": "success",
                "reason": f"itemized_{len(drawing_items)}_items",
                "tile_id": tile_id,
                "raw_content": str(model_result.get("raw_content") or "")[:800],
            }
        )

    success_count = sum(1 for row in status_rows if row.get("status") == "success")
    error_count = sum(1 for row in status_rows if row.get("status") == "error")
    skipped_count = sum(1 for row in status_rows if row.get("status") == "skipped")
    return {
        "ok": True,
        "phase": "PDF-Direct-2-llm-itemization",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "llm_itemization_status": _llm_itemization_status(success_count, error_count, skipped_count),
            "selected_image_count": len(selected_images),
            "llm_success_count": success_count,
            "llm_error_count": error_count,
            "llm_skipped_count": skipped_count,
            "raw_item_count": len(item_rows),
        },
        "item_rows": item_rows,
        "llm_status_rows": status_rows,
    }


def build_optional_pdf_ai_quantity_suggestion_report(
    *,
    parse_report: Mapping[str, Any],
    tile_report: Mapping[str, Any],
    mapping_rows: list[Mapping[str, Any]],
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    if not settings.feature_pdf_ai_quantity_suggestion:
        return None
    try:
        return build_pdf_ai_quantity_suggestion_report(
            parse_report=parse_report,
            tile_report=tile_report,
            mapping_rows=mapping_rows,
            max_visual_images=settings.pdf_ai_quantity_suggestion_max_images,
            trace_id=trace_id,
        )
    except Exception as exc:
        return {
            "ok": False,
            "phase": "BIZ-2x-pdf-ai-quantity-suggestion",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "ai_quantity_status": "failed",
                "ai_quantity_candidate_count": 0,
            },
            "suggestion_rows": [],
            "llm_status_rows": [],
            "issues": [
                {
                    "级别": "warning",
                    "说明": f"PDF AI 候选工程量生成失败，已保留待算量：{exc}",
                }
            ],
        }


def select_images_for_pdf_itemization(tile_rows: list[dict[str, Any]], *, max_images: int) -> list[dict[str, Any]]:
    if max_images <= 0:
        return []
    rendered = [
        row
        for row in tile_rows
        if row.get("image_path")
        and Path(str(row.get("image_path"))).exists()
        and Path(str(row.get("image_path"))).is_file()
    ]
    whole_pages = [row for row in rendered if row.get("tile_type") == "whole_page_preview"]
    grid_tiles = [row for row in rendered if row.get("tile_type") == "grid"]
    cad_views = [row for row in rendered if row.get("tile_type") == CAD_VIEW_TILE_TYPE]
    whole_pages.sort(key=lambda row: (int(_float(row.get("page"), 0)), str(row.get("tile_id") or "")))
    cad_views.sort(
        key=lambda row: (
            int(_float(row.get("page"), 0)),
            str(row.get("source_file") or ""),
            str(row.get("tile_id") or ""),
        )
    )
    grid_tiles.sort(
        key=lambda row: (
            -int(_float(row.get("priority"), 0)),
            -_image_file_size(row.get("image_path")),
            str(row.get("source_file") or ""),
            str(row.get("tile_id") or ""),
        )
    )
    if cad_views:
        selected = cad_views[:max_images]
        remaining = max_images - len(selected)
        if remaining > 0:
            selected.extend(whole_pages[:remaining])
        return selected
    selected: list[dict[str, Any]] = []
    selected.extend(whole_pages[: min(len(whole_pages), max_images)])
    remaining = max_images - len(selected)
    if remaining > 0:
        selected.extend(grid_tiles[:remaining])
    return selected


def _image_file_size(path: Any) -> int:
    try:
        image_path = Path(str(path or ""))
        if image_path.exists() and image_path.is_file():
            return image_path.stat().st_size
    except (OSError, ValueError):
        return 0
    return 0


def dedupe_pdf_item_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _dedupe_pdf_item_rows_by_project_action(rows)


def _dedupe_pdf_item_rows_by_project_action(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    id_key = "\u8bc6\u522b\u7f16\u53f7"
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        cleaned = dict(row)
        _clean_pdf_item_placeholder_fields(cleaned)
        key = _pdf_item_project_action_key(cleaned)
        if not key.strip("|"):
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = cleaned
            continue
        _merge_pdf_item_row(current, cleaned)
    result = list(merged.values())
    for index, row in enumerate(result, start=1):
        row[id_key] = f"PDFITEM-{index:06d}"
    return result


def _pdf_item_project_action_key(row: Mapping[str, Any]) -> str:
    name = _clean_text(row.get("\u56fe\u7eb8\u9879\u76ee\u540d\u79f0"))
    material = _clean_text(row.get("\u6750\u6599\u7f16\u53f7"))
    spec = _clean_text(row.get("\u89c4\u683c/\u505a\u6cd5"))
    if not name and not material and not spec:
        return ""
    action_bucket = _pdf_item_action_bucket(f"{name} {spec}")
    return "|".join([_normalize(name), _normalize(material), action_bucket])


def _pdf_item_action_bucket(text: str) -> str:
    value = _clean_text(text)
    buckets = [
        ("\u6e7f\u8d34", "wet_paste"),
        ("\u5e72\u6302", "dry_hang"),
        ("\u62c6\u9664", "demolition"),
        ("\u540a\u9876", "ceiling"),
        ("\u8e22\u811a\u7ebf", "skirting"),
        ("\u95e8\u5957", "door_trim"),
        ("\u53cc\u5f00\u95e8", "double_door"),
        ("\u5355\u5f00\u95e8", "single_door"),
        ("\u95e8", "door"),
        ("\u9632\u6c34", "waterproof"),
        ("\u7f8e\u7f1d", "joint"),
        ("\u706f\u69fd", "light_trough"),
        ("\u7a97\u5e18\u76d2", "curtain_box"),
        ("\u6d82\u6599", "paint"),
    ]
    for token, bucket in buckets:
        if token in value:
            return bucket
    return "general"


def _merge_pdf_item_row(current: dict[str, Any], row: Mapping[str, Any]) -> None:
    space_key = "\u7a7a\u95f4/\u90e8\u4f4d"
    material_key = "\u6750\u6599\u7f16\u53f7"
    spec_key = "\u89c4\u683c/\u505a\u6cd5"
    evidence_key = "\u8bc1\u636e\u6587\u672c"
    unit_key = "\u5efa\u8bae\u5355\u4f4d"
    confidence_key = "\u7f6e\u4fe1\u5ea6"
    review_key = "\u9700\u4eba\u5de5\u590d\u6838"
    reason_key = "\u8bc6\u522b\u539f\u56e0"

    current[space_key] = _join_unique([current.get(space_key), row.get(space_key)])
    current[material_key] = _join_unique([current.get(material_key), row.get(material_key)])
    current[spec_key] = _join_unique([current.get(spec_key), row.get(spec_key)])
    current[evidence_key] = _join_unique([current.get(evidence_key), row.get(evidence_key)])
    current["tile_id"] = _join_unique([current.get("tile_id"), row.get("tile_id")], separator=",")
    if not _clean_text(current.get(unit_key)) and _clean_text(row.get(unit_key)):
        current[unit_key] = row.get(unit_key)
    current[confidence_key] = max(_float(current.get(confidence_key), 0), _float(row.get(confidence_key), 0))
    current[review_key] = current.get(review_key) or row.get(review_key)
    current[reason_key] = _join_unique([current.get(reason_key), row.get(reason_key)])


def _clean_pdf_item_placeholder_fields(row: dict[str, Any]) -> None:
    for key in ("\u89c4\u683c/\u505a\u6cd5", "\u8bc1\u636e\u6587\u672c"):
        if _is_pdf_item_placeholder_value(row.get(key)):
            row[key] = ""


def _is_pdf_item_placeholder_value(value: Any) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    placeholders = [
        "\u6750\u6599\u7f16\u53f7\u3001\u6750\u6599\u540d\u79f0\u3001\u89c4\u683c\u3001\u505a\u6cd5\u3001\u5b89\u88c5\u65b9\u5f0f\u6216\u6784\u9020\u8bf4\u660e",
        "\u56fe\u7eb8\u4e0a\u53ef\u89c1\u7684\u539f\u6587\u6216\u53ef\u8ffd\u6eaf\u4f9d\u636e",
    ]
    return any(placeholder in text for placeholder in placeholders)


def _legacy_dedupe_pdf_item_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = "|".join(
            [
                _normalize(row.get("图纸项目名称")),
                _normalize(row.get("空间/部位")),
                _normalize(row.get("材料编号")),
                _normalize(row.get("规格/做法")),
            ]
        )
        if not key.strip("|"):
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = dict(row)
            continue
        current["置信度"] = max(_float(current.get("置信度"), 0), _float(row.get("置信度"), 0))
        current["证据文本"] = _join_unique([current.get("证据文本"), row.get("证据文本")])
        current["tile_id"] = _join_unique([current.get("tile_id"), row.get("tile_id")], separator=",")
    result = list(merged.values())
    for index, row in enumerate(result, start=1):
        row["识别编号"] = f"PDFITEM-{index:06d}"
    return result


def build_standard_mapping_rows(item_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in item_rows:
        query = " ".join(
            [
                str(item.get("图纸项目名称") or ""),
                str(item.get("空间/部位") or ""),
                str(item.get("材料编号") or ""),
                str(item.get("规格/做法") or ""),
                str(item.get("证据文本") or ""),
            ]
        )
        candidates = search_standard_index(query, limit=5)
        selected = candidates[0] if candidates else {}
        unit_options = list(selected.get("unit_options") or [])
        unit = unit_options[0] if unit_options else str(item.get("建议单位") or "")
        mapping_status = "standard_mapped" if selected else "manual_standard_mapping_required"
        feature = build_project_feature_text(item, selected)
        quantity_rule = selected.get("quantity_rule") if isinstance(selected.get("quantity_rule"), Mapping) else {}
        rows.append(
            {
                "识别编号": item.get("识别编号", ""),
                "映射状态": mapping_status,
                "标准号": selected.get("standard_code", ""),
                "标准项目编码": selected.get("item_code", ""),
                "标准项目名称": selected.get("item_name", "") or item.get("图纸项目名称", ""),
                "标准章节": selected.get("chapter_name", ""),
                "标准单位": unit,
                "匹配分数": selected.get("score", ""),
                "匹配原因": selected.get("match_reason", ""),
                "候选数量": len(candidates),
                "项目特征": feature,
                "工程量计算规则": quantity_rule.get("rule_text", ""),
                "工程量": "待算量",
                "source_item": item,
                "standard_candidates": candidates,
            }
        )
    return rows


def build_four_field_rows(mapping_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in mapping_rows:
        rows.append(
            {
                "项目名称": build_display_project_name(row),
                "项目特征": row.get("项目特征", ""),
                "单位": row.get("标准单位", ""),
                "工程量": row.get("工程量", "待算量") or "待算量",
            }
        )
    return rows


def build_display_project_name(mapping_row: Mapping[str, Any]) -> str:
    source_item = mapping_row.get("source_item") or {}
    concrete_name = _clean_text(source_item.get("图纸项目名称") if isinstance(source_item, Mapping) else "")
    standard_name = _clean_text(mapping_row.get("标准项目名称"))
    if not concrete_name:
        return standard_name
    if not standard_name or _normalize(concrete_name) == _normalize(standard_name):
        return concrete_name
    return f"{concrete_name}（{standard_name}）"


def build_project_feature_text(item: Mapping[str, Any], selected: Mapping[str, Any]) -> str:
    parts: list[str] = []
    feature_fields = []
    for field in selected.get("feature_fields") or []:
        if isinstance(field, Mapping):
            name = str(field.get("name") or "").strip()
        else:
            name = str(field or "").strip()
        if name:
            feature_fields.append(name)
    source_parts = []
    if item.get("空间/部位"):
        source_parts.append(f"空间/部位：{item.get('空间/部位')}")
    if item.get("材料编号"):
        source_parts.append(f"材料编号：{item.get('材料编号')}")
    if item.get("规格/做法"):
        source_parts.append(f"规格/做法：{item.get('规格/做法')}")
    if item.get("证据文本"):
        source_parts.append(f"图纸证据：{item.get('证据文本')}")
    source_text = "；".join(source_parts)
    if feature_fields and source_text:
        parts.extend(f"{field}：{source_text}" for field in feature_fields[:4])
    elif source_text:
        parts.append(source_text)
    if not parts:
        parts.append("PDF 图纸识别，需人工补充项目特征")
    return "；".join(parts)


def write_pdf_direct_itemization_outputs(
    report: Mapping[str, Any],
    *,
    business_dir: Path,
    debug_dir: Path,
    run_timestamp: str,
) -> dict[str, str]:
    outputs: dict[str, str] = {}
    quantity_outputs = write_quantity_list_outputs(
        list(report.get("quantity_list_rows") or []),
        business_dir,
        stem=f"BIZ2x_PDF直接识图四字段清单_{run_timestamp}",
    )
    outputs["quantity_list_xlsx"] = quantity_outputs["xlsx"]
    outputs["quantity_list_csv"] = quantity_outputs["csv"]

    item_csv = debug_dir / f"BIZ2x_PDF直接识图项目候选_{run_timestamp}.csv"
    _write_csv(item_csv, list(report.get("item_rows") or []), PDF_ITEM_HEADERS)
    outputs["pdf_direct_item_csv"] = str(item_csv.resolve())

    mapping_csv = debug_dir / f"BIZ2x_PDF直接识图标准映射_{run_timestamp}.csv"
    _write_csv(mapping_csv, list(report.get("standard_mapping_rows") or []), STANDARD_MAPPING_HEADERS)
    outputs["pdf_direct_standard_mapping_csv"] = str(mapping_csv.resolve())

    ai_quantity_report = report.get("ai_quantity_report") if isinstance(report.get("ai_quantity_report"), Mapping) else None
    if ai_quantity_report:
        outputs.update(
            write_pdf_ai_quantity_suggestion_outputs(
                ai_quantity_report,
                debug_dir,
                stem=f"BIZ2x_PDF_AI候选工程量_{run_timestamp}",
            )
        )

    markdown = debug_dir / f"BIZ2x_PDF直接识图列项_{run_timestamp}.md"
    markdown.write_text(_build_markdown(report), encoding="utf-8")
    outputs["pdf_direct_itemization_markdown"] = str(markdown.resolve())

    debug_json = debug_dir / f"BIZ2x_PDF直接识图列项_{run_timestamp}.json"
    debug_payload = dict(report)
    outputs["pdf_direct_itemization_json"] = str(debug_json.resolve())
    debug_payload["outputs"] = outputs
    debug_json.write_text(json.dumps(_json_safe_report(debug_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs


def build_pdf_direct_itemization_summary(
    *,
    parse_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    tile_report: Mapping[str, Any],
    itemization_report: Mapping[str, Any],
    item_rows: list[dict[str, Any]],
    mapping_rows: list[dict[str, Any]],
    quantity_list_rows: list[dict[str, Any]],
    ai_quantity_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    render_summary = render_report.get("summary") or {}
    itemization_summary = itemization_report.get("summary") or {}
    ai_summary = (ai_quantity_report or {}).get("summary") or {}
    mapping_status_counts = Counter(row.get("映射状态", "") for row in mapping_rows)
    return {
        "pdf_file_count": (parse_report.get("summary") or {}).get("pdf_file_count", 0),
        "pdf_page_count": (parse_report.get("summary") or {}).get("page_count", 0),
        "pdf_text_row_count": (parse_report.get("summary") or {}).get("text_row_count", 0),
        "pdf_render_status": render_summary.get("render_status", ""),
        "pdf_rendered_page_count": render_summary.get("rendered_page_count", 0),
        "pdf_tile_count": (tile_report.get("summary") or {}).get("tile_count", 0),
        "pdf_cad_view_frame_count": (tile_report.get("summary") or {}).get("cad_view_frame_count", 0),
        "pdf_direct_itemization_status": itemization_summary.get("llm_itemization_status", ""),
        "pdf_direct_selected_image_count": itemization_summary.get("selected_image_count", 0),
        "pdf_direct_raw_item_count": itemization_summary.get("raw_item_count", 0),
        "pdf_direct_item_count": len(item_rows),
        "standard_mapped_count": mapping_status_counts.get("standard_mapped", 0),
        "manual_standard_mapping_required_count": mapping_status_counts.get("manual_standard_mapping_required", 0),
        "quantity_list_row_count": len(quantity_list_rows),
        "pdf_ai_quantity_status": ai_summary.get("ai_quantity_status", "disabled_or_not_run"),
        "pdf_ai_quantity_candidate_count": ai_summary.get("ai_quantity_candidate_count", 0),
        "pdf_ai_quantity_needs_review_count": ai_summary.get("candidate_needs_manual_review_count", 0),
    }


def build_pdf_direct_itemization_issues(
    render_report: Mapping[str, Any],
    itemization_report: Mapping[str, Any],
    mapping_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    render_status = (render_report.get("summary") or {}).get("render_status")
    if render_status != "rendered":
        issues.append({"级别": "warning", "说明": "PDF 未完全渲染为高清 PNG，PDF 直接视觉列项可能不可用或不完整。"})
    itemization_status = (itemization_report.get("summary") or {}).get("llm_itemization_status")
    if itemization_status in {"skipped", "error"}:
        issues.append({"级别": "error", "说明": "PDF 视觉模型未成功生成项目候选，请检查 Poppler/pdftoppm 和视觉模型配置。"})
    manual_count = sum(1 for row in mapping_rows if row.get("映射状态") != "standard_mapped")
    if manual_count:
        issues.append({"级别": "warning", "说明": f"{manual_count} 条 PDF 项目候选未映射到标准库，需人工确认。"})
    return issues


def _run_pdf_itemization_call(
    *,
    image_path: Path,
    image_row: Mapping[str, Any],
    page_texts: dict[tuple[str, int], list[str]],
    style_prompt_text: str = "",
    trace_id: str | None = None,
) -> dict[str, Any]:
    if not image_path.exists() or not image_path.is_file():
        raise FileNotFoundError(str(image_path))
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
        "quantity_policy": "禁止估算工程量；工程量统一由系统后置为待算量",
    }
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            call_glm_pdf_drawing_itemize(
                encoded,
                _image_mime_type(image_path),
                page_context=context,
                prompt_addition=_direct_itemization_prompt_addition(style_prompt_text),
                trace_id=trace_id,
            )
        )
    raise RuntimeError("running_event_loop_in_sync_pdf_direct_itemization")


def _direct_itemization_prompt_addition(style_prompt_text: str) -> str:
    cleaned = _clean_style_prompt_for_json_itemization(style_prompt_text)
    if not cleaned:
        return ""
    max_chars = 1800
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "\n\n[已截断：仅保留前 1800 字人工列项口径]"


def _clean_style_prompt_for_json_itemization(style_prompt_text: str) -> str:
    text = str(style_prompt_text or "").strip()
    if not text:
        return ""
    marker = "## 三、简短版提示词"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = _clean_text(text)
    if not text:
        return ""
    return (
        "采用以下人工预算员列项口径，但必须保持主提示词要求的严格 JSON schema，"
        "只输出 drawing_items 数组，不要输出 Markdown 表格或解释文字。\n"
        f"{text}"
    )


def _pdf_item_row(item: Mapping[str, Any], image: Mapping[str, Any], index: int) -> dict[str, Any]:
    return {
        "识别编号": f"PDFITEM-{index:06d}",
        "PDF文件": image.get("source_file", ""),
        "页码": image.get("page", ""),
        "tile_id": image.get("tile_id", ""),
        "图纸项目名称": _clean_text(item.get("item_name")),
        "空间/部位": _clean_text(item.get("space")),
        "材料编号": "、".join(_clean_text(code) for code in item.get("material_codes") or [] if _clean_text(code)),
        "规格/做法": _clean_text(item.get("spec_or_method")),
        "证据文本": _clean_text(item.get("evidence_text")),
        "建议单位": _clean_text(item.get("suggested_unit")),
        "置信度": round(_float(item.get("confidence"), 0.0), 3),
        "需人工复核": "是" if item.get("needs_manual_review", True) else "否",
        "识别原因": _clean_text(item.get("reason")),
    }


def _page_text_index(parse_report: Mapping[str, Any]) -> dict[tuple[str, int], list[str]]:
    result: dict[tuple[str, int], list[str]] = {}
    for row in parse_report.get("text_rows") or []:
        text = _clean_text(row.get("text"))
        if not text:
            continue
        key = (str(row.get("source_file") or ""), int(_float(row.get("page"), 0)))
        result.setdefault(key, []).append(text[:300])
    return result


def _llm_itemization_status(success_count: int, error_count: int, skipped_count: int) -> str:
    if success_count:
        return "success" if not error_count else "partial_success"
    if error_count:
        return "error"
    if skipped_count:
        return "skipped"
    return "no_images_selected"


def _build_markdown(report: Mapping[str, Any]) -> str:
    summary = build_pdf_direct_itemization_summary(
        parse_report=report.get("parse_report") or {},
        render_report=report.get("render_report") or {},
        tile_report=report.get("tile_report") or {},
        itemization_report=report.get("itemization_report") or {},
        item_rows=list(report.get("item_rows") or []),
        mapping_rows=list(report.get("standard_mapping_rows") or []),
        quantity_list_rows=list(report.get("quantity_list_rows") or []),
        ai_quantity_report=report.get("ai_quantity_report") or {},
    )
    lines = [
        "# PDF 直接识图列项报告",
        "",
        f"- PDF 文件数：{summary.get('pdf_file_count', 0)}",
        f"- PDF 页数：{summary.get('pdf_page_count', 0)}",
        f"- PNG 渲染状态：{summary.get('pdf_render_status', '-')}",
        f"- LLM 列项状态：{summary.get('pdf_direct_itemization_status', '-')}",
        f"- 项目候选数：{summary.get('pdf_direct_item_count', 0)}",
        f"- 标准库映射数：{summary.get('standard_mapped_count', 0)}",
        f"- AI 候选工程量：{summary.get('pdf_ai_quantity_candidate_count', 0)}",
        f"- 四字段清单行数：{summary.get('quantity_list_row_count', 0)}",
        "",
        "## 边界",
        "",
        "- 本路线只生成项目清单候选。",
        "- AI 可输出候选工程量，但必须显示为待确认，不作为最终工程量。",
        "- DXF/PDF 证据融合不参与本次主流程。",
    ]
    return "\n".join(lines)


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _json_safe_report(report: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(report)
    result["standard_mapping_rows"] = [
        {
            key: value
            for key, value in dict(row).items()
            if key not in {"source_item", "standard_candidates"}
        }
        for row in report.get("standard_mapping_rows") or []
    ]
    return result


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize(value: Any) -> str:
    return re.sub(r"[\s\-_—、，。；;:：|/\\()（）\[\]{}【】<>\"'“”‘’]+", "", _clean_text(value).lower())


def _join_unique(values: list[Any], *, separator: str = "；") -> str:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        for part in re.split(r"[；;]", text):
            cleaned = _clean_text(part)
            key = _normalize(cleaned)
            if not cleaned or key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
    return separator.join(result)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
