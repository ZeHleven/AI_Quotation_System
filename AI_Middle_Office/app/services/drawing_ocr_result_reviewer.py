from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "drawing_ocr_result_review_v1"

QUALITY_LABEL_CN = {
    "high": "高质量有效文字",
    "medium": "中等质量可疑文字",
    "low": "低质量文字/噪声",
    "no_text": "无 OCR 文本",
}

EFFECTIVENESS_LABEL_CN = {
    "effective_material_text": "有效材料/做法文字",
    "possible_material_text": "可能有效材料文字",
    "useful_non_material_text": "有效但非材料优先文字",
    "uncertain_text": "可疑文字，需复核",
    "low_value_noise": "低价值文字/噪声",
    "no_text_noise": "无文本噪声",
    "not_scored": "未 OCR 评分",
}

STRICT_MATERIAL_KEYWORD_RE = re.compile(
    r"(材料名称|材料|材质|做法|饰面|面层|基层|墙面|地面|顶面|天花|吊顶|"
    r"瓷砖|地砖|墙砖|石材|木饰面|木作|乳胶漆|涂料|油漆|玻璃|不锈钢|"
    r"地毯|踢脚|门槛|窗台|灯槽|窗帘盒|拆除|新建|安装)",
    re.IGNORECASE,
)
LEGEND_KEYWORD_RE = re.compile(r"(图例|图列|材料表|材料名称|编号|名称|规格|备注)")
WEAK_NON_MATERIAL_RE = re.compile(r"(建设单位|设计单位|证书|资质|地址|电话|传真|公司|CONSTRUCTION)", re.IGNORECASE)


def build_ocr_result_review_report(
    *,
    execution_plan: Mapping[str, Any],
    quality_report: Mapping[str, Any],
    output_dir: str | Path,
    business_screenshot_dir: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    regions = [dict(row) for row in execution_plan.get("regions") or [] if isinstance(row, Mapping)]
    scores_by_region = {
        _clean_text(row.get("region_id")): dict(row)
        for row in quality_report.get("crop_scores") or []
        if isinstance(row, Mapping) and _clean_text(row.get("region_id"))
    }
    review_rows = [_review_row(region, scores_by_region.get(_clean_text(region.get("region_id")))) for region in regions]
    summary = _summary(review_rows, execution_plan=execution_plan, quality_report=quality_report)
    outputs = _write_outputs(
        directory=directory,
        summary=summary,
        rows=review_rows,
        business_screenshot_dir=business_screenshot_dir,
    )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "rows": review_rows,
        "outputs": outputs,
    }


def _review_row(region: Mapping[str, Any], score: Mapping[str, Any] | None) -> dict[str, Any]:
    score = dict(score or {})
    preview = [str(item) for item in score.get("ocr_text_preview") or [] if _clean_text(item)]
    joined = "\n".join(preview)
    material_signal = _material_signal(score, joined_text=joined)
    label = _effectiveness_label(score, material_signal=material_signal, joined_text=joined)
    return {
        "region_id": _clean_text(region.get("region_id")),
        "original_region_id": _clean_text(region.get("original_region_id")),
        "source_file": _clean_text(region.get("source_file")),
        "page": _int(region.get("page")),
        "ocr_execution_bucket": _clean_text(region.get("ocr_execution_bucket")),
        "ocr_execution_bucket_cn": _clean_text(region.get("ocr_execution_bucket_cn")),
        "budget_bucket": _clean_text(region.get("budget_bucket")),
        "budget_bucket_cn": _clean_text(region.get("budget_bucket_cn")),
        "candidate_decision_cn": _clean_text(region.get("candidate_decision_cn")),
        "candidate_reason_cn": _clean_text(region.get("candidate_reason_cn")),
        "candidate_signal_cn": _clean_text(region.get("candidate_signal_cn")),
        "candidate_risk_cn": _clean_text(region.get("candidate_risk_cn")),
        "candidate_next_action_cn": _clean_text(region.get("next_action_cn")),
        "ocr_quality_label": _clean_text(score.get("ocr_quality_label")) or "not_scored",
        "ocr_quality_label_cn": QUALITY_LABEL_CN.get(_clean_text(score.get("ocr_quality_label")), "未 OCR 评分"),
        "ocr_quality_score": score.get("ocr_quality_score", ""),
        "ocr_text_line_count": _int(score.get("ocr_text_line_count")),
        "ocr_useful_line_count": _int(score.get("ocr_useful_line_count")),
        "ocr_noise_line_count": _int(score.get("ocr_noise_line_count")),
        "ocr_chinese_char_count": _int(score.get("ocr_chinese_char_count")),
        "ocr_material_code_count": _int(score.get("ocr_material_code_count")),
        "ocr_dimension_count": _int(score.get("ocr_dimension_count")),
        "ocr_material_keyword_count": _int(score.get("ocr_material_keyword_count")),
        "ocr_avg_confidence": score.get("ocr_avg_confidence", ""),
        "ocr_text_preview": preview,
        "ocr_result_summary_cn": _ocr_result_summary_cn(score, material_signal=material_signal, preview=preview),
        "ocr_effectiveness_label": label,
        "ocr_effectiveness_label_cn": EFFECTIVENESS_LABEL_CN.get(label, label),
        "material_signal_cn": _material_signal_cn(material_signal),
        "noise_reason_cn": _noise_reason_cn(score, label=label, joined_text=joined),
        "feedback_action_cn": _feedback_action_cn(region, score, label=label, material_signal=material_signal),
        "image_path": _clean_text(score.get("image_path")),
        "ocr_input_image_path": _clean_text(score.get("ocr_input_image_path")),
    }


def _material_signal(score: Mapping[str, Any], *, joined_text: str) -> dict[str, Any]:
    strict_keywords = sorted(set(STRICT_MATERIAL_KEYWORD_RE.findall(joined_text)))
    legend_keywords = sorted(set(LEGEND_KEYWORD_RE.findall(joined_text)))
    weak_non_material = sorted(set(WEAK_NON_MATERIAL_RE.findall(joined_text)))
    material_code_count = _int(score.get("ocr_material_code_count"))
    dimension_count = _int(score.get("ocr_dimension_count"))
    chinese_count = _int(score.get("ocr_chinese_char_count"))
    useful_line_count = _int(score.get("ocr_useful_line_count"))
    if strict_keywords:
        strength = 3 + min(2, len(strict_keywords)) + min(2, material_code_count) + (1 if dimension_count > 0 else 0)
    elif legend_keywords and (material_code_count > 0 or dimension_count > 0):
        strength = 2 + min(2, material_code_count) + (1 if dimension_count > 0 else 0)
    elif material_code_count > 0 and not weak_non_material:
        strength = min(3, material_code_count * 2)
        if dimension_count >= 8:
            strength = max(0, strength - 2)
    else:
        strength = 0
    if weak_non_material and not strict_keywords:
        strength = max(0, strength - 2)
    return {
        "strict_keywords": strict_keywords,
        "legend_keywords": legend_keywords,
        "weak_non_material_keywords": weak_non_material,
        "material_code_count": material_code_count,
        "dimension_count": dimension_count,
        "chinese_count": chinese_count,
        "useful_line_count": useful_line_count,
        "strength": strength,
    }


def _effectiveness_label(score: Mapping[str, Any], *, material_signal: Mapping[str, Any], joined_text: str) -> str:
    quality_label = _clean_text(score.get("ocr_quality_label"))
    if not score:
        return "not_scored"
    if quality_label == "no_text" or _int(score.get("ocr_text_line_count")) <= 0:
        return "no_text_noise"
    strength = _int(material_signal.get("strength"))
    if quality_label in {"high", "medium"} and strength >= 4:
        return "effective_material_text"
    if quality_label in {"high", "medium"} and strength >= 2:
        return "possible_material_text"
    if quality_label in {"high", "medium"} and (
        _int(score.get("ocr_chinese_char_count")) >= 12 or _int(score.get("ocr_useful_line_count")) >= 4
    ):
        return "useful_non_material_text"
    if quality_label == "low" and strength >= 2:
        return "uncertain_text"
    if quality_label == "low":
        return "low_value_noise"
    return "uncertain_text" if joined_text.strip() else "no_text_noise"


def _ocr_result_summary_cn(score: Mapping[str, Any], *, material_signal: Mapping[str, Any], preview: Sequence[str]) -> str:
    if not score:
        return "本区域尚未 OCR 或未产生评分。"
    label_cn = QUALITY_LABEL_CN.get(_clean_text(score.get("ocr_quality_label")), "未 OCR 评分")
    parts = [
        f"OCR 判断为{label_cn}",
        f"读出 {_int(score.get('ocr_text_line_count'))} 行文本",
        f"其中有效文本行 {_int(score.get('ocr_useful_line_count'))} 行",
        f"中文 {_int(score.get('ocr_chinese_char_count'))} 个",
        f"材料代号 {_int(score.get('ocr_material_code_count'))} 个",
        f"尺寸 {_int(score.get('ocr_dimension_count'))} 个",
    ]
    if preview:
        parts.append("预览：" + " / ".join(str(item) for item in preview[:4]))
    return "；".join(parts)


def _material_signal_cn(signal: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if signal.get("strict_keywords"):
        parts.append("命中材料/做法关键词：" + "、".join(str(item) for item in signal.get("strict_keywords") or []))
    if _int(signal.get("material_code_count")):
        parts.append(f"命中材料代号 {_int(signal.get('material_code_count'))} 个")
    if _int(signal.get("dimension_count")):
        parts.append(f"命中尺寸 {_int(signal.get('dimension_count'))} 个")
    if signal.get("legend_keywords"):
        parts.append("命中图例/材料表线索：" + "、".join(str(item) for item in signal.get("legend_keywords") or []))
    if signal.get("weak_non_material_keywords"):
        parts.append("同时命中非材料弱信号：" + "、".join(str(item) for item in signal.get("weak_non_material_keywords") or []))
    return "；".join(parts) if parts else "未命中明确材料/做法信号"


def _noise_reason_cn(score: Mapping[str, Any], *, label: str, joined_text: str) -> str:
    if label == "not_scored":
        return "未 OCR 评分，暂不能判断噪声。"
    if label == "no_text_noise":
        return "OCR 未读出文本，可视为无文本噪声或需重新裁剪。"
    if label == "low_value_noise":
        return "OCR 质量低，缺少中文、材料代号、尺寸或材料关键词，倾向轴号/数字/碎片字符。"
    if label == "useful_non_material_text":
        return "OCR 文本较清晰，但主要像设计单位、标题、证书或普通图纸信息，不应直接作为材料正样本。"
    if label == "uncertain_text":
        return "存在少量有效信号但不足以确认材料文字，需要人工复核。"
    if WEAK_NON_MATERIAL_RE.search(joined_text) and not STRICT_MATERIAL_KEYWORD_RE.search(joined_text):
        return "包含较多设计单位/证书/公司信息，材料属性不强。"
    return "暂无明显噪声原因。"


def _feedback_action_cn(
    region: Mapping[str, Any],
    score: Mapping[str, Any],
    *,
    label: str,
    material_signal: Mapping[str, Any],
) -> str:
    bucket = _clean_text(region.get("ocr_execution_bucket"))
    if label in {"effective_material_text", "possible_material_text"}:
        if bucket == "fallback_overflow_budget_cut":
            return "下轮提高同类 overflow 区域优先级，并保留为材料文字正样本候选。"
        if bucket == "fallback_recoverable_rejected":
            return "下轮放宽同类 rejected 召回条件，并进入人工复核正样本候选。"
        return "下轮保持或提高同类区域优先级，并进入材料文字正样本候选。"
    if label == "useful_non_material_text":
        return "保留为有效图纸文字，但不要作为材料正样本；后续语义分类时降低材料权重。"
    if label == "uncertain_text":
        return "进入人工复核；复核后决定提权或降权。"
    if bucket == "fallback_recoverable_rejected":
        return "下轮降低同类误拒绝兜底比例，作为低价值负样本参考。"
    if bucket == "fallback_overflow_budget_cut":
        return "下轮保持少量 overflow 抽样，但降低同类低质量形态优先级。"
    return "下轮降低同类区域优先级，作为噪声/负样本参考。"


def _summary(rows: Sequence[Mapping[str, Any]], *, execution_plan: Mapping[str, Any], quality_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "review_row_count": len(rows),
        "effectiveness_counts": dict(Counter(_clean_text(row.get("ocr_effectiveness_label")) for row in rows)),
        "quality_label_counts": dict(Counter(_clean_text(row.get("ocr_quality_label")) for row in rows)),
        "execution_bucket_counts": dict(Counter(_clean_text(row.get("ocr_execution_bucket")) for row in rows)),
        "effective_material_count": sum(1 for row in rows if row.get("ocr_effectiveness_label") == "effective_material_text"),
        "possible_material_count": sum(1 for row in rows if row.get("ocr_effectiveness_label") == "possible_material_text"),
        "noise_or_low_value_count": sum(1 for row in rows if row.get("ocr_effectiveness_label") in {"low_value_noise", "no_text_noise"}),
        "source_execution_summary": execution_plan.get("summary") or {},
        "source_ocr_quality_summary": quality_report.get("summary") or {},
    }


def _write_outputs(
    directory: Path,
    *,
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    business_screenshot_dir: str | Path | None = None,
) -> dict[str, str]:
    summary_path = directory / "ocr_result_review_summary.json"
    rows_json_path = directory / "ocr_result_review.json"
    csv_path = directory / "ocr_result_review.csv"
    md_path = directory / "ocr_result_review.md"
    business_csv_path = directory / "ocr_result_business_review.csv"
    business_md_path = directory / "ocr_result_business_review.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    rows_json_path.write_text(json.dumps(list(rows), ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_path, rows)
    md_path.write_text(_build_markdown(summary=summary, rows=rows), encoding="utf-8")
    business_rows = _prepare_business_rows(rows, business_screenshot_dir=business_screenshot_dir)
    _write_business_csv(business_csv_path, business_rows)
    business_md_path.write_text(_build_business_markdown(summary=summary, rows=business_rows), encoding="utf-8")
    outputs = {
        "ocr_result_review_summary_json": str(summary_path.resolve()),
        "ocr_result_review_json": str(rows_json_path.resolve()),
        "ocr_result_review_csv": str(csv_path.resolve()),
        "ocr_result_review_markdown": str(md_path.resolve()),
        "ocr_result_business_review_csv": str(business_csv_path.resolve()),
        "ocr_result_business_review_markdown": str(business_md_path.resolve()),
    }
    if business_screenshot_dir:
        outputs["ocr_result_business_screenshot_dir"] = str(Path(business_screenshot_dir).resolve())
    return outputs


def _prepare_business_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    business_screenshot_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    prepared = [dict(row) for row in rows]
    if not business_screenshot_dir:
        return prepared
    asset_dir = Path(business_screenshot_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(prepared, start=1):
        source = Path(_clean_text(row.get("image_path")))
        if not source.is_file():
            row["business_screenshot_path"] = row.get("image_path") or ""
            continue
        target_name = f"{index:03d}_{_safe_filename(row.get('region_id'))}{source.suffix.lower()}"
        target = asset_dir / target_name
        shutil.copy2(source, target)
        row["business_screenshot_path"] = str(target.resolve())
    return prepared


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "region_id",
        "original_region_id",
        "ocr_execution_bucket",
        "ocr_execution_bucket_cn",
        "budget_bucket_cn",
        "candidate_decision_cn",
        "candidate_reason_cn",
        "candidate_signal_cn",
        "candidate_risk_cn",
        "candidate_next_action_cn",
        "ocr_quality_label",
        "ocr_quality_label_cn",
        "ocr_quality_score",
        "ocr_effectiveness_label",
        "ocr_effectiveness_label_cn",
        "ocr_result_summary_cn",
        "material_signal_cn",
        "noise_reason_cn",
        "feedback_action_cn",
        "ocr_text_line_count",
        "ocr_useful_line_count",
        "ocr_noise_line_count",
        "ocr_chinese_char_count",
        "ocr_material_code_count",
        "ocr_dimension_count",
        "ocr_material_keyword_count",
        "ocr_text_preview",
        "image_path",
        "ocr_input_image_path",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in headers})


def _write_business_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "序号",
        "系统结论",
        "建议处理",
        "识别到的文字",
        "系统判断依据",
        "人工确认",
        "人工备注",
        "截图路径",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            writer.writerow(
                {
                    "序号": index,
                    "系统结论": row.get("ocr_effectiveness_label_cn") or "",
                    "建议处理": _business_action_cn(row),
                    "识别到的文字": _business_preview(row),
                    "系统判断依据": _business_reason_cn(row),
                    "人工确认": "",
                    "人工备注": "",
                    "截图路径": _business_screenshot_path(row),
                }
            )


def _build_markdown(*, summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# OCR 结果有效性中文审阅表",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 审阅区域数: {summary.get('review_row_count', 0)}",
        f"- 有效材料/做法文字: {summary.get('effective_material_count', 0)}",
        f"- 可能有效材料文字: {summary.get('possible_material_count', 0)}",
        f"- 低价值/噪声: {summary.get('noise_or_low_value_count', 0)}",
        "",
        "## 分类统计",
        "",
        "```json",
        json.dumps(summary.get("effectiveness_counts") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    for title, labels in [
        ("有效或可能有效材料文字", {"effective_material_text", "possible_material_text"}),
        ("有效但非材料优先文字", {"useful_non_material_text"}),
        ("可疑或低价值文字", {"uncertain_text", "low_value_noise", "no_text_noise"}),
    ]:
        lines.extend([f"## {title}", ""])
        bucket_rows = [row for row in rows if _clean_text(row.get("ocr_effectiveness_label")) in labels]
        if not bucket_rows:
            lines.extend(["本轮没有样本。", ""])
            continue
        for index, row in enumerate(bucket_rows[:20], start=1):
            preview = row.get("ocr_text_preview") if isinstance(row.get("ocr_text_preview"), Sequence) else []
            lines.extend(
                [
                    f"### {index}. `{row.get('region_id')}`",
                    "",
                    f"- 来源: {row.get('ocr_execution_bucket_cn')}；原区域: `{row.get('original_region_id')}`",
                    f"- 判断: {row.get('ocr_effectiveness_label_cn')}；质量: {row.get('ocr_quality_label_cn')} / `{row.get('ocr_quality_score')}`",
                    f"- OCR 概要: {row.get('ocr_result_summary_cn')}",
                    f"- 材料信号: {row.get('material_signal_cn')}",
                    f"- 噪声原因: {row.get('noise_reason_cn')}",
                    f"- 下轮动作: {row.get('feedback_action_cn')}",
                    f"- crop: `{row.get('image_path')}`",
                    "- OCR 预览:",
                ]
            )
            if preview:
                for text in preview[:8]:
                    lines.append(f"  - {text}")
            else:
                lines.append("  - （无 OCR 文本）")
            lines.append("")
    return "\n".join(lines)


def _build_business_markdown(*, summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# OCR 结果业务审阅简表",
        "",
        f"- 审阅区域数: {summary.get('review_row_count', 0)}",
        f"- 建议纳入材料/做法候选: {summary.get('effective_material_count', 0)}",
        f"- 低价值/噪声: {summary.get('noise_or_low_value_count', 0)}",
        "",
        "> 人工确认建议填写：正确 / 误判 / 不确定。",
        "",
        "| 序号 | 系统结论 | 建议处理 | 识别到的文字 | 系统判断依据 | 人工确认 |",
        "|---:|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _md_cell(row.get("ocr_effectiveness_label_cn") or ""),
                    _md_cell(_business_action_cn(row)),
                    _md_cell(_business_preview(row)),
                    _md_cell(_business_reason_cn(row)),
                    "",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 截图路径",
            "",
        ]
    )
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. `{_business_screenshot_path(row)}`")
    return "\n".join(lines)


def _business_action_cn(row: Mapping[str, Any]) -> str:
    label = _clean_text(row.get("ocr_effectiveness_label"))
    if label == "effective_material_text":
        return "纳入材料/做法候选"
    if label == "possible_material_text":
        return "人工确认后再纳入"
    if label == "useful_non_material_text":
        return "暂不纳入材料候选，仅作图纸上下文"
    if label in {"low_value_noise", "no_text_noise"}:
        return "作为噪声降权"
    if label == "not_scored":
        return "待 OCR 后再判断"
    return "人工复核"


def _business_preview(row: Mapping[str, Any]) -> str:
    preview = row.get("ocr_text_preview")
    if not isinstance(preview, Sequence) or isinstance(preview, (str, bytes)):
        return "无 OCR 文本"
    texts = [_clean_text(item) for item in preview if _clean_text(item)]
    priority_texts = [
        text for text in texts if STRICT_MATERIAL_KEYWORD_RE.search(text) or LEGEND_KEYWORD_RE.search(text)
    ]
    selected: list[str] = []
    for text in [*priority_texts, *texts]:
        if text not in selected:
            selected.append(text)
        if len(selected) >= 6:
            break
    return "；".join(selected) if selected else "无 OCR 文本"


def _business_reason_cn(row: Mapping[str, Any]) -> str:
    label = _clean_text(row.get("ocr_effectiveness_label"))
    material_signal = _clean_text(row.get("material_signal_cn"))
    noise_reason = _clean_text(row.get("noise_reason_cn"))
    if label in {"effective_material_text", "possible_material_text"}:
        return material_signal or "命中材料/做法相关文字"
    if label == "useful_non_material_text":
        return noise_reason or "文字清晰，但不像报价材料"
    if label in {"low_value_noise", "no_text_noise"}:
        return noise_reason or "OCR 结果无有效材料价值"
    if label == "not_scored":
        return "尚未 OCR 评分"
    return noise_reason or material_signal or "需要人工判断"


def _business_screenshot_path(row: Mapping[str, Any]) -> str:
    return _clean_text(row.get("business_screenshot_path")) or _clean_text(row.get("image_path"))


def _safe_filename(value: Any) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", _clean_text(value))
    return text.strip("._") or "crop"


def _md_cell(value: Any) -> str:
    text = _clean_text(value)
    return text.replace("|", "｜").replace("\n", " / ")


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return " | ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False)
    return value if value is not None else ""


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
