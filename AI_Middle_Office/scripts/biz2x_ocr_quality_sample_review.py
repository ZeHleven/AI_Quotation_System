from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_highres_region_renderer import build_highres_region_render_report  # noqa: E402
from app.services.drawing_ocr_quality_scorer import build_highres_ocr_quality_report  # noqa: E402


DEFAULT_PDF = ROOT / "tmp" / "xinda_staff_canteen_drawing.pdf"

NEGATIVE_SIGNAL_FLAGS = {
    "score_below_threshold",
    "line_dominant",
    "too_sparse_after_line_removal",
    "single_component_stroke",
    "colored_region_without_text_fragments",
    "too_few_split_text_fragments",
    "split_candidate_too_small",
    "split_dense_hatch_noise",
}
NEGATIVE_SIGNAL_SUBTYPES = {
    "line_or_marker_noise",
    "noise_or_fill",
    "split_noise",
}

QUALITY_LABEL_CN = {
    "high": "高质量有效文字",
    "medium": "中等质量可疑文字",
    "low": "低质量文字/噪声",
    "no_text": "无 OCR 文本",
}
SAMPLE_BUCKET_CN = {
    "expected_effective_text": "预计有效文字区域",
    "recoverable_rejected_text_like": "可能被误拒绝的 rejected 区域",
    "expected_noise_or_no_text": "预计噪声/无文本区域",
    "overflow_budget_cut": "预算截断 overflow 区域",
}
REGION_SUBTYPE_CN = {
    "colored_text_or_callout": "彩色文字/引线标注",
    "right_side_notes_text": "右侧说明文字",
    "text_line": "文字行",
    "text_block": "文字块",
    "line_or_marker_noise": "线条/标记噪声",
    "noise_or_fill": "填充/图案噪声",
    "split_noise": "大块拆分后的疑似噪声",
}
PAGE_ZONE_CN = {
    "right_notes": "右侧说明区",
    "bottom_title": "底部标题栏",
    "top_header": "顶部页眉区",
    "main_drawing": "主图区域",
}
PLANNER_SOURCE_CN = {
    "medium_cv_text_region_detector": "中清图文字区域发现",
    "medium_cv_text_region_detector.large_region_splitter": "大 CAD 区域二次拆分",
}
QUALITY_FLAG_CN = {
    "too_narrow": "区域太窄",
    "too_short": "区域太矮",
    "too_large_for_text_region": "区域太大，不适合直接当文字块",
    "aspect_ratio_unlikely_text": "宽高比不像文字",
    "too_sparse_after_line_removal": "去线后过于稀疏",
    "too_dense_possible_fill_or_hatch": "过密，疑似填充/剖面线",
    "line_dominant": "线条占比过高",
    "no_text_like_components": "没有像文字的小组件",
    "single_component_stroke": "单组件笔画/线段",
    "colored_region_without_text_fragments": "彩色区域但缺少文字碎片",
    "score_below_threshold": "分数低于入选阈值",
    "split_from_too_large_region": "来自大块区域二次拆分",
    "too_few_split_text_fragments": "拆分后文字碎片太少",
    "split_candidate_too_small": "拆分候选太小",
    "split_dense_hatch_noise": "拆分后仍像密集填充噪声",
    "ocr_feedback_positive_shape_match": "匹配 OCR 正样本形态",
    "ocr_feedback_negative_shape_match": "匹配 OCR 负样本形态",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an OCR quality review pack with effective and likely-noise crops.")
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="Source drawing PDF.")
    parser.add_argument("--discovery-run-dir", default="", help="A text_region_discovery run directory.")
    parser.add_argument("--text-region-plan", default="", help="Path to text_region_plan.json.")
    parser.add_argument("--text-region-rejected", default="", help="Path to text_region_rejected.json.")
    parser.add_argument("--text-region-overflow", default="", help="Path to text_region_overflow.json.")
    parser.add_argument("--output-dir", default="", help="Defaults to outputs/biz2x_trial/ocr_quality_review/<run_id>.")
    parser.add_argument("--positive-samples", type=int, default=8)
    parser.add_argument("--recoverable-rejected-samples", type=int, default=6)
    parser.add_argument("--negative-samples", type=int, default=12)
    parser.add_argument("--overflow-samples", type=int, default=6)
    parser.add_argument("--max-image-side", type=int, default=1200)
    parser.add_argument("--default-scale", type=float, default=32.0)
    parser.add_argument("--max-scale", type=float, default=96.0)
    parser.add_argument("--max-pixels", type=int, default=32_000_000)
    parser.add_argument("--min-width-px", type=int, default=900)
    parser.add_argument("--min-height-px", type=int, default=96)
    parser.add_argument("--paddlex-cache-dir", default="", help="Optional PADDLE_PDX_CACHE_HOME override for local PaddleOCR.")
    parser.add_argument("--ocr-engine", default="paddleocr")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if args.paddlex_cache_dir:
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(args.paddlex_cache_dir).resolve())

    plan_path, rejected_path, overflow_path = _resolve_discovery_inputs(args)
    if not plan_path.exists() or not plan_path.is_file():
        raise FileNotFoundError(f"text_region_plan.json not found: {plan_path}")
    if not rejected_path.exists() or not rejected_path.is_file():
        raise FileNotFoundError(f"text_region_rejected.json not found: {rejected_path}")

    if args.output_dir:
        run_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_ocr_quality_review")
        run_dir = ROOT / "outputs" / "biz2x_trial" / "ocr_quality_review" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rejected_regions = json.loads(rejected_path.read_text(encoding="utf-8"))
    overflow_regions = json.loads(overflow_path.read_text(encoding="utf-8")) if overflow_path.exists() and overflow_path.is_file() else []
    review_regions = build_review_sample_regions(
        selected_regions=[row for row in plan.get("regions") or [] if isinstance(row, Mapping)],
        rejected_regions=[row for row in rejected_regions if isinstance(row, Mapping)],
        overflow_regions=[row for row in overflow_regions if isinstance(row, Mapping)],
        positive_samples=args.positive_samples,
        recoverable_rejected_samples=args.recoverable_rejected_samples,
        negative_samples=args.negative_samples,
        overflow_samples=args.overflow_samples,
    )
    review_plan = {
        "schema_version": "drawing_ocr_quality_review_plan_v1",
        "source_text_region_plan": str(plan_path.resolve()),
        "source_text_region_rejected": str(rejected_path.resolve()),
        "source_text_region_overflow": str(overflow_path.resolve()) if overflow_path else "",
        "regions": review_regions,
    }
    review_plan_path = run_dir / "ocr_quality_review_plan.json"
    review_plan_path.write_text(json.dumps(review_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_review_plan_csv(run_dir / "ocr_quality_review_plan.csv", review_regions)

    referenced_pages = {
        int(row.get("page") or 0)
        for row in review_regions
        if str(row.get("source_file") or "").strip() == pdf_path.name and int(row.get("page") or 0) > 0
    }
    parse_report = _build_fast_pdf_page_parse_report(pdf_path, referenced_pages=referenced_pages)
    highres_report = build_highres_region_render_report(
        parse_report=parse_report,
        layout_plan_report=review_plan,
        output_dir=run_dir / "highres_review_crops",
        max_regions=len(review_regions),
        default_scale=args.default_scale,
        max_scale=args.max_scale,
        max_pixels=args.max_pixels,
        min_width_px=args.min_width_px,
        min_height_px=args.min_height_px,
        min_area_ratio=0.000001,
        max_area_ratio=0.22,
    )
    quality_report = build_highres_ocr_quality_report(
        crop_manifest=highres_report.get("crop_manifest") or [],
        output_dir=run_dir / "ocr_quality",
        ocr_engine=args.ocr_engine,
        max_crops=len(review_regions),
        max_image_side=args.max_image_side,
    )
    quality_scores_review_csv = run_dir / "ocr_quality_scores_review_cn.csv"
    _write_quality_scores_review_csv(
        quality_scores_review_csv,
        scores=[row for row in quality_report.get("crop_scores") or [] if isinstance(row, Mapping)],
        review_regions=review_regions,
    )

    review_markdown = build_quality_review_markdown(
        run_dir=run_dir,
        pdf_path=pdf_path,
        plan_path=plan_path,
        rejected_path=rejected_path,
        overflow_path=overflow_path,
        review_regions=review_regions,
        highres_report=highres_report,
        quality_report=quality_report,
    )
    review_doc_path = run_dir / "ocr_quality_review.md"
    review_doc_path.write_text(review_markdown, encoding="utf-8")

    summary = {
        "run_dir": str(run_dir.resolve()),
        "pdf_path": str(pdf_path.resolve()),
        "text_region_plan": str(plan_path.resolve()),
        "text_region_rejected": str(rejected_path.resolve()),
        "text_region_overflow": str(overflow_path.resolve()) if overflow_path else "",
        "review_region_count": len(review_regions),
        "expected_effective_sample_count": sum(1 for row in review_regions if row.get("review_sample_bucket") == "expected_effective_text"),
        "recoverable_rejected_sample_count": sum(1 for row in review_regions if row.get("review_sample_bucket") == "recoverable_rejected_text_like"),
        "expected_noise_sample_count": sum(1 for row in review_regions if row.get("review_sample_bucket") == "expected_noise_or_no_text"),
        "overflow_sample_count": sum(1 for row in review_regions if row.get("review_sample_bucket") == "overflow_budget_cut"),
        "highres_summary": highres_report.get("summary"),
        "ocr_quality_summary": quality_report.get("summary"),
        "feedback_profile_summary": {
            "positive_sample_count": (quality_report.get("ocr_quality_feedback_profile") or {}).get("positive_sample_count", 0),
            "negative_sample_count": (quality_report.get("ocr_quality_feedback_profile") or {}).get("negative_sample_count", 0),
        },
        "outputs": {
            "ocr_quality_review_plan_json": str(review_plan_path.resolve()),
            "ocr_quality_review_plan_csv": str((run_dir / "ocr_quality_review_plan.csv").resolve()),
            "ocr_quality_scores_review_cn_csv": str(quality_scores_review_csv.resolve()),
            "ocr_quality_review_markdown": str(review_doc_path.resolve()),
            **dict(highres_report.get("outputs") or {}),
            **dict(quality_report.get("outputs") or {}),
        },
    }
    summary_path = run_dir / "ocr_quality_review_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def build_review_sample_regions(
    *,
    selected_regions: Sequence[Mapping[str, Any]],
    rejected_regions: Sequence[Mapping[str, Any]],
    positive_samples: int,
    negative_samples: int,
    overflow_regions: Sequence[Mapping[str, Any]] = (),
    recoverable_rejected_samples: int = 0,
    overflow_samples: int = 0,
) -> list[dict[str, Any]]:
    positives = sorted(
        [dict(row) for row in selected_regions],
        key=lambda row: (-_float(row.get("priority")), -_float(row.get("confidence")), _int(row.get("page")), _clean_text(row.get("region_id"))),
    )[: max(0, int(positive_samples or 0))]
    recoverable = _select_recoverable_review_regions(
        rejected_regions,
        max_count=max(0, int(recoverable_rejected_samples or 0)),
    )
    negatives = _select_negative_review_regions(rejected_regions, max_count=max(0, int(negative_samples or 0)))
    overflow = _select_overflow_review_regions(overflow_regions, max_count=max(0, int(overflow_samples or 0)))

    result: list[dict[str, Any]] = []
    for index, row in enumerate(positives, start=1):
        result.append(_review_region(row, bucket="expected_effective_text", index=index))
    for index, row in enumerate(recoverable, start=1):
        result.append(_review_region(row, bucket="recoverable_rejected_text_like", index=index))
    for index, row in enumerate(negatives, start=1):
        result.append(_review_region(row, bucket="expected_noise_or_no_text", index=index))
    for index, row in enumerate(overflow, start=1):
        result.append(_review_region(row, bucket="overflow_budget_cut", index=index))
    return result


def _select_recoverable_review_regions(rejected_regions: Sequence[Mapping[str, Any]], *, max_count: int) -> list[dict[str, Any]]:
    if max_count <= 0:
        return []
    candidates = [dict(row) for row in rejected_regions if _is_recoverable_review_candidate(row)]
    candidates.sort(key=lambda row: (-_recoverable_candidate_score(row), _int(row.get("page")), _bbox_top(row.get("bbox_ratio")), _bbox_left(row.get("bbox_ratio"))))
    return _dedupe_region_rows(candidates, max_count=max_count)


def _is_recoverable_review_candidate(row: Mapping[str, Any]) -> bool:
    bbox = row.get("bbox_ratio")
    if not isinstance(bbox, Sequence) or len(bbox) != 4:
        return False
    layer = _clean_text(row.get("rejected_layer"))
    if layer == "recoverable_text_like":
        return True
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    subtype = _clean_text(row.get("region_subtype"))
    if subtype in NEGATIVE_SIGNAL_SUBTYPES:
        return False
    return bool(flags & {"score_below_threshold", "split_from_too_large_region"}) and _float(row.get("priority")) >= 0.18


def _recoverable_candidate_score(row: Mapping[str, Any]) -> float:
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    score = _float(row.get("priority"), 0.0)
    score += _float(row.get("confidence"), 0.0) * 0.4
    if _clean_text(row.get("rejected_layer")) == "recoverable_text_like":
        score += 0.35
    if "ocr_feedback_positive_shape_match" in flags:
        score += 0.28
    if "split_from_too_large_region" in flags:
        score += 0.12
    return score


def _select_negative_review_regions(rejected_regions: Sequence[Mapping[str, Any]], *, max_count: int) -> list[dict[str, Any]]:
    if max_count <= 0:
        return []
    candidates = [dict(row) for row in rejected_regions if _is_negative_review_candidate(row)]
    candidates.sort(key=lambda row: (-_negative_candidate_score(row), _int(row.get("page")), _bbox_top(row.get("bbox_ratio")), _bbox_left(row.get("bbox_ratio"))))
    selected: list[dict[str, Any]] = []
    seen_reasons: set[str] = set()
    for row in candidates:
        reason_key = _negative_reason_key(row)
        if reason_key in seen_reasons and len(selected) < max_count // 2:
            continue
        selected.append(row)
        seen_reasons.add(reason_key)
        if len(selected) >= max_count:
            return selected
    for row in candidates:
        if any(_clean_text(row.get("region_id")) == _clean_text(existing.get("region_id")) for existing in selected):
            continue
        selected.append(row)
        if len(selected) >= max_count:
            break
    return selected


def _select_overflow_review_regions(overflow_regions: Sequence[Mapping[str, Any]], *, max_count: int) -> list[dict[str, Any]]:
    if max_count <= 0:
        return []
    candidates = [dict(row) for row in overflow_regions if _has_valid_bbox(row)]
    candidates.sort(key=lambda row: (-_overflow_candidate_score(row), _int(row.get("page")), _bbox_top(row.get("bbox_ratio")), _bbox_left(row.get("bbox_ratio"))))
    return _dedupe_region_rows(candidates, max_count=max_count)


def _overflow_candidate_score(row: Mapping[str, Any]) -> float:
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    score = _float(row.get("priority"), 0.0)
    score += _float(row.get("confidence"), 0.0) * 0.35
    if _clean_text(row.get("overflow_reason")) in {"page_region_cap", "global_region_cap"}:
        score += 0.16
    if _clean_text(row.get("rejected_layer")) == "recoverable_text_like":
        score += 0.18
    if "ocr_feedback_positive_shape_match" in flags:
        score += 0.22
    return score


def _dedupe_region_rows(rows: Sequence[Mapping[str, Any]], *, max_count: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = _clean_text(row.get("region_id")) or json.dumps(row.get("bbox_ratio") or [], ensure_ascii=False)
        if key in seen:
            continue
        selected.append(dict(row))
        seen.add(key)
        if len(selected) >= max_count:
            break
    return selected


def _has_valid_bbox(row: Mapping[str, Any]) -> bool:
    bbox = row.get("bbox_ratio")
    if not isinstance(bbox, Sequence) or len(bbox) != 4:
        return False
    return _float(bbox[2]) > _float(bbox[0]) and _float(bbox[3]) > _float(bbox[1])


def _is_negative_review_candidate(row: Mapping[str, Any]) -> bool:
    if not _has_valid_bbox(row):
        return False
    bbox = row.get("bbox_ratio")
    area = max(0.0, (_float(bbox[2]) - _float(bbox[0])) * (_float(bbox[3]) - _float(bbox[1])))
    if area <= 0 or area > 0.08:
        return False
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    subtype = _clean_text(row.get("region_subtype"))
    if flags & NEGATIVE_SIGNAL_FLAGS:
        return True
    return subtype in NEGATIVE_SIGNAL_SUBTYPES


def _negative_candidate_score(row: Mapping[str, Any]) -> float:
    flags = {str(flag) for flag in row.get("quality_flags") or []}
    score = _float(row.get("priority"), 0.0)
    score += len(flags & NEGATIVE_SIGNAL_FLAGS) * 0.12
    if _clean_text(row.get("region_subtype")) in NEGATIVE_SIGNAL_SUBTYPES:
        score += 0.18
    if "score_below_threshold" in flags:
        score += 0.08
    return score


def _negative_reason_key(row: Mapping[str, Any]) -> str:
    flags = [str(flag) for flag in row.get("quality_flags") or [] if str(flag) in NEGATIVE_SIGNAL_FLAGS]
    if flags:
        return flags[0]
    return _clean_text(row.get("region_subtype")) or "unknown"


def _review_region(row: Mapping[str, Any], *, bucket: str, index: int) -> dict[str, Any]:
    result = dict(row)
    original_region_id = _clean_text(row.get("region_id")) or f"region_{index:03d}"
    prefixes = {
        "expected_effective_text": "pos",
        "recoverable_rejected_text_like": "rec",
        "expected_noise_or_no_text": "neg",
        "overflow_budget_cut": "ovf",
    }
    result["original_region_id"] = original_region_id
    result["region_id"] = f"{prefixes.get(bucket, 'rev')}_{index:03d}_{_safe_identifier(original_region_id)}"
    result["review_sample_bucket"] = bucket
    result["review_sample_rank"] = index
    result["recommended_tools"] = ["ocr"]
    result["expected_information"] = ["drawing_text"]
    if not isinstance(result.get("crop_strategy"), Mapping):
        result["crop_strategy"] = {"highres_scale": 64.0, "padding_ratio": 0.018}
    return result


def build_quality_review_markdown(
    *,
    run_dir: Path,
    pdf_path: Path,
    plan_path: Path,
    rejected_path: Path,
    overflow_path: Path,
    review_regions: Sequence[Mapping[str, Any]],
    highres_report: Mapping[str, Any],
    quality_report: Mapping[str, Any],
) -> str:
    rows = [dict(row) for row in quality_report.get("crop_scores") or []]
    crop_by_region = {
        _clean_text(row.get("region_id")): row
        for row in highres_report.get("crop_manifest") or []
        if _clean_text(row.get("region_id"))
    }
    region_by_id = {_clean_text(row.get("region_id")): dict(row) for row in review_regions}
    high_rows = [row for row in rows if _clean_text(row.get("ocr_quality_label")) == "high"]
    medium_rows = [row for row in rows if _clean_text(row.get("ocr_quality_label")) == "medium"]
    low_rows = [row for row in rows if _clean_text(row.get("ocr_quality_label")) in {"low", "no_text"}]
    profile = quality_report.get("ocr_quality_feedback_profile") or {}

    lines: list[str] = [
        "# OCR 质量样本审阅报告",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 源 PDF: `{pdf_path}`",
        f"- 输入有效候选 plan: `{plan_path}`",
        f"- 输入 rejected 候选: `{rejected_path}`",
        f"- 输入 overflow 候选: `{overflow_path}`",
        f"- 输出目录: `{run_dir}`",
        f"- 审阅样本数: {len(review_regions)}",
        f"- OCR 状态: `{quality_report.get('status')}`",
        f"- OCR 行数: {(quality_report.get('summary') or {}).get('ocr_text_line_count', 0)}",
        f"- 高质量: {len(high_rows)}，中质量: {len(medium_rows)}，低质量/no_text: {len(low_rows)}",
        f"- 反馈画像正样本: {profile.get('positive_sample_count', 0)}，负样本: {profile.get('negative_sample_count', 0)}",
        "",
        "## 判断口径",
        "",
        "- 高质量: OCR 质量分 >= 0.68，且有有效文本行。",
        "- 中质量: OCR 质量分 >= 0.38，但未达到高质量。",
        "- 低质量/no_text: OCR 质量分 < 0.38，或 OCR 没有识别到文本。",
        "- 有效信息信号: 中文、材料代号、尺寸、材料/构造关键词、有效文本行、较高置信度。",
        "- 噪声信号: OCR 无文本、低置信度、单字符/符号、无材料/尺寸/中文有效信息，或来源区域带线条/稀疏/单组件等拒绝标记。",
        "",
        "## 判断信号中文对照",
        "",
        "| 原始字段 | 中文含义 | 说明 |",
        "|---|---|---|",
        "| `ocr_quality_score` | OCR 质量分 | 越高表示文字越可读、有效信息越多 |",
        "| `ocr_quality_label` | OCR 质量标签 | 高质量/中质量/低质量/无文本 |",
        "| `ocr_text_line_count` | OCR 文本行数 | OCR 实际读出的文本行数量 |",
        "| `ocr_useful_line_count` | 有效文本行数 | 中文、材料代号、尺寸或关键词命中的行数 |",
        "| `ocr_noise_line_count` | 噪声文本行数 | 单字符、符号、乱码或信息量低的行数 |",
        "| `ocr_chinese_char_count` | 中文字符数 | OCR 结果中的中文数量 |",
        "| `ocr_material_code_count` | 材料代号数 | 如 PT-01、CT-02、WD-01 等 |",
        "| `ocr_dimension_count` | 尺寸命中数 | 如 600x1200、3900、4200 等 |",
        "| `ocr_material_keyword_count` | 材料/构造关键词数 | 如 材料、做法、墙面、地面、涂料 等 |",
        "| `ocr_avg_confidence` | OCR 平均置信度 | OCR 引擎对文本识别的平均信心 |",
        "| `region_subtype` | 区域类型 | 文字行、文字块、彩色标注、线条噪声等 |",
        "| `page_zone` | 页面区域 | 主图、右侧说明、标题栏、页眉等 |",
        "| `text_density` | 文字密度 | 去掉长线后，小组件在区域内的密度 |",
        "| `component_count` | 小组件数量 | 类似字形笔画/字符碎片的数量 |",
        "| `quality_flags` | 质量/拒绝标记 | 说明区域为什么被选中、拒绝或需要复核 |",
        "",
    ]

    lines.extend(_markdown_section("系统判断为高质量有效文字", high_rows, crop_by_region, region_by_id))
    lines.extend(_markdown_section("系统判断为中质量文字", medium_rows, crop_by_region, region_by_id))
    lines.extend(_markdown_section("系统判断为低质量/no_text 噪声", low_rows, crop_by_region, region_by_id))
    lines.extend(
        [
            "## 供人工复核的输出文件",
            "",
            f"- 质量分 CSV: `{(quality_report.get('outputs') or {}).get('ocr_quality_scores_csv', '')}`",
            f"- OCR 原始行 JSON: `{(quality_report.get('outputs') or {}).get('ocr_quality_rows_json', '')}`",
            f"- 反馈画像 JSON: `{(quality_report.get('outputs') or {}).get('ocr_quality_feedback_profile_json', '')}`",
            f"- highres crop manifest: `{(highres_report.get('outputs') or {}).get('highres_region_manifest_json', '')}`",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_section(
    title: str,
    rows: Sequence[Mapping[str, Any]],
    crop_by_region: Mapping[str, Mapping[str, Any]],
    region_by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        lines.extend(["本轮没有样本。", ""])
        return lines
    for index, row in enumerate(sorted(rows, key=lambda item: -_float(item.get("ocr_quality_score"))), start=1):
        region_id = _clean_text(row.get("region_id"))
        crop = crop_by_region.get(region_id, {})
        region = region_by_id.get(region_id, {})
        features = row.get("source_region_features") if isinstance(row.get("source_region_features"), Mapping) else {}
        flags = row.get("source_region_quality_flags") or []
        preview = [str(item) for item in row.get("ocr_text_preview") or []]
        quality_label = _clean_text(row.get("ocr_quality_label"))
        sample_bucket = _clean_text(region.get("review_sample_bucket"))
        subtype = _clean_text(row.get("region_subtype"))
        planner = _clean_text(row.get("source_region_planner_source"))
        page_zone = _clean_text(features.get("page_zone"))
        lines.extend(
            [
                f"### {index}. `{region_id}`",
                "",
                f"- 样本来源: {SAMPLE_BUCKET_CN.get(sample_bucket, sample_bucket)} (`{sample_bucket}`)；原 region: `{region.get('original_region_id', '')}`",
                f"- 质量标签/分数: {QUALITY_LABEL_CN.get(quality_label, quality_label)} (`{quality_label}`) / `{row.get('ocr_quality_score')}`",
                f"- OCR 信号: 文本行数 {row.get('ocr_text_line_count')}，有效文本行 {row.get('ocr_useful_line_count')}，噪声文本行 {row.get('ocr_noise_line_count')}，平均置信度 {row.get('ocr_avg_confidence')}",
                f"- 内容信号: 中文字符 {row.get('ocr_chinese_char_count')}，材料代号 {row.get('ocr_material_code_count')}，尺寸 {row.get('ocr_dimension_count')}，材料/构造关键词 {row.get('ocr_material_keyword_count')}",
                f"- 区域形态信号: 区域类型 {REGION_SUBTYPE_CN.get(subtype, subtype)} (`{subtype}`)，发现来源 {PLANNER_SOURCE_CN.get(planner, planner)} (`{planner}`)，页面区域 {PAGE_ZONE_CN.get(page_zone, page_zone)} (`{page_zone}`)，宽度 `{features.get('width_px', '')}`，高度 `{features.get('height_px', '')}`，文字密度 `{features.get('text_density', '')}`，小组件数 `{features.get('component_count', '')}`",
                f"- 区域拒绝/质量标记: {_format_quality_flags_cn(flags)}",
                f"- crop 图片: `{crop.get('image_path', row.get('image_path', ''))}`",
                f"- OCR 输入图: `{row.get('ocr_input_image_path', '')}`",
                "- OCR 文本预览:",
            ]
        )
        if preview:
            for text in preview[:12]:
                lines.append(f"  - {text}")
        else:
            lines.append("  - （无 OCR 文本）")
        lines.append("")
    return lines


def _quality_flag_cn(flag: Any) -> str:
    value = _clean_text(flag)
    if not value:
        return ""
    return f"{QUALITY_FLAG_CN.get(value, value)} ({value})"


def _format_quality_flags_cn(flags: Sequence[Any]) -> str:
    translated = [_quality_flag_cn(flag) for flag in flags if _clean_text(flag)]
    return "；".join(translated) if translated else "无"


def _resolve_discovery_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if args.text_region_plan and args.text_region_rejected:
        overflow_path = Path(args.text_region_overflow) if args.text_region_overflow else Path(args.text_region_plan).parent / "text_region_overflow.json"
        return Path(args.text_region_plan), Path(args.text_region_rejected), overflow_path
    run_dir = Path(args.discovery_run_dir) if args.discovery_run_dir else _latest_discovery_run_dir()
    text_regions_dir = run_dir / "text_regions"
    overflow_path = Path(args.text_region_overflow) if args.text_region_overflow else text_regions_dir / "text_region_overflow.json"
    return text_regions_dir / "text_region_plan.json", text_regions_dir / "text_region_rejected.json", overflow_path


def _latest_discovery_run_dir() -> Path:
    root = ROOT / "outputs" / "biz2x_trial" / "text_region_discovery"
    candidates = [
        path
        for path in root.glob("*")
        if path.is_dir()
        and (path / "text_regions" / "text_region_plan.json").is_file()
        and (path / "text_regions" / "text_region_rejected.json").is_file()
    ]
    if not candidates:
        raise FileNotFoundError("No text_region_discovery run with plan and rejected JSON was found.")
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1]


def _write_quality_scores_review_csv(
    path: Path,
    *,
    scores: Sequence[Mapping[str, Any]],
    review_regions: Sequence[Mapping[str, Any]],
) -> None:
    region_by_id = {_clean_text(row.get("region_id")): dict(row) for row in review_regions}
    headers = [
        "region_id",
        "original_region_id",
        "quality_label",
        "quality_label_cn",
        "quality_score",
        "sample_bucket",
        "sample_bucket_cn",
        "region_subtype",
        "region_subtype_cn",
        "page_zone",
        "page_zone_cn",
        "ocr_text_line_count",
        "ocr_useful_line_count",
        "ocr_noise_line_count",
        "ocr_chinese_char_count",
        "ocr_material_code_count",
        "ocr_dimension_count",
        "ocr_material_keyword_count",
        "ocr_avg_confidence",
        "quality_flags",
        "quality_flags_cn",
        "ocr_text_preview",
        "image_path",
        "ocr_input_image_path",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for score in scores:
            region_id = _clean_text(score.get("region_id"))
            region = region_by_id.get(region_id, {})
            features = score.get("source_region_features") if isinstance(score.get("source_region_features"), Mapping) else {}
            flags = score.get("source_region_quality_flags") or []
            quality_label = _clean_text(score.get("ocr_quality_label"))
            sample_bucket = _clean_text(region.get("review_sample_bucket"))
            subtype = _clean_text(score.get("region_subtype"))
            page_zone = _clean_text(features.get("page_zone"))
            writer.writerow(
                {
                    "region_id": region_id,
                    "original_region_id": region.get("original_region_id", ""),
                    "quality_label": quality_label,
                    "quality_label_cn": QUALITY_LABEL_CN.get(quality_label, quality_label),
                    "quality_score": score.get("ocr_quality_score", ""),
                    "sample_bucket": sample_bucket,
                    "sample_bucket_cn": SAMPLE_BUCKET_CN.get(sample_bucket, sample_bucket),
                    "region_subtype": subtype,
                    "region_subtype_cn": REGION_SUBTYPE_CN.get(subtype, subtype),
                    "page_zone": page_zone,
                    "page_zone_cn": PAGE_ZONE_CN.get(page_zone, page_zone),
                    "ocr_text_line_count": score.get("ocr_text_line_count", ""),
                    "ocr_useful_line_count": score.get("ocr_useful_line_count", ""),
                    "ocr_noise_line_count": score.get("ocr_noise_line_count", ""),
                    "ocr_chinese_char_count": score.get("ocr_chinese_char_count", ""),
                    "ocr_material_code_count": score.get("ocr_material_code_count", ""),
                    "ocr_dimension_count": score.get("ocr_dimension_count", ""),
                    "ocr_material_keyword_count": score.get("ocr_material_keyword_count", ""),
                    "ocr_avg_confidence": score.get("ocr_avg_confidence", ""),
                    "quality_flags": "|".join(str(flag) for flag in flags),
                    "quality_flags_cn": "；".join(_quality_flag_cn(flag) for flag in flags),
                    "ocr_text_preview": " | ".join(str(item) for item in score.get("ocr_text_preview") or []),
                    "image_path": score.get("image_path", ""),
                    "ocr_input_image_path": score.get("ocr_input_image_path", ""),
                }
            )


def _write_review_plan_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "region_id",
        "original_region_id",
        "review_sample_bucket",
        "review_sample_bucket_cn",
        "review_sample_rank",
        "source_file",
        "page",
        "priority",
        "selected",
        "region_subtype",
        "region_subtype_cn",
        "bbox_ratio",
        "quality_flags",
        "quality_flags_cn",
        "reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "region_id": row.get("region_id", ""),
                    "original_region_id": row.get("original_region_id", ""),
                    "review_sample_bucket": row.get("review_sample_bucket", ""),
                    "review_sample_bucket_cn": SAMPLE_BUCKET_CN.get(_clean_text(row.get("review_sample_bucket")), _clean_text(row.get("review_sample_bucket"))),
                    "review_sample_rank": row.get("review_sample_rank", ""),
                    "source_file": row.get("source_file", ""),
                    "page": row.get("page", ""),
                    "priority": row.get("priority", ""),
                    "selected": row.get("selected", ""),
                    "region_subtype": row.get("region_subtype", ""),
                    "region_subtype_cn": REGION_SUBTYPE_CN.get(_clean_text(row.get("region_subtype")), _clean_text(row.get("region_subtype"))),
                    "bbox_ratio": json.dumps(row.get("bbox_ratio") or [], ensure_ascii=False),
                    "quality_flags": "|".join(str(flag) for flag in row.get("quality_flags") or []),
                    "quality_flags_cn": "；".join(_quality_flag_cn(flag) for flag in row.get("quality_flags") or []),
                    "reason": row.get("reason", ""),
                }
            )


def _build_fast_pdf_page_parse_report(pdf_path: Path, *, referenced_pages: set[int]) -> dict[str, Any]:
    content = pdf_path.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    page_rows: list[dict[str, Any]] = []
    engine = "pypdf_page_size_only"
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        wanted = referenced_pages or set(range(1, len(reader.pages) + 1))
        for page_index, page in enumerate(reader.pages, start=1):
            if page_index not in wanted:
                continue
            media_box = page.mediabox
            width = float(media_box.width or 0)
            height = float(media_box.height or 0)
            rotation = int(page.get("/Rotate", 0) or 0)
            page_rows.append(
                {
                    "source_file": pdf_path.name,
                    "page": page_index,
                    "width_pt": round(width, 3),
                    "height_pt": round(height, 3),
                    "rotation": rotation,
                    "text_length": 0,
                    "needs_visual_recognition": True,
                    "parse_status": "parsed_page_size_only",
                }
            )
            if len(page_rows) >= len(wanted):
                break
    except Exception:
        engine = "regex_page_size_only"
        page_count = max(referenced_pages) if referenced_pages else (len(re.findall(rb"/Type\s*/Page\b", content)) or 1)
        media_box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*\]", content)
        width = float(media_box.group(1)) if media_box else 595.0
        height = float(media_box.group(2)) if media_box else 842.0
        wanted = referenced_pages or set(range(1, page_count + 1))
        for page_index in sorted(wanted):
            page_rows.append(
                {
                    "source_file": pdf_path.name,
                    "page": page_index,
                    "width_pt": round(width, 3),
                    "height_pt": round(height, 3),
                    "rotation": 0,
                    "text_length": 0,
                    "needs_visual_recognition": True,
                    "parse_status": "parsed_page_size_by_regex",
                }
            )
    return {
        "ok": True,
        "phase": "PDF-2-fast-page-size-parse",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dependency_status": {},
        "summary": {
            "pdf_file_count": 1,
            "page_count": len(page_rows),
            "text_row_count": 0,
            "text_page_count": 0,
            "scanned_or_visual_page_count": len(page_rows),
            "parse_engine": engine,
        },
        "file_rows": [
            {
                "file_name": pdf_path.name,
                "path": str(pdf_path.resolve()),
                "size_bytes": pdf_path.stat().st_size,
                "sha256": file_hash,
                "page_count": len(page_rows),
                "parse_engine": engine,
                "parse_status": "parsed_page_size_only",
            }
        ],
        "page_rows": page_rows,
        "text_rows": [],
    }


def _safe_identifier(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return text[:48]


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


def _bbox_top(value: Any) -> float:
    return float(value[1]) if isinstance(value, Sequence) and len(value) >= 2 else 0.0


def _bbox_left(value: Any) -> float:
    return float(value[0]) if isinstance(value, Sequence) and value else 0.0


if __name__ == "__main__":
    main()
