from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw


PHASE = "BIZ-2x-medium-text-region-discovery"
SCHEMA_VERSION = "drawing_text_region_discovery_v1"
OCR_FEEDBACK_SCHEMA_VERSION = "drawing_ocr_quality_feedback_profile_v1"

TEXT_REGION_TYPE = "text_region_candidate"


def build_text_region_discovery_report(
    *,
    render_report: Mapping[str, Any],
    output_dir: str | Path,
    max_pages: int = 0,
    max_regions_per_page: int = 80,
    max_regions: int = 240,
    min_score: float = 0.38,
    min_width_px: int = 8,
    min_height_px: int = 6,
    max_area_ratio: float = 0.18,
    ocr_quality_feedback_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Find likely text regions on medium-resolution rendered drawing pages.

    This stage intentionally does not OCR text or infer material semantics. It
    only creates a conservative region manifest that later high-resolution OCR
    stages can consume.
    """

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    feedback_profile = _normalize_ocr_quality_feedback_profile(ocr_quality_feedback_profile)
    settings = {
        "max_pages": max_pages,
        "max_regions_per_page": max_regions_per_page,
        "max_regions": max_regions,
        "min_score": min_score,
        "min_width_px": min_width_px,
        "min_height_px": min_height_px,
        "max_area_ratio": max_area_ratio,
        **_ocr_feedback_settings(feedback_profile),
    }
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    page_rows = _selected_render_rows(render_report, max_pages=max_pages)
    regions: list[dict[str, Any]] = []
    rejected_regions: list[dict[str, Any]] = []

    try:
        import cv2  # type: ignore
    except Exception as exc:  # noqa: BLE001 - optional local CV dependency
        errors.append({"code": "TEXT_REGION_CV2_UNAVAILABLE", "message": f"opencv-python unavailable: {exc}"})
        return _write_report(
            directory=directory,
            status="failed",
            page_rows=page_rows,
            regions=[],
            rejected_regions=[],
            warnings=warnings,
            errors=errors,
            settings=settings,
        )

    for page_index, render in enumerate(page_rows, start=1):
        image_path = Path(_clean_text(render.get("png_path")))
        if not image_path.exists() or not image_path.is_file():
            warnings.append(
                {
                    "code": "TEXT_REGION_SOURCE_IMAGE_MISSING",
                    "message": "Rendered page image is missing.",
                    "source_image": str(image_path),
                }
            )
            continue
        try:
            page_regions, page_rejected = _detect_page_text_regions(
                cv2=cv2,
                render=render,
                page_index=page_index,
                image_path=image_path,
                min_score=min_score,
                min_width_px=min_width_px,
                min_height_px=min_height_px,
                max_area_ratio=max_area_ratio,
                ocr_quality_feedback_profile=feedback_profile,
            )
        except Exception as exc:  # noqa: BLE001 - one bad page should not stop the batch
            errors.append(
                {
                    "code": "TEXT_REGION_PAGE_FAILED",
                    "message": str(exc),
                    "source_image": str(image_path),
                }
            )
            continue
        regions.extend(page_regions[: max(0, int(max_regions_per_page or 0))])
        rejected_regions.extend(page_rejected)

    regions = _dedupe_regions(regions)
    regions = sorted(
        regions,
        key=lambda item: (
            -_float(item.get("priority"), 0.0),
            -_float(item.get("confidence"), 0.0),
            _int(item.get("page")),
            _bbox_top(item.get("bbox_ratio")),
            _bbox_left(item.get("bbox_ratio")),
        ),
    )[: max(0, int(max_regions or 0))]
    for index, region in enumerate(regions, start=1):
        region["region_id"] = f"tr_{_int(region.get('page')):03d}_{index:04d}"

    annotation_outputs = _write_annotations(
        output_dir=directory / "annotations",
        render_rows=page_rows,
        regions=regions,
        rejected_regions=rejected_regions,
    )
    status = "completed"
    if errors and regions:
        status = "completed_with_errors"
    elif errors:
        status = "failed"
    elif warnings:
        status = "completed_with_warnings"
    elif not regions:
        status = "completed_without_regions"
        warnings.append({"code": "TEXT_REGION_EMPTY", "message": "No likely text regions were selected."})

    report = _write_report(
        directory=directory,
        status=status,
        page_rows=page_rows,
        regions=regions,
        rejected_regions=rejected_regions,
        warnings=warnings,
        errors=errors,
        settings=settings,
    )
    report["outputs"].update(annotation_outputs)
    return report


def _detect_page_text_regions(
    *,
    cv2: Any,
    render: Mapping[str, Any],
    page_index: int,
    image_path: Path,
    min_score: float,
    min_width_px: int,
    min_height_px: int,
    max_area_ratio: float,
    ocr_quality_feedback_profile: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")
    height, width = image.shape[:2]
    foreground = _foreground_mask(cv2, image)
    line_mask = _long_line_mask(cv2, foreground, width=width, height=height)
    no_lines = cv2.bitwise_and(foreground, cv2.bitwise_not(line_mask))
    grouped_line = cv2.dilate(no_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (18, 6)), iterations=1)
    grouped_block = cv2.dilate(no_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (28, 12)), iterations=1)

    raw_boxes = [
        *_contour_boxes(cv2, grouped_line, source="line_group"),
        *_contour_boxes(cv2, grouped_block, source="block_group"),
    ]
    merged_boxes = _merge_candidate_boxes(raw_boxes, width=width, height=height)
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, box in enumerate(merged_boxes, start=1):
        features = _candidate_features(
            cv2=cv2,
            image=image,
            foreground=foreground,
            no_lines=no_lines,
            line_mask=line_mask,
            box=box,
            page_width=width,
            page_height=height,
        )
        decision = _score_candidate(
            features,
            min_width_px=min_width_px,
            min_height_px=min_height_px,
            max_area_ratio=max_area_ratio,
            min_score=min_score,
            ocr_quality_feedback_profile=ocr_quality_feedback_profile,
        )
        row = _region_row(
            render=render,
            page_index=page_index,
            image_path=image_path,
            box=box,
            features=features,
            decision=decision,
            page_width=width,
            page_height=height,
            index=index,
        )
        if decision["selected"]:
            selected.append(row)
        else:
            rejected.append(row)
            if "too_large_for_text_region" in decision.get("flags", []):
                split_selected, split_rejected = _split_rejected_large_region(
                    cv2=cv2,
                    render=render,
                    page_index=page_index,
                    image_path=image_path,
                    image=image,
                    foreground=foreground,
                    no_lines=no_lines,
                    line_mask=line_mask,
                    parent_box=box,
                    parent_row=row,
                    page_width=width,
                    page_height=height,
                    min_score=min_score,
                    min_width_px=min_width_px,
                    min_height_px=min_height_px,
                    max_area_ratio=max_area_ratio,
                    ocr_quality_feedback_profile=ocr_quality_feedback_profile,
                    index_seed=index * 1000,
                )
                selected.extend(split_selected)
                rejected.extend(split_rejected)
    selected.sort(key=lambda item: (-_float(item.get("priority")), _bbox_top(item.get("bbox_ratio")), _bbox_left(item.get("bbox_ratio"))))
    rejected.sort(key=lambda item: (-_float(item.get("priority")), _bbox_top(item.get("bbox_ratio")), _bbox_left(item.get("bbox_ratio"))))
    return selected, rejected


def _split_rejected_large_region(
    *,
    cv2: Any,
    render: Mapping[str, Any],
    page_index: int,
    image_path: Path,
    image: np.ndarray,
    foreground: np.ndarray,
    no_lines: np.ndarray,
    line_mask: np.ndarray,
    parent_box: Mapping[str, Any],
    parent_row: Mapping[str, Any],
    page_width: int,
    page_height: int,
    min_score: float,
    min_width_px: int,
    min_height_px: int,
    max_area_ratio: float,
    ocr_quality_feedback_profile: Mapping[str, Any],
    index_seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    child_boxes = _large_region_child_boxes(
        cv2=cv2,
        no_lines=no_lines,
        parent_box=parent_box,
        page_width=page_width,
        page_height=page_height,
    )
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    child_max_area_ratio = min(max_area_ratio, 0.018)
    child_min_score = max(0.30, min_score - 0.05)
    parent_bbox_pixel = list(parent_row.get("bbox_pixel") or [])
    for child_index, child_box in enumerate(child_boxes, start=1):
        features = _candidate_features(
            cv2=cv2,
            image=image,
            foreground=foreground,
            no_lines=no_lines,
            line_mask=line_mask,
            box=child_box,
            page_width=page_width,
            page_height=page_height,
        )
        features.update(
            {
                "split_parent_bbox_pixel": parent_bbox_pixel,
                "split_parent_area_ratio": (parent_row.get("features") or {}).get("area_ratio", ""),
                "split_source": _clean_text(child_box.get("source")),
            }
        )
        decision = _score_candidate(
            features,
            min_width_px=min_width_px,
            min_height_px=min_height_px,
            max_area_ratio=child_max_area_ratio,
            min_score=child_min_score,
            ocr_quality_feedback_profile=ocr_quality_feedback_profile,
        )
        decision = _apply_split_candidate_gate(decision, features)
        row = _region_row(
            render=render,
            page_index=page_index,
            image_path=image_path,
            box=child_box,
            features=features,
            decision=decision,
            page_width=page_width,
            page_height=page_height,
            index=index_seed + child_index,
        )
        row["planner_source"] = "medium_cv_text_region_detector.large_region_splitter"
        row["parent_region_id"] = parent_row.get("region_id", "")
        row["parent_bbox_pixel"] = parent_bbox_pixel
        row["quality_flags"] = [*list(row.get("quality_flags") or []), "split_from_too_large_region"]
        row["reason"] = _split_decision_reason(decision)
        if decision["selected"]:
            selected.append(row)
        else:
            rejected.append(row)
    selected.sort(key=lambda item: (-_float(item.get("priority")), _bbox_top(item.get("bbox_ratio")), _bbox_left(item.get("bbox_ratio"))))
    rejected.sort(key=lambda item: (-_float(item.get("priority")), _bbox_top(item.get("bbox_ratio")), _bbox_left(item.get("bbox_ratio"))))
    return selected[:40], rejected[:80]


def _large_region_child_boxes(
    *,
    cv2: Any,
    no_lines: np.ndarray,
    parent_box: Mapping[str, Any],
    page_width: int,
    page_height: int,
) -> list[dict[str, Any]]:
    x, y, w, h = _box_edges(parent_box)
    parent_w = max(0, w - x)
    parent_h = max(0, h - y)
    if parent_w < 24 or parent_h < 14:
        return []
    roi = no_lines[y:h, x:w]
    char_mask = _small_component_mask(cv2, roi, parent_width=parent_w, parent_height=parent_h)
    if cv2.countNonZero(char_mask) <= 0:
        return []

    raw_boxes: list[dict[str, Any]] = []
    kernels = [
        (10, 3, "large_split_text_line_tight"),
        (18, 5, "large_split_text_line_loose"),
    ]
    for kernel_width, kernel_height, source in kernels:
        grouped = cv2.dilate(
            char_mask,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, kernel_height)),
            iterations=1,
        )
        for box in _contour_boxes(cv2, grouped, source=source):
            child = _expand_box(
                {
                    "x": int(box["x"]) + x,
                    "y": int(box["y"]) + y,
                    "w": int(box["w"]),
                    "h": int(box["h"]),
                    "source": source,
                },
                pad_x=2,
                pad_y=2,
                page_width=page_width,
                page_height=page_height,
            )
            if _large_region_child_shape_allowed(child, parent_width=parent_w, parent_height=parent_h, page_width=page_width, page_height=page_height):
                raw_boxes.append(child)

    merged = _dedupe_candidate_boxes(raw_boxes)
    filtered = [
        box
        for box in merged
        if _large_region_child_shape_allowed(box, parent_width=parent_w, parent_height=parent_h, page_width=page_width, page_height=page_height)
    ]
    filtered.sort(key=lambda item: (int(item["y"]), int(item["x"]), int(item["h"]) * int(item["w"])))
    return filtered[:160]


def _apply_split_candidate_gate(decision: Mapping[str, Any], features: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(decision)
    flags = list(result.get("flags") or [])
    if _int(features.get("component_count")) < 2:
        flags.append("too_few_split_text_fragments")
    if _int(features.get("width_px")) < 28 and _int(features.get("component_count")) < 3:
        flags.append("split_candidate_too_small")
    if "too_dense_possible_fill_or_hatch" in flags:
        flags.append("split_dense_hatch_noise")
    if any(flag in flags for flag in {"too_few_split_text_fragments", "split_candidate_too_small", "split_dense_hatch_noise"}):
        result["selected"] = False
        result["flags"] = flags
        if _clean_text(result.get("region_subtype")) not in {"noise_or_fill", "line_or_marker_noise"}:
            result["region_subtype"] = "split_noise"
    return result


def _dedupe_candidate_boxes(boxes: Sequence[Mapping[str, Any]], *, iou_threshold: float = 0.68) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    sorted_boxes = sorted(
        [dict(box) for box in boxes if int(box.get("w") or 0) > 0 and int(box.get("h") or 0) > 0],
        key=lambda item: (-(int(item.get("w") or 0) * int(item.get("h") or 0)), int(item.get("y") or 0), int(item.get("x") or 0)),
    )
    for box in sorted_boxes:
        bbox = _box_to_ratio_like(box)
        duplicate = False
        for current in kept:
            if _bbox_iou(bbox, _box_to_ratio_like(current)) >= iou_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return kept


def _box_to_ratio_like(box: Mapping[str, Any]) -> list[float]:
    x = float(box.get("x") or 0)
    y = float(box.get("y") or 0)
    w = float(box.get("w") or 0)
    h = float(box.get("h") or 0)
    return [x, y, x + w, y + h]


def _small_component_mask(cv2: Any, roi: np.ndarray, *, parent_width: int, parent_height: int) -> np.ndarray:
    mask = np.zeros_like(roi)
    if roi.size == 0:
        return mask
    count, labels, stats, _ = cv2.connectedComponentsWithStats(roi, 8)
    max_component_width = max(8, min(96, int(parent_width * 0.18)))
    max_component_height = max(6, min(64, int(parent_height * 0.20)))
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        if area < 2 or width <= 0 or height <= 0:
            continue
        if width > max_component_width or height > max_component_height:
            continue
        aspect = float(width) / max(1.0, float(height))
        if aspect < 0.035 or aspect > 24.0:
            continue
        if (height <= 2 and width >= 18) or (width <= 2 and height >= 18):
            continue
        mask[labels == index] = 255
    return mask


def _large_region_child_shape_allowed(
    box: Mapping[str, Any],
    *,
    parent_width: int,
    parent_height: int,
    page_width: int,
    page_height: int,
) -> bool:
    _, _, w, h = int(box.get("x") or 0), int(box.get("y") or 0), int(box.get("w") or 0), int(box.get("h") or 0)
    if w < 7 or h < 5:
        return False
    if h > min(88, max(12, int(parent_height * 0.22))):
        return False
    if w > min(480, max(24, int(parent_width * 0.55))):
        return False
    area_ratio = float(w * h) / max(1.0, float(page_width * page_height))
    if area_ratio > 0.018:
        return False
    aspect = float(w) / max(1.0, float(h))
    return 0.12 <= aspect <= 45.0


def _expand_box(
    box: Mapping[str, Any],
    *,
    pad_x: int,
    pad_y: int,
    page_width: int,
    page_height: int,
) -> dict[str, Any]:
    x = max(0, int(box.get("x") or 0) - pad_x)
    y = max(0, int(box.get("y") or 0) - pad_y)
    x2 = min(page_width, int(box.get("x") or 0) + int(box.get("w") or 0) + pad_x)
    y2 = min(page_height, int(box.get("y") or 0) + int(box.get("h") or 0) + pad_y)
    return {
        "x": x,
        "y": y,
        "w": max(0, x2 - x),
        "h": max(0, y2 - y),
        "source": _clean_text(box.get("source")),
    }


def _foreground_mask(cv2: Any, image: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    min_channel = np.minimum(np.minimum(r, g), b)
    max_channel = np.maximum(np.maximum(r, g), b)
    dark = min_channel < 218
    non_white = min_channel < 246
    saturated = (hsv[:, :, 1] > 42) & (hsv[:, :, 2] > 70)
    channel_delta = (max_channel - min_channel) > 28
    mask = (dark | (saturated & non_white) | (channel_delta & non_white)).astype(np.uint8) * 255
    return mask


def _long_line_mask(cv2: Any, mask: np.ndarray, *, width: int, height: int) -> np.ndarray:
    horizontal_size = max(36, min(160, width // 18))
    vertical_size = max(36, min(160, height // 18))
    horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_size, 1)))
    vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, vertical_size)))
    return cv2.bitwise_or(horizontal, vertical)


def _contour_boxes(cv2: Any, mask: np.ndarray, *, source: str) -> list[dict[str, Any]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[dict[str, Any]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        boxes.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "source": source})
    return boxes


def _merge_candidate_boxes(boxes: Sequence[Mapping[str, Any]], *, width: int, height: int) -> list[dict[str, Any]]:
    cleaned = [
        dict(box)
        for box in boxes
        if int(box.get("w") or 0) > 0 and int(box.get("h") or 0) > 0 and int(box.get("w") or 0) <= width and int(box.get("h") or 0) <= height
    ]
    merged: list[dict[str, Any]] = []
    for box in sorted(cleaned, key=lambda item: (int(item["y"]), int(item["x"]), int(item["h"]))):
        candidate = dict(box)
        did_merge = False
        for current in merged:
            if _boxes_should_merge(current, candidate):
                x1 = min(int(current["x"]), int(candidate["x"]))
                y1 = min(int(current["y"]), int(candidate["y"]))
                x2 = max(int(current["x"] + current["w"]), int(candidate["x"] + candidate["w"]))
                y2 = max(int(current["y"] + current["h"]), int(candidate["y"] + candidate["h"]))
                current.update(
                    {
                        "x": max(0, x1),
                        "y": max(0, y1),
                        "w": min(width, x2) - max(0, x1),
                        "h": min(height, y2) - max(0, y1),
                        "source": _join_unique([current.get("source"), candidate.get("source")]),
                    }
                )
                did_merge = True
                break
        if not did_merge:
            merged.append(candidate)
    return [box for box in merged if int(box["w"]) > 0 and int(box["h"]) > 0]


def _boxes_should_merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    lx1, ly1, lx2, ly2 = _box_edges(left)
    rx1, ry1, rx2, ry2 = _box_edges(right)
    overlap_x = min(lx2, rx2) - max(lx1, rx1)
    overlap_y = min(ly2, ry2) - max(ly1, ry1)
    if overlap_x > 0 and overlap_y > 0:
        return True
    left_h = max(1, ly2 - ly1)
    right_h = max(1, ry2 - ry1)
    center_y_close = abs(((ly1 + ly2) / 2.0) - ((ry1 + ry2) / 2.0)) <= max(10, min(left_h, right_h) * 0.75)
    horizontal_gap = max(0, max(lx1, rx1) - min(lx2, rx2))
    vertical_gap = max(0, max(ly1, ry1) - min(ly2, ry2))
    similar_height = max(left_h, right_h) / max(1, min(left_h, right_h)) <= 2.4
    return (center_y_close and horizontal_gap <= 22 and similar_height) or (vertical_gap <= 8 and overlap_x > min(left_h, right_h))


def _candidate_features(
    *,
    cv2: Any,
    image: np.ndarray,
    foreground: np.ndarray,
    no_lines: np.ndarray,
    line_mask: np.ndarray,
    box: Mapping[str, Any],
    page_width: int,
    page_height: int,
) -> dict[str, Any]:
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    area = max(1, w * h)
    fg_roi = foreground[y : y + h, x : x + w]
    clean_roi = no_lines[y : y + h, x : x + w]
    line_roi = line_mask[y : y + h, x : x + w]
    image_roi = image[y : y + h, x : x + w]
    density = float(cv2.countNonZero(clean_roi)) / float(area)
    raw_density = float(cv2.countNonZero(fg_roi)) / float(area)
    line_ratio = float(cv2.countNonZero(line_roi)) / max(1.0, float(cv2.countNonZero(fg_roi)))
    component_count = _component_count(cv2, clean_roi)
    color_ratio = _saturated_pixel_ratio(cv2, image_roi)
    aspect_ratio = float(w) / max(1.0, float(h))
    area_ratio = float(area) / max(1.0, float(page_width * page_height))
    center_x = (x + w / 2.0) / max(1.0, float(page_width))
    center_y = (y + h / 2.0) / max(1.0, float(page_height))
    return {
        "width_px": w,
        "height_px": h,
        "area_px": area,
        "area_ratio": round(area_ratio, 8),
        "aspect_ratio": round(aspect_ratio, 6),
        "foreground_density": round(raw_density, 6),
        "text_density": round(density, 6),
        "line_ratio": round(line_ratio, 6),
        "component_count": component_count,
        "color_ratio": round(color_ratio, 6),
        "page_zone": _page_zone(center_x=center_x, center_y=center_y),
        "center_x_ratio": round(center_x, 6),
        "center_y_ratio": round(center_y, 6),
    }


def _component_count(cv2: Any, roi: np.ndarray) -> int:
    if roi.size == 0:
        return 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(roi, 8)
    result = 0
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        if area >= 2 and width >= 1 and height >= 1:
            result += 1
    return result


def _saturated_pixel_ratio(cv2: Any, image_roi: np.ndarray) -> float:
    if image_roi.size == 0:
        return 0.0
    hsv = cv2.cvtColor(image_roi, cv2.COLOR_BGR2HSV)
    saturated = (hsv[:, :, 1] > 55) & (hsv[:, :, 2] > 80)
    return float(np.count_nonzero(saturated)) / max(1.0, float(image_roi.shape[0] * image_roi.shape[1]))


def _score_candidate(
    features: Mapping[str, Any],
    *,
    min_width_px: int,
    min_height_px: int,
    max_area_ratio: float,
    min_score: float,
    ocr_quality_feedback_profile: Mapping[str, Any],
) -> dict[str, Any]:
    flags: list[str] = []
    w = _int(features.get("width_px"))
    h = _int(features.get("height_px"))
    area_ratio = _float(features.get("area_ratio"))
    aspect = _float(features.get("aspect_ratio"))
    density = _float(features.get("text_density"))
    raw_density = _float(features.get("foreground_density"))
    line_ratio = _float(features.get("line_ratio"))
    component_count = _int(features.get("component_count"))
    color_ratio = _float(features.get("color_ratio"))
    if w < min_width_px:
        flags.append("too_narrow")
    if h < min_height_px:
        flags.append("too_short")
    if area_ratio > max_area_ratio:
        flags.append("too_large_for_text_region")
    if aspect < 0.06 or aspect > 70:
        flags.append("aspect_ratio_unlikely_text")
    if density < 0.006:
        flags.append("too_sparse_after_line_removal")
    if raw_density > 0.72 and area_ratio > 0.0004:
        flags.append("too_dense_possible_fill_or_hatch")
    if line_ratio > 0.78 and component_count <= 2:
        flags.append("line_dominant")
    if component_count <= 0:
        flags.append("no_text_like_components")
    if component_count <= 1 and density < 0.085 and line_ratio >= 0.25:
        flags.append("single_component_stroke")
    if color_ratio >= 0.025 and component_count <= 1 and density < 0.085:
        flags.append("colored_region_without_text_fragments")

    height_score = _bell_score(h, low=7, ideal_low=14, ideal_high=80, high=190)
    density_score = _bell_score(density, low=0.006, ideal_low=0.02, ideal_high=0.22, high=0.55)
    component_score = min(1.0, math.log1p(max(0, component_count)) / math.log(18))
    aspect_score = _bell_score(aspect, low=0.08, ideal_low=0.6, ideal_high=18.0, high=55.0)
    color_score = min(1.0, color_ratio * 12.0)
    line_penalty = min(0.42, line_ratio * 0.34)
    size_penalty = 0.18 if area_ratio > max_area_ratio * 0.55 else 0.0
    score = (
        height_score * 0.24
        + density_score * 0.24
        + component_score * 0.22
        + aspect_score * 0.16
        + color_score * 0.14
        - line_penalty
        - size_penalty
    )
    zone = _clean_text(features.get("page_zone"))
    if zone == "right_notes":
        score += 0.04
    elif zone == "bottom_title":
        score -= 0.04
    base_score = max(0.0, min(1.0, score))
    feedback = _ocr_feedback_adjustment(features, ocr_quality_feedback_profile)
    if feedback["positive_shape_match"]:
        flags.append("ocr_feedback_positive_shape_match")
    if feedback["negative_shape_match"]:
        flags.append("ocr_feedback_negative_shape_match")
    score = max(0.0, min(1.0, base_score + _float(feedback.get("score_delta"), 0.0)))
    hard_rejects = {
        "too_narrow",
        "too_short",
        "too_large_for_text_region",
        "aspect_ratio_unlikely_text",
        "too_sparse_after_line_removal",
        "line_dominant",
        "no_text_like_components",
        "single_component_stroke",
        "colored_region_without_text_fragments",
    }
    selected = score >= min_score and not any(flag in hard_rejects for flag in flags)
    if not selected and score < min_score:
        flags.append("score_below_threshold")
    return {
        "selected": selected,
        "base_score": round(base_score, 6),
        "score": round(score, 6),
        "confidence": round(max(0.0, min(0.98, score * 0.82 + 0.08)), 6),
        "flags": flags,
        "suggested_highres_scale": _suggested_highres_scale(features),
        "region_subtype": _region_subtype(features, flags=flags),
        "ocr_feedback_score_delta": round(_float(feedback.get("score_delta"), 0.0), 6),
        "ocr_feedback_positive_similarity": round(_float(feedback.get("positive_similarity"), 0.0), 6),
        "ocr_feedback_negative_similarity": round(_float(feedback.get("negative_similarity"), 0.0), 6),
        "ocr_feedback_positive_shape_match": bool(feedback.get("positive_shape_match")),
        "ocr_feedback_negative_shape_match": bool(feedback.get("negative_shape_match")),
        "ocr_feedback_reason": _clean_text(feedback.get("reason")),
    }


def _normalize_ocr_quality_feedback_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        return {}
    normalized = dict(profile)
    positive_count = _int(normalized.get("positive_sample_count"))
    negative_count = _int(normalized.get("negative_sample_count"))
    if positive_count <= 0 and negative_count <= 0:
        return {}
    return normalized


def _ocr_feedback_settings(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ocr_quality_feedback_enabled": bool(profile),
        "ocr_quality_feedback_schema_version": _clean_text(profile.get("schema_version")) if profile else "",
        "ocr_quality_feedback_positive_sample_count": _int(profile.get("positive_sample_count")) if profile else 0,
        "ocr_quality_feedback_negative_sample_count": _int(profile.get("negative_sample_count")) if profile else 0,
    }


def _ocr_feedback_adjustment(features: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    if not profile:
        return {
            "score_delta": 0.0,
            "positive_similarity": 0.0,
            "negative_similarity": 0.0,
            "positive_shape_match": False,
            "negative_shape_match": False,
            "reason": "",
        }

    settings = profile.get("settings") if isinstance(profile.get("settings"), Mapping) else {}
    threshold = _float(settings.get("shape_match_threshold"), 0.55)
    positive_weight = _float(settings.get("positive_score_weight"), 0.10)
    negative_weight = _float(settings.get("negative_score_weight"), 0.14)
    max_positive_delta = _float(settings.get("max_positive_delta"), 0.12)
    max_negative_delta = _float(settings.get("max_negative_delta"), -0.18)
    positive_similarity = _feature_profile_similarity(features, profile.get("positive_feature_profile"))
    negative_similarity = _feature_profile_similarity(features, profile.get("negative_feature_profile"))
    positive_match = positive_similarity >= threshold and _int(profile.get("positive_sample_count")) > 0
    negative_match = negative_similarity >= threshold and _int(profile.get("negative_sample_count")) > 0

    positive_delta = min(max_positive_delta, positive_similarity * positive_weight) if positive_match else 0.0
    negative_delta = -min(abs(max_negative_delta), negative_similarity * negative_weight) if negative_match else 0.0
    score_delta = max(max_negative_delta, min(max_positive_delta, positive_delta + negative_delta))
    reasons: list[str] = []
    if positive_match:
        reasons.append(f"positive_shape_match:{positive_similarity:.2f}")
    if negative_match:
        reasons.append(f"negative_shape_match:{negative_similarity:.2f}")
    return {
        "score_delta": round(score_delta, 6),
        "positive_similarity": round(positive_similarity, 6),
        "negative_similarity": round(negative_similarity, 6),
        "positive_shape_match": positive_match,
        "negative_shape_match": negative_match,
        "reason": ";".join(reasons),
    }


def _feature_profile_similarity(features: Mapping[str, Any], profile: Any) -> float:
    if not isinstance(profile, Mapping) or _int(profile.get("sample_count")) <= 0:
        return 0.0
    numeric_ranges = profile.get("numeric_ranges") if isinstance(profile.get("numeric_ranges"), Mapping) else {}
    numeric_scores: list[float] = []
    for feature, feature_range in numeric_ranges.items():
        if not isinstance(feature_range, Mapping):
            continue
        value = _float(features.get(feature), default=float("nan"))
        if _is_nan(value):
            continue
        numeric_scores.append(_range_similarity(value, feature_range))

    categorical_values = profile.get("categorical_values") if isinstance(profile.get("categorical_values"), Mapping) else {}
    categorical_scores: list[float] = []
    for feature, options in categorical_values.items():
        candidate = _clean_text(features.get(feature))
        if not candidate or not isinstance(options, Sequence):
            continue
        matched = 0.0
        for option in options:
            if not isinstance(option, Mapping):
                continue
            if candidate == _clean_text(option.get("value")):
                matched = max(matched, 0.65 + min(0.35, _float(option.get("ratio"), 0.0) * 0.35))
        categorical_scores.append(matched)

    numeric_score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0.0
    categorical_score = sum(categorical_scores) / len(categorical_scores) if categorical_scores else 0.0
    if numeric_scores and categorical_scores:
        return max(0.0, min(1.0, numeric_score * 0.76 + categorical_score * 0.24))
    if numeric_scores:
        return max(0.0, min(1.0, numeric_score))
    if categorical_scores:
        return max(0.0, min(1.0, categorical_score))
    return 0.0


def _range_similarity(value: float, feature_range: Mapping[str, Any]) -> float:
    low = _float(feature_range.get("min"), 0.0)
    high = _float(feature_range.get("max"), low)
    if high < low:
        low, high = high, low
    if low <= value <= high:
        return 1.0
    span = max(1e-9, high - low)
    distance = low - value if value < low else value - high
    return max(0.0, min(1.0, 1.0 - distance / span))


def _bell_score(value: float, *, low: float, ideal_low: float, ideal_high: float, high: float) -> float:
    if value < low or value > high:
        return 0.0
    if ideal_low <= value <= ideal_high:
        return 1.0
    if value < ideal_low:
        return max(0.0, (value - low) / max(ideal_low - low, 1e-9))
    return max(0.0, (high - value) / max(high - ideal_high, 1e-9))


def _suggested_highres_scale(features: Mapping[str, Any]) -> float:
    h = _int(features.get("height_px"))
    area_ratio = _float(features.get("area_ratio"))
    if h <= 18 or area_ratio < 0.00008:
        return 64.0
    if h <= 42 or area_ratio < 0.0004:
        return 48.0
    return 32.0


def _region_subtype(features: Mapping[str, Any], *, flags: Sequence[str]) -> str:
    if any(flag in {"single_component_stroke", "colored_region_without_text_fragments"} for flag in flags):
        return "line_or_marker_noise"
    if any(flag in {"too_large_for_text_region", "too_dense_possible_fill_or_hatch"} for flag in flags):
        return "noise_or_fill"
    if _float(features.get("color_ratio")) >= 0.025:
        return "colored_text_or_callout"
    if _clean_text(features.get("page_zone")) == "right_notes":
        return "right_side_notes_text"
    if _float(features.get("aspect_ratio")) >= 8:
        return "text_line"
    return "text_block"


def _region_row(
    *,
    render: Mapping[str, Any],
    page_index: int,
    image_path: Path,
    box: Mapping[str, Any],
    features: Mapping[str, Any],
    decision: Mapping[str, Any],
    page_width: int,
    page_height: int,
    index: int,
) -> dict[str, Any]:
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    bbox_ratio = [
        round(x / max(page_width, 1), 8),
        round(y / max(page_height, 1), 8),
        round((x + w) / max(page_width, 1), 8),
        round((y + h) / max(page_height, 1), 8),
    ]
    scale = float(decision.get("suggested_highres_scale") or 48.0)
    return {
        "region_id": f"tr_raw_{page_index:03d}_{index:04d}",
        "region_type": TEXT_REGION_TYPE,
        "region_subtype": _clean_text(decision.get("region_subtype")) or "text_block",
        "source_file": _clean_text(render.get("source_file")) or image_path.name,
        "page": _int(render.get("page")) or page_index,
        "source_image_path": str(image_path.resolve()),
        "bbox_pixel": [x, y, x + w, y + h],
        "bbox_ratio": bbox_ratio,
        "base_priority": round(float(decision.get("base_score") or decision.get("score") or 0.0), 6),
        "priority": round(float(decision.get("score") or 0.0), 6),
        "confidence": round(float(decision.get("confidence") or 0.0), 6),
        "ocr_feedback_score_delta": round(float(decision.get("ocr_feedback_score_delta") or 0.0), 6),
        "ocr_feedback_positive_similarity": round(float(decision.get("ocr_feedback_positive_similarity") or 0.0), 6),
        "ocr_feedback_negative_similarity": round(float(decision.get("ocr_feedback_negative_similarity") or 0.0), 6),
        "ocr_feedback_positive_shape_match": bool(decision.get("ocr_feedback_positive_shape_match")),
        "ocr_feedback_negative_shape_match": bool(decision.get("ocr_feedback_negative_shape_match")),
        "ocr_feedback_reason": _clean_text(decision.get("ocr_feedback_reason")),
        "recommended_tools": ["ocr"],
        "expected_information": ["drawing_text"],
        "crop_strategy": {"highres_scale": scale, "padding_ratio": _padding_for_scale(scale)},
        "features": dict(features),
        "quality_flags": list(decision.get("flags") or []),
        "selected": bool(decision.get("selected")),
        "reason": _decision_reason(decision),
        "planner_source": "medium_cv_text_region_detector",
    }


def _padding_for_scale(scale: float) -> float:
    if scale >= 64:
        return 0.012
    if scale >= 48:
        return 0.018
    return 0.024


def _decision_reason(decision: Mapping[str, Any]) -> str:
    if decision.get("selected"):
        return "Likely text-like foreground region selected for later high-resolution OCR."
    flags = [str(item) for item in decision.get("flags") or []]
    return "Rejected by medium-resolution text-region gate: " + ", ".join(flags[:6])


def _split_decision_reason(decision: Mapping[str, Any]) -> str:
    if decision.get("selected"):
        return "Recovered from a large CAD foreground region by secondary text-cluster splitting."
    flags = [str(item) for item in decision.get("flags") or []]
    return "Rejected by secondary split gate inside large CAD region: " + ", ".join(flags[:6])


def _dedupe_regions(regions: Sequence[Mapping[str, Any]], *, iou_threshold: float = 0.72) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for region in sorted(regions, key=lambda item: -_float(item.get("priority"))):
        duplicate_index = -1
        for index, current in enumerate(kept):
            if _clean_text(current.get("source_file")) != _clean_text(region.get("source_file")):
                continue
            if _int(current.get("page")) != _int(region.get("page")):
                continue
            if _bbox_iou(current.get("bbox_ratio"), region.get("bbox_ratio")) >= iou_threshold:
                duplicate_index = index
                break
        if duplicate_index < 0:
            kept.append(dict(region))
        elif _float(region.get("priority")) > _float(kept[duplicate_index].get("priority")):
            kept[duplicate_index] = dict(region)
    return kept


def _selected_render_rows(render_report: Mapping[str, Any], *, max_pages: int) -> list[dict[str, Any]]:
    rows = [dict(row) for row in render_report.get("render_rows") or [] if _clean_text(row.get("png_path"))]
    rows.sort(key=lambda item: (_clean_text(item.get("source_file")), _int(item.get("page"))))
    if max_pages and max_pages > 0:
        return rows[:max_pages]
    return rows


def _write_report(
    *,
    directory: Path,
    status: str,
    page_rows: Sequence[Mapping[str, Any]],
    regions: Sequence[Mapping[str, Any]],
    rejected_regions: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    summary = {
        "text_region_discovery_status": status,
        "page_count": len(page_rows),
        "selected_region_count": len(regions),
        "rejected_region_count": len(rejected_regions),
        "colored_region_count": sum(1 for row in regions if row.get("region_subtype") == "colored_text_or_callout"),
        "right_notes_region_count": sum(1 for row in regions if row.get("region_subtype") == "right_side_notes_text"),
        "large_region_split_selected_count": sum(
            1 for row in regions if _clean_text(row.get("planner_source")) == "medium_cv_text_region_detector.large_region_splitter"
        ),
        "large_region_split_rejected_count": sum(
            1 for row in rejected_regions if _clean_text(row.get("planner_source")) == "medium_cv_text_region_detector.large_region_splitter"
        ),
        "suggested_64x_region_count": sum(1 for row in regions if _float((row.get("crop_strategy") or {}).get("highres_scale")) >= 64),
        "ocr_feedback_enabled": bool(settings.get("ocr_quality_feedback_enabled")),
        "ocr_feedback_positive_match_count": sum(1 for row in regions if row.get("ocr_feedback_positive_shape_match")),
        "ocr_feedback_negative_match_count": sum(1 for row in regions if row.get("ocr_feedback_negative_shape_match")),
        "ocr_feedback_rejected_positive_match_count": sum(1 for row in rejected_regions if row.get("ocr_feedback_positive_shape_match")),
        "ocr_feedback_rejected_negative_match_count": sum(1 for row in rejected_regions if row.get("ocr_feedback_negative_shape_match")),
        "warning_count": len(warnings),
        "error_count": len(errors),
        "settings": dict(settings),
    }
    outputs = _write_outputs(
        directory=directory,
        summary=summary,
        regions=regions,
        rejected_regions=rejected_regions,
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
        "regions": list(regions),
        "rejected_regions": list(rejected_regions),
        "warnings": list(warnings),
        "errors": list(errors),
        "outputs": outputs,
    }


def _write_outputs(
    *,
    directory: Path,
    summary: Mapping[str, Any],
    regions: Sequence[Mapping[str, Any]],
    rejected_regions: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    payloads = [
        ("text_region_summary_json", "text_region_summary.json", dict(summary)),
        ("text_region_plan_json", "text_region_plan.json", {"schema_version": SCHEMA_VERSION, "regions": list(regions)}),
        ("text_region_rejected_json", "text_region_rejected.json", list(rejected_regions)),
        ("text_region_diagnostics_json", "text_region_diagnostics.json", {"warnings": list(warnings), "errors": list(errors)}),
    ]
    outputs: dict[str, str] = {}
    for key, filename, payload in payloads:
        path = directory / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    region_csv = directory / "text_region_candidates.csv"
    _write_region_csv(region_csv, regions)
    outputs["text_region_candidates_csv"] = str(region_csv.resolve())
    rejected_csv = directory / "text_region_rejected.csv"
    _write_region_csv(rejected_csv, rejected_regions)
    outputs["text_region_rejected_csv"] = str(rejected_csv.resolve())
    return outputs


def _write_region_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "region_id",
        "source_file",
        "page",
        "selected",
        "region_subtype",
        "base_priority",
        "priority",
        "confidence",
        "ocr_feedback_score_delta",
        "ocr_feedback_positive_similarity",
        "ocr_feedback_negative_similarity",
        "ocr_feedback_positive_shape_match",
        "ocr_feedback_negative_shape_match",
        "ocr_feedback_reason",
        "bbox_ratio",
        "bbox_pixel",
        "highres_scale",
        "width_px",
        "height_px",
        "text_density",
        "line_ratio",
        "component_count",
        "color_ratio",
        "page_zone",
        "planner_source",
        "parent_bbox_pixel",
        "quality_flags",
        "reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            features = row.get("features") if isinstance(row.get("features"), Mapping) else {}
            crop_strategy = row.get("crop_strategy") if isinstance(row.get("crop_strategy"), Mapping) else {}
            writer.writerow(
                {
                    "region_id": row.get("region_id", ""),
                    "source_file": row.get("source_file", ""),
                    "page": row.get("page", ""),
                    "selected": row.get("selected", ""),
                    "region_subtype": row.get("region_subtype", ""),
                    "base_priority": row.get("base_priority", ""),
                    "priority": row.get("priority", ""),
                    "confidence": row.get("confidence", ""),
                    "ocr_feedback_score_delta": row.get("ocr_feedback_score_delta", ""),
                    "ocr_feedback_positive_similarity": row.get("ocr_feedback_positive_similarity", ""),
                    "ocr_feedback_negative_similarity": row.get("ocr_feedback_negative_similarity", ""),
                    "ocr_feedback_positive_shape_match": row.get("ocr_feedback_positive_shape_match", ""),
                    "ocr_feedback_negative_shape_match": row.get("ocr_feedback_negative_shape_match", ""),
                    "ocr_feedback_reason": row.get("ocr_feedback_reason", ""),
                    "bbox_ratio": json.dumps(row.get("bbox_ratio") or [], ensure_ascii=False),
                    "bbox_pixel": json.dumps(row.get("bbox_pixel") or [], ensure_ascii=False),
                    "highres_scale": crop_strategy.get("highres_scale", ""),
                    "width_px": features.get("width_px", ""),
                    "height_px": features.get("height_px", ""),
                    "text_density": features.get("text_density", ""),
                    "line_ratio": features.get("line_ratio", ""),
                    "component_count": features.get("component_count", ""),
                    "color_ratio": features.get("color_ratio", ""),
                    "page_zone": features.get("page_zone", ""),
                    "planner_source": row.get("planner_source", ""),
                    "parent_bbox_pixel": json.dumps(row.get("parent_bbox_pixel") or [], ensure_ascii=False),
                    "quality_flags": "|".join(str(item) for item in row.get("quality_flags") or []),
                    "reason": row.get("reason", ""),
                }
            )


def _write_annotations(
    *,
    output_dir: Path,
    render_rows: Sequence[Mapping[str, Any]],
    regions: Sequence[Mapping[str, Any]],
    rejected_regions: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    paths: list[str] = []
    for render in render_rows:
        image_path = Path(_clean_text(render.get("png_path")))
        if not image_path.exists() or not image_path.is_file():
            continue
        source_file = _clean_text(render.get("source_file")) or image_path.stem
        page = _int(render.get("page"))
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)
        page_regions = [
            row
            for row in regions
            if _clean_text(row.get("source_file")) == source_file and _int(row.get("page")) == page
        ]
        page_rejected = [
            row
            for row in rejected_regions
            if _clean_text(row.get("source_file")) == source_file and _int(row.get("page")) == page
        ][:80]
        _draw_regions(draw, page_rejected, outline=(180, 180, 180), label_prefix="R")
        _draw_regions(draw, page_regions, outline=(255, 64, 0), label_prefix="T")
        output_path = output_dir / f"{_safe_stem(source_file)}_p{page:03d}_text_regions.png"
        image.save(output_path)
        paths.append(str(output_path.resolve()))
    manifest_path = output_dir / "text_region_annotations.json"
    manifest_path.write_text(json.dumps(paths, ensure_ascii=False, indent=2), encoding="utf-8")
    outputs["text_region_annotations_json"] = str(manifest_path.resolve())
    if paths:
        outputs["text_region_annotation_count"] = str(len(paths))
    return outputs


def _draw_regions(draw: ImageDraw.ImageDraw, rows: Sequence[Mapping[str, Any]], *, outline: tuple[int, int, int], label_prefix: str) -> None:
    for index, row in enumerate(rows, start=1):
        bbox = row.get("bbox_pixel") or []
        if not isinstance(bbox, Sequence) or len(bbox) != 4:
            continue
        try:
            x1, y1, x2, y2 = [int(float(item)) for item in bbox]
        except (TypeError, ValueError):
            continue
        draw.rectangle((x1, y1, x2, y2), outline=outline, width=2)
        label = f"{label_prefix}{index}:{float(row.get('priority') or 0):.2f}"
        draw.rectangle((x1, max(0, y1 - 14), min(x2, x1 + 84), y1), fill=(255, 255, 255), outline=outline)
        draw.text((x1 + 2, max(0, y1 - 13)), label, fill=outline)


def _page_zone(*, center_x: float, center_y: float) -> str:
    if center_y >= 0.78:
        return "bottom_title"
    if center_x >= 0.68:
        return "right_notes"
    if center_y <= 0.16:
        return "top_header"
    return "main_drawing"


def _box_edges(box: Mapping[str, Any]) -> tuple[int, int, int, int]:
    x = int(box.get("x") or 0)
    y = int(box.get("y") or 0)
    w = int(box.get("w") or 0)
    h = int(box.get("h") or 0)
    return x, y, x + w, y + h


def _bbox_iou(left: Any, right: Any) -> float:
    if not isinstance(left, Sequence) or not isinstance(right, Sequence) or len(left) != 4 or len(right) != 4:
        return 0.0
    try:
        lx1, ly1, lx2, ly2 = [float(item) for item in left]
        rx1, ry1, rx2, ry2 = [float(item) for item in right]
    except (TypeError, ValueError):
        return 0.0
    ix1, iy1 = max(lx1, rx1), max(ly1, ry1)
    ix2, iy2 = min(lx2, rx2), min(ly2, ry2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    left_area = max(0.0, lx2 - lx1) * max(0.0, ly2 - ly1)
    right_area = max(0.0, rx2 - rx1) * max(0.0, ry2 - ry1)
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _bbox_top(value: Any) -> float:
    return float(value[1]) if isinstance(value, Sequence) and len(value) >= 2 else 0.0


def _bbox_left(value: Any) -> float:
    return float(value[0]) if isinstance(value, Sequence) and value else 0.0


def _join_unique(values: Sequence[Any]) -> str:
    result: list[str] = []
    for raw in values:
        value = _clean_text(raw)
        if value and value not in result:
            result.append(value)
    return "|".join(result)


def _safe_stem(value: str) -> str:
    text = Path(value or "drawing").stem
    text = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("._")
    return text or "drawing"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value
