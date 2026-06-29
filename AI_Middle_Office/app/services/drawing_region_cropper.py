from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image


PHASE = "BIZ-2x-pdf-region-cropper"
MAX_CROP_LONG_SIDE = 5200


def build_region_crop_report(
    *,
    render_report: Mapping[str, Any],
    layout_plan_report: Mapping[str, Any],
    crop_dir: str | Path,
    max_regions: int = 24,
    min_area_ratio: float = 0.0004,
    max_area_ratio: float = 0.80,
    iou_threshold: float = 0.70,
) -> dict[str, Any]:
    directory = Path(crop_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    render_by_page = _render_rows_by_page(render_report)
    valid_regions = _validate_regions(
        layout_plan_report.get("regions") or [],
        render_by_page=render_by_page,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        warnings=warnings,
    )
    deduped_regions = _dedupe_regions(valid_regions, iou_threshold=iou_threshold, warnings=warnings)
    selected_regions = sorted(
        deduped_regions,
        key=lambda row: (-_float(row.get("priority"), 0.0), -_float(row.get("confidence"), 0.0), _int(row.get("page")), _clean_text(row.get("region_id"))),
    )[: max(0, int(max_regions or 0))]

    crop_manifest: list[dict[str, Any]] = []
    for index, region in enumerate(selected_regions, start=1):
        render = render_by_page.get((_clean_text(region.get("source_file")), _int(region.get("page"))))
        if not render:
            continue
        source_image = Path(str(render.get("png_path") or ""))
        if not source_image.exists() or not source_image.is_file():
            warnings.append(
                {
                    "code": "REGION_CROP_SOURCE_IMAGE_MISSING",
                    "message": "Rendered page image is missing.",
                    "region_id": region.get("region_id"),
                    "source_image": str(source_image),
                }
            )
            continue
        try:
            with Image.open(source_image) as image:
                image = image.convert("RGB")
                width, height = image.size
                bbox_ratio = [float(item) for item in region.get("bbox_ratio") or []]
                padding = _crop_padding(region)
                padded_ratio = _pad_bbox_ratio(bbox_ratio, padding)
                bbox_pixel = _ratio_to_pixel_bbox(bbox_ratio, width=width, height=height)
                padded_pixel = _ratio_to_pixel_bbox(padded_ratio, width=width, height=height)
                crop = image.crop(tuple(padded_pixel))
                requested_scale = _crop_scale(region)
                crop, applied_scale = _resize_crop(crop, requested_scale)
                crop_id = f"region_p{_int(region.get('page')):03d}_{index:03d}_{_safe_identifier(region.get('region_id')) or 'r'}"
                type_slug = (
                    _safe_identifier(region.get("region_subtype"))
                    or _safe_identifier(region.get("region_type"))
                    or "unknown"
                )[:24]
                filename = (
                    f"{_safe_stem(_clean_text(region.get('source_file')))[:32]}_"
                    f"region_p{_int(region.get('page')):03d}_{index:03d}_"
                    f"{type_slug}.png"
                )
                output_path = directory / filename
                crop.save(output_path)
                crop_manifest.append(
                    {
                        "crop_id": crop_id,
                        "crop_type": "layout_region",
                        "region_id": _clean_text(region.get("region_id")),
                        "region_type": _clean_text(region.get("region_type")) or "unknown",
                        "source_file": _clean_text(region.get("source_file")),
                        "page": _int(region.get("page")),
                        "source_image_path": str(source_image.resolve()),
                        "image_path": str(output_path.resolve()),
                        "bbox_ratio": bbox_ratio,
                        "padded_bbox_ratio": padded_ratio,
                        "bbox_pixel": bbox_pixel,
                        "padded_bbox_pixel": padded_pixel,
                        "image_width_px": crop.width,
                        "image_height_px": crop.height,
                        "scale_factor": applied_scale,
                        "requested_scale": requested_scale,
                        "padding_ratio": padding,
                        "priority": _float(region.get("priority"), 0.0),
                        "confidence": _float(region.get("confidence"), 0.0),
                        "recommended_tools": list(region.get("recommended_tools") or []),
                        "expected_information": list(region.get("expected_information") or []),
                        "reason": _clean_text(region.get("reason")),
                        "risk_flags": list(region.get("risk_flags") or []),
                        "risk_note": _clean_text(region.get("risk_note")),
                        "status": "created",
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one bad crop should not stop the run
            errors.append(
                {
                    "code": "REGION_CROP_FAILED",
                    "message": str(exc),
                    "region_id": region.get("region_id"),
                }
            )
    status = "completed"
    if errors and crop_manifest:
        status = "completed_with_errors"
    elif errors:
        status = "failed"
    elif not crop_manifest and layout_plan_report.get("status") not in {"skipped", "failed"}:
        status = "completed_without_crops"
        warnings.append(
            {
                "code": "REGION_CROP_EMPTY",
                "message": "No valid regions were cropped from the layout plan.",
            }
        )
    elif layout_plan_report.get("status") in {"skipped", "failed"}:
        status = "skipped"
    summary = {
        "region_crop_status": status,
        "layout_plan_region_count": len(list(layout_plan_report.get("regions") or [])),
        "valid_region_count": len(valid_regions),
        "deduped_region_count": len(deduped_regions),
        "region_crop_count": len(crop_manifest),
        "region_crop_warning_count": len(warnings),
        "region_crop_error_count": len(errors),
    }
    outputs = _write_region_crop_outputs(
        crop_dir=directory,
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
        "crop_manifest": crop_manifest,
        "warnings": warnings,
        "errors": errors,
        "outputs": outputs,
    }


def _validate_regions(
    regions: Sequence[Any],
    *,
    render_by_page: Mapping[tuple[str, int], Mapping[str, Any]],
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
            warnings.append({"code": "REGION_BBOX_INVALID", "message": "bbox_ratio must contain 4 numbers.", "region_id": region.get("region_id")})
            continue
        try:
            x1, y1, x2, y2 = [float(item) for item in bbox]
        except (TypeError, ValueError):
            warnings.append({"code": "REGION_BBOX_INVALID", "message": "bbox_ratio contains non-numeric values.", "region_id": region.get("region_id")})
            continue
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            warnings.append({"code": "REGION_BBOX_OUT_OF_RANGE", "message": "bbox_ratio is out of range.", "region_id": region.get("region_id")})
            continue
        area = _bbox_area_ratio([x1, y1, x2, y2])
        if area < min_area_ratio:
            warnings.append({"code": "REGION_AREA_TOO_SMALL", "message": "Region area is too small for reliable OCR.", "region_id": region.get("region_id")})
            continue
        if area > max_area_ratio:
            warnings.append({"code": "REGION_AREA_TOO_LARGE", "message": "Region area is too large; likely not a targeted crop.", "region_id": region.get("region_id")})
            continue
        key = (_clean_text(region.get("source_file")), _int(region.get("page")))
        if key not in render_by_page:
            warnings.append({"code": "REGION_PAGE_NOT_RENDERED", "message": "Region source page was not rendered.", "region_id": region.get("region_id")})
            continue
        valid.append(region)
    return valid


def _dedupe_regions(
    regions: Sequence[Mapping[str, Any]],
    *,
    iou_threshold: float,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for region in sorted(regions, key=lambda row: (-_float(row.get("priority"), 0.0), -_float(row.get("confidence"), 0.0))):
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
                        "code": "REGION_DEDUPED_BY_IOU",
                        "message": "Overlapping region removed by IoU dedupe.",
                        "removed_region_id": region.get("region_id"),
                        "kept_region_id": kept.get("region_id"),
                    }
                )
                break
        if not duplicate:
            selected.append(dict(region))
    return selected


def _render_rows_by_page(render_report: Mapping[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for render in render_report.get("render_rows") or []:
        key = (_clean_text(render.get("source_file")), _int(render.get("page")))
        if key[0] and key[1]:
            rows[key] = dict(render)
    return rows


def _pad_bbox_ratio(bbox: Sequence[float], padding: float) -> list[float]:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        round(max(0.0, x1 - padding), 6),
        round(max(0.0, y1 - padding), 6),
        round(min(1.0, x2 + padding), 6),
        round(min(1.0, y2 + padding), 6),
    ]


def _ratio_to_pixel_bbox(bbox: Sequence[float], *, width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return [
        max(0, min(width, int(round(x1 * width)))),
        max(0, min(height, int(round(y1 * height)))),
        max(0, min(width, int(round(x2 * width)))),
        max(0, min(height, int(round(y2 * height)))),
    ]


def _resize_crop(image: Image.Image, requested_scale: float) -> tuple[Image.Image, float]:
    scale = max(1.0, min(4.0, float(requested_scale or 1.0)))
    max_side = max(image.size)
    if max_side <= 0:
        return image, 1.0
    capped_scale = min(scale, MAX_CROP_LONG_SIDE / max_side)
    if capped_scale <= 1.02:
        return image, 1.0
    resized = image.resize((max(1, int(image.width * capped_scale)), max(1, int(image.height * capped_scale))), Image.Resampling.LANCZOS)
    return resized, round(capped_scale, 3)


def _write_region_crop_outputs(
    *,
    crop_dir: Path,
    summary: Mapping[str, Any],
    crop_manifest: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    payloads = [
        ("region_crop_summary_json", crop_dir / "region_crop_summary.json", dict(summary)),
        ("region_crop_manifest_json", crop_dir / "region_crop_manifest.json", list(crop_manifest)),
        (
            "region_crop_diagnostics_json",
            crop_dir / "region_crop_diagnostics.json",
            {"warnings": list(warnings), "errors": list(errors)},
        ),
    ]
    outputs: dict[str, str] = {}
    for key, path, payload in payloads:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    return outputs


def _crop_scale(region: Mapping[str, Any]) -> float:
    strategy = region.get("crop_strategy") if isinstance(region.get("crop_strategy"), Mapping) else {}
    return max(1.0, min(4.0, _float(strategy.get("scale"), 2.0)))


def _crop_padding(region: Mapping[str, Any]) -> float:
    strategy = region.get("crop_strategy") if isinstance(region.get("crop_strategy"), Mapping) else {}
    return max(0.0, min(0.12, _float(strategy.get("padding_ratio"), 0.03)))


def _bbox_iou(left: Any, right: Any) -> float:
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)) or len(left) != 4 or len(right) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = [float(item) for item in left]
    bx1, by1, bx2, by2 = [float(item) for item in right]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = _bbox_area_ratio([ax1, ay1, ax2, ay2]) + _bbox_area_ratio([bx1, by1, bx2, by2]) - inter
    return inter / union if union > 0 else 0.0


def _bbox_area_ratio(bbox: Sequence[float]) -> float:
    x1, y1, x2, y2 = [float(item) for item in bbox]
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


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
