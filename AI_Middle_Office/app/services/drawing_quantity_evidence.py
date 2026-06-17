from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


DIRECT_AREA_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m2|m²|㎡|平方米|平米)",
    re.IGNORECASE,
)
DIRECT_VOLUME_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m3|m³|立方米|立方)",
    re.IGNORECASE,
)
DIRECT_LENGTH_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m|米)(?!m|m²|m2|m3|m³)",
    re.IGNORECASE,
)
COUNT_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>个|樘|套|处|组|盏|台|块)")
DIMENSION_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|毫米|厚|mm厚)"
    r"|(?P<size>\d+(?:\.\d+)?\s*[xX×*]\s*\d+(?:\.\d+)?(?:\s*[xX×*]\s*\d+(?:\.\d+)?)?)"
    r"|(?P<spacing>@\s*\d+(?:\.\d+)?|中距\s*\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

DIRECT_STATUS_SINGLE = "direct_quantity_candidate_needs_manual_review"
DIRECT_STATUS_MULTI = "multiple_direct_quantity_candidates_needs_manual_review"
PARTIAL_STATUS = "partial_quantity_evidence_needs_manual_measurement"
MISSING_STATUS = "missing_quantity_measurement_needs_manual_review"
RULE_TEXT_STATUS = "rule_text_needs_manual_measurement"


def extract_quantity_evidence_for_standard_matches(
    standard_match_report: dict[str, Any],
    text_records: list[dict[str, Any]] | None = None,
    *,
    max_evidence_per_candidate: int = 8,
) -> dict[str, Any]:
    records = [_normalize_text_record(record) for record in text_records or []]
    candidates = list(standard_match_report.get("standard_item_candidates") or [])
    quantity_candidates: list[dict[str, Any]] = []
    flattened_evidence: list[dict[str, Any]] = []

    for candidate in candidates:
        evidence_rows = _collect_candidate_quantity_evidence(
            candidate,
            records,
            max_evidence_per_candidate=max_evidence_per_candidate,
        )
        decision = _decide_quantity_status(candidate, evidence_rows)
        row = {
            "candidate_key": candidate.get("candidate_key", ""),
            "source_file": candidate.get("source_file", ""),
            "source_row_number": candidate.get("source_row_number", 0),
            "source_name": candidate.get("source_name", ""),
            "source_spec_or_method": candidate.get("source_spec_or_method", ""),
            "standard_item_code": candidate.get("standard_item_code", ""),
            "standard_item_name": candidate.get("standard_item_name", ""),
            "chapter_name": candidate.get("chapter_name", ""),
            "unit_options": list(candidate.get("unit_options") or []),
            "quantity_rule_text": candidate.get("quantity_rule_text", ""),
            "quantity_formula_type": candidate.get("quantity_formula_type", ""),
            "quantity_required_evidence": list(candidate.get("quantity_required_evidence") or []),
            "quantity_status": decision["status"],
            "suggested_quantity": decision["suggested_quantity"],
            "suggested_unit": decision["suggested_unit"],
            "quantity_can_be_final_without_manual_review": False,
            "quantity_block_reason": decision["block_reason"],
            "evidence_count": len(evidence_rows),
            "direct_evidence_count": sum(1 for item in evidence_rows if item["is_direct_for_formula"]),
            "evidence_summary": _evidence_summary(evidence_rows),
            "quantity_evidence": evidence_rows,
        }
        quantity_candidates.append(row)
        for evidence in evidence_rows:
            flattened_evidence.append(
                {
                    "candidate_key": row["candidate_key"],
                    "standard_item_code": row["standard_item_code"],
                    "standard_item_name": row["standard_item_name"],
                    **evidence,
                }
            )

    status_counts = Counter(row["quantity_status"] for row in quantity_candidates)
    formula_counts = Counter(row.get("quantity_formula_type") or "missing" for row in quantity_candidates)
    evidence_type_counts = Counter(item["evidence_type"] for item in flattened_evidence)
    direct_count = sum(1 for row in quantity_candidates if row["direct_evidence_count"] > 0)
    partial_count = sum(1 for row in quantity_candidates if row["evidence_count"] > 0 and row["direct_evidence_count"] == 0)
    missing_count = sum(1 for row in quantity_candidates if row["evidence_count"] == 0)

    return {
        "ok": True,
        "phase": "BIZ-2x-5-quantity-evidence-preview",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "source_phase": standard_match_report.get("phase", ""),
        "source_summary": standard_match_report.get("summary", {}),
        "summary": {
            "standard_candidate_count": len(candidates),
            "text_record_count": len(records),
            "quantity_direct_candidate_count": direct_count,
            "quantity_partial_evidence_count": partial_count,
            "quantity_missing_evidence_count": missing_count,
            "quantity_ready_without_manual_review_count": 0,
            "status_counts": dict(status_counts.most_common()),
            "formula_counts": dict(formula_counts.most_common()),
            "evidence_type_counts": dict(evidence_type_counts.most_common()),
            "final_generation_status": "blocked_until_quantity_evidence_and_manual_review",
        },
        "quantity_candidates": quantity_candidates,
        "quantity_evidence_rows": flattened_evidence,
    }


def build_quantity_evidence_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-5 工程量证据提取与标准规则判断报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 标准项目候选数：{summary['standard_candidate_count']}",
        f"- 可检索图纸文字记录数：{summary['text_record_count']}",
        f"- 有直接工程量候选数：{summary['quantity_direct_candidate_count']}",
        f"- 仅有局部尺寸/做法证据数：{summary['quantity_partial_evidence_count']}",
        f"- 未找到工程量证据数：{summary['quantity_missing_evidence_count']}",
        f"- 无人工复核可直接生成数：{summary['quantity_ready_without_manual_review_count']}",
        f"- 最终清单生成状态：{summary['final_generation_status']}",
        "",
        "## 工程量候选判断",
        "",
        "| 候选编号 | 标准编码 | 标准项目 | 规则类型 | 状态 | 建议工程量 | 单位 | 证据数 | 结论 |",
        "| --- | --- | --- | --- | --- | ---: | --- | ---: | --- |",
    ]
    for row in report["quantity_candidates"][:160]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["candidate_key"]),
                    _md(row["standard_item_code"]),
                    _md(row["standard_item_name"]),
                    _md(row["quantity_formula_type"]),
                    _md(row["quantity_status"]),
                    _md(row["suggested_quantity"]),
                    _md(row["suggested_unit"]),
                    str(row["evidence_count"]),
                    _md(row["quantity_block_reason"]),
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- 本报告只判断工程量证据是否存在，不生成最终四字段工程量清单。",
        "- 项目特征字段仍沿用 BIZ-2x-4 的 active GB/T 标准库字段口径。",
        "- 工程量规则来自标准库 `quantity_rule`；没有可追溯面积、长度、数量、体积证据时必须标记待人工补量。",
        "- 即使识别到直接面积/长度/数量文本，当前也只作为候选工程量，仍需人工复核后才能进入 Excel 或报价。",
    ]
    return "\n".join(lines) + "\n"


def build_quantity_candidate_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report["quantity_candidates"]:
        rows.append(
            {
                "候选编号": row["candidate_key"],
                "来源文件": row["source_file"],
                "来源行号": row["source_row_number"],
                "图纸识别名称": row["source_name"],
                "图纸识别规格或做法": row["source_spec_or_method"],
                "标准项目编码": row["standard_item_code"],
                "标准项目名称": row["standard_item_name"],
                "标准单位": "、".join(row["unit_options"]),
                "工程量规则类型": row["quantity_formula_type"],
                "标准工程量计算规则": row["quantity_rule_text"],
                "标准要求证据": "、".join(row["quantity_required_evidence"]),
                "工程量状态": row["quantity_status"],
                "建议工程量": row["suggested_quantity"],
                "建议单位": row["suggested_unit"],
                "是否可免人工生成": "否",
                "阻断原因": row["quantity_block_reason"],
                "证据数量": row["evidence_count"],
                "直接工程量证据数量": row["direct_evidence_count"],
                "证据摘要": row["evidence_summary"],
            }
        )
    return rows


def build_quantity_evidence_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report["quantity_evidence_rows"]:
        rows.append(
            {
                "候选编号": row["candidate_key"],
                "标准项目编码": row["standard_item_code"],
                "标准项目名称": row["standard_item_name"],
                "证据类型": row["evidence_type"],
                "证据值": row["value"],
                "证据单位": row["unit"],
                "是否匹配工程量规则": "是" if row["is_direct_for_formula"] else "否",
                "证据置信度": f"{row['confidence']:.2f}",
                "证据文本": row["text"],
                "来源文件": row["source_file"],
                "图层": row["layer"],
                "布局": row["layout"],
                "块名": row["block_name"],
                "X": row["x"],
                "Y": row["y"],
                "源行号": row["line_number"],
                "业务标签": " / ".join(row["role_tags"]),
            }
        )
    return rows


def write_quantity_evidence_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or "BIZ2x5_工程量证据提取"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    candidate_csv_path = directory / f"{file_stem}_工程量候选判断.csv"
    evidence_csv_path = directory / f"{file_stem}_工程量证据明细.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_quantity_evidence_markdown(report), encoding="utf-8")
    _write_csv(candidate_csv_path, build_quantity_candidate_csv_rows(report))
    _write_csv(evidence_csv_path, build_quantity_evidence_csv_rows(report))
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "quantity_candidate_csv": str(candidate_csv_path),
        "quantity_evidence_csv": str(evidence_csv_path),
    }


def _collect_candidate_quantity_evidence(
    candidate: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    max_evidence_per_candidate: int,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    formula_type = _clean_text(candidate.get("quantity_formula_type"))

    candidate_text = "；".join(
        part
        for part in [
            _clean_text(candidate.get("source_name")),
            _clean_text(candidate.get("source_spec_or_method")),
            _clean_text(candidate.get("evidence_text")),
        ]
        if part
    )
    evidence.extend(_extract_measurements_from_text(candidate_text, formula_type, 0.9, source="candidate_source"))

    for record in records:
        score = _record_context_score(candidate, record)
        if score < 0.28:
            continue
        for item in _extract_measurements_from_text(record["text"], formula_type, score, source="dxf_text_record"):
            item.update(
                {
                    "source_file": record["source_file"],
                    "layer": record["layer"],
                    "layout": record["layout"],
                    "block_name": record["block_name"],
                    "x": record["x"],
                    "y": record["y"],
                    "line_number": record["line_number"],
                    "role_tags": record["role_tags"],
                }
            )
            evidence.append(item)

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in sorted(evidence, key=lambda row: (-row["is_direct_for_formula"], -row["confidence"], row["text"])):
        item.setdefault("source_file", _clean_text(candidate.get("source_file")))
        item.setdefault("layer", "")
        item.setdefault("layout", "")
        item.setdefault("block_name", "")
        item.setdefault("x", None)
        item.setdefault("y", None)
        item.setdefault("line_number", candidate.get("source_row_number") or 0)
        item.setdefault("role_tags", [])
        key = (item["evidence_type"], item["value"], item["unit"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= max_evidence_per_candidate:
            break
    return normalized


def _extract_measurements_from_text(
    text: str,
    formula_type: str,
    confidence: float,
    *,
    source: str,
) -> list[dict[str, Any]]:
    clean = _clean_text(text)
    if not clean:
        return []
    rows: list[dict[str, Any]] = []
    for regex, evidence_type in [
        (DIRECT_AREA_RE, "area_text"),
        (DIRECT_VOLUME_RE, "volume_text"),
        (COUNT_RE, "count_text"),
    ]:
        for match in regex.finditer(clean):
            value = match.group("value")
            unit = match.group("unit")
            rows.append(
                {
                    "evidence_type": evidence_type,
                    "value": value,
                    "unit": unit,
                    "text": clean,
                    "confidence": round(min(0.98, confidence), 2),
                    "source": source,
                    "is_direct_for_formula": _evidence_matches_formula(evidence_type, formula_type),
                }
            )
    for match in DIRECT_LENGTH_RE.finditer(clean):
        value = match.group("value")
        unit = match.group("unit")
        if _has_direct_length_context(clean, match.start(), match.end()):
            evidence_type = "length_text"
            is_direct = _evidence_matches_formula(evidence_type, formula_type)
        else:
            evidence_type = "dimension_text"
            is_direct = False
        rows.append(
            {
                "evidence_type": evidence_type,
                "value": value,
                "unit": unit,
                "text": clean,
                "confidence": round(min(0.98, confidence), 2),
                "source": source,
                "is_direct_for_formula": is_direct,
            }
        )
    for match in DIMENSION_RE.finditer(clean):
        value = match.group("size") or match.group("spacing") or match.group("value") or ""
        unit = match.group("unit") or ""
        rows.append(
            {
                "evidence_type": "dimension_text",
                "value": _clean_text(value),
                "unit": _clean_text(unit),
                "text": clean,
                "confidence": round(min(0.9, confidence), 2),
                "source": source,
                "is_direct_for_formula": False,
            }
        )
    return rows


def _decide_quantity_status(candidate: dict[str, Any], evidence_rows: list[dict[str, Any]]) -> dict[str, str]:
    formula_type = _clean_text(candidate.get("quantity_formula_type"))
    direct = [row for row in evidence_rows if row["is_direct_for_formula"]]
    if direct:
        best = direct[0]
        if len(direct) == 1:
            status = DIRECT_STATUS_SINGLE
            reason = "识别到一个直接工程量候选，但仍需人工复核来源范围和扣减规则"
        else:
            status = DIRECT_STATUS_MULTI
            reason = "识别到多个直接工程量候选，需人工判断取值范围、是否汇总或是否扣减"
        return {
            "status": status,
            "suggested_quantity": best["value"],
            "suggested_unit": best["unit"],
            "block_reason": reason,
        }
    if evidence_rows:
        return {
            "status": PARTIAL_STATUS,
            "suggested_quantity": "",
            "suggested_unit": "",
            "block_reason": "仅识别到尺寸/厚度/规格等局部证据，缺少可按标准规则计算的面积、长度、数量或体积",
        }
    if formula_type == "rule_text":
        return {
            "status": RULE_TEXT_STATUS,
            "suggested_quantity": "",
            "suggested_unit": "",
            "block_reason": "标准库为文字规则类型，需要人工按规则判断工程量口径",
        }
    return {
        "status": MISSING_STATUS,
        "suggested_quantity": "",
        "suggested_unit": "",
        "block_reason": "未找到可追溯工程量证据，需人工从图纸量取或补充",
    }


def _evidence_matches_formula(evidence_type: str, formula_type: str) -> bool:
    if formula_type in {"area", "expanded_area"}:
        return evidence_type == "area_text"
    if formula_type == "length":
        return evidence_type == "length_text"
    if formula_type == "count":
        return evidence_type == "count_text"
    if formula_type == "volume":
        return evidence_type == "volume_text"
    return False


def _has_direct_length_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 12) : min(len(text), end + 12)]
    if any(keyword in window for keyword in ("超过", "高", "高度", "厚", "间距", "中距", "净高", "距")):
        return False
    return any(
        keyword in window
        for keyword in ("长度", "总长", "长", "延长米", "工程量", "L=", "L：", "L:", "l=", "l：", "l:")
    )


def _record_context_score(candidate: dict[str, Any], record: dict[str, Any]) -> float:
    haystack = _normalize(" ".join([record["text"], record["layer"], record["block_name"]]))
    terms = _candidate_terms(candidate)
    score = 0.0
    if _clean_text(candidate.get("source_file")) and _clean_text(candidate.get("source_file")) == record["source_file"]:
        score += 0.2
    for term in terms:
        normalized = _normalize(term)
        if len(normalized) >= 2 and normalized in haystack:
            score += 0.22 if len(normalized) < 4 else 0.36
    if any(tag in {"plan", "elevation", "detail", "construction_method", "material_table"} for tag in record["role_tags"]):
        score += 0.05
    return min(score, 0.98)


def _candidate_terms(candidate: dict[str, Any]) -> list[str]:
    raw_parts = [
        candidate.get("source_name"),
        candidate.get("source_spec_or_method"),
        candidate.get("standard_item_name"),
        candidate.get("evidence_text"),
    ]
    terms: list[str] = []
    for part in raw_parts:
        clean = _clean_text(part)
        if not clean:
            continue
        terms.append(clean)
        for token in re.split(r"[\s,，。；;:：|/\\()（）\[\]【】、\-]+", clean):
            token = _clean_text(token)
            if len(token) >= 2 and not token.replace(".", "").isdigit():
                terms.append(token)
                terms.extend(_cjk_chunks(token))
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = _normalize(term)
        if key and key not in seen:
            seen.add(key)
            deduped.append(term)
    return deduped[:30]


def _cjk_chunks(token: str) -> list[str]:
    if len(token) < 4 or not re.search(r"[\u4e00-\u9fff]", token):
        return []
    chunks: list[str] = []
    for size in (2, 3):
        for index in range(0, max(0, len(token) - size + 1)):
            chunk = token[index : index + size]
            if re.search(r"[\u4e00-\u9fff]", chunk):
                chunks.append(chunk)
            if len(chunks) >= 16:
                return chunks
    return chunks


def _evidence_summary(evidence_rows: list[dict[str, Any]]) -> str:
    if not evidence_rows:
        return ""
    parts = []
    for row in evidence_rows[:4]:
        value = f"{row['value']}{row['unit']}".strip()
        parts.append(f"{row['evidence_type']}:{value or row['text'][:24]}")
    return "；".join(parts)


def _normalize_text_record(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file": _clean_text(raw.get("source_file")),
        "entity_type": _clean_text(raw.get("entity_type")),
        "text": _clean_text(raw.get("text")),
        "layer": _clean_text(raw.get("layer")),
        "layout": _clean_text(raw.get("layout")),
        "block_name": _clean_text(raw.get("block_name")),
        "x": raw.get("x"),
        "y": raw.get("y"),
        "line_number": int(raw.get("line_number") or 0),
        "role_tags": list(raw.get("role_tags") or []),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", _clean_text(value).lower())


def _md(value: Any) -> str:
    text = _clean_text(value)
    return text.replace("|", "\\|").replace("\n", " / ")
