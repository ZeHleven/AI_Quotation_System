from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


PHASE = "BIZ-2x-highres-pdf-region-render"
RENDER_METHOD = "pdfium_clip_crop"


def build_highres_region_render_report(
    *,
    parse_report: Mapping[str, Any],
    layout_plan_report: Mapping[str, Any],
    output_dir: str | Path,
    max_regions: int = 24,
    default_scale: float = 64.0,
    max_scale: float = 96.0,
    max_pixels: int = 32_000_000,
    min_width_px: int = 1200,
    min_height_px: int = 300,
    default_padding_ratio: float = 0.005,
    min_area_ratio: float = 0.00002,
    max_area_ratio: float = 0.25,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    crop_manifest: list[dict[str, Any]] = []
    requested_regions = list(layout_plan_report.get("regions") or [])

    if not requested_regions:
        return _report(
            directory=directory,
            status="skipped",
            requested_regions=[],
            valid_regions=[],
            crop_manifest=crop_manifest,
            warnings=warnings,
            errors=errors,
            default_scale=default_scale,
            max_scale=max_scale,
            max_pixels=max_pixels,
        )

    try:
        import pypdfium2 as pdfium  # type: ignore
    except Exception as exc:  # noqa: BLE001
        errors.append(
            {
                "code": "HIGHRES_RENDER_PDFIUM_UNAVAILABLE",
                "message": f"pypdfium2 unavailable: {exc}",
            }
        )
        return _report(
            directory=directory,
            status="failed",
            requested_regions=requested_regions,
            valid_regions=[],
            crop_manifest=crop_manifest,
            warnings=warnings,
            errors=errors,
            default_scale=default_scale,
            max_scale=max_scale,
            max_pixels=max_pixels,
        )

    pdf_by_source = _pdf_paths_by_source(parse_report)
    page_by_source = _page_rows_by_source(parse_report)
    valid_regions = _valid_regions(
        requested_regions,
        pdf_by_source=pdf_by_source,
        page_by_source=page_by_source,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        warnings=warnings,
    )
    selected_regions = sorted(
        valid_regions,
        key=lambda row: (
            -_float(row.get("priority"), 0.0),
            -_float(row.get("confidence"), 0.0),
            _int(row.get("page")),
            _clean_text(row.get("region_id")),
        ),
    )[: max(0, int(max_regions or 0))]

    documents: dict[Path, Any] = {}
    try:
        for index, region in enumerate(selected_regions, start=1):
            source_file = _clean_text(region.get("source_file"))
            page_no = _int(region.get("page"))
            pdf_path = pdf_by_source.get(source_file)
            page_row = page_by_source.get((source_file, page_no), {})
            if pdf_path is None:
                continue
            try:
                document = documents.get(pdf_path)
                if document is None:
                    document = pdfium.PdfDocument(str(pdf_path))
                    documents[pdf_path] = document
                page_index = page_no - 1
                if page_index < 0 or page_index >= len(document):
                    warnings.append(
                        {
                            "code": "HIGHRES_RENDER_PAGE_OUT_OF_RANGE",
                            "message": "Region references a page outside the PDF page count.",
                            "region_id": region.get("region_id"),
                            "page": page_no,
                        }
                    )
                    continue
                page = document[page_index]
                page_width_pt, page_height_pt = _page_size_pt(page, page_row)
                if page_width_pt <= 0 or page_height_pt <= 0:
                    warnings.append(
                        {
                            "code": "HIGHRES_RENDER_PAGE_SIZE_INVALID",
                            "message": "PDF page size is missing or invalid.",
                            "region_id": region.get("region_id"),
                        }
                    )
                    page.close()
                    continue
                crop_row = _render_region_crop(
                    page=page,
                    region=region,
                    pdf_path=pdf_path,
                    page_width_pt=page_width_pt,
                    page_height_pt=page_height_pt,
                    directory=directory,
                    index=index,
                    default_scale=default_scale,
                    max_scale=max_scale,
                    max_pixels=max_pixels,
                    min_width_px=min_width_px,
                    min_height_px=min_height_px,
                    default_padding_ratio=default_padding_ratio,
                    warnings=warnings,
                )
                if crop_row:
                    crop_manifest.append(crop_row)
                page.close()
            except Exception as exc:  # noqa: BLE001 - one bad region should not stop the batch
                errors.append(
                    {
                        "code": "HIGHRES_RENDER_REGION_FAILED",
                        "message": str(exc),
                        "region_id": region.get("region_id"),
                    }
                )
    finally:
        for document in documents.values():
            try:
                document.close()
            except Exception:
                pass

    if errors and crop_manifest:
        status = "completed_with_errors"
    elif errors:
        status = "failed"
    elif not crop_manifest and selected_regions:
        status = "completed_without_crops"
        warnings.append(
            {
                "code": "HIGHRES_RENDER_EMPTY",
                "message": "No high-resolution region crops were created.",
            }
        )
    elif not selected_regions:
        status = "skipped"
    else:
        status = "completed_with_warnings" if warnings else "completed"

    return _report(
        directory=directory,
        status=status,
        requested_regions=requested_regions,
        valid_regions=valid_regions,
        crop_manifest=crop_manifest,
        warnings=warnings,
        errors=errors,
        default_scale=default_scale,
        max_scale=max_scale,
        max_pixels=max_pixels,
    )


def _render_region_crop(
    *,
    page: Any,
    region: Mapping[str, Any],
    pdf_path: Path,
    page_width_pt: float,
    page_height_pt: float,
    directory: Path,
    index: int,
    default_scale: float,
    max_scale: float,
    max_pixels: int,
    min_width_px: int,
    min_height_px: int,
    default_padding_ratio: float,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    bbox_ratio = [float(item) for item in region.get("bbox_ratio") or []]
    padding = _region_padding(region, default_padding_ratio)
    padded_ratio = _pad_bbox_ratio(bbox_ratio, padding)
    crop_width_pt = max(0.0, (padded_ratio[2] - padded_ratio[0]) * page_width_pt)
    crop_height_pt = max(0.0, (padded_ratio[3] - padded_ratio[1]) * page_height_pt)
    if crop_width_pt <= 0 or crop_height_pt <= 0:
        warnings.append(
            {
                "code": "HIGHRES_RENDER_REGION_SIZE_INVALID",
                "message": "Region has no drawable size after padding.",
                "region_id": region.get("region_id"),
            }
        )
        return None

    requested_scale = _requested_scale(
        region,
        default_scale=default_scale,
        max_scale=max_scale,
        min_width_px=min_width_px,
        min_height_px=min_height_px,
        crop_width_pt=crop_width_pt,
        crop_height_pt=crop_height_pt,
    )
    applied_scale, scale_flags = _scale_for_pixel_cap(
        requested_scale,
        crop_width_pt=crop_width_pt,
        crop_height_pt=crop_height_pt,
        max_pixels=max_pixels,
    )
    crop_amounts = _ratio_to_pdfium_crop(
        padded_ratio,
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
    )
    bitmap = page.render(scale=applied_scale, crop=tuple(crop_amounts))
    image = bitmap.to_pil().convert("RGB")

    crop_id = f"highres_p{_int(region.get('page')):03d}_{index:03d}_{_safe_identifier(region.get('region_id')) or 'r'}"
    type_slug = (
        _safe_identifier(region.get("region_subtype"))
        or _safe_identifier(region.get("region_type"))
        or "unknown"
    )[:24]
    filename = (
        f"{_safe_stem(_clean_text(region.get('source_file')))[:32]}_"
        f"hr_p{_int(region.get('page')):03d}_{index:03d}_"
        f"{type_slug}.png"
    )
    output_path = directory / filename
    image.save(output_path)
    quality = _quality_status(
        image_width=image.width,
        image_height=image.height,
        min_width_px=min_width_px,
        min_height_px=min_height_px,
        scale_flags=scale_flags,
    )
    return {
        "crop_id": crop_id,
        "crop_type": "highres_pdf_region",
        "region_id": _clean_text(region.get("region_id")),
        "region_type": _clean_text(region.get("region_type")) or "unknown",
        "region_subtype": _clean_text(region.get("region_subtype")),
        "source_file": _clean_text(region.get("source_file")),
        "page": _int(region.get("page")),
        "source_pdf_path": str(pdf_path.resolve()),
        "source_image_path": "",
        "image_path": str(output_path.resolve()),
        "bbox_ratio": bbox_ratio,
        "padded_bbox_ratio": padded_ratio,
        "pdf_crop_amounts": [round(float(item), 6) for item in crop_amounts],
        "page_width_pt": round(page_width_pt, 6),
        "page_height_pt": round(page_height_pt, 6),
        "image_width_px": image.width,
        "image_height_px": image.height,
        "requested_scale": round(requested_scale, 4),
        "render_scale": round(applied_scale, 4),
        "scale_factor": round(applied_scale, 4),
        "render_method": RENDER_METHOD,
        "is_upscaled_from_lowres": False,
        "source_quality": "rerendered_from_source_pdf",
        "padding_ratio": padding,
        "priority": _float(region.get("priority"), 0.0),
        "confidence": _float(region.get("confidence"), 0.0),
        "recommended_tools": list(region.get("recommended_tools") or []),
        "expected_information": list(region.get("expected_information") or []),
        "reason": _clean_text(region.get("reason")),
        "risk_flags": list(region.get("risk_flags") or []),
        "risk_note": _clean_text(region.get("risk_note")),
        "source_region_features": dict(region.get("features") or {}) if isinstance(region.get("features"), Mapping) else {},
        "source_region_quality_flags": list(region.get("quality_flags") or []),
        "source_region_planner_source": _clean_text(region.get("planner_source")),
        "source_region_selected": region.get("selected") if isinstance(region.get("selected"), bool) else None,
        "source_region_bbox_pixel": list(region.get("bbox_pixel") or []),
        "quality_status": quality["status"],
        "quality_flags": quality["flags"],
        "status": "created",
    }


def _valid_regions(
    regions: Sequence[Any],
    *,
    pdf_by_source: Mapping[str, Path],
    page_by_source: Mapping[tuple[str, int], Mapping[str, Any]],
    min_area_ratio: float,
    max_area_ratio: float,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for raw in regions:
        if not isinstance(raw, Mapping):
            continue
        region = dict(raw)
        bbox = region.get("bbox_ratio")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            warnings.append({"code": "HIGHRES_REGION_BBOX_INVALID", "message": "bbox_ratio must contain 4 numbers.", "region_id": region.get("region_id")})
            continue
        try:
            x1, y1, x2, y2 = [float(item) for item in bbox]
        except (TypeError, ValueError):
            warnings.append({"code": "HIGHRES_REGION_BBOX_INVALID", "message": "bbox_ratio contains non-numeric values.", "region_id": region.get("region_id")})
            continue
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            warnings.append({"code": "HIGHRES_REGION_BBOX_OUT_OF_RANGE", "message": "bbox_ratio is out of range.", "region_id": region.get("region_id")})
            continue
        area = (x2 - x1) * (y2 - y1)
        if area < min_area_ratio:
            warnings.append({"code": "HIGHRES_REGION_AREA_TOO_SMALL", "message": "Region area is too small for reliable rendering.", "region_id": region.get("region_id")})
            continue
        if area > max_area_ratio:
            warnings.append({"code": "HIGHRES_REGION_AREA_TOO_LARGE", "message": "Region is too large for high-scale clipped rendering.", "region_id": region.get("region_id")})
            continue
        source_file = _clean_text(region.get("source_file"))
        page_no = _int(region.get("page"))
        if source_file not in pdf_by_source:
            warnings.append({"code": "HIGHRES_REGION_SOURCE_PDF_MISSING", "message": "Region source PDF is not available.", "region_id": region.get("region_id")})
            continue
        if (source_file, page_no) not in page_by_source:
            warnings.append({"code": "HIGHRES_REGION_PAGE_MISSING", "message": "Region source page metadata is not available.", "region_id": region.get("region_id")})
            continue
        valid.append(region)
    return valid


def _requested_scale(
    region: Mapping[str, Any],
    *,
    default_scale: float,
    max_scale: float,
    min_width_px: int,
    min_height_px: int,
    crop_width_pt: float,
    crop_height_pt: float,
) -> float:
    crop_strategy = region.get("crop_strategy")
    strategy_scale = 0.0
    if isinstance(crop_strategy, Mapping):
        strategy_scale = _float(crop_strategy.get("highres_scale"), 0.0)
    region_scale = _float(region.get("highres_scale"), 0.0)
    requested = max(default_scale, strategy_scale, region_scale)
    if crop_width_pt > 0 and min_width_px > 0:
        requested = max(requested, (min_width_px + 1) / crop_width_pt)
    if crop_height_pt > 0 and min_height_px > 0:
        requested = max(requested, (min_height_px + 1) / crop_height_pt)
    return max(1.0, min(float(max_scale or requested), requested))


def _scale_for_pixel_cap(
    requested_scale: float,
    *,
    crop_width_pt: float,
    crop_height_pt: float,
    max_pixels: int,
) -> tuple[float, list[str]]:
    flags: list[str] = []
    if max_pixels <= 0:
        return requested_scale, flags
    estimated_pixels = crop_width_pt * crop_height_pt * requested_scale * requested_scale
    if estimated_pixels <= max_pixels:
        return requested_scale, flags
    capped_scale = math.sqrt(max_pixels / max(1.0, crop_width_pt * crop_height_pt))
    applied = max(1.0, min(requested_scale, capped_scale))
    if applied < requested_scale:
        flags.append("scale_reduced_to_pixel_cap")
    return applied, flags


def _quality_status(
    *,
    image_width: int,
    image_height: int,
    min_width_px: int,
    min_height_px: int,
    scale_flags: Sequence[str],
) -> dict[str, Any]:
    flags = list(scale_flags)
    if image_width < min_width_px:
        flags.append("width_below_highres_gate")
    if image_height < min_height_px:
        flags.append("height_below_highres_gate")
    status = "passed" if not any(flag.endswith("_below_highres_gate") for flag in flags) else "needs_rerender_or_manual_review"
    if "scale_reduced_to_pixel_cap" in flags and status == "passed":
        status = "passed_with_scale_cap"
    return {"status": status, "flags": flags}


def _ratio_to_pdfium_crop(
    bbox: Sequence[float],
    *,
    page_width_pt: float,
    page_height_pt: float,
) -> list[float]:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        max(0.0, x1 * page_width_pt),
        max(0.0, page_height_pt - y2 * page_height_pt),
        max(0.0, page_width_pt - x2 * page_width_pt),
        max(0.0, y1 * page_height_pt),
    ]


def _pad_bbox_ratio(bbox: Sequence[float], padding: float) -> list[float]:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        round(max(0.0, x1 - padding), 6),
        round(max(0.0, y1 - padding), 6),
        round(min(1.0, x2 + padding), 6),
        round(min(1.0, y2 + padding), 6),
    ]


def _region_padding(region: Mapping[str, Any], default_padding_ratio: float) -> float:
    crop_strategy = region.get("crop_strategy")
    if isinstance(crop_strategy, Mapping):
        value = _float(crop_strategy.get("padding_ratio"), default_padding_ratio)
    else:
        value = default_padding_ratio
    return max(0.0, min(0.08, value))


def _page_size_pt(page: Any, page_row: Mapping[str, Any]) -> tuple[float, float]:
    width = _float(page_row.get("width_pt"), 0.0)
    height = _float(page_row.get("height_pt"), 0.0)
    if width > 0 and height > 0:
        return width, height
    try:
        raw_width, raw_height = page.get_size()
        return float(raw_width), float(raw_height)
    except Exception:
        return 0.0, 0.0


def _pdf_paths_by_source(parse_report: Mapping[str, Any]) -> dict[str, Path]:
    rows: dict[str, Path] = {}
    for file_row in parse_report.get("file_rows") or []:
        name = _clean_text(file_row.get("file_name") or file_row.get("source_file"))
        path = Path(_clean_text(file_row.get("path")))
        if name and path.exists() and path.is_file():
            rows[name] = path
    return rows


def _page_rows_by_source(parse_report: Mapping[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for page_row in parse_report.get("page_rows") or []:
        key = (_clean_text(page_row.get("source_file")), _int(page_row.get("page")))
        if key[0] and key[1]:
            rows[key] = dict(page_row)
    return rows


def _report(
    *,
    directory: Path,
    status: str,
    requested_regions: Sequence[Mapping[str, Any]],
    valid_regions: Sequence[Mapping[str, Any]],
    crop_manifest: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    default_scale: float,
    max_scale: float,
    max_pixels: int,
) -> dict[str, Any]:
    summary = {
        "highres_render_status": status,
        "requested_region_count": len(list(requested_regions)),
        "valid_region_count": len(list(valid_regions)),
        "highres_crop_count": len(list(crop_manifest)),
        "quality_passed_count": sum(1 for row in crop_manifest if _clean_text(row.get("quality_status")).startswith("passed")),
        "not_upscaled_from_lowres_count": sum(1 for row in crop_manifest if row.get("is_upscaled_from_lowres") is False),
        "default_scale": default_scale,
        "max_scale": max_scale,
        "max_pixels": max_pixels,
        "highres_warning_count": len(list(warnings)),
        "highres_error_count": len(list(errors)),
    }
    outputs = _write_outputs(
        directory=directory,
        summary=summary,
        crop_manifest=crop_manifest,
        warnings=warnings,
        errors=errors,
    )
    return {
        "ok": status not in {"failed"},
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "summary": summary,
        "crop_manifest": list(crop_manifest),
        "warnings": list(warnings),
        "errors": list(errors),
        "outputs": outputs,
    }


def _write_outputs(
    *,
    directory: Path,
    summary: Mapping[str, Any],
    crop_manifest: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    payloads = [
        ("highres_region_summary_json", directory / "highres_region_summary.json", dict(summary)),
        ("highres_region_manifest_json", directory / "highres_region_manifest.json", list(crop_manifest)),
        ("highres_region_diagnostics_json", directory / "highres_region_diagnostics.json", {"warnings": list(warnings), "errors": list(errors)}),
    ]
    outputs: dict[str, str] = {}
    for key, path, payload in payloads:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    return outputs


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


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


def _safe_stem(value: str) -> str:
    stem = Path(value or "drawing").stem
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", stem).strip("._-")
    return stem or "drawing"


def _safe_identifier(value: Any) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"[^0-9a-zA-Z_-]+", "_", text).strip("_")
    return text[:80]
