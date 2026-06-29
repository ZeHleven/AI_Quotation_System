from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from PIL import Image


CAD_VIEW_TILE_TYPE = "cad_view"


@dataclass(frozen=True)
class CadViewFrame:
    x1: int
    y1: int
    x2: int
    y2: int
    ink_ratio: float
    border_coverage: float

    @property
    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    @property
    def area(self) -> int:
        return self.width * self.height


def build_cad_view_frame_report(
    *,
    parse_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    view_dir: str | Path,
    max_views_per_page: int = 48,
) -> dict[str, Any]:
    directory = Path(view_dir)
    directory.mkdir(parents=True, exist_ok=True)
    page_by_key = {
        (str(row.get("source_file") or ""), int(row.get("page") or 0)): row
        for row in parse_report.get("page_rows") or []
    }

    view_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    for render in render_report.get("render_rows") or []:
        source_file = str(render.get("source_file") or "")
        page_no = int(_float(render.get("page"), 0))
        image_path = Path(str(render.get("png_path") or ""))
        page = page_by_key.get((source_file, page_no), {})
        if not image_path.exists() or not image_path.is_file():
            status_rows.append(
                {
                    "status": "skipped",
                    "reason": "rendered_page_image_missing",
                    "source_file": source_file,
                    "page": page_no,
                }
            )
            continue
        try:
            page_view_rows = _detect_and_crop_page_views(
                image_path=image_path,
                source_file=source_file,
                page_no=page_no,
                page_width_pt=_float(page.get("width_pt"), 0),
                page_height_pt=_float(page.get("height_pt"), 0),
                output_dir=directory,
                max_views=max_views_per_page,
            )
        except Exception as exc:
            status_rows.append(
                {
                    "status": "error",
                    "reason": str(exc)[:300],
                    "source_file": source_file,
                    "page": page_no,
                }
            )
            continue
        view_rows.extend(page_view_rows)
        status_rows.append(
            {
                "status": "success",
                "reason": f"detected_{len(page_view_rows)}_cad_views",
                "source_file": source_file,
                "page": page_no,
            }
        )

    return {
        "ok": True,
        "phase": "PDF-4b-cad-view-frame-detection",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "cad_view_frame_count": len(view_rows),
            "cad_view_page_success_count": sum(1 for row in status_rows if row.get("status") == "success"),
            "cad_view_page_error_count": sum(1 for row in status_rows if row.get("status") == "error"),
            "cad_view_page_skipped_count": sum(1 for row in status_rows if row.get("status") == "skipped"),
        },
        "view_rows": view_rows,
        "status_rows": status_rows,
    }


def augment_tile_report_with_cad_views(
    tile_report: Mapping[str, Any],
    cad_view_report: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(tile_report)
    summary = dict(result.get("summary") or {})
    tile_rows = list(result.get("tile_rows") or [])
    cad_view_rows = list(cad_view_report.get("view_rows") or [])
    if cad_view_rows:
        tile_rows.extend(cad_view_rows)
    summary["cad_view_frame_count"] = len(cad_view_rows)
    summary["tile_count"] = len(tile_rows)
    result["summary"] = summary
    result["tile_rows"] = tile_rows
    return result


def _detect_and_crop_page_views(
    *,
    image_path: Path,
    source_file: str,
    page_no: int,
    page_width_pt: float,
    page_height_pt: float,
    output_dir: Path,
    max_views: int,
) -> list[dict[str, Any]]:
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    frames = detect_cad_view_frames(image, max_frames=max_views)
    rows: list[dict[str, Any]] = []
    for index, frame in enumerate(frames, start=1):
        crop_box = _expand_box((frame.x1, frame.y1, frame.x2, frame.y2), width=width, height=height)
        crop = image.crop(crop_box)
        tile_id = f"p{page_no:03d}_view{index:03d}"
        crop_path = output_dir / f"{_safe_stem(source_file)}_{tile_id}.png"
        crop.save(crop_path)
        bbox_pdf = _pixel_box_to_pdf_box(
            crop_box,
            image_width=width,
            image_height=height,
            page_width_pt=page_width_pt,
            page_height_pt=page_height_pt,
        )
        rows.append(
            {
                "tile_id": tile_id,
                "source_file": source_file,
                "page": page_no,
                "tile_type": CAD_VIEW_TILE_TYPE,
                "bbox_pdf": bbox_pdf,
                "bbox_pixel": list(crop_box),
                "image_path": str(crop_path.resolve()),
                "status": "cad_view_frame_created",
                "priority": 250,
                "view_frame_ink_ratio": round(frame.ink_ratio, 4),
                "view_frame_border_coverage": round(frame.border_coverage, 4),
            }
        )
    return rows


def detect_cad_view_frames(image: Image.Image, *, max_frames: int = 48) -> list[CadViewFrame]:
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width <= 0 or height <= 0:
        return []
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    cyan = (red < 120) & (green > 130) & (blue > 130) & (green > red + 30) & (blue > red + 30)
    ink = (red < 245) | (green < 245) | (blue < 245)

    min_width = max(180, int(width * 0.055))
    min_height = max(110, int(height * 0.03))
    max_width = max(min_width, int(width * 0.65))
    max_height = max(min_height, int(height * 0.5))
    line_merge_tolerance = max(8, int(min(width, height) * 0.007))

    y_lines = _merge_weighted_coords(
        _horizontal_line_candidates(cyan, min_run_length=max(120, int(min_width * 0.6))),
        tolerance=line_merge_tolerance,
    )
    x_lines = _merge_weighted_coords(
        _vertical_line_candidates(cyan, min_run_length=max(90, int(min_height * 0.65))),
        tolerance=line_merge_tolerance,
    )
    if len(x_lines) < 2 or len(y_lines) < 2:
        return []

    candidates: list[CadViewFrame] = []
    for y_index, y1 in enumerate(y_lines):
        for y2 in y_lines[y_index + 1 :]:
            frame_height = y2 - y1
            if frame_height < min_height or frame_height > max_height:
                continue
            for x_index, x1 in enumerate(x_lines):
                for x2 in x_lines[x_index + 1 :]:
                    frame_width = x2 - x1
                    if frame_width < min_width or frame_width > max_width:
                        continue
                    border_values = (
                        _horizontal_coverage(cyan, y1, x1, x2),
                        _horizontal_coverage(cyan, y2, x1, x2),
                        _vertical_coverage(cyan, x1, y1, y2),
                        _vertical_coverage(cyan, x2, y1, y2),
                    )
                    if min(border_values) < 0.55:
                        continue
                    if _has_internal_vertical_line(cyan, x_lines, x1, y1, x2, y2):
                        continue
                    if _has_internal_horizontal_line(cyan, y_lines, x1, y1, x2, y2):
                        continue
                    ratio = _ink_ratio(ink, x1, y1, x2, y2)
                    if ratio < 0.006:
                        continue
                    candidates.append(
                        CadViewFrame(
                            x1=x1,
                            y1=y1,
                            x2=x2,
                            y2=y2,
                            ink_ratio=ratio,
                            border_coverage=sum(border_values) / len(border_values),
                        )
                    )

    candidates.sort(key=lambda frame: (frame.y1, frame.x1, -frame.area))
    return _dedupe_overlapping_frames(candidates)[:max_frames]


def _horizontal_line_candidates(mask: np.ndarray, *, min_run_length: int) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for y in range(mask.shape[0]):
        lengths = [x2 - x1 + 1 for x1, x2 in _runs(mask[y]) if x2 - x1 + 1 >= min_run_length]
        if lengths:
            result.append((y, max(lengths)))
    return result


def _vertical_line_candidates(mask: np.ndarray, *, min_run_length: int) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for x in range(mask.shape[1]):
        lengths = [y2 - y1 + 1 for y1, y2 in _runs(mask[:, x]) if y2 - y1 + 1 >= min_run_length]
        if lengths:
            result.append((x, max(lengths)))
    return result


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    indexes = np.where(values)[0]
    if indexes.size == 0:
        return []
    result: list[tuple[int, int]] = []
    start = previous = int(indexes[0])
    for raw_index in indexes[1:]:
        index = int(raw_index)
        if index <= previous + 1:
            previous = index
            continue
        result.append((start, previous))
        start = previous = index
    result.append((start, previous))
    return result


def _merge_weighted_coords(coords: list[tuple[int, int]], *, tolerance: int) -> list[int]:
    groups: list[list[tuple[int, int]]] = []
    for coord, weight in sorted(coords):
        if not groups or coord - groups[-1][-1][0] > tolerance:
            groups.append([(coord, weight)])
        else:
            groups[-1].append((coord, weight))
    return [max(group, key=lambda item: item[1])[0] for group in groups]


def _horizontal_coverage(mask: np.ndarray, y: int, x1: int, x2: int, *, band: int = 4) -> float:
    height = mask.shape[0]
    top = max(0, y - band)
    bottom = min(height, y + band + 1)
    width = max(1, x2 - x1 + 1)
    return float(mask[top:bottom, x1 : x2 + 1].any(axis=0).sum()) / width


def _vertical_coverage(mask: np.ndarray, x: int, y1: int, y2: int, *, band: int = 4) -> float:
    width = mask.shape[1]
    left = max(0, x - band)
    right = min(width, x + band + 1)
    height = max(1, y2 - y1 + 1)
    return float(mask[y1 : y2 + 1, left:right].any(axis=1).sum()) / height


def _has_internal_vertical_line(mask: np.ndarray, x_lines: list[int], x1: int, y1: int, x2: int, y2: int) -> bool:
    for x in x_lines:
        if x1 + 30 < x < x2 - 30 and _vertical_coverage(mask, x, y1, y2) > 0.65:
            return True
    return False


def _has_internal_horizontal_line(mask: np.ndarray, y_lines: list[int], x1: int, y1: int, x2: int, y2: int) -> bool:
    for y in y_lines:
        if y1 + 30 < y < y2 - 30 and _horizontal_coverage(mask, y, x1, x2) > 0.65:
            return True
    return False


def _ink_ratio(mask: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> float:
    area = max(1, (x2 - x1 + 1) * (y2 - y1 + 1))
    return float(mask[y1 : y2 + 1, x1 : x2 + 1].sum()) / area


def _dedupe_overlapping_frames(frames: list[CadViewFrame]) -> list[CadViewFrame]:
    accepted: list[CadViewFrame] = []
    for frame in frames:
        if all(_intersection_over_union(frame, existing) < 0.25 for existing in accepted):
            accepted.append(frame)
    return accepted


def _intersection_over_union(left: CadViewFrame, right: CadViewFrame) -> float:
    x1 = max(left.x1, right.x1)
    y1 = max(left.y1, right.y1)
    x2 = min(left.x2, right.x2)
    y2 = min(left.y2, right.y2)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if intersection <= 0:
        return 0.0
    union = left.area + right.area - intersection
    return float(intersection) / max(1, union)


def _expand_box(box: tuple[int, int, int, int], *, width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    margin = max(8, int(min(width, height) * 0.004))
    return (
        max(0, x1 - margin),
        max(0, y1 - margin),
        min(width, x2 + margin),
        min(height, y2 + margin),
    )


def _pixel_box_to_pdf_box(
    box: tuple[int, int, int, int],
    *,
    image_width: int,
    image_height: int,
    page_width_pt: float,
    page_height_pt: float,
) -> list[float]:
    if image_width <= 0 or image_height <= 0 or page_width_pt <= 0 or page_height_pt <= 0:
        return list(box)
    x1, y1, x2, y2 = box
    return [
        round(x1 / image_width * page_width_pt, 3),
        round(y1 / image_height * page_height_pt, 3),
        round(x2 / image_width * page_width_pt, 3),
        round(y2 / image_height * page_height_pt, 3),
    ]


def _safe_stem(value: str) -> str:
    raw = Path(value).stem or "pdf_page"
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    return cleaned[:80] or "pdf_page"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
