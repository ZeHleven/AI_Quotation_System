from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw

from app.services.drawing_cad_view_detail_planner import build_cad_view_detail_manifest
from app.services.drawing_layout_planner import create_layout_grid_thumbnail


PHASE = "BIZ-2x-material-region-mvp"
SCHEMA_VERSION = "material_region_plan_v1"

MaterialRegionPlanner = Callable[[list[dict[str, Any]]], Mapping[str, Any] | str]

REGION_TYPES = {
    "material_table",
    "legend_table",
    "material_callout",
    "finish_code_label",
    "material_note",
    "finish_schedule",
    "design_note",
    "title_block",
    "unknown",
}
ATOMIC_TOOLS = {"ocr", "vlm_read", "llm_structure", "skip"}
COMPOSITE_TOOL_MAP = {
    "ocr_then_llm": ["ocr", "llm_structure"],
    "ocr_and_vlm": ["ocr", "vlm_read"],
    "vlm": ["vlm_read"],
    "highres_render": ["ocr"],
}
MATERIAL_CODE_RE = re.compile(
    r"(?<![A-Z0-9])((?:CT|PT|ST|MT|GL|WD|WC|MR|AL|SS|TL|W)\s*[-_]?\s*[A-Z0-9]{1,4})(?![A-Z0-9])",
    re.IGNORECASE,
)
MATERIAL_WORD_RE = re.compile(
    r"(tile|stone|glass|stainless|steel|wood|paint|brick|panel|finish|wall|floor|ceiling|aluminum)",
    re.IGNORECASE,
)


def build_material_region_plan_report(
    *,
    render_report: Mapping[str, Any],
    cad_view_report: Mapping[str, Any] | None = None,
    planner_dir: str | Path,
    material_region_planner: MaterialRegionPlanner | None = None,
    max_pages: int = 2,
    max_cad_views: int = 8,
    max_regions: int = 32,
    grid_size: int = 4,
    thumbnail_max_side: int = 1600,
    iou_threshold: float = 0.68,
) -> dict[str, Any]:
    directory = Path(planner_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    page_manifest = build_material_page_manifest(
        render_report=render_report,
        planner_dir=directory / "pages",
        max_pages=max_pages,
        grid_size=grid_size,
        thumbnail_max_side=thumbnail_max_side,
        warnings=warnings,
    )
    cad_view_manifest = build_material_cad_view_manifest(
        render_report=render_report,
        cad_view_report=cad_view_report or {},
        planner_dir=directory / "cad_views",
        max_views=max_cad_views,
        grid_size=grid_size,
        thumbnail_max_side=thumbnail_max_side,
        warnings=warnings,
    )
    view_manifest = [*page_manifest, *cad_view_manifest]
    local_regions = build_local_material_candidate_regions(view_manifest=view_manifest, warnings=warnings)

    raw_plan: Mapping[str, Any] | str = {}
    vlm_regions: list[dict[str, Any]] = []
    if material_region_planner is not None and view_manifest:
        try:
            raw_plan = material_region_planner(view_manifest)
            vlm_regions = normalize_material_region_plan(raw_plan, view_manifest=view_manifest, warnings=warnings)
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": "MATERIAL_REGION_VLM_CALL_FAILED", "message": str(exc)})

    regions = _dedupe_regions([*vlm_regions, *local_regions], iou_threshold=iou_threshold, warnings=warnings)
    regions = sorted(
        regions,
        key=lambda row: (
            -_float(row.get("priority"), 0.0),
            -_float(row.get("confidence"), 0.0),
            _int(row.get("page")),
            _bbox_top(row.get("bbox_ratio")),
            _bbox_left(row.get("bbox_ratio")),
        ),
    )[: max(0, int(max_regions or 0))]
    regions = sorted(regions, key=lambda row: (_int(row.get("page")), _bbox_top(row.get("bbox_ratio")), _bbox_left(row.get("bbox_ratio"))))

    status = "completed"
    if not view_manifest:
        status = "skipped"
        warnings.append({"code": "MATERIAL_REGION_NO_VIEW_MANIFEST", "message": "No page or CAD-view images are available."})
    elif errors and regions:
        status = "completed_with_errors"
    elif errors:
        status = "failed"
    elif warnings:
        status = "completed_with_warnings"

    annotation_outputs = _write_page_annotations(render_report=render_report, regions=regions, output_dir=directory / "annotations")
    summary = {
        "material_region_plan_status": status,
        "planner_view_count": len(view_manifest),
        "page_planner_view_count": len(page_manifest),
        "cad_view_planner_view_count": len(cad_view_manifest),
        "local_material_region_count": len(local_regions),
        "vlm_material_region_count": len(vlm_regions),
        "material_region_count": len(regions),
        "material_table_region_count": sum(1 for row in regions if row.get("region_type") in {"material_table", "legend_table", "finish_schedule"}),
        "material_callout_region_count": sum(1 for row in regions if row.get("region_type") in {"material_callout", "finish_code_label"}),
        "warning_count": len(warnings),
        "error_count": len(errors),
    }
    outputs = _write_plan_outputs(
        planner_dir=directory,
        view_manifest=view_manifest,
        raw_plan=raw_plan,
        regions=regions,
        summary=summary,
        warnings=warnings,
        errors=errors,
    )
    outputs.update(annotation_outputs)
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


def build_material_page_manifest(
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
    rows: list[dict[str, Any]] = []
    for render in render_report.get("render_rows") or []:
        if len(rows) >= max(0, int(max_pages or 0)):
            break
        image_path = Path(_clean_text(render.get("png_path")))
        if not image_path.exists() or not image_path.is_file():
            warnings.append({"code": "MATERIAL_REGION_PAGE_IMAGE_MISSING", "message": "Rendered page image is missing."})
            continue
        try:
            with Image.open(image_path) as image:
                width, height = image.size
            page_no = _int(render.get("page"))
            source_file = _clean_text(render.get("source_file")) or image_path.name
            view_id = f"material_page_p{page_no:03d}_{len(rows) + 1:03d}"
            output_path = directory / f"{_safe_stem(source_file)}_{view_id}_grid.png"
            thumb_width, thumb_height = create_layout_grid_thumbnail(
                image_path=image_path,
                output_path=output_path,
                grid_size=grid_size,
                max_side=thumbnail_max_side,
            )
            rows.append(
                {
                    "view_id": view_id,
                    "source_file": source_file,
                    "page": page_no,
                    "tile_type": "material_page",
                    "selection_role": "material_region_page_planner",
                    "image_path": str(output_path.resolve()),
                    "source_image_path": str(image_path.resolve()),
                    "image_width_px": width,
                    "image_height_px": height,
                    "page_image_width_px": width,
                    "page_image_height_px": height,
                    "thumbnail_width_px": thumb_width,
                    "thumbnail_height_px": thumb_height,
                    "grid_size": max(1, int(grid_size or 1)),
                    "bbox_pixel": [0, 0, width, height],
                    "parent_bbox_pixel": [0, 0, width, height],
                    "parent_bbox_ratio": [0.0, 0.0, 1.0, 1.0],
                    "priority": 100,
                }
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append({"code": "MATERIAL_REGION_PAGE_PREP_FAILED", "message": str(exc), "image_path": str(image_path)})
    return rows


def build_material_cad_view_manifest(
    *,
    render_report: Mapping[str, Any],
    cad_view_report: Mapping[str, Any],
    planner_dir: str | Path,
    max_views: int,
    grid_size: int,
    thumbnail_max_side: int,
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not cad_view_report or not (cad_view_report.get("view_rows") or []):
        return []
    rows = build_cad_view_detail_manifest(
        render_report=render_report,
        cad_view_report=cad_view_report,
        planner_dir=planner_dir,
        max_views=max_views,
        grid_size=grid_size,
        thumbnail_max_side=thumbnail_max_side,
        warnings=warnings,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        patched = dict(row)
        patched["tile_type"] = "material_cad_view"
        patched["selection_role"] = "material_region_cad_view_planner"
        result.append(patched)
    return result


def build_local_material_candidate_regions(
    *,
    view_manifest: Sequence[Mapping[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    max_regions_per_view: int = 10,
) -> list[dict[str, Any]]:
    warnings = warnings if warnings is not None else []
    regions: list[dict[str, Any]] = []
    for view in view_manifest:
        source_image = Path(_clean_text(view.get("source_image_path") or view.get("image_path")))
        if not source_image.exists() or not source_image.is_file():
            continue
        try:
            with Image.open(source_image) as image:
                image = image.convert("RGB")
                candidates = _detect_material_color_candidates(image)
        except Exception as exc:  # noqa: BLE001
            warnings.append({"code": "MATERIAL_REGION_LOCAL_DETECT_FAILED", "message": str(exc), "view_id": view.get("view_id")})
            continue
        for index, candidate in enumerate(candidates[:max_regions_per_view], start=1):
            regions.append(
                _region_from_local_bbox(
                    view=view,
                    local_bbox_ratio=candidate["bbox_ratio"],
                    region_id_suffix=f"local_{index:03d}",
                    region_type=candidate["region_type"],
                    priority=min(_float(candidate["priority"], 0.0), 0.72),
                    confidence=min(_float(candidate["confidence"], 0.0), 0.62),
                    expected_information=candidate["expected_information"],
                    reason=candidate["reason"],
                    planner_source="local_material_visual_scan",
                    recommended_tools=["ocr", "vlm_read"],
                    crop_strategy={"highres_scale": 64.0, "padding_ratio": candidate["padding_ratio"]},
                    visible_clues=candidate["visible_clues"],
                    risk_flags=["needs_highres_render", "ocr_required"],
                    risk_note="Local visual scan found material-like colored text or table geometry; OCR must confirm the content.",
                )
            )
    return regions


def build_material_region_planner_prompt(view_payloads: list[dict[str, Any]]) -> str:
    view_context = [
        {
            "view_id": _clean_text(row.get("view_id")),
            "source_file": _clean_text(row.get("source_file")),
            "page": _int(row.get("page")),
            "tile_type": _clean_text(row.get("tile_type")),
            "selection_role": _clean_text(row.get("selection_role")),
            "grid_size": _int(row.get("grid_size")),
            "parent_bbox_pixel": row.get("parent_bbox_pixel") or row.get("bbox_pixel") or [],
            "page_image_width_px": _int(row.get("page_image_width_px") or row.get("image_width_px")),
            "page_image_height_px": _int(row.get("page_image_height_px") or row.get("image_height_px")),
        }
        for row in view_payloads
    ]
    return (
        "You are a material-information candidate region planner for architectural/CAD drawings.\n\n"
        "Your job is only to find regions that may contain material information. Do not create a bill of quantities, "
        "do not infer quantities, and do not treat unreadable text as fact.\n\n"
        "Find regions such as material tables, legend tables, finish schedules, CT/PT/ST/MT/W1/W2/W3 labels, "
        "red leader-line material callouts, yellow/green material notes, and dense material-code labels.\n\n"
        "All bbox_ratio values must be relative to the image identified by view_id. If the image is a CAD view crop, "
        "the system will convert your local bbox to full-page coordinates.\n\n"
        "Allowed region_type values: material_table, legend_table, material_callout, finish_code_label, "
        "material_note, finish_schedule, design_note, title_block, unknown.\n"
        "Allowed recommended_tools values: ocr, vlm_read, ocr_then_llm, ocr_and_vlm, skip.\n\n"
        "Return strict JSON only:\n"
        "{\n"
        '  "schema_version": "material_region_plan_v1",\n'
        '  "regions": [\n'
        "    {\n"
        '      "region_id": "m001",\n'
        '      "view_id": "material_page_p001_001",\n'
        '      "region_type": "material_callout",\n'
        '      "grid_ref": "B2-C2",\n'
        '      "bbox_ratio": [0.30, 0.20, 0.48, 0.32],\n'
        '      "priority": 0.92,\n'
        '      "confidence": 0.78,\n'
        '      "recommended_tools": ["ocr_and_vlm"],\n'
        '      "expected_information": ["material_code", "material_name", "specification", "leader_target"],\n'
        '      "crop_strategy": {"highres_scale": 64, "padding_ratio": 0.05},\n'
        '      "visible_clues": ["colored material code box", "leader line", "adjacent material text"],\n'
        '      "reason": "Likely material callout with code and description.",\n'
        '      "risk_flags": ["thumbnail_text_unreadable"],\n'
        '      "risk_note": "Needs source-PDF high-resolution clipped rendering and OCR."\n'
        "    }\n"
        "  ],\n"
        '  "missing_or_unclear": [],\n'
        '  "planner_notes": ""\n'
        "}\n\n"
        "Input view manifest:\n"
        + json.dumps(view_context, ensure_ascii=False, separators=(",", ":"))
    )


def normalize_material_region_plan(
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
    view_by_id = {_clean_text(row.get("view_id")): dict(row) for row in view_manifest}
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_regions, start=1):
        if not isinstance(raw, Mapping):
            continue
        view_id = _clean_text(raw.get("view_id"))
        view = view_by_id.get(view_id)
        if view is None:
            warnings.append({"code": "MATERIAL_REGION_UNKNOWN_VIEW", "message": "Planner region references an unknown view_id.", "view_id": view_id})
            continue
        local_bbox = _normalize_bbox_ratio(raw.get("bbox_ratio"), warnings=warnings, index=index)
        if local_bbox is None:
            continue
        region_type = _normalize_region_type(raw.get("region_type"))
        rows.append(
            _region_from_local_bbox(
                view=view,
                local_bbox_ratio=local_bbox,
                region_id_suffix=_safe_identifier(raw.get("region_id")) or f"vlm_{index:03d}",
                region_type=region_type,
                priority=_clamp_float(raw.get("priority"), default=0.75),
                confidence=_clamp_float(raw.get("confidence"), default=0.65),
                expected_information=_string_list(raw.get("expected_information")),
                reason=_clean_text(raw.get("reason")),
                planner_source="vlm_material_region",
                grid_ref=_clean_text(raw.get("grid_ref")),
                recommended_tools=_normalize_tools(raw.get("recommended_tools")),
                crop_strategy=_normalize_crop_strategy(raw.get("crop_strategy")),
                visible_clues=_string_list(raw.get("visible_clues")),
                risk_flags=_string_list(raw.get("risk_flags")),
                risk_note=_clean_text(raw.get("risk_note") or raw.get("risk")),
            )
        )
    return [row for row in rows if row.get("bbox_ratio")]


def build_material_ocr_subcrop_report(
    *,
    highres_report: Mapping[str, Any],
    output_dir: str | Path,
    max_subcrops: int = 24,
    max_subcrops_per_region: int = 4,
    max_long_side_px: int = 1800,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    crop_manifest: list[dict[str, Any]] = []

    for region in highres_report.get("crop_manifest") or []:
        if len(crop_manifest) >= max_subcrops:
            break
        image_path = Path(_clean_text(region.get("image_path")))
        if not image_path.exists() or not image_path.is_file():
            continue
        try:
            with Image.open(image_path) as source:
                image = source.convert("RGB")
                candidates = _detect_material_color_candidates(image, grid_cols=36, grid_rows=36, include_fallback=True)
                if not candidates:
                    candidates = [{"bbox_ratio": [0.0, 0.0, 1.0, 1.0], "region_type": region.get("region_type") or "unknown"}]
                for index, candidate in enumerate(candidates[:max_subcrops_per_region], start=1):
                    if len(crop_manifest) >= max_subcrops:
                        break
                    bbox = _pad_bbox_ratio(candidate["bbox_ratio"], 0.025)
                    pixel_bbox = _ratio_to_pixel_bbox(bbox, width=image.width, height=image.height)
                    if _pixel_area(pixel_bbox) <= 0:
                        continue
                    crop = image.crop(tuple(pixel_bbox))
                    crop, scale_factor = _cap_image_long_side(crop, max_long_side_px=max_long_side_px)
                    crop_id = f"material_ocr_{len(crop_manifest) + 1:03d}_{_safe_identifier(region.get('region_id'))}"
                    output_path = directory / f"{crop_id}.png"
                    crop.save(output_path)
                    crop_manifest.append(
                        {
                            "crop_id": crop_id,
                            "crop_type": "material_ocr_subcrop",
                            "parent_highres_crop_id": _clean_text(region.get("crop_id")),
                            "region_id": _clean_text(region.get("region_id")),
                            "region_type": _clean_text(region.get("region_type")) or "unknown",
                            "source_file": _clean_text(region.get("source_file")),
                            "page": _int(region.get("page")),
                            "source_image_path": str(image_path.resolve()),
                            "image_path": str(output_path.resolve()),
                            "bbox_ratio": bbox,
                            "bbox_pixel": pixel_bbox,
                            "image_width_px": crop.width,
                            "image_height_px": crop.height,
                            "scale_factor": scale_factor,
                            "render_method": region.get("render_method"),
                            "source_quality": region.get("source_quality"),
                            "is_upscaled_from_lowres": region.get("is_upscaled_from_lowres"),
                            "reason": "OCR-ready subcrop from high-resolution PDF clipped region.",
                            "status": "created",
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": "MATERIAL_OCR_SUBCROP_FAILED", "message": str(exc), "image_path": str(image_path)})

    status = "completed"
    if errors and crop_manifest:
        status = "completed_with_errors"
    elif errors:
        status = "failed"
    elif not crop_manifest:
        status = "skipped"
        warnings.append({"code": "MATERIAL_OCR_SUBCROP_EMPTY", "message": "No OCR subcrops were created."})
    summary = {
        "material_ocr_subcrop_status": status,
        "source_highres_crop_count": len(list(highres_report.get("crop_manifest") or [])),
        "material_ocr_subcrop_count": len(crop_manifest),
        "warning_count": len(warnings),
        "error_count": len(errors),
    }
    outputs = _write_simple_outputs(
        directory=directory,
        payloads=[
            ("material_ocr_subcrop_summary_json", "material_ocr_subcrop_summary.json", summary),
            ("material_ocr_subcrop_manifest_json", "material_ocr_subcrop_manifest.json", crop_manifest),
            ("material_ocr_subcrop_diagnostics_json", "material_ocr_subcrop_diagnostics.json", {"warnings": warnings, "errors": errors}),
        ],
    )
    return {
        "ok": status not in {"failed"},
        "phase": "BIZ-2x-material-ocr-subcrop",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "summary": summary,
        "crop_manifest": crop_manifest,
        "warnings": warnings,
        "errors": errors,
        "outputs": outputs,
    }


def build_material_evidence_report(
    *,
    material_region_report: Mapping[str, Any],
    highres_report: Mapping[str, Any],
    ocr_report: Mapping[str, Any],
    output_dir: str | Path,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    regions = {_clean_text(row.get("region_id")): dict(row) for row in material_region_report.get("regions") or []}
    highres_by_region = {_clean_text(row.get("region_id")): dict(row) for row in highres_report.get("crop_manifest") or []}
    rows_by_region: dict[str, list[dict[str, Any]]] = {}
    for row in ocr_report.get("ocr_rows") or []:
        if not isinstance(row, Mapping):
            continue
        region_id = _clean_text(row.get("region_id"))
        if not region_id:
            continue
        rows_by_region.setdefault(region_id, []).append(dict(row))

    evidence_rows: list[dict[str, Any]] = []
    material_mentions: list[dict[str, Any]] = []
    for region_id, ocr_rows in sorted(rows_by_region.items()):
        texts = _unique_texts(row.get("text") for row in ocr_rows)
        codes = _unique_texts(code for text in texts for code in _extract_material_codes(text))
        region = regions.get(region_id, {})
        highres = highres_by_region.get(region_id, {})
        confidence = max([_float(row.get("confidence"), 0.0) for row in ocr_rows] or [0.0])
        evidence = {
            "evidence_id": f"MEV-{len(evidence_rows) + 1:04d}",
            "region_id": region_id,
            "region_type": _clean_text(region.get("region_type")) or _clean_text(ocr_rows[0].get("region_type")),
            "planner_source": _clean_text(region.get("planner_source")),
            "source_file": _clean_text(ocr_rows[0].get("source_file")),
            "page": _int(ocr_rows[0].get("page")),
            "material_codes": codes,
            "source_texts": texts,
            "confidence": round(confidence, 4),
            "highres_image_path": _clean_text(highres.get("image_path")),
            "ocr_crop_ids": _unique_texts(row.get("crop_id") for row in ocr_rows),
            "status": "has_material_code" if codes else "text_only_needs_review",
        }
        evidence_rows.append(evidence)
        for code in codes:
            material_mentions.append(
                {
                    "code": _normalize_material_code(code),
                    "source_region_id": region_id,
                    "source_region_type": evidence["region_type"],
                    "source_texts": texts,
                    "confidence": evidence["confidence"],
                    "highres_image_path": evidence["highres_image_path"],
                }
            )

    summary = {
        "material_evidence_status": "completed",
        "material_evidence_region_count": len(evidence_rows),
        "material_mention_count": len(material_mentions),
        "material_code_count": len({_normalize_material_code(row.get("code")) for row in material_mentions if row.get("code")}),
        "ocr_text_line_count": len(list(ocr_report.get("ocr_rows") or [])),
    }
    outputs = _write_simple_outputs(
        directory=directory,
        payloads=[
            ("material_evidence_summary_json", "material_evidence_summary.json", summary),
            ("material_evidence_json", "material_evidence.json", evidence_rows),
            ("material_mentions_json", "material_mentions.json", material_mentions),
        ],
    )
    return {
        "ok": True,
        "phase": "BIZ-2x-material-evidence",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "completed",
        "summary": summary,
        "material_evidence": evidence_rows,
        "material_mentions": material_mentions,
        "outputs": outputs,
    }


def _detect_material_color_candidates(
    image: Image.Image,
    *,
    grid_cols: int = 48,
    grid_rows: int = 48,
    include_fallback: bool = False,
) -> list[dict[str, Any]]:
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width <= 0 or height <= 0:
        return []
    masks = _material_color_masks(rgb)
    material_mask = masks["red"] | masks["yellow"] | masks["green"]
    components = _mask_grid_components(material_mask, grid_cols=grid_cols, grid_rows=grid_rows)
    candidates: list[dict[str, Any]] = []
    for component in components:
        bbox_pixel = _component_mask_bbox(material_mask, component, width=width, height=height, grid_cols=grid_cols, grid_rows=grid_rows)
        if not bbox_pixel:
            continue
        bbox_pixel = _expand_pixel_bbox(bbox_pixel, width=width, height=height, padding=max(8, int(min(width, height) * 0.01)))
        bbox_ratio = _pixel_bbox_to_ratio(bbox_pixel, width=width, height=height)
        area = _bbox_area_ratio(bbox_ratio)
        if area < 0.00008 or area > 0.32:
            continue
        stats = _candidate_color_stats(masks, bbox_pixel)
        candidates.append(_candidate_from_stats(bbox_ratio, stats))
    if include_fallback and not candidates and material_mask.sum() > max(10, int(width * height * 0.00002)):
        ys, xs = np.where(material_mask)
        bbox = _expand_pixel_bbox([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], width=width, height=height, padding=12)
        candidates.append(_candidate_from_stats(_pixel_bbox_to_ratio(bbox, width=width, height=height), _candidate_color_stats(masks, bbox)))
    return sorted(
        candidates,
        key=lambda row: (-_float(row.get("priority"), 0), -_float(row.get("confidence"), 0), _bbox_top(row.get("bbox_ratio")), _bbox_left(row.get("bbox_ratio"))),
    )


def _material_color_masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    red = rgb[:, :, 0].astype(np.int16)
    green = rgb[:, :, 1].astype(np.int16)
    blue = rgb[:, :, 2].astype(np.int16)
    red_mask = (red > 145) & (green < 135) & (blue < 135) & (red > green + 35)
    yellow_mask = (red > 165) & (green > 150) & (blue < 140)
    green_mask = (green > 145) & (red < 155) & (blue < 170) & (green > red + 30)
    dark_mask = (red < 80) & (green < 80) & (blue < 80)
    return {"red": red_mask, "yellow": yellow_mask, "green": green_mask, "dark": dark_mask}


def _mask_grid_components(mask: np.ndarray, *, grid_cols: int, grid_rows: int) -> list[list[tuple[int, int]]]:
    height, width = mask.shape[:2]
    occupied = np.zeros((grid_rows, grid_cols), dtype=bool)
    for row in range(grid_rows):
        y1 = int(height * row / grid_rows)
        y2 = int(height * (row + 1) / grid_rows)
        for col in range(grid_cols):
            x1 = int(width * col / grid_cols)
            x2 = int(width * (col + 1) / grid_cols)
            cell = mask[y1:y2, x1:x2]
            if cell.size <= 0:
                continue
            if int(cell.sum()) >= max(2, int(cell.size * 0.0008)):
                occupied[row, col] = True
    components: list[list[tuple[int, int]]] = []
    seen: set[tuple[int, int]] = set()
    for row in range(grid_rows):
        for col in range(grid_cols):
            if not occupied[row, col] or (row, col) in seen:
                continue
            group: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque([(row, col)])
            seen.add((row, col))
            while queue:
                current = queue.popleft()
                group.append(current)
                for nr in range(max(0, current[0] - 1), min(grid_rows, current[0] + 2)):
                    for nc in range(max(0, current[1] - 1), min(grid_cols, current[1] + 2)):
                        if (nr, nc) in seen or not occupied[nr, nc]:
                            continue
                        seen.add((nr, nc))
                        queue.append((nr, nc))
            components.append(group)
    return components


def _component_mask_bbox(
    mask: np.ndarray,
    component: Sequence[tuple[int, int]],
    *,
    width: int,
    height: int,
    grid_cols: int,
    grid_rows: int,
) -> list[int]:
    if not component:
        return []
    rows = [item[0] for item in component]
    cols = [item[1] for item in component]
    x1 = int(width * min(cols) / grid_cols)
    x2 = int(width * (max(cols) + 1) / grid_cols)
    y1 = int(height * min(rows) / grid_rows)
    y2 = int(height * (max(rows) + 1) / grid_rows)
    local = mask[y1:y2, x1:x2]
    ys, xs = np.where(local)
    if xs.size == 0 or ys.size == 0:
        return []
    return [x1 + int(xs.min()), y1 + int(ys.min()), x1 + int(xs.max()) + 1, y1 + int(ys.max()) + 1]


def _candidate_color_stats(masks: Mapping[str, np.ndarray], bbox_pixel: Sequence[int]) -> dict[str, Any]:
    x1, y1, x2, y2 = [int(item) for item in bbox_pixel]
    area = max(1, (x2 - x1) * (y2 - y1))
    stats = {
        "red_ratio": float(masks["red"][y1:y2, x1:x2].sum()) / area,
        "yellow_ratio": float(masks["yellow"][y1:y2, x1:x2].sum()) / area,
        "green_ratio": float(masks["green"][y1:y2, x1:x2].sum()) / area,
        "dark_ratio": float(masks["dark"][y1:y2, x1:x2].sum()) / area,
        "aspect": (x2 - x1) / max(1, (y2 - y1)),
        "area_px": area,
    }
    return stats


def _candidate_from_stats(bbox_ratio: Sequence[float], stats: Mapping[str, Any]) -> dict[str, Any]:
    red_ratio = _float(stats.get("red_ratio"), 0.0)
    yellow_ratio = _float(stats.get("yellow_ratio"), 0.0)
    green_ratio = _float(stats.get("green_ratio"), 0.0)
    dark_ratio = _float(stats.get("dark_ratio"), 0.0)
    aspect = _float(stats.get("aspect"), 1.0)
    area_ratio = _bbox_area_ratio(bbox_ratio)
    visible_clues: list[str] = []
    if red_ratio > 0.0005:
        visible_clues.append("red material-like text or leader line")
    if yellow_ratio > 0.0005:
        visible_clues.append("yellow material-like text")
    if green_ratio > 0.0005:
        visible_clues.append("green note/title text")
    if dark_ratio > 0.01 and aspect > 1.5:
        visible_clues.append("table or note linework")

    strong_red_signal = red_ratio > 0.0005
    yellow_only_signal = yellow_ratio > 0.0005 and not strong_red_signal
    green_title_like = green_ratio > yellow_ratio and not strong_red_signal

    if (area_ratio > 0.08 or aspect > 2.2) and strong_red_signal and (dark_ratio > 0.002 or red_ratio > 0.002):
        region_type = "legend_table"
        expected = ["material_code", "material_name", "specification", "legend_symbol"]
        reason = "Wide colored-text/table cluster likely contains a material or legend table."
        priority = 0.90
        confidence = 0.70
        padding = 0.045
    elif strong_red_signal and aspect > 0.8:
        region_type = "material_callout"
        expected = ["material_code", "material_name", "specification", "leader_target"]
        reason = "Red/yellow annotation cluster likely contains an inline material callout."
        priority = 0.88
        confidence = 0.68
        padding = 0.055
    elif yellow_only_signal and area_ratio < 0.025 and not green_title_like:
        region_type = "finish_code_label"
        expected = ["material_code", "finish_code", "nearby_description"]
        reason = "Yellow finish-code cluster likely marks material codes or finish labels."
        priority = 0.64
        confidence = 0.48
        padding = 0.050
    elif green_ratio > 0.001:
        region_type = "material_note"
        expected = ["material_code", "material_note", "drawing_note"]
        reason = "Green note cluster may contain a material legend, note, or title-strip text."
        priority = 0.50 if green_title_like else 0.58
        confidence = 0.36 if green_title_like else 0.42
        padding = 0.040
    else:
        region_type = "material_note"
        expected = ["material_note"]
        reason = "Colored text cluster may contain material information."
        priority = 0.52
        confidence = 0.34
        padding = 0.040
    return {
        "bbox_ratio": [round(float(item), 6) for item in bbox_ratio],
        "region_type": region_type,
        "priority": round(min(0.96, priority + min(0.04, area_ratio)), 4),
        "confidence": confidence,
        "expected_information": expected,
        "reason": reason,
        "visible_clues": visible_clues,
        "padding_ratio": padding,
    }


def _region_from_local_bbox(
    *,
    view: Mapping[str, Any],
    local_bbox_ratio: Sequence[float],
    region_id_suffix: str,
    region_type: str,
    priority: float,
    confidence: float,
    expected_information: Sequence[str],
    reason: str,
    planner_source: str,
    grid_ref: str = "",
    recommended_tools: Sequence[str] | None = None,
    crop_strategy: Mapping[str, Any] | None = None,
    visible_clues: Sequence[str] | None = None,
    risk_flags: Sequence[str] | None = None,
    risk_note: str = "",
) -> dict[str, Any]:
    bbox = _local_bbox_to_page_ratio(local_bbox_ratio, view=view)
    view_id = _clean_text(view.get("view_id"))
    return {
        "region_id": _safe_identifier(f"{view_id}_{region_id_suffix}"),
        "view_id": view_id,
        "source_file": _clean_text(view.get("source_file")),
        "page": _int(view.get("page")),
        "region_type": _normalize_region_type(region_type),
        "planner_source": planner_source,
        "grid_ref": grid_ref,
        "bbox_ratio": bbox,
        "local_bbox_ratio": [round(float(item), 6) for item in local_bbox_ratio],
        "parent_view_bbox_ratio": list(view.get("parent_bbox_ratio") or []),
        "parent_view_bbox_pixel": list(view.get("parent_bbox_pixel") or view.get("bbox_pixel") or []),
        "priority": round(max(0.0, min(1.0, float(priority))), 4),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "recommended_tools": list(recommended_tools or ["ocr", "vlm_read"]),
        "expected_information": list(expected_information),
        "crop_strategy": dict(crop_strategy or {"highres_scale": 64.0, "padding_ratio": 0.05}),
        "visible_clues": list(visible_clues or []),
        "reason": reason,
        "risk_flags": list(risk_flags or ["thumbnail_text_unreadable", "needs_highres_render"]),
        "risk_note": risk_note or "Needs source-PDF high-resolution clipped rendering and OCR confirmation.",
    }


def _local_bbox_to_page_ratio(local_bbox: Sequence[float], *, view: Mapping[str, Any]) -> list[float]:
    parent = _normalize_pixel_bbox(view.get("parent_bbox_pixel") or view.get("bbox_pixel"))
    page_width = _int(view.get("page_image_width_px") or view.get("image_width_px"))
    page_height = _int(view.get("page_image_height_px") or view.get("image_height_px"))
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


def _dedupe_regions(
    regions: Sequence[Mapping[str, Any]],
    *,
    iou_threshold: float,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for region in sorted(regions, key=lambda row: (-_float(row.get("priority"), 0.0), -_float(row.get("confidence"), 0.0))):
        if not region.get("bbox_ratio"):
            continue
        duplicate = False
        for kept in selected:
            if _clean_text(kept.get("source_file")) != _clean_text(region.get("source_file")):
                continue
            if _int(kept.get("page")) != _int(region.get("page")):
                continue
            if _bbox_iou(kept.get("bbox_ratio"), region.get("bbox_ratio")) >= iou_threshold:
                duplicate = True
                warnings.append(
                    {
                        "code": "MATERIAL_REGION_DEDUPED",
                        "message": "Overlapping material candidate region removed.",
                        "removed_region_id": region.get("region_id"),
                        "kept_region_id": kept.get("region_id"),
                    }
                )
                break
        if not duplicate:
            selected.append(dict(region))
    return selected


def _write_page_annotations(
    *,
    render_report: Mapping[str, Any],
    regions: Sequence[Mapping[str, Any]],
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    render_by_page = {
        (_clean_text(row.get("source_file")), _int(row.get("page"))): dict(row)
        for row in render_report.get("render_rows") or []
    }
    regions_by_page: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for region in regions:
        regions_by_page.setdefault((_clean_text(region.get("source_file")), _int(region.get("page"))), []).append(region)

    annotation_paths: list[str] = []
    for key, page_regions in regions_by_page.items():
        render = render_by_page.get(key)
        if not render:
            continue
        image_path = Path(_clean_text(render.get("png_path")))
        if not image_path.exists():
            continue
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)
        for index, region in enumerate(page_regions, start=1):
            bbox = _ratio_to_pixel_bbox(region.get("bbox_ratio") or [], width=image.width, height=image.height)
            if not bbox:
                continue
            color = _annotation_color(region.get("region_type"))
            draw.rectangle(tuple(bbox), outline=color, width=4)
            label = f"{index}:{_clean_text(region.get('region_type'))[:18]}"
            draw.rectangle((bbox[0], max(0, bbox[1] - 20), bbox[0] + 210, bbox[1]), fill=(255, 255, 255), outline=color)
            draw.text((bbox[0] + 4, max(0, bbox[1] - 18)), label, fill=color)
        output_path = output_dir / f"{_safe_stem(key[0])}_p{key[1]:03d}_material_regions.png"
        image.save(output_path)
        annotation_paths.append(str(output_path.resolve()))
    manifest_path = output_dir / "material_region_annotations.json"
    manifest_path.write_text(json.dumps(annotation_paths, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "material_region_annotations_json": str(manifest_path.resolve()),
        "material_region_annotation_count": str(len(annotation_paths)),
    }


def _write_plan_outputs(
    *,
    planner_dir: Path,
    view_manifest: Sequence[Mapping[str, Any]],
    raw_plan: Mapping[str, Any] | str,
    regions: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    return _write_simple_outputs(
        directory=planner_dir,
        payloads=[
            ("material_region_plan_json", "material_region_plan.json", {"schema_version": SCHEMA_VERSION, "regions": list(regions)}),
            ("material_region_views_json", "material_region_views.json", list(view_manifest)),
            ("material_region_raw_json", "material_region_raw.json", raw_plan),
            ("material_region_summary_json", "material_region_summary.json", dict(summary)),
            ("material_region_diagnostics_json", "material_region_diagnostics.json", {"warnings": list(warnings), "errors": list(errors)}),
        ],
    )


def _write_simple_outputs(*, directory: Path, payloads: Sequence[tuple[str, str, Any]]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for key, filename, payload in payloads:
        path = directory / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    return outputs


def _coerce_json_object(payload: Mapping[str, Any] | str, *, warnings: list[dict[str, Any]]) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    text = _clean_text(payload)
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.DOTALL)
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        text = match.group(0) if match else "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        warnings.append({"code": "MATERIAL_REGION_JSON_PARSE_FAILED", "message": str(exc)})
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _normalize_bbox_ratio(value: Any, *, warnings: list[dict[str, Any]], index: int) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        warnings.append({"code": "MATERIAL_REGION_BBOX_INVALID", "message": "bbox_ratio must contain four numbers.", "region_index": index})
        return None
    try:
        x1, y1, x2, y2 = [float(item) for item in value]
    except (TypeError, ValueError):
        warnings.append({"code": "MATERIAL_REGION_BBOX_INVALID", "message": "bbox_ratio contains non-numeric values.", "region_index": index})
        return None
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 <= x1 or y2 <= y1:
        warnings.append({"code": "MATERIAL_REGION_BBOX_EMPTY", "message": "bbox_ratio has no area.", "region_index": index})
        return None
    return [round(x1, 6), round(y1, 6), round(x2, 6), round(y2, 6)]


def _normalize_region_type(value: Any) -> str:
    region_type = _clean_text(value).lower()
    return region_type if region_type in REGION_TYPES else "unknown"


def _normalize_tools(value: Any) -> list[str]:
    raw_items = value if isinstance(value, (list, tuple)) else [value] if value else []
    result: list[str] = []
    for raw in raw_items:
        tool = _clean_text(raw).lower()
        for item in COMPOSITE_TOOL_MAP.get(tool, [tool]):
            if item in ATOMIC_TOOLS and item not in result:
                result.append(item)
    return result or ["ocr", "vlm_read"]


def _normalize_crop_strategy(value: Any) -> dict[str, float]:
    strategy = value if isinstance(value, Mapping) else {}
    return {
        "highres_scale": max(8.0, min(96.0, _float(strategy.get("highres_scale") or strategy.get("scale"), 64.0))),
        "padding_ratio": max(0.0, min(0.10, _float(strategy.get("padding_ratio"), 0.05))),
    }


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
    if width <= 0 or height <= 0 or len(bbox) != 4:
        return []
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        round(max(0.0, min(1.0, x1 / width)), 6),
        round(max(0.0, min(1.0, y1 / height)), 6),
        round(max(0.0, min(1.0, x2 / width)), 6),
        round(max(0.0, min(1.0, y2 / height)), 6),
    ]


def _ratio_to_pixel_bbox(bbox: Sequence[Any], *, width: int, height: int) -> list[int]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return []
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        max(0, min(width, int(round(x1 * width)))),
        max(0, min(height, int(round(y1 * height)))),
        max(0, min(width, int(round(x2 * width)))),
        max(0, min(height, int(round(y2 * height)))),
    ]


def _expand_pixel_bbox(bbox: Sequence[int], *, width: int, height: int, padding: int) -> list[int]:
    x1, y1, x2, y2 = [int(item) for item in bbox]
    return [max(0, x1 - padding), max(0, y1 - padding), min(width, x2 + padding), min(height, y2 + padding)]


def _pad_bbox_ratio(bbox: Sequence[float], padding: float) -> list[float]:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        round(max(0.0, x1 - padding), 6),
        round(max(0.0, y1 - padding), 6),
        round(min(1.0, x2 + padding), 6),
        round(min(1.0, y2 + padding), 6),
    ]


def _cap_image_long_side(image: Image.Image, *, max_long_side_px: int) -> tuple[Image.Image, float]:
    long_side = max(image.size)
    if long_side <= max_long_side_px or long_side <= 0:
        return image, 1.0
    scale = max_long_side_px / long_side
    resized = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
    return resized, round(scale, 4)


def _pixel_area(bbox: Sequence[int]) -> int:
    if len(bbox) != 4:
        return 0
    return max(0, int(bbox[2]) - int(bbox[0])) * max(0, int(bbox[3]) - int(bbox[1]))


def _bbox_iou(left: Any, right: Any) -> float:
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)) or len(left) != 4 or len(right) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = [float(item) for item in left]
    bx1, by1, bx2, by2 = [float(item) for item in right]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = _bbox_area_ratio(left) + _bbox_area_ratio(right) - inter
    return inter / union if union > 0 else 0.0


def _bbox_area_ratio(bbox: Sequence[Any]) -> float:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return 0.0
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_left(value: Any) -> float:
    return float(value[0]) if isinstance(value, (list, tuple)) and value else 0.0


def _bbox_top(value: Any) -> float:
    return float(value[1]) if isinstance(value, (list, tuple)) and len(value) > 1 else 0.0


def _extract_material_codes(text: Any) -> list[str]:
    return [_normalize_material_code(match.group(1)) for match in MATERIAL_CODE_RE.finditer(_clean_text(text))]


def _normalize_material_code(value: Any) -> str:
    text = re.sub(r"\s+", "", _clean_text(value)).upper()
    match = re.match(r"^([A-Z]+)[-_]?([A-Z0-9]+)$", text)
    if not match:
        return text
    return f"{match.group(1)}-{match.group(2)}"


def _unique_texts(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = re.sub(r"\s+", "", text).lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _annotation_color(region_type: Any) -> tuple[int, int, int]:
    value = _normalize_region_type(region_type)
    if value in {"material_table", "legend_table", "finish_schedule"}:
        return (220, 0, 0)
    if value in {"material_callout", "finish_code_label"}:
        return (245, 145, 0)
    if value == "material_note":
        return (0, 150, 0)
    return (30, 80, 220)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _safe_identifier(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", _clean_text(value)).strip("_")[:96]


def _safe_stem(value: str) -> str:
    text = Path(value or "drawing").stem
    text = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("._")
    return text[:48] or "drawing"


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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
