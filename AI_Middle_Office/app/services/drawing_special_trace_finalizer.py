from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from app.services import drawing_quantity_confirmation as confirmation
from app.services.drawing_standard_rule_executor import READY_STATUS


PHASE = "BIZ-2x-special-trace-finalization"
ADOPT_VALUE = "是"
PASS_VALUE = "通过"
DEDUCTION_REVIEW_COLUMN = "扣减/合并规则复核"
SPECIAL_TRACE_ID_COLUMN = "专项算量编号"

ISSUE_HEADERS = ["复核序号", "专项算量编号", "问题说明", "处理建议"]
SKIPPED_HEADERS = ["复核序号", "专项算量编号", "是否采用", "跳过原因"]


def build_special_trace_confirmation_pack(special_quantity_report: Mapping[str, Any]) -> dict[str, Any]:
    confirmation_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for index, trace in enumerate(_trace_rows(special_quantity_report), start=1):
        row_id = f"BIZ2xSQ6-{index:04d}"
        ready = _trace_ready(trace)
        confirmation_row = _to_confirmation_row(
            row_id,
            trace,
            review={
                confirmation.ADOPT_COLUMN: "待确认" if ready else "否",
                confirmation.REVIEW_COLUMN: "",
                confirmation.MANUAL_QUANTITY_COLUMN: trace.get("建议工程量") if ready else "",
                confirmation.MANUAL_UNIT_COLUMN: trace.get("建议单位") if ready else "",
                confirmation.QUANTITY_SOURCE_COLUMN: _default_quantity_source(trace) if ready else "",
                confirmation.MANUAL_NAME_COLUMN: trace.get("项目名称", ""),
                confirmation.MANUAL_FEATURE_COLUMN: _project_feature_text(trace),
            },
        )
        if not ready:
            confirmation_row["待补量原因"] = trace.get("阻断原因", "")
        confirmation_rows.append(confirmation_row)
        feature_rows.extend(_to_feature_rows(row_id, trace, _project_feature_text(trace), status="pending_special_trace_review"))
        evidence_rows.append(_to_evidence_row(row_id, trace, _clean_text(trace.get(DEDUCTION_REVIEW_COLUMN))))

    ready_count = sum(1 for row in _trace_rows(special_quantity_report) if _trace_ready(row))
    return {
        "ok": True,
        "phase": "BIZ-2x-special-trace-confirmation-pack",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "source_special_trace_count": len(_trace_rows(special_quantity_report)),
            "ready_special_trace_count": ready_count,
            "blocked_special_trace_count": len(_trace_rows(special_quantity_report)) - ready_count,
            "confirmation_row_count": len(confirmation_rows),
            "feature_detail_count": len(feature_rows),
            "evidence_detail_count": len(evidence_rows),
            "source_phase": "BIZ-2x-special-quantity-calculation-trace",
            "final_export_requires_manual_confirmation": True,
        },
        "confirmation_rows": confirmation_rows,
        "feature_rows": feature_rows,
        "evidence_rows": evidence_rows,
    }


def build_special_trace_finalization(
    listing_or_special_report: Mapping[str, Any],
    reviews: list[Mapping[str, Any]],
) -> dict[str, Any]:
    trace_lookup = {
        _clean_text(row.get(SPECIAL_TRACE_ID_COLUMN)): row
        for row in _trace_rows(listing_or_special_report)
        if _clean_text(row.get(SPECIAL_TRACE_ID_COLUMN))
    }
    confirmation_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    for index, review in enumerate(reviews, start=1):
        trace_id = _review_trace_id(review)
        adopt = _review_value(review, confirmation.ADOPT_COLUMN, "adopt", "是否采用")
        if not _is_yes(adopt):
            skipped_rows.append(_skipped(index, trace_id, adopt, "业务未确认采用"))
            continue
        trace = trace_lookup.get(trace_id)
        row_issues: list[str] = []
        if not trace:
            row_issues.append("未找到对应专项算量 trace")
        elif trace_id in used_ids:
            row_issues.append("同一专项算量 trace 不能重复采用")
        else:
            row_issues.extend(_validate_trace_for_final(trace))
            row_issues.extend(_validate_review_for_final(review, trace))

        if row_issues:
            issues.append(_issue(index, trace_id, "；".join(row_issues), "补齐复核信息或改选专项 trace 后重新提交"))
            continue

        used_ids.add(trace_id)
        row_id = f"BIZ2xSQFINAL-{len(confirmation_rows) + 1:04d}"
        confirmation_row = _to_confirmation_row(row_id, trace, review=review)
        confirmation_rows.append(confirmation_row)
        feature_rows.extend(
            _to_feature_rows(
                row_id,
                trace,
                _review_value(review, confirmation.MANUAL_FEATURE_COLUMN, "project_feature", "项目特征")
                or _project_feature_text(trace),
                status="confirmed_from_special_trace_review",
            )
        )
        evidence_rows.append(_to_evidence_row(row_id, trace, _review_deduction_review(review)))

    confirmation_pack = {
        "ok": True,
        "phase": "BIZ-2x-special-trace-converted-biz2x6-confirmation-pack",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "confirmation_row_count": len(confirmation_rows),
            "feature_detail_count": len(feature_rows),
            "evidence_detail_count": len(evidence_rows),
            "source_phase": PHASE,
            "final_export_requires_manual_confirmation": False,
        },
        "confirmation_rows": confirmation_rows,
        "feature_rows": feature_rows,
        "evidence_rows": evidence_rows,
    }
    validation = confirmation.validate_confirmation_rows(confirmation_rows)
    validation["phase"] = "BIZ-2x-special-trace-final-confirmation-validation"
    validation["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    validation_issues = _validation_issues_as_business_issues(validation)
    all_issues = [*issues, *validation_issues]
    return {
        "ok": bool(confirmation_rows) and not all_issues and validation.get("ok") is True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": bool(validation.get("ok")) and not all_issues,
        "summary": {
            "review_row_count": len(reviews),
            "source_special_trace_count": len(trace_lookup),
            "adopted_review_count": sum(1 for item in reviews if _is_yes(_review_value(item, confirmation.ADOPT_COLUMN, "adopt", "是否采用"))),
            "converted_confirmation_row_count": len(confirmation_rows),
            "conversion_issue_count": len(issues),
            "validation_issue_count": len(validation_issues),
            "skipped_row_count": len(skipped_rows),
            "biz2x6_validation_ok": bool(validation.get("ok")) and not validation_issues,
            "final_ready_count": validation.get("summary", {}).get("adopted_final_row_count", 0) if not all_issues else 0,
        },
        "issues": all_issues,
        "skipped_rows": skipped_rows,
        "confirmation_pack": confirmation_pack,
        "confirmation_validation": validation,
    }


def write_special_trace_finalization_outputs(
    finalization: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_DWG专项trace生成最终清单_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    issue_csv_path = directory / f"{file_stem}_问题.csv"
    skipped_csv_path = directory / f"{file_stem}_跳过.csv"
    confirmation_csv_path = directory / f"{file_stem}_BIZ2x6确认行.csv"

    json_path.write_text(json.dumps(finalization, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_special_trace_finalization_markdown(finalization), encoding="utf-8")
    _write_csv(issue_csv_path, list(finalization.get("issues") or []), ISSUE_HEADERS)
    _write_csv(skipped_csv_path, list(finalization.get("skipped_rows") or []), SKIPPED_HEADERS)
    confirmation_rows = list((finalization.get("confirmation_pack") or {}).get("confirmation_rows") or [])
    _write_csv(confirmation_csv_path, confirmation_rows, confirmation.CONFIRMATION_HEADERS)

    outputs = {
        "json": str(json_path),
        "markdown": str(md_path),
        "issue_csv": str(issue_csv_path),
        "skipped_csv": str(skipped_csv_path),
        "converted_confirmation_csv": str(confirmation_csv_path),
    }
    if confirmation_rows:
        confirmation_outputs = confirmation.write_confirmation_outputs(
            finalization.get("confirmation_pack") or {},
            directory,
            stem=f"{file_stem}_BIZ2x6确认行",
        )
        outputs.update({f"confirmation_{key}": value for key, value in confirmation_outputs.items()})
        validation_outputs = confirmation.write_validation_report(
            finalization.get("confirmation_validation") or {},
            directory,
            stem=f"{file_stem}_BIZ2x6确认行校验",
        )
        outputs.update({f"validation_{key}": value for key, value in validation_outputs.items()})
    return outputs


def build_special_trace_finalization_markdown(finalization: Mapping[str, Any]) -> str:
    summary = finalization.get("summary", {})
    lines = [
        "# BIZ-2x 专项 trace 生成最终四字段清单",
        "",
        f"- 生成时间：{finalization.get('generated_at', '-')}",
        f"- 复核行：{summary.get('review_row_count', 0)}",
        f"- 采用复核行：{summary.get('adopted_review_count', 0)}",
        f"- 转确认行：{summary.get('converted_confirmation_row_count', 0)}",
        f"- 转换问题：{summary.get('conversion_issue_count', 0)}",
        f"- BIZ-2x-6 校验是否通过：{'是' if summary.get('biz2x6_validation_ok') else '否'}",
        f"- 最终四字段可导出行：{summary.get('final_ready_count', 0)}",
        "",
        "## 导出条件",
        "",
        "- 专项 trace 必须是可复核状态。",
        "- 标准规则执行状态必须通过。",
        "- 业务必须确认采用并填写“核验结论=通过”。",
        "- 项目特征不能保留“待确认/待补/缺失”等占位。",
        "- 扣减/合并规则复核必须填写。",
        "- 最终 Excel 复用 BIZ-2x-6 四字段校验。",
    ]
    if finalization.get("issues"):
        lines.extend(["", "## 前 10 条问题", ""])
        for issue in list(finalization.get("issues") or [])[:10]:
            lines.append(f"- {issue.get(SPECIAL_TRACE_ID_COLUMN, '')}：{issue.get('问题说明', '')}")
    return "\n".join(lines) + "\n"


def _trace_rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(report.get("special_quantity_trace_rows") or [])


def _trace_ready(trace: Mapping[str, Any]) -> bool:
    return (
        _clean_text(trace.get("trace状态")) == "special_quantity_trace_ready_for_manual_review"
        and _clean_text(trace.get("是否可复核")) == "是"
        and _clean_text(trace.get("标准规则执行状态")) == READY_STATUS
        and _parse_positive_decimal(trace.get("建议工程量")) is not None
    )


def _validate_trace_for_final(trace: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if _clean_text(trace.get("trace状态")) != "special_quantity_trace_ready_for_manual_review":
        issues.append("专项 trace 不是可复核状态")
    if _clean_text(trace.get("是否可复核")) != "是":
        issues.append("专项 trace 未标记为可复核")
    if _clean_text(trace.get("标准规则执行状态")) != READY_STATUS:
        issues.append("标准规则执行状态未通过")
    if _parse_positive_decimal(trace.get("建议工程量")) is None:
        issues.append("专项 trace 建议工程量为空或不大于 0")
    if not _clean_text(trace.get("建议单位")):
        issues.append("专项 trace 建议单位为空")
    if not _clean_text(trace.get("标准工程量计算规则")):
        issues.append("缺少标准工程量计算规则")
    return issues


def _validate_review_for_final(review: Mapping[str, Any], trace: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if _review_value(review, confirmation.REVIEW_COLUMN, "review_result", "核验结论") != PASS_VALUE:
        issues.append("核验结论必须填写“通过”")
    feature_text = _review_value(review, confirmation.MANUAL_FEATURE_COLUMN, "project_feature", "项目特征") or _project_feature_text(trace)
    if not feature_text:
        issues.append("项目特征不能为空")
    if any(marker in feature_text for marker in ("待确认", "待补", "缺失", "missing_needs_manual_review")):
        issues.append("项目特征仍包含待确认/待补/缺失提示")
    if not _review_deduction_review(review):
        issues.append("扣减/合并规则复核不能为空")
    quantity = _review_value(review, confirmation.MANUAL_QUANTITY_COLUMN, "quantity", "工程量") or trace.get("建议工程量")
    if _parse_positive_decimal(quantity) is None:
        issues.append("确认工程量必须大于 0")
    unit = _review_value(review, confirmation.MANUAL_UNIT_COLUMN, "unit", "单位") or trace.get("建议单位")
    if not _clean_text(unit):
        issues.append("确认单位不能为空")
    return issues


def _to_confirmation_row(row_id: str, trace: Mapping[str, Any], *, review: Mapping[str, Any]) -> dict[str, Any]:
    quantity = _review_value(review, confirmation.MANUAL_QUANTITY_COLUMN, "quantity", "工程量") or trace.get("建议工程量")
    unit = _review_value(review, confirmation.MANUAL_UNIT_COLUMN, "unit", "单位") or trace.get("建议单位")
    project_name = _review_value(review, confirmation.MANUAL_NAME_COLUMN, "project_name", "项目名称") or trace.get("项目名称")
    feature_text = _review_value(review, confirmation.MANUAL_FEATURE_COLUMN, "project_feature", "项目特征") or _project_feature_text(trace)
    source = _review_value(review, confirmation.QUANTITY_SOURCE_COLUMN, "quantity_source_note", "工程量来源说明") or _default_quantity_source(trace)
    return {
        "确认行号": row_id,
        confirmation.ADOPT_COLUMN: _review_value(review, confirmation.ADOPT_COLUMN, "adopt", "是否采用") or "待确认",
        confirmation.REVIEW_COLUMN: _review_value(review, confirmation.REVIEW_COLUMN, "review_result", "核验结论"),
        confirmation.MANUAL_QUANTITY_COLUMN: _clean_text(quantity),
        confirmation.MANUAL_UNIT_COLUMN: _clean_text(unit),
        confirmation.QUANTITY_SOURCE_COLUMN: source,
        confirmation.MANUAL_NAME_COLUMN: _clean_text(project_name),
        confirmation.MANUAL_FEATURE_COLUMN: feature_text,
        confirmation.ISSUE_COLUMN: _review_value(review, confirmation.ISSUE_COLUMN, "note", "问题说明"),
        "候选编号": _clean_text(trace.get(SPECIAL_TRACE_ID_COLUMN)),
        "标准项目编码": _clean_text(trace.get("标准项目编码")),
        "标准项目名称": _clean_text(trace.get("项目名称")),
        "标准单位": _clean_text(trace.get("建议单位")),
        "工程量状态": _clean_text(trace.get("trace状态")),
        "待补量原因": _clean_text(trace.get("阻断原因")),
        "工程量规则类型": _clean_text(trace.get("标准规则模板")),
        "标准工程量计算规则": _clean_text(trace.get("标准工程量计算规则")),
        "建议工程量": _clean_text(trace.get("建议工程量")),
        "建议单位": _clean_text(trace.get("建议单位")),
        "工程量证据摘要": _default_quantity_source(trace),
        "图纸识别名称": _clean_text(trace.get("图纸项目名称")),
        "图纸识别规格或做法": _review_deduction_review(review),
        "来源文件": _trace_source_file(trace),
        "来源行号": _clean_text(trace.get("区域编号")),
        "匹配置信度": "",
        "项目特征缺失字段": "",
    }


def _to_feature_rows(row_id: str, trace: Mapping[str, Any], feature_text: str, *, status: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, value in _split_feature_text(feature_text):
        rows.append(
            {
                "确认行号": row_id,
                "候选编号": _clean_text(trace.get(SPECIAL_TRACE_ID_COLUMN)),
                "标准项目编码": _clean_text(trace.get("标准项目编码")),
                "标准项目名称": _clean_text(trace.get("项目名称")),
                "项目特征字段": name,
                "候选填充值": value,
                "状态": status,
                "置信度": "",
                "证据文本": feature_text,
            }
        )
    return rows


def _to_evidence_row(row_id: str, trace: Mapping[str, Any], deduction_review: str) -> dict[str, Any]:
    return {
        "确认行号": row_id,
        "候选编号": _clean_text(trace.get(SPECIAL_TRACE_ID_COLUMN)),
        "标准项目编码": _clean_text(trace.get("标准项目编码")),
        "标准项目名称": _clean_text(trace.get("项目名称")),
        "证据类型": "special_quantity_trace",
        "证据值": _clean_text(trace.get("建议工程量")),
        "证据单位": _clean_text(trace.get("建议单位")),
        "是否匹配工程量规则": "是" if _trace_ready(trace) else "否",
        "证据置信度": "",
        "证据文本": f"{_default_quantity_source(trace)}；扣减/合并复核：{deduction_review}".strip("；"),
        "来源文件": _trace_source_file(trace),
        "图层": "",
        "布局": "",
        "块名": "",
        "X": "",
        "Y": "",
        "源行号": _clean_text(trace.get("区域编号")),
        "业务标签": "BIZ-2x A6 专项trace复核",
    }


def _default_quantity_source(trace: Mapping[str, Any]) -> str:
    parts = [
        f"专项trace：{_clean_text(trace.get(SPECIAL_TRACE_ID_COLUMN))}",
        f"标准规则：{_clean_text(trace.get('标准工程量计算规则'))}",
        f"计算公式：{_clean_text(trace.get('计算公式'))}",
        f"计算输入：{_clean_text(trace.get('计算输入'))}",
        f"区域编号：{_clean_text(trace.get('区域编号'))}",
        f"房间编号：{_clean_text(trace.get('房间编号'))}",
    ]
    return "；".join(part for part in parts if part and not part.endswith("："))


def _project_feature_text(trace: Mapping[str, Any]) -> str:
    calculation_trace = trace.get("calculation_trace") if isinstance(trace.get("calculation_trace"), dict) else {}
    return _clean_text(calculation_trace.get("project_feature_text"))


def _trace_source_file(trace: Mapping[str, Any]) -> str:
    calculation_trace = trace.get("calculation_trace") if isinstance(trace.get("calculation_trace"), dict) else {}
    binding = calculation_trace.get("region_binding") if isinstance(calculation_trace.get("region_binding"), dict) else {}
    return _clean_text(binding.get("来源文件"))


def _split_feature_text(feature_text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for part in _clean_text(feature_text).replace("\n", "；").split("；"):
        item = part.strip()
        if not item:
            continue
        if "：" in item:
            name, value = item.split("：", 1)
        elif ":" in item:
            name, value = item.split(":", 1)
        else:
            name, value = item, ""
        if name.strip():
            rows.append((name.strip(), value.strip()))
    return rows


def _review_trace_id(review: Mapping[str, Any]) -> str:
    return _clean_text(review.get(SPECIAL_TRACE_ID_COLUMN) or review.get("special_quantity_id") or review.get("trace_id"))


def _review_deduction_review(review: Mapping[str, Any]) -> str:
    return _clean_text(review.get(DEDUCTION_REVIEW_COLUMN) or review.get("deduction_review") or review.get("deduction_and_merge_review"))


def _review_value(review: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean_text(review.get(key))
        if value:
            return value
    return ""


def _validation_issues_as_business_issues(validation: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, issue in enumerate(validation.get("issues") or [], start=1):
        issues.append(
            {
                "复核序号": "",
                SPECIAL_TRACE_ID_COLUMN: issue.get("confirmation_row_id", ""),
                "问题说明": "；".join(issue.get("issues") or []),
                "处理建议": "补齐项目特征、工程量、单位或来源说明后重新提交",
            }
        )
    return issues


def _issue(index: int, trace_id: str, message: str, suggestion: str) -> dict[str, Any]:
    return {
        "复核序号": index,
        SPECIAL_TRACE_ID_COLUMN: trace_id,
        "问题说明": message,
        "处理建议": suggestion,
    }


def _skipped(index: int, trace_id: str, adopt: str, reason: str) -> dict[str, Any]:
    return {
        "复核序号": index,
        SPECIAL_TRACE_ID_COLUMN: trace_id,
        "是否采用": adopt,
        "跳过原因": reason,
    }


def _write_csv(path: Path, rows: list[Mapping[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _parse_positive_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed > 0 else None


def _is_yes(value: Any) -> bool:
    return _clean_text(value) in {"是", "Y", "y", "yes", "YES", "true", "TRUE", "1"}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
