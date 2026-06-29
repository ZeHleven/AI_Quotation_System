from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageDraw


PHASE = "BIZ-2x-pdf-layout-planner"
SCHEMA_VERSION = "drawing_layout_plan_v1"

LayoutPlanner = Callable[[list[dict[str, Any]]], Mapping[str, Any] | str]

REGION_TYPES = {
    "title_block",
    "material_table",
    "legend",
    "design_note",
    "main_plan",
    "elevation",
    "section",
    "node_detail",
    "schedule",
    "dimension_dense_area",
    "drawing_index",
    "revision_block",
    "finish_plan",
    "ceiling_plan",
    "floor_plan",
    "wall_elevation",
    "door_window_schedule",
    "curtain_wall_detail",
    "aluminum_panel_detail",
    "stone_detail",
    "glass_detail",
    "unknown",
}

ATOMIC_TOOLS = {"ocr", "vlm_read", "llm_structure", "skip"}
COMPOSITE_TOOL_MAP = {
    "ocr_then_llm": ["ocr", "llm_structure"],
    "ocr_and_vlm": ["ocr", "vlm_read"],
    "vlm": ["vlm_read"],
    "vlm_read": ["vlm_read"],
}

DEFAULT_CROP_STRATEGY = {
    "scale": 2.0,
    "padding_ratio": 0.03,
}


def build_pdf_layout_plan_report(
    *,
    render_report: Mapping[str, Any],
    planner_dir: str | Path,
    layout_planner: LayoutPlanner | None = None,
    max_pages: int = 3,
    grid_size: int = 4,
    thumbnail_max_side: int = 1800,
) -> dict[str, Any]:
    directory = Path(planner_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    page_manifest = build_layout_planner_page_manifest(
        render_report=render_report,
        planner_dir=directory / "pages",
        max_pages=max_pages,
        grid_size=grid_size,
        thumbnail_max_side=thumbnail_max_side,
        warnings=warnings,
    )
    raw_plan: Mapping[str, Any] | str = {}
    status = "completed"
    if not page_manifest:
        status = "failed"
        errors.append(
            {
                "code": "LAYOUT_PLANNER_NO_RENDERED_PAGES",
                "message": "No rendered page image is available for layout planning.",
            }
        )
    elif layout_planner is None:
        status = "skipped"
        warnings.append(
            {
                "code": "LAYOUT_PLANNER_NOT_CONFIGURED",
                "message": "No VLM layout planner was configured; region OCR will use only default page crops.",
            }
        )
    else:
        try:
            raw_plan = layout_planner(page_manifest)
        except Exception as exc:  # noqa: BLE001 - planner failure must not stop the PDF agent
            raw_plan = {}
            status = "failed"
            errors.append(
                {
                    "code": "LAYOUT_PLANNER_CALL_FAILED",
                    "message": str(exc),
                }
            )

    normalized = normalize_layout_plan(raw_plan, page_manifest=page_manifest, warnings=warnings)
    if status == "completed" and warnings:
        status = "completed_with_warnings"
    summary = {
        "layout_plan_status": status,
        "layout_plan_page_count": len(page_manifest),
        "layout_plan_region_count": len(normalized.get("regions") or []),
        "layout_plan_high_priority_region_count": sum(
            1 for row in normalized.get("regions") or [] if _float(row.get("priority"), 0.0) >= 0.75
        ),
        "layout_plan_warning_count": len(warnings),
        "layout_plan_error_count": len(errors),
    }
    outputs = _write_layout_plan_outputs(
        planner_dir=directory,
        page_manifest=page_manifest,
        raw_plan=raw_plan,
        normalized_plan=normalized,
        summary=summary,
        warnings=warnings,
        errors=errors,
    )
    return {
        "ok": status not in {"failed"},
        "phase": PHASE,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "summary": summary,
        "page_manifest": page_manifest,
        "regions": list(normalized.get("regions") or []),
        "raw_plan": raw_plan,
        "warnings": warnings,
        "errors": errors,
        "outputs": outputs,
    }


def build_layout_planner_page_manifest(
    *,
    render_report: Mapping[str, Any],
    planner_dir: str | Path,
    max_pages: int,
    grid_size: int,
    thumbnail_max_side: int,
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    directory = Path(planner_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings = warnings if warnings is not None else []
    pages: list[dict[str, Any]] = []
    for render in render_report.get("render_rows") or []:
        if len(pages) >= max(0, int(max_pages or 0)):
            break
        image_path = Path(str(render.get("png_path") or ""))
        if not image_path.exists() or not image_path.is_file():
            warnings.append(
                {
                    "code": "LAYOUT_PLANNER_SOURCE_IMAGE_MISSING",
                    "message": "Rendered page image is missing.",
                    "source_image": str(image_path),
                }
            )
            continue
        try:
            with Image.open(image_path) as image:
                width, height = image.size
            page_no = _int(render.get("page"))
            source_file = _clean_text(render.get("source_file")) or image_path.name
            view_id = f"layout_p{page_no:03d}_{len(pages) + 1:03d}"
            output_path = directory / f"{_safe_stem(source_file)}_{view_id}_grid.png"
            thumb_width, thumb_height = create_layout_grid_thumbnail(
                image_path=image_path,
                output_path=output_path,
                grid_size=grid_size,
                max_side=thumbnail_max_side,
            )
            pages.append(
                {
                    "view_id": view_id,
                    "source_file": source_file,
                    "page": page_no,
                    "tile_type": "layout_planner_page",
                    "selection_role": "layout_planner",
                    "image_path": str(output_path.resolve()),
                    "source_image_path": str(image_path.resolve()),
                    "image_width_px": width,
                    "image_height_px": height,
                    "thumbnail_width_px": thumb_width,
                    "thumbnail_height_px": thumb_height,
                    "grid_size": max(1, int(grid_size or 1)),
                    "bbox_pixel": [0, 0, width, height],
                    "priority": 100,
                }
            )
        except Exception as exc:  # noqa: BLE001 - page thumbnail prep should degrade
            warnings.append(
                {
                    "code": "LAYOUT_PLANNER_PAGE_PREP_FAILED",
                    "message": str(exc),
                    "source_image": str(image_path),
                }
            )
    return pages


def create_layout_grid_thumbnail(
    *,
    image_path: Path,
    output_path: Path,
    grid_size: int = 4,
    max_side: int = 1800,
) -> tuple[int, int]:
    grid = max(1, int(grid_size or 1))
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    max_original_side = max(image.size)
    if max_original_side > max_side:
        scale = float(max_side) / float(max_original_side)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    width, height = image.size
    line_color = (255, 80, 80)
    text_color = (255, 0, 0)
    for index in range(1, grid):
        x = int(width * index / grid)
        y = int(height * index / grid)
        draw.line((x, 0, x, height), fill=line_color, width=2)
        draw.line((0, y, width, y), fill=line_color, width=2)
    for row in range(grid):
        for col in range(grid):
            label = f"{chr(ord('A') + col)}{row + 1}"
            x = int(width * col / grid) + 10
            y = int(height * row / grid) + 10
            draw.rectangle((x - 4, y - 3, x + 46, y + 20), fill=(255, 255, 255), outline=line_color)
            draw.text((x, y), label, fill=text_color)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return image.size


def build_layout_planner_prompt(page_payloads: list[dict[str, Any]]) -> str:
    page_context = [
        {
            "view_id": _clean_text(row.get("view_id")),
            "source_file": _clean_text(row.get("source_file")),
            "page": _int(row.get("page")),
            "grid_size": _int(row.get("grid_size")),
            "image_width_px": _int(row.get("image_width_px")),
            "image_height_px": _int(row.get("image_height_px")),
        }
        for row in page_payloads
    ]
    return (
        "你是工程图纸版面规划器，不是清单识别器。\n\n"
        "你的任务是根据整页缩略图和红色坐标网格，判断本页图纸哪些区域值得后续高清裁切、OCR识别或VLM精读。\n\n"
        "严格要求：\n"
        "1. 禁止输出施工清单、工程量、报价分项或最终工程判断。\n"
        "2. 不要把看不清的文字当成事实；只判断区域位置和后续读取策略。\n"
        "3. 所有 bbox_ratio 使用 0-1 页面比例坐标，格式为 [x1, y1, x2, y2]。\n"
        "4. region_type 只能从以下枚举选择：title_block, material_table, legend, design_note, main_plan, elevation, section, node_detail, schedule, dimension_dense_area, drawing_index, revision_block, finish_plan, ceiling_plan, floor_plan, wall_elevation, door_window_schedule, curtain_wall_detail, aluminum_panel_detail, stone_detail, glass_detail, unknown。\n"
        "5. recommended_tools 使用数组，只能包含 ocr, vlm_read, llm_structure, skip。\n"
        "6. priority 和 confidence 必须是 0 到 1 的数字。\n"
        "7. 请优先找图签、材料表、图例、设计说明、节点大样、尺寸密集区、主图区域。\n\n"
        "输出 JSON schema：\n"
        "{\n"
        '  "schema_version": "drawing_layout_plan_v1",\n'
        '  "page_type": "unknown",\n'
        '  "overall_assessment": "",\n'
        '  "regions": [\n'
        "    {\n"
        '      "region_id": "r001",\n'
        '      "view_id": "layout_p001_001",\n'
        '      "source_file": "",\n'
        '      "page": 1,\n'
        '      "region_type": "material_table",\n'
        '      "grid_ref": "G2-H4",\n'
        '      "bbox_ratio": [0.72, 0.18, 0.96, 0.42],\n'
        '      "priority": 0.9,\n'
        '      "confidence": 0.82,\n'
        '      "recommended_tools": ["ocr", "vlm_read"],\n'
        '      "expected_information": ["material_codes", "specifications"],\n'
        '      "crop_strategy": {"scale": 2.0, "padding_ratio": 0.03},\n'
        '      "reason": "疑似材料表或图例区域",\n'
        '      "risk_flags": ["thumbnail_text_unreadable"],\n'
        '      "risk_note": "缩略图中文字不可读，需要高清 OCR"\n'
        "    }\n"
        "  ],\n"
        '  "missing_or_unclear": [],\n'
        '  "planner_notes": ""\n'
        "}\n\n"
        "本次输入页面清单：\n"
        + json.dumps(page_context, ensure_ascii=False, separators=(",", ":"))
    )


def normalize_layout_plan(
    payload: Mapping[str, Any] | str,
    *,
    page_manifest: Sequence[Mapping[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    warnings = warnings if warnings is not None else []
    data = _coerce_json_object(payload, warnings=warnings)
    raw_regions = data.get("regions") if isinstance(data, Mapping) else []
    if isinstance(raw_regions, Mapping):
        raw_regions = [raw_regions]
    if not isinstance(raw_regions, list):
        raw_regions = []
    page_by_view_id = {_clean_text(row.get("view_id")): dict(row) for row in page_manifest if _clean_text(row.get("view_id"))}
    pages = [dict(row) for row in page_manifest]
    regions: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_regions, start=1):
        if not isinstance(raw, Mapping):
            continue
        page = _resolve_region_page(raw, pages=pages, page_by_view_id=page_by_view_id)
        bbox = _normalize_bbox_ratio(raw.get("bbox_ratio"), warnings=warnings, index=index)
        if bbox is None:
            continue
        region_type = _clean_text(raw.get("region_type")).lower()
        if region_type not in REGION_TYPES:
            warnings.append(
                {
                    "code": "LAYOUT_REGION_TYPE_UNKNOWN",
                    "message": f"Unknown region_type normalized to unknown: {region_type}",
                    "region_index": index,
                }
            )
            region_type = "unknown"
        region_id = _safe_identifier(_clean_text(raw.get("region_id")) or f"r{index:03d}") or f"r{index:03d}"
        regions.append(
            {
                "region_id": region_id,
                "view_id": _clean_text(raw.get("view_id")) or page.get("view_id", ""),
                "source_file": _clean_text(raw.get("source_file")) or page.get("source_file", ""),
                "page": _int(raw.get("page")) or _int(page.get("page")),
                "region_type": region_type,
                "grid_ref": _clean_text(raw.get("grid_ref")),
                "bbox_ratio": bbox,
                "priority": _clamp_float(raw.get("priority"), default=0.5),
                "confidence": _clamp_float(raw.get("confidence"), default=0.5),
                "recommended_tools": normalize_recommended_tools(raw.get("recommended_tools")),
                "expected_information": _clean_text_list(raw.get("expected_information")),
                "crop_strategy": normalize_crop_strategy(raw.get("crop_strategy")),
                "reason": _clean_text(raw.get("reason")),
                "risk_flags": _clean_text_list(raw.get("risk_flags")),
                "risk_note": _clean_text(raw.get("risk_note") or raw.get("risk")),
            }
        )
    return {
        "schema_version": _clean_text(data.get("schema_version")) or SCHEMA_VERSION,
        "page_type": _clean_text(data.get("page_type")) or "unknown",
        "overall_assessment": _clean_text(data.get("overall_assessment")),
        "regions": regions,
        "missing_or_unclear": _clean_text_list(data.get("missing_or_unclear")),
        "planner_notes": _clean_text(data.get("planner_notes") or data.get("global_recommendation")),
    }


def normalize_recommended_tools(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    for raw in raw_items:
        tool = _clean_text(raw).lower()
        expanded = COMPOSITE_TOOL_MAP.get(tool, [tool])
        for item in expanded:
            if item in ATOMIC_TOOLS and item not in result:
                result.append(item)
    return result or ["ocr"]


def normalize_crop_strategy(value: Any) -> dict[str, float]:
    strategy = value if isinstance(value, Mapping) else {}
    return {
        "scale": _clamp_float(strategy.get("scale"), default=DEFAULT_CROP_STRATEGY["scale"], low=1.0, high=4.0),
        "padding_ratio": _clamp_float(
            strategy.get("padding_ratio"),
            default=DEFAULT_CROP_STRATEGY["padding_ratio"],
            low=0.0,
            high=0.12,
        ),
    }


def _write_layout_plan_outputs(
    *,
    planner_dir: Path,
    page_manifest: Sequence[Mapping[str, Any]],
    raw_plan: Mapping[str, Any] | str,
    normalized_plan: Mapping[str, Any],
    summary: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    payloads = [
        ("layout_plan_json", planner_dir / "layout_plan.json", dict(normalized_plan)),
        ("layout_plan_pages_json", planner_dir / "layout_plan_pages.json", list(page_manifest)),
        ("layout_plan_raw_json", planner_dir / "layout_plan_raw.json", raw_plan),
        ("layout_plan_summary_json", planner_dir / "layout_plan_summary.json", dict(summary)),
        (
            "layout_plan_diagnostics_json",
            planner_dir / "layout_plan_diagnostics.json",
            {"warnings": list(warnings), "errors": list(errors)},
        ),
    ]
    outputs: dict[str, str] = {}
    for key, path, payload in payloads:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    return outputs


def _resolve_region_page(
    raw: Mapping[str, Any],
    *,
    pages: list[dict[str, Any]],
    page_by_view_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    view_id = _clean_text(raw.get("view_id") or raw.get("planner_view_id") or raw.get("page_view_id"))
    if view_id and view_id in page_by_view_id:
        return dict(page_by_view_id[view_id])
    source_file = _clean_text(raw.get("source_file"))
    page_no = _int(raw.get("page"))
    if source_file or page_no:
        for page in pages:
            if source_file and source_file != _clean_text(page.get("source_file")):
                continue
            if page_no and page_no != _int(page.get("page")):
                continue
            return dict(page)
    return pages[0] if pages else {}


def _normalize_bbox_ratio(
    value: Any,
    *,
    warnings: list[dict[str, Any]],
    index: int,
) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        warnings.append({"code": "LAYOUT_REGION_BBOX_INVALID", "message": "bbox_ratio must contain 4 numbers.", "region_index": index})
        return None
    try:
        x1, y1, x2, y2 = [float(item) for item in value]
    except (TypeError, ValueError):
        warnings.append({"code": "LAYOUT_REGION_BBOX_INVALID", "message": "bbox_ratio contains non-numeric values.", "region_index": index})
        return None
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 <= x1 or y2 <= y1:
        warnings.append({"code": "LAYOUT_REGION_BBOX_EMPTY", "message": "bbox_ratio has no area.", "region_index": index})
        return None
    return [round(x1, 6), round(y1, 6), round(x2, 6), round(y2, 6)]


def _coerce_json_object(payload: Mapping[str, Any] | str, *, warnings: list[dict[str, Any]]) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    text = str(payload or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        text = match.group(0) if match else "{}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        warnings.append({"code": "LAYOUT_PLANNER_JSON_INVALID", "message": "VLM layout planner returned invalid JSON."})
        return {}
    return data if isinstance(data, Mapping) else {}


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def _safe_stem(value: str) -> str:
    text = Path(value or "drawing").stem
    text = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("._")
    return text or "drawing"


def _clean_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _clean_text(raw)
        key = _normalize(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize(value: Any) -> str:
    return re.sub(r"[\s\-_—、，。；;:：/\\()（）\[\]{}【】<>\"'“”‘’+|]+", "", _clean_text(value)).lower()


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, *, default: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(low, min(high, number)), 4)
