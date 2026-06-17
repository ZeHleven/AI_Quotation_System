from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from app.services import drawing_quantity_confirmation as confirmation


PHASE = "BIZ-2x-dwg-cad-selection-finalization"
ADOPT_ACTION = "采纳"

ISSUE_HEADERS = ["选择序号", "列项序号", "建议编号", "问题说明", "处理建议"]
SKIPPED_HEADERS = ["选择序号", "列项序号", "建议编号", "处理动作", "跳过原因"]


def build_dwg_selection_finalization(
    listing_report: Mapping[str, Any],
    selections: list[Mapping[str, Any]],
) -> dict[str, Any]:
    item_rows = list(listing_report.get("item_rows") or [])
    item_lookup = {_row_no(row): row for row in item_rows if _row_no(row)}
    confirmation_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    adopted_usage_keys: set[tuple[str, str]] = set()

    for index, selection in enumerate(selections, start=1):
        action = _clean_text(selection.get("action"))
        row_no = _clean_text(selection.get("row_no") or selection.get("列项序号"))
        suggestion_key = _clean_text(selection.get("suggestion_key") or selection.get("建议编号"))
        if action != ADOPT_ACTION:
            skipped_rows.append(
                {
                    "选择序号": index,
                    "列项序号": row_no,
                    "建议编号": suggestion_key,
                    "处理动作": action,
                    "跳过原因": "页面未标记为采纳",
                }
            )
            continue

        item_row = item_lookup.get(row_no)
        if not item_row:
            issues.append(_issue(index, row_no, suggestion_key, "未找到对应列项行", "重新生成 DWG 列项结果后再提交"))
            continue
        option = _find_candidate_option(item_row, suggestion_key)
        if not option:
            issues.append(_issue(index, row_no, suggestion_key, "未找到该列项下的 CAD 候选建议", "确认没有跨行选择候选量"))
            continue

        row_issues = _validate_adopted_option(option)
        usage_key = (_clean_text(item_row.get("标准项目编码")), suggestion_key)
        if usage_key in adopted_usage_keys:
            row_issues.append("同一标准项目下同一个 CAD 建议编号已被采纳，不能重复生成清单行")
        project_feature = _clean_text(selection.get("project_feature")) or _default_project_feature_text(item_row)
        if not project_feature:
            row_issues.append("项目特征为空，不能生成最终四字段清单")
        quantity = _clean_text(selection.get("quantity")) or _clean_text(option.get("建议工程量"))
        if _parse_positive_decimal(quantity) is None:
            row_issues.append("采纳工程量必须大于 0")
        unit = _clean_text(selection.get("unit")) or _clean_text(option.get("建议单位"))
        if not unit:
            row_issues.append("采纳单位不能为空")
        if row_issues:
            issues.append(_issue(index, row_no, suggestion_key, "；".join(row_issues), "补齐或改选 CAD 候选后重新提交"))
            continue

        adopted_usage_keys.add(usage_key)
        confirmation_row_id = f"BIZ2x-dwgselect-{len(confirmation_rows) + 1:04d}"
        confirmation_row = _to_confirmation_row(confirmation_row_id, item_row, option, selection, project_feature)
        confirmation_rows.append(confirmation_row)
        feature_rows.extend(_to_feature_rows(confirmation_row_id, item_row, project_feature))
        evidence_rows.append(_to_evidence_row(confirmation_row_id, item_row, option))

    confirmation_pack = {
        "ok": True,
        "phase": "BIZ-2x-dwg-selection-confirmation-pack",
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
    validation["phase"] = "BIZ-2x-dwg-selection-confirmation-validation"
    validation["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    validation_issues = _validation_issues_as_business_issues(validation)
    all_issues = [*issues, *validation_issues]
    return {
        "ok": bool(confirmation_rows) and not all_issues and validation.get("ok") is True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": bool(validation.get("ok")) and not all_issues,
        "summary": {
            "selection_count": len(selections),
            "adopted_selection_count": sum(1 for item in selections if _clean_text(item.get("action")) == ADOPT_ACTION),
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


def write_dwg_selection_finalization_outputs(
    finalization: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_DWG候选采纳生成最终清单_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    issue_csv_path = directory / f"{file_stem}_问题.csv"
    skipped_csv_path = directory / f"{file_stem}_跳过.csv"
    confirmation_csv_path = directory / f"{file_stem}_BIZ2x6确认行.csv"

    json_path.write_text(json.dumps(finalization, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_dwg_selection_finalization_markdown(finalization), encoding="utf-8")
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
        validation = finalization.get("confirmation_validation") or {}
        validation_outputs = confirmation.write_validation_report(
            validation,
            directory,
            stem=f"{file_stem}_BIZ2x6确认行校验",
        )
        outputs.update({f"validation_{key}": value for key, value in validation_outputs.items()})
    return outputs


def build_dwg_selection_finalization_markdown(finalization: Mapping[str, Any]) -> str:
    summary = finalization.get("summary", {})
    lines = [
        "# BIZ-2x DWG CAD 候选采纳生成最终清单",
        "",
        f"- 生成时间：{finalization.get('generated_at', '-')}",
        f"- 页面选择数：{summary.get('selection_count', 0)}",
        f"- 采纳选择数：{summary.get('adopted_selection_count', 0)}",
        f"- 转确认行：{summary.get('converted_confirmation_row_count', 0)}",
        f"- 转换问题：{summary.get('conversion_issue_count', 0)}",
        f"- BIZ-2x-6 校验是否通过：{'是' if summary.get('biz2x6_validation_ok') else '否'}",
        f"- 最终四字段可导出行：{summary.get('final_ready_count', 0)}",
        "",
        "## 导出条件",
        "",
        "- 页面动作必须是“采纳”。",
        "- 采纳建议编号必须属于当前列项的 `CAD候选列表`。",
        "- CAD 候选必须可复核，工程量必须大于 0。",
        "- 项目特征必须按标准库字段口径生成或由业务员确认，不允许保留待确认/缺失占位。",
        "- 最终 Excel 仍复用 BIZ-2x-6 四字段校验。",
    ]
    if finalization.get("issues"):
        lines.extend(["", "## 前 10 条问题", ""])
        for issue in list(finalization.get("issues") or [])[:10]:
            lines.append(f"- 列项 {issue.get('列项序号', '')} / {issue.get('建议编号', '')}：{issue.get('问题说明', '')}")
    return "\n".join(lines) + "\n"


def _to_confirmation_row(
    row_id: str,
    item_row: Mapping[str, Any],
    option: Mapping[str, Any],
    selection: Mapping[str, Any],
    project_feature: str,
) -> dict[str, Any]:
    quantity = _clean_text(selection.get("quantity")) or _clean_text(option.get("建议工程量"))
    unit = _clean_text(selection.get("unit")) or _clean_text(option.get("建议单位"))
    source_note = _clean_text(selection.get("quantity_source_note")) or _default_quantity_source_note(option)
    return {
        "确认行号": row_id,
        confirmation.ADOPT_COLUMN: "是",
        confirmation.REVIEW_COLUMN: "通过",
        confirmation.MANUAL_QUANTITY_COLUMN: quantity,
        confirmation.MANUAL_UNIT_COLUMN: unit,
        confirmation.QUANTITY_SOURCE_COLUMN: source_note,
        confirmation.MANUAL_NAME_COLUMN: _clean_text(selection.get("project_name")) or _clean_text(item_row.get("项目名称")),
        confirmation.MANUAL_FEATURE_COLUMN: project_feature,
        confirmation.ISSUE_COLUMN: _clean_text(selection.get("note")),
        "候选编号": _clean_text(option.get("建议编号")),
        "标准项目编码": _clean_text(item_row.get("标准项目编码")),
        "标准项目名称": _clean_text(item_row.get("项目名称")),
        "标准单位": _clean_text(item_row.get("单位")),
        "工程量状态": "页面采纳CAD候选量",
        "待补量原因": "",
        "工程量规则类型": "",
        "标准工程量计算规则": _clean_text(item_row.get("工程量计算规则")),
        "建议工程量": _clean_text(option.get("建议工程量")),
        "建议单位": _clean_text(option.get("建议单位")),
        "工程量证据摘要": _clean_text(option.get("算量证据")),
        "图纸识别名称": _clean_text(item_row.get("图纸识别名称")),
        "图纸识别规格或做法": _clean_text(item_row.get("图纸识别规格或做法")),
        "来源文件": _clean_text(item_row.get("来源文件")),
        "来源行号": _clean_text(option.get("CAD来源图元行号")),
        "匹配置信度": item_row.get("匹配置信度", ""),
        "项目特征缺失字段": "",
    }


def _to_feature_rows(row_id: str, item_row: Mapping[str, Any], project_feature: str) -> list[dict[str, Any]]:
    field_names = _feature_field_names(item_row)
    if not field_names:
        return []
    return [
        {
            "确认行号": row_id,
            "候选编号": "",
            "标准项目编码": _clean_text(item_row.get("标准项目编码")),
            "标准项目名称": _clean_text(item_row.get("项目名称")),
            "项目特征字段": field_name,
            "候选填充值": _feature_value_from_text(project_feature, field_name),
            "状态": "business_adopted_from_dwg_selection",
            "置信度": "",
            "证据文本": _clean_text(item_row.get("来源证据")) or _clean_text(item_row.get("图纸识别规格或做法")),
        }
        for field_name in field_names
    ]


def _to_evidence_row(row_id: str, item_row: Mapping[str, Any], option: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "确认行号": row_id,
        "候选编号": _clean_text(option.get("建议编号")),
        "标准项目编码": _clean_text(item_row.get("标准项目编码")),
        "标准项目名称": _clean_text(item_row.get("项目名称")),
        "证据类型": "CAD标准规则trace",
        "证据值": _clean_text(option.get("建议工程量")),
        "证据单位": _clean_text(option.get("建议单位")),
        "是否匹配工程量规则": "是",
        "证据置信度": _clean_text(option.get("绑定置信度")),
        "证据文本": _clean_text(option.get("算量证据")) or _clean_text(option.get("推荐原因")),
        "来源文件": _clean_text(item_row.get("来源文件")),
        "图层": _clean_text(option.get("CAD来源")),
        "布局": "",
        "块名": "",
        "X": "",
        "Y": "",
        "源行号": _clean_text(option.get("CAD来源图元行号")),
        "业务标签": _clean_text(option.get("推荐动作")),
    }


def _validate_adopted_option(option: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if _clean_text(option.get("是否可复核")) != "是":
        issues.append("CAD候选未达到可复核状态")
    if _parse_positive_decimal(option.get("建议工程量")) is None:
        issues.append("CAD候选建议工程量为空或不大于0")
    if not _clean_text(option.get("建议单位")):
        issues.append("CAD候选建议单位为空")
    return issues


def _find_candidate_option(item_row: Mapping[str, Any], suggestion_key: str) -> Mapping[str, Any] | None:
    for option in item_row.get("CAD候选列表") or []:
        if _clean_text(option.get("建议编号")) == suggestion_key:
            return option
    return None


def _row_no(row: Mapping[str, Any]) -> str:
    return _clean_text(row.get("序号"))


def _default_project_feature_text(item_row: Mapping[str, Any]) -> str:
    field_names = _feature_field_names(item_row)
    source_value = "；".join(
        _dedupe_keep_order(
            [
                _clean_text(item_row.get("图纸识别规格或做法")),
                _clean_text(item_row.get("图纸识别名称")),
                _clean_text(item_row.get("来源证据")),
            ]
        )
    )
    if not field_names:
        return source_value
    if not source_value:
        return ""
    return "；".join(f"{field_name}：{source_value}" for field_name in field_names)


def _default_quantity_source_note(option: Mapping[str, Any]) -> str:
    parts = ["页面采纳 CAD 候选量，并按标准库工程量计算规则进入 BIZ-2x-6 校验"]
    if _clean_text(option.get("CAD公式")):
        parts.append(f"CAD公式：{_clean_text(option.get('CAD公式'))}")
    if _clean_text(option.get("CAD来源图元行号")):
        parts.append(f"CAD行号：{_clean_text(option.get('CAD来源图元行号'))}")
    return "；".join(parts)


def _feature_field_names(item_row: Mapping[str, Any]) -> list[str]:
    text = _clean_text(item_row.get("项目特征字段"))
    if not text:
        return []
    return _dedupe_keep_order([item.strip() for item in text.replace("、", "；").replace(";", "；").split("；") if item.strip()])


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _feature_value_from_text(feature_text: str, field_name: str) -> str:
    prefix = f"{field_name}："
    for part in feature_text.split("；"):
        if part.startswith(prefix):
            return part[len(prefix) :].strip()
    return ""


def _validation_issues_as_business_issues(validation: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, issue in enumerate(validation.get("issues") or [], start=1):
        issues.append(
            {
                "选择序号": "",
                "列项序号": issue.get("confirmation_row_id", ""),
                "建议编号": "",
                "问题说明": "；".join(issue.get("issues") or []),
                "处理建议": "补齐项目特征、工程量、单位或来源说明后重新提交",
            }
        )
    return issues


def _issue(index: int, row_no: str, suggestion_key: str, message: str, suggestion: str) -> dict[str, Any]:
    return {
        "选择序号": index,
        "列项序号": row_no,
        "建议编号": suggestion_key,
        "问题说明": message,
        "处理建议": suggestion,
    }


def _write_csv(path: Path, rows: list[Mapping[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _parse_positive_decimal(value: Any) -> Decimal | None:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return decimal_value if decimal_value > 0 else None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
