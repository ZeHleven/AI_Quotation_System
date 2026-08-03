from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.quantity_standard_library import (
    ACTIVE_STATUS,
    QuantityStandardItem,
    QuantityStandardLibrary,
    quantity_standard_summary,
)
from app.services.drawing_project_lexicon import PROJECT_SIGNAL_TERMS


DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "standards"
    / "gbtn50854_2024_word_active_20260610_024610.json"
)

GENERIC_SOURCE_NAMES = {
    "材料名称",
    "材料材料",
    "做法详图",
    "工程做法",
    "注意事项",
    "注意:",
    "类别",
    "编号",
    "通用节点（一）",
    "通用节点（二）",
    "通用节点（三）",
    "通用节点（四）",
}

TEXT_SPLITTER_RE = re.compile(r"[\s\-_—、，。；;:：|/\\()（）\[\]{}《》<>\"'“”‘’]+")
THICKNESS_RE = re.compile(r"(?:(?:\d+(?:\.\d+)?)\s*厚[^；;，,。|]*)")
SPEC_RE = re.compile(r"(?:\d+(?:\.\d+)?\s*(?:mm|MM|m|M)?|[A-Z]{1,3}\s*)?[∅Φ]?\d+\s*[×xX*]\s*\d+(?:\s*[×xX*]\s*\d+)?|@\s*\d+|中距\s*\d+\s*mm", re.IGNORECASE)
MATERIAL_TERM_RE = re.compile(
    r"(?:玻化砖|地砖|墙砖|纸面石膏板|石膏板|轻钢龙骨|主龙骨|次龙骨|透光软膜|无机涂料|乳胶漆|聚氨酯涂膜|聚合物水泥|水泥砂浆|细石混凝土|轻集料混凝土|大理石|不锈钢|铝镁合金|铝板|阻燃板|木基层|防火涂料|防水涂料|腻子|窗台板|窗帘盒)"
)

STANDARD_ITEM_MAPPINGS: tuple[dict[str, Any], ...] = (
    {
        "target_code": "011102003",
        "reason": "图纸出现地砖/玻化砖/块料地面做法，按块料楼地面作为候选",
        "triggers_any": ("玻化砖", "地砖", "块料", "地面做法", "水泥砂浆结合层", "细石混凝土找平层"),
        "boost": 76.0,
    },
    {
        "target_code": "011203003",
        "reason": "图纸出现墙砖/块料墙面线索，按块料墙、柱面作为候选",
        "triggers_any": ("墙砖", "块料墙", "墙面块料", "墙面砖"),
        "boost": 76.0,
    },
    {
        "target_code": "010904002",
        "reason": "图纸出现楼地面涂膜防水线索，按楼(地)面涂膜防水作为候选",
        "triggers_any": ("聚氨酯涂膜防水", "地面防水", "厨卫地面做法", "防水三遍"),
        "boost": 82.0,
    },
    {
        "target_code": "010903002",
        "reason": "图纸出现墙面涂膜防水线索，按墙面涂膜防水作为候选",
        "triggers_any": ("墙面涂膜防水", "墙面防水"),
        "boost": 78.0,
    },
    {
        "target_code": "011302001",
        "reason": "图纸出现石膏板/轻钢龙骨/普通吊顶线索，按平面吊顶天棚作为候选",
        "triggers_any": ("石膏板", "轻钢龙骨", "主龙骨", "次龙骨", "龙骨吊顶", "透光软膜", "吊顶详图"),
        "boost": 80.0,
    },
    {
        "target_code": "011302003",
        "reason": "图纸出现灯槽/造型/软膜等吊顶造型线索，按艺术造型吊顶天棚作为候选",
        "triggers_any": ("灯槽", "造型", "软膜相接", "透光软膜", "暗藏灯带"),
        "boost": 68.0,
    },
    {
        "target_code": "011404002",
        "reason": "图纸出现天棚或石膏板基层喷刷涂料线索，按天棚喷刷涂料作为候选",
        "triggers_any": ("石膏板刮瓷刷", "天棚喷刷", "无机涂料", "乳胶漆"),
        "context_any": ("石膏板", "天棚", "吊顶", "软膜"),
        "boost": 72.0,
    },
    {
        "target_code": "011404001",
        "reason": "图纸出现墙面喷刷涂料线索，按墙面喷刷涂料作为候选",
        "triggers_any": ("墙面喷刷", "墙面乳胶漆", "墙面无机涂料"),
        "boost": 72.0,
    },
    {
        "target_code": "010810002",
        "reason": "图纸出现窗帘盒线索，按窗帘盒作为候选",
        "triggers_any": ("窗帘盒",),
        "boost": 86.0,
    },
    {
        "target_code": "010809001",
        "reason": "图纸出现窗台板线索，按窗台板作为候选",
        "triggers_any": ("窗台板",),
        "boost": 82.0,
    },
    {
        "target_code": "011105006",
        "reason": "图纸出现金属/铝镁合金踢脚线线索，按金属踢脚线作为候选",
        "triggers_any": ("铝镁合金踢脚线", "金属踢脚线", "不锈钢踢脚线"),
        "boost": 84.0,
    },
    {
        "target_code": "011102001",
        "reason": "图纸出现大理石/石材地面线索，按石材楼地面作为候选",
        "triggers_any": ("大理石地面", "石材地面", "地面石材"),
        "boost": 78.0,
    },
)


def match_drawing_fields_to_standard(
    field_report: dict[str, Any],
    library: QuantityStandardLibrary,
    *,
    limit_per_source: int = 5,
    min_confidence: float = 0.45,
) -> dict[str, Any]:
    active_items = [item for item in library.items if item.status == ACTIVE_STATUS]
    items_by_code = {item.item_code: item for item in active_items}
    source_signals = _build_source_signals(field_report)
    candidate_groups: list[dict[str, Any]] = []
    flattened: list[dict[str, Any]] = []

    for signal_index, signal in enumerate(source_signals, start=1):
        matches = _match_signal(signal, active_items, items_by_code)
        matches = [match for match in matches if match["confidence"] >= min_confidence]
        matches = matches[:limit_per_source]
        if not matches:
            continue
        candidate_key = f"BIZ2x4-{signal_index:04d}"
        group_matches: list[dict[str, Any]] = []
        for rank, match in enumerate(matches, start=1):
            item = match["item"]
            feature_candidates = _build_feature_fill_candidates(signal, item)
            standard_candidate = {
                "rank": rank,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "chapter_name": item.chapter_name,
                "unit_options": list(item.unit_options),
                "quantity_rule_text": _clean_text(item.quantity_rule.get("rule_text")),
                "quantity_formula_type": _clean_text(item.quantity_rule.get("formula_type")),
                "quantity_required_evidence": list(item.quantity_rule.get("required_evidence") or []),
                "quantity_evidence_status": "missing_quantity_measurement_needs_manual_review",
                "feature_fields": item.feature_names,
                "feature_fill_candidates": feature_candidates,
                "no_feature_fields_in_standard": item.no_feature_fields_in_standard,
                "match_score": round(match["score"], 2),
                "match_confidence": match["confidence"],
                "match_reasons": match["reasons"],
                "matched_fields": match["matched_fields"],
                "source_note": item.source_note,
            }
            group_matches.append(standard_candidate)
            flattened.append(_flatten_candidate(candidate_key, signal, standard_candidate))
        candidate_groups.append(
            {
                "candidate_key": candidate_key,
                "source_signal": signal,
                "standard_candidates": group_matches,
            }
        )

    unique_codes = sorted({row["standard_item_code"] for row in flattened})
    confidence_counts = Counter(_confidence_bucket(row["match_confidence"]) for row in flattened)
    matched_signal_keys = {group["candidate_key"] for group in candidate_groups}
    return {
        "ok": True,
        "phase": "BIZ-2x-4-standard-match-preview",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "standard_library_summary": quantity_standard_summary(library),
        "source_summary": field_report.get("summary", {}),
        "summary": {
            "source_signal_count": len(source_signals),
            "matched_signal_count": len(candidate_groups),
            "unmatched_source_signal_count": max(0, len(source_signals) - len(matched_signal_keys)),
            "standard_candidate_count": len(flattened),
            "unique_standard_item_count": len(unique_codes),
            "unique_standard_item_codes": unique_codes,
            "confidence_counts": dict(confidence_counts.most_common()),
            "quantity_ready_count": 0,
            "quantity_pending_count": len(flattened),
            "final_generation_status": "blocked_until_quantity_evidence_and_manual_review",
        },
        "source_signals": source_signals,
        "candidate_groups": candidate_groups,
        "standard_item_candidates": flattened,
    }


def build_standard_match_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-4 图纸字段匹配 GB/T 标准项目候选报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 来源线索数：{summary['source_signal_count']}",
        f"- 已匹配线索数：{summary['matched_signal_count']}",
        f"- 标准项目候选数：{summary['standard_candidate_count']}",
        f"- 唯一标准项目数：{summary['unique_standard_item_count']}",
        f"- 工程量可直接生成数：{summary['quantity_ready_count']}",
        f"- 工程量待人工/待证据数：{summary['quantity_pending_count']}",
        f"- 最终清单生成状态：{summary['final_generation_status']}",
        "",
        "## 标准项目候选",
        "",
        "| 候选编号 | 来源名称 | 标准编码 | 标准项目 | 置信度 | 单位 | 工程量状态 | 匹配理由 |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in report["standard_item_candidates"][:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["candidate_key"]),
                    _md(row["source_name"]),
                    _md(row["standard_item_code"]),
                    _md(row["standard_item_name"]),
                    f"{row['match_confidence']:.2f}",
                    _md("、".join(row["unit_options"])),
                    _md(row["quantity_evidence_status"]),
                    _md("；".join(row["match_reasons"][:3])),
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- 本报告只输出标准项目候选，不输出最终四字段清单。",
        "- 项目特征字段名称完全来自 active GB/T 标准库；候选填充值只来自图纸文本证据。",
        "- 工程量规则只展示标准库原规则；当前没有足够尺寸/面积证据时统一标记为待人工确认。",
    ]
    return "\n".join(lines) + "\n"


def build_standard_match_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "候选编号": row["candidate_key"],
            "来源类型": row["source_kind_label"],
            "来源文件": row["source_file"],
            "来源行号": row["source_row_number"],
            "图纸识别名称": row["source_name"],
            "图纸识别规格或做法": row["source_spec_or_method"],
            "标准项目编码": row["standard_item_code"],
            "标准项目名称": row["standard_item_name"],
            "章节": row["chapter_name"],
            "匹配置信度": f"{row['match_confidence']:.2f}",
            "匹配理由": "；".join(row["match_reasons"]),
            "项目特征字段": "；".join(row["feature_fields"]),
            "待人工补充字段": "；".join(row["missing_feature_fields"]),
            "单位选项": "；".join(row["unit_options"]),
            "工程量计算规则": row["quantity_rule_text"],
            "工程量状态": row["quantity_evidence_status"],
            "原始证据文本": row["evidence_text"],
        }
        for row in report["standard_item_candidates"]
    ]


def build_feature_fill_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in report["candidate_groups"]:
        signal = group["source_signal"]
        for candidate in group["standard_candidates"]:
            for feature in candidate["feature_fill_candidates"]:
                rows.append(
                    {
                        "候选编号": group["candidate_key"],
                        "来源文件": signal["source_file"],
                        "图纸识别名称": signal["source_name"],
                        "标准项目编码": candidate["item_code"],
                        "标准项目名称": candidate["item_name"],
                        "项目特征字段": feature["field_name"],
                        "候选填充值": feature["candidate_value"],
                        "状态": feature["status"],
                        "置信度": f"{feature['confidence']:.2f}",
                        "证据文本": feature["evidence_text"],
                    }
                )
    return rows


def write_standard_match_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or "BIZ2x4_GBT标准项目候选匹配"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    match_csv_path = directory / f"{file_stem}_标准项目候选.csv"
    feature_csv_path = directory / f"{file_stem}_项目特征待填充.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_standard_match_markdown(report), encoding="utf-8")
    _write_csv(match_csv_path, build_standard_match_csv_rows(report))
    _write_csv(feature_csv_path, build_feature_fill_csv_rows(report))
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "standard_match_csv": str(match_csv_path),
        "feature_fill_csv": str(feature_csv_path),
    }


def _build_source_signals(field_report: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    source_rows = [
        *list(field_report.get("material_method_rows", [])),
        *list(field_report.get("drawing_annotation_rows", [])),
    ]
    for row in source_rows:
        source_name = _clean_text(row.get("material_or_method_name"))
        if not _source_name_is_useful(source_name):
            continue
        spec_or_method = _clean_text(row.get("spec_or_method"))
        evidence = "；".join(
            part
            for part in [
                source_name,
                spec_or_method,
            ]
            if part
        )
        if not _evidence_has_match_signal(evidence):
            continue
        key = (
            _clean_text(row.get("source_file")),
            _clean_text(row.get("row_type")),
            _normalize(source_name),
            _normalize(spec_or_method)[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        row_type = _clean_text(row.get("row_type"))
        signals.append(
            {
                "source_kind": row_type or "unknown",
                "source_kind_label": _clean_text(row.get("row_type_label")) or row_type,
                "source_file": _clean_text(row.get("source_file")),
                "source_table_anchor": _clean_text(row.get("source_table_anchor")),
                "source_row_number": int(row.get("source_row_number") or 0),
                "source_name": source_name,
                "source_spec_or_method": spec_or_method,
                "source_confidence": float(row.get("confidence") or 0),
                "evidence_text": evidence,
                "raw_row_text": _clean_text(row.get("raw_row_text")),
            }
        )
    signals.sort(key=lambda item: (item["source_file"], item["source_row_number"], item["source_name"]))
    return signals


def _match_signal(
    signal: dict[str, Any],
    active_items: list[QuantityStandardItem],
    items_by_code: dict[str, QuantityStandardItem],
) -> list[dict[str, Any]]:
    evidence = signal["evidence_text"]
    normalized_evidence = _normalize(evidence)
    scored: dict[str, dict[str, Any]] = {}

    for mapping in STANDARD_ITEM_MAPPINGS:
        if not _mapping_matches(evidence, mapping):
            continue
        item = items_by_code.get(mapping["target_code"])
        if not item:
            continue
        if _is_contextually_suppressed(item, evidence):
            continue
        _add_score(
            scored,
            item,
            float(mapping["boost"]),
            mapping["reason"],
            "business_mapping",
        )

    for item in active_items:
        if _is_contextually_suppressed(item, evidence):
            continue
        score, reasons, matched_fields = _standard_text_score(item, normalized_evidence)
        if score > 0:
            _add_score(scored, item, score, "标准库文本检索命中", ",".join(matched_fields), reasons=reasons)

    matches = []
    for item_code, match in scored.items():
        score = max(0.0, match["score"])
        confidence = min(0.96, round(score / 100.0, 2))
        if score < 38:
            continue
        matches.append(
            {
                "item": match["item"],
                "score": score,
                "confidence": confidence,
                "reasons": _dedupe(match["reasons"]),
                "matched_fields": _dedupe(match["matched_fields"]),
            }
        )
    matches.sort(key=lambda entry: (-entry["confidence"], -entry["score"], entry["item"].item_code))
    return matches


def _is_contextually_suppressed(item: QuantityStandardItem, evidence: str) -> bool:
    if item.item_code == "010506024" and any(keyword in evidence for keyword in ("龙骨", "吊顶", "石膏板", "膨胀螺栓")):
        return True
    if item.item_code == "010810001" and "窗帘盒" in evidence:
        return True
    if item.item_code == "011507003" and "灯箱处" in evidence:
        return True
    if item.item_code in {"011105001", "011105004"} and "踢脚线" in evidence and any(
        keyword in evidence for keyword in ("不锈钢", "铝镁合金", "金属")
    ):
        return True
    return False


def _standard_text_score(item: QuantityStandardItem, normalized_evidence: str) -> tuple[float, list[str], list[str]]:
    score = 0.0
    reasons: list[str] = []
    matched_fields: list[str] = []
    item_name_norm = _normalize(item.item_name)
    if item_name_norm and item_name_norm in normalized_evidence:
        score += 78.0
        reasons.append(f"图纸文本直接包含标准项目名称：{item.item_name}")
        matched_fields.append("item_name")
    for token in _name_tokens(item.item_name):
        token_norm = _normalize(token)
        if len(token_norm) >= 2 and token_norm in normalized_evidence:
            score += 18.0
            reasons.append(f"图纸文本包含标准项目关键词：{token}")
            matched_fields.append("item_name_token")
    for keyword in item.keywords:
        keyword_norm = _normalize(keyword)
        if len(keyword_norm) >= 2 and keyword_norm in normalized_evidence:
            score += 24.0
            reasons.append(f"图纸文本包含标准库关键词：{keyword}")
            matched_fields.append("keywords")
    if score and item.chapter_name and _normalize(item.chapter_name) in normalized_evidence:
        score += 8.0
        matched_fields.append("chapter_name")
    for keyword in item.exclusion_keywords:
        keyword_norm = _normalize(keyword)
        if keyword_norm and keyword_norm in normalized_evidence:
            score -= 45.0
            reasons.append(f"命中排除词：{keyword}")
            matched_fields.append("exclusion_keywords")
    return max(0.0, score), reasons, matched_fields


def _build_feature_fill_candidates(signal: dict[str, Any], item: QuantityStandardItem) -> list[dict[str, Any]]:
    if item.no_feature_fields_in_standard:
        return [
            {
                "field_name": "",
                "candidate_value": "",
                "status": "not_applicable_no_feature_fields_in_standard",
                "confidence": 1.0,
                "evidence_text": "",
            }
        ]
    features: list[dict[str, Any]] = []
    evidence = signal["evidence_text"]
    for field_name in item.feature_names:
        value = _extract_feature_value(field_name, evidence)
        features.append(
            {
                "field_name": field_name,
                "candidate_value": value,
                "status": "candidate_from_drawing_text" if value else "missing_needs_manual_review",
                "confidence": 0.68 if value else 0.0,
                "evidence_text": evidence if value else "",
            }
        )
    return features


def _extract_feature_value(field_name: str, evidence: str) -> str:
    normalized_field = _normalize(field_name)
    candidates: list[str] = []
    if any(key in normalized_field for key in ("材料", "品种", "材质", "面层", "基层", "防护", "涂料", "防水膜")):
        candidates.extend(match.group(0) for match in MATERIAL_TERM_RE.finditer(evidence))
    if any(key in normalized_field for key in ("厚度", "遍数", "强度等级", "做法", "找平层", "结合层", "防水层", "规格")):
        candidates.extend(match.group(0) for match in THICKNESS_RE.finditer(evidence))
        if "三遍" in evidence:
            candidates.append("三遍")
        if "1:3水泥砂浆" in evidence:
            candidates.append("1:3水泥砂浆")
        if "C20细石混凝土" in evidence:
            candidates.append("C20细石混凝土")
    if any(key in normalized_field for key in ("规格", "中距", "吊杆", "龙骨", "高度", "尺寸")):
        candidates.extend(match.group(0).strip() for match in SPEC_RE.finditer(evidence))
        if "龙骨" in evidence:
            candidates.extend(term for term in ("轻钢龙骨", "主龙骨", "次龙骨") if term in evidence)
    if "部位" in normalized_field:
        for term in ("厨卫地面", "地面", "天棚", "吊顶", "窗帘盒", "窗台板", "墙面"):
            if term in evidence:
                candidates.append(term)
    if "形式" in normalized_field and "吊顶" in evidence:
        candidates.append("吊顶")
    return "；".join(_dedupe([_clean_text(item) for item in candidates if _clean_text(item)]))[:300]


def _flatten_candidate(
    candidate_key: str,
    signal: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    missing_fields = [
        feature["field_name"]
        for feature in candidate["feature_fill_candidates"]
        if feature["status"] == "missing_needs_manual_review"
    ]
    return {
        "candidate_key": candidate_key,
        "source_kind": signal["source_kind"],
        "source_kind_label": signal["source_kind_label"],
        "source_file": signal["source_file"],
        "source_table_anchor": signal["source_table_anchor"],
        "source_row_number": signal["source_row_number"],
        "source_name": signal["source_name"],
        "source_spec_or_method": signal["source_spec_or_method"],
        "evidence_text": signal["evidence_text"],
        "standard_item_code": candidate["item_code"],
        "standard_item_name": candidate["item_name"],
        "chapter_name": candidate["chapter_name"],
        "unit_options": candidate["unit_options"],
        "feature_fields": candidate["feature_fields"],
        "missing_feature_fields": missing_fields,
        "quantity_rule_text": candidate["quantity_rule_text"],
        "quantity_formula_type": candidate["quantity_formula_type"],
        "quantity_evidence_status": candidate["quantity_evidence_status"],
        "match_confidence": candidate["match_confidence"],
        "match_reasons": candidate["match_reasons"],
        "matched_fields": candidate["matched_fields"],
    }


def _mapping_matches(evidence: str, mapping: dict[str, Any]) -> bool:
    if not any(trigger in evidence for trigger in mapping.get("triggers_any", ())):
        return False
    context = mapping.get("context_any")
    if context and not any(keyword in evidence for keyword in context):
        return False
    return True


def _add_score(
    scored: dict[str, dict[str, Any]],
    item: QuantityStandardItem,
    score: float,
    reason: str,
    matched_field: str,
    *,
    reasons: list[str] | None = None,
) -> None:
    current = scored.setdefault(
        item.item_code,
        {"item": item, "score": 0.0, "reasons": [], "matched_fields": []},
    )
    current["score"] += score
    current["reasons"].extend(reasons or [reason])
    current["matched_fields"].append(matched_field)


def _source_name_is_useful(source_name: str) -> bool:
    if not source_name or source_name in GENERIC_SOURCE_NAMES:
        return False
    if len(source_name) <= 1:
        return False
    if source_name.replace(".", "").isdigit():
        return False
    return True


def _evidence_has_match_signal(evidence: str) -> bool:
    signals = (
        "砖",
        "地面",
        "防水",
        "吊顶",
        "龙骨",
        "石膏板",
        "涂料",
        "乳胶漆",
        "软膜",
        "窗帘盒",
        "窗台板",
        "踢脚线",
        "水泥砂浆",
        "混凝土",
        "大理石",
        "不锈钢",
        "铝",
        "腻子",
        "门",
        "窗",
        "拆除",
        "铲除",
        "隔断",
        "隔墙",
        "台盆",
        "马桶",
        "龙头",
        "地漏",
        "洁具",
        "灯具",
        "开关",
        "插座",
        "管线",
        "保护",
        "运输",
        "保洁",
    )
    return any(signal in evidence for signal in (*signals, *PROJECT_SIGNAL_TERMS))


def _name_tokens(value: str) -> list[str]:
    return [
        token
        for token in TEXT_SPLITTER_RE.split(value)
        if len(_normalize(token)) >= 2 and token not in {"项目", "其他", "零星"}
    ]


def _confidence_bucket(value: float) -> str:
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


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


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
