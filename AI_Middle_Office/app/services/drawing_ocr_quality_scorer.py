from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image

from app.services.drawing_agent_ocr import (
    MATERIAL_CODE_RE,
    OCR_ENGINE_PADDLE,
    OcrRunner,
    _load_local_ocr_runner,
    normalize_ocr_result,
)


PHASE = "BIZ-2x-highres-ocr-quality-score"
SCHEMA_VERSION = "drawing_ocr_quality_score_v1"
OCR_FEEDBACK_SCHEMA_VERSION = "drawing_ocr_quality_feedback_profile_v1"

OCR_FEEDBACK_NUMERIC_FEATURES = (
    "width_px",
    "height_px",
    "area_ratio",
    "aspect_ratio",
    "foreground_density",
    "text_density",
    "line_ratio",
    "component_count",
    "color_ratio",
)
OCR_FEEDBACK_CATEGORICAL_FEATURES = (
    "page_zone",
    "split_source",
    "planner_source",
    "region_subtype",
    "quality_flags",
)

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
ALNUM_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
DIMENSION_RE = re.compile(r"(?<!\d)(?:\d{2,5}\s*(?:x|X|\u00d7|\*)\s*\d{2,5}|\d{3,5}\s*(?:mm|MM|\u6beb\u7c73|m|M)?)(?!\d)")
MATERIAL_KEYWORD_RE = re.compile(
    r"(\u6750\u6599|\u6750\u8d28|\u505a\u6cd5|\u9970\u9762|\u9762\u5c42|\u57fa\u5c42|"
    r"\u5899\u9762|\u5730\u9762|\u9876\u9762|\u5929\u82b1|\u540a\u9876|\u74f7\u7816|"
    r"\u5730\u7816|\u5899\u7816|\u77f3\u6750|\u6728\u9970\u9762|\u6728\u4f5c|"
    r"\u4e73\u80f6\u6f06|\u6d82\u6599|\u6cb9\u6f06|\u73bb\u7483|\u4e0d\u9508\u94a2|"
    r"\u5730\u6bef|\u8e22\u811a|\u95e8|\u7a97|\u706f|\u706f\u69fd|\u7a97\u5e18\u76d2|"
    r"\u62c6\u9664|\u65b0\u5efa|\u5b89\u88c5|\u5de5\u7a0b|\u65bd\u5de5|\u56fe\u540d|\u9879\u76ee)",
    re.IGNORECASE,
)


def build_highres_ocr_quality_report(
    *,
    crop_manifest: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
    ocr_engine: str = OCR_ENGINE_PADDLE,
    ocr_runner: OcrRunner | None = None,
    max_crops: int = 20,
    max_image_side: int = 1800,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    crops = _selected_crops(crop_manifest, max_crops=max_crops)

    runner = ocr_runner
    unavailable_reason = ""
    if runner is None:
        runner, unavailable_reason = _load_local_ocr_runner(ocr_engine)

    ocr_rows: list[dict[str, Any]] = []
    crop_scores: list[dict[str, Any]] = []
    completed_crops = 0
    status = "completed"
    if runner is None:
        status = "unavailable"
        warnings.append(
            {
                "code": "OCR_QUALITY_OCR_UNAVAILABLE",
                "message": unavailable_reason or "local OCR runner unavailable",
                "engine": ocr_engine,
            }
        )
    else:
        for crop in crops:
            image_path = Path(str(crop.get("image_path") or ""))
            if not image_path.exists() or not image_path.is_file():
                errors.append(
                    {
                        "code": "OCR_QUALITY_IMAGE_MISSING",
                        "message": "high-resolution crop image is missing",
                        "crop_id": crop.get("crop_id"),
                        "image_path": str(image_path),
                    }
                )
                continue
            try:
                ocr_image_path = _prepared_ocr_image_path(
                    image_path=image_path,
                    output_dir=directory / "ocr_inputs",
                    max_image_side=max_image_side,
                )
                raw_result = runner(ocr_image_path)
                rows = normalize_ocr_result(raw_result, crop=crop)
                for row in rows:
                    row["ocr_input_image_path"] = str(ocr_image_path.resolve())
                ocr_rows.extend(rows)
                crop_scores.append(score_ocr_crop(crop={**dict(crop), "ocr_input_image_path": str(ocr_image_path.resolve())}, ocr_rows=rows))
                completed_crops += 1
            except Exception as exc:  # noqa: BLE001 - OCR engines vary by install/version
                errors.append(
                    {
                        "code": "OCR_QUALITY_CROP_FAILED",
                        "message": str(exc),
                        "crop_id": crop.get("crop_id"),
                        "image_path": str(image_path),
                    }
                )
                crop_scores.append(score_ocr_crop(crop=crop, ocr_rows=[]))
        if errors and completed_crops:
            status = "completed_with_errors"
        elif errors:
            status = "failed"

    if status == "unavailable":
        crop_scores = [score_ocr_crop(crop=crop, ocr_rows=[]) for crop in crops]

    summary = _quality_summary(
        status=status,
        crop_count=len(crops),
        completed_crops=completed_crops,
        ocr_rows=ocr_rows,
        crop_scores=crop_scores,
        warnings=warnings,
        errors=errors,
        max_crops=max_crops,
        max_image_side=max_image_side,
        unavailable_reason=unavailable_reason,
    )
    feedback_profile = build_ocr_quality_feedback_profile(crop_scores=crop_scores)
    outputs = _write_quality_outputs(
        directory=directory,
        summary=summary,
        crop_scores=crop_scores,
        ocr_rows=ocr_rows,
        warnings=warnings,
        errors=errors,
        feedback_profile=feedback_profile,
    )
    return {
        "ok": status not in {"failed"},
        "phase": PHASE,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "summary": summary,
        "crop_scores": crop_scores,
        "ocr_quality_feedback_profile": feedback_profile,
        "ocr_rows": ocr_rows,
        "warnings": warnings,
        "errors": errors,
        "outputs": outputs,
    }


def score_ocr_crop(*, crop: Mapping[str, Any], ocr_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    texts = [_clean_text(row.get("text")) for row in ocr_rows if _clean_text(row.get("text"))]
    joined = " ".join(texts)
    confidence_values = [_float(row.get("confidence"), 0.0) for row in ocr_rows if _float(row.get("confidence"), 0.0) > 0]
    avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    text_char_count = sum(len(re.sub(r"\s+", "", text)) for text in texts)
    chinese_char_count = len(CHINESE_RE.findall(joined))
    material_code_count = len({match.group(0).upper().replace("_", "-").replace(" ", "") for match in MATERIAL_CODE_RE.finditer(joined)})
    dimension_count = len(DIMENSION_RE.findall(joined))
    material_keyword_count = len(MATERIAL_KEYWORD_RE.findall(joined))
    useful_line_count = sum(1 for text in texts if _is_useful_text_line(text))
    noise_line_count = sum(1 for text in texts if _is_noise_text_line(text))
    low_confidence_line_count = sum(1 for row in ocr_rows if _float(row.get("confidence"), 0.0) and _float(row.get("confidence"), 0.0) < 0.45)
    line_count = len(texts)
    noise_ratio = noise_line_count / max(1, line_count)
    low_confidence_ratio = low_confidence_line_count / max(1, line_count)

    score = (
        min(1.0, useful_line_count / 8.0) * 0.24
        + min(1.0, text_char_count / 120.0) * 0.12
        + min(1.0, chinese_char_count / 45.0) * 0.14
        + min(1.0, material_code_count / 3.0) * 0.20
        + min(1.0, dimension_count / 6.0) * 0.14
        + min(1.0, material_keyword_count / 5.0) * 0.12
        + min(1.0, avg_confidence) * 0.04
    )
    score -= min(0.25, noise_ratio * 0.18)
    score -= min(0.16, low_confidence_ratio * 0.10)
    if line_count == 0:
        score = 0.0
    score = max(0.0, min(1.0, score))

    return {
        "crop_id": _clean_text(crop.get("crop_id")),
        "region_id": _clean_text(crop.get("region_id")),
        "source_file": _clean_text(crop.get("source_file")),
        "page": _int(crop.get("page")),
        "image_path": _clean_text(crop.get("image_path")),
        "ocr_input_image_path": _clean_text(crop.get("ocr_input_image_path")) or _clean_text(crop.get("image_path")),
        "region_type": _clean_text(crop.get("region_type")),
        "region_subtype": _clean_text(crop.get("region_subtype")),
        "original_priority": round(_float(crop.get("priority"), 0.0), 6),
        "ocr_quality_score": round(score, 6),
        "ocr_quality_label": _quality_label(score=score, line_count=line_count),
        "ocr_text_line_count": line_count,
        "ocr_useful_line_count": useful_line_count,
        "ocr_noise_line_count": noise_line_count,
        "ocr_avg_confidence": round(avg_confidence, 6),
        "ocr_text_char_count": text_char_count,
        "ocr_chinese_char_count": chinese_char_count,
        "ocr_material_code_count": material_code_count,
        "ocr_dimension_count": dimension_count,
        "ocr_material_keyword_count": material_keyword_count,
        "ocr_low_confidence_line_count": low_confidence_line_count,
        "ocr_text_preview": texts[:12],
        "source_region_features": dict(crop.get("source_region_features") or {}) if isinstance(crop.get("source_region_features"), Mapping) else {},
        "source_region_quality_flags": list(crop.get("source_region_quality_flags") or []),
        "source_region_planner_source": _clean_text(crop.get("source_region_planner_source")),
        "source_region_bbox_pixel": list(crop.get("source_region_bbox_pixel") or []),
    }


def build_ocr_quality_reranked_plan(
    *,
    text_region_plan: Mapping[str, Any],
    quality_report: Mapping[str, Any],
    output_dir: str | Path,
    sampled_weight: float = 0.65,
    unsampled_priority_factor: float = 0.35,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    scores_by_region = {
        _clean_text(row.get("region_id")): row
        for row in quality_report.get("crop_scores") or []
        if _clean_text(row.get("region_id"))
    }
    regions: list[dict[str, Any]] = []
    for raw in text_region_plan.get("regions") or []:
        if not isinstance(raw, Mapping):
            continue
        region = dict(raw)
        original_priority = _float(region.get("priority"), 0.0)
        score_row = scores_by_region.get(_clean_text(region.get("region_id")))
        if score_row:
            ocr_score = _float(score_row.get("ocr_quality_score"), 0.0)
            feedback_priority = original_priority * (1.0 - sampled_weight) + ocr_score * sampled_weight
            region["ocr_quality_sampled"] = True
            region["ocr_quality_score"] = round(ocr_score, 6)
            region["ocr_quality_label"] = _clean_text(score_row.get("ocr_quality_label"))
            region["ocr_feedback_priority"] = round(feedback_priority, 6)
            region["ocr_quality_features"] = {
                "text_line_count": score_row.get("ocr_text_line_count", 0),
                "useful_line_count": score_row.get("ocr_useful_line_count", 0),
                "chinese_char_count": score_row.get("ocr_chinese_char_count", 0),
                "material_code_count": score_row.get("ocr_material_code_count", 0),
                "dimension_count": score_row.get("ocr_dimension_count", 0),
                "material_keyword_count": score_row.get("ocr_material_keyword_count", 0),
                "avg_confidence": score_row.get("ocr_avg_confidence", 0.0),
                "text_preview": score_row.get("ocr_text_preview", []),
            }
        else:
            feedback_priority = original_priority * unsampled_priority_factor
            region["ocr_quality_sampled"] = False
            region["ocr_quality_score"] = 0.0
            region["ocr_quality_label"] = "not_sampled"
            region["ocr_feedback_priority"] = round(feedback_priority, 6)
            region["quality_flags"] = [*list(region.get("quality_flags") or []), "ocr_quality_not_sampled"]
        regions.append(region)

    regions.sort(
        key=lambda row: (
            -_float(row.get("ocr_feedback_priority"), 0.0),
            -_float(row.get("ocr_quality_score"), 0.0),
            -_float(row.get("priority"), 0.0),
            _int(row.get("page")),
            _clean_text(row.get("region_id")),
        )
    )
    for index, region in enumerate(regions, start=1):
        region["ocr_feedback_rank"] = index

    summary = {
        "input_region_count": len(list(text_region_plan.get("regions") or [])),
        "reranked_region_count": len(regions),
        "sampled_region_count": sum(1 for row in regions if row.get("ocr_quality_sampled")),
        "high_quality_region_count": sum(1 for row in regions if row.get("ocr_quality_label") == "high"),
        "medium_quality_region_count": sum(1 for row in regions if row.get("ocr_quality_label") == "medium"),
        "low_quality_region_count": sum(1 for row in regions if row.get("ocr_quality_label") == "low"),
        "no_text_region_count": sum(1 for row in regions if row.get("ocr_quality_label") == "no_text"),
        "sampled_weight": sampled_weight,
        "unsampled_priority_factor": unsampled_priority_factor,
    }
    outputs = _write_reranked_outputs(directory=directory, summary=summary, regions=regions)
    return {
        "ok": True,
        "phase": "BIZ-2x-ocr-quality-rerank-text-region-plan",
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "regions": regions,
        "outputs": outputs,
    }


def build_ocr_quality_feedback_profile(
    *,
    quality_report: Mapping[str, Any] | None = None,
    crop_scores: Sequence[Mapping[str, Any]] | None = None,
    min_positive_score: float = 0.68,
    max_negative_score: float = 0.25,
) -> dict[str, Any]:
    """Summarize OCR-proven good/bad region shapes for the next discovery pass."""

    rows = list(crop_scores or [])
    if not rows and quality_report is not None:
        rows = [row for row in quality_report.get("crop_scores") or [] if isinstance(row, Mapping)]

    positive_samples: list[dict[str, Any]] = []
    negative_samples: list[dict[str, Any]] = []
    source_scores: list[dict[str, Any]] = []
    for row in rows:
        sample = _ocr_feedback_sample(row)
        score = _float(row.get("ocr_quality_score"), 0.0)
        label = _clean_text(row.get("ocr_quality_label"))
        bucket = ""
        if sample and score >= min_positive_score and _int(row.get("ocr_text_line_count")) > 0:
            positive_samples.append(sample)
            bucket = "positive"
        elif sample and (label == "no_text" or score <= max_negative_score):
            negative_samples.append(sample)
            bucket = "negative"
        source_scores.append(
            {
                "region_id": _clean_text(row.get("region_id")),
                "crop_id": _clean_text(row.get("crop_id")),
                "ocr_quality_score": round(score, 6),
                "ocr_quality_label": label,
                "ocr_text_line_count": _int(row.get("ocr_text_line_count")),
                "feedback_bucket": bucket or "ignored",
            }
        )

    return {
        "schema_version": OCR_FEEDBACK_SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "positive_sample_count": len(positive_samples),
        "negative_sample_count": len(negative_samples),
        "positive_feature_profile": _build_feedback_feature_profile(positive_samples),
        "negative_feature_profile": _build_feedback_feature_profile(negative_samples),
        "settings": {
            "min_positive_score": min_positive_score,
            "max_negative_score": max_negative_score,
            "positive_score_weight": 0.10,
            "negative_score_weight": 0.14,
            "shape_match_threshold": 0.55,
            "max_positive_delta": 0.12,
            "max_negative_delta": -0.18,
        },
        "source_scores": source_scores[:200],
    }


def _ocr_feedback_sample(row: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_features = row.get("source_region_features")
    if not isinstance(raw_features, Mapping):
        return None
    features = dict(raw_features)
    planner_source = _clean_text(row.get("source_region_planner_source"))
    if planner_source:
        features["planner_source"] = planner_source
    region_subtype = _clean_text(row.get("region_subtype"))
    if region_subtype:
        features["region_subtype"] = region_subtype
    quality_flags = [str(flag).strip() for flag in row.get("source_region_quality_flags") or [] if str(flag).strip()]
    if quality_flags:
        features["quality_flags"] = quality_flags
    if not any(feature in features for feature in OCR_FEEDBACK_NUMERIC_FEATURES):
        return None
    return features


def _build_feedback_feature_profile(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "sample_count": len(samples),
        "numeric_ranges": _feedback_numeric_ranges(samples),
        "categorical_values": _feedback_categorical_values(samples),
    }


def _feedback_numeric_ranges(samples: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for feature in OCR_FEEDBACK_NUMERIC_FEATURES:
        values = [_float(sample.get(feature), default=float("nan")) for sample in samples]
        values = [value for value in values if not math_is_nan(value)]
        if not values:
            continue
        low = min(values)
        high = max(values)
        mean = sum(values) / len(values)
        padding = _feedback_range_padding(feature=feature, low=low, high=high, mean=mean)
        result[feature] = {
            "min": round(max(0.0, low - padding), 8),
            "max": round(high + padding, 8),
            "mean": round(mean, 8),
            "sample_count": float(len(values)),
        }
    return result


def _feedback_categorical_values(samples: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for feature in OCR_FEEDBACK_CATEGORICAL_FEATURES:
        counts: dict[str, int] = {}
        for sample in samples:
            raw_value = sample.get(feature)
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for item in values:
                value = _clean_text(item)
                if not value:
                    continue
                counts[value] = counts.get(value, 0) + 1
        if counts:
            total = sum(counts.values())
            result[feature] = [
                {"value": value, "count": count, "ratio": round(count / max(1, total), 6)}
                for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]
            ]
    return result


def _feedback_range_padding(*, feature: str, low: float, high: float, mean: float) -> float:
    span = max(0.0, high - low)
    if feature in {"width_px", "height_px", "component_count"}:
        return max(2.0, span * 0.35, abs(mean) * 0.22)
    if feature == "aspect_ratio":
        return max(0.18, span * 0.35, abs(mean) * 0.30)
    return max(0.002, span * 0.35, abs(mean) * 0.35)


def math_is_nan(value: float) -> bool:
    return value != value


def _selected_crops(crop_manifest: Sequence[Mapping[str, Any]], *, max_crops: int) -> list[dict[str, Any]]:
    rows = [dict(row) for row in crop_manifest if _clean_text(row.get("image_path"))]
    rows.sort(
        key=lambda row: (
            -_float(row.get("priority"), 0.0),
            -_float(row.get("confidence"), 0.0),
            _int(row.get("page")),
            _clean_text(row.get("crop_id")),
        )
    )
    return rows[: max(0, int(max_crops or 0))]


def _prepared_ocr_image_path(*, image_path: Path, output_dir: Path, max_image_side: int) -> Path:
    if max_image_side <= 0:
        return image_path
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        width, height = image.size
        longest = max(width, height)
        if longest <= max_image_side:
            return image_path
        scale = float(max_image_side) / float(longest)
        target_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        resized = image.resize(target_size, Image.Resampling.LANCZOS)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{image_path.stem}_ocr_{target_size[0]}x{target_size[1]}.png"
    resized.save(output_path)
    return output_path


def _is_useful_text_line(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    if len(CHINESE_RE.findall(compact)) >= 2:
        return True
    if MATERIAL_CODE_RE.search(compact) or DIMENSION_RE.search(compact) or MATERIAL_KEYWORD_RE.search(compact):
        return True
    return bool(re.search(r"[A-Za-z]{2,}[-_]?\d{1,4}", compact))


def _is_noise_text_line(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    if _is_useful_text_line(compact):
        return False
    alnum_count = len(ALNUM_RE.findall(compact))
    if alnum_count == 0:
        return True
    if len(compact) <= 2 and not DIMENSION_RE.search(compact):
        return True
    return alnum_count / max(1, len(compact)) < 0.35


def _quality_label(*, score: float, line_count: int) -> str:
    if line_count <= 0:
        return "no_text"
    if score >= 0.68:
        return "high"
    if score >= 0.38:
        return "medium"
    return "low"


def _quality_summary(
    *,
    status: str,
    crop_count: int,
    completed_crops: int,
    ocr_rows: Sequence[Mapping[str, Any]],
    crop_scores: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    max_crops: int,
    max_image_side: int,
    unavailable_reason: str,
) -> dict[str, Any]:
    scores = [_float(row.get("ocr_quality_score"), 0.0) for row in crop_scores]
    summary = {
        "ocr_quality_status": status,
        "crop_count": crop_count,
        "ocr_completed_crop_count": completed_crops,
        "ocr_text_line_count": len(ocr_rows),
        "scored_crop_count": len(crop_scores),
        "high_quality_crop_count": sum(1 for row in crop_scores if row.get("ocr_quality_label") == "high"),
        "medium_quality_crop_count": sum(1 for row in crop_scores if row.get("ocr_quality_label") == "medium"),
        "low_quality_crop_count": sum(1 for row in crop_scores if row.get("ocr_quality_label") == "low"),
        "no_text_crop_count": sum(1 for row in crop_scores if row.get("ocr_quality_label") == "no_text"),
        "average_ocr_quality_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "max_ocr_quality_score": round(max(scores), 6) if scores else 0.0,
        "min_ocr_quality_score": round(min(scores), 6) if scores else 0.0,
        "max_crops": max_crops,
        "max_image_side": max_image_side,
        "warning_count": len(warnings),
        "error_count": len(errors),
    }
    if unavailable_reason:
        summary["unavailable_reason"] = unavailable_reason
    return summary


def _write_quality_outputs(
    *,
    directory: Path,
    summary: Mapping[str, Any],
    crop_scores: Sequence[Mapping[str, Any]],
    ocr_rows: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    feedback_profile: Mapping[str, Any],
) -> dict[str, str]:
    payloads = [
        ("ocr_quality_summary_json", directory / "ocr_quality_summary.json", dict(summary)),
        ("ocr_quality_scores_json", directory / "ocr_quality_scores.json", list(crop_scores)),
        ("ocr_quality_rows_json", directory / "ocr_quality_rows.json", list(ocr_rows)),
        ("ocr_quality_feedback_profile_json", directory / "ocr_quality_feedback_profile.json", dict(feedback_profile)),
        ("ocr_quality_diagnostics_json", directory / "ocr_quality_diagnostics.json", {"warnings": list(warnings), "errors": list(errors)}),
    ]
    outputs: dict[str, str] = {}
    for key, path, payload in payloads:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    score_csv = directory / "ocr_quality_scores.csv"
    _write_score_csv(score_csv, crop_scores)
    outputs["ocr_quality_scores_csv"] = str(score_csv.resolve())
    return outputs


def _write_score_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "crop_id",
        "region_id",
        "source_file",
        "page",
        "original_priority",
        "ocr_quality_score",
        "ocr_quality_label",
        "ocr_text_line_count",
        "ocr_useful_line_count",
        "ocr_avg_confidence",
        "ocr_chinese_char_count",
        "ocr_material_code_count",
        "ocr_dimension_count",
        "ocr_material_keyword_count",
        "ocr_noise_line_count",
        "image_path",
        "ocr_input_image_path",
        "ocr_text_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **{key: row.get(key, "") for key in headers if key != "ocr_text_preview"},
                    "ocr_text_preview": " | ".join(str(item) for item in row.get("ocr_text_preview") or []),
                }
            )


def _write_reranked_outputs(*, directory: Path, summary: Mapping[str, Any], regions: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    payloads = [
        ("ocr_rerank_summary_json", directory / "text_region_plan_rerank_summary.json", dict(summary)),
        ("ocr_reranked_text_region_plan_json", directory / "text_region_plan_reranked.json", {"schema_version": SCHEMA_VERSION, "regions": list(regions)}),
    ]
    for key, path, payload in payloads:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    csv_path = directory / "text_region_plan_reranked.csv"
    headers = [
        "ocr_feedback_rank",
        "region_id",
        "source_file",
        "page",
        "priority",
        "ocr_feedback_priority",
        "ocr_quality_sampled",
        "ocr_quality_score",
        "ocr_quality_label",
        "bbox_ratio",
        "bbox_pixel",
        "quality_flags",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in regions:
            writer.writerow(
                {
                    "ocr_feedback_rank": row.get("ocr_feedback_rank", ""),
                    "region_id": row.get("region_id", ""),
                    "source_file": row.get("source_file", ""),
                    "page": row.get("page", ""),
                    "priority": row.get("priority", ""),
                    "ocr_feedback_priority": row.get("ocr_feedback_priority", ""),
                    "ocr_quality_sampled": row.get("ocr_quality_sampled", ""),
                    "ocr_quality_score": row.get("ocr_quality_score", ""),
                    "ocr_quality_label": row.get("ocr_quality_label", ""),
                    "bbox_ratio": json.dumps(row.get("bbox_ratio") or [], ensure_ascii=False),
                    "bbox_pixel": json.dumps(row.get("bbox_pixel") or [], ensure_ascii=False),
                    "quality_flags": "|".join(str(item) for item in row.get("quality_flags") or []),
                }
            )
    outputs["ocr_reranked_text_region_plan_csv"] = str(csv_path.resolve())
    return outputs


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
