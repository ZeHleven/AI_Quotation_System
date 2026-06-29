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
    overflow_regions: list[dict[str, Any]] = []

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
            overflow_regions=[],
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
            page_regions, page_rejected, page_overflow = _detect_page_text_regions(
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
        page_limit = max(0, int(max_regions_per_page or 0))
        page_selected, page_budget_overflow = _select_regions_with_budget_diversity(
            page_regions,
            limit=page_limit,
            scope="page",
        )
        regions.extend(page_selected)
        overflow_regions.extend(
            _overflow_rows(
                page_budget_overflow,
                overflow_reason="page_region_cap",
                overflow_reason_cn="超过单页候选上限，未进入本轮 OCR 计划",
                overflow_source_bucket="selected_candidate",
            )
        )
        rejected_regions.extend(page_rejected)
        overflow_regions.extend(page_overflow)

    regions = _dedupe_regions(regions)
    sorted_regions = sorted(
        regions,
        key=lambda item: (
            -_float(item.get("priority"), 0.0),
            -_float(item.get("confidence"), 0.0),
            _int(item.get("page")),
            _bbox_top(item.get("bbox_ratio")),
            _bbox_left(item.get("bbox_ratio")),
        ),
    )
    global_limit = max(0, int(max_regions or 0))
    regions, global_budget_overflow = _select_regions_with_budget_diversity(
        sorted_regions,
        limit=global_limit,
        scope="global",
    )
    overflow_regions.extend(
        _overflow_rows(
            global_budget_overflow,
            overflow_reason="global_region_cap",
            overflow_reason_cn="超过全局候选上限，未进入本轮 OCR 计划",
            overflow_source_bucket="selected_candidate",
        )
    )
    rejected_regions = [_with_rejected_layer(row, min_score=min_score) for row in rejected_regions]
    overflow_regions = [_with_rejected_layer(row, min_score=min_score) for row in overflow_regions]
    for index, region in enumerate(regions, start=1):
        region["region_id"] = f"tr_{_int(region.get('page')):03d}_{index:04d}"
    regions = [_with_candidate_explanation(row) for row in regions]
    rejected_regions = [_with_candidate_explanation(row) for row in rejected_regions]
    overflow_regions = [_with_candidate_explanation(row) for row in overflow_regions]

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
        overflow_regions=overflow_regions,
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
    merged_boxes.extend(_colored_annotation_candidate_boxes(cv2=cv2, image=image, page_width=width, page_height=height))
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
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
                split_selected, split_rejected, split_overflow = _split_rejected_large_region(
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
                overflow.extend(split_overflow)
    selected.sort(key=lambda item: (-_float(item.get("priority")), _bbox_top(item.get("bbox_ratio")), _bbox_left(item.get("bbox_ratio"))))
    rejected.sort(key=lambda item: (-_float(item.get("priority")), _bbox_top(item.get("bbox_ratio")), _bbox_left(item.get("bbox_ratio"))))
    overflow.sort(key=lambda item: (-_float(item.get("priority")), _bbox_top(item.get("bbox_ratio")), _bbox_left(item.get("bbox_ratio"))))
    return selected, rejected, overflow


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
    selected_limit = 40
    rejected_limit = 80
    overflow = [
        *_overflow_rows(
            selected[selected_limit:],
            overflow_reason="large_region_split_selected_cap",
            overflow_reason_cn="超过单个大块拆分 selected 上限，未进入本轮 OCR 计划",
            overflow_source_bucket="selected_candidate",
            parent_bbox_pixel=parent_bbox_pixel,
        ),
        *_overflow_rows(
            rejected[rejected_limit:],
            overflow_reason="large_region_split_rejected_cap",
            overflow_reason_cn="超过单个大块拆分 rejected 记录上限，仅进入 overflow 审阅清单",
            overflow_source_bucket="rejected_candidate",
            parent_bbox_pixel=parent_bbox_pixel,
        ),
    ]
    return selected[:selected_limit], rejected[:rejected_limit], overflow


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


def _select_regions_with_budget_diversity(
    regions: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    scope: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = [_with_budget_bucket(row, scope=scope) for row in regions]
    candidates = sorted(candidates, key=_region_priority_sort_key)
    if limit <= 0:
        return [], [_with_budget_selection(row, selected=False, rank=0, scope=scope) for row in candidates]
    if len(candidates) <= limit:
        return [_with_budget_selection(row, selected=True, rank=index, scope=scope) for index, row in enumerate(candidates, start=1)], []

    bucket_order = [
        "ocr_positive_feedback",
        "colored_annotation",
        "right_notes",
        "large_region_split",
        "main_drawing",
        "fallback_high_priority",
    ]
    bucket_ratios = {
        "ocr_positive_feedback": 0.22,
        "colored_annotation": 0.24,
        "right_notes": 0.14,
        "large_region_split": 0.14,
        "main_drawing": 0.18,
        "fallback_high_priority": 0.08,
    }
    by_bucket: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in bucket_order}
    for row in candidates:
        by_bucket.setdefault(_clean_text(row.get("budget_bucket")) or "fallback_high_priority", []).append(row)

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    for bucket in bucket_order:
        rows = by_bucket.get(bucket) or []
        if not rows or len(selected) >= limit:
            continue
        target = min(len(rows), max(1, int(math.ceil(limit * bucket_ratios.get(bucket, 0.0)))))
        target = min(target, limit - len(selected))
        for row in rows[:target]:
            key = _region_identity_key(row)
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= limit:
                break

    for row in candidates:
        if len(selected) >= limit:
            break
        key = _region_identity_key(row)
        if key in selected_keys:
            continue
        selected.append(row)
        selected_keys.add(key)

    selected = selected[:limit]
    selected_keys = {_region_identity_key(row) for row in selected}
    selected_rows = [
        _with_budget_selection(row, selected=True, rank=index, scope=scope)
        for index, row in enumerate(sorted(selected, key=_region_priority_sort_key), start=1)
    ]
    overflow_rows = [
        _with_budget_selection(row, selected=False, rank=0, scope=scope)
        for row in candidates
        if _region_identity_key(row) not in selected_keys
    ]
    return selected_rows, overflow_rows


def _with_budget_bucket(row: Mapping[str, Any], *, scope: str) -> dict[str, Any]:
    result = dict(row)
    bucket = _budget_bucket(result)
    result["budget_bucket"] = bucket
    result["budget_bucket_cn"] = _budget_bucket_cn(bucket)
    result["budget_scope"] = scope
    result["budget_reason_cn"] = _budget_reason_cn(result, bucket=bucket)
    return result


def _with_budget_selection(row: Mapping[str, Any], *, selected: bool, rank: int, scope: str) -> dict[str, Any]:
    result = _with_budget_bucket(row, scope=scope)
    result["budget_selected"] = bool(selected)
    result["budget_rank"] = rank if selected else ""
    if selected:
        result["budget_decision_cn"] = f"{_budget_bucket_cn(_clean_text(result.get('budget_bucket')))}：预算内保留"
    else:
        result["budget_decision_cn"] = f"{_budget_bucket_cn(_clean_text(result.get('budget_bucket')))}：预算不足，进入 overflow"
    return result


def _budget_bucket(row: Mapping[str, Any]) -> str:
    features = row.get("features") if isinstance(row.get("features"), Mapping) else {}
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    subtype = _clean_text(row.get("region_subtype"))
    page_zone = _clean_text(features.get("page_zone"))
    candidate_source = _clean_text(features.get("candidate_source"))
    planner_source = _clean_text(row.get("planner_source"))
    if bool(row.get("ocr_feedback_positive_shape_match")):
        return "ocr_positive_feedback"
    if candidate_source == "colored_annotation_cluster" or subtype == "colored_text_or_callout":
        return "colored_annotation"
    if planner_source == "medium_cv_text_region_detector.large_region_splitter" or "split_from_too_large_region" in flags:
        return "large_region_split"
    if page_zone == "right_notes" or subtype == "right_side_notes_text":
        return "right_notes"
    if page_zone == "main_drawing":
        return "main_drawing"
    return "fallback_high_priority"


def _budget_bucket_cn(bucket: str) -> str:
    return {
        "ocr_positive_feedback": "OCR 正反馈相似区域",
        "colored_annotation": "彩色图签/材料表候选",
        "right_notes": "右侧说明/做法文字候选",
        "large_region_split": "大块 CAD 二次拆分小字",
        "main_drawing": "主图文字/引线标注",
        "fallback_high_priority": "综合高优先级兜底",
    }.get(_clean_text(bucket), "未分类候选")


def _budget_reason_cn(row: Mapping[str, Any], *, bucket: str) -> str:
    if bucket == "ocr_positive_feedback":
        return "OCR 反馈证明类似形态曾产生高质量有效文字，本轮优先保留。"
    if bucket == "colored_annotation":
        return "彩色图签、材料表或说明块候选，拼版 CAD 页中可能包含材料名称、图例和做法说明。"
    if bucket == "right_notes":
        return "位于右侧说明区或说明文字块，可能包含材料做法、节点说明或图例信息。"
    if bucket == "large_region_split":
        return "来自大块 CAD 区域二次拆分，保留少量小字召回，避免大块误拒绝。"
    if bucket == "main_drawing":
        return "来自主图区域，保留房间、节点、引线和局部材料代号证据。"
    return "综合优先级较高，作为预算剩余时的兜底 OCR 候选。"


def _region_identity_key(row: Mapping[str, Any]) -> str:
    bbox = row.get("bbox_ratio") or row.get("bbox_pixel") or []
    return "|".join(
        [
            _clean_text(row.get("region_id")),
            _clean_text(row.get("source_file")),
            str(_int(row.get("page"))),
            json.dumps(bbox, ensure_ascii=False),
            _clean_text(row.get("planner_source")),
        ]
    )


def _region_priority_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -_float(item.get("priority"), 0.0),
        -_float(item.get("confidence"), 0.0),
        _int(item.get("page")),
        _bbox_top(item.get("bbox_ratio")),
        _bbox_left(item.get("bbox_ratio")),
    )


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


def _colored_annotation_candidate_boxes(
    *,
    cv2: Any,
    image: np.ndarray,
    page_width: int,
    page_height: int,
) -> list[dict[str, Any]]:
    """Find dense colored annotation/title-block clusters on CAD mosaic pages."""
    if image.size == 0:
        return []
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    saturated = (saturation > 35) & (value > 80)
    # Keep common CAD annotation/title-block colors and skip cyan frame lines.
    target_hue = (
        ((hue >= 18) & (hue <= 38))
        | ((hue >= 45) & (hue <= 82))
        | (hue <= 8)
        | ((hue >= 140) & (hue <= 172))
    )
    mask = (saturated & target_hue).astype(np.uint8) * 255
    if cv2.countNonZero(mask) <= 0:
        return []

    grouped = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (14, 8)), iterations=1)
    contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[dict[str, Any]] = []
    page_area = max(1.0, float(page_width * page_height))
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = max(0, int(width) * int(height))
        if area < 80 or area > page_area * 0.018:
            continue
        if width < 8 or height < 6:
            continue
        color_density = float(cv2.countNonZero(mask[y : y + height, x : x + width])) / max(1.0, float(area))
        if color_density < 0.035:
            continue
        aspect = float(width) / max(1.0, float(height))
        if aspect < 0.12 or aspect > 18.0:
            continue
        boxes.append(
            _expand_box(
                {"x": x, "y": y, "w": width, "h": height, "source": "colored_annotation_cluster"},
                pad_x=3,
                pad_y=3,
                page_width=page_width,
                page_height=page_height,
            )
        )
    boxes = _dedupe_candidate_boxes(boxes, iou_threshold=0.62)
    boxes.sort(key=lambda item: (int(item["y"]), int(item["x"]), -(int(item["w"]) * int(item["h"]))))
    return boxes[:120]


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
    candidate_source = _clean_text(box.get("source"))
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
        "candidate_source": candidate_source,
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
    candidate_source = _clean_text(features.get("candidate_source"))
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
    if candidate_source == "colored_annotation_cluster":
        score += 0.09
        if 0.00018 <= area_ratio <= 0.006 and color_ratio >= 0.025:
            score += 0.05
    base_score = max(0.0, min(1.0, score))
    feedback_features = dict(features)
    feedback_features["quality_flags"] = list(flags)
    feedback_features["region_subtype"] = _region_subtype(features, flags=flags)
    feedback = _ocr_feedback_adjustment(feedback_features, ocr_quality_feedback_profile)
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
    negative_profile_flags = _profile_categorical_values(profile.get("negative_feature_profile"), "quality_flags")
    if negative_profile_flags and not (negative_profile_flags & _feature_string_values(features.get("quality_flags"))):
        negative_similarity = min(negative_similarity, max(0.0, threshold - 0.01))
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
        candidates = _feature_string_values(features.get(feature))
        if not candidates or not isinstance(options, Sequence):
            continue
        matched = 0.0
        for option in options:
            if not isinstance(option, Mapping):
                continue
            if _clean_text(option.get("value")) in candidates:
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


def _profile_categorical_values(profile: Any, feature: str) -> set[str]:
    if not isinstance(profile, Mapping):
        return set()
    categorical_values = profile.get("categorical_values") if isinstance(profile.get("categorical_values"), Mapping) else {}
    options = categorical_values.get(feature) if isinstance(categorical_values, Mapping) else []
    result: set[str] = set()
    if not isinstance(options, Sequence):
        return result
    for option in options:
        if isinstance(option, Mapping):
            value = _clean_text(option.get("value"))
            if value:
                result.add(value)
    return result


def _feature_string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence):
        values = [str(item) for item in value]
    else:
        values = [str(value or "")]
    return {item.strip() for item in values if item.strip()}


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


def _overflow_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    overflow_reason: str,
    overflow_reason_cn: str,
    overflow_source_bucket: str,
    parent_bbox_pixel: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        item = _with_budget_selection(row, selected=False, rank=0, scope=_clean_text(row.get("budget_scope")) or "overflow")
        item["overflow"] = True
        item["overflow_reason"] = overflow_reason
        item["overflow_reason_cn"] = overflow_reason_cn
        item["overflow_source_bucket"] = overflow_source_bucket
        item["overflow_original_selected"] = bool(item.get("selected"))
        item["selected"] = False
        if parent_bbox_pixel is not None and not item.get("parent_bbox_pixel"):
            item["parent_bbox_pixel"] = list(parent_bbox_pixel)
        result.append(item)
    return result


def _with_rejected_layer(row: Mapping[str, Any], *, min_score: float) -> dict[str, Any]:
    result = dict(row)
    if result.get("selected") and not result.get("overflow"):
        return result
    layer, reason_cn = _rejected_layer(result, min_score=min_score)
    result["rejected_layer"] = layer
    result["rejected_layer_cn"] = _rejected_layer_cn(layer)
    result["rejected_layer_reason_cn"] = reason_cn
    return result


def _with_candidate_explanation(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    layer = _clean_text(result.get("rejected_layer"))
    if result.get("selected") and not result.get("overflow"):
        result["candidate_decision_cn"] = "入选：预算内主路径 OCR"
        result["candidate_reason_cn"] = _selected_candidate_reason_cn(result)
        result["candidate_risk_cn"] = _selected_candidate_risk_cn(result)
        result["next_action_cn"] = "直接 OCR"
    elif result.get("overflow"):
        result["candidate_decision_cn"] = "未入选主路径：预算截断，进入 overflow"
        result["candidate_reason_cn"] = _overflow_candidate_reason_cn(result)
        result["candidate_risk_cn"] = _overflow_candidate_risk_cn(result)
        result["next_action_cn"] = "保留为 overflow 审阅候选，并按兜底预算抽样 OCR"
    elif layer == "recoverable_text_like":
        result["candidate_decision_cn"] = "未入选：规则拒绝，但可能误拒绝"
        result["candidate_reason_cn"] = _clean_text(result.get("rejected_layer_reason_cn")) or "具备一定文字特征，但未达到本轮主路径入选要求。"
        result["candidate_risk_cn"] = "存在漏掉有效文字的风险，但也可能只是轴号、尺寸或碎片字符，适合少量兜底 OCR 复核。"
        result["next_action_cn"] = "少量兜底 OCR 复核；若多次低质量则继续压低优先级"
    elif layer == "too_large_needs_split":
        result["candidate_decision_cn"] = "未入选：区域过大，不能直接 OCR"
        result["candidate_reason_cn"] = _clean_text(result.get("rejected_layer_reason_cn")) or "区域过大，直接 OCR 容易混入大量线条和噪声。"
        result["candidate_risk_cn"] = "整块直接 OCR 成本高且噪声大，但内部可能有小字簇，需要继续拆分。"
        result["next_action_cn"] = "继续二次拆分或抽样复核"
    elif layer == "low_priority_text_like":
        result["candidate_decision_cn"] = "未入选：低优先级文字候选"
        result["candidate_reason_cn"] = _clean_text(result.get("rejected_layer_reason_cn")) or "像文字但优先级不足，本轮不进入 OCR。"
        result["candidate_risk_cn"] = "可能包含低价值轴号、尺寸或图签碎片，也可能漏掉少量说明文字。"
        result["next_action_cn"] = "保留记录，等待后续反馈或预算充足时复核"
    else:
        result["candidate_decision_cn"] = "未入选：规则拒绝，暂定噪声"
        result["candidate_reason_cn"] = _clean_text(result.get("rejected_layer_reason_cn")) or "不满足当前文字候选条件。"
        result["candidate_risk_cn"] = "误杀风险相对低，可作为噪声/负样本参考。"
        result["next_action_cn"] = "暂不 OCR"
    result["candidate_signal_cn"] = _candidate_signal_cn(result)
    return result


def _selected_candidate_reason_cn(row: Mapping[str, Any]) -> str:
    reason = _clean_text(row.get("budget_reason_cn"))
    bucket_cn = _clean_text(row.get("budget_bucket_cn"))
    if reason:
        return reason
    if bucket_cn:
        return f"属于{bucket_cn}，并在当前预算内排序靠前。"
    return "综合优先级达到本轮 OCR 预算要求。"


def _overflow_candidate_reason_cn(row: Mapping[str, Any]) -> str:
    reason_parts = []
    budget_reason = _clean_text(row.get("budget_reason_cn"))
    overflow_reason = _clean_text(row.get("overflow_reason_cn"))
    if budget_reason:
        reason_parts.append(budget_reason)
    if overflow_reason:
        reason_parts.append(overflow_reason)
    if not reason_parts:
        reason_parts.append("该区域已被发现，但本轮 OCR 预算不足。")
    return "；".join(reason_parts)


def _selected_candidate_risk_cn(row: Mapping[str, Any]) -> str:
    bucket = _clean_text(row.get("budget_bucket"))
    if bucket == "ocr_positive_feedback":
        return "历史 OCR 正反馈提升了可信度，但仍需 OCR 质量评分和后续语义分类确认是否为材料信息。"
    if bucket == "colored_annotation":
        return "可能是材料表/图例，也可能是图签、设计单位或标题信息，需 OCR 后再筛材料。"
    if bucket == "large_region_split":
        return "来自大块拆分，可能召回小字，也可能混入线条和轴号噪声。"
    return "可能包含有效文字，也可能只是轴号、尺寸或普通图纸说明。"


def _overflow_candidate_risk_cn(row: Mapping[str, Any]) -> str:
    bucket = _clean_text(row.get("budget_bucket"))
    if bucket in {"colored_annotation", "ocr_positive_feedback"}:
        return "虽然未进主路径，但存在材料表、图例或说明文字漏召回风险，适合兜底 OCR 抽样。"
    if bucket == "large_region_split":
        return "来自大块二次拆分，可能有小字簇，但噪声风险较高，适合少量抽样。"
    return "本轮预算未覆盖，价值不确定，适合保留审阅或低比例抽样。"


def _candidate_signal_cn(row: Mapping[str, Any]) -> str:
    features = row.get("features") if isinstance(row.get("features"), Mapping) else {}
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    signals: list[str] = []
    bucket_cn = _clean_text(row.get("budget_bucket_cn"))
    if bucket_cn:
        signals.append(f"预算分类：{bucket_cn}")
    layer_cn = _clean_text(row.get("rejected_layer_cn"))
    if layer_cn:
        signals.append(f"拒绝层级：{layer_cn}")
    overflow_reason = _clean_text(row.get("overflow_reason_cn"))
    if overflow_reason:
        signals.append(f"预算截断：{overflow_reason}")
    subtype_cn = _region_subtype_cn(_clean_text(row.get("region_subtype")))
    if subtype_cn:
        signals.append(f"区域类型：{subtype_cn}")
    page_zone_cn = _page_zone_cn(_clean_text(features.get("page_zone")))
    if page_zone_cn:
        signals.append(f"页面位置：{page_zone_cn}")
    if _clean_text(features.get("candidate_source")) == "colored_annotation_cluster":
        signals.append("候选来源：彩色图签/说明块聚类")
    if row.get("ocr_feedback_positive_shape_match") or "ocr_feedback_positive_shape_match" in flags:
        signals.append("OCR 反馈：匹配有效文字正样本形态")
    if row.get("ocr_feedback_negative_shape_match") or "ocr_feedback_negative_shape_match" in flags:
        signals.append("OCR 反馈：匹配低价值负样本形态")
    width = _int(features.get("width_px"))
    height = _int(features.get("height_px"))
    if width > 0 and height > 0:
        signals.append(f"尺寸：{width}x{height}px")
    density = _float(features.get("text_density"), -1.0)
    if density >= 0:
        signals.append(f"文字密度：{density:.4f}")
    component_count = _int(features.get("component_count"))
    if component_count > 0:
        signals.append(f"小组件数：{component_count}")
    important_flags = [
        _quality_flag_cn(flag)
        for flag in [
            "too_large_for_text_region",
            "split_from_too_large_region",
            "line_dominant",
            "single_component_stroke",
            "colored_region_without_text_fragments",
            "score_below_threshold",
        ]
        if flag in flags
    ]
    if important_flags:
        signals.append("关键标记：" + "、".join(important_flags))
    return "；".join(_unique_texts(signals)) or "暂无明显信号"


def _region_subtype_cn(subtype: str) -> str:
    return {
        "colored_text_or_callout": "彩色文字/引线标注",
        "right_side_notes_text": "右侧说明文字",
        "text_line": "文字行",
        "text_block": "文字块",
        "line_or_marker_noise": "线条/标记噪声",
        "noise_or_fill": "填充/图框噪声",
        "split_noise": "大块拆分后的疑似噪声",
    }.get(_clean_text(subtype), _clean_text(subtype))


def _page_zone_cn(zone: str) -> str:
    return {
        "right_notes": "右侧说明区",
        "bottom_title": "底部标题栏",
        "top_header": "顶部页眉区",
        "main_drawing": "主图区域",
    }.get(_clean_text(zone), _clean_text(zone))


def _quality_flag_cn(flag: str) -> str:
    return {
        "too_large_for_text_region": "区域过大",
        "split_from_too_large_region": "来自大块二次拆分",
        "line_dominant": "线条占比高",
        "single_component_stroke": "单组件笔画/线段",
        "colored_region_without_text_fragments": "彩色区域但缺少文字碎片",
        "score_below_threshold": "分数低于入选阈值",
    }.get(_clean_text(flag), _clean_text(flag))


def _unique_texts(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        result.append(cleaned)
        seen.add(cleaned)
    return result


def _rejected_layer(row: Mapping[str, Any], *, min_score: float) -> tuple[str, str]:
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    features = row.get("features") if isinstance(row.get("features"), Mapping) else {}
    priority = _float(row.get("priority"), 0.0)
    component_count = _int(features.get("component_count"))
    density = _float(features.get("text_density"))
    line_ratio = _float(features.get("line_ratio"))
    subtype = _clean_text(row.get("region_subtype"))
    if "too_large_for_text_region" in flags:
        return "too_large_needs_split", "区域过大，不能直接 OCR，需要继续拆分或抽样复核"
    if row.get("overflow"):
        return "low_priority_text_like", _clean_text(row.get("overflow_reason_cn")) or "候选被预算截断，建议进入 overflow 审阅清单"
    if row.get("ocr_feedback_positive_shape_match") and priority >= max(0.0, min_score - 0.16):
        return "recoverable_text_like", "匹配 OCR 正样本形态，且分数接近阈值，可能是被误拒绝的文字"
    hard_noise_flags = {
        "no_text_like_components",
        "single_component_stroke",
        "colored_region_without_text_fragments",
        "line_dominant",
        "aspect_ratio_unlikely_text",
    }
    if flags & hard_noise_flags and component_count <= 1:
        return "hard_noise", "单组件、线条或彩色标记特征明显，倾向真实噪声"
    if subtype in {"line_or_marker_noise", "noise_or_fill"} and priority < max(0.0, min_score - 0.08):
        return "hard_noise", "区域类型倾向线条/填充噪声，且优先级明显低于阈值"
    if priority >= max(0.0, min_score - 0.08) or (component_count >= 3 and density >= 0.01 and line_ratio < 0.82):
        return "recoverable_text_like", "具备一定文字碎片或接近入选阈值，建议低成本 OCR 复核"
    if "score_below_threshold" in flags:
        return "low_priority_text_like", "像文字但分数低于本轮入选阈值，可作为低优先级候选保留"
    return "hard_noise", "不满足文字候选条件，当前倾向噪声"


def _rejected_layer_cn(layer: str) -> str:
    return {
        "hard_noise": "明确噪声",
        "recoverable_text_like": "可能被误拒绝的文字",
        "too_large_needs_split": "需要继续拆分的大块区域",
        "low_priority_text_like": "低优先级文字候选",
    }.get(layer, layer)


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


def _layer_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        layer = _clean_text(row.get("rejected_layer")) or "unclassified"
        counts[layer] = counts.get(layer, 0) + 1
    return counts


def _budget_bucket_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        bucket = _clean_text(row.get("budget_bucket")) or _budget_bucket(row)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _write_report(
    *,
    directory: Path,
    status: str,
    page_rows: Sequence[Mapping[str, Any]],
    regions: Sequence[Mapping[str, Any]],
    rejected_regions: Sequence[Mapping[str, Any]],
    overflow_regions: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    rejected_layer_counts = _layer_counts(rejected_regions)
    overflow_layer_counts = _layer_counts(overflow_regions)
    summary = {
        "text_region_discovery_status": status,
        "page_count": len(page_rows),
        "selected_region_count": len(regions),
        "rejected_region_count": len(rejected_regions),
        "overflow_region_count": len(overflow_regions),
        "large_region_split_hit_cap": any(row.get("overflow_reason") == "large_region_split_selected_cap" for row in overflow_regions),
        "large_region_split_rejected_hit_cap": any(row.get("overflow_reason") == "large_region_split_rejected_cap" for row in overflow_regions),
        "page_region_hit_cap": any(row.get("overflow_reason") == "page_region_cap" for row in overflow_regions),
        "global_region_hit_cap": any(row.get("overflow_reason") == "global_region_cap" for row in overflow_regions),
        "rejected_layer_counts": rejected_layer_counts,
        "overflow_layer_counts": overflow_layer_counts,
        "budget_diversity_enabled": True,
        "selected_budget_bucket_counts": _budget_bucket_counts(regions),
        "overflow_budget_bucket_counts": _budget_bucket_counts(overflow_regions),
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
        overflow_regions=overflow_regions,
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
        "overflow_regions": list(overflow_regions),
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
    overflow_regions: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    payloads = [
        ("text_region_summary_json", "text_region_summary.json", dict(summary)),
        ("text_region_plan_json", "text_region_plan.json", {"schema_version": SCHEMA_VERSION, "regions": list(regions)}),
        ("text_region_rejected_json", "text_region_rejected.json", list(rejected_regions)),
        ("text_region_overflow_json", "text_region_overflow.json", list(overflow_regions)),
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
    overflow_csv = directory / "text_region_overflow.csv"
    _write_region_csv(overflow_csv, overflow_regions)
    outputs["text_region_overflow_csv"] = str(overflow_csv.resolve())
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
        "candidate_source",
        "planner_source",
        "candidate_decision_cn",
        "candidate_reason_cn",
        "candidate_signal_cn",
        "candidate_risk_cn",
        "next_action_cn",
        "budget_bucket",
        "budget_bucket_cn",
        "budget_reason_cn",
        "budget_selected",
        "budget_rank",
        "budget_decision_cn",
        "parent_bbox_pixel",
        "overflow",
        "overflow_reason",
        "overflow_reason_cn",
        "overflow_source_bucket",
        "rejected_layer",
        "rejected_layer_cn",
        "rejected_layer_reason_cn",
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
                    "candidate_source": features.get("candidate_source", ""),
                    "planner_source": row.get("planner_source", ""),
                    "candidate_decision_cn": row.get("candidate_decision_cn", ""),
                    "candidate_reason_cn": row.get("candidate_reason_cn", ""),
                    "candidate_signal_cn": row.get("candidate_signal_cn", ""),
                    "candidate_risk_cn": row.get("candidate_risk_cn", ""),
                    "next_action_cn": row.get("next_action_cn", ""),
                    "budget_bucket": row.get("budget_bucket", ""),
                    "budget_bucket_cn": row.get("budget_bucket_cn", ""),
                    "budget_reason_cn": row.get("budget_reason_cn", ""),
                    "budget_selected": row.get("budget_selected", ""),
                    "budget_rank": row.get("budget_rank", ""),
                    "budget_decision_cn": row.get("budget_decision_cn", ""),
                    "parent_bbox_pixel": json.dumps(row.get("parent_bbox_pixel") or [], ensure_ascii=False),
                    "overflow": row.get("overflow", ""),
                    "overflow_reason": row.get("overflow_reason", ""),
                    "overflow_reason_cn": row.get("overflow_reason_cn", ""),
                    "overflow_source_bucket": row.get("overflow_source_bucket", ""),
                    "rejected_layer": row.get("rejected_layer", ""),
                    "rejected_layer_cn": row.get("rejected_layer_cn", ""),
                    "rejected_layer_reason_cn": row.get("rejected_layer_reason_cn", ""),
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
