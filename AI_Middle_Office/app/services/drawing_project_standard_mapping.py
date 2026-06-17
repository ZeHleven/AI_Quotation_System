from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.drawing_project_lexicon import (
    CATEGORY_LABELS,
    DEFAULT_PROJECT_LEXICON_PATH,
    DEFAULT_SAMPLE_ANSWER_XLSX,
    build_recognition_terms,
    classify_project_category,
    extract_material_codes,
    infer_standard_mapping,
    load_project_lexicon,
)
from app.services.quantity_standard_library import QuantityStandardItem, QuantityStandardLibrary


BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = BACKEND_ROOT.parent
DEFAULT_PROJECT_STANDARD_MAPPING_PATH = (
    BACKEND_ROOT / "data" / "drawing_project_mappings" / "biz2x_sample_answer_standard_mapping.json"
)

MAPPING_STATUS_STANDARD = "GB/T 标准项目"
MAPPING_STATUS_MERGE = "GB/T 可归并项目"
MAPPING_STATUS_SUPPLEMENTAL = "补充清单项目"
MAPPING_STATUS_OUT_OF_SCOPE = "暂不纳入项目"

SUPPLEMENTAL_CATEGORIES = {"demolition", "lighting_electrical", "measure"}
OTHER_SPECIALTY_CATEGORIES = {"sanitary", "lighting_electrical"}

MAPPING_CSV_HEADERS = [
    "映射编号",
    "人工行号",
    "分类",
    "映射状态",
    "人工项目名称",
    "人工项目特征",
    "人工单位",
    "人工工程量",
    "材料编号",
    "标准项目编码",
    "标准项目名称",
    "标准章节",
    "标准单位",
    "单位校验",
    "项目特征字段口径",
    "项目特征模板",
    "工程量计算规则",
    "工程量公式类型",
    "识别强词",
    "映射原因",
    "复核建议",
]

TEXT_SPLITTER_RE = re.compile(r"[\s\-_—、，。；;:：|/\\()（）\[\]{}【】<>\"'“”‘’]+")
MATERIAL_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,4}\s*[-－]\s*\d{1,3})(?![A-Z0-9])", re.IGNORECASE)


def build_project_standard_mapping_from_answer_rows(
    answer_rows: list[dict[str, Any]],
    *,
    library: QuantityStandardLibrary,
    lexicon: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lexicon_data = lexicon if lexicon is not None else load_project_lexicon(DEFAULT_PROJECT_LEXICON_PATH)
    lexicon_index = _index_lexicon_entries(lexicon_data)
    rows: list[dict[str, Any]] = []

    for index, source_row in enumerate(answer_rows, start=1):
        item_name = _clean_text(source_row.get("item_name"))
        feature = _clean_text(source_row.get("feature"))
        unit = _clean_text(source_row.get("unit"))
        category = classify_project_category(item_name, feature)
        material_codes = extract_material_codes(" ".join([item_name, feature, _clean_text(source_row.get("raw_text"))]))
        lexicon_entry = _find_lexicon_entry(source_row, lexicon_index)
        standard = _resolve_standard_mapping(
            item_name=item_name,
            feature=feature,
            category=category,
            library=library,
            lexicon_entry=lexicon_entry,
        )
        mapping_status = _mapping_status(item_name, feature, category, standard)
        unit_check = _unit_check(unit, standard.get("unit_options") or [])
        feature_fields = list(standard.get("feature_fields") or [])
        strong_terms, weak_terms = build_recognition_terms(item_name, feature, category, material_codes)
        review_note = _review_note(mapping_status, category, unit_check, standard)

        rows.append(
            {
                "mapping_id": f"BIZ2xMAP-{index:04d}",
                "source_row_no": source_row.get("row_no", ""),
                "source_sheet_name": source_row.get("sheet_name", ""),
                "source_item_code": source_row.get("item_code", ""),
                "manual_item_name": item_name,
                "manual_feature": feature,
                "manual_unit": unit,
                "manual_quantity": _clean_text(source_row.get("quantity")),
                "manual_raw_text": _clean_text(source_row.get("raw_text")),
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"]),
                "material_codes": material_codes,
                "mapping_status": mapping_status,
                "standard_item_code": standard.get("item_code", ""),
                "standard_item_name": standard.get("item_name", ""),
                "standard_chapter_name": standard.get("chapter_name", ""),
                "standard_unit_options": standard.get("unit_options", []),
                "unit_check_status": unit_check,
                "standard_feature_fields": feature_fields,
                "feature_text_template": _feature_text_template(
                    mapping_status=mapping_status,
                    category_label=CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"]),
                    item_name=item_name,
                    manual_feature=feature,
                    material_codes=material_codes,
                    feature_fields=feature_fields,
                ),
                "standard_quantity_rule_text": standard.get("quantity_rule_text", ""),
                "standard_quantity_formula_type": standard.get("quantity_formula_type", ""),
                "drawing_evidence_requirements": standard.get("drawing_evidence_requirements", []),
                "recognition_strong_terms": strong_terms,
                "recognition_weak_terms": weak_terms,
                "mapping_reason": standard.get("mapping_reason") or _fallback_mapping_reason(mapping_status, category),
                "review_note": review_note,
                "allowed_for_project_candidate": mapping_status != MAPPING_STATUS_OUT_OF_SCOPE,
                "allowed_for_final_quantity": False,
                "quantity_status": _quantity_status(mapping_status, bool(standard.get("item_code"))),
                "lexicon_entry_id": (lexicon_entry or {}).get("entry_id", ""),
            }
        )

    status_counts = Counter(row["mapping_status"] for row in rows)
    category_counts = Counter(row["category"] for row in rows)
    unit_check_counts = Counter(row["unit_check_status"] for row in rows)
    mapped_standard_codes = sorted({row["standard_item_code"] for row in rows if row["standard_item_code"]})
    return {
        "ok": True,
        "phase": "BIZ-2x-R2-1-sample-answer-standard-mapping",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": {
            "manual_answer_row_count": len(answer_rows),
            "manual_xlsx": str(DEFAULT_SAMPLE_ANSWER_XLSX),
            "lexicon_json": str(DEFAULT_PROJECT_LEXICON_PATH),
            "standard_file": str(library.source_path),
            "policy": "answer_driven_mapping_table_before_cad_quantity_binding",
        },
        "summary": {
            "manual_answer_row_count": len(answer_rows),
            "mapping_entry_count": len(rows),
            "mapping_status_counts": dict(status_counts.most_common()),
            "category_counts": dict(category_counts.most_common()),
            "unit_check_counts": dict(unit_check_counts.most_common()),
            "standard_mapped_row_count": sum(
                1
                for row in rows
                if row["mapping_status"] in {MAPPING_STATUS_STANDARD, MAPPING_STATUS_MERGE}
                and row.get("standard_item_code")
            ),
            "supplemental_row_count": status_counts.get(MAPPING_STATUS_SUPPLEMENTAL, 0),
            "out_of_scope_row_count": status_counts.get(MAPPING_STATUS_OUT_OF_SCOPE, 0),
            "unique_standard_item_count": len(mapped_standard_codes),
            "unique_standard_item_codes": mapped_standard_codes,
            "safe_for_final_quantity": False,
        },
        "rows": rows,
        "notes": [
            "本映射表覆盖样例人工清单每一行，用于 R2 标准口径确认和后续识别器优先匹配。",
            "GB/T 标准项目与 GB/T 可归并项目可以输出标准项目名称、项目特征字段口径、单位与工程量规则。",
            "补充清单项目允许进入四字段候选，但不得伪写为 GB/T 项目，也不得在缺少 R3/R4 证据时生成最终工程量。",
        ],
    }


def load_project_standard_mapping(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or DEFAULT_PROJECT_STANDARD_MAPPING_PATH)
    if not target.exists():
        return {"ok": False, "summary": {"mapping_entry_count": 0}, "rows": []}
    return json.loads(target.read_text(encoding="utf-8"))


def find_standard_mapping_for_lexicon_entry(
    entry: dict[str, Any],
    mapping: dict[str, Any] | None,
) -> dict[str, Any]:
    if not mapping or not mapping.get("rows"):
        return {}
    source_row_no = _clean_text(entry.get("source_row_no"))
    source_sheet_name = _clean_text(entry.get("source_sheet_name"))
    manual_name = _normalize(entry.get("manual_item_name"))
    manual_unit = _normalize_unit(entry.get("manual_unit"))
    for row in mapping.get("rows") or []:
        if (
            source_row_no
            and source_sheet_name
            and _clean_text(row.get("source_row_no")) == source_row_no
            and _clean_text(row.get("source_sheet_name")) == source_sheet_name
        ):
            return row
    for row in mapping.get("rows") or []:
        if _normalize(row.get("manual_item_name")) != manual_name:
            continue
        if manual_unit and _normalize_unit(row.get("manual_unit")) not in {"", manual_unit}:
            continue
        return row
    return {}


def match_source_signal_to_standard_mapping(
    signal: dict[str, Any],
    mapping: dict[str, Any],
    *,
    limit: int = 3,
    min_score: float = 0.58,
) -> list[dict[str, Any]]:
    text = _clean_text(signal.get("evidence_text")) or _clean_text(signal.get("raw_row_text")) or _clean_text(signal.get("source_name"))
    source_name = _clean_text(signal.get("source_name"))
    if not text and not source_name:
        return []
    scored: list[dict[str, Any]] = []
    for row in mapping.get("rows") or []:
        if not row.get("allowed_for_project_candidate", True):
            continue
        score, reasons = _mapping_match_score(row, source_name, text)
        if score < min_score:
            continue
        scored.append({"mapping": row, "score": round(score, 3), "reasons": reasons})
    scored.sort(key=lambda item: (item["score"], _mapping_priority(item["mapping"])), reverse=True)
    return scored[:limit]


def write_project_standard_mapping_outputs(
    mapping: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_R2_样例答案标准映射表_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{file_stem}.json"
    csv_path = directory / f"{file_stem}.csv"
    markdown_path = directory / f"{file_stem}.md"
    json_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_mapping_csv(csv_path, mapping.get("rows") or [])
    markdown_path.write_text(build_project_standard_mapping_markdown(mapping), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}


def build_project_standard_mapping_markdown(mapping: dict[str, Any]) -> str:
    summary = mapping.get("summary") or {}
    lines = [
        "# BIZ-2x R2-1 样例答案标准映射表",
        "",
        f"- 生成时间：{mapping.get('generated_at', '-')}",
        f"- 人工清单行数：{summary.get('manual_answer_row_count', 0)}",
        f"- 映射行数：{summary.get('mapping_entry_count', 0)}",
        f"- GB/T 标准或可归并行数：{summary.get('standard_mapped_row_count', 0)}",
        f"- 补充清单行数：{summary.get('supplemental_row_count', 0)}",
        f"- 暂不纳入行数：{summary.get('out_of_scope_row_count', 0)}",
        f"- 映射状态分布：{summary.get('mapping_status_counts', {})}",
        f"- 单位校验分布：{summary.get('unit_check_counts', {})}",
        "",
        "## 映射明细",
        "",
        "| 编号 | 人工行号 | 映射状态 | 人工项目 | 标准项目 | 单位校验 | 复核建议 |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in (mapping.get("rows") or [])[:150]:
        standard = row.get("standard_item_name") or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("mapping_id")),
                    _md(row.get("source_row_no")),
                    _md(row.get("mapping_status")),
                    _md(row.get("manual_item_name")),
                    _md(standard),
                    _md(row.get("unit_check_status")),
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
            "- 本表只解决项目归类、标准字段口径和补充项边界，不直接生成最终工程量。",
            "- `allowed_for_final_quantity=false` 的行必须等 R3/R4 形成 CAD 证据、计算公式和标准规则 trace 后才能进入最终算量。",
            "- 单位冲突行不得复用系统工程量，必须先确认按标准单位重算还是按补充清单单位保留。",
        ]
    )
    return "\n".join(lines) + "\n"


def _resolve_standard_mapping(
    *,
    item_name: str,
    feature: str,
    category: str,
    library: QuantityStandardLibrary,
    lexicon_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    explicit_code, reason = _explicit_standard_code(item_name, feature, category)
    if explicit_code:
        item = _find_item_by_code(library, explicit_code)
        if item:
            return _standard_item_payload(item, reason)

    lexicon_code = _clean_text((lexicon_entry or {}).get("standard_item_code"))
    if lexicon_code:
        item = _find_item_by_code(library, lexicon_code)
        if item:
            return _standard_item_payload(
                item,
                _clean_text((lexicon_entry or {}).get("mapping_reason")) or "继承 R1 词库候选标准项目",
            )

    inferred = infer_standard_mapping(item_name, feature, category, library)
    if inferred:
        return inferred

    return {}


def _explicit_standard_code(item_name: str, feature: str, category: str) -> tuple[str, str]:
    text = f"{item_name} {feature}"
    if "拆除" in text or "铲除" in text:
        return "", ""
    if category == "linear_finish":
        if "窗帘盒" in text:
            return "010810002", "窗帘盒按 GB/T 窗帘盒候选，长度单位需按标准复核"
        if "窗台" in text:
            return "010809001", "窗台板按 GB/T 窗台板候选，面积单位需按标准复核"
        if any(term in text for term in ("挡水条", "金属线条", "装饰线条", "线条")):
            return "011502001", "线性收口构件按 GB/T 成品装饰线条候选，需复核是否为补充清单"
    if category == "door_window":
        if "门套" in text:
            if "金属" in text or "不锈钢" in text or "铝" in text:
                return "010808002", "金属门窗套按 GB/T 金属门窗套候选"
            if "石材" in text:
                return "010808003", "石材门窗套按 GB/T 石材门窗套候选"
            return "010808004", "门套按 GB/T 成品门窗套候选"
        if "实木" in text or "木门" in text:
            return "010801001", "成品木门按 GB/T 木质门候选，需复核标准是否按面积计量"
        if "不锈钢" in text:
            return "010805005", "不锈钢门按 GB/T 不锈钢饰面门候选"
        if "玻璃门" in text:
            return "010805004", "玻璃门按 GB/T 全玻自由门候选，需复核门型"
        if "铝合金" in text or "金属" in text:
            return "010802001", "铝合金/金属门按 GB/T 金属(塑钢)门候选"
    if category == "sanitary":
        if any(term in text for term in ("洗手台", "洗漱台", "台盆")):
            return "011505001", "洗手台/台盆按 GB/T 洗漱台候选，需复核是否属给排水洁具安装"
        if any(term in text for term in ("厕纸架", "纸巾架", "毛巾架", "扶手")):
            return "011505002", "卫生间小五金按 GB/T 洗厕配件候选，需复核配件范围"
        if "镜箱" in text:
            return "011505004", "镜箱按 GB/T 镜箱候选"
        if "镜" in text:
            return "011505003", "镜面玻璃按 GB/T 镜面玻璃候选"
    if category == "other":
        if "窗帘盒" in text:
            return "010810002", "窗帘盒按 GB/T 窗帘盒候选"
        if "装饰柜" in text or "成品柜" in text:
            return "011501004", "成品柜类按 GB/T 成品柜、架、台候选"
    return "", ""


def _mapping_status(item_name: str, feature: str, category: str, standard: dict[str, Any]) -> str:
    if _is_out_of_scope(item_name, feature):
        return MAPPING_STATUS_OUT_OF_SCOPE
    if standard.get("item_code"):
        standard_name = _clean_text(standard.get("item_name"))
        manual_norm = _normalize(MATERIAL_CODE_RE.sub("", item_name))
        standard_norm = _normalize(standard_name)
        if manual_norm and standard_norm and (manual_norm == standard_norm or standard_norm in manual_norm):
            return MAPPING_STATUS_STANDARD
        return MAPPING_STATUS_MERGE
    return MAPPING_STATUS_SUPPLEMENTAL


def _feature_text_template(
    *,
    mapping_status: str,
    category_label: str,
    item_name: str,
    manual_feature: str,
    material_codes: list[str],
    feature_fields: list[str],
) -> str:
    if mapping_status == MAPPING_STATUS_OUT_OF_SCOPE:
        return f"暂不纳入本轮四字段清单：{item_name}"
    if not feature_fields:
        return f"补充清单类别：{category_label}；识别项目：{item_name}；标准映射状态：{mapping_status}"
    parts = []
    source_value = _clean_text(manual_feature) or _clean_text(item_name)
    if material_codes:
        source_value = f"{source_value}（材料编号：{'、'.join(material_codes)}）"
    for field_name in feature_fields:
        field_norm = _normalize(field_name)
        if any(key in field_norm for key in ("材料", "品种", "规格", "面层", "基层", "龙骨", "涂料", "防水")):
            value = source_value or "待 R3 从图纸材料表补充"
        elif any(key in field_norm for key in ("部位", "位置")):
            value = "待 R3 绑定房间/区域"
        else:
            value = _clean_text(manual_feature) or "待 R3/R4 补充"
        parts.append(f"{field_name}：{value}")
    return "；".join(parts)


def _unit_check(manual_unit: str, standard_units: list[Any]) -> str:
    unit = _normalize_unit(manual_unit)
    standard = {_normalize_unit(value) for value in standard_units if _normalize_unit(value)}
    if not unit and not standard:
        return "not_applicable"
    if not unit:
        return "manual_unit_missing"
    if not standard:
        return "supplemental_uses_manual_unit"
    if unit in standard:
        return "matched"
    return "unit_conflict_needs_confirmation"


def _review_note(mapping_status: str, category: str, unit_check: str, standard: dict[str, Any]) -> str:
    if mapping_status == MAPPING_STATUS_OUT_OF_SCOPE:
        return "非报价实体或说明性行，默认不进入四字段清单"
    if unit_check == "unit_conflict_needs_confirmation":
        return "单位与标准库不一致，需确认按标准单位重算或作为补充清单保留"
    if mapping_status == MAPPING_STATUS_SUPPLEMENTAL:
        if category in OTHER_SPECIALTY_CATEGORIES:
            return "疑似洁具/电气/给排水等其它专业，需业务确认是否纳入本次报价"
        if category in SUPPLEMENTAL_CATEGORIES:
            return "当前 active GB/T 库无直接项目，先按补充清单候选保留"
        return "当前未匹配 active 标准库，需 R2 人工复核"
    if standard.get("item_code"):
        return "可进入标准字段口径候选，仍需 R3/R4 CAD 证据后才能算量"
    return "待复核"


def _quantity_status(mapping_status: str, has_standard_code: bool) -> str:
    if mapping_status == MAPPING_STATUS_OUT_OF_SCOPE:
        return "暂不纳入本轮四字段清单"
    if has_standard_code:
        return "待 CAD 区域/边界绑定后按标准规则计算"
    return "补充清单项目，待规则确认后计算"


def _fallback_mapping_reason(mapping_status: str, category: str) -> str:
    if mapping_status == MAPPING_STATUS_SUPPLEMENTAL:
        if category == "demolition":
            return "拆除类当前未在 active GB/T 装饰装修标准库中形成直接候选，先按补充清单项目保留"
        if category == "lighting_electrical":
            return "灯具/开关/插座/管线类疑似电气专业，先按补充清单或其它专业候选保留"
        if category == "measure":
            return "保护、运输、保洁等措施类先按补充清单项目保留"
        return "当前未匹配 active GB/T 标准项目，进入补充清单或待范围确认"
    if mapping_status == MAPPING_STATUS_OUT_OF_SCOPE:
        return "说明性或非报价实体行，不进入本轮四字段清单"
    return "按样例答案和 active GB/T 标准库候选映射"


def _index_lexicon_entries(lexicon: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for entry in lexicon.get("entries") or []:
        keys = []
        source_row_no = _clean_text(entry.get("source_row_no"))
        source_sheet_name = _clean_text(entry.get("source_sheet_name"))
        if source_row_no and source_sheet_name:
            keys.append(f"row:{source_sheet_name}:{source_row_no}")
        manual_name = _normalize(entry.get("manual_item_name"))
        if manual_name:
            keys.append(f"name:{manual_name}|unit:{_normalize_unit(entry.get('manual_unit'))}")
        for key in keys:
            index.setdefault(key, []).append(entry)
    return index


def _find_lexicon_entry(source_row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    row_key = f"row:{_clean_text(source_row.get('sheet_name'))}:{_clean_text(source_row.get('row_no'))}"
    if index.get(row_key):
        return index[row_key][0]
    name_key = f"name:{_normalize(source_row.get('item_name'))}|unit:{_normalize_unit(source_row.get('unit'))}"
    if index.get(name_key):
        return index[name_key][0]
    return {}


def _mapping_match_score(row: dict[str, Any], source_name: str, evidence_text: str) -> tuple[float, list[str]]:
    source_norm = _normalize(" ".join([source_name, evidence_text]))
    source_raw = " ".join([source_name, evidence_text])
    if not source_norm:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    manual_name_norm = _normalize(row.get("manual_item_name"))
    if manual_name_norm and manual_name_norm in source_norm:
        score = max(score, 0.88)
        reasons.append(f"命中人工答案项目名称：{row.get('manual_item_name')}")
    for term in row.get("recognition_strong_terms") or []:
        term_norm = _normalize(term)
        if term_norm and term_norm in source_norm:
            score = max(score, 0.86)
            reasons.append(f"命中 R2 强识别词：{term}")
    material_hits = [code for code in row.get("material_codes") or [] if code and code in source_raw.upper()]
    if material_hits:
        score = max(score, 0.82)
        reasons.append("命中材料编号：" + "、".join(material_hits))
    weak_hits = []
    for term in row.get("recognition_weak_terms") or []:
        term_norm = _normalize(term)
        if len(term_norm) >= 2 and term_norm in source_norm:
            weak_hits.append(term)
    if weak_hits:
        score = max(score, min(0.78, 0.46 + len(set(weak_hits)) * 0.08))
        reasons.append("命中 R2 弱识别词：" + "、".join(_dedupe(weak_hits)[:5]))
    if row.get("standard_item_code"):
        score += 0.02
    return min(score, 0.98), _dedupe(reasons)


def _mapping_priority(row: dict[str, Any]) -> int:
    status = row.get("mapping_status")
    if status == MAPPING_STATUS_STANDARD:
        return 100
    if status == MAPPING_STATUS_MERGE:
        return 90
    if status == MAPPING_STATUS_SUPPLEMENTAL:
        return 70
    return 10


def _is_out_of_scope(item_name: str, feature: str) -> bool:
    text = f"{item_name} {feature}"
    return bool(any(term in text for term in ("合计", "小计", "说明", "暂列金额", "规费", "税金")) and not item_name)


def _find_item_by_code(library: QuantityStandardLibrary, item_code: str) -> QuantityStandardItem | None:
    for item in library.items:
        if item.item_code == item_code and item.status == "active":
            return item
    return None


def _standard_item_payload(item: QuantityStandardItem, reason: str) -> dict[str, Any]:
    return {
        "item_code": item.item_code,
        "item_name": item.item_name,
        "chapter_name": item.chapter_name,
        "unit_options": list(item.unit_options),
        "feature_fields": item.feature_names,
        "quantity_rule_text": _clean_text(item.quantity_rule.get("rule_text")),
        "quantity_formula_type": _clean_text(item.quantity_rule.get("formula_type")),
        "drawing_evidence_requirements": list(item.drawing_evidence_requirements),
        "mapping_reason": reason,
    }


def _write_mapping_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MAPPING_CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "映射编号": row.get("mapping_id", ""),
                    "人工行号": row.get("source_row_no", ""),
                    "分类": row.get("category_label", ""),
                    "映射状态": row.get("mapping_status", ""),
                    "人工项目名称": row.get("manual_item_name", ""),
                    "人工项目特征": row.get("manual_feature", ""),
                    "人工单位": row.get("manual_unit", ""),
                    "人工工程量": row.get("manual_quantity", ""),
                    "材料编号": "、".join(row.get("material_codes") or []),
                    "标准项目编码": row.get("standard_item_code", ""),
                    "标准项目名称": row.get("standard_item_name", ""),
                    "标准章节": row.get("standard_chapter_name", ""),
                    "标准单位": " / ".join(row.get("standard_unit_options") or []),
                    "单位校验": row.get("unit_check_status", ""),
                    "项目特征字段口径": "；".join(row.get("standard_feature_fields") or []),
                    "项目特征模板": row.get("feature_text_template", ""),
                    "工程量计算规则": row.get("standard_quantity_rule_text", ""),
                    "工程量公式类型": row.get("standard_quantity_formula_type", ""),
                    "识别强词": "；".join(row.get("recognition_strong_terms") or []),
                    "映射原因": row.get("mapping_reason", ""),
                    "复核建议": row.get("review_note", ""),
                }
            )


def _normalize_unit(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value)).lower()
    text = text.replace("平方米", "m2").replace("平方", "m2").replace("㎡", "m2").replace("m²", "m2")
    text = text.replace("立方米", "m3").replace("立方", "m3").replace("m³", "m3").replace("㎥", "m3")
    text = text.replace("米", "m")
    return re.sub(r"\s+", "", text)


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value)).lower()
    return TEXT_SPLITTER_RE.sub("", text)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        key = _normalize(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", "<br>")
