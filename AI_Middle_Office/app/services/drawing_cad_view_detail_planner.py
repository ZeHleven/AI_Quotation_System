from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from PIL import Image

from app.services.drawing_layout_planner import create_layout_grid_thumbnail


PHASE = "BIZ-2x-cad-view-detail-planner"
SCHEMA_VERSION = "cad_view_detail_plan_v1"

CadViewDetailPlanner = Callable[[list[dict[str, Any]]], Mapping[str, Any] | str]

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
    "unknown",
}
REGION_SUBTYPES = {
    "right_title_bar",
    "right_material_grid",
    "bottom_note_bar",
    "material_code_callout",
    "local_legend",
    "local_design_note",
    "unknown",
}
ATOMIC_TOOLS = {"ocr", "vlm_read", "llm_structure", "skip"}


def build_cad_view_detail_plan_report(
    *,
    render_report: Mapping[str, Any],
    cad_view_report: Mapping[str, Any],
    planner_dir: str | Path,
    view_region_planner: CadViewDetailPlanner | None = None,
    max_views: int = 24,
    grid_size: int = 3,
    thumbnail_max_side: int = 1400,
) -> dict[str, Any]:
    directory = Path(planner_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    view_manifest = build_cad_view_detail_manifest(
        render_report=render_report,
        cad_view_report=cad_view_report,
        planner_dir=directory / "views",
        max_views=max_views,
        grid_size=grid_size,
        thumbnail_max_side=thumbnail_max_side,
        warnings=warnings,
    )
    structural_regions = build_structural_detail_regions(view_manifest=view_manifest, warnings=warnings)

    raw_plan: Mapping[str, Any] | str = {}
    vlm_regions: list[dict[str, Any]] = []
    status = "completed"
    if view_manifest and view_region_planner is not None:
        try:
            raw_plan = view_region_planner(view_manifest)
            vlm_regions = normalize_cad_view_detail_plan(raw_plan, view_manifest=view_manifest, warnings=warnings)
        except Exception as exc:  # noqa: BLE001 - VLM detail planning must degrade to structural bars
            raw_plan = {}
            errors.append({"code": "CAD_VIEW_DETAIL_PLANNER_CALL_FAILED", "message": str(exc)})
            status = "completed_with_warnings"
    elif not view_manifest:
        status = "skipped"
        warnings.append({"code": "CAD_VIEW_DETAIL_NO_CAD_VIEWS", "message": "No CAD view crops are available."})
    else:
        warnings.append(
            {
                "code": "CAD_VIEW_DETAIL_VLM_NOT_CONFIGURED",
                "message": "No CAD-view VLM planner configured; using structural right/bottom bar candidates.",
            }
        )

    regions = _dedupe_regions([*structural_regions, *vlm_regions], warnings=warnings)
    if status == "completed" and warnings:
        status = "completed_with_warnings"

    summary = {
        "cad_view_detail_plan_status": status,
        "cad_view_count": len(list(cad_view_report.get("view_rows") or [])),
        "selected_cad_view_count": len(view_manifest),
        "structural_detail_region_count": len(structural_regions),
        "vlm_detail_region_count": len(vlm_regions),
        "cad_view_detail_region_count": len(regions),
        "right_bar_region_count": sum(1 for row in regions if row.get("region_subtype") in {"right_title_bar", "right_material_grid"}),
        "bottom_note_region_count": sum(1 for row in regions if row.get("region_subtype") == "bottom_note_bar"),
        "cad_view_detail_warning_count": len(warnings),
        "cad_view_detail_error_count": len(errors),
    }
    outputs = _write_outputs(
        planner_dir=directory,
        view_manifest=view_manifest,
        raw_plan=raw_plan,
        regions=regions,
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
        "view_manifest": view_manifest,
        "regions": regions,
        "raw_plan": raw_plan,
        "warnings": warnings,
        "errors": errors,
        "outputs": outputs,
    }


def build_cad_view_detail_manifest(
    *,
    render_report: Mapping[str, Any],
    cad_view_report: Mapping[str, Any],
    planner_dir: str | Path,
    max_views: int,
    grid_size: int,
    thumbnail_max_side: int,
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    directory = Path(planner_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings = warnings if warnings is not None else []
    render_by_page = {
        (_clean_text(row.get("source_file")), _int(row.get("page"))): dict(row)
        for row in render_report.get("render_rows") or []
    }
    views: list[dict[str, Any]] = []
    for raw in sorted(
        list(cad_view_report.get("view_rows") or []),
        key=lambda row: (_int(row.get("page")), _bbox_top(row.get("bbox_pixel")), _bbox_left(row.get("bbox_pixel"))),
    ):
        if len(views) >= max(0, int(max_views or 0)):
            break
        image_path = Path(_clean_text(raw.get("image_path")))
        bbox_pixel = _normalize_pixel_bbox(raw.get("bbox_pixel"))
        source_file = _clean_text(raw.get("source_file"))
        page_no = _int(raw.get("page"))
        render = render_by_page.get((source_file, page_no), {})
        page_width = _int(render.get("image_width_px"))
        page_height = _int(render.get("image_height_px"))
        if not image_path.exists() or not image_path.is_file() or not bbox_pixel or page_width <= 0 or page_height <= 0:
            warnings.append(
                {
                    "code": "CAD_VIEW_DETAIL_SOURCE_VIEW_INVALID",
                    "message": "CAD view crop or page geometry is missing.",
                    "view_id": _clean_text(raw.get("tile_id")),
                }
            )
            continue
        try:
            with Image.open(image_path) as image:
                view_width, view_height = image.size
            view_id = _clean_text(raw.get("tile_id")) or f"p{page_no:03d}_view{len(views) + 1:03d}"
            output_path = directory / f"{_safe_stem(source_file)}_{_safe_identifier(view_id)}_detail_grid.png"
            thumb_width, thumb_height = create_layout_grid_thumbnail(
                image_path=image_path,
                output_path=output_path,
                grid_size=grid_size,
                max_side=thumbnail_max_side,
            )
            views.append(
                {
                    "view_id": view_id,
                    "source_file": source_file,
                    "page": page_no,
                    "tile_type": "cad_view_detail_page",
                    "selection_role": "cad_view_detail_planner",
                    "image_path": str(output_path.resolve()),
                    "source_image_path": str(image_path.resolve()),
                    "page_image_width_px": page_width,
                    "page_image_height_px": page_height,
                    "view_image_width_px": view_width,
                    "view_image_height_px": view_height,
                    "thumbnail_width_px": thumb_width,
                    "thumbnail_height_px": thumb_height,
                    "grid_size": max(1, int(grid_size or 1)),
                    "parent_bbox_pixel": bbox_pixel,
                    "parent_bbox_ratio": _pixel_bbox_to_ratio(bbox_pixel, width=page_width, height=page_height),
                    "priority": _float(raw.get("priority"), 0.0),
                    "view_frame_ink_ratio": _float(raw.get("view_frame_ink_ratio"), 0.0),
                    "view_frame_border_coverage": _float(raw.get("view_frame_border_coverage"), 0.0),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad CAD view should not stop the planner
            warnings.append(
                {
                    "code": "CAD_VIEW_DETAIL_VIEW_PREP_FAILED",
                    "message": str(exc),
                    "view_id": _clean_text(raw.get("tile_id")),
                }
            )
    return views


def build_structural_detail_regions(
    *,
    view_manifest: Sequence[Mapping[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    warnings = warnings if warnings is not None else []
    for view in view_manifest:
        image_path = Path(_clean_text(view.get("source_image_path")))
        if not image_path.exists():
            continue
        try:
            with Image.open(image_path) as image:
                image = image.convert("RGB")
                right_bbox, right_confidence, right_reason = _detect_right_bar_bbox(image)
                right_mid_bbox = [right_bbox[0], 0.30, right_bbox[2], 0.82]
                bottom_bbox, bottom_confidence, bottom_reason = _detect_bottom_note_bbox(image)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                {
                    "code": "CAD_VIEW_DETAIL_STRUCTURAL_DETECT_FAILED",
                    "message": str(exc),
                    "view_id": _clean_text(view.get("view_id")),
                }
            )
            continue
        rows.append(
            _region_from_local_bbox(
                view=view,
                local_bbox_ratio=right_bbox,
                region_id_suffix="right_title_bar",
                region_type="title_block",
                region_subtype="right_title_bar",
                priority=0.92,
                confidence=right_confidence,
                expected_information=["drawing_name", "drawing_number", "material_codes", "view_notes"],
                reason=right_reason,
            )
        )
        rows.append(
            _region_from_local_bbox(
                view=view,
                local_bbox_ratio=right_mid_bbox,
                region_id_suffix="right_material_grid",
                region_type="material_table",
                region_subtype="right_material_grid",
                priority=0.94,
                confidence=max(0.55, right_confidence - 0.05),
                expected_information=["material_codes", "specifications", "legend_keys"],
                reason="Middle part of the right-side title/material column is likely to contain small material grids or legends.",
            )
        )
        rows.append(
            _region_from_local_bbox(
                view=view,
                local_bbox_ratio=bottom_bbox,
                region_id_suffix="bottom_note_bar",
                region_type="design_note",
                region_subtype="bottom_note_bar",
                priority=0.88,
                confidence=bottom_confidence,
                expected_information=["drawing_name", "drawing_number", "notes", "scale"],
                reason=bottom_reason,
            )
        )
    return [row for row in rows if row.get("bbox_ratio")]


def build_cad_view_detail_planner_prompt(view_payloads: list[dict[str, Any]]) -> str:
    view_context = [
        {
            "view_id": _clean_text(row.get("view_id")),
            "source_file": _clean_text(row.get("source_file")),
            "page": _int(row.get("page")),
            "grid_size": _int(row.get("grid_size")),
            "parent_bbox_pixel": row.get("parent_bbox_pixel") or [],
            "view_image_width_px": _int(row.get("view_image_width_px")),
            "view_image_height_px": _int(row.get("view_image_height_px")),
        }
        for row in view_payloads
    ]
    return (
        "You are a CAD view internal layout planner, not a quantity-list generator.\n\n"
        "Each image is one cropped CAD view from a large PDF sheet. Your task is to mark small high-value "
        "sub-regions inside each CAD view for later high-resolution OCR or VLM reading.\n\n"
        "Focus on these tiny areas:\n"
        "- right-side green title/material bar\n"
        "- bottom yellow note/title strip\n"
        "- local material table or legend inside the view\n"
        "- dense material-code callouts such as CT/PT/ST/MT/GL/WD\n\n"
        "Rules:\n"
        "1. Do not output construction items, quantities, pricing items, or engineering conclusions.\n"
        "2. All bbox_ratio values are relative to the cropped CAD view image, not the full PDF page.\n"
        "3. Use only these region_type values: title_block, material_table, legend, design_note, main_plan, "
        "elevation, section, node_detail, schedule, dimension_dense_area, unknown.\n"
        "4. Use only these region_subtype values: right_title_bar, right_material_grid, bottom_note_bar, "
        "material_code_callout, local_legend, local_design_note, unknown.\n"
        "5. recommended_tools must be an array containing only ocr, vlm_read, llm_structure, skip.\n"
        "6. priority and confidence must be numbers from 0 to 1.\n"
        "7. If a text area is too small to read in the thumbnail, still mark it and describe the risk.\n\n"
        "Return strict JSON only:\n"
        "{\n"
        '  "schema_version": "cad_view_detail_plan_v1",\n'
        '  "regions": [\n'
        "    {\n"
        '      "region_id": "v001_r001",\n'
        '      "view_id": "p001_view001",\n'
        '      "region_type": "title_block",\n'
        '      "region_subtype": "right_title_bar",\n'
        '      "grid_ref": "C1-C3",\n'
        '      "bbox_ratio": [0.82, 0.02, 0.99, 0.98],\n'
        '      "priority": 0.9,\n'
        '      "confidence": 0.8,\n'
        '      "recommended_tools": ["ocr", "vlm_read"],\n'
        '      "expected_information": ["drawing_name", "material_codes", "specifications"],\n'
        '      "crop_strategy": {"scale": 4.0, "padding_ratio": 0.005},\n'
        '      "reason": "right-side colored title/material bar",\n'
        '      "risk_flags": ["tiny_text"],\n'
        '      "risk_note": "needs high-resolution OCR"\n'
        "    }\n"
        "  ],\n"
        '  "missing_or_unclear": [],\n'
        '  "planner_notes": ""\n'
        "}\n\n"
        "Input CAD view manifest:\n"
        + json.dumps(view_context, ensure_ascii=False, separators=(",", ":"))
    )


def normalize_cad_view_detail_plan(
    payload: Mapping[str, Any] | str,
    *,
    view_manifest: Sequence[Mapping[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    warnings = warnings if warnings is not None else []
    data = _coerce_json_object(payload, warnings=warnings)
    raw_regions = data.get("regions") if isinstance(data, Mapping) else []
    if isinstance(raw_regions, Mapping):
        raw_regions = [raw_regions]
    if not isinstance(raw_regions, list):
        raw_regions = []
    views = {_clean_text(row.get("view_id")): dict(row) for row in view_manifest}
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_regions, start=1):
        if not isinstance(raw, Mapping):
            continue
        view_id = _clean_text(raw.get("view_id"))
        view = views.get(view_id)
        if view is None:
            warnings.append(
                {
                    "code": "CAD_VIEW_DETAIL_UNKNOWN_VIEW",
                    "message": "VLM region references an unknown CAD view.",
                    "region_index": index,
                    "view_id": view_id,
                }
            )
            continue
        local_bbox = _normalize_bbox_ratio(raw.get("bbox_ratio"), warnings=warnings, index=index)
        if local_bbox is None:
            continue
        region_type = _clean_text(raw.get("region_type")).lower()
        if region_type not in REGION_TYPES:
            region_type = "unknown"
        region_subtype = _clean_text(raw.get("region_subtype")).lower()
        if region_subtype not in REGION_SUBTYPES:
            region_subtype = "unknown"
        row = _region_from_local_bbox(
            view=view,
            local_bbox_ratio=local_bbox,
            region_id_suffix=_safe_identifier(raw.get("region_id")) or f"vlm_{index:03d}",
            region_type=region_type,
            region_subtype=region_subtype,
            priority=_float(raw.get("priority"), 0.0),
            confidence=_float(raw.get("confidence"), 0.0),
            expected_information=_string_list(raw.get("expected_information")),
            reason=_clean_text(raw.get("reason")),
            planner_source="vlm_cad_view_detail",
            grid_ref=_clean_text(raw.get("grid_ref")),
            recommended_tools=_normalize_tools(raw.get("recommended_tools")),
            crop_strategy=_normalize_crop_strategy(raw.get("crop_strategy")),
            risk_flags=_string_list(raw.get("risk_flags")),
            risk_note=_clean_text(raw.get("risk_note")),
        )
        if row.get("bbox_ratio"):
            rows.append(row)
    return rows


def _region_from_local_bbox(
    *,
    view: Mapping[str, Any],
    local_bbox_ratio: Sequence[float],
    region_id_suffix: str,
    region_type: str,
    region_subtype: str,
    priority: float,
    confidence: float,
    expected_information: Sequence[str],
    reason: str,
    planner_source: str = "structural_cad_view_detail",
    grid_ref: str = "",
    recommended_tools: Sequence[str] | None = None,
    crop_strategy: Mapping[str, Any] | None = None,
    risk_flags: Sequence[str] | None = None,
    risk_note: str = "",
) -> dict[str, Any]:
    page_bbox = _local_bbox_to_page_ratio(local_bbox_ratio, view=view)
    view_id = _clean_text(view.get("view_id"))
    return {
        "region_id": _safe_identifier(f"{view_id}_{region_id_suffix}"),
        "view_id": view_id,
        "source_file": _clean_text(view.get("source_file")),
        "page": _int(view.get("page")),
        "region_type": region_type if region_type in REGION_TYPES else "unknown",
        "region_subtype": region_subtype if region_subtype in REGION_SUBTYPES else "unknown",
        "planner_source": planner_source,
        "grid_ref": grid_ref,
        "bbox_ratio": page_bbox,
        "local_bbox_ratio": [round(float(item), 6) for item in local_bbox_ratio],
        "parent_view_bbox_ratio": list(view.get("parent_bbox_ratio") or []),
        "parent_view_bbox_pixel": list(view.get("parent_bbox_pixel") or []),
        "priority": round(max(0.0, min(1.0, float(priority))), 4),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "recommended_tools": list(recommended_tools or ["ocr", "vlm_read"]),
        "expected_information": list(expected_information),
        "crop_strategy": dict(crop_strategy or {"scale": 4.0, "padding_ratio": 0.005}),
        "reason": reason,
        "risk_flags": list(risk_flags or ["tiny_text"]),
        "risk_note": risk_note or "Small CAD title/material text requires high-resolution OCR.",
    }


def _detect_right_bar_bbox(image: Image.Image) -> tuple[list[float], float, str]:
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width <= 0 or height <= 0:
        return [0.82, 0.0, 1.0, 1.0], 0.4, "Fallback right-side bar."
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    green_mask = (green > 145) & (red < 140) & (blue < 160) & (green > red + 35) & (green > blue + 15)
    roi_start = int(width * 0.62)
    roi = green_mask[:, roi_start:]
    column_counts = roi.sum(axis=0)
    threshold = max(3, int(height * 0.012))
    indexes = np.where(column_counts >= threshold)[0]
    if indexes.size:
        x1 = max(int(width * 0.80), roi_start + int(indexes.min()) - int(width * 0.015))
        return [round(x1 / width, 6), 0.0, 1.0, 1.0], 0.82, "Detected green pixels concentrated on the right-side CAD title/material bar."
    return [0.82, 0.0, 1.0, 1.0], 0.55, "Fallback to the right-side strip because no strong green bar was detected."


def _detect_bottom_note_bbox(image: Image.Image) -> tuple[list[float], float, str]:
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width <= 0 or height <= 0:
        return [0.0, 0.88, 1.0, 1.0], 0.4, "Fallback bottom note strip."
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    yellow_mask = (red > 170) & (green > 145) & (blue < 130)
    green_mask = (green > 145) & (red < 140) & (blue < 160) & (green > red + 35)
    bottom_start = int(height * 0.70)
    roi = yellow_mask[bottom_start:, :] | green_mask[bottom_start:, :]
    row_counts = roi.sum(axis=1)
    threshold = max(3, int(width * 0.01))
    indexes = np.where(row_counts >= threshold)[0]
    if indexes.size:
        y1 = max(0, bottom_start + int(indexes.min()) - int(height * 0.025))
        return [0.0, round(y1 / height, 6), 1.0, 1.0], 0.78, "Detected yellow/green pixels in the bottom CAD note/title strip."
    return [0.0, 0.86, 1.0, 1.0], 0.5, "Fallback to the bottom strip because no strong yellow note bar was detected."


def _local_bbox_to_page_ratio(local_bbox: Sequence[float], *, view: Mapping[str, Any]) -> list[float]:
    parent = _normalize_pixel_bbox(view.get("parent_bbox_pixel"))
    page_width = _int(view.get("page_image_width_px"))
    page_height = _int(view.get("page_image_height_px"))
    if not parent or page_width <= 0 or page_height <= 0:
        return []
    px1, py1, px2, py2 = parent
    width = max(1, px2 - px1)
    height = max(1, py2 - py1)
    lx1, ly1, lx2, ly2 = [float(item) for item in local_bbox]
    x1 = px1 + lx1 * width
    y1 = py1 + ly1 * height
    x2 = px1 + lx2 * width
    y2 = py1 + ly2 * height
    return [
        round(max(0.0, min(1.0, x1 / page_width)), 6),
        round(max(0.0, min(1.0, y1 / page_height)), 6),
        round(max(0.0, min(1.0, x2 / page_width)), 6),
        round(max(0.0, min(1.0, y2 / page_height)), 6),
    ]


def _dedupe_regions(regions: Sequence[Mapping[str, Any]], *, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for region in sorted(
        regions,
        key=lambda row: (
            -_float(row.get("priority"), 0.0),
            -_float(row.get("confidence"), 0.0),
            _clean_text(row.get("view_id")),
            _clean_text(row.get("region_subtype")),
        ),
    ):
        duplicate = False
        for kept in selected:
            if _clean_text(kept.get("view_id")) != _clean_text(region.get("view_id")):
                continue
            if _clean_text(kept.get("region_subtype")) != _clean_text(region.get("region_subtype")):
                continue
            if _bbox_iou(kept.get("bbox_ratio"), region.get("bbox_ratio")) >= 0.85:
                duplicate = True
                warnings.append(
                    {
                        "code": "CAD_VIEW_DETAIL_REGION_DEDUPED",
                        "message": "Overlapping CAD-view detail region removed.",
                        "removed_region_id": region.get("region_id"),
                        "kept_region_id": kept.get("region_id"),
                    }
                )
                break
        if not duplicate:
            selected.append(dict(region))
    return sorted(selected, key=lambda row: (_int(row.get("page")), _bbox_top(row.get("bbox_ratio")), _bbox_left(row.get("bbox_ratio"))))


def _write_outputs(
    *,
    planner_dir: Path,
    view_manifest: Sequence[Mapping[str, Any]],
    raw_plan: Mapping[str, Any] | str,
    regions: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    payloads = [
        ("cad_view_detail_plan_json", planner_dir / "cad_view_detail_plan.json", {"regions": list(regions)}),
        ("cad_view_detail_views_json", planner_dir / "cad_view_detail_views.json", list(view_manifest)),
        ("cad_view_detail_raw_json", planner_dir / "cad_view_detail_raw.json", raw_plan),
        ("cad_view_detail_summary_json", planner_dir / "cad_view_detail_summary.json", dict(summary)),
        (
            "cad_view_detail_diagnostics_json",
            planner_dir / "cad_view_detail_diagnostics.json",
            {"warnings": list(warnings), "errors": list(errors)},
        ),
    ]
    outputs: dict[str, str] = {}
    for key, path, payload in payloads:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    return outputs


def _coerce_json_object(payload: Mapping[str, Any] | str, *, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return dict(payload)
    text = _clean_text(payload)
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.DOTALL)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            warnings.append({"code": "CAD_VIEW_DETAIL_JSON_PARSE_FAILED", "message": "Planner did not return a JSON object."})
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            warnings.append({"code": "CAD_VIEW_DETAIL_JSON_PARSE_FAILED", "message": str(exc)})
            return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _normalize_bbox_ratio(value: Any, *, warnings: list[dict[str, Any]], index: int) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        warnings.append({"code": "CAD_VIEW_DETAIL_BBOX_INVALID", "message": "bbox_ratio must contain four numbers.", "region_index": index})
        return None
    try:
        x1, y1, x2, y2 = [float(item) for item in value]
    except (TypeError, ValueError):
        warnings.append({"code": "CAD_VIEW_DETAIL_BBOX_INVALID", "message": "bbox_ratio contains non-numeric values.", "region_index": index})
        return None
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        warnings.append({"code": "CAD_VIEW_DETAIL_BBOX_OUT_OF_RANGE", "message": "bbox_ratio is out of range.", "region_index": index})
        return None
    return [round(x1, 6), round(y1, 6), round(x2, 6), round(y2, 6)]


def _normalize_pixel_bbox(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    try:
        x1, y1, x2, y2 = [int(round(float(item))) for item in value]
    except (TypeError, ValueError):
        return []
    if x1 >= x2 or y1 >= y2:
        return []
    return [x1, y1, x2, y2]


def _pixel_bbox_to_ratio(bbox: Sequence[int], *, width: int, height: int) -> list[float]:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        round(max(0.0, min(1.0, x1 / width)), 6),
        round(max(0.0, min(1.0, y1 / height)), 6),
        round(max(0.0, min(1.0, x2 / width)), 6),
        round(max(0.0, min(1.0, y2 / height)), 6),
    ]


def _normalize_tools(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return ["ocr", "vlm_read"]
    tools = [_clean_text(item).lower() for item in value]
    tools = [item for item in tools if item in ATOMIC_TOOLS]
    return tools or ["ocr", "vlm_read"]


def _normalize_crop_strategy(value: Any) -> dict[str, float]:
    strategy = value if isinstance(value, Mapping) else {}
    return {
        "scale": max(1.0, min(4.0, _float(strategy.get("scale"), 4.0))),
        "padding_ratio": max(0.0, min(0.03, _float(strategy.get("padding_ratio"), 0.005))),
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _bbox_iou(left: Any, right: Any) -> float:
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)) or len(left) != 4 or len(right) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = [float(item) for item in left]
    bx1, by1, bx2, by2 = [float(item) for item in right]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = _bbox_area([ax1, ay1, ax2, ay2]) + _bbox_area([bx1, by1, bx2, by2]) - inter
    return inter / union if union > 0 else 0.0


def _bbox_area(bbox: Sequence[float]) -> float:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_left(value: Any) -> float:
    return float(value[0]) if isinstance(value, (list, tuple)) and value else 0.0


def _bbox_top(value: Any) -> float:
    return float(value[1]) if isinstance(value, (list, tuple)) and len(value) > 1 else 0.0


def _safe_identifier(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", _clean_text(value)).strip("_")


def _safe_stem(value: str) -> str:
    text = Path(value or "drawing").stem
    text = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("._")
    return text or "drawing"


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


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
