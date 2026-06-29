from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from app.services import drawing_quantity_confirmation as confirmation


BINDING_PHASE = "BIZ-2x-low-risk-mvp-project-binding"
FINALIZATION_PHASE = "BIZ-2x-low-risk-mvp-finalization"

MVP_BINDING_ID_COLUMN = "MVP绑定编号"
MVP_CATEGORY_COLUMN = "MVP类别"
MVP_SUGGESTION_COLUMN = "MVP建议编号"
PROJECT_ROW_NO_COLUMN = "项目行序号"
BINDING_STATUS_COLUMN = "绑定状态"
BINDING_ACTION_COLUMN = "处理建议"

ADOPT_VALUE = "是"
PASS_VALUE = "通过"
PENDING_VALUE = "待确认"

BINDING_HEADERS = [
    MVP_BINDING_ID_COLUMN,
    BINDING_STATUS_COLUMN,
    BINDING_ACTION_COLUMN,
    MVP_CATEGORY_COLUMN,
    MVP_SUGGESTION_COLUMN,
    PROJECT_ROW_NO_COLUMN,
    "项目名称",
    "项目特征",
    "单位",
    "建议工程量",
    "建议单位",
    "来源文件",
    "图层",
    "块名",
    "绑定说明",
    "风险提示",
]

ISSUE_HEADERS = [MVP_BINDING_ID_COLUMN, MVP_SUGGESTION_COLUMN, PROJECT_ROW_NO_COLUMN, "问题说明", "处理建议"]
SKIPPED_HEADERS = [MVP_BINDING_ID_COLUMN, MVP_SUGGESTION_COLUMN, PROJECT_ROW_NO_COLUMN, "是否采用", "跳过原因"]
FINAL_HEADERS = ["项目名称", "项目特征", "单位", "工程量"]


def build_low_risk_mvp_binding_pack(listing_report: Mapping[str, Any]) -> dict[str, Any]:
    mvp_rows = list(listing_report.get("low_risk_quantity_mvp_rows") or [])
    item_rows = list(listing_report.get("item_rows") or [])
    binding_rows: list[dict[str, Any]] = []
    confirmation_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []

    for index, mvp_row in enumerate(mvp_rows, start=1):
        matches = _candidate_matches(mvp_row, item_rows)
        status = _binding_status(mvp_row, matches)
        if not matches:
            binding_id = f"BIZ2xMVPB-{index:04d}-00"
            binding_rows.append(_binding_row(binding_id, mvp_row, None, None, status))
            continue
        for match_index, match in enumerate(matches, start=1):
            binding_id = f"BIZ2xMVPB-{index:04d}-{match_index:02d}"
            item_row = match["item_row"]
            option = match["option"]
            binding_row = _binding_row(binding_id, mvp_row, item_row, option, status)
            binding_rows.append(binding_row)
            if _can_build_confirmation_row(binding_row, option):
                confirmation_row = _to_confirmation_row(binding_id, mvp_row, item_row, option)
                confirmation_rows.append(confirmation_row)
                feature_rows.extend(_to_feature_rows(binding_id, item_row, confirmation_row[confirmation.MANUAL_FEATURE_COLUMN]))
                evidence_rows.append(_to_evidence_row(binding_id, mvp_row, item_row, option))

    status_counts = Counter(row[BINDING_STATUS_COLUMN] for row in binding_rows)
    return {
        "ok": True,
        "phase": BINDING_PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "requires_manual_review": True,
        "summary": {
            "mvp_candidate_count": len(mvp_rows),
            "binding_row_count": len(binding_rows),
            "ready_binding_count": status_counts.get("ready_for_manual_confirmation", 0),
            "ambiguous_binding_count": status_counts.get("needs_manual_select_one_project_row", 0),
            "blocked_binding_count": sum(
                count for status_name, count in status_counts.items() if str(status_name).startswith("blocked_")
            ),
            "confirmation_row_count": len(confirmation_rows),
            "feature_detail_count": len(feature_rows),
            "evidence_detail_count": len(evidence_rows),
            "binding_status_counts": dict(status_counts.most_common()),
            "final_export_requires_manual_confirmation": True,
            "next_step": "business_review_low_risk_mvp_confirmation_then_finalize_quantity_list",
        },
        "binding_rows": binding_rows,
        "confirmation_rows": confirmation_rows,
        "feature_rows": feature_rows,
        "evidence_rows": evidence_rows,
    }


def build_low_risk_mvp_finalization(listing_report: Mapping[str, Any], reviews: list[Mapping[str, Any]]) -> dict[str, Any]:
    binding_pack = build_low_risk_mvp_binding_pack(listing_report)
    binding_lookup = {str(row.get(MVP_BINDING_ID_COLUMN) or ""): row for row in binding_pack.get("binding_rows") or []}
    confirmation_template_lookup = {
        str(row.get("确认行号") or ""): row for row in binding_pack.get("confirmation_rows") or []
    }
    confirmation_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    used_binding_ids: set[str] = set()
    used_suggestion_keys: set[str] = set()

    for index, review in enumerate(reviews, start=1):
        binding_id = _review_binding_id(review)
        binding_row = binding_lookup.get(binding_id)
        adopt = _review_value(review, confirmation.ADOPT_COLUMN, "adopt", "是否采用")
        suggestion_key = _review_value(review, MVP_SUGGESTION_COLUMN, "suggestion_key", "建议编号")
        row_no = _review_value(review, PROJECT_ROW_NO_COLUMN, "row_no", "项目行序号")
        if not _is_yes(adopt):
            skipped_rows.append(_skipped(binding_id, suggestion_key, row_no, adopt, "业务未确认采用"))
            continue
        if not binding_row:
            issues.append(_issue(binding_id, suggestion_key, row_no, "未找到对应 MVP 绑定行", "重新生成 DWG 识图结果后再提交"))
            continue
        row_issues = _validate_binding_review(binding_row, review)
        if binding_id in used_binding_ids:
            row_issues.append("同一 MVP 绑定行不能重复采用")
        binding_suggestion = str(binding_row.get(MVP_SUGGESTION_COLUMN) or "")
        if binding_suggestion in used_suggestion_keys:
            row_issues.append("同一 MVP 建议编号只能采用一次")
        if row_issues:
            issues.append(
                _issue(
                    binding_id,
                    binding_suggestion,
                    str(binding_row.get(PROJECT_ROW_NO_COLUMN) or ""),
                    "；".join(row_issues),
                    "修正人工确认信息或改选绑定行后重新提交",
                )
            )
            continue

        used_binding_ids.add(binding_id)
        used_suggestion_keys.add(binding_suggestion)
        template = confirmation_template_lookup.get(binding_id) or {}
        confirmation_row = _review_to_confirmation_row(binding_id, template, binding_row, review)
        confirmation_rows.append(confirmation_row)
        feature_rows.extend(_feature_rows_from_confirmation(binding_id, confirmation_row))
        evidence_rows.append(_evidence_row_from_binding(binding_id, binding_row))

    confirmation_pack = {
        "ok": True,
        "phase": "BIZ-2x-low-risk-mvp-confirmed-biz2x6-pack",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "confirmation_row_count": len(confirmation_rows),
            "feature_detail_count": len(feature_rows),
            "evidence_detail_count": len(evidence_rows),
            "source_phase": FINALIZATION_PHASE,
            "final_export_requires_manual_confirmation": False,
        },
        "confirmation_rows": confirmation_rows,
        "feature_rows": feature_rows,
        "evidence_rows": evidence_rows,
    }
    validation = confirmation.validate_confirmation_rows(confirmation_rows)
    validation["phase"] = "BIZ-2x-low-risk-mvp-confirmation-validation"
    validation["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    validation_issues = _validation_issues_as_business_issues(validation)
    all_issues = [*issues, *validation_issues]
    merged_rows, merge_summary = _merge_confirmation_rows_into_quantity_list(listing_report, confirmation_rows)
    ok = bool(confirmation_rows) and not all_issues and validation.get("ok") is True
    return {
        "ok": ok,
        "phase": FINALIZATION_PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": ok,
        "summary": {
            "review_row_count": len(reviews),
            "adopted_review_count": sum(1 for item in reviews if _is_yes(_review_value(item, confirmation.ADOPT_COLUMN, "adopt", "是否采用"))),
            "converted_confirmation_row_count": len(confirmation_rows),
            "conversion_issue_count": len(issues),
            "validation_issue_count": len(validation_issues),
            "skipped_row_count": len(skipped_rows),
            "biz2x6_validation_ok": bool(validation.get("ok")) and not validation_issues,
            "final_ready_count": len(merged_rows) if ok else 0,
            **merge_summary,
        },
        "issues": all_issues,
        "skipped_rows": skipped_rows,
        "binding_pack": binding_pack,
        "confirmation_pack": confirmation_pack,
        "confirmation_validation": validation,
        "quantity_list_rows": merged_rows if ok else [],
    }


def write_low_risk_mvp_binding_outputs(
    pack: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_低风险MVP绑定确认包_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    binding_csv_path = directory / f"{file_stem}_绑定明细.csv"
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_low_risk_mvp_binding_markdown(pack), encoding="utf-8")
    _write_csv(binding_csv_path, list(pack.get("binding_rows") or []), BINDING_HEADERS)
    outputs = {"json": str(json_path), "markdown": str(md_path), "binding_csv": str(binding_csv_path)}
    confirmation_outputs = confirmation.write_confirmation_outputs(
        pack,
        directory,
        stem=f"{file_stem}_BIZ2x6人工确认",
    )
    outputs.update({f"confirmation_{key}": value for key, value in confirmation_outputs.items()})
    return outputs


def write_low_risk_mvp_finalization_outputs(
    finalization: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_低风险MVP回填四字段清单_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    issue_csv_path = directory / f"{file_stem}_问题.csv"
    skipped_csv_path = directory / f"{file_stem}_跳过.csv"
    confirmation_csv_path = directory / f"{file_stem}_BIZ2x6确认行.csv"
    final_csv_path = directory / f"{file_stem}_最终四字段清单.csv"
    final_xlsx_path = directory / f"{file_stem}_最终四字段清单.xlsx"

    json_path.write_text(json.dumps(finalization, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_low_risk_mvp_finalization_markdown(finalization), encoding="utf-8")
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
            stem=f"{file_stem}_BIZ2x6确认工作簿",
        )
        outputs.update({f"confirmation_{key}": value for key, value in confirmation_outputs.items()})
    if finalization.get("ok"):
        final_rows = list(finalization.get("quantity_list_rows") or [])
        _write_csv(final_csv_path, final_rows, FINAL_HEADERS)
        confirmation.write_final_quantity_workbook(final_rows, final_xlsx_path)
        outputs["validation_final_csv"] = str(final_csv_path)
        outputs["validation_final_xlsx"] = str(final_xlsx_path)
        outputs["low_risk_mvp_final_csv"] = str(final_csv_path)
        outputs["low_risk_mvp_final_xlsx"] = str(final_xlsx_path)
    return outputs


def build_low_risk_mvp_binding_markdown(pack: Mapping[str, Any]) -> str:
    summary = pack.get("summary") or {}
    lines = [
        "# 低风险 MVP 候选绑定确认包",
        "",
        f"- 生成时间：{pack.get('generated_at', '-')}",
        f"- MVP 候选数：{summary.get('mvp_candidate_count', 0)}",
        f"- 可确认绑定：{summary.get('ready_binding_count', 0)}",
        f"- 需选项目行：{summary.get('ambiguous_binding_count', 0)}",
        f"- 阻断绑定：{summary.get('blocked_binding_count', 0)}",
        f"- 人工确认行：{summary.get('confirmation_row_count', 0)}",
        "",
        "## 边界",
        "",
        "- 本包只覆盖地面面积、吊顶面积、灯具/洁具数量首批 MVP 候选。",
        "- 确认工作簿默认不采用；业务员必须把采用行改为“是”并填写“通过”。",
        "- 通过后才能生成回填工程量的四字段清单。",
    ]
    return "\n".join(lines) + "\n"


def build_low_risk_mvp_finalization_markdown(finalization: Mapping[str, Any]) -> str:
    summary = finalization.get("summary") or {}
    lines = [
        "# 低风险 MVP 回填四字段清单",
        "",
        f"- 生成时间：{finalization.get('generated_at', '-')}",
        f"- 复核行数：{summary.get('review_row_count', 0)}",
        f"- 采用行数：{summary.get('adopted_review_count', 0)}",
        f"- 回填更新行：{summary.get('merged_updated_row_count', 0)}",
        f"- 追加确认行：{summary.get('merged_appended_row_count', 0)}",
        f"- 最终四字段行：{summary.get('final_ready_count', 0)}",
        f"- 是否通过：{'是' if finalization.get('ok') else '否'}",
    ]
    if finalization.get("issues"):
        lines.extend(["", "## 问题", ""])
        for issue in list(finalization.get("issues") or [])[:20]:
            lines.append(f"- {issue.get(MVP_BINDING_ID_COLUMN, '')}：{issue.get('问题说明', '')}")
    return "\n".join(lines) + "\n"


def _candidate_matches(mvp_row: Mapping[str, Any], item_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    suggestion_key = _clean_text(mvp_row.get("suggestion_key"))
    matches: list[dict[str, Any]] = []
    if not suggestion_key:
        return matches
    for item_row in item_rows:
        for option in item_row.get("CAD候选列表") or []:
            if _clean_text(option.get("建议编号")) == suggestion_key:
                matches.append({"item_row": item_row, "option": option})
    return matches


def _binding_status(mvp_row: Mapping[str, Any], matches: list[dict[str, Any]]) -> str:
    if not bool(mvp_row.get("ready_for_manual_review")):
        return "blocked_mvp_candidate_not_ready"
    ready_matches = [match for match in matches if _option_ready(match["option"])]
    if not ready_matches:
        return "blocked_no_project_row_binding" if not matches else "blocked_candidate_option_not_ready"
    if len(ready_matches) == 1 and len(matches) == 1:
        return "ready_for_manual_confirmation"
    return "needs_manual_select_one_project_row"


def _binding_row(
    binding_id: str,
    mvp_row: Mapping[str, Any],
    item_row: Mapping[str, Any] | None,
    option: Mapping[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    return {
        MVP_BINDING_ID_COLUMN: binding_id,
        BINDING_STATUS_COLUMN: status,
        BINDING_ACTION_COLUMN: _binding_action(status),
        MVP_CATEGORY_COLUMN: _clean_text(mvp_row.get("mvp_category_label") or mvp_row.get("mvp_category")),
        MVP_SUGGESTION_COLUMN: _clean_text(mvp_row.get("suggestion_key")),
        PROJECT_ROW_NO_COLUMN: _clean_text((item_row or {}).get("序号")),
        "项目名称": _clean_text((item_row or {}).get("项目名称")),
        "项目特征": _default_project_feature_text(item_row or {}),
        "单位": _clean_text((item_row or {}).get("单位")),
        "建议工程量": _clean_text((option or {}).get("建议工程量") or mvp_row.get("suggested_quantity")),
        "建议单位": _clean_text((option or {}).get("建议单位") or mvp_row.get("suggested_unit")),
        "来源文件": _clean_text(mvp_row.get("source_file")),
        "图层": _clean_text(mvp_row.get("layer")),
        "块名": _clean_text(mvp_row.get("block_name")),
        "绑定说明": _clean_text((option or {}).get("推荐原因") or (option or {}).get("推荐说明")),
        "风险提示": "；".join(mvp_row.get("risk_flags") or []),
    }


def _binding_action(status: str) -> str:
    if status == "ready_for_manual_confirmation":
        return "下载确认工作簿，确认项目行与工程量后采用"
    if status == "needs_manual_select_one_project_row":
        return "同一 MVP 候选匹配多个项目行，需只选择一个项目行采用"
    if status == "blocked_mvp_candidate_not_ready":
        return "MVP 候选本身不可复核，暂不采用"
    if status == "blocked_candidate_option_not_ready":
        return "项目行候选量不可复核，暂不采用"
    return "未绑定到项目行，需补项目识别或人工补量"


def _can_build_confirmation_row(binding_row: Mapping[str, Any], option: Mapping[str, Any]) -> bool:
    return str(binding_row.get(BINDING_STATUS_COLUMN)) in {
        "ready_for_manual_confirmation",
        "needs_manual_select_one_project_row",
    } and _option_ready(option)


def _option_ready(option: Mapping[str, Any]) -> bool:
    if _clean_text(option.get("是否可复核")) != "是":
        return False
    if _parse_positive_decimal(option.get("建议工程量")) is None:
        return False
    return bool(_clean_text(option.get("建议单位")))


def _to_confirmation_row(
    binding_id: str,
    mvp_row: Mapping[str, Any],
    item_row: Mapping[str, Any],
    option: Mapping[str, Any],
) -> dict[str, Any]:
    feature_text = _default_project_feature_text(item_row)
    quantity = _clean_text(option.get("建议工程量") or mvp_row.get("suggested_quantity"))
    unit = _clean_text(option.get("建议单位") or mvp_row.get("suggested_unit"))
    return {
        "确认行号": binding_id,
        confirmation.ADOPT_COLUMN: PENDING_VALUE,
        confirmation.REVIEW_COLUMN: "",
        confirmation.MANUAL_QUANTITY_COLUMN: quantity,
        confirmation.MANUAL_UNIT_COLUMN: unit,
        confirmation.QUANTITY_SOURCE_COLUMN: _default_quantity_source(binding_id, mvp_row, item_row, option),
        confirmation.MANUAL_NAME_COLUMN: _clean_text(item_row.get("项目名称")),
        confirmation.MANUAL_FEATURE_COLUMN: feature_text,
        confirmation.ISSUE_COLUMN: "",
        "候选编号": _clean_text(mvp_row.get("suggestion_key")),
        "标准项目编码": _clean_text(item_row.get("标准项目编码")),
        "标准项目名称": _clean_text(item_row.get("项目名称")),
        "标准单位": _clean_text(item_row.get("单位")),
        "工程量状态": "低风险 MVP 候选已绑定项目行，待人工确认",
        "待补量原因": "",
        "工程量规则类型": _clean_text((option.get("trace状态") or "")),
        "标准工程量计算规则": _clean_text(item_row.get("工程量计算规则")),
        "建议工程量": quantity,
        "建议单位": unit,
        "工程量证据摘要": _clean_text(option.get("算量证据") or option.get("推荐原因")),
        "图纸识别名称": _clean_text(item_row.get("图纸识别名称")),
        "图纸识别规格或做法": _clean_text(item_row.get("图纸识别规格或做法")),
        "来源文件": _clean_text(item_row.get("来源文件")),
        "来源行号": _clean_text(item_row.get("序号")),
        "匹配置信度": _clean_text(item_row.get("匹配置信度")),
        "项目特征缺失字段": "",
    }


def _to_feature_rows(row_id: str, item_row: Mapping[str, Any], feature_text: str) -> list[dict[str, Any]]:
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
            "候选填充值": _feature_value_from_text(feature_text, field_name),
            "状态": "low_risk_mvp_bound_pending_business_review",
            "置信度": "",
            "证据文本": feature_text,
        }
        for field_name in field_names
    ]


def _to_evidence_row(
    row_id: str,
    mvp_row: Mapping[str, Any],
    item_row: Mapping[str, Any],
    option: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "确认行号": row_id,
        "候选编号": _clean_text(mvp_row.get("suggestion_key")),
        "标准项目编码": _clean_text(item_row.get("标准项目编码")),
        "标准项目名称": _clean_text(item_row.get("项目名称")),
        "证据类型": "low_risk_mvp_cad_quantity",
        "证据值": _clean_text(option.get("建议工程量") or mvp_row.get("suggested_quantity")),
        "证据单位": _clean_text(option.get("建议单位") or mvp_row.get("suggested_unit")),
        "是否匹配工程量规则": "是",
        "证据置信度": _clean_text(option.get("绑定置信度")),
        "证据文本": _clean_text(option.get("算量证据") or option.get("推荐原因")),
        "来源文件": _clean_text(mvp_row.get("source_file")),
        "图层": _clean_text(mvp_row.get("layer")),
        "布局": "",
        "块名": _clean_text(mvp_row.get("block_name")),
        "X": "",
        "Y": "",
        "源行号": _clean_text(option.get("CAD来源图元行号")),
        "业务标签": "低风险 MVP 候选绑定",
    }


def _validate_binding_review(binding_row: Mapping[str, Any], review: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if str(binding_row.get(BINDING_STATUS_COLUMN)) not in {"ready_for_manual_confirmation", "needs_manual_select_one_project_row"}:
        issues.append("MVP 绑定状态不可采用")
    if _review_value(review, confirmation.REVIEW_COLUMN, "review_result", "核验结论") != PASS_VALUE:
        issues.append("核验结论必须填写“通过”")
    if _parse_positive_decimal(_review_value(review, confirmation.MANUAL_QUANTITY_COLUMN, "quantity", "工程量") or binding_row.get("建议工程量")) is None:
        issues.append("确认工程量必须大于 0")
    if not (_review_value(review, confirmation.MANUAL_UNIT_COLUMN, "unit", "单位") or _clean_text(binding_row.get("建议单位"))):
        issues.append("确认单位不能为空")
    feature = _review_value(review, confirmation.MANUAL_FEATURE_COLUMN, "project_feature", "项目特征") or _clean_text(binding_row.get("项目特征"))
    if not feature:
        issues.append("项目特征不能为空")
    if any(marker in feature for marker in ("待确认", "待补", "缺失", "missing_needs_manual_review")):
        issues.append("项目特征不能保留待确认/缺失占位")
    if not (_review_value(review, confirmation.QUANTITY_SOURCE_COLUMN, "quantity_source_note", "工程量来源说明") or _clean_text(binding_row.get("绑定说明"))):
        issues.append("工程量来源说明不能为空")
    return issues


def _review_to_confirmation_row(
    binding_id: str,
    template: Mapping[str, Any],
    binding_row: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(template)
    row["确认行号"] = binding_id
    row[confirmation.ADOPT_COLUMN] = ADOPT_VALUE
    row[confirmation.REVIEW_COLUMN] = PASS_VALUE
    row[confirmation.MANUAL_QUANTITY_COLUMN] = _review_value(review, confirmation.MANUAL_QUANTITY_COLUMN, "quantity", "工程量") or row.get(confirmation.MANUAL_QUANTITY_COLUMN) or binding_row.get("建议工程量", "")
    row[confirmation.MANUAL_UNIT_COLUMN] = _review_value(review, confirmation.MANUAL_UNIT_COLUMN, "unit", "单位") or row.get(confirmation.MANUAL_UNIT_COLUMN) or binding_row.get("建议单位", "")
    row[confirmation.QUANTITY_SOURCE_COLUMN] = _review_value(review, confirmation.QUANTITY_SOURCE_COLUMN, "quantity_source_note", "工程量来源说明") or row.get(confirmation.QUANTITY_SOURCE_COLUMN) or binding_row.get("绑定说明", "")
    row[confirmation.MANUAL_NAME_COLUMN] = _review_value(review, confirmation.MANUAL_NAME_COLUMN, "project_name", "项目名称") or row.get(confirmation.MANUAL_NAME_COLUMN) or binding_row.get("项目名称", "")
    row[confirmation.MANUAL_FEATURE_COLUMN] = _review_value(review, confirmation.MANUAL_FEATURE_COLUMN, "project_feature", "项目特征") or row.get(confirmation.MANUAL_FEATURE_COLUMN) or binding_row.get("项目特征", "")
    row[confirmation.ISSUE_COLUMN] = _review_value(review, confirmation.ISSUE_COLUMN, "note", "问题说明")
    row["候选编号"] = row.get("候选编号") or binding_row.get(MVP_SUGGESTION_COLUMN, "")
    row["来源行号"] = row.get("来源行号") or binding_row.get(PROJECT_ROW_NO_COLUMN, "")
    return row


def _merge_confirmation_rows_into_quantity_list(
    listing_report: Mapping[str, Any],
    confirmation_rows: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    base_rows = [dict(row) for row in (listing_report.get("quantity_list_rows") or [])]
    if not base_rows:
        base_rows = _quantity_rows_from_item_rows(listing_report.get("item_rows") or [])
    item_lookup = {_clean_text(row.get("序号")): row for row in (listing_report.get("item_rows") or [])}
    updated_indices: set[int] = set()
    appended = 0
    for row in confirmation_rows:
        row_no = _clean_text(row.get("来源行号"))
        item_row = item_lookup.get(row_no) or {}
        final_row = {
            "项目名称": _clean_text(row.get(confirmation.MANUAL_NAME_COLUMN)),
            "项目特征": _clean_text(row.get(confirmation.MANUAL_FEATURE_COLUMN)),
            "单位": _clean_text(row.get(confirmation.MANUAL_UNIT_COLUMN)),
            "工程量": _clean_text(row.get(confirmation.MANUAL_QUANTITY_COLUMN)),
        }
        index = _find_quantity_row_index(base_rows, final_row, item_row, updated_indices)
        if index is None:
            base_rows.append(final_row)
            appended += 1
        else:
            base_rows[index] = final_row
            updated_indices.add(index)
    return base_rows, {"merged_updated_row_count": len(updated_indices), "merged_appended_row_count": appended}


def _quantity_rows_from_item_rows(item_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "项目名称": _clean_text(row.get("项目名称")),
            "项目特征": _default_project_feature_text(row),
            "单位": _clean_text(row.get("单位")),
            "工程量": "待算量",
        }
        for row in item_rows
    ]


def _find_quantity_row_index(
    rows: list[Mapping[str, Any]],
    final_row: Mapping[str, Any],
    item_row: Mapping[str, Any],
    used_indices: set[int],
) -> int | None:
    names = [_clean_text(final_row.get("项目名称")), _clean_text(item_row.get("项目名称"))]
    names = [name for name in names if name]
    for index, row in enumerate(rows):
        if index in used_indices:
            continue
        row_name = _clean_text(row.get("项目名称"))
        if row_name in names:
            return index
    for index, row in enumerate(rows):
        if index in used_indices:
            continue
        row_name = _clean_text(row.get("项目名称"))
        if any(name and (name in row_name or row_name in name) for name in names):
            return index
    return None


def _feature_rows_from_confirmation(row_id: str, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    feature_text = _clean_text(row.get(confirmation.MANUAL_FEATURE_COLUMN))
    result: list[dict[str, Any]] = []
    for name, value in _split_feature_text(feature_text):
        result.append(
            {
                "确认行号": row_id,
                "候选编号": row.get("候选编号", ""),
                "标准项目编码": row.get("标准项目编码", ""),
                "标准项目名称": row.get("标准项目名称", ""),
                "项目特征字段": name,
                "候选填充值": value,
                "状态": "confirmed_from_low_risk_mvp",
                "置信度": "",
                "证据文本": feature_text,
            }
        )
    return result


def _evidence_row_from_binding(row_id: str, binding_row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "确认行号": row_id,
        "候选编号": binding_row.get(MVP_SUGGESTION_COLUMN, ""),
        "标准项目编码": "",
        "标准项目名称": binding_row.get("项目名称", ""),
        "证据类型": "low_risk_mvp_binding_review",
        "证据值": binding_row.get("建议工程量", ""),
        "证据单位": binding_row.get("建议单位", ""),
        "是否匹配工程量规则": "是",
        "证据置信度": "",
        "证据文本": binding_row.get("绑定说明", ""),
        "来源文件": binding_row.get("来源文件", ""),
        "图层": binding_row.get("图层", ""),
        "布局": "",
        "块名": binding_row.get("块名", ""),
        "X": "",
        "Y": "",
        "源行号": "",
        "业务标签": "低风险 MVP 人工确认",
    }


def _default_project_feature_text(item_row: Mapping[str, Any]) -> str:
    feature_text = _clean_text(item_row.get("项目特征"))
    if feature_text:
        return feature_text
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
    if field_names and source_value:
        return "；".join(f"{field_name}：{source_value}" for field_name in field_names)
    return source_value


def _default_quantity_source(
    binding_id: str,
    mvp_row: Mapping[str, Any],
    item_row: Mapping[str, Any],
    option: Mapping[str, Any],
) -> str:
    parts = [
        f"低风险MVP绑定：{binding_id}",
        f"MVP建议编号：{_clean_text(mvp_row.get('suggestion_key'))}",
        f"项目行：{_clean_text(item_row.get('序号'))}",
        f"CAD来源：{_clean_text(option.get('CAD来源')) or _clean_text(mvp_row.get('layer'))}",
        f"CAD公式：{_clean_text(option.get('CAD公式')) or _clean_text(mvp_row.get('formula'))}",
    ]
    return "；".join(part for part in parts if part and not part.endswith("："))


def _feature_field_names(item_row: Mapping[str, Any]) -> list[str]:
    text = _clean_text(item_row.get("项目特征字段"))
    if not text:
        return []
    return _dedupe_keep_order([item.strip() for item in text.replace("、", "；").replace(";", "；").split("；") if item.strip()])


def _feature_value_from_text(feature_text: str, field_name: str) -> str:
    prefix = f"{field_name}："
    for part in _clean_text(feature_text).split("；"):
        if part.startswith(prefix):
            return part[len(prefix) :].strip()
    return ""


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


def _validation_issues_as_business_issues(validation: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for issue in validation.get("issues") or []:
        issues.append(
            {
                MVP_BINDING_ID_COLUMN: _clean_text(issue.get("confirmation_row_id")),
                MVP_SUGGESTION_COLUMN: "",
                PROJECT_ROW_NO_COLUMN: "",
                "问题说明": "；".join(issue.get("issues") or []),
                "处理建议": "补齐人工确认表后重新提交",
            }
        )
    return issues


def _issue(binding_id: str, suggestion_key: str, row_no: str, message: str, suggestion: str) -> dict[str, str]:
    return {
        MVP_BINDING_ID_COLUMN: binding_id,
        MVP_SUGGESTION_COLUMN: suggestion_key,
        PROJECT_ROW_NO_COLUMN: row_no,
        "问题说明": message,
        "处理建议": suggestion,
    }


def _skipped(binding_id: str, suggestion_key: str, row_no: str, adopt: str, reason: str) -> dict[str, str]:
    return {
        MVP_BINDING_ID_COLUMN: binding_id,
        MVP_SUGGESTION_COLUMN: suggestion_key,
        PROJECT_ROW_NO_COLUMN: row_no,
        "是否采用": adopt,
        "跳过原因": reason,
    }


def _review_binding_id(review: Mapping[str, Any]) -> str:
    return _review_value(review, MVP_BINDING_ID_COLUMN, "mvp_binding_id", "binding_id", "确认行号")


def _review_value(review: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean_text(review.get(key))
        if value:
            return value
    return ""


def _is_yes(value: Any) -> bool:
    return _clean_text(value) in {"是", "Y", "y", "yes", "YES", "true", "True", "1"}


def _parse_positive_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _write_csv(path: Path, rows: list[Mapping[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})
