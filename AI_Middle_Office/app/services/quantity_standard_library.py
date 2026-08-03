from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STANDARD_LIBRARY_PATH = BACKEND_ROOT / "data" / "standards" / "gbtn50854_2024_min_seed.json"

ACTIVE_STATUS = "active"
DRAFT_STATUS = "draft_needs_standard_verification"
ARCHIVED_STATUS = "archived"
ALLOWED_STATUSES = {ACTIVE_STATUS, DRAFT_STATUS, ARCHIVED_STATUS}

STANDARD_LIBRARY_VERSION = "biz2x-gbt50854-2024-standard-v0"


class QuantityStandardLibraryError(ValueError):
    pass


@dataclass(frozen=True)
class QuantityStandardItem:
    item_code: str
    official_item_code: str
    duplicate_item_code_sequence: int
    item_name: str
    chapter_name: str
    status: str
    verification_status: str
    feature_fields: tuple[dict[str, Any], ...]
    unit_options: tuple[str, ...]
    quantity_rule: dict[str, Any]
    drawing_evidence_requirements: tuple[str, ...]
    keywords: tuple[str, ...]
    exclusion_keywords: tuple[str, ...]
    source_note: str
    no_feature_fields_in_standard: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuantityStandardItem":
        if not isinstance(raw, dict):
            raise QuantityStandardLibraryError("standard item must be an object")
        status = _clean_text(raw.get("status"))
        if status not in ALLOWED_STATUSES:
            raise QuantityStandardLibraryError(f"invalid status for {raw.get('item_code')}: {status}")
        item = cls(
            item_code=_required_text(raw, "item_code"),
            official_item_code=_clean_text(raw.get("official_item_code")) or _required_text(raw, "item_code"),
            duplicate_item_code_sequence=int(raw.get("duplicate_item_code_sequence") or 0),
            item_name=_required_text(raw, "item_name"),
            chapter_name=_required_text(raw, "chapter_name"),
            status=status,
            verification_status=_clean_text(raw.get("verification_status")),
            feature_fields=tuple(_list_of_dicts(raw.get("feature_fields"), field_name="feature_fields")),
            unit_options=tuple(_list_of_text(raw.get("unit_options"))),
            quantity_rule=dict(raw.get("quantity_rule") or {}),
            drawing_evidence_requirements=tuple(_list_of_text(raw.get("drawing_evidence_requirements"))),
            keywords=tuple(_list_of_text(raw.get("keywords"))),
            exclusion_keywords=tuple(_list_of_text(raw.get("exclusion_keywords"))),
            source_note=_clean_text(raw.get("source_note")),
            no_feature_fields_in_standard=bool(raw.get("no_feature_fields_in_standard")),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if not self.item_code:
            raise QuantityStandardLibraryError("item_code is required")
        if not self.item_name:
            raise QuantityStandardLibraryError(f"{self.item_code}: item_name is required")
        if not self.unit_options:
            raise QuantityStandardLibraryError(f"{self.item_code}: unit_options is required")
        if not isinstance(self.quantity_rule, dict):
            raise QuantityStandardLibraryError(f"{self.item_code}: quantity_rule must be an object")
        if self.status == ACTIVE_STATUS:
            _validate_active_item(self)

    @property
    def required_feature_names(self) -> list[str]:
        return [
            _clean_text(field.get("name"))
            for field in self.feature_fields
            if isinstance(field, dict) and bool(field.get("required"))
        ]

    @property
    def feature_names(self) -> list[str]:
        return [_clean_text(field.get("name")) for field in self.feature_fields if isinstance(field, dict)]

    @property
    def quantity_formula_type(self) -> str:
        return _clean_text(self.quantity_rule.get("formula_type"))

    @property
    def quantity_rule_status(self) -> str:
        return _clean_text(self.quantity_rule.get("rule_status"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_code": self.item_code,
            "official_item_code": self.official_item_code,
            "duplicate_item_code_sequence": self.duplicate_item_code_sequence,
            "item_name": self.item_name,
            "chapter_name": self.chapter_name,
            "status": self.status,
            "verification_status": self.verification_status,
            "feature_fields": list(self.feature_fields),
            "unit_options": list(self.unit_options),
            "quantity_rule": self.quantity_rule,
            "drawing_evidence_requirements": list(self.drawing_evidence_requirements),
            "keywords": list(self.keywords),
            "exclusion_keywords": list(self.exclusion_keywords),
            "source_note": self.source_note,
            "no_feature_fields_in_standard": self.no_feature_fields_in_standard,
        }


@dataclass(frozen=True)
class QuantityStandardLibrary:
    version: str
    standard: dict[str, Any]
    items: tuple[QuantityStandardItem, ...]
    out_of_scope_policy: dict[str, Any]
    source_path: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, source_path: str = "") -> "QuantityStandardLibrary":
        version = _required_text(raw, "version")
        if version != STANDARD_LIBRARY_VERSION:
            raise QuantityStandardLibraryError(f"unsupported standard library version: {version}")
        standard = raw.get("standard")
        if not isinstance(standard, dict):
            raise QuantityStandardLibraryError("standard metadata is required")
        items_raw = raw.get("items")
        if not isinstance(items_raw, list):
            raise QuantityStandardLibraryError("items must be a list")
        items = tuple(QuantityStandardItem.from_dict(item) for item in items_raw)
        _validate_unique_item_codes(items)
        return cls(
            version=version,
            standard=standard,
            items=items,
            out_of_scope_policy=dict(raw.get("out_of_scope_policy") or {}),
            source_path=source_path,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "standard": self.standard,
            "items": [item.as_dict() for item in self.items],
            "out_of_scope_policy": self.out_of_scope_policy,
            "source_path": self.source_path,
        }


def load_quantity_standard_library(path: str | Path | None = None) -> QuantityStandardLibrary:
    source = Path(path) if path else DEFAULT_STANDARD_LIBRARY_PATH
    if not source.exists():
        raise QuantityStandardLibraryError(f"standard library file not found: {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuantityStandardLibraryError(f"invalid standard library JSON: {source}") from exc
    return QuantityStandardLibrary.from_dict(raw, source_path=str(source))


def quantity_standard_summary(library: QuantityStandardLibrary) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    chapter_counts: dict[str, int] = {}
    formula_counts: dict[str, int] = {}
    required_feature_total = 0
    for item in library.items:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1
        chapter_counts[item.chapter_name] = chapter_counts.get(item.chapter_name, 0) + 1
        formula = item.quantity_formula_type or "missing"
        formula_counts[formula] = formula_counts.get(formula, 0) + 1
        required_feature_total += len(item.required_feature_names)
    return {
        "version": library.version,
        "standard_code": library.standard.get("code"),
        "standard_name": library.standard.get("name"),
        "source_text_status": library.standard.get("source_text_status"),
        "item_count": len(library.items),
        "active_count": status_counts.get(ACTIVE_STATUS, 0),
        "draft_count": status_counts.get(DRAFT_STATUS, 0),
        "archived_count": status_counts.get(ARCHIVED_STATUS, 0),
        "status_counts": status_counts,
        "chapter_counts": dict(sorted(chapter_counts.items())),
        "formula_counts": dict(sorted(formula_counts.items())),
        "required_feature_total": required_feature_total,
        "safe_for_final_generation": status_counts.get(ACTIVE_STATUS, 0) > 0,
        "strict_rules": list(library.standard.get("strict_rules") or []),
    }


def search_quantity_standard_items(
    library: QuantityStandardLibrary,
    query: str,
    *,
    include_draft: bool = True,
    limit: int = 20,
) -> list[dict[str, Any]]:
    normalized_query = _normalize_search_text(query)
    if not normalized_query:
        return []
    results: list[tuple[float, QuantityStandardItem, list[str]]] = []
    for item in library.items:
        if item.status != ACTIVE_STATUS and not include_draft:
            continue
        score, matched = _item_search_score(item, normalized_query)
        if score > 0:
            results.append((score, item, matched))
    results.sort(key=lambda entry: (-entry[0], entry[1].item_code))
    return [
        {
            "score": score,
            "matched_fields": matched,
            "item": item.as_dict(),
        }
        for score, item, matched in results[:limit]
    ]


def build_quantity_standard_markdown(library: QuantityStandardLibrary) -> str:
    summary = quantity_standard_summary(library)
    lines = [
        "# BIZ-2x GB/T 50854-2024 标准库最小可用版预览",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 标准：{summary['standard_code']} {summary['standard_name']}",
        f"- 文本状态：{summary['source_text_status']}",
        f"- 条目数量：{summary['item_count']}",
        f"- active：{summary['active_count']}，draft：{summary['draft_count']}，archived：{summary['archived_count']}",
        f"- 是否可直接用于最终清单生成：{'是' if summary['safe_for_final_generation'] else '否，仍需 OCR/人工校对后启用 active'}",
        "",
        "## 强约束",
        "",
    ]
    for rule in summary["strict_rules"]:
        lines.append(f"- {rule}")
    lines.extend(["", "## 章节分布", ""])
    for chapter, count in summary["chapter_counts"].items():
        lines.append(f"- {chapter}: {count}")
    lines.extend(["", "## 条目明细", ""])
    for item in library.items:
        required = "、".join(item.required_feature_names) or "-"
        features = "、".join(item.feature_names) or "-"
        units = "、".join(item.unit_options) or "-"
        evidence = "、".join(item.drawing_evidence_requirements) or "-"
        lines.extend(
            [
                f"### {item.item_code} {item.item_name}",
                "",
                f"- 状态：{item.status}",
                f"- 校对状态：{item.verification_status}",
                f"- 章节：{item.chapter_name}",
                f"- 单位：{units}",
                f"- 必填项目特征：{required}",
                f"- 全部项目特征字段：{features}",
                f"- 工程量规则状态：{item.quantity_rule_status}",
                f"- 工程量规则候选：{_clean_text(item.quantity_rule.get('rule_text'))}",
                f"- 图纸证据需求：{evidence}",
                f"- 关键词：{'、'.join(item.keywords) or '-'}",
                f"- 说明：{item.source_note or '-'}",
                "",
            ]
        )
    return "\n".join(lines)


def build_quantity_standard_csv_rows(library: QuantityStandardLibrary) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in library.items:
        rows.append(
            {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "chapter_name": item.chapter_name,
                "status": item.status,
                "verification_status": item.verification_status,
                "unit_options": " / ".join(item.unit_options),
                "required_feature_fields": " / ".join(item.required_feature_names),
                "all_feature_fields": " / ".join(item.feature_names),
                "quantity_rule_status": item.quantity_rule_status,
                "quantity_formula_type": item.quantity_formula_type,
                "quantity_rule_text": _clean_text(item.quantity_rule.get("rule_text")),
                "drawing_evidence_requirements": " / ".join(item.drawing_evidence_requirements),
                "keywords": " / ".join(item.keywords),
                "exclusion_keywords": " / ".join(item.exclusion_keywords),
                "source_note": item.source_note,
            }
        )
    return rows


def build_quantity_standard_review_rows(library: QuantityStandardLibrary) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in library.items:
        feature_fields = list(item.feature_fields) or [{"name": "", "required": False}]
        for index, field in enumerate(feature_fields, start=1):
            rows.append(
                {
                    "review_status": "pending",
                    "activation_gate": "not_ready_pending_standard_review",
                    "standard_code": _clean_text(library.standard.get("code")),
                    "standard_name": _clean_text(library.standard.get("name")),
                    "source_text_status": _clean_text(library.standard.get("source_text_status")),
                    "item_code": item.item_code,
                    "candidate_item_name": item.item_name,
                    "candidate_chapter_name": item.chapter_name,
                    "candidate_status": item.status,
                    "candidate_verification_status": item.verification_status,
                    "standard_page": "",
                    "official_item_code": "",
                    "official_item_name": "",
                    "official_chapter_name": "",
                    "feature_field_order": index,
                    "candidate_feature_field": _clean_text(field.get("name")),
                    "candidate_feature_required": "yes" if bool(field.get("required")) else "no",
                    "official_feature_field": "",
                    "official_feature_required": "",
                    "feature_source_excerpt": "",
                    "candidate_unit_options": " / ".join(item.unit_options),
                    "official_unit_options": "",
                    "candidate_quantity_formula_type": item.quantity_formula_type,
                    "candidate_quantity_rule": _clean_text(item.quantity_rule.get("rule_text")),
                    "official_quantity_rule": "",
                    "quantity_rule_source_excerpt": "",
                    "candidate_drawing_evidence_requirements": " / ".join(item.drawing_evidence_requirements),
                    "official_drawing_evidence_requirements": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "review_note": "",
                    "review_result": "",
                }
            )
    return rows


def build_quantity_standard_business_review_rows(library: QuantityStandardLibrary) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_number, row in enumerate(build_quantity_standard_review_rows(library), start=1):
        rows.append(
            {
                "序号": row_number,
                "核验状态": _review_status_label(row.get("review_status")),
                "启用门禁说明": _activation_gate_label(row.get("activation_gate")),
                "标准编号": row.get("standard_code", ""),
                "标准名称": row.get("standard_name", ""),
                "标准PDF文本状态": _source_text_status_label(row.get("source_text_status")),
                "候选标准项目编码（系统内部）": row.get("item_code", ""),
                "候选项目名称": row.get("candidate_item_name", ""),
                "候选章节": row.get("candidate_chapter_name", ""),
                "候选状态": _item_status_label(row.get("candidate_status")),
                "候选校对状态": _verification_status_label(row.get("candidate_verification_status")),
                "标准页码（人工填写）": row.get("standard_page", ""),
                "官方项目编码（人工填写）": row.get("official_item_code", ""),
                "官方项目名称（人工填写）": row.get("official_item_name", ""),
                "官方章节（人工填写）": row.get("official_chapter_name", ""),
                "项目特征序号": row.get("feature_field_order", ""),
                "候选项目特征字段（仅供参考）": row.get("candidate_feature_field", ""),
                "候选字段是否必填": _yes_no_label(row.get("candidate_feature_required")),
                "官方项目特征字段（人工填写）": row.get("official_feature_field", ""),
                "官方字段是否必填（人工填写）": row.get("official_feature_required", ""),
                "项目特征原文摘录（人工填写）": row.get("feature_source_excerpt", ""),
                "候选单位（仅供参考）": row.get("candidate_unit_options", ""),
                "官方单位（人工填写）": row.get("official_unit_options", ""),
                "候选工程量规则类型（仅供参考）": _formula_type_label(row.get("candidate_quantity_formula_type")),
                "候选工程量计算规则（仅供参考）": row.get("candidate_quantity_rule", ""),
                "官方工程量计算规则（人工填写）": row.get("official_quantity_rule", ""),
                "工程量规则原文摘录（人工填写）": row.get("quantity_rule_source_excerpt", ""),
                "候选图纸证据要求（仅供参考）": row.get("candidate_drawing_evidence_requirements", ""),
                "官方图纸证据要求（人工填写）": row.get("official_drawing_evidence_requirements", ""),
                "核验人": row.get("reviewer", ""),
                "核验日期": row.get("reviewed_at", ""),
                "问题说明/备注": row.get("review_note", ""),
                "核验结论（通过/有问题）": row.get("review_result", ""),
            }
        )
    return rows


def quantity_standard_review_summary(
    library: QuantityStandardLibrary,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    review_rows = rows if rows is not None else build_quantity_standard_review_rows(library)
    pending_rows = sum(1 for row in review_rows if row.get("review_status") == "pending")
    feature_rows = sum(1 for row in review_rows if row.get("candidate_feature_field"))
    return {
        **quantity_standard_summary(library),
        "review_row_count": len(review_rows),
        "feature_review_row_count": feature_rows,
        "pending_review_row_count": pending_rows,
        "activation_ready": False,
        "activation_block_reason": (
            "standard PDF has no extractable text in current environment; "
            "fill official feature fields, official units, and official quantity rules before enabling active items"
        ),
        "required_manual_review_fields": [
            "标准页码（人工填写）",
            "官方项目编码（人工填写）",
            "官方项目名称（人工填写）",
            "官方项目特征字段（人工填写）",
            "官方单位（人工填写）",
            "官方工程量计算规则（人工填写）",
            "工程量规则原文摘录（人工填写）",
            "核验状态",
            "核验人",
            "核验日期",
            "核验结论（通过/有问题）",
        ],
    }


def build_quantity_standard_review_markdown(library: QuantityStandardLibrary) -> str:
    rows = build_quantity_standard_review_rows(library)
    summary = quantity_standard_review_summary(library, rows)
    lines = [
        "# BIZ-2x-1 GB/T 50854-2024 标准库人工校对包",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 标准：{summary['standard_code']} {summary['standard_name']}",
        f"- PDF 文本状态：{summary['source_text_status']}",
        f"- 候选项目数：{summary['item_count']}",
        f"- 校对行数：{summary['review_row_count']}",
        f"- 当前 active 项目数：{summary['active_count']}",
        f"- 是否可直接用于最终清单生成：{'是' if summary['safe_for_final_generation'] else '否，仍需 OCR/人工校对后启用 active'}",
        "",
        "## 使用规则",
        "",
        "1. 候选字段只作为定位线索，不能直接作为最终标准口径。",
        "2. `official_feature_field` 必须按 GB/T 50854-2024 原文项目特征字段填写。",
        "3. “官方工程量计算规则（人工填写）”必须按 GB/T 50854-2024 原文工程量计算规则填写。",
        "4. “工程量规则原文摘录（人工填写）”应记录可追溯的原文摘录或页码定位。",
        "5. 只有完成校对并将条目标记为 `verified_against_standard` 后，后续 DWG 识图结果才能引用该条目生成最终清单。",
        "6. 每行最后一列“核验结论（通过/有问题）”请填写“通过”或“有问题”。",
        "",
        "## 必填校对列",
        "",
    ]
    for field in summary["required_manual_review_fields"]:
        lines.append(f"- `{field}`")
    lines.extend(["", "## 候选项目概览", "", "| 候选编码 | 候选项目名称 | 章节 | 候选单位 | 特征字段数 | 计算规则类型 | 状态 |", "| --- | --- | --- | --- | ---: | --- | --- |"])
    for item in library.items:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.item_code,
                    item.item_name,
                    item.chapter_name,
                    " / ".join(item.unit_options),
                    str(len(item.feature_fields)),
                    item.quantity_formula_type or "-",
                    item.status,
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 输出说明",
            "",
            "- CSV：用于 Excel 打开并逐行校对。",
            "- JSON：用于后续导入校对结果或生成 active 标准库的结构化依据。",
            "- Markdown：用于交接说明和审计留痕。",
        ]
    )
    return "\n".join(lines)


def write_quantity_standard_review_pack(
    library: QuantityStandardLibrary,
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x1_GBT50854标准库人工校对表_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}.csv"
    json_path = target_dir / f"{file_stem}.json"

    rows = build_quantity_standard_review_rows(library)
    business_rows = build_quantity_standard_business_review_rows(library)
    payload = {
        "summary": quantity_standard_review_summary(library, rows),
        "review_rows": rows,
        "business_review_rows": business_rows,
    }
    markdown_path.write_text(build_quantity_standard_review_markdown(library), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = list(business_rows[0].keys()) if business_rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(business_rows)

    return {
        "markdown": str(markdown_path),
        "csv": str(csv_path),
        "json": str(json_path),
    }


def write_quantity_standard_outputs(
    library: QuantityStandardLibrary,
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"biz2x1_quantity_standard_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}.csv"
    json_path = target_dir / f"{file_stem}.json"

    markdown_path.write_text(build_quantity_standard_markdown(library), encoding="utf-8")
    json_path.write_text(json.dumps(library.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    rows = build_quantity_standard_csv_rows(library)
    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "markdown": str(markdown_path),
        "csv": str(csv_path),
        "json": str(json_path),
    }


def _validate_active_item(item: QuantityStandardItem) -> None:
    if item.verification_status != "verified_against_standard":
        raise QuantityStandardLibraryError(f"{item.item_code}: active item must be verified_against_standard")
    if not item.feature_fields and not item.no_feature_fields_in_standard:
        raise QuantityStandardLibraryError(f"{item.item_code}: active item must define feature_fields")
    if not item.required_feature_names and not item.no_feature_fields_in_standard:
        raise QuantityStandardLibraryError(f"{item.item_code}: active item must define required feature fields")
    if not _clean_text(item.quantity_rule.get("rule_text")):
        raise QuantityStandardLibraryError(f"{item.item_code}: active item must define quantity_rule.rule_text")
    if item.quantity_rule_status != "verified_against_standard":
        raise QuantityStandardLibraryError(f"{item.item_code}: active quantity rule must be verified")
    if not item.drawing_evidence_requirements:
        raise QuantityStandardLibraryError(f"{item.item_code}: active item must define drawing evidence requirements")


def _review_status_label(value: Any) -> str:
    return {
        "pending": "待核验",
        "passed": "已核验通过",
        "problem": "有问题",
    }.get(_clean_text(value), _clean_text(value))


def _activation_gate_label(value: Any) -> str:
    return {
        "not_ready_pending_standard_review": "未完成标准核验，不能启用为正式标准条目",
    }.get(_clean_text(value), _clean_text(value))


def _source_text_status_label(value: Any) -> str:
    return {
        "pdf_image_only_pending_ocr": "PDF 暂无可抽取文本，待 OCR 或人工核验",
    }.get(_clean_text(value), _clean_text(value))


def _item_status_label(value: Any) -> str:
    return {
        ACTIVE_STATUS: "已启用",
        DRAFT_STATUS: "草稿，待标准核验",
        ARCHIVED_STATUS: "已归档",
    }.get(_clean_text(value), _clean_text(value))


def _verification_status_label(value: Any) -> str:
    return {
        "pending_pdf_ocr_or_manual_review": "待 PDF OCR 或人工核验",
        "verified_against_standard": "已按标准原文核验",
    }.get(_clean_text(value), _clean_text(value))


def _yes_no_label(value: Any) -> str:
    cleaned = _clean_text(value).lower()
    if cleaned in {"yes", "true", "1"}:
        return "是"
    if cleaned in {"no", "false", "0"}:
        return "否"
    return _clean_text(value)


def _formula_type_label(value: Any) -> str:
    return {
        "area": "面积",
        "vertical_area": "垂直面积",
        "ceiling_area": "天棚面积",
        "length": "长度",
        "count": "数量",
        "count_or_area": "数量或面积",
        "mixed_by_object": "按项目对象分别计算",
    }.get(_clean_text(value), _clean_text(value))


def _validate_unique_item_codes(items: Iterable[QuantityStandardItem]) -> None:
    seen: set[str] = set()
    for item in items:
        if item.item_code in seen:
            raise QuantityStandardLibraryError(f"duplicate item_code: {item.item_code}")
        seen.add(item.item_code)


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = _clean_text(raw.get(key))
    if not value:
        raise QuantityStandardLibraryError(f"{key} is required")
    return value


def _list_of_dicts(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise QuantityStandardLibraryError(f"{field_name} must be a list")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise QuantityStandardLibraryError(f"{field_name} entries must be objects")
        result.append(dict(item))
    return result


def _list_of_text(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise QuantityStandardLibraryError("expected a list")
    return [text for text in (_clean_text(item) for item in value) if text]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


_SEARCH_SPLITTER = re.compile(r"[\s\-_—–,，、;；:：|+&()（）\[\]{}【】<>《》\"'“”‘’/\\]+")


def _normalize_search_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value)).lower()
    return _SEARCH_SPLITTER.sub("", text)


def _item_search_score(item: QuantityStandardItem, normalized_query: str) -> tuple[float, list[str]]:
    fields = {
        "item_name": item.item_name,
        "chapter_name": item.chapter_name,
        "keywords": " ".join(item.keywords),
        "feature_fields": " ".join(item.feature_names),
    }
    score = 0.0
    matched: list[str] = []
    for field, value in fields.items():
        normalized_value = _normalize_search_text(value)
        if not normalized_value:
            continue
        if normalized_query == normalized_value:
            score += 10.0
            matched.append(field)
        elif normalized_query in normalized_value or normalized_value in normalized_query:
            score += 5.0
            matched.append(field)
    for keyword in item.keywords:
        normalized_keyword = _normalize_search_text(keyword)
        if normalized_keyword and normalized_keyword in normalized_query:
            score += 3.0
            if "keywords" not in matched:
                matched.append("keywords")
    for keyword in item.exclusion_keywords:
        normalized_keyword = _normalize_search_text(keyword)
        if normalized_keyword and normalized_keyword in normalized_query:
            score -= 4.0
            if "exclusion_keywords" not in matched:
                matched.append("exclusion_keywords")
    return max(0.0, score), matched
