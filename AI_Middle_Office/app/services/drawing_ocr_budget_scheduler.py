from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "drawing_budgeted_ocr_execution_plan_v1"

EXECUTION_BUCKET_CN = {
    "primary_selected": "预算内主路径入选区域",
    "fallback_overflow_budget_cut": "预算截断兜底复核区域",
    "fallback_recoverable_rejected": "可能误拒绝兜底复核区域",
}

BUDGET_BUCKET_CN = {
    "ocr_positive_feedback": "OCR 正反馈相似区域",
    "colored_annotation": "彩色图签/材料表候选",
    "right_notes": "右侧说明/做法文字候选",
    "large_region_split": "大块 CAD 二次拆分小字",
    "main_drawing": "主图文字/引线标注",
    "fallback_high_priority": "综合高优先级兜底",
}


def build_budgeted_ocr_execution_plan(
    *,
    selected_regions: Sequence[Mapping[str, Any]],
    rejected_regions: Sequence[Mapping[str, Any]],
    overflow_regions: Sequence[Mapping[str, Any]],
    total_budget: int = 40,
    overflow_reserve: int | None = None,
    recoverable_rejected_reserve: int | None = None,
) -> dict[str, Any]:
    budget = max(0, int(total_budget or 0))
    overflow_target = _default_reserve(budget, ratio=0.12, minimum=2, explicit=overflow_reserve)
    recoverable_target = _default_reserve(budget, ratio=0.08, minimum=2, explicit=recoverable_rejected_reserve)
    if overflow_target + recoverable_target > budget:
        scale = budget / max(1, overflow_target + recoverable_target)
        overflow_target = int(math.floor(overflow_target * scale))
        recoverable_target = max(0, budget - overflow_target)

    selected_candidates = [dict(row) for row in selected_regions if _has_valid_bbox(row)]
    overflow_candidates = [dict(row) for row in overflow_regions if _has_valid_bbox(row)]
    recoverable_candidates = [dict(row) for row in rejected_regions if _is_recoverable_rejected_candidate(row)]

    primary_target = max(0, budget - overflow_target - recoverable_target)
    primary = _select_diverse_regions(
        selected_candidates,
        max_count=primary_target,
        bucket_order=[
            "ocr_positive_feedback",
            "colored_annotation",
            "right_notes",
            "main_drawing",
            "large_region_split",
            "fallback_high_priority",
        ],
        bucket_ratios={
            "ocr_positive_feedback": 0.50,
            "colored_annotation": 0.25,
            "right_notes": 0.10,
            "main_drawing": 0.08,
            "large_region_split": 0.04,
            "fallback_high_priority": 0.03,
        },
    )
    used_keys = {_region_key(row) for row in primary}

    recoverable = _select_ranked_regions(
        [row for row in recoverable_candidates if _region_key(row) not in used_keys],
        max_count=recoverable_target,
        score_fn=_recoverable_score,
    )
    used_keys.update(_region_key(row) for row in recoverable)

    overflow = _select_diverse_regions(
        [row for row in overflow_candidates if _region_key(row) not in used_keys],
        max_count=overflow_target,
        bucket_order=[
            "colored_annotation",
            "ocr_positive_feedback",
            "large_region_split",
            "right_notes",
            "main_drawing",
            "fallback_high_priority",
        ],
        bucket_ratios={
            "colored_annotation": 0.36,
            "ocr_positive_feedback": 0.34,
            "large_region_split": 0.20,
            "right_notes": 0.04,
            "main_drawing": 0.04,
            "fallback_high_priority": 0.02,
        },
    )
    used_keys.update(_region_key(row) for row in overflow)

    remaining = budget - len(primary) - len(recoverable) - len(overflow)
    if remaining > 0:
        extra_primary = _select_diverse_regions(
            [row for row in selected_candidates if _region_key(row) not in used_keys],
            max_count=remaining,
            bucket_order=[
                "ocr_positive_feedback",
                "colored_annotation",
                "right_notes",
                "main_drawing",
                "large_region_split",
                "fallback_high_priority",
            ],
        )
        primary.extend(extra_primary)
        used_keys.update(_region_key(row) for row in extra_primary)
        remaining = budget - len(primary) - len(recoverable) - len(overflow)

    if remaining > 0:
        extra_overflow = _select_diverse_regions(
            [row for row in overflow_candidates if _region_key(row) not in used_keys],
            max_count=remaining,
            bucket_order=[
                "colored_annotation",
                "ocr_positive_feedback",
                "large_region_split",
                "right_notes",
                "main_drawing",
                "fallback_high_priority",
            ],
        )
        overflow.extend(extra_overflow)
        used_keys.update(_region_key(row) for row in extra_overflow)

    regions: list[dict[str, Any]] = []
    for index, row in enumerate(primary, start=1):
        regions.append(_execution_region(row, bucket="primary_selected", index=index))
    for index, row in enumerate(recoverable, start=1):
        regions.append(_execution_region(row, bucket="fallback_recoverable_rejected", index=index))
    for index, row in enumerate(overflow, start=1):
        regions.append(_execution_region(row, bucket="fallback_overflow_budget_cut", index=index))

    summary = {
        "schema_version": SCHEMA_VERSION,
        "requested_total_budget": budget,
        "actual_execution_region_count": len(regions),
        "primary_selected_target": primary_target,
        "recoverable_rejected_target": recoverable_target,
        "overflow_target": overflow_target,
        "input_selected_count": len(selected_candidates),
        "input_recoverable_rejected_count": len(recoverable_candidates),
        "input_overflow_count": len(overflow_candidates),
        "execution_bucket_counts": dict(Counter(_clean_text(row.get("ocr_execution_bucket")) for row in regions)),
        "source_budget_bucket_counts": dict(Counter(_clean_text(row.get("budget_bucket")) or _infer_budget_bucket(row) for row in regions)),
        "fallback_region_count": sum(
            1
            for row in regions
            if _clean_text(row.get("ocr_execution_bucket"))
            in {"fallback_overflow_budget_cut", "fallback_recoverable_rejected"}
        ),
        "has_overflow_fallback": any(row.get("ocr_execution_bucket") == "fallback_overflow_budget_cut" for row in regions),
        "has_recoverable_rejected_fallback": any(row.get("ocr_execution_bucket") == "fallback_recoverable_rejected" for row in regions),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": summary,
        "regions": regions,
    }


def _default_reserve(budget: int, *, ratio: float, minimum: int, explicit: int | None) -> int:
    if explicit is not None:
        return max(0, int(explicit or 0))
    if budget <= 0:
        return 0
    return min(budget, max(minimum, int(math.ceil(budget * ratio))))


def _select_diverse_regions(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_count: int,
    bucket_order: Sequence[str],
    bucket_ratios: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    limit = max(0, int(max_count or 0))
    if limit <= 0:
        return []
    candidates = sorted([dict(row) for row in rows], key=_priority_sort_key)
    if len(candidates) <= limit:
        return candidates

    ratios = dict(bucket_ratios or {})
    by_bucket: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in bucket_order}
    for row in candidates:
        by_bucket.setdefault(_infer_budget_bucket(row), []).append(row)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in bucket_order:
        bucket_rows = by_bucket.get(bucket) or []
        if not bucket_rows or len(selected) >= limit:
            continue
        target = ratios.get(bucket)
        if target is None:
            target_count = 1
        else:
            target_count = max(1, int(math.ceil(limit * target)))
        for row in bucket_rows[: min(target_count, limit - len(selected))]:
            key = _region_key(row)
            if key in seen:
                continue
            selected.append(row)
            seen.add(key)
            if len(selected) >= limit:
                break

    for row in candidates:
        if len(selected) >= limit:
            break
        key = _region_key(row)
        if key in seen:
            continue
        selected.append(row)
        seen.add(key)
    return selected[:limit]


def _select_ranked_regions(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_count: int,
    score_fn: Any,
) -> list[dict[str, Any]]:
    limit = max(0, int(max_count or 0))
    candidates = sorted(
        [dict(row) for row in rows if _has_valid_bbox(row)],
        key=lambda row: (-float(score_fn(row)), _int(row.get("page")), _bbox_top(row.get("bbox_ratio")), _bbox_left(row.get("bbox_ratio"))),
    )
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in candidates:
        key = _region_key(row)
        if key in seen:
            continue
        selected.append(row)
        seen.add(key)
        if len(selected) >= limit:
            break
    return selected


def _execution_region(row: Mapping[str, Any], *, bucket: str, index: int) -> dict[str, Any]:
    original_region_id = _clean_text(row.get("region_id")) or f"region_{index:03d}"
    prefix = {
        "primary_selected": "ocrp",
        "fallback_overflow_budget_cut": "ocro",
        "fallback_recoverable_rejected": "ocrr",
    }.get(bucket, "ocr")
    result = dict(row)
    inferred_budget_bucket = _infer_budget_bucket(result)
    result["budget_bucket"] = _clean_text(result.get("budget_bucket")) or inferred_budget_bucket
    result["budget_bucket_cn"] = _clean_text(result.get("budget_bucket_cn")) or _budget_bucket_cn(inferred_budget_bucket)
    result["original_region_id"] = original_region_id
    result["region_id"] = f"{prefix}_{index:03d}_{_safe_identifier(original_region_id)}"
    result["ocr_execution_bucket"] = bucket
    result["ocr_execution_bucket_cn"] = EXECUTION_BUCKET_CN.get(bucket, bucket)
    result["ocr_execution_rank"] = index
    result["ocr_execution_reason_cn"] = _execution_reason_cn(result, bucket=bucket)
    result["ocr_execution_budget_decision_cn"] = _execution_decision_cn(result, bucket=bucket)
    result["candidate_decision_cn"] = _execution_decision_cn(result, bucket=bucket)
    result["candidate_reason_cn"] = _execution_reason_cn(result, bucket=bucket)
    result["candidate_signal_cn"] = _execution_signal_cn(result, bucket=bucket)
    result["candidate_risk_cn"] = _execution_risk_cn(result, bucket=bucket)
    result["next_action_cn"] = _execution_next_action_cn(bucket)
    result["recommended_tools"] = ["ocr"]
    result["expected_information"] = ["drawing_text"]
    if not isinstance(result.get("crop_strategy"), Mapping):
        result["crop_strategy"] = {"highres_scale": 64.0, "padding_ratio": 0.018}
    return result


def _execution_signal_cn(row: Mapping[str, Any], *, bucket: str) -> str:
    signals: list[str] = [f"执行分类：{EXECUTION_BUCKET_CN.get(bucket, bucket)}"]
    budget_bucket_cn = _clean_text(row.get("budget_bucket_cn"))
    if budget_bucket_cn:
        signals.append(f"来源分类：{budget_bucket_cn}")
    rejected_layer_cn = _clean_text(row.get("rejected_layer_cn"))
    if rejected_layer_cn:
        signals.append(f"拒绝层级：{rejected_layer_cn}")
    overflow_reason_cn = _clean_text(row.get("overflow_reason_cn"))
    if overflow_reason_cn:
        signals.append(f"预算截断：{overflow_reason_cn}")
    if row.get("ocr_feedback_positive_shape_match") or "ocr_feedback_positive_shape_match" in {str(flag) for flag in row.get("quality_flags") or []}:
        signals.append("OCR 反馈：匹配有效文字正样本形态")
    return "；".join(_unique_texts(signals))


def _execution_risk_cn(row: Mapping[str, Any], *, bucket: str) -> str:
    if bucket == "primary_selected":
        return "已进入主路径，但仍需 OCR 质量评分和后续语义分类确认是否为材料信息。"
    if bucket == "fallback_overflow_budget_cut":
        return "原本被预算截断，存在材料表/说明文字漏召回风险；本轮只抽少量样本验证价值。"
    if bucket == "fallback_recoverable_rejected":
        return "原本被规则拒绝，可能是误杀，也可能只是轴号、尺寸或碎片字符。"
    return "需要结合 OCR 结果复核。"


def _execution_next_action_cn(bucket: str) -> str:
    if bucket == "primary_selected":
        return "直接 OCR"
    if bucket == "fallback_overflow_budget_cut":
        return "兜底 OCR；若产出 high/medium，后续提高同类 overflow 优先级"
    if bucket == "fallback_recoverable_rejected":
        return "兜底 OCR；若持续 low/no_text，后续降低同类误召回"
    return "OCR"


def _execution_reason_cn(row: Mapping[str, Any], *, bucket: str) -> str:
    if bucket == "primary_selected":
        return "来自 discovery 预算内主路径，本轮作为 OCR 主体执行。"
    if bucket == "fallback_overflow_budget_cut":
        budget_bucket = _budget_bucket_cn(_infer_budget_bucket(row))
        return f"该区域已被发现但因预算进入 overflow，抽取少量 {budget_bucket} 做兜底 OCR，降低材料文字漏召回风险。"
    if bucket == "fallback_recoverable_rejected":
        return "该 rejected 区域被判断为可能误拒绝，抽取少量样本做兜底 OCR，验证是否应召回。"
    return "进入本轮 OCR 执行计划。"


def _execution_decision_cn(row: Mapping[str, Any], *, bucket: str) -> str:
    if bucket == "primary_selected":
        return "预算内主路径保留，直接 OCR。"
    if bucket == "fallback_overflow_budget_cut":
        return "原本预算截断，本轮使用兜底名额 OCR。"
    if bucket == "fallback_recoverable_rejected":
        return "原本规则拒绝，本轮使用误拒绝复核名额 OCR。"
    return "本轮 OCR。"


def _is_recoverable_rejected_candidate(row: Mapping[str, Any]) -> bool:
    if not _has_valid_bbox(row):
        return False
    layer = _clean_text(row.get("rejected_layer"))
    if layer == "recoverable_text_like":
        return True
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    subtype = _clean_text(row.get("region_subtype"))
    if subtype in {"line_or_marker_noise", "noise_or_fill", "split_noise"}:
        return False
    return bool(flags & {"score_below_threshold", "split_from_too_large_region", "ocr_feedback_positive_shape_match"})


def _recoverable_score(row: Mapping[str, Any]) -> float:
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    score = _float(row.get("priority"), 0.0) + _float(row.get("confidence"), 0.0) * 0.35
    if _clean_text(row.get("rejected_layer")) == "recoverable_text_like":
        score += 0.35
    if "ocr_feedback_positive_shape_match" in flags:
        score += 0.30
    if "split_from_too_large_region" in flags:
        score += 0.12
    return score


def _priority_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -_float(row.get("priority"), 0.0),
        -_float(row.get("confidence"), 0.0),
        _int(row.get("page")),
        _bbox_top(row.get("bbox_ratio")),
        _bbox_left(row.get("bbox_ratio")),
        _clean_text(row.get("region_id")),
    )


def _infer_budget_bucket(row: Mapping[str, Any]) -> str:
    bucket = _clean_text(row.get("budget_bucket"))
    if bucket:
        return bucket
    features = row.get("features") if isinstance(row.get("features"), Mapping) else {}
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    subtype = _clean_text(row.get("region_subtype"))
    source = _clean_text(features.get("candidate_source"))
    zone = _clean_text(features.get("page_zone"))
    planner = _clean_text(row.get("planner_source"))
    if row.get("ocr_feedback_positive_shape_match") or "ocr_feedback_positive_shape_match" in flags:
        return "ocr_positive_feedback"
    if source == "colored_annotation_cluster" or subtype == "colored_text_or_callout":
        return "colored_annotation"
    if planner == "medium_cv_text_region_detector.large_region_splitter" or "split_from_too_large_region" in flags:
        return "large_region_split"
    if zone == "right_notes" or subtype == "right_side_notes_text":
        return "right_notes"
    if zone == "main_drawing":
        return "main_drawing"
    return "fallback_high_priority"


def _budget_bucket_cn(bucket: str) -> str:
    return BUDGET_BUCKET_CN.get(_clean_text(bucket), "未分类候选")


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


def _has_valid_bbox(row: Mapping[str, Any]) -> bool:
    bbox = row.get("bbox_ratio")
    if not isinstance(bbox, Sequence) or len(bbox) != 4:
        return False
    return _float(bbox[2]) > _float(bbox[0]) and _float(bbox[3]) > _float(bbox[1])


def _region_key(row: Mapping[str, Any]) -> str:
    return "|".join(
        [
            _clean_text(row.get("source_file")),
            str(_int(row.get("page"))),
            _clean_text(row.get("original_region_id")) or _clean_text(row.get("region_id")),
            str(row.get("bbox_ratio") or ""),
        ]
    )


def _bbox_top(bbox: Any) -> float:
    if isinstance(bbox, Sequence) and len(bbox) >= 2:
        return _float(bbox[1])
    return 0.0


def _bbox_left(bbox: Any) -> float:
    if isinstance(bbox, Sequence) and len(bbox) >= 1:
        return _float(bbox[0])
    return 0.0


def _safe_identifier(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.strip())
    return cleaned[:80] or "region"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
