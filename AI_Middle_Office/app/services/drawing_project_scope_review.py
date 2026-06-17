from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.drawing_project_standard_mapping import (
    DEFAULT_PROJECT_STANDARD_MAPPING_PATH,
    MAPPING_STATUS_MERGE,
    MAPPING_STATUS_STANDARD,
    MAPPING_STATUS_SUPPLEMENTAL,
    load_project_standard_mapping,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_SCOPE_REVIEW_PATH = (
    BACKEND_ROOT / "data" / "drawing_project_mappings" / "biz2x_sample_answer_scope_review.json"
)

ACTION_KEEP_GBT = "keep_gbt_candidate"
ACTION_CONFIRM_UNIT = "confirm_standard_unit_or_convert_to_supplement"
ACTION_KEEP_SUPPLEMENTAL = "keep_supplemental_candidate"
ACTION_HOLD_OTHER_SPECIALTY = "hold_other_specialty_until_scope_confirmed"
ACTION_KEEP_FITOUT_SUPPLEMENTAL = "keep_fitout_supplemental_candidate"

SCOPE_REVIEW_CSV_HEADERS = [
    "复核编号",
    "映射编号",
    "人工Sheet",
    "人工行号",
    "项目分类",
    "人工项目名称",
    "人工单位",
    "映射状态",
    "标准项目编码",
    "标准项目名称",
    "单位校验",
    "复核动作",
    "范围分组",
    "是否允许进入四字段候选",
    "最低识别置信度",
    "是否需要业务确认",
    "最终算量状态",
    "误识别防护说明",
    "复核建议",
]


def build_project_scope_review(mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    mapping_data = mapping if mapping is not None else load_project_standard_mapping(DEFAULT_PROJECT_STANDARD_MAPPING_PATH)
    rows: list[dict[str, Any]] = []
    for index, mapping_row in enumerate(mapping_data.get("rows") or [], start=1):
        decision = _scope_decision(mapping_row)
        rows.append(
            {
                "scope_review_id": f"BIZ2xR22-{index:04d}",
                "mapping_id": mapping_row.get("mapping_id", ""),
                "source_sheet_name": mapping_row.get("source_sheet_name", ""),
                "source_row_no": mapping_row.get("source_row_no", ""),
                "manual_item_name": mapping_row.get("manual_item_name", ""),
                "manual_feature": mapping_row.get("manual_feature", ""),
                "manual_unit": mapping_row.get("manual_unit", ""),
                "manual_quantity": mapping_row.get("manual_quantity", ""),
                "category": mapping_row.get("category", ""),
                "category_label": mapping_row.get("category_label", ""),
                "mapping_status": mapping_row.get("mapping_status", ""),
                "standard_item_code": mapping_row.get("standard_item_code", ""),
                "standard_item_name": mapping_row.get("standard_item_name", ""),
                "standard_unit_options": mapping_row.get("standard_unit_options", []),
                "unit_check_status": mapping_row.get("unit_check_status", ""),
                **decision,
            }
        )

    action_counts = Counter(row["review_action"] for row in rows)
    scope_counts = Counter(row["scope_bucket"] for row in rows)
    candidate_allowed_count = sum(1 for row in rows if row["recognition_allowed"])
    confirmation_count = sum(1 for row in rows if row["business_confirmation_required"])
    issue_rows = [
        row
        for row in rows
        if row["business_confirmation_required"]
        or row["review_action"] in {ACTION_CONFIRM_UNIT, ACTION_HOLD_OTHER_SPECIALTY}
    ]
    return {
        "ok": True,
        "phase": "BIZ-2x-R2-2-unit-and-supplemental-scope-review",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": {
            "mapping_entry_count": (mapping_data.get("summary") or {}).get("mapping_entry_count", 0),
            "mapping_json": str(DEFAULT_PROJECT_STANDARD_MAPPING_PATH),
            "policy": "review_unit_conflicts_and_supplemental_scope_before_quantity_generation",
        },
        "summary": {
            "scope_review_entry_count": len(rows),
            "issue_row_count": len(issue_rows),
            "recognition_allowed_count": candidate_allowed_count,
            "business_confirmation_required_count": confirmation_count,
            "review_action_counts": dict(action_counts.most_common()),
            "scope_bucket_counts": dict(scope_counts.most_common()),
            "safe_for_final_quantity": False,
        },
        "rows": rows,
        "issue_rows": issue_rows,
        "notes": [
            "R2-2 只确认单位冲突和补充清单范围边界，不生成最终工程量。",
            "其它专业项目允许保留为强匹配候选，但默认需要业务确认范围；弱匹配不进入四字段候选。",
            "单位冲突项目不得复用系统工程量，需确认按标准单位重算或降级为补充清单。",
        ],
    }


def load_project_scope_review(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or DEFAULT_PROJECT_SCOPE_REVIEW_PATH)
    if not target.exists():
        return {"ok": False, "summary": {"scope_review_entry_count": 0}, "rows": []}
    return json.loads(target.read_text(encoding="utf-8"))


def find_scope_review_for_mapping_entry(
    mapping_entry: dict[str, Any] | None,
    scope_review: dict[str, Any] | None,
) -> dict[str, Any]:
    if not mapping_entry or not scope_review or not scope_review.get("rows"):
        return {}
    mapping_id = _clean_text(mapping_entry.get("mapping_id"))
    if mapping_id:
        for row in scope_review.get("rows") or []:
            if _clean_text(row.get("mapping_id")) == mapping_id:
                return row
    row_key = (
        _clean_text(mapping_entry.get("source_sheet_name")),
        _clean_text(mapping_entry.get("source_row_no")),
        _normalize(mapping_entry.get("manual_item_name")),
    )
    for row in scope_review.get("rows") or []:
        if (
            _clean_text(row.get("source_sheet_name")),
            _clean_text(row.get("source_row_no")),
            _normalize(row.get("manual_item_name")),
        ) == row_key:
            return row
    return {}


def write_project_scope_review_outputs(
    review: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_R2_单位冲突与补充清单边界复核_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{file_stem}.json"
    csv_path = directory / f"{file_stem}.csv"
    issue_csv_path = directory / f"{file_stem}_需复核清单.csv"
    markdown_path = directory / f"{file_stem}.md"
    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_scope_csv(csv_path, review.get("rows") or [])
    _write_scope_csv(issue_csv_path, review.get("issue_rows") or [])
    markdown_path.write_text(build_project_scope_review_markdown(review), encoding="utf-8")
    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "issue_csv": str(issue_csv_path),
        "markdown": str(markdown_path),
    }


def build_project_scope_review_markdown(review: dict[str, Any]) -> str:
    summary = review.get("summary") or {}
    lines = [
        "# BIZ-2x R2-2 单位冲突与补充清单边界复核",
        "",
        f"- 生成时间：{review.get('generated_at', '-')}",
        f"- 复核行数：{summary.get('scope_review_entry_count', 0)}",
        f"- 需复核行数：{summary.get('issue_row_count', 0)}",
        f"- 允许进入四字段候选行数：{summary.get('recognition_allowed_count', 0)}",
        f"- 需业务确认行数：{summary.get('business_confirmation_required_count', 0)}",
        f"- 复核动作分布：{summary.get('review_action_counts', {})}",
        f"- 范围分组分布：{summary.get('scope_bucket_counts', {})}",
        "",
        "## 需复核清单",
        "",
        "| 复核编号 | 人工行号 | 项目 | 动作 | 范围分组 | 最低置信度 | 建议 |",
        "| --- | ---: | --- | --- | --- | ---: | --- |",
    ]
    for row in (review.get("issue_rows") or [])[:150]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("scope_review_id")),
                    _md(row.get("source_row_no")),
                    _md(row.get("manual_item_name")),
                    _md(row.get("review_action")),
                    _md(row.get("scope_bucket")),
                    _md(f"{float(row.get('recognition_min_score') or 0):.2f}"),
                    _md(row.get("review_note")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- `confirm_standard_unit_or_convert_to_supplement` 行必须先确认单位口径，不能直接进入最终算量。",
            "- `hold_other_specialty_until_scope_confirmed` 行属于电气、给排水、洁具或设备安装边界，默认只允许强匹配进入候选。",
            "- `keep_supplemental_candidate` 行可保留为补充清单候选，但仍需后续补充规则和 CAD 证据。",
        ]
    )
    return "\n".join(lines) + "\n"


def _scope_decision(row: dict[str, Any]) -> dict[str, Any]:
    mapping_status = _clean_text(row.get("mapping_status"))
    unit_check = _clean_text(row.get("unit_check_status"))
    category = _clean_text(row.get("category"))
    item_name = _clean_text(row.get("manual_item_name"))
    text = f"{item_name} {_clean_text(row.get('manual_feature'))}"

    if unit_check == "unit_conflict_needs_confirmation":
        return {
            "review_action": ACTION_CONFIRM_UNIT,
            "scope_bucket": "unit_conflict",
            "recognition_allowed": True,
            "recognition_min_score": 0.72,
            "business_confirmation_required": True,
            "final_quantity_status": "待 R2-2 单位口径确认，不进入最终算量",
            "false_positive_guard": "保留候选，但不得复用系统工程量；需确认按标准单位重算或改为补充清单",
            "review_note": "单位与 GB/T 标准单位不一致，优先确认是否按标准单位重算",
        }

    if mapping_status in {MAPPING_STATUS_STANDARD, MAPPING_STATUS_MERGE}:
        return {
            "review_action": ACTION_KEEP_GBT,
            "scope_bucket": "gbt_standard_or_merge",
            "recognition_allowed": True,
            "recognition_min_score": 0.58,
            "business_confirmation_required": False,
            "final_quantity_status": "待 CAD 区域/边界绑定后按标准规则计算",
            "false_positive_guard": "按 active GB/T 字段口径保留，后续必须绑定 CAD 证据",
            "review_note": "标准映射和单位口径可作为候选，仍需 CAD 证据后算量",
        }

    if mapping_status == MAPPING_STATUS_SUPPLEMENTAL:
        if category == "demolition":
            return {
                "review_action": ACTION_KEEP_SUPPLEMENTAL,
                "scope_bucket": "supplemental_demolition",
                "recognition_allowed": True,
                "recognition_min_score": 0.66,
                "business_confirmation_required": False,
                "final_quantity_status": "拆除补充清单项目，待补充拆除规则和 CAD 证据",
                "false_positive_guard": "必须命中拆除/铲除强项目词，说明文字不进入候选",
                "review_note": "拆除类按补充清单候选保留",
            }
        if category == "measure" or any(term in text for term in ("保洁", "保护", "运输")):
            return {
                "review_action": ACTION_KEEP_SUPPLEMENTAL,
                "scope_bucket": "supplemental_measure",
                "recognition_allowed": True,
                "recognition_min_score": 0.82,
                "business_confirmation_required": True,
                "final_quantity_status": "措施/保护补充清单项目，待业务确认计量口径",
                "false_positive_guard": "只允许强匹配进入候选，避免材料说明误入措施项目",
                "review_note": "措施类建议保留，但需确认计量口径和是否纳入本次报价",
            }
        if category in {"lighting_electrical", "sanitary"} or _is_other_specialty_text(text):
            return {
                "review_action": ACTION_HOLD_OTHER_SPECIALTY,
                "scope_bucket": "other_specialty_scope_pending",
                "recognition_allowed": True,
                "recognition_min_score": 0.82,
                "business_confirmation_required": True,
                "final_quantity_status": "其它专业/设备安装候选，待业务确认是否纳入本次报价",
                "false_positive_guard": "只允许强匹配进入四字段候选，不匹配 GB/T 装饰装修标准项目",
                "review_note": "疑似电气、给排水、洁具或设备安装，需确认范围后再沉淀补充规则",
            }
        return {
            "review_action": ACTION_KEEP_FITOUT_SUPPLEMENTAL,
            "scope_bucket": "supplemental_fitout_scope_pending",
            "recognition_allowed": True,
            "recognition_min_score": 0.76,
            "business_confirmation_required": True,
            "final_quantity_status": "装饰补充清单候选，待 R2/R4 补充规则确认",
            "false_positive_guard": "保留人工答案中的装饰补充项，弱匹配需人工复核",
            "review_note": "装饰范围内但 active GB/T 未直接覆盖，建议作为补充清单候选",
        }

    return {
        "review_action": "review_manually",
        "scope_bucket": "manual_review",
        "recognition_allowed": False,
        "recognition_min_score": 0.98,
        "business_confirmation_required": True,
        "final_quantity_status": "待人工确认是否纳入",
        "false_positive_guard": "默认不进入候选",
        "review_note": "映射状态未覆盖，需人工确认",
    }


def _is_other_specialty_text(text: str) -> bool:
    return any(
        term in text
        for term in (
            "配电",
            "电缆",
            "电气",
            "配管",
            "配线",
            "插座",
            "开关",
            "灯具",
            "给水",
            "排水",
            "阀门",
            "水表",
            "地漏",
            "马桶",
            "花洒",
            "龙头",
            "热水器",
            "排气扇",
        )
    )


def _write_scope_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCOPE_REVIEW_CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "复核编号": row.get("scope_review_id", ""),
                    "映射编号": row.get("mapping_id", ""),
                    "人工Sheet": row.get("source_sheet_name", ""),
                    "人工行号": row.get("source_row_no", ""),
                    "项目分类": row.get("category_label", ""),
                    "人工项目名称": row.get("manual_item_name", ""),
                    "人工单位": row.get("manual_unit", ""),
                    "映射状态": row.get("mapping_status", ""),
                    "标准项目编码": row.get("standard_item_code", ""),
                    "标准项目名称": row.get("standard_item_name", ""),
                    "单位校验": row.get("unit_check_status", ""),
                    "复核动作": row.get("review_action", ""),
                    "范围分组": row.get("scope_bucket", ""),
                    "是否允许进入四字段候选": "是" if row.get("recognition_allowed") else "否",
                    "最低识别置信度": f"{float(row.get('recognition_min_score') or 0):.2f}",
                    "是否需要业务确认": "是" if row.get("business_confirmation_required") else "否",
                    "最终算量状态": row.get("final_quantity_status", ""),
                    "误识别防护说明": row.get("false_positive_guard", ""),
                    "复核建议": row.get("review_note", ""),
                }
            )


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value)).lower()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", "<br>")
